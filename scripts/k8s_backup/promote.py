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

Secrets are promoted too, but their values never reach git: see
templated_secret().
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
_PRUNE_DISABLED_KINDS = {"PersistentVolumeClaim", "Cluster", "Secret"}

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


# ---- Secrets -----------------------------------------------------------------
#
# A promoted Secret goes to git with every value replaced by a ${VAR}
# placeholder. The value itself -- base64, exactly as the Secret stores it --
# goes into the gitignored kubernetes/.local/variable-substitutions.yaml as a
# `sensitivity: secret` entry, which capture renders into
# kubernetes/.local/cluster-substitutions-secret.yaml; once that's applied to
# the cluster, Flux's postBuild substitution puts the value back. Base64
# because it's always a plain YAML scalar: a raw value holding a newline, a
# ": " or a leading quote would corrupt the manifest Flux substitutes into.

SECRET_SEED_SOURCE = "auto-secret"

_GENERATED_SECRET_TYPES = {
    "kubernetes.io/service-account-token",
    "bootstrap.kubernetes.io/token",
    "helm.sh/release.v1",
}


def generated_secret_reason(secret: dict) -> str | None:
    """Why a Secret is recreated by something else and so isn't promoted,
    or None. (ownerReferences and Helm-managed are skipfilter's job.)
    """
    metadata = secret.get("metadata", {})
    annotations = metadata.get("annotations") or {}
    labels = metadata.get("labels") or {}
    if secret.get("type") in _GENERATED_SECRET_TYPES:
        return f"type {secret['type']}"
    if "cert-manager.io/certificate-name" in annotations:
        return "issued by cert-manager"
    if "cnpg.io/cluster" in labels:
        return "created by CloudNativePG"
    # kube-webhook-certgen (ingress-nginx's admission hook Job, among other
    # charts) writes its webhook certificate as an Opaque Secret with exactly
    # these keys and no labels, annotations or owner to say so.
    if secret.get("type") == "Opaque" and set(secret.get("data") or {}) == {"ca", "cert", "key"}:
        return "webhook certificate generated by kube-webhook-certgen"
    return None


def secret_placeholder(namespace: str, name: str, key: str) -> str:
    """`${NOIP_DUC_CREDENTIALS_NOIP_PASSWORD}` for key NOIP_PASSWORD of
    noip-duc/noip-duc-credentials: the namespace is left out when the Secret
    name already starts with it. Always a valid Flux variable name.
    """
    base = name if name.startswith(namespace) else f"{namespace}-{name}"
    var = re.sub(r"[^A-Za-z0-9]", "_", f"{base}_{key}").upper()
    if var[0].isdigit():
        var = f"_{var}"
    return f"${{{var}}}"


def templated_secret(secret: dict) -> tuple[dict, dict[str, str]]:
    """(manifest, {placeholder: base64 value}) for an already-neat()ed
    Secret. An empty value is written as-is; it holds nothing to hide.
    """
    metadata = secret.get("metadata", {})
    namespace, name = metadata.get("namespace", ""), metadata["name"]
    values: dict[str, str] = {}
    data: dict[str, str] = {}
    for key, value in sorted((secret.get("data") or {}).items()):
        if not value:
            data[key] = ""
            continue
        placeholder = secret_placeholder(namespace, name, key)
        data[key] = placeholder
        values[placeholder] = value

    out_meta: dict = {"name": name, "namespace": namespace}
    if metadata.get("labels"):
        out_meta["labels"] = dict(metadata["labels"])
    annotations = dict(metadata.get("annotations") or {})
    annotations[PRUNE_ANNOTATION] = "disabled"
    out_meta["annotations"] = annotations

    manifest = {"apiVersion": "v1", "kind": "Secret", "metadata": out_meta, "type": secret.get("type", "Opaque")}
    if secret.get("immutable"):
        manifest["immutable"] = True
    manifest["data"] = data
    return manifest, values
