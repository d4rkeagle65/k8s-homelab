"""Attaches to each of Bookshelf's authors the works rreading-glasses holds but left off it.

Runs as the rreading-glasses-repair CronJob (cronjob-rreading-glasses-repair.yaml).
rreading-glasses' first load of an author attaches its works in batches that each
start from the pre-refresh snapshot, so the last batch overwrites the others and
the author comes out short (upstream issue 602). Every work it fetched is still in
its cache, under `w<id>`, naming the author first. So, from its database alone and
without one Hardcover lookup, a work is missing from an author when its cached copy
has editions and names that author first, and the author's cached record (`a<id>`)
doesn't list it.

The repair is upstream's own path for a newly fetched work: `DELETE /work/<id>`
expires the cached copy (memory and database), and `GET /work/<id>` fetches it again
from Hardcover, which attaches it to its author in the background. That costs about
one lookup of Hardcover's daily budget a work, so a run:

- skips entirely while Bookshelf runs a command that looks books up (BUSY_COMMANDS)
  or rreading-glasses has a refresh or denormalization pending;
- skips an author mid-refresh (an `ra<id>` row: its batches would overwrite the
  repair) or whose record expires within the hour (fetching a work would refresh it);
- reads `rg_gql_ratelimit_remaining{bucket="daily"}` before every work and stops
  below MIN_DAILY_REMAINING; with no reading after the first work, it stops, since
  it can't tell what is left;
- repairs at most MAX_WORKS works, one every PACE_SECONDS, smallest authors first,
  and after an author with more than LARGE_AUTHOR missing, none other.

After each author it waits for rreading-glasses' denormalization to finish, reads
the author's record again and reports each work attached or not, then asks Bookshelf
to refresh that author. Stopped half-way, nothing is left inconsistent: an expired
work is fetched again by whoever asks next, and the next run finds what is still
missing.

DRY_RUN=true reports the short authors, their missing works and whether a run could
go ahead, and changes nothing. Exit 0: done, nothing to do or skipped; 1: a work
didn't attach or a check failed; 2: something couldn't be read.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
RG_URL = os.environ.get("RG_URL", "http://rreading-glasses:8788")
BOOKSHELF_URL = os.environ.get("BOOKSHELF_URL", "http://bookshelf:8787")
MIN_DAILY_REMAINING = int(os.environ.get("MIN_DAILY_REMAINING", "2000"))
MAX_WORKS = int(os.environ.get("MAX_WORKS", "300"))
LARGE_AUTHOR = int(os.environ.get("LARGE_AUTHOR", "50"))
PACE_SECONDS = float(os.environ.get("PACE_SECONDS", "5"))
# Seconds an author's record must still have before it expires, so fetching one
# of its works doesn't start a refresh of the whole author.
AUTHOR_TTL_MARGIN = 3600
# Bookshelf commands that look books or authors up through rreading-glasses.
BUSY_COMMANDS = {"RefreshAuthor", "RefreshBook", "RescanFolders", "DownloadedBooksScan",
                 "ManualImport", "BulkRefreshAuthor", "ImportListSync", "AuthorSearch", "BookSearch"}
PAGE = 500


class CantRead(Exception):
    pass


def http(method: str, url: str, headers: dict | None = None, body=None, timeout: int = 60):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = dict(headers or {})
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise CantRead(f"{method} {url.split('?')[0]}: {e}") from e


def bookshelf(method: str, path: str, body=None):
    status, raw = http(method, f"{BOOKSHELF_URL}/api/v1/{path}",
                       {"X-Api-Key": os.environ["BOOKSHELF_API_KEY"]}, body)
    if status >= 300:
        raise CantRead(f"Bookshelf {method} {path}: HTTP {status}")
    return json.loads(raw) if raw else None


def metrics() -> dict[str, float]:
    status, raw = http("GET", f"{RG_URL}/debug/metrics")
    if status != 200:
        raise CantRead(f"rreading-glasses metrics: HTTP {status}")
    out = {}
    for line in raw.decode().splitlines():
        m = re.match(r'^(rg_[a-z_]+(?:\{[^}]*\})?) (\S+)$', line)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def daily_remaining(m: dict[str, float]) -> float | None:
    return m.get('rg_gql_ratelimit_remaining{bucket="daily"}')


def pending(m: dict[str, float]) -> float:
    return (m.get('rg_controller_pending_operations{type="refresh"}', 0)
            + m.get('rg_controller_pending_operations{type="denormalization"}', 0)
            + m.get('rg_db_total{type="refreshing"}', 0))


def decode(value: bytes):
    """A cache value: gzipped JSON, or upstream's marker for a missing record."""
    try:
        return json.loads(gzip.decompress(value))
    except (OSError, ValueError):
        return None


