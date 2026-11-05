"""grimmory-authors.py against an in-memory Grimmory: authors no book names are deleted and the
list read back; a dry run deletes nothing; what it can't read stops it with exit 2."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "kubernetes/prod/apps/grimmory/grimmory/app/authors/grimmory-authors.py"
ADMIN_PASS = "admin-" + "test-value"


class FakeGrimmory:
    def __init__(self, keep_deleted=False):
        self.authors = [{"id": 1, "name": "Clare, Cassandra", "bookCount": 0},
                        {"id": 2, "name": "Cassandra Clare", "bookCount": 3},
                        {"id": 3, "name": "Nobody", "bookCount": "0"}]
        self.keep_deleted = keep_deleted
        self.deleted: list[list[int]] = []
        self.login_status = 200
        self.list_answer = None
        self.delete_status = 200

    def call(self, method, path, body=None, token=None):
        if path == "/auth/login":
            ok = self.login_status == 200 and body["password"] == ADMIN_PASS
            return (200, {"accessToken": "t"}) if ok else (self.login_status if self.login_status != 200 else 401, None)
        assert token == "t", "every call after the login carries its token"
        if method == "GET":
            return self.list_answer if self.list_answer else (200, [dict(a) for a in self.authors])
        self.deleted.append(body)
        if self.delete_status != 200:
            return self.delete_status, None
        if not self.keep_deleted:
            self.authors = [a for a in self.authors if a["id"] not in body]
        return 200, None


@pytest.fixture
def authors(monkeypatch):
    def make(dry_run="false", **kw):
        monkeypatch.setenv("GRIMMORY_URL", "http://grimmory.example.invalid/")
        monkeypatch.setenv("GRIMMORY_ADMIN_USERNAME", "admin")
        monkeypatch.setenv("GRIMMORY_ADMIN_PASSWORD", ADMIN_PASS)
        if dry_run is None:
            monkeypatch.delenv("DRY_RUN", raising=False)
        else:
            monkeypatch.setenv("DRY_RUN", dry_run)
        monkeypatch.setattr(sys, "dont_write_bytecode", True)
        loader = importlib.util.spec_from_file_location("grimmory_authors_under_test", SCRIPT)
        module = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(module)
        fake = FakeGrimmory(**kw)
        module.call = fake.call
        return module, fake
    return make


def test_authors_with_no_books_are_deleted_and_read_back(authors, capsys):
    m, fake = authors()
    assert m.main() == 0
    assert fake.deleted == [[1, 3]] and [a["id"] for a in fake.authors] == [2]
    assert "Deleted 2; read back: 1 author(s), none without books." in capsys.readouterr().out


@pytest.mark.parametrize("dry_run", [None, "true", "anything"])
def test_a_dry_run_is_the_default_and_deletes_nothing(authors, capsys, dry_run):
    m, fake = authors(dry_run=dry_run)
    assert m.main() == 0
    assert fake.deleted == []
    assert "would delete author 1: 'Clare, Cassandra'" in capsys.readouterr().out


def test_a_delete_that_didnt_take_answers_1(authors, capsys):
    m, fake = authors(keep_deleted=True)
    assert m.main() == 1
    assert "still with no books" in capsys.readouterr().out


def test_nothing_to_delete_writes_nothing(authors):
    m, fake = authors()
    fake.authors = [a for a in fake.authors if a["id"] == 2]
    assert m.main() == 0 and fake.deleted == []


@pytest.mark.parametrize("break_it", ["login", "list status", "list shape"])
def test_what_it_cant_read_answers_2(authors, capsys, break_it):
    m, fake = authors()
    if break_it == "login":
        fake.login_status = 401
    elif break_it == "list status":
        fake.list_answer = (500, None)
    else:
        fake.list_answer = (200, [{"id": 1, "name": "x"}])
    with pytest.raises(SystemExit) as exit_:
        m.main()
    assert exit_.value.code == 2 and fake.deleted == []
    assert "COULD NOT ANSWER" in capsys.readouterr().out


def test_a_refused_delete_answers_2(authors, capsys):
    m, fake = authors()
    fake.delete_status = 500
    with pytest.raises(SystemExit) as exit_:
        m.main()
    assert exit_.value.code == 2 and "deleting authors answered 500" in capsys.readouterr().out
