"""Questrade: every row kind keeps its own clock (backlog TJ-2381, TJ-5017).

A Questrade export stamps most rows midnight, so a day's rows tie and
keep the file's order (CA-DATE-14). The stock-dividend, cash-in-lieu,
reinvestment and interest builders stamped a fixed 09:30:00 instead
(the cash in lieu's sale 09:30:01), while a Buy, a Sell, a dividend, a
split, a fee, a transfer and a journal took the row's own time: shares a
stock dividend or a reinvestment added sorted after a sale, a journal or
a transfer listed below them on their day. Each row now takes its own
clock; the cash in lieu's sale, the one generated leg, takes its
parent's and follows it in the emitted order. A return of capital alone
stays at 09:30:00 (the ruling on TJ-5018).

Rows that share a clock keep the file's order, and a reinvestment spends
the cash a dividend of its day brought: where the export lists the REI
row before the DIV row, the credit is ordered before the purchase (an
order, not a time), so the cash ledger does not overdraw.

Every security and account is made up (QZRX, QZRY, QZRW; 99900001).
"""
import contextlib
import io
import unittest
from pathlib import Path

from taxjson.bin.taxjson_fx_cash import build_ledger
from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from taxjson.model.transaction import TaxTransaction
from taxjson.rules import ids as R
from tax_rules import rule
from _tmpfiles import private_dir

HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
        "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
        "Account #,Activity Type,Account Type\n")
MID = "12:00:00 AM"


def _row(when, settle, action, sym, desc, qty, price, gross, net, kind,
         cur="CAD"):
    if cur != "CAD":
        desc = desc.replace("C$", "U$")
    return (f"{when},{settle},{action},{sym},{desc},{qty},{price},{gross},"
            f"0.00,{net},{cur},99900001,{kind},Individual\n")


def _buy(day, sym, qty, price, settle=None):
    cost = f"{qty * price:.2f}"
    return _row(f"{day} {MID}", f"{settle or day} {MID}", "Buy", sym,
                f"{sym[:4]} CORP", qty, f"{price:.2f}", f"-{cost}",
                f"-{cost}", "Trades")


def _sell(day, sym, qty, price, clock=MID):
    cash = f"{qty * price:.2f}"
    # Settled on its own day (a cash trade): the Canadian order is by
    # settlement date first, and the clock decides within it.
    return _row(f"{day} {clock}", f"{day} {MID}", "Sell", sym,
                f"{sym[:4]} CORP", -qty, f"{price:.2f}", cash, cash,
                "Trades")


def _stock_dividend(day, sym, qty, clock=MID):
    return _row(f"{day} {clock}", f"{day} {MID}", "DIS", sym,
                f"{sym[:4]} CORP STK DIV ON 100 SHS REC 08/08/25 PAY "
                f"08/15/25", qty, "0.00", "0.00", "0.00", "Dividends")


def _reinvest(day, sym, qty, price, clock=MID, cur="CAD"):
    cash = f"{qty * price:.2f}"
    return _row(f"{day} {clock}", f"{day} {MID}", "REI", sym,
                f"{sym[:4]} FUND REINV{chr(64)}C${price:.2f}", qty,
                f"{price:.2f}", f"-{cash}", f"-{cash}",
                "Dividend reinvestment", cur=cur)


def _dividend(day, sym, cash, clock=MID, cur="CAD"):
    return _row(f"{day} {clock}", f"{day} {MID}", "DIV", sym,
                f"{sym[:4]} FUND CASH DIV ON 100 SHS REC 09/20/25 PAY "
                f"09/27/25", 0, "0.00", "0.00", cash, "Dividends", cur=cur)


def _cash_in_lieu(day, sym, clock=MID):
    return _row(f"{day} {clock}", f"{day} {MID}", "CIL", sym,
                f"{sym[:4]} CORP CASH IN LIEU OF .40000 REC 09/08/25 PAY "
                f"09/10/25", 0, "0.00", "0.00", "3.60", "Other")


def _interest(day, clock=MID, cur="CAD", cash="1.25"):
    return _row(f"{day} {clock}", f"{day} {MID}", "INT", "",
                "INTEREST ON CREDIT BALANCE 09/25", 0, "0.00", "0.00",
                cash, "Interest", cur=cur)


