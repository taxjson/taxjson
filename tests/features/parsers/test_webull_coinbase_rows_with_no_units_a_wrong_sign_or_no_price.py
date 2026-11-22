"""Webull and Coinbase trade rows the books cannot hold, refused in the
parser (the owner's rulings of 2026-11-19; backlog TJ-4501, TJ-4502,
TJ-4505, TJ-4511):

- a trade of 0 units, or a blank quantity, with or without cash: it
  reached `tjs run`'s schema check, which stopped the account naming no
  row ("the parser should not emit it");
- a Buy whose quantity is negative with no cash (Webull) or a blank Total
  (Coinbase): booked as a purchase, the sign dropped;
- a Webull share trade at Price 0 carrying Proceeds: booked unchecked;
- two Webull $0 option rows of one contract at one instant before its
  expiry, nothing held: each counted the other as held, so both passed
  the zero-close check as closes.

Each refusal names the file and line, what is wrong and the fix. All data
synthetic (made-up tickers ZZQA, ZZQB; the shared preamble's account).
"""
import os
import unittest

from taxjson.lib.brokerages.base import BrokerageParseError
from _support.cb_parse import _parse as _cb_parse
from _support.wb_option_buy import _parse as _wb_parse

_ZERO_UNITS_FIX = ("Correct the Quantity from the statement, or delete the row and "
                   "enter the trade as a .tt BUYSELL line (a charge with no trade is a "
                   ".tt FEE line).")
_SALE_SIGN = "the action says purchase, the quantity says sale; refusing to guess"


def setUpModule():
    # Coinbase dates a UTC stamp in the local zone, which has no default:
    # the module ran only after another that set it (a gate split alone).
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


class TestWebullTradeOfNoUnits(unittest.TestCase):
    """TJ-4502 (2), TJ-4505 (2): a trade of 0 units is refused by the
    parser, cash or none, naming its line and the Quantity cell."""

    def _refused(self, rows):
        with self.assertRaises(BrokerageParseError) as cm:
            _wb_parse(rows)
        return str(cm.exception)

    def test_a_buy_of_0_shares_carrying_cash(self):
        msg = self._refused('USD,15-01-2025,BUY,ZZQA,ZZQA INC,SHS,0,1.00,,5.00\n')
        self.assertTrue(msg.startswith("wb.csv line 13: a BUY row with Quantity 0 — "
                                       "a trade of 0 units has no direction"), msg)
        self.assertIn(_ZERO_UNITS_FIX, msg)

    def test_a_0_quantity_option_row_at_0(self):
        msg = self._refused(
            'USD,02-06-2025,BUY,@ZZQB,PUT ZZQB12/19/25 20,OPC,1,0.80,,(81.00)\n'
            'USD,13-06-2025,BUY,,,,0,0.00,,\n')
        self.assertIn("wb.csv line 14: a BUY row with Quantity 0", msg)

    def test_a_trade_of_one_unit_is_booked(self):
        tx, _ = _wb_parse('USD,15-01-2025,BUY,ZZQA,ZZQA INC,SHS,1,5.00,,(5.00)\n')
        self.assertEqual([t["quantity"] for t in tx], [1.0])


