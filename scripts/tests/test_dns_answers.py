"""ansible/files/dns-answers.py: an A query for NAME to each address in turn, the first answer
with a record ending it. Against a stand-in socket that reads the query and answers as told."""

from __future__ import annotations

import importlib.util
import struct
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "ansible/files/dns-answers.py"


def load():
    spec = importlib.util.spec_from_file_location("dns_answers_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


m = load()


def reply(query: bytes, *, same_id=True, rcode=0, answers=1, short=False):
    qid = struct.unpack(">H", query[:2])[0]
    if short:
        return b"\x00\x01"
    return struct.pack(">HHHHHH", qid if same_id else (qid + 1) % 0x10000, 0x8180 | rcode, 1, answers, 0, 0)


class Net:
    """Each address's behaviour: a reply builder, or an exception to raise."""

    def __init__(self, behaviour):
        self.behaviour, self.asked = behaviour, []

    def socket(self, family, kind):
        net = self

        class Sock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def settimeout(self, t):
                self.timeout = t

            def sendto(self, packet, where):
                net.asked.append((where, packet, self.timeout))
                self.where, self.packet = where, packet

            def recvfrom(self, n):
                b = net.behaviour[self.where[0]]
                if isinstance(b, Exception):
                    raise b
                return b(self.packet), self.where

        return Sock()


@pytest.fixture
def net(monkeypatch):
    def make(behaviour):
        n = Net(behaviour)
        monkeypatch.setattr(m.socket, "socket", n.socket)
        return n
    return make


def test_the_query_asks_for_an_a_record_with_recursion(net):
    n = net({"192.0.2.53": lambda q: reply(q)})
    assert m.ask("192.0.2.53", "nas.home.example.") is None
    (where, packet, timeout), = n.asked
    assert where == ("192.0.2.53", 53) and timeout == m.TIMEOUT_SECONDS
    assert packet[2:12] == struct.pack(">HHHHH", 0x0100, 1, 0, 0, 0)
    assert packet[12:] == b"\x03nas\x04home\x07example\x00" + struct.pack(">HH", 1, 1)


@pytest.mark.parametrize("behaviour, problem", [
    (lambda q: reply(q, short=True), "a reply too short to read"),
    (lambda q: reply(q, same_id=False), "a reply to a different query"),
    (lambda q: reply(q, rcode=3), "an error reply (rcode 3)"),
    (lambda q: reply(q, answers=0), "a reply with no records"),
    (TimeoutError("timed out"), "no reply (timed out)"),
])
def test_each_way_an_address_fails_to_answer_is_named(net, behaviour, problem):
    net({"192.0.2.53": behaviour})
    assert m.ask("192.0.2.53", "nas.home.example") == problem


def test_main_stops_at_the_first_address_that_answers(net, capsys):
    n = net({"192.0.2.1": TimeoutError("timed out"), "192.0.2.2": lambda q: reply(q),
             "192.0.2.3": lambda q: reply(q)})
    assert m.main(["dns-answers.py", "nas.home.example", "192.0.2.1", "192.0.2.2", "192.0.2.3"]) == 0
    assert [w[0] for w, _, _ in n.asked] == ["192.0.2.1", "192.0.2.2"]
    out = capsys.readouterr().out
    assert "192.0.2.1: no reply (timed out)" in out and "192.0.2.2 answers nas.home.example" in out


def test_main_answers_1_when_none_answers_and_2_without_its_arguments(net, capsys):
    net({"192.0.2.1": lambda q: reply(q, answers=0), "192.0.2.2": lambda q: reply(q, rcode=2)})
    assert m.main(["dns-answers.py", "nas.home.example", "192.0.2.1", "192.0.2.2"]) == 1
    assert m.main(["dns-answers.py", "nas.home.example"]) == 2
    assert "dns-answers.py NAME ADDRESS" in capsys.readouterr().err
