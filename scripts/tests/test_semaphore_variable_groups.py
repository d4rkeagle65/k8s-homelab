"""scripts/semaphore_variable_groups.py: a group is written only when managed, and then exactly
as declared (secrets created, set by id, the undeclared removed), read back; a value Key
Vault didn't give stops that group rather than writing it empty; every other group is only
reported; and its config gives each template the variables its script reads.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m = load("semaphore_variable_groups")

ENV = {"AZURE_TENANT_ID": "t", "AZURE_CLIENT_ID": "c", "AZURE_CLIENT_SECRET": "s",
       "AZURE_KEYVAULT_URL": "https://vault.example", "SEMAPHORE_URL": "http://semaphore.example",
       "SEMAPHORE_SYNC_TOKEN": "tok"}
VAULT = {"BASE-DOMAIN": "example.org", "A-TOKEN": "<a-value>", "B-TOKEN": "<b-value>"}


class Fake:
    """Azure's login, Key Vault and Semaphore's environment API, recording each write."""

    def __init__(self, groups=None, vault=None, login=200, put_drops=False):
        self.vault = dict(VAULT if vault is None else vault)
        self.groups = {g["id"]: g for g in (groups or [])}
        self.login, self.put_drops, self.writes = login, put_drops, []

    def http(self, method, url, headers=None, body=None, form=None, timeout=30):
        if "login.microsoftonline.com" in url:
            return self.login, ({"access_token": "kv"} if self.login == 200 else None)
        if url.startswith("https://vault.example/secrets/"):
            name = url.split("/secrets/")[1].split("?")[0]
            return (200, {"value": self.vault[name]}) if name in self.vault else (404, None)
        path = url.split("/api", 1)[1]
        if path == "/projects":
            return 200, [{"id": 1, "name": "Homelab"}]
        if path == "/project/1/environment" and method == "GET":
            return 200, [{"id": g["id"], "name": g["name"]} for g in self.groups.values()]
        if path == "/project/1/environment" and method == "POST":
            self.writes.append(("POST", body))
            gid = max(self.groups, default=0) + 1
            self.groups[gid] = self.apply({"id": gid, "name": body["name"], "secrets": []}, body)
            return 201, {"id": gid}
        gid = int(path.rsplit("/", 1)[1])
        if method == "PUT":
            self.writes.append(("PUT", body))
            if not self.put_drops:
                self.apply(self.groups[gid], body)
            return 204, None
        return 200, json.loads(json.dumps(self.groups[gid]))

    @staticmethod
    def apply(g, body):
        g["json"], g["env"] = body["json"], body["env"]
        secrets = {s["id"]: s for s in g.get("secrets", [])}
        for op in body["secrets"]:
            if op["operation"] == "delete":
                secrets.pop(op["id"])
            elif op["operation"] == "update":
                secrets[op["id"]]["value"] = op["secret"]
            else:
                sid = max(secrets, default=100) + 1
                secrets[sid] = {"id": sid, "name": op["name"], "type": op["type"], "value": op["secret"]}
        g["secrets"] = list(secrets.values())
        return g


def run(monkeypatch, tmp_path, fake, config, env=None, argv=("semaphore_variable_groups.py",)):
    path = tmp_path / "groups.yaml"
    path.write_text(config, encoding="utf-8")
    monkeypatch.setattr(m, "http", fake.http)
    for k in [*ENV, "SEMAPHORE_VARIABLE_GROUPS"]:
        monkeypatch.delenv(k, raising=False)
    for k, v in {**(ENV if env is None else env), "SEMAPHORE_VARIABLE_GROUPS": str(path)}.items():
        monkeypatch.setenv(k, v)
    return m.main(list(argv))


CONFIG = """project: Homelab
groups:
  Thing:
    managed: true
    environment:
      THING_URL: {template: "https://thing.{BASE-DOMAIN}"}
      A_TOKEN: {keyvault: A-TOKEN}
    variables:
      thing_flag: {value: true}
      b_token: {keyvault: B-TOKEN}
