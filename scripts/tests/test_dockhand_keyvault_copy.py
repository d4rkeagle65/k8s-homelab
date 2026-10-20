"""dockhand_keyvault_copy.py: the names map and its refusals, what it skips and refuses in the
export, and dry and real runs against an in-memory Key Vault, with keyvault_copy's boundaries
(the Azure CLI token, the REST calls) replaced. Every run also checks that no value reaches the
output, and the repo's own map is read as the script reads it."""

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


def entry(stack, key, value, environment="docker-vm"):
    return {"environment": environment, "stack": stack, "key": key, "value": value}


MAP = """
cadvisor:
  CADVISOR_ADDRESS: DOCKER_VM_1_MGMT_ADDRESS
databasus:
  DATABASUS_BIND_ADDRESS: DOCKER_VM_1_MGMT_ADDRESS
kea:
  KEA_DB_PASSWORD: KEA_DB_PASSWORD
  STORK_OIDC_CLIENT_SECRET: AUTHENTIK_STORK_OIDC_CLIENT_SECRET
  STORK_OIDC_ISSUER_URL: {built: "https://auth.${BASE_DOMAIN}/application/o/stork/"}
netbox:
  SYNC_APPLY: {none: "a setting: written into the compose file"}
  CONFIG_DIFF_PASSWORD: {none: "blank"}
semaphore:
  TZ: TIMEZONE
  NETBOX_TOKEN: NETBOX_SEMAPHORE_TOKEN
vaultwarden:
  SMTP_FROM: OWNER_EMAIL
"""
ENTRIES = [
    entry("cadvisor", "CADVISOR_ADDRESS", "address-value-0"),
    entry("databasus", "DATABASUS_BIND_ADDRESS", "address-value-0"),
    entry("kea", "KEA_DB_PASSWORD", "kea-db-value-1"),
    entry("kea", "STORK_OIDC_CLIENT_SECRET", "stork-oidc-value-2"),
    entry("kea", "STORK_OIDC_ISSUER_URL", "https://auth.example-domain.test/application/o/stork/"),
    entry("netbox", "SYNC_APPLY", "true-setting"),
    entry("semaphore", "TZ", "Etc/UTC"),
    entry("semaphore", "NETBOX_TOKEN", "semaphore-netbox-value-3"),
    entry("tailscale", "TS_AUTHKEY", "azurekv://TAILSCALE-AUTH-KEY"),
    entry("garage", "DOCKHAND_SECRET_SELECTOR", "selector-value"),
    entry("vaultwarden", "SMTP_FROM", "owner-address-value"),
]
# cluster-secrets' copies: one the same, one base64 of a stack's, one sharing a value under
# another name, the domain a built form reads; and one disabled.
HELD = {
    "KEA-DB-PASSWORD": {"value": "kea-db-value-1", "tags": {"source": "cluster-secrets"}},
    "AUTHENTIK-STORK-OIDC-CLIENT-SECRET": {"value": base64.b64encode(b"stork-oidc-value-2").decode(),
                                           "tags": {"source": "cluster-secrets"}},
    "TIMEZONE": {"value": "Etc/UTC", "tags": {"source": "cluster-secrets"}},
    "ACME-EMAIL": {"value": "owner-address-value", "tags": {"source": "cluster-secrets"}},
    "BASE-DOMAIN": {"value": "example-domain.test", "tags": {"source": "cluster-secrets"}},
    "INGRESS-IP": {"value": "ingress-value-5", "tags": {"source": "cluster-secrets"}},
    "OLD-ONE": {"value": "old-disabled-value", "tags": {}, "enabled": False},
}
ADDED = ["DOCKER-VM-1-MGMT-ADDRESS", "NETBOX-SEMAPHORE-TOKEN", "OWNER-EMAIL"]


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
    """Runs main() on an export and a map with the boundaries replaced; returns (exit code, output)."""

    def _run(entries, vault, *flags, raw=None, names=MAP):
        m = load()
        monkeypatch.setattr(m.kv, "access_token", lambda: "tok")
        vault.Stop = m.kv.Stop
        monkeypatch.setattr(m.kv, "request", vault)
        export, names_map = tmp_path / "export.json", tmp_path / "names.yaml"
        export.write_text(raw if raw is not None else json.dumps(entries), encoding="utf-8")
        names_map.write_text(names, encoding="utf-8")
        code = m.main([str(export), "--vault", VAULT, "--map", str(names_map), *flags])
        out = capsys.readouterr()
        text = out.out + out.err
        values = [e["value"] for e in entries if isinstance(e, dict) and isinstance(e.get("value"), str)]
        values += [s["value"] for s in vault.held.values()]
        for value in values:
            assert len(value) < 6 or value not in text, "a secret value reached the output"
        return code, text

    return _run


