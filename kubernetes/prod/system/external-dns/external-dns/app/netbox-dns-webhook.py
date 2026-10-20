"""ExternalDNS's webhook provider for NetBox DNS: the Ingress hostnames ExternalDNS
finds become records in NetBox (old-queues/QUEUE-netbox.md, phase 4). It runs beside
ExternalDNS in its pod and listens on 127.0.0.1 only, so nothing else can reach
its unauthenticated API.

ExternalDNS's webhook API (version 1): GET / negotiates the domain filter, GET
/records lists the records this provider owns, POST /records applies changes,
POST /adjustendpoints passes endpoints through. /healthz on PORT_HEALTH answers
the probes.

Ownership: a record this provider writes carries OWNER_MARK as its description;
only those are listed, changed or deleted. A hand-made record of the same name
and type is never touched: the change is skipped and logged. A record goes in
the NetBox DNS zone whose name is the longest suffix of the hostname; with no
such zone it's skipped and logged (zones are made by hand in NetBox).

A failed read of NetBox answers 500, never an empty list: ExternalDNS would read
an empty answer as "nothing exists" and create everything again.

Settings from the environment: NETBOX_URL, NETBOX_TOKEN (with or without
"Bearer "), DOMAIN_FILTER (comma-separated). Standard library only.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

MEDIA = "application/external.dns.webhook+json;version=1"
OWNER_MARK = "external-dns"
TYPES = {"A", "AAAA", "CNAME", "TXT"}


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), msg, flush=True)


class NetBoxError(Exception):
    """NetBox didn't answer as asked; never read as an empty answer."""


