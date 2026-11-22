#!/usr/bin/env python3
"""move_tests: move test modules into their packages, whole or split by
class (PLAN.md step 9).

Usage: scripts/move_tests.py PLACEMENT [--dry-run]

PLACEMENT has one move per line, `OLD_MODULE NEW_PATH` (a whole module)
or `OLD_MODULE::Class NEW_PATH` (a class: a split), NEW_PATH below
tests/ (`rules/ca/sl/test_still_held.py`); `#` starts a comment. For each
whole move it:
  1. `git mv`s the file, making each new folder a package (discovery skips
     a folder without `__init__.py` silently);
  2. ends the module's docstring with `Formerly: OLD_MODULE`, which
     scripts/moved.py and tests/test_moved_tests.py read;
  3. re-keys the per-file counts in scripts/architecture-baseline.json;
  4. names the module dotted where a script runs it by name (RUN_FILES);
  5. rewrites the path `tests/OLD_MODULE.py` in the tracked text files
     (not CHANGELOG.md or backlog.md, which are history, nor the tools,
     TOOLS, which a batch never changes);
then sets ROUND_NAMED_MAX in tests/test_test_layout.py to the new count,
and lists each other mention of an old name for the batch to settle by
hand (CHANGELOG.md and backlog.md apart). A shared helper's `Shared
from:` line (SHARED_FROM) is history too, which scripts/moved.py
resolves: kept as it is, and not listed (TJ-3996). Every move is
checked first (the module there and tracked, the target free, no new
path git ignores or ruff skips, each module's new text computed), and
a failure after that undoes what was done. A RUN_FILES or LAYOUT module moved by an earlier
batch is found by its `Formerly:` line. The batch's proof is
scripts/verify_test_moves.py run as BASE's copy, from git (the command
in its docstring): a run from the checkout is the batch's own copy.

A split (SplitPlan) places every class of OLD_MODULE that runs tests, a
new file per NEW_PATH, from that module alone; OLD_MODULE is removed
(`git rm`). Each new file is OLD's text cut by line ranges (Layout: every
line of OLD in one span, a statement's with the comment and blank lines
above it and the indented comments below it), in OLD's order: its
docstring ending `Formerly: OLD_MODULE`; what runs at import (an import
no statement reads, the hermetic setup, a call, a store, a loop, an
import of a tests/ module that runs code at import (helper_files): in
every file) and unittest's module hooks (`setUpModule`,
`tearDownModule`, `load_tests`: in every file); a decorated def or
class no statement reads (its decorator runs at import: in every file);
the classes placed there, each docstring ending `Formerly:
OLD_MODULE.Class` (one made when it has none, the body's first
statement); and every module-level name those reach (bases, mixins, helpers,
constants), a helper two files reach copied into each, identical. The
one text not OLD's lines: an import of which a file reads some names only
is written with those (import_text, its trailing comment kept). The
architecture baseline's counts for OLD go: each new file's are ruff's
on it (each at most the hits of OLD's lines it took, ruff_bound: a
helper's copy counts again, an import kept for what it runs may add
F401, nothing else may; --dry-run checks it too) and its own suppression
comments. A script that runs OLD by class (`OLD.Class`) names the
class's new module. A stage that picks modules by a grep (stage_patterns:
no-extras' fetch pattern, release-edits' words) picks each new file by
its own text; the ones it no longer picks are listed (they leave it,
their tests run in the suite). Refused, before anything is written,
every problem at once, all modules' (each by hand first): a test
class placed twice or not at all, a placed class that runs no tests,
every class to one file (move it whole), a NEW_PATH taking classes from
two modules; a file that needs a class placed in another (a base, or a
statement that runs everywhere and reads it); a name a test changes
after import (a store into it, a method call on it but a READ_ONLY one,
passing it to a def that changes an argument, a `global` statement,
which counts its name as read and written) that two files reach; a
module-level name only a `global` statement makes, set or read in two
files (global_names); a copied name that holds a temp folder or an open
file (TEMP_CALLS: each file would make its own); state two files would each hold a copy of that code can change
(each copy its own): a copied class's mutable or call-built class
attribute, or a copied mutable value (a list, a dict, a call's result),
that something changes, passes to a call that changes or keeps it
(CHANGING_CALLS, KEEPING, a def of OLD's that changes an argument; a
callee the text does not show, the outcome run records), gives a
second name or hands out (SplitPlan._use); a copied def's mutable default or cache (CACHES); a
copied class's `__init_subclass__` or metaclass (class_state); a read
of a class's links to the others (LINKS: `__subclasses__`, `__mro__`,
`__bases__`) or of the module's own file (OWN_FILE: `__file__`,
`__spec__`, which a new folder changes; tests/_paths.py names the
folders); a statement that changes a module-level name when it is
defined (a nested def's body runs when called: not counted), or reads
its own module's names by name, or imports OLD by name; a second name
for a test class (`TestA2 = TestA`) or one a call makes (`type()`, a
def of OLD's that builds one); a test class bound in an `if` or `try`;
two statements on one line; a star import; a run file naming OLD as a
module or by path, outside a comment (a comment's mention is listed
with the others, to settle); two new files of one name; a comment on
the closing line of the module's or a class's docstring, which the
`Formerly:` line would move away from its text (closing_comment). The
plan lists where OLD branches on the machine (env_branches: `if
shutil.which(...)`, a skipIf, os.environ, `try: import x`): the
verifier's runs see one side. What the text cannot show (state one
class leaves for another, order) the verifier's outcome runs catch.
"""
from __future__ import annotations

import ast
import importlib.util
import io
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path
from typing import Callable, Dict, Iterable, List, NoReturn, Optional, Set, Tuple

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"
BASELINE = Path("scripts") / "architecture-baseline.json"
LAYOUT = Path("tests") / "test_test_layout.py"
# Where a script names a test module to run it.
RUN_FILES = ("scripts/mutation_audit.py", "scripts/ci_no_extras.sh", "scripts/ci.sh",
             ".github/workflows/tests.yml", "tests/test_fix_1029_devtools.py")
HISTORY = ("CHANGELOG.md", "backlog.md")
# Folder names ruff's default `exclude` skips at any depth (ruff 0.17's
# `--show-settings`; the gate's lint stage, CI's lint job and
# check_architecture's ruff counts would never see a module below one;
# `build`, which ruff skips through .gitignore, git check-ignore refuses):
# a target folder takes none of them.
UNLINTED = frozenset((".bzr", ".direnv", ".eggs", ".git", ".git-rewrite", ".hg",
                      ".ipynb_checkpoints", ".mypy_cache", ".nox", ".pants.d", ".pyenv",
                      ".pytest_cache", ".pytype", ".ruff_cache", ".svn", ".tox", ".venv",
                      ".vscode", "__pypackages__", "_build", "buck-out", "dist",
                      "node_modules", "site-packages", "venv"))
# A family whose folder is not its id's last part (`dist` is UNLINTED).
FAMILY_FOLDERS = {"distributions": "DIST"}
# The line naming the test module a shared helper came from
# (scripts/extract_support.py writes it): history, like HISTORY.
SHARED_FROM = "Shared from: "
# The tools: a batch never changes them (scripts/verify_test_moves.py
# runs BASE's copies; a split's plan reads extract_support's analysis),
# so the mover never rewrites them.
TOOLS = ("scripts/move_tests.py", "scripts/verify_test_moves.py", "scripts/moved.py",
         "scripts/extract_support.py")
# The packages a module moves into (PLAN.md step 9): never a helper's
# folder, a fixture's or one named like a module tests import.
PACKAGE_ROOTS = ("rules", "features")
_CODING = re.compile(r"^[ \t\f]*#.*?coding[:=]")
SKIP_PREFIXES = ("audits/", ".ci/")
_PACKAGE_DOCS = {
    ("rules",): "The tests that pin tax-logic rules, by country and rule family\n(PLAN.md step 9: rules/<country>/<family>/test_<subject>.py).",
    ("rules", "ca"): "The tests of Canada's rules, by rule family.",
    ("rules", "us"): "The tests of the United States' rules, by rule family.",
    ("features",): "The tests of what no tax-logic rule states, by area of the\narchitecture map (PLAN.md step 9: features/<area>/test_<subject>.py).",
}


def package_doc(parts: Tuple[str, ...]) -> str:
    if parts in _PACKAGE_DOCS:
        return _PACKAGE_DOCS[parts]
    if len(parts) == 3 and parts[0] == "rules":
        family = FAMILY_FOLDERS.get(parts[2], parts[2]).upper()
        return f"The tests of rule family {parts[1].upper()}-{family}."
    if len(parts) == 2 and parts[0] == "features":
        return f"The tests of {parts[1].replace('_', ' ')} (a feature area)."
    return f"Tests: {'/'.join(parts)}."


def dotted(rel: str) -> str:
    """tests-relative `rules/ca/sl/test_x.py` -> `rules.ca.sl.test_x`."""
    return rel[:-3].replace("/", ".")


def package_dirs_ok(segs: List[str]) -> bool:
    """Folders a module moves into: below a PACKAGE_ROOTS root, each a
    package name, never a helper's (`_x`) or one a module's name shadows
    (`test_x`, which discovery reads as a module)."""
    return (bool(segs) and segs[0] in PACKAGE_ROOTS
            and all(s.isidentifier() and not s.startswith(("_", "test_")) for s in segs))


