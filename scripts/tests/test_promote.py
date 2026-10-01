"""Promotion (a `.promote` marker): capture writes a namespace's non-Helm
resources into the release's app/ as Flux-applied manifests, cleaned so
they re-apply both over the live objects and onto a rebuilt cluster, and
holds them unchanged whenever this run's data can't be trusted.

No cluster access: kube calls are monkeypatched, and everything writes to
a throwaway tree under tmp_path.
"""

from __future__ import annotations

from ruamel.yaml import YAML

from k8s_backup import capture, generate, kube, promote
from k8s_backup.filetracker import FileTracker
from k8s_backup.fluxowner import FluxOwnership
from k8s_backup.ownership import capture_owner, generate_owner
from k8s_backup.secretscan import REDACTION_PLACEHOLDER
from k8s_backup.sink import Sink

_yaml = YAML(typ="safe")
DYNAMIC_PV = "pvc-ba335a83-b39c-46cc-b267-236b9976070f"
APP = "kubernetes/apps/babybuddy/babybuddy/app"


# ---- clean_for_gitops -------------------------------------------------------

def test_service_drops_assigned_ips_but_keeps_headless():
    svc = {"kind": "Service", "metadata": {"name": "s"}, "spec": {
        "clusterIP": "10.228.166.228", "clusterIPs": ["10.228.166.228"],
        "type": "LoadBalancer", "healthCheckNodePort": 31000,
        "ports": [{"port": 8000, "nodePort": 30080}],
    }}
    spec = promote.clean_for_gitops(svc)["spec"]
    assert "clusterIP" not in spec and "clusterIPs" not in spec and "healthCheckNodePort" not in spec
    assert spec["ports"] == [{"port": 8000}]

    headless = {"kind": "Service", "metadata": {"name": "h"}, "spec": {"clusterIP": "None", "clusterIPs": ["None"]}}
    assert promote.clean_for_gitops(headless)["spec"]["clusterIP"] == "None"


def test_pvc_unpins_dynamic_volume_keeps_static_and_disables_prune():
    def pvc(volume):
        return {"kind": "PersistentVolumeClaim", "metadata": {
            "name": "c", "finalizers": ["kubernetes.io/pvc-protection"],
            "annotations": {"pv.kubernetes.io/bind-completed": "yes"},
        }, "spec": {"volumeName": volume, "storageClassName": "nfs-retain-rwo"}}

    dynamic = promote.clean_for_gitops(pvc(DYNAMIC_PV))
    assert "volumeName" not in dynamic["spec"]
    assert "finalizers" not in dynamic["metadata"]
    assert dynamic["metadata"]["annotations"] == {promote.PRUNE_ANNOTATION: "disabled"}
    assert promote.clean_for_gitops(pvc("synmedia-nfs"))["spec"]["volumeName"] == "synmedia-nfs"


def test_workload_bookkeeping_is_dropped_but_pod_template_kept():
    restarted = {"kubectl.kubernetes.io/restartedAt": "2026-09-01"}
    deploy = {"kind": "Deployment", "metadata": {"name": "d", "annotations": {"deployment.kubernetes.io/revision": "13"}},
              "spec": {"template": {"metadata": {"creationTimestamp": None, "labels": {"app": "d"},
                                                 "annotations": dict(restarted)}}}}
    cleaned = promote.clean_for_gitops(deploy)
    assert "annotations" not in cleaned["metadata"]
    # Dropping restartedAt changes the pod template once Flux adopts the
    # Deployment, which rolls every pod (this happened to babybuddy).
    assert cleaned["spec"]["template"]["metadata"] == {"labels": {"app": "d"}, "annotations": restarted}

    sts = {"kind": "StatefulSet", "metadata": {"name": "m"}, "spec": {"volumeClaimTemplates": [
        {"metadata": {"name": "data", "creationTimestamp": None}, "status": {"phase": "Pending"}}]}}
    assert promote.clean_for_gitops(sts)["spec"]["volumeClaimTemplates"] == [{"metadata": {"name": "data"}}]


def test_database_cluster_disables_prune():
    db = {"kind": "Cluster", "apiVersion": "postgresql.cnpg.io/v1", "metadata": {"name": "db"}, "spec": {"instances": 2}}
    assert promote.clean_for_gitops(db)["metadata"]["annotations"] == {promote.PRUNE_ANNOTATION: "disabled"}


