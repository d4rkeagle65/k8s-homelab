"""scripts/keyvault_facts.py: a Key Vault name takes its value only from the one NetBox object
claiming it, and only one whose status lets it be used; --write sets what differs and reads it
back; --find reports which secrets equal or contain which NetBox object's forms; no run ever
prints a value.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("keyvault_facts", ROOT / "scripts" / "keyvault_facts.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

ENV = {"AZURE_TENANT_ID": "t", "AZURE_CLIENT_ID": "reader", "AZURE_CLIENT_SECRET": "s",
       "AZURE_KEYVAULT_URL": "https://vault.example", "NETBOX_URL": "https://netbox.example",
       "NETBOX_TOKEN": "nbt_x"}
WRITER = {"FACTS_AZURE_CLIENT_ID": "writer", "FACTS_AZURE_CLIENT_SECRET": "w"}


def nb_claims(claims):
    """A multiple selection as NetBox's API gives it."""
    return [{"value": c, "label": c} for c in claims]


def ip(id_, address, dns="", status="active", claims=(), vm=None):
    obj = {"id": id_, "address": address, "dns_name": dns, "status": {"value": status},
           "custom_fields": {m.FIELD: nb_claims(claims)}, "assigned_object": None}
    if vm is not None:
        obj["assigned_object"] = {"virtual_machine": {"id": vm}}
    return obj


class Fake:
    """Azure's login, Key Vault (list, get, put) and NetBox's API, recording each write."""

    def __init__(self, vault, ips=(), ranges=(), prefixes=(), vms=None, field=True, choices=None,
                 put_drops=False, vm_list=(), devices=(), records=()):
        self.vault, self.writes, self.put_drops = dict(vault), [], put_drops
        self.tags, self.version, self.reads, self.tagged = {}, {n: 1 for n in vault}, [], []
        self.nb = {"ipam/ip-addresses": list(ips), "ipam/ip-ranges": list(ranges), "ipam/prefixes": list(prefixes),
                   "virtualization/virtual-machines": list(vm_list), "dcim/devices": list(devices),
                   "plugins/netbox-dns/records": list(records)}
        self.vms, self.field, self.choices = vms or {}, field, choices

    def http(self, method, url, headers=None, body=None, form=None, timeout=30):
        if "login.microsoftonline.com" in url:
            return 200, {"access_token": form["client_id"]}
        if url.startswith("https://vault.example/secrets?"):
            return 200, {"value": [{"id": f"https://vault.example/secrets/{n}", "attributes": {"enabled": True},
                                    "tags": self.tags.get(n, {})} for n in self.vault]}
        if url.startswith("https://vault.example/secrets/"):
            name, _, version = url.split("/secrets/")[1].split("?")[0].partition("/")
            if method in ("PUT", "PATCH") and headers["Authorization"] != "Bearer writer":
                return 403, None
            if method == "PUT":
                self.writes.append(name)
                if not self.put_drops:
                    # A new version carries only the tags it's given, as in Key Vault.
                    self.vault[name], self.tags[name] = body["value"], dict(body.get("tags") or {})
                    self.version[name] = self.version.get(name, 0) + 1
                return 200, {}
            if method == "PATCH":
                if version != f"v{self.version[name]}":
                    return 404, None
                self.tagged.append(name)
                self.tags[name] = dict(body["tags"])
                return 200, {}
            self.reads.append(name)
            if name not in self.vault:
                return 404, None
            return 200, {"value": self.vault[name], "id": f"https://vault.example/secrets/{name}/v{self.version[name]}",
                         "tags": self.tags.get(name, {})}
        path = url.split("/api/", 1)[1]
        if path.startswith("extras/custom-fields/"):
            return 200, {"results": [{"name": m.FIELD, "choice_set": {"id": 1}}] if self.field else []}
        if path.startswith("extras/custom-field-choice-sets/"):
            return 200, {"extra_choices": [[c, c] for c in (self.choices or [])]}
        tail = path.split("/?")[0].rstrip("/").rsplit("/", 1)[-1]
        if path.startswith("virtualization/virtual-machines/") and tail.isdigit():
            return 200, {"id": int(tail), "status": {"value": self.vms[int(tail)]}}
        base = path.split("/?")[0]
        return 200, {"results": self.nb[base], "next": None}


def go(monkeypatch, tmp_path, capsys, fake, facts, args=(), env=ENV):
    mapfile = tmp_path / "map.yaml"
    mapfile.write_text("facts:\n" + "".join(f"  {k}: {v}\n" for k, v in facts.items()) if facts else "facts: {}\n")
    monkeypatch.setattr(m, "http", fake.http)
    monkeypatch.setattr(sys.modules["semaphore_variable_groups"], "http", fake.http)
    for k in list(ENV) + list(WRITER) + ["KEYVAULT_FACTS"]:
        monkeypatch.delenv(k, raising=False)
    for k, v in {**env, "KEYVAULT_FACTS": str(mapfile)}.items():
        monkeypatch.setenv(k, v)
    rc = m.main(list(args))
    return rc, capsys.readouterr().out