def feature_areas(repo: Path) -> Optional[Set[str]]:
    """The areas tests/features/ may hold: FEATURE_AREAS's keys in the
    repository's layout test (found by `layout_path`, wherever it moved),
    read as text (a literal dict, annotated or not); None when it names
    none (a repository from before them)."""
    try:
        tree = ast.parse(layout_path(repo / "tests").read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for n in tree.body:
        target = (n.targets[0] if isinstance(n, ast.Assign) and len(n.targets) == 1
                  else n.target if isinstance(n, ast.AnnAssign) else None)
        if isinstance(target, ast.Name) and target.id == "FEATURE_AREAS" and n.value is not None:
            return set(ast.literal_eval(n.value))
    return None


def placement_problem(new: str) -> Optional[str]:
    """NEW (below tests/) as a place to move a module to, or why not."""
    segs = new.split("/")
    if (not new.endswith(".py") or len(segs) < 3 or not package_dirs_ok(segs[:-1])
            or not re.fullmatch(r"test_\w+\.py", segs[-1])):
        return (f"{new!r} is not a path to a test_*.py in a package below tests/rules/ "
                f"or tests/features/ (no folder named `_x` or `test_x`)")
    return None


SPLIT = "::"


def read_placement(text: str) -> List[Tuple[str, str]]:
    """The moves, (OLD, NEW): OLD a module (a whole move) or `OLD::Class`
    (a split's class). A NEW takes one whole module, or classes of one."""
    moves, olds, news = [], set(), set()
    whole: Set[str] = set()
    by_class: Dict[str, str] = {}          # a split's NEW -> its module
    split: Set[str] = set()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            raise SystemExit(f"move_tests: line {n}: want `OLD_MODULE NEW_PATH` or "
                             f"`OLD_MODULE::Class NEW_PATH`: {raw!r}")
        old, new = parts
        m = re.fullmatch(r"(test_\w+)(?:::([A-Za-z_]\w*))?", old)
        if not m:
            raise SystemExit(f"move_tests: line {n}: {old!r} is not a test module name "
                             f"(or `module::Class`)")
        bad = placement_problem(new)
        if bad:
            raise SystemExit(f"move_tests: line {n}: {bad}")
        mod = m.group(1)
        if old in olds:
            raise SystemExit(f"move_tests: line {n}: {old} twice")
        if m.group(2) is None:
            if new in news:
                raise SystemExit(f"move_tests: line {n}: {new} twice")
            whole.add(mod)
        else:
            if by_class.get(new, mod) != mod:
                raise SystemExit(f"move_tests: line {n}: {new} takes classes of {by_class[new]} "
                                 f"and {mod}: a new file comes from one module")
            if new in news and new not in by_class:
                raise SystemExit(f"move_tests: line {n}: {new} twice")
            by_class[new] = mod
            split.add(mod)
        if mod in whole and mod in split:
            raise SystemExit(f"move_tests: line {n}: {mod} moves whole and by class: one or "
                             f"the other")
        olds.add(old)
        news.add(new)
        moves.append((old, new))
    return moves


def closing_comment(text: str, end: int) -> bool:
    """TEXT has a comment after a docstring that ends at offset END, on its
    closing line: the line `Formerly:` goes before, so the comment would end
    up beside it, away from the text it is about (a `# pii-ok`)."""
    return text[end:].split("\n", 1)[0].lstrip().startswith("#")


def add_formerly(text: str, old: str) -> str:
    """The module's text with its docstring ending `Formerly: OLD`
    (a docstring made when it has none). ValueError: a comment on the
    docstring's closing line (closing_comment)."""
    tree = ast.parse(text)
    first = tree.body[0] if tree.body else None
    line = f"Formerly: {old}"
    lines = split_lines(text)
    nl = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"   # the file's line end
    if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        # After a shebang and a coding line (on line one or two), which
        # must stay where they are to count.
        k = 1 if lines and lines[0].startswith("#!") else 0
        for i in range(min(2, len(lines))):
            if _CODING.match(lines[i]):
                k = i + 1
        return "".join(lines[:k]) + f'"""{line}"""{nl}' + "".join(lines[k:])
    starts = [0]
    for ln in lines:
        starts.append(starts[-1] + len(ln))
    end_line = lines[first.end_lineno - 1]
    # The AST's columns count UTF-8 bytes; the text, characters.
    col = len(end_line.encode("utf-8")[:first.end_col_offset].decode("utf-8"))
    end = starts[first.end_lineno - 1] + col
    seg = text[:end]
    q = seg[-3:]
    if q not in ('"""', "'''"):
        raise ValueError("the docstring is not triple-quoted: make it so first")
    if closing_comment(text, end):
        raise ValueError(f"a comment on the docstring's closing line (line {first.end_lineno}), "
                         f"which the `Formerly:` line would move away from its text: put it on "
                         f"a line of its own first")
    body_end = end - len(q)
    body = text[:body_end]
    stripped = body.rstrip()
    own_line = body[len(stripped):].count("\n") > 0
    new = stripped + nl + nl + line + (nl if own_line else "") + q
    out = new + text[end:]
    assert (ast.get_docstring(ast.parse(out), clean=True) or "").splitlines()[-1] == line
    return out


BASELINE_KINDS = ("ruff", "noqa", "print_calls", "money")


def rekey_baseline(doc: dict, renames: Dict[str, str]) -> dict:
    for kind in BASELINE_KINDS:
        sect = doc.get(kind)
        if not isinstance(sect, dict):
            continue
        for code, per in list(sect.items()):
            if isinstance(per, dict):
                sect[code] = dict(sorted((renames.get(k, k), v) for k, v in per.items()))
    return doc


def split_baseline(doc: dict, gone: Iterable[str], counts: Dict[str, Dict[str, Dict[str, int]]]
                   ) -> dict:
    """DOC without the files GONE (split modules' paths), with COUNTS
    (kind -> code -> {new path: count}) added; a code with no file left
    goes."""
    gone = set(gone)
    for kind in BASELINE_KINDS:
        sect = doc.get(kind)
        if not isinstance(sect, dict):
            continue
        for code, per in list(sect.items()):
            if isinstance(per, dict):
                per = {k: v for k, v in per.items() if k not in gone}
                per.update({k: v for k, v in counts.get(kind, {}).get(code, {}).items() if v})
                if per:
                    sect[code] = dict(sorted(per.items()))
                else:
                    del sect[code]
        for code, per in counts.get(kind, {}).items():
            if code not in sect and any(per.values()):
                sect[code] = dict(sorted((k, v) for k, v in per.items() if v))
        doc[kind] = dict(sorted(sect.items()))
    return doc


_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")


def split_lines(text: str) -> List[str]:
    """TEXT's lines, their ends kept, split where Python's parser splits
    them (`\\r\\n`, `\\r`, `\\n`), not also at a form feed or a Unicode
    separator as str.splitlines does: the AST's line numbers count these."""
    return _LINE.findall(text)


def name_pattern(old: str) -> "re.Pattern[str]":
    """OLD as a module name, not inside a longer name, a path or `OLD.py`."""
    return re.compile(rf"(?<![\w/.]){re.escape(old)}(?![\w]|\.py)")


def path_pattern(old: str) -> "re.Pattern[str]":
    """`tests/OLD.py` as a whole path: not the end of a longer one
    (`packages/x/tests/OLD.py`, `$WORK/tests/OLD.py`), not `OLD.py.bak`
    (a sentence's full stop after it is not a suffix)."""
    return re.compile(rf"(?<![\w/.-])tests/{re.escape(old)}\.py(?!\w|\.\w)")


def read_exact(p: Path) -> str:
    """A file's text with its line ends as they are (CRLF kept)."""
    with open(p, encoding="utf-8", newline="") as f:
        return f.read()


def write_exact(p: Path, text: str) -> None:
    """Write TEXT to P as it is, all or nothing: a temporary file beside it,
    given P's mode, then put in its place, so a write that fails (a full
    disk, a file-size limit) leaves P as it was (TJ-3725)."""
    tmp = p.with_name(f".{p.name}.move-tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        if p.exists():
            os.chmod(tmp, p.stat().st_mode & 0o7777)
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _formerly_map(tests: Path):
    return _sibling("moved").formerly_map(tests)


def moved_path(tests: Path, rel: str) -> str:
    """REL (a RUN_FILES or LAYOUT path, `tests/test_x.py`) where it is now:
    a top-level module gone from tests/ is where its `Formerly:` line says
    (the tools keep the old path: a batch never changes them)."""
    m = re.fullmatch(r"tests/(test_\w+)\.py", rel)
    if not m or (tests / f"{m.group(1)}.py").exists():
        return rel
    places = [r for r, c in _formerly_map(tests).get(m.group(1), []) if not c]
    return f"tests/{places[0]}" if len(places) == 1 else rel


def run_files(tests: Path) -> Tuple[str, ...]:
    return tuple(moved_path(tests, f) for f in RUN_FILES)


def layout_path(tests: Path) -> Path:
    return tests.parent / moved_path(tests, LAYOUT.as_posix())


def round_named_count(tests: Path, layout: Optional[Path] = None) -> int:
    """The round-named modules below TESTS, by the patterns of LAYOUT (the
    layout test's file; found by `layout_path` when not given)."""
    tree = ast.parse((layout or layout_path(tests)).read_text(encoding="utf-8"))
    rxs, skip = {}, ()
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name):
            name = n.targets[0].id
            if name in ("_ROUND_NAMED", "_ROUND_WORDS"):
                rxs[name] = re.compile(n.value.args[0].value)
            elif name == "_NOT_ROUND_NAMED":
                skip = ast.literal_eval(n.value)
    assert len(rxs) == 2, "tests/test_test_layout.py lacks _ROUND_NAMED or _ROUND_WORDS"
    return sum(1 for p in tests.rglob("test_*.py")
               if "__pycache__" not in p.parts and p.name not in skip
               and any(rx.match(p.name) for rx in rxs.values()))


def tracked_text_files(repo: Path) -> List[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=repo, capture_output=True,
                         check=True).stdout.decode().split("\0")
    return [f for f in out if f and not f.startswith(SKIP_PREFIXES)
            and (repo / f).is_file() and not f.endswith((".png", ".pdf", ".xlsx", ".gz"))]


def _read(p: Path) -> Optional[str]:
    try:
        return read_exact(p)
    except (UnicodeDecodeError, OSError):
        return None


def is_formerly_line(ln: str) -> bool:
    """LN is a `Formerly:` line: one of a docstring's, or one the mover
    made a whole docstring (`\"\"\"Formerly: test_x.TestY\"\"\"`)."""
    return re.sub(r'^\s*(?:[rRuU]?(?:"""|\'\'\'))?', "", ln).startswith("Formerly: ")


def rename_text(f: str, text: str, olds: Dict[str, str],
                run: Tuple[str, ...] = RUN_FILES,
                classes: Optional[Dict[str, str]] = None) -> str:
    """File f's text under the renames (OLD module -> new path below
    tests/): the path everywhere, the dotted name where a script runs a
    module (RUN, the RUN_FILES where they are now: run_files), and there a
    split's class (CLASSES, `OLD.Class` -> its new path) as
    `new.dotted.Class`; history (HISTORY) and the tools (TOOLS) as they
    were."""
    if f in HISTORY or f in TOOLS:
        return text
    for old, new in olds.items():
        text = path_pattern(old).sub(f"tests/{new}", text)
    if f in run:      # a module moved here keeps its own `Formerly:` line
        lines = split_lines(text)
        for i, ln in enumerate(lines):
            if not is_formerly_line(ln):
                for name, new in (classes or {}).items():
                    ln = re.sub(rf"(?<![\w/.]){re.escape(name)}(?!\w)",
                                f"{dotted(new)}.{name.split('.', 1)[1]}", ln)
                for old, new in olds.items():
                    ln = name_pattern(old).sub(dotted(new), ln)
                lines[i] = ln
        text = "".join(lines)
    return text


def is_shared_helper(f: str) -> bool:
    """F (repo-relative) is a shared test helper: a module of tests/_support/,
    or a plugin's private module beside its tests (`packages/P/tests/_x.py`)."""
    segs = f.split("/")
    return f.endswith(".py") and (
        (len(segs) >= 3 and segs[:2] == ["tests", "_support"])
        or (len(segs) == 4 and segs[0] == "packages" and segs[2] == "tests"
            and segs[3].startswith("_")))


def shared_from_lines(f: str, text: str) -> Set[int]:
    """The numbers of F's lines that are its `Shared from:` line: in a
    shared helper (is_shared_helper), a line of its module docstring that
    starts so (TJ-3996). Nothing else: not a comment, a string or another
    docstring, nor a test module's line."""
    if not is_shared_helper(f):
        return set()
    try:
        body = ast.parse(text).body
    except SyntaxError:
        return set()
    doc = body[0] if body else None
    if not (isinstance(doc, ast.Expr) and isinstance(doc.value, ast.Constant)
            and isinstance(doc.value.value, str)):
        return set()
    lines = split_lines(text)
    return {n for n in range(doc.lineno, doc.end_lineno + 1)
            if lines[n - 1].strip().startswith(SHARED_FROM)}


# --------------------------------------------------------------- splits
# unittest's module hooks: a split copies them into every new file.
HOOKS = ("setUpModule", "tearDownModule", "load_tests")
IMPORT_WIDTH = 99
FORMERLY = "Formerly: "
Keep = Dict[int, Optional[Tuple[str, ...]]]


def _sibling(name: str):
    """A script beside this one, as a module (scripts/ is no package)."""
    spec = importlib.util.spec_from_file_location(
        f"_mover_{name}", Path(__file__).resolve().parent / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _is_doc(n: ast.AST) -> bool:
    return (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str))


def _first(n: ast.stmt) -> int:
    """N's first line: its decorators'."""
    return min([n.lineno] + [d.lineno for d in getattr(n, "decorator_list", ())])


def _col(line: str, byte_col: int) -> int:
    """The AST's column (UTF-8 bytes) as characters of LINE."""
    return len(line.encode("utf-8")[:byte_col].decode("utf-8"))


def _alias(a: ast.alias) -> str:
    return a.name + (f" as {a.asname}" if a.asname else "")


class Layout:
    """A module's lines as a split cuts them (1-based, inclusive): the head
    (what comes before the first statement after the docstring, and the
    docstring), each statement's span (the comment and blank lines since
    the statement before, its decorators, itself, and the indented comment
    lines below it), the foot. Every line is in exactly one. ValueError:
    two statements on one line."""

    def __init__(self, text: str):
        self.text = text
        self.lines = split_lines(text)
        self.nl = "\r\n" if self.lines and self.lines[0].endswith("\r\n") else "\n"
        self.tree = ast.parse(text)
        body = self.tree.body
        doc = body[0] if body and _is_doc(body[0]) else None
        self.stmts: List[ast.stmt] = body[1:] if doc else list(body)
        self.head = doc.end_lineno if doc else (
            _first(self.stmts[0]) - 1 if self.stmts else len(self.lines))
        self.spans: List[Tuple[int, int]] = []
        prev, end = self.head, (doc.end_lineno if doc else 0)
        for s in self.stmts:
            if _first(s) <= end:
                raise ValueError(f"line {_first(s)} holds two statements: put each on lines "
                                 f"of its own first")
            last, k = s.end_lineno, s.end_lineno + 1
            while k <= len(self.lines):
                ln = self.lines[k - 1]
                if ln.strip() and not (ln[0] in " \t\f" and ln.lstrip().startswith("#")):
                    break
                if ln.strip():
                    last = k             # an indented comment below it is its own
                k += 1
            self.spans.append((prev + 1, last))
            prev, end = last, s.end_lineno
        self.foot = prev + 1

    def stmt_lines(self, i: int) -> List[str]:
        a, b = self.spans[i]
        return self.lines[a - 1:b]


def import_text(s: ast.stmt, aliases: List[ast.alias], indent: str = "", nl: str = "\n") -> str:
    """S, an import, with ALIASES only (no line end): `import a, b`, or
    `from M import a, b` wrapped in parentheses past IMPORT_WIDTH."""
    names = [_alias(a) for a in aliases]
    if isinstance(s, ast.Import):
        return f"{indent}import {', '.join(names)}"
    assert isinstance(s, ast.ImportFrom)
    module = "." * s.level + (s.module or "")
    one = f"{indent}from {module} import {', '.join(names)}"
    if len(one) <= IMPORT_WIDTH:
        return one
    out, line, pad = [f"{indent}from {module} import ("], "", indent + "    "
    for piece in (x + "," for x in names):
        if line and len(pad) + len(line) + 1 + len(piece) > IMPORT_WIDTH:
            out.append(pad + line)
            line = ""
        line = f"{line} {piece}" if line else piece
    return nl.join(out + [pad + line, f"{indent})"])


def _comment_rows(text: str) -> Set[int]:
    """The rows (1-based) of TEXT's comment tokens."""
    out: Set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                out.add(tok.start[0])
    except (tokenize.TokenError, SyntaxError):
        pass
    return out


def _trimmed(lay: Layout, i: int, keep: Tuple[str, ...]) -> List[str]:
    """Statement I's span, an import, with the names KEEP only: the lines
    above it and below it as they are, the statement written with them and
    its last line's comment kept. ValueError: a comment inside it."""
    s = lay.stmts[i]
    if not isinstance(s, (ast.Import, ast.ImportFrom)):
        raise ValueError(f"line {s.lineno}: not an import, and some of it kept")
    aliases = [a for a in s.names if _alias(a) in keep]
    if not aliases or len(aliases) != len(keep):
        raise ValueError(f"line {s.lineno}: names it does not import: {', '.join(keep)}")
    own = "".join(lay.lines[s.lineno - 1:s.end_lineno])
    if any(r < s.end_lineno - s.lineno + 1 for r in _comment_rows(own)):
        raise ValueError(f"line {s.lineno}: an import with a comment inside it, of which a new "
                         f"file reads only some names: put the comment above it first")
    first, last = lay.lines[s.lineno - 1], lay.lines[s.end_lineno - 1]
    indent = first[:_col(first, s.col_offset)]
    tail = last[_col(last, s.end_col_offset):]
    a, b = lay.spans[i]
    new = import_text(s, aliases, indent, lay.nl) + (tail if tail.strip() else lay.nl)
    return lay.lines[a - 1:s.lineno - 1] + [new] + lay.lines[s.end_lineno:b]


def _header_end(lines: List[str], node: ast.ClassDef) -> int:
    """The line (1-based) of the colon that ends class NODE's header."""
    depth = 0
    src = io.StringIO("".join(lines[node.lineno - 1:]))
    for tok in tokenize.generate_tokens(src.readline):
        if tok.type == tokenize.OP and tok.string in "([{":
            depth += 1
        elif tok.type == tokenize.OP and tok.string in ")]}":
            depth -= 1
        elif tok.type == tokenize.OP and tok.string == ":" and depth == 0:
            return node.lineno + tok.start[0] - 1
    raise ValueError(f"{node.name}: no end to its header")


def add_class_formerly(text: str, cls: str, line: str) -> str:
    """TEXT with module-level class CLS's docstring ending LINE (one
    made when it has none: the body's first statement, right below the
    header). ValueError: a body on the class's own line, a docstring not
    triple-quoted, a comment on its closing line (closing_comment)."""
    tree = ast.parse(text)
    node = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls), None)
    if node is None:
        raise ValueError(f"no module-level class {cls}")
    lines = split_lines(text)
    first = node.body[0]
    start = _first(first)
    ln = lines[start - 1]
    lead = re.match(r"[ \t\f]*", ln).group()
    own = ln[len(lead):].startswith("@") if start != first.lineno else \
        _col(ln, first.col_offset) == len(lead)
    if not own or start <= node.lineno:
        raise ValueError(f"{cls}: its body is on the line of `class`: give it lines of its own")
    nl = "\r\n" if lines[start - 1].endswith("\r\n") else "\n"
    if _is_doc(first):
        starts = [0]
        for ln in lines:
            starts.append(starts[-1] + len(ln))
        e = first.value
        end = starts[e.end_lineno - 1] + _col(lines[e.end_lineno - 1], e.end_col_offset)
        q = text[end - 3:end]
        if q not in ('"""', "'''"):
            raise ValueError(f"{cls}: its docstring is not triple-quoted: make it so first")
        if closing_comment(text, end):
            raise ValueError(f"{cls}: a comment on its docstring's closing line (line "
                             f"{e.end_lineno}), which the `Formerly:` line would move away from "
                             f"its text: put it on a line of its own first")
        body = text[:end - 3]
        stripped = body.rstrip()
        own_line = "\n" in body[len(stripped):] or "\r" in body[len(stripped):]
        out = (stripped + nl + nl + lead + line + (nl + lead if own_line else "") + q
               + text[end:])
    else:          # the first statement of the body: right below the header's colon
        hdr = _header_end(lines, node)
        out = "".join(lines[:hdr]) + f'{lead}"""{line}"""{nl}' + "".join(lines[hdr:])
    done = next(n for n in ast.parse(out).body if isinstance(n, ast.ClassDef) and n.name == cls)
    assert (ast.get_docstring(done, clean=True) or "").splitlines()[-1] == line
    return out