def test_foreign_variables_are_escaped_for_flux():
    doc = {"data": {"run.sh": "echo ${HOME} on ${BASE_DOMAIN}; echo $${ALREADY}", "n": 3}}
    assert promote.escape_foreign_variables(doc, {"BASE_DOMAIN"}) == {
        "data": {"run.sh": "echo $${HOME} on ${BASE_DOMAIN}; echo $${ALREADY}", "n": 3}
    }


def test_statefulset_claims_are_recognised():
    sts = {"metadata": {"name": "mosquitto"}, "spec": {"volumeClaimTemplates": [{"metadata": {"name": "data"}}]}}
    [pattern] = promote.statefulset_claim_names([sts])
    assert pattern.match("data-mosquitto-0")
    assert not pattern.match("mosquitto-data")


# ---- capture / generate end to end ------------------------------------------

def _obj(kind, name, ns="babybuddy", **extra):
    return {"apiVersion": "v1", "kind": kind, "metadata": {"name": name, "namespace": ns}, **extra}


CLUSTER = {
    "deployment": [_obj("Deployment", "babybuddy-server", spec={"template": {"metadata": {"labels": {"app": "bb"}}}})],
    "service": [_obj("Service", "babybuddy-svc", spec={"clusterIP": "10.0.0.1", "ports": [{"port": 8000}]}),
                _obj("Service", "other-svc", ns="other", spec={"clusterIP": "10.0.0.2"})],
    "persistentvolumeclaim": [_obj("PersistentVolumeClaim", "babybuddy-config", spec={"volumeName": DYNAMIC_PV})],
    "configmap": [_obj("ConfigMap", "babybuddy-env", data={"TZ": "UTC"})],
}


def _serve(monkeypatch, cluster, failing=()):
    def get_all(context, kind):
        if kind in failing:
            return [], f"could not list '{kind}'"
        return cluster.get(kind, []), None

    monkeypatch.setattr(kube, "get_all", get_all)
    monkeypatch.setattr(kube, "get_namespace", lambda context, ns: _obj("Namespace", ns, ns=None))


def _mark(tmp_path):
    marker = tmp_path / "kubernetes/apps/babybuddy/babybuddy/.promote"
    marker.parent.mkdir(parents=True)
    marker.write_text("", encoding="utf-8")


def _capture(tmp_path):
    tracker = FileTracker(tmp_path, capture_owner(tmp_path))
    sink = Sink(tmp_path, False, tracker)
    warnings: list[str] = []
    capture._capture_namespaced_resources(sink, None, warnings, [], False, [], FluxOwnership([]))
    tracker.sweep_orphans()
    return tracker.report, warnings


def test_promoted_namespace_is_captured_into_app_and_generated(tmp_path, monkeypatch):
    _mark(tmp_path)
    _serve(monkeypatch, CLUSTER)
    _capture(tmp_path)

    app = tmp_path / APP
    assert sorted(p.name for p in app.glob("*.yaml")) == [
        "configmap-babybuddy-env.yaml", "deployment-babybuddy-server.yaml",
        "persistentvolumeclaim-babybuddy-config.yaml", "service-babybuddy-svc.yaml",
    ]
    assert "clusterIP" not in _yaml.load((app / "service-babybuddy-svc.yaml").read_text())["spec"]
    assert (tmp_path / "kubernetes/apps/babybuddy/namespace.yaml").is_file()
    # Not promoted: still the raw safety net.
    assert (tmp_path / "kubernetes/raw/other/service/other-svc.yaml").is_file()
    assert not (tmp_path / "kubernetes/raw/babybuddy").exists()

    generate.run(tmp_path, dry_run=False, verbose=False)
    ks = _yaml.load((tmp_path / "kubernetes/apps/babybuddy/babybuddy/ks.yaml").read_text())
    assert ks["spec"]["path"] == f"./{APP}"
    assert ks["spec"]["targetNamespace"] == "babybuddy"
    assert len(_yaml.load((app / "kustomization.yaml").read_text())["resources"]) == 4
    assert "babybuddy" in _yaml.load((tmp_path / "kubernetes/apps/kustomization.yaml").read_text())["resources"]


