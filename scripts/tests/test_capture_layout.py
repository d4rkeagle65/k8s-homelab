"""capture against a Flux-managed cluster: controller-stamped labels are
stripped, chart sources come from the live HelmRelease, and a hand-written
release's namespace.yaml is never overwritten.

No cluster access: kube calls are monkeypatched, and everything writes to
a throwaway tree under tmp_path.
"""

from __future__ import annotations

from pathlib import Path

from k8s_backup import capture, kube
from k8s_backup.filetracker import FileTracker
from k8s_backup.neat import neat
from k8s_backup.ownership import capture_owner
from k8s_backup.sink import Sink

FLUX_LABELS = {
    "kustomize.toolkit.fluxcd.io/name": "cluster-resources",
    "kustomize.toolkit.fluxcd.io/namespace": "flux-system",
}


def test_neat_strips_flux_ownership_labels():
    pv = {"kind": "PersistentVolume", "metadata": {"name": "emby-media-pv", "labels": dict(FLUX_LABELS)}}
    assert "labels" not in neat(pv)["metadata"]

    ns = {"kind": "Namespace", "metadata": {"name": "emby", "labels": {**FLUX_LABELS, "team": "media"}}}
    assert neat(ns)["metadata"]["labels"] == {"team": "media"}


def test_chart_source_prefers_live_helmrelease():
    hr = {"spec": {"chart": {"spec": {"sourceRef": {"kind": "HelmRepository", "name": "d4rkeagle65"}}}}}
    assert capture._chart_source_from_helmrelease(hr) == {
        "resolved": True, "kind": "HelmRepository", "repoName": "d4rkeagle65", "reason": None,
    }
    # Not installed by Flux: fall back to the `helm search repo` lookup.
    assert capture._chart_source_from_helmrelease(None) is None
    assert capture._chart_source_from_helmrelease({"spec": {}}) is None


def test_capture_leaves_handwritten_namespace_yaml_alone(tmp_path, monkeypatch):
    handwritten_ns = "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: media\n"
    files = {
        "kubernetes/apps/media/namespace.yaml": handwritten_ns,
        "kubernetes/apps/media/sonarr/.handwritten": "",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")

    def fake_namespace(context, name):
        return {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": name}, "spec": {"finalizers": ["kubernetes"]}}

    monkeypatch.setattr(kube, "get_namespace", fake_namespace)
    monkeypatch.setattr(capture.helmcli, "list_releases", lambda context: [
        {"name": "sonarr", "namespace": "media"},
        {"name": "emby", "namespace": "emby"},
    ])
    monkeypatch.setattr(capture.helmcli, "get_metadata", lambda context, name, ns: {"chart": "c", "version": "1"})
    monkeypatch.setattr(capture.helmcli, "get_values_yaml", lambda *a, **k: "{}\n")
    monkeypatch.setattr(capture.helmcli, "repo_update", lambda context: None)
    monkeypatch.setattr(kube, "get_all", lambda context, kind: ([], None))
    monkeypatch.setattr(capture, "_resolve_chart_source", lambda *a: {
        "resolved": True, "kind": "HelmRepository", "repoName": "r", "reason": None,
    })

    tracker = FileTracker(tmp_path, capture_owner(tmp_path))
    sink = Sink(tmp_path, False, tracker)
    capture._capture_helm_releases(sink, None, [], "t", [], [], False, [], capture.FluxOwnership([]))

    assert (tmp_path / "kubernetes/apps/media/namespace.yaml").read_text(encoding="utf-8") == handwritten_ns
    # A captured release's namespace is still written as before.
    assert (tmp_path / "kubernetes/apps/emby/namespace.yaml").is_file()
    assert not (tmp_path / "kubernetes/apps/media/sonarr/release.yaml").exists()


def test_longhorn_manager_storageclasses_are_skipped():
    from k8s_backup.skipfilter import should_skip

    def sc(name, provisioner="driver.longhorn.io"):
        return {"kind": "StorageClass", "provisioner": provisioner, "metadata": {"name": name}}

    assert should_skip("storageclass", sc("longhorn"))
    assert should_skip("storageclass", sc("longhorn-static"))
    # A Longhorn class you define yourself is still yours to capture.
    assert should_skip("storageclass", sc("longhorn-1replica")) is None
    assert should_skip("storageclass", sc("longhorn", provisioner="example.com/other")) is None


def test_controller_managed_objects_are_skipped():
    from k8s_backup.skipfilter import should_skip

    outpost = {"kind": "Deployment", "metadata": {"name": "ak-outpost-ldap",
               "labels": {"app.kubernetes.io/managed-by": "goauthentik.io"}}}
    assert "goauthentik.io" in should_skip("deployment", outpost)
    mine = {"kind": "Deployment", "metadata": {"name": "x", "labels": {"app.kubernetes.io/managed-by": "kustomize"}}}
    assert should_skip("deployment", mine) is None
