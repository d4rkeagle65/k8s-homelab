"""Alertmanager's webhook into Zammad's Checkmk endpoint: an alert opens a ticket,
a repeat adds a note to it, and its recovery closes it (QUEUE-zammad.md).

Zammad keys an open ticket by `host` and `service`, and titles it "<host> is
<state>". So `service` is the alert's name, and `host` the alert's name with
what it's about (its instance, pod, node, job or namespace), which keeps one
ticket per alert and a readable title. `warning` and `critical` alerts only;
Alertmanager's route sends nothing else, and this checks again.

A failed call to Zammad answers Alertmanager with 502, so it retries. The
Zammad URL carries the integration's token: it comes from the environment
(ZAMMAD_URL) and is never logged. Standard library only.
"""
from __future__ import annotations

import json
import os
import sys
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


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), msg, flush=True)


def wanted(alert: dict) -> bool:
    labels = alert.get("labels") or {}
    return labels.get("severity") in SEVERITIES and labels.get("alertname") not in NEVER


def to_checkmk(alert: dict) -> dict:
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

    def log_message(self, *args):  # no access log: the path is ours, the target isn't logged
        pass

    def do_GET(self):
        self.send_response(200 if self.path == "/healthz" else 404)
        self.end_headers()

    def do_POST(self):
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
            fields = to_checkmk(alert)
            try:
                result = send(self.url, fields)
                log(f"{fields['host']}: {fields['state']}: {result}")
            except (urllib.error.URLError, OSError, ValueError) as e:
                failed += 1
                reason = getattr(e, "code", None) or getattr(e, "reason", None) or type(e).__name__
                log(f"{fields['host']}: {fields['state']}: Zammad didn't take it ({reason})")
        self.send_response(502 if failed else 200)
        self.end_headers()


def main() -> None:
    url = os.environ.get("ZAMMAD_URL", "").strip()
    if not url.startswith("https://"):
        sys.exit("ZAMMAD_URL is missing or not https")
    Handler.url = url
    port = int(os.environ.get("PORT", "8080"))
    log(f"listening on {port}")
    ThreadingHTTPServer(("", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
