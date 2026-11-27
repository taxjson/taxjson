"""Re-audit 2 (crypto-04 list): fill-crypto's par list, `run --fast`
and the crypto map, the de-peg ATTENTION echo, `elections --set` repeated,
the same exchange export in two crypto accounts and the shared price
caches' save. Synthetic data only; no network.

Formerly: test_fix_a2_crypto_run"""
import tempfile
import unittest


class TestSameExportInTwoAccounts(unittest.TestCase):
    """Formerly: test_fix_a2_crypto_run.TestSameExportInTwoAccounts"""
    def test_flagged_and_strict_stops(self):
        # A2-0569: 2,000 + 2,000 booked at rc 0.
        from _support.cb_send_projects import CB_HEADER, _proj
        from _support.crypto_sends import _cli
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
