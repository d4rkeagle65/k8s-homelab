"""
Properties every script must keep, whatever it does. Each fails silently in production when
broken, which is why it is a check rather than a comment:

  compiles in every target Python   3.12-only syntax is a SyntaxError on 3.11
  imports what each Python has      tomllib is 3.11+; distutils is gone from 3.12
  takes the keywords each Python's  a keyword one version added is a TypeError on the one before
  standard library takes
  runs nothing when imported        the convention suites import the script to test it
  help                              a module docstring, and help= on every argparse argument:
                                    the only reference an operator has
  except: pass is a ratchet         the reason something failed, discarded
  except: return False              "could not find out" becomes "no", and the script acts on it
  bare except:                      also catches KeyboardInterrupt and SystemExit
  suppress(Exception)               a failed read becomes no answer at all
  open() with no encoding=          reads the locale's code page on Windows and UTF-8 on Linux;
                                    so does subprocess text mode

Each AST scanner is a function, self-tested below against snippets that must be caught and
snippets that must not.
"""

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _testcommon as t

cfg = t.config()
scripts = t.scripts_under_test()
t.scanned("PyFiles", len(scripts), "scripts under test")

# ------------------------------------------------------------------ scanners


def handlers(tree):
    return [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]


def does_nothing(stmt):
    return isinstance(stmt, (ast.Pass, ast.Continue)) or (
        isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
        and (stmt.value.value is None or stmt.value.value is Ellipsis))


def find_empty_catch(tree):
    """An except whose body does nothing that counts: pass, continue, ... or None alone."""
    return ["line %d" % h.lineno for h in handlers(tree) if len(h.body) == 1 and does_nothing(h.body[0])]


def find_catch_returns_false(tree):
    # The LAST statement decides what the handler answers, whatever is logged before it.
    return ["line %d" % h.lineno for h in handlers(tree)
            if isinstance(h.body[-1], ast.Return) and isinstance(h.body[-1].value, ast.Constant)
            and h.body[-1].value.value is False]


def find_bare_except(tree):
    return ["line %d" % h.lineno for h in handlers(tree) if h.type is None]


def find_suppressed_error(tree):
    """contextlib.suppress of Exception or BaseException, however suppress was imported."""
    imp = t.imports_of(tree)
    bound = t.bound_names(tree)
    hits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and t.resolve_call(n, imp.aliases, bound) == "contextlib.suppress":
            if any((t.dotted(a) or "").split(".")[-1] in ("Exception", "BaseException") for a in n.args):
                hits.append("line %d" % n.lineno)
    return hits


SUBPROCESS_TEXT = ("subprocess.run", "subprocess.Popen", "subprocess.check_output",
                   "subprocess.call", "subprocess.check_call")


