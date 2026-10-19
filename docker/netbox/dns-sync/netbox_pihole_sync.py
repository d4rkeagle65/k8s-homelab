"""Copies NetBox DNS's records to every Pi-hole: old-queues/QUEUE-netbox-dns.md.

NetBox is the one owner of local DNS. This owns every name inside a NetBox zone
on each Pi-hole, and nothing outside them: it adds the A, AAAA and CNAME records
NetBox has and a Pi-hole lacks, and removes a Pi-hole record in a NetBox zone
that NetBox doesn't have only when SYNC_DELETE is true. Pi-hole keeps addresses
as "<ip> <name> [<name>...]" lines (dns.hosts); a line naming more than one name
inside a zone is replaced by one line per name. Every change is read back.

A failed read is not an empty list: NetBox not answering, or answering with no
zone or no record, stops the run before anything is written; so does a Pi-hole
that refuses a read or a login.

Without --apply (or SYNC_APPLY=true) it only reports. --every SECONDS runs it in a loop (the stack's
service), each round starting afresh, a failed round reported and the next one
tried; a round that wrote cleanly touches --health, which the healthcheck reads.

Configuration from the environment, never arguments: NETBOX_API and
NETBOX_TOKEN (read-only); PIHOLE_1_URL and PIHOLE_1_PASSWORD (an app password),
PIHOLE_2_..., as many as are numbered without a gap; PIHOLE_TLS_VERIFY (default
true); SYNC_APPLY and SYNC_DELETE (default false).
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class Refused(Exception):
    """A read failed or answered nothing usable; nothing is written."""


def unexpected(extras: list[str]) -> str:
    """Names unknown arguments without their values (a value may be a password)."""
    names = sorted({e.split("=", 1)[0] for e in extras if e.startswith("-")}) or ["(values only)"]
    return f"FAIL: unexpected arguments: {', '.join(names)}; their values aren't shown"


def call(method: str, url: str, headers: dict, body=None, verify: bool = True):
    """(status, parsed JSON or None). An HTTP error is an answer; no answer raises Refused."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={**headers, "Content-Type": "application/json", "Accept": "application/json"})
    ctx = None if verify else ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None
    except (urllib.error.URLError, OSError) as e:
        raise Refused(f"{method} {url.split('?')[0]}: no answer ({getattr(e, 'reason', e)})")


# --- NetBox -------------------------------------------------------------------

def netbox_wanted(url: str, token: str) -> tuple[list[str], set[tuple[str, str, str]]]:
    """NetBox's active zones, and its active A, AAAA and CNAME records in them as
    (name, type, value), names fully qualified without a trailing dot."""
    token = token.strip().removeprefix("Bearer ")
    h = {"Authorization": f"Bearer {token}"}

    def every(path: str) -> list[dict]:
        out, nxt = [], f"{url.rstrip('/')}/api/plugins/netbox-dns/{path}/?limit=500"
        while nxt:
            status, body = call("GET", nxt, h)
            if status != 200 or not isinstance(body, dict) or "results" not in body:
                raise Refused(f"NetBox's {path} didn't answer (HTTP {status})")
            out += body["results"]
            nxt = body.get("next")
        return out

    zones = {z["id"]: z["name"].rstrip(".").lower() for z in every("zones") if status_of(z) == "active"}
    if not zones:
        raise Refused("NetBox lists no active zone: refusing to treat that as no records")
    wanted = set()
    for r in every("records"):
        zone = zones.get((r.get("zone") or {}).get("id"))
        if zone is None or status_of(r) != "active" or r.get("type") not in ("A", "AAAA", "CNAME"):
            continue
        name = zone if r["name"] in ("@", "") else f"{r['name'].rstrip('.').lower()}.{zone}"
        value = r["value"].strip()
        if r["type"] == "CNAME":
            value = value.rstrip(".").lower()
            if "." not in value:  # a relative target is inside its own zone
                value = f"{value}.{zone}"
        wanted.add((name, r["type"], value))
    if not wanted:
        raise Refused("NetBox lists no A, AAAA or CNAME record: refusing to treat that as none wanted")
    return sorted(zones.values()), wanted