class Cache:
    """rreading-glasses' cache table, read-only."""

    def __init__(self):
        import pg8000.native
        self.db = pg8000.native.Connection(
            user=os.environ["PGUSER"], password=os.environ["PGPASSWORD"],
            host=os.environ["PGHOST"], port=int(os.environ.get("PGPORT", "5432")), database=os.environ["PGDATABASE"],
            application_name="rreading-glasses-repair")
        self.db.run("SET default_transaction_read_only = on")

    def rows(self, keys: list[str]):
        if not keys:
            return []
        return self.db.run("SELECT key, value, extract(epoch FROM expires - now()) FROM cache "
                           "WHERE key = ANY(:k)", k=keys)

    def works(self):
        """Every cached work, a page at a time: (key, value)."""
        last = ""
        while True:
            page = self.db.run("SELECT key, value FROM cache WHERE key ~ '^w[0-9]+$' AND key > :last "
                               "ORDER BY key LIMIT :n", last=last, n=PAGE)
            yield from page
            if len(page) < PAGE:
                return
            last = page[-1][0]

    def expired(self, key: str) -> bool:
        rows = self.db.run("SELECT expires <= now() FROM cache WHERE key = :k", k=key)
        return bool(rows and rows[0][0])

    def close(self):
        self.db.close()


def works_by_author(works) -> dict[int, set[int]]:
    """Each author's works among the cached ones: those with editions, by their first author."""
    out: dict[int, set[int]] = {}
    for _key, value in works:
        w = decode(value)
        if not isinstance(w, dict) or not w.get("Books"):
            continue
        authors = w.get("Authors") or []
        if authors and authors[0].get("ForeignId"):
            out.setdefault(int(authors[0]["ForeignId"]), set()).add(int(w["ForeignId"]))
    return out


def missing(author: dict, cached: set[int]) -> list[int]:
    have = {int(w["ForeignId"]) for w in author.get("Works") or []}
    return sorted(cached - have)


def plan(short: dict[int, list[int]], max_works: int, large: int) -> list[tuple[int, list[int]]]:
    """Smallest authors first, up to max_works works; one large author ends the run."""
    out, total = [], 0
    for aid, works in sorted(short.items(), key=lambda kv: (len(kv[1]), kv[0])):
        room = max_works - total
        if room <= 0:
            break
        take = works[:room]
        out.append((aid, take))
        total += len(take)
        if len(works) > large:
            break
    return out


def wait_settled(timeout: int = 300) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pending(metrics()) == 0:
            return True
        time.sleep(5)
    return False


def main() -> int:
    mode = "dry run: changes nothing" if DRY_RUN else "repair"
    print(f"rreading-glasses repair ({mode})")

    busy = sorted({c["name"] for c in bookshelf("GET", "command")
                   if c.get("status") in ("queued", "started") and c.get("name") in BUSY_COMMANDS})
    m = metrics()
    left = daily_remaining(m)
    print(f"Hardcover daily budget left: {'no reading yet' if left is None else int(left)}")
    if busy:
        print(f"Bookshelf is busy ({', '.join(busy)}): skipped")
    if pending(m):
        print("rreading-glasses has a refresh or denormalization pending: skipped")

    authors = {int(a["foreignAuthorId"]): a for a in bookshelf("GET", "author")}
    cache = Cache()
    try:
        return repair(authors, m, left, busy, cache)
    finally:
        cache.close()


