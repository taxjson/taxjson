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
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from _support.transfer_registered import (
    _QH, _project, _run, _totals, _TAXABLE, _TFSA, _gains_with_context,
)
from taxjson.rules import ids as R
from _style import CapturedWidth


_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestOutsidePlanWithdrawal(unittest.TestCase):
    """TJ-0828: buy 20 SYN.TO for 200, sell 10 for 80 on June 2 (a loss
    of 20), contribute 10 to a TFSA outside the project on June 3 at 90
    (a loss of 10, denied for good), withdraw them on June 23 at 100
    and sell them on June 24 for 110. No replacement is held on day 30:
    the June 2 loss is allowed, -20 + 10 = -10 — as with the TFSA in
    the project.

    Formerly: test_fix_1029_r7_transfers.TestOutsidePlanWithdrawal"""

    def _case(self, td: str, inside: bool) -> Path:
        hist = ("BUYSELL 2025-01-02 09:30:00 SYN.TO 20 CAD 10 200 0\n"
                "BUYSELL 2025-06-02 09:30:00 SYN.TO -10 CAD 8 80 0\n"
                "INKIND 2025-06-03 SYN.TO -10 CAD 9 plan=tfsa\n"
                "INKIND 2025-06-23 SYN.TO 10 CAD 10 plan=tfsa\n"
                "BUYSELL 2025-06-24 09:30:00 SYN.TO -10 CAD 11 110 0\n")
        data = _QH + (
            "2025-06-03,2025-06-03,TF6,SYN.TO,SYN CORP TRANSFER OUT,-10,0,"
            "0,0,0,CAD,Transfers,99900001,Margin\n"
            "2025-06-23,2025-06-23,TF6,SYN.TO,SYN CORP TRANSFER IN,10,0,0,"
            "0,0,CAD,Transfers,99900001,Margin\n")
        accounts = {"margin": _TAXABLE}
        files = {"inputs/margin/trades.tt": hist,
                 "inputs/margin/qt.csv": data}
        if inside:
            accounts["plan"] = _TFSA
            files["inputs/plan/qt.csv"] = _QH + (
                "2025-06-03,2025-06-03,TF6,SYN.TO,SYN CORP TRANSFER IN,10,"
                "0,0,0,0,CAD,Transfers,99900002,TFSA\n"
                "2025-06-23,2025-06-23,TF6,SYN.TO,SYN CORP TRANSFER OUT,"
                "-10,0,0,0,0,CAD,Transfers,99900002,TFSA\n")
        return _project(Path(td) / ("inside" if inside else "outside"),
                        accounts, files)

    @rule(R.CA_SL_03)
    def test_withdrawal_clears_the_outside_plan(self):
        with tempfile.TemporaryDirectory() as td:
            for inside in (True, False):
                root = self._case(td, inside)
                for _ in range(2):      # a second run: the same books
                    _run(self, root)
                    self.assertAlmostEqual(
                        _totals(root)["gain_13200"], -10.0, places=2,
                        msg=f"inside={inside}")


class TestDeclaredContributionBeforeJournal(unittest.TestCase):
    """TJ-0829: margin sells 10 SYN.TO at a loss of 20 on June 2 and
    contributes its other 10 to the project's TFSA on June 3 (an INKIND
    line; the TFSA's transfer-in is there), which sells them on June
    20. An unrelated 10-share transfer-in of margin posts on June 4
    (its own .tt purchase covers it) and margin sells 10 on June 24.
    On day 30 margin and the plan hold nothing: -20 + (-10 denied) +
    10 = -10 — as when the unrelated arrival is weeks later.

    Formerly: test_fix_1029_r7_transfers.TestDeclaredContributionBeforeJournal"""

    def _case(self, td: str, arrival: str) -> Path:
        hist = ("BUYSELL 2025-01-02 09:30:00 SYN.TO 20 CAD 10 200 0\n"
                "BUYSELL 2025-06-02 09:30:00 SYN.TO -10 CAD 8 80 0\n"
                "INKIND 2025-06-03 SYN.TO -10 CAD 9 plan=tfsa\n"
                "BUYSELL 2025-06-24 09:30:00 SYN.TO -10 CAD 11 110 0\n"
                f"BUYSELL {arrival} 09:30:00 SYN.TO 10 CAD 10 100 0\n")
        data = _QH + (
            "2025-06-03,2025-06-03,TF6,SYN.TO,SYN CORP TRANSFER OUT,-10,0,"
            "0,0,0,CAD,Transfers,99900001,Margin\n"
            f"{arrival},{arrival},TF6,SYN.TO,SYN CORP TRANSFER BOOK VALUE "
            "100.00,10,0,0,0,0,CAD,Transfers,99900001,Margin\n")
        plan = _QH + (
            "2025-06-03,2025-06-03,TF6,SYN.TO,SYN CORP TRANSFER IN,10,0,0,"
            "0,0,CAD,Transfers,99900002,TFSA\n"
            "2025-06-20,2025-06-23,Sell,SYN.TO,SYN CORP,-10,10,100,0,100,"
            "CAD,Trades,99900002,TFSA\n")
        return _project(Path(td) / arrival,
                        {"margin": _TAXABLE, "plan": _TFSA},
                        {"inputs/margin/trades.tt": hist,
                         "inputs/margin/qt.csv": data,
                         "inputs/plan/qt.csv": plan})

    @rule(R.CA_SL_03)
    def test_line_wins_over_the_journal(self):
        with tempfile.TemporaryDirectory() as td:
            for arrival in ("2025-06-16", "2025-06-04"):
                root = self._case(td, arrival)
                _run(self, root)
                self.assertAlmostEqual(_totals(root)["gain_13200"], -10.0,
                                       places=2, msg=arrival)
                moves = json.loads((root / "work" / "in_kind.json")
                                   .read_text())["moves"]
                self.assertEqual([(m["registered"], m["booked"])
                                  for m in moves], [("plan", True)],
                                 arrival)


