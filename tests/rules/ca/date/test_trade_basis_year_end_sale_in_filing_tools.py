"""Regression tests for the filing-b medium findings (reconcile-slips,
T1135, report commands on partial/stale books, audit, form-export).
Synthetic data only.

Formerly: test_fix_filing_b"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth
import _paths
from _support.tt_cash_project import _toml


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO_ROOT = _paths.REPO


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()


_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _qt(trade, settle, action, sym, qty, price):
    gross = abs(qty) * price
    net = -gross if qty > 0 else gross
    return (f"{trade} 10:00:00 AM,{settle} 12:00:00 AM,{action},{sym},"
            f"{sym} DESC,{qty},{price:.2f},{gross:.2f},0.00,{net:.2f},CAD,"
            f"55500001,Trades,Individual\n")  # pii-ok


@rule(R.CA_DATE_02)
class TestTradeBasisProject(unittest.TestCase):
    """R1-192, R1-200, S052-10: a Canada project on tax_date = "trade"
    — the Dec-31 sale settling in January belongs to 2025 everywhere.

    Formerly: test_fix_filing_b.TestTradeBasisProject"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        root = cls.root = Path(cls._td.name)
        _toml(root, 2025, 'source_currencies = []\ntax_date = "trade"\n'
                          'option_grant_timing_since = 2025\n',
              [("margin", "taxable", "")])
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "qt.csv").write_text(
            _QT_HEADER
            + _qt("2025-03-03", "2025-03-04", "Buy", "ZZZ.TO", 100, 10.0)
            + _qt("2025-06-02", "2025-06-03", "Sell", "ZZZ.TO", -50, 13.0)
            + _qt("2025-12-31", "2026-01-02", "Sell", "ZZZ.TO", -50, 14.0))
        # T1135 needs foreign property: classify the CAD test symbol.
        (root / "ticker.map").write_text("T1135 ZZZ.TO USA\n")
        r = _cli(root, "run", "--no-input")
        assert r.returncode == 0, r.stderr[-2000:]

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    @rule(R.CA_DISP_03)
    def test_form_export_and_sum_keep_the_year_end_sale(self):
        fe = json.loads(_cli(self.root, "form-export", "--json").stdout)
        self.assertAlmostEqual(fe["totals"]["gain_all"], 350.0, places=2)
        sm = json.loads(_cli(self.root, "sum", "--json").stdout)
        self.assertAlmostEqual(sm["filing"]["totals"]["gain"], 350.0,
                               places=2)

    @rule(R.CA_RPT_10)
    def test_carryover_uses_the_project_basis(self):
        co = json.loads(_cli(self.root, "carryover", "--json").stdout)
        rows = {r["year"]: r for r in co["rows"]}
        self.assertAlmostEqual(rows[2025]["net_gain"], 350.0, places=2)
        self.assertNotIn(2026, rows)

    @rule(R.CA_RPT_01)
    def test_t1135_follows_the_basis(self):
        r = _cli(self.root, "t1135", "--json")
        rep = json.loads(r.stdout)
        row = next(p for p in rep["properties"] if p["symbol"] == "ZZZ.TO")
        self.assertAlmostEqual(row["gain"], 350.0, places=2)
        self.assertAlmostEqual(row["year_end_cost"], 0.0, places=2)
