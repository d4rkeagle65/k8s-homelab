"""
Documentation says what is true now, and the instructions a session loads stay rules.

  narration in docs and comments   history belongs in the commit and CHANGELOG, not the doc
  essay-length comment runs        past a dozen lines a comment is usually narrating
  CLAUDE.md is bounded as rules    it loads every session, so each rule competes for attention
  no rule stated twice             six words shared by two CLAUDE.md files is a restatement
  suites named in CLAUDE.md exist  a renamed suite leaves the sentence pointing at nothing
  tests/README.md is complete      a suite missing from the index is one nobody knows exists
  QUEUE.md contents match          both directions, because only one catches a rename

A regex cannot judge prose. These match recognisable shapes; lower a ceiling deliberately. The
pattern scans are self-tested at the bottom, because a scan that stops recognising its target
stays green forever.
"""

import ast
import fnmatch
import io
import os
import re
import sys
import tokenize
import unicodedata
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _testcommon as t

cfg = t.config()
root = t.root()
tracked = t.tracked_files()


def doc_excluded(rel):
    return any(fnmatch.fnmatchcase(rel.lower(), x.lower()) for x in (cfg.get("DocExclude") or []) if x)


# Not 'used to': it is present tense as often as not (the key is used to sign).
NARRATION = re.compile(r"\bpreviously\b|\bturned out\b|\bnobody (noticed|caught|reviewed)\b"
                       r"|\bwhich is how \b|\bit meant that\b|\bwas found by\b", re.IGNORECASE)


def find_narration(units):
    """Each narration phrase in each unit of text: a paragraph of a doc, or one comment. A
    unit is matched whole, so a phrase split across a line break is still one phrase."""
    return ["%d [%s]" % (u.line, m.group(0).strip()) for u in units
            for m in NARRATION.finditer(re.sub(r"\s+", " ", u.text))]


def paragraphs(lines):
    """A doc's paragraphs, each with the line it starts on."""
    out, buf, at = [], [], 0
    for i, l in enumerate(lines):
        if l.strip():
            if not buf:
                at = i + 1
            buf.append(l)
        elif buf:
            out.append(SimpleNamespace(line=at, text=" ".join(buf)))
            buf = []
    if buf:
        out.append(SimpleNamespace(line=at, text=" ".join(buf)))
    return out


# ------------------------------------------------------------------ 1. narration
t.section("docs say what is true now")
# Excluded by name, because the rule would damage them: CLAUDE.md is where the rules live,
# CHANGELOG.md is history by definition, QUEUE*.md and old-queues/ are working notes.
md = [f for f in tracked if f.lower().endswith(".md")
      and not re.fullmatch(r"(CLAUDE|CHANGELOG)\.md|QUEUE.*\.md", os.path.basename(f))
      and not t.rel(f).startswith("old-queues/") and not doc_excluded(t.rel(f))]
t.scanned("MarkdownFiles", len(md), ".md files")
hits = ["%s:%s" % (t.rel(f), h) for f in md for h in find_narration(paragraphs(t.source(f).split("\n")))]
t.ratchet("NarrationInDocs", len(hits), "narration in docs", hits,
          "make it a rule in CLAUDE.md if it stops a bug coming back; otherwise the commit already records it")

t.section("comments say what is true now")
COMMENT_EXTS = (".py", ".pyi", ".sh", ".toml", ".cfg", ".yml", ".yaml")
commentable = [f for f in tracked
               if (os.path.splitext(f)[1].lower() in COMMENT_EXTS
                   or os.path.basename(f) in (".gitattributes", ".gitignore"))
               and not doc_excluded(t.rel(f))]
t.scanned("CommentFiles", len(commentable), "commentable files")


