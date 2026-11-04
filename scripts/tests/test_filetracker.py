"""FileTracker: what a run wrote, against what was on disk before it, and the orphans it then
removes. It decides what capture deletes, so each rule is shown on a throwaway tree."""

from __future__ import annotations

from k8s_backup.filetracker import FileTracker


def _tree(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _owns_generated(rel):
    return not rel.startswith("kubernetes/hand/")


def test_a_write_is_added_modified_or_unchanged_by_its_content(tmp_path):
    _tree(tmp_path, {"kubernetes/a.yaml": "a\n", "kubernetes/b.yaml": "b\n"})
    t = FileTracker(tmp_path, _owns_generated)
    t.record_written(tmp_path / "kubernetes/a.yaml", "a\n")
    t.record_written(tmp_path / "kubernetes/b.yaml", "b changed\n")
    t.record_written(tmp_path / "kubernetes/c.yaml", "c\n")
    assert (t.report.unchanged, t.report.modified, t.report.added) == (
        ["kubernetes/a.yaml"], ["kubernetes/b.yaml"], ["kubernetes/c.yaml"])
    assert t.was_written(tmp_path / "kubernetes/c.yaml")
    assert not t.was_written(tmp_path / "kubernetes/d.yaml")


def test_sweep_removes_owned_orphans_only_and_prunes_the_folders_they_leave(tmp_path):
    _tree(tmp_path, {
        "kubernetes/prod/apps/gone/gone/ks.yaml": "x\n",   # an orphan, alone in its folders
        "kubernetes/prod/apps/kept/ks.yaml": "x\n",       # written again this run
        "kubernetes/hand/notes.yaml": "x\n",              # not the tracker's
        "README.md": "x\n",                               # a top-level file it owns
        "elsewhere/file.yaml": "x\n",                     # outside what it scans
    })
    t = FileTracker(tmp_path, _owns_generated)
    t.record_written(tmp_path / "kubernetes/prod/apps/kept/ks.yaml", "x\n")
    t.sweep_orphans()
    assert t.report.removed == ["README.md", "kubernetes/prod/apps/gone/gone/ks.yaml"]
    assert not (tmp_path / "kubernetes/prod/apps/gone").exists(), "the folders it emptied go too"
    assert (tmp_path / "kubernetes/prod/apps/kept/ks.yaml").is_file()
    assert (tmp_path / "kubernetes/hand/notes.yaml").is_file()
    assert (tmp_path / "elsewhere/file.yaml").is_file()


def test_a_folder_that_still_holds_something_stays(tmp_path):
    _tree(tmp_path, {"kubernetes/prod/x/old.yaml": "x\n", "kubernetes/prod/x/hand/keep.txt": "x\n"})
    (tmp_path / "kubernetes/prod/empty-already").mkdir(parents=True)
    t = FileTracker(tmp_path, lambda rel: rel.endswith(".yaml"))
    t.sweep_orphans()
    assert not (tmp_path / "kubernetes/prod/x/old.yaml").exists()
    assert (tmp_path / "kubernetes/prod/x/hand/keep.txt").is_file()
    assert not (tmp_path / "kubernetes/prod/empty-already").exists()


def test_a_dry_run_reports_the_orphans_and_removes_nothing(tmp_path):
    _tree(tmp_path, {"kubernetes/gone/ks.yaml": "x\n"})
    t = FileTracker(tmp_path, _owns_generated, dry_run=True)
    t.sweep_orphans()
    assert t.report.removed == ["kubernetes/gone/ks.yaml"]
    assert (tmp_path / "kubernetes/gone/ks.yaml").is_file()


def test_kept_files_are_current_and_never_swept(tmp_path):
    _tree(tmp_path, {"kubernetes/held.yaml": "x\n", "kubernetes/gone.yaml": "x\n"})
    t = FileTracker(tmp_path, _owns_generated)
    t.keep_existing([tmp_path / "kubernetes/held.yaml", tmp_path / "kubernetes/never-there.yaml"])
    t.sweep_orphans()
    assert t.report.unchanged == ["kubernetes/held.yaml"]
    assert t.report.removed == ["kubernetes/gone.yaml"]
    assert (tmp_path / "kubernetes/held.yaml").is_file()
