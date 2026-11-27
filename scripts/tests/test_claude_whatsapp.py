"""claude-whatsapp.py against a fake Claude and a fake Graph API: Meta's signature gates every
POST, only allowed numbers reach Claude, a retried delivery is answered once, a conversation
carries its follow-ups and starts over when it should, and an exchange that failed or was
refused is never kept in it."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import io
import json
import sys
import threading
import types
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = (Path(__file__).resolve().parents[2] / "kubernetes" / "prod" / "apps" / "claude-whatsapp"
          / "claude-whatsapp" / "app" / "claude-whatsapp.py")
SECRET = ("app-" + "secret-test-value").encode()
VERIFY = "verify-" + "test-value"
OWNER = "15550001111"  # 555 numbers are fictional
STRANGER = "15550002222"


def fake_anthropic() -> types.ModuleType:
    """The SDK's surface the script touches: its client and the error classes it catches."""
    mod = types.ModuleType("anthropic")

    class APIError(Exception):
        pass

    class APIStatusError(APIError):
        def __init__(self, status_code=500):
            super().__init__(status_code)
            self.status_code = status_code

    class RateLimitError(APIStatusError):
        def __init__(self):
            super().__init__(429)

    class APIConnectionError(APIError):
        pass

    mod.APIError, mod.APIStatusError, mod.RateLimitError = APIError, APIStatusError, RateLimitError
    mod.APIConnectionError = APIConnectionError
    mod.Anthropic = lambda **kw: SimpleNamespace()
    return mod


def load():
    previous = sys.modules.get("anthropic")
    sys.modules["anthropic"] = fake_anthropic()
    try:
        spec = importlib.util.spec_from_file_location("claude_whatsapp_under_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # dataclasses look their module up while it loads
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = previous
    return module


m = load()


def text(t):
    return SimpleNamespace(type="text", text=t)


def response(*blocks, stop="end_turn", category=None):
    details = SimpleNamespace(category=category) if stop == "refusal" else None
    return SimpleNamespace(content=list(blocks), stop_reason=stop, stop_details=details)


class FakeClaude:
    """client.beta.messages.create: answers from `script` in order (a response, or an
    exception to raise), recording each request's messages as sent."""

    def __init__(self, *script):
        self.script, self.requests = list(script), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        kwargs["messages"] = [dict(msg) for msg in kwargs["messages"]]  # as sent, not as later grown
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class FakeWhatsApp:
    def __init__(self, deliver=True):
        self.deliver, self.sent, self.read = deliver, [], []

    def mark_read(self, msg_id):
        self.read.append(msg_id)

    def send(self, to, body):
        self.sent.append((to, body))
        return self.deliver


def payload(*messages, statuses=()):
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{
        "field": "messages",
        "value": {"messaging_product": "whatsapp", "messages": list(messages), "statuses": list(statuses)}}]}]}


def message(msg_id, sender=OWNER, body="hello", kind="text"):
    msg = {"id": msg_id, "from": sender, "type": kind, "timestamp": "0"}
    if kind == "text":
        msg["text"] = {"body": body}
    return msg


def make_bot(*script, clock=None, max_turns=40, idle_minutes=30, web_search_uses=3):
    client = FakeClaude(*script)
    claude = m.Claude(client, model="claude-opus-5-5", effort="low", web_search_uses=web_search_uses)
    whatsapp = FakeWhatsApp()
    times = clock or iter(range(1_800_000_000, 1_900_000_000, 60))
    bot = m.Bot(claude, whatsapp, frozenset({OWNER}), m.Conversations(idle_minutes * 60, max_turns),
                timezone.utc, clock=lambda: next(times))
    return bot, client, whatsapp


def drain(bot):
    while not bot.work.empty():
        bot.handle(bot.work.get())


def sign(body: bytes, secret: bytes = SECRET) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


# --- Meta's signature ------------------------------------------------------------------

def test_a_body_signed_with_the_app_secret_passes():
    body = json.dumps(payload(message("wamid.1"))).encode()
    assert m.signature_ok(SECRET, body, sign(body))


@pytest.mark.parametrize("header", [None, "", "sha1=abc", sign(b"another body"),
                                    sign(b'{"object": "x"}', b"wrong-secret")])
def test_a_missing_wrong_or_foreign_signature_fails(header):
    assert not m.signature_ok(SECRET, b'{"object": "x"}', header)


# --- what a payload holds --------------------------------------------------------------

def test_messages_are_read_and_receipts_are_not():
    found = m.incoming_messages(payload(
        message("wamid.1", body="what's the weather"), message("wamid.2", kind="audio"),
        statuses=[{"id": "wamid.0", "status": "delivered"}]))
    assert found == [m.Incoming("wamid.1", OWNER, "text", "what's the weather"),
                     m.Incoming("wamid.2", OWNER, "audio", "")]


@pytest.mark.parametrize("data", [{}, {"object": "page", "entry": payload(message("x"))["entry"]},
                                  payload({"from": OWNER, "type": "text"}),
                                  payload({"id": "wamid.3", "type": "text"})])
