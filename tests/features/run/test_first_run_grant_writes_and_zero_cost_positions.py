"""Backlog group `missing` (2026-10-25): find-missing-history, its drafts
and transfers.

TJ-0008  a transfer row stating a book value of 0 was drafted as a $0.00
         purchase; filled in with its date only it booked a $0 lot. The
         line now leaves the cost as COST (the run refuses it until it is
         filled in), and a row with no id no longer says "(row ?)".
TJ-0009  a transfer-in the run reports PARTLY covered by .tt purchases
         was "not drafted: ... a line would count it twice": the other
         units are drafted now, at COST, with a CHECK.
TJ-0017  a grant-timed written option sold with no purchase in the
         files was called "NOT in the totals" while its premium was.
TJ-0018  one draft line per sale that went short: more than the deepest
         short when a purchase in the files came between two of them.
TJ-0043  `tjs transfers`' `! ` lines were over 100 columns and wrapped.
TJ-0044  the run's closing list named one $0 lot twice (part sold, part
         held): "2 positions at a $0 cost (SAMPK.TO, SAMPK.TO)".
TJ-0055  `tjs transfers` TYPE showed the broker's free text (with the
         other account's number) instead of a transfer type.
TJ-0074  the drafts' slower own-move tier paired a leg of an in-kind
         move (to a registered account) with an outside arrival.
TJ-0077  find-missing-history, sanity and transfers said nothing about
         books older than the inputs, and --write-missing-history wrote
         an OPENING line for a position a just-added purchase fixes.
TJ-0175  the --write-missing-history note was one unwrapped line.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_backlog_missing
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from _style import CapturedWidth
from _support.ib_statement_rows import HEAD, TRADES_H

from taxjson.lib import first_run as FR
import _paths

REPO = _paths.REPO
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _cli(root, *args, width=None):
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"),
               TAXJSON_OFFLINE="1", NO_COLOR="1",
               TAXJSON_LOCAL_TZ="America/Toronto")
    if width is not None:
        env["TAXJSON_WIDTH"] = str(width)
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=300)


_QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
            "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
            "Activity Type,Account #,Account Type")


_TOML = """\
[settings]
country = "canada"
local_timezone = "America/Toronto"
tax_date = "settle"
year = {year}
base_currency = "CAD"
source_currencies = []
{extra}
[accounts.margin]
type = "taxable"
"""


def _project(tmp, name, rows, *, year=2024, extra="", files=None):
    root = Path(tmp) / name
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(_TOML.format(year=year, extra=extra))
    if rows:
        (root / "inputs" / "margin" / "activity.csv").write_text(
            "\n".join([_QT_HEAD] + rows) + "\n")
    for fname, text in (files or {}).items():
        (root / "inputs" / "margin" / fname).write_text(text)
    return root


# ------------------------------------------------- what the gains booked
class TestGrantTimedWrites(unittest.TestCase):
    """Formerly: test_fix_backlog_missing.TestGrantTimedWrites"""

    def test_tj0017_a_write_booked_at_grant_is_in_the_totals(self):
        opt = "ZZC241220C00040000.TO"
        recs = [
            {"symbol": opt, "direction": "SHORT", "grant": True, "qty": 1.0,
             "id": "w1", "date": "2024-06-03", "date_settle": "2024-06-04"},
            {"symbol": opt, "direction": "SHORT", "grant": False,
             "qty": 1.0, "id": "b1", "date": "2024-06-03",
             "date_settle": "2024-06-04"},
            {"symbol": opt, "direction": "SHORT", "grant": True, "qty": 1.0,
             "id": "w2", "date": "2024-09-03", "date_settle": "2024-09-04"}]
        row = SimpleNamespace(
            candidate=SimpleNamespace(symbol=opt, account="margin"),
            in_year_short_sales=[("w1", 1.0, 1.0), ("w2", 1.0, 1.0)])
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "margin_gains.json").write_text(
                json.dumps({"transactions": recs}))
            got = FR.engine_booking(work, [row], 2024)
            self.assertEqual(got[(opt, "margin")], FR.BOOKED_SHORT_COVER)
            # Not booked at grant (no grant records): the buy closed one
            # write, the other is an open short at the year's end.
            (work / "margin_gains.json").write_text(
                json.dumps({"transactions": [recs[1]]}))
            got = FR.engine_booking(work, [row], 2024)
            self.assertEqual(got[(opt, "margin")], FR.BOOKED_OPEN)

    def test_tj0017_through_the_run(self):
        fii = ("Financial Instrument Information,Header,Asset Category,"
               "Symbol,Description,Conid,Security ID,Underlying,Listing "
               "Exch,Multiplier,Expiry,Delivery Month,Type,Strike,Code\n"
               "Financial Instrument Information,Data,Equity and Index "
               "Options,ZZC   241220C00040000,ZZC 20DEC24 40 C,990000201,,"
               "ZZC,CDE,100,2024-12-20,2024-12,C,40,\n")

        def opt(when, qty, price, basis, code):
            return (f'Trades,Data,Order,Equity and Index Options,CAD,'
                    f'U1234567,ZZC 20DEC24 40 C,"{when}",{qty},{price},0,'
                    f'{-qty * price * 100},-1,{basis},0,0,{code}\n')
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp, "b45", [], extra="option_grant_timing_since = 2024\n",
                files={"ib.csv": (
                    HEAD + fii + TRADES_H
                    + opt("2024-06-03, 09:00:00", -1, 3, -250, "C")
                    + opt("2024-06-03, 15:00:00", 1, 2.8, 281, "O")
                    + opt("2024-09-03, 10:00:00", -1, 4, -281, "C"))})
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("NOT in the totals", r.stdout + r.stderr)
            d = json.loads(_cli(root, "sum", "--json").stdout)
            self.assertEqual(d["no_purchase_uncovered"], [])
            self.assertEqual([x["booked"] for x in d["no_purchase_in_totals"]],
                             ["short_cover"])


# ----------------------------------------------------- the closing list
class TestZeroCostPositionNamedOnce(unittest.TestCase):
    """Formerly: test_fix_backlog_missing.TestZeroCostPositionNamedOnce"""

    def test_tj0044_one_lot_part_sold_part_held_is_one_position(self):
        doc = {"year": 2024,
               "zero_cost_sold": [{"symbol": "SAMPK.TO",
                                   "account": "margin"}],
               "zero_cost_held": [{"symbol": "SAMPK.TO", "account": "margin",
                                   "quantity": 30.0}]}
        lines = [t for _k, t in FR.concise_items(doc) if "$0 cost" in t]
        self.assertEqual(len(lines), 1)
        self.assertIn("1 position at a $0 cost (SAMPK.TO)", lines[0])
        self.assertNotIn("SAMPK.TO, SAMPK.TO", lines[0])
        text = "\n".join(FR.render(doc, width_=0))
        self.assertIn("1 position at a $0 cost (1 sold in 2024, 1 still "
                      "held): SAMPK.TO (margin).", text)


if __name__ == "__main__":
    unittest.main()
