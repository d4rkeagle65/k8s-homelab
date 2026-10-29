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
HELMRELEASE = ROOT / "kubernetes/prod/apps/n8n/n8n/app/helmrelease.yaml"
# The runner's default N8N_RUNNERS_BUILTINS_DENY (task-runner-python's constants.py at 2.43.3).
DENIED = ("eval", "exec", "compile", "open", "input", "breakpoint", "getattr", "object", "type",
          "vars", "setattr", "delattr", "hasattr", "dir", "memoryview", "globals", "locals")


def allowed_modules() -> set[str]:
    """The standard-library modules the Python runner allows (the launcher file's override)."""
    found = re.findall(r'"N8N_RUNNERS_STDLIB_ALLOW":\s*"([^"]*)"', HELMRELEASE.read_text(encoding="utf-8"))
    assert len(found) == 1, "the launcher file's N8N_RUNNERS_STDLIB_ALLOW, once"
    return {m.strip() for m in found[0].split(",") if m.strip()}


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
def test_python_code_imports_only_what_the_runner_allows_and_calls_no_denied_builtin(path):
    allowed = allowed_modules()
    for node in python_nodes(json.loads(path.read_text(encoding="utf-8"))):
        code = node["parameters"]["pythonCode"]
        imported = {m.split(".")[0] for m in re.findall(r"^\s*(?:import|from)\s+([\w.]+)", code, re.M)}
        assert imported <= allowed, f"{node['name']}: imports {sorted(imported - allowed)}, which the runner refuses"
        assert not re.search(r"^\s*class\s", code, re.M), f"{node['name']}: class needs __build_class__, denied"
        used = [b for b in DENIED if re.search(rf"(?<![\w.]){b}\(", code)]
        assert not used, f"{node['name']}: denied builtins {used}"


def test_the_launcher_file_is_the_running_runners_tags():
    """The launcher file is the runners image's own, copied at one tag: a Renovate bump of the
    image fails here until the file is compared with the new tag's and its annotation moved."""
    text = HELMRELEASE.read_text(encoding="utf-8")
    image = re.search(r"repository: n8nio/runners\s+tag: (\S+)", text).group(1)
    copied = re.search(r'homelab.local/runners-tag: "([^"]+)"', text).group(1)
    assert copied == image, f"the launcher file was copied at {copied}; the runners image is {image}"
    assert "N8N_RUNNERS_CONFIG_PATH" in text and "/etc/n8n-runners/n8n-task-runners.json" in text


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


# --- "Zammad: tickets in" -----------------------------------------------------------------

TICKETS_IN = "zammad-tickets-in.json"


def event(node, body):
    out, printed = run_code(workflow(TICKETS_IN), node, [{"json": {"headers": {}, "body": body}}])
    return (out[0]["json"] if out else None), printed


def semaphore(name="Host: Proxmox Upgrade", status="❌ ERROR"):
    """Semaphore v2.19's Slack alert, as its slack.tmpl renders it."""
    return {"attachments": [{"title": f"Task: {name}", "title_link": "https://semaphore.example/project/1/templates/2?t=1185",
                             "text": f"execution #1185, status: {status}!", "color": "danger", "mrkdwn_in": ["text"],
                             "fields": [{"title": "Author", "value": "—", "short": True}]}]}


def test_a_semaphore_failure_opens_and_the_templates_next_success_closes_it():
    down, _ = event("Semaphore → event", semaphore())
    up, _ = event("Semaphore → event", semaphore(status="✅ SUCCESS"))
    assert down["host"] == up["host"] == "Semaphore Host: Proxmox Upgrade" and down["service"] == "semaphore"
    assert down["state"] == "CRITICAL" and up["state"] == "OK"
    assert "execution #1185" in down["text"] and "?t=1185" in down["text"]


def test_a_semaphore_confirmation_wait_or_an_empty_alert_is_nothing():
    for body in (semaphore(status="⚠️ WAITING_CONFIRMATION"), {}, {"attachments": "x"}, None):
        out, printed = event("Semaphore → event", body)
        assert out is None and printed


