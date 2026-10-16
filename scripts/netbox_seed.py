"""Seed NetBox from the homelab's live sources: QUEUE-netbox.md, phase 2.

Reads the networks from Kea (subnets and pools) and from Proxmox (each bridge's
addresses), the hosts and guests from Proxmox, and Semaphore's static
inventories, then makes NetBox match: the site, VLANs, prefixes and DHCP
ranges; the Proxmox cluster with its hosts as devices and its guests as
virtual machines, their interfaces and addresses; the inventories' other hosts
as devices; and each inventory group as a tag on its hosts, with the group's
variables as a config context on that tag, so Ansible's NetBox inventory can
rebuild the groups.

Names come from netbox_seed.yaml; no address is in this repo. A dry run (the
default) reports what it would create or change; --apply writes it. Safe to
run again: it finds each object by name (or address) and changes only what
differs, and never deletes anything.

Credentials are files in the home folder, never arguments: ~/.netbox-token (a
write-enabled NetBox token, "Bearer nbt_..."), ~/.proxmox-token (the full
"PVEAPIToken=..." header), ~/.kea-api ("user:password"), ~/.semaphore-token.
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML


def read_secret(name: str) -> str:
    """One credential file from the home folder, stripped."""
    return (Path.home() / name).read_text(encoding="utf-8").strip()


def http(method: str, url: str, headers: dict, body=None, insecure: bool = False):
    """JSON in, JSON out. Raises on any HTTP error, with the server's message."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={**headers, "Content-Type": "application/json",
                                          "Accept": "application/json"})
    ctx = ssl._create_unverified_context() if insecure else None
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"FAIL: {method} {url.split('?')[0]} answered {e.code}: {e.read()[:300]!r}")


def slug(name: str) -> str:
    """NetBox's slug for a name: lower case, words joined by hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def disk_gb(config: dict) -> int:
    """A Proxmox guest's disks in GB, summed: rootfs, mp*, scsi*, virtio*, sata*, ide*
    (CD drives and EFI or TPM state left out)."""
    total = 0.0
    for key, value in config.items():
        if not re.match(r"^(rootfs|mp\d+|scsi\d+|virtio\d+|sata\d+|ide\d+)$", key):
            continue
        if "media=cdrom" in str(value):
            continue
        m = re.search(r"size=(\d+(?:\.\d+)?)([KMGT])", str(value))
        if m:
            total += float(m.group(1)) * {"K": 1 / 2**20, "M": 1 / 1024, "G": 1, "T": 1024}[m.group(2)]
    return round(total)


def static_ips(config: dict) -> dict[str, list[str]]:
    """{netN: [address/prefix]} from a container's ip= settings (dhcp left out)."""
    out = {}
    for key, value in config.items():
        if re.match(r"^net\d+$", key):
            m = re.search(r"\bip=(\d+\.\d+\.\d+\.\d+/\d+)", str(value))
            if m:
                out[key] = [m.group(1)]
    return out


def bridge_of(value: str) -> str | None:
    m = re.search(r"\bbridge=(\w+)", value)
    return m.group(1) if m else None


def network_for(bridge_addresses: list[str], kea_subnets: list[str]) -> str | None:
    """A bridge's subnet: the Kea subnet holding its addresses, else the network of the
    first address with a prefix length (a host's own interface)."""
    for addr in bridge_addresses:
        ip = ipaddress.ip_interface(addr).ip if "/" in addr else ipaddress.ip_address(addr)
        for subnet in kea_subnets:
            if ip in ipaddress.ip_network(subnet):
                return subnet
    for addr in bridge_addresses:
        if "/" in addr:
            return str(ipaddress.ip_interface(addr).network)
    return None


