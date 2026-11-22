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


class TestLockKeepsBox18Gain(unittest.TestCase):
    """TJ-0898: 2024 (Canada) has a T5 box 18 dividend of 100 mapped by
    [[capital_gains_dividends]] and no sale; 2025 has a loss of 100 and
    points prior_year_record at 2024's lock. After close-year the 2025
    ledger read 2024's net as the RETURN row alone (0) and offered no
    carry-back.

    Formerly: test_fix_1029_r7b_forms.TestLockKeepsBox18Gain"""

    def test_carry_back_room_after_handoff(self):
        with tempfile.TemporaryDirectory() as d:
            p = _project(
                Path(d) / "y2024", year=2024,
                tt=("BUYSELL 2024-01-03 10:00:00 FUND.TO 10 CAD 10 100 0\n"
                    "DIVIDEND 2024-06-03 10:00:00 FUND.TO 10 CAD 10 100 0\n"),
                settings="option_grant_timing_since = 2024\n")
            with (p / "taxjson.toml").open("a") as f:
                f.write('\n[[capital_gains_dividends]]\nsymbol = "FUND.TO"'
                        '\nyear = 2024\namount = "all"\n')
            _run(self, p)
            r = _tjs(p, "close-year", "--yes")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lock = json.loads((p / "filed" / "2024.json").read_text())
            self.assertEqual(lock.get("slip_gains"), 100.0)
            q = _project(
                Path(d) / "y2025", year=2025,
                tt=("BUYSELL 2025-01-03 10:00:00 LOSS.TO 10 CAD 20 200 0\n"
                    "BUYSELL 2025-06-03 10:00:00 LOSS.TO -10 CAD 10 100 0\n"),
                settings=('prior_year_record = "../y2024/filed/2024.json"\n'
                          "option_grant_timing_since = 2025\n"))
            _run(self, q)
            r = _tjs(q, "carryover", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            led = json.loads(r.stdout)
            rows = {row["year"]: row for row in led["rows"]}
            self.assertEqual(rows[2024]["net_gain"], 100.0)
            self.assertEqual(rows[2024]["slip_gains"], 100.0)
            self.assertTrue(rows[2024]["from_lock"])
            self.assertNotIn("differs_from_filed", rows[2024])
            cands = rows[2025]["carryback_candidates"]
            self.assertEqual([(c["year"], c["amount"]) for c in cands],
                             [(2024, 100.0)])

    def test_damaged_slip_gains_is_a_damaged_lock(self):
        from taxjson.lib.filed_locks import lock_shape_problem
        self.assertIn("slip_gains", lock_shape_problem(
            {"year": 2024, "slip_gains": float("nan")}, 2024) or "")


if __name__ == "__main__":
    unittest.main()
