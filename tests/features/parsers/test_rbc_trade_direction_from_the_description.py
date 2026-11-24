"""RBC: a trade row's direction, where the Activity does not give it
(backlog TJ-2383, TJ-5021).

A row whose Activity this parser does not know (`Trade`) is a trade when
its Description has the word Buy or Sell. Its sign was then read from
the Quantity alone, and RBC prints a sale's Quantity unsigned as often
as signed: "NAME - Sell" with 10 was a purchase, refused one step later
as a shifted column. The Description's own direction now signs such a
row; a Description with both words, a Buy with a negative Quantity, and
an Activity of Buy or Sell whose Description ends with the other verb
are refused, naming the line.

Whatever gave the direction (the Activity or the Description), it must
agree with the sign of the row's Value, the cash before commission: a
sale's is cash in, a purchase's cash out. Where the Description gave the
direction, a row whose direction and Value disagree is refused either
way, naming the line and both readings. Under a Buy or Sell Activity the
Activity gives the side and only a sale with a negative Value is refused
(it was booked as a sale with negative proceeds); a purchase whose cash
is written positive books by its magnitude, as before. The Value, not
the net Amount: a sale whose commission is larger than its proceeds
still books.

Every security is made up (QZRX); the account is none.
"""
import contextlib
import io
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages import rbc_direct
from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
from _tmpfiles import private_dir

HEAD = ("Date,Activity,Symbol,Description,Quantity,Price,Settlement Date,"
        "Currency,Value,Amount\n")
BUY = "01/12/2024,Buy,QZRX,QZRX CORP - Buy,10,9.00,01/15/2024,CAD,-90.00,-94.95\n"


def _sale(activity, desc, qty="10"):
    return (f"02/12/2024,{activity},QZRX,{desc},{qty},9.00,02/14/2024,CAD,"
            f"90.00,80.05\n")


def _purchase(activity, desc, qty="10"):
    return (f"02/12/2024,{activity},QZRX,{desc},{qty},9.00,02/14/2024,CAD,"
            f"-90.00,-94.95\n")


def _parse(*rows):
    path = Path(private_dir()) / "rbc_2024.csv"
    path.write_text(HEAD + BUY + "".join(rows), encoding="utf-8")
    sink = io.StringIO()
    with contextlib.redirect_stderr(sink), contextlib.redirect_stdout(sink):
        return RbcBrokerage().parse_file(path)[1:]


def _facts(txs):
    return [(t["action"], t["quantity"], t["net_amount"], t["fee"])
            for t in txs]