class NetBox:
    """Find-or-create and update, reporting each change; writes only with apply."""

    def __init__(self, url: str, token: str, apply: bool):
        self.base = url.rstrip("/") + "/api/"
        self.headers = {"Authorization": token if token.startswith("Bearer ") else "Bearer " + token}
        self.apply = apply
        self.counts: dict[str, int] = {}
        self.fake = 0

    def _note(self, what: str):
        self.counts[what] = self.counts.get(what, 0) + 1

    def find(self, path: str, **params):
        q = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        res = http("GET", f"{self.base}{path}/?{q}&limit=2", self.headers)["results"]
        if len(res) > 1:
            raise SystemExit(f"FAIL: more than one {path} matches {params}")
        return res[0] if res else None

    def set_primary(self, path: str, obj: dict, ip: dict, label: str):
        """Make ip the object's primary IPv4 address."""
        current = (obj.get("primary_ip4") or {}).get("id") if isinstance(obj.get("primary_ip4"), dict) else obj.get("primary_ip4")
        if current == ip["id"] and ip["id"] > 0:
            return
        print(f"  set primary address: {label}")
        self._note("set primary_ip4/" + path)
        if self.apply and obj["id"] > 0 and ip["id"] > 0:
            http("PATCH", f"{self.base}{path}/{obj['id']}/", self.headers, {"primary_ip4": ip["id"]})

    def ensure(self, path: str, lookup: dict, fields: dict, label: str):
        """The object matching lookup, made or updated to carry fields. In a dry run an
        object it would create comes back as a placeholder with a negative id."""
        # A lookup by an object this dry run would only create can't match anything.
        pending = any(isinstance(v, int) and v < 0 for v in lookup.values())
        have = None if pending else self.find(path, **lookup)
        want = {**lookup, **fields} if have is None else fields
        if have is None:
            print(f"  create {path.split('/')[-1]}: {label}")
            self._note("create " + path)
            if not self.apply:
                self.fake -= 1
                return {"id": self.fake, **want}
            return http("POST", self.base + path + "/", self.headers, want)
        change = {k: v for k, v in fields.items() if not same(have.get(k), v)}
        if change:
            print(f"  update {path.split('/')[-1]}: {label} ({', '.join(sorted(change))})")
            self._note("update " + path)
            if self.apply and have["id"] > 0:
                return http("PATCH", f"{self.base}{path}/{have['id']}/", self.headers, change)
        return have


def same(have, want) -> bool:
    """A NetBox field's value against the one we'd write: a nested object by id, a
    choice by value, tags by slug, lists in any order."""
    if isinstance(have, dict) and "id" in have and not isinstance(want, dict):
        return have["id"] == want
    if isinstance(have, dict) and "value" in have:
        return have["value"] == want
    if isinstance(have, list) and isinstance(want, list):
        def key(x):
            return x.get("slug", x.get("id")) if isinstance(x, dict) else x
        return sorted(map(str, map(key, have))) == sorted(map(str, map(key, want)))
    if isinstance(have, float) or isinstance(want, float):
        return have is not None and want is not None and float(have) == float(want)
    return have == want


def kea_networks(url: str, cred: str):
    """Kea's subnets, and its pools as (first, last) per subnet."""
    auth = {"Authorization": "Basic " + base64.b64encode(cred.encode()).decode()}
    r = http("POST", url, auth, {"command": "config-get", "service": ["dhcp4"]})
    r = r[0] if isinstance(r, list) else r
    if r.get("result") != 0:
        raise SystemExit(f"FAIL: Kea's config-get answered {r.get('result')}: {r.get('text')}")
    d = r["arguments"]["Dhcp4"]
    subnets = d.get("subnet4", []) + [s for n in d.get("shared-networks", []) for s in n.get("subnet4", [])]
    pools = {}
    for s in subnets:
        for p in s.get("pools", []):
            first, _, last = p["pool"].partition("-")
            pools.setdefault(s["subnet"], []).append((first.strip(), last.strip()))
    return [s["subnet"] for s in subnets], pools


