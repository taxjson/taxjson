"""Golden ledgers: whole-book answers a refactor must not move.

Each book under tests/golden/<book>/ has a book.json and an expected/
folder of what it produced when it was recorded:

  kind "project"  project/ is a complete synthetic project (inputs, taxjson.toml,
                  the elections saved beside the inputs). It is copied to a
                  temp folder and `tjs run --no-input` runs on it. Recorded:
                  everything the run writes or changes outside work/ (reports/,
                  exports/, what it adds beside the inputs), the _WORK_COMPARED
                  files in work/, and its stdout and stderr (_run/).
                  Then the book's `views`, in order, in the same project:
                  each a `tjs` command line (or {"argv", "cwd", "record",
                  "width", "tool"}: cwd from the run's folder; record "output"
                  leaves out what that view writes, each path at its state
                  before it; width the TAXJSON_WIDTH it prints at, 0 by
                  default as the run's captured files, 120 as a person piping
                  it sees it, named <name>_w120; tool a taxjson.bin module
                  (a console script's) run with argv instead of `tjs`, for a
                  tool's document no command prints, named <tool>_<argv>).
                  Recorded under _views/: each one's stdout (<name>.stdout.json
                  for a --json view whose output parses, so it compares by
                  value), its stderr when it has one, every exit code in
                  index.json, and under _views/files/ what the views wrote or
                  changed outside work/, judged on the masked text (a
                  byte-for-byte copy of a file already there only named, in
                  copies.json; a digest of a project file checked against it).
  kind "engine"   inputs.json holds a fuzzer's book, frozen (the generator can
                  change; the book does not). Each country's compute_gains
                  runs on it directly, its stderr recorded beside the result.
                  Not through run_gains, which post-processes the result, money
                  rounded to 4 places among it: these books are the ones that
                  see a change past that. The project books cover run_gains.

Left out, on purpose: the rest of work/, the stages' intermediate files. The
plan replaces those; what they feed is compared. Masked (_STAMPS): the clock,
which is the only thing that moves between runs (run dates, and how many days
of the synthetic rate cache exist), a close-year lock's build (taxjson's commit
and version) and its inputs' digests, and a trace's row hashes (the PII gate reads them as account
numbers). A book's dates are all long past, so a radar's rows do
not depend on the day it runs.

The comparison is structural for JSON and TOML: numbers agree to float noise
(_close), keys and list order exactly. A --json view's money also carries no
more places than its field's (money_tails: no binary float's error, TJ-4930). A line of text compares exactly: spacing,
blank lines and how a number is printed are what the person sees. A project file
the run deletes is recorded under _run/removed/, so it shows as a difference.

The clock: tests/test_golden_clock.py runs every project book a thousand
days later (tests/_clockshift) and expects the same answer.

A refactor leaves these alone: a difference means it changed behaviour. A
change that means to move a figure or a message records the new answer, and
the diff of expected/ is what its review reads:

    venv/bin/python3 tests/_golden.py --update [book ...]

Every book is synthetic; the amounts are made up, and the rates are
tests/_hermetic's constants.
"""
import argparse
import fnmatch
import functools
import hashlib
import io
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager, redirect_stderr
from decimal import Decimal
from pathlib import Path, PurePosixPath

import _paths

# This checkout's code, whichever taxjson the interpreter would have found:
# one recording cannot mix two checkouts.
sys.path.insert(0, str(_paths.SRC))

import _hermetic  # noqa: F401  (a synthetic HOME, offline: tests/_hermetic)
import _style

from taxjson.engine.by_country import get_tax_rules
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.pipeline import prepare_books
from taxjson.model import money as M
import tomllib

REPO = _paths.REPO
GOLDEN = _paths.TESTS / "golden"
# Agree past a float's last digits, and no further: a satoshi is a difference.
ABS_TOLERANCE = 1e-9
REL_TOLERANCE = 1e-14
MAX_SHOWN = 15
_PLAIN_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?\Z")
_LEDGER = re.compile(r"(?:^|/)work/[^/]*_gains\.json\Z")
_ENGINE_LEDGERS = ("canada.json", "usa.json")
# What a run writes into a project: never copied from project/, never tracked
# there (.gitignore). A test holds this to what the books record.
_GENERATED = ("work", "reports", "exports", "crypto_sends.tt")
VIEWS = "_views/"
_CLOCKSHIFT = _paths.TESTS / "_clockshift"
_OLDSUM = _paths.TESTS / "_oldsum"
_WORK_COMPARED = ("*_gains.json", "*_gains_wash.json", "*_report.json",
                  "*_gains.traces", "*_gains_wash.traces")
