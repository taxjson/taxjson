"""Re-audit-2 fixes, filing-reports list (helper D): the run pipeline
(crypto-sends stage, raw pass phantoms, blended .sum diagnostics, run
state banner), `sum`/`estimate` account handling and flag guards,
instalment dates, and the estimate's Canadian-issuer test and wording.

All data is synthetic (fake account ids, invented tickers).

Formerly: test_fix_a2_filrep_d
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
import _paths

REPO_ROOT = _paths.REPO


def _taxjson(root, *a, env=None):
    e = {**os.environ, "TAXJSON_OFFLINE": "1", "NO_COLOR": "1",
         "PYTHONPATH": str(REPO_ROOT / "src")}
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *a], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=e)


def _tt_project(root, accounts, *, year=2025, country="canada",
                extra=""):
    """accounts: {name: (type, tt text)}."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    cur = "CAD" if country == "canada" else "USD"
    cfg = (f'[settings]\nyear = {year}\ncountry = "{country}"\n'
           f'base_currency = "{cur}"\nsource_currencies = []\n' + extra)
    for name, (typ, tt) in accounts.items():
        cfg += f'[accounts.{name}]\ntype = "{typ}"\n'
        d = root / "inputs" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "hist.tt").write_text(tt)
    (root / "taxjson.toml").write_text(cfg)
    return root


class TestSumUnreadableAccount(unittest.TestCase):
    """A2-1119, A2-1120: `sum` never drops an account it cannot read
    with only a warning and rc 0.

    Formerly: test_fix_a2_filrep_d.TestSumUnreadableAccount"""

    @rule(R.CA_ACB_05)
    def test_a2_1119_unreadable_sheltered_gains_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = _tt_project(Path(td) / "p", {
                "margin": ("taxable",
                           "BUYSELL 2025-01-06 10:00:00 AAA.TO 100 CAD 10.00"
                           " 1000.00 0\n"
                           "BUYSELL 2025-05-06 10:00:00 AAA.TO -100 CAD 9.00"
                           " 900.00 0\n"),
                "rrsp": ("sheltered",
                         "BUYSELL 2025-01-06 10:00:00 BBB.TO 100 CAD 10.00"
                         " 1000.00 0\n"
                         "BUYSELL 2025-05-06 10:00:00 BBB.TO -100 CAD 15.00"
                         " 1500.00 0\n")})
            r = _taxjson(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            ok = _taxjson(root, "sum")
            self.assertEqual(ok.returncode, 0, ok.stderr[-2000:])
            for f in (root / "work").glob("rrsp_gains*.json"):
                f.write_text(f.read_text()[:40])
            s = _taxjson(root, "sum")
            self.assertNotEqual(s.returncode, 0, s.stdout[-2000:])
            self.assertIn("rrsp", s.stderr)

    @rule(R.US_RPT_01)
    def test_a2_1120_us_entry_without_term_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = _tt_project(Path(td) / "p", {
                "brkA": ("taxable",
                         "BUYSELL 2025-01-06 10:00:00 AAA.US 100 USD 10.00"
                         " 1000.00 0\n"
                         "BUYSELL 2025-05-06 10:00:00 AAA.US -100 USD 15.00"
                         " 1500.00 0\n"),
                "brkB": ("taxable",
                         "BUYSELL 2025-01-06 10:00:00 BBB.US 100 USD 10.00"
                         " 1000.00 0\n"
                         "BUYSELL 2025-05-06 10:00:00 BBB.US -100 USD 30.00"
                         " 3000.00 0\n")}, country="usa")
            r = _taxjson(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            for f in (root / "work").glob("brkB_gains*.json"):
                doc = json.loads(f.read_text())
                for t in doc.get("transactions", []):
                    t.pop("term", None)
                f.write_text(json.dumps(doc))
            s = _taxjson(root, "sum")
            self.assertNotEqual(s.returncode, 0, s.stdout[-2000:])
            self.assertIn("brkB", s.stderr)


class TestEstimateFlagGuards(unittest.TestCase):
    """A2-1122, A2-1123: the estimate's flag guards are the ones that
    fire (named flag, before the province check), so a test can tell
    them from the later library checks.

    Formerly: test_fix_a2_filrep_d.TestEstimateFlagGuards"""

    def _project(self, tmp, province=True):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            + ('province = "ON"\n' if province else "")
            + '[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_gains.json").write_text(json.dumps(
            {"summary": {"year": "2025"}, "transactions": [
                {"date": "2025-03-01", "symbol": "AAA.TO", "qty": 10,
                 "proceeds": 500.0, "cost": 1000.0, "gain": -500.0,
                 "currency": "CAD", "days_held": 30}]}))
        return root

    def test_a2_1122_deductions_flag_checked_before_province(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, province=False)
            for flag in ("--deductions", "--carrying-charges"):
                for v in ("inf", "-5", "nan"):
                    r = _taxjson(root, "estimate", flag, v)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertIn(f"{flag} must be a non-negative finite "
                                  f"number", r.stderr)
                    self.assertNotIn("needs a province", r.stderr)

    def test_a2_1123_other_income_flags_one_guard_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, province=False)
            for cmd in ("sum", "estimate"):
                for flag, v in (("--other-income", "nan"),
                                ("--other-losses", "-5"),
                                ("--other-income", "inf")):
                    r = _taxjson(root, cmd, flag, v)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertIn(f"{flag} must be a non-negative finite "
                                  f"number", r.stderr)
            (root / "taxjson.toml").write_text(
                (root / "taxjson.toml").read_text()
                + "[estimate]\nother_losses = -100\n")
            r = _taxjson(root, "estimate")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("[estimate] other_losses must be a non-negative "
                          "finite number", r.stderr)


if __name__ == "__main__":
    unittest.main()
