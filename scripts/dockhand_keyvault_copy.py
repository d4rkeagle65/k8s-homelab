"""Copy Dockhand's stack variables into Azure Key Vault under their standard names
(scripts/README.md's "Add or change a substitution variable").

Reads the JSON that scripts/dockhand-export-secrets.cjs writes inside the Dockhand container (a
list of {environment, stack, key, value}, one per secret stack variable, decrypted) and
docker/secret-names.yaml, which gives each stack variable's Key Vault secret. Every exported
variable must be in that map, as one of:

  NAME            the Key Vault secret NAME, "_" as "-": written from the export when Key Vault
                  lacks it; when Key Vault holds it, its value must be the same (plain, or base64
                  of it, which the stack can read only once Key Vault holds it plain)
  {built: "..."}  not stored: the compose file builds it from the template's ${NAME}s, each held
                  by Key Vault or written by this run, and the filled-in template must give
                  exactly the exported value
  {none: "..."}   not stored and not read, for the reason given

A dry run (the default) reports each name's state, each built form's check, the values Key
Vault also holds under another name, and each stack's references: the names its compose file
reads, which Dockhand sets to azurekv://<NAME>. --apply writes only the names Key Vault lacks,
tagged source=dockhand, and reads each back; it never changes a secret Key Vault already holds.
Values are compared by SHA-256 and never printed; they travel only in memory and HTTPS request
bodies.

Nothing is written when the script can't answer, or when the answer needs a decision: a
variable the map doesn't name, an entry of another shape, two variables that become one name
with different values, a name Key Vault holds with a different value or disabled, a built form
that gives a different value or reads a name nothing holds, or Key Vault refusing or not
answering a read. A value that is already a secret-provider reference, and Dockhand's own
bulk-pull selectors, are skipped with the reason.

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

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

import keyvault_copy as kv

MAP = Path(__file__).resolve().parents[1] / "docker" / "secret-names.yaml"
SOURCE_TAG = "dockhand"
# Dockhand's bulk-pull selectors (BULK_SELECTOR_VARS in its stacks.ts): settings, not secrets.
SELECTORS = {"DOCKHAND_SECRET_SELECTOR", "OP_ENVIRONMENT_ID"}
# The inline references Dockhand's secret providers resolve; a value that is one isn't a secret.
REFERENCE = re.compile(r"(azurekv|op|keepass|pass|psono)://")
VARIABLE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
PLACEHOLDER = re.compile(r"\$\{([A-Za-z][A-Za-z0-9_]*)\}")


def keyvault_name(name: str) -> str:
    """A standard name's Key Vault secret: each '_' as '-'."""
    return name.replace("_", "-")


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


def check_name(name: object, where: str) -> str:
    if not (isinstance(name, str) and VARIABLE.fullmatch(name) and kv.SECRET_NAME.fullmatch(keyvault_name(name))):
        raise kv.Stop(f"{where}: {name!r} isn't a variable name Key Vault can hold")
    return name


def read_map(path: Path) -> dict[str, dict[str, tuple]]:
    """Per stack, each variable's entry: ("name", NAME), ("built", template, [NAME...]) or
    ("none", reason)."""
    try:
        data = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise kv.Stop(f"{path}: {error.strerror}") from error
    except YAMLError as error:
        raise kv.Stop(f"{path} isn't YAML ({type(error).__name__})") from error
    if not isinstance(data, dict):
        raise kv.Stop(f"{path} isn't a map of stacks")
    rules: dict[str, dict[str, tuple]] = {}
    for stack, variables in data.items():
        if not isinstance(stack, str) or not isinstance(variables, dict):
            raise kv.Stop(f"{path}: {stack!r} isn't a stack's map of variables")
        rules[stack] = {}
        for key, entry in variables.items():
            where = f"{path}: {stack} {key}"
            if not isinstance(key, str):
                raise kv.Stop(f"{where}: a variable is named by text")
            if isinstance(entry, str):
                rules[stack][key] = ("name", check_name(entry, where))
            elif isinstance(entry, dict) and len(entry) == 1 and isinstance(entry.get("built"), str):
                template = entry["built"]
                names = [check_name(n, where) for n in PLACEHOLDER.findall(template)]
                if not names:
                    raise kv.Stop(f"{where}: a built form with no ${{NAME}} is a constant; give it as {{none: ...}}")
                if "$" in PLACEHOLDER.sub("", template):
                    raise kv.Stop(f"{where}: a built form takes ${{NAME}}s and nothing else with a $")
                rules[stack][key] = ("built", template, names)
            elif isinstance(entry, dict) and len(entry) == 1 and isinstance(entry.get("none"), str) and entry["none"].strip():
                rules[stack][key] = ("none", entry["none"].strip())
            else:
                raise kv.Stop(f"{where}: an entry is a NAME, {{built: \"...\"}} or {{none: \"reason\"}}")
    return rules


