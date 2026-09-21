"""Shared credential/secret/IP detection, used by both the capture pipeline
(to redact before anything is ever written to disk) and the test suite
(scripts/tests/test_secrets_hygiene.py, as a second, independent gate).

Both consumers import from here rather than duplicating patterns, so a
tightened rule protects the repo whether it's caught at generation time or
at commit time.
"""

from __future__ import annotations

import ipaddress
import math
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# High-confidence credential patterns. Each of these matching anywhere in a
# string is essentially never a false positive.
# ---------------------------------------------------------------------------
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
AWS_ACCESS_KEY_RE = re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")
GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")
SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")

HIGH_CONFIDENCE_PATTERNS = {
    "private key material": PRIVATE_KEY_RE,
    "AWS access key ID": AWS_ACCESS_KEY_RE,
    "GitHub token": GITHUB_TOKEN_RE,
    "Slack token": SLACK_TOKEN_RE,
    "JWT": JWT_RE,
}

BANNED_EXTENSIONS = {".pem", ".key", ".p12", ".pfx"}

# Key SUFFIXES that mean "this holds a reference/location/flag, not secret
# material," no matter what word precedes them (e.g. extraSecretName,
# clientSecretRef). Deliberately does NOT include generic "key"/"token"/
# "secret" endings alone -- apiKey / clientSecret / accessToken holding a
# literal string is exactly the case this scan exists to catch.
SAFE_KEY_SUFFIXES = (
    "name",
    "ref",
    "path",
    "url",
    "uri",
    "enabled",
    "kind",
    "type",
    "host",
    "hostname",
    "class",
    "annotations",
    "labels",
)

# Specific exact key names (case-insensitive) found in practice to be a
# key-name-of-a-key-in-a-secret, not the secret itself, that the suffix
# rule above doesn't already cover. Add to this list only for a confirmed
# false positive -- never to silence a real finding.
SAFE_KEY_NAMES = {
    "apitokenkey",  # e.g. cloudflare-tunnel chart: cloudflare.secretRef.apiTokenKey
    "existingsecret",  # common Helm chart convention: name of a pre-existing Secret object
}

SUSPICIOUS_KEY_RE = re.compile(
    r"(password|passwd|pwd|secret|token|apikey|api[_-]?key|access[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)


def _is_safe_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in SAFE_KEY_NAMES:
        return True
    return any(lowered.endswith(suffix) for suffix in SAFE_KEY_SUFFIXES)


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(s)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


# base64-ish, length >= 32, must mix case and digits (excludes pure lowercase
# hex digests like sha256:..., excludes UUIDs which only use hex + dashes).
_BASE64ISH_RE = re.compile(r"^[A-Za-z0-9+/]{32,}={0,2}$")


def looks_like_high_entropy_secret(value: str) -> bool:
    if not _BASE64ISH_RE.match(value):
        return False
    has_upper = any(c.isupper() for c in value)
    has_lower = any(c.islower() for c in value)
    has_digit = any(c.isdigit() for c in value)
    if not (has_upper and has_lower and has_digit):
        return False
    return shannon_entropy(value) >= 4.5


def scan_text_for_high_confidence_patterns(text: str) -> list[str]:
    return [label for label, pattern in HIGH_CONFIDENCE_PATTERNS.items() if pattern.search(text)]


def find_suspicious_plaintext_values(obj, path: str = "") -> list[tuple[str, str]]:
    """Walk a parsed manifest/values tree; return (path, value) for any
    password/secret/token-ish key holding a non-empty literal string (as
    opposed to a dict like secretKeyRef, which is a safe indirection).
    """
    findings: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            child_path = f"{path}.{key}" if path else str(key)
            if (
                isinstance(key, str)
                and SUSPICIOUS_KEY_RE.search(key)
                and not _is_safe_key(key)
                and isinstance(val, str)
                and val.strip() != ""
            ):
                findings.append((child_path, val))
            findings.extend(find_suspicious_plaintext_values(val, child_path))
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            findings.extend(find_suspicious_plaintext_values(val, f"{path}[{i}]"))
    return findings


def find_high_entropy_strings(obj, path: str = "") -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            child_path = f"{path}.{key}" if path else str(key)
            findings.extend(find_high_entropy_strings(val, child_path))
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            findings.extend(find_high_entropy_strings(val, f"{path}[{i}]"))
    elif isinstance(obj, str) and looks_like_high_entropy_secret(obj):
        findings.append((path, obj))
    return findings


def _looks_like_env_var_list(val: list) -> bool:
    return bool(val) and all(
        isinstance(item, dict) and "name" in item and "value" in item for item in val
    )


def find_env_var_credentials(obj, path: str = "") -> list[tuple[str, str]]:
    """`env: [{name: DB_PASSWORD, value: literal}]` is the single most
    common way a container spec carries a credential directly (as opposed
    to `valueFrom.secretKeyRef`). find_suspicious_plaintext_values can't
    see this because the suspicious word is in a *sibling* field (`name`),
    not the key holding the value.
    """
    findings: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key in ("env", "args", "command") and isinstance(val, list) and _looks_like_env_var_list(val):
                for i, item in enumerate(val):
                    name = item.get("name", "")
                    value = item.get("value")
                    if (
                        isinstance(name, str)
                        and SUSPICIOUS_KEY_RE.search(name)
                        and not _is_safe_key(name)
                        and isinstance(value, str)
                        and value.strip() != ""
                    ):
                        findings.append((f"{child_path}[{i}].value", value))
            findings.extend(find_env_var_credentials(val, child_path))
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            findings.extend(find_env_var_credentials(val, f"{path}[{i}]"))
    return findings


# ---------------------------------------------------------------------------
# IP addresses. A private/RFC1918 address is expected, load-bearing content
# in a homelab infra backup (this repo's own spec names several by IP).
# A PUBLIC address is a different matter: it's a much stronger, more
# specific piece of identifying information (it can pin down a physical
# network), so it gets treated as a real finding rather than inventory.
# ---------------------------------------------------------------------------
IPV4_CANDIDATE_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"
)

