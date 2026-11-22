"""The views A/B (step 6h): two trees' project books, every view, and each
`shown` call paired between them.

    python3 tests/_views_ab.py dump OUT [--src DIR] [--rates fx4]
        [--places N] [--fmt-log] [--books NAME ...]
    python3 tests/_views_ab.py diff BASE NEW [--strict] [--show N]
    python3 tests/_views_ab.py ties BASE NEW [--deep NEW10]
    python3 tests/_views_ab.py fmt DUMP

`dump` runs the golden project books (tests/golden, kind "project") as
tests/_golden.py does: each copied, `tjs run --no-input`, then its views
(a book that records none runs SUMMARY_VIEWS, summaries that read no
clock), every process with the shown log on (tests/_shown_log: every
`M.shown` call, by book, step (the run or a view), tool context, site and
ordinal, with its operand, places and result; and which process wrote
each file). OUT (JSON) keeps each book's outputs, as the goldens record
them, its calls, the contexts that wrote each output file (`writers`), and
a digest of every file under the project after the run and after each view
(its text as the goldens record it, masked as they mask, then
_DIGEST_MASKS and _DIGEST_SORTED, each with its reason). A book whose run
left no record of the shim (idle: its taxjson imports failed, which the
shim says), an error the shim recorded (--places could not tell a call:
TJ-3394), a log file empty, with a line cut off or with no proc record,
a process that left no exit record or fewer calls than it made, more
child contexts claimed than proc records (TJ-3396, TJ-3398), two
processes in one context, or a --fmt-log run with no format sink
(TJ-3364) did not run. --src DIR runs another tree's taxjson (the tree or
its src; the base: `git archive <commit> | tar -x -C DIR`); the books and
these tools are this tests/ folder's. --rates fx4: the HOME's rates have
four places (tests/_hermetic RATE_SETS), so a converted figure carries
six. --places N: the files 4-place writers make (the gains files, the cost
timelines, the shim's WIDENED: TJ-3370) written at N places. --fmt-log: the
tree run through a format-log copy (tests/_fmt_log.py), its prints of an
exact half recorded too.

`diff` compares two dumps' outputs book by book as the goldens compare
(_golden.parsed and _golden.diff: JSON and TOML numbers to float noise,
text lines exactly) and explains each moved number by the calls (step
6h(4a), TJ-3363). Each of BASE's calls is paired with NEW's of the same
book, step, context, site and ordinal (in 6h(4) the trees differ only in
shown's body); a call in one tree only, another count at a site or other
places is refused. A paired call is

  same         its result the same in both trees;
  cut          its operand the same and its result not: at an exact half
               (the operand's repr has places+1 places and ends in 5), the
               NEW result one unit further from zero than BASE's (as half
               up moves a tie round() did not); else it is unexplained;
  carried      its operand moved, by no more than the sum of the moves of
               its process's earlier cuts, each site and operand once
               (TJ-3395: a sum or a difference of printed cuts; `ties`
               lists the cuts each could hold); one past that bound is
               unexplained, and named.

A moved number is explained only by a cut or carried call of a process that
printed that stream (the run's or a view's: the step's processes) or wrote
that file (its opener, or the tool whose stdout went to the temp renamed
into place, and the children of either; `writers`): its BASE text the
call's BASE result as printed and its NEW text the NEW result, at the
number's own places (the call's: TJ-3397), with the same sign, spelled as
BASE spelled it (its commas), or for a JSON or TOML number its value, at
no more places than the call's; each call explains one
number at most (a maximum matching, book by book). Nothing else explains a
number: a figure no changed call printed (a gains file's, which
round_floats writes, say) reads unexplained. Its limits: a call is not
tied to the output its result reached, so any changed call of a writer's
process with the two results a number shows can explain it (TJ-3386), and
a carried call is bounded by its process's earlier cuts, not by what it
summed (TJ-3387). The labels:

  cut          each number that moved is explained, every one by a cut call;
  carried      each is explained, one at least by a carried call;
  bytes        an output whose values compare the same but whose text
               differs (-0.0, an int for a float, order, spacing, float
               noise), or whose text differs beside its explained moves:
               BASE's text with each moved number written as NEW writes it
               (spelled as BASE spelled it) is not NEW's, but for the
               spaces beside a moved number that its change of width
               realigns (TJ-3367, TJ-3378, TJ-3379);
  unexplained  anything else: a book or an output in one dump only, a
               length, a list or text reordered, a sign changed, a number
               no changed call explains.

`ties` is diff's labels and the calls, each held to the flip's rule
(`flip_rule`, TJ-3392): BASE's result is round() of its operand at its
places, NEW's the half-up cut of its operand's repr (a float not finite
passed through, an int as round() leaves it); a call that breaks it, cut,
carried or the same in both trees, is unexplained and named, and explains
nothing. Then each cut (an exact half; with
--deep, NEW's tree dumped with --places 10, its call there: "same at 10
places" (the operand unchanged: no 4-place file made it), a file tie (the
operand there another, not a half, within a unit: named) or "not found at
10 places"), each carried call with the cuts it could hold, and each call
unexplained. With NEW dumped with --fmt-log it lists each format that
printed an exact half (a figure the flip cannot move). `fmt` lists a
--fmt-log dump's format ties by site: money by its names, other, and the
sites reviewed as not money (tests/_fmt_log.py NOT_MONEY, each with its
reason).

A change meant to move nothing expects no difference: `diff --strict`
exits 1 on any (byte for byte, after the goldens' masks; its calls need
not pair), on any file whose digest differs after the run or a view (one
in one dump only, or other bytes, named: an unrecorded work file too,
TJ-3366), and when nothing was compared. `diff` exits 1 on an unexplained
or bytes difference; `ties` on that, an unexplained call (the flip's rule
broken among them), BASE calls at a half a half-up cut moves where no
call's result changed, calls changed with nothing
moved, or a format tie a half-up cut prints otherwise (money by name or
not, but at a reviewed site, TJ-3368); `fmt` on such a tie; both on a
module the format log could not rewrite. A dump with no books, a name
twice or a book that failed to run is refused (exit 2), as are two dumps
of other rates, places or Python, calls that do not pair, a log a process
left empty, cut off or without its proc record, more child contexts
claimed than proc records (TJ-3396, TJ-3398), a --deep dump of
another tree, rates or Python, not more places than NEW, or missing a
book, and for `fmt` (and `ties` of a --fmt-log NEW) a dump with a book
whose run had no format sink.
"""
import argparse
import fnmatch
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict, deque
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation
from pathlib import Path

import _fmt_log
import _golden
import _hermetic
import _paths
import _style

