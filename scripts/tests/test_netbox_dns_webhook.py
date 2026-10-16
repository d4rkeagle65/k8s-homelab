"""netbox-dns-webhook.py, ExternalDNS's webhook provider for NetBox DNS: which
records it owns, where a hostname goes, what it leaves alone, and that a failed
read of NetBox is never an empty list. Documentation names and addresses only
(example.net, RFC 5737)."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[2] / "kubernetes" / "prod" / "system" / "external-dns"
          / "external-dns" / "app" / "netbox-dns-webhook.py")


def load():
    spec = importlib.util.spec_from_file_location("netbox_dns_webhook_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load()

ZONES = [{"id": 1, "name": "example.net"}, {"id": 2, "name": "lab.example.net"}]


def rec(rid, name, zone, value, rtype="A", owner=True):
    zname = {1: "example.net", 2: "lab.example.net"}[zone]
    return {"id": rid, "name": name, "zone": {"id": zone, "name": zname}, "type": rtype, "value": value, "ttl": None,
            "description": m.OWNER_MARK if owner else "", "fqdn": (zname if name == "@" else f"{name}.{zname}") + "."}


def ep(name, *targets, rtype="A"):
    return {"dnsName": name, "recordType": rtype, "targets": list(targets)}


def test_the_longest_zone_holds_a_name():
    assert m.zone_for("app.lab.example.net", ZONES)["id"] == 2
    assert m.zone_for("app.example.net", ZONES)["id"] == 1
    assert m.zone_for("example.net", ZONES)["id"] == 1
    assert m.zone_for("app.example.org", ZONES) is None
    assert m.relative("app.lab.example.net", "lab.example.net") == "app"
    assert m.relative("example.net", "example.net") == "@"


def test_only_owned_records_are_listed_grouped_by_name_and_type():
    records = [rec(1, "app", 1, "192.0.2.10"), rec(2, "app", 1, "192.0.2.11"),
               rec(3, "nas", 1, "192.0.2.20", owner=False), rec(4, "@", 2, "192.0.2.30")]
    out = m.endpoints(records, {z["id"]: z for z in ZONES})
    assert [(e["dnsName"], e["targets"]) for e in out] == [
        ("app.example.net", ["192.0.2.10", "192.0.2.11"]), ("lab.example.net", ["192.0.2.30"])]


def test_a_create_goes_in_its_zone_marked_as_owned():
    creates, deletes, skipped = m.plan({"Create": [ep("app.lab.example.net", "192.0.2.10")]}, [], ZONES)
    assert creates == [{"zone": 2, "name": "app", "type": "A", "value": "192.0.2.10", "ttl": None,
                        "status": "active", "description": m.OWNER_MARK}]
    assert deletes == [] and skipped == []


def test_an_update_replaces_only_the_targets_that_changed():
    records = [rec(1, "app", 1, "192.0.2.10"), rec(2, "app", 1, "192.0.2.11")]
    creates, deletes, _ = m.plan({"UpdateNew": [ep("app.example.net", "192.0.2.11", "192.0.2.12")]}, records, ZONES)
    assert [c["value"] for c in creates] == ["192.0.2.12"] and deletes == [1]


def test_a_hand_made_record_and_a_name_without_a_zone_are_left_alone():
    records = [rec(5, "nas", 1, "192.0.2.20", owner=False)]
    changes = {"Create": [ep("nas.example.net", "192.0.2.99"), ep("app.example.org", "192.0.2.1")]}
    creates, deletes, skipped = m.plan(changes, records, ZONES)
    assert creates == [] and deletes == []
    assert any("hand-made" in s for s in skipped) and any("no NetBox DNS zone" in s for s in skipped)


def test_a_delete_removes_only_owned_records():
    records = [rec(1, "app", 1, "192.0.2.10"), rec(2, "nas", 1, "192.0.2.20", owner=False)]
    _, deletes, _ = m.plan({"Delete": [ep("app.example.net", "192.0.2.10"), ep("nas.example.net", "192.0.2.20")]},
                           records, ZONES)
    assert deletes == [1]


class FakeNetBox:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def all(self, path):
        if self.fail:
            raise m.NetBoxError("GET zones/: HTTP 503")
        return ZONES if path == "zones/" else [rec(1, "app", 1, "192.0.2.10")]

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), m.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def request(url, method="GET", body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Accept": m.MEDIA, "Content-Type": m.MEDIA})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read()
            return r.status, r.headers.get("Content-Type"), json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), json.loads(e.read() or b"null")


def test_the_api_negotiates_lists_and_applies(server):
    nb = FakeNetBox()
    m.Handler.netbox, m.Handler.domains = nb, ["example.net"]
    assert request(server + "/") == (200, m.MEDIA, {"include": ["example.net"]})
    status, _, body = request(server + "/records")
    assert status == 200 and body[0]["dnsName"] == "app.example.net"
    status, _, _ = request(server + "/records", "POST", {"Create": [ep("new.example.net", "192.0.2.50")]})
    assert status == 204 and nb.calls == [("POST", "records/", {"zone": 1, "name": "new", "type": "A",
                                                                  "value": "192.0.2.50", "ttl": None,
                                                                  "status": "active", "description": m.OWNER_MARK})]
    assert request(server + "/adjustendpoints", "POST", [ep("x.example.net", "192.0.2.1")])[2][0]["dnsName"] == "x.example.net"


def test_a_failed_read_of_netbox_is_an_error_never_an_empty_list(server):
    m.Handler.netbox, m.Handler.domains = FakeNetBox(fail=True), ["example.net"]
    status, _, body = request(server + "/records")
    assert status == 500 and "503" in body["error"]
    status, _, _ = request(server + "/records", "POST", {"Create": [ep("new.example.net", "192.0.2.50")]})
    assert status == 500


def test_the_token_works_with_or_without_bearer():
    assert m.NetBox("https://nb.example.net", "Bearer nbt_x").headers["Authorization"] == "Bearer nbt_x"
    assert m.NetBox("https://nb.example.net", " nbt_x\n").headers["Authorization"] == "Bearer nbt_x"


def test_the_release_carries_the_scripts_checksum_so_a_change_rolls_the_pod():
    import hashlib
    release = (SCRIPT.parent / "helmrelease-netbox.yaml").read_text(encoding="utf-8")
    want = hashlib.sha256(SCRIPT.read_bytes()).hexdigest()[:12]
    assert f'homelab.local/webhook-sha256: "{want}"' in release, (
        f"netbox-dns-webhook.py changed: set podAnnotations homelab.local/webhook-sha256 to \"{want}\" "
        "in helmrelease-netbox.yaml, or the pod keeps running the old script")


def test_change_lists_are_read_in_either_spelling():
    creates, _, _ = m.plan({"create": [ep("a.example.net", "192.0.2.1")], "updateNew": [ep("b.example.net", "192.0.2.2")]},
                           [], ZONES)
    assert sorted(c["name"] for c in creates) == ["a", "b"]
