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


def _tool(module, *args):
    return subprocess.run(
        [sys.executable, "-m", module, *args], capture_output=True,
        text=True, env=_env(), stdin=subprocess.DEVNULL, timeout=300)


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


# ------------------------------------------------------------------ #53
class TestOptionBoundaryDamagedBook(unittest.TestCase):
    """Formerly: test_fix_issues_31_55.TestOptionBoundaryDamagedBook"""

    def test_book_without_rows_is_one_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2024, ["margin"], {})
            (root / "work").mkdir()
            (root / "work" / "margin_base.json").write_text(
                '{"accounts": []}')
            r = _cli(root, "option-boundary")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn('work/margin_base.json has no "transactions" list',
                      r.stderr)


# ------------------------------------------------------------------ #54
class TestAsOfDateRange(unittest.TestCase):
    """Formerly: test_fix_issues_31_55.TestAsOfDateRange"""

    def test_problem(self):
        from taxjson.lib.dates import as_of_date_problem
        self.assertIsNone(as_of_date_problem("2025-03-03"))
        self.assertIsNone(as_of_date_problem("9999-10-31"))
        self.assertIn("out of range", as_of_date_problem("9999-12-31"))
        self.assertIn("out of range", as_of_date_problem("0001-01-01"))
        self.assertIn("0001-03-02", as_of_date_problem("0001-01-01"))
        self.assertIn("not a real calendar date",
                      as_of_date_problem("2025-02-30"))
        self.assertIn("not a YYYY-MM-DD", as_of_date_problem("2025-3-3"))

    def test_radar_and_safe_to_sell_refuse_it(self):
        with tempfile.TemporaryDirectory() as td:
            book = Path(td) / "book.json"
            book.write_text(json.dumps({"transactions": [{
                "action": "BUYSELL", "date": "2025-01-02",
                "date_settle": "2025-01-03", "time": "09:30:00",
                "symbol": "QZZQ.TO", "quantity": 10, "price": 10,
                "net_amount": 100, "currency": "CAD",
                "account": "margin"}]}))
            for mod in ("taxjson.bin.taxjson_wash_radar",
                        "taxjson.bin.taxjson_safe_to_sell"):
                r = _tool(mod, "--taxable", str(book), "--country",
                          "canada", "--date", "9999-12-31")
                self.assertNotEqual(r.returncode, 0, mod)
                self.assertNotIn("Traceback", r.stderr, mod)
                self.assertIn("--date 9999-12-31 is out of range",
                              r.stderr, mod)


if __name__ == "__main__":
    unittest.main()
