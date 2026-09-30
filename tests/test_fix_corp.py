"""Regression tests for the 2026-09 audit's corporate-action findings
(area `corp`). All data synthetic: fake tickers, fake ISINs, fake broker
account ids (U5550001 / 55500001 style)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.core import TaxTransaction
from taxjson.lib.corporate_timeline import SplitTimeline

REPO_ROOT = Path(__file__).resolve().parent.parent


def _split(symbol, date, ratio, account='A', symbol_new=''):
    return TaxTransaction(action='SPLIT', date=date, symbol=symbol,
                          quantity=ratio, symbol_new=symbol_new,
                          account=account)


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args],
        cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)


_CAD_CONFIG = """\
[settings]
year = 2026
country = "canada"
province = "ON"
base_currency = "CAD"
source_currencies = []
tax_date = "settle"

[accounts.margin]
type = "taxable"
"""


# --------------------------------------------------------------- R1-135
class TestSplitBookedOnTwoDates(unittest.TestCase):
    """R1-135: one split booked on two dates by two brokers must apply
    once (the event key used to include the exact date)."""

    def test_engine_dedupe_collapses_nearby_dates(self):
        txs = [_split('XYZ.TO', '2026-06-11', 2.0),
               _split('XYZ.TO', '2026-06-15', 2.0)]
        kept = SplitTimeline.dedupe(txs)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].date, '2026-06-11')   # earliest wins

    def test_engine_dedupe_keeps_earliest_regardless_of_order(self):
        txs = [_split('XYZ.TO', '2026-06-15', 2.0),
               _split('XYZ.TO', '2026-06-11', 2.0)]
        kept = SplitTimeline.dedupe(txs)
        self.assertEqual([t.date for t in kept], ['2026-06-11'])

    def test_distant_or_different_ratio_splits_stay(self):
        txs = [_split('XYZ.TO', '2026-01-10', 2.0),
               _split('XYZ.TO', '2026-06-15', 2.0),
               _split('XYZ.TO', '2026-06-16', 3.0)]
        self.assertEqual(len(SplitTimeline.dedupe(txs)), 3)

    def test_shared_seen_across_lists_uses_window(self):
        seen = set()
        a = SplitTimeline.dedupe([_split('K.US', '2026-06-11', 10.0,
                                         account='tfsa')], seen)
        b = SplitTimeline.dedupe([_split('K.US', '2026-06-15', 10.0,
                                         account='lira')], seen)
        self.assertEqual(len(a), 1)
        self.assertEqual(b, [])

    def test_apply_distributions_balance_on(self):
        from taxjson.bin.taxjson_apply_distributions import balance_on
        rows = [
            {"action": "BUYSELL", "symbol": "XYZ.TO", "date": "2026-01-05",
             "quantity": 200, "account": "margin"},
            {"action": "SPLIT", "symbol": "XYZ.TO", "date": "2026-06-11",
             "quantity": 2, "account": "margin"},
            {"action": "SPLIT", "symbol": "XYZ.TO", "date": "2026-06-15",
             "quantity": 2, "account": "margin"},
        ]
        self.assertAlmostEqual(balance_on(rows, "XYZ.TO", "2026-07-01"),
                               400.0)

    def test_run_one_account_two_brokers(self):
        # Two broker files of ONE taxable account, each holding 100 XYZ
        # through the same 2:1 split, dated 06-11 by one and 06-15 by
        # the other; both sell all 200. Correct total gain:
        # 2 x (200*6 - 100*10) = 400.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(_CAD_CONFIG)
            d = root / "inputs" / "margin"
            d.mkdir(parents=True)
            (d / "brokerA.tt").write_text(
                "BUYSELL 2026-01-05 10:00:00 XYZ.TO 100 CAD 10 -1000 0\n"
                "SPLIT 2026-06-11 20:25:00 XYZ.TO XYZ.TO 2\n"
                "BUYSELL 2026-08-03 10:00:00 XYZ.TO -200 CAD 6 1200 0\n")
            (d / "brokerB.tt").write_text(
                "BUYSELL 2026-01-06 10:00:00 XYZ.TO 100 CAD 10 -1000 0\n"
                "SPLIT 2026-06-15 20:25:00 XYZ.TO XYZ.TO 2\n"
                "BUYSELL 2026-08-04 10:00:00 XYZ.TO -200 CAD 6 1200 0\n")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            g = json.loads((root / "work" /
                            "margin_gains_wash.json").read_text())
            self.assertAlmostEqual(float(g["summary"]["total_gain"]), 400.0,
                                   places=2)
            self.assertEqual([i for i in g.get("inventory", [])
                              if abs(i["qty"]) > 1e-9], [])
            sums = (root / "reports" / "margin.sum").read_text()
            self.assertIn("booked on two dates", sums + r.stderr)


if __name__ == '__main__':
    unittest.main()
