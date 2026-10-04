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
from tax_rules import rule, rule_absent  # noqa: E402


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


# ------------------------------------------------- B1 split-share list
def _dist(symbol, desc=""):
    return {"action": "DIVIDEND", "symbol": symbol, "date": "2026-01-15",
            "currency": "CAD", "net_amount": 10.0, "gross_amount": 10.0,
            "income_label": "distribution", "record_date": "2025-12-31",
            "description": desc}


class TestSplitShareList(_MapCase):
    @rule("CA-INC-DATE-TRUST", "CA-INC-DATE-DIV")
    def test_ticker_map_adds_and_removes_a_split_share_corporation(self):
        from taxjson.lib.income_dating import IncomeRules
        self.use_map("SPLITSHARE ZZQ\nSPLITSHARE BK NO\n")
        rules = IncomeRules("canada")
        # a corporation: dated when paid
        self.assertEqual(rules.income_date(_dist("ZZQ.PR.A.TO")),
                         "2026-01-15")
        # removed from the built-in list: a trust, dated at record
        self.assertEqual(rules.income_date(_dist("BK.TO")), "2025-12-31")

    @rule("CA-INC-DATE-TRUST", "CA-INC-DATE-DIV")
    def test_builtin_list_decision_is_noted_once(self):
        from taxjson.lib.income_dating import IncomeRules
        self.no_map()
        rules = IncomeRules("canada")
        err = StringIO()
        with redirect_stderr(err):
            for _ in range(3):
                self.assertEqual(rules.income_date(_dist("BK.TO")),
                                 "2026-01-15")
        self.assertEqual(err.getvalue().count("SPLITSHARE BK NO"), 1,
                         err.getvalue())

    @rule("CA-INC-DATE-TRUST")
    @rule_absent("CA-INC-DATE-TRUST", country="usa")
    def test_split_share_list_is_canada_only(self):
        from taxjson.lib.income_dating import IncomeRules
        self.no_map()
        err = StringIO()
        with redirect_stderr(err):
            us = IncomeRules("usa").income_date(_dist("BK.TO"))
        # a US project: a foreign payment, dated when paid, and the
        # Canadian split-share list is never consulted
        self.assertEqual(us, "2026-01-15")
        self.assertNotIn("split-share", err.getvalue())
        self.assertEqual(
            IncomeRules("canada").income_date(_dist("ZZT.UN.TO")),
            "2025-12-31")

    @rule("CA-INC-DATE-TRUST")
    def test_tax_logic_prints_the_list_in_force(self):
        from taxjson.lib import tax_logic as TL
        self.use_map("SPLITSHARE ZZQ\nSPLITSHARE BK NO\n")
        text = TL.render("canada", {})
        self.assertIn("ZZQ", text)
        self.assertIn("SPLITSHARE", text)
        self.assertNotIn(" BK,", text)

    def test_no_split_share_table_left_in_code(self):
        import taxjson.lib.income_dating as ID
        self.assertFalse(hasattr(ID, "SPLIT_SHARE_ROOTS"))


# ------------------------------------------------- B16 map_ticker
class TestMapTickerNoCadTarget(unittest.TestCase):
    def test_listing_is_never_remapped(self):
        from taxjson.lib.ticker_map import map_ticker
        self.assertEqual(map_ticker("ZZQ.US"), "ZZQ.US")
        self.assertEqual(map_ticker("ZZQ.NYSE"), "ZZQ.NYSE")
        self.assertEqual(map_ticker("ZZQ.17DEC27.4.02.P"),
                         "ZZQ271217P00004020")


# ------------------------------------------------- B3 §1256 index roots
def _us8949(symbol, proceeds, cost):
    return {"date": "2025-10-24", "date_settle": "2025-10-24",
            "symbol": symbol, "qty": -1, "proceeds": proceeds,
            "cost": cost, "gain": proceeds - cost, "disallowed_amount": 0.0,
            "days_held": 4, "term": "SHORT_TERM", "direction": "LONG",
            "commission": 0.0, "fee": 0.0, "account": "margin",
            "currency": "USD", "is_option": True}