def fragment_text(text: str, old: str, keep: Keep, placed: Iterable[str]) -> str:
    """The new file of a split: OLD's TEXT cut to its head, the statements
    KEEP names (index of Layout.stmts -> None, the whole span, or the
    names an import keeps), in OLD's order, and its foot; the classes
    PLACED with their `Formerly: OLD.Class` line, the module's docstring
    with `Formerly: OLD`. A kept statement after statements left out has at
    least the blank lines that led them (the line between two import
    groups stays when the second's first import goes)."""
    lay = Layout(text)
    out = lay.lines[:lay.head]
    carry = 0         # the blank lines leading a run of statements left out
    for i in range(len(lay.stmts)):
        lead = len(list(itertools.takewhile(lambda ln: not ln.strip(), lay.stmt_lines(i))))
        if i not in keep:
            carry = carry if i and i - 1 not in keep else lead
            continue
        names = keep[i]
        out += [lay.nl] * max(0, carry - lead)        # a group's blank line, its first gone
        out += lay.stmt_lines(i) if names is None else _trimmed(lay, i, names)
        carry = 0
    out += lay.lines[lay.foot - 1:]
    frag = "".join(out)
    for cls in sorted(set(placed)):
        frag = add_class_formerly(frag, cls, f"{FORMERLY}{old}.{cls}")
    return add_formerly(frag, old)


def class_lines(node: ast.ClassDef) -> List[str]:
    """The old names a class's docstring ends with (`Formerly: OLD.Class`)."""
    return [ln[len(FORMERLY):].strip() for ln in
            (ast.get_docstring(node, clean=True) or "").splitlines() if ln.startswith(FORMERLY)]


def taken_lines(text: str, keep: Keep) -> Set[int]:
    """OLD's lines (1-based) a new file holding KEEP took: its head, the
    spans of the statements it keeps (an import's whole span, however many
    names it keeps), its foot."""
    lay = Layout(text)
    out = set(range(1, lay.head + 1)) | set(range(lay.foot, len(lay.lines) + 1))
    for i in keep:
        a, b = lay.spans[i]
        out |= set(range(a, b + 1))
    return out


def ruff_hits(root: Path, rels: List[str]) -> Optional[Dict[str, List[Tuple[str, int]]]]:
    """ruff's hits in the files RELS below ROOT, as check_architecture
    counts them (its rule set, suppressions ignored): {rel: [(code,
    line)]}. None when ruff does not run (the dev venv's Python has it)."""
    r = subprocess.run([sys.executable, "-I", "-m", "ruff", "check", "--no-cache", "--ignore-noqa",
                        "--output-format=json", "--", *rels], cwd=root, capture_output=True,
                       text=True)
    try:
        hits = json.loads(r.stdout)
    except ValueError:
        return None
    if r.returncode not in (0, 1) or not isinstance(hits, list) or (r.returncode == 1 and not hits):
        return None
    out: Dict[str, List[Tuple[str, int]]] = {rel: [] for rel in rels}
    for h in hits:
        rel = Path(h["filename"]).resolve().relative_to(root.resolve()).as_posix()
        out.setdefault(rel, []).append((h["code"] or "syntax", h["location"]["row"]))
    return out


def counted(hits: Iterable[Tuple[str, int]], lines: Optional[Set[int]] = None) -> Dict[str, int]:
    """{code: count} of HITS, (code, line), on LINES (all when None)."""
    out: Dict[str, int] = {}
    for code, row in hits:
        if lines is None or row in lines:
            out[code] = out.get(code, 0) + 1
    return out


# Methods that only read the object they are called on: any other method
# called on a module-level value two new files copy counts as a change.
READ_ONLY = frozenset("""get keys values items copy count index find rfind startswith endswith
    format format_map join split rsplit splitlines strip lstrip rstrip lower upper title casefold
    capitalize swapcase replace encode decode partition rpartition zfill ljust rjust center
    expandtabs translate isdigit isalpha isalnum isspace isupper islower isnumeric isdecimal
    isidentifier isoformat strftime date time timestamp total_seconds weekday isoweekday
    toordinal exists is_dir is_file read_text read_bytes resolve relative_to joinpath
    with_name with_suffix with_stem as_posix as_uri glob rglob iterdir stat match fullmatch
    search findall finditer sub subn group groups groupdict span start end union intersection
    difference symmetric_difference issubset issuperset isdisjoint quantize normalize
    as_tuple is_zero is_signed to_eng_string hex bit_length conjugate most_common elements
    total __contains__ __getitem__ __len__ __iter__""".split())
# Calls that build a value no method changes (a class attribute so built
# is no state a copy would hold apart).
IMMUTABLE_CALLS = frozenset({"frozenset", "tuple", "str", "int", "float", "bool", "bytes",
                             "Decimal", "Fraction", "Path", "PurePath", "PurePosixPath", "date",
                             "datetime", "timedelta", "compile", "MappingProxyType",
                             "namedtuple", "dedent", "dumps", "join", "format", "strip", "lstrip",
                             "rstrip", "replace", "lower", "upper", "encode", "decode",
                             "strftime", "isoformat", "object", "getLogger", "partial",
                             "itemgetter", "attrgetter", "mkdtemp", "gettempdir", "getcwd", "cwd",
                             "home", "resolve", "absolute", "expanduser", "staticmethod",
                             "classmethod", "property"})
# The runner imports it before any test module (`python -m unittest
# _hermetic test_x`): its setup at import is every file's already.
RUNNER_SETUP = frozenset({"_hermetic"})
# Calls by name that only read the values passed to them (builtins and a
# few of the standard library's; READ_ONLY's methods and a test's asserts,
# TEST_METHODS, too): a value passed to one is still only its holder's.
READING_CALLS = frozenset("""len sorted list tuple set frozenset dict str repr bool int float
    complex bytes any all sum min max enumerate zip iter reversed isinstance issubclass type id
    hash format print map filter next divmod abs round ord chr dumps deepcopy copy dedent
    Counter OrderedDict Decimal Fraction Path PurePath""".split())
TEST_METHODS = ("assert", "fail", "subTest", "skipTest")
# Calls that change an object passed to them (the standard library's: heapq,
# bisect, random, operator, setattr) or hand out its namespace (vars).
CHANGING_CALLS = frozenset("""heappush heappop heapify heapreplace heappushpop insort
    insort_left insort_right shuffle setattr delattr setitem delitem iadd iconcat vars""".split())
# Methods that keep what is passed to them in their own object (a second
# reference to it); EXTENDING ones keep its items only. os.environ keeps
# strings: what it is given it only reads.
KEEPING = frozenset("""append appendleft add insert setdefault put put_nowait register
    __setitem__ extend extendleft update""".split())
EXTENDING = frozenset({"extend", "extendleft", "update"})
# Decorators that keep a cache for what they wrap (functools'): each copy its own.
CACHES = frozenset({"lru_cache", "cache"})
# What links a class to its module's other classes, which a split changes:
# each file's copies are its own classes, and through a base a test reaches
# a copied base's state.
LINKS = {"__subclasses__": "a class's subclasses: a file's copy has its own file's only",
         "__mro__": "a class's bases, through which a copied base's state is changed",
         "__bases__": "a class's bases, through which a copied base's state is changed",
         "__base__": "a class's base, through which a copied base's state is changed",
         "mro": "a class's bases, through which a copied base's state is changed"}
