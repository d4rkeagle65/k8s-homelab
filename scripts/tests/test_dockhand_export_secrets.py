"""dockhand-export-secrets.cjs against a fake Dockhand database, through
dockhand_export_secrets_check.cjs. It needs Node with node:sqlite (22.13 or later, as Dockhand's
image has), so it skips itself where there is none."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

CHECK = Path(__file__).resolve().parent / "dockhand_export_secrets_check.cjs"


def test_export_reads_only_secrets_and_stops_on_what_it_cant_decrypt():
    node = shutil.which("node")
    if not node:
        pytest.skip("node isn't on PATH")
    if subprocess.run([node, "-e", "require('node:sqlite')"], capture_output=True).returncode != 0:
        pytest.skip("this node has no node:sqlite (Node 22.13 or later)")
    result = subprocess.run([node, "--no-warnings", str(CHECK)], capture_output=True, text=True,
                            encoding="utf-8", timeout=120)
    not_passed = [line for line in result.stdout.splitlines() if line.startswith("not ok")]
    assert result.returncode == 0 and result.stdout.rstrip().endswith("ALL PASSED"), not_passed or result.stderr[-500:]
