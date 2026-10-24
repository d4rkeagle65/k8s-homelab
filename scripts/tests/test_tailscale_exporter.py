"""tailscale-exporter.py: the metrics it renders from Tailscale's API answers, and that a
read that fails is reported as such, never as zeros. Documentation addresses only (RFC 5737)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[2] / "kubernetes" / "prod" / "system" / "tailscale-exporter"
          / "tailscale-exporter" / "app" / "tailscale-exporter.py")


def load():
    spec = importlib.util.spec_from_file_location("tailscale_exporter_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load()

ROUTER = {"hostname": "router-a", "tags": ["tag:subnet-router"], "os": "linux", "clientVersion": "1.90.1-t1",
          "connectedToControl": True, "lastSeen": "2026-10-24T20:00:00Z", "keyExpiryDisabled": True,
          "expires": "2027-04-01T00:00:00Z", "authorized": True, "updateAvailable": False,
          "advertisedRoutes": ["192.0.2.0/24", "198.51.100.0/24"], "enabledRoutes": ["192.0.2.0/24"],
          "clientConnectivity": {"mappingVariesByDestIP": False,
                                 "latency": {"nyc": {"latencyMs": 12.5, "preferred": True}, "sfo": {"latencyMs": 80}}}}
LAPTOP = {"hostname": "laptop", "tags": [], "connectedToControl": False, "lastSeen": "2026-10-20T00:00:00Z",
          "keyExpiryDisabled": False, "expires": "2026-11-01T00:00:00Z", "authorized": False}


def metric(lines, name, **labels):
    want = [f'{k}="{v}"' for k, v in labels.items()]
    hits = [ln for ln in lines if ln.startswith(name + "{") or ln.startswith(name + " ")]
    hits = [ln for ln in hits if all(w in ln for w in want)]
    return [float(h.rsplit(" ", 1)[1]) for h in hits]


def test_a_device_reports_online_routes_and_relay_latency():
    lines = m.device_lines([ROUTER, LAPTOP])
    assert metric(lines, "tailscale_devices") == [2]
    assert metric(lines, "tailscale_device_online", device="router-a", tags="tag:subnet-router") == [1]
    assert metric(lines, "tailscale_device_online", device="laptop") == [0]
    assert metric(lines, "tailscale_device_route_enabled", device="router-a", route="192.0.2.0/24") == [1]
    assert metric(lines, "tailscale_device_route_enabled", device="router-a", route="198.51.100.0/24") == [0]
    assert metric(lines, "tailscale_device_derp_latency_seconds", device="router-a", region="nyc") == [0.0125]
    assert metric(lines, "tailscale_device_derp_latency_seconds", region="sfo") == []  # not preferred
    assert metric(lines, "tailscale_device_authorized", device="laptop") == [0]


def test_only_a_key_that_expires_has_an_expiry():
    lines = m.device_lines([ROUTER, LAPTOP])
    assert metric(lines, "tailscale_device_key_expiry_timestamp_seconds", device="router-a") == []
    assert metric(lines, "tailscale_device_key_expiry_timestamp_seconds", device="laptop") == [1793491200]


def test_the_policys_newest_change_names_its_actor():
    events = [
        {"eventTime": "2026-10-24T18:00:00Z", "target": {"type": "POLICY_FILE"}, "actor": {"type": "OAUTH_CLIENT", "id": "k1"}},
        {"eventTime": "2026-10-24T19:00:00Z", "target": {"type": "POLICY_FILE"}, "actor": {"type": "USER", "loginName": "someone"}},
        {"eventTime": "2026-10-24T20:00:00Z", "target": {"type": "DNS"}, "actor": {"type": "USER"}},
    ]
    lines = m.policy_lines(events)
    assert metric(lines, "tailscale_policy_changes", actor_type="OAUTH_CLIENT") == [1]
    assert metric(lines, "tailscale_policy_changes", actor_type="USER") == [1]
    assert metric(lines, "tailscale_policy_last_change_timestamp_seconds", actor_type="USER", actor="someone") == [1792868400]


def test_a_policy_change_is_told_from_a_dns_change_as_the_log_records_them():
    # Both are UPDATEs of the TAILNET (the log's shape on 2026-10-24): the policy's values
    # are the document as text, DNS's are structured.
    policy = {"action": "UPDATE", "type": "CONFIG", "target": {"type": "TAILNET"}, "old": "{}", "new": "{ }",
              "eventTime": "2026-10-24T17:28:18Z", "actor": {"type": "OAUTH_CLIENT", "id": "k1"}}
    dns = {"action": "UPDATE", "type": "CONFIG", "target": {"type": "TAILNET"},
           "old": {"Domains": [], "Routes": {}}, "new": {"Domains": [], "Routes": {}},
           "eventTime": "2026-10-24T17:04:05Z", "actor": {"type": "OAUTH_CLIENT", "id": "k1"}}
    token = {"action": "CREATE", "target": {"type": "OAUTH_ACCESS_TOKEN"}, "eventTime": "2026-10-24T18:00:00Z"}
    assert [m.is_policy_change(e) for e in (policy, dns, token)] == [True, False, False]


def test_no_keys_is_none_not_a_failed_read():
    class NoKeys:
        def get(self, path):
            return {"devices": []} if path.startswith("devices") else {"keys": None} if path == "keys" else {"logs": None}
    text = m.read(NoKeys())
    assert 'tailscale_api_up{endpoint="keys"} 1' in text and 'tailscale_api_up{endpoint="configuration_log"} 1' in text


def test_a_read_that_fails_is_reported_and_its_metrics_left_out():
    class Failing:
        def get(self, path):
            if path.startswith("devices"):
                raise OSError("timed out")
            return {"keys": [], "logs": []}
    text = m.read(Failing())
    assert 'tailscale_api_up{endpoint="devices"} 0' in text
    assert 'tailscale_api_up{endpoint="keys"} 1' in text
    assert "tailscale_devices" not in text  # never "0 devices" for "couldn't read"


def test_label_values_are_escaped():
    assert m.line("x", {"a": 'q"\\\n'}, 1) == 'x{a="q\\"\\\\ "} 1'
