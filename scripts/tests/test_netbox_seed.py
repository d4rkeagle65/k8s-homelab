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


def pool(name, *addresses):
    return {"metadata": {"name": name}, "spec": {"addresses": list(addresses)}}


def test_pool_ranges_reads_a_range_as_given_and_a_cidr_as_its_ends():
    pools = [pool("mgmt", "198.51.100.50/32"), pool("production", "192.0.2.50-192.0.2.70", "203.0.113.0/30")]
    assert m.pool_ranges(pools) == [("production", "192.0.2.50", "192.0.2.70"),
                                    ("mgmt", "198.51.100.50", "198.51.100.50"),
                                    ("production", "203.0.113.0", "203.0.113.3")]


def test_pool_ranges_leaves_out_ipv6():
    assert m.pool_ranges([pool("v6", "2001:db8::/126")]) == []


def svc(ns, name, kind, *ips):
    return {"metadata": {"namespace": ns, "name": name}, "spec": {"type": kind},
            "status": {"loadBalancer": {"ingress": [{"ip": ip} for ip in ips]}}}


def test_load_balancer_ips_names_every_service_on_a_shared_address():
    services = [svc("traefik", "traefik", "LoadBalancer", "192.0.2.50"),
                svc("kea", "kea-db-lan", "LoadBalancer", "192.0.2.53"),
                svc("dns", "tcp", "LoadBalancer", "192.0.2.52"), svc("dns", "udp", "LoadBalancer", "192.0.2.52"),
                svc("app", "web", "ClusterIP"), svc("app", "pending", "LoadBalancer")]
    assert m.load_balancer_ips(services) == {"192.0.2.50": ["traefik/traefik"],
                                             "192.0.2.52": ["dns/tcp", "dns/udp"],
                                             "192.0.2.53": ["kea/kea-db-lan"]}


def test_a_ca_that_isnt_base64_stops_before_any_request():
    import pytest
    with pytest.raises(SystemExit, match="K8S_CA_B64"):
        m.cluster_load_balancers("https://192.0.2.1:6443", "token", "not base64!")


def vip(i, address, description, status="active", role="vip"):
    return {"id": i, "address": address, "description": description,
            "status": {"value": status}, "role": {"value": role} if role else None}


def iprange(i, first, last, description, status="active"):
    return {"id": i, "start_address": first, "end_address": last, "description": description,
            "status": {"value": status}}


def test_a_removed_services_address_is_stale_and_a_live_one_isnt():
    addresses = [vip(1, "192.0.2.50/24", "LoadBalancer traefik/traefik"),
                 vip(2, "192.0.2.53/24", "LoadBalancer kea/kea-db-lan")]
    old_ips, _ = m.stale_load_balancers(addresses, [], {"192.0.2.50": ["traefik/traefik"]}, [])
    assert [a["id"] for a in old_ips] == [2]


def test_a_changed_address_retires_the_old_one():
    # The Service moved from .53 to .55: .55 is recorded by ensure, .53 is stale.
    addresses = [vip(2, "192.0.2.53/24", "LoadBalancer kea/kea-db-lan")]
    old_ips, _ = m.stale_load_balancers(addresses, [], {"192.0.2.55": ["kea/kea-db-lan"]}, [])
    assert [a["id"] for a in old_ips] == [2]


def test_a_removed_pools_range_is_stale_and_a_single_address_pool_has_none():
    ranges = [iprange(7, "192.0.2.50/24", "192.0.2.70/24", "MetalLB pool production"),
              iprange(8, "192.0.2.80/24", "192.0.2.90/24", "MetalLB pool old")]
    pools = [("production", "192.0.2.50", "192.0.2.70"), ("mgmt", "198.51.100.50", "198.51.100.50")]
    _, old_ranges = m.stale_load_balancers([], ranges, {}, pools)
    assert [r["id"] for r in old_ranges] == [8]


def test_only_this_scripts_own_and_not_yet_deprecated_objects_are_stale():
    addresses = [vip(1, "192.0.2.60/24", "a host's own address"),                       # not ours
                 vip(2, "192.0.2.61/24", "LoadBalancer gone/svc", status="deprecated"),  # already retired
                 vip(3, "192.0.2.62/24", "LoadBalancer gone/svc", role="anycast"),      # not a VIP
                 vip(4, "192.0.2.63/24", "LoadBalancer gone/svc")]
    ranges = [iprange(7, "192.0.2.10/24", "192.0.2.20/24", "lan: Kea DHCP pool"),
              iprange(8, "192.0.2.80/24", "192.0.2.90/24", "MetalLB pool old", status="deprecated")]
    old_ips, old_ranges = m.stale_load_balancers(addresses, ranges, {}, [])
    assert [a["id"] for a in old_ips] == [4]
    assert old_ranges == []


def test_a_failed_cluster_read_stops_the_run_before_anything_is_retired(monkeypatch):
    # The cluster is read before any NetBox write; a failed read raises, so main() never
    # reaches stale_load_balancers with an empty answer.
    import base64
    import pytest

    def refused(*args, **kwargs):
        raise SystemExit("FAIL: GET https://192.0.2.1:6443/api/v1/services: no answer (refused)")

    monkeypatch.setattr(m, "http", refused)
    ca = base64.b64encode(b"-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n").decode()
    with pytest.raises(SystemExit, match="no answer"):
        m.cluster_load_balancers("https://192.0.2.1:6443", "token", ca)


