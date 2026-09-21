"""Deterministic YAML I/O built on ruamel.yaml.

ruamel.yaml preserves the insertion order of plain dicts (classic PyYAML
sorts map keys by default), which is what makes re-running this script
against an unchanged cluster produce byte-identical files: Kubernetes API
JSON responses have stable key order (Go struct field order for typed
fields, and Go's encoding/json sorts map keys such as labels/annotations
alphabetically), and json.loads preserves that order as-is. Generated Flux
CRs are built as plain dicts in a fixed field order every run, so they're
stable too.

All files are written UTF-8 with `\\n` line endings and no BOM (Python's
"utf-8" codec never emits one).
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.nodes import ScalarNode
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

_yaml = YAML()
_yaml.default_flow_style = False
_yaml.width = 100_000  # don't wrap long scalars (domains, base64-ish strings)
_yaml.explicit_start = False

# Kubernetes object data comes in over JSON (via `kubectl get -o json`),
# where every annotation/label/string field is unambiguously a string.
# ruamel's own plain-scalar resolver is YAML-1.2-ish and only treats
# true/false as booleans, but the consumers of this repo's YAML (kubectl
# apply, kustomize, Flux's controllers) are Go tools built on YAML 1.1
# semantics, which also treats y/yes/on/n/no/off (any case) and null/~ as
# non-string. A real annotation this bit: Kubernetes sets
# `pv.kubernetes.io/bound-by-controller: "yes"` (a string) on PVs/PVCs;
# without this guard ruamel emits it as a bare `yes`, and kubeconform (and
# `kubectl apply`) then read it back as the boolean `true`, silently
# changing the value's type. See https://yaml.org/type/bool.html (YAML
# 1.1 bool) vs ruamel's core-schema-only resolver.
_YAML11_BOOL_OR_NULL = re.compile(
    r"^(y|Y|yes|Yes|YES|n|N|no|No|NO|true|True|TRUE|false|False|FALSE|"
    r"on|On|ON|off|Off|OFF|null|Null|NULL|~)$"
)


def _needs_quoting(value: str) -> bool:
    if value == "":
        return True
    if _YAML11_BOOL_OR_NULL.match(value):
        return True
    # Catches int/float/timestamp-looking strings too (e.g. a chart
    # version of "20260101" or a numeric-looking secret key name) so they
    # round-trip as the string they actually are.
    tag = _yaml.resolver.resolve(ScalarNode, value, (True, False))
    return tag != "tag:yaml.org,2002:str"


def _harden_scalars(obj: Any) -> Any:
    """Force-quote any string that a YAML 1.1 parser would misread as a
    bool/null/number, in place. Mutates dict/list (and ruamel's
    CommentedMap/CommentedSeq, which behave like them) so any attached
    comments survive untouched.
    """
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            val = obj[key]
            if isinstance(val, str):
                if _needs_quoting(val):
                    obj[key] = DoubleQuotedScalarString(val)
            else:
                _harden_scalars(val)
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            if isinstance(val, str):
                if _needs_quoting(val):
                    obj[i] = DoubleQuotedScalarString(val)
            else:
                _harden_scalars(val)
    return obj


def to_yaml_string(data: Any) -> str:
    stream = io.StringIO()
    _yaml.dump(_harden_scalars(data), stream)
    return stream.getvalue()


def parse_yaml_string(text: str) -> Any:
    return _yaml.load(text)


def write_yaml_file(path: Path, data: Any, *, header: str | None = None, dry_run: bool = False) -> str:
    """Render `data` as YAML, optionally prefixed with a header comment block.

    Returns the content that was (or would be) written, for the caller to
    hand to the file tracker regardless of dry-run.
    """
    body = to_yaml_string(data)
    content = (header + body) if header else body
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    return content


def write_text_file(path: Path, content: str, *, dry_run: bool = False) -> str:
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    return content


def read_yaml_file(path: Path) -> Any:
    return parse_yaml_string(path.read_text(encoding="utf-8"))
