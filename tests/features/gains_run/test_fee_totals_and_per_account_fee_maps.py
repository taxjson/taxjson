"""Low-round engine pins (fixl/engine-tests): end-to-end assertions on
engine output fields and boundaries that a mutant could change with
every other test still passing. Each class names the audit finding.

Synthetic data only; account labels are fake.

Formerly: test_fix_l_engine_pins
"""
import contextlib
import copy
import io
import json
import unittest
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.pipeline import GainsRequest, run_gains
from tax_rules import rule
from taxjson.rules import ids as R


def T(action, date, sym, qty, net, *, time='10:00:00', settle=None,
      cur='CAD', acct='a', price=None, **kw):
    """One synthetic row; `net` is the fee-inclusive money magnitude."""
    if price is None:
        price = abs(net / qty) if qty else 0.0
    return TaxTransaction(action=action, date=date, time=time,
                          date_settle=settle or date, symbol=sym,
                          quantity=float(qty), price=float(price),
                          net_amount=float(net), currency=cur,
                          account=acct, **kw)


def U(*a, **kw):
    return T(*a, cur='USD', **kw)


def run(book, year, country='canada', sheltered=(), **kw):
    """pipeline.run_gains (what `tjs run` uses) on a taxable book."""
    req = GainsRequest(country=country, year=year, taxable=True, **kw)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(copy.deepcopy(list(book)),
                        copy.deepcopy(list(sheltered)), [], req=req)
    return res


class TestPipelineFeeTotals(unittest.TestCase):
    """G1-10 / S076-11 (pipeline builder): summary.total_fees_by_currency
    on a settle-date project — commission AND fee, the settle-date year
    window (a Dec-30 trade settling in January counts in January's year,
    a Dec-31 one moves out), stocks/options buckets, no empty bucket for
    a fee-free currency — and the `sum` figure it feeds.

    Formerly: test_fix_l_engine_pins.TestPipelineFeeTotals"""

    def test_settle_year_fee_map_and_sum(self):
        book = [
            T('BUYSELL', '2024-06-03', 'ABC.TO', 10, 107, commission=7),
            T('BUYSELL', '2024-12-30', 'XYZ.TO', 100, 1009.99,
              settle='2025-01-02', commission=9.99),
            T('BUYSELL', '2025-06-02', 'XYZ.TO', -50, 594.0,
              commission=4.95, fee=1.05),
            T('BUYSELL', '2025-03-03', 'XYZ250620C00012000.TO', 1, 101.5,
              commission=1.25, fee=0.25),
            T('BUYSELL', '2025-04-01', 'XYZ250620C00012000.TO', -1, 148.5,
              commission=1.25, fee=0.25),
            T('BUYSELL', '2025-12-31', 'XYZ.TO', -10, 117.0,
              settle='2026-01-02', commission=2.50, fee=0.50),
            U('BUYSELL', '2025-05-05', 'QQQ.US', 1, 400.0),
        ]
        res = run(book, 2025)
        fees = res['summary']['total_fees_by_currency']
        self.assertEqual(set(fees), {'CAD'})
        cad = fees['CAD']
        self.assertAlmostEqual(cad['stocks'], 15.99, places=9)
        self.assertAlmostEqual(cad['options'], 3.00, places=9)
        self.assertAlmostEqual(cad['total'], 18.99, places=9)
        from taxjson.lib.gain_summary import summarize_gains
        s = summarize_gains(json.loads(json.dumps(res)))
        self.assertAlmostEqual(s['total_fees']['CAD'], 18.99, places=9)
        self.assertAlmostEqual(s['option_fees']['CAD'], 3.00, places=9)


def _split(combined, account, base):
    from taxjson.bin.taxjson_split_gains import split_for_account
    return split_for_account(combined, account, base)


def _brow(date, sym, qty, commission=0.0, fee=0.0, settle=None,
          action='BUYSELL'):
    return {'action': action, 'date': date, 'date_settle': settle or date,
            'time': '10:00:00', 'symbol': sym, 'quantity': qty,
            'price': 10.0, 'net_amount': abs(qty) * 10.0,
            'commission': commission, 'fee': fee, 'currency': 'CAD',
            'account': 'a'}


class TestSplitGainsFeeMap(unittest.TestCase):
    """S050-10 / S076-11 (split builder): a blended account's fee map is
    rebuilt from its base book for the target year only, commission plus
    fee, on the tax_date basis.

    Formerly: test_fix_l_engine_pins.TestSplitGainsFeeMap"""

    def test_year_scoped_commission_plus_fee(self):
        combined = {'transactions': [], 'inventory': [],
                    'summary': {'year': '2025',
                                'tax_date_basis': 'settle'}}
        base = [
            _brow('2024-05-01', 'XYZ.TO', 10, commission=9.99),
            _brow('2025-02-03', 'XYZ.TO', 10, commission=3.95, fee=1.00),
            _brow('2025-03-03', 'XYZ.TO', -10, commission=3.95, fee=1.00),
            # Traded 2024-12-31, settles 2025-01-02: 2025 on this basis.
            _brow('2024-12-31', 'XYZ250620C00012000.TO', 1,
                  commission=1.25, settle='2025-01-02'),
            # Traded 2025-12-31, settles in 2026: not 2025.
            _brow('2025-12-31', 'XYZ.TO', 5, commission=4.00,
                  settle='2026-01-02'),
        ]
        fees = _split(combined, 'a', base)['summary']['total_fees_by_currency']
        self.assertEqual(set(fees), {'CAD'})
        self.assertAlmostEqual(fees['CAD']['stocks'], 9.90, places=9)
        self.assertAlmostEqual(fees['CAD']['options'], 1.25, places=9)
        self.assertAlmostEqual(fees['CAD']['total'], 11.15, places=9)


@rule(R.CA_ACB_01)
class TestSplitGainsBlendedInventoryApportioned(unittest.TestCase):
    """S050-22: an account-less (s.47 blended) inventory row is split by
    each account's own units at the blended ACB per share.

    Formerly: test_fix_l_engine_pins.TestSplitGainsBlendedInventoryApportioned"""

    def test_two_accounts_share_one_pool(self):
        combined = {'transactions': [], 'summary': {'year': '2025'},
                    'inventory': [{'symbol': 'XYZ.TO', 'qty': 150.0,
                                   'total_cost': 1500.0, 'currency': 'CAD'}]}
        base_a = [_brow('2025-02-03', 'XYZ.TO', 100)]
        base_b = [dict(_brow('2025-02-04', 'XYZ.TO', 50), account='b')]
        inv_a = _split(combined, 'a', base_a)['inventory']
        inv_b = _split(combined, 'b', base_b)['inventory']
        self.assertEqual(len(inv_a), 1)
        self.assertEqual((inv_a[0]['qty'], inv_a[0]['total_cost']),
                         (100.0, 1000.0))
        self.assertEqual((inv_b[0]['qty'], inv_b[0]['total_cost']),
                         (50.0, 500.0))
        self.assertEqual(inv_a[0]['account'], 'a')


if __name__ == '__main__':
    unittest.main()
