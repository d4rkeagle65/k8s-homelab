"""secret_words_access.py against an in-memory Azure Resource Manager: what it reports missing,
extra and above the vault, what --apply adds and removes and reads back, and what stops it
before it changes anything. The boundaries (az, the ARM calls, Key Vault's read) are replaced,
and every run checks that no ID reaches the output."""

from __future__ import annotations

import importlib.util
import sys
import urllib.parse
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "secret_words_access.py"
SUB = "/subscriptions/0123-sub"
VAULT = SUB + "/resourceGroups/rg-test/providers/Microsoft.KeyVault/vaults/kv-test-01"
PRINCIPAL = "principal-oid-4567"
USER = SUB + "/providers/Microsoft.Authorization/roleDefinitions/4633458b-user"
OFFICER = SUB + "/providers/Microsoft.Authorization/roleDefinitions/b86a8fe4-officer"
CONFIG = 'key_vault_list = "SECRET-WORDS"\nvariables = ["BASE_DOMAIN", "Child1"]\n'
SECRETS = {"BASE-DOMAIN", "Child1", "SECRET-WORDS"}


def load():
    spec = importlib.util.spec_from_file_location("secret_words_access_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


def assignment(scope, role, n):
    return {"id": f"{scope}/providers/Microsoft.Authorization/roleAssignments/a{n}",
            "properties": {"scope": scope, "roleDefinitionId": role, "principalId": PRINCIPAL}}


def complete():
    return [assignment(f"{VAULT}/secrets/BASE-DOMAIN", USER, 1), assignment(f"{VAULT}/secrets/Child1", USER, 2),
            assignment(f"{VAULT}/secrets/SECRET-WORDS", OFFICER, 3)]


class FakeArm:
    """The few ARM calls the script makes: a secret's metadata, role definitions by name, the
    principal's assignments, and adding and removing one. A scope can refuse a PUT."""

    def __init__(self, assignments=(), secrets=SECRETS, refuse_put=()):
        self.assignments = list(assignments)
        self.secrets, self.refuse_put = set(secrets), set(refuse_put)
        self.changes = []

    def __call__(self, method, path, token, body=None):
        assert token == "armtok"
        route, _, query = path.partition("?")
        query = urllib.parse.unquote(query)
        if method == "GET" and "/secrets/" in route and "/Microsoft.Authorization/" not in route:
            return (200, {}) if route.rsplit("/", 1)[1] in self.secrets else (404, {"error": {"code": "SecretNotFound"}})
        if method == "GET" and route.endswith("/roleDefinitions"):
            role = query.split("roleName eq '")[1].split("'")[0]
            return 200, {"value": [{"id": {"Key Vault Secrets User": USER, "Key Vault Secrets Officer": OFFICER}[role]}]}
        if method == "GET" and route.endswith("/roleAssignments"):
            assert f"principalId eq '{PRINCIPAL}'" in query
            return 200, {"value": self.assignments}
        if method == "PUT":
            scope = route.split("/providers/Microsoft.Authorization/")[0]
            self.changes.append(("add", scope, body["properties"]["roleDefinitionId"]))
            assert body["properties"]["principalId"] == PRINCIPAL
            assert body["properties"]["principalType"] == "ServicePrincipal"
            if scope in self.refuse_put:
                return 403, {"error": {"code": "AuthorizationFailed"}}
            self.assignments.append(assignment(scope, body["properties"]["roleDefinitionId"], len(self.changes) + 10))
            return 201, {}
        if method == "DELETE":
            self.changes.append(("remove", route))
            self.assignments = [a for a in self.assignments if a["id"] != route]
            return 200, {}
        raise AssertionError(f"unexpected call {method} {path}")


@pytest.fixture
def run(capsys, monkeypatch, tmp_path):
    config = tmp_path / "secret_words.toml"
    config.write_text(CONFIG, encoding="utf-8")

    def _run(argv, fake, client_id_in_vault="client-0002"):
        m = load()
        monkeypatch.setattr(m.sw, "CONFIG", config)
        monkeypatch.setattr(m, "arm", fake)
        asked = []

        def fake_az(*args):
            asked.append(args)
            if args[:3] == ("ad", "sp", "show"):
                assert args[4] in ("client-0001", "client-0002")
                return PRINCIPAL
            if args[:2] == ("keyvault", "show"):
                assert args[3] == "kv-test-01"
                return VAULT
            if args[:2] == ("account", "get-access-token"):
                return "armtok"
            raise AssertionError(args)

        monkeypatch.setattr(m, "az", fake_az)
        monkeypatch.setattr(m.kv, "access_token", lambda: "kvtok")
        monkeypatch.setattr(m.sw, "read_secret", lambda url, name, token, missing_ok=False:
                            client_id_in_vault if name == m.CLIENT_ID_NAME else pytest.fail(f"read {name}"))
        code = m.main(["--vault", "kv-test-01", *argv])
        out = capsys.readouterr()
        text = out.out + out.err
        for an_id in (PRINCIPAL, "0123-sub", "4633458b", "b86a8fe4", "client-000"):
            assert an_id not in text, "an ID reached the output"
        return code, text

    return _run


def test_a_dry_run_reports_each_missing_role_and_changes_nothing(run):
    fake = FakeArm()
    code, text = run(["--app-id", "client-0001"], fake)
    assert code == 0 and fake.changes == []
    assert "3 secrets: 0 with their role, 3 missing it" in text
    assert "missing  Key Vault Secrets User on BASE-DOMAIN" in text
    assert "missing  Key Vault Secrets Officer on SECRET-WORDS" in text and "dry run" in text


def test_apply_adds_each_role_on_its_one_secret_and_reads_them_back(run):
    fake = FakeArm()
    code, text = run(["--app-id", "client-0001", "--apply"], fake)
    assert code == 0
    assert sorted(fake.changes) == sorted([("add", f"{VAULT}/secrets/BASE-DOMAIN", USER),
                                           ("add", f"{VAULT}/secrets/Child1", USER),
                                           ("add", f"{VAULT}/secrets/SECRET-WORDS", OFFICER)])
    assert "read back: 3 of 3 secrets with their role, 0 extra on the vault, 0 above it" in text


def test_a_principal_already_in_order_is_left_as_it_is(run):
    fake = FakeArm(complete())
    code, text = run(["--app-id", "client-0001", "--apply"], fake)
    assert code == 0 and fake.changes == []


def test_scopes_compare_without_case(run):
    upper = [assignment(a["properties"]["scope"].upper(), a["properties"]["roleDefinitionId"].upper(), i)
             for i, a in enumerate(complete())]
    fake = FakeArm(upper)
    code, text = run(["--app-id", "client-0001", "--apply"], fake)
    assert code == 0 and fake.changes == [] and "3 with their role, 0 missing it" in text


def test_a_role_on_the_whole_vault_is_extra_and_apply_removes_it(run):
    vault_wide = assignment(VAULT, USER, 9)
    fake = FakeArm(complete() + [vault_wide])
    code, text = run(["--app-id", "client-0001"], fake)
    assert code == 0 and "extra    Key Vault Secrets User on the vault" in text and fake.changes == []
    code, text = run(["--app-id", "client-0001", "--apply"], fake)
    assert code == 0 and fake.changes == [("remove", vault_wide["id"])]


def test_the_wrong_role_on_a_secret_is_extra_and_its_own_is_missing(run):
    wrong = assignment(f"{VAULT}/secrets/Child1", OFFICER, 8)
    fake = FakeArm([a for a in complete() if not a["properties"]["scope"].endswith("Child1")] + [wrong])
    code, text = run(["--app-id", "client-0001", "--apply"], fake)
    assert code == 0
    assert ("remove", wrong["id"]) in fake.changes and ("add", f"{VAULT}/secrets/Child1", USER) in fake.changes
    assert "extra    Key Vault Secrets Officer on Child1" in text


def test_a_role_above_the_vault_is_reported_left_alone_and_fails_the_run(run):
    above = assignment(SUB, USER, 7)
    fake = FakeArm(complete() + [above])
    code, text = run(["--app-id", "client-0001", "--apply"], fake)
    assert code == 1 and fake.changes == []
    assert "above    Key Vault Secrets User on a scope above the vault (left alone" in text


def test_a_secret_the_vault_lacks_stops_before_any_change(run):
    fake = FakeArm(secrets=SECRETS - {"Child1"})
    code, text = run(["--app-id", "client-0001", "--apply"], fake)
    assert code == 2 and fake.changes == [] and "Child1 in the vault: Azure answered HTTP 404" in text


def test_a_refused_add_is_named_and_fails_the_run(run):
    fake = FakeArm(refuse_put={f"{VAULT}/secrets/SECRET-WORDS"})
    code, text = run(["--app-id", "client-0001", "--apply"], fake)
    assert code == 1 and "NOT ADDED Key Vault Secrets Officer on SECRET-WORDS: HTTP 403 AuthorizationFailed" in text
    assert "read back: 2 of 3 secrets with their role" in text


def test_the_principal_defaults_to_the_one_key_vault_names(run):
    code, text = run([], FakeArm(complete()))
    assert code == 0 and "3 with their role, 0 missing it" in text


def test_an_empty_client_id_in_key_vault_stops(run):
    code, text = run([], FakeArm(), client_id_in_vault="")
    assert code == 2 and "name the principal with --app-id" in text
