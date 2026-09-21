"""Capture phase: every kubectl/helm read, writing backup data to the output tree.

Owns: docs/*, kubernetes/raw/**, kubernetes/cluster/<kind>/*.yaml,
kubernetes/apps/<ns>/namespace.yaml, kubernetes/apps/<ns>/<release>/release.yaml,
kubernetes/apps/<ns>/<release>/app/values*.yaml, kubernetes/flux/meta/repositories/<repo>.yaml.

Does not write any Flux HelmRelease/Kustomization CR or kustomization.yaml --
that synthesis is generate.py's job, and it reads what this module wrote
rather than being handed it in memory, so `generate` can be re-run offline
after a hand edit to a captured values.yaml.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from . import constants, flux, headers, helmcli, inventory, kube, secretscan, skipfilter, varsub, yamlio
from .filetracker import FileTracker, RunReport
from .neat import neat
from .ownership import capture_owns
from .paths import sanitize_filename
from .sink import Sink


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_nl(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _neat_and_redact(
    item: dict,
    context_label: str,
    redaction_log: list[str],
    verbose: bool,
    substitutions: list[dict] | None = None,
) -> dict:
    """Clean runtime fields, then redact anything that looks like a
    credential BEFORE it's ever written to disk. This is the primary
    defense; the pytest suite in scripts/tests/ and the pre-commit hook
    are a second, independent gate in case something slips past this one
    (e.g. a resource kind added later without going through this path).

    `substitutions`, when given, additionally replaces operator-listed
    environment-specific literals (an internal hostname, an account email)
    with a placeholder -- only ever passed for git-tracked output; the
    kubernetes/.local/ copy stays full-fidelity. See varsub.py.
    """
    cleaned = neat(item)
    redactions = secretscan.redact_credentials(cleaned)
    for r in redactions:
        entry = f"{context_label}: {r.path} ({r.reason})"
        redaction_log.append(entry)
        if verbose:
            print(f"  [REDACTED] {entry}", file=sys.stderr)
    if substitutions:
        varsub.apply_substitutions(cleaned, substitutions)
    return cleaned


def run(root: Path, context: str | None, dry_run: bool, verbose: bool) -> tuple[RunReport, dict]:
    tracker = FileTracker(root, capture_owns, dry_run=dry_run)
    sink = Sink(root, dry_run, tracker)
    timestamp = _now_iso()
    warnings: list[str] = []
    redactions: list[str] = []
    resolved_context = context or kube.current_context()

    varsub.ensure_example_scaffold(root, dry_run)

    # Auto-seed from a quick pre-fetch of just the kinds it looks at, so a
    # literal first appearing on THIS run's cluster state still gets
    # substituted in this same run's output rather than only the next one.
    # (Costs three extra `kubectl get` calls; _capture_cluster_resources and
    # _capture_namespaced_resources below fetch these kinds again as part
    # of their normal sweep. Ingress is fetched unfiltered here -- including
    # Helm-managed ones that never reach kubernetes/raw/ -- because a
    # dominant domain used by e.g. immich's Helm-templated Ingress is just
    # as much "your domain" as one from a hand-authored Ingress.)
    preseed_raw = {
        "clusterissuer.cert-manager.io": kube.get_all(context, "clusterissuer.cert-manager.io")[0],
        "persistentvolume": kube.get_all(context, "persistentvolume")[0],
    }
    preseed_ingress = kube.get_all(context, "ingress")[0]
    for desc in varsub.auto_seed(root, preseed_raw, preseed_ingress, dry_run):
        warnings.append(f"auto-added variable substitution: {desc}")
        if verbose:
            print(f"  [auto-seed] {desc}", file=sys.stderr)

    substitutions = varsub.load_substitutions(root)
    if substitutions:
        print(f"Loaded {len(substitutions)} variable substitution(s) from kubernetes/.local/variable-substitutions.yaml")

    print("Capturing Helm releases and values...")
    helm_repos = helmcli.repo_list(context)
    release_infos = _capture_helm_releases(
        sink, context, helm_repos, timestamp, warnings, redactions, verbose, substitutions
    )

    print("Capturing Helm repositories...")
    repo_names = _write_helm_repository_files(sink, helm_repos, timestamp)

    print("Capturing cluster-scoped resources...")
    cluster_counts, cluster_raw, local_counts = _capture_cluster_resources(
        sink, context, warnings, redactions, verbose, substitutions
    )

    print("Writing local (gitignored) substitution manifests...")
    _write_local_substitution_manifests(sink, substitutions)

    print("Capturing namespaced non-Helm resources...")
    raw_counts = _capture_namespaced_resources(sink, context, warnings, redactions, verbose, substitutions)
    _write_raw_readme(sink)

    print("Capturing secrets metadata...")
    secret_rows = _capture_secrets(sink, context, timestamp, warnings)

    print("Writing docs/ip-inventory.csv and docs/inventory.md...")
    nodes_for_ip_context = kube.list_nodes(context)
    ip_context = secretscan.build_ip_context(nodes_for_ip_context, cluster_raw)
    ip_rows = _collect_ip_inventory(root, ip_context)
    sink.write_text(root / "docs" / "ip-inventory.csv", inventory.build_ip_inventory_csv(ip_rows))

    ip_summary = _summarize_ip_inventory(ip_rows)
    inv_data = _gather_inventory_data(
        context, resolved_context, release_infos, helm_repos, cluster_raw, timestamp, warnings, ip_summary
    )
    inventory_md = varsub.apply_substitutions_text(inventory.build_inventory_md(inv_data), substitutions)
    sink.write_text(root / "docs" / "inventory.md", inventory_md)

    sink.write_text(root / "docs" / "redactions.md", inventory.build_redactions_md(redactions, timestamp))

    tracker.sweep_orphans()
    tracker.report.warnings = warnings
    if redactions:
        tracker.report.warnings.append(
            f"{len(redactions)} value(s) redacted before writing -- see docs/redactions.md"
        )

    stats = {
        "release_count": len(release_infos),
        "repo_count": len(repo_names),
        "cluster_resource_count": sum(cluster_counts.values()),
        "cluster_local_only_count": sum(local_counts.values()),
        "raw_resource_count": sum(raw_counts.values()),
        "secret_count": len(secret_rows),
    }
    return tracker.report, stats


def _resolve_chart_source(context, chart_name, chart_version, helm_repos, verbose):
    for repo in helm_repos:
        repo_name = repo["name"]
        if helmcli.search_repo_exact(context, repo_name, chart_name, chart_version):
            kind = "OCIRepository" if repo["url"].startswith("oci://") else "HelmRepository"
            return {"resolved": True, "kind": kind, "repoName": repo_name, "reason": None}
    reason = flux.unresolved_source_reason(chart_name, chart_version)
    return {"resolved": False, "kind": None, "repoName": None, "reason": reason}


def _capture_helm_releases(sink: Sink, context, helm_repos, timestamp, warnings, redactions, verbose, substitutions):
    releases_list = helmcli.list_releases(context)
    if not sink.dry_run:
        helmcli.repo_update(context)
    else:
        warnings.append(
            "dry-run: skipped `helm repo update`; chart-source resolution used whatever "
            "local repo cache already existed and may be stale"
        )

    infos = []
    for rel in releases_list:
        name = rel["name"]
        namespace = rel["namespace"]
        meta = helmcli.get_metadata(context, name, namespace)
        chart_name = meta.get("chart", "")
        chart_version = meta.get("version", "")
        app_version = meta.get("appVersion", "")
        revision = meta.get("revision", rel.get("revision"))
        status = meta.get("status", rel.get("status"))

        if verbose:
            print(f"  {namespace}/{name}: {chart_name}-{chart_version}", file=sys.stderr)

        app_dir = sink.root / "kubernetes" / "apps" / namespace / name / "app"
        for filename, all_values in (("values.yaml", False), ("values-all.yaml", True)):
            raw_text = helmcli.get_values_yaml(context, name, namespace, all_values=all_values)
            values_data = yamlio.parse_yaml_string(raw_text) or {}
            found = secretscan.redact_credentials(values_data)
            for r in found:
                entry = f"{namespace}/{name}/app/{filename}: {r.path} ({r.reason})"
                redactions.append(entry)
                if verbose:
                    print(f"  [REDACTED] {entry}", file=sys.stderr)
            substituted_count = varsub.apply_substitutions(values_data, substitutions)
            if found or substituted_count:
                # Redacted and/or substituted: re-serialize from the parsed
                # (now-modified) tree rather than writing Helm's original
                # text verbatim.
                sink.write_text(app_dir / filename, yamlio.to_yaml_string(values_data))
            else:
                # Nothing to change: write Helm's own YAML text unchanged,
                # to stay byte-faithful to `helm get values` where possible.
                sink.write_text(app_dir / filename, _ensure_nl(raw_text))

        source = _resolve_chart_source(context, chart_name, chart_version, helm_repos, verbose)
        if not source["resolved"]:
            warnings.append(f"{namespace}/{name}: {source['reason']}")

        release_doc = {
            "release": name,
            "namespace": namespace,
            "chart": chart_name,
            "chartVersion": chart_version,
            "appVersion": app_version,
            "revision": revision,
            "status": status,
            "capturedAt": timestamp,
            "chartSource": {
                "resolved": source["resolved"],
                "kind": source["kind"],
                "name": source["repoName"],
            },
        }
        # Plain (non-sticky) header: release.yaml's own capturedAt field
        # already changes every run by design (it's a backup log entry,
        # like docs/inventory.md), so a sticky header timestamp here would
        # just be inconsistent with the body.
        header = headers.generated_header(
            timestamp,
            extra_lines=["Backup identity record; not a Kubernetes manifest, never applied."],
        )
        sink.write_yaml(
            sink.root / "kubernetes" / "apps" / namespace / name / "release.yaml",
            release_doc,
            header=header,
        )
        infos.append(release_doc)

    for ns in sorted({info["namespace"] for info in infos}):
        ns_obj = kube.get_namespace(context, ns)
        if ns_obj:
            path = sink.root / "kubernetes" / "apps" / ns / "namespace.yaml"
            cleaned_ns = _neat_and_redact(ns_obj, f"apps/{ns}/namespace.yaml", redactions, verbose, substitutions)
            sink.write_yaml(path, cleaned_ns)

    return infos


def _write_local_substitution_manifests(sink: Sink, substitutions: list[dict]) -> None:
    """Ready-to-`kubectl apply` ConfigMap/Secret with the REAL substitution
    values, for kubernetes/flux/config/cluster-resources.yaml's
    postBuild.substituteFrom to pick up -- gitignored, regenerated every
    run from kubernetes/.local/variable-substitutions.yaml. If a tier has
    no entries, nothing is written this run and the tracker's orphan sweep
    removes any stale file from a previous run that did have one.
    """
    configmap = varsub.render_configmap(substitutions)
    if configmap:
        sink.write_yaml(sink.root.joinpath(*varsub.LOCAL_CONFIGMAP_PATH), configmap)
    secret = varsub.render_secret(substitutions)
    if secret:
        sink.write_yaml(sink.root.joinpath(*varsub.LOCAL_SECRET_PATH), secret)


def _write_helm_repository_files(sink: Sink, helm_repos, timestamp) -> list[str]:
    names = []
    for repo in helm_repos:
        name = repo["name"]
        url = repo["url"]
        if url.startswith("oci://"):
            doc = flux.oci_repository(name=name, url=url, interval=constants.HELMREPOSITORY_INTERVAL)
        else:
            doc = flux.helm_repository(name=name, url=url, interval=constants.HELMREPOSITORY_INTERVAL)
        path = sink.root / "kubernetes" / "flux" / "meta" / "repositories" / f"{name}.yaml"
        sink.write_yaml_stable(path, doc, timestamp)
        names.append(name)
    return names


def _capture_cluster_resources(sink: Sink, context, warnings, redactions, verbose, substitutions):
    """Writes to kubernetes/cluster/<kind>/ (git-tracked) for resources that
    are genuinely the operator's own, and to kubernetes/.local/cluster/<kind>/
    (gitignored, still backed up) for ones that are built-in/system/operator-
    owned/dynamically-provisioned -- see skipfilter.classify_cluster_locality.
    Variable substitution only ever applies to the tracked copy; the local
    one stays full-fidelity for an actual restore.
    """
    counts: dict[str, int] = {}
    local_counts: dict[str, int] = {}
    raw_items: dict[str, list[dict]] = {}
    for kind in constants.CLUSTER_RESOURCE_KINDS:
        items, warn = kube.get_all(context, kind)
        if warn:
            warnings.append(warn)
            if verbose:
                print(f"  [cluster] {warn}", file=sys.stderr)
            raw_items[kind] = []
            counts[kind] = 0
            local_counts[kind] = 0
            continue
        raw_items[kind] = items
        kept = 0
        kept_local = 0
        for item in items:
            reason = skipfilter.should_skip(kind, item)
            meta = item.get("metadata", {})
            if reason:
                if verbose:
                    print(f"  [cluster] skip {kind}/{meta.get('name')}: {reason}", file=sys.stderr)
                continue
            name = sanitize_filename(meta["name"])
            ns = meta.get("namespace")
            filename = f"{ns}-{name}.yaml" if ns else f"{name}.yaml"

            locality_reason = skipfilter.classify_cluster_locality(kind, item)
            if locality_reason:
                if verbose:
                    print(f"  [cluster] local-only {kind}/{meta.get('name')}: {locality_reason}", file=sys.stderr)
                path = sink.root / "kubernetes" / ".local" / "cluster" / kind / filename
                label = f".local/cluster/{kind}/{filename}"
                sink.write_yaml(path, _neat_and_redact(item, label, redactions, verbose))
                kept_local += 1
                continue

            path = sink.root / "kubernetes" / "cluster" / kind / filename
            label = f"cluster/{kind}/{filename}"
            sink.write_yaml(path, _neat_and_redact(item, label, redactions, verbose, substitutions))
            kept += 1
        counts[kind] = kept
        local_counts[kind] = kept_local
    return counts, raw_items, local_counts


def _capture_namespaced_resources(sink: Sink, context, warnings, redactions, verbose, substitutions):
    counts: dict[str, int] = {}
    for kind in constants.NAMESPACED_RESOURCE_KINDS:
        items, warn = kube.get_all(context, kind)
        if warn:
            warnings.append(warn)
            if verbose:
                print(f"  [raw] {warn}", file=sys.stderr)
            counts[kind] = 0
            continue
        kept = 0
        for item in items:
            meta = item.get("metadata", {})
            ns = meta.get("namespace")
            if not ns or ns in constants.BUILTIN_NAMESPACES_TO_SKIP:
                continue
            reason = skipfilter.should_skip(kind, item)
            if reason:
                if verbose:
                    print(f"  [raw] skip {ns}/{kind}/{meta.get('name')}: {reason}", file=sys.stderr)
                continue
            name = sanitize_filename(meta["name"])
            path = sink.root / "kubernetes" / "raw" / ns / kind / f"{name}.yaml"
            label = f"raw/{ns}/{kind}/{name}.yaml"
            sink.write_yaml(path, _neat_and_redact(item, label, redactions, verbose, substitutions))
            kept += 1
        counts[kind] = kept
    return counts


_RAW_README = """# kubernetes/raw

