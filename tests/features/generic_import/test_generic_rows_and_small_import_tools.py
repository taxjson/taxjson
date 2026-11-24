"""Generic importer and small import-tool fixes of 2026-10-29, round 7
wave B (group generic).

TJ-0863 (a generic option row's cash at 100 shares despite a ticker.map
MULT 10), TJ-0731 (a zero-price sale whose fee is its whole cash booked
the fee as proceeds), TJ-0732 (a commission-bearing $0 option expiry
settled T+1, into the next year), TJ-0734 (explicit execution clocks
dropped: a newest-first export replayed a day's trades backwards),
TJ-0668 (a futures row whose commission or rebate crosses its notional
took its cash direction from the price sign alone), TJ-0855 (Webull
overlapping downloads counted one expiry once per copy), TJ-0736
(taxjson-merge2 shared one broker account's withholding with another
account's dividend), TJ-0737 (an XLSX formula with no saved result
converted to an empty cell).

Every fixture is SYNTHETIC: made-up tickers (SYN), account 99900001,
invented amounts. Each test's files live in a folder of their own.

Formerly: test_fix_1029_r7b_generic
"""
import contextlib
import csv
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _siblings import with_siblings

from _support.generic_gains import _mapping, _parse, _gains

try:
    import openpyxl
    _XLSX = True
except ImportError:
    _XLSX = False


class TestGenericOptionMult(unittest.TestCase):
    """TJ-0863: the option's contract size is ticker.map MULT first, else
    a mini when only x10 fits the row's own amount, else 100."""

    SYM = "SYN251219C00005000.TO"
    TM = f"MULT {SYM} 10\n"

    def test_price_only_rows_use_the_declared_mult(self):
        csv_text = ("Date,Action,Symbol,Qty,Price,Currency\n"
                    f"2025-06-02,B,{self.SYM},1,2,CAD\n"
                    f"2025-06-04,S,{self.SYM},-1,3,CAD\n")
        rows, _err = _parse(csv_text, _mapping(), ticker_map=self.TM)
        self.assertEqual([r["net_amount"] for r in rows], [20.0, 30.0])
        self.assertEqual([r["gross_amount"] for r in rows], [20.0, 30.0])
        g = _gains(rows, 2025, ticker_map=self.TM)
        self.assertAlmostEqual(g["summary"]["total_gain"], 10.0)

    def test_explicit_mini_amounts_accepted_with_the_mult(self):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                    f"2025-06-02,B,{self.SYM},1,2,-20,CAD\n"
                    f"2025-06-04,S,{self.SYM},-1,3,30,CAD\n")
        rows, _err = _parse(csv_text, _mapping('amount = "Amount"'),
                            ticker_map=self.TM)
        self.assertEqual([r["net_amount"] for r in rows], [20.0, 30.0])

    def test_mini_amounts_without_mult_are_derived_as_a_mini(self):
        # docs/tax-rules.md: a row whose own money fits a mini's 10 and
        # not 100 is booked as a mini (the other parsers' rule).
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                    f"2025-06-02,B,{self.SYM},1,2,-20,CAD\n")
        rows, _err = _parse(csv_text, _mapping('amount = "Amount"'))
        self.assertEqual(rows[0]["net_amount"], 20.0)
        self.assertEqual(rows[0]["multiplier"], 10.0)
        self.assertEqual(rows[0]["contract_size_basis"], "derived")

    def test_standard_contract_unchanged(self):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                    f"2025-06-02,B,{self.SYM},1,2,-200,CAD\n")
        rows, _err = _parse(csv_text, _mapping('amount = "Amount"'))
        self.assertEqual(rows[0]["net_amount"], 200.0)
        self.assertNotIn("multiplier", rows[0])
        # Price only, no MULT: the standard 100 as before.
        rows, _err = _parse("Date,Action,Symbol,Qty,Price,Currency\n"
                            f"2025-06-02,B,{self.SYM},1,2,CAD\n",
                            _mapping())
        self.assertEqual(rows[0]["net_amount"], 200.0)

    def test_amount_fitting_neither_size_still_refused(self):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                    f"2025-06-02,B,{self.SYM},1,2,-77,CAD\n")
        with self.assertRaisesRegex(ValueError, "differs from qty x price"):
            _parse(csv_text, _mapping('amount = "Amount"'))