VALUES = ["192.0.2.20", "nas.lan.example", "198.51.100.200-198.51.100.220"]


def no_values(out):
    for v in VALUES + ["192.0.2.21", "192.0.2.30"]:
        assert v not in out, v


def test_find_reports_what_equals_or_contains_which_object_and_never_a_value(monkeypatch, tmp_path, capsys):
    fake = Fake({"PIHOLE-ADDRESS": "192.0.2.20\n", "PIHOLE-URL": "http://192.0.2.20/admin",
                 "NAS-HOSTNAME": "nas.lan.example", "POOL": "198.51.100.200-198.51.100.220",
                 "NEAR": "192.0.2.201", "A-TOKEN": "<x>"},
                ips=[ip(7, "192.0.2.20/24"), ip(8, "192.0.2.30/24", dns="nas.lan.example", status="deprecated")],
                ranges=[{"id": 3, "start_address": "198.51.100.200/24", "end_address": "198.51.100.220/24",
                         "status": {"value": "active"}, "custom_fields": {}}])
    rc, out = go(monkeypatch, tmp_path, capsys, fake, {}, ["--find"])
    assert rc == 0
    assert "PIHOLE-ADDRESS: equals address of IP address #7 (active) (after trimming whitespace)" in out
    assert "PIHOLE-URL: contains address of IP address #7 (active)" in out
    assert "NAS-HOSTNAME: equals dns_name of IP address #8 (deprecated)" in out
    assert "POOL: equals range of IP range #3 (active)" in out
    assert "NEAR" not in out  # 192.0.2.201 is another address, not one containing .20
    assert "2 match nothing in NetBox" in out
    assert fake.writes == []
    no_values(out)


def test_each_name_takes_its_one_usable_source_and_reports_agreement(monkeypatch, tmp_path, capsys):
    fake = Fake({"PIHOLE-ADDRESS": "192.0.2.20", "NAS-HOSTNAME": "old.example", "POOL": "198.51.100.200-198.51.100.220"},
                ips=[ip(7, "192.0.2.20/24", claims=["PIHOLE-ADDRESS"], vm=1),
                     ip(8, "192.0.2.30/24", dns="nas.lan.example", claims=["NAS-HOSTNAME"])],
                ranges=[{"id": 3, "start_address": "198.51.100.200/24", "end_address": "198.51.100.220/24",
                         "status": {"value": "active"}, "custom_fields": {m.FIELD: nb_claims(["POOL"])}}],
                vms={1: "active"}, choices=["PIHOLE-ADDRESS", "NAS-HOSTNAME", "POOL", "PIHOLE-URL"])
    facts = {"PIHOLE-ADDRESS": "{take: address}", "NAS-HOSTNAME": "{take: dns_name}", "POOL": "{take: range}",
             "PIHOLE-URL": '{template: "http://{PIHOLE-ADDRESS}/admin"}'}
    rc, out = go(monkeypatch, tmp_path, capsys, fake, facts)
    assert rc == 0
    assert "PIHOLE-ADDRESS: agrees (address of IP address #7)" in out
    assert "NAS-HOSTNAME: differs (dns_name of IP address #8)" in out
    assert "POOL: agrees (range of IP range #3)" in out
    assert "PIHOLE-URL: not in Key Vault (its template on PIHOLE-ADDRESS)" in out
    assert fake.writes == []
    no_values(out)


def test_write_sets_what_differs_with_the_writer_and_reads_it_back(monkeypatch, tmp_path, capsys):
    fake = Fake({"NAS-HOSTNAME": "old.example", "PIHOLE-ADDRESS": "192.0.2.20"},
                ips=[ip(7, "192.0.2.20/24", claims=["PIHOLE-ADDRESS"]),
                     ip(8, "192.0.2.30/24", dns="nas.lan.example", claims=["NAS-HOSTNAME"])],
                choices=["PIHOLE-ADDRESS", "NAS-HOSTNAME"])
    facts = {"PIHOLE-ADDRESS": "{take: address}", "NAS-HOSTNAME": "{take: dns_name}"}
    rc, out = go(monkeypatch, tmp_path, capsys, fake, facts, ["--write"], {**ENV, **WRITER})
    assert rc == 0
    assert fake.writes == ["NAS-HOSTNAME"]
    assert fake.vault["NAS-HOSTNAME"] == "nas.lan.example"
    assert "set, and reads back the same" in out
    no_values(out)


