"""
The generator's own test suite (scripts/tests, pytest) passes.

scripts/tests holds the repo's structure and secret-hygiene checks and the generator's unit
tests. This runs it in the Python that runs the runner, so the commit gate covers it, and
reports each failing test by name. A pytest that cannot run is a failure that names the cause,
never a pass: no tests collected is not "nothing failed".
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _testcommon as t


def summary(text):
    """The counts in pytest's closing line ("3 failed, 105 passed, 2 skipped in 4.1s"), as a
    dict of outcome to count; empty when there is no such line."""
    last = [l for l in text.splitlines() if re.search(r"\b(passed|failed|error|errors|skipped)\b", l)
            and re.search(r"\bin [0-9.]+s\b", l)]
    if not last:
        return {}
    return {k.rstrip("s") if k == "errors" else k: int(n)
            for n, k in re.findall(r"(\d+) (passed|failed|errors|error|skipped)", last[-1])}


def failing(text):
    """The test ids pytest reports as failed or errored, from its short summary."""
    return re.findall(r"(?m)^(?:FAILED|ERROR) (\S+)", text)


t.section("pytest is available")
probe = t.run([sys.executable, "-m", "pytest", "--version"], cwd=t.root(), timeout=60)
t.check("pytest runs in this Python", probe.code, 0)
if probe.code != 0:
    t.advise("install it: %s -m pip install -r scripts/requirements-test.txt" % sys.executable)
    t.complete()

t.section("scripts/tests passes")
res = t.run([sys.executable, "-m", "pytest", "scripts/tests", "-q", "-rfE", "-p", "no:cacheprovider"],
            cwd=t.root(), timeout=int(t.config().get("SuiteTimeoutSeconds", 600)) - 30)
counts = summary(res.out)
t.check("pytest finished within its time", res.timed_out, False)
t.check("pytest reported its counts", bool(counts), True)
t.scanned("PytestTests", counts.get("passed", 0) + counts.get("failed", 0), "pytest tests run")
t.check("no pytest test failed", counts.get("failed", 0), 0)
t.check("no pytest test errored", counts.get("error", 0), 0)
t.check("pytest exited 0", res.code, 0)
for name in failing(res.out)[:10]:
    # Indented under the failed check, and never a line opening with FAIL, which the runner
    # would count as this suite's own.
    t.detail("pytest: %s" % name, "yellow")
if counts.get("skipped"):
    t.detail("%d skipped (tests that need capture's local files skip themselves without them)"
             % counts["skipped"])

t.section("the summary parser, proven against pytest's own lines")
t.check("SELF-TEST: a mixed summary is read whole",
        summary("== 3 failed, 105 passed, 2 skipped in 4.10s =="), {"failed": 3, "passed": 105, "skipped": 2})
t.check("  errors count as errors", summary("1 passed, 2 errors in 0.5s"), {"passed": 1, "error": 2})
t.check("  a line with no timing is not a summary", summary("collected 3 items / 1 passed"), {})
t.check("SELF-TEST: failed and errored test ids are both named",
        failing("FAILED a.py::t1 - x\nERROR b.py::t2\nfailed c.py"), ["a.py::t1", "b.py::t2"])

t.complete()
