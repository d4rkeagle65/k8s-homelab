"""media-sync.py against in-memory Bookshelf, qBittorrent, SABnzbd and Seerr: each section
writes only what differs and reads it back, a dry run writes nothing, a write that doesn't
take or an app that can't be read fails the Job, and an API key is never printed."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
import urllib.parse
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "kubernetes/prod/apps/media/media-sync/app/sync/media-sync.py"
APPS = {"BOOKSHELF": "bookshelf", "QBITTORRENT": "qbittorrent", "SABNZBD": "sabnzbd", "SEERR": "seerr"}
KEY = "test-" + "key-value"


class FakeApps:
    """The four apps' APIs as the sync uses them. `ignored` apps answer a write with success
    and store nothing; `down` apps can't be reached."""

    def __init__(self, module, ignored=(), down=()):
        self.m, self.ignored, self.down = module, set(ignored), set(down)
        self.writes: list[tuple[str, str]] = []
        self.bookshelf = {"development": {"id": 1, "metadataSource": "goodreads", "other": "x"}}
        self.qbit_prefs = {"max_ratio": "1.5", "dht": True}
        self.qbit_cats = {"movies": {"name": "movies", "savePath": "/data/movies"}}
        self.sab_misc = {"download_dir": "/incomplete", "direct_unpack": "0"}
        self.sab_cats = [{"name": "tv", "dir": "tv", "pp": "3", "priority": "0"}]
        # A server carries fields media-sync.yaml doesn't list (its API key, here a stand-in).
        self.seerr = {"radarr": [{"id": 1, "name": "Radarr", "hostname": "radarr", "is4k": False, "apiKey": "kept"}],
                      "sonarr": []}

    def http(self, method, url, headers=None, body=None, form=None):
        parts = urllib.parse.urlsplit(url)
        app = parts.hostname.split(".")[0]
        if app in self.down:
            raise self.m.CantRead(f"{method} {app}: connection refused")
        write = method != "GET" or "set_config" in parts.query
        if write:
            self.writes.append((app, f"{method} {parts.path}"))
            if app in self.ignored:
                return 200, {}
        path = parts.path
        if app == "bookshelf":
            name = path.split("/config/")[1].split("/")[0]
            if method == "GET":
                return 200, dict(self.bookshelf[name])
            self.bookshelf[name] = dict(body)
            return 202, {}
        if app == "qbittorrent":
            if path.endswith("/app/preferences"):
                return 200, dict(self.qbit_prefs)
            if path.endswith("/app/setPreferences"):
                self.qbit_sent = json.loads(form["json"])
                self.qbit_prefs.update(self.qbit_sent)
                return 200, None
            if path.endswith("/torrents/categories"):
                return 200, {n: dict(v) for n, v in self.qbit_cats.items()}
            if path.endswith(("/torrents/createCategory", "/torrents/editCategory")):
                self.qbit_cats[form["category"]] = {"name": form["category"], "savePath": form["savePath"]}
                return 200, None
        if app == "sabnzbd":
            q = dict(urllib.parse.parse_qsl(parts.query))
            assert q["apikey"] == KEY and q["output"] == "json"
            if q["mode"] == "get_config":
                section = q["section"]
                value = dict(self.sab_misc) if section == "misc" else [dict(c) for c in self.sab_cats]
                return 200, {"config": {section: value}}
            if q["section"] == "misc":
                self.sab_misc[q["keyword"]] = q["value"]
                return 200, {"status": True}
            fields = {k: v for k, v in q.items() if k not in ("mode", "section", "keyword", "output", "apikey")}
            cat = next((c for c in self.sab_cats if c["name"] == q["keyword"]), None)
            if cat is None:
                self.sab_cats.append({"name": q["keyword"], **fields})
            else:
                cat.update(fields)
            return 200, {"status": True}
        if app == "seerr":
            kind = path.split("/settings/")[1].split("/")[0]
            if method == "GET":
                return 200, [dict(s) for s in self.seerr[kind]]
            if method == "POST":
                self.seerr[kind].append({**body, "id": 10 + len(self.seerr[kind])})
                return 201, {}
            sid = int(path.rsplit("/", 1)[1])
            # A PUT replaces the server whole, as Seerr's settings API does.
            self.seerr[kind] = [dict(body, id=sid) if s["id"] == sid else s for s in self.seerr[kind]]
            return 200, {}
        raise AssertionError(f"unexpected {method} {url}")


WANT = {
    "bookshelf": {"development": {"metadataSource": "hardcover"}},
    "qbittorrent": {"preferences": {"max_ratio": 2, "dht": True},
                    "categories": {"movies": "/data/movies", "books": "/data/books"}},
    "sabnzbd": {"misc": {"direct_unpack": 1, "download_dir": "/incomplete"},
                "categories": {"tv": {"dir": "tv", "pp": 3}, "audio": {"dir": "audio", "pp": 1}}},
    "seerr": {"radarr": [{"name": "Radarr", "hostname": "radarr", "is4k": True}],
              "sonarr": [{"name": "Sonarr", "hostname": "sonarr", "is4k": False}]},
}


