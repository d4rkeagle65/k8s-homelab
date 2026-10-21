"""keyvault_copy.py: the name mapping and its refusals, reading the values from the
ExternalSecret's Secret, and a dry run and a real run against an in-memory Key Vault, with
the script's boundaries (kubectl, the Azure CLI token, the REST calls) replaced. Every run
also checks that no value reaches the output."""

from __future__ import annotations

import base64
import importlib.util
import sys
import urllib.error
import urllib.request
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


def external_secret(variables, ready="True", template=None):
    spec = {"data": [{"secretKey": v, "remoteRef": {"key": "item", "property": v}} for v in variables]}
    if template:
        spec["target"] = {"name": "cluster-secrets", "template": {"mergePolicy": "Merge", "data": template}}
    return {"spec": spec, "status": {"conditions": [{"type": "Ready", "status": ready, "reason": "SecretSynced"}]}}


def b64(text):
    return base64.b64encode(text.encode()).decode()


def secret(values, extra=None):
    data = {k: b64(v) for k, v in values.items()}
    data.update(extra or {})
    return {"data": data}


# Child1 encoded by the template, so Key Vault holds it plain and the Secret its base64.
ENCODED = {"Child1": "{{ .Child1 | b64enc }}"}
ENCODED_VALUES = {**VALUES, "Child1": b64(VALUES["Child1"])}


class FakeVault:
    """Key Vault's secrets API in memory: GET, PUT; a name can be set to refuse reads or writes,
    or to get no answer (raising the script's Stop, as its request() does)."""

    def __init__(self, held=None, refuse_read=(), refuse_write=(), corrupt=(), no_answer=()):
        self.held = dict(held or {})
        self.refuse_read, self.refuse_write, self.corrupt = set(refuse_read), set(refuse_write), set(corrupt)
        self.no_answer = set(no_answer)
        self.puts = []
        self.Stop = Exception

    def __call__(self, method, url, token, body=None):
        assert token == "tok"
        name = url.split("/secrets/")[1].split("?")[0]
        if (method, name) in self.no_answer:
            raise self.Stop(f"{name}: no answer from Key Vault to a {method} (timed out)")
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
        vault.Stop = m.Stop
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


def test_values_decode_once_more_a_variable_the_template_b64encs():
    assert m.source_values(external_secret(VALUES, template=ENCODED), secret(ENCODED_VALUES)) == VALUES


def test_values_take_a_template_key_built_from_other_variables_as_no_change():
    built = {"BUILT_BY_TEMPLATE": "x {{ .Child1 | b64enc }}"}
    got = m.source_values(external_secret(VALUES, template=built), secret(VALUES, {"BUILT_BY_TEMPLATE": "eA=="}))
    assert got == VALUES


@pytest.mark.parametrize("form", ["{{ .Child1 }}", "{{.Child1 | b64enc}}", "{{ .INGRESS_IP | b64enc }}"])
def test_values_refuse_a_template_of_a_variables_name_in_another_form(form):
    with pytest.raises(m.Stop, match="Child1: the template builds it other than as"):
        m.source_values(external_secret(VALUES, template={"Child1": form}), secret(ENCODED_VALUES))


def test_values_refuse_a_b64enc_variable_whose_secret_value_isnt_base64():
    with pytest.raises(m.Stop, match="Child1: the template b64encs it, but"):
        m.source_values(external_secret(VALUES, template=ENCODED), secret(VALUES))


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


def test_apply_writes_a_variable_the_template_b64encs_plain(run):
    vault = FakeVault(held={"MEDIA-SONARR-HOST": VALUES["MEDIA_SONARR_HOST"], "INGRESS-IP": VALUES["INGRESS_IP"]})
    code, text, _ = run(["--vault", "kv-test-01", "--apply"], vault, external_secret(VALUES, template=ENCODED),
                        secret(ENCODED_VALUES))
    assert code == 0 and vault.puts == ["Child1"] and vault.held["Child1"] == VALUES["Child1"]
    assert "read back: 3 of 3 match" in text


def test_a_variable_the_template_b64encs_held_plain_is_the_same(run):
    vault = FakeVault(held={"MEDIA-SONARR-HOST": VALUES["MEDIA_SONARR_HOST"], "Child1": VALUES["Child1"],
                            "INGRESS-IP": VALUES["INGRESS_IP"]})
    code, text, _ = run(["--vault", "kv-test-01", "--apply"], vault, external_secret(VALUES, template=ENCODED),
                        secret(ENCODED_VALUES))
    assert code == 0 and vault.puts == []
    assert "3 already the same, 0 held plain" in text and "read back: 3 of 3 match" in text


