"""Secret / credential / sensitive-data hygiene checks.

This is a SECOND, independent gate -- capture.py already runs the same
detectors (k8s_backup/secretscan.py) and redacts before anything is
written to disk, and the pre-commit hook (.githooks/pre-commit) runs this
whole suite before `git commit` is allowed to complete. This file exists
in case something reaches the repo a different way: a hand-edit to
values.yaml, a resource kind added to capture.py later without wiring up
redaction for it, or a `--no-verify` commit bypassing the hook.

A failure here means "a human needs to look at this," not necessarily
"the generator is broken." Confirmed-safe matches (a legitimately
high-entropy but non-secret value) belong in
docs/secrets-scan-allowlist.txt, never a code change to weaken the check
for everyone.
"""

from __future__ import annotations

import csv
import re
import subprocess

import pytest

from k8s_backup import secretscan as ss

_DATA_KEY_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

SKIP_DIR_NAMES = {".git"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf"}


def _gitignored(repo_root, paths):
    """The subset of `paths` git ignores. Empty if git isn't available or
    this isn't a git checkout, so the caller then checks everything.
    """
    # NUL-delimited (-z): text-mode stdin on Windows would turn "\n" into
    # "\r\n" and git would then match nothing.
    rels = "\0".join(p.relative_to(repo_root).as_posix() for p in paths)
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "check-ignore", "-z", "--stdin"],
            input=rels.encode("utf-8"), capture_output=True,
        )
    except OSError:
        return set()
    return {repo_root / rel for rel in proc.stdout.decode("utf-8").split("\0") if rel}


def _local_secret_store(repo_root, paths):
    """Files under kubernetes/.local/ that git ignores: the local-only store
    that is SUPPOSED to hold real values -- the rendered cluster-substitutions
    Secret, variable-substitutions.yaml with promoted Secrets' values, the
    full-fidelity cluster backup. The content scans below skip them, but
    only while git really ignores them: the moment .local/ could be
    committed, they're scanned again.
    """
    local_root = repo_root / "kubernetes" / ".local"
    local = [p for p in paths if p.is_relative_to(local_root)]
    return _gitignored(repo_root, local) if local else set()


def _text_files(all_files):
    for path in all_files:
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path


def test_no_banned_key_material_file_extensions(all_files, repo_root):
    bad = [p for p in all_files if p.suffix.lower() in ss.BANNED_EXTENSIONS]
    assert not bad, (
        "found file(s) with a private-key-material extension, which this repo "
        f"should never contain: {[str(p.relative_to(repo_root)) for p in bad]}"
    )


def test_no_secret_manifests_captured(all_yaml_files, load_yaml, repo_root):
    """No Secret manifest may be CAPTURED FROM THE CLUSTER -- the generator
    never fetches "secret" as a resource kind anywhere in capture.py, only
    metadata via _capture_secrets() into docs/secrets-inventory.csv.

    One intentional exception is the legacy, gitignored
    kubernetes/.local/cluster-substitutions-secret.yaml, which older captures
    rendered from the operator's own local file (and the next capture
    deletes) -- allowlisted by exact path, not by weakening this check.

    A promoted Secret (promote.templated_secret) is the other: every value
    is exactly one ${VAR} placeholder (or empty), the real value living only
    in Vaultwarden. One literal value anywhere and it's an offender.
    """
    placeholder = re.compile(r"^\$\{[_A-Za-z][_A-Za-z0-9]*\}$")

    def only_placeholders(doc):
        values = [*(doc.get("data") or {}).values(), *(doc.get("stringData") or {}).values()]
        return all(v in ("", None) or (isinstance(v, str) and placeholder.match(v)) for v in values)

    allowed_path = repo_root / "kubernetes" / ".local" / "cluster-substitutions-secret.yaml"
    offenders = []
    for path in all_yaml_files:
        if path == allowed_path:
            continue
        try:
            doc = load_yaml(path)
        except Exception:
            continue
        if isinstance(doc, dict) and doc.get("kind") == "Secret" and not only_placeholders(doc):
            offenders.append(path)
    assert not offenders, (
        "found a captured Secret manifest -- this generator must never write Secret "
        f"data/stringData to disk: {[str(p.relative_to(repo_root)) for p in offenders]}"
    )


