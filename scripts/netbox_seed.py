"""Seed NetBox from the homelab's live sources: old-queues/QUEUE-netbox.md.

Reads the networks from Kea (subnets and pools) and from Proxmox (each bridge's
addresses), the hosts and guests from Proxmox, and the Kubernetes cluster's
LoadBalancer addresses and MetalLB pools, then makes NetBox match: the site,
VLANs, prefixes and DHCP ranges; the Proxmox cluster with its hosts as devices
(ports, VLAN subinterfaces and bridges) and its guests as virtual machines (each
NIC with its MAC, VLAN and address, each disk a virtual disk); and each
LoadBalancer address (role VIP, described by the Services holding it) and each
MetalLB pool's range, so an address chosen from NetBox can't collide with one. One this
script recorded (its description starts "LoadBalancer " or "MetalLB pool ") that the
cluster no longer has is set Deprecated, never deleted; a later Service on that address
makes it active again. A range moved within a pool can't be recorded until the old one is
deleted by hand, since NetBox refuses overlapping ranges whatever their status.

Kea's reservations (every subnet's, from its config and its hosts database) each become an
address with status DHCP, described "Kea reservation <hostname> (<identifier>)". Its DNS
name is left alone: NetBox DNS would turn it into a record, and whether DHCP names go into
DNS is the owner's decision (QUEUE-kea-ha.md). An address something else already holds (a
device's or VM's interface, a VIP, any other description) is reported, never changed. One
this script recorded that Kea no longer reserves is set Deprecated, never deleted.

Each reservation on the IoT network (netbox_seed_iot.yaml) also becomes a device named after
its hostname, described "Kea reservation device": its kind and role from the first rule
matching the hostname, its wifi0 interface carrying the reservation's MAC and address. The
manufacturers, device types (with their interface and power-port templates) and roles it
uses come from the same file. The rule gives a device its starting type and role; NetBox
owns both after that, unless NETBOX_SEED_IOT_MODELS (else ~/.netbox-seed-iot-models.json),
a JSON object of hostname: device type slug, names its exact model. A device of the same
name not described so, or a MAC on another interface, is reported, never changed; one whose
reservation is gone is set Offline, never deleted. Its addresses get no DNS name, for the
reason above.

It leaves alone what NetBox owns: tags (Ansible's groups), config contexts (their
variables), each host's local context, and the devices Proxmox doesn't run (the
access points). A primary address NetBox already has is kept while the host
still has it. A VM whose role is one of netbox_seed.yaml's vm_roles has its shape
owned by NetBox (old-queues/QUEUE-netbox-shape.md): its vCPUs, memory, disk sizes and the
custom fields talos_apply_mode and proxmox_tags are never written, and where
Proxmox differs it's reported, since a resize made in NetBox reaches Proxmox
only when its pull request is merged and applied.

A VM whose status in NetBox is Decommissioning is being removed by hand and is
left exactly as it is: nothing about it or its addresses is written.

Each address on a device's or VM's interface whose prefix names a zone (the
prefix's custom field dns_zone) gets the DNS name <object name>.<zone>, from
which NetBox DNS makes its record (old-queues/QUEUE-netbox-dns.md); every other address's
DNS name is left as it is.

Names come from netbox_seed.yaml; no address is in this repo. A dry run (the
default) reports what it would create or change; --apply writes it. Safe to
run again: it finds each object by name (or address) and changes only what
differs. It deletes one kind of object only: a Proxmox node's interface that
Proxmox no longer has, and only with prune_node_interfaces on in
netbox_seed.yaml; off, it lists them. An interface with an address or a cable,
or that another interface still uses, is kept and reported.

Credentials are never arguments. Each comes from an environment variable when it's
set (Semaphore's variable group), else from a file in the home folder:
NETBOX_WRITE_TOKEN or ~/.netbox-token (a write-enabled NetBox token, "nbt_...";
never NETBOX_TOKEN, the inventory's read-only one), PROXMOX_TOKEN or
~/.proxmox-token (the full "PVEAPIToken=..." header; read-only is enough),
KEA_API or ~/.kea-api ("user:password"), K8S_TOKEN or ~/.k8s-token (the
semaphore ServiceAccount's token, which may list Services and MetalLB's pools:
kubernetes/prod/system/cluster-access/semaphore/), K8S_CA_B64 or ~/.k8s-ca-b64
(the cluster's CA certificate, PEM in base64 on one line). The URLs default to
NETBOX_API, PROXMOX_URL, KEA_URL and K8S_API_URL. The K8S_* values come from the
"Kubernetes API" variable group the Ansible jobs read too (ansible/README.md).
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

from ruamel.yaml import YAML


def read_secret(name: str, env: str) -> str:
    """One credential: the environment variable env when set, else the file name in the
    home folder, stripped."""
    value = os.environ.get(env, "").strip()
    if value:
        return value
    path = Path.home() / name
    if not path.is_file():
        raise SystemExit(f"FAIL: no credential: set {env}, or put it in ~/{name}")
    return path.read_text(encoding="utf-8").strip()


def http(method: str, url: str, headers: dict, body=None, insecure: bool = False, ca_pem: str | None = None):
    """JSON in, JSON out. Raises on any HTTP error, with the server's message. ca_pem
    trusts that CA alone (the Kubernetes API's own)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={**headers, "Content-Type": "application/json",
                                          "Accept": "application/json"})
    ctx = ssl._create_unverified_context() if insecure else (ssl.create_default_context(cadata=ca_pem) if ca_pem else None)
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"FAIL: {method} {url.split('?')[0]} answered {e.code}: {e.read()[:300]!r}")
    except (urllib.error.URLError, OSError) as e:
        raise SystemExit(f"FAIL: {method} {url.split('?')[0]}: no answer ({getattr(e, 'reason', e)})")


