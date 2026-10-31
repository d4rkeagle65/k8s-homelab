"""Test every path into Zammad end to end: Semaphore's "Tool: Alerting test (script)".

Each source that opens tickets (QUEUE-n8n-zammad.md; n8n/workflows/zammad-tickets-in.json)
gets a failure, which must open a ticket with that source's key, then its recovery, which
must close it:

  alertmanager  a marker line pushed to Loki (its push route, LOKI_PUSH_URL), which Loki's
                HomelabAlertingTest rule turns into an alert, Alertmanager routes to n8n,
                and n8n tickets. The whole path: Loki's ruler, both Alertmanager pods'
                routing, n8n, Zammad. It resolves by itself once the line is out of the
                rule's two-minute window, and Alertmanager sends the recovery at its next
                group interval, so this leg takes most of the run's ten minutes.
  semaphore     a task alert in Semaphore's Slack format, posted to n8n's webhook
  databasus     a failed, then completed, backup, posted to n8n's webhook
  dockhand      a stack's failed, then successful, deploy, posted to n8n's webhook (a
                stack event, so no hold)

Those three test n8n and Zammad with each sender's own token, not the sender itself:
Semaphore, Databasus and Dockhand send only when something happens to them.

Each run's keys carry the run's time, so a ticket a run left open never answers for the
next. Every recovery is sent even when a step before it failed, so a run leaves nothing
open but what Zammad didn't close in time, which it reports. A source that couldn't be
asked (n8n's webhook refusing, Zammad not answering) is a failure that says so, never a
pass. Exit 0: every leg opened and closed its ticket; 1: one didn't; 2: what the run needs
is missing.

The environment (Semaphore's "Alerting Test" variable group, environment variables only:
Semaphore hands a Python template's extra variables as arguments, which this refuses):
  N8N_URL, ZAMMAD_URL       https://n8n.<domain>, https://<Zammad's host>
  LOKI_PUSH_URL             https://loki-push.<domain>/loki/api/v1/push
  ZAMMAD_API_TOKEN          the token n8n writes with (Key Vault ZAMMAD-BRIDGE-API-TOKEN)
  SEMAPHORE_ALERT_TOKEN, DATABASUS_ALERT_TOKEN, DOCKHAND_ALERT_TOKEN
                            n8n's webhook passwords (Key Vault, the same names with -)
  LOKI_PUSH_PASSWORD        the push route's password (user alloy)
  ALERTING_TEST_LEGS        optional: which legs, comma-separated (default all four)
Standard library only.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

LEGS = ("alertmanager", "semaphore", "databasus", "dockhand")
NEEDS = {
    "alertmanager": ("LOKI_PUSH_URL", "LOKI_PUSH_PASSWORD"),
    "semaphore": ("N8N_URL", "SEMAPHORE_ALERT_TOKEN"),
    "databasus": ("N8N_URL", "DATABASUS_ALERT_TOKEN"),
    "dockhand": ("N8N_URL", "DOCKHAND_ALERT_TOKEN"),
}
# How long each wait lasts, in seconds: a webhook's ticket within a minute or two (Dockhand
# answers before it writes); the alert's opening within Loki's evaluation and Alertmanager's
# group wait, its closing within the window, an evaluation and a group interval (5 m).
WAIT_WEBHOOK = 120
WAIT_ALERT_OPEN = 300
WAIT_ALERT_CLOSE = 900
POLL = 10


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def request(method: str, url: str, headers: dict, body=None, timeout: float = 30):
    """(status, parsed JSON or None). A refused or unreachable request raises."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/json", **({"Content-Type": "application/json"} if data else {}), **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        try:
            return r.status, json.loads(raw) if raw.strip() else None
        except ValueError:
            return r.status, None