def test_high_confidence_credential_patterns(repo_root, all_files, allowlist):
    local_store = _local_secret_store(repo_root, all_files)
    offenders = []
    for path in _text_files(all_files):
        if path in local_store:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if text in allowlist:
            continue
        found = ss.scan_text_for_high_confidence_patterns(text)
        if found:
            offenders.append((path.relative_to(repo_root), found))
    assert not offenders, (
        "high-confidence credential pattern(s) found -- if genuinely a false "
        "positive, add the exact matched string to docs/secrets-scan-allowlist.txt; "
        f"otherwise remove/rotate it: {offenders}"
    )


def test_secrets_inventory_csv_columns_have_no_leaked_values(repo_root):
    path = repo_root / "docs" / "secrets-inventory.csv"
    if not path.exists():
        pytest.skip("no secrets-inventory.csv yet; run `capture` first")
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == ["namespace", "name", "type", "keys", "created"]
        for row in reader:
            for key in row["keys"].split(";"):
                if not key:
                    continue
                assert _DATA_KEY_NAME_RE.match(key), (
                    f"secrets-inventory.csv row for {row['namespace']}/{row['name']} has a "
                    f"'keys' entry that doesn't look like a data key name: {key!r} -- this "
                    "column must only ever hold key NAMES, never values"
                )


def test_values_files_have_no_plaintext_credentials(repo_root, load_yaml, allowlist):
    offenders = []
    for path in sorted(repo_root.glob("kubernetes/apps/*/*/app/values*.yaml")):
        try:
            data = load_yaml(path)
        except Exception:
            continue
        if not data:
            continue
        for field_path, value in ss.find_suspicious_plaintext_values(data):
            if value in allowlist:
                continue
            offenders.append((path.relative_to(repo_root), field_path))
    assert not offenders, (
        "found a password/secret/token-like key holding a literal string value "
        "(as opposed to a secretKeyRef-style indirection) -- if this is really just "
        "a reference/identifier and not credential material, add its exact value to "
        f"docs/secrets-scan-allowlist.txt: {offenders}"
    )


def test_no_high_entropy_secrets_in_captured_manifests(repo_root, all_yaml_files, load_yaml, allowlist):
    local_store = _local_secret_store(repo_root, all_yaml_files)
    offenders = []
    for path in all_yaml_files:
        if path in local_store:
            continue
        try:
            data = load_yaml(path)
        except Exception:
            continue
        if not data:
            continue
        for field_path, value in ss.find_high_entropy_strings(data):
            if value in allowlist:
                continue
            offenders.append((path.relative_to(repo_root), field_path))
    assert not offenders, (
        "found a high-entropy base64-ish string in a captured manifest -- this is "
        "how a raw/ resource can end up carrying a real credential even though this "
        "generator never captures Secret objects directly (e.g. a token passed as a "
        "container command-line argument). Rotate the credential if it's real, or "
        f"add its exact value to docs/secrets-scan-allowlist.txt if it's not: {offenders}"
    )


def test_no_env_var_credentials_in_captured_manifests(repo_root, all_yaml_files, load_yaml, allowlist):
    """`env: [{name: DB_PASSWORD, value: literal}]` in a captured Deployment/
    CronJob/etc -- the single most common way a credential ends up baked
    into a pod spec instead of behind `valueFrom.secretKeyRef`.
    """
    local_store = _local_secret_store(repo_root, all_yaml_files)
    offenders = []
    for path in all_yaml_files:
        if path in local_store:
            continue
        try:
            data = load_yaml(path)
        except Exception:
            continue
        if not data:
            continue
        for field_path, value in ss.find_env_var_credentials(data):
            if value in allowlist:
                continue
            offenders.append((path.relative_to(repo_root), field_path))
    assert not offenders, (
        "found an env/args/command entry whose name looks like a credential and "
        "whose value is a literal string (not a secretKeyRef) -- rotate/move it to "
        f"a Secret, or allowlist the exact value if it's a confirmed false positive: {offenders}"
    )


def test_no_substitution_literal_leaks_outside_local(repo_root, all_files):
    """Every private value capture replaces (as of its last run: the
    gitignored kubernetes/.local/substitutions-cache.yaml, written from the
    live cluster-secrets) exists specifically to NOT appear outside kubernetes/.local/ -- checks every
    git-tracked file directly for each one, rather than only trusting that
    apply_substitutions() was called on the right data structures. This is
    exactly the check that would have caught a real bug found while
    building this: an auto-seeded note field that named the actual domain
    value, which leaked into docs/variable-substitutions.md and
    docs/inventory.md (both git-tracked) before the note wording was fixed.
    """
    from k8s_backup.varsub import load_cache

    # Config-only entries (`replace: false`, e.g. MEDIA_PUID) are values for
    # Flux to substitute, not literals kept out of git -- "1000" may appear anywhere.
    substitutions = [s for s in load_cache(repo_root) if s["replace"]]
    if not substitutions:
        pytest.skip("no variable substitutions configured")

    local_root = repo_root / "kubernetes" / ".local"
    ignored = _gitignored(repo_root, all_files)
    offenders = []
    for path in all_files:
        if path.is_relative_to(local_root) or path in ignored:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for sub in substitutions:
            if sub["literal"] in text:
                offenders.append((path.relative_to(repo_root), sub["placeholder"]))
    assert not offenders, (
        "a variable-substitution literal leaked into a git-tracked file outside "
        f"kubernetes/.local/: {offenders}"
    )