def _ledger(txs):
    """Ledger v1 (`tjs fx-cash`) over the parsed rows, in their order,
    at a flat rate: the overdrafts it counts and what it saw come in."""
    # (`source`, as the run stamps a broker file's rows: a return of
    # capital brings cash when it is a broker's row.)
    doc = build_ledger([dict(t, source="qt_2025.csv") for t in txs], "CAD",
                       {}, 2025, rate_of=lambda cur, on: 1.35,
                       country="canada")
    return doc["overdrafts"], doc["per_currency"]["USD"]["acquired"]


def _order(txs):
    return [(t["action"], t["symbol"], t["time"]) for t in txs]


def _parse(rows):
    folder = Path(private_dir())
    path = folder / "qt_2025.csv"
    path.write_text(HEAD + "".join(rows), encoding="utf-8")
    sink = io.StringIO()
    with contextlib.redirect_stderr(sink), contextlib.redirect_stdout(sink):
        return QuestradeBrokerage().parse_file(path)


def _gains(rows):
    """The Canadian engine's realized rows of the parsed book."""
    fields = TaxTransaction.__dataclass_fields__
    book = [TaxTransaction(**{k: v for k, v in r.items() if k in fields})
            for r in rows]
    with contextlib.redirect_stderr(io.StringIO()):
        res = CanadaTaxRules().compute_gains(book)
    return [g for g in res["transactions"]
            if "gain" in g and g.get("action") is None]


def _clocks(txs, sym):
    return [(t["time"], t["quantity"]) for t in txs if t["symbol"] == sym]


@rule(R.CA_DATE_14)
class TestSharesAddedBeforeASaleOfTheirDay(unittest.TestCase):
    """A day's rows tie at the export's midnight and keep its order."""

    def test_a_stock_dividend_listed_before_the_sale_is_sold_by_it(self):
        txs = _parse([_buy("2025-03-03", "QZRX.TO", 100, 10.0,
                           settle="2025-03-04"),
                      _stock_dividend("2025-08-15", "QZRX.TO", 10),
                      _sell("2025-08-15", "QZRX.TO", 110, 10.0)])
        self.assertEqual(_clocks(txs, "QZRX.TO"),
                         [("00:00:00", 100.0), ("00:00:00", 10.0),
                          ("00:00:00", -110.0)])
        gains = _gains(txs)
        # 110 shares at an ACB of 1000.00 sold for 1100.00: one sale,
        # nothing sold short (at 09:30 the dividend followed the sale,
        # which then sold 100 held and 10 short).
        self.assertEqual(len(gains), 1, gains)
        self.assertAlmostEqual(gains[0]["gain"], 100.0, places=2)

    def test_a_reinvestment_listed_before_the_sale_is_sold_by_it(self):
        txs = _parse([_buy("2025-08-18", "QZRY.TO", 100, 4.0,
                           settle="2025-08-19"),
                      _reinvest("2025-08-29", "QZRY.TO", 5, 4.0),
                      _sell("2025-08-29", "QZRY.TO", 105, 5.0)])
        self.assertEqual(_clocks(txs, "QZRY.TO"),
                         [("00:00:00", 100.0), ("00:00:00", 5.0),
                          ("00:00:00", -105.0)])
        gains = _gains(txs)
        # 105 units at an ACB of 420.00 sold for 525.00.
        self.assertEqual(len(gains), 1, gains)
        self.assertAlmostEqual(gains[0]["gain"], 105.0, places=2)

    def test_a_sale_listed_first_stays_first(self):
        # The control of the order: the same rows the other way round
        # are a sale before the shares arrive.
        txs = _parse([_buy("2025-03-03", "QZRX.TO", 100, 10.0,
                           settle="2025-03-04"),
                      _sell("2025-08-15", "QZRX.TO", 100, 10.0),
                      _stock_dividend("2025-08-15", "QZRX.TO", 10)])
        self.assertEqual([q for _t, q in _clocks(txs, "QZRX.TO")],
                         [100.0, -100.0, 10.0])
        gains = _gains(txs)
        self.assertEqual(len(gains), 1, gains)
        self.assertAlmostEqual(gains[0]["gain"], 0.0, places=2)