_FEE_MAP = _mapping('amount = "Amount"\nfee = "Fee"', 'fee_sign = "charged"',
                    "[options]\nallow_large_fees = true")


class TestZeroPriceDisposalWithFee(unittest.TestCase):
    """TJ-0731: a sale at a zero price whose fee is its only cash nets
    the fee as a NEGATIVE proceeds, stated or derived alike."""

    def _rows(self, amount):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Fee,Currency\n"
                    "2025-01-06,B,SYN.TO,1,100,100,0,CAD\n"
                    f"2025-06-03,S,SYN.TO,-1,0,{amount},1,CAD\n")
        return _parse(csv_text, _FEE_MAP)[0]

    def test_explicit_amount_agrees_with_derived(self):
        explicit = self._rows("-1")
        derived = self._rows("")
        self.assertEqual(explicit[1]["net_amount"], -1.0)
        self.assertEqual(derived[1]["net_amount"], -1.0)
        g = _gains(explicit, 2025)
        self.assertAlmostEqual(g["summary"]["total_gain"], -101.0)

    def test_unsigned_quantity_sale_paying_its_fee_accepted(self):
        # A positive quantity with a negative amount is a buy by its
        # signs, unless the commission really is the whole cash.
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Fee,Currency\n"
                    "2025-01-06,B,SYN.TO,1,100,100,0,CAD\n"
                    "2025-06-03,S,SYN.TO,1,0,-1,1,CAD\n")
        rows, _err = _parse(csv_text, _FEE_MAP)
        self.assertEqual(rows[1]["net_amount"], -1.0)

    def test_zero_price_sale_with_real_proceeds_unchanged(self):
        # No price column filled but an amount: the amount is the cash.
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Fee,Currency\n"
                    "2025-06-03,S,SYN.TO,-1,,95,5,CAD\n")
        rows, _err = _parse(csv_text, _FEE_MAP)
        self.assertEqual(rows[0]["net_amount"], 95.0)


class TestCommissionBearingExpiry(unittest.TestCase):
    """TJ-0732: a $0 option close at its expiry is the expiry even when
    a commission makes its cash nonzero: dated and settled that day."""

    SYM = "SYN251231P00100000.TO"

    def _rows(self, amount):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Fee,Currency\n"
                    f"2025-01-06,B,{self.SYM},1,1,100,0,CAD\n"
                    f"2025-12-31,S,{self.SYM},-1,0,{amount},1,CAD\n")
        return _parse(csv_text, _FEE_MAP)[0]

    @rule(R.CA_DATE_08)
    def test_expiry_settles_on_the_expiry_day(self):
        for amount in ("-1", ""):
            rows = self._rows(amount)
            self.assertEqual(rows[1]["date_settle"], "2025-12-31", amount)
            self.assertEqual(rows[1]["net_amount"], -1.0, amount)
            g25 = _gains(rows, 2025)
            g26 = _gains(rows, 2026)
            self.assertAlmostEqual(g25["summary"]["total_gain"], -101.0)
            self.assertAlmostEqual(g26["summary"]["total_gain"], 0.0)

    def test_close_posted_after_expiry_with_commission_is_clamped(self):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Fee,Currency\n"
                    f"2025-01-06,B,{self.SYM},1,1,100,0,CAD\n"
                    f"2026-01-02,S,{self.SYM},-1,0,-1,1,CAD\n")
        rows, err = _parse(csv_text, _FEE_MAP)
        self.assertEqual(rows[1]["date"], "2025-12-31")
        self.assertEqual(rows[1]["date_settle"], "2025-12-31")
        self.assertIn("dated the expiry day", err)


