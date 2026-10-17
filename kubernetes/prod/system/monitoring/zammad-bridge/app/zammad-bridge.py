"""Alertmanager's webhook into Zammad's Checkmk endpoint: an alert opens a ticket,
a repeat adds a note to it, and its recovery closes it (QUEUE-zammad.md).

Zammad keys an open ticket by `host` and `service`, and titles it "<host> is
<state>". So `service` is the alert's name, and `host` the alert's name with
what it's about (its instance, pod, node, job or namespace), which keeps one
ticket per alert and a readable title. `warning` and `critical` alerts only;
Alertmanager's route sends nothing else, and this checks again.

An alert about a group (TargetDown: "40% of the targets are down"; a Deployment
short of replicas) names the group, not its members. For those, the ticket also
lists the members down at that moment, asked of Prometheus (PROMETHEUS_URL); when
Prometheus can't answer, the ticket says so rather than listing none.

Dockhand's notifications arrive at /dockhand/<token> (Apprise's json://
format: title, message, type): failure and warning open a ticket, success and
info close it. The ticket's key is the title with its state words taken out, so
"Container unhealthy: x" and "Container healthy: x" are one ticket. The token is
DOCKHAND_TOKEN; unset, that endpoint answers 404 to everything.

A failed call to Zammad answers Alertmanager with 502, so it retries. The
Zammad URL carries the integration's token: it comes from the environment
(ZAMMAD_URL) and is never logged. Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import sys
import hmac
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SEVERITIES = {"warning", "critical"}
NEVER = {"Watchdog", "InfoInhibitor"}
# Labels that say what an alert is about, most specific first.
TARGET_LABELS = ("instance", "pod", "persistentvolumeclaim", "node", "job", "service")
# Labels left out of the ticket's text: Prometheus's own bookkeeping.
QUIET_LABELS = {"prometheus", "alertstate", "endpoint", "container", "uid"}
# The members behind an alert about a group: the PromQL selecting the ones down,
# and the alert's labels that narrow it. A workload's pods are matched by name.
# A target in a pod gets its pod's node; one outside the cluster (a Proxmox host)
# is kept as it is, by its instance.
UP_DOWN = ("(up{%(m)s} == 0) * on (namespace, pod) group_left (node)"
           ' max by (namespace, pod, node) (kube_pod_info{pod!=""}) or on (instance) (up{%(m)s} == 0)',
           ("job", "namespace", "service"))
PODS_NOT_READY = ('(kube_pod_status_ready{condition="true",%(m)s} == 0)'
                  " * on (namespace, pod) group_left (node) max by (namespace, pod, node) (kube_pod_info)",
                  ("namespace",))
MEMBERS = {
    "TargetDown": UP_DOWN,
    "AlertmanagerClusterDown": UP_DOWN,
    "KubeDeploymentReplicasMismatch": PODS_NOT_READY,
    "KubeDeploymentRolloutStuck": PODS_NOT_READY,
    "KubeStatefulSetReplicasMismatch": PODS_NOT_READY,
    "KubeStatefulSetUpdateNotRolledOut": PODS_NOT_READY,
    "KubeDaemonSetRolloutStuck": PODS_NOT_READY,
    "KubeDaemonSetMisScheduled": PODS_NOT_READY,
    "KubeDaemonSetNotScheduled": PODS_NOT_READY,
}
WORKLOAD_LABELS = ("deployment", "statefulset", "daemonset")
LISTED = 25


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), msg, flush=True)


def wanted(alert: dict) -> bool:
    labels = alert.get("labels") or {}
    return labels.get("severity") in SEVERITIES and labels.get("alertname") not in NEVER


def members_query(alert: dict) -> str | None:
    """The PromQL listing the members down behind this alert, or None when it
    isn't about a group (or names no group to narrow to)."""
    labels = alert.get("labels") or {}
    if labels.get("alertname") not in MEMBERS:
        return None
    template, keys = MEMBERS[labels["alertname"]]
    matchers = [f"{k}={json.dumps(labels[k])}" for k in keys if labels.get(k)]
    if (template, keys) == PODS_NOT_READY:
        workload = next((labels[k] for k in WORKLOAD_LABELS if labels.get(k)), "")
        if not re.fullmatch(r"[a-z0-9]([-a-z0-9.]*[a-z0-9])?", workload):
            return None
        matchers.append(f'pod=~"{workload}-.*"')
    if not matchers:
        return None
    return template % {"m": ",".join(matchers)}


def describe(series: list[dict]) -> list[str]:
    """One line per member: its pod or instance, its node, and its address."""
    out = []
    for item in series:
        m = item.get("metric") or {}
        name = m.get("pod") or m.get("instance") or m.get("job") or "?"
        line = name
        if m.get("node"):
            line += f" on {m['node']}"
        if m.get("instance") and m.get("instance") != name:
            line += f" ({m['instance']})"
        out.append(line)
    return sorted(set(out))


def members(alert: dict, prometheus: str) -> list[str]:
    """Lines for the ticket naming the members down now; empty when the alert
    isn't about a group. A failed query says so: it isn't "none down"."""
    query = members_query(alert)
    if query is None or not prometheus or alert.get("status") == "resolved":
        return []
    url = f"{prometheus.rstrip('/')}/api/v1/query?" + urllib.parse.urlencode({"query": query})
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            body = json.load(r)
        if body.get("status") != "success":
            raise ValueError(body.get("error") or "no success")
        found = describe(body["data"]["result"])
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as e:
        reason = getattr(e, "code", None) or getattr(e, "reason", None) or type(e).__name__
        return [f"Couldn't list which are down: Prometheus didn't answer ({reason})."]
    if not found:
        return ["Down now: none found (recovered since, or what's missing has no pod to list)."]
    shown = found[:LISTED]
    more = f", the first {LISTED} listed" if len(found) > LISTED else ""
    return [f"Down now ({len(found)}{more}):"] + [f"- {line}" for line in shown]