def slug(name: str) -> str:
    """NetBox's slug for a name: lower case, words joined by hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def disk_sizes(config: dict) -> dict[str, int]:
    """A Proxmox guest's disks by key (rootfs, mp*, scsi*, virtio*, sata*, ide*; CD
    drives and EFI or TPM state left out), each in NetBox's MB: Proxmox's G is
    1000 of them, so a size entered in NetBox as GB x 1000 reads back whole."""
    sizes = {}
    for key, value in config.items():
        if not re.match(r"^(rootfs|mp\d+|scsi\d+|virtio\d+|sata\d+|ide\d+)$", key):
            continue
        if "media=cdrom" in str(value):
            continue
        m = re.search(r"size=(\d+(?:\.\d+)?)([KMGT])", str(value))
        if m:
            sizes[key] = round(float(m.group(1)) * {"K": 1000 / 2**20, "M": 1000 / 1024, "G": 1000, "T": 1024000}[m.group(2)])
    return sizes


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

    def all(self, path: str, **params) -> list[dict]:
        """Every object of a kind, page by page."""
        q = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        url, out = f"{self.base}{path}/?{q}&limit=500", []
        while url:
            page = http("GET", url, self.headers)
            out += page["results"]
            url = page.get("next")
        return out

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

    def delete(self, path: str, obj: dict, label: str, allowed: bool):
        """Delete obj when allowed, reading it back as gone; otherwise only report it."""
        kind = path.split("/")[-1]
        if not allowed:
            print(f"  stale {kind}: {label} (prune_node_interfaces is off)")
            self._note("stale " + path)
            return
        print(f"  delete {kind}: {label}")
        self._note("delete " + path)
        if self.apply:
            http("DELETE", f"{self.base}{path}/{obj['id']}/", self.headers)
            if self.find(path, id=obj["id"]) is not None:
                raise SystemExit(f"FAIL: {label} is still in NetBox after its delete")

    def retire(self, path: str, obj: dict, label: str, gone_from: str = "the cluster", status: str = "deprecated"):
        """Set obj's status to status (Deprecated, or Offline for a device), never deleted (an
        address chosen from NetBox should still see it was in use), reading it back."""
        verb = "deprecate" if status == "deprecated" else f"set {status}"
        print(f"  {verb} {path.split('/')[-1]}: {label} (no longer in {gone_from})")
        self._note(f"{verb.replace(' ', '-')} {path}")
        if self.apply:
            http("PATCH", f"{self.base}{path}/{obj['id']}/", self.headers, {"status": status})
            back = self.find(path, id=obj["id"])
            if back is None or (back.get("status") or {}).get("value") != status:
                raise SystemExit(f"FAIL: {label} isn't {status} after its update")


def stale_load_balancers(addresses: list[dict], ranges: list[dict], lb_ips: dict[str, list[str]],
                         lb_pools: list[tuple[str, str, str]]) -> tuple[list[dict], list[dict]]:
    """The VIP addresses and IP ranges this script recorded for the cluster (their description
    starts "LoadBalancer " or "MetalLB pool ") that the cluster no longer has and that aren't
    Deprecated yet: an address no Service holds, a range no pool has (a single-address pool
    is recorded only as its address)."""
    def value(field):
        return field.get("value") if isinstance(field, dict) else field

    def plain(addr: str) -> str:
        return addr.split("/")[0]

    live_ranges = {(first, last) for _, first, last in lb_pools if first != last}
    old_ips = [a for a in addresses
               if (a.get("description") or "").startswith("LoadBalancer ") and value(a.get("role")) == "vip"
               and value(a.get("status")) != "deprecated" and plain(a["address"]) not in lb_ips]
    old_ranges = [r for r in ranges
                  if (r.get("description") or "").startswith("MetalLB pool ") and value(r.get("status")) != "deprecated"
                  and (plain(r["start_address"]), plain(r["end_address"])) not in live_ranges]
    return old_ips, old_ranges


def stale_interfaces(have: list[dict], names: set[str]) -> tuple[list[dict], list[tuple[dict, str]]]:
    """A node's NetBox interfaces that Proxmox no longer has: those to delete, each
    before any it points at (a VLAN before its port, a port before its bridge), and
    those kept, with why. One is kept while it has an address or a cable, or while an
    interface that stays has it as its parent, bridge or LAG."""
    gone = {i["id"]: i for i in have if i["name"] not in names}
    why = {}
    for i in gone.values():
        if i.get("count_ipaddresses"):
            why[i["id"]] = "it has an address"
        elif i.get("cable"):
            why[i["id"]] = "it has a cable"
    def refs(i: dict) -> list[int]:
        return [r["id"] for r in (i.get("parent"), i.get("bridge"), i.get("lag")) if isinstance(r, dict)]
    changed = True
    while changed:
        changed = False
        for i in have:
            if i["id"] in gone and i["id"] not in why:
                continue
            for r in refs(i):
                if r in gone and r not in why:
                    why[r] = f"{i['name']} still uses it"
                    changed = True
    left = [i for i in gone.values() if i["id"] not in why]
    order = []
    while left:
        pointed_at = {r for i in left for r in refs(i)}
        ready = sorted((i for i in left if i["id"] not in pointed_at), key=lambda i: i["name"])
        order += ready
        left = [i for i in left if i not in ready]
    kept = sorted(((gone[k], v) for k, v in why.items()), key=lambda p: p[0]["name"])
    return order, kept


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
    # Custom fields: only the ones we write are compared; NetBox returns them all.
    if isinstance(have, dict) and isinstance(want, dict):
        return all(same(have.get(k), v) for k, v in want.items())
    if isinstance(have, float) or isinstance(want, float):
        return have is not None and want is not None and float(have) == float(want)
    return have == want


def dns_label(name: str) -> str:
    """An object's name as a DNS label: lower case, anything but letters, digits and
    hyphens turned into hyphens."""
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")


def wanted_dns_names(addresses: list[dict], prefixes: list[dict], skip_vms: frozenset = frozenset(),
                     skip_devices: frozenset = frozenset()) -> dict[int, str]:
    """By address id, the DNS name each device's or VM's address should carry: its
    object's name in the zone of the most specific prefix holding it that names one.
    A VM in skip_vms or a device in skip_devices (by id) gets none."""
    import ipaddress
    zones = []
    for p in prefixes:
        zone = (p.get("custom_fields") or {}).get("dns_zone")
        if isinstance(zone, dict) and zone.get("name"):
            zones.append((ipaddress.ip_network(p["prefix"]), zone["name"]))
    out = {}
    for a in addresses:
        owner = a.get("assigned_object") or {}
        obj = owner.get("device") or owner.get("virtual_machine")
        if a.get("assigned_object_type") not in ("dcim.interface", "virtualization.vminterface") or not obj:
            continue
        if owner.get("virtual_machine") and obj.get("id") in skip_vms:
            continue
        if owner.get("device") and obj.get("id") in skip_devices:
            continue
        ip = ipaddress.ip_interface(a["address"]).ip
        held = [(net, z) for net, z in zones if ip in net]
        if held and dns_label(obj["name"]):
            zone = max(held, key=lambda h: h[0].prefixlen)[1]
            out[a["id"]] = f"{dns_label(obj['name'])}.{zone}"
    return out


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


# A host reservation's identifier, in the order Kea checks them.
KEA_IDENTIFIERS = ("hw-address", "client-id", "duid", "circuit-id", "flex-id")


def kea_reservations(url: str, cred: str, page_size: int = 100) -> dict[str, dict]:
    """Every IPv4 reservation Kea holds, by address: {identifier, hostname, subnet-id}. Read
    with host_cmds' reservation-get-page across all subnets and sources (its config, then
    its hosts database), copying each page's "next" into the following request until Kea
    answers result 3 (empty). Any other answer stops the run: a failed read is no answer."""
    auth = {"Authorization": "Basic " + base64.b64encode(cred.encode()).decode()}
    args: dict = {"limit": page_size}
    out: dict[str, dict] = {}
    for _ in range(10000):
        r = http("POST", url, auth, {"command": "reservation-get-page", "service": ["dhcp4"], "arguments": args})
        r = r[0] if isinstance(r, list) else r
        if r.get("result") == 3:
            return out
        if r.get("result") != 0:
            raise SystemExit(f"FAIL: Kea's reservation-get-page answered {r.get('result')}: {r.get('text')}")
        a = r.get("arguments") or {}
        for h in a.get("hosts") or []:
            ip = h.get("ip-address")
            if not ip or ip == "0.0.0.0":
                continue
            kind = next((k for k in KEA_IDENTIFIERS if h.get(k)), None)
            entry = {"identifier": f"{kind} {h[kind]}" if kind else "no identifier",
                     "hostname": (h.get("hostname") or "").strip(), "subnet-id": h.get("subnet-id")}
            if ip in out and out[ip] != entry:
                print(f"  note: {ip} is reserved twice in Kea; recording the first")
                continue
            out.setdefault(ip, entry)
        nxt = a.get("next")
        if not nxt:
            return out
        args = {"limit": page_size, "from": nxt.get("from", 0), "source-index": nxt.get("source-index", 0)}
    raise SystemExit("FAIL: Kea's reservation pages never ended (10000 pages)")


def reservation_label(r: dict) -> str:
    return "Kea reservation " + (f"{r['hostname']} ({r['identifier']})" if r["hostname"] else r["identifier"])


def plan_reservations(addresses: list[dict], reserved: dict[str, dict],
                      own_devices: frozenset = frozenset()) -> tuple[list[str], list[tuple[str, str]], list[dict]]:
    """What the reservations need in NetBox, given its addresses: the reserved addresses to
    record (new, or this script's own record of them), those something else holds (address,
    what holds it), and this script's records Kea no longer reserves. This script's own: its
    description starts "Kea reservation ", on no interface (or on one of own_devices, the
    IoT devices this script made, by id) and no VIP."""
    def value(field):
        return field.get("value") if isinstance(field, dict) else field

    def holder(a: dict) -> str | None:
        if a.get("assigned_object_id"):
            obj = a.get("assigned_object") or {}
            host = obj.get("device") or obj.get("virtual_machine") or {}
            if (obj.get("device") and host.get("id") in own_devices
                    and (a.get("description") or "").startswith("Kea reservation ")):
                return None
            return f"on {host.get('name') or 'an interface'}"
        if value(a.get("role")) == "vip":
            return "a VIP"
        if not (a.get("description") or "").startswith("Kea reservation "):
            return f"recorded as '{a.get('description') or 'no description'}'"
        return None

    by_ip = {a["address"].split("/")[0]: a for a in addresses}
    record, held = [], []
    for ip in sorted(reserved, key=lambda i: tuple(int(x) for x in i.split("."))):
        have = by_ip.get(ip)
        why = holder(have) if have else None
        if why:
            held.append((ip, why))
        else:
            record.append(ip)
    stale = [a for ip, a in by_ip.items()
             if ip not in reserved and holder(a) is None and value(a.get("status")) != "deprecated"]
    return record, held, stale


IOT_DEVICE_MARK = "Kea reservation device"


def iot_name(hostname: str) -> str | None:
    """What follows "<site code>iot" (or "<site code><area code>iot") in an IoT
    reservation's hostname (netbox_seed_iot.yaml), lower case, or None."""
    m = re.match(r"^[a-z]{2}(?:[a-z]{2})?iot(.+)$", hostname.lower())
    return m.group(1) if m else None


