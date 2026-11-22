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


# ------------------------------------------------------------------ #55
class TestPartialLockCarryforward(unittest.TestCase):
    """Formerly: test_fix_issues_31_55.TestPartialLockCarryforward"""

    def test_resolvers_say_provisional(self):
        from taxjson.lib import carryforward as CF
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "filed").mkdir()
            lock = {"year": 2024, "country": "canada",
                    "carryforwards": {
                        "net_capital_loss": {"closing": 50},
                        "minimum_tax": {"closing_by_year": {"2024": 20}}}}
            settings = {"year": 2025, "country": "canada"}
            for closed, partial in (("2024-06-01T12:00:00", True),
                                    ("2025-03-01T12:00:00", False)):
                (d / "filed" / "2024.json").write_text(
                    json.dumps(dict(lock, closed_at=closed)))
                loss = CF.resolve_losses(d, settings, "canada", False)
                amt = CF.resolve_amt(d, settings, {})
                self.assertEqual(loss["other_losses"], 50)
                for res in (loss, amt):
                    said = any("provisional" in n for n in res["notes"])
                    self.assertEqual(said, partial, closed)
                    self.assertEqual(res.get("partial_lock"),
                                     2024 if partial else None)

    def test_estimate_says_it_on_one_line(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2025, ["margin"], {
                "margin": ("BUYSELL 2025-01-10 09:30:00 QZZQ.TO 10 CAD 10 "
                           "100 0\n"
                           "BUYSELL 2025-05-10 09:30:00 QZZQ.TO -5 CAD 12 "
                           "60 0\n")})
            (root / "filed").mkdir()
            (root / "filed" / "2024.json").write_text(json.dumps({
                "year": 2024, "country": "canada", "accounts": {},
                "closed_at": "2024-06-01T12:00:00",
                "carryforwards": {"net_capital_loss": {"closing": 50}}}))
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _cli(root, "estimate", "--province", "ON")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            acts = [ln for ln in r.stdout.splitlines()
                    if ln.startswith("! Carryovers come from the 2024")]
            self.assertEqual(len(acts), 1, r.stdout)
            self.assertLessEqual(len(acts[0]), 100)
            r = _cli(root, "estimate", "--province", "ON", "--json")
            doc = json.loads(r.stdout)
            self.assertEqual(
                doc["estimate"]["carry_sources"]["partial_lock"], 2024)


if __name__ == "__main__":
    unittest.main()
