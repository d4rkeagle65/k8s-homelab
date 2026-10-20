#!/usr/bin/env python3
"""Write this Proxmox host's guests' backups as metrics for node-exporter.

For each guest on this host: whether an enabled backup job covers it, its newest
archive's time and size on that job's storage, and the result of its newest
vzdump run here, from the task logs ("Finished Backup of VM N", or "Backup of VM
N failed", or started and never finished). Read through pvesh, so it runs as
root on the host; standard library only.

Every read must answer: when one fails, the file says
proxmox_backup_collector_success 0 and carries no guest series, rather than a
partial answer that would read as "no backup". The file is replaced whole, so
node-exporter never reads half of one.

    proxmox-backup-metrics.py [OUTPUT]
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time

OUTPUT = "/var/lib/prometheus/node-exporter/proxmox_backup.prom"
# Newest vzdump tasks read for each guest's last result; nightly runs, so a
# guest missing from all of them has had no run here in about a month.
TASKS_READ = 30

FINISHED = re.compile(r"Finished Backup of VM (\d+)")
FAILED = re.compile(r"Backup of VM (\d+) failed")
STARTED = re.compile(r"Starting Backup of VM (\d+)")

HELP = {
    "proxmox_backup_collector_success":
        "1 when every read this run answered; 0 when one failed, and then no guest series",
    "proxmox_backup_collector_last_run_timestamp_seconds": "When this collector last ran",
    "proxmox_backup_guest_covered": "1 when an enabled backup job covers the guest",
    "proxmox_backup_archive_timestamp_seconds": "The guest's newest backup archive's creation time",
    "proxmox_backup_archive_size_bytes": "The guest's newest backup archive's size",
    "proxmox_backup_last_result": "The guest's newest vzdump run on this host: 1 finished, 0 failed",
    "proxmox_backup_last_run_timestamp_seconds": "When the vzdump task holding that run started",
}


class ReadFailed(Exception):
    pass


def pvesh(path: str, *args: str):
    cmd = ["pvesh", "get", path, "--output-format", "json", *args]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ReadFailed(f"{path}: {e}") from None
    if out.returncode != 0:
        raise ReadFailed(f"{path}: exit {out.returncode}: {out.stderr.strip()[:200]}")
    try:
        return json.loads(out.stdout)
    except ValueError:
        raise ReadFailed(f"{path}: answered something other than JSON") from None


def id_list(value) -> set[int]:
    return {int(v) for v in re.split(r"[,\s]+", str(value or "")) if v.isdigit()}


def covering_storages(guest: dict, jobs: list[dict], node: str) -> set[str]:
    """The storages of every enabled job that backs this guest up."""
    found = set()
    for job in jobs:
        if str(job.get("enabled", 1)) == "0":
            continue
        if job.get("node") and job["node"] != node:
            continue
        vmid = guest["vmid"]
        if job.get("all") in (1, "1", True):
            covered = vmid not in id_list(job.get("exclude"))
        elif job.get("pool"):
            covered = guest.get("pool") == job["pool"]
        else:
            covered = vmid in id_list(job.get("vmid"))
        if covered:
            found.add(job.get("storage") or "local")
    return found


def results_from_log(lines: list[str]) -> dict[int, int]:
    """Each guest's result in one finished vzdump task: 1 finished, 0 failed."""
    result: dict[int, int] = {}
    for line in lines:
        for pattern, value in ((STARTED, 0), (FINISHED, 1), (FAILED, 0)):
            m = pattern.search(line)
            if m:
                result[int(m.group(1))] = value
    return result


def collect(node: str) -> list[tuple[str, dict, float]]:
    guests = [g for g in pvesh("/cluster/resources", "--type", "vm")
              if g.get("node") == node and "vmid" in g]
    jobs = pvesh("/cluster/backup")
    storages = {g["vmid"]: covering_storages(g, jobs, node) for g in guests}

    newest: dict[int, dict] = {}
    for storage in sorted(set().union(*storages.values()) if storages else set()):
        for item in pvesh(f"/nodes/{node}/storage/{storage}/content", "--content", "backup"):
            vmid = int(item.get("vmid") or 0)
            if storage in storages.get(vmid, ()) and item.get("ctime", 0) > newest.get(vmid, {}).get("ctime", 0):
                newest[vmid] = item

    last: dict[int, tuple[int, float]] = {}
    wanted = {g["vmid"] for g in guests}
    for task in pvesh(f"/nodes/{node}/tasks", "--typefilter", "vzdump", "--limit", str(TASKS_READ)):
        if wanted <= set(last):
            break
        if not task.get("endtime"):
            continue
        log = pvesh(f"/nodes/{node}/tasks/{task['upid']}/log", "--limit", "1000000")
        for vmid, value in results_from_log([entry.get("t", "") for entry in log]).items():
            last.setdefault(vmid, (value, float(task.get("starttime", 0))))

    series = []
    for g in sorted(guests, key=lambda g: g["vmid"]):
        labels = {"vmid": str(g["vmid"]), "guest": g.get("name", ""), "type": g.get("type", "")}
        vmid = g["vmid"]
        series.append(("proxmox_backup_guest_covered", labels, 1 if storages[vmid] else 0))
        if vmid in newest:
            archive = {**labels, "storage": newest[vmid]["volid"].split(":")[0]}
            series.append(("proxmox_backup_archive_timestamp_seconds", archive, newest[vmid]["ctime"]))
            series.append(("proxmox_backup_archive_size_bytes", archive, newest[vmid].get("size", 0)))
        if vmid in last:
            series.append(("proxmox_backup_last_result", labels, last[vmid][0]))
            series.append(("proxmox_backup_last_run_timestamp_seconds", labels, last[vmid][1]))
    return series


def label_text(labels: dict) -> str:
    if not labels:
        return ""
    esc = {k: str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
           for k, v in labels.items()}
    return "{" + ",".join(f'{k}="{v}"' for k, v in esc.items()) + "}"


def render(series: list[tuple[str, dict, float]]) -> str:
    """The text format, each metric's samples together under its HELP and TYPE."""
    families: dict[str, list[str]] = {}
    for name, labels, value in series:
        families.setdefault(name, []).append(f"{name}{label_text(labels)} {value}")
    out = []
    for name, samples in families.items():
        out += [f"# HELP {name} {HELP[name]}", f"# TYPE {name} gauge", *samples]
    return "\n".join(out) + "\n"


def write_atomically(path: str, text: str) -> None:
    folder = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".proxmox_backup.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def main() -> int:
    output = sys.argv[1] if len(sys.argv) > 1 else OUTPUT
    node = socket.gethostname().split(".")[0]
    now = time.time()
    try:
        series = collect(node)
        ok = 1
    except ReadFailed as e:
        print(f"proxmox-backup-metrics: a read failed, so no guest series: {e}", file=sys.stderr)
        series, ok = [], 0
    head = [("proxmox_backup_collector_success", {}, ok),
            ("proxmox_backup_collector_last_run_timestamp_seconds", {}, now)]
    write_atomically(output, render(head + series))
    print(f"proxmox-backup-metrics: {node}: wrote {len(series)} guest series, success={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
