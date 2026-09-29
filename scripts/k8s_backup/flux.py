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


def substitute_from(configmap_name: str | None, secret_name: str | None) -> list[dict]:
    """postBuild.substituteFrom entries. Both optional so Flux doesn't fail
    before the operator has created the ConfigMap/Secret on the cluster.
    """
    refs = []
    if configmap_name:
        refs.append({"kind": "ConfigMap", "name": configmap_name, "optional": True})
    if secret_name:
        refs.append({"kind": "Secret", "name": secret_name, "optional": True})
    return refs


def flux_kustomization(
    *,
    name: str,
    target_namespace: str,
    path: str,
    interval: str,
    configmap_name: str,
    secret_name: str,
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
    spec["postBuild"] = {"substituteFrom": substitute_from(configmap_name, secret_name)}
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
    configmap_name: str | None = None,
    secret_name: str | None = None,
) -> dict:
    """Flux Kustomization for kubernetes/apps/ (the per-app Flux
    Kustomization CRs). postBuild.substituteFrom is applied here so
    ${PLACEHOLDER} tokens in the child ks.yaml files themselves resolve at
    reconciliation time. It does NOT reach the HelmReleases those children
    build -- Flux doesn't inherit postBuild -- which is why
    flux_kustomization() wires its own.
    Both refs are optional so Flux doesn't fail before the operator has
    created the ConfigMap/Secret on the cluster. Like flux_kustomization(),
    they're wired whether or not a local substitutions file exists, so a
    clone without kubernetes/.local/ generates the same file.
    https://fluxcd.io/flux/components/kustomize/kustomizations/#post-build-variable-substitution
    """
    spec: dict = {
        "interval": interval,
        "path": path,
        "prune": True,
        "sourceRef": {"kind": "GitRepository", "name": FLUX_NAMESPACE},
        "wait": True,
    }
    refs = substitute_from(configmap_name, secret_name)
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


def cluster_resources_kustomization(
    *,
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
    Always wired, as in root_cluster_kustomization(): dropping them in a
    clone without kubernetes/.local/ would blank every ${PLACEHOLDER} here.
    https://fluxcd.io/flux/components/kustomize/kustomizations/#post-build-variable-substitution
    """
    spec: dict = {
        "interval": interval,
        "path": "./kubernetes/cluster",
        "prune": True,
        "sourceRef": {"kind": "GitRepository", "name": FLUX_NAMESPACE},
    }
    refs = substitute_from(configmap_name, secret_name)
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
