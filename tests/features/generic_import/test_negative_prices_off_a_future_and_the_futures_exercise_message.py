"""A price below 0 on anything but a plain future is refused by the run's
row check and by the `.tt` reader alike, a TRANSFER line's too, and the
generic importer names the `.tt` lines that book a futures exercise
(backlog TJ-5261, TJ-5262, TJ-5266).

* TJ-5261: the row check (`validate_transactions`) exempted every
  futures-prefixed symbol from "negative price", so an option on a
  future at a price below 0 passed it; the `.tt` reader refuses the
  same line. The generic importer writes such a row from a CSV that
  states the price. One rule now: only a plain future trades below 0.
* TJ-5262: a `.tt` TRANSFER line at a negative price was read with no
  word (the total checks are a trade line's). Refused at its line, as an
  INKIND line's negative value and an OPENING line's negative cost are.
* TJ-5266: the generic importer told the user to book a futures
  exercise as `.tt` ASSIGN lines; the reader refuses an ASSIGN line of
  the future itself. The message now names the form that books: the
  option's leg an ASSIGN line, the future's leg a BUYSELL line, each
  with an example the reader takes.

No tax rule states any of these (they are what a row may hold), so none
is cited. Every contract and share is made up (F:QZM26.US, QZRX.US).
"""
import contextlib
import io
import re
import tempfile
import unittest
from pathlib import Path

from _tmpfiles import isolate_layouts
from taxjson.lib.brokerages.generic import GenericBrokerage
from taxjson.lib.brokerages.schema import validate_transactions
from taxjson.lib.tt_format import parse_tt_line, tt_to_json

isolate_layouts()

FUT = "F:QZM26.US"
FUT_OPTION = "F:QZF270618C00060000.US"
SHARE = "QZRX.US"
OPTION = "QZRX270219C00045000.US"

TOML = """\
[columns]
date = "Date"
action = "Action"
symbol = "Symbol"
quantity = "Quantity"
price = "Price"
amount = "Amount"
fee = "Fee"
currency = "Currency"

[actions]
"BUY" = "buy"
"SELL" = "sell"
"ASSIGNED IN" = "assign_buy"
"ASSIGNED OUT" = "assign_sell"
"""
HEADER = "Date,Action,Symbol,Quantity,Price,Amount,Fee,Currency\n"


def _row(symbol, qty, price, net, **more):
    return dict({'action': 'BUYSELL', 'date': '2026-04-20',
                 'date_settle': '2026-04-20', 'time': '12:00:00',
                 'symbol': symbol, 'quantity': qty, 'price': price,
                 'net_amount': net, 'currency': 'USD', 'account': 'margin',
                 'id': 'abc'}, **more)


def _read(line, where="hand.tt:7"):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        return parse_tt_line(line, account_name="margin", source=where)


def _generic(rows):
    with tempfile.TemporaryDirectory() as td:
        csv = Path(td) / "activity.csv"
        csv.write_text(HEADER + rows)
        csv.with_name(csv.name + ".toml").write_text(TOML)
        out = io.StringIO()
        with contextlib.redirect_stderr(out), contextlib.redirect_stdout(out):
            return GenericBrokerage().parse_file(csv)


