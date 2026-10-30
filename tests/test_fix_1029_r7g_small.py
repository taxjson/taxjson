"""Fix round 7, wave G (small): backlog TJ-1334, TJ-1337.
Synthetic data only: made-up tickers (SYN, FND, ZZP, ZZS), issuers and broker
accounts (99900001)."""
import json
import tempfile
import unittest

from _qa_project import tj
from tax_rules import rule
from taxjson.lib import slip_audit as SA
from test_fix_1029_r7c_slips import CFG, _div, _interest, _line, _project
from test_fix_1029_r7e_slips import _cli_project, _qt, _qt_buy, _qt_div
from test_fix_1029_r7f_useng import (
    _TWO, _blocks, _buy, _dis, _held, _run_355, _sell_spun, _trade, _PARENT)


class UnnamedT3BesideANetNegativePayer(unittest.TestCase):
    """TJ-1334: a payer's net negative income holds none of a T3 of no named
    fund, and never makes it more than its own amount."""

    T3 = ('year = 2025\n[[slip]]\ntype = "T3"\naccount = "margin"\n'
          'issuer = "Synthetic Fund"\nboxes = { 49 = 100 }\n')

    def _status(self, other, div=130.0, paid=30.0):
        rows = [_div("SYN.TO", "2025-06-02", div, source="y.csv"),
                _interest("2025-06-02", paid, source="y.csv")]
        if other:
            rows.append(_div("FND.TO", "2025-06-02", other, source="x.csv"))
        return _line(SA.audit(_project(rows, self.T3), CFG),
                     "interest")["status"]

    @rule("CA-SLIP-01")
    def test_it_takes_no_more_than_its_own_amount(self):
        # 130 - 100 + 30 = 60: over the minimum, however negative the other.
        for other in (-50.0, -10.01, -0.01, -5000.0):
            with self.subTest(other=other):
                self.assertEqual(self._status(other), "differs")

    @rule("CA-SLIP-01")
    def test_what_its_own_amount_leaves_under_50_is_still_excused(self):
        # 110 - 100 + 30 = 40.
        self.assertEqual(self._status(-50.0, div=110.0), "under-50")

    @rule("CA-SLIP-01")
    def test_the_other_payers_income_still_holds_it(self):
        self.assertEqual(self._status(100.0, div=40.0, paid=40.0), "differs")
        self.assertEqual(self._status(0.0, div=110.0), "under-50")

    @rule("CA-SLIP-01")
    def test_through_the_cli(self):
        interest = ("2025-06-30,2025-06-30,INT,,INTEREST,0,0,30.00,0,30.00,"
                    "CAD,Interest")
        with tempfile.TemporaryDirectory() as td:
            root = _cli_project(td, {
                "margin/a.csv": _qt("99900001", _qt_buy("FND.TO"),
                                    _qt_div("FND.TO", -50.0)),
                "margin/b.csv": _qt("99900002", _qt_buy("SYN.TO"),
                                    _qt_div("SYN.TO", 130.0), interest),
                "slips/slips.toml": self.T3})
            rep = json.loads(tj(root, "slip-audit", "--json",
                                check=False).stdout)
        status = [ln["status"] for a in rep["accounts"] for g in a["groups"]
                  for b in g["buckets"] for ln in b["lines"]
                  if ln["category"] == "interest"]
        self.assertEqual(status, ["differs"])


class Questrade355KnownBugTwoAccountTotals(unittest.TestCase):
    """TJ-1337, open: the summed "ON n SHS" of two broker accounts equals the
    shares of the whole folder, so a later lot reads as due bill though one
    account's own count shows it was not paid on. These are v0.38.0's figures,
    pinned so the known bug does not turn into another one. A fix needs each
    account's count against that account's lots; it should change them to 20
    ZZS at 40, June lot 300 (Reg. §1.358-2)."""

    def test_an_older_lot_sold_between_the_dates(self):
        doc, _out, _ = _run_355({'qt.csv': (
            _buy('2024-01-08', 100, 100) + _buy('2024-01-09', 100, 100, _TWO)
            + _buy('2025-06-05', 100, 300)
            + _trade('2025-06-11', 'Sell', 'ZZP', _PARENT, -100, 150, _TWO)
            + _dis(100, 10) + _dis(100, 10, _TWO) + _sell_spun(20, 200))})
        self.assertEqual(_blocks(doc), [('LONG_TERM', 10.0, 10.0),
                                        ('SHORT_TERM', 10.0, 30.0)])
        self.assertAlmostEqual(_held(doc), 360.0)

    def test_an_older_purchase_in_no_export(self):
        doc, _out, _ = _run_355({'qt.csv': (
            _buy('2024-01-08', 100, 100) + _buy('2025-06-10', 100, 300)
            + _dis(100, 10) + _dis(100, 10, _TWO) + _sell_spun(10, 100))})
        self.assertEqual(_blocks(doc), [('LONG_TERM', 10.0, 10.0)])
        self.assertAlmostEqual(_held(doc), 360.0)


if __name__ == '__main__':
    unittest.main()
