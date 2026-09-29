"""Variable substitution for environment-specific literals in git-tracked
captured files (an internal hostname, an ACME account email, ...).

This is deliberately separate from secretscan.py's redaction: redaction is
about material that must never be written to disk anywhere (a credential),
so it always applies, everywhere, automatically-detected. Substitution is
about literals that are fine to have on disk locally but that you'd rather
not repeat verbatim across every git-tracked file. Two ways an entry gets
here:

1. Auto-seeded (auto_seed(), source: auto) for a narrow, curated set of
   fields known to carry identity-bearing info: a ClusterIssuer's ACME
   account email, and a PersistentVolume's NFS server when it's a hostname
   rather than a bare IP (the plain private IPs used elsewhere are treated
   as ordinary, expected infra detail -- see docs/inventory.md's IP
   section). This is deliberately narrow rather than generic detection,
   which would be unreliable and noisy.
2. Hand-added by the operator for anything else.

Either way, only git-tracked output gets substituted -- kubernetes/.local/
stays full-fidelity for an actual restore -- and the real values live only
in this gitignored file plus the ready-to-apply ConfigMap/Secret rendered
alongside it, never in git.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import yamlio

LOCAL_SUBSTITUTIONS_PATH = ("kubernetes", ".local", "variable-substitutions.yaml")
LOCAL_CONFIGMAP_PATH = ("kubernetes", ".local", "cluster-substitutions-configmap.yaml")
LOCAL_SECRET_PATH = ("kubernetes", ".local", "cluster-substitutions-secret.yaml")

SUBSTITUTIONS_CONFIGMAP_NAME = "cluster-substitutions"
# Same name as the ConfigMap (a Secret and a ConfigMap may share one): this is
# what the live cluster has, and what every hand-written ks.yaml references.
SUBSTITUTIONS_SECRET_NAME = "cluster-substitutions"

_EXAMPLE_TEMPLATE = """\
# Local, gitignored: exact literal values to replace with a placeholder in
# every git-tracked captured file (values.yaml/values-all.yaml, raw/ and
# cluster/ manifests, namespace.yaml). Never committed -- that would defeat
# the point. Entries marked `source: auto` were detected automatically (see
# varsub.auto_seed); add your own for anything else you'd rather not repeat
# verbatim across the repo. `sensitivity: secret` entries render into
# kubernetes/.local/cluster-substitutions-secret.yaml instead of the
# ConfigMap, for kubernetes/flux/config/cluster-resources.yaml's
# postBuild.substituteFrom to pick up once you apply it to the cluster.
#
# - literal: internal-host.example.lan
#   placeholder: "${EXAMPLE_HOSTNAME}"
#   note: what this is and why it's templated
#   sensitivity: configmap
"""

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def local_substitutions_path(root: Path) -> Path:
    return root.joinpath(*LOCAL_SUBSTITUTIONS_PATH)


def ensure_example_scaffold(root: Path, dry_run: bool) -> None:
    path = local_substitutions_path(root)
    if path.exists() or dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_EXAMPLE_TEMPLATE, encoding="utf-8", newline="\n")


def _load_raw_entries(root: Path) -> list:
    path = local_substitutions_path(root)
    if not path.exists():
        return []
    try:
        data = yamlio.read_yaml_file(path)
    except Exception:
        return []
    return list(data) if data else []


_BARE_VAR_NAME = re.compile(r"^[_A-Za-z][_A-Za-z0-9]*$")


def normalize_placeholder(placeholder: str) -> str:
    """`NAME` -> `${NAME}`. A bare name is just ordinary text once it's in a
    manifest: capture swaps the literal for it and Flux's postBuild
    substitution, which only expands `${NAME}`, never swaps it back.
    """
    return f"${{{placeholder}}}" if _BARE_VAR_NAME.match(placeholder) else placeholder


def bare_placeholders(root: Path) -> list[str]:
    """Placeholders in the local file written without the `${...}` wrapper
    (load_substitutions() wraps them), so the operator can be told to fix it.
    """
    return [
        str(e.get("placeholder"))
        for e in _load_raw_entries(root)
        if isinstance(e, dict) and e.get("placeholder") and _BARE_VAR_NAME.match(str(e["placeholder"]))
    ]


def load_substitutions(root: Path) -> list[dict]:
    """Every entry, each with `replace`: False for a config-only entry
    (`replace: false` in the file) -- a value Flux substitutes, like
    MEDIA_PUID, but not a literal to find and replace in captured output
    (replacing every "1000" in the repo would be nonsense). Config-only
    entries still render into the local ConfigMap/Secret, so applying those
    files never drops a key the cluster relies on.
    """
    entries = []
    for entry in _load_raw_entries(root):
        if not isinstance(entry, dict):
            continue
        literal = entry.get("literal")
        placeholder = entry.get("placeholder")
        replace = entry.get("replace", True) is not False
        # A config-only value may legitimately be empty (a suffix that's
        # blank in production); an empty literal to replace would match
        # everywhere.
        if not placeholder or literal is None or (replace and not literal):
            continue
        entries.append(
            {
                "literal": str(literal),
                "placeholder": normalize_placeholder(str(placeholder)),
                "sensitivity": entry.get("sensitivity", "configmap"),
                "note": entry.get("note", ""),
                "replace": replace,
            }
        )
    return entries


def _replaceable(substitutions: list[dict]) -> list[dict]:
    return [s for s in substitutions if s.get("replace", True)]


def apply_substitutions(obj: Any, substitutions: list[dict]) -> int:
    """Mutate `obj` in place, replacing every occurrence of each literal
    with its placeholder in any string leaf (exact match or as a substring
    of a longer string, e.g. an email embedded in a larger sentence).
    Returns the number of leaf values touched. Config-only entries
    (`replace: false`) are never replaced.
    """
    substitutions = _replaceable(substitutions)
    if not substitutions:
        return 0
    # Longest literal first: if both "user@example.com" and "example.com"
    # are substituted, the shorter domain-only literal would otherwise
    # consume part of the email first (it's a substring of it), leaving a
    # mangled "user@${DOMAIN}" instead of "${EMAIL}" -- order-dependent and
    # surprising. Trying the most specific (longest) match first avoids that.
    ordered = sorted(substitutions, key=lambda s: len(s["literal"]), reverse=True)
    count = 0
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            val = obj[key]
            if isinstance(val, str):
                new_val = val
                for sub in ordered:
                    if sub["literal"] in new_val:
                        new_val = new_val.replace(sub["literal"], sub["placeholder"])
                if new_val != val:
                    obj[key] = new_val
                    count += 1
            else:
                count += apply_substitutions(val, substitutions)
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            if isinstance(val, str):
                new_val = val
                for sub in ordered:
                    if sub["literal"] in new_val:
                        new_val = new_val.replace(sub["literal"], sub["placeholder"])
                if new_val != val:
                    obj[i] = new_val
                    count += 1
            else:
                count += apply_substitutions(val, substitutions)
    return count


def apply_substitutions_text(text: str, substitutions: list[dict]) -> str:
    """Same as apply_substitutions but for a plain rendered string (a .md
    doc, not a YAML tree) -- a second, defense-in-depth pass over
    docs/inventory.md and docs/variable-substitutions.md so a note/warning
    string that happens to embed a literal value (a real bug found and
    fixed once already: an auto-seeded note that named the actual domain)
    still can't leak it into a git-tracked file.
    """
    substitutions = _replaceable(substitutions)
    if not substitutions:
        return text
    for sub in sorted(substitutions, key=lambda s: len(s["literal"]), reverse=True):
        text = text.replace(sub["literal"], sub["placeholder"])
    return text


def _find_strings_matching(obj: Any, pattern: re.Pattern) -> list[str]:
    found = []
    if isinstance(obj, dict):
        for val in obj.values():
            found.extend(_find_strings_matching(val, pattern))
    elif isinstance(obj, list):
        for val in obj:
            found.extend(_find_strings_matching(val, pattern))
    elif isinstance(obj, str) and pattern.match(obj):
        found.append(obj)
    return found


def _extract_ingress_hostnames(ingress_items: list[dict]) -> list[str]:
    hosts = []
    for ing in ingress_items:
        spec = ing.get("spec", {})
        for rule in spec.get("rules", []) or []:
            if rule.get("host"):
                hosts.append(rule["host"])
        for tls in spec.get("tls", []) or []:
            for h in tls.get("hosts", []) or []:
                hosts.append(h)
    return hosts


def _registrable_domain(hostname: str) -> str:
    """Last two labels of a hostname (auth.example.com -> example.com).

    A real public-suffix list (example.co.uk needing three labels, not
    two) would be more correct, but this is a homelab tool operating on a
    single operator's own domain(s), not a multi-tenant SaaS -- the naive
    heuristic is a reasonable, auditable tradeoff here rather than a new
    dependency, and a wrong grouping only means a candidate domain doesn't
    get flagged, never that something is substituted incorrectly.
    """
    parts = hostname.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else hostname


def _dominant_domains(ingress_items: list[dict], min_hosts: int = 2) -> list[str]:
    """Registrable domains used across at least `min_hosts` distinct
    Ingress hostnames -- a domain that shows up once might be a one-off
    external reference; one used repeatedly across your own apps is very
    likely your own domain.
    """
    counts: dict[str, set[str]] = {}
    for host in _extract_ingress_hostnames(ingress_items):
        domain = _registrable_domain(host)
        counts.setdefault(domain, set()).add(host)
    return [domain for domain, hosts in counts.items() if len(hosts) >= min_hosts]


def _next_placeholder(base: str, taken: set[str]) -> str:
    candidate = f"${{{base}}}"
    if candidate not in taken:
        return candidate
    i = 2
    while f"${{{base}_{i}}}" in taken:
        i += 1
    return f"${{{base}_{i}}}"


def auto_seed(root: Path, cluster_raw: dict, ingress_items: list[dict], dry_run: bool) -> list[str]:
    """Detect candidates from a curated set of known identity-bearing
    fields (ACME account emails, non-IP NFS server hostnames, and a
    dominant Ingress domain -- see _dominant_domains) and append any not
    already present (by literal value) to the local substitutions file.
    Never touches an existing entry -- only adds new ones -- so a
    placeholder name or sensitivity you've hand-edited is never overwritten.
    Returns a human-readable description of what was added, for the run's
    warning/summary output; never returns or logs the literal values
    themselves beyond what's written to the gitignored file.
    """
    if dry_run:
        return []

    # Imported here (not at module load) to avoid a capture.py <-> varsub.py
    # <-> secretscan.py import cycle; classify_ip has no dependency back on
    # this module.
    from . import secretscan

    existing = _load_raw_entries(root)
    existing_literals = {e.get("literal") for e in existing if isinstance(e, dict)}
    existing_placeholders = {e.get("placeholder") for e in existing if isinstance(e, dict)}

    candidates: list[tuple[str, str, str, str]] = []  # (literal, base_name, sensitivity, note)

    for issuer in cluster_raw.get("clusterissuer.cert-manager.io", []):
        acme = issuer.get("spec", {}).get("acme", {})
        name = issuer.get("metadata", {}).get("name", "?")
        for email in _find_strings_matching(acme, _EMAIL_RE):
            candidates.append((email, "ACME_EMAIL", "secret", f"ACME account email (ClusterIssuer {name})"))

    for pv in cluster_raw.get("persistentvolume", []):
        server = pv.get("spec", {}).get("nfs", {}).get("server")
        pv_name = pv.get("metadata", {}).get("name", "?")
        if server and secretscan.classify_ip(server) is None:
            candidates.append((server, "NFS_HOSTNAME", "configmap", f"NFS server hostname (PV {pv_name})"))

    for domain in _dominant_domains(ingress_items):
        # Note text intentionally excludes the domain itself: it ends up in
        # docs/variable-substitutions.md (git-tracked) and in this run's
        # warnings (which flow into git-tracked docs/inventory.md), and the
        # whole point of this entry is to keep that value out of git.
        candidates.append((domain, "BASE_DOMAIN", "configmap", "domain used across multiple Ingress hosts"))

    new_entries = []
    added_descriptions = []
    seen_this_run: set[str] = set()
    for literal, base_name, sensitivity, note in candidates:
        if literal in existing_literals or literal in seen_this_run:
            continue
        seen_this_run.add(literal)
        placeholder = _next_placeholder(base_name, existing_placeholders)
        existing_placeholders.add(placeholder)
        new_entries.append(
            {
                "literal": literal,
                "placeholder": placeholder,
                "note": note,
                "sensitivity": sensitivity,
                "source": "auto",
            }
        )
        added_descriptions.append(f"{placeholder} ({note})")

    if not new_entries:
        return []

    _write_entries(root, existing + new_entries)
    return added_descriptions


def sync_secret_entries(root: Path, wanted: dict[str, tuple[str, str]], source: str, dry_run: bool) -> list[str]:
    """Make sure every placeholder in `wanted` ({placeholder: (literal,
    note)}) has a `sensitivity: secret` entry holding that literal: adds
    missing ones, and updates ones added earlier with the same `source`
    whose literal has changed (a rotated password). An entry written by
    hand for the same placeholder is left alone. Never removes one -- an
    entry disappearing would blank a value Flux still substitutes.
    Returns what changed, by placeholder and note only, never a value.
    """
    if dry_run or not wanted:
        return []
    entries = _load_raw_entries(root)
    by_placeholder = {
        normalize_placeholder(str(e["placeholder"])): e
        for e in entries
        if isinstance(e, dict) and e.get("placeholder")
    }
    changes = []
    for placeholder, (literal, note) in wanted.items():
        entry = by_placeholder.get(placeholder)
        if entry is None:
            entries.append(
                {"literal": literal, "placeholder": placeholder, "note": note, "sensitivity": "secret", "source": source}
            )
            changes.append(f"added {placeholder} ({note})")
        elif entry.get("source") == source and str(entry.get("literal")) != literal:
            entry["literal"] = literal
            changes.append(f"updated {placeholder} ({note}): the live value changed")
    if changes:
        _write_entries(root, entries)
    return changes


def _write_entries(root: Path, entries: list) -> None:
    path = local_substitutions_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Local, gitignored: exact literal values replaced with a placeholder in every\n"
        "# git-tracked captured file. Entries marked `source: auto` were detected\n"
        "# automatically (ACME emails, non-IP NFS server hostnames); `source: auto-secret`\n"
        "# entries hold a promoted Secret's values (base64, as the Secret stores them).\n"
        "# `replace: false` marks a config-only variable: rendered for Flux to substitute,\n"
        "# never searched for and replaced in captured files (e.g. MEDIA_PUID).\n"
        "# Add your own for anything else. Never committed -- that would defeat the point.\n"
    )
    yamlio.write_yaml_file(path, entries, header=header)


def _var_name(placeholder: str) -> str:
    return placeholder.strip("${}")


def render_configmap(substitutions: list[dict]) -> dict | None:
    data = {
        _var_name(s["placeholder"]): s["literal"]
        for s in substitutions
        if s.get("sensitivity", "configmap") != "secret"
    }
    if not data:
        return None
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": SUBSTITUTIONS_CONFIGMAP_NAME, "namespace": "flux-system"},
        "data": data,
    }


def render_secret(substitutions: list[dict]) -> dict | None:
    data = {_var_name(s["placeholder"]): s["literal"] for s in substitutions if s.get("sensitivity") == "secret"}
    if not data:
        return None
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {"name": SUBSTITUTIONS_SECRET_NAME, "namespace": "flux-system"},
        "stringData": data,
    }
