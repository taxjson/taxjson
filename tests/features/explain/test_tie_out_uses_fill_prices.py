"""Re-audit-2 tests-pins-01: filing-command fixes that held but had no
test failing when reverted.

- close-year rounds totals once across accounts (A2-0867, S031-20);
- close-year --force still refuses per-account (unblended) books
  (A2-0868, S046-01);
- the [settings] year check in the soft readers (A2-0869 / A2-0870);
- fx-cash refuses a taxable account with inputs but no native file
  (A2-0884, S046-18);
- the LEAPS view's same-stamp order follows the engine (A2-0891, S040-01);
- crypto-sends' per-year stablecoin currency gain counts only gifts and
  payments and leaves a superficial loss out (A2-0507, A2-0874);
- `tjs audit` passes the crypto fill prices (A2-0860, R1-271).

Synthetic data only.

Formerly: test_fix_a2_pins1_01_filing
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
import _paths


REPO_ROOT = _paths.REPO


def _cli(root, *args, home=None):
    env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1")
    if home is not None:
        env.update(HOME=str(home), TAXJSON_LOCAL_TZ="America/Toronto")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL)


def _sends_project(td):
    from _support.crypto_sends import _project as _crypto_sends_project
    return _crypto_sends_project(td)


class TestAuditUsesTheFillPrices(unittest.TestCase):
    """Formerly: test_fix_a2_pins1_01_filing.TestAuditUsesTheFillPrices"""
    @rule(R.CA_INC_04)
    def test_crypto_payment_from_a_fill_priced_reward_ties_out(self):
        # A2-0860 (R1-271): a coin reward with no price in the export is
        # priced by fill-crypto-prices; the 2026 payment's cost comes
        # from it. The audit recomputes with those fill prices and ties
        # out to the run.
        from _support.crypto_sends import PAY_ID
        with tempfile.TemporaryDirectory() as td:
            root, home = _sends_project(td)
            r = _cli(root, "run", "--no-input", home=home)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            r = _cli(root, "crypto-sends", "crypto", "--set",
                     f"{PAY_ID}=payment", home=home)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            r = _cli(root, "run", "--no-input", home=home)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            self.assertTrue((root / "work" / "crypto_filled.json").exists())
            a = _cli(root, "audit", "--json", home=home)
        self.assertEqual(a.returncode, 0, a.stdout[-1500:] + a.stderr[-800:])
        self.assertFalse(json.loads(a.stdout)["failed"])


if __name__ == "__main__":
    unittest.main()
