"""The tailnet's state for Prometheus, polled from Tailscale's API (QUEUE-tailscale.md).

Signs in with a read-only OAuth client (TS_CLIENT_ID, TS_CLIENT_SECRET: devices, routes,
policy file, auth keys and the configuration log, all read) and every REFRESH_SECONDS reads
the tailnet's devices, its auth keys and the last day of its configuration log. /metrics
serves what the last read found; it answers 503 until the first read is done.

Each API read is reported apart (tailscale_api_up{endpoint}): one that fails is 0, and its
metrics are left out rather than shown as zeros, so "couldn't read" never reads as "none".
The token is kept in memory only and never logged. Standard library only.
"""
from __future__ import annotations

import calendar
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API = os.environ.get("TS_API", "https://api.tailscale.com").rstrip("/")
TAILNET = os.environ.get("TS_TAILNET", "-")
REFRESH = int(os.environ.get("REFRESH_SECONDS", "120"))
# The configuration log read each time, for the policy's last change.
LOG_WINDOW = int(os.environ.get("LOG_WINDOW_SECONDS", "86400"))


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), msg, flush=True)


def ts(value: str | None) -> float | None:
    """An API timestamp (RFC 3339, 'Z') as Unix seconds, or None."""
    if not value or value.startswith("0001-"):
        return None
    try:
        return float(calendar.timegm(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return None


def esc(value) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def line(name: str, labels: dict, value) -> str:
    inner = ",".join(f'{k}="{esc(v)}"' for k, v in labels.items())
    return f"{name}{{{inner}}} {value}" if inner else f"{name} {value}"


def device_lines(devices: list[dict]) -> list[str]:
    """Each device's state; its name is the hostname, its tags as one comma-joined label."""
    out = [line("tailscale_devices", {}, len(devices))]
    for d in devices:
        name = d.get("hostname") or d.get("name", "?")
        tags = ",".join(sorted(d.get("tags") or []))
        dev = {"device": name}
        out.append(line("tailscale_device_info", {**dev, "tags": tags, "os": d.get("os", ""),
                                                  "client_version": (d.get("clientVersion") or "").split("-")[0],
                                                  "owner": "tagged" if tags else "user"}, 1))
        out.append(line("tailscale_device_online", {**dev, "tags": tags}, 1 if d.get("connectedToControl") else 0))
        seen = ts(d.get("lastSeen"))
        if seen is not None:
            out.append(line("tailscale_device_last_seen_timestamp_seconds", dev, int(seen)))
        expires = ts(d.get("expires"))
        if not d.get("keyExpiryDisabled") and expires is not None:
            out.append(line("tailscale_device_key_expiry_timestamp_seconds", {**dev, "tags": tags}, int(expires)))
        out.append(line("tailscale_device_authorized", dev, 1 if d.get("authorized") else 0))
        out.append(line("tailscale_device_update_available", dev, 1 if d.get("updateAvailable") else 0))
        enabled = set(d.get("enabledRoutes") or [])
        for route in d.get("advertisedRoutes") or []:
            out.append(line("tailscale_device_route_enabled", {**dev, "route": route}, 1 if route in enabled else 0))
        conn = d.get("clientConnectivity") or {}
        if "mappingVariesByDestIP" in conn:
            out.append(line("tailscale_device_hard_nat", dev, 1 if conn["mappingVariesByDestIP"] else 0))
        for region, lat in (conn.get("latency") or {}).items():
            if isinstance(lat, dict) and lat.get("preferred") and "latencyMs" in lat:
                out.append(line("tailscale_device_derp_latency_seconds", {**dev, "region": region},
                                round(float(lat["latencyMs"]) / 1000, 4)))
    return out


def key_lines(keys: list[dict]) -> list[str]:
    """Each auth key's expiry (an OAuth client is a key too, and doesn't expire)."""
    out = []
    for k in keys:
        expires = ts(k.get("expires"))
        if expires is not None:
            out.append(line("tailscale_auth_key_expiry_timestamp_seconds",
                            {"key_id": k.get("id", "?"), "description": k.get("description", "")}, int(expires)))
    return out


def policy_lines(events: list[dict]) -> list[str]:
    """The policy file's newest change in the log window, with who made it: the actor's
    type (a person, or an OAuth client such as OpenTofu's) and the client's or person's id."""
    changes = [e for e in events if "POLICY" in str((e.get("target") or {}).get("type", "")).upper()
               or "ACL" in str((e.get("target") or {}).get("type", "")).upper()]
    out = [line("tailscale_policy_changes", {}, len(changes))]
    newest = max(changes, key=lambda e: ts(e.get("eventTime")) or 0, default=None)
    if newest and ts(newest.get("eventTime")):
        actor = newest.get("actor") or {}
        out.append(line("tailscale_policy_last_change_timestamp_seconds",
                        {"actor_type": actor.get("type", ""), "actor": actor.get("id") or actor.get("loginName", "")},
                        int(ts(newest["eventTime"]))))
    return out


class Client:
    def __init__(self, client_id: str, secret: str):
        self.id, self.secret, self.token, self.until = client_id, secret, "", 0.0

    def bearer(self) -> str:
        if time.time() > self.until - 300:
            body = urllib.parse.urlencode({"client_id": self.id, "client_secret": self.secret,
                                           "grant_type": "client_credentials"}).encode()
            with urllib.request.urlopen(urllib.request.Request(API + "/api/v2/oauth/token", data=body), timeout=30) as r:
                answer = json.load(r)
            self.token, self.until = answer["access_token"], time.time() + int(answer.get("expires_in", 3600))
        return self.token

    def get(self, path: str):
        req = urllib.request.Request(f"{API}/api/v2/tailnet/{TAILNET}/{path}",
                                     headers={"Authorization": "Bearer " + self.bearer(), "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)


def read(client: Client) -> str:
    out, ups = [], {}
    end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - LOG_WINDOW))
    reads = {
        "devices": (lambda: client.get("devices?fields=all")["devices"], device_lines),
        "keys": (lambda: [client.get("keys/" + urllib.parse.quote(k["id"]))
                          for k in client.get("keys").get("keys", [])], key_lines),
        "configuration_log": (lambda: client.get(f"logging/configuration?start={start}&end={end}").get("logs", []),
                              policy_lines),
    }
    for endpoint, (fetch, render) in reads.items():
        try:
            out += render(fetch())
            ups[endpoint] = 1
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as e:
            reason = getattr(e, "code", None) or getattr(e, "reason", None) or type(e).__name__
            log(f"{endpoint}: couldn't read ({reason})")
            ups[endpoint] = 0
    out += [line("tailscale_api_up", {"endpoint": k}, v) for k, v in ups.items()]
    out.append(line("tailscale_exporter_last_read_timestamp_seconds", {}, int(time.time())))
    return "\n".join(out) + "\n"


class Handler(BaseHTTPRequestHandler):
    page = ""

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            return
        if self.path != "/metrics" or not Handler.page:
            self.send_response(404 if self.path != "/metrics" else 503)
            self.end_headers()
            return
        body = Handler.page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def loop(client: Client) -> None:
    while True:
        Handler.page = read(client)
        time.sleep(REFRESH)


def main() -> None:
    cid, secret = os.environ.get("TS_CLIENT_ID", "").strip(), os.environ.get("TS_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        sys.exit("TS_CLIENT_ID and TS_CLIENT_SECRET must be set (the tailscale-exporter Secret)")
    threading.Thread(target=loop, args=(Client(cid, secret),), daemon=True).start()
    port = int(os.environ.get("PORT", "8080"))
    log(f"listening on {port}, reading every {REFRESH}s")
    ThreadingHTTPServer(("", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
