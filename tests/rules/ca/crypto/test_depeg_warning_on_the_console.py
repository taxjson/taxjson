"""Re-audit 2 (crypto-04 list): fill-crypto's par list, `run --fast`
and the crypto map, the de-peg ATTENTION echo, `elections --set` repeated,
the same exchange export in two crypto accounts and the shared price
caches' save. Synthetic data only; no network.

Formerly: test_fix_a2_crypto_run"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R


class TestDepegReachesTheConsole(unittest.TestCase):
    """Formerly: test_fix_a2_crypto_run.TestDepegReachesTheConsole"""
    @rule(R.CA_CRYPTO_02)
    def test_depeg_warning_is_an_attention_line(self):
        # A2-1001: the warning reached only the .sum.
        from taxjson.pipeline.diagnostics import ATTENTION_PREFIX, echo_parse_stats
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


if __name__ == "__main__":
    unittest.main()
