"""Questrade rows the books cannot hold, refused in the parser (the
owner's rulings of 2026-11-19; backlog TJ-4541 (1), TJ-4542, TJ-4543,
TJ-4548):

- a trade of 0 units: it reached `tjs run`'s schema check, which stopped
  the account naming no row;
- a share trade at Price 0 with a Gross: booked at price 0, unchecked;
- a CAD-settled (EXCHANGE RATE) trade with a Net Amount of 0, booked at
  Gross x rate, or at EXCHANGE RATE 0, booked on the CAD listing at the
  USD price;
- a Symbol of ".TO", booked as a symbol of its own, and a BRW journal leg
  with a blank Symbol, booked as a TRANSFER of symbol "";
- a stock-split row carrying cash, its cash dropped with no word (a
  stock dividend's was already refused, R1-72: pinned here at a cent).

Each refusal names the file and line, what is wrong and the fix. All data
synthetic (made-up tickers SAMPLA, SAMPLD, SAMPLF, SAMPLU).
"""
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from _support.qt_parse import QH, qrow, qt_parse

_F = "99900001.csv"


def _parse(rows):
    return qt_parse({_F: rows})


def _refused(test, rows):
    with test.assertRaises(BrokerageParseError) as cm:
        _parse(rows)
    return str(cm.exception)


def _trade(**kw):
    base = dict(td='2025-03-03', sym='SAMPLA', desc='SAMPLA INC WE ACTED AS AGENT',
                qty='10', price='5.00', gross='-50.00', comm='-4.95', net='-54.95',
                cur='USD')
    base.update(kw)
    return qrow(**base)


class TestATradeOfNoUnits(unittest.TestCase):
    """TJ-4543 (2): a Buy of 0 shares, cash or none."""

    def test_carrying_cash(self):
        msg = _refused(self, _trade(qty='0', price='0.00', gross='0.00', net='-4.95'))
        self.assertTrue(msg.startswith("99***.csv line 2: a Buy row with Quantity 0 — "
                                       "a trade of 0 units has no direction"), msg)
        self.assertIn("Correct the Quantity from the statement, or delete the row and "
                      "enter the trade as a .tt BUYSELL line", msg)

    def test_with_no_cash(self):
        msg = _refused(self, _trade(action='Sell', qty='0', price='0.00', gross='0.00',
                                    comm='0.00', net='0.00'))
        self.assertIn("a Sell row with Quantity 0", msg)

    def test_a_blank_quantity_is_refused_as_before(self):
        self.assertIn("required Quantity is blank", _refused(self, _trade(qty='')))


class TestAShareTradeAtPrice0WithAGross(unittest.TestCase):
    """TJ-4543 (1)."""

    def test_refused(self):
        msg = _refused(self, _trade(qty='10', price='0.00', gross='-500.00',
                                    net='-504.95'))
        self.assertTrue(msg.startswith(
            "99***.csv line 2: Buy 10 SAMPLA at Price 0 with Gross Amount -500.00 — a "
            "share trade at Price 0 carrying cash: the Price cell is cut or the columns "
            "are shifted"), msg)
        self.assertIn("Fill the Price from the statement", msg)

    def test_the_same_trade_with_its_price_is_booked(self):
        txs, _ = _parse(_trade(qty='10', price='50.00', gross='-500.00', net='-504.95'))
        self.assertEqual([(t['price'], t['net_amount']) for t in txs], [(50.0, 504.95)])

    def test_an_assignments_stock_leg_at_price_0_is_booked_at_its_cash(self):
        # The ruling covers a Buy or a Sell (review round 1): an ASN
        # stock leg at Price 0 is booked at its Net Amount, as before.
        put = 'PUT SAMPLO 10/17/25 5.00 SAMPLO INC'
        txs, _ = _parse(
            _trade(td='2025-10-01', action='Sell', sym='SAMPLO.OPT',
                   desc=f'{put} WE ACTED AS AGENT', qty='-1', price='0.40',
                   gross='40.00', comm='-1.00', net='39.00')
            + qrow(td='2025-10-17', action='ASN', sym='SAMPLO.OPT',
                   desc=f'{put} ASSIGNMENT', qty='1', price='0.00', gross='0.00',
                   comm='0.00', net='0.00', cur='USD', act='Other')
            + qrow(td='2025-10-17', action='ASN', sym='SAMPLO',
                   desc='SAMPLO INC ASSIGNMENT OF OPTION PUT SAMPLO 10/17/25 5',
                   qty='100', price='0.00', gross='-500.00', comm='0.00',
                   net='-500.00', cur='USD', act='Other'))
        self.assertEqual([(t['action'], t['symbol'], t['quantity'], t['net_amount'])
                          for t in txs[2:]],
                         [('ASSIGN', 'SAMPLO.US', 100.0, 500.0)])


