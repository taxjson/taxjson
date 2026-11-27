"""The freshness-edge audit (TJ-2324; the owner's decision (d) of 2026-11-08).

Every stage's record names what it was built from: the inputs its
`needs_rebuild(out, *inputs)` declares (src/taxjson/pipeline/freshness.py).
A run builds every stage, and a view its full-history files, every time
(`tjs run` always rebuilds everything, `--fast` is gone; TJ-4307) and records
each; the sweep reads the records, and only the fetched rates are rebuilt
when an input their record names has changed. A file a stage reads that the
declaration does not name is the failure left: the record says less than the
stage read.

This runs every project golden book (tests/golden, `kind: project`) and
VARIANTS (a book with an edit made before it runs: a missing-history line or
an ALLOWLOSS line, which no book has), each in full, then the commands that
build on demand after a run (BUILD_ON_DEMAND), then those commands again,
WORKERS at a time, with every file open traced:
tests/_opentrace/sitecustomize.py, on PYTHONPATH, so no code in src/ changes.
It judges each build:

- a build is the span from a `needs_rebuild` that answered True to its output
  written (renamed into place, or opened for writing); an ask no write follows
  is a query (the run asking whether a parse is current), not a build;
- a read inside a build counts when it is under the project or the HOME (the
  synthetic one, tests/_hermetic); it is no edge when the freshness module
  itself made it (its records, lib/records'), when it is the output, a
  declared input or a file the build wrote first, when it is the run's
  taxjson.toml (every record holds it as `config`), or when ALLOWED names it;
- every other read is an undeclared edge, and the test names each.

What it does not judge: reads between builds, the run's own (its diagnostics,
the checklist, the reports, and the files it derives a declared input from,
as the crypto fill's ticker.map stamp). A read there that feeds a build in
memory is the method's blind spot (TJ-2498).

It also holds that the books reach every `needs_rebuild` call site in the
stages and the CLI (the stage table's executor's: a new one no book reaches
fails, so it is judged), that the executor ran every row of the stage table
(pipeline/stage_table) in these runs, and that the on-demand commands run
again with nothing changed build every file they read again, each once (no
record's word serves a view: TJ-4307). The no-change run test (a no-change `--fast` built nothing and wrote only a
listed few files) went with `--fast`: every run builds every stage.
"""
import ast
import fnmatch
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import _golden          # the synthetic HOME (tests/_hermetic) and the books
import _style
from taxjson.lib.dispatch import python_module_argv
from taxjson.parsers.registry import rate_reading_brokers
from taxjson.pipeline import stage_table
import _paths

TESTS = _paths.TESTS
SRC = TESTS.parent / "src" / "taxjson"
TRACER = TESTS / "_opentrace"
BOOKS = tuple(b for b in _golden.books() if _golden.meta(b)["kind"] == "project")
# {name: (book, [(file, text appended)])}: a book with an edit made before it
# runs. No golden book has an `OPENING ... cost=unknown` line, so the gains
# stages' reads of the lines were never judged (TJ-4128); nor an ALLOWLOSS
# line, so the gains' reads of the loss overrides were not (TJ-4181).
VARIANTS = {"style-canada+opening": (
    "style-canada", [("inputs/margin/margin_extra.tt",
                      "OPENING 2023-12-29 GHOSTQ.TO 40 cost=unknown\n")]),
            "style-canada+allowloss": (
    "style-canada", [("inputs/margin/margin_extra.tt",
                      'ALLOWLOSS 2024-11-01 SAMPA.TO reason="synthetic"\n')])}
WORKERS = 5
# After the run, the commands whose files are built on demand, so their
# call sites are judged too (cli/common.py's full-history gains).
BUILD_ON_DEMAND = {"demo": (["stats", "--all-history"],)}

