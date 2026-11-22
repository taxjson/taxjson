"""Re-audit-2 fixes: the .tt converter, the schema validator, Canadian
venue suffixes, bare rename targets and partial trade cancellations
(fix list parsers-common-03 + A2-0635). Synthetic data only.

Formerly: test_fix_a2_tt"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _quiet(fn, *a, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out = fn(*a, **kw)
    return out, err.getvalue()


class TestSchemaFutures(unittest.TestCase):
    """A2-0302, A2-1082, A2-1087, A2-1089, A2-1088.

    Formerly: test_fix_a2_tt.TestSchemaFutures"""

    def _v(self, **kw):
        from taxjson.lib.brokerages.schema import validate_transactions
        tx = dict(action='BUYSELL', date='2025-04-01', currency='USD',
                  quantity=1, net_amount=10.0, price=10.0)
        tx.update(kw)
        return validate_transactions([tx])

    def test_every_futures_prefix_may_have_a_negative_price(self):
        for sym in ('F:CLK0.US', '/CLK0.US', '\\CLK0.US'):
            with self.subTest(sym=sym):
                errs, _ = self._v(symbol=sym, price=-5.0, net_amount=2.5,
                                  multiplier=1000, quantity=-1)
                self.assertFalse([e for e in errs if 'negative price' in e])
        from taxjson.bin.taxjson_validate import validate_transactions as V
        flat = str(V([{'action': 'BUYSELL', 'date': '2020-04-20',
                       'symbol': '/CLK0.US', 'quantity': 1, 'price': -5.0,
                       'net_amount': 2.5, 'currency': 'USD'}]))
        self.assertNotIn('Price is negative', flat)

    def test_negative_price_futures_buy_may_carry_signed_net(self):
        errs, _ = self._v(symbol='F:CLK0.US', price=-5.0,
                          net_amount=-4997.5, multiplier=1000)
        self.assertEqual(errs, [])
        # A share buy with a negative net is still wrong.
        errs, _ = self._v(symbol='XYZ.US', net_amount=-10.0)
        self.assertTrue([e for e in errs if '>= 0' in e])

    def test_undeclared_futures_size_is_not_guessed(self):
        for sym in ('F:CLK5.US', '/CLK5.US'):
            errs, warns = self._v(symbol=sym, price=20.0,
                                  net_amount=20002.37)
            self.assertEqual((errs, warns), ([], []), sym)
        # Declared, the check still runs (and is an error).
        errs, _ = self._v(symbol='F:CLK5.US', price=20.0,
                          net_amount=2000.0, multiplier=1000)
        self.assertTrue(errs)

    def test_option_settling_after_expiry_warns(self):
        _, warns = self._v(symbol='XYZ251231C00050000.US', date='2025-12-31',
                           date_settle='2026-01-02', price=0.0,
                           net_amount=0.0)
        self.assertTrue([w for w in warns if 'after the expiry day' in w])
        # A trade on the expiry day settles T+1 (CA-DATE-04): not flagged.
        _, warns = self._v(symbol='XYZ251231C00050000.US', date='2025-12-31',
                           date_settle='2026-01-02', price=0.5,
                           net_amount=49.0, quantity=-1)
        self.assertFalse([w for w in warns if 'expiry' in w])
        _, warns = self._v(symbol='XYZ251231C00050000.US', date='2025-12-30',
                           date_settle='2025-12-31', price=0.0,
                           net_amount=0.0)
        self.assertFalse([w for w in warns if 'expiry' in w])

    @rule(R.CA_FX_04)
    @rule(R.US_FUT_01)
    def test_negative_price_buy_books_a_gain_in_both_countries(self):
        from taxjson.model.transaction import TaxTransaction
        from taxjson.lib.futures import method_for, settle_futures

        def fill(date, qty, price, net):
            return TaxTransaction(action='BUYSELL', date=date,
                                  date_settle=date, symbol='F:CLK0.US',
                                  quantity=qty, price=price,
                                  net_amount=net, currency='USD',
                                  fee=2.5, account='m')
        for country in ('canada', 'usa'):
            with self.subTest(country=country):
                rows, _ = settle_futures(
                    [fill('2020-04-20', 1, -5.0, -4997.5),
                     fill('2020-04-22', -1, 1.0, 997.5)],
                    method_for(country))
                pl = sum(r.net_amount for r in rows if r.quantity < 0)
                # Paid -4,997.50 (received cash) to open, received  # pii-ok
                # 997.50 to close: +5,995.00, not a 4,000 loss.  # pii-ok
                self.assertAlmostEqual(pl, 5995.0, places=2)


class TestPartialTradeCancel(unittest.TestCase):
    """A2-0298: a Ca naming one execution of a multi-fill Order cancels
    that part of the aggregated Order row.

    Formerly: test_fix_a2_tt.TestPartialTradeCancel"""

    HEAD = ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers\n'
            'Statement,Data,Title,Activity Statement\n'
            'Trades,Header,DataDiscriminator,Asset Category,Currency,'
            'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
            'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')

    @staticmethod
    def _tr(when, qty, price, comm, code, disc='Order'):
        proceeds = round(-qty * price, 6)
        return (f'Trades,Data,{disc},Stocks,USD,U5550001,QZK,"{when}",'  # pii-ok
                f'{qty},{price},{price},{proceeds},{comm},0,0,0,{code}\n')

    def _parse_ib(self, body):
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ib.csv"
            p.write_text(self.HEAD + body, encoding="utf-8")
            return _quiet(IbBrokerage().parse_file, p)

    def test_execution_cancel_reduces_the_order(self):
        w = '2025-03-03, 10:00:00'
        body = (self._tr(w, 440, 10, -2.2, 'O')
                + self._tr(w, 400, 10, -2.0, 'O', 'Trade')
                + self._tr(w, 40, 10, -0.2, 'O', 'Trade')
                + self._tr(w, -40, 10, 0.2, 'Ca')
                + self._tr(w, -40, 10, 0.2, 'Ca', 'Trade')
                + self._tr(w, 40, 10.05, -0.2, 'O')
                + self._tr(w, 40, 10.05, -0.2, 'O', 'Trade'))
        txs, err = self._parse_ib(body)
        trades = sorted((t['quantity'], t['price']) for t in txs
                        if t['action'] == 'BUYSELL')
        self.assertEqual(trades, [(40, 10.05), (400, 10)])
        order = next(t for t in txs if t['quantity'] == 400)
        self.assertAlmostEqual(order['net_amount'], 4002.0, places=6)
        self.assertIn("one execution", err)

    def test_merge2_reduces_an_order_from_another_statement(self):
        from taxjson.bin.taxjson_merge2 import cancel_trade_pairs
        from taxjson.model.transaction import TaxTransaction
        from taxjson.lib.trade_cancel import TRADE_CANCEL_TYPE
        base = dict(action='BUYSELL', date='2025-03-03', time='10:00:00',
                    symbol='QZK.US', currency='USD', price=10.0,
                    account='m')
        order = TaxTransaction(**base, quantity=500, net_amount=5002.5,
                               fee=2.5)
        ca = TaxTransaction(**base, quantity=-50, net_amount=500.25,
                            fee=-0.25, type=TRADE_CANCEL_TYPE)
        kept, err = _quiet(cancel_trade_pairs, [order, ca])
        self.assertEqual([(t.quantity, round(t.net_amount, 6))
                          for t in kept], [(450, 4502.25)])
        self.assertNotIn("none of this account's inputs", err)

    def test_two_candidate_orders_stay_unmatched(self):
        from taxjson.lib.trade_cancel import (TRADE_CANCEL_TYPE,
                                              pair_cancellations)
        base = dict(action='BUYSELL', date='2025-03-03', time='10:00:00',
                    symbol='QZK.US', currency='USD', price=10.0,
                    account='m')
        rows = [dict(base, quantity=440, net_amount=4400.0),
                dict(base, quantity=300, net_amount=3000.0),
                dict(base, quantity=-40, net_amount=400.0,
                     type=TRADE_CANCEL_TYPE)]
        kept, pairs, unmatched = pair_cancellations(rows)
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(len(kept), 3)

    def test_unrefunded_commission_stays_with_the_order(self):
        """2026-10 audit #29: the kept part is the order less the
        cancellation's own money; a commission IB did not refund stays
        whole (pro rata only for a figure the cancellation lacks)."""
        from taxjson.lib.trade_cancel import (TRADE_CANCEL_TYPE,
                                              pair_cancellations)
        base = dict(action='BUYSELL', date='2025-03-03', time='10:00:00',
                    symbol='QZK.US', currency='USD', price=10.0,
                    account='m')
        rows = [dict(base, quantity=20, net_amount=-205.0, commission=-5.0),
                dict(base, quantity=-10, net_amount=100.0, commission=0.0,
                     type=TRADE_CANCEL_TYPE)]
        partials = []
        kept, _pairs, _un = pair_cancellations(rows, partials=partials)
        self.assertEqual(len(partials), 1)
        (k,) = kept
        self.assertEqual((k['quantity'], k['net_amount'], k['commission']),
                         (10, -105.0, -5.0))
        # A cancellation carrying no commission: scaled, as before.
        rows[1].pop('commission')
        kept, _p, _u = pair_cancellations(rows)
        self.assertEqual(kept[0]['commission'], -2.5)

    def test_same_time_order_beats_an_exact_fill_at_another_time(self):
        """2026-10 audit #62: a Ca at 10:00 names an execution of the
        100-share order at 10:00, not the 40-share fill at 09:00."""
        from taxjson.lib.trade_cancel import (TRADE_CANCEL_TYPE,
                                              pair_cancellations)
        base = dict(action='BUYSELL', date='2025-03-03', symbol='QZK.US',
                    currency='USD', price=10.0, account='m')
        rows = [dict(base, time='09:00:00', quantity=40, net_amount=401.0),
                dict(base, time='10:00:00', quantity=100, net_amount=1005.0),
                dict(base, time='10:00:00', quantity=-40, net_amount=402.0,
                     type=TRADE_CANCEL_TYPE)]
        kept, pairs, unmatched = pair_cancellations(rows)
        self.assertEqual(unmatched, [])
        self.assertEqual(pairs, [])
        self.assertEqual(sorted((t['time'], t['quantity'], t['net_amount'])
                                for t in kept),
                         [('09:00:00', 40, 401.0), ('10:00:00', 60, 603.0)])
        # With the fill at the cancellation's time, the exact fill wins.
        rows[0]['time'] = '10:00:00'
        kept, pairs, _u = pair_cancellations(rows)
        self.assertEqual(len(pairs), 1)
        self.assertEqual([t['quantity'] for t in kept], [100])


if __name__ == '__main__':
    unittest.main()