def comment_units(path, text=None):
    """A file's comments as units: a docstring, a comment after code, or a run of whole-line
    comments, each with the line it starts on and, for a run, its length in lines. Python
    comments come from the tokenizer and docstrings from the parser, so a string is never read
    as a comment; other files go by the # at the start of a line."""
    text = t.source(path) if text is None else text
    units, run = [], []

    def flush():
        if run:
            units.append(SimpleNamespace(line=run[0][0], text=" ".join(r[1] for r in run), run=len(run)))
            run.clear()

    tokens = None
    if path.endswith((".py", ".pyi")):
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
        except (tokenize.TokenError, SyntaxError):
            tokens = None   # one that does not tokenize is read line by line, as other files are
    if tokens is not None:
        lines = text.split("\n")
        prev = -1
        for tok in tokens:
            if tok.type != tokenize.COMMENT:
                continue
            ln, col = tok.start
            own = lines[ln - 1][:col].strip() == ""
            if tok.string.startswith("#!"):
                continue
            if not own:
                flush()
                prev = -1
                units.append(SimpleNamespace(line=ln, text=tok.string, run=0))
                continue
            if prev != ln - 1:
                flush()
            run.append((ln, tok.string))
            prev = ln
        flush()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = None
        for node in ast.walk(tree) if tree else ():
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                    units.append(SimpleNamespace(line=first.lineno, text=first.value.value, run=0))
        return units
    for n, line in enumerate(text.split("\n"), 1):
        s = line.strip()
        if s.startswith("#") and not s.startswith("#!"):
            run.append((n, s))
        else:
            flush()
    flush()
    return units


c_hits, essays = [], []
essay_at = t.budget("EssayCommentLines")
for f in commentable:
    units = comment_units(f)
    c_hits += ["%s:%s" % (t.rel(f), h) for h in find_narration(units)]
    if f.endswith((".py", ".pyi")):
        essays += ["%s:%d (%d lines)" % (t.rel(f), u.line, u.run) for u in units if u.run >= essay_at]
t.ratchet("NarrationInComments", len(c_hits), "narration in comments", c_hits,
          "keep the invariant the comment protects; drop the account of how it was found")
t.ratchet("EssayComments", len(essays), "comment runs of %d+ lines" % essay_at, essays,
          "a long explanation belongs in a docstring or a doc; a long comment usually narrates")

# ------------------------------------------------------------------ 2. CLAUDE.md
t.section("CLAUDE.md is rules, and rules are bounded")


def measure_rules(text):
    """A rule is a bullet whose lead is bold, running to the next blank line or heading.
    Everything else that is not blank or a heading is prose."""
    rules, prose, cur = [], 0, None
    for n, line in enumerate(re.split(r"\r?\n", text), 1):
        s = line.strip()
        if not s or re.match(r"#{1,6}\s", line):
            cur = None
            continue
        if re.match(r"\s*-\s+\*\*", line):
            cur = SimpleNamespace(line=n, lines=1, lead=re.sub(r"\*\*.*$", "", re.sub(r"^-\s+\*\*", "", s)))
            rules.append(cur)
            continue
        if cur:
            cur.lines += 1
            continue
        prose += 1
    return SimpleNamespace(rules=rules, prose=prose, rule_lines=sum(r.lines for r in rules))


claude = [f for f in tracked if os.path.basename(f) == "CLAUDE.md"]
t.scanned("ClaudeFiles", len(claude), "CLAUDE.md files")
measured = []
for c in claude:
    m = measure_rules(t.source(c))
    m.rel = t.rel(c)
    measured.append(m)
root_m = [m for m in measured if m.rel == "CLAUDE.md"]
t.check("a root CLAUDE.md exists", len(root_m), 1)
over_root = False
if root_m:
    root_m = root_m[0]
    t.check("the root CLAUDE.md is within its rule budget", len(root_m.rules) <= t.budget("ClaudeRulesRoot"), True)
    t.detail("%d rules (budget %d)" % (len(root_m.rules), t.budget("ClaudeRulesRoot")))
    t.check("  and is more rule than prose", root_m.prose <= root_m.rule_lines, True)
    t.detail("%d prose vs %d rule lines" % (root_m.prose, root_m.rule_lines))
    over_root = len(root_m.rules) > t.budget("ClaudeRulesRoot")
