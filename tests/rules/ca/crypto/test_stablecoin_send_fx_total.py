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
import os
import re
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


@rule(R.CA_CRYPTO_08)
class TestCryptoSendsStablecoinFxTotal(unittest.TestCase):
    """The per-year line counts the currency gain of stablecoin GIFTS and
    PAYMENTS only, a superficial loss excluded: Kraken's 100-USDC gift
    gains 7.97; Coinbase's 10-USDC send loses 0.21 (superficial); the
    2026 Hybrid Earn sweep is a self move.

    Formerly: test_fix_a2_pins1_01_filing.TestCryptoSendsStablecoinFxTotal"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root, cls.home = _sends_project(cls._tmp.name)
        r = _cli(cls.root, "run", "--no-input", home=cls.home)
        assert r.returncode == 0, r.stderr[-2000:]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _total(self, cb_decision):
        from _support.crypto_sends import CB_USDC_ID, HYBRID_ID, KR_USDC_ID
        for sid, dec in ((KR_USDC_ID, "gift"), (CB_USDC_ID, cb_decision),
                         (HYBRID_ID, "self")):
            r = _cli(self.root, "crypto-sends", "crypto", "--set",
                     f"{sid}={dec}", home=self.home)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        r = _cli(self.root, "crypto-sends", "crypto", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertIn("likely SUPERFICIAL", r.stdout)
        # The per-year totals: `  YYYY:  +n.nn CAD` under the section
        # heading, then the "Superficial losses are excluded" paragraph.
        lines = r.stdout.splitlines()
        i = lines.index("STABLECOIN GIFTS AND PAYMENTS — currency gain")
        self.assertIn("Superficial losses are excluded",
                      " ".join(r.stdout.split()))
        return [ln.strip() for ln in lines[i + 1:]
                if re.match(r"  \d{4}:", ln)]

    def test_superficial_loss_is_excluded(self):
        # A2-0874: the CB gift's -0.21 is superficial, so 2025 = +7.97.
        self.assertEqual(self._total("gift"), ["2025:  +7.97 CAD"])

    def test_self_sends_are_not_counted(self):
        # A2-0507: the 2026 self sweep (-0.10, not superficial) stays out.
        self.assertEqual(self._total("self"), ["2025:  +7.97 CAD"])


if __name__ == "__main__":
    unittest.main()
