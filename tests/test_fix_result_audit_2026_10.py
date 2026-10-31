"""The 2026-10 result and formatting audit: ten result-changing defects
(R01-R10) and three formatting ones (F01-F03), each pinned here on
synthetic books. R01 changes a filing figure (CA-CORP-13 / US-CORP-14);
the rest are the analytics and planning views."""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx

from taxjson.bin.taxjson_t1135 import walk_costs
from taxjson.lib import div_estimate as D
from taxjson.lib import option_yield as O
from taxjson.lib import tax_plot as TP
from taxjson.lib import trade_stats as S
from taxjson.lib.pipeline import GainsRequest, run_gains

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


class TestFilingFigures(unittest.TestCase):
    @rule("CA-CORP-13")
    def test_R01_merger_after_earlier_rename_conserves_shares_and_basis(self):
        """A 1:1 rename, then a merger with per-account ratios: the
        weights follow the shares renamed into the merger's source."""
        b = [tx('BUYSELL', '2024-01-01', 'ZZPRE.US', 10, 100, account=a, id=a)
             for a in ('a', 'b')]
        b += [tx('SPLIT', '2024-06-01', 'ZZPRE.US', 1, 0,
                 symbol_new='ZZOLD.US', id='pre')]
        b += [tx('SPLIT', '2025-06-03', 'ZZOLD.US', ratio, 0,
                 account=a, symbol_new='ZZNEW.US', id='split-' + a)
              for a, ratio in [('a', 1), ('b', 2)]]
        b += [tx('BUYSELL', '2025-07-01', 'ZZNEW.US', -15, 150,
                 account='b', id='sale')]
        with contextlib.redirect_stderr(io.StringIO()):
            r = run_gains(b, req=GainsRequest(country='canada', year=2025,
                                             taxable=True))
        self.assertEqual((sum(i['qty'] for i in r['inventory']),
                          sum(i['total_cost'] for i in r['inventory']),
                          sum(t.get('gain', 0) for t in r['transactions'])),
                         (15, 100, 50))

    @rule("CA-RPT-12")
    def test_R02_t1135_trade_basis_uses_trade_date_weights(self):
        """A purchase traded before the merger but settling after it
        weighed on a trade-basis project's replay. The walk reads the
        engine's pools now (backlog TJ-0417), and the engine refuses a
        rename inside a trade's settlement lag, as `tjs run` does."""
        from taxjson.lib.core import SplitStraddlesSettlementError
        b = [tx('BUYSELL', '2025-01-01', 'ZZOLD.US', 10, 100, account='b', id='b'),
             tx('BUYSELL', '2025-06-02', 'ZZOLD.US', 10, 100, account='a',
                settle='2025-06-04', id='a')]
        b += [tx('SPLIT', '2025-06-03', 'ZZOLD.US', ratio, 0,
                 account=a, symbol_new='ZZNEW.US', id='split-' + a)
              for a, ratio in [('a', 1), ('b', 2)]]
        b += [tx('BUYSELL', '2025-07-01', 'ZZNEW.US', -15, 150, id='sale')]
        with contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SplitStraddlesSettlementError):
            walk_costs([t.to_dict() for t in b], 2025, {}, tax_date='trade')


class TestStats(unittest.TestCase):
    @rule("CA-RPT-16")
    def test_R03_signed_buyback_is_money_paid(self):
        rs = [row('2025-01-01', -1, 100), row('2025-02-01', 1, -40)]
        r = S.written_option_trades(rs, lambda t: t['date'])
        self.assertEqual(r['trades'][0]['pnl'], 60)

    @rule("CA-RPT-16")
    def test_R04_stats_keeps_closes_after_option_split(self):
        rs = [row('2025-01-01', -1, 100),
              row('2025-02-01', 2, 0, action='SPLIT', symbol_new=N),
              row('2025-06-20', 2, 0, sym=N)]
        r = S.written_option_trades(rs, lambda t: t['date'])
        self.assertEqual(sum(t['pnl'] for t in r['trades']), 100)
        self.assertEqual(r['trades'][0]['contracts'], 2)
        # The same split reported by two rows is one event.
        rs2 = rs[:2] + [row('2025-02-01', 2, 0, action='SPLIT',
                            symbol_new=N)] + rs[2:]
        r2 = S.written_option_trades(rs2, lambda t: t['date'])
        self.assertEqual(sum(t['pnl'] for t in r2['trades']), 100)


class TestOptionYield(unittest.TestCase):
    @rule("CA-PLAN-07")
    def test_R05_renamed_option_is_found_by_new_underlying(self):
        rs = [row('2025-01-01', -1, 100),
              row('2025-02-01', 2, 0, action='SPLIT', symbol_new=N),
              row('2025-06-20', 2, 0, sym=N)]
        r = oy(rs, underlyings=['ZZR'])
        self.assertEqual(sum(t['realized'] for t in r['rows']), 100)
        self.assertEqual(r['unmatched_underlyings'], [])
        self.assertEqual(r['closed'][0]['symbol'], N)

    @rule("CA-PLAN-07")
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

    @rule("CA-PLAN-07")
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

    @rule("CA-PLAN-07")
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
    @rule("CA-PLAN-06")
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

    @rule("CA-PLAN-06")
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


class TestPlotTaxFormatting(unittest.TestCase):
    def _rows(self, taxes):
        pts, ct = [], 0.0
        for i, t in enumerate(taxes):
            ct += t
            pts.append({"month": f"2025-{i + 1:02d}", "cum_tax": ct,
                        "cum_income": ct * 4})
        return TP.build(pts, year=2025, base="USD", year_estimate=ct,
                        year_income=ct * 4, label="usa", accounts=[])["rows"]

    def test_F01_cent_sized_levels_are_labelled_in_cents(self):
        lines = TP.chart(self._rows([0.04] * 12), 0.48, 120)
        labels = [ln.split("|")[0].strip() for ln in lines[1:]
                  if "|" in ln]
        self.assertNotIn("0", labels)
        self.assertIn("0.04", labels)

    def test_F02_fits_a_60_column_terminal(self):
        rows = self._rows([10.0] * 12)
        for w in (60, 48, 40):
            lines = TP.chart(rows, 120.0, w)
            self.assertTrue(all(len(ln) <= w for ln in lines),
                            (w, max(len(ln) for ln in lines)))
            self.assertEqual(len(lines[-1].split()), 12)


if __name__ == '__main__':
    unittest.main()
