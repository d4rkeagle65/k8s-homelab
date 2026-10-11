"""Applies booklore.yaml to Booklore through its API, then reads it back.

Runs as the booklore-sync Job whenever booklore.yaml or this script changes.
Steps, each idempotent:

1. First-run setup, only when Booklore has no user yet: the local admin.
2. Libraries, matched by name: missing ones are added with their folders.
   Existing ones are left as they are (changing one rescans it, which is
   Booklore's to do from its UI).
3. Settings: each entry is one of Booklore's settings, by its key name
   (AppSettingKey), written as given. A string value env:NAME anywhere in it
   comes from that environment variable, so no secret is in the ConfigMap.
4. Group mappings (an Authentik group to Booklore's permissions and
   libraries), matched by group: added or updated.
5. Local users, matched by username: missing ones are added; an existing one
   is left alone, and its password is checked by logging in as it.
6. Read back: every setting listed, library, mapping and user must match, or
   the Job fails. Secrets are checked only as set, since Booklore masks them.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

import yaml

BASE = os.environ["BOOKLORE_URL"].rstrip("/") + "/api/v1"
CONFIG = os.environ.get("BOOKLORE_SYNC_CONFIG", "/etc/booklore-sync/booklore.yaml")
TOKEN = None


def call(method: str, path: str, body=None, token=None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token or TOKEN:
        req.add_header("Authorization", "Bearer " + (token or TOKEN))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else None
        except ValueError:
            return e.code, raw.decode(errors="replace")[:200]


def must(status: int, body, what: str, ok=(200, 201, 204)):
    if status not in ok:
        sys.exit(f"FAIL: {what} answered {status}: {body}")
    return body


def resolve(value):
    if isinstance(value, dict):
        return {k: resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v) for v in value]
    if isinstance(value, str) and value.startswith("env:"):
        name = value[4:]
        if name not in os.environ:
            sys.exit(f"FAIL: booklore.yaml reads {name}, which this Job's environment lacks")
        return os.environ[name]
    return value


def wait_ready() -> None:
    last = "no answer"
    for _ in range(90):
        try:
            status, _ = call("GET", "/healthcheck")
            if status == 200:
                return
            last = f"status {status}"
        except OSError as e:
            last = str(e)
        time.sleep(5)
    sys.exit(f"FAIL: Booklore didn't answer /api/v1/healthcheck within 7.5 minutes (last: {last})")


def login(username: str, password: str) -> str | None:
    status, body = call("POST", "/auth/login", {"username": username, "password": password})
    if status == 200 and isinstance(body, dict) and body.get("accessToken"):
        return body["accessToken"]
    return None


def first_run(admin: dict) -> None:
    global TOKEN
    status, body = call("GET", "/setup/status")
    done = must(status, body, "setup status")
    if not (isinstance(done, dict) and done.get("data") is True):
        must(*call("POST", "/setup", admin), "first-run setup")
        print("setup: first run done; local admin created")
    else:
        print("setup: done before")
    TOKEN = login(admin["username"], admin["password"])
    if not TOKEN:
        sys.exit("FAIL: the local admin from booklore.yaml can't log in")


def libraries_by_name() -> dict:
    return {lib["name"]: lib for lib in must(*call("GET", "/libraries"), "listing libraries")}


def apply_libraries(wanted: list[dict]) -> dict:
    have = libraries_by_name()
    for lib in wanted:
        if lib["name"] in have:
            continue
        body = dict(lib)
        body["paths"] = [{"path": p} for p in lib["paths"]]
        must(*call("POST", "/libraries", body), f"adding library {lib['name']}")
        print(f"libraries: added {lib['name']}")
    extra = sorted(set(have) - {lib["name"] for lib in wanted})
    if extra:
        print(f"libraries: left alone, not in booklore.yaml: {', '.join(extra)}")
    return {name: lib["id"] for name, lib in libraries_by_name().items()}


def apply_settings(settings: dict) -> None:
    if not settings:
        return
    body = [{"name": k, "value": resolve(v)} for k, v in settings.items()]
    must(*call("PUT", "/settings", body), "writing settings")
    print(f"settings: wrote {len(body)}")


def mapping_body(m: dict, library_ids: dict) -> dict:
    missing = [n for n in m.get("libraries", []) if n not in library_ids]
    if missing:
        sys.exit(f"FAIL: mapping {m['group']} names libraries booklore.yaml doesn't define: {missing}")
    return {
        "oidcGroupClaim": m["group"],
        "isAdmin": bool(m.get("admin", False)),
        "permissions": sorted(m.get("permissions", [])),
        "libraryIds": sorted(library_ids[n] for n in m.get("libraries", [])),
        "description": m.get("description", ""),
    }


def apply_mappings(wanted: list[dict], library_ids: dict) -> None:
    have = {m["oidcGroupClaim"]: m for m in must(*call("GET", "/admin/oidc-group-mappings"), "listing group mappings")}
    for m in wanted:
        body = mapping_body(m, library_ids)
        cur = have.get(m["group"])
        if cur is None:
            must(*call("POST", "/admin/oidc-group-mappings", body), f"adding mapping {m['group']}")
            print(f"group mappings: added {m['group']}")
        elif mapping_differs(cur, body):
            must(*call("PUT", f"/admin/oidc-group-mappings/{cur['id']}", dict(body, id=cur["id"])), f"updating mapping {m['group']}")
            print(f"group mappings: updated {m['group']}")


def mapping_differs(cur: dict, want: dict) -> bool:
    return (bool(cur.get("isAdmin")) != want["isAdmin"]
            or sorted(cur.get("permissions") or []) != want["permissions"]
            or sorted(cur.get("libraryIds") or []) != want["libraryIds"])


def apply_users(wanted: list[dict], library_ids: dict) -> None:
    have = {u["username"]: u for u in must(*call("GET", "/users"), "listing users")}
    for u in wanted:
        u = resolve(u)
        if u["username"] in have:
            continue
        body = {k: v for k, v in u.items() if k not in ("libraries", "permissions")}
        body.update({p: True for p in u.get("permissions", [])})
        body["selectedLibraries"] = sorted(library_ids[n] for n in u.get("libraries", []))
        must(*call("POST", "/auth/register", body), f"adding user {u['username']}")
        print(f"users: added {u['username']}")


def verify(spec: dict, library_ids: dict) -> None:
    problems = []
    current = must(*call("GET", "/settings"), "reading settings back")
    for key, value in (spec.get("settings") or {}).items():
        field = "".join(w.capitalize() if i else w.lower() for i, w in enumerate(key.split("_")))
        got = current.get(field)
        problems += compare(field, got, resolve(value))
    have = libraries_by_name()
    for lib in spec.get("libraries") or []:
        if lib["name"] not in have:
            problems.append(f"library {lib['name']} is missing")
        elif sorted(p["path"] for p in have[lib["name"]].get("paths") or []) != sorted(lib["paths"]):
            problems.append(f"library {lib['name']} has other folders")
    mappings = {m["oidcGroupClaim"]: m for m in must(*call("GET", "/admin/oidc-group-mappings"), "reading mappings back")}
    for m in spec.get("groupMappings") or []:
        if m["group"] not in mappings:
            problems.append(f"group mapping {m['group']} is missing")
        elif mapping_differs(mappings[m["group"]], mapping_body(m, library_ids)):
            problems.append(f"group mapping {m['group']} differs")
    users = {u["username"] for u in must(*call("GET", "/users"), "reading users back")}
    for u in spec.get("users") or []:
        u = resolve(u)
        if u["username"] not in users:
            problems.append(f"user {u['username']} is missing")
        elif not login(u["username"], u["password"]):
            problems.append(f"user {u['username']} can't log in with its password from booklore.yaml")
    if problems:
        sys.exit("FAIL: read back:\n  " + "\n  ".join(problems))
    print(f"verified: {len(spec.get('settings') or {})} settings, {len(have)} libraries, "
          f"{len(spec.get('groupMappings') or [])} group mappings, {len(spec.get('users') or [])} users")


def compare(path: str, got, want) -> list[str]:
    if isinstance(want, dict):
        if not isinstance(got, dict):
            return [f"setting {path} is {type(got).__name__}"]
        out = []
        for k, v in want.items():
            if "secret" in k.lower():
                if not got.get(k):
                    out.append(f"setting {path}.{k} is empty")
                continue
            out += compare(f"{path}.{k}", got.get(k), v)
        return out
    if isinstance(want, list):
        return [] if sorted(map(str, got or [])) == sorted(map(str, want)) else [f"setting {path} is {got!r}"]
    return [] if str(got).lower() == str(want).lower() else [f"setting {path} is {got!r}"]


def main() -> None:
    with open(CONFIG, encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    wait_ready()
    first_run(resolve(spec["admin"]))
    library_ids = apply_libraries(spec.get("libraries") or [])
    apply_settings(spec.get("settings") or {})
    apply_mappings(spec.get("groupMappings") or [], library_ids)
    apply_users(spec.get("users") or [], library_ids)
    verify(spec, library_ids)


if __name__ == "__main__":
    main()
