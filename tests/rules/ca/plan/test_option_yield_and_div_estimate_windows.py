"""The 2026-10 result and formatting audit: ten result-changing defects
(R01-R10) and three formatting ones (F01-F03), each pinned here on
synthetic books. R01 changes a filing figure (CA-CORP-13 / US-CORP-14);
the rest are the analytics and planning views.

Formerly: test_fix_result_audit_2026_10"""
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R

from taxjson.lib import div_estimate as D
from taxjson.lib import option_yield as O

C = 'ZZQ250620C00010000.US'
N = 'ZZR250620C00005000.US'


def row(d, q, net, sym=C, action='BUYSELL', acct='a', **kw):
    return tx(action, d, sym, q, net, account=acct, **kw).to_dict()


def oy(rows, end='2025-12-31', **kw):
    return O.analyze(O.contracts([(r['account'], r) for r in rows]),
                     start='2025-01-01', end=end, scope='audit', **kw)


def div(d, q, gross):
    return ('a', row(d, q, gross, sym='ZZQ.US', action='DIVIDEND'))


def de(rows, months=1):
    return D.estimate([dict(account='a', symbol='ZZQ.US', qty=10,
                            group='taxable')], D.payments(rows),
                      as_of='2025-02-01', months=months, tax_year=2025,
                      base='USD', fx=lambda cur: 1)['rows']


class TestOptionYield(unittest.TestCase):
    """Formerly: test_fix_result_audit_2026_10.TestOptionYield"""
    @rule(R.CA_PLAN_07)
    def test_R05_renamed_option_is_found_by_new_underlying(self):
        rs = [row('2025-01-01', -1, 100),
              row('2025-02-01', 2, 0, action='SPLIT', symbol_new=N),
              row('2025-06-20', 2, 0, sym=N)]
        r = oy(rs, underlyings=['ZZR'])
        self.assertEqual(sum(t['realized'] for t in r['rows']), 100)
        self.assertEqual(r['unmatched_underlyings'], [])
        self.assertEqual(r['closed'][0]['symbol'], N)

    @rule(R.CA_PLAN_07)
    def test_R06_unknown_premium_must_not_be_reported_as_known_loss(self):
        rs = [row('2025-01-01', -1, 0, action='OPENING_BALANCE'),
              row('2025-02-01', 1, 40)]
        r = oy(rs)
        self.assertIsNone(r['closed'][0]['pnl'])
        self.assertIsNone(r['totals']['USD']['realized'])
        self.assertIsNone(r['rows'][0]['win_rate'])
        self.assertEqual(r['rows'][0]['paid'], 40.0)
        text = O.render(r).text()
        self.assertIn("unknown: 1 of the 1 contract(s) closed had no "
                      "premium in the books", text)

    @rule(R.CA_PLAN_07)
    def test_R07_mixed_capital_total_keeps_uncovered_exposure(self):
        rs = [row('2025-01-01', -1, 100), row('2025-02-01', 1, 0),
              row('2025-01-01', -1, 100, sym=N),
              row('2025-02-01', 1, 0, sym=N)]
        r = oy(rs, shares={('a', 'ZZQ.US'): (10, 100)})
        expected = round(200 / (100 + 500 * 31 / 365), 6)
        t = r['totals']['USD']
        self.assertEqual(t['yield'], expected)
        self.assertEqual(t['yield_basis'], 'mixed')
        self.assertEqual(t['notional_capital'], round(500 * 31 / 365, 2))
        self.assertIn("of capital (the 100.00 cost of 10 share(s) held, "
                      "and an average strike notional of",
                      O.render(r).text())

    @rule(R.CA_PLAN_07)
    def test_R10_future_split_cannot_rewrite_historical_counts(self):
        rs = [row('2025-01-01', -2, 200), row('2025-01-10', 1, 0)]
        before = oy(rs, end='2025-01-31')['rows'][0]
        rs += [row('2025-02-01', 2, 0, action='SPLIT', symbol_new=N)]
        after = oy(rs, end='2025-01-31')
        keys = ('written_contracts', 'closed_contracts', 'open_contracts')
        self.assertEqual(tuple(before[k] for k in keys),
                         tuple(after['rows'][0][k] for k in keys))
        self.assertEqual(after['closed'][0]['symbol'], C)
        # After the split the same lot counts in the new units.
        later = oy(rs, end='2025-02-28')['rows'][0]
        self.assertEqual((later['written_contracts'],
                          later['closed_contracts'],
                          later['open_contracts']), (4.0, 2.0, 2.0))

    def test_F03_closed_and_open_rows_name_their_currency(self):
        rs = [row('2025-01-01', -1, 100, currency='USD'),
              row('2025-06-20', 1, 0, currency='USD'),
              row('2025-01-01', -1, 200, acct='b', currency='CAD'),
              row('2025-06-20', 1, 0, acct='b', currency='CAD'),
              row('2025-07-01', -1, 50, acct='b', currency='CAD')]
        text = O.render(oy(rs)).text()
        closed = [ln for ln in text.splitlines() if ln.startswith("2025-06-20")]
        self.assertEqual(len(closed), 2)
        self.assertTrue(any(" USD " in ln for ln in closed))
        self.assertTrue(any(" CAD " in ln for ln in closed))
        opened = [ln for ln in text.splitlines() if ln.startswith("2025-07-01")]
        self.assertIn(" CAD ", opened[0])


class TestDivEstimate(unittest.TestCase):
    """Formerly: test_fix_result_audit_2026_10.TestDivEstimate"""
    @rule(R.CA_PLAN_06)
    def test_R08_rest_of_year_does_not_shrink_with_forward_window(self):
        rs = [div('2024-12-15', 10, 10), div('2025-01-15', 10, 10)]
        one, twelve = de(rs, 1)[0], de(rs, 12)[0]
        self.assertEqual(one['rest_of_year_amount'],
                         twelve['rest_of_year_amount'])
        self.assertEqual(one['rest_of_year_amount'], 110.0)
        # The window itself is still the window asked for.
        self.assertEqual((one['window_amount'], len(one['projected'])),
                         (10.0, 1))
        self.assertEqual(twelve['window_amount'], 120.0)

    @rule(R.CA_PLAN_06)
    def test_R09_future_reversal_cannot_change_as_of_estimate(self):
        rs = [div('2024-12-15', 10, 10), div('2025-01-15', 10, 10)]
        future = div('2025-03-15', 10, -10)
        self.assertEqual(de(rs)[0]['window_amount'],
                         de(rs + [future])[0]['window_amount'])
        self.assertEqual(de(rs + [future])[0]['frequency'], 'monthly')
        # A share count dated after the as-of date is not borrowed: a
        # payer whose only payment names no count stays unestimated.
        rs2 = [div('2025-01-15', 0, 10)]
        self.assertEqual(de(rs2), [])
        rs3 = rs2 + [div('2025-03-15', 10, 10)]
        self.assertEqual(de(rs3), [])
        # The cutoff is explicit too.
        self.assertEqual(D.payments(rs3, as_of='2025-02-01'), {})
        self.assertEqual(len(D.payments(rs3)['ZZQ.US']), 2)


if __name__ == '__main__':
    unittest.main()
