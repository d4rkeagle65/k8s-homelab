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


def test_disks_by_key_in_netbox_mb_leaving_out_cdroms_and_state():
    cfg = {"scsi0": "local-lvm:vm-1-disk-0,size=32G", "scsi1": "nas:vm-1-disk-1,size=512M",
           "ide2": "none,media=cdrom", "efidisk0": "local:vm-1-disk-2,size=4M", "rootfs": "x:sub,size=8G",
           "mp0": "data:sub,size=1T"}
    # A G is NetBox's 1000 MB, so 32 GB entered in NetBox as 32000 reads back whole.
    assert m.disk_sizes(cfg) == {"scsi0": 32000, "scsi1": 500, "rootfs": 8000, "mp0": 1024000}


def test_static_ips_reads_containers_and_skips_dhcp():
    cfg = {"net0": "name=eth0,bridge=vmbr7,ip=192.0.2.10/24,gw=192.0.2.1", "net1": "name=eth1,bridge=vmbr0,ip=dhcp",
           "memory": "512"}
    assert m.static_ips(cfg) == {"net0": ["192.0.2.10/24"]}


def test_static_ips_reads_a_vms_cloud_init_addresses_by_its_nic():
    cfg = {"net0": "virtio=BC:24:11:00:00:01,bridge=vmbr7", "ipconfig0": "gw=192.0.2.254,ip=192.0.2.24/24",
           "ipconfig1": "ip=198.51.100.24/24", "ipconfig2": "ip=dhcp"}
    assert m.static_ips(cfg) == {"net0": ["192.0.2.24/24"], "net1": ["198.51.100.24/24"]}


def test_mac_of_reads_vm_and_container_nics():
    assert m.mac_of("virtio=bc:24:11:0a:0b:0c,bridge=vmbr7,firewall=1") == "BC:24:11:0A:0B:0C"
    assert m.mac_of("name=eth0,bridge=vmbr0,hwaddr=BC:24:11:0A:0B:0D,ip=dhcp") == "BC:24:11:0A:0B:0D"
    assert m.mac_of("name=eth0,bridge=vmbr0") is None


def test_agent_on_reads_both_spellings():
    assert m.agent_on("1,fstrim_cloned_disks=1")
    assert m.agent_on("enabled=1,type=virtio")
    assert not m.agent_on("0")
    assert not m.agent_on("enabled=0")
    assert not m.agent_on(None)


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
    # Custom fields: NetBox returns every one; only those written are compared.
    assert m.same({"vmid": 110, "talos_apply_mode": "no_reboot", "proxmox_tags": None}, {"vmid": 110})
    assert not m.same({"vmid": 110, "talos_apply_mode": None}, {"vmid": 110, "talos_apply_mode": "auto"})


def test_slug():
    assert m.slug("OpenWrt access point") == "openwrt-access-point"
    assert m.slug("Work laptop") == "work-laptop"


def test_read_secret_prefers_the_environment_then_the_home_file(tmp_path, monkeypatch):
    monkeypatch.setattr(m.Path, "home", lambda: tmp_path)
    (tmp_path / ".kea-api").write_text("from-file\n", encoding="utf-8")
    monkeypatch.setenv("KEA_API", " from-env ")
    assert m.read_secret(".kea-api", "KEA_API") == "from-env"
    monkeypatch.delenv("KEA_API")
    assert m.read_secret(".kea-api", "KEA_API") == "from-file"
    try:
        m.read_secret(".absent", "ABSENT_VAR")
    except SystemExit as e:
        assert "ABSENT_VAR" in str(e)
    else:
        raise AssertionError("a missing credential must stop the run")


def test_unexpected_arguments_are_named_never_shown():
    value = "not-a-real-value"
    msg = m.unexpected(["NETBOX_WRITE_TOKEN=" + value, "PROXMOX_TOKEN=prefix=" + value, value])
    assert "NETBOX_WRITE_TOKEN" in msg and "PROXMOX_TOKEN" in msg
    assert value not in msg and "prefix" not in msg


