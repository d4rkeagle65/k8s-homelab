"""
Properties of every tracked text file. Each is a byte nobody can see, found late and somewhere
else: a lone CR inside a path surfaces as a missing file in a different script; a tab in
markdown only reads wrong; a UTF-16 file defeats every scan that reads it as text.

  UTF-16              one finding, and the fix for every other check here, which would misread it
  line endings        LF throughout when test_config.toml says so, declared in .gitattributes
  control characters  LF, and CR only as half of CRLF; nothing else below 0x20
  conflict markers    a merge left half-done
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _testcommon as t

cfg = t.config()
exts = list(cfg.get("TextExtensions") or [])
text = [f for f in t.tracked_files()
        if os.path.splitext(f)[1] in exts or os.path.basename(f) in exts]
t.scanned("TextFiles", len(text), "tracked text files")


# Scanners, as functions so the self-test can feed them bytes instead of writing poisoned files.
def stray_control(data):
    """'<line>:0x<NN>' for the first stray control byte, or ''."""
    m = re.search(rb"[\x00-\x09\x0b\x0c\x0e-\x1f]|\r(?!\n)", data)
    if not m:
        return ""
    return "%d:0x%02X" % (1 + data.count(b"\n", 0, m.start()), m.group(0)[0])


def has_crlf(data):
    return b"\r\n" in data


def is_utf16(data):
    return data[:2] in (b"\xff\xfe", b"\xfe\xff")


def has_conflict(data):
    return bool(re.search(rb"(?m)^(<{7}|>{7}) ", data))


t.section("the scanners, proven against injected bytes")
t.check("SELF-TEST: clean text passes", stray_control(b"a\nb"), "")
t.check("  the CR of a CRLF passes", stray_control(b"a\r\nb"), "")
t.check("  a lone CR is CAUGHT", stray_control(b"a\rb"), "1:0x0D")
t.check("  a TAB is CAUGHT", stray_control(b"a\tb"), "1:0x09")
t.check("  a 0x08 is CAUGHT, on the right line", stray_control(b"a\nb\nc\x08"), "3:0x08")
t.check("SELF-TEST: CRLF is CAUGHT", has_crlf(b"a\r\nb"), True)
t.check("  LF is not", has_crlf(b"a\nb"), False)
t.check("SELF-TEST: a UTF-16 BOM is CAUGHT", is_utf16(b"\xff\xfea\x00"), True)
t.check("  UTF-8 with a BOM is not", is_utf16(b"\xef\xbb\xbfa"), False)
t.check("SELF-TEST: a conflict marker is CAUGHT", has_conflict(b"a\n<<<<<<< HEAD\nb\n"), True)
t.check("  a line of seven < inside text is not", has_conflict(b"a <<<<<<< b\n"), False)

ctrl, crlf, conflict, wide = [], [], [], []
for f in text:
    with open(f, "rb") as fh:
        data = fh.read()
    r = t.rel(f)
    # UTF-16 is one finding, and the fix for all the rest: every other check here would misread it.
    if is_utf16(data):
        wide.append(r)
        continue
    hit = stray_control(data)
    if hit:
        ctrl.append("%s:%s" % (r, hit))
    if cfg.get("LineEndings", "LF") == "LF" and has_crlf(data):
        crlf.append(r)
    if has_conflict(data):
        conflict.append(r)

t.section("text files are not UTF-16")
t.check("no tracked text file is UTF-16", ", ".join(wide), "")
if wide:
    t.advise("re-save as UTF-8")

t.section("no invisible bytes")
t.check("no tracked text file contains a stray control character", len(ctrl), 0)
for c in ctrl[:8]:
    t.detail(c, "yellow")
if ctrl:
    t.advise("usually sed or a heredoc: \\t and \\c are escapes there. Use the Edit/Write tools.")

if cfg.get("LineEndings", "LF") == "LF":
    t.section("line endings are LF")
    t.check("no tracked text file uses CRLF", len(crlf), 0)
    for c in crlf[:8]:
        t.detail("CRLF  %s" % c, "yellow")
    ga = os.path.join(t.root(), ".gitattributes")
    t.check(".gitattributes declares eol=lf", os.path.isfile(ga) and "eol=lf" in t.source(ga), True)

t.section("no merge left half-done")
t.check("no tracked file holds a conflict marker", ", ".join(conflict), "")

t.complete()
