"""rreading-glasses-repair.py against stand-ins for rreading-glasses (its cache, metrics and work
endpoints) and Bookshelf: which authors are short, what a run repairs and in what order, and
every condition that skips or stops it, since each repair spends Hardcover's daily budget."""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "kubernetes/prod/apps/media/rreading-glasses/app/repair/rreading-glasses-repair.py"


def gz(obj) -> bytes:
    return gzip.compress(json.dumps(obj).encode())


class World:
    """rreading-glasses and Bookshelf in memory. Author 1 has works 11 and 12 attached and 13
    cached but missing; author 2 has 21 missing; author 3 is complete."""

    def __init__(self, m):
        self.m = m
        self.works = {11: 1, 12: 1, 13: 1, 21: 2, 31: 3, 14: None}  # work -> first author (14: no editions)
        self.attached = {1: {11, 12}, 2: set(), 3: {31}}
        self.ttl = {1: 86400.0, 2: 86400.0, 3: 86400.0}
        self.refreshing: set[int] = set()
        self.expired: set[int] = set()
        self.budget = [5000.0]                   # each reading takes the next, the last repeats
        self.pending = 0
        self.commands = []                        # Bookshelf's queue
        self.posted = []
        self.calls = []
        self.attach_fails: set[int] = set()
        self.delete_ignored: set[int] = set()
        self.get_status = 200

    # -- rreading-glasses' cache, as Cache reads it
    def rows(self, keys):
        out = []
        for key in keys:
            aid = int(key.lstrip("ra"))
            if key.startswith("ra"):
                if aid in self.refreshing:
                    out.append((key, gz({}), 600.0))
            elif aid in self.attached:
                out.append((key, gz({"Works": [{"ForeignId": w} for w in sorted(self.attached[aid])]}), self.ttl[aid]))
        return out

    def cached_works(self):
        for wid, aid in sorted(self.works.items()):
            books = [{"x": 1}] if aid is not None else []
            authors = [{"ForeignId": aid if aid is not None else 1}, {"ForeignId": 99}]
            yield f"w{wid}", gz({"ForeignId": wid, "Books": books, "Authors": authors})
        yield "w999", b"not gzip"  # upstream's marker for a missing record

    # -- HTTP
    def http(self, method, url, headers=None, body=None, timeout=60):
        self.calls.append((method, url.split("//", 1)[1].split("/", 1)[1]))
        if url.startswith(self.m.RG_URL):
            path = url[len(self.m.RG_URL):]
            if path == "/debug/metrics":
                left = self.budget.pop(0) if len(self.budget) > 1 else self.budget[0]
                lines = [f'rg_controller_pending_operations{{type="refresh"}} {self.pending}',
                         'rg_controller_pending_operations{type="denormalization"} 0', "other_metric 1"]
                if left is not None:
                    lines.append(f'rg_gql_ratelimit_remaining{{bucket="daily"}} {left}')
                return 200, "\n".join(lines).encode()
            wid = int(path.rsplit("/", 1)[1])
            if method == "DELETE":
                if wid not in self.delete_ignored:
                    self.expired.add(wid)
                return 200, b""
            if self.get_status != 200:
                return self.get_status, b""
            if wid not in self.attach_fails:
                self.attached[self.works[wid]].add(wid)
            return 200, b"{}"
        path = url.split("/api/v1/", 1)[1]
        assert headers["X-Api-Key"] == "test-key"
        if path == "command" and method == "GET":
            return 200, json.dumps(self.commands).encode()
        if path == "command":
            self.posted.append(body)
            return 201, b"{}"
        if path == "author":
            return 200, json.dumps([{"id": 100 + a, "foreignAuthorId": str(a)} for a in (1, 2, 3)]).encode()
        raise AssertionError(url)

    def lookups(self):
        return [p for meth, p in self.calls if meth == "GET" and p.startswith("work/")]


class FakeCache:
    def __init__(self, world):
        self.world = world

    def rows(self, keys):
        return self.world.rows(keys)

    def works(self):
        return self.world.cached_works()

    def expired(self, key):
        return int(key[1:]) in self.world.expired

    def close(self):
        pass


@pytest.fixture
def run(monkeypatch):
    def make(dry_run="", **env):
        monkeypatch.setenv("DRY_RUN", dry_run)
        monkeypatch.setenv("BOOKSHELF_API_KEY", "test-key")
        for k, v in env.items():
            monkeypatch.setenv(k, str(v))
        monkeypatch.setattr(sys, "dont_write_bytecode", True)
        loader = importlib.util.spec_from_file_location("rg_repair_under_test", SCRIPT)
        m = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(m)
        world = World(m)
        m.http = world.http
        m.Cache = lambda: FakeCache(world)
        monkeypatch.setattr(m.time, "sleep", lambda s: None)
        return m, world
    return make


