"""Medium-round regression tests (area: misc).

Pins the tax tables, AMT arithmetic and other amounts the mutation audit
found unpinned (G1-*), plus the misc fixes. All data is synthetic.

Formerly: test_fix_misc"""
import unittest


class TestFillCryptoAmountsPinned(unittest.TestCase):
    """G1-6: fill-crypto's valuation, row by row, from a seeded cache.

    Formerly: test_fix_misc.TestFillCryptoAmountsPinned"""

    def test_price_times_quantity_and_stablecoins(self):
        import contextlib
        import io
        import json
        import sys
        import tempfile
        from pathlib import Path
        import taxjson.bin.fill_crypto_prices as fc
        from taxjson.lib import crypto_prices
        rows = [
            # 0: staking reward, no price: FMV x qty, relabelled USD.
            {"action": "DIVIDEND", "date": "2026-03-02", "symbol": "SOL",
             "quantity": 1.5, "price": 0.0, "net_amount": 0.0, "currency": "CAD"},
            # 1: a sell with no price: FMV x |qty|.
            {"action": "BUYSELL", "date": "2026-03-02", "symbol": "SOL",
             "quantity": -2.0, "price": 0.0, "net_amount": 0.0, "currency": "USD"},
            # 2: stablecoin reward: 1.0 a unit.
            {"action": "DIVIDEND", "date": "2026-03-02", "symbol": "USDC",
             "quantity": 5.0, "price": 0.0, "net_amount": 0.0, "currency": "USD"},
            # 3: a reward already folded to USD: 1.0 a unit.
            {"action": "DIVIDEND", "date": "2026-03-02", "symbol": "USD",
             "quantity": 3.0, "price": 0.0, "net_amount": 0.0, "currency": "USD"},
            # 4: broker total, no price: the price is derived, total kept.
            {"action": "BUYSELL", "date": "2026-03-02", "symbol": "ETH",
             "quantity": 3.0, "price": 0.0, "net_amount": 150.0, "currency": "CAD"},
            # 5: a USD cash BUYSELL is not a reward: untouched.
            {"action": "BUYSELL", "date": "2026-03-02", "symbol": "USD",
             "quantity": 4.0, "price": 0.0, "net_amount": 0.0, "currency": "USD"},
            # 6: the lookup fails: left at 0 in its own currency, reported.
            {"action": "DIVIDEND", "date": "2026-03-02", "symbol": "ADA",
             "quantity": 2.0, "price": 0.0, "net_amount": 0.0, "currency": "CAD"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "in.json"
            inp.write_text(json.dumps({"transactions": rows}))
            cache = Path(tmp) / "cache.json"
            cache.write_text(json.dumps({"SOL-2026-03-02": 140.25}))

            def no_fetch(sym, date):
                if sym == "ADA":
                    return 0.0              # a failed lookup
                raise AssertionError(f"unexpected price fetch {sym} {date}")
            saved = (crypto_prices.CACHE_FILE, crypto_prices.get_crypto_price, sys.argv, fc.time.sleep)
            crypto_prices.CACHE_FILE = str(cache)
            crypto_prices.get_crypto_price = no_fetch
            fc.time.sleep = lambda s: None
            sys.argv = ["fill-crypto", str(inp), "--project-root", tmp]
            out, err = io.StringIO(), io.StringIO()
            try:
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(err):
                    fc.main()
            finally:
                crypto_prices.CACHE_FILE, crypto_prices.get_crypto_price, sys.argv, fc.time.sleep = saved
        got = json.loads(out.getvalue())["transactions"]
        pick = [(t["symbol"], t["price"], t["net_amount"], t["currency"]) for t in got]
        self.assertEqual(pick, [
            ("SOL", 140.25, 210.375, "USD"),
            ("SOL", 140.25, 280.5, "USD"),
            ("USDC", 1.0, 5.0, "USD"),
            ("USD", 1.0, 3.0, "USD"),
            ("ETH", 50.0, 150.0, "CAD"),
            ("USD", 0.0, 0.0, "USD"),
            ("ADA", 0.0, 0.0, "CAD"),
        ])
        self.assertIn("1 row(s) left UNPRICED", err.getvalue())
        self.assertEqual(got[0]["gross_amount"], 210.375)


if __name__ == "__main__":
    unittest.main()