class TestWebullBuyWithANegativeQuantity(unittest.TestCase):
    """TJ-4502 (1): a BUY of -1 with no cash was booked as a buy of 1."""

    _WRITE = 'USD,02-06-2025,SELL,@ZZQB,PUT ZZQB06/20/25 20,OPC,-1,0.80,,79.35\n'

    def test_with_no_cash_refused(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _wb_parse(self._WRITE + 'USD,20-06-2025,BUY,,,,-1,0.00,,\n')
        msg = str(cm.exception)
        self.assertTrue(msg.startswith("wb.csv line 14: a BUY row with a NEGATIVE "
                                       "Quantity -1 and no cash — "), msg)
        self.assertIn(_SALE_SIGN, msg)
        self.assertIn("Correct the Quantity's sign or the Action Code from the "
                      "statement.", msg)

    def test_with_cash_in_still_refused_as_before(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _wb_parse(self._WRITE + 'USD,20-06-2025,BUY,,,,-1,0.10,,9.00\n')
        self.assertIn("both say sale", str(cm.exception))

    def test_the_close_written_as_a_buy_of_1_is_booked(self):
        tx, _ = _wb_parse(self._WRITE + 'USD,20-06-2025,BUY,,,,1,0.00,,\n')
        self.assertEqual([t["quantity"] for t in tx], [-1.0, 1.0])


class TestWebullShareTradeAtPrice0WithCash(unittest.TestCase):
    """TJ-4501 (1): a share row at Price 0 carrying Proceeds was booked
    unchecked, at price 0; an option's $0 close is still an expiry."""

    _BUY = 'USD,15-01-2025,BUY,ZZQA,ZZQA INC,SHS,10,12.00,,(121.00)\n'

    def test_refused(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _wb_parse(self._BUY + 'USD,16-01-2025,SELL,,,,-10,0.00,,1.00\n')
        msg = str(cm.exception)
        self.assertTrue(msg.startswith("wb.csv line 14: SELL -10 ZZQA.US at Price 0 with "
                                       "Proceeds 1.00 — a share trade at Price 0 "
                                       "carrying cash: the Price cell is cut or the "
                                       "columns are shifted"), msg)
        self.assertIn("Fill the Price from the statement", msg)

    def test_the_same_sale_with_its_price_is_booked(self):
        tx, _ = _wb_parse(self._BUY + 'USD,16-01-2025,SELL,,,,-10,12.10,,120.00\n')
        self.assertEqual([t["net_amount"] for t in tx], [121.0, 120.0])

    def test_an_option_closed_at_0_with_no_cash_is_an_expiry(self):
        tx, _ = _wb_parse(
            'USD,02-06-2025,BUY,@ZZQB,PUT ZZQB06/20/25 20,OPC,1,0.80,,(81.00)\n'
            'USD,20-06-2025,SELL,,,,-1,0.00,,\n')
        self.assertEqual(tx[-1]["time"], "16:00:00")


class TestWebullZeroRowsAtOneInstant(unittest.TestCase):
    """TJ-4505 (1): a $0 row's same-instant $0 twins are no holding."""

    def test_a_buy_and_a_sale_at_0_with_nothing_held_refused(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _wb_parse('USD,13-06-2025,BUY,@ZZQB,PUT ZZQB12/19/25 20,OPC,1,0.00,,\n'
                      'USD,13-06-2025,SELL,,,,-1,0.00,,\n')
        msg = str(cm.exception)
        self.assertTrue(msg.startswith("wb.csv line 13: BUY 1 ZZQB251219P00020000.US "
                                       "at $0 (no Price, no Proceeds) opens a position "
                                       "(held before it: 0)"), msg)
        self.assertIn("Refusing to book a premium-free buy", msg)

    def test_two_closes_at_0_of_two_held_booked(self):
        tx, _ = _wb_parse('USD,02-06-2025,BUY,@ZZQB,PUT ZZQB12/19/25 20,OPC,2,0.80,,(161.00)\n'
                          'USD,13-06-2025,SELL,,,,-1,0.00,,\n'
                          'USD,13-06-2025,SELL,,,,-1,0.00,,\n')
        self.assertEqual([t["quantity"] for t in tx], [2.0, -1.0, -1.0])

    def test_two_closes_at_0_of_one_held_before_the_expiry_refused(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _wb_parse('USD,02-06-2025,BUY,@ZZQB,PUT ZZQB12/19/25 20,OPC,1,0.80,,(81.00)\n'
                      'USD,13-06-2025,SELL,,,,-1,0.00,,\n'
                      'USD,13-06-2025,SELL,,,,-1,0.00,,\n')
        self.assertIn("opens a position (held before it: 1)", str(cm.exception))

    def test_at_the_expiry_two_closes_of_one_held_booked(self):
        # At the expiry a close beyond what the data holds is a position
        # bought before the first export, as before.
        tx, _ = _wb_parse('USD,02-06-2025,BUY,@ZZQB,PUT ZZQB06/20/25 20,OPC,1,0.80,,(81.00)\n'
                          'USD,20-06-2025,SELL,,,,-1,0.00,,\n'
                          'USD,20-06-2025,SELL,,,,-1,0.00,,\n')
        self.assertEqual([t["quantity"] for t in tx], [1.0, -1.0, -1.0])


class TestCoinbaseBuyRowsTheBooksCannotHold(unittest.TestCase):
    """TJ-4511: a Buy with a negative quantity and a blank Total, and a
    trade of 0 units (or a blank quantity) carrying cash."""

    _T = "X1,2025-01-15 10:00:00 UTC,{t},ETH,{q},USD,3000,{s},{tot},{f},\n"

    def _refused(self, **kw):
        with self.assertRaises(ValueError) as cm:
            _cb_parse(self._T.format(**kw))
        return str(cm.exception)

    def test_a_buy_with_a_negative_quantity_and_a_blank_total(self):
        msg = self._refused(t="Buy", q="-1", s="3000", tot="", f="10")
        self.assertTrue(msg.startswith(
            "Coinbase cb_2025.csv line 3 (Buy ETH 2025-01-15 10:00:00 UTC): a Buy row "
            "with a NEGATIVE Quantity Transacted -1 and a blank Total — "), msg)
        self.assertIn(_SALE_SIGN, msg)

    def test_the_sale_signature_still_refused_as_before(self):
        msg = self._refused(t="Buy", q="-1", s="-3000", tot="-3010", f="10")
        self.assertIn("the sale signature", msg)

    def test_a_buy_of_0_with_a_total(self):
        msg = self._refused(t="Buy", q="0", s="", tot="-5", f="0")
        self.assertTrue(msg.startswith(
            "Coinbase cb_2025.csv line 3 (Buy ETH 2025-01-15 10:00:00 UTC): a Buy row "
            "with Quantity Transacted 0 — a trade of 0 units"), msg)
        self.assertIn("Correct the Quantity Transacted from the statement", msg)

    def test_a_sale_with_a_blank_quantity(self):
        msg = self._refused(t="Sell", q="", s="", tot="5", f="0")
        self.assertIn("a Sell row with a blank Quantity Transacted — a trade of 0 units",
                      msg)

    def test_a_buy_of_one_coin_is_booked(self):
        tx, _err, _p = _cb_parse(self._T.format(t="Buy", q="1", s="3000", tot="3010",
                                                f="10"))
        self.assertEqual([(t["quantity"], t["net_amount"]) for t in tx], [(1.0, 3010.0)])


if __name__ == "__main__":
    unittest.main()