m = load()


def rules(text, tmp_path):
    path = tmp_path / "names.yaml"
    path.write_text(text, encoding="utf-8")
    return m.read_map(path)


def test_the_repos_map_reads():
    got = m.read_map(m.MAP)
    assert got and all(rule[0] in ("name", "built", "none") for stack in got.values() for rule in stack.values())


def test_a_map_entry_is_a_name_a_built_form_or_a_reason(tmp_path):
    got = rules(MAP, tmp_path)
    assert got["cadvisor"]["CADVISOR_ADDRESS"] == ("name", "DOCKER_VM_1_MGMT_ADDRESS")
    assert got["kea"]["STORK_OIDC_ISSUER_URL"] == ("built", "https://auth.${BASE_DOMAIN}/application/o/stork/",
                                                   ["BASE_DOMAIN"])
    assert got["netbox"]["CONFIG_DIFF_PASSWORD"] == ("none", "blank")


@pytest.mark.parametrize("bad", ["s:\n  K: 5\n", "s:\n  K: [A]\n", "s:\n  K: bad-name\n", "s:\n  K: {built: 1}\n",
                                 's:\n  K: {built: "constant"}\n', 's:\n  K: {built: "$HOME/${A}"}\n',
                                 's:\n  K: {none: ""}\n', 's:\n  K: {built: "${A}", none: "x"}\n', "- a list\n",
                                 "s: [K]\n", "s:\n  K: A\n  K: B\n", "s: {K: A\n"])
def test_a_map_of_another_shape_stops(tmp_path, bad):
    with pytest.raises(m.kv.Stop):
        rules(bad, tmp_path)


def test_a_variable_the_map_doesnt_name_stops_before_reading_key_vault(run):
    vault = FakeVault(HELD)
    code, text = run(ENTRIES + [entry("netbox", "NEW_ONE", "new-value-6")], vault, "--apply")
    assert code == 2 and "not in the map: netbox NEW_ONE" in text and vault.gets == [] and vault.puts == []


@pytest.mark.parametrize("value", ["azurekv://kea--X", "op://vault/item/field", "'op://vault/item/field'",
                                   ' "keepass://db/entry" ', "pass://share/item", "psono://id/field"])
def test_a_provider_reference_is_skipped_quoted_or_not(value):
    targets, built, skipped, refs = m.plan([entry("kea", "X", value)], {})
    assert targets == {} and skipped == ["kea X: already a secret-provider reference"]


@pytest.mark.parametrize("value", ["postgres://user:pw@db:5432/x", "https://host/path", "opaque-op://"])
def test_a_value_that_merely_looks_like_a_url_is_copied(value):
    targets, _, _, _ = m.plan([entry("netbox", "DATABASE_URL", value)],
                              {"netbox": {"DATABASE_URL": ("name", "NETBOX_DB_URL")}})
    assert list(targets) == ["NETBOX_DB_URL"]


def test_dockhands_bulk_selectors_are_skipped():
    targets, _, skipped, _ = m.plan([entry("a", "DOCKHAND_SECRET_SELECTOR", "x"), entry("a", "OP_ENVIRONMENT_ID", "y")], {})
    assert targets == {} and len(skipped) == 2


