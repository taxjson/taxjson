"""Re-audit-2 error paths (errors lists, crypto / FX caches, crypto-sends,
rates files). Synthetic data only; caches live under temporary paths and
nothing touches the network.

A2-0772 / A2-0773 / A2-1403 / A2-1446 / A2-0464: a damaged entry in
  ~/.crypto_price_cache.json (null, "abc", true, Infinity) or a cache
  that is not a JSON object is a cache miss, never a traceback or a
  price of 1.0 / inf.
A2-0474 / A2-0800 / A2-1446: a damaged block of
  ~/.currency_price_cache.json (_coverage / _boc / _boc_noon of the
  wrong shape) is dropped with a warning naming the cache.
A2-0465 / A2-0467 / A2-0775 / A2-1407: duplicate_lines reads a .tt with
  a BOM like convert-tt does.
A2-1405: a generated crypto_sends.tt re-saved with a BOM is still ours.
A2-1406: a wrong-shape transfer sidecar is one line naming the file.
A2-1404 / A2-1415 / A2-0776 / A2-1449: sends.json as a directory, a
  wrong-type note, a BOM.
A2-0790 / A2-1434 / A2-1411: an unreadable or BOM'd rates file.
A2-1437: convert-currency --rates naming a missing file.
A2-1423: fees-sum with a NaN rate.
A2-1424: fx-cash on a book a futures row refuses.

Formerly: test_fix_a2_errA_c
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import _hermetic  # noqa: F401  (the synthetic HOME before taxjson's caches read it: TJ-3857)
from taxjson.bin import fill_crypto_prices as F
from taxjson.lib import crypto_prices
from taxjson.lib import crypto_sends as cs
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestCryptoPriceCache(unittest.TestCase):
    """Formerly: test_fix_a2_errA_c.TestCryptoPriceCache"""
    BAD = ("null", '"abc"', "true", "Infinity", "[1]")

    def _cache(self, td, text):
        p = Path(td) / "cc.json"
        p.write_text(text)
        return p

    def test_cached_price_accepts_only_finite_positive_numbers(self):
        c = {"a": None, "b": "abc", "c": True, "d": float("inf"),
             "e": [1], "f": 0.0, "g": 12.5, "h": "7.5"}
        for k in "abcdef":
            self.assertIsNone(crypto_prices.cached_price(c, k), k)
        self.assertEqual(crypto_prices.cached_price(c, "g"), 12.5)
        self.assertEqual(crypto_prices.cached_price(c, "h"), 7.5)
        self.assertIsNone(crypto_prices.cached_price([], "g"))

    def test_non_object_cache_degrades_to_empty(self):
        with tempfile.TemporaryDirectory() as td:
            for text in ("[]", "5", '"x"', "﻿{}"):
                p = self._cache(td, text)
                with mock.patch.object(crypto_prices, "CACHE_FILE", str(p)):
                    self.assertEqual(crypto_prices.load_cache(), {}, text)

    def test_fill_crypto_treats_a_damaged_entry_as_a_miss(self):
        # A2-0773: null was a TypeError traceback; A2-1403: true booked a
        # price of 1.0 and Infinity a price of inf.
        tx = {"date": "2024-07-02", "time": "10:00:00", "symbol": "ETH",
              "action": "BUYSELL", "quantity": 0.5, "price": 0.0,
              "currency": "USD", "net_amount": 0.0,
              "description": "buy", "account": "c"}
        for bad in self.BAD:
            with tempfile.TemporaryDirectory() as td:
                p = self._cache(td, '{"ETH-2024-07-02": %s}' % bad)
                inp = Path(td) / "in.json"
                inp.write_text(json.dumps({"transactions": [tx]}))
                err, out = io.StringIO(), io.StringIO()
                with mock.patch.object(crypto_prices, "CACHE_FILE", str(p)), \
                        mock.patch.object(crypto_prices, "get_crypto_price",
                                          return_value=3000.0) as g, \
                        mock.patch.object(F.time, "sleep"), \
                        mock.patch.object(sys, "argv", ["fc", str(inp)]), \
                        redirect_stderr(err), redirect_stdout(out):
                    try:
                        F.main()
                    except SystemExit as e:
                        self.assertIn(e.code, (0, None), err.getvalue())
                self.assertEqual(g.call_count, 1, bad)
                self.assertIn("not a price", err.getvalue(), bad)
                row = json.loads(out.getvalue())["transactions"][0]
                self.assertEqual(row["price"], 3000.0, bad)
                self.assertEqual(json.loads(p.read_text())
                                 ["ETH-2024-07-02"], 3000.0, bad)

    def test_crypto_sends_lookup_treats_a_damaged_entry_as_a_miss(self):
        # A2-0464: null was a TypeError traceback in crypto-sends.
        with tempfile.TemporaryDirectory() as td:
            for text in [('{"SOL-2024-01-02": %s}' % b) for b in self.BAD] \
                    + ["5", "[]"]:
                p = self._cache(td, text)
                with mock.patch.object(crypto_prices, "CACHE_FILE", str(p)), \
                        mock.patch("taxjson.lib.offline.offline_enabled",
                                   return_value=True):
                    self.assertEqual(
                        cs.yahoo_usd_price(Path(td))("SOL", "2024-01-02"),
                        (None, "SOL"), text)
            p = self._cache(td, '{"SOL-2024-01-02": 150.0}')
            with mock.patch.object(crypto_prices, "CACHE_FILE", str(p)):
                self.assertEqual(
                    cs.yahoo_usd_price(Path(td))("SOL", "2024-01-02"),
                    (150.0, "SOL"))


if __name__ == "__main__":
    unittest.main()
