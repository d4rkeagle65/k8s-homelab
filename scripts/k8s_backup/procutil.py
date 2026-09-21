"""Subprocess helpers and external-tool presence checks.

Every call in this module is either a read against the cluster (kubectl get,
helm list/get/search) or a local read of the Helm client's own repo cache
(helm search reads a local index; nothing here ever mutates cluster state).
"""

from __future__ import annotations

import json
import shutil
import subprocess


class ToolError(RuntimeError):
    """Raised when a required external tool is missing or a command fails."""


def require_tool(name: str, install_hint: str) -> None:
    if shutil.which(name) is None:
        raise ToolError(f"Required tool '{name}' was not found on PATH. {install_hint}")


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    # Force UTF-8 decoding of child-process output regardless of the
    # console's locale codepage (Windows defaults to cp1252/cp437, which
    # can't decode non-ASCII bytes kubectl/helm sometimes emit, e.g. in
    # chart annotations or CRD descriptions).
    proc = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and proc.returncode != 0:
        raise ToolError(
            f"Command failed ({proc.returncode}): {' '.join(args)}\n{proc.stderr.strip()}"
        )
    return proc


def run_json(args: list[str]):
    proc = run(args)
    stdout = proc.stdout.strip()
    if not stdout:
        return None
    return json.loads(stdout)


def run_json_tolerant(args: list[str]):
    """Like run_json, but returns (data, error) instead of raising.

    Used for lookups where the resource kind might not exist on this
    cluster at all (e.g. clusterissuer.cert-manager.io when cert-manager
    isn't installed) -- that's a normal "nothing to capture" case, not a
    fatal error.
    """
    proc = run(args, check=False)
    if proc.returncode != 0:
        return None, proc.stderr.strip()
    stdout = proc.stdout.strip()
    if not stdout:
        return None, None
    try:
        return json.loads(stdout), None
    except json.JSONDecodeError as exc:
        return None, str(exc)
