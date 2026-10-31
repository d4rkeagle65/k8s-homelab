"""scripts/alerting_test.py: its test alerts are the ones n8n tickets, under the keys it waits
for (n8n's own Code nodes run on them), the Loki rule matches its marker, and a run passes
only when every leg's ticket opened and closed, sends every recovery, and never reads a
source that couldn't be asked as a pass.
"""
from __future__ import annotations

import importlib.util
import re
import urllib.error
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from .test_n8n_workflows import TICKETS_IN, run_code, workflow

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "kubernetes/prod/system/monitoring/monitors/app/configmap-loki-rules-homelab.yaml"


def load():
    spec = importlib.util.spec_from_file_location("alerting_test", ROOT / "scripts" / "alerting_test.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m = load()
EV = m.events("20261031120000")


def n8n_event(node, body):
    out, _ = run_code(workflow(TICKETS_IN), node, [{"json": {"headers": {}, "body": body}}])
    return out[0]["json"] if out else None


def test_each_webhook_legs_failure_and_recovery_key_the_ticket_it_waits_for():
    for leg, node in (("semaphore", "Semaphore → event"), ("databasus", "Databasus → event")):
        down, up = n8n_event(node, EV[leg]["failure"]), n8n_event(node, EV[leg]["recovery"])
        assert (down["host"], down["service"]) == (up["host"], up["service"]) == EV[leg]["key"], leg
        assert down["state"] != "OK" and up["state"] == "OK", leg
    for kind, state in (("failure", "CRITICAL"), ("recovery", "OK")):
        out, _ = run_code(workflow(TICKETS_IN), "Dockhand → event",
                          [{"json": {"note": EV["dockhand"][kind], "docker": [], "held": {}}}])
        e = out[0]["json"]
        assert ((e["host"], e["service"]), e["state"], e["hold"]) == (EV["dockhand"]["key"], state, 0)


def test_the_alert_legs_marker_fires_the_rule_whose_alert_n8n_keys_as_it_waits_for():
    groups = YAML(typ="safe", pure=True).load(YAML(typ="safe", pure=True).load(RULES.read_text(encoding="utf-8"))["data"]["homelab.yaml"])["groups"]
    [rule] = [r for g in groups for r in g["rules"] if r.get("alert") == "HomelabAlertingTest"]
    stream = EV["alertmanager"]["stream"]
    assert f'source="{stream["source"]}"' in rule["expr"] and re.search(r"\bby \(job\)", rule["expr"])
    labels = {"alertname": "HomelabAlertingTest", "job": stream["job"], **rule["labels"]}
    out, _ = run_code(workflow(TICKETS_IN), "Alertmanager → alerts",
                      [{"json": {"headers": {}, "body": {"alerts": [{"status": "firing", "labels": labels}]}}}])
    assert (out[0]["json"]["host"], out[0]["json"]["service"]) == EV["alertmanager"]["key"]
    other = [r["expr"] for g in groups for r in g["rules"] if r.get("alert") != "HomelabAlertingTest"]
    assert not any("alerting-test" in e or re.search(r'source=~"[^"]*\.\*', e) for e in other), \
        "another rule would count the test's marker"


ENV = {"N8N_URL": "https://n8n.example", "ZAMMAD_URL": "https://zammad.example",
       "LOKI_PUSH_URL": "https://loki-push.example/loki/api/v1/push", "ZAMMAD_API_TOKEN": "z",
       "SEMAPHORE_ALERT_TOKEN": "s", "DATABASUS_ALERT_TOKEN": "d", "DOCKHAND_ALERT_TOKEN": "k",
       "LOKI_PUSH_PASSWORD": "l"}


class Fake:
    """n8n, Loki and Zammad: a failure opens a ticket of its key, a recovery closes it."""

    def __init__(self, refuse=(), zammad_down=False, never_close=()):
        self.tickets, self.sent, self.refuse = {}, [], set(refuse)
        self.zammad_down, self.never_close = zammad_down, set(never_close)

    def request(self, method, url, headers, body=None, timeout=30):
        if "/webhook/zammad/" in url:
            leg = url.rsplit("/", 1)[1]
            kind = "failure" if body == EV[leg]["failure"] else "recovery"
            self.sent.append((leg, kind))
            if leg in self.refuse:
                raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
            self.event(EV[leg]["key"], kind == "failure", leg)
            return 200, {}
        if "loki" in url:
            self.sent.append(("alertmanager", "marker"))
            self.event(EV["alertmanager"]["key"], True, "alertmanager")
            return 204, None
        if self.zammad_down:
            raise urllib.error.URLError("refused")
        if "/tickets/search" in url:
            return 200, [t for t in self.tickets.values() if t["state"] != "closed"]
        tid = int(url.split("/tickets/")[1].split("?")[0])
        t = next(t for t in self.tickets.values() if t["id"] == tid)
        if t["key"] == EV["alertmanager"]["key"] and "alertmanager" not in self.never_close:
            t["state"] = "closed"  # the alert resolves while its close is waited for
        return 200, {"id": tid, "state": t["state"]}

    def event(self, key, failing, leg):
        if failing:
            self.tickets[key] = {"id": len(self.tickets) + 1, "number": str(100 + len(self.tickets)), "key": key,
                                 "state": "new", "preferences": {"check_mk": {"host": key[0], "service": key[1]}}}
        elif key in self.tickets and leg not in self.never_close:
            self.tickets[key]["state"] = "closed"


def run(monkeypatch, fake, env=None, argv=("alerting_test.py",)):
    monkeypatch.setattr(m, "request", fake.request)
    clock = [0.0]  # sleeping moves a fake clock, so a wait's deadline passes at once
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(m.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(m.time, "strftime", lambda f, *a: "20261031120000" if "%Y" in f else "12:00:00")
    for k in list(m.os.environ):
        if k in ENV or k == "ALERTING_TEST_LEGS":
            monkeypatch.delenv(k)
    for k, v in (env if env is not None else ENV).items():
        monkeypatch.setenv(k, v)
    return m.main(list(argv))


def test_every_leg_opening_and_closing_its_ticket_passes(monkeypatch, capsys):
    fake = Fake()
    assert run(monkeypatch, fake) == 0
    out = capsys.readouterr().out
    assert all(f"PASS {leg}" in out for leg in m.LEGS)
    assert all(t["state"] == "closed" for t in fake.tickets.values())


def test_a_refused_webhook_fails_its_leg_and_the_others_still_run_and_recover(monkeypatch, capsys):
    fake = Fake(refuse={"databasus"})
    assert run(monkeypatch, fake) == 1
    out = capsys.readouterr().out
    assert "FAIL databasus: n8n didn't take the failure (HTTP 403)" in out and "PASS dockhand" in out
    assert ("databasus", "recovery") in fake.sent  # sent even after its failure was refused


def test_a_ticket_that_never_closes_fails_and_says_so(monkeypatch, capsys):
    assert run(monkeypatch, Fake(never_close={"semaphore"})) == 1
    assert "FAIL semaphore: ticket #" in capsys.readouterr().out


def test_zammad_not_answering_is_a_failure_never_a_pass(monkeypatch, capsys):
    assert run(monkeypatch, Fake(zammad_down=True)) == 1
    out = capsys.readouterr().out
    assert "PASS" not in out and "couldn't ask" in out


def test_one_leg_alone_and_what_the_run_needs(monkeypatch, capsys):
    fake = Fake()
    assert run(monkeypatch, fake, dict(ENV, ALERTING_TEST_LEGS="dockhand")) == 0
    assert {leg for leg, _ in fake.sent} == {"dockhand"}
    assert run(monkeypatch, Fake(), dict(ENV, ALERTING_TEST_LEGS="pagerduty")) == 2
    assert run(monkeypatch, Fake(), {k: v for k, v in ENV.items() if k != "DOCKHAND_ALERT_TOKEN"}) == 2
    assert "missing: DOCKHAND_ALERT_TOKEN" in capsys.readouterr().out
    assert run(monkeypatch, Fake(), dict(ENV, N8N_URL="http://n8n.example")) == 2
    assert run(monkeypatch, Fake(), argv=("alerting_test.py", "--legs=x")) == 2  # extra variables as arguments
