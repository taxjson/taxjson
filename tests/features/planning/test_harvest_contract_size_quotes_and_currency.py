"""Re-audit-2 planning fixes: `tjs harvest` and the price chain.

Offline throughout: prices come from injected fetchers (the price
chain's `fetchers` test hook) or a seeded price cache, never the
network. Synthetic books only.

Formerly: test_fix_a2_planning_harvest
"""

import tempfile
import unittest
from pathlib import Path

from _support.planning_harvest_runs import TODAY, _iso, _write, _gains, _run, _json_rows


class TestDeclaredContractSize(unittest.TestCase):
    """A2-0367 / A2-1177: harvest --options values an option at the
    contract size its rows declared (the inventory's `multiplier`), as
    `tjs list` and the holdings export do; 100 only when none is
    declared.

    Formerly: test_fix_a2_planning_harvest.TestDeclaredContractSize"""

    def _book(self, td, inv):
        return _write(Path(td) / "margin_gains_wash.json", _gains(inv))

    def test_declared_x10_contract(self):
        sym = "XYZ261218C00050000.TO"
        with tempfile.TemporaryDirectory() as td:
            g = self._book(td, [{"symbol": sym, "qty": 5,
                                 "total_cost": 100.0, "multiplier": 10.0,
                                 "position_start_date": _iso(-20)}])
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--options",
                 "--json"],
                fetchers=[lambda rem: {}],
                option_fetchers=[lambda rem: {s: (2.0, "ibkr")
                                              for s in rem}])
        self.assertEqual(rc, 0, err)
        row = _json_rows(out)["rows"][0]
        self.assertEqual(row["multiplier"], 10.0)
        self.assertAlmostEqual(row["value"], 100.0)
        self.assertAlmostEqual(row["unrealized"], 0.0)
        self.assertEqual(row["verdict"], "FLAT")

    def test_text_cost_per_share_uses_declared_size(self):
        sym = "XYZ261218C00050000.TO"
        with tempfile.TemporaryDirectory() as td:
            g = self._book(td, [{"symbol": sym, "qty": 2,
                                 "total_cost": 30.0, "multiplier": 10.0,
                                 "position_start_date": _iso(-20)}])
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--options"],
                fetchers=[lambda rem: {}],
                option_fetchers=[lambda rem: {s: (2.10, "ibkr")
                                              for s in rem}])
        self.assertEqual(rc, 0, err)
        line = next(ln for ln in out.splitlines() if sym in ln)
        self.assertIn("1.5000", line)          # 30 / (2 x 10)
        self.assertIn("12.00", line)           # 2 x 2.10 x 10 - 30
        self.assertNotIn("390.00", line)

    def test_undeclared_equity_option_stays_x100(self):
        sym = "XYZ261218C00050000.TO"
        with tempfile.TemporaryDirectory() as td:
            g = self._book(td, [{"symbol": sym, "qty": 1,
                                 "total_cost": 200.0,
                                 "position_start_date": _iso(-20)}])
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--options",
                 "--json"],
                fetchers=[lambda rem: {}],
                option_fetchers=[lambda rem: {s: (2.0, "ibkr")
                                              for s in rem}])
        self.assertEqual(rc, 0, err)
        row = _json_rows(out)["rows"][0]
        self.assertEqual(row["multiplier"], 100.0)
        self.assertAlmostEqual(row["value"], 200.0)


class TestCryptoTickerMap(unittest.TestCase):
    """A2-0364: harvest quotes a coin under the project's
    ticker.map CRYPTO spelling — the one the books were priced with.

    Formerly: test_fix_a2_planning_harvest.TestCryptoTickerMap"""

    def test_project_map_spelling_is_asked(self):
        asked = {}

        def fetch(rem):
            asked.update(rem)
            return {s: (100.0, "fake", "USD") for s in rem}

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "ticker.map").write_text("CRYPTO FOO FOO123\n")
            g = _write(root / "work" / "crypto_gains_wash.json", _gains([
                {"symbol": "FOO", "qty": 1, "total_cost": 135.0,
                 "position_start_date": _iso(-50)}]))
            (root / "work" / "to_base.csv").write_text(
                f"{TODAY.isoformat()} 12:00:00 USD CAD 1.35\n")
            rc, out, err = _run([str(g), "--no-ibkr", "--country",
                                 "canada", "--json"], fetchers=[fetch])
        self.assertEqual(rc, 0, err)
        self.assertEqual(asked, {"FOO": "FOO123-USD"})

    def test_only_the_project_map_names_a_coin_id(self):
        # The built-in coin id table is gone: without a CRYPTO line a
        # coin is quoted as <SYMBOL>-USD.
        from taxjson.lib.price_chain import (load_crypto_overrides,
                                             yf_symbol_for)
        self.assertEqual(yf_symbol_for("FOO"), "FOO-USD")
        self.assertEqual(yf_symbol_for("FOO", {"FOO": "FOO123"}),
                         "FOO123-USD")
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(load_crypto_overrides([td]), {})


