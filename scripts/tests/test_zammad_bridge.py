"""zammad-bridge.py, Alertmanager's webhook into Zammad's Checkmk endpoint: which
alerts it passes, and the fields Zammad keys its tickets by. Documentation
addresses only (RFC 5737)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[2] / "kubernetes" / "prod" / "system" / "monitoring"
          / "zammad-bridge" / "app" / "zammad-bridge.py")


def load():
    spec = importlib.util.spec_from_file_location("zammad_bridge_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load()


def alert(status="firing", **labels):
    return {"status": status, "fingerprint": "abc123",
            "labels": {"alertname": "TargetDown", "severity": "warning", **labels},
            "annotations": {"summary": "Targets are down", "description": "1 of 5 targets are down."}}


def test_only_warning_and_critical_pass_never_the_plumbing():
    assert m.wanted(alert(severity="warning")) and m.wanted(alert(severity="critical"))
    assert not m.wanted(alert(severity="info"))
    assert not m.wanted(alert(alertname="Watchdog", severity="none"))
    assert not m.wanted(alert(alertname="InfoInhibitor", severity="warning"))


def test_host_names_the_alert_and_its_target_and_service_the_alert():
    f = m.to_checkmk(alert(instance="192.0.2.10:9100", job="proxmox-hosts", namespace="monitoring"))
    assert f["service"] == "TargetDown"
    assert f["host"] == "TargetDown monitoring/192.0.2.10:9100"
    assert f["state"] == "WARNING" and f["event_id"] == "abc123"
    assert "Targets are down" in f["text"] and "job=proxmox-hosts" in f["text"]


def test_the_same_alert_firing_and_resolved_keys_the_same_ticket():
    # Zammad closes the open ticket whose host and service match; OK is its recovery state.
    firing = m.to_checkmk(alert(pod="web-0", namespace="apps", severity="critical"))
    resolved = m.to_checkmk(alert(status="resolved", pod="web-0", namespace="apps", severity="critical"))
    assert (firing["host"], firing["service"]) == (resolved["host"], resolved["service"])
    assert firing["state"] == "CRITICAL" and resolved["state"] == "OK"


def test_an_alert_with_no_target_label_falls_back_to_its_namespace_then_its_name():
    assert m.to_checkmk(alert(namespace="zammad"))["host"] == "TargetDown zammad"
    assert m.to_checkmk(alert())["host"] == "TargetDown"
