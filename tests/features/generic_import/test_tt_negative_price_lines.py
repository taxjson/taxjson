"""A `.tt` trade line at a price below 0 is read against its total or
refused, whatever it names (backlog TJ-5031).

A plain future's BUYSELL line at a negative price has been read against
its own total since TJ-5162 (its size on the line, the total qty x price
x size +/- fee with its sign; the rest refused). What the total checks
still stood down on, at a price below 0:

* an ASSIGN line of a future (the contract an option on it delivers):
  the reader took it, at any price and with any total, and the run
  stopped later at the futures settlement ("only BUYSELL fills of a
  futures contract can be booked"), which names the row by its id and
  no file or line. The reader now refuses it at its line with that
  reason;
* a share, an option, an option on a future: nothing trades them below
  0, the line was read with no word, and the run's row check refused a
  share's later, naming no line (an option on a future passed it);
* a cancellation of a buy at a negative price, whose negative total was
  accepted or refused by the rule for a commission larger than the gross.

Every contract and share is made up (F:QZM26.US, QZRX.US).
"""
import contextlib
import io
import unittest

from taxjson.lib import tt_format
from taxjson.lib.tt_format import parse_tt_line

FUT = "F:QZM26.US"
FUT_OPTION = "F:QZF270618C00060000.US"
SHARE = "QZRX.US"
OPTION = "QZRX270219C00045000.US"


def _parse(line, where="hand.tt:7"):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        tx = parse_tt_line(line, account_name="margin", source=where)
    return tx, err.getvalue()


def _line(action, symbol, rest):
    return f"{action} 2026-04-20 12:00:00 {symbol} {rest}"


def _refusal(case, line):
    with case.assertRaises(ValueError) as cm:
        _parse(line)
    text = str(cm.exception)
    case.assertTrue(text.startswith("hand.tt:7: "), text)
    case.assertIn(line, text)
    return text


class TestAnAssignLineOfAFuture(unittest.TestCase):
    """The settlement books BUYSELL fills of a future only: an ASSIGN
    line of one is refused where it is written, with that reason. Base:
    the reader takes each of these lines, and `tjs run` stops at the
    settlement naming an id."""

    def _refused(self, rest):
        text = _refusal(self, _line("ASSIGN", FUT, rest))
        self.assertIn(f"ASSIGN line of the futures contract {FUT}", text)
        self.assertIn("only BUYSELL fills of a futures contract can be "
                      "booked (on their settled P/L)", text)
        self.assertIn("Write the line as BUYSELL", text)
        # Not a remedy for the total, which the run would then refuse.
        self.assertNotIn("its total is qty x price", text)
        self.assertNotIn("add it after the fee", text)

    def test_at_a_negative_price_whatever_the_total(self):
        for rest in ("-1 USD -10 100 0 x10", "-1 USD -10 -100 0 x10",
                     "1 USD -10 100 0 x10", "1 USD -10 -100 0 x10",
                     "-1 USD -10 -100 0"):
            with self.subTest(rest=rest):
                self._refused(rest)

    def test_at_a_price_above_zero_too(self):
        for rest in ("1 USD 10 100 0 x10", "-1 USD 15 150 0 x10",
                     "1 USD 0 0 0"):
            with self.subTest(rest=rest):
                self._refused(rest)

    def test_the_same_fill_written_as_buysell_is_read(self):
        tx, err = _parse(_line("BUYSELL", FUT, "1 USD -10 -100 0 x10"))
        self.assertEqual((tx["quantity"], tx["net_amount"]), (1.0, -100.0))
        tx, _ = _parse(_line("BUYSELL", FUT, "1 USD 10 100 0 x10"))
        self.assertEqual(tx["net_amount"], 100.0)
        self.assertEqual(err, "")

    def test_an_option_on_a_future_keeps_its_assign_line(self):
        tx, _ = _parse(_line("ASSIGN", FUT_OPTION, "-1 USD 0 0 0"))
        self.assertEqual((tx["action"], tx["quantity"]), ("ASSIGN", -1.0))


