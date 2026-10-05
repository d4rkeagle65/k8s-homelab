"""Where an app lives in the repo, and the labels it carries.

Every app is kubernetes/prod/<category>/<namespace>/<release>/. The category is
the `homelab.local/category` label on the app's Namespace in the cluster; this
module is the one place that knows the folder layout and the label names, so
capture, generate, ownership and the tests agree on them.

A namespace's folder sits under exactly one category folder. capture refuses a
namespace with no valid category label, and one whose label names a different
category from the folder it already has: moving a folder is a `git mv` made on
purpose, never something a run does on its own.
"""

from __future__ import annotations

from pathlib import Path

ENV = "prod"
ENV_DIR = Path("kubernetes") / ENV
CATEGORIES = ("system", "services", "apps")

LABEL_PREFIX = "homelab.local/"
CATEGORY_LABEL = LABEL_PREFIX + "category"
ENV_LABEL = LABEL_PREFIX + "env"
MANAGED_BY_LABEL = LABEL_PREFIX + "managed-by"
PART_OF_LABEL = "app.kubernetes.io/part-of"
# How people log in to a namespace's app, set by hand on its Namespace: which
# apps use Authentik's OIDC is known only inside Authentik.
LOGIN_LABEL = LABEL_PREFIX + "login"
LOGIN_VALUES = ("authentik-oidc", "authentik-proxy", "app", "none")

# managed-by values, one per kind of release folder (see ownership.py).
MANAGED_HELM = "helm"
MANAGED_HAND = "hand"
MANAGED_PROMOTE = "promote"


class LayoutError(Exception):
    """The repo or the cluster doesn't say where an app belongs."""


def env_root(root: Path) -> Path:
    return root / ENV_DIR


def namespace_dirs(root: Path) -> list[Path]:
    """Every kubernetes/prod/<category>/<namespace>/ folder, sorted by path.
    Folders under prod/ that aren't a category are left out; check_layout
    names them."""
    base = env_root(root)
    if not base.is_dir():
        return []
    return sorted(
        ns_dir
        for category in CATEGORIES
        if (base / category).is_dir()
        for ns_dir in (base / category).iterdir()
        if ns_dir.is_dir()
    )


def release_dirs(root: Path) -> list[Path]:
    """Every kubernetes/prod/<category>/<namespace>/<release>/ folder."""
    return [r for ns_dir in namespace_dirs(root) for r in sorted(p for p in ns_dir.iterdir() if p.is_dir())]


def category_of(ns_dir: Path) -> str:
    """The category a namespace folder sits in."""
    return ns_dir.parent.name


def existing_namespace_dir(root: Path, namespace: str) -> Path | None:
    """The namespace's folder if it has one, in whichever category."""
    found = [d for d in namespace_dirs(root) if d.name == namespace]
    if len(found) > 1:
        raise LayoutError(
            f"namespace {namespace} has a folder in more than one category: "
            + ", ".join(d.relative_to(root).as_posix() for d in found)
        )
    return found[0] if found else None


def category_from_labels(labels: dict | None) -> str | None:
    """The category a Namespace's labels give, or None when the label is
    missing or isn't one of CATEGORIES."""
    value = (labels or {}).get(CATEGORY_LABEL)
    return value if value in CATEGORIES else None


def namespace_dir(root: Path, namespace: str, labels: dict | None) -> Path:
    """Where a namespace's folder belongs, from its live labels. Raises
    LayoutError when the label is missing or invalid, or names a different
    category from the folder the namespace already has."""
    category = category_from_labels(labels)
    if category is None:
        value = (labels or {}).get(CATEGORY_LABEL)
        detail = f"its {CATEGORY_LABEL} label is {value!r}" if value is not None else f"it has no {CATEGORY_LABEL} label"
        raise LayoutError(
            f"namespace {namespace}: {detail}; label it with one of {', '.join(CATEGORIES)} "
            f"(kubectl label namespace {namespace} {CATEGORY_LABEL}=<category>)"
        )
    current = existing_namespace_dir(root, namespace)
    if current is not None and category_of(current) != category:
        raise LayoutError(
            f"namespace {namespace} is labelled {category} but its folder is "
            f"{current.relative_to(root).as_posix()}; move it with "
            f"git mv {current.relative_to(root).as_posix()} {(ENV_DIR / category / namespace).as_posix()}"
        )
    return env_root(root) / category / namespace


def app_path(release_dir: Path, root: Path) -> str:
    """A release's app/ folder as a Flux Kustomization's spec.path."""
    return "./" + (release_dir / "app").relative_to(root).as_posix()


def common_labels(ns_dir: Path, managed_by: str) -> dict[str, str]:
    """The labels every object of a release in this namespace folder carries,
    applied through commonMetadata on its Flux Kustomization and HelmRelease."""
    return {
        ENV_LABEL: ENV,
        CATEGORY_LABEL: category_of(ns_dir),
        PART_OF_LABEL: ns_dir.name,
        MANAGED_BY_LABEL: managed_by,
    }


def check_layout(root: Path) -> list[str]:
    """What's wrong with the folder layout itself: anything under prod/ that
    isn't a category folder, and a namespace folder in two categories."""
    problems = []
    base = env_root(root)
    if base.is_dir():
        for p in sorted(base.iterdir()):
            if p.is_dir() and p.name not in CATEGORIES:
                problems.append(f"{p.relative_to(root).as_posix()} is not one of the categories {', '.join(CATEGORIES)}")
    seen: dict[str, Path] = {}
    for ns_dir in namespace_dirs(root):
        if ns_dir.name in seen:
            problems.append(
                f"namespace {ns_dir.name} has folders in {category_of(seen[ns_dir.name])} and {category_of(ns_dir)}"
            )
        seen[ns_dir.name] = ns_dir
    return problems
