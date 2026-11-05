"""scripts/limitrange_jobs.py: which Job templates a LimitRange's defaults reach."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "limitrange_jobs", Path(__file__).resolve().parents[1] / "limitrange_jobs.py")
lj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lj)

LR = [{"metadata": {"namespace": "a"}, "spec": {"limits": [
    {"type": "Container", "defaultRequest": {"cpu": "10m", "memory": "64Mi"},
     "default": {"cpu": "500m", "memory": "512Mi"}},
    {"type": "Pod", "max": {"memory": "4Gi"}}]}}]


def job(name, kind="Job", resources=None, init=None, ns="a"):
    spec = {"template": {"spec": {"containers": [{"name": "main", "resources": resources or {}}],
                                  **({"initContainers": init} if init else {})}}}
    if kind == "CronJob":
        spec = {"jobTemplate": {"spec": spec}}
    return {"kind": kind, "metadata": {"name": name, "namespace": ns}, "spec": spec}


def test_defaults_read_only_container_limits():
    d = lj.defaults_by_namespace(LR)
    assert d == {"a": {"requests": {"cpu": "10m", "memory": "64Mi"}, "limits": {"cpu": "500m", "memory": "512Mi"}}}


def test_quantity():
    assert lj.quantity("512Mi") == 512 * 2**20
    assert lj.quantity("1Gi") == 2**30
    assert lj.quantity("500m") == 0.5
    assert lj.quantity("2") == 2.0


def test_gaps_name_each_missing_field_and_the_limit_it_runs_under():
    d = lj.defaults_by_namespace(LR)["a"]
    rows = lj.gaps(job("j", resources={"requests": {"memory": "128Mi"}}), d)
    assert rows == [{"container": "main", "missing": {"requests.cpu": "10m", "limits.cpu": "500m",
                                                       "limits.memory": "512Mi"}, "memory_limit": "512Mi"}]
    full = {"requests": {"cpu": "1m", "memory": "1Mi"}, "limits": {"cpu": "1", "memory": "1Gi"}}
    assert lj.gaps(job("j", resources=full), d) == []


def test_gaps_read_a_cronjobs_template_and_init_containers():
    d = lj.defaults_by_namespace(LR)["a"]
    cj = job("c", kind="CronJob", resources={"limits": {"memory": "2Gi", "cpu": "1"},
                                              "requests": {"memory": "1Gi", "cpu": "1m"}},
             init=[{"name": "init", "resources": {}}])
    rows = lj.gaps(cj, d)
    assert [r["container"] for r in rows] == ["init"]


def test_report_flags_a_peak_near_the_limit_and_matches_pods_to_the_longest_name():
    d = lj.defaults_by_namespace(LR)
    objs = [job("backup", kind="CronJob"), job("backup-cleanup", kind="CronJob")]
    peak = {("a", "backup-29001-abcde", "main"): 450 * 2**20,         # backup: 450Mi of 512Mi
            ("a", "backup-cleanup-29001-fghij", "main"): 900 * 2**20}  # cleanup's own pod
    lines, flagged = lj.report(objs, d, peak)
    by = {line.split(" [")[0]: line for line in lines}
    assert "peak 450Mi" in by["a CronJob/backup"] and "FLAG" in by["a CronJob/backup"]
    assert "peak 900Mi" in by["a CronJob/backup-cleanup"]
    assert flagged == 2


def test_report_says_when_no_pod_was_seen():
    d = lj.defaults_by_namespace(LR)
    lines, flagged = lj.report([job("once")], d, {})
    assert "no pod in Prometheus" in lines[0] and flagged == 0


def test_report_without_peaks_names_the_gaps_only():
    d = lj.defaults_by_namespace(LR)
    lines, flagged = lj.report([job("once")], d, None)
    assert "peak" not in lines[0] and "gets requests.cpu=10m" in lines[0] and flagged == 0


# --- what it reads from the cluster: a stand-in for run (kubectl and helm) ------------------------

import json
import sys

import pytest


def _cluster(monkeypatch, answers):
    """run() answers from `answers`, keyed by the first arguments that match; a value that's an
    exception is raised, as a refused read is."""
    calls = []

    def run(args):
        calls.append(args)
        for key, value in answers.items():
            if tuple(args[:len(key)]) == key:
                if isinstance(value, Exception):
                    raise value
                return value if isinstance(value, str) else json.dumps(value)
        raise AssertionError(f"unexpected command {args}")

    monkeypatch.setattr(lj, "run", run)
    return calls


def _prom(results, status="success"):
    return {"status": status, "data": {"result": [
        {"metric": {"namespace": ns, "pod": pod, "container": c}, "value": [0, str(v)]} for ns, pod, c, v in results]}}


def test_peaks_asks_prometheus_for_the_namespaces_and_reads_each_container(monkeypatch):
    calls = _cluster(monkeypatch, {("kubectl", "get", "--raw"): _prom([("a", "j-1", "main", 1024.5)])})
    assert lj.peaks(["a", "b"]) == {("a", "j-1", "main"): 1024.5}
    query = calls[0][3]
    assert query.startswith(lj.PROMETHEUS + "?query=")
    assert "a%7Cb" in query and "15d" in query


def test_a_failed_prometheus_query_could_not_answer(monkeypatch):
    _cluster(monkeypatch, {("kubectl", "get", "--raw"): {"status": "error", "error": "bad query"}})
    with pytest.raises(lj.CouldNotAnswer, match="bad query"):
        lj.peaks(["a"])


HOOKS = """\
apiVersion: v1
kind: ServiceAccount
metadata: {name: x}
---
apiVersion: batch/v1
kind: Job
metadata: {name: migrate}
spec: {template: {spec: {containers: [{name: main}]}}}
"""


def test_hook_jobs_reads_each_releases_hooks_in_the_namespaces_given(monkeypatch):
    calls = _cluster(monkeypatch, {
        ("helm", "list"): [{"name": "app", "namespace": "a"}, {"name": "other", "namespace": "z"}],
        ("helm", "get", "hooks", "app"): HOOKS,
    })
    jobs = lj.hook_jobs({"a"})
    assert [(j["metadata"]["name"], j["metadata"]["namespace"], j["hook_of"]) for j in jobs] == [("migrate", "a", "app")]
    assert ["helm", "get", "hooks", "other", "-n", "z"] not in calls, "a namespace not asked for isn't read"


def test_a_refused_read_stops_the_run_answering_nothing(monkeypatch, capsys):
    _cluster(monkeypatch, {("kubectl", "get", "limitranges"): lj.CouldNotAnswer("kubectl get limitranges: forbidden")})
    monkeypatch.setattr(sys, "argv", ["limitrange_jobs.py"])
    assert lj.main() == 2
    assert "COULD NOT ANSWER" in capsys.readouterr().err


def _main_cluster(monkeypatch, peak_bytes):
    owned = job("backup-29001")
    owned["metadata"]["ownerReferences"] = [{"kind": "CronJob", "name": "backup"}]
    return _cluster(monkeypatch, {
        ("kubectl", "get", "limitranges"): {"items": LR},
        ("kubectl", "get", "cronjobs"): {"items": [job("backup", kind="CronJob"), job("elsewhere", kind="CronJob", ns="z")]},
        ("kubectl", "get", "jobs"): {"items": [owned, job("once")]},
        ("helm", "list"): [{"name": "app", "namespace": "a"}],
        ("helm", "get", "hooks", "app"): HOOKS,
        ("kubectl", "get", "--raw"): _prom([("a", "backup-29001-abcde", "main", peak_bytes)]),
    })


def test_main_reads_cronjobs_unowned_jobs_and_hooks_and_flags_a_peak_near_the_limit(monkeypatch, capsys):
    _main_cluster(monkeypatch, 500 * 2**20)  # of the 512Mi default limit
    monkeypatch.setattr(sys, "argv", ["limitrange_jobs.py"])
    assert lj.main() == 1
    out = capsys.readouterr().out
    assert "1 namespaces with Container defaults; 3 Job templates read" in out
    assert "a CronJob/backup [main]" in out and "FLAG" in out
    assert "a Job/once [main]" in out and "a Job/migrate (hook of app) [main]" in out
    assert "backup-29001" not in out.replace("backup-29001-abcde", ""), "a CronJob's own Job is its template again"
    assert "elsewhere" not in out, "a namespace with no LimitRange isn't read"


def test_main_answers_0_with_nothing_flagged_and_skips_prometheus_when_asked(monkeypatch, capsys):
    calls = _main_cluster(monkeypatch, 10 * 2**20)
    monkeypatch.setattr(sys, "argv", ["limitrange_jobs.py"])
    assert lj.main() == 0
    capsys.readouterr()
    calls.clear()
    monkeypatch.setattr(sys, "argv", ["limitrange_jobs.py", "--no-peaks"])
    assert lj.main() == 0
    assert not [c for c in calls if c[:3] == ["kubectl", "get", "--raw"]]
    out = capsys.readouterr().out
    assert "flagged" not in out and "peak" not in out and "Prometheus" not in out


def test_main_with_no_container_limitrange_has_nothing_to_check(monkeypatch, capsys):
    _cluster(monkeypatch, {("kubectl", "get", "limitranges"): {"items": []}})
    monkeypatch.setattr(sys, "argv", ["limitrange_jobs.py", "--namespace", "a"])
    assert lj.main() == 0
    assert "nothing to check" in capsys.readouterr().out


def test_run_returns_stdout_and_a_failure_could_not_answer():
    assert lj.run([sys.executable, "-c", "print('ok')"]).strip() == "ok"
    with pytest.raises(lj.CouldNotAnswer):
        lj.run([sys.executable, "-c", "import sys; sys.exit(1)"])
