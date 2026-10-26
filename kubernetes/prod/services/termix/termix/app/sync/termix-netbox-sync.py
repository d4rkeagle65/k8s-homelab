"""Termix's SSH and RDP hosts from NetBox (QUEUE-bastion.md, step 6).

The hosts are NetBox's devices and VMs that the playbooks give the web bastion's key: tagged
debian (the owner's admin account, TERMIX_SSH_ADMIN_USER), openwrt_aps (root), synology
(TERMIX_SSH_SYNOLOGY_USER), nas without synology, the TerraMaster
(TERMIX_SSH_TERRAMASTER_USER), or bastion-key, a device given the key by hand (a switch, the
firewall), as the termix_user in its NetBox local context. Each is reached by its DNS name on the management network
(the address NetBox holds in the prefix described "Management"; a host with none there,
such as the travel router on its own LAN, by its primary address), on port 22 unless NetBox
records an SSH service on another port, in a folder named for its NetBox role.

A host tagged rdp (the Windows DC) is a remote desktop host instead: on port 3389 unless
NetBox records an RDP service, with RDP turned on in Termix's remote-desktop plugin (its
host settings, a call of their own), and the credential its local context names in
termix_credential, a password one with its own username; it has no default.

Every host logs in with a Termix credential made by hand in Termix's UI, so the private key
never leaves Termix: the bastion key, named by TERMIX_CREDENTIAL (default "bastion"), unless
the host's NetBox local context names another in termix_credential (the RSA bastion key, for
a device that takes no Ed25519 key). The host's own username overrides the credential's. A
host naming a credential Termix doesn't have exactly once is reported and skipped; one the
sync made before is left as it is, never deleted for it. The sync never writes a credential.

The sync owns only the hosts it made, which carry the tag "netbox" in Termix: it creates
the ones new in NetBox, updates the ones that differ, and deletes the ones that left it.
An update keeps what the user set on the host (pin, notes, status checks) and changes only
the fields above. A host made by hand is never touched. A host whose username isn't set is
reported and skipped, never made with another user.

DRY_RUN (default "true") reads both sides, prints the plan and changes nothing. Otherwise it
writes, then reads Termix back and checks every host it owns matches NetBox. Exit 0 when it
ran (and the read-back matched), 1 when a write failed or didn't take, 2 when it couldn't
answer: NetBox or Termix unreadable, the credential missing, or NetBox naming no host at all
(which would delete every synced host).
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
CREDENTIAL = os.environ.get("TERMIX_CREDENTIAL", "bastion").strip()
# The fields the sync owns on a host it made; the rest stay as the user left them.
SYNCED = ("ip", "port", "username", "folder")
SYNCED_RDP = ("ip", "port", "folder")
RDP_PLUGIN = "remote-desktop"

# NetBox tag -> where its username comes from: an environment variable, a fixed name, or
# a key of the host's NetBox local context.
ACCOUNTS = [
    ("debian", {"env": "TERMIX_SSH_ADMIN_USER"}),
    ("openwrt_aps", {"name": "root"}),
    ("synology", {"env": "TERMIX_SSH_SYNOLOGY_USER"}),
    ("nas", {"env": "TERMIX_SSH_TERRAMASTER_USER"}),  # a nas not tagged synology
    ("bastion-key", {"context": "termix_user"}),
]


def fail(msg: str) -> None:
    print(f"COULD NOT ANSWER: {msg}")
    sys.exit(2)


def get(url: str, headers: dict):
    req = urllib.request.Request(url, headers={**headers, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def send(method: str, url: str, headers: dict, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={**headers, "Accept": "application/json", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw.strip() else None


def tags_of(h: dict) -> set[str]:
    t = h.get("tags")
    return set(t if isinstance(t, list) else str(t or "").replace(",", " ").split())


def listed(payload, *keys) -> list[dict]:
    """A Termix list, whether bare or wrapped."""
    if isinstance(payload, list):
        return payload
    for k in keys:
        if isinstance(payload.get(k), list):
            return payload[k]
    return []


def is_rdp(want: dict) -> bool:
    return want.get("protocol") == "rdp"


def host_body(want: dict, credential_id, have: dict | None = None) -> dict:
    """The body for a create, or for an update of `have`: Termix's PUT replaces the whole
    record, so an update starts from what Termix holds and overlays the synced fields."""
    body = dict(have or {}, statusCheckEnabled=True)
    if have:  # what the user set stays
        body["statusCheckEnabled"] = bool(have.get("statusCheckEnabled", True))
        body["tags"] = sorted(tags_of(have) | {OWNED_TAG})
    else:
        body["tags"] = [OWNED_TAG]
    if is_rdp(want):
        # The login is the RDP protocol's (protocolAuth), the credential's own username; the
        # SSH side has none.
        body.update({k: want[k] for k in SYNCED_RDP})
        body.update({"name": want["name"], "connectionType": "rdp", "enableSsh": False, "authType": "none",
                     "credentialId": None, "overrideCredentialUsername": False, "username": "",
                     "protocolAuth": {"rdp": {"authType": "credential", "credentialId": credential_id}}})
        return body
    body.update({k: want[k] for k in SYNCED})
    body.update({"name": want["name"], "connectionType": "ssh", "authType": "credential",
                 "credentialId": credential_id, "overrideCredentialUsername": True, "enableSsh": True})
    return body


def rdp_settings(want: dict) -> dict:
    """The remote-desktop plugin's host settings an RDP host needs."""
    return {"enableRdp": True, "rdpPort": want["port"]}


