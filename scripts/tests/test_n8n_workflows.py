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


def verdicts(tickets, alerts=None, docker=None, pods=None):
    """pods: each Alertmanager pod's answer; alerts: one answer, given by both pods."""
    alerts = alerts if alerts is not None else ok([])
    gathered = {"tickets": ok(tickets) if not isinstance(tickets, dict) else tickets,
                "alerts": pods if pods is not None else [alerts, alerts],
                "docker": docker if docker is not None else [ok([]), ok([]), ok([])]}
    out, _ = run_code(workflow(CLOSE), "Verdict", [{"json": gathered}])
    return {o["json"]["id"]: (o["json"]["verdict"], o["json"]["reason"]) for o in out}


def test_a_persons_ticket_is_left_out():
    assert verdicts([ck(1, "TargetDown monitoring", "TargetDown"), {"id": 2, "preferences": {}}]).keys() == {1}


def test_an_alert_ticket_is_over_when_no_active_alert_names_its_host():
    t = [ck(7, "TargetDown monitoring", "TargetDown")]
    assert verdicts(t, ok([{"labels": {"alertname": "TargetDown", "namespace": "monitoring"}}]))[7][0] == "ongoing"
    assert verdicts(t, ok([alert("KubeJobFailed")]))[7][0] == "over"
    # The same alert about something else no longer keeps it open: hosts are rebuilt exactly.
    assert verdicts(t, ok([{"labels": {"alertname": "TargetDown", "namespace": "other"}}]))[7][0] == "over"


def test_an_alert_held_by_one_alertmanager_pod_is_active_and_a_pod_that_cant_answer_closes_nothing():
    """Alertmanager's pods share no alerts: Loki's reach each pod, but one may hold an alert the
    other doesn't (a restart, a send that failed), so every pod is asked."""
    t = [ck(7, "HostLogsSilent monitoring", "HostLogsSilent")]
    held = ok([{"labels": {"alertname": "HostLogsSilent", "namespace": "monitoring"}}])
    assert verdicts(t, pods=[ok([]), held])[7][0] == "ongoing"
    assert verdicts(t, pods=[ok([]), ok([])])[7][0] == "over"
    assert verdicts(t, pods=[ok([]), {"error": "connect ECONNREFUSED"}])[7][0] == "unknown"
    assert verdicts(t, pods=[])[7][0] == "unknown"  # no pod asked is no answer


def test_the_closing_check_and_tickets_in_name_an_alerts_ticket_with_one_rule():
    """Verdict rebuilds each active alert's host to match it to its ticket, so its rule must
    be the one tickets are opened with: the same lines in both nodes."""
    def block(wf, node):
        code = next(n for n in python_nodes(workflow(wf)) if n["name"] == node)["parameters"]["pythonCode"]
        return code[code.index("TARGET_LABELS = "):code.index('.strip()[:250]', code.index("def alert_host"))]
    assert block(CLOSE, "Verdict") == block(TICKETS_IN, "Alertmanager → alerts")


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
    events = ev if isinstance(ev, list) else [ev]
    out, _ = run_code(workflow(TICKETS_IN), "Decide", [{"json": {"events": events, "tickets": tickets}}])
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
    # The sources pointed here write (QUEUE-n8n-zammad.md); any other is a dry run.
    assert [a["value"] for a in settings if a["name"] == "write_sources"] == ["semaphore,databasus,dockhand,alertmanager"]
    writing = nodes["Writing?"]["parameters"]["conditions"]["conditions"][0]["leftValue"]
    assert "write_sources" in writing and "$json.source" in writing
    assert wf["connections"]["Writing?"]["main"][0][0]["node"] == "Zammad: write"
    assert nodes["Zammad: open tickets"].get("executeOnce") is True  # one listing for every event of a run
    assert "onError" not in nodes["Zammad: write"] and "onError" not in nodes["Zammad: open tickets"]
    assert nodes["Alertmanager"]["parameters"]["authentication"] == "headerAuth"
    assert nodes["Alertmanager"]["parameters"]["responseMode"] == "lastNode"  # a failure is a 500: it's sent again
    # ...and a run with nothing to write, its last node giving no item, answers 200 with no
    # body rather than n8n's 500 "No item to return", which Alertmanager would retry and
    # Semaphore logs as a failed alert.
    for name in ("Alertmanager", "Semaphore", "Databasus"):
        assert nodes[name]["parameters"].get("responseData") == "noData", name
    for name in ("Semaphore", "Databasus"):
        p = nodes[name]["parameters"]
        assert p["authentication"] == "basicAuth" and p["responseMode"] == "lastNode" and nodes[name].get("webhookId")


