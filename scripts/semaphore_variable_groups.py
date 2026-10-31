"""Make Semaphore's variable groups from Key Vault: Semaphore's "Sync: Key Vault → Semaphore
(script)" (QUEUE-semaphore-variables.md).

semaphore_variable_groups.yaml declares each group: its extra variables and environment
variables, each from Key Vault (a secret), a template of Key Vault values, or a plain value.
Every run reports, for every declared group, what differs from Semaphore (by name: a value
is never printed), and for every group the file doesn't declare, the names it holds. Only a
group marked `managed: true` is written, and then exactly as declared: missing, it's created;
a variable or secret it holds that isn't declared is removed. Each written group is read back:
its plain variables must equal the declared ones and its secrets be exactly the declared
names and types. Semaphore never returns a secret's value, so a managed group's secrets are
set at every run (the report says "set") and can't be compared.

A group with any value Key Vault didn't give (missing, or the read failed) isn't written, and
the run fails naming it: a failed read is not an empty value. "Key Vault Sync", the group this
template runs with, is never declared: it holds the credentials below.

The environment ("Key Vault Sync", environment variables only: Semaphore hands a Python
template's extra variables as arguments, which this refuses):
  AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
                        semaphore-keyvault-reader, "Key Vault Secrets User" on the vault
  AZURE_KEYVAULT_URL    https://<vault>.vault.azure.net
  SEMAPHORE_URL         Semaphore's own address, as its tasks reach it
  SEMAPHORE_SYNC_TOKEN  an API token of a user who may manage the project's resources
  SEMAPHORE_VARIABLE_GROUPS  optional: the config's path (default: beside this script)
Exit 0: every managed group matches, read back; 1: a group couldn't be made or read back,
or a value couldn't be read; 2: what the run needs is missing or the config is wrong.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ruamel.yaml import YAML

OWN_GROUP = "Key Vault Sync"
NEEDS = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_KEYVAULT_URL",
         "SEMAPHORE_URL", "SEMAPHORE_SYNC_TOKEN")
KINDS = {"variables": "var", "environment": "env"}  # the config's section: Semaphore's secret type
PLACEHOLDER = re.compile(r"\{([A-Za-z0-9-]+)\}")


class Unread(Exception):
    """A value Key Vault didn't give: missing, or the read failed."""


def log(msg: str) -> None:
    print(msg, flush=True)


def http(method: str, url: str, headers: dict | None = None, body=None, form: dict | None = None, timeout=30):
    """(status, parsed JSON or None); an HTTP error status is returned, a refused connection raises."""
    data, hdrs = None, {"Accept": "application/json", **(headers or {})}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        return e.code, None


# --- the config ---------------------------------------------------------------------------

def load_config(path: Path) -> dict:
    """The declared groups, checked; raises ValueError naming what's wrong."""
    doc = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8")) or {}
    groups = doc.get("groups") or {}
    if not isinstance(doc.get("project"), str) or not isinstance(groups, dict):
        raise ValueError("needs project: <name> and groups: {<group>: ...}")
    if OWN_GROUP in groups:
        raise ValueError(f"declares {OWN_GROUP!r}, the sync's own group: it would rewrite its credentials")
    for name, group in groups.items():
        if not isinstance(group, dict) or set(group) - {"managed", *KINDS}:
            raise ValueError(f"{name}: only managed, variables and environment")
        seen = set()
        for kind in KINDS:
            for var, spec in (group.get(kind) or {}).items():
                if not isinstance(spec, dict) or len(set(spec) & {"keyvault", "template", "value"}) != 1 \
                        or set(spec) - {"keyvault", "template", "value", "secret"}:
                    raise ValueError(f"{name}: {var}: one of keyvault, template or value (and secret)")
                if kind == "environment" and isinstance(spec.get("value"), (dict, list)):
                    raise ValueError(f"{name}: {var}: an environment variable is a single value")
                if var in seen:
                    raise ValueError(f"{name}: {var} declared twice")
                seen.add(var)
    return doc


def is_secret(spec: dict) -> bool:
    return bool(spec.get("secret", "keyvault" in spec))


# --- Key Vault ----------------------------------------------------------------------------

