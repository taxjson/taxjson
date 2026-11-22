"""Backlog fixes 2026-10-29, fix round 7 wave D, group `ib`: Interactive
Brokers, mostly regressions of this round's earlier fixes.

TJ-1041 (a same-day withdrawal walked before the timed Forex that funded
it), TJ-1042 / TJ-1043 (identical refunds of two accounts in combined
statements), TJ-1044 (a coded short open at the statement end read as a
holding before it opened), TJ-1120 (a JPY future on
CME dated in Tokyo time), TJ-1130 (the overlap step dropped a cancelled
trade's copy whichever account's row it was).
Synthetic data only; account ids are fake (pii-ok).

Formerly: test_fix_1029_r7d_ib
"""
import unittest
from _support.ib_statement_rows import HEAD, TRADES_H, _trade
from _support.ib_brokerage_base import _A, _B, _period, _Y2025, _Brokerage, _buy

_ADJ_H = ('Commission Adjustments,Header,Currency,Account,Date,Description,'
          'Amount\n')


def _adj(acct, day, amount, desc):
    return f'Commission Adjustments,Data,CAD,{acct},{day},"{desc}",{amount}\n'


class TestIdenticalRefundsOfTwoAccounts(_Brokerage):
    """TJ-1042, TJ-1043: two accounts' refunds alike in currency, date,
    amount and wording are two refunds; TJ-0740's rule (a refund folds
    into its own account's trade) stays.

    Formerly: test_fix_1029_r7d_ib.TestIdenticalRefundsOfTwoAccounts"""

    def test_refunds_for_trades_in_another_statement(self):
        # TJ-1042: each account's refund folds into its own trade.
        y25 = (HEAD + _Y2025 + TRADES_H
               + _buy(_A, '2025-12-15, 10:00:00')
               + _buy(_B, '2025-12-15, 10:00:00'))
        y26 = (HEAD + _period('January 1, 2026 - December 31, 2026') + _ADJ_H
               + _adj(_A, '2026-01-10', 1, 'Refund (SYN, 10, 2025-12-15)')
               + _adj(_B, '2026-01-10', 1, 'Refund (SYN, 10, 2025-12-15)'))
        rows, err = self._run({'ib_2025.csv': y25, 'ib_2026.csv': y26})
        self.assertEqual(sorted(t['net_amount'] for t in rows
                                if t['action'] == 'BUYSELL'), [100.0, 100.0],
                         err)
        self.assertNotIn('FEE', [t['action'] for t in rows])

    def test_a_refund_of_one_account_only_is_not_the_others(self):
        y25 = (HEAD + _Y2025 + TRADES_H
               + _buy(_A, '2025-12-15, 10:00:00')
               + _buy(_B, '2025-12-15, 10:00:00'))
        y26 = (HEAD + _period('January 1, 2026 - December 31, 2026') + _ADJ_H
               + _adj(_B, '2026-01-10', 1, 'Refund (SYN, 10, 2025-12-15)'))
        rows, err = self._run({'ib_2025.csv': y25, 'ib_2026.csv': y26})
        self.assertEqual(sorted(t['net_amount'] for t in rows
                                if t['action'] == 'BUYSELL'), [100.0, 101.0],
                         err)
        self.assertNotIn('FEE', [t['action'] for t in rows])

    def _overlap(self, refunds, order=('ib_h1.csv', 'ib_full.csv')):
        sells = ''.join(
            _trade('SYN', '2025-09-02, 10:00:00', -10, 20, 200, 0, code='C',
                   cur='CAD', acct=a) for a in (_A, _B))
        files = {
            'ib_h1.csv': (HEAD + _period('January 1, 2025 - June 30, 2025')
                          + TRADES_H + _buy(_A) + _buy(_B)),
            'ib_full.csv': HEAD + _Y2025 + TRADES_H + _buy(_A) + _buy(_B)
            + sells + _ADJ_H + refunds}
        return self._run({n: files[n] for n in order})

    def test_overlapping_copies_of_both_accounts_get_their_refund(self):
        # TJ-1043: both copies of each purchase end at net 100.
        refunds = (_adj(_A, '2025-02-03', 1, 'Refund (SYN, 10, 2025-01-06)')
                   + _adj(_B, '2025-02-03', 1, 'Refund (SYN, 10, 2025-01-06)'))
        for order in (('ib_h1.csv', 'ib_full.csv'),
                      ('ib_full.csv', 'ib_h1.csv')):
            with self.subTest(order=order):
                rows, err = self._overlap(refunds, order)
                buys = [t['net_amount'] for t in rows
                        if t['action'] == 'BUYSELL' and t['quantity'] > 0]
                self.assertEqual(buys, [100.0] * 4, err)

    def test_overlap_refund_of_one_account_only(self):
        refunds = _adj(_B, '2025-02-03', 1, 'Refund (SYN, 10, 2025-01-06)')
        rows, err = self._overlap(refunds)
        buys = sorted(t['net_amount'] for t in rows
                      if t['action'] == 'BUYSELL' and t['quantity'] > 0)
        self.assertEqual(buys, [100.0, 100.0, 101.0, 101.0], err)

    def test_a_copy_of_the_refund_in_a_statement_without_account_column(self):
        # One account's own statement (no Account column on its refund
        # row) and a combined one list one refund: folded once in each
        # copy of the trade.
        adj_h = ('Commission Adjustments,Header,Currency,Date,Description,'
                 'Amount\n')
        own = (HEAD + _Y2025
               + TRADES_H + _buy(_A) + adj_h
               + 'Commission Adjustments,Data,CAD,2025-02-03,'
                 '"Refund (SYN, 10, 2025-01-06)",1\n')
        combined = (HEAD + _Y2025 + TRADES_H + _buy(_A) + _buy(_B) + _ADJ_H
                    + _adj(_A, '2025-02-03', 1,
                           'Refund (SYN, 10, 2025-01-06)'))
        rows, err = self._run({'own.csv': own, 'combined.csv': combined})
        buys = sorted(t['net_amount'] for t in rows
                      if t['action'] == 'BUYSELL')
        self.assertEqual(buys, [100.0, 100.0, 101.0], err)
        self.assertNotIn('FEE', [t['action'] for t in rows])


if __name__ == '__main__':
    unittest.main()
