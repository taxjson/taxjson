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
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
from _support.transfer_registered import _QH, _TAXABLE, _TFSA, _cli, _held, _project, _run, _totals

_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _tf(day: str, qty: int, book: int, sym: str = "SYN.TO") -> str:
    what = f"TRANSFER BOOK VALUE {book}.00" if book else "TRANSFER OUT"
    return (f"{day},{day},TF6,{sym},SYN CORP {what},{qty},0,0,0,0,CAD,"
            f"Transfers,99900001,Margin\n")


def _sell(day: str, qty: int, net: int, sym: str = "SYN.TO") -> str:
    return (f"{day},{day},Sell,{sym},SYN CORP,-{qty},{net / qty},{net},0,"
            f"{net},CAD,Trades,99900001,Margin\n")


def _margin(td: str, name: str, tt: str, csv: str, **accounts) -> Path:
    files = {"inputs/margin/trades.tt": tt, "inputs/margin/qt.csv": _QH + csv}
    return _project(Path(td) / name, {"margin": _TAXABLE, **accounts}, files)


class TestSaleBetweenArrivals(unittest.TestCase):
    """TJ-1054, with TJ-0823's own case kept (the second purchase dated
    after the sale: tests/rules/ca/acb/test_arrival_coverage_and_distinct_listings.py).

    Formerly: test_fix_1029_r7d_movesxfer.TestSaleBetweenArrivals"""

    def test_broker_sold_the_first_arrival(self):
        tt = ("BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n"
              "BUYSELL 2025-03-03 09:30:00 SYN.TO 10 CAD 12 120 0\n"
              "BUYSELL 2025-03-05 09:30:00 SYN.TO -10 CAD 13 130 0\n")
        csv = (_tf("2025-02-03", 10, 100) + _sell("2025-02-10", 10, 150)
               + _tf("2025-04-01", 10, 300) + _sell("2025-06-02", 10, 400))
        with tempfile.TemporaryDirectory() as td:
            root = _margin(td, "p", tt, csv)
            _run(self, root)
            self.assertAlmostEqual(_totals(root)["gain_13200"], 160.0,
                                   places=2)
            self.assertEqual(_held(root), [])

    def test_sale_of_the_accounts_own_tt_units(self):
        tt = ("BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n"
              "BUYSELL 2025-01-03 09:30:00 SYN.TO 10 CAD 10 100 0\n"
              "BUYSELL 2025-02-10 09:30:00 SYN.TO -10 CAD 15 150 0\n")
        csv = (_tf("2025-02-03", 10, 100) + _tf("2025-03-10", 10, 300)
               + _sell("2025-06-02", 20, 800))
        with tempfile.TemporaryDirectory() as td:
            root = _margin(td, "p", tt, csv)
            _run(self, root)
            self.assertAlmostEqual(_totals(root)["gain_13200"], 450.0,
                                   places=2)
            self.assertEqual(_held(root), [])

    def test_a_short_tt_round_trip_after_the_broker_sale(self):
        """The broker sold the arrived units, so a .tt sale with no .tt
        units on hand has no arrived units left to sell either: the .tt
        purchase after it closes that short and covers no arrival."""
        from taxjson.lib import transfer_in as TI

        def tx(day, q):
            return {"action": "BUYSELL", "date": day, "time": "09:30:00",
                    "symbol": "SYN.TO", "quantity": q}

        def arr(day):
            return TI.Arrival(
                account="m", symbol="SYN.TO", key="SYN.TO", date=day,
                time="09:30:00", quantity=10.0, currency="CAD",
                book_value=100.0, broker="questrade", source="",
                description="", row={"time": "09:30:00"})
        tt = [tx("2025-01-02", 10), tx("2025-03-03", -10),
              tx("2025-03-05", 10)]
        found = [arr("2025-02-03"), arr("2025-04-01")]
        TI.mark_covered(found, {"m": tt},
                        broker_rows={"m": [tx("2025-02-10", -10)]})
        self.assertEqual([a.status for a in found],
                         ["covered", "book_value"])


class TestContributedBookedArrival(unittest.TestCase):
    """TJ-1055: A0 is booked at 100 and contributed at 120 (gain 20); A1
    is covered by its purchase of 200 and sold for 400 (gain 200).

    Formerly: test_fix_1029_r7d_movesxfer.TestContributedBookedArrival"""

    def test_contribution_spends_no_later_purchase(self):
        tt = ("BUYSELL 2025-04-01 09:30:00 SYN.TO 10 CAD 20 200 0\n"
              "INKIND 2025-03-03 SYN.TO -10 CAD 12 plan=tfsa\n")
        csv = (_tf("2025-02-03", 10, 100) + _tf("2025-03-03", -10, 0)
               + _tf("2025-05-01", 10, 250) + _sell("2025-06-02", 10, 400))
        plan = _QH + ("2025-03-03,2025-03-03,TF6,SYN.TO,SYN CORP TRANSFER "
                      "IN,10,0,0,0,0,CAD,Transfers,99900002,TFSA\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p",
                            {"margin": _TAXABLE, "plan": _TFSA},
                            {"inputs/margin/trades.tt": tt,
                             "inputs/margin/qt.csv": _QH + csv,
                             "inputs/plan/qt.csv": plan})
            for _ in range(2):      # the in-kind file exists from run 2
                _run(self, root)
                self.assertIn(".tt_covers", _cli(root, "transfers").stdout)
            self.assertAlmostEqual(_totals(root)["gain_13200"], 220.0,
                                   places=2)
            self.assertEqual(
                [r for r in _held(root) if r[0] == "margin"], [])


if __name__ == "__main__":
    unittest.main()