# (file glob, reader, out glob, reason): the file relative to the project
# ("~/" for the HOME), the reader the innermost taxjson frame's
# `module:function` (or `reader <- its caller`); None matches any.
ALLOWED = (
    (None, "pipeline/inputs.py:_inputs_fingerprint <- pipeline/inputs.py:_parse_reads",
     "*work/*.json",
     "a parse's in-run reuse key (`_parse_reads`: its command and the account "
     "folder's files, hashed so the second pass takes the first's parse): "
     "not an input across runs"),
    ("~/.currency_price_cache.json", "lib/rate_cache.py:load_cache", "*work/to_base.csv",
     "the rates stage's grounds are its coverage and window "
     "(`_rates_coverage_stale`), its rebuild recorded with `mark_built`"),
    ("*ticker.map", "lib/cli_diag.py:read_text_utf8", "*work/*_filled.json",
     "fill-crypto reads the map's CRYPTO lines, declared through their stamp "
     "`<account>_crypto_ticker_map.state` (re-audit A2-0585)"),
    ("*work/*.json", "bin/taxjson_corp_actions.py:_spellings_from", "*work/*_corp.json",
     "the parse and corporate-action outputs the run checked (`--spellings-from`), "
     "read only to refuse two spellings of a symbol no ticker.map line decides "
     "(backlog TJ-4616): a refusal exits 2 and writes no output; the output "
     "written never holds anything of theirs"),
)

# (file glob, reader, out glob, stamp glob, reason): a read whose lines a stamp
# declares (a work/ file holding what the build takes from the file, rewritten
# only when that changes). Excused only in a build whose ask names a file
# matching the stamp glob: a stamp dropped from the declaration is an edge again.
STAMPED = (
    ("*.tt", "lib/cli_diag.py:read_text_utf8 <- lib/missing_history.py:read_tt_openings",
     "*work/*gains*.json", "*work/missing_history.state",
     "a gains run given --incomplete-history reads the .tt OPENING cost=unknown lines "
     "(`_missing_history_deps`, TJ-4128): no other line of the file is read, only "
     "these lines and their places"),
    ("*.tt", "lib/cli_diag.py:read_text_utf8 <- lib/missing_history.py:read_tt_openings",
     "*reports/wash_radar_*.rpt", "*work/missing_history.state",
     "the wash radar given --incomplete-history reads the same lines (TJ-4261)"),
    ("*.tt", "lib/cli_diag.py:read_text_utf8 <- lib/missing_history.py:read_tt_openings",
     "*work/*_base.json", "*work/missing_history.state",
     "the equity merge given --incomplete-history holds the account's lines as rows "
     "while it books the dated renames and applies the opening cut-off "
     "(`hold_declared_openings`, TJ-5007 / TJ-5042): only these lines are read"),
    ("*.tt", "lib/cli_diag.py:read_text_utf8 <- lib/missing_history.py:read_tt_openings",
     "*work/*_raw.json", "*work/missing_history.state",
     "the native books' merge holds the same lines the same way (TJ-5007 / TJ-5042)"),
    ("*.tt", "lib/cli_diag.py:read_text_utf8 <- lib/missing_history.py:read_tt_openings",
     "*work/*_mapped.json", "*work/missing_history.state",
     "a crypto account's map stage holds the same lines while it books the dated "
     "renames (TJ-5042)"),
    ("*.tt", "lib/cli_diag.py:read_text_utf8 <- lib/dated_events.py:read_declarations",
     "*work/*_ib.json", "*work/*_ib_tt.state",
     "the IB parse reads the project's .tt RENAME lines (`_ib_tt_state`, TJ-4129)"),
    ("*.tt", "lib/brokerages/ib_extractor.py:_account_tt_links", "*work/*_ib.json",
     "*work/*_ib_tt.state",
     "the IB parse reads its account's .tt SPLIT rows that move a position to another "
     "symbol (TJ-4129)"),
)

