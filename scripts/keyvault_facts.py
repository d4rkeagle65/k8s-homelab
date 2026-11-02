"""Write the site's facts NetBox holds into Key Vault: Semaphore's "Sync: NetBox → Key Vault
(script)" (QUEUE-site-facts.md, stage 3).

A NetBox IP address, IP range or prefix says which Key Vault secrets it feeds in its custom
field `keyvault_secrets` (a multiple selection), so no host name or address is in git.
keyvault_facts.yaml says what each name takes from its object:
  address    an IP address's host part        cidr       an IP address, or a prefix, with its mask
  dns_name   an IP address's DNS name         range      an IP range, "start-end", host parts
or a template on other names ({PIHOLE1-MGMT-ADDRESS}): a mapped name gives its NetBox value,
any other Key Vault's.

A value is taken only from an object NetBox's status table lets be used (README.md, "What a
NetBox status means"): an address Active, Reserved or DHCP, and its device or VM Staged or
Active; a range or prefix Active or Reserved. A name whose object is in any other status is
reported and its Key Vault secret left alone. A mapped name no object claims, or two do, is
reported too, and fails the run: the map promises it a source.

The runs (a value is never printed, only names, NetBox object IDs and yes/no):
  (none)    for each mapped name, whether NetBox and Key Vault agree
  --write   the same, then sets each that differs and reads it back
  --find    compares every Key Vault secret with every NetBox address, range and prefix in
            each form and reports which equal or contain which: the evidence for tagging an
            object and mapping its name, with no value seen
Any other argument is refused, unnamed: Semaphore passes a group's extra variables as
arguments, and this never echoes one.

The environment (Semaphore's "Key Vault Sync" and "Key Vault Facts" groups):
  AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_KEYVAULT_URL
                        the sync's reader (scripts/semaphore_variable_groups.py)
  NETBOX_URL, NETBOX_TOKEN   NetBox's API, read only
  FACTS_AZURE_CLIENT_ID, FACTS_AZURE_CLIENT_SECRET
                        --write only: the writer, "Key Vault Secrets Officer" on each mapped
                        secret and nothing else (--roles prints the az lines for them)
  KEYVAULT_FACTS        optional: the map's path (default: beside this script)
Exit 0: every name answered (and with --write, every difference set and read back); 1: a
name couldn't be answered, a mapped name has no single source, or a write didn't take;
2: what the run needs is missing or the map is wrong.
"""
from __future__ import annotations

import ipaddress
import os
import re
import sys
import urllib.error
import urllib.parse
from pathlib import Path

# Beside this script, which Python puts first on the path.
from semaphore_variable_groups import KeyVault, Unread, http, log

FIELD = "keyvault_secrets"
TAKES = {"address", "cidr", "dns_name", "range"}
NEEDS = ["AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_KEYVAULT_URL",
         "NETBOX_URL", "NETBOX_TOKEN"]
WRITER = ["FACTS_AZURE_CLIENT_ID", "FACTS_AZURE_CLIENT_SECRET"]
PLACEHOLDER = re.compile(r"\{([A-Za-z0-9-]+)\}")
NAME = re.compile(r"^[A-Za-z0-9-]{1,127}$")
# README.md, "What a NetBox status means": what a job may use.
USABLE_ADDRESS = {"active", "reserved", "dhcp"}
USABLE_HOST = {"staged", "active"}
USABLE_SPAN = {"active", "reserved"}
KINDS = {"ip": ("ipam/ip-addresses", "IP address"), "range": ("ipam/ip-ranges", "IP range"),
         "prefix": ("ipam/prefixes", "prefix")}


# --- the map ------------------------------------------------------------------------------