class TestExecutionClocks(unittest.TestCase):
    """TJ-0734: a date format with a clock keeps each row's time, so a
    newest-first export replays the day in time order."""

    LINES = ["2025-03-03 10:00:00,B,SYN.TO,10,10,100,CAD",
             "2025-03-03 11:00:00,S,SYN.TO,-10,20,200,CAD",
             "2025-03-03 12:00:00,B,SYN.TO,10,30,300,CAD"]
    MAP = _mapping('amount = "Amount"', 'date = "%Y-%m-%d %H:%M:%S"')

    def _rows(self, lines):
        return _parse("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                      + "\n".join(lines) + "\n", self.MAP)[0]

    @rule(R.CA_DATE_14)
    def test_reverse_export_gives_the_same_gain(self):
        for lines in (self.LINES, list(reversed(self.LINES))):
            rows = self._rows(lines)
            self.assertEqual(sorted(r["time"] for r in rows),
                             ["10:00:00", "11:00:00", "12:00:00"])
            g = _gains(rows, 2025)
            self.assertAlmostEqual(g["summary"]["total_gain"], 100.0)
            self.assertEqual(g["wash_sales"], [])

    def test_newest_first_same_clock_rows_still_reversed(self):
        # Rows at one moment keep the export's order, read bottom-up in
        # a newest-first file (CA-DATE-14).
        lines = ["2025-03-04 10:00:00,S,SYN.TO,-10,20,200,CAD",
                 "2025-03-03 10:00:00,B,SYN.TO,10,30,300,CAD",
                 "2025-03-03 10:00:00,S,SYN.TO,-10,20,200,CAD",
                 "2025-03-03 10:00:00,B,SYN.TO,10,10,100,CAD"]
        rows = self._rows(lines)
        self.assertEqual([r["price"] for r in rows], [10.0, 20.0, 30.0,
                                                      20.0])

    def test_date_only_format_keeps_the_default_stamp(self):
        rows, _err = _parse(
            "Date,Action,Symbol,Qty,Price,Amount,Currency\n"
            "2025-03-03,B,SYN.TO,10,10,100,CAD\n",
            _mapping('amount = "Amount"'))
        self.assertEqual(rows[0]["time"], "09:30:00")


class TestClocksOfEventRows(unittest.TestCase):
    """TJ-5018: with a clock in the date format, a transfer and an income
    row keep their time as a trade does (both were stamped 09:30:00). A
    cost adjustment does not, by rule: every importer stamps one
    09:30:00, the clock on its row being the broker's posting, so it
    follows its day's rows stamped 09:30:00 or earlier and precedes a
    trade clocked later (CA-DATE-14 / US-DATE-13)."""

    HEAD = "Date,Action,Symbol,Qty,Price,Amount,Currency\n"
    MAP = _mapping('amount = "Amount"', 'date = "%Y-%m-%d %H:%M:%S"',
                   'DIV = "dividend"\nPIL = "dividend_in_lieu"\nTAX = "tax"\n'
                   'INT = "interest"\nFEE = "fee"\nTIN = "transfer_in"\n'
                   'TOUT = "transfer_out"\nROC = "roc"\nADJ = "adjust"')

    def _rows(self, *lines, mapping=None):
        return _parse(self.HEAD + "".join(line + "\n" for line in lines),
                      mapping or self.MAP)[0]

    def test_a_transfer_and_an_income_row_keep_the_clock(self):
        rows = self._rows("2025-03-03 12:00:00,B,SYN.TO,10,10,100,CAD",
                          "2025-03-03 13:01:02,DIV,SYN.TO,,,5,CAD",
                          "2025-03-03 13:01:03,PIL,SYN.TO,,,5,CAD",
                          "2025-03-03 13:01:04,TAX,SYN.TO,,,-1,CAD",
                          "2025-03-03 13:01:05,INT,,,,2,CAD",
                          "2025-03-03 13:01:06,FEE,,,,-3,CAD",
                          "2025-03-03 14:00:00,TIN,SYN.TO,5,,,CAD",
                          "2025-03-03 15:59:00,TOUT,SYN.TO,15,,,CAD")
        self.assertEqual([(r["action"], r["time"]) for r in rows],
                         [("BUYSELL", "12:00:00"), ("DIVIDEND", "13:01:02"),
                          ("DIVIDEND_IN_LIEU", "13:01:03"), ("TAX", "13:01:04"),
                          ("INTEREST", "13:01:05"), ("FEE", "13:01:06"),
                          ("TRANSFER", "14:00:00"), ("TRANSFER", "15:59:00")])

    def test_without_a_clock_they_are_stamped_as_before(self):
        rows = self._rows("2025-03-03,DIV,SYN.TO,,,5,CAD", "2025-03-03,TIN,SYN.TO,5,,,CAD",
                          mapping=self.MAP.replace(" %H:%M:%S", ""))
        self.assertEqual([r["time"] for r in rows], ["09:30:00", "09:30:00"])

    @rule(R.CA_DATE_14)
    @rule(R.US_DATE_13)
    def test_a_cost_adjustment_is_stamped_the_day_s_open_as_every_importer_s(self):
        from taxjson.lib.brokerages.base import roc_adjust_row
        rows = self._rows("2025-03-03 13:00:00,ROC,SYN.TO,,,5,CAD",
                          "2025-03-03 15:59:00,ADJ,SYN.TO,,,-4,CAD")
        self.assertEqual([(r["action"], r.get("type", ""), r["time"]) for r in rows],
                         [("ADJUST", "roc", "09:30:00"), ("ADJUST", "", "09:30:00")])
        self.assertEqual(roc_adjust_row(symbol="SYN.TO", currency="CAD", date="2025-03-03",
                                        desc="", amount=5.0, account="A")["time"],
                         "09:30:00")

    @rule(R.CA_DATE_14)
    def test_the_stamp_places_a_return_of_capital_among_the_day_s_trades(self):
        # 10 held at 100; 5 sold for 50; a return of capital of 5 posted
        # at 13:00, stamped 09:30:00. The trades settle on their trade
        # date, so the stamp alone places it: before a sale at noon (the
        # 10 units cost 95: gain 2.50, 5 left at 47.50), after one at
        # 09:00 or at 09:30:00 (gain 0, 5 left at 45).
        mapping = self.MAP + "\n[options]\nsettle_on_trade_date = true\n"
        for clock, gain, left in (("12:00:00", 2.5, 47.5), ("09:00:00", 0.0, 45.0),
                                  ("09:30:00", 0.0, 45.0)):
            with self.subTest(sale=clock):
                g = _gains(self._rows("2025-02-03 10:00:00,B,SYN.TO,10,10,100,CAD",
                                      f"2025-03-03 {clock},S,SYN.TO,-5,10,50,CAD",
                                      "2025-03-03 13:00:00,ROC,SYN.TO,,,5,CAD",
                                      mapping=mapping), 2025)
                self.assertAlmostEqual(g["summary"]["total_gain"], gain)
                self.assertEqual([(i["symbol"], i["qty"], round(i["total_cost"], 2))
                                  for i in g["inventory"]], [("SYN.TO", 5.0, left)])

    @rule(R.CA_DATE_14)
    def test_a_trade_that_settles_later_follows_the_return_of_capital(self):
        # The default settlement: the sale stands at its settlement date,
        # after the pay day, whatever its clock.
        for clock in ("09:00:00", "12:00:00"):
            with self.subTest(sale=clock):
                g = _gains(self._rows("2025-02-03 10:00:00,B,SYN.TO,10,10,100,CAD",
                                      f"2025-03-03 {clock},S,SYN.TO,-5,10,50,CAD",
                                      "2025-03-03 13:00:00,ROC,SYN.TO,,,5,CAD"), 2025)
                self.assertAlmostEqual(g["summary"]["total_gain"], 2.5)


class TestSplitFillsAcrossOverlappingExports(unittest.TestCase):
    """TJ-5038: two purchases alike but for their descriptions already
    have two ids. The second was marked a split fill ("second fill [fill
    #2]"), so an overlapping export holding it alone (unmarked there)
    added a third row: 30 shares at 300 for 20 at 200. And two exports
    listing identical rows of a day in opposite orders marked different
    rows: the marks went on before a newest-first export was reversed."""

    HEAD = "Date,Action,Symbol,Qty,Price,Amount,Currency,Note\n"
    MAP = _mapping('amount = "Amount"\ndescription = "Note"')

    @staticmethod
    def _ids(rows):
        import dataclasses
        from taxjson.model.transaction import TaxTransaction as T
        names = {f.name for f in dataclasses.fields(T)}
        return [T(**{k: v for k, v in r.items() if k in names}).id for r in rows]

    def _rows(self, *lines):
        return _parse(self.HEAD + "".join(line + "\n" for line in lines), self.MAP)[0]

    def test_rows_their_descriptions_tell_apart_are_not_marked(self):
        first = "2025-03-03,B,SYN.TO,10,10,100,CAD,first fill"
        second = "2025-03-03,B,SYN.TO,10,10,100,CAD,second fill"
        both, alone = self._rows(first, second), self._rows(second)
        self.assertEqual([r["description"] for r in both], ["first fill", "second fill"])
        ids = self._ids(both)
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(self._ids(alone), ids[1:])

    def test_identical_rows_are_still_marked(self):
        line = "2025-03-03,B,SYN.TO,10,10,100,CAD,fill"
        rows = self._rows(line, line)
        self.assertEqual([r["description"] for r in rows], ["fill", "fill [fill #2]"])
        self.assertEqual(len(set(self._ids(rows))), 2)

    def test_the_marks_follow_the_order_a_newest_first_export_is_read_in(self):
        # Alike in every field of the id, apart in the fee (no part of
        # it; both within the amount's tolerance): the row without a mark
        # is the day's first in both exports.
        head = self.HEAD.replace("Note", "Fee,Note")
        mapping = _mapping('amount = "Amount"\nfee = "Fee"\ndescription = "Note"',
                           'fee_sign = "charged"', "[options]\nallow_large_fees = true")
        days = ["2025-02-03,B,SYN.TO,1,10,10,CAD,0,early",
                "2025-03-03,B,SYN.TO,10,10,100,CAD,0.5,fill",
                "2025-03-03,B,SYN.TO,10,10,100,CAD,0.9,fill",
                "2025-04-01,B,SYN.TO,1,10,10,CAD,0,late"]
        seen = []
        for lines in (days, days[::-1]):
            rows = _parse(head + "".join(line + "\n" for line in lines), mapping)[0]
            seen.append([(r["date"], r["description"], r["fee"]) for r in rows])
        self.assertEqual(seen[0], seen[1])
        self.assertEqual(seen[0][1:3], [("2025-03-03", "fill", 0.5),
                                        ("2025-03-03", "fill [fill #2]", 0.9)])


_FUT_MAP = _mapping('amount = "Amount"\nfee = "Fee"',
                    'fee_sign = "charged"',
                    "[options]\nallow_large_fees = true")


class TestFuturesCashDirection(unittest.TestCase):
    """TJ-0668: a futures row's cash direction is the one its notional
    sign (the price's) and its signed commission allow."""

    def _row(self, price, amount, fee, target="B", qty=1, extra=""):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Fee,Currency\n"
                    + extra +
                    f"2025-01-15,{target},F:SYNH6.US,{qty},{price},{amount},"
                    f"{fee},USD\n")
        rows, _err = _parse(csv_text, _FUT_MAP)
        return rows[-1]

    def test_ordinary_rows_unchanged_and_gross_is_the_notional(self):
        r = self._row(10, 9, -1)          # a rebate of 1 on a notional 10
        self.assertEqual((r["net_amount"], r["gross_amount"]), (9.0, 10.0))
        r = self._row(-10, 9, 1)          # paid 1 on a notional -10
        self.assertEqual((r["net_amount"], r["gross_amount"]), (-9.0, 10.0))
        r = self._row(10, 9, 1, target="S", qty=-1)
        self.assertEqual((r["net_amount"], r["gross_amount"]), (9.0, 10.0))

    def test_crossing_rows_follow_a_cash_signed_file(self):
        # The file signs its cash (a buy paid is negative): the row's own
        # sign decides when the commission crosses the notional.
        signed = "2025-01-10,B,F:SYNM6.US,1,10,-11,1,USD\n"
        r = self._row(1, 1, -2, extra=signed)       # rebate 2 > notional
        self.assertEqual(r["net_amount"], -1.0)
        r = self._row(-1, -1, 2, extra=signed)      # fee 2 > |notional|
        self.assertEqual(r["net_amount"], 1.0)

    def test_crossing_row_with_no_sign_evidence_refused(self):
        with self.assertRaisesRegex(ValueError, "either way"):
            self._row(1, 1, -2)
        with self.assertRaisesRegex(ValueError, "either way"):
            self._row(-1, 1, 2)


