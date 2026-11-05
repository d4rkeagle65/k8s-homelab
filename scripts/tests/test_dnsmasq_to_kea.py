"""docker/kea/tools/dnsmasq_to_kea.py: a dnsmasq DHCP setup into Kea's site file and lease
import list, and what it can't convert reported for a look. One dnsmasq setup (documentation
addresses, and 198.18.0.9 from the benchmarking range for one in no subnet) exercises each rule; the expected Kea values follow from dnsmasq's and Kea's
meanings, not from a run of the script."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docker/kea/tools/dnsmasq_to_kea.py"

CONF = """\
# a comment, and a line with no = are skipped
no-resolv
domain=home.example
domain=lab.example,198.51.100.0/24
dhcp-range=set:lan,192.0.2.100,192.0.2.199,255.255.255.0,12h
dhcp-range=eth1,198.51.100.50,198.51.100.60,1h
dhcp-range=203.0.113.0,static
dhcp-range=::1,::ff,constructor:eth0
dhcp-option=tag:lan,option:router,192.0.2.1
dhcp-option=tag:lan,option:dns-server,192.0.2.53,192.0.2.54
dhcp-option=eth1,3,198.51.100.1
dhcp-option=option:ntp-server,192.0.2.123
dhcp-option=option:domain-search,a.example,b.example
dhcp-option=121,192.0.2.0/24,192.0.2.1,198.51.100.0/24,192.0.2.2
dhcp-option=option:netmask,255.255.255.0
dhcp-option=tag:nope,option:router,192.0.2.250
dhcp-option=vendor:MSFT,2,1i
dhcp-option=option:smtp-server,0.0.0.0
dhcp-option=252,http://wpad.example/wpad.dat
dhcp-host=AA:BB:CC:DD:EE:01,192.0.2.10,printer
dhcp-host=aa-bb-cc-dd-ee-2,192.0.2.11,laptop,set:trusted,24h
dhcp-host=id:01:aa:bb,192.0.2.12
dhcp-host=AA:BB:CC:DD:EE:03,AA:BB:CC:DD:EE:04,192.0.2.13,dual
dhcp-host=AA:BB:CC:DD:EE:05,ignore
dhcp-host=onlyname,192.0.2.14
dhcp-host=AA:BB:CC:DD:EE:06,justaname
dhcp-host=AA:BB:CC:DD:EE:07,198.18.0.9
dhcp-host=00:11:22:*:*:*,192.0.2.15
dhcp-boot=pxelinux.0
"""


def load():
    spec = importlib.util.spec_from_file_location("dnsmasq_to_kea_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    sys.modules[spec.name] = module  # @dataclass looks its module up there
    spec.loader.exec_module(module)
    return module


m = load()


@pytest.fixture
def converted(tmp_path, capsys):
    (tmp_path / "dnsmasq.conf").write_text(CONF, encoding="utf-8")
    future, past = int(time.time()) + 3600, int(time.time()) - 3600
    (tmp_path / "dnsmasq.leases").write_text(
        f"0 aa:bb:cc:dd:ee:01 192.0.2.10 printer 01:aa:bb:cc:dd:ee:01\n"
        f"{future} aa:bb:cc:dd:ee:09 192.0.2.150 * *\n"
        f"{past} aa:bb:cc:dd:ee:0a 192.0.2.151 old *\n"
        f"{future} aa:bb:cc:dd:ee:0b 198.18.0.9 outside *\n"
        f"duid 00:01:00:01:aa:bb\n", encoding="utf-8")
    out = tmp_path / "out"
    assert m.main(["--conf", str(tmp_path / "dnsmasq.conf"), "--leases", str(tmp_path / "dnsmasq.leases"),
                   "--out-dir", str(out)]) == 0
    text = (out / "site.json").read_text(encoding="utf-8")
    site = json.loads("{" + text + "}")  # members only, for Kea to include inside "Dhcp4"
    leases = json.loads((out / "leases-import.json").read_text(encoding="utf-8"))
    return site, leases, capsys.readouterr().out


def _subnet(site, cidr):
    return next(s for s in site["subnet4"] if s["subnet"] == cidr)


def _options(entry):
    return {o["name"]: o["data"] for o in entry.get("option-data", [])}


def test_site_json_is_members_only(converted, tmp_path):
    text = (tmp_path / "out" / "site.json").read_text(encoding="utf-8")
    assert not text.lstrip().startswith("{") and text.strip().startswith('"valid-lifetime"')


def test_ranges_become_subnets_with_their_ids_pools_and_lifetimes(converted):
    site, _, _ = converted
    assert [s["subnet"] for s in site["subnet4"]] == ["192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"]
    lan, eth1, static = (_subnet(site, c) for c in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))
    assert (lan["id"], eth1["id"], static["id"]) == (2, 100, 113), "a /24's third octet"
    assert lan["pools"] == [{"pool": "192.0.2.100 - 192.0.2.199"}] and lan["valid-lifetime"] == 43200
    assert eth1["pools"] == [{"pool": "198.51.100.50 - 198.51.100.60"}] and eth1["valid-lifetime"] == 3600
    assert "pools" not in static, "a static range reserves only"
    assert site["valid-lifetime"] == 3600, "dnsmasq's default lease time"


def test_options_go_to_their_tags_subnets_or_globally(converted):
    site, _, _ = converted
    lan, eth1, static = (_subnet(site, c) for c in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))
    assert _options(lan) == {"routers": "192.0.2.1", "domain-name-servers": "192.0.2.53, 192.0.2.54",
                             "domain-name": "home.example"}
    assert _options(eth1) == {"routers": "198.51.100.1", "domain-name": "lab.example"}, \
        "a bare interface name is the tag; a subnet's own domain wins"
    assert _options(static) == {"routers": "203.0.113.254", "domain-name": "home.example"}, \
        "no router in dnsmasq: --router-host's address"
    assert _options(site) == {
        "ntp-servers": "192.0.2.123", "domain-search": "a.example, b.example",
        "classless-static-route": "192.0.2.0/24 - 192.0.2.1, 198.51.100.0/24 - 192.0.2.2"}


def test_hosts_become_reservations_by_mac_or_client_id(converted):
    site, _, _ = converted
    assert _subnet(site, "192.0.2.0/24")["reservations"] == [
        {"hw-address": "aa:bb:cc:dd:ee:01", "ip-address": "192.0.2.10", "hostname": "printer"},
        {"hw-address": "aa:bb:cc:dd:ee:02", "ip-address": "192.0.2.11", "hostname": "laptop"},
        {"client-id": "01:aa:bb", "ip-address": "192.0.2.12"},
        {"hw-address": "aa:bb:cc:dd:ee:03", "ip-address": "192.0.2.13", "hostname": "dual"},
        {"hw-address": "aa:bb:cc:dd:ee:04", "ip-address": "192.0.2.13", "hostname": "dual"},
    ]
    assert site["ip-reservations-unique"] is False, "one address for two MACs needs it"


def test_current_leases_are_listed_for_import(converted):
    _, leases, out = converted
    assert leases == [
        {"ip-address": "192.0.2.10", "hw-address": "aa:bb:cc:dd:ee:01", "subnet-id": 2, "valid-lft": 43200,
         "hostname": "printer", "client-id": "01:aa:bb:cc:dd:ee:01"},
        {"ip-address": "192.0.2.150", "hw-address": "aa:bb:cc:dd:ee:09", "subnet-id": 2, "valid-lft": 43200,
         "expire": leases[1]["expire"]},
    ]
    assert leases[1]["expire"] > time.time()
    assert "leases: 2 to import, 1 already expired, 1 outside the known subnets" in out


@pytest.mark.parametrize("check", [
    "CHECK: not converted: dhcp-boot=pxelinux.0",
    "CHECK: option for tags ['nope'] matches no subnet",
    "CHECK: vendor/encapsulated option not converted",
    "CHECK: 0.0.0.0 means 'this dnsmasq server'",
    "CHECK: option 252 has no standard Kea name",
    "CHECK: 'ignore' host (dnsmasq refuses it) needs a Kea DROP class",
    "CHECK: reservation by hostname only isn't possible in Kea",
    "CHECK: reservation without an address (name only)",
    "CHECK: address in no known subnet: dhcp-host=AA:BB:CC:DD:EE:07,198.18.0.9",
    "CHECK: wildcard MAC not supported by Kea reservations",
    "CHECK: 203.0.113.0/24: no router option in dnsmasq",
    "CHECK: no DNS server option for some subnets",
    "note: skipped (IPv6): dhcp-range=::1,::ff,constructor:eth0",
    "note: assumed /24 (no netmask given): 198.51.100.0/24",
    "note: skipped netmask",
    "note: per-host lease time not carried over",
    "note: host tags ['set:trusted'] not carried over",
])
def test_what_it_cant_convert_is_reported(converted, check):
    assert check in converted[2]


def test_a_second_non_24_subnet_is_numbered_from_1000(tmp_path, capsys):
    (tmp_path / "c.conf").write_text("dhcp-range=198.18.0.10,198.18.0.20,255.255.128.0\n"
                                     "dhcp-range=198.18.128.10,198.18.128.20,255.255.128.0\n"
                                     "dhcp-range=198.19.0.10,198.19.0.20,255.255.255.0\n", encoding="utf-8")
    m.main(["--conf", str(tmp_path / "c.conf"), "--out-dir", str(tmp_path / "o")])
    site = json.loads("{" + (tmp_path / "o" / "site.json").read_text(encoding="utf-8") + "}")
    assert [(s["subnet"], s["id"]) for s in site["subnet4"]] == [
        ("198.18.0.0/17", 1000), ("198.18.128.0/17", 1001), ("198.19.0.0/24", 1002)], \
        "a /24 whose third octet is 0 is numbered too"
    assert not (tmp_path / "o" / "leases-import.json").exists()


@pytest.mark.parametrize("text, seconds", [("infinite", 0xFFFFFFFF), ("90", 90), ("45m", 2700), ("12h", 43200),
                                           ("2d", 172800), ("1w", 604800), ("soon", None)])
def test_lease_times(text, seconds):
    assert m.lease_seconds(text) == seconds


def test_two_24s_with_one_third_octet_get_different_ids_and_static_never_pools(tmp_path):
    (tmp_path / "c.conf").write_text("dhcp-range=192.0.2.10,192.0.2.20,255.255.255.0\n"
                                     "dhcp-range=198.51.2.10,198.51.2.20,255.255.255.0\n"
                                     "dhcp-range=203.0.113.10,203.0.113.20,static\n", encoding="utf-8")
    m.main(["--conf", str(tmp_path / "c.conf"), "--out-dir", str(tmp_path / "o")])
    site = json.loads("{" + (tmp_path / "o" / "site.json").read_text(encoding="utf-8") + "}")
    assert [(s["subnet"], s["id"]) for s in site["subnet4"]] == [
        ("192.0.2.0/24", 2), ("198.51.2.0/24", 1000), ("203.0.113.0/24", 113)]
    assert "pools" not in _subnet(site, "203.0.113.0/24"), "a static range reserves only, even with an end"
