"""GitHub issues #31, #32, #49, #53, #54, #55 (synthetic data only).

- #31: rows of two accounts at one moment follow the accounts' NAMES,
  not their order in taxjson.toml: reordering the file changes no gain,
  and so the books stay current (CA-DATE-14 / US-DATE-13).
- #32: the filed-year locks a US gains run reads (US-WASH-22) are part
  of the run's fingerprint; Canada's gains read none.
- #49: `years` names a damaged filed/<year>.json instead of calling the
  year filed.
- #53: option-boundary on a base book without its rows: one error line.
- #54: wash-radar / safe-to-sell refuse a --date whose window leaves
  the calendar.
- #55: carry-forwards read from a lock taken before its year ended say
  they are provisional.

Formerly: test_fix_issues_31_55
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.years_report import years_report
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"


def _env():
    return dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
                TAXJSON_WIDTH="0", NO_COLOR="1")


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
         str(root), *args], capture_output=True, text=True, env=_env(),
        stdin=subprocess.DEVNULL, timeout=300)


def _config(year, accounts, country="canada"):
    cur = "CAD" if country == "canada" else "USD"
    return (f'[settings]\ncountry = "{country}"\nyear = {year}\n'
            f'base_currency = "{cur}"\nsource_currencies = []\n'
            + "".join(f'\n[accounts.{a}]\ntype = "taxable"\n'
                      for a in accounts))


# ------------------------------------------------------------------ #49
class TestYearsNamesADamagedLock(unittest.TestCase):
    """Formerly: test_fix_issues_31_55.TestYearsNamesADamagedLock"""

    def test_shapes(self):
        from taxjson.lib.filed_locks import lock_shape_problem
        good = {"year": 2024, "accounts": {"m": {}}, "totals": {}}
        self.assertIsNone(lock_shape_problem(good, 2024))
        self.assertIsNone(lock_shape_problem({}, 2024))
        for bad in ([], None, "x", 3, {"accounts": []},
                    {"accounts": {"m": 1}}, {"totals": []},
                    {"year": 2023}, {"year": "soon"}):
            self.assertTrue(lock_shape_problem(bad, 2024), bad)

    def test_years_reports_the_problem(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            for y, body in ((2024, "[]"), (2025, json.dumps(
                    {"year": 2025, "accounts": {}, "totals": {},
                     "closed_at": "2026-03-01T12:00:00"}))):
                d = top / str(y)
                d.mkdir()
                (d / "taxjson.toml").write_text(_config(y, ["margin"]))
                (d / "filed").mkdir()
                (d / "filed" / f"{y}.json").write_text(body)
            rep = {r["year"]: r for r in years_report(top)["years"]}
            self.assertIn("filed/2024.json is not a close-year lock",
                          rep[2024]["problem"] or "")
            self.assertIsNone(rep[2025]["problem"])
            r = _cli(top, "years")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            row = [ln for ln in r.stdout.splitlines()
                   if ln.startswith("2024")]
            self.assertIn("lock damaged", row[0])
            self.assertIn("! 2024: filed/2024.json is not a close-year",
                          r.stdout)


if __name__ == "__main__":
    unittest.main()
