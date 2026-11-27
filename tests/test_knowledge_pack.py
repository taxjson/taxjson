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
- a `tjs COMMAND` / `tjs COMMAND` it quotes is not a command;
- a troubleshooting entry lacks Check / Cause / Fix / Fixed in / Code, or its
  "Fixed in" is not a released version heading in CHANGELOG.md, `—`, or
  `unreleased` (scripts/release.sh turns `unreleased` into the new tag);
- the pack (or the bug-report template) holds personal data: an e-mail address, an account-number shape
  (an IB U-number or an 8+ digit run that is not a synthetic id or a date),
  a money amount of 1,000 or more with cents, or anything
  scripts/check-pii.sh refuses (its private denylist and figure list apply
  when the maintainer has them).

The docs another change may not have added yet (OPTIONAL: tax-rules,
settings and the user pages brokers, commands, glossary, limits, upgrading)
are checked when present.
"""
import ast
import datetime
import io
import re
import shutil
import subprocess
import tempfile
import tokenize
import unittest
from functools import lru_cache
from pathlib import Path
from unittest import mock
import _paths
from _support.suite_history_pages import RENAMED_IN, history_releases, renamed_ok

ROOT = _paths.REPO

REQUIRED = ("AGENTS.md", "docs/troubleshooting.md", "docs/architecture-map.md")
OPTIONAL = ("docs/tax-rules.md", "docs/settings.md",
            # The user pages (v0.28.0 docs rework): checked like the pack.
            "docs/brokers.md", "docs/commands.md", "docs/glossary.md",
            "docs/limits.md", "docs/upgrading.md")
# Files outside the pack whose code references and commands are checked
# like its own (TJ-3561): the known problems a release ships with.
REFERENCED = ("KNOWN_ISSUES.md",)

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
# `path` `name`: a path and one name with no dash between them (the map's
# parenthetical lists: "the readers (`src/x.py` `fn`, ...)"; TJ-3608).
_BARE_REF = re.compile(r"`([^`\s]+)`\s+`([A-Za-z_][A-Za-z0-9_]*)(?:\(\))?`")


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _pack_files():
    return [p for p in REQUIRED + OPTIONAL if (ROOT / p).is_file()]


def _referencing_files():
    return _pack_files() + [p for p in REFERENCED if (ROOT / p).is_file()]


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
        # Every backticked identifier after the list too (the list used
        # to stop at a parenthetical, so a removed `cmd_scan` after one
        # went unchecked): `path` — `a` (what it does, `b`), `c` names b
        # and c in path as well. The span ends where the reference's own
        # parenthetical closes or at the next path outside a
        # parenthetical; a reference nested in a parenthetical is its
        # own (skipped here until that parenthetical closes).
        for tok in _span_idents(text, m.end()):
            if tok not in syms:
                syms.append(tok)
        seen.add(m.start(1))
        _check_one(where, path, syms, bad)
    for m in _BARE_REF.finditer(text):
        if m.start(1) in seen:
            continue
        path, sym0 = _split_path_symbol(m.group(1))
        if _is_repo_path(path) and not sym0:
            seen.add(m.start(1))
            _check_one(where, path, [m.group(2)], bad)
    for m in _TICKS.finditer(text):
        if m.start(1) in seen:
            continue
        path, sym = _split_path_symbol(m.group(1))
        if _is_repo_path(path):
            _check_one(where, path, [sym] if sym else [], bad)


_SPAN_EVENT = re.compile(r"`([^`]+)`|[()]")


def _span_idents(text, pos):
    """The backticked identifiers a reference ending at `pos` goes on to
    name (check_refs)."""
    out, depth, skip = [], 0, None
    for e in _SPAN_EVENT.finditer(text, pos):
        tok = e.group(1)
        if tok is None:
            depth += 1 if e.group(0) == "(" else -1
            if depth < 0:
                break
            if skip is not None and depth < skip:
                skip = None
            continue
        if _is_repo_path(_split_path_symbol(tok)[0]):
            if depth == 0:
                break
            skip = depth if skip is None else min(skip, depth)
        elif skip is None and _IDENT.match(tok) and "." not in tok:
            out.append(tok)
    return out


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
        elif full.suffix == ".py":
            home = _moved_to(path, s)
            if home:
                bad.append(f"{where}: `{s}` is only in {path}'s comments or "
                           f"imports; it is defined in {home}")
            elif _engine_prose_only(path, s):
                bad.append(f"{where}: `{s}` is only in {path}'s comments, "
                           f"docstrings or imports: name the engine module "
                           f"whose code holds it")


@lru_cache(maxsize=None)
def _top_defs():
    """Every top-level function, class and assigned name in src/, with
    the files that define it."""
    out = {}
    for p in sorted((ROOT / "src").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        for n in ast.parse(p.read_text(encoding="utf-8")).body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = [n.name]
            elif isinstance(n, ast.Assign):
                names = [t.id for t in n.targets if isinstance(t, ast.Name)]
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                names = [n.target.id]
            else:
                names = []
            for name in names:
                out.setdefault(name, set()).add(rel)
    return out


@lru_cache(maxsize=None)
def _code_text(path):
    """The file's source without its comments, docstrings, import
    statements (a name it only imports is defined elsewhere: TJ-3530) and
    module-level `__all__` (a shim lists the names it imports: PLAN.md
    step 10's lib/core.py), with the names it re-exports (`import X as X`,
    the explicit form)."""
    src = (ROOT / path).read_text(encoding="utf-8")
    docs, imports, reexports = set(), set(), []
    tree = ast.parse(src)
    for n in tree.body:
        targets = n.targets if isinstance(n, ast.Assign) else [getattr(n, "target", None)]
        if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            imports.update(range(n.lineno, n.end_lineno + 1))
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            imports.update(range(n.lineno, n.end_lineno + 1))
            reexports += [a.name for a in n.names if a.asname == a.name]
        body = getattr(n, "body", None)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)) and body:
            b = body[0]
            if (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant)
                    and isinstance(b.value.value, str)):
                docs.add((b.lineno, b.col_offset))
    return " ".join([t.string for t in tokenize.generate_tokens(io.StringIO(src).readline)
                     if t.type != tokenize.COMMENT
                     and not (t.type == tokenize.STRING and t.start in docs)
                     and t.start[0] not in imports] + reexports)


# The engine package (PLAN step 10): one file moved into many, each module's
# comments still naming what the others hold.
ENGINE_PREFIX = "src/taxjson/engine/"


def _engine_prose_only(path, sym):
    """True when a name the reference gives to a module of the engine
    package is only in its comments, docstrings or imports. A local, a
    parameter or a key has no top-level home, so `_moved_to` passes it
    (TJ-4075: `_suppress_record` under ca/solve.py, a local of
    ca/walk.py)."""
    sym = sym.strip()
    if sym.endswith("()"):
        sym = sym[:-2]
    if not path.startswith(ENGINE_PREFIX) or not _IDENT.match(sym) or "." in sym:
        return False
    return not _has_symbol(_code_text(path), sym)


def _moved_to(path, sym):
    """Where a code name the reference gives to `path` is defined, when
    `path` has it only in comments and docstrings and another module
    defines it: moved code whose old home still mentions it (step 5's
    cash-events readers, TJ-2559; a plain lowercase name too, TJ-2570)."""
    sym = sym.strip()
    if sym.endswith("()"):
        sym = sym[:-2]
    if not _IDENT.match(sym) or "." in sym:
        return None
    homes = _top_defs().get(sym)
    if not homes or path in homes or _has_symbol(_code_text(path), sym):
        return None
    return ", ".join(sorted(homes))


def _commands():
    from taxjson.cli import parser as cli_parser
    return {name for _, names in cli_parser._COMMAND_GROUPS for name in names}


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
        for p in _referencing_files():
            for n, line in enumerate(_read(p).splitlines(), 1):
                check_refs(f"{p}:{n}", line, bad)
        self.assertEqual(bad, [])

    def test_an_engine_module_names_only_what_its_code_holds(self):
        # TJ-4075: a local the walk sets, named under the solver, which
        # only mentions it in a comment.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            eng = root / "src" / "taxjson" / "engine"
            eng.mkdir(parents=True)
            (eng / "solve.py").write_text(
                '"""The loop; see walk_local."""\n# reads deferred_key\n'
                'def loop():\n    return {"real_key": 1}\n')
            (eng / "walk.py").write_text(
                'def walk():\n    walk_local = 1\n    return {"deferred_key": walk_local}\n')
            (root / "src" / "other.py").write_text("# walk_local\n")
            _top_defs.cache_clear()
            _code_text.cache_clear()
            try:
                with mock.patch(f"{__name__}.ROOT", root):
                    bad = []
                    check_refs("x.md:1", "`src/taxjson/engine/solve.py` — `loop`, "
                                         "`real_key`, `walk_local`, `deferred_key`", bad)
                    self.assertEqual(bad, [
                        "x.md:1: `walk_local` is only in src/taxjson/engine/solve.py's "
                        "comments, docstrings or imports: name the engine module whose "
                        "code holds it",
                        "x.md:1: `deferred_key` is only in src/taxjson/engine/solve.py's "
                        "comments, docstrings or imports: name the engine module whose "
                        "code holds it"])
                    bad = []
                    check_refs("x.md:1", "`src/taxjson/engine/walk.py` — `walk_local`, "
                                         "`deferred_key`", bad)
                    self.assertEqual(bad, [])
                    # Outside the engine the text match stands.
                    bad = []
                    check_refs("x.md:1", "`src/other.py` — `walk_local`", bad)
                    self.assertEqual(bad, [])
            finally:
                _top_defs.cache_clear()
                _code_text.cache_clear()

    def test_a_moved_name_its_old_home_only_mentions_fails(self):
        # TJ-2559: `ib_cash_events` moved to lib/cash_readers/ib.py while
        # the parser still named it in a comment, which the text match
        # passed.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "src" / "pkg").mkdir(parents=True)
            (root / "src" / "pkg" / "old.py").write_text(
                '"""Was home to moved_fn."""\n# moved_fn now lives in new.py\n'
                'from pkg.new import imported_fn\n'
                'from pkg.new import reexported_fn as reexported_fn\n'
                'from pkg.new import listed_fn\n'
                '__all__ = ["listed_fn"]\n__all__ += ["listed_fn"]\n'
                'def kept_fn():\n    return used_fn()\n')
            (root / "src" / "pkg" / "new.py").write_text(
                "def moved_fn():\n    pass\n\ndef used_fn():\n    pass\n\n"
                "def imported_fn():\n    pass\n\ndef reexported_fn():\n    pass\n\n"
                "def listed_fn():\n    pass\n")
            _top_defs.cache_clear()
            _code_text.cache_clear()
            try:
                with mock.patch(f"{__name__}.ROOT", root):
                    bad = []
                    check_refs("x.md:1", "`src/pkg/old.py` — `kept_fn`, `used_fn`, `moved_fn`, "
                                         "`imported_fn`, `reexported_fn`, `listed_fn`", bad)
                    # A name a shim only imports and lists in `__all__`
                    # (step 10's lib/core.py) is not defined there either.
                    self.assertEqual(bad, [
                        "x.md:1: `moved_fn` is only in src/pkg/old.py's comments or "
                        "imports; it is defined in src/pkg/new.py",
                        "x.md:1: `imported_fn` is only in src/pkg/old.py's comments or "
                        "imports; it is defined in src/pkg/new.py",
                        "x.md:1: `listed_fn` is only in src/pkg/old.py's comments or "
                        "imports; it is defined in src/pkg/new.py"])
                    bad = []
                    check_refs("x.md:1", "`src/pkg/new.py` — `moved_fn`", bad)
                    self.assertEqual(bad, [])
                    # TJ-3608: the form with no dash, `path` `name`.
                    bad = []
                    check_refs("x.md:1", "the readers (`src/pkg/old.py` `moved_fn`, "
                                         "`src/pkg/new.py` `used_fn`)", bad)
                    self.assertEqual(bad, [
                        "x.md:1: `moved_fn` is only in src/pkg/old.py's comments or "
                        "imports; it is defined in src/pkg/new.py"])
            finally:
                _top_defs.cache_clear()
                _code_text.cache_clear()

    def test_every_quoted_command_exists(self):
        from taxjson.cli.parser import RENAMED_COMMANDS
        self.assertEqual(set(RENAMED_IN), set(RENAMED_COMMANDS))
        cmds = _commands()
        bad = []
        for p in _referencing_files():
            lines = _read(p).splitlines()
            fixed = history_releases(p, lines)
            for n, line in enumerate(lines, 1):
                # A reference's symbols are code text, not commands.
                line = _REF.sub(lambda m: "`" + m.group(1) + "`", line)
                for tok in _TICKS.findall(line):
                    m = re.match(r"^(?:tjs|taxjson)\s+(?:-C\s+\S+\s+)?([a-z][a-z0-9-]*)", tok)
                    if not m or m.group(1) in cmds:
                        continue
                    if renamed_ok(m.group(1), fixed[n - 1]):
                        continue
                    bad.append(f"{p}:{n}: `{tok}` — no command {m.group(1)!r}"
                               + (f" (renamed {RENAMED_COMMANDS[m.group(1)]!r})"
                                  if m.group(1) in RENAMED_COMMANDS else ""))
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
            if fixed and not re.search(r"`v\d+\.\d+\.\d+`|—|^unreleased$", fixed):
                bad.append(f"{title}: Fixed in is a version in backticks, — or unreleased")
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


class TestUpgrading(unittest.TestCase):
    def test_note_headings_are_releases(self):
        # scripts/release.sh --prepare renames "### Unreleased" to the tag
        # (audit 2026-10-22 #9): any other heading is a CHANGELOG release.
        doc = _read("docs/upgrading.md")
        part = doc.split("## Changes that ask something of you", 1)[1]
        part = re.split(r"^## ", part, flags=re.M)[0]
        heads = re.findall(r"^### (.*)$", part, re.M)
        released = _released()
        bad = [h for i, h in enumerate(heads)
               if not (h == "Unreleased" and i == 0)
               and not (re.fullmatch(r"v(\d+\.\d+\.\d+)", h)
                        and h[1:] in released)]
        self.assertEqual(bad, [], "a heading is Unreleased (first) or a CHANGELOG release")


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
