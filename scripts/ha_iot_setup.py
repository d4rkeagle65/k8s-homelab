"""Add the IoT network's devices to Home Assistant from NetBox, and put each in its room.

The devices are NetBox's on the IoT network (netbox_seed.py makes them from Kea's
reservations, described "Kea reservation device"), each with its wifi0 MAC and
address. A device Home Assistant already has is one whose device registry entry
carries that MAC. For the rest, the first entry of ha_iot_setup.yaml matching the
device's type or manufacturer names the integration, and the script runs its setup
flow by address, as the UI's "Add integration" would. A flow that asks for what the
script can't give (a login it has no variables for, a PIN shown on a TV) is closed
and the device reported as waiting; the owner finishes it in the UI or supplies the
login and runs again. A flow ha_iot_setup.yaml marks attended (someone approves a
prompt or reads a PIN at the device) runs only with --attended; without it, those
devices are listed as waiting for an attended run. The device must be on and showing
(a Fire TV's prompt appears only on its screen, and a Samsung TV leaves the network in
standby), or its setup fails as cannot_connect. Devices no entry matches are the cloud integrations' and are
only placed in rooms.

Rooms: each device's Location in NetBox (a room of the site) names its area in Home
Assistant, so NetBox is where a room is set. A device in no area is put in its
Location's area (made if missing); one already in an area keeps it, since an area
chosen in Home Assistant is the owner's, and a difference is reported. A device with
no Location is listed.

A dry run (the default) reports what it would add and place; --apply does it and
reads Home Assistant's device registry back. It removes nothing. Names are printed,
never addresses, MACs or credentials.

Credentials are never arguments: HA_TOKEN or ~/.ha-token (a Home Assistant
administrator's long-lived token), NETBOX_TOKEN or ~/.netbox-token (read access is
enough), and each integration's login variables from ha_iot_setup.yaml. The URLs are
HA_URL (else ~/.ha-url) and NETBOX_API.

In Semaphore it is two Python templates like the NetBox sync, on one variable group
holding all of the above as environment variables (secret): script
scripts/ha_iot_setup.py with argument --apply, safe to schedule, and the same with
--apply --attended, run by hand while someone is at the TVs.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import ssl
import struct
import sys
from pathlib import Path
from urllib.parse import urlsplit

from ruamel.yaml import YAML

# netbox_seed.py sits beside this script, so Python finds it when it runs the script.
from netbox_seed import IOT_DEVICE_MARK, NetBox, http, read_secret, unexpected


class SocketClosed(Exception):
    """The WebSocket was closed under us (an idle connection is dropped on the way)."""


class HomeAssistantSocket:
    """Home Assistant's WebSocket API (the device and area registries have no REST
    form): one connection, authenticated, one command at a time."""

    def __init__(self, url: str, token: str):
        try:
            self._open(url, token)
        except (SocketClosed, OSError) as e:
            raise SystemExit(f"FAIL: no WebSocket to Home Assistant: {e}") from e

    def _open(self, url: str, token: str):
        u = urlsplit(url)
        port = u.port or (443 if u.scheme == "https" else 80)
        raw = socket.create_connection((u.hostname, port), timeout=60)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=u.hostname) \
            if u.scheme == "https" else raw
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(f"GET /api/websocket HTTP/1.1\r\nHost: {u.hostname}\r\nUpgrade: websocket\r\n"
                          f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                          "Sec-WebSocket-Version: 13\r\n\r\n".encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += self._read(1)
        status = head.split(b"\r\n")[0]
        if b" 101 " not in status:
            raise SystemExit(f"FAIL: Home Assistant refused the WebSocket: {status[:80]!r}")
        self.last = 0
        self._recv()
        self._send({"type": "auth", "access_token": token})
        if self._recv().get("type") != "auth_ok":
            raise SystemExit("FAIL: Home Assistant refused the token (HA_TOKEN or ~/.ha-token)")

    def _read(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            try:
                chunk = self.sock.recv(n - len(out))
            except OSError as e:
                raise SocketClosed(str(e)) from e
            if not chunk:
                raise SocketClosed("no more data")
            out += chunk
        return out

    def _send(self, obj: dict):
        payload = json.dumps(obj).encode()
        size = len(payload)
        head = bytes([0x81])
        if size < 126:
            head += bytes([0x80 | size])
        elif size < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", size)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", size)
        mask = os.urandom(4)
        try:
            self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        except OSError as e:
            raise SocketClosed(str(e)) from e

    def _recv(self) -> dict:
        data = b""
        while True:
            b1, b2 = self._read(2)
            size = b2 & 0x7F
            if size == 126:
                size = struct.unpack(">H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(size)
            if b1 & 0x0F in (0x9, 0xA):  # ping, pong
                continue
            if b1 & 0x0F == 0x8:
                raise SocketClosed("close frame")
            data += payload
            if b1 & 0x80:
                return json.loads(data)

    def call(self, kind: str, **args):
        self.last += 1
        self._send({"id": self.last, "type": kind, **args})
        while True:
            r = self._recv()
            if r.get("id") == self.last and r.get("type") == "result":
                if not r.get("success"):
                    raise SystemExit(f"FAIL: Home Assistant's {kind} answered {r.get('error')}")
                return r["result"]


FLOW_TIMEOUT = 90


class HomeAssistant:
    """Config flows over REST, registries over the WebSocket. A setup flow can keep
    the WebSocket idle for minutes, long enough for it to be dropped on the way, so a
    call that finds it closed opens a new one and is sent again, once."""

    def __init__(self, url: str, token: str):
        self.base = url.rstrip("/")
        self.headers = {"Authorization": "Bearer " + token}
        self.url, self.token = url, token
        self.ws = HomeAssistantSocket(url, token)

    def call(self, kind: str, **args):
        try:
            return self.ws.call(kind, **args)
        except SocketClosed:
            self.ws = HomeAssistantSocket(self.url, self.token)
            try:
                return self.ws.call(kind, **args)
            except SocketClosed as e:
                raise SystemExit(f"FAIL: Home Assistant's WebSocket closed twice during {kind}: {e}") from e

    def flow(self, path: str, body=None, method: str = "POST"):
        """One call of a setup flow. A step can wait on the device (an ADB connection
        waits for its prompt on the TV), so it gets FLOW_TIMEOUT seconds; one that
        fails or doesn't answer comes back as {"type": "no_answer"}, so that device
        fails and the others still run."""
        try:
            return http(method, f"{self.base}/api/config/config_entries/flow{path}", self.headers, body,
                        timeout=FLOW_TIMEOUT)
        except SystemExit as e:
            return {"type": "no_answer", "reason": str(e).removeprefix("FAIL: ")}

    def devices(self) -> list[dict]:
        return self.call("config/device_registry/list")

    def areas(self) -> list[dict]:
        return self.call("config/area_registry/list")


def norm_mac(mac: str) -> str:
    return re.sub(r"[^0-9a-f]", "", mac.lower())


def netbox_iot_devices(devices: list[dict], interfaces: list[dict]) -> list[dict]:
    """NetBox's IoT devices (those netbox_seed.py made) as {name, type, manufacturer,
    host, mac, room}: the address from the device's primary IPv4, the MAC from its
    wifi0's primary MAC, the room its Location's name. Any of them missing is None."""
    macs = {i["device"]["id"]: (i.get("primary_mac_address") or {}).get("mac_address")
            for i in interfaces if i["name"] == "wifi0"}
    out = []
    for d in devices:
        if (d.get("description") or "") != IOT_DEVICE_MARK:
            continue
        ip = (d.get("primary_ip4") or {}).get("address")
        out.append({"name": d["name"], "type": d["device_type"]["slug"],
                    "manufacturer": d["device_type"]["manufacturer"]["slug"],
                    "host": ip.split("/")[0] if ip else None, "mac": macs.get(d["id"]),
                    "room": (d.get("location") or {}).get("name")})
    return sorted(out, key=lambda d: d["name"])


def integration_for(device: dict, integrations: list[dict]) -> dict | None:
    """The first ha_iot_setup.yaml entry matching the device's type or manufacturer."""
    return next((i for i in integrations
                 if i.get("type") == device["type"] or i.get("manufacturer") == device["manufacturer"]), None)


