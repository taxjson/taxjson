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
import os
import unittest

from _style import CapturedWidth
from tax_rules import rule
from taxjson.rules import ids as R
from _support.transfer_registered import _gains_with_context

_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestMoveInParts(unittest.TestCase):
    """TJ-1102: a move between registered accounts that posts in parts,
    or gathers two senders' units in one leg, carries the replacement as
    a single-leg move does (TJ-0654). A 400 loss on January 20.

    Formerly: test_fix_1029_r7d_movesxfer.TestMoveInParts"""

    def _denied(self, *rows) -> tuple:
        from tax_rules.dual import tx
        g = _gains_with_context(
            [tx(a, d, "SYN.TO", q, n, currency="CAD", account=acct, id=i)
             for i, a, d, q, n, acct in rows])
        return (round(sum(float(t.get("disallowed_amount") or 0.0)
                          for t in g["transactions"]), 2),
                round(sum(float(t.get("permanently_disallowed") or 0.0)
                          for t in g["transactions"]), 2))

    BUY = ("rep", "BUYSELL", "2025-01-10", 100, 500, "r1")
    OUT = ("o", "TRANSFER", "2025-01-15", -100, 0, "r1")
    PARTS = (("i1", "TRANSFER", "2025-01-15", 60, 0, "r2"),
             ("i2", "TRANSFER", "2025-01-16", 40, 0, "r2"))

    @rule(R.CA_SL_08)
    def test_received_in_parts(self):
        self.assertEqual(self._denied(self.BUY, self.OUT, *self.PARTS),
                         (400.0, 400.0))
        # The first part listed before the out leg.
        self.assertEqual(
            self._denied(self.BUY, self.PARTS[0], self.OUT, self.PARTS[1]),
            (400.0, 400.0))

    @rule(R.CA_SL_08)
    def test_sent_in_parts(self):
        self.assertEqual(self._denied(
            self.BUY, ("o1", "TRANSFER", "2025-01-15", -60, 0, "r1"),
            ("o2", "TRANSFER", "2025-01-15", -40, 0, "r1"),
            ("i", "TRANSFER", "2025-01-16", 100, 0, "r2")), (400.0, 400.0))

    @rule(R.CA_SL_08)
    def test_two_senders_one_leg(self):
        self.assertEqual(self._denied(
            ("rep1", "BUYSELL", "2025-01-10", 60, 300, "r1"),
            ("rep3", "BUYSELL", "2025-01-10", 40, 200, "r3"),
            ("o1", "TRANSFER", "2025-01-15", -60, 0, "r1"),
            ("o3", "TRANSFER", "2025-01-15", -40, 0, "r3"),
            ("i", "TRANSFER", "2025-01-15", 100, 0, "r2")), (400.0, 400.0))

    @rule(R.CA_SL_02)
    def test_part_sold_before_day_30_backs_only_the_rest(self):
        self.assertEqual(self._denied(
            self.BUY, self.OUT, *self.PARTS,
            ("s", "BUYSELL", "2025-02-05", -60, 330, "r2")), (160.0, 160.0))

    @rule(R.CA_SL_02)
    def test_older_units_in_parts_back_nothing(self):
        self.assertEqual(self._denied(
            ("r1old", "BUYSELL", "2024-03-01", 100, 900, "r1"),
            self.OUT, *self.PARTS), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