def test_deleted_resource_is_swept_but_failed_listing_holds(tmp_path, monkeypatch):
    _mark(tmp_path)
    _serve(monkeypatch, CLUSTER)
    _capture(tmp_path)
    svc_file = tmp_path / APP / "service-babybuddy-svc.yaml"

    # The kind couldn't be listed: nothing may be swept, or Flux would prune live objects.
    _serve(monkeypatch, CLUSTER, failing=("service",))
    report, warnings = _capture(tmp_path)
    assert svc_file.is_file() and not report.removed
    assert any("left unchanged" in w for w in warnings)

    # Actually gone from the cluster: swept.
    _serve(monkeypatch, {**CLUSTER, "service": []})
    report, _ = _capture(tmp_path)
    assert not svc_file.exists()
    assert f"{APP}/service-babybuddy-svc.yaml" in report.removed


def test_redaction_holds_existing_manifests(tmp_path, monkeypatch):
    _mark(tmp_path)
    _serve(monkeypatch, CLUSTER)
    _capture(tmp_path)
    before = (tmp_path / APP / "configmap-babybuddy-env.yaml").read_text()

    leaky = _obj("ConfigMap", "babybuddy-env", data={"TZ": "UTC", "AWS_KEY": "AKIA" + "IOSFODNN7EXAMPLE"})  # split: keep the scanner off this file
    _serve(monkeypatch, {**CLUSTER, "configmap": [leaky]})
    _, warnings = _capture(tmp_path)

    after = (tmp_path / APP / "configmap-babybuddy-env.yaml").read_text()
    assert after == before and REDACTION_PLACEHOLDER not in after
    assert any("needed credential redaction" in w for w in warnings)


def test_statefulset_pvcs_are_not_promoted(tmp_path, monkeypatch):
    _mark(tmp_path)
    _serve(monkeypatch, {
        "statefulset": [_obj("StatefulSet", "mosquitto", spec={"volumeClaimTemplates": [{"metadata": {"name": "data"}}]})],
        "persistentvolumeclaim": [_obj("PersistentVolumeClaim", "data-mosquitto-0"),
                                  _obj("PersistentVolumeClaim", "mosquitto-data")],
    })
    _capture(tmp_path)
    names = sorted(p.name for p in (tmp_path / APP).glob("*.yaml"))
    assert names == ["persistentvolumeclaim-mosquitto-data.yaml", "statefulset-mosquitto.yaml"]


def test_ownership_of_promoted_files(tmp_path):
    _mark(tmp_path)
    capture_owns, generate_owns = capture_owner(tmp_path), generate_owner(tmp_path)
    assert capture_owns(f"{APP}/deployment-babybuddy-server.yaml")
    assert not capture_owns(f"{APP}/kustomization.yaml")
    assert generate_owns(f"{APP}/kustomization.yaml")
    assert not capture_owns("kubernetes/apps/emby/emby/app/deployment-x.yaml")  # not promoted


def test_storage_classes_used():
    assert promote.storage_classes_used({"kind": "PersistentVolumeClaim", "spec": {"storageClassName": "nfs-client"}}) == ["nfs-client"]
    assert promote.storage_classes_used({"kind": "Cluster", "spec": {
        "storage": {"storageClass": "longhorn"}, "walStorage": {"storageClass": "nfs-retain-rwo"}}}) == ["longhorn", "nfs-retain-rwo"]
    assert promote.storage_classes_used({"kind": "StatefulSet", "spec": {"volumeClaimTemplates": [
        {"spec": {"storageClassName": "a"}}, {"spec": {}}]}}) == ["a"]
    assert promote.storage_classes_used({"kind": "Deployment", "spec": {}}) == []


def test_missing_storage_class_is_warned_not_blocked(tmp_path, monkeypatch):
    _mark(tmp_path)
    cluster = {**CLUSTER, "persistentvolumeclaim": [_obj("PersistentVolumeClaim", "babybuddy-config",
               spec={"storageClassName": "nfs-client"})], "storageclass": [{"metadata": {"name": "nfs-retain-rwo"}}]}
    _serve(monkeypatch, cluster)
    _, warnings = _capture(tmp_path)
    assert (tmp_path / APP / "persistentvolumeclaim-babybuddy-config.yaml").is_file()
    assert any("'nfs-client' doesn't exist" in w for w in warnings)


