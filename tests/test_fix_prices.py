"""Price units and price-cache identity (GitHub issues #75, #82).

#75: Yahoo's history adapter dropped the unit Yahoo names ('GBp'), so a
125p close on an LSE line was read as 125 pounds (the '.L' suffix says
GBP) and an in-kind move was booked at 100 times its value. The unit is
now kept through the adapter and the close cache, normalised by the one
table the live quote uses, and a close that names no unit on a market
that quotes in pence/cents is refused.

#82: the current-price cache was keyed only by the book symbol, so after
a QUOTE / CRYPTO line remapped it the previous asset's price was served.
Entries now record the spelling they were fetched for.

Every fixture is synthetic: invented tickers, prices and dates; the
providers are mocked (no network).
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tax_rules import rule

from taxjson.lib import price_chain as PC

DAY = "2025-02-03"


def _fake_yf(currency, closes):
    """A stand-in yfinance module whose Ticker reports `currency` in its
    history metadata and returns `closes` ({day: close})."""
    class Ticker:
        def __init__(self, symbol):
            self.history_metadata = ({"currency": currency}
                                     if currency is not None else {})

        def history(self, **kw):
            return {"Close": dict(closes)}
    return SimpleNamespace(Ticker=Ticker)


def _close(quote_symbol, currency, close, cp, symbol=None):
    err = io.StringIO()
    with patch.dict(sys.modules,
                    {"yfinance": _fake_yf(currency, {DAY: close})}), \
            redirect_stderr(err):
        c = PC.close_on(symbol or quote_symbol, quote_symbol, DAY,
                        cache_path=cp, offline=False)
    return c, err.getvalue()


class TestHistoryUnits(unittest.TestCase):
    """#75: the day's close keeps the provider's unit."""

    def test_history_adapter_keeps_and_normalises_the_unit(self):
        with patch.dict(sys.modules,
                        {"yfinance": _fake_yf("GBp", {DAY: 125.0})}):
            got = PC._yf_history_fetcher({"SYN.L": "SYN.L"}, start=DAY,
                                         end=DAY, verbose=False)
        self.assertEqual(got, {"SYN.L": ({DAY: 1.25}, "GBP")})

    @rule("US-INKIND-03")
    def test_pence_close_is_pounds_and_cached_with_its_unit(self):
        with tempfile.TemporaryDirectory() as td:
            cp = Path(td) / ".close_cache.json"
            c, _ = _close("SYN.L", "GBp", 125.0, cp)
            self.assertEqual((c.price, c.currency), (1.25, "GBP"))
            rec = json.loads(cp.read_text())[f"SYN.L|SYN.L@{DAY}"]
            self.assertEqual((rec["price"], rec["currency"], rec["unit"]),
                             (1.25, "GBP", "provider"))
            # Served from the cache, offline, still in pounds.
            c = PC.close_on("SYN.L", "SYN.L", DAY, cache_path=cp,
                            offline=True)
            self.assertEqual((c.price, c.currency, c.source),
                             (1.25, "GBP", "cache"))

    def test_other_minor_units(self):
        with tempfile.TemporaryDirectory() as td:
            cp = Path(td) / ".close_cache.json"
            for qs, unit, major in (("SYN.JO", "ZAc", "ZAR"),
                                    ("SYN.TA", "ILA", "ILS"),
                                    ("SYO.L", "GBX", "GBP")):
                c, _ = _close(qs, unit, 500.0, cp)
                self.assertEqual((c.price, c.currency), (5.0, major), qs)

    def test_provider_currency_beats_the_suffix(self):
        # An LSE line quoted in dollars: Yahoo's 'USD', not the suffix.
        with tempfile.TemporaryDirectory() as td:
            c, _ = _close("SYN.L", "USD", 40.0,
                          Path(td) / ".close_cache.json")
            self.assertEqual((c.price, c.currency), (40.0, "USD"))

    def test_unit_less_close_on_a_minor_unit_market_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            cp = Path(td) / ".close_cache.json"
            c, err = _close("SYN.L", None, 125.0, cp)
            self.assertIsNone(c)
            self.assertIn("did not say its unit", err)
            self.assertFalse(cp.exists())
            # A test tier with no unit, likewise (the listing's suffix
            # is never read as the unit there).
            with redirect_stderr(io.StringIO()):
                c = PC.close_on("SYN.L", "SYN.L", DAY, cache_path=cp,
                                fetchers=[lambda p, s, e: {"SYN.L":
                                                           {DAY: 125.0}}])
            self.assertIsNone(c)

    def test_unit_less_close_elsewhere_takes_the_listing_currency(self):
        with tempfile.TemporaryDirectory() as td:
            c, _ = _close("SYN.TO", None, 12.0,
                          Path(td) / ".close_cache.json")
            self.assertEqual((c.price, c.currency), (12.0, "CAD"))

    def test_an_unknown_minor_unit_is_refused_not_guessed(self):
        self.assertEqual(PC.clean_quote_currency(125.0, "USd")[1], None)
        self.assertIn("minor unit", PC.clean_quote_currency(125.0, "USd")[2])
        with tempfile.TemporaryDirectory() as td:
            cp = Path(td) / ".close_cache.json"
            c, err = _close("SYN.US", "USd", 125.0, cp, symbol="SYN.US")
            self.assertIsNone(c)
            self.assertIn("'USd'", err)
            self.assertFalse(cp.exists())

    def test_old_cache_entry_on_a_minor_unit_market_is_a_miss(self):
        # Written before the unit was kept: currency read off '.L'.
        with tempfile.TemporaryDirectory() as td:
            cp = Path(td) / ".close_cache.json"
            cp.write_text(json.dumps({
                f"SYN.L|SYN.L@{DAY}": {"price": 125.0, "day": DAY,
                                        "currency": "GBP",
                                        "source": "yfinance"},
                f"SYN.TO|SYN.TO@{DAY}": {"price": 12.0, "day": DAY,
                                          "currency": "CAD",
                                          "source": "yfinance"}}))
            with self.assertRaises(PC.OfflineCloseMissing):
                PC.close_on("SYN.L", "SYN.L", DAY, cache_path=cp,
                            offline=True)
            # Online it is fetched again, with its unit.
            c, _ = _close("SYN.L", "GBp", 125.0, cp)
            self.assertEqual((c.price, c.currency, c.source),
                             (1.25, "GBP", "yfinance"))
            # Elsewhere the old entry still serves (its suffix is its
            # currency).
            c = PC.close_on("SYN.TO", "SYN.TO", DAY, cache_path=cp,
                            offline=True)
            self.assertEqual((c.price, c.currency), (12.0, "CAD"))

    def test_live_quote_shares_the_unit_table(self):
        with patch.dict(sys.modules,
                        {"yfinance": SimpleNamespace(Ticker=lambda s:
                                                     _LiveTicker("ZAc"))}):
            got = PC._yfinance_fetcher({"SYN.JO": "SYN.JO"}, verbose=False)
        self.assertEqual(got, {"SYN.JO": (5.0, "ZAR")})
        err = io.StringIO()
        with patch.dict(sys.modules,
                        {"yfinance": SimpleNamespace(Ticker=lambda s:
                                                     _LiveTicker("USd"))}), \
                redirect_stderr(err):
            got = PC._yfinance_fetcher({"SYN.US": "SYN"}, verbose=False)
        self.assertEqual(got, {})
        self.assertIn("'USd'", err.getvalue())


