"""netbox_guests.py: the shape it takes from NetBox's GraphQL answer, when it
refuses to write, and what a pull request lists. Documentation addresses only
(RFC 5737)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "netbox_guests.py"


def load():
    spec = importlib.util.spec_from_file_location("netbox_guests_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load()

CFG = {"vm_roles": {"controlplane": "Talos control plane", "worker": "Talos worker", "container": "Plain container",
                    "docker_vm": "Docker VM", "pihole": "Pi-hole"},
       "networks": [{"bridge": "vmbr7", "name": "Production", "vlan": 7}, {"bridge": "vmbr6", "name": "IoT", "vlan": 6}]}
PREFIXES = [{"prefix": "192.0.2.0/24", "description": "Production"},
            {"prefix": "198.51.100.0/24", "description": "IoT"}]


def vm(vmid, role, vcpus="2.00", memory=4096, disks=(("scsi0", 32000),), mode="no_reboot", status="active", **extra):
    cf = {"vmid": vmid, "talos_apply_mode": {"value": mode, "label": mode} if mode else None, "proxmox_tags": None}
    cf.update(extra.pop("cf", {}))
    return {"name": f"guest{vmid}", "status": status, "role": {"name": role} if role else None, "vcpus": vcpus,
            "memory": memory, "custom_fields": cf, "virtualdisks": [{"name": n, "size": s} for n, s in disks],
            "interfaces": extra.pop("interfaces", [])}


def answer(*vms):
    return {"virtual_machine_list": list(vms), "prefix_list": PREFIXES}


def test_talos_nodes_take_role_sizes_disks_and_apply_mode():
    out = m.shape(answer(vm(110, "Talos control plane"),
                         vm(114, "Talos worker", vcpus=6.0, memory=20480, disks=(("scsi0", 80000), ("scsi1", 128000)))), CFG)
    assert out["talos_nodes"] == {
        "110": {"role": "controlplane", "cores": 2, "memory": 4096, "disk_gb": 32, "data_gb": 0, "apply_mode": "no_reboot"},
        "114": {"role": "worker", "cores": 6, "memory": 20480, "disk_gb": 80, "data_gb": 128, "apply_mode": "no_reboot"},
    }
    assert out["containers"] == {} and out["docker_vms"] == {}


def test_a_container_takes_its_bridges_by_prefix_in_nic_order_and_its_tags():
    c = vm(120, "Plain container", vcpus=1, memory=512, disks=(("rootfs", 4000),), mode=None,
           cf={"proxmox_tags": "web; debian"},
           interfaces=[{"name": "net1", "ip_addresses": [{"address": "198.51.100.20/24"}]},
                       {"name": "net0", "ip_addresses": [{"address": "192.0.2.20/24"}]}])
    out = m.shape(answer(vm(110, "Talos control plane"), c), CFG)
    assert out["containers"] == {"120": {"cores": 1, "memory": 512, "disk_gb": 4,
                                         "bridges": ["vmbr7", "vmbr6"], "tags": ["debian", "web"]}}


def test_vms_without_an_owned_role_or_being_decommissioned_are_left_out():
    out = m.shape(answer(vm(110, "Talos control plane"), vm(107, None), vm(130, "Something else"),
                         vm(113, "Talos control plane", status="decommissioning")), CFG)
    assert list(out["talos_nodes"]) == ["110"]


def test_a_missing_field_refuses_the_whole_file():
    with pytest.raises(m.Refused, match="VMID 111"):
        m.shape(answer(vm(110, "Talos control plane"), vm(111, "Talos worker", mode=None)), CFG)
    with pytest.raises(m.Refused, match="VMID 112"):
        m.shape(answer(vm(110, "Talos control plane"), vm(112, "Talos worker", disks=())), CFG)
    with pytest.raises(m.Refused, match="not whole"):
        m.shape(answer(vm(110, "Talos control plane", vcpus="1.50")), CFG)
    no_net = vm(120, "Plain container", disks=(("rootfs", 4000),), interfaces=[{"name": "net0", "ip_addresses": []}])
    with pytest.raises(m.Refused, match="names no bridge"):
        m.shape(answer(vm(110, "Talos control plane"), no_net), CFG)


def test_no_talos_node_is_refused_never_written_as_none():
    with pytest.raises(m.Refused, match="no Talos node"):
        m.shape(answer(), CFG)


def test_the_output_is_stable_and_a_change_is_listed_by_vmid():
    a = m.shape(answer(vm(110, "Talos control plane"), vm(112, "Talos control plane")), CFG)
    b = m.shape(answer(vm(112, "Talos control plane"), vm(110, "Talos control plane")), CFG)
    assert m.render(a) == m.render(b) and m.render(a).endswith("}\n")
    bigger = json.loads(m.render(a))
    bigger["talos_nodes"]["112"]["memory"] = 8192
    bigger["talos_nodes"]["115"] = dict(bigger["talos_nodes"]["110"])
    del bigger["talos_nodes"]["110"]
    assert m.changes(a, bigger) == ["- talos_nodes 110: removed", "- talos_nodes 112: memory 4096 -> 8192",
                                    "- talos_nodes 115: added"]


def test_the_file_in_git_has_the_generators_layout():
    path = SCRIPT.parents[1] / m.OUTPUT
    text = path.read_text(encoding="utf-8")
    assert m.render(json.loads(text)) == text, f"{m.OUTPUT} is generated: run scripts/netbox_guests.py, never edit it"


def test_unexpected_arguments_are_named_never_shown():
    msg = m.unexpected(["--token=nbt_abc", "value"])
    assert "--token" in msg and "nbt_abc" not in msg


class FakeGitHub:
    """Answers open_pr's calls; records them."""

    def __init__(self, main_text, branch_exists=True, open_prs=()):
        self.main_text, self.branch_exists, self.open_prs, self.calls = main_text, branch_exists, list(open_prs), []

    def __call__(self, method, url, headers, body=None):
        import base64
        self.calls.append((method, url.split("/repos/o/r")[-1], body))
        path = url.split("/repos/o/r")[-1]
        if method == "GET" and path == "/git/ref/heads/main":
            return 200, {"object": {"sha": "main-sha"}}
        if method == "GET" and path.startswith("/contents/"):
            if self.main_text is None:
                return 404, {"message": "Not Found"}
            return 200, {"sha": "file-sha", "content": base64.b64encode(self.main_text.encode()).decode()}
        if method == "PATCH" and path.startswith("/git/refs/heads/"):
            return (200, {}) if self.branch_exists else (422, {"message": "Reference does not exist"})
        if method == "POST" and path == "/git/refs":
            return 201, {}
        if method == "PUT":
            return 200, {}
        if method == "GET" and path.startswith("/pulls"):
            return 200, self.open_prs
        if method == "POST" and path == "/pulls":
            return 201, {"number": 7}
        if method == "PATCH" and path.startswith("/pulls/"):
            return 200, {}
        return 500, {"message": "unexpected call"}


