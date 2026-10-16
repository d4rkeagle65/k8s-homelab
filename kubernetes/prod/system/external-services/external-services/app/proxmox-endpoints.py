"""Keeps the proxmox Service's EndpointSlice to the Proxmox hosts that answer.

Every INTERVAL seconds: reads the hosts from NetBox (active devices with the
role NETBOX_ROLE, by their primary IPv4), asks each one's API on PORT, and
writes the ones that answered as the EndpointSlice of SERVICE, so Traefik only
sends requests to those. A host that stops answering drops out within one
interval; one that comes back returns the same way.

When NetBox can't be read, or answers with no hosts, the last list it gave is
kept and still checked: a failed lookup is not an empty cluster. With no list
yet, nothing is written. Each write is read back before it counts.

Standard library only (the image is plain Python). Settings come from the
environment: NETBOX_URL, NETBOX_TOKEN ("nbt_..." with or without "Bearer "),
NETBOX_ROLE, SERVICE, PORT, INTERVAL, HEARTBEAT (a file touched after every
pass, for the liveness probe).
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SA = Path("/var/run/secrets/kubernetes.io/serviceaccount")
API = "https://kubernetes.default.svc"
MANAGER = "proxmox-endpoints"


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), msg, flush=True)


def netbox_hosts(url: str, token: str, role: str) -> list[str]:
    """The primary IPv4 address of every active device with that role, sorted.
    Raises on any failure, so a refused or broken answer never reads as none."""
    token = token.removeprefix("Bearer ").strip()
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/dcim/devices/?role={role}&status=active&limit=1000",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        body = json.load(r)
    return addresses(body["results"])


def addresses(devices: list[dict]) -> list[str]:
    """The devices' primary IPv4 addresses, without their prefix length, sorted;
    a device without one is left out."""
    out = {(d.get("primary_ip4") or {}).get("address", "").split("/")[0] for d in devices}
    return sorted(a for a in out if a)


def refresh(known: list[str], fetch, role: str) -> list[str]:
    """The host list for this pass: what fetch() returns, unless it fails or
    returns nothing, when the last list stays."""
    try:
        found = fetch()
    except Exception as e:  # noqa: BLE001 - any failure keeps the last list
        log(f"NetBox not read ({type(e).__name__}: {e}); keeping the last list ({len(known)})")
        return known
    if not found:
        log(f"NetBox lists no active device with role {role}; keeping the last list ({len(known)})")
        return known
    if found != known:
        log(f"NetBox lists {len(found)} host(s) with role {role}")
    return found


def problem(addr: str, port: int, timeout: float = 3.0) -> str | None:
    """Why the host's web server isn't serving, or None when it is: any HTTP
    response below 500 for its login page, which needs no ticket (the API's
    unauthenticated 401 is held back on purpose, to slow password guessing, and
    can outlast the timeout). Its certificate is for its name, not this address,
    so it isn't checked here."""
    ctx = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(f"https://{addr}:{port}/", timeout=timeout, context=ctx):
            return None
    except urllib.error.HTTPError as e:
        return None if e.code < 500 else f"HTTP {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return str(getattr(e, "reason", e)) or type(e).__name__


def endpoint_slice(namespace: str, service: str, port: int, up: list[str]) -> dict:
    """The EndpointSlice listing the hosts that answered, all ready."""
    return {
        "apiVersion": "discovery.k8s.io/v1",
        "kind": "EndpointSlice",
        "metadata": {
            "name": service,
            "namespace": namespace,
            "labels": {"kubernetes.io/service-name": service,
                       "endpointslice.kubernetes.io/managed-by": MANAGER},
        },
        "addressType": "IPv4",
        "ports": [{"name": "https", "port": port, "protocol": "TCP"}],
        "endpoints": [{"addresses": [a], "conditions": {"ready": True}} for a in up],
    }


def kube(method: str, path: str, body: dict | None = None, content_type: str = "application/json") -> dict:
    token = (SA / "token").read_text(encoding="utf-8").strip()
    ctx = ssl.create_default_context(cafile=str(SA / "ca.crt"))
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": content_type,
                                          "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
        return json.load(r)


def written(obj: dict) -> list[str]:
    """The addresses an EndpointSlice lists, sorted."""
    return sorted(a for e in obj.get("endpoints") or [] for a in e.get("addresses", []))


def main() -> None:
    url, token = os.environ["NETBOX_URL"], os.environ["NETBOX_TOKEN"]
    role, service = os.environ.get("NETBOX_ROLE", "hypervisor"), os.environ.get("SERVICE", "proxmox")
    port, interval = int(os.environ.get("PORT", "8006")), float(os.environ.get("INTERVAL", "15"))
    heartbeat = Path(os.environ.get("HEARTBEAT", "/tmp/alive"))
    namespace = (SA / "namespace").read_text(encoding="utf-8").strip()
    path = f"/apis/discovery.k8s.io/v1/namespaces/{namespace}/endpointslices/{service}"
    known: list[str] = []
    current: list[str] | None = None
    with ThreadPoolExecutor(max_workers=16) as pool:
        while True:
            known = refresh(known, lambda: netbox_hosts(url, token, role), role)
            if known:
                problems = dict(zip(known, pool.map(lambda a: problem(a, port), known)))
                up = [a for a in known if problems[a] is None]
                if up != current:
                    for a in known:
                        if problems[a] is not None and (current is None or a in current):
                            log(f"{a} left out: {problems[a]}")
                    try:
                        kube("PATCH", f"{path}?fieldManager={MANAGER}&force=true",
                             endpoint_slice(namespace, service, port, up), "application/apply-patch+yaml")
                        back = written(kube("GET", path))
                        if back == up:
                            log(f"{len(up)} of {len(known)} host(s) answering: {', '.join(up) or 'none'}")
                            current = up
                        else:
                            log(f"wrote {up} but read back {back}; trying again next pass")
                    except Exception as e:  # noqa: BLE001 - the next pass tries again
                        log(f"EndpointSlice not written ({type(e).__name__}: {e})")
            heartbeat.touch()
            time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyError as e:
        sys.exit(f"missing setting {e}")
