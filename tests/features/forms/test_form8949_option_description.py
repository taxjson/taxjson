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


class TestUsOptionRecordReachesForm8949(unittest.TestCase):
    """S070-16: the US record's is_option marks the 8949 description.

    Formerly: test_fix_l_engine_pins.TestUsOptionRecordReachesForm8949"""

    def test_option_description(self):
        from taxjson.lib.tax_forms import build_8949
        res = run([U('BUYSELL', '2025-01-06', 'ABC250718C00045000', 2, 400),
                   U('BUYSELL', '2025-03-10', 'ABC250718C00045000', -2, 600),
                   U('BUYSELL', '2025-01-06', 'ABC.US', 10, 100),
                   U('BUYSELL', '2025-03-10', 'ABC.US', -10, 150)],
                  2025, country='usa')
        recs = {s['symbol']: s for s in sales(res)}
        self.assertIs(recs['ABC250718C00045000']['is_option'], True)
        self.assertIs(recs['ABC.US']['is_option'], False)
        desc = sorted(r['description']
                      for r in build_8949(sales(res))['part_I'])
        self.assertEqual(desc, ['10 ABC.US', '2 ABC250718C00045000 (option)'])


if __name__ == '__main__':
    unittest.main()
