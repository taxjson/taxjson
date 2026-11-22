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


class TestCanadaFeeApportionmentEndToEnd(unittest.TestCase):
    """R1-302: the per-record commission/fee (fee_share = closing_qty /
    |qty|) feeds Schedule 3 outlays and proceeds; nothing pinned it.

    Formerly: test_fix_l_engine_pins.TestCanadaFeeApportionmentEndToEnd"""

    @rule(R.CA_ACB_02, R.CA_DISP_01)
    def test_record_fees_and_schedule3_outlays(self):
        book = [
            T('BUYSELL', '2025-01-06', 'XYZ.TO', 100, 5010, price=50,
              commission=10),
            T('BUYSELL', '2025-03-03', 'XYZ.TO', -60, 3570, price=60,
              commission=30),
            # A sale that crosses zero: 100 close the long, 50 open a
            # short, so the closing record carries 100/150 of the fees.
            T('BUYSELL', '2025-01-06', 'ABC.TO', 100, 1005, price=10,
              commission=5),
            T('BUYSELL', '2025-03-03', 'ABC.TO', -150, 1482, price=10,
              commission=15, fee=3),
            T('BUYSELL', '2025-04-01', 'ABC.TO', 50, 405, price=8,
              commission=5),
        ]
        res = run(book, 2025)
        recs = {(s['symbol'], s['date']): s for s in sales(res)}
        xyz = recs[('XYZ.TO', '2025-03-03')]
        self.assertEqual(xyz['commission'], 30.0)
        self.assertEqual(xyz['fee'], 0.0)
        self.assertEqual(xyz['proceeds'], 3570.0)
        self.assertEqual(xyz['cost'], 3006.0)
        self.assertEqual(xyz['gain'], 564.0)
        abc = recs[('ABC.TO', '2025-03-03')]
        self.assertAlmostEqual(abc['commission'], 10.0, places=9)
        self.assertAlmostEqual(abc['fee'], 2.0, places=9)
        row = schedule3(res, 2025)['XYZ.TO']
        self.assertEqual((row['proceeds'], row['acb'], row['outlays'],
                          row['gain']), (3600.0, 3006.0, 30.0, 564.0))


if __name__ == '__main__':
    unittest.main()