def test_retire_writes_only_with_apply_and_reads_back(monkeypatch):
    calls = []

    def fake_http(method, url, headers, body=None, **kw):
        calls.append((method, url.split("/api/", 1)[1], body))
        if method == "GET":
            return {"results": [{"id": 4, "status": {"value": "deprecated"}}]}
        return {}

    monkeypatch.setattr(m, "http", fake_http)
    m.NetBox("https://netbox.example", "nbt_x", apply=False).retire("ipam/ip-addresses", {"id": 4}, "x")
    assert calls == []
    m.NetBox("https://netbox.example", "nbt_x", apply=True).retire("ipam/ip-addresses", {"id": 4}, "x")
    assert calls[0] == ("PATCH", "ipam/ip-addresses/4/", {"status": "deprecated"})
    assert calls[1][0] == "GET"


# Kea's reservations. The MACs are from the documentation range (00:00:5e:00:53:xx).

def kea_host(ip, mac, hostname="", subnet=1):
    return {"ip-address": ip, "hw-address": mac, "hostname": hostname, "subnet-id": subnet}


def kea_pages(pages):
    """A fake http answering reservation-get-page with pages in turn (each a list of hosts),
    then result 3, recording each request's arguments."""
    seen = []

    def fake_http(method, url, headers, body=None, **kw):
        assert body["command"] == "reservation-get-page" and body["service"] == ["dhcp4"]
        seen.append(dict(body["arguments"]))
        i = len(seen) - 1
        if i >= len(pages):
            return [{"result": 3, "text": "0 IPv4 host(s) found.", "arguments": {"count": 0, "hosts": []}}]
        return [{"result": 0, "arguments": {"count": len(pages[i]), "hosts": pages[i],
                                            "next": {"from": 100 + i, "source-index": 1}}}]
    return fake_http, seen


def test_reservations_are_read_page_by_page_until_kea_says_empty(monkeypatch):
    fake, seen = kea_pages([[kea_host("192.0.2.10", "00:00:5e:00:53:01", "printer")],
                            [kea_host("192.0.2.11", "00:00:5e:00:53:02"), {"ip-address": "0.0.0.0", "hw-address": "x"}]])
    monkeypatch.setattr(m, "http", fake)
    got = m.kea_reservations("http://127.0.0.1:8000/", "user:pw", page_size=1)
    assert sorted(got) == ["192.0.2.10", "192.0.2.11"]
    assert got["192.0.2.10"] == {"identifier": "hw-address 00:00:5e:00:53:01", "hostname": "printer", "subnet-id": 1}
    # The first page has no position; each later one copies the previous page's "next".
    assert seen[0] == {"limit": 1}
    assert seen[1] == {"limit": 1, "from": 100, "source-index": 1}
    assert seen[2] == {"limit": 1, "from": 101, "source-index": 1}


def test_a_failed_kea_read_stops_the_run(monkeypatch):
    import pytest
    monkeypatch.setattr(m, "http", lambda *a, **k: [{"result": 1, "text": "unknown command"}])
    with pytest.raises(SystemExit, match="reservation-get-page answered 1"):
        m.kea_reservations("http://127.0.0.1:8000/", "user:pw")


def nb_addr(i, address, description, status="dhcp", assigned=None, role=None):
    return {"id": i, "address": address, "description": description, "status": {"value": status},
            "role": {"value": role} if role else None, "assigned_object_id": assigned,
            "assigned_object": {"virtual_machine": {"name": "vm1"}} if assigned else None}


def test_new_and_own_reservations_are_recorded_and_others_are_reported():
    reserved = {"192.0.2.10": {"identifier": "hw-address 00:00:5e:00:53:01", "hostname": "printer", "subnet-id": 1},
                "192.0.2.11": {"identifier": "hw-address 00:00:5e:00:53:02", "hostname": "", "subnet-id": 1},
                "192.0.2.12": {"identifier": "hw-address 00:00:5e:00:53:03", "hostname": "nas", "subnet-id": 1},
                "192.0.2.13": {"identifier": "hw-address 00:00:5e:00:53:04", "hostname": "", "subnet-id": 1},
                "192.0.2.14": {"identifier": "hw-address 00:00:5e:00:53:05", "hostname": "", "subnet-id": 1}}
    addresses = [nb_addr(1, "192.0.2.11/24", "Kea reservation hw-address 00:00:5e:00:53:02"),   # ours already
                 nb_addr(2, "192.0.2.12/24", "", status="active", assigned=7),                  # a VM's
                 nb_addr(3, "192.0.2.13/24", "LoadBalancer kea/kea-db-lan", status="active", role="vip"),
                 nb_addr(4, "192.0.2.14/24", "the printer, by hand", status="active")]          # someone's note
    record, held, stale = m.plan_reservations(addresses, reserved)
    assert record == ["192.0.2.10", "192.0.2.11"]
    assert held == [("192.0.2.12", "on vm1"), ("192.0.2.13", "a VIP"),
                    ("192.0.2.14", "recorded as 'the printer, by hand'")]
    assert stale == []


