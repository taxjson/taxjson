"""Fix round 7 (wave D) of 2026-10-29, group movesxfer: transfers,
in-kind moves and handoff.

TJ-1054: a .tt sale between two arrivals sold the first arrival's units
          even when the broker had already sold them or the account held
          .tt units of its own, so the second arrival, with no purchase
          left, read as covered and was never booked.
TJ-1036: `tjs journals` listed one ambiguous journal leg once per leg it
          could pair with: the suggestion repeated, the count inflated.
TJ-1037: handoff's own-sale replay left out a split reported in another
          account's books, so a closed-year sale out of the split units
          was not its own row and a same-size January sale read as a double.
TJ-1069: the book cost a rename carries averaged an option's premiums
          where the books pair them oldest first.
TJ-1119: `tjs years` and check-filed read the deleted lock of a year closed
          as a single folder, then migrated, as a year never closed.
TJ-1118: a shared-exports run at a terminal lost its in-memory legacy-id
          migration when it saved an answer, then stopped with a traceback.
TJ-1055: an in-kind contribution of an arrival booked at book value was
          charged to the .tt purchases, so a later arrival's own purchase
          looked spent and that arrival was booked on top of it.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_1029_r7d_movesxfer
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
from _support.transfer_registered import _cli

_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestDeletedLockOfAMigratedYear(unittest.TestCase):
    """TJ-1119: the reports close-year kept show the year was closed (the
    sign TJ-0272 gave the next year's readers); its own commands say
    `lock missing`, as with a freeze note.

    Formerly: test_fix_1029_r7d_movesxfer.TestDeletedLockOfAMigratedYear"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.top = top = Path(cls._td.name) / "p"
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
                        (top / "2024", ("new-year", "2025"))):
            r = _cli(d, *args)
            assert r.returncode == 0, r.stdout + r.stderr
        (top / "2024" / "filed" / "2024.json").unlink()

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_years_says_lock_missing(self):
        r = _cli(self.top, "years")
        self.assertIn("lock missing", r.stdout, r.stdout + r.stderr)
        self.assertIn("filed/2024.json is missing", " ".join(r.stdout.split()))
        doc = json.loads(_cli(self.top, "years", "--json").stdout)
        y = next(x for x in doc["years"] if x["year"] == 2024)
        self.assertTrue(y["filed"])
        self.assertIn("filed/2024.json is missing", y["problem"])

    def test_check_filed_says_so_and_fails(self):
        # (after a run of the migrated year: check-filed reads books a
        # full run built from its inputs, TJ-4305; the run says it too)
        r = _cli(self.top / "2024", "run", "--no-input")
        self.assertIn("filed/2024.json is missing", " ".join(r.stderr.split()))
        r = _cli(self.top / "2024", "check-filed")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("filed/2024.json is missing",
                      " ".join((r.stdout + r.stderr).split()))

    def test_no_sign_or_a_year_not_ended_is_no_closed_year(self):
        from datetime import date as _date
        from taxjson.lib import project_layout as PL
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "taxjson.toml").write_text("[settings]\n")
            self.assertFalse(PL.lock_missing_frozen(d, 2024))
            rep = d / "filed" / "2024" / "reports"
            rep.mkdir(parents=True)
            self.assertFalse(PL.lock_missing_frozen(d, 2024))   # empty
            (rep / "sum.txt").write_text("x")
            self.assertTrue(PL.lock_missing_frozen(d, 2024))
            # A lock taken during the year keeps reports too.
            y = _date.today().year
            rep = d / "filed" / str(y) / "reports"
            rep.mkdir(parents=True)
            (rep / "sum.txt").write_text("x")
            self.assertFalse(PL.lock_missing_frozen(d, y))


if __name__ == "__main__":
    unittest.main()
