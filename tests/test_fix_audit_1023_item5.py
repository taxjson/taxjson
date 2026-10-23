"""Workflow audit 2026-10-23, items 5 and 11: only a return of capital
is a return of capital.

* 5: slip-audit netted every ADJUST row of the year into T3 box 42, so a
  reinvested `[[distributions]]` entry or a notional distribution (ACB
  raises) offset the real return of capital: a correct box 42 showed as
  'differs', with a suggested `ADJUST ... type=roc` that would cut the
  ACB by the reinvested amount.
* 11: an s.86.1 spin-off's parent cost reduction was 'capital returned'
  in roc-sum and the reason 'return-of-capital' in sanity.

Every fixture is SYNTHETIC: made-up tickers ZZ*, account U1234567.
"""
import json
import re
import tempfile
import unittest
from pathlib import Path

from _qa_project import console, project, tj, toml
from tax_rules import rule

from taxjson.lib import positions_check as PC
from taxjson.lib.income_dating import is_return_of_capital


def _adj(amount, typ="", rid="r1", **kw):
    return dict({"action": "ADJUST", "symbol": "ZZT.TO", "currency": "CAD",
                 "date": "2025-09-30", "net_amount": amount, "type": typ,
                 "id": rid}, **kw)


class TestClassifier(unittest.TestCase):

    @rule("CA-SLIP-01", "CA-DIST-01", "CA-DIST-02", "CA-CORP-05")
    def test_kinds(self):
        roc = [_adj(-10.0, "roc"),                 # a broker's ROC
               _adj(10.0, "roc"),                  # its reversal
               _adj(-12.5),                        # a .tt ADJUST, no type
               _adj(12.5),                         # the January pair's
               _adj(-5.0, "dist", "DIST-ZZT.TO-2025-12-29-margin")]
        not_roc = [_adj(50.0, "dist", "DIST-ZZT.TO-2025-12-29-margin"),
                   _adj(40.0, "dist"),             # RBC notional
                   _adj(-300.0, corp_event_id="20250701-zzp-zzt-0001",
                        corp_election="rollover_s_86_1"),
                   _adj(-300.0, "spinoff_355"),
                   _adj(7.0, rid="WASH_x__1"),
                   dict(_adj(-1.0, "roc"), action="DIVIDEND")]
        for r in roc:
            self.assertTrue(is_return_of_capital(r), r)
        for r in not_roc:
            self.assertFalse(is_return_of_capital(r), r)

    @rule("CA-CORP-05")
    def test_sanity_reason_names_the_spin_off(self):
        rows = [_adj(-300.0, symbol="ZZP.US",
                     corp_event_id="20250701-zzp-zzt-0001"),
                _adj(50.0, "dist", "DIST-ZZT.TO-2025-12-29-margin"),
                _adj(-10.0, "roc", symbol="ZZR.TO")]
        self.assertEqual(PC.roc_symbols(rows), {"ZZR.TO"})
        self.assertEqual(PC.spinoff_symbols(rows), {"ZZP.US"})
        res = PC.compare_cost(
            symbol="ZZP.US", broker_cost=100.0, cost_currency="CAD",
            cost_kind="book value", position_currency="CAD", base="CAD",
            country="canada", filing={"total_cost": 150.0,
                                      "deferred_wash": 0.0},
            own=150.0, native=None, pooled=False, roc=False,
            spinoff=True, tol_abs=1.0, tol_rel=0.001)
        self.assertIn("spin-off", res["reasons"])
        self.assertNotIn("return-of-capital", res["reasons"])
        self.assertIn("spin-off", PC.REASON_TEXT)


_TT = """BUYSELL 2025-01-06 10:00:00 ZZT.TO 100 CAD 20 2000 0
DIVIDEND 2025-06-30 09:30:00 ZZT.TO 100 CAD 0.30 30.00 record=2025-06-27 label=distribution
ADJUST 2025-09-30 09:30:00 ZZT.TO CAD -10.00 type=roc record=2025-09-26
ADJUST 2025-12-31 16:00:00 ZZT.TO CAD 40.00 type=dist
"""

_DIST = ('[[distributions]]\nsymbol = "ZZT.TO"\n'
         'record_date = 2025-12-29\nper_share = 0.50\n')

_SLIPS = """year = 2025

[[slip]]
type = "T3"
issuer = "ZZT Income Trust"
account = "margin"
currency = "CAD"
security = "ZZT.TO"
boxes = { 21 = 120.00, 42 = 10.00 }
"""