class TestACadSettledTrade(unittest.TestCase):
    """TJ-4542: the CAD-cash identity runs on every EXCHANGE RATE row, and
    a rate of 0 is refused."""

    _DESC = 'SAMPLU INC WE ACTED AS AGENT EXCHANGE RATE {r}'

    def _row(self, rate, net):
        return _trade(td='2025-07-11', sym='SAMPLU', desc=self._DESC.format(r=rate),
                      price='50.00', gross='-500.00', net=net, cur='CAD')

    def test_a_net_amount_of_0_refused(self):
        msg = _refused(self, self._row('1.3500', '0.00'))
        self.assertTrue(msg.startswith(
            "99***.csv line 2: CAD Net Amount 0.00 is not the USD Gross 500.00 x "
            "EXCHANGE RATE 1.35 = 675.00 give or take the Commission 4.95"), msg)
        self.assertIn("Correct the Net Amount and the rate from the trade confirmation.",
                      msg)

    def test_a_rate_of_0_refused(self):
        msg = _refused(self, self._row('0.0000', '-504.95'))
        self.assertTrue(msg.startswith(
            "99***.csv line 2: a CAD-settled trade states EXCHANGE RATE 0.0000"), msg)
        self.assertIn("Correct the rate and the Net Amount from the trade confirmation.",
                      msg)

    def test_a_trade_at_its_rate_is_booked_in_usd_listing(self):
        txs, _ = _parse(self._row('1.3500', '-679.95'))
        self.assertEqual([(t['symbol'], t['net_amount']) for t in txs],
                         [('SAMPLU.US', 679.95)])


