"""Points Grimmory's books at their files again after Bookshelf moves or renames them.

Grimmory's library scan (SYNC_LIBRARY_FILES, nightly) matches books to files by path: a file
that moved is a deleted book and a new one, and the deleted book takes its locked metadata,
shelves, reading progress, notes and Kobo state with it. Grimmory recognises a move only when
its folder watcher sees it, and the watcher sees only changes made from its own pod: Bookshelf
writes the share over NFS from another. So this job does what the watcher would have done
(BookFilePersistenceService.updatePathIfChanged in Grimmory 3.5.0), before the scan runs:

1. Reads every live book's files from Grimmory's database (book_file: path and content hash).
2. Lists the files on the share, read-only at the same path Grimmory mounts it.
3. A database file missing on disk, whose hash belongs to exactly one file no book claims, has
   moved there: its book_file row gets the new folder and name, its book the library path.

The hash is Grimmory's own (FileFingerprint.generateHash: MD5 of 1 KiB at offsets 0, 1 KiB and
then x4 up to 1 GiB), so a match is the same content. A missing file with no match, or with
more than one, is only reported: the scan then treats it as Grimmory always has. So is a
folder-based audiobook (none in the library). Nothing runs while one of Grimmory's library
tasks is in progress.

DRY_RUN (default "true") reports what it would change and changes nothing. Exit 0 when it ran
(moves made or none needed), 2 when it couldn't answer (database or share unreadable).
"""
import hashlib
import os
import sys

import pymysql

DRY_RUN = os.environ.get("DRY_RUN", "true").strip().lower() != "false"
# Offsets FileFingerprint reads: 1024 << (2 * i) for i in -1..10, where Java's i = -1 shifts to 0.
OFFSETS = [0] + [1024 << (2 * i) for i in range(11)]
# A task left IN_PROGRESS by a crash would block every run, so only recent ones count.
BUSY_SQL = ("SELECT type FROM tasks WHERE status = 'IN_PROGRESS' AND type LIKE '%LIBRARY%' "
            "AND created_at > UTC_TIMESTAMP() - INTERVAL 6 HOUR")


def fail(msg):
    print(f"COULD NOT ANSWER: {msg}")
    sys.exit(2)


def grimmory_hash(path):
    size = os.path.getsize(path)
    md5 = hashlib.md5()
    with open(path, "rb") as f:
        for off in OFFSETS:
            if off >= size:
                break
            f.seek(off)
            md5.update(f.read(1024))
    return md5.hexdigest()