@pytest.fixture
def media(monkeypatch, tmp_path):
    def make(config=WANT, dry_run=False, **fake_kw):
        for env, app in APPS.items():
            monkeypatch.setenv(f"{env}_URL", f"http://{app}.example.invalid/")
            monkeypatch.setenv(f"{env}_API_KEY", KEY)
        monkeypatch.setitem(sys.modules, "yaml", types.SimpleNamespace(safe_load=lambda f: json.loads(f.read())))
        monkeypatch.setattr(sys, "dont_write_bytecode", True)
        loader = importlib.util.spec_from_file_location("media_sync_under_test", SCRIPT)
        module = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(module)
        cfg = tmp_path / "media-sync.yaml"
        cfg.write_text(json.dumps(config), encoding="utf-8")
        module.CONFIG, module.DRY_RUN = str(cfg), dry_run
        fake = FakeApps(module, **fake_kw)
        module.http = fake.http
        return module, fake
    return make


def test_each_section_writes_what_differs_and_reads_it_back(media, capsys):
    m, fake = media()
    m.main()
    assert fake.bookshelf["development"] == {"id": 1, "metadataSource": "hardcover", "other": "x"}
    assert fake.qbit_prefs == {"max_ratio": 2, "dht": True}
    assert fake.qbit_cats["books"]["savePath"] == "/data/books"
    assert fake.sab_misc["direct_unpack"] == "1"
    assert {c["name"]: c["pp"] for c in fake.sab_cats} == {"tv": "3", "audio": "1"}
    assert fake.seerr["radarr"][0]["is4k"] is True and fake.seerr["sonarr"][0]["name"] == "Sonarr"
    assert fake.seerr["radarr"][0]["apiKey"] == "kept", "an update keeps what the config doesn't list"
    assert fake.qbit_sent == {"max_ratio": 2}, "only the preferences that differ are sent"
    out = capsys.readouterr().out
    assert "qbittorrent categories: creating books" in out and "media-sync: done" in out
    assert ("qbittorrent", "POST /api/v2/torrents/editCategory") not in fake.writes, "movies was already right"
    assert KEY not in out


def test_a_dry_run_reports_each_difference_and_writes_nothing(media, capsys):
    m, fake = media(dry_run=True)
    m.main()
    assert fake.writes == []
    out = capsys.readouterr().out
    assert "bookshelf development: would change metadataSource" in out
    assert "seerr sonarr Sonarr: would add" in out
    assert "qbittorrent categories: would create books" in out


def test_a_second_run_changes_nothing(media, capsys):
    m, fake = media()
    m.main()
    first = list(fake.writes)
    fake.writes.clear()
    m.FAILED.clear()
    m.main()
    assert first and fake.writes == []


@pytest.mark.parametrize("app", ["bookshelf", "qbittorrent", "sabnzbd", "seerr"])
def test_a_write_that_doesnt_take_fails_the_job(media, app):
    m, fake = media(ignored={app})
    with pytest.raises(SystemExit, match="FAIL"):
        m.main()
    assert any(app in f and ("didn't take" in f) for f in m.FAILED), m.FAILED


def test_an_app_that_cant_be_read_fails_the_job_and_the_others_still_sync(media, capsys):
    m, fake = media(down={"qbittorrent"})
    with pytest.raises(SystemExit, match="qbittorrent: couldn't read it"):
        m.main()
    assert fake.bookshelf["development"]["metadataSource"] == "hardcover"
    assert fake.seerr["sonarr"], "the sections after it ran"
    assert "nothing changed there" in capsys.readouterr().out


def test_a_section_the_script_doesnt_know_stops_it_before_any_write(media):
    m, fake = media(config={**WANT, "lidarr": {}})
    with pytest.raises(SystemExit, match="doesn't know: lidarr"):
        m.main()
    assert fake.writes == []


def test_env_values_are_read_and_a_missing_one_stops_the_run(media, monkeypatch):
    m, fake = media(config={"bookshelf": {"development": {"metadataSource": "env:MEDIA_TEST_SOURCE"}}})
    monkeypatch.setenv("MEDIA_TEST_SOURCE", "hardcover")
    m.main()
    assert fake.bookshelf["development"]["metadataSource"] == "hardcover"
    monkeypatch.delenv("MEDIA_TEST_SOURCE")
    with pytest.raises(SystemExit, match="reads MEDIA_TEST_SOURCE"):
        m.main()


def test_same_compares_as_the_apps_mean_it(media):
    m, _ = media()
    assert m.same("true", True) and m.same(True, True) and not m.same("false", True)
    assert m.same("2", 2) and m.same(2.0, 2) and m.same("1.5", 1.5) and not m.same("x", 2) and not m.same(True, 1)
    assert m.same(None, None) and m.same("", None) and not m.same("a", None)
    assert m.same([1], [1]) and not m.same([1], [2])
    assert m.same("a", "a") and not m.same(None, "a")


def test_a_key_or_password_is_never_shown(media):
    m, _ = media()
    assert m.shown("apiKey", "x") == m.shown("password", "y") == "<hidden>"
    assert m.shown("dir", "tv") == '"tv"'


def test_sabnzbd_categories_that_dont_take_fail_the_job(media):
    m, fake = media(config={"sabnzbd": {"categories": WANT["sabnzbd"]["categories"]}}, ignored={"sabnzbd"})
    with pytest.raises(SystemExit, match="sabnzbd categories: didn't take: audio"):
        m.main()
