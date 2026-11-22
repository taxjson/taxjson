"""Regressions for FUZZ-2026-07 confirmed findings (minimal repros).

Formerly: test_fuzz_fixes"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import _paths

REPO_ROOT = _paths.REPO


_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,"
              "Description,Quantity,Price,Gross Amount,Commission,"
              "Net Amount,Currency,Account #,Activity Type,Account Type\n")


def _qt_row(d, action, sym, qty, price, comm, net):
    return (f"{d} 09:30:00 AM,{d} 12:00:00 AM,{action},{sym},DESC,"
            f"{qty},{price},{abs(qty)*price},{comm},{net},CAD,1,"
            f"Trades,Individual\n")


def _pipeline_project(root, margin_rows, rrsp_rows):
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "rrsp").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.margin]\ntype = "taxable"\n'
        '[accounts.rrsp]\ntype = "sheltered"\n')
    (root / "inputs" / "margin" / "questrade.csv").write_text(
        _QT_HEADER + "".join(margin_rows))
    (root / "inputs" / "rrsp" / "questrade.csv").write_text(
        _QT_HEADER + "".join(rrsp_rows))


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


class TestFuzzI_ShelteredFeesNotFolded(unittest.TestCase):
    """#I: the wash pass fed sheltered_base into each taxable account's
    gains run and the fee tally summed over ALL books — an RRSP's fee
    appeared in every taxable wash file and `tjs sum` multi-counted
    it. Fee tallies are now taxable-book-only.

    Formerly: test_fuzz_fixes.TestFuzzI_ShelteredFeesNotFolded"""

    def test_sum_fees_counts_each_fee_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _pipeline_project(
                root,
                [_qt_row("2025-01-15", "Buy", "XEI.TO", 100, 10.0,
                         9.90, -1009.90)],
                [_qt_row("2025-02-03", "Buy", "ZAG.TO", 50, 14.0,
                         14.95, -714.95)])
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            s = _run_cli(root, "sum", "--json")
            doc = json.loads(s.stdout)
        by = {a["account"]: a for a in doc["accounts"]}
        self.assertAlmostEqual(by["margin"]["fees"], 9.90, places=2)
        self.assertAlmostEqual(by["rrsp"]["fees"], 14.95, places=2)
        self.assertAlmostEqual(doc["totals"]["fees"], 24.85, places=2)


if __name__ == "__main__":
    unittest.main()
