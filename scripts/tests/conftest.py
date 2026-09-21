"""Shared fixtures for the GitOps repo test suite.

Run with `pytest scripts/tests` from the repo root, or `pytest` from
anywhere inside the repo (pytest walks up looking for tests/ automatically).
The repo root is located relative to this file (repo_root/scripts/tests/
conftest.py), not the current working directory, so it works either way.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

REPO_ROOT = Path(__file__).resolve().parents[2]

_yaml = YAML(typ="safe")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def all_files(repo_root: Path) -> list[Path]:
    """Every tracked-by-convention file in the repo, excluding .git."""
    return [
        p
        for p in repo_root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(repo_root).parts
    ]


@pytest.fixture(scope="session")
def all_yaml_files(all_files: list[Path]) -> list[Path]:
    return [p for p in all_files if p.suffix in (".yaml", ".yml")]


@pytest.fixture(scope="session")
def load_yaml():
    def _load(path: Path):
        with open(path, encoding="utf-8") as f:
            return _yaml.load(f)

    return _load


@pytest.fixture(scope="session")
def allowlist(repo_root: Path) -> set[str]:
    """Exact strings the secret/entropy scan should not flag.

    One entry per line in docs/secrets-scan-allowlist.txt (repo-relative,
    optional). Use this only for confirmed-safe values a scan flags by
    mistake (e.g. a legitimately high-entropy but non-secret string) --
    never to silence a real finding. `#`-prefixed lines are comments.
    """
    path = repo_root / "docs" / "secrets-scan-allowlist.txt"
    if not path.exists():
        return set()
    entries = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.add(line)
    return entries