def iot_kind(hostname: str, rules: list[dict]) -> dict | None:
    """The first rule whose name pattern matches a reservation hostname's iot_name, or None."""
    name = iot_name(hostname)
    if name is None:
        return None
    return next((r for r in rules if re.search(r["name"], name)), None)


def plan_iot_devices(reserved: dict[str, dict], subnet: str, rules: list[dict],
                     models: dict[str, str]) -> tuple[list[dict], list[tuple[str, str]]]:
    """The devices the IoT network's reservations make: [{name, ip, mac, type, role}] for
    each reservation in subnet with a hostname and a hardware address, its type from models
    (a hostname's exact model, NETBOX_SEED_IOT_MODELS) else from its rule; and those it
    can't place (address, why)."""
    net = ipaddress.ip_network(subnet)
    devices, skipped = [], []
    for ip in sorted(reserved, key=lambda i: tuple(int(x) for x in i.split("."))):
        if ipaddress.ip_address(ip) not in net:
            continue
        r = reserved[ip]
        kind, _, ident = r["identifier"].partition(" ")
        if not r["hostname"]:
            skipped.append((ip, "no hostname"))
            continue
        if kind != "hw-address":
            skipped.append((ip, f"{r['hostname']}: no hardware address ({kind})"))
            continue
        rule = iot_kind(r["hostname"], rules)
        if rule is None:
            skipped.append((ip, f"{r['hostname']}: no rule in netbox_seed_iot.yaml matches"))
            continue
        devices.append({"name": r["hostname"], "ip": ip, "mac": ident.upper(),
                        "type": models.get(r["hostname"], rule["type"]), "role": rule["role"]})
    return devices, skipped


