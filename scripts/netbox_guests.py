"""Writes the shape NetBox owns for the Talos VMs, plain containers, Docker VMs
and Pi-holes into
tofu/proxmox/netbox-guests.json, and with --pr opens a pull request for it:
QUEUE-netbox-shape.md.

A VM is in the file when its NetBox role is one of netbox_seed.yaml's vm_roles.
For each, by VMID: the Talos VMs' role, cores, memory, disk_gb (virtual disk
scsi0), data_gb (scsi1, else 0) and apply_mode (custom field talos_apply_mode);
the plain containers' and Pi-holes' cores, memory, disk_gb (rootfs), bridges and
tags; the Docker VMs' cores, memory, disk_gb (scsi0), data_gb (scsi1, else 0), bridges and
tags. A NIC's bridge is its description's ("Proxmox bridge vmbr7"), else its
address's network's (by its prefix, to that network's bridge in netbox_seed.yaml);
tags are the custom field proxmox_tags, separated by ;. Never a name or an
address: OpenTofu reads those from NetBox at plan time.

It stops, writing and opening nothing, when NetBox doesn't answer in full, lists
no Talos node, or a VM with a role lacks a field. The output is sorted, so a run
with nothing changed writes the same bytes and opens nothing.

--pr, when the file differs from main's: points the branch netbox/guests at main,
commits the file there and opens a pull request, or updates the one already open,
all through GitHub's API. The repository is GITHUB_REPOSITORY ("owner/name"), else
read from git's origin.

Credentials are never arguments: NETBOX_TOKEN (a read-only NetBox token) and,
for --pr, GITHUB_TOKEN (Contents and Pull requests on this repository), each from
the environment. NetBox's address is NETBOX_API.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = "tofu/proxmox/netbox-guests.json"
BRANCH = "netbox/guests"
QUERY = ("{ virtual_machine_list { name status role { name } vcpus memory custom_fields"
         " virtualdisks { name size } interfaces { name description ip_addresses { address } } }"
         " prefix_list { prefix description } }")


class Refused(Exception):
    """NetBox's answer can't be turned into the file; nothing is written."""


def unexpected(extras: list[str]) -> str:
    """Names unknown arguments without their values (a value may be a token)."""
    names = sorted({e.split("=", 1)[0] for e in extras if e.startswith("-")}) or ["(values only)"]
    return f"FAIL: unexpected arguments: {', '.join(names)}; their values aren't shown"


def number(value) -> int:
    """NetBox's decimals (vCPUs) arrive as strings or floats; the file holds whole numbers."""
    n = float(value)
    if n != int(n):
        raise ValueError(f"{value} isn't a whole number")
    return int(n)


def bridge_of(address: str, prefixes: list[tuple], bridges: dict[str, str]) -> str | None:
    """The bridge an address is on: its prefix's network (the prefix's description,
    as the seed writes it) to that network's bridge."""
    import ipaddress
    ip = ipaddress.ip_interface(address).ip
    best = None
    for net, name in prefixes:
        if ip in net and (best is None or net.prefixlen > best[0].prefixlen):
            best = (net, name)
    return bridges.get(best[1]) if best else None


def interface_bridge(itf: dict, prefixes: list[tuple], bridges: dict[str, str]) -> str | None:
    """A NIC's bridge: from its description ("Proxmox bridge vmbr7", as the seed and
    a planned guest's entry write it), else from its address's network. None when
    neither says, or the addresses disagree."""
    m = re.fullmatch(r"Proxmox bridge (\S+)", (itf.get("description") or "").strip())
    if m:
        return m.group(1)
    found = {bridge_of(a["address"], prefixes, bridges) for a in itf.get("ip_addresses") or []}
    found.discard(None)
    return found.pop() if len(found) == 1 else None


