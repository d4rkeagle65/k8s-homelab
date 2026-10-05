"""layout.py: where a namespace's folder goes, and capture refusing a namespace
it can't place before it writes anything under kubernetes/."""

from __future__ import annotations

import pytest

from k8s_backup import capture, kube, layout
from k8s_backup.procutil import ToolError

CATEGORY = layout.CATEGORY_LABEL


def _labels(category):
    return {CATEGORY: category}


def test_a_labelled_namespace_goes_in_its_category(tmp_path):
    assert layout.namespace_dir(tmp_path, "immich", _labels("apps")) == tmp_path / "kubernetes/prod/apps/immich"


@pytest.mark.parametrize("labels", [None, {}, {CATEGORY: "databases"}, {CATEGORY: ""}])
def test_a_namespace_without_a_valid_category_is_refused(tmp_path, labels):
    with pytest.raises(layout.LayoutError, match="immich"):
        layout.namespace_dir(tmp_path, "immich", labels)


def test_a_label_that_disagrees_with_the_existing_folder_is_refused(tmp_path):
    (tmp_path / "kubernetes/prod/apps/redis-ha").mkdir(parents=True)
    with pytest.raises(layout.LayoutError, match="git mv"):
        layout.namespace_dir(tmp_path, "redis-ha", _labels("services"))
    # The label that matches the folder is fine.
    assert layout.namespace_dir(tmp_path, "redis-ha", _labels("apps")) == tmp_path / "kubernetes/prod/apps/redis-ha"


def test_check_layout_names_stray_folders_and_a_namespace_in_two_categories(tmp_path):
    for rel in ("kubernetes/prod/apps/emby", "kubernetes/prod/system/emby", "kubernetes/prod/misc/x"):
        (tmp_path / rel).mkdir(parents=True)
    problems = layout.check_layout(tmp_path)
    assert any("misc" in p for p in problems)
    assert any("emby" in p and "apps" in p and "system" in p for p in problems)
    assert layout.check_layout(tmp_path / "nothing-here") == []


def test_common_labels_follow_the_folder(tmp_path):
    ns_dir = tmp_path / "kubernetes/prod/services/cnpg-system"
    assert layout.common_labels(ns_dir, layout.MANAGED_HELM) == {
        layout.ENV_LABEL: "prod",
        CATEGORY: "services",
        layout.PART_OF_LABEL: "cnpg-system",
        layout.MANAGED_BY_LABEL: "helm",
    }


def _fake_namespaces(monkeypatch, by_name):
    monkeypatch.setattr(kube, "get_namespace", lambda context, ns: by_name.get(ns))


def test_capture_stops_before_writing_when_a_namespace_cant_be_placed(tmp_path, monkeypatch):
    _fake_namespaces(monkeypatch, {
        "emby": {"metadata": {"name": "emby", "labels": _labels("apps")}},
        "media": {"metadata": {"name": "media"}},  # no category
    })
    with pytest.raises(ToolError) as err:
        capture._namespace_dirs(tmp_path, None, {"emby", "media", "gone"})
    text = str(err.value)
    assert "media" in text and "no homelab.local/category label" in text
    # A namespace that no longer exists is named as such, not as unlabelled.
    assert "namespace gone doesn't exist in the cluster" in text
    assert "emby" not in text.split("\n", 1)[1]  # the placeable one isn't a problem
    assert not (tmp_path / "kubernetes").exists()


def test_capture_places_every_labelled_namespace(tmp_path, monkeypatch):
    _fake_namespaces(monkeypatch, {
        "emby": {"metadata": {"name": "emby", "labels": _labels("apps")}},
        "cnpg-system": {"metadata": {"name": "cnpg-system", "labels": _labels("services")}},
    })
    dirs = capture._namespace_dirs(tmp_path, None, {"emby", "cnpg-system"})
    assert dirs == {
        "emby": tmp_path / "kubernetes/prod/apps/emby",
        "cnpg-system": tmp_path / "kubernetes/prod/services/cnpg-system",
    }


def test_capture_names_the_folder_of_a_namespace_that_is_gone(tmp_path, monkeypatch):
    (tmp_path / "kubernetes/prod/apps/babybuddy-mcp/babybuddy-mcp").mkdir(parents=True)
    _fake_namespaces(monkeypatch, {})
    with pytest.raises(ToolError) as err:
        capture._namespace_dirs(tmp_path, None, {"babybuddy-mcp"})
    assert "remove its folder (kubernetes/prod/apps/babybuddy-mcp)" in str(err.value)
