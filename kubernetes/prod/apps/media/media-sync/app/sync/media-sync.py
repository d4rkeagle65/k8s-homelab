"""Applies media-sync.yaml to the media apps through their APIs, then reads it back.

Runs as the media-sync Job whenever media-sync.yaml or this script changes. Each
section is one app, compared setting by setting with what the app has:

- bookshelf: /api/v1/config/development (the metadata source).
- qbittorrent: listed preferences, and categories with their save paths (a
  missing one is created; one not listed is left alone).
- sabnzbd: listed misc settings, and categories (dir, post-processing, script,
  priority).
- seerr: its Radarr and Sonarr links, matched by name (a missing one is added).

DRY_RUN=true reports every difference and changes nothing. Otherwise each
difference is written and the section read again: a value that didn't take
fails the Job. An app that can't be read fails the Job too, rather than being
taken as unchanged. A value env:NAME comes from that environment variable; an
API key is never printed.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import yaml

CONFIG = os.environ.get("MEDIA_SYNC_CONFIG", "/etc/media-sync/media-sync.yaml")
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
FAILED: list[str] = []
SECRET_KEYS = ("apikey", "api_key", "password")


class CantRead(Exception):
    pass


def resolve(value):
    if isinstance(value, dict):
        return {k: resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v) for v in value]
    if isinstance(value, str) and value.startswith("env:"):
        name = value[4:]
        if name not in os.environ:
            sys.exit(f"FAIL: media-sync.yaml reads {name}, which this Job's environment lacks")
        return os.environ[name]
    return value


def shown(key: str, value) -> str:
    return "<hidden>" if any(s in key.lower() for s in SECRET_KEYS) else json.dumps(value)


def http(method: str, url: str, headers: dict | None = None, body=None, form: dict | None = None):
    data = None
    hdrs = dict(headers or {})
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except OSError as e:
        raise CantRead(f"{method} {url.split('?')[0]}: {e}") from None
    text = raw.decode(errors="replace")
    try:
        parsed = json.loads(text) if text.strip() else None
    except ValueError:
        parsed = text
    return status, parsed


def get_json(url: str, headers: dict | None = None):
    status, body = http("GET", url, headers)
    if status != 200 or body is None or isinstance(body, str):
        raise CantRead(f"GET {url.split('?')[0]} answered {status}")
    return body


def same(have, want) -> bool:
    """Equal as the app means it: APIs return numbers and booleans as text in places."""
    if isinstance(want, bool):
        return have is want or str(have).lower() == str(want).lower()
    if isinstance(want, (int, float)):
        number = have if isinstance(have, (int, float)) and not isinstance(have, bool) else None
        if isinstance(have, str) and re.fullmatch(r"-?\d+(\.\d+)?", have.strip()):
            number = float(have)
        return number is not None and float(number) == float(want)
    if want is None:
        return have in (None, "", "None")
    if isinstance(want, (list, dict)):
        return have == want
    return have is not None and str(have) == str(want)


def diffs(have: dict, want: dict) -> dict:
    return {k: v for k, v in want.items() if not same(have.get(k), v)}


def report(section: str, changes: dict, have: dict, what: str = "") -> None:
    label = f"{section}{' ' + what if what else ''}"
    if not changes:
        print(f"{label}: unchanged")
        return
    verb = "would change" if DRY_RUN else "changing"
    for k, v in changes.items():
        print(f"{label}: {verb} {k}: {shown(k, have.get(k))} -> {shown(k, v)}")


def check_back(section: str, what: str, have: dict, want: dict) -> None:
    left = diffs(have, want)
    if left:
        FAILED.append(f"{section} {what}: didn't take: {', '.join(left)}")
        print(f"FAIL: {section} {what}: read back differs on {', '.join(left)}")
    else:
        print(f"{section} {what}: read back")


# --- Bookshelf ---------------------------------------------------------------

def sync_bookshelf(want: dict) -> None:
    base = os.environ["BOOKSHELF_URL"].rstrip("/") + "/api/v1"
    hdr = {"X-Api-Key": os.environ["BOOKSHELF_API_KEY"]}
    for name, wanted in want.items():
        have = get_json(f"{base}/config/{name}", hdr)
        changes = diffs(have, wanted)
        report("bookshelf", changes, have, name)
        if changes and not DRY_RUN:
            status, body = http("PUT", f"{base}/config/{name}/{have['id']}", hdr, {**have, **wanted})
            if status not in (200, 202):
                FAILED.append(f"bookshelf {name}: PUT answered {status}")
                continue
            check_back("bookshelf", name, get_json(f"{base}/config/{name}", hdr), wanted)


# --- qBittorrent -------------------------------------------------------------

def sync_qbittorrent(want: dict) -> None:
    base = os.environ["QBITTORRENT_URL"].rstrip("/") + "/api/v2"
    hdr = {"Authorization": "Bearer " + os.environ["QBITTORRENT_API_KEY"]}
    prefs = want.get("preferences") or {}
    if prefs:
        have = get_json(f"{base}/app/preferences", hdr)
        changes = diffs(have, prefs)
        report("qbittorrent", changes, have, "preferences")
        if changes and not DRY_RUN:
            status, _ = http("POST", f"{base}/app/setPreferences", hdr, form={"json": json.dumps(changes)})
            if status != 200:
                FAILED.append(f"qbittorrent preferences: setPreferences answered {status}")
            else:
                check_back("qbittorrent", "preferences", get_json(f"{base}/app/preferences", hdr), prefs)
    cats = want.get("categories") or {}
    if cats:
        have = get_json(f"{base}/torrents/categories", hdr)
        paths = {n: (v or {}).get("savePath", "") for n, v in have.items()}
        changes = {n: p for n, p in cats.items() if n not in paths or paths[n] != p}
        missing = [n for n in changes if n not in paths]
        report("qbittorrent", changes, paths, "categories")
        for n in missing:
            print(f"qbittorrent categories: {'would create' if DRY_RUN else 'creating'} {n}")
        if changes and not DRY_RUN:
            for n, p in changes.items():
                op = "createCategory" if n in missing else "editCategory"
                status, _ = http("POST", f"{base}/torrents/{op}", hdr, form={"category": n, "savePath": p})
                if status != 200:
                    FAILED.append(f"qbittorrent category {n}: {op} answered {status}")
            back = get_json(f"{base}/torrents/categories", hdr)
            check_back("qbittorrent", "categories",
                       {n: (v or {}).get("savePath", "") for n, v in back.items()}, cats)


# --- SABnzbd -----------------------------------------------------------------

def sab(params: dict):
    url = os.environ["SABNZBD_URL"].rstrip("/") + "/api?" + urllib.parse.urlencode(
        {**params, "output": "json", "apikey": os.environ["SABNZBD_API_KEY"]})
    return http("GET", url)


def sab_config(section: str):
    status, body = sab({"mode": "get_config", "section": section})
    if status != 200 or not isinstance(body, dict) or "config" not in body:
        raise CantRead(f"SABnzbd get_config {section} answered {status}")
    return body["config"][section]


def sync_sabnzbd(want: dict) -> None:
    misc = want.get("misc") or {}
    if misc:
        have = sab_config("misc")
        changes = diffs(have, misc)
        report("sabnzbd", changes, have, "misc")
        if changes and not DRY_RUN:
            for k, v in changes.items():
                status, body = sab({"mode": "set_config", "section": "misc", "keyword": k, "value": v})
                if status != 200:
                    FAILED.append(f"sabnzbd misc {k}: set_config answered {status}")
            check_back("sabnzbd", "misc", sab_config("misc"), misc)
    cats = want.get("categories") or {}
    if cats:
        have = {c["name"]: c for c in sab_config("categories")}
        for name, fields in cats.items():
            cur = have.get(name, {})
            changes = {k: v for k, v in fields.items() if str(cur.get(k, "")) != str(v)}
            what = f"category {name}" + ("" if name in have else " (missing)")
            report("sabnzbd", changes, cur, what)
            if changes and not DRY_RUN:
                status, _ = sab({"mode": "set_config", "section": "categories", "keyword": name,
                                 **{k: str(v) for k, v in fields.items()}})
                if status != 200:
                    FAILED.append(f"sabnzbd {what}: set_config answered {status}")
        if not DRY_RUN:
            back = {c["name"]: c for c in sab_config("categories")}
            left = [n for n, f in cats.items()
                    if any(str(back.get(n, {}).get(k, "")) != str(v) for k, v in f.items())]
            if left:
                FAILED.append(f"sabnzbd categories: didn't take: {', '.join(left)}")
                print(f"FAIL: sabnzbd categories: read back differs on {', '.join(left)}")
            else:
                print("sabnzbd categories: read back")


# --- Seerr -------------------------------------------------------------------

def sync_seerr(want: dict) -> None:
    base = os.environ["SEERR_URL"].rstrip("/") + "/api/v1/settings"
    hdr = {"X-Api-Key": os.environ["SEERR_API_KEY"]}
    for kind in ("radarr", "sonarr"):
        servers = want.get(kind) or []
        if not servers:
            continue
        have = {s["name"]: s for s in get_json(f"{base}/{kind}", hdr)}
        for wanted in servers:
            name = wanted["name"]
            cur = have.get(name)
            if cur is None:
                print(f"seerr {kind} {name}: {'would add' if DRY_RUN else 'adding'}")
                if not DRY_RUN:
                    status, _ = http("POST", f"{base}/{kind}", hdr, wanted)
                    if status not in (200, 201):
                        FAILED.append(f"seerr {kind} {name}: POST answered {status}")
                        continue
            else:
                changes = diffs(cur, wanted)
                report("seerr", changes, cur, f"{kind} {name}")
                if not changes or DRY_RUN:
                    continue
                status, _ = http("PUT", f"{base}/{kind}/{cur['id']}", hdr, {**cur, **wanted})
                if status not in (200, 201):
                    FAILED.append(f"seerr {kind} {name}: PUT answered {status}")
                    continue
            back = {s["name"]: s for s in get_json(f"{base}/{kind}", hdr)}
            check_back("seerr", f"{kind} {name}", back.get(name, {}), wanted)


SECTIONS = {
    "bookshelf": sync_bookshelf,
    "qbittorrent": sync_qbittorrent,
    "sabnzbd": sync_sabnzbd,
    "seerr": sync_seerr,
}


def main() -> None:
    with open(CONFIG, encoding="utf-8") as f:
        config = resolve(yaml.safe_load(f) or {})
    unknown = sorted(set(config) - set(SECTIONS))
    if unknown:
        sys.exit(f"FAIL: media-sync.yaml has sections this script doesn't know: {', '.join(unknown)}")
    print(f"media-sync: {'DRY RUN, reporting only' if DRY_RUN else 'applying'}")
    for name, fn in SECTIONS.items():
        if name not in config:
            continue
        try:
            fn(config[name])
        except CantRead as e:
            FAILED.append(f"{name}: couldn't read it ({e})")
            print(f"FAIL: {name}: couldn't read it ({e}); nothing changed there")
    if FAILED:
        sys.exit("FAIL: " + "; ".join(FAILED))
    print("media-sync: done")


if __name__ == "__main__":
    main()
