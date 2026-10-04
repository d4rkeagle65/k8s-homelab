"""Tests for _check_output_is_safe, which refuses an --output inside scripts/
or above the repo. With --output defaulting to the current directory, that
is what running from the wrong directory looks like.

The check began with a real incident: the tool used to copy scripts/ into
--output, and an --output inside scripts/ copied it into a subdirectory of
itself, one layer deeper on every run, until the path grew past Windows'
MAX_PATH. That copy is gone; the check stays, since capture and generate
would otherwise write a repo layout among the tool's own source files.

This exercises the fix directly against the real _SCRIPT_SOURCE_DIR (the
scripts/ directory these tests themselves live under), not a synthetic
stand-in, so a future refactor that changes how that constant is computed
still gets caught here.
"""

from __future__ import annotations

import pytest

from k8s_backup.cli import _SCRIPT_SOURCE_DIR, _check_output_is_safe
from k8s_backup.procutil import ToolError


def test_output_inside_scripts_dir_is_rejected():
    with pytest.raises(ToolError):
        _check_output_is_safe(_SCRIPT_SOURCE_DIR / "homelab")


def test_output_equal_to_scripts_dir_is_rejected():
    with pytest.raises(ToolError):
        _check_output_is_safe(_SCRIPT_SOURCE_DIR)


def test_scripts_dir_inside_output_is_rejected():
    # e.g. --output resolving to scripts/'s grandparent or higher.
    with pytest.raises(ToolError):
        _check_output_is_safe(_SCRIPT_SOURCE_DIR.parent.parent)


def test_output_equal_to_own_repo_is_accepted():
    # The workflow the README documents: `python scripts/backup.py all` from
    # the repo root, where --output defaults to the current directory.
    _check_output_is_safe(_SCRIPT_SOURCE_DIR.parent)


def test_sibling_output_directory_is_accepted():
    # A directory beside scripts/ is allowed too: it's neither inside
    # scripts/ nor an ancestor of it.
    _check_output_is_safe(_SCRIPT_SOURCE_DIR.parent / "homelab")
