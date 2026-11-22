"""What a run's records say after one edit, and what the views build from
them (backlog TJ-4128, TJ-4129, TJ-4135 to TJ-4137, TJ-4181 to TJ-4184,
TJ-4230, TJ-4260, TJ-4265, TJ-4266, TJ-1807). Formerly tests/test_fast_edges:
`tjs run --fast` after one edit against a full run; `--fast` is retired (the
owner's decision of 2026-11-17: `tjs run` always rebuilds everything), so a
run after an edit is a full run, and what stays is:

- the views: `tjs stats --all-history` after an edit, with no run
  between, or after the run (cli/common.py `_gains_all_file`,
  `_gains_all_blend`: built every time, TJ-4307), builds what a build of
  the full-history files from scratch builds;
- the records each run writes (the sweep reads them): each
  declares what its stage reads (the missing history's lines and maps, the
  IB parse's .tt lines, the merges' fingerprints, the blend's books, the
  renders' and the radars' inputs, the locks), checked on the record;
- the edits themselves move what they are compared on (each a past
  missed edge: the IB parse's ticker change, the openings, a MULT line,
  a journal's legs);
- the store (TJ-4265, TJ-1807): what the last run's row instances built
  and this run's do not is swept, and what the markers carried is declared;
  TestAnOlderTaxjsonsWork: a project whose work/ an older taxjson left
  (81f69b75's format, TJ-4266) is rebuilt and vouched for again by its first
  run, its old files gone.

Each case copies style-canada (tests/golden: an IB margin account, and .tt
files in two accounts), makes its setup edit, runs, makes the edit, runs
(or the on-demand command, or both) and keeps what it built; a case with
the on-demand command then builds the answer in the same folder: the
on-demand files removed and built again. The gains, the native books' gains, the
full-history gains, the IB parse, the holdings, the cost timelines and the
.tt conversions' warnings must agree, masked as the goldens mask the clock.

TestAFolderWhereAFileWasExpected: a folder at a path the run reads as a
file runs as it did before the guard (TJ-4133, TJ-4134), and one a stage
declares anyway is a one-line refusal, from `tjs run` and from a view
command's on-demand build alike (TJ-4180). Synthetic data only; the runs go
WORKERS at a time.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

import _golden          # the synthetic HOME (tests/_hermetic) and the books
import _style
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.project_layout import _GAINS_ALL_SUFFIX
from taxjson.pipeline.freshness import record_path

BOOK = "style-canada"
WORKERS = 5
# What a case compares, relative to the project.
# (A pattern matches a dot-file too: the blend's .blend_gains_all.json.)
COMPARED = ("work/*_gains.json", "work/*_raw_gains.json", "work/*_raw_base_gains.json",
            "work/*_gains_wash.json", "work/*_ib.json", f"work/*{_GAINS_ALL_SUFFIX}",
            "reports/*_holdings.toml",
            # the cost timelines a gains run writes beside its file (`tjs
            # list --date`), and the .tt conversions' captured warnings
            # (lib/tt_totals: the console's and `run --strict`'s), and the
            # blend's cost timeline
            "work/.*_gains.timeline.json", "work/*_tt_*.json.diag",
            "work/.*_gains_wash.timeline.json",
            # the renders and the reports across accounts (TJ-4260): the
            # summaries and wash twins, the report models, the crypto
            # validation, the wash pass's traces, the radars' sidecars and
            # every report (ccd, leaps, the radars, cross-listings, fees)
            "reports/*.sum", "work/*_report.json", "work/*_validate.diag",
            "work/*_gains_wash.traces", "reports/*.rpt", "reports/wash_radar_*.json")
MARGIN_TT = "inputs/margin/margin_extra.tt"
TFSA_TT = "inputs/tfsa/tfsa_extra.tt"
IB_CSV = "inputs/margin/ib_extra.csv"
# One contract id under two symbols in the IB statement: QZOA bought, then
# sold as QZNB. The parse books the ticker change unless a .tt line does.
_CONID_FII = ('Financial Instrument Information,Data,Stocks,"QZNB, QZOA",QZNB CORP,'
              '990000779,US9990007791,,NYSE,1,,,COMMON,,\n')
_CONID_TRADES = ('Trades,Data,Order,Stocks,USD,QZOA,"2024-02-12, 10:00:00",100,10.00,10.00,'
                 '-1000.00,-1.00,1001.00,0,0,O\n'
                 'Trades,Data,Order,Stocks,USD,QZNB,"2024-08-01, 10:00:00",-100,12.00,12.00,'
                 '1200.00,-1.00,-1001.00,198.00,0,C\n')


# A non-cash distribution on the opened symbol, sized on the units held.
_DISTRIBUTION = ('\n[[distributions]]\nsymbol = "GHOSTQ.TO"\nrecord_date = 2024-06-28\n'
                 'per_share = 0.25\n')
# A map line for a symbol no book holds: the map exists before the edit.
_OTHER_MAP_LINE = "GLOBAL ZZZA.TO ZZZB.TO\n"


def _opening(symbol: str, qty: int) -> str:
    return f"OPENING 2023-12-29 {symbol} {qty} cost=unknown\n"


def _append(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.write_text((p.read_text(encoding="utf-8") if p.exists() else "") + text,
                 encoding="utf-8")


def _replace(root: Path, rel: str, old: str, new: str) -> None:
    p = root / rel
    text = p.read_text(encoding="utf-8")
    assert old in text, f"{rel}: {old!r}"
    p.write_text(text.replace(old, new), encoding="utf-8")


def _insert_after(root: Path, rel: str, header: str, rows: str) -> None:
    """`rows` after the line of `rel` that starts with `header`."""
    p = root / rel
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    (i,) = [n for n, ln in enumerate(lines) if ln.startswith(header)]
    p.write_text("".join(lines[:i + 1]) + rows + "".join(lines[i + 1:]), encoding="utf-8")


def _conid_change(root: Path) -> None:
    _insert_after(root, IB_CSV, "Financial Instrument Information,Header", _CONID_FII)
    _insert_after(root, IB_CSV, "Trades,Header", _CONID_TRADES)


# A journal between two listings of one security (a TOBASE pair): QZA
# bought on the TSX, 90 moved to its US line. The account keeps its
# TRANSFER rows out of its books (transfers = false), so the legs are in
# the parse's transfer evidence alone (work/margin_ib_transfers.json),
# which the native merge books as the journal (_raw_book_journals).
_JOURNAL_FII = ('Financial Instrument Information,Data,Stocks,QZA,QZALPHA MINES CORP,'
                '999000201,CA9990002011,,TSE,1,,,COMMON,,\n')
_JOURNAL_TRADES = ('Trades,Data,Order,Stocks,CAD,QZA,"2024-06-19, 10:00:00",100,40.00,40.00,'
                   '-4000.00,0,4000.00,0,0,O\n')


def _journal_legs(qty: int) -> str:
    return (f"Transfers,Data,Stocks,CAD,QZA,2024-07-31,InterDepot,Out,IB,U1234567,-{qty},0,"
            f"-{40 * qty},0,0,\n"
            f"Transfers,Data,Stocks,USD,QZA,2024-07-31,InterDepot,In,IB,U1234567,{qty},0,"
            f"{32 * qty},0,0,\n")


def _journal(root: Path) -> None:
    _append(root, "ticker.map", "TOBASE QZA.US QZA.TO\n")
    _journal_joined(root)


def _journal_joined(root: Path) -> None:
    """The journal's legs with no map line: the run joins the two listings
    (work/ticker.map.effective, lib/cross_listings)."""
    _insert_after(root, IB_CSV, "Financial Instrument Information,Header", _JOURNAL_FII)
    _insert_after(root, IB_CSV, "Trades,Header", _JOURNAL_TRADES)
    _insert_after(root, IB_CSV, "Transfers,Header", _journal_legs(90))


def _undo_join(root: Path) -> None:
    """A DISTINCT line for the joined pair: the effective map goes. The
    book's record as it was, joined, is kept beside the project."""
    shutil.copyfile(record_path(root / "work/margin_base.json"), root / "joined_record.json")
    _append(root, "ticker.map", "DISTINCT QZA.TO QZA.US\n")


