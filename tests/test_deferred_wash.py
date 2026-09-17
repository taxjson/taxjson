"""Deferred-wash tracking: inventory reports the denied superficial
losses still parked in each open position's basis.

Canada: WASH_ ADJUSTs tally into the pool; partial sells release
proportionally (ACB-average); a full drain releases everything.
USA: §1091 bumps tag the replacement lot; FIFO consumption carries the
deferral out with the lot.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _gains(country, txs, tmp):
    base = Path(tmp) / "acct_base.json"
    base.write_text(json.dumps({"transactions": txs}))
    r = subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_gains",
         "--country", country, "--year", "2025", "--taxable", str(base)],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _tx(dt, qty, price, sym="XYZ.TO"):
    return {"action": "BUYSELL", "date": dt, "date_settle": dt,
            "time": "09:30:00", "symbol": sym, "quantity": qty,
            "price": price, "net_amount": abs(qty) * price,
            "currency": "CAD", "account": "acct"}


# Buy 100@20; sell all @10 (loss 1,000); rebuy 100@11 six days later —
# a classic superficial loss, fully denied and deferred.
WASH = [_tx("2025-01-06", 100, 20.0),
        _tx("2025-02-03", -100, 10.0),
        _tx("2025-02-09", 100, 11.0)]


class TestDeferredWash(unittest.TestCase):
    def _inv(self, country, txs):
        with tempfile.TemporaryDirectory() as tmp:
            out = _gains(country, txs, tmp)
        inv = {r["symbol"]: r for r in out.get("inventory") or []}
        return out, inv

    def test_canada_deferred_parked_in_pool(self):
        out, inv = self._inv("canada", WASH)
        row = inv["XYZ.TO"]
        self.assertEqual(row["deferred_wash"], 1000.0)
        # Basis = 1,100 rebuy + 1,000 deferred.
        self.assertAlmostEqual(row["total_cost"], 2100.0, places=2)

    def test_canada_partial_sell_releases_proportionally(self):
        # Sell 40 of the 100 replacement shares later (well outside any
        # wash window, at a gain so no new wash fires).
        txs = WASH + [_tx("2025-06-01", -40, 30.0)]
        out, inv = self._inv("canada", txs)
        row = inv["XYZ.TO"]
        self.assertAlmostEqual(row["qty"], 60.0, places=4)
        self.assertAlmostEqual(row["deferred_wash"], 600.0, places=2)

    def test_canada_full_drain_releases_all(self):
        txs = WASH + [_tx("2025-06-01", -100, 30.0)]
        out, inv = self._inv("canada", txs)
        self.assertNotIn("XYZ.TO", inv)         # position closed
        # And the recovered loss shows up in the final sale's gain:
        # proceeds 3,000 - basis 2,100 = 900 (vs 1,900 without deferral).
        final = [t for t in out["transactions"]
                 if t.get("date") == "2025-06-01"]
        self.assertAlmostEqual(final[0]["gain"], 900.0, places=2)

    def test_canada_second_wash_stacks(self):
        # Another round: sell 100@8 (loss vs bumped basis), rebuy again.
        txs = WASH + [_tx("2025-06-02", -100, 8.0),
                      _tx("2025-06-10", 100, 8.5)]
        out, inv = self._inv("canada", txs)
        row = inv["XYZ.TO"]
        # First deferral (1,000) was released by the 06-02 full drain,
        # whose loss (800 - 2,100 basis... = -1,300) is denied and
        # re-deferred into the new pool.
        self.assertAlmostEqual(row["deferred_wash"], 1300.0, places=2)
        self.assertAlmostEqual(row["total_cost"], 850.0 + 1300.0,
                               places=2)

    def test_usa_deferred_on_replacement_lot(self):
        out, inv = self._inv("usa", WASH)
        row = inv["XYZ.TO"]
        self.assertEqual(row["deferred_wash"], 1000.0)
        self.assertAlmostEqual(row["total_cost"], 2100.0, places=2)

    def test_usa_partial_fifo_release(self):
        txs = WASH + [_tx("2025-06-01", -40, 30.0)]
        out, inv = self._inv("usa", txs)
        row = inv["XYZ.TO"]
        self.assertAlmostEqual(row["deferred_wash"], 600.0, places=2)

    def test_no_wash_no_deferral(self):
        txs = [_tx("2025-01-06", 100, 20.0)]
        for country in ("canada", "usa"):
            _out, inv = self._inv(country, txs)
            self.assertEqual(inv["XYZ.TO"]["deferred_wash"], 0.0)


class TestSurfacing(unittest.TestCase):
    """`taxjson list` DEFERRED column and the wash-sales embedded
    footer read the new inventory field."""

    def _project(self, tmp):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_gains.json").write_text(json.dumps({
            "summary": {"year": "2025"}, "transactions": [
                {"symbol": "XYZ.TO", "date": "2025-02-03", "qty": 100,
                 "proceeds": 1000.0, "cost": 2000.0, "raw_gain": -1000.0,
                 "gain": 0.0, "disallowed_amount": 1000.0,
                 "is_wash_sale": True, "currency": "CAD"}],
            "inventory": [
                {"symbol": "XYZ.TO", "qty": 100, "total_cost": 2100.0,
                 "position_start_date": "2025-02-09",
                 "deferred_wash": 1000.0},
                {"symbol": "CLEAN.TO", "qty": 10, "total_cost": 500.0,
                 "position_start_date": "2025-01-05",
                 "deferred_wash": 0.0}]}))
        return root

    def _run(self, root, *args):
        return subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(root), *args],
            cwd=REPO_ROOT, capture_output=True, text=True)

    def test_list_shows_deferred_column_and_footer(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(self._project(tmp), "list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DEFERRED", r.stdout)
        self.assertRegex(r.stdout,
                         r"XYZ\.TO\s+100\s+2,100\.00\s+21\.00\s+1,000\.00")
        self.assertRegex(r.stdout, r"CLEAN\.TO\s+10\s+500\.00\s+50\.00\s+-")
        self.assertIn("DEFERRED: 1,000.00 CAD", r.stdout)

    def test_list_json_carries_deferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(self._project(tmp), "list", "--json")
        doc = json.loads(r.stdout)
        row = next(x for x in doc["rows"] if x["symbol"] == "XYZ.TO")
        self.assertEqual(row["deferred_wash"], 1000.0)
        self.assertEqual(doc["totals"]["deferred_wash"], 1000.0)

    def test_wash_sales_embedded_footer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = self._run(root, "wash-sales")
            r2 = self._run(root, "wash-sales", "--json")
        self.assertIn("Currently embedded in OPEN positions: 1,000.00",
                      r.stdout)
        self.assertEqual(json.loads(r2.stdout)["totals"]
                         ["embedded_in_open"], 1000.0)


if __name__ == "__main__":
    unittest.main()
