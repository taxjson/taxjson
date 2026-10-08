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
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


_SAVED_TZ = None


def setUpModule():
    # The crypto parsers outside a project need a named zone (no default
    # since the same generalisation, A2): set one, restore it after.
    _WIDTH.start()
    global _SAVED_TZ
    _SAVED_TZ = os.environ.get("TAXJSON_LOCAL_TZ")
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


def tearDownModule():
    _WIDTH.stop()
    if _SAVED_TZ is None:
        os.environ.pop("TAXJSON_LOCAL_TZ", None)
    else:
        os.environ["TAXJSON_LOCAL_TZ"] = _SAVED_TZ


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
        self.assertEqual(markets.ib_venue_suffix("LSE"), "")
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


# --------------------------------------------- B5-B9 crypto lists
def _kr(files, target, cash=True):
    from test_fix_l_parsers2_kraken import _parse
    return _parse(files, target, cash=cash)


def _kl(*rows):
    from test_fix_m_parsers2_crypto import KR_LEDGER_H
    return KR_LEDGER_H + "".join(r + "\n" for r in rows)


class TestStablecoinList(_MapCase):
    def test_one_list_kraken_copy_gone(self):
        from taxjson.lib.brokerages import kraken as K
        self.assertFalse(hasattr(K, "_USD_SUFFIX_STABLES"))
        self.no_map()
        # every former Kraken-only "stablecoin ending in USD" is in the one
        # list, and a legacy pair ending in one is still refused
        for s in ("PYUSD", "RLUSD", "FDUSD", "GUSD"):
            self.assertIn(s, markets.usd_stablecoins())
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                K._split_pair(f"ETH{s}")

    @rule("CA-CRYPTO-02")
    @rule_absent("CA-CRYPTO-02", country="usa")
    @rule("US-CRYPTO-02")
    def test_stable_line_makes_a_coin_us_dollar_cash_in_canada(self):
        self.use_map("STABLE ZZUSD USD\n")
        led = _kl("L1,R1,2025-03-01 12:00:00,earn,reward,currency,,ZZUSD,"
                  "spot,5,0,5")
        ca, _ = _kr({"kr_ledgers.csv": led}, "kr_ledgers.csv", cash=True)
        us, _ = _kr({"kr_ledgers.csv": led}, "kr_ledgers.csv", cash=False)
        # Canada: US-dollar cash income, no coin position; USA: the
        # reward acquires the coin (property)
        self.assertEqual([t["action"] for t in ca], ["DIVIDEND"])
        self.assertEqual(ca[0]["currency"], "USD")
        self.assertIn(("BUYSELL", "ZZUSD"),
                      {(t["action"], t["symbol"]) for t in us})

    @rule("CA-CRYPTO-02")
    def test_stable_no_line_makes_a_builtin_coin_property(self):
        self.use_map("STABLE USDT NO\n")
        led = _kl("L1,R1,2025-03-01 12:00:00,earn,reward,currency,,USDT,"
                  "spot,5,0,5")
        ca, err = _kr({"kr_ledgers.csv": led}, "kr_ledgers.csv", cash=True)
        self.assertIn(("BUYSELL", "USDT"),
                      {(t["action"], t["symbol"]) for t in ca})
        self.assertNotIn("note: USDT", err)

    @rule("CA-CRYPTO-02")
    def test_builtin_stablecoin_noted_once(self):
        self.no_map()
        led = _kl("L1,R1,2025-03-01 12:00:00,earn,reward,currency,,USDC,"
                  "spot,5,0,5",
                  "L2,R2,2025-03-02 12:00:00,earn,reward,currency,,USDC,"
                  "spot,5,0,10")
        _txs, err = _kr({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertEqual(err.count("STABLE USDC NO"), 1, err)

    @rule("CA-CRYPTO-02")
    def test_tax_logic_lists_the_coins_in_force(self):
        from taxjson.lib import tax_logic as TL
        self.use_map("STABLE ZZUSD USD\nSTABLE DAI NO\n")
        text = TL.catalog("canada")["CA-CRYPTO-02"].text
        self.assertIn("ZZUSD", text)
        self.assertNotIn("DAI,", text)


@rule("CA-CRYPTO-01")
class TestNoBuiltinStakedFold(_MapCase):
    def _cb_convert(self):
        from test_fix_m_parsers2_crypto import _bs, _cb_row, _parse_cb
        txs, err = _parse_cb(
            _cb_row("b1", "2025-01-02 10:00:00 UTC", "Buy", "ZZC", "1",
                    "CAD", "2000", "2000", "2000", "0")
            + _cb_row("c1", "2025-06-02 10:00:00 UTC", "Convert", "ZZC",
                      "-1", "CAD", "4000", "4000", "4000", "0",
                      "Converted 1 ZZC to 1 ZZC2"))
        return _bs(txs), err

    def test_without_a_line_a_staked_code_is_its_own_coin(self):
        self.no_map()
        bs, err = self._cb_convert()
        self.assertEqual(sorted(t["symbol"] for t in bs),
                         ["ZZC", "ZZC", "ZZC2"])
        self.assertIn("GLOBAL ZZC2 ZZC", err)
        # nothing named in the code: the old ETH2 fold is gone too
        from taxjson.lib.brokerages.coinbase import _cb_symbol
        from taxjson.lib.brokerages.kraken import _normalize_asset
        self.assertEqual(_cb_symbol("ETH2"), "ETH2")
        self.assertEqual(_normalize_asset("ETH2"), "ETH2")

    def test_global_line_folds_it_before_the_parse(self):
        self.use_map("GLOBAL ZZC2 ZZC\n")
        bs, err = self._cb_convert()
        self.assertEqual([t["symbol"] for t in bs], ["ZZC"])  # the buy
        self.assertNotIn("GLOBAL ZZC2 ZZC", err)


class TestKrakenCodes(_MapCase):
    FORMER = {"XXBT": "BTC", "XBT": "BTC", "XETH": "ETH", "XLTC": "LTC",
              "XXRP": "XRP", "XXLM": "XLM", "XXMR": "XMR", "XZEC": "ZEC",
              "XXDG": "DOGE", "XDG": "DOGE", "XETC": "ETC", "XMLN": "MLN",
              "XREP": "REP", "ZUSD": "USD", "ZJPY": "JPY", "ZEC": "ZEC",
              "XTZ": "XTZ"}

    def test_legacy_codes_from_market_data(self):
        from taxjson.lib.brokerages.kraken import _normalize_asset
        self.no_map()
        for raw, want in self.FORMER.items():
            self.assertEqual(_normalize_asset(raw, fold_stable=False), want,
                             raw)

    def test_global_line_adds_a_legacy_code(self):
        from taxjson.lib.brokerages.kraken import _normalize_asset
        self.use_map("GLOBAL XZZQ ZZQ\n")
        self.assertEqual(_normalize_asset("XZZQ"), "ZZQ")

    @rule("CA-CRYPTO-01")
    def test_bonded_code_without_its_coin_is_kept_and_noted(self):
        self.no_map()
        led = _kl("LS1,RS1,2025-06-01 10:00:00,staking,,currency,,ZZQ28.S,"
                  "spot,2.0,0,2.0")
        txs, err = _kr({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertEqual({t["symbol"] for t in txs}, {"ZZQ28"})
        self.assertIn("GLOBAL ZZQ28 ZZQ", err)

    @rule("CA-CRYPTO-01")
    def test_bonded_code_any_lock_period_folds_into_its_coin(self):
        self.no_map()
        led = _kl("LB1,RB1,2025-05-01 10:00:00,transfer,spottostaking,"
                  "currency,,ZZQ,spot,-2.0,0,0",
                  "LS1,RS1,2025-06-01 10:00:00,staking,,currency,,ZZQ90.S,"
                  "spot,2.0,0,2.0")
        txs, _ = _kr({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertEqual({t["symbol"] for t in txs}, {"ZZQ"})

    def test_legacy_pairs_from_the_fiat_and_stablecoin_lists(self):
        from taxjson.lib.brokerages.kraken import _split_pair
        self.no_map()
        for pair, want in (("XXBTZUSD", ("XBT", "USD")),
                           ("XXBTZJPY", ("XBT", "JPY")),
                           ("XETHXXBT", ("ETH", "XBT")),
                           ("USDTZUSD", ("USDT", "USD")),
                           ("ZUSDZCAD", ("USD", "CAD")),
                           ("ADAUSD", ("ADA", "USD")),
                           ("SOLUSDT", ("SOL", "USDT")),
                           ("XTZUSD", ("XTZ", "USD")),
                           ("ADACHF", ("ADA", "CHF"))):
            self.assertEqual(_split_pair(pair), want, pair)
        self.use_map("STABLE ZZST USD\n")
        self.assertEqual(_split_pair("ZZSTZEUR"), ("ZZST", "EUR"))


# ------------------------------------------------- B2 RBC USD class
class TestRbcUsdClassByExtract(_MapCase):
    def _brokerage(self, ticker_map):
        import json
        import subprocess
        from test_rbc_parse_audit_2026_09 import HDR, row
        body = (row("March 15, 2024", "Buy", "ZZD",
                    "SAMPLE U S DLR CURRENCY ETF UNIT", "100", "10",
                    "-1009.95", "USD", "SAMPLE U S DLR CURRENCY ETF DA")
                + row("March 13, 2024", "Sell", "ZZD",
                      "SAMPLE U S DLR CURRENCY ETF UNIT", "-100", "13.5",
                      "1340.05", "CAD", "SAMPLE U S DLR CURRENCY ETF CA"))
        csv = self.tmp / "rbc.csv"
        csv.write_text(HDR + body)
        argv = [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
                "--brokerage", "rbc_direct", "--account", "margin"]
        if ticker_map:
            tm = self.tmp / "ticker.map"
            tm.write_text(ticker_map)
            argv += ["--ticker-map", str(tm), "--security-overrides",
                     str(tm)]
        env = dict(os.environ, PYTHONPATH=str(
            Path(__file__).resolve().parent.parent / "src"))
        r = subprocess.run(argv + [str(csv)], capture_output=True,
                           text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return ([t["symbol"] for t in json.loads(r.stdout)["transactions"]],
                r.stderr)

    def test_extract_line_moves_the_usd_rows_quietly(self):
        syms, err = self._brokerage(
            "EXTRACT SAMPLE U S DLR CURRENCY ETF | USD | ZZD.U.TO\n")
        self.assertEqual(sorted(syms), ["ZZD.TO", "ZZD.U.TO"])
        self.assertNotIn("US-dollar class", err)

    def test_without_the_line_it_is_said(self):
        syms, err = self._brokerage("")
        self.assertEqual(sorted(syms), ["ZZD.TO", "ZZD.US"])
        self.assertIn("EXTRACT SAMPLE U S DLR CURRENCY ETF | USD | ZZD.U.TO",
                      err)

    def test_no_named_security_left_in_the_parser(self):
        from taxjson.lib.brokerages import rbc_direct
        src = Path(rbc_direct.__file__).read_text()
        self.assertNotIn("_RBC_USD_DLR_RE", src)
        self.assertNotIn("'DLR.U.TO'", src)


# ------------------------------------------- C one venue / currency table
class TestOneVenueTable(_MapCase):
    """Each former copy of the suffix / currency / ISO tables reads the
    market data now; every case the copy handled still holds, and the
    gaps between copies (.VN, .V in corp actions) are closed."""

    FORMER_CA = ("TO", "V", "CN", "NE", "VN")

    def test_income_dating_and_dates(self):
        from taxjson.lib import dates, income_dating
        self.assertEqual(set(income_dating.CA_LISTING_SUFFIXES),
                         set(self.FORMER_CA))
        for s in self.FORMER_CA:
            self.assertEqual(dates.market_of(f"ZZQ.{s}", "USD"), "CAD")
        self.assertEqual(dates.market_of("ZZQ.US", "CAD"), "USD")
        self.assertEqual(dates.market_of("ZZQ.L", "USD"), "GBP")
        self.assertEqual(dates.market_of("ZZQ.AX", "USD"), "AUD")
        self.assertEqual(dates.market_of("ZZQ.DE", "EUR"), "EUR")

    def test_check_dates_schema_generic(self):
        from taxjson.lib import check_dates
        from taxjson.lib.brokerages import generic, schema
        for s in self.FORMER_CA:
            self.assertEqual(check_dates.asset_class(f"ZZQ.{s}", False),
                             "ca-equity")
        self.assertEqual(set(schema.KNOWN_SUFFIXES),
                         set(self.FORMER_CA) | {"US", "AX", "L"})
        self.assertIn("VN", generic._KNOWN_SUFFIXES)   # was missing

    def test_export_reconcile_handoff(self):
        from types import SimpleNamespace
        from taxjson.bin import taxjson_export as EX
        from taxjson.bin import taxjson_reconcile_slips as RS
        from taxjson.lib import handoff
        args = SimpleNamespace(long=False, short=False, no_options=False,
                               no_futures=False, no_equities=False,
                               no_cad=True, no_usd=False)
        for s in self.FORMER_CA:     # .VN passed --no-cad before
            self.assertFalse(EX._passes_filters({"symbol": f"ZZQ.{s}",
                                                 "qty": 1}, args), s)
        for s in self.FORMER_CA + ("US", "AX", "L"):
            self.assertEqual(RS._SUFFIX_RE.sub("", f"ZZQ.{s}"), "ZZQ")
            self.assertEqual(handoff._root_sym(f"zzq.{s}"), "ZZQ")
        self.assertEqual(handoff._root_sym("ZZQ.B"), "ZZQ.B")

    def test_price_chain_quote_currency_unchanged(self):
        from taxjson.lib.price_chain import quote_currency
        for sym, cur in (("ZZQ.US", "USD"), ("ZZQ.TO", "CAD"),
                         ("ZZQ.V", "CAD"), ("ZZQ.CN", "CAD"),
                         ("ZZQ.NE", "CAD"), ("ZZQ.L", "GBP"),
                         ("ZZQ.AX", "AUD"), ("ZZQ-U.TO", "USD"),
                         ("ETH-JPY", "JPY"), ("ZZQ", "USD")):
            self.assertEqual(quote_currency(sym), cur, sym)
        # Yahoo's own .VN is another market, never Questrade's venue
        self.assertIsNone(quote_currency("ZZQ.VN"))

    def test_corp_actions_suffixes(self):
        from taxjson.lib import corp_actions as CA
        self.assertEqual(CA._CURRENCY_SUFFIX,
                         {"CAD": "TO", "USD": "US", "AUD": "AX", "GBP": "L"})
        self.assertEqual(CA._apply_suffix("ZZQ.V", "TO"), "ZZQ.V")  # was .V.TO
        self.assertEqual(CA._apply_suffix("ZZQ.B", "US"), "ZZQ.B.US")
        self.assertEqual(CA._apply_suffix("ZZQ.TO", "TO"), "ZZQ.TO")

    def test_t1135_domicile_by_suffix(self):
        from taxjson.bin import taxjson_t1135 as T
        self.assertEqual(T._SUFFIX_COUNTRY["US"], "USA")
        self.assertEqual(T._SUFFIX_COUNTRY["L"], "GBR")
        self.assertEqual(T._SUFFIX_COUNTRY["AX"], "AUS")
        for s in self.FORMER_CA:
            self.assertIsNone(T._SUFFIX_COUNTRY[s])

    def test_parsers_currency_suffix(self):
        from taxjson.lib.brokerages.base import BaseBrokerage
        from taxjson.lib.brokerages.webull import WebullBrokerage
        from taxjson.lib.brokerages import ib_extractor as IB
        b = BaseBrokerage()
        self.assertEqual(BaseBrokerage.CURRENCY_EXT_MAP,
                         {"CAD": "TO", "USD": "US", "AUD": "AX", "GBP": "L"})
        self.assertEqual(b.apply_currency_suffix("ZZQ", "usd"), "ZZQ.US")
        self.assertEqual(b.apply_currency_suffix("ZZQ.VN", "CAD"), "ZZQ.TO")
        self.assertEqual(b.apply_currency_suffix("ZZQ.L", "AUD"), "ZZQ.AX")
        self.assertIsNone(WebullBrokerage.CURRENCY_EXT_FALLBACK)
        self.assertEqual(IB._ib_currency_ext("AUD"), "AX")
        self.assertEqual(IB._split_known_ext("ZZQ.L"), ("ZZQ", "L"))
        self.assertEqual(IB._split_known_ext("ZZQ.V"), ("ZZQ.V", None))
        fb = set()
        for cc, ext in (("CA", "TO"), ("AU", "AX"), ("GB", "L"),
                        ("IE", "L"), ("US", "US")):
            self.assertEqual(IB._isin_ext(cc + "0000000000", "ZZQ", fb), ext)
        self.assertEqual(IB._isin_ext("DE0000000000", "ZZQ", fb), "US")
        self.assertIn(("ZZQ.US", "DE"), fb)
        former_tags = {
            'CAD', 'USD', 'EUR', 'GBP', 'AUD', 'CHF', 'JPY', 'HKD', 'SEK',
            'NOK', 'DKK', 'NZD', 'SGD', 'CNH', 'CNY', 'MXN', 'ILS', 'ZAR',
            'KRW', 'INR', 'PLN', 'CZK', 'HUF', 'TRY'}
        self.assertTrue(former_tags <= set(IB._IB_CURRENCY_TAGS))

    def test_rbc_questrade_strip_every_venue(self):
        from taxjson.lib.brokerages.questrade import _journal_root
        from taxjson.lib.brokerages.rbc_direct import _names_underlying
        for s in self.FORMER_CA + ("US",):
            self.assertEqual(_journal_root(f"ZZQ.U.{s}"), "ZZQ")
            self.assertTrue(_names_underlying("ZZQ", f"ZZQ.{s}"))


# ------------------------------------------------- B17 IB venues
class TestIbVenues(_MapCase):
    FORMER_CA = ('TSE', 'VENTURE', 'TSXV', 'CSE', 'NEO', 'AEQLIT', 'PURE',
                 'OMEGA', 'CHIXCA', 'ALPHA', 'LYNX')

    def _ext(self, sym, cur, exch):
        from taxjson.lib.brokerages.ib_extractor import _ib_listing_ext
        return _ib_listing_ext("Stocks", sym, cur,
                               {("Stocks", sym): {"exch": exch}})

    def test_former_venues_unchanged(self):
        self.no_map()
        for v in self.FORMER_CA:
            self.assertEqual(self._ext("ZZQ.U", "USD", v), "TO", v)
            self.assertEqual(self._ext("ZZQ", "USD", v), "US", v)
        for v in ("LSE", "LSEETF", "LSEIOB1"):
            self.assertEqual(self._ext("ZZQ", "USD", v), "L", v)
        self.assertEqual(self._ext("ZZQ", "USD", "NYSE"), "US")

    def test_venue_line_adds_and_removes(self):
        self.use_map("VENUE ZZEX L\nVENUE LSEETF NO\n")
        self.assertEqual(self._ext("ZZQ", "USD", "ZZEX"), "L")
        err = StringIO()
        with redirect_stderr(err):
            self.assertEqual(self._ext("ZZQ", "USD", "LSEETF"), "US")
        self.assertEqual(err.getvalue(), "")   # a US line: nothing to say

    def test_unknown_venue_is_noted_once(self):
        # the silent case: a USD line on a venue the data lacks stays .US
        self.no_map()
        err = StringIO()
        with redirect_stderr(err):
            self.assertEqual(self._ext("ZZQ", "USD", "ZZEX"), "US")
            self.assertEqual(self._ext("ZZR", "USD", "ZZEX"), "US")
            self.assertEqual(self._ext("ZZQ", "USD", "NASDAQ"), "US")
        self.assertEqual(err.getvalue().count("VENUE ZZEX SUFFIX"), 1)


if __name__ == "__main__":
    unittest.main()