def proxmox_state(url: str, token: str):
    """The cluster's name, its nodes (with each bridge's address), and its guests (with
    their config and addresses per interface)."""
    def get(p):
        return http("GET", url.rstrip("/") + "/api2/json" + p, {"Authorization": token}, insecure=True)["data"]
    cluster = next((c["name"] for c in get("/cluster/status") if c.get("type") == "cluster"), None)
    nodes = {}
    for n in get("/nodes"):
        bridges = {i["iface"]: i.get("cidr") for i in get(f"/nodes/{n['node']}/network") if i["type"] == "bridge"}
        nodes[n["node"]] = {"bridges": bridges, "maxcpu": n.get("maxcpu"), "status": n.get("status")}
    guests = []
    for r in get("/cluster/resources?type=vm"):
        cfg = get(f"/nodes/{r['node']}/{r['type']}/{r['vmid']}/config")
        ips = static_ips(cfg) if r["type"] == "lxc" else {}
        if r["type"] == "qemu" and r.get("status") == "running" and cfg.get("agent", "").startswith("1"):
            try:
                ifaces = get(f"/nodes/{r['node']}/qemu/{r['vmid']}/agent/network-get-interfaces")["result"]
            except SystemExit:
                ifaces = []
            macs = {re.search(r"=([0-9A-Fa-f:]{17})", str(v)).group(1).lower(): k
                    for k, v in cfg.items() if re.match(r"^net\d+$", k) and re.search(r"=([0-9A-Fa-f:]{17})", str(v))}
            for i in ifaces:
                key = macs.get((i.get("hardware-address") or "").lower())
                for a in i.get("ip-addresses", []):
                    if key and a.get("ip-address-type") == "ipv4" and not a["ip-address"].startswith("127."):
                        ips.setdefault(key, []).append(f"{a['ip-address']}/{a['prefix']}")
        guests.append({**r, "config": cfg, "ips": ips,
                       "nets": {k: bridge_of(str(v)) for k, v in cfg.items() if re.match(r"^net\d+$", k)}})
    return cluster, nodes, guests