def test_a_removed_reservation_is_stale_but_only_this_scripts_own():
    addresses = [nb_addr(1, "192.0.2.20/24", "Kea reservation hw-address 00:00:5e:00:53:09"),   # gone from Kea
                 nb_addr(2, "192.0.2.21/24", "Kea reservation old", status="deprecated"),     # already retired
                 nb_addr(3, "192.0.2.22/24", "Kea reservation vm", status="active", assigned=7),  # a VM took it
                 nb_addr(4, "192.0.2.23/24", "a static address", status="active")]
    _, _, stale = m.plan_reservations(addresses, {})
    assert [a["id"] for a in stale] == [1]


def test_a_reservations_label_has_its_hostname_when_it_has_one():
    assert m.reservation_label({"identifier": "hw-address 00:00:5e:00:53:01", "hostname": "printer"}) == \
        "Kea reservation printer (hw-address 00:00:5e:00:53:01)"
    assert m.reservation_label({"identifier": "client-id 01:02", "hostname": ""}) == "Kea reservation client-id 01:02"


# The IoT network's devices (netbox_seed_iot.yaml). Hostnames follow its scheme with a made-up
# site code, "zz".

def iot_catalogue():
    from ruamel.yaml import YAML
    return YAML(typ="safe", pure=True).load((SCRIPT.parent / "netbox_seed_iot.yaml").read_text(encoding="utf-8"))


def test_the_catalogue_names_only_what_it_defines_and_every_type_has_its_reserved_interface():
    cat = iot_catalogue()
    makers = {x["slug"] for x in cat["manufacturers"]}
    types = {t["slug"]: t for t in cat["device_types"]}
    assert len(types) == len(cat["device_types"])
    for t in types.values():
        assert t["manufacturer"] in makers, t["slug"]
        assert m.reserved_interface(t) in [(i["name"], i["type"]) for i in t["interfaces"]], t["slug"]
    for r in cat["rules"]:
        assert r["type"] in types and r["role"] in cat["roles"], r
    assert len({m.slug(r) for r in cat["roles"]}) == len(cat["roles"])


def test_a_reservation_goes_on_wifi0_unless_its_type_names_another_interface():
    wifi = {"interfaces": [{"name": "bt0", "type": "ieee802.15.1"}, {"name": "wifi0", "type": "ieee802.11ac"}]}
    wired = {"reserved_on": "eth0", "interfaces": [{"name": "eth0", "type": "100base-tx"},
                                                   {"name": "wifi0", "type": "ieee802.11n"}]}
    assert m.reserved_interface(wifi) == ("wifi0", "ieee802.11ac")
    assert m.reserved_interface(wired) == ("eth0", "100base-tx")
    rules = iot_catalogue()["rules"]
    types = {t["slug"]: t for t in iot_catalogue()["device_types"]}
    proxy = m.iot_kind("zziotesbtpfront", rules)
    assert proxy["role"] == "Bluetooth Proxy"
    # The door proxy runs on Wi-Fi (devices/door-bt-proxy/README.md), its reservation on the base MAC.
    assert m.reserved_interface(types[proxy["type"]])[0] == "wifi0"


def test_iot_kind_reads_what_follows_iot_and_the_first_matching_rule_wins():
    rules = iot_catalogue()["rules"]
    assert m.iot_kind("zziotkalikitchen", rules)["type"] == "kasa-hs200"
    assert m.iot_kind("ZZIOTKAPLDESK", rules)["role"] == "Smart Power Plug"
    assert m.iot_kind("zziotazechospotliving", rules)["type"] == "amazon-echo-spot"
    assert m.iot_kind("zziotazbrechosp", rules)["type"] == "amazon-echo-spot"
    assert m.iot_kind("zziotazechosbedroom", rules)["type"] == "amazon-echo-dot-4th-gen-clock"
    assert m.iot_kind("zziotazechodkitchen", rules)["type"] == "amazon-echo-dot"
    assert m.iot_kind("zziotazfiretliving", rules)["role"] == "TV"
    assert m.iot_kind("zziotazfiresbedroom", rules)["role"] == "Media Viewer"
    assert m.iot_kind("zzaaiotkalikitchen", rules)["type"] == "kasa-hs200"
    assert m.iot_kind("zziotqqunknown", rules) is None
    assert m.iot_kind("printer", rules) is None


def test_plan_iot_devices_takes_the_subnets_named_hardware_reservations():
    rules = iot_catalogue()["rules"]
    reserved = {
        "192.0.2.10": {"identifier": "hw-address 00:00:5e:00:53:0a", "hostname": "zziotkalihall", "subnet-id": 6},
        "192.0.2.11": {"identifier": "hw-address 00:00:5e:00:53:0b", "hostname": "zziotwzcamporch", "subnet-id": 6},
        "192.0.2.12": {"identifier": "hw-address 00:00:5e:00:53:0c", "hostname": "", "subnet-id": 6},
        "192.0.2.13": {"identifier": "client-id 01:02", "hostname": "zziotkapldesk", "subnet-id": 6},
        "192.0.2.14": {"identifier": "hw-address 00:00:5e:00:53:0e", "hostname": "zziotqqthing", "subnet-id": 6},
        "198.51.100.5": {"identifier": "hw-address 00:00:5e:00:53:0f", "hostname": "zziotkalinas", "subnet-id": 7},
    }
    devices, skipped = m.plan_iot_devices(reserved, "192.0.2.0/24", rules, {"zziotkalihall": "kasa-hs210"})
    assert devices == [
        {"name": "zziotkalihall", "ip": "192.0.2.10", "mac": "00:00:5E:00:53:0A", "type": "kasa-hs210",
         "role": "Smart Power Switch"},
        {"name": "zziotwzcamporch", "ip": "192.0.2.11", "mac": "00:00:5E:00:53:0B", "type": "wyze-cam-v3",
         "role": "Camera"}]
    assert [ip for ip, _ in skipped] == ["192.0.2.12", "192.0.2.13", "192.0.2.14"]
    assert "no hostname" in skipped[0][1] and "client-id" in skipped[1][1] and "no rule" in skipped[2][1]