class TestASymbolWithNoTicker(unittest.TestCase):
    """TJ-4543 (3) and TJ-4541 (1)."""

    def test_a_symbol_of_dot_to_refused(self):
        msg = _refused(self, _trade(sym='.TO', desc='SAMPLE CORP WE ACTED AS AGENT',
                                    cur='CAD'))
        self.assertTrue(msg.startswith(
            "99***.csv line 2: Buy row's Symbol '.TO' is a listing suffix with no "
            "ticker (missing required field 'symbol')"), msg)

    def test_a_journal_leg_with_a_blank_symbol_refused(self):
        msg = _refused(self, _trade(td='2025-07-10', sym='SAMPLF.TO',
                                    desc='SAMPLF US DOLLAR FUND WE ACTED AS AGENT',
                                    price='13.00', gross='-130.00', net='-134.95',
                                    cur='CAD')
                       + qrow(td='2025-09-02', action='BRW', sym='',
                              desc='SAMPLF US DOLLAR FUND JOURNAL POSITION FROM CAD '
                                   'BOOK VALUE: $103.81 CNV@ 1.3000',
                              qty='10', price='0.00', gross='0.00', comm='0.00',
                              net='0.00', cur='USD', act='Other'))
        self.assertTrue(msg.startswith("99***.csv line 3: a BRW journal row has no "
                                       "Symbol"), msg)
        self.assertIn("Write the security's symbol in the cell (its partner row names "
                      "it), or book the move in a .tt file.", msg)

    def test_a_refused_lone_leg_is_not_also_said_booked(self):
        # Review round 2: the lone-leg warning ("Booked as a transfer leg
        # of  on its own", naming nothing) came just before the refusal.
        rows = qrow(td='2025-09-02', action='BRW', sym='',
                    desc='SAMPLF US DOLLAR FUND JOURNAL POSITION FROM CAD BOOK VALUE: '
                         '$103.81 CNV@ 1.3000',
                    qty='10', price='0.00', gross='0.00', comm='0.00', net='0.00',
                    cur='USD', act='Other')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / _F
            p.write_text(QH + rows, encoding='utf-8')
            ctx = QuestradeBrokerage.prepare_files([p])
        self.assertEqual([m for m in ctx.messages if 'no partner' in m], [])
        self.assertTrue(_refused(self, rows).startswith(
            "99***.csv line 2: a BRW journal row has no Symbol"))

    def test_a_paired_leg_with_a_blank_symbol_takes_its_partners(self):
        # Review round 1: the TO leg names the security, so the pair is
        # booked on both lines, as main booked it.
        name = 'SAMPLF US DOLLAR FUND'
        txs, _ = _parse(
            _trade(td='2025-07-10', sym='SAMPLF.TO', desc=f'{name} WE ACTED AS AGENT',
                   price='13.00', gross='-130.00', net='-134.95', cur='CAD')
            + qrow(td='2025-09-02', action='BRW', sym='SAMPLF',
                   desc=f'{name} JOURNAL POSITION TO USD', qty='-10', price='0.00',
                   gross='0.00', comm='0.00', net='0.00', cur='CAD', act='Other')
            + qrow(td='2025-09-02', action='BRW', sym='',
                   desc=f'{name} JOURNAL POSITION FROM CAD BOOK VALUE: $103.81 '
                        'CNV@ 1.3000',
                   qty='10', price='0.00', gross='0.00', comm='0.00', net='0.00',
                   cur='USD', act='Other'))
        legs = [(t['symbol'], t['quantity'], t.get('journal_pair')) for t in txs[1:]]
        self.assertEqual(legs, [('SAMPLF.TO', -10.0, '2025-09-02#1'),
                                ('SAMPLF.U.TO', 10.0, '2025-09-02#1')])


class TestASplitRowCarryingCash(unittest.TestCase):
    """TJ-4548 (n), and (d) pinned."""

    def _dis(self, desc, qty, net, sym='SAMPLA.TO'):
        return qrow(td='2025-09-15', action='DIS', sym=sym, desc=desc, qty=qty,
                    price='0.00', gross='0.00', comm='0.00', net=net, cur='CAD',
                    act='Dividends')

    def test_refused(self):
        msg = _refused(self, self._dis('SAMPLA CORP STK SPLIT ON 100 SHS', '100', '1.00'))
        self.assertTrue(msg.startswith(
            "99***.csv line 2: a stock-split DIS row carries both a quantity (100) and "
            "cash (1.00; 'SAMPLA CORP STK SPLIT ON 100 SHS') — refusing to guess what "
            "the cash is."), msg)
        self.assertIn("Split it into a share row at Net Amount 0.00 and a cash row", msg)

    def test_split_into_a_share_row_and_a_cash_row_both_booked(self):
        txs, _ = _parse(
            self._dis('SAMPLA CORP STK SPLIT ON 100 SHS', '100', '0.00')
            + self._dis('SAMPLA CORP CASH DIV ON 100 SHS', '0', '1.00'))
        self.assertEqual(sorted((t['action'], t['quantity'] if t['action'] == 'SPLIT'
                                 else t['net_amount']) for t in txs),
                         [('DIVIDEND', 1.0), ('SPLIT', 2.0)])

    def test_a_stock_dividend_carrying_a_cent_still_refused(self):
        msg = _refused(self, self._dis('SAMPLD SPLIT CORP STK DIV ON 100 SHS', '5', '0.01',
                                       sym='SAMPLD.TO'))
        self.assertIn("a stock-dividend DIS row carries both a quantity (5) and cash "
                      "(0.01;", msg)


if __name__ == "__main__":
    unittest.main()
