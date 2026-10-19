"""docker/netbox/dns-sync/netbox_pihole_sync.py: what it would change on a Pi-hole,
what it refuses to act on, and a full round against a fake NetBox and Pi-hole.
Documentation addresses and example names only (RFC 5737, RFC 2606)."""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docker" / "netbox" / "dns-sync" / "netbox_pihole_sync.py"


def load():
    spec = importlib.util.spec_from_file_location("netbox_pihole_sync_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load()

ZONES = ["mgmt.example", "example.com"]
WANTED = {("host1.mgmt.example", "A", "192.0.2.1"), ("app.example.com", "A", "198.51.100.10"),
          ("www.example.com", "CNAME", "app.example.com")}


def test_adds_what_netbox_has_keeps_what_matches_and_ignores_names_outside_its_zones():
    hosts = ["192.0.2.1 host1.mgmt.example", "203.0.113.9 printer.other.test"]
    rm_h, add_h, rm_c, add_c, left = m.plan(ZONES, WANTED, hosts, [], delete=True)
    assert rm_h == [] and rm_c == [] and left == []
    assert add_h == ["198.51.100.10 app.example.com"] and add_c == ["www.example.com,app.example.com"]


def test_a_record_netbox_lacks_is_kept_and_listed_until_deletes_are_on():
    hosts = ["192.0.2.1 host1.mgmt.example", "192.0.2.99 old.mgmt.example"]
    cnames = ["gone.example.com,app.example.com"]
    rm_h, _, rm_c, _, left = m.plan(ZONES, WANTED, hosts, cnames, delete=False)
    assert rm_h == [] and rm_c == [] and left == ["192.0.2.99 old.mgmt.example", "gone.example.com,app.example.com"]
    rm_h, _, rm_c, _, left = m.plan(ZONES, WANTED, hosts, cnames, delete=True)
    assert rm_h == ["192.0.2.99 old.mgmt.example"] and rm_c == ["gone.example.com,app.example.com"] and left == []


def test_a_line_with_two_names_becomes_one_line_each_keeping_a_foreign_name_as_spelled():
    hosts = ["192.0.2.1 host1.mgmt.example Alias.Other.Test"]
    rm_h, add_h, *_ = m.plan(ZONES, WANTED, hosts, [], delete=True)
    assert rm_h == hosts
    assert add_h == ["192.0.2.1 Alias.Other.Test", "192.0.2.1 host1.mgmt.example", "198.51.100.10 app.example.com"]


def test_a_split_lines_name_netbox_lacks_is_dropped_only_with_deletes_on():
    hosts = ["192.0.2.1 host1.mgmt.example stale.mgmt.example"]
    _, add_h, _, _, left = m.plan(ZONES, WANTED, hosts, [], delete=False)
    assert "192.0.2.1 stale.mgmt.example" in add_h and left == ["192.0.2.1 stale.mgmt.example"]
    _, add_h, _, _, left = m.plan(ZONES, WANTED, hosts, [], delete=True)
    assert "192.0.2.1 stale.mgmt.example" not in add_h and left == []


def test_zone_membership_is_by_label_never_by_substring():
    assert m.in_zone("a.example.com", ZONES) and m.in_zone("example.com", ZONES)
    assert not m.in_zone("notexample.com", ZONES) and not m.in_zone("example.com.evil.test", ZONES)


# --- a fake NetBox and Pi-hole behind m.call ---------------------------------

class Fake:
    def __init__(self, zones, records, hosts, cnames=(), password="pw"):
        self.zones, self.records = zones, records
        self.hosts, self.cnames, self.password = list(hosts), list(cnames), password
        self.calls, self.sessions = [], 0

    def __call__(self, method, url, headers, body=None, verify=True):
        path = urllib.parse.urlsplit(url).path
        self.calls.append((method, path))
        if "/api/plugins/netbox-dns/" in path:
            kind = path.rstrip("/").rsplit("/", 1)[-1]
            data = self.zones if kind == "zones" else self.records
            return (200, {"results": data, "next": None}) if data is not None else (503, None)
        if path == "/api/auth" and method == "POST":
            if body.get("password") != self.password:
                return 401, {"session": {"valid": False}}
            self.sessions += 1
            return 200, {"session": {"valid": True, "sid": "s1"}}
        if path == "/api/auth" and method == "DELETE":
            self.sessions -= 1
            return 204, None
        if path == "/api/config/dns":
            return 200, {"config": {"dns": {"hosts": list(self.hosts), "cnameRecords": list(self.cnames)}}}
        kind, value = path.split("/api/config/dns/", 1)[1].split("/", 1)
        value = urllib.parse.unquote(value)
        target = self.hosts if kind == "hosts" else self.cnames
        if method == "PUT":
            target.append(value)
            return 201, None
        if method == "DELETE" and value in target:
            target.remove(value)
            return 204, None
        return 404, {"error": {"message": "not found"}}


def zone(id, name, status="active"):
    return {"id": id, "name": name, "status": {"value": status}}


def rec(zone_id, name, type, value, status="active"):
    return {"zone": {"id": zone_id}, "name": name, "type": type, "value": value, "status": {"value": status}}


ENV = {"NETBOX_API": "https://netbox.example", "NETBOX_TOKEN": "t",
       "PIHOLE_1_URL": "https://pihole1.example", "PIHOLE_1_PASSWORD": "pw"}


def test_netbox_records_become_fully_qualified_names_skipping_inactive_ones(monkeypatch):
    fake = Fake([zone(1, "mgmt.example"), zone(2, "off.example", "deprecated")],
                [rec(1, "host1", "A", "192.0.2.1"), rec(1, "@", "A", "192.0.2.2"), rec(1, "www", "CNAME", "host1"),
                 rec(1, "old", "A", "192.0.2.3", "inactive"), rec(2, "x", "A", "192.0.2.4"), rec(1, "@", "SOA", "ns")],
                [])
    monkeypatch.setattr(m, "call", fake)
    zones, wanted = m.netbox_wanted("https://netbox.example", "Bearer t")
    assert zones == ["mgmt.example"]
    assert wanted == {("host1.mgmt.example", "A", "192.0.2.1"), ("mgmt.example", "A", "192.0.2.2"),
                      ("www.mgmt.example", "CNAME", "host1.mgmt.example")}


@pytest.mark.parametrize("zones,records,why", [(None, [], "didn't answer"), ([], [], "no active zone"),
                                               ([zone(1, "mgmt.example")], [], "no A, AAAA or CNAME")])
def test_a_failed_or_empty_netbox_read_writes_nothing(monkeypatch, capsys, zones, records, why):
    fake = Fake(zones, records, ["192.0.2.9 old.mgmt.example"])
    monkeypatch.setattr(m, "call", fake)
    monkeypatch.setattr(sys, "argv", ["sync", "--apply"])
    monkeypatch.setattr(m.os, "environ", {**ENV, "SYNC_DELETE": "true"})
    with pytest.raises(SystemExit) as e:
        m.main()
    assert e.value.code == 1 and why in capsys.readouterr().out
    assert fake.hosts == ["192.0.2.9 old.mgmt.example"]
    assert not any(c[0] in ("PUT", "DELETE") and "/config/" in c[1] for c in fake.calls)


def test_a_round_applies_reads_back_and_logs_out(monkeypatch, capsys):
    fake = Fake([zone(1, "mgmt.example")], [rec(1, "host1", "A", "192.0.2.1"), rec(1, "host2", "A", "192.0.2.2")],
                ["192.0.2.1 host1.mgmt.example proxmox-hosts.mgmt.example", "192.0.2.9 old.mgmt.example",
                 "203.0.113.5 keep.other.test"])
    monkeypatch.setattr(m, "call", fake)
    monkeypatch.setattr(sys, "argv", ["sync", "--apply"])
    monkeypatch.setattr(m.os, "environ", dict(ENV))
    with pytest.raises(SystemExit) as e:
        m.main()
    out = capsys.readouterr().out
    assert e.value.code == 0 and "matches NetBox" in out and "SYNC_DELETE is off" in out
    assert sorted(fake.hosts) == ["192.0.2.1 host1.mgmt.example", "192.0.2.1 proxmox-hosts.mgmt.example",
                                  "192.0.2.2 host2.mgmt.example", "192.0.2.9 old.mgmt.example",
                                  "203.0.113.5 keep.other.test"]
    assert fake.sessions == 0 and "pw" not in out


def test_a_dry_run_reports_and_writes_nothing(monkeypatch, capsys):
    fake = Fake([zone(1, "mgmt.example")], [rec(1, "host2", "A", "192.0.2.2")], [])
    monkeypatch.setattr(m, "call", fake)
    monkeypatch.setattr(sys, "argv", ["sync"])
    monkeypatch.setattr(m.os, "environ", dict(ENV))
    with pytest.raises(SystemExit) as e:
        m.main()
    assert e.value.code == 0 and "add    192.0.2.2 host2.mgmt.example" in capsys.readouterr().out
    assert fake.hosts == []


def test_sync_apply_in_the_environment_writes_as_apply_does(monkeypatch):
    fake = Fake([zone(1, "mgmt.example")], [rec(1, "host2", "A", "192.0.2.2")], [])
    monkeypatch.setattr(m, "call", fake)
    monkeypatch.setattr(sys, "argv", ["sync"])
    monkeypatch.setattr(m.os, "environ", {**ENV, "SYNC_APPLY": "true"})
    with pytest.raises(SystemExit) as e:
        m.main()
    assert e.value.code == 0 and fake.hosts == ["192.0.2.2 host2.mgmt.example"]


def test_a_refused_login_fails_the_round_without_writing(monkeypatch, capsys):
    fake = Fake([zone(1, "mgmt.example")], [rec(1, "host2", "A", "192.0.2.2")], [], password="other")
    monkeypatch.setattr(m, "call", fake)
    monkeypatch.setattr(sys, "argv", ["sync", "--apply"])
    monkeypatch.setattr(m.os, "environ", dict(ENV))
    with pytest.raises(SystemExit) as e:
        m.main()
    assert e.value.code == 1 and "login refused" in capsys.readouterr().out and fake.hosts == []


def test_piholes_are_numbered_without_a_gap_and_need_a_password():
    assert m.piholes_from({**ENV, "PIHOLE_3_URL": "https://skipped.example"}) == [("https://pihole1.example", "pw")]
    with pytest.raises(SystemExit, match="without PIHOLE_1_PASSWORD"):
        m.piholes_from({"PIHOLE_1_URL": "https://pihole1.example"})


def test_unexpected_arguments_are_named_never_shown():
    msg = m.unexpected(["--password=hunter2-long", "value"])
    assert "--password" in msg and "hunter2" not in msg
