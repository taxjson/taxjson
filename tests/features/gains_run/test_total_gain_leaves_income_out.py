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
from _support.view_report_tools import SRC, _tool, _row


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


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


class TestTotalGainLeavesIncomeOut(_Tmp):
    """Formerly: test_fixl_planning_reports.TestTotalGainLeavesIncomeOut"""
    def test_helper_ignores_income_rows(self):
        sys.path.insert(0, str(SRC))
        from taxjson.lib.pipeline import _trade_gain_total
        rows = [{"action": "BUYSELL", "gain": 500.0},
                {"action": "DIVIDEND", "gain": 1.0},
                {"action": "DIVIDEND_IN_LIEU", "gain": 1.0}]
        self.assertEqual(_trade_gain_total(rows), 500.0)

    def test_gains_run_total(self):
        book = self.write("book.json", {"transactions": [
            _row("2025-01-06", "XEI.TO", 100, -1000.0),
            _row("2025-03-03", "XEI.TO", -100, 1500.0),
            _row("2025-02-01", "XEI.TO", 0, 18.0, action="DIVIDEND",
                 gross_amount=18.0),
            _row("2025-02-15", "XEI.TO", 0, 18.0, action="DIVIDEND",
                 gross_amount=18.0)]})
        r = _tool("taxjson_gains", "--country", "canada", "--year", "2025",
                  book)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual(d["summary"]["total_gain"], 500.0)


if __name__ == "__main__":
    unittest.main()
