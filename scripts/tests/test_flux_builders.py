"""The flux.py builders capture writes with, which generate never calls, so
test_generate_reproduces.py can't see them: the source repositories and the note on a chart
no repository was found for."""

from __future__ import annotations

from ruamel.yaml import YAML

from k8s_backup import constants, flux

_yaml = YAML(typ="safe", pure=True)


def test_helm_repository_writes_what_capture_committed(repo_root):
    """Each committed HelmRepository of the shape capture writes (an interval and a url, the
    interval capture's) is exactly what helm_repository builds for its name and url."""
    checked = 0
    for path in sorted((repo_root / "kubernetes/flux/meta/repositories").glob("*.yaml")):
        doc = _yaml.load(path.read_text(encoding="utf-8"))
        if path.name == "kustomization.yaml" or doc.get("kind") != "HelmRepository":
            continue
        spec = doc["spec"]
        if set(spec) != {"interval", "url"} or spec["interval"] != constants.HELMREPOSITORY_INTERVAL:
            continue  # edited by hand since capture wrote it
        checked += 1
        built = flux.helm_repository(name=doc["metadata"]["name"], url=spec["url"],
                                     interval=constants.HELMREPOSITORY_INTERVAL)
        assert built == doc, path.name
    assert checked >= 5, f"only {checked} HelmRepositories of capture's shape, so little was checked"


def test_oci_repository_with_and_without_a_tag():
    base = {"apiVersion": "source.toolkit.fluxcd.io/v1", "kind": "OCIRepository",
            "metadata": {"name": "charts", "namespace": "flux-system"}}
    assert flux.oci_repository(name="charts", url="oci://registry.example/charts", interval="1h") == {
        **base, "spec": {"interval": "1h", "url": "oci://registry.example/charts"}}
    assert flux.oci_repository(name="charts", url="oci://registry.example/charts", interval="1h", tag="1.2.3") == {
        **base, "spec": {"interval": "1h", "url": "oci://registry.example/charts", "ref": {"tag": "1.2.3"}}}


def test_an_unresolved_source_names_the_chart_and_version():
    reason = flux.unresolved_source_reason("emby", "1.0.0")
    assert "chart 'emby' version '1.0.0'" in reason and "sourceRef" in reason
