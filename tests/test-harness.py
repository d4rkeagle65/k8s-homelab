"""
The harness and the suites' own conventions.

Every assertion in the repo runs through check, every verdict through suite_verdict, and every
behaviour suite through import_script - so a defect here turns every other suite green for the
wrong reason. Nothing here calls check with a deliberate failure: the runner counts FAIL lines
textually, and the suite would redden on its own probe. Each primitive is exercised through a
local copy or a direct call instead.
"""

import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _testcommon as t

cfg = t.config()

t.section("test_config.toml carries only keys the harness knows")
unknown = [k for k in cfg if k not in t.KNOWN_CONFIG_KEYS]
t.check("no unknown key in test_config.toml", ", ".join(unknown), "")
if unknown:
    t.advise("a misspelt key takes its default in silence: fix the name, or add a new framework "
             "key to KNOWN_CONFIG_KEYS in _testcommon.py - and a key written after a [table] "
             "belongs to that table")
t.check("  TargetPythons names versions as \"3.N\" strings",
        ", ".join(str(v) for v in cfg.get("TargetPythons") or []
                  if not (isinstance(v, str) and re.fullmatch(r"\d+\.\d+", v))), "")
t.check("  LineEndings is LF or Any", cfg.get("LineEndings", "LF") in ("LF", "Any"), True)
timeout = cfg.get("SuiteTimeoutSeconds", 600)
t.check("  SuiteTimeoutSeconds is a whole number of seconds",
        isinstance(timeout, int) and not isinstance(timeout, bool) and 1 <= timeout <= 99999, True)
t.check("  every Prerequisites entry is well-formed", "; ".join(t.prerequisites().problems), "")
# A switch written as the string "false" is true to Python; a list written as one string is
# read a character at a time; a table written as anything else is read as empty.
t.check("  the true/false settings are true or false",
        ", ".join(k for k in ("RequireHelp", "ReadmeCatalogue", "StrictRatchets")
                  if k in cfg and not isinstance(cfg[k], bool)), "")
t.check("  the list settings are lists",
        ", ".join(k for k in ("Scripts", "ExcludeScripts", "TargetPythons", "EnvironmentSuites",
                              "ExtraSuites", "TextExtensions", "DocExclude")
                  if k in cfg and not isinstance(cfg[k], list)), "")
t.check("  Ratchets, Budgets and Floors are tables of whole numbers",
        ", ".join("%s.%s" % (tb, k) for tb in ("Ratchets", "Budgets", "Floors")
                  for k, v in (cfg.get(tb) or {}).items()
                  if not isinstance(v, int) or isinstance(v, bool)), "")

suite_files = sorted(os.path.join(t.HERE, f) for f in os.listdir(t.HERE)
                     if re.fullmatch(r"test-.+\.py", f))
suite_files += [p for p in (os.path.join(t.root(), x) for x in cfg.get("ExtraSuites") or [] if x)
                if os.path.isfile(p)]
# Each suite's parse, read once. One that does not parse is red in the runner already, and is
# named here so the checks below can pass over it rather than end on it.
suite_trees, unparsed = {}, []
for f in suite_files:
    try:
        suite_trees[f] = ast.parse(t.source(f), filename=f)
    except SyntaxError as e:
        unparsed.append("%s:%s" % (os.path.basename(f), e.lineno))


def asserted_keys(text):
    """Each literal key a call to ratchet or scanned asserts - by position or by name, however
    the call is laid out - and each budget read through budget(). Never one in a comment or a
    string. A key built at run time cannot be seen from here."""
    found = {"assert": [], "budget": []}
    for n in ast.walk(ast.parse(text)):
        if not isinstance(n, ast.Call):
            continue
        name = (t.dotted(n.func) or "").split(".")[-1]
        if name not in ("ratchet", "scanned", "budget"):
            continue
        arg = n.args[0] if n.args else next((k.value for k in n.keywords
                                             if k.arg in ("key", "name")), None)
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            found["budget" if name == "budget" else "assert"].append(arg.value)
    return found


snippet = ('t.ratchet("A", 1)\nt.ratchet(count=0, key="B")\nscanned(\n    "C",\n    3)\n'
           't.ratchet(k, 1)\nt.ratchet("My-Debt", 0)\nt.budget("D")\nx = budget(name="E")\n')