def test_a_changed_file_moves_the_branch_to_main_commits_it_and_opens_a_pull_request(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    old = m.render(m.shape(answer(vm(110, "Talos control plane")), CFG))
    new = m.render(m.shape(answer(vm(110, "Talos control plane", memory=8192)), CFG))
    gh = FakeGitHub(old, branch_exists=False)
    monkeypatch.setattr(m, "call", gh)
    m.open_pr(new, "ghp_secret")
    methods = [(c[0], c[1].split("?")[0]) for c in gh.calls]
    assert ("POST", "/git/refs") in methods and ("POST", "/pulls") in methods
    put = next(c[2] for c in gh.calls if c[0] == "PUT")
    assert put["branch"] == "netbox/guests" and put["sha"] == "file-sha"
    out = capsys.readouterr().out
    assert "opened pull request #7" in out and "memory 4096 -> 8192" in out and "ghp_secret" not in out


def test_an_open_pull_request_is_updated_and_an_unchanged_file_opens_nothing(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    text = m.render(m.shape(answer(vm(110, "Talos control plane")), CFG))
    gh = FakeGitHub(text)
    monkeypatch.setattr(m, "call", gh)
    m.open_pr(text, "t")
    assert [c[0] for c in gh.calls] == ["GET", "GET"] and "no pull request needed" in capsys.readouterr().out
    gh = FakeGitHub(None, open_prs=[{"number": 3}])
    monkeypatch.setattr(m, "call", gh)
    m.open_pr(text, "t")
    put = next(c[2] for c in gh.calls if c[0] == "PUT")
    assert "sha" not in put and "updated pull request #3" in capsys.readouterr().out


def test_a_docker_vm_takes_bridges_from_its_nic_descriptions_and_both_disks():
    d = vm(107, "Docker VM", vcpus="4.00", memory=12288, disks=(("scsi0", 32000), ("scsi1", 64000)), mode=None,
           cf={"proxmox_tags": "debian13;community-script"},
           interfaces=[{"name": "net2", "description": "Proxmox bridge vmbr8", "ip_addresses": []},
                       {"name": "net0", "description": "Proxmox bridge vmbr0", "ip_addresses": []},
                       {"name": "net1", "description": "Proxmox bridge vmbr7", "ip_addresses": [{"address": "192.0.2.40/24"}]}])
    planned = vm(120, "Docker VM", memory=8192, disks=(("scsi0", 96000),), mode=None, status="planned",
                 interfaces=[{"name": "net0", "description": "", "ip_addresses": [{"address": "192.0.2.41/24"}]}])
    out = m.shape(answer(vm(110, "Talos control plane"), d, planned), CFG)
    assert out["docker_vms"] == {
        "107": {"cores": 4, "memory": 12288, "disk_gb": 32, "data_gb": 64,
                "bridges": ["vmbr0", "vmbr7", "vmbr8"], "tags": ["community-script", "debian13"]},
        "120": {"cores": 2, "memory": 8192, "disk_gb": 96, "data_gb": 0, "bridges": ["vmbr7"], "tags": []},
    }


def test_a_docker_vm_without_its_system_disk_refuses_the_file():
    d = vm(107, "Docker VM", disks=(), mode=None, interfaces=[{"name": "net0", "description": "Proxmox bridge vmbr0"}])
    with pytest.raises(m.Refused, match="VMID 107: needs a virtual disk scsi0"):
        m.shape(answer(vm(110, "Talos control plane"), d), CFG)


def test_a_pihole_takes_its_rootfs_its_bridges_in_nic_order_and_its_tags_in_a_group_of_its_own():
    nics = [{"name": f"net{n}", "description": f"Proxmox bridge {b}", "ip_addresses": []}
            for n, b in reversed(list(enumerate(["vmbr7", "vmbr0", "vmbr2", "vmbr6", "vmbr8"])))]
    p = vm(100, "Pi-hole", memory=2048, disks=(("rootfs", 8000),), mode=None,
           cf={"proxmox_tags": "community-script;adblock"}, interfaces=nics)
    out = m.shape(answer(vm(110, "Talos control plane"), p), CFG)
    assert out["piholes"] == {"100": {"cores": 2, "memory": 2048, "disk_gb": 8,
                                      "bridges": ["vmbr7", "vmbr0", "vmbr2", "vmbr6", "vmbr8"],
                                      "tags": ["adblock", "community-script"]}}
    assert out["containers"] == {}
    with pytest.raises(m.Refused, match="VMID 109: needs a virtual disk rootfs"):
        m.shape(answer(vm(110, "Talos control plane"), vm(109, "Pi-hole", disks=(("scsi0", 8000),), mode=None,
                                                          interfaces=nics)), CFG)
