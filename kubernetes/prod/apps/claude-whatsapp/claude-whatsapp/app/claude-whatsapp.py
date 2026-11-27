"""Claude over WhatsApp: a webhook for Meta's WhatsApp Cloud API that answers each text
from an allowed number with Claude's reply, so a phone's voice assistant (Gemini on
Android Auto: "message Claude on WhatsApp") can talk to Claude.

Meta POSTs every incoming message to /webhook. The POST's X-Hub-Signature-256 must be the
HMAC-SHA256 of its body under the app secret, or it is refused with 403 and nothing in it
is read. A message from a number not in ALLOWED_NUMBERS is dropped and never reaches
Claude. Each POST is answered 200 at once and the reply is written on a worker thread,
since Meta retries a webhook that is slow to answer; a retried message (the same id) is
answered once.

Each number keeps one conversation in memory, so follow-up questions work. It starts over
after IDLE_MINUTES without a message, after MAX_TURNS exchanges, on "new chat" or
"reset", and when the pod restarts. A conversation is only ever appended to, never
trimmed: Claude's thinking blocks stay valid only in the unedited conversation that
produced them.

GET /webhook answers Meta's subscription check (hub.verify_token must equal
WHATSAPP_VERIFY_TOKEN); GET /healthz answers 200. Message text, phone numbers and keys
are never logged. Configuration is the environment, read once at start; a required
value that is missing stops it before it listens.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import anthropic

REQUIRED = (
    "ANTHROPIC_API_KEY",
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_APP_SECRET",
    "WHATSAPP_VERIFY_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID",
    "ALLOWED_NUMBERS",
)
GRAPH_URL = "https://graph.facebook.com"
MAX_BODY = 1_000_000  # bytes; Meta's webhook payloads are a few KB
CHUNK = 4000  # characters per reply message, under WhatsApp's 4096 for one text
MAX_CONTINUATIONS = 5  # resumes of a paused turn (a long web search) before giving up
RESET_WORDS = {"new chat", "reset", "start over", "new conversation"}

SYSTEM_PROMPT = """\
You are Claude, answering WhatsApp messages from one person. They are often driving: \
their phone's assistant reads your reply aloud and dictated their message, so expect \
transcription mistakes and read past them.

Write for the ear:
- Plain sentences only. No markdown, bullet points, tables, emoji, or URLs.
- Lead with the answer. Two to four sentences is the usual length; go longer only when \
asked.
- Spell out what a listener needs: say "about 20 minutes", not "~20 min".
- If a question can't be answered well without a detail you lack (where they are, which \
one they mean), ask one short question back.

Each message starts with the time it was sent in parentheses; use it for questions about \
today, opening hours or schedules. Search the web when the answer depends on current \
information, and say briefly when you couldn't find something."""


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), msg, flush=True)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def digits(number: str) -> str:
    """A phone number as WhatsApp's wa_id writes it: country code and number, digits only."""
    return re.sub(r"\D", "", number)


def mask(number: str) -> str:
    """Enough of a number to tell two apart in the log, never the whole of it."""
    return "..." + number[-2:] if number else "(none)"


def allowed_numbers(raw: str) -> frozenset[str]:
    """The numbers in a list split by commas, semicolons or new lines; spaces, dashes and
    brackets inside a number are only formatting."""
    return frozenset(d for d in (digits(part) for part in re.split(r"[,;\n]+", raw)) if d)


