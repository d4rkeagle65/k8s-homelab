"""
Each Docker stack's .env (docker/<stack>/), the blank list Dockhand's Populate reads: a stack
whose compose.yaml uses variables has one, it names exactly those variables, and every value
is blank, since the real ones live only in Dockhand (docker/CLAUDE.md).

  missing .env        Populate lists nothing, and a variable is easy to forget in Dockhand
  missing or extra    the list drifted from compose.yaml
  a value             a real value one commit away from a public repo
  no mem_limit        a long-running service that can take the whole host's memory with it
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _testcommon as t
from ruamel.yaml import YAML

stacks = t.tracked_files("docker/*/compose.yaml")
t.scanned("DockerStacks", len(stacks), "Docker stacks' compose files")
envs = {t.rel(f): f for f in t.tracked_files("docker/*/.env")}


# Scanners, as functions so the self-test can feed them text instead of writing files.
def compose_variables(text):
    """The variables a compose file substitutes: ${NAME}, ${NAME:-default}, $NAME. Compose
    reads $$ as a literal dollar, and a comment line substitutes nothing."""
    names = set()
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        line = line.replace("$$", "")
        names.update(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", line))
        names.update(re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", line))
    return names


def env_entries(text):
    """{name: value} for each NAME=VALUE line, and the lines that are neither that, a
    comment nor blank."""
    entries, bad = {}, []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m:
            entries[m.group(1)] = m.group(2)
        else:
            bad.append(line)
    return entries, bad


def unlimited_services(text):
    """The services that keep running (restart isn't "no") without a mem_limit. Without
    one, a single runaway container can push the whole host into its OOM killer."""
    services = (YAML(typ="safe").load(text) or {}).get("services") or {}
    return sorted(name for name, svc in services.items()
                  if str((svc or {}).get("restart", "no")) != "no" and "mem_limit" not in (svc or {}))


t.section("the scanners, proven against sample text")
t.check("SELF-TEST: ${NAME} is a variable", compose_variables("a: ${HOST}"), {"HOST"})
t.check("  so is ${NAME:-default}", compose_variables("a: ${TZ:-UTC}"), {"TZ"})
t.check("  so is $NAME", compose_variables("a: $PORT"), {"PORT"})
t.check("  $$NAME is a literal, not one", compose_variables('c: "[ -n "$$PW" ]"'), set())
t.check("  a comment line names none", compose_variables("# set ${HOST} in Dockhand"), set())
t.check("SELF-TEST: a blank entry", env_entries("# x\nA=\n\nB=\n"), ({"A": "", "B": ""}, []))
t.check("  a value is read as one", env_entries("A=secret\n")[0], {"A": "secret"})
t.check("  a line that's neither is reported", env_entries("A\n")[1], ["A"])
t.check("SELF-TEST: a running service without mem_limit is CAUGHT",
        unlimited_services("services:\n  a:\n    restart: always\n"), ["a"])
t.check("  one with mem_limit passes", unlimited_services("services:\n  a:\n    restart: always\n    mem_limit: 1g\n"), [])
t.check("  a one-shot step needs none", unlimited_services("services:\n  a:\n    restart: \"no\"\n"), [])
t.check("  a limit inherited through an anchor counts",
        unlimited_services("x: &d\n  mem_limit: 1g\nservices:\n  a:\n    <<: *d\n    restart: always\n"), [])

t.section("every stack's .env")
for compose in stacks:
    folder = os.path.dirname(t.rel(compose))
    used = compose_variables(t.source(compose))
    env = folder + "/.env"
    if not used:
        t.check("%s uses no variables, so needs no .env" % folder, True, True)
        continue
    t.check("%s has a .env" % folder, env in envs, True)
    if env not in envs:
        continue
    entries, bad = env_entries(t.source(envs[env]))
    t.check("  every line is NAME=, a comment or blank", bad, [])
    t.check("  it lists every variable compose.yaml uses", sorted(used - set(entries)), [])
    t.check("  and none that compose.yaml doesn't", sorted(set(entries) - used), [])
    t.check("  every value is blank", sorted(n for n, v in entries.items() if v.strip()), [])

t.section("every long-running service has a memory limit")
for compose in stacks:
    t.check("%s: every running service has mem_limit" % os.path.dirname(t.rel(compose)),
            unlimited_services(t.source(compose)), [])

t.complete()
