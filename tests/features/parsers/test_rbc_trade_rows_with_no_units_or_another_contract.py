"""RBC Direct trade and transfer rows the owner ruled on (2026-11-19;
backlog TJ-4536):

- a Buy or Sell of no units, a blank or 0 Quantity, is refused in the
  parser, naming the file, the line and the Quantity cell: it reached
  `tjs run`'s row check, which stopped the account naming no row (the
  twin of the blank Value the parser refuses, R1-82);
- KEPT and pinned: a listed ticker whose description names another
  underlying's contract stays refused, and an option transfer under an
  RBC code whose own description does not parse books the contract the
  code's Buy row names.

All data synthetic (made-up tickers SAMPA, SAMPB; code 8SMPLA1).
"""
import unittest

from taxjson.lib.brokerages.base import BrokerageParseError
from _support.rbc_rows import parse_files, row


def _trade(activity='Buy', qty='10', price='5.00', value='-59.95', symbol='SAMPA',
           desc=None, date='01/12/2024', settle='01/15/2024'):
    # A settlement day past the 12th: the dates read one way only.
    return row(date, activity, symbol, '', qty, price, value, 'CAD',
               desc or f'SAMPA CORP - {activity}', settle=settle)


def _refused(test, body):
    with test.assertRaises(BrokerageParseError) as cm:
        parse_files({'99900001.csv': body})
    return str(cm.exception)


class TestATradeOfNoUnits(unittest.TestCase):

    def test_a_blank_quantity_carrying_cash(self):
        msg = _refused(self, _trade(qty='', price='', value='-9.95'))
        self.assertTrue(msg.startswith(
            "99***.csv:2: a Buy row with a blank Quantity — a trade of 0 units has "
            "no direction"), msg)
        self.assertIn("Correct the Quantity from the statement, or delete the row and "
                      "enter the trade as a .tt BUYSELL line", msg)

    def test_a_quantity_of_0_with_no_cash(self):
        msg = _refused(self, _trade(activity='Sell', qty='0', value='0'))
        self.assertTrue(msg.startswith("99***.csv:2: a Sell row with Quantity 0 — "), msg)

    def test_a_buy_with_a_negative_quantity_is_refused_as_before(self):
        self.assertIn("a Buy row with a NEGATIVE Quantity -10",
                      _refused(self, _trade(qty='-10')))

    def test_a_trade_with_its_quantity_is_booked(self):
        txs, _err, _p = parse_files({'99900001.csv': _trade()})
        self.assertEqual([(t['symbol'], t['quantity'], t['net_amount']) for t in txs],
                         [('SAMPA.TO', 10.0, 59.95)])


class TestKeptShapes(unittest.TestCase):
    """Ruling 12: right as main books them; pinned."""

    def test_a_listed_ticker_naming_another_underlyings_contract_is_refused(self):
        msg = _refused(self, _trade(
            date='02/13/2024', settle='02/14/2024', qty='2', price='1.50', value='-312.45',
            desc='CALL .SAMPB 03/15/24 30 SAMPB CORP DA OPEN CONTRACT'))
        self.assertIn("Symbol 'SAMPA' is a listed ticker but the description names "
                      "option contract SAMPB240315C00030000", msg)
        self.assertIn("refusing to guess which one the row trades", msg)

    def test_an_option_transfer_whose_description_does_not_parse(self):
        body = (row('12/16/2024', 'Transfers', '8SMPLA1', '', '-1', '', '0', 'CAD',
                    'TFO - CALL .SAMPA 03/21/25 30 SAMPA CORP ACCOUNT TRANSFER')
                + row('12/13/2024', 'Buy', '8SMPLA1', '', '1', '1.50', '-161.20', 'CAD',
                      'CALL .SAMPA 03/21/25 30 SAMPA CORP DA OPEN CONTRACT',
                      settle='12/16/2024'))
        txs, _err, _p = parse_files({'99900001.csv': body})
        moved = [t for t in txs if t['action'] == 'TRANSFER']
        self.assertEqual([(t['symbol'], t['quantity'], t.get('multiplier')) for t in moved],
                         [('SAMPA250321C00030000.TO', -1.0, 100.0)])


if __name__ == "__main__":
    unittest.main()