HERE = _paths.TESTS
SHIM = HERE / "_shown_log"
FORMAT = "taxjson views A/B 3"
# tests/_shown_log/sitecustomize.py's switches.
LOG = "TAXJSON_TEST_SHOWN_LOG"
PLACES = "TAXJSON_TEST_GAINS_PLACES"
BOOK = "TAXJSON_TEST_SHOWN_BOOK"
VIEW = "TAXJSON_TEST_SHOWN_VIEW"
CTX = "TAXJSON_TEST_SHOWN_CTX"
ROOT = "TAXJSON_TEST_SHOWN_ROOT"
GAINS_PLACES = 4                 # round_floats' default: the gains file's places
RUN = "run --no-input"
# A book that records no views runs these: summaries the golden books'
# views run, none of which reads the clock (each book's at a thousand
# days later is the same: tests/test_golden_clock.py's check).
SUMMARY_VIEWS = (
    "sum", "sum --json", "trades-sum", "trades-sum --json", "fees-sum", "fees-sum --json",
    "divs-sum", "divs-sum --json", "wash-sales", "wash-sales --json", "carryover",
    "carryover --json", "form-export", "form-export --json", "list", "list --json",
    "stats", "stats --json", "option-yield", "option-yield --json", "leaps-sum",
    "leaps-sum --json", "option-boundary", "option-boundary --json", "edge-cases",
    "edge-cases --json", "fx-cash", "fx-cash --json", "spinoffs", "spinoffs --json",
    "ccd-sum", "ccd-sum --json", "t1135", "t1135 --json",
)
LABELS = ("cut", "carried", "bytes", "unexplained")
CALL_LABELS = ("same", "cut", "carried", "unexplained")
_FAILS = ("bytes", "unexplained")
_GONE = "<not in this dump>"
# A number in a text line: digits, thousands commas, a point. Its sign
# and currency are text around it.
_NUMBER = re.compile(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
_STREAMS = (".stdout.txt", ".stdout.json", ".stderr.txt")
_KEPT = ("fmt",)                 # the log records a dump keeps beside its calls
_DECIMAL = re.compile(r"Decimal\('([^']*)'\)\Z")
# What a file's digest masks beyond the goldens' masks (_golden._STAMPS):
# what differs between two runs of one tree, each with its reason (the
# goldens record none of these files).
_DIGEST_MASKS = (
    # The clock: an older tree's merge and map stages stamp their work files
    # (work/*_base.json, *_raw.json, .blend_base.json, crypto_merged.json; no
    # longer since TJ-4267).
    (re.compile(r'"(merged_at|mapped_at)": "[^"]*"'), r'"\1": "<stamp>"'),
    # The book's copy: a wash pass's record (in its wash file's record since
    # TJ-1804, work/.wash_inputs.json before) keys its freshness on each
    # input's mtime, the moment the project was copied.
    (re.compile(r'"mtime_ns": \d+'), '"mtime_ns": "<mtime>"'),
)
# Files whose lines a digest reads in sorted order, each with its reason.
_DIGEST_SORTED = (
    # pipeline/inputs `_inputs_fingerprint` orders its lines by each file's full
    # path, so a shipped layout's line moves with where the tree lives (a
    # format-log copy, an archive) against the project's files.
    ("*_inputs.fingerprint", "lines in the order of each file's full path"),
)


class Refused(Exception):
    """A dump diff and ties will not read (exit 2)."""


class Shape(str):
    """A count where two lists or texts differ in length: never a figure."""


class Reordered(str):
    """Two lists or texts of the same items in another order: never a figure."""


# ---------------------------------------------------------------- dump
def project_books() -> list:
    return [b for b in _golden.books() if _golden.meta(b)["kind"] == "project"]


def src_folder(arg) -> Path:
    """The src folder of DIR: DIR/src, or DIR itself if it holds taxjson."""
    try:
        return _fmt_log.src_folder(arg)
    except ValueError as e:
        raise Refused(f"--src {e}") from None


def view_label(view) -> str:
    """A view as the log names it: its tjs words, after its folder when
    that is not the run's (`../2025: handoff`)."""
    cwd = view.get("cwd", ".") if isinstance(view, dict) else "."
    words = " ".join(_golden.view_argv(view))
    return words if os.path.normpath(cwd) == "." else f"{Path(os.path.normpath(cwd)).as_posix()}: {words}"


def _book_env(src: Path, home: dict, logdir: Path, book: str, places: int) -> dict:
    """The goldens' environment (_golden._env) with `src`'s taxjson and the
    shown log first on PYTHONPATH, the book's own synthetic HOME."""
    env = _style.env(TAXJSON_WIDTH=0)
    drop = {_style.SRC.resolve(), src, SHIM}
    rest = [p for p in env["PYTHONPATH"].split(os.pathsep) if p and Path(p).resolve() not in drop]
    env["PYTHONPATH"] = os.pathsep.join([str(src), str(SHIM), *rest])
    env.update(home)
    env.update({LOG: str(logdir), BOOK: book})
    for k in (VIEW, PLACES, CTX, ROOT):
        env.pop(k, None)
    if places != GAINS_PLACES:
        env[PLACES] = str(places)
    return env


def _taxjson_of(env: dict, cwd: Path) -> Path:
    """Where a child with `env` imports taxjson from (an empty cwd)."""
    r = subprocess.run([sys.executable, "-c", "import taxjson.model.money as m; print(m.__file__)"],
                       cwd=cwd, env=env, capture_output=True, text=True, timeout=120, check=False)
    if r.returncode:
        raise Refused(f"taxjson does not import: {r.stderr.strip()[-500:]}")
    return Path(r.stdout.strip()).resolve().parents[2]




def _read_log(folder: Path) -> list:
    """Every record of a log folder, each process's file in the order it
    wrote them, each naming its file: a call (the shim's compact "s" line)
    as a "shown" record with its process's book and view, its ordinal at
    its context and site (`n`) and its rank among the process's calls
    (`q`)."""
    recs = []
    for p in sorted(folder.glob("*.jsonl")):
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = []
        for i, line in enumerate(text.split("\n")[:-1] if text.endswith("\n") else text.split("\n")):
            try:
                r = json.loads(line)
            except ValueError:
                r = None
            last = i == text.count("\n") and not text.endswith("\n")
            if last or not (isinstance(r, dict) or (isinstance(r, list) and len(r) == 6 and r[0] == "s")):
                # A process killed mid-write (TJ-3398), or one that died
                # before its first flush: an empty file (TJ-3396).
                r = {"kind": "unreadable", "line": i + 1, "text": line[:60] or "an empty log"}
            lines.append(r)
        head = next((r for r in lines if isinstance(r, dict) and r.get("kind") == "proc"), {})
        ranks, q = Counter(), 0
        for r in lines:
            if isinstance(r, list):
                _s, ctx, site, x, places, result = r
                r = {"kind": "shown", "book": head.get("book", "?"), "view": head.get("view", "?"),
                     "ctx": ctx, "site": site, "repr": x, "places": places, "result": result,
                     "n": ranks[(ctx, site)], "q": q}
                ranks[(ctx, site)] += 1
                q += 1
            r["file"] = p.name
            recs.append(r)
    return recs


def process_of(ctx: str) -> str:
    """The process a context runs in: the context up to its first tool
    after its last child process ("0>taxjson_gains#2" runs in "0")."""
    i = ctx.rfind("|")
    j = ctx.find(">", i + 1)
    return ctx if j < 0 else ctx[:j]


def writers(records, outputs) -> dict:
    """{output file: [[step, context], ...] that wrote it}: each that
    opened it for writing, or renamed into place a file it wrote; a file a
    tool's stdout was redirected to (run_to_file's temp, by its inode: a
    tool started after the temp was last opened) is that tool's, not its
    caller's. A run's file counts the run's writes, a view's
    (_views/files/) the views'."""
    direct, renames, tools = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in records:
        phase = "run" if r["view"] == RUN else "views"
        if r["kind"] == "write":
            direct[(phase, r["path"])].append(r)
        elif r["kind"] == "rename":
            renames[(phase, r["path"])].append(r)
        elif r["kind"] == "ctx" and r.get("ino"):
            tools[tuple(r["ino"])].append(r)

    def of(phase, rel, seen):
        if (phase, rel) in seen:
            return set()
        seen = seen | {(phase, rel)}
        out = {(w["view"], w["ctx"]) for w in direct.get((phase, rel), ())}
        for rn in renames.get((phase, rel), ()):
            opened = max((w["t"] for w in direct.get((phase, rn["src"]), ()) if w["t"] <= rn["t"]), default=0)
            cands = [c for c in tools.get(tuple(rn.get("ino") or ()), ()) if opened <= c["t"] <= rn["t"]]
            if cands:
                c = max(cands, key=lambda c: c["t"])
                out.add((c["view"], c["id"]))
            else:
                out |= of(phase, rn["src"], seen)
        return out

    found = {}
    for name in outputs:
        if name.startswith(f"{_golden.VIEWS}files/"):
            found[name] = sorted(map(list, of("views", name[len(_golden.VIEWS) + len("files/"):], frozenset())))
        elif not name.startswith(("_run/", _golden.VIEWS)):
            found[name] = sorted(map(list, of("run", name, frozenset())))
    return found


def _calls(records) -> dict:
    """A book's calls as a dump keeps them: {step: {context: {site:
    [[operand, places, result, q], ...]}}}, each site's list in its
    ordinal's order."""
    out = {}
    for r in records:
        if r["kind"] == "shown":
            out.setdefault(r["view"], {}).setdefault(r["ctx"], {}).setdefault(r["site"], []).append(
                [r["repr"], r["places"], r["result"], r["q"]])
    return out


def _digests(root: Path, spellings, masks) -> dict:
    """{path from the project: a digest of its text as the goldens record
    it} of every file under the project: the folder, the HOME and the src
    masked (_golden._text), then `masks` (_DIGEST_MASKS)."""
    files = _golden._snapshot(root)
    out = {}
    for rel, data in sorted(files.items()):
        text = _golden._text(rel, data, files, spellings)
        for s, mark in masks:
            text = text.replace(s, mark)
        for pattern, mask in _DIGEST_MASKS:
            text = pattern.sub(mask, text)
        if any(fnmatch.fnmatch(rel.rsplit("/", 1)[-1], pat) for pat, _why in _DIGEST_SORTED):
            text = "".join(sorted(text.splitlines(keepends=True)))
        out[rel] = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return out


def _shim_problem(records, fmt_log: bool, steps=(RUN,), claims: int = 0):
    """Why a book's shown log cannot be read (TJ-3364, TJ-3377, TJ-3396,
    TJ-3398), or None: the shim said it could not install (or that
    --places could not tell a call), a log file is empty or has a line cut
    off, a log file has no proc record (written and flushed first), a step
    (the run or a view) left no proc record (idle there: no calls would
    read as none), a process left no exit record or fewer calls than it
    counted (its log cut short), more child processes claimed a context
    (`claims`, the files in the log's claims/) than left a proc record,
    two processes of a step had one context (their calls would not
    pair), or under --fmt-log a process had no format sink."""
    errors = [r for r in records if r["kind"] == "error"]
    if errors:
        what = errors[0].get("what") or "the shown log did not install"
        return f"{what} in {len(errors)} process(es): {errors[0]['error']}"
    bad = [r for r in records if r["kind"] == "unreadable"]
    if bad:
        r = bad[0]
        return (f"{len(bad)} log line(s) empty or cut off ({r['file']} line {r['line']}: {r['text']!r}): "
                f"a process died mid-write or before its first flush")
    procs = [r for r in records if r["kind"] == "proc"]
    headless = sorted({r.get("file") for r in records} - {r.get("file") for r in procs} - {None})
    if headless:
        return (f"{len(headless)} log file(s) with no proc record ({headless[0]}): a process the shim did "
                f"not start in (a fork?) or one that died before its first flush")
    seen = {r["view"] for r in procs}
    idle = [s for s in steps if s not in seen]
    if idle:
        where = "the run" if idle == [RUN] else f"{len(idle)} step(s) ({idle[0]})"
        return f"the shown log left no record in {where}: the shim was idle"
    exits = {r.get("file"): r for r in records if r["kind"] == "exit"}
    calls = Counter(r.get("file") for r in records if r["kind"] == "shown")
    short = [r for r in procs if exits.get(r.get("file"), {}).get("calls") != calls[r.get("file")]]
    if short:
        r = short[0]
        return (f"{len(short)} process(es) left no exit record or fewer calls than they made ({r['view']}: "
                f"{calls[r.get('file')]} read, {exits.get(r.get('file'), {}).get('calls', 'no exit')}): "
                f"the log was cut short")
    children = sum(1 for r in procs if "|" in (r.get("pctx") or ""))
    if claims > children:
        return (f"{claims} child process(es) claimed a context and {children} left a proc record: "
                f"a process died before its first flush")
    twice = sorted(k for k, n in Counter((r["view"], r.get("pctx")) for r in procs).items() if n > 1)
    if twice:
        return (f"{len(twice)} context(s) of two processes ({twice[0][0]}: {twice[0][1]}): "
                f"their calls cannot be told apart")
    sinkless = [r for r in procs if not r.get("fmt_sink")]
    if fmt_log and sinkless:
        return (f"{len(sinkless)} process(es) had no format sink ({sinkless[0]['view']}): "
                f"the format log's copy did not run there")
    return None


def dump_book(book: str, src: Path, rates: str, places: int, run_src: Path = None,
              fmt_log: bool = False) -> dict:
    """One book's record: its views, its outputs (_golden._run_project's),
    its calls and format records, the contexts that wrote each output
    file, every file's digest after the run and after each view, and the
    error that stopped it (None). `run_src`: the src the children import
    (a format-log copy of `src`)."""
    run_src = run_src or src
    m = dict(_golden.meta(book))
    if not m.get("views"):
        m["views"] = list(SUMMARY_VIEWS)
    views = [{"name": _golden.view_name(v), "label": view_label(v)} for v in m["views"]]
    with tempfile.TemporaryDirectory(prefix="views-ab-") as tmp:
        logdir, home_dir = Path(tmp) / "log", str(Path(tmp) / "home")
        env = _book_env(run_src, _hermetic.home_env(home_dir, rates), logdir, book, places)
        got = _taxjson_of(env, Path(tmp))
        if got != run_src:
            raise Refused(f"{book}: taxjson came from {got}, not {run_src}")
        first, stages, state = [], [], {}
        real_env, real_tjs = _golden._env, _golden._tjs
        homes = sorted({home_dir, os.path.realpath(home_dir)}, key=len, reverse=True)
        masks = [(s, "<home>") for s in homes] + [(str(run_src), "<src>"), (str(src), "<src>")]

        def labelled(argv, cwd, run_env):
            # Each tjs process named by its view, for its log records: the
            # first is the run, in the folder the views' cwd is from. The
            # project's root bounds the files the log names.
            if not first:
                first.append(Path(cwd))
                first.append(Path(os.path.normpath(Path(cwd) / os.path.relpath(".", m.get("run_dir", ".")))))
            rel = os.path.relpath(cwd, first[0])
            words = " ".join(argv)
            label = words if rel == "." else f"{Path(rel).as_posix()}: {words}"
            r = real_tjs(argv, cwd, dict(run_env, **{VIEW: label, ROOT: str(first[1])}))
            # Every file's digest after the run, and what each view changed.
            root = first[1]
            now = _digests(root, {str(root), os.path.realpath(root)}, masks)
            changed = {k: now.get(k) for k in sorted(set(now) | set(state)) if now.get(k) != state.get(k)}
            stages.append([label, changed])
            state.clear()
            state.update(now)
            return r

        _golden._env = lambda clock_days=0, old_sum=False: dict(env)
        _golden._tjs = labelled
        try:
            outputs, error = _golden._run_project(book, m), None
        except (AssertionError, subprocess.SubprocessError, OSError) as e:
            # A run that exited otherwise than the book records, or a view
            # that timed out: recorded, and diff refuses the dump.
            outputs, error = {}, f"{type(e).__name__}: {e}"
        finally:
            _golden._env, _golden._tjs = real_env, real_tjs
        records = _read_log(logdir)
        steps = [s[0] for s in stages] or [RUN]
        twice = sorted(s for s, n in Counter(steps).items() if n > 1)
        error = error or (f"step {twice[0]!r} twice: its calls would not pair" if twice else None) \
            or _shim_problem(records, fmt_log, steps, len(list((logdir / "claims").glob("*.*"))))
        spellings = sorted({home_dir, os.path.realpath(home_dir), str(run_src)}, key=len, reverse=True)
        for name, text in outputs.items():
            for s in spellings:
                text = text.replace(s, "<home>" if s != str(run_src) else "<src>")
            outputs[name] = text
    procs = [r for r in records if r["kind"] == "proc"]
    return {"book": book, "views": views, "error": error, "outputs": outputs,
            "log": sorted((r for r in records if r["kind"] in _KEPT), key=_log_key),
            "calls": _calls(records), "writers": writers(records, outputs), "files": stages,
            "shim": {"processes": len(procs), "fmt_sink": sum(1 for r in procs if r.get("fmt_sink")),
                     "calls": sum(1 for r in records if r["kind"] == "shown")}}


def _log_key(r):
    return (r["view"], r.get("site", ""), r["kind"], r.get("repr", ""), r.get("places", 0), r.get("ctx", ""),
            r.get("expr", ""))


def dump(a) -> int:
    src = src_folder(a.src) if a.src else _style.SRC.resolve()
    books = a.books or project_books()
    unknown = sorted(set(books) - set(project_books()))
    if unknown or len(set(books)) != len(books):
        raise Refused(f"--books: {', '.join(unknown) or 'a book twice'}: "
                      f"the project books are {', '.join(project_books())}")
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="views-ab-fmt-") as fmt_tmp:
        run_src, stats = src, None
        if a.fmt_log:
            run_src, stats = _fmt_log.make(src, Path(fmt_tmp))
            print(f"format log: {stats['sites']} format site(s) in {stats['modules']} module(s)"
                  + (f"; not rewritten: {', '.join(stats['failed'])}" if stats["failed"] else ""))
        records = []
        for book in books:
            records.append(dump_book(book, src, a.rates, a.places, run_src, bool(a.fmt_log)))
            r = records[-1]
            fmts = sum(1 for x in r["log"] if x["kind"] == "fmt")
            print(f"{book}: {len(r['outputs'])} outputs, {r['shim']['calls']} shown call(s) in "
                  f"{r['shim']['processes']} process(es)"
                  + (f", {fmts} format tie(s)" if a.fmt_log else "")
                  + (f"; did not run: {r['error'].splitlines()[0]}" if r["error"] else ""))
    doc = {"format": FORMAT, "src": str(src), "rates": a.rates, "places": a.places,
           "python": platform.python_version(), "fmt_log": stats, "books": records}
    text = json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n"
    Path(a.out).write_text(text, encoding="utf-8")
    failed = [r["book"] for r in records if r["error"]]
    print(f"{len(records)} book(s) from {src} (rates {a.rates}, gains at {a.places} places"
          + (", format log" if a.fmt_log else "") + f") in {time.perf_counter() - t0:.0f}s; "
          f"{sum(r['shim']['calls'] for r in records)} call(s), {len(text) / 1e6:.1f} MB"
          + (f"; did not run: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


# ---------------------------------------------------------------- reading
def _unique_pairs(items):
    seen = Counter(k for k, _v in items)
    twice = sorted(k for k, n in seen.items() if n > 1)
    if twice:
        raise Refused(f"{twice[0]!r} twice")
    return dict(items)


def load(path) -> dict:
    """A dump, refused (Refused) when it holds nothing to compare, a name
    twice (a book, an output, a key: one would hide the other) or a book
    that did not run."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
    except Refused as e:
        raise Refused(f"{path}: {e}") from None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise Refused(f"{path}: not a views A/B dump ({FORMAT!r}: dump it again)")
    books = doc.get("books") or []
    if not books:
        raise Refused(f"{path}: no books")
    twice = sorted(n for n, k in Counter(b["book"] for b in books).items() if k > 1)
    if twice:
        raise Refused(f"{path}: book {twice[0]!r} twice")
    for b in books:
        if b.get("error"):
            raise Refused(f"{path}: {b['book']} did not run: {b['error'].splitlines()[0]}")
    doc["books"] = {b["book"]: b for b in books}
    return doc


def _same_kind(base: dict, new: dict, what: str) -> None:
    for key in ("rates", "places", "python"):
        if base[key] != new[key]:
            raise Refused(f"{what}: {key} {base[key]} against {new[key]}: dump both alike")


# ---------------------------------------------------------------- comparing
def _canon(item) -> str:
    return item if isinstance(item, str) else json.dumps(item, sort_keys=True, default=str)


def walk(a, b, path: str = ""):
    """(path, before, after) for each place `a` and `b` differ, as
    _golden.diff finds them (each of its lines is one of these), but a
    list or text of the same items in another order is one place."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            p = f"{path}.{k}"
            if k not in a or k not in b:
                yield p, a.get(k, _GONE), b.get(k, _GONE)
            else:
                yield from walk(a[k], b[k], p)
        return
    lines = isinstance(a, _golden._Lines) and isinstance(b, _golden._Lines)
    if lines or (isinstance(a, list) and isinstance(b, list)):
        what = "lines" if lines else "items"
        if len(a) != len(b):
            yield path, Shape(f"{len(a)} {what}"), Shape(f"{len(b)} {what}")
        elif a != b and sorted(map(_canon, a)) == sorted(map(_canon, b)):
            yield path, Reordered(f"{len(a)} {what}"), Reordered(f"{len(b)} {what} in another order")
            return
        for i, (x, y) in enumerate(zip(a, b)):
            if lines:
                if x != y:
                    yield f"{path}[{i}]", x, y
            else:
                yield from walk(x, y, f"{path}[{i}]")
        return
    if _golden.diff(a, b, path):
        yield path, a, b


def differences(base: dict, new: dict):
    """(output, path, before, after) for one book's outputs, and an
    output whose text differs where its values do not: (output, None,
    its first line that differs before, after)."""
    for name in sorted(set(base) | set(new)):
        if name not in base or name not in new:
            yield name, "", "<output>" if name in base else _GONE, "<output>" if name in new else _GONE
            continue
        try:
            a, b = _golden.parsed(name, base[name]), _golden.parsed(name, new[name])
        except ValueError:      # JSON or TOML that does not parse: its lines
            a, b = _golden._Lines(base[name].splitlines()), _golden._Lines(new[name].splitlines())
        rows = 0
        for path, x, y in walk(a, b):
            rows += 1
            yield name, path, x, y
        if not rows and base[name] != new[name]:
            yield name, None, *_first_change(base[name], new[name])


def _first_change(a: str, b: str):
    la, lb = a.splitlines(keepends=True), b.splitlines(keepends=True)
    for i, (x, y) in enumerate(zip(la, lb)):
        if x != y:
            return f"line {i + 1}: {x!r}", f"{y!r}"
    i = min(len(la), len(lb))
    return f"line {i + 1}: {(la[i:] or [''])[0]!r}", f"{(lb[i:] or [''])[0]!r}"


def _numbers(v):
    """(the text around a value's numbers, its numbers as Decimals, a
    text's?) or None: not a number or a text. A JSON or TOML number keeps
    its sign; a text's numbers do not (the sign is text around them)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return ("",), [Decimal(repr(v))], False
        except InvalidOperation:
            return None
    if not isinstance(v, str):
        return None
    parts = _NUMBER.split(v)
    words = tuple(" ".join(p.split()) for p in parts[0::2])
    return words, [Decimal(p.replace(",", "")) for p in parts[1::2]], True


def _places(d: Decimal) -> int:
    return max(0, -d.as_tuple().exponent)



def _shown(x: Decimal, before: str, text: bool) -> str:
    """A moved number as it reads: a text's with the minus written before it."""
    return f"-{x}" if text and before.endswith("-") else str(x)



# ---------------------------------------------------------------- pairing
def _value(text):
    """A logged repr's number as a Decimal (a float's, an int's, a
    Decimal's), or None."""
    if not isinstance(text, str):
        return None
    m = _DECIMAL.match(text)
    try:
        return Decimal(m[1] if m else text)
    except InvalidOperation:
        return None


def _as_logged(text):
    """A logged result as the program held it: a float, an int or a
    Decimal (what it formats), or None."""
    m = _DECIMAL.match(text) if isinstance(text, str) else None
    try:
        if m:
            return Decimal(m[1])
        return int(text) if re.fullmatch(r"-?\d+", text) else float(text)
    except (ValueError, TypeError, InvalidOperation):
        return None


def exact_half(repr_: str, places) -> bool:
    """The repr has places+1 places and ends in 5."""
    try:
        sign, digits, exp = Decimal(repr_).as_tuple()
    except (InvalidOperation, TypeError, ValueError):
        return False
    return isinstance(places, int) and exp == -(places + 1) and digits[-1] == 5


# The flip's cut: `shown`'s own context (model/money `_SHOWN`), mirrored here
# rather than imported, so the tool judges a tree without running its code:
# wide enough for any float, nothing trapped. tests/test_views_ab.py checks
# the two agree on a table of operands (TJ-3403).
_WIDE = Context(prec=400, rounding=ROUND_HALF_UP, traps=[])


def half_up(repr_: str, places: int) -> float:
    """The flip's cut of a float's repr: half up at `places`, in `_WIDE`,
    a float back, as step 6h(4)'s `shown` cuts (a zero keeps its sign)."""
    return float(Decimal(repr_).quantize(Decimal(1).scaleb(-places, _WIDE), rounding=ROUND_HALF_UP, context=_WIDE))


def _would_move(repr_: str, places) -> bool:
    """A float operand whose half-up cut is not round()'s."""
    if not exact_half(repr_, places):
        return False
    try:
        x = float(repr_)
    except ValueError:
        return False
    return half_up(repr_, places) != round(x, places)


class Call:
    """A call paired between the trees: its book, step, context, site and
    ordinal; BASE's and NEW's operand and result; its label (CALL_LABELS),
    why one is unexplained, and a carried call's cuts."""

    def __init__(self, book, key, places, base, new):
        self.book = book
        self.step, self.ctx, self.site, self.n = key
        self.places = places
        self.base, self.new = base, new          # (operand, result)
        self.process = (self.step, process_of(self.ctx))
        self.q = 0
        self.label, self.why, self.holds = "same", "", None
        self.d_operand = self.d_result = Decimal(0)
        self.bound = Decimal(0)

    def where(self) -> str:
        return f"{self.site} ({self.step}: {self.ctx} #{self.n})"


def _flat(record) -> dict:
    """{(step, context, site, ordinal): (operand, places, result, q)} of a dump's book."""
    out = {}
    for step, ctxs in (record.get("calls") or {}).items():
        for ctx, sites in ctxs.items():
            for site, calls in sites.items():
                for n, (x, places, result, q) in enumerate(calls):
                    out[(step, ctx, site, n)] = (x, places, result, q)
    return out


def paired(book: str, base: dict, new: dict, rule: bool = False) -> list:
    """Each of the book's calls in BASE paired with NEW's and labelled
    (`classify`; with `rule`, each checked against the flip's rule), or
    Refused: a call in one tree only, another count at a site, or other
    places."""
    a, b = _flat(base), _flat(new)
    if bool(a) != bool(b):
        empty, other = ("NEW", "BASE") if a else ("BASE", "NEW")
        raise Refused(f"{empty}'s {book} logged no call where {other}'s logged {len(a or b)}: "
                      f"was {empty}'s shown log idle?")
    if a.keys() != b.keys():
        sa, sb = Counter(k[:3] for k in a), Counter(k[:3] for k in b)
        site = min(k for k in set(sa) | set(sb) if sa[k] != sb[k])
        step, ctx, where = site
        what = (f"another count at a site: {sa[site]} in BASE, {sb[site]} in NEW" if sa[site] and sb[site]
                else f"a call in {'BASE' if sa[site] else 'NEW'} only")
        raise Refused(f"{book}: the calls do not pair: {where} ({step}: {ctx}): {what}")
    calls = []
    for key in sorted(b):
        (xa, pa, ra, _qa), (xb, pb, rb, qb) = a[key], b[key]
        if pa != pb:
            raise Refused(f"{book}: the calls do not pair: {key[2]} ({key[0]}: {key[1]} #{key[3]}): "
                          f"places {pa} in BASE, {pb} in NEW")
        c = Call(book, key, pb, (xa, ra), (xb, rb))
        c.q = qb
        calls.append(c)
    classify(calls, rule)
    return calls


def _noise(*xs) -> Decimal:
    """Float noise a carried operand's move may carry: its sums are binary."""
    return Decimal("1e-12") * (1 + sum(abs(x) for x in xs))


def _rounded(repr_: str, places, rounding) -> str:
    """What a cut of the logged operand gives, as the log writes it: a float
    by `rounding` (None: round()'s, from its binary value; else half up
    from its repr, `half_up`, as the flipped `shown` cuts), passed through
    when not finite; an int as round() leaves it; None for anything else
    (a Decimal, which `shown` cuts in its own context, reaches no logged
    call: the rule breaks on it)."""
    if not isinstance(places, int) or isinstance(places, bool):
        return None
    if re.fullmatch(r"-?\d+", repr_ or ""):
        return repr(round(int(repr_), places))
    try:
        x = float(repr_)
    except (TypeError, ValueError):
        return None
    if repr(x) != repr_:
        return None
    if not math.isfinite(x) or rounding is None:
        return repr(round(x, places)) if math.isfinite(x) else repr(x)
    return repr(half_up(repr_, places))


def flip_rule(c) -> str:
    """Why a paired call breaks the flip's rule, or "": BASE's result is
    round() of its operand at its places, NEW's the half-up cut of its
    operand's repr (a float not finite passed through; an int as round()
    leaves it) (TJ-3392, TJ-3393)."""
    for tree, (x, result), rounding in (("BASE", c.base, None), ("NEW", c.new, ROUND_HALF_UP)):
        want = _rounded(x, c.places, rounding)
        if want is None:
            return f"the flip's rule: {tree}'s operand {x} @{c.places} is not a float or an int"
        if want != result:
            how = "round()" if rounding is None else "half up from its repr"
            return f"the flip's rule: {tree}'s {x} @{c.places} gave {result}, {how} gives {want}"
    return ""


def classify(calls, rule: bool = False) -> None:
    """Each call same, cut, carried or unexplained (the module docstring),
    each process's calls in NEW's order: a carried operand's move bounded
    by the moves of the process's earlier cuts, each site and operand once
    (TJ-3395). With `rule` (`ties`), a call that breaks the flip's rule is
    unexplained whatever it is (`flip_rule`)."""
    by_process = defaultdict(list)
    for c in calls:
        by_process[c.process].append(c)
    for group in by_process.values():
        group.sort(key=lambda c: c.q)
        budget, earlier, counted = Decimal(0), [], set()
        for c in group:
            (xa, ra), (xb, rb) = c.base, c.new
            broken = flip_rule(c) if rule else ""
            if broken:
                c.label, c.why = "unexplained", broken
                continue
            if ra == rb:
                continue
            va, vb = _value(ra), _value(rb)
            if va is None or vb is None or not (va.is_finite() and vb.is_finite()):
                c.label, c.why = "unexplained", f"its result {ra} -> {rb} is not a finite number"
                continue
            if va == vb:
                c.label, c.why = "unexplained", f"its result's form changed: {ra} -> {rb}"
                continue
            c.d_result = vb - va
            if xa == xb:
                unit = Decimal(1).scaleb(-c.places) if isinstance(c.places, int) else None
                operand = _value(xa)
                if not exact_half(xa, c.places):
                    c.label, c.why = "unexplained", f"a cut not at an exact half: {xa} @{c.places} {ra} -> {rb}"
                    continue
                # Half up moves a tie one unit away from zero, where round() did not.
                if abs(c.d_result) != unit or (c.d_result > 0) != (operand > 0):
                    c.label, c.why = "unexplained", (f"a cut of {_signed(c.d_result)}, not one unit away from "
                                                     f"zero: {xa} @{c.places} {ra} -> {rb}")
                    continue
                c.label = "cut"
                if (c.site, xa) not in counted:
                    counted.add((c.site, xa))
                    budget += abs(c.d_result)
            else:
                oa, ob = _value(xa), _value(xb)
                if oa is None or ob is None or not (oa.is_finite() and ob.is_finite()):
                    c.label, c.why = "unexplained", f"its operand {xa} -> {xb} is not a finite number"
                    continue
                c.d_operand, c.bound = ob - oa, budget
                if abs(c.d_operand) > budget + _noise(oa, ob):
                    c.label = "unexplained"
                    c.why = (f"carried: its operand moved {_signed(c.d_operand)}, past the {budget} its "
                             f"process's {len(counted)} earlier cut(s) moved")
                    continue
                c.label = "carried"
                c.holds = _holds(c, earlier)
            earlier.append(c)


def _signed(d: Decimal) -> str:
    return f"{d:+f}"


def _holds(c: Call, earlier: list):
    """Earlier changed calls whose results' moves, added or taken away,
    make the carried operand's move (what it could hold: the log does not
    say what it summed): cuts before carried calls, those of its own
    context first, the fewest, the latest; None when none do (an average,
    a product: it is still within the bound)."""
    def search(cands):
        cands = cands[-64:][::-1]
        places = max([p for p in (x.places for x in cands) if isinstance(p, int)] or [0])
        unit = Decimal(1).scaleb(-places)
        target = (c.d_operand / unit).to_integral_value()
        if abs(c.d_operand - target * unit) > _noise(_value(c.base[0]), _value(c.new[0])) or not target:
            return None
        best = {0: ()}
        for i, x in enumerate(cands):
            d = x.d_result / unit
            if d != d.to_integral_value():
                continue
            d = int(d)
            for s, combo in list(best.items()):
                for sign in (1, -1):
                    t = s + sign * d
                    if t not in best or len(best[t]) > len(combo) + 1:
                        best[t] = combo + ((sign, i),)
        found = best.get(int(target))
        return [(sign, cands[i]) for sign, i in found] if found else None
    cuts = [x for x in earlier if x.label == "cut"]
    return (search([x for x in cuts if x.ctx == c.ctx]) or search(cuts)
            or search([x for x in earlier if x.ctx == c.ctx]) or search(earlier))


# ---------------------------------------------------------------- explaining
def _pool(record: dict, output: str):
    """What may explain an output's moved numbers: a predicate on a call
    (a stream: the step's processes; a file: the processes of the contexts
    that wrote it, and their children), and its name; False: no figure
    (what the goldens record of the views themselves, a removed file)."""
    if output in ("_run/stdout.txt", "_run/stderr.txt"):
        return (lambda c: c.step == RUN), f"the {RUN} step's processes"
    if output.startswith("_run/"):
        return False
    if output.startswith(f"{_golden.VIEWS}files/") or not output.startswith(_golden.VIEWS):
        wrote = [tuple(w) for w in (record.get("writers") or {}).get(output, ())]
        procs = {(step, process_of(ctx)) for step, ctx in wrote}

        def ok(c):
            return c.process in procs or any(c.step == s and c.ctx.startswith(x + "|") for s, x in wrote)
        named = ", ".join(sorted({f"{s}: {p}" for s, p in procs})) or "none the log saw"
        return ok, f"the process(es) that wrote it ({named})"
    for v in record["views"]:
        if output in (f"{_golden.VIEWS}{v['name']}{s}" for s in _STREAMS):
            return (lambda c, step=v["label"]: c.step == step), f"the view {v['label']}'s processes"
    return False


def _printed(result: str, places: int, comma: bool) -> str:
    """A logged result as a fixed-point format prints it."""
    v = _as_logged(result)
    return "" if v is None else format(v, f"{',' if comma else ''}.{places}f")


def _token_ok(result: str, text: str, end: int, token: str, places: int, commas) -> bool:
    """Whether `text` printed `result` as the token ending at `end`: at
    `places` (BASE's token's), with the commas given (None: either), its
    sign as the text reads it (a minus before the token is the result's)."""
    for comma in ((True, False) if commas is None else (commas,)):
        p = _printed(result, places, comma)
        if p and p.lstrip("-") == token and text[:end].endswith(p) \
                and (p.startswith("-") or not text[:end - len(p)].endswith("-")):
            return True
    return False


def _needs(before, after):
    """(why it cannot be explained, None) or (None, [(test, a, b)]): each
    moved number of one difference, with a test of a call that printed it
    (BASE's result as BASE's text, NEW's as NEW's) and its values."""
    a, b = _numbers(before), _numbers(after)
    if a is None or b is None or a[0] != b[0] or len(a[1]) != len(b[1]):
        return "not the same text around its numbers", None
    text = a[2]
    out = []
    ta = list(_NUMBER.finditer(before)) if text else None
    tb = list(_NUMBER.finditer(after)) if text else None
    for i, (x, y) in enumerate(zip(a[1], b[1])):
        if x == y:
            continue
        if not (x.is_finite() and y.is_finite()):
            return "not a finite number", None
        if not text:
            if x and y and (x < 0) != (y < 0):
                return "its sign changed", None

            # No more places than the call cut to: a JSON writer drops
            # trailing zeros (2.5 from a 2-place cut), never adds one.
            shows = max(_places(x), _places(y))

            def test(c, x=x, y=y, shows=shows):
                return (isinstance(c.places, int) and shows <= c.places
                        and _value(c.base[1]) == x and _value(c.new[1]) == y)
        else:
            ma, mb = ta[i], tb[i]
            if before[:ma.start()].endswith("-") != after[:mb.start()].endswith("-"):
                return "its sign changed", None
            # NEW's places and commas as BASE wrote them; a BASE number
            # under 1,000 shows no commas either way.
            places = len(ma[0].split(".", 1)[1]) if "." in ma[0] else 0
            commas = True if "," in ma[0] else (None if x < 1000 else False)

            def test(c, ma=ma, mb=mb, places=places, commas=commas):
                return (c.places == places
                        and _token_ok(c.base[1], before, ma.end(), ma[0], places, "," in ma[0])
                        and _token_ok(c.new[1], after, mb.end(), mb[0], places, commas))
        out.append((test, x, y))
    if not out:
        return "no number moved", None
    return None, out


def _match(needs, changed) -> dict:
    """{need: call} of a maximum matching: each moved number its own call
    among those that printed it (augmenting paths, needs in order)."""
    cands = [[j for j, c in enumerate(changed) if pool(c) and test(c)] for pool, test in needs]
    owner, held = {}, {}            # call: need, need: call
    for i in range(len(needs)):
        via, queue, free = {}, deque([i]), None
        while queue and free is None:
            n = queue.popleft()
            for j in cands[n]:
                if j in via:
                    continue
                via[j] = n
                if j not in owner:
                    free = j
                    break
                queue.append(owner[j])
        while free is not None:     # each need on the path takes the next call
            n = via[free]
            back = held.get(n)
            owner[free], held[n] = n, free
            free = back
    return {n: changed[j] for n, j in held.items()}


def compare(base: dict, new: dict, pair: bool = True, rule: bool = False) -> dict:
    """{book: {"outputs": n compared, "rows": [(label, output+path, what
    moved, why, the calls that explain it)], "calls": [Call] (None when
    they were not paired: `pair` False, `diff --strict`, and they do not
    pair)}}."""
    out = {}
    for book in sorted(set(base["books"]) | set(new["books"])):
        if book not in base["books"] or book not in new["books"]:
            out[book] = {"outputs": 0, "calls": [], "rows": [("unexplained", "(book)", "",
                         f"in {'NEW' if book in new['books'] else 'BASE'} only", ())]}
            continue
        b, n = base["books"][book], new["books"][book]
        unpaired = None
        try:
            calls = paired(book, b, n, rule)
        except Refused as e:
            if pair:
                raise
            calls, unpaired = None, str(e)
        changed = [c for c in (calls or []) if c.label in ("cut", "carried")]
        found = []                  # [output, path, x, y, why, needs]
        for output, path, x, y in differences(b["outputs"], n["outputs"]):
            if path is None:
                found.append([output, None, x, y, "the text differs, its values do not", None])
                continue
            pool = _pool(n, output)
            if pool is False or x is _GONE or y is _GONE or isinstance(x, Shape):
                why, needs = "not a figure", None
            elif isinstance(x, Reordered):
                why, needs = "reordered: the same items in another order", None
            elif calls is None:
                why, needs = f"not explained ({unpaired})", None
            else:
                why, needs = _needs(x, y)
                needs = [(pool[0], t, xa, ya) for t, xa, ya in needs] if needs else None
            found.append([output, path, x, y, why or pool[1], needs])
        flat = [(i, k) for i, f in enumerate(found) if f[5] for k in range(len(f[5]))]
        matched = _match([(found[i][5][k][0], found[i][5][k][1]) for i, k in flat], changed)
        by_row = defaultdict(dict)
        for m, c in matched.items():
            i, k = flat[m]
            by_row[i][k] = c
        rows, moves = [], defaultdict(set)
        for i, (output, path, x, y, why, needs) in enumerate(found):
            if path is None:
                rows.append(("bytes", output, f"{x} -> {y}", why, ()))
                continue
            where, whole = f"{output}{path}", f"{_gist(x)} -> {_gist(y)}"
            if not needs:
                rows.append(("unexplained", where, whole, why, ()))
                continue
            moved = _moved_text(x, y)
            got = by_row.get(i, {})
            if len(got) < len(needs):
                miss = [k for k in range(len(needs)) if k not in got]
                k = miss[0]
                printed = [c for c in changed if needs[k][0](c) and needs[k][1](c)]
                rows.append(("unexplained", where, moved,
                             f"{len(miss)} of {len(needs)} moved number(s) explained by no changed call: "
                             + (f"each that printed {needs[k][2]} -> {needs[k][3]} explains another number"
                                if printed else f"none of {why} printed {needs[k][2]} -> {needs[k][3]}"), ()))
                continue
            used = [got[k] for k in range(len(needs))]
            lab = "carried" if any(c.label == "carried" for c in used) else "cut"
            rows.append((lab, where, moved, "; ".join(f"{c.label} {c.site} {c.new[0]}" for c in used), tuple(used)))
            moves[output] |= {(abs(xa), abs(ya)) for _t, _u, xa, ya in needs}
        # A byte change beside the explained moves (TJ-3367, TJ-3378, TJ-3379).
        by_output = defaultdict(list)
        for r, f in zip(rows, found):
            by_output[f[0]].append(r)
        rows = []
        for output, mine in by_output.items():
            if mine and all(r[0] in ("cut", "carried") for r in mine):
                beside = _beside(b["outputs"][output], n["outputs"][output], moves[output])
                if beside:
                    mine = mine + [("bytes", output, beside, "a byte change beside its explained moves", ())]
            rows += mine
        out[book] = {"outputs": len(set(b["outputs"]) & set(n["outputs"])), "rows": rows, "calls": calls,
                     "unpaired": unpaired}
    return out


def _moved_text(before, after) -> str:
    a, b = _numbers(before), _numbers(after)
    moved = []
    for i, (x, y) in enumerate(zip(a[1], b[1])):
        if x != y:
            moved.append(f"{_shown(x, a[0][i], a[2])} -> {_shown(y, b[0][i], a[2])}")
    return "; ".join(moved)


def _gist(v) -> str:
    text = v if isinstance(v, str) else json.dumps(v)
    return text if len(text) <= 90 else text[:87] + "..."


def _spelled_like(a: str, b: str) -> bool:
    """Whether NEW's token `b` is spelled as BASE's `a` spells a number:
    each its float's shortest repr (a JSON writer's), or `b` at `a`'s
    places with `a`'s commas (a number under 1,000 shows none either way)."""
    try:
        x, y = Decimal(a.replace(",", "")), Decimal(b.replace(",", ""))
    except InvalidOperation:
        return False
    if "," not in a and "," not in b and repr(float(x)) == a and repr(float(y)) == b:
        return True
    places = len(a.split(".", 1)[1]) if "." in a else 0
    commas = (True,) if "," in a else (True, False) if x < 1000 else (False,)
    return any(format(y, f"{',' if c else ''}.{places}f") == b for c in commas)


def _realigned(x: str, y: str, before: int, after: int) -> bool:
    """Whether text `x` became `y` only in the spaces a moved number's
    change of width realigns: the spaces after a number `before` wider
    (a column it leads), or before one `after` wider (a column it ends)."""
    def split(s):
        lead = len(s) - len(s.lstrip(" "))
        if lead == len(s):
            return lead, "", 0
        trail = len(s) - len(s.rstrip(" "))
        return lead, s[lead:len(s) - trail], trail
    (la, ca, ta), (lb, cb, tb) = split(x), split(y)
    if ca != cb:
        return False
    if not ca:                                      # spaces only, between two numbers
        return len(y) - len(x) in {-before, -after, -(before + after)}
    return lb - la in {0, -before} and tb - ta in {0, -after}


def _beside(a: str, b: str, moves: set):
    """The first change in NEW's text that its explained moves do not make
    (TJ-3367), or None: BASE's text, each moved number written as NEW
    writes it, must be NEW's text, each moved number spelled as BASE spelled
    it (TJ-3378), but for the spaces beside a moved number that its change
    of width realigns (TJ-3379). A number in other digits but the same
    value (5 -> 5.0), float noise, a sign (0.0 -> -0.0), a move under the
    goldens' tolerance, a separator removed or a tab for spaces is such a
    change."""
    pa, pb = _NUMBER.split(a), _NUMBER.split(b)
    if len(pa) != len(pb):
        return f"{(len(pa) - 1) // 2} numbers -> {(len(pb) - 1) // 2}"

    def moved(i):
        return 0 <= i < len(pa) and pa[i] != pb[i] and \
            (Decimal(pa[i].replace(",", "")), Decimal(pb[i].replace(",", ""))) in moves \
            and _spelled_like(pa[i], pb[i])
    width = [len(pb[i]) - len(pa[i]) if i % 2 and moved(i) else 0 for i in range(len(pa))]
    for i, (x, y) in enumerate(zip(pa, pb)):
        if x == y or (i % 2 and moved(i)):
            continue
        if not i % 2 and (moved(i - 1) or moved(i + 1)) \
                and _realigned(x, y, width[i - 1] if i else 0, width[i + 1] if i + 1 < len(pa) else 0):
            continue
        context = "".join(pa[max(0, i - 2):i])[-30:]
        return f"...{context}{x[:40]!r} -> {y[:40]!r}"
    return None


def _print_rows(rows, show: int) -> None:
    for lab, where, moved, why, _calls in rows[:show]:
        print(f"  {lab:<11} {where}  {moved}  ({why})")
    if len(rows) > show:
        print(f"  ... and {len(rows) - show} more")


def _totals(result: dict) -> Counter:
    c = Counter()
    for r in result.values():
        c["outputs"] += r["outputs"]
        c["differences"] += len(r["rows"])
        c.update(row[0] for row in r["rows"])
    return c


def file_rows(base: dict, new: dict) -> tuple:
    """(digests compared, [(the step, the file, what differs)]) of one
    book's files in two dumps: each file under the project after the run
    and after each view (a file in one dump only, or with other bytes),
    named once at the step its difference starts; a step in one dump only
    is a row of its own."""
    a, b = base.get("files") or [], new.get("files") or []
    rows, n = [], 0
    if [s[0] for s in a] != [s[0] for s in b]:
        rows.append(("(steps)", "", f"{len(a)} step(s) against {len(b)}: "
                     f"{next((x[0], y[0]) for x, y in zip(a + [['-']], b + [['-']]) if x[0] != y[0])}"))
    sa, sb, was = {}, {}, {}
    for (label, da), (_label, db) in zip(a, b):
        for state, delta in ((sa, da), (sb, db)):
            for rel, digest in delta.items():
                if digest is None:
                    state.pop(rel, None)
                else:
                    state[rel] = digest
        n += len(set(da) | set(db))
        now = {rel: (sa.get(rel), sb.get(rel)) for rel in set(sa) | set(sb) if sa.get(rel) != sb.get(rel)}
        for rel, (x, y) in sorted(now.items()):
            if was.get(rel) != (x, y):
                rows.append((label, rel, "in NEW only" if x is None else "in BASE only" if y is None
                             else "other bytes"))
        was = now
    return n, rows



def diff(a) -> int:
    base, new = load(a.base), load(a.new)
    _same_kind(base, new, "BASE and NEW")
    result = compare(base, new, pair=not a.strict)
    files, digests = [], 0
    for book in sorted(set(base["books"]) & set(new["books"])):
        n, rows = file_rows(base["books"][book], new["books"][book])
        digests += n
        files += [(book, *r) for r in rows]
    for book, r in result.items():
        rows = r["rows"]
        if r.get("unpaired"):
            print(f"{book}: not paired: {r['unpaired']}")
        if not rows:
            print(f"{book}: {r['outputs']} outputs, same")
            continue
        counts = Counter(row[0] for row in rows)
        print(f"{book}: {r['outputs']} outputs, {len(rows)} difference(s) ("
              + ", ".join(f"{lab} {counts[lab]}" for lab in LABELS) + ")")
        _print_rows(rows, a.show)
    t = _totals(result)
    print(f"views A/B: {len(result)} book(s), {t['outputs']} outputs compared; "
          f"differences {t['differences']}: " + ", ".join(f"{lab} {t[lab]}" for lab in LABELS))
    if a.strict:
        for book, step, rel, what in files[:a.show * 5]:
            print(f"  file        {book}: {rel}  after {step}: {what}")
        if len(files) > a.show * 5:
            print(f"  ... and {len(files) - a.show * 5} more")
        print(f"files: {digests} digest(s) compared after the run and each view; {len(files)} differ")
    if a.strict and not (t["outputs"] and digests):
        print("--strict: nothing was compared", file=sys.stderr)
        return 1
    if a.strict:
        return 1 if t["differences"] or files else 0
    return 1 if any(t[lab] for lab in _FAILS) else 0


# ---------------------------------------------------------------- ties
def _deep_index(record: dict):
    """A --deep dump's book: its calls by key, and their operands by step,
    site and places."""
    flat = _flat(record)
    by_site = defaultdict(set)
    for (step, _ctx, site, _n), (x, places, _r, _q) in flat.items():
        by_site[(step, site, places)].add(x)
    return flat, by_site


def _tie(c: Call, deep) -> str:
    """A cut's operand at 10 places: "same at 10 places" (its own call
    there, or another of its step and site, has the operand), a file tie
    (the nearest operand there within a unit, not a half: named) or "not
    found at 10 places"."""
    flat, by_site = deep
    x = c.new[0]
    own = flat.get((c.step, c.ctx, c.site, c.n))
    near = by_site.get((c.step, c.site, c.places), set())
    if (own is not None and own[0] == x) or x in near:
        return "same at 10 places"
    xd, unit = Decimal(x), Decimal(1).scaleb(-c.places)
    found = sorted((d for d in (_value(r) for r in ([own[0]] if own else []) + sorted(near))
                    if d is not None and d.is_finite() and abs(d - xd) < unit), key=lambda d: (abs(d - xd), d))
    if found and not exact_half(str(found[0]), c.places):
        return f"file tie (10 places: {found[0]})"
    return "not found at 10 places"


def _fmt_rows(record: dict) -> dict:
    """{(site, expression): [the format log's records]} of one book: a
    line that formats two values is two sites."""
    sites = defaultdict(list)
    for r in record["log"]:
        if r["kind"] == "fmt":
            sites[(r["site"], r["expr"])].append(r)
    return sites


def _fmt_order(kv):
    (site, expr), recs = kv
    return (_fmt_class(kv[0], recs) == "not money", not recs[0]["money"], site, expr)


def _fmt_class(key, recs) -> str:
    """money (by its names), other, or not money: a site listed as reviewed
    (tests/_fmt_log.py NOT_MONEY)."""
    site, expr = key
    if (site.rsplit(":", 1)[0], expr) in _fmt_log.NOT_MONEY:
        return "not money"
    return "money" if recs[0]["money"] else "other"


def _fmt_moves(key, recs) -> bool:
    """A tie a half-up cut prints otherwise than its format did, at a site
    not listed as reviewed not money: money whatever its names (TJ-3368)."""
    return _fmt_class(key, recs) != "not money" and any(r["moves"] for r in recs)


def _fmt_line(key, recs: list) -> str:
    site, expr = key
    r = next((x for x in recs if x["moves"]), recs[0])
    views = sorted({x["view"] for x in recs})
    reprs = sorted({f"{x['repr']} @{x['places']}" for x in recs})
    kind = _fmt_class(key, recs)
    why = _fmt_log.NOT_MONEY.get((site.rsplit(":", 1)[0], expr))
    return (f"  {kind:<9} {site}  {r['how']} {expr}  "
            f"{', '.join(reprs[:3])}{', ...' if len(reprs) > 3 else ''}  printed {r['printed']}, "
            f"half up {r['half_up']}{'' if r['moves'] else ' (the same)'}  "
            f"{len(recs)} print(s) in {', '.join(views[:3])}{', ...' if len(views) > 3 else ''}"
            + (f"  [{why}]" if why else ""))


def _sinks(doc: dict, what: str) -> None:
    """Refused unless every book of a --fmt-log dump ran with the format
    log's sink (TJ-3364: an idle sink lists no tie)."""
    if not doc.get("fmt_log"):
        raise Refused(f"{what}: dumped without --fmt-log")
    idle = sorted(b for b, r in doc["books"].items()
                  if not (r.get("shim") or {}).get("fmt_sink")
                  or r["shim"]["fmt_sink"] != r["shim"].get("processes"))
    if idle:
        raise Refused(f"{what}: no format sink ran in {', '.join(idle)}: dump it again with --fmt-log")


def _holds_text(c: Call) -> str:
    if not c.holds:
        return f"no sum of its process's changes; within the bound {c.bound}"
    return ", ".join(f"{'+' if s > 0 else '-'}{x.site} {x.new[0]} ({x.base[1]} -> {x.new[1]})"
                     for s, x in c.holds)


def ties(a) -> int:
    base, new = load(a.base), load(a.new)
    _same_kind(base, new, "BASE and NEW")
    deep = load(a.deep) if a.deep else None
    if deep is not None:
        for key, ok in (("src", deep["src"] == new["src"]), ("rates", deep["rates"] == new["rates"]),
                        ("places", deep["places"] > new["places"]), ("python", deep["python"] == new["python"])):
            if not ok:
                raise Refused(f"--deep {a.deep}: {key} {deep[key]} against NEW's {new[key]}: dump NEW's tree "
                              f"at NEW's rates and Python with --places 10")
        missing = sorted(set(new["books"]) - set(deep["books"]))
        if missing:
            raise Refused(f"--deep {a.deep}: no {', '.join(missing)}: dump every book NEW holds")
    if new.get("fmt_log"):
        _sinks(new, "NEW")
    result = compare(base, new, rule=True)
    n = Counter()
    books_with, sites = 0, set()
    fmt_sites, fmt_moves = set(), set()
    for book in sorted(new["books"]):
        record = new["books"][book]
        r = result.get(book, {})
        calls, rows = r.get("calls") or [], r.get("rows", [])
        by = defaultdict(list)
        for c in calls:
            by[c.label].append(c)
        n.update({lab: len(by[lab]) for lab in CALL_LABELS})
        n["moved"] += sum(1 for c in calls if c.base[1] != c.new[1])
        n["would"] += sum(1 for x, places, _r, _q in _flat(base["books"].get(book, {})).values()
                          if _would_move(x, places))
        n["off-half"] += sum(1 for c in by["unexplained"] if c.why.startswith("a cut not at"))
        n["rule"] += sum(1 for c in by["unexplained"] if c.why.startswith("the flip's rule"))
        used = {id(c) for row in rows for c in row[4]}
        idle = [c for c in by["cut"] + by["carried"] if id(c) not in used]
        n["unprinted"] += len(idle)
        fmts = _fmt_rows(record)
        if not (by["cut"] or by["carried"] or by["unexplained"] or rows or fmts):
            continue
        books_with += bool(by["cut"])
        print(f"== {book}")
        print(f"calls: {len(calls)} paired: " + ", ".join(f"{lab} {len(by[lab])}" for lab in CALL_LABELS))
        deep_index = _deep_index(deep["books"][book]) if deep is not None else None
        if by["cut"]:
            print("cuts:")
            groups = defaultdict(list)
            for c in by["cut"]:
                groups[(c.site, c.base[0], c.places, c.base[1], c.new[1])].append(c)
            for (site, repr_, places, ra, rb), group in sorted(groups.items()):
                sites.add(site)
                tie_of = Counter("-" if deep_index is None else _tie(c, deep_index) for c in group)
                for tie, k in tie_of.items():
                    n[tie.split(" (")[0]] += k
                steps = sorted({c.step for c in group})
                named = ", ".join(steps[:3]) + (", ..." if len(steps) > 3 else "")
                print(f"  {site}  {repr_} @{places}  {ra} -> {rb}  exact half  "
                      f"{'; '.join(f'{t} x{k}' if len(tie_of) > 1 else t for t, k in sorted(tie_of.items()))}  "
                      f"{len(group)} call(s) in {len(steps)} step(s): {named}")
        if by["carried"]:
            print("carried (each with the cuts it could hold):")
            groups = defaultdict(list)
            for c in by["carried"]:
                groups[(c.site, c.base[0], c.new[0], c.places, c.base[1], c.new[1], _holds_text(c))].append(c)
            for (site, xa, xb, places, ra, rb, holds), group in groups.items():
                steps = sorted({c.step for c in group})
                made = "  a tie the flip made" if exact_half(xb, places) and not exact_half(xa, places) else ""
                print(f"  {site}  {xa} -> {xb} @{places} ({_signed(group[0].d_operand)})  {ra} -> {rb}{made}  "
                      f"{len(group)} call(s) in {', '.join(steps[:3])}{', ...' if len(steps) > 3 else ''}  "
                      f"could hold: {holds}")
        if by["unexplained"]:
            print("unexplained calls:")
            for c in by["unexplained"]:
                print(f"  {c.where()}  {c.why}")
        if rows:
            print("differences:")
            _print_rows(rows, len(rows))
        if idle:
            print(f"changed, behind no moved number: {len(idle)} call(s)")
            for c in idle[:5]:
                print(f"  {c.label:<7} {c.where()}  {c.base[0]} -> {c.new[0]} @{c.places}  {c.base[1]} -> {c.new[1]}")
            if len(idle) > 5:
                print(f"  ... and {len(idle) - 5} more")
        if fmts:
            print("format ties (a print the flip cannot move):")
            for key, recs in sorted(fmts.items(), key=_fmt_order):
                print(_fmt_line(key, recs))
                fmt_sites.add(key)
                if _fmt_moves(key, recs):
                    fmt_moves.add(key)
    t = _totals(result)
    still = n["would"] and not n["moved"]
    if still:
        print(f"no call changed where BASE has {n['would']} at a half a half-up cut moves: "
              f"is NEW's tree the flipped one?", file=sys.stderr)
    silent = n["moved"] and not t["differences"]
    if silent:
        print("calls changed and nothing moved: does NEW print them?", file=sys.stderr)
    print(f"ties: {n['cut']} cut call(s) at {len(sites)} site(s) in {books_with} book(s); exact halves "
          f"{n['cut']}/{n['cut'] + n['off-half']}; carried {n['carried']}; unexplained calls {n['unexplained']} "
          f"({n['rule']} against the flip's rule); "
          + (f"same at 10 places {n['same at 10 places']}, file ties {n['file tie']}, "
             f"not found at 10 places {n['not found at 10 places']}; " if deep is not None
             else "10 places not checked (no --deep); ")
          + f"differences {t['differences']} (cut {t['cut']}, carried {t['carried']}, bytes {t['bytes']}), "
            f"unexplained {t['unexplained']}; changed calls behind no moved number {n['unprinted']}"
          + (f"; format ties {len(fmt_sites)} site(s), {len(fmt_moves)} a half-up cut prints otherwise"
             if new.get("fmt_log") else ""))
    unlogged = (new.get("fmt_log") or {}).get("failed")
    if unlogged:
        print(f"the format log could not rewrite {', '.join(unlogged)}", file=sys.stderr)
    fails = (any(t[lab] for lab in _FAILS) or n["unexplained"] or still or silent or fmt_moves or unlogged)
    return 1 if fails else 0


def fmt(a) -> int:
    doc = load(a.dump)
    _sinks(doc, a.dump)
    sites = defaultdict(list)
    for record in doc["books"].values():
        for key, recs in _fmt_rows(record).items():
            sites[key] += recs
    kinds, moves = Counter(), 0
    for key, recs in sorted(sites.items(), key=_fmt_order):
        print(_fmt_line(key, recs) + f"  ({', '.join(sorted({r['book'] for r in recs}))})")
        kinds[_fmt_class(key, recs)] += 1
        moves += _fmt_moves(key, recs)
    stats = doc["fmt_log"]
    print(f"format ties: {len(sites)} site(s) ({kinds['money']} money by name, {kinds['other']} other, "
          f"{kinds['not money']} reviewed not money); {moves} a half-up cut prints otherwise; "
          f"{sum(len(r) for r in sites.values())} print(s); the format log rewrote {stats['sites']} "
          f"format(s) in {stats['modules']} module(s)"
          + (f", not {len(stats['failed'])} module(s)" if stats["failed"] else ""))
    return 1 if moves or stats["failed"] else 0


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump", help="the project books' outputs and shown calls from one tree")
    d.add_argument("out")
    d.add_argument("--src", metavar="DIR", help="the tree (or its src) whose taxjson runs")
    d.add_argument("--rates", choices=sorted(_hermetic.RATE_SETS), default="default")
    d.add_argument("--places", type=int, default=GAINS_PLACES, metavar="N",
                   help="the 4-place writers' money places: gains files, cost timelines (default 4)")
    d.add_argument("--fmt-log", action="store_true", help="run a format-log copy (tests/_fmt_log.py)")
    d.add_argument("--books", nargs="+", metavar="NAME", help="these books only")
    c = sub.add_parser("diff", help="two dumps compared, each difference explained by the calls")
    c.add_argument("base")
    c.add_argument("new")
    c.add_argument("--strict", action="store_true", help="fail on any difference, byte for byte")
    c.add_argument("--show", type=int, default=20, metavar="N", help="differences shown per book")
    t = sub.add_parser("ties", help="diff's labels and each changed call checked")
    t.add_argument("base")
    t.add_argument("new")
    t.add_argument("--deep", metavar="NEW10", help="NEW's tree dumped with --places 10")
    f = sub.add_parser("fmt", help="a --fmt-log dump's formats that printed an exact half")
    f.add_argument("dump")
    a = ap.parse_args(argv)
    try:
        return {"dump": dump, "diff": diff, "ties": ties, "fmt": fmt}[a.cmd](a)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
