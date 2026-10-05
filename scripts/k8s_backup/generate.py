"""Generate phase: synthesize Flux CRs and kustomizations from what `capture`
already wrote to disk. Reads only the local filesystem -- no kubectl/helm
calls -- so it can be re-run offline, including after a hand edit to a
captured values.yaml (the "source of truth" values file per the task spec).

Owns: kubernetes/prod/<category>/<namespace>/<release>/app/{helmrelease.yaml,kustomization.yaml},
the releases' ks.yaml, and the kustomization.yaml of every namespace, category
and of kubernetes/prod itself (layout.py),
kubernetes/flux/meta/repositories/kustomization.yaml,
kubernetes/flux/config/cluster.yaml, kubernetes/flux/config/cluster-resources.yaml,
kubernetes/cluster/kustomization.yaml,
.sops.yaml, .gitattributes, .gitignore (lines added by hand are kept), and README.md only
when none exists yet -- an existing one is the operator's.
Release folders with a `.handwritten` marker are skipped (ownership.py).
"""

from __future__ import annotations

import re
from pathlib import Path

from . import constants, dependencies, flux, inventory, layout, promote, scaffold, yamlio
from .filetracker import FileTracker, RunReport
from .ownership import PROMOTE_MARKER, generate_owner, is_handwritten, is_promoted
from .sink import Sink


def run(root: Path, dry_run: bool, verbose: bool) -> tuple[RunReport, dict]:
    tracker = FileTracker(root, generate_owner(root), dry_run=dry_run)
    sink = Sink(root, dry_run, tracker)
    warnings: list[str] = []

    print("Generating per-release Flux scaffolding...")
    release_count = _generate_release_scaffolding(sink, warnings, verbose)

    print("Generating per-namespace and per-category kustomizations...")
    _generate_namespace_kustomizations(sink)
    _generate_env_kustomizations(sink)

    print("Generating repository and cluster kustomizations...")
    repo_count = _generate_repository_kustomization(sink)
    cluster_count = _generate_cluster_kustomization(sink)

    # Both the root `cluster` and cluster-resources Kustomizations wire
    # postBuild.substituteFrom for cluster-settings and cluster-secrets.
    # Without it on the root, ${PLACEHOLDER} tokens in the child ks.yaml
    # files are treated as literal strings at reconcile time. (Each child
    # ks.yaml wires its own for its HelmRelease -- Flux doesn't inherit
    # postBuild.)
    print("Generating root Flux Kustomization...")
    sink.write_yaml_stable(
        root / "kubernetes" / "flux" / "config" / "cluster.yaml",
        flux.root_cluster_kustomization(common_labels={layout.ENV_LABEL: layout.ENV}),
    )

    if (root / "kubernetes" / "cluster").exists():
        sink.write_yaml_stable(
            root / "kubernetes" / "flux" / "config" / "cluster-resources.yaml",
            flux.cluster_resources_kustomization(),
        )

    print("Writing top-level scaffold files...")
    prior_timestamp, prior_context = _read_prior_capture_meta(root)
    stats = _gather_stats_from_disk(root)
    gitignore_path = root / ".gitignore"
    existing_gitignore = gitignore_path.read_text(encoding="utf-8") if gitignore_path.is_file() else ""
    sink.write_text(gitignore_path, scaffold.gitignore(existing_gitignore))
    sink.write_text(root / ".gitattributes", scaffold.gitattributes())
    sink.write_text(root / ".sops.yaml", scaffold.sops_yaml_scaffold())
    if not (root / "README.md").exists():
        sink.write_text(
            root / "README.md",
            scaffold.readme(timestamp=prior_timestamp, context=prior_context, stats=stats),
        )
    (root / ".github").mkdir(parents=True, exist_ok=True)

    tracker.sweep_orphans()
    tracker.report.warnings = warnings

    return tracker.report, {
        "release_count": release_count,
        "repo_count": repo_count,
        "cluster_kustomization_entries": cluster_count,
    }


