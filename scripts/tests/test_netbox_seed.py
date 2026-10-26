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
    return YAML(typ="safe").load((SCRIPT.parent / "netbox_seed_iot.yaml").read_text(encoding="utf-8"))


def test_the_catalogue_names_only_what_it_defines_and_every_type_has_wifi0():
    cat = iot_catalogue()
    makers = {x["slug"] for x in cat["manufacturers"]}
    types = {t["slug"]: t for t in cat["device_types"]}
    assert len(types) == len(cat["device_types"])
    for t in types.values():
        assert t["manufacturer"] in makers, t["slug"]
        assert "wifi0" in [i["name"] for i in t["interfaces"]], t["slug"]
    for r in cat["rules"]:
        assert r["type"] in types and r["role"] in cat["roles"], r
    assert len({m.slug(r) for r in cat["roles"]}) == len(cat["roles"])


def test_iot_kind_reads_what_follows_iot_and_the_first_matching_rule_wins():
    rules = iot_catalogue()["rules"]
    assert m.iot_kind("zziotkalikitchen", rules)["type"] == "kasa-hs200"
    assert m.iot_kind("ZZIOTKAPLDESK", rules)["role"] == "Smart Power Plug"
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
    assert set(m.TERMIX_TAGS) == {"bastion-key", "rdp"}
    for tag in m.TERMIX_TAGS:
        assert set(m.TERMIX_TAGS[tag][0]) == {"dcim.device", "virtualization.virtualmachine"}
    assert not set(m.TERMIX_TAGS) & set(m.TAILSCALE_TAGS)
