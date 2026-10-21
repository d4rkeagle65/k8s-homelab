"""
How each Ansible playbook picks its hosts and says where it runs (ansible/README.md, "Picking
the hosts"): a playbook that works on inventory hosts imports select-hosts.yml, so a run can
name its hosts in target_hosts, and its plays run on the group that picks; what runs on
Semaphore itself runs as the host semaphore, never localhost.

  no select-hosts.yml   a job with no way to limit it to some hosts, and no guard against an
                        inventory that parsed to nothing (the run exits 0 having done nothing)
  a play on its group   a play that ignores target_hosts and runs on every host anyway
  localhost             a log line that reads [localhost], not where the step ran
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _testcommon as t
from ruamel.yaml import YAML

# The standard's own parts.
PIECES = {"select-hosts.yml", "semaphore-host.yml", "require-hosts.yml"}
# Playbooks that pick their hosts another way, and why. The ones marked True still read
# target_hosts themselves.
EXEMPT = {
    "talos-upgrade.yml": (True, "its nodes come from the cluster, not the inventory"),
    "talos-reboot.yml": (True, "its VMs come from Proxmox and the cluster, not the inventory"),
    "kea-ha.yml": (False, "it names its pair by role, kea_primary and kea_standby"),
}

# The glob matches in subfolders too: keep the playbooks, the files directly in ansible/.
playbooks = [p for p in t.tracked_files("ansible/*.yml") if os.path.dirname(t.rel(p)) == "ansible"]
task_files = [p for p in t.tracked_files("ansible/tasks/*.yml") if os.path.dirname(t.rel(p)) == "ansible/tasks"]
t.scanned("AnsiblePlaybooks", len(playbooks), "Ansible playbooks")


def code_lines(text):
    """The lines that aren't comments."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def localhost_uses(text):
    """Each way a file runs something as localhost: a delegation, or reading its facts."""
    code = code_lines(text)
    return (re.findall(r"delegate_to:\s*['\"]?localhost\b", code)
            + re.findall(r"hostvars\[\s*['\"]localhost['\"]\s*\]", code))


def problems(name, text):
    """What's wrong with one playbook's host selection, as short descriptions."""
    found = []
    items = YAML(typ="safe").load(text) or []
    plays = [i for i in items if isinstance(i, dict) and "hosts" in i]
    imports = {i.get("ansible.builtin.import_playbook") or i.get("import_playbook")
               for i in items if isinstance(i, dict)} - {None}
    for play in plays:
        if play["hosts"] == "localhost" and name != "semaphore-host.yml":
            found.append("a play on localhost: %s" % play.get("name"))
    found += ["%s: run it as semaphore" % use for use in localhost_uses(text)]
    on_hosts = [p for p in plays if p["hosts"] not in ("semaphore", "localhost")]
    if name in PIECES or not on_hosts:
        return found
    if name in EXEMPT:
        if EXEMPT[name][0] and "target_hosts" not in code_lines(text):
            found.append("doesn't read target_hosts")
        return found
    if "select-hosts.yml" not in imports:
        found.append("doesn't import select-hosts.yml")
    found += ["a play on %s, not targets: %s" % (p["hosts"], p.get("name"))
              for p in on_hosts if p["hosts"] != "targets"]
    return found


t.section("the scanner, proven against sample text")
GOOD = ("- ansible.builtin.import_playbook: select-hosts.yml\n  vars: {select_from: pihole}\n"
        "- name: Work\n  hosts: targets\n  tasks: []\n"
        "- name: Report\n  hosts: semaphore\n  tasks: []\n")
t.check("SELF-TEST: a playbook keeping to the standard passes", problems("x.yml", GOOD), [])
t.check("  a play on its group instead of targets is CAUGHT",
        problems("x.yml", GOOD.replace("hosts: targets", "hosts: pihole")),
        ["a play on pihole, not targets: Work"])
t.check("  one without select-hosts.yml is CAUGHT",
        problems("x.yml", GOOD.replace("select-hosts.yml", "require-hosts.yml")),
        ["doesn't import select-hosts.yml"])
t.check("  a play on localhost is CAUGHT",
        problems("x.yml", GOOD.replace("hosts: semaphore", "hosts: localhost")),
        ["a play on localhost: Report"])
t.check("  a delegation to localhost is CAUGHT",
        len(localhost_uses("    - command: x\n      delegate_to: localhost\n")), 1)
t.check("  so is reading localhost's facts",
        len(localhost_uses("    kubectl_cmd: \"{{ hostvars['localhost'].kubectl_cmd }}\"\n")), 1)
t.check("  a comment naming localhost isn't",
        localhost_uses("# not delegate_to: localhost, as hostvars['localhost'] would\n"), [])
t.check("  semaphore-host.yml's own play on localhost passes",
        problems("semaphore-host.yml", "- name: Name it\n  hosts: localhost\n  tasks: []\n"), [])
t.check("  an exempt playbook that should read target_hosts and doesn't is CAUGHT",
        problems("talos-upgrade.yml", "- name: Work\n  hosts: talos_upgrade_nodes\n  tasks: []\n"),
        ["doesn't read target_hosts"])
t.check("  every exemption names a playbook here",
        sorted(set(EXEMPT) - {os.path.basename(p) for p in playbooks}), [])

t.section("every playbook picks its hosts through target_hosts")
for path in playbooks:
    name = os.path.basename(path)
    t.check(name, problems(name, t.source(path)), [])

t.section("tasks files run on Semaphore as semaphore")
for path in task_files:
    t.check(t.rel(path), localhost_uses(t.source(path)), [])

t.complete()
