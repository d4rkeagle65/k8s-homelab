"""helm CLI wrappers. Read-only against the cluster.

`repo_update` and `search_repo` touch the Helm client's local index cache
(the same cache `helm search repo` always reads from), not the cluster or
the generated repo output.
"""

from __future__ import annotations

import json

from . import procutil


def _base_args(context: str | None) -> list[str]:
    args = ["helm"]
    if context:
        args += ["--kube-context", context]
    return args


def list_releases(context: str | None) -> list[dict]:
    data = procutil.run_json(_base_args(context) + ["list", "-A", "-o", "json"])
    return data or []


def get_values_yaml(context: str | None, release: str, namespace: str, *, all_values: bool = False) -> str:
    args = _base_args(context) + ["get", "values", release, "-n", namespace, "-o", "yaml"]
    if all_values:
        args.append("-a")
    proc = procutil.run(args)
    return proc.stdout


def get_metadata(context: str | None, release: str, namespace: str) -> dict:
    data = procutil.run_json(
        _base_args(context) + ["get", "metadata", release, "-n", namespace, "-o", "json"]
    )
    return data or {}


def repo_list(context: str | None) -> list[dict]:
    # helm exits non-zero with "Error: no repositories to show" when none
    # are configured -- that's a valid empty result, not a failure.
    proc = procutil.run(_base_args(context) + ["repo", "list", "-o", "json"], check=False)
    if proc.returncode != 0:
        return []
    try:
        return json.loads(proc.stdout) if proc.stdout.strip() else []
    except json.JSONDecodeError:
        return []


def repo_update(context: str | None) -> None:
    procutil.run(_base_args(context) + ["repo", "update"], check=False)


def search_repo_exact(context: str | None, repo_name: str, chart_name: str, version: str) -> bool:
    """True if `helm search repo` finds `repo_name/chart_name` at exactly `version`.

    Searches the local repo index cache built by `helm repo list` +
    `helm repo update`. This is the only way to recover which configured
    repo a release's chart came from: Helm does not persist the source
    repo/URL used at install time anywhere in the release object itself
    (verified against `helm get metadata`, `helm get manifest`, and the
    Release/Chart.Metadata data model -- none of them carry it).
    """
    proc = procutil.run(
        _base_args(context) + ["search", "repo", f"{repo_name}/{chart_name}", "-l", "-o", "json"],
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return False
    try:
        results = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False
    target = f"{repo_name}/{chart_name}"
    return any(r.get("name") == target and r.get("version") == version for r in results)
