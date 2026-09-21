"""Curated resource lists and skip-filter constants (task spec sections 3, 4, 6)."""

from __future__ import annotations

# Kubernetes creates these automatically; the task spec says to skip them.
BUILTIN_NAMESPACES_TO_SKIP = {"default", "kube-system", "kube-public", "kube-node-lease"}

# Kind strings use kubectl's TYPE[.VERSION][.GROUP] addressing (see
# `kubectl get --help`, "Use kubectl api-resources for a complete list") so
# short names that exist in more than one API group resolve unambiguously.
CLUSTER_RESOURCE_KINDS = [
    "namespace",
    "persistentvolume",
    "storageclass",
    "clusterissuer.cert-manager.io",
    "clusterrole",
    "clusterrolebinding",
    "ingressclass",
    "priorityclass",
    "ipaddresspool.metallb.io",
    "l2advertisement.metallb.io",
    "bgppeer.metallb.io",
    "bgpadvertisement.metallb.io",
]

# NOTE ON THE FOUR metallb.io KINDS ABOVE: they are namespace-scoped in real
# MetalLB (verified against a live cluster: `kubectl api-resources
# --api-group=metallb.io -o wide` reports NAMESPACED=true for all of them,
# and the IPAddressPool actually lives in `metallb-system`). The task brief
# lists them as "cluster-scoped kinds," which is incorrect. This script does
# not special-case them or assume either scope: `capture.py` fetches every
# kind in this list with `kubectl get <kind> -A -o json`, which is a
# documented no-op for genuinely cluster-scoped kinds, and uses each
# returned item's own metadata.namespace to decide the on-disk filename
# (namespace-qualified only when the item actually has one). They still
# land under kubernetes/cluster/<kind>/ as the target layout in the spec
# shows, since that grouping (cluster-wide infra config) is a reasonable
# convenience regardless of the API's namespacing.
NAMESPACE_SCOPED_CLUSTER_KINDS_NOTE = (
    "ipaddresspool.metallb.io, l2advertisement.metallb.io, bgppeer.metallb.io, "
    "and bgpadvertisement.metallb.io are namespace-scoped APIs (normally in "
    "metallb-system), not cluster-scoped. Captured under kubernetes/cluster/ "
    "anyway as cluster-wide infra config; filenames are namespace-qualified."
)

NAMESPACED_RESOURCE_KINDS = [
    "configmap",
    "ingress",
    "networkpolicy",
    "persistentvolumeclaim",
    "service",
    "serviceaccount",
    "role",
    "rolebinding",
    "cronjob",
    "deployment",
    "statefulset",
    "daemonset",
    "horizontalpodautoscaler",
    "poddisruptionbudget",
    "certificate.cert-manager.io",
    "issuer.cert-manager.io",
]

HELMRELEASE_INTERVAL = "30m"
HELMRELEASE_KS_INTERVAL = "30m"
HELMREPOSITORY_INTERVAL = "1h"
FLUX_NAMESPACE = "flux-system"

TOOL_NAME = "k8s-backup-repo-gen"