# The names by which a module reads its own file (tests/test_test_layout.py
# allows them in tests/_paths.py only).
OWN_FILE = ("__file__", "__spec__")
DECORATED = "a decorated definition no statement reads: its decorator runs at import"
# Calls whose value is a place outside the module, made anew by each copy:
# a temp folder, an open file.
TEMP_CALLS = {"mkdtemp": "a temp folder", "TemporaryDirectory": "a temp folder",
              "mkstemp": "an open file", "TemporaryFile": "an open file",
              "NamedTemporaryFile": "an open file", "SpooledTemporaryFile": "an open file",
              "open": "an open file"}
# What a test's code can branch on that differs from one machine to another
# (the verifier sees the side its own run takes).
ENV_CALLS = frozenset({"which", "find_spec", "getenv", "import_module", "system", "machine",
                       "python_implementation", "skipIf", "skipUnless"})
ENV_NAMES = {("os", "environ"), ("sys", "platform"), ("os", "name"), ("sys", "version_info"),
             ("sys", "flags")}


def global_names(s: ast.AST) -> Set[str]:
    """The names a `global` statement in S declares."""
    return {n for g in ast.walk(s) if isinstance(g, ast.Global) for n in g.names}


def env_read(x: Optional[ast.AST]) -> Optional[str]:
    """What expression X reads of the machine (ENV_CALLS, ENV_NAMES), or None."""
    for y in ast.walk(x) if x is not None else ():
        if isinstance(y, ast.Call) and _func_name(y.func) in ENV_CALLS:
            return ast.unparse(y.func)
        if isinstance(y, ast.Attribute) and isinstance(y.value, ast.Name) and \
                (y.value.id, y.attr) in ENV_NAMES:
            return f"{y.value.id}.{y.attr}"
    return None


def _value_state(v: ast.AST, names: Iterable[str] = ()) -> Optional[str]:
    """What a value V holds that a method can change: a list, dict, set or
    comprehension, a call's result (but IMMUTABLE_CALLS), a module-level
    name NAMES has (a second name for such a value)."""
    if isinstance(v, ast.Name) and v.id in names:
        return f"{v.id}'s value"
    if isinstance(v, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp,
                      ast.GeneratorExp)):
        return f"a {type(v).__name__.lower()}"
    if isinstance(v, ast.Call):
        f = v.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        return None if name in IMMUTABLE_CALLS and not any(
            map(_items_state, v.args)) else f"{name or 'a call'}()'s value"
    if isinstance(v, ast.Tuple):
        return next((x for x in map(_value_state, v.elts) if x), None)
    return None


def _items_state(v: ast.AST) -> Optional[str]:
    """What an immutable call's argument V passes on that a method can
    change: a literal's items' (frozenset({1}) holds none), else V's own."""
    if isinstance(v, (ast.List, ast.Set, ast.Tuple)):
        return next((x for x in map(_value_state, v.elts) if x), None)
    if isinstance(v, ast.Dict):
        return next((x for x in map(_value_state, v.values) if x), None)
    return _value_state(v)


def class_state(c: ast.ClassDef,
                changed: Optional[Callable[[str, bool], Optional[str]]] = None,
                mutable: Iterable[str] = ()) -> Optional[str]:
    """What a copy of class C would hold apart (each file's copy its own):
    a metaclass, `__init_subclass__`, a class attribute a method can change
    (_value_state; MUTABLE: the module-level names of such values) that
    CHANGED (the attribute's name, whether its items can change -> how code
    can change it, or None; no CHANGED: however) finds changeable. None for
    none."""
    if any(k.arg == "metaclass" for k in c.keywords):
        return "a metaclass"
    for n in c.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "__init_subclass__":
            return "__init_subclass__"
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
            why = _value_state(n.value, mutable)
            tg = n.targets[0] if isinstance(n, ast.Assign) else n.target
            where = (changed(tg.id, _deep_items(n.value)) if changed and isinstance(tg, ast.Name)
                     else "") if why else None
            if where is not None:
                return (f"a class attribute that is {why} ({ast.unparse(tg)}, line {n.lineno})"
                        + (f", {where}" if where else ""))
    return None


def _deep_items(v: ast.AST) -> bool:
    """Value V's items may be objects a method changes: V is not a literal
    of constants and immutable values (_value_state) only."""
    def plain(x: ast.AST) -> bool:
        return _value_state(x) is None and not isinstance(x, (ast.Name, ast.Attribute,
                                                               ast.Subscript, ast.Starred))
    if isinstance(v, (ast.List, ast.Set, ast.Tuple)):
        return not all(map(plain, v.elts))
    if isinstance(v, ast.Dict):
        return not all(map(plain, v.values))
    return True


def _func_name(f: Optional[ast.AST]) -> Optional[str]:
    """The name of a call's function F: a name's, an attribute's (`m.f`)."""
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None


def _call_name(call: Optional[ast.AST]) -> Optional[str]:
    return _func_name(call.func) if isinstance(call, ast.Call) else None


def _binds(fn: ast.AST, name: str) -> bool:
    """Function FN binds NAME itself: a parameter, or an assignment
    without `global`."""
    a = fn.args
    if name in {x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs,
                                *[v for v in (a.vararg, a.kwarg) if v]]}:
        return True
    nodes = [x for st in (fn.body if isinstance(fn.body, list) else [fn.body])
             for x in ast.walk(st)]
    if any(isinstance(g, (ast.Global, ast.Nonlocal)) and name in g.names for g in nodes):
        return False
    return any(isinstance(x, ast.Name) and x.id == name and isinstance(x.ctx, ast.Store)
               for x in nodes)


def _through(x: ast.AST, attr: str) -> bool:
    """X is an attribute named ATTR of something, or reached through one
    (`self.seen`, `cls.seen[0]`, `self.seen.x`)."""
    while isinstance(x, (ast.Subscript, ast.Attribute)):
        if isinstance(x, ast.Attribute) and x.attr == attr:
            return True
        x = x.value
    return False


def helper_files(tests: Path, module: str) -> List[Path]:
    """The files below TESTS that `import MODULE` (dotted) runs: each
    package's `__init__.py` on the way and the module's own; [] when it is
    no module of TESTS."""
    parts, out = module.split("."), []
    for k in range(1, len(parts) + 1):
        d = tests.joinpath(*parts[:k])
        if (d / "__init__.py").is_file():
            out.append(d / "__init__.py")
        elif k == len(parts) and d.with_suffix(".py").is_file():
            out.append(d.with_suffix(".py"))
        else:
            break
    return out


def stage_patterns(repo: Path) -> Dict[str, Tuple[str, bool]]:
    """The stages that pick test modules by their text: name -> (the
    pattern, whether a line naming git does not count): the no-extras
    stage's plugin grep (scripts/ci_no_extras.sh PLUGIN_RE), the
    release-edits stage's words (release_words)."""
    out: Dict[str, Tuple[str, bool]] = {}
    nx = _read(repo / "scripts" / "ci_no_extras.sh") or ""
    m = re.search(r'^PLUGIN_RE="((?:[^"\\]|\\.)*)"', nx, re.M)
    if m:
        out["no-extras"] = (re.sub(r'\\(["\\$`])', r"\1", m.group(1)), True)
    words = release_words(repo)
    if words:
        out["release-edits"] = (words, False)
    return out


def stage_selects(pattern: Tuple[str, bool], text: str) -> bool:
    """A stage's grep (stage_patterns) picks a module of TEXT: a line
    matching its pattern (and, for the plugin's, naming no `git`)."""
    rx, skip_git = pattern
    return any(re.search(rx, ln) and not (skip_git and re.search(r"\bgit\b", ln))
               for ln in text.splitlines())