def events(run: str) -> dict:
    """Each leg's failure, recovery and the ticket key n8n gives them.

    scripts/tests/test_alerting_test.py runs n8n's own Code nodes on these, so a key here
    is the one n8n opens the ticket with.
    """
    name = f"alerting-test-{run}"
    return {
        "alertmanager": {
            "key": (f"HomelabAlertingTest monitoring/{name}", "HomelabAlertingTest"),
            "stream": {"source": "alerting-test", "job": name},
            "line": f"HomelabAlertingTest {run}: the alerting test's marker (Semaphore, Tool: Alerting test)",
        },
        "semaphore": {
            "key": (f"Semaphore Alerting test {run}", "semaphore"),
            "user": "semaphore",
            "failure": {"attachments": [{"title": f"Task: Alerting test {run}", "color": "danger",
                                         "text": "execution #0, status: ❌ ERROR!"}]},
            "recovery": {"attachments": [{"title": f"Task: Alerting test {run}", "color": "good",
                                          "text": "execution #0, status: ✅ SUCCESS!"}]},
        },
        "databasus": {
            "key": (f"Databasus backup {name}", "databasus"),
            "user": "databasus",
            "failure": {"heading": f'❌ Backup failed for database "{name}"', "message": "the alerting test"},
            "recovery": {"heading": f'✅ Backup completed for database "{name}"', "message": "the alerting test"},
        },
        "dockhand": {
            "key": (f"Dockhand Stack {name}", "dockhand"),
            "user": "dockhand",
            "failure": {"title": "Stack deploy failed", "message": f'Stack "{name}" deploy failed: the alerting test',
                        "type": "error"},
            "recovery": {"title": "Stack deployed", "message": f'Stack "{name}" deployed successfully',
                         "type": "success"},
        },
    }


class Zammad:
    def __init__(self, url: str, token: str):
        self.url, self.headers = url.rstrip("/"), {"Authorization": f"Token token={token}"}

    def open_ticket(self, key: tuple[str, str]) -> dict | None:
        """The open ticket with this key, or None. Zammad not answering raises."""
        query = urllib.parse.quote("state.name:new OR state.name:open")
        _, found = request("GET", f"{self.url}/api/v1/tickets/search?query={query}&limit=200&expand=true", self.headers)
        found = found.get("tickets") if isinstance(found, dict) else found
        for t in found if isinstance(found, list) else []:
            ck = ((t.get("preferences") or {}).get("check_mk") or {}) if isinstance(t, dict) else {}
            if (ck.get("host"), ck.get("service")) == key:
                return t
        return None

    def state(self, ticket_id) -> str:
        _, t = request("GET", f"{self.url}/api/v1/tickets/{ticket_id}?expand=true", self.headers)
        return str((t or {}).get("state") or "")


def wait(what: str, seconds: float, check):
    """check() until it returns something truthy, or None after seconds. A check that raises
    is retried: Zammad's search lags a write, and a hiccup isn't an answer."""
    deadline, last = time.monotonic() + seconds, None
    while True:
        try:
            got = check()
            if got:
                return got
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = e
        if time.monotonic() >= deadline:
            if last:
                log(f"  {what}: still couldn't ask at the end ({describe(last)})")
            return None
        time.sleep(POLL)


def describe(e: Exception) -> str:
    return f"HTTP {e.code}" if isinstance(e, urllib.error.HTTPError) else type(e).__name__


def post_webhook(env: dict, leg: str, ev: dict, kind: str) -> str | None:
    """POST a failure or recovery to n8n's webhook; None when n8n took it, else why not."""
    url = f"{env['N8N_URL'].rstrip('/')}/webhook/zammad/{leg}"
    try:
        status, _ = request("POST", url, {"Authorization": basic(ev["user"], env[f"{leg.upper()}_ALERT_TOKEN"])}, ev[kind])
    except (urllib.error.URLError, OSError) as e:
        return f"n8n didn't take the {kind} ({describe(e)})"
    return None if 200 <= status < 300 else f"n8n answered the {kind} with {status}"


def push_marker(env: dict, ev: dict) -> str | None:
    """Push the alert leg's marker line to Loki; None when Loki took it, else why not."""
    body = {"streams": [{"stream": ev["stream"], "values": [[str(time.time_ns()), ev["line"]]]}]}
    try:
        status, _ = request("POST", env["LOKI_PUSH_URL"], {"Authorization": basic("alloy", env["LOKI_PUSH_PASSWORD"])}, body)
    except (urllib.error.URLError, OSError) as e:
        return f"Loki didn't take the marker ({describe(e)})"
    return None if 200 <= status < 300 else f"Loki answered the marker with {status}"


