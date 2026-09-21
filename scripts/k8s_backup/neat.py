"""In-script equivalent of `kubectl neat`'s runtime-field cleanup.

Neither `kubectl-neat` nor krew is installed on the operator's machine
(verified: `kubectl neat --help` -> "unknown command", `kubectl krew list`
-> "unknown command"). kubectl-neat's own README describes its cleanup only
in general terms (removes the status subresource, metadata "clutter" like
resourceVersion/uid, and kind-specific default values such as a
scheduler-assigned spec.nodeName) and does not publish an exact field list;
the project is also marked unmaintained upstream. Rather than take a
dependency on a plugin the operator would have to install and whose exact
behavior isn't pinned down, this reimplements the well-documented, kind-
independent subset of that cleanup directly: the status subresource and the
metadata bookkeeping fields the API server fills in itself. It deliberately
does NOT attempt kubectl-neat's kind-specific default-value removal (e.g.
stripping an implicit default ServiceAccount token volume); the task spec's
own filter logic (constants.py) already excludes anything Helm-managed or
owned by a parent object, which is where most of that noise would come
from anyway.
"""

from __future__ import annotations

import copy

_RUNTIME_METADATA_FIELDS = (
    "resourceVersion",
    "uid",
    "generation",
    "creationTimestamp",
    "managedFields",
    "selfLink",
)

_RUNTIME_ANNOTATIONS = (
    "kubectl.kubernetes.io/last-applied-configuration",
)


def neat(obj: dict) -> dict:
    """Return a cleaned deep copy of a single Kubernetes manifest."""
    obj = copy.deepcopy(obj)
    obj.pop("status", None)

    metadata = obj.get("metadata")
    if isinstance(metadata, dict):
        for field in _RUNTIME_METADATA_FIELDS:
            metadata.pop(field, None)
        annotations = metadata.get("annotations")
        if isinstance(annotations, dict):
            for key in _RUNTIME_ANNOTATIONS:
                annotations.pop(key, None)
            if not annotations:
                metadata.pop("annotations", None)

        # Every Namespace gets this label auto-added by the API server
        # (the NamespaceDefaultLabelName feature, stable since 1.21) -- it's
        # never something a user set, so it's pure noise in a backup.
        if obj.get("kind") == "Namespace":
            labels = metadata.get("labels")
            if isinstance(labels, dict):
                labels.pop("kubernetes.io/metadata.name", None)
                if not labels:
                    metadata.pop("labels", None)

    # A hand-authored static PersistentVolume's claimRef expresses binding
    # *intent* (name/namespace of the PVC it should bind to) -- keep that.
    # resourceVersion/uid are a snapshot of one specific live binding and
    # are meaningless (and immediately stale) outside that exact moment.
    if obj.get("kind") == "PersistentVolume":
        claim_ref = obj.get("spec", {}).get("claimRef")
        if isinstance(claim_ref, dict):
            claim_ref.pop("resourceVersion", None)
            claim_ref.pop("uid", None)

    return obj
