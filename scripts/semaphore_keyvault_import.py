"""Copy one Semaphore variable group's values into Key Vault, once: run by
ansible/semaphore-tool-keyvault-import.yml ("Tool: Variable Group → Key Vault (one time)";
QUEUE-semaphore-variables.md).

Semaphore never returns a secret's value through its API, but hands a task its variable
groups' values: the playbook, run with "Key Vault Sync" and the group to import, gathers that
group's variables and passes them here on standard input, as JSON, never in a file:

  {"group": "<name>", "declared": {<the group's entry in semaphore_variable_groups.yaml>},
   "values": {"<variable>": <its value in the task, or null when the task hasn't it>}}

Each variable the config takes from Key Vault is written to its secret there (JSON for one
marked json: true), unless Key Vault already holds it: the same value is left as it is, a
different one is reported and never overwritten. Each write is read back. A variable built
from a template or a plain value isn't imported. Nothing is printed but names.

Writing needs "Key Vault Secrets Officer" for the run; "Key Vault Secrets User", the sync's
own, refuses it (403), and this says so. Its environment: AZURE_TENANT_ID, AZURE_CLIENT_ID,
AZURE_CLIENT_SECRET, AZURE_KEYVAULT_URL ("Key Vault Sync"'s). Exit 0: every value is in Key
Vault, as the group has it; 1: one isn't (missing in the task, different, refused, didn't
read back); 2: the input or the environment is wrong.
"""
from __future__ import annotations

import json
import os
import sys

from semaphore_variable_groups import KINDS, NEEDS, OWN_GROUP, KeyVault, Unread


def encoded(spec: dict, value) -> str | None:
    """The value as Key Vault keeps it, or None for one there's nothing of."""
    if value is None or value == "":
        return None
    if spec.get("json"):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value if isinstance(value, str) else json.dumps(value)


def main(stdin: str, env: dict) -> int:
    try:
        doc = json.loads(stdin)
        group, declared, values = doc["group"], doc["declared"], doc["values"]
        assert isinstance(declared, dict) and isinstance(values, dict)
    except (ValueError, KeyError, TypeError, AssertionError):
        print("the input isn't {group, declared, values} as JSON (run by semaphore-tool-keyvault-import.yml)")
        return 2
    if group == OWN_GROUP:
        print(f"{OWN_GROUP!r} is the sync's own group: its values stay in Semaphore")
        return 2
    missing = [v for v in NEEDS if v.startswith("AZURE_") and not env.get(v, "").strip()]
    if missing:
        print(f"missing: {', '.join(missing)}: attach {OWN_GROUP!r} to the template beside the group to import")
        return 2
    vault = KeyVault(env)
    try:
        vault.login()
    except Unread as e:
        print(f"stopped: {e}")
        return 1
    print(f"{group}:")
    failed = False
    for kind in KINDS:
        for var, spec in (declared.get(kind) or {}).items():
            if "keyvault" not in spec:
                print(f"  {var}: not imported ({'a template' if 'template' in spec else 'a plain value'} in the config)")
                continue
            name, value = spec["keyvault"], encoded(spec, values.get(var))
            if value is None:
                print(f"  {var}: not in this task, so not in the group (or empty): nothing to import")
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
