"""Filesystem-safe filenames for Kubernetes resource names.

Kubernetes allows `:` in some object names (many built-in ClusterRoles and
ClusterRoleBindings use "system:foo:bar"), which Windows/NTFS rejects
outright in a path. Namespaces and Helm release names are DNS-1123 labels
and can't contain any of these characters, so this only ever fires for the
occasional cluster-scoped RBAC object.
"""

from __future__ import annotations

import re

_UNSAFE = re.compile(r'[<>:"/\\|?*]')


def sanitize_filename(name: str) -> str:
    cleaned = _UNSAFE.sub("-", name)
    return cleaned.rstrip(" .") or "_"
