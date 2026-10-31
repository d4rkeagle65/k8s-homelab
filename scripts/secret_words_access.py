"""Give one service principal exactly the Key Vault roles secret_words.py needs, each on a single
secret, as secret_words.toml names them: "Key Vault Secrets User" on each variable's secret and
"Key Vault Secrets Officer" on the list secret, the only one it writes. Any other role it holds
on the vault or one of its secrets is extra. A role it holds above the vault (its resource
group, the subscription) is reported and left alone: it reaches more than these secrets, and
taking it away is outside this script.

A dry run (the default) reports what is missing and what is extra. --apply adds the missing and
removes the extra, then reads the principal's roles back and compares. Nothing is changed when
a read can't answer: a secret the config names that the vault lacks, a role or the principal
that can't be found, or Azure refusing or not answering.

--app-id is the principal's application (client) ID; by default, the value of the Key Vault
secret ENTRA-CLAUDE-CLOUD-CLIENT-ID. The vault is found as secret_words.py finds it. It signs in
as the Azure CLI's account (az login), which needs Owner or User Access Administrator on the
vault for --apply, and to read the vault and its role assignments for a dry run.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

import keyvault_copy as kv
import secret_words as sw

ARM = "https://management.azure.com"
AUTH_API = "2022-04-01"
VAULT_API = "2023-07-01"
CLIENT_ID_NAME = "ENTRA-CLAUDE-CLOUD-CLIENT-ID"
READER, WRITER = "Key Vault Secrets User", "Key Vault Secrets Officer"


def az(*args: str) -> str:
    """One Azure CLI read, its output as text; a failure is a Stop."""
    path = shutil.which("az")
    if not path:
        raise kv.Stop("the Azure CLI (az) isn't on PATH")
    result = subprocess.run([path, *args], capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        detail = (result.stderr.strip().splitlines() or ["no output"])[-1]
        raise kv.Stop(f"az {' '.join(args[:3])}: {detail}")
    return result.stdout.strip()


def arm(method: str, path: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    """One Azure Resource Manager call: its status and JSON body. No answer is a Stop."""
    req = urllib.request.Request(ARM + path, data=json.dumps(body).encode("utf-8") if body is not None else None,
                                 method=method,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read() or b"{}")
        except ValueError:
            return error.code, {"error": {"code": "unreadable error body"}}
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        raise kv.Stop(f"no answer from Azure to a {method} ({getattr(error, 'reason', error)})") from error


def arm_ok(method: str, path: str, token: str, what: str, body: dict | None = None) -> dict:
    status, answer = arm(method, path, token, body)
    if status not in (200, 201, 204):
        raise kv.Stop(f"{what}: Azure answered HTTP {status} {kv.error_code(answer)}")
    return answer


def wanted(vault_id: str, list_secret: str, variables: list[str]) -> dict[str, str]:
    """Each secret's scope with the role the principal should hold there."""
    scopes = {f"{vault_id}/secrets/{name}": READER for name in kv.check_names(variables).values()}
    scopes[f"{vault_id}/secrets/{list_secret}"] = WRITER
    return scopes


def role_ids(vault_id: str, token: str) -> dict[str, str]:
    """The two roles' definition IDs, by name, as the vault sees them."""
    ids = {}
    for role in (READER, WRITER):
        query = urllib.parse.quote(f"roleName eq '{role}'")
        found = arm_ok("GET", f"{vault_id}/providers/Microsoft.Authorization/roleDefinitions?$filter={query}"
                              f"&api-version={AUTH_API}", token, role).get("value") or []
        if len(found) != 1:
            raise kv.Stop(f"{role}: {len(found)} role definitions found, not one")
        ids[role] = found[0]["id"]
    return ids


def held(vault_id: str, principal: str, token: str) -> list[dict]:
    """The principal's role assignments at, above and below the vault."""
    query = urllib.parse.quote(f"principalId eq '{principal}'")
    return arm_ok("GET", f"{vault_id}/providers/Microsoft.Authorization/roleAssignments?$filter={query}"
                         f"&api-version={AUTH_API}", token, "the principal's role assignments").get("value") or []


def guid(role_definition_id: str) -> str:
    """A role definition's GUID: the same role reads as one ID at the subscription and another
    path elsewhere."""
    return role_definition_id.rsplit("/", 1)[-1].lower()