_STAMPS = (
    (re.compile(r"(?m)^generated_at = .*$"), "generated_at = 2000-01-01T00:00:00Z"),
    (re.compile(r'"(generated_at|as_of_date|closed_at)": "[^"]*"'), r'"\1": "<stamp>"'),
    # A string only: a count under one of these names is not the clock (the
    # years' last run, a snapshot's, the checklist's verdicts).
    (re.compile(r'"(last_run|taken|checked|checked_oldest)": "[^"]*"'), r'"\1": "<stamp>"'),
    # A snapshot's folder, named for its year and the time it was taken.
    (re.compile(r"\b(\d{4})\.\d{8}-\d{6}(?:-\d+)?\b"), r"\1.<stamp>"),
    (re.compile(r"(?m)^(Generated:\s+)\S+"), r"\1<stamp>"),
    (re.compile(r"(?m)^(# generated\s+).*$"), r"\1<stamp>"),
    (re.compile(r"(?m)^(WASH RADAR .*, as of )\d{4}-\d\d-\d\d"), r"\1<stamp>"),
    (re.compile(r"( Valet for )\d+( dates)"), r"\1<n>\2"),
    # Not the clock: the build a close-year lock records (commit, a dirty tree,
    # version), which moves with every commit and differs in a clean clone.
    # Its keys still compare.
    (re.compile(r'"taxjson": \{[^{}]*\}'),
     lambda m: re.sub(r'(": )(?:"[^"]*"|true|false|null|-?[0-9.]+)', r'\1"<build>"', m.group(0))),
    # A digest no file of the project checked (_checked_digests): a file name
    # beside a hex digest reads to gitleaks as a token (coinbase_demo.csv).
    (re.compile(r'(": ")[0-9a-f]{64}(")'), r"\1<sha256>\2"),
    # A TXF file's header is dated the day it is made; its records' D lines are not.
    (re.compile(r"\A(V\d+\nA[^\n]*\n)D\d\d/\d\d/\d{4}$", re.M), r"\1D<stamp>"),
    # Not the clock: a trace's hash of a row, which the PII gate rightly reads as
    # an account number when its digits run long beside `account=`.
    (re.compile(r"\bid=[0-9a-f]{16}\b"), "id=<hash>"),
)

# Masked in one command's view alone ({its first word: stamps}), so a count
# of that name anywhere else still compares. Not the clock either: a
# snapshot's size, which counts the build its snapshot.json records (a dirty
# tree's flag, the commit's spelling).
_VIEW_STAMPS = {
    "snapshot": ((re.compile(r'("bytes": )\d+'), r'\1"<bytes>"'),),
}


def books() -> list:
    return sorted(p.parent.name for p in GOLDEN.glob("*/book.json"))