def plan_device(device: dict, integrations: list[dict], by_mac: dict, attended: bool) -> tuple[str, dict | None]:
    """What a run does with one NetBox device, and its ha_iot_setup.yaml entry: "present"
    (Home Assistant has its MAC), "cloud" (no entry: set up by hand), "attended" (its
    flow needs someone at the device and this run isn't attended) or "add"."""
    entry = integration_for(device, integrations)
    if norm_mac(device["mac"]) in by_mac:
        return "present", entry
    if entry is None:
        return "cloud", None
    if entry.get("attended") and not attended:
        return "attended", entry
    return "add", entry


def ha_device_by_mac(ha_devices: list[dict]) -> dict[str, dict]:
    """Home Assistant's devices by each MAC in their connections."""
    out = {}
    for d in ha_devices:
        for kind, value in d.get("connections") or []:
            if kind == "mac":
                out[norm_mac(value)] = d
    return out


def first_answer(schema: list[dict], host: str, fields: dict) -> dict:
    """The answer to a flow's first step: the host, the entry's fields the form asks
    for, and an empty value for each collapsed section (Home Assistant requires them)."""
    names = {f["name"] for f in schema}
    answer = {"host": host, **{k: v for k, v in fields.items() if k in names}}
    for f in schema:
        if f.get("type") == "expandable" and f["name"] not in answer:
            answer[f["name"]] = {}
    return answer


