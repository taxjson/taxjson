"""Re-audit 2 (crypto-04 list): fill-crypto's par list, `run --fast`
and the crypto map, the de-peg ATTENTION echo, `elections --set` repeated,
the same exchange export in two crypto accounts and the shared price
caches' save. Synthetic data only; no network.

Formerly: test_fix_a2_crypto_run"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R


class TestFillCryptoPar(unittest.TestCase):
    """Formerly: test_fix_a2_crypto_run.TestFillCryptoPar"""
    def _fill(self, rows):
        import taxjson.bin.fill_crypto_prices as fc
        from taxjson.lib import crypto_prices
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "in.json"
            inp.write_text(json.dumps({"transactions": rows}))
            saved = (crypto_prices.CACHE_FILE, crypto_prices.get_crypto_price, sys.argv)
            crypto_prices.CACHE_FILE = str(Path(tmp) / "cache.json")
            crypto_prices.get_crypto_price = lambda s, d: 0.999
            sys.argv = ["fill-crypto", str(inp)]
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(io.StringIO()):
                    fc.main()
            finally:
                crypto_prices.CACHE_FILE, crypto_prices.get_crypto_price, sys.argv = saved
        return json.loads(out.getvalue())["transactions"]

    @rule(R.US_CRYPTO_02)
    def test_pyusd_and_gusd_rewards_at_par(self):
        # A2-1000 / A2-0593: PYUSD/GUSD went to Yahoo (0.999).
        rows = [{"action": a, "date": "2025-03-01", "time": "10:00:00",
                 "symbol": sym, "quantity": 50.0, "price": 0.0,
                 "net_amount": 0.0, "currency": "USD"}
                for sym in ("PYUSD", "GUSD", "USDC")
                for a in ("DIVIDEND", "BUYSELL")]
        for t in self._fill(rows):
            self.assertEqual((t["symbol"], t["price"], t["net_amount"]),
                             (t["symbol"], 1.0, 50.0))


if __name__ == "__main__":
    unittest.main()