def shape(data: dict, cfg: dict) -> dict:
    """The file's content from NetBox's GraphQL answer; Refused when it's incomplete."""
    import ipaddress
    roles = {name: key for key, name in cfg["vm_roles"].items()}
    bridges = {n["name"]: n["bridge"] for n in cfg["networks"]}
    prefixes = [(ipaddress.ip_network(p["prefix"]), p.get("description") or "") for p in data["prefix_list"]]
    talos, containers, docker_vms, piholes, problems = {}, {}, {}, {}, []
    for vm in data["virtual_machine_list"]:
        role = roles.get((vm.get("role") or {}).get("name"))
        if role is None or str(vm.get("status", "")).lower() == "decommissioning":
            continue
        cf = vm.get("custom_fields") or {}
        label = f"VMID {cf.get('vmid')}" if cf.get("vmid") is not None else "a VM with no vmid"
        disks = {d["name"]: d["size"] for d in vm.get("virtualdisks") or []}
        mode = cf.get("talos_apply_mode")
        mode = mode.get("value") if isinstance(mode, dict) else mode  # a select arrives as {value, label}
        try:
            vmid = str(int(cf["vmid"]))
            entry = {"cores": number(vm["vcpus"]), "memory": number(vm["memory"])}
        except (KeyError, TypeError, ValueError) as e:
            problems.append(f"{label}: vmid, vCPUs or memory missing or not whole ({type(e).__name__})")
            continue
        if role in ("controlplane", "worker"):
            if "scsi0" not in disks or mode not in ("auto", "no_reboot"):
                problems.append(f"{label}: needs a virtual disk scsi0 and talos_apply_mode auto or no_reboot")
                continue
            entry.update(role=role, disk_gb=round(disks["scsi0"] / 1000), data_gb=round(disks.get("scsi1", 0) / 1000),
                         apply_mode=mode)
            talos[vmid] = entry
        else:
            nets = []
            for itf in sorted(vm.get("interfaces") or [], key=lambda i: (len(i["name"]), i["name"])):
                bridge = interface_bridge(itf, prefixes, bridges)
                if bridge is None:
                    problems.append(f"{label}: interface {itf['name']} names no bridge and isn't on one listed network")
                    break
                nets.append(bridge)
            else:
                tags = sorted(t.strip() for t in str(cf.get("proxmox_tags") or "").split(";") if t.strip())
                root = "scsi0" if role == "docker_vm" else "rootfs"
                if root not in disks or not nets:
                    problems.append(f"{label}: needs a virtual disk {root} and at least one interface")
                    continue
                entry.update(disk_gb=round(disks[root] / 1000), bridges=nets, tags=tags)
                if role == "container":
                    containers[vmid] = entry
                elif role == "pihole":
                    piholes[vmid] = entry
                else:
                    entry["data_gb"] = round(disks.get("scsi1", 0) / 1000)
                    docker_vms[vmid] = entry
    if problems:
        raise Refused("; ".join(sorted(problems)))
    if not talos:
        raise Refused("NetBox lists no Talos node with a role: refusing to write a file that would drop them all")
    return {"talos_nodes": talos, "containers": containers, "docker_vms": docker_vms, "piholes": piholes}


def render(content: dict) -> str:
    return json.dumps(content, indent=2, sort_keys=True) + "\n"


def changes(old: dict, new: dict) -> list[str]:
    """What a pull request's description lists: each VMID added, removed or changed."""
    lines = []
    for group in ("talos_nodes", "containers", "docker_vms", "piholes"):
        a, b = old.get(group) or {}, new.get(group) or {}
        for vmid in sorted(set(a) | set(b), key=int):
            if vmid not in a:
                lines.append(f"- {group} {vmid}: added")
            elif vmid not in b:
                lines.append(f"- {group} {vmid}: removed")
            elif a[vmid] != b[vmid]:
                diff = ", ".join(f"{k} {a[vmid].get(k)} -> {b[vmid].get(k)}"
                                 for k in sorted(set(a[vmid]) | set(b[vmid])) if a[vmid].get(k) != b[vmid].get(k))
                lines.append(f"- {group} {vmid}: {diff}")
    return lines