got = asserted_keys(snippet)
t.check("  SELF-TEST: a key asserted by position, by name, or across lines is seen",
        ",".join(got["assert"]), "A,B,C,My-Debt")
t.check("  and a budget read through budget()", ",".join(got["budget"]), "D,E")
t.check("  and not one in a comment or a string",
        ",".join(asserted_keys('# t.ratchet("Old", 0)\ns = "t.ratchet(\'Str\', 0)"\n')["assert"]), "")
asserted, read_budgets = [], []
for f in suite_trees:
    keys = asserted_keys(t.source(f))
    asserted += keys["assert"]
    read_budgets += keys["budget"]
unknown_table = ["%s.%s" % (table, k) for table in ("Ratchets", "Floors", "Budgets")
                 for k in (cfg.get(table) or {})
                 if k not in t.KNOWN_TABLE_KEYS[table]
                 and k not in (read_budgets if table == "Budgets" else asserted)]
t.check("  every Ratchets, Floors and Budgets key is known or used by a suite",
        ", ".join(unknown_table), "")
if unknown_table:
    t.advise("fix the name; a new framework key goes in KNOWN_TABLE_KEYS in _testcommon.py, and a "
             "project's own is asserted by its suite (t.ratchet or t.scanned), or read by it "
             "through t.budget if a budget")


def calls_our_hook(text, hook="pre-commit"):
    """Whether another hook's text runs ours: a line that is not a comment, naming
    .githooks/<hook> and nothing longer - not pre-commit.template, not pre-commit-old."""
    pattern = r"\.githooks[\\/]" + re.escape(hook) + r"(?![\w.-])"
    return any(re.search(pattern, l) for l in re.split(r"\r?\n", text)
               if not re.match(r"\s*#", l))


t.check("  SELF-TEST: a hook that runs ours is seen",
        calls_our_hook('#!/bin/sh\nnpx lint-staged\nsh .githooks/pre-commit "$@"\n'), True)
t.check("  and one that only mentions it, or a longer name, is not",
        "%s %s" % (calls_our_hook("# TODO: run .githooks/pre-commit\nexit 0\n"),
                   calls_our_hook("exec .githooks/pre-commit.template\n")), "False False")

# git never copies core.hooksPath into a clone, and nothing else would say the hook is off: the
# secret scan and this suite then never run at commit. In CI no commit is made, so it is a SKIP
# - and CI=false or CI=0, which some tools set on a developer's machine, is not CI.
ci = os.environ.get("CI", "").strip()
if ci and ci not in ("false", "0"):
    t.skip("the pre-commit hook is enabled", "running in CI, where no commit is made")
else:
    # As a path, so git expands ~ the way it does when it runs the hook; compared as folders,
    # both from the top folder as git spells it, so ./.githooks or its full path is the same.
    hooks_path = t.run(["git", "-C", t.root(), "config", "--type=path", "--get", "core.hooksPath"],
                       merge=False).out.strip()
    top = t.run(["git", "-C", t.root(), "rev-parse", "--show-toplevel"], merge=False).out.strip()
    folder = lambda p: os.path.normcase(os.path.normpath(os.path.join(top, p)))
    ours = folder(".githooks")
    hooks_set = folder(hooks_path) if hooks_path else ""
    # Another hooks folder, as the installer leaves one, counts when its hooks call ours. Where
    # that folder holds generated stubs, HookSource names the folder of the hooks written by hand.
    hook_source = folder(cfg["HookSource"]) if cfg.get("HookSource") else hooks_set

    def other_calls_ours(hook):
        f = os.path.join(hook_source, hook) if hooks_set and hooks_set != ours else ""
        return bool(f) and os.path.isfile(f) and calls_our_hook(t.source(f), hook)

    calls = other_calls_ours("pre-commit")
    t.check("the pre-commit hook is enabled (core.hooksPath = .githooks)",
            hooks_set == ours or calls, True)
    if hooks_set != ours and not calls:
        t.advise("run: git config core.hooksPath .githooks, or call .githooks/pre-commit from the "
                 "hook it names (HookSource in test_config.toml when that folder is generated)")
    if hooks_set and hooks_set != ours and calls:
        # A merge that commits on its own runs pre-merge-commit, not pre-commit: the same gate.
        t.check("  and its pre-merge-commit runs ours too", other_calls_ours("pre-merge-commit"), True)
        for h in ("pre-applypatch", "post-rewrite"):
            if not other_calls_ours(h):
                t.detail("note: %s in %s does not run .githooks/%s" % (h, hook_source, h))
    # A sparse checkout can leave the folder out, and git then runs no hook without a word.
    t.check("  and the hook it names is there to run", os.path.isfile(os.path.join(ours, "pre-commit")), True)

