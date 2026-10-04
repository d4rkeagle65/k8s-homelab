"""
Shared harness for this repo's test suites.

Import it at the top of every suite:

    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _testcommon as t

Its state - the failure count, the config, the run's scratch folder - belongs to the process,
and the runner starts every suite in a process of its own, so suites stay independent. Not
named test-*.py: the runner globs that pattern, and this is a library, not a suite.

Written for Python 3.11 and later, on Windows and Linux alike. Keep it that way: the suites run
under whichever Python runs the runner, and the harness must not be the reason one cannot run
them. The probe it runs in each target Python (PROBE) is written for 3.8, since a project may
target an older Python than the one that runs its suites.
"""

import ast
import builtins
import collections
import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from types import SimpleNamespace

if sys.version_info < (3, 11):
    raise SystemExit("The suites need Python 3.11 or later; this is %d.%d." % sys.version_info[:2])

import tomllib

# Output is UTF-8 wherever it goes. Redirected to a file on Windows, Python writes the locale's
# code page, and a file name outside it would end the suite with UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
# This process's own scratch folder name: a new one each run, so no two runs ever share one.
RUN_ID = uuid.uuid4().hex

_fail = 0
_config = None
_project_files = {}
_pythons = {}
_stdlib = {}
_imported = 0
_sections = 0

# Every key test_config.toml may carry. test-harness.py fails on any other: a mistyped key
# silently takes its default and the run still reports green.
KNOWN_CONFIG_KEYS = (
    "Scripts", "ExcludeScripts", "TargetPythons", "SuiteTimeoutSeconds", "EnvironmentSuites",
    "ExtraSuites", "RequireHelp", "LineEndings", "TextExtensions", "DocExclude",
    "ReadmeCatalogue", "StrictRatchets", "Ratchets", "Budgets", "Floors", "Prerequisites",
    "HookSource",
)

# The framework's own keys in test_config.toml's tables. A key in Ratchets or Floors is valid
# when it is one of these or one a suite asserts (a project's own suites add their own);
# test-harness fails on any other, since a mistyped floor key silently takes the default of 1.
KNOWN_TABLE_KEYS = {
    "Ratchets": ("EmptyCatch", "CatchReturnsFalse", "BareExcept", "SuppressedError",
                 "OpenWithoutEncoding", "NarrationInDocs", "NarrationInComments", "EssayComments"),
    "Floors": ("PyFiles", "TrackedScripts", "TextFiles", "MarkdownFiles", "CommentFiles",
               "ClaudeFiles", "Suites"),
    "Budgets": ("ClaudeRulesRoot", "ClaudeRulesTotal", "ClaudeRuleMaxLines", "EssayCommentLines"),
}

# ------------------------------------------------------------------------- output


def _use_color():
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    # A Windows console shows ANSI colours once virtual terminal processing is on.
    import ctypes
    kernel = ctypes.windll.kernel32
    handle = kernel.GetStdHandle(-11)
    mode = ctypes.c_uint32()
    if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    return bool(kernel.SetConsoleMode(handle, mode.value | 0x0004))


_COLOR = _use_color()
_CODES = {"green": "32", "red": "31", "yellow": "33", "cyan": "36", "gray": "90",
          "white": "97"}


def say(text="", color=None):
    """One line of output, coloured only on a terminal: what the runner reads has no codes."""
    if color and _COLOR:
        text = "\033[%sm%s\033[0m" % (_CODES[color], text)
    print(text, flush=True)


def section(title):
    global _sections
    say(("\n" if _sections else "") + "--- %s ---" % title, "cyan")
    _sections += 1


def detail(text, color="gray"):
    """Evidence under a check, indented so the runner shows it beneath a FAIL line."""
    say("          %s" % text, color)


def advise(text):
    detail("-> %s" % text, "yellow")


# ------------------------------------------------------------------------- locations


def root():
    """The repo root: the parent of this tests folder. Resolved from this file, so the tree
    can be moved or cloned without editing anything."""
    return os.path.dirname(HERE)


def read_toml(path):
    """A TOML file as a dict, or an error naming the file and the line that is wrong."""
    with open(path, "rb") as f:
        try:
            return tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ValueError("%s is not valid TOML: %s" % (path, e)) from e


def config():
    """test_config.toml, read once per process."""
    global _config
    if _config is None:
        p = os.path.join(HERE, "test_config.toml")
        if not os.path.isfile(p):
            raise FileNotFoundError("Not found: %s - every suite reads its settings from it." % p)
        _config = read_toml(p)
    return _config


def budget(name):
    """A fixed limit from test_config.toml's Budgets. Read through here, so test-harness can
    see which budget keys the suites use."""
    table = config().get("Budgets") or {}
    if name not in table:
        raise KeyError("Budgets.%s is not set in tests/test_config.toml" % name)
    return int(table[name])


