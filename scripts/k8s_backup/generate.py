"""Generate phase: synthesize Flux CRs and kustomizations from what `capture`
already wrote to disk. Reads only the local filesystem -- no kubectl/helm
calls -- so it can be re-run offline, including after a hand edit to a
captured values.yaml (the "source of truth" values file per the task spec).

Owns: kubernetes/apps/*/*/app/{helmrelease.yaml,kustomization.yaml},
kubernetes/apps/*/*/ks.yaml, kubernetes/apps/*/kustomization.yaml,
kubernetes/flux/meta/repositories/kustomization.yaml,
kubernetes/flux/config/cluster.yaml, kubernetes/flux/config/cluster-resources.yaml,
kubernetes/cluster/kustomization.yaml, docs/variable-substitutions.md,
.sops.yaml, .gitignore, README.md.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import stat
from pathlib import Path

from . import constants, flux, inventory, scaffold, varsub, yamlio
from .filetracker import FileTracker, RunReport
from .ownership import generate_owns
from .sink import Sink


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(root: Path, dry_run: bool, verbose: bool) -> tuple[RunReport, dict]:
    tracker = FileTracker(root, generate_owns, dry_run=dry_run)
    sink = Sink(root, dry_run, tracker)
    timestamp = _now_iso()
    warnings: list[str] = []

    print("Generating per-release Flux scaffolding...")
    release_count = _generate_release_scaffolding(sink, timestamp, warnings, verbose)

    print("Generating per-namespace kustomizations...")
    _generate_namespace_kustomizations(sink, timestamp)

    print("Generating repository and cluster kustomizations...")
    repo_count = _generate_repository_kustomization(sink, timestamp)
    cluster_count = _generate_cluster_kustomization(sink, timestamp)

    # Load substitutions once so both the apps and cluster-resources
    # Kustomizations can wire postBuild.substituteFrom for the same
    # ConfigMap/Secret. Without this on the apps root, ${PLACEHOLDER}
    # tokens in child ks.yaml files (and their HelmReleases) are treated
    # as literal strings at reconcile time.
    substitutions = varsub.load_substitutions(root)
    has_configmap = varsub.render_configmap(substitutions) is not None
    has_secret = varsub.render_secret(substitutions) is not None

    print("Generating root Flux Kustomization...")
    sink.write_yaml_stable(
        root / "kubernetes" / "flux" / "config" / "cluster.yaml",
        flux.root_cluster_kustomization(
            has_configmap=has_configmap,
            has_secret=has_secret,
            configmap_name=varsub.SUBSTITUTIONS_CONFIGMAP_NAME,
            secret_name=varsub.SUBSTITUTIONS_SECRET_NAME,
        ),
        timestamp,
    )

    if (root / "kubernetes" / "cluster").exists():
        sink.write_yaml_stable(
            root / "kubernetes" / "flux" / "config" / "cluster-resources.yaml",
            flux.cluster_resources_kustomization(
                has_configmap=has_configmap,
                has_secret=has_secret,
                configmap_name=varsub.SUBSTITUTIONS_CONFIGMAP_NAME,
                secret_name=varsub.SUBSTITUTIONS_SECRET_NAME,
            ),
            timestamp,
        )
        # Defense-in-depth: this doc is built FROM `substitutions`, so it
        # shouldn't be able to leak a literal value in the first place, but
        # a future note field embedding one (as BASE_DOMAIN's briefly did)
        # shouldn't reach this git-tracked file either way.
        doc = varsub.apply_substitutions_text(
            inventory.build_variable_substitutions_md(substitutions), substitutions
        )
        sink.write_text(root / "docs" / "variable-substitutions.md", doc)

    print("Writing top-level scaffold files...")
    prior_timestamp, prior_context = _read_prior_capture_meta(root)
    stats = _gather_stats_from_disk(root)
    sink.write_text(root / ".gitignore", scaffold.gitignore())
    sink.write_text(root / ".gitattributes", scaffold.gitattributes())
    hook_path = sink.write_text(root / ".githooks" / "pre-commit", scaffold.pre_commit_hook())
    if not dry_run:
        # Git on Linux/macOS requires the executable bit to run a hook file
        # directly; harmless on Windows, which doesn't track this bit.
        current_mode = os.stat(hook_path).st_mode
        os.chmod(hook_path, current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    sink.write_text(root / ".sops.yaml", scaffold.sops_yaml_scaffold())
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


def _generate_release_scaffolding(sink: Sink, timestamp, warnings, verbose) -> int:
    apps_root = sink.root / "kubernetes" / "apps"
    if not apps_root.exists():
        warnings.append("kubernetes/apps does not exist yet; run `capture` first")
        return 0

    count = 0
    for ns_dir in sorted(p for p in apps_root.iterdir() if p.is_dir()):
        namespace = ns_dir.name
        for release_dir in sorted(p for p in ns_dir.iterdir() if p.is_dir()):
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
            )
            sink.write_yaml_stable(
                release_dir / "app" / "helmrelease.yaml", hr_doc, timestamp, extra_lines=extra_header_lines
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
                timestamp,
            )

            ks_doc = flux.flux_kustomization(
                name=release_name,
                target_namespace=namespace,
                path=f"./kubernetes/apps/{namespace}/{release_name}/app",
                interval=constants.HELMRELEASE_KS_INTERVAL,
            )
            sink.write_yaml_stable(release_dir / "ks.yaml", ks_doc, timestamp)
            count += 1

            if verbose:
                print(f"  {namespace}/{release_name}")

    return count


def _generate_namespace_kustomizations(sink: Sink, timestamp: str) -> None:
    apps_root = sink.root / "kubernetes" / "apps"
    if not apps_root.exists():
        return
    for ns_dir in sorted(p for p in apps_root.iterdir() if p.is_dir()):
        if not (ns_dir / "namespace.yaml").exists():
            continue
        resources = ["namespace.yaml"]
        for release_dir in sorted(p for p in ns_dir.iterdir() if p.is_dir()):
            if (release_dir / "ks.yaml").exists():
                resources.append(f"{release_dir.name}/ks.yaml")
        sink.write_yaml_stable(ns_dir / "kustomization.yaml", flux.kustomization_file(resources), timestamp)


def _generate_repository_kustomization(sink: Sink, timestamp: str) -> int:
    repo_dir = sink.root / "kubernetes" / "flux" / "meta" / "repositories"
    if not repo_dir.exists():
        return 0
    files = sorted(p.name for p in repo_dir.glob("*.yaml") if p.name != "kustomization.yaml")
    sink.write_yaml_stable(repo_dir / "kustomization.yaml", flux.kustomization_file(files), timestamp)
    return len(files)


def _generate_cluster_kustomization(sink: Sink, timestamp: str) -> int:
    cluster_dir = sink.root / "kubernetes" / "cluster"
    if not cluster_dir.exists():
        return 0
    resources = sorted(
        f"{kind_dir.name}/{f.name}"
        for kind_dir in cluster_dir.iterdir()
        if kind_dir.is_dir()
        for f in kind_dir.glob("*.yaml")
    )
    sink.write_yaml_stable(cluster_dir / "kustomization.yaml", flux.kustomization_file(resources), timestamp)
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
    apps_root = root / "kubernetes" / "apps"
    release_count = len(list(apps_root.glob("*/*/release.yaml"))) if apps_root.exists() else 0

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