_WB_ACCT = "99900001"         # a synthetic broker account id
_WB_PRE = [f"Account Number: {_WB_ACCT}"]
_WB_H = ['Currency', 'Date', 'Action Code', 'Symbol', 'Security Description',
         'Type Code', 'Quantity', 'Price', 'Proceeds']
_WB_OPEN = ['CAD', '2025-07-15', 'SELL', '@SYN', 'CALL SYN07/18/25 5', 'OPC',
            -1, .5, 50]
_WB_CLOSE = ['CAD', '2025-07-18', 'BUY', '@SYN', 'CALL SYN07/18/25 5', 'OPC',
             1, 0, 0]


class TestWebullOverlappingExports(unittest.TestCase):
    """TJ-0855: the same expiry in three overlapping downloads is one
    expiry to the zero-close check, not three buys."""

    def test_three_copies_of_one_expiry_accepted(self):
        from taxjson.lib.brokerages.webull import WebullBrokerage
        for n in (1, 2, 3):
            with tempfile.TemporaryDirectory() as td:
                d = Path(td)
                for i in range(n):
                    rows = ([_WB_PRE, _WB_H]
                            + ([_WB_OPEN] if i == 0 else []) + [_WB_CLOSE])
                    s = io.StringIO()
                    csv.writer(s).writerows(rows)
                    (d / f'webull_{i}.csv').write_text(s.getvalue())
                for i in range(n):
                    with contextlib.redirect_stderr(io.StringIO()):
                        tx = with_siblings(WebullBrokerage()).parse_file(
                            d / f'webull_{i}.csv')
                    self.assertEqual(len(tx), 2 if i == 0 else 1, (n, i))

    def test_a_real_opening_at_zero_still_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        from taxjson.lib.brokerages.webull import WebullBrokerage
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            write = ['CAD', '2025-07-10', 'SELL', '@SYN',
                     'CALL SYN07/18/25 5', 'OPC', -1, 0, 0]
            for i in range(2):
                s = io.StringIO()
                csv.writer(s).writerows(
                    [_WB_PRE, _WB_H, write])
                (d / f'webull_{i}.csv').write_text(s.getvalue())
            with self.assertRaisesRegex(BrokerageParseError,
                                        "opens a position"):
                with contextlib.redirect_stderr(io.StringIO()):
                    with_siblings(WebullBrokerage()).parse_file(
                        d / 'webull_0.csv')