t.section("the Prerequisites reader and the version parser")
fx = t.prerequisites({
    "Packages": [{"Name": "A", "Minimum": "1.2"}, {"Name": "B", "Pythons": ["3.12"]},
                 {"Name": "C", "Minimum": "x"}, {"Name": "D", "MinVersion": "1.0"},
                 {"Name": "E", "Pythons": ["Linux"]}, {"Name": "x", "Pythons": [3.10]}],
    "Tools": [{"Name": "t", "Arguments": ["--version"], "Minimum": "3", "TimeoutSeconds": 5},
              {"Name": "u"}, {"Name": "v", "Minimum": "1.0"},
              {"Name": "w", "Arguments": ["--version"], "TimeoutSeconds": 0},
              {"Name": "y", "Arguments": ["--version"], "Minimum": 2.90},
              {"Name": "z", "Pythons": ["3.12"]}],
    "Extra": [],
})
t.check("the reader keeps every well-formed entry", " ".join(e.name for e in fx.packages + fx.tools), "A B t u")
t.check("  a one-part Minimum reads as a version", t.version_text(fx.tools[0].minimum), "3.0")
t.check("  a tool given no Arguments has none", len(fx.tools[1].arguments), 0)
t.check("  a TimeoutSeconds is kept, and 30 is the default", "%s %s" % (fx.tools[0].timeout, fx.tools[1].timeout), "5 30")
t.check("  a package keeps its Pythons", "%d %s" % (len(fx.packages[0].pythons), ",".join(fx.packages[1].pythons)), "0 3.12")
t.check("  and names each malformed one",
        " ".join((re.search(r"\((\w)\)", p).group(1) if re.search(r"\((\w)\)", p) else "table")
                 for p in fx.problems), "table C D E x v w y z")
for text, want in (("Python 3.12.1", "3.12.1"), ("git version 2.45.1.windows.1", "2.45.1"),
                   ("v20", "20.0"), ("7.4.0-preview.3", "7.4.0"), ("7-Zip 23.01 (x64) : Copyright", "23.1"),
                   ("GNU Wget2 2.1.0 - multithreaded", "2.1.0"), ("no version here", "")):
    t.check("  '%s' reads as [%s]" % (text, want), t.version_text(t.loose_version(text)), want)
t.check("  2.1 and 2.1.0 are the same version", t.compare_versions((2, 1), (2, 1, 0)), 0)
t.check("  2.10 is above 2.9", t.compare_versions((2, 10), (2, 9)), 1)

t.section("check compares as strings")
tc = t.source(os.path.join(t.HERE, "_testcommon.py"))
t.check("check compares str(got) == str(want)", bool(re.search(r"str\(got\)\s*==\s*str\(want\)", tc)), True)
same = lambda got, want: str(got) == str(want)
t.check("  a list does not satisfy one of its elements", same(["a", "b"], "a"), False)
t.check("  unequal scalars do not compare equal", same(3, 4), False)
t.check("  a bool matches its string form", same(True, "True"), True)
t.check("  a number matches its string form", same(1, "1"), True)

t.section("suite_verdict believes the worst of three signals")
ok = "  PASS  one\n  PASS  two\n\nALL CHECKS PASSED\n"
t.check("an honest pass is green", t.suite_verdict(ok, 0).ok, True)
t.check("a FAIL line with a PASSED sign-off is red",
        t.suite_verdict("  PASS  a\n  FAIL  b got [1] want [2]\nALL CHECKS PASSED", 0).ok, False)
