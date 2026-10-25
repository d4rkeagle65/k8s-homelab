"""termix-netbox-sync.py: which NetBox hosts become Termix hosts, with which login, address,
port and folder. Documentation addresses only (RFC 5737)."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[2] / "kubernetes" / "prod" / "services" / "termix" / "termix"
          / "app" / "sync" / "termix-netbox-sync.py")


def load():
    os.environ.setdefault("NETBOX_URL", "https://netbox.invalid")
    spec = importlib.util.spec_from_file_location("termix_sync_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load()

PREFIXES = [{"prefix": "192.0.2.0/24", "description": "Management"}, {"prefix": "198.51.100.0/24", "description": "Production"}]


def obj(id, name, tags, role, kind="vm", primary=None):
    return {"id": id, "name": name, "_kind": kind, "tags": [{"slug": t} for t in tags], "role": {"name": role},
            "primary_ip4": primary}


def addr(owner_id, address, dns, kind="vm"):
    key = "virtual_machine" if kind == "vm" else "device"
    return {"address": address, "dns_name": dns,
            "assigned_object_type": "virtualization.vminterface" if kind == "vm" else "dcim.interface",
            "assigned_object": {key: {"id": owner_id}}}


def test_each_platform_gets_its_login_and_the_management_name(monkeypatch):
    monkeypatch.setenv("TERMIX_SSH_ADMIN_USER", "admin1")
    monkeypatch.delenv("TERMIX_SSH_SYNOLOGY_USER", raising=False)
    objects = [obj(1, "pihole", ["ansible", "debian", "pihole"], "Pi-hole"),
               obj(2, "ap", ["openwrt_aps"], "Access point", kind="device"),
               obj(3, "nas1", ["nas", "synology"], "Storage", kind="device"),
               obj(4, "switch", [], "Switch", kind="device")]
    addresses = [addr(1, "198.51.100.3/24", "pihole.prod.example"), addr(1, "192.0.2.3/24", "pihole.mgmt.example"),
                 addr(2, "192.0.2.6/24", "ap.mgmt.example", kind="device")]
    hosts, skipped = m.wanted_hosts(objects, addresses, PREFIXES, [])
    assert hosts["pihole"]["ip"] == "pihole.mgmt.example" and hosts["pihole"]["username"] == "admin1"
    assert hosts["pihole"]["folder"] == "Pi-hole" and hosts["pihole"]["port"] == 22
    assert hosts["ap"]["username"] == "root"
    assert "switch" not in hosts and "nas1" not in hosts  # no login tag; username not set
    assert any(s.startswith("nas1: username not set") for s in skipped)


def test_the_terramaster_isnt_taken_for_the_synology(monkeypatch):
    monkeypatch.setenv("TERMIX_SSH_SYNOLOGY_USER", "syn")
    monkeypatch.setenv("TERMIX_SSH_TERRAMASTER_USER", "tnas")
    assert m.username_for({"nas", "synology"})[0] == "syn"
    assert m.username_for({"nas"})[0] == "tnas"


def test_a_host_off_the_management_network_uses_its_primary_address(monkeypatch):
    monkeypatch.setenv("TERMIX_SSH_ADMIN_USER", "admin1")
    router = obj(5, "travel", ["openwrt_aps"], "Router", kind="device",
                 primary={"address": "203.0.113.1/24", "dns_name": ""})
    hosts, _ = m.wanted_hosts([router], [], PREFIXES, [])
    assert hosts["travel"]["ip"] == "203.0.113.1" and hosts["travel"]["via"] == "primary address"


WANT = {"name": "pihole", "ip": "pihole.mgmt.example", "port": 22, "username": "admin1", "folder": "Pi-hole",
        "login": "debian", "via": "management network"}


def test_an_update_keeps_what_the_user_set_and_overlays_the_synced_fields():
    have = {"id": 7, "name": "pihole", "ip": "old.example", "port": 2222, "username": "x", "folder": "Old",
            "pin": True, "notes": "mine", "statusCheckEnabled": False, "tags": "netbox,lab"}
    body = m.host_body(WANT, 3, have)
    assert body["pin"] is True and body["notes"] == "mine" and body["statusCheckEnabled"] is False
    assert (body["ip"], body["port"], body["username"], body["folder"]) == ("pihole.mgmt.example", 22, "admin1", "Pi-hole")
    assert body["authType"] == "credential" and body["credentialId"] == 3 and body["overrideCredentialUsername"] is True
    assert set(body["tags"]) == {"netbox", "lab"}
    assert m.differs(have, WANT, 3) == ["ip", "port", "username", "folder", "credential", "username override"]
    assert m.differs(dict(body), WANT, 3) == []


class FakeTermix:
    def __init__(self, hosts, creds):
        self.hosts, self.creds, self.calls = hosts, creds, []

    def get(self, url, headers):
        if url.endswith("/credentials"):
            return self.creds
        if "netbox" in url:
            raise AssertionError("NetBox is stubbed through wanted_hosts")
        return self.hosts

    def send(self, method, url, headers, body=None):
        self.calls.append((method, url))
        if method == "POST":
            self.hosts.append(dict(body, id=100 + len(self.calls)))
        elif method == "DELETE":
            self.hosts[:] = [h for h in self.hosts if not url.endswith(f"/{h['id']}")]


def run(monkeypatch, fake, wanted, dry_run):
    monkeypatch.setenv("TERMIX_API_KEY", "k")
    monkeypatch.setenv("NETBOX_TOKEN", "t")
    monkeypatch.setattr(m, "DRY_RUN", dry_run)
    monkeypatch.setattr(m, "netbox_all", lambda path: [])
    monkeypatch.setattr(m, "wanted_hosts", lambda *a: (wanted, []))
    monkeypatch.setattr(m, "get", fake.get)
    monkeypatch.setattr(m, "send", fake.send)
    try:
        return m.main()
    except SystemExit as e:
        return e.code


def test_a_dry_run_writes_nothing_even_without_the_credential(monkeypatch, capsys):
    fake = FakeTermix([], [])
    assert run(monkeypatch, fake, {"pihole": WANT}, dry_run=True) == 0
    assert fake.calls == [] and "would create pihole" in capsys.readouterr().out


def test_the_writes_refuse_without_the_credential_or_with_netbox_empty(monkeypatch):
    assert run(monkeypatch, FakeTermix([], []), {"pihole": WANT}, dry_run=False) == 2
    owned = [{"id": 1, "name": "gone", "tags": "netbox"}]
    fake = FakeTermix(owned, [{"id": 3, "name": "bastion"}])
    assert run(monkeypatch, fake, {}, dry_run=False) == 2 and fake.calls == []


def test_writes_create_and_delete_only_synced_hosts_then_read_back(monkeypatch, capsys):
    hosts = [{"id": 1, "name": "Docker Vm 1", "tags": ""}, {"id": 2, "name": "gone", "tags": "netbox"}]
    fake = FakeTermix(hosts, [{"id": 3, "name": "bastion"}])
    assert run(monkeypatch, fake, {"pihole": WANT}, dry_run=False) == 0
    assert sorted(fake.calls) == [("DELETE", "http://termix:8080/host/db/host/2"), ("POST", "http://termix:8080/host/db/host")]
    assert [h["name"] for h in fake.hosts] == ["Docker Vm 1", "pihole"]
    assert "VERIFIED" in capsys.readouterr().out


def test_a_write_that_did_not_take_fails_the_run(monkeypatch, capsys):
    fake = FakeTermix([], [{"id": 3, "name": "bastion"}])
    fake.send = lambda *a, **k: None  # reports success, stores nothing
    assert run(monkeypatch, fake, {"pihole": WANT}, dry_run=False) == 1
    assert "NOT AS WANTED after the writes: pihole" in capsys.readouterr().out


def test_a_hand_made_host_with_a_synced_name_is_left_alone(monkeypatch, capsys):
    fake = FakeTermix([{"id": 1, "name": "pihole", "tags": ""}], [{"id": 3, "name": "bastion"}])
    assert run(monkeypatch, fake, {"pihole": WANT}, dry_run=False) == 0
    assert fake.calls == [] and "skipped pihole: a host made by hand" in capsys.readouterr().out


def test_a_device_given_the_key_by_hand_logs_in_as_its_local_context_says(monkeypatch):
    switch = obj(6, "switch", ["bastion-key"], "Switch", kind="device")
    switch["local_context_data"] = {"termix_user": "netadmin"}
    firewall = obj(7, "firewall", ["bastion-key"], "Firewall", kind="device")
    addresses = [addr(6, "192.0.2.10/24", "switch.mgmt.example", kind="device"),
                 addr(7, "192.0.2.11/24", "firewall.mgmt.example", kind="device")]
    hosts, skipped = m.wanted_hosts([switch, firewall], addresses, PREFIXES, [])
    assert hosts["switch"]["username"] == "netadmin" and hosts["switch"]["folder"] == "Switch"
    assert "firewall" not in hosts
    assert skipped == ["firewall: username not set (bastion-key (local context termix_user not set))"]


def test_a_host_takes_the_credential_its_local_context_names_else_the_default():
    switch = obj(6, "switch", ["bastion-key"], "Switch", kind="device")
    switch["local_context_data"] = {"termix_user": "netadmin", "termix_credential": "bastion-rsa"}
    firewall = obj(7, "firewall", ["bastion-key"], "Firewall", kind="device")
    firewall["local_context_data"] = {"termix_user": "admin"}
    addresses = [addr(6, "192.0.2.10/24", "switch.mgmt.example", kind="device"),
                 addr(7, "192.0.2.11/24", "firewall.mgmt.example", kind="device")]
    hosts, _ = m.wanted_hosts([switch, firewall], addresses, PREFIXES, [])
    assert hosts["switch"]["credential"] == "bastion-rsa"
    assert hosts["firewall"]["credential"] == m.CREDENTIAL


CREDS = [{"id": 3, "name": "bastion"}, {"id": 4, "name": "bastion-rsa"}]


def test_each_host_is_made_with_its_own_credential(monkeypatch):
    rsa = dict(WANT, name="switch", credential="bastion-rsa")
    fake = FakeTermix([], list(CREDS))
    assert run(monkeypatch, fake, {"pihole": WANT, "switch": rsa}, dry_run=False) == 0
    by_name = {h["name"]: h["credentialId"] for h in fake.hosts}
    assert by_name == {"pihole": 3, "switch": 4}


def test_a_named_credential_termix_lacks_holds_that_host_and_never_deletes_it(monkeypatch, capsys):
    synced_switch = {"id": 9, "name": "switch", "tags": "netbox", "authType": "credential", "credentialId": 3}
    rsa = dict(WANT, name="switch", credential="bastion-rsa")
    fake = FakeTermix([synced_switch], [{"id": 3, "name": "bastion"}])
    assert run(monkeypatch, fake, {"pihole": WANT, "switch": rsa}, dry_run=False) == 0
    assert fake.calls == [("POST", "http://termix:8080/host/db/host")]  # pihole made; switch neither changed nor deleted
    out = capsys.readouterr().out
    assert "credential(s) named 'bastion-rsa'" in out and "VERIFIED" in out