# (file glob, reader, reason): a project file the run reads that is no input of
# the books, so the input fingerprint (lib/checklist `input_fingerprint`) need
# not hash it: `test_every_project_file_the_run_reads_is_fingerprinted`. The
# glob is relative to the project's top folder; the reader is the innermost
# taxjson frame's `module:function`, and a file is excused only when every
# read of it is by a reader named here.
NOT_BOOK_INPUTS = (
    ("*", "lib/rates_window.py:earliest_in_files",
     "the rates' window (pipeline/stages `_rates_inputs`: every file of an "
     "account's folder, the slips and the positions snapshots) reads only the "
     "earliest date written in each, so the fetched rates reach back that far; "
     "the books take a rate by each row's own date"),
    ("holdings/*", "lib/positions_reports.py:_decode",
     "a broker's positions report saved in holdings/ (rbc-options' Holdings "
     "Exports): no book figure comes from it. The run reads it to tell it is one "
     "(lib/holdings_dir `report_kind`) and for the closing summary's accounts "
     "with no holdings to check them against (lib/first_run `unchecked_accounts`), "
     "which reports/run_summary.json keeps: unhashed, that field goes stale when "
     "holdings/ changes until the next run (backlog TJ-5386)"),
)


def _record_names(run_root: Path, path: str) -> set:
    """What the input fingerprint may name `path` (a real path): by its
    path from the folder the run ran in, and a file of a shared inputs/
    folder beside a year's folder (one level up: the demo's) by
    "inputs/..." too, as the record keys it (docs/settings.md,
    inputs_dir)."""
    rel = Path(os.path.relpath(path, os.path.realpath(run_root))).as_posix()
    return {rel, rel.removeprefix("../")}