class TestMinorUnitQuotes(unittest.TestCase):
    """A2-0379 / A2-0692: a quote for an LSE line (.L, quoted in pence
    or pounds) that does not state its unit is never valued as pounds:
    a live tier's unit-less hit is passed over, and a unit-less cache
    entry (written before S077-04) leaves the row out loudly.

    Formerly: test_fix_a2_planning_harvest.TestMinorUnitQuotes"""

    def _book(self, root):
        (root / "to_base.csv").write_text(
            f"{TODAY.isoformat()} 12:00:00 GBP CAD 1.85\n")
        return _write(root / "margin_gains_wash.json", _gains([
            {"symbol": "VOD.L", "qty": 1000, "total_cost": 1295.0,
             "position_start_date": _iso(-200)}]))

    def test_unit_less_live_quote_is_not_used(self):
        with tempfile.TemporaryDirectory() as td:
            g = self._book(Path(td))
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--json"],
                fetchers=[lambda rem: {s: (70.0, "fake-yf")
                                       for s in rem}])
        self.assertEqual(rc, 0, err)
        self.assertEqual(_json_rows(out)["rows"], [])
        self.assertIn("VOD.L", err)
        self.assertIn("pence", err)
        # Never cached as if it were a verified quote.
        self.assertFalse((Path(td) / ".price_cache.json").exists()
                         and "VOD.L" in (Path(td) / ".price_cache.json")
                         .read_text())

    def test_unit_less_live_quote_falls_through_to_next_tier(self):
        with tempfile.TemporaryDirectory() as td:
            g = self._book(Path(td))
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--json"],
                fetchers=[lambda rem: {s: (70.0, "ibkr") for s in rem},
                          lambda rem: {s: (0.70, "yfinance", "GBP")
                                       for s in rem}])
        self.assertEqual(rc, 0, err)
        row = _json_rows(out)["rows"][0]
        self.assertEqual(row["price_source"], "yfinance")
        self.assertAlmostEqual(row["value"], 1295.0)

    def test_pre_fix_cache_entry_without_currency_is_omitted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = self._book(root)
            _write(root / ".price_cache.json", {"VOD.L": {
                "price": 70.0, "asof": TODAY.isoformat(),
                "source": "yfinance", "quote_symbol": "VOD.L"}})
            # Audit 2026-10-24 A31: the entry is a cache MISS (pence or
            # pounds is unknown), so offline the run stops naming the
            # symbol, as for any symbol the cache lacks; online the
            # next tier prices it (below).
            with self.assertRaises(SystemExit) as cm:
                _run([str(g), "--no-ibkr", "--country", "canada",
                      "--json"], env={"TAXJSON_OFFLINE": "1"})
            self.assertIn("no current price in the cache: VOD.L",
                          str(cm.exception))
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--json"],
                fetchers=[lambda rem: {s: (0.70, "yfinance", "GBP")
                                       for s in rem}])
        self.assertEqual(rc, 0, err)
        row = _json_rows(out)["rows"][0]
        self.assertEqual(row["price_source"], "yfinance")
        self.assertAlmostEqual(row["value"], 1295.0)

    def test_cache_entry_with_currency_is_used(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = self._book(root)
            _write(root / ".price_cache.json", {"VOD.L": {
                "price": 0.70, "asof": TODAY.isoformat(),
                "source": "yfinance", "currency": "GBP",
                "quote_symbol": "VOD.L"}})
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--json"],
                env={"TAXJSON_OFFLINE": "1"})
        self.assertEqual(rc, 0, err)
        row = _json_rows(out)["rows"][0]
        self.assertAlmostEqual(row["value"], 1295.0)


class TestCachedCurrencyValidated(unittest.TestCase):
    """A2-1170: a cached quote's currency is checked like its price:
    stripped and upper-cased; a non-string never crashes the run. Since
    audit 2026-10-24 A40 such an entry is a cache MISS (said), not a
    quote with its currency dropped: offline the run stops naming the
    symbol, online the next tier prices it again.

    Formerly: test_fix_a2_planning_harvest.TestCachedCurrencyValidated"""

    def _go(self, cur, fetchers=None):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "to_base.csv").write_text(
                f"{TODAY.isoformat()} 12:00:00 USD CAD 1.25\n")
            g = _write(root / "margin_gains_wash.json", _gains([
                {"symbol": "AAPL.US", "qty": 10, "total_cost": 3000.0,
                 "position_start_date": _iso(-200)}]))
            _write(root / ".price_cache.json", {"AAPL.US": {
                "price": 200.0, "asof": TODAY.isoformat(),
                "source": "yfinance", "currency": cur,
                "quote_symbol": "AAPL"}})
            return _run([str(g), "--no-ibkr", "--country", "canada",
                         "--json"], fetchers=fetchers,
                        env={} if fetchers else {"TAXJSON_OFFLINE": "1"})

    def test_lower_case_currency_is_normalised(self):
        rc, out, err = self._go(" usd ")
        self.assertEqual(rc, 0, err)
        row = _json_rows(out)["rows"][0]
        self.assertEqual(row["price_currency"], "USD")
        self.assertAlmostEqual(row["value"], 2500.0)

    def test_non_string_currency_does_not_crash(self):
        for bad in (["x"], {"a": 1}, 5):
            with self.assertRaises(SystemExit) as cm:
                self._go(bad)
            self.assertNotIn("Traceback", str(cm.exception))
            self.assertIn("no current price in the cache: AAPL.US",
                          str(cm.exception))
            rc, out, err = self._go(
                bad, fetchers=[lambda rem: {s: (210.0, "yfinance", "USD")
                                            for s in rem}])
            self.assertEqual(rc, 0, err)
            self.assertNotIn("Traceback", err)
            row = _json_rows(out)["rows"][0]
            self.assertEqual((row["price_currency"], row["price_source"]),
                             ("USD", "yfinance"))


if __name__ == "__main__":
    unittest.main()
