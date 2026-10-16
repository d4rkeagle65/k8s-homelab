"""proxmox-endpoints.py, the helper that keeps the proxmox route's EndpointSlice
to the Proxmox hosts that answer: its pure parts, and the guard that a failed
NetBox lookup never empties the route. Documentation addresses only (RFC 5737)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[2] / "kubernetes" / "prod" / "system" / "external-services"
          / "external-services" / "app" / "proxmox-endpoints.py")


def load():
    spec = importlib.util.spec_from_file_location("proxmox_endpoints_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load()


def test_addresses_strips_lengths_drops_hosts_without_one_and_sorts():
    devices = [{"primary_ip4": {"address": "192.0.2.12/24"}}, {"primary_ip4": None},
               {"primary_ip4": {"address": "192.0.2.10/24"}}, {}]
    assert m.addresses(devices) == ["192.0.2.10", "192.0.2.12"]


def test_refresh_takes_a_new_list():
    # The positive control: without it, "keeps the old list" would pass for a refresh that never updates.
    assert m.refresh(["192.0.2.10"], lambda: ["192.0.2.10", "192.0.2.11"], "hypervisor") == ["192.0.2.10", "192.0.2.11"]


def test_refresh_keeps_the_last_list_when_netbox_fails_or_lists_nothing():
    def broken():
        raise OSError("NetBox is down")
    assert m.refresh(["192.0.2.10"], broken, "hypervisor") == ["192.0.2.10"]
    assert m.refresh(["192.0.2.10"], lambda: [], "hypervisor") == ["192.0.2.10"]


def test_endpoint_slice_lists_each_answering_host_ready_and_names_its_service():
    s = m.endpoint_slice("ns", "proxmox", 8006, ["192.0.2.10", "192.0.2.11"])
    assert s["metadata"]["labels"]["kubernetes.io/service-name"] == "proxmox"
    # Not the EndpointSlice controller's label: it would take the slice over.
    assert s["metadata"]["labels"]["endpointslice.kubernetes.io/managed-by"] != "endpointslice-controller.k8s.io"
    assert m.written(s) == ["192.0.2.10", "192.0.2.11"]
    assert all(e["conditions"]["ready"] for e in s["endpoints"])
    assert s["ports"] == [{"name": "https", "port": 8006, "protocol": "TCP"}]


def test_problem_is_none_for_a_serving_host_and_says_why_otherwise(monkeypatch):
    import urllib.error

    def serving(url, timeout, context):
        raise urllib.error.HTTPError(url, 401, "no ticket", {}, None)
    monkeypatch.setattr(m.urllib.request, "urlopen", serving)
    assert m.problem("192.0.2.10", 8006) is None

    def broken(url, timeout, context):
        raise urllib.error.HTTPError(url, 502, "bad gateway", {}, None)
    monkeypatch.setattr(m.urllib.request, "urlopen", broken)
    assert m.problem("192.0.2.10", 8006) == "HTTP 502"

    def refused(url, timeout, context):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))
    monkeypatch.setattr(m.urllib.request, "urlopen", refused)
    assert "refused" in m.problem("192.0.2.10", 8006)
