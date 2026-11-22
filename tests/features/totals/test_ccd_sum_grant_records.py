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
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from _style import CapturedWidth
from _support.view_report_tools import REPO_ROOT, _env


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


# ------------------------------------------------------------ ccd-sum
class TestCcdSumGrantRecords(_Tmp):
    """Formerly: test_fixl_planning_reports.TestCcdSumGrantRecords"""
    def test_grant_write_is_not_a_close(self):
        root = self.tmp
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n')
        sym = "XYZ250620C00030000.TO"
        rows = [
            # grant timing: the premium recognised at the write ...
            dict(symbol=sym, date="2025-02-03", date_settle="2025-02-04",
                 gain=200.0, cost=-200.0, proceeds=0.0, qty=1,
                 direction="SHORT", grant=True, currency="CAD",
                 account="margin"),
            # ... and the buy-back that closes it.
            dict(symbol=sym, date="2025-03-03", date_settle="2025-03-04",
                 gain=-50.0, cost=0.0, proceeds=-50.0, qty=1,
                 direction="SHORT", currency="CAD", account="margin")]
        self.write("work/margin_gains.json",
                   {"summary": {"year": "2025"}, "transactions": rows})
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(root), "ccd-sum", "--json"], capture_output=True,
            text=True, env=_env(), cwd=REPO_ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = json.loads(r.stdout)["rows"][0]
        self.assertEqual(rec["contracts"], 1)
        self.assertEqual(rec["qty"], 1.0)
        self.assertAlmostEqual(rec["gain"], 150.0)


if __name__ == "__main__":
    unittest.main()