# --- the pure parts ---------------------------------------------------------------------------

def test_works_are_each_first_authors_and_need_editions(run):
    m, world = run()
    assert m.works_by_author(world.cached_works()) == {1: {11, 12, 13}, 2: {21}, 3: {31}}


def test_missing_is_what_the_cache_holds_and_the_author_lacks(run):
    m, _ = run()
    assert m.missing({"Works": [{"ForeignId": "11"}, {"ForeignId": 12}]}, {11, 12, 13, 14}) == [13, 14]
    assert m.missing({}, {5}) == [5]


def test_plan_takes_the_smallest_authors_first_up_to_the_limit_and_stops_after_a_large_one(run):
    m, _ = run()
    short = {7: [1, 2, 3], 8: [4], 9: list(range(10, 70))}
    assert m.plan(short, 300, 50) == [(8, [4]), (7, [1, 2, 3]), (9, list(range(10, 70)))]
    assert m.plan(short, 3, 50) == [(8, [4]), (7, [1, 2])]
    assert m.plan({1: list(range(60)), 2: [1]}, 300, 50) == [(2, [1]), (1, list(range(60)))]
    big_first = {1: list(range(60)), 2: list(range(70))}
    assert [a for a, _ in m.plan(big_first, 300, 50)] == [1], "one large author ends the run"


def test_metrics_read_the_budget_and_whats_pending(run):
    m, world = run()
    world.pending = 2
    got = m.metrics()
    assert m.daily_remaining(got) == 5000.0 and m.pending(got) == 2
    assert "other_metric" not in got
    assert m.daily_remaining({}) is None


def test_decode_reads_gzipped_json_and_takes_anything_else_as_missing(run):
    m, _ = run()
    assert m.decode(gz({"a": 1})) == {"a": 1}
    assert m.decode(b"not gzip") is None


# --- a run --------------------------------------------------------------------------------------

def test_a_run_repairs_the_short_authors_smallest_first_and_refreshes_each(run, capsys):
    m, world = run()
    assert m.main() == 0
    assert world.lookups() == ["work/13", "work/21"], "both one work short: by author id"
    assert world.attached[1] == {11, 12, 13} and world.attached[2] == {21}
    assert world.posted == [{"name": "RefreshAuthor", "authorId": 101}, {"name": "RefreshAuthor", "authorId": 102}]
    assert [c for c in world.calls if c[0] == "DELETE"] == [("DELETE", "work/13"), ("DELETE", "work/21")]
    assert "attached 1 of 1" in capsys.readouterr().out


def test_a_dry_run_reports_and_looks_nothing_up(run, capsys):
    m, world = run(dry_run="true")
    assert m.main() == 0
    assert world.lookups() == [] and world.posted == []
    out = capsys.readouterr().out
    assert "author 1 (Bookshelf 101): 2 works, 1 missing" in out and "2 author(s) to repair, 2 work(s)" in out


@pytest.mark.parametrize("why", ["bookshelf busy", "rg pending", "budget low", "nothing short"])
def test_a_run_is_skipped_without_one_lookup(run, why):
    m, world = run()
    if why == "bookshelf busy":
        world.commands = [{"name": "RefreshAuthor", "status": "started"}, {"name": "Backup", "status": "queued"}]
    elif why == "rg pending":
        world.pending = 1
    elif why == "budget low":
        world.budget = [1999.0]
    else:
        world.attached = {1: {11, 12, 13}, 2: {21}, 3: {31}}
    assert m.main() == 0
    assert world.lookups() == [] and world.posted == []


def test_a_finished_bookshelf_command_doesnt_hold_a_run(run):
    m, world = run()
    world.commands = [{"name": "RefreshAuthor", "status": "completed"}]
    assert m.main() == 0 and world.lookups()


def test_an_author_mid_refresh_or_about_to_expire_is_skipped(run, capsys):
    m, world = run()
    world.refreshing = {1}
    world.ttl[2] = 1800.0
    assert m.main() == 0
    assert world.lookups() == []
    out = capsys.readouterr().out
    assert "skipped, mid-refresh" in out and "skipped, record expires within the hour" in out


def test_a_budget_that_drops_mid_run_stops_it_and_checks_what_was_done(run, capsys):
    m, world = run()
    world.budget = [5000.0, 5000.0, 1500.0]  # the run's reading, before work 13, before work 21
    assert m.main() == 0
    assert world.lookups() == ["work/13"]
    assert world.posted == [{"name": "RefreshAuthor", "authorId": 101}]
    assert "STOP: budget down to 1500" in capsys.readouterr().out