def test_variables_that_share_a_name_are_one_secret_when_their_values_agree():
    names = {"a": {"X": ("name", "SHARED")}, "b": {"Y": ("name", "SHARED")}}
    targets, _, _, refs = m.plan([entry("a", "X", "v"), entry("b", "Y", "v")], names)
    assert targets == {"SHARED": {"value": "v", "readers": ["a X", "b Y"]}} and refs == {"a": {"SHARED"}, "b": {"SHARED"}}
    with pytest.raises(m.kv.Stop, match="both become SHARED, with different values"):
        m.plan([entry("a", "X", "one"), entry("b", "Y", "two")], names)


def test_empty_values_match_by_nothing_else_so_form_no_group():
    targets = {name: {"value": value, "readers": []} for name, value in
               (("EMPTY_A", ""), ("EMPTY_B", ""), ("SAME_A", "same-value"), ("SAME_B", "same-value"))}
    assert m.shared_values(targets, {}) == ["SAME-A = SAME-B"]


def test_two_names_key_vault_reads_as_one_stop():
    with pytest.raises(m.kv.Stop, match="one name to Key Vault"):
        m.plan([entry("a", "X", "v"), entry("b", "Y", "v")], {"a": {"X": ("name", "A_B")}, "b": {"Y": ("name", "a_b")}})


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


def test_dry_run_reports_states_built_forms_and_references_and_writes_nothing(run):
    vault = FakeVault(HELD)
    code, text = run(ENTRIES, vault)
    assert code == 0 and vault.puts == [] and "dry run: nothing written" in text
    assert ("11 stack variables from 8 stacks: 6 Key Vault names (3 to add, 2 held with the same value, "
            "1 held base64), 1 built, 3 skipped") in text
    assert "add    DOCKER-VM-1-MGMT-ADDRESS  (cadvisor CADVISOR_ADDRESS, databasus DATABASUS_BIND_ADDRESS)" in text
    assert "base64 AUTHENTIK-STORK-OIDC-CLIENT-SECRET  (kea STORK_OIDC_CLIENT_SECRET)" in text
    assert "built  kea STORK_OIDC_ISSUER_URL: its built form gives the same value" in text
    assert "skip   netbox SYNC_APPLY: a setting: written into the compose file" in text
    assert "skip   tailscale TS_AUTHKEY: already a secret-provider reference" in text
    assert "skip   garage DOCKHAND_SECRET_SELECTOR: Dockhand's bulk-pull selector" in text
    assert "in the map but not in the export: netbox CONFIG_DIFF_PASSWORD" in text
    assert "  ACME-EMAIL = OWNER-EMAIL\n" in text
    assert ("  kea AUTHENTIK_STORK_OIDC_CLIENT_SECRET = azurekv://AUTHENTIK-STORK-OIDC-CLIENT-SECRET"
            "  (once Key Vault holds it plain)") in text
    assert "  kea BASE_DOMAIN = azurekv://BASE-DOMAIN\n" in text  # read by the built form
    assert "  semaphore TIMEZONE = azurekv://TIMEZONE\n" in text
    assert "OLD-ONE" not in vault.gets  # a disabled secret isn't read
    assert "conflicts" not in text


def test_apply_writes_only_the_missing_names_tagged_and_reads_them_back(run):
    vault = FakeVault(HELD)
    code, text = run(ENTRIES, vault, "--apply")
    assert code == 0 and "read back: 3 of 3 written names match the export, value and tag" in text
    assert sorted(vault.puts) == sorted(ADDED)
    assert vault.held["DOCKER-VM-1-MGMT-ADDRESS"] == {"value": "address-value-0", "tags": {"source": "dockhand"}}
    for name in HELD:
        assert vault.held[name] == HELD[name], f"{name} changed"
    assert "dry run" not in text


