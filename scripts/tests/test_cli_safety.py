"""Regression test for a real incident: running with --output resolving to
a path inside scripts/ made _copy_scripts_into_repo's shutil.copytree()
copy scripts/ into a subdirectory of itself. Each run nested one layer
deeper into the previous run's leftover copy before finally erroring out
once the accumulated path length exceeded Windows' MAX_PATH, so the
failure surfaced several runs after the actual mistake as repeated
.../scripts/homelab/scripts/homelab/... segments.

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
    # e.g. --output resolving to scripts/'s parent or higher.
    with pytest.raises(ToolError):
        _check_output_is_safe(_SCRIPT_SOURCE_DIR.parent)


def test_sibling_output_directory_is_accepted():
    # The normal case: --output is a directory next to scripts/, not
    # inside it or an ancestor of it.
    _check_output_is_safe(_SCRIPT_SOURCE_DIR.parent / "homelab")
