"""grimmory-relink.py: a book file Bookshelf moved is pointed at its new path, found by
Grimmory's own content hash; anything less certain is left for Grimmory's scan.

Its SQL runs against SQLite here, through a stand-in for pymysql that translates the few
MySQL-only parts (%s placeholders, <=>, UTC_TIMESTAMP() - INTERVAL 6 HOUR); the files are real,
on a scratch share.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "kubernetes/prod/apps/grimmory/grimmory/app/relink/grimmory-relink.py"


class MySQLError(Exception):
    pass


class Cursor:
    def __init__(self, conn, on_execute):
        self.c, self.on_execute, self.rowcount = conn.cursor(), on_execute, -1

    def execute(self, sql, args=()):
        self.on_execute(sql)
        sql = (sql.replace("%s", "?").replace("<=>", "IS")
               .replace("UTC_TIMESTAMP() - INTERVAL 6 HOUR", "datetime('now', '-6 hours')"))
        try:
            self.c.execute(sql, args)
        except sqlite3.Error as e:
            raise MySQLError(str(e)) from None
        self.rowcount = self.c.rowcount

    def fetchall(self):
        return [tuple(r) for r in self.c.fetchall()]

    def fetchone(self):
        r = self.c.fetchone()
        return tuple(r) if r is not None else None


class DB:
    """Grimmory's tables, as much of them as the job reads."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", isolation_level="DEFERRED")
        self.conn.executescript("""
            CREATE TABLE tasks (type TEXT, status TEXT, created_at TEXT);
            CREATE TABLE library_path (id INTEGER PRIMARY KEY, path TEXT);
            CREATE TABLE book (id INTEGER PRIMARY KEY, library_path_id INTEGER, deleted INTEGER);
            CREATE TABLE book_file (id INTEGER PRIMARY KEY, book_id INTEGER, file_sub_path TEXT, file_name TEXT,
                                    current_hash TEXT, is_folder_based INTEGER);
        """)
        self.conn.commit()
        self.hooks: list = []
        self.rolled_back = False

    def run(self, sql, args=()):
        self.conn.execute(sql, args)
        self.conn.commit()

    def rows(self, sql):
        return [tuple(r) for r in self.conn.execute(sql).fetchall()]

    def cursor(self):
        return Cursor(self.conn, lambda sql: [h(sql) for h in self.hooks])

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.rolled_back = True
        self.conn.rollback()


def ghash(data: bytes) -> str:
    """Grimmory's FileFingerprint, as the job documents it: MD5 of 1 KiB at 0, then 1 KiB << 2i."""
    md5 = hashlib.md5()
    for off in [0] + [1024 << (2 * i) for i in range(11)]:
        if off >= len(data):
            break
        md5.update(data[off:off + 1024])
    return md5.hexdigest()


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Two library paths on a scratch share, Grimmory's database, and the job loaded with them."""
    lib1, lib2 = tmp_path / "books", tmp_path / "comics"
    lib1.mkdir()
    lib2.mkdir()
    (lib1 / ".keep").write_text("x")
    (lib2 / ".keep").write_text("x")
    db = DB()
    db.run("INSERT INTO library_path VALUES (1, ?), (2, ?)", (str(lib1) + "/", str(lib2)))

    def book(bid, lpid, sub, name, data, folder=0, deleted=0, on_disk_at=None):
        db.run("INSERT OR IGNORE INTO book VALUES (?, ?, ?)", (bid, lpid, deleted))
        fid = bid * 10 + len(db.rows(f"SELECT id FROM book_file WHERE book_id = {bid}"))
        db.run("INSERT INTO book_file VALUES (?, ?, ?, ?, ?, ?)", (fid, bid, sub, name, ghash(data), folder))
        if on_disk_at is not None:
            on_disk_at.parent.mkdir(parents=True, exist_ok=True)
            on_disk_at.write_bytes(data)
        return fid

    def load(dry_run="false"):
        monkeypatch.setitem(sys.modules, "pymysql", types.SimpleNamespace(MySQLError=MySQLError, connect=lambda **kw: db))
        for k in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"):
            monkeypatch.setenv(k, "x")
        monkeypatch.setenv("DRY_RUN", dry_run)
        monkeypatch.setattr(sys, "dont_write_bytecode", True)
        loader = importlib.util.spec_from_file_location("grimmory_relink_under_test", SCRIPT)
        module = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(module)
        return module

    return types.SimpleNamespace(db=db, lib1=lib1, lib2=lib2, book=book, load=load)