class NetBox:
    def __init__(self, url: str, token: str):
        self.base = url.rstrip("/") + "/api/plugins/netbox-dns/"
        token = token.strip().removeprefix("Bearer ").strip()
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
                        "Content-Type": "application/json"}

    def call(self, method: str, path: str, body=None):
        req = urllib.request.Request(self.base + path, method=method, headers=self.headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise NetBoxError(f"{method} {path.split('?')[0]}: HTTP {e.code} {e.read()[:200]!r}") from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise NetBoxError(f"{method} {path.split('?')[0]}: {getattr(e, 'reason', e)}") from None

    def all(self, path: str) -> list:
        out, url = [], f"{path}?limit=500"
        while url:
            page = self.call("GET", url)
            out += page["results"]
            nxt = page.get("next")
            url = nxt.split("/api/plugins/netbox-dns/", 1)[1] if nxt else None
        return out


def zone_for(fqdn: str, zones: list[dict]) -> dict | None:
    """The zone whose name is the longest suffix of fqdn (or fqdn itself)."""
    fqdn = fqdn.rstrip(".").lower()
    best = None
    for z in zones:
        name = z["name"].rstrip(".").lower()
        if (fqdn == name or fqdn.endswith("." + name)) and (best is None or len(name) > len(best["name"])):
            best = z
    return best


def relative(fqdn: str, zone_name: str) -> str:
    fqdn, zone_name = fqdn.rstrip(".").lower(), zone_name.rstrip(".").lower()
    return "@" if fqdn == zone_name else fqdn[: -(len(zone_name) + 1)]


def fqdn_of(record: dict, zones_by_id: dict) -> str:
    """A record's full name, without the trailing dot: NetBox DNS's fqdn field,
    else its name in its zone ("@" being the zone itself)."""
    if record.get("fqdn"):
        return record["fqdn"].rstrip(".")
    zone = record.get("zone") or {}
    zname = zones_by_id.get(zone.get("id"), zone).get("name", "").rstrip(".")
    return zname if record["name"] == "@" else f"{record['name']}.{zname}"


def endpoints(records: list[dict], zones_by_id: dict) -> list[dict]:
    """Owned records grouped as ExternalDNS endpoints: one per name and type."""
    grouped: dict[tuple, dict] = {}
    for r in records:
        if r.get("description") != OWNER_MARK or r.get("type") not in TYPES:
            continue
        key = (fqdn_of(r, zones_by_id), r["type"])
        ep = grouped.setdefault(key, {"dnsName": key[0], "recordType": key[1], "targets": [],
                                      "recordTTL": r.get("ttl") or 0, "labels": {}, "providerSpecific": []})
        ep["targets"].append(r["value"])
    for ep in grouped.values():
        ep["targets"].sort()
    return sorted(grouped.values(), key=lambda e: (e["dnsName"], e["recordType"]))


def plan(changes: dict, records: list[dict], zones: list[dict]) -> tuple[list, list, list]:
    """What to create and delete in NetBox for ExternalDNS's changes, and what was
    skipped and why: (creates, deletes by id, skipped messages)."""
    # ExternalDNS names the lists Create or create (camelCase from 0.2x): either.
    changes = {k.lower(): v for k, v in (changes or {}).items()}
    zones_by_id = {z["id"]: z for z in zones}
    current: dict[tuple, list] = {}
    for r in records:
        current.setdefault((fqdn_of(r, zones_by_id).lower(), r["type"]), []).append(r)
    creates, deletes, skipped = [], [], []
    wanted = list(changes.get("create") or []) + list(changes.get("updatenew") or [])
    for ep in wanted:
        name, rtype = ep["dnsName"].rstrip(".").lower(), ep.get("recordType", "A")
        if rtype not in TYPES:
            skipped.append(f"{name} {rtype}: type not handled")
            continue
        zone = zone_for(name, zones)
        if zone is None:
            skipped.append(f"{name} {rtype}: no NetBox DNS zone holds it")
            continue
        have = current.get((name, rtype), [])
        if any(r.get("description") != OWNER_MARK for r in have):
            skipped.append(f"{name} {rtype}: a hand-made record has that name; left alone")
            continue
        targets = set(ep.get("targets") or [])
        for r in have:
            if r["value"] not in targets:
                deletes.append(r["id"])
        for value in sorted(targets - {r["value"] for r in have}):
            creates.append({"zone": zone["id"], "name": relative(name, zone["name"]), "type": rtype, "value": value,
                            "ttl": ep.get("recordTTL") or None, "status": "active", "description": OWNER_MARK})
    for ep in changes.get("delete") or []:
        name, rtype = ep["dnsName"].rstrip(".").lower(), ep.get("recordType", "A")
        deletes += [r["id"] for r in current.get((name, rtype), []) if r.get("description") == OWNER_MARK]
    # Sorted: ExternalDNS sends the same changes in a different order each loop.
    return creates, sorted(set(deletes)), sorted(skipped)


class Handler(BaseHTTPRequestHandler):
    netbox: NetBox
    domains: list[str] = []
    last_skipped: list[str] = []  # logged in full only when it changes

    def log_message(self, *args):
        pass

    def answer(self, code: int, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        if body is not None:
            self.send_header("Content-Type", MEDIA)
            self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"null")

    def do_GET(self):
        if self.path == "/":
            self.answer(200, {"include": self.domains})
        elif self.path == "/records":
            try:
                zones = self.netbox.all("zones/")
                records = self.netbox.all("records/")
            except NetBoxError as e:
                log(f"listing records failed: {e}")
                self.answer(500, {"error": str(e)})
                return
            self.answer(200, endpoints(records, {z["id"]: z for z in zones}))
        else:
            self.answer(404)

    def do_POST(self):
        if self.path == "/adjustendpoints":
            self.answer(200, self.body() or [])
            return
        if self.path != "/records":
            self.answer(404)
            return
        try:
            changes = self.body() or {}
            zones = self.netbox.all("zones/")
            records = self.netbox.all("records/")
            creates, deletes, skipped = plan(changes, records, zones)
            if skipped != Handler.last_skipped:
                sizes = {k: len(v or []) for k, v in changes.items() if isinstance(v, list)}
                log(f"changes asked: {sizes or 'none'}")
                for msg in skipped:
                    log(f"skipped {msg}")
                Handler.last_skipped = skipped
            for rid in deletes:
                self.netbox.call("DELETE", f"records/{rid}/")
            for rec in creates:
                self.netbox.call("POST", "records/", rec)
            if creates or deletes:
                log(f"applied: {len(creates)} created, {len(deletes)} deleted")
        except (NetBoxError, ValueError, KeyError) as e:
            log(f"applying changes failed: {e}")
            self.answer(500, {"error": str(e)})
            return
        self.answer(204)


class Health(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200 if self.path == "/healthz" else 404)
        self.end_headers()


def main() -> None:
    url, token = os.environ.get("NETBOX_URL", "").strip(), os.environ.get("NETBOX_TOKEN", "")
    domains = [d.strip() for d in os.environ.get("DOMAIN_FILTER", "").split(",") if d.strip()]
    if not url.startswith("https://") or not token.strip() or not domains:
        sys.exit("NETBOX_URL (https), NETBOX_TOKEN and DOMAIN_FILTER are required")
    Handler.netbox, Handler.domains = NetBox(url, token), domains
    Thread(target=ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT_HEALTH", "8080"))), Health).serve_forever,
           daemon=True).start()
    log(f"listening on 127.0.0.1:8888 for {', '.join(domains)}")
    ThreadingHTTPServer(("127.0.0.1", 8888), Handler).serve_forever()


if __name__ == "__main__":
    main()
