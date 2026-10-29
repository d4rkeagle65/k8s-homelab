"""The apps' own counts for the Wall dashboard (QUEUE-kiosk.md), polled from their APIs.

Every REFRESH_SECONDS it reads, inside the cluster:
  Tdarr      its statistics (the transcode and health check queues and results) and its
             nodes' busy workers (get-nodes: Tdarr keeps workers in memory, not in its
             node records): no key, Tdarr has no authentication
  Semaphore  the project's last tasks, counted by status (a Guest's API token), and a
             running count of the tasks that finished, by status, for a count over any
             time range (increase()). It remembers which task IDs it counted; its first
             read after a start only records them, so a restart never counts old tasks as
             new, and tasks that finish while it's down aren't counted.
  Emby       the sessions playing something, and those Emby transcodes
  Seerr      its requests, counted by state
  Immich     its photo and video counts and the storage they use (an admin's API key with
             only server.statistics)

Each app is reported apart (wall_source_up{source}): one that can't be read is 0 and its
metrics are left out rather than shown as zeros, so "couldn't read" never reads as "none".
An app whose key isn't set is reported down with that reason in the log. Keys are kept in
memory only and never logged. /metrics serves the last read, 503 until the first is done.
Standard library only.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REFRESH = int(os.environ.get("REFRESH_SECONDS", "60"))
TIMEOUT = 15


def env(name: str) -> str:
    return os.environ.get(name, "").strip()


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), msg, flush=True)


def esc(value) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def line(name: str, labels: dict, value) -> str:
    inner = ",".join(f'{k}="{esc(v)}"' for k, v in labels.items())
    return f"{name}{{{inner}}} {value}" if inner else f"{name} {value}"


def call(url: str, headers: dict | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/json", **(headers or {}),
                                                           **({"Content-Type": "application/json"} if data else {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def as_list(payload) -> list:
    return payload if isinstance(payload, list) else list(payload.values()) if isinstance(payload, dict) else []


def tdarr() -> list[str]:
    url = env("TDARR_URL")
    stats = as_list(call(f"{url}/api/v2/cruddb", body={"data": {"collection": "StatisticsJSONDB", "mode": "getAll"}}))
    if not stats:
        raise ValueError("no statistics document")
    s = stats[0]
    # Tdarr's tables: 1 transcode queue, 2 transcode success or not required, 3 transcode
    # error or cancelled, 4 health check queue, 5 health check success, 6 health check error.
    tables = {"1": ("transcode", "queued"), "2": ("transcode", "done"), "3": ("transcode", "error"),
              "4": ("health_check", "queued"), "5": ("health_check", "done"), "6": ("health_check", "error")}
    out = [line("wall_tdarr_files", {"stage": st, "state": state}, int(s.get(f"table{n}Count") or 0))
           for n, (st, state) in tables.items()]
    out.append(line("wall_tdarr_library_files", {}, int(s.get("totalFileCount") or 0)))
    nodes = call(f"{url}/api/v2/get-nodes")
    busy: dict[str, int] = {}
    for n in as_list(nodes):
        for w in (n.get("workers") or {}).values() if isinstance(n, dict) else []:
            if isinstance(w, dict) and not w.get("idle"):
                kind = "health_check" if "healthcheck" in str(w.get("workerType", "")) else "transcode"
                busy[kind] = busy.get(kind, 0) + 1
    for kind in ("transcode", "health_check"):
        out.append(line("wall_tdarr_workers_busy", {"type": kind}, busy.get(kind, 0)))
    out.append(line("wall_tdarr_workers_running", {}, sum(busy.values())))
    return out


FINISHED = ("success", "error", "stopped")
seen_tasks: set | None = None          # task IDs already counted; None until the first read
finished_total: dict[str, int] = {s: 0 for s in FINISHED}


def semaphore() -> list[str]:
    global seen_tasks
    url, token, project = env("SEMAPHORE_URL"), env("SEMAPHORE_TOKEN"), env("SEMAPHORE_PROJECT") or "1"
    if not token:
        raise ValueError("SEMAPHORE_TOKEN not set")
    tasks = call(f"{url}/api/project/{project}/tasks/last", {"Authorization": f"Bearer {token}"})
    counts: dict[str, int] = {}
    for t in tasks if isinstance(tasks, list) else []:
        counts[t.get("status") or "unknown"] = counts.get(t.get("status") or "unknown", 0) + 1
    done = {t["id"]: t.get("status") for t in tasks if isinstance(t, dict) and t.get("status") in FINISHED and "id" in t}
    if seen_tasks is not None:
        for tid, st in done.items():
            if tid not in seen_tasks:
                finished_total[st] += 1
    seen_tasks = set(done)  # the IDs the API still lists; an older one never comes back
    out = [line("wall_semaphore_tasks", {"status": st}, n) for st, n in sorted(counts.items())]
    out += [line("wall_semaphore_tasks_finished_total", {"status": st}, n) for st, n in finished_total.items()]
    active = sum(n for st, n in counts.items() if st in ("running", "starting", "waiting"))
    out.append(line("wall_semaphore_tasks_active", {}, active))
    return out


def emby() -> list[str]:
    url, key = env("EMBY_URL"), env("EMBY_API_KEY")
    if not key:
        raise ValueError("EMBY_API_KEY not set")
    sessions = call(f"{url}/emby/Sessions", {"X-Emby-Token": key})
    playing = [s for s in sessions if s.get("NowPlayingItem")]
    transcoding = [s for s in playing if s.get("TranscodingInfo")]
    return [line("wall_emby_streams", {}, len(playing)), line("wall_emby_transcodes", {}, len(transcoding))]


def seerr() -> list[str]:
    url, key = env("SEERR_URL"), env("SEERR_API_KEY")
    if not key:
        raise ValueError("SEERR_API_KEY not set")
    counts = call(f"{url}/api/v1/request/count", {"X-Api-Key": key})
    return [line("wall_seerr_requests", {"state": k}, int(v)) for k, v in sorted(counts.items())
            if isinstance(v, (int, float)) and not isinstance(v, bool)]


def immich() -> list[str]:
    url, key = env("IMMICH_URL"), env("IMMICH_API_KEY")
    if not key:
        raise ValueError("IMMICH_API_KEY not set")
    s = call(f"{url}/api/server/statistics", {"x-api-key": key})
    out = [line("wall_immich_assets", {"type": "photo"}, int(s.get("photos") or 0)),
           line("wall_immich_assets", {"type": "video"}, int(s.get("videos") or 0)),
           line("wall_immich_usage_bytes", {"type": "all"}, int(s.get("usage") or 0))]
    for k, t in (("usagePhotos", "photo"), ("usageVideos", "video")):
        if k in s:
            out.append(line("wall_immich_usage_bytes", {"type": t}, int(s[k] or 0)))
    return out


SOURCES = {"tdarr": tdarr, "semaphore": semaphore, "emby": emby, "seerr": seerr, "immich": immich}


def read() -> str:
    out = ["# The apps' own counts for the Wall dashboard (wall-exporter.py)."]
    for name, fn in SOURCES.items():
        try:
            lines = fn()
            out.append(line("wall_source_up", {"source": name}, 1))
            out += lines
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            reason = getattr(e, "code", None) or getattr(e, "reason", None) or e
            log(f"{name}: couldn't read ({type(e).__name__}: {reason})")
            out.append(line("wall_source_up", {"source": name}, 0))
    out.append(line("wall_exporter_last_read_timestamp_seconds", {}, int(time.time())))
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


def loop() -> None:
    while True:
        Handler.page = read()
        time.sleep(REFRESH)


def main() -> None:
    threading.Thread(target=loop, daemon=True).start()
    port = int(os.environ.get("PORT", "8080"))
    log(f"listening on {port}, reading every {REFRESH}s")
    ThreadingHTTPServer(("", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
