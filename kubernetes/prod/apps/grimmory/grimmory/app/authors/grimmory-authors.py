"""Deletes Grimmory's authors that no book names, then reads the list back.

A book Grimmory adds takes its author from the file's own metadata ("Clare, Cassandra");
its metadata lookup then names the author as the book's source does ("Cassandra Clare"),
and the book moves to that author. The first author is left with no books: Grimmory
never removes it (3.5.0), so the Authors page lists both. This deletes each author
whose book count is 0, through Grimmory's own "Delete authors" call (DELETE
/api/v1/authors, which also removes the author's photos). An author with any book,
even one in the trash, is kept.

Signs in as the local admin (the grimmory-sync Secret's GRIMMORY_ADMIN_*). DRY_RUN
(default "true") lists what it would delete and changes nothing. Exit 0 when it ran,
1 when the read-back still finds an author with no books, 2 when it couldn't answer
(Grimmory unreachable, the login refused, an answer it can't read).
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ["GRIMMORY_URL"].rstrip("/") + "/api/v1"
DRY_RUN = os.environ.get("DRY_RUN", "true").strip().lower() != "false"


def fail(msg):
    print(f"COULD NOT ANSWER: {msg}")
    sys.exit(2)


def call(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError) as e:
        fail(f"{method} {path}: {e}")


def empty_authors(token):
    status, body = call("GET", "/authors", token=token)
    if status != 200 or not isinstance(body, list):
        fail(f"listing authors answered {status}")
    try:
        return [(a["id"], a["name"]) for a in body if int(a["bookCount"]) == 0], len(body)
    except (KeyError, TypeError, ValueError):
        fail("the author list isn't in the form this script reads")


def main():
    status, body = call("POST", "/auth/login", {"username": os.environ["GRIMMORY_ADMIN_USERNAME"],
                                                "password": os.environ["GRIMMORY_ADMIN_PASSWORD"]})
    token = body.get("accessToken") if status == 200 and isinstance(body, dict) else None
    if not token:
        fail(f"the local admin's login answered {status}")

    empty, total = empty_authors(token)
    print(f"{total} author(s), {len(empty)} with no books")
    for aid, name in empty:
        print(f"  {'would delete' if DRY_RUN else 'deleting'} author {aid}: {name!r}")
    if not empty or DRY_RUN:
        print("Dry run: nothing changed." if empty else "Nothing to delete.")
        return 0

    status, _ = call("DELETE", "/authors", [aid for aid, _ in empty], token=token)
    if status != 200:
        fail(f"deleting authors answered {status}")
    left, total = empty_authors(token)
    if left:
        print(f"Read back: {len(left)} author(s) still with no books: {[n for _, n in left]}")
        return 1
    print(f"Deleted {len(empty)}; read back: {total} author(s), none without books.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