def test_a_payload_that_is_not_a_whatsapp_message_yields_nothing(data):
    assert m.incoming_messages(data) == []


def test_allowed_numbers_are_digits_whatever_they_were_written_with():
    assert m.allowed_numbers("+1 (555) 000-1111, 15550002222;;") == frozenset({OWNER, STRANGER})
    assert m.allowed_numbers(" , ") == frozenset()


# --- who reaches Claude ----------------------------------------------------------------

def test_a_stranger_never_reaches_claude():
    bot, client, whatsapp = make_bot()
    assert bot.take(payload(message("wamid.1", sender=STRANGER))) == 0
    drain(bot)
    assert client.requests == [] and whatsapp.sent == []


def test_a_retried_delivery_is_answered_once():
    bot, client, whatsapp = make_bot(response(text("Hi.")))
    assert bot.take(payload(message("wamid.1"))) == 1
    assert bot.take(payload(message("wamid.1"))) == 0
    drain(bot)
    assert len(client.requests) == 1 and whatsapp.sent == [(OWNER, "Hi.")]


# --- conversations ---------------------------------------------------------------------

def test_a_follow_up_carries_the_conversation_so_far():
    bot, client, whatsapp = make_bot(response(text("Paris.")), response(text("About 2 million.")))
    bot.take(payload(message("wamid.1", body="capital of France?")))
    bot.take(payload(message("wamid.2", body="how many people live there?")))
    drain(bot)
    first, second = (r["messages"] for r in client.requests)
    assert [msg["role"] for msg in first] == ["user"]
    assert [msg["role"] for msg in second] == ["user", "assistant", "user"]
    assert second[0]["content"].endswith("capital of France?")
    assert second[1]["content"] == [text("Paris.")]  # Claude's own blocks, sent back unchanged
    assert whatsapp.sent == [(OWNER, "Paris."), (OWNER, "About 2 million.")]


def test_each_message_carries_the_time_it_was_sent():
    when = datetime(2026, 11, 27, 15, 4, tzinfo=timezone.utc)
    bot, client, _ = make_bot(response(text("ok")), clock=iter([when.timestamp()]))
    bot.take(payload(message("wamid.1", body="is it open?")))
    drain(bot)
    sent = client.requests[0]["messages"][0]["content"]
    assert sent == "(Friday, November 27, 2026, 3:04 PM UTC)\nis it open?"


@pytest.mark.parametrize("words", ["new chat", "Reset.", "START OVER"])
def test_reset_words_start_over_without_asking_claude(words):
    bot, client, whatsapp = make_bot(response(text("one")), response(text("two")))
    for i, body in enumerate(["first", words, "second"]):
        bot.take(payload(message(f"wamid.{i}", body=body)))
    drain(bot)
    assert len(client.requests) == 2
    assert [msg["role"] for msg in client.requests[1]["messages"]] == ["user"]
    assert whatsapp.sent[1] == (OWNER, "OK, starting a new conversation.")


def test_an_idle_conversation_starts_over():
    start = 1_800_000_000
    bot, client, _ = make_bot(response(text("one")), response(text("two")), response(text("three")),
                              clock=iter([start, start + 29 * 60, start + 29 * 60 + 31 * 60]))
    for i in range(3):
        bot.take(payload(message(f"wamid.{i}")))
    drain(bot)
    assert [len(r["messages"]) for r in client.requests] == [1, 3, 1]


def test_a_conversation_at_its_turn_limit_starts_over():
    bot, client, _ = make_bot(*(response(text(str(i))) for i in range(3)), max_turns=2)
    for i in range(3):
        bot.take(payload(message(f"wamid.{i}")))
    drain(bot)
    assert [len(r["messages"]) for r in client.requests] == [1, 3, 1]


# --- what Claude answers ---------------------------------------------------------------

def test_every_request_asks_for_fallbacks_effort_and_web_search():
    bot, client, _ = make_bot(response(text("ok")))
    bot.take(payload(message("wamid.1")))
    drain(bot)
    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5" and req["output_config"] == {"effort": "low"}
    assert req["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in req["betas"]
    assert req["tools"] == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}]
    assert "thinking" not in req  # Opus 5.5 always thinks; any explicit setting but adaptive is a 400


def test_web_search_off_sends_no_tools():
    bot, client, _ = make_bot(response(text("ok")), web_search_uses=0)
    bot.take(payload(message("wamid.1")))
    drain(bot)
    assert "tools" not in client.requests[0]


def test_a_paused_turn_is_resumed_and_kept_whole():
    searching = SimpleNamespace(type="server_tool_use", text=None)
    bot, client, whatsapp = make_bot(response(searching, stop="pause_turn"), response(text("Open until 9.")),
                                     response(text("You're welcome.")))
    bot.take(payload(message("wamid.1", body="is the library open?")))
    bot.take(payload(message("wamid.2", body="thanks")))
    drain(bot)
    resume = client.requests[1]["messages"]
    assert [msg["role"] for msg in resume] == ["user", "assistant"] and resume[1]["content"] == [searching]
    kept = client.requests[2]["messages"][1]["content"]
    assert kept == [searching, text("Open until 9.")]
    assert whatsapp.sent[0] == (OWNER, "Open until 9.")


