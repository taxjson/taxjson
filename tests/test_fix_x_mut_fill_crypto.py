"""Mutation pins for bin/fill_crypto_prices.py (audit G1-0).

Each test kills one or more mutants that survived the whole suite in
the 2026-09-30 mutation round (scripts/mutation_audit.py operators,
run per module): a behaviour the price filler gets right but nothing
asserted. The network is never touched — get_crypto_price is replaced,
or urllib.request.urlopen is mocked for the fetch itself.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import taxjson.bin.fill_crypto_prices as fc
from tax_rules import rule


def _row(**kw):
    r = {"action": "BUYSELL", "date": "2025-03-03", "symbol": "ZZQ",
         "quantity": 1.0, "price": 0.0, "net_amount": 0.0,
         "currency": "USD"}
    r.update(kw)
    return r


class _Run:
    """Run fill-crypto's main() on `rows` with a stub price source, a
    private cache file and no sleeping; restore every module global."""

    def __call__(self, tmp, rows, fetch, *, today="2026-09-30",
                 argv_extra=(), raw_text=None):
        cache = Path(tmp) / "cache.json"
        inp = Path(tmp) / "in.json"
        inp.write_text(raw_text if raw_text is not None
                       else json.dumps({"transactions": rows}),
                       encoding="utf-8")
        calls = []

        def _fetch(sym, day):
            calls.append((sym, day))
            return fetch(sym, day)
        saved = (fc.CACHE_FILE, fc.get_crypto_price, sys.argv,
                 dict(fc.SYMBOL_OVERRIDES), fc.time.sleep, fc._utc_today)
        fc.CACHE_FILE = str(cache)
        fc.get_crypto_price = _fetch
        fc.time.sleep = lambda s: None
        fc._utc_today = lambda: today
        sys.argv = ["fill-crypto", str(inp), "--project-root", str(tmp),
                    *argv_extra]
        out, err = io.StringIO(), io.StringIO()
        code = 0
        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                try:
                    fc.main()
                except SystemExit as e:
                    code = e.code
        finally:
            (fc.CACHE_FILE, fc.get_crypto_price, sys.argv, overrides,
             fc.time.sleep, fc._utc_today) = saved
            fc.SYMBOL_OVERRIDES.clear()
            fc.SYMBOL_OVERRIDES.update(overrides)
        txs = (json.loads(out.getvalue())["transactions"]
               if out.getvalue().strip() else None)
        cached = (json.loads(cache.read_text()) if cache.exists() else {})
        return txs, cached, err.getvalue(), calls, code


run = _Run()


class TestCheapCoinPrices(unittest.TestCase):
    """A coin worth less than $1 (DOGE, SHIB ...) is a real price: the
    `> 0` tests must not be `> 1` (m115, m131, m138, m139)."""

    @rule("CA-CRYPTO-01")
    def test_sub_dollar_historical_close_is_applied_and_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            txs, cached, err, _c, _ = run(
                tmp, [_row(symbol="DOGE", quantity=1000.0)],
                lambda s, d: 0.25)
            self.assertEqual(txs[0]["price"], 0.25)
            self.assertEqual(txs[0]["net_amount"], 250.0)
            self.assertEqual(cached, {"DOGE-2025-03-03": 0.25})
            self.assertNotIn("UNPRICED", err)

    def test_sub_dollar_close_for_today_is_provisional_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            txs, cached, err, _c, _ = run(
                tmp, [_row(symbol="DOGE", date="2026-09-30",
                           quantity=10.0)],
                lambda s, d: 0.5, today="2026-09-30")
            self.assertEqual(txs[0]["price"], 0.5)
            self.assertEqual(cached, {})
            self.assertIn("today's candle is still open", err)

    def test_failed_fetch_for_today_is_not_called_provisional(self):
        # p == 0 (the failure sentinel) is not an intraday price (m127).
        with tempfile.TemporaryDirectory() as tmp:
            txs, cached, err, _c, _ = run(
                tmp, [_row(date="2026-09-30")], lambda s, d: 0.0,
                today="2026-09-30")
            self.assertEqual(txs[0]["price"], 0.0)
            self.assertNotIn("today's candle", err)
            self.assertIn("1 row(s) left UNPRICED", err)

    def test_tiny_broker_price_is_kept_not_refetched(self):
        # A meme coin quoted at 5e-8 USD is priced: the "no price"
        # epsilon is 1e-8, not 1e-7 (m46/m48 swap filter, m104 fill).
        with tempfile.TemporaryDirectory() as tmp:
            rows = [_row(id="T1-buy", symbol="BABY", quantity=1e9,
                         price=5e-8, net_amount=50.0),
                    _row(id="T1-sell", symbol="ZZQ", quantity=-1.0,
                         price=0.0, net_amount=0.0)]
            txs, _cache, err, calls, _ = run(tmp, rows, lambda s, d: 0.0)
            self.assertEqual(calls, [("ZZQ", "2025-03-03")])
            self.assertEqual(txs[0]["price"], 5e-8)
            self.assertEqual(txs[0]["net_amount"], 50.0)
            # The ZZQ leg is NOT a swap pair (its partner is priced),
            # so it stays unpriced (no swap valuation).
            self.assertEqual(txs[1]["net_amount"], 0.0)
            self.assertIn("1 row(s) left UNPRICED", err)

    def test_tiny_broker_total_is_kept(self):
        # A total of 5e-8 is the broker's own figure (m96/m98/m106).
        with tempfile.TemporaryDirectory() as tmp:
            txs, _c, _e, calls, _ = run(
                tmp, [_row(symbol="BABY", quantity=1.0,
                           net_amount=5e-8)], lambda s, d: 9.0)
            self.assertEqual(calls, [])
            self.assertEqual(txs[0]["net_amount"], 5e-8)
            self.assertEqual(txs[0]["price"], 5e-8)


    def test_row_with_a_tiny_broker_price_and_no_total_is_not_refetched(self):
        # A row the broker priced (5e-8 per coin) keeps its price even
        # with no total, and it is not an unpriced swap leg either (m104
        # fill epsilon, m46 swap-filter epsilon).
        with tempfile.TemporaryDirectory() as tmp:
            rows = [_row(id="K3-buy", symbol="BABY", quantity=0.1,
                         price=5e-8, net_amount=0.0),
                    _row(id="K3-sell", symbol="BBB", quantity=-2.0)]
            txs, _c, err, calls, _ = run(tmp, rows, lambda s, d: 7.0)
            self.assertEqual(calls, [("BBB", "2025-03-03")])
            by = {t["symbol"]: t for t in txs}
            self.assertEqual((by["BABY"]["price"], by["BABY"]["net_amount"]),
                             (5e-8, 0.0))
            self.assertEqual(by["BBB"]["net_amount"], 14.0)
            self.assertNotIn("swap(s) valued", err)


class TestBrokerTotals(unittest.TestCase):
    def test_sell_with_blank_price_derives_a_positive_price(self):
        # qty -2, total 100 -> 50 per coin: abs() on quantity (m122,
        # m141) and the 8-decimal rounding (m136).
        with tempfile.TemporaryDirectory() as tmp:
            txs, _c, _e, calls, _ = run(
                tmp, [_row(quantity=-3.0, net_amount=100.0)],
                lambda s, d: 9.0)
            self.assertEqual(calls, [])
            self.assertEqual(txs[0]["price"], 33.33333333)
            self.assertEqual(txs[0]["net_amount"], 100.0)

    def test_negative_derived_total_is_kept_not_refetched(self):
        # Coinbase signs a DERIVED total the engine's way (a sale whose
        # fee exceeds its value is negative): still the broker's figure
        # (m105), and the derived price is a magnitude (m140).
        with tempfile.TemporaryDirectory() as tmp:
            txs, _c, _e, calls, _ = run(
                tmp, [_row(quantity=-2.0, net_amount=-0.5)],
                lambda s, d: 9.0)
            self.assertEqual(calls, [])
            self.assertEqual(txs[0]["net_amount"], -0.5)
            self.assertEqual(txs[0]["price"], 0.25)


class TestStablecoinsAtPar(unittest.TestCase):
    @rule("CA-INC-04")
    def test_stablecoin_sell_is_valued_at_par_as_a_magnitude(self):
        # A USDC row with no price is worth its quantity; the net is a
        # magnitude even on a negative-quantity row (m72).
        with tempfile.TemporaryDirectory() as tmp:
            txs, cached, _e, calls, _ = run(
                tmp, [_row(symbol="USDC", quantity=-50.0)],
                lambda s, d: 9.0)
            self.assertEqual(calls, [])
            self.assertEqual((txs[0]["price"], txs[0]["net_amount"]),
                             (1.0, 50.0))
            self.assertEqual(cached, {})

    def test_stablecoin_row_with_a_negative_derived_total_keeps_it(self):
        # A USDC sale whose fee exceeded its value carries a negative
        # DERIVED total: the broker's figure, not re-valued at par (m97).
        with tempfile.TemporaryDirectory() as tmp:
            txs, _c, _e, _calls, _ = run(
                tmp, [_row(symbol="USDC", quantity=-0.4,
                           net_amount=-0.6)], lambda s, d: 9.0)
            self.assertEqual(txs[0]["net_amount"], -0.6)


class TestSwapValuedOnce(unittest.TestCase):
    """CA-CRYPTO-01 / US-CRYPTO-01: a coin-for-coin trade is a sale of
    one and a purchase of the other at ONE fair value."""

    def _pair(self, **recv):
        r = _row(id="K1-buy", symbol="AAA", quantity=3.0)
        r.update(recv)
        return [r, _row(id="K1-sell", symbol="BBB", quantity=-2.0)]

    @rule("CA-CRYPTO-01")
    @rule("US-CRYPTO-01")
    def test_both_legs_take_the_received_value(self):
        prices = {"AAA": 100.0, "BBB": 7.0}
        with tempfile.TemporaryDirectory() as tmp:
            txs, _c, err, _calls, _ = run(tmp, self._pair(),
                                          lambda s, d: prices[s])
            by = {t["symbol"]: t for t in txs}
            self.assertEqual(by["AAA"]["net_amount"], 300.0)
            self.assertEqual(by["BBB"]["net_amount"], 300.0)
            self.assertEqual(by["BBB"]["price"], 150.0)
            # Exactly one swap counted (m5, m21), none left unpriced.
            self.assertIn("1 crypto-to-crypto swap(s) valued ONCE", err)
            self.assertNotIn("UNPRICED", err)

    @rule("CA-CRYPTO-01")
    @rule("US-CRYPTO-01")
    def test_received_unpriced_falls_back_to_the_spent_value(self):
        # The received coin has no close: the spent coin's value (a
        # tiny one, 0.5 USD) values both legs (m62) and both leave the
        # unpriced list (m65, m93, m94); 8-decimal prices (m64).
        prices = {"AAA": 0.0, "BBB": 0.25}
        with tempfile.TemporaryDirectory() as tmp:
            txs, _c, err, _calls, _ = run(tmp, self._pair(),
                                          lambda s, d: prices[s])
            by = {t["symbol"]: t for t in txs}
            self.assertEqual(by["AAA"]["net_amount"], 0.5)
            self.assertEqual(by["AAA"]["price"], 0.16666667)
            self.assertEqual(by["BBB"]["net_amount"], 0.5)
            self.assertNotIn("UNPRICED", err)

    @rule("CA-CRYPTO-01")
    @rule("US-CRYPTO-01")
    def test_spent_unpriced_takes_the_received_value(self):
        # The SPENT coin has no close: valued from the received leg and
        # removed from the unpriced list (m94).
        prices = {"AAA": 100.0, "BBB": 0.0}
        with tempfile.TemporaryDirectory() as tmp:
            txs, _c, err, _calls, _ = run(tmp, self._pair(),
                                          lambda s, d: prices[s])
            by = {t["symbol"]: t for t in txs}
            self.assertEqual(by["BBB"]["net_amount"], 300.0)
            self.assertNotIn("UNPRICED", err)

    @rule("CA-CRYPTO-01")
    @rule("US-CRYPTO-01")
    def test_neither_leg_priced_stays_unpriced(self):
        # No value at all: nothing invented (m61 "else 1.0", m36), both
        # legs reported unpriced (m20 path).
        with tempfile.TemporaryDirectory() as tmp:
            txs, _c, err, _calls, _ = run(tmp, self._pair(),
                                          lambda s, d: 0.0)
            self.assertEqual([t["net_amount"] for t in txs], [0.0, 0.0])
            self.assertIn("2 row(s) left UNPRICED", err)
            self.assertNotIn("swap(s) valued", err)

    @rule("CA-CRYPTO-01")
    @rule("US-CRYPTO-01")
    def test_received_leg_with_a_negative_broker_total_values_the_swap(self):
        # Only UNPRICED legs pair; a leg carrying a total of either sign
        # is priced (m45, m47) — and so is not a swap leg at all.
        with tempfile.TemporaryDirectory() as tmp:
            rows = self._pair(net_amount=-40.0)
            txs, _c, err, calls, _ = run(tmp, rows, lambda s, d: 7.0)
            self.assertEqual(calls, [("BBB", "2025-03-03")])
            by = {t["symbol"]: t for t in txs}
            self.assertEqual(by["AAA"]["net_amount"], -40.0)
            self.assertEqual(by["BBB"]["net_amount"], 14.0)
            self.assertNotIn("swap(s) valued", err)
        with tempfile.TemporaryDirectory() as tmp:
            rows = self._pair(price=-3.0)
            txs, _c, err, calls, _ = run(tmp, rows, lambda s, d: 7.0)
            self.assertEqual(calls, [("BBB", "2025-03-03")])
            self.assertNotIn("swap(s) valued", err)

    @rule("CA-CRYPTO-01")
    @rule("US-CRYPTO-01")
    def test_same_direction_legs_are_not_a_swap(self):
        # Two buys under one id stem are not an exchange (m17).
        with tempfile.TemporaryDirectory() as tmp:
            rows = [_row(id="K2-buy", symbol="AAA", quantity=3.0),
                    _row(id="K2-sell", symbol="BBB", quantity=2.0)]
            prices = {"AAA": 100.0, "BBB": 7.0}
            txs, _c, err, _calls, _ = run(tmp, rows,
                                          lambda s, d: prices[s])
            by = {t["symbol"]: t for t in txs}
            self.assertEqual(by["AAA"]["net_amount"], 300.0)
            self.assertEqual(by["BBB"]["net_amount"], 14.0)
            self.assertNotIn("swap(s) valued", err)


class TestUnpricedWarning(unittest.TestCase):
    def _rows(self, n):
        return [_row(symbol=f"Z{i:02d}") for i in range(n)]

    def test_ten_rows_listed_without_a_more_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            _t, _c, err, _calls, _ = run(tmp, self._rows(10),
                                         lambda s, d: 0.0)
            self.assertIn("10 row(s) left UNPRICED", err)
            self.assertNotIn("more)", err)

    def test_eleven_rows_list_ten_and_count_the_rest(self):
        # (+1 more) — m117 (len-10 -> len+10), m133/m82 (10 -> 11),
        # m40 (> -> >=).
        with tempfile.TemporaryDirectory() as tmp:
            _t, _c, err, _calls, _ = run(tmp, self._rows(11),
                                         lambda s, d: 0.0)
            self.assertIn("11 row(s) left UNPRICED", err)
            self.assertIn("BUYSELL Z09 2025-03-03 (+1 more)", err)
            self.assertNotIn("Z10 2025", err)


class TestInputAndMapErrors(unittest.TestCase):
    def test_malformed_input_row_is_a_clean_error(self):
        # The loader's ValueError is reported, exit 1 — not a traceback
        # (m6 except-body -> raise, m66 exit code).
        with tempfile.TemporaryDirectory() as tmp:
            txs, _c, err, _calls, code = run(
                tmp, None, lambda s, d: 1.0,
                raw_text=json.dumps({"transactions": [
                    _row(quantity="not-a-number")]}))
            self.assertIsNone(txs)
            self.assertEqual(code, 1)
            self.assertIn("taxjson-fill-crypto", err)
            self.assertNotIn("Traceback", err)

    def test_map_blank_and_comment_lines_are_silent(self):
        # m86: a blank line is skipped, not reported as malformed.
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "ticker.map").write_text(
                "# my coins\n\nCRYPTO ZZQ ZZQ999\n   # indented comment\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                m = fc.load_symbol_overrides([tmp])
            self.assertEqual(m["ZZQ"], "ZZQ999")
            self.assertEqual(err.getvalue(), "")

    def test_malformed_map_line_is_skipped_with_its_line_number(self):
        # m84 (line numbers start at 1), m88 (a skipped line maps
        # nothing).
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "ticker.map").write_text(
                "CRYPTO AAA AAA1 extra\nCRYPTO BBB\nCRYPTO CCC CCC1\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                m = fc.load_symbol_overrides([tmp])
            self.assertNotIn("AAA", m)
            self.assertNotIn("BBB", m)
            self.assertEqual(m["CCC"], "CCC1")
            self.assertIn("ticker.map:1: CRYPTO needs", err.getvalue())
            self.assertIn("ticker.map:2: CRYPTO needs", err.getvalue())

    @unittest.skipIf(os.name != "posix" or os.geteuid() == 0,
                     "needs POSIX permissions and a non-root user")
    def test_unreadable_map_warns(self):
        # m10: the coin spellings are ticker.map CRYPTO lines now; a
        # ticker.map that cannot be read is an error naming it (the
        # project's run refuses it too) — never a silent fallback to the
        # built-ins.
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, "ticker.map")
            p.write_text("CRYPTO ZZQ ZZQ1\n")
            p.chmod(0)
            try:
                with self.assertRaises(OSError) as cm:
                    fc.load_symbol_overrides([tmp])
            finally:
                p.chmod(0o600)
            self.assertIn("ticker.map", str(cm.exception))

    @unittest.skipIf(os.name != "posix" or os.geteuid() == 0,
                     "needs POSIX permissions and a non-root user")
    def test_unwritable_cache_warns(self):
        # m2: a cache that cannot be written is a warning.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp, "ro")
            d.mkdir()
            d.chmod(0o500)
            saved = fc.CACHE_FILE
            fc.CACHE_FILE = str(d / "cache.json")
            try:
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    fc.save_cache({"X-2025-01-01": 1.0})
            finally:
                fc.CACHE_FILE = saved
                d.chmod(0o700)
            self.assertIn("could not write", err.getvalue())


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    # a real urlopen response is a context manager (fill-crypto closes it)
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestFetch(unittest.TestCase):
    def _fetch(self, closes):
        req = {}

        def urlopen(r, timeout=None):
            req["url"] = r.full_url
            return _Resp({"chart": {"result": [{"indicators": {
                "quote": [{"close": closes}]}}]}})
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "0"}), \
                mock.patch("urllib.request.urlopen", urlopen), \
                contextlib.redirect_stderr(err):
            p = fc.get_crypto_price("DOGE", "2025-03-03")
        return p, req["url"], err.getvalue()

    def test_one_utc_day_window(self):
        # period2 = period1 + one day (m42 +->-, m89 86400->86401).
        _p, url, _e = self._fetch([0.3])
        self.assertIn("DOGE-USD?period1=1740960000&period2=1741046400&",
                      url)

    def test_sub_dollar_close_is_a_price(self):
        # m119: `v > 0`, not `v > 1`.
        p, _u, err = self._fetch([None, 0.3])
        self.assertEqual(p, 0.3)
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()
