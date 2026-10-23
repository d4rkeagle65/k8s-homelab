"""ha_iot_setup.py's pure helpers and its setup-flow driver: which NetBox devices it
reads, which integration adds each, which room each goes in, and how it answers or
closes a flow. Home Assistant is a scripted fake; no address or MAC is real."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

SCRIPT = Path(__file__).resolve().parents[1] / "ha_iot_setup.py"


def load():
    if str(SCRIPT.parent) not in sys.path:  # where running the script puts it, for netbox_seed
        sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("ha_iot_setup_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load()
INTEGRATIONS = YAML(typ="safe").load(SCRIPT.with_suffix(".yaml").read_text(encoding="utf-8"))["integrations"]


def nb_device(id_, name, type_, maker, ip="192.0.2.10/24", mark=True):
    return {"id": id_, "name": name, "description": m.IOT_DEVICE_MARK if mark else "",
            "device_type": {"slug": type_, "manufacturer": {"slug": maker}},
            "primary_ip4": {"address": ip} if ip else None}


def test_netbox_devices_are_the_syncs_own_with_their_address_and_wifi0_mac():
    devices = [nb_device(1, "zziotkalihall", "kasa-hs200", "kasa"),
               nb_device(2, "printer", "x", "y", mark=False),
               nb_device(3, "zziotkaplnoip", "kasa-hs103p2", "kasa", ip=None)]
    interfaces = [{"name": "wifi0", "device": {"id": 1}, "primary_mac_address": {"mac_address": "00:00:5E:00:53:01"}},
                  {"name": "bt0", "device": {"id": 1}, "primary_mac_address": {"mac_address": "00:00:5E:00:53:99"}}]
    got = m.netbox_iot_devices(devices, interfaces)
    assert [d["name"] for d in got] == ["zziotkalihall", "zziotkaplnoip"]
    assert got[0]["host"] == "192.0.2.10" and got[0]["mac"] == "00:00:5E:00:53:01"
    assert got[1]["host"] is None and got[1]["mac"] is None


def test_integration_by_type_or_manufacturer_and_none_for_the_cloud_ones():
    kasa = {"type": "kasa-hs200", "manufacturer": "kasa"}
    fire = {"type": "amazon-fire-stick-4k", "manufacturer": "amazon"}
    echo = {"type": "amazon-echo-dot", "manufacturer": "amazon"}
    assert m.integration_for(kasa, INTEGRATIONS)["handler"] == "tplink"
    assert m.integration_for(fire, INTEGRATIONS)["fields"]["device_class"] == "firetv"
    assert m.integration_for(echo, INTEGRATIONS) is None


def test_area_is_the_first_room_pattern_found_after_iot():
    rooms = [["nur", "Nursery"], ["liv", "Living Room"], ["bed", "Bedroom"]]
    assert m.area_for("zziotgopurnurs", rooms) == "Nursery"
    assert m.area_for("zzaaiotkalilivend", rooms) == "Living Room"
    assert m.area_for("zziotkalibednur", rooms) == "Nursery"  # first pattern wins
    assert m.area_for("zziotazthermost", rooms) is None
    assert m.area_for("livingroomtv", rooms) is None  # not an IoT hostname


def test_home_assistant_devices_by_mac_whatever_its_format():
    by = m.ha_device_by_mac([{"id": "a", "connections": [["mac", "00:00:5E:00:53:01"]]},
                             {"id": "b", "connections": [["zigbee", "x"]]}])
    assert by[m.norm_mac("00-00-5e-00-53-01")]["id"] == "a" and len(by) == 1


def test_first_answer_gives_only_the_fields_the_form_asks_and_its_sections():
    schema = [{"name": "host"}, {"name": "device_class"}, {"name": "more_options", "type": "expandable"}]
    got = m.first_answer(schema, "192.0.2.10", {"device_class": "firetv", "port": 5555})
    assert got == {"host": "192.0.2.10", "device_class": "firetv", "more_options": {}}


class FakeHA:
    """Answers each flow call with the next scripted response, recording what was sent."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.sent = []

    def flow(self, path, body=None, method="POST"):
        self.sent.append((method, path, body))
        return None if method == "DELETE" else self.responses.pop(0)


def form(*names, step="user", errors=None):
    return {"type": "form", "flow_id": "F", "step_id": step, "errors": errors or {},
            "data_schema": [{"name": n} for n in names]}


TPLINK = m.integration_for({"type": "kasa-hs200", "manufacturer": "kasa"}, INTEGRATIONS)


def test_a_flow_that_needs_only_the_host_is_added():
    ha = FakeHA(form("host"), {"type": "create_entry", "title": "Hall HS200"})
    assert m.run_flow(ha, TPLINK, "192.0.2.10", environ={}) == ("added", "Hall HS200")
    assert ha.sent[1] == ("POST", "/F", {"host": "192.0.2.10"})


def test_a_login_step_without_its_variables_is_closed_and_reported_waiting():
    ha = FakeHA(form("host"), form("username", "password", step="user_auth_confirm"))
    status, detail = m.run_flow(ha, TPLINK, "192.0.2.10", environ={})
    assert status == "waiting" and TPLINK["login"]["username"] in detail
    assert ha.sent[-1] == ("DELETE", "/F", None)


def test_a_login_step_with_its_variables_is_answered():
    ha = FakeHA(form("host"), form("username", "password"), {"type": "create_entry", "title": "Plug"})
    fake = os.urandom(4).hex()  # made per run, so no credential-shaped literal sits in the repo
    env = {TPLINK["login"]["username"]: "user@example.com", TPLINK["login"]["password"]: fake}
    assert m.run_flow(ha, TPLINK, "192.0.2.10", environ=env)[0] == "added"
    assert ha.sent[2][2] == {"username": "user@example.com", "password": fake}


def test_a_step_it_cannot_answer_is_closed_and_reported_waiting():
    ha = FakeHA(form("host"), form("pin", step="encrypted_pairing"))
    status, detail = m.run_flow(ha, {"handler": "samsungtv"}, "192.0.2.10", environ={})
    assert status == "waiting" and "encrypted_pairing" in detail
    assert ha.sent[-1][0] == "DELETE"


def test_a_form_with_errors_fails_and_is_closed():
    ha = FakeHA(form("host"), form("host", errors={"base": "cannot_connect"}))
    status, detail = m.run_flow(ha, TPLINK, "192.0.2.10", environ={})
    assert (status, detail) == ("failed", "base: cannot_connect")
    assert ha.sent[-1][0] == "DELETE"


def test_already_configured_is_present_and_another_abort_fails():
    ha = FakeHA(form("host"), {"type": "abort", "reason": "already_configured"})
    assert m.run_flow(ha, TPLINK, "192.0.2.10", environ={}) == ("present", "already_configured")
    ha = FakeHA(form("host"), {"type": "abort", "reason": "no_devices_found"})
    assert m.run_flow(ha, TPLINK, "192.0.2.10", environ={})[0] == "failed"


def test_the_room_map_comes_from_the_environment_and_must_be_pairs():
    assert m.load_rooms({"HA_IOT_AREAS": json.dumps([["nur", "Nursery"]])}) == [["nur", "Nursery"]]
    with pytest.raises(SystemExit):
        m.load_rooms({"HA_IOT_AREAS": json.dumps({"nur": "Nursery"})})