def test_grimmory_hash_reads_1kib_at_each_offset(world, tmp_path):
    m = world.load()
    data = bytes(range(256)) * 20  # 5120 bytes: the slices at 0, 1024 and 4096
    path = tmp_path / "f"
    path.write_bytes(data)
    assert m.grimmory_hash(path) == hashlib.md5(data[0:1024] + data[1024:2048] + data[4096:5120]).hexdigest()
    small = tmp_path / "s"
    small.write_bytes(b"abc")
    assert m.grimmory_hash(small) == hashlib.md5(b"abc").hexdigest()


def test_a_moved_file_is_relinked_across_library_paths_and_read_back(world, capsys):
    data = b"novel" * 500
    fid = world.book(1, 1, "Old Author", "Book.epub", data)
    (world.lib2 / "New Author").mkdir()
    (world.lib2 / "New Author" / "Book (2020).epub").write_bytes(data)
    assert world.load().main() == 0
    assert world.db.rows(f"SELECT file_sub_path, file_name FROM book_file WHERE id = {fid}") == [("New Author", "Book (2020).epub")]
    assert world.db.rows("SELECT library_path_id FROM book WHERE id = 1") == [(2,)]
    assert "Relinked 1 of 1 file(s)" in capsys.readouterr().out


def test_a_file_moved_to_a_library_root_gets_an_empty_folder(world):
    data = b"root" * 500
    fid = world.book(1, 1, "Somewhere", "Book.epub", data)
    (world.lib1 / "Book.epub").write_bytes(data)
    assert world.load().main() == 0
    assert world.db.rows(f"SELECT file_sub_path, file_name FROM book_file WHERE id = {fid}") == [("", "Book.epub")]


def test_a_dry_run_reports_the_move_and_changes_nothing(world, capsys):
    data = b"novel" * 500
    world.book(1, 1, "Old", "Book.epub", data)
    (world.lib1 / "New").mkdir()
    (world.lib1 / "New" / "Book.epub").write_bytes(data)
    before = world.db.rows("SELECT * FROM book_file")
    assert world.load(dry_run="true").main() == 0
    assert world.db.rows("SELECT * FROM book_file") == before
    assert "would move book 1" in capsys.readouterr().out


def test_what_isnt_certain_is_left_for_grimmory_s_scan(world, capsys):
    gone, twice, folder = b"gone" * 300, b"twice" * 300, b"folder" * 300
    world.book(1, 1, "A", "Gone.epub", gone)                    # no file holds its content
    world.book(2, 1, "B", "Twice.epub", twice)                  # two unclaimed files hold it
    for name in ("x.epub", "y.epub"):
        (world.lib1 / name).write_bytes(twice)
    world.book(3, 1, "C", "Audio", folder, folder=1)            # folder-based
    (world.lib1 / "Audio2").write_bytes(folder)
    same = b"same" * 300                                        # two missing files, one content
    world.book(4, 1, "D", "One.epub", same)
    world.book(5, 1, "E", "Two.epub", same)
    (world.lib1 / "Same.epub").write_bytes(same)
    before = world.db.rows("SELECT * FROM book_file")
    assert world.load().main() == 0
    assert world.db.rows("SELECT * FROM book_file") == before
    out = capsys.readouterr().out
    assert "(no file with its content)" in out and "(2 files share its content)" in out
    assert "(folder-based, not handled here)" in out


def test_a_file_another_book_still_owns_is_never_taken(world, capsys):
    copy = b"copy" * 300
    lost = world.book(1, 1, "A", "Lost.epub", copy)
    world.book(2, 1, "B", "Owned.epub", copy, on_disk_at=world.lib1 / "B" / "Owned.epub")
    assert world.load().main() == 0
    assert world.db.rows(f"SELECT file_sub_path, file_name FROM book_file WHERE id = {lost}") == [("A", "Lost.epub")]
    assert "(no file with its content)" in capsys.readouterr().out