def main():
    try:
        db = pymysql.connect(host=os.environ["DB_HOST"], user=os.environ["DB_USER"],
                             password=os.environ["DB_PASSWORD"], database=os.environ["DB_NAME"],
                             autocommit=False, connect_timeout=20)
    except Exception as e:  # noqa: BLE001 - any failure here means no answer
        fail(f"database: {e}")
    cur = db.cursor()

    cur.execute(BUSY_SQL)
    busy = [r[0] for r in cur.fetchall()]
    if busy:
        print(f"Skipped: Grimmory is running {', '.join(busy)}; the next run looks again.")
        return 0

    cur.execute("SELECT id, path FROM library_path")
    lib_paths = {lid: path.rstrip("/") for lid, path in cur.fetchall()}
    for path in lib_paths.values():
        if not os.path.isdir(path) or not os.listdir(path):
            fail(f"library path {path} is missing or empty here: the share isn't mounted")

    cur.execute("SELECT f.id, f.book_id, b.library_path_id, f.file_sub_path, f.file_name, "
                "f.current_hash, f.is_folder_based FROM book_file f JOIN book b ON b.id = f.book_id "
                "WHERE NOT COALESCE(b.deleted, 0) AND b.library_path_id IS NOT NULL")
    rows = cur.fetchall()
    known, missing = set(), []
    for fid, bid, lpid, sub, name, fhash, folder in rows:
        full = os.path.join(lib_paths[lpid], sub or "", name)
        known.add(full)
        if not os.path.exists(full):
            missing.append((fid, bid, lpid, sub, name, fhash, folder, full))
    print(f"{len(rows)} book file(s) in Grimmory, {len(missing)} missing on the share")
    if not missing:
        print("Nothing moved.")
        return 0

    # Files on the share no book claims, by Grimmory's hash.
    unclaimed = {}
    for lpid, root in lib_paths.items():
        for d, _, files in os.walk(root):
            for name in files:
                full = os.path.join(d, name)
                if full not in known:
                    try:
                        unclaimed.setdefault(grimmory_hash(full), []).append((lpid, root, full))
                    except OSError as e:
                        print(f"  unreadable, skipped: {full}: {e}")
    wanted = {}
    for m in missing:
        wanted[m[5]] = wanted.get(m[5], 0) + 1

    plans, unmatched = [], []
    for fid, bid, lpid, sub, name, fhash, folder, full in missing:
        found = unclaimed.get(fhash, [])
        if folder:
            unmatched.append((full, "folder-based, not handled here"))
        elif len(found) == 1 and wanted[fhash] == 1:
            new_lpid, root, new_full = found[0]
            new_sub = os.path.relpath(os.path.dirname(new_full), root)
            plans.append((fid, bid, sub, name, fhash, new_lpid, "" if new_sub == "." else new_sub,
                          os.path.basename(new_full), full, new_full))
        else:
            unmatched.append((full, "no file with its content" if not found else
                              f"{len(found)} files share its content"))

    # One book's files must all land under one library path (the book row holds it).
    by_book = {}
    for p in plans:
        by_book.setdefault(p[1], set()).add(p[5])
    split = {b for b, lps in by_book.items() if len(lps) > 1}
    for p in [p for p in plans if p[1] in split]:
        unmatched.append((p[8], "its book's files moved to different library paths"))
    plans = [p for p in plans if p[1] not in split]

    for p in plans:
        print(f"  {'would move' if DRY_RUN else 'moving'} book {p[1]} file {p[0]}: {p[8]} -> {p[9]}")
    for full, why in unmatched:
        print(f"  left for Grimmory's scan ({why}): {full}")
    if DRY_RUN or not plans:
        print(f"{'Dry run: ' if DRY_RUN else ''}{len(plans)} file(s) to relink, {len(unmatched)} left; nothing changed.")
        return 0

    cur.execute(BUSY_SQL)
    if cur.fetchall():
        print("Skipped: a library task started meanwhile; the next run looks again.")
        return 0
    for fid, bid, sub, name, fhash, new_lpid, new_sub, new_name, _, _ in plans:
        cur.execute("UPDATE book_file SET file_sub_path = %s, file_name = %s WHERE id = %s AND "
                    "file_sub_path = %s AND file_name = %s AND current_hash = %s",
                    (new_sub, new_name, fid, sub, name, fhash))
        if cur.rowcount != 1:
            db.rollback()
            fail(f"book_file {fid} changed while this ran; nothing was changed")
        cur.execute("UPDATE book SET library_path_id = %s WHERE id = %s", (new_lpid, bid))
    db.commit()

    # Read back: each row names the new file, and the file is there.
    bad = 0
    for fid, _, _, _, _, new_lpid, new_sub, new_name, _, new_full in plans:
        cur.execute("SELECT f.file_sub_path, f.file_name, b.library_path_id FROM book_file f "
                    "JOIN book b ON b.id = f.book_id WHERE f.id = %s", (fid,))
        got = cur.fetchone()
        if got != (new_sub, new_name, new_lpid) or not os.path.exists(new_full):
            bad += 1
            print(f"  NOT APPLIED: book_file {fid} reads {got}")
    db.commit()
    print(f"Relinked {len(plans) - bad} of {len(plans)} file(s); {len(unmatched)} left for the scan.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