def _generate_release_scaffolding(sink: Sink, warnings, verbose) -> int:
    root = sink.root
    if not layout.env_root(root).exists():
        warnings.append(f"{layout.ENV_DIR.as_posix()} does not exist yet; run `capture` first")
        return 0
    warnings.extend(layout.check_layout(root))

    operators = dependencies.operator_kustomizations(root)
    count = 0
    for ns_dir in layout.namespace_dirs(root):
        namespace = ns_dir.name
        for release_dir in sorted(p for p in ns_dir.iterdir() if p.is_dir()):
            if is_handwritten(release_dir):
                continue
            if is_promoted(release_dir):
                if _generate_promoted_scaffolding(sink, namespace, release_dir, warnings, operators):
                    count += 1
                continue
            release_yaml = release_dir / "release.yaml"
            values_yaml = release_dir / "app" / "values.yaml"
            if not release_yaml.exists() or not values_yaml.exists():
                continue
            release_meta = yamlio.read_yaml_file(release_yaml)
            values = yamlio.read_yaml_file(values_yaml) or {}

            release_name = release_meta["release"]
            chart_name = release_meta["chart"]
            chart_version = release_meta["chartVersion"]
            chart_source = release_meta.get("chartSource", {}) or {}
            resolved = bool(chart_source.get("resolved"))

            if resolved:
                source_kind = chart_source["kind"]
                source_name = chart_source["name"]
                extra_header_lines = None
            else:
                source_kind = "HelmRepository"
                source_name = "REPLACE-ME"
                reason = flux.unresolved_source_reason(chart_name, chart_version)
                extra_header_lines = [f"WARNING: {reason}"]
                warnings.append(f"{namespace}/{release_name}: unresolved chart source, see helmrelease.yaml")

            hr_doc = flux.helm_release(
                release_name=release_name,
                namespace=namespace,
                chart_name=chart_name,
                chart_version=chart_version,
                source_kind=source_kind,
                source_name=source_name,
                values=values,
                interval=constants.HELMRELEASE_INTERVAL,
                common_labels=layout.common_labels(ns_dir, layout.MANAGED_HELM),
            )
            sink.write_yaml_stable(
                release_dir / "app" / "helmrelease.yaml", hr_doc, extra_lines=extra_header_lines
            )

            manifest_files = sorted(
                p.name
                for p in (release_dir / "app").iterdir()
                if p.suffix == ".yaml"
                and p.name not in ("kustomization.yaml", "values.yaml", "values-all.yaml")
            )
            sink.write_yaml_stable(
                release_dir / "app" / "kustomization.yaml",
                flux.kustomization_file(manifest_files),
            )

            ks_doc = flux.flux_kustomization(
                name=release_name,
                target_namespace=namespace,
                path=layout.app_path(release_dir, root),
                interval=constants.HELMRELEASE_KS_INTERVAL,
                common_labels=layout.common_labels(ns_dir, layout.MANAGED_HELM),
                depends_on=dependencies.depends_on(
                    release_name, [release_dir / "app" / f for f in manifest_files], operators
                ),
                wait=release_name in operators.values(),
            )
            sink.write_yaml_stable(release_dir / "ks.yaml", ks_doc)
            count += 1

            if verbose:
                print(f"  {namespace}/{release_name}")

    return count


def _generate_promoted_scaffolding(
    sink: Sink, namespace: str, release_dir: Path, warnings, operators: dict[str, str]
) -> bool:
    """app/kustomization.yaml and ks.yaml for a promoted release, whose
    app/ manifests capture wrote (see promote.py).
    """
    app_dir = release_dir / "app"
    manifests = sorted(p.name for p in app_dir.glob("*.yaml") if p.name != "kustomization.yaml") if app_dir.is_dir() else []
    if not manifests:
        warnings.append(f"{namespace}/{release_dir.name}: marked {PROMOTE_MARKER} but has no manifests yet; run `capture` first")
        return False
    sink.write_yaml_stable(app_dir / "kustomization.yaml", flux.kustomization_file(manifests))
    ks_doc = flux.flux_kustomization(
        name=release_dir.name,
        target_namespace=namespace,
        path=layout.app_path(release_dir, sink.root),
        interval=constants.HELMRELEASE_KS_INTERVAL,
        common_labels=layout.common_labels(release_dir.parent, layout.MANAGED_PROMOTE),
        depends_on=dependencies.depends_on(release_dir.name, [app_dir / m for m in manifests], operators),
        wait=release_dir.name in operators.values(),
        ignore=promote.FLUX_IGNORE_RULES,
    )
    sink.write_yaml_stable(release_dir / "ks.yaml", ks_doc)
    return True


def _generate_namespace_kustomizations(sink: Sink) -> None:
    for ns_dir in layout.namespace_dirs(sink.root):
        if not (ns_dir / "namespace.yaml").exists():
            continue
        resources = ["namespace.yaml"]
        for release_dir in sorted(p for p in ns_dir.iterdir() if p.is_dir()):
            if (release_dir / "ks.yaml").exists():
                resources.append(f"{release_dir.name}/ks.yaml")
        sink.write_yaml_stable(ns_dir / "kustomization.yaml", flux.kustomization_file(resources))