@rule(R.CA_DATE_14)
class TestEachRowsOwnClock(unittest.TestCase):

    def test_a_clock_the_export_gives_is_kept(self):
        txs = _parse([
            _buy("2025-03-03", "QZRX.TO", 100, 10.0, settle="2025-03-04"),
            _stock_dividend("2025-08-15", "QZRX.TO", 10,
                            clock="02:05:09 PM"),
            _buy("2025-08-18", "QZRY.TO", 100, 4.0, settle="2025-08-19"),
            _reinvest("2025-08-29", "QZRY.TO", 5, 4.0,
                      clock="11:20:00 AM"),
            _interest("2025-09-30", clock="02:45:10 PM")])
        self.assertEqual(_clocks(txs, "QZRX.TO")[1], ("14:05:09", 10.0))
        self.assertEqual(_clocks(txs, "QZRY.TO")[1], ("11:20:00", 5.0))
        interest = [t for t in txs if t["action"] == "INTEREST"]
        self.assertEqual([(t["date"], t["time"], t["net_amount"])
                          for t in interest],
                         [("2025-09-30", "14:45:10", 1.25)])

    def test_the_cash_in_lieu_sale_follows_its_parent_at_its_clock(self):
        for clock, want in ((MID, "00:00:00"), ("10:15:00 AM", "10:15:00")):
            with self.subTest(clock=clock):
                txs = _parse([_cash_in_lieu("2025-09-10", "QZRW.TO",
                                            clock=clock)])
                self.assertEqual(_clocks(txs, "QZRW.TO"),
                                 [(want, 0.4), (want, -0.4)])
                self.assertEqual([t["net_amount"] for t in txs], [0.0, 3.6])

    def test_nothing_of_the_day_comes_between_the_two_legs(self):
        # A purchase listed after the cash in lieu, at the same
        # midnight: the fraction is acquired and sold before it (one
        # second later, the sale would have followed the purchase and
        # taken part of its cost: a loss in place of this gain).
        txs = _parse([_buy("2025-03-03", "QZRW.TO", 100, 1.0,
                           settle="2025-03-04"),
                      _cash_in_lieu("2025-09-10", "QZRW.TO"),
                      _buy("2025-09-10", "QZRW.TO", 100, 20.0)])
        self.assertEqual([q for _t, q in _clocks(txs, "QZRW.TO")],
                         [100.0, 0.4, -0.4, 100.0])
        gains = _gains(txs)
        self.assertEqual(len(gains), 1, gains)
        # 0.4 of 100.4 shares at an ACB of 100.00: a cost of 0.40.
        self.assertAlmostEqual(gains[0]["gain"],
                               3.60 - 100.0 * 0.4 / 100.4, places=2)

    def test_a_newest_first_export_keeps_the_legs_in_order(self):
        txs = _parse([_cash_in_lieu("2025-09-10", "QZRW.TO"),
                      _buy("2025-03-03", "QZRW.TO", 100, 10.0,
                           settle="2025-03-04")])
        self.assertEqual([q for _t, q in _clocks(txs, "QZRW.TO")],
                         [100.0, 0.4, -0.4])