def test_a_value_rewritten_plain_before_its_template_is_live_is_reported_and_left(run):
    # No template yet, so the Secret holds Child1's base64; Key Vault holds it decoded, as
    # the owner's rewrite leaves it until the batch's pull request merges.
    vault = FakeVault(held={"Child1": VALUES["Child1"], "INGRESS-IP": VALUES["INGRESS_IP"]})
    code, text, _ = run(["--vault", "kv-test-01", "--apply"], vault, external_secret(VALUES), secret(ENCODED_VALUES))
    assert code == 0
    assert "1 held plain with no template to encode them" in text and "  plain  Child1" in text
    assert vault.puts == ["MEDIA-SONARR-HOST"] and vault.held["Child1"] == VALUES["Child1"]
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


def test_a_write_tags_its_secret_as_cluster_secrets_unless_given_tags(monkeypatch):
    bodies = []
    monkeypatch.setattr(m, "request", lambda method, url, token, body=None: bodies.append(body) or (200, {}))
    assert m.write("https://kv-test-01.vault.azure.net", "A", "v", "tok") == ""
    assert m.write("https://kv-test-01.vault.azure.net", "B", "v", "tok", {"source": "dockhand"}) == ""
    assert [b["tags"] for b in bodies] == [{"source": "cluster-secrets"}, {"source": "dockhand"}]


@pytest.mark.parametrize("value, decoded", [("cGxhaW4tdGV4dA==", "plain-text"), ("", ""),
                                            ("not base64!", None), ("//79", None)])
def test_decoded_digest_hashes_only_base64_of_text(value, decoded):
    assert m.decoded_digest(value) == (m.digest(decoded) if decoded is not None else None)


def test_a_bad_vault_name_stops_before_reading_anything(run):
    vault = FakeVault()
    code, text, calls = run(["--vault", "https://x.vault.azure.net"], vault, external_secret(VALUES), secret(VALUES))
    assert code == 2 and calls == [] and vault.puts == []


@pytest.mark.parametrize("failure", [urllib.error.URLError("timed out"), TimeoutError("timed out"),
                                     ConnectionResetError("reset by peer")])
def test_no_answer_from_key_vault_is_a_stop_naming_the_secret_not_the_vault(monkeypatch, failure):
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise failure

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    monkeypatch.setattr(m, "RETRY_PAUSE_SECONDS", 0)
    with pytest.raises(m.Stop) as stopped:
        m.request("GET", "https://kv-test-01.vault.azure.net/secrets/INGRESS-IP?api-version=7.4", "tok")
    assert "INGRESS-IP: no answer from Key Vault to a GET" in str(stopped.value)
    assert "kv-test-01" not in str(stopped.value)
    assert len(calls) == 2  # tried once more before stopping


class Answer:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b'{"value": "v"}'


def test_a_get_that_gets_no_answer_once_is_tried_again(monkeypatch):
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionResetError("reset by peer")
        return Answer()

    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    monkeypatch.setattr(m, "RETRY_PAUSE_SECONDS", 0)
    assert m.request("GET", "https://kv-test-01.vault.azure.net/secrets/INGRESS-IP?api-version=7.4", "tok") == (200, {"value": "v"})
    assert len(calls) == 2


def test_a_put_that_gets_no_answer_is_never_tried_again(monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise ConnectionResetError("reset by peer")

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    monkeypatch.setattr(m, "RETRY_PAUSE_SECONDS", 0)
    with pytest.raises(m.Stop) as stopped:
        m.request("PUT", "https://kv-test-01.vault.azure.net/secrets/INGRESS-IP?api-version=7.4", "tok", {"value": "v"})
    assert "no answer from Key Vault to a PUT" in str(stopped.value)
    assert len(calls) == 1  # a retried write could land twice


def test_a_dry_run_whose_read_gets_no_answer_stops_without_writing(run):
    vault = FakeVault(no_answer={("GET", "INGRESS-IP")})
    code, text, _ = run(["--vault", "kv-test-01"], vault, external_secret(VALUES), secret(VALUES))
    assert code == 2 and vault.puts == [] and "nothing written" in text and "no answer" in text


def test_a_write_that_gets_no_answer_stops_writing_but_still_reads_back(run):
    vault = FakeVault(no_answer={("PUT", "MEDIA-SONARR-HOST")})
    code, text, _ = run(["--vault", "kv-test-01", "--apply"], vault, external_secret(VALUES), secret(VALUES))
    assert code == 1
    assert vault.puts == []  # the first write got no answer, and none followed
    assert "NOT WRITTEN MEDIA-SONARR-HOST" in text and "no further writes" in text
    assert "read back: 0 of 3 match" in text
