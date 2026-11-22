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
import tempfile
import unittest
from datetime import date
from pathlib import Path
from _style import CapturedWidth
from _support.view_report_tools import _tool, _row


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


# ------------------------------------------------- missing-history
class TestMissingHistory(_Tmp):
    """Formerly: test_fixl_planning_reports.TestMissingHistory"""
    def _books(self):
        (self.tmp / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            '[accounts.margin]\ntype = "taxable"\n'
            '[accounts.kids]\ntype = "sheltered"\n')
        m = self.write("work/margin_base.json", {"transactions": [
            _row("2025-01-06", "BBB.TO", 10, -100.0),
            _row("2025-04-01", "BBB.TO", -30, 300.0)]})
        k = self.write("work/kids_base.json", {"transactions": [
            _row("2025-04-01", "CCC.TO", -10, 100.0, account="kids")]})
        return m, k

    def test_unknown_account_is_refused(self):
        m, _ = self._books()
        r = _tool("taxjson_missing_history", "--account", "margn", m)
        self.assertEqual(r.returncode, 2)
        self.assertIn("margin", r.stderr)
        self.assertNotIn("No missing-cost-basis issues", r.stdout)

    def test_sheltered_rows_do_not_affect_the_year(self):
        from taxjson.lib import checklist as cl
        m, k = self._books()
        r = _tool("taxjson_missing_history", "--year", "2025", m, k)
        self.assertEqual(r.returncode, 0, r.stderr)
        aff = r.stdout.split("AFFECTS 2025")[1].split("SHELTERED")[0]
        self.assertIn("BBB.TO", aff)
        self.assertNotIn("CCC.TO", aff)
        self.assertRegex(r.stdout, r"SHELTERED 2025 .*superficial-loss")
        shel = r.stdout.split("SHELTERED 2025")[1]
        self.assertIn("CCC.TO", shel)
        ctx = cl.Ctx(root=self.tmp, cfg={"settings": {"year": 2025}},
                     year=2025, today=date(2026, 9, 29),
                     run_sub=lambda argv, timeout=0: (0, r.stdout, ""))
        res = cl.d_missing_history(ctx)
        self.assertIn("1 position(s)", res.detail)
        self.assertNotIn("CCC.TO", res.detail)


if __name__ == "__main__":
    unittest.main()
