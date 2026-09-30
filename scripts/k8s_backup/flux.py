"""Builders for Flux custom resources and Kustomize kustomizations.

apiVersions verified against Flux's own docs (fetched 2026-09-12):
- HelmRelease:                helm.toolkit.fluxcd.io/v2
  https://fluxcd.io/flux/components/helm/helmreleases/
- Kustomization (kustomize-controller): kustomize.toolkit.fluxcd.io/v1
  https://fluxcd.io/flux/components/kustomize/kustomizations/
- HelmRepository / OCIRepository:       source.toolkit.fluxcd.io/v1
  https://fluxcd.io/flux/components/source/ocirepositories/
"""

from __future__ import annotations

from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import LiteralScalarString

from .constants import FLUX_NAMESPACE, SECRETS_SECRET_NAME, SETTINGS_CONFIGMAP_NAME


def unresolved_source_reason(chart_name: str, chart_version: str) -> str:
    return (
        f"chart '{chart_name}' version '{chart_version}' was not found in any repo from "
        "`helm repo list` via `helm search repo`. Helm does not persist the source repo/URL "
        "used at install time anywhere in the release object, so this can't be inferred "
        "automatically. Confirm the source repo by hand, `helm repo add` it if needed, and "
        "replace spec.chart.spec.sourceRef below."
    )


def helm_release(
    *,
    release_name: str,
    namespace: str,
    chart_name: str,
    chart_version: str,
    source_kind: str,
    source_name: str,
    values: dict,
    interval: str,
) -> dict:
    return {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {
            "name": release_name,
            "namespace": namespace,
        },
        "spec": {
            "interval": interval,
            "chart": {
                "spec": {
                    "chart": chart_name,
                    "version": chart_version,
                    "sourceRef": {
                        "kind": source_kind,
                        "name": source_name,
                        # The HelmRepository/OCIRepository lives in
                        # flux-system while the HelmRelease lives in the
                        # app namespace, so this is a cross-namespace
                        # reference. Flux allows this by default; if the
                        # eventual Flux install has cross-namespace refs
                        # disabled (--no-cross-namespace-refs on
                        # helm-controller), this field must be dropped and
                        # a same-namespace source used instead.
                        "namespace": FLUX_NAMESPACE,
                    },
                }
            },
            "values": values if values else {},
        },
    }


def substitute_from() -> list[dict]:
    """postBuild.substituteFrom entries: the plain settings committed in git,
    and the private values External Secrets builds from Vaultwarden (see
    varsub.py). Optional, so a Kustomization doesn't hard-fail if one is
    briefly missing (a new cluster, before External Secrets has synced).
    https://fluxcd.io/flux/components/kustomize/kustomizations/#post-build-variable-substitution
    """
    return [
        {"kind": "ConfigMap", "name": SETTINGS_CONFIGMAP_NAME, "optional": True},
        {"kind": "Secret", "name": SECRETS_SECRET_NAME, "optional": True},
    ]


def flux_kustomization(
    *,
    name: str,
    target_namespace: str,
    path: str,
    interval: str,
    depends_on: list[dict] | None = None,
    wait: bool = False,
    depends_on_comment: str = (
        "Set by generate: the operators whose APIs this app's manifests use\n"
        "(scripts/k8s_backup/dependencies.py)."
    ),
) -> dict:
    spec = CommentedMap()
    spec["interval"] = interval
    spec["path"] = path
    spec["prune"] = True
    spec["sourceRef"] = {"kind": "GitRepository", "name": FLUX_NAMESPACE}
    spec["targetNamespace"] = target_namespace
    if wait:
        # Without it this Kustomization is Ready as soon as its HelmRelease
        # object is applied, before Helm has installed anything, so a
        # dependsOn on it wouldn't wait for the operator's CRDs.
        spec["wait"] = True
    spec["dependsOn"] = depends_on or []
    spec.yaml_set_comment_before_after_key("dependsOn", before=depends_on_comment, indent=2)
    # Always wired, not only when a local substitutions file exists: Flux
    # does not inherit postBuild from the parent `cluster` Kustomization, so
    # without this every ${PLACEHOLDER} in this release's HelmRelease values
    # reaches the cluster as a literal string. The refs are optional, so
    # this is harmless on a cluster with no substitutions at all.
    spec["postBuild"] = {"substituteFrom": substitute_from()}
    return {
        "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
        "kind": "Kustomization",
        "metadata": {
            "name": name,
            "namespace": FLUX_NAMESPACE,
        },
        "spec": spec,
    }