class SplitPlan:
    """What splitting tests/OLD.py by class does, worked out before anything
    is written: each new file's text (fragment_text), what it copies, what
    no new file takes. Refused (SystemExit) as the module docstring says,
    a line for each problem, every one of a stage at once (the module and
    the placement; what the files would share; the texts). The verifier
    builds BASE's again from HEAD's class lines: the same refusals, the
    same texts."""

    def __init__(self, repo: Path, old: str, places: List[Tuple[str, str]]):
        self.repo, self.old, self.places = repo, old, places
        self.problems: List[str] = []
        self.hits: List[Tuple[str, int]] = []      # ruff's in OLD (_check runs it)
        self.text = read_exact(repo / "tests" / f"{old}.py")
        try:
            lay = self.lay = Layout(self.text)
        except (ValueError, SyntaxError) as e:
            self.refuse(str(e))
        ES = self.ES = _sibling("extract_support")
        stmts = lay.stmts
        self.parents = {c: p for p in ast.walk(lay.tree) for c in ast.iter_child_nodes(p)}
        self.defs: Dict[str, List[ast.stmt]] = {}       # OLD's defs and classes, any depth
        for x in ast.walk(lay.tree):
            if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.defs.setdefault(x.name, []).append(x)
        self.bound: Dict[str, List[int]] = {}
        for i, s in enumerate(stmts):
            for name in ES.module_binds(s):
                self.bound.setdefault(name, []).append(i)
        if "*" in self.bound:
            self.refuse(f"line {stmts[self.bound['*'][0]].lineno} star-imports: what the classes "
                        f"read from it is not known")
        # A name a function declares `global` is read by it too (`_N += 1`,
        # `_N = 1`): its file holds the name, which a split may copy.
        self.reads = [(ES.module_reads(s) | global_names(s)) & set(self.bound) for s in stmts]
        self.data = {n for s in stmts if not isinstance(s, (ast.Import, ast.ImportFrom,
                                                             ast.FunctionDef, ast.AsyncFunctionDef,
                                                             ast.ClassDef))
                     for n in ES.module_binds(s)}       # module-level values
        self._own_imports()
        self._links()
        self._placement()
        self.effects: Dict[int, str] = {}               # an import kept for what it runs
        self.everywhere = self._everywhere()
        self.keeps: Dict[str, Keep] = {}
        self.allow: Dict[str, Dict[str, int]] = {}      # ruff hits a file may add (F401)
        wants = {new: self._closure(new) for new in self.news}
        self._crossings(wants)
        self._shared_state(wants)
        for new, (want, names) in wants.items():
            keep: Keep = {}
            for i in sorted(want):
                s = stmts[i]
                if isinstance(s, (ast.Import, ast.ImportFrom)) and (
                        i not in self.everywhere or i in self.effects):
                    kept = tuple(_alias(a) for a in s.names
                                 if (a.asname or a.name).split(".")[0] in names)
                    keep[i] = None if len(kept) in (0, len(s.names)) else kept
                    if not kept:         # kept whole for what it runs: unused here
                        self.allow.setdefault(new, {})["F401"] = \
                            self.allow.get(new, {}).get("F401", 0) + len(s.names)
                else:
                    keep[i] = None
            self.keeps[new] = keep
        self.texts: Dict[str, str] = {}
        for new, keep in self.keeps.items():
            placed = [c for c, n in self.placed.items() if n == new]
            try:
                text = fragment_text(self.text, old, keep, placed)
                ast.parse(text)
            except (ValueError, SyntaxError, AssertionError) as e:
                self.problem(f"tests/{new}: {e}")
                continue
            self.texts[new] = text
        self.settle()
        taken = set().union(*[set(k) for k in self.keeps.values()])
        self.left = [i for i in range(len(stmts)) if i not in taken]
        self.copied = [i for i in range(len(stmts)) if i not in self.everywhere and not isinstance(
            stmts[i], (ast.Import, ast.ImportFrom)) and sum(i in k for k in self.keeps.values()) > 1]

    def problem(self, why: str) -> None:
        self.problems.append(f"move_tests: tests/{self.old}.py: {why}")

    def settle(self) -> None:
        """Refused (SystemExit) with every problem found so far, a line each."""
        if self.problems:
            raise SystemExit("\n".join(dict.fromkeys(self.problems)))

    def refuse(self, why: str) -> NoReturn:
        self.problem(why)
        raise SystemExit("\n".join(dict.fromkeys(self.problems)))

    def _where(self, i: int) -> str:
        s = self.lay.stmts[i]
        names = sorted(self.ES.module_binds(s))
        return f"line {s.lineno}" + (f" ({', '.join(names)})" if names else "")

    def ruff_bound(self, new: str) -> Dict[str, int]:
        """The most ruff may find in NEW, by code: OLD's hits (self.hits) on
        the lines it took, and an import kept for what it runs, unused there."""
        out = counted(self.hits, taken_lines(self.text, self.keeps[new]))
        for code, n in self.allow.get(new, {}).items():
            out[code] = out.get(code, 0) + n
        return out

    def shared_names(self) -> Dict[str, List[str]]:
        """The module-level names two new files or more hold a copy of (a
        def's, a class's, a value's; not an import's, whose object they
        share) -> those files: the state the verifier's outcome run watches."""
        out: Dict[str, List[str]] = {}
        for i, s in enumerate(self.lay.stmts):
            holders = [new for new in self.news if i in self.keeps[new]]
            if len(holders) > 1 and not isinstance(s, (ast.Import, ast.ImportFrom)):
                for n in sorted(self.ES.module_binds(s)):
                    out[n] = list(dict.fromkeys(out.get(n, []) + holders))
        return out

    def _own_imports(self) -> None:
        """OLD importing itself by name (`from OLD import X`, in a test):
        its new files are other modules. Refused."""
        for x in ast.walk(self.lay.tree):
            mods = [a.name for a in x.names] if isinstance(x, ast.Import) else \
                [x.module or ""] if isinstance(x, ast.ImportFrom) else []
            if any(m == self.old or m.startswith(f"{self.old}.") for m in mods):
                self.problem(f"line {x.lineno} imports its own module by name ({self.old}), "
                             f"which a split leaves no more: name the class itself there by "
                             f"hand first")

    def _links(self) -> None:
        """OLD reading what a split changes: a class's links to the others
        (LINKS: its subclasses, its bases, through which a copied base's
        state is changed) or the module's own file (OWN_FILE), by name or as
        `getattr`'s or a subscript's string. Refused."""
        seen: Set[Tuple[int, str]] = set()
        for x in ast.walk(self.lay.tree):
            p = self.parents.get(x)
            if isinstance(x, ast.Constant) and isinstance(x.value, str):
                if not (isinstance(p, ast.Subscript) and p.slice is x or isinstance(p, ast.Call)
                        and _call_name(p) in ("getattr", "hasattr", "setattr", "delattr")):
                    continue
                name = x.value
            elif isinstance(x, ast.Attribute) and x.attr in LINKS:
                name = x.attr
                if name == "mro" and not (isinstance(p, ast.Call) and p.func is x):
                    continue
            elif isinstance(x, ast.Name) and x.id in OWN_FILE:
                name = x.id
            else:
                continue
            if (x.lineno, name) in seen or name not in LINKS and name not in OWN_FILE:
                continue
            seen.add((x.lineno, name))
            if name in OWN_FILE:
                self.problem(f"line {x.lineno} reads {name}, where the module is, which a split "
                             f"moves (a path from it changes with the new file's folder): name "
                             f"the folder through tests/_paths.py (REPO, TESTS, FIXTURES: the "
                             f"form a split keeps) by hand first")
            else:
                self.problem(f"line {x.lineno} reads {name} ({LINKS[name]}), which a split "
                             f"changes: by hand first")

    def _changes_passed(self, d: ast.stmt) -> bool:
        """Def or class D of OLD can change an object passed to it: a store
        into a parameter or a changing method called on one (a method's
        own `self` apart); a class's object may keep it."""
        if isinstance(d, ast.ClassDef):
            return True
        params = set(self.ES._params(d))
        if isinstance(self.parents.get(d), ast.ClassDef) and d.args.args:
            params.discard(d.args.args[0].arg)
        return bool(self.ES.changes(ast.walk(d), params))

    def _reads_only(self, call: Optional[ast.AST]) -> bool:
        """CALL only reads what it is passed: a def of OLD's that changes no
        argument (_changes_passed), else a call by a name READING_CALLS or
        READ_ONLY has, or a test's assert (TEST_METHODS)."""
        name = _call_name(call)
        if not name:
            return False
        own = self.defs.get(name)
        if own:
            return not any(self._changes_passed(d) for d in own)
        return name in READING_CALLS or name in READ_ONLY or name.startswith(TEST_METHODS)

    def _passed(self, call: ast.Call, deep: bool, at: str) -> Optional[str]:
        """How CALL can change or keep an object passed to it, or None: a
        def of OLD's that changes an argument (_changes_passed), a call that
        changes what it is given (CHANGING_CALLS), a method that keeps it
        in another object (KEEPING; an EXTENDING one its items, which DEEP
        says can change). A callee the text does not show (an import's
        def, another method) the verifier's outcome run watches: the
        copies' state after each class (state_problems)."""
        name = _call_name(call)
        if self._reads_only(call):
            return None
        if name in self.defs:
            return f"passed to {name}() at {at}, which changes an argument"
        if name in CHANGING_CALLS:
            return f"passed to {name}() at {at}, which changes it"
        f = call.func
        if isinstance(f, ast.Attribute) and name in KEEPING \
                and ast.unparse(f.value) not in ("os.environ", "environ") \
                and (deep or name not in EXTENDING):
            return f"kept by .{name}() at {at}"
        return None

    def _use(self, x: ast.AST, deep: bool) -> Optional[str]:
        """How code can change the object read at X through that read, or
        None when it only reads it: a method but a READ_ONLY one called on
        it (`changed at line N`), a store into it, an item of it used so
        (DEEP: its items can change), a second name for it (assigned,
        returned, yielded; kept in a container used so), passed to a call
        that changes or keeps it (_passed), a changing method handed out
        (MUTATORS), called, entered by `with`. Iterated, compared, tested, formatted, unpacked or copied
        (`x + y`, `x[1:]` of constants) it is only read."""
        p = self.parents.get(x)
        at = f"line {getattr(x, 'lineno', '?')}"
        if isinstance(p, ast.Attribute):
            if not isinstance(p.ctx, ast.Load):
                return f"stored into at {at}"
            q = self.parents.get(p)
            if isinstance(q, ast.Call) and q.func is p:
                return None if p.attr in READ_ONLY else f"changed at {at} (.{p.attr}())"
            if p.attr in self.ES.MUTATORS:
                return f"its .{p.attr}() handed out at {at}"
            return None if p.attr in READ_ONLY else self._use(p, True)
        if isinstance(p, ast.Subscript):
            if p.value is not x:
                return None                                  # a key
            if not isinstance(p.ctx, ast.Load):
                return f"stored into at {at}"
            return self._use(p, True) if deep else None
        if isinstance(p, ast.Call):
            return f"called at {at}" if p.func is x else self._passed(p, deep, at)
        if isinstance(p, ast.keyword):
            call = self.parents.get(p)
            return None if p.arg is None or not isinstance(call, ast.Call) else \
                self._passed(call, deep, at)
        if isinstance(p, ast.Starred) or isinstance(p, ast.Dict) and (
                x not in p.values or p.keys[p.values.index(x)] is None):
            return None                                      # unpacked (`*x`, `**x`); a key
        if isinstance(p, (ast.List, ast.Tuple, ast.Set, ast.Dict)) or \
                isinstance(p, (ast.BoolOp, ast.IfExp)) and getattr(p, "test", None) is not x:
            return self._use(p, deep)                        # its value may be X
        if isinstance(p, (ast.Compare, ast.BinOp, ast.UnaryOp, ast.FormattedValue, ast.Expr,
                          ast.If, ast.While, ast.Assert, ast.IfExp, ast.Slice, ast.AugAssign,
                          ast.comprehension, ast.For, ast.AsyncFor, ast.Match)):
            return None
        if isinstance(p, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            return f"aliased at {at}"
        if isinstance(p, (ast.Return, ast.Yield, ast.YieldFrom, ast.Lambda)):
            return f"handed out at {at}"
        if isinstance(p, ast.withitem):
            return f"entered at {at}"
        return f"used at {at} ({type(p).__name__})"

    def _attribute_change(self, attr: str, deep: bool = True) -> Optional[str]:
        """How OLD can change an attribute named ATTR of anything: an
        augmented assignment to it or through it, a `del` of it, each read
        of it as _use sees it (DEEP: its items can change); `changed at line
        N` and the like, or None. A store to the attribute itself
        (`self.x = []`) binds the instance's own."""
        for x in ast.walk(self.lay.tree):
            if isinstance(x, ast.AugAssign) and _through(x.target, attr):
                return f"changed at line {x.lineno}"
            if isinstance(x, ast.Attribute) and x.attr == attr:
                if isinstance(x.ctx, ast.Del):
                    return f"deleted at line {x.lineno}"
                how = self._use(x, deep) if isinstance(x.ctx, ast.Load) else None
                if how:
                    return how
        return None

    def _function_of(self, x: ast.AST) -> Tuple[Optional[ast.AST], bool]:
        """The function whose body holds X (None at import: module level, a
        class body, a decorator or default), and whether a module hook's
        body holds it (HOOKS: it sets up its own file's copy)."""
        inner, child, p = None, x, self.parents.get(x)
        while p is not None:
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)) and child in p.body or \
                    isinstance(p, ast.Lambda) and child is p.body:
                inner = inner or p
                if self.parents.get(p) is self.lay.tree and getattr(p, "name", "") in HOOKS:
                    return inner, True
            child, p = p, self.parents.get(p)
        return inner, False

    def _name_use(self, name: str, deep: bool) -> Optional[str]:
        """How code that runs after import can change module-level NAME's
        object: _use of each read of it in a function (not a hook's, nor in
        one that binds NAME itself)."""
        for x in ast.walk(self.lay.tree):
            if not (isinstance(x, ast.Name) and x.id == name and isinstance(x.ctx, ast.Load)):
                continue
            fn, hook = self._function_of(x)
            if fn is None or hook or _binds(fn, name):
                continue
            how = self._use(x, deep)
            if how:
                return how
        return None

    def _mutable_names(self) -> Dict[str, bool]:
        """OLD's module-level values a method can change (_value_state, or
        a second name for one) -> whether their items can change too."""
        out: Dict[str, bool] = {}
        for s in self.lay.stmts:
            if isinstance(s, (ast.Assign, ast.AnnAssign)) and s.value is not None \
                    and _value_state(s.value, out):
                deep = out.get(s.value.id, True) if isinstance(s.value, ast.Name) else \
                    _deep_items(s.value)
                for n in self.ES.module_binds(s):
                    out[n] = out.get(n, False) or deep
        return out

    def _def_state(self, s: ast.stmt) -> List[Tuple[ast.stmt, str]]:
        """The defs of S its import makes (S itself, a class's methods)
        that hold state of their own: a mutable default (made once, at
        import) or a cache decorator (CACHES)."""
        todo, out = [s], []
        while todo:
            n = todo.pop(0)
            if isinstance(n, ast.ClassDef):
                todo += [m for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                             ast.ClassDef))]
                continue
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in [*n.args.defaults, *[k for k in n.args.kw_defaults if k is not None]]:
                why = _value_state(d)
                if why:
                    out.append((n, f"a mutable default ({why}, line {d.lineno})"))
                    break
            for d in n.decorator_list:
                if _func_name(d.func if isinstance(d, ast.Call) else d) in CACHES:
                    out.append((n, f"a cache ({ast.unparse(d)}, line {d.lineno})"))
                    break
        return out

    def _helper_effect(self, module: str, seen: Set[Path]) -> Optional[str]:
        """What importing MODULE, one of tests/ (helper_files), does at import
        beyond imports, definitions, assignments of new names and changes of
        its own values (a call, a store into or a change of what it
        imports; through the tests/ modules it imports too), or None.
        RUNNER_SETUP runs first in every file anyway."""
        if module.split(".")[0] in RUNNER_SETUP:
            return None
        ES, tests = self.ES, self.repo / "tests"
        for path in helper_files(tests, module):
            if path in seen:
                continue
            seen.add(path)
            try:
                tree = ast.parse(read_exact(path))
            except (SyntaxError, UnicodeDecodeError, ValueError):
                return f"{path.relative_to(tests).as_posix()} does not parse"
            pkg = ".".join(path.relative_to(tests).with_suffix("").parts)
            pkg = pkg[:-len(".__init__")] if pkg.endswith(".__init__") else pkg.rpartition(".")[0]
            imported = {n for s in tree.body if isinstance(s, (ast.Import, ast.ImportFrom))
                        for n in ES.module_binds(s)}
            own = {n for s in tree.body if not isinstance(s, (ast.Import, ast.ImportFrom))
                   for n in ES.module_binds(s)}
            where = f"{path.relative_to(tests).as_posix()}:"
            for s in tree.body:
                if ES._is_main_guard(s):
                    continue
                if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) and isinstance(
                        s.value.func, ast.Attribute) and ES._root(s.value.func.value) in own:
                    continue                     # a change of its own value (`ENV.pop(k)`)
                if isinstance(s, (ast.Import, ast.ImportFrom)):
                    if isinstance(s, ast.Import):
                        subs = [a.name for a in s.names]
                    else:
                        head = ".".join(pkg.split(".")[:len(pkg.split(".")) - s.level + 1]) \
                            if s.level else ""
                        mod = ".".join(x for x in (head, s.module or "") if x)
                        subs = [mod] + [f"{mod}.{a.name}" for a in s.names]
                    for m in subs:
                        why = self._helper_effect(m, seen)
                        if why:
                            return why
                    continue
                nodes = ES.import_time_nodes(s)
                if ES._runs_code(s):
                    return f"{where}{s.lineno} runs code"
                if isinstance(s, (ast.Assign, ast.AnnAssign)) and any(
                        not isinstance(tg, (ast.Name, ast.Tuple, ast.List))
                        and ES._root(tg) not in own
                        for tg in (s.targets if isinstance(s, ast.Assign) else [s.target])):
                    return f"{where}{s.lineno} stores into something"
                hits = [x for x in ES.changes(nodes, imported) if not x.startswith("global")]
                if hits:
                    return f"{where}{s.lineno} changes {hits[0].split()[0]}"
        return None

    def _test_cases(self) -> Set[str]:
        """OLD's module-level classes that are TestCases (a base named so,
        or one of these)."""
        out: Set[str] = set()
        more = True
        while more:
            more = False
            for name, c in self.classes.items():
                if name not in out and any("TestCase" in ast.unparse(b) or self.ES._root(b) in out
                                           for b in c.bases):
                    out.add(name)
                    more = True
        return out

    def _makes_test_case(self, call: ast.Call, cases: Set[str], seen: Tuple[str, ...] = ()) -> bool:
        """CALL makes a test class: `type(NAME, BASES, NS)` with a TestCase
        among BASES (CASES: OLD's), or a def of OLD whose body makes one
        (a class built on one, or with test methods; through the defs it
        calls too)."""
        f = call.func
        if isinstance(f, ast.Name) and f.id == "type" and len(call.args) == 3:
            return any("TestCase" in ast.unparse(b) or self.ES._root(b) in cases
                       for b in ast.walk(call.args[1]) if isinstance(b, (ast.Name, ast.Attribute)))
        if not isinstance(f, ast.Name) or f.id in seen:
            return False
        for j in self.bound.get(f.id, []):
            s = self.lay.stmts[j]
            if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for x in ast.walk(s):
                if isinstance(x, ast.ClassDef) and (
                        any("TestCase" in ast.unparse(b) or self.ES._root(b) in cases
                            for b in x.bases)
                        or any(isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                               and m.name.startswith("test") for m in x.body)):
                    return True
                if isinstance(x, ast.Call) and self._makes_test_case(x, cases, seen + (f.id,)):
                    return True
        return False

    def _placement(self) -> None:
        ES, stmts = self.ES, self.lay.stmts
        self.classes = {s.name: s for s in stmts if isinstance(s, ast.ClassDef)}
        runs = {name: ES.Plan._runs_tests(c, self.classes) for name, c in self.classes.items()}
        for i, s in enumerate(stmts):
            if isinstance(s, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for x in ES.import_time_nodes(s):
                if isinstance(x, ast.ClassDef) and any(
                        isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and m.name.startswith("test") for m in x.body):
                    self.problem(f"line {x.lineno}: the test class {x.name} is bound in a "
                                 f"{type(s).__name__.lower()} statement: put it at module level "
                                 f"first")
        self.placed: Dict[str, str] = {}
        for cls, new in self.places:
            if cls not in self.classes:
                self.problem(f"no module-level class {cls}")
            elif len(self.bound.get(cls, [])) > 1:
                self.problem(f"{cls} is bound at module level more than once")
            elif not runs[cls]:
                self.problem(f"{cls} runs no tests here (a mixin or a fixture base): it goes "
                             f"with the classes built on it, not by a line of its own")
            else:
                self.placed[cls] = new
        cases = self._test_cases()
        for s in stmts:
            if not isinstance(s, (ast.Assign, ast.AnnAssign)) or s.value is None:
                continue
            names = [x.id for tg in (s.targets if isinstance(s, ast.Assign) else [s.target])
                     for x in ast.walk(tg) if isinstance(x, ast.Name)]
            v = s.value
            if isinstance(v, ast.Name) and runs.get(v.id):
                self.problem(f"line {s.lineno}: {', '.join(names)} = {v.id}, a second name for "
                             f"a test class (unittest runs its tests under each): drop it by "
                             f"hand first")
                continue
            test = r"Test([A-Z0-9_]|$)"
            made = isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "type" \
                and not (v.args and isinstance(v.args[0], ast.Constant)
                         and not re.match(test, str(v.args[0].value)))
            maker = any(self._makes_test_case(c, cases) for c in ES.import_time_nodes(s)
                        if isinstance(c, ast.Call))
            if made or maker or (isinstance(v, ast.Call) and any(re.match(test, n) for n in names)):
                self.problem(f"line {s.lineno}: {', '.join(names) or 'a name'} is made by a "
                             f"call ({ast.unparse(v)[:40]}), a test class no line can place: "
                             f"write it as a class of its own by hand first")
        missing = sorted(c for c, why in runs.items() if why and c not in self.placed
                         and c not in {c2 for c2, _n in self.places})
        if missing:
            self.problem(f"{', '.join(missing)} run(s) tests and no line places it: a split "
                         f"places every test class")
        self.news = list(dict.fromkeys(self.placed.values()))
        if len(self.news) == 1:
            self.problem(f"every class to {self.news[0]}: move it whole (`{self.old} "
                         f"{self.news[0]}`)")
        self.index = {cls: self.bound[cls][0] for cls in self.placed}

    def _effect(self, i: int) -> Optional[str]:
        """Why statement I runs code at import that the names it binds do
        not carry (so every new file runs it), or None."""
        ES, s = self.ES, self.lay.stmts[i]
        if isinstance(s, (ast.Import, ast.ImportFrom)):
            if isinstance(s, ast.ImportFrom) and s.module == "__future__":
                return "a __future__ import"
            if not ES.module_binds(s) & set().union(*self.reads):
                return "an import no statement reads"
            mods = [a.name for a in s.names] if isinstance(s, ast.Import) else \
                [] if s.level else [s.module or ""] + [f"{s.module}.{a.name}" for a in s.names]
            for m in mods:
                why = self._helper_effect(m, set())
                if why:
                    self.effects[i] = why
                    return f"an import of tests/ that runs code at import: {why}"
            return None
        if ES.module_binds(s) & set(HOOKS):
            return "a module hook"
        if ES._runs_code(s):
            return f"runs code at import ({type(s).__name__})"
        nodes = ES.import_time_nodes(s)
        if isinstance(s, (ast.Assign, ast.AnnAssign)):
            targets = s.targets if isinstance(s, ast.Assign) else [s.target]
            if any(not isinstance(t, (ast.Name, ast.Tuple, ast.List)) for t in targets):
                return "a store at import"
            if ES.module_binds(s).isdisjoint(set().union(*self.reads)) and any(
                    isinstance(x, ast.Call) for x in nodes):
                return "a call no statement reads the result of"
        touched = [x for x in ES.changes(nodes, set(self.bound)) if not x.startswith("global")]
        touched += self._calls_changing(nodes, self.data)
        for f in sorted(ES._called_at_import(nodes) & set(self.bound)):
            how = self._changes_of(f)
            if how:
                touched.append(f"{f}(): {how}")
        defined = isinstance(s, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        if touched:
            if defined:
                self.problem(f"{self._where(i)} changes module-level state when it is defined "
                             f"({touched[0]}): by hand first")
                return None
            return f"changes at import ({touched[0]})"
        if defined and s.decorator_list and s.name not in self.placed and s.name not in set().union(
                *[r for j, r in enumerate(self.reads) if j != i]):
            return DECORATED
        return None

    def _calls_changing(self, nodes: Iterable[ast.AST], names: Set[str]) -> List[str]:
        """The calls in NODES that can change the objects of NAMES (module-level
        values): a method not READ_ONLY called on one, one passed to a def or
        class of OLD that changes an argument (extract_support)."""
        ES, out = self.ES, []
        for x in nodes:
            if not isinstance(x, ast.Call):
                continue
            f = x.func
            if isinstance(f, ast.Attribute) and f.attr not in READ_ONLY \
                    and ES._root(f.value) in names:
                out.append(f"{ES._root(f.value)}.{f.attr}()")
            elif isinstance(f, ast.Name) and f.id in self.bound and any(
                    ES._changes_an_argument(self.lay.stmts[j]) for j in self.bound[f.id]):
                passed = [ES._root(a) for a in [*x.args, *[k.value for k in x.keywords]]]
                hit = next((r for r in passed if r in names), None)
                if hit:
                    out.append(f"{hit} passed to {f.id}(), which changes an argument")
        return out

    def _changes_of(self, name: str, seen: Tuple[str, ...] = ()) -> Optional[str]:
        """What calling NAME, a def or class of OLD, changes of module-level
        objects (through the defs it calls too); None for nothing."""
        ES = self.ES
        for i in self.bound.get(name, []):
            s = self.lay.stmts[i]
            if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            # What runs when it is called: its body (a class's: its methods'),
            # not a def it makes, whose body runs when that one is called.
            bodies = [s] if not isinstance(s, ast.ClassDef) else [
                m for m in s.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            nodes = [x for b in bodies for st in b.body for x in ES.import_time_nodes(st)]
            found = [x for x in ES.changes(nodes, self.reads[i]) if not x.startswith("global")]
            found += [f"global {', '.join(g.names)}" for g in nodes if isinstance(g, ast.Global)]
            if found:
                return found[0]
            for r in sorted(self.reads[i] - {name} - set(seen)):
                how = self._changes_of(r, seen + (name,))
                if how:
                    return f"{how}, through {r}"
        return None

    def _everywhere(self) -> Dict[int, str]:
        """The statements every new file holds, and why (a class only when
        decorated and read by none: one that changes state when defined is
        refused)."""
        return {i: why for i in range(len(self.lay.stmts)) for why in [self._effect(i)] if why}

    def _closure(self, new: str) -> Tuple[Set[int], Set[str]]:
        """The statements NEW holds and the names they read."""
        want = {self.index[c] for c, n in self.placed.items() if n == new} | set(self.everywhere)
        todo, names = list(want), set()
        while todo:
            i = todo.pop()
            for r in self.reads[i]:
                names.add(r)
                for j in self.bound[r]:
                    if j not in want:
                        want.add(j)
                        todo.append(j)
        return want, names

    def _crossings(self, wants: Dict[str, Tuple[Set[int], Set[str]]]) -> None:
        """A new file that needs a class another one takes: refused."""
        stmts = self.lay.stmts
        for cls, new in self.placed.items():
            for b in self.classes[cls].bases:
                base = ast.unparse(b)
                if self.placed.get(base, new) != new:
                    self.problem(f"{cls}'s base {base} runs tests and goes to "
                                 f"{self.placed[base]}, {cls} to {new}: a class goes where its "
                                 f"base goes")
        for new, (want, _names) in wants.items():
            for cls, there in self.placed.items():
                if there == new or self.index[cls] not in want:
                    continue
                by = [i for i in want if cls in self.reads[i] and i != self.index[cls]]
                why = (f"{self._where(by[0])}" + (f", which every new file holds "
                                                  f"({self.everywhere[by[0]]})"
                                                  if by[0] in self.everywhere else "")
                       if by else "it")
                self.problem(f"{new} needs {cls} ({why} reads it), which goes to {there}: place "
                             f"them together, or change that by hand first")
        imports: Dict[str, list] = {}
        for s in stmts:
            if isinstance(s, (ast.Import, ast.ImportFrom)):
                for a in s.names:
                    imports.setdefault((a.asname or a.name).split(".")[0], []).append((s, a))
        names = self.ES.sys_names(imports, self.lay.tree)
        for i in sorted(set().union(*[w for w, _n in wants.values()])):
            how = self.ES.own_namespace(stmts[i], self.old, names)
            if how:
                self.problem(f"{self._where(i)} reads its own module's names by name ({how[0]}): "
                             f"a new file holds only what it reads by name")

    def _shared_state(self, wants: Dict[str, Tuple[Set[int], Set[str]]]) -> None:
        """State two new files would each hold a copy of, which a test can
        change after import: a copied class's (class_state: a mutable class
        attribute that any code can change, _attribute_change), a copied
        def's (a mutable default, a cache: _def_state), a copied mutable
        value passed to a call, given a second name or changed in a function
        (_name_use), a shared name a function changes (its store into it, a
        method that changes it, `global`; not a module hook, which sets up
        its own file's copy). Each refused."""
        ES, stmts = self.ES, self.lay.stmts
        count: Dict[int, int] = {}
        for want, _names in wants.values():
            for i in want:
                count[i] = count.get(i, 0) + 1
        shared = {n for i, c in count.items() if c > 1
                  and not isinstance(stmts[i], (ast.Import, ast.ImportFrom))
                  for n in ES.module_binds(stmts[i])}
        mutable = self._mutable_names()
        named: Set[str] = set()
        for i in sorted(i for i, c in count.items() if c > 1):
            s = stmts[i]
            if isinstance(s, (ast.Import, ast.ImportFrom)):
                continue
            why = class_state(s, self._attribute_change, mutable) \
                if isinstance(s, ast.ClassDef) else None
            if why:
                self.problem(f"{s.name} (line {s.lineno}) goes into {count[i]} new files and has "
                             f"{why}: each copy would hold its own (by hand first)")
            for f, why in self._def_state(s):
                self.problem(f"{f.name} (line {f.lineno}) goes into {count[i]} new files and has "
                             f"{why}: each copy would hold its own (by hand first)")
            for n in sorted(ES.module_binds(s) & set(mutable) - named):
                named.add(n)
                how = self._name_use(n, mutable[n])
                if how:
                    self.problem(f"{n} (line {s.lineno}) goes into {count[i]} new files and is "
                                 f"{how}: each file would change its own copy (by hand first)")
        for i in sorted(i for i, c in count.items() if c > 1):
            s = stmts[i]
            if isinstance(s, (ast.Assign, ast.AnnAssign)) and s.value is not None:
                made = next((c for c in ast.walk(s.value) if isinstance(c, ast.Call)
                             and _call_name(c) in TEMP_CALLS), None)
                if made is not None:
                    self.problem(f"{', '.join(sorted(ES.module_binds(s)))} (line {s.lineno}) goes "
                                 f"into {count[i]} new files and holds "
                                 f"{TEMP_CALLS[_call_name(made)]} ({ast.unparse(made.func)}(), "
                                 f"line {made.lineno}): each file would make its own (by hand "
                                 f"first)")
        made_globals: Dict[str, int] = {}
        for g in ast.walk(self.lay.tree):
            if isinstance(g, ast.Global):
                for n in g.names:
                    if n not in self.bound:
                        made_globals.setdefault(n, g.lineno)
        for n, line in sorted(made_globals.items()):
            files = [new for new, (want, _names) in wants.items() if any(
                n in global_names(stmts[i]) or any(isinstance(x, ast.Name) and x.id == n
                                                    for x in ast.walk(stmts[i])) for i in want)]
            if len(files) > 1:
                self.problem(f"{n}, a module-level name a `global` statement makes when it runs "
                             f"(line {line}), is set or read in {len(files)} new files: each file "
                             f"would hold its own (by hand first)")
        for i in sorted(count):
            for f in ast.walk(stmts[i]):
                if not isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) or f is stmts[i] \
                        and f.name in HOOKS:     # a hook sets its own file's state up
                    continue
                free = ES.module_reads(f) & shared
                inner = [x for st in f.body for x in ast.walk(st)]
                hits = [x for x in ES.changes(inner, free) if not x.startswith("global")]
                hits += [f"{n} (global)" for g in inner if isinstance(g, ast.Global)
                         for n in g.names if n in shared]
                hits += self._calls_changing(inner, free & self.data)
                if hits:
                    self.problem(f"{f.name} (line {f.lineno}) changes {hits[0].split()[0]} after "
                                 f"import, which more than one new file reaches: each would "
                                 f"change its own copy (by hand first)")

    def leavers(self) -> Dict[str, List[str]]:
        """The stages that pick OLD by its text (stage_patterns) -> the new
        files whose text they no longer pick (their tests run in the suite,
        not in that stage)."""
        out = {}
        for stage, pattern in stage_patterns(self.repo).items():
            if stage_selects(pattern, self.text):
                gone = [new for new, text in self.texts.items() if not stage_selects(pattern, text)]
                if gone:
                    out[stage] = gone
        return out

    def env_branches(self) -> List[Tuple[int, str]]:
        """Where OLD's code branches on the machine (env_read: `if
        shutil.which(...)`, a skipIf, os.environ) or tries an import (`try:
        import openpyxl`): (line, what). Where the machine differs, a test
        can run otherwise than in the verifier's runs, which see one side."""
        out: List[Tuple[int, str]] = []
        for x in ast.walk(self.lay.tree):
            if isinstance(x, (ast.If, ast.IfExp, ast.While)):
                what = env_read(x.test)
            elif isinstance(x, ast.Call) and _call_name(x) in ("skipIf", "skipUnless"):
                what = env_read(x.args[0] if x.args else None)
            elif isinstance(x, ast.Try) and any(
                    isinstance(y, (ast.Import, ast.ImportFrom)) for b in x.body for y in ast.walk(b)):
                what = "an import tried"
            else:
                continue
            if what:
                out.append((x.lineno, what))
        return sorted(set(out))

    def describe(self) -> List[str]:
        """The plan, a line each: the new files, what each copies, what none
        takes, what every file holds for what it runs at import (an import of
        tests/, a decorated definition none reads), a file leaving a stage,
        the branches on the machine (env_branches)."""
        out = []
        for new in self.news:
            classes = [c for c, n in self.placed.items() if n == new]
            out.append(f"tests/{self.old}.py :: {', '.join(classes)} -> tests/{new} "
                       f"({dotted(new)})")
        for i in self.copied:
            out.append(f"  copied into {sum(i in k for k in self.keeps.values())} files: "
                       f"{self._where(i)}")
        for i, why in sorted(self.effects.items()):
            out.append(f"  in every file for what it runs at import: line "
                       f"{self.lay.stmts[i].lineno} ({why})")
        for i, why in sorted(self.everywhere.items()):
            if why == DECORATED:
                out.append(f"  in every file for what it runs at import: {self._where(i)} ({why})")
        for i in self.left:
            out.append(f"  in no new file: {self._where(i)}")
        for stage, gone in self.leavers().items():
            out.append(f"  leaves the {stage} stage (its pattern is not in it; its tests run in "
                       f"the suite): {', '.join(f'tests/{n}' for n in gone)}")
        env = self.env_branches()
        if env:
            out.append("  branches on the machine (the verifier's runs see one side): "
                       + ", ".join(f"line {n} ({what})" for n, what in env[:12])
                       + (f" and {len(env) - 12} more" if len(env) > 12 else ""))
        return out


def grouped(moves: List[Tuple[str, str]]) -> Tuple[List[Tuple[str, str]],
                                                   Dict[str, List[Tuple[str, str]]]]:
    """MOVES as the whole moves and the splits (module -> [(Class, NEW)])."""
    whole: List[Tuple[str, str]] = []
    splits: Dict[str, List[Tuple[str, str]]] = {}
    for old, new in moves:
        if SPLIT in old:
            mod, cls = old.split(SPLIT, 1)
            splits.setdefault(mod, []).append((cls, new))
        else:
            whole.append((old, new))
    return whole, splits


def release_words(repo: Path) -> Optional[str]:
    """The pattern by which ci.sh's release-edits stage selects the test
    modules (`grep -rlE --include='test_*.py' 'WORDS' tests`), or None."""
    ci = repo / "scripts" / "ci.sh"
    m = re.search(r"grep -rlE --include='test_\*\.py' '([^']+)' tests",
                  _read(ci) or "") if ci.is_file() else None
    return m.group(1) if m else None


# The run file whose tests compare module stems with what a stage's grep
# picks (`for mod in (...): self.assertIn(mod, picked)`).
STAGE_TEST = "tests/test_fix_1029_devtools.py"


def comment_columns(f: str, text: str) -> Dict[int, int]:
    """Where each line (1-based) of file F's TEXT starts its comment: a
    .py file's by its tokens, another's (shell, YAML) at a `#` that starts
    a word outside quotes."""
    out: Dict[int, int] = {}
    if f.endswith(".py"):
        try:
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type == tokenize.COMMENT:
                    out[tok.start[0]] = tok.start[1]
        except (tokenize.TokenError, SyntaxError):
            pass
        return out
    for n, ln in enumerate(split_lines(text), 1):
        quote, i = None, 0
        while i < len(ln):
            c = ln[i]
            if quote:
                if c == "\\" and quote == '"':
                    i += 1
                elif c == quote:
                    quote = None
            elif c in "'\"":
                quote = c
            elif c == "#" and (i == 0 or ln[i - 1] in " \t"):
                out[n] = i
                break
            i += 1
    return out


def run_file_problems(repo: Path, old: str, placed: Iterable[str]) -> List[str]:
    """Where a script runs OLD, split, by a name a split cannot follow: the
    module bare or by path (a class it places, `OLD.Class` with PLACED's
    Class, follows it), each with what to do by hand. A mention in a
    comment only runs nothing: the mover lists it after the split, with
    the other mentions to settle by hand. A stage that picks modules by
    their text picks each new file by its own (SplitPlan.leavers)."""
    tests = repo / "tests"
    run = run_files(tests)
    if f"tests/{old}.py" in (*run, layout_path(tests).relative_to(repo).as_posix()):
        return ["a run file or the layout test moves whole"]
    out = []
    for f, was in zip(run, RUN_FILES):
        text = (_read(repo / f) if (repo / f).is_file() else None) or ""
        cols = comment_columns(f, text)
        for n, ln in enumerate(split_lines(text), 1):
            if ln.strip().startswith(FORMERLY):
                continue
            code = cols.get(n, len(ln))           # a comment's mention runs nothing
            if any(m.start() < code for m in path_pattern(old).finditer(ln)):
                out.append(f"{f}:{n} names its path: name the classes' modules there by hand "
                           f"first")
                continue
            for m in name_pattern(old).finditer(ln):
                rest = re.match(r"\.(\w+)", ln[m.end():])
                if m.start() >= code or rest and rest.group(1) in set(placed):
                    continue
                out.append(f"{f}:{n} names it by its stem, as a stage's grep picks a module: "
                           f"drop the stem there in a commit before the split, and name the new "
                           f"modules' stems the stage picks (the dry run lists the ones that "
                           f"leave it) in one after it" if was == STAGE_TEST else
                           f"{f}:{n} names it as a module, which a split leaves no more: name a "
                           f"class of it there (`{old}.Class`, which follows its class) or "
                           f"another module, by hand first")
                break
    return out


def _check(repo: Path, moves: List[Tuple[str, str]]
           ) -> Tuple[Dict[str, str], Dict[str, "SplitPlan"]]:
    """Every move possible, before anything changes: the module there and
    tracked, the target free and its name no other module's (TJ-3724),
    each folder on the way a package or new, no
    new path one git ignores (.gitignore names folders like inputs/ and
    tmp*/ at any depth) nor below a folder ruff skips (UNLINTED: `dist`,
    `venv`); each module's text with its `Formerly:` line, each split's
    plan (SplitPlan) and run files (run_file_problems), and the ruff hits
    of its module. Refused (SystemExit) with every problem, all modules',
    a line each; an error here leaves the tree as it was."""
    tests = repo / "tests"
    whole, splits = grouped(moves)
    olds = [o for o, _n in whole] + list(splits)
    news = [n for _o, n in whole] + list(dict.fromkeys(n for p in splits.values() for _c, n in p))
    # Below tests/ and the packages' tests/ (both on sys.path in the
    # plugin's run; TJ-3724, TJ-3750): a moving module's own name is free
    # once it leaves, unless another module has it too, in tests/ or the
    # plugin's tests (TJ-3753, TJ-3770: the moving modules leave by path).
    moving = {tests / f"{o}.py" for o in olds}
    taken = {q.stem for q in tests.rglob("test_*.py") if q not in moving} | \
        {q.stem for q in repo.glob("packages/*/tests/**/test_*.py")}
    tracked = set(subprocess.run(
        ["git", "ls-files", "-z", "--", *[f"tests/{o}.py" for o in olds]],
        cwd=repo, capture_output=True, check=True).stdout.decode().split("\0"))
    texts: Dict[str, str] = {}
    new_paths: List[str] = []
    areas = feature_areas(repo)
    problems: List[str] = []
    absent: Set[str] = set()
    for old in olds:
        src = tests / f"{old}.py"
        if not src.is_file():
            problems.append(f"move_tests: tests/{old}.py does not exist")
            absent.add(old)
        elif f"tests/{old}.py" not in tracked:
            problems.append(f"move_tests: tests/{old}.py is not tracked by git")
            absent.add(old)
    mine: Dict[str, str] = {}
    for new in news:
        if (tests / new).exists():
            problems.append(f"move_tests: tests/{new} exists")
        if Path(new).stem in mine:
            problems.append(f"move_tests: tests/{new} and tests/{mine[Path(new).stem]}: the "
                            f"placement names two new files {Path(new).stem}: a module's name "
                            f"is its own")
        mine.setdefault(Path(new).stem, new)
        if Path(new).stem in taken:
            problems.append(f"move_tests: tests/{new}: a module below tests/ is named "
                            f"{Path(new).stem} already: name the subject otherwise")
        parts = Path(new).parent.parts
        # A feature module goes in its map section's area, one folder deep.
        if parts[0] == "features" and (len(parts) != 2 or (areas is not None
                                                          and parts[1] not in areas)):
            problems.append(f"move_tests: tests/{new}: a feature module goes in "
                            f"tests/features/<area>/, an area of FEATURE_AREAS "
                            f"({LAYOUT.as_posix()}: one per section of the architecture map)")
        skipped = sorted(UNLINTED & set(parts))
        if skipped:
            problems.append(f"move_tests: tests/{new}: ruff skips a folder named {skipped[0]} "
                            f"(its default exclude): name the folder otherwise "
                            f"(FAMILY_FOLDERS)")
        for i in range(1, len(parts) + 1):
            d = tests.joinpath(*parts[:i])
            if d.exists() and not (d / "__init__.py").is_file():
                problems.append(f"move_tests: tests/{'/'.join(parts[:i])} exists and is no "
                                f"package (no __init__.py)")
                break
            if not d.exists():
                new_paths.append(f"tests/{'/'.join(parts[:i])}/__init__.py")
        new_paths.append(f"tests/{new}")
    for old, _new in whole:
        if old in absent:
            continue
        try:
            texts[old] = add_formerly(read_exact(tests / f"{old}.py"), old)
        except (ValueError, SyntaxError, AssertionError) as e:
            problems.append(f"move_tests: tests/{old}.py: {e}")
    plans: Dict[str, SplitPlan] = {}
    for old, places in splits.items():
        if old in absent:
            continue
        try:
            plans[old] = SplitPlan(repo, old, places)
        except SystemExit as e:
            problems += str(e).splitlines()
    for old, plan in plans.items():
        problems += [f"move_tests: tests/{old}.py: {p}"
                     for p in run_file_problems(repo, old, plan.placed)]
    r = subprocess.run(["git", "check-ignore", "--stdin", "-z"], cwd=repo, capture_output=True,
                       input="".join(f"{x}\0" for x in dict.fromkeys(new_paths)).encode())
    if r.returncode not in (0, 1):
        problems.append(f"move_tests: git check-ignore: {r.stderr.decode().strip()}")
    for x in [x for x in r.stdout.decode().split("\0") if x]:
        problems.append(f"move_tests: {x} is a path git ignores (.gitignore): name the folder "
                        f"otherwise")
    if problems:
        raise SystemExit("\n".join(dict.fromkeys(problems)))
    if plans:
        hits = ruff_hits(repo, [f"tests/{o}.py" for o in plans])
        if hits is None:
            raise SystemExit(f"move_tests: ruff does not run with {sys.executable}: a split "
                             f"re-keys the baseline's ruff counts (the dev venv's Python has it)")
        for old, plan in plans.items():
            plan.hits = hits.get(f"tests/{old}.py", [])
    return texts, plans


def move(repo: Path, moves: List[Tuple[str, str]], dry_run: bool = False) -> List[str]:
    """Do the moves; return the mentions of an old name left. A dry run
    checks them all (and computes each new text) and changes nothing; a
    failure after the checks undoes every change made."""
    tests = repo / "tests"
    whole, _splits = grouped(moves)
    texts, plans = _check(repo, moves)
    for plan in plans.values():
        for line in plan.describe():
            print(line)
    if dry_run and plans:
        # The write's ruff check too, on the new texts in a folder of their own.
        with tempfile.TemporaryDirectory(prefix="move-tests-dry-") as td:
            root = Path(td)
            if (repo / "pyproject.toml").is_file():
                shutil.copy2(repo / "pyproject.toml", root / "pyproject.toml")
            for plan in plans.values():
                for new, text in plan.texts.items():
                    (root / "tests" / new).parent.mkdir(parents=True, exist_ok=True)
                    write_exact(root / "tests" / new, text)
            try:
                split_counts(root, plans)
            except RuntimeError as e:
                raise SystemExit(f"move_tests: {e}") from e
    if dry_run:
        for old, new in whole:
            print(f"tests/{old}.py -> tests/{new} ({dotted(new)})")
        return []
    undo: List[Callable[[], None]] = []

    def git(*a: str) -> None:
        r = subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"git {' '.join(a)}: {r.stderr.strip()}")

    def write(p: Path, text: str) -> None:
        # write_exact is all or nothing (TJ-3725): a write that fails changed
        # nothing, so its undo follows it.
        before = read_exact(p) if p.exists() else None
        write_exact(p, text)
        undo.append(lambda: p.unlink() if before is None else write_exact(p, before))
    try:
        _move(repo, tests, whole, texts, git, write, undo, plans)
    except BaseException as e:
        failed = 0
        for step in reversed(undo):
            try:
                step()
            except (OSError, RuntimeError) as e2:   # keep undoing; say what could not be
                print(f"move_tests: undoing: {e2}", file=sys.stderr)
                failed += 1
        raise SystemExit(f"move_tests: {e}; " + ("every change undone" if not failed else
                         f"undone but for {failed} step(s) above: `git status` shows them")) from e
    olds = [o for o, _n in whole] + list(plans)
    moved_to = {f"tests/{new}" for _o, new in whole} | {
        f"tests/{new}" for plan in plans.values() for new in plan.news}
    left = []
    word = re.compile(r"\b(" + "|".join(re.escape(o) for o in olds) + r")\b")
    for f in tracked_text_files(repo):
        if f in TOOLS:
            continue
        text = _read(repo / f)
        if text is None or not word.search(text):
            continue
        shared = shared_from_lines(f, text)     # history (TJ-3996)
        for n, ln in enumerate(split_lines(text), 1):
            if n in shared:
                continue
            if word.search(ln) and not (f in moved_to and is_formerly_line(ln)):
                tag = " (history)" if f in HISTORY else ""
                left.append(f"{f}:{n}{tag}: {ln.strip()[:140]}")
    return left


def _packages(repo: Path, tests: Path, new: str, git: Callable[..., None],
              write: Callable[[Path, str], None], undo: List[Callable[[], None]]) -> None:
    """Each folder on the way to NEW a package (made, its __init__.py added)."""
    parts = tuple(Path(new).parent.parts)
    for i in range(1, len(parts) + 1):
        d = tests.joinpath(*parts[:i])
        if not d.exists():
            d.mkdir()
            undo.append(d.rmdir)
        init = d / "__init__.py"
        if not init.exists():
            write(init, f'"""{package_doc(parts[:i])}"""\n')
            rel = init.relative_to(repo).as_posix()
            git("add", "--", rel)
            undo.append(lambda rel=rel: git("rm", "-q", "--cached", "--", rel))


def _split(repo: Path, tests: Path, plan: "SplitPlan", git: Callable[..., None],
           write: Callable[[Path, str], None], undo: List[Callable[[], None]]) -> None:
    """PLAN's changes: its module removed, each new file written and added."""
    old = f"tests/{plan.old}.py"
    git("rm", "-q", "--cached", "--", old)
    undo.append(lambda: git("add", "--", old))
    (repo / old).unlink()
    undo.append(lambda: write_exact(repo / old, plan.text))
    for new, text in plan.texts.items():
        _packages(repo, tests, new, git, write, undo)
        write(tests / new, text)
        rel = f"tests/{new}"
        git("add", "--", rel)
        undo.append(lambda rel=rel: git("rm", "-q", "--cached", "--", rel))


def split_counts(root: Path, plans: Dict[str, "SplitPlan"]) -> Dict[str, Dict[str, Dict[str, int]]]:
    """The baseline's counts for the splits' new files, written below ROOT
    (the repository; a dry run's copy of their texts): ruff's on each (at
    most SplitPlan.ruff_bound: a copy's count again, never a hit of its own),
    its suppression comments. RuntimeError when ruff does not run or finds
    more."""
    rels = [f"tests/{new}" for plan in plans.values() for new in plan.news]
    hits = ruff_hits(root, rels)
    if hits is None:
        raise RuntimeError("ruff did not run on the new files")
    noqa_in_text = _sibling("check_architecture").noqa_in_text
    out: Dict[str, Dict[str, Dict[str, int]]] = {"ruff": {}, "noqa": {}}
    for plan in plans.values():
        for new in plan.news:
            rel = f"tests/{new}"
            got = counted(hits.get(rel, []))
            may = plan.ruff_bound(new)
            over = sorted(c for c, n in got.items() if n > may.get(c, 0))
            if over:
                raise RuntimeError(f"{rel}: ruff finds {over[0]} {got[over[0]]} time(s), the "
                                   f"lines of tests/{plan.old}.py it took {may.get(over[0], 0)}: "
                                   f"a finding of the new file's own (by hand)")
            for kind, counts in (("ruff", got), ("noqa", noqa_in_text(plan.texts[new]))):
                for code, n in counts.items():
                    out[kind].setdefault(code, {})[rel] = n
    return out


def _move(repo: Path, tests: Path, moves: List[Tuple[str, str]], texts: Dict[str, str],
          git: Callable[..., None], write: Callable[[Path, str], None],
          undo: List[Callable[[], None]], plans: Optional[Dict[str, "SplitPlan"]] = None) -> None:
    """The moves' and the splits' changes, each with its undo pushed on UNDO."""
    plans = plans or {}
    for old, new in moves:
        _packages(repo, tests, new, git, write, undo)
        git("mv", f"tests/{old}.py", f"tests/{new}")
        undo.append(lambda old=old, new=new: git("mv", f"tests/{new}", f"tests/{old}.py"))
        write(tests / new, texts[old])
    for plan in plans.values():
        _split(repo, tests, plan, git, write, undo)
    olds = dict(moves)
    paths = {f"tests/{old}.py": f"tests/{new}" for old, new in moves}
    b = repo / BASELINE
    if b.is_file():
        doc = rekey_baseline(json.loads(read_exact(b)), paths)
        if plans:
            doc = split_baseline(doc, [f"tests/{o}.py" for o in plans], split_counts(repo, plans))
        write(b, json.dumps(doc, indent=1) + "\n")
    run = run_files(tests)       # the moved modules' `Formerly:` lines are written
    classes = {f"{old}.{cls}": new for old, plan in plans.items()
               for cls, new in plan.placed.items()}
    for f in tracked_text_files(repo):
        if f in HISTORY or f in TOOLS or f == str(BASELINE):
            continue
        p = repo / f
        text = _read(p)
        if text is None:
            continue
        new_text = rename_text(f, text, olds, run, classes)
        if new_text != text:
            write(p, new_text)
    lay = layout_path(tests)
    if lay.is_file():
        t = read_exact(lay)
        t2 = re.sub(r"^ROUND_NAMED_MAX = \d+$", f"ROUND_NAMED_MAX = {round_named_count(tests, lay)}",
                    t, count=1, flags=re.M)
        if t2 != t:
            write(lay, t2)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry-run" in argv
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    moves = read_placement(Path(args[0]).read_text(encoding="utf-8"))
    left = move(REPO, moves, dry_run=dry)
    if not dry:
        whole, splits = grouped(moves)
        split = (f", {len(splits)} split into "
                 f"{len({n for p in splits.values() for _c, n in p})} file(s)" if splits else "")
        print(f"move_tests: {len(whole)} module(s) moved{split}; ROUND_NAMED_MAX "
              f"{round_named_count(TESTS)}")
        hist = [x for x in left if "(history)" in x]
        rest = [x for x in left if "(history)" not in x]
        if rest:
            print(f"move_tests: {len(rest)} other mention(s) of an old name to settle:")
            for x in rest:
                print(f"  {x}")
        if hist:
            print(f"move_tests: {len(hist)} in CHANGELOG.md and backlog.md (history; "
                  f"an open backlog entry gets the new name)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
