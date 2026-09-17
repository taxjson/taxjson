"""Conservation post-conditions: share-count replay and stranded-basis
residue, asserted at the end of both engines. Fabricated violations must
warn; complex-but-correct scenarios must stay silent."""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal

from taxjson.lib.core import (
    CanadaTaxRules,
    TaxTransaction,
    USATaxRules,
    _verify_share_conservation,
    _warn_stranded_basis,
)


def tx(action='BUYSELL', date='2025-03-10', symbol='X.US', qty=0.0,
       symbol_new='', time='09:30:00', price=0.0, net=0.0):
    return TaxTransaction(action=action, date=date, time=time,
                          symbol=symbol, quantity=qty, price=price,
                          net_amount=net, symbol_new=symbol_new,
                          currency='CAD')


def check(rows, actual, zero_ratio_skips=False):
    err = io.StringIO()
    with redirect_stderr(err):
        _verify_share_conservation(rows, actual, 'test',
                                   zero_ratio_skips=zero_ratio_skips)
    return err.getvalue()


class TestShareCountChecker(unittest.TestCase):
    ROWS = [tx(qty=100), tx(action='SPLIT', date='2025-04-01', qty=2.0)]

    def test_correct_inventory_is_silent(self):
        self.assertEqual(check(self.ROWS, {'X.US': 200.0}), '')

    def test_double_applied_split_warns(self):
        err = check(self.ROWS, {'X.US': 400.0})
        self.assertIn('conservation: test share-count mismatch', err)
        self.assertIn('X.US', err)

    def test_rename_chain_aliases_collapse(self):
        rows = [
            tx(symbol='OLD.TO', qty=100),
            tx(action='SPLIT', date='2025-04-01', symbol='OLD.TO',
               qty=1.0, symbol_new='NEW.TO'),
            tx(date='2025-05-01', symbol='NEW.TO', qty=50),
        ]
        # Inventory keyed either way maps through the rename.
        self.assertEqual(check(rows, {'NEW.TO': 150.0}), '')
        self.assertEqual(check(rows, {'OLD.TO': 100.0, 'NEW.TO': 50.0}), '')
        self.assertIn('mismatch', check(rows, {'NEW.TO': 100.0}))

    def test_zero_ratio_mirrors_each_engine(self):
        rows = [tx(qty=100),
                tx(action='SPLIT', date='2025-04-01', qty=0.0)]
        # US skips a ratio-0 SPLIT entirely: position stays 100.
        self.assertEqual(check(rows, {'X.US': 100.0},
                               zero_ratio_skips=True), '')
        # Canada applies it blindly: position becomes 0.
        self.assertEqual(check(rows, {'X.US': 0.0},
                               zero_ratio_skips=False), '')


class TestStrandedBasisChecker(unittest.TestCase):
    def _pools(self, qty, cost):
        return {'X.US': {'qty': qty, 'total_cost': Decimal(str(cost))}}

    def _run(self, pools):
        err = io.StringIO()
        with redirect_stderr(err):
            _warn_stranded_basis(pools)
        return err.getvalue()

    def test_empty_pool_with_cost_warns(self):
        self.assertIn('stranded basis', self._run(self._pools(0.0, 500.0)))

    def test_live_pool_and_clean_drain_silent(self):
        self.assertEqual(self._run(self._pools(10.0, 500.0)), '')
        self.assertEqual(self._run(self._pools(0.0, 0.001)), '')

    def test_shared_object_counted_once(self):
        pool = {'qty': 0.0, 'total_cost': Decimal('500')}
        err = self._run({'OLD.TO': pool, 'NEW.TO': pool})
        self.assertEqual(err.count('stranded basis'), 1)


class TestEndToEndSilence(unittest.TestCase):
    """Complex-but-correct histories must produce no conservation
    warnings from either engine."""

    def _gains(self, rules, txs):
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            rules.compute_gains(txs)
        return [l for l in err.getvalue().splitlines()
                if 'conservation:' in l]

    SCENARIO = [
        tx(date='2025-01-10', qty=100, price=10.0, net=1000.0),
        tx(action='SPLIT', date='2025-02-01', qty=2.0),
        tx(date='2025-03-01', qty=-50, price=6.0, net=300.0),
        #

        # Rename with a ratio, then trade the new ticker.
        tx(action='SPLIT', date='2025-04-01', qty=0.5,
           symbol_new='Y.US'),
        tx(date='2025-05-01', symbol='Y.US', qty=25, price=20.0,
           net=500.0),
        tx(date='2025-06-01', symbol='Y.US', qty=-30, price=21.0,
           net=630.0),
        # A short position on another symbol.
        tx(date='2025-06-15', symbol='S.US', qty=-40, price=5.0,
           net=200.0),
        # Phantom opening on a third.
        tx(action='OPENING_BALANCE', date='2025-01-05', symbol='P.US',
           qty=30),
        tx(date='2025-07-01', symbol='P.US', qty=-30, price=4.0,
           net=120.0),
    ]

    def test_canada_silent(self):
        self.assertEqual(self._gains(CanadaTaxRules(), self.SCENARIO), [])

    def test_usa_silent(self):
        self.assertEqual(self._gains(USATaxRules(), self.SCENARIO), [])


class TestRenameWashDeferral(unittest.TestCase):
    """FIXED (was a pinned known-bug found by the conservation check the
    first time it ran): the deferral ADJUST is now keyed to the TRIGGER's
    symbol, so a superficial loss whose repurchase sits across a merger
    rename (SSL.TO loss -> rename -> RGLD.US buy) lands the denied amount
    on the replacement's ACB instead of recreating a dead pool under the
    old ticker and stranding it forever."""

    def test_wash_deferral_across_rename_reaches_replacement(self):
        txs = [
            tx(date='2025-01-15', symbol='SSL.TO', qty=100, price=20.0,
               net=2000.0),
            tx(date='2025-06-15', symbol='SSL.TO', qty=-100, price=10.0,
               net=1000.0),
            tx(action='SPLIT', date='2025-06-20', symbol='SSL.TO',
               qty=0.0625, symbol_new='RGLD.US'),
            tx(date='2025-07-01', symbol='RGLD.US', qty=10, price=200.0,
               net=2000.0),
        ]
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            res = CanadaTaxRules().compute_gains(txs)
        self.assertNotIn('stranded basis', err.getvalue())
        # Replacement carries purchase cost + the deferred $1,000 loss.
        rgld = next(h for h in res['inventory']
                    if h['symbol'] == 'RGLD.US')
        self.assertAlmostEqual(rgld['total_cost'], 3000.0, places=2)


if __name__ == '__main__':
    unittest.main()