def test_only_this_scripts_iot_devices_whose_reservation_is_gone_go_offline():
    mark = m.IOT_DEVICE_MARK
    have = [{"id": 1, "name": "zziotkalihall", "description": mark, "status": {"value": "active"}},
            {"id": 2, "name": "zziotkaliold", "description": mark, "status": {"value": "active"}},
            {"id": 3, "name": "zziotkaligone", "description": mark, "status": {"value": "offline"}},
            {"id": 4, "name": "by-hand", "description": "", "status": {"value": "active"}}]
    assert [d["id"] for d in m.stale_iot_devices(have, {"zziotkalihall"})] == [2]


def test_iot_models_reads_the_environment_then_the_home_file(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(m.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("NETBOX_SEED_IOT_MODELS", raising=False)
    assert m.iot_models() == {}
    (tmp_path / ".netbox-seed-iot-models.json").write_text('{"zziotkalihall": "kasa-hs210"}', encoding="utf-8")
    assert m.iot_models() == {"zziotkalihall": "kasa-hs210"}
    monkeypatch.setenv("NETBOX_SEED_IOT_MODELS", '{"zziotkalistairs": "kasa-hs210"}')
    assert m.iot_models() == {"zziotkalistairs": "kasa-hs210"}
    monkeypatch.setenv("NETBOX_SEED_IOT_MODELS", "not json")
    with pytest.raises(SystemExit, match="isn't JSON"):
        m.iot_models()
    monkeypatch.setenv("NETBOX_SEED_IOT_MODELS", '["kasa-hs210"]')
    with pytest.raises(SystemExit, match="JSON object"):
        m.iot_models()


def test_an_address_on_this_scripts_own_iot_device_is_still_its_own():
    reserved = {"192.0.2.10": {"identifier": "hw-address 00:00:5e:00:53:0a", "hostname": "zziotkalihall", "subnet-id": 6}}
    a = nb_addr(1, "192.0.2.10/24", "Kea reservation zziotkalihall (hw-address 00:00:5e:00:53:0a)", assigned=5)
    a["assigned_object"] = {"device": {"id": 9, "name": "zziotkalihall"}}
    assert m.plan_reservations([a], reserved, frozenset({9}))[:2] == (["192.0.2.10"], [])
    assert m.plan_reservations([a], reserved)[1] == [("192.0.2.10", "on zziotkalihall")]
    # Once its reservation is gone, the address is stale like any of this script's own.
    assert [x["id"] for x in m.plan_reservations([a], {}, frozenset({9}))[2]] == [1]


def test_this_scripts_iot_devices_addresses_get_no_dns_name():
    a = _addr(1, "192.0.2.10/24", kind="dcim.interface", name="zziotkalihall")
    a["assigned_object"]["device"]["id"] = 9
    assert m.wanted_dns_names([a], PREFIXES, skip_devices=frozenset({9})) == {}
    assert m.wanted_dns_names([a], PREFIXES) == {1: "zziotkalihall.mgmt.example"}


def test_retire_can_set_a_device_offline(monkeypatch):
    calls = []

    def fake_http(method, url, headers, body=None, **kw):
        calls.append((method, url.split("/api/", 1)[1], body))
        return {"results": [{"id": 4, "status": {"value": "offline"}}]} if method == "GET" else {}

    monkeypatch.setattr(m, "http", fake_http)
    nb = m.NetBox("https://netbox.example", "nbt_x", apply=True)
    nb.retire("dcim/devices", {"id": 4}, "x", "Kea's reservations", status="offline")
    assert calls[0] == ("PATCH", "dcim/devices/4/", {"status": "offline"})
    assert nb.counts == {"set-offline dcim/devices": 1}


def test_the_tailscale_tags_each_go_on_one_kind_of_object():
    tags = m.TAILSCALE_TAGS
    assert set(tags) == {"tailscale-route-home", "tailscale-route-travel", "tailscale-dns", "tailscale-host"}
    # A route tag only on prefixes, so a host can never become an advertised route.
    assert tags["tailscale-route-home"][0] == tags["tailscale-route-travel"][0] == ["ipam.prefix"]
    assert tags["tailscale-dns"][0] == ["netbox_dns.zone"]
    assert set(tags["tailscale-host"][0]) == {"dcim.device", "virtualization.virtualmachine", "ipam.ipaddress"}
    assert all(about for _, about in tags.values())


def test_the_termix_tags_go_on_hosts_only():
    assert set(m.TERMIX_TAGS) == {"bastion-key", "rdp", "thingino"}
    for tag in ("bastion-key", "rdp"):
        assert set(m.TERMIX_TAGS[tag][0]) == {"dcim.device", "virtualization.virtualmachine"}
    # A camera is a device, never a VM.
    assert m.TERMIX_TAGS["thingino"][0] == ["dcim.device"]
    assert not set(m.TERMIX_TAGS) & set(m.TAILSCALE_TAGS)


def test_an_iot_device_being_decommissioned_is_held_and_never_set_offline():
    held = {"name": "zziotkalihall", "description": m.IOT_DEVICE_MARK, "status": {"value": "decommissioning"}}
    live = {"name": "zziotkalikitchen", "description": m.IOT_DEVICE_MARK, "status": {"value": "active"}}
    assert m.iot_held(held) and not m.iot_held(live)
    assert m.iot_held({"status": "decommissioning"})  # a plain value, as some reads give it
    # Its reservation gone, a live device goes Offline; one being decommissioned is left.
    assert [d["name"] for d in m.stale_iot_devices([held, live], set())] == ["zziotkalikitchen"]


def test_a_sync_moves_a_status_only_between_planned_active_and_offline():
    st = lambda v: {"status": {"value": v}}
    assert m.synced_status(None, True) == "active" and m.synced_status(None, False) == "offline"
    assert m.synced_status(st("active"), False) == "offline" and m.synced_status(st("offline"), True) == "active"
    assert m.synced_status(st("planned"), True) == "active"
    assert m.synced_status(st("planned"), False) is None  # not built yet: stays Planned
    for owner_set in ("staged", "failed", "decommissioning", "inventory"):
        assert m.synced_status(st(owner_set), True) is None and m.synced_status(st(owner_set), False) is None


def _owned(addr_id, address, vm_id, dns=""):
    a = _addr(addr_id, address)
    a["assigned_object"]["virtual_machine"]["id"] = vm_id
    a["dns_name"] = dns
    return a


def test_dns_names_follow_status_and_leave_decommissioning_and_hand_set_names():
    vms = [{"id": 1, "status": {"value": "active"}}, {"id": 2, "status": {"value": "offline"}},
           {"id": 3, "status": {"value": "decommissioning"}}, {"id": 4, "status": {"value": "failed"}}]
    addrs = [_owned(10, "192.0.2.10/24", 1),                               # in use, unnamed: named
             _owned(11, "192.0.2.11/24", 2, dns="guest1.mgmt.example"),    # offline, our name: cleared
             _owned(12, "192.0.2.12/24", 3, dns="guest1.mgmt.example"),    # decommissioning: left
             _owned(13, "192.0.2.13/24", 4, dns="handmade.example")]       # failed, hand-set name: left
    assert m.dns_plan(addrs, PREFIXES, vms, [], frozenset()) == {10: "guest1.mgmt.example", 11: ""}


# --- NetBox's writes and read-backs, against an in-memory NetBox ------------------------------

import urllib.parse

import pytest


class FakeNetBox:
    """NetBox's REST API in memory: objects per path; GET filters by query (field=value, a
    nested object's id by <field>_id or <field>) and pages by limit and offset; POST, PATCH
    and DELETE change what the next GET reads. `keep_deleted` answers a DELETE and keeps the
    object, as a NetBox that refused it quietly would."""

    def __init__(self, keep_deleted=False):
        self.objects: dict[str, list[dict]] = {}
        self.calls: list[tuple[str, str, object]] = []
        self.next_id = 100
        self.keep_deleted = keep_deleted

    def add(self, path, **obj):
        self.next_id += 1
        obj = {"id": self.next_id, **obj}
        self.objects.setdefault(path, []).append(obj)
        return obj

    @staticmethod
    def _matches(obj, key, value):
        for k in (key, key[:-3] if key.endswith("_id") else None):
            if k and k in obj:
                v = obj[k]["id"] if isinstance(obj[k], dict) else obj[k]
                return str(v) == value
        return False

    def http(self, method, url, headers, body=None, **kw):
        self.calls.append((method, url, body))
        parts = urllib.parse.urlsplit(url)
        path = parts.path.split("/api/", 1)[1].strip("/")
        if method == "GET":
            q = dict(urllib.parse.parse_qsl(parts.query))
            limit, offset = int(q.pop("limit", 50)), int(q.pop("offset", 0))
            hits = [o for o in self.objects.get(path, []) if all(self._matches(o, k, v) for k, v in q.items())]
            page = hits[offset:offset + limit]
            more = offset + limit < len(hits)
            nxt = f"{parts.scheme}://{parts.netloc}{parts.path}?{parts.query}&offset={offset + limit}" if more else None
            return {"count": len(hits), "results": page, "next": nxt}
        kind, _, oid = path.rpartition("/")
        if method == "POST":
            return self.add(path, **body)
        obj = next(o for o in self.objects[kind] if o["id"] == int(oid))
        if method == "PATCH":
            obj.update(body)
            return obj
        if method == "DELETE":
            if not self.keep_deleted:
                self.objects[kind].remove(obj)
            return None
        raise AssertionError(method)

    def writes(self):
        return [(m_, u.split("/api/", 1)[1], b) for m_, u, b in self.calls if m_ != "GET"]


@pytest.fixture
def netbox(monkeypatch):
    def make(apply=True, **kw):
        fake = FakeNetBox(**kw)
        monkeypatch.setattr(m, "http", fake.http)
        return fake, m.NetBox("https://netbox.example", "token", apply)
    return make


def test_ensure_creates_with_the_lookup_and_fields_only_when_applying(netbox):
    fake, nb = netbox(apply=True)
    made = nb.ensure("dcim/sites", {"slug": "home"}, {"name": "Home", "status": "active"}, "Home")
    assert fake.writes() == [("POST", "dcim/sites/", {"slug": "home", "name": "Home", "status": "active"})]
    assert made["id"] > 0 and fake.objects["dcim/sites"] == [made]
    assert nb.counts == {"create dcim/sites": 1}

    fake, nb = netbox(apply=False)
    first = nb.ensure("dcim/sites", {"slug": "home"}, {"name": "Home"}, "Home")
    second = nb.ensure("dcim/sites", {"slug": "lab"}, {"name": "Lab"}, "Lab")
    assert fake.writes() == [] and (first["id"], second["id"]) == (-1, -2)
    assert first == {"id": -1, "slug": "home", "name": "Home"}


def test_ensure_patches_only_what_differs_and_leaves_a_match_alone(netbox):
    fake, nb = netbox(apply=True)
    site = fake.add("dcim/sites", slug="home", name="Home", status={"value": "active", "label": "Active"})
    assert nb.ensure("dcim/sites", {"slug": "home"}, {"name": "Home", "status": "active"}, "Home") == site
    assert fake.writes() == [] and nb.counts == {}
    nb.ensure("dcim/sites", {"slug": "home"}, {"name": "Home Lab", "status": "active"}, "Home")
    assert fake.writes() == [("PATCH", f"dcim/sites/{site['id']}/", {"name": "Home Lab"})]
    assert nb.counts == {"update dcim/sites": 1}

    fake, nb = netbox(apply=False)
    fake.add("dcim/sites", slug="home", name="Home")
    nb.ensure("dcim/sites", {"slug": "home"}, {"name": "Home Lab"}, "Home")
    assert fake.writes() == [] and nb.counts == {"update dcim/sites": 1}


def test_ensure_looks_up_nothing_by_an_object_the_dry_run_only_would_create(netbox):
    fake, nb = netbox(apply=False)
    made = nb.ensure("dcim/interfaces", {"device_id": -3, "name": "vmbr0"}, {"type": "bridge"}, "n vmbr0")
    assert fake.calls == [] and made["id"] < 0


def test_find_refuses_more_than_one_match(netbox):
    fake, nb = netbox()
    fake.add("dcim/devices", name="pve1")
    assert nb.find("dcim/devices", name="pve1")["name"] == "pve1"
    assert nb.find("dcim/devices", name="pve2") is None
    fake.add("dcim/devices", name="pve1")
    with pytest.raises(SystemExit):
        nb.find("dcim/devices", name="pve1")


def test_all_reads_every_page(netbox):
    fake, nb = netbox()
    for i in range(1203):
        fake.add("ipam/ip-addresses", address=f"192.0.2.{i % 250}/24", role="vip" if i % 3 == 0 else "")
    assert len(nb.all("ipam/ip-addresses")) == 1203
    assert len(nb.all("ipam/ip-addresses", role="vip")) == 401
    assert sum(1 for c in fake.calls if c[0] == "GET") == 3 + 1


def test_delete_writes_only_when_allowed_and_applying_and_reads_it_back(netbox):
    fake, nb = netbox(apply=True)
    port = fake.add("dcim/interfaces", name="eno2")
    nb.delete("dcim/interfaces", port, "pve1 eno2", allowed=False)
    assert fake.writes() == [] and nb.counts == {"stale dcim/interfaces": 1}
    nb.delete("dcim/interfaces", port, "pve1 eno2", allowed=True)
    assert fake.writes() == [("DELETE", f"dcim/interfaces/{port['id']}/", None)]
    assert fake.objects["dcim/interfaces"] == []
    assert fake.calls[-1][0] == "GET", "the delete is read back"

    fake, nb = netbox(apply=False)
    port = fake.add("dcim/interfaces", name="eno2")
    nb.delete("dcim/interfaces", port, "pve1 eno2", allowed=True)
    assert fake.calls == [] and nb.counts == {"delete dcim/interfaces": 1}


def test_a_delete_netbox_didnt_do_stops_the_run(netbox):
    fake, nb = netbox(apply=True, keep_deleted=True)
    port = fake.add("dcim/interfaces", name="eno2")
    with pytest.raises(SystemExit):
        nb.delete("dcim/interfaces", port, "pve1 eno2", allowed=True)


def test_set_ref_points_the_field_once_both_objects_exist(netbox):
    fake, nb = netbox(apply=True)
    dev = fake.add("dcim/devices", name="pve1", primary_ip4=None)
    ip = fake.add("ipam/ip-addresses", address="192.0.2.5/24")
    nb.set_ref("dcim/devices", dev, "primary_ip4", ip, "pve1")
    assert fake.writes() == [("PATCH", f"dcim/devices/{dev['id']}/", {"primary_ip4": ip["id"]})]
    nb.set_ref("dcim/devices", {**dev, "primary_ip4": {"id": ip["id"]}}, "primary_ip4", ip, "pve1")
    assert len(fake.writes()) == 1, "already pointing there: nothing written"

    dev, ip = {"id": 900, "primary_ip4": None}, {"id": 901}
    for apply, obj, target in ((True, {"id": -1}, ip), (True, dev, {"id": -2}), (False, dev, ip)):
        fake, nb = netbox(apply=apply)
        nb.set_ref("dcim/devices", obj, "primary_ip4", target, "pve1")
        assert fake.writes() == [] and nb.counts == {"set primary_ip4/dcim/devices": 1}


def test_differ_reports_an_owned_shape_and_writes_nothing(netbox, capsys):
    fake, nb = netbox(apply=True)
    m.differ(nb, "vm1", {"vcpus": (4, 4), "memory": (8192, 4096)})
    assert fake.calls == [] and nb.counts == {"differ owned/shape": 1}
    out = capsys.readouterr().out
    assert "vm1 memory: NetBox 8192, Proxmox 4096" in out and "vcpus" not in out


def test_own_iot_devices_are_the_sites_devices_with_the_mark(netbox):
    fake, nb = netbox()
    mine = fake.add("dcim/devices", name="plug-1", site={"id": 7}, description=m.IOT_DEVICE_MARK)
    fake.add("dcim/devices", name="pve1", site={"id": 7}, description="")
    fake.add("dcim/devices", name="plug-2", site={"id": 8}, description=m.IOT_DEVICE_MARK)
    assert m.own_iot_devices(nb, 7) == [mine]


def test_with_len_takes_the_holding_prefixes_length():
    prefixes = {"192.0.2.0/24": {}, "198.51.100.0/25": {}}
    assert m.with_len("192.0.2.9", prefixes) == "192.0.2.9/24"
    assert m.with_len("198.51.100.9/32", prefixes) == "198.51.100.9/25"
    assert m.with_len("203.0.113.9/28", prefixes) == "203.0.113.9/28"
    assert m.with_len("203.0.113.9", prefixes) == "203.0.113.9/32"


def test_primary_of_is_the_address_without_its_length():
    assert m.primary_of({"primary_ip4": {"id": 1, "address": "192.0.2.5/24"}}) == "192.0.2.5"
    assert m.primary_of({"primary_ip4": None}) is None
    assert m.primary_of({}) is None


def test_bridge_of():
    assert m.bridge_of("virtio=BC:24:11:00:00:01,bridge=vmbr7,firewall=1") == "vmbr7"
    assert m.bridge_of("virtio=BC:24:11:00:00:01") is None


NODE_IFACES = [
    {"iface": "eno1.30", "type": "vlan"},
    {"iface": "eno1", "type": "eth", "comments": " uplink "},
    {"iface": "vmbr0", "type": "bridge", "bridge_ports": "eno1", "cidr": "192.0.2.5/24"},
    {"iface": "vmbr30", "type": "bridge", "bridge_ports": "eno1.30", "cidr": "198.51.100.5/24"},
]


def test_node_interfaces_nests_ports_in_bridges_and_vlans_under_ports(netbox):
    fake, nb = netbox(apply=True)
    dev = fake.add("dcim/devices", name="pve1")
    vlan = fake.add("ipam/vlans", vid=30, name="IoT")
    prefixes = {"192.0.2.0/24": {}, "198.51.100.0/24": {}}
    primary = m.node_interfaces(nb, prefixes, {30: vlan}, dev, "pve1", NODE_IFACES, None)

    made = [b for meth, path, b in fake.writes() if path == "dcim/interfaces/" and meth == "POST"]
    assert [b["name"] for b in made] == ["vmbr0", "vmbr30", "eno1", "eno1.30"], "bridges, then ports, then VLANs"
    by = {o["name"]: o for o in fake.objects["dcim/interfaces"]}
    assert by["eno1"]["bridge"] == by["vmbr0"]["id"] and by["eno1"]["description"] == "uplink"
    assert by["eno1.30"]["parent"] == by["eno1"]["id"] and by["eno1.30"]["bridge"] == by["vmbr30"]["id"]
    assert (by["eno1.30"]["mode"], by["eno1.30"]["untagged_vlan"], by["eno1.30"]["description"]) == \
        ("access", vlan["id"], "VLAN 30")
    assert {o["type"] for o in made if o["name"].startswith("vmbr")} == {"bridge"}
    ips = {o["address"]: o for o in fake.objects["ipam/ip-addresses"]}
    assert set(ips) == {"192.0.2.5/24", "198.51.100.5/24"}
    assert ips["192.0.2.5/24"]["assigned_object_id"] == by["vmbr0"]["id"]
    assert primary == ips["192.0.2.5/24"], "no primary in NetBox: the management bridge's"


def test_node_interfaces_keeps_the_primary_netbox_has_while_the_node_has_it(netbox):
    fake, nb = netbox(apply=True)
    dev = fake.add("dcim/devices", name="pve1")
    prefixes = {"192.0.2.0/24": {}, "198.51.100.0/24": {}}
    primary = m.node_interfaces(nb, prefixes, {}, dev, "pve1", NODE_IFACES, "198.51.100.5")
    assert primary["address"] == "198.51.100.5/24"
    gone = m.node_interfaces(nb, prefixes, {}, dev, "pve1", NODE_IFACES, "203.0.113.1")
    assert gone["address"] == "192.0.2.5/24"


def test_same_tells_lists_of_different_objects_apart():
    assert m.same([{"slug": "a"}, {"slug": "b"}], ["b", "a"])
    assert not m.same([{"slug": "a"}, {"slug": "b"}], ["a", "c"])
    assert not m.same([{"id": 1}], [2])


def test_kea_networks_reads_plain_and_shared_subnets_and_their_pools(monkeypatch):
    answer = [{"result": 0, "arguments": {"Dhcp4": {
        "subnet4": [{"subnet": "192.0.2.0/24", "pools": [{"pool": "192.0.2.100 - 192.0.2.199"}]}],
        "shared-networks": [{"subnet4": [{"subnet": "198.51.100.0/24",
                                          "pools": [{"pool": "198.51.100.10-198.51.100.20"},
                                                    {"pool": "198.51.100.30-198.51.100.40"}]}]}]}}}]
    sent = []
    monkeypatch.setattr(m, "http", lambda method, url, headers, body=None, **kw: sent.append(body) or answer)
    subnets, pools = m.kea_networks("http://kea.example/", "user:pass")
    assert subnets == ["192.0.2.0/24", "198.51.100.0/24"]
    assert pools == {"192.0.2.0/24": [("192.0.2.100", "192.0.2.199")],
                     "198.51.100.0/24": [("198.51.100.10", "198.51.100.20"), ("198.51.100.30", "198.51.100.40")]}
    assert sent == [{"command": "config-get", "service": ["dhcp4"]}]


def test_a_kea_error_answer_stops_the_run(monkeypatch):
    monkeypatch.setattr(m, "http", lambda *a, **kw: [{"result": 1, "text": "unauthorized"}])
    with pytest.raises(SystemExit):
        m.kea_networks("http://kea.example/", "user:pass")


def test_proxmox_state_reads_nodes_and_guests_and_the_agents_addresses(monkeypatch):
    ifaces = [{"iface": "vmbr0", "type": "bridge", "cidr": "192.0.2.5/24", "active": 1},
              {"iface": "wlx0", "type": "eth", "active": 0},
              {"iface": "vmbr9", "type": "bridge", "cidr": "203.0.113.5/24", "active": 0}]
    agent = {"result": [
        {"hardware-address": "bc:24:11:00:00:01",
         "ip-addresses": [{"ip-address-type": "ipv4", "ip-address": "192.0.2.50", "prefix": 24},
                          {"ip-address-type": "ipv6", "ip-address": "fe80::1", "prefix": 64},
                          {"ip-address-type": "ipv4", "ip-address": "127.0.0.1", "prefix": 8}]},
        {"hardware-address": "bc:24:11:00:00:02",
         "ip-addresses": [{"ip-address-type": "ipv4", "ip-address": "198.51.100.9", "prefix": 24}]},
        {"hardware-address": "00:00:00:00:00:00",
         "ip-addresses": [{"ip-address-type": "ipv4", "ip-address": "127.0.0.1", "prefix": 8}]}]}
    answers = {
        "/cluster/status": [{"type": "cluster", "name": "lab"}, {"type": "node", "name": "pve1"}],
        "/nodes": [{"node": "pve1", "maxcpu": 8, "status": "online"}],
        "/nodes/pve1/network": ifaces,
        "/cluster/resources?type=vm": [{"node": "pve1", "type": "qemu", "vmid": 101, "name": "vm1", "status": "running"},
                                       {"node": "pve1", "type": "qemu", "vmid": 102, "name": "vm2", "status": "running"}],
        "/nodes/pve1/qemu/101/config": {"agent": "1", "net0": "virtio=BC:24:11:00:00:01,bridge=vmbr0",
                                        "net1": "virtio=BC:24:11:00:00:02,bridge=vmbr7",
                                        "ipconfig1": "ip=198.51.100.24/24"},
        "/nodes/pve1/qemu/101/agent/network-get-interfaces": agent,
        "/nodes/pve1/qemu/102/config": {"agent": "1", "net0": "virtio=BC:24:11:00:00:03,bridge=vmbr0"},
    }

    def fake_http(method, url, headers, body=None, **kw):
        p = url.split("/api2/json", 1)[1]
        if p == "/nodes/pve1/qemu/102/agent/network-get-interfaces":
            raise SystemExit("FAIL: no agent")
        return {"data": answers[p]}

    monkeypatch.setattr(m, "http", fake_http)
    cluster, nodes, guests = m.proxmox_state("https://pve.example:8006", "token")
    assert cluster == "lab"
    assert nodes["pve1"]["bridges"] == {"vmbr0": "192.0.2.5/24"}, "an interface that's down stays out"
    assert nodes["pve1"]["names"] == {"vmbr0", "wlx0", "vmbr9"}, "but it still counts as the node's"
    vm1, vm2 = guests
    assert vm1["ips"] == {"net0": ["192.0.2.50/24"], "net1": ["198.51.100.24/24"]}, \
        "the agent's IPv4 on a NIC without a configured address; a configured NIC keeps only that"
    assert vm1["nets"] == {"net0": "vmbr0", "net1": "vmbr7"}
    assert vm1["macs"] == {"net0": "BC:24:11:00:00:01", "net1": "BC:24:11:00:00:02"}
    assert vm2["ips"] == {}, "an agent that doesn't answer adds nothing and stops nothing"