def test_a_write_that_doesnt_take_fails_the_run(monkeypatch, tmp_path, capsys):
    fake = Fake({"NAS-HOSTNAME": "old.example"}, ips=[ip(8, "192.0.2.30/24", dns="nas.lan.example",
                                                         claims=["NAS-HOSTNAME"])],
                choices=["NAS-HOSTNAME"], put_drops=True)
    rc, out = go(monkeypatch, tmp_path, capsys, fake, {"NAS-HOSTNAME": "{take: dns_name}"}, ["--write"],
                 {**ENV, **WRITER})
    assert rc == 1
    assert "set, but reads back different" in out


def test_write_needs_the_writer_identity(monkeypatch, tmp_path, capsys):
    rc, out = go(monkeypatch, tmp_path, capsys, Fake({}), {"A": "{take: address}"}, ["--write"])
    assert rc == 2
    assert "FACTS_AZURE_CLIENT_ID" in out


@pytest.mark.parametrize("ips, vms, line, rc", [
    ([], {}, "A: no NetBox object claims it", 1),
    ([ip(1, "192.0.2.20/24", claims=["A"]), ip(2, "192.0.2.21/24", claims=["A"])], {},
     "A: claimed by 2 objects (IP address #1, IP address #2): left alone", 1),
    ([ip(1, "192.0.2.20/24", status="deprecated", claims=["A"])], {},
     "A: IP address #1: the address is deprecated, so it's left alone", 0),
    ([ip(1, "192.0.2.20/24", claims=["A"], vm=4)], {4: "offline"},
     "A: IP address #1: its device or VM is offline, so it's left alone", 0),
    ([ip(1, "192.0.2.20/24", claims=["A"], vm=4)], {4: "decommissioning"},
     "A: IP address #1: its device or VM is decommissioning, so it's left alone", 0),
])
def test_a_name_without_one_usable_source_is_never_written(monkeypatch, tmp_path, capsys, ips, vms, line, rc):
    fake = Fake({"A": "old"}, ips=ips, vms=vms, choices=["A"])
    got, out = go(monkeypatch, tmp_path, capsys, fake, {"A": "{take: address}"}, ["--write"], {**ENV, **WRITER})
    assert got == rc
    assert line in out
    assert fake.writes == []


def test_a_template_on_a_name_left_alone_is_left_alone_too(monkeypatch, tmp_path, capsys):
    fake = Fake({"A": "old", "URL": "old"}, ips=[ip(1, "192.0.2.20/24", status="deprecated", claims=["A"])],
                choices=["A", "URL"])
    facts = {"A": "{take: address}", "URL": '{template: "http://{A}"}'}
    rc, out = go(monkeypatch, tmp_path, capsys, fake, facts, ["--write"], {**ENV, **WRITER})
    assert "URL: a name its template reads has no usable value above, so it's left alone" in out
    assert fake.writes == []


def test_the_field_and_its_choices_are_checked(monkeypatch, tmp_path, capsys):
    rc, out = go(monkeypatch, tmp_path, capsys, Fake({}, field=False), {"A": "{take: address}"})
    assert "NetBox has no custom field keyvault_secrets" in out
    rc, out = go(monkeypatch, tmp_path, capsys, Fake({}, choices=[]), {"A": "{take: address}"})
    assert "keyvault_secrets's choices lack A" in out


def test_a_claim_the_map_lacks_is_reported(monkeypatch, tmp_path, capsys):
    fake = Fake({"A": "192.0.2.20"}, ips=[ip(1, "192.0.2.20/24", claims=["A", "B"])], choices=["A", "B"])
    rc, out = go(monkeypatch, tmp_path, capsys, fake, {"A": "{take: address}"})
    assert "IP address #1 claims B, which keyvault_facts.yaml doesn't map" in out


def test_any_other_argument_is_refused_without_repeating_it(monkeypatch, tmp_path, capsys):
    rc, out = go(monkeypatch, tmp_path, capsys, Fake({}), {}, ["NETBOX_TOKEN=<secret-value>"])
    assert rc == 2
    assert "<secret-value>" not in out
    rc, out = go(monkeypatch, tmp_path, capsys, Fake({}), {}, ["--find", "--write"])
    assert rc == 2


@pytest.mark.parametrize("facts, msg", [
    ({"A": "{take: owner}"}, "take: is one of"),
    ({"A": '{template: "plain"}'}, "names no other secret"),
    ({"A": '{template: "{B}"}', "B": '{template: "{C}"}'}, "aren't templates"),
    ({"A": "{take: address, template: x}"}, "exactly one of"),
    ({"a_b": "{take: address}"}, "isn't a Key Vault name"),
])
def test_what_the_map_may_not_say(monkeypatch, tmp_path, capsys, facts, msg):
    rc, out = go(monkeypatch, tmp_path, capsys, Fake({}), facts)
    assert rc == 2
    assert msg in out


