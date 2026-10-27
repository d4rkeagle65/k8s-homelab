"""wall-exporter.py: the metrics it renders from each app's API answers, and that an app it
can't read, or has no key for, is reported down and never as zeros."""

from __future__ import annotations

import importlib.util
import sys
import urllib.error
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[2] / "kubernetes" / "prod" / "system" / "monitoring" / "wall-exporter"
          / "app" / "wall-exporter.py")


def load():
    spec = importlib.util.spec_from_file_location("wall_exporter_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load()

STATS = [{"_id": "statistics", "totalFileCount": 812, "table1Count": 40, "table2Count": 760, "table3Count": 2,
          "table4Count": 5, "table5Count": 800, "table6Count": 1}]
NODES = {"n1": {"nodeName": "internal", "workers": {
    "w1": {"workerType": "transcodecpu", "idle": False}, "w2": {"workerType": "healthcheckcpu", "idle": False},
    "w3": {"workerType": "transcodecpu", "idle": True}}}}
TASKS = [{"status": "running"}, {"status": "success"}, {"status": "success"}, {"status": "error"}, {"status": "waiting"}]
SESSIONS = [{"NowPlayingItem": {"Name": "x"}, "TranscodingInfo": {"VideoCodec": "h264"}},
            {"NowPlayingItem": {"Name": "y"}}, {"DeviceName": "idle"}]
COUNTS = {"total": 30, "movie": 20, "tv": 10, "pending": 2, "approved": 25, "declined": 1, "processing": 3, "available": 24}
IMMICH = {"photos": 50000, "videos": 1200, "usage": 400_000_000_000, "usagePhotos": 150_000_000_000,
          "usageVideos": 250_000_000_000, "usageByUser": []}


def fake_call(answers):
    def call(url, headers=None, body=None):
        for frag, answer in answers.items():
            if frag in url and (body is None or frag != "cruddb" or True):
                if callable(answer):
                    return answer(url, headers, body)
                return answer
        raise urllib.error.URLError("refused")
    return call


def tdarr_answer(url, headers, body):
    return NODES if url.endswith("/get-nodes") else STATS


def setup(monkeypatch, answers, keys=True):
    for k, v in {"TDARR_URL": "http://tdarr", "SEMAPHORE_URL": "http://sem", "EMBY_URL": "http://emby",
                 "SEERR_URL": "http://seerr", "IMMICH_URL": "http://immich"}.items():
        monkeypatch.setenv(k, v)
    for k in ("SEMAPHORE_TOKEN", "EMBY_API_KEY", "SEERR_API_KEY", "IMMICH_API_KEY"):
        if keys:
            monkeypatch.setenv(k, "k")
        else:
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(m, "call", fake_call(answers))


ALL = {"cruddb": tdarr_answer, "get-nodes": tdarr_answer, "/tasks/last": TASKS, "/emby/Sessions": SESSIONS,
       "/request/count": COUNTS, "/server/statistics": IMMICH}


def test_every_app_read_renders_its_counts(monkeypatch):
    setup(monkeypatch, ALL)
    page = m.read()
    for src in ("tdarr", "semaphore", "emby", "seerr", "immich"):
        assert f'wall_source_up{{source="{src}"}} 1' in page
    assert 'wall_tdarr_files{stage="transcode",state="queued"} 40' in page
    assert 'wall_tdarr_files{stage="health_check",state="error"} 1' in page
    assert "wall_tdarr_workers_running 2" in page and "wall_tdarr_library_files 812" in page  # the idle one isn't counted
    assert 'wall_tdarr_workers_busy{type="transcode"} 1' in page and 'wall_tdarr_workers_busy{type="health_check"} 1' in page
    assert "wall_semaphore_tasks_active 2" in page and 'wall_semaphore_tasks{status="success"} 2' in page
    assert "wall_emby_streams 2" in page and "wall_emby_transcodes 1" in page
    assert 'wall_seerr_requests{state="pending"} 2' in page
    assert 'wall_immich_assets{type="photo"} 50000' in page and 'wall_immich_usage_bytes{type="all"} 400000000000' in page


def test_an_app_that_cant_be_read_is_down_and_shows_no_zeros(monkeypatch):
    answers = dict(ALL)
    del answers["/emby/Sessions"]
    setup(monkeypatch, answers)
    page = m.read()
    assert 'wall_source_up{source="emby"} 0' in page
    assert "wall_emby_streams" not in page
    assert 'wall_source_up{source="seerr"} 1' in page


def test_an_app_without_its_key_is_down(monkeypatch):
    setup(monkeypatch, ALL, keys=False)
    page = m.read()
    assert 'wall_source_up{source="tdarr"} 1' in page  # no key needed
    for src in ("semaphore", "emby", "seerr", "immich"):
        assert f'wall_source_up{{source="{src}"}} 0' in page
    assert "wall_immich_assets" not in page