@rule(R.CA_DATE_14)
class TestACreditBeforeTheReinvestmentThatSpendsIt(unittest.TestCase):
    """Rows sharing a clock must not reorder the cash."""

    DAY = "2025-09-26"

    def _rei(self, **kw):
        return _reinvest(self.DAY, "QZRE", 5, 4.0, cur="USD", **kw)

    def _div(self, sym="QZRE", **kw):
        return _dividend(self.DAY, sym, "20.00", cur="USD", **kw)

    def test_the_rei_row_listed_first_does_not_overdraw(self):
        # The reviewer's min_rei_first: base walked the dividend
        # (00:00:00) before the reinvestment (09:30:00); with both at
        # midnight the file's order had the purchase spend first.
        txs = _parse([self._rei(), self._div()])
        self.assertEqual(_order(txs),
                         [("DIVIDEND", "QZRE.US", "00:00:00"),
                          ("BUYSELL", "QZRE.US", "00:00:00")])
        self.assertEqual(_ledger(txs), ({}, 20.0))

    def test_the_div_row_listed_first_is_as_listed(self):
        txs = _parse([self._div(), self._rei()])
        self.assertEqual([a for a, _s, _t in _order(txs)],
                         ["DIVIDEND", "BUYSELL"])
        self.assertEqual(_ledger(txs), ({}, 20.0))

    def test_among_other_rows_of_the_day(self):
        # The reviewer's rei_first: a purchase, then the REI row before
        # its dividend, then a later purchase listed before the interest
        # that would pay for it. The dividend moves before the
        # reinvestment; the interest is another day's and stays where
        # the file lists it (the one overdraft base has too, with the
        # opening purchase's: the export holds no deposit ledger v1
        # reads).
        txs = _parse([
            _row(f"2025-03-04 {MID}", f"2025-03-05 {MID}", "Buy", "QZRE",
                 "QZRE FUND", 100, "4.00", "-400.00", "-400.00", "Trades",
                 cur="USD"),
            self._rei(), self._div(),
            _row(f"2025-10-31 {MID}", f"2025-10-31 {MID}", "Buy", "QZRE",
                 "QZRE FUND", 1, "1.25", "-1.25", "-1.25", "Trades",
                 cur="USD"),
            _interest("2025-10-31", cur="USD")])
        self.assertEqual([a for a, _s, _t in _order(txs)],
                         ["BUYSELL", "DIVIDEND", "BUYSELL", "BUYSELL",
                          "INTEREST"])
        self.assertEqual(_ledger(txs), ({"USD": 2}, 21.25))

    def test_an_interest_credit_of_the_moment_moves_too(self):
        txs = _parse([self._rei(),
                      _interest(self.DAY, cur="USD", cash="20.00")])
        self.assertEqual([a for a, _s, _t in _order(txs)],
                         ["INTEREST", "BUYSELL"])
        self.assertEqual(_ledger(txs), ({}, 20.0))

    def test_what_is_not_moved(self):
        # Another security's dividend, another currency's, a charge.
        for later in (self._div(sym="QZRF"),
                      _dividend(self.DAY, "QZRE.TO", "20.00"),
                      _dividend(self.DAY, "QZRE", "-20.00", cur="USD")):
            with self.subTest(later=later[:60]):
                txs = _parse([self._rei(), later])
                self.assertEqual(_order(txs)[0],
                                 ("BUYSELL", "QZRE.US", "00:00:00"))

    def _roc(self):
        return _row(f"{self.DAY} {MID}", f"{self.DAY} {MID}", "DIV", "QZRE",
                    "QZRE FUND RETURN OF CAPITAL ON 100 SHS", 0, "0.00",
                    "0.00", "20.00", "Dividends", cur="USD")

    def test_a_reinvestment_paid_by_a_return_of_capital_follows_it(self):
        # A return of capital is stamped 09:30:00 by every importer (a
        # convention, not a clock); a reinvestment at the export's
        # midnight came before the cash that pays it (an overdraft in
        # ledger v1, a BORROWED line in v2, where v2.0.0 had neither).
        # With no clock of its own it takes that stamp and follows the
        # credit, whichever the export lists first.
        for rows in ([self._roc(), self._rei()], [self._rei(), self._roc()]):
            with self.subTest(first=rows[0][35:38]):
                txs = _parse(rows)
                self.assertEqual(_order(txs),
                                 [("ADJUST", "QZRE.US", "09:30:00"),
                                  ("BUYSELL", "QZRE.US", "09:30:00")])
                self.assertEqual(_ledger(txs), ({}, 20.0))

    def test_a_reinvestment_with_a_clock_keeps_it_beside_the_stamp(self):
        txs = _parse([self._roc(), self._rei(clock="08:00:00 AM")])
        self.assertEqual(sorted(_order(txs)),
                         [("ADJUST", "QZRE.US", "09:30:00"),
                          ("BUYSELL", "QZRE.US", "08:00:00")])

    def test_no_credit_lends_a_later_real_clock(self):
        # The reviewer's int_later_clock: a reinvestment at 10:00, a
        # sale of its units at noon, interest at 14:45. The reinvestment
        # took the interest's clock and fell after the sale.
        day = self.DAY
        txs = _parse([
            self._rei(clock="10:00:00 AM"),
            _row(f"{day} 12:00:00 PM", f"{day} {MID}", "Sell", "QZRE",
                 "QZRE FUND", -5, "5.00", "25.00", "25.00", "Trades",
                 cur="USD"),
            _interest(day, clock="02:45:00 PM", cur="USD")])
        self.assertEqual(_order(txs),
                         [("BUYSELL", "QZRE.US", "10:00:00"),
                          ("BUYSELL", "QZRE.US", "12:00:00"),
                          ("INTEREST", "CASH", "14:45:00")])
        # A dividend of the same security at a later real clock: each
        # row where its clock puts it, the midnight reinvestment too.
        for clock, want in (("10:00:00 AM", "10:00:00"), (MID, "00:00:00")):
            with self.subTest(clock=clock):
                txs = _parse([self._rei(clock=clock),
                              self._div(clock="02:45:00 PM")])
                self.assertEqual(_order(txs),
                                 [("BUYSELL", "QZRE.US", want),
                                  ("DIVIDEND", "QZRE.US", "14:45:00")])

    def test_a_credit_at_an_earlier_clock_changes_nothing(self):
        txs = _parse([self._rei(clock="11:20:00 AM"),
                      self._div(clock="10:15:00 AM")])
        self.assertEqual(sorted(_order(txs), key=lambda o: o[2]),
                         [("DIVIDEND", "QZRE.US", "10:15:00"),
                          ("BUYSELL", "QZRE.US", "11:20:00")])
        self.assertEqual(
            _ledger(sorted(txs, key=lambda t: t["time"])), ({}, 20.0))

    def test_every_row_keeps_its_clock(self):
        txs = _parse([self._rei(clock="11:20:00 AM"),
                      self._div(clock="11:20:00 AM")])
        self.assertEqual(_order(txs),
                         [("DIVIDEND", "QZRE.US", "11:20:00"),
                          ("BUYSELL", "QZRE.US", "11:20:00")])