def test_databasus_keys_a_ticket_by_database_and_kind_and_success_closes_it():
    fail, _ = event("Databasus → event", {"heading": '❌ Backup failed for database "kea" (workspace "home")', "message": "pg_dump: timeout"})
    up, _ = event("Databasus → event", {"heading": '✅ Backup completed for database "kea" (workspace "home")', "message": "12 MB"})
    assert fail["host"] == up["host"] == "Databasus backup kea" and fail["service"] == "databasus"
    assert fail["state"] == "CRITICAL" and up["state"] == "OK" and "pg_dump: timeout" in fail["text"]


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
        f, _ = event("Databasus → event", {"heading": heading})
        assert (f["host"], f["state"]) == (host, state), heading
    assert event("Databasus → event", {"message": "no heading"})[0] is None


def decide(ev, tickets):
    tickets = tickets if isinstance(tickets, dict) else ok(tickets)
    out, _ = run_code(workflow(TICKETS_IN), "Decide", [{"json": {"event": ev, "tickets": tickets}}])
    return [o["json"] for o in out]


FAIL = {"host": "Semaphore Sync: x", "service": "semaphore", "state": "CRITICAL", "text": "execution #9, status: ERROR!"}
UP = dict(FAIL, state="OK")


def test_a_failure_with_no_open_ticket_opens_one_keyed_as_the_checkmk_endpoint_did():
    [w] = decide(FAIL, [ck(5, "Semaphore other", "semaphore"), {"id": 6, "preferences": {}}])
    assert (w["action"], w["method"], w["path"]) == ("create", "POST", "/api/v1/tickets")
    b = w["body"]
    assert b["title"] == "Semaphore Sync: x is CRITICAL" and b["group"] == "Alerts" and b["customer_id"] == 1
    assert b["preferences"] == {"check_mk": {"host": "Semaphore Sync: x", "service": "semaphore"}}
    assert b["article"]["body"] == FAIL["text"] and b["article"]["sender"] == "Customer" and b["article"]["internal"] is False


def test_a_repeat_adds_a_note_to_the_open_ticket():
    [w] = decide(FAIL, [ck(5, "Semaphore Sync: x", "semaphore")])
    assert (w["action"], w["path"], w["body"]["ticket_id"], w["expect"]) == ("note", "/api/v1/ticket_articles", 5, "open")


def test_a_recovery_closes_every_open_ticket_of_its_key_and_with_none_is_nothing():
    ws = decide(UP, [ck(5, "Semaphore Sync: x", "semaphore"), ck(7, "Semaphore Sync: x", "semaphore")])
    assert [(w["action"], w["path"], w["body"]["state"], w["expect"]) for w in ws] == [
        ("close", "/api/v1/tickets/5", "closed", "closed"), ("close", "/api/v1/tickets/7", "closed", "closed")]
    assert decide(UP, []) == [] and decide(dict(UP, state="UP"), []) == []


def test_no_write_when_zammads_open_tickets_cant_be_listed():
    for broken in ({"error": "ECONNREFUSED"}, {"statusCode": 200}):
        with pytest.raises(Exception, match="couldn't be listed"):
            decide(FAIL, broken)


def verify(decision, back):
    out, _ = run_code(workflow(TICKETS_IN), "Verify", [{"json": {"decision": decision, "back": back}}])
    return [o["json"] for o in out]


def test_verify_passes_only_what_zammad_reads_back():
    d = {"action": "create", "host": "Semaphore Sync: x", "service": "semaphore", "expect": "open"}
    t = dict(ck(9, "Semaphore Sync: x", "semaphore"), state="new")
    assert verify(d, ok(t))[0]["state"] == "new"
    with pytest.raises(Exception, match="state reads 'new'"):
        verify(dict(d, action="close", expect="closed"), ok(t))
    with pytest.raises(Exception, match="key reads"):
        verify(dict(d, host="other"), ok(t))
    with pytest.raises(Exception, match="couldn't be read back"):
        verify(d, {"error": "timed out"})


def test_tickets_in_writes_only_with_write_on_and_answers_after_its_last_node():
    wf = workflow(TICKETS_IN)
    nodes = {n["name"]: n for n in wf["nodes"]}
    settings = nodes["Settings"]["parameters"]["assignments"]["assignments"]
    # On since Semaphore points here (QUEUE-n8n-zammad.md, stage 2).
    assert [a["value"] for a in settings if a["name"] == "write"] == [True]
    assert wf["connections"]["Writing?"]["main"][0][0]["node"] == "Zammad: write"
    assert "onError" not in nodes["Zammad: write"] and "onError" not in nodes["Zammad: open tickets"]
    for name in ("Semaphore", "Databasus"):
        p = nodes[name]["parameters"]
        assert p["authentication"] == "basicAuth" and p["responseMode"] == "lastNode" and nodes[name].get("webhookId")