class _LiveTicker:
    def __init__(self, unit):
        self.history_metadata = {"currency": unit}

    def history(self, **kw):
        return _Hist()


class _Hist:
    empty = False

    def __getitem__(self, k):
        return SimpleNamespace(iloc=[500.0])


class TestInKindPenceEndToEnd(unittest.TestCase):
    """#75's reproduction: an LSE line contributed in kind to an RRSP,
    valued from Yahoo's pence close, through the Canadian engine."""

    @rule("CA-INKIND-06")
    def test_contribution_gain_uses_pounds_not_pence(self):
        from taxjson.lib import in_kind as IK
        from taxjson.lib.core import (CanadaTaxRules, TaxTransaction,
                                      coerce_transaction_row)
        from taxjson.bin.taxjson_convert_currency import process_transactions
        with tempfile.TemporaryDirectory() as td:
            close, _ = _close("SYN.L", "GBp", 125.0,
                              Path(td) / ".close_cache.json")
        leg = IK.Leg("a", "synthetic", {"date": DAY, "symbol": "SYN.L",
                                        "quantity": -100,
                                        "currency": "GBP"}, "SYN.L")
        m = IK.Move(IK.CONTRIBUTION, leg, None, "rrsp", 100, DAY)
        IK.value([m], lambda move: close)
        self.assertEqual((m.fmv, m.currency), (125.0, "GBP"))
        sale = coerce_transaction_row(
            IK.booked_rows([m], "canada")["a"][0], 0, "synthetic")
        buy = TaxTransaction(action="BUYSELL", date="2024-01-08",
                             symbol="SYN.L", quantity=100, currency="CAD",
                             net_amount=100, account="a")
        conv = process_transactions([sale], "CAD",
                                    {"GBP": {DAY: Decimal("2")}}, None,
                                    country="canada")
        r = CanadaTaxRules().compute_gains([buy, *conv],
                                           detect_wash_sales=False)
        self.assertAlmostEqual(sum(t["gain"] for t in r["transactions"]),
                               150.0, places=2)


