"""Termix's SSH hosts from NetBox (QUEUE-bastion.md, step 6).

The hosts are NetBox's devices and VMs that the playbooks give the web bastion's key: tagged
debian (the owner's admin account, TERMIX_SSH_ADMIN_USER), openwrt_aps (root), synology
(TERMIX_SSH_SYNOLOGY_USER) or nas without synology, the TerraMaster
(TERMIX_SSH_TERRAMASTER_USER). Each is reached by its DNS name on the management network
(the address NetBox holds in the prefix described "Management"; a host with none there,
such as the travel router on its own LAN, by its primary address), on port 22 unless NetBox
records an SSH service on another port, in a folder named for its NetBox role.

The sync owns only the hosts it made, which carry the tag "netbox" in Termix: it creates
the ones new in NetBox, updates the ones that differ, and deletes the ones that left it.
A host made by hand is never touched. A host whose username isn't set is reported and
skipped, never made with another user.

DRY_RUN (default "true") reads both sides and prints the plan, and the field names of
Termix's host and credential records (never their values), and changes nothing. This
version only plans: the writes come once those fields are read. Exit 0 when it ran, 2 when
it couldn't answer (NetBox or Termix unreadable).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

NETBOX = os.environ["NETBOX_URL"].rstrip("/")
TERMIX = os.environ.get("TERMIX_URL", "http://termix:8080").rstrip("/")
DRY_RUN = os.environ.get("DRY_RUN", "true").strip().lower() != "false"
OWNED_TAG = "netbox"

# NetBox tag -> where its username comes from: an environment variable, or a fixed name.
ACCOUNTS = [
    ("debian", {"env": "TERMIX_SSH_ADMIN_USER"}),
    ("openwrt_aps", {"name": "root"}),
    ("synology", {"env": "TERMIX_SSH_SYNOLOGY_USER"}),
    ("nas", {"env": "TERMIX_SSH_TERRAMASTER_USER"}),  # a nas not tagged synology
]


def fail(msg: str) -> None:
    print(f"COULD NOT ANSWER: {msg}")
    sys.exit(2)


def get(url: str, headers: dict):
    req = urllib.request.Request(url, headers={**headers, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def netbox_all(path: str) -> list[dict]:
    """Every page of a NetBox list."""
    token = os.environ["NETBOX_TOKEN"].strip()
    headers = {"Authorization": "Bearer " + (token[7:] if token.startswith("Bearer ") else token)}
    out, url = [], f"{NETBOX}/api/{path}{'&' if '?' in path else '?'}limit=200"
    while url:
        page = get(url, headers)
        out += page["results"]
        url = page.get("next")
    return out


def username_for(tags: set[str]) -> tuple[str | None, str]:
    """The login for a host with these tags, and where it came from (for the report)."""
    for tag, source in ACCOUNTS:
        if tag == "nas" and "synology" in tags:
            continue
        if tag in tags:
            if "name" in source:
                return source["name"], tag
            value = os.environ.get(source["env"], "").strip()
            return (value or None), f"{tag} ({source['env']}{'' if value else ' not set'})"
    return None, "no login tag"


def wanted_hosts(objects: list[dict], addresses: list[dict], prefixes: list[dict],
                 services: list[dict]) -> tuple[dict[str, dict], list[str]]:
    """{name: host} from NetBox, and the hosts skipped with why."""
    import ipaddress
    mgmt = [ipaddress.ip_network(p["prefix"]) for p in prefixes if (p.get("description") or "").strip() == "Management"]
    by_owner: dict[tuple[str, int], list[dict]] = {}
    for a in addresses:
        kind = a.get("assigned_object_type") or ""
        obj = a.get("assigned_object") or {}
        parent = obj.get("device") or obj.get("virtual_machine") or {}
        if parent.get("id"):
            by_owner.setdefault(("vm" if "virtualization" in kind else "device", parent["id"]), []).append(a)
    ssh_ports = {}
    for s in services:
        if re.search(r"\bssh\b", s.get("name", ""), re.I) and s.get("ports"):
            parent = s.get("parent") or s.get("device") or s.get("virtual_machine") or {}
            if parent.get("id"):
                ssh_ports[(s.get("parent_object_type") or "", parent["id"])] = s["ports"][0]
    hosts, skipped = {}, []
    for o in objects:
        tags = {t["slug"] for t in o.get("tags", [])}
        user, source = username_for(tags)
        if source == "no login tag":
            continue
        name = o["name"]
        if not user:
            skipped.append(f"{name}: username not set ({source})")
            continue
        found = None
        for a in by_owner.get((o["_kind"], o["id"]), []):
            ip = ipaddress.ip_interface(a["address"]).ip
            if any(ip in n for n in mgmt):
                found = a.get("dns_name") or str(ip)
                break
        via = "management network"
        if not found and (o.get("primary_ip4") or {}).get("address"):
            primary = o["primary_ip4"]
            found = primary.get("dns_name") or str(ipaddress.ip_interface(primary["address"]).ip)
            via = "primary address"
        if not found:
            skipped.append(f"{name}: no address on the management network, and no primary address, in NetBox")
            continue
        port = next((p for (t, i), p in ssh_ports.items() if i == o["id"] and o["_kind"] in t), 22)
        hosts[name] = {"name": name, "ip": found, "port": port, "username": user,
                       "folder": (o.get("role") or {}).get("name") or "Other", "login": source, "via": via}
    return hosts, skipped


def main() -> int:
    try:
        objects = [dict(d, _kind="device") for d in netbox_all("dcim/devices/?status=active")]
        objects += [dict(v, _kind="vm") for v in netbox_all("virtualization/virtual-machines/?status=active")]
        addresses = netbox_all("ipam/ip-addresses/?")
        prefixes = netbox_all("ipam/prefixes/?")
        services = netbox_all("ipam/services/?")
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        fail(f"NetBox: {getattr(e, 'code', None) or e}")
    wanted, skipped = wanted_hosts(objects, addresses, prefixes, services)

    headers = {"Authorization": "Bearer " + os.environ["TERMIX_API_KEY"].strip()}
    try:
        hosts = get(f"{TERMIX}/host/db/host", headers)
        creds = get(f"{TERMIX}/credentials", headers)
    except (urllib.error.URLError, OSError, ValueError) as e:
        fail(f"Termix: {getattr(e, 'code', None) or getattr(e, 'reason', None) or e}")
    hosts = hosts if isinstance(hosts, list) else hosts.get("hosts") or hosts.get("data") or []
    creds = creds if isinstance(creds, list) else creds.get("credentials") or creds.get("data") or []

    # The record shapes, for the write version: field names only, never values.
    if hosts:
        print("termix host fields:", sorted(hosts[0]))
    print("termix credentials:", len(creds), "| fields:", sorted(creds[0]) if creds else "-")

    def tags_of(h):
        t = h.get("tags")
        return set(t if isinstance(t, list) else str(t or "").replace(",", " ").split())
    owned = {h.get("name"): h for h in hosts if OWNED_TAG in tags_of(h)}
    by_hand = sorted(h.get("name") for h in hosts if OWNED_TAG not in tags_of(h))

    print(f"NetBox: {len(wanted)} host(s) to have; Termix: {len(owned)} synced, {len(by_hand)} made by hand (left alone: {by_hand})")
    for name, h in sorted(wanted.items()):
        have = owned.get(name)
        if not have:
            print(f"  {'would create' if DRY_RUN else 'create'} {name}: port {h['port']}, by its {h['via']}, as {h['login']}, in {h['folder']}")
        else:
            diff = [k for k in ("ip", "port", "username", "folder") if str(have.get(k)) != str(h[k])]
            if diff:
                print(f"  {'would update' if DRY_RUN else 'update'} {name}: {', '.join(diff)}")
    for name in sorted(set(owned) - set(wanted)):
        print(f"  {'would delete' if DRY_RUN else 'delete'} {name}: no longer in NetBox")
    for s in skipped:
        print(f"  skipped {s}")
    if not DRY_RUN:
        print("This version only plans; the writes come once the fields above are read.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
