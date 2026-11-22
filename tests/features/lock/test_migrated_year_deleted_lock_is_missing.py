"""Fix round 7 (wave B) of 2026-10-29, the handoff group: the year
handoff's straddler and double checks, the deleted prior-year lock, the
all-history selection recompute and the journals report (synthetic
data only).

Formerly: test_fix_1029_r7b_handoff"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj


class TestDeletedLockMigratedYear(unittest.TestCase):
    """TJ-0272 (left open): a year closed as a single folder and then
    moved by `migrate --to-years` carries no freeze note; its deleted
    lock is still a missing lock for the next year, read from the
    reports close-year kept in filed/<year>/reports/.

    Formerly: test_fix_1029_r7b_handoff.TestDeletedLockMigratedYear"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        top = Path(cls._td.name) / "p"
        (top / "inputs" / "margin").mkdir(parents=True)
        (top / "inputs" / "margin" / "trades.tt").write_text(
            "BUYSELL 2024-01-05 10:00:00 ZZQ.TO 10 CAD 10.00 -100.00 0.00\n"
            "BUYSELL 2024-07-02 10:00:00 ZZQ.TO -5 CAD 5.00 25.00 0.00\n")
        (top / "taxjson.toml").write_text(
            '[settings]\nyear = 2024\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            'province = "ON"\n\n[accounts.margin]\ntype = "taxable"\n')
        for d, args in ((top, ("run", "--no-input")),
                        (top, ("close-year", "--yes")),
                        (top, ("migrate", "--to-years")),
                        (top / "2024", ("new-year", "2025")),
                        (top / "2025", ("run", "--no-input"))):
            tj(d, *args)
        cls.top = top

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.p = Path(td.name) / "p"
        shutil.copytree(self.top, self.p, symlinks=True)
        self.y5 = self.p / "2025"

    def test_the_year_has_no_freeze_note(self):
        self.assertNotIn("## frozen by",
                         (self.p / "2024" / "taxjson.toml").read_text())
        r = tj(self.y5, "estimate", "--json")
        self.assertEqual(
            json.loads(r.stdout)["estimate"]["losses_opening"], 25.0)

    def test_deleted_lock_stops_estimate_carryover_handoff(self):
        (self.p / "2024" / "filed" / "2024.json").unlink()
        for cmd, rc in (("estimate", 1), ("carryover", 1), ("handoff", 2)):
            r = tj(self.y5, cmd, check=False)
            self.assertEqual(r.returncode, rc, (cmd, r.stdout, r.stderr))
            self.assertIn("the 2024 close-year lock is missing, while the "
                          "2024 project holds the reports close-year kept "
                          "as filed", r.stdout + r.stderr, cmd)

    def test_never_closed_is_still_no_record(self):
        (self.p / "2024" / "filed" / "2024.json").unlink()
        shutil.rmtree(self.p / "2024" / "filed" / "2024")
        r = tj(self.y5, "estimate", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
