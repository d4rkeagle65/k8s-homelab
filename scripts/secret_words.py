"""Keep this clone's secret-words list current from Azure Key Vault.

The list holds the words no commit may contain; the pre-commit hook and
tests/test-identifying-data.py read it. It is written to the git folder every worktree of
the clone shares (<git common dir>/secret-words), so one copy serves them all and git never
tracks it. Its words come from Key Vault, as secret_words.toml names them: the current value
of each cluster-secrets variable it lists, and one Key Vault secret holding the other words,
one a line ('#' starts a comment).

With no option it reads both and rewrites the list. --check reads them and compares, writing
nothing. --add-from FILE (- for stdin) adds FILE's words to the Key Vault list, reads it back,
then rewrites the local one, so a word never travels as an argument. --if-older HOURS reads
Key Vault only when the list is that old or older, and says when it didn't.

Nothing is written unless every read answered: a variable Key Vault lacks or refuses, a
missing list secret or no answer stops the run and leaves the old list as it was, since a
shorter list would pass words the old one blocked. A word shorter than MIN_LENGTH would match
ordinary text, so it is left out and named by its source. Output gives counts and names,
never a word.

The vault is --vault, else AZURE_KEYVAULT_URL, else the azure-keyvault-bootstrap Secret in
flux-system (kubectl's current context). The sign-in is keyvault_copy.py's: a service
principal from AZURE_CLIENT_ID, AZURE_CLIENT_SECRET and AZURE_TENANT_ID (in a cloud session,
AZURE_CLIENT_SECRET_BY_PROXY in place of the secret), else az login. Reading needs "Key Vault
Secrets User"; --add-from needs "Key Vault Secrets Officer". secret_words_access.py gives a
principal those roles on exactly these secrets.
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import subprocess
import sys
import time
import tomllib
import urllib.parse
from pathlib import Path

import keyvault_copy as kv

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "secret_words.toml"
LIST_NAME = "secret-words"
MIN_LENGTH = 4
VAULT_URL = re.compile(r"https://[A-Za-z][A-Za-z0-9-]{1,22}[A-Za-z0-9]\.vault\.azure\.net")
HEADER = ("# Written by scripts/secret_words.py from Key Vault; the next run rewrites it.\n"
          "# Add a word with --add-from, not here.\n")


def parse(text: str) -> set[str]:
    """The words in a list's text, as the hook reads them: one a line, '#' lines and blank
    lines skipped, case ignored."""
    return {line.strip().lower() for line in text.replace("\r", "").split("\n")
            if line.strip() and not line.lstrip().startswith("#")}


def load_config(path: Path | None = None) -> tuple[str, list[str]]:
    path = path or CONFIG
    with open(path, "rb") as f:
        config = tomllib.load(f)
    list_secret, variables = config.get("key_vault_list"), config.get("variables")
    if not isinstance(list_secret, str) or not kv.SECRET_NAME.fullmatch(list_secret):
        raise kv.Stop(f"{path.name}: key_vault_list isn't a Key Vault name")
    if not isinstance(variables, list) or not all(isinstance(v, str) for v in variables):
        raise kv.Stop(f"{path.name}: variables isn't a list of names")
    kv.check_names(variables)
    return list_secret, variables


def list_path() -> Path:
    """<git common dir>/secret-words for the clone this script is in."""
    result = subprocess.run(["git", "-C", str(HERE), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        detail = (result.stderr.strip().splitlines() or ["no output"])[-1]
        raise kv.Stop(f"git rev-parse --git-common-dir: {detail}")
    return Path(result.stdout.strip()) / LIST_NAME


def vault_url(name: str | None) -> str:
    if name:
        if not kv.VAULT_NAME.fullmatch(name):
            raise kv.Stop(f"--vault: {name!r} isn't a Key Vault name (3-24 letters, digits and '-')")
        return f"https://{name}.vault.azure.net"
    url = os.environ.get("AZURE_KEYVAULT_URL", "").strip()
    if not url:
        secret = kv.kubectl_json(None, "-n", "flux-system", "get", "secret", "azure-keyvault-bootstrap")
        held = (secret.get("data") or {}).get("AZURE_KEYVAULT_URL")
        if not held:
            raise kv.Stop("the azure-keyvault-bootstrap Secret holds no AZURE_KEYVAULT_URL; name the vault with --vault")
        url = base64.b64decode(held).decode("utf-8").strip()
    url = url.rstrip("/")
    if not VAULT_URL.fullmatch(url):
        raise kv.Stop("AZURE_KEYVAULT_URL isn't a Key Vault URL (https://<name>.vault.azure.net)")
    return url


def read_secret(url: str, name: str, token: str, missing_ok: bool = False) -> str | None:
    """A Key Vault secret's value; None when it holds none and missing_ok, else a Stop."""
    status, body = kv.request("GET", f"{url}/secrets/{urllib.parse.quote(name)}?api-version={kv.API_VERSION}", token)
    if status == 404 and missing_ok:
        return None
    if status != 200:
        raise kv.Stop(f"{name}: Key Vault answered a read with HTTP {status} {kv.error_code(body)}")
    return body.get("value", "")


