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
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
from _support.ib_statement_rows import HEAD, TRADES_H, _parse_ib

from taxjson.model.transaction import TaxTransaction
from taxjson.lib.purchase_drafts import draft_purchases
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


def _qt(date, action, sym, desc, qty, price, gross, comm, net,
        kind="Trades", acct="99900001"):
    return (f"{date},{date},{action},{sym},{desc},{qty},{price},{gross},"
            f"{comm},{net},CAD,{kind},{acct},Margin")


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


def _ib(sym, when, qty, price, basis, code):
    proceeds = -qty * price
    return (f'Trades,Data,Order,Stocks,CAD,U1234567,{sym},"{when}",{qty},'
            f'{price},0,{proceeds},-1,{basis},0,0,{code}\n')


class TestIbDraftsCapAtTheDeepestShort(unittest.TestCase):
    """Formerly: test_fix_backlog_missing.TestIbDraftsCapAtTheDeepestShort"""

    def test_tj0018_a_sale_of_a_purchase_in_the_files_gets_no_line(self):
        # Ten shares held before the data are sold at 09:00 (IB code C,
        # Basis 250); bought again at 15:00; that purchase is sold in
        # September (code C, its Basis 281). Ten units are missing.
        _, rows, _ = _parse_ib(
            HEAD + TRADES_H
            + _ib("ZZS", "2024-06-03, 09:00:00", -10, 30, -250, "C")
            + _ib("ZZS", "2024-06-03, 15:00:00", 10, 28, 281, "O")
            + _ib("ZZS", "2024-09-03, 10:00:00", -10, 40, -281, "C"))
        keys = TaxTransaction.__dataclass_fields__
        txs = [TaxTransaction(**{k: v for k, v in dict(r, account="margin")
                                 .items() if k in keys}) for r in rows]
        drafts, gaps = draft_purchases(txs, country="canada")
        self.assertEqual([(d.quantity, d.cost, d.sale_date) for d in drafts],
                         [(10.0, 250.0, "2024-06-03")])
        self.assertEqual(gaps, [])


# ------------------------------------------ books older than the inputs
class TestStaleBooks(unittest.TestCase):
    """Formerly: test_fix_backlog_missing.TestStaleBooks"""

    def test_tj0077_tj0175_stale_books_and_the_written_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, "fm", [
                _qt("2025-02-03", "Buy", "SAMPY.TO", "SAMPLE Y CORP", 10,
                    10, -100, -4.95, -104.95),
                _qt("2025-04-10", "Sell", "SAMPZ.TO", "SAMPLE Z CORP", -5,
                    10, 50, -4.95, 45.05)], year=2025)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            for cmd in ("find-missing-history", "sanity", "transfers"):
                self.assertNotIn("not the clean result",
                                 _cli(root, cmd).stderr, cmd)
            # The purchase is added, and no run follows.
            mh = root / "inputs" / "margin" / "missing_history.tt"
            (root / "inputs" / "margin" / "z.tt").write_text(
                "BUYSELL  2022-01-10  09:30:00  SAMPZ.TO  5  CAD  8.00  "
                "40.00  0\n")
            for cmd in ("find-missing-history", "sanity", "transfers"):
                self.assertIn("not the clean result",
                              _cli(root, cmd).stderr, cmd)
            for flag in ("--write-missing-history", "--write-purchases"):
                r = _cli(root, "find-missing-history", flag)
                self.assertEqual(r.returncode, 1, (flag, r.stderr))
                self.assertIn("older than the inputs", r.stderr)
                self.assertIn("nothing was written", r.stderr)
            self.assertFalse(mh.exists())
            self.assertFalse((root / "inputs" / "margin"
                              / "purchases_draft.tt.txt").exists())
            # The purchase taken out again and the books rebuilt: the
            # lines are written, the note wrapped at the width.
            (root / "inputs" / "margin" / "z.tt").unlink()
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            r = _cli(root, "find-missing-history",
                     "--write-missing-history", width=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(mh.is_file())
            lines = r.stderr.splitlines()
            self.assertTrue(any("`tjs run` reads them." in ln
                                for ln in lines), r.stderr)
            for ln in lines:
                self.assertLessEqual(len(ln), 120, ln)
            # The write's own changes (missing_history.tt, the
            # requires_taxjson pin) do not stop a second one.
            r = _cli(root, "find-missing-history",
                     "--write-missing-history")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("already there", r.stderr)


if __name__ == "__main__":
    unittest.main()