@rule(R.CA_DATE_14)
class TestWhatDoesNotChange(unittest.TestCase):

    def test_a_return_of_capital_stays_at_0930(self):
        txs = _parse([
            _buy("2025-03-03", "QZRX.TO", 100, 10.0, settle="2025-03-04"),
            _row(f"2025-06-30 {MID}", f"2025-06-30 {MID}", "DIV",
                 "QZRX.TO", "QZRX CORP RETURN OF CAPITAL ON 100 SHS", 0,
                 "0.00", "0.00", "42.50", "Dividends")])
        adjust = [t for t in txs if t["action"] == "ADJUST"]
        self.assertEqual([t["time"] for t in adjust], ["09:30:00"])

    def test_trades_dividends_and_fees_keep_their_clocks(self):
        txs = _parse([
            _buy("2025-03-03", "QZRX.TO", 100, 10.0, settle="2025-03-04"),
            _sell("2025-03-20", "QZRX.TO", 40, 12.0, clock="10:15:00 AM"),
            _row("2025-04-01 09:45:00 AM", f"2025-04-01 {MID}", "DIV",
                 "QZRX.TO", "QZRX CORP CASH DIV ON 60 SHS REC 03/28/25 "
                 "PAY 04/01/25", 0, "0.00", "0.00", "15.00", "Dividends"),
            _row("2025-07-02 01:00:00 PM", f"2025-07-02 {MID}", "FCH", "",
                 "ADR CUSTODY FEE # SHARES QZRX RECORD DATE 06/15/25", 0,
                 "0.00", "0.00", "-3.50", "Fees and rebates")])
        self.assertEqual([(t["action"], t["time"]) for t in txs],
                         [("BUYSELL", "00:00:00"), ("BUYSELL", "10:15:00"),
                          ("DIVIDEND", "09:45:00"), ("FEE", "13:00:00")])


if __name__ == "__main__":
    unittest.main()