def status_of(obj: dict) -> str:
    s = obj.get("status")
    return (s.get("value") if isinstance(s, dict) else s) or "active"


# --- the plan -----------------------------------------------------------------

def in_zone(name: str, zones: list[str]) -> bool:
    return any(name == z or name.endswith("." + z) for z in zones)


def plan(zones: list[str], wanted: set, hosts: list[str], cnames: list[str], delete: bool):
    """What one Pi-hole needs, as (remove_hosts, add_hosts, remove_cnames, add_cnames,
    left): Pi-hole's own line texts to remove and add, and the records in NetBox's zones
    a delete would remove but SYNC_DELETE is off for."""
    want_a = {w for w in wanted if w[1] in ("A", "AAAA")}
    have, readd, remove_hosts, left = set(), set(), [], []
    for line in hosts:
        ip, *names = line.split()
        kind = "AAAA" if ":" in ip else "A"
        if not any(in_zone(n.lower(), zones) for n in names):
            continue
        if len(names) == 1:
            pair = (names[0].lower(), kind, ip)
            if pair in want_a:
                have.add(pair)
            elif delete:
                remove_hosts.append(line)
            else:
                left.append(line)
            continue
        # One name per line: the line goes, and each of its names comes back alone, a
        # name outside NetBox's zones as it was spelled, so the split never drops one.
        remove_hosts.append(line)
        for n in names:
            pair = (n.lower(), kind, ip) if in_zone(n.lower(), zones) else (n, kind, ip)
            if in_zone(pair[0], zones) and pair not in want_a:
                if delete:
                    continue
                left.append(f"{ip} {pair[0]}")
            readd.add(pair)
    add_hosts = sorted(f"{ip} {n}" for n, _, ip in (want_a - have) | (readd - have))

    have_c, remove_cnames = set(), []
    for line in cnames:
        name, target = (line.split(",") + [""])[:2]
        name, target = name.strip().lower(), target.strip().rstrip(".").lower()
        if not in_zone(name, zones):
            continue
        if (name, "CNAME", target) in wanted:
            have_c.add((name, "CNAME", target))
        elif delete:
            remove_cnames.append(line)
        else:
            left.append(line)
    add_cnames = sorted(f"{n},{t}" for n, _, t in {w for w in wanted if w[1] == "CNAME"} - have_c)
    return remove_hosts, add_hosts, remove_cnames, add_cnames, sorted(left)


# --- Pi-hole ------------------------------------------------------------------

class PiHole:
    """One Pi-hole's v6 API, by a session from its app password; logged out after."""

    def __init__(self, url: str, password: str, verify: bool):
        self.api = url.rstrip("/") + "/api/"
        self.verify = verify
        status, body = call("POST", self.api + "auth", {}, {"password": password}, verify)
        sid = ((body or {}).get("session") or {}).get("sid") if isinstance(body, dict) else None
        if status != 200 or not sid:
            raise Refused(f"{url}: login refused (HTTP {status})")
        self.h = {"X-FTL-SID": sid}

    def lists(self) -> tuple[list[str], list[str]]:
        status, body = call("GET", self.api + "config/dns", self.h, verify=self.verify)
        try:
            dns = body["config"]["dns"]
            return list(dns["hosts"]), list(dns["cnameRecords"])
        except (TypeError, KeyError):
            raise Refused(f"{self.api}: config/dns didn't answer (HTTP {status})")

    def change(self, method: str, kind: str, value: str) -> None:
        status, body = call(method, self.api + f"config/dns/{kind}/" + urllib.parse.quote(value, safe=""), self.h,
                            verify=self.verify)
        if status not in (200, 201, 204):
            error = (body or {}).get("error", {}) if isinstance(body, dict) else {}
            raise Refused(f"{method} {kind} {value}: HTTP {status} {error.get('message', '')}".rstrip())

    def close(self) -> None:
        try:
            call("DELETE", self.api + "auth", self.h, verify=self.verify)
        except Refused as e:
            # Pi-hole caps its sessions; an unclosed one lapses at its own timeout.
            print(f"  note: logout not confirmed ({e}); the session lapses on its own")