def test_names_gives_the_writer_and_every_mapped_name_reading_nothing(monkeypatch, tmp_path, capsys):
    mapfile = tmp_path / "map.yaml"
    mapfile.write_text("writer: some-writer\nfacts:\n  A: {take: address}\n  B: {take: cidr}\n")
    monkeypatch.setenv("KEYVAULT_FACTS", str(mapfile))
    monkeypatch.setattr(m, "http", lambda *a, **k: pytest.fail("--names reads nothing"))
    assert m.main(["--names"]) == 0
    import json as j
    assert j.loads(capsys.readouterr().out) == {"writer": "some-writer", "names": ["A", "B"]}
    mapfile.write_text("facts:\n  A: {take: address}\n")
    assert m.main(["--names"]) == 2 and "writer: names the writer" in capsys.readouterr().out


def test_the_real_map_loads():
    m.load_map(ROOT / "scripts" / "keyvault_facts.yaml")


def test_its_variable_group_gives_what_it_reads_beside_the_syncs_own():
    groups = importlib.util.spec_from_file_location("svg", ROOT / "scripts" / "semaphore_variable_groups.py")
    svg = importlib.util.module_from_spec(groups)
    groups.loader.exec_module(svg)
    config = svg.load_config(ROOT / "scripts" / "semaphore_variable_groups.yaml")
    group = config["groups"]["Key Vault Facts"]
    # The Azure reader comes from "Key Vault Sync", the sync's own group, which isn't declared.
    reads = set(m.NEEDS) - {v for v in m.NEEDS if v.startswith("AZURE_")}
    assert set(group["environment"]) == reads and not group.get("variables")
    # The writer only in its own group, which only the write template uses.
    assert set(config["groups"]["Key Vault Facts Writer"]["environment"]) == set(m.WRITER)
    assert not set(m.WRITER) & set(group["environment"])


def prefix(id_, cidr, claims=(), status="active", vlan=None):
    return {"id": id_, "prefix": cidr, "status": {"value": status}, "custom_fields": {m.FIELD: nb_claims(claims)},
            "vlan": vlan}


def test_collect_joins_every_claimant_so_tagging_another_adds_it(monkeypatch, tmp_path, capsys):
    claims = ["CLIENTS"]
    nets = [prefix(1, "192.0.2.0/24", claims), prefix(2, "198.51.100.0/24", claims),
            prefix(3, "203.0.113.0/24", claims, status="deprecated")]
    fake = Fake({"CLIENTS": "192.0.2.0/24"}, prefixes=nets, choices=claims)
    rc, out = go(monkeypatch, tmp_path, capsys, fake, {"CLIENTS": '{collect: cidr, join: " "}'},
                 ["--write"], {**ENV, **WRITER})
    assert rc == 0
    assert "CLIENTS: differs (cidr of 2 objects (1 left out by status))" in out
    assert fake.vault["CLIENTS"] == "192.0.2.0/24 198.51.100.0/24"
    assert "203.0.113" not in fake.vault["CLIENTS"]


def view(monkeypatch, tmp_path, name, body):
    d = tmp_path / "views"
    d.mkdir(exist_ok=True)
    (d / f"{name}.py").write_text(body)
    monkeypatch.setattr(m, "VIEWS", d)


SITE_VIEW = '''
def build(site):
    nets = {}
    for p in site.usable("prefix"):
        nets[p["vlan"]["name"]] = {"prefix": site.form("prefix", p)["cidr"]}
    return {"networks": nets, "domain": site.secret("BASE-DOMAIN")}
'''


def test_a_view_builds_json_from_netbox_compared_as_data_and_reported_by_path(monkeypatch, tmp_path, capsys):
    view(monkeypatch, tmp_path, "site", SITE_VIEW)
    nets = [prefix(1, "192.0.2.0/24", vlan={"name": "Management"}), prefix(2, "198.51.100.0/24", vlan={"name": "IoT"})]
    # The same data with other spacing and order agrees; the value is never printed.
    held = '{ "domain": "example.org", "networks": {"IoT": {"prefix": "198.51.100.0/24"}, "Management": {"prefix": "192.0.2.0/24"}} }'
    fake = Fake({"SITE": held, "BASE-DOMAIN": "example.org"}, prefixes=nets)
    rc, out = go(monkeypatch, tmp_path, capsys, fake, {"SITE": "{view: site}"})
    assert rc == 0 and "SITE: agrees (view site)" in out
    # A prefix added in NetBox reaches it at the next run, reported by path.
    fake.nb["ipam/prefixes"].append(prefix(3, "203.0.113.0/24", vlan={"name": "DMZ"}))
    rc, out = go(monkeypatch, tmp_path, capsys, fake, {"SITE": "{view: site}"}, ["--write"], {**ENV, **WRITER})
    assert "SITE: differs (view site)" in out and "networks.DMZ (only in NetBox's)" in out
    assert "203.0.113" not in out and "example.org" not in out
    import json as j
    assert j.loads(fake.vault["SITE"])["networks"]["DMZ"] == {"prefix": "203.0.113.0/24"}


