"""Write the site's facts NetBox holds into Key Vault: Semaphore's "Sync: NetBox → Key Vault
(script)" (QUEUE-site-facts.md, stage 3).

A NetBox IP address, IP range or prefix says which Key Vault secrets it feeds in its custom
field `keyvault_secrets` (a multiple selection), so no host name or address is in git.
keyvault_facts.yaml says how each name's value is made, in one of four ways:
  take: FORM      from the one object claiming the name; FORM is one of
                    address   an IP address's host part   cidr    an address or prefix with its mask
                    dns_name  an IP address's DNS name    range   an IP range, "start-end"
  collect: FORM   from every object claiming the name, sorted and joined (join:, default ","):
                  tagging another object in NetBox adds it at the next run
  template: TEXT  TEXT with each {NAME} replaced: a name mapped here gives its value, any
                  other Key Vault's
  view: NAME      scripts/keyvault_views/NAME.py's build(site), for a value with structure (a
                  JSON variable): it reads NetBox through `site` (Site, below) and returns a
                  string, or data written as JSON. A new JSON variable is a new view; a change
                  in NetBox reaches it at the next run.

A value is taken only from an object NetBox's status table lets be used (README.md, "What a
NetBox status means"): an address Active, Reserved or DHCP, and its device or VM Staged or
Active; a range or prefix Active or Reserved. A name whose object is in any other status is
reported and its Key Vault secret left alone. A mapped name no object claims, or two do, is
reported too, and fails the run: the map promises it a source.

The runs (a value is never printed, only names, NetBox object IDs, JSON paths and yes/no):
  (none)    for each mapped name, whether NetBox and Key Vault agree; for JSON, which paths
            differ (compared as data, so formatting doesn't count)
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

import importlib.util
import ipaddress
import json
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
WAYS = ("take", "collect", "template", "view")
VIEWS = Path(__file__).resolve().parent / "keyvault_views"
VIEW = re.compile(r"^[a-z0-9_]+$")
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
        ways = [w for w in WAYS if w in (spec if isinstance(spec, dict) else {})]
        extra = set(spec if isinstance(spec, dict) else {}) - set(WAYS) - ({"join"} if ways == ["collect"] else set())
        if len(ways) != 1 or extra:
            raise ValueError(f"{name}: give exactly one of {', '.join(w + ':' for w in WAYS)}"
                             + (" (join: goes only with collect:)" if "join" in extra else ""))
        for way in ("take", "collect"):
            if way in spec and spec[way] not in TAKES:
                raise ValueError(f"{name}: {way}: is one of {', '.join(sorted(TAKES))}")
        if "view" in spec and not (isinstance(spec["view"], str) and VIEW.match(spec["view"])
                                   and (VIEWS / f"{spec['view']}.py").is_file()):
            raise ValueError(f"{name}: view: names a file in scripts/keyvault_views/, without .py")
        if "template" in spec:
            refs = PLACEHOLDER.findall(str(spec["template"]))
            if not refs:
                raise ValueError(f"{name}: its template names no other secret")
            if name in refs or any(facts.get(r, {}).get("template") for r in refs):
                raise ValueError(f"{name}: a template may name only secrets that aren't templates")
    return facts


# --- NetBox -------------------------------------------------------------------------------

class NetBox:
    def __init__(self, env: dict):
        self.url = env["NETBOX_URL"].rstrip("/")
        self.headers = {"Authorization": "Bearer " + env["NETBOX_TOKEN"].strip().removeprefix("Bearer ")}
        self.hosts, self.cache = {}, {}

    def all(self, path: str) -> list[dict]:
        """Every object at path (read once per run); RuntimeError when NetBox didn't answer in full."""
        if path in self.cache:
            return self.cache[path]
        out, url = [], f"{self.url}/api/{path}/?limit=500"
        while url:
            status, body = http("GET", url, self.headers)
            if status != 200 or not isinstance(body, dict) or not isinstance(body.get("results"), list):
                raise RuntimeError(f"NetBox answered HTTP {status} for {path}")
            out += body["results"]
            url = body.get("next")
        self.cache[path] = out
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


class Site:
    """What a view reads. Each method raises when NetBox or Key Vault didn't answer, so a view
    never builds a value from a partial read."""

    def __init__(self, nb: NetBox, vault: KeyVault):
        self.nb, self.vault = nb, vault

    def usable(self, kind: str) -> list[dict]:
        """The objects of a kind ("ip", "range", "prefix") a job may use (the status table)."""
        return [o for o in self.nb.all(KINDS[kind][0]) if usable(self.nb, kind, o) is None]

    def claiming(self, name: str) -> list[tuple[str, dict]]:
        """The usable objects whose keyvault_secrets names name."""
        return [(k, o) for k in KINDS for o in self.usable(k) if name in (o.get("custom_fields") or {}).get(FIELD) or []]

    def all(self, path: str) -> list[dict]:
        """Every object at an API path (dcim/devices, ipam/vlans, plugins/netbox-dns/records):
        status is the view's to check, by the same table."""
        return self.nb.all(path)

    def secret(self, name: str) -> str:
        """Another Key Vault secret's value, for what NetBox doesn't hold (a key, a domain)."""
        return self.vault.get(name)

    form = staticmethod(forms)


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