# A loss in a taxable account the blend pools (margin), and a sheltered
# account's purchase of the same shares 7 days after the sale, held past the
# 30th day: the loss is superficial (s.54) only through the sheltered books
# the blended gains read (`--sheltered`).
_LOSS_PAIR = ("BUYSELL 2024-03-01 09:30:00 QZL.TO 100 CAD 10.00 1000.00 0\n"
              "BUYSELL 2024-09-03 09:30:00 QZL.TO -100 CAD 8.00 800.00 0\n")
_SHELTERED_BUY = ("BUYSELL 2024-09-10 09:30:00 QZL.TO 50 CAD 8.00 400.00 0\n"
                  "BUYSELL 2024-12-02 09:30:00 QZL.TO -50 CAD 9.00 450.00 0\n")


# A coin sold at a loss and bought back within the 30 days in the crypto
# account (the superficial-loss rule reaches crypto in Canada), sold again
# by the year's end; and the filing position against its denial. The crypto
# account is not in the blend: the line moves the loss overrides alone, not
# a blended account's book (every gains file records every line).
_COIN_LOSS = ("CB-DEMO-0101,2024-02-01 15:00:00 UTC,Buy,LTC,10.00000000,USD,80.00,800.00,800.00,"
              "0.00,Bought 10 LTC\n"
              "CB-DEMO-0102,2024-06-03 15:00:00 UTC,Sell,LTC,10.00000000,USD,70.00,700.00,700.00,"
              "0.00,Sold 10 LTC\n"
              "CB-DEMO-0103,2024-06-10 15:00:00 UTC,Buy,LTC,10.00000000,USD,71.00,710.00,710.00,"
              "0.00,Bought 10 LTC\n"
              "CB-DEMO-0104,2024-08-01 15:00:00 UTC,Sell,LTC,10.00000000,USD,75.00,750.00,750.00,"
              "0.00,Sold 10 LTC\n")
_COIN_ALLOWLOSS = 'ALLOWLOSS 2024-06-03 LTC reason="synthetic"\n'


# The book's Kraken export in a crypto account of its own: two taxable
# crypto accounts, so a crypto blend (`cryptoblend`), whose members' books
# the crypto chain builds (crypto_base, not base: TJ-4232).
_KRAKEN = "inputs/crypto2/kraken_demo.csv"
_KRAKEN_SELL = '"2024-12-03 13:55:30","sell","limit","4200.00","2100.00"'


def _crypto_blend(root: Path) -> None:
    (root / "inputs/crypto2").mkdir()
    (root / "inputs/crypto/kraken_demo.csv").rename(root / _KRAKEN)
    _append(root, "taxjson.toml", '\n[accounts.crypto2]\ntype = "taxable"\ncrypto = true\n')


# A coin sold at a loss and bought back within the 30 days, still held: the
# crypto account's own book (its own wash pass with the sheltered books, its
# validation, its holdings and renders: TJ-4260).
_COIN_REBUY = "".join(_COIN_LOSS.splitlines(keepends=True)[:3])


# A taxable account taken out of the project: its table and its exports.
def _remove_qt(root: Path) -> None:
    _replace(root, "taxjson.toml", '[accounts.qt]\ntype = "taxable"\n', "")
    shutil.rmtree(root / "inputs/qt")


# A map line for a symbol the books hold (the tfsa's .tt rows), no join.
_SAMPA_MAP_LINE = "GLOBAL SAMPA.TO SAMPB.TO\n"


# A mini option (10 units a contract) whose .tt totals are right at its
# size: a typo at the standard 100 until the map's MULT line sizes it
# (lib/tt_format `_tt_contract_size`, TJ-0864).
_MINI = "QZM241220C00005000.TO"
_MINI_LINES = (f"BUYSELL 2024-06-03 10:00:00 {_MINI} 1 CAD 2 20 0\n"
               f"BUYSELL 2024-06-10 10:00:00 {_MINI} -1 CAD 3 30 0\n")