t.check("a sign-off with a non-zero exit is red", t.suite_verdict(ok, 1).ok, False)
t.check("a zero exit with no sign-off is red", t.suite_verdict("  PASS  one\n", 0).ok, False)
died = t.suite_verdict('  File "x.py", line 3\n    }\n    ^\nSyntaxError: unmatched \'}\'', 1)
t.check("a suite that died before any check is red", died.ok, False)
t.check("  and its reason is shown", any("SyntaxError" in l for l in died.detail), True)
t.check("FAIL is counted only at line start",
        t.suite_verdict("  PASS  the FAIL path is reported\nALL CHECKS PASSED", 0).ok, True)
empty = t.suite_verdict("\nALL CHECKS PASSED\n", 0)
t.check("a suite that signs off with no check is red", "%s %s" % (empty.ok, empty.detail[0]), "False made no check")
threw = t.suite_verdict("  PASS  one\nTraceback (most recent call last):\nRuntimeError: the probe failed: boom\n", 1)
t.check("a suite that raises after a check shows why", any("boom" in l for l in threw.detail), True)
skipped = t.suite_verdict("  PASS  a\n  SKIP  b - not installed\n\nALL CHECKS PASSED\n", 0)
t.check("a SKIP is counted, and leaves the suite green", "%s %s" % (skipped.skip, skipped.ok), "1 True")

t.section("import_script loads definitions, and refuses a script that acts")
work = t.scratch("harness", fresh=True)


def write(name, *lines):
    p = os.path.join(work, name)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return p


marker = os.path.join(work, "ran.txt").replace("\\", "/")
write("helper_beside.py", "WORD = 'beside'")
fixture = write("fixture.py",
                '"""A fixture."""',
                "import os",
                "import helper_beside",
                "try:",
                "    import tomllib",
                "except ImportError:",
                "    tomllib = None",
                "if os.name == 'nt':",
                "    SEP = 'win'",
                "else:",
                "    SEP = 'posix'",
                "TABLE = {'a': 1}",
                "COUNT: int = 0",
                "def outer():",
                "    def inner():",
                "        return 'inner'",
                "    return 'outer:' + inner() + ':' + str(TABLE['a']) + ':' + helper_beside.WORD",
                "def bump():",
                "    global COUNT",
                "    COUNT += 1",
                "    return COUNT",
                "class Row:",
                "    def __init__(self, k):",
                "        self.k = k",
                "if __name__ == '__main__':",
                "    open(%r, 'w', encoding='utf-8').write('main ran')" % marker)
m = t.import_script(fixture)
t.check("a script's functions are defined", m.outer(), "outer:inner:1:beside")
t.check("  its classes too", m.Row("a").k, "a")
t.check("  an if or try of imports and assignments runs", "%s %s" % (m.SEP in ("win", "posix"), m.tomllib is not None), "True True")
t.check("  its __main__ guard does not run", os.path.exists(marker), False)
m.bump()
m2 = t.import_script(fixture)
t.check("  each import is a module of its own", "%s %s" % (m.COUNT, m2.COUNT), "1 0")
for label, lines in (("a bare call", ["def f():", "    pass", "open(%r, 'w').write('x')" % marker]),
                     ("a raise", ["raise SystemExit('top-level code ran')"]),
                     ("a loop", ["for i in range(1):", "    open(%r, 'w').write('x')" % marker]),
                     ("a with", ["with open(%r, 'w') as f:" % marker, "    f.write('x')"]),
                     ("an if whose test calls", ["if open(%r, 'w').write('x'):" % marker, "    pass"]),
                     ("a call in the __main__ guard's else",
                      ["if __name__ == '__main__':", "    pass", "else:", "    open(%r, 'w').write('x')" % marker])):
    p = write("acts.py", *lines)
    refused = ""
    try:
        t.import_script(p)
    except RuntimeError as e:
        refused = str(e)
    t.check("a script with %s at its top level is refused" % label,
            "%s %s" % ("acts when imported" in refused, os.path.exists(marker)), "True False")
    if label == "a bare call":
        t.check("  and the refusal names the line", "at line 3: open(" in refused, True)
