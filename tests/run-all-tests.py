"""
Run every test suite and summarise the result.

Each test-*.py runs in its own child process of the Python that runs this, in parallel. Output
is printed in FILE order whatever order suites finish in, so two runs stay comparable, and it
still streams: a result prints once every suite before it is done.

A suite is green only when it printed 'ALL CHECKS PASSED', printed no FAIL line, exited 0, and
passed at least one check (suite_verdict in _testcommon.py). A suite that made no checks at all
shows the tail of what it said, because a syntax error reported as "pass=0 fail=0" is how a
broken suite hides. A suite still running after test_config.toml's SuiteTimeoutSeconds is
stopped, with every process it started, and is red.

Dispatch is longest-first, from the durations the previous full run recorded in the scratch
folder. A suite nobody has timed sorts first: assuming a new suite is slow costs a little
packing once, assuming it is fast makes it the tail. The record is machine-local and disposable.

Treat a green run as "nothing regressed", not as "this works". The suites are static and
fake-based; how a call behaves against a real system is outside what they can see.

    py -3 tests/run-all-tests.py
    py -3 tests/run-all-tests.py --name test-script-health
    py -3 tests/run-all-tests.py --include-environment
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _testcommon as t


def main():
    ap = argparse.ArgumentParser(description="Run every test suite and summarise the result.")
    ap.add_argument("--name", nargs="*", default=[],
                    help="run only these suites, by base name (a,b or a b); environment suites "
                         "run when named")
    ap.add_argument("--parallel", type=int, default=0,
                    help="suites at once; default one per logical processor, at most 8. 1 runs "
                         "them one at a time, the thing to reach for when a parallel run reports "
                         "something strange")
    ap.add_argument("--include-environment", action="store_true",
                    help="also run EnvironmentSuites, which assert against what is installed on "
                         "this machine; run them whenever the imports or tools the scripts use "
                         "change")
    ap.add_argument("--path", default="", help="run the suites in this folder instead of this one")
    a = ap.parse_args()

    cfg = t.config()
    folder = os.path.abspath(a.path) if a.path else t.HERE
    files = sorted(os.path.join(folder, f) for f in os.listdir(folder)
                   if re.fullmatch(r"test-.+\.py", f) and os.path.isfile(os.path.join(folder, f)))
    missing_extra = False
    if not a.path:
        for x in cfg.get("ExtraSuites") or []:
            if not x:
                continue
            p = os.path.join(t.root(), x)
            if os.path.isfile(p):
                files.append(p)
            else:
                t.say("  MISSING  ExtraSuites names %s, which does not exist" % x, "red")
                missing_extra = True
    base = lambda f: os.path.splitext(os.path.basename(f))[0]
    names = [n.strip() for arg in a.name for n in arg.split(",") if n.strip()]
    if names:
        # A name that matches nothing is a typo, not a request to run nothing and call it green.
        unknown = [n for n in names if n not in {base(f) for f in files}]
        for u in unknown:
            t.say("  MISSING  --name %s matches no suite" % u, "red")
        if unknown:
            sys.exit(1)
        files = [f for f in files if base(f) in names]
    elif not a.include_environment:
        files = [f for f in files if base(f) not in (cfg.get("EnvironmentSuites") or [])]
    if not files:
        t.say("  no suites matched", "red")
        sys.exit(1)

    timeout = int(cfg.get("SuiteTimeoutSeconds") or 600)
    parallel = a.parallel if a.parallel > 0 else min(os.cpu_count() or 1, 8)

    shared = t.scratch(shared=True)
    timing_file = os.path.join(shared, "suite-timings.json")
    # A suite that raises or is stopped never signs off, so its run folder stays. One untouched
    # for a day is far past any timeout, so no running suite owns it.
    for d in os.listdir(shared):
        p = os.path.join(shared, d)
        if re.fullmatch(r"[0-9a-f]{32}", d) and os.path.isdir(p) and time.time() - os.path.getmtime(p) > 86400:
            shutil.rmtree(p, ignore_errors=True)
    est = {}
    if os.path.isfile(timing_file):
        try:
            with open(timing_file, encoding="utf-8") as f:
                est = {str(k): float(v) for k, v in json.load(f).items()}
        except (OSError, ValueError, AttributeError):
            est = {}   # a corrupt record costs only ordering

    pending = [{"index": i, "file": f, "est": est.get(base(f), float("inf"))}
               for i, f in enumerate(files)]
    # Each child writes bytecode nowhere and its output as UTF-8. Not Python's UTF-8 mode: that
    # would also change what open() reads with no encoding, and hide that hazard in the code
    # under test.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")

    def start(item):
        # A file, not a pipe: a process the suite started can hold the handle after the suite
        # itself has exited, and a pipe would wait for it.
        out = tempfile.TemporaryFile()
        proc = subprocess.Popen([sys.executable, item["file"]], stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT, env=env, cwd=t.root(),
                                start_new_session=(os.name != "nt"))
        return {"item": item, "proc": proc, "out": out, "started": time.monotonic(),
                "timed_out": False}

    def finish(job):
        job["proc"].wait()
        job["out"].seek(0)
        text = job["out"].read().decode("utf-8", errors="replace")
        job["out"].close()
        v = t.suite_verdict(text, job["proc"].returncode)
        if job["timed_out"]:
            v.ok = False
            v.detail = (["timed out after %ds and was stopped, with every process it started" % timeout]
                        + [l for l in re.split(r"\r?\n", text) if l.strip()][-3:])
        return {"name": base(job["item"]["file"]), "v": v,
                "seconds": time.monotonic() - job["started"]}

    def show(r):
        v = r["v"]
        t.say("  %-6s %-34s pass=%-4d fail=%-4d skip=%-3d %6.1fs"
              % ("PASS" if v.ok else "FAIL", r["name"], v.passed, v.failed, v.skip, r["seconds"]),
              "green" if v.ok else "red")
        if not v.ok:
            for l in v.detail:
                t.say("          %s" % l, "yellow")
        # A skip leaves the suite green, and is shown so that a machine missing a Python says so.
        for s in v.skips:
            t.say("          %s" % s, "yellow")

    running, results = [], {}
    total_pass = total_fail = total_skip = 0
    failed = []
    next_to_print = 0
    clock = time.monotonic()
    while pending or running:
        while len(running) < parallel and pending:
            nxt = max(pending, key=lambda p: p["est"])
            pending.remove(nxt)
            running.append(start(nxt))
        time.sleep(0.1)
        for j in running:
            if j["proc"].poll() is None and time.monotonic() - j["started"] > timeout:
                t.stop_tree(j["proc"])
                j["timed_out"] = True
        for j in [j for j in running if j["proc"].poll() is not None]:
            results[j["item"]["index"]] = finish(j)
            running.remove(j)
        while next_to_print in results:
            r = results[next_to_print]
            show(r)
            total_pass += r["v"].passed
            total_fail += r["v"].failed
            total_skip += r["v"].skip
            if not r["v"].ok:
                failed.append(r["name"])
            next_to_print += 1
    elapsed = time.monotonic() - clock

    # Only a full run records timings: a --name run sees a handful of suites. Merged over the
    # old estimates, so a suite that did not run keeps its last figure.
    if not names and not a.path and results:
        merged = {k: v for k, v in est.items() if re.fullmatch(r"[A-Za-z0-9._-]+", k)}
        merged.update({r["name"]: round(r["seconds"], 2) for r in results.values()
                       if re.fullmatch(r"[A-Za-z0-9._-]+", r["name"])})
        try:
            with open(timing_file, "w", encoding="utf-8", newline="\n") as f:
                json.dump(dict(sorted(merged.items())), f, indent=1)
        except OSError as e:
            # Dispatch order is an optimisation; failing to record it must not fail the run.
            t.say("  note: suite timings not recorded: %s" % e, "gray")

    done = sorted(results.values(), key=lambda r: -r["seconds"])
    work = sum(r["seconds"] for r in done)
    if len(done) > 1:
        t.say()
        t.say("  slowest suites", "gray")
        for r in done[:5]:
            t.say("    %6.1fs  %-34s %3.0f%% of the work"
                  % (r["seconds"], r["name"], 100 * r["seconds"] / max(work, 0.001)), "gray")

    t.say()
    t.say("  TOTAL  pass=%d  fail=%d  skip=%d  across %d suite(s) in %.1fs (%d at a time, Python %s)"
          % (total_pass, total_fail, total_skip, len(files), elapsed, parallel, t.current_python()),
          "white")
    if failed or missing_extra:
        if failed:
            t.say("  failing: %s" % ", ".join(failed), "red")
        sys.exit(1)
    t.say("  ALL SUITES PASSED", "green")
    sys.exit(0)


main()
