"""Tests for taxjson-reconcile-slips (T5008 / 1099-B slip reconciliation)."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from taxjson.bin.taxjson_reconcile_slips import (
    load_computed,
    load_slip,
    main,
    norm_symbol,
    reconcile,
)


def sell(symbol="AAPL.US", date="2025-05-02", qty=-100, proceeds=11990.0,
         cost=10000.0, commission=10.0, fee=0.0, direction="LONG", **extra):
    e = {
        "date": date, "date_settle": date, "symbol": symbol, "qty": qty,
        "proceeds": proceeds, "cost": cost,
        "gain": proceeds - cost, "disallowed_amount": 0.0,
        "days_held": 100, "direction": direction,
        "commission": commission, "fee": fee, "account": "margin",
    }
    e.update(extra)
    return e


def write_gains(td, entries, name="margin_gains.json"):
    p = Path(td) / name
    p.write_text(json.dumps({"transactions": entries}))
    return p


def write_slip(td, text, name="t5008.csv"):
    p = Path(td) / name
    p.write_text(text)
    return p


class TestNormSymbol(unittest.TestCase):
    def test_strips_market_suffixes(self):
        self.assertEqual(norm_symbol("AAPL.US"), "AAPL")
        self.assertEqual(norm_symbol("shop.to"), "SHOP")
        self.assertEqual(norm_symbol(".DOL.TO"), "DOL")
        self.assertEqual(norm_symbol("BTC"), "BTC")


class TestLoadSlip(unittest.TestCase):
    def test_t5008_style_headers(self):
        with tempfile.TemporaryDirectory() as td:
            p = write_slip(td, "Security,Box 16,Box 21,Box 20\n"
                               "AAPL,100,12000.00,10000.00\n"
                               "AAPL,50,6000.00,5000.00\n")
            slip = load_slip(p)
        self.assertAlmostEqual(slip["AAPL"]["proceeds"], 18000.0)
        self.assertAlmostEqual(slip["AAPL"]["qty"], 150.0)
        self.assertAlmostEqual(slip["AAPL"]["cost"], 15000.0)
        self.assertEqual(slip["AAPL"]["rows"], 2)

    def test_1099b_style_headers_and_negatives(self):
        with tempfile.TemporaryDirectory() as td:
            p = write_slip(td, "Symbol,Quantity,Proceeds,Cost or other basis\n"
                               'MSFT,"1,000","$20,000.00",(19000.00)\n')
            slip = load_slip(p)
        self.assertAlmostEqual(slip["MSFT"]["proceeds"], 20000.0)
        self.assertAlmostEqual(slip["MSFT"]["qty"], 1000.0)
        self.assertAlmostEqual(slip["MSFT"]["cost"], -19000.0)

    def test_cost_column_optional(self):
        with tempfile.TemporaryDirectory() as td:
            p = write_slip(td, "symbol,proceeds\nAAPL,12000\n")
            slip = load_slip(p)
        self.assertIsNone(slip["AAPL"]["cost"])

    def test_unrecognizable_headers_fail_loud(self):
        with tempfile.TemporaryDirectory() as td:
            p = write_slip(td, "foo,bar\n1,2\n")
            with self.assertRaises(SystemExit):
                load_slip(p)


class TestReconcile(unittest.TestCase):
    def _computed(self, entries, year=2025):
        with tempfile.TemporaryDirectory() as td:
            return load_computed([write_gains(td, entries)], year)

    def test_gross_proceeds_match(self):
        computed = self._computed([sell()])   # net 11990 + 10 commission
        slip = {"AAPL": {"qty": 100.0, "proceeds": 12000.0, "cost": None,
                         "rows": 1}}
        rep = reconcile(slip, computed, 1.0)
        self.assertTrue(rep["clean"])
        self.assertEqual(rep["rows"][0]["status"], "OK")

    def test_net_proceeds_match_is_noted(self):
        computed = self._computed([sell()])
        slip = {"AAPL": {"qty": 100.0, "proceeds": 11990.0, "cost": None,
                         "rows": 1}}
        rep = reconcile(slip, computed, 1.0)
        self.assertTrue(rep["clean"])
        self.assertIn("NET proceeds", rep["rows"][0]["detail"])

    def test_proceeds_mismatch(self):
        computed = self._computed([sell()])
        slip = {"AAPL": {"qty": 100.0, "proceeds": 15000.0, "cost": None,
                         "rows": 1}}
        rep = reconcile(slip, computed, 1.0)
        self.assertFalse(rep["clean"])
        self.assertEqual(rep["rows"][0]["status"], "MISMATCH")
        self.assertIn("proceeds off", rep["rows"][0]["detail"])

    def test_quantity_mismatch(self):
        computed = self._computed([sell()])
        slip = {"AAPL": {"qty": 90.0, "proceeds": 12000.0, "cost": None,
                         "rows": 1}}
        rep = reconcile(slip, computed, 1.0)
        self.assertEqual(rep["rows"][0]["status"], "MISMATCH")
        self.assertIn("quantity off", rep["rows"][0]["detail"])

    def test_cost_difference_is_note_not_mismatch(self):
        # Per-broker book value vs blended ACB is often legitimate.
        computed = self._computed([sell()])
        slip = {"AAPL": {"qty": 100.0, "proceeds": 12000.0, "cost": 9000.0,
                         "rows": 1}}
        rep = reconcile(slip, computed, 1.0)
        self.assertEqual(rep["rows"][0]["status"], "OK")
        self.assertIn("slip cost differs", rep["rows"][0]["detail"])

    def test_missing_both_directions(self):
        computed = self._computed([sell()])
        slip = {"NVDA": {"qty": 10.0, "proceeds": 5000.0, "cost": None,
                         "rows": 1}}
        rep = reconcile(slip, computed, 1.0)
        by = {r["symbol"]: r for r in rep["rows"]}
        self.assertEqual(by["NVDA"]["status"], "MISSING_FROM_COMPUTED")
        self.assertEqual(by["AAPL"]["status"], "MISSING_FROM_SLIP")
        self.assertFalse(rep["clean"])

    def test_tainted_rows_flagged(self):
        computed = self._computed([sell(tainted=True)])
        slip = {"AAPL": {"qty": 100.0, "proceeds": 12000.0, "cost": None,
                         "rows": 1}}
        rep = reconcile(slip, computed, 1.0)
        self.assertIn("tainted", rep["rows"][0]["detail"])


class TestCli(unittest.TestCase):
    def test_clean_run_exits_0(self):
        with tempfile.TemporaryDirectory() as td:
            g = write_gains(td, [sell()])
            s = write_slip(td, "symbol,quantity,proceeds\nAAPL,100,12000\n")
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main([str(s), "--gains", str(g), "--year", "2025"])
            self.assertEqual(rc, 0)
            self.assertIn("1 OK", out.getvalue())

    def test_mismatch_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            g = write_gains(td, [sell()])
            s = write_slip(td, "symbol,quantity,proceeds\nAAPL,100,15000\n")
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main([str(s), "--gains", str(g)])
            self.assertEqual(rc, 1)
            self.assertIn("MISMATCH", out.getvalue())

    def test_json_output(self):
        with tempfile.TemporaryDirectory() as td:
            g = write_gains(td, [sell()])
            s = write_slip(td, "symbol,proceeds\nAAPL,12000\n")
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main([str(s), "--gains", str(g), "--json"])
            self.assertEqual(rc, 0)
            rep = json.loads(out.getvalue())
            self.assertEqual(rep["counts"]["ok"], 1)

    def test_missing_slip_file(self):
        with tempfile.TemporaryDirectory() as td:
            g = write_gains(td, [sell()])
            with redirect_stderr(io.StringIO()):
                rc = main(["/nonexistent.csv", "--gains", str(g)])
            self.assertEqual(rc, 2)


class TestWrapperExcludesCrypto(unittest.TestCase):
    """2026-09 audit: `taxjson reconcile-slips` fed every taxable
    account, crypto included — and exchanges issue no T5008/1099-B, so
    each crypto disposition was MISSING_FROM_SLIP and the command could
    never exit 0 on a project with one. The wrapper drops `crypto =
    true` accounts and says so."""

    def test_clean_equity_slip_exits_0_beside_crypto_book(self):
        import os
        import subprocess
        import sys
        repo = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.kraken]\ntype = "taxable"\ncrypto = true\n')
            write_gains(root / "work", [sell()], "margin_gains.json")
            write_gains(root / "work",
                        [sell(symbol="BTC", qty=-0.5, proceeds=40000.0,
                              cost=30000.0, account="kraken")],
                        "kraken_gains.json")
            slip = write_slip(td, "symbol,quantity,proceeds\n"
                                  "AAPL,100,12000\n")
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run",
                 "-C", str(root), "reconcile-slips", str(slip)],
                cwd=repo, capture_output=True, text=True,
                env={**os.environ, "TAXJSON_OFFLINE": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("1 OK", r.stdout)
        self.assertNotIn("BTC", r.stdout)
        self.assertIn("kraken", r.stderr)
        self.assertIn("excluded", r.stderr)


if __name__ == "__main__":
    unittest.main()