def stale_iot_devices(devices: list[dict], names: set[str]) -> list[dict]:
    """This script's IoT devices (described IOT_DEVICE_MARK) whose reservation is gone and
    that aren't Offline yet."""
    def value(field):
        return field.get("value") if isinstance(field, dict) else field
    return [d for d in devices if (d.get("description") or "") == IOT_DEVICE_MARK
            and d["name"] not in names and value(d.get("status")) != "offline"]


def iot_models() -> dict[str, str]:
    """Exact models known before a device is made, by reservation hostname: the JSON object
    in NETBOX_SEED_IOT_MODELS, else in ~/.netbox-seed-iot-models.json, else none. Hostnames
    stay out of this repo, so this is the one place they're named."""
    raw = os.environ.get("NETBOX_SEED_IOT_MODELS", "").strip()
    path = Path.home() / ".netbox-seed-iot-models.json"
    if not raw and path.is_file():
        raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    try:
        models = json.loads(raw)
    except ValueError as e:
        raise SystemExit(f"FAIL: NETBOX_SEED_IOT_MODELS isn't JSON: {e}")
    if not isinstance(models, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in models.items()):
        raise SystemExit("FAIL: NETBOX_SEED_IOT_MODELS must be a JSON object of hostname: device type slug")
    return models


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
        # Every interface the node has, up or down: what decides one is gone from it.
        names = {i["iface"] for i in get(f"/nodes/{n['node']}/network")}
        nodes[n["node"]] = {"bridges": bridges, "ifaces": ifaces, "names": names,
                            "maxcpu": n.get("maxcpu"), "status": n.get("status")}
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


