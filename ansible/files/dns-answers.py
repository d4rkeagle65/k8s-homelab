#!/usr/bin/env python3
"""Ask each address in turn for NAME (an A record, over UDP port 53).

Exits 0 at the first address that answers with a record, and 1 when none does,
printing what each address said. Standard library only: it runs on the Proxmox
hosts, which have no DNS tools.

    dns-answers.py NAME ADDRESS [ADDRESS ...]
"""

from __future__ import annotations

import random
import socket
import struct
import sys

TIMEOUT_SECONDS = 2.0


def _query(name: str) -> tuple[int, bytes]:
    qid = random.randint(0, 0xFFFF)
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)  # recursion desired, 1 question
    labels = b"".join(bytes([len(p)]) + p.encode("ascii") for p in name.rstrip(".").split("."))
    return qid, header + labels + b"\0" + struct.pack(">HH", 1, 1)  # type A, class IN


def ask(address: str, name: str) -> str | None:
    """None when the address answered NAME with a record, else what went wrong."""
    qid, packet = _query(name)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(TIMEOUT_SECONDS)
            sock.sendto(packet, (address, 53))
            reply, _ = sock.recvfrom(4096)
    except OSError as exc:
        return f"no reply ({exc or type(exc).__name__})"
    if len(reply) < 12:
        return "a reply too short to read"
    rid, flags, _, answers, _, _ = struct.unpack(">HHHHHH", reply[:12])
    if rid != qid:
        return "a reply to a different query"
    if flags & 0x000F:
        return f"an error reply (rcode {flags & 0x000F})"
    if answers == 0:
        return "a reply with no records"
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    name, addresses = argv[1], argv[2:]
    for address in addresses:
        problem = ask(address, name)
        if problem is None:
            print(f"{address} answers {name}")
            return 0
        print(f"{address}: {problem}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
