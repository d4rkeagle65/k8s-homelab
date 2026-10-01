"""Thin wrapper that writes a file and records it with the FileTracker in
one call, so no call site can forget one half of that pair.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import headers, yamlio
from .filetracker import FileTracker


class Sink:
    def __init__(self, root: Path, dry_run: bool, tracker: FileTracker):
        self.root = root
        self.dry_run = dry_run
        self.tracker = tracker

    def write_yaml(self, path: Path, data: Any, *, header: str | None = None) -> Path:
        content = yamlio.write_yaml_file(path, data, header=header, dry_run=self.dry_run)
        self.tracker.record_written(path, content)
        return path

    def write_yaml_stable(self, path: Path, data: Any, *, extra_lines: list[str] | None = None) -> Path:
        """Write a generated YAML file under the generated-file header (see
        headers.py). The same content always gives the same bytes.
        """
        content = headers.generated_header(extra_lines=extra_lines) + yamlio.to_yaml_string(data)
        if not self.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        self.tracker.record_written(path, content)
        return path

    def write_text(self, path: Path, content: str) -> Path:
        content = yamlio.write_text_file(path, content, dry_run=self.dry_run)
        self.tracker.record_written(path, content)
        return path
