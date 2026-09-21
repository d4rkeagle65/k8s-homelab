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
SUBSTITUTIONS_SECRET_NAME = "cluster-substitutions-secret"

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


def load_substitutions(root: Path) -> list[dict]:
    entries = []
    for entry in _load_raw_entries(root):
        if not isinstance(entry, dict):
            continue
        literal = entry.get("literal")
        placeholder = entry.get("placeholder")
        if literal and placeholder:
            entries.append(
                {
                    "literal": str(literal),
                    "placeholder": str(placeholder),
                    "sensitivity": entry.get("sensitivity", "configmap"),
                    "note": entry.get("note", ""),
                }
            )
    return entries


def apply_substitutions(obj: Any, substitutions: list[dict]) -> int:
    """Mutate `obj` in place, replacing every occurrence of each literal
    with its placeholder in any string leaf (exact match or as a substring
    of a longer string, e.g. an email embedded in a larger sentence).
    Returns the number of leaf values touched.
    """
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

    combined = existing + new_entries
    path = local_substitutions_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Local, gitignored: exact literal values replaced with a placeholder in every\n"
        "# git-tracked captured file. Entries marked `source: auto` were detected\n"
        "# automatically (ACME emails, non-IP NFS server hostnames); add your own for\n"
        "# anything else. Never committed -- that would defeat the point.\n"
    )
    yamlio.write_yaml_file(path, combined, header=header)
    return added_descriptions


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
