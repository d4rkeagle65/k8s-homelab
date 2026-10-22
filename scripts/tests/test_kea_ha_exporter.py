"""docker/kea/ha_exporter.py: Kea's HA state for Prometheus and Homepage."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "docker" / "kea" / "ha_exporter.py"
_spec = importlib.util.spec_from_file_location("ha_exporter", _PATH)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

# status-get's arguments as Kea 3.2 gives them on the primary of a hot-standby pair.
HEALTHY = {
    "pid": 1,
    "high-availability": [{
        "ha-mode": "hot-standby",
        "ha-servers": {
            "local": {"role": "primary", "scopes": ["primary"], "server-name": "a", "state": "hot-standby"},
            "remote": {"role": "standby", "age": 3, "in-touch": True, "last-state": "hot-standby",
                       "communication-interrupted": False, "clock-skew": 0, "server-name": "b",
                       "last-scopes": []},
        },
    }],
}
FAKE_CREDENTIALS = "homepage:" + "not-a-real-one"


def with_remote(**changes):
    args = json.loads(json.dumps(HEALTHY))
    args["high-availability"][0]["ha-servers"]["remote"].update(changes)
    return args


def test_a_healthy_pair_is_normal():
    s = m.ha_status(HEALTHY)
    assert s["mode"] == "hot-standby" and s["normal"] is True
    assert s["local"] == {"role": "primary", "state": "hot-standby"}
    assert s["remote"]["role"] == "standby" and s["remote"]["state"] == "hot-standby"
    assert s["remote"]["age"] == 3


@pytest.mark.parametrize("changes", [
    {"last-state": "partner-down"},
    {"in-touch": False},
    {"communication-interrupted": True},
])
def test_a_failover_or_a_lost_partner_is_not_normal(changes):
    assert m.ha_status(with_remote(**changes))["normal"] is False


def test_the_local_server_out_of_its_mode_is_not_normal():
    args = json.loads(json.dumps(HEALTHY))
    args["high-availability"][0]["ha-servers"]["local"]["state"] = "waiting"
    assert m.ha_status(args)["normal"] is False


def test_status_get_without_ha_is_unanswered_not_normal():
    with pytest.raises(m.Unanswered):
        m.ha_status({"pid": 1})


def test_metrics_carry_each_servers_state_and_the_verdict():
    text = m.metrics(m.ha_status(HEALTHY))
    assert "kea_ha_api_up 1" in text
    assert 'kea_ha_state{server="local",role="primary",state="hot-standby",mode="hot-standby"} 1' in text
    assert 'kea_ha_state{server="remote",role="standby",state="hot-standby",mode="hot-standby"} 1' in text
    assert "kea_ha_partner_age_seconds 3" in text and "kea_ha_normal 1" in text
    assert "server-name" not in text and '"a"' not in text


def test_metrics_without_an_answer_say_only_that():
    assert m.metrics(None).strip().splitlines()[-1] == "kea_ha_api_up 0"
    assert "kea_ha_normal" not in m.metrics(None)


def test_a_label_value_cannot_break_out_of_its_quotes():
    args = json.loads(json.dumps(HEALTHY))
    args["high-availability"][0]["ha-servers"]["local"]["state"] = 'x"} 1\nkea_ha_normal 1'
    line = [x for x in m.metrics(m.ha_status(args)).splitlines() if 'server="local"' in x][0]
    assert line.count('"') - line.count('\\"') == 8


class Answer:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


@pytest.fixture
def credentials(tmp_path):
    path = tmp_path / "api-credentials"
    path.write_text(FAKE_CREDENTIALS + "\n", encoding="utf-8")
    return str(path)


def test_status_get_sends_basic_auth_and_unwraps_the_list(monkeypatch, credentials):
    sent = []

    def answer(request, timeout):
        sent.append(request)
        return Answer(json.dumps([{"result": 0, "arguments": HEALTHY}]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", answer)
    assert m.status_get("http://kea:8000/", credentials) == HEALTHY
    (request,) = sent
    assert json.loads(request.data) == {"command": "status-get"}
    assert request.get_header("Authorization").startswith("Basic ")


@pytest.mark.parametrize("failure", ["http", "refused", "result", "garbage"])
def test_a_failed_call_is_unanswered_and_never_names_the_credentials(monkeypatch, credentials, failure):
    def answer(request, timeout):
        if failure == "http":
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b""))
        if failure == "refused":
            raise urllib.error.URLError(ConnectionRefusedError("refused"))
        if failure == "result":
            return Answer(json.dumps([{"result": 1, "text": "unauthorized"}]).encode())
        return Answer(b"<html>")

    monkeypatch.setattr(urllib.request, "urlopen", answer)
    with pytest.raises(m.Unanswered) as raised:
        m.status_get("http://kea:8000/", credentials)
    message = str(raised.value)
    encoded = base64.b64encode(FAKE_CREDENTIALS.encode()).decode()
    assert "not-a-real-one" not in message and "homepage" not in message
    assert encoded[:12] not in message and "Basic" not in message


def test_a_missing_credentials_file_is_unanswered(tmp_path):
    with pytest.raises(m.Unanswered):
        m.status_get("http://kea:8000/", str(tmp_path / "absent"))
