"""
Every module the scripts import is installed, with every name they take from it and every
keyword they pass to it, in each Python that will run them - checked against what is
INSTALLED on this machine.

A package updated past the keyword a script passes fails the same way a permissions problem
does, at the one call that uses it, on the one run that reaches it. Nothing else can see that.

ENVIRONMENT suite: it goes red when a package updates, not when the code changes, so the commit
gate leaves it out. Run it with run-all-tests.py --include-environment whenever you change which
modules the scripts import.

  resolved      the module imports, has the name, and takes every keyword   -> assert
  not found     no installed package provides it                            -> FAIL
  broken        it is installed and raises on import                        -> FAIL
  bad keyword   the callable lacks a keyword passed                         -> FAIL
  unreadable    the callable publishes no signature                         -> reported, unverifiable

An import under a platform or version test, or in a try that catches the import failing, may be
absent by design, and is left out.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _testcommon as t

scripts = t.scripts_under_test()
t.scanned("PyFiles", len(scripts), "scripts under test")

modules, names, calls = set(), set(), {}
for s in scripts:
    # A script that does not parse is test-script-health's to report; its imports cannot be read.
    try:
        tree = t.parse(s)
    except SyntaxError:
        t.detail("not read, as it does not parse: %s" % t.rel(s), "yellow")
        continue
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
# The project's own modules are found where its scripts would find them: beside each script,
# and at the repo's top.
paths = sorted({os.path.dirname(s) for s in scripts} | {t.root()})
t.detail("%d module(s) imported, %d name(s) taken from them, %d call(s) resolvable from the source"
         % (len(modules), len(names), len(calls)))
where = lambda m: ", ".join(sorted({w for q, c in calls.items() if q == m or q.startswith(m + ".") for w in c["where"]})[:3])

for version, exe in t.target_pythons():
    t.section("Python %s" % version)
    if not exe:
        t.skip("Python %s resolution" % version, "Python %s is not installed on this machine" % version)
        continue
    r = t.probe(exe, modules=modules, names=names, calls={k: v["keywords"] for k, v in calls.items()},
                mode="all", paths=paths)
    missing = sorted(m for m, st in r["modules"].items() if st == "NotFound")
    broken = sorted("%s: %s" % (m, st) for m, st in r["modules"].items() if st.startswith("ImportError"))
    no_name = sorted("%s from %s" % (k.split(":")[1], k.split(":")[0]) for k, st in r["names"].items() if st == "NoName")
    no_name += sorted("%s at %s" % (q, ", ".join(calls[q]["where"][:3])) for q, c in r["calls"].items() if c["status"] == "NoName")
    # Each bad keyword where it is passed, not every call to the callable.
    bad = ["%s(%s) at %s" % (q, k, ", ".join(calls[q]["keyword_where"].get(k, [])[:3]))
           for q, c in sorted(r["calls"].items()) if c["status"] == "BadKeyword" for k in c["bad"]]
    unread = sorted(q for q, c in r["calls"].items() if c["status"] == "Unreadable" and calls[q]["keywords"])
    t.check("every module the scripts import is installed in %s" % version, len(missing), 0)
    for m in missing[:10]:
        t.detail("%s%s" % (m, (" at " + where(m)) if where(m) else ""), "yellow")
    t.check("every module imports in %s" % version, len(broken), 0)
    for b in broken[:10]:
        t.detail(b, "yellow")
    t.check("every name taken from a module is there in %s" % version, ", ".join(no_name), "")
    t.check("every keyword binds in %s" % version, len(bad), 0)
    for b in bad[:10]:
        t.detail(b, "yellow")
    # A callable that publishes no signature - one written in C, often - is a fact about the
    # callable, and a permanent one: said, never passed, and not a SKIP, which would never clear.
    t.detail("%d call(s) resolved" % len([q for q, c in r["calls"].items() if c["status"] == "OK"]))
    if unread:
        t.detail("keywords unverifiable, as these publish no signature: %s" % ", ".join(unread))

t.complete()