def load_map(path: Path) -> dict:
    """{name: spec}, checked; raises ValueError naming what's wrong."""
    from ruamel.yaml import YAML
    doc = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8")) or {}
    facts = doc.get("facts") or {}
    if not isinstance(facts, dict):
        raise ValueError("facts: must map each Key Vault name to what it takes")
    for name, spec in facts.items():
        if not isinstance(name, str) or not NAME.match(name):
            raise ValueError(f"{name!r} isn't a Key Vault name")
        if not isinstance(spec, dict) or len(spec) != 1 or not ({"take", "template"} & set(spec)):
            raise ValueError(f"{name}: give exactly one of take: or template:")
        if "take" in spec and spec["take"] not in TAKES:
            raise ValueError(f"{name}: take: is one of {', '.join(sorted(TAKES))}")
        if "template" in spec:
            refs = PLACEHOLDER.findall(str(spec["template"]))
            if not refs:
                raise ValueError(f"{name}: its template names no other secret")
            if name in refs or any(facts.get(r, {}).get("template") for r in refs):
                raise ValueError(f"{name}: a template may name only taken or unmapped secrets")
    return facts


# --- NetBox -------------------------------------------------------------------------------

class NetBox:
    def __init__(self, env: dict):
        self.url = env["NETBOX_URL"].rstrip("/")
        self.headers = {"Authorization": "Bearer " + env["NETBOX_TOKEN"].strip().removeprefix("Bearer ")}
        self.hosts = {}

    def all(self, path: str) -> list[dict]:
        """Every object at path; RuntimeError when NetBox didn't answer in full."""
        out, url = [], f"{self.url}/api/{path}/?limit=500"
        while url:
            status, body = http("GET", url, self.headers)
            if status != 200 or not isinstance(body, dict) or not isinstance(body.get("results"), list):
                raise RuntimeError(f"NetBox answered HTTP {status} for {path}")
            out += body["results"]
            url = body.get("next")
        return out

    def field(self) -> dict | None:
        status, body = http("GET", f"{self.url}/api/extras/custom-fields/?name={FIELD}", self.headers)
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError(f"NetBox answered HTTP {status} for its custom fields")
        return (body.get("results") or [None])[0]

    def choices(self, field: dict) -> set[str] | None:
        """The names the field's choice set offers, or None when it has none."""
        ref = field.get("choice_set")
        if not ref:
            return None
        status, body = http("GET", f"{self.url}/api/extras/custom-field-choice-sets/{ref['id']}/", self.headers)
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError(f"NetBox answered HTTP {status} for {FIELD}'s choices")
        return {c[0] for c in body.get("extra_choices") or []}

    def host_status(self, ip: dict) -> str | None:
        """The status of the device or VM the address's interface is on, or None when unassigned."""
        obj = ip.get("assigned_object") or {}
        for key, path in (("device", "dcim/devices"), ("virtual_machine", "virtualization/virtual-machines")):
            ref = obj.get(key)
            if ref:
                if (path, ref["id"]) not in self.hosts:
                    status, body = http("GET", f"{self.url}/api/{path}/{ref['id']}/", self.headers)
                    if status != 200 or not isinstance(body, dict):
                        raise RuntimeError(f"NetBox answered HTTP {status} for {path} #{ref['id']}")
                    self.hosts[(path, ref["id"])] = (body.get("status") or {}).get("value")
                return self.hosts[(path, ref["id"])]
        return None


def status_of(obj: dict) -> str:
    return (obj.get("status") or {}).get("value") or ""


def forms(kind: str, obj: dict) -> dict[str, str]:
    """What this object can give: {take: value}."""
    if kind == "ip":
        out = {"address": str(ipaddress.ip_interface(obj["address"]).ip), "cidr": obj["address"]}
        if (obj.get("dns_name") or "").strip():
            out["dns_name"] = obj["dns_name"].strip()
        return out
    if kind == "range":
        start = ipaddress.ip_interface(obj["start_address"]).ip
        end = ipaddress.ip_interface(obj["end_address"]).ip
        return {"range": f"{start}-{end}"}
    return {"cidr": obj["prefix"]}


def usable(nb: NetBox, kind: str, obj: dict) -> str | None:
    """None when a job may use the object, else why not."""
    status = status_of(obj)
    if kind == "ip":
        if status not in USABLE_ADDRESS:
            return f"the address is {status or 'without a status'}"
        host = nb.host_status(obj)
        if host is not None and host not in USABLE_HOST:
            return f"its device or VM is {host or 'without a status'}"
        return None
    return None if status in USABLE_SPAN else f"it is {status or 'without a status'}"


