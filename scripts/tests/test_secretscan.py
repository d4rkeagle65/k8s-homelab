"""The detectors behind capture's redaction and the hygiene scans, proven against values
that must be caught and values that must not. A detector that stops matching keeps the
repo-wide scans in test_secrets_hygiene.py green forever, so each one is shown firing here.

Credential-shaped values are built at run time, never written as literals, so this file
holds none of the shapes the commit hook refuses.
"""

from __future__ import annotations

import copy
import string

import pytest

from k8s_backup import secretscan as ss

LITERAL = "not-" + "a-real-one"
# Every letter and digit once: base64-ish, mixed case and digits, entropy log2(62).
HIGH_ENTROPY = string.ascii_letters + string.digits
# The name of a Secret holding one, which is no credential itself.
SECRET_NAME = "admin-creds"


def test_shannon_entropy():
    assert ss.shannon_entropy("") == 0.0
    assert ss.shannon_entropy("aaaa") == 0.0
    assert ss.shannon_entropy("abab") == 1.0
    assert ss.shannon_entropy(HIGH_ENTROPY) > 5.9


def test_high_entropy_secret_is_caught_and_lookalikes_are_not():
    assert ss.looks_like_high_entropy_secret(HIGH_ENTROPY)
    assert not ss.looks_like_high_entropy_secret(HIGH_ENTROPY[:20]), "under 32 characters"
    assert not ss.looks_like_high_entropy_secret("0123456789abcdef" * 3), "a lower-case hex digest"
    assert not ss.looks_like_high_entropy_secret("Aa1" * 12), "mixed but low entropy"
    assert not ss.looks_like_high_entropy_secret(HIGH_ENTROPY + "-x"), "not base64-ish"


def test_find_high_entropy_strings_walks_nested_values():
    doc = {"spec": {"args": ["--x", HIGH_ENTROPY], "name": "plain"}}
    assert ss.find_high_entropy_strings(doc) == [("spec.args[1]", HIGH_ENTROPY)]
    assert ss.find_high_entropy_strings({"a": ["plain", {"b": "words here"}]}) == []


def _env(*items):
    return {"spec": {"containers": [{"name": "app", "env": list(items)}]}}


def test_env_var_credential_is_caught_beside_valuefrom_items():
    from_secret = {"name": "API_TOKEN", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}}
    literal = {"name": "DB_PASSWORD", "value": LITERAL}
    path = "spec.containers[0].env[1].value"
    assert ss.find_env_var_credentials(_env(from_secret, literal)) == [(path, LITERAL)]
    assert ss.find_env_var_credentials(_env(literal)) == [("spec.containers[0].env[0].value", LITERAL)]


def test_env_vars_that_hold_no_credential_are_not_caught():
    assert ss.find_env_var_credentials(_env(
        {"name": "API_TOKEN", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}},
        {"name": "MODE", "value": LITERAL},
        {"name": "ALLOW_PASSWORD_LOGIN", "value": "false"},
        {"name": "REQUIRE_SECRETS", "value": "True"},
        {"name": "DB_PASSWORD", "value": ""},
        {"name": "DB_PASSWORD_PATH", "value": "/run/secrets/db"},
    )) == []
    assert ss.find_env_var_credentials({"args": ["--token", LITERAL]}) == [], "args strings are no env list"


def test_suspicious_plaintext_value_is_caught_and_indirection_is_not():
    values = {"admin": {"adminPassword": LITERAL, "existingSecret": SECRET_NAME,
                        "passwordSecretRef": {"name": "s", "key": "k"}}}
    assert ss.find_suspicious_plaintext_values(values) == [("admin.adminPassword", LITERAL)]


def test_redact_credentials_replaces_each_kind_and_keeps_no_value():
    doc = {
        "spec": {"containers": [{"name": "app", "env": [
            {"name": "API_TOKEN", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}},
            {"name": "DB_PASSWORD", "value": LITERAL},
            {"name": "ALLOW_PASSWORD_LOGIN", "value": "false"},
        ], "args": ["--seed", HIGH_ENTROPY]}]},
        "config": {"adminPassword": LITERAL, "existingSecret": SECRET_NAME},
    }
    want = copy.deepcopy(doc)
    redactions = ss.redact_credentials(doc)
    placeholder = ss.REDACTION_PLACEHOLDER
    want["spec"]["containers"][0]["env"][1]["value"] = placeholder
    want["spec"]["containers"][0]["args"][1] = placeholder
    want["config"]["adminPassword"] = placeholder
    assert doc == want
    assert sorted(r.path for r in redactions) == [
        "config.adminPassword", "spec.containers[0].args[1]", "spec.containers[0].env[1].value"]
    record = repr(redactions)
    assert LITERAL not in record and HIGH_ENTROPY not in record, "a redaction record carries no value"
    assert ss.redact_credentials(doc) == [], "a second pass finds nothing left to redact"


