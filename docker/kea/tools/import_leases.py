#!/usr/bin/env python3
"""Load the leases dnsmasq_to_kea.py wrote into a running Kea, through its API.

Each lease goes in with lease4-update and force-create, so running it twice
is harmless. Run it right after Kea starts and before the relay points at
Kea, so every client keeps its address.

Usage:
  python import_leases.py --url http://<kea api address>:8000/ \
         --credentials-file <file with one user:password line> leases-import.json
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.request
from pathlib import Path


def call(url: str, auth: str, command: str, arguments: dict) -> dict:
    body = json.dumps({"command": command, "arguments": arguments}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        answer = json.load(resp)
    # A daemon's own socket answers with one object; the old control agent
    # with a list of them.
    return answer[0] if isinstance(answer, list) else answer


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", required=True)
    ap.add_argument("--credentials-file", required=True, type=Path)
    ap.add_argument("leases", type=Path)
    args = ap.parse_args(argv)

    auth = base64.b64encode(args.credentials_file.read_text().strip().encode()).decode()
    leases = json.loads(args.leases.read_text())
    ok, failed = 0, []
    for lease in leases:
        answer = call(args.url, auth, "lease4-update", {**lease, "force-create": True})
        if answer.get("result") == 0:
            ok += 1
        else:
            failed.append((lease["ip-address"], answer.get("text")))
    print(f"imported {ok} of {len(leases)} leases")
    for ip, text in failed:
        print(f"FAILED {ip}: {text}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
