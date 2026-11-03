"""Shared fixtures for the GitOps repo test suite.

Run with `pytest scripts/tests` from the repo root, or `pytest` from
anywhere inside the repo (pytest walks up looking for tests/ automatically).
The repo root is located relative to this file (repo_root/scripts/tests/
conftest.py), not the current working directory, so it works either way.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from ruamel.yaml.composer import ComposerError

REPO_ROOT = Path(__file__).resolve().parents[2]

_yaml = YAML(typ="safe", pure=True)


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


# Each file parsed once a session: ruamel's pure-Python parser takes about 5 seconds over the
# repo's YAML, and several tests read every file. Keyed by path, size and mtime, so a file a test
# writes is read afresh; an error is kept and raised again, as a fresh parse would.
_parsed: dict[tuple, tuple] = {}


def _documents(path: Path) -> list:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _parsed:
        try:
            _parsed[key] = ("ok", list(_yaml.load_all(path.read_text(encoding="utf-8"))))
        except Exception as e:  # noqa: BLE001 - kept to raise again for the caller to judge
            _parsed[key] = ("error", e)
    kind, value = _parsed[key]
    if kind == "error":
        raise value
    return value


@pytest.fixture(scope="session")
def load_yaml():
    """A file's one YAML document (None when it has none), as YAML.load gives it: a file of
    several documents raises ComposerError. Each call gets its own copy, so a test may change
    what it's given."""
    def _load(path: Path):
        docs = _documents(path)
        if len(docs) > 1:
            raise ComposerError("expected a single document in the stream", None,
                                "but found another document", None)
        return copy.deepcopy(docs[0]) if docs else None

    return _load


@pytest.fixture(scope="session")
def load_yaml_all():
    """Every YAML document in a file, as YAML.load_all gives them, each call its own copy."""
    return lambda path: copy.deepcopy(_documents(path))


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