Safety-net dump of namespaced resources that aren't managed by a Helm
release and aren't owned by another object (see the skip filter in the
generator's `skipfilter.py`). Nothing under this tree is wired into Flux.

Move items into `kubernetes/apps/<namespace>/<app>/app/` by hand as you
curate them; this script will stop re-writing a file here once the live
resource it came from is deleted or starts matching the skip filter (for
example, once it's adopted into a Helm release).
"""


def _write_raw_readme(sink: Sink) -> None:
    sink.write_text(sink.root / "kubernetes" / "raw" / "README.md", _RAW_README)


def _capture_secrets(sink: Sink, context, timestamp, warnings):
    items, warn = kube.get_all(context, "secret")
    if warn:
        warnings.append(warn)
        items = []

    rows = []
    for item in items:
        meta = item.get("metadata", {})
        secret_type = item.get("type", "")
        annotations = meta.get("annotations") or {}
        if secret_type == "kubernetes.io/service-account-token":
            continue
        if secret_type.startswith("helm.sh/"):
            continue
        if "meta.helm.sh/release-name" in annotations:
            continue
        keys = sorted((item.get("data") or {}).keys())
        rows.append(
            {
                "namespace": meta.get("namespace", ""),
                "name": meta["name"],
                "type": secret_type,
                "keys": ";".join(keys),
                "created": meta.get("creationTimestamp", ""),
            }
        )
    rows.sort(key=lambda r: (r["namespace"], r["name"]))

    sink.write_text(sink.root / "docs" / "secrets-inventory.csv", inventory.build_secrets_csv(rows))
    sink.write_text(
        sink.root / "docs" / "sops-planning.md", inventory.build_sops_planning_md(rows, timestamp)
    )
    return rows


def _collect_ip_inventory(root: Path, ip_context: dict) -> list[dict]:
    """Every IP address found anywhere in the final (post-redaction) output
    tree, one row per (ip, file), with a best-effort "what is this for"
    guess (docs/ip-inventory.csv) plus the aggregate used by the
    informational summary in docs/inventory.md.

    This is visibility, not (by itself) a test: a homelab infra backup is
    expected to be full of its own private/RFC1918 addresses (MetalLB
    pool, NFS server, node IPs, ...). Public addresses also get a real,
    failing test (test_no_public_ip_addresses) rather than just a row here.
    """
    per_file: dict[tuple[str, str], list[str]] = {}
    classifications: dict[str, str] = {}
    for path in sorted(root.glob("kubernetes/**/*.yaml")):
        try:
            data = yamlio.read_yaml_file(path)
        except Exception:
            continue
        if not data:
            continue
        rel = path.relative_to(root).as_posix()
        for field_path, ip, classification in secretscan.find_ip_addresses(data):
            classifications[ip] = classification
            per_file.setdefault((ip, rel), []).append(field_path)

    rows = []
    for (ip, rel), field_paths in per_file.items():
        classification = classifications[ip]
        purpose = secretscan.classify_ip_purpose(ip, classification, field_paths[0], ip_context)
        rows.append(
            {
                "ip": ip,
                "classification": classification,
                "purpose": purpose,
                "file": rel,
                "occurrences": len(field_paths),
                "field_paths": ";".join(sorted(set(field_paths))),
            }
        )
    rows.sort(key=lambda r: (r["classification"] != "public", r["ip"], r["file"]))
    return rows


def _summarize_ip_inventory(ip_rows: list[dict]) -> list[dict]:
    """Collapse the per-file CSV rows into one row per distinct IP, for the
    short summary in docs/inventory.md (docs/ip-inventory.csv has the
    per-file detail).
    """
    totals: dict[str, dict] = {}
    for row in ip_rows:
        entry = totals.setdefault(row["ip"], {"ip": row["ip"], "classification": row["classification"], "occurrences": 0})
        entry["occurrences"] += row["occurrences"]
    return sorted(totals.values(), key=lambda r: (r["classification"] != "public", r["ip"]))


def _gather_inventory_data(
    context, resolved_context, release_infos, helm_repos, cluster_raw, timestamp, warnings, ip_inventory
):
    nodes = []
    for node in kube.list_nodes(context):
        labels = node.get("metadata", {}).get("labels", {})
        roles = ",".join(
            sorted(
                label.split("node-role.kubernetes.io/", 1)[1]
                for label in labels
                if label.startswith("node-role.kubernetes.io/")
            )
        ) or "worker"
        nodes.append(
            {
                "name": node["metadata"]["name"],
                "roles": roles,
                "version": node.get("status", {}).get("nodeInfo", {}).get("kubeletVersion", ""),
                "os": node.get("status", {}).get("nodeInfo", {}).get("osImage", ""),
            }
        )

    namespaces = sorted(ns["metadata"]["name"] for ns in kube.list_namespaces(context))

    pvs = []
    for pv in cluster_raw.get("persistentvolume", []):
        spec = pv.get("spec", {})
        pvs.append(
            {
                "name": pv["metadata"]["name"],
                "capacity": spec.get("capacity", {}).get("storage", ""),
                "reclaim": spec.get("persistentVolumeReclaimPolicy", ""),
                "storageClass": spec.get("storageClassName", ""),
                "phase": pv.get("status", {}).get("phase", ""),
            }
        )

    storageclasses = []
    for sc in cluster_raw.get("storageclass", []):
        annotations = sc.get("metadata", {}).get("annotations", {}) or {}
        storageclasses.append(
            {
                "name": sc["metadata"]["name"],
                "provisioner": sc.get("provisioner", ""),
                "reclaim": sc.get("reclaimPolicy", ""),
                "is_default": annotations.get("storageclass.kubernetes.io/is-default-class", "false"),
            }
        )

    clusterissuers = sorted(
        ci["metadata"]["name"] for ci in cluster_raw.get("clusterissuer.cert-manager.io", [])
    )

    metallb_pools = []
    for pool in cluster_raw.get("ipaddresspool.metallb.io", []):
        meta = pool.get("metadata", {})
        metallb_pools.append(
            {
                "namespace": meta.get("namespace", ""),
                "name": meta.get("name", ""),
                "addresses": pool.get("spec", {}).get("addresses", []),
            }
        )

    crds = []
    for crd in kube.list_crds(context):
        spec = crd.get("spec", {})
        versions = spec.get("versions", [])
        version_name = versions[-1]["name"] if versions else ""
        crds.append(
            {
                "name": crd["metadata"]["name"],
                "group": spec.get("group", ""),
                "version": version_name,
            }
        )
    crds.sort(key=lambda c: c["name"])

    server_ver = kube.server_version(context)
    server_version_str = server_ver.get("serverVersion", {}).get("gitVersion", "unknown")

    return {
        "timestamp": timestamp,
        "context": resolved_context,
        "server_version": server_version_str,
        "nodes": nodes,
        "namespaces": namespaces,
        "releases": sorted(release_infos, key=lambda r: (r["namespace"], r["release"])),
        "helm_repos": helm_repos,
        "pvs": pvs,
        "storageclasses": storageclasses,
        "clusterissuers": clusterissuers,
        "metallb_pools": metallb_pools,
        "crds": crds,
        "warnings": warnings,
        "ip_inventory": ip_inventory,
    }
