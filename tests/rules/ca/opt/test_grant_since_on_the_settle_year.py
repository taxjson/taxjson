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


class TestGrantSinceUsesTheWritesSettleYear(unittest.TestCase):
    """S068-22: option_grant_timing_since compares the write's year on the
    tax_date basis — a Dec-31 write settling in the `since` year is
    grant-timed.

    Formerly: test_fix_l_engine_pins.TestGrantSinceUsesTheWritesSettleYear"""

    @rule(R.CA_OPT_01)
    def test_dec31_write_settling_in_since_year(self):
        book = [
            T('BUYSELL', '2025-12-31', 'ABC270115C00030000.TO', -1, 300,
              settle='2026-01-02'),
            T('BUYSELL', '2027-01-05', 'ABC270115C00030000.TO', 1, 400,
              settle='2027-01-06'),
        ]
        kw = dict(option_premium_timing='grant', option_grant_since=2026)
        self.assertEqual(run(book, 2026, **kw)['summary']['total_gain'],
                         300.0)
        self.assertEqual(run(book, 2027, **kw)['summary']['total_gain'],
                         -400.0)


if __name__ == '__main__':
    unittest.main()