# Dotted 4-part version numbers (container image tags, chart/appVersions)
# are syntactically indistinguishable from an IPv4 address by content alone
# -- e.g. Emby's own version scheme produced the tag "version-4.9.1.90" on
# this cluster, a real false positive caught while building this. Skip IP
# scanning under keys that are conventionally a version/tag, not a host.
IP_SAFE_KEY_NAMES = {"tag", "version", "chartversion", "appversion", "revision", "imagetag"}


def classify_ip(candidate: str) -> str | None:
    """Return 'private', 'public', 'reserved', or None if not a valid IP."""
    try:
        addr = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    if addr.is_loopback or addr.is_link_local or addr.is_private:
        return "private"
    if addr.is_reserved or addr.is_multicast or addr.is_unspecified:
        return "reserved"
    return "public"


def find_ip_addresses(obj, path: str = "") -> list[tuple[str, str, str]]:
    """Walk a parsed tree; return (path, ip, classification) for every
    string that contains a valid IPv4 address.
    """
    findings: list[tuple[str, str, str]] = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            child_path = f"{path}.{key}" if path else str(key)
            if isinstance(key, str) and key.lower() in IP_SAFE_KEY_NAMES:
                continue
            findings.extend(find_ip_addresses(val, child_path))
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            findings.extend(find_ip_addresses(val, f"{path}[{i}]"))
    elif isinstance(obj, str):
        for match in IPV4_CANDIDATE_RE.finditer(obj):
            classification = classify_ip(match.group())
            if classification:
                findings.append((path, match.group(), classification))
    return findings


_FIELD_PATH_HINTS = [
    (re.compile(r"clusterip", re.IGNORECASE), "Kubernetes Service clusterIP"),
    (re.compile(r"loadbalancer.*ip|ingress\[\d+\]\.ip", re.IGNORECASE), "Service LoadBalancer ingress IP"),
    (re.compile(r"externalips?\[", re.IGNORECASE), "Service externalIPs entry"),
    (re.compile(r"hostip", re.IGNORECASE), "node host IP (the node a pod landed on)"),
    (re.compile(r"podip", re.IGNORECASE), "pod IP"),
    (re.compile(r"nfs\.server|(^|\.)server$", re.IGNORECASE), "NFS server address"),
    (re.compile(r"nameserver|dns", re.IGNORECASE), "DNS server address"),
    (re.compile(r"addresses\[", re.IGNORECASE), "address pool / range entry"),
]


def _ip_in_metallb_range(ip: str, addr_spec: str) -> bool:
    """MetalLB IPAddressPool spec.addresses entries are either a CIDR
    ("192.168.1.0/24") or a hyphenated range ("192.168.1.50-192.168.1.70")."""
    try:
        target = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if "-" in addr_spec:
        start_s, _, end_s = addr_spec.partition("-")
        try:
            return ipaddress.ip_address(start_s.strip()) <= target <= ipaddress.ip_address(end_s.strip())
        except ValueError:
            return False
    try:
        return target in ipaddress.ip_network(addr_spec.strip(), strict=False)
    except ValueError:
        return False


def build_ip_context(nodes: list[dict], cluster_raw: dict) -> dict:
    """Known-infrastructure IPs gathered from this same capture run, used to
    give find_ip_addresses' CSV a real "what is this for" answer instead of
    just a private/public guess.
    """
    node_ips: dict[str, str] = {}
    for node in nodes:
        name = node.get("metadata", {}).get("name", "?")
        for addr in node.get("status", {}).get("addresses", []):
            ip = addr.get("address", "")
            if classify_ip(ip):
                node_ips[ip] = f"node {name} ({addr.get('type', 'Address')})"

    nfs_server_pv_counts: dict[str, int] = {}
    for pv in cluster_raw.get("persistentvolume", []):
        server = pv.get("spec", {}).get("nfs", {}).get("server")
        if server and classify_ip(server):
            nfs_server_pv_counts[server] = nfs_server_pv_counts.get(server, 0) + 1
    nfs_servers = {
        ip: (f"NFS server (backs {count} PersistentVolume{'s' if count != 1 else ''})")
        for ip, count in nfs_server_pv_counts.items()
    }

    metallb_pools: list[tuple[str, str]] = []
    for pool in cluster_raw.get("ipaddresspool.metallb.io", []):
        name = pool.get("metadata", {}).get("name", "?")
        for addr_spec in pool.get("spec", {}).get("addresses", []):
            metallb_pools.append((name, addr_spec))

    return {"node_ips": node_ips, "nfs_servers": nfs_servers, "metallb_pools": metallb_pools}


