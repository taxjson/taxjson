"""Regression tests for the 2026-09 audit's MEDIUM corporate-action and
distributions.map findings (area `corp`, medium round). All data
synthetic: fake tickers, fake ISINs, fake broker account ids."""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args],
        cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)


def _quiet(fn, *a, **kw):
    buf = io.StringIO()
    with redirect_stderr(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()


# ======================================================= distributions.map
class TestDistributionsMap(unittest.TestCase):
    def _apply(self, txs, rows, **kw):
        from taxjson.bin.taxjson_apply_distributions import (
            apply_distributions)
        (doc, n), err = _quiet(apply_distributions,
                               {"transactions": list(txs)}, rows, "m", **kw)
        adj = [t for t in doc["transactions"] if t["action"] == "ADJUST"]
        return adj, n, err

    def test_s000_06_bom_is_stripped(self):
        from taxjson.bin.taxjson_apply_distributions import load_map
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "distributions.map"
            p.write_bytes("XYZ.TO 2025-06-30 0.50\n".encode("utf-8-sig"))
            self.assertEqual(load_map(p), [("XYZ.TO", "2025-06-30", 0.5)])

    def test_s025_13_symbol_case_insensitive(self):
        from taxjson.bin.taxjson_apply_distributions import load_map
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "distributions.map"
            p.write_text("xaw.to 2025-06-30 0.50\n")
            rows = load_map(p)
        self.assertEqual(rows, [("XAW.TO", "2025-06-30", 0.5)])
        adj, n, _ = self._apply(
            [{"action": "BUYSELL", "date": "2025-01-15", "symbol": "XAW.TO",
              "quantity": 100.0}], rows)
        self.assertEqual(n, 1)
        self.assertAlmostEqual(adj[0]["net_amount"], 50.0)

    def test_s000_07_split_inside_settle_lag(self):
        # A pre-split sale traded 06-10 10:00, settling 06-11; the 2:1
        # split posts 06-10 20:25 (IB evening batch). The engine
        # re-denominates the sale to -1000 post-split shares: 1000 held.
        from taxjson.bin.taxjson_apply_distributions import balance_on
        txs = [
            {"action": "BUYSELL", "date": "2026-01-05", "time": "10:00:00",
             "date_settle": "2026-01-06", "symbol": "ABC.TO",
             "quantity": 1000.0},
            {"action": "BUYSELL", "date": "2026-06-10", "time": "10:00:00",
             "date_settle": "2026-06-11", "symbol": "ABC.TO",
             "quantity": -500.0},
            {"action": "SPLIT", "date": "2026-06-10", "time": "20:25:00",
             "symbol": "ABC.TO", "symbol_new": "ABC.TO", "quantity": 2.0},
        ]
        self.assertAlmostEqual(
            balance_on(txs, "ABC.TO", "2026-12-29", "settle"), 1000.0)
        # Between execution and settlement the holder of record still
        # has the pre-split 1000 shares, split to 2000.
        self.assertAlmostEqual(
            balance_on(txs, "ABC.TO", "2026-06-10", "settle"), 2000.0)
        adj, n, _ = self._apply(txs, [("ABC.TO", "2026-12-29", 0.43)])
        self.assertAlmostEqual(adj[0]["net_amount"], 430.0, places=4)

    def test_s025_10_old_ticker_key_lands_on_live_pool(self):
        txs = [
            {"action": "BUYSELL", "date": "2026-01-05", "symbol": "OLD.TO",
             "quantity": 100.0},
            {"action": "SPLIT", "date": "2026-05-01", "symbol": "OLD.TO",
             "symbol_new": "NEW.TO", "quantity": 1.0},
        ]
        adj, n, err = self._apply(txs, [("OLD.TO", "2026-06-30", 1.0)])
        self.assertEqual(n, 1)
        self.assertEqual(adj[0]["symbol"], "NEW.TO", err)
        self.assertAlmostEqual(adj[0]["net_amount"], 100.0)
        # The current ticker keyed BEFORE the rename lands on OLD.TO,
        # the pool live on that date (the rename then carries it).
        adj, n, _ = self._apply(txs, [("NEW.TO", "2026-03-31", 1.0)])
        self.assertEqual((n, adj[0]["symbol"]), (1, "OLD.TO"))

    def test_s025_22_key_goes_through_ticker_map(self):
        from taxjson.bin.taxjson_apply_distributions import main
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            base = t / "margin_base.json"
            base.write_text(json.dumps({"transactions": [
                {"action": "BUYSELL", "date": "2026-01-05",
                 "symbol": "ABC.TO", "quantity": 100.0,
                 "account": "margin"}]}))
            (t / "distributions.map").write_text("ABC.US 2026-06-30 1.00\n")
            (t / "ticker.map").write_text("TOBASE ABC.US ABC.TO\n")
            rc, err = _quiet(main, [str(base), "--map",
                                    str(t / "distributions.map"),
                                    "--ticker-map", str(t / "ticker.map")])
            self.assertEqual(rc, 0)
            doc = json.loads(base.read_text())
        adj = [x for x in doc["transactions"] if x["action"] == "ADJUST"]
        self.assertEqual(len(adj), 1, err)
        self.assertEqual(adj[0]["symbol"], "ABC.TO")
        self.assertAlmostEqual(adj[0]["net_amount"], 100.0)

    def test_s026_00_income_not_counted_is_said(self):
        adj, n, err = self._apply(
            [{"action": "BUYSELL", "date": "2024-01-15", "symbol": "XIC.TO",
              "quantity": 1000.0}], [("XIC.TO", "2024-12-30", 0.5)])
        self.assertEqual(n, 1)
        self.assertIn("not counted as income", err)

    def test_run_passes_ticker_map(self):
        # End to end: TOBASE consolidates ABC.US into ABC.TO; the map
        # row keyed by the broker's listing still raises the ACB.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin" / "book.tt").write_text(
                "BUYSELL 2026-01-05 10:00:00 ABC.US 100 CAD 10 -1000 0\n"
                "BUYSELL 2026-09-01 10:00:00 ABC.TO -100 CAD 12 1200 0\n")
            (root / "ticker.map").write_text("GLOBAL ABC.US ABC.TO\n")
            (root / "distributions.map").write_text(
                "ABC.US 2026-06-30 1.00\n")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("+100.00 ACB adjustment", r.stderr)
            g = json.loads((root / "work" /
                            "margin_gains_wash.json").read_text())
        self.assertAlmostEqual(float(g["summary"]["total_gain"]), 100.0,
                               places=2)


if __name__ == "__main__":
    unittest.main()
