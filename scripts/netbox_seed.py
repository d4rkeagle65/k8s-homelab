"""Seed NetBox from the homelab's live sources: QUEUE-netbox.md.

Reads the networks from Kea (subnets and pools) and from Proxmox (each bridge's
addresses), and the hosts and guests from Proxmox, then makes NetBox match: the
site, VLANs, prefixes and DHCP ranges; the Proxmox cluster with its hosts as
devices (ports, VLAN subinterfaces and bridges) and its guests as virtual
machines (each NIC with its MAC, VLAN and address).

It leaves alone what NetBox owns: tags (Ansible's groups), config contexts (their
variables), each host's local context, and the devices Proxmox doesn't run (the
access points). A primary address NetBox already has is kept while the host
still has it.

Names come from netbox_seed.yaml; no address is in this repo. A dry run (the
default) reports what it would create or change; --apply writes it. Safe to
run again: it finds each object by name (or address) and changes only what
differs, and never deletes anything.

Credentials are files in the home folder, never arguments: ~/.netbox-token (a
write-enabled NetBox token, "Bearer nbt_..."), ~/.proxmox-token (the full
"PVEAPIToken=..." header), ~/.kea-api ("user:password").
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
    """{netN: [address/prefix]} from a guest's fixed addresses: a container's netN ip=,
    a VM's cloud-init ipconfigN ip= (dhcp left out)."""
    out = {}
    for key, value in config.items():
        m = re.match(r"^(?:net|ipconfig)(\d+)$", key)
        if m:
            a = re.search(r"\bip=(\d+\.\d+\.\d+\.\d+/\d+)", str(value))
            if a:
                out.setdefault("net" + m.group(1), []).append(a.group(1))
    return out


def mac_of(value: str) -> str | None:
    """A guest NIC's MAC address (netN's model=MAC or hwaddr=MAC), upper case as NetBox keeps it."""
    m = re.search(r"=([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\b", value)
    return m.group(1).upper() if m else None