def run_flow(ha, entry: dict, host: str, environ=os.environ) -> tuple[str, str]:
    """Run entry's integration's setup flow for host: ("added", title), ("present",
    reason), ("waiting", what it asks for) or ("failed", why). A flow left on a form is
    closed, so nothing half-made stays open in Home Assistant."""
    r = ha.flow("", {"handler": entry["handler"], "show_advanced_options": False})
    answered_host = False
    flow_id = None
    for _ in range(5):
        kind = r.get("type")
        if kind == "create_entry":
            return "added", r.get("title") or entry["handler"]
        if kind == "abort":
            reason = r.get("reason", "")
            return ("present" if reason in ("already_configured", "already_in_progress") else "failed"), reason
        if kind == "no_answer":
            if r.get("flow_id") is None and flow_id:
                ha.flow(f"/{flow_id}", method="DELETE")
            return "failed", r.get("reason") or "no answer"
        if kind != "form":
            return "failed", f"flow answered {kind}"
        flow_id = r["flow_id"]
        schema = r.get("data_schema") or []
        names = {f["name"] for f in schema}
        if r.get("errors"):
            ha.flow(f"/{r['flow_id']}", method="DELETE")
            return "failed", ", ".join(f"{k}: {v}" for k, v in r["errors"].items())
        if "host" in names and not answered_host:
            answer = first_answer(schema, host, entry.get("fields") or {})
            answered_host = True
        elif {"username", "password"} <= names:
            login = entry.get("login") or {}
            user, password = environ.get(login.get("username", ""), ""), environ.get(login.get("password", ""), "")
            if not (user and password):
                ha.flow(f"/{r['flow_id']}", method="DELETE")
                names_ = " and ".join(v for v in (login.get("username"), login.get("password")) if v)
                return "waiting", f"a login ({names_ or 'none configured'}), or finish it in the UI"
            answer = {"username": user, "password": password}
        else:
            ha.flow(f"/{r['flow_id']}", method="DELETE")
            return "waiting", f"step {r.get('step_id')} ({', '.join(sorted(names)) or 'a confirmation'}): finish it in the UI"
        r = ha.flow(f"/{r['flow_id']}", answer)
    ha.flow(f"/{r['flow_id']}", method="DELETE")
    return "failed", "too many steps"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(Path(__file__).with_suffix(".yaml")),
                    help="which integration adds which device (ha_iot_setup.yaml)")
    ap.add_argument("--netbox-url", default=os.environ.get("NETBOX_API") or None,
                    required=not os.environ.get("NETBOX_API"), help="https://netbox.<domain> (default: $NETBOX_API)")
    ha_url_file = Path.home() / ".ha-url"
    ha_url = os.environ.get("HA_URL") or (ha_url_file.read_text(encoding="utf-8").strip() if ha_url_file.is_file() else None)
    ap.add_argument("--ha-url", default=ha_url, required=not ha_url,
                    help="Home Assistant's URL (default: $HA_URL, else ~/.ha-url)")
    ap.add_argument("--apply", action="store_true", help="add devices and set areas (default: report only)")
    ap.add_argument("--attended", action="store_true",
                    help="also run the setups that need someone at the device (ha_iot_setup.yaml's attended)")
    args, extras = ap.parse_known_args()
    # One line at a time, as it happens: Semaphore's log otherwise shows the whole run at its end.
    sys.stdout.reconfigure(line_buffering=True)
    if extras:
        raise SystemExit(unexpected(extras))

    integrations = YAML(typ="safe").load(Path(args.config).read_text(encoding="utf-8"))["integrations"]
    nb = NetBox(args.netbox_url, read_secret(".netbox-token", "NETBOX_TOKEN"), apply=False)
    nb_devices = nb.all("dcim/devices", description=IOT_DEVICE_MARK)
    devices = netbox_iot_devices(nb_devices, nb.all("dcim/interfaces", name="wifi0") if nb_devices else [])
    if not devices:
        raise SystemExit("FAIL: NetBox lists no IoT device (netbox_seed.py's); nothing to compare against")
    ha = HomeAssistant(args.ha_url, read_secret(".ha-token", "HA_TOKEN"))
    by_mac = ha_device_by_mac(ha.devices())
    print(f"read: {len(devices)} IoT devices in NetBox, {len(by_mac)} MACs in Home Assistant's devices")
    print("dry run: nothing is changed" if not args.apply else "applying")

    failed = 0
    print("integrations:")
    added = []
    for d in devices:
        if not (d["host"] and d["mac"]):
            print(f"  {d['name']}: NetBox has no {'address' if not d['host'] else 'MAC'} for it")
            failed += 1
            continue
        action, entry = plan_device(d, integrations, by_mac, args.attended)
        if action in ("present", "cloud"):
            continue
        if action == "attended":
            print(f"  waiting: {d['name']} ({entry['handler']}): needs someone at the device; run with --attended")
            continue
        if not args.apply:
            print(f"  would add {d['name']} ({entry['handler']})")
            continue
        print(f"  starting {d['name']} ({entry['handler']})"
              + (": approve the prompt on its screen now (it must be on and showing)" if entry.get("attended") else ""))
        status, detail = run_flow(ha, entry, d["host"])
        print(f"  {status}: {d['name']} ({entry['handler']}): {detail}")
        if status == "added":
            added.append(d)
        failed += status == "failed"
    if args.apply and added:
        by_mac = ha_device_by_mac(ha.devices())
        for d in added:
            if norm_mac(d["mac"]) not in by_mac:
                print(f"  FAIL: {d['name']}: its entry was made, but no Home Assistant device carries its MAC")
                failed += 1
        print(f"  read back: {sum(norm_mac(d['mac']) in by_mac for d in added)} of {len(added)} added devices found")

    print("areas:")
    areas = {a["name"].casefold(): a for a in ha.areas()}
    placed = []
    for d in devices:
        ha_dev = by_mac.get(norm_mac(d["mac"] or ""))
        if ha_dev is None:
            continue
        want = d["room"]
        if want is None:
            print(f"  {d['name']}: no Location in NetBox")
            continue
        have = ha_dev.get("area_id")
        area = areas.get(want.casefold())
        if have:
            if area is None or have != area["area_id"]:
                print(f"  kept: {d['name']} stays in the area set in Home Assistant, not {want}")
            continue
        if not args.apply:
            print(f"  would put {d['name']} in {want}" + ("" if area else " (a new area)"))
            continue
        if area is None:
            area = ha.call("config/area_registry/create", name=want)
            areas[want.casefold()] = area
            print(f"  made area {want}")
        ha.call("config/device_registry/update", device_id=ha_dev["id"], area_id=area["area_id"])
        placed.append((ha_dev["id"], area["area_id"], d["name"], want))
    if placed:
        now = {x["id"]: x.get("area_id") for x in ha.devices()}
        for dev_id, area_id, name, want in placed:
            ok = now.get(dev_id) == area_id
            print(f"  {'put' if ok else 'FAIL: not in its area after the update:'} {name} in {want}")
            failed += not ok
    if failed:
        raise SystemExit(f"FAIL: {failed} device(s) failed; see above")


if __name__ == "__main__":
    main()
