#!/usr/bin/env python3
"""Kea's HA state, for Prometheus and Homepage.

On each request it asks Kea's API for status-get, with the credentials file the
API checks (one "user:password" line), and answers with the HA part only:

  /metrics  Prometheus text: kea_ha_api_up, kea_ha_state (1 for each server's
            current state, as this server sees it), kea_ha_partner_age_seconds,
            kea_ha_partner_in_touch, kea_ha_clock_skew_seconds and kea_ha_normal
            (1 when both servers are in the mode's own state and in touch)
  /status   JSON: the mode, each server's role and state, the partner's age and
            whether all of it is normal; 503 when Kea gives no usable answer

The credentials never leave this process, nor does anything else status-get
says. Environment: KEA_API_URL, KEA_API_CREDENTIALS, LISTEN_PORT (9548).
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_URL = os.environ.get("KEA_API_URL", "http://kea-dhcp4:8000/")
CREDENTIALS_FILE = os.environ.get("KEA_API_CREDENTIALS", "/run/secrets/kea-api-credentials")
PORT = int(os.environ.get("LISTEN_PORT", "9548"))
TIMEOUT_SECONDS = 5


class Unanswered(Exception):
    """Kea's API gave no usable answer. The message names the failure, never the
    credentials or the request."""


def status_get(url: str, credentials_file: str) -> dict:
    """status-get's arguments from Kea's API."""
    try:
        with open(credentials_file, encoding="utf-8") as f:
            user_password = f.read().strip()
    except OSError as error:
        raise Unanswered(f"credentials file unreadable ({type(error).__name__})") from None
    auth = base64.b64encode(user_password.encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        url, data=json.dumps({"command": "status-get"}).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            answer = json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        raise Unanswered(f"HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as error:
        raise Unanswered(f"no answer ({type(error).__name__})") from None
    # Kea's HTTP socket wraps its answer in a list, as its control agent did.
    first = answer[0] if isinstance(answer, list) and answer else answer
    if not isinstance(first, dict):
        raise Unanswered("an answer that isn't a JSON object")
    if first.get("result") != 0:
        raise Unanswered(f"result {first.get('result')}")
    return first.get("arguments") or {}


def ha_status(arguments: dict) -> dict:
    """The HA relationship in status-get's arguments: the mode, each server's role and
    state, the partner's age, and whether all of it is normal."""
    relationships = arguments.get("high-availability") or []
    if not relationships:
        raise Unanswered("no high-availability in status-get")
    relationship = relationships[0]
    mode = relationship.get("ha-mode", "")
    servers = relationship.get("ha-servers") or {}
    local, remote = servers.get("local") or {}, servers.get("remote") or {}
    status = {
        "mode": mode,
        "local": {"role": local.get("role", ""), "state": local.get("state", "")},
        "remote": {"role": remote.get("role", ""), "state": remote.get("last-state", ""),
                   "age": remote.get("age"), "in_touch": bool(remote.get("in-touch")),
                   "clock_skew": remote.get("clock-skew")},
    }
    # Hot-standby and load-balancing name their normal state after the mode; any other
    # (partner-down, waiting, syncing, terminated...) is a failover or a recovery.
    status["normal"] = (status["local"]["state"] == mode and status["remote"]["state"] == mode
                        and status["remote"]["in_touch"]
                        and not remote.get("communication-interrupted", False))
    return status


def _label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def metrics(status: dict | None) -> str:
    """Prometheus text for an HA status, or for none when Kea didn't answer."""
    lines = ["# HELP kea_ha_api_up Whether Kea's API answered status-get with its HA state.",
             "# TYPE kea_ha_api_up gauge",
             f"kea_ha_api_up {0 if status is None else 1}"]
    if status is not None:
        lines += ["# HELP kea_ha_state Each server's HA state as this server sees it: 1 for the state it is in.",
                  "# TYPE kea_ha_state gauge"]
        for server in ("local", "remote"):
            s = status[server]
            lines.append(f'kea_ha_state{{server="{server}",role="{_label(s["role"])}",'
                         f'state="{_label(s["state"])}",mode="{_label(status["mode"])}"}} 1')
        remote = status["remote"]
        if remote["age"] is not None:
            lines += ["# HELP kea_ha_partner_age_seconds Seconds since this server last heard from its partner.",
                      "# TYPE kea_ha_partner_age_seconds gauge",
                      f"kea_ha_partner_age_seconds {remote['age']}"]
        lines += ["# HELP kea_ha_partner_in_touch Whether this server is in touch with its partner.",
                  "# TYPE kea_ha_partner_in_touch gauge",
                  f"kea_ha_partner_in_touch {int(remote['in_touch'])}"]
        if remote["clock_skew"] is not None:
            lines += ["# HELP kea_ha_clock_skew_seconds The partner's clock skew from this server's.",
                      "# TYPE kea_ha_clock_skew_seconds gauge",
                      f"kea_ha_clock_skew_seconds {remote['clock_skew']}"]
        lines += ["# HELP kea_ha_normal 1 when both servers are in the mode's own state and in touch.",
                  "# TYPE kea_ha_normal gauge",
                  f"kea_ha_normal {int(status['normal'])}"]
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 -- http.server's name
        if self.path not in ("/metrics", "/status"):
            self._send(404, "text/plain", "not found\n")
            return
        try:
            status, error = ha_status(status_get(API_URL, CREDENTIALS_FILE)), None
        except Unanswered as unanswered:
            status, error = None, str(unanswered)
        if self.path == "/metrics":
            self._send(200, "text/plain; version=0.0.4", metrics(status))
        elif status is None:
            self._send(503, "application/json", json.dumps({"error": error}))
        else:
            self._send(200, "application/json", json.dumps(status))

    def _send(self, code: int, content_type: str, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 -- http.server's name
        pass  # a request a minute from Prometheus and Homepage: nothing worth a log line


if __name__ == "__main__":
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