# --- what an address in capture's IP inventory is for ---------------------------------------------

def _context():
    nodes = [{"metadata": {"name": "w1"}, "status": {"addresses": [
        {"type": "InternalIP", "address": "192.0.2.11"}, {"type": "Hostname", "address": "w1"}]}}]
    cluster = {
        "persistentvolume": [{"spec": {"nfs": {"server": "192.0.2.50"}}}, {"spec": {"nfs": {"server": "192.0.2.50"}}},
                             {"spec": {"hostPath": {"path": "/x"}}}],
        "ipaddresspool.metallb.io": [{"metadata": {"name": "lan"},
                                      "spec": {"addresses": ["198.51.100.10-198.51.100.20", "203.0.113.0/28"]}}],
    }
    return ss.build_ip_context(nodes, cluster)


def test_build_ip_context_names_nodes_nfs_servers_and_metallb_pools():
    ctx = _context()
    assert ctx["node_ips"] == {"192.0.2.11": "node w1 (InternalIP)"}, "a hostname isn't an address"
    assert ctx["nfs_servers"] == {"192.0.2.50": "NFS server (backs 2 PersistentVolumes)"}
    assert ctx["metallb_pools"] == [("lan", "198.51.100.10-198.51.100.20"), ("lan", "203.0.113.0/28")]


@pytest.mark.parametrize("ip, spec, inside", [
    ("198.51.100.10", "198.51.100.10-198.51.100.20", True), ("198.51.100.20", "198.51.100.10 - 198.51.100.20", True),
    ("198.51.100.21", "198.51.100.10-198.51.100.20", False), ("203.0.113.15", "203.0.113.0/28", True),
    ("203.0.113.16", "203.0.113.0/28", False), ("not-an-ip", "203.0.113.0/28", False),
    ("203.0.113.1", "garbage", False), ("203.0.113.1", "a-b", False),
])
def test_an_address_is_in_a_metallb_range_or_cidr(ip, spec, inside):
    assert ss._ip_in_metallb_range(ip, spec) is inside


@pytest.mark.parametrize("ip, classification, path, purpose", [
    ("192.0.2.11", "private", "spec.x", "node w1 (InternalIP)"),
    ("192.0.2.50", "private", "spec.x", "NFS server (backs 2 PersistentVolumes)"),
    ("198.51.100.15", "public", "spec.x", "MetalLB IPAddressPool address (lan)"),
    ("0.0.0.0", "reserved", "spec.x", "bind-all address (listen on every interface)"),
    ("127.0.0.1", "private", "spec.x", "loopback"),
    ("169.254.1.1", "private", "spec.x", "link-local (APIPA) -- often a metrics/health bind address"),
    ("192.0.2.99", "private", "spec.clusterIP", "Kubernetes Service clusterIP"),
    ("192.0.2.99", "private", "spec.nfs.server", "NFS server address"),
    ("192.0.2.99", "private", "spec.x", "private address, no more specific role identified from context"),
    ("8.8.8.8", "public", "spec.x", "public address, purpose not identified from context -- review this one"),
])
def test_classify_ip_purpose_takes_the_run_s_own_knowledge_first(ip, classification, path, purpose):
    assert ss.classify_ip_purpose(ip, classification, path, _context()) == purpose


# Each high-confidence shape, assembled here so this file holds none of them whole.
_SHAPES = {
    "private key material": "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIE...\n",
    "AWS access key ID": "id: " + "AKIA" + "ABCDEFGHIJKLMNOP",
    "GitHub token": "token " + "ghp_" + "a1" * 18,
    "Slack token": "hook " + "xoxb-" + "1234567890-abc",
    "JWT": "bearer " + ".".join(["eyJ" + "hbGciOiJIUzI1", "eyJzdWIiOiIxMjM0", "SflKxwRJSMeKKF2QT4"]),
}


@pytest.mark.parametrize("label", sorted(_SHAPES))
def test_each_high_confidence_shape_is_caught(label):
    assert ss.scan_text_for_high_confidence_patterns(_SHAPES[label]) == [label]


def test_text_without_a_credential_shape_is_not():
    assert ss.scan_text_for_high_confidence_patterns(
        "a private key goes in Key Vault; AKIA is a prefix; ghp_ alone; eyJ.short.x") == []
