"""talos/gen-config.py's cluster settings, read from TF_VAR_site's talos block (the
same values tofu/proxmox uses) rather than kubeadm's leftover ConfigMap.
Documentation names and addresses only."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "talos" / "gen-config.py"


def load():
    spec = importlib.util.spec_from_file_location("talos_gen_config_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load()
SITE = {"networks": {}, "talos": {"cluster_endpoint": "k8s.example.org:6443", "dns_domain": "cluster.example",
                                  "pod_subnet": "10.244.0.0/16", "service_subnet": "10.96.0.0/12"}}


def test_settings_come_from_the_talos_block():
    s = m.cluster_settings(json.dumps(SITE))
    assert s == {"clusterName": "kubernetes", "controlPlaneEndpoint": "k8s.example.org:6443",
                 "dnsDomain": "cluster.example", "podSubnet": "10.244.0.0/16", "serviceSubnet": "10.96.0.0/12"}


def test_a_missing_value_or_no_talos_block_stops_it():
    broken = json.loads(json.dumps(SITE)); broken["talos"]["dns_domain"] = ""
    with pytest.raises(SystemExit):
        m.cluster_settings(json.dumps(broken))
    with pytest.raises(SystemExit):
        m.cluster_settings(json.dumps({"networks": {}}))
    with pytest.raises(SystemExit):
        m.cluster_settings("not json")


# --- the patches, and what the cluster is read for -------------------------------------------
# Documentation addresses (RFC 5737); a kubectl stand-in on the module replaces the cluster.

from ruamel.yaml import YAML

_yaml = YAML(typ="safe", pure=True)
NET = "192.0.2.0/24"
# The secretbox secret's shape (32 bytes, base64), made at run time.
SECRETBOX = "QUJD" * 11


def _docs(text):
    return [d for d in _yaml.load_all(text) if d is not None]


def _kubectl(answers):
    """A kubectl stand-in: the JSON answer for the first of its arguments that names one."""
    def fake(context, *args):
        for key, value in answers.items():
            if key in args:
                return json.dumps(value)
        raise AssertionError(f"unexpected kubectl call: {args}")
    return fake


def _nodes(*addresses):
    return {"items": [{"metadata": {"name": f"n{i}"},
                       "status": {"addresses": [{"type": "Hostname", "address": f"n{i}"}]
                                  + [{"type": "InternalIP", "address": a} for a in ips]}}
                      for i, ips in enumerate(addresses)]}


def _apiservers(*issuer_lists):
    return {"items": [{"spec": {"nodeName": f"cp{i}", "containers": [{"command": ["kube-apiserver"] + [
        f"--service-account-issuer={x}" for x in issuers]}]}} for i, issuers in enumerate(issuer_lists)]}


def test_site_patch_carries_the_settings_and_the_nodes_network():
    settings = m.cluster_settings(json.dumps(SITE))
    network, node = _docs(m.site_patch(settings, NET))
    assert network == {"apiVersion": "v1alpha1", "kind": "KubeNetworkConfig", "dnsDomain": "cluster.example",
                       "podSubnets": ["10.244.0.0/16"], "serviceSubnets": ["10.96.0.0/12"]}
    assert node == {"apiVersion": "v1alpha1", "kind": "KubeNodeConfig", "nodeIP": {"validSubnets": [NET]}}


def test_etcd_patch_advertises_on_the_nodes_network():
    assert _docs(m.etcd_patch(NET)) == [{"cluster": {"etcd": {"advertisedSubnets": [NET]}}}]


def _providers(text):
    (doc,) = _docs(text)
    assert (doc["kind"], doc["config"]["kind"]) == ("KubeEtcdEncryptionConfig", "EncryptionConfiguration")
    (rule,) = doc["config"]["resources"]
    assert rule["resources"] == ["secrets"]
    return rule["providers"]


@pytest.mark.parametrize("order", [["secretbox", "identity"], ["identity", "secretbox"], ["identity"]])
def test_encryption_patch_lists_the_providers_in_the_files_order(tmp_path, monkeypatch, order):
    listed = tmp_path / "encryption-order.txt"
    listed.write_text("# a comment\n\n" + "\n".join(order) + "\n", encoding="utf-8")
    monkeypatch.setattr(m, "ENCRYPTION_ORDER", listed)
    text, got_order = m.encryption_patch(f"secrets:\n    secretboxencryptionsecret: {SECRETBOX}\n")
    providers = _providers(text)
    assert got_order == order
    assert [next(iter(p)) for p in providers] == order
    for p in providers:
        if "secretbox" in p:
            assert p["secretbox"] == {"keys": [{"name": "key2", "secret": SECRETBOX}]}
        else:
            assert p == {"identity": {}}


def test_encryption_patch_reads_the_committed_order():
    order = [l.strip() for l in m.ENCRYPTION_ORDER.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.strip().startswith("#")]
    text, got = m.encryption_patch(f"    secretboxencryptionsecret: {SECRETBOX}\n")
    assert got == order and [next(iter(p)) for p in _providers(text)] == order


@pytest.mark.parametrize("listed, secrets", [
    ("aescbc\nidentity\n", f"    secretboxencryptionsecret: {SECRETBOX}\n"),
    ("# nothing listed\n", f"    secretboxencryptionsecret: {SECRETBOX}\n"),
    ("secretbox\n", "    bootstraptoken: x\n"),
])
def test_encryption_patch_stops_on_an_order_or_secrets_it_cant_build(tmp_path, monkeypatch, listed, secrets):
    path = tmp_path / "encryption-order.txt"
    path.write_text(listed, encoding="utf-8")
    monkeypatch.setattr(m, "ENCRYPTION_ORDER", path)
    with pytest.raises(SystemExit):
        m.encryption_patch(secrets)


def test_the_secretbox_key_is_named_as_opentofu_names_it():
    """Kubernetes stores each encrypted Secret tagged with its key's name, so a control plane
    built by OpenTofu and one built here must name the key alike, or neither reads the other's."""
    tf = (SCRIPT.parents[1] / "tofu" / "proxmox" / "talos-config.tf").read_text(encoding="utf-8")
    names = re.findall(r'secretbox\s*=\s*\{\s*secretbox\s*=\s*\{\s*keys\s*=\s*\[\{\s*name\s*=\s*"([^"]+)"', tf)
    assert len(names) == 1, "talos-config.tf's secretbox provider not found"
    text, _ = m.encryption_patch(f"    secretboxencryptionsecret: {SECRETBOX}\n")
    built = [p["secretbox"]["keys"][0]["name"] for p in _providers(text) if "secretbox" in p]
    assert built == names