def plan(entries: list[dict], rules: dict[str, dict[str, tuple]]) -> tuple[dict, list, list, dict]:
    """The Key Vault names the export fills ({value, readers}), the built forms, the variables
    skipped with why, and each stack's references. A variable the map doesn't name, or two
    variables that become one name with different values, stop the run."""
    targets: dict[str, dict] = {}
    by_lower: dict[str, str] = {}
    built: list[dict] = []
    skipped: list[str] = []
    refs: dict[str, set[str]] = {}
    unmapped: list[str] = []
    for entry in entries:
        stack, key, value = entry["stack"], entry["key"], entry["value"]
        reader = f"{stack} {key}"
        if key in SELECTORS:
            skipped.append(f"{reader}: Dockhand's bulk-pull selector, not a secret")
            continue
        if is_reference(value):
            skipped.append(f"{reader}: already a secret-provider reference")
            continue
        rule = rules.get(stack, {}).get(key)
        if rule is None:
            unmapped.append(reader)
            continue
        if rule[0] == "none":
            skipped.append(f"{reader}: {rule[1]}")
            continue
        if rule[0] == "built":
            built.append({"reader": reader, "template": rule[1], "names": rule[2], "value": value})
            refs.setdefault(stack, set()).update(rule[2])
            continue
        name = rule[1]
        refs.setdefault(stack, set()).add(name)
        first = by_lower.setdefault(keyvault_name(name).lower(), name)
        if first != name:
            raise kv.Stop(f"{name} and {first} are one name to Key Vault, which ignores case")
        target = targets.setdefault(name, {"value": value, "readers": []})
        if target["value"] != value:
            raise kv.Stop(f"{reader} and {target['readers'][0]} both become {name}, with different values")
        target["readers"].append(reader)
    if unmapped:
        raise kv.Stop(f"not in the map: {', '.join(unmapped)}; give each its name first")
    return targets, built, skipped, refs