"""


def test_a_new_managed_group_is_created_as_declared_and_read_back(monkeypatch, tmp_path, capsys):
    fake = Fake()
    assert run(monkeypatch, tmp_path, fake, CONFIG) == 0
    [(method, body)] = fake.writes
    assert method == "POST" and json.loads(body["env"]) == {"THING_URL": "https://thing.example.org"}
    assert json.loads(body["json"]) == {"thing_flag": True}
    assert sorted((s["name"], s["type"], s["secret"], s["operation"]) for s in body["secrets"]) == [
        ("A_TOKEN", "env", "<a-value>", "create"), ("b_token", "var", "<b-value>", "create")]
    out = capsys.readouterr().out
    assert "written and read back" in out and "<a-value>" not in out and "<b-value>" not in out


def existing(**over):
    g = {"id": 7, "name": "Thing", "json": json.dumps({"old_var": 1}), "env": json.dumps({"THING_URL": "https://old"}),
         "secrets": [{"id": 101, "name": "A_TOKEN", "type": "env"}, {"id": 102, "name": "STALE", "type": "env"}]}
    g.update(over)
    return g


def test_an_existing_managed_group_is_made_exactly_as_declared(monkeypatch, tmp_path, capsys):
    fake = Fake([existing()])
    assert run(monkeypatch, tmp_path, fake, CONFIG) == 0
    [(method, body)] = fake.writes
    ops = sorted((s["name"], s["operation"], s.get("id")) for s in body["secrets"])
    assert method == "PUT" and body["id"] == 7
    assert ops == [("A_TOKEN", "update", 101), ("STALE", "delete", 102), ("b_token", "create", None)]
    out = capsys.readouterr().out
    for line in ("change environment THING_URL", "remove variables old_var", "add variables thing_flag",
                 "set secret environment A_TOKEN", "remove secret environment STALE", "add secret variables b_token"):
        assert line in out, line


def test_an_unmanaged_group_is_reported_and_never_written(monkeypatch, tmp_path, capsys):
    fake = Fake([existing()])
    assert run(monkeypatch, tmp_path, fake, CONFIG.replace("managed: true", "managed: false")) == 0
    assert fake.writes == [] and "change environment THING_URL" in capsys.readouterr().out


def test_a_value_key_vault_lacks_stops_its_group_and_fails_the_run(monkeypatch, tmp_path, capsys):
    fake = Fake(vault={"BASE-DOMAIN": "example.org", "A-TOKEN": "a"})
    assert run(monkeypatch, tmp_path, fake, CONFIG) == 1
    assert fake.writes == [] and "B-TOKEN: not in Key Vault" in capsys.readouterr().out


def test_a_write_that_doesnt_take_fails_the_run(monkeypatch, tmp_path, capsys):
    assert run(monkeypatch, tmp_path, Fake([existing()], put_drops=True), CONFIG) == 1
    assert "didn't take" in capsys.readouterr().out


def test_a_refused_login_stops_before_any_group(monkeypatch, tmp_path, capsys):
    fake = Fake(login=401)
    assert run(monkeypatch, tmp_path, fake, CONFIG) == 1
    assert fake.writes == [] and "refused the sync's login" in capsys.readouterr().out


def test_undeclared_groups_are_listed_by_name_and_left_alone(monkeypatch, tmp_path, capsys):
    other = {"id": 3, "name": "Pi-Hole Settings", "json": json.dumps({"pihole_settings": {}}), "env": "{}",
             "secrets": [{"id": 9, "name": "PIHOLE_PASSWORD", "type": "env"}]}
    own = {"id": 4, "name": "Key Vault Sync", "json": "{}", "env": "{}", "secrets": []}
    fake = Fake([other, own])
    assert run(monkeypatch, tmp_path, fake, CONFIG) == 0
    out = capsys.readouterr().out
    assert "Pi-Hole Settings (not declared): variables ['pihole_settings']" in out and "env:PIHOLE_PASSWORD" in out
    assert "Key Vault Sync (not declared)" not in out and all(w[0] == "POST" for w in fake.writes)


def test_what_the_config_may_not_say(monkeypatch, tmp_path, capsys):
    for bad in ("project: Homelab\ngroups:\n  Key Vault Sync: {managed: true}\n",
                "project: Homelab\ngroups:\n  X:\n    environment:\n      A: {keyvault: A, value: 1}\n",
                "project: Homelab\ngroups:\n  X:\n    environment:\n      A: {value: [1]}\n",
                "project: Homelab\ngroups:\n  X:\n    environment:\n      A: {value: 1}\n    variables:\n      A: {value: 2}\n",
                "project: Homelab\ngroups:\n  X: {extra: 1}\n"):
        assert run(monkeypatch, tmp_path, Fake(), bad) == 2, bad
    assert run(monkeypatch, tmp_path, Fake(), CONFIG, env={k: v for k, v in ENV.items() if k != "AZURE_CLIENT_SECRET"}) == 2
    assert run(monkeypatch, tmp_path, Fake(), CONFIG, argv=("x", "--apply")) == 2
    assert "missing: AZURE_CLIENT_SECRET" in capsys.readouterr().out


def test_two_groups_of_one_name_are_never_written(monkeypatch, tmp_path, capsys):
    fake = Fake([existing(), existing(id=8)])
    assert run(monkeypatch, tmp_path, fake, CONFIG) == 1 and fake.writes == []


def test_the_real_config_loads_and_gives_the_alerting_test_what_it_reads():
    config = m.load_config(ROOT / "scripts" / "semaphore_variable_groups.yaml")
    alerting = load("alerting_test")
    group = config["groups"]["Alerting Test"]
    reads = {"ZAMMAD_URL", "ZAMMAD_API_TOKEN"} | {v for needs in alerting.NEEDS.values() for v in needs}
    assert set(group["environment"]) == reads and not group.get("variables")  # a Python template: environment only
