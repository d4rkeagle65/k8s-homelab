"""Tracks which files a phase wrote, so a second run can remove orphans left
behind by resources that no longer exist in the cluster (task spec: "when a
resource has been deleted from the cluster since the last run, the
corresponding file in the repo should be removed").
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_SCAN_ROOTS = ("kubernetes", "docs")
_SCAN_TOP_FILES = ("README.md", ".sops.yaml", ".gitignore", ".gitattributes", ".githooks/pre-commit")


@dataclass
class RunReport:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class FileTracker:
    def __init__(self, root: Path, owns: Callable[[str], bool], dry_run: bool = False):
        self.root = root
        self.owns = owns
        self.dry_run = dry_run
        self.report = RunReport()
        self._before: dict[str, str] = {}
        self._seen: set[str] = set()
        self._scan_existing()

    def _scan_existing(self) -> None:
        for rel_root in _SCAN_ROOTS:
            base = self.root / rel_root
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(self.root).as_posix()
                if self.owns(rel):
                    self._before[rel] = _hash_file(path)
        for name in _SCAN_TOP_FILES:
            path = self.root / name
            if path.is_file() and self.owns(name):
                self._before[name] = _hash_file(path)

    def record_written(self, path: Path, content: str) -> None:
        rel = path.relative_to(self.root).as_posix()
        self._seen.add(rel)
        new_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        old_hash = self._before.get(rel)
        if old_hash is None:
            self.report.added.append(rel)
        elif old_hash != new_hash:
            self.report.modified.append(rel)
        else:
            self.report.unchanged.append(rel)

    def sweep_orphans(self) -> None:
        orphaned = sorted(set(self._before) - self._seen)
        for rel in orphaned:
            self.report.removed.append(rel)
            if not self.dry_run:
                (self.root / rel).unlink(missing_ok=True)
        if not self.dry_run:
            for rel_root in _SCAN_ROOTS:
                base = self.root / rel_root
                if base.exists():
                    _prune_empty_dirs(base)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prune_empty_dirs(base: Path) -> None:
    for path in sorted((p for p in base.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            if not any(path.iterdir()):
                path.rmdir()
        except OSError:
            pass
