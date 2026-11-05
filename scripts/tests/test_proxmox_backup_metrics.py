"""ansible/files/proxmox-backup-metrics.py: each guest's backup coverage, newest archive and
last vzdump result, written whole for node-exporter, and no guest series at all when a read
fails. Against a stand-in for pvesh."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "ansible/files/proxmox-backup-metrics.py"


def load():
    spec = importlib.util.spec_from_file_location("proxmox_backup_metrics_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load()

GUESTS = [{"vmid": 101, "node": "pve1", "name": "web", "type": "qemu"},
          {"vmid": 102, "node": "pve1", "name": "db", "type": "lxc", "pool": "data"},
          {"vmid": 103, "node": "pve1", "name": "scratch", "type": "qemu"},
          {"vmid": 201, "node": "pve2", "name": "elsewhere", "type": "qemu"}]
JOBS = [{"enabled": 1, "all": 1, "exclude": "103", "storage": "nas"},
        {"enabled": 0, "vmid": "103", "storage": "nas"},
        {"enabled": 1, "pool": "data", "storage": "local"},
        {"enabled": 1, "vmid": "103", "node": "pve2", "storage": "nas"}]
CONTENT = {"nas": [{"vmid": 101, "volid": "nas:backup/vzdump-qemu-101-old.vma.zst", "ctime": 1000, "size": 5},
                   {"vmid": 101, "volid": "nas:backup/vzdump-qemu-101-new.vma.zst", "ctime": 2000, "size": 7},
                   {"vmid": 103, "volid": "nas:backup/vzdump-qemu-103.vma.zst", "ctime": 3000, "size": 9}],
           "local": [{"vmid": 102, "volid": "local:backup/vzdump-lxc-102.tar.zst", "ctime": 1500, "size": 3}]}
TASKS = [{"upid": "T3", "starttime": 300, "endtime": 0},       # still running: skipped
         {"upid": "T2", "starttime": 200, "endtime": 250},
         {"upid": "T1", "starttime": 100, "endtime": 150}]
LOGS = {"T2": ["INFO: Starting Backup of VM 101", "ERROR: Backup of VM 101 failed - no space",
               "INFO: Starting Backup of VM 102"],
        "T1": ["INFO: Starting Backup of VM 101", "INFO: Finished Backup of VM 101",
               "INFO: Starting Backup of VM 103", "INFO: Finished Backup of VM 103"]}


@pytest.fixture
def host(monkeypatch):
    read, fail = [], set()

    def pvesh(path, *args):
        read.append(path)
        if path in fail:
            raise m.ReadFailed(f"{path}: exit 255: refused")
        if path == "/cluster/resources":
            return GUESTS
        if path == "/cluster/backup":
            return JOBS
        if path.endswith("/content"):
            return CONTENT[path.split("/")[4]]
        if path == "/nodes/pve1/tasks":
            return TASKS
        return [{"t": line} for line in LOGS[path.split("/")[4]]]

    monkeypatch.setattr(m, "pvesh", pvesh)
    monkeypatch.setattr(m.socket, "gethostname", lambda: "pve1.home.example")
    return read, fail


def _series(collected):
    return {(n, tuple(sorted(lab.items()))): v for n, lab, v in collected}


def test_coverage_follows_each_job_s_rule():
    g = {"vmid": 103, "pool": None}
    assert m.covering_storages({"vmid": 101}, JOBS, "pve1") == {"nas"}
    assert m.covering_storages({"vmid": 102, "pool": "data"}, JOBS, "pve1") == {"nas", "local"}
    assert m.covering_storages(g, JOBS, "pve1") == set(), "excluded, a disabled job, another node's job"
    assert m.covering_storages(g, JOBS, "pve2") == {"nas"}
    assert m.covering_storages({"vmid": 5}, [{"vmid": "4,5 6"}], "x") == {"local"}, "no storage named: local"


def test_a_task_log_gives_each_guest_s_last_word():
    assert m.results_from_log(LOGS["T2"]) == {101: 0, 102: 0}, "a start with no finish is a failure"
    assert m.results_from_log(LOGS["T1"]) == {101: 1, 103: 1}


def test_collect_reads_this_host_s_guests_newest_archives_and_newest_results(host):
    got = _series(m.collect("pve1"))
    web = (("guest", "web"), ("type", "qemu"), ("vmid", "101"))
    db = (("guest", "db"), ("type", "lxc"), ("vmid", "102"))
    scratch = (("guest", "scratch"), ("type", "qemu"), ("vmid", "103"))
    assert got[("proxmox_backup_guest_covered", web)] == 1 and got[("proxmox_backup_guest_covered", scratch)] == 0
    web_archive = tuple(sorted(dict(web, storage="nas").items()))
    assert got[("proxmox_backup_archive_timestamp_seconds", web_archive)] == 2000, "the newest"
    assert got[("proxmox_backup_archive_size_bytes", web_archive)] == 7
    assert not any(n.startswith("proxmox_backup_archive") and dict(lab)["vmid"] == "103" for n, lab in got), \
        "an archive on no storage covering it isn't its backup"
    assert got[("proxmox_backup_last_result", web)] == 0 and got[("proxmox_backup_last_run_timestamp_seconds", web)] == 200.0
    assert got[("proxmox_backup_last_result", db)] == 0
    assert got[("proxmox_backup_last_result", scratch)] == 1 and got[("proxmox_backup_last_run_timestamp_seconds", scratch)] == 100.0
    assert not any(dict(lab).get("vmid") == "201" for _, lab in got), "another host's guest"


def test_collect_stops_reading_logs_once_every_guest_has_a_result(host, monkeypatch):
    read, _ = host
    monkeypatch.setitem(LOGS, "T2", LOGS["T2"] + ["INFO: Starting Backup of VM 103", "INFO: Finished Backup of VM 103"])
    m.collect("pve1")
    assert "/nodes/pve1/tasks/T1/log" not in read


def test_main_writes_the_file_whole_with_success(host, tmp_path, monkeypatch, capsys):
    out = tmp_path / "proxmox_backup.prom"
    monkeypatch.setattr(sys, "argv", ["proxmox-backup-metrics.py", str(out)])
    assert m.main() == 0
    text = out.read_text(encoding="utf-8")
    assert "proxmox_backup_collector_success 1" in text
    assert text.count("# TYPE proxmox_backup_guest_covered gauge") == 1, "each family together"
    assert 'proxmox_backup_guest_covered{vmid="101",guest="web",type="qemu"} 1' in text
    assert oct(os.stat(out).st_mode & 0o777) == "0o644"
    assert [p.name for p in tmp_path.iterdir()] == ["proxmox_backup.prom"], "no temporary file left"


@pytest.mark.parametrize("failing", ["/cluster/resources", "/cluster/backup", "/nodes/pve1/storage/nas/content",
                                     "/nodes/pve1/tasks", "/nodes/pve1/tasks/T2/log"])
def test_a_failed_read_writes_success_0_and_no_guest_series(host, tmp_path, monkeypatch, failing, capsys):
    _, fail = host
    fail.add(failing)
    out = tmp_path / "proxmox_backup.prom"
    monkeypatch.setattr(sys, "argv", ["proxmox-backup-metrics.py", str(out)])
    assert m.main() == 1
    text = out.read_text(encoding="utf-8")
    assert "proxmox_backup_collector_success 0" in text and "proxmox_backup_guest" not in text
    assert "a read failed" in capsys.readouterr().err


def test_a_failed_write_leaves_the_old_file_and_no_temporary(tmp_path, monkeypatch):
    out = tmp_path / "proxmox_backup.prom"
    out.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(m.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        m.write_atomically(str(out), "new\n")
    assert out.read_text(encoding="utf-8") == "old\n"
    assert [p.name for p in tmp_path.iterdir()] == ["proxmox_backup.prom"]


def test_pvesh_answers_json_or_fails_the_read(monkeypatch):
    import subprocess

    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, *run.answer)

    monkeypatch.setattr(m.subprocess, "run", run)
    run.answer = (0, '[{"a": 1}]', "")
    assert m.pvesh("/x") == [{"a": 1}]
    for answer in ((255, "", "no such path"), (0, "not json", "")):
        run.answer = answer
        with pytest.raises(m.ReadFailed):
            m.pvesh("/x")


def test_label_values_are_escaped():
    assert m.label_text({"guest": 'a"b\\c\nd'}) == '{guest="a\\"b\\\\c\\nd"}'