def objects(nb: NetBox) -> list[tuple[str, dict]]:
    return [(kind, obj) for kind, (path, _) in KINDS.items() for obj in nb.all(path)]


def label(kind: str, obj: dict) -> str:
    return f"{KINDS[kind][1]} #{obj['id']}"


# --- Key Vault ----------------------------------------------------------------------------

class Vault(KeyVault):
    def names(self) -> list[str]:
        """Every enabled secret's name; Unread when the list failed."""
        out, url = [], f"{self.url}/secrets?api-version=7.4"
        while url:
            try:
                status, body = http("GET", url, {"Authorization": f"Bearer {self.token}"})
            except (urllib.error.URLError, OSError) as e:
                raise Unread(f"Key Vault's list couldn't be asked ({type(e).__name__})")
            if status != 200 or not isinstance((body or {}).get("value"), list):
                raise Unread(f"Key Vault answered HTTP {status} for its list of secrets")
            out += [s["id"].rstrip("/").rsplit("/", 1)[1] for s in body["value"]
                    if (s.get("attributes") or {}).get("enabled", True)]
            url = body.get("nextLink")
        return out


def writer_env(env: dict) -> dict:
    return {**env, "AZURE_CLIENT_ID": env["FACTS_AZURE_CLIENT_ID"],
            "AZURE_CLIENT_SECRET": env["FACTS_AZURE_CLIENT_SECRET"]}


# --- the runs -----------------------------------------------------------------------------

def contains(value: str, part: str) -> bool:
    """part within value, not as a piece of a longer address or name."""
    return re.search(r"(?<![\w.-])" + re.escape(part) + r"(?![\w-]|\.\w)", value) is not None


def find(vault: Vault, nb: NetBox) -> int:
    index = [(take, v, kind, obj) for kind, obj in objects(nb) for take, v in forms(kind, obj).items()]
    names, unread, unmatched = vault.names(), [], 0
    log(f"{len(names)} Key Vault secrets against {len({(k, o['id']) for _, _, k, o in index})} NetBox objects:")
    for name in sorted(names):
        try:
            value = vault.get(name)
        except Unread as e:
            unread.append(str(e))
            continue
        trimmed = value.strip()
        hits = [f"equals {take} of {label(kind, obj)} ({status_of(obj)})" for take, v, kind, obj in index if v == trimmed]
        if not hits:
            hits = [f"contains {take} of {label(kind, obj)} ({status_of(obj)})" for take, v, kind, obj in index
                    if take in ("address", "dns_name") and contains(value, v)]
        if hits:
            note = " (after trimming whitespace)" if trimmed != value and any(h.startswith("equals") for h in hits) else ""
            log(f"  {name}: {'; '.join(hits)}{note}")
        else:
            unmatched += 1
    log(f"  {unmatched} match nothing in NetBox")
    for u in unread:
        log(f"  couldn't read {u}")
    return 1 if unread else 0


def sources(nb: NetBox, facts: dict) -> tuple[dict, list[str]]:
    """{name: [(kind, obj)]} for every object claiming a name, and the claims the map lacks."""
    claims, stray = {n: [] for n in facts}, []
    for kind, obj in objects(nb):
        for name in (obj.get("custom_fields") or {}).get(FIELD) or []:
            if name in claims:
                claims[name].append((kind, obj))
            else:
                stray.append(f"{label(kind, obj)} claims {name}, which keyvault_facts.yaml doesn't map")
    return claims, stray