def helm_repository(*, name: str, url: str, interval: str) -> dict:
    return {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "HelmRepository",
        "metadata": {"name": name, "namespace": FLUX_NAMESPACE},
        "spec": {"interval": interval, "url": url},
    }


def oci_repository(*, name: str, url: str, interval: str, tag: str | None = None) -> dict:
    spec = {"interval": interval, "url": url}
    if tag:
        spec["ref"] = {"tag": tag}
    return {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "OCIRepository",
        "metadata": {"name": name, "namespace": FLUX_NAMESPACE},
        "spec": spec,
    }


def kustomization_file(resources: list[str], *, protect_namespaces: bool = False) -> dict:
    doc = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": sorted(resources),
    }
    if protect_namespaces:
        doc["patches"] = [NAMESPACE_PRUNE_PATCH]
    return doc


# Deleting a Namespace deletes everything in it, including the PVCs, database
# Clusters and Secrets whose own `prune: disabled` would otherwise keep them.
# So Flux never prunes a Namespace; one that's no longer wanted is deleted by
# hand. With a target, kustomize applies the patch to every Namespace in the
# build and ignores the name in it.
# https://fluxcd.io/flux/components/kustomize/kustomizations/#prune
NAMESPACE_PRUNE_PATCH = {
    "target": {"kind": "Namespace"},
    "patch": LiteralScalarString(
        "apiVersion: v1\n"
        "kind: Namespace\n"
        "metadata:\n"
        "  name: any\n"
        "  annotations:\n"
        "    kustomize.toolkit.fluxcd.io/prune: disabled\n"
    ),
}


def root_cluster_kustomization(
    name: str = "cluster",
    path: str = "./kubernetes/apps",
    interval: str = "10m",
) -> dict:
    """Flux Kustomization for kubernetes/apps/ (the per-app Flux
    Kustomization CRs). postBuild.substituteFrom is applied here so
    ${PLACEHOLDER} tokens in the child ks.yaml files themselves resolve at
    reconciliation time. It does NOT reach the HelmReleases those children
    build -- Flux doesn't inherit postBuild -- which is why
    flux_kustomization() wires its own.
    The refs are the same as flux_kustomization()'s (substitute_from()).
    https://fluxcd.io/flux/components/kustomize/kustomizations/#post-build-variable-substitution
    """
    spec: dict = {
        "interval": interval,
        "path": path,
        "prune": True,
        "sourceRef": {"kind": "GitRepository", "name": FLUX_NAMESPACE},
        "wait": True,
    }
    refs = substitute_from()
    if refs:
        spec["postBuild"] = {"substituteFrom": refs}
    return {
        "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
        "kind": "Kustomization",
        "metadata": {
            "name": name,
            "namespace": FLUX_NAMESPACE,
        },
        "spec": spec,
    }


def cluster_resources_kustomization(*, interval: str = "30m") -> dict:
    """Flux Kustomization for kubernetes/cluster/ (cluster-scoped, non-Helm
    resources). Named distinctly from root_cluster_kustomization()'s
    "cluster" (which points at kubernetes/apps) to avoid a name collision
    in the flux-system namespace.

    postBuild.substituteFrom resolves any ${PLACEHOLDER} left by
    varsub.py's variable substitution (substitute_from()).
    https://fluxcd.io/flux/components/kustomize/kustomizations/#post-build-variable-substitution
    """
    spec: dict = {
        "interval": interval,
        "path": "./kubernetes/cluster",
        "prune": True,
        "sourceRef": {"kind": "GitRepository", "name": FLUX_NAMESPACE},
    }
    refs = substitute_from()
    if refs:
        spec["postBuild"] = {"substituteFrom": refs}
    return {
        "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
        "kind": "Kustomization",
        "metadata": {
            "name": "cluster-resources",
            "namespace": FLUX_NAMESPACE,
        },
        "spec": spec,
    }