def call(method: str, url: str, headers: dict, body=None):
    """An HTTP call: (status, parsed JSON or None). A refused call is an answer, not an error."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={**headers, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None


def netbox(url: str, token: str) -> dict:
    token = token.strip().removeprefix("Bearer ")
    status, body = call("POST", url.rstrip("/") + "/graphql/",
                        {"Authorization": f"Bearer {token}", "Accept": "application/json"}, {"query": QUERY})
    if status != 200 or not isinstance(body, dict) or body.get("errors") or "data" not in body:
        detail = (body or {}).get("errors") if isinstance(body, dict) else None
        raise Refused(f"NetBox's GraphQL didn't answer in full (HTTP {status}{': ' + str(detail)[:300] if detail else ''})")
    return body["data"]


def repository() -> str:
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    origin = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"],
                            capture_output=True, text=True, encoding="utf-8").stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", origin)
    if not m:
        raise Refused("can't tell the GitHub repository: set GITHUB_REPOSITORY (owner/name)")
    return m.group(1)


def open_pr(text: str, token: str) -> None:
    repo = repository()
    api = f"https://api.github.com/repos/{repo}"
    h = {"Authorization": f"Bearer {token.strip()}", "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}

    def must(status_body, wanted, what):
        status, body = status_body
        if status not in wanted:
            message = body.get("message") if isinstance(body, dict) else ""
            raise Refused(f"GitHub refused {what} (HTTP {status}: {message})")
        return body

    main = must(call("GET", f"{api}/git/ref/heads/main", h), (200,), "reading main")["object"]["sha"]
    status, current = call("GET", f"{api}/contents/{OUTPUT}?ref=main", h)
    if status not in (200, 404):
        must((status, current), (200,), f"reading {OUTPUT} on main")
    # The branch starts at main, so main's copy is the one the commit replaces.
    current_sha = current["sha"] if status == 200 else None
    old_text = base64.b64decode(current["content"]).decode("utf-8") if status == 200 else ""
    if old_text == text:
        print(f"main's {OUTPUT} matches NetBox: no pull request needed")
        return
    old = json.loads(old_text) if old_text else {}
    summary = changes(old, json.loads(text)) or ["- the file's layout only"]

    moved, _ = call("PATCH", f"{api}/git/refs/heads/{BRANCH}", h, {"sha": main, "force": True})
    if moved in (404, 422):  # no such branch yet
        must(call("POST", f"{api}/git/refs", h, {"ref": f"refs/heads/{BRANCH}", "sha": main}), (201,), f"creating {BRANCH}")
    elif moved != 200:
        raise Refused(f"GitHub refused moving {BRANCH} to main (HTTP {moved})")
    put = {"message": "netbox: the guests' shape from NetBox", "branch": BRANCH,
           "content": base64.b64encode(text.encode("utf-8")).decode()}
    if current_sha:
        put["sha"] = current_sha
    must(call("PUT", f"{api}/contents/{OUTPUT}", h, put), (200, 201), f"committing {OUTPUT} to {BRANCH}")

    body = ("The shape NetBox holds for the Talos VMs, plain containers, Docker VMs and Pi-holes, generated by "
            "`scripts/netbox_guests.py` (QUEUE-netbox-shape.md). Merging changes nothing in Proxmox "
            "until an OpenTofu apply is run.\n\n" + "\n".join(summary))
    owner = repo.split("/")[0]
    found = must(call("GET", f"{api}/pulls?state=open&head={urllib.parse.quote(owner + ':' + BRANCH)}", h), (200,), "listing pull requests")
    if found:
        must(call("PATCH", f"{api}/pulls/{found[0]['number']}", h, {"body": body}), (200,), "updating the pull request")
        print(f"updated pull request #{found[0]['number']}")
    else:
        pr = must(call("POST", f"{api}/pulls", h, {"title": "NetBox: guests' shape", "head": BRANCH, "base": "main",
                                                   "body": body}), (201,), "opening the pull request")
        print(f"opened pull request #{pr['number']}")
    print("\n".join(summary))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default=str(ROOT / "scripts" / "netbox_seed.yaml"), help="vm_roles and networks")
    ap.add_argument("--output", default=str(ROOT / OUTPUT), help=f"where to write (default {OUTPUT})")
    ap.add_argument("--pr", action="store_true", help="open or update the pull request when main's file differs")
    args, extras = ap.parse_known_args()
    if extras:
        raise SystemExit(unexpected(extras))
    url, token = os.environ.get("NETBOX_API", ""), os.environ.get("NETBOX_TOKEN", "")
    if not url or not token:
        raise SystemExit("FAIL: set NETBOX_API and NETBOX_TOKEN (a read-only NetBox token)")
    if args.pr and not os.environ.get("GITHUB_TOKEN"):
        raise SystemExit("FAIL: --pr needs GITHUB_TOKEN; nothing was written")
    try:
        cfg = YAML(typ="safe").load(Path(args.config).read_text(encoding="utf-8"))
        text = render(shape(netbox(url, token), cfg))
        Path(args.output).write_text(text, encoding="utf-8", newline="\n")
        content = json.loads(text)
        print(f"wrote {args.output}: {len(content['talos_nodes'])} Talos nodes, {len(content['containers'])} containers, "
              f"{len(content['docker_vms'])} Docker VMs, {len(content['piholes'])} Pi-holes")
        if args.pr:
            open_pr(text, os.environ["GITHUB_TOKEN"])
    except (Refused, urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", None) or e
        raise SystemExit(f"FAIL: {reason}; nothing was opened")


if __name__ == "__main__":
    main()