def test_no_public_ip_addresses(repo_root, all_yaml_files, load_yaml, allowlist):
    """Private/RFC1918 addresses are expected, load-bearing content in a
    homelab infra backup (this repo documents its own MetalLB pool, NFS
    server, etc. by IP). A PUBLIC address is different: it's specific
    enough to identify a physical network, so it's treated as a real
    finding rather than routine inventory (see docs/inventory.md for the
    private-IP inventory, which is informational, not a test).
    """
    offenders = []
    for path in all_yaml_files:
        try:
            data = load_yaml(path)
        except Exception:
            continue
        if not data:
            continue
        for field_path, ip, classification in ss.find_ip_addresses(data):
            if classification != "public" or ip in allowlist:
                continue
            offenders.append((path.relative_to(repo_root), field_path, ip))
    assert not offenders, (
        "found a public (non-RFC1918) IP address in a captured manifest -- if this "
        "is intentional (e.g. a public DNS target that's supposed to be here), "
        f"add the exact IP to docs/secrets-scan-allowlist.txt: {offenders}"
    )


_PLACEHOLDER_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")
_HOSTNAME_LABEL = "kubernetes.io/hostname"
# local-path-provisioner's catch-all entry; a keyword, not a node.
_NODE_PATH_MAP_DEFAULT = "DEFAULT_PATH_FOR_NON_LISTED_NODES"


def _node_names(obj, path=""):
    """(field path, value) for every field that names a Kubernetes node:
    `nodeName`, a `kubernetes.io/hostname` nodeSelector or affinity term,
    and local-path-provisioner's `nodePathMap[].node`.
    """
    if isinstance(obj, dict):
        if isinstance(obj.get("nodeName"), str):
            yield f"{path}.nodeName", obj["nodeName"]
        if isinstance(obj.get(_HOSTNAME_LABEL), str):
            yield f"{path}.{_HOSTNAME_LABEL}", obj[_HOSTNAME_LABEL]
        if obj.get("key") == _HOSTNAME_LABEL and isinstance(obj.get("values"), list):
            for i, value in enumerate(obj["values"]):
                yield f"{path}.values[{i}]", value
        if isinstance(obj.get("nodePathMap"), list):
            for i, entry in enumerate(obj["nodePathMap"]):
                if isinstance(entry, dict) and entry.get("node") != _NODE_PATH_MAP_DEFAULT:
                    yield f"{path}.nodePathMap[{i}].node", entry.get("node")
        for key, value in obj.items():
            yield from _node_names(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from _node_names(value, f"{path}[{i}]")


def test_node_names_are_placeholders(repo_root, all_yaml_files):
    """Node names are hostnames, which stay out of this public repo. Unlike
    test_no_substitution_literal_leaks_outside_local this needs no local
    file, so it also runs in a fresh clone: every field that names a node
    must be a ${VARIABLE} from the cluster-substitutions ConfigMap.
    """
    local_root = repo_root / "kubernetes" / ".local"
    ignored = _gitignored(repo_root, all_yaml_files)
    offenders = []
    for path in all_yaml_files:
        if path.is_relative_to(local_root) or path in ignored:
            continue
        try:
            docs = list(_yaml_all(path))
        except Exception:
            continue
        for doc in docs:
            for field_path, value in _node_names(doc):
                if not (isinstance(value, str) and _PLACEHOLDER_RE.match(value)):
                    offenders.append((path.relative_to(repo_root).as_posix(), field_path))
    assert not offenders, (
        "a node name is written out in a git-tracked file; use a ${VARIABLE} "
        f"placeholder instead (the value is not shown here): {offenders}"
    )


def _yaml_all(path):
    from ruamel.yaml import YAML

    return YAML(typ="safe").load_all(path.read_text(encoding="utf-8"))