def run(vault: Vault, nb: NetBox, facts: dict, writer: Vault | None) -> int:
    failed = False
    if not facts:
        log("keyvault_facts.yaml maps no names yet")
        return 0
    field = nb.field()
    if field is None:
        log(f"NetBox has no custom field {FIELD}, so no object claims a name")
    else:
        for name in sorted(set(facts) - (nb.choices(field) or set())):
            log(f"{FIELD}'s choices lack {name}, so no object can claim it")
    claims, stray = sources(nb, facts)
    for line in stray:
        log(line)
    netbox = {}
    for name, spec in facts.items():
        if "take" not in spec:
            continue
        found = claims[name]
        if len(found) != 1:
            log(f"{name}: " + ("no NetBox object claims it" if not found else
                f"claimed by {len(found)} objects ({', '.join(label(k, o) for k, o in found)}): left alone"))
            failed = True
            continue
        kind, obj = found[0]
        why = usable(nb, kind, obj)
        if why:
            log(f"{name}: {label(kind, obj)}: {why}, so it's left alone")
            continue
        value = forms(kind, obj).get(spec["take"])
        if value is None:
            log(f"{name}: {label(kind, obj)} has no {spec['take']}")
            failed = True
            continue
        netbox[name] = (value, f"{spec['take']} of {label(kind, obj)}")
    for name, spec in facts.items():
        if "template" not in spec:
            continue
        refs = PLACEHOLDER.findall(spec["template"])
        if any(r in facts and r not in netbox for r in refs):
            log(f"{name}: left alone, since a name its template reads has no usable value above")
            continue
        try:
            value = PLACEHOLDER.sub(lambda m: netbox[m.group(1)][0] if m.group(1) in netbox else vault.get(m.group(1)),
                                    spec["template"])
        except Unread as e:
            log(f"{name}: its template couldn't be filled: {e}")
            failed = True
            continue
        netbox[name] = (value, f"its template on {', '.join(refs)}")
    for name, (value, source) in netbox.items():
        try:
            have = vault.get(name)
        except Unread as e:
            if "not in Key Vault" not in str(e):
                log(f"{name}: couldn't read Key Vault: {e}")
                failed = True
                continue
            have = None
        if have == value:
            log(f"{name}: agrees ({source})")
            continue
        log(f"{name}: {'not in Key Vault' if have is None else 'differs'} ({source})")
        if writer is None:
            continue
        status = writer.put(name, value)
        try:
            back = writer.get(name) if status == 200 else None
        except Unread as e:
            back, status = None, f"{status}, then the read back failed: {e}"
        if back == value:
            log("  set, and reads back the same")
        else:
            log(f"  not set: Key Vault answered {status}" if back is None else "  set, but reads back different")
            failed = True
    return 1 if failed else 0


def roles(facts: dict) -> None:
    """The az lines that give the writer exactly the mapped secrets."""
    log("# As the vault's owner, with $writer the writer's object ID and $vault the vault's name:")
    for name in facts:
        log(f'az role assignment create --assignee-object-id $writer --assignee-principal-type ServicePrincipal '
            f'--role "Key Vault Secrets Officer" --scope "$(az keyvault show -n $vault --query id -o tsv)/secrets/{name}"')


def parse_args(argv: list[str]) -> str | None:
    """'find', 'write', 'roles' or None; ValueError for anything else, never repeating it."""
    known = {"--find": "find", "--write": "write", "--roles": "roles"}
    picked = [known[a] for a in argv if a in known]
    if len(picked) != len(argv) or len(picked) > 1:
        raise ValueError("refused: give at most one of --find, --write, --roles; any other argument "
                         "isn't taken (not shown: it may be a value)")
    return picked[0] if picked else None


def main(argv: list[str]) -> int:
    try:
        mode = parse_args(argv)
    except ValueError as e:
        log(str(e))
        return 2
    env = dict(os.environ)
    path = Path(env.get("KEYVAULT_FACTS") or Path(__file__).with_suffix(".yaml"))
    try:
        facts = load_map(path)
    except (OSError, ValueError) as e:
        log(f"{path.name}: {e}")
        return 2
    if mode == "roles":
        roles(facts)
        return 0
    missing = [v for v in NEEDS + (WRITER if mode == "write" else []) if not env.get(v, "").strip()]
    if missing:
        log(f"missing: {', '.join(missing)} (this script's docstring says what each is)")
        return 2
    vault, nb = Vault(env), NetBox(env)
    try:
        vault.login()
        writer = None
        if mode == "write":
            writer = Vault(writer_env(env))
            writer.login()
        return find(vault, nb) if mode == "find" else run(vault, nb, facts, writer)
    except (Unread, RuntimeError, urllib.error.URLError, OSError) as e:
        log(f"stopped: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
