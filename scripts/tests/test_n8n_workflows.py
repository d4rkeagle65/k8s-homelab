"""n8n's workflows (n8n/workflows/): each file is a workflow n8n can import, its credentials are
the ones n8n-credentials imports, and each Python Code node's own code is run here on the cases
it must answer (QUEUE-n8n-zammad.md, "Settled"), the way n8n's Python runner runs it: wrapped
in a function, given `_items`, with no imports and none of the builtins the runner denies.
"""
from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / "n8n" / "workflows").glob("*.json"))
CREDENTIALS = ROOT / "kubernetes/prod/apps/n8n/n8n/app/externalsecret.yaml"
# The runner's default N8N_RUNNERS_BUILTINS_DENY (task-runner-python's constants.py at 2.43.3).
DENIED = ("eval", "exec", "compile", "open", "input", "breakpoint", "getattr", "object", "type",
          "vars", "setattr", "delattr", "hasattr", "dir", "memoryview", "globals", "locals")


def workflow(name: str) -> dict:
    return json.loads((ROOT / "n8n" / "workflows" / name).read_text(encoding="utf-8"))


def python_nodes(wf: dict) -> list[dict]:
    return [n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.code"
            and n["parameters"].get("language") in ("pythonNative", "python")]


def run_code(wf: dict, node: str, items: list[dict]) -> tuple[list[dict], list]:
    """A Python Code node's output and what it printed, run as n8n's runner runs it."""
    code = next(n for n in python_nodes(wf) if n["name"] == node)["parameters"]["pythonCode"]
    printed = []
    scope = {"print": lambda *a: printed.append(a)}
    exec("def __node(_items):\n" + textwrap.indent(code, "    ") + "\n", scope)
    return scope["__node"](items), printed


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_each_workflow_connects_only_nodes_it_has(path):
    wf = json.loads(path.read_text(encoding="utf-8"))
    names = [n["name"] for n in wf["nodes"]]
    assert len(names) == len(set(names)), "two nodes share a name"
    for source, outputs in wf["connections"].items():
        assert source in names
        assert all(t["node"] in names for branch in outputs["main"] for t in branch)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_each_credential_named_is_one_n8n_imports(path):
    imported = set(re.findall(r'"id":\s*"([^"]+)"', CREDENTIALS.read_text(encoding="utf-8")))
    for node in json.loads(path.read_text(encoding="utf-8"))["nodes"]:
        for cred in (node.get("credentials") or {}).values():
            assert cred.get("id") in imported, f"{node['name']}: {cred} isn't in n8n-credentials"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_python_code_imports_nothing_and_calls_no_denied_builtin(path):
    for node in python_nodes(json.loads(path.read_text(encoding="utf-8"))):
        code = node["parameters"]["pythonCode"]
        assert not re.search(r"^\s*(import|from)\s", code, re.M), f"{node['name']}: an import the runner refuses"
        assert not re.search(r"^\s*class\s", code, re.M), f"{node['name']}: class needs __build_class__, denied"
        used = [b for b in DENIED if re.search(rf"(?<![\w.]){b}\(", code)]
        assert not used, f"{node['name']}: denied builtins {used}"


# --- "Zammad: close what's over": Verdict ----------------------------------------------

CLOSE = "zammad-close-whats-over.json"


def ck(tid, host, service):
    return {"id": tid, "number": str(tid), "title": f"{host} is CRITICAL",
            "preferences": {"check_mk": {"host": host, "service": service}}}


def ok(body):
    return {"body": body, "statusCode": 200}


def alert(name):
    return {"labels": {"alertname": name}}


NETBOX = [{"Names": ["/netbox"], "State": "running", "Status": "Up 2 hours (healthy)",
           "Labels": {"com.docker.compose.project": "netbox"}},
          {"Names": ["/netbox-worker"], "State": "exited", "Status": "Exited (1) 3 minutes ago",
           "Labels": {"com.docker.compose.project": "netbox"}}]


def verdicts(tickets, alerts=None, docker=None):
    gathered = {"tickets": ok(tickets) if not isinstance(tickets, dict) else tickets,
                "alerts": alerts if alerts is not None else ok([]),
                "docker": docker if docker is not None else [ok([]), ok([]), ok([])]}
    out, _ = run_code(workflow(CLOSE), "Verdict", [{"json": gathered}])
    return {o["json"]["id"]: (o["json"]["verdict"], o["json"]["reason"]) for o in out}