def vault_contents(vault: str, vault_url: str, token: str) -> tuple[dict[str, tuple[str, dict, str | None]], set[str]]:
    """Every enabled secret in the vault, by name, as keyvault_copy.remote_secret reads it; and
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


def held_value(vault_url: str, name: str, token: str) -> str:
    """The value Key Vault holds under name, for filling in a built form; never printed."""
    status, body = kv.request("GET", f"{vault_url}/secrets/{urllib.parse.quote(name)}?api-version={kv.API_VERSION}", token)
    if status != 200:
        raise kv.Stop(f"{name}: Key Vault answered a read with HTTP {status} {kv.error_code(body)}")
    return body.get("value", "")


def states(targets: dict, held: dict, disabled: set[str]) -> tuple[dict[str, str], list[str]]:
    """Each name's state: add (Key Vault lacks it), same, or base64 (held as base64 of the same
    value); and the conflicts: a name disabled or held with another value."""
    held_lower = {name.lower(): name for name in held}
    disabled_lower = {name.lower() for name in disabled}
    state: dict[str, str] = {}
    conflicts: list[str] = []
    for name, target in targets.items():
        vault_name = keyvault_name(name).lower()
        if vault_name in disabled_lower:
            conflicts.append(f"{keyvault_name(name)} is disabled in Key Vault")
        elif vault_name not in held_lower:
            state[name] = "add"
        else:
            value_digest, _, decoded = held[held_lower[vault_name]]
            want = kv.digest(target["value"])
            if value_digest == want:
                state[name] = "same"
            elif decoded == want:
                state[name] = "base64"
            else:
                conflicts.append(f"{keyvault_name(name)}: Key Vault holds another value than {target['readers'][0]}")
    return state, conflicts


def check_built(built: list[dict], targets: dict, held: dict, vault_url: str, token: str) -> tuple[list[str], list[str]]:
    """Each built form filled in from Key Vault (or from this run's names) and compared with the
    exported value: the readers whose form gives the same value, and the conflicts."""
    held_lower = {name.lower(): name for name in held}
    values: dict[str, str | None] = {}
    matching: list[str] = []
    conflicts: list[str] = []
    for item in built:
        missing = []
        for name in item["names"]:
            if name not in values:
                vault_name = keyvault_name(name).lower()
                values[name] = (held_value(vault_url, held_lower[vault_name], token) if vault_name in held_lower
                                else targets[name]["value"] if name in targets else None)
            if values[name] is None:
                missing.append(name)
        if missing:
            conflicts.append(f"{item['reader']}: its built form reads {', '.join(missing)}, which nothing holds")
        elif kv.digest(PLACEHOLDER.sub(lambda m: values[m.group(1)], item["template"])) == kv.digest(item["value"]):
            matching.append(item["reader"])
        else:
            conflicts.append(f"{item['reader']}: its built form gives another value than Dockhand holds")
    return matching, conflicts


def shared_values(targets: dict, held: dict) -> list[str]:
    """Each group of Key Vault names holding one value, where the group has one of this run's
    names in it, a secret holding it base64 marked so; empty values, which match by nothing but
    being empty, are left out."""
    target_lower = {keyvault_name(name).lower() for name in targets}
    groups: dict[str, set[str]] = {}
    for name, target in targets.items():
        groups.setdefault(kv.digest(target["value"]), set()).add(keyvault_name(name))
    for name, (value_digest, _, decoded) in held.items():
        if name.lower() in target_lower:
            continue
        if value_digest in groups:
            groups[value_digest].add(name)
        elif decoded in groups:
            groups[decoded].add(f"{name} (base64)")
    empty = kv.digest("")
    return sorted(" = ".join(sorted(names, key=str.lower)) for d, names in groups.items() if len(names) > 1 and d != empty)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", type=Path, help="the JSON dockhand-export-secrets.cjs wrote")
    parser.add_argument("--vault", required=True, help="the Key Vault's name (not its URL)")
    parser.add_argument("--map", type=Path, default=MAP, help="the names map (default: docker/secret-names.yaml)")
    parser.add_argument("--apply", action="store_true", help="write the names Key Vault lacks; without it, only report")
    args = parser.parse_args(argv)

    try:
        if not kv.VAULT_NAME.fullmatch(args.vault):
            raise kv.Stop(f"--vault: {args.vault!r} isn't a Key Vault name (3-24 letters, digits and '-')")
        vault_url = f"https://{args.vault}.vault.azure.net"
        rules = read_map(args.map)
        entries = read_export(args.export)
        targets, built, skipped, refs = plan(entries, rules)
        token = kv.access_token()
        held, disabled = vault_contents(args.vault, vault_url, token)
        state, conflicts = states(targets, held, disabled)
        matching, built_conflicts = check_built(built, targets, held, vault_url, token)
        conflicts += built_conflicts
    except kv.Stop as stop:
        print(f"stopped, nothing written: {stop}", file=sys.stderr)
        return 2

    counts = {k: sum(1 for s in state.values() if s == k) for k in ("add", "same", "base64")}
    stacks = {e["stack"] for e in entries}
    print(f"{len(entries)} stack variables from {len(stacks)} stacks: {len(targets)} Key Vault names "
          f"({counts['add']} to add, {counts['same']} held with the same value, {counts['base64']} held base64), "
          f"{len(built)} built, {len(skipped)} skipped")
    for name, s in state.items():
        if s != "same":
            print(f"  {s:6s} {keyvault_name(name)}  ({', '.join(targets[name]['readers'])})")
    for reader in matching:
        print(f"  built  {reader}: its built form gives the same value")
    for reason in skipped:
        print(f"  skip   {reason}")
    exported = {(e["stack"], e["key"]) for e in entries}
    unused = [f"{stack} {key}" for stack, keys in rules.items() for key in keys if (stack, key) not in exported]
    if unused:
        print(f"in the map but not in the export: {', '.join(unused)}")
    if conflicts:
        print("conflicts (nothing is written until each is settled):")
        for conflict in conflicts:
            print(f"  {conflict}")
    shared = shared_values(targets, held)
    if shared:
        print("the same value under more than one Key Vault name:")
        for group in shared:
            print(f"  {group}")

    failed = 0
    mismatched: list[str] = []
    written = [name for name, s in state.items() if s == "add"]
    if args.apply and not conflicts:
        for name in written:
            try:
                why = kv.write(vault_url, keyvault_name(name), targets[name]["value"], token, {"source": SOURCE_TAG})
            except kv.Stop as stop:
                failed += 1
                print(f"  NOT WRITTEN {keyvault_name(name)}: {stop}; no further writes")
                break
            if why:
                failed += 1
                print(f"  NOT WRITTEN {keyvault_name(name)}: {why}")
        for name in written:
            try:
                secret = kv.remote_secret(vault_url, keyvault_name(name), token)
            except kv.Stop as stop:
                mismatched.append(f"{keyvault_name(name)} ({stop})")
                continue
            if secret is None or secret[0] != kv.digest(targets[name]["value"]) or secret[1].get("source") != SOURCE_TAG:
                mismatched.append(keyvault_name(name))
        print(f"read back: {len(written) - len(mismatched)} of {len(written)} written names match the export, value and tag")
        for name in mismatched:
            print(f"  MISMATCH {name}")

    print("each stack's references: the names its compose file reads, set in Dockhand to")
    for stack in sorted(refs):
        for name in sorted(refs[stack]):
            note = "  (once Key Vault holds it plain)" if state.get(name) == "base64" else ""
            print(f"  {stack} {name} = azurekv://{keyvault_name(name)}{note}")
    if conflicts:
        print(f"nothing written: {len(conflicts)} conflict(s)" if args.apply else "dry run: nothing written")
        return 2 if args.apply else 1
    if not args.apply:
        print("dry run: nothing written (--apply writes the names Key Vault lacks)")
        return 0
    return 1 if failed or mismatched else 0


if __name__ == "__main__":
    sys.exit(main())