class TestTheRowCheckAtANegativePrice(unittest.TestCase):
    """TJ-5261."""

    def test_an_option_on_a_future_is_an_error_naming_the_row(self):
        # Base: ([], []) for each.
        for qty, net in ((-1.0, 100.0), (-1.0, -100.0), (1.0, 100.0)):
            with self.subTest(qty=qty, net=net):
                errors, _ = validate_transactions(
                    [_row(FUT_OPTION, qty, -10.0, net, multiplier=10.0)])
                self.assertIn(
                    f"tx[0] BUYSELL {FUT_OPTION} 2026-04-20: negative price "
                    f"-10.0: only a futures contract (an F: or / symbol, not "
                    f"an option on one) trades below 0 — correct the row in "
                    f"the export, or enter it as a .tt line and delete the "
                    f"row.", errors)

    def test_the_row_is_named_by_its_file_and_line(self):
        errors, _ = validate_transactions([_row(
            FUT_OPTION, -1.0, -10.0, 100.0, source='activity.csv', line=4)])
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith(
            f"activity.csv line 4: BUYSELL {FUT_OPTION} 2026-04-20: negative "
            f"price -10.0"), errors[0])

    def test_every_futures_prefix_of_an_option(self):
        for sym in ("/QZF270618C00060000", "F:QZF270618P00060000.US"):
            with self.subTest(sym=sym):
                errors, _ = validate_transactions(
                    [_row(sym, -1.0, -10.0, 100.0, multiplier=10.0)])
                self.assertTrue(any("negative price" in e for e in errors),
                                errors)

    def test_a_plain_future_still_trades_below_zero(self):
        # (a control: a buy at a negative price receives money)
        for sym in (FUT, "/QZM26"):
            with self.subTest(sym=sym):
                self.assertEqual(validate_transactions(
                    [_row(sym, 1.0, -10.0, -100.0, multiplier=10.0),
                     _row(sym, -1.0, -10.0, -100.0, multiplier=10.0)]),
                    ([], []))

    def test_a_share_and_an_equity_option_are_errors_as_before(self):
        for sym in (SHARE, OPTION):
            with self.subTest(sym=sym):
                errors, _ = validate_transactions([_row(sym, -1.0, -10.0, 10.0)])
                self.assertEqual(len(errors), 1)
                self.assertIn(f"tx[0] BUYSELL {sym} 2026-04-20: negative price "
                              f"-10.0", errors[0])

    def test_an_option_on_a_future_at_a_price_above_zero_passes(self):
        self.assertEqual(validate_transactions(
            [_row(FUT_OPTION, -1.0, 10.0, 100.0, multiplier=10.0)]), ([], []))

    def test_a_price_within_float_noise_of_zero_is_zero(self):
        # A computed price of -1e-13 is 0, for the row check and the
        # reader alike; -0.004 is a negative price for both.
        for sym in (SHARE, OPTION, FUT_OPTION):
            with self.subTest(sym=sym):
                self.assertEqual(validate_transactions(
                    [_row(sym, 100.0, -1e-13, 0.0), _row(sym, 100.0, -0.0, 0.0)]),
                    ([], []))
                errors, _ = validate_transactions([_row(sym, 100.0, -0.004, 0.4)])
                self.assertTrue(any("negative price -0.004" in e for e in errors),
                                errors)
        tx = _read(f"BUYSELL 2026-04-20 12:00:00 {SHARE} 100 USD -0.0000000000001 0 0")
        self.assertEqual(tx['quantity'], 100.0)
        self.assertEqual(_read(f"TRANSFER 2026-04-20 12:00:00 {SHARE} 10 USD "
                               f"-0.0000000000001 0")['action'], 'TRANSFER')
        for line in (f"BUYSELL 2026-04-20 12:00:00 {SHARE} 100 USD -0.004 0.4 0",
                     f"TRANSFER 2026-04-20 12:00:00 {SHARE} 100 USD -0.004 0.4"):
            with self.subTest(line=line), self.assertRaises(ValueError) as cm:
                _read(line)
            self.assertIn("price -0.004 is negative", str(cm.exception))

    def test_the_reader_and_the_row_check_agree(self):
        # One rule: what the reader refuses at a price below 0, the row
        # check refuses; what it reads, the row check passes.
        cases = ((SHARE, "-1 USD -10 10 0"), (OPTION, "-1 USD -1 100 0"),
                 (FUT_OPTION, "-1 USD -10 100 0 x10"),
                 (FUT, "-1 USD -10 -100 0 x10"), (FUT, "1 USD -10 -100 0 x10"))
        for sym, rest in cases:
            with self.subTest(sym=sym, rest=rest):
                line = f"BUYSELL 2026-04-20 12:00:00 {sym} {rest}"
                q, _cur, price, net = rest.split()[:4]
                row = _row(sym, float(q), float(price), float(net),
                           multiplier=10.0 if 'x10' in rest else None)
                errors, _ = validate_transactions([row])
                try:
                    _read(line)
                    refused = False
                except ValueError:
                    refused = True
                self.assertEqual(refused, any("negative price" in e
                                              for e in errors), errors)

    def test_the_generic_importers_row_of_one_is_refused_by_the_check(self):
        # The importer writes the price a CSV states for a futures-prefixed
        # symbol. Base: the option row passes the check and is booked.
        got = _generic("2026-04-20,SELL,F:QZF270618C00060000,1,-10.00,"
                       "100.00,,USD\n")
        self.assertEqual([(g['symbol'], g['price']) for g in got],
                         [(FUT_OPTION, -10.0)])
        errors, _ = validate_transactions(got)
        self.assertTrue(any("negative price -10.0: only a futures contract"
                            in e for e in errors), errors)
        # (a control: the future itself)
        got = _generic("2026-04-20,BUY,F:QZM26,1,-10.00,100.00,,USD\n")
        self.assertEqual(validate_transactions(got), ([], []))


