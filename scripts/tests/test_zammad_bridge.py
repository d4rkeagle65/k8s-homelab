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


def test_a_group_alert_asks_for_its_members_narrowed_by_its_labels():
    q = m.members_query(alert(job="node-exporter", namespace="monitoring", service="node-exporter"))
    assert 'up{job="node-exporter",namespace="monitoring",service="node-exporter"} == 0' in q
    q = m.members_query(alert(alertname="KubeDeploymentReplicasMismatch", namespace="media",
                              deployment="sonarr"))
    assert 'namespace="media",pod=~"sonarr-.*"' in q


def test_an_alert_about_one_thing_or_with_nothing_to_narrow_by_asks_nothing():
    assert m.members_query(alert(alertname="KubePodCrashLooping", pod="x-1", namespace="a")) is None
    assert m.members_query(alert()) is None
    # A workload name that isn't a Kubernetes name never reaches the regex.
    assert m.members_query(alert(alertname="KubeDeploymentReplicasMismatch", namespace="a",
                                 deployment='x".*')) is None


class Answer:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        import io
        import json
        return io.BytesIO(json.dumps(self.body).encode())

    def __exit__(self, *a):
        return False


def with_prometheus(monkeypatch, body=None, error=None):
    def fake(url, timeout=None):
        if error:
            raise error
        return Answer(body)
    monkeypatch.setattr(m.urllib.request, "urlopen", fake)


def test_the_ticket_lists_the_members_down_with_their_node_and_address(monkeypatch):
    with_prometheus(monkeypatch, {"status": "success", "data": {"result": [
        {"metric": {"pod": "exporter-b", "node": "w2", "instance": "192.0.2.12:9100"}},
        {"metric": {"pod": "exporter-a", "node": "w1", "instance": "192.0.2.11:9100"}},
        {"metric": {"instance": "192.0.2.20:9100", "job": "proxmox-hosts"}},
    ]}})
    text = m.to_checkmk(alert(job="node-exporter", namespace="monitoring"), "http://prom:9090")["text"]
    assert "Down now (3):\n- 192.0.2.20:9100\n- exporter-a on w1 (192.0.2.11:9100)\n- exporter-b on w2" in text


def test_a_failed_lookup_says_so_and_never_reads_as_none_down(monkeypatch):
    with_prometheus(monkeypatch, error=m.urllib.error.URLError("refused"))
    text = m.to_checkmk(alert(job="node-exporter", namespace="monitoring"), "http://prom:9090")["text"]
    assert "Couldn't list which are down" in text and "Down now" not in text
    with_prometheus(monkeypatch, {"status": "error", "error": "bad query"})
    text = m.to_checkmk(alert(job="node-exporter", namespace="monitoring"), "http://prom:9090")["text"]
    assert "Couldn't list which are down" in text


def test_a_long_list_is_cut_and_says_how_many_more(monkeypatch):
    with_prometheus(monkeypatch, {"status": "success", "data": {"result": [
        {"metric": {"pod": f"p-{i:02}"}} for i in range(30)]}})
    text = m.to_checkmk(alert(job="j", namespace="n"), "http://prom:9090")["text"]
    assert "Down now (30, the first 25 listed):" in text and "- p-24" in text and "- p-25" not in text


def test_a_recovery_or_no_prometheus_asks_nothing(monkeypatch):
    with_prometheus(monkeypatch, error=AssertionError("asked"))
    assert "Down now" not in m.to_checkmk(alert(status="resolved", job="j", namespace="n"), "http://p")["text"]
    assert "Couldn't" not in m.to_checkmk(alert(job="j", namespace="n"))["text"]


def test_a_dockhand_failure_opens_and_its_recovery_closes_the_same_ticket():
    down = m.dockhand_to_checkmk({"title": "Container unhealthy: kea-dhcp4", "message": "on env 2", "type": "failure"})
    up = m.dockhand_to_checkmk({"title": "Container healthy: kea-dhcp4", "message": "on env 2", "type": "success"})
    assert down["state"] == "CRITICAL" and up["state"] == "OK"
    assert down["host"] == up["host"] == "Dockhand Container: kea-dhcp4" and down["service"] == "dockhand"
    assert "on env 2" in down["text"]
    assert m.dockhand_to_checkmk({"title": "Git sync failed: kea", "type": "warning"})["state"] == "WARNING"
    assert m.dockhand_to_checkmk({"message": "no title"}) is None


def dockhand_post(path, body):
    import io

    class Fake(m.Handler):
        def __init__(self):
            self.path, self.headers = path, {"Content-Length": str(len(body))}
            self.rfile, self.wfile, self.codes = io.BytesIO(body), io.BytesIO(), []

        def send_response(self, code, message=None):
            self.codes.append(code)

        def end_headers(self):
            pass
    h = Fake()
    h.do_POST()
    return h.codes[0]


def test_the_dockhand_endpoint_wants_its_token_and_is_off_without_one(monkeypatch):
    sent = []
    monkeypatch.setattr(m, "send", lambda url, fields: sent.append(fields) or "ok")
    body = b'{"title": "Container stopped: x", "type": "failure"}'
    monkeypatch.setattr(m.Handler, "dockhand_token", "")
    assert dockhand_post("/dockhand/anything", body) == 404
    monkeypatch.setattr(m.Handler, "dockhand_token", "s3cr3t-token")
    assert dockhand_post("/dockhand/wrong", body) == 404
    assert dockhand_post("/dockhand/s3cr3t-token", body) == 200 and sent[0]["state"] == "CRITICAL"


def test_a_dockhand_title_starting_with_dockhand_isnt_doubled():
    f = m.dockhand_to_checkmk({"title": "Dockhand Test Notification", "type": "info"})
    assert f["host"] == "Dockhand Test Notification"