def repair(authors: dict, m: dict[str, float], left: float | None, busy: list[str], cache: "Cache") -> int:
    records = {}
    for key, value, ttl in cache.rows([f"a{i}" for i in authors] + [f"ra{i}" for i in authors]):
        records[key] = (decode(value), float(ttl))
    cached = works_by_author(cache.works())

    short: dict[int, list[int]] = {}
    for aid in sorted(authors):
        rec, ttl = records.get(f"a{aid}", (None, 0.0))
        if not isinstance(rec, dict):
            print(f"  author {aid}: not in rreading-glasses' cache")
            continue
        miss = missing(rec, cached.get(aid, set()))
        if not miss:
            continue
        why = ("mid-refresh" if f"ra{aid}" in records
               else "record expires within the hour" if ttl < AUTHOR_TTL_MARGIN else "")
        print(f"  author {aid} (Bookshelf {authors[aid]['id']}): {len(rec.get('Works') or [])} works, "
              f"{len(miss)} missing{f'; skipped, {why}' if why else ''}")
        if not why:
            short[aid] = miss
    print(f"{len(short)} author(s) to repair, {sum(len(v) for v in short.values())} work(s)")

    if DRY_RUN or busy or pending(m) or not short:
        return 0
    if left is not None and left < MIN_DAILY_REMAINING:
        print(f"Budget below {MIN_DAILY_REMAINING}: skipped")
        return 0

    failed, done = 0, 0
    for aid, works in plan(short, MAX_WORKS, LARGE_AUTHOR):
        print(f"Author {aid}: {len(works)} work(s)")
        tried = []
        for wid in works:
            left = daily_remaining(metrics())
            # Every stop first checks the works already fetched for this author, and has
            # Bookshelf refresh it: they were expired and fetched again.
            if left is None and done:
                print("STOP: no budget reading after a lookup, so what is left can't be told")
                report(aid, tried, authors, failed, cache)
                return 2
            if left is not None and left < MIN_DAILY_REMAINING:
                print(f"STOP: budget down to {int(left)}")
                return report(aid, tried, authors, failed, cache)
            status, _ = http("DELETE", f"{RG_URL}/work/{wid}")
            if status != 200 or not cache.expired(f"w{wid}"):
                print(f"FAIL: DELETE /work/{wid} didn't expire it (HTTP {status})")
                report(aid, tried, authors, failed, cache)
                return 1
            status, _ = http("GET", f"{RG_URL}/work/{wid}", timeout=120)
            done += 1
            if status != 200:
                print(f"STOP: GET /work/{wid}: HTTP {status}")
                return report(aid, tried, authors, failed, cache) or 1
            tried.append(wid)
            time.sleep(PACE_SECONDS)
        failed = report(aid, tried, authors, failed, cache)
    left = daily_remaining(metrics())
    print(f"Done: {done} lookup(s); budget left {'unknown' if left is None else int(left)}")
    return 1 if failed else 0


def report(aid: int, tried: list[int], authors: dict, failed: int, cache: Cache) -> int:
    """Waits for the attachments, reads the author again, refreshes it in Bookshelf."""
    if not tried:
        return failed
    if not wait_settled():
        print(f"FAIL: author {aid}: rreading-glasses still busy after 5 minutes; not checked")
        return failed + 1
    rows = cache.rows([f"a{aid}"])
    rec = decode(rows[0][1]) if rows else None
    have = {int(w["ForeignId"]) for w in (rec or {}).get("Works") or []}
    lost = [w for w in tried if w not in have]
    print(f"  attached {len(tried) - len(lost)} of {len(tried)}"
          + (f"; not attached: {', '.join(map(str, lost))}" if lost else ""))
    bookshelf("POST", "command", {"name": "RefreshAuthor", "authorId": authors[aid]["id"]})
    return failed + (1 if lost else 0)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CantRead as e:
        print(f"FAIL: couldn't read: {e}")
        sys.exit(2)
