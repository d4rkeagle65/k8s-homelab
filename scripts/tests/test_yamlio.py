"""Tests for the YAML writer's string styles: a multi-line string comes out
as a literal block, a string a YAML 1.1 parser would misread comes out
double-quoted, and every value reads back exactly as it went in.
"""

from __future__ import annotations

import pytest
from ruamel.yaml import YAML

from k8s_backup import yamlio
from k8s_backup.yamlio import to_yaml_string

# A plain YAML 1.2 reader, independent of the writer's own instance.
_reader = YAML(typ="safe")

ROUND_TRIP_CASES = [
    "line one\nline two\n",
    "no trailing newline\nat the end",
    "two trailing newlines\n\n",
    "  indented first line\nsecond\n",
    "trailing spaces   \nnext line\n",
    "a line of spaces\n   \nafter it\n",
    "a\ttab\nand more\n",
    "# starts like a comment\nkey: value\n",
    "\nstarts with a newline\n",
    "windows\r\nline endings\r\n",
    "next line\x85character\n",
    "unicode line separator\n",
    "a bell\x07in it\n",
    "!Env NOT_A_TAG\n- not a list\n",
    "yes",
    "",
    "plain",
]


@pytest.mark.parametrize("value", ROUND_TRIP_CASES)
def test_string_reads_back_unchanged(value):
    assert _reader.load(to_yaml_string({"k": value}))["k"] == value


def test_multi_line_string_is_a_literal_block():
    text = to_yaml_string({"data": {"blueprint.yaml": "version: 1\nentries: []\n"}})
    assert text == "data:\n  blueprint.yaml: |\n    version: 1\n    entries: []\n"


def test_multi_line_strings_in_lists_are_blocks_too():
    text = to_yaml_string({"args": ["one\ntwo\n"]})
    assert text == "args:\n- |\n  one\n  two\n"


def test_windows_line_endings_stay_quoted():
    # A block would read back with \n in place of \r\n.
    assert to_yaml_string({"k": "a\r\nb\r\n"}) == 'k: "a\\r\\nb\\r\\n"\n'


def test_yaml11_bool_is_still_quoted():
    assert to_yaml_string({"bound": "yes"}) == 'bound: "yes"\n'


def test_plain_drops_comments_so_nested_data_carries_none():
    parsed = yamlio.parse_yaml_string(
        "config:\n  data:\n    # why the flag is on\n    FLAG: \"true\"\nlist:\n  - a  # trailing\n"
    )
    nested = {"spec": {"values": yamlio.plain(parsed)}}
    text = yamlio.to_yaml_string(nested)
    assert "#" not in text
    assert yamlio.parse_yaml_string(text) == {
        "spec": {"values": {"config": {"data": {"FLAG": "true"}}, "list": ["a"]}}
    }
