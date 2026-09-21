"""kubectl wrappers. Read-only: every function here issues `kubectl get`."""

from __future__ import annotations

from . import procutil


def _base_args(context: str | None) -> list[str]:
    args = ["kubectl"]
    if context:
        args += ["--context", context]
    return args


def current_context() -> str:
    proc = procutil.run(["kubectl", "config", "current-context"])
    return proc.stdout.strip()


def server_version(context: str | None) -> dict:
    data = procutil.run_json(_base_args(context) + ["version", "-o", "json"])
    return data or {}


def list_nodes(context: str | None) -> list[dict]:
    data = procutil.run_json(_base_args(context) + ["get", "nodes", "-o", "json"])
    return (data or {}).get("items", [])


def list_namespaces(context: str | None) -> list[dict]:
    data = procutil.run_json(_base_args(context) + ["get", "namespace", "-o", "json"])
    return (data or {}).get("items", [])


def get_namespace(context: str | None, name: str) -> dict | None:
    return procutil.run_json(_base_args(context) + ["get", "namespace", name, "-o", "json"])


def get_all(context: str | None, kind: str) -> tuple[list[dict], str | None]:
    """Fetch every object of `kind` across all namespaces.

    `-A` is a documented no-op for cluster-scoped kinds (kubectl ignores
    it), so this one code path works uniformly for both scopes -- see the
    note in constants.py about the metallb.io kinds.

    Returns (items, warning). `warning` is set (and items is []) when the
    kind isn't installed on this cluster at all, which is a normal
    "nothing to capture for this kind" outcome, not a fatal error.
    """
    data, err = procutil.run_json_tolerant(_base_args(context) + ["get", kind, "-A", "-o", "json"])
    if err is not None:
        return [], f"could not list '{kind}': {err}"
    return (data or {}).get("items", []), None


def get_namespaced(context: str | None, kind: str, namespace: str) -> tuple[list[dict], str | None]:
    data, err = procutil.run_json_tolerant(
        _base_args(context) + ["get", kind, "-n", namespace, "-o", "json"]
    )
    if err is not None:
        return [], f"could not list '{kind}' in namespace '{namespace}': {err}"
    return (data or {}).get("items", []), None


def list_secrets(context: str | None, namespace: str) -> list[dict]:
    data = procutil.run_json(
        _base_args(context) + ["get", "secret", "-n", namespace, "-o", "json"]
    )
    return (data or {}).get("items", [])


def list_crds(context: str | None) -> list[dict]:
    data = procutil.run_json(_base_args(context) + ["get", "crd", "-o", "json"])
    return (data or {}).get("items", [])