class TestATransferLineAtANegativePrice(unittest.TestCase):
    """TJ-5262."""

    LINE = "TRANSFER 2026-04-20 12:00:00 QZRX.US 10 USD -10 100"

    def test_it_is_refused_at_its_line(self):
        # Base: read (quantity 10, price -10, total 100), and the row
        # check passes the row.
        for line in (self.LINE, self.LINE.replace(" 10 USD", " -10 USD"),
                     self.LINE + " DECLARED", self.LINE.replace(" 100", " -100"),
                     self.LINE.replace("QZRX.US", OPTION),
                     self.LINE.replace("QZRX.US", FUT_OPTION)):
            with self.subTest(line=line), self.assertRaises(ValueError) as cm:
                _read(line)
            text = str(cm.exception)
            self.assertTrue(text.startswith(
                "hand.tt:7: TRANSFER price -10 is negative: a transfer "
                "line's price is the value of one unit moved, never below "
                "0"), text)
            self.assertIn("Write the price above 0, or 0 when the value is "
                          "unknown.", text)
            self.assertIn(f"Line: {line!r}", text)

    def test_the_file_reader_names_the_file_and_line(self):
        with tempfile.TemporaryDirectory() as td:
            tt = Path(td) / "moves.tt"
            tt.write_text("# a move in\n" + self.LINE + "\n")
            with self.assertRaises(ValueError) as cm, \
                    contextlib.redirect_stderr(io.StringIO()):
                tt_to_json(tt, "margin", equity=True)
        self.assertTrue(str(cm.exception).startswith(
            "moves.tt:2: TRANSFER price -10 is negative"), str(cm.exception))

    def test_a_plain_futures_line_reads_as_its_buysell_line_does(self):
        # A future trades below 0: its TRANSFER line is read, as its
        # BUYSELL line at that price is.
        tx = _read(self.LINE.replace("QZRX.US", FUT))
        self.assertEqual((tx['symbol'], tx['price']), (FUT, -10.0))
        self.assertEqual(_read(f"BUYSELL 2026-04-20 12:00:00 {FUT} 1 USD -10 -100 0 "
                               f"x10")['price'], -10.0)

    def test_what_is_read_as_before(self):
        for rest, price, net in (("10 USD 10 100", 10.0, 100.0),
                                 ("10 USD 0 0", 0.0, 0.0),
                                 ("-10 USD 0.0 0.0 DECLARED", 0.0, 0.0),
                                 # (a total's sign is the row's, as the
                                 # writer writes it: not this refusal's)
                                 ("-10 USD 10 -100", 10.0, -100.0)):
            with self.subTest(rest=rest):
                tx = _read(f"TRANSFER 2026-04-20 12:00:00 QZRX.US {rest}")
                self.assertEqual((tx['action'], tx['price'], tx['net_amount']),
                                 ('TRANSFER', price, net))


class TestTheFuturesExerciseMessage(unittest.TestCase):
    """TJ-5266."""

    def refused(self, rows):
        with self.assertRaises(ValueError) as cm:
            _generic(rows)
        return str(cm.exception)

    def test_it_names_the_form_that_books_each_leg(self):
        # Base: "... names the futures contract F:QZM26.US: a futures
        # exercise or assignment is not read from a mapping; book both
        # legs as .tt ASSIGN lines."
        text = self.refused("2026-06-18,ASSIGNED IN,F:QZM26,1,60,-600,,USD\n")
        self.assertTrue(text.startswith(
            "generic importer: activity.csv line 2: ASSIGNED IN (-> "
            f"assign_buy) names the futures contract {FUT}: a futures "
            "exercise or assignment is not read from a mapping."), text)
        self.assertIn("the option's leg as an ASSIGN line", text)
        self.assertIn("the future's leg as a BUYSELL line", text)
        self.assertIn(f"`BUYSELL 2025-06-18 16:00:00 {FUT} 1 USD 60 600 0 x10`",
                      text)
        self.assertIn("An ASSIGN line of the future itself is refused", text)
        self.assertNotIn("both legs as .tt ASSIGN lines", text)

    def test_an_option_on_a_future_is_named_as_one(self):
        text = self.refused(
            "2026-06-18,ASSIGNED IN,F:QZF270618C00060000,1,0,0,,USD\n")
        self.assertIn(f"names the option on a future {FUT_OPTION}:", text)
        self.assertIn(f"`ASSIGN 2025-06-18 16:00:00 {FUT_OPTION} -1 USD 0 0`",
                      text)

    def test_the_reader_takes_both_examples(self):
        # The lines the message shows are lines the reader reads; the
        # form the old message named, an ASSIGN line of the future, is
        # one it refuses.
        for row, names in (
                ("2026-06-18,ASSIGNED IN,F:QZM26,1,60,-600,,USD\n",
                 {'OPTION': FUT_OPTION}),
                ("2026-06-18,ASSIGNED IN,F:QZF270618C00060000,1,0,0,,USD\n",
                 {'FUTURE': FUT})):
            lines = re.findall(r"`((?:ASSIGN|BUYSELL) [^`]+)`", self.refused(row))
            self.assertEqual([ln.split()[0] for ln in lines],
                             ['ASSIGN', 'BUYSELL'])
            for line in lines:
                for word, sym in names.items():
                    line = line.replace(f" {word} ", f" {sym} ")
                with self.subTest(line=line):
                    tx = _read(line)
                    self.assertEqual(tx['action'], line.split()[0])
            with self.assertRaises(ValueError):
                _read(lines[1].replace("BUYSELL", "ASSIGN", 1).replace(
                    " FUTURE ", f" {FUT} "))


if __name__ == "__main__":
    unittest.main()
