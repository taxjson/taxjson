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


class TestUsReplacementOrderAcquiredByTime(unittest.TestCase):
    """S070-13: same-date replacements go in order acquired by clock time
    (Reg. 1.1091-1(c)), whatever their ids: the 10:00 taxable lot takes
    the wash, the 14:00 IRA lot does not make it permanent.

    Formerly: test_fix_l_engine_pins.TestUsReplacementOrderAcquiredByTime"""

    def _run(self, tax_time, ira_time):
        book = [U('BUYSELL', '2025-01-06', 'AAA.US', 100, 2000,
                  time='09:30:00', id='b0'),
                U('BUYSELL', '2025-02-03', 'AAA.US', -100, 1000, id='s0'),
                U('BUYSELL', '2025-02-10', 'AAA.US', 100, 1100,
                  time=tax_time, id='z_tax')]
        ira = [U('BUYSELL', '2025-02-10', 'AAA.US', 100, 1100,
                 time=ira_time, id='a_ira', acct='ira')]
        res = run(book, 2025, country='usa', sheltered=ira)
        inv = {i['symbol']: i for i in res['inventory']}
        return res, inv['AAA.US']['total_cost']

    @rule(R.US_WASH_09, R.US_WASH_20)
    def test_taxable_lot_first(self):
        res, basis = self._run('10:00:00', '14:00:00')
        self.assertEqual(basis, 2100.0)
        self.assertEqual(res['summary']['total_gain'], 0.0)

    @rule(R.US_WASH_11, R.US_WASH_20)
    def test_earlier_ira_lot_makes_it_permanent(self):
        # The IRA lot was acquired first (10:00): it takes the wash, the
        # denial is permanent and the 14:00 taxable lot keeps its cost.
        res, basis = self._run('14:00:00', '10:00:00')
        self.assertEqual(basis, 1100.0)
        rec, = sales(res)
        self.assertEqual(rec['permanently_disallowed'], 1000.0)


if __name__ == '__main__':
    unittest.main()
