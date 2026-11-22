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
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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


def _project(root, accounts, year=2025):
    """accounts: {name: (type, .tt text)}."""
    t = (f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\ncountry = "canada"\n'
         f'province = "ON"\nbase_currency = "CAD"\nsource_currencies = []\n'
         f'tax_date = "settle"\noption_grant_timing_since = 2025\n')
    for name, (typ, tt) in accounts.items():
        t += f'[accounts.{name}]\ntype = "{typ}"\n'
        (root / "inputs" / name).mkdir(parents=True)
        (root / "inputs" / name / "trades.tt").write_text(tt)
    (root / "taxjson.toml").write_text(t)


def _run(tc, root, *extra):
    r = _cli(root, "run", "--no-input", *extra)
    tc.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])


class TestFxCashRefusesAMissingNativeFile(unittest.TestCase):
    """Formerly: test_fix_a2_pins1_01_filing.TestFxCashRefusesAMissingNativeFile"""
    def test_account_with_inputs_but_no_native_file(self):
        # A2-0884: fx-cash skipped the account (rc 0, a partial ledger).
        from taxjson.pipeline.run import _NATIVE_TX_SUFFIXES
        tt = ("BUYSELL 2025-03-03 10:00:00 ZZZ.TO 100 CAD 10 1000 0\n"
              "BUYSELL 2025-06-02 10:00:00 ZZZ.TO -100 CAD 12 1200 0\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _project(root, {"margin": ("taxable", tt),
                            "cash": ("taxable", tt.replace("ZZZ", "YYY"))})
            _run(self, root)
            gone = [p for suf in _NATIVE_TX_SUFFIXES
                    for p in [root / "work" / f"cash{suf}"] if p.exists()]
            self.assertTrue(gone)
            for p in gone:
                p.unlink()
            r = _cli(root, "fx-cash")
            s = _cli(root, "sum")
        self.assertNotEqual(r.returncode, 0, r.stdout[-1000:])
        self.assertIn("no native transaction file for taxable account "
                      "'cash'", r.stderr)
        self.assertEqual(s.returncode, 0, s.stderr[-1500:])
        self.assertIn("FX-on-cash (line 15300) estimate omitted", s.stderr)


if __name__ == "__main__":
    unittest.main()
