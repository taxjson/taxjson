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
from _support.transfer_registered import _TAXABLE, _cli, _project, _run

_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestHandoffSplitInAnotherAccount(unittest.TestCase):
    """TJ-1037, with TJ-0808's own case kept (the split in the selling
    account's books: tests/rules/ca/rpt/test_handoff_straddlers_and_closed_split_sales.py).

    Formerly: test_fix_1029_r7d_movesxfer.TestHandoffSplitInAnotherAccount"""

    SPLIT = "SPLIT 2024-12-02 09:30:00 SYN.TO SYN.TO 2\n"

    def _handoff(self, a_extra: str, b_extra: str):
        a = "BUYSELL 2024-01-02 09:30:00 SYN.TO 1 CAD 10 10 0\n" + a_extra
        b = (b_extra + "BUYSELL 2024-01-02 09:30:00 SYN.TO 1 CAD 10 10 0\n"
             "BUYSELL 2024-12-30 09:30:00 SYN.TO -2 CAD 15 30 0\n"
             "BUYSELL 2024-12-31 09:30:00 SYN.TO 2 CAD 10 20 0\n")
        accounts = {"a": _TAXABLE, "b": _TAXABLE}
        with tempfile.TemporaryDirectory() as td:
            old = _project(Path(td) / "2024", accounts,
                           {"inputs/a/t.tt": a, "inputs/b/t.tt": b},
                           year=2024)
            _run(self, old)
            r = _cli(old, "close-year", "--yes", "--force")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            p = _project(
                Path(td) / "2025", accounts,
                {"inputs/a/t.tt": a,
                 "inputs/b/t.tt":
                     b + "BUYSELL 2025-01-02 09:30:00 SYN.TO -2 CAD 15 30 0\n"},
                tail="")
            cfg = p / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace(
                "[settings]\n",
                '[settings]\nprior_year_record = "../2024/filed/2024.json"\n',
                1))
            _run(self, p)
            r = _cli(p, "handoff", "--json")
            return r.returncode, json.loads(r.stdout)

    def test_split_in_another_accounts_books(self):
        rc, rep = self._handoff(self.SPLIT, "")
        self.assertEqual((rc, rep["double"]), (0, []), rep)

    def test_split_in_the_selling_account_or_both(self):
        for a, b in (("", self.SPLIT), (self.SPLIT, self.SPLIT)):
            rc, rep = self._handoff(a, b)
            self.assertEqual((rc, rep["double"]), (0, []), rep)


if __name__ == "__main__":
    unittest.main()
