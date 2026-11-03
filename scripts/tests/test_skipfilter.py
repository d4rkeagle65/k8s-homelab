"""classify_cluster_locality decides which cluster-scoped objects capture keeps out of git
(kubernetes/.local/). test_structure.py's test of the tracked tree finds none there, which
it would also do if the classifier stopped matching, so each rule is shown firing here.
"""

from __future__ import annotations

import pytest

from k8s_backup.skipfilter import classify_cluster_locality


def _obj(name, **extra):
    return {"metadata": {"name": name}, **extra}


@pytest.mark.parametrize("kind, obj", [
    ("namespace", _obj("kube-system")),
    ("clusterrole.rbac.authorization.k8s.io", _obj("system:controller:job-controller")),
    ("clusterrole", _obj("view")),
    ("clusterrolebinding", _obj("kubeadm:get-nodes")),
    ("clusterrolebinding", _obj("app-reader", roleRef={"name": "view"})),
    ("clusterrolebinding", _obj("app-binding", roleRef={"name": "app-role"},
                                subjects=[{"kind": "ServiceAccount", "namespace": "kube-system"}])),
    ("persistentvolume", _obj("pvc-0123abcd-0123-0123-0123-0123456789ab")),
    ("priorityclass", _obj("system-node-critical")),
])
def test_builtin_and_infrastructure_objects_stay_local(kind, obj):
    assert classify_cluster_locality(kind, obj)


@pytest.mark.parametrize("kind, obj", [
    ("namespace", _obj("babybuddy")),
    ("clusterrole", _obj("app-role")),
    ("clusterrolebinding", _obj("app-binding", roleRef={"name": "app-role"},
                                subjects=[{"kind": "ServiceAccount", "namespace": "babybuddy"}])),
    ("persistentvolume", _obj("emby-media")),
    ("priorityclass", _obj("homelab-critical")),
])
def test_objects_this_repo_deploys_are_tracked(kind, obj):
    assert classify_cluster_locality(kind, obj) is None