def pool_ranges(pools: list[dict]) -> list[tuple[str, str, str]]:
    """Each MetalLB IPAddressPool's IPv4 ranges as (pool name, first, last): "a-b" as given,
    a CIDR as its first and last address."""
    out = []
    for p in pools:
        name = p["metadata"]["name"]
        for a in (p.get("spec") or {}).get("addresses") or []:
            if "-" in a:
                first, _, last = a.partition("-")
                first, last = ipaddress.ip_address(first.strip()), ipaddress.ip_address(last.strip())
            else:
                net = ipaddress.ip_network(a.strip(), strict=False)
                first, last = net[0], net[-1]
            if first.version == 4:
                out.append((name, str(first), str(last)))
    return sorted(out, key=lambda r: ipaddress.ip_address(r[1]))


def load_balancer_ips(services: list[dict]) -> dict[str, list[str]]:
    """Each address a LoadBalancer Service holds, with the Services holding it (namespace/name,
    sorted: MetalLB lets Services share one)."""
    out: dict[str, set[str]] = {}
    for s in services:
        if (s.get("spec") or {}).get("type") != "LoadBalancer":
            continue
        who = f"{s['metadata']['namespace']}/{s['metadata']['name']}"
        for i in ((s.get("status") or {}).get("loadBalancer") or {}).get("ingress") or []:
            if i.get("ip"):
                out.setdefault(i["ip"], set()).add(who)
    return {ip: sorted(who) for ip, who in sorted(out.items(), key=lambda kv: ipaddress.ip_address(kv[0]))}


def cluster_load_balancers(url: str, token: str, ca_b64: str):
    """The LoadBalancer addresses and MetalLB pool ranges, from the Kubernetes API."""
    try:
        ca_pem = base64.b64decode(re.sub(r"\s", "", ca_b64), validate=True).decode()
    except ValueError:
        raise SystemExit("FAIL: K8S_CA_B64 isn't base64 on one line")
    def get(p):
        return http("GET", url.rstrip("/") + p, {"Authorization": "Bearer " + token}, ca_pem=ca_pem)["items"]
    return load_balancer_ips(get("/api/v1/services")), pool_ranges(get("/apis/metallb.io/v1beta1/ipaddresspools"))


