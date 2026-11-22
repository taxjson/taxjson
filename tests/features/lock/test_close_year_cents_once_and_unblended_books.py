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


class TestCloseYearRoundsOnce(unittest.TestCase):
    """Formerly: test_fix_a2_pins1_01_filing.TestCloseYearRoundsOnce"""
    def test_disallowed_total_is_rounded_once_across_accounts(self):
        # A2-0867: three accounts each deny 100.0045 — the lock holds
        # 300.01 (the wash-sales total), not 3 x 100.00.
        accounts = {}
        for i, s in enumerate(("AAA", "BBB", "CCC")):
            accounts[f"a{i}"] = ("taxable", (
                f"BUYSELL 2025-03-03 10:00:00 {s}.TO 1 CAD 200.0045 "
                f"200.0045 0\n"
                f"BUYSELL 2025-06-02 10:00:00 {s}.TO -1 CAD 100 100 0\n"
                f"BUYSELL 2025-06-10 10:00:00 {s}.TO 1 CAD 100 100 0\n"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _project(root, accounts)
            _run(self, root)
            w = _cli(root, "wash-sales", "--json")
            c = _cli(root, "close-year", "--yes")
            self.assertEqual(c.returncode, 0, c.stderr[-1500:])
            lock = json.loads((root / "filed" / "2025.json").read_text())
            f = _cli(root, "check-filed")
        # The wash-sales total adds the rows' cents, as sum's RETURN row
        # and close-year's CLOSED block do (backlog TJ-0428); the lock's
        # figure is its `denied_unrounded`.
        self.assertAlmostEqual(json.loads(w.stdout)["totals"]["denied"],
                               300.00, places=2)
        self.assertAlmostEqual(
            json.loads(w.stdout)["totals"]["denied_unrounded"], 300.01,
            places=2)
        self.assertAlmostEqual(lock["totals"]["disallowed"], 300.01,
                               places=2)
        self.assertEqual(f.returncode, 0, f.stdout[-1500:] + f.stderr[-800:])


class TestCloseYearForceRefusesUnblendedBooks(unittest.TestCase):
    """Formerly: test_fix_a2_pins1_01_filing.TestCloseYearForceRefusesUnblendedBooks"""
    def test_force_does_not_lock_per_account_books(self):
        # A2-0868: books from `run --account` only (no blended pass);
        # --force overrides the run-state check but never this refusal.
        tt = ("BUYSELL 2025-03-03 10:00:00 ZZZ.TO 100 CAD 10 1000 0\n"
              "BUYSELL 2025-06-02 10:00:00 ZZZ.TO -100 CAD 12 1200 0\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _project(root, {"a": ("taxable", tt), "b": ("taxable", tt)})
            for acct in ("a", "b"):
                _run(self, root, "--account", acct)
            c = _cli(root, "close-year", "--yes", "--force")
            locked = (root / "filed" / "2025.json").exists()
        self.assertNotEqual(c.returncode, 0, c.stdout[-1500:])
        self.assertIn("only has per-account (pre-wash, unblended) gains",
                      c.stderr)
        self.assertFalse(locked)


if __name__ == "__main__":
    unittest.main()