def rel(path):
    """A path relative to the repo root, with forward slashes; a path outside it, unchanged."""
    r = root()
    p = os.path.abspath(path)
    if os.path.normcase(p).startswith(os.path.normcase(r.rstrip("\\/") + os.sep)):
        return p[len(r.rstrip("\\/")):].lstrip("\\/").replace("\\", "/")
    return path


Result = collections.namedtuple("Result", "code out err timed_out pid")


def stop_tree(proc):
    """Stop a process and every process it started: a hung tool is a child of what started it,
    and stopping the parent alone leaves it running."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif proc.poll() is None:
        # Started in a session of its own (run), so its process group is its own pid.
        os.killpg(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        detail("process %d did not stop within 10s of being killed" % proc.pid, "yellow")


def run(args, cwd=None, timeout=None, env=None, merge=True, stdin_text=""):
    """Run a command and return Result(code, out, err, timed_out, pid). A non-zero exit never
    raises: the exit code says whether it worked, and the caller reads it. stderr is merged
    into out unless merge is False. The command gets stdin_text and then end of input, so one
    that reads its input cannot wait. Past timeout seconds it is stopped with every process it
    started, and code is None."""
    p = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT if merge else subprocess.PIPE,
                         start_new_session=(os.name != "nt"))
    timed_out = False
    try:
        out, err = p.communicate(stdin_text.encode("utf-8"), timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        stop_tree(p)
        try:
            out, err = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            # A process that left the tree still holds the pipe; what it printed is lost, and
            # the timeout is what is reported.
            out, err = b"", b""
    decode = lambda b: (b or b"").decode("utf-8", errors="replace")
    return Result(None if timed_out else p.returncode, decode(out), decode(err), timed_out, p.pid)


def tracked_files(*patterns):
    """Files git would commit - tracked, plus untracked files .gitignore does not exclude - as
    full paths. A tree walk also sees what .gitignore excludes (local configs, output,
    secrets), so any scan that SELECTS files asks git instead. Raises outside a git repo
    rather than scanning nothing."""
    r = root()
    # Case-insensitive, as Windows is: a plain pathspec misses Tool.PY when asked for *.py.
    spec = [p if p.startswith(":") else ":(icase)" + p for p in patterns]
    # NUL-separated and unquoted, so a name with a space or a letter outside ASCII arrives whole.
    res = run(["git", "-C", r, "-c", "core.quotepath=off", "ls-files", "-z", "--cached",
               "--others", "--exclude-standard", "--"] + spec, merge=False)
    if res.code != 0:
        raise RuntimeError("git ls-files failed in %s: %s" % (r, res.err.strip()))
    full = [os.path.normpath(os.path.join(r, n)) for n in sorted(set(res.out.split("\0"))) if n]
    if os.name == "nt":
        # A file deleted from the working copy is rightly skipped; one Windows cannot open
        # because its path is too long is not absent, and dropping it would pass every check on
        # it. The \\?\ form reaches a long path, so a deleted file is told apart.
        too_long = [f for f in full if len(f) >= 260 and not os.path.isfile(f)
                    and os.path.isfile(_extended(f))]
        if too_long:
            raise RuntimeError("%d tracked file(s) cannot be read here, their paths being 260 "
                               "characters or more: %s. Enable long paths in Windows, or shorten "
                               "them." % (len(too_long), ", ".join(rel(f) for f in too_long[:3])))
    return [f for f in full if os.path.isfile(f)]


def _extended(path):
    return "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path


def project_file(name):
    """A file by NAME, found among the tracked files. Addressing it as root/<name> hard-codes
    a layout decision into every suite, so moving it to a subfolder breaks unrelated suites.
    Ambiguity raises rather than picking one: a suite asserting against the wrong copy passes
    for the wrong reason."""
    if name in _project_files:
        return _project_files[name]
    want = os.path.normcase(name)
    hits = [f for f in tracked_files() if os.path.normcase(os.path.basename(f)) == want]
    if len(hits) == 1:
        _project_files[name] = hits[0]
        return hits[0]
    if not hits:
        raise FileNotFoundError("No tracked file named '%s' under %s." % (name, root()))
    raise RuntimeError("Found %d files named '%s': %s. Refusing to guess."
                       % (len(hits), name, ", ".join(rel(h) for h in hits)))


def source(path):
    """A file's text, decoded as UTF-8 with any BOM dropped and CRLF normalised to LF, so a
    correct pattern does not fail on line endings."""
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        return f.read().replace("\r\n", "\n")


def scripts_under_test():
    """The scripts the health checks hold to account: Scripts in test_config.toml when it
    names any, otherwise every tracked .py outside tests/ - derived, so a new script is covered
    the day it is added. ExcludeScripts removes paths by glob pattern."""
    cfg = config()
    names = [n for n in (cfg.get("Scripts") or []) if n]
    if names:
        files = [project_file(n) for n in names]
    else:
        files = [f for f in tracked_files("*.py") if not rel(f).startswith("tests/")]
    excl = [x.lower() for x in (cfg.get("ExcludeScripts") or []) if x]
    return [f for f in files if not any(fnmatch.fnmatchcase(rel(f).lower(), x) for x in excl)]


def _scratch_base():
    r = root()
    h = hashlib.sha1(os.path.normcase(r).encode("utf-8")).hexdigest()[:8]
    return os.path.join(tempfile.gettempdir(), "tests-%s-%s" % (os.path.basename(r), h))


def scratch(name="", fresh=False, shared=False):
    """A scratch folder under the system's temp folder for this suite run alone: a folder named
    for the run, inside the repo's own folder, removed when the suite signs off. Two runs - of
    one repo or of two - never share one. shared is the repo's folder itself, for what must
    outlive a run (the runner's timings). fresh empties the named folder first, for a suite that
    reuses a name."""
    base = _scratch_base()
    if not shared:
        base = os.path.join(base, RUN_ID)
    p = os.path.join(base, name) if name else base
    if fresh and os.path.exists(p):
        shutil.rmtree(p)
    os.makedirs(p, exist_ok=True)
    return p


# ------------------------------------------------------------------------- assertions


def check(name, got, want, width=58):
    """One assertion, counted into this suite's failures. Compared as STRINGS, so what the
    line prints is what was compared: 1 and '1' agree, and a value that prints like the one
    wanted never fails beside it as "got [1] want [1]"."""
    global _fail
    if str(got) == str(want):
        say("  PASS  %s %s" % (name.ljust(width), got), "green")
        return True
    say("  FAIL  %s got [%s] want [%s]" % (name.ljust(width), got, want), "red")
    _fail += 1
    return False


def skip(name, why):
    """A check that could not run here, and why. Never a PASS: a missing prerequisite is a
    fact about the machine, not a finding about the code."""
    say("  SKIP  %s - %s" % (name, why), "yellow")


def complete():
    """The closing convention run-all-tests.py reads: the literal 'ALL CHECKS PASSED' and exit
    0, or a non-zero exit. This run's scratch goes with it."""
    shutil.rmtree(os.path.join(_scratch_base(), RUN_ID), ignore_errors=True)
    say()
    if _fail:
        say("%d CHECK(S) FAILED" % _fail, "red")
        sys.exit(1)
    say("ALL CHECKS PASSED", "green")
    sys.exit(0)


def suite_verdict(text, exit_code):
    """The runner's reading of one suite's output. A suite is green only when it printed
    'ALL CHECKS PASSED', printed no FAIL line, AND exited 0 - any two of those disagreeing
    means the suite lost track of its own result, so the sign-off is worth nothing. A suite
    that passed no check is red too: either it died before it could, and its reason is in the
    tail, or it signed off having proved nothing."""
    lines = re.split(r"\r?\n", text)
    passed = sum(1 for l in lines if re.match(r"\s*PASS\s", l))
    failed = sum(1 for l in lines if re.match(r"\s*FAIL\s", l))
    skips = [l.strip() for l in lines if re.match(r"\s*SKIP\s", l)]
    signed = "ALL CHECKS PASSED" in text
    # A suite that made no check at all proved nothing, however it signed off.
    ok = signed and failed == 0 and exit_code == 0 and passed > 0
    if passed == 0 and failed == 0:
        shown = ["made no check"] + [l for l in lines if l.strip()][-5:]
    else:
        # Each failure with the evidence the suite printed under it (the file, the line), since
        # a count alone says what is wrong but not where.
        shown = []
        count = 0
        for i, l in enumerate(lines):
            if count >= 5:
                break
            if not re.match(r"\s*FAIL\s", l):
                continue
            count += 1
            shown.append(l)
            j = i + 1
            while j < len(lines) and j <= i + 3 and re.match(r"\s{8,}\S", lines[j]):
                shown.append(lines[j])
                j += 1
    if not ok and failed == 0 and passed > 0:
        # It passed checks and then lost its result - most often by raising. The reason is in
        # the tail.
        shown = (["exit code %s, sign-off %s" % (exit_code, "present" if signed else "missing")]
                 + [l for l in lines if l.strip() and not re.match(r"\s*PASS\s", l)][-5:])
    # Skips are reported, never judged: a check that could not run here is a fact about the
    # machine.
    return SimpleNamespace(passed=passed, failed=failed, skip=len(skips), skips=skips, ok=ok,
                           detail=shown)


def calibrating():
    """True only during the installer's --calibrate: it names a record file it has just made.
    A variable left behind in a shell or a CI job names a file long gone, and would otherwise
    turn every ratchet and floor into a silent pass."""
    p = os.environ.get("TEST_RATCHET_CALIBRATE", "")
    return bool(p) and os.path.isfile(p)


def _record(line):
    # A file of this process's own beside the record, so suites can calibrate in parallel.
    with open("%s.%d" % (os.environ["TEST_RATCHET_CALIBRATE"], os.getpid()), "a",
              encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")


def ratchet(key, count, what=None, examples=(), advice=""):
    """A count that may fall and never rise. Ceilings live in test_config.toml's Ratchets, so
    they sit in one place and the installer's --calibrate can set them from a real run. With
    StrictRatchets, a count BELOW its ceiling fails too: headroom lets the next one in free, so
    the ceiling comes down in the same commit as the fix. While calibrating, the count is
    recorded and nothing is judged."""
    what = what or key
    if calibrating():
        _record("Ratchets.%s=%d" % (key, count))
        say("  CALIBRATE  %s = %d" % (key, count), "cyan")
        return
    table = config().get("Ratchets") or {}
    if key not in table:
        check("ratchet '%s' has a ceiling in test_config.toml" % key, False, True)
        return
    ceiling = int(table[key])
    check("%s has not risen" % what, count <= ceiling, True)
    detail("%d (ceiling %d)" % (count, ceiling))
    if count > ceiling:
        for e in list(examples)[:8]:
            detail(e, "yellow")
        if advice:
            advise(advice)
    if config().get("StrictRatchets") and count < ceiling:
        check("  and its ceiling has come down to meet it (Ratchets.%s = %d)" % (key, count),
              ceiling, count)


def scanned(key, count, what=None):
    """Assert the DENOMINATOR of a scan. The likeliest way a scan rots is a filter that stops
    matching, after which it reports "0 problems" forever while reading nothing. The floor
    comes from test_config.toml's Floors (default 1); calibration sets it to half today's
    count, and to 0 for a scan that finds nothing yet - a repo adopting the framework before
    its first script - so the gate starts green. Recalibrate once there is something to count."""
    what = what or key
    if calibrating():
        _record("Floors.%s=%d" % (key, 0 if count == 0 else max(1, count // 2)))
        return
    table = config().get("Floors") or {}
    floor = int(table.get(key, 1))
    check("scanned a plausible number of %s" % what, count >= floor, True)
    detail("%d scanned (floor %d)" % (count, floor))


# ------------------------------------------------------------------------- the code under test


def parse(path):
    """A file's AST as the running Python parses it. One that does not parse raises
    SyntaxError, naming the line."""
    return ast.parse(source(path), filename=path)


def dotted(node):
    """The dotted name an expression spells - 'os.path.join' - or None for anything else."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def is_main_guard(test):
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)):
        return False
    pair = [test.left, test.comparators[0]]
    names = [n for n in pair if isinstance(n, ast.Name) and n.id == "__name__"]
    consts = [n for n in pair if isinstance(n, ast.Constant) and n.value == "__main__"]
    return len(names) == 1 and len(consts) == 1


# A type alias (type X = ...) is a definition too, on the Pythons that have one.
_DEFINES = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
            ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Pass) + tuple(
                getattr(ast, n) for n in ("TypeAlias",) if hasattr(ast, n))


def top_level_actions(tree, text=None):
    """Each statement at a module's top level that acts when the module is imported, as (line,
    text). Allowed: imports, definitions, assignments, a constant expression (the docstring),
    the `if __name__ == "__main__":` guard, and an if or try made only of those whose test
    calls nothing. An assignment may call something - `log = logging.getLogger(__name__)` must
    - and what it calls does run at import."""
    lines = (text or "").split("\n")
    found = []

    def calls(node):
        return node is not None and any(isinstance(n, ast.Call) for n in ast.walk(node))

    def walk(stmts, top):
        for s in stmts:
            if isinstance(s, _DEFINES):
                continue
            if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant):
                continue
            if isinstance(s, ast.If) and top and is_main_guard(s.test):
                walk(s.orelse, False)
                continue
            if isinstance(s, ast.If) and not calls(s.test):
                walk(s.body, False)
                walk(s.orelse, False)
                continue
            if isinstance(s, (ast.Try, getattr(ast, "TryStar", ast.Try))):
                walk(s.body, False)
                for h in s.handlers:
                    walk(h.body, False)
                walk(s.orelse, False)
                walk(s.finalbody, False)
                continue
            shown = lines[s.lineno - 1].strip() if 0 < s.lineno <= len(lines) else type(s).__name__
            found.append((s.lineno, shown))

    walk(tree.body, True)
    return found


def import_script(path):
    """Load a script by its path as a fresh module, and return it. Its top level runs -
    imports, definitions, assignments - and nothing else: a script whose top level would act
    (top_level_actions) is refused, naming the line, before any of it runs. Each call gives a
    new module, so one test's changes to its state never reach the next. The script's folder
    goes on sys.path, so its own imports of the modules beside it resolve."""
    global _imported
    text = source(path)
    acts = top_level_actions(ast.parse(text, filename=path), text)
    if acts:
        raise RuntimeError("import_script: %s acts when imported, at line %d: %s - its top level "
                           "may hold only imports, definitions, assignments and the __main__ "
                           "guard" % (rel(path), acts[0][0], acts[0][1]))
    _imported += 1
    stem = re.sub(r"\W", "_", os.path.splitext(os.path.basename(path))[0])
    name = "_under_test_%s_%d" % (stem, _imported)
    folder = os.path.dirname(os.path.abspath(path))
    if folder not in sys.path:
        sys.path.append(folder)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered, as an import would be: dataclasses and pickle look a module up by name.
    sys.modules[name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


_GUARD_TESTS = ("sys.platform", "os.name", "sys.version_info", "platform.")
_GUARD_ERRORS = ("ImportError", "ModuleNotFoundError", "Exception", "BaseException")


def _guarded(node, parents):
    """Whether a statement runs only on some platforms or Pythons: inside an if that tests
    sys.platform, os.name, sys.version_info or the platform module, or in the body of a try
    that catches the import failing."""
    child, p = node, parents.get(node)
    while p is not None:
        if isinstance(p, ast.If) and child is not p.test and any(g in ast.unparse(p.test) for g in _GUARD_TESTS):
            return True
        if isinstance(p, (ast.Try, getattr(ast, "TryStar", ast.Try))) and child in p.body:
            for h in p.handlers:
                types = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
                if h.type is None or any((dotted(x) or "").split(".")[-1] in _GUARD_ERRORS for x in types):
                    return True
        child, p = p, parents.get(p)
    return False


def imports_of(tree):
    """What a module imports, anywhere in it. modules is every module imported, by full name;
    names is (module, name) for each `from module import name`; aliases maps a local name to
    what it stands for ('np' -> 'numpy', 'run' -> 'subprocess.run'). guarded holds each module
    and each (module, name) imported only where it may be absent - under a platform or version
    test, or in a try that catches the import failing - which a check of one Python must not
    hold against it. A relative import is the project's own, and is left out."""
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    modules, names, aliases, plain = set(), set(), {}, set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                modules.add(a.name)
                if not _guarded(n, parents):
                    plain.add(a.name)
                if a.asname:
                    aliases[a.asname] = a.name
                else:
                    top = a.name.split(".")[0]
                    aliases[top] = top
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            modules.add(n.module)
            guarded = _guarded(n, parents)
            if not guarded:
                plain.add(n.module)
            for a in n.names:
                if a.name == "*":
                    continue
                names.add((n.module, a.name))
                if not guarded:
                    plain.add((n.module, a.name))
                # A __future__ import is a compiler switch, never called: a local variable that
                # shares its name ('annotations') is not a use of it.
                if n.module != "__future__":
                    aliases[a.asname or a.name] = n.module + "." + a.name
    guarded = {m for m in modules if m not in plain} | {p for p in names if p not in plain}
    return SimpleNamespace(modules=modules, names=names, aliases=aliases, guarded=guarded)


def bound_names(tree):
    """Every name a module binds anywhere - assigned, defined, a parameter, an import, an
    exception's name - so a call to one is not taken for the builtin of that name."""
    found = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            found.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(n.name)
        elif isinstance(n, ast.arg):
            found.add(n.arg)
        elif isinstance(n, ast.alias):
            found.add((n.asname or n.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            found.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            found.update(n.names)
    return found


def resolve_call(call, aliases, bound):
    """What a call stands for, by its qualified name - 'json.dumps' for `json.dumps(...)` or
    for `dumps(...)` after `from json import dumps`, 'builtins.open' for `open(...)` where the
    module never rebinds open - or None when the source cannot say: a method of an object, a
    variable, a name the module defines itself."""
    name = dotted(call.func)
    if not name:
        return None
    head, _, rest = name.partition(".")
    if head in aliases:
        return aliases[head] + ("." + rest if rest else "")
    if head not in bound and hasattr(builtins, head):
        return "builtins." + name
    return None


def calls_of(tree):
    """Every call a module makes to something it imported, or to a builtin it does not
    rebind, by what it stands for (resolve_call), with the keywords each passes: qualified
    name -> {'keywords': set, 'where': [line], 'keyword_lines': {keyword: [line]}, 'guarded':
    bool}. guarded is true when every such call goes through an import that imports_of calls
    guarded."""
    imp = imports_of(tree)
    bound = bound_names(tree)
    found = {}
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        qual = resolve_call(n, imp.aliases, bound)
        if not qual:
            continue
        head = dotted(n.func).split(".")[0]
        target = imp.aliases.get(head, "")
        module, _, attr = target.rpartition(".")
        guarded = target in imp.guarded or (module, attr) in imp.guarded
        entry = found.setdefault(qual, {"keywords": set(), "where": [], "keyword_lines": {}, "guarded": True})
        for k in n.keywords:
            if k.arg:
                entry["keywords"].add(k.arg)
                entry["keyword_lines"].setdefault(k.arg, []).append(n.lineno)
        entry["where"].append(n.lineno)
        entry["guarded"] = entry["guarded"] and guarded
    return found


# ------------------------------------------------------------------------- target Pythons


def current_python():
    return "%d.%d" % sys.version_info[:2]


def find_python(version):
    """The executable of Python `version` ('3.12'), or None when it is not installed. The
    running one is itself; otherwise `py -3.N` on Windows, `python3.N` on PATH, then `uv python
    find`. Each candidate must answer with that version: the Windows Store's python3.exe is an
    alias that runs no Python at all. Remembered for the process."""
    version = str(version)
    if version in _pythons:
        return _pythons[version]
    found = sys.executable if version == current_python() else None
    candidates = []
    if found is None:
        if os.name == "nt" and shutil.which("py"):
            candidates.append(["py", "-" + version])
        exe = shutil.which("python" + version)
        if exe:
            candidates.append([exe])
        if shutil.which("uv"):
            r = run(["uv", "python", "find", version], timeout=60, merge=False)
            if r.code == 0 and r.out.strip():
                candidates.append([r.out.strip().splitlines()[-1]])
    for c in candidates:
        r = run(c + ["-c", "import sys; print('%d.%d' % sys.version_info[:2]); print(sys.executable)"],
                timeout=30, merge=False)
        lines = r.out.strip().splitlines()
        if r.code == 0 and len(lines) >= 2 and lines[0].strip() == version:
            found = lines[1].strip()
            break
    _pythons[version] = found
    return found


def target_pythons():
    """(version, executable or None) for each of TargetPythons. Empty, the default, is the
    Python that runs the suites."""
    versions = [str(v) for v in (config().get("TargetPythons") or [])] or [current_python()]
    return [(v, find_python(v)) for v in versions]


def stdlib_names(exe=None):
    """The standard library's top-level module names in a Python (the running one, by
    default), as it lists them. Remembered for the process."""
    if exe is None:
        return set(sys.stdlib_module_names)
    if exe not in _stdlib:
        r = run([exe, "-c", "import sys, json; print(json.dumps(sorted(getattr(sys, 'stdlib_module_names', []))))"],
                timeout=60, merge=False)
        if r.code != 0:
            raise RuntimeError("%s could not list its standard library: %s" % (exe, r.err.strip()))
        _stdlib[exe] = set(json.loads(r.out))
    return _stdlib[exe]


# Runs in a child of one target Python: compiles the listed files, then resolves the modules,
# the names taken from them and the keywords passed to their callables. Written for 3.8. Results
# go to a JSON file, never stdout, which an imported module may write to.
PROBE = r'''
import importlib, importlib.util, inspect, json, sys

def main():
    with open(sys.argv[1], encoding="utf-8") as f:
        req = json.load(f)
    for p in req.get("paths", []):
        if p not in sys.path:
            sys.path.append(p)
    std = set(getattr(sys, "stdlib_module_names", ())) | set(req.get("std", []))
    out = {"python": "%d.%d" % sys.version_info[:2], "executable": sys.executable,
           "parse": [], "modules": {}, "names": {}, "calls": {}}
    for f in req.get("files", []):
        errors = []
        try:
            with open(f, "rb") as fh:
                compile(fh.read(), f, "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as e:
            errors.append("line %s: %s" % (getattr(e, "lineno", "?"), getattr(e, "msg", e)))
        out["parse"].append({"file": f, "errors": errors})
    judged = lambda m: req.get("mode") == "all" or m.split(".")[0] in std
    loaded = {}

    def load(m):
        if m not in loaded:
            status, mod = "OK", None
            try:
                if importlib.util.find_spec(m) is None:
                    status = "NotFound"
                else:
                    mod = importlib.import_module(m)
            except ModuleNotFoundError:
                status = "NotFound"
            except Exception as e:
                status = "ImportError: %s: %s" % (type(e).__name__, e)
            loaded[m] = (status, mod)
        return loaded[m]

    for m in req.get("modules", []):
        if judged(m):
            out["modules"][m] = load(m)[0]
    for m, n in req.get("names", []):
        if not judged(m):
            continue
        status, mod = load(m)
        if status == "OK":
            has = hasattr(mod, n)
            if not has:
                try:
                    has = importlib.util.find_spec(m + "." + n) is not None
                except Exception:
                    has = False
            status = "OK" if has else "NoName"
        out["names"][m + ":" + n] = status
    for qual, keywords in sorted(req.get("calls", {}).items()):
        if not judged(qual):
            continue
        parts = qual.split(".")
        # NotFound: no module of the name loads. NoName: the module does, and lacks the callable -
        # itertools.batched in a Python before 3.12.
        obj, status = None, "NotFound"
        for i in range(len(parts) - 1, 0, -1):
            st, mod = load(".".join(parts[:i]))
            if st != "OK":
                continue
            obj = mod
            for attr in parts[i:]:
                obj = getattr(obj, attr, None)
                if obj is None:
                    break
            status = "OK" if obj is not None else "NoName"
            break
        bad = []
        if status == "OK" and keywords:
            try:
                params = inspect.signature(obj).parameters.values()
            except (TypeError, ValueError) as e:
                status, bad = "Unreadable", ["%s" % e]
            else:
                if not any(p.kind == p.VAR_KEYWORD for p in params):
                    allowed = set(p.name for p in params if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY))
                    bad = sorted(k for k in keywords if k not in allowed)
                    if bad:
                        status = "BadKeyword"
        out["calls"][qual] = {"status": status, "bad": bad}
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(out, f)

main()
'''


def probe(exe, files=(), modules=(), names=(), calls=None, mode="stdlib", paths=()):
    """Compile `files` and resolve `modules`, `names` ((module, name) pairs) and `calls`
    (qualified name -> keywords) in a child of the Python at `exe`. Mode 'stdlib' judges only
    the standard library - deterministic per Python version, so it belongs in the commit gate;
    'all' also judges installed packages and the project's own modules (found on `paths`),
    which is a fact about this machine. Returns the child's answer as a dict."""
    work = scratch("probe-%d" % os.getpid())
    script = os.path.join(work, "probe.py")
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write(PROBE)
    tag = hashlib.sha1(exe.encode("utf-8")).hexdigest()[:8]
    req, out = os.path.join(work, "in-%s.json" % tag), os.path.join(work, "out-%s.json" % tag)
    if os.path.exists(out):
        os.remove(out)
    std = sorted(stdlib_names()) if mode == "stdlib" else []
    with open(req, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"files": list(files), "modules": sorted(modules), "names": sorted(names),
                   "calls": {k: sorted(v) for k, v in (calls or {}).items()},
                   "mode": mode, "std": std, "paths": list(paths)}, f)
    r = run([exe, script, req, out], cwd=root(), timeout=600)
    if r.code != 0 or not os.path.isfile(out):
        raise RuntimeError("The probe in %s failed (exit %s): %s"
                           % (exe, r.code, " | ".join(r.out.strip().splitlines()[-5:])))
    with open(out, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------------- convention suites


def assert_adapted(adapted_for):
    """True when tests/_project.py names a script this repo has. Otherwise ONE failing check
    saying what to do, and False - the suite then closes, instead of failing a dozen times on
    functions that belong to some other project."""
    name = adapted_for or ""
    if name and any(os.path.normcase(os.path.basename(f)) == os.path.normcase(name)
                    for f in tracked_files()):
        return True
    check("tests/_project.py is adapted to this project (ADAPTED_FOR = '%s')" % name, False, True)
    advise("set PROJECT_SCRIPT and ADAPTED_FOR, and rewrite the fake and the entry points for "
           "this script")
    return False


def _field(row, name):
    return row.get(name) if isinstance(row, dict) else getattr(row, name, None)


def row_map(rows, key, outcome):
    """A run's rows as key -> outcome, by the row field names _project.py gives. Keys seen
    twice are in .duplicates: two rows with one identity make every comparison ambiguous."""
    found, dupes = {}, []
    for r in rows or []:
        k = str(_field(r, key))
        if k in found:
            dupes.append(k)
        else:
            found[k] = str(_field(r, outcome))
    return SimpleNamespace(map=found, duplicates=dupes)


def compare_rows(a, b):
    """The differences between two runs, keyed on identity, never on what the run measured: a
    verdict that moved is ONE CHANGED entry, not a VANISHED plus an APPEARED."""
    out = []
    for k in a:
        if k not in b:
            out.append({"kind": "VANISHED", "key": k, "was": a[k], "now": ""})
        elif a[k] != b[k]:
            out.append({"kind": "CHANGED", "key": k, "was": a[k], "now": b[k]})
    for k in b:
        if k not in a:
            out.append({"kind": "APPEARED", "key": k, "was": "", "now": b[k]})
    return out


# ------------------------------------------------------------------------- prerequisites


def loose_version(text):
    """The version in a line of text as a tuple - 'Python 3.12.1' is (3, 12, 1), 'v20' is
    (20, 0) - or None when there is none. A dotted number standing on its own wins over a digit
    in a name ('7-Zip 23.01' is 23.1, not 7); a bare number counts only when there is no dotted
    one."""
    m = re.search(r"(?<![\w.-])\d+(\.\d+){1,3}", str(text or ""))
    if not m:
        m = re.search(r"\d+(\.\d+){0,3}", str(text or ""))
    if not m:
        return None
    v = m.group(0)
    if "." not in v:
        v += ".0"
    return tuple(int(p) for p in v.split("."))


def version_text(v):
    return "" if v is None else ".".join(str(p) for p in v)


def compare_versions(a, b):
    """-1, 0 or 1, with absent parts read as 0: 2.1 is 2.1.0."""
    n = max(len(a), len(b))
    a, b = tuple(a) + (0,) * (n - len(a)), tuple(b) + (0,) * (n - len(b))
    return (a > b) - (a < b)


def prerequisites(table=None):
    """test_config.toml's Prerequisites as well-formed entries, plus a line for each malformed
    one. An unknown key is malformed: a mistyped 'MinVersion' would otherwise check nothing, in
    silence.
      Packages  { Name, Minimum, Pythons }                  Minimum and Pythons optional
      Tools     { Name, Arguments, Minimum, TimeoutSeconds }
                Minimum needs Arguments, which print the version; TimeoutSeconds defaults to 30
    Pythons, empty or absent, means every target Python. A tool is checked once: PATH does not
    depend on which Python runs."""
    if table is None:
        table = config().get("Prerequisites")
    packages, tools, bad = [], [], []
    if table is None:
        table = {}
    if not isinstance(table, dict):
        table, bad = {}, ["Prerequisites is not a table"]
    bad += ["Prerequisites.%s is not Packages or Tools" % k for k in table
            if k not in ("Packages", "Tools")]
    for kind in ("Packages", "Tools"):
        allowed = (("Name", "Minimum", "Pythons") if kind == "Packages"
                   else ("Name", "Arguments", "Minimum", "TimeoutSeconds"))
        entries = table.get(kind) or []
        if not isinstance(entries, list):
            bad.append("Prerequisites.%s is not a list" % kind)
            continue
        for i, e in enumerate(x for x in entries if x is not None):
            at = "Prerequisites.%s[%d]" % (kind, i)
            if not isinstance(e, dict):
                bad.append("%s: not a table" % at)
                continue
            why = []
            if not str(e.get("Name") or "").strip():
                why.append("no Name")
            why += ["unknown key '%s'" % k for k in e if k not in allowed]
            if "Minimum" in e:
                # Unquoted, 2.90 is the number 2.9: a version is always written as a string.
                if not isinstance(e["Minimum"], str):
                    why.append("Minimum %s is not quoted - write it as a string, \"%s\""
                               % (e["Minimum"], e["Minimum"]))
                elif not re.fullmatch(r"\d+(\.\d+){0,3}", e["Minimum"]):
                    why.append("Minimum '%s' is not a version" % e["Minimum"])
            pythons = e.get("Pythons") or []
            if not isinstance(pythons, list):
                why.append("Pythons is not a list")
                pythons = []
            why += ["Python '%s' is not a version written as \"3.N\"" % p for p in pythons
                    if not (isinstance(p, str) and re.fullmatch(r"\d+\.\d+", p))]
            if "TimeoutSeconds" in e and not (isinstance(e["TimeoutSeconds"], int)
                                              and not isinstance(e["TimeoutSeconds"], bool)
                                              and 1 <= e["TimeoutSeconds"] <= 9999):
                why.append("TimeoutSeconds '%s' is not a whole number of seconds from 1 to 9999"
                           % e["TimeoutSeconds"])
            args = e.get("Arguments") or []
            if not isinstance(args, list):
                why.append("Arguments is not a list")
                args = []
            args = [str(a) for a in args if a is not None]
            if kind == "Tools" and "Minimum" in e and not args:
                why.append("Minimum without the Arguments that print the version")
            if why:
                bad.append("%s (%s): %s" % (at, e.get("Name"), "; ".join(why)))
                continue
            minimum = loose_version(e["Minimum"]) if "Minimum" in e else None
            if kind == "Packages":
                packages.append(SimpleNamespace(name=str(e["Name"]), minimum=minimum,
                                                pythons=pythons, source="test_config.toml"))
            else:
                tools.append(SimpleNamespace(name=str(e["Name"]), arguments=args, minimum=minimum,
                                             timeout=e.get("TimeoutSeconds", 30)))
    return SimpleNamespace(packages=packages, tools=tools, problems=bad)
