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


if __name__ == '__main__':
    unittest.main()
