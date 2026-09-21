"""Which on-disk paths each phase owns, for idempotent orphan cleanup.

A phase's FileTracker only ever deletes a file it owns. Scoping ownership
this way means running `capture` alone never deletes `generate`'s output
(helmrelease.yaml, ks.yaml, kustomization.yaml, ...) just because that
invocation didn't touch it, and vice versa for `generate` alone.

fnmatch's `*` matches across `/`, but every segment it stands in for here
is a Kubernetes namespace or release name, and those can't contain `/`
(DNS-1123 label), so it can't accidentally span directory boundaries.
"""

from __future__ import annotations

import fnmatch

CAPTURE_PATTERNS = [
    "docs/*",
    "kubernetes/raw/README.md",
    "kubernetes/raw/*/*/*.yaml",
    "kubernetes/cluster/*/*.yaml",
    "kubernetes/.local/cluster/*/*.yaml",
    "kubernetes/.local/cluster-substitutions-configmap.yaml",
    "kubernetes/.local/cluster-substitutions-secret.yaml",
    "kubernetes/apps/*/namespace.yaml",
    "kubernetes/apps/*/*/release.yaml",
    "kubernetes/apps/*/*/app/values.yaml",
    "kubernetes/apps/*/*/app/values-all.yaml",
    "kubernetes/flux/meta/repositories/*.yaml",
]
CAPTURE_EXCLUDE = [
    "kubernetes/flux/meta/repositories/kustomization.yaml",
    "docs/variable-substitutions.md",  # matches "docs/*" but is generate.py's, not capture.py's
]

GENERATE_PATTERNS = [
    "kubernetes/apps/*/*/app/helmrelease.yaml",
    "kubernetes/apps/*/*/app/kustomization.yaml",
    "kubernetes/apps/*/*/ks.yaml",
    "kubernetes/apps/*/kustomization.yaml",
    "kubernetes/flux/meta/repositories/kustomization.yaml",
    "kubernetes/flux/config/cluster.yaml",
    "kubernetes/flux/config/cluster-resources.yaml",
    "kubernetes/cluster/kustomization.yaml",
    "docs/variable-substitutions.md",
    ".sops.yaml",
    ".gitignore",
    ".gitattributes",
    ".githooks/pre-commit",
    "README.md",
]


def _matches(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in patterns)


def capture_owns(rel: str) -> bool:
    return _matches(rel, CAPTURE_PATTERNS) and not _matches(rel, CAPTURE_EXCLUDE)


def generate_owns(rel: str) -> bool:
    return _matches(rel, GENERATE_PATTERNS)