def _empty_manifest(path: str) -> bool:
    """The elections manifest a first run creates, with no election:
    the same as none."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) == {"elections": {}}
    except (OSError, ValueError):
        return False


def not_book_input(rel: str, reader: str):
    """The reason NOT_BOOK_INPUTS gives for `reader`'s read of project file
    `rel`, or None."""
    for f, r, why in NOT_BOOK_INPUTS:
        if fnmatch.fnmatch(rel, f) and r == reader:
            return why
    return None


def allowed(rel: str, reader: str, out_rel: str, caller: str = ""):
    """The reason ALLOWED gives for a read, or None."""
    for f, r, o, why in ALLOWED:
        if ((f is None or fnmatch.fnmatch(rel, f))
                and (r is None or r in (reader, f"{reader} <- {caller}"))
                and (o is None or fnmatch.fnmatch(out_rel, o))):
            return why
    return None


def stamped(rel: str, reader: str, out_rel: str, caller: str, inputs_rel):
    """The reason STAMPED gives for a read in a build that declares the
    stamp (`inputs_rel`: the ask's inputs, as `rel` spells them), or None."""
    for f, r, o, stamp, why in STAMPED:
        if (fnmatch.fnmatch(rel, f) and r in (reader, f"{reader} <- {caller}")
                and fnmatch.fnmatch(out_rel, o)
                and any(fnmatch.fnmatch(i or "", stamp) for i in inputs_rel)):
            return why
    return None


def _written(e):
    if e["ev"] == "rename":
        return e["dst"]
    if e["ev"] == "open" and e["write"]:
        return e["path"]
    return None


def builds(events):
    """[(start, end, ask)]: each ask that answered True, to its output's
    first write (renamed into place, or opened for writing); an ask no
    write follows before the next ask of the same output is a query, left
    out."""
    out = []
    for i, a in enumerate(events):
        if a["ev"] != "ask" or not a["stale"]:
            continue
        for j in range(i + 1, len(events)):
            e = events[j]
            if e["ev"] == "ask" and e["out"] == a["out"]:
                break
            if _written(e) == a["out"]:
                out.append((i, j, a))
                break
    return out


def judge(events, root: str, home: str, config: str):
    """The undeclared edges of one process's trace: {(file, reader, out)},
    paths relative to `root` ("~/" for `home`)."""
    def rel(p):
        for base, tag in ((root, ""), (home, "~/")):
            if p == base or p.startswith(base + os.sep):
                return tag + os.path.relpath(p, base)
        return None

    spans = builds(events)
    edges = set()
    for k, e in enumerate(events):
        if e["ev"] != "open" or e["write"] or e["fresh"] or e["path"] == config:
            continue
        r = rel(e["path"])
        if r is None:
            continue
        live = [(i, a) for i, end, a in spans if i < k < end]
        if not live:
            continue                     # between builds: TJ-2498
        # A build inside another's span (asked and written before it ends)
        # is the one reading: the innermost.
        i, a = max(live, key=lambda s: s[0])
        if (e["path"] == a["out"] or e["path"] in a["inputs"]
                or any(_written(x) == e["path"] for x in events[i:k])):
            continue
        reader = _fn(e.get("at"))
        o = rel(a["out"]) or a["out"]
        via = _fn(e.get("via"))
        if (allowed(r, reader, o, via) is None
                and stamped(r, reader, o, via, [rel(i) for i in a["inputs"]]) is None):
            edges.add((r, reader, o))
    return edges


def _fn(frame):
    """`module:function` of a traced `module:function:line`."""
    return (frame or "").rsplit(":", 1)[0]


def call_sites():
    """{(module, first line, last line)} of every `needs_rebuild(` call in the
    package (by its name or as an attribute), past the freshness module."""
    sites = set()
    for p in sorted(SRC.rglob("*.py")):
        mod = p.relative_to(SRC).as_posix()
        if mod == "pipeline/freshness.py":
            continue
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            f = n.func if isinstance(n, ast.Call) else None
            if ((isinstance(f, ast.Name) and f.id == "needs_rebuild")
                    or (isinstance(f, ast.Attribute) and f.attr == "needs_rebuild")):
                sites.add((mod, n.lineno, n.end_lineno))
    return sites


def _traced(argvs, cwd: Path, trace: Path, name: str, exit_code: int):
    """Run each tjs command line of `argvs` in `cwd` traced into the new folder
    `trace`: the events of each process."""
    trace.mkdir()
    # The rows of the stage table the run executed, beside it.
    reached = trace.with_name(trace.name + "-reached")
    reached.mkdir()
    env = _style.env(TAXJSON_WIDTH=0, TAXJSON_TRACE_OPENS=str(trace),
                     **{stage_table.REACHED_LOG_ENV: str(reached)})
    # The tools in the run's own process: a child's reads land in a log of
    # its own, with no asks to judge them by.
    env.pop("TAXJSON_DISPATCH", None)
    env["PYTHONPATH"] = os.pathsep.join([str(TRACER), env["PYTHONPATH"]])
    for argv in argvs:
        r = subprocess.run(python_module_argv("taxjson.bin.taxjson_run", argv), cwd=cwd,
                           capture_output=True, text=True, env=env, timeout=900,
                           umask=0o077, stdin=subprocess.DEVNULL, check=False)
        if r.returncode != (exit_code if argv[0] == "run" else 0):
            raise AssertionError(f"{name}: tjs {' '.join(argv)} exited {r.returncode}:\n"
                                 f"{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    return [[json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
            for p in sorted(trace.iterdir())]


def _traced_book(name: str, tmp: Path):
    """Run book (or variant) `name` traced, in full with the commands that
    build on demand, then those commands again: (the events of each process
    of the run, of the commands run again, root, home, config, the run's
    folder)."""
    book, edits = VARIANTS.get(name, (name, []))
    meta = json.loads((_golden.GOLDEN / book / "book.json").read_text(encoding="utf-8"))
    root = tmp / name
    shutil.copytree(_golden.GOLDEN / book / "project", root,
                    ignore=shutil.ignore_patterns(*_golden._GENERATED))
    _golden._make_private(root)
    for rel, text in edits:
        with open(root / rel, "a", encoding="utf-8") as f:
            f.write(text)
    # The project's files before it runs (the person's, not the run's), beside
    # the trace: `test_every_project_file_the_run_reads_is_fingerprinted`.
    (tmp / f"{name}-before.txt").write_text(
        "".join(os.path.realpath(p) + "\n" for p in sorted(root.rglob("*")) if p.is_file()),
        encoding="utf-8")
    cwd = root / meta.get("run_dir", ".")
    ex = meta.get("exit", 0)
    full = _traced([["run", "--no-input"], *BUILD_ON_DEMAND.get(book, ())], cwd,
                   tmp / f"{name}-trace", name, ex)
    again = _traced(list(BUILD_ON_DEMAND.get(book, ())), cwd, tmp / f"{name}-trace-again",
                    name, ex)
    return (full, again, os.path.realpath(root), os.path.realpath(os.environ["HOME"]),
            os.path.realpath(cwd / "taxjson.toml"), os.path.realpath(cwd))


class TestFreshnessEdges(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        names = BOOKS + tuple(VARIANTS)
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            runs = ex.map(lambda n: _traced_book(n, Path(cls._tmp.name)), names)
            cls.books = dict(zip(names, runs))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_tracer_saw_the_builds(self):
        # A tracer that loaded nowhere would judge nothing and pass.
        for book, (logs, _again, root, home, config, _cwd) in self.books.items():
            with self.subTest(book=book):
                events = [e for log in logs for e in log]
                self.assertTrue(any(e["ev"] == "start" for e in events))
                spans = [s for log in logs for s in builds(log)]
                self.assertGreater(len(spans), 5)
                # A parser's read of its export inside the parse's build: a
                # parse run as a child (TAXJSON_DISPATCH=subprocess) reads in
                # a log of its own, with no asks, and nothing would be judged.
                parser_reads = sum(
                    1 for log in logs for i, end, a in builds(log)
                    for e in log[i:end] if e["ev"] == "open" and not e["write"]
                    and e["path"] in a["inputs"]
                    and "lib/brokerages/" in f"{e.get('at')} {e.get('via')}")
                self.assertGreater(parser_reads, 0)

    def test_every_read_a_build_makes_is_declared(self):
        found = []
        for book, (logs, again, root, home, config, _cwd) in self.books.items():
            for log in logs + again:
                found += [f"{book}: {out} reads {f} ({reader})"
                          for f, reader, out in sorted(judge(log, root, home, config))]
        self.assertEqual(found, [], "undeclared freshness edges (declare the file in the "
                                    "stage's needs_rebuild, or say in ALLOWED why it is none):\n"
                         + "\n".join(found))

    def test_the_books_reach_every_call_site(self):
        # A site counts once one of its asks became a judged build.
        reached = set()
        for logs, *_ in self.books.values():
            for log in logs:
                for _i, _end, a in builds(log):
                    if a.get("at"):
                        mod, _f, line = a["at"].rsplit(":", 2)
                        reached.add((mod, int(line)))
        missed = sorted(f"{mod}:{a}" for mod, a, b in call_sites()
                        if not any(m == mod and a <= ln <= b for m, ln in reached))
        self.assertEqual(missed, [], "needs_rebuild call sites no book builds through: "
                                     "add a book or a BUILD_ON_DEMAND command that does")

    def test_every_row_of_the_stage_table_is_reached(self):
        # The executor runs every row (test_stage_table: its one ask); the
        # books reach every row.
        reached = {row for p in Path(self._tmp.name).glob("*-reached/*.log")
                   for row in p.read_text(encoding="utf-8").split()}
        self.assertEqual(sorted(set(stage_table.TABLE) - reached), [],
                         "rows of the stage table no book's run reached")

    def test_every_project_file_the_run_reads_is_fingerprinted(self):
        # The filing commands refuse books older than their inputs by the
        # last full run's input fingerprint (lib/checklist `input_fingerprint`,
        # recorded in work/), whose list of files is its own (`_input_paths`),
        # not the stage table's: a file the run reads that the list misses
        # would let `tjs check-filed` or `tjs form-export` read books built
        # before it changed and say nothing (the architecture review of
        # 2026-11-21, F25). Every file of the project as it was before the
        # run that the run (or a view after it) opened is a key of the run's
        # record, or NOT_BOOK_INPUTS says why it is no input of the books.
        # The record's names and the empty manifest are judged here on
        # their own (`_record_names`, `_empty_manifest`), not by
        # lib/checklist's `_fp_key` and `_empty_manifest`, which a test
        # holds to the files they name: `TestFingerprintCoversEveryRunInput`
        # (tests/features/checks/test_checklist_run_state_and_gates.py).
        # The filed-year locks are lib/checklist's `_lock_paths` (no golden
        # book has one).
        from taxjson.lib import checklist
        from taxjson.lib.fingerprint_record import FINGERPRINT_FILE
        from taxjson.lib.project_layout import read_config
        found, judged = [], 0
        for book, (logs, _again, root, _home, _config, cwd) in self.books.items():
            before = set((Path(self._tmp.name) / f"{book}-before.txt")
                         .read_text(encoding="utf-8").splitlines())
            run_root = Path(cwd)
            keys = set(json.loads((run_root / "work" / FINGERPRINT_FILE)
                                  .read_text(encoding="utf-8"))["files"])
            cfg = read_config(run_root)
            locks = {os.path.realpath(p)
                     for p in checklist._lock_paths(run_root, cfg).values()}
            read = {}                   # {file: its readers}
            for e in (e for log in logs for e in log):
                if e["ev"] == "open" and not e["write"] and e["path"] in before:
                    read.setdefault(e["path"], set()).add(_fn(e.get("at")))
            judged += len(read)
            for p in sorted(read):
                names = _record_names(run_root, p)
                rel = os.path.relpath(p, root)
                if (names & keys or p in locks
                        or (Path(p).name == "elections.json" and _empty_manifest(p))
                        or all(not_book_input(rel, r) for r in read[p])):
                    continue
                found.append(f"{book}: {rel} (named {' or '.join(sorted(names))}; read by "
                             f"{', '.join(sorted(read[p]))})")
        self.assertGreater(judged, 20)        # the tracer saw the run's reads
        self.assertEqual(found, [], "project files the run reads that its input fingerprint "
                                    "does not hash (add them to lib/checklist `_input_paths`, "
                                    "or say in NOT_BOOK_INPUTS why the books never read them):\n"
                         + "\n".join(found))

    def test_the_missing_history_is_read_in_a_build(self):
        # The variant's gains runs read its line: a judged read, not one
        # between builds (where the method does not look: TJ-2498).
        logs, _again, root, home, config, _cwd = self.books["style-canada+opening"]
        reads = {a["out"] for log in logs for i, end, a in builds(log) for e in log[i:end]
                 if e["ev"] == "open" and not e["write"] and e["path"].endswith(".tt")
                 and "read_tt_openings" in (e.get("via") or "")}
        self.assertIn(os.path.join(root, "work", "margin_raw_gains.json"), reads)

    def test_a_view_run_again_builds_again(self):
        # No record's word serves a view (TJ-4307): run again with nothing
        # changed, it builds every full-history file it reads, each once in
        # the command (cli/common `_built_this_command`: demo's blend members).
        again = {book: a for book, (_l, a, *_r) in self.books.items() if a}
        self.assertTrue(again, "no book runs a command that builds on demand")
        for book, (_logs, logs, _r, _h, _c, cwd) in self.books.items():
            # The command's own process (the engine's children ask nothing).
            views = [[e for e in log if e["ev"] == "ask"] for log in logs]
            views = [v for v in views if v]
            with self.subTest(book=book):
                self.assertEqual(len(views), len(BUILD_ON_DEMAND.get(book.split("+")[0], ())),
                                 "a view that built nothing")
                for v in views:
                    asks = [os.path.relpath(e["out"], cwd) for e in v]
                    self.assertEqual([a for a, e in zip(asks, v) if not e["stale"]], [],
                                     "reused on its record")
                    self.assertEqual(sorted({a for a in asks if asks.count(a) > 1}), [],
                                     "built twice in one command")


class TestTheJudge(unittest.TestCase):
    """The judge on a made-up trace: a planted edge is found, a declared read
    and a query's read are not."""

    def _ev(self):
        R, H = "/p", "/h"
        return R, H, [
            {"ev": "ask", "out": f"{R}/work/a.json", "inputs": [f"{R}/in.csv"], "stale": True,
             "at": "pipeline/stages.py:f:10"},
            {"ev": "open", "path": f"{R}/in.csv", "write": False, "fresh": False, "at": "x.py:g:1"},
            {"ev": "open", "path": f"{R}/ticker.map", "write": False, "fresh": False,
             "at": "lib/y.py:h:2"},
            {"ev": "open", "path": f"{R}/work/.deps/a.json.json", "write": False,
             "fresh": True, "at": None},
            {"ev": "open", "path": f"{R}/work/a.json.x.part", "write": True, "fresh": False},
            {"ev": "rename", "src": f"{R}/work/a.json.x.part", "dst": f"{R}/work/a.json"},
            {"ev": "ask", "out": f"{R}/work/b.json", "inputs": [], "stale": True},
            {"ev": "open", "path": f"{R}/other.csv", "write": False, "fresh": False,
             "at": "lib/z.py:k:3"},
        ]

    def test_a_planted_edge_is_found(self):
        R, H, ev = self._ev()
        self.assertEqual(judge(ev, R, H, f"{R}/taxjson.toml"),
                         {("ticker.map", "lib/y.py:h", "work/a.json")})

    def test_a_stamped_read_needs_its_stamp_declared(self):
        R = "/p"
        def ev(inputs):
            return [{"ev": "ask", "out": f"{R}/work/m_ib.json", "inputs": inputs, "stale": True},
                    {"ev": "open", "path": f"{R}/inputs/m/x.tt", "write": False, "fresh": False,
                     "at": "lib/brokerages/ib_extractor.py:_account_tt_links:5"},
                    {"ev": "open", "path": f"{R}/work/m_ib.json", "write": True, "fresh": False}]
        edge = {("inputs/m/x.tt", "lib/brokerages/ib_extractor.py:_account_tt_links",
                 "work/m_ib.json")}
        self.assertEqual(judge(ev([f"{R}/in.csv"]), R, "/h", f"{R}/taxjson.toml"), edge)
        self.assertEqual(judge(ev([f"{R}/work/m_ib_tt.state"]), R, "/h", f"{R}/taxjson.toml"),
                         set())

    def test_a_read_is_the_innermost_builds(self):
        # A build asked and written inside another's span reads for itself:
        # the outer build declaring the file does not excuse the inner one.
        R = "/p"
        def op(p, w=False):
            return {"ev": "open", "path": p, "write": w, "fresh": False, "at": "lib/q.py:r:1"}
        ev = [{"ev": "ask", "out": f"{R}/work/a.json", "inputs": [f"{R}/rates.csv"], "stale": True},
              {"ev": "ask", "out": f"{R}/work/b.json", "inputs": [], "stale": True},
              op(f"{R}/rates.csv"),
              op(f"{R}/work/b.json", True),
              op(f"{R}/rates.csv"),
              op(f"{R}/work/a.json", True)]
        self.assertEqual(judge(ev, R, "/h", f"{R}/taxjson.toml"),
                         {("rates.csv", "lib/q.py:r", "work/b.json")})

    def test_a_build_ends_at_its_first_write(self):
        # An output opened for writing ends its build: a later read of the
        # run (another stage's, before the next ask) is not charged to it.
        R = "/p"
        ev = [{"ev": "ask", "out": f"{R}/work/a.json", "inputs": [], "stale": True},
              {"ev": "open", "path": f"{R}/work/a.json", "write": True, "fresh": False},
              {"ev": "open", "path": f"{R}/ticker.map", "write": False, "fresh": False,
               "at": "lib/q.py:r:1"},
              {"ev": "open", "path": f"{R}/work/a.json", "write": True, "fresh": False}]
        self.assertEqual(builds(ev), [(0, 1, ev[0])])
        self.assertEqual(judge(ev, R, "/h", f"{R}/taxjson.toml"), set())


class TestKrakenRowsUseTheRates(unittest.TestCase):
    """TJ-2497: a Kraken multi-leg receipt whose fiat leg has no amountusd is
    split through the run's rates, so its parse declares them (the registry's
    `reads_rates`, read by the parse stage)."""

    LEGS = ["L1,RS1,2025-03-03 12:00:00,spend,,currency,ZCAD,spot,-100,0,0",
            "L2,RS1,2025-03-03 12:00:00,spend,,currency,ZUSD,spot,-50,0,0",
            "L3,RS1,2025-03-03 12:00:00,receive,,currency,ADA,spot,300,0,300"]

    def _split(self, rate):
        from _support.kraken_ledger import _KL_H, _ledger, _parse
        from taxjson.lib.brokerages._crypto_common import set_depeg_rates
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, {"TAXJSON_LOCAL_TZ": "UTC"}):
            rates = None
            if rate is not None:
                rates = Path(td) / "to_base.csv"
                rates.write_text(f"2025-03-03 00:00:00 USD CAD {rate} BOC\n", encoding="utf-8")
            set_depeg_rates(rates, None)       # None clears an earlier parse's
            try:
                txs, _err = _parse({"kr_ledgers.csv": _ledger(self.LEGS, _KL_H)},
                                   "kr_ledgers.csv")
            finally:
                set_depeg_rates(None, None)
        return sorted(round(t["quantity"], 4) for t in txs if t["action"] == "BUYSELL")

    def test_the_split_moves_with_the_rates(self):
        self.assertEqual(self._split(None), [150.0, 150.0])           # equal shares
        self.assertEqual(self._split(1.25), [115.3846, 184.6154])     # 80 : 50 in USD
        self.assertEqual(self._split(1.50), [128.5714, 171.4286])

    def test_a_parser_reads_the_rates_only_when_the_registry_says_so(self):
        # The parse stage gives the rates (and declares them) only to a parser
        # the registry marks `reads_rates` (TJ-2508): one naming the rates'
        # readers unmarked would get none in a run, one marked naming none
        # re-parses for nothing. A name wherever it stands: a call, an
        # attribute (`cc.usd_value`), an import.
        from taxjson.parsers.registry import SPECS
        readers = {"usd_value", "warn_depeg", "set_depeg_rates", "_DEPEG_RATES"}
        marked = {s.module.rsplit(".", 1)[1] for s in SPECS if s.reads_rates}
        reading = set()
        for p in sorted((SRC / "lib" / "brokerages").glob("*.py")):
            if p.stem == "_crypto_common":
                continue
            for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                said = ({n.id} if isinstance(n, ast.Name)
                        else {n.attr} if isinstance(n, ast.Attribute)
                        else {a.name for a in n.names}
                        if isinstance(n, (ast.Import, ast.ImportFrom)) else set())
                if said & readers:
                    reading.add(p.stem)
        self.assertIn("kraken", reading)         # the lock sees the uses it locks
        self.assertEqual(reading, marked, "a parser module that reads the run's rates "
                         "(the de-peg check, `usd_value`) is marked `reads_rates` in "
                         "parsers/registry.py, and only such a one (TJ-2497, TJ-2508)")
        self.assertEqual({s.id for s in SPECS if s.reads_rates}, rate_reading_brokers())


if __name__ == "__main__":
    unittest.main()