def test_a_name_held_with_another_value_is_a_conflict_and_nothing_is_written(run):
    held = dict(HELD, **{"NETBOX-SEMAPHORE-TOKEN": {"value": "another-value-7", "tags": {}}})
    code, text = run(ENTRIES, FakeVault(held))
    assert code == 1 and "NETBOX-SEMAPHORE-TOKEN: Key Vault holds another value than semaphore NETBOX_TOKEN" in text
    vault = FakeVault(held)
    code, text = run(ENTRIES, vault, "--apply")
    assert code == 2 and vault.puts == [] and "nothing written: 1 conflict(s)" in text


def test_a_disabled_name_is_a_conflict(run):
    held = dict(HELD, **{"OWNER-EMAIL": {"value": "owner-address-value", "tags": {}, "enabled": False}})
    vault = FakeVault(held)
    code, text = run(ENTRIES, vault, "--apply")
    assert code == 2 and vault.puts == [] and "OWNER-EMAIL is disabled in Key Vault" in text


def test_a_built_form_that_gives_another_value_is_a_conflict(run):
    held = dict(HELD, **{"BASE-DOMAIN": {"value": "another-domain.test", "tags": {}}})
    vault = FakeVault(held)
    code, text = run(ENTRIES, vault, "--apply")
    assert code == 2 and vault.puts == [] and "kea STORK_OIDC_ISSUER_URL: its built form gives another value" in text


def test_a_built_form_reading_a_name_nothing_holds_is_a_conflict(run):
    held = {k: v for k, v in HELD.items() if k != "BASE-DOMAIN"}
    vault = FakeVault(held)
    code, text = run(ENTRIES, vault, "--apply")
    assert code == 2 and vault.puts == [] and "reads BASE_DOMAIN, which nothing holds" in text


def test_a_built_form_may_read_a_name_this_run_writes(run):
    names = MAP.replace("  NETBOX_TOKEN: NETBOX_SEMAPHORE_TOKEN\n",
                        '  NETBOX_TOKEN: NETBOX_SEMAPHORE_TOKEN\n  TOKEN_HEADER: {built: "Bearer ${NETBOX_SEMAPHORE_TOKEN}"}\n')
    entries = ENTRIES + [entry("semaphore", "TOKEN_HEADER", "Bearer semaphore-netbox-value-3")]
    code, text = run(entries, FakeVault(HELD), names=names)
    assert code == 0 and "built  semaphore TOKEN_HEADER: its built form gives the same value" in text


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
    vault = FakeVault(HELD, no_answer={("PUT", "DOCKER-VM-1-MGMT-ADDRESS")})
    code, text = run(ENTRIES, vault, "--apply")
    assert code == 1 and vault.puts == [] and "no further writes" in text
    assert "read back: 0 of 3 written names match" in text and "MISMATCH OWNER-EMAIL" in text


def test_a_bad_read_back_fails_the_run(run):
    vault = FakeVault(HELD, corrupt={"OWNER-EMAIL"})
    code, text = run(ENTRIES, vault, "--apply")
    assert code == 1 and "MISMATCH OWNER-EMAIL" in text and "read back: 2 of 3 written names match" in text


def test_a_write_that_loses_its_tag_fails_the_read_back(run):
    vault = FakeVault(HELD, drop_tags={"OWNER-EMAIL"})
    code, text = run(ENTRIES, vault, "--apply")
    assert code == 1 and "MISMATCH OWNER-EMAIL" in text


def test_a_bad_vault_name_stops_before_reading_anything(monkeypatch, tmp_path, capsys):
    module = load()
    asked = []
    monkeypatch.setattr(module.kv, "access_token", lambda: asked.append(1) or "tok")
    export = tmp_path / "export.json"
    export.write_text(json.dumps([entry("kea", "K", "v")]), encoding="utf-8")
    assert module.main([str(export), "--vault", "https://x.vault.azure.net"]) == 2 and asked == []
    assert "nothing written" in capsys.readouterr().err
