"""Planning low round: report and diagnostic tools.

  R1-211 / S051-01 / S031-04  sum-gains, `sum`, fees-sum and divs-sum
          labels: the non-option column is not 'STOCK', crypto staking
          is not a dividend
  S051-00 the .sum bottom line is GAIN + DIV + PIL, labelled so
  S051-02 sum-gains FILE keeps wash_solver_iterations (and routed rows)
  R1-313  sum-gains TOTAL row pinned; summary.total_gain leaves income out
  S028-07 ccd-sum CLOSES/QTY do not count grant-timing write records
  R1-239 / S028-08 / S028-11  ccd-gains / leaps-gains per-unit columns,
          break-even legacy rows, per-currency totals
  S028-09 / S035-13 / S051-11 / S079-11  unreadable or wrong-shape
          inputs: a one-line refusal, never a traceback or a partial
          report at exit 0; a bare-array book is accepted
  R1-265 / S029-15 / S029-18 / G7-1  taxjson-diff and taxjson-extractors
  S035-07 / S035-08  taxjson-missing-history --account and registered
          accounts
  S051-13 / S051-14  sum-income help text; income rows with no amount
  S077-14 report.json wash total from 'disallowed_amount'

All data is synthetic.

Formerly: test_fixl_planning_reports
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from _style import CapturedWidth
from _support.view_report_tools import SRC, _tool


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _gain(symbol, gain, cost, proceeds, *, cur="CAD", days=30, qty=-1,
          **kw):
    return dict(symbol=symbol, gain=gain, cost=cost, proceeds=proceeds,
                qty=qty,
                currency=cur, days_held=days, date="2025-03-03",
                action="BUYSELL", **kw)


def _div(symbol, amt, cur="CAD"):
    return dict(symbol=symbol, action="DIVIDEND", dividend=amt, gain=0.0,
                currency=cur, date="2025-02-01")


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.tmp = Path(self._d.name)

    def write(self, name, doc):
        p = self.tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(doc, (bytes, str)):
            p.write_bytes(doc if isinstance(doc, bytes) else doc.encode())
        else:
            p.write_text(json.dumps(doc))
        return p


# ------------------------------------------------------------ sum-gains
class TestSumGainsTotals(_Tmp):
    """Formerly: test_fixl_planning_reports.TestSumGainsTotals"""
    BOOK = {"summary": {"year": "2025"}, "transactions": [
        _gain("XEI.TO", 500.0, 1000.0, 1500.0),
        _gain("F:CLZ5.US", 100.0, 1000.0, 1100.0),
        _gain("XEI250620C00030000.TO", 50.0, 100.0, 150.0,
              direction="LONG"),
        _div("XEI.TO", 18.0), _div("XEI.TO", 18.0), _div("XEI.TO", 18.0),
    ]}

    def test_total_row_and_labels(self):
        r = _tool("taxjson_sum_gains", self.write("g.json", self.BOOK))
        self.assertEqual(r.returncode, 0, r.stderr)
        tot = next(ln for ln in r.stdout.splitlines()
                   if ln.startswith("TOTAL "))
        # TOTAL | COST | PROCEEDS | GAIN | NON-OPT | OPT | DIV | PIL | DAYS
        self.assertEqual(tot.split()[1:9],
                         ["704.00", "2,100.00", "2,750.00", "650.00",
                          "600.00", "50.00", "54.00", "0.00"])
        out = r.stdout
        # R1-211 / S051-01: the future is in the non-option figure, and
        # the label no longer says STOCK.
        self.assertRegex(out, r"TOTAL REALIZED NON-OPTION GAIN:\s+600\.00 CAD")
        self.assertIn("futures and crypto", out)
        self.assertNotIn("REALIZED STOCK GAIN", out)
        self.assertNotIn("ASSET: Stocks", out)
        # S051-00: the bottom line says what it adds up.
        self.assertRegex(out, r"GRAND TOTAL \(GAIN\+DIV\+PIL\):\s+704\.00 CAD")
        self.assertNotIn("GRAND TOTAL REALIZED GAIN", out)
        self.assertRegex(out, r"TOTAL DIVIDENDS / STAKING:\s+54\.00 CAD")

    def test_file_path_keeps_solver_and_routed_rows(self):
        doc = dict(self.BOOK, summary={"year": "2025",
                                       "wash_solver_iterations": 2,
                                       "wash_solver_converged": True},
                   manual_reporting_required=[
                       {"symbol": "ZZZ.TO", "qty": 1, "proceeds": 5},
                       {"symbol": "YYY.TO", "qty": 1, "proceeds": 6}])
        p = self.write("g.json", doc)
        by_file = json.loads(_tool("taxjson_sum_gains", "--json", p).stdout)
        by_stdin = json.loads(_tool("taxjson_sum_gains", "--json",
                                    stdin=p.read_text()).stdout)
        for d in (by_file, by_stdin):
            self.assertEqual(d["tainted_routed"], 2)
            self.assertEqual(d["wash_solver_iterations"], 2)


# ------------------------------------------------------- ccd/leaps gains
class TestOptionGainsReports(_Tmp):
    """Formerly: test_fixl_planning_reports.TestOptionGainsReports"""
    def test_per_unit_columns_are_derived(self):
        doc = {"transactions": [
            dict(symbol="AAPL260116C00200000.US", direction="SHORT",
                 qty=1.0, cost=-100.0, proceeds=-300.0, gain=200.0,
                 currency="USD", date="2025-03-03", days_held=10),
            dict(symbol="AAPL260116C00150000.US", direction="LONG",
                 qty=2.0, cost=1000.0, proceeds=1600.0, gain=600.0,
                 currency="USD", date="2025-03-03", days_held=10)]}
        p = self.write("g.json", doc)
        ccd = _tool("taxjson_ccd_gains", p).stdout
        lg = _tool("taxjson_leaps_gains", p).stdout
        # ccd.rpt shows a short call's legs as PREMIUM / BUYBACK in
        # real-world orientation (S028-04): cost -100 -> premium 100.
        self.assertIn("PREM/QTY", ccd)
        self.assertRegex(ccd, r"100\.0000\s+300\.0000\s+200\.0000")
        self.assertRegex(lg, r"500\.0000\s+800\.0000\s+300\.0000")
        self.assertIn("(amounts in USD)", ccd)

    def test_grant_row_has_no_negative_zero(self):
        doc = {"transactions": [
            dict(symbol="AAPL260116C00200000.US", direction="SHORT",
                 qty=1.0, cost=-428.5, proceeds=-0.0, gain=428.5,
                 grant=True, currency="CAD", date="2025-03-03")]}
        out = _tool("taxjson_ccd_gains", self.write("g.json", doc)).stdout
        self.assertNotIn("-0.00", out)

    def test_break_even_legacy_row_has_one_direction(self):
        doc = {"transactions": [
            dict(symbol="XYZ260116C00050000.US", qty=1.0, cost=250.0,
                 proceeds=250.0, gain=0.0, currency="USD",
                 date="2025-03-03")]}
        p = self.write("g.json", doc)
        self.assertNotIn("XYZ.US", _tool("taxjson_ccd_gains", p).stdout)
        self.assertIn("XYZ.US", _tool("taxjson_leaps_gains", p).stdout)

    def test_mixed_currencies_are_totalled_apart(self):
        doc = {"transactions": [
            dict(symbol="AAA260116C00010000.US", direction="SHORT", qty=1,
                 cost=-100.0, proceeds=0.0, gain=100.0, currency="USD",
                 date="2025-03-03"),
            dict(symbol="BBB260116C00010000.TO", direction="SHORT", qty=1,
                 cost=-100.0, proceeds=0.0, gain=100.0, currency="CAD",
                 date="2025-03-03")]}
        out = _tool("taxjson_ccd_gains", self.write("g.json", doc)).stdout
        self.assertNotRegex(out, r"(?m)^TOTAL\s+200\.00$")
        self.assertRegex(out, r"(?m)^TOTAL\s+CAD\s+100\.00$")
        self.assertRegex(out, r"(?m)^TOTAL\s+USD\s+100\.00$")


# ------------------------------------------------------- sum-income
class TestSumIncome(_Tmp):
    """Formerly: test_fixl_planning_reports.TestSumIncome"""
    def test_missing_amount_is_refused(self):
        rows = [dict(action="DIVIDEND", date="2025-03-03", symbol="MSFT.US",
                     currency="USD", account="margin"),
                dict(action="TAX", date="2025-03-03", symbol="MSFT.US",
                     currency="USD", account="margin", net_amount=None)]
        r = _tool("taxjson_sum_income", self.write("b.json",
                                                   {"transactions": rows}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("no amount", r.stderr)
        self.assertNotIn("TOTAL NET INCOME", r.stdout)

    def test_amounts_present_still_sum(self):
        rows = [dict(action="DIVIDEND", date="2025-03-03", symbol="MSFT.US",
                     currency="USD", gross_amount=37.5, net_amount=31.87),
                dict(action="TAX", date="2025-03-03", symbol="MSFT.US",
                     currency="USD", net_amount=5.63)]
        r = _tool("taxjson_sum_income", self.write("b.json",
                                                   {"transactions": rows}))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"TOTAL NET INCOME\s+31\.87")

    def test_help_names_the_real_input(self):
        r = _tool("taxjson_sum_income", "--help")
        self.assertNotIn("taxjson_income.py", r.stdout)
        self.assertIn("base", r.stdout)


# ------------------------------------------------------- report.json
class TestReportJsonWashTotal(unittest.TestCase):
    """Formerly: test_fixl_planning_reports.TestReportJsonWashTotal"""
    def test_disallowed_amount_is_read(self):
        sys.path.insert(0, str(SRC))
        from taxjson.lib.account_report import build_account_report
        # The US engine's wash record carries only disallowed_amount.
        rep = build_account_report(
            {"summary": {"year": "2025"}, "transactions": [],
             "wash_sales": [{"loss_tx_id": "x", "date": "2025-03-03",
                             "disallowed_amount": 1000.0}]}, "margin")
        self.assertEqual(rep["wash"], {"count": 1, "total_disallowed": 1000.0})


if __name__ == "__main__":
    unittest.main()
