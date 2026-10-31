"""
Identifying data in EVERY tracked file, at every commit. The pre-commit hook reads only what
a commit stages, so a word added to the list later never finds the copy already in the
tree, and it has no rule for addresses; this reads the whole tree, every extension, binary
files included (their ASCII still matches).

  a private address   a host on 10/8, 172.16/12, 192.168/16 or the tailnet's 100.64/10:
                      the homelab's own, which live in Vaultwarden and NetBox, never git.
                      A network (a /prefix, or a last octet of 0) is fine; examples use
                      RFC 5737's 192.0.2.0/24, 198.51.100.0/24 and 203.0.113.0/24
  a secret word       matched as the hook matches it (case-blind, anywhere in the text), from
                      the list scripts/secret_words.py writes outside the tree, so a clone
                      without it says the rule is not armed
  the hook's shapes   a private key, a GUID labelled as an identifier, a certificate
                      thumbprint, a secret assigned a literal, a cloud or service access key
                      (scripts/tests/test_secrets_hygiene.py also scans for keys, but only
                      in files that read as UTF-8 text)

A line that must hold one (a test of a range's edge) says so with the marker
`identifying-data: allow` on that line; review sees every use.

A finding names the file and line, never the value: this output can reach a CI log, and the
repo is public.
"""

import ipaddress
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _testcommon as t

files = [f for f in t.tracked_files() if os.path.isfile(f)]
t.scanned("AllFiles", len(files), "tracked files, every extension")

# The hook and this suite hold the patterns and their self-tests, so neither is read for them.
SELF = {".githooks/pre-commit", ".githooks/pre-commit.template", "tests/test-identifying-data.py"}

PRIVATE = re.compile(
    r"(?<![\d.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7]))"
    r"\.\d{1,3}\.\d{1,3}(?!\d|\.\d|/\d)")

SHAPES = [
    ("a private key", re.compile(r"-----BEGIN ([A-Z]+ )?PRIVATE KEY-----")),
    ("a GUID labelled as an identifier", re.compile(
        r"(?i)(tenant_?id|subscription_?id|client_?id|app_?id|object_?id)[^A-Za-z0-9]{0,12}"
        r"(?!(.)\2{7}-)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")),
    ("a certificate thumbprint", re.compile(r"(?i)thumbprint[^A-Za-z0-9]{0,12}[0-9a-f]{40}")),
    ("a secret assigned a literal value", re.compile(
        r"""(?i)(password|passwd|secret|api_?key|access_?key|token)["']?\s*[:=]\s*["'][^"'<${%\s][^"']{7,}["']""")),
    ("a cloud or service access key", re.compile(
        r"AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36}|xox[baprs]-[A-Za-z0-9-]{10,}|AccountKey=[A-Za-z0-9+/=]{20,}")),
]


# Scanners, as functions so the self-test can feed them text instead of writing poisoned files.
ALLOW = "identifying-data: allow"


def private_hosts(text):
    """Line numbers holding a private host address (never the address itself), but not on a
    line carrying ALLOW."""
    lines = []
    for m in PRIVATE.finditer(text):
        start = text.rfind("\n", 0, m.start()) + 1
        end = text.find("\n", m.end())
        if ALLOW in text[start:end if end != -1 else len(text)]:
            continue
        try:
            ip = ipaddress.ip_address(m.group(0))
        except ValueError:  # an octet over 255: a version or an ID, not an address
            continue
        if ip.packed[-1] == 0:  # a network, written without its prefix
            continue
        lines.append(1 + text.count("\n", 0, m.start()))
    return lines


def words_in(text, words):
    """Line numbers holding any listed word, matched case-blind anywhere, as the hook does."""
    low = text.lower()
    lines = set()
    for w in words:
        start = low.find(w)
        while start != -1:
            lines.add(1 + low.count("\n", 0, start))
            start = low.find(w, start + 1)
    return sorted(lines)


def shapes_in(text):
    """(shape, line) for each of the hook's shapes found."""
    return [(name, 1 + text.count("\n", 0, m.start())) for name, rx in SHAPES for m in rx.finditer(text)]