def find_open_without_encoding(tree):
    """Text read or written with the locale's encoding: open() or io.open() in a text mode,
    a path's read_text() or write_text(), or subprocess in text mode - each with no encoding.
    A mode the source does not spell out cannot be judged, and is left out."""
    imp = t.imports_of(tree)
    bound = t.bound_names(tree)
    keywords = lambda n: {k.arg for k in n.keywords if k.arg}
    hits = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        qual = t.resolve_call(n, imp.aliases, bound)
        kw = keywords(n)
        if qual in ("builtins.open", "io.open"):
            mode = n.args[1] if len(n.args) > 1 else next((k.value for k in n.keywords if k.arg == "mode"), None)
            text_mode = mode is None or (isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" not in mode.value)
            if text_mode and "encoding" not in kw and len(n.args) < 4:
                hits.append("line %d: open()" % n.lineno)
        elif qual in SUBPROCESS_TEXT:
            text = any(k.arg in ("text", "universal_newlines") and isinstance(k.value, ast.Constant) and k.value.value is True
                       for k in n.keywords)
            if text and "encoding" not in kw:
                hits.append("line %d: %s(text=True)" % (n.lineno, qual))
        elif qual is None and isinstance(n.func, ast.Attribute) and "encoding" not in kw:
            if (n.func.attr == "read_text" and not n.args) or (n.func.attr == "write_text" and len(n.args) < 2):
                hits.append("line %d: %s()" % (n.lineno, n.func.attr))
    return hits


def help_gaps(tree):
    """What a script lacks of its help: its module docstring, and help= on each argparse
    argument, by the argument's first name."""
    gaps = [] if ast.get_docstring(tree) else ["a module docstring"]
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "add_argument":
            if not any(k.arg == "help" for k in n.keywords):
                first = n.args[0].value if n.args and isinstance(n.args[0], ast.Constant) else "line %d" % n.lineno
                gaps.append("help= for %s" % first)
    return gaps


# ------------------------------------------------------------------ self-test
t.section("the scanners, proven against injected violations")
P = ast.parse
count = lambda finder, code: len(finder(P(code)))
t.check("SELF-TEST: an empty except is CAUGHT", count(find_empty_catch, "try:\n    x()\nexcept OSError:\n    pass\n"), 1)
t.check("  one that logs is not", count(find_empty_catch, "try:\n    x()\nexcept OSError as e:\n    log(e)\n"), 0)
t.check("  continue, ... and None alone are CAUGHT",
        count(find_empty_catch, "for i in y:\n    try:\n        x()\n    except OSError:\n        continue\n"
                                "try:\n    x()\nexcept OSError:\n    ...\ntry:\n    x()\nexcept OSError:\n    None\n"), 3)
t.check("SELF-TEST: except: return False is CAUGHT", count(find_catch_returns_false, "def f():\n    try:\n        x()\n    except OSError:\n        return False\n"), 1)
t.check("  one that sets a flag is not", count(find_catch_returns_false, "try:\n    x()\nexcept OSError:\n    ok = False\n"), 0)
t.check("  one that logs, then returns False, is CAUGHT",
        count(find_catch_returns_false, "def f():\n    try:\n        x()\n    except OSError as e:\n        log(e)\n        return False\n"), 1)
t.check("  one that returns None is not", count(find_catch_returns_false, "def f():\n    try:\n        x()\n    except KeyError:\n        return None\n"), 0)
t.check("SELF-TEST: a bare except is CAUGHT", count(find_bare_except, "try:\n    x()\nexcept:\n    raise\n"), 1)
t.check("  except Exception is not", count(find_bare_except, "try:\n    x()\nexcept Exception:\n    raise\n"), 0)
t.check("SELF-TEST: suppress(Exception) is CAUGHT", count(find_suppressed_error, "import contextlib\nwith contextlib.suppress(Exception):\n    x()\n"), 1)
t.check("  so is suppress imported by name, with BaseException among others",
        count(find_suppressed_error, "from contextlib import suppress as quiet\nwith quiet(KeyError, BaseException):\n    x()\n"), 1)
t.check("  suppress(FileNotFoundError) is not", count(find_suppressed_error, "import contextlib\nwith contextlib.suppress(FileNotFoundError):\n    x()\n"), 0)
t.check("SELF-TEST: open() with no encoding is CAUGHT", count(find_open_without_encoding, "f = open('a')\ng = open('a', 'w')\nh = open('a', mode='rt')\n"), 3)
t.check("  with encoding, in binary, or with a mode it cannot read, it is not",
        count(find_open_without_encoding, "f = open('a', encoding='utf-8')\ng = open('a', 'rb')\nh = open('a', m)\n"
                                          "i = open('a', 'w', -1, 'utf-8')\n"), 0)
t.check("  read_text() and write_text(data) are CAUGHT, and with encoding are not",
        count(find_open_without_encoding, "p.read_text()\np.write_text('x')\np.read_text('utf-8')\np.write_text('x', encoding='utf-8')\n"), 2)
t.check("  subprocess in text mode is CAUGHT, and with encoding is not",
        count(find_open_without_encoding, "import subprocess\nsubprocess.run(a, text=True)\nsubprocess.run(a, text=True, encoding='utf-8')\nsubprocess.run(a)\n"), 1)
t.check("  an open the module defines itself is not", count(find_open_without_encoding, "def open(p):\n    return p\nopen('a')\n"), 0)
t.check("SELF-TEST: a script with no docstring lacks help", help_gaps(P("import os\n")), ["a module docstring"])
t.check("  an argparse argument with no help= is named",
        help_gaps(P('"""Doc."""\nap.add_argument("--path", help="the folder")\nap.add_argument("--count")\n')), ["help= for --count"])

# ------------------------------------------------------------------ per script
parsed, unparsed = {}, []
for s in scripts:
    try:
        parsed[s] = t.parse(s)
    except SyntaxError as e:
        unparsed.append("%s:%s %s" % (t.rel(s), e.lineno, e.msg))
t.section("the scripts parse under the Python running the suites")
# The scanners read the parse of this Python: a script it cannot parse is not scanned at all.
t.check("Python %s parses every script" % t.current_python(), ", ".join(unparsed), "")


def collect(finder):
    return ["%s %s" % (t.rel(s), h) for s, tree in parsed.items() for h in finder(tree)]


t.section("every script runs nothing when imported")
acts = ["%s:%d %s" % (t.rel(s), ln, txt) for s, tree in parsed.items()
        for ln, txt in t.top_level_actions(tree, t.source(s))]
t.check("no script acts at its top level", len(acts), 0)
for a in acts[:8]:
    t.detail(a, "yellow")
if acts:
    t.advise("move it into main() and call that under if __name__ == \"__main__\":")

if cfg.get("RequireHelp", True):
    t.section("every script carries help")
    for s, tree in parsed.items():
        t.check("%s has its help" % t.rel(s), ", ".join(help_gaps(tree)), "")

t.section("a failed read must not become a confident answer")
h = collect(find_empty_catch)
t.ratchet("EmptyCatch", len(h), "empty except blocks", h, "say what could not be done, or fix another one in the same commit")
h = collect(find_catch_returns_false)
t.ratchet("CatchReturnsFalse", len(h), "except blocks that return False", h,
          "could-not-answer is a third outcome: re-raise, or catch only the not-found error")
h = collect(find_bare_except)
t.ratchet("BareExcept", len(h), "bare except: clauses", h, "name the exceptions; except Exception at the widest")
h = collect(find_suppressed_error)
t.ratchet("SuppressedError", len(h), "suppress(Exception) blocks", h, "suppress only the error that means absent, such as FileNotFoundError")

t.section("text is read and written in a named encoding")
h = collect(find_open_without_encoding)
t.ratchet("OpenWithoutEncoding", len(h), "text opened with no encoding", h, "pass encoding=\"utf-8\"")

t.section("each target Python compiles the scripts and has what they import")
modules, names, calls = set(), set(), {}
for s, tree in parsed.items():
    imp = t.imports_of(tree)
    modules |= {m for m in imp.modules if m not in imp.guarded}
    names |= {p for p in imp.names if p not in imp.guarded}
    for qual, c in t.calls_of(tree).items():
        if c["guarded"]:
            continue
        entry = calls.setdefault(qual, {"keywords": set(), "where": [], "keyword_where": {}})
        entry["keywords"] |= c["keywords"]
        entry["where"] += ["%s:%d" % (t.rel(s), ln) for ln in c["where"]]
        for k, lines in c["keyword_lines"].items():
            entry["keyword_where"].setdefault(k, []).extend("%s:%d" % (t.rel(s), ln) for ln in lines)
for version, exe in t.target_pythons():
    if not exe:
        t.skip("Python %s checks" % version, "Python %s is not installed on this machine" % version)
        continue
    r = t.probe(exe, files=scripts, modules=modules, names=names,
                calls={k: v["keywords"] for k, v in calls.items()})
    for p in r["parse"]:
        t.check("  %s compiles %s" % (version, t.rel(p["file"])), " | ".join(p["errors"]), "")
    # A module this Python has but cannot import here - tkinter with no Tk beneath it - is a fact
    # about the machine, not the code: a SKIP. One it does not have at all is the code's.
    broken = sorted("%s (%s)" % (m, st) for m, st in r["modules"].items() if st.startswith("ImportError"))
    missing = sorted(m for m, st in r["modules"].items() if st == "NotFound")
    missing += sorted("%s from %s" % (k.split(":")[1], k.split(":")[0]) for k, st in r["names"].items() if st == "NoName")
    # A callable its module lacks in this Python, called through the module (itertools.batched).
    missing += sorted("%s at %s" % (q, ", ".join(calls[q]["where"][:3])) for q, c in r["calls"].items() if c["status"] == "NoName")
    t.check("  %s has every standard-library module and name the scripts import" % version, ", ".join(missing), "")
    if broken:
        t.skip("  %s imports %s" % (version, ", ".join(m.split(" ")[0] for m in broken)), "it cannot here: %s" % "; ".join(broken))
    # Each bad keyword where it is passed, not every call to the callable.
    bad = ["%s(%s) at %s" % (q, k, ", ".join(calls[q]["keyword_where"].get(k, [])[:3]))
           for q, c in sorted(r["calls"].items()) if c["status"] == "BadKeyword" for k in c["bad"]]
    t.check("  %s takes every keyword the scripts pass to the standard library" % version, len(bad), 0)
    for b in bad[:8]:
        t.detail(b, "yellow")
    judged = [q for q, c in r["calls"].items() if c["status"] in ("OK", "BadKeyword")]
    unread = [q for q, c in r["calls"].items() if c["status"] == "Unreadable"]
    t.detail("%d standard-library call(s) checked; %d whose signature this Python does not publish"
             % (len(judged), len(unread)))

t.complete()