def test_a_view_that_fails_is_reported_by_type_and_line_never_its_message(monkeypatch, tmp_path, capsys):
    view(monkeypatch, tmp_path, "broken", 'def build(site):\n    raise ValueError(site.secret("A-TOKEN"))\n')
    fake = Fake({"OUT": "x", "A-TOKEN": "<the-value>"})
    rc, out = go(monkeypatch, tmp_path, capsys, fake, {"OUT": "{view: broken}"}, ["--write"], {**ENV, **WRITER})
    assert rc == 1
    assert "OUT: its view failed: ValueError at broken.py:2" in out
    assert "<the-value>" not in out and fake.writes == []


def test_a_template_on_a_name_that_failed_never_falls_back_to_key_vault(monkeypatch, tmp_path, capsys):
    fake = Fake({"A": "old", "URL": "old"}, choices=["A", "URL"])  # nothing claims A
    rc, out = go(monkeypatch, tmp_path, capsys, fake, {"A": "{take: address}", "URL": '{template: "http://{A}"}'},
                 ["--write"], {**ENV, **WRITER})
    assert rc == 1 and fake.writes == []
    assert "URL: a name its template reads has no usable value above, so it's left alone" in out


@pytest.mark.parametrize("facts, msg", [
    ({"A": '{take: address, join: ","}'}, "join: goes only with collect:"),
    ({"A": "{view: nowhere}"}, "names a file in scripts/keyvault_views/"),
    ({"A": "{view: ../x}"}, "names a file in scripts/keyvault_views/"),
    ({"A": "{collect: owner}"}, "collect: is one of"),
])
def test_what_collect_and_view_may_not_say(monkeypatch, tmp_path, capsys, facts, msg):
    rc, out = go(monkeypatch, tmp_path, capsys, Fake({}), facts)
    assert rc == 2 and msg in out


def test_a_node_name_comes_from_its_vm_and_only_while_the_vm_may_be_used(monkeypatch, tmp_path, capsys):
    node = {"id": 9, "name": "worker-a", "status": {"value": "active"},
            "custom_fields": {m.FIELD: nb_claims(["WORKER-NODE-4"])}}
    fake = Fake({"WORKER-NODE-4": "worker-old"}, vm_list=[node], choices=["WORKER-NODE-4"])
    rc, out = go(monkeypatch, tmp_path, capsys, fake, {"WORKER-NODE-4": "{take: name}"}, ["--write"], {**ENV, **WRITER})
    assert rc == 0 and fake.vault["WORKER-NODE-4"] == "worker-a" and "worker-a" not in out
    node["status"] = {"value": "decommissioning"}
    fake.vault["WORKER-NODE-4"] = "worker-old"
    rc, out = go(monkeypatch, tmp_path, capsys, fake, {"WORKER-NODE-4": "{take: name}"}, ["--write"], {**ENV, **WRITER})
    assert "VM #9: it is decommissioning, so it's left alone" in out and fake.vault["WORKER-NODE-4"] == "worker-old"


def record(id_, fqdn, claims=(), status="active", type_="A", value="192.0.2.227"):
    return {"id": id_, "fqdn": fqdn, "type": type_, "status": status, "active": True, "value": value,
            "custom_fields": {m.FIELD: nb_claims(claims)}}


def test_a_host_name_comes_from_its_dns_record_and_a_url_from_a_format(monkeypatch, tmp_path, capsys):
    fake = Fake({"NAS-HOSTNAME": "nas.lan.example", "PIHOLE-2-URL": "http://192.0.2.21"},
                records=[record(4, "nas.lan.example.", ["NAS-HOSTNAME"]), record(5, "lan.example.", type_="SOA")],
                ips=[ip(16, "192.0.2.21/24", claims=["PIHOLE-2-URL"])], choices=["NAS-HOSTNAME", "PIHOLE-2-URL"])
    facts = {"NAS-HOSTNAME": "{take: fqdn}", "PIHOLE-2-URL": '{take: address, format: "http://{}"}'}
    rc, out = go(monkeypatch, tmp_path, capsys, fake, facts)
    assert rc == 0
    assert "NAS-HOSTNAME: agrees (fqdn of DNS record #4)" in out
    assert "PIHOLE-2-URL: agrees (address of IP address #16)" in out
    rc, out = go(monkeypatch, tmp_path, capsys, Fake({"NAS-HOSTNAME": "nas.lan.example", "NFS-SERVER": "192.0.2.227"},
                 records=[record(4, "nas.lan.example.")]), {}, ["--find"])
    assert "NAS-HOSTNAME: equals fqdn of DNS record #4 (active)" in out
    assert "NFS-SERVER: equals value of DNS record #4 (active)" in out
    assert "192.0.2.227" not in out


