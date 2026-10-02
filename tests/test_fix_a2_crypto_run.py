"""Re-audit 2 (crypto-04 list): fill-crypto's par list, `run --fast`
and the crypto map, the de-peg ATTENTION echo, `elect --set` repeated,
the same exchange export in two crypto accounts and the shared price
caches' save. Synthetic data only; no network."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule


class TestFillCryptoPar(unittest.TestCase):
    def _fill(self, rows):
        import taxjson.bin.fill_crypto_prices as fc
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "in.json"
            inp.write_text(json.dumps({"transactions": rows}))
            saved = (fc.CACHE_FILE, fc.get_crypto_price, sys.argv)
            fc.CACHE_FILE = str(Path(tmp) / "cache.json")
            fc.get_crypto_price = lambda s, d: 0.999
            sys.argv = ["fill-crypto", str(inp)]
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(io.StringIO()):
                    fc.main()
            finally:
                fc.CACHE_FILE, fc.get_crypto_price, sys.argv = saved
        return json.loads(out.getvalue())["transactions"]

    @rule("US-CRYPTO-02")
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


class TestFastSeesTheWorkMap(unittest.TestCase):
    def test_work_map_reprices_under_fast(self):
        # A2-0585: fill-crypto reads work/crypto_ticker.map, the --fast
        # stamp hashed only the root map.
        from test_fix_crypto import KR_LEDGER_H, _env, _project, _run_cli
        led = (KR_LEDGER_H
               + "LX1,RX1,2026-03-02 12:00:00,earn,reward,currency,crypto,"
                 "SOL,spot / main,1.0,0,1\n")
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".crypto_price_cache.json").write_text(json.dumps(
                {"SOL-2026-03-02": 5.0, "SOLFIX-2026-03-02": 100.0}))
            root = _project(td)
            (root / "inputs" / "crypto" / "kr_ledgers_2026.csv").write_text(
                led)
            env = _env(home, TAXJSON_OFFLINE="1")

            def price():
                rows = json.loads((root / "work" / "crypto_filled.json")
                                  .read_text())["transactions"]
                return [r["price"] for r in rows
                        if r["action"] == "DIVIDEND"][0]

            r = _run_cli(root, "run", "--no-input", env=env)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(price(), 5.0)
            (root / "work" / "crypto_ticker.map").write_text("SOL SOLFIX\n")
            r = _run_cli(root, "run", "--fast", "--no-input", env=env)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(price(), 100.0)


class TestDepegReachesTheConsole(unittest.TestCase):
    @rule("CA-CRYPTO-02")
    def test_depeg_warning_is_an_attention_line(self):
        # A2-1001: the warning reached only the .sum.
        from taxjson.bin.taxjson_run import ATTENTION_PREFIX, echo_parse_stats
        from taxjson.lib.brokerages._crypto_common import warn_depeg
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertTrue(warn_depeg("USDC", 0.88, 1000, "2025-03-01",
                                       "cb.csv"))
        line = err.getvalue().strip()
        self.assertTrue(line.startswith(ATTENTION_PREFIX), line)
        self.assertIn("120.00 USD de-peg", line)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "a_coinbase.json"
            (Path(tmp) / "a_coinbase.json.diag").write_text(line + "\n")
            con = io.StringIO()
            with contextlib.redirect_stdout(con):
                echo_parse_stats(out)
        self.assertIn("de-peg", con.getvalue())


class TestElectOneSetPerCommand(unittest.TestCase):
    def test_two_sets_are_refused_not_half_saved(self):
        # A2-0563 / A2-0568: the first --set was dropped at rc 0.
        from test_fix_sends import _cli
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.margin]\n'
                'type = "taxable"\n')
            r = _cli(root, Path(td), "elect", "margin", "--set",
                     "20250303-abc-xyz-fa49=rollover_s_85_1_5", "--set",
                     "20250304-abc-xyz-d19a=rollover_s_85_1_5")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("ONE --set per command", r.stderr)
            self.assertFalse(list(root.rglob("*manifest*.json")))


class TestSameExportInTwoAccounts(unittest.TestCase):
    def test_flagged_and_strict_stops(self):
        # A2-0569: 2,000 + 2,000 booked at rc 0.
        from test_fix_a2_crypto_sends import CB_HEADER, _proj
        from test_fix_sends import _cli
        cb = (CB_HEADER
              + "c1,2025-03-01 12:00:00 UTC,Buy,ETH,1,CAD,3000,3000,3000,0,"
                "Bought 1 ETH\n"
              + "c2,2025-04-01 12:00:00 UTC,Sell,ETH,1,CAD,5000,5000,5000,0,"
                "Sold 1 ETH\n")
        with tempfile.TemporaryDirectory() as td:
            root, home = _proj(td, {"a": {"cb_2025.csv": cb},
                                    "b": {"cb_2025.csv": cb}})
            r = _cli(root, home, "run", "--no-input", "--strict")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("booked twice", r.stderr)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            self.assertIn("identical coinbase row(s)", r.stderr)


if __name__ == "__main__":
    unittest.main()
