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
        self.drop_deletes = drop_deletes
        self.refuse_deletes = refuse_deletes

    def call(self, method, path, body=None, auth=True):
        self.calls.append((method, path))
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
            items.append(dict(body, id=max(i["id"] for i in items) + 1))
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