@pytest.mark.parametrize("facts, msg", [
    ({"A": '{take: address, format: "http://"}'}, "format: is text with one {} where the value goes"),
    ({"A": '{template: "{B}", format: "{}"}'}, "format: with take: or collect:"),
])
def test_what_format_may_not_say(monkeypatch, tmp_path, capsys, facts, msg):
    rc, out = go(monkeypatch, tmp_path, capsys, Fake({}), facts)
    assert rc == 2 and msg in out


KEY = {m.FINGERPRINT_KEY: "<fingerprint-key>"}


def test_fingerprints_spare_the_reads_and_a_hand_edit_is_still_caught(monkeypatch, tmp_path, capsys):
    fake = Fake({"NAS-HOSTNAME": "nas.lan.example", "PIHOLE-ADDRESS": "192.0.2.20", **KEY},
                ips=[ip(7, "192.0.2.20/24", claims=["PIHOLE-ADDRESS"]),
                     ip(8, "192.0.2.30/24", dns="nas.lan.example", claims=["NAS-HOSTNAME"])],
                choices=["PIHOLE-ADDRESS", "NAS-HOSTNAME"])
    facts = {"PIHOLE-ADDRESS": "{take: address}", "NAS-HOSTNAME": "{take: dns_name}"}
    writer = {**ENV, **WRITER}
    # The first --write finds both agreeing but untagged: it tags them, their values untouched.
    rc, out = go(monkeypatch, tmp_path, capsys, fake, facts, ["--write"], writer)
    assert rc == 0 and sorted(fake.tagged) == ["NAS-HOSTNAME", "PIHOLE-ADDRESS"] and fake.writes == []
    assert "fingerprint added" in out
    # Next run: both agree by fingerprint, and neither value is read.
    fake.reads.clear()
    rc, out = go(monkeypatch, tmp_path, capsys, fake, facts)
    assert rc == 0 and [r for r in fake.reads if r != m.FINGERPRINT_KEY] == []
    assert "2 of 2 agreed by fingerprint, their values not read" in out
    # A hand edit makes an untagged version: it's read, found different, and set back.
    fake.vault["PIHOLE-ADDRESS"], fake.tags["PIHOLE-ADDRESS"] = "192.0.2.99", {}
    fake.version["PIHOLE-ADDRESS"] += 1
    fake.reads.clear()
    rc, out = go(monkeypatch, tmp_path, capsys, fake, facts, ["--write"], writer)
    assert fake.reads.count("PIHOLE-ADDRESS") == 2 and "NAS-HOSTNAME" not in fake.reads  # its read, and the read back
    assert fake.vault["PIHOLE-ADDRESS"] == "192.0.2.20" and "changed: PIHOLE-ADDRESS" in out
    # A change in NetBox changes the fingerprint, so that one is read and set.
    fake.nb["ipam/ip-addresses"][1]["dns_name"] = "nas2.lan.example"
    rc, out = go(monkeypatch, tmp_path, capsys, fake, facts, ["--write"], writer)
    assert fake.vault["NAS-HOSTNAME"] == "nas2.lan.example" and "changed: NAS-HOSTNAME" in out
    assert "<fingerprint-key>" not in out and "192.0.2" not in out


def test_full_reads_every_value_and_a_missing_key_reads_too(monkeypatch, tmp_path, capsys):
    fake = Fake({"PIHOLE-ADDRESS": "192.0.2.20", **KEY}, ips=[ip(7, "192.0.2.20/24", claims=["PIHOLE-ADDRESS"])],
                choices=["PIHOLE-ADDRESS"])
    fake.tags["PIHOLE-ADDRESS"] = {m.TAG: m.fingerprint("<fingerprint-key>", "PIHOLE-ADDRESS", "192.0.2.20")}
    rc, out = go(monkeypatch, tmp_path, capsys, fake, {"PIHOLE-ADDRESS": "{take: address}"}, ["--full"])
    assert rc == 0 and "PIHOLE-ADDRESS" in fake.reads and "by fingerprint" not in out
    # --full with --write still tags what it sets.
    fake.nb["ipam/ip-addresses"][0]["address"] = "192.0.2.21/24"
    rc, out = go(monkeypatch, tmp_path, capsys, fake, {"PIHOLE-ADDRESS": "{take: address}"}, ["--write", "--full"],
                 {**ENV, **WRITER})
    assert fake.tags["PIHOLE-ADDRESS"][m.TAG] == m.fingerprint("<fingerprint-key>", "PIHOLE-ADDRESS", "192.0.2.21")
    del fake.vault[m.FINGERPRINT_KEY]
    fake.reads.clear()
    rc, out = go(monkeypatch, tmp_path, capsys, fake, {"PIHOLE-ADDRESS": "{take: address}"})
    assert rc == 0 and "isn't in Key Vault, so each value is read" in out and "PIHOLE-ADDRESS" in fake.reads