def test_bare_placeholder_is_wrapped_so_flux_substitutes_it():
    from k8s_backup.varsub import normalize_placeholder

    assert normalize_placeholder("Child1") == "${Child1}"
    assert normalize_placeholder("${BASE_DOMAIN}") == "${BASE_DOMAIN}"
    assert normalize_placeholder("not a var name") == "not a var name"


def test_lost_pvcs_are_not_promoted(tmp_path, monkeypatch):
    _mark(tmp_path)
    lost = _obj("PersistentVolumeClaim", "redis-data-old-0", status={"phase": "Lost"})
    _serve(monkeypatch, {**CLUSTER, "persistentvolumeclaim": [*CLUSTER["persistentvolumeclaim"], lost]})
    _, warnings = _capture(tmp_path)
    assert not (tmp_path / APP / "persistentvolumeclaim-redis-data-old-0.yaml").exists()
    assert (tmp_path / APP / "persistentvolumeclaim-babybuddy-config.yaml").is_file()
    assert any("PVC is Lost" in w for w in warnings)


# ---- restartedAt -------------------------------------------------------------

RESTARTED = promote.RESTARTED_AT_ANNOTATION


def _deploy(restarted_at=None, name="d"):
    meta = {"labels": {"app": name}}
    if restarted_at:
        meta["annotations"] = {RESTARTED: restarted_at}
    return {"kind": "Deployment", "metadata": {"name": name}, "spec": {"template": {"metadata": meta}}}


def _restarted_at(doc):
    return ((doc["spec"]["template"]["metadata"].get("annotations")) or {}).get(RESTARTED)


def test_restarted_at_is_frozen_at_the_value_git_has():
    # First capture: nothing in git yet, so the live value is kept.
    assert _restarted_at(promote.freeze_restarted_at(_deploy("live"), None)) == "live"
    # A later `kubectl rollout restart` doesn't change what git has...
    assert _restarted_at(promote.freeze_restarted_at(_deploy("live"), _deploy("git"))) == "git"
    # ...nor adds the annotation where git has none.
    frozen = promote.freeze_restarted_at(_deploy("live"), _deploy())
    assert "annotations" not in frozen["spec"]["template"]["metadata"]
    # Git's value is kept even if the live one is gone: Flux fails an apply
    # whose ignored field is missing from a manifest it owns.
    assert _restarted_at(promote.freeze_restarted_at(_deploy(), _deploy("git"))) == "git"
    # Another object, or one without a pod template: untouched.
    assert _restarted_at(promote.freeze_restarted_at(_deploy("live"), _deploy("git", name="other"))) == "live"
    cm = {"kind": "ConfigMap", "metadata": {"name": "d"}, "data": {"a": "b"}}
    assert promote.freeze_restarted_at(cm, cm) == cm


def test_capture_keeps_restarted_at_and_flux_ignores_it(tmp_path, monkeypatch):
    def cluster(restarted_at):
        deploy = _obj("Deployment", "babybuddy-server", spec={"template": {"metadata": {
            "labels": {"app": "bb"}, "annotations": {RESTARTED: restarted_at}}}})
        return {**CLUSTER, "deployment": [deploy]}

    _mark(tmp_path)
    path = tmp_path / APP / "deployment-babybuddy-server.yaml"
    _serve(monkeypatch, cluster("2026-06-14T17:41:12-04:00"))
    _capture(tmp_path)
    before = path.read_text()
    assert "2026-06-14T17:41:12-04:00" in before

    _serve(monkeypatch, cluster("2026-10-01T09:00:00-04:00"))  # kubectl rollout restart
    _capture(tmp_path)
    assert path.read_text() == before

    generate.run(tmp_path, dry_run=False, verbose=False)
    ks = _yaml.load((tmp_path / "kubernetes/apps/babybuddy/babybuddy/ks.yaml").read_text())
    assert ks["spec"]["ignore"] == [
        {"paths": ["/spec/template/metadata/annotations/kubectl.kubernetes.io~1restartedAt"]}
    ]
