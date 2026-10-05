"""Hand-written releases (a `.handwritten` marker in the release folder)
must survive a generate run untouched: before this existed, generate's
orphan sweep deleted every hand-written ks.yaml, helmrelease.yaml and
kustomization.yaml because it owns those paths but didn't write them, and
capture deleted the namespace.yaml and Helm repository file they rely on.

Runs the real generate.run against a throwaway tree under tmp_path, never
against the repo itself.
"""

from __future__ import annotations

from pathlib import Path

from k8s_backup import generate
from k8s_backup.ownership import capture_owner, generate_owner

HANDWRITTEN_FILES = {
    "kubernetes/prod/apps/media/sonarr/.handwritten": "",
    "kubernetes/prod/apps/media/sonarr/ks.yaml": "kind: Kustomization\nmetadata:\n  name: sonarr\n",
    "kubernetes/prod/apps/media/sonarr/app/kustomization.yaml": "resources:\n- helmrelease.yaml\n",
    "kubernetes/prod/apps/media/sonarr/app/helmrelease.yaml": (
        "apiVersion: helm.toolkit.fluxcd.io/v2\n"
        "kind: HelmRelease\n"
        "metadata:\n  name: sonarr\n"
        "spec:\n  chart:\n    spec:\n      chart: app-template\n      version: 3.7.3\n"
        "      sourceRef:\n        kind: HelmRepository\n        name: bjw-s\n"
    ),
    "kubernetes/prod/apps/media/sonarr/app/postgres.yaml": "kind: Cluster\n",
    "kubernetes/prod/apps/media/namespace.yaml": "kind: Namespace\nmetadata:\n  name: media\n",
    "kubernetes/flux/meta/repositories/bjw-s.yaml": "kind: HelmRepository\nmetadata:\n  name: bjw-s\n",
}
UNRELATED_REPO = "kubernetes/flux/meta/repositories/unused.yaml"


def _build_tree(root: Path) -> None:
    files = dict(HANDWRITTEN_FILES)
    files[UNRELATED_REPO] = "kind: HelmRepository\nmetadata:\n  name: unused\n"
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_owners_exclude_handwritten_dependencies(tmp_path):
    _build_tree(tmp_path)
    capture_owns = capture_owner(tmp_path)
    generate_owns = generate_owner(tmp_path)
    for rel in HANDWRITTEN_FILES:
        assert not capture_owns(rel), rel
        assert not generate_owns(rel), rel
    # Only repositories a hand-written release references are protected.
    assert capture_owns(UNRELATED_REPO)
    # A normal captured release is still owned as before.
    assert generate_owns("kubernetes/prod/apps/obsidian/obsidian/ks.yaml")
    assert capture_owns("kubernetes/prod/apps/obsidian/obsidian/release.yaml")


def test_generate_leaves_handwritten_release_untouched(tmp_path):
    _build_tree(tmp_path)
    generate.run(tmp_path, dry_run=False, verbose=False)

    for rel, text in HANDWRITTEN_FILES.items():
        path = tmp_path / rel
        assert path.is_file(), f"generate removed {rel}"
        assert path.read_text(encoding="utf-8") == text, f"generate rewrote {rel}"
    # The namespace kustomization is still generator output, and lists the release.
    ns_kustomization = (tmp_path / "kubernetes/prod/apps/media/kustomization.yaml").read_text(encoding="utf-8")
    assert "sonarr/ks.yaml" in ns_kustomization
    assert not (tmp_path / "kubernetes/prod/apps/media/sonarr/release.yaml").exists()