class TestQuoteCacheSpelling(unittest.TestCase):
    """#82: a cached quote serves only the spelling it was fetched for."""

    def _fill(self, cp, spelling="SYN111-USD"):
        PC.fetch_prices({"SYN": spelling}, cache_path=cp,
                        fetchers=[lambda pairs: {"SYN": (50, "yfinance",
                                                         "USD")}])

    def test_remapped_symbol_is_a_cache_miss(self):
        with tempfile.TemporaryDirectory() as td:
            cp = Path(td) / ".price_cache.json"
            self._fill(cp)
            self.assertEqual(json.loads(cp.read_text())["SYN"]
                             ["quote_symbol"], "SYN111-USD")
            err = io.StringIO()
            with redirect_stderr(err):
                q = PC.fetch_prices({"SYN": "SYN222-USD"}, cache_path=cp,
                                    fetchers=[])
            self.assertEqual(q, {})
            self.assertIn("SYN (cached for SYN111-USD, wanted SYN222-USD)",
                          err.getvalue())
            # Offline, the miss refuses rather than serve the old asset.
            with redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                PC.fetch_prices({"SYN": "SYN222-USD"}, cache_path=cp,
                                offline=True)

    def test_same_spelling_is_served(self):
        with tempfile.TemporaryDirectory() as td:
            cp = Path(td) / ".price_cache.json"
            self._fill(cp)
            q = PC.fetch_prices({"SYN": "SYN111-USD"}, cache_path=cp,
                                offline=True)
            self.assertEqual((q["SYN"].price, q["SYN"].currency,
                              q["SYN"].source), (50.0, "USD", "cache:0d"))

    def test_entry_without_a_recorded_spelling_is_a_miss(self):
        with tempfile.TemporaryDirectory() as td:
            cp = Path(td) / ".price_cache.json"
            cp.write_text(json.dumps({"SYN": {
                "price": 50.0, "asof": date.today().isoformat(),
                "source": "yfinance", "currency": "USD"}}))
            with redirect_stderr(io.StringIO()):
                q = PC.fetch_prices({"SYN": "SYN111-USD"}, cache_path=cp,
                                    fetchers=[])
            self.assertEqual(q, {})

    def test_option_cache_round_trip(self):
        occ = "SYN261120C00050000.US"
        with tempfile.TemporaryDirectory() as td:
            cp = Path(td) / ".price_cache.json"
            PC.fetch_option_prices([occ], cache_path=cp,
                                   fetchers=[lambda rem: {occ: (1.5,
                                                                "ibkr")}])
            q = PC.fetch_option_prices([occ], cache_path=cp, fetchers=[])
            self.assertEqual(q[occ].price, 1.5)


if __name__ == "__main__":
    unittest.main()
