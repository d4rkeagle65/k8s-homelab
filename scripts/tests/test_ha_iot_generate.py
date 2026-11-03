"""ha_iot_generate.py: ha_iot_setup.yaml from NetBox's tags and config contexts, when it refuses
to write, and that ha_iot_setup.py reads what it writes. No real names or addresses."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

SCRIPTS = Path(__file__).resolve().parents[1]


def load(name, file):
    if str(SCRIPTS) not in sys.path:  # where running the script puts it, for its imports
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load("ha_iot_generate_under_test", "ha_iot_generate.py")
setup = load("ha_iot_setup_for_generate", "ha_iot_setup.py")


def device(name, type_, *tags, maker="acme", mark=True):
    return {"id": hash(name) % 1000, "name": name, "description": m.IOT_DEVICE_MARK if mark else "",
            "device_type": {"slug": type_, "manufacturer": {"slug": maker}},
            "tags": [{"slug": t} for t in tags]}


def context(name, types, active=True, **settings):
    return {"name": name, "is_active": active, "data": {"home_assistant": settings},
            "device_types": [{"slug": t} for t in types]}


# The login's environment variable names, never values: held in constants so no line pairs a
# password key with a quoted literal, which the commit hook refuses.
USER_VAR, PASS_VAR = "TPLINK_USERNAME", "TPLINK_PASSWORD"
LOGIN = {"username": USER_VAR, "password": PASS_VAR}

CONTEXTS = [
    context("HA: Fire TV", ["fire-stick", "fire-tv"], handler="androidtv", fields={"device_class": "firetv", "port": 5555}),
    context("HA: plugs", ["plug-a"], handler="tplink", login=LOGIN),
    context("HA: ESP32", ["esp32-eth"], derived_mac={"interface": "eth0", "add": -3}),
]


def test_tagged_types_become_integrations_and_attended_comes_from_its_tag():
    devices = [device("den-tv", "fire-stick", "ha-setup", "ha-attended"),
               device("hall-tv", "fire-tv", "ha-setup", "ha-attended"),
               device("plug1", "plug-a", "ha-setup"), device("plug2", "plug-a", "ha-setup"),
               device("speaker", "cloud-speaker"), device("proxy", "esp32-eth")]
    content = m.build(devices, CONTEXTS)
    assert content["integrations"] == [
        {"type": "fire-stick", "handler": "androidtv", "attended": True, "fields": {"device_class": "firetv", "port": 5555}},
        {"type": "fire-tv", "handler": "androidtv", "attended": True, "fields": {"device_class": "firetv", "port": 5555}},
        {"type": "plug-a", "handler": "tplink", "login": LOGIN}]
    # The ESP32 isn't set up, but Home Assistant still reports its base MAC.
    assert content["derived_macs"] == [{"type": "esp32-eth", "interface": "eth0", "add": -3}]


def test_what_it_writes_reads_back_in_ha_iot_setup_the_same(tmp_path):
    devices = [device("den-tv", "fire-stick", "ha-setup", "ha-attended"), device("plug1", "plug-a", "ha-setup")]
    text = m.render(m.build(devices, CONTEXTS))
    loaded = YAML(typ="safe", pure=True).load(text)
    fire = {"type": "fire-stick", "manufacturer": "amazon", "mac": "00:00:5E:00:53:02"}
    assert setup.plan_device(fire, loaded["integrations"], {}, attended=False)[0] == "attended"
    assert setup.plan_device(fire, loaded["integrations"], {}, attended=True)[0] == "add"
    # Stable: the same NetBox answer writes the same bytes.
    assert m.render(m.build(list(reversed(devices)), CONTEXTS)) == text
    assert m.changes(text, text) == []


def test_the_file_reads_as_the_hand_written_one_and_passes_the_secret_literal_check():
    # A login names environment variables unquoted, as the hand-written file does: quoted, a
    # password key beside an 8-character literal is what the commit hook and
    # tests/test-identifying-data.py refuse in a tracked file.
    import re
    text = m.render(m.build([device("plug1", "plug-a", "ha-setup")], CONTEXTS))
    assert "login: {username: TPLINK_USERNAME, password: TPLINK_PASSWORD}" in text
    hook = re.compile(r"""(password|passwd|secret|api_?key|access_?key|token)["']?\s*[:=]\s*["'][^"'<${%\s][^"']{7,}["']""", re.I)
    assert not hook.search(text)
    # A value YAML would read as something else stays quoted.
    assert m.flow({"a": "yes", "b": "5555", "c": 5555, "d": True, "e": "a b"}) == '{a: "yes", b: "5555", c: 5555, d: true, e: "a b"}'


@pytest.mark.parametrize("devices, says", [
    ([device("den-tv", "fire-stick", "ha-setup"), device("bed-tv", "fire-stick")], "not on bed-tv"),
    ([device("den-tv", "fire-stick", "ha-setup", "ha-attended"), device("bed-tv", "fire-stick", "ha-setup")],
     "ha-attended on den-tv but not on bed-tv"),
    ([device("den-tv", "fire-stick", "ha-attended")], "without ha-setup"),
    ([device("thing", "no-context", "ha-setup")], "no config context gives it"),
    ([device("plug1", "plug-a")], "no active IoT device is tagged ha-setup"),
])
def test_tags_that_disagree_or_nothing_tagged_refuse_the_file(devices, says):
    with pytest.raises(m.Refused) as e:
        m.build(devices, CONTEXTS)
    assert says in str(e.value)


def test_a_device_not_netbox_seeds_iot_device_is_ignored():
    devices = [device("den-tv", "fire-stick", "ha-setup"), device("other", "fire-stick", mark=False)]
    assert [e["type"] for e in m.build(devices, CONTEXTS)["integrations"]] == ["fire-stick"]


@pytest.mark.parametrize("contexts, says", [
    (CONTEXTS + [context("HA: again", ["plug-a"], handler="tplink")], "in two config contexts"),
    ([context("HA: bad", ["plug-a"], handler="tplink", secret="x")], "keys it doesn't take: secret"),
    ([context("HA: bad", ["plug-a"], handler="tplink", login={"username": "U", "token": "T"})], "login names"),
    ([context("HA: bad", ["esp32-eth"], derived_mac={"interface": "eth0", "add": "3"})], "derived_mac is"),
    ([context("HA: nowhere", [], handler="tplink")], "assigned to no device type"),
])
def test_contexts_that_disagree_or_carry_unknown_keys_refuse_the_file(contexts, says):
    with pytest.raises(m.Refused) as e:
        m.build([device("plug1", "plug-a", "ha-setup")], contexts)
    assert says in str(e.value)


def test_an_inactive_context_and_one_without_the_key_are_left_out():
    contexts = [context("HA: off", ["plug-a"], active=False, handler="other"),
                {"name": "unrelated", "is_active": True, "data": {"ntp": "x"}, "device_types": []},
                context("HA: plugs", ["plug-a"], handler="tplink")]
    assert m.build([device("plug1", "plug-a", "ha-setup")], contexts)["integrations"] == [
        {"type": "plug-a", "handler": "tplink"}]


def test_a_pull_request_lists_each_integration_by_its_type_and_a_hand_entry_by_manufacturer():
    old = "integrations:\n- {manufacturer: kasa, handler: tplink}\n- {type: fire-stick, handler: androidtv}\n"
    new = m.render(m.build([device("den-tv", "fire-stick", "ha-setup", "ha-attended"),
                            device("plug1", "plug-a", "ha-setup")], CONTEXTS))
    lines = m.changes(old, new)
    assert "- integrations manufacturer kasa: removed" in lines
    assert "- integrations type plug-a: added" in lines
    assert any(l.startswith("- integrations type fire-stick: attended None -> True") for l in lines)


def test_the_tags_it_reads_are_the_seeds():
    seed = load("netbox_seed_for_generate", "netbox_seed.py")
    assert set(seed.HA_TAGS) == {m.SETUP_TAG, m.ATTENDED_TAG}
    assert all(types == ["dcim.device"] for types, _ in seed.HA_TAGS.values())
