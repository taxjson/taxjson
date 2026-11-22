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

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib import checklist as CL
from taxjson.lib import project_layout as PL
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


def _project(root, year, accounts, books, country="canada"):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(_config(year, accounts, country))
    (root / "ticker.map").write_text("")
    for a, text in books.items():
        d = root / "inputs" / a
        d.mkdir(parents=True, exist_ok=True)
        (d / "trades.tt").write_text(text)
    return root


def _gain(root, account):
    doc = json.loads((Path(root) / "work" / f"{account}_gains_wash.json")
                     .read_text())
    return doc["summary"]["total_gain"]


# ------------------------------------------------------------------ #32
class TestLocksInTheFingerprint(unittest.TestCase):
    """#32: a new or removed filed-year lock of a US project makes the
    books stale; a Canadian project's gains read no lock.

    Formerly: test_fix_issues_31_55.TestLocksInTheFingerprint"""

    def _lock(self, root, year, country):
        (root / "filed").mkdir(exist_ok=True)
        (root / "filed" / f"{year}.json").write_text(json.dumps({
            "schema_version": 1, "year": year, "country": country,
            "accounts": {}, "totals": {},
            "closed_at": f"{year + 1}-03-01T12:00:00"}))

    def test_canada_lock_is_not_a_run_input(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2025, ["margin"], {
                "margin": "BUYSELL 2025-01-02 09:30:00 QZZQ.TO 1 CAD 1 1 0\n"})
            cfg = PL.read_config(root)
            before = CL.input_fingerprint(root, cfg)
            self._lock(root, 2024, "canada")
            self.assertEqual(CL.input_fingerprint(root, cfg), before)

    @rule(R.US_WASH_22)
    def test_us_lock_makes_the_books_stale(self):
        # margin's 2026-01-05 loss is washed by b's 2025-12-15 buy, whose
        # sale on 12-17 is in 2025: with 2025 locked, the basis add is
        # booked in the loss's year instead (b's 2025 gain moves).
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2026, ["margin", "b"], {
                "margin": ("BUYSELL 2025-01-02 09:30:00 QZZQ.US 10 USD 50 "
                           "500 0\n"
                           "BUYSELL 2026-01-05 09:30:00 QZZQ.US -10 USD 30 "
                           "300 0\n"),
                "b": ("BUYSELL 2025-12-15 09:30:00 QZZQ.US 10 USD 30 300 0\n"
                      "BUYSELL 2025-12-17 09:30:00 QZZQ.US -10 USD 31 310 "
                      "0\n")}, country="usa")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            before = _gain(root, "b")
            self._lock(root, 2025, "usa")
            why = CL.inputs_changed(root, PL.read_config(root))
            self.assertIn(CL.LOCKED_YEARS_KEY, why or "")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotAlmostEqual(_gain(root, "b"), before, places=2)
            self.assertEqual(
                CL.inputs_changed(root, PL.read_config(root)), "")
            (root / "filed" / "2025.json").unlink()
            self.assertIn(CL.LOCKED_YEARS_KEY, CL.inputs_changed(
                root, PL.read_config(root)) or "")


if __name__ == "__main__":
    unittest.main()