def agent_on(value: str) -> bool:
    """Whether a VM's agent setting turns the guest agent on: "1,..." or "enabled=1,..."."""
    first = str(value or "").split(",")[0]
    return first in ("1", "enabled=1")


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

    def set_ref(self, path: str, obj: dict, field: str, target: dict, label: str):
        """Point obj's field (primary_ip4, primary_mac_address) at target, which has to
        exist first, since NetBox checks it's assigned to obj."""
        current = obj.get(field)
        current = current.get("id") if isinstance(current, dict) else current
        if current == target["id"] and target["id"] > 0:
            return
        print(f"  set {field}: {label}")
        self._note(f"set {field}/{path}")
        if self.apply and obj["id"] > 0 and target["id"] > 0:
            http("PATCH", f"{self.base}{path}/{obj['id']}/", self.headers, {field: target["id"]})

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
        # Only interfaces that are up: a node's unused ports (a USB Wi-Fi adapter) stay out.
        ifaces = [i for i in get(f"/nodes/{n['node']}/network") if i.get("active")]
        bridges = {i["iface"]: i.get("cidr") for i in ifaces if i["type"] == "bridge"}
        nodes[n["node"]] = {"bridges": bridges, "ifaces": ifaces, "maxcpu": n.get("maxcpu"), "status": n.get("status")}
    guests = []
    for r in get("/cluster/resources?type=vm"):
        cfg = get(f"/nodes/{r['node']}/{r['type']}/{r['vmid']}/config")
        ips = static_ips(cfg)
        if r["type"] == "qemu" and r.get("status") == "running" and agent_on(cfg.get("agent")):
            try:
                ifaces = get(f"/nodes/{r['node']}/qemu/{r['vmid']}/agent/network-get-interfaces")["result"]
            except SystemExit:
                ifaces = []
            macs = {mac_of(str(v)): k for k, v in cfg.items() if re.match(r"^net\d+$", k) and mac_of(str(v))}
            configured = set(ips)
            for i in ifaces:
                key = macs.get((i.get("hardware-address") or "").upper())
                # A NIC with a configured address keeps only that: what the agent adds
                # there is a floating address (Talos's API VIP), held by whichever node has it.
                if not key or key in configured:
                    continue
                for a in i.get("ip-addresses", []):
                    if a.get("ip-address-type") == "ipv4" and not a["ip-address"].startswith("127."):
                        ips.setdefault(key, []).append(f"{a['ip-address']}/{a['prefix']}")
        guests.append({**r, "config": cfg, "ips": ips,
                       "nets": {k: bridge_of(str(v)) for k, v in cfg.items() if re.match(r"^net\d+$", k)},
                       "macs": {k: mac_of(str(v)) for k, v in cfg.items() if re.match(r"^net\d+$", k)}})
    return cluster, nodes, guests


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(Path(__file__).with_suffix(".yaml")), help="the names (netbox_seed.yaml)")
    ap.add_argument("--netbox-url", required=True, help="https://netbox.<domain>")
    ap.add_argument("--proxmox-url", required=True, help="https://<a Proxmox host>:8006")
    ap.add_argument("--kea-url", required=True, help="Kea's control agent, e.g. http://127.0.0.1:8000/")
    ap.add_argument("--apply", action="store_true", help="write to NetBox (default: report only)")
    args = ap.parse_args()

    cfg = YAML(typ="safe").load(Path(args.config).read_text(encoding="utf-8"))
    nb = NetBox(args.netbox_url, read_secret(".netbox-token"), args.apply)
    kea_subnets, kea_pools = kea_networks(args.kea_url, read_secret(".kea-api"))
    cluster_name, nodes, guests = proxmox_state(args.proxmox_url, read_secret(".proxmox-token"))
    print(f"read: {len(kea_subnets)} Kea subnets, {len(nodes)} Proxmox nodes, {len(guests)} guests")
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
    vlans: dict[str, dict] = {}  # by bridge
    vlan_by_vid: dict[int, dict] = {}
    for net in cfg["networks"]:
        subnet = network_for(bridge_addrs.get(net["bridge"], []), kea_subnets)
        if not subnet:
            print(f"  SKIP {net['name']}: nothing on {net['bridge']} has an address to place it")
            continue
        vlan = None
        if net.get("vlan"):
            vlan = nb.ensure("ipam/vlans", {"vid": net["vlan"], "site_id": site["id"]},
                             {"name": net["name"], "site": site["id"], "status": "active"}, f"VLAN {net['vlan']} {net['name']}")
            vlans[net["bridge"]] = vlan_by_vid[net["vlan"]] = vlan
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

    print("kinds:")
    maker = nb.ensure("dcim/manufacturers", {"slug": "generic"}, {"name": "Generic"}, "Generic")
    spec = cfg["hypervisor"]
    role = nb.ensure("dcim/device-roles", {"slug": slug(spec["role"])}, {"name": spec["role"]}, spec["role"])
    dtype = nb.ensure("dcim/device-types", {"slug": slug(spec["type"])},
                      {"model": spec["type"], "manufacturer": maker["id"]}, spec["type"])
    ctype = nb.ensure("virtualization/cluster-types", {"slug": slug(cfg["cluster_type"])}, {"name": cfg["cluster_type"]}, cfg["cluster_type"])
    cluster = nb.ensure("virtualization/clusters", {"name": cluster_name},
                        {"type": ctype["id"], "scope_type": "dcim.site", "scope_id": site["id"], "status": "active"}, "the Proxmox cluster")

    def address(addr: str, assigned_type: str, assigned_id: int, label: str):
        return nb.ensure("ipam/ip-addresses", {"address": with_len(addr)},
                         {"status": "active", "assigned_object_type": assigned_type, "assigned_object_id": assigned_id},
                         label)

    def primary_of(obj: dict) -> str | None:
        """The address of NetBox's primary IPv4 for obj, without its length."""
        p = obj.get("primary_ip4")
        return p["address"].split("/")[0] if isinstance(p, dict) and p.get("address") else None

    def node_interfaces(dev: dict, name: str, ifaces: list[dict], wanted: str | None):
        """A Proxmox node's bridges, ports and VLAN subinterfaces as Proxmox has them:
        each port in its bridge, each VLAN under its port, each address on its bridge.
        The primary address stays the one NetBox has while the node still has it, else
        it's the management bridge's."""
        made: dict[str, dict] = {}
        primary = fallback = None
        # Bridges first, then the ports in them, then the VLANs on those ports.
        order = {"bridge": 0, "eth": 1, "bond": 1, "vlan": 2}
        members = {p: i["iface"] for i in ifaces for p in str(i.get("bridge_ports") or "").split() if i["type"] == "bridge"}
        for i in sorted(ifaces, key=lambda i: (order.get(i["type"], 3), i["iface"])):
            iname = i["iface"]
            fields = {"device": dev["id"], "type": {"bridge": "bridge", "eth": "other", "bond": "lag"}.get(i["type"], "virtual"),
                      "description": i.get("comments", "").strip()}
            if iname in members and members[iname] in made:
                fields["bridge"] = made[members[iname]]["id"]
            if i["type"] == "vlan":
                port, _, vid = iname.partition(".")
                if port in made:
                    fields["parent"] = made[port]["id"]
                fields["description"] = fields["description"] or f"VLAN {vid}"
                if int(vid) in vlan_by_vid:
                    fields.update(mode="access", untagged_vlan=vlan_by_vid[int(vid)]["id"])
            made[iname] = nb.ensure("dcim/interfaces", {"device_id": dev["id"], "name": iname}, fields, f"{name} {iname}")
            addr = i.get("cidr")
            if addr:
                ip = address(addr, "dcim.interface", made[iname]["id"], f"{name} {iname}")
                if wanted and addr.split("/")[0] == wanted:
                    primary = ip
                elif iname == "vmbr0":
                    fallback = ip
        return primary or fallback

    print("devices:")
    for name in sorted(nodes):
        dev = nb.ensure("dcim/devices", {"name": name},
                        {"role": role["id"], "device_type": dtype["id"], "site": site["id"], "status": "active",
                         "cluster": cluster["id"]}, name)
        primary = node_interfaces(dev, name, nodes[name]["ifaces"], primary_of(dev))
        if primary:
            nb.set_ref("dcim/devices", dev, "primary_ip4", primary, name)

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
                        "site": site["id"], "vcpus": vcpus, "memory": int(cfg_g.get("memory", 0)), "disk": disk_gb(cfg_g) * 1000,
                        "description": f"Proxmox {g['type']} {g['vmid']}"},
                       g["name"])
        wanted = primary_of(vm)
        primary = None
        for key in sorted(g["nets"]):
            bridge = g["nets"][key]
            vlan = vlans.get(bridge)
            # The VLAN is the bridge's: Proxmox tags on the host, so the guest's NIC is untagged.
            itf = nb.ensure("virtualization/interfaces", {"virtual_machine_id": vm["id"], "name": key},
                            {"virtual_machine": vm["id"], "description": f"Proxmox bridge {bridge}",
                             "mode": "access" if vlan else None, "untagged_vlan": vlan["id"] if vlan else None},
                            f"{g['name']} {key}")
            if g["macs"].get(key):
                mac = nb.ensure("dcim/mac-addresses", {"mac_address": g["macs"][key]},
                                {"assigned_object_type": "virtualization.vminterface", "assigned_object_id": itf["id"]},
                                f"{g['name']} {key} MAC")
                nb.set_ref("virtualization/interfaces", itf, "primary_mac_address", mac, f"{g['name']} {key}")
            for addr in g["ips"].get(key, []):
                ip = address(addr, "virtualization.vminterface", itf["id"], f"{g['name']} {key}")
                if primary is None or (wanted and addr.split("/")[0] == wanted):
                    primary = ip
        if primary:
            nb.set_ref("virtualization/virtual-machines", vm, "primary_ip4", primary, g["name"])

    print("summary:", ", ".join(f"{k.split()[0]} {v} {k.split()[1].split('/')[-1]}" for k, v in sorted(nb.counts.items()))
          or "nothing to change")


if __name__ == "__main__":
    main()
