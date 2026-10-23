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


def test_a_container_event_is_keyed_by_the_container_its_message_names():
    die = m.dockhand_to_checkmk({"title": "Container Die", "message": 'Container "kea-dhcp4" die (kea:3.2.1)', "type": "error"})
    start = m.dockhand_to_checkmk({"title": "Container Start", "message": 'Container "kea-dhcp4" start (kea:3.2.1)', "type": "success"})
    other = m.dockhand_to_checkmk({"title": "Container Die", "message": 'Container "netbox" die', "type": "error"})
    assert die["host"] == start["host"] == "Dockhand Container kea-dhcp4" and other["host"] == "Dockhand Container netbox"
    assert die["state"] == "CRITICAL" and start["state"] == "OK"


def semaphore(name="Host: Proxmox Upgrade", status="❌ ERROR"):
    """Semaphore v2.19's Slack alert, as its slack.tmpl renders it."""
    return {"attachments": [{"title": f"Task: {name}", "title_link": "https://semaphore.example/project/1/templates/2?t=1185",
                             "text": f"execution #1185, status: {status}!", "color": "danger", "mrkdwn_in": ["text"],
                             "fields": [{"title": "Author", "value": "—", "short": True}]}]}


def test_a_semaphore_failure_opens_and_the_templates_next_success_closes_it():
    down = m.semaphore_to_checkmk(semaphore())
    up = m.semaphore_to_checkmk(semaphore(status="✅ SUCCESS"))
    assert down["host"] == up["host"] == "Semaphore Host: Proxmox Upgrade" and down["service"] == "semaphore"
    assert down["state"] == "CRITICAL" and up["state"] == "OK"
    assert "execution #1185" in down["text"] and "?t=1185" in down["text"]


def test_a_semaphore_confirmation_wait_or_an_empty_alert_opens_nothing():
    assert m.semaphore_to_checkmk(semaphore(status="⚠️ WAITING_CONFIRMATION")) is None
    assert m.semaphore_to_checkmk({}) is None and m.semaphore_to_checkmk({"attachments": "x"}) is None


def test_databasus_keys_a_ticket_by_database_and_kind_and_success_closes_it():
    fail = m.databasus_to_checkmk({"heading": '❌ Backup failed for database "kea" (workspace "home")', "message": "pg_dump: timeout"})
    ok = m.databasus_to_checkmk({"heading": '✅ Backup completed for database "kea" (workspace "home")', "message": "12 MB"})
    assert fail["host"] == ok["host"] == "Databasus backup kea" and fail["service"] == "databasus"
    assert fail["state"] == "CRITICAL" and ok["state"] == "OK" and "pg_dump: timeout" in fail["text"]


def test_databasus_health_verification_and_physical_kinds_stay_apart():
    cases = {
        "❌ [kea] DB is unavailable": ("Databasus health kea", "CRITICAL"),
        "✅ [kea] DB is online": ("Databasus health kea", "OK"),
        'backup verification failed after 3 attempts for "kea"': ("Databasus verification kea", "CRITICAL"),
        'backup verification passed for "kea"': ("Databasus verification kea", "OK"),
        'Physical FULL failed for "kea" (workspace "home")': ("Databasus backup kea", "CRITICAL"),
        'Physical INCR completed for "kea" (workspace "home")': ("Databasus backup kea", "OK"),
        'Physical WAL gap detected for "kea"': ("Databasus WAL kea", "CRITICAL"),
        "Test message": ("Databasus test", "WARNING"),
    }
    for heading, (host, state) in cases.items():
        f = m.databasus_to_checkmk({"heading": heading})
        assert (f["host"], f["state"]) == (host, state), heading
    assert m.databasus_to_checkmk({"message": "no heading"}) is None


def test_each_token_route_wants_its_own_token_and_is_off_without_one(monkeypatch):
    import json
    sent = []
    monkeypatch.setattr(m, "send", lambda url, fields: sent.append(fields) or "ok")
    bodies = {"semaphore": json.dumps(semaphore()).encode(),
              "databasus": b'{"heading": "\\u274c [kea] DB is unavailable", "message": ""}'}
    for route, body in bodies.items():
        monkeypatch.setattr(m.Handler, f"{route}_token", "")
        assert dockhand_post(f"/{route}/anything", body) == 404
        monkeypatch.setattr(m.Handler, f"{route}_token", "s3cr3t-token")
        monkeypatch.setattr(m.Handler, "dockhand_token", "other-token")
        assert dockhand_post(f"/{route}/wrong", body) == 404
        assert dockhand_post(f"/{route}/other-token", body) == 404
        assert dockhand_post(f"/{route}/s3cr3t-token", body) == 200 and sent[-1]["state"] == "CRITICAL"
    assert dockhand_post("/semaphore/s3cr3t-token", json.dumps(semaphore(status="⚠️ WAITING_CONFIRMATION")).encode()) == 200
    assert len(sent) == 2


def container(name, state="exited", status="Exited (1) 2 seconds ago", project="kea"):
    labels = {"com.docker.compose.project": project} if project else {"databasus.verification.agent_id": "a1"}
    return {"Names": [f"/{name}"], "State": state, "Status": status, "Labels": labels}


def died(name):
    return {"title": "Container Die", "message": f'Container "{name}" die (some/image:1)', "type": "error"}


HOSTS = ["192.0.2.10:9339", "192.0.2.20:9339"]


