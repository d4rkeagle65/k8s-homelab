"""Variable substitution: keep private literals (the domain, IPs, promoted
Secrets' values, ...) out of git-tracked captured files by replacing each
with a ${NAME} placeholder that Flux substitutes back at apply time.

The values live in two objects in flux-system that every Flux
Kustomization reads (postBuild.substituteFrom):

- `cluster-secrets`: a Secret built by External Secrets from the custom
  fields of one Vaultwarden item (kubernetes/secrets/cluster-secrets.yaml).
  Every non-empty value here is a literal capture replaces.
- `cluster-settings`: a ConfigMap committed in git
  (kubernetes/flux/meta/vars/cluster-settings.yaml) for plain settings like
  MEDIA_PUID. Substituted by Flux, never searched for (replacing every
  "1000" would be nonsense).

capture reads both from the live cluster (from_live). A new private value
it finds -- an auto-detected domain (auto_seed_candidates) or a promoted
Secret's value -- is replaced in this run's output at once, and written to
the gitignored kubernetes/.local/vaultwarden-pending.yaml to be added to
Vaultwarden by hand. Each run also writes the full list to the gitignored
kubernetes/.local/substitutions-cache.yaml, which the pre-commit hook's
leak check reads without cluster access.

Before cluster-secrets existed, the values lived in the gitignored
kubernetes/.local/variable-substitutions.yaml (load_substitutions); capture
still falls back to it on a cluster without cluster-secrets.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import constants, yamlio

LOCAL_SUBSTITUTIONS_PATH = ("kubernetes", ".local", "variable-substitutions.yaml")
CACHE_PATH = ("kubernetes", ".local", "substitutions-cache.yaml")
PENDING_PATH = ("kubernetes", ".local", "vaultwarden-pending.yaml")

SECRETS_SECRET_NAME = constants.SECRETS_SECRET_NAME
SETTINGS_CONFIGMAP_NAME = constants.SETTINGS_CONFIGMAP_NAME


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def local_substitutions_path(root: Path) -> Path:
    return root.joinpath(*LOCAL_SUBSTITUTIONS_PATH)


def _load_raw_entries(root: Path, path: Path | None = None) -> list:
    path = path or local_substitutions_path(root)
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


def load_substitutions(root: Path, path: Path | None = None) -> list[dict]:
    """The legacy local file (or `path`, e.g. the cache): every entry, each with `replace`: False for a config-only entry
    (`replace: false` in the file) -- a value Flux substitutes, like
    MEDIA_PUID, but not a literal to find and replace in captured output
    (replacing every "1000" in the repo would be nonsense). Config-only
    entries still render into the local ConfigMap/Secret, so applying those
    files never drops a key the cluster relies on.
    """
    entries = []
    for entry in _load_raw_entries(root, path):
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


def auto_seed_candidates(cluster_raw: dict, ingress_items: list[dict], known: list[dict]) -> list[dict]:
    """Private literals found in a curated set of identity-bearing fields
    (ACME account emails, non-IP NFS server hostnames, and a dominant Ingress
    domain -- see _dominant_domains) that aren't among `known` yet, as new
    entries with a free placeholder name. The caller replaces them in this
    run's output and lists them for Vaultwarden; nothing is written here.
    """
    # Imported here (not at module load) to avoid a capture.py <-> varsub.py
    # <-> secretscan.py import cycle; classify_ip has no dependency back on
    # this module.
    from . import secretscan

    known_literals = {s["literal"] for s in known}
    taken = {s["placeholder"] for s in known}

    candidates: list[tuple[str, str, str]] = []  # (literal, base_name, note)
    for issuer in cluster_raw.get("clusterissuer.cert-manager.io", []):
        acme = issuer.get("spec", {}).get("acme", {})
        name = issuer.get("metadata", {}).get("name", "?")
        for email in _find_strings_matching(acme, _EMAIL_RE):
            candidates.append((email, "ACME_EMAIL", f"ACME account email (ClusterIssuer {name})"))

    for pv in cluster_raw.get("persistentvolume", []):
        server = pv.get("spec", {}).get("nfs", {}).get("server")
        pv_name = pv.get("metadata", {}).get("name", "?")
        if server and secretscan.classify_ip(server) is None:
            candidates.append((server, "NFS_HOSTNAME", f"NFS server hostname (PV {pv_name})"))

    for domain in _dominant_domains(ingress_items):
        # Note text intentionally excludes the domain itself: notes reach
        # this run's warnings, which flow into git-tracked docs/inventory.md.
        candidates.append((domain, "BASE_DOMAIN", "domain used across multiple Ingress hosts"))

    new_entries = []
    for literal, base_name, note in candidates:
        if literal in known_literals:
            continue
        known_literals.add(literal)
        placeholder = _next_placeholder(base_name, taken)
        taken.add(placeholder)
        new_entries.append(
            {"literal": literal, "placeholder": placeholder, "sensitivity": "secret", "note": note, "replace": True}
        )
    return new_entries


def from_live(secret: dict | None, settings: dict | None) -> list[dict]:
    """Substitution entries from the live cluster-secrets Secret (private:
    replaced wherever found, unless empty) and cluster-settings ConfigMap
    (plain settings: never replaced). Every name is included, empty or not,
    so capture knows which ${NAME}s Flux will substitute.
    """
    import base64

    entries = []
    for name, value in sorted(((settings or {}).get("data") or {}).items()):
        entries.append(
            {"literal": str(value), "placeholder": f"${{{name}}}", "sensitivity": "configmap", "note": "", "replace": False}
        )
    for name, value in sorted(((secret or {}).get("data") or {}).items()):
        literal = base64.b64decode(value).decode("utf-8", errors="replace")
        entries.append(
            {"literal": literal, "placeholder": f"${{{name}}}", "sensitivity": "secret", "note": "", "replace": bool(literal)}
        )
    return entries


SETTINGS_PATH = ("kubernetes", "flux", "meta", "vars", "cluster-settings.yaml")

PENDING_HEADER = (
    "# Local, gitignored, written by capture: private values Vaultwarden doesn't hold yet.\n"
    "# Add each as a hidden custom field of the `cluster-secrets` item (field name = name),\n"
    "# and an entry in kubernetes/secrets/cluster-secrets.yaml, then re-run capture.\n"
    "# capture deletes this file once none are left.\n"
)
CACHE_HEADER = (
    "# Local, gitignored, rewritten by every capture from the live cluster-secrets and\n"
    "# cluster-settings (plus any values still pending for Vaultwarden). Don't edit:\n"
    "# change the Vaultwarden item or kubernetes/flux/meta/vars/cluster-settings.yaml.\n"
)


def load_settings(root: Path) -> dict | None:
    """The cluster-settings ConfigMap as committed in git -- where those
    values live, so it's read from the repo, not the cluster.
    """
    path = root.joinpath(*SETTINGS_PATH)
    return yamlio.read_yaml_file(path) if path.is_file() else None


def pending_rows(entries: list[dict]) -> list[dict]:
    """kubernetes/.local/vaultwarden-pending.yaml: the values Vaultwarden
    doesn't hold yet, for the operator to add (capture writes it with
    PENDING_HEADER, through its file tracker so it's removed once empty).
    """
    return [{"name": _var_name(e["placeholder"]), "value": e["literal"], "note": e.get("note", "")} for e in entries]


def cache_rows(entries: list[dict]) -> list[dict]:
    """kubernetes/.local/substitutions-cache.yaml: every substitution a
    capture used, for the pre-commit leak check (test_secrets_hygiene),
    which has no cluster access.
    """
    return [{"literal": e["literal"], "placeholder": e["placeholder"], "replace": e.get("replace", True)} for e in entries]


def load_cache(root: Path) -> list[dict]:
    """What the last capture used: the cache, or the legacy local file on a
    checkout that hasn't run capture since cluster-secrets existed.
    """
    cache = root.joinpath(*CACHE_PATH)
    return load_substitutions(root, cache if cache.exists() else None)


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