def signature_ok(secret: bytes, body: bytes, header: str | None) -> bool:
    """Meta signs each POST: X-Hub-Signature-256 is "sha256=" and the HMAC-SHA256 of the
    raw body under the app secret, in hex."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):].strip().lower())


@dataclass(frozen=True)
class Incoming:
    id: str
    sender: str  # digits
    kind: str  # "text", "audio", "image", ...
    text: str  # the body of a text message, else ""


def incoming_messages(payload: dict) -> list[Incoming]:
    """The messages in a webhook payload (object whatsapp_business_account, field
    messages). Delivery and read receipts (value.statuses) are not messages and are left
    out, as is anything without an id or a sender."""
    found = []
    if payload.get("object") != "whatsapp_business_account":
        return found
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            for msg in (change.get("value") or {}).get("messages") or []:
                msg_id, sender = msg.get("id"), digits(str(msg.get("from") or ""))
                if not msg_id or not sender:
                    continue
                kind = msg.get("type") or "unknown"
                text = (msg.get("text") or {}).get("body", "") if kind == "text" else ""
                found.append(Incoming(msg_id, sender, kind, text))
    return found


class Seen:
    """The ids of the last `size` messages taken, so a retried delivery is answered once."""

    def __init__(self, size: int = 2000):
        self.size, self.ids, self.lock = size, OrderedDict(), threading.Lock()

    def first_time(self, msg_id: str) -> bool:
        with self.lock:
            if msg_id in self.ids:
                return False
            self.ids[msg_id] = None
            while len(self.ids) > self.size:
                self.ids.popitem(last=False)
            return True


@dataclass
class Conversation:
    messages: list = field(default_factory=list)
    last: float = 0.0

    @property
    def turns(self) -> int:
        return sum(1 for m in self.messages if m["role"] == "user")


class Conversations:
    """One conversation per number, started over when it has been idle `idle_seconds` or
    has had `max_turns` exchanges."""

    def __init__(self, idle_seconds: float, max_turns: int):
        self.idle_seconds, self.max_turns = idle_seconds, max_turns
        self.by_sender: dict[str, Conversation] = {}

    def current(self, sender: str, now: float) -> Conversation:
        conv = self.by_sender.get(sender)
        if conv is None or now - conv.last > self.idle_seconds or conv.turns >= self.max_turns:
            conv = self.by_sender[sender] = Conversation()
        conv.last = now
        return conv

    def reset(self, sender: str) -> None:
        self.by_sender.pop(sender, None)


def sent_at(now: datetime) -> str:
    """The time a message was sent, as the prompt says each message starts with.
    Built by hand: strftime's unpadded %-d and %-I don't exist on every platform."""
    hour = now.hour % 12 or 12
    return (f"{now:%A}, {now:%B} {now.day}, {now.year}, {hour}:{now:%M} "
            f"{'AM' if now.hour < 12 else 'PM'} {now.tzname() or ''}").rstrip()


def text_of(content) -> str:
    return "\n\n".join(b.text.strip() for b in content if getattr(b, "type", None) == "text" and b.text.strip())


@dataclass
class Reply:
    text: str
    # The assistant turn to append to the conversation, or None when the exchange must not
    # be kept (an error, or a refusal by every model the request could fall back to).
    assistant: list | None


class Claude:
    def __init__(self, client, model: str, effort: str, web_search_uses: int):
        self.client, self.model, self.effort = client, model, effort
        self.tools = ([{"type": "web_search_20260209", "name": "web_search", "max_uses": web_search_uses}]
                      if web_search_uses > 0 else [])

    def _create(self, messages: list):
        kwargs = dict(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=messages,
            output_config={"effort": self.effort},
            # Auto-caching: a long conversation re-reads its earlier turns from the cache.
            cache_control={"type": "ephemeral"},
            # A refused request is re-run by the API on a model chosen for the refusal's
            # category, inside the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if self.tools:
            kwargs["tools"] = self.tools
        return self.client.beta.messages.create(**kwargs)

    def answer(self, history: list, user_text: str) -> Reply:
        messages = history + [{"role": "user", "content": user_text}]
        content: list = []
        try:
            response = self._create(messages)
            content = list(response.content)
            # A turn paused by a long server-side tool run is resumed by sending it back.
            for _ in range(MAX_CONTINUATIONS):
                if response.stop_reason != "pause_turn":
                    break
                response = self._create(messages + [{"role": "assistant", "content": content}])
                content = content + list(response.content)
        except anthropic.RateLimitError:
            log("claude: rate limited")
            return Reply("Claude is busy right now. Try again in a minute.", None)
        except anthropic.APIStatusError as e:
            log(f"claude: API error {e.status_code} {type(e).__name__}")
            return Reply("Something went wrong reaching Claude. Try again in a minute.", None)
        except anthropic.APIConnectionError as e:
            log(f"claude: no connection ({type(e).__name__})")
            return Reply("I couldn't reach Claude just now. Try again in a minute.", None)

        stop = response.stop_reason
        if stop == "refusal":
            category = getattr(getattr(response, "stop_details", None), "category", None)
            log(f"claude: refused (category {category})")
            return Reply("Claude can't help with that one.", None)
        text = text_of(content)
        if stop == "pause_turn":
            log(f"claude: still paused after {MAX_CONTINUATIONS} resumes")
            return Reply(text or "That took too long to look up. Try asking a narrower question.", None)
        if not text:
            log(f"claude: no text in the reply (stop {stop})")
            return Reply("Claude didn't come back with an answer. Try asking another way.", None)
        if stop == "max_tokens":
            log("claude: reply cut at max_tokens")
            text += "\n\n(That's where the answer was cut off.)"
        return Reply(text, content)


class WhatsApp:
    def __init__(self, token: str, phone_number_id: str, version: str):
        self.token, self.base = token, f"{GRAPH_URL}/{version}/{phone_number_id}/messages"

    def _post(self, body: dict) -> dict | None:
        req = Request(
            self.base, data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            # Meta's error object: code and type say what's wrong; its message can quote
            # the request, so it stays out of the log.
            try:
                err = json.load(e).get("error", {})
            except (ValueError, AttributeError):
                err = {}
            log(f"whatsapp: HTTP {e.code} (error {err.get('code')}/{err.get('error_subcode')} {err.get('type')})")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            log(f"whatsapp: no connection ({type(e).__name__})")
        return None

    def mark_read(self, msg_id: str) -> None:
        """Ticks the message blue and shows "typing..." until the reply arrives (or for
        25 seconds). Only a courtesy: failing it changes nothing else."""
        self._post({"messaging_product": "whatsapp", "status": "read", "message_id": msg_id,
                    "typing_indicator": {"type": "text"}})

    def send(self, to: str, text: str) -> bool:
        """Sends text in as many messages as WhatsApp's limit needs; True only when Meta
        returned a message id for every one."""
        ok = True
        for part in split(text):
            result = self._post({"messaging_product": "whatsapp", "recipient_type": "individual",
                                 "to": to, "type": "text", "text": {"preview_url": False, "body": part}})
            if not (result and (result.get("messages") or [{}])[0].get("id")):
                log(f"whatsapp: no message id for a reply to {mask(to)}")
                ok = False
                break
        return ok


def split(text: str, size: int = CHUNK) -> list[str]:
    """Text cut into parts of at most `size` characters, at a paragraph, line or sentence
    break where one falls in the second half of the part."""
    parts, rest = [], text.strip()
    while len(rest) > size:
        window = rest[:size]
        cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(". "))
        cut = cut + 1 if cut >= size // 2 else size
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        parts.append(rest)
    return parts


class Bot:
    def __init__(self, claude: Claude, whatsapp: WhatsApp, allowed: frozenset[str],
                 conversations: Conversations, tz, clock=time.time):
        self.claude, self.whatsapp, self.allowed = claude, whatsapp, allowed
        self.conversations, self.tz, self.clock = conversations, tz, clock
        self.seen = Seen()
        self.work: queue.Queue[Incoming] = queue.Queue()

    def take(self, payload: dict) -> int:
        """Queues each new message from an allowed number; returns how many."""
        taken = 0
        for msg in incoming_messages(payload):
            if msg.sender not in self.allowed:
                log(f"dropped {msg.kind} {msg.id[-8:]} from {mask(msg.sender)}: not an allowed number")
                continue
            if not self.seen.first_time(msg.id):
                log(f"skipped {msg.id[-8:]}: already taken")
                continue
            self.work.put(msg)
            taken += 1
        return taken

    def handle(self, msg: Incoming) -> None:
        self.whatsapp.mark_read(msg.id)
        if msg.kind != "text" or not msg.text.strip():
            self.whatsapp.send(msg.sender, "I can only read text messages.")
            return
        if msg.text.strip().lower().rstrip(".!") in RESET_WORDS:
            self.conversations.reset(msg.sender)
            self.whatsapp.send(msg.sender, "OK, starting a new conversation.")
            return
        now = self.clock()
        conv = self.conversations.current(msg.sender, now)
        stamped = f"({sent_at(datetime.fromtimestamp(now, self.tz))})\n{msg.text.strip()}"
        started = time.monotonic()
        reply = self.claude.answer(conv.messages, stamped)
        if reply.assistant is not None:
            conv.messages += [{"role": "user", "content": stamped},
                              {"role": "assistant", "content": reply.assistant}]
        sent = self.whatsapp.send(msg.sender, reply.text)
        log(f"answered {msg.id[-8:]} for {mask(msg.sender)} in {time.monotonic() - started:.1f}s "
            f"(turn {conv.turns}, {len(reply.text)} chars, {'sent' if sent else 'NOT sent'})")

    def run_worker(self) -> None:
        while True:
            msg = self.work.get()
            try:
                self.handle(msg)
            except Exception as e:  # one bad message must not stop the worker
                log(f"error handling {msg.id[-8:]}: {type(e).__name__}")


def make_handler(bot: Bot, app_secret: bytes, verify_token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "claude-whatsapp"

        def log_message(self, *args):  # requests are logged by what they did, below
            pass

        def _reply(self, status: int, body: str = "") -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path == "/healthz":
                return self._reply(200, "ok")
            if path != "/webhook":
                return self._reply(404)
            params = dict(urllib.parse.parse_qsl(query))
            token = params.get("hub.verify_token", "")
            if params.get("hub.mode") == "subscribe" and hmac.compare_digest(token, verify_token):
                log("webhook: Meta's subscription check passed")
                return self._reply(200, params.get("hub.challenge", ""))
            log("webhook: a subscription check with the wrong token")
            return self._reply(403)

        def do_POST(self):
            if self.path.partition("?")[0] != "/webhook":
                return self._reply(404)
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._reply(411)
            if length < 0 or length > MAX_BODY:
                return self._reply(413)
            body = self.rfile.read(length)
            if not signature_ok(app_secret, body, self.headers.get("X-Hub-Signature-256")):
                log("webhook: refused a POST with a bad signature")
                return self._reply(403)
            try:
                payload = json.loads(body)
            except ValueError:
                return self._reply(400)
            bot.take(payload if isinstance(payload, dict) else {})
            return self._reply(200, "ok")

    return Handler


def main() -> int:
    missing = [name for name in REQUIRED if not env(name)]
    if missing:
        log(f"not starting: {', '.join(missing)} not set")
        return 1
    allowed = allowed_numbers(env("ALLOWED_NUMBERS"))
    if not allowed:
        log("not starting: ALLOWED_NUMBERS holds no phone number")
        return 1
    tz = ZoneInfo(env("TIMEZONE")) if env("TIMEZONE") else timezone.utc
    # BOT_*, not CLAUDE_*: Claude Code's own CLAUDE_* variables would leak in on a local run.
    claude = Claude(anthropic.Anthropic(timeout=120.0), model=env("BOT_MODEL", "claude-opus-5-5"),
                    effort=env("BOT_EFFORT", "low"), web_search_uses=int(env("WEB_SEARCH_MAX_USES", "3")))
    whatsapp = WhatsApp(env("WHATSAPP_ACCESS_TOKEN"), env("WHATSAPP_PHONE_NUMBER_ID"),
                        env("GRAPH_API_VERSION", "v26.0"))
    conversations = Conversations(idle_seconds=60 * float(env("IDLE_MINUTES", "30")),
                                  max_turns=int(env("MAX_TURNS", "40")))
    bot = Bot(claude, whatsapp, allowed, conversations, tz)
    threading.Thread(target=bot.run_worker, name="worker", daemon=True).start()
    port = int(env("PORT", "8080"))
    server = ThreadingHTTPServer(("", port), make_handler(bot, env("WHATSAPP_APP_SECRET").encode(),
                                                         env("WHATSAPP_VERIFY_TOKEN")))
    log(f"listening on {port}: model {claude.model}, effort {claude.effort}, "
        f"{len(allowed)} allowed number(s), web search {'on' if claude.tools else 'off'}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
