"""The 2026-10 result and formatting audit: ten result-changing defects
(R01-R10) and three formatting ones (F01-F03), each pinned here on
synthetic books. R01 changes a filing figure (CA-CORP-13 / US-CORP-14);
the rest are the analytics and planning views.

Formerly: test_fix_result_audit_2026_10"""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R

from taxjson.bin.taxjson_t1135 import walk_costs
from taxjson.lib import trade_stats as S
from taxjson.lib.pipeline import GainsRequest, run_gains

C = 'ZZQ250620C00010000.US'
N = 'ZZR250620C00005000.US'


def row(d, q, net, sym=C, action='BUYSELL', acct='a', **kw):
    return tx(action, d, sym, q, net, account=acct, **kw).to_dict()


class TestFilingFigures(unittest.TestCase):
    """Formerly: test_fix_result_audit_2026_10.TestFilingFigures"""
    @rule(R.CA_CORP_13)
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

    @rule(R.CA_RPT_12)
    def test_R02_t1135_trade_basis_uses_trade_date_weights(self):
        """A purchase traded before the merger but settling after it
        weighed on a trade-basis project's replay. The walk reads the
        engine's pools now (backlog TJ-0417), and the engine refuses a
        rename inside a trade's settlement lag, as `tjs run` does."""
        from taxjson.engine.ca.taxrules import SplitStraddlesSettlementError
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
    """Formerly: test_fix_result_audit_2026_10.TestStats"""
    @rule(R.CA_RPT_16)
    def test_R03_signed_buyback_is_money_paid(self):
        rs = [row('2025-01-01', -1, 100), row('2025-02-01', 1, -40)]
        r = S.written_option_trades(rs, lambda t: t['date'])
        self.assertEqual(r['trades'][0]['pnl'], 60)

    @rule(R.CA_RPT_16)
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


if __name__ == '__main__':
    unittest.main()