def decode(data):
    """Text to scan. UTF-16 is decoded first, or nothing below would match it; anything else
    that isn't UTF-8 is read byte for byte, so a binary file's ASCII is still seen."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


t.section("the scanners, proven against injected text")
t.check("SELF-TEST: a host on each private network is CAUGHT",
        private_hosts("a 10.1.2.3\nb 172.16.228.3`\nc 192.168.1.50-192.168.1.70\nd 100.64.1.9"), [1, 2, 3, 3, 4])
t.check("  networks, documentation and public addresses are not",
        private_hosts("10.0.0.0/8 172.16.0.0/12 10.96.0.0 192.0.2.10 198.51.100.7 203.0.113.5 8.8.8.8 127.0.0.1"), [])
t.check("  nor the edges just outside each range",
        private_hosts("172.15.1.1 172.32.1.1 100.63.1.1 100.128.1.1 192.169.1.1 11.1.1.1"), [])
t.check("  nor one on a line marked to allow it", private_hosts("x 10.1.2.3  # " + ALLOW + "\ny 10.1.2.4"), [2])
t.check("  nor versions: dotted parts, a fifth part or an octet over 255",
        private_hosts("version-4.10.1.90 v1.10.1.2.5 10.1.2.300 2.2.0.231"), [])
t.check("SELF-TEST: a listed word is CAUGHT, case-blind, on its line",
        words_in("one\nthe Acme Widgets host\n", ["acme widgets"]), [2])
t.check("  text without it is not", words_in("one\ntwo\n", ["acme widgets"]), [])
t.check("SELF-TEST: a private key and an access key are CAUGHT",
        # Built from pieces, so scripts/tests/test_secrets_hygiene.py's scan doesn't read this file as a key.
        [s for s, _ in shapes_in("-----BEGIN RSA PRIVATE " + "KEY-----\nAKIA" + "A" * 16)],
        ["a private key", "a cloud or service access key"])
t.check("  a placeholder secret and a repeated-digit GUID are not",
        shapes_in('password: "${DB_PASSWORD}"\ntenant_id: 00000000-0000-0000-0000-000000000000'), [])
t.check("SELF-TEST: UTF-16 is read as text", "10.1.2.3" in decode("10.1.2.3".encode("utf-16")), True)
t.check("  and a binary file's ASCII still is", "10.1.2.3" in decode(b"\x89PNG\xff\x00 10.1.2.3"), True)

def word_list():
    """The secret-words list's path, or None: the one the hook names (it runs the suites on a
    copy that is a repository of its own), else the clone's, in the git folder its worktrees
    share, else a .secret-words at the top, read only while the clone's is absent."""
    named = os.environ.get("SECRET_WORDS_FILE")
    if named:
        return named
    res = t.run(["git", "-C", t.root(), "rev-parse", "--path-format=absolute", "--git-common-dir"], merge=False)
    if res.code != 0:
        raise RuntimeError("git rev-parse --git-common-dir failed in %s: %s" % (t.root(), res.err.strip()))
    for path in (os.path.join(res.out.strip(), "secret-words"), os.path.join(t.root(), ".secret-words")):
        if os.path.isfile(path):
            return path
    return None


words = []
listed = word_list()
if listed:
    with open(listed, encoding="utf-8", errors="replace") as fh:
        words = sorted({w.strip().lower() for w in fh if w.strip() and not w.lstrip().startswith("#")})

addresses, named, shaped = [], [], []
for f in files:
    r = t.rel(f).replace("\\", "/")
    if r in SELF or r == ".secret-words":
        continue
    with open(f, "rb") as fh:
        text = decode(fh.read())
    addresses += ["%s:%d" % (r, n) for n in private_hosts(text)]
    named += ["%s:%d" % (r, n) for n in words_in(text, words)]
    shaped += ["%s:%d  %s" % (r, n, s) for s, n in shapes_in(text)]

t.section("no private host address in any tracked file")
t.check("no tracked file holds a private host address", len(addresses), 0)
for a in addresses[:12]:
    t.detail(a, "yellow")
if addresses:
    t.advise("a ${VARIABLE} from cluster-secrets, a name NetBox resolves, or an RFC 5737 example")

t.section("no secret word in any tracked file")
if words:
    t.check("no tracked file holds a word on the secret-words list", len(named), 0)
    for a in named[:12]:
        t.detail(a, "yellow")
else:
    t.skip("no tracked file holds a word on the secret-words list",
           "no secret-words list here (scripts/secret_words.py writes it), so the named-word rule is not armed")

t.section("none of the hook's secret shapes in any tracked file")
t.check("no tracked file holds a key, labelled ID, thumbprint or secret literal", len(shaped), 0)
for a in shaped[:12]:
    t.detail(a, "yellow")

t.complete()