def test_a_persons_ticket_is_left_out():
    assert verdicts([ck(1, "TargetDown monitoring", "TargetDown"), {"id": 2, "preferences": {}}]).keys() == {1}


def test_an_alert_ticket_is_over_only_when_no_alert_of_its_name_is_active():
    t = [ck(7, "TargetDown monitoring", "TargetDown")]
    assert verdicts(t, ok([alert("TargetDown")]))[7][0] == "ongoing"
    assert verdicts(t, ok([alert("KubeJobFailed")]))[7][0] == "over"
    # The same name about something else keeps it open: a wrong guess never closes.
    assert verdicts([ck(8, "TargetDown other", "TargetDown")], ok([alert("TargetDown")]))[8][0] == "ongoing"


def test_alertmanager_that_cant_be_asked_closes_no_alert_ticket():
    t = [ck(7, "TargetDown monitoring", "TargetDown")]
    assert verdicts(t, {"error": "connect ECONNREFUSED"})[7][0] == "unknown"
    assert verdicts(t, {"statusCode": 200})[7][0] == "unknown"  # no body


def test_a_dockhand_container_or_stack_ticket_follows_what_docker_says():
    docker = [ok(NETBOX), {"error": "timed out"}, ok([])]
    v = verdicts([ck(1, "Dockhand Container netbox", "dockhand"), ck(2, "Dockhand Container netbox-worker", "dockhand"),
                  ck(3, "Dockhand Stack netbox", "dockhand"), ck(4, "Dockhand Container gone", "dockhand")], docker=docker)
    assert v[1] == ("over", "running and healthy")
    assert v[2][0] == "ongoing"
    assert v[3] == ("ongoing", "not running: netbox-worker")
    assert v[4] == ("unknown", "not found on any Docker host, 1 couldn't be asked")
    running = [dict(c, State="running", Status="Up 1 minute") for c in NETBOX]
    assert verdicts([ck(3, "Dockhand Stack netbox", "dockhand")], docker=[ok(running)])[3][0] == "over"


def test_an_unhealthy_or_starting_container_is_ongoing():
    for status in ("Up 1 minute (unhealthy)", "Up 5 seconds (health: starting)"):
        c = [{"Names": ["/netbox"], "State": "running", "Status": status}]
        assert verdicts([ck(1, "Dockhand Container netbox", "dockhand")], docker=[ok(c)])[1][0] == "ongoing"


def test_a_stacks_one_shot_that_exited_0_doesnt_keep_it_open():
    c = NETBOX[:1] + [dict(NETBOX[1], Status="Exited (0) 1 hour ago")]
    assert verdicts([ck(3, "Dockhand Stack netbox", "dockhand")], docker=[ok(c)])[3][0] == "over"


def test_docker_hosts_that_cant_be_asked_make_a_stack_unknown():
    down = [{"error": "refused"}] * 3
    assert verdicts([ck(3, "Dockhand Stack netbox", "dockhand")], docker=down)[3] == (
        "unknown", "3 of 3 Docker hosts couldn't be asked")


def test_unchecked_kinds_stay_open():
    v = verdicts([ck(1, "Semaphore Sync: x", "semaphore"), ck(2, "Databasus Netbox health", "databasus"),
                  ck(3, "Dockhand Environment offline", "dockhand")])
    assert {k: x[0] for k, x in v.items()} == {1: "unknown", 2: "unknown", 3: "unknown"}


def test_zammads_answer_in_either_shape_and_none_open():
    t = [ck(7, "TargetDown monitoring", "TargetDown")]
    assert verdicts(ok({"tickets": t}))[7][0] == "over"
    assert verdicts([]) == {}
    out, printed = run_code(workflow(CLOSE), "Verdict", [])
    assert out == [] and printed


def test_the_workflow_closes_only_what_verdict_calls_over_and_only_with_close_on():
    wf = workflow(CLOSE)
    nodes = {n["name"]: n for n in wf["nodes"]}
    over = nodes["Over?"]["parameters"]["conditions"]["conditions"][0]
    assert over["leftValue"] == "={{ $json.verdict }}" and over["rightValue"] == "over"
    assert wf["connections"]["Over?"]["main"][0][0]["node"] == "Closing?"
    assert wf["connections"]["Closing?"]["main"][0][0]["node"] == "Zammad: close it"
    settings = nodes["Settings"]["parameters"]["assignments"]["assignments"]
    assert [a["value"] for a in settings if a["name"] == "close"] == [False]  # the dry run, in git