def _generate_env_kustomizations(sink: Sink) -> None:
    """kubernetes/prod/<category>/kustomization.yaml for each category, listing
    its namespace folders, and kubernetes/prod/kustomization.yaml, listing the
    categories: what the root `cluster` Flux Kustomization applies. A newly
    captured namespace is listed the next run, so it's deployed rather than
    sitting on disk unreferenced. The Namespace prune patch sits at the top,
    where every Namespace in the build passes through it.
    """
    base = layout.env_root(sink.root)
    if not base.exists():
        return
    categories = []
    for category in layout.CATEGORIES:
        cat_dir = base / category
        namespaces = sorted(
            d.name for d in layout.namespace_dirs(sink.root) if d.parent == cat_dir and (d / "namespace.yaml").exists()
        )
        if not namespaces:
            continue
        sink.write_yaml_stable(cat_dir / "kustomization.yaml", flux.kustomization_file(namespaces))
        categories.append(category)
    sink.write_yaml_stable(base / "kustomization.yaml", flux.kustomization_file(categories, protect_namespaces=True))


def _generate_repository_kustomization(sink: Sink) -> int:
    repo_dir = sink.root / "kubernetes" / "flux" / "meta" / "repositories"
    if not repo_dir.exists():
        return 0
    files = sorted(p.name for p in repo_dir.glob("*.yaml") if p.name != "kustomization.yaml")
    sink.write_yaml_stable(repo_dir / "kustomization.yaml", flux.kustomization_file(files))
    return len(files)


def _generate_cluster_kustomization(sink: Sink) -> int:
    cluster_dir = sink.root / "kubernetes" / "cluster"
    if not cluster_dir.exists():
        return 0
    resources = sorted(
        f"{kind_dir.name}/{f.name}"
        for kind_dir in cluster_dir.iterdir()
        if kind_dir.is_dir()
        for f in kind_dir.glob("*.yaml")
    )
    sink.write_yaml_stable(
        cluster_dir / "kustomization.yaml", flux.kustomization_file(resources, protect_namespaces=True)
    )
    return len(resources)


_INVENTORY_CAPTURED_RE = re.compile(r"^Captured:\s*(.+)$", re.MULTILINE)
_INVENTORY_CONTEXT_RE = re.compile(r"^Kube context:\s*`(.+)`$", re.MULTILINE)


def _read_prior_capture_meta(root: Path) -> tuple[str, str]:
    inv_path = root / "docs" / "inventory.md"
    if not inv_path.exists():
        return "(unknown; run `capture` first)", "(unknown)"
    text = inv_path.read_text(encoding="utf-8")
    captured = _INVENTORY_CAPTURED_RE.search(text)
    context = _INVENTORY_CONTEXT_RE.search(text)
    return (
        captured.group(1).strip() if captured else "(unknown)",
        context.group(1).strip() if context else "(unknown)",
    )


def _gather_stats_from_disk(root: Path) -> dict:
    release_count = sum(1 for d in layout.release_dirs(root) if (d / "release.yaml").exists())

    repo_dir = root / "kubernetes" / "flux" / "meta" / "repositories"
    repo_count = (
        len([p for p in repo_dir.glob("*.yaml") if p.name != "kustomization.yaml"])
        if repo_dir.exists()
        else 0
    )

    cluster_dir = root / "kubernetes" / "cluster"
    cluster_count = (
        sum(len(list(d.glob("*.yaml"))) for d in cluster_dir.iterdir() if d.is_dir())
        if cluster_dir.exists()
        else 0
    )

    local_cluster_dir = root / "kubernetes" / ".local" / "cluster"
    local_count = (
        sum(len(list(d.glob("*.yaml"))) for d in local_cluster_dir.iterdir() if d.is_dir())
        if local_cluster_dir.exists()
        else 0
    )

    raw_dir = root / "kubernetes" / "raw"
    raw_count = len(list(raw_dir.glob("*/*/*.yaml"))) if raw_dir.exists() else 0

    secrets_csv = root / "docs" / "secrets-inventory.csv"
    secret_count = 0
    if secrets_csv.exists():
        lines = secrets_csv.read_text(encoding="utf-8").splitlines()
        secret_count = max(0, len(lines) - 1)

    return {
        "release_count": release_count,
        "repo_count": repo_count,
        "cluster_resource_count": cluster_count,
        "cluster_local_only_count": local_count,
        "raw_resource_count": raw_count,
        "secret_count": secret_count,
    }
