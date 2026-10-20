"""dockhand_keyvault_copy.py: the names, what it skips and refuses in the export, and dry and
real runs against an in-memory Key Vault, with keyvault_copy's boundaries (the Azure CLI token,
the REST calls) replaced. Every run also checks that no value reaches the output."""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "dockhand_keyvault_copy.py"
VAULT = "kv-test-01"
HOST = f"{VAULT}.vault.azure.net"


def load():
    spec = importlib.util.spec_from_file_location("dockhand_keyvault_copy_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


def ours(stack, key):
    return {"source": "dockhand", "stack": stack, "variable": key}


def entry(stack, key, value, environment="docker-vm"):
    return {"environment": environment, "stack": stack, "key": key, "value": value}


ENTRIES = [
    entry("kea", "KEA_API_PASSWORD", "kea-pass-value-1"),
    entry("netbox", "DB_PASSWORD", "netbox-db-value-2"),
    entry("netbox_worker", "DB_PASSWORD", "netbox-db-value-2"),
    entry("semaphore", "TOKEN", "semaphore-value-3"),
    entry("netbox", "EMPTY_ONE", ""),
    entry("kea", "EMPTY_TWO", ""),
    entry("tailscale", "TS_AUTHKEY", "azurekv://tailscale--TS-AUTHKEY"),
    entry("garage", "DOCKHAND_SECRET_SELECTOR", "selector-value"),
    entry("garage", "RPC_SECRET", "garage-rpc-value-4", environment="nas"),
]
# cluster-secrets' copies, one sharing a value with a stack variable and one holding another's
# as base64; one disabled; two earlier copies of stack variables, one since changed in Dockhand.
HELD = {
    "KEA-API-PASSWORD": {"value": "kea-pass-value-1", "tags": {"source": "cluster-secrets"}},
    "GARAGE-RPC-B64": {"value": base64.b64encode(b"garage-rpc-value-4").decode(), "tags": {"source": "cluster-secrets"}},
    "INGRESS-IP": {"value": "ingress-value-5", "tags": {"source": "cluster-secrets"}},
    "OLD-ONE": {"value": "old-disabled-value", "tags": {}, "enabled": False},
    "netbox--DB-PASSWORD": {"value": "netbox-db-value-2", "tags": ours("netbox", "DB_PASSWORD")},
    "semaphore--TOKEN": {"value": "semaphore-value-earlier", "tags": ours("semaphore", "TOKEN")},
}


class FakeVault:
    """Key Vault's secrets API in memory: the paged list, GET and PUT, names matched ignoring
    case as Key Vault does. Every request must be for the vault's own host."""

    def __init__(self, held=None, page=2, refuse_list=False, list_no_answer=False, next_host=None,
                 no_answer=(), corrupt=(), drop_tags=()):
        self.held = {name: dict(secret) for name, secret in (held or {}).items()}
        self.page, self.refuse_list, self.list_no_answer, self.next_host = page, refuse_list, list_no_answer, next_host
        self.no_answer, self.corrupt, self.drop_tags = set(no_answer), set(corrupt), set(drop_tags)
        self.puts, self.gets = [], []
        self.Stop = Exception

    def find(self, name):
        return next((n for n in self.held if n.lower() == name.lower()), None)

    def __call__(self, method, url, token, body=None):
        assert token == "tok"
        parts = urllib.parse.urlsplit(url)
        assert parts.hostname == HOST, "the token went to another host"
        if parts.path == "/secrets":
            if self.list_no_answer:
                raise self.Stop("secrets: no answer from Key Vault to a GET (timed out)")
            if self.refuse_list:
                return 403, {"error": {"code": "Forbidden"}}
            start = int(urllib.parse.parse_qs(parts.query).get("$skiptoken", ["0"])[0])
            names = sorted(self.held)
            more = start + self.page < len(names)
            return 200, {
                "value": [{"id": f"https://{HOST}/secrets/{n}", "tags": self.held[n].get("tags", {}),
                           "attributes": {"enabled": self.held[n].get("enabled", True)}}
                          for n in names[start:start + self.page]],
                "nextLink": (f"https://{self.next_host or HOST}:443/secrets?api-version=7.4"
                             f"&$skiptoken={start + self.page}&maxresults=25") if more else None}
        name = urllib.parse.unquote(parts.path.split("/secrets/")[1])
        if (method, name) in self.no_answer:
            raise self.Stop(f"{name}: no answer from Key Vault to a {method} (timed out)")
        found = self.find(name)
        if method == "GET":
            self.gets.append(name)
            if found is None:
                return 404, {"error": {"code": "SecretNotFound"}}
            if not self.held[found].get("enabled", True):
                return 403, {"error": {"code": "Forbidden"}}
            return 200, {"value": self.held[found]["value"], "tags": self.held[found].get("tags", {})}
        self.puts.append(name)
        self.held[found or name] = {"value": body["value"] + ("!" if name in self.corrupt else ""),
                                    "tags": {} if name in self.drop_tags else body["tags"]}
        return 200, {}


@pytest.fixture
def run(capsys, monkeypatch, tmp_path):
    """Runs main() on an export file with the boundaries replaced; returns (exit code, output)."""

    def _run(entries, vault, *flags, raw=None):
        m = load()
        monkeypatch.setattr(m.kv, "access_token", lambda: "tok")
        vault.Stop = m.kv.Stop
        monkeypatch.setattr(m.kv, "request", vault)
        export = tmp_path / "export.json"
        export.write_text(raw if raw is not None else json.dumps(entries), encoding="utf-8")
        code = m.main([str(export), "--vault", VAULT, *flags])
        out = capsys.readouterr()
        text = out.out + out.err
        values = [e["value"] for e in entries if isinstance(e, dict) and isinstance(e.get("value"), str)]
        values += [s["value"] for s in vault.held.values()]
        for value in values:
            assert len(value) < 6 or value not in text, "a secret value reached the output"
        return code, text

    return _run


m = load()


def test_a_name_is_the_stack_and_variable_with_dashes():
    assert m.keyvault_name("kea", "KEA_API_PASSWORD") == "kea--KEA-API-PASSWORD"
    assert m.keyvault_name("netbox_worker", "db_password") == "netbox-worker--db-password"


@pytest.mark.parametrize("value", ["azurekv://kea--X", "op://vault/item/field", "'op://vault/item/field'",
                                   ' "keepass://db/entry" ', "pass://share/item", "psono://id/field"])
def test_a_provider_reference_is_skipped_quoted_or_not(value):
    targets, skipped = m.plan([entry("kea", "X", value)])
    assert targets == {} and skipped == ["kea X: already a secret-provider reference"]


@pytest.mark.parametrize("value", ["postgres://user:pw@db:5432/x", "https://host/path", "opaque-op://"])
def test_a_value_that_merely_looks_like_a_url_is_copied(value):
    targets, skipped = m.plan([entry("netbox", "DATABASE_URL", value)])
    assert list(targets) == ["netbox--DATABASE-URL"] and skipped == []


def test_dockhands_bulk_selectors_are_skipped():
    targets, skipped = m.plan([entry("a", "DOCKHAND_SECRET_SELECTOR", "x"), entry("a", "OP_ENVIRONMENT_ID", "y")])
    assert targets == {} and len(skipped) == 2


def test_a_name_key_vault_wont_take_stops():
    with pytest.raises(m.kv.Stop, match="isn't a valid Key Vault name"):
        m.plan([entry("my.stack", "X", "v")])


def test_two_variables_that_become_one_name_ignoring_case_stop():
    with pytest.raises(m.kv.Stop, match="both become"):
        m.plan([entry("kea", "A_B", "one"), entry("Kea", "a_b", "two")])


def test_one_stack_variable_in_two_environments_is_one_secret_when_its_value_agrees():
    targets, _ = m.plan([entry("garage", "K", "same"), entry("garage", "K", "same", environment="nas")])
    assert list(targets) == ["garage--K"]
    with pytest.raises(m.kv.Stop, match="differs between Dockhand environments"):
        m.plan([entry("garage", "K", "one"), entry("garage", "K", "two", environment="nas")])


@pytest.mark.parametrize("raw", ['{"stack": "a"}', '[{"stack": "a", "key": "B"}]',
                                 '[{"stack": "a", "key": "B", "value": 5}]',
                                 '[{"environment": 5, "stack": "a", "key": "B", "value": "leak-value-9"}]',
                                 '[{"stack": "", "key": "B", "value": "leak-value-9"}]', "not json"])
def test_an_export_of_another_shape_stops_without_quoting_it(run, raw):
    vault = FakeVault()
    code, text = run([], vault, "--apply", raw=raw)
    assert code == 2 and "nothing written" in text and "leak-value-9" not in text
    assert vault.gets == [] and vault.puts == []


def test_an_export_with_a_byte_order_mark_is_read(tmp_path):
    export = tmp_path / "export.json"
    export.write_bytes(b"\xef\xbb\xbf" + json.dumps([entry("kea", "K", "v")]).encode())
    assert m.read_export(export) == [entry("kea", "K", "v")]


def test_dry_run_reports_and_writes_nothing(run):
    vault = FakeVault(HELD)
    code, text = run(ENTRIES, vault)
    assert code == 0 and vault.puts == [] and "dry run: nothing written" in text
    assert "7 stack variables from 5 stacks: 5 missing from Key Vault, 1 different, 1 already the same; 2 skipped" in text
    assert "add    kea--KEA-API-PASSWORD" in text and "update semaphore--TOKEN" in text
    assert "skip   tailscale TS_AUTHKEY: already a secret-provider reference" in text
    assert "skip   garage DOCKHAND_SECRET_SELECTOR: Dockhand's bulk-pull selector" in text
    assert "  kea--KEA-API-PASSWORD = KEA-API-PASSWORD\n" in text
    assert "  netbox--DB-PASSWORD = netbox-worker--DB-PASSWORD\n" in text
    assert "  garage--RPC-SECRET = GARAGE-RPC-B64 (base64)\n" in text
    assert "kea--EMPTY-TWO = netbox--EMPTY-ONE" not in text  # empty values match by nothing else
    assert "  netbox_worker DB_PASSWORD = azurekv://netbox-worker--DB-PASSWORD" in text
    assert "  garage RPC_SECRET = azurekv://garage--RPC-SECRET" in text
    assert "OLD-ONE" not in vault.gets  # a disabled secret isn't read


def test_apply_writes_what_differs_tagged_and_reads_every_name_back(run):
    vault = FakeVault(HELD)
    code, text = run(ENTRIES, vault, "--apply")
    assert code == 0 and "read back: 7 of 7 match the export, value and tags" in text
    assert sorted(vault.puts) == sorted(["kea--KEA-API-PASSWORD", "netbox-worker--DB-PASSWORD", "semaphore--TOKEN",
                                         "netbox--EMPTY-ONE", "kea--EMPTY-TWO", "garage--RPC-SECRET"])
    assert vault.held["semaphore--TOKEN"] == {"value": "semaphore-value-3", "tags": ours("semaphore", "TOKEN")}
    assert vault.held["garage--RPC-SECRET"]["tags"] == ours("garage", "RPC_SECRET")
    assert vault.held["KEA-API-PASSWORD"] == HELD["KEA-API-PASSWORD"]  # the cluster's own copy untouched
    assert "dry run" not in text


def test_an_earlier_copy_under_another_case_is_the_same_secret(run):
    vault = FakeVault({"KEA--api-password": {"value": "kea-pass-value-1", "tags": ours("kea", "API_PASSWORD")}})
    code, text = run([entry("kea", "API_PASSWORD", "kea-pass-value-1")], vault, "--apply")
    assert code == 0 and "1 already the same" in text and vault.puts == []


@pytest.mark.parametrize("tags", [{"source": "cluster-secrets"}, {}, ours("netbox", "API_PASSWORD"),
                                  {"source": "dockhand", "stack": "kea"}])
def test_a_name_held_by_anything_but_that_stack_variables_copy_stops_before_any_write(run, tags):
    vault = FakeVault({"kea--API-PASSWORD": {"value": "other-value-6", "tags": tags}})
    code, text = run([entry("kea", "API_PASSWORD", "kea-pass-value-1"), entry("kea", "OTHER", "kea-other-7")],
                     vault, "--apply")
    assert code == 2 and vault.puts == [] and "kea--API-PASSWORD is held by a secret not tagged as kea API_PASSWORD" in text


def test_a_disabled_secret_under_a_stack_variables_name_stops(run):
    vault = FakeVault({"kea--K": {"value": "kea-k-value-8", "tags": ours("kea", "K"), "enabled": False}})
    code, text = run([entry("kea", "K", "kea-k-value-8")], vault, "--apply")
    assert code == 2 and vault.puts == [] and "kea--K is disabled in Key Vault" in text


def test_a_list_that_continues_on_another_host_stops_before_sending_the_token(run):
    vault = FakeVault(HELD, next_host="elsewhere.example")
    code, text = run(ENTRIES, vault, "--apply")
    assert code == 2 and vault.puts == [] and "continues on another host" in text


@pytest.mark.parametrize("vault", [FakeVault(HELD, refuse_list=True), FakeVault(HELD, list_no_answer=True),
                                   FakeVault(HELD, no_answer={("GET", "INGRESS-IP")})])
def test_a_list_or_read_that_cant_answer_stops_before_any_write(run, vault):
    code, text = run(ENTRIES, vault, "--apply")
    assert code == 2 and vault.puts == [] and "nothing written" in text


def test_a_write_that_gets_no_answer_stops_writing_but_still_reads_back(run):
    vault = FakeVault({}, no_answer={("PUT", "garage--RPC-SECRET")})
    code, text = run([entry("garage", "RPC_SECRET", "garage-rpc-value-4", "nas"), entry("kea", "K", "kea-k-value-8")],
                     vault, "--apply")
    assert code == 1 and vault.puts == [] and "no further writes" in text
    assert "read back: 0 of 2 match" in text and "MISMATCH garage--RPC-SECRET" in text and "MISMATCH kea--K" in text


def test_a_bad_read_back_fails_the_run(run):
    vault = FakeVault({}, corrupt={"kea--K"})
    code, text = run([entry("kea", "K", "kea-k-value-8")], vault, "--apply")
    assert code == 1 and "MISMATCH kea--K" in text and "read back: 0 of 1 match" in text


def test_a_write_that_loses_its_tags_fails_the_read_back(run):
    vault = FakeVault({}, drop_tags={"kea--K"})
    code, text = run([entry("kea", "K", "kea-k-value-8")], vault, "--apply")
    assert code == 1 and "MISMATCH kea--K" in text and "read back: 0 of 1 match" in text


def test_a_bad_vault_name_stops_before_reading_anything(monkeypatch, tmp_path, capsys):
    module = load()
    asked = []
    monkeypatch.setattr(module.kv, "access_token", lambda: asked.append(1) or "tok")
    export = tmp_path / "export.json"
    export.write_text(json.dumps([entry("kea", "K", "v")]), encoding="utf-8")
    assert module.main([str(export), "--vault", "https://x.vault.azure.net"]) == 2 and asked == []
    assert "nothing written" in capsys.readouterr().err
