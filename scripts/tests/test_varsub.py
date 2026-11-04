"""varsub.load_settings: capture reads the cluster-settings ConfigMap from git, where those
values live, never from the cluster."""

from __future__ import annotations

from k8s_backup import varsub


def test_load_settings_reads_the_committed_configmap(repo_root):
    doc = varsub.load_settings(repo_root)
    assert doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == varsub.SETTINGS_CONFIGMAP_NAME
    assert doc["data"], "the committed cluster-settings holds no values, so nothing was checked"


def test_load_settings_reads_a_copy_and_is_none_without_one(tmp_path):
    assert varsub.load_settings(tmp_path) is None
    path = tmp_path.joinpath(*varsub.SETTINGS_PATH)
    path.parent.mkdir(parents=True)
    path.write_text("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: cluster-settings\ndata:\n  TZ: UTC\n",
                    encoding="utf-8")
    assert varsub.load_settings(tmp_path)["data"] == {"TZ": "UTC"}