# --- "Zammad: tickets in": Dockhand ----------------------------------------------------------

def container(name, state="exited", status="Exited (1) 2 seconds ago", project="kea"):
    labels = {"com.docker.compose.project": project} if project else {"databasus.verification.agent_id": "a1"}
    return {"Names": [f"/{name}"], "State": state, "Status": status, "Labels": labels}


def died(name, action="die"):
    return {"title": f"Container {action.title()}", "message": f'Container "{name}" {action} (some/image:1)', "type": "error"}


def dockhand(note, docker=None, held=None):
    docker = docker if docker is not None else [ok([]), ok([]), ok([])]
    out, printed = run_code(workflow(TICKETS_IN), "Dockhand → event",
                            [{"json": {"note": note, "docker": docker, "held": held or {}}}])
    return (out[0]["json"] if out else None), printed


def test_a_dockhand_failure_opens_and_its_recovery_closes_the_same_ticket():
    down, _ = dockhand({"title": "Container unhealthy: kea-dhcp4", "message": "on env 2", "type": "failure"})
    up, _ = dockhand({"title": "Container healthy: kea-dhcp4", "message": "on env 2", "type": "success"})
    assert down["state"] == "CRITICAL" and up["state"] == "OK" and down["hold"] == up["hold"] == 0
    assert down["host"] == up["host"] == "Dockhand Container: kea-dhcp4" and down["service"] == "dockhand"
    assert "on env 2" in down["text"]
    assert dockhand({"title": "Git sync failed: kea", "type": "warning"})[0]["state"] == "WARNING"
    assert dockhand({"message": "no title"})[0] is None


def test_a_stack_event_is_keyed_by_its_stack_so_a_deploy_closes_its_failed_sync():
    hosts = [dockhand({"title": t, "message": m, "type": k})[0]["host"] for t, m, k in (
        ("Git sync failed", 'Stack "netbox" sync failed:  Image x Building', "error"),
        ("Stack deploy failed", 'Stack "netbox" deploy failed: y', "error"),
        ("Git stack deployed", 'Stack "netbox" deployed from git', "success"),
        ("Stack deployed", 'Stack "netbox" deployed successfully', "success"))]
    assert set(hosts) == {"Dockhand Stack netbox"}
    assert dockhand({"title": "Git sync failed", "message": 'Stack "lldpd" sync failed: z', "type": "error"})[0]["host"] == "Dockhand Stack lldpd"


def test_a_dockhand_title_starting_with_dockhand_isnt_doubled():
    assert dockhand({"title": "Dockhand Test Notification", "type": "info"})[0]["host"] == "Dockhand Test Notification"


def test_a_container_event_is_keyed_by_the_container_its_message_names_and_a_recovery_is_never_held():
    lists = [ok([container("kea-dhcp4"), container("netbox")]), ok([]), ok([])]
    die, _ = dockhand(died("kea-dhcp4"), lists)
    start, _ = dockhand({"title": "Container Start", "message": 'Container "kea-dhcp4" start (kea:3.2.1)', "type": "success"},
                        [{"error": "never asked"}] * 3)
    other, _ = dockhand(died("netbox"), lists)
    assert die["host"] == start["host"] == "Dockhand Container kea-dhcp4" and other["host"] == "Dockhand Container netbox"
    assert die["state"] == "CRITICAL" and start["state"] == "OK" and start["hold"] == 0 and "couldn't" not in start["text"]


def test_a_stack_containers_failure_is_held_and_the_others_are_nothing():
    lists = [ok([container("kea-dhcp4"), container("kea-db-init", status="Exited (0) 3 seconds ago"),
                 container("loose", project=None)]), ok([]), ok([])]
    held, printed = dockhand(died("kea-dhcp4"), lists)
    assert (held["hold"], held["container"], held["repeat"]) == (180, "kea-dhcp4", "note") and "held 180s" in printed[0][0]
    assert dockhand(died("kea-db-init"), lists)[0] is None          # a one-shot's normal finish
    assert dockhand(died("loose"), lists)[0] is None                # outside every stack
    assert dockhand(died("databasus-verif-1f2e"), lists)[0] is None  # removed by what started it


def test_a_host_that_cant_be_asked_never_drops_a_ticket():
    f, _ = dockhand(died("kea-dhcp4"), [ok([]), {"error": "timed out"}, ok([])])
    assert f["state"] == "CRITICAL" and f["hold"] == 180 and "1 of 3 Docker hosts couldn't be asked" in f["text"]
    f, _ = dockhand(died("kea-dhcp4"), [ok([container("kea-dhcp4")]), {"error": "timed out"}, ok([])])
    assert "couldn't be asked" not in f["text"]


