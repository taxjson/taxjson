"""Backlog fix round 7 (wave B) of 2026-10-29, group `forms`: form
export, carryover and the cash view.

TJ-0895  a commission-only disposal of unknown-cost units: its manual
         row keeps net proceeds -2, never "proceeds 2".
TJ-0896  US carryover: a loss still carried after the last year with a
         sale takes each later year's 3,000 up to the project year.
TJ-0898  a closed year's T5 box 18 capital-gains dividends stay in the
         carry-back room the next year's carryover offers.
TJ-0901  the standalone Form 8949 takes its year from the gains files
         (or the trade date), not a December trade's January settlement.
TJ-0904  taxjson-carryover --filed-lock YEAR=PATH refuses a lock of
         another year than YEAR.
TJ-0905  the standalone form export reads a gains file named twice once.
TJ-0889  `tjs cash`: a copied line keeps its book (at=alpha), so the
         copy reads into the book the original did.
TJ-0693  `tjs cash`: a stock-lending receipt is a #CREDIT line and an
         other credit, never a fee paid.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN*, ZZ*), account names (margin), amounts under
1,000 (or round test figures).

Formerly: test_fix_1029_r7b_forms
"""
import json
import tempfile
import unittest
from pathlib import Path

from _support.filing_forms_project import _tjs, _project, _run


class TestCashLineKeepsItsBook(unittest.TestCase):
    """TJ-0889: one taxable account keeps two foreign-cash books by
    hand (at=alpha, at=beta). `tjs cash` printed their lines without
    at=, so the documented copy into a .tt file read every line into
    the folder's default book (margin/tt).

    Formerly: test_fix_1029_r7b_forms.TestCashLineKeepsItsBook"""

    TT = ("BUYSELL 2025-06-02 10:00:00 SYN.TO 1 CAD 10 10 0\n"
          "CASHOPEN 2025-01-01 USD 100 130 at=alpha\n"
          "CASHOPEN 2025-01-01 USD 200 280 at=beta\n"
          "CASHMOVE 2025-06-02 USD -10 spot at=alpha\n")

    def test_copy_keeps_book(self):
        from taxjson.lib import cash_events as CE
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d) / "p", tt=self.TT,
                            settings="option_grant_timing_since = 2025\n")
            _run(self, root)
            r = _tjs(root, "cash", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            rows = json.loads(r.stdout)["rows"]
            self.assertEqual([(x["book"], x["tt"]) for x in rows], [
                ("margin/alpha", "CASHOPEN 2025-01-01 USD 100.00 130.00 "
                                 "at=alpha"),
                ("margin/beta", "CASHOPEN 2025-01-01 USD 200.00 280.00 "
                                "at=beta"),
                ("margin/alpha", "CASHMOVE 2025-06-02 USD -10.00 spot "
                                 "at=alpha")])
            text = _tjs(root, "cash")
            self.assertIn("at=beta", text.stdout)
            cfg = {"accounts": {"margin": {"type": "taxable"}}}
            before = [x["book"] for x in
                      CE.collect(root, cfg, "canada", {})["lines"]]
            (root / "inputs" / "margin" / "trades.tt").write_text(
                self.TT.splitlines()[0] + "\n"
                + "\n".join(x["tt"] for x in rows) + "\n")
            after = [x["book"] for x in
                     CE.collect(root, cfg, "canada", {})["lines"]]
            self.assertEqual(before, after)
            self.assertEqual(after, ["margin/alpha", "margin/beta",
                                     "margin/alpha"])

    def test_unit_default_book_needs_no_at(self):
        from taxjson.lib import cash_events as CE
        books = CE.Books()
        # A folder of .tt files only: its default book is <label>/tt.
        self.assertIsNone(CE.at_token({"label": "margin",
                                       "book": "margin/tt"}, books))
        self.assertEqual(CE.at_token({"label": "margin",
                                      "book": "margin/alpha"}, books),
                         "alpha")
        ev = {"kind": "CASHOPEN", "date": "2025-01-01", "currency": "USD",
              "units": 1.0, "cost": 2.0}
        self.assertEqual(CE.tt_line(ev), "CASHOPEN 2025-01-01 USD 1.00 2.00")
        self.assertEqual(CE.tt_line(ev, at="alpha"),
                         "CASHOPEN 2025-01-01 USD 1.00 2.00 at=alpha")


class TestCashLendingReceiptIsNoFee(unittest.TestCase):
    """TJ-0693: a Questrade LFJ row (stock lending income, +10 CAD) was
    listed by `tjs cash` as `#FEE ... +10.00` and counted in TOTAL FEES
    (fees.CAD 10).

    Formerly: test_fix_1029_r7b_forms.TestCashLendingReceiptIsNoFee"""

    CSV = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
           "Activity Type,Account #,Account Type\n"
           "2025-06-03,2025-06-03,LFJ,,Stock lending income,0,0,10,0,10,"
           "CAD,Other,99900001,Margin\n")

    def test_receipt_is_a_credit(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d) / "p",
                            settings="option_grant_timing_since = 2025\n")
            (root / "inputs" / "margin" / "cash.csv").write_text(self.CSV)
            _run(self, root)
            r = _tjs(root, "cash", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual(doc["totals"]["fees"], {})
            self.assertEqual(doc["totals"]["credits"], {"CAD": 10.0})
            tts = [x["tt"] for x in doc["rows"]]
            self.assertIn("#CREDIT 2025-06-03 CAD +10.00", tts)
            text = _tjs(root, "cash").stdout
            self.assertNotIn("TOTAL FEES", text)
            self.assertIn("TOTAL OTHER CREDITS: 10.00 CAD", text)

    def test_unit_fee_paid_stays_fee(self):
        from taxjson.lib import cash_events as CE
        from taxjson.lib import cash_lines as CL
        self.assertEqual(CE.tt_line(CL.flow("2025-03-05", "USD", -2.0)),
                         "#FEE 2025-03-05 USD -2.00")
        self.assertEqual(CE.tt_line(CL.flow("2025-03-05", "USD", 2.0)),
                         "#CREDIT 2025-03-05 USD +2.00")


if __name__ == "__main__":
    unittest.main()
