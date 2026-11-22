"""Low-round engine pins (fixl/engine-tests): end-to-end assertions on
engine output fields and boundaries that a mutant could change with
every other test still passing. Each class names the audit finding.

Synthetic data only; account labels are fake.

Formerly: test_fix_l_engine_pins
"""
import contextlib
import copy
import io
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


def sales(res):
    return [t for t in res['transactions'] if 'gain' in t]


class TestUsChunkFeeApportionment(unittest.TestCase):
    """S070-15: a FIFO sale across two lots splits its commission by each
    chunk's quantity.

    Formerly: test_fix_l_engine_pins.TestUsChunkFeeApportionment"""

    @rule(R.US_BASIS_03)
    def test_two_chunks(self):
        res = run([U('BUYSELL', '2025-01-06', 'XYZ.US', 100, 1000),
                   U('BUYSELL', '2025-01-07', 'XYZ.US', 100, 1100),
                   U('BUYSELL', '2025-03-03', 'XYZ.US', -120, 1470,
                     price=12.5, commission=30, fee=6)],
                  2025, country='usa')
        chunks = sorted(sales(res), key=lambda s: -s['qty'])
        self.assertEqual([c['qty'] for c in chunks], [100.0, 20.0])
        self.assertEqual([c['commission'] for c in chunks], [25.0, 5.0])
        self.assertEqual([c['fee'] for c in chunks], [5.0, 1.0])
        self.assertEqual([c['proceeds'] for c in chunks], [1225.0, 245.0])


if __name__ == '__main__':
    unittest.main()
