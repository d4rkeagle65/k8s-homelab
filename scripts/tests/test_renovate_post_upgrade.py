"""renovate_post_upgrade.sync_chart_versions: a chart version Renovate wrote into
a captured release's generated HelmRelease reaches its release.yaml, so generate
keeps it instead of putting the old one back. Runs on a throwaway tree.
"""

from __future__ import annotations

from pathlib import Path

from renovate_post_upgrade import helmrelease_chart_version, sync_chart_versions

RELEASE = (
    "# Backup identity record; not a Kubernetes manifest, never applied.\n"
    "release: couchdb\nnamespace: obsidian\nchart: couchdb\n"
    "chartVersion: 4.6.3\nappVersion: 3.5.2\n"
)
HELMRELEASE = (
    "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\nspec:\n"
    "  chart:\n    spec:\n      chart: couchdb\n      version: {version}\n"
    "  values:\n    image:\n      version: not-this-one\n"
)


def tree(tmp_path: Path, version: str) -> Path:
    rel = tmp_path / "kubernetes/prod/apps/obsidian/obsidian"
    (rel / "app").mkdir(parents=True)
    (rel / "release.yaml").write_text(RELEASE, encoding="utf-8")
    (rel / "app/helmrelease.yaml").write_text(HELMRELEASE.format(version=version), encoding="utf-8")
    return rel / "release.yaml"


def test_reads_the_chart_version_not_a_later_version_key():
    assert helmrelease_chart_version(HELMRELEASE.format(version='"4.7.0"')) == "4.7.0"


def test_a_new_chart_version_reaches_release_yaml(tmp_path):
    release = tree(tmp_path, "4.7.0")
    assert sync_chart_versions(tmp_path) == ["kubernetes/prod/apps/obsidian/obsidian/release.yaml"]
    text = release.read_text(encoding="utf-8")
    assert "chartVersion: 4.7.0\n" in text
    assert text.replace("4.7.0", "4.6.3") == RELEASE


def test_an_unchanged_release_is_left_alone(tmp_path):
    release = tree(tmp_path, "4.6.3")
    assert sync_chart_versions(tmp_path) == []
    assert release.read_text(encoding="utf-8") == RELEASE
