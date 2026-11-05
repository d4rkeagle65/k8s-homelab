"""grimmory-sync.py against an in-memory Grimmory: the first run, libraries, settings, group
mappings and users each written as grimmory.yaml says and read back, the Job failing on what
didn't take."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "kubernetes/prod/apps/grimmory/grimmory/app/sync/grimmory-sync.py"
ADMIN_PASS = "admin-" + "test-value"
USER_PASS = "user-" + "test-value"
# grimmory.yaml's references to them, and the mask Grimmory shows a secret as.
ADMIN_FROM_ENV = "env:GRIMMORY_TEST_ADMIN_PASS"
USER_FROM_ENV = "env:GRIMMORY_TEST_USER_PASS"
MASKED = "*" * 8


def camel(key):
    return "".join(w.capitalize() if i else w.lower() for i, w in enumerate(key.split("_")))


class FakeGrimmory:
    """Grimmory's API as the sync uses it. `drop_settings` answers a settings write and keeps
    none of it; `collide` answers the first login of each user with 400, as two logins within
    a second do."""

    def __init__(self, set_up=False, drop_settings=False, collide=0):
        self.set_up, self.drop_settings, self.collide = set_up, drop_settings, collide
        self.users = {"admin": ADMIN_PASS} if set_up else {}
        self.libraries: list[dict] = []
        self.settings: dict = {}
        self.mappings: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        self.registered: list[dict] = []

    def _id(self, items):
        return max((i["id"] for i in items), default=0) + 1

    def call(self, method, path, body=None, token=None):
        self.calls.append((method, path))
        if path == "/healthcheck":
            return 200, None
        if path == "/setup/status":
            return 200, {"data": self.set_up}
        if path == "/setup":
            self.set_up, self.users[body["username"]] = True, body["password"]
            return 200, None
        if path == "/auth/login":
            if self.collide:
                self.collide -= 1
                return 400, {"message": "A data conflict occurred"}
            ok = self.users.get(body["username"]) == body["password"]
            return (200, {"accessToken": "t-" + body["username"]}) if ok else (401, None)
        if path == "/auth/register":
            self.users[body["username"]] = body["password"]
            self.registered.append(body)
            return 201, None
        if path == "/users":
            return 200, [{"username": u} for u in self.users]
        if path == "/libraries":
            if method == "POST":
                self.libraries.append(dict(body, id=self._id(self.libraries)))
                return 201, None
            return 200, [dict(lib) for lib in self.libraries]
        if path.startswith("/libraries/"):
            lib_id = int(path.split("/")[2])
            if path.endswith("/refresh"):
                return 204, None
            self.libraries = [dict(body, id=lib_id) if lib["id"] == lib_id else lib for lib in self.libraries]
            return 200, None
        if path == "/settings":
            if method == "PUT":
                if not self.drop_settings:
                    self.settings.update({camel(s["name"]): s["value"] for s in body})
                return 200, None
            return 200, dict(self.settings)
        if path == "/admin/oidc-group-mappings":
            if method == "POST":
                self.mappings.append(dict(body, id=self._id(self.mappings)))
                return 201, None
            return 200, [dict(x) for x in self.mappings]
        if path.startswith("/admin/oidc-group-mappings/"):
            mid = int(path.rsplit("/", 1)[1])
            self.mappings = [dict(body) if x["id"] == mid else x for x in self.mappings]
            return 200, None
        raise AssertionError(f"unexpected {method} {path}")

    def writes(self):
        return [c for c in self.calls if c[0] != "GET" and c[1] != "/auth/login"]


def spec(**extra):
    return {
        "admin": {"username": "admin", "password": ADMIN_FROM_ENV},
        "libraries": [{"name": "Books", "paths": ["/books"], "watch": True}],
        "settings": {"METADATA_PROVIDER": "hardcover",
                     "OIDC_PROVIDER_DETAILS": {"clientId": "grimmory", "clientSecret": ADMIN_FROM_ENV,
                                               "scopes": ["openid", "email"]}},
        "groupMappings": [{"group": "readers", "permissions": ["canDownload"], "libraries": ["Books"]}],
        "users": [{"username": "reader", "password": USER_FROM_ENV, "permissions": ["canUpload"],
                   "libraries": ["Books"]}],
        **extra,
    }


@pytest.fixture
def grimmory(monkeypatch):
    def make(config=None, **fake_kw):
        monkeypatch.setenv("GRIMMORY_URL", "http://grimmory.example.invalid/")
        monkeypatch.setenv("GRIMMORY_TEST_ADMIN_PASS", ADMIN_PASS)
        monkeypatch.setenv("GRIMMORY_TEST_USER_PASS", USER_PASS)
        cfg = config if config is not None else spec()
        monkeypatch.setitem(sys.modules, "yaml", types.SimpleNamespace(safe_load=lambda f: json.loads(json.dumps(cfg))))
        monkeypatch.setattr(sys, "dont_write_bytecode", True)
        loader = importlib.util.spec_from_file_location("grimmory_sync_under_test", SCRIPT)
        module = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(module)
        module.CONFIG = str(SCRIPT)  # opened, then handed to the stubbed safe_load
        fake = FakeGrimmory(**fake_kw)
        module.call = fake.call
        monkeypatch.setattr(module.time, "sleep", lambda s: None)
        return module, fake
    return make


def test_a_fresh_grimmory_is_set_up_as_grimmory_yaml_says(grimmory, capsys):
    m, fake = grimmory()
    m.main()
    assert fake.users == {"admin": ADMIN_PASS, "reader": USER_PASS}
    (books,) = fake.libraries
    assert books["paths"] == [{"path": "/books"}] and books["watch"] is True
    assert fake.settings["metadataProvider"] == "hardcover"
    assert fake.mappings == [{"oidcGroupClaim": "readers", "isAdmin": False, "permissions": ["canDownload"],
                              "libraryIds": [books["id"]], "description": "", "id": 1}]
    (reader,) = fake.registered
    assert reader["canUpload"] is True and reader["selectedLibraries"] == [books["id"]]
    assert "permissions" not in reader and "libraries" not in reader
    out = capsys.readouterr().out
    assert "setup: first run done" in out and "verified:" in out and ADMIN_PASS not in out


def test_a_second_run_only_writes_the_settings_again(grimmory):
    m, fake = grimmory()
    m.main()
    fake.calls.clear()
    m.main()
    assert fake.writes() == [("PUT", "/settings")]


def test_a_changed_library_is_updated_and_rescanned(grimmory, capsys):
    m, fake = grimmory()
    m.main()
    fake.calls.clear()
    s = spec()
    s["libraries"][0]["paths"] = ["/books", "/comics"]
    m2, _ = grimmory(config=s)
    m2.call = fake.call
    m2.main()
    lib_id = fake.libraries[0]["id"]
    assert ("PUT", f"/libraries/{lib_id}") in fake.calls and ("PUT", f"/libraries/{lib_id}/refresh") in fake.calls
    assert sorted(p["path"] for p in fake.libraries[0]["paths"]) == ["/books", "/comics"]


def test_a_changed_mapping_is_updated(grimmory):
    m, fake = grimmory()
    m.main()
    s = spec()
    s["groupMappings"][0]["admin"] = True
    m2, _ = grimmory(config=s)
    m2.call = fake.call
    m2.main()
    assert fake.mappings[0]["isAdmin"] is True and fake.mappings[0]["id"] == 1


def test_a_setting_grimmory_didnt_keep_fails_the_read_back(grimmory):
    m, _ = grimmory(drop_settings=True)
    with pytest.raises(SystemExit, match="setting metadataProvider is None"):
        m.main()


def test_a_secret_setting_is_checked_as_set_only(grimmory):
    s = spec()
    s["settings"]["SMTP_SECRET"] = ADMIN_FROM_ENV
    m, fake = grimmory(config=s)
    real = fake.call

    def masks(method, path, body=None, token=None):
        status, answer = real(method, path, body, token)
        if method == "GET" and path == "/settings":
            answer = dict(answer, smtpSecret=MASKED,
                          oidcProviderDetails=dict(answer["oidcProviderDetails"], clientSecret=MASKED))
        return status, answer

    m.call = masks
    m.main()


def test_an_existing_user_is_left_alone_and_its_password_checked(grimmory):
    m, fake = grimmory(set_up=True)
    fake.users["reader"] = "another-" + "password"
    with pytest.raises(SystemExit, match="user reader can't log in"):
        m.main()
    assert fake.registered == []


def test_an_admin_that_cant_log_in_stops_the_run(grimmory, monkeypatch):
    m, fake = grimmory(set_up=True)
    fake.users["admin"] = "changed-" + "elsewhere"
    with pytest.raises(SystemExit, match="local admin from grimmory.yaml can't log in"):
        m.main()
    assert fake.libraries == []


def test_a_mapping_naming_an_undefined_library_stops_the_run(grimmory):
    s = spec()
    s["groupMappings"][0]["libraries"] = ["Comics"]
    m, _ = grimmory(config=s)
    with pytest.raises(SystemExit, match="names libraries grimmory.yaml doesn't define"):
        m.main()


def test_a_login_collision_is_retried(grimmory):
    m, fake = grimmory(set_up=True, collide=2)
    assert m.login("admin", ADMIN_PASS) == "t-admin"
    fake.collide = 3
    assert m.login("admin", ADMIN_PASS) is None


def test_compare_reads_nested_settings_lists_in_any_order_and_case(grimmory):
    m, _ = grimmory()
    assert m.compare("x", {"a": "TRUE", "l": ["b", "a"], "secret": "set"}, {"a": True, "l": ["a", "b"], "secret": "w"}) == []
    assert m.compare("x", {"a": "no"}, {"a": "yes"}) == ["setting x.a is 'no'"]
    assert m.compare("x", "flat", {"a": 1}) == ["setting x is str"]
    assert m.compare("x", {"clientSecret": ""}, {"clientSecret": "w"}) == ["setting x.clientSecret is empty"]


def test_it_waits_for_grimmory_and_gives_up(grimmory):
    m, _ = grimmory()
    answers = iter([OSError("refused"), (503, None), (200, None)])
    m.call = lambda *a, **k: (lambda a: (_ for _ in ()).throw(a) if isinstance(a, Exception) else a)(next(answers))
    m.wait_ready()
    m.call = lambda *a, **k: (503, None)
    with pytest.raises(SystemExit, match="within 7.5 minutes"):
        m.wait_ready()