def test_after_the_hold_a_failure_still_failed_is_a_ticket_and_one_running_again_is_nothing():
    lists = [ok([container("kea-dhcp4")]), ok([]), ok([])]
    held, _ = dockhand(died("kea-dhcp4"), lists)
    still, _ = dockhand(died("kea-dhcp4"), lists, held=held)
    assert still["hold"] == 0 and still["state"] == "CRITICAL" and still["host"] == "Dockhand Container kea-dhcp4"
    running = [ok([container("kea-dhcp4", state="running", status="Up 40 seconds")]), ok([]), ok([])]
    gone, printed = dockhand(died("kea-dhcp4"), running, held=held)
    assert gone is None and "running again" in printed[0][0]
    down, _ = dockhand(died("kea-dhcp4"), [{"error": "timed out"}] * 3, held=held)
    assert down["hold"] == 0 and "couldn't be asked" in down["text"]


def test_a_kill_holds_longer_than_its_die_and_adds_nothing_to_the_ticket_the_die_opened():
    lists = [ok([container("kea-postgres")]), ok([]), ok([])]
    die, _ = dockhand(died("kea-postgres"), lists)
    kill, _ = dockhand(died("kea-postgres", "kill"), lists)
    assert kill["hold"] > die["hold"] and (die["repeat"], kill["repeat"]) == ("note", "skip")
    after, _ = dockhand(died("kea-postgres", "kill"), lists, held=kill)
    assert decide(after, [ck(4, "Dockhand Container kea-postgres", "dockhand")]) == []   # the die's ticket: nothing added
    assert [w["action"] for w in decide(after, [])] == ["create"]                      # a kill alone still opens one
    assert [w["action"] for w in decide(dict(after, repeat="note"), [ck(4, "Dockhand Container kea-postgres", "dockhand")])] == ["note"]


def test_dockhands_branch_answers_at_once_and_holds_only_what_asks():
    wf = workflow(TICKETS_IN)
    nodes, c = {n["name"]: n for n in wf["nodes"]}, wf["connections"]
    hook = nodes["Dockhand"]["parameters"]
    assert hook["authentication"] == "basicAuth" and hook["responseMode"] == "onReceived"
    assert c["Hold?"]["main"][0][0]["node"] == "Wait out the hold" and c["Hold?"]["main"][1][0]["node"] == "Event"
    assert nodes["Wait out the hold"]["parameters"] == {"amount": "={{ $json.hold }}", "unit": "seconds"}
    assert c["Gather after the hold"]["main"][0][0]["node"] == "Dockhand → event"  # the same rules, after the hold


def test_an_environment_event_is_keyed_by_its_environment_so_online_closes_its_offline():
    off, _ = dockhand({"title": "Environment offline", "message": 'Environment "Local" is unreachable', "type": "error"})
    on, _ = dockhand({"title": "Environment online", "message": 'Environment "Local" is now reachable', "type": "success"})
    nas, _ = dockhand({"title": "Environment online", "message": 'Environment "NAS one" is now reachable', "type": "success"})
    assert off["host"] == on["host"] == "Dockhand Environment Local" and nas["host"] == "Dockhand Environment NAS one"
    assert (off["state"], on["state"], off["hold"]) == ("CRITICAL", "OK", 0)



def test_several_events_in_one_run_each_decide_and_one_key_opens_once():
    a = dict(FAIL, source="alertmanager")
    b = dict(FAIL, host="Semaphore other", source="alertmanager")
    ws = decide([a, b, dict(a)], [])
    assert [(w["action"], w["host"], w["source"]) for w in ws] == [
        ("create", "Semaphore Sync: x", "alertmanager"), ("create", "Semaphore other", "alertmanager")]


# --- "Zammad: tickets in": Alertmanager ------------------------------------------------------

def am_alert(status="firing", **labels):
    return {"status": status, "fingerprint": "abc123",
            "labels": {"alertname": "TargetDown", "severity": "warning", **labels},
            "annotations": {"summary": "Targets are down", "description": "1 of 5 targets are down."}}


def am_alerts(*alerts):
    out, _ = run_code(workflow(TICKETS_IN), "Alertmanager → alerts", [{"json": {"headers": {}, "body": {"alerts": list(alerts)}}}])
    return [o["json"] for o in out]


