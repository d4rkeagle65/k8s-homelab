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