total = sum(len(m.rules) for m in measured)
t.check("all CLAUDE.md files are within the total budget", total <= t.budget("ClaudeRulesTotal"), True)
t.detail("%d rules across %d file(s) (budget %d)" % (total, len(measured), t.budget("ClaudeRulesTotal")))
# Every rule loads every session and competes with the rest for attention.
if over_root or total > t.budget("ClaudeRulesTotal"):
    t.advise("earn a new rule its slot by retiring or merging one; a rule a suite enforces can go. "
             "Split by scope, never to get under the budget.")
long_rules = ["%s:%d (%d lines) %s" % (m.rel, r.line, r.lines, r.lead) for m in measured for r in m.rules
              if r.lines > t.budget("ClaudeRuleMaxLines")]
t.check("no rule runs past %d lines" % t.budget("ClaudeRuleMaxLines"), len(long_rules), 0)
for l in long_rules[:5]:
    t.detail(l, "yellow")


def shingles(text):
    """The distinct six-word windows of a text, with markdown, punctuation and backticked
    identifiers normalised away so emphasis is not a difference."""
    words = re.sub(r"[^A-Za-z0-9 ]", " ", re.sub(r"`[^`]*`", " ", text)).lower().split()
    return {" ".join(words[i:i + 6]) for i in range(len(words) - 5)}


owner, dupes = {}, set()
for c in claude:
    r = t.rel(c)
    for k in sorted(shingles(t.source(c))):
        if k in owner:
            dupes.add("%s / %s: '%s'" % (owner[k], r, k))
        else:
            owner[k] = r
dupes = sorted(dupes)
t.check("no six-word phrase appears in two CLAUDE.md files", len(dupes), 0)
for d in dupes[:6]:
    t.detail(d, "yellow")
if dupes:
    t.advise("state it in the file whose SCOPE it matches, and delete the other.")

extra = [os.path.join(root, x) for x in (cfg.get("ExtraSuites") or []) if x]
named = sorted({"%s|%s" % (t.rel(c), m.group(0)) for c in claude
                for m in re.finditer(r"test-[A-Za-z0-9_-]+\.py", t.source(c))})
dangling = [n for n in named if not os.path.isfile(os.path.join(t.HERE, n.split("|")[1]))
            and not any(os.path.basename(x) == n.split("|")[1] for x in extra)]
t.check("every suite a CLAUDE.md names exists", ", ".join(dangling), "")

# ------------------------------------------------------------------ 3. tests/README.md
t.section("tests/README.md lists every suite")
readme = os.path.join(t.HERE, "README.md")
t.check("tests/README.md exists", os.path.isfile(readme), True)
if os.path.isfile(readme):
    rt = t.source(readme)
    on_disk = ([f for f in sorted(os.listdir(t.HERE)) if re.fullmatch(r"test-.+\.py", f)]
               + [os.path.basename(x) for x in extra])
    # A table row, not a mention: a suite named only in prose has no row.
    listed = sorted(set(re.findall(r"(?m)^\|\s*`(test-[^`\s]+\.py)`", rt)))
    t.check("every suite appears in the table", ", ".join(s for s in on_disk if s not in listed), "")
    t.check("no table row names a suite that does not exist", ", ".join(s for s in listed if s not in on_disk), "")
    stated = re.search(r"\*\*(\d+)\s+suites", rt)
    t.check("the header states a suite count (**N suites**)", bool(stated), True)
    if stated:
        t.check("  and it matches the %d suites" % len(on_disk), int(stated.group(1)), len(on_disk))


# ------------------------------------------------------------------ 4. QUEUE.md
def slug(h):
    """A heading's anchor as GitHub makes it: lower case, punctuation other than - and _
    dropped, spaces to hyphens. Letters outside ASCII stay."""
    kept = "".join(ch for ch in h.lower() if ch in "_ -" or unicodedata.category(ch)[0] == "L"
                   or unicodedata.category(ch) == "Nd")
    return kept.replace(" ", "-")


