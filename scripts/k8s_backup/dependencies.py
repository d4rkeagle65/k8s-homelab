"""Infer spec.dependsOn for the per-app Flux Kustomizations (ks.yaml).

An app depends on an operator when one of the manifests its Kustomization
applies uses an API the operator installs, e.g. a CloudNativePG `Cluster`
(postgresql.cnpg.io) or a cert-manager `Certificate` (cert-manager.io).
Without the dependency, Flux applies the app before the operator's CRDs
exist, the dry-run fails, and it only converges by retrying.

Only objects in the app's own manifests count. Objects a HelmRelease renders
in-cluster can't be seen offline, and an annotation such as
`cert-manager.io/cluster-issuer` on an Ingress isn't one: the Ingress applies
fine without cert-manager, which issues the certificate once it's running.

The operator's Kustomization is found by chart name, so the dependency is
only added when that operator is itself in the repo; a Flux Kustomization
that depends on one that doesn't exist never becomes Ready. generate also
sets `wait: true` on each operator's own Kustomization: without it, Flux
marks that Kustomization Ready once the HelmRelease object is applied, not
once Helm has installed the CRDs.
"""

from __future__ import annotations

from pathlib import Path

from . import layout, yamlio
from .ownership import is_handwritten

# API group -> the Helm chart that installs its CRDs. A group also matches
# its subgroups (acme.cert-manager.io is cert-manager's too).
OPERATOR_CHARTS = {
    "postgresql.cnpg.io": "cloudnative-pg",
    "cert-manager.io": "cert-manager",
    "metallb.io": "metallb",
    "longhorn.io": "longhorn",
    "external-secrets.io": "external-secrets",
    "traefik.io": "traefik",
}


def _release_charts(release_dir: Path) -> set[str]:
    """Charts a release folder installs: from release.yaml for a captured
    release, else from a hand-written app/helmrelease.yaml.
    """
    release_yaml = release_dir / "release.yaml"
    if release_yaml.is_file():
        meta = yamlio.read_yaml_file(release_yaml) or {}
        return {meta["chart"]} if meta.get("chart") else set()
    helmrelease = release_dir / "app" / "helmrelease.yaml"
    if not (is_handwritten(release_dir) and helmrelease.is_file()):
        return set()
    charts = set()
    for doc in yamlio.read_yaml_documents(helmrelease):
        if isinstance(doc, dict) and doc.get("kind") == "HelmRelease":
            chart = (((doc.get("spec") or {}).get("chart") or {}).get("spec") or {}).get("chart")
            if chart:
                charts.add(chart)
    return charts


def operator_kustomizations(root: Path) -> dict[str, str]:
    """Chart name -> the Flux Kustomization (release folder name) that
    installs it. Only charts listed in OPERATOR_CHARTS are looked up; if two
    folders install the same chart, the first in sorted order wins.
    """
    wanted = set(OPERATOR_CHARTS.values())
    found: dict[str, str] = {}
    for release_dir in layout.release_dirs(root):
        for chart in sorted(_release_charts(release_dir) & wanted):
            found.setdefault(chart, release_dir.name)
    return found


def _operator_chart(api_version: str) -> str | None:
    group = api_version.rpartition("/")[0] if "/" in api_version else ""
    for operator_group, chart in OPERATOR_CHARTS.items():
        if group == operator_group or group.endswith("." + operator_group):
            return chart
    return None


def depends_on(name: str, manifests: list[Path], operators: dict[str, str]) -> list[dict]:
    """dependsOn entries for the Kustomization `name` that applies
    `manifests`, sorted by name. Never lists `name` itself.
    """
    names = set()
    for path in manifests:
        for doc in yamlio.read_yaml_documents(path):
            if not isinstance(doc, dict):
                continue
            chart = _operator_chart(str(doc.get("apiVersion") or ""))
            if chart and chart in operators and operators[chart] != name:
                names.add(operators[chart])
    return [{"name": n} for n in sorted(names)]
