"""Applies organizr.yaml to Organizr through its API, then reads it back.

Runs as the organizr-sync Job whenever organizr.yaml or this script changes.
Steps, each idempotent:

1. First-run setup (Organizr's wizard), only when Organizr has none: the local
   admin, the API key, the hash key and the SQLite database. When the API key
   is refused and the wizard says a database exists, the stored API key and
   ORGANIZR_API_KEY differ: the run stops rather than guess.
2. Settings: every key under settings, written in one call. A value written
   as env:NAME is read from that environment variable, so no secret is in the
   ConfigMap.
3. Categories and tabs, matched by name: missing ones are added, differing
   ones updated. Ones Organizr has that organizr.yaml doesn't list are left
   alone and reported.
4. Read back: every setting (secrets only as present, since Organizr stores
   them encrypted), every category and every tab must match, or the Job fails.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

import yaml

BASE = os.environ["ORGANIZR_URL"].rstrip("/") + "/api/v2"
TOKEN = os.environ["ORGANIZR_API_KEY"]
CONFIG = os.environ.get("ORGANIZR_SYNC_CONFIG", "/etc/organizr-sync/organizr.yaml")
SECRET_WORDS = ("password", "token", "secret", "key", "apikey")


def call(method: str, path: str, body=None, auth: bool = True) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if auth:
        req.add_header("Token", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def data_of(resp: dict):
    return (resp.get("response") or {}).get("data")


def message_of(resp: dict) -> str:
    return str((resp.get("response") or {}).get("message"))


def resolve(value):
    if isinstance(value, str) and value.startswith("env:"):
        name = value[4:]
        if name not in os.environ:
            sys.exit(f"FAIL: organizr.yaml reads {name}, which this Job's environment lacks")
        return os.environ[name]
    return value


def is_secret(key: str) -> bool:
    return any(w in key.lower() for w in SECRET_WORDS)


def wait_ready() -> None:
    last = "no answer"
    for _ in range(60):
        try:
            status, resp = call("GET", "/ping", auth=False)
            if status == 200 and data_of(resp) == "pong":
                return
            last = f"status {status}"
        except OSError as e:
            last = str(e)
        time.sleep(5)
    sys.exit(f"FAIL: Organizr didn't answer /api/v2/ping within 5 minutes (last: {last})")


def first_run(wizard: dict) -> None:
    status, _ = call("GET", "/config")
    if status == 200:
        print("setup: done before, the API key is accepted")
        return
    body = {k: resolve(v) for k, v in wizard.items()}
    status, resp = call("POST", "/wizard", body, auth=False)
    if status == 401 and "already exists" in message_of(resp):
        sys.exit("FAIL: Organizr is set up but refuses ORGANIZR_API_KEY: its stored API key differs")
    if status != 200:
        sys.exit(f"FAIL: first-run setup answered {status}: {message_of(resp)}")
    status, _ = call("GET", "/config")
    if status != 200:
        sys.exit(f"FAIL: first-run setup reported success, but the API key is refused ({status})")
    print("setup: first run done; local admin, API key and database created")


def apply_settings(settings: dict) -> None:
    wanted = {k: resolve(v) for k, v in settings.items()}
    status, resp = call("PUT", "/config", wanted)
    if status != 200:
        sys.exit(f"FAIL: writing settings answered {status}: {message_of(resp)}")
    print(f"settings: wrote {len(wanted)}")


def by_name(items, field: str) -> dict:
    return {i[field]: i for i in items or [] if isinstance(i, dict) and field in i}


def list_of(path: str, field: str) -> dict:
    status, resp = call("GET", path)
    if status != 200:
        sys.exit(f"FAIL: reading {path} answered {status}: {message_of(resp)}")
    data = data_of(resp)
    if isinstance(data, dict):
        data = data.get(path.strip("/")) or data.get("tabs") or data.get("categories") or list(data.values())
    return by_name(data, field)


def differs(have: dict, want: dict) -> dict:
    return {k: v for k, v in want.items() if str(have.get(k)) != str(v)}


def apply_list(path: str, field: str, wanted: list[dict]) -> None:
    have = list_of(path, field)
    for item in wanted:
        name = item[field]
        if name not in have:
            status, resp = call("POST", path, item)
            if status not in (200, 201):
                sys.exit(f"FAIL: adding {path} {name} answered {status}: {message_of(resp)}")
            print(f"{path.strip('/')}: added {name}")
            continue
        change = differs(have[name], item)
        if change:
            status, resp = call("PUT", f"{path}/{have[name]['id']}", change)
            if status != 200:
                sys.exit(f"FAIL: updating {path} {name} answered {status}: {message_of(resp)}")
            print(f"{path.strip('/')}: updated {name} ({', '.join(sorted(change))})")
    extra = sorted(set(have) - {i[field] for i in wanted})
    if extra:
        print(f"{path.strip('/')}: left alone, not in organizr.yaml: {', '.join(extra)}")


def category_ids(categories: list[dict]) -> dict:
    have = list_of("/categories", "category")
    return {name: have[name]["category_id"] for name in (c["category"] for c in categories) if name in have}


def verify(settings: dict, categories: list[dict], tabs: list[dict]) -> None:
    problems = []
    status, resp = call("GET", "/config")
    config = data_of(resp) if status == 200 else None
    if not isinstance(config, dict):
        sys.exit(f"FAIL: reading the settings back answered {status}")
    for key, value in settings.items():
        got = config.get(key)
        if is_secret(key):
            if not got:
                problems.append(f"setting {key} is empty")
        elif str(got).lower() != str(resolve(value)).lower():
            problems.append(f"setting {key} is {got!r}")
    have = list_of("/categories", "category")
    problems += [f"category {c['category']} is missing" for c in categories if c["category"] not in have]
    have = list_of("/tabs", "name")
    for tab in tabs:
        if tab["name"] not in have:
            problems.append(f"tab {tab['name']} is missing")
        elif differs(have[tab["name"]], tab):
            problems.append(f"tab {tab['name']} differs in {', '.join(sorted(differs(have[tab['name']], tab)))}")
    if problems:
        sys.exit("FAIL: read back:\n  " + "\n  ".join(problems))
    print(f"verified: {len(settings)} settings, {len(categories)} categories, {len(tabs)} tabs")


def main() -> None:
    with open(CONFIG, encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    wait_ready()
    first_run(spec["wizard"])
    apply_settings(spec.get("settings") or {})
    categories = spec.get("categories") or []
    apply_list("/categories", "category", categories)
    ids = category_ids(categories)
    tabs = []
    for tab in spec.get("tabs") or []:
        tab = dict(tab)
        cat = tab.pop("category", None)
        if cat is not None:
            if cat not in ids:
                sys.exit(f"FAIL: tab {tab['name']} names category {cat}, which organizr.yaml doesn't define")
            tab["category_id"] = ids[cat]
        tabs.append(tab)
    apply_list("/tabs", "name", tabs)
    verify(spec.get("settings") or {}, categories, tabs)


if __name__ == "__main__":
    main()