def test_the_fingerprint_is_keyed_and_tied_to_the_name():
    a = m.fingerprint("<k1>", "A", "192.0.2.20")
    assert a != m.fingerprint("<k2>", "A", "192.0.2.20") and a != m.fingerprint("<k1>", "B", "192.0.2.20")
    assert a != __import__("hashlib").sha256(b"192.0.2.20").hexdigest()


@pytest.mark.parametrize("args, ok", [(["--write", "--full"], True), (["--full"], True), (["--find", "--full"], False),
                                      (["--full", "--full"], False), (["--names", "--full"], False)])
def test_full_goes_alone_or_with_write(args, ok):
    if ok:
        assert m.parse_args(args)[1] is True
    else:
        with pytest.raises(ValueError):
            m.parse_args(args)


@pytest.mark.parametrize("choice, want", [("check", (None, False)), ("find", ("find", False)), ("full", (None, True)),
                                          ("write", ("write", False)),
                                          ("write-full", ("write", True))])
def test_facts_mode_names_each_choice_as_an_argument_or_in_the_environment(choice, want):
    assert m.parse_args([f"FACTS_MODE={choice}"]) == want
    assert m.parse_args([], {"FACTS_MODE": choice}) == want


@pytest.mark.parametrize("argv, env", [(["FACTS_MODE=<other>"], None), (["FACTS_MODE=find", "--write"], None),
                                       (["FACTS_MODE=find", "FACTS_MODE=write"], None),
                                       (["--write"], {"FACTS_MODE": "find"}), ([], {"FACTS_MODE": "<other>"})])
def test_facts_mode_goes_alone_and_names_only_a_known_choice(argv, env):
    with pytest.raises(ValueError) as e:
        m.parse_args(argv, env)
    assert "<other>" not in str(e.value)


def test_the_survey_argument_runs_its_mode_end_to_end(monkeypatch, tmp_path, capsys):
    fake = Fake({"A": "192.0.2.20"}, ips=[ip(7, "192.0.2.20/24", claims=["A"])], choices=["A"])
    rc, out = go(monkeypatch, tmp_path, capsys, fake, {"A": "{take: address}"}, ["FACTS_MODE=check"])
    assert rc == 0 and "A: agrees (address of IP address #7)" in out
    with pytest.raises(ValueError):
        m.parse_args(["FACTS_MODE=roles"])  # gone: keyvault-secrets.ps1 -FactsWriter keeps the writer's roles


def test_kea_db_clients_is_the_smallest_prefix_holding_both_docker_vms(monkeypatch, tmp_path, capsys):
    vms = lambda a, b: [ip(22, a, claims=["DOCKER-VM-1-PROD-ADDRESS"]), ip(47, b, claims=["DOCKER-VM-2-PROD-ADDRESS"])]
    facts = {"KEA-DB-CLIENTS": "{view: kea_db_clients}"}
    fake = Fake({"KEA-DB-CLIENTS": "192.0.2.40/31"}, ips=vms("192.0.2.40/24", "192.0.2.41/24"))
    rc, out = go(monkeypatch, tmp_path, capsys, fake, facts)
    assert rc == 0 and "KEA-DB-CLIENTS: agrees (view kea_db_clients)" in out
    # A VM moved one address along: a /30 then holds both.
    fake = Fake({"KEA-DB-CLIENTS": "192.0.2.40/31"}, ips=vms("192.0.2.41/24", "192.0.2.42/24"))
    rc, out = go(monkeypatch, tmp_path, capsys, fake, facts, ["--write"], {**ENV, **WRITER})
    assert fake.vault["KEA-DB-CLIENTS"] == "192.0.2.40/30"
    # Far apart: it stops rather than let a wide prefix into the database.
    fake = Fake({"KEA-DB-CLIENTS": "192.0.2.40/31"}, ips=vms("192.0.2.40/24", "192.0.2.90/24"))
    rc, out = go(monkeypatch, tmp_path, capsys, fake, facts, ["--write"], {**ENV, **WRITER})
    assert rc == 1 and fake.writes == [] and "wider than /30" in out and "192.0.2" not in out


