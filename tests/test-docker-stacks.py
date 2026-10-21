"""
Each Docker stack's .env (docker/<stack>/), the list Dockhand's Populate reads: a stack whose
compose.yaml uses variables has one, it names exactly those variables, and every value is
blank or the variable's own Key Vault reference, azurekv://<NAME with - for _>, naming a
secret docker/secret-names.yaml gives; Dockhand resolves those at each deploy
(docker/CLAUDE.md). Anything else is a value.

  missing .env        Populate lists nothing, and a variable is easy to forget in Dockhand
  missing or extra    the list drifted from compose.yaml
  a value             a real value one commit away from a public repo
  another reference   a variable reading a secret meant for another, or one no map gives
  no mem_limit        a long-running service that can take the whole host's memory with it
  proxies differ      a Docker socket proxy allowing what another host's doesn't
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


def map_names(text):
    """Every Key Vault name docker/secret-names.yaml gives a stack: each NAME entry, and each
    ${NAME} a built form reads."""
    names = set()
    for variables in (YAML(typ="safe").load(text) or {}).values():
        for entry in (variables or {}).values():
            if isinstance(entry, str):
                names.add(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("built"), str):
                names.update(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", entry["built"]))
    return names


def not_allowed(entries, known):
    """The names whose value is neither blank nor their own Key Vault reference to a name the
    map gives."""
    return sorted(name for name, value in entries.items()
                  if value.strip() and not (value == "azurekv://" + name.replace("_", "-") and name in known))


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
t.check("SELF-TEST: a blank value and a variable's own reference pass",
        not_allowed({"A_B": "", "A_B2": "azurekv://A-B2"}, {"A_B2"}), [])
t.check("  a real value is CAUGHT", not_allowed({"A": "secret"}, {"A"}), ["A"])
t.check("  a reference to another name is CAUGHT", not_allowed({"A_B": "azurekv://C-D"}, {"A_B", "C_D"}), ["A_B"])
t.check("  a reference to a name no map gives is CAUGHT", not_allowed({"A_B": "azurekv://A-B"}, set()), ["A_B"])
t.check("  so is one with anything around it", not_allowed({"A": "'azurekv://A' "}, {"A"}), ["A"])
t.check("SELF-TEST: the map's names are its entries and its built forms' ${NAME}s",
        sorted(map_names('s:\n  X: N1\n  Y: {built: "https://a.${N2}/${N3}"}\n  Z: {none: "blank"}\n')), ["N1", "N2", "N3"])
t.check("SELF-TEST: a running service without mem_limit is CAUGHT",
        unlimited_services("services:\n  a:\n    restart: always\n"), ["a"])
t.check("  one with mem_limit passes", unlimited_services("services:\n  a:\n    restart: always\n    mem_limit: 1g\n"), [])
t.check("  a one-shot step needs none", unlimited_services("services:\n  a:\n    restart: \"no\"\n"), [])
t.check("  a limit inherited through an anchor counts",
        unlimited_services("x: &d\n  mem_limit: 1g\nservices:\n  a:\n    <<: *d\n    restart: always\n"), [])

t.section("every stack's .env")
known = map_names(t.source(t.project_file("secret-names.yaml")))
t.check("the names map gives some names", len(known) > 0, True)
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
    t.check("  every value is blank or its own Key Vault reference", not_allowed(entries, known), [])

def socket_proxies(texts):
    """{folder: (image, command)} for each wollomatic/socket-proxy service: the stacks that
    give Homepage a read-only Docker view (docker/docker-socket-proxy*), which differ only in
    the address they publish on."""
    found = {}
    for folder, text in texts.items():
        for name, svc in ((YAML(typ="safe").load(text) or {}).get("services") or {}).items():
            image = str((svc or {}).get("image", ""))
            if image.split(":")[0] == "wollomatic/socket-proxy":
                found[folder] = (image, tuple((svc or {}).get("command") or []))
    return found


t.check("SELF-TEST: a proxy allowing more than another is CAUGHT",
        len(set(socket_proxies({
            "a": "services:\n  p:\n    image: wollomatic/socket-proxy:1\n    command: [-allowGET=x]\n",
            "b": "services:\n  p:\n    image: wollomatic/socket-proxy:1\n    command: [-allowGET=.*]\n",
        }).values())), 2)

t.section("the Docker socket proxies allow the same requests")
proxies = socket_proxies({os.path.dirname(t.rel(c)): t.source(c) for c in stacks})
t.check("there are socket-proxy stacks to compare", len(proxies) > 0, True)
t.check("  every one has the same image and command",
        sorted(f for f in proxies if proxies[f] != proxies.get("docker/docker-socket-proxy")), [])

t.section("every long-running service has a memory limit")
for compose in stacks:
    t.check("%s: every running service has mem_limit" % os.path.dirname(t.rel(compose)),
            unlimited_services(t.source(compose)), [])

t.complete()
