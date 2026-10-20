"""Copy Dockhand's secret stack variables into Azure Key Vault: QUEUE-azure-keyvault.md.

Reads the JSON that scripts/dockhand-export-secrets.cjs writes inside the Dockhand container:
a list of {environment, stack, key, value}, one per secret stack variable, decrypted. Each
becomes one Key Vault secret named <stack>--<key> with each "_" as "-" (the kea stack's
KEA_API_PASSWORD is kea--KEA-API-PASSWORD), tagged with its stack and variable, holding exactly
its value. A stack reads it through the inline reference azurekv://<name>. Dockhand's bulk pull
can't stand in: it hands a stack every secret in the vault under its Key Vault name, which a
compose file can't interpolate (a variable name has no "-"), cluster-secrets' among them.

A dry run (the default) reports, for each variable, whether Key Vault lacks its name, holds the
same value or holds a different one; which values Key Vault also holds under another name, or
another stack variable shares (each is still copied under its own name: drop an entry from the
JSON to reference the other instead); and each stack variable's reference. --apply writes the
missing and different ones, then reads every name back and compares value and tags. Values are
compared by SHA-256 and never printed; they travel only in memory and HTTPS request bodies.

Nothing is written when the script can't answer: the JSON isn't the export's shape, two
variables become one name (Key Vault ignores case) with different values, a name isn't valid in
Key Vault, a name is held by a secret not tagged as that stack variable's, a name's secret is
disabled, or Key Vault refuses or doesn't answer a read. Skipped, each with its reason: a value
that is already a secret-provider reference, and Dockhand's own bulk-pull selectors.

--vault and the sign-in are keyvault_copy.py's: the Azure CLI's account (az login), with "Key
Vault Secrets Officer" to write, or "Key Vault Secrets User" for a dry run. The JSON holds every
value in plain text: delete it once the stacks read Key Vault.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

import keyvault_copy as kv

SOURCE_TAG = "dockhand"
# Dockhand's bulk-pull selectors (BULK_SELECTOR_VARS in its stacks.ts): settings, not secrets.
SELECTORS = {"DOCKHAND_SECRET_SELECTOR", "OP_ENVIRONMENT_ID"}
# The inline references Dockhand's secret providers resolve; a value that is one isn't a secret.
REFERENCE = re.compile(r"(azurekv|op|keepass|pass|psono)://")


def keyvault_name(stack: str, key: str) -> str:
    """The Key Vault name for one stack variable: <stack>--<key>, each '_' as '-'."""
    return f"{stack.replace('_', '-')}--{key.replace('_', '-')}"


def tags_for(stack: str, key: str) -> dict[str, str]:
    return {"source": SOURCE_TAG, "stack": stack, "variable": key}


def is_reference(value: str) -> bool:
    """True for a provider reference, quoted or not, as Dockhand reads one."""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return bool(REFERENCE.match(v))


def read_export(path: Path) -> list[dict]:
    """The export's entries; anything else in the file stops the run (without quoting it)."""
    try:
        entries = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise kv.Stop(f"{path}: {error.strerror}") from error
    except ValueError as error:
        raise kv.Stop(f"{path} isn't JSON") from error
    if not isinstance(entries, list):
        raise kv.Stop(f"{path} isn't the export's list of variables")
    for number, entry in enumerate(entries, 1):
        if not (isinstance(entry, dict)
                and all(isinstance(entry.get(k), str) and entry[k] for k in ("stack", "key"))
                and isinstance(entry.get("value"), str)
                and (entry.get("environment") is None or isinstance(entry["environment"], str))):
            raise kv.Stop(f"{path}: entry {number} isn't {{environment, stack, key, value}}")
    return entries


def plan(entries: list[dict]) -> tuple[dict[str, dict], list[str]]:
    """The secrets to hold, by Key Vault name ({stack, key, value}), and the variables skipped,
    each with why. The same stack variable in two Dockhand environments is one secret when its
    value is the same in both."""
    targets: dict[str, dict] = {}
    by_lower: dict[str, str] = {}
    skipped: list[str] = []
    for entry in entries:
        stack, key, value = entry["stack"], entry["key"], entry["value"]
        if key in SELECTORS:
            skipped.append(f"{stack} {key}: Dockhand's bulk-pull selector, not a secret")
            continue
        if is_reference(value):
            skipped.append(f"{stack} {key}: already a secret-provider reference")
            continue
        name = keyvault_name(stack, key)
        if not kv.SECRET_NAME.fullmatch(name):
            raise kv.Stop(f"{stack} {key}: {name} isn't a valid Key Vault name (letters, digits and '-', 1-127)")
        if name.lower() not in by_lower:
            by_lower[name.lower()] = name
            targets[name] = {"stack": stack, "key": key, "value": value}
            continue
        held = targets[by_lower[name.lower()]]
        if (held["stack"], held["key"]) != (stack, key):
            raise kv.Stop(f"{stack} {key} and {held['stack']} {held['key']} both become the Key Vault name {name}")
        if held["value"] != value:
            raise kv.Stop(f"{stack} {key} differs between Dockhand environments, and both would be {name}")
    return targets, skipped