def build_view(view: str, site: Site):
    """The view's value as Key Vault holds it, and its data (None for a plain string)."""
    spec = importlib.util.spec_from_file_location(f"keyvault_views.{view}", VIEWS / f"{view}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = mod.build(site)
    if isinstance(data, str):
        return data, None
    return json.dumps(data, sort_keys=True, separators=(",", ":")), data


def paths(a, b, at: str = "") -> list[str]:
    """Where two JSON values differ, as paths: never the values."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            p = f"{at}.{k}" if at else str(k)
            if k not in a:
                out.append(f"{p} (only in Key Vault)")
            elif k not in b:
                out.append(f"{p} (only in NetBox's)")
            else:
                out += paths(a[k], b[k], p)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [x for i, (u, v) in enumerate(zip(a, b)) for x in paths(u, v, f"{at}[{i}]")]
    if isinstance(a, list) and isinstance(b, list):
        return [f"{at or '(the whole value)'} ({len(a)} items from NetBox, {len(b)} in Key Vault)"]
    return [] if a == b else [at or "(the whole value)"]


def make(name: str, spec: dict, claims: dict, nb: NetBox, site: Site, made: dict):
    """(value, data, source) for one name, or a str saying why it's left alone; raises Unread."""
    if "take" in spec or "collect" in spec:
        take = spec.get("take") or spec["collect"]
        found = claims[name]
        if not found:
            raise LookupError("no NetBox object claims it")
        if "take" in spec and len(found) > 1:
            raise LookupError(f"claimed by {len(found)} objects ({', '.join(label(k, o) for k, o in found)}): left alone")
        values, skipped = [], []
        for kind, obj in found:
            why = usable(nb, kind, obj)
            if why:
                skipped.append(f"{label(kind, obj)}: {why}")
                continue
            value = forms(kind, obj).get(take)
            if value is None:
                raise LookupError(f"{label(kind, obj)} has no {take}")
            values.append((value, label(kind, obj)))
        if not values:
            return f"{'; '.join(skipped)}, so it's left alone"
        if "take" in spec:
            return values[0][0], None, f"{take} of {values[0][1]}"
        joined = spec.get("join", ",").join(sorted({v for v, _ in values}))
        return joined, None, f"{take} of {len(values)} objects" + (f" ({len(skipped)} left out by status)" if skipped else "")
    if "view" in spec:
        value, data = build_view(spec["view"], site)
        return value, data, f"view {spec['view']}"
    refs = PLACEHOLDER.findall(spec["template"])
    if any(r in made and isinstance(made[r], str) for r in refs):
        return "a name its template reads has no usable value above, so it's left alone"
    value = PLACEHOLDER.sub(lambda m: made[m.group(1)][0] if m.group(1) in made else site.secret(m.group(1)),
                            spec["template"])
    return value, None, f"its template on {', '.join(refs)}"


def agree(have: str | None, value: str, data) -> tuple[bool, list[str]]:
    """Whether Key Vault already holds it (JSON compared as data), and the paths that differ."""
    if have is None:
        return False, []
    if data is None:
        return have == value, []
    try:
        held = json.loads(have)
    except ValueError:
        return False, ["(Key Vault's isn't JSON)"]
    diff = paths(data, held)
    return not diff, diff


def run(vault: Vault, nb: NetBox, facts: dict, writer: Vault | None) -> int:
    failed = False
    if not facts:
        log("keyvault_facts.yaml maps no names yet")
        return 0
    if any(w in spec for spec in facts.values() for w in ("take", "collect")):
        field = nb.field()
        if field is None:
            log(f"NetBox has no custom field {FIELD}, so no object claims a name")
        else:
            offered = nb.choices(field) or set()
            for name in sorted(n for n, sp in facts.items() if ("take" in sp or "collect" in sp) and n not in offered):
                log(f"{FIELD}'s choices lack {name}, so no object can claim it")
    claims, stray = sources(nb, facts)
    for line in stray:
        log(line)
    site, made = Site(nb, vault), {}
    # Templates last: they read the others' values.
    for name, spec in sorted(facts.items(), key=lambda kv: "template" in kv[1]):
        try:
            result = make(name, spec, claims, nb, site, made)
        except LookupError as e:
            log(f"{name}: {e}")
            made[name], failed = "failed", True
            continue
        except Unread as e:
            log(f"{name}: couldn't be made: {e}")
            made[name], failed = "failed", True
            continue
        except Exception as e:  # a view's own bug: its type and line, never its message (it may hold a value)
            tb = e.__traceback__
            while tb.tb_next:
                tb = tb.tb_next
            log(f"{name}: its view failed: {type(e).__name__} at {Path(tb.tb_frame.f_code.co_filename).name}:{tb.tb_lineno}")
            made[name], failed = "failed", True
            continue
        made[name] = result
        if isinstance(result, str):
            log(f"{name}: {result}")
            continue
        value, data, source = result
        try:
            have = vault.get(name)
        except Unread as e:
            if "not in Key Vault" not in str(e):
                log(f"{name}: couldn't read Key Vault: {e}")
                failed = True
                continue
            have = None
        same, diff = agree(have, value, data)
        if same:
            log(f"{name}: agrees ({source})")
            continue
        log(f"{name}: {'not in Key Vault' if have is None else 'differs'} ({source})")
        for p in diff[:20]:
            log(f"  {p}")
        if len(diff) > 20:
            log(f"  and {len(diff) - 20} more paths")
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