def rdp_settings_differ(have: dict, want: dict) -> bool:
    current = ((have.get("pluginSettings") or {}).get(RDP_PLUGIN)) or {}
    return any(str(current.get(k)).lower() != str(v).lower() for k, v in rdp_settings(want).items())


def differs(have: dict, want: dict, credential_id) -> list[str]:
    """What of the host record differs (the RDP plugin's settings are rdp_settings_differ's)."""
    if is_rdp(want):
        diff = [k for k in SYNCED_RDP if str(have.get(k) or "") != str(want[k])]
        if have.get("connectionType") != "rdp":
            diff.append("connection type")
        login = ((have.get("protocolAuth") or {}).get("rdp")) or {}
        if login.get("authType") != "credential" or str(login.get("credentialId")) != str(credential_id):
            diff.append("credential")
        return diff
    diff = [k for k in SYNCED if str(have.get(k) or "") != str(want[k])]
    if have.get("authType") != "credential" or str(have.get("credentialId")) != str(credential_id):
        diff.append("credential")
    if not have.get("overrideCredentialUsername"):
        diff.append("username override")
    return diff


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


def username_for(tags: set[str], context: dict | None = None) -> tuple[str | None, str]:
    """The login for a host with these tags and NetBox local context, and where it came
    from (for the report)."""
    for tag, source in ACCOUNTS:
        if tag == "nas" and "synology" in tags:
            continue
        if tag in tags:
            if "name" in source:
                return source["name"], tag
            if "context" in source:
                value = str((context or {}).get(source["context"]) or "").strip()
                return (value or None), f"{tag} (local context {source['context']}{'' if value else ' not set'})"
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
    ports: dict[str, dict] = {"ssh": {}, "rdp": {}}
    for s in services:
        for proto in ports:
            if re.search(rf"\b{proto}\b", s.get("name", ""), re.I) and s.get("ports"):
                parent = s.get("parent") or s.get("device") or s.get("virtual_machine") or {}
                if parent.get("id"):
                    ports[proto][(s.get("parent_object_type") or "", parent["id"])] = s["ports"][0]
    hosts, skipped = {}, []
    for o in objects:
        tags = {t["slug"] for t in o.get("tags", [])}
        context = o.get("local_context_data") or {}
        name = o["name"]
        protocol = "rdp" if "rdp" in tags else "ssh"
        if protocol == "rdp":
            user, source = "", "rdp (its credential's username)"
            if not str(context.get("termix_credential") or "").strip():
                skipped.append(f"{name}: tagged rdp, but its local context names no termix_credential")
                continue
        else:
            user, source = username_for(tags, context)
        if source == "no login tag":
            continue
        if protocol == "ssh" and not user:
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
        default_port = 3389 if protocol == "rdp" else 22
        port = next((p for (t, i), p in ports[protocol].items() if i == o["id"] and o["_kind"] in t), default_port)
        credential = str(context.get("termix_credential") or "").strip() or CREDENTIAL
        hosts[name] = {"name": name, "ip": found, "port": port, "username": user,
                       "folder": (o.get("role") or {}).get("name") or "Other", "login": source, "via": via,
                       "credential": credential, "protocol": protocol}
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

    if not wanted:
        fail("NetBox names no host to sync; refusing to plan deleting every synced one")

    headers = {"Authorization": "Bearer " + os.environ["TERMIX_API_KEY"].strip()}
    try:
        hosts = listed(get(f"{TERMIX}/host/db/host", headers), "hosts", "data")
        creds = listed(get(f"{TERMIX}/credentials", headers), "credentials", "data")
    except (urllib.error.URLError, OSError, ValueError) as e:
        fail(f"Termix: {getattr(e, 'code', None) or getattr(e, 'reason', None) or e}")
    matches = [c for c in creds if c.get("name") == CREDENTIAL]
    missing = None
    if len(matches) != 1:
        missing = (f"Termix has {len(matches)} credential(s) named {CREDENTIAL!r}, not one: make it in Termix's UI "
                   "(Credentials, an SSH key, the bastion's private key, no username)")
        if not DRY_RUN:
            fail(missing)
    # Each host's credential by name; one Termix lacks (or has twice) holds that host back.
    cred_ids: dict[str, object] = {}
    held = set()
    for name, want in sorted(wanted.items()):
        cname = want.get("credential", CREDENTIAL)
        found_c = [c for c in creds if c.get("name") == cname]
        if len(found_c) == 1:
            cred_ids[name] = found_c[0]["id"]
        elif cname == CREDENTIAL:
            cred_ids[name] = None  # the default's absence is reported above, and refused for writes
        else:
            held.add(name)
            skipped.append(f"{name}: Termix has {len(found_c)} credential(s) named {cname!r} (its local context's "
                           "termix_credential), not one; make it in Termix's UI. A synced host by that name is left as it is")
    wanted = {n: w for n, w in wanted.items() if n not in held}

    owned = {h.get("name"): h for h in hosts if OWNED_TAG in tags_of(h)}
    by_hand = sorted(h.get("name") for h in hosts if OWNED_TAG not in tags_of(h))
    clash = sorted(set(by_hand) & set(wanted))
    print(f"NetBox: {len(wanted)} host(s) to have; Termix: {len(owned)} synced, {len(by_hand)} made by hand "
          f"(left alone: {by_hand}); credential {CREDENTIAL!r} {'MISSING' if missing else 'found'}")
    if missing:
        print(f"  {missing}; the writes would refuse to run")

    verb = (lambda v: "would " + v) if DRY_RUN else (lambda v: v)
    plan = []
    for name, want in sorted(wanted.items()):
        credential_id = cred_ids[name]
        if name in clash:
            print(f"  skipped {name}: a host made by hand has that name; rename or delete it to let the sync own it")
            continue
        have = owned.get(name)
        if not have:
            print(f"  {verb('create')} {name}: {want.get('protocol', 'ssh')} port {want['port']}, by its {want['via']}, "
                  f"as {want['login']}, with {want.get('credential', CREDENTIAL)!r}, in {want['folder']}")
            plan.append(("POST", f"{TERMIX}/host/db/host", host_body(want, credential_id), name))
        else:
            diff = differs(have, want, credential_id)
            if is_rdp(want) and rdp_settings_differ(have, want):
                diff.append("RDP settings")
            if diff:
                print(f"  {verb('update')} {name}: {', '.join(diff)}")
                if diff != ["RDP settings"]:
                    plan.append(("PUT", f"{TERMIX}/host/db/host/{have['id']}", host_body(want, credential_id, have), name))
                else:
                    plan.append(("SETTINGS", None, None, name))
    for name in sorted(set(owned) - set(wanted) - held):
        print(f"  {verb('delete')} {name}: no longer in NetBox")
        plan.append(("DELETE", f"{TERMIX}/host/db/host/{owned[name]['id']}", None, name))
    for s in skipped:
        print(f"  skipped {s}")
    if not plan:
        print("No changes.")
    if DRY_RUN or not plan:
        return 0

    failed = 0
    for method, url, body, name in plan:
        if method == "SETTINGS":
            continue  # after the host writes, below
        try:
            send(method, url, headers, body)
        except urllib.error.HTTPError as e:
            failed += 1
            print(f"  FAILED {method} {name}: HTTP {e.code} {e.read()[:200].decode(errors='replace')}")
        except (urllib.error.URLError, OSError) as e:
            failed += 1
            print(f"  FAILED {method} {name}: {e}")

    def read_owned() -> dict[str, dict]:
        try:
            after = listed(get(f"{TERMIX}/host/db/host", headers), "hosts", "data")
        except (urllib.error.URLError, OSError, ValueError) as e:
            fail(f"Termix, reading back: {getattr(e, 'code', None) or e}")
        return {h.get("name"): h for h in after if OWNED_TAG in tags_of(h)}

    # RDP turned on in the remote-desktop plugin's settings, by host ID, once the host exists.
    rdp_names = {n for n, w in wanted.items() if is_rdp(w) and n not in clash}
    if rdp_names:
        now = read_owned()
        for name in sorted(rdp_names):
            have = now.get(name)
            if not have or not rdp_settings_differ(have, wanted[name]):
                continue
            try:
                send("PUT", f"{TERMIX}/plugins/{RDP_PLUGIN}/settings/host/{have['id']}", headers, rdp_settings(wanted[name]))
            except urllib.error.HTTPError as e:
                failed += 1
                print(f"  FAILED RDP settings {name}: HTTP {e.code} {e.read()[:200].decode(errors='replace')}")
            except (urllib.error.URLError, OSError) as e:
                failed += 1
                print(f"  FAILED RDP settings {name}: {e}")

    # Read back: every host NetBox names is in Termix as wanted, and none it dropped remains.
    now = read_owned()
    wrong = [n for n, w in wanted.items() if n not in clash and (
        n not in now or differs(now[n], w, cred_ids[n]) or (is_rdp(w) and rdp_settings_differ(now[n], w)))]
    wrong += [n for n in now if n not in wanted and n not in held]
    for n in sorted(wrong):
        print(f"  NOT AS WANTED after the writes: {n}")
    print("VERIFIED" if not wrong and not failed else f"{failed} write(s) failed, {len(wrong)} host(s) not as wanted")
    return 0 if not wrong and not failed else 1

if __name__ == "__main__":
    sys.exit(main())