class TestMergeDividendTaxByAccount(unittest.TestCase):
    """TJ-0736: a withholding row pairs with the dividend of its own
    broker account."""

    @staticmethod
    def _row(action, amount, qty, acct):
        return dict(action=action, date='2025-06-02',
                    date_settle='2025-06-02', time='09:30:00',
                    symbol='SYN.US', quantity=qty, currency='USD',
                    price=1 if action == 'DIVIDEND' else .2,
                    net_amount=amount,
                    gross_amount=amount if action == 'DIVIDEND' else 0,
                    account='margin', source_account=acct,
                    description='SYN CASH DIVIDEND', source='ib.csv')

    def test_identified_accounts_reconcile_separately(self):
        from taxjson.bin.taxjson_merge2 import reconcile_dividend_tax
        rows = [self._row('DIVIDEND', 10, 10, 'acct-a'),
                self._row('DIVIDEND', 20, 20, 'acct-b'),
                self._row('TAX', 2, 10, 'acct-a')]
        reconcile_dividend_tax(rows)
        a, b, tax = rows
        self.assertAlmostEqual(a['net_amount'], 8.0)
        self.assertAlmostEqual(b['net_amount'], 20.0)
        self.assertEqual(tax['quantity'], 10)
        self.assertAlmostEqual(tax['price'], 0.2)

    def test_unknown_account_keeps_the_pooled_pairing(self):
        from taxjson.bin.taxjson_merge2 import reconcile_dividend_tax
        rows = [self._row('DIVIDEND', 10, 10, 'acct-a'),
                self._row('DIVIDEND', 20, 20, 'acct-a'),
                self._row('TAX', 3, 0, '')]
        reconcile_dividend_tax(rows)
        self.assertAlmostEqual(rows[0]['net_amount'], 9.0)
        self.assertAlmostEqual(rows[1]['net_amount'], 18.0)
        self.assertEqual(rows[2]['quantity'], 30)

    def test_project_accounts_reconcile_separately(self):
        from taxjson.bin.taxjson_merge2 import reconcile_dividend_tax
        rows = [self._row('DIVIDEND', 10, 10, ''),
                self._row('DIVIDEND', 20, 20, ''),
                self._row('TAX', 2, 10, '')]
        rows[1]['account'] = 'tfsa'
        reconcile_dividend_tax(rows)
        self.assertAlmostEqual(rows[0]['net_amount'], 8.0)
        self.assertAlmostEqual(rows[1]['net_amount'], 20.0)


