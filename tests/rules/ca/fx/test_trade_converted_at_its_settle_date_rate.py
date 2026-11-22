"""Low-round engine pins (fixl/engine-tests): end-to-end assertions on
engine output fields and boundaries that a mutant could change with
every other test still passing. Each class names the audit finding.

Synthetic data only; account labels are fake.

Formerly: test_fix_l_engine_pins
"""
import unittest
from decimal import Decimal
from taxjson.model.transaction import TaxTransaction
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


class TestConvertCurrencySettleDateKey(unittest.TestCase):
    """S028-12: convert-currency prices a trade at its SETTLE date's rate.

    Formerly: test_fix_l_engine_pins.TestConvertCurrencySettleDateKey"""

    @rule(R.CA_FX_01)
    def test_dec31_trade_settling_january_uses_the_settle_rate(self):
        from taxjson.bin.taxjson_convert_currency import convert_transaction
        history = {'USD': {'2025-12-31': Decimal('1.37'),
                           '2026-01-02': Decimal('1.40')}}
        tx = T('BUYSELL', '2025-12-31', 'XYZ.US', -100, 10000, price=100,
               settle='2026-01-02', cur='USD')
        out = convert_transaction(tx, 'CAD', history, Decimal('1.0'))
        self.assertEqual(out.currency, 'CAD')
        self.assertAlmostEqual(out.net_amount, 14000.0, places=6)
        self.assertAlmostEqual(out.price, 140.0, places=6)


if __name__ == '__main__':
    unittest.main()
