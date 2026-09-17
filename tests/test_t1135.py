"""Tests for taxjson-t1135 (CRA foreign-property helper).

Covers: domicile classification + t1135.map overrides, the cost walk
(baseline state, simultaneous-total threshold test, splits/renames,
phantom openings, shorts, ADJUST), the income/gain join, report assembly,
and the CLI end-to-end (text + JSON) including the `taxjson t1135` wrapper.
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from taxjson.bin.taxjson_t1135 import (
    REVIEW,
    build_report,
    classify_country,
    join_income_gains,
    load_overrides,
    main,
    walk_costs,
)


def tx(action="BUYSELL", date="2025-01-15", symbol="AAPL.US", qty=0.0,
       net=0.0, symbol_new="", time="09:30:00", **extra):
    d = {
        "action": action, "date": date, "date_settle": date, "time": time,
        "symbol": symbol, "quantity": qty, "net_amount": net,
        "symbol_new": symbol_new, "currency": "CAD",
    }
    d.update(extra)
    return d


class TestClassify(unittest.TestCase):
    def test_suffixes(self):
        self.assertEqual(classify_country("AAPL.US", {}), "USA")
        self.assertEqual(classify_country("BP.L", {}), "GBR")
        self.assertEqual(classify_country("BHP.AX", {}), "AUS")
        self.assertIsNone(classify_country("RY.TO", {}))
        self.assertIsNone(classify_country("XYZ.V", {}))

    def test_option_symbol_uses_its_own_suffix(self):
        self.assertEqual(classify_country("AAPL250117C00150000.US", {}), "USA")
        self.assertIsNone(classify_country("MDA251219P00029000.TO", {}))

    def test_no_suffix_is_review(self):
        self.assertEqual(classify_country("BTC", {}), REVIEW)

    def test_unknown_suffix_is_review(self):
        self.assertEqual(classify_country("SAP.DE", {}), REVIEW)

    def test_overrides_win(self):
        ov = {"ENB.US": None, "GLXY.TO": "USA"}
        self.assertIsNone(classify_country("ENB.US", ov))
        self.assertEqual(classify_country("GLXY.TO", ov), "USA")


class TestOverridesFile(unittest.TestCase):
    def test_parse(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t1135.map"
            p.write_text(
                "# comment\n"
                "ENB.US  CA\n"
                "GLXY.TO USA   # foreign corp on TSX\n"
                "BTC EXCLUDE\n"
                "malformed-line-here\n"
            )
            err = io.StringIO()
            with redirect_stderr(err):
                ov = load_overrides(p)
        self.assertIsNone(ov["ENB.US"])
        self.assertEqual(ov["GLXY.TO"], "USA")
        self.assertIsNone(ov["BTC"])
        self.assertNotIn("malformed-line-here", ov)
        self.assertIn("line ignored", err.getvalue())


class TestWalkCosts(unittest.TestCase):
    def test_basic_buy_hold(self):
        txs = [tx(date="2024-06-01", qty=100, net=15000.0)]
        w = walk_costs(txs, 2025, {})
        s = w["per_symbol"]["AAPL.US"]
        self.assertAlmostEqual(s["max_cost"], 15000.0, places=2)
        self.assertAlmostEqual(s["year_end_cost"], 15000.0, places=2)
        self.assertAlmostEqual(w["max_total_cost"], 15000.0, places=2)

    def test_sell_reduces_year_end_but_not_max(self):
        txs = [
            tx(date="2024-06-01", qty=100, net=15000.0),
            tx(date="2025-05-01", qty=-50, net=9000.0),
        ]
        w = walk_costs(txs, 2025, {})
        s = w["per_symbol"]["AAPL.US"]
        self.assertAlmostEqual(s["max_cost"], 15000.0, places=2)
        self.assertAlmostEqual(s["year_end_cost"], 7500.0, places=2)

    def test_threshold_uses_simultaneous_total_not_sum_of_maxima(self):
        # Hold A ($90k), sell it entirely, THEN buy B ($90k): total never
        # exceeds $90k even though the per-symbol maxima sum to $180k.
        txs = [
            tx(date="2025-01-10", symbol="AAA.US", qty=100, net=90000.0),
            tx(date="2025-03-10", symbol="AAA.US", qty=-100, net=95000.0),
            tx(date="2025-04-10", symbol="BBB.US", qty=100, net=90000.0),
        ]
        w = walk_costs(txs, 2025, {})
        self.assertAlmostEqual(w["max_total_cost"], 90000.0, places=2)
        self.assertAlmostEqual(w["per_symbol"]["AAA.US"]["max_cost"], 90000.0, places=2)
        self.assertAlmostEqual(w["per_symbol"]["BBB.US"]["max_cost"], 90000.0, places=2)

    def test_canadian_symbols_do_not_count(self):
        txs = [
            tx(symbol="RY.TO", qty=100, net=14000.0),
            tx(symbol="AAPL.US", qty=10, net=1500.0),
        ]
        w = walk_costs(txs, 2025, {})
        self.assertNotIn("RY.TO", w["per_symbol"])
        self.assertAlmostEqual(w["max_total_cost"], 1500.0, places=2)

    def test_split_keeps_cost(self):
        txs = [
            tx(date="2025-01-10", qty=100, net=10000.0),
            tx(action="SPLIT", date="2025-02-10", qty=2.0),
        ]
        w = walk_costs(txs, 2025, {})
        s = w["per_symbol"]["AAPL.US"]
        self.assertAlmostEqual(s["year_end_cost"], 10000.0, places=2)

    def test_rename_carries_cost_and_max(self):
        txs = [
            tx(date="2025-01-10", symbol="FB.US", qty=100, net=20000.0),
            tx(action="SPLIT", date="2025-03-01", symbol="FB.US",
               qty=1.0, symbol_new="META.US"),
        ]
        w = walk_costs(txs, 2025, {})
        self.assertNotIn("FB.US", w["per_symbol"])
        s = w["per_symbol"]["META.US"]
        self.assertAlmostEqual(s["year_end_cost"], 20000.0, places=2)
        self.assertAlmostEqual(s["max_cost"], 20000.0, places=2)

    def test_phantom_opening_flags_unknown_acb(self):
        txs = [
            tx(action="OPENING_BALANCE", date="2024-01-01", qty=100, net=0.0),
            tx(date="2025-02-01", qty=10, net=2000.0),
        ]
        w = walk_costs(txs, 2025, {})
        s = w["per_symbol"]["AAPL.US"]
        self.assertTrue(s["unknown_acb"])
        self.assertAlmostEqual(s["year_end_cost"], 2000.0, places=2)

    def test_phantom_only_holding_still_listed(self):
        # A foreign position made ENTIRELY of phantom shares has tracked
        # cost 0 but is still specified foreign property — it must appear
        # in the table (flagged), not silently vanish.
        txs = [tx(action="OPENING_BALANCE", date="2023-01-01",
                  symbol="STX.US", qty=50, net=0.0)]
        w = walk_costs(txs, 2025, {})
        s = w["per_symbol"]["STX.US"]
        self.assertTrue(s["unknown_acb"])
        self.assertAlmostEqual(s["year_end_cost"], 0.0, places=2)

    def test_short_position_is_not_property(self):
        txs = [tx(date="2025-01-10", qty=-100, net=15000.0)]
        w = walk_costs(txs, 2025, {})
        self.assertNotIn("AAPL.US", w["per_symbol"])
        self.assertAlmostEqual(w["max_total_cost"], 0.0, places=2)

    def test_adjust_reduces_cost(self):
        txs = [
            tx(date="2025-01-10", qty=100, net=10000.0),
            tx(action="ADJUST", date="2025-06-01", qty=0, net=-1000.0),
        ]
        w = walk_costs(txs, 2025, {})
        s = w["per_symbol"]["AAPL.US"]
        self.assertAlmostEqual(s["year_end_cost"], 9000.0, places=2)
        self.assertAlmostEqual(s["max_cost"], 10000.0, places=2)

    def test_events_after_year_end_ignored(self):
        txs = [
            tx(date="2025-06-01", qty=100, net=10000.0),
            tx(date="2026-01-05", qty=100, net=90000.0),
        ]
        w = walk_costs(txs, 2025, {})
        self.assertAlmostEqual(w["max_total_cost"], 10000.0, places=2)

    def test_standing_position_with_no_in_year_events(self):
        txs = [tx(date="2023-06-01", qty=100, net=120000.0)]
        w = walk_costs(txs, 2025, {})
        self.assertAlmostEqual(w["max_total_cost"], 120000.0, places=2)
        self.assertEqual(w["max_total_date"], "2025-01-01")

    def test_income_rows_do_not_move_cost(self):
        txs = [
            tx(date="2025-01-10", qty=100, net=10000.0),
            tx(action="DIVIDEND", date="2025-02-10", qty=0, net=50.0),
            tx(action="TAX", date="2025-02-10", qty=0, net=7.5),
        ]
        w = walk_costs(txs, 2025, {})
        self.assertAlmostEqual(w["per_symbol"]["AAPL.US"]["year_end_cost"],
                               10000.0, places=2)


class TestIncomeGains(unittest.TestCase):
    def _gains_file(self, td, entries):
        p = Path(td) / "gains.json"
        p.write_text(json.dumps({"transactions": entries}))
        return p

    def test_join(self):
        entries = [
            {"action": "DIVIDEND", "symbol": "AAPL.US",
             "date": "2025-03-01", "dividend": 120.0, "gain": 0.0},
            {"action": "DIVIDEND_IN_LIEU", "symbol": "AAPL.US",
             "date": "2025-04-01", "pil": 30.0, "gain": 0.0},
            {"symbol": "AAPL.US", "date": "2025-05-01", "qty": -50,
             "gain": 750.0},
            {"symbol": "AAPL.US", "date": "2024-05-01", "qty": -50,
             "gain": 999.0},                    # wrong year — excluded
        ]
        with tempfile.TemporaryDirectory() as td:
            got = join_income_gains([self._gains_file(td, entries)], 2025)
        self.assertAlmostEqual(got["AAPL.US"]["income"], 150.0, places=2)
        self.assertAlmostEqual(got["AAPL.US"]["gain"], 750.0, places=2)


class TestBuildReport(unittest.TestCase):
    def _write(self, td, name, payload):
        p = Path(td) / name
        p.write_text(json.dumps(payload))
        return p

    def test_report_assembly(self):
        with tempfile.TemporaryDirectory() as td:
            base = self._write(td, "base.json", {"transactions": [
                tx(date="2024-06-01", qty=100, net=150000.0),
                tx(date="2025-05-01", qty=-50, net=90000.0),
                tx(symbol="RY.TO", date="2024-07-01", qty=100, net=14000.0),
            ]})
            gains = self._write(td, "gains.json", {"transactions": [
                {"action": "DIVIDEND", "symbol": "AAPL.US",
                 "date": "2025-03-01", "dividend": 120.0, "gain": 0.0},
                {"symbol": "AAPL.US", "date": "2025-05-01", "gain": 15000.0},
                # income on a foreign symbol never held in the cost walk
                {"action": "DIVIDEND", "symbol": "BP.L",
                 "date": "2025-06-01", "dividend": 40.0, "gain": 0.0},
            ]})
            rep = build_report([base], [gains], 2025, {}, "CAD")

        self.assertTrue(rep["filing_required"])
        self.assertTrue(rep["simplified_method_available"])
        by_symbol = {r["symbol"]: r for r in rep["properties"]}
        self.assertAlmostEqual(by_symbol["AAPL.US"]["max_cost"], 150000.0, places=2)
        self.assertAlmostEqual(by_symbol["AAPL.US"]["year_end_cost"], 75000.0, places=2)
        self.assertAlmostEqual(by_symbol["AAPL.US"]["income"], 120.0, places=2)
        self.assertAlmostEqual(by_symbol["AAPL.US"]["gain"], 15000.0, places=2)
        self.assertIn("BP.L", by_symbol)          # income-only row present
        self.assertAlmostEqual(by_symbol["BP.L"]["income"], 40.0, places=2)
        self.assertNotIn("RY.TO", by_symbol)
        self.assertIn("USA", rep["by_country"])
        self.assertIn("GBR", rep["by_country"])

    def test_detailed_method_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            base = self._write(td, "base.json", {"transactions": [
                tx(date="2025-01-10", qty=100, net=260000.0),
            ]})
            rep = build_report([base], [], 2025, {}, "CAD")
        self.assertTrue(rep["filing_required"])
        self.assertFalse(rep["simplified_method_available"])

    def test_below_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            base = self._write(td, "base.json", {"transactions": [
                tx(date="2025-01-10", qty=10, net=15000.0),
            ]})
            rep = build_report([base], [], 2025, {}, "CAD")
        self.assertFalse(rep["filing_required"])


class TestCli(unittest.TestCase):
    def test_end_to_end_text_and_json(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "margin_base.json"
            base.write_text(json.dumps({"transactions": [
                tx(date="2025-01-10", qty=100, net=120000.0),
                tx(symbol="BTC", date="2025-02-01", qty=1, net=60000.0),
            ]}))
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main([str(base), "--year", "2025"])
            self.assertEqual(rc, 0)
            text = out.getvalue()
            self.assertIn("T1135 FILING REQUIRED", text)
            self.assertIn("AAPL.US", text)
            self.assertIn("t1135.map", text)      # review note for BTC

            out = io.StringIO()
            with redirect_stdout(out):
                rc = main([str(base), "--year", "2025", "--json"])
            self.assertEqual(rc, 0)
            rep = json.loads(out.getvalue())
            self.assertTrue(rep["filing_required"])
            self.assertIn("BTC", rep["review_symbols"])

    def test_missing_file_is_error(self):
        err = io.StringIO()
        with redirect_stderr(err):
            rc = main(["/nonexistent/base.json", "--year", "2025"])
        self.assertEqual(rc, 2)

    def test_map_override_excludes(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "base.json"
            base.write_text(json.dumps({"transactions": [
                tx(symbol="ENB.US", date="2025-01-10", qty=100, net=120000.0),
            ]}))
            m = Path(td) / "t1135.map"
            m.write_text("ENB.US CA\n")
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main([str(base), "--year", "2025", "--json",
                           "--map", str(m)])
            self.assertEqual(rc, 0)
            rep = json.loads(out.getvalue())
        self.assertFalse(rep["filing_required"])
        self.assertEqual(rep["properties"], [])


class TestRunWrapper(unittest.TestCase):
    def test_taxjson_t1135_subcommand(self):
        try:
            import tomllib  # noqa: F401
        except ImportError:
            try:
                import tomli  # noqa: F401
            except ImportError:
                self.skipTest("no TOML support on this interpreter")
        import argparse
        from taxjson.bin.taxjson_run import cmd_t1135

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n\n'
                '[accounts.margin]\ntype = "taxable"\n\n'
                '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n\n'
                '[accounts.rrsp]\ntype = "sheltered"\n'
            )
            work = root / "work"
            work.mkdir()
            (work / "margin_base.json").write_text(json.dumps({
                "transactions": [
                    tx(date="2025-01-10", qty=100, net=120000.0)]}))
            # Second taxable account: pins the argv regression where a
            # base file appearing after an interleaved --gains was
            # rejected by argparse.
            (work / "crypto_base.json").write_text(json.dumps({
                "transactions": [
                    tx(symbol="BTC", date="2025-02-01", qty=1,
                       net=60000.0)]}))
            # A sheltered base file must NOT be read even if present.
            (work / "rrsp_base.json").write_text(json.dumps({
                "transactions": [
                    tx(symbol="MSFT.US", date="2025-01-10", qty=100,
                       net=500000.0)]}))
            (work / "margin_gains.json").write_text(json.dumps({
                "transactions": [
                    {"action": "DIVIDEND", "symbol": "AAPL.US",
                     "date": "2025-03-01", "dividend": 55.0, "gain": 0.0}]}))

            args = argparse.Namespace(dir=str(root), json=True)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                with self.assertRaises(SystemExit) as cm:
                    cmd_t1135(args)
            self.assertEqual(cm.exception.code, 0)
            rep = json.loads(out.getvalue())
        self.assertTrue(rep["filing_required"])
        symbols = [r["symbol"] for r in rep["properties"]]
        self.assertIn("AAPL.US", symbols)
        self.assertIn("BTC", symbols)          # second taxable account read
        self.assertNotIn("MSFT.US", symbols)   # sheltered excluded
        by_symbol = {r["symbol"]: r for r in rep["properties"]}
        self.assertAlmostEqual(by_symbol["AAPL.US"]["income"], 55.0, places=2)


if __name__ == "__main__":
    unittest.main()
