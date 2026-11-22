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


def schedule3(res, year):
    from taxjson.lib.tax_forms import build_schedule3
    rep = build_schedule3(res['transactions'], year)
    return {r['symbol']: r for r in rep['rows']}


class TestDaysHeldDrivesScheduleThreeAcqYear(unittest.TestCase):
    """S069-09: the Canada record's days_held is where form-export's
    Schedule 3 year of acquisition comes from.

    Formerly: test_fix_l_engine_pins.TestDaysHeldDrivesScheduleThreeAcqYear"""

    def test_lot_bought_in_an_earlier_year(self):
        res = run([T('BUYSELL', '2023-03-01', 'XYZ.TO', 100, 1000),
                   T('BUYSELL', '2025-06-02', 'XYZ.TO', -100, 1500)], 2025)
        rec, = sales(res)
        self.assertEqual(rec['days_held'], 824)
        self.assertEqual(schedule3(res, 2025)['XYZ.TO']['acq_year'], '2023')


if __name__ == '__main__':
    unittest.main()