class TestAPriceBelowZeroOnAnythingElse(unittest.TestCase):
    """Only a future trades below 0: the line is refused where it is."""

    def test_a_share_line_is_refused_naming_the_line(self):
        # Base: read with no word (the run's row check refused it
        # later: "tx[0] BUYSELL QZRX.US 2026-04-20: negative price").
        for rest in ("-1 USD -10 10 0", "1 USD -10 10 0",
                     "-1 USD -10 -10 0", "1 USD -10 -10 0"):
            with self.subTest(rest=rest):
                text = _refusal(self, _line("BUYSELL", SHARE, rest))
                self.assertIn("BUYSELL price -10 is negative", text)
                self.assertIn("only a futures contract", text)
                self.assertIn("Write the price above 0", text)

    def test_an_option_line_is_refused(self):
        text = _refusal(self, _line("BUYSELL", OPTION, "-1 USD -1 100 0"))
        self.assertIn("BUYSELL price -1 is negative", text)

    def test_an_option_on_a_future_is_refused(self):
        # Base: booked as written, sale or buy, either sign of total;
        # the run's row check lets a futures symbol's price through.
        for rest in ("-1 USD -10 100 0 x10", "-1 USD -10 -100 0 x10",
                     "1 USD -10 100 0 x10"):
            with self.subTest(rest=rest):
                text = _refusal(self, _line("BUYSELL", FUT_OPTION, rest))
                self.assertIn("not an option on one", text)

    def test_an_assign_line_of_shares_is_refused(self):
        text = _refusal(self, _line("ASSIGN", SHARE, "100 USD -10 1000 0"))
        self.assertIn("ASSIGN price -10 is negative", text)

    def test_a_typed_line_is_refused_too(self):
        text = _refusal(self, _line("BUYSELL", SHARE,
                                    "-1 USD -10 10 0 type=trade_cancel"))
        self.assertIn("BUYSELL price -10 is negative", text)


class TestANegativeNotionalIsNotACommission(unittest.TestCase):

    def test_the_commission_rule_does_not_answer_for_a_negative_gross(self):
        # Base: True, by the arithmetic (0 > -100 and the total fits).
        tx, _ = _parse(_line("BUYSELL", FUT, "-1 USD -10 -100 0 x10"))
        self.assertFalse(tt_format._excess_commission_sale(tx))

    def test_a_cancellation_with_another_total_is_told_the_notional(self):
        # The cancellation of a buy at a negative price sells at it.
        # Base: "sell total -250 is negative ... A negative total is
        # read only when the line's commission exceeds qty x price".
        text = _refusal(self, _line(
            "BUYSELL", FUT, "-1 USD -10 -250 0 x10 type=trade_cancel"))
        self.assertIn("futures sell at the negative price -10", text)
        self.assertIn("-100.00 here; the line's total is -250", text)
        self.assertNotIn("commission exceeds", text)

    def test_a_cancellation_with_no_size_is_told_the_size(self):
        text = _refusal(self, _line(
            "BUYSELL", FUT, "-1 USD -10 -100 0 type=trade_cancel"))
        self.assertIn("The line has no contract size", text)
        self.assertNotIn("commission exceeds", text)


class TestWhatDoesNotChange(unittest.TestCase):

    def test_a_futures_fill_at_a_negative_price(self):
        tx, _ = _parse(_line("BUYSELL", FUT, "1 USD -10 -100 0 x10"))
        self.assertEqual(tx["net_amount"], -100.0)
        tx, _ = _parse(_line("BUYSELL", FUT, "-1 USD -10 -102.5 2.5 x10"))
        self.assertEqual(tx["net_amount"], -102.5)
        text = _refusal(self, _line("BUYSELL", FUT, "-1 USD -10 100 0 x10"))
        self.assertIn("futures sell at the negative price -10", text)
        text = _refusal(self, _line("BUYSELL", FUT, "-1 USD -10 -100 0"))
        self.assertIn("The line has no contract size", text)

    def test_a_cancellation_and_a_settled_line_at_a_negative_price(self):
        tx, _ = _parse(_line("BUYSELL", FUT,
                             "-1 USD -5 -5000 0 x1000 type=trade_cancel"))
        self.assertEqual(tx["net_amount"], -5000.0)
        # A cancellation of a SALE at a negative price buys: its total
        # is not read against the price, as before.
        tx, _ = _parse(_line("BUYSELL", FUT,
                             "1 USD -5 -5000 0 x1000 type=trade_cancel"))
        self.assertEqual(tx["net_amount"], -5000.0)
        tx, _ = _parse(_line("BUYSELL", FUT,
                             "-1 USD -5 250 0 type=futures_settlement"))
        self.assertEqual(tx["net_amount"], 250.0)

    def test_a_commission_larger_than_the_gross(self):
        tx, _ = _parse(_line("BUYSELL", OPTION, "-1 USD 0.01 -0.25 1.25"))
        self.assertEqual(tx["net_amount"], -0.25)
        self.assertTrue(tt_format._excess_commission_sale(tx))
        tx, _ = _parse(_line("BUYSELL", FUT, "-1 USD 0.1 -1.5 2.5 x10"))
        self.assertEqual(tx["net_amount"], -1.5)
        text = _refusal(self, _line("BUYSELL", SHARE, "-10 USD 5 -50 0"))
        self.assertIn("sell total -50 is negative", text)
        self.assertIn("commission exceeds", text)

    def test_an_assign_line_of_shares(self):
        tx, _ = _parse(_line("ASSIGN", SHARE, "100 USD 10 1000 0"))
        self.assertEqual((tx["action"], tx["net_amount"]),
                         ("ASSIGN", 1000.0))


if __name__ == "__main__":
    unittest.main()