def classify_ip_purpose(ip: str, classification: str, field_path: str, ip_context: dict) -> str:
    if ip in ip_context.get("node_ips", {}):
        return ip_context["node_ips"][ip]
    if ip in ip_context.get("nfs_servers", {}):
        return ip_context["nfs_servers"][ip]
    for pool_name, addr_spec in ip_context.get("metallb_pools", []):
        if _ip_in_metallb_range(ip, addr_spec):
            return f"MetalLB IPAddressPool address ({pool_name})"

    if ip == "0.0.0.0":
        return "bind-all address (listen on every interface)"
    if ip == "127.0.0.1":
        return "loopback"
    if ip.startswith("169.254."):
        return "link-local (APIPA) -- often a metrics/health bind address"

    for pattern, label in _FIELD_PATH_HINTS:
        if pattern.search(field_path):
            return label

    if classification == "public":
        return "public address, purpose not identified from context -- review this one"
    return "private address, no more specific role identified from context"


def find_ip_addresses_in_text(text: str) -> list[tuple[str, str]]:
    findings = []
    for match in IPV4_CANDIDATE_RE.finditer(text):
        classification = classify_ip(match.group())
        if classification:
            findings.append((match.group(), classification))
    return findings


# ---------------------------------------------------------------------------
# Redaction: applied at capture time, before anything is written to disk.
# ---------------------------------------------------------------------------
REDACTION_PLACEHOLDER = "<REDACTED-BY-k8s-backup-repo-gen>"


@dataclass
class Redaction:
    path: str
    reason: str


def redact_credentials(obj) -> list[Redaction]:
    """Mutate `obj` in place, replacing any value this module's detectors
    flag as likely credential material with REDACTION_PLACEHOLDER. Returns
    what was redacted (path + reason only -- never the value itself, even
    in the returned record, so a redaction can be safely logged/printed).
    """
    redactions: list[Redaction] = []
    _redact_pattern_matches(obj, "", redactions)
    _redact_env_var_credentials(obj, "", redactions)
    _redact_suspicious_plaintext(obj, "", redactions)
    return redactions


def _redact_pattern_matches(obj, path, redactions: list[Redaction]) -> None:
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            child_path = f"{path}.{key}" if path else str(key)
            val = obj[key]
            if isinstance(val, str):
                found = scan_text_for_high_confidence_patterns(val)
                if not found and looks_like_high_entropy_secret(val):
                    found = ["high-entropy string"]
                if found:
                    obj[key] = REDACTION_PLACEHOLDER
                    redactions.append(Redaction(child_path, ", ".join(found)))
            else:
                _redact_pattern_matches(val, child_path, redactions)
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            child_path = f"{path}[{i}]"
            if isinstance(val, str):
                found = scan_text_for_high_confidence_patterns(val)
                if not found and looks_like_high_entropy_secret(val):
                    found = ["high-entropy string"]
                if found:
                    obj[i] = REDACTION_PLACEHOLDER
                    redactions.append(Redaction(child_path, ", ".join(found)))
            else:
                _redact_pattern_matches(val, child_path, redactions)


def _redact_env_var_credentials(obj, path, redactions: list[Redaction]) -> None:
    if isinstance(obj, dict):
        for key, val in obj.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key in ("env", "args", "command") and isinstance(val, list) and _looks_like_env_var_list(val):
                for i, item in enumerate(val):
                    name = item.get("name", "")
                    value = item.get("value")
                    if (
                        isinstance(name, str)
                        and SUSPICIOUS_KEY_RE.search(name)
                        and not _is_safe_key(name)
                        and isinstance(value, str)
                        and value.strip() != ""
                        and value != REDACTION_PLACEHOLDER
                    ):
                        item["value"] = REDACTION_PLACEHOLDER
                        redactions.append(
                            Redaction(f"{child_path}[{i}].value", f"env var name '{name}' looks like a credential")
                        )
            _redact_env_var_credentials(val, child_path, redactions)
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            _redact_env_var_credentials(val, f"{path}[{i}]", redactions)


def _redact_suspicious_plaintext(obj, path, redactions: list[Redaction]) -> None:
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            child_path = f"{path}.{key}" if path else str(key)
            val = obj[key]
            if (
                isinstance(key, str)
                and SUSPICIOUS_KEY_RE.search(key)
                and not _is_safe_key(key)
                and isinstance(val, str)
                and val.strip() != ""
                and val != REDACTION_PLACEHOLDER
            ):
                obj[key] = REDACTION_PLACEHOLDER
                redactions.append(Redaction(child_path, f"key name '{key}' looks like a credential"))
            else:
                _redact_suspicious_plaintext(val, child_path, redactions)
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            _redact_suspicious_plaintext(val, f"{path}[{i}]", redactions)