def meta(book: str) -> dict:
    return json.loads((GOLDEN / book / "book.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- running
def _masked(text: str, *folders) -> str:
    # Longest first: one spelling can end in the other (/private/var, /var).
    for spelling in sorted(folders, key=len, reverse=True):
        text = text.replace(spelling, "<project>")
    for pattern, stamp in _STAMPS:
        text = pattern.sub(stamp, text)
    return text


_DIGEST = re.compile(r'"([^"\n]+)": "([0-9a-f]{64})"')


def _checked_digests(text: str, rel: str, files: dict) -> str:
    """A recorded file's `"<path>": "<sha256>"` pairs, each checked against
    the project file it names (beside the recorded file's folder, as a
    close-year lock's frozen inputs are, or from the project's root):
    `<sha256 ok>` or `<sha256 MISMATCH>`. One that names no file is left to
    _STAMPS's mask."""
    here = PurePosixPath(rel).parent

    def check(m):
        for cand in (here.parent / m.group(1), here / m.group(1), PurePosixPath(m.group(1))):
            data = files.get(str(cand))
            if data is not None:
                ok = hashlib.sha256(data).hexdigest() == m.group(2)
                return f'"{m.group(1)}": "<sha256 {"ok" if ok else "MISMATCH"}>"'
        return m.group(0)
    return _DIGEST.sub(check, text)


def _compared(rel: str) -> bool:
    parts = rel.split("/")
    if "work" not in parts:
        return True
    return parts[-2] == "work" and any(fnmatch.fnmatch(parts[-1], p) for p in _WORK_COMPARED)


def _make_private(root: Path) -> None:
    """A run warns about a project others can read, and a fresh checkout's
    modes follow the umask: a book's answer must not."""
    for p in (root, *root.rglob("*")):
        p.chmod(0o700 if p.is_dir() else 0o600)


def _snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def _decoded(data: bytes) -> str:
    """A text output, or what identifies a binary one: it still compares."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return f"binary: {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()}\n"


def _env(clock_days: int, old_sum: bool = False) -> dict:
    """The run's environment: the clock `clock_days` later
    (tests/_clockshift), and with `old_sum` Python 3.11's float sum()
    (tests/_oldsum, TJ-2791)."""
    env = _style.env(TAXJSON_WIDTH=0)
    if clock_days:
        env["PYTHONPATH"] = os.pathsep.join((str(_CLOCKSHIFT), env["PYTHONPATH"]))
        env["TAXJSON_TEST_CLOCK_DAYS"] = str(clock_days)
    if old_sum:
        env["PYTHONPATH"] = os.pathsep.join((str(_OLDSUM), env["PYTHONPATH"]))
        env["TAXJSON_TEST_OLD_SUM"] = "1"
    return env


def _tool(module: str, argv: list, cwd: Path, env: dict):
    """The taxjson.bin module `module` run with argv, as a child."""
    return subprocess.run(
        python_module_argv(f"taxjson.bin.{module}", argv),
        cwd=cwd, capture_output=True, text=True, env=env, timeout=900,
        umask=0o077, stdin=subprocess.DEVNULL, check=False)


def _tjs(argv: list, cwd: Path, env: dict):
    """`tjs argv` as a child; argv that starts with a tool's console script
    (a "tool" view's view_argv) runs that taxjson.bin module instead."""
    tools = console_scripts()
    if argv and argv[0] in tools:
        return _tool(tools[argv[0]], argv[1:], cwd, env)
    return _tool("taxjson_run", argv, cwd, env)


@functools.cache
def console_scripts() -> dict:
    """{console script: taxjson.bin module} of the stand-alone tools
    (pyproject.toml's [project.scripts], `taxjson-NAME =
    "taxjson.bin._entry:MODULE"`): the modules a view's "tool" may name."""
    scripts = tomllib.loads((_paths.REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    return {name: target.split(":", 1)[1] for name, target in scripts.items()
            if target.startswith("taxjson.bin._entry:")}


def view_tool(view):
    """The taxjson.bin module a view runs instead of `tjs` (its "tool"),
    or None; a "tool" that is no console script's module is refused."""
    tool = view.get("tool") if isinstance(view, dict) else None
    if tool is not None and (not isinstance(tool, str) or tool not in console_scripts().values()):
        raise AssertionError(f"tool {tool!r}: a taxjson.bin module a console script runs (pyproject.toml)")
    return tool


def view_args(view) -> list:
    """The view's argv as its book writes it (after `tjs`, or after its
    tool)."""
    return shlex.split(view["argv"] if isinstance(view, dict) else view)


def view_argv(view) -> list:
    """The words the view runs: its argv after `tjs`, or a tool's console
    script and then its argv (`taxjson-sum-gains --json x.json`), as
    scripts/style/byte_proof.py and tests/_views_ab.py read a command."""
    tool = view_tool(view)
    first = [next(n for n, mod in console_scripts().items() if mod == tool)] if tool else []
    return first + view_args(view)


def view_width(view) -> int:
    """The TAXJSON_WIDTH a view prints at: its "width", else 0 (never wrap,
    as the run's captured files are). A width other than 0 is what a person
    sees: lib/out.py width() reads it (at least 40)."""
    return view.get("width", 0) if isinstance(view, dict) else 0


def view_name(view) -> str:
    """The file name a view records under: its words, the dashes and the
    folder's dots dropped, a tool's module before them and a width other
    than 0 last (`sum --json` is sum_json, ../2025's `handoff`
    2025_handoff, `sum` at width 120 sum_w120, taxjson_sum_gains's
    `--json x.json` taxjson_sum_gains_json_x.json)."""
    cwd = view.get("cwd", "") if isinstance(view, dict) else ""
    words = [w for w in cwd.split("/") if w not in ("", ".", "..")]
    words += [view_tool(view)] if view_tool(view) else []
    words += view_args(view)
    if view_width(view):
        words.append(f"w{view_width(view)}")
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", "_".join(w.lstrip("-") for w in words)).strip("_")


def _text(rel: str, data: bytes, files: dict, spellings) -> str:
    """A file's text as recorded: decoded, its digests checked against
    `files`, its stamps and folder masked."""
    return _masked(_checked_digests(_decoded(data), rel, files), *spellings)


def _changed(rel: str, before: dict, after: dict, spellings) -> bool:
    """Whether a path's masked text differs between two snapshots (made or
    removed counts): a rewrite that moves only a stamp is no change, whether
    or not the clock ticked between them (TJ-2773, TJ-2781)."""
    old, new = before.get(rel), after.get(rel)
    if old == new:
        return False
    if old is None or new is None:
        return True
    return _text(rel, old, after, spellings) != _text(rel, new, after, spellings)


def _outputs(before: dict, after: dict, spellings, views: bool = False) -> dict:
    """{name: text} of what changed from `before` to `after` among the
    compared files: the new text (a view's under _views/files/; a view's copy
    of a file there before only named, in _views/copies.json), a removed file
    under _run/removed/ or _views/removed/. Changed means its masked text
    changed: a rewrite that moves only a stamp is not one, whether or not the
    clock ticked between the two."""
    prefix = f"{VIEWS}files/" if views else ""
    out, copied = {}, {}
    for rel in sorted(set(before) - set(after)):
        if _compared(rel):
            out[f"{VIEWS if views else '_run/'}removed/{rel}"] = ""
    # The first path in order when two files hold the same bytes.
    sources = {data: rel for rel, data in sorted(before.items(), reverse=True)} if views else {}

    for rel, data in sorted(after.items()):
        if not _compared(rel) or not _changed(rel, before, after, spellings):
            continue
        if data in sources:
            copied[rel] = sources[data]
        else:
            out[prefix + rel] = _text(rel, data, after, spellings)
    if views:
        out[f"{VIEWS}copies.json"] = json.dumps(copied, indent=1, sort_keys=True) + "\n"
    return out


def _run_views(book: str, m: dict, root: Path, env: dict, spellings) -> dict:
    """The book's views, in order, after its run: what _VIEWS records."""
    views = m.get("views", [])
    names = [view_name(v) for v in views]
    if len(set(names)) != len(names):
        raise AssertionError(f"{book}: two views record as one name: {names}")
    before = _snapshot(root)
    # Each path an output-only view changed, and its state before that view
    # (None: it did not exist). A later recorded view's change to it is
    # recorded again (TJ-2777); changed is judged on masked text, as the
    # recording is (TJ-2781). Views before the first output-only one need no
    # snapshot of their own: nothing is held yet (TJ-2789).
    held, state = {}, None
    first = next((i for i, v in enumerate(views)
                  if isinstance(v, dict) and v.get("record") == "output"), len(views))
    out, index = {}, []
    for i, (view, name) in enumerate(zip(views, names)):
        opts = view if isinstance(view, dict) else {}
        if opts.get("record", "all") not in ("all", "output"):
            raise AssertionError(f"{book}: {name}: record {opts['record']!r}: all or output")
        width = opts.get("width", 0)
        if type(width) is not int or width < 0:
            raise AssertionError(f"{book}: {name}: width {width!r}: a whole number, 0 or more")
        tool = view_tool(view)
        cwd = root / m.get("run_dir", ".") / opts.get("cwd", ".")
        argv = view_argv(view)
        if i == first:
            state = _snapshot(root)
        r = _tjs(argv, cwd, {**env, "TAXJSON_WIDTH": str(width)} if width else env)
        if state is not None:
            now = _snapshot(root)
            for rel in set(state) | set(now):
                if not _changed(rel, state, now, spellings):
                    continue
                if opts.get("record") == "output":
                    held.setdefault(rel, state.get(rel))
                else:
                    held.pop(rel, None)
            state = now
        index.append({"view": " ".join(view_args(view)), "cwd": opts.get("cwd", "."), "exit": r.returncode,
                      **({"width": width} if width else {}), **({"tool": tool} if tool else {})})
        stdout = _masked(r.stdout, *spellings)
        for pattern, stamp in _VIEW_STAMPS.get(argv[0] if argv else "", ()):
            stdout = pattern.sub(stamp, stdout)
        kind = "txt"
        if "--json" in argv:
            try:
                json.loads(stdout)
                kind = "json"
            except ValueError:
                pass
        out[f"{VIEWS}{name}.stdout.{kind}"] = stdout
        if r.stderr:
            out[f"{VIEWS}{name}.stderr.txt"] = _masked(r.stderr, *spellings)
    out[f"{VIEWS}index.json"] = json.dumps(index, indent=1) + "\n"
    after = _snapshot(root)
    for rel, was in held.items():
        if was is None:
            after.pop(rel, None)
        else:
            after[rel] = was
    out.update(_outputs(before, after, spellings, views=True))
    return out


@contextmanager
def project_run(book: str, m: dict, clock_days: int = 0, old_sum: bool = False):
    """A project book copied to a temp folder of its own and run (`tjs run
    --no-input`), its exit status checked against the book's: yields
    (root, env, result, before), `before` the project's snapshot ahead of
    the run. The folder goes when the block ends. A run left in project/ by
    hand is not copied: its old stages would be reused, and the test would
    not prove this code."""
    with tempfile.TemporaryDirectory(prefix="golden-") as tmp:
        root = Path(tmp) / "p"
        shutil.copytree(GOLDEN / book / "project", root,
                        ignore=shutil.ignore_patterns(*_GENERATED))
        _make_private(root)
        env = _env(clock_days, old_sum)
        before = _snapshot(root)
        r = _tjs(["run", "--no-input"], root / m.get("run_dir", "."), env)
        if r.returncode != m.get("exit", 0):
            raise AssertionError(f"{book}: tjs run exited {r.returncode}, "
                                 f"expected {m.get('exit', 0)}:\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
        yield root, env, r, before


def _run_project(book: str, m: dict, views: bool = True, clock_days: int = 0,
                 old_sum: bool = False) -> dict:
    """{path from the project: text} of the outputs, the temp folder's name
    removed (project_run)."""
    with project_run(book, m, clock_days, old_sum) as (root, env, r, before):
        spellings = {str(root), os.path.realpath(root)}
        out = {"_run/stdout.txt": _masked(r.stdout, *spellings),
               "_run/stderr.txt": _masked(r.stderr, *spellings)}
        out.update(_outputs(before, _snapshot(root), spellings))
        if views and m.get("views"):
            out.update(_run_views(book, m, root, env, spellings))
        return out


def _txs(rows: list) -> list:
    return [TaxTransaction(**r) for r in rows]


def _run_engine(book: str, m: dict) -> dict:
    doc = json.loads((GOLDEN / book / "inputs.json").read_text(encoding="utf-8"))
    out = {}
    for country in ("canada", "usa"):
        kw = {"per_account_basis": True} if country == "usa" else {}
        err = io.StringIO()
        with redirect_stderr(err):
            txs, shel = _txs(doc["taxable"]), _txs(doc["sheltered"])
            if m.get("prepare"):
                txs, shel, _accounts, _log = prepare_books(
                    txs, shel, taxable=True, phantom_hint=False)
            result = get_tax_rules(country).compute_gains(
                txs, sheltered_transactions=shel, **kw)
        out[f"{country}.json"] = json.dumps(result, indent=1, sort_keys=True, default=str) + "\n"
        out[f"{country}.stderr.txt"] = _masked(err.getvalue())
    return out


def produce(book: str, views: bool = True, clock_days: int = 0, old_sum: bool = False) -> dict:
    """What the book records; without its views when `views` is False (the
    lock's run alone), with the clock `clock_days` later and with `old_sum`
    Python 3.11's float sum() (a project book's processes)."""
    m = meta(book)
    if m["kind"] == "project":
        return _run_project(book, m, views=views, clock_days=clock_days, old_sum=old_sum)
    return _run_engine(book, m)


def _ledger_missing(book: str, files) -> str:
    """Why these files protect nothing, or "": a book with no ledger would
    compare empty with empty and pass."""
    if meta(book)["kind"] == "project":
        return "" if any(_LEDGER.search(n) for n in files) else "no work/*_gains.json"
    missing = [n for n in _ENGINE_LEDGERS if n not in files]
    return f"no {', '.join(missing)}" if missing else ""


# ---------------------------------------------------------------- reading
class _Lines(list):
    """A text file's lines: compared exactly."""


def parsed(name: str, text: str):
    if name.endswith(".json"):
        return json.loads(text)
    if name.endswith(".toml"):
        return tomllib.loads(text)
    return _Lines(text.splitlines())


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _close(a, b) -> bool:
    a, b = float(a), float(b)
    if a == b or (math.isnan(a) and math.isnan(b)):
        return True
    return math.isclose(a, b, rel_tol=REL_TOLERANCE, abs_tol=ABS_TOLERANCE)


def diff(a, b, path: str = "") -> list:
    """Where `a` (expected) and `b` (now) differ, as readable lines."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            if k not in a:
                out.append(f"{path}.{k}: new: {b[k]!r}")
            elif k not in b:
                out.append(f"{path}.{k}: gone (was {a[k]!r})")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, _Lines) and isinstance(b, _Lines):
        out = [] if len(a) == len(b) else [f"{path}: {len(a)} lines, now {len(b)}"]
        return out + [f"{path}[{i}]: {x!r}, now {y!r}" for i, (x, y) in enumerate(zip(a, b)) if x != y]
    if isinstance(a, list) and isinstance(b, list):
        out = [] if len(a) == len(b) else [f"{path}: {len(a)} items, now {len(b)}"]
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f"{path}[{i}]")
        return out
    if isinstance(a, bool) or isinstance(b, bool):
        return [] if a is b else [f"{path}: {a!r}, now {b!r}"]
    if _is_num(a) and _is_num(b):
        return [] if _close(a, b) else [f"{path}: {a!r}, now {b!r}"]
    if isinstance(a, str) and isinstance(b, str):
        if _PLAIN_NUMBER.match(a) and _PLAIN_NUMBER.match(b):
            return [] if _close(a, b) else [f"{path}: {a!r}, now {b!r}"]
    return [] if a == b else [f"{path}: {a!r}, now {b!r}"]


# ------------------------------------------------------------ --json money
# A --json view prints money as taxjson holds it (TJ-4930: a float sum or
# quotient printed its binary error there, 80.72999999999999 for 80.73,
# and the comparison above, to float noise, let it pass): no number
# carries more places than its field's. Money has at most model/money's
# DERIVED_PLACES; a price per unit (a key naming one) PRICE_PLACES, the
# parsers' cut; what a view adds up from the gains files, their
# GAINS_PLACES (GAINS_FIGURES: the view file's name's start, the keys). A
# quantity is not money (a float sum of units is not held to this).
PRICE_PLACES = 8
GAINS_FIGURES = {
    "form-export_": ("gain_unrounded", "adjustment_unrounded", "denied_unrounded"),
    "winners_": ("proceeds", "cost", "gain", "denied"),
    "losers_": ("proceeds", "cost", "gain", "denied"),
    "ccd-sum_": ("proceeds", "cost", "gain"),
    "taxjson_sum_gains_": ("cap", "opt", "div", "pil", "pil_paid", "cost", "proceeds",
                           "st_gain", "lt_gain"),
}


def _quantity_key(key) -> bool:
    k = str(key or "")
    return k in M._QTY_FIELDS or "qty" in k or "quantity" in k


def _places(token: str) -> int:
    e = Decimal(token).as_tuple().exponent
    return max(0, -e) if isinstance(e, int) else 0


def money_places(name: str, key) -> int:
    """The most places a number under `key` may carry in the --json view
    recorded as `name` (its file under _views/), or -1: not money."""
    if _quantity_key(key):
        return -1
    if any(name.startswith(v) and key in keys for v, keys in GAINS_FIGURES.items()):
        return M.GAINS_PLACES
    return PRICE_PLACES if "price" in str(key or "") else M.DERIVED_PLACES


def money_tails(name: str, text: str) -> list:
    """Where a --json view's text (recorded as `name`) carries a money
    number with more places than its field's (money_places), as readable
    lines; [] when none does, or the text is not JSON. A number in a list
    is under the list's key."""
    try:
        doc = json.loads(text, parse_float=lambda t: ("number", t))
    except ValueError:
        return []
    out = []

    def walk(v, path, key):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, f"{path}.{k}", k)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, f"{path}[{i}]", key)
        elif isinstance(v, tuple):
            most = money_places(name, key)
            if most >= 0 and _places(v[1]) > most:
                out.append(f"{path}: {v[1]} has {_places(v[1])} places, its field "
                           f"{most} (a binary float's error? TJ-4930)")
    walk(doc, "", None)
    return out


def _part(name: str, only) -> bool:
    return only is None or (only == "views") == name.startswith(VIEWS)


def compare(book: str, only=None, clock_days: int = 0, old_sum: bool = False) -> list:
    """Every difference between what the book produces and expected/: all of
    it, or only "run" (the views not run) or only "views" (after the run)."""
    if only not in (None, "run", "views"):
        raise ValueError(f"only: {only!r}")
    expected_dir = GOLDEN / book / "expected"
    was = {p.relative_to(expected_dir).as_posix(): p.read_text(encoding="utf-8")
           for p in sorted(expected_dir.rglob("*")) if p.is_file()}
    why = _ledger_missing(book, was)
    if why:
        return [f"expected/ protects nothing: {why}"]
    now = produce(book, views=only != "run", clock_days=clock_days, old_sum=old_sum)
    was = {n: t for n, t in was.items() if _part(n, only)}
    now = {n: t for n, t in now.items() if _part(n, only)}
    problems = [f"{n}: not produced any more" for n in sorted(set(was) - set(now))]
    problems += [f"{n}: new output, not recorded" for n in sorted(set(now) - set(was))]
    for name in sorted(set(was) & set(now)):
        try:
            problems += [f"{name}{d}" for d in diff(parsed(name, was[name]), parsed(name, now[name]))]
        except ValueError as e:         # JSON or TOML that no longer parses
            problems.append(f"{name}: does not parse: {e}")
    # What the views print now, whatever was recorded (TJ-4930).
    for name in sorted(now):
        if name.startswith(VIEWS) and name.endswith(".stdout.json"):
            problems += [f"{name}{t}" for t in money_tails(name[len(VIEWS):], now[name])]
    return problems


def report(book: str, problems: list) -> str:
    shown = "\n".join(problems[:MAX_SHOWN])
    more = f"\n... and {len(problems) - MAX_SHOWN} more" if len(problems) > MAX_SHOWN else ""
    return (f"golden book {book}: {len(problems)} difference(s)\n{shown}{more}\n"
            "A refactor must leave these alone: it changed behaviour. If the change "
            f"means to move a figure: venv/bin/python3 tests/_golden.py --update {book}")


def update(book: str) -> int:
    expected_dir = GOLDEN / book / "expected"
    now = produce(book)
    why = _ledger_missing(book, now)
    if why:
        raise SystemExit(f"{book}: refusing to record: {why}. expected/ is left as it was.")
    if expected_dir.exists():
        shutil.rmtree(expected_dir)
    for name, text in now.items():
        p = expected_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return len(now)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check or record the golden ledgers.")
    ap.add_argument("--update", action="store_true", help="record each book's current output")
    ap.add_argument("book", nargs="*", help="default: every book")
    args = ap.parse_args(argv)
    rc = 0
    for book in args.book or books():
        if args.update:
            print(f"{book}: recorded {update(book)} files")
            continue
        problems = compare(book)
        print(f"{book}: ok" if not problems else report(book, problems))
        rc = rc or bool(problems)
    return int(rc)


if __name__ == "__main__":
    sys.exit(main())
