"""scripts/semaphore_run.py: a template started as Semaphore's own task form starts it (survey
values in `environment`, secrets in `secret`), a dry run unless --real and read back as one,
its log followed to its result; what can't be sent as asked refused before anything starts;
a plan waiting for confirmation rejected only when this run started it; and a Semaphore that
doesn't answer never read as an answer."""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "semaphore_run.py"


def load():
    spec = importlib.util.spec_from_file_location("semaphore_run_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = load()

TOKEN_VALUE = "abc"
ENV = {"SEMAPHORE_URL": "http://semaphore.example/", "SEMAPHORE_TOKEN": TOKEN_VALUE}

RUN_COMMAND = {"id": 7, "name": "Tool: Run Command", "app": "ansible", "survey_vars": [
    {"name": "run_command", "title": "Command", "required": True},
    {"name": "target_hosts", "title": "Hosts", "required": True},
    {"name": "run_timeout", "title": "Timeout", "type": "int", "default_value": "300"},
]}
SYNC = {"id": 8, "name": "Sync: NetBox", "app": "python", "survey_vars": [
    {"name": "DRY_RUN", "type": "enum", "values": [{"name": "yes", "value": "true"},
                                                   {"name": "no", "value": "false"}]},
    {"name": "API_KEY", "type": "secret"},
]}
PLAN = {"id": 9, "name": "Plan: Cloudflare", "app": "tofu", "survey_vars": []}
OTHER = {"id": 10, "name": "Tool: Run Command (old)", "app": "", "survey_vars": []}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeSemaphore:
    """Semaphore's API: projects, templates and tasks, each task walking through `statuses`
    (one per read) while its log grows by a line per read; every request recorded."""

    def __init__(self, statuses=("running", "success"), projects=None, keep_dry_run=True, fail=None):
        self.statuses, self.keep_dry_run, self.fail = list(statuses), keep_dry_run, fail
        self.projects = projects if projects is not None else [{"id": 1, "name": "Homelab"}]
        self.templates = [RUN_COMMAND, SYNC, PLAN, OTHER]
        self.tasks, self.calls = {}, []

    def __call__(self, request, timeout=30):
        method, path = request.get_method(), request.full_url.split("/api", 1)[1]
        body = json.loads(request.data) if request.data else None
        self.calls.append((method, path, body, request.headers.get("Authorization")))
        if self.fail:
            raise self.fail
        if path == "/projects":
            return self.answer(self.projects)
        if path == "/project/1/templates":
            return self.answer(self.templates)
        if path == "/project/1/tasks" and method == "POST":
            task = {"id": 1400 + len(self.tasks), "template_id": body["template_id"], "status": "waiting",
                    "environment": body["environment"], "params": dict(body.get("params") or {}),
                    "log": [], "reads": 0}
            if not self.keep_dry_run:
                task["params"].pop("dry_run", None)
            self.tasks[task["id"]] = task
            return self.answer({k: v for k, v in task.items() if k not in ("log", "reads")}, 201)
        parts = path.split("/")  # ['', 'project', '1', 'tasks', id, ...]
        task = self.tasks[int(parts[4])]
        if len(parts) == 6 and method == "POST":  # stop, reject
            task["status"] = {"stop": "stopped", "reject": "rejected"}[parts[5]]
            task["by"] = parts[5]
            return self.answer(None, 204)
        if len(parts) == 6:  # output
            return self.answer([{"task_id": task["id"], "output": line} for line in task["log"]])
        if task.get("by") is None and task["reads"] > 0:  # the read-back after POST is the first read
            task["status"] = self.statuses[min(task["reads"] - 1, len(self.statuses) - 1)]
            task["log"].append(f"line {task['reads']}")
        task["reads"] += 1
        return self.answer({k: v for k, v in task.items() if k not in ("log", "reads", "by")})

    @staticmethod
    def answer(value, status=200):
        return Response(b"" if value is None else json.dumps(value).encode("utf-8"))


@pytest.fixture
def sem(monkeypatch):
    def make(**kw):
        fake = FakeSemaphore(**kw)
        monkeypatch.setattr(m, "urlopen", fake)
        monkeypatch.setattr(m, "sleep", lambda s: None)
        return fake
    return make


def run(*argv, env=ENV, home=None):
    return m.main(list(argv), env=dict(env), home=home)


def posted(fake):
    return next(body for method, path, body, _ in fake.calls if method == "POST" and path.endswith("/tasks"))


# ---------------------------------------------------------------- settings and names

def test_settings_come_from_the_environment_or_else_the_home_folder(tmp_path):
    (tmp_path / ".semaphore-url").write_text("http://from-file.example\n", encoding="utf-8")
    (tmp_path / ".semaphore-token").write_text("Bearer xyz\n", encoding="utf-8")
    client = m.connect({}, tmp_path)
    assert client.api == "http://from-file.example/api"
    assert client.headers["Authorization"] == "Bearer xyz", "a pasted 'Bearer ' isn't doubled"
    assert m.connect(dict(ENV), tmp_path).api == "http://semaphore.example/api", "the environment wins"


def test_a_missing_setting_names_both_places_and_starts_nothing(tmp_path, capsys, sem):
    fake = sem()
    assert run("run", "Tool: Run Command", env={}, home=tmp_path) == 2
    err = capsys.readouterr().err
    assert "SEMAPHORE_URL (or ~/.semaphore-url)" in err and "SEMAPHORE_TOKEN (or ~/.semaphore-token)" in err
    assert fake.calls == []


def test_the_only_project_is_used_and_several_need_a_name(sem, capsys):
    fake = sem(projects=[{"id": 1, "name": "Homelab"}, {"id": 2, "name": "Lab"}])
    assert run("templates") == 2
    assert "Homelab (1), Lab (2)" in capsys.readouterr().err
    assert run("--project", "Homelab", "templates") == 0
    assert run("templates", env={**ENV, "SEMAPHORE_PROJECT": "1"}) == 0
    assert all(path in ("/projects", "/project/1/templates") for _, path, _, _ in fake.calls)


@pytest.mark.parametrize("wanted, found", [("Tool: Run Command", 7), ("7", 7), ("tool: run command", 7),
                                           ("Sync: NetBox", 8)])
def test_a_template_is_found_by_name_id_or_case(wanted, found):
    assert m.find_template(m_templates(), wanted)["id"] == found


def m_templates():
    return [RUN_COMMAND, SYNC, PLAN, OTHER]


def test_an_unknown_template_suggests_the_near_ones():
    with pytest.raises(m.Refused, match="did you mean: Tool: Run Command, Tool: Run Command \\(old\\)"):
        m.find_template(m_templates(), "Run Command")
    with pytest.raises(m.Refused, match="2 templates match"):
        m.find_template([RUN_COMMAND, dict(RUN_COMMAND, id=70)], "Tool: Run Command")


def test_templates_lists_each_with_its_survey(sem, capsys):
    sem()
    assert run("templates", "run command") == 0
    out = capsys.readouterr().out
    assert "Tool: Run Command  [ansible]  survey: run_command* target_hosts* run_timeout:int" in out
    assert "Sync: NetBox" not in out
    assert "Tool: Run Command (old)  [ansible]" in out, "an empty app is Ansible"


# ---------------------------------------------------------------- the request

def test_survey_values_go_in_environment_as_the_task_form_sends_them():
    body = m.task_request(RUN_COMMAND, {"run_command": "uptime", "target_hosts": "pve"}, {}, real=False)
    assert body["template_id"] == 7 and body["params"] == {"dry_run": True}
    assert json.loads(body["environment"]) == {"run_command": "uptime", "target_hosts": "pve",
                                               "run_timeout": "300"}, "a default counts"
    assert "secret" not in body and "dry_run" not in body, "a top-level dry_run is ignored by Semaphore"


def test_a_real_run_sends_no_dry_run():
    body = m.task_request(RUN_COMMAND, {"run_command": "uptime", "target_hosts": "pve"}, {}, real=True)
    assert body["params"] == {}


def test_base64_carries_braces_and_counts_for_the_required_name():
    command = "docker ps --format '{{.Names}}'"
    body = m.task_request(RUN_COMMAND, {"target_hosts": "docker"}, {"run_command": command}, real=True)
    env = json.loads(body["environment"])
    assert "run_command" not in env
    assert base64.b64decode(env["run_command_b64"]).decode("utf-8") == command


@pytest.mark.parametrize("values, encoded, real, refusal", [
    ({"target_hosts": "pve"}, {}, False, "needs 'run_command'"),
    ({"run_command": "uptime", "target_hosts": "pve", "hosts": "x"}, {}, False, "no survey variable 'hosts'"),
    ({"run_command": "echo {{ x }}", "target_hosts": "pve"}, {}, False, "Ansible would expand"),
    ({"run_command": "{% raw %}", "target_hosts": "pve"}, {}, False, "Ansible would expand"),
    ({"run_command": "a", "target_hosts": "pve", "run_timeout": "soon"}, {}, False, "whole number"),
    ({"run_command": "a", "target_hosts": "pve"}, {"run_command": "b"}, False, "both plain and base64"),
])
def test_what_cant_be_sent_as_asked_is_refused(values, encoded, real, refusal):
    with pytest.raises(m.Refused, match=refusal):
        m.task_request(RUN_COMMAND, values, encoded, real)


def test_another_kind_of_template_has_no_dry_run_so_needs_real():
    with pytest.raises(m.Refused, match="python template.*--real"):
        m.task_request(SYNC, {"DRY_RUN": "true"}, {}, real=False)
    body = m.task_request(SYNC, {"DRY_RUN": "true", "API_KEY": "{{kept}}"}, {}, real=True)
    assert json.loads(body["environment"]) == {"DRY_RUN": "true"}
    assert json.loads(body["secret"]) == {"API_KEY": "{{kept}}"}, "a secret goes apart; braces are Ansible's only"
    with pytest.raises(m.Refused, match="one of true, false"):
        m.task_request(SYNC, {"DRY_RUN": "maybe"}, {}, real=True)


def test_name_value_pairs():
    assert m.pairs(["a=1", "b=x=y", "c="]) == {"a": "1", "b": "x=y", "c": ""}
    for bad in (["a"], ["=1"], ["a=1", "a=2"]):
        with pytest.raises(m.Refused):
            m.pairs(bad)


# ---------------------------------------------------------------- the run

def test_a_dry_run_is_started_read_back_and_followed_to_success(sem, capsys):
    fake = sem(statuses=("running", "running", "success"))
    assert run("run", "Tool: Run Command", "-v", "run_command=uptime", "-v", "target_hosts=pve",
               "--message", "a check") == 0
    body = posted(fake)
    assert body["params"] == {"dry_run": True} and body["message"] == "a check"
    out = capsys.readouterr().out
    assert "task 1400: Tool: Run Command, a dry run, in project Homelab" in out
    assert out.count("line 1") == 1 and "line 3" in out, "each log line once, as it arrives"
    assert out.rstrip().endswith("task 1400: success")
    assert {auth for *_, auth in fake.calls} == {"Bearer abc"}


@pytest.mark.parametrize("status, code", [("error", 1), ("stopped", 1), ("success", 0)])
def test_the_exit_code_is_the_tasks_result(sem, status, code):
    sem(statuses=(status,))
    assert run("run", "Tool: Run Command", "-v", "run_command=a", "-v", "target_hosts=b", "--real") == code


def test_a_dry_run_not_stored_as_one_is_stopped_at_once(sem, capsys):
    fake = sem(keep_dry_run=False)
    assert run("run", "Tool: Run Command", "-v", "run_command=a", "-v", "target_hosts=b") == 2
    assert fake.tasks[1400]["by"] == "stop"
    assert ("POST", "/project/1/tasks/1400/stop", {"force": True}, "Bearer abc") in fake.calls
    assert "wasn't stored as a dry run" in capsys.readouterr().err


def test_a_real_run_isnt_stopped_for_having_no_dry_run(sem):
    fake = sem(keep_dry_run=False)
    assert run("run", "Tool: Run Command", "-v", "run_command=a", "-v", "target_hosts=b", "--real") == 0
    assert "by" not in fake.tasks[1400]


def test_a_plan_this_run_started_is_rejected_never_confirmed(sem, capsys):
    fake = sem(statuses=("running", "waiting_confirmation"))
    assert run("run", "Plan: Cloudflare", "--real") == 1
    assert fake.tasks[1400]["by"] == "reject"
    assert not any(path.endswith("/confirm") for _, path, _, _ in fake.calls)
    assert "task 1400: rejected" in capsys.readouterr().out


def test_show_wait_leaves_someone_elses_plan_waiting(sem, capsys):
    fake = sem(statuses=("waiting_confirmation",))
    fake.tasks[1500] = {"id": 1500, "template_id": 9, "status": "running", "params": {}, "log": [], "reads": 1}
    assert run("show", "1500", "--wait") == 3
    assert "by" not in fake.tasks[1500] and not any(method == "POST" for method, *_ in fake.calls)
    assert "left as it is" in capsys.readouterr().out


def test_a_task_still_running_at_the_timeout_says_how_to_follow_it(sem, monkeypatch, capsys):
    sem(statuses=("running",))
    ticks = iter(range(0, 10000, 5))
    monkeypatch.setattr(m, "clock", lambda: next(ticks))
    assert run("run", "Tool: Run Command", "-v", "run_command=a", "-v", "target_hosts=b", "--timeout", "12") == 3
    assert "still running after 12s; `show 1400 --wait` follows it" in capsys.readouterr().out


def test_no_wait_returns_once_started(sem):
    fake = sem()
    assert run("run", "Tool: Run Command", "-v", "run_command=a", "-v", "target_hosts=b", "--no-wait") == 0
    assert not any(path.endswith("/output") for _, path, _, _ in fake.calls)


def test_show_prints_an_earlier_tasks_log_and_status(sem, capsys):
    fake = sem()
    fake.tasks[1500] = {"id": 1500, "template_id": 7, "status": "success", "params": {},
                        "log": ["ok: [pve]"], "reads": 0, "by": "done"}
    assert run("show", "1500") == 0
    assert capsys.readouterr().out == "ok: [pve]\ntask 1500: success\n"


@pytest.mark.parametrize("failure, says", [
    (urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b"bad token")), "HTTP 401: bad token"),
    (urllib.error.URLError("connection refused"), "didn't answer GET /projects"),
])
def test_a_semaphore_that_doesnt_answer_is_never_an_answer(sem, capsys, failure, says):
    sem(fail=failure)
    assert run("templates") == 2
    assert says in capsys.readouterr().err


def test_an_answer_that_isnt_json_is_no_answer(monkeypatch, capsys):
    monkeypatch.setattr(m, "urlopen", lambda request, timeout=30: Response(b"<html>sign in</html>"))
    assert run("templates") == 2
    assert "isn't JSON" in capsys.readouterr().err
