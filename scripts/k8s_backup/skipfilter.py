"""Skip-filter logic shared by cluster-scoped and namespaced resource capture
(task spec section 6).

Skip a resource if any of:
1. metadata.ownerReferences is present (recreated by its parent).
2. label app.kubernetes.io/managed-by=Helm (captured via the Helm release instead).
3. annotation meta.helm.sh/release-name is present (same reason).
4. it's a well-known auto-generated singleton: the `kubernetes` Service in
   `default`, the `kube-root-ca.crt` ConfigMap in any namespace, or the
   `default` ServiceAccount in any namespace.
"""

from __future__ import annotations

import re

# Cluster-scoped resources that are legitimately un-owned and un-Helm-managed
# (so should_skip() below doesn't catch them) but are still not "yours" --
# built-in Kubernetes RBAC objects created by kube-apiserver/kubeadm at
# bootstrap, resources owned by the Tigera/Calico operator, dynamically
# provisioned PersistentVolumes, and built-in PriorityClasses. These get
# captured to the gitignored kubernetes/.local/ tree instead of the
# git-tracked one: still backed up locally, but committing them means git
# thinks it owns content that a Kubernetes version upgrade or a PVC
# recreate will change out from under you.
_BUILTIN_CLUSTERROLE_PREFIXES = ("system:", "kubeadm:", "tigera-operator")
_BUILTIN_CLUSTERROLE_EXACT = {"admin", "edit", "view", "cluster-admin"}
DYNAMIC_PV_NAME_RE = re.compile(
    r"^pvc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

# Namespaces Kubernetes/the underlying platform creates on its own, or that
# belong to infrastructure this repo doesn't itself deploy (the CNI). A
# fresh `kubectl apply` of this repo would never need to (re)create these.
_BUILTIN_OR_INFRA_NAMESPACES = {"default", "kube-system", "kube-public", "kube-node-lease", "tigera-operator"}

# Namespaces where a subject living there is a strong signal of "wiring
# into cluster infrastructure," not an app this repo deploys -- every
# Helm release this repo tracks lives in its own dedicated namespace
# (the one-namespace-per-app convention), never one of these.
_INFRA_SUBJECT_NAMESPACES = {"default", "kube-system", "kube-public", "kube-node-lease"}

_LONGHORN_MANAGED_STORAGECLASSES = {"longhorn", "longhorn-static"}

# app.kubernetes.io/managed-by values naming a controller that creates the
# object and keeps reconciling it -- neither backed up as yours nor promoted,
# or Flux and that controller would fight over it.
_CONTROLLER_MANAGED_BY = {
    "goauthentik.io",  # Authentik's outpost Deployments, Services and token Secrets
}


def _is_builtin_rbac_name(name: str) -> bool:
    return name in _BUILTIN_CLUSTERROLE_EXACT or name.startswith(_BUILTIN_CLUSTERROLE_PREFIXES)


def classify_cluster_locality(kind: str, obj: dict) -> str | None:
    """Return a reason string if this cluster-scoped resource belongs in the
    gitignored kubernetes/.local/ tree rather than the git-tracked one --
    in short, anything that wouldn't actually be used to deploy this repo
    to a cluster (built-in, bootstrap-managed, or wiring into
    infrastructure the repo doesn't itself track). None means: track it
    normally. Only meaningful for resources that already passed
    should_skip() (i.e. aren't Helm-managed/owned/singleton).
    """
    kind_norm = kind.split(".", 1)[0].lower()
    metadata = obj.get("metadata", {})
    name = metadata.get("name", "")

    if kind_norm == "namespace" and name in _BUILTIN_OR_INFRA_NAMESPACES:
        return "built-in namespace or CNI-operator namespace this repo doesn't itself deploy"

    if kind_norm == "clusterrole" and _is_builtin_rbac_name(name):
        return "built-in RBAC object (kube-apiserver/kubeadm bootstrap or the Tigera operator), not yours"

    if kind_norm == "clusterrolebinding":
        if _is_builtin_rbac_name(name):
            return "built-in RBAC object (kube-apiserver/kubeadm bootstrap or the Tigera operator), not yours"
        role_ref_name = obj.get("roleRef", {}).get("name", "")
        if _is_builtin_rbac_name(role_ref_name):
            return f"grants access via the built-in ClusterRole '{role_ref_name}', not a role this repo defines"
        subjects = obj.get("subjects") or []
        subject_namespaces = {s.get("namespace") for s in subjects if s.get("kind") == "ServiceAccount"}
        if subject_namespaces and subject_namespaces <= _INFRA_SUBJECT_NAMESPACES:
            return "binds a ServiceAccount that lives in a built-in namespace, not one this repo deploys into"

    if kind_norm == "persistentvolume" and DYNAMIC_PV_NAME_RE.match(name):
        return "dynamically provisioned by a StorageClass; recreated automatically alongside its PVC"

    if kind_norm == "priorityclass" and name.startswith("system-"):
        return "built-in Kubernetes PriorityClass"

    return None


def should_skip(kind: str, obj: dict) -> str | None:
    """Return a short reason string if `obj` should be skipped, else None."""
    metadata = obj.get("metadata", {})

    if metadata.get("ownerReferences"):
        return "has ownerReferences (recreated by its parent)"

    labels = metadata.get("labels") or {}
    if labels.get("app.kubernetes.io/managed-by") == "Helm":
        return "managed-by=Helm (captured via Helm release)"
    managed_by = labels.get("app.kubernetes.io/managed-by")
    if managed_by in _CONTROLLER_MANAGED_BY:
        return f"managed-by={managed_by} (created and reconciled by that controller)"

    annotations = metadata.get("annotations") or {}
    if "meta.helm.sh/release-name" in annotations:
        return "has meta.helm.sh/release-name annotation (captured via Helm release)"

    name = metadata.get("name")
    namespace = metadata.get("namespace")
    kind_norm = kind.split(".", 1)[0].lower()

    if kind_norm == "service" and namespace == "default" and name == "kubernetes":
        return "built-in kubernetes Service in default namespace"
    if kind_norm == "configmap" and name == "kube-root-ca.crt":
        return "auto-generated kube-root-ca.crt ConfigMap"
    if kind_norm == "serviceaccount" and name == "default":
        return "auto-generated default ServiceAccount"
    # CloudNativePG writes this into every namespace holding a Cluster,
    # without an ownerReference, and rewrites it on each operator upgrade.
    if (
        kind_norm == "configmap"
        and name == "cnpg-default-monitoring"
        and "cnpg.io/operatorVersion" in (metadata.get("annotations") or {})
    ):
        return "created by the CloudNativePG operator"
    # longhorn-manager builds these from its longhorn-storageclass ConfigMap
    # (which the Helm release owns) and carries no label or ownerReference
    # saying so. Committing them would have cluster-resources fight the
    # manager. A StorageClass you create for Longhorn yourself is kept.
    if (
        kind_norm == "storageclass"
        and obj.get("provisioner") == "driver.longhorn.io"
        and name in _LONGHORN_MANAGED_STORAGECLASSES
    ):
        return "created by longhorn-manager from its Helm-managed ConfigMap"

    return None