def test_dns_label_keeps_letters_digits_and_hyphens():
    assert m.dns_label("HostA01") == "hosta01"
    assert m.dns_label("Core switch (Model X-1)") == "core-switch-model-x-1"


def _addr(id, address, kind="virtualization.vminterface", name="guest1", dns=""):
    owner = {"virtual_machine" if kind == "virtualization.vminterface" else "device": {"name": name}} if kind else {}
    return {"id": id, "address": address, "assigned_object_type": kind, "assigned_object": owner, "dns_name": dns}


PREFIXES = [{"prefix": "192.0.2.0/24", "custom_fields": {"dns_zone": {"id": 1, "name": "mgmt.example"}}},
            {"prefix": "192.0.2.128/25", "custom_fields": {"dns_zone": {"id": 2, "name": "inner.example"}}},
            {"prefix": "198.51.100.0/24", "custom_fields": {"dns_zone": None}}]


def test_every_device_and_vm_address_is_named_in_its_most_specific_prefixes_zone():
    got = m.wanted_dns_names([_addr(1, "192.0.2.10/24"), _addr(2, "192.0.2.200/24", name="guest1"),
                              _addr(3, "192.0.2.11/24", kind="dcim.interface", name="Switch 02")], PREFIXES)
    assert got == {1: "guest1.mgmt.example", 2: "guest1.inner.example", 3: "switch-02.mgmt.example"}


def test_an_address_without_a_zone_or_an_owner_is_left_alone():
    got = m.wanted_dns_names([_addr(1, "198.51.100.5/24"), _addr(2, "203.0.113.5/24"),
                              _addr(3, "192.0.2.12/24", kind=None), _addr(4, "192.0.2.13/24", kind="ipam.fhrpgroup")],
                             PREFIXES)
    assert got == {}


def test_a_decommissioning_vms_addresses_get_no_name():
    a = _addr(1, "192.0.2.10/24")
    a["assigned_object"]["virtual_machine"]["id"] = 7
    assert m.wanted_dns_names([a], PREFIXES, frozenset({7})) == {}
    assert m.wanted_dns_names([a], PREFIXES) == {1: "guest1.mgmt.example"}


def iface(id, name, parent=None, bridge=None, ips=0, cable=None):
    ref = lambda r: {"id": r} if r else None
    return {"id": id, "name": name, "parent": ref(parent), "bridge": ref(bridge), "lag": None,
            "count_ipaddresses": ips, "cable": cable}


def test_stale_interfaces_deletes_a_retired_vlan_port_before_its_bridge():
    have = [iface(1, "eth5"), iface(2, "vmbr4"), iface(3, "eth5.4", parent=1, bridge=2), iface(4, "vmbr0", ips=1)]
    delete, kept = m.stale_interfaces(have, {"eth5", "vmbr0"})
    assert [i["name"] for i in delete] == ["eth5.4", "vmbr4"]
    assert kept == []


def test_stale_interfaces_keeps_one_with_an_address_or_cable_and_what_it_holds():
    have = [iface(1, "eth5"), iface(2, "vmbr4", ips=1), iface(3, "eth5.4", parent=1, bridge=2),
            iface(5, "eth9", cable={"id": 7})]
    delete, kept = m.stale_interfaces(have, {"eth5"})
    assert [i["name"] for i in delete] == ["eth5.4"]
    assert [(i["name"], why) for i, why in kept] == [("eth9", "it has a cable"), ("vmbr4", "it has an address")]


def test_stale_interfaces_keeps_a_bridge_a_present_port_still_names():
    # Proxmox still has eth5.4 under vmbr4 in NetBox's view: vmbr4 can't go before it.
    have = [iface(2, "vmbr4"), iface(3, "eth5.4", bridge=2)]
    delete, kept = m.stale_interfaces(have, {"eth5.4"})
    assert delete == []
    assert [(i["name"], why) for i, why in kept] == [("vmbr4", "eth5.4 still uses it")]


def test_stale_interfaces_leaves_what_proxmox_has_even_when_down():
    have = [iface(1, "eth3")]
    assert m.stale_interfaces(have, {"eth3"}) == ([], [])
