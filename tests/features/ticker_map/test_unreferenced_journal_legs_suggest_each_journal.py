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
import csv
import io
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from _style import CapturedWidth
from _support.transfer_registered import _TAXABLE, _cli, _project

_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _rbc_row(day, action, q, cur, desc, price=0, value=0):
    return [day, action, "SYN", "SYNTHETIC FUND", q, price, day, "99900001",
            value, cur, desc]


class TestAmbiguousJournalLegs(unittest.TestCase):
    """TJ-1036: no J~ reference, so nothing tells the same-day legs
    apart; one suggestion per journal they can be. TJ-0850's two distinct
    references stay two journals (tests/features/ticker_map/test_rbc_journal_references_tell_same_day_journals_apart.py).

    Formerly: test_fix_1029_r7d_movesxfer.TestAmbiguousJournalLegs"""

    def _journals(self, outs: int, ins: int) -> dict:
        rows = [["Date", "Activity", "Symbol", "Symbol Description",
                 "Quantity", "Price", "Settlement Date", "Account",
                 "Value", "Currency", "Description"],
                _rbc_row("March 11, 2025", "Buy", 40, "USD",
                         "SYNTHETIC FUND UNSOLICITED DA", 10, -400)]
        rows += [_rbc_row("May 12, 2025", "Transfers", -10, "USD",
                          "TFR - SYNTHETIC FUND TRANSFER TO C$")] * outs
        rows += [_rbc_row("May 12, 2025", "Transfers", 10, "CAD",
                          "TFR - SYNTHETIC FUND TRANSFER FROM U$")] * ins
        buf = io.StringIO()
        csv.writer(buf).writerows(rows)
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", {"a": _TAXABLE}, {
                "inputs/a/rbc.csv":
                    '"Activity Export as of Jan 5, 2026 at 8:59:00 am ET"'
                    "\n\n" + buf.getvalue()})
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace(
                "source_currencies = []", 'source_currencies = ["USD"]'))
            (root / "work").mkdir()
            d, rates = date(2025, 1, 1), []
            while d <= date(2026, 1, 31):
                rates.append(f"{d} 12:00:00 USD CAD 1.3 boc")
                d += timedelta(days=1)
            (root / "work/to_base.csv").write_text("\n".join(rates) + "\n")
            _cli(root, "run", "--no-input")
            return json.loads(_cli(root, "journals", "--json").stdout)

    def test_one_per_journal_it_can_be(self):
        for outs, ins, want in ((1, 2, 1), (2, 1, 1), (2, 2, 2)):
            rep = self._journals(outs, ins)
            self.assertEqual(
                (len(rep["journals"]), rep["counts"]["suggested"],
                 rep["pending"]), (want, want, want), f"{outs}x{ins}")

    def test_candidates_of_another_listing_or_day_stay(self):
        """A candidate the user must choose between stays listed: one
        out-leg that could be either of two listings, or one in-leg that
        could take either of two days' out-legs."""
        from taxjson.lib import cross_listings as XL

        def leg(sym, day, q):
            return XL.Leg("a", "rbc_direct", sym, day, q)

        def pairs(*legs):
            r = XL.analyze(list(legs), {}, {}, base_currency="CAD")
            return sorted((p.out.symbol, p.out.date, p.into.symbol)
                          for p in r["suggested"])
        self.assertEqual(
            pairs(leg("SYN.US", "2025-05-12", -10),
                  leg("SYN.TO", "2025-05-12", 10),
                  leg("SYN.TO", "2025-05-12", 10)),
            [("SYN.US", "2025-05-12", "SYN.TO")])
        self.assertEqual(
            pairs(leg("SYN.US", "2025-05-12", -10),
                  leg("SYN.US", "2025-05-13", -10),
                  leg("SYN.TO", "2025-05-13", 10)),
            [("SYN.US", "2025-05-12", "SYN.TO"),
             ("SYN.US", "2025-05-13", "SYN.TO")])
        self.assertEqual(
            pairs(leg("SYN.US", "2025-05-12", -10),
                  leg("SYN.TO", "2025-05-12", 10),
                  leg("SYN.V", "2025-05-12", 10)),
            [("SYN.US", "2025-05-12", "SYN.TO"),
             ("SYN.US", "2025-05-12", "SYN.V")])


if __name__ == "__main__":
    unittest.main()