def test_a_book_whose_files_moved_to_two_library_paths_is_left(world, capsys):
    a, b = b"part-a" * 300, b"part-b" * 300
    world.book(1, 1, "X", "a.epub", a)
    world.book(1, 1, "X", "b.pdf", b)
    (world.lib1 / "a.epub").write_bytes(a)
    (world.lib2 / "b.pdf").write_bytes(b)
    before = world.db.rows("SELECT * FROM book_file")
    assert world.load().main() == 0
    assert world.db.rows("SELECT * FROM book_file") == before
    assert "moved to different library paths" in capsys.readouterr().out


def test_a_deleted_book_and_a_file_still_in_place_are_not_touched(world):
    kept = b"kept" * 300
    world.book(1, 1, "A", "In place.epub", kept, on_disk_at=world.lib1 / "A" / "In place.epub")
    world.book(2, 1, "B", "Trashed.epub", b"trash" * 300, deleted=1)
    (world.lib1 / "Trashed.epub").write_bytes(b"trash" * 300)
    before = world.db.rows("SELECT * FROM book_file")
    assert world.load().main() == 0
    assert world.db.rows("SELECT * FROM book_file") == before


def test_a_library_task_in_progress_skips_the_run_but_a_stale_one_doesnt(world, capsys):
    data = b"novel" * 500
    fid = world.book(1, 1, "Old", "Book.epub", data)
    (world.lib1 / "Book.epub").write_bytes(data)
    world.db.run("INSERT INTO tasks VALUES ('SYNC_LIBRARY_FILES', 'IN_PROGRESS', datetime('now'))")
    assert world.load().main() == 0
    assert "Skipped: Grimmory is running SYNC_LIBRARY_FILES" in capsys.readouterr().out
    assert world.db.rows(f"SELECT file_sub_path FROM book_file WHERE id = {fid}") == [("Old",)]
    world.db.run("UPDATE tasks SET created_at = datetime('now', '-7 hours')")
    assert world.load().main() == 0
    assert world.db.rows(f"SELECT file_sub_path FROM book_file WHERE id = {fid}") == [("",)]


def test_a_task_that_starts_meanwhile_stops_it_before_any_write(world, capsys):
    data = b"novel" * 500
    fid = world.book(1, 1, "Old", "Book.epub", data)
    (world.lib1 / "Book.epub").write_bytes(data)
    seen = []

    def start_a_task_on_the_second_look(sql):
        if "FROM tasks" in sql:
            seen.append(sql)
            if len(seen) == 2:
                world.db.run("INSERT INTO tasks VALUES ('SCAN_LIBRARY', 'IN_PROGRESS', datetime('now'))")

    world.db.hooks.append(start_a_task_on_the_second_look)
    assert world.load().main() == 0
    assert "a library task started meanwhile" in capsys.readouterr().out
    assert world.db.rows(f"SELECT file_sub_path FROM book_file WHERE id = {fid}") == [("Old",)]


def test_a_row_that_changed_meanwhile_rolls_everything_back(world):
    first, second = b"first" * 300, b"second" * 300
    f1 = world.book(1, 1, "A", "1.epub", first)
    f2 = world.book(2, 1, "B", "2.epub", second)
    (world.lib1 / "1 moved.epub").write_bytes(first)
    (world.lib1 / "2 moved.epub").write_bytes(second)

    def rename_the_second_under_it(sql):
        if sql.startswith("UPDATE book_file") and not hasattr(rename_the_second_under_it, "done"):
            rename_the_second_under_it.done = True
            # Another writer renames the second file before this job's update reaches it.
            world.db.conn.execute(f"UPDATE book_file SET file_name = 'renamed.epub' WHERE id = {f2}")

    world.db.hooks.append(rename_the_second_under_it)
    with pytest.raises(SystemExit) as exit_:
        world.load().main()
    assert exit_.value.code == 2 and world.db.rolled_back
    assert world.db.rows(f"SELECT file_sub_path, file_name FROM book_file WHERE id = {f1}") == [("A", "1.epub")]


def test_a_share_that_isnt_mounted_answers_2(world):
    (world.lib2 / ".keep").unlink()
    world.book(1, 1, "A", "1.epub", b"x" * 300)
    with pytest.raises(SystemExit) as exit_:
        world.load().main()
    assert exit_.value.code == 2


def test_a_database_error_answers_2_and_rolls_back(world):
    world.db.conn.execute("DROP TABLE book_file")
    with pytest.raises(SystemExit) as exit_:
        world.load().main()
    assert exit_.value.code == 2 and world.db.rolled_back