@pytest.mark.parametrize("failure", [
    response(text("partial"), stop="refusal", category="cyber"),
    m.anthropic.RateLimitError(),
    m.anthropic.APIStatusError(529),
    m.anthropic.APIConnectionError(),
    response(stop="end_turn"),
])
def test_a_failed_or_refused_exchange_is_answered_but_never_kept(failure):
    bot, client, whatsapp = make_bot(failure, response(text("fine")))
    bot.take(payload(message("wamid.1", body="first")))
    bot.take(payload(message("wamid.2", body="second")))
    drain(bot)
    assert whatsapp.sent[0][1] and whatsapp.sent[0][1] != "partial"
    # The next request starts from the history as it was: nothing of the failed turn.
    assert [msg["role"] for msg in client.requests[1]["messages"]] == ["user"]


def test_a_reply_cut_at_max_tokens_says_so():
    bot, _, whatsapp = make_bot(response(text("A long answer"), stop="max_tokens"))
    bot.take(payload(message("wamid.1")))
    drain(bot)
    assert whatsapp.sent[0][1].startswith("A long answer") and "cut off" in whatsapp.sent[0][1]


def test_a_voice_note_is_answered_without_claude():
    bot, client, whatsapp = make_bot()
    bot.take(payload(message("wamid.1", kind="audio")))
    drain(bot)
    assert client.requests == [] and whatsapp.sent == [(OWNER, "I can only read text messages.")]
    assert whatsapp.read == ["wamid.1"]


# --- sending ---------------------------------------------------------------------------

def test_a_long_reply_is_split_under_the_limit_and_loses_nothing():
    long = " ".join(f"Sentence number {i} is here." for i in range(600))
    parts = m.split(long)
    assert len(parts) > 1 and all(len(p) <= m.CHUNK for p in parts)
    assert " ".join(parts).split() == long.split()


class FakeGraph:
    def __init__(self, answers):
        self.answers, self.bodies = list(answers), []

    def __call__(self, req, timeout):
        self.bodies.append(json.loads(req.data))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())


def test_send_is_true_only_when_meta_returns_a_message_id(monkeypatch):
    graph = FakeGraph([{"messages": [{"id": "wamid.out"}]}, {"messages": []},
                       urllib.error.HTTPError("u", 401, "x", {}, io.BytesIO(b'{"error": {"code": 190}}'))])
    monkeypatch.setattr(m, "urlopen", graph)
    wa = m.WhatsApp("token", "123", "v26.0")
    assert wa.send(OWNER, "one") is True
    assert wa.send(OWNER, "two") is False
    assert wa.send(OWNER, "three") is False
    assert graph.bodies[0] == {"messaging_product": "whatsapp", "recipient_type": "individual", "to": OWNER,
                               "type": "text", "text": {"preview_url": False, "body": "one"}}


# --- the HTTP side ---------------------------------------------------------------------

@pytest.fixture
def server():
    bot, _, _ = make_bot()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), m.make_handler(bot, SECRET, VERIFY))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield bot, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def call(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, ""


def test_the_subscription_check_answers_only_the_right_token(server):
    _, base = server
    assert call(f"{base}/webhook?hub.mode=subscribe&hub.verify_token={VERIFY}&hub.challenge=42") == (200, "42")
    assert call(f"{base}/webhook?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=42")[0] == 403
    assert call(f"{base}/healthz") == (200, "ok")
    assert call(f"{base}/other")[0] == 404


def test_only_a_signed_post_is_read(server):
    bot, base = server
    body = json.dumps(payload(message("wamid.1"))).encode()
    assert call(f"{base}/webhook", body, {"X-Hub-Signature-256": sign(body, b"wrong")})[0] == 403
    assert call(f"{base}/webhook", body, {})[0] == 403
    assert bot.work.empty()
    assert call(f"{base}/webhook", body, {"X-Hub-Signature-256": sign(body)}) == (200, "ok")
    assert bot.work.qsize() == 1


# --- start ---------------------------------------------------------------------------

def test_it_will_not_start_without_its_settings(monkeypatch, capsys):
    for name in m.REQUIRED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "set")
    assert m.main() == 1
    out = capsys.readouterr().out
    assert "WHATSAPP_APP_SECRET" in out and "ANTHROPIC_API_KEY" not in out


def test_it_will_not_start_with_no_allowed_number(monkeypatch, capsys):
    for name in m.REQUIRED:
        monkeypatch.setenv(name, "set")
    monkeypatch.setenv("ALLOWED_NUMBERS", " , ")
    assert m.main() == 1
    assert "ALLOWED_NUMBERS holds no phone number" in capsys.readouterr().out


def test_sent_at_reads_as_a_listener_expects():
    morning = datetime(2026, 1, 5, 0, 7, tzinfo=timezone(timedelta(hours=-5), "EST"))
    assert m.sent_at(morning) == "Monday, January 5, 2026, 12:07 AM EST"
