"""Backlog fix round 7 (wave B) of 2026-10-29, group `forms`: form
export, carryover and the cash view.

TJ-0895  a commission-only disposal of unknown-cost units: its manual
         row keeps net proceeds -2, never "proceeds 2".
TJ-0896  US carryover: a loss still carried after the last year with a
         sale takes each later year's 3,000 up to the project year.
TJ-0898  a closed year's T5 box 18 capital-gains dividends stay in the
         carry-back room the next year's carryover offers.
TJ-0901  the standalone Form 8949 takes its year from the gains files
         (or the trade date), not a December trade's January settlement.
TJ-0904  taxjson-carryover --filed-lock YEAR=PATH refuses a lock of
         another year than YEAR.
TJ-0905  the standalone form export reads a gains file named twice once.
TJ-0889  `tjs cash`: a copied line keeps its book (at=alpha), so the
         copy reads into the book the original did.
TJ-0693  `tjs cash`: a stock-lending receipt is a #CREDIT line and an
         other credit, never a fee paid.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN*, ZZ*), account names (margin), amounts under
1,000 (or round test figures).

Formerly: test_fix_1029_r7b_forms
"""
import json
import tempfile
import unittest
from pathlib import Path

from _support.filing_forms_project import _tjs, _project, _run


class TestUsCarryoverRunsToProjectYear(unittest.TestCase):
    """TJ-0896: a 2025 US project whose only sale is a 2023 short-term
    loss of 9000 (and a 2025 dividend). The ledger stopped at 2023 with
    6000 carried, while recording the default 3000 for 2025 produced
    the 2024 and 2025 rows and nothing carried.

    Formerly: test_fix_1029_r7b_forms.TestUsCarryoverRunsToProjectYear"""

    TT = ("BUYSELL 2023-01-03 10:00:00 SYN.US 100 USD 100 10000 0\n"
          "BUYSELL 2023-06-01 10:00:00 SYN.US -100 USD 10 1000 0\n"
          "DIVIDEND 2025-03-03 10:00:00 NEW.US 10 USD 1 10 0\n")

    def _ledger(self, root):
        r = _tjs(root, "carryover", "--json")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)
        return json.loads(r.stdout)

    def test_trailing_years_take_their_deduction(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d) / "p", country="usa", tt=self.TT)
            _run(self, root)
            led = self._ledger(root)
            self.assertEqual([r["year"] for r in led["rows"]],
                             [2023, 2024, 2025])
            self.assertEqual([r["ordinary_income_offset"]
                              for r in led["rows"]], [3000.0] * 3)
            self.assertEqual(led["final_carryforward"], 0.0)
            # Recording the default for 2025 changes nothing.
            with (root / "taxjson.toml").open("a") as f:
                f.write("\n[carryover]\nclaimed = {2025 = 3000}\n")
            led2 = self._ledger(root)
            self.assertEqual(led2["final_carryforward"], 0.0)
            self.assertEqual([r["year"] for r in led2["rows"]],
                             [2023, 2024, 2025])

    def test_unit_through_year(self):
        from taxjson.bin.taxjson_carryover import build_usa_ledger
        nets = {2023: {"net": -9000.0, "st": -9000.0, "lt": 0.0,
                       "dispositions": 1}}
        led = build_usa_ledger(nets, {}, through_year=2025)
        self.assertEqual([r["year"] for r in led["rows"]],
                         [2023, 2024, 2025])
        self.assertEqual(led["final_carryforward"], 0.0)
        # Without it (no project year), as before.
        self.assertEqual(build_usa_ledger(nets, {})["final_carryforward"],
                         6000.0)
        # No carry left: no empty trailing row.
        gain = {2023: {"net": 50.0, "st": 50.0, "lt": 0.0,
                       "dispositions": 1}}
        self.assertEqual([r["year"] for r in build_usa_ledger(
            gain, {}, through_year=2025)["rows"]], [2023])


if __name__ == "__main__":
    unittest.main()