_IB_HEAD = (
    "Statement,Header,Field Name,Field Value\n"
    "Statement,Data,BrokerName,Interactive Brokers\n"
    "Statement,Data,Title,Activity Statement\n"
    'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
    "Financial Instrument Information,Header,Asset Category,Symbol,"
    "Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,"
    "Expiry,Delivery Month,Type,Strike,Code\n"
    "Financial Instrument Information,Data,Equity and Index Options,"
    "SYN 19DEC25 10 C,SYN CALL,999000101,,SYN,TSE,10,2025-12-19,,C,10,\n"
    "Transfers,Header,Asset Category,Currency,Symbol,Date,Type,Direction,"
    "Xfer Company,Xfer Account,Qty,Xfer Price,Market Value,Realized P/L,"
    "Cash Amount,Code\n")


class TestIbTransferMultiplier(unittest.TestCase):
    """TJ-0830: IB states the mini call's size (10) in its Financial
    Instrument Information; the call moves from margin to the TFSA on
    June 3 (an INKIND line at 20; the plan's own transfer row kept,
    transfers = true), a day after 100 SYN.TO were sold at a loss of
    100. The call is a right to acquire 10 shares: 10 of the loss is
    denied, and with the call's own gain of 10 the total is -80 — as
    with a ticker.map MULT line.

    Formerly: test_fix_1029_r7_transfers.TestIbTransferMultiplier"""

    def _case(self, td: str, mult_line: bool) -> Path:
        sym = "SYN251219C00010000.TO"
        hist = ("BUYSELL 2025-01-02 09:30:00 SYN.TO 100 CAD 2 200 0\n"
                f"BUYSELL 2025-01-02 09:30:00 {sym} 1 CAD 1 10 0 x10\n"
                "BUYSELL 2025-06-02 09:30:00 SYN.TO -100 CAD 1 100 0\n"
                f"INKIND 2025-06-03 {sym} -1 CAD 0 20 plan=tfsa\n")
        out = (_IB_HEAD + "Transfers,Data,Equity and Index Options,CAD,"
               "SYN 19DEC25 10 C,2025-06-03,ACATS,Out,Other Broker,"
               "99900002,-1,0,-20,0,0,\n")
        inn = (_IB_HEAD + "Transfers,Data,Equity and Index Options,CAD,"
               "SYN 19DEC25 10 C,2025-06-03,ACATS,In,Other Broker,"
               "99900002,1,0,20,0,0,\n")
        root = _project(Path(td) / ("map" if mult_line else "row"),
                        {"margin": _TAXABLE,
                         "plan": _TFSA + "\ntransfers = true"},
                        {"inputs/margin/trades.tt": hist,
                         "inputs/margin/ib.csv": out,
                         "inputs/plan/ib.csv": inn})
        if mult_line:
            (root / "ticker.map").write_text("MULT SYN 10\n")
        return root

    @rule(R.CA_SL_05)
    def test_stated_size_reaches_the_loss_rule(self):
        with tempfile.TemporaryDirectory() as td:
            for mult_line in (True, False):
                root = self._case(td, mult_line)
                _run(self, root)
                t = _totals(root)
                # The shares' -100 + 10 denied on line 13200, the
                # call's +10 on 15300.
                self.assertAlmostEqual(t["gain_13200"], -90.0, places=2,
                                       msg=f"MULT line={mult_line}")
                self.assertAlmostEqual(t["gain_all"], -80.0, places=2,
                                       msg=f"MULT line={mult_line}")

    def test_transfer_row_carries_the_multiplier(self):
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ib.csv"
            p.write_text(_IB_HEAD + "Transfers,Data,Equity and Index "
                         "Options,CAD,SYN 19DEC25 10 C,2025-06-03,ACATS,"
                         "Out,Other Broker,99900002,-1,0,-20,0,0,\n")
            rows = [t for t in IbBrokerage().parse_file(p)
                    if t.get("action") == "TRANSFER"]
            self.assertEqual([(t["quantity"], t["price"], t["multiplier"])
                              for t in rows], [(-1.0, 2.0, 10.0)])