def to_checkmk(alert: dict, prometheus: str = "") -> dict:
    """Zammad's Checkmk fields for one Alertmanager alert."""
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}
    name = labels.get("alertname", "alert")
    target = next((labels[k] for k in TARGET_LABELS if labels.get(k)), "")
    namespace = labels.get("namespace", "")
    if namespace and namespace not in target:
        target = f"{namespace}/{target}" if target else namespace
    host = f"{name} {target}".strip()[:250]
    state = "OK" if alert.get("status") == "resolved" else labels.get("severity", "warning").upper()
    detail = [annotations.get("summary", ""), annotations.get("description", "")]
    detail += members(alert, prometheus)
    detail += [f"{k}={v}" for k, v in sorted(labels.items()) if k not in QUIET_LABELS]
    if annotations.get("runbook_url"):
        detail.append(f"runbook: {annotations['runbook_url']}")
    return {
        "event_id": alert.get("fingerprint") or host,
        "host": host,
        "service": name,
        "state": state,
        "text": "\n".join(line for line in detail if line),
    }


# Words that say a Dockhand event's state rather than what it's about.
STATE_WORDS = re.compile(r"\b(un)?healthy\b|\b(re)?start(ed|ing)?\b|\bstop(ped|ping)?\b|\bfail(ed|ure)?\b|"
                         r"\bsucce(ss|eded|ssful)\b|\berror\b|\bwarning\b|\bblocked\b|\bskipped\b|\bcompleted?\b",
                         re.IGNORECASE)
DOCKHAND_STATES = {"failure": "CRITICAL", "error": "CRITICAL", "warning": "WARNING", "success": "OK", "info": "OK"}


def dockhand_to_checkmk(note: dict) -> dict | None:
    """Zammad's Checkmk fields for one Dockhand notification (Apprise's JSON), or
    None when it carries no title to key a ticket by."""
    title = str(note.get("title") or "").strip()
    if not title:
        return None
    key = re.sub(r"\s+([:,|])", r"\1", re.sub(r"\s+", " ", STATE_WORDS.sub("", title))).strip(" :-|") or title
    key = re.sub(r"^dockhand\b[\s:-]*", "", key, flags=re.IGNORECASE) or key  # its titles often start "Dockhand"
    state = DOCKHAND_STATES.get(str(note.get("type") or "info").lower(), "WARNING")
    return {
        "event_id": "dockhand " + key,
        "host": ("Dockhand " + key)[:250],
        "service": "dockhand",
        "state": state,
        "text": "\n".join(x for x in (title, str(note.get("message") or "").strip()) if x),
    }


def send(url: str, fields: dict) -> str:
    """POST the fields to Zammad; its JSON answer's result, or raise."""
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r).get("result", "")


class Handler(BaseHTTPRequestHandler):
    url = ""
    prometheus = ""

    def log_message(self, *args):  # no access log: the path is ours, the target isn't logged
        pass

    def do_GET(self):
        self.send_response(200 if self.path == "/healthz" else 404)
        self.end_headers()

    dockhand_token = ""

    def do_POST(self):
        if self.path.startswith("/dockhand/"):
            self.dockhand()
            return
        if self.path != "/alertmanager":
            self.send_response(404)
            self.end_headers()
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self.send_response(400)
            self.end_headers()
            return
        failed = 0
        for alert in body.get("alerts") or []:
            if not wanted(alert):
                continue
            fields = to_checkmk(alert, self.prometheus)
            try:
                result = send(self.url, fields)
                log(f"{fields['host']}: {fields['state']}: {result}")
            except (urllib.error.URLError, OSError, ValueError) as e:
                failed += 1
                reason = getattr(e, "code", None) or getattr(e, "reason", None) or type(e).__name__
                log(f"{fields['host']}: {fields['state']}: Zammad didn't take it ({reason})")
        self.send_response(502 if failed else 200)
        self.end_headers()


    def dockhand(self):
        token = self.path[len("/dockhand/"):].split("?")[0]
        if not self.dockhand_token or not hmac.compare_digest(token.encode(), self.dockhand_token.encode()):
            self.send_response(404)
            self.end_headers()
            return
        try:
            note = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self.send_response(400)
            self.end_headers()
            return
        fields = dockhand_to_checkmk(note if isinstance(note, dict) else {})
        if fields is None:
            log(f"dockhand: a notification with no title (keys {sorted(note)[:8] if isinstance(note, dict) else '?'}); skipped")
            self.send_response(200)
            self.end_headers()
            return
        try:
            result = send(self.url, fields)
            log(f"{fields['host']}: {fields['state']}: {result}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            reason = getattr(e, "code", None) or getattr(e, "reason", None) or type(e).__name__
            log(f"{fields['host']}: {fields['state']}: Zammad didn't take it ({reason})")
            self.send_response(502)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()


def main() -> None:
    url = os.environ.get("ZAMMAD_URL", "").strip()
    if not url.startswith("https://"):
        sys.exit("ZAMMAD_URL is missing or not https")
    Handler.url = url
    Handler.prometheus = os.environ.get("PROMETHEUS_URL", "").strip()
    if not Handler.prometheus:
        log("PROMETHEUS_URL unset: tickets won't list the members of a group alert")
    Handler.dockhand_token = os.environ.get("DOCKHAND_TOKEN", "").strip()
    if not Handler.dockhand_token:
        log("DOCKHAND_TOKEN unset: /dockhand answers 404")
    port = int(os.environ.get("PORT", "8080"))
    log(f"listening on {port}")
    ThreadingHTTPServer(("", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
