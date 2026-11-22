"""Fix round 7 of 2026-10-29, group transfers: transfers between
accounts and in-kind moves to and from registered plans.

TJ-0823: a .tt sale between two covered arrivals sold the first
          arrival's units twice (the arrival had consumed them, the
          sale took them again), so the second arrival's own .tt
          purchase looked spent and its broker book value was booked
          as well: a phantom holding and a lower gain.
TJ-0828: a declared in-kind withdrawal from a plan outside the project
          never took the shares out of that plan's side of the loss
          rule, so a loss whose replacement the plan had already given
          back was denied for good.
TJ-0829: a nearby unrelated transfer-in of the same account was paired
          with a declared in-kind contribution as a journal; the line
          then booked a move to a plan outside the project, whose
          replacement shares the real plan's sale never cleared.
TJ-0830: IB's transfer rows dropped the contract size they were priced
          with, so a mini call moved into a plan replaced 100 shares.
TJ-0827: the in-kind repair lines named the project's own inputs/ when
          its exports are shared (../inputs): a line added there is
          never read.
TJ-0821: a DISTINCT line did not stop the arrival check pairing two
          listings of one root in one account as a listing journal.
TJ-0822: the .tt coverage of a transfer-in skipped the dated RENAME
          lines and the INKIND contributions the converted rows leave out.
TJ-0654: a replacement a registered account bought in a loss's window
          and moved to another registered account was lost to the
          per-account still-held test.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_1029_r7_transfers
"""
import os
import tempfile
import unittest
from datetime import date as _date, timedelta
from pathlib import Path

from _support.transfer_registered import _QH, _project, _run, _totals, _held, _TAXABLE, _TFSA
from _style import CapturedWidth


_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestSaleBetweenCoveredArrivals(unittest.TestCase):
    """TJ-0823: two arrivals of 10 SYN.TO, each backed by its original
    .tt purchase (100, then 200); the first 10 sold by a .tt line
    between them (150); the rest sold at the broker (400). Gain 250,
    nothing held — as when the middle sale is a broker row.

    Formerly: test_fix_1029_r7_transfers.TestSaleBetweenCoveredArrivals"""

    def _case(self, td: str, manual: bool) -> Path:
        hist = ("BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n"
                "BUYSELL 2025-03-03 09:30:00 SYN.TO 10 CAD 20 200 0\n")
        data = _QH + (
            "2025-02-03,2025-02-03,TF6,SYN.TO,SYN CORP TRANSFER BOOK "
            "VALUE 100.00,10,0,0,0,0,CAD,Transfers,99900001,Margin\n"
            "2025-03-10,2025-03-10,TF6,SYN.TO,SYN CORP TRANSFER BOOK "
            "VALUE 300.00,10,0,0,0,0,CAD,Transfers,99900001,Margin\n"
            "2025-06-02,2025-06-03,Sell,SYN.TO,SYN CORP,-10,40,400,0,400,"
            "CAD,Trades,99900001,Margin\n")
        if manual:
            hist += "BUYSELL 2025-02-10 09:30:00 SYN.TO -10 CAD 15 150 0\n"
        else:
            data += ("2025-02-10,2025-02-11,Sell,SYN.TO,SYN CORP,-10,15,150,"
                     "0,150,CAD,Trades,99900001,Margin\n")
        return _project(Path(td) / ("manual" if manual else "broker"),
                        {"margin": _TAXABLE},
                        {"inputs/margin/trades.tt": hist,
                         "inputs/margin/qt.csv": data})

    def test_manual_sale_between_arrivals(self):
        with tempfile.TemporaryDirectory() as td:
            for manual in (False, True):
                root = self._case(td, manual)
                _run(self, root)
                t = _totals(root)
                self.assertAlmostEqual(t["gain_13200"], 250.0, places=2,
                                       msg=f"manual={manual}")
                self.assertEqual(_held(root), [], f"manual={manual}")


class TestDistinctListingIsNoJournal(unittest.TestCase):
    """TJ-0821: margin sends 10 SYN.TO out on March 3 and receives 10
    SYN.US (another company: DISTINCT SYN.TO SYN.US) from outside its
    books on March 4 at a stated 250, and sells them for 300 (USD). The
    arrival is no listing journal: booked at its stated cost, its sale
    a gain of 50 USD — as an arrival of DIFF.US is.

    Formerly: test_fix_1029_r7_transfers.TestDistinctListingIsNoJournal"""

    def _case(self, td: str, new: str) -> Path:
        rows = [
            ("2025-01-06", "2025-01-07", "Buy", "SYN.TO", "SYN ALPHA CORP",
             10, 10, -100, "CAD", "Trades"),
            ("2025-03-03", "2025-03-03", "TF6", "SYN.TO",
             "SYN ALPHA CORP TRANSFER OUT", -10, 0, 0, "CAD", "Transfers"),
            ("2025-03-04", "2025-03-04", "TF6", new,
             "SYN BETA CORP TRANSFER BOOK VALUE 250.00", 10, 0, 0, "USD",
             "Transfers"),
            ("2025-06-02", "2025-06-03", "Sell", new, "SYN BETA CORP", -10,
             30, 300, "USD", "Trades")]
        data = _QH + "".join(
            f"{d},{s},{a},{sym},{desc},{q},{px},{net},0,{net},{cur},{k},"
            f"99900001,Margin\n"
            for d, s, a, sym, desc, q, px, net, cur, k in rows)
        start = _date(2024, 1, 1)
        rates = "".join(f"{start + timedelta(days=i)} 12:00:00 USD CAD "
                        f"1.0000 boc\n" for i in range(1096))
        root = _project(Path(td) / new, {"margin": _TAXABLE},
                        {"inputs/margin/qt.csv": data,
                         "work/to_base.csv": rates})
        cfg = (root / "taxjson.toml").read_text()
        (root / "taxjson.toml").write_text(cfg.replace(
            "source_currencies = []", 'source_currencies = ["USD"]'))
        (root / "ticker.map").write_text(f"DISTINCT SYN.TO {new}\n")
        return root

    def test_distinct_listing_arrival_is_booked(self):
        # The figures in CAD at whatever rate the run converts with: the
        # SYN.US arrival is booked as the DIFF.US one is.
        with tempfile.TemporaryDirectory() as td:
            got = {}
            for new in ("DIFF.US", "SYN.US"):
                root = self._case(td, new)
                _run(self, root)
                t = _totals(root)
                got[new] = (round(t["gain_13200"], 2),
                            round(t["proceeds_13199"], 2))
            self.assertGreater(got["DIFF.US"][1], 0.0)
            self.assertGreater(got["DIFF.US"][0], 0.0)
            self.assertEqual(got["SYN.US"], got["DIFF.US"])