class TestSlipAuditDistributions(unittest.TestCase):
    """A T3 box 42 of 10.00 and the books' return of capital of 10.00,
    beside a reinvested [[distributions]] entry (+50.00) and a notional
    distribution (.tt type=dist, +40.00) that raise the ACB."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = project(cls.tmp.name, "p",
                           {"margin/trades.tt": _TT,
                            "slips/slips.toml": _SLIPS})
        (cls.root / "taxjson.toml").write_text(toml(year=2025) + _DIST)
        tj(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @rule("CA-SLIP-01", "CA-SLIP-03", "CA-DIST-01", "CA-DIST-02")
    def test_box_42_agrees_and_no_roc_suggested(self):
        r = tj(self.root, "slip-audit", "--json", check=False)
        rep = json.loads(r.stdout)
        (acc,) = rep["accounts"]
        (g,) = acc["groups"]
        lines = {ln["category"]: ln for b in g["buckets"]
                 for ln in b["lines"]}
        self.assertEqual((lines["roc"]["slip"], lines["roc"]["books"],
                          lines["roc"]["status"]), (10.0, 10.0, "ok"))
        for tt in rep["suggestions"].get("tt_lines") or []:
            for ln in tt["lines"]:
                self.assertFalse(ln.startswith("ADJUST"), ln)
        r = tj(self.root, "slip-audit", check=False)
        self.assertRegex(r.stdout, r"Return of capital\s+T3 42\s+10\.00\s+"
                                   r"10\.00\s+\+0\.00\s+ok")
        self.assertNotIn("type=roc", r.stdout)

    @rule("CA-SLIP-01", "CA-DIST-01")
    def test_roc_sum_lists_the_raises_apart(self):
        r = tj(self.root, "roc-sum", "--json")
        doc = json.loads(r.stdout)
        self.assertEqual(doc["totals"], {"CAD": 10.0})
        self.assertEqual([(x["symbol"], x["capital_returned"])
                          for x in doc["rows"]], [("ZZT.TO", 10.0)])
        other = sorted((o["acb_change"], o["kind"])
                       for o in doc["other_adjustments"])
        self.assertEqual(other, [
            (40.0, "notional distribution"),
            (50.0, "reinvested distribution ([[distributions]])")])
        r = tj(self.root, "roc-sum")
        self.assertIn("TOTAL CAPITAL RETURNED (ACB reduced): 10.00 CAD",
                      r.stdout)
        self.assertIn("OTHER ACB ADJUSTMENTS (not a return of capital, "
                      "not in T3 box 42", r.stdout)


_IB = """Statement,Header,Field Name,Field Value
Statement,Data,BrokerName,Interactive Brokers
Statement,Data,Title,Activity Statement
Statement,Data,Period,"January 1, 2024 - December 31, 2024"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,U1234567
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,USD,ZZP,"2024-02-15, 10:00:00",100,40.00,40.00,-4000.00,-1.00,4001.00,0,0,O
Corporate Actions,Header,Asset Category,Currency,Report Date,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code
Corporate Actions,Data,Stocks,USD,2024-07-02,"2024-07-01, 20:25:00","ZZP(US0000000921) Spinoff  1 for 10 (ZZT, ZZT CORP, US0000000922)",10,0,250,0,
Corporate Actions,Data,Total,,,,,,0,250,0,
Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code
Open Positions,Data,Summary,Stocks,USD,ZZP,100,1,37.51,3751.00,41,4100,349,
Open Positions,Data,Summary,Stocks,USD,ZZT,10,1,25.00,250.00,30,300,50,
"""


class TestSpinOffIsNotRoc(unittest.TestCase):
    """An s.86.1 spin-off (IB) elected rollover_s_86_1: the parent's
    cost reduction is no capital returned (roc-sum) and sanity names the
    spin-off, not a return of capital, as the cost difference's reason."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = project(cls.tmp.name, "ib", {"margin/ib_2024.csv": _IB})
        r = tj(cls.root, "run", "--no-input", check=False)
        m = re.search(r"--set (\S+?)=<", console(r))
        assert m, console(r)[-3000:]
        tj(cls.root, "elect", "margin", "--set",
           f"{m.group(1)}=rollover_s_86_1", "--hint",
           "allocated_acb_cad=300")
        tj(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @rule("CA-CORP-05")
    def test_roc_sum_leaves_the_allocation_out(self):
        doc = json.loads(tj(self.root, "roc-sum", "--json").stdout)
        self.assertEqual((doc["rows"], doc["totals"]), ([], {}))
        (o,) = doc["other_adjustments"]
        self.assertEqual(o["symbol"], "ZZP.US")
        self.assertLess(o["acb_change"], 0)
        self.assertEqual(o["kind"],
                         "spin-off cost allocation (rollover_s_86_1)")
        out = tj(self.root, "roc-sum").stdout
        self.assertNotIn("TOTAL CAPITAL RETURNED", out)
        self.assertIn("No return of capital in tax year 2024.", out)

    @rule("CA-CORP-05")
    def test_sanity_reason_is_the_spin_off(self):
        r = tj(self.root, "sanity", "margin=inputs/margin/ib_2024.csv",
               "--json", check=False)
        rows = {c["symbol"]: c for c in json.loads(r.stdout)["cost"]["rows"]}
        reasons = rows["ZZP.US"]["reasons"]
        self.assertIn("spin-off", reasons)
        self.assertNotIn("return-of-capital", reasons)


if __name__ == "__main__":
    unittest.main()
