#!/usr/bin/env python3
"""Kea's HA state and lease counts, for Prometheus and Homepage.

On each request it asks Kea's API for status-get and statistic-get-all, with the
credentials file the API checks (one "user:password" line), and answers with the
HA state and two lease totals only:

  /metrics  Prometheus text: kea_ha_api_up, kea_ha_state (1 for each server's
            current state, as this server sees it), kea_ha_partner_age_seconds,
            kea_ha_partner_in_touch, kea_ha_clock_skew_seconds, kea_ha_normal
            (1 when both servers are in the mode's own state and in touch);
            kea_stats_api_up, kea_dhcp4_assigned_addresses and
            kea_dhcp4_total_addresses (every subnet's, summed: Kea's own counts,
            whatever Stork's agent exports)
  /status   JSON: the mode, each server's role and state, the partner's age,
            whether all of it is normal, and the lease totals; 503 when Kea gives
            no usable HA answer

The credentials never leave this process, nor does anything else Kea says.
Environment: KEA_API_URL, KEA_API_CREDENTIALS, LISTEN_PORT (9548).
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_URL = os.environ.get("KEA_API_URL", "http://kea-dhcp4:8000/")
CREDENTIALS_FILE = os.environ.get("KEA_API_CREDENTIALS", "/run/secrets/kea-api-credentials")
PORT = int(os.environ.get("LISTEN_PORT", "9548"))
TIMEOUT_SECONDS = 5
SUBNET_STATISTIC = re.compile(r"^subnet\[\d+\]\.(assigned|total)-addresses$")


class Unanswered(Exception):
    """Kea's API gave no usable answer. The message names the failure, never the
    credentials or the request."""


def command(url: str, credentials_file: str, name: str) -> dict:
    """One API command's arguments from Kea."""
    try:
        with open(credentials_file, encoding="utf-8") as f:
            user_password = f.read().strip()
    except OSError as error:
        raise Unanswered(f"credentials file unreadable ({type(error).__name__})") from None
    auth = base64.b64encode(user_password.encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        url, data=json.dumps({"command": name}).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            answer = json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        raise Unanswered(f"{name}: HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as error:
        raise Unanswered(f"{name}: no answer ({type(error).__name__})") from None
    # Kea's HTTP socket wraps its answer in a list, as its control agent did.
    first = answer[0] if isinstance(answer, list) and answer else answer
    if not isinstance(first, dict):
        raise Unanswered(f"{name}: an answer that isn't a JSON object")
    if first.get("result") != 0:
        raise Unanswered(f"{name}: result {first.get('result')}")
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


def lease_counts(arguments: dict) -> dict:
    """Assigned and total addresses, every subnet's summed, from statistic-get-all's
    arguments: each statistic is a list of [value, timestamp] samples, newest first."""
    counts = {"assigned": 0, "total": 0}
    seen = False
    for name, samples in arguments.items():
        match = SUBNET_STATISTIC.match(name)
        if match and samples:
            counts[match.group(1)] += int(samples[0][0])
            seen = True
    if not seen:
        raise Unanswered("no subnet statistics in statistic-get-all")
    return counts


def _label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def metrics(status: dict | None, leases: dict | None = None) -> str:
    """Prometheus text for an HA status and lease totals, either None when Kea didn't
    answer for it."""
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
    lines += ["# HELP kea_stats_api_up Whether Kea's API answered statistic-get-all with subnet statistics.",
              "# TYPE kea_stats_api_up gauge",
              f"kea_stats_api_up {0 if leases is None else 1}"]
    if leases is not None:
        lines += ["# HELP kea_dhcp4_assigned_addresses Addresses Kea has leased, every subnet's summed.",
                  "# TYPE kea_dhcp4_assigned_addresses gauge",
                  f"kea_dhcp4_assigned_addresses {leases['assigned']}",
                  "# HELP kea_dhcp4_total_addresses Addresses in Kea's pools, every subnet's summed.",
                  "# TYPE kea_dhcp4_total_addresses gauge",
                  f"kea_dhcp4_total_addresses {leases['total']}"]
    return "\n".join(lines) + "\n"


def collect() -> tuple[dict | None, dict | None, str | None]:
    """The HA status and the lease totals, each None when Kea didn't answer for it,
    and the HA failure's reason."""
    try:
        status, error = ha_status(command(API_URL, CREDENTIALS_FILE, "status-get")), None
    except Unanswered as unanswered:
        status, error = None, str(unanswered)
    try:
        leases = lease_counts(command(API_URL, CREDENTIALS_FILE, "statistic-get-all"))
    except Unanswered:
        leases = None
    return status, leases, error


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 -- http.server's name
        if self.path not in ("/metrics", "/status"):
            self._send(404, "text/plain", "not found\n")
            return
        status, leases, error = collect()
        if self.path == "/metrics":
            self._send(200, "text/plain; version=0.0.4", metrics(status, leases))
        elif status is None:
            self._send(503, "application/json", json.dumps({"error": error}))
        else:
            self._send(200, "application/json", json.dumps({**status, "leases": leases}))

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
