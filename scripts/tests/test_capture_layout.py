"""capture against a Flux-managed cluster: controller-stamped labels are
stripped, chart sources come from the live HelmRelease, and a hand-written
release's namespace.yaml is never overwritten.

No cluster access: kube calls are monkeypatched, and everything writes to
a throwaway tree under tmp_path.
"""

from __future__ import annotations

from pathlib import Path

from k8s_backup import capture, kube, layout

CATEGORY = layout.CATEGORY_LABEL
from k8s_backup.filetracker import FileTracker
from k8s_backup.fluxowner import FluxOwnership
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
        "kubernetes/prod/apps/media/namespace.yaml": handwritten_ns,
        "kubernetes/prod/apps/media/sonarr/.handwritten": "",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")

    def fake_namespace(context, name):
        return {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": name, "labels": {CATEGORY: "apps"}},
                "spec": {"finalizers": ["kubernetes"]}}

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
    capture._capture_helm_releases(sink, None, [], [], [], False, [], capture.FluxOwnership([]))

    assert (tmp_path / "kubernetes/prod/apps/media/namespace.yaml").read_text(encoding="utf-8") == handwritten_ns
    # A captured release's namespace is still written as before.
    assert (tmp_path / "kubernetes/prod/apps/emby/namespace.yaml").is_file()
    assert not (tmp_path / "kubernetes/prod/apps/media/sonarr/release.yaml").exists()


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


def test_namespace_with_an_apps_folder_is_not_captured_under_cluster(tmp_path):
    (tmp_path / "kubernetes/prod/apps/emby").mkdir(parents=True)
    emby = {"kind": "Namespace", "metadata": {"name": "emby"}}
    tandoor = {"kind": "Namespace", "metadata": {"name": "tandoor"}}
    assert capture._namespace_owned_by_apps(tmp_path, "namespace", emby)
    # No app: it stays under kubernetes/cluster/namespace/.
    assert capture._namespace_owned_by_apps(tmp_path, "namespace", tandoor) is None
    # Other kinds are never affected.
    assert capture._namespace_owned_by_apps(tmp_path, "persistentvolume", {"metadata": {"name": "emby"}}) is None


def test_capture_stops_if_flux_kustomizations_cannot_be_listed(monkeypatch):
    import pytest

    unreachable = "could not list 'kustomizations.kustomize.toolkit.fluxcd.io': Unable to connect to the server"
    monkeypatch.setattr(kube, "get_all", lambda c, kind: ([], unreachable))
    with pytest.raises(capture.ToolError, match="Nothing was written"):
        capture._flux_ownership(None)

    # No Flux on this cluster at all: nothing is foreign, and that's fine.
    no_flux = "could not list 'kustomizations...': error: the server doesn't have a resource type \"kustomizations\""
    monkeypatch.setattr(kube, "get_all", lambda c, kind: ([], no_flux))
    obj = {"metadata": {"labels": {"kustomize.toolkit.fluxcd.io/name": "cluster-shared",
                                   "kustomize.toolkit.fluxcd.io/namespace": "flux-system"}}}
    assert capture._flux_ownership(None).foreign_reason(obj) is None


def test_failed_cluster_listing_keeps_existing_files(tmp_path, monkeypatch):
    issuer = tmp_path / "kubernetes/cluster/clusterissuer.cert-manager.io/letsencrypt.yaml"
    issuer.parent.mkdir(parents=True)
    issuer.write_text("apiVersion: cert-manager.io/v1\nkind: ClusterIssuer\nmetadata:\n  name: letsencrypt\n")
    monkeypatch.setattr(kube, "get_all", lambda c, kind: ([], f"could not list '{kind}': Unable to connect to the server"))

    tracker = FileTracker(tmp_path, capture_owner(tmp_path))
    sink = Sink(tmp_path, False, tracker)
    capture._capture_cluster_resources(sink, None, [], [], False, [], FluxOwnership([]))
    tracker.sweep_orphans()
    assert issuer.is_file()


