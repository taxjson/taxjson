"""Re-audit 2 (crypto-04 list): fill-crypto's par list, `run --fast`
and the crypto map, the de-peg ATTENTION echo, `elections --set` repeated,
the same exchange export in two crypto accounts and the shared price
caches' save. Synthetic data only; no network.

Formerly: test_fix_a2_crypto_run"""
import tempfile
import unittest
from pathlib import Path


class TestElectOneSetPerCommand(unittest.TestCase):
    """Formerly: test_fix_a2_crypto_run.TestElectOneSetPerCommand"""
    def test_two_sets_are_refused_not_half_saved(self):
        # A2-0563 / A2-0568: the first --set was dropped at rc 0.
        from _support.crypto_sends import _cli
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.margin]\n'
                'type = "taxable"\n')
            r = _cli(root, Path(td), "elections", "margin", "--set",
                     "20250303-abc-xyz-fa49=rollover_s_85_1_5", "--set",
                     "20250304-abc-xyz-d19a=rollover_s_85_1_5")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("ONE --set per command", r.stderr)
            self.assertFalse(list(root.rglob("*manifest*.json")))


if __name__ == "__main__":
    unittest.main()
