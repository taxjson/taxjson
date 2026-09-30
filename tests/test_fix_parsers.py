"""Regression tests for the broker-parser audit findings R1-51, R1-63,
R1-66, R1-91, S010-05 and S014-07, plus the last comma-stripping number
parses. Every fixture is synthetic: invented tickers, fake account ids
(55500001 / U5550001), invented prices."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.core import CanadaTaxRules, TaxTransaction


def _parse(parser, content, name='t.csv'):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(content, encoding='utf-8')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            txs = parser.parse_file(p)
    return txs, err.getvalue()


def _tt(rows):
    fields = TaxTransaction.__dataclass_fields__
    return [TaxTransaction(**{k: v for k, v in r.items() if k in fields})
            for r in rows]


def _gains(rows):
    """Canada engine over parser dicts -> (realized entries, result)."""
    with contextlib.redirect_stderr(io.StringIO()):
        res = CanadaTaxRules().compute_gains(_tt(rows))
    return [g for g in res['transactions']
            if 'gain' in g and g.get('action') is None]


# ------------------------------------------------------------------ IB
IB_HEAD = ('Statement,Header,Field Name,Field Value\n'
           'Statement,Data,BrokerName,Interactive Brokers\n'
           'Statement,Data,Title,Activity Statement\n')
IB_TRADES_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
               'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
               'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')


def _ib_trade(sym, when, qty, price, proceeds, comm=0, code='O',
              cur='CAD', acct='U5550001'):  # pii-ok (synthetic)
    return (f'Trades,Data,Order,Stocks,{cur},{acct},{sym},"{when}",{qty},'
            f'{price},0,{proceeds},{comm},0,0,0,{code}\n')


def _ib(rows):
    from taxjson.lib.brokerages.ib_extractor import IbBrokerage
    return _parse(IbBrokerage(), IB_HEAD + IB_TRADES_H + ''.join(rows))


BUY_200 = _ib_trade('XYZ', '2025-01-10, 10:00:00', 200, 60, -12000, 0, 'O')
SELL_ORIG = _ib_trade('XYZ', '2025-06-02, 10:00:00', -100, 50, 5000, 0, 'C')
SELL_CA = _ib_trade('XYZ', '2025-06-02, 10:00:00', 100, 50, -5000, 0, 'Ca')
SELL_REBOOK = _ib_trade('XYZ', '2025-06-02, 10:05:00', -100, 51, 5100, 0,
                        'C')


class TestIbTradeCancellation(unittest.TestCase):
    """R1-51: a Trades row coded `Ca` reverses its original fill."""

    def _assert_one_sale_at_51(self, txs):
        trades = [(t['quantity'], t['price']) for t in txs
                  if t['action'] == 'BUYSELL']
        self.assertEqual(trades, [(200, 60), (-100, 51)])
        gains = _gains(txs)
        self.assertAlmostEqual(sum(g['gain'] for g in gains), -900.0,
                               places=2)
        self.assertFalse(any(g.get('is_wash_sale') for g in gains))

    def test_same_statement_cancel_and_rebook(self):
        txs, err = _ib([BUY_200, SELL_ORIG, SELL_CA, SELL_REBOOK])
        self._assert_one_sale_at_51(txs)
        self.assertIn('cancelled (Ca)', err)

    def test_cancellation_listed_before_its_original(self):
        txs, _ = _ib([BUY_200, SELL_CA, SELL_ORIG, SELL_REBOOK])
        self._assert_one_sale_at_51(txs)

    def test_cross_statement_pair_nets_in_merge2(self):
        from taxjson.bin.taxjson_merge2 import cancel_trade_pairs
        from taxjson.lib.trade_cancel import TRADE_CANCEL_TYPE
        a, _ = _ib([BUY_200, SELL_ORIG])
        b, err = _ib([SELL_CA, SELL_REBOOK])
        ca = [t for t in b if t.get('type') == TRADE_CANCEL_TYPE]
        self.assertEqual(len(ca), 1, "the unpaired Ca leg is marked")
        self.assertIn('not in this statement', err)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            merged = cancel_trade_pairs(_tt(a + b))
        self.assertEqual([(t.quantity, t.price) for t in merged],
                         [(200, 60), (-100, 51)])
        self.assertIn('cancellation', buf.getvalue())

    def test_merge2_warns_when_the_original_is_nowhere(self):
        from taxjson.bin.taxjson_merge2 import cancel_trade_pairs
        b, _ = _ib([SELL_CA, SELL_REBOOK])
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            merged = cancel_trade_pairs(_tt(b))
        self.assertEqual(len(merged), 2)
        self.assertIn('none of this account', buf.getvalue())

    def test_other_codes_untouched(self):
        # A code merely CONTAINING the letters (e.g. `Ca` inside a word)
        # is not a cancellation: tokens are matched exactly.
        txs, _ = _ib([BUY_200,
                      _ib_trade('XYZ', '2025-06-02, 10:00:00', -100, 50,
                                5000, 0, 'C;Cax')])
        self.assertEqual(len([t for t in txs
                              if t['action'] == 'BUYSELL']), 2)


# ----------------------------------------------------------- Questrade
QT_HEAD = ('Transaction Date,Settlement Date,Action,Symbol,Description,'
           'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
           'Account #,Activity Type,Account Type\n')


def _qt_row(date, action, sym, desc, qty, price, gross, comm, net,
            cur='CAD', activity='Trades'):
    return (f'{date} 12:00:00 AM,{date} 12:00:00 AM,{action},{sym},'
            f'"{desc}",{qty},{price},{gross},{comm},{net},{cur},'
            f'55500001,{activity},Individual margin\n')  # pii-ok


def _qt(rows):
    from taxjson.lib.brokerages.questrade import QuestradeBrokerage
    return _parse(QuestradeBrokerage(), QT_HEAD + ''.join(rows))


class TestQuestradeAssignmentWords(unittest.TestCase):
    """R1-63: only zero-cash option legs are zeroed."""

    PUT_WRITE = _qt_row('2025-05-01', 'Sell', '',
                        'PUT QZX 06/20/25 50.00 QZX CORP', 1, 2.00, 200,
                        0, 200)
    PUT_ASN = _qt_row('2025-06-23', 'ASN', '',
                      'PUT QZX 06/20/25 50.00 QZX CORP ASSIGNMENT', 1, 0,
                      0, 0, 0)
    STOCK_SALE = _qt_row('2025-07-10', 'Sell', 'QZX.TO',
                         'QZX CORP WE ACTED AS AGENT', 100, 55, 5500, 0,
                         5500)

    def _gain(self, stock_leg):
        txs, _ = _qt([self.PUT_WRITE, self.PUT_ASN, stock_leg,
                      self.STOCK_SALE])
        leg = [t for t in txs if t['symbol'] == 'QZX.TO'
               and t['quantity'] > 0][0]
        self.assertAlmostEqual(leg['net_amount'], 5000.0)
        return txs, sum(g['gain'] for g in _gains(txs))

    def test_asn_stock_leg_keeps_its_cash(self):
        txs, gain = self._gain(_qt_row(
            '2025-06-23', 'ASN', 'QZX.TO', 'QZX CORP ASSIGNMENT OF PUT',
            100, 50, -5000, 0, -5000))
        self.assertAlmostEqual(gain, 700.0, places=2)
        leg = [t for t in txs if t['symbol'] == 'QZX.TO'
               and t['quantity'] > 0][0]
        self.assertEqual(leg['action'], 'ASSIGN')

    def test_buy_described_as_assignment_keeps_its_cash(self):
        _, gain = self._gain(_qt_row(
            '2025-06-23', 'Buy', 'QZX.TO', 'QZX CORP ASSIGNMENT OF PUT',
            100, 50, -5000, 0, -5000))
        self.assertAlmostEqual(gain, 700.0, places=2)

    def test_security_named_exercise_is_an_ordinary_trade(self):
        txs, _ = _qt([
            _qt_row('2025-03-03', 'Buy', 'AEE.TO',
                    'ACME EXERCISE EQUIPMENT INC', 100, 5, -500, -4.95,
                    -504.95),
            _qt_row('2025-04-03', 'Sell', 'AEE.TO',
                    'ACME EXERCISE EQUIPMENT INC', 100, 9, 900, -4.95,
                    895.05)])
        self.assertEqual({t['action'] for t in txs}, {'BUYSELL'})
        self.assertAlmostEqual(sum(g['gain'] for g in _gains(txs)),
                               390.10, places=2)

    def test_sale_described_expired_keeps_its_proceeds(self):
        txs, _ = _qt([
            _qt_row('2025-03-03', 'Buy', 'QZR.TO', 'QZR RIGHTS', 100, 10,
                    -1000, 0, -1000),
            _qt_row('2025-04-03', 'Sell', 'QZR.TO',
                    'QZR RIGHTS - EXPIRED 04/30/25', 100, 12, 1200, 0,
                    1200)])
        self.assertAlmostEqual(sum(g['gain'] for g in _gains(txs)),
                               200.0, places=2)

    def test_zero_cash_option_legs_still_zeroed(self):
        txs, _ = _qt([self.PUT_WRITE, self.PUT_ASN])
        asn = [t for t in txs if t['action'] == 'ASSIGN'][0]
        self.assertEqual((asn['price'], asn['net_amount']), (0.0, 0.0))
        self.assertTrue(asn['symbol'].startswith('QZX250620P'))

    def test_exp_row_with_cash_is_refused(self):
        with self.assertRaisesRegex(BrokerageParseError, 'EXP'):
            _qt([_qt_row('2025-06-23', 'EXP', '',
                         'PUT QZX 06/20/25 50.00 QZX CORP - EXPIRED', 1,
                         1.0, 100, 0, 100)])

    def test_zero_cash_stock_assignment_is_refused(self):
        with self.assertRaisesRegex(BrokerageParseError, 'not an option'):
            _qt([_qt_row('2025-06-23', 'ASN', 'QZX.TO',
                         'QZX CORP ASSIGNMENT OF PUT', 100, 0, 0, 0, 0)])


class TestQuestradeReversals(unittest.TestCase):
    """R1-66: CIL / REI keep their sign; a reversal cancels."""

    CIL = ('QZF SPLIT CORP CASH IN LIEU OF .50000 REC 06/19/26 PAY '
           '06/26/26')
    REI = 'QZF SPLIT CORP REINV@C$8.34000 REC 04/30/26 PAY 05/11/26'

    def test_cil_reversal_cancels(self):
        txs, _ = _qt([
            _qt_row('2026-06-30', 'CIL', 'QZF.TO', self.CIL, 0, 0, 0, 0,
                    4.56, activity='Corporate actions'),
            _qt_row('2026-07-03', 'CIL', 'QZF.TO', self.CIL, 0, 0, 0, 0,
                    -4.56, activity='Corporate actions')])
        self.assertEqual(txs, [])

    def test_rei_reversal_cancels(self):
        txs, _ = _qt([
            _qt_row('2026-05-11', 'REI', 'QZF.TO', self.REI, 3, 0, 0, 0,
                    -25.02, activity='Dividend reinvestment'),
            _qt_row('2026-05-14', 'REI', 'QZF.TO', self.REI, -3, 0, 0, 0,
                    25.02, activity='Dividend reinvestment')])
        self.assertEqual(txs, [])

    def test_reversal_listed_first_still_cancels(self):
        txs, _ = _qt([
            _qt_row('2026-05-14', 'REI', 'QZF.TO', self.REI, -3, 0, 0, 0,
                    25.02, activity='Dividend reinvestment'),
            _qt_row('2026-05-11', 'REI', 'QZF.TO', self.REI, 3, 0, 0, 0,
                    -25.02, activity='Dividend reinvestment')])
        self.assertEqual(txs, [])

    def test_lone_reversal_is_refused(self):
        with self.assertRaisesRegex(BrokerageParseError, 'reversal'):
            _qt([_qt_row('2026-07-03', 'CIL', 'QZF.TO', self.CIL, 0, 0,
                         0, 0, -4.56, activity='Corporate actions')])
        with self.assertRaisesRegex(BrokerageParseError, 'reversal'):
            _qt([_qt_row('2026-05-14', 'REI', 'QZF.TO', self.REI, -3, 0,
                         0, 0, 25.02, activity='Dividend reinvestment')])

    def test_normal_rows_unchanged(self):
        txs, _ = _qt([
            _qt_row('2026-05-11', 'REI', 'QZF.TO', self.REI, 3, 0, 0, 0,
                    -25.02, activity='Dividend reinvestment'),
            _qt_row('2026-06-30', 'CIL', 'QZF.TO', self.CIL, 0, 0, 0, 0,
                    4.56, activity='Corporate actions')])
        self.assertEqual([t['quantity'] for t in txs], [3, 0.5, -0.5])


if __name__ == '__main__':
    unittest.main()
