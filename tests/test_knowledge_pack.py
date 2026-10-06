"""The knowledge pack: AGENTS.md and docs/troubleshooting.md,
docs/architecture-map.md, docs/tax-rules.md, docs/settings.md.

AI assistants (Claude Code, Codex, Gemini CLI, Cursor) start from these
files when they help someone run taxjson or change its code. A pack that
names a file, function or message that has moved sends them the wrong way,
so this test fails when:

- a file the pack names does not exist, or a symbol written after it is not
  in that file (a reference is `` `path` — `sym1`, `sym2` ``; an identifier
  must occur as a whole word, any other text — a message fragment — as a
  plain substring);
- a `tjs COMMAND` / `taxjson COMMAND` it quotes is not a command;
- a troubleshooting entry lacks Check / Cause / Fix / Fixed in / Code, or its
  "Fixed in" is not a released version heading in CHANGELOG.md;
- the pack (or the bug-report template) holds personal data: an e-mail address, an account-number shape
  (an IB U-number or an 8+ digit run that is not a synthetic id or a date),
  a money amount of 1,000 or more with cents, or anything
  scripts/check-pii.sh refuses (its private denylist and figure list apply
  when the maintainer has them).

The two docs another change may not have added yet (tax-rules, settings) are
checked when present.
"""
import datetime
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED = ("AGENTS.md", "docs/troubleshooting.md", "docs/architecture-map.md")
OPTIONAL = ("docs/tax-rules.md", "docs/settings.md")

# A repository path: relative, no spaces or placeholders, a file with an
# extension or a directory ending in '/'.
_PATH = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*(/|\.[A-Za-z0-9]+)$")
# Top-level entries a backticked path may start with to count as a path in
# THIS repository (a bare `work/x.sum` or `taxjson.toml` is a user's
# project file, not ours). Root files are named in full.
_REPO_DIRS = ("src/", "tests/", "scripts/", "docs/", "packages/",
              "examples/", ".github/")
