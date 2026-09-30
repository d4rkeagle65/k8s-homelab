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
        {"kind": "ConfigMap", "name": "cluster-settings", "optional": True},
        {"kind": "Secret", "name": "cluster-secrets", "optional": True},
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


OPERATOR_RELEASES = {
    "kubernetes/apps/cnpg-system/namespace.yaml": "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: cnpg-system\n",
    "kubernetes/apps/cnpg-system/cnpg/release.yaml": (
        "release: cnpg\nnamespace: cnpg-system\nchart: cloudnative-pg\nchartVersion: 1.0.0\n"
        "chartSource:\n  resolved: true\n  kind: HelmRepository\n  name: cnpg\n"
    ),
    "kubernetes/apps/cnpg-system/cnpg/app/values.yaml": "{}\n",
    "kubernetes/apps/cnpg-system/cnpg/app/cluster-own.yaml": (
        "apiVersion: postgresql.cnpg.io/v1\nkind: Cluster\nmetadata:\n  name: own\n"
    ),
}


def _depends_on(root: Path, rel: str) -> list:
    return _yaml.load((root / rel).read_text(encoding="utf-8"))["spec"]["dependsOn"]


def test_ks_depends_on_the_operator_whose_api_it_uses(tmp_path):
    _write(tmp_path, CAPTURED_RELEASE | OPERATOR_RELEASES | {
        "kubernetes/apps/emby/emby/app/cluster-emby.yaml": (
            "apiVersion: postgresql.cnpg.io/v1\nkind: Cluster\nmetadata:\n  name: emby\n"
        ),
        # Promoted, with cert-manager's API but no cert-manager in the repo.
        "kubernetes/apps/emby/emby-extras/.promote": "",
        "kubernetes/apps/emby/emby-extras/app/objects.yaml": (
            "apiVersion: cert-manager.io/v1\nkind: Certificate\nmetadata:\n  name: a\n---\n"
            "apiVersion: postgresql.cnpg.io/v1\nkind: Cluster\nmetadata:\n  name: b\n"
        ),
    })
    generate.run(tmp_path, dry_run=False, verbose=False)

    assert _depends_on(tmp_path, "kubernetes/apps/emby/emby/ks.yaml") == [{"name": "cnpg"}]
    assert _depends_on(tmp_path, "kubernetes/apps/emby/emby-extras/ks.yaml") == [{"name": "cnpg"}]
    # The operator never depends on itself, and waits for its HelmRelease
    # so that being Ready means its CRDs exist.
    operator_ks = _yaml.load((tmp_path / "kubernetes/apps/cnpg-system/cnpg/ks.yaml").read_text(encoding="utf-8"))
    assert operator_ks["spec"]["dependsOn"] == []
    assert operator_ks["spec"]["wait"] is True
    emby_ks = _yaml.load((tmp_path / "kubernetes/apps/emby/emby/ks.yaml").read_text(encoding="utf-8"))
    assert "wait" not in emby_ks["spec"]


def test_ks_ignores_operator_annotations_and_helm_values(tmp_path):
    _write(tmp_path, CAPTURED_RELEASE | {
        "kubernetes/apps/cert-manager/namespace.yaml": (
            "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: cert-manager\n"
        ),
        "kubernetes/apps/cert-manager/cert-manager/release.yaml": (
            "release: cert-manager\nnamespace: cert-manager\nchart: cert-manager\nchartVersion: 1.0.0\n"
            "chartSource:\n  resolved: true\n  kind: HelmRepository\n  name: jetstack\n"
        ),
        "kubernetes/apps/cert-manager/cert-manager/app/values.yaml": "{}\n",
        "kubernetes/apps/emby/emby/app/values.yaml": (
            "ingress:\n  annotations:\n    cert-manager.io/cluster-issuer: letsencrypt\n"
        ),
        "kubernetes/apps/emby/emby/app/ingress-emby.yaml": (
            "apiVersion: networking.k8s.io/v1\nkind: Ingress\nmetadata:\n  name: emby\n"
            "  annotations:\n    cert-manager.io/cluster-issuer: letsencrypt\n"
        ),
    })
    generate.run(tmp_path, dry_run=False, verbose=False)

    assert _depends_on(tmp_path, "kubernetes/apps/emby/emby/ks.yaml") == []


def test_handwritten_operator_is_found_by_its_helmrelease(tmp_path):
    _write(tmp_path, CAPTURED_RELEASE | {
        "kubernetes/apps/longhorn-system/namespace.yaml": (
            "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: longhorn-system\n"
        ),
        "kubernetes/apps/longhorn-system/longhorn/.handwritten": "",
        "kubernetes/apps/longhorn-system/longhorn/ks.yaml": "kind: Kustomization\nmetadata:\n  name: longhorn\n",
        "kubernetes/apps/longhorn-system/longhorn/app/helmrelease.yaml": (
            "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\nmetadata:\n  name: longhorn\n"
            "spec:\n  chart:\n    spec:\n      chart: longhorn\n"
        ),
        "kubernetes/apps/emby/emby/app/recurringjob.yaml": (
            "apiVersion: longhorn.io/v1beta2\nkind: RecurringJob\nmetadata:\n  name: nightly\n"
        ),
    })
    generate.run(tmp_path, dry_run=False, verbose=False)

    assert _depends_on(tmp_path, "kubernetes/apps/emby/emby/ks.yaml") == [{"name": "longhorn"}]
    # Still never written to.
    assert "dependsOn" not in (tmp_path / "kubernetes/apps/longhorn-system/longhorn/ks.yaml").read_text()


def test_namespaces_are_never_pruned(tmp_path):
    # Both Kustomizations that apply Namespaces patch them prune: disabled,
    # so neither can delete one (and everything in it) when it drops it.
    _write(tmp_path, CAPTURED_RELEASE | {
        "kubernetes/cluster/namespace/emby.yaml": "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: emby\n",
    })
    generate.run(tmp_path, dry_run=False, verbose=False)

    for rel in ("kubernetes/apps/kustomization.yaml", "kubernetes/cluster/kustomization.yaml"):
        doc = _yaml.load((tmp_path / rel).read_text(encoding="utf-8"))
        [patch] = doc["patches"]
        assert patch["target"] == {"kind": "Namespace"}
        body = _yaml.load(patch["patch"])
        assert body["kind"] == "Namespace"
        assert body["metadata"]["annotations"] == {"kustomize.toolkit.fluxcd.io/prune": "disabled"}
