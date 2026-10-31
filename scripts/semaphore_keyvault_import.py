"""Copy one Semaphore variable group's values into Key Vault, once: run by
ansible/semaphore-tool-keyvault-import.yml ("Tool: Variable Group → Key Vault (one time)";
QUEUE-semaphore-variables.md).

Semaphore never returns a secret's value through its API, but hands a task its variable
groups' values. The template carries two groups, "Key Vault Sync" and the one to import; the
playbook gathers every variable semaphore_variable_groups.yaml declares, for any group, from
the run, and passes them here on standard input, as JSON, never in a file:

  {"project": "<name>", "groups": {<the config's groups>}, "group": "<name>" or null,
   "dry_run": true or false, "values": {"<variable>": <its value in the run, or null>}}

The group to import is the one the template has attached beside "Key Vault Sync", asked of
Semaphore's API (the playbook is the template's; "group", the survey's import_group,
overrides it). Each of its variables the config takes from Key Vault is written to its
secret there (JSON for one marked json: true), unless Key Vault already holds it: the same
value is left as it is, a different one is reported and never overwritten. Each write is read
back. A dry run says, for each, whether it would write it, finds it the same, or finds it
different, and writes nothing. A variable built from a template or a plain value isn't
imported. Nothing is printed but names.

Writing needs "Key Vault Secrets Officer" for the run; "Key Vault Secrets User", the sync's
own, refuses it (403), and this says so. Its environment is "Key Vault Sync"'s:
AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_KEYVAULT_URL, SEMAPHORE_URL and
SEMAPHORE_SYNC_TOKEN. Exit 0: every value is in Key Vault, as the group has it (or would be);
1: one isn't (missing in the run, different, refused, didn't read back); 2: the input, the
environment or the template's groups are wrong.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error

from semaphore_variable_groups import KINDS, NEEDS, OWN_GROUP, KeyVault, Semaphore, Unread

PLAYBOOK = "ansible/semaphore-tool-keyvault-import.yml"


class Wrong(Exception):
    """The template's groups, or the input, aren't what an import needs."""


def encoded(spec: dict, value) -> str | None:
    """The value as Key Vault keeps it, or None for one there's nothing of."""
    if value is None or value == "":
        return None
    if spec.get("json"):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value if isinstance(value, str) else json.dumps(value)


def attached_group(sem: Semaphore, project: str) -> str:
    """The group the import template has attached beside "Key Vault Sync"."""
    pid = sem.project_id(project)
    mine = [t for t in sem.call("GET", f"/project/{pid}/templates") or [] if t.get("playbook") == PLAYBOOK]
    if len(mine) != 1:
        raise Wrong(f"{len(mine)} templates run {PLAYBOOK}: keep one")
    ids = mine[0].get("environment_ids") or [mine[0].get("environment_id")]
    names = {e.get("id"): e.get("name") for e in sem.call("GET", f"/project/{pid}/environment") or []}
    others = [names.get(i) for i in ids if names.get(i) != OWN_GROUP]
    if OWN_GROUP not in (names.get(i) for i in ids):
        raise Wrong(f"the template hasn't {OWN_GROUP!r} attached")
    if len(others) != 1:
        raise Wrong(f"the template has {len(others)} groups beside {OWN_GROUP!r} ({others}): attach exactly the one to import")
    return others[0]


def main(stdin: str, env: dict) -> int:
    try:
        doc = json.loads(stdin)
        project, groups, values = doc["project"], doc["groups"], doc["values"]
        dry = doc.get("dry_run") is True
        assert isinstance(groups, dict) and isinstance(values, dict)
    except (ValueError, KeyError, TypeError, AssertionError):
        print("the input isn't {project, groups, values} as JSON (run by semaphore-tool-keyvault-import.yml)")
        return 2
    missing = [v for v in NEEDS if not env.get(v, "").strip()]
    if missing:
        print(f"missing: {', '.join(missing)}: attach {OWN_GROUP!r} to the template beside the group to import")
        return 2
    vault = KeyVault(env)
    try:
        group = doc.get("group") or attached_group(Semaphore(env), project)
        if group == OWN_GROUP:
            raise Wrong(f"{OWN_GROUP!r} is the sync's own group: its values stay in Semaphore")
        if group not in groups:
            raise Wrong(f"{group!r} isn't declared in semaphore_variable_groups.yaml: declare it first")
        vault.login()
    except Wrong as e:
        print(f"stopped: {e}")
        return 2
    except (Unread, RuntimeError, urllib.error.URLError, OSError) as e:
        print(f"stopped: {e}")
        return 1
    print(f"{group}{' (a dry run: nothing written)' if dry else ''}:")
    failed = False
    for kind in KINDS:
        for var, spec in (groups[group].get(kind) or {}).items():
            if "keyvault" not in spec:
                print(f"  {var}: not imported ({'a template' if 'template' in spec else 'a plain value'} in the config)")
                continue
            name, value = spec["keyvault"], encoded(spec, values.get(var))
            if value is None:
                print(f"  {var}: not in this run, so not in the group (or empty): nothing to import")
                failed = True
                continue
            try:
                have = vault.get(name)
            except Unread as e:
                if "not in Key Vault" not in str(e):
                    print(f"  {var}: {e}")
                    failed = True
                    continue
                have = None
            if have is not None:
                if have == value:
                    print(f"  {var}: {name} already holds it")
                else:
                    print(f"  {var}: {name} holds a different value: left as it is; compare the two by hand")
                    failed = True
                continue
            if dry:
                print(f"  {var}: would write {name}")
                continue
            status = vault.put(name, value)
            if status == 403:
                print(f"  {var}: Key Vault refused writing {name} (403): the sync's identity needs "
                      "\"Key Vault Secrets Officer\" on the vault for the import")
                failed = True
                continue
            try:
                back = vault.get(name) if status in (200, 201) else None
            except Unread:
                back = None
            if back == value:
                print(f"  {var}: written to {name}, read back")
            else:
                print(f"  {var}: writing {name} didn't take (HTTP {status})")
                failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.stdin.read(), dict(os.environ)))