t.check("SELF-TEST: an assignment that calls is allowed, as getLogger must be",
        t.top_level_actions(ast.parse("import logging\nlog = logging.getLogger(__name__)\n")), [])
# Every kind of definition this Python parses - a type alias from 3.12 - sits at the top level freely.
definitions = ["import os", "from os import path", "def f():\n    pass", "class C:\n    pass", "x = 1",
               "x: int = 1", "x += 1"] + (["type Pair = tuple[int, int]"] if sys.version_info >= (3, 12) else [])
t.check("  and so is every kind of definition this Python parses",
        [d.split()[0] for d in definitions if t.top_level_actions(ast.parse(d))], [])

t.section("the code under test, as the scans see it")
code = ("import json, os.path\nimport numpy as np\nfrom subprocess import run as sh\nfrom . import sibling\n"
        "def open(x):\n    return x\n"
        "json.dumps({}, indent=2)\nos.path.join('a', 'b')\nsh(['x'], check=True)\nnp.array([1], dtype=int)\n"
        "open(1)\nlen([], key=1)\nobj.method(a=1)\n")
tree = ast.parse(code)
imp = t.imports_of(tree)
t.check("imports_of names every module, and leaves a relative import out", ",".join(sorted(imp.modules)), "json,numpy,os.path,subprocess")
t.check("  and each name taken from one", sorted(imp.names), [("subprocess", "run")])
t.check("  and what each local name stands for", "%s %s" % (imp.aliases["np"], imp.aliases["sh"]), "numpy subprocess.run")
guards = ("import sys, os\nif sys.platform == 'win32':\n    import msvcrt\n"
          "if sys.version_info >= (3, 11):\n    from datetime import UTC\n"
          "try:\n    import tomli\nexcept ImportError:\n    tomli = None\n"
          "if os.environ.get('X'):\n    import shelve\nmsvcrt.getch()\n")
g = t.imports_of(ast.parse(guards))
t.check("  an import under a platform or version test, or a try for ImportError, is guarded",
        sorted(str(x) for x in g.guarded), ["('datetime', 'UTC')", "datetime", "msvcrt", "tomli"])
t.check("  one under any other test is not", "shelve" in g.guarded, False)
t.check("  and a call through a guarded import is guarded", t.calls_of(ast.parse(guards))["msvcrt.getch"]["guarded"], True)
found = t.calls_of(tree)
t.check("calls_of resolves a call through an import alias", ",".join(sorted(found)),
        "builtins.len,json.dumps,numpy.array,os.path.join,subprocess.run")
t.check("  with its keywords", sorted(found["subprocess.run"]["keywords"]), ["check"])
t.check("  and the line each keyword is passed on", found["subprocess.run"]["keyword_lines"], {"check": [9]})
t.check("  and is not guarded when its import is not", found["subprocess.run"]["guarded"], False)
t.check("  and a name the file binds is not taken for the builtin", "builtins.open" in found, False)
future = t.calls_of(ast.parse("from __future__ import annotations\nimport json\n"
                              "annotations = {}\nannotations.get('a')\njson.loads('1')\n"))
t.check("  a variable named like a __future__ import is not a call into __future__",
        ",".join(sorted(future)), "json.loads")

t.section("the probe answers in each Python, and can say no")
# The positive control for every per-version check in the suites: without it, a probe that
# never ran would leave them reporting nothing on a machine with every target installed.
exe = t.find_python(t.current_python())
t.check("the running Python is found as itself", exe, sys.executable)
t.check("a Python that is not installed is None", t.find_python("3.99"), None)
broken = write("broken.py", "def f(:", "    pass")
probe_file = write("probe_me.py", "import json", "from os import no_such_name, path", "import no_such_module_xyz")
calls = {"shutil.copy": {"follow_symlinks"}, "shutil.copyfile": {"follow_symlink"}, "builtins.max": {"key"},
         "logging.info": {"extra"}, "json.no_such_function": set()}
r = t.probe(exe, files=[probe_file, broken], modules=["json", "no_such_module_xyz"],
            names=[("os", "no_such_name"), ("os", "path")], calls=calls)
