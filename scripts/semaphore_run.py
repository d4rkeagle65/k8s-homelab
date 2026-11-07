"""Start a Semaphore template from a session and read its result: how a Claude session runs
the Semaphore tasks CLAUDE.md lets it start (the `semaphore` skill, .claude/skills/).

  semaphore_run.py templates [TEXT]          the project's templates and their survey variables
  semaphore_run.py run TEMPLATE [-v NAME=VALUE ...] [-b NAME=VALUE ...] [--real]
  semaphore_run.py show TASK_ID [--wait]     an earlier task's status and log

`run` starts TEMPLATE (its name, or its ID) with each survey variable given, waits for the
task, prints its log as it arrives and exits with its result. It is a dry run unless --real
is given: `"params": {"dry_run": true}`, which Semaphore honours only for an Ansible template,
so a template of any other kind needs --real, and its own survey variable (a `dry_run` or
`DRY_RUN`) is what keeps it read-only. The task is read back before waiting: a dry run that
Semaphore didn't store as one is stopped at once and the run fails. A task that ends waiting
for confirmation (an OpenTofu plan) is rejected; nothing here ever confirms one.

Survey values go where Semaphore's own task form puts them: `environment` (a JSON string of
extra variables), and `secret` for a secret variable. A name the template's survey doesn't
have is refused, as is a required one left out (its default counts). An Ansible extra variable
holding `{{` or `{%` is refused too, since Ansible would expand it: -b NAME=VALUE sends the
value base64-encoded as NAME_b64 instead, for a playbook that reads one ("Tool: Run Command"
reads `run_command_b64`).

Settings, each from the environment or else a file in the home folder (never from git):
  SEMAPHORE_URL      or ~/.semaphore-url     Semaphore's address, e.g. https://semaphore.<domain>
  SEMAPHORE_TOKEN    or ~/.semaphore-token   an API token (Semaphore: your user > API Tokens)
  SEMAPHORE_PROJECT  optional, a project's name or ID; with one project, that one

Exit 0: the task succeeded (or `show` read it); 1: the task failed, was stopped or rejected;
2: it couldn't be started or read (a setting missing, a name refused, Semaphore not answering);
3: still running when --timeout ran out (its ID is printed: `show TASK_ID --wait` follows it),
or, for `show --wait`, a task waiting for confirmation (left for its owner).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ANSIBLE = {"", "ansible"}  # a template's `app`: empty is Ansible, Semaphore's default
FINISHED = {"success": 0, "error": 1, "stopped": 1, "rejected": 1}
WAITING_CONFIRMATION = "waiting_confirmation"
EXPANDED_BY_ANSIBLE = ("{{", "{%")

urlopen = urllib.request.urlopen
sleep = time.sleep
clock = time.monotonic


class Refused(Exception):
    """What was asked can't be done as asked: nothing was started."""


class Unanswered(Exception):
    """Semaphore didn't give an answer: a refused connection, an error status, not JSON."""


def setting(name: str, filename: str, env=os.environ, home: Path | None = None) -> str:
    """The value from the environment, else from ~/<filename>; empty if neither has one."""
    value = (env.get(name) or "").strip()
    if value:
        return value
    path = (home or Path.home()) / filename
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


