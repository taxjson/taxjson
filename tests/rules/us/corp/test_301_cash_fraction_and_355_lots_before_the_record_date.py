"""Regression tests for fix round 7 (wave C), group engine: the engines
(lib/core.py, lib/corp_actions.py, lib/futures.py).

TJ-0947 an option held at an unknown cost (an OPENING cost=unknown),
        exercised: its shares were booked at the strike alone, a
        definite gain; their cost is unknown too.
TJ-0948 a taxable spin-off's fraction paid in cash was left out of the
        dividend, and the broker's $0 fraction diluted the new shares'
        cost.
TJ-0949 two broker accounts' exports in one folder: a short future
        opened in one closed the long held in the other.
TJ-0951 a US §355 spin-off allocated basis from lots bought after its
        record date.
TJ-0952 buying back a warrant written short was named as a
        superficial-loss replacement.
TJ-0953 a US short sale covered at a loss in December and delivered in
        January was the earlier year's loss.
TJ-0954 a warrant exercise with no shares per warrant stated, delivered
        in several rows: the first row took all the warrants' cost.
TJ-0956 a US return of capital beyond many small lots' basis: each
        lot's sub-cent excess was left as negative basis.

Every fixture is synthetic: invented tickers (SYN*), account labels
and amounts.

Formerly: test_fix_1029_r7c_engine
"""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R


def _gains(book, country, year=2025):
    """pipeline.run_gains under one country (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    with contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        return run_gains(list(book), [], [], req=GainsRequest(
            country=country, taxable=True, year=year,
            phantom_hint=False))


# ---------------------------------------------------------------- TJ-0948
class TestTaxableSpinoffFraction(unittest.TestCase):
    """A §301 spin-off delivering 10 SYNS and paying 10 for 0.5 more
    (fmv_per_share 20): the distribution is 210, the 10.5 shares cost
    210 and the fraction is sold for the cash (Canada's
    taxable_deemed_dividend: test_spinoff_fraction_sale).

    Formerly: test_fix_1029_r7c_engine.TestTaxableSpinoffFraction"""

    @rule(R.US_CORP_06)
    def test_us_301_counts_and_sells_the_fraction(self):
        from taxjson.lib.corp_actions import CorporateAction, resolve_event
        ev = CorporateAction(
            date='2025-06-16', time='09:30:00', action_type='spinoff',
            source_symbol='SYNP.US', source_isin='SYNP.US',
            target_symbol='SYNS.US', target_isin='SYNS.US',
            ratio_new=10, ratio_old=105, qty_disposed=0.0,
            qty_received=10.0, fmv=0.0, currency='USD',
            target_currency='USD', account='margin', cash_in_lieu=10.0,
            cash_in_lieu_currency='USD', fraction_in_lieu=0.5,
            cash_in_lieu_date='2025-06-19')
        with contextlib.redirect_stderr(io.StringIO()):
            rows = resolve_event(ev, 'taxable_distribution_301', 'usa',
                                 {'fmv_per_share': 20.0})
        self.assertEqual(
            [(r['action'], r.get('quantity'), r['net_amount'])
             for r in rows],
            [('DIVIDEND', 0.0, 210.0), ('BUYSELL', 10.5, 210.0),
             ('BUYSELL', -0.5, 10.0)])
        self.assertEqual(rows[2]['date'], '2025-06-19')


# ---------------------------------------------------------------- TJ-0951
class TestUs355EntitledLots(unittest.TestCase):
    """100 SYNP bought 2024-01-08 for 100 and 100 more 2025-06-10 for
    300; a §355 spin-off with record date 2025-06-02 pays 10 SYNS on
    2025-06-16, allocated 10. Only the old lot is entitled: it keeps 90
    and the 10 SYNS cost 10 with its 2024 date; the June lot keeps 300.
    Selling the old 100 for 200 and the 10 SYNS for 100: 200, all long
    term.

    Formerly: test_fix_1029_r7c_engine.TestUs355EntitledLots"""

    def _rows(self, record_date):
        from taxjson.lib.tx_load import coerce_transaction_row
        from taxjson.lib.corp_actions import CorporateAction, resolve_event
        ev = CorporateAction(
            date='2025-06-16', time='09:30:00', action_type='spinoff',
            source_symbol='SYNP.US', source_isin='SYNP.US',
            target_symbol='SYNS.US', target_isin='SYNS.US',
            ratio_new=10, ratio_old=100, qty_disposed=0.0,
            qty_received=10.0, fmv=0.0, currency='USD',
            target_currency='USD', account='margin',
            record_date=record_date)
        with contextlib.redirect_stderr(io.StringIO()):
            rows = resolve_event(ev, 'tax_free_355', 'usa',
                                 {'allocated_acb': 10.0})
        return [coerce_transaction_row(r, i, 'spin')
                for i, r in enumerate(rows)]

    def _run(self, record_date):
        book = [tx('BUYSELL', '2024-01-08', 'SYNP.US', 100, 100),
                tx('BUYSELL', '2025-06-10', 'SYNP.US', 100, 300),
                *self._rows(record_date),
                tx('BUYSELL', '2025-07-02', 'SYNP.US', -100, 200),
                tx('BUYSELL', '2025-07-03', 'SYNS.US', -10, 100)]
        return _gains(book, 'usa')

    @rule(R.US_CORP_07)
    def test_only_lots_before_the_record_date_share_basis(self):
        res = self._run('2025-06-02')
        sales = res['transactions']
        self.assertAlmostEqual(sum(t['gain'] for t in sales), 200.0,
                               places=4)
        self.assertEqual({t['term'] for t in sales}, {'LONG_TERM'})
        inv = {i['symbol']: i for i in res['inventory']}
        self.assertAlmostEqual(inv['SYNP.US']['total_cost'], 300.0,
                               places=4)

    def test_no_record_date_keeps_every_lot_held(self):
        """With no record date stated, the lots held on the pay date
        share it, as before."""
        res = self._run('')
        inv = {i['symbol']: i for i in res['inventory']}
        self.assertAlmostEqual(inv['SYNP.US']['total_cost'], 292.5,
                               places=4)


if __name__ == '__main__':
    unittest.main()