# name: (setup, edit[, after]), each a function of the project folder;
# `after` "stats": the edit is followed by `tjs stats --all-history`, not
# a run (its full-history files are built on demand); "run+stats": by
# a run, then `tjs stats --all-history` (the full-history files built on
# demand from what the run rebuilt).
CASES = {
    "opening line changed": (
        lambda r: _append(r, MARGIN_TT, _opening("GHOSTQ.TO", 40)),
        lambda r: _replace(r, MARGIN_TT, _opening("GHOSTQ.TO", 40), _opening("GHOSTQ.TO", 60))),
    "opening line added": (
        lambda r: _append(r, MARGIN_TT, _opening("GHOSTQ.TO", 40)),
        lambda r: _append(r, TFSA_TT, _opening("GHOSTR.TO", 25))),
    "opening line removed": (
        lambda r: _append(r, MARGIN_TT, _opening("GHOSTQ.TO", 40) + _opening("GHOSTR.TO", 25)),
        lambda r: _replace(r, MARGIN_TT, _opening("GHOSTR.TO", 25), "")),
    "tt file with an opening added": (
        lambda r: _append(r, MARGIN_TT, _opening("GHOSTQ.TO", 40)),
        lambda r: _append(r, "inputs/tfsa/openings.tt", _opening("GHOSTR.TO", 25))),
    "tt file with an opening removed": (
        lambda r: (_append(r, MARGIN_TT, _opening("GHOSTQ.TO", 40)),
                   _append(r, "inputs/margin/openings.tt", _opening("GHOSTR.TO", 25))),
        lambda r: (r / "inputs/margin/openings.tt").unlink()),
    "ib: the account's split row added": (
        _conid_change,
        lambda r: _append(r, MARGIN_TT, "SPLIT 2024-08-01 00:00:00 QZOA.US QZNB.US 1\n")),
    "ib: another account's rename line added": (
        _conid_change,
        lambda r: _append(r, TFSA_TT, "RENAME 2024-08-01 QZOA.US QZNB.US\n")),
    "map: a GLOBAL line renames an opening's symbol": (
        lambda r: (_append(r, MARGIN_TT, _opening("GHOSTW.TO", 40)),
                   _append(r, "ticker.map", _OTHER_MAP_LINE)),
        lambda r: _append(r, "ticker.map", "GLOBAL GHOSTW.TO GHOSTQ.TO\n")),
    "map: a DELETE line for an opening's symbol": (
        lambda r: (_append(r, MARGIN_TT, _opening("GHOSTQ.TO", 40) + _opening("GHOSTR.TO", 25)),
                   _append(r, "ticker.map", _OTHER_MAP_LINE)),
        lambda r: _append(r, "ticker.map", "DELETE GHOSTR.TO\n")),
    # margin is in the blend (`_gains_all_blend`), tfsa alone (`_gains_all_file`).
    "stats: opening lines changed, no run between": (
        lambda r: (_append(r, MARGIN_TT, _opening("GHOSTQ.TO", 40)),
                   _append(r, TFSA_TT, _opening("GHOSTR.TO", 25))),
        lambda r: (_replace(r, MARGIN_TT, _opening("GHOSTQ.TO", 40), _opening("GHOSTQ.TO", 60)),
                   _replace(r, TFSA_TT, _opening("GHOSTR.TO", 25), _opening("GHOSTR.TO", 35))),
        "stats"),
    "distributions: an opening line changed": (
        lambda r: (_append(r, MARGIN_TT, _opening("GHOSTQ.TO", 40)),
                   _append(r, "taxjson.toml", _DISTRIBUTION)),
        lambda r: _replace(r, MARGIN_TT, _opening("GHOSTQ.TO", 40), _opening("GHOSTQ.TO", 60))),
    # The gains row's inputs and ground (pipeline/stage_table), each held
    # by no other case (the review of step 12's table, TJ-4181 to TJ-4183):
    # the loss overrides every account's gains read,
    "gains: an ALLOWLOSS line added": (
        lambda r: None,
        lambda r: _append(r, MARGIN_TT, 'ALLOWLOSS 2024-11-01 SAMPA.TO reason="synthetic"\n')),
    # the cost timeline gone (the run writes it with the gains),
    "gains: the cost timeline removed": (
        lambda r: None,
        lambda r: (r / "work/.margin_gains.timeline.json").unlink()),
    # the map a .tt conversion sizes an option line's total check by,
    "tt: a MULT line sizes an option line": (
        lambda r: (_append(r, MARGIN_TT, _MINI_LINES), _append(r, "ticker.map", _OTHER_MAP_LINE)),
        lambda r: _append(r, "ticker.map", f"MULT {_MINI} 10\n")),
    # and the transfer evidence the native merge books a journal from.
    "raw: a journal's legs changed in the transfer evidence": (
        _journal,
        lambda r: _replace(r, IB_CSV, _journal_legs(90), _journal_legs(80))),
    # The blend's rows (TJ-4230): a member's book (the blended books' merge
    # and the full-history one),
    "blend: a member's book changed": (
        lambda r: None,
        lambda r: _append(r, MARGIN_TT, _LOSS_PAIR),
        "run+stats"),
    # a crypto blend's member's book (the crypto chain's: TJ-4232),
    "blend: a crypto member's book changed": (
        _crypto_blend,
        lambda r: _replace(r, _KRAKEN, _KRAKEN_SELL, _KRAKEN_SELL.replace("4200", "4300")
                           .replace("2100", "2150")),
        "run+stats"),
    # a sheltered account's book (the sheltered merge, the blended gains
    # that read it, each member's split, the account's own full history),
    "blend: a sheltered account's purchase makes a loss superficial": (
        lambda r: _append(r, MARGIN_TT, _LOSS_PAIR),
        lambda r: _append(r, TFSA_TT, _SHELTERED_BUY),
        "run+stats"),
    # the loss overrides alone (a line of an account outside the blend),
    "blend: an ALLOWLOSS line of an account outside the blend": (
        lambda r: _append(r, "inputs/crypto/coinbase_demo.csv", _COIN_LOSS),
        lambda r: _append(r, "inputs/crypto/positions.tt", _COIN_ALLOWLOSS),
        "run+stats"),
    # and the blended gains' ground: their cost timeline gone.
    "blend: the blend's cost timeline removed": (
        lambda r: None,
        lambda r: (r / "work/.blend_gains_wash.timeline.json").unlink()),
    # The renders and the reports across accounts (TJ-4260): a crypto
    # account's own book (its wash pass, validation, holdings, summaries,
    # report model and radar),
    "crypto: a coin sold at a loss and bought back": (
        lambda r: None,
        lambda r: _append(r, "inputs/crypto/coinbase_demo.csv", _COIN_REBUY)),
    # the wash pass's ground (its traces gone, its cost timeline gone) and
    # the radars' (an account's sidecar and COMBINED's).
    "wash: the traces removed": (
        lambda r: None,
        lambda r: (r / "work/crypto_gains_wash.traces").unlink()),
    "wash: the cost timeline removed": (
        lambda r: None,
        lambda r: (r / "work/.crypto_gains_wash.timeline.json").unlink()),
    "radar: the sidecars removed": (
        lambda r: None,
        lambda r: ((r / "reports/wash_radar_margin.json").unlink(),
                   (r / "reports/wash_radar_COMBINED.json").unlink())),
    # What the last run's row instances built and this one's do not is swept
    # (work/.deps/run.json, TJ-4265): an account gone from taxjson.toml, an
    # export gone from an account.
    "sweep: a taxable account removed": (lambda r: None, _remove_qt),
    "sweep: an export removed from an account": (
        lambda r: None, lambda r: (r / "inputs/crypto/kraken_demo.csv").unlink()),
    # What the markers and `invalidate` carried, declared (TJ-1807): the
    # missing history withdrawn (the gains' stamp; a distribution's book's
    # too), ticker.map removed, the effective map's join gone (each stage
    # given the map declares the one it is given).
    "withdrawn: the last opening line removed": (
        lambda r: _append(r, MARGIN_TT, _opening("GHOSTQ.TO", 40)),
        lambda r: _replace(r, MARGIN_TT, _opening("GHOSTQ.TO", 40), "")),
    "withdrawn: a distribution's last opening line removed": (
        lambda r: (_append(r, MARGIN_TT, _opening("GHOSTQ.TO", 40)),
                   _append(r, "taxjson.toml", _DISTRIBUTION)),
        lambda r: _replace(r, MARGIN_TT, _opening("GHOSTQ.TO", 40), "")),
    "withdrawn: ticker.map removed": (
        lambda r: _append(r, "ticker.map", _SAMPA_MAP_LINE),
        lambda r: (r / "ticker.map").unlink()),
    "withdrawn: the journal's join undone": (_journal_joined, _undo_join),
}