def test_no_budget_reading_after_a_lookup_stops_with_2(run):
    m, world = run()
    world.budget = [None, None]
    assert m.main() == 2
    assert world.lookups() == ["work/13"]


def test_a_delete_that_didnt_expire_the_work_stops_with_1(run):
    m, world = run()
    world.delete_ignored = {13}
    assert m.main() == 1
    assert world.lookups() == []


def test_a_work_that_didnt_attach_answers_1_and_names_it(run, capsys):
    m, world = run()
    world.attach_fails = {21}
    assert m.main() == 1
    assert "not attached: 21" in capsys.readouterr().out
    assert len(world.posted) == 2, "the author is refreshed in Bookshelf either way"


def test_a_failed_lookup_stops_with_1(run):
    m, world = run()
    world.get_status = 429
    assert m.main() == 1
    assert world.lookups() == ["work/13"]


def test_max_works_bounds_a_run(run):
    m, world = run(MAX_WORKS=1)
    assert m.main() == 0
    assert world.lookups() == ["work/13"]


def test_attachments_never_settling_fail_the_check(run, monkeypatch, capsys):
    m, world = run()
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(m.time, "time", lambda: next(clock))
    real_metrics = m.metrics

    def busy_after_lookups():
        got = real_metrics()
        if world.lookups():
            got['rg_controller_pending_operations{type="refresh"}'] = 1
        return got

    m.metrics = busy_after_lookups
    assert m.main() == 1
    assert "still busy after 5 minutes; not checked" in capsys.readouterr().out


def test_bookshelf_refusing_a_read_cant_read(run):
    m, world = run()
    real = world.http
    world_http = lambda method, url, headers=None, body=None, timeout=60: (  # noqa: E731
        (500, b"") if url.endswith("/api/v1/author") else real(method, url, headers, body, timeout))
    m.http = world_http
    with pytest.raises(m.CantRead):
        m.main()


# --- the cache's reads: a stand-in for pg8000 that pages as Postgres would ----------------------

class FakeConnection:
    def __init__(self, keys, expired=(), **kw):
        self.keys, self.expired_keys, self.ran = sorted(keys), set(expired), []

    def run(self, sql, **params):
        self.ran.append((sql, params))
        if sql.startswith("SET "):
            return []
        if "key ~ '^w[0-9]+$'" in sql:
            after = [k for k in self.keys if k > params["last"]]
            return [(k, b"v") for k in after[:params["n"]]]
        if "expires <= now()" in sql:
            return [(params["k"] in self.expired_keys,)] if params["k"] in self.keys else []
        return [(k, b"v", 100.0) for k in self.keys if k in params["k"]]

    def close(self):
        self.closed = True


def _cache(monkeypatch, run, **conn_kw):
    m, _ = run()
    import types as _types
    conn = FakeConnection(**conn_kw)
    pg = _types.SimpleNamespace(native=_types.SimpleNamespace(Connection=lambda **kw: conn))
    monkeypatch.setitem(sys.modules, "pg8000", pg)
    monkeypatch.setitem(sys.modules, "pg8000.native", pg.native)
    for k in ("PGUSER", "PGPASSWORD", "PGHOST", "PGDATABASE"):
        monkeypatch.setenv(k, "x")
    loader = importlib.util.spec_from_file_location("rg_repair_cache_under_test", SCRIPT)
    real = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(real)
    return real.Cache(), conn


def test_the_cache_is_read_only_and_every_work_is_read_page_by_page(monkeypatch, run):
    keys = [f"w{i}" for i in range(1, 1201)]
    cache, conn = _cache(monkeypatch, run, keys=keys)
    assert conn.ran[0][0] == "SET default_transaction_read_only = on"
    got = [k for k, _ in cache.works()]
    assert got == sorted(keys) and len(got) == 1200
    assert len([s for s, _ in conn.ran if "key ~" in s]) == 3, "pages of 500: 500, 500, 200"


def test_a_last_page_of_exactly_500_ends_with_an_empty_read(monkeypatch, run):
    cache, conn = _cache(monkeypatch, run, keys=[f"w{i}" for i in range(500)])
    assert len(list(cache.works())) == 500


def test_the_cache_reads_rows_and_expiry_and_closes(monkeypatch, run):
    cache, conn = _cache(monkeypatch, run, keys=["a1", "w5"], expired={"w5"})
    assert cache.rows([]) == [] and conn.ran[-1][0].startswith("SET"), "no keys: no query"
    assert cache.rows(["a1", "a2"]) == [("a1", b"v", 100.0)]
    assert cache.expired("w5") and not cache.expired("a1") and not cache.expired("w6")
    cache.close()
    assert conn.closed