def test_release_record_is_unchanged_when_the_release_is(tmp_path, monkeypatch):
    monkeypatch.setattr(kube, "get_namespace", lambda c, ns: {
        "apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns, "labels": {CATEGORY: "apps"}}})
    monkeypatch.setattr(capture.helmcli, "list_releases", lambda c: [{"name": "emby", "namespace": "emby"}])
    meta = {"chart": "emby", "version": "1.0.0", "appVersion": "4.9", "revision": 3, "status": "deployed"}
    monkeypatch.setattr(capture.helmcli, "get_metadata", lambda c, name, ns: dict(meta))
    monkeypatch.setattr(capture.helmcli, "get_values_yaml", lambda *a, **k: "{}\n")
    monkeypatch.setattr(capture.helmcli, "repo_update", lambda c: None)
    monkeypatch.setattr(kube, "get_all", lambda c, kind: ([], None))
    monkeypatch.setattr(capture, "_resolve_chart_source", lambda *a: {
        "resolved": True, "kind": "HelmRepository", "repoName": "r", "reason": None,
    })
    path = tmp_path / "kubernetes/prod/apps/emby/emby/release.yaml"

    def run():
        tracker = FileTracker(tmp_path, capture_owner(tmp_path))
        capture._capture_helm_releases(Sink(tmp_path, False, tracker), None, [], [], [], False, [], capture.FluxOwnership([]))
        return path.read_text(encoding="utf-8")

    first = run()
    assert first.startswith("# Generated by k8s-backup-repo-gen.\n")  # no date to change
    assert run() == first  # a later capture of the same release
    meta["revision"] = 4
    upgraded = run()
    assert "revision: 4" in upgraded and upgraded.splitlines()[0] == first.splitlines()[0]


def test_neat_strips_the_labels_commonmetadata_stamps():
    stamped = {CATEGORY: "services", layout.ENV_LABEL: "prod", layout.PART_OF_LABEL: "mqtt",
               layout.MANAGED_BY_LABEL: "promote"}
    cm = {"kind": "ConfigMap", "metadata": {"name": "c", "labels": {**stamped, "app": "mosquitto"}}}
    assert neat(cm)["metadata"]["labels"] == {"app": "mosquitto"}
    # A Namespace keeps its own category, data and exposure; only env is stamped on it.
    ns = {"kind": "Namespace", "metadata": {"name": "mqtt", "labels": {
        CATEGORY: "services", layout.ENV_LABEL: "prod", "homelab.local/data": "files"}}}
    assert neat(ns)["metadata"]["labels"] == {CATEGORY: "services", "homelab.local/data": "files"}


def test_a_cluster_wide_object_an_app_applies_is_not_captured_under_cluster():
    def applied_by(name):
        return {"kustomize.toolkit.fluxcd.io/name": name, "kustomize.toolkit.fluxcd.io/namespace": "flux-system"}
    owner = FluxOwnership([
        {"metadata": {"name": "flux-system", "namespace": "flux-system", "labels": applied_by("flux-system")}},
        {"metadata": {"name": "cluster", "namespace": "flux-system", "labels": applied_by("flux-system")}},
        {"metadata": {"name": "semaphore", "namespace": "flux-system", "labels": applied_by("cluster")}},
    ])
    role = {"kind": "ClusterRole", "metadata": {"name": "semaphore-cluster-health", "labels": applied_by("semaphore")}}
    issuer = {"kind": "ClusterIssuer", "metadata": {"name": "le", "labels": applied_by("cluster-resources")}}
    assert owner.foreign_reason(role) is None  # its entry point is the generator's own `cluster`
    assert "semaphore" in owner.app_owned_reason(role)
    assert owner.app_owned_reason(issuer) is None  # cluster-resources' own objects are captured
    assert owner.app_owned_reason({"kind": "ClusterRole", "metadata": {"name": "by-hand"}}) is None


def test_an_object_another_app_applies_is_not_promoted_into_this_folder():
    def applied_by(name):
        return {"kustomize.toolkit.fluxcd.io/name": name, "kustomize.toolkit.fluxcd.io/namespace": "flux-system"}
    owner = FluxOwnership([
        {"metadata": {"name": "flux-system", "namespace": "flux-system", "labels": applied_by("flux-system")}},
        {"metadata": {"name": "cluster", "namespace": "flux-system", "labels": applied_by("flux-system")}},
        {"metadata": {"name": "immich-extras", "namespace": "flux-system", "labels": applied_by("cluster")}},
        {"metadata": {"name": "immich-backup", "namespace": "flux-system", "labels": applied_by("cluster")}},
    ])
    mine = {"kind": "Secret", "metadata": {"name": "a", "labels": applied_by("immich-extras")}}
    beside = {"kind": "Secret", "metadata": {"name": "b", "labels": applied_by("immich-backup")}}
    by_hand = {"kind": "Secret", "metadata": {"name": "c"}}
    assert owner.foreign_reason(beside) is None  # same entry point, so the older rule keeps it
    assert "immich-backup" in owner.other_app_reason(beside, "immich-extras")
    assert owner.other_app_reason(mine, "immich-extras") is None
    assert owner.other_app_reason(by_hand, "immich-extras") is None  # made by hand: promoted as before
