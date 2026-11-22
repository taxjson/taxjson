"""Re-audit 2 (crypto-04 list): fill-crypto's par list, `run --fast`
and the crypto map, the de-peg ATTENTION echo, `elect --set` repeated,
the same exchange export in two crypto accounts and the shared price
caches' save. Synthetic data only; no network.

Formerly: test_fix_a2_crypto_run"""
import json
import tempfile
import unittest
from pathlib import Path


class TestFastSeesTheWorkMap(unittest.TestCase):
    """Formerly: test_fix_a2_crypto_run.TestFastSeesTheWorkMap"""
    def test_work_map_reprices_under_fast(self):
        # A2-0585: fill-crypto reads the CRYPTO lines of a work/ticker.map
        # (once work/crypto_ticker.map), the --fast stamp hashed only the
        # root map.
        from _support.crypto_project import KR_LEDGER_H, _env, _project, _run_cli
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
            (root / "work" / "ticker.map").write_text("CRYPTO SOL SOLFIX\n")
            r = _run_cli(root, "run", "--no-input", env=env)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(price(), 100.0)


if __name__ == "__main__":
    unittest.main()