class KeyVault:
    def __init__(self, env: dict):
        self.url = env["AZURE_KEYVAULT_URL"].rstrip("/")
        self.env, self.token, self.cache = env, None, {}

    def login(self) -> None:
        status, body = http("POST", f"https://login.microsoftonline.com/{self.env['AZURE_TENANT_ID']}/oauth2/v2.0/token",
                            form={"grant_type": "client_credentials", "client_id": self.env["AZURE_CLIENT_ID"],
                                  "client_secret": self.env["AZURE_CLIENT_SECRET"],
                                  "scope": "https://vault.azure.net/.default"})
        if status != 200 or not (body or {}).get("access_token"):
            raise Unread(f"Azure refused the sync's login (HTTP {status}): is the service principal's secret expired?")
        self.token = body["access_token"]

    def get(self, name: str) -> str:
        if name not in self.cache:
            try:
                status, body = http("GET", f"{self.url}/secrets/{urllib.parse.quote(name)}?api-version=7.4",
                                    {"Authorization": f"Bearer {self.token}"})
            except (urllib.error.URLError, OSError) as e:
                raise Unread(f"{name}: Key Vault couldn't be asked ({type(e).__name__})")
            if status == 404:
                raise Unread(f"{name}: not in Key Vault")
            if status != 200 or not isinstance((body or {}).get("value"), str):
                raise Unread(f"{name}: Key Vault answered HTTP {status}")
            self.cache[name] = body["value"]
        return self.cache[name]


def resolve(spec: dict, vault: KeyVault):
    if "keyvault" in spec:
        return vault.get(spec["keyvault"])
    if "template" in spec:
        return PLACEHOLDER.sub(lambda m: vault.get(m.group(1)), spec["template"])
    return spec["value"]


def desired(group: dict, vault: KeyVault) -> tuple[dict, list[str]]:
    """{kind: {name: (value, secret)}}, and what Key Vault didn't give."""
    out, unread = {k: {} for k in KINDS}, []
    for kind in KINDS:
        for var, spec in (group.get(kind) or {}).items():
            try:
                value = resolve(spec, vault)
            except Unread as e:
                unread.append(f"{var}: {e}")
                continue
            if kind == "environment" and not isinstance(value, str):
                value = json.dumps(value) if isinstance(value, bool) else str(value)
            out[kind][var] = (value, is_secret(spec))
    return out, unread


# --- Semaphore ----------------------------------------------------------------------------

class Semaphore:
    def __init__(self, env: dict):
        self.url = env["SEMAPHORE_URL"].rstrip("/") + "/api"
        self.headers = {"Authorization": f"Bearer {env['SEMAPHORE_SYNC_TOKEN']}"}

    def call(self, method, path, body=None):
        status, got = http(method, self.url + path, self.headers, body)
        if status >= 300:
            raise RuntimeError(f"Semaphore answered {method} {path} with HTTP {status}")
        return got

    def project_id(self, name: str) -> int:
        found = [p["id"] for p in self.call("GET", "/projects") or [] if p.get("name") == name]
        if len(found) != 1:
            raise RuntimeError(f"{len(found)} Semaphore projects named {name!r}")
        return found[0]

    def groups(self, pid: int) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for e in self.call("GET", f"/project/{pid}/environment") or []:
            out.setdefault(e.get("name"), []).append(e)
        return out

    def group(self, pid: int, gid: int) -> dict:
        return self.call("GET", f"/project/{pid}/environment/{gid}")


def current(e: dict) -> dict:
    """{kind: {name: value}} for its plain variables, and {(name, type): id} for its secrets."""
    def parsed(raw):
        try:
            got = json.loads(raw or "{}")
            return got if isinstance(got, dict) else {}
        except ValueError:
            return {}
    return {"variables": parsed(e.get("json")), "environment": parsed(e.get("env")),
            "secrets": {(s.get("name"), s.get("type")): s.get("id") for s in e.get("secrets") or []}}


def differences(want: dict, have: dict) -> list[str]:
    """What making the group as declared would do, by name only."""
    lines = []
    for kind, stype in KINDS.items():
        plain = {k: v for k, (v, secret) in want[kind].items() if not secret}
        secret = {k for k, (_, s) in want[kind].items() if s}
        for k in sorted(set(plain) - set(have[kind])):
            lines.append(f"add {kind} {k}")
        for k in sorted(k for k in set(plain) & set(have[kind]) if plain[k] != have[kind][k]):
            lines.append(f"change {kind} {k}")
        for k in sorted(set(have[kind]) - set(plain)):
            lines.append(f"remove {kind} {k}")
        for k in sorted(secret):
            lines.append(f"{'set' if (k, stype) in have['secrets'] else 'add'} secret {kind} {k}")
        for k in sorted(n for (n, t) in have["secrets"] if t == stype and n not in secret):
            lines.append(f"remove secret {kind} {k}")
    return lines


