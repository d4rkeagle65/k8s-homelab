"""generate, run on a copy of the committed tree, changes nothing: every generated file in git is
what the builders (flux.py, generate.py) write today. A builder that changes what it writes
fails here, in the same commit, rather than in whichever later run regenerates the tree; a
generated file edited by hand fails here too, since the next generate would undo it.

Only tracked files are copied, as a fresh clone has them: generate must write the same with
or without kubernetes/.local/.
"""

from __future__ import annotations

import subprocess

from k8s_backup import generate


def _tracked(repo_root) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=repo_root, capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def _tree(root) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_generate_on_the_committed_tree_changes_nothing(repo_root, tmp_path, capsys):
    for rel in _tracked(repo_root):
        src = repo_root / rel
        if not src.is_file():  # deleted in the working tree, not yet in the index
            continue
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    before = _tree(tmp_path)
    assert any(r.endswith("/ks.yaml") for r in before), "copied no release, so nothing was checked"

    generate.run(tmp_path, dry_run=False, verbose=False)
    capsys.readouterr()
    after = _tree(tmp_path)

    changed = sorted(r for r in before if r in after and after[r] != before[r])
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    assert not (changed or added or removed), (
        "generate doesn't reproduce the committed tree; run `python scripts/backup.py generate` and "
        f"commit what it writes, or fix the builder. Changed: {changed[:10]}; written new: {added[:10]}; "
        f"removed: {removed[:10]}")