def _tjs(root: Path, *argv: str, code: int = 0, argvs=None) -> subprocess.CompletedProcess:
    r = subprocess.run(argvs or python_module_argv("taxjson.bin.taxjson_run", list(argv)),
                       cwd=root, capture_output=True, text=True,
                       env=_style.env(TAXJSON_WIDTH=0), timeout=900, umask=0o077,
                       stdin=subprocess.DEVNULL, check=False)
    if r.returncode != code:
        raise AssertionError(f"tjs {' '.join(argv)} exited {r.returncode}:\n"
                             f"{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    return r


def _copy(tmp: Path, name: str, book: str = BOOK) -> Path:
    root = tmp / name.replace(" ", "-").replace(":", "").replace("'", "")
    shutil.copytree(_golden.GOLDEN / book / "project", root,
                    ignore=shutil.ignore_patterns(*_golden._GENERATED))
    _golden._make_private(root)
    return root


def _built_on_demand(root: Path) -> None:
    """The full-history gains built from scratch: the files `tjs stats
    --all-history` builds, and their records, removed first."""
    work = root / "work"
    for p in [*work.glob(f"*{_GAINS_ALL_SUFFIX}"), *work.glob(".*_all_base.json")]:
        p.unlink()
        record_path(p).unlink(missing_ok=True)
    _tjs(root, "stats", "--all-history")


def _built(root: Path) -> dict:
    """{file: its masked text} of the COMPARED files."""
    return {p.relative_to(root).as_posix():
            _golden._masked(p.read_text(encoding="utf-8"), str(root), str(root.resolve()))
            for pattern in COMPARED for p in sorted(root.glob(pattern))}


class Run(NamedTuple):
    before: dict    # what the run built before the edit
    edited: dict    # what is left right after it (a case may remove a built file)
    after: dict     # what the run and the views built after it
    full: dict      # the answer: the views' files rebuilt from scratch
    root: Path      # the project folder
    left: tuple     # every file in work/ and reports/ after it, relative
    views: bool     # the case asks the views


def _case(tmp: Path, name: str) -> Run:
    setup, edit, *after = CASES[name]
    flavour = after[0] if after else "run"
    stats, run = flavour in ("stats", "run+stats"), flavour in ("run", "run+stats")
    root = _copy(tmp, name)
    setup(root)
    _tjs(root, "run", "--no-input")
    if stats:
        _tjs(root, "stats", "--all-history")
    before = _built(root)
    edit(root)
    edited = _built(root)
    if run:
        _tjs(root, "run", "--no-input")
    if stats:
        _tjs(root, "stats", "--all-history")
    after = _built(root)
    left = tuple(sorted(p.relative_to(root).as_posix() for d in ("work", "reports")
                        for p in (root / d).rglob("*") if p.is_file()))
    if stats:
        _built_on_demand(root)
    return Run(before, edited, after, _built(root), root, left, stats)


class TestARunAfterAnEdit(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            cls.runs = dict(zip(CASES, ex.map(lambda n: _case(tmp, n), CASES)))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_views_build_what_a_rebuild_builds(self):
        # The full-history files a view built after the edit against the
        # same files removed and built from scratch (built every time now,
        # TJ-4307: the comparison holds the build, not a reuse).
        viewed = {n: r for n, r in self.runs.items() if r.views}
        self.assertGreaterEqual(len(viewed), 5)
        for name, run in viewed.items():
            after, full = run.after, run.full
            with self.subTest(edit=name):
                self.assertTrue(any(f.endswith(_GAINS_ALL_SUFFIX) for f in full))
                self.assertEqual(sorted(after), sorted(full))
                stale = [f for f in sorted(full) if after[f] != full[f]]
                self.assertEqual(stale, [], f"{name}: the views left these as they were")

    def test_every_run_builds_the_books_and_the_renders(self):
        for name, run in self.runs.items():
            with self.subTest(edit=name):
                self.assertTrue(any(f.endswith("_holdings.toml") for f in run.full))
                self.assertTrue(any(f.endswith("_raw_gains.json") for f in run.full))

    def test_every_edit_moves_what_it_is_compared_on(self):
        # An edit that changed nothing compared would hold nothing (the
        # timeline's removal is itself the change).
        for name, run in self.runs.items():
            with self.subTest(edit=name):
                self.assertNotEqual([f for f in sorted(set(run.before) | set(run.full))
                                     if run.full.get(f) != run.before.get(f)
                                     or run.edited.get(f) != run.before.get(f)], [])

    def test_the_ib_edits_move_the_parse(self):
        # The contract id's ticker change: booked by the parse, then (the
        # .tt line books it) not.
        for name in ("ib: the account's split row added",
                     "ib: another account's rename line added"):
            with self.subTest(edit=name):
                before, full = self.runs[name].before, self.runs[name].full
                self.assertIn('"ib-conid"', before["work/margin_ib.json"])
                self.assertNotIn('"ib-conid"', full["work/margin_ib.json"])

    def test_the_on_demand_case_moves_both_builders(self):
        # The blend's full-history file and an account's own both moved.
        run = self.runs["stats: opening lines changed, no run between"]
        before, full = run.before, run.full
        moved = {f for f in full if before.get(f) != full[f]}
        self.assertLessEqual({f"work/.blend{_GAINS_ALL_SUFFIX}", f"work/tfsa{_GAINS_ALL_SUFFIX}"},
                             moved)

    def test_the_distributions_merge_declares_the_lines(self):
        # Its fingerprint rebuilds it on the edit too, so no edit can tell
        # the declaration apart: the record names it (TJ-4137).
        run = self.runs["distributions: an opening line changed"]
        before, full, root = run.before, run.full, run.root
        self.assertNotEqual(before["work/margin_raw_gains.json"], full["work/margin_raw_gains.json"])
        rec = json.loads(record_path(root / "work" / "margin_base.json").read_text(
            encoding="utf-8"))
        self.assertIn("work/missing_history.state", rec["inputs"])

    def test_the_table_cases_move_what_their_input_alone_declares(self):
        # What each moves no other input of its stage does (TJ-4181 to
        # TJ-4183): another account's gains (its books unchanged), the
        # timeline itself, the conversion's warning (its rows unchanged),
        # the native books (the parse unchanged).
        moves = {"gains: an ALLOWLOSS line added": ("work/qt_gains.json", "work/tfsa_gains.json"),
                 "gains: the cost timeline removed": ("work/.margin_gains.timeline.json",),
                 "tt: a MULT line sizes an option line": (
                     "work/margin_tt_margin_extra.json.diag",),
                 "raw: a journal's legs changed in the transfer evidence": (
                     "work/margin_raw_gains.json", "reports/margin_holdings.toml")}
        keeps = {"gains: an ALLOWLOSS line added": ("work/qt_raw_gains.json",),
                 "tt: a MULT line sizes an option line": ("work/margin_gains.json",),
                 "raw: a journal's legs changed in the transfer evidence": (
                     "work/margin_ib.json", "work/margin_gains.json")}
        for name, files in moves.items():
            run = self.runs[name]
            with self.subTest(edit=name):
                for f in files:
                    self.assertTrue(run.before.get(f) != run.full.get(f)
                                    or run.edited.get(f) != run.before.get(f), f)
                for f in keeps.get(name, ()):
                    self.assertEqual(run.before[f], run.full[f], f)

    def test_the_blend_cases_move_what_their_input_alone_declares(self):
        # A member's book moves the blend's merge (and the full history's),
        # a crypto member's the crypto blend's, a sheltered purchase only
        # the blended gains and the split (the members' books unchanged),
        # the timeline only itself.
        moves = {"blend: a member's book changed": (
                     "work/margin_gains_wash.json", f"work/.blend{_GAINS_ALL_SUFFIX}"),
                 "blend: a crypto member's book changed": (
                     "work/.cryptoblend_gains_wash.json", "work/crypto2_gains_wash.json",
                     f"work/.cryptoblend{_GAINS_ALL_SUFFIX}", f"work/crypto2{_GAINS_ALL_SUFFIX}"),
                 "blend: a sheltered account's purchase makes a loss superficial": (
                     "work/margin_gains_wash.json", f"work/tfsa{_GAINS_ALL_SUFFIX}"),
                 "blend: the blend's cost timeline removed": (
                     "work/.blend_gains_wash.timeline.json",),
                 "blend: an ALLOWLOSS line of an account outside the blend": (
                     "work/.blend_gains_wash.json", f"work/.blend{_GAINS_ALL_SUFFIX}",
                     f"work/tfsa{_GAINS_ALL_SUFFIX}")}
        keeps = {"blend: a sheltered account's purchase makes a loss superficial": (
                     "work/margin_gains.json", "work/qt_gains.json",
                     f"work/margin{_GAINS_ALL_SUFFIX}"),
                 "blend: an ALLOWLOSS line of an account outside the blend": (
                     "work/margin_raw_gains.json", "work/qt_raw_gains.json")}
        for name, files in moves.items():
            run = self.runs[name]
            with self.subTest(edit=name):
                for f in files:
                    self.assertTrue(run.before.get(f) != run.full.get(f)
                                    or run.edited.get(f) != run.before.get(f), f)
                for f in keeps.get(name, ()):
                    self.assertEqual(run.before[f], run.full[f], f)

    def test_the_blend_declares_what_no_edit_tells_apart(self):
        # Every change these carry moves another input of the row too: a
        # member's book moves the blend's merge, which the split reads
        # beside it; taxjson.toml is hashed in every record anyway. Kept as
        # the asks named them (TJ-4230), the record is checked for each.
        work = self.runs["blend: an ALLOWLOSS line of an account outside the blend"].root / "work"

        def inputs(out: str) -> dict:
            return json.loads(record_path(work / out).read_text(encoding="utf-8"))["inputs"]
        self.assertLessEqual({"work/.blend_gains_wash.json", "work/.blend_base.json",
                              "work/margin_base.json"}, set(inputs("margin_gains_wash.json")))
        self.assertLessEqual({f"work/.blend{_GAINS_ALL_SUFFIX}", "work/margin_base.json"},
                             set(inputs(f"margin{_GAINS_ALL_SUFFIX}")))
        for out in (f".blend{_GAINS_ALL_SUFFIX}", f"tfsa{_GAINS_ALL_SUFFIX}"):
            self.assertIn("taxjson.toml", inputs(out), out)

    def test_a_crypto_blend_declares_its_members_books(self):
        # The crypto chain's (crypto_base): a member's book moves the
        # blend's merges, which the splits read beside it, so the records
        # are checked (TJ-4232).
        work = self.runs["blend: a crypto member's book changed"].root / "work"

        def inputs(out: str) -> set:
            return set(json.loads(record_path(work / out).read_text(encoding="utf-8"))["inputs"])
        books = {"work/crypto_base.json", "work/crypto2_base.json"}
        self.assertLessEqual(books, inputs(".cryptoblend_base.json"))
        self.assertLessEqual(books, inputs(f".cryptoblend{_GAINS_ALL_SUFFIX}"))
        for a in ("crypto", "crypto2"):
            with self.subTest(account=a):
                self.assertLessEqual({"work/.cryptoblend_gains_wash.json",
                                      "work/.cryptoblend_base.json", f"work/{a}_base.json"},
                                     inputs(f"{a}_gains_wash.json"))
                self.assertLessEqual({f"work/.cryptoblend{_GAINS_ALL_SUFFIX}",
                                      f"work/{a}_base.json"}, inputs(f"{a}{_GAINS_ALL_SUFFIX}"))

    def test_the_blended_gains_declare_the_missing_history(self):
        # A blended account's OPENING line, or a map line for its symbol,
        # moves its book too (its inputs fingerprint), and so the blend's
        # merge: the record is checked (TJ-4230).
        rec = record_path(self.runs["opening line changed"].root / "work/.blend_gains_wash.json")
        self.assertLessEqual({"work/missing_history.state", "ticker.map"},
                             set(json.loads(rec.read_text(encoding="utf-8"))["inputs"]))

    def test_the_merges_declare_what_no_edit_tells_apart(self):
        # Every change these carry moves another input of the merge too
        # (the review of step 12's table, TJ-4184): kept as the merges'
        # asks named them, the record is checked for each.
        work = self.runs["gains: an ALLOWLOSS line added"].root / "work"

        def inputs(out: str) -> dict:
            return json.loads(record_path(work / out).read_text(encoding="utf-8"))["inputs"]
        base = inputs("tfsa_base.json")
        self.assertIn("work/tfsa_inputs.fingerprint", base)
        # A sheltered account has no arrivals file: declared absent all the same.
        self.assertIsNone(base.get("work/tfsa_transfer_costs.json", "undeclared"))
        self.assertIn("work/crypto_sources.list", inputs("crypto_merged.json"))


    def test_the_renders_declare_what_no_edit_tells_apart(self):
        # Each moves with another input of its row (TJ-4260): the wash
        # pass's sheltered books with its own, the native books' three
        # files together, a holdings handoff's wash file only for units
        # moved in from another of your accounts, a crypto account's parses
        # and venue books with its gains, a summary's or report model's book
        # with its gains, the radar's and the lint's books with their gains,
        # the fees' rates with the books; the day the radar counts from, the
        # map and the missing history the edits here do not tell apart, and
        # a summary's diagnostics.
        # The records are checked.
        def inputs(case: str, out: str) -> set:
            rec = record_path(self.runs[case].root / out)
            return set(json.loads(rec.read_text(encoding="utf-8"))["inputs"])
        plain = "gains: an ALLOWLOSS line added"
        books = {"work/margin_base.json", "work/qt_base.json", "work/crypto_base.json"}
        gains = {"work/margin_gains_wash.json", "work/qt_gains_wash.json",
                 "work/crypto_gains_wash.json", "work/tfsa_gains.json", "work/rrsp_gains.json"}
        radar = books | {"work/sheltered_base.json", "work/loss_overrides.json",
                         "work/radar_today.state", "work/margin_gains_wash.json",
                         "work/qt_gains_wash.json", "work/crypto_gains_wash.json"}
        expect = {
            "work/crypto_gains_wash.json": {"work/crypto_base.json", "work/sheltered_base.json",
                                            "work/loss_overrides.json"},
            "reports/margin_holdings.toml": {"work/margin_raw.json", "work/margin_raw_gains.json",
                                             "work/margin_raw_base_gains.json",
                                             "work/margin_gains_wash.json"},
            "reports/crypto_holdings.toml": {"work/crypto_gains.json", "work/crypto_base.json",
                                             "work/crypto_coinbase.json",
                                             "work/crypto_coinbase_transfers.json",
                                             "work/crypto_gains_wash.json"},
            "reports/margin.sum": {"work/margin_base.json", "work/margin_gains.json",
                                   "work/margin_gains.json.diag"},
            "reports/margin_wash.sum": {"work/margin_base.json", "work/margin_blend.diag"},
            "work/margin_report.json": {"work/margin_base.json", "work/margin_gains_wash.json"},
            "work/tfsa_report.json": {"work/tfsa_base.json", "work/tfsa_gains.json"},
            "reports/ccd.rpt": gains, "reports/leaps.rpt": gains,
            "reports/wash_radar_margin.rpt": radar, "reports/wash_radar_COMBINED.rpt": radar,
            "reports/crosslistings.rpt": books | {"work/sheltered_base.json"},
            "reports/fees.rpt": {"work/to_base.csv", "work/margin_ib.json"},
        }
        for out, want in expect.items():
            with self.subTest(out=out):
                self.assertLessEqual(want, inputs(plain, out))
        mapped = "map: a GLOBAL line renames an opening's symbol"
        for out in ("reports/margin_holdings.toml", "reports/wash_radar_margin.rpt",
                    "reports/wash_radar_COMBINED.rpt", "reports/crosslistings.rpt",
                    "reports/fees.rpt"):
            with self.subTest(out=out, input="ticker.map"):
                self.assertIn("ticker.map", inputs(mapped, out))
        # The radar's own, not only through the missing history's maps
        # (the journal's TOBASE line, no opening line).
        for out in ("reports/wash_radar_margin.rpt", "reports/wash_radar_COMBINED.rpt"):
            with self.subTest(out=out, input="ticker.map, no missing history"):
                self.assertIn("ticker.map", inputs(
                    "raw: a journal's legs changed in the transfer evidence", out))
        for out in ("work/crypto_gains_wash.json", "reports/wash_radar_margin.rpt",
                    "reports/wash_radar_COMBINED.rpt"):
            with self.subTest(out=out, input="the missing history"):
                self.assertIn("work/missing_history.state", inputs("opening line changed", out))


    # The store (TJ-4265, TJ-1807).
    def test_a_removed_account_leaves_no_book_a_view_could_read(self):
        # Nor any file of its own: its stamps, its detection notes
        # (TJ-4264; what a view built for it: TestTheSweepStaysInItsFolders).
        run = self.runs["sweep: a taxable account removed"]
        self.assertEqual(_own_files(run.left, "qt"), [],
                         "the run left the removed account's files")
        self.assertNotIn("work/qt_base.json", run.left)
        listed = json.loads((run.root / "work/.deps/run.json").read_text(encoding="utf-8"))
        self.assertEqual(listed["format"], 2)
        self.assertFalse([k for k in listed["instances"] if "/qt_" in k or "/qt." in k])

    def test_an_export_removed_leaves_no_parse(self):
        run = self.runs["sweep: an export removed from an account"]
        for f in ("work/crypto_kraken.json", "work/crypto_kraken.json.diag",
                  "work/crypto_kraken_transfers.json", "work/.deps/crypto_kraken.json.json"):
            with self.subTest(file=f):
                self.assertIn(f, self.runs["gains: an ALLOWLOSS line added"].left)
                self.assertNotIn(f, run.left)

    def test_the_withdrawn_inputs_are_declared_and_no_marker_is_kept(self):
        # Each marker's cascade is a declared input: the set a record names
        # without the lines, the map or the join differs from the one it
        # names with them, which alone rebuilds (freshness `_is_stale`).
        def inputs(case: str, out: str) -> dict:
            rec = json.loads(record_path(self.runs[case].root / out).read_text(encoding="utf-8"))
            return rec["inputs"]
        mh = "work/missing_history.state"
        for out in ("work/margin_gains.json", "work/margin_raw_gains.json"):
            with self.subTest(out=out, input=mh):
                self.assertIn(mh, inputs("opening line changed", out))
                self.assertNotIn(mh, inputs("withdrawn: the last opening line removed", out))
        with self.subTest(out="work/margin_base.json", input=mh):
            self.assertIn(mh, inputs("distributions: an opening line changed",
                                     "work/margin_base.json"))
            self.assertNotIn(mh, inputs("withdrawn: a distribution's last opening line removed",
                                        "work/margin_base.json"))
        with self.subTest(input="ticker.map"):
            self.assertIsNotNone(inputs("map: a GLOBAL line renames an opening's symbol",
                                        "work/tfsa_base.json")["ticker.map"])
            self.assertNotIn("ticker.map", inputs("withdrawn: ticker.map removed",
                                                  "work/tfsa_base.json"))
        with self.subTest(input="the effective map"):
            case = "withdrawn: the journal's join undone"
            joined = json.loads((self.runs[case].root / "joined_record.json").read_text(
                encoding="utf-8"))["inputs"]
            self.assertIn("work/ticker.map.effective", joined)
            self.assertNotIn("ticker.map", joined)
            plain = inputs(case, "work/margin_base.json")
            self.assertIn("ticker.map", plain)
            self.assertNotIn("work/ticker.map.effective", plain)
        for name, run in self.runs.items():
            with self.subTest(edit=name):
                self.assertEqual([f for f in run.left if f.endswith(
                    (".bump", ".missing_history_applied", ".phantoms_applied",
                     ".project_maps_applied", ".wash_inputs.json"))], [])


def _as_81f69b75(root: Path) -> None:
    """work/ as taxjson at 81f69b75 left it: its records format 1, none for
    reports/ and no run.json; the wash passes' record in work/.wash_inputs.json;
    a bump of `invalidate`'s; the books' merge time; no radar day."""
    work = root / "work"
    deps = work / ".deps"
    shutil.rmtree(deps / "reports")
    (deps / "run.json").unlink()
    (work / "radar_today.state").unlink()
    washes = {}
    for rp in deps.glob("*.json"):
        rec = json.loads(rp.read_text(encoding="utf-8"))
        if "wash" in rec:
            washes[rp.name[:-len(".json")]] = rec.pop("wash")
        rp.write_text(json.dumps({**rec, "format": 1}, sort_keys=True) + "\n", encoding="utf-8")
    (work / ".wash_inputs.json").write_text(
        json.dumps({"schema_version": 1, "wash": washes}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    (deps / "qt_sources.list.bump").write_text("0123456789abcdef\n", encoding="utf-8")
    for p in work.glob("*_base.json"):
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc.setdefault("metadata", {})["merged_at"] = "2026-11-01T12:00:00+00:00"
        p.write_text(json.dumps(doc, indent=2), encoding="utf-8")


class TestAnOlderTaxjsonsWork(unittest.TestCase):
    """A project an older taxjson ran (work/ in 81f69b75's format: TJ-4266):
    the first run after the upgrade rebuilds everything, never stops and
    never keeps a file its old record vouched for. A gains file is planted, changed, with its old
    record made to vouch for the change: kept, it would be a stale skip.
    No record vouches and no run.json lists anything, so the sweep removes
    nothing; the files the older taxjson kept beside the records go, by
    their fixed names, and nothing else (TJ-4274)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = cls.root = _copy(Path(cls._tmp.name), "an older taxjson's work")
        _tjs(root, "run", "--no-input")
        cls.full = _built(root)
        _as_81f69b75(root)
        planted = root / "work/margin_gains.json"
        planted.write_text(planted.read_text(encoding="utf-8").replace(
            '"transactions"', '"planted": true, "transactions"', 1), encoding="utf-8")
        rp = record_path(planted)
        rec = json.loads(rp.read_text(encoding="utf-8"))
        import hashlib
        rec["out"] = hashlib.sha256(planted.read_bytes()).hexdigest()
        rp.write_text(json.dumps(rec, sort_keys=True) + "\n", encoding="utf-8")
        for name in (".missing_history_applied", ".phantoms_applied", ".project_maps_applied",
                     ".deps/notes.bump"):
            (root / "work" / name).write_text("1\n", encoding="utf-8")
        (root / "work/saved_by_me.json").write_text("{}\n", encoding="utf-8")
        def files():
            return {p.relative_to(root).as_posix() for d in ("work", "reports")
                    for p in (root / d).rglob("*") if p.is_file()}
        cls.before = files()
        cls.first = _tjs(root, "run", "--no-input")
        cls.files_after = files()
        cls.after = _built(root)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_first_run_builds_what_a_full_run_builds(self):
        self.assertEqual(sorted(self.after), sorted(self.full))
        self.assertEqual([f for f in sorted(self.full) if self.after[f] != self.full[f]], [])
        self.assertNotIn('"planted"', (self.root / "work/margin_gains.json").read_text(
            encoding="utf-8"))

    def test_every_output_is_vouched_for_again_and_the_old_files_are_gone(self):
        work = self.root / "work"
        old = [p.relative_to(work).as_posix() for p in (work / ".deps").rglob("*.json")
               if json.loads(p.read_text(encoding="utf-8")).get("format") != 2]
        self.assertEqual(old, [])
        listed = json.loads((work / ".deps/run.json").read_text(encoding="utf-8"))
        self.assertIn("work/margin_gains.json", listed["instances"])
        for name in (".wash_inputs.json", ".deps/qt_sources.list.bump"):
            with self.subTest(file=name):
                self.assertFalse((work / name).exists())
        rec = json.loads(record_path(work / "margin_gains_wash.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["wash"]["blend"], "blend")
        self.assertEqual(sorted(f for f in self.before - self.files_after
                                if not f.startswith("work/.deps/") or f.endswith(".bump")), [
            "work/.deps/notes.bump", "work/.deps/qt_sources.list.bump",
            "work/.missing_history_applied", "work/.phantoms_applied",
            "work/.project_maps_applied", "work/.wash_inputs.json"])
        self.assertNotIn("merged_at", (work / "margin_base.json").read_text(encoding="utf-8"))


def _own_files(left, account: str) -> list:
    """The files of `left` (relative to the project) that are `account`'s."""
    a = account
    return [f for f in left if f.startswith((
        f"work/{a}_", f"work/.{a}_", f"reports/{a}", f"reports/wash_radar_{a}",
        f"work/.deps/{a}_", f"work/.deps/.{a}_", f"work/.deps/reports/{a}",
        f"work/.deps/reports/wash_radar_{a}"))]


def _files(top: Path) -> dict:
    """Every file under `top` (a link not followed), by path: its bytes, or
    where a link points."""
    out = {}
    for dp, dn, fn in os.walk(top, followlinks=False):
        for f in fn + [d for d in dn if os.path.islink(os.path.join(dp, d))]:
            p = Path(dp) / f
            out[p.relative_to(top).as_posix()] = (
                "-> " + os.readlink(p) if p.is_symlink() else p.read_bytes())
    return out


class TestTheSweepStaysInItsFolders(unittest.TestCase):
    """The review's sweep cases on a built project (TJ-4269, TJ-4270): a
    work/.deps/run.json edited by hand or by another program removes nothing
    of yours and nothing outside work/ and reports/ (the sweep takes its
    names from the table: a folder link inside work/ or reports/, its first
    repro the elections manifest and sends.json through `work/in`, the
    second three files outside the project); a link in an output's place
    goes, not what it points to. An account emptied of its exports is said
    once: its books are removed, not left to delete by hand."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        built = _copy(tmp, "built")
        _tjs(built, "run", "--no-input")
        root = cls.root = tmp / "hostile"
        shutil.copytree(built, root, symlinks=True)
        out = cls.outside = tmp / "outside"
        out.mkdir()
        for n in ("secret.txt", "secret2.txt", "target.txt"):
            (out / n).write_text("keep\n", encoding="utf-8")
        os.symlink("../inputs", root / "work/in")
        os.symlink(out, root / "work/sub")
        os.symlink(out, root / "reports/link")
        os.symlink(out / "target.txt", root / "work/linkfile")
        (out / "parse.json").write_text("{}\n", encoding="utf-8")   # (fees reads it)
        os.symlink(out / "parse.json", root / "work/ghost_webull.json")
        # Files whose names fit a row's (TJ-4273, TJ-4274): the legacy
        # elections manifest (no export group "manifest"), notes of yours.
        (root / "work/margin_manifest.json").write_text('{"elections": {}}\n', encoding="utf-8")
        for rel in ("reports/notes.sum", "reports/mine_holdings.toml"):
            (root / rel).write_text("mine\n", encoding="utf-8")
        (root / "reports/adir").mkdir()
        for rel in ("reports/mynotes.txt", "reports/adir/x.txt"):
            (root / rel).write_text("mine\n", encoding="utf-8")
        (root / "work/saved_by_me.json").write_text("{}\n", encoding="utf-8")  # (fees reads it)
        rf = root / "work/.deps/run.json"
        doc = json.loads(rf.read_text(encoding="utf-8"))
        ghost = {"row": "gains", "account": "ghost", "beside": [
            "work/sub/target.txt", "../taxjson.toml", "inputs/margin/manifest.json",
            "reports/adir/x.txt", "work/in/crypto/sends.json"]}
        for k in ("work/in/margin/manifest.json", "work/in/crypto/sends.json",
                  "work/sub/secret.txt", "reports/link/secret2.txt", "work/linkfile",
                  "work/../taxjson.toml", str(root / "taxjson.toml"), "inputs/qt/questrade_demo.csv",
                  "taxjson.toml", "reports/mynotes.txt", "work/saved_by_me.json", "reports/adir",
                  "reports/adir/x.txt", "work/./../inputs/margin/manifest.json",
                  "reports/../inputs/margin/margin_extra.tt", "work\\..\\taxjson.toml",
                  "work//../taxjson.toml"):
            doc["instances"][k] = ghost
        doc["instances"]["reports/mynotes.txt"] = {"row": "sum", "account": "mynotes"}
        doc["instances"]["work/in_margin.json"] = {"row": "parse", "account": "in",
                                                   "broker": "margin/manifest"}
        # A link where the table's output is, listed as the instance that wrote it.
        doc["instances"]["work/ghost_webull.json"] = {"row": "parse", "account": "ghost",
                                                      "broker": "webull"}
        doc["instances"].update({
            "work/margin_manifest.json": {"row": "parse", "account": "margin", "broker": "manifest"},
            "work/saved_by_me.json": {"row": "parse", "account": "saved", "broker": "by_me"},
            "reports/notes.sum": {"row": "sum", "account": "notes"},
            "reports/mine_holdings.toml": {"row": "holdings", "account": "mine"}})
        rf.write_text(json.dumps(doc), encoding="utf-8")
        cls.inputs = _files(root / "inputs")
        cls.toml = (root / "taxjson.toml").read_bytes()
        cls.out_before = _files(out)
        cls.swept = _tjs(root, "run", "--no-input")
        cls.after_sweep = (_files(root / "inputs"), _files(out))
        _tjs(root, "run", "--no-input")
        # An account emptied of its exports (inputs/qt kept): a full run,
        # the same on an older taxjson's work/ (no run.json), a run of it.
        # An account removed after a view built its full history (TJ-4264).
        r = cls.removed = tmp / "removed"
        shutil.copytree(built, r, symlinks=True)
        _tjs(r, "stats", "--all-history")
        cls.removed_had = (r / f"work/qt{_GAINS_ALL_SUFFIX}").is_file()
        _remove_qt(r)
        _tjs(r, "run", "--no-input")
        # A book of a removed account edited after the run that built it.
        r = cls.edited = tmp / "edited"
        shutil.copytree(built, r, symlinks=True)
        with open(r / "work/qt_base.json", "a", encoding="utf-8") as f:
            f.write("\n")
        _remove_qt(r)
        cls.edited_run = _tjs(r, "run", "--no-input")
        cls.emptied = {}
        for name, argv in (("listed", ()), ("older", ()), ("account", ("--account", "qt"))):
            r = tmp / f"emptied-{name}"
            shutil.copytree(built, r, symlinks=True)
            shutil.move(str(r / "inputs/qt/questrade_demo.csv"), str(tmp / f"{name}.csv"))
            if name == "older":
                (r / "work/.deps/run.json").unlink()
            cls.emptied[name] = (r, _tjs(r, "run", "--no-input", *argv))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_nothing_outside_work_and_reports_goes(self):
        self.assertEqual(self.after_sweep, (self.inputs, self.out_before))
        self.assertEqual(_files(self.root / "inputs"), self.inputs)
        self.assertEqual(_files(self.outside), self.out_before)
        self.assertEqual((self.root / "taxjson.toml").read_bytes(), self.toml)

    def test_nothing_of_yours_goes(self):
        for rel in ("reports/mynotes.txt", "reports/adir/x.txt", "work/saved_by_me.json",
                    "work/in", "work/sub", "reports/link", "work/linkfile",
                    "work/margin_manifest.json", "reports/notes.sum",
                    "reports/mine_holdings.toml"):
            with self.subTest(file=rel):
                self.assertTrue(os.path.lexists(self.root / rel))

    def test_a_file_taxjson_did_not_write_is_kept_and_said(self):
        # A link in an output's place (not what the run built there), and
        # files of yours with the table's names: kept, said once (TJ-4274).
        self.assertTrue(os.path.islink(self.root / "work/ghost_webull.json"))
        self.assertEqual((self.outside / "parse.json").read_text(encoding="utf-8"), "{}\n")
        said = [line for line in self.swept.stdout.splitlines()
                if "the last run listed and this one does not build" in line]
        self.assertEqual(len(said), 1, self.swept.stdout[-2000:])
        self.assertTrue(said[0].lstrip().startswith(("note: kept 3 files", "Info: kept 3 files")))
        for rel in ("work/ghost_webull.json", "reports/notes.sum", "reports/mine_holdings.toml"):
            self.assertIn(f"{rel} (not written by taxjson)", self.swept.stdout)
        self.assertNotIn("work/margin_manifest.json", self.swept.stdout)

    def test_a_book_you_edited_is_kept_and_said(self):
        root, out = self.edited, self.edited_run.stdout
        self.assertTrue((root / "work/qt_base.json").is_file())
        self.assertIn("work/qt_base.json (edited since taxjson wrote it)", out)
        self.assertIn("the last run built and this one does not", out)
        left = [p.relative_to(root).as_posix() for d in ("work", "reports")
                for p in (root / d).rglob("*") if p.is_file()]
        # (the stamps went with the books that did; its notes stay with it)
        self.assertEqual(sorted(f for f in _own_files(left, "qt") if "/.deps/" not in f),
                         ["work/qt_base.json", "work/qt_base.json.diag"])

    def test_a_removed_account_leaves_what_a_view_built_for_it_too(self):
        self.assertTrue(self.removed_had)
        left = [p.relative_to(self.removed).as_posix() for d in ("work", "reports")
                for p in (self.removed / d).rglob("*") if p.is_file()]
        self.assertEqual(_own_files(left, "qt"), [])

    def test_an_emptied_account_is_said_once(self):
        # TJ-4270: a full run says it removed the books; no `STILL EXIST`
        # telling you to delete them. An older work/, or a run of the
        # account alone, removes nothing, so the warning still says so.
        still = "STILL EXIST"
        root, r = self.emptied["listed"]
        self.assertNotIn(still, r.stdout + r.stderr)
        self.assertIn("the last run built and this one does not", r.stdout)
        self.assertIn("skipping account 'qt'", r.stdout + r.stderr)
        self.assertFalse((root / "work/qt_base.json").exists())
        for name in ("older", "account"):
            with self.subTest(run=name):
                root, r = self.emptied[name]
                self.assertIn(still, r.stdout + r.stderr)
                self.assertTrue((root / "work/qt_base.json").exists())


class TestTheLocksAreDeclared(unittest.TestCase):
    """A US project's filed-year lock (`tjs close-year` writes it): declared
    by the gains, the blended gains and the full-history gains (an account's
    own and the blend's), the rows that pass it to the engine
    (`--locked-year`). No figure of the synthetic book moves with one, so
    the records are checked (TJ-4230). A second taxable account makes the
    full history blended."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = cls.root = _copy(Path(cls._tmp.name), "us lock", book="style-usa")
        (root / "inputs/margin2").mkdir()
        (root / "inputs/margin2/extra.tt").write_text(
            "BUYSELL 2024-02-01 09:30:00 QZU.US 10 USD 10.00 100.00 0\n", encoding="utf-8")
        _append(root, "taxjson.toml", '\n[accounts.margin2]\ntype = "taxable"\n')
        _tjs(root, "run", "--no-input")
        _tjs(root, "stats", "--all-history")
        _tjs(root, "close-year", "--yes")
        _tjs(root, "run", "--no-input")
        _tjs(root, "stats", "--all-history")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_gains_rows_declare_the_lock(self):
        self.assertTrue((self.root / "filed/2024.json").is_file())
        for out in ("margin_gains.json", ".blend_gains_wash.json",
                    f"roth{_GAINS_ALL_SUFFIX}", f".blend{_GAINS_ALL_SUFFIX}"):
            rec = json.loads(record_path(self.root / "work" / out).read_text(encoding="utf-8"))
            with self.subTest(out=out):
                self.assertIn("filed/2024.json", rec["inputs"])


# A Wealthsimple-layout export, read without a mapping file.
_WS_CSV = ("transaction_date,settlement_date,account_id,account_type,activity_type,"
           "activity_sub_type,direction,symbol,name,currency,quantity,unit_price,commission,"
           "net_cash_amount\n"
           "2024-01-15,2024-01-16,99900001,TFSA,Trade,BUY,LONG,ZZQ,ZZQ Sample Fund,CAD,100,"
           "10.00,0.00,-1000.00\n")
# A run whose stage declares a folder (none does: injected): the sheltered
# row over one member whose book is a folder.
_INJECTED = ("import sys\n"
             "from pathlib import Path\n"
             "from taxjson.cli import books\n"
             "from taxjson.pipeline import run, stage_table as ST\n"
             "books._cmd_run = lambda args: ST.execute(run._RUN_STAGES, ST.StageCtx(\n"
             "    work=Path('work').resolve(), members=['tfsa'], own=None),\n"
             "    'sheltered')\n"
             "from taxjson.bin.taxjson_run import main\n"
             "sys.argv = ['tjs', 'run', '--no-input']\n"
             "sys.exit(main())\n")


def _folder_case(tmp: Path, name: str):
    root = _copy(tmp, f"folder {name}")
    if name == "mapping":
        (root / "inputs/tfsa/activity.csv").write_text(_WS_CSV, encoding="utf-8")
        (root / "inputs/tfsa/generic.toml").mkdir()
        return _tjs(root, "run", "--no-input")
    if name == "manifest":
        (root / "inputs/margin/manifest.json").unlink()
        (root / "inputs/margin/manifest.json").mkdir()
        return _tjs(root, "run", "--no-input", code=1)
    if name == "view":
        # The loss overrides' state, declared absent (no ALLOWLOSS line), is
        # a folder when a view command builds the full history (TJ-4180).
        _tjs(root, "run", "--no-input")
        (root / "work/loss_overrides.json").mkdir()
        return _tjs(root, "stats", "--all-history", code=2)
    (root / "work/tfsa_base.json").mkdir(parents=True)
    return _tjs(root, "run", code=2, argvs=[sys.executable, "-c", _INJECTED])


class TestAFolderWhereAFileWasExpected(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        names = ("mapping", "manifest", "injected", "view")
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            cls.runs = dict(zip(names, ex.map(lambda n: _folder_case(tmp, n), names)))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_a_folder_at_a_mapping_name_runs_as_before(self):
        # The export needs no mapping: the folder was never read (TJ-4133).
        self.assertNotIn("Traceback", self.runs["mapping"].stderr)

    def test_a_folder_at_the_manifest_keeps_its_refusal(self):
        r = self.runs["manifest"]
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("taxjson-corp-actions: error: manifest at inputs/margin/manifest.json "
                      "cannot be read (Is a directory)", r.stderr)

    def test_a_declared_folder_is_one_line(self):
        r = self.runs["injected"]
        self.assertEqual(r.stderr, "tjs run: error: work/tfsa_base.json is a folder where a file "
                                   "was expected — rename or remove it, then run again\n")

    def test_a_declared_folder_is_one_line_from_a_view_command_too(self):
        # The stale-wash warnings `stats` gives first are its own; the build
        # ends in the one line (a traceback before, TJ-4180).
        r = self.runs["view"]
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.stderr.splitlines()[-1],
                         "tjs stats: error: work/loss_overrides.json is a folder where a file "
                         "was expected — rename or remove it, then run again")


if __name__ == "__main__":
    unittest.main()