def test_a_stack_containers_failure_is_a_ticket_and_the_others_are_not(monkeypatch):
    lists = {HOSTS[0]: [container("kea-dhcp4"), container("kea-db-init", status="Exited (0) 3 seconds ago"),
                        container("loose", project=None)],
             HOSTS[1]: []}
    monkeypatch.setattr(m, "docker_containers", lambda host: lists[host])
    assert m.dockhand_to_checkmk(died("kea-dhcp4"), HOSTS)["state"] == "CRITICAL"
    assert m.dockhand_to_checkmk(died("kea-db-init"), HOSTS) is None        # a one-shot's normal finish
    assert m.dockhand_to_checkmk(died("loose"), HOSTS) is None              # outside every stack
    assert m.dockhand_to_checkmk(died("databasus-verif-1f2e"), HOSTS) is None  # removed by what started it
    up = {"title": "Container Start", "message": 'Container "kea-dhcp4" start (some/image:1)', "type": "success"}
    assert m.dockhand_to_checkmk(up, HOSTS)["state"] == "OK"                # a recovery is never asked about


def test_a_host_that_cant_be_asked_never_drops_a_ticket(monkeypatch):
    def flaky(host):
        if host == HOSTS[1]:
            raise OSError("timed out")
        return []
    monkeypatch.setattr(m, "docker_containers", flaky)
    f = m.dockhand_to_checkmk(died("kea-dhcp4"), HOSTS)
    assert f["state"] == "CRITICAL" and "1 of 2 Docker hosts couldn't be asked" in f["text"]
    monkeypatch.setattr(m, "docker_containers", lambda host: [container("kea-dhcp4")] if host == HOSTS[0] else flaky(host))
    assert m.dockhand_to_checkmk(died("kea-dhcp4"), HOSTS)["text"].count("couldn't be asked") == 0


def test_without_hosts_every_container_failure_is_a_ticket(monkeypatch):
    monkeypatch.setattr(m, "docker_containers", lambda host: (_ for _ in ()).throw(AssertionError("asked")))
    assert m.dockhand_to_checkmk(died("databasus-verif-1f2e"))["state"] == "CRITICAL"


class FakeTimer:
    """threading.Timer that fires only when the test says so."""
    made = []

    def __init__(self, seconds, fn):
        self.seconds, self.fn, self.cancelled, self.daemon = seconds, fn, False, False
        FakeTimer.made.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


def held(monkeypatch, lists):
    """The hold on, a fake clock, Zammad recorded; each host's containers from lists."""
    import json
    sent = []
    FakeTimer.made = []
    monkeypatch.setattr(m.threading, "Timer", FakeTimer)
    monkeypatch.setattr(m, "HELD", {})
    monkeypatch.setattr(m, "send", lambda url, fields: sent.append(fields) or "ok")
    monkeypatch.setattr(m, "docker_containers", lambda host: lists[host])
    monkeypatch.setattr(m.Handler, "dockhand_token", "s3cr3t-token")
    monkeypatch.setattr(m.Handler, "docker_hosts", HOSTS)
    monkeypatch.setattr(m.Handler, "hold_seconds", 180)
    post = lambda note: dockhand_post("/dockhand/s3cr3t-token", json.dumps(note).encode())
    return sent, post


def test_a_held_failure_still_failed_after_the_hold_is_a_ticket(monkeypatch):
    lists = {HOSTS[0]: [container("kea-dhcp4")], HOSTS[1]: []}
    sent, post = held(monkeypatch, lists)
    assert post(died("kea-dhcp4")) == 200 and sent == [] and FakeTimer.made[0].seconds == 180
    FakeTimer.made[0].fn()
    assert len(sent) == 1 and sent[0]["state"] == "CRITICAL" and sent[0]["host"] == "Dockhand Container kea-dhcp4"


def test_a_container_running_again_after_the_hold_opens_nothing(monkeypatch):
    lists = {HOSTS[0]: [container("netbox")], HOSTS[1]: []}
    sent, post = held(monkeypatch, lists)
    post(died("netbox"))
    lists[HOSTS[0]] = [container("netbox", state="running", status="Up 40 seconds")]
    FakeTimer.made[0].fn()
    assert sent == []


def test_a_recovery_within_the_hold_cancels_it_and_sends_nothing(monkeypatch):
    lists = {HOSTS[0]: [container("netbox")], HOSTS[1]: []}
    sent, post = held(monkeypatch, lists)
    post(died("netbox"))
    assert post({"title": "Container Start", "message": 'Container "netbox" start (some/image:1)', "type": "success"}) == 200
    assert FakeTimer.made[0].cancelled and sent == []
    FakeTimer.made[0].fn()  # a timer that fires after its cancel does nothing
    assert sent == []


def test_a_die_and_a_kill_for_one_container_hold_once(monkeypatch):
    lists = {HOSTS[0]: [container("kea-postgres")], HOSTS[1]: []}
    sent, post = held(monkeypatch, lists)
    post(died("kea-postgres"))
    post({"title": "Container Kill", "message": 'Container "kea-postgres" kill (some/image:1)', "type": "error"})
    assert len(FakeTimer.made) == 1


def test_a_host_that_cant_be_asked_after_the_hold_still_opens_the_ticket(monkeypatch):
    lists = {HOSTS[0]: [container("kea-dhcp4")], HOSTS[1]: []}
    sent, post = held(monkeypatch, lists)
    post(died("kea-dhcp4"))
    monkeypatch.setattr(m, "docker_containers", lambda host: (_ for _ in ()).throw(OSError("timed out")))
    FakeTimer.made[0].fn()
    assert len(sent) == 1 and "couldn't be asked" in sent[0]["text"]
