"""Copy the cluster-secrets variables into Azure Key Vault: QUEUE-azure-keyvault.md.

Reads the variables the cluster-secrets ExternalSecret maps (its spec.data, never the ones its
template builds) and their values from the Secret it keeps in flux-system. Each becomes one
Key Vault secret named after its variable with "_" replaced by "-" (MEDIA_SONARR_API_KEY is
MEDIA-SONARR-API-KEY), holding exactly the value the Secret holds: a value held as base64
stays base64. Once cluster-secrets reads Key Vault, a dry run is a check that the two agree.

A dry run (the default) reports, for each name, whether Key Vault lacks it, holds the same
value or holds a different one. --apply writes the missing and different ones, then reads
every name back and compares. Values are compared by SHA-256; they are never printed, logged,
written to disk or passed as arguments, and travel only in memory and HTTPS request bodies.

Nothing is written when the script can't answer: the ExternalSecret isn't Ready, a mapped
variable is missing from the Secret or isn't text, two variables become one Key Vault name
(Key Vault ignores case), a name isn't valid there, or Key Vault refuses or doesn't answer a
read. A write that gets no answer stops the writing; every name is still read back.

--vault names the vault (its URL is https://<name>.vault.azure.net; the name isn't in this
repo). It signs in as the Azure CLI's current account (az login), which needs "Key Vault
Secrets Officer" on the vault to write, or "Key Vault Secrets User" for a dry run. kubectl
uses its current context, or --context.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

NAMESPACE = "flux-system"
EXTERNAL_SECRET_NAME = "cluster-secrets"
API_VERSION = "7.4"
SECRET_NAME = re.compile(r"[A-Za-z0-9-]{1,127}")
VAULT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9-]{1,22}[A-Za-z0-9]")


class Stop(Exception):
    """A state the script can't act on safely: the run ends without writing anything more."""


def keyvault_name(variable: str) -> str:
    """The Key Vault secret name for a cluster-secrets variable: each '_' becomes '-'."""
    return variable.replace("_", "-")


def check_names(variables: list[str]) -> dict[str, str]:
    """Each variable's Key Vault name, refusing one Key Vault won't take and two variables
    that become the same name once case is ignored."""
    names: dict[str, str] = {}
    taken: dict[str, str] = {}
    for variable in variables:
        name = keyvault_name(variable)
        if not SECRET_NAME.fullmatch(name):
            raise Stop(f"{variable}: {name} isn't a valid Key Vault name (letters, digits and '-', 1-127)")
        if name.lower() in taken:
            raise Stop(f"{variable} and {taken[name.lower()]} both become the Key Vault name {name}")
        taken[name.lower()] = variable
        names[variable] = name
    return names


