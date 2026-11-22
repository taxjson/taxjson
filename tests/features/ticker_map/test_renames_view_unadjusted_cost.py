"""The 2026-10-29 fix round, group "edgecases": `tjs edge-cases`
and `tjs option-boundary` on synthetic books.

- TJ-0396: a contract on explicit close timing (option_premium_timing =
  "close") whose write year's lock records grant timing, or reports the
  premium, is a double count, flagged ATTENTION like a transition
  contract's (CA-OPT-07).
- TJ-0225: edge-cases names a lock reached through [settings]
  prior_year_record by its path, as option-boundary does, not as
  filed/<year>.json.

Made-up symbols (ZZQ, Q) and amounts only.

Formerly: test_fix_1029_edgecases
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib.dispatch import python_module_argv
import _paths

SRC = _paths.REPO / "src"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=600)


class TestRenamesSayTheCostIsUnadjusted(unittest.TestCase):
    """TJ-0357: `tjs renames` carries the book cost as bought (a replay
    of the trades, docs/commands.md); a denied loss the books add to
    the replacement's cost is not in it. The view now says so, with the
    amount, so the figure is not read as the books' adjusted cost.

    Formerly: test_fix_1029_edgecases.TestRenamesSayTheCostIsUnadjusted"""

    def _project(self, td, country):
        root = Path(td) / country
        (root / "inputs" / "margin").mkdir(parents=True)
        cur = "CAD" if country == "canada" else "USD"
        sfx = "TO" if country == "canada" else "US"
        (root / "taxjson.toml").write_text(
            f'[settings]\nyear = 2025\ncountry = "{country}"\n'
            f'base_currency = "{cur}"\nsource_currencies = []\n\n'
            f'[accounts.margin]\ntype = "taxable"\n')
        return root, cur, sfx

    def _renames(self, root):
        r = _cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)
        r = _cli(root, "renames", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        (ev,) = json.loads(r.stdout)["renames"]
        text = _cli(root, "renames").stdout
        return ev, " ".join(text.split())

    def _check(self, country):
        with tempfile.TemporaryDirectory() as td:
            root, cur, sfx = self._project(td, country)
            # A 500 loss on 2025-03-03, the shares bought back on
            # 2025-03-10 (denied: added to their cost), then renamed.
            (root / "inputs" / "margin" / "m.tt").write_text(
                f"BUYSELL 2025-01-06 10:00:00 ZZQ.{sfx} 10 {cur} 100 "
                f"1000 0\n"
                f"BUYSELL 2025-03-03 10:00:00 ZZQ.{sfx} -10 {cur} 50 "
                f"500 0\n"
                f"BUYSELL 2025-03-10 10:00:00 ZZQ.{sfx} 10 {cur} 50 "
                f"500 0\n"
                f"RENAME 2025-07-01 ZZQ.{sfx} ZZR.{sfx}\n")
            ev, text = self._renames(root)
        self.assertEqual([(c["qty"], c["book_cost"]) for c in
                          ev["carried"]], [(10.0, 500.0)])
        self.assertEqual(ev["denied_not_in_cost"], 500.0)
        self.assertIn(f"book cost 500.00 {cur} carried", text)
        self.assertIn(f"Book cost is as bought: the 500.00 of denied "
                      f"losses on ZZQ.{sfx} by 2025-07-01 is in the "
                      f"replacement shares' cost in the books, not here "
                      f"(`tjs list` shows the adjusted cost).", text)

    @rule(R.US_BASIS_01)
    def test_us_wash_sale(self):
        self._check("usa")

    @rule(R.CA_ACB_01)
    def test_canada_superficial_loss(self):
        self._check("canada")

    def test_no_denial_no_note(self):
        with tempfile.TemporaryDirectory() as td:
            root, cur, sfx = self._project(td, "usa")
            (root / "inputs" / "margin" / "m.tt").write_text(
                f"BUYSELL 2025-01-06 10:00:00 ZZQ.{sfx} 10 {cur} 100 "
                f"1000 0\n"
                f"RENAME 2025-07-01 ZZQ.{sfx} ZZR.{sfx}\n")
            ev, text = self._renames(root)
        self.assertEqual(ev["denied_not_in_cost"], 0.0)
        self.assertNotIn("Book cost is as bought", text)


if __name__ == "__main__":
    unittest.main()
