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
from taxjson.lib.numeric import round_floats
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


class TestGainsJsonRounding(unittest.TestCase):
    """R1-317 (part 2): gains JSON money is rounded to 4 dp, ROUND_HALF_UP;
    quantity keys pass through. The old test compared with places=4,
    which the unrounded value also passes.

    Formerly: test_fix_l_engine_pins.TestGainsJsonRounding"""

    def test_four_dp_half_up_exact(self):
        out = round_floats({'gain': 100.123456789, 'cost': 1.23465,
                            'proceeds': 2.00005, 'fee': -0.00005,
                            'qty': 100.123456789,
                            'nested': [{'price': 0.00001234}]})
        self.assertEqual(out['gain'], 100.1235)
        self.assertEqual(out['cost'], 1.2347)       # half-even: 1.2346
        self.assertEqual(out['proceeds'], 2.0001)   # half-even: 2.0000
        self.assertEqual(out['fee'], -0.0001)
        self.assertEqual(out['qty'], 100.123456789)
        self.assertEqual(out['nested'][0]['price'], 0.0)


class TestCanadaOptionRecordUnits(unittest.TestCase):
    """S070-02: a Canada option record's qty is contracts (Schedule 3
    units), not shares; share rows unchanged.

    Formerly: test_fix_l_engine_pins.TestCanadaOptionRecordUnits"""

    def test_option_and_share_units(self):
        res = run([
            T('BUYSELL', '2025-01-06', 'ABC250718C00045000.TO', 2, 400),
            T('BUYSELL', '2025-03-10', 'ABC250718C00045000.TO', -2, 600),
            T('BUYSELL', '2025-01-06', 'ABC.TO', 100, 1000),
            T('BUYSELL', '2025-03-10', 'ABC.TO', -100, 1500)], 2025)
        q = {s['symbol']: s['qty'] for s in sales(res)}
        self.assertEqual(q, {'ABC250718C00045000.TO': 2.0, 'ABC.TO': 100.0})
        s3 = schedule3(res, 2025)
        self.assertEqual(s3['ABC250718C00045000.TO']['units'], 2.0)
        self.assertEqual(s3['ABC.TO']['units'], 100.0)


class TestSplitAtTheTradesOwnTimestamp(unittest.TestCase):
    """S071-05: a trade stamped exactly at the split's (date, time) is
    already in post-split units and is not re-denominated.

    Formerly: test_fix_l_engine_pins.TestSplitAtTheTradesOwnTimestamp"""

    @rule(R.CA_CORP_01)
    def test_ex_date_trade_at_split_time(self):
        res = run([
            T('BUYSELL', '2025-01-06', 'XYZ.TO', 100, 2000, time='09:30:00',
              id='b0'),
            T('SPLIT', '2025-06-10', 'XYZ.TO', 2.0, 0, time='09:30:00',
              price=0, id='sp'),
            T('BUYSELL', '2025-06-10', 'XYZ.TO', 100, 1200, time='09:30:00',
              settle='2025-06-11', id='b1'),
            T('BUYSELL', '2025-07-02', 'XYZ.TO', -300, 4500,
              settle='2025-07-03', id='s1')], 2025)
        rec, = sales(res)
        self.assertEqual((rec['qty'], rec['cost'], rec['gain']),
                         (300.0, 3200.0, 1300.0))
        self.assertEqual([i for i in res['inventory']
                          if abs(i.get('qty') or 0) > 1e-9], [])


if __name__ == '__main__':
    unittest.main()
