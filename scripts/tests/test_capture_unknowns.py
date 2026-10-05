"""capture saying "couldn't find out" instead of acting on it: Helm repository
files a live HelmRelease still uses, a failed chart search, and manifests the IP
inventory couldn't parse."""

from __future__ import annotations

import subprocess

import pytest

from k8s_backup import capture, helmcli, kube, procutil, yamlio
from k8s_backup.filetracker import FileTracker
from k8s_backup.ownership import capture_owner
from k8s_backup.sink import Sink

REPOS = "kubernetes/flux/meta/repositories"


def _repo_files(tmp_path, *names):
    d = tmp_path / REPOS
    d.mkdir(parents=True)
    for n in names:
        (d / f"{n}.yaml").write_text(f"kind: HelmRepository\nmetadata:\n  name: {n}\n", encoding="utf-8")


def _hr(repo):
    return {"spec": {"chart": {"spec": {"sourceRef": {"kind": "HelmRepository", "name": repo}}}}}


def _run_repositories(tmp_path, monkeypatch, local_repos, live, err=None):
    monkeypatch.setattr(kube, "get_all", lambda context, kind: ([] if err else live, err))
    tracker = FileTracker(tmp_path, capture_owner(tmp_path))
    sink = Sink(tmp_path, False, tracker)
    warnings = []
    written = capture._write_helm_repository_files(sink, local_repos)
    capture._keep_referenced_repositories(sink, None, written, warnings)
    tracker.sweep_orphans()
    return sorted(p.stem for p in (tmp_path / REPOS).glob("*.yaml") if p.name != "kustomization.yaml"), warnings


def test_a_repository_a_live_helmrelease_uses_is_kept_without_helm_repo_list(tmp_path, monkeypatch):
    _repo_files(tmp_path, "jetstack", "immich", "stale")
    left, warnings = _run_repositories(tmp_path, monkeypatch, [], [_hr("jetstack"), _hr("immich")])
    assert left == ["immich", "jetstack"]  # "stale" is used by nothing, so it still goes
    assert any("jetstack" in w and "kept" in w for w in warnings)


def test_every_repository_file_is_kept_when_the_helmreleases_cant_be_listed(tmp_path, monkeypatch):
    _repo_files(tmp_path, "jetstack", "stale")
    left, warnings = _run_repositories(tmp_path, monkeypatch, [], [], err="could not list 'helmreleases': forbidden")
    assert left == ["jetstack", "stale"]
    assert any("kept every Helm repository file" in w for w in warnings)


class _Proc:
    def __init__(self, code, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def test_helm_repo_list_none_configured_is_empty_but_a_failure_raises(monkeypatch):
    monkeypatch.setattr(procutil, "run", lambda *a, **k: _Proc(1, err="Error: no repositories to show"))
    assert helmcli.repo_list(None) == []
    monkeypatch.setattr(procutil, "run", lambda *a, **k: _Proc(1, err="Error: permission denied"))
    with pytest.raises(procutil.ToolError, match="permission denied"):
        helmcli.repo_list(None)


def test_a_failed_chart_search_is_unknown_not_absent(monkeypatch):
    monkeypatch.setattr(procutil, "run", lambda *a, **k: _Proc(1, err="Error: index missing"))
    assert helmcli.search_repo_exact(None, "r", "c", "1.0") is None
    monkeypatch.setattr(procutil, "run", lambda *a, **k: _Proc(0, out='[{"name":"r/c","version":"2.0"}]'))
    assert helmcli.search_repo_exact(None, "r", "c", "1.0") is False
    monkeypatch.setattr(capture.helmcli, "search_repo_exact", lambda *a: None)
    source = capture._resolve_chart_source(None, "c", "1.0", [{"name": "r", "url": "https://x"}], False)
    assert source["resolved"] is False and "couldn't search r" in source["reason"]


def test_the_ip_inventory_names_a_manifest_it_couldnt_parse(tmp_path):
    (tmp_path / "kubernetes").mkdir()
    (tmp_path / "kubernetes/good.yaml").write_text("spec:\n  ip: 10.0.0.1\n", encoding="utf-8")
    (tmp_path / "kubernetes/bad.yaml").write_text("a: [unclosed\n", encoding="utf-8")
    warnings = []
    rows = capture._collect_ip_inventory(tmp_path, {}, warnings)
    assert any(r.get("file", r.get("path", "")) == "kubernetes/good.yaml" or "good.yaml" in str(r) for r in rows)
    assert any("kubernetes/bad.yaml" in w for w in warnings)