queue = os.path.join(root, "QUEUE.md")
if os.path.isfile(queue):
    t.section("QUEUE.md's contents list matches its sections")
    q = t.source(queue)
    heads = [h for h in re.findall(r"(?m)^##\s+(.+?)\s*$", q) if h != "Contents"]
    # Every in-page link, whatever it holds: one that no heading makes is stale.
    links = re.findall(r"\]\(#([^)\s]+)\)", q)
    t.check("QUEUE.md has a Contents section", bool(re.search(r"(?m)^##\s+Contents\s*$", q)), True)
    t.check("every section appears in the contents", ", ".join(h for h in heads if slug(h) not in links), "")
    slugs = [slug(h) for h in heads]
    t.check("no contents entry names a missing section", ", ".join(l for l in links if l not in slugs), "")

if cfg.get("ReadmeCatalogue"):
    t.section("README.md names every top-level entry")
    rm = os.path.join(root, "README.md")
    t.check("README.md exists", os.path.isfile(rm), True)
    if os.path.isfile(rm):
        rmt = t.source(rm)
        by_pattern = "QUEUE-<task>.md" in rmt
        tops = sorted({t.rel(f).split("/")[0] for f in tracked})
        tops = [x for x in tops if x and not x.startswith(".") and x != "README.md"
                and not (by_pattern and fnmatch.fnmatchcase(x, "QUEUE-*.md"))]
        # Named as a word: 'lib' inside "library" is not a mention of the folder.
        t.check("every top-level entry appears in README.md",
                ", ".join(x for x in tops if not re.search(r"(?<![\w.-])" + re.escape(x) + r"(?![\w-])", rmt)), "")

# ------------------------------------------------------------------ self-test
t.section("the pattern scans, proven against injected text")
shares = lambda a, b: len(shingles(a) & shingles(b))
t.check("SELF-TEST: six shared words are CAUGHT", shares("one two three four five six", "zero one two three four five six"), 1)
t.check("  five shared words are not", shares("one two three four five nine", "one two three four five six"), 0)
t.check("  emphasis and case are not a difference", shares("**One** two _three_ FOUR five six", "one two three four five six"), 1)
t.check("  punctuation is not a difference", shares("one, two; three: four - five. six", "one two three four five six"), 1)
t.check("  a window repeated in one file is one key", len(shingles("a b c d e f a b c d e f")), 6)
t.check("SELF-TEST: every narration phrase fires",
        len([s for s in ("it previously failed", "that turned out wrong", "nobody noticed it",
                         "which is how it broke", "it meant that nothing ran", "it was found by a run")
             if not NARRATION.search(s)]), 0)
t.check("  ordinary prose does not",
        len([s for s in ("the value is read back after it is written", "set this to the accepted domain",
                         "the key is used to sign requests") if NARRATION.search(s)]), 0)
t.check("SELF-TEST: a phrase split across a line break fires", len(find_narration(paragraphs(["This is", "what nobody", "noticed."]))), 1)
probe = "\n".join(['"""NOTES: this previously failed."""',
                   "x = 1  # which is how the outage started",
                   'doc = """',
                   "# it previously was a shell script",
                   '"""',
                   "# nobody noticed"]) + "\n"
pu = comment_units("probe.py", probe)
t.check("SELF-TEST: a docstring and a comment after code are scanned", len(find_narration([u for u in pu if u.line <= 2])), 2)
t.check("  a string is not a comment, and does not hide what follows",
        "%d %d" % (len(find_narration(pu)), len([u for u in pu if u.line in (4, 5)])), "3 0")
run_text = "\n".join("# line %d" % i for i in range(1, 4)) + "\nx = 1\n# alone\n"
t.check("SELF-TEST: whole-line comments make one run, broken by code",
        [u.run for u in comment_units("probe.py", run_text)], [3, 1])
t.check("SELF-TEST: a slug keeps _ and letters outside ASCII", slug("Fix my_func: café parsing"), "fix-my_func-café-parsing")
pm = measure_rules("# T\n\nIntro line.\n\n- **Rule one.** Body\n  continues.\n- **Rule two.** Body.")
t.check("SELF-TEST: the rule counter finds two rules", len(pm.rules), 2)
t.check("  charges the first its two lines", pm.rules[0].lines, 2)
t.check("  and counts the intro as prose", pm.prose, 1)

t.complete()
