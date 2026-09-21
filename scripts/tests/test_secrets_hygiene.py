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

import pytest

from k8s_backup import secretscan as ss

_DATA_KEY_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

SKIP_DIR_NAMES = {".git"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf"}


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

    The one intentional exception is kubernetes/.local/cluster-substitutions-
    secret.yaml: a Secret manifest, but one this tool RENDERS from the
    operator's own kubernetes/.local/variable-substitutions.yaml entries
    (see varsub.py), not something scraped off a live cluster object. It's
    gitignored and exists so Flux's postBuild.substituteFrom has something
    to apply -- allowlisted by exact path, not by weakening this check.
    """
    from k8s_backup.varsub import LOCAL_SECRET_PATH

    allowed_path = repo_root.joinpath(*LOCAL_SECRET_PATH)
    offenders = []
    for path in all_yaml_files:
        if path == allowed_path:
            continue
        try:
            doc = load_yaml(path)
        except Exception:
            continue
        if isinstance(doc, dict) and doc.get("kind") == "Secret":
            offenders.append(path)
    assert not offenders, (
        "found a captured Secret manifest -- this generator must never write Secret "
        f"data/stringData to disk: {[str(p.relative_to(repo_root)) for p in offenders]}"
    )


def test_high_confidence_credential_patterns(repo_root, all_files, allowlist):
    offenders = []
    for path in _text_files(all_files):
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
    offenders = []
    for path in all_yaml_files:
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
    offenders = []
    for path in all_yaml_files:
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
    """Every literal in kubernetes/.local/variable-substitutions.yaml exists
    specifically to NOT appear outside kubernetes/.local/ -- checks every
    git-tracked file directly for each one, rather than only trusting that
    apply_substitutions() was called on the right data structures. This is
    exactly the check that would have caught a real bug found while
    building this: an auto-seeded note field that named the actual domain
    value, which leaked into docs/variable-substitutions.md and
    docs/inventory.md (both git-tracked) before the note wording was fixed.
    """
    from k8s_backup.varsub import load_substitutions

    substitutions = load_substitutions(repo_root)
    if not substitutions:
        pytest.skip("no variable substitutions configured")

    local_root = repo_root / "kubernetes" / ".local"
    offenders = []
    for path in all_files:
        if path.is_relative_to(local_root):
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
