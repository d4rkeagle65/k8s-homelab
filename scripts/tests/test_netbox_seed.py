"""netbox_seed.py's pure helpers: how it reads Proxmox guests' disks and
addresses, places a bridge's network, and compares NetBox's values with the
ones it would write. Documentation addresses only (RFC 5737)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "netbox_seed.py"


def load():
    spec = importlib.util.spec_from_file_location("netbox_seed_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load()


def test_disk_sums_disks_and_leaves_out_cdroms_and_state():
    cfg = {"scsi0": "local-lvm:vm-1-disk-0,size=32G", "scsi1": "nas:vm-1-disk-1,size=512M",
           "ide2": "none,media=cdrom", "efidisk0": "local:vm-1-disk-2,size=4M", "rootfs": "x:sub,size=8G",
           "mp0": "data:sub,size=1T"}
    # 32 + 0.5 + 8 + 1024: the CD drive and the EFI disk are left out; 1064.5 rounds to even.
    assert m.disk_gb(cfg) == 1064


def test_static_ips_reads_containers_and_skips_dhcp():
    cfg = {"net0": "name=eth0,bridge=vmbr7,ip=192.0.2.10/24,gw=192.0.2.1", "net1": "name=eth1,bridge=vmbr0,ip=dhcp",
           "memory": "512"}
    assert m.static_ips(cfg) == {"net0": ["192.0.2.10/24"]}


def test_network_prefers_kea_subnet_then_a_hosts_own_prefix():
    kea = ["192.0.2.0/24", "198.51.100.0/24"]
    assert m.network_for(["198.51.100.7/24"], kea) == "198.51.100.0/24"
    assert m.network_for(["203.0.113.5/25"], kea) == "203.0.113.0/25"
    assert m.network_for([], kea) is None


def test_same_compares_nested_choices_and_tags():
    assert m.same({"id": 4, "name": "x"}, 4)
    assert not m.same({"id": 4}, 5)
    assert m.same({"value": "active", "label": "Active"}, "active")
    assert m.same([{"slug": "b"}, {"slug": "a"}], [{"slug": "a"}, {"slug": "b"}])
    assert not m.same([{"slug": "a"}], [{"slug": "a"}, {"slug": "b"}])
    assert m.same(1024.0, 1024)


def test_slug():
    assert m.slug("OpenWrt access point") == "openwrt-access-point"
    assert m.slug("Work laptop") == "work-laptop"