def test_find_reports_where_each_leaf_of_a_json_variable_comes_from(monkeypatch, tmp_path, capsys):
    import json as j
    site = {"networks": {"vmbr0": {"prefix": "192.0.2", "gateway": "192.0.2.1"}},
            "vm_login": {"user": "<the-user>", "ssh_keys": ["<a-key>"]}, "zone": "lan.example"}
    fake = Fake({"SITE": j.dumps(site), "OWNER-SSH-KEY": "<a-key>"},
                ips=[ip(3, "192.0.2.1/24")], prefixes=[prefix(1, "192.0.2.0/24")],
                records=[record(9, "nas.lan.example.", value="192.0.2.30")])
    rc, out = go(monkeypatch, tmp_path, capsys, fake, {}, ["--find"])
    assert rc == 0
    assert "SITE (JSON, 5 leaves):" in out
    assert "networks.vmbr0.prefix: equals first three octets of prefix #1 (active)" in out
    assert "networks.vmbr0.gateway: equals address of IP address #3 (active)" in out
    assert "vm_login.ssh_keys[0]: equals Key Vault's OWNER-SSH-KEY" in out
    assert "match nothing: vm_login.user, zone" in out
    for v in ("192.0.2", "<the-user>", "<a-key>", "lan.example"):
        assert v not in out, v


def test_the_proxmox_site_view_builds_tofus_site_from_netbox_and_its_own_secrets(monkeypatch, tmp_path, capsys):
    import json as j
    vlan = lambda vid, name: {"vid": vid, "name": name}
    nets = [prefix(1, "192.0.2.0/25", vlan=vlan(5, "Management")), prefix(2, "198.51.100.0/24", vlan=vlan(3, "Work laptop")),
            prefix(3, "203.0.113.0/24", vlan=vlan(6, "IoT")), prefix(4, "192.0.2.128/25", vlan=vlan(7, "Production")),
            prefix(5, "198.51.100.128/25", vlan=vlan(8, "DMZ"))]
    def tag(r, slug):
        r["tags"] = [{"slug": slug}]
        return r
    records = [tag(record(19, "gw.lan.example.", value="192.0.2.254"), "gateway"),
               tag(record(36, "api.lan.example.", value="192.0.2.140"), "kubernetes-api")]
    piholes = [{"id": 2, "name": "pihole2", "role": {"name": "Pi-hole"}, "status": {"value": "active"}, "custom_fields": {}},
               {"id": 1, "name": "pihole1", "role": {"name": "Pi-hole"}, "status": {"value": "active"}, "custom_fields": {}}]
    ips = [ip(16, "192.0.2.131/25", vm=2), ip(11, "192.0.2.130/25", vm=1), ip(12, "192.0.2.10/25", vm=1)]
    secrets = {"NODE-SEARCH-DOMAIN": "nodes.example", "CLUSTER-DOMAIN": "svc.cluster.example", "CALICO-POD-CIDR": "10.10.0.0/16",
               "KUBERNETES-SERVICE-CIDR": "10.20.0.0/16", "OWNER-USERNAME": "<owner>", "OWNER-SSH-PUBLIC-KEY": "<key-a>",
               "SEMAPHORE-SHARED-SSH-PUBLIC-KEY": "<key-b>"}
    fake = Fake({**secrets, "SITE": "{}"}, prefixes=nets, records=records, vm_list=piholes, ips=ips, vms={1: "active", 2: "active"})
    rc, out = go(monkeypatch, tmp_path, capsys, fake, {"SITE": "{view: tofu_proxmox_site}"}, ["--write"], {**ENV, **WRITER})
    assert rc == 0, out
    got = j.loads(fake.vault["SITE"])
    assert got["networks"]["vmbr7"] == {"prefix": "192.0.2", "prefix_length": 25, "gateway": "192.0.2.254"}
    assert got["networks"]["vmbr0"] == {"prefix": "192.0.2", "prefix_length": 25}  # no gateway record in it
    assert set(got["networks"]) == {"vmbr0", "vmbr2", "vmbr6", "vmbr7", "vmbr8"}  # vmbr1 has no VLAN
    assert got["node_dns_servers"] == ["192.0.2.130", "192.0.2.131"]  # the Pi-holes' Production addresses, by name
    assert got["talos"] == {"cluster_endpoint": "api.lan.example:6443", "api_vip": "192.0.2.140", "dns_domain": "svc.cluster.example",
                            "pod_subnet": "10.10.0.0/16", "service_subnet": "10.20.0.0/16"}
    assert got["vm_login"] == {"user": "<owner>", "ssh_keys": ["<key-a>", "<key-b>"]} and got["node_search_domain"] == "nodes.example"
    assert "<key-a>" not in out and "192.0.2" not in out
    # A second gateway record in one prefix stops it rather than guess.
    fake.nb["plugins/netbox-dns/records"].append(tag(record(20, "gw2.lan.example.", value="192.0.2.253"), "gateway"))
    rc, out = go(monkeypatch, tmp_path, capsys, fake, {"SITE": "{view: tofu_proxmox_site}"})
    assert rc == 1 and "vmbr7: 2 gateway records in its prefix, not one" in out
