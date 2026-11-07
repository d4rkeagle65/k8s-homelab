"""ansible/inventory_plugins/netbox_fallback.py: NetBox's inventory, answered from the last-good
copy when NetBox can't answer, and only then. The collection's plugin and Ansible's Display are
stood in for, so this runs wherever the suite does."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import time
import types
import urllib.error
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "ansible/inventory_plugins/netbox_fallback.py"
URLS = ["https://netbox.example.invalid/api/dcim/devices/?limit=0", "https://netbox.example.invalid/api/ipam/ip-addresses/"]


class AnsibleError(Exception):
    pass


def _stub_modules(monkeypatch, warnings):
    """The collection's nb_inventory as the plugin uses it: open_url, AnsibleError, DOCUMENTATION,
    and an InventoryModule whose parse fetches each URL and whose _fetch_information calls open_url."""
    nb = types.ModuleType("nb_inventory")
    nb.DOCUMENTATION = "name: nb_inventory\nchoices: ['netbox.netbox.nb_inventory']\n"
    nb.AnsibleError = AnsibleError
    nb.answers, nb.failures = {}, {}

    def open_url(url, *a, **kw):
        if url in nb.failures:
            raise nb.failures[url]
        return io.BytesIO(json.dumps(nb.answers[url]).encode())

    nb.open_url = open_url

    class InventoryModule:
        def parse(self, inventory, loader, path, cache=True):
            self.got = {url: self._fetch_information(url) for url in URLS}

        def _fetch_information(self, url):
            try:
                return json.loads(nb.open_url(url).read())
            except urllib.error.HTTPError as e:
                raise AnsibleError(f"wrapped: {e}") from e

    nb.InventoryModule = InventoryModule
    display = types.ModuleType("display")
    display.Display = lambda: types.SimpleNamespace(warning=warnings.append)
    for leaf, module in (("ansible.utils.display", display),
                         ("ansible_collections.netbox.netbox.plugins.inventory.nb_inventory", nb)):
        parts = leaf.split(".")
        for i in range(1, len(parts)):  # each package, holding the next as an attribute
            name = ".".join(parts[:i])
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
        monkeypatch.setitem(sys.modules, leaf, module)
        for i in range(len(parts) - 1, 0, -1):
            setattr(sys.modules[".".join(parts[:i])], parts[i], sys.modules[".".join(parts[:i + 1])])
    return nb


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    warnings = []
    nb = _stub_modules(monkeypatch, warnings)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    copy = tmp_path / "last-good.json"
    monkeypatch.setenv("NETBOX_INVENTORY_FALLBACK", str(copy))
    spec = importlib.util.spec_from_file_location("netbox_fallback_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    nb.answers = {URLS[0]: [{"name": "pve1"}], URLS[1]: [{"address": "192.0.2.5/24"}]}

    def run():
        inv = m.InventoryModule()
        inv.parse(None, None, "netbox.yml")
        return inv.got

    return types.SimpleNamespace(m=m, nb=nb, copy=copy, warnings=warnings, run=run)


def test_its_documentation_names_this_plugin(plugin):
    assert "name: netbox_fallback" in plugin.m.DOCUMENTATION and "choices: ['netbox_fallback']" in plugin.m.DOCUMENTATION


def test_an_answer_is_kept_as_the_last_good_copy(plugin):
    got = plugin.run()
    assert got[URLS[0]] == [{"name": "pve1"}]
    saved = json.loads(plugin.copy.read_text(encoding="utf-8"))
    assert saved["answers"] == {URLS[0]: [{"name": "pve1"}], URLS[1]: [{"address": "192.0.2.5/24"}]}
    assert abs(saved["saved_at"] - time.time()) < 60
    if os.name != "nt":  # Windows can't set POSIX file modes; the 0o600 intent holds on the Linux host
        assert oct(plugin.copy.stat().st_mode & 0o777) == "0o600"
    assert plugin.warnings == []


@pytest.mark.parametrize("outage", [
    urllib.error.HTTPError(URLS[0], 502, "Bad Gateway", {}, io.BytesIO(b"")),
    urllib.error.URLError(ConnectionRefusedError("refused")),
    TimeoutError("timed out"),
])
def test_netbox_down_is_answered_from_the_copy_with_its_age(plugin, outage):
    plugin.run()
    plugin.nb.answers[URLS[0]] = [{"name": "changed while down"}]
    plugin.nb.failures = {URLS[0]: outage}
    before = plugin.copy.read_text(encoding="utf-8")
    got = plugin.run()
    assert got[URLS[0]] == [{"name": "pve1"}], "the last good answer"
    (warning,) = plugin.warnings
    assert "1 request(s) answered from the last-good copy" in warning and " h old " in warning
    assert plugin.copy.read_text(encoding="utf-8") == before, "a run that used the copy doesn't overwrite it"


@pytest.mark.parametrize("code", [401, 403, 404])
def test_anything_else_netbox_says_fails_as_the_collection_does(plugin, code):
    plugin.run()
    plugin.nb.failures = {URLS[0]: urllib.error.HTTPError(URLS[0], code, "no", {}, io.BytesIO(b""))}
    with pytest.raises(AnsibleError, match="wrapped"):
        plugin.run()
    assert plugin.warnings == []


def test_an_outage_with_no_copy_of_the_request_fails(plugin):
    plugin.nb.failures = {URLS[1]: urllib.error.URLError("refused")}
    with pytest.raises(AnsibleError, match="has no answer for"):
        plugin.run()


def test_a_copy_that_cant_be_written_warns_and_the_run_goes_on(plugin, tmp_path, monkeypatch):
    monkeypatch.setenv("NETBOX_INVENTORY_FALLBACK", str(tmp_path / "missing-folder" / "copy.json"))
    got = plugin.run()
    assert got[URLS[0]] == [{"name": "pve1"}]
    (warning,) = plugin.warnings
    assert "Couldn't keep the last-good NetBox copy" in warning


def test_an_unreadable_copy_is_no_copy(plugin):
    plugin.copy.write_text("{not json", encoding="utf-8")
    plugin.nb.failures = {URLS[0]: urllib.error.URLError("refused")}
    with pytest.raises(AnsibleError, match="has no answer for"):
        plugin.run()