def unexpected(extras: list[str]) -> str:
    """The message for arguments the script doesn't take, naming each without its
    value: an argument can carry a credential (Semaphore passes a variable group's
    extra variables as NAME=value), and the message lands in a task log."""
    names = [a.split("=", 1)[0] if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", a) else "(a value)" for a in extras]
    return (f"FAIL: arguments this script doesn't take: {', '.join(names)} (values not shown). Credentials "
            "and URLs go in the environment: in Semaphore, the variable group's environment variables, "
            "not its extra variables.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(Path(__file__).with_suffix(".yaml")), help="the names (netbox_seed.yaml)")
    ap.add_argument("--iot-config", default=str(Path(__file__).with_name("netbox_seed_iot.yaml")),
                    help="the IoT devices' kinds and naming rules (netbox_seed_iot.yaml)")
    def url(name: str, env: str, example: str):
        ap.add_argument(name, default=os.environ.get(env) or None, required=not os.environ.get(env),
                        help=f"{example} (default: ${env})")
    url("--netbox-url", "NETBOX_API", "https://netbox.<domain>")
    url("--proxmox-url", "PROXMOX_URL", "https://<a Proxmox host>:8006")
    url("--kea-url", "KEA_URL", "Kea's control agent, e.g. http://127.0.0.1:8000/")
    url("--k8s-url", "K8S_API_URL", "the Kubernetes API, e.g. https://<address>:6443")
    ap.add_argument("--apply", action="store_true", help="write to NetBox (default: report only)")
    args, extras = ap.parse_known_args()
    if extras:
        raise SystemExit(unexpected(extras))

    cfg = YAML(typ="safe").load(Path(args.config).read_text(encoding="utf-8"))
    iot = YAML(typ="safe").load(Path(args.iot_config).read_text(encoding="utf-8"))
    models = iot_models()
    nb = NetBox(args.netbox_url, read_secret(".netbox-token", "NETBOX_WRITE_TOKEN"), args.apply)
    kea_subnets, kea_pools = kea_networks(args.kea_url, read_secret(".kea-api", "KEA_API"))
    kea_reserved = kea_reservations(args.kea_url, read_secret(".kea-api", "KEA_API"))
    cluster_name, nodes, guests = proxmox_state(args.proxmox_url, read_secret(".proxmox-token", "PROXMOX_TOKEN"))
    lb_ips, lb_pools = cluster_load_balancers(args.k8s_url, read_secret(".k8s-token", "K8S_TOKEN"),
                                              read_secret(".k8s-ca-b64", "K8S_CA_B64"))
    print(f"read: {len(kea_subnets)} Kea subnets, {len(kea_reserved)} Kea reservations, "
          f"{len(nodes)} Proxmox nodes, {len(guests)} guests, "
          f"{len(lb_ips)} LoadBalancer addresses, {len(lb_pools)} MetalLB ranges")
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

    # MetalLB's: each pool's range (a single address is only its address below), and each
    # address a Service holds, as a VIP named for the Services.
    print("load balancers:")
    old_ips, old_ranges = stale_load_balancers(nb.all("ipam/ip-addresses", role="vip"), nb.all("ipam/ip-ranges"),
                                               lb_ips, lb_pools)
    for r in old_ranges:
        nb.retire("ipam/ip-ranges", r, r["description"])
    for a in old_ips:
        nb.retire("ipam/ip-addresses", a, f"{a['address']} ({a['description']})")
    for pool, first, last in lb_pools:
        if first != last:
            plen = with_len(first).split("/")[1]
            nb.ensure("ipam/ip-ranges", {"start_address": f"{first}/{plen}"},
                      {"end_address": f"{last}/{plen}", "status": "active", "description": f"MetalLB pool {pool}"},
                      f"MetalLB pool {pool}")
    for ip, holders in lb_ips.items():
        nb.ensure("ipam/ip-addresses", {"address": with_len(ip)},
                  {"status": "active", "role": "vip", "description": "LoadBalancer " + ", ".join(holders)},
                  f"LoadBalancer {', '.join(holders)}")

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

    # Each VM's VMID, which OpenTofu finds it by (tofu/proxmox).
    nb.ensure("extras/custom-fields", {"name": "vmid"},
              {"type": "integer", "object_types": ["virtualization.virtualmachine"], "label": "VMID",
               "description": "The Proxmox VMID (netbox_seed.py)", "unique": True}, "custom field vmid")

    # The shape NetBox owns for the vm_roles' VMs, beyond vCPUs, memory and disks.
    modes = nb.ensure("extras/custom-field-choice-sets", {"name": "Talos apply modes"},
                      {"extra_choices": [["auto", "auto"], ["no_reboot", "no_reboot"]],
                       "description": "How OpenTofu applies a Talos node's config (tofu/proxmox)"}, "choice set Talos apply modes")
    nb.ensure("extras/custom-fields", {"name": "talos_apply_mode"},
              {"type": "select", "choice_set": modes["id"], "object_types": ["virtualization.virtualmachine"],
               "label": "Talos apply mode", "description": "auto, or no_reboot (old-queues/QUEUE-netbox-shape.md)"},
              "custom field talos_apply_mode")
    nb.ensure("extras/custom-fields", {"name": "proxmox_tags"},
              {"type": "text", "object_types": ["virtualization.virtualmachine"], "label": "Proxmox tags",
               "description": "A guest's Proxmox tags, separated by ; (old-queues/QUEUE-netbox-shape.md)"},
              "custom field proxmox_tags")
    vm_roles = {key: nb.ensure("dcim/device-roles", {"slug": slug(name)}, {"name": name, "vm_role": True}, name)
                for key, name in cfg["vm_roles"].items()}
    owned_roles = {r["id"] for r in vm_roles.values()}

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
    node_devs = {}
    for name in sorted(nodes):
        dev = nb.ensure("dcim/devices", {"name": name},
                        {"role": role["id"], "device_type": dtype["id"], "site": site["id"], "status": "active",
                         "cluster": cluster["id"]}, name)
        node_devs[name] = dev
        primary = node_interfaces(dev, name, nodes[name]["ifaces"], primary_of(dev))
        if primary:
            nb.set_ref("dcim/devices", dev, "primary_ip4", primary, name)

    # NetBox keeps an interface a node no longer has until it's deleted; with
    # prune_node_interfaces on, the sync deletes it, else it's listed here.
    print("stale node interfaces:")
    prune = bool(cfg.get("prune_node_interfaces"))
    for name, dev in sorted(node_devs.items()):
        if dev["id"] < 0:
            continue
        delete, kept = stale_interfaces(nb.all("dcim/interfaces", device_id=dev["id"]), nodes[name]["names"])
        for i, reason in kept:
            print(f"  KEEP {name} {i['name']}: not in Proxmox, but {reason}")
        for i in delete:
            nb.delete("dcim/interfaces", i, f"{name} {i['name']}", prune)

    def differ(name: str, pairs: dict):
        """Report where Proxmox differs from a shape NetBox owns; nothing is written."""
        for field, (netbox, proxmox) in pairs.items():
            if not same(netbox, proxmox):
                print(f"  NetBox owns {name} {field}: NetBox {netbox}, Proxmox {proxmox} (left as NetBox has it)")
                nb._note("differ owned/shape")

    print("virtual machines:")
    skip = set(cfg.get("skip_vmids") or [])
    retiring = nb.all("virtualization/virtual-machines", status="decommissioning")
    retiring_names = {v["name"] for v in retiring}
    for g in sorted(guests, key=lambda g: g["name"]):
        if g["vmid"] in skip:
            print(f"  skip {g['type']} {g['vmid']} (skip_vmids)")
            continue
        if g["name"] in retiring_names:
            print(f"  skip {g['type']} {g['vmid']} {g['name']} (Decommissioning in NetBox)")
            continue
        cfg_g = g["config"]
        shape = {"vcpus": int(cfg_g.get("cores", 1)) * int(cfg_g.get("sockets", 1)),
                 "memory": int(cfg_g.get("memory", 0))}
        fields = {"cluster": cluster["id"], "status": "active" if g.get("status") == "running" else "offline",
                  "site": site["id"], "device": node_devs[g["node"]]["id"], "custom_fields": {"vmid": int(g["vmid"])},
                  "description": f"Proxmox {g['type']} {g['vmid']}"}
        before = nb.find("virtualization/virtual-machines", name=g["name"])
        role = ((before or {}).get("role") or {}).get("id")
        owned = role in owned_roles
        if owned:
            differ(g["name"], {k: (before.get(k), v) for k, v in shape.items()})
        else:
            fields.update(shape)
        vm = nb.ensure("virtualization/virtual-machines", {"name": g["name"]}, fields, g["name"])
        for key, mb in sorted(disk_sizes(cfg_g).items()):
            have = nb.find("virtualization/virtual-disks", virtual_machine_id=vm["id"], name=key) if vm["id"] > 0 else None
            if have and owned:
                differ(g["name"], {f"disk {key} (MB)": (have.get("size"), mb)})
                continue
            nb.ensure("virtualization/virtual-disks", {"virtual_machine_id": vm["id"], "name": key},
                      {"virtual_machine": vm["id"], "size": mb}, f"{g['name']} {key}")
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

    # Kea's reservations, after the VMs, so an address one of them holds is seen as theirs.
    print("reservations:")

    def own_iot_devices() -> list[dict]:
        return [d for d in nb.all("dcim/devices", site_id=site["id"]) if (d.get("description") or "") == IOT_DEVICE_MARK]

    own_ids = frozenset(d["id"] for d in own_iot_devices()) if site["id"] > 0 else frozenset()
    record, held, stale = plan_reservations(nb.all("ipam/ip-addresses"), kea_reserved, own_ids)
    for a in stale:
        nb.retire("ipam/ip-addresses", a, f"{a['address']} ({a['description']})", "Kea")
    for ip, why in held:
        print(f"  held elsewhere: {ip}, reserved in Kea ({reservation_label(kea_reserved[ip])}), is {why}")
        nb._note("held-elsewhere ipam/ip-addresses")
    for ip in record:
        label = reservation_label(kea_reserved[ip])
        nb.ensure("ipam/ip-addresses", {"address": with_len(ip)}, {"status": "dhcp", "description": label}, label)

    # The IoT network's reservations as devices (netbox_seed_iot.yaml): each named after its
    # reservation, its kind from its hostname's rule (NetBox owns it once the device exists,
    # unless NETBOX_SEED_IOT_MODELS names it), its wifi0 with the MAC and the address.
    print("iot devices:")
    iot_subnet = next((s for s, p in prefixes.items() if p.get("description") == iot["network"]), None)
    if iot_subnet is None:
        print(f"  SKIP: no prefix for the {iot['network']} network, so no device is made or retired")
    else:
        devices, skipped = plan_iot_devices(kea_reserved, iot_subnet, iot["rules"], models)
        for ip, why in skipped:
            print(f"  no device: {ip} ({why})")
            nb._note("no-device dcim/devices")
        makers = {m["slug"]: nb.ensure("dcim/manufacturers", {"slug": m["slug"]}, {"name": m["name"]}, m["name"])
                  for m in iot["manufacturers"]}
        types, wifi_type = {}, {}
        for t in iot["device_types"]:
            # The templates first: NetBox gives a new device the components its type has then.
            dt = nb.ensure("dcim/device-types", {"slug": t["slug"]},
                           {"model": t["model"], "manufacturer": makers[t["manufacturer"]]["id"], "u_height": 0,
                            "description": t.get("description", "")}, t["model"])
            for i in t.get("interfaces", []):
                nb.ensure("dcim/interface-templates", {"device_type_id": dt["id"], "name": i["name"]},
                          {"device_type": dt["id"], "type": i["type"]}, f"{t['model']} {i['name']}")
            for p in t.get("power_ports", []):
                nb.ensure("dcim/power-port-templates", {"device_type_id": dt["id"], "name": p["name"]},
                          {"device_type": dt["id"], "type": p["type"]}, f"{t['model']} {p['name']}")
            types[t["slug"]] = dt
            wifi_type[t["slug"]] = next((i["type"] for i in t.get("interfaces", []) if i["name"] == "wifi0"), "ieee802.11n")
        roles = {r: nb.ensure("dcim/device-roles", {"slug": slug(r)}, {"name": r}, r) for r in iot["roles"]}
        for dev in stale_iot_devices(own_iot_devices() if site["id"] > 0 else [], {d["name"] for d in devices}):
            nb.retire("dcim/devices", dev, dev["name"], "Kea's reservations", status="offline")
        recorded = set(record)
        for d in devices:
            if d["type"] not in types:
                print(f"  no device: {d['ip']} ({d['name']}: device type {d['type']} isn't in netbox_seed_iot.yaml)")
                nb._note("no-device dcim/devices")
                continue
            have = nb.find("dcim/devices", name=d["name"], site_id=site["id"]) if site["id"] > 0 else None
            if have is not None and (have.get("description") or "") != IOT_DEVICE_MARK:
                print(f"  not this script's: device {d['name']} exists, described "
                      f"'{have.get('description') or 'nothing'}'; left as it is")
                nb._note("held-elsewhere dcim/devices")
                continue
            fields = {"site": site["id"], "status": "active", "description": IOT_DEVICE_MARK}
            if have is None:
                fields.update({"device_type": types[d["type"]]["id"], "role": roles[d["role"]]["id"]})
            elif d["name"] in models:
                fields["device_type"] = types[d["type"]]["id"]
            dev = nb.ensure("dcim/devices", {"name": d["name"], "site_id": site["id"]}, fields, d["name"])
            itf = nb.find("dcim/interfaces", device_id=dev["id"], name="wifi0") if dev["id"] > 0 else None
            if itf is None:
                itf = nb.ensure("dcim/interfaces", {"device_id": dev["id"], "name": "wifi0"},
                                {"device": dev["id"], "type": wifi_type[d["type"]]}, f"{d['name']} wifi0")
            # A MAC already on another interface isn't moved: that's someone else's record.
            had = nb.find("dcim/mac-addresses", mac_address=d["mac"])
            if had is not None and had.get("assigned_object_id") not in (None, itf["id"]):
                print(f"  held elsewhere: {d['name']}'s MAC is on another interface; left as it is")
                nb._note("held-elsewhere dcim/mac-addresses")
            else:
                mac = nb.ensure("dcim/mac-addresses", {"mac_address": d["mac"]},
                                {"assigned_object_type": "dcim.interface", "assigned_object_id": itf["id"]},
                                f"{d['name']} wifi0 MAC")
                nb.set_ref("dcim/interfaces", itf, "primary_mac_address", mac, f"{d['name']} wifi0")
            if d["ip"] not in recorded:
                print(f"  no address: {d['ip']} isn't this script's to assign to {d['name']} (see above)")
                continue
            label = reservation_label(kea_reserved[d["ip"]])
            ip = nb.ensure("ipam/ip-addresses", {"address": with_len(d["ip"])},
                           {"status": "dhcp", "description": label,
                            "assigned_object_type": "dcim.interface", "assigned_object_id": itf["id"]}, label)
            nb.set_ref("dcim/devices", dev, "primary_ip4", ip, d["name"])

    # Each device's and VM's addresses named in their prefix's zone; NetBox DNS's
    # DNSsync turns each name into the zone's record. Not the IoT devices': whether DHCP
    # names go into DNS is the owner's decision (QUEUE-kea-ha.md).
    print("dns names:")
    addresses = nb.all("ipam/ip-addresses")
    iot_ids = frozenset(d["id"] for d in own_iot_devices()) if site["id"] > 0 else frozenset()
    wanted = wanted_dns_names(addresses, nb.all("ipam/prefixes"), frozenset(v["id"] for v in retiring), iot_ids)
    for a in sorted(addresses, key=lambda a: a["address"]):
        name = wanted.get(a["id"])
        if name is not None and (a.get("dns_name") or "") != name:
            nb.ensure("ipam/ip-addresses", {"id": a["id"]}, {"dns_name": name}, f"{a['address']} dns_name")

    print("summary:", ", ".join(f"{k.split()[0]} {v} {k.split()[1].split('/')[-1]}" for k, v in sorted(nb.counts.items()))
          or "nothing to change")


if __name__ == "__main__":
    main()
