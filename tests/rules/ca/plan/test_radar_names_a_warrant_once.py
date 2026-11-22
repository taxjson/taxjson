"""The wash radar names a warrant bought in a share loss's window once.

A warrant on the shares is a right to acquire them (CA-SL-14): the radar
notes it for a manual check, against the in-window loss whose window it
was bought in and, while taxable shares are still held, for a loss sale
today. A warrant already named against the loss is not named again for
the sale today: one line per buy (wash radar `_row_notes`). Synthetic
data only: made-up tickers (ZZW) in throwaway folders.
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from _support.planning_radar import _radar, _row


_BUY = _row("2026-05-01", "ZZW.TO", 100, 50.0)
_SALE = _row("2026-09-08", "ZZW.TO", -60, 40.0)
_WARRANT = _row("2026-09-10", "ZZW.WS.TO", 100, 1.0)


def _warrant_notes(book):
    r = _radar("canada", book, as_of="2026-09-20")["ZZW.TO"]
    return r, [n for n in r["notes"] if n.startswith("NOTE: ZZW.WS.TO ")]


@rule(R.CA_SL_14, R.CA_PLAN_02)
class TestRadarNamesAWarrantOnce(unittest.TestCase):

    def test_named_against_the_loss_not_again_for_a_sale_today(self):
        # 40 shares still held: the sale-today view would name the same
        # warrant; the loss's note already did.
        r, notes = _warrant_notes([_BUY, _SALE, _WARRANT])
        self.assertEqual(r["category"], "BLOCKED", r["advisory"])
        self.assertEqual(r["taxable_qty"], 40.0)
        self.assertEqual(len(notes), 1, r["notes"])
        self.assertIn("inside the window of the 2026-09-08 loss", notes[0])
        self.assertEqual(r["advisory"].count("NOTE: ZZW.WS.TO "), 1,
                         r["advisory"])
        self.assertNotIn("a loss sale today", r["advisory"])

    def test_named_for_a_sale_today_when_no_loss_names_it(self):
        # No loss: the sale-today view names the warrant, once.
        r, notes = _warrant_notes([_BUY, _WARRANT])
        self.assertEqual(len(notes), 1, r["notes"])
        self.assertIn("inside the window of a loss sale today", notes[0])


if __name__ == "__main__":
    unittest.main()