def piholes_from(env) -> list[tuple[str, str]]:
    out, n = [], 1
    while env.get(f"PIHOLE_{n}_URL"):
        if not env.get(f"PIHOLE_{n}_PASSWORD"):
            raise SystemExit(f"FAIL: PIHOLE_{n}_URL is set without PIHOLE_{n}_PASSWORD")
        out.append((env[f"PIHOLE_{n}_URL"], env[f"PIHOLE_{n}_PASSWORD"]))
        n += 1
    if not out:
        raise SystemExit("FAIL: set PIHOLE_1_URL and PIHOLE_1_PASSWORD (and _2_ for the next)")
    return out


def sync_one(url: str, password: str, zones, wanted, apply: bool, delete: bool, verify: bool) -> bool:
    """One Pi-hole: report, and with apply write and read back. True when it matches."""
    ph = PiHole(url, password, verify)
    try:
        hosts, cnames = ph.lists()
        rm_h, add_h, rm_c, add_c, left = plan(zones, wanted, hosts, cnames, delete)
        label = urllib.parse.urlsplit(url).hostname
        print(f"{label}: {len(hosts)} host lines, {len(cnames)} CNAMEs; remove {len(rm_h)} + {len(rm_c)}, "
              f"add {len(add_h)} + {len(add_c)}" + (f"; {len(left)} not in NetBox, kept (SYNC_DELETE is off)" if left else ""))
        for line in rm_h + rm_c:
            print(f"  remove {line}")
        for line in add_h + add_c:
            print(f"  add    {line}")
        if not apply or not (rm_h or add_h or rm_c or add_c):
            return True
        # Removals first: a split line's names come back one per line after it goes.
        for line in rm_h:
            ph.change("DELETE", "hosts", line)
        for line in rm_c:
            ph.change("DELETE", "cnameRecords", line)
        for line in add_h:
            ph.change("PUT", "hosts", line)
        for line in add_c:
            ph.change("PUT", "cnameRecords", line)
        hosts, cnames = ph.lists()
        again = plan(zones, wanted, hosts, cnames, delete)
        if any(again[:4]):
            print(f"  FAIL: read back, {label} still differs: {sum(map(len, again[:4]))} changes outstanding")
            return False
        print(f"  read back: {label} matches NetBox")
        return True
    finally:
        ph.close()


def round_(args, env) -> bool:
    verify = env.get("PIHOLE_TLS_VERIFY", "true").lower() != "false"
    delete = env.get("SYNC_DELETE", "false").lower() == "true"
    zones, wanted = netbox_wanted(env["NETBOX_API"], env["NETBOX_TOKEN"])
    print(f"NetBox: {len(zones)} zones, {len(wanted)} records; deletes {'on' if delete else 'off'}"
          + ("" if args.apply else "; dry run"))
    ok = True
    for url, password in piholes_from(env):
        try:
            ok &= sync_one(url, password, zones, wanted, args.apply, delete, verify)
        except Refused as e:
            print(f"  FAIL: {e}")
            ok = False
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="write the changes (default: report only)")
    ap.add_argument("--every", type=int, default=0, help="run every SECONDS, for ever")
    ap.add_argument("--health", default="", help="a file touched after each clean round")
    args, extras = ap.parse_known_args()
    if extras:
        raise SystemExit(unexpected(extras))
    env = os.environ
    if not env.get("NETBOX_API") or not env.get("NETBOX_TOKEN"):
        raise SystemExit("FAIL: set NETBOX_API and NETBOX_TOKEN (a read-only NetBox token)")
    piholes_from(env)
    args.apply = args.apply or env.get("SYNC_APPLY", "false").lower() == "true"
    while True:
        try:
            ok = round_(args, env)
        except Refused as e:
            print(f"FAIL: {e}; nothing was written")
            ok = False
        if ok and args.health:
            Path(args.health).touch()
        sys.stdout.flush()
        if not args.every:
            raise SystemExit(0 if ok else 1)
        time.sleep(args.every)


if __name__ == "__main__":
    main()
