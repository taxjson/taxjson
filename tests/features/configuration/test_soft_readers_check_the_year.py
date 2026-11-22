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


class TestSoftReadersCheckTheYear(unittest.TestCase):
    """Formerly: test_fix_a2_pins1_01_filing.TestSoftReadersCheckTheYear"""
    def test_an_implausible_year_is_refused_after_a_run(self):
        # A2-0869 / A2-0870: the books were built for 2025, then
        # [settings] year was edited. sum and divs-sum refuse instead of
        # reporting tax year 1850 / 0 / True with rc 0.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _project(root, {"margin": ("taxable",
                    "BUYSELL 2025-03-03 10:00:00 ZZZ.TO 100 CAD 10 1000 0\n"
                    "BUYSELL 2025-06-02 10:00:00 ZZZ.TO -100 CAD 12 1200 "
                    "0\n")})
            _run(self, root)
            cfg = (root / "taxjson.toml").read_text()
            for bad, msg in (("1850", "not a plausible tax year"),
                             ("0", "not a plausible tax year"),
                             ("true", "must be an integer tax year")):
                (root / "taxjson.toml").write_text(
                    cfg.replace("year = 2025", f"year = {bad}"))
                for cmd in ("sum", "divs-sum", "checklist"):
                    r = _cli(root, cmd)
                    self.assertEqual(r.returncode, 1, (bad, cmd, r.stdout[-500:]))
                    self.assertIn(msg, r.stderr, (bad, cmd))


if __name__ == "__main__":
    unittest.main()