def body(name: str, pid: int, want: dict, have: dict | None, existing: dict | None) -> dict:
    """The POST or PUT body making the group exactly as declared."""
    b = {"name": name, "project_id": pid,
         "json": json.dumps({k: v for k, (v, s) in want["variables"].items() if not s}),
         "env": json.dumps({k: v for k, (v, s) in want["environment"].items() if not s}),
         "secrets": []}
    old = have["secrets"] if have else {}
    for kind, stype in KINDS.items():
        for k, (v, secret) in want[kind].items():
            if not secret:
                continue
            if (k, stype) in old:
                b["secrets"].append({"id": old[(k, stype)], "name": k, "secret": v, "type": stype, "operation": "update"})
            else:
                b["secrets"].append({"name": k, "secret": v, "type": stype, "operation": "create"})
    wanted = {(k, KINDS[kind]) for kind in KINDS for k, (_, s) in want[kind].items() if s}
    for (k, stype), sid in old.items():
        if (k, stype) not in wanted:
            b["secrets"].append({"id": sid, "name": k, "type": stype, "operation": "delete"})
    if existing:
        b["id"] = existing["id"]
        if existing.get("password") is not None:
            b["password"] = existing["password"]
    return b


def matches(want: dict, back: dict) -> list[str]:
    """What the read-back shows differently from the declared, by name."""
    have = current(back)
    wrong = []
    for kind, stype in KINDS.items():
        plain = {k: v for k, (v, s) in want[kind].items() if not s}
        if have[kind] != plain:
            wrong.append(f"{kind} read back as {sorted(have[kind])}, not {sorted(plain)}"
                         if set(have[kind]) != set(plain) else f"{kind}: a value read back differently")
    secrets = {(k, KINDS[kind]) for kind in KINDS for k, (_, s) in want[kind].items() if s}
    if set(have["secrets"]) != secrets:
        wrong.append(f"secrets read back as {sorted(have['secrets'])}, not {sorted(secrets)}")
    return wrong


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        log("semaphore_variable_groups.py takes no arguments: its settings are environment variables "
            f"(Semaphore: the {OWN_GROUP!r} group's environment variables, never extra variables)")
        return 2
    env = dict(os.environ)
    missing = [v for v in NEEDS if not env.get(v, "").strip()]
    if missing:
        log(f"missing: {', '.join(missing)} (the {OWN_GROUP!r} group; this script's docstring says what each is)")
        return 2
    path = Path(env.get("SEMAPHORE_VARIABLE_GROUPS") or Path(__file__).with_suffix(".yaml"))
    try:
        config = load_config(path)
    except (OSError, ValueError) as e:
        log(f"{path.name}: {e}")
        return 2
    vault, sem = KeyVault(env), Semaphore(env)
    try:
        vault.login()
        pid = sem.project_id(config["project"])
        live = sem.groups(pid)
    except (Unread, RuntimeError, urllib.error.URLError, OSError) as e:
        log(f"stopped before any group: {e}")
        return 1
    failed = False
    for name, group in config["groups"].items():
        managed = group.get("managed") is True
        log(f"{name} ({'managed' if managed else 'compared only'}):")
        found = live.get(name, [])
        if len(found) > 1:
            log(f"  {len(found)} groups have this name: none written; rename or delete the extras")
            failed = True
            continue
        try:
            existing = sem.group(pid, found[0]["id"]) if found else None
        except (RuntimeError, urllib.error.URLError, OSError) as e:
            log(f"  couldn't read it: {e}")
            failed = True
            continue
        want, unread = desired(group, vault)
        have = current(existing) if existing else None
        for line in (differences(want, have) if have else ["create the group"] + differences(want, current({}))):
            log(f"  {line}")
        if unread:
            for u in unread:
                log(f"  couldn't get {u}")
            log("  not written: a value is missing" if managed else "  (a value is missing)")
            failed = True
            continue
        if not managed:
            continue
        try:
            if existing:
                sem.call("PUT", f"/project/{pid}/environment/{existing['id']}", body(name, pid, want, have, existing))
                gid = existing["id"]
            else:
                made = sem.call("POST", f"/project/{pid}/environment", body(name, pid, want, None, None))
                gid = (made or {}).get("id")
                if gid is None:
                    gid = next((e["id"] for e in sem.groups(pid).get(name, [])), None)
            back = sem.group(pid, gid) if gid is not None else {}
        except (RuntimeError, urllib.error.URLError, OSError) as e:
            log(f"  writing it failed: {e}")
            failed = True
            continue
        wrong = matches(want, back) if back else ["it isn't there after writing"]
        if wrong:
            for w in wrong:
                log(f"  didn't take: {w}")
            failed = True
        else:
            log("  written and read back")
    declared = set(config["groups"]) | {OWN_GROUP}
    for name in sorted(n for n in live if n not in declared):
        for e in live[name]:
            try:
                have = current(sem.group(pid, e["id"]))
            except (RuntimeError, urllib.error.URLError, OSError) as ex:
                log(f"{name} (not declared): couldn't read it: {ex}")
                continue
            secrets = sorted(f"{t}:{n}" for (n, t) in have["secrets"])
            log(f"{name} (not declared): variables {sorted(have['variables'])}, environment "
                f"{sorted(have['environment'])}, secrets {secrets}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