class TestIndexOptionRoots(_MapCase):
    @rule("US-OPT-04")
    def test_indexopt_line_adds_and_removes_a_root(self):
        from taxjson.bin import taxjson_form_export as FE
        self.use_map("INDEXOPT ZZX\nINDEXOPT SPX NO\n")
        rep = FE.build_8949([_us8949("ZZX251219C06000000.US", 500.0, 0.0),
                             _us8949("SPX251219C06000000.US", 700.0, 0.0)])
        self.assertEqual([r["description"] for r in rep["section_1256"]],
                         ["ZZX251219C06000000.US"])
        self.assertEqual([r["description"] for r in rep["part_I"]],
                         ["1 SPX251219C06000000.US (option)"])

    @rule("US-OPT-04")
    @rule_absent("US-OPT-04", country="canada")
    def test_canada_reports_an_index_option_on_schedule_3(self):
        from taxjson.bin import taxjson_form_export as FE
        self.no_map()
        e = dict(_us8949("SPX251219C06000000.US", 700.0, 0.0),
                 term=None, currency="CAD", date="2025-06-10",
                 date_settle="2025-06-10")
        err = StringIO()
        with redirect_stderr(err):
            rows = FE.build_schedule3([e], 2025)["rows"]
        # Canada: an ordinary option on Schedule 3; the US list is never
        # consulted (no §1256 note) ...
        self.assertEqual(len(rows), 1)
        self.assertNotIn("INDEXOPT", err.getvalue())
        # ... the US: off Form 8949, listed for Form 6781
        with redirect_stderr(StringIO()):
            us = FE.build_8949([_us8949("SPX251219C06000000.US", 700.0,
                                        0.0)])
        self.assertEqual(len(us["section_1256"]), 1)

    @rule("US-OPT-04")
    def test_builtin_root_is_noted_once(self):
        from taxjson.lib.futures import section_1256_kind
        self.no_map()
        err = StringIO()
        with redirect_stderr(err):
            for _ in range(2):
                self.assertEqual(section_1256_kind("XSP251219P00500000"),
                                 "index option")
        self.assertEqual(err.getvalue().count("INDEXOPT XSP NO"), 1)

    @rule("US-OPT-04")
    def test_tax_logic_lists_the_roots_in_force(self):
        from taxjson.lib import tax_logic as TL
        self.use_map("INDEXOPT ZZX\n")
        text = TL.render("usa", {})
        for root in ("SPXPM", "MRUT", "XEO", "ZZX"):   # all, not "weekly"
            self.assertIn(root, text)
        self.assertNotIn("INDEXOPT", TL.render("canada", {}))


# ------------------------------------------------- B4 evening session
class TestEveningSessionRoots(_MapCase):
    def _date(self, root, time="20:30:00"):
        from taxjson.lib.brokerages.ib_extractor import _ib_market_trade_date
        # a Monday evening
        return _ib_market_trade_date("2025-12-29", time,
                                     "Equity and Index Options", "USD", "",
                                     f"{root} 16JAN26 6000 C")[0]

    @rule("CA-DATE-SESSION")
    def test_builtin_root_moves_and_notes_once(self):
        self.no_map()
        err = StringIO()
        with redirect_stderr(err):
            self.assertEqual(self._date("VIXW"), "2025-12-30")
            self.assertEqual(self._date("VIXW"), "2025-12-30")
            self.assertEqual(self._date("ZZX"), "2025-12-29")
            self.assertEqual(self._date("VIXW", "16:00:00"), "2025-12-29")
        self.assertEqual(err.getvalue().count("EVENING VIXW NO"), 1)

    @rule("CA-DATE-SESSION")
    def test_evening_line_adds_and_removes_a_root(self):
        self.use_map("EVENING ZZX\nEVENING XSP NO\n")
        self.assertEqual(self._date("ZZX"), "2025-12-30")
        self.assertEqual(self._date("XSP"), "2025-12-29")

    def test_check_dates_reads_the_same_list(self):
        from taxjson.lib import check_dates as CD
        self.use_map("EVENING ZZX\n")
        self.assertTrue(CD._is_gth_root("ZZX"))
        self.assertTrue(CD._is_gth_root("VIXW"))
        self.assertFalse(CD._is_gth_root("ZZY"))

    @rule("CA-DATE-SESSION")
    def test_tax_logic_names_every_root_incl_weekly_vix(self):
        from taxjson.lib import tax_logic as TL
        self.no_map()
        for c in ("canada", "usa"):
            self.assertIn("VIXW", TL.render(c, {}))


if __name__ == "__main__":
    unittest.main()