def test_image_version_reads_the_installer_line(tmp_path, monkeypatch):
    assert re.fullmatch(r"v\d+\.\d+\.\d+", m.image_version())
    common = tmp_path / "common.yaml"
    common.write_text("machine:\n  install:\n    image: factory.example/installer/abc:v1.2.3\n", encoding="utf-8")
    monkeypatch.setattr(m, "COMMON", common)
    assert m.image_version() == "v1.2.3"
    common.write_text("machine:\n  install:\n    image: factory.example/installer/abc:latest\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        m.image_version()


def test_check_issuer_wants_every_api_servers_first_issuer(monkeypatch):
    want = "https://k8s.example.org:6443"
    monkeypatch.setattr(m, "kubectl", _kubectl({"pods": _apiservers([want], [want, "https://old.example"])}))
    m.check_issuer(None, "k8s.example.org:6443")
    for bad in (_apiservers([want], ["https://old.example", want]), _apiservers([want], []), {"items": []}):
        monkeypatch.setattr(m, "kubectl", _kubectl({"pods": bad}))
        with pytest.raises(SystemExit):
            m.check_issuer(None, "k8s.example.org:6443")


def test_node_network_is_the_one_network_every_node_is_in(monkeypatch):
    monkeypatch.setattr(m, "kubectl", _kubectl({"nodes": _nodes(["192.0.2.10"], ["192.0.2.11"], ["192.0.2.200"])}))
    assert m.node_network(None, 24) == (NET, 3)
    for bad in (_nodes(["192.0.2.10"], ["198.51.100.10"]), _nodes(["192.0.2.10", "192.0.2.11"]), _nodes([])):
        monkeypatch.setattr(m, "kubectl", _kubectl({"nodes": bad}))
        with pytest.raises(SystemExit):
            m.node_network(None, 24)


def test_k8s_version_drops_the_v(monkeypatch):
    monkeypatch.setattr(m, "kubectl", _kubectl({"version": {"serverVersion": {"gitVersion": "v1.34.2"}}}))
    assert m.k8s_version(None) == "1.34.2"


def test_kubectl_passes_the_context_only_when_given(monkeypatch):
    calls = []
    monkeypatch.setattr(m, "run", lambda argv: calls.append(argv) or "")
    m.kubectl("lab", "get", "nodes")
    m.kubectl(None, "get", "nodes")
    assert calls == [["kubectl", "--context", "lab", "get", "nodes"], ["kubectl", "get", "nodes"]]


def test_run_returns_stdout_and_stops_on_a_failure_or_a_missing_tool():
    assert m.run([sys.executable, "-c", "print('out')"]).strip() == "out"
    with pytest.raises(SystemExit):
        m.run([sys.executable, "-c", "import sys; sys.exit(3)"])
    with pytest.raises(SystemExit):
        m.run(["no-such-tool-for-this-test"])


def _main_world(tmp_path, monkeypatch, *, talosctl="v1.14.4", generated=None,
                issuer="https://k8s.example.org:6443"):
    """main() against a stand-in cluster and talosctl. `generated` gives controlplane.yaml's text
    from the encryption patch talosctl was handed; by default it carries that patch as it is."""
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text(f"secrets:\n    secretboxencryptionsecret: {SECRETBOX}\n", encoding="utf-8")
    site = tmp_path / "site.json"
    site.write_text(json.dumps(SITE), encoding="utf-8")
    common = tmp_path / "common.yaml"
    common.write_text("machine:\n  install:\n    image: factory.example/installer/abc:v1.14.4\n", encoding="utf-8")
    monkeypatch.setattr(m, "COMMON", common)
    monkeypatch.setattr(m.shutil, "which", lambda tool: "/usr/bin/" + tool)
    monkeypatch.setattr(m, "kubectl", _kubectl({
        "pods": _apiservers([issuer]),
        "nodes": _nodes(["192.0.2.10"], ["192.0.2.11"]),
        "version": {"serverVersion": {"gitVersion": "v1.34.2"}}}))
    calls = []

    def run(argv):
        calls.append(argv)
        if argv[:2] == ["talosctl", "version"]:
            return talosctl + "\n"
        if argv[:3] == ["talosctl", "gen", "config"]:
            out = Path(argv[argv.index("--output-dir") + 1])
            patch = (out / "site-patch-encryption.yaml").read_text(encoding="utf-8")
            (out / "controlplane.yaml").write_text(
                "machine: {}\n---\n" + (generated(patch) if generated else patch), encoding="utf-8")
            (out / "worker.yaml").write_text("machine: {}\n", encoding="utf-8")
        return ""

    monkeypatch.setattr(m, "run", run)
    monkeypatch.setattr(sys, "argv", ["gen-config.py", "--secrets", str(secrets), "--site", str(site)])
    return calls


def test_main_writes_and_validates_both_configs(tmp_path, monkeypatch, capsys):
    calls = _main_world(tmp_path, monkeypatch)
    m.main()
    gen = next(c for c in calls if c[:3] == ["talosctl", "gen", "config"])
    assert gen[3:5] == ["kubernetes", "https://k8s.example.org:6443"]
    assert gen[gen.index("--kubernetes-version") + 1] == "1.34.2"
    assert gen[gen.index("--talos-version") + 1] == "v1.14"
    assert [c[3] for c in calls if c[:2] == ["talosctl", "validate"]] == [
        str(tmp_path / "controlplane.yaml"), str(tmp_path / "worker.yaml")]
    assert "2 nodes on 192.0.2.0/24" in capsys.readouterr().out


@pytest.mark.parametrize("why, world", [
    ("talosctl's version isn't the installer image's", {"talosctl": "v1.13.0"}),
    ("an API server issues tokens for another address", {"issuer": "https://old.example:6443"}),
    ("the encryption providers came out in another order",
     {"generated": lambda patch: patch.replace("secretbox", "identity", 1)}),
    ("a Flannel document survived the patches",
     {"generated": lambda patch: patch + "---\nkind: KubeFlannelCNIConfig\n"}),
])
def test_main_stops_when_something_doesnt_match(tmp_path, monkeypatch, why, world):
    _main_world(tmp_path, monkeypatch, **world)
    with pytest.raises(SystemExit):
        m.main()


def test_main_wont_overwrite_outputs_without_force(tmp_path, monkeypatch):
    calls = _main_world(tmp_path, monkeypatch)
    (tmp_path / "worker.yaml").write_text("kept\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        m.main()
    assert calls == [] and (tmp_path / "worker.yaml").read_text(encoding="utf-8") == "kept\n"
