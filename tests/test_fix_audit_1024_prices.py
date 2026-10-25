"""Audit 2026-10-24, group "prices" (lib/price_chain.py,
bin/fill_crypto_prices.py): findings A31, A32, A33, A40, B30, B32.

Every fixture is synthetic (made-up tickers, prices, dates) and the
providers are replaced by test tiers or patched functions: no network.
"""
import io
import json
import tempfile
import threading
import unittest
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

from tax_rules import rule

from taxjson.bin import fill_crypto_prices as FC
from taxjson.lib import price_chain as PC
from taxjson.lib.core import TaxTransaction
from _style import CapturedWidth

# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _fetch(pairs, cache_path, fetchers, **kw):
    err = io.StringIO()
    with redirect_stderr(err):
        quotes = PC.fetch_prices(pairs, cache_path=cache_path,
                                 fetchers=fetchers, **kw)
    return quotes, err.getvalue()


class TestPriceChainCache(unittest.TestCase):

    def test_a31_cached_minor_unit_quote_without_a_unit_is_a_miss(self):
        """A31: Live price cache accepts a minor-unit listing without a
        provider currency. A cached 125 for an LSE line with no
        provider-named unit could be pence or pounds: a miss (said),
        as the live tiers already treat such a quote."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "quotes.json"
            today = date.today().isoformat()
            p.write_text(json.dumps({"ZZQ.L": {
                "price": 125, "asof": today, "quote_symbol": "ZZQ.L"}}))
            quotes, err = _fetch({"ZZQ.L": "ZZQ.L"}, p, [])
            self.assertEqual(quotes, {})
            self.assertIn("ZZQ.L did not say its unit", err)
            # The same entry with the provider's unit recorded is served.
            p.write_text(json.dumps({"ZZQ.L": {
                "price": 1.25, "asof": today, "quote_symbol": "ZZQ.L",
                "currency": "GBP"}}))
            quotes, err = _fetch({"ZZQ.L": "ZZQ.L"}, p, [])
            self.assertEqual((quotes["ZZQ.L"].price, quotes["ZZQ.L"].currency,
                              quotes["ZZQ.L"].source), (1.25, "GBP", "cache:0d"))
            self.assertNotIn("did not say its unit", err)

    def test_a32_close_cache_entry_dated_outside_the_window_is_a_miss(self):
        """A32: Historical close cache serves a future price without
        validating its effective day. A December close filed under a
        January key is refused: offline that is a miss (the caller's
        override is named), online the live tier is asked and rewrites
        the entry."""
        key = "ZZQ.US|ZZQ@2026-01-01"
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "close.json"
            p.write_text(json.dumps({key: {
                "price": 100, "day": "2026-12-31", "currency": "USD"}}))
            err = io.StringIO()
            with redirect_stderr(err), \
                    self.assertRaises(PC.OfflineCloseMissing):
                PC.close_on("ZZQ.US", "ZZQ", "2026-01-01", cache_path=p,
                            offline=True)
            self.assertIn("is dated 2026-12-31, outside 2025-12-25..2026-01-01"
                          " — not used", err.getvalue())
            # Online: the tier's close replaces the bad entry.
            with redirect_stderr(io.StringIO()):
                c = PC.close_on("ZZQ.US", "ZZQ", "2026-01-01", cache_path=p,
                                fetchers=[lambda pairs, s, e: {
                                    "ZZQ.US": {"2025-12-31": 40.0}}])
            self.assertEqual((c.price, c.day, c.source),
                             (40.0, "2025-12-31", "yfinance"))
            self.assertEqual(json.loads(p.read_text())[key]["day"],
                             "2025-12-31")
            # An entry dated within the lookback window is still served.
            with redirect_stderr(io.StringIO()):
                c = PC.close_on("ZZQ.US", "ZZQ", "2026-01-01", cache_path=p,
                                offline=True)
            self.assertEqual((c.price, c.day, c.source),
                             (40.0, "2025-12-31", "cache"))
            # One dated before the window (a close the live tier could
            # not have returned for this key) is a miss too.
            p.write_text(json.dumps({key: {
                "price": 100, "day": "2025-12-01", "currency": "USD"}}))
            with redirect_stderr(io.StringIO()), \
                    self.assertRaises(PC.OfflineCloseMissing):
                PC.close_on("ZZQ.US", "ZZQ", "2026-01-01", cache_path=p,
                            offline=True)

    def test_a33_concurrent_fetches_keep_each_others_fresh_prices(self):
        """A33: Concurrent price requests overwrite each other's distinct
        cached symbols. Two runs load the (empty) cache together, each
        fetches its own symbol: both entries are on disk afterwards
        (the fresh entries are merged into the file under its lock,
        never the stale dict a run loaded)."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "quotes.json"
            barrier = threading.Barrier(2)
            original = PC._load_cache

            def read_together(path):
                cache = original(path)
                barrier.wait(timeout=5)
                return cache

            def quote(sym):
                price = {"ZZQ.US": 10, "ZZR.US": 20}[sym]
                with redirect_stderr(io.StringIO()):
                    return PC.fetch_prices(
                        {sym: sym}, cache_path=p,
                        fetchers=[lambda rem: {sym: (price, "synthetic",
                                                     "USD")}])
            with patch.object(PC, "_load_cache", side_effect=read_together):
                with ThreadPoolExecutor(2) as pool:
                    list(pool.map(quote, ["ZZQ.US", "ZZR.US"]))
            saved = json.loads(p.read_text())
            self.assertEqual(sorted(saved), ["ZZQ.US", "ZZR.US"])
            self.assertEqual((saved["ZZQ.US"]["price"],
                              saved["ZZR.US"]["price"]), (10.0, 20.0))
            # A fresh quote still replaces the entry on disk (the stale
            # one does not come back).
            with redirect_stderr(io.StringIO()):
                PC.fetch_prices({"ZZQ.US": "ZZQ.US"}, cache_path=p,
                                fetchers=[lambda rem: {
                                    "ZZQ.US": (12, "synthetic", "USD")}])
            saved = json.loads(p.read_text())
            self.assertEqual((saved["ZZQ.US"]["price"],
                              saved["ZZR.US"]["price"]), (12.0, 20.0))

    def test_a40_tier_hit_with_a_currency_problem_is_rejected(self):
        """A40: Live-price resolver accepts a quote whose explicit
        currency is invalid. A tier that names a unit taxjson cannot
        read ('USd': a minor unit of unknown size) is not used: the
        next tier is asked, and nothing of the bad hit is cached."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "q.json"
            tiers = [lambda rem: {"ZZQ.US": (1000, "first", "USd")},
                     lambda rem: {"ZZQ.US": (11, "second", "USD")}]
            quotes, err = _fetch({"ZZQ.US": "ZZQ"}, p, tiers)
            q = quotes["ZZQ.US"]
            self.assertEqual((q.price, q.source, q.currency),
                             (11.0, "second", "USD"))
            self.assertIn("first quote for ZZQ.US: currency 'USd' looks like "
                          "a minor unit (pence/cents) taxjson does not know "
                          "— not used; the next source is asked", err)
            self.assertEqual(json.loads(p.read_text())["ZZQ.US"]["price"],
                             11.0)
            # The only tier: no quote at all, nothing cached.
            p2 = Path(td) / "q2.json"
            quotes, err = _fetch({"ZZQ.US": "ZZQ"}, p2,
                                 [lambda rem: {"ZZQ.US": (1000, "first",
                                                          ["USD"])}])
            self.assertEqual(quotes, {})
            self.assertIn("is not a code — not used", err)
            self.assertFalse(p2.exists())
            # A cache entry carrying such a unit is a miss too.
            p2.write_text(json.dumps({"ZZQ.US": {
                "price": 1000, "asof": date.today().isoformat(),
                "quote_symbol": "ZZQ", "currency": "USd"}}))
            quotes, err = _fetch({"ZZQ.US": "ZZQ"}, p2, [])
            self.assertEqual(quotes, {})
            self.assertIn("price cache entry for ZZQ.US: currency 'USd' "
                          "looks like a minor unit", err)


def _tx(day, qty, sym, **kw):
    return TaxTransaction(**dict(
        date=day, date_settle=day, time="10:00:00", account="wallet",
        symbol=sym, action="BUYSELL", quantity=qty, net_amount=0,
        currency="USD", **kw))


def _fill(rows, cache, price=8.0):
    """fill-crypto's _fill on `rows` with a stub Yahoo (every lookup
    answers `price`), a given in-memory cache and no sleeping; returns
    (output rows, lookups made, the cache as saved, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    saved = {}
    with patch.object(FC, "load_transactions", return_value=rows), \
            patch.object(FC, "load_cache", return_value=cache), \
            patch.object(FC, "get_crypto_price", return_value=price) as get, \
            patch.object(FC, "save_cache", side_effect=saved.update), \
            patch.object(FC.time, "sleep"), \
            redirect_stdout(out), redirect_stderr(err):
        FC._fill(Namespace(input="synthetic"))
    return (json.loads(out.getvalue())["transactions"], get.call_count,
            saved, err.getvalue())


class TestFillCryptoPrices(unittest.TestCase):

    def test_b30_zero_cached_crypto_price_is_looked_up_again(self):
        """B30: A zero cached crypto price permanently prevents retrying
        the quote. A cached 0 (or a negative) is no price: the entry is
        dropped with a warning, the coin looked up again and the fresh
        price applied and cached."""
        for bad in (0, -3, "0"):
            rows, lookups, saved, err = _fill(
                [_tx("2026-01-02", 1, "ZZCOIN")],
                {"ZZCOIN-2026-01-02": bad})
            self.assertEqual(lookups, 1, bad)
            self.assertEqual((rows[0]["price"], rows[0]["net_amount"]),
                             (8.0, 8.0), bad)
            self.assertIn(f"entry ZZCOIN-2026-01-02 is {bad!r}, not a price "
                          f"— ignored and looked up again", err)
            self.assertNotIn("UNPRICED", err)
            self.assertEqual(saved.get("ZZCOIN-2026-01-02"), 8.0, bad)
        # A usable cached price is still served without a lookup.
        rows, lookups, _saved, err = _fill(
            [_tx("2026-01-02", 1, "ZZCOIN")], {"ZZCOIN-2026-01-02": 5.5})
        self.assertEqual((lookups, rows[0]["price"]), (0, 5.5))
        self.assertNotIn("looked up again", err)

    @rule("CA-CRYPTO-02")
    @rule("US-CRYPTO-02")
    def test_b32_swap_against_a_stablecoin_takes_its_par_value_on_both_legs(
            self):
        """B32: Unpriced crypto swaps into a stablecoin book unequal
        proceeds and acquisition cost. One coin spent for 10 USDC is
        10 USD of consideration on both legs (the stablecoin's par,
        exact by definition), not Yahoo's close of the coin on one leg
        and 10 on the other; the same when the stablecoin is spent."""
        rows, _n, _saved, err = _fill(
            [_tx("2026-01-02", -1, "ZZCOIN", id="swap-sell"),
             _tx("2026-01-02", 10, "USDC", id="swap-buy")], {})
        by = {r["symbol"]: r for r in rows}
        self.assertEqual([by["ZZCOIN"]["net_amount"], by["USDC"]["net_amount"]],
                         [10, 10])
        self.assertEqual((by["ZZCOIN"]["price"], by["USDC"]["price"]),
                         (10.0, 1.0))
        self.assertIn("1 crypto-to-crypto swap(s) valued ONCE", err)
        self.assertNotIn("UNPRICED", err)
        # The stablecoin spent: the coin bought costs the dollars paid.
        rows, _n, _saved, err = _fill(
            [_tx("2026-01-02", -10, "USDC", id="swap2-sell"),
             _tx("2026-01-02", 1, "ZZCOIN", id="swap2-buy")], {})
        by = {r["symbol"]: r for r in rows}
        self.assertEqual([by["ZZCOIN"]["net_amount"], by["USDC"]["net_amount"]],
                         [10, 10])
        self.assertEqual(by["ZZCOIN"]["price"], 10.0)
        # A coin-for-coin swap keeps the received coin's value.
        rows, _n, _saved, err = _fill(
            [_tx("2026-01-02", -1, "ZZCOIN", id="swap3-sell"),
             _tx("2026-01-02", 4, "ZZALT", id="swap3-buy")], {})
        self.assertEqual([r["net_amount"] for r in rows], [32.0, 32.0])


if __name__ == "__main__":
    unittest.main()
