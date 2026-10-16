"""talos/gen-config.py's cluster settings, read from TF_VAR_site's talos block (the
same values tofu/proxmox uses) rather than kubeadm's leftover ConfigMap.
Documentation names and addresses only."""

from __future__ import annotations

import importlib.util
import json
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
