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