def variable_words(url: str, token: str, variables: list[str]) -> tuple[set[str], list[str]]:
    """Each variable's current value as a word, and the variables whose value is too short
    to list."""
    words, short = set(), []
    for variable, name in kv.check_names(variables).items():
        word = (read_secret(url, name, token) or "").strip().lower()
        if len(word) < MIN_LENGTH:
            short.append(variable)
        else:
            words.add(word)
    return words, short


def listed_words(url: str, token: str, list_secret: str) -> tuple[set[str], list[str]]:
    """The list secret's words, and one entry naming it for each word too short to list."""
    listed = parse(read_secret(url, list_secret, token) or "")
    return {w for w in listed if len(w) >= MIN_LENGTH}, [f"a word in {list_secret}" for w in listed if len(w) < MIN_LENGTH]


def write_list(path: Path, words: set[str]) -> None:
    """Writes the list whole, then swaps it in, so a reader never sees half of one."""
    part = path.with_name(path.name + ".part")
    with open(part, "w", encoding="utf-8", newline="\n") as f:
        f.write(HEADER + "".join(w + "\n" for w in sorted(words)))
    os.replace(part, path)


def local_words(path: Path) -> set[str] | None:
    if not path.is_file():
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        return parse(f.read())


def add_words(url: str, token: str, list_secret: str, words: set[str], from_variables: set[str]) -> tuple[int, int, int, int]:
    """Appends the words neither the list secret nor a variable holds to the list secret and
    reads it back; returns (added, already listed, already a variable's, too short)."""
    held = read_secret(url, list_secret, token, missing_ok=True)
    listed = parse(held or "")
    short = {w for w in words if len(w) < MIN_LENGTH}
    new = sorted(words - listed - from_variables - short)
    counts = (len(new), len(words & listed), len((words & from_variables) - listed), len(short - listed))
    if not new:
        return counts
    text = (held or "").rstrip("\n")
    value = (text + "\n" if text else "") + "\n".join(new) + "\n"
    why = kv.write(url, list_secret, value, token, tags={"source": "secret-words"})
    if why:
        raise kv.Stop(f"{list_secret}: not written: {why}")
    if kv.digest(read_secret(url, list_secret, token) or "") != kv.digest(value):
        raise kv.Stop(f"{list_secret}: written, but Key Vault reads back a different value")
    return counts


def age_hours(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 3600


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vault", help="the Key Vault's name (default: AZURE_KEYVAULT_URL, else the bootstrap Secret)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="compare the list here with Key Vault; write nothing")
    mode.add_argument("--add-from", metavar="FILE", help="add FILE's words (- for stdin) to the Key Vault list")
    parser.add_argument("--if-older", metavar="HOURS", type=float,
                        help="read Key Vault only when the list here is at least HOURS old")
    args = parser.parse_args(argv)
    if args.if_older is not None and (args.check or args.add_from):
        parser.error("--if-older only goes with a plain refresh")

    here = None
    try:
        path = list_path()
        here = local_words(path)
        if args.if_older is not None and here is not None and age_hours(path) < args.if_older:
            print(f"secret words: {len(here)} here, written {age_hours(path):.1f}h ago; "
                  f"Key Vault not read (--if-older {args.if_older:g})")
            return 0
        adding = None
        if args.add_from == "-":
            adding = parse(sys.stdin.read())
        elif args.add_from:
            with open(args.add_from, encoding="utf-8", errors="replace") as f:
                adding = parse(f.read())
        list_secret, variables = load_config()
        url = vault_url(args.vault)
        token = kv.access_token()
        from_variables, short = variable_words(url, token, variables)
        if adding is not None:
            added, already, variable, too_short = add_words(url, token, list_secret, adding, from_variables)
            print(f"{list_secret}: {added} added, {already} already listed, {variable} already a variable's value, "
                  f"{too_short} under {MIN_LENGTH} characters and not added" + ("; read back" if added else ""))
        listed, short_listed = listed_words(url, token, list_secret)
    except (kv.Stop, OSError, tomllib.TOMLDecodeError) as stop:
        state = ("absent, so the commit hook's word rule is not armed" if here is None
                 else f"{len(here)} words, left as it was")
        print(f"secret words: stopped, nothing written here: {stop}. The list here: {state}.", file=sys.stderr)
        return 2

    words = from_variables | listed
    for source in short + short_listed:
        print(f"  left out, under {MIN_LENGTH} characters: {source}")
    if args.check:
        if here is None:
            print(f"secret words: none here; Key Vault holds {len(words)}")
            return 1
        missing, extra = len(words - here), len(here - words)
        print(f"secret words: {len(here)} here, {len(words)} in Key Vault; {missing} missing here, {extra} only here")
        return 1 if missing or extra else 0
    before = here or set()
    try:
        write_list(path, words)
    except OSError as error:
        print(f"secret words: stopped, the list here not rewritten: {error}", file=sys.stderr)
        return 2
    print(f"secret words: {len(words)} ({len(from_variables)} from {len(variables)} variables, "
          f"{len(listed)} from {list_secret}); {len(words - before)} added here, {len(before - words)} removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
