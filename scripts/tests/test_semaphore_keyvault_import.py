"""scripts/semaphore_keyvault_import.py: each value the config takes from Key Vault is written
there once, read back; one Key Vault already holds the same is left, a different one is never
overwritten; a value the task lacks, a refused write, or a write that didn't take fails the run;
and it prints names, never a value.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("semaphore_keyvault_import", ROOT / "scripts" / "semaphore_keyvault_import.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
sync = sys.modules["semaphore_variable_groups"]

ENV = {"AZURE_TENANT_ID": "t", "AZURE_CLIENT_ID": "c", "AZURE_CLIENT_SECRET": "s", "AZURE_KEYVAULT_URL": "https://vault.example"}
DECLARED = {"variables": {"admin_ssh_keys": {"keyvault": "DEBIAN-ADMIN-SSH-KEYS", "json": True, "secret": False},
                          "loki_push_url": {"template": "https://loki-push.{BASE-DOMAIN}"}},
            "environment": {"HAWSER_TOKEN": {"keyvault": "HAWSER-TOKEN-SYNOLOGY"},
                            "OTHER": {"keyvault": "OTHER-ONE"}}}


class Vault:
    def __init__(self, held=None, readonly=False, drop=False):
        self.held, self.readonly, self.drop, self.puts = dict(held or {}), readonly, drop, []

    def http(self, method, url, headers=None, body=None, form=None, timeout=30):
        if "login.microsoftonline.com" in url:
            return 200, {"access_token": "kv"}
        name = url.split("/secrets/")[1].split("?")[0]
        if method == "PUT":
            self.puts.append(name)
            if self.readonly:
                return 403, None
            if not self.drop:
                self.held[name] = body["value"]
            return 200, {"value": body["value"]}
        return (200, {"value": self.held[name]}) if name in self.held else (404, None)


def run(monkeypatch, capsys, vault, values, group="Synology Baseline", declared=DECLARED, env=ENV):
    monkeypatch.setattr(sync, "http", vault.http)
    code = m.main(json.dumps({"group": group, "declared": declared, "values": values}), dict(env))
    return code, capsys.readouterr().out


VALUES = {"admin_ssh_keys": ["ssh-ed25519 <key> admin"], "HAWSER_TOKEN": "<hawser-value>", "OTHER": "<other-value>"}


def test_each_value_is_written_once_and_read_back(monkeypatch, capsys):
    vault = Vault()
    code, out = run(monkeypatch, capsys, vault, VALUES)
    assert code == 0 and sorted(vault.puts) == ["DEBIAN-ADMIN-SSH-KEYS", "HAWSER-TOKEN-SYNOLOGY", "OTHER-ONE"]
    assert json.loads(vault.held["DEBIAN-ADMIN-SSH-KEYS"]) == VALUES["admin_ssh_keys"]  # json: true
    assert vault.held["HAWSER-TOKEN-SYNOLOGY"] == "<hawser-value>"
    assert "loki_push_url: not imported (a template" in out
    assert "<hawser-value>" not in out and "<key>" not in out  # names only


def test_the_same_value_is_left_and_a_different_one_never_overwritten(monkeypatch, capsys):
    vault = Vault({"HAWSER-TOKEN-SYNOLOGY": "<hawser-value>", "OTHER-ONE": "<something-else>"})
    code, out = run(monkeypatch, capsys, vault, VALUES)
    assert code == 1 and vault.puts == ["DEBIAN-ADMIN-SSH-KEYS"]
    assert "HAWSER-TOKEN-SYNOLOGY already holds it" in out and "OTHER-ONE holds a different value" in out
    assert vault.held["OTHER-ONE"] == "<something-else>"


def test_a_value_the_task_lacks_fails_and_writes_nothing_for_it(monkeypatch, capsys):
    vault = Vault()
    code, out = run(monkeypatch, capsys, vault, dict(VALUES, HAWSER_TOKEN=None, OTHER=""))
    assert code == 1 and "HAWSER-TOKEN-SYNOLOGY" not in vault.puts and "OTHER-ONE" not in vault.puts
    assert out.count("not in this task") == 2


def test_a_read_only_identity_is_told_what_role_the_import_needs(monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, Vault(readonly=True), VALUES)
    assert code == 1 and "Key Vault Secrets Officer" in out


def test_a_write_that_doesnt_read_back_fails(monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, Vault(drop=True), VALUES)
    assert code == 1 and "didn't take" in out


def test_what_it_refuses(monkeypatch, capsys):
    assert run(monkeypatch, capsys, Vault(), VALUES, group="Key Vault Sync")[0] == 2
    assert run(monkeypatch, capsys, Vault(), VALUES, env={k: v for k, v in ENV.items() if k != "AZURE_CLIENT_SECRET"})[0] == 2
    monkeypatch.setattr(sync, "http", Vault().http)
    assert m.main("not json", dict(ENV)) == 2