class TestRegisteredReplacementMoved(unittest.TestCase):
    """TJ-0654: registered account r1 buys 100 SYN.TO on January 10
    (inside the loss's window); a custody move of your own takes them to
    registered account r2 on January 15, which holds them past day 30.
    The replacement is still owned: the 400 loss is denied for good, as
    when r1 keeps them. A round trip through r2 (sold before day 30)
    denies nothing, and r2's own older shares never back a denial.

    Formerly: test_fix_1029_r7_transfers.TestRegisteredReplacementMoved"""

    def _ctx(self, *rows):
        from tax_rules.dual import tx
        return [tx(a, d, "SYN.TO", q, n, currency="CAD", account=acct,
                   id=i) for i, a, d, q, n, acct in rows]

    def _denied(self, *rows) -> float:
        g = _gains_with_context(self._ctx(*rows))
        denied = round(sum(float(t.get("disallowed_amount") or 0.0)
                           for t in g["transactions"]), 2)
        # A registered account's replacement: denied for good, all of it.
        self.assertEqual(round(sum(
            float(t.get("permanently_disallowed") or 0.0)
            for t in g["transactions"]), 2), denied)
        return denied

    @rule(R.CA_SL_08)
    def test_move_between_registered_accounts_keeps_the_replacement(self):
        buy = ("rep", "BUYSELL", "2025-01-10", 100, 500, "r1")
        self.assertEqual(self._denied(buy), 400.0)
        self.assertEqual(self._denied(
            buy, ("out", "TRANSFER", "2025-01-15", -100, 0, "r1"),
            ("in", "TRANSFER", "2025-01-15", 100, 0, "r2")), 400.0)
        # The move's legs listed the other way round.
        self.assertEqual(self._denied(
            buy, ("in", "TRANSFER", "2025-01-15", 100, 0, "r2"),
            ("out", "TRANSFER", "2025-01-15", -100, 0, "r1")), 400.0)

    @rule(R.CA_SL_02)
    def test_sold_before_day_30_or_older_units_deny_nothing(self):
        buy = ("rep", "BUYSELL", "2025-01-10", 100, 500, "r1")
        move = (("out", "TRANSFER", "2025-01-15", -100, 0, "r1"),
                ("in", "TRANSFER", "2025-01-15", 100, 0, "r2"))
        # Moved, then sold by r2 inside the window: nothing held.
        self.assertEqual(self._denied(
            buy, *move,
            ("sell", "BUYSELL", "2025-02-05", -100, 550, "r2")), 0.0)
        # r1 buys and sells inside the window; r2's own 2024 units then
        # move to r1: older units, never a backing.
        self.assertEqual(self._denied(
            ("r2old", "BUYSELL", "2024-03-01", 100, 900, "r2"),
            buy,
            ("sell", "BUYSELL", "2025-01-12", -100, 520, "r1"),
            ("out", "TRANSFER", "2025-01-15", -100, 0, "r2"),
            ("in", "TRANSFER", "2025-01-15", 100, 0, "r1")), 0.0)

    @rule(R.CA_SL_08)
    def test_taxable_lot_move_keeps_a_taxable_replacement(self):
        """A taxable rebuy moved between two taxable accounts (a lot
        move) is still the replacement, with a registered move of older
        units in the window too: deferred, as without that move."""
        import contextlib
        import copy
        import io
        from taxjson.lib.pipeline import GainsRequest, run_gains
        from tax_rules.dual import tx

        def _t(i, a, d, q, n, acct, **kw):
            return tx(a, d, "SYN.TO", q, n, currency="CAD", account=acct,
                      id=i, **kw)
        book = [_t("old", "BUYSELL", "2024-01-08", 100, 1000, "a"),
                _t("loss", "BUYSELL", "2025-01-20", -100, 600, "a"),
                _t("rebuy", "BUYSELL", "2025-01-25", 100, 500, "a"),
                _t("lo", "TRANSFER", "2025-01-27", -100, 0, "a",
                   type="lot_move"),
                _t("li", "TRANSFER", "2025-01-27", 100, 0, "b",
                   type="lot_move")]
        for ctx in ([], [_t("r1old", "BUYSELL", "2024-03-01", 50, 400, "r1"),
                         _t("o", "TRANSFER", "2025-01-15", -50, 0, "r1"),
                         _t("i", "TRANSFER", "2025-01-15", 50, 0, "r2")]):
            with contextlib.redirect_stderr(io.StringIO()), \
                    contextlib.redirect_stdout(io.StringIO()):
                g = run_gains(copy.deepcopy(book), copy.deepcopy(ctx), [],
                              GainsRequest(country="canada", year=2025,
                                           taxable=True, phantom_hint=False))
            self.assertEqual(
                [(t["id"], round(t["disallowed_amount"], 2),
                  round(t["permanently_disallowed"], 2))
                 for t in g["transactions"]], [("loss", 400.0, 0.0)],
                len(ctx))


if __name__ == "__main__":
    unittest.main()
