"""Which on-disk paths each phase owns, for idempotent orphan cleanup.

A phase's FileTracker only ever deletes a file it owns. Scoping ownership
this way means running `capture` alone never deletes `generate`'s output
(helmrelease.yaml, ks.yaml, kustomization.yaml, ...) just because that
invocation didn't touch it, and vice versa for `generate` alone.

fnmatch's `*` matches across `/`, but every segment it stands in for here
is a Kubernetes namespace or release name, and those can't contain `/`
(DNS-1123 label), so it can't accidentally span directory boundaries.

Hand-written releases are the exception. A release folder containing a
`.handwritten` marker file is authored by hand rather than captured, so
neither phase may sweep anything inside it, nor the namespace.yaml and
Helm repository files it depends on (see handwritten_protected_paths).
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Callable

from . import yamlio

HANDWRITTEN_MARKER = ".handwritten"
# See promote.py: capture writes the namespace's non-Helm resources into
# this folder's app/ instead of kubernetes/raw/.
PROMOTE_MARKER = ".promote"

CAPTURE_PATTERNS = [
    "docs/*",
    "kubernetes/raw/README.md",
    "kubernetes/raw/*/*/*.yaml",
    "kubernetes/cluster/*/*.yaml",
    "kubernetes/.local/cluster/*/*.yaml",
    "kubernetes/.local/substitutions-cache.yaml",
    "kubernetes/.local/vaultwarden-pending.yaml",
    # No longer written (cluster-secrets replaced them), but still owned, so
    # the next capture deletes these copies of every value.
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
    "kubernetes/apps/kustomization.yaml",
    "kubernetes/flux/meta/repositories/kustomization.yaml",
    "kubernetes/flux/config/cluster.yaml",
    "kubernetes/flux/config/cluster-resources.yaml",
    "kubernetes/cluster/kustomization.yaml",
    "docs/variable-substitutions.md",
    ".sops.yaml",
    ".gitignore",
    ".gitattributes",
    ".githooks/pre-commit",
    # Not README.md: generate only writes one when none exists, so owning it
    # would make the orphan sweep delete an operator-written README.
]


def _matches(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in patterns)


def capture_owns(rel: str) -> bool:
    return _matches(rel, CAPTURE_PATTERNS) and not _matches(rel, CAPTURE_EXCLUDE)


def generate_owns(rel: str) -> bool:
    return _matches(rel, GENERATE_PATTERNS)


def is_handwritten(release_dir: Path) -> bool:
    return (release_dir / HANDWRITTEN_MARKER).is_file()


def is_promoted(release_dir: Path) -> bool:
    return (release_dir / PROMOTE_MARKER).is_file() and not is_handwritten(release_dir)


def promoted_release_dirs(root: Path) -> list[Path]:
    apps = root / "kubernetes" / "apps"
    if not apps.is_dir():
        return []
    return sorted(p.parent for p in apps.glob(f"*/*/{PROMOTE_MARKER}") if is_promoted(p.parent))


def _promoted_manifest_owner(root: Path) -> Callable[[str], bool]:
    """The manifests capture writes into a promoted release's app/ -- every
    .yaml there except generate's kustomization.yaml.
    """
    prefixes = tuple(f"{d.relative_to(root).as_posix()}/app/" for d in promoted_release_dirs(root))

    def owns(rel: str) -> bool:
        if not prefixes or not rel.startswith(prefixes) or not rel.endswith(".yaml"):
            return False
        rest = rel[len(next(p for p in prefixes if rel.startswith(p))):]
        return "/" not in rest and rest != "kustomization.yaml"

    return owns


def handwritten_release_dirs(root: Path) -> list[Path]:
    apps = root / "kubernetes" / "apps"
    if not apps.is_dir():
        return []
    return sorted(p.parent for p in apps.glob(f"*/*/{HANDWRITTEN_MARKER}") if p.is_file())


def handwritten_protected_paths(root: Path) -> set[str]:
    """Repo-relative paths neither phase may sweep because a hand-written
    release needs them: every file in the release folder, its namespace's
    namespace.yaml (capture only writes that for namespaces with a live
    Helm release, so it would otherwise vanish before the first deploy),
    and each Helm repository file its helmrelease.yaml's sourceRef names
    (capture only writes repositories from the local `helm repo list`).
    """
    protected: set[str] = set()
    source_names: set[str] = set()
    for release_dir in handwritten_release_dirs(root):
        protected.update(p.relative_to(root).as_posix() for p in release_dir.rglob("*") if p.is_file())
        namespace_yaml = release_dir.parent / "namespace.yaml"
        protected.add(namespace_yaml.relative_to(root).as_posix())
        hr_path = release_dir / "app" / "helmrelease.yaml"
        if hr_path.is_file():
            hr = yamlio.read_yaml_file(hr_path) or {}
            source_ref = hr.get("spec", {}).get("chart", {}).get("spec", {}).get("sourceRef", {})
            if source_ref.get("name"):
                source_names.add(source_ref["name"])

    repo_dir = root / "kubernetes" / "flux" / "meta" / "repositories"
    if source_names and repo_dir.is_dir():
        for path in repo_dir.glob("*.yaml"):
            if path.name == "kustomization.yaml":
                continue
            doc = yamlio.read_yaml_file(path) or {}
            if doc.get("metadata", {}).get("name") in source_names:
                protected.add(path.relative_to(root).as_posix())
    return protected


def _excluding(owns: Callable[[str], bool], protected: set[str]) -> Callable[[str], bool]:
    return lambda rel: owns(rel) and rel not in protected


def capture_owner(root: Path) -> Callable[[str], bool]:
    """capture_owns plus promoted releases' manifests, minus anything a
    hand-written release depends on."""
    promoted = _promoted_manifest_owner(root)
    return _excluding(lambda rel: capture_owns(rel) or promoted(rel), handwritten_protected_paths(root))


def generate_owner(root: Path) -> Callable[[str], bool]:
    """generate_owns, minus anything a hand-written release depends on."""
    return _excluding(generate_owns, handwritten_protected_paths(root))
