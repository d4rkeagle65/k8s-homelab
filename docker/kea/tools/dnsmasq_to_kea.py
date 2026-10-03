#!/usr/bin/env python3
"""Convert a dnsmasq DHCP setup into Kea's site file and a lease import list.

Reads dnsmasq's config files (dhcp-range, dhcp-host, dhcp-option, domain)
and, optionally, its leases file, and writes into --out-dir:

  site.json           the members included at the end of kea-dhcp4.conf:
                      subnet4 (pools, options, reservations), global options
  leases-import.json  one lease4-add argument set per current lease, so
                      clients keep their addresses (see import_leases.py)

and prints a report of everything it couldn't convert, which needs a look
before Kea goes live. Nothing here knows the real network: every address
comes from the input files, which stay out of git, like the output.

Subnet IDs are the third octet for /24s (stable and readable), otherwise
numbered from 1000. A range without a netmask is taken as a /24 (dnsmasq
takes it from the interface; --prefix overrides).

Usage:
  python dnsmasq_to_kea.py --conf dnsmasq.conf [--conf more.conf ...]
         [--leases dnsmasq.leases] [--tag-subnet TAG=CIDR ...]
         [--prefix 24] [--router-host 254] --out-dir DIR
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# dnsmasq option names (dnsmasq --help dhcp) to option numbers.
DNSMASQ_OPTION_NAMES = {
    "netmask": 1, "time-offset": 2, "router": 3, "dns-server": 6,
    "log-server": 7, "lpr-server": 9, "hostname": 12, "boot-file-size": 13,
    "domain-name": 15, "swap-server": 16, "root-path": 17, "extension-path": 18,
    "ip-forward-enable": 19, "default-ttl": 23, "mtu": 26, "broadcast": 28,
    "router-discovery": 31, "static-route": 33, "arp-timeout": 35,
    "tcp-ttl": 37, "nis-domain": 40, "nis-server": 41, "ntp-server": 42,
    "vendor-encap": 43, "netbios-ns": 44, "netbios-dd": 45,
    "netbios-nodetype": 46, "netbios-scope": 47, "T1": 58, "T2": 59,
    "tftp-server": 66, "bootfile-name": 67, "smtp-server": 69,
    "domain-search": 119, "sip-server": 120, "classless-static-route": 121,
    "tftp-server-address": 150, "server-ip-address": 255,
}

# Option number to Kea's name, for the options Kea defines as standard.
KEA_OPTION_NAMES = {
    2: "time-offset", 3: "routers", 4: "time-servers", 6: "domain-name-servers",
    7: "log-servers", 9: "lpr-servers", 15: "domain-name", 16: "swap-server",
    17: "root-path", 18: "extensions-path", 19: "ip-forwarding",
    23: "default-ip-ttl", 26: "interface-mtu", 28: "broadcast-address",
    33: "static-routes", 35: "arp-cache-timeout", 37: "default-tcp-ttl",
    40: "nis-domain", 41: "nis-servers", 42: "ntp-servers",
    44: "netbios-name-servers", 45: "netbios-dd-server",
    46: "netbios-node-type", 47: "netbios-scope", 66: "tftp-server-name",
    67: "boot-file-name", 69: "smtp-server", 119: "domain-search",
    121: "classless-static-route",
}

# Options dnsmasq handles itself that have no Kea option to carry over.
SKIPPED_OPTIONS = {1: "netmask (Kea derives it from the subnet)",
                   12: "host-name (Kea sends reserved hostnames itself)",
                   58: "T1 (set renew-timer instead)",
                   59: "T2 (set rebind-timer instead)"}

TIME_RE = re.compile(r"^(\d+)([smhdw]?)$")
MAC_RE = re.compile(r"^(?:[0-9a-fA-F]{1,2}[:-]){5}[0-9a-fA-F]{1,2}$")
INFINITE = 0xFFFFFFFF


def lease_seconds(text: str) -> int | None:
    if text == "infinite":
        return INFINITE
    m = TIME_RE.match(text)
    if not m:
        return None
    return int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)]


def is_ipv4(text: str) -> bool:
    try:
        ipaddress.IPv4Address(text)
        return True
    except ValueError:
        return False


def normal_mac(text: str) -> str:
    return ":".join(part.zfill(2) for part in re.split("[:-]", text.lower()))


@dataclass
class Subnet:
    network: ipaddress.IPv4Network
    pools: list = field(default_factory=list)
    lifetime: int | None = None
    options: dict = field(default_factory=dict)      # code -> data text
    reservations: list = field(default_factory=list)
    domain: str | None = None
    static_only: bool = False

    @property
    def subnet_id(self) -> int:
        return int(self.network.network_address.packed[2]) if self.network.prefixlen == 24 else 0


class Converter:
    def __init__(self, prefix: int, router_host: int, tag_subnets: dict):
        self.prefix = prefix
        self.router_host = router_host
        self.subnets: dict[ipaddress.IPv4Network, Subnet] = {}
        self.tag_subnets: dict[str, set] = {t: {n} for t, n in tag_subnets.items()}
        self.global_options: dict[int, str] = {}
        self.global_domain: str | None = None
        self.notes: list[str] = []      # needs attention
        self.info: list[str] = []       # converted, worth knowing
        self.pending_options: list = []  # (tags, code, data, line) until tags resolve
        self.hosts: list = []            # parsed dhcp-host entries

    # -- reading ---------------------------------------------------------
    def read(self, path: Path) -> None:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = (s.strip() for s in line.split("=", 1))
            handler = {
                "dhcp-range": self.dhcp_range, "dhcp-host": self.dhcp_host,
                "dhcp-option": self.dhcp_option, "dhcp-option-force": self.dhcp_option,
                "domain": self.domain,
            }.get(key)
            if handler:
                handler(value, line)
            elif key.startswith("dhcp-") or key in ("conf-file", "conf-dir", "addn-hosts"):
                self.notes.append(f"not converted: {line}")

    def subnet_for(self, net: ipaddress.IPv4Network) -> Subnet:
        if net not in self.subnets:
            self.subnets[net] = Subnet(net)
        return self.subnets[net]

    def subnet_containing(self, ip: ipaddress.IPv4Address) -> Subnet | None:
        for net, sub in self.subnets.items():
            if ip in net:
                return sub
        return None

    def dhcp_range(self, value: str, line: str) -> None:
        parts = [p.strip() for p in value.split(",")]
        # Older syntax: a leading bare name (or net:name) sets that tag, as
        # set: does, e.g. dhcp-range=eth0,<start>,<end>,12h.
        if parts and not is_ipv4(parts[0]) and not parts[0].startswith(("set:", "tag:")):
            name = parts[0][4:] if parts[0].startswith("net:") else parts[0]
            if ":" not in name:  # not an IPv6 start address
                parts[0] = "set:" + name
        sets = [p[4:] for p in parts if p.startswith("set:")]
        matches = [p[4:] for p in parts if p.startswith("tag:")]
        rest = [p for p in parts if not p.startswith(("set:", "tag:"))]
        if rest and ":" in rest[0] and not is_ipv4(rest[0]):
            self.info.append(f"skipped (IPv6): {line}")
            return
        if not rest or not is_ipv4(rest[0]):
            self.notes.append(f"not understood: {line}")
            return
        start = ipaddress.IPv4Address(rest[0])
        end, mask, lifetime, static = None, None, None, False
        for p in rest[1:]:
            if p in ("static", "proxy"):
                static = p == "static"
                if p == "proxy":
                    self.notes.append(f"proxy-DHCP range not converted: {line}")
                    return
            elif is_ipv4(p):
                if end is None and mask is None and not p.startswith("255."):
                    end = ipaddress.IPv4Address(p)
                elif mask is None:
                    mask = p
                # a fourth address is the broadcast; Kea derives it
            elif lease_seconds(p) is not None:
                lifetime = lease_seconds(p)
            else:
                self.notes.append(f"unknown part {p!r} in: {line}")
        if mask:
            net = ipaddress.IPv4Network(f"{start}/{mask}", strict=False)
        else:
            net = ipaddress.IPv4Network(f"{start}/{self.prefix}", strict=False)
            self.info.append(f"assumed /{self.prefix} (no netmask given): {net}")
        sub = self.subnet_for(net)
        if static or end is None:
            sub.static_only = sub.static_only or not sub.pools
            self.info.append(f"{net}: reservations only (static), no pool")
        else:
            sub.pools.append(f"{start} - {end}")
        if lifetime is not None:
            sub.lifetime = lifetime
        for tag in sets:
            self.tag_subnets.setdefault(tag, set()).add(net)
        if matches:
            self.notes.append(f"{net}: range limited to clients tagged {matches}; "
                              f"Kea needs a client class for that: {line}")

    def dhcp_host(self, value: str, line: str) -> None:
        macs, client_id, ip, name, lifetime, tags, ignore = [], None, None, None, None, [], False
        for p in (s.strip() for s in value.split(",")):
            if not p:
                continue
            if p.startswith(("set:", "tag:")):
                tags.append(p)
            elif p.startswith("id:"):
                client_id = p[3:]
            elif p == "ignore":
                ignore = True
            elif MAC_RE.match(p):
                macs.append(normal_mac(p))
            elif "*" in p and ":" in p:
                self.notes.append(f"wildcard MAC not supported by Kea reservations: {line}")
                return
            elif is_ipv4(p):
                ip = ipaddress.IPv4Address(p)
            elif lease_seconds(p) is not None:
                lifetime = lease_seconds(p)
            elif p.startswith("[") or ":" in p:
                self.info.append(f"IPv6 part ignored: {line}")
            else:
                name = p
        self.hosts.append((macs, client_id, ip, name, lifetime, tags, ignore, line))

    def dhcp_option(self, value: str, line: str) -> None:
        parts = [p.strip() for p in value.split(",")]
        tags = []
        while parts:
            head = parts[0]
            if head.startswith(("encap:", "vi-encap:", "vendor:")):
                self.notes.append(f"vendor/encapsulated option not converted: {line}")
                return
            if head.startswith(("tag:", "net:")):
                tags.append(head[4:])
            elif not head.isdigit() and not head.startswith(("option:", "option6:")):
                # Older syntax: a leading bare name matches that tag, as
                # tag: does, e.g. dhcp-option=eth0,option:router,<address>.
                tags.append(head)
            else:
                break
            parts.pop(0)
        if not parts:
            return
        opt = parts.pop(0)
        if opt.startswith("option6:"):
            return
        if opt.startswith("option:"):
            code = DNSMASQ_OPTION_NAMES.get(opt[7:])
            if code is None:
                self.notes.append(f"unknown option name: {line}")
                return
        elif opt.isdigit():
            code = int(opt)
        else:
            self.notes.append(f"not understood: {line}")
            return
        self.pending_options.append((tags, code, parts, line))

    def domain(self, value: str, line: str) -> None:
        parts = [p.strip() for p in value.split(",")]
        name = parts[0]
        if len(parts) == 1:
            self.global_domain = name
            return
        target = parts[1]
        try:
            if "/" in target:
                net = ipaddress.IPv4Network(target, strict=False)
            elif len(parts) >= 3 and is_ipv4(parts[2]):
                net = ipaddress.IPv4Network(f"{target}/{self.prefix}", strict=False)
            else:
                raise ValueError
        except ValueError:
            self.notes.append(f"domain line not understood: {line}")
            return
        self.subnet_for(net).domain = name

    # -- resolving -------------------------------------------------------
    def option_data(self, code: int, values: list, line: str) -> str | None:
        if code in SKIPPED_OPTIONS:
            self.info.append(f"skipped {SKIPPED_OPTIONS[code]}: {line}")
            return None
        if not values or values == [""]:
            self.notes.append(f"option {code} sent empty (dnsmasq: 'don't send it'); "
                              f"Kea just won't send it unless configured: {line}")
            return None
        if "0.0.0.0" in values:
            self.notes.append(f"0.0.0.0 means 'this dnsmasq server' and has no Kea "
                              f"equivalent; set the real address: {line}")
            return None
        if code in (121, 33):
            if len(values) % 2:
                self.notes.append(f"odd number of route values: {line}")
                return None
            return ", ".join(f"{values[i]} - {values[i + 1]}" for i in range(0, len(values), 2))
        return ", ".join(values)

    def resolve(self) -> None:
        for tags, code, values, line in self.pending_options:
            data = self.option_data(code, values, line)
            if data is None:
                continue
            if code not in KEA_OPTION_NAMES:
                self.notes.append(f"option {code} has no standard Kea name; needs an "
                                  f"option-def: {line}")
                continue
            if not tags:
                self.global_options[code] = data
                continue
            nets = set()
            for tag in tags:
                nets |= self.tag_subnets.get(tag, set())
            if not nets:
                self.notes.append(f"option for tags {tags} matches no subnet (give "
                                  f"--tag-subnet TAG=CIDR if it's an interface name): {line}")
                continue
            for net in nets:
                self.subnet_for(net).options[code] = data

        ip_count: dict = {}
        for macs, client_id, ip, name, lifetime, tags, ignore, line in self.hosts:
            if ignore:
                self.notes.append(f"'ignore' host (dnsmasq refuses it) needs a Kea DROP "
                                  f"class: {line}")
                continue
            if not macs and not client_id:
                self.notes.append(f"reservation by hostname only isn't possible in Kea: {line}")
                continue
            if ip is None:
                self.notes.append(f"reservation without an address (name only); add it as "
                                  f"a global reservation by hand if needed: {line}")
                continue
            sub = self.subnet_containing(ip)
            if sub is None:
                self.notes.append(f"address in no known subnet: {line}")
                continue
            if lifetime is not None:
                self.info.append(f"per-host lease time not carried over: {line}")
            if tags:
                self.info.append(f"host tags {tags} not carried over: {line}")
            idents = [("hw-address", m) for m in macs] or [("client-id", client_id)]
            for kind, ident in idents:
                res = {kind: ident, "ip-address": str(ip)}
                if name:
                    res["hostname"] = name
                sub.reservations.append(res)
                ip_count[ip] = ip_count.get(ip, 0) + 1
        self.shared_ips = sorted(str(ip) for ip, n in ip_count.items() if n > 1)
        if self.shared_ips:
            self.info.append(f"addresses reserved for more than one MAC (allowed with "
                             f"ip-reservations-unique false): {', '.join(self.shared_ips)}")

    # -- writing ---------------------------------------------------------
    def site(self) -> dict:
        next_id = 1000
        used: set = set()
        subnets = []
        for net in sorted(self.subnets, key=lambda n: (n.network_address, n.prefixlen)):
            sub = self.subnets[net]
            sid = sub.subnet_id
            if not sid or sid in used:
                sid, next_id = next_id, next_id + 1
            used.add(sid)
            options = dict(sub.options)
            if 3 not in options and 3 not in self.global_options:
                router = net.network_address + self.router_host
                options[3] = str(router)
                self.notes.append(f"{net}: no router option in dnsmasq (it sends its own "
                                  f"address there); using {router}. Check it's the gateway.")
            if 15 not in options and 15 not in self.global_options:
                domain = sub.domain or self.global_domain
                if domain:
                    options[15] = domain
            entry = {"id": sid, "subnet": str(net)}
            if sub.pools:
                entry["pools"] = [{"pool": p} for p in sub.pools]
            if sub.lifetime:
                entry["valid-lifetime"] = sub.lifetime
            if options:
                entry["option-data"] = [{"name": KEA_OPTION_NAMES[c], "data": d}
                                        for c, d in sorted(options.items())]
            if sub.reservations:
                entry["reservations"] = sorted(sub.reservations, key=lambda r: ipaddress.IPv4Address(r["ip-address"]))
            subnets.append(entry)
        if 6 not in self.global_options and not all(
                any(o["name"] == "domain-name-servers" for o in s.get("option-data", [])) for s in subnets):
            self.notes.append("no DNS server option for some subnets (dnsmasq sends "
                              "its own address by default); set domain-name-servers")
        site = {
            # dnsmasq's default lease time is one hour.
            "valid-lifetime": 3600,
            "option-data": [{"name": KEA_OPTION_NAMES[c], "data": d}
                            for c, d in sorted(self.global_options.items())],
            "subnet4": subnets,
        }
        if self.shared_ips:
            site["ip-reservations-unique"] = False
        return site

    def leases(self, path: Path, ids: dict) -> list:
        now = int(time.time())
        out, expired, unknown = [], 0, 0
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            f = raw.split()
            if len(f) < 4 or f[0] == "duid" or not is_ipv4(f[2]):
                continue
            expiry, mac, ip = int(f[0]), f[1], ipaddress.IPv4Address(f[2])
            if expiry and expiry < now:
                expired += 1
                continue
            sub = self.subnet_containing(ip)
            if sub is None or not MAC_RE.match(mac):
                unknown += 1
                continue
            lease = {"ip-address": str(ip), "hw-address": normal_mac(mac),
                     "subnet-id": ids[sub.network]}
            lifetime = sub.lifetime or 3600
            lease["valid-lft"] = lifetime if lifetime != INFINITE else INFINITE
            if expiry:
                lease["expire"] = expiry
            if f[3] != "*":
                lease["hostname"] = f[3]
            if len(f) > 4 and f[4] != "*":
                lease["client-id"] = f[4]
            out.append(lease)
        self.info.append(f"leases: {len(out)} to import, {expired} already expired, "
                         f"{unknown} outside the known subnets")
        return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--conf", action="append", required=True, type=Path)
    ap.add_argument("--leases", type=Path)
    ap.add_argument("--tag-subnet", action="append", default=[], metavar="TAG=CIDR")
    ap.add_argument("--prefix", type=int, default=24)
    ap.add_argument("--router-host", type=int, default=254,
                    help="host number used as the router when dnsmasq sets none")
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args(argv)

    tag_subnets = {}
    for item in args.tag_subnet:
        tag, cidr = item.split("=", 1)
        tag_subnets[tag] = ipaddress.IPv4Network(cidr, strict=False)

    conv = Converter(args.prefix, args.router_host, tag_subnets)
    for path in args.conf:
        conv.read(path)
    conv.resolve()
    site = conv.site()
    ids = {ipaddress.IPv4Network(s["subnet"]): s["id"] for s in site["subnet4"]}

    args.out_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(site, indent=2)
    # Kea includes this inside its "Dhcp4" object: members only, no braces.
    (args.out_dir / "site.json").write_text(text[1:-1].strip("\n") + "\n", encoding="utf-8")
    if args.leases:
        leases = conv.leases(args.leases, ids)
        (args.out_dir / "leases-import.json").write_text(json.dumps(leases, indent=2) + "\n", encoding="utf-8")

    print(f"subnets: {len(site['subnet4'])}, reservations: "
          f"{sum(len(s.get('reservations', [])) for s in site['subnet4'])}, "
          f"global options: {len(site['option-data'])}")
    for s in site["subnet4"]:
        print(f"  id {s['id']:>4}  {s['subnet']:<18} pools {len(s.get('pools', []))}  "
              f"reservations {len(s.get('reservations', []))}  "
              f"options {[o['name'] for o in s.get('option-data', [])]}")
    for line in conv.info:
        print(f"note: {line}")
    for line in conv.notes:
        print(f"CHECK: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