class Semaphore:
    def __init__(self, url: str, token: str):
        self.api = url.rstrip("/") + "/api"
        token = token[len("Bearer "):] if token.startswith("Bearer ") else token
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def call(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = dict(self.headers, **({"Content-Type": "application/json"} if data else {}))
        request = urllib.request.Request(self.api + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace").strip()[:300]
            raise Unanswered(f"Semaphore answered {method} {path} with HTTP {e.code}"
                             + (f": {detail}" if detail else "")) from e
        except (urllib.error.URLError, OSError) as e:
            raise Unanswered(f"Semaphore didn't answer {method} {path}: {e}") from e
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except ValueError as e:
            raise Unanswered(f"Semaphore's answer to {method} {path} isn't JSON") from e


def connect(env=os.environ, home: Path | None = None) -> Semaphore:
    url = setting("SEMAPHORE_URL", ".semaphore-url", env, home)
    token = setting("SEMAPHORE_TOKEN", ".semaphore-token", env, home)
    missing = [n for n, v in (("SEMAPHORE_URL (or ~/.semaphore-url)", url),
                              ("SEMAPHORE_TOKEN (or ~/.semaphore-token)", token)) if not v]
    if missing:
        raise Refused("not set: " + ", ".join(missing))
    return Semaphore(url, token)


def pick_project(client: Semaphore, wanted: str) -> dict:
    projects = client.call("GET", "/projects")
    if not isinstance(projects, list):
        raise Unanswered("Semaphore's project list isn't a list")
    if wanted:
        found = [p for p in projects if str(p.get("id")) == wanted or p.get("name") == wanted]
    else:
        found = projects if len(projects) == 1 else []
    if len(found) != 1:
        names = ", ".join(f"{p.get('name')} ({p.get('id')})" for p in projects) or "none"
        raise Refused(f"which project? {'no project ' + repr(wanted) if wanted else 'more than one'};"
                      f" set SEMAPHORE_PROJECT to one of: {names}")
    return found[0]


def templates_of(client: Semaphore, pid) -> list[dict]:
    got = client.call("GET", f"/project/{pid}/templates")
    if not isinstance(got, list):
        raise Unanswered("Semaphore's template list isn't a list")
    return got


def find_template(templates: list[dict], wanted: str) -> dict:
    for match in (lambda t: str(t.get("id")) == wanted or t.get("name") == wanted,
                  lambda t: (t.get("name") or "").casefold() == wanted.casefold()):
        found = [t for t in templates if match(t)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise Refused(f"{len(found)} templates match {wanted!r}; give its ID")
    near = [t.get("name") for t in templates if wanted.casefold() in (t.get("name") or "").casefold()]
    raise Refused(f"no template {wanted!r}" + (f"; did you mean: {', '.join(near)}" if near else
                                               "; `templates` lists them"))


def pairs(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items:
        name, eq, value = item.partition("=")
        if not eq or not name:
            raise Refused(f"{item!r} isn't NAME=VALUE")
        if name in out:
            raise Refused(f"{name} given twice")
        out[name] = value
    return out


def task_request(template: dict, values: dict[str, str], encoded: dict[str, str], real: bool) -> dict:
    """The body of POST /tasks, as Semaphore's task form builds it; Refused if it can't be."""
    app = template.get("app") or ""
    survey = {v.get("name"): v for v in template.get("survey_vars") or []}
    if not real and app not in ANSIBLE:
        raise Refused(f"{template.get('name')!r} is a {app} template, which Semaphore runs with no dry"
                      " run: pass --real, with its own survey variable keeping it read-only if it has one")
    for name in [*values, *encoded]:
        if name not in survey:
            raise Refused(f"{template.get('name')!r} has no survey variable {name!r}; it has: "
                          + (", ".join(survey) or "none"))
        if name in values and name in encoded:
            raise Refused(f"{name} given both plain and base64")
    plain, secret = {}, {}
    for name, var in survey.items():
        given = name in values or name in encoded
        if not given and var.get("default_value"):
            values = {**values, name: var["default_value"]}
        elif not given:
            if var.get("required"):
                raise Refused(f"{template.get('name')!r} needs {name!r} ({var.get('title') or name})")
            continue
        if name in encoded:
            plain[f"{name}_b64"] = base64.b64encode(encoded[name].encode("utf-8")).decode("ascii")
            continue
        value = values[name]
        kind = var.get("type") or ""
        if kind == "int" and not value.lstrip("-").isdigit():
            raise Refused(f"{name} takes a whole number, not {value!r}")
        if kind == "enum":
            allowed = [str(e.get("value")) for e in var.get("values") or []]
            if value not in allowed:
                raise Refused(f"{name} takes one of {', '.join(allowed)}, not {value!r}")
        if app in ANSIBLE and any(mark in value for mark in EXPANDED_BY_ANSIBLE):
            raise Refused(f"{name}'s value holds {{{{ or {{%, which Ansible would expand: send it with"
                          f" -b {name}=... if the playbook reads {name}_b64")
        (secret if kind == "secret" else plain)[name] = value
    body = {"template_id": template["id"], "environment": json.dumps(plain), "params": {}}
    if secret:
        body["secret"] = json.dumps(secret)
    if not real:
        body["params"]["dry_run"] = True
    return body


def follow(client: Semaphore, pid, task_id, timeout: float, reject: bool, poll: float = 2.0, out=print) -> int:
    """Print the task's log as it grows until it ends; its exit code (FINISHED, or 3 at timeout).

    A task waiting for confirmation is rejected when `reject` (a task this run started), else
    left for its owner and reported (3)."""
    printed, rejected, deadline = 0, False, clock() + timeout
    while True:
        task = client.call("GET", f"/project/{pid}/tasks/{task_id}") or {}
        lines = client.call("GET", f"/project/{pid}/tasks/{task_id}/output") or []
        for line in lines[printed:]:
            out(line.get("output", ""))
        printed = max(printed, len(lines))
        status = task.get("status")
        if status in FINISHED:
            out(f"task {task_id}: {status}")
            return FINISHED[status]
        if status == WAITING_CONFIRMATION and not reject:
            out(f"task {task_id}: waiting for confirmation, left as it is (only a task this run started is rejected)")
            return 3
        if status == WAITING_CONFIRMATION and not rejected:
            client.call("POST", f"/project/{pid}/tasks/{task_id}/reject")
            out(f"task {task_id}: waiting for confirmation, so rejected (nothing here confirms)")
            rejected = True
        if clock() >= deadline:
            out(f"task {task_id}: still {status} after {timeout:g}s; `show {task_id} --wait` follows it")
            return 3
        sleep(poll)


def start(client: Semaphore, pid, body: dict) -> int:
    """POST the task and read it back; a dry run not stored as one is stopped at once."""
    created = client.call("POST", f"/project/{pid}/tasks", body) or {}
    task_id = created.get("id")
    if not task_id:
        raise Unanswered("Semaphore started no task (its answer has no ID)")
    stored = client.call("GET", f"/project/{pid}/tasks/{task_id}") or {}
    wanted_dry = body["params"].get("dry_run") is True
    if wanted_dry and (stored.get("params") or {}).get("dry_run") is not True:
        client.call("POST", f"/project/{pid}/tasks/{task_id}/stop", {"force": True})
        raise Unanswered(f"task {task_id} wasn't stored as a dry run, so it was stopped; read its log"
                         " in Semaphore to see whether anything ran")
    return task_id


def describe(template: dict) -> str:
    survey = []
    for v in template.get("survey_vars") or []:
        mark = "*" if v.get("required") else ""
        kind = f":{v['type']}" if v.get("type") else ""
        survey.append(f"{v.get('name')}{mark}{kind}")
    return (f"{template.get('id'):>5}  {template.get('name')}  [{template.get('app') or 'ansible'}]"
            + (f"  survey: {' '.join(survey)}" if survey else ""))


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", default="", help="a project's name or ID (default: SEMAPHORE_PROJECT, or the only one)")
    sub = p.add_subparsers(dest="command", required=True)
    ls = sub.add_parser("templates", help="list the project's templates and their survey variables")
    ls.add_argument("text", nargs="?", default="", help="only templates whose name holds this")
    run = sub.add_parser("run", help="start a template, follow its log and exit with its result")
    run.add_argument("template", help="the template's name (exact, or differing only in case) or ID")
    run.add_argument("-v", "--var", action="append", default=[], metavar="NAME=VALUE",
                     help="a survey variable's value; repeat for each")
    run.add_argument("-b", "--b64", action="append", default=[], metavar="NAME=VALUE",
                     help="a survey variable's value sent base64-encoded as NAME_b64 (for {{ ... }})")
    run.add_argument("--real", action="store_true",
                     help="run for real; without it, an Ansible dry run (other kinds refuse to start)")
    run.add_argument("--message", default="", help="the task's message in Semaphore's history")
    run.add_argument("--timeout", type=float, default=900, help="seconds to wait for the task (default 900)")
    run.add_argument("--no-wait", action="store_true", help="print the task's ID and return once it is started")
    show = sub.add_parser("show", help="an earlier task's status and log")
    show.add_argument("task", type=int, help="the task's ID")
    show.add_argument("--wait", action="store_true", help="follow it until it ends")
    show.add_argument("--timeout", type=float, default=900, help="seconds to wait with --wait (default 900)")
    return p.parse_args(argv)


def main(argv=None, env=os.environ, home: Path | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = parse(argv)
    try:
        client = connect(env, home)
        project = pick_project(client, args.project or (env.get("SEMAPHORE_PROJECT") or "").strip())
        pid = project["id"]
        if args.command == "templates":
            for t in templates_of(client, pid):
                if args.text.casefold() in (t.get("name") or "").casefold():
                    print(describe(t))
            return 0
        if args.command == "show":
            if args.wait:
                return follow(client, pid, args.task, args.timeout, reject=False)
            task = client.call("GET", f"/project/{pid}/tasks/{args.task}") or {}
            for line in client.call("GET", f"/project/{pid}/tasks/{args.task}/output") or []:
                print(line.get("output", ""))
            print(f"task {args.task}: {task.get('status')}")
            return 0
        template = find_template(templates_of(client, pid), args.template)
        body = task_request(template, pairs(args.var), pairs(args.b64), args.real)
        if args.message:
            body["message"] = args.message
        task_id = start(client, pid, body)
        print(f"task {task_id}: {template.get('name')}, {'a real run' if args.real else 'a dry run'},"
              f" in project {project.get('name')}", flush=True)
        if args.no_wait:
            return 0
        return follow(client, pid, task_id, args.timeout, reject=True)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except Unanswered as e:
        print(f"no answer: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
