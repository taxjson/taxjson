"""The US wash radar's note for a loss sale today, on a renamed ticker.

A long call bought in the last 30 days is noted for a loss sale today of
its shares (US-WASH-12: the engine flags it, never sizes it). The calls
are keyed by the shares' rename class, so the note follows the class: a
ticker renamed and later traded again (a new identity after its rename
date), or renamed and back, still gets the note for a call on it (wash
radar `_row_notes`). Synthetic data only: made-up tickers (ZZQ, ZZR,
ZZK, ZZL) in throwaway folders.
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from _support.planning_radar import _radar, _row


def _usd(d, sym, qty, px):
    return _row(d, sym, qty, px, currency="USD")


def _rename(d, old, new):
    return dict(_usd(d, old, 1.0, 0.0), action="SPLIT", symbol_new=new)


def _call_notes(row):
    return [n for n in row["notes"] if "a long call on these shares" in n]


@rule(R.US_WASH_12, R.US_PLAN_02)
class TestUsRadarCallNoteOnARenamedTicker(unittest.TestCase):

    def test_ticker_traded_again_after_its_rename(self):
        book = [_usd("2026-01-05", "ZZQ.US", 100, 20.0),
                _rename("2026-03-02", "ZZQ.US", "ZZR.US"),
                _usd("2026-08-03", "ZZQ.US", 50, 10.0),
                _usd("2026-09-23", "ZZQ261218C00015000.US", 1, 3.0)]
        rows = _radar("usa", book, as_of="2026-10-01")
        self.assertEqual(rows["ZZQ.US"]["taxable_qty"], 50.0)
        self.assertEqual(len(_call_notes(rows["ZZQ.US"])), 1,
                         rows["ZZQ.US"]["advisory"])
        self.assertIn("bought in the last 30 days",
                      rows["ZZQ.US"]["advisory"])
        # The renamed shares are another security: no call on them.
        self.assertEqual(_call_notes(rows["ZZR.US"]), [])

    def test_ticker_renamed_and_back(self):
        book = [_usd("2026-01-05", "ZZK.US", 100, 20.0),
                _rename("2026-03-02", "ZZK.US", "ZZL.US"),
                _rename("2026-06-01", "ZZL.US", "ZZK.US"),
                _usd("2026-09-23", "ZZK261218C00015000.US", 1, 3.0)]
        row = _radar("usa", book, as_of="2026-10-01")["ZZK.US"]
        self.assertEqual(row["taxable_qty"], 100.0)
        self.assertEqual(len(_call_notes(row)), 1, row["advisory"])


if __name__ == "__main__":
    unittest.main()
