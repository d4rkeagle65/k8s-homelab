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

from .constants import FLUX_NAMESPACE


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


def flux_kustomization(
    *,
    name: str,
    target_namespace: str,
    path: str,
    interval: str,
    depends_on_comment: str = (
        "Not auto-detected by this script (out of scope). Fill in by hand, e.g.:\n"
        "  - name: some-other-release"
    ),
) -> dict:
    spec = CommentedMap()
    spec["interval"] = interval
    spec["path"] = path
    spec["prune"] = True
    spec["sourceRef"] = {"kind": "GitRepository", "name": FLUX_NAMESPACE}
    spec["targetNamespace"] = target_namespace
    spec["dependsOn"] = []
    spec.yaml_set_comment_before_after_key("dependsOn", before=depends_on_comment, indent=2)
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


def kustomization_file(resources: list[str]) -> dict:
    return {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": sorted(resources),
    }


def root_cluster_kustomization(
    name: str = "cluster",
    path: str = "./kubernetes/apps",
    interval: str = "10m",
    *,
    has_configmap: bool = False,
    has_secret: bool = False,
    configmap_name: str | None = None,
    secret_name: str | None = None,
) -> dict:
    """Flux Kustomization for kubernetes/apps/ (the per-app Flux
    Kustomization CRs). postBuild.substituteFrom is applied here so
    ${PLACEHOLDER} tokens in the child ks.yaml files (and, transitively,
    the HelmRelease values they build) resolve at reconciliation time.
    Both refs are optional so Flux doesn't fail before the operator has
    created the ConfigMap/Secret on the cluster.
    https://fluxcd.io/flux/components/kustomize/kustomizations/#post-build-variable-substitution
    """
    spec: dict = {
        "interval": interval,
        "path": path,
        "prune": True,
        "sourceRef": {"kind": "GitRepository", "name": FLUX_NAMESPACE},
        "wait": True,
    }
    substitute_from = []
    if has_configmap and configmap_name:
        substitute_from.append({"kind": "ConfigMap", "name": configmap_name, "optional": True})
    if has_secret and secret_name:
        substitute_from.append({"kind": "Secret", "name": secret_name, "optional": True})
    if substitute_from:
        spec["postBuild"] = {"substituteFrom": substitute_from}
    return {
        "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
        "kind": "Kustomization",
        "metadata": {
            "name": name,
            "namespace": FLUX_NAMESPACE,
        },
        "spec": spec,
    }


def cluster_resources_kustomization(
    *,
    has_configmap: bool,
    has_secret: bool,
    interval: str = "30m",
    configmap_name: str,
    secret_name: str,
) -> dict:
    """Flux Kustomization for kubernetes/cluster/ (cluster-scoped, non-Helm
    resources). Named distinctly from root_cluster_kustomization()'s
    "cluster" (which points at kubernetes/apps) to avoid a name collision
    in the flux-system namespace.

    postBuild.substituteFrom resolves any ${PLACEHOLDER} left by
    varsub.py's variable substitution -- both refs are optional so Flux
    doesn't fail reconciliation before the operator has created the
    ConfigMap/Secret on the cluster (see varsub.render_configmap/_secret).
    https://fluxcd.io/flux/components/kustomize/kustomizations/#post-build-variable-substitution
    """
    spec: dict = {
        "interval": interval,
        "path": "./kubernetes/cluster",
        "prune": True,
        "sourceRef": {"kind": "GitRepository", "name": FLUX_NAMESPACE},
    }
    substitute_from = []
    if has_configmap:
        substitute_from.append({"kind": "ConfigMap", "name": configmap_name, "optional": True})
    if has_secret:
        substitute_from.append({"kind": "Secret", "name": secret_name, "optional": True})
    if substitute_from:
        spec["postBuild"] = {"substituteFrom": substitute_from}
    return {
        "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
        "kind": "Kustomization",
        "metadata": {
            "name": "cluster-resources",
            "namespace": FLUX_NAMESPACE,
        },
        "spec": spec,
    }
