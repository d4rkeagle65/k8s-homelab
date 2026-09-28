"""generate against the base+overlay layout (apps/ + shared/ + test/, with
hand-written media releases): per-app substitution, the top-level apps
kustomization, and not clobbering what the operator wrote by hand.
Plus capture's Flux-ownership skip, which keeps shared/ and test/ out of
the captured tree.

Runs the real generate.run against a throwaway tree under tmp_path, never
against the repo itself.
"""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from k8s_backup import generate, scaffold
from k8s_backup.fluxowner import FluxOwnership

_yaml = YAML(typ="safe")

CAPTURED_RELEASE = {
    "kubernetes/apps/emby/namespace.yaml": "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: emby\n",
    "kubernetes/apps/emby/emby/release.yaml": (
        "release: emby\nnamespace: emby\nchart: emby\nchartVersion: 1.0.0\n"
        "chartSource:\n  resolved: true\n  kind: HelmRepository\n  name: d4rkeagle65\n"
    ),
    "kubernetes/apps/emby/emby/app/values.yaml": "host: emby.${BASE_DOMAIN}\n",
    "kubernetes/apps/media/namespace.yaml": "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: media\n",
    "kubernetes/apps/media/sonarr/.handwritten": "",
    "kubernetes/apps/media/sonarr/ks.yaml": "kind: Kustomization\nmetadata:\n  name: sonarr\n",
}


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_generated_ks_wires_substitutions_even_without_local_file(tmp_path):
    # No kubernetes/.local/ at all, as on a fresh clone: the per-app
    # Kustomization must still substitute, since Flux won't inherit it.
    _write(tmp_path, CAPTURED_RELEASE)
    generate.run(tmp_path, dry_run=False, verbose=False)

    ks = _yaml.load((tmp_path / "kubernetes/apps/emby/emby/ks.yaml").read_text(encoding="utf-8"))
    assert ks["spec"]["postBuild"]["substituteFrom"] == [
        {"kind": "ConfigMap", "name": "cluster-substitutions", "optional": True},
        {"kind": "Secret", "name": "cluster-substitutions", "optional": True},
    ]


def test_apps_kustomization_lists_every_namespace(tmp_path):
    _write(tmp_path, CAPTURED_RELEASE)
    generate.run(tmp_path, dry_run=False, verbose=False)

    doc = _yaml.load((tmp_path / "kubernetes/apps/kustomization.yaml").read_text(encoding="utf-8"))
    assert doc["resources"] == ["emby", "media"]


def test_existing_readme_is_left_alone(tmp_path):
    _write(tmp_path, CAPTURED_RELEASE)
    readme = tmp_path / "README.md"
    readme.write_text("# my homelab\n", encoding="utf-8")
    generate.run(tmp_path, dry_run=False, verbose=False)
    assert readme.read_text(encoding="utf-8") == "# my homelab\n"


def test_missing_readme_is_scaffolded(tmp_path):
    _write(tmp_path, CAPTURED_RELEASE)
    generate.run(tmp_path, dry_run=False, verbose=False)
    assert (tmp_path / "README.md").is_file()


def test_gitignore_keeps_hand_added_lines_and_is_stable():
    first = scaffold.gitignore("/docs/\n\n/secrets/\n__pycache__/\n")
    assert first.startswith(scaffold.gitignore())
    assert first.endswith("/docs/\n/secrets/\n")
    assert first.count("__pycache__/") == 1  # already managed, not duplicated
    assert scaffold.gitignore(first) == first
    assert scaffold.gitignore("") == scaffold.gitignore()


def _ks(name: str, parent: str | None) -> dict:
    labels = {"kustomize.toolkit.fluxcd.io/name": parent, "kustomize.toolkit.fluxcd.io/namespace": "flux-system"}
    return {"metadata": {"name": name, "namespace": "flux-system", "labels": labels if parent else {}}}


def _obj(applied_by: str | None) -> dict:
    if applied_by is None:
        return {"metadata": {"name": "x"}}
    return {
        "metadata": {
            "name": "x",
            "labels": {
                "kustomize.toolkit.fluxcd.io/name": applied_by,
                "kustomize.toolkit.fluxcd.io/namespace": "flux-system",
            },
        }
    }


def test_flux_ownership_skips_only_hand_written_entrypoints():
    owner = FluxOwnership([
        _ks("flux-system", "flux-system"),  # bootstrap root applies itself
        _ks("cluster", "flux-system"),
        _ks("cluster-resources", "flux-system"),
        _ks("cluster-shared", "flux-system"),
        _ks("cluster-test", "flux-system"),
        _ks("emby", "cluster"),
        _ks("sonarr", "cluster"),
        _ks("sonarr-test", "cluster-test"),
    ])
    assert owner.foreign_reason(_obj("emby")) is None
    assert owner.foreign_reason(_obj("sonarr")) is None
    assert owner.foreign_reason(_obj("cluster-resources")) is None
    assert owner.foreign_reason(_obj(None)) is None
    assert owner.foreign_reason(_obj("unknown-ks")) is None  # can't resolve: capture as before
    assert "cluster-shared" in owner.foreign_reason(_obj("cluster-shared"))
    assert "cluster-test" in owner.foreign_reason(_obj("cluster-test"))
    assert "cluster-test" in owner.foreign_reason(_obj("sonarr-test"))
    # Flux's own namespace/RBAC, applied straight from gotk-components.yaml.
    assert "flux-system" in owner.foreign_reason(_obj("flux-system"))


def test_flux_ownership_is_empty_without_flux():
    assert FluxOwnership([]).foreign_reason(_obj("cluster-test")) is None