def run_webhook_leg(env: dict, zammad: Zammad, leg: str, ev: dict) -> tuple[bool, str]:
    """Failure, its ticket, recovery, its close. The recovery is sent whatever happened."""
    problem = post_webhook(env, leg, ev, "failure")
    ticket = None
    if problem is None:
        ticket = wait(f"{leg}'s ticket", WAIT_WEBHOOK, lambda: zammad.open_ticket(ev["key"]))
        if ticket is None:
            problem = f"no open ticket keyed {ev['key'][0]!r} within {WAIT_WEBHOOK} s"
        else:
            log(f"  {leg}: ticket #{ticket.get('number')} opened")
    recovery = post_webhook(env, leg, ev, "recovery")
    if problem:
        return False, problem + ("; " + recovery if recovery else "")
    if recovery:
        return False, f"ticket #{ticket.get('number')} opened, then {recovery}: close it by hand"
    closed = wait(f"{leg}'s close", WAIT_WEBHOOK, lambda: zammad.state(ticket["id"]) == "closed")
    if not closed:
        return False, f"ticket #{ticket.get('number')} opened but wasn't closed within {WAIT_WEBHOOK} s"
    return True, f"ticket #{ticket.get('number')} opened and closed"


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        print("alerting_test.py takes no arguments: its settings are environment variables "
              "(Semaphore: environment variables in its variable group, never extra variables)")
        return 2
    env = dict(os.environ)
    legs = [x.strip() for x in env.get("ALERTING_TEST_LEGS", ",".join(LEGS)).split(",") if x.strip()]
    unknown = [x for x in legs if x not in LEGS]
    if unknown or not legs:
        print(f"ALERTING_TEST_LEGS names {unknown or 'none'}; the legs are {', '.join(LEGS)}")
        return 2
    needed = {"ZAMMAD_URL", "ZAMMAD_API_TOKEN"} | {v for leg in legs for v in NEEDS[leg]}
    missing = sorted(v for v in needed if not env.get(v, "").strip())
    not_https = sorted(v for v in needed if v.endswith("_URL") and env.get(v) and not env[v].startswith("https://"))
    if missing or not_https:
        print("; ".join(x for x in ("missing: " + ", ".join(missing) if missing else "",
                                     "not https: " + ", ".join(not_https) if not_https else "") if x)
              + " (Semaphore's \"Alerting Test\" variable group, scripts/alerting_test.py says what each is)")
        return 2
    run = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    ev = events(run)
    zammad = Zammad(env["ZAMMAD_URL"], env["ZAMMAD_API_TOKEN"])
    log(f"alerting test {run}: {', '.join(legs)}")
    results: dict[str, tuple[bool, str]] = {}
    started = time.monotonic()
    if "alertmanager" in legs:
        problem = push_marker(env, ev["alertmanager"])
        if problem:
            results["alertmanager"] = (False, problem)
        else:
            log("  alertmanager: marker pushed to Loki")
    for leg in ("semaphore", "databasus", "dockhand"):
        if leg in legs:
            log(f"{leg}:")
            results[leg] = run_webhook_leg(env, zammad, leg, ev[leg])
    if "alertmanager" in legs and "alertmanager" not in results:
        log("alertmanager: waiting for its ticket (Loki's rule, then Alertmanager's group wait)")
        key = ev["alertmanager"]["key"]
        left = max(WAIT_ALERT_OPEN - (time.monotonic() - started), 60)
        ticket = wait("the alert's ticket", left, lambda: zammad.open_ticket(key))
        if ticket is None:
            results["alertmanager"] = (False, f"no open ticket keyed {key[0]!r} within {WAIT_ALERT_OPEN} s of the push")
        else:
            log(f"  alertmanager: ticket #{ticket.get('number')} opened; waiting for the alert to resolve")
            closed = wait("the alert's close", WAIT_ALERT_CLOSE, lambda: zammad.state(ticket["id"]) == "closed")
            results["alertmanager"] = (True, f"ticket #{ticket.get('number')} opened and closed") if closed else (
                False, f"ticket #{ticket.get('number')} opened but wasn't closed within {WAIT_ALERT_CLOSE} s "
                       "(it closes when the alert resolves; check Alertmanager's recovery reached n8n)")
    log(f"alerting test {run}, {time.monotonic() - started:.0f} s:")
    for leg in legs:
        ok, why = results[leg]
        log(f"  {'PASS' if ok else 'FAIL'} {leg}: {why}")
    return 0 if all(ok for ok, _ in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
