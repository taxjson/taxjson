"""Backlog fix round 7 (wave D) of 2026-10-29, group `edgeplan`: edge
cases, option-boundary, the estimate and the carryover.

TJ-1046  option-boundary's already-amended test left out the same-day
         buy-back in the lock's figure for a series and day, so a lock
         still reporting the assigned premium read as amended.
TJ-1067  under grant timing a write across Dec 31 is an option write in
         both project years, not an "option sale" in the new one.
TJ-1068  the expiry review names the share leg the engine's premium
         ledger used, also one in the opposite direction.
TJ-1113  the window review's units held on day 30 are the holders' the
         engine tests, not every account's.
TJ-1030  a minimum tax carryover is claimed only while the claim lowers
         tax (or the origin would lapse anyway).
TJ-1075  a damaged prior-year lock (its year field is not its file's)
         fails `tjs carryover` as damaged, not with --filed-lock advice.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN*, ZZ*, LOSS*), account names (margin), small
amounts (or round test figures).

Formerly: test_fix_1029_r7d_edgeplan
"""
import json
import tempfile
import unittest
from pathlib import Path

from _support.filing_forms_project import _project, _run, _tjs


class TestDamagedPriorLockCarryover(unittest.TestCase):
    """TJ-1075: filed/2024.json whose year field says 2023 named as the
    2025 project's prior_year_record. `tjs carryover` keyed it 2023 (the
    year field) and the TJ-0904 check refused `--filed-lock 2023=...`
    with advice about an argument the user never typed (exit 2).

    Formerly: test_fix_1029_r7d_edgeplan.TestDamagedPriorLockCarryover"""

    def test_damaged_lock_named_as_damaged(self):
        with tempfile.TemporaryDirectory() as d:
            p = _project(
                Path(d) / "y2024", year=2024,
                tt=("BUYSELL 2024-01-03 10:00:00 SYN.TO 10 CAD 10 100 0\n"
                    "BUYSELL 2024-06-03 10:00:00 SYN.TO -10 CAD 20 200 0\n"),
                settings="option_grant_timing_since = 2024\n")
            _run(self, p)
            r = _tjs(p, "close-year", "--yes", "--force")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lock = p / "filed" / "2024.json"
            doc = json.loads(lock.read_text())
            doc["year"] = 2023
            lock.write_text(json.dumps(doc, indent=2))
            q = _project(
                Path(d) / "y2025", year=2025,
                tt=("BUYSELL 2025-01-03 10:00:00 LOSS.TO 10 CAD 20 200 0\n"
                    "BUYSELL 2025-06-03 10:00:00 LOSS.TO -10 CAD 10 100 0\n"),
                settings=("option_grant_timing_since = 2025\n"
                          'prior_year_record = "../y2024/filed/2024.json"\n'))
            _run(self, q)
            r = _tjs(q, "carryover")
            err = " ".join((r.stdout + r.stderr).split())
            self.assertEqual(r.returncode, 1, err)
            self.assertIn("says year 2023, not 2024", err)
            self.assertIn("re-close that year", err)
            self.assertNotIn("give it as", err)


if __name__ == "__main__":
    unittest.main()