class TestCoverageReadsDeclarations(unittest.TestCase):
    """TJ-0822: the .tt coverage of a transfer-in reads the account's
    declarations too.

    A dated RENAME: 10 OLDS.TO bought for 100 (.tt), renamed NEWS.TO on
    February 3 (a .tt RENAME line), arrive by transfer on March 3 (a
    stated 250) and are sold for 300: the purchase covers the arrival,
    gain 200 and nothing held — as with a SPLIT line that renames.

    An INKIND contribution: 10 bought for 100 (.tt) go into the TFSA on
    June 3 at 120 (an INKIND line), 10 others arrive from outside on
    June 16 (a stated 100) and are sold for 150 on June 24: the
    contributed purchase covers nothing — 20 + 50 = 70, proceeds 270 —
    as when the purchase is a broker row.

    Formerly: test_fix_1029_r7_transfers.TestCoverageReadsDeclarations"""

    def _rename(self, td: str, how: str) -> Path:
        hist = "BUYSELL 2025-01-02 09:30:00 OLDS.TO 10 CAD 10 100 0\n"
        hist += {"rename": "RENAME 2025-02-03 OLDS.TO NEWS.TO\n",
                 "split": "SPLIT 2025-02-03 00:00:00 OLDS.TO NEWS.TO 1\n"
                 }[how]
        data = _QH + (
            "2025-03-03,2025-03-03,TF6,NEWS.TO,SYN CORP TRANSFER BOOK VALUE"
            " 250.00,10,0,0,0,0,CAD,Transfers,99900001,Margin\n"
            "2025-06-02,2025-06-03,Sell,NEWS.TO,SYN CORP,-10,30,300,0,300,"
            "CAD,Trades,99900001,Margin\n")
        return _project(Path(td) / how, {"margin": _TAXABLE},
                        {"inputs/margin/trades.tt": hist,
                         "inputs/margin/qt.csv": data})

    def test_dated_rename_carries_the_purchase(self):
        with tempfile.TemporaryDirectory() as td:
            for how in ("split", "rename"):
                root = self._rename(td, how)
                _run(self, root)
                self.assertAlmostEqual(_totals(root)["gain_13200"], 200.0,
                                       places=2, msg=how)
                self.assertEqual(_held(root), [], how)

    def _inkind(self, td: str, manual: bool) -> Path:
        hist = "INKIND 2025-06-03 SYN.TO -10 CAD 12 plan=tfsa\n"
        data = _QH + (
            "2025-06-03,2025-06-03,TF6,SYN.TO,SYN CORP TRANSFER OUT,-10,0,"
            "0,0,0,CAD,Transfers,99900001,Margin\n"
            "2025-06-16,2025-06-16,TF6,SYN.TO,SYN CORP TRANSFER BOOK VALUE "
            "100.00,10,0,0,0,0,CAD,Transfers,99900001,Margin\n"
            "2025-06-24,2025-06-25,Sell,SYN.TO,SYN CORP,-10,15,150,0,150,"
            "CAD,Trades,99900001,Margin\n")
        if manual:
            hist += "BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n"
        else:
            data += ("2025-01-02,2025-01-03,Buy,SYN.TO,SYN CORP,10,10,-100,"
                     "0,-100,CAD,Trades,99900001,Margin\n")
        plan = _QH + ("2025-06-03,2025-06-03,TF6,SYN.TO,SYN CORP TRANSFER "
                      "IN,10,0,0,0,0,CAD,Transfers,99900002,TFSA\n")
        return _project(Path(td) / ("manual" if manual else "broker"),
                        {"margin": _TAXABLE, "plan": _TFSA},
                        {"inputs/margin/trades.tt": hist,
                         "inputs/margin/qt.csv": data,
                         "inputs/plan/qt.csv": plan})

    def test_contributed_purchase_covers_no_later_arrival(self):
        with tempfile.TemporaryDirectory() as td:
            for manual in (False, True):
                root = self._inkind(td, manual)
                _run(self, root)
                t = _totals(root)
                self.assertAlmostEqual(t["gain_13200"], 70.0, places=2,
                                       msg=f"manual={manual}")
                self.assertAlmostEqual(t["proceeds_13199"], 270.0,
                                       places=2, msg=f"manual={manual}")
                self.assertEqual(
                    [r for r in _held(root) if r[0] == "margin"], [],
                    f"manual={manual}")


if __name__ == "__main__":
    unittest.main()