def digest(value: str) -> str:
    """The value's SHA-256, the only form in which a value is ever compared or shown."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def decoded_digest(value: str) -> str | None:
    """The SHA-256 of the text a base64 value decodes to, or None when it isn't base64 of UTF-8
    text: how a value held base64 (for a Secret's data:) is compared with the same one held plain."""
    try:
        return digest(base64.b64decode(value, validate=True).decode("utf-8"))
    except ValueError:  # binascii.Error and UnicodeDecodeError are both ValueErrors
        return None


def kubectl_json(context: str | None, *args: str) -> dict:
    """One kubectl read, as JSON; a failed read stops the run."""
    cmd = ["kubectl", *(["--context", context] if context else []), *args, "-o", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        detail = (result.stderr.strip().splitlines() or ["no output"])[-1]
        raise Stop(f"kubectl {' '.join(args)}: {detail}")
    return json.loads(result.stdout)


def source_values(external_secret: dict, secret: dict) -> dict[str, str]:
    """Every variable the ExternalSecret maps, with its value from the Secret it builds."""
    ready = next((c for c in external_secret.get("status", {}).get("conditions", []) if c.get("type") == "Ready"), {})
    if ready.get("status") != "True":
        raise Stop(f"the {EXTERNAL_SECRET_NAME} ExternalSecret isn't Ready ({ready.get('reason', 'no Ready condition')}), "
                   "so its Secret may not hold Vaultwarden's values")
    variables = [d["secretKey"] for d in external_secret.get("spec", {}).get("data") or []]
    if not variables:
        raise Stop(f"the {EXTERNAL_SECRET_NAME} ExternalSecret maps no variables")
    data = secret.get("data") or {}
    values: dict[str, str] = {}
    for variable in variables:
        if variable not in data:
            raise Stop(f"{variable}: the ExternalSecret maps it but its Secret doesn't hold it")
        try:
            values[variable] = base64.b64decode(data[variable]).decode("utf-8")
        except UnicodeDecodeError as error:
            raise Stop(f"{variable}: its value isn't UTF-8 text, which a Key Vault secret holds") from error
    return values


def access_token() -> str:
    """A Key Vault token for the Azure CLI's signed-in account."""
    az = shutil.which("az")
    if not az:
        raise Stop("the Azure CLI (az) isn't on PATH")
    result = subprocess.run([az, "account", "get-access-token", "--resource", "https://vault.azure.net", "-o", "json"],
                            capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        detail = (result.stderr.strip().splitlines() or ["no output"])[-1]
        raise Stop(f"az account get-access-token: {detail} (run az login first)")
    return json.loads(result.stdout)["accessToken"]


def request(method: str, url: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    """One Key Vault REST call: its HTTP status and JSON body (an error's body too). No answer
    at all (a dropped connection, a timeout, a name that won't resolve) is a Stop: it says
    nothing about the secret."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    name = url.split("/secrets/")[-1].split("?")[0]
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        raw = error.read() or b"{}"
        try:
            return error.code, json.loads(raw)
        except ValueError:
            return error.code, {"error": {"code": "unreadable error body"}}
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        raise Stop(f"{name}: no answer from Key Vault to a {method} ({getattr(error, 'reason', error)})") from error


def error_code(body: dict) -> str:
    return (body.get("error") or {}).get("code", "")


def remote_secret(vault_url: str, name: str, token: str) -> tuple[str, dict, str | None] | None:
    """The SHA-256 of the value Key Vault holds under name, the secret's tags, and the SHA-256
    of what the value decodes to when it is base64 (decoded_digest); None when it holds none."""
    status, body = request("GET", f"{vault_url}/secrets/{urllib.parse.quote(name)}?api-version={API_VERSION}", token)
    if status == 404:
        return None
    if status != 200:
        raise Stop(f"{name}: Key Vault answered a read with HTTP {status} {error_code(body)}")
    value = body.get("value", "")
    return digest(value), body.get("tags") or {}, decoded_digest(value)


def remote_digest(vault_url: str, name: str, token: str) -> str | None:
    """The SHA-256 of the value Key Vault holds under name, or None when it holds none."""
    held = remote_secret(vault_url, name, token)
    return held[0] if held else None


def write(vault_url: str, name: str, value: str, token: str, tags: dict | None = None) -> str:
    """Sets name's value and tags (by default, cluster-secrets'); returns "" when written, else
    why not."""
    status, body = request("PUT", f"{vault_url}/secrets/{urllib.parse.quote(name)}?api-version={API_VERSION}", token,
                           {"value": value, "tags": tags or {"source": "cluster-secrets"}})
    if status == 200:
        return ""
    if status == 409 and error_code(body) == "Conflict":
        return "deleted but recoverable: recover or purge it in Key Vault first"
    return f"HTTP {status} {error_code(body)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vault", required=True, help="the Key Vault's name (not its URL)")
    parser.add_argument("--context", help="the kubectl context to read from (default: the current one)")
    parser.add_argument("--apply", action="store_true", help="write what differs; without it, only report")
    args = parser.parse_args(argv)

    try:
        if not VAULT_NAME.fullmatch(args.vault):
            raise Stop(f"--vault: {args.vault!r} isn't a Key Vault name (3-24 letters, digits and '-')")
        vault_url = f"https://{args.vault}.vault.azure.net"
        external_secret = kubectl_json(args.context, "-n", NAMESPACE, "get", "externalsecret", EXTERNAL_SECRET_NAME)
        target = (external_secret.get("spec", {}).get("target") or {}).get("name") or EXTERNAL_SECRET_NAME
        secret = kubectl_json(args.context, "-n", NAMESPACE, "get", "secret", target)
        values = source_values(external_secret, secret)
        names = check_names(list(values))
        token = access_token()
        state = {}
        for variable, name in names.items():
            have = remote_digest(vault_url, name, token)
            state[variable] = "add" if have is None else ("same" if have == digest(values[variable]) else "update")
    except Stop as stop:
        print(f"stopped, nothing written: {stop}", file=sys.stderr)
        return 2

    counts = {k: sum(1 for s in state.values() if s == k) for k in ("add", "same", "update")}
    print(f"{len(state)} variables: {counts['add']} missing from Key Vault, {counts['update']} different, "
          f"{counts['same']} already the same")
    for variable, action in state.items():
        if action != "same":
            print(f"  {action:6s} {names[variable]}")
    if not args.apply:
        print("dry run: nothing written (--apply writes)")
        return 0

    failed = 0
    for variable, action in state.items():
        if action == "same":
            continue
        try:
            why = write(vault_url, names[variable], values[variable], token)
        except Stop as stop:
            failed += 1
            print(f"  NOT WRITTEN {names[variable]}: {stop}; no further writes")
            break
        if why:
            failed += 1
            print(f"  NOT WRITTEN {names[variable]}: {why}")
    mismatched = []
    for variable, name in names.items():
        try:
            if remote_digest(vault_url, name, token) != digest(values[variable]):
                mismatched.append(name)
        except Stop as stop:
            mismatched.append(f"{name} ({stop})")
    print(f"read back: {len(names) - len(mismatched)} of {len(names)} match the Secret")
    for name in mismatched:
        print(f"  MISMATCH {name}")
    return 1 if failed or mismatched else 0


if __name__ == "__main__":
    sys.exit(main())
