"""
What the scripts need installed is installed on this machine, at a version they work with, in
every target Python that runs them.

The packages come from test_config.toml's Prerequisites.Packages and from each script's PEP 723
block (# /// script), whose requires-python each target Python must also meet; the native tools
come from Prerequisites.Tools. A package is looked up by its distribution name in a child of
every target Python it applies to. A tool must be on PATH, answer within TimeoutSeconds (default
30) with no input, print its version when run with its Arguments, and meet its Minimum. A tool
that is found but exits non-zero - a launcher with no runtime behind it, a Store alias standing
in for the real program - does not report a version.

ENVIRONMENT suite: it goes red when the machine changes, not when the code does, so the commit
gate leaves it out. Run it with run-all-tests.py --include-environment on a machine that will
run the scripts, and whenever what they need changes.

  installed      the package is installed for that Python                  -> assert
  version        its version meets every specifier                         -> assert
  on PATH        the tool resolves to a program                            -> assert
  answers        it exits within its TimeoutSeconds                        -> assert
  version        it exits 0 and prints a version with its Arguments        -> assert

Versions compare as PEP 440 does for the operators ==, !=, >=, <=, >, <, ~= and a trailing .*,
with absent parts read as 0; a pre-release or local version compares as its release.
"""

import json
import os
import re
import shutil
import sys
import time
import tomllib
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _testcommon as t

pre = t.prerequisites()
for p in pre.problems:
    t.detail("not checked, malformed (test-harness fails on it): %s" % p, "yellow")

# ------------------------------------------------------------------ versions and requirements


def allows(have, specs):
    """Whether a version meets every (operator, version) specifier."""
    for op, text in specs:
        star = text.endswith(".*")
        if star:
            # The prefix as written: 1.* is any 1.x, where loose_version would read 1 as 1.0.
            if not re.fullmatch(r"\d+(\.\d+)*", text[:-2]):
                return False
            want = tuple(int(x) for x in text[:-2].split("."))
        else:
            want = t.loose_version(text)
        if want is None:
            return False
        if star:
            prefix = have[:len(want)] + (0,) * (len(want) - len(have[:len(want)]))
            match = prefix == want
            if (op == "==" and not match) or (op == "!=" and match):
                return False
            continue
        c = t.compare_versions(have, want)
        if op == "~=":
            # ~=1.4.5 is >=1.4.5 and ==1.4.*: the last part may rise, the ones before stay.
            stem = want[:-1] if len(want) > 1 else want
            prefix = have[:len(stem)] + (0,) * (len(stem) - len(have[:len(stem)]))
            if c < 0 or prefix != stem:
                return False
        elif not {"==": c == 0, "===": c == 0, "!=": c != 0, ">=": c >= 0, "<=": c <= 0,
                  ">": c > 0, "<": c < 0}.get(op, False):
            return False
    return True