class TestAnUnknownActivityIsSignedByItsDescription(unittest.TestCase):

    def test_a_sell_description_with_an_unsigned_quantity_is_a_sale(self):
        # Base: "Value 80.05 does not fit |Quantity| 10 x Price 9".
        self.assertEqual(_facts(_parse(_sale("Trade", "QZRX CORP - Sell"))),
                         [("BUYSELL", -10.0, 80.05, 9.95)])

    def test_it_is_the_sale_a_sell_row_books(self):
        by_word = _parse(_sale("Trade", "QZRX CORP - Sell"))
        by_activity = _parse(_sale("Sell", "QZRX CORP - Sell"))
        self.assertEqual(_facts(by_word), _facts(by_activity))
        self.assertEqual(by_word[0]["date_settle"],
                         by_activity[0]["date_settle"])

    def test_a_signed_quantity_reads_the_same(self):
        self.assertEqual(
            _facts(_parse(_sale("Trade", "QZRX CORP - Sell", qty="-10"))),
            [("BUYSELL", -10.0, 80.05, 9.95)])

    def test_a_buy_description_is_a_purchase(self):
        self.assertEqual(
            _facts(_parse(_purchase("Trade", "QZRX CORP - Buy"))),
            [("BUYSELL", 10.0, 94.95, 4.95)])

    def test_a_buy_description_with_a_negative_quantity_is_refused(self):
        # Base: booked as a sale of 10, the Description never read. A
        # sale's cash beside it: refused for the Value first.
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(_sale("Trade", "QZRX CORP - Buy", qty="-10"))
        text = str(cm.exception)
        self.assertIn("rbc_2024.csv:3:", text)
        self.assertIn('the Description ends "- Buy"', text)
        self.assertIn("a purchase, but the Value 90.00 is cash received, "
                      "a sale", text)
        # A purchase's cash beside it: refused for the Quantity, the
        # message naming what in the Description was read.
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(_purchase("Trade", "QZRX CORP - Buy", qty="-10"))
        text = str(cm.exception)
        self.assertIn("rbc_2024.csv:3:", text)
        self.assertIn('a row read as a purchase (the Description ends '
                      '"- Buy"; the Activity \'Trade\' is not one this '
                      'parser knows) has a NEGATIVE Quantity -10', text)
        self.assertIn("Set the Activity cell to Buy or Sell", text)

    def test_a_word_of_the_name_is_named_as_a_word(self):
        # The message does not call a word of the security's name a
        # verb: it says which was read.
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(_purchase("Trade", "QZRX Buy CORP", qty="-10"))
        text = str(cm.exception)
        self.assertIn('the Description has the word "Buy"', text)
        self.assertNotIn("says purchase", text)

    def test_a_description_with_both_words_is_refused(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(_sale("Trade", "QZRX Buy Sell CORP"))
        text = str(cm.exception)
        self.assertIn("rbc_2024.csv:3:", text)
        self.assertIn("Activity 'Trade' is not one this parser knows", text)
        self.assertIn('both "Buy" and "Sell"', text)
        self.assertIn("Set the Activity cell to Buy or Sell", text)

    def test_the_closing_verb_decides_over_a_word_in_the_name(self):
        self.assertEqual(rbc_direct.rbc_desc_side("QZRX Buy CORP - Sell"), "sell")
        self.assertEqual(
            _facts(_parse(_sale("Trade", "QZRX Buy CORP - Sell"))),
            [("BUYSELL", -10.0, 80.05, 9.95)])


class TestAnActivityAndADescriptionThatContradict(unittest.TestCase):

    def test_a_sell_whose_description_ends_buy_is_refused(self):
        # Base: a sale of 10, by the Activity alone.
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(_sale("Sell", "QZRX CORP - Buy"))
        text = str(cm.exception)
        self.assertIn("rbc_2024.csv:3:", text)
        self.assertIn("Activity 'Sell' but the Description ends \"- Buy\"",
                      text)
        self.assertIn("Correct the cell that is wrong", text)

    def test_a_buy_whose_description_ends_sell_is_refused(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(_purchase("Buy", "QZRX CORP - Sell"))
        text = str(cm.exception)
        self.assertIn("rbc_2024.csv:3:", text)
        self.assertIn("Activity 'Buy' but the Description ends \"- Sell\"",
                      text)

    def test_a_name_ending_in_a_hyphenated_buy_is_a_name(self):
        # "E-Buy" closes the name, not the row: the verb needs its
        # separator (" - Buy").
        self.assertEqual(rbc_direct.rbc_desc_side("QZRX CORP - Buy"), "buy")
        self.assertEqual(
            rbc_direct._TRADE_DESC_VERB_RE.search("QZRX E-Buy"), None)
        self.assertEqual(
            _facts(_parse(_sale("Sell", "QZRX E-Buy", qty="-10"))),
            [("BUYSELL", -10.0, 80.05, 9.95)])

    def test_a_word_inside_the_name_contradicts_nothing(self):
        # Only the verb the export ends the Description with is read
        # against a known Activity, never a word of the security's name.
        self.assertEqual(
            _facts(_parse(_sale("Sell", "QZRX Buy CORP"))),
            [("BUYSELL", -10.0, 80.05, 9.95)])


def _row(activity, desc, qty, price, value, amount):
    return (f"02/12/2024,{activity},QZRX,{desc},{qty},{price},02/14/2024,"
            f"CAD,{value},{amount}\n")


class TestTheDirectionAgainstTheValuesSign(unittest.TestCase):
    """A sale's Value is cash in, a purchase's cash out, whatever gave
    the direction."""

    def _refused(self, row, *phrases):
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(row)
        text = str(cm.exception)
        self.assertIn("rbc_2024.csv:3:", text)
        for phrase in phrases:
            self.assertIn(phrase, text)
        self.assertIn("a sale's cash comes in and a purchase's goes out",
                      text)
        self.assertIn("Correct the cell that is wrong from the statement",
                      text)
        self.assertIn(".tt file", text)

    def test_a_sell_row_with_cash_paid_out_is_refused(self):
        # Base (and the first fix): a sale of 10 with proceeds of -94.95.
        self._refused(_row("Sell", "QZRX CORP - Sell", 10, "9.00",
                           "-90.00", "-94.95"),
                      "Activity 'Sell': a sale, but the Value -90.00 is "
                      "cash paid out, a purchase")

    def test_a_buy_row_with_its_cash_written_unsigned_books(self):
        # The Activity gives the side: Value and Amount both positive
        # and of a purchase's size (the cost with its commission) are
        # the purchase by their magnitude, as on base. The right figure.
        txs = _parse(_row("Buy", "QZRX CORP - Buy", 10, "9.00", "90.00",
                          "94.95"))
        self.assertEqual(_facts(txs), [("BUYSELL", 10.0, 94.95, 4.95)])

    def test_a_buy_row_with_a_sales_cash_is_refused_by_the_money(self):
        # A positive net below the gross is no purchase's cash: refused
        # by the money check, as on base.
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(_row("Buy", "QZRX CORP - Buy", 10, "9.00", "90.00",
                        "80.05"))
        self.assertIn("rbc_2024.csv:3: Value 80.05 does not fit",
                      str(cm.exception))

    def test_a_sale_by_the_description_with_cash_paid_out_is_refused(self):
        # The first fix booked each as a sale with negative proceeds: a
        # purchase whose name holds the word, the closing verb too.
        self._refused(_row("Trade", "QZRX Sell CORP", 10, "9.00", "-90.00",
                           "-94.95"),
                      'the Description has the word "Sell" (the Activity '
                      "'Trade' is not one this parser knows): a sale, but "
                      "the Value -90.00 is cash paid out, a purchase")
        self._refused(_row("Trade", "QZRX CORP - Sell", 10, "9.00",
                           "-90.00", "-94.95"),
                      'the Description ends "- Sell"')

    def test_a_purchase_by_the_description_with_cash_received(self):
        self._refused(_row("Trade", "QZRX CORP - Buy", 10, "9.00", "90.00",
                           "80.05"),
                      'the Description ends "- Buy"',
                      "a purchase, but the Value 90.00 is cash received, "
                      "a sale")

    def test_a_sale_whose_commission_exceeds_its_proceeds_books(self):
        # Value +0.50 (cash in before commission), Amount -9.45: the
        # Value is what is read, so the sale books with its negative
        # proceeds, under the Activity and under the Description.
        for activity in ("Sell", "Trade"):
            with self.subTest(activity=activity):
                txs = _parse(_row(activity, "QZRX CORP - Sell", 10, "0.05",
                                  "0.50", "-9.45"))
                self.assertEqual(_facts(txs),
                                 [("BUYSELL", -10.0, -9.45, 9.95)])

    def test_the_text_and_the_row_name_the_same_columns(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(_row("Sell", "QZRX CORP - Sell", 10, "9.00", "-90.00",
                        "-94.95"))
        text = str(cm.exception)
        self.assertIn("the Value -90.00 is cash paid out", text)
        self.assertIn("qty=10 Value=-90 Amount=-94.95 CAD", text)
        self.assertNotIn("value=", text)

    def test_a_zero_value_states_no_direction(self):
        txs = _parse(_row("Trade", "QZRX CORP - Sell", 10, "0", "0", "0"))
        self.assertEqual(_facts(txs)[0][:3], ("BUYSELL", -10.0, 0.0))


class TestAnExportWithOneMoneyColumn(unittest.TestCase):
    """No Value beside the net: the same rule on the one column. A sale
    that pays is an excess commission only while what it pays is less
    than |Quantity| x Price."""

    HEAD1 = HEAD.replace(",Value,Amount", ",Value")
    BUY1 = BUY.replace(",-90.00,-94.95", ",-94.95")

    def _parse1(self, activity, desc, qty, price, cash):
        path = Path(private_dir()) / "rbc_2024.csv"
        path.write_text(
            self.HEAD1 + self.BUY1
            + f"02/12/2024,{activity},QZRX,{desc},{qty},{price},"
              f"02/14/2024,CAD,{cash}\n", encoding="utf-8")
        sink = io.StringIO()
        with contextlib.redirect_stderr(sink), \
                contextlib.redirect_stdout(sink):
            return RbcBrokerage().parse_file(path)[1:]

    def _refused(self, *row):
        with self.assertRaises(BrokerageParseError) as cm:
            self._parse1(*row)
        text = str(cm.exception)
        self.assertIn("rbc_2024.csv:3:", text)
        self.assertIn("a sale's cash comes in and a purchase's goes out",
                      text)
        return text

    def test_a_sale_by_the_description_that_pays_a_purchases_cash(self):
        # The reviewer's c and c3. Base, and the tree until now: a sale
        # of 10 with proceeds of -94.95.
        text = self._refused("Trade", "QZRX Sell CORP", 10, "9.00",
                             "-94.95")
        self.assertIn('the Description has the word "Sell"', text)
        self.assertIn("a sale, but the cash -94.95 is cash paid out, a "
                      "purchase", text)
        self.assertIn("qty=10 cash=-94.95 CAD", text)
        self.assertNotIn("Value", text)
        text = self._refused("Trade", "QZRX CORP - Sell", 10, "9.00",
                             "-94.95")
        self.assertIn('the Description ends "- Sell"', text)

    def test_under_a_sell_activity_the_activity_rules(self):
        # One column cannot tell a purchase's cash from a commission
        # larger than the proceeds, and a penny option close pays more
        # than its |Quantity| x Price: 1 contract at 0.01 is 1.00 of
        # proceeds, a commission of 10.95, the net -9.95
        # (tests/test_rbc_2022_export.py pins that row). So a Sell row
        # is read as before, its commission bounded by the money check.
        txs = self._parse1("Sell", "QZRX CORP - Sell", -10, "0.10",
                           "-8.95")
        self.assertEqual(_facts(txs), [("BUYSELL", -10.0, -8.95, 9.95)])
        txs = self._parse1("Sell", "QZRX CORP - Sell", 10, "9.00",
                           "-94.95")
        self.assertEqual(_facts(txs)[0][:3], ("BUYSELL", -10.0, -94.95))

    def test_under_the_description_a_larger_payment_is_refused(self):
        # The same penny row with its side read from the Description:
        # refused, and the remedy (the Activity cell) books it.
        text = self._refused("Trade", "QZRX CORP - Sell", 10, "0.10",
                             "-8.95")
        self.assertIn("Correct the cell that is wrong from the statement "
                      "(the Activity: Buy or Sell", text)

    def test_an_excess_commission_books(self):
        # 10 x 0.50 = 5.00 of proceeds, a commission of 9.95: -4.95.
        for activity, qty in (("Sell", -10), ("Trade", 10)):
            with self.subTest(activity=activity):
                txs = self._parse1(activity, "QZRX CORP - Sell", qty,
                                   "0.50", "-4.95")
                self.assertEqual(_facts(txs),
                                 [("BUYSELL", -10.0, -4.95, 9.95)])

    def test_a_purchase_by_the_description_with_cash_received(self):
        # Whether or not the amount fits a purchase: the Description
        # gave the side, and nothing else says the cash is unsigned.
        for cash in ("80.05", "94.95"):
            with self.subTest(cash=cash):
                text = self._refused("Trade", "QZRX CORP - Buy", 10,
                                     "9.00", cash)
                self.assertIn(f"a purchase, but the cash {cash} is cash "
                              f"received, a sale", text)

    def test_a_buy_row_with_its_cash_written_unsigned_books(self):
        self.assertEqual(
            _facts(self._parse1("Buy", "QZRX CORP - Buy", 10, "9.00",
                                "94.95")),
            [("BUYSELL", 10.0, 94.95, 4.95)])

    def test_a_sale_with_no_price_is_left_to_the_money_check(self):
        # Nothing to weigh the cash against: read as before.
        txs = self._parse1("Sell", "QZRX CORP - Sell", -10, "0", "-9.95")
        self.assertEqual(_facts(txs)[0][:3], ("BUYSELL", -10.0, -9.95))

    def test_ordinary_rows(self):
        self.assertEqual(
            _facts(self._parse1("Sell", "QZRX CORP - Sell", -10, "9.00",
                                "80.05")),
            [("BUYSELL", -10.0, 80.05, 9.95)])
        self.assertEqual(
            _facts(self._parse1("Trade", "QZRX CORP - Sell", 10, "9.00",
                                "80.05")),
            [("BUYSELL", -10.0, 80.05, 9.95)])


class TestWhatDoesNotChange(unittest.TestCase):

    def test_buy_and_sell_rows(self):
        self.assertEqual(
            _facts(_parse(_sale("Sell", "QZRX CORP - Sell"))),
            [("BUYSELL", -10.0, 80.05, 9.95)])
        self.assertEqual(
            _facts(_parse(_sale("Sell", "QZRX CORP - Sell", qty="-10"))),
            [("BUYSELL", -10.0, 80.05, 9.95)])
        self.assertEqual(
            _facts(_parse(_purchase("Buy", "QZRX CORP - Buy"))),
            [("BUYSELL", 10.0, 94.95, 4.95)])

    def test_a_description_with_neither_word_states_no_direction(self):
        self.assertEqual(rbc_direct.rbc_desc_side("QZRX CORP"), "")
        self.assertEqual(rbc_direct.rbc_desc_side("QZRX Buy Sell CORP"), "both")

    def test_the_side_of_a_row_that_is_no_trade_is_none(self):
        class Row:
            activity, desc, cls = "Dividends", "QZRX CORP - Sell", "dividend"
        self.assertEqual(rbc_direct.rbc_trade_side(Row), "")


if __name__ == "__main__":
    unittest.main()