def compare(vault_id: str, want: dict[str, str], ids: dict[str, str], assignments: list[dict]):
    """(missing: scope -> role, extra: assignments at or below the vault, above: assignments
    above it). Scopes compare without case, as Azure treats them."""
    role_of = {guid(i): role for role, i in ids.items()}
    wanted_at = {s.lower(): r for s, r in want.items()}
    inside = vault_id.lower()
    have, extra, above = set(), [], []
    for a in assignments:
        scope = a["properties"]["scope"].lower()
        role = role_of.get(guid(a["properties"]["roleDefinitionId"]))
        if scope != inside and not scope.startswith(inside + "/"):
            above.append(a)
        elif role is not None and wanted_at.get(scope) == role:
            have.add(scope)
        else:
            extra.append(a)
    missing = {s: r for s, r in want.items() if s.lower() not in have}
    return missing, extra, above


def describe(vault_id: str, assignment: dict, ids: dict[str, str]) -> str:
    """An assignment as its role and place, never its IDs."""
    role = {guid(i): r for r, i in ids.items()}.get(guid(assignment["properties"]["roleDefinitionId"]),
                                                     "a role other than these two")
    scope, inside = assignment["properties"]["scope"], vault_id.lower()
    if scope.lower() == inside:
        place = "the vault"
    elif scope.lower().startswith(inside + "/secrets/"):
        place = scope.rsplit("/", 1)[1]
    elif scope.lower().startswith(inside + "/"):
        place = "part of the vault"
    else:
        place = "a scope above the vault"
    return f"{role} on {place}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vault", help="the Key Vault's name (default: as secret_words.py finds it)")
    parser.add_argument("--app-id", help=f"the principal's application ID (default: Key Vault's {CLIENT_ID_NAME})")
    parser.add_argument("--apply", action="store_true", help="add the missing roles and remove the extra ones")
    args = parser.parse_args(argv)

    try:
        list_secret, variables = sw.load_config()
        url = sw.vault_url(args.vault)
        app_id = args.app_id or (sw.read_secret(url, CLIENT_ID_NAME, kv.access_token()) or "").strip()
        if not app_id:
            raise kv.Stop(f"{CLIENT_ID_NAME} is empty; name the principal with --app-id")
        principal = az("ad", "sp", "show", "--id", app_id, "--query", "id", "-o", "tsv")
        name = url.split("//", 1)[1].split(".", 1)[0]
        vault_id = az("keyvault", "show", "--name", name, "--query", "id", "-o", "tsv")
        token = az("account", "get-access-token", "--resource", ARM, "--query", "accessToken", "-o", "tsv")
        want = wanted(vault_id, list_secret, variables)
        for scope in want:
            arm_ok("GET", f"{scope}?api-version={VAULT_API}", token, scope.rsplit("/", 1)[1] + " in the vault")
        ids = role_ids(vault_id, token)
        missing, extra, above = compare(vault_id, want, ids, held(vault_id, principal, token))
    except kv.Stop as stop:
        print(f"stopped, nothing changed: {stop}", file=sys.stderr)
        return 2

    print(f"{len(want)} secrets: {len(want) - len(missing)} with their role, {len(missing)} missing it; "
          f"{len(extra)} extra roles on the vault, {len(above)} above it")
    for scope, role in missing.items():
        print(f"  missing  {role} on {scope.rsplit('/', 1)[1]}")
    for a in extra:
        print(f"  extra    {describe(vault_id, a, ids)}")
    for a in above:
        print(f"  above    {describe(vault_id, a, ids)} (left alone: remove it in Azure)")
    if not args.apply:
        print("dry run: nothing changed (--apply changes)")
        return 0

    failed = 0
    try:
        for scope, role in missing.items():
            body = {"properties": {"roleDefinitionId": ids[role], "principalId": principal, "principalType": "ServicePrincipal"}}
            status, answer = arm("PUT", f"{scope}/providers/Microsoft.Authorization/roleAssignments/{uuid.uuid4()}"
                                        f"?api-version={AUTH_API}", token, body)
            if status not in (200, 201):
                failed += 1
                print(f"  NOT ADDED {role} on {scope.rsplit('/', 1)[1]}: HTTP {status} {kv.error_code(answer)}")
        for a in extra:
            status, answer = arm("DELETE", f"{a['id']}?api-version={AUTH_API}", token)
            if status not in (200, 204):
                failed += 1
                print(f"  NOT REMOVED {describe(vault_id, a, ids)}: HTTP {status} {kv.error_code(answer)}")
        missing, extra, above = compare(vault_id, want, ids, held(vault_id, principal, token))
    except kv.Stop as stop:
        print(f"stopped part way: {stop}; run it again to see where it stands", file=sys.stderr)
        return 2
    print(f"read back: {len(want) - len(missing)} of {len(want)} secrets with their role, "
          f"{len(extra)} extra on the vault, {len(above)} above it")
    return 1 if failed or missing or extra or above else 0


if __name__ == "__main__":
    sys.exit(main())