def parse_requirement(text):
    """A PEP 508 requirement as (name, specifiers, marker), or None when it is not one this
    suite reads: name[extras] specifiers ; marker. A URL requirement is not."""
    m = re.fullmatch(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*\(?([^;@()]*)\)?\s*(?:;\s*(.+))?", text)
    if not m:
        return None
    specs = re.findall(r"(===|~=|==|!=|<=|>=|<|>)\s*([^\s,]+)", m.group(3))
    if m.group(3).strip() and not specs:
        return None
    return m.group(1), specs, (m.group(4) or "").strip()


def script_metadata(text):
    """A script's PEP 723 block (# /// script ... # ///) as a dict, or None when it has none."""
    m = re.search(r"(?m)^# /// script\s*$\n((?:^#(?: .*)?$\n)*?)^# ///\s*$", text)
    if not m:
        return None
    body = "\n".join(l[2:] if l.startswith("# ") else l[1:] for l in m.group(1).splitlines())
    return tomllib.loads(body)


t.section("comparing versions")
v = t.loose_version
t.check("2.1 meets >=2.1.0", allows(v("2.1"), [(">=", "2.1.0")]), True)
t.check("2.0.9 is below >=2.1", allows(v("2.0.9"), [(">=", "2.1")]), False)
t.check("2.1.0 is ==2.1", allows(v("2.1.0"), [("==", "2.1")]), True)
t.check("2.1.1 is not ==2.1", allows(v("2.1.1"), [("==", "2.1")]), False)
t.check("1.9 is ==1.*", allows(v("1.9"), [("==", "1.*")]), True)
t.check("2.0 is not ==1.*", allows(v("2.0"), [("==", "1.*")]), False)
t.check("1.5 is not <=1.4.9", allows(v("1.5"), [("<=", "1.4.9")]), False)
t.check("1.0.0.1 is not <=1.0", allows(v("1.0.0.1"), [("<=", "1.0")]), False)
t.check("1.0 is <=1.0", allows(v("1.0"), [("<=", "1.0")]), True)
t.check("2.5 is ~=2.2, and 3.0 is not", "%s %s" % (allows(v("2.5"), [("~=", "2.2")]), allows(v("3.0"), [("~=", "2.2")])), "True False")
t.check("1.4.9 is ~=1.4.5, and 1.5.0 is not", "%s %s" % (allows(v("1.4.9"), [("~=", "1.4.5")]), allows(v("1.5.0"), [("~=", "1.4.5")])), "True False")
t.check("1.3 is not !=1.3, and 1.3.1 is", "%s %s" % (allows(v("1.3"), [("!=", "1.3")]), allows(v("1.3.1"), [("!=", "1.3")])), "False True")
t.check("every specifier must hold: 3.0 is not >=2.31,<3", allows(v("3.0"), [(">=", "2.31"), ("<", "3")]), False)
t.check("a requirement reads as name, specifiers and marker",
        parse_requirement("requests[socks] >=2.31, <3 ; python_version < '3.12'"),
        ("requests", [(">=", "2.31"), ("<", "3")], "python_version < '3.12'"))
t.check("  a bare name has no specifiers", parse_requirement("rich"), ("rich", [], ""))
t.check("  a URL requirement is not read", parse_requirement("pkg @ https://example.com/pkg.zip"), None)
block = ('#!/usr/bin/env python3\n# /// script\n# requires-python = ">=3.11"\n# dependencies = [\n'
         '#   "requests>=2.31",\n# ]\n# ///\nimport requests\n')
t.check("a PEP 723 block reads as its table", script_metadata(block),
        {"requires-python": ">=3.11", "dependencies": ["requests>=2.31"]})
t.check("  a script with none has none", script_metadata("import os\n"), None)

# ------------------------------------------------------------------ what the scripts need
need = [SimpleNamespace(name=p.name, specs=[(">=", t.version_text(p.minimum))] if p.minimum else [],
                        pythons=p.pythons, source="test_config.toml") for p in pre.packages]
accepts = []    # (script, requires-python specifiers)
unread = []
for s in t.scripts_under_test():
    try:
        meta = script_metadata(t.source(s))
    except tomllib.TOMLDecodeError as e:
        unread.append("%s: its # /// script block is not TOML: %s" % (t.rel(s), e))
        continue
    if not meta:
        continue
    rp = parse_requirement("python" + str(meta.get("requires-python", "")))
    if meta.get("requires-python") and rp and rp[1]:
        accepts.append((t.rel(s), rp[1]))
    for d in meta.get("dependencies") or []:
        r = parse_requirement(str(d))
        if not r:
            unread.append("%s: dependency '%s' is not a requirement this suite reads" % (t.rel(s), d))
        elif r[2]:
            unread.append("%s: dependency '%s' has a marker, which this suite does not evaluate" % (t.rel(s), d))
        else:
            need.append(SimpleNamespace(name=r[0], specs=r[1], pythons=[], source="# /// script in %s" % t.rel(s)))
for u in unread:
    t.skip("a dependency", u)

# The positive control: a distribution written here, which the probe must find on its path.
control = t.scratch("control")
info = os.path.join(control, "stf_control-1.2.3.dist-info")
os.makedirs(info, exist_ok=True)
with open(os.path.join(info, "METADATA"), "w", encoding="utf-8", newline="\n") as f:
    f.write("Metadata-Version: 2.1\nName: stf-control\nVersion: 1.2.3\n")
LOOKUP = ("import importlib.metadata as m, json, sys\nsys.path.insert(0, sys.argv[2])\nout = {}\n"
          "for n in json.loads(sys.argv[1]):\n    try:\n        out[n] = m.version(n)\n"
          "    except m.PackageNotFoundError:\n        out[n] = None\nprint(json.dumps(out))\n")

for version, exe in t.target_pythons():
    t.section("Python %s" % version)
    if not exe:
        t.skip("packages in Python %s" % version, "Python %s is not installed on this machine" % version)
        continue
    here = [n for n in need if not n.pythons or version in n.pythons]
    r = t.run([exe, "-c", LOOKUP, json.dumps(["stf-control"] + sorted({n.name for n in here})), control],
              timeout=120, merge=False)
    if r.code != 0:
        raise RuntimeError("The package lookup in Python %s failed (exit %s): %s" % (version, r.code, r.err.strip()[-300:]))
    have = json.loads(r.out)
    t.check("the package lookup finds the control distribution in %s" % version, have.get("stf-control"), "1.2.3")
    missing = ["%s (%s)" % (n.name, n.source) for n in here if not have.get(n.name)]
    low = ["%s: installed %s, wants %s (%s)" % (n.name, have[n.name], ",".join(o + x for o, x in n.specs), n.source)
           for n in here if have.get(n.name) and n.specs and not allows(t.loose_version(have[n.name]) or (0,), n.specs)]
    t.check("every required package is installed in %s" % version, len(missing), 0)
    for m_ in missing:
        t.detail(m_, "yellow")
    t.check("every required package is at a version it accepts in %s" % version, len(low), 0)
    for l in low:
        t.detail(l, "yellow")
    refused = ["%s wants Python %s" % (s, ",".join(o + x for o, x in specs)) for s, specs in accepts
               if not allows(t.loose_version(version), specs)]
    t.check("Python %s is one every script accepts (requires-python)" % version, "; ".join(refused), "")
    t.detail("%d package(s) required here" % len(here))


# ------------------------------------------------------------------ tools
def tool_version(name, args, timeout):
    """Whether a tool is on PATH, and what it printed and returned with args. One still running
    after timeout seconds is stopped, with its children, and is timed out."""
    exe = shutil.which(name)
    if not exe:
        return SimpleNamespace(found=False)
    if not args:
        return SimpleNamespace(found=True, args=False)
    clock = time.monotonic()
    r = t.run([exe] + list(args), timeout=timeout)
    return SimpleNamespace(found=True, args=True, timed_out=r.timed_out, code=r.code, pid=r.pid,
                           out=" ".join(r.out.split()), seconds=time.monotonic() - clock,
                           version=t.loose_version(r.out) if r.code == 0 else None)


def running(pid):
    if os.name == "nt":
        return str(pid) in t.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"], merge=False).out
    return os.path.exists("/proc/%d" % pid) if os.path.isdir("/proc") else t.run(["kill", "-0", str(pid)]).code == 0


t.section("tools")
# The positive controls: the harness already needs git, so its version must read; and a tool
# that does not answer is stopped, not waited for.
git = tool_version("git", ["--version"], 30)
t.check("the tool probe reads the version git prints", bool(git.found and git.version), True)
slow = tool_version(sys.executable, ["-c", "import time; time.sleep(30)"], 1)
t.check("a tool still running at its timeout is timed out", slow.timed_out, True)
t.check("  well before it would have finished", slow.seconds < 15, True)
t.check("  and is no longer running", not running(slow.pid), True)
absent, hung, silent, old = [], [], [], []
for tool in pre.tools:
    cmd = " ".join([tool.name] + tool.arguments)
    r = tool_version(tool.name, tool.arguments, tool.timeout)
    if not r.found:
        absent.append(tool.name)
    elif not r.args:
        continue
    elif r.timed_out:
        hung.append("%s did not answer within %ds and was stopped" % (cmd, tool.timeout))
    elif not r.version:
        silent.append("%s exited %s: %s" % (cmd, r.code, r.out[:120] + ("..." if len(r.out) > 120 else "")))
    elif tool.minimum and t.compare_versions(r.version, tool.minimum) < 0:
        old.append("%s reports %s, below %s" % (cmd, t.version_text(r.version), t.version_text(tool.minimum)))
for label, found in (("is on PATH", absent), ("answers within its timeout", hung),
                     ("reports its version", silent), ("meets its minimum", old)):
    t.check("every required tool %s" % label, len(found), 0)
    for x in found:
        t.detail(x, "yellow")
t.detail("%d tool(s) required" % len(pre.tools))

t.complete()
