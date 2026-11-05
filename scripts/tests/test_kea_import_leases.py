"""docker/kea/tools/import_leases.py: each lease sent to Kea with lease4-update and
force-create, each refusal named and the run failed. Against a stand-in for Kea's API."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docker/kea/tools/import_leases.py"
CREDENTIALS = "kea:" + "not-a-real-one"


def load():
    spec = importlib.util.spec_from_file_location("kea_import_leases_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load()

LEASES = [{"ip-address": "192.0.2.10", "hw-address": "aa:bb:cc:dd:ee:01", "subnet-id": 2, "valid-lft": 3600},
          {"ip-address": "192.0.2.11", "hw-address": "aa:bb:cc:dd:ee:02", "subnet-id": 2, "valid-lft": 3600}]


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def kea(monkeypatch, tmp_path):
    sent, refuse, as_list = [], set(), [False]

    def urlopen(req, timeout):
        body = json.loads(req.data)
        sent.append((req, body))
        ip = body["arguments"]["ip-address"]
        answer = {"result": 1, "text": f"{ip}: refused"} if ip in refuse else {"result": 0, "text": "ok"}
        return Answer(json.dumps([answer] if as_list[0] else answer).encode())

    monkeypatch.setattr(m.urllib.request, "urlopen", urlopen)
    (tmp_path / "creds").write_text(CREDENTIALS + "\n", encoding="utf-8")
    (tmp_path / "leases.json").write_text(json.dumps(LEASES), encoding="utf-8")
    args = ["--url", "http://kea.example.invalid:8000/", "--credentials-file", str(tmp_path / "creds"),
            str(tmp_path / "leases.json")]
    return sent, refuse, as_list, args


def test_each_lease_goes_in_with_force_create_and_basic_auth(kea, capsys):
    sent, _, _, args = kea
    assert m.main(args) == 0
    assert [b for _, b in sent] == [{"command": "lease4-update", "arguments": {**lease, "force-create": True}}
                                    for lease in LEASES]
    req = sent[0][0]
    assert req.get_header("Authorization") == "Basic " + base64.b64encode(CREDENTIALS.encode()).decode()
    assert req.full_url == "http://kea.example.invalid:8000/"
    out = capsys.readouterr().out
    assert "imported 2 of 2 leases" in out and "not-a-real-one" not in out


def test_a_refused_lease_is_named_and_fails_the_run(kea, capsys):
    sent, refuse, _, args = kea
    refuse.add("192.0.2.11")
    assert m.main(args) == 1
    out = capsys.readouterr().out
    assert "imported 1 of 2 leases" in out and "FAILED 192.0.2.11: 192.0.2.11: refused" in out


def test_the_old_control_agent_s_list_answer_is_read_too(kea):
    _, refuse, as_list, args = kea
    as_list[0] = True
    assert m.main(args) == 0
    refuse.add("192.0.2.10")
    assert m.main(args) == 1
