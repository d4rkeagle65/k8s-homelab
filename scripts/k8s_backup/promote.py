"""Promotion: turning a namespace's captured non-Helm resources into a
Flux-managed release instead of a gitignored kubernetes/raw/ dump.

Opt-in per app with a `.promote` marker in kubernetes/apps/<ns>/<app>/.
capture then writes every resource it keeps from namespace <ns> into that
folder's app/, and generate adds app/kustomization.yaml and ks.yaml, the
same as for a captured Helm release. From then on each capture refreshes
the manifests from the live cluster, and Flux applies them.

Raw capture is a full-fidelity backup; a promoted manifest has to be
re-appliable instead, both over the live object (Flux adopts it) and onto
a rebuilt cluster. clean_for_gitops() strips what the cluster assigned on
its own and that would break or pin a rebuild: a Service's allocated
cluster IP and node ports, a PVC's binding to one specific dynamically
provisioned PV, controller finalizers and bookkeeping annotations.
"""

from __future__ import annotations

import copy
import re

from .paths import sanitize_filename
from .skipfilter import DYNAMIC_PV_NAME_RE

# Flux never deletes an object carrying this, even if its manifest
# disappears from git -- for the kinds whose deletion loses data.
# https://fluxcd.io/flux/components/kustomize/kustomizations/#prune
PRUNE_ANNOTATION = "kustomize.toolkit.fluxcd.io/prune"
_PRUNE_DISABLED_KINDS = {"PersistentVolumeClaim", "Cluster"}

_BOOKKEEPING_ANNOTATIONS = (
    "deployment.kubernetes.io/revision",
    "pv.kubernetes.io/bind-completed",
    "pv.kubernetes.io/bound-by-controller",
    "volume.beta.kubernetes.io/storage-provisioner",
    "volume.kubernetes.io/storage-provisioner",
    "volume.kubernetes.io/selected-node",
)


def manifest_filename(kind: str, name: str) -> str:
    """app/ filename for a promoted resource: `<kind>-<name>.yaml`. `kind`
    is the capture kind string (e.g. "cluster.postgresql.cnpg.io"), so the
    prefix is predictable even when listing that kind failed and there's
    no object to read its kind from.
    """
    return f"{kind_prefix(kind)}-{sanitize_filename(name)}.yaml"


def kind_prefix(kind: str) -> str:
    return kind.split(".", 1)[0].lower()


def statefulset_claim_names(statefulsets: list[dict]) -> list[re.Pattern]:
    """PVCs a StatefulSet creates from its volumeClaimTemplates are named
    <template>-<statefulset>-<ordinal>. The StatefulSet recreates them, so
    promoting them separately would only duplicate its declaration.
    """
    patterns = []
    for sts in statefulsets:
        sts_name = sts.get("metadata", {}).get("name", "")
        for template in sts.get("spec", {}).get("volumeClaimTemplates") or []:
            template_name = template.get("metadata", {}).get("name", "")
            patterns.append(re.compile(rf"^{re.escape(template_name)}-{re.escape(sts_name)}-\d+$"))
    return patterns


def storage_classes_used(obj: dict) -> list[str]:
    """StorageClass names a manifest requests storage from: a PVC, a
    StatefulSet's volumeClaimTemplates, a CloudNativePG Cluster's data and
    WAL volumes.
    """
    kind, spec = obj.get("kind"), obj.get("spec") or {}
    if kind == "PersistentVolumeClaim":
        names = [spec.get("storageClassName")]
    elif kind == "StatefulSet":
        names = [(t.get("spec") or {}).get("storageClassName") for t in spec.get("volumeClaimTemplates") or []]
    elif kind == "Cluster":
        names = [(spec.get("storage") or {}).get("storageClass"), (spec.get("walStorage") or {}).get("storageClass")]
    else:
        names = []
    return [n for n in names if n]


_VAR_REF = re.compile(r"(?<!\$)\$\{([^}]*)\}")


def escape_foreign_variables(obj, known: set[str]):
    """Escape every `${NAME}` whose NAME isn't a substitution variable as
    `$${NAME}`, which Flux's postBuild substitution turns back into a
    literal `${NAME}`. Unescaped, Flux would replace it -- an undefined
    variable with an empty string -- silently corrupting e.g. a shell script
    or config file carried in a ConfigMap. Returns the escaped copy.
    """
    if isinstance(obj, dict):
        return {k: escape_foreign_variables(v, known) for k, v in obj.items()}
    if isinstance(obj, list):
        return [escape_foreign_variables(v, known) for v in obj]
    if isinstance(obj, str) and "${" in obj:
        return _VAR_REF.sub(lambda m: m.group(0) if m.group(1) in known else "$" + m.group(0), obj)
    return obj


def _drop(mapping: dict | None, keys) -> None:
    if isinstance(mapping, dict):
        for key in keys:
            mapping.pop(key, None)


def _drop_empty(parent: dict, key: str) -> None:
    if isinstance(parent, dict) and not parent.get(key):
        parent.pop(key, None)


def clean_for_gitops(obj: dict) -> dict:
    """A deep copy of an already-neat()ed manifest, made re-appliable."""
    obj = copy.deepcopy(obj)
    kind = obj.get("kind")
    metadata = obj.setdefault("metadata", {})
    spec = obj.get("spec") if isinstance(obj.get("spec"), dict) else {}

    metadata.pop("finalizers", None)
    _drop(metadata.get("annotations"), _BOOKKEEPING_ANNOTATIONS)
    _drop_empty(metadata, "annotations")

    # Nothing else in the pod template is touched -- in particular not the
    # kubectl.kubernetes.io/restartedAt left by `kubectl rollout restart`.
    # When Flux adopts an object it drops kubectl's field ownership, so any
    # template field missing from the manifest is deleted, and deleting it
    # changes the template: a rollout of every pod.
    template_meta = (spec.get("template") or {}).get("metadata")
    if isinstance(template_meta, dict):
        template_meta.pop("creationTimestamp", None)

    if kind == "StatefulSet":
        for template in spec.get("volumeClaimTemplates") or []:
            template.pop("status", None)
            _drop(template.get("metadata"), ("creationTimestamp",))

    if kind == "Service":
        if spec.get("clusterIP") != "None":  # headless: None is declared, not assigned
            spec.pop("clusterIP", None)
            spec.pop("clusterIPs", None)
        spec.pop("healthCheckNodePort", None)
        for port in spec.get("ports") or []:
            port.pop("nodePort", None)

    if kind == "PersistentVolumeClaim":
        # A static PV's name is binding intent; a dynamic one won't exist
        # on a rebuilt cluster and would leave the claim Pending forever.
        if DYNAMIC_PV_NAME_RE.match(spec.get("volumeName") or ""):
            spec.pop("volumeName", None)

    if kind in _PRUNE_DISABLED_KINDS:
        metadata.setdefault("annotations", {})[PRUNE_ANNOTATION] = "disabled"

    return obj
