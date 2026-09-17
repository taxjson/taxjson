"""Regression tests for the silent-corruption fixes:
  - fill_crypto_prices no longer caches a failed (0.0) price fetch
  - Webull parser warns when it drops a real (non-blank) unhandled action
  - IB currency→suffix helper warns on an unmapped currency instead of
    silently producing a junk suffix
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


class TestCryptoCacheNotPoisoned(unittest.TestCase):
    def _run(self, fetch_return, tmp):
        import taxjson.bin.fill_crypto_prices as fc
        cache = Path(tmp) / "cache.json"
        inp = Path(tmp) / "in.json"
        inp.write_text(json.dumps({"transactions": [{
            "action": "BUYSELL", "date": "2026-01-02", "time": "12:00:00",
            "symbol": "FOO", "quantity": 1.0, "price": 0.0,
            "currency": "USD", "net_amount": 0.0}]}))
        saved = (fc.CACHE_FILE, fc.get_crypto_price, sys.argv)
        fc.CACHE_FILE = str(cache)
        fc.get_crypto_price = lambda s, d: fetch_return
        sys.argv = ["fill-crypto", str(inp)]
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                fc.main()
            out = json.loads(buf.getvalue())
        finally:
            fc.CACHE_FILE, fc.get_crypto_price, sys.argv = saved
        return out, cache

    def test_failed_fetch_not_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, cache = self._run(0.0, tmp)
            self.assertFalse(cache.exists(),
                             "a 0.0 (failed) fetch must not be cached")
            self.assertEqual(out["transactions"][0]["price"], 0.0)

    def test_successful_fetch_is_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, cache = self._run(5.0, tmp)
            self.assertTrue(cache.exists())
            self.assertIn("FOO-2026-01-02", json.loads(cache.read_text()))
            self.assertEqual(out["transactions"][0]["price"], 5.0)


class TestWebullSkipWarning(unittest.TestCase):
    HEADER = "Currency,Date,Action Code,Symbol,Name,X,Quantity,Price,Y,Proceeds"

    def _parse(self, rows, tmp):
        from taxjson.lib.brokerages.webull import WebullBrokerage
        p = Path(tmp) / "wb.csv"
        p.write_text("\n".join([self.HEADER] + rows) + "\n")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            txs = WebullBrokerage().parse_file(p)
        return txs, buf.getvalue()

    def test_real_dropped_action_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            txs, err = self._parse([
                "USD,15-01-2025,BUY,AAPL,APPLE,,10,150,,1500",
                "USD,20-01-2025,DIVIDEND,AAPL,APPLE DIV,,0,0,,5",
            ], tmp)
        self.assertEqual(len(txs), 1)            # only the BUY booked
        self.assertIn("DIVIDEND", err)
        self.assertIn("warning", err.lower())

    def test_blank_continuation_rows_are_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            txs, err = self._parse([
                "USD,15-01-2025,BUY,AAPL,APPLE,,10,150,,1500",
                "USD,,,,,,,,,",            # blank continuation row
            ], tmp)
        self.assertEqual(len(txs), 1)
        self.assertNotIn("warning", err.lower())


class TestIbCurrencyExt(unittest.TestCase):
    def test_known_currency_maps(self):
        from taxjson.lib.brokerages.ib_extractor import _ib_currency_ext
        self.assertEqual(_ib_currency_ext("USD"), "US")
        self.assertEqual(_ib_currency_ext("CAD"), "TO")
        self.assertEqual(_ib_currency_ext("AUD"), "AX")
        self.assertEqual(_ib_currency_ext("GBP"), "L")

    def test_unknown_currency_warns_and_echoes(self):
        import taxjson.bin  # noqa
        from taxjson.lib.brokerages import ib_extractor as ib
        ib._IB_WARNED_CURRENCIES.discard("EUR")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ext = ib._ib_currency_ext("EUR")
        self.assertEqual(ext, "EUR")
        self.assertIn("warning", buf.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