def am_events(alerts, members=None):
    items = [{"json": {"alert": a, "members": members if members is not None else ok({"status": "success", "data": {"result": []}})}}
             for a in am_alerts(*alerts)]
    out, _ = run_code(workflow(TICKETS_IN), "Alert → event", items)
    return [o["json"] for o in out]


def test_only_warning_and_critical_alerts_pass_never_the_plumbing():
    got = am_alerts(am_alert(severity="warning"), am_alert(severity="critical"), am_alert(severity="info"),
                    am_alert(alertname="Watchdog", severity="none"), am_alert(alertname="InfoInhibitor", severity="warning"))
    assert [a["state"] for a in got] == ["WARNING", "CRITICAL"]


def test_host_names_the_alert_and_its_target_and_service_the_alert():
    [e] = am_events([am_alert(instance="192.0.2.10:9100", job="proxmox-hosts", namespace="monitoring")])
    assert (e["service"], e["host"], e["state"], e["source"]) == (
        "TargetDown", "TargetDown monitoring/192.0.2.10:9100", "WARNING", "alertmanager")
    assert "Targets are down" in e["text"] and "job=proxmox-hosts" in e["text"]


def test_the_same_alert_firing_and_resolved_keys_the_same_ticket():
    firing, resolved = am_alerts(am_alert(pod="web-0", namespace="apps", severity="critical"),
                                 am_alert(status="resolved", pod="web-0", namespace="apps", severity="critical"))
    assert (firing["host"], firing["service"]) == (resolved["host"], resolved["service"])
    assert firing["state"] == "CRITICAL" and resolved["state"] == "OK"


def test_an_alert_with_no_target_label_falls_back_to_its_namespace_then_its_name():
    assert [a["host"] for a in am_alerts(am_alert(namespace="zammad"), am_alert())] == ["TargetDown zammad", "TargetDown"]


def test_a_group_alert_asks_for_its_members_narrowed_by_its_labels():
    [a] = am_alerts(am_alert(job="node-exporter", namespace="monitoring", service="node-exporter"))
    assert 'up{job="node-exporter",namespace="monitoring",service="node-exporter"} == 0' in a["query"]
    [a] = am_alerts(am_alert(alertname="KubeDeploymentReplicasMismatch", namespace="media", deployment="sonarr"))
    assert 'namespace="media",pod=~"sonarr-.*"' in a["query"]


def test_an_alert_about_one_thing_or_with_nothing_to_narrow_by_or_resolved_asks_nothing():
    got = am_alerts(am_alert(alertname="KubePodCrashLooping", pod="x-1", namespace="a"), am_alert(),
                    am_alert(alertname="KubeDeploymentReplicasMismatch", namespace="a", deployment='x".*'),
                    am_alert(status="resolved", job="j", namespace="n"))
    assert [a["query"] for a in got] == ["", "", "", ""]


def test_the_ticket_lists_the_members_down_with_their_node_and_address():
    members = ok({"status": "success", "data": {"result": [
        {"metric": {"pod": "exporter-b", "node": "w2", "instance": "192.0.2.12:9100"}},
        {"metric": {"pod": "exporter-a", "node": "w1", "instance": "192.0.2.11:9100"}},
        {"metric": {"instance": "192.0.2.20:9100", "job": "proxmox-hosts"}}]}})
    [e] = am_events([am_alert(job="node-exporter", namespace="monitoring")], members)
    assert "Down now (3):\n- 192.0.2.20:9100\n- exporter-a on w1 (192.0.2.11:9100)\n- exporter-b on w2" in e["text"]


def test_a_failed_lookup_says_so_and_never_reads_as_none_down():
    for members in ({"error": "ECONNREFUSED"}, ok({"status": "error", "error": "bad query"})):
        [e] = am_events([am_alert(job="node-exporter", namespace="monitoring")], members)
        assert "Couldn't list which are down" in e["text"] and "Down now" not in e["text"]


def test_a_long_list_is_cut_and_says_how_many_more():
    members = ok({"status": "success", "data": {"result": [{"metric": {"pod": f"p-{i:02}"}} for i in range(30)]}})
    [e] = am_events([am_alert(job="j", namespace="n")], members)
    assert "Down now (30, the first 25 listed):" in e["text"] and "- p-24" in e["text"] and "- p-25" not in e["text"]


def test_an_alert_without_a_query_lists_no_members_whatever_prometheus_said():
    [e] = am_events([am_alert(status="resolved", job="j", namespace="n")], {"error": "never asked"})
    assert "Down now" not in e["text"] and "Couldn't" not in e["text"]
