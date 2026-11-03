"""organizr-sync.py against an in-memory Organizr: with remove_unlisted on,
the categories and tabs organizr.yaml lists are the whole set, and nothing of
Organizr's own is ever deleted; with it off, unlisted ones are only reported.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / (
    "kubernetes/prod/services/organizr/organizr/app/sync/organizr-sync.py")


class FakeOrganizr:
    """Organizr's API as the sync uses it. drop_deletes answers a DELETE with
    200 and keeps the item, as a call that reports success and stores nothing."""

    def __init__(self, drop_deletes=False, refuse_deletes=False):
        self.tabs = [
            {"id": 1, "name": "Settings", "type": 0, "category_id": 0, "group_id": 1},
            {"id": 2, "name": "Homepage", "type": 0, "category_id": 0, "group_id": 4},
            {"id": 4, "name": "Jellyseerr", "type": 2, "category_id": 1, "group_id": 4},
            {"id": 5, "name": "Emby", "type": 2, "category_id": 1, "group_id": 4},
        ]
        self.categories = [
            {"id": 1, "category": "Unsorted", "category_id": 0, "default": 1},
            {"id": 2, "category": "Media", "category_id": 1, "default": 0},
            {"id": 3, "category": "Old", "category_id": 2, "default": 0},
        ]
        self.config = {}
        self.calls = []
        self.bodies = []
        self.drop_deletes = drop_deletes
        self.refuse_deletes = refuse_deletes

    def call(self, method, path, body=None, auth=True):
        self.calls.append((method, path))
        self.bodies.append(body)
        ok = lambda data=None: (200, {"response": {"data": data}})  # noqa: E731
        if path == "/ping":
            return ok("pong")
        if path == "/config":
            if method == "PUT":
                self.config.update(body)
            return ok(dict(self.config))
        kind, _, ident = path.strip("/").partition("/")
        items = self.tabs if kind == "tabs" else self.categories
        if method == "GET":
            return ok({"tabs": list(self.tabs), "categories": list(self.categories)}
                      if kind == "tabs" else list(self.categories))
        if method == "POST":
            new = dict(body, id=max(i["id"] for i in items) + 1)
            if kind == "categories":  # Organizr numbers a new category itself
                new["category_id"] = max(c["category_id"] for c in items) + 1
            items.append(new)
            return ok()
        item = next(i for i in items if str(i["id"]) == ident)
        if method == "PUT":
            item.update(body)
            return ok()
        if method == "DELETE":
            if self.refuse_deletes:
                return 500, {"response": {"message": "refused"}}
            if not self.drop_deletes:
                items.remove(item)
            return ok()
        raise AssertionError(f"unexpected call {method} {path}")

    def names(self):
        return sorted(t["name"] for t in self.tabs), sorted(c["category"] for c in self.categories)


SPEC_TABS = [
    {"name": "Settings", "type": 0, "category_id": 0, "group_id": 1},
    {"name": "Homepage", "type": 0, "category_id": 0, "group_id": 4},
    {"name": "Emby", "category": "Media", "type": 2, "group_id": 4},
]
SPEC_CATEGORIES = [{"category": "Unsorted"}, {"category": "Media"}]


def load(monkeypatch, fake: FakeOrganizr, spec: dict):
    monkeypatch.setenv("ORGANIZR_URL", "http://organizr.invalid")
    monkeypatch.setenv("ORGANIZR_API_KEY", "test-only")
    # PyYAML is installed by the Job, not necessarily here; main only needs safe_load.
    monkeypatch.setitem(sys.modules, "yaml", types.SimpleNamespace(safe_load=lambda f: spec))
    # No bytecode beside the manifests: a cached copy is trusted by mtime and size,
    # so an edit of the same length within a second would run the old code.
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    loader = importlib.util.spec_from_file_location("organizr_sync_under_test", SCRIPT)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    module.call = fake.call
    module.CONFIG = str(SCRIPT)  # opened, then handed to the stubbed safe_load
    return module


def load_only():
    """The module with stand-ins it never reaches, for its pure functions."""
    mp = pytest.MonkeyPatch()
    try:
        return load(mp, FakeOrganizr(), {})
    finally:
        mp.undo()


def spec(**extra) -> dict:
    return {"wizard": {}, "settings": {}, "categories": [dict(c) for c in SPEC_CATEGORIES],
            "tabs": [dict(t) for t in SPEC_TABS], **extra}


def test_on_removes_the_unlisted_and_keeps_the_listed(monkeypatch, capsys):
    fake = FakeOrganizr()
    load(monkeypatch, fake, spec(remove_unlisted=True)).main()
    assert fake.names() == (["Emby", "Homepage", "Settings"], ["Media", "Unsorted"])
    out = capsys.readouterr().out
    assert "tabs: removed Jellyseerr" in out and "categories: removed Old" in out


def test_tabs_go_before_categories(monkeypatch):
    fake = FakeOrganizr()
    load(monkeypatch, fake, spec(remove_unlisted=True)).main()
    deletes = [p for m, p in fake.calls if m == "DELETE"]
    assert [p.split("/")[1] for p in deletes] == ["tabs", "categories"]


def test_off_reports_and_removes_nothing(monkeypatch, capsys):
    fake = FakeOrganizr()
    before = fake.names()
    load(monkeypatch, fake, spec()).main()
    assert fake.names() == before
    assert not [c for c in fake.calls if c[0] == "DELETE"]
    out = capsys.readouterr().out
    assert "not in organizr.yaml (remove_unlisted is off): Jellyseerr" in out
    assert "not in organizr.yaml (remove_unlisted is off): Old" in out


def test_only_true_turns_it_on(monkeypatch):
    fake = FakeOrganizr()
    load(monkeypatch, fake, spec(remove_unlisted="yes")).main()
    assert not [c for c in fake.calls if c[0] == "DELETE"]


def test_an_unlisted_builtin_tab_stops_the_run_before_any_delete(monkeypatch):
    fake = FakeOrganizr()
    s = spec(remove_unlisted=True)
    s["tabs"] = [t for t in s["tabs"] if t["name"] != "Settings"]
    with pytest.raises(SystemExit, match="Settings are Organizr's own"):
        load(monkeypatch, fake, s).main()
    assert not [c for c in fake.calls if c[0] == "DELETE"]
    assert "Jellyseerr" in fake.names()[0]


def test_an_unlisted_default_category_stops_the_run(monkeypatch):
    fake = FakeOrganizr()
    s = spec(remove_unlisted=True)
    s["categories"] = [c for c in s["categories"] if c["category"] != "Unsorted"]
    with pytest.raises(SystemExit, match="Unsorted are Organizr's own"):
        load(monkeypatch, fake, s).main()
    assert "Unsorted" in fake.names()[1]


def test_a_refused_delete_stops_the_run(monkeypatch):
    fake = FakeOrganizr(refuse_deletes=True)
    with pytest.raises(SystemExit, match="removing tabs Jellyseerr answered 500"):
        load(monkeypatch, fake, spec(remove_unlisted=True)).main()


def test_a_delete_that_stored_nothing_fails_the_read_back(monkeypatch):
    fake = FakeOrganizr(drop_deletes=True)
    with pytest.raises(SystemExit, match="tab Jellyseerr is there, not in organizr.yaml"):
        load(monkeypatch, fake, spec(remove_unlisted=True)).main()


def test_the_real_config_lists_organizrs_own(monkeypatch):
    """organizr.yaml turns remove_unlisted on, so it must list every built-in
    the sync would otherwise refuse to delete."""
    text = (SCRIPT.parent / "organizr.yaml").read_text(encoding="utf-8")
    assert "\nremove_unlisted: true\n" in text
    for line in ("{name: Settings, type: 0,", "{name: Homepage, type: 0,", "{category: Unsorted,"):
        assert line in text


# --- what the sync writes: settings, new and changed categories and tabs, the first run ----------

# A setting read from the environment, and the value the test puts there.
PLEX_FROM_ENV = "env:ORGANIZR_TEST_PLEX_TOKEN"
FROM_ENV = "from-the-environment"


class FreshOrganizr(FakeOrganizr):
    """An Organizr whose wizard hasn't run: the API key is refused until POST /wizard, and
    then accepted (or still refused, with key_refused, as when the stored key differs)."""

    def __init__(self, set_up=False, key_refused=False, **kw):
        super().__init__(**kw)
        self.set_up, self.key_refused, self.wizard = set_up, key_refused, None

    def call(self, method, path, body=None, auth=True):
        if path == "/wizard":
            self.calls.append((method, path))
            if self.set_up:
                return 401, {"response": {"message": "Database already exists"}}
            self.wizard, self.set_up = body, True
            return 200, {"response": {"data": None}}
        if path == "/config" and (not self.set_up or self.key_refused):
            self.calls.append((method, path))
            return 401, {"response": {"message": "Token not valid"}}
        return super().call(method, path, body, auth)


def test_a_missing_category_and_tab_are_added_with_the_tabs_category_id(monkeypatch, capsys):
    fake = FakeOrganizr()
    s = spec()
    s["categories"].append({"category": "Tools"})
    s["tabs"].append({"name": "Grafana", "category": "Tools", "type": 1, "group_id": 1})
    load(monkeypatch, fake, s).main()
    tools = next(c for c in fake.categories if c["category"] == "Tools")
    grafana = next(t for t in fake.tabs if t["name"] == "Grafana")
    assert grafana["category_id"] == tools["category_id"] and grafana["type"] == 1
    out = capsys.readouterr().out
    assert "categories: added Tools" in out and "tabs: added Grafana" in out


def test_a_differing_tab_is_updated_in_what_differs_only(monkeypatch, capsys):
    fake = FakeOrganizr()
    s = spec()
    emby = next(t for t in s["tabs"] if t["name"] == "Emby")
    emby["group_id"] = 1
    load(monkeypatch, fake, s).main()
    puts = [(p, b) for (m_, p), b in zip(fake.calls, fake.bodies) if m_ == "PUT" and p.startswith("/tabs/")]
    assert puts == [("/tabs/5", {"group_id": 1})]
    assert next(t for t in fake.tabs if t["name"] == "Emby")["group_id"] == 1
    assert "tabs: updated Emby (group_id)" in capsys.readouterr().out


def test_an_unchanged_list_writes_nothing(monkeypatch):
    fake = FakeOrganizr()
    load(monkeypatch, fake, spec()).main()
    assert [c for c in fake.calls if c[0] in ("POST", "DELETE") or (c[0] == "PUT" and c[1] != "/config")] == []


def test_differs_compares_as_text():
    m = load_only()
    assert m.differs({"type": 2, "group_id": "4"}, {"type": "2", "group_id": 4}) == {}
    assert m.differs({"type": 2}, {"type": 3, "name": "x"}) == {"type": 3, "name": "x"}


def test_settings_are_written_with_env_values_and_read_back(monkeypatch):
    fake = FakeOrganizr()
    monkeypatch.setenv("ORGANIZR_TEST_PLEX_TOKEN", "from-the-environment")
    s = spec(settings={"title": "Home", "plexToken": PLEX_FROM_ENV})
    load(monkeypatch, fake, s).main()
    assert fake.config == {"title": "Home", "plexToken": FROM_ENV}


def test_a_setting_organizr_didnt_keep_fails_the_read_back(monkeypatch):
    fake = FakeOrganizr()
    real = fake.call

    def drops_title(method, path, body=None, auth=True):
        if method == "PUT" and path == "/config":
            body = {k: v for k, v in body.items() if k != "title"}
        return real(method, path, body, auth)

    module = load(monkeypatch, fake, spec(settings={"title": "Home"}))
    module.call = drops_title
    with pytest.raises(SystemExit, match="setting title is None"):
        module.main()


def test_a_secret_setting_is_read_back_as_present_only(monkeypatch):
    fake = FakeOrganizr()
    real = fake.call

    def encrypts(method, path, body=None, auth=True):
        if method == "PUT" and path == "/config":
            body = {k: ("encrypted:" + v if k == "plexToken" else v) for k, v in body.items()}
        return real(method, path, body, auth)

    monkeypatch.setenv("ORGANIZR_TEST_PLEX_TOKEN", "from-the-environment")
    module = load(monkeypatch, fake, spec(settings={"plexToken": PLEX_FROM_ENV}))
    module.call = encrypts
    module.main()


def test_a_setting_naming_a_missing_variable_stops_the_run(monkeypatch):
    fake = FakeOrganizr()
    monkeypatch.delenv("ORGANIZR_TEST_UNSET", raising=False)
    with pytest.raises(SystemExit, match="reads ORGANIZR_TEST_UNSET"):
        load(monkeypatch, fake, spec(settings={"x": "env:ORGANIZR_TEST_UNSET"})).main()
    assert fake.config == {}


def test_a_refused_settings_write_stops_the_run(monkeypatch):
    fake = FakeOrganizr()
    real = fake.call
    module = load(monkeypatch, fake, spec(settings={"title": "Home"}))
    module.call = lambda m_, p, b=None, auth=True: (500, {"response": {"message": "no"}}) \
        if (m_, p) == ("PUT", "/config") else real(m_, p, b, auth)
    with pytest.raises(SystemExit, match="writing settings answered 500"):
        module.main()


def test_the_first_run_sets_organizr_up_from_the_wizard_block(monkeypatch, capsys):
    fake = FreshOrganizr()
    monkeypatch.setenv("ORGANIZR_TEST_ADMIN_PASS", "from-the-environment")
    load(monkeypatch, fake, spec(wizard={"username": "admin", "pass": "env:ORGANIZR_TEST_ADMIN_PASS"})).main()
    assert fake.wizard == {"username": "admin", "pass": "from-the-environment"}
    assert "first run done" in capsys.readouterr().out


def test_a_set_up_organizr_skips_the_wizard(monkeypatch, capsys):
    fake = FreshOrganizr(set_up=True)
    load(monkeypatch, fake, spec(wizard={"username": "admin"})).main()
    assert ("POST", "/wizard") not in fake.calls
    assert "setup: done before" in capsys.readouterr().out


def test_a_set_up_organizr_refusing_the_key_stops_the_run(monkeypatch):
    fake = FreshOrganizr(set_up=True, key_refused=True)
    with pytest.raises(SystemExit, match="its stored API key differs"):
        load(monkeypatch, fake, spec(wizard={"username": "admin"})).main()


def test_a_wizard_that_leaves_the_key_refused_stops_the_run(monkeypatch):
    fake = FreshOrganizr(key_refused=True)
    with pytest.raises(SystemExit, match="reported success, but the API key is refused"):
        load(monkeypatch, fake, spec(wizard={"username": "admin"})).main()


def test_it_waits_for_organizr_to_answer_and_gives_up_after_five_minutes(monkeypatch):
    module = load_only()
    answers = iter([OSError("refused"), (503, {}), (200, {"response": {"data": "pong"}})])

    def ping(method, path, body=None, auth=True):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    slept = []
    monkeypatch.setattr(module.time, "sleep", slept.append)
    module.call = ping
    module.wait_ready()
    assert slept == [5, 5]
    module.call = lambda *a, **kw: (503, {})
    with pytest.raises(SystemExit, match="within 5 minutes"):
        module.wait_ready()
    assert len(slept) == 2 + 60


def test_secret_words_name_secret_settings():
    m = load_only()
    assert m.is_secret("plexToken") and m.is_secret("API_KEY") and m.is_secret("adminPassword")
    assert not m.is_secret("title")


@pytest.mark.parametrize("method, change, why", [
    ("POST", lambda s: s["tabs"].append({"name": "Grafana", "type": 1, "group_id": 1}), "adding /tabs Grafana answered 500"),
    ("PUT", lambda s: next(t for t in s["tabs"] if t["name"] == "Emby").update(group_id=1),
     "updating /tabs Emby answered 500"),
])
def test_a_refused_add_or_update_stops_the_run(monkeypatch, method, change, why):
    fake = FakeOrganizr()
    real = fake.call
    s = spec()
    change(s)
    module = load(monkeypatch, fake, s)
    module.call = lambda m_, p, b=None, auth=True: (500, {"response": {"message": "no"}}) \
        if m_ == method and p.startswith("/tabs") else real(m_, p, b, auth)
    with pytest.raises(SystemExit, match=why):
        module.main()