def vault_contents(vault: str, vault_url: str, token: str) -> tuple[dict[str, tuple[str, dict]], set[str]]:
    """Every enabled secret in the vault, by name, with its value's SHA-256 and its tags; and
    the names of the disabled ones, which can't be read."""
    enabled: dict[str, bool] = {}
    url = f"{vault_url}/secrets?api-version={kv.API_VERSION}&maxresults=25"
    while url:
        # The token goes only to the vault, wherever a page says the list continues.
        if urllib.parse.urlsplit(url).hostname != f"{vault}.vault.azure.net".lower():
            raise kv.Stop("Key Vault's list of secrets continues on another host")
        try:
            status, body = kv.request("GET", url, token)
        except kv.Stop as stop:
            raise kv.Stop("listing the vault's secrets: no answer from Key Vault") from stop
        if status != 200:
            raise kv.Stop(f"listing the vault's secrets: HTTP {status} {kv.error_code(body)}")
        for item in body.get("value") or []:
            enabled[item["id"].rstrip("/").rsplit("/", 1)[-1]] = (item.get("attributes") or {}).get("enabled", True)
        url = body.get("nextLink")
    held = {}
    for name, on in enabled.items():
        if on and (secret := kv.remote_secret(vault_url, name, token)) is not None:
            held[name] = secret
    return held, {name for name, on in enabled.items() if not on}


def owned(tags: dict, target: dict) -> bool:
    """True when a secret's tags say it is this stack variable's copy."""
    return all(tags.get(k) == v for k, v in tags_for(target["stack"], target["key"]).items())


def shared_values(targets: dict[str, dict], held: dict[str, tuple[str, dict]]) -> list[str]:
    """Each group of names holding one value, where the group has a stack variable in it; empty
    values, which match by nothing but being empty, are left out."""
    target_lower = {name.lower() for name in targets}
    groups: dict[str, set[str]] = {}
    for name, target in targets.items():
        groups.setdefault(kv.digest(target["value"]), set()).add(name)
    for name, (value_digest, _) in held.items():
        if name.lower() not in target_lower and value_digest in groups:
            groups[value_digest].add(name)
    empty = kv.digest("")
    return sorted(" = ".join(sorted(names, key=str.lower)) for d, names in groups.items() if len(names) > 1 and d != empty)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", type=Path, help="the JSON dockhand-export-secrets.cjs wrote")
    parser.add_argument("--vault", required=True, help="the Key Vault's name (not its URL)")
    parser.add_argument("--apply", action="store_true", help="write what differs; without it, only report")
    args = parser.parse_args(argv)

    try:
        if not kv.VAULT_NAME.fullmatch(args.vault):
            raise kv.Stop(f"--vault: {args.vault!r} isn't a Key Vault name (3-24 letters, digits and '-')")
        vault_url = f"https://{args.vault}.vault.azure.net"
        targets, skipped = plan(read_export(args.export))
        token = kv.access_token()
        held, disabled = vault_contents(args.vault, vault_url, token)
        held_lower = {name.lower(): name for name in held}
        disabled_lower = {name.lower() for name in disabled}
        state: dict[str, str] = {}
        taken: list[str] = []
        for name, target in targets.items():
            if name.lower() in disabled_lower:
                taken.append(f"{name} is disabled in Key Vault")
            elif name.lower() not in held_lower:
                state[name] = "add"
            else:
                value_digest, tags = held[held_lower[name.lower()]]
                if not owned(tags, target):
                    taken.append(f"{name} is held by a secret not tagged as {target['stack']} {target['key']} "
                                 f"(source: {tags.get('source', 'none')})")
                else:
                    state[name] = "same" if value_digest == kv.digest(target["value"]) else "update"
        if taken:
            raise kv.Stop("; ".join(taken))
    except kv.Stop as stop:
        print(f"stopped, nothing written: {stop}", file=sys.stderr)
        return 2

    counts = {k: sum(1 for s in state.values() if s == k) for k in ("add", "same", "update")}
    stacks = {t["stack"] for t in targets.values()}
    print(f"{len(targets)} stack variables from {len(stacks)} stacks: {counts['add']} missing from Key Vault, "
          f"{counts['update']} different, {counts['same']} already the same; {len(skipped)} skipped")
    for name, action in state.items():
        if action != "same":
            print(f"  {action:6s} {name}")
    for reason in skipped:
        print(f"  skip   {reason}")
    shared = shared_values(targets, held)
    if shared:
        print("the same value under more than one name (each is still copied; drop an entry from the JSON "
              "to reference the other):")
        for group in shared:
            print(f"  {group}")

    failed = 0
    mismatched: list[str] = []
    if args.apply:
        for name, action in state.items():
            if action == "same":
                continue
            target = targets[name]
            try:
                why = kv.write(vault_url, name, target["value"], token, tags_for(target["stack"], target["key"]))
            except kv.Stop as stop:
                failed += 1
                print(f"  NOT WRITTEN {name}: {stop}; no further writes")
                break
            if why:
                failed += 1
                print(f"  NOT WRITTEN {name}: {why}")
        for name, target in targets.items():
            try:
                secret = kv.remote_secret(vault_url, name, token)
            except kv.Stop as stop:
                mismatched.append(f"{name} ({stop})")
                continue
            if secret is None or secret[0] != kv.digest(target["value"]) or not owned(secret[1], target):
                mismatched.append(name)
        print(f"read back: {len(targets) - len(mismatched)} of {len(targets)} match the export, value and tags")
        for name in mismatched:
            print(f"  MISMATCH {name}")

    print("each stack variable's value in Dockhand, with this vault as the stack's secret provider:")
    for name, target in sorted(targets.items(), key=lambda item: (item[1]["stack"], item[1]["key"])):
        print(f"  {target['stack']} {target['key']} = azurekv://{name}")
    if not args.apply:
        print("dry run: nothing written (--apply writes)")
        return 0
    return 1 if failed or mismatched else 0


if __name__ == "__main__":
    sys.exit(main())
