"""No hard-coded security data in the flow (owner, 2026-10-04): the one
shipped market-data file (taxjson/data/markets.toml, lib/markets) and
the ticker.map lines that extend or override it."""
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from taxjson.lib import markets  # noqa: E402
from taxjson.lib.ticker_map import read_side_rules  # noqa: E402


class _MapCase(unittest.TestCase):
    """A ticker.map in a temp dir, read through TAXJSON_TICKER_MAP."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self._env = os.environ.get(markets.ENV_TICKER_MAP)
        markets.use_ticker_map(None)
        markets.reset_notes()

    def tearDown(self):
        if self._env is None:
            os.environ.pop(markets.ENV_TICKER_MAP, None)
        else:
            os.environ[markets.ENV_TICKER_MAP] = self._env
        markets.use_ticker_map(None)
        markets.reset_notes()
        self._td.cleanup()

    def use_map(self, text: str) -> Path:
        p = self.tmp / "ticker.map"
        p.write_text(text)
        # a fresh mtime key even within one clock tick
        st = p.stat()
        os.utime(p, (st.st_atime, st.st_mtime + len(text)))
        os.environ[markets.ENV_TICKER_MAP] = str(p)
        return p

    def no_map(self):
        os.environ[markets.ENV_TICKER_MAP] = ""


class TestShippedData(unittest.TestCase):
    def test_data_file_is_package_data(self):
        self.assertTrue(markets.DATA_FILE.is_file())
        root = Path(__file__).resolve().parent.parent
        self.assertIn('taxjson = ["data/*.toml"]',
                      (root / "pyproject.toml").read_text())

    def test_venue_tables(self):
        self.assertEqual(markets.canadian_suffixes(),
                         {"TO", "V", "CN", "NE", "VN"})
        self.assertEqual(markets.known_suffixes(),
                         {"TO", "V", "CN", "NE", "VN", "US", "L", "AX"})
        self.assertEqual(markets.suffix_currency("VN"), "CAD")
        self.assertEqual(markets.suffix_country("L"), "GBR")
        self.assertEqual(markets.currency_suffix("aud"), "AX")
        self.assertIsNone(markets.currency_suffix("EUR"))
        self.assertEqual(markets.isin_country_suffix("IE"), "L")

    def test_one_fiat_list_covers_every_former_copy(self):
        former = {  # Coinbase / Kraken / IB currency tags / Yahoo pairs
            'USD', 'CAD', 'EUR', 'GBP', 'AUD', 'NZD', 'JPY', 'CHF', 'SGD',
            'HKD', 'SEK', 'NOK', 'DKK', 'PLN', 'CZK', 'BRL', 'MXN', 'INR',
            'ZAR', 'TRY', 'KRW', 'CNY', 'AED', 'ILS', 'CNH', 'HUF'}
        self.assertEqual(markets.fiat_currencies(), former)


class TestTickerMapKeywords(_MapCase):
    def test_parse_every_market_keyword(self):
        p = self.use_map(
            "STABLE ZZUSD USD\nSTABLE USDT NO\nSPLITSHARE ZZQ\n"
            "SPLITSHARE BK NO\nINDEXOPT ZZX\nEVENING ZZX yes\n"
            "MULT ZZQ1 50\nVENUE ZZEX TO\nVENUE LSE NO\n")
        r = read_side_rules(p)
        self.assertEqual(r.problems, [])
        self.assertEqual(r.stable, {"ZZUSD": True, "USDT": False})
        self.assertEqual(r.splitshare, {"ZZQ": True, "BK": False})
        self.assertEqual(r.indexopt, {"ZZX": True})
        self.assertEqual(r.evening, {"ZZX": True})
        self.assertEqual(r.mult, {"ZZQ1": 50.0})
        self.assertEqual(r.venue, {"ZZEX": "TO", "LSE": None})

    def test_bad_lines_are_problems(self):
        p = self.use_map("STABLE ZZ EUR\nMULT ZZ1 -3\nVENUE ZZEX QQ\n"
                         "SPLITSHARE ZZ MAYBE\n")
        r = read_side_rules(p)
        self.assertEqual(len(r.problems), 4, r.problems)

    def test_rename_parser_refuses_a_bad_market_line(self):
        from taxjson.bin.taxjson_ticker_map import _parse_map_file
        p = self.use_map("GLOBAL ZZA.US ZZB.US\nMULT ZZ1 zero\n")
        problems = _parse_map_file(p)[1]
        self.assertTrue(any("MULT" in m for m in problems), problems)

    def test_overrides_apply(self):
        self.use_map("STABLE ZZUSD USD\nSTABLE USDT NO\nSPLITSHARE ZZQ.TO\n"
                     "SPLITSHARE BK NO\nINDEXOPT ZZX\nINDEXOPT SPX NO\n"
                     "EVENING ZZX\nMULT ZZQ1 10\nVENUE LSE NO\n"
                     "VENUE ZZEX TO\nGLOBAL ZZSTK ZZC\n")
        self.assertIn("ZZUSD", markets.usd_stablecoins())
        self.assertNotIn("USDT", markets.usd_stablecoins())
        self.assertTrue(markets.is_usd_stablecoin("ZZUSD"))
        self.assertFalse(markets.is_usd_stablecoin("USDT"))
        self.assertTrue(markets.is_split_share_root("ZZQ"))
        self.assertFalse(markets.is_split_share_root("BK"))
        self.assertTrue(markets.is_index_option_root("ZZX"))
        self.assertFalse(markets.is_index_option_root("SPX"))
        self.assertTrue(markets.is_evening_session_root("ZZX"))
        self.assertEqual(markets.contract_size("ZZQ1", ""), 10.0)
        self.assertEqual(markets.contract_size("ZZQ1250117C00010000",
                                               "ZZQ1"), 10.0)
        self.assertIsNone(markets.contract_size("ZZR", "ZZR"))
        self.assertIsNone(markets.ib_venue_suffix("LSE"))
        self.assertEqual(markets.ib_venue_suffix("zzex"), "TO")
        self.assertEqual(markets.crypto_alias("zzstk"), "ZZC")

    def test_no_map_is_the_builtin_lists(self):
        self.no_map()
        self.assertEqual(markets.usd_stablecoins(),
                         markets.builtin_usd_stablecoins())
        self.assertEqual(markets.ib_venue_suffix("LSE"), "L")
        self.assertEqual(markets.crypto_alias("ETH2"), "ETH2")

    def test_builtin_decision_is_noted_once(self):
        self.no_map()
        err = StringIO()
        with redirect_stderr(err):
            self.assertTrue(markets.is_split_share_root("BK"))
            self.assertTrue(markets.is_split_share_root("BK"))
            self.assertFalse(markets.is_split_share_root("ZZQ"))
        out = err.getvalue()
        self.assertEqual(out.count("note:"), 1, out)
        self.assertIn("SPLITSHARE BK NO", out)

    def test_ticker_map_decision_is_not_noted(self):
        self.use_map("SPLITSHARE BK\n")
        err = StringIO()
        with redirect_stderr(err):
            self.assertTrue(markets.is_split_share_root("BK"))
        self.assertEqual(err.getvalue(), "")

    def test_explicit_map_wins_over_environment(self):
        other = self.tmp / "other.map"
        other.write_text("STABLE ZZOTHER USD\n")
        self.use_map("STABLE ZZENV USD\n")
        markets.use_ticker_map(other)
        self.assertIn("ZZOTHER", markets.usd_stablecoins())
        self.assertNotIn("ZZENV", markets.usd_stablecoins())
        markets.use_ticker_map(None)
        self.assertIn("ZZENV", markets.usd_stablecoins())


if __name__ == "__main__":
    unittest.main()