def _add_cached_value(path: Path, coord: str, value: str) -> None:
    """Give the formula cell `coord` of the first sheet a saved result,
    as a spreadsheet program's save does (openpyxl writes none)."""
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                text = data.decode("utf-8")
                start = text.index(f'r="{coord}"')
                end = text.index("</c>", start)
                # openpyxl writes an empty <v></v>: no saved result.
                body = text[start:end].replace("<v></v>", "")
                text = (text[:start] + body + f"<v>{value}</v>"
                        + text[end:])
                data = text.encode("utf-8")
            zout.writestr(item, data)
    tmp.replace(path)


@unittest.skipUnless(_XLSX, "xlsx extras not installed (openpyxl)")
class TestXlsxUncachedFormula(unittest.TestCase):
    """TJ-0737: a formula cell with no saved result is refused, naming
    the sheet and cell; one with a saved result converts as before."""

    def _book(self, d: Path) -> Path:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Trades"
        ws.append(["Date", "Symbol", "Qty", "Price", "Fee"])
        ws.append(["2025-03-03", "SYN", 10, 10, "=1+1"])
        p = d / "book.xlsx"
        wb.save(p)
        return p

    def test_uncached_formula_refused(self):
        from taxjson.bin.xlsx_to_csv import read_sheet
        with tempfile.TemporaryDirectory() as td:
            p = self._book(Path(td))
            with self.assertRaisesRegex(ValueError, r"'Trades'.*E2"):
                read_sheet(str(p))

    def test_cached_formula_converts(self):
        from taxjson.bin.xlsx_to_csv import read_sheet
        with tempfile.TemporaryDirectory() as td:
            p = self._book(Path(td))
            _add_cached_value(p, "E2", "2")
            sheet = read_sheet(str(p))
            self.assertEqual(sheet.rows[1][4], "2")


if __name__ == "__main__":
    unittest.main()