_ROOT_FILES = re.compile(r"^([A-Z][A-Z_]*\.md|install\.sh|setup\.sh|"
                         r"run_tests\.sh|pyproject\.toml|channels\.json)$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
# `path` — `a`, `b`  (also -- or a plain hyphen between them).
_REF = re.compile(r"`([^`\s]+)`\s+(?:—|--|-)\s+((?:`[^`]+`(?:,\s*|\s+and\s+)?)+)")
_TICKS = re.compile(r"`([^`]+)`")


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _pack_files():
    return [p for p in REQUIRED + OPTIONAL if (ROOT / p).is_file()]


# Public files the pack points people to: scanned for personal data too.
TEMPLATES = (".github/ISSUE_TEMPLATE/bug_report.md",)


def _public_files():
    return _pack_files() + [p for p in TEMPLATES if (ROOT / p).is_file()]


def _is_repo_path(tok):
    if not _PATH.match(tok) or tok.startswith("/"):
        return False
    return tok.startswith(_REPO_DIRS) or bool(_ROOT_FILES.match(tok))


def _split_path_symbol(tok):
    """`src/x.py:func` -> (src/x.py, func); else (tok, None)."""
    if ":" in tok:
        path, _, sym = tok.partition(":")
        if _PATH.match(path) and _IDENT.match(sym):
            return path, sym
    return tok, None


def _has_symbol(text, sym):
    sym = sym.strip()
    if sym.endswith("()"):
        sym = sym[:-2]
    if _IDENT.match(sym):
        return all(re.search(r"(?<![A-Za-z0-9_])" + re.escape(part)
                             + r"(?![A-Za-z0-9_])", text)
                   for part in sym.split("."))
    return sym in text


def check_refs(where, text, bad):
    """Every repository path named in TEXT exists; every symbol written
    after a path (the reference form) is in that file."""
    seen = set()
    for m in _REF.finditer(text):
        path, sym0 = _split_path_symbol(m.group(1))
        if not _is_repo_path(path):
            continue        # `ticker.map` — `QUOTE`: a user's file, not ours
        syms = _TICKS.findall(m.group(2))
        if sym0:
            syms.insert(0, sym0)
        seen.add(m.start(1))
        _check_one(where, path, syms, bad)
    for m in _TICKS.finditer(text):
        if m.start(1) in seen:
            continue
        path, sym = _split_path_symbol(m.group(1))
        if _is_repo_path(path):
            _check_one(where, path, [sym] if sym else [], bad)


def _check_one(where, path, syms, bad):
    full = ROOT / path.rstrip("/")
    if not full.exists():
        if path in OPTIONAL and not syms:
            return          # a pack doc another change adds: named ahead
        bad.append(f"{where}: no {path}")
        return
    if full.is_dir():
        return
    src = full.read_text(encoding="utf-8", errors="replace")
    for s in syms:
        if not _has_symbol(src, s):
            bad.append(f"{where}: `{s}` is not in {path}")


def _commands():
    from taxjson.bin import taxjson_run
    return {name for _, names in taxjson_run._COMMAND_GROUPS for name in names}


def _released():
    return set(re.findall(r"^## v(\d+\.\d+\.\d+)\b", _read("CHANGELOG.md"), re.M))


def _entries(doc):
    return [e for e in re.split(r"^### ", doc, flags=re.M)[1:]]


class TestPackFilesExist(unittest.TestCase):
    def test_required_files(self):
        for p in REQUIRED:
            self.assertTrue((ROOT / p).is_file(), f"{p} is missing")

    def test_agents_md_is_short_and_points_at_the_pack(self):
        text = _read("AGENTS.md")
        n = text.count("\n")
        self.assertLessEqual(n, 110, f"AGENTS.md is loaded into every session: keep it short ({n} lines)")
        for p in REQUIRED[1:] + OPTIONAL:
            self.assertIn(p, text, f"AGENTS.md does not point at {p}")

    def test_bug_template_is_the_one_agents_md_names(self):
        self.assertTrue((ROOT / TEMPLATES[0]).is_file())
        # One bug template: a .yml form beside it shows two in the chooser.
        self.assertFalse((ROOT / ".github/ISSUE_TEMPLATE/bug_report.yml").exists())

    def test_no_claude_md_beside_it(self):
        # Claude Code reads AGENTS.md itself; a CLAUDE.md would shadow it.
        self.assertFalse((ROOT / "CLAUDE.md").exists())


class TestReferences(unittest.TestCase):
    def test_every_path_and_symbol_exists(self):
        bad = []
        for p in _pack_files():
            for n, line in enumerate(_read(p).splitlines(), 1):
                check_refs(f"{p}:{n}", line, bad)
        self.assertEqual(bad, [])

    def test_every_quoted_command_exists(self):
        cmds = _commands()
        bad = []
        for p in _pack_files():
            for n, line in enumerate(_read(p).splitlines(), 1):
                # A reference's symbols are code text, not commands.
                line = _REF.sub(lambda m: "`" + m.group(1) + "`", line)
                for tok in _TICKS.findall(line):
                    m = re.match(r"^(?:tjs|taxjson)\s+(?:-C\s+\S+\s+)?([a-z][a-z0-9-]*)", tok)
                    if m and m.group(1) not in cmds:
                        bad.append(f"{p}:{n}: `{tok}` — no command {m.group(1)!r}")
        self.assertEqual(bad, [])


class TestTroubleshooting(unittest.TestCase):
    FIELDS = ("Check", "Cause", "Fix", "Fixed in", "Code")

    def setUp(self):
        self.doc = _read("docs/troubleshooting.md")
        self.entries = _entries(self.doc)

    def test_has_entries(self):
        self.assertGreaterEqual(len(self.entries), 40)

    def test_every_entry_is_complete(self):
        released = _released()
        bad = []
        for e in self.entries:
            title = e.split("\n", 1)[0][:70]
            for f in self.FIELDS:
                if not re.search(r"^- \*\*" + re.escape(f) + r":\*\*", e, re.M):
                    bad.append(f"{title}: no {f}")
            fixed = re.search(r"^- \*\*Fixed in:\*\*\s*(.*)$", e, re.M)
            fixed = fixed.group(1) if fixed else ""
            for v in re.findall(r"`v(\d+\.\d+\.\d+)`", fixed):
                if v not in released:
                    bad.append(f"{title}: v{v} is not a release heading in CHANGELOG.md")
            if fixed and not re.search(r"`v\d+\.\d+\.\d+`|—", fixed):
                bad.append(f"{title}: Fixed in is a version in backticks or —")
            code = re.search(r"^- \*\*Code:\*\*\s*(.*)$", e, re.M)
            code = code.group(1) if code else ""
            if code and not any(_is_repo_path(_split_path_symbol(t)[0])
                                for t in _TICKS.findall(code)):
                bad.append(f"{title}: Code names no file")
        self.assertEqual(bad, [])

    def test_titles_are_unique(self):
        titles = [e.split("\n", 1)[0].strip() for e in self.entries]
        dup = sorted({t for t in titles if titles.count(t) > 1})
        self.assertEqual(dup, [])


class TestArchitectureMap(unittest.TestCase):
    def test_sections_and_bullets(self):
        doc = _read("docs/architecture-map.md")
        self.assertGreaterEqual(len(re.findall(r"^## ", doc, re.M)), 10)
        bullets = [ln for ln in doc.splitlines() if re.match(r"^\s*- `", ln)]
        bad = [ln.strip()[:70] for ln in bullets if not _REF.search(ln)
               and not re.match(r"^\s*- `[^`]+`:", ln)]
        self.assertEqual(bad, [], "a map bullet is `path` — `symbols`: what they do")


# ---- personal data --------------------------------------------------------

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Synthetic ids the tests and docs use: U1234567(x), U555xxxx, U9990xxxx.
_IB_ID = re.compile(r"(?<![A-Za-z0-9])[Uu](\d{7,8})(?!\d)")
_IB_SYNTH = re.compile(r"^(1234567\d?|555\d{4,5}|9990\d{3,4})$")
_LONG_DIGITS = re.compile(r"(?<![\d.,A-Za-z])\d{8,}(?![\d])")
_AMOUNT = re.compile(r"(?<![\d,.])\d{1,3}(?:,\d{3})+\.\d{2}(?!\d)")
_PII_OK = re.compile(r"(#|//|<!--)\s*pii-ok|pii-ok:")


def _real_date8(tok):
    try:
        datetime.datetime.strptime(tok, "%Y%m%d")
        return True
    except ValueError:
        return False


def personal_data(text):
    """(line, what) for every line of TEXT that looks like personal data."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if _PII_OK.search(line):
            continue
        if _EMAIL.search(line):
            out.append((n, "an e-mail address"))
        for m in _IB_ID.finditer(line):
            if not _IB_SYNTH.match(m.group(1)):
                out.append((n, "an IB account id (U + 7-8 digits)"))
        for m in _LONG_DIGITS.finditer(line):
            tok = m.group(0)
            if not (tok.startswith(("9990", "555")) or (len(tok) == 8 and _real_date8(tok))):
                out.append((n, "an account-number shape (8+ digits)"))
        if _AMOUNT.search(line):
            out.append((n, "a money amount of 1,000 or more with cents"))
    return out


class TestPersonalData(unittest.TestCase):
    def test_detector(self):
        hits = lambda s: [w for _, w in personal_data(s)]  # noqa: E731
        self.assertTrue(hits("mail " + "someone" + "@" + "example.org"))
        self.assertTrue(hits("account U" + "7654321"))
        self.assertFalse(hits("account U" + "5550001 and U" + "1234567"))
        self.assertTrue(hits("acct " + "1234" + "5678"))
        self.assertFalse(hits("on 2025" + "0131 and 9990" + "0001"))
        self.assertTrue(hits("a total of 12" + ",345.67"))
        self.assertFalse(hits("a total of 345.67, or 1,000 shares"))
        self.assertFalse(hits("a synthetic 12" + ",345.67 <!-- pii-ok -->"))

    def test_pack_has_no_personal_data(self):
        bad = [f"{p}:{n}: {what}" for p in _public_files()
               for n, what in personal_data(_read(p))]
        self.assertEqual(bad, [])

    @unittest.skipUnless(shutil.which("bash"), "bash required")
    def test_check_pii_passes_on_the_pack(self):
        r = subprocess.run(["bash", str(ROOT / "scripts" / "check-pii.sh"), *_public_files()],
                           cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