by_file = {os.path.basename(p["file"]): p["errors"] for p in r["parse"]}
t.check("  it compiled each file it was given", "%d %d" % (len(by_file["probe_me.py"]), len(by_file["broken.py"])), "0 1")
t.check("  and names the line a file does not compile at", by_file["broken.py"][0].startswith("line 1:"), True)
t.check("  a module of the standard library is found", r["modules"].get("json"), "OK")
t.check("  one outside it is not judged in stdlib mode", "no_such_module_xyz" in r["modules"], False)
t.check("  a name the module lacks is NoName", r["names"].get("os:no_such_name"), "NoName")
t.check("  a submodule taken by name is found", r["names"].get("os:path"), "OK")
t.check("  a keyword the callable takes binds", r["calls"]["shutil.copy"]["status"], "OK")
t.check("  one it does not take is BadKeyword, named", "%s %s" % (r["calls"]["shutil.copyfile"]["status"], r["calls"]["shutil.copyfile"]["bad"]), "BadKeyword ['follow_symlink']")
t.check("  a callable that takes **kwargs takes any keyword", r["calls"]["logging.info"]["status"], "OK")
t.check("  one whose signature cannot be read (max) is Unreadable, never OK", r["calls"]["builtins.max"]["status"], "Unreadable")
t.check("  a callable the module lacks is NoName", r["calls"]["json.no_such_function"]["status"], "NoName")
r = t.probe(exe, modules=["no_such_module_xyz"], calls={"no_such_module_xyz.f": set()}, mode="all")
t.check("  in mode all, a module that is not installed is NotFound", r["modules"].get("no_such_module_xyz"), "NotFound")
t.check("  and so is a callable in it", r["calls"]["no_such_module_xyz.f"]["status"], "NotFound")

t.section("addressing a file by name")
try:
    t.project_file("no-such-file-anywhere.py")
    threw = False
except FileNotFoundError:
    threw = True
t.check("a missing name raises rather than returning nothing", threw, True)
t.check("the harness itself is found by name", t.rel(t.project_file("_testcommon.py")), "tests/_testcommon.py")

t.section("every suite follows the conventions")
t.scanned("Suites", len(suite_files), "suites")


def imports_harness(tree):
    return any(isinstance(n, ast.Import) and any(a.name == "_testcommon" for a in n.names)
               or isinstance(n, ast.ImportFrom) and n.module == "_testcommon" for n in tree.body)


def closes(tree):
    last = tree.body[-1] if tree.body else None
    return (isinstance(last, ast.Expr) and isinstance(last.value, ast.Call)
            and (t.dotted(last.value.func) or "").split(".")[-1] == "complete")


TEMP_VARS = ("TEMP", "TMP", "TMPDIR")


def own_temp(tree):
    """Whether a suite reaches for the machine's temp folder itself - the tempfile module, or a
    TEMP, TMP or TMPDIR variable read by index or by a call - rather than through scratch()."""
    named = lambda n: isinstance(n, ast.Constant) and n.value in TEMP_VARS
    for n in ast.walk(tree):
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] == "tempfile" for a in n.names):
            return True
        if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "tempfile":
            return True
        if isinstance(n, ast.Subscript) and named(n.slice):
            return True
        if isinstance(n, ast.Call) and n.args and named(n.args[0]):
            return True
    return False


t.check("every suite parses", ", ".join(unparsed), "")
for label, off in (("imports _testcommon", lambda tr: not imports_harness(tr)),
                   ("closes with complete()", lambda tr: not closes(tr)),
                   ("keeps scratch under scratch()", own_temp)):
    bad = [os.path.basename(f) for f, tree in suite_trees.items() if off(tree)]
    t.check("every suite %s" % label, ", ".join(bad), "")
t.check("SELF-TEST: complete() inside a string does not count", closes(ast.parse("x = 't.complete()'\n")), False)
t.check("  called last, it does", closes(ast.parse("import _testcommon as t\nt.complete()\n")), True)
t.check("SELF-TEST: each way to the temp folder is caught",
        [own_temp(ast.parse(s)) for s in ("import tempfile", "from tempfile import mkdtemp",
                                          "os.environ['TEMP']", "os.getenv('TMPDIR')")],
        [True, True, True, True])
t.check("  and scratch() is not", own_temp(ast.parse("d = t.scratch('x')")), False)

t.complete()