def semaphore_inventories(url: str, token: str, wanted: dict):
    """{tag: {"hosts": {name: ansible_host}, "vars": {...}}} from the named static YAML
    inventories."""
    inv = http("GET", url.rstrip("/") + "/api/project/1/inventory", {"Authorization": "Bearer " + token})
    out = {}
    for i in inv:
        tag = wanted.get(i["name"])
        if not tag:
            continue
        doc = YAML(typ="safe").load(StringIO(i["inventory"])) or {}
        for group in doc.values():
            hosts = {h: str((v or {}).get("ansible_host", h)) for h, v in ((group or {}).get("hosts") or {}).items()}
            out[tag] = {"hosts": hosts, "vars": dict((group or {}).get("vars") or {})}
    missing = sorted(set(wanted.values()) - set(out))
    if missing:
        raise SystemExit(f"FAIL: Semaphore has no inventory for {missing}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(Path(__file__).with_suffix(".yaml")), help="the names (netbox_seed.yaml)")
    ap.add_argument("--netbox-url", required=True, help="https://netbox.<domain>")
    ap.add_argument("--proxmox-url", required=True, help="https://<a Proxmox host>:8006")
    ap.add_argument("--kea-url", required=True, help="Kea's control agent, e.g. http://127.0.0.1:8000/")
    ap.add_argument("--semaphore-url", required=True, help="https://semaphore.<domain>")
    ap.add_argument("--apply", action="store_true", help="write to NetBox (default: report only)")
    args = ap.parse_args()

    cfg = YAML(typ="safe").load(Path(args.config).read_text(encoding="utf-8"))
    nb = NetBox(args.netbox_url, read_secret(".netbox-token"), args.apply)
    kea_subnets, kea_pools = kea_networks(args.kea_url, read_secret(".kea-api"))
    cluster_name, nodes, guests = proxmox_state(args.proxmox_url, read_secret(".proxmox-token"))
    inventories = semaphore_inventories(args.semaphore_url, read_secret(".semaphore-token"), cfg["inventories"])
    print(f"read: {len(kea_subnets)} Kea subnets, {len(nodes)} Proxmox nodes, {len(guests)} guests, "
          f"{sum(len(i['hosts']) for i in inventories.values())} inventory hosts")
    print("dry run: nothing is written" if not args.apply else "applying")

    site = nb.ensure("dcim/sites", {"slug": slug(cfg["site"])}, {"name": cfg["site"], "status": "active"}, cfg["site"])

    print("networks:")
    bridge_addrs: dict[str, list[str]] = {}
    for n in nodes.values():
        for br, cidr in n["bridges"].items():
            if cidr:
                bridge_addrs.setdefault(br, []).append(cidr)
    for g in guests:
        if g["vmid"] in set(cfg.get("skip_vmids") or []):
            continue
        for key, addrs in g["ips"].items():
            br = g["nets"].get(key)
            if br:
                bridge_addrs.setdefault(br, []).extend(addrs)
    prefixes = {}
    for net in cfg["networks"]:
        subnet = network_for(bridge_addrs.get(net["bridge"], []), kea_subnets)
        if not subnet:
            print(f"  SKIP {net['name']}: nothing on {net['bridge']} has an address to place it")
            continue
        vlan = None
        if net.get("vlan"):
            vlan = nb.ensure("ipam/vlans", {"vid": net["vlan"], "site_id": site["id"]},
                             {"name": net["name"], "site": site["id"], "status": "active"}, f"VLAN {net['vlan']} {net['name']}")
        # NetBox 4.2+ ties a prefix to its site through a scope; a site field is ignored.
        fields = {"scope_type": "dcim.site", "scope_id": site["id"], "status": "active",
                  "description": net["name"], "vlan": vlan["id"] if vlan else None}
        prefixes[subnet] = nb.ensure("ipam/prefixes", {"prefix": subnet}, fields, net["name"])
        for first, last in kea_pools.get(subnet, []):
            plen = subnet.split("/")[1]
            nb.ensure("ipam/ip-ranges", {"start_address": f"{first}/{plen}"},
                      {"end_address": f"{last}/{plen}", "status": "active", "description": f"{net['name']}: Kea DHCP pool"},
                      f"{net['name']} DHCP pool")

    def with_len(addr: str) -> str:
        """An address with its network's prefix length (NetBox stores it so)."""
        ip = ipaddress.ip_address(addr.split("/")[0])
        for subnet in prefixes:
            if ip in ipaddress.ip_network(subnet):
                return f"{ip}/{subnet.split('/')[1]}"
        return addr if "/" in addr else f"{ip}/32"

    print("tags and their variables:")
    tags = {}
    for tag, inv in inventories.items():
        tags[tag] = nb.ensure("extras/tags", {"slug": tag}, {"name": tag}, tag)
        nb.ensure("extras/config-contexts", {"name": f"ansible: {tag}"},
                  {"weight": 1000, "data": inv["vars"], "tags": [tag], "description": f"Semaphore's {tag} group variables"},
                  f"ansible: {tag}")
    host_tags: dict[str, list[str]] = {}
    host_addr: dict[str, str] = {}
    for tag, inv in inventories.items():
        for host, addr in inv["hosts"].items():
            short = host.split(".")[0]
            host_tags.setdefault(short, []).append(tag)
            host_addr[short] = addr

    print("kinds:")
    maker = nb.ensure("dcim/manufacturers", {"slug": "generic"}, {"name": "Generic"}, "Generic")
    kinds = {}
    for spec in [cfg["hypervisor"], *cfg["devices"].values()]:
        role = nb.ensure("dcim/device-roles", {"slug": slug(spec["role"])}, {"name": spec["role"]}, spec["role"])
        dtype = nb.ensure("dcim/device-types", {"slug": slug(spec["type"])},
                          {"model": spec["type"], "manufacturer": maker["id"]}, spec["type"])
        kinds[spec["role"]] = (role, dtype)
    ctype = nb.ensure("virtualization/cluster-types", {"slug": slug(cfg["cluster_type"])}, {"name": cfg["cluster_type"]}, cfg["cluster_type"])
    cluster = nb.ensure("virtualization/clusters", {"name": cluster_name},
                        {"type": ctype["id"], "scope_type": "dcim.site", "scope_id": site["id"], "status": "active"}, "the Proxmox cluster")

    def address(addr: str, assigned_type: str, assigned_id: int, label: str):
        return nb.ensure("ipam/ip-addresses", {"address": with_len(addr)},
                         {"status": "active", "assigned_object_type": assigned_type, "assigned_object_id": assigned_id},
                         label)

    print("devices:")
    role, dtype = kinds[cfg["hypervisor"]["role"]]
    hosts = [(name, role, dtype, cluster["id"], {"vmbr0": host_addr.get(name) or (nodes[name]["bridges"].get("vmbr0") or "")})
             for name in sorted(nodes)]
    for tag, spec in cfg["devices"].items():
        r, t = kinds[spec["role"]]
        hosts += [(h.split(".")[0], r, t, None, {"management": a}) for h, a in sorted(inventories[tag]["hosts"].items())]
    for name, r, t, cl, ifaces in hosts:
        fields = {"role": r["id"], "device_type": t["id"], "site": site["id"], "status": "active",
                  "tags": [{"slug": t} for t in host_tags.get(name, [])]}
        if cl:
            fields["cluster"] = cl
        dev = nb.ensure("dcim/devices", {"name": name}, fields, name)
        primary = None
        for iname, addr in ifaces.items():
            if not addr:
                continue
            itf = nb.ensure("dcim/interfaces", {"device_id": dev["id"], "name": iname},
                            {"device": dev["id"], "type": "virtual"}, f"{name} {iname}")
            primary = address(addr, "dcim.interface", itf["id"], f"{name} {iname}")
        if primary:
            nb.set_primary("dcim/devices", dev, primary, name)

    print("virtual machines:")
    skip = set(cfg.get("skip_vmids") or [])
    for g in sorted(guests, key=lambda g: g["name"]):
        if g["vmid"] in skip:
            print(f"  skip {g['type']} {g['vmid']} (skip_vmids)")
            continue
        cfg_g = g["config"]
        vcpus = int(cfg_g.get("cores", 1)) * int(cfg_g.get("sockets", 1))
        vm = nb.ensure("virtualization/virtual-machines", {"name": g["name"]},
                       {"cluster": cluster["id"], "status": "active" if g.get("status") == "running" else "offline",
                        "vcpus": vcpus, "memory": int(cfg_g.get("memory", 0)), "disk": disk_gb(cfg_g) * 1000,
                        "tags": [{"slug": t} for t in host_tags.get(g["name"], [])],
                        "description": f"Proxmox {g['type']} {g['vmid']}"},
                       g["name"])
        wanted = host_addr.get(g["name"])
        primary = None
        for key in sorted(g["nets"]):
            itf = nb.ensure("virtualization/interfaces", {"virtual_machine_id": vm["id"], "name": key},
                            {"virtual_machine": vm["id"]}, f"{g['name']} {key}")
            for addr in g["ips"].get(key, []):
                ip = address(addr, "virtualization.vminterface", itf["id"], f"{g['name']} {key}")
                if primary is None or (wanted and addr.split("/")[0] == wanted):
                    primary = ip
        if primary:
            nb.set_primary("virtualization/virtual-machines", vm, primary, g["name"])

    print("summary:", ", ".join(f"{k.split()[0]} {v} {k.split()[1].split('/')[-1]}" for k, v in sorted(nb.counts.items()))
          or "nothing to change")


if __name__ == "__main__":
    main()
