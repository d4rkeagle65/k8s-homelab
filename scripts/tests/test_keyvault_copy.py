"""keyvault_copy.py: the name mapping and its refusals, reading the values from the
ExternalSecret's Secret, and a dry run and a real run against an in-memory Key Vault, with
the script's boundaries (kubectl, the Azure CLI token, the REST calls) replaced. Every run
also checks that no value reaches the output."""

from __future__ import annotations

import base64
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "keyvault_copy.py"
VALUES = {"MEDIA_SONARR_HOST": "s0narr-value-xyz", "Child1": "child-one-value", "INGRESS_IP": "value-three-abc"}


def load():
    spec = importlib.util.spec_from_file_location("keyvault_copy_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


def external_secret(variables, ready="True"):
    return {"spec": {"data": [{"secretKey": v, "remoteRef": {"key": "item", "property": v}} for v in variables]},
            "status": {"conditions": [{"type": "Ready", "status": ready, "reason": "SecretSynced"}]}}


def secret(values, extra=None):
    data = {k: base64.b64encode(v.encode()).decode() for k, v in values.items()}
    data.update(extra or {})
    return {"data": data}


class FakeVault:
    """Key Vault's secrets API in memory: GET, PUT; a name can be set to refuse reads or writes."""

    def __init__(self, held=None, refuse_read=(), refuse_write=(), corrupt=()):
        self.held = dict(held or {})
        self.refuse_read, self.refuse_write, self.corrupt = set(refuse_read), set(refuse_write), set(corrupt)
        self.puts = []

    def __call__(self, method, url, token, body=None):
        assert token == "tok"
        name = url.split("/secrets/")[1].split("?")[0]
        if method == "GET":
            if name in self.refuse_read:
                return 403, {"error": {"code": "Forbidden"}}
            if name not in self.held:
                return 404, {"error": {"code": "SecretNotFound"}}
            return 200, {"value": self.held[name]}
        self.puts.append(name)
        if name in self.refuse_write:
            return 409, {"error": {"code": "Conflict"}}
        self.held[name] = body["value"] + ("!" if name in self.corrupt else "")
        return 200, {}


@pytest.fixture
def run(capsys):
    """Runs main() with the boundaries replaced; returns (exit code, output, vault, kubectl calls)."""

    def _run(argv, vault, es=None, sec=None):
        m = load()
        calls = []

        def fake_kubectl(context, *args):
            calls.append(args)
            return es if "externalsecret" in args else sec

        m.kubectl_json = fake_kubectl
        m.access_token = lambda: "tok"
        m.request = vault
        code = m.main(argv)
        out = capsys.readouterr()
        text = out.out + out.err
        for value in VALUES.values():
            assert value not in text, "a secret value reached the output"
        return code, text, calls

    return _run


m = load()


def test_names_map_underscores_to_dashes():
    assert m.check_names(["MEDIA_SONARR_HOST", "Child1"]) == {"MEDIA_SONARR_HOST": "MEDIA-SONARR-HOST",
                                                                  "Child1": "Child1"}


def test_names_refuse_a_character_key_vault_wont_take():
    with pytest.raises(m.Stop, match="isn't a valid Key Vault name"):
        m.check_names(["A.B"])


def test_names_refuse_two_variables_that_become_one_name_ignoring_case():
    with pytest.raises(m.Stop, match="both become"):
        m.check_names(["A_B", "a-b"])


def test_values_come_from_the_secret_for_the_mapped_variables_only():
    got = m.source_values(external_secret(VALUES), secret(VALUES, {"BUILT_BY_TEMPLATE": "eA=="}))
    assert got == VALUES


def test_values_refuse_an_external_secret_that_isnt_ready():
    with pytest.raises(m.Stop, match="isn't Ready"):
        m.source_values(external_secret(VALUES, ready="False"), secret(VALUES))


def test_values_refuse_a_mapped_variable_the_secret_lacks():
    partial = {k: v for k, v in VALUES.items() if k != "Child1"}
    with pytest.raises(m.Stop, match="Child1"):
        m.source_values(external_secret(VALUES), secret(partial))


def test_values_refuse_a_value_that_isnt_text():
    with pytest.raises(m.Stop, match="isn't UTF-8"):
        m.source_values(external_secret(["K"]), {"data": {"K": base64.b64encode(b"\xff\xfe").decode()}})


def test_dry_run_reports_and_writes_nothing(run):
    vault = FakeVault(held={"Child1": VALUES["Child1"], "INGRESS-IP": "something else"})
    code, text, _ = run(["--vault", "kv-test-01"], vault, external_secret(VALUES), secret(VALUES))
    assert code == 0 and vault.puts == []
    assert "1 missing from Key Vault, 1 different, 1 already the same" in text
    assert "add    MEDIA-SONARR-HOST" in text and "update INGRESS-IP" in text and "dry run" in text


def test_apply_writes_only_what_differs_and_reads_every_name_back(run):
    vault = FakeVault(held={"Child1": VALUES["Child1"]})
    code, text, _ = run(["--vault", "kv-test-01", "--apply"], vault, external_secret(VALUES), secret(VALUES))
    assert code == 0
    assert sorted(vault.puts) == ["INGRESS-IP", "MEDIA-SONARR-HOST"]
    assert vault.held == {"MEDIA-SONARR-HOST": VALUES["MEDIA_SONARR_HOST"], "Child1": VALUES["Child1"],
                          "INGRESS-IP": VALUES["INGRESS_IP"]}
    assert "read back: 3 of 3 match" in text


def test_a_refused_read_stops_before_any_write(run):
    vault = FakeVault(refuse_read={"INGRESS-IP"})
    code, text, _ = run(["--vault", "kv-test-01", "--apply"], vault, external_secret(VALUES), secret(VALUES))
    assert code == 2 and vault.puts == [] and "nothing written" in text and "HTTP 403" in text


def test_a_refused_write_and_a_bad_read_back_both_fail_the_run(run):
    vault = FakeVault(refuse_write={"Child1"}, corrupt={"INGRESS-IP"})
    code, text, _ = run(["--vault", "kv-test-01", "--apply"], vault, external_secret(VALUES), secret(VALUES))
    assert code == 1
    assert "NOT WRITTEN Child1: deleted but recoverable" in text
    assert "MISMATCH Child1" in text and "MISMATCH INGRESS-IP" in text and "read back: 1 of 3 match" in text


def test_a_bad_vault_name_stops_before_reading_anything(run):
    vault = FakeVault()
    code, text, calls = run(["--vault", "https://x.vault.azure.net"], vault, external_secret(VALUES), secret(VALUES))
    assert code == 2 and calls == [] and vault.puts == []
