"""The 2026-10 filing-amount audit: five defects that changed exported
filing figures (F1-F5), each with the neighbouring control that passed.

All data is synthetic (made-up tickers, whole-dollar amounts).

Formerly: test_fix_filing_audit_2026_10
"""
import contextlib
import io
import unittest

from taxjson.lib.tax_forms import build_8949
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.corp_actions import (CorporateAction,
                                      _emit_basis_carryover_rename)
from taxjson.lib.pipeline import GainsRequest, run_gains
from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R
from _style import CapturedWidth


_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def compute(book, country, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        result = run_gains(book, req=GainsRequest(country=country,
                                                  taxable=True, **kw))
    return result, err.getvalue()


def merger_rows(country, account, old, new, cur):
    """corp_actions' rows for one account's rollover merger delivery:
    the EMPIRICAL ratio new / old (12 -> 12 is 1, 40 -> 42 is 1.05)."""
    ev = CorporateAction(
        date='2024-06-03', time='00:00:01', action_type='merger',
        source_symbol='QZOLD.US', source_isin='', target_symbol='QZNEW.US',
        target_isin='', ratio_new=1.05, ratio_old=1, qty_disposed=old,
        qty_received=new, fmv=0, currency=cur, target_currency=cur,
        account=account)
    return [TaxTransaction(**r) for r in _emit_basis_carryover_rename(
        ev, {}, statute_note='synthetic rollover', cil_note='synthetic')]


def merger_book(country, sold, *, orphan=False):
    """Two accounts through one merger with different delivered
    ratios; b sells `sold` of its new shares. `orphan` adds a third
    account holding the old shares with no merger row of its own."""
    cur = 'CAD' if country == 'canada' else 'USD'
    book = [tx('BUYSELL', '2023-01-02', 'QZOLD.US', 12, 120, account='a',
               currency=cur, id='buy-a'),
            tx('BUYSELL', '2023-01-02', 'QZOLD.US', 40, 400, account='b',
               currency=cur, id='buy-b')]
    if orphan:
        book.append(tx('BUYSELL', '2023-01-02', 'QZOLD.US', 20, 200,
                       account='c', currency=cur, id='buy-c'))
    for account, old, new in [('a', 12, 12), ('b', 40, 42)]:
        book += merger_rows(country, account, old, new, cur)
    book.append(tx('BUYSELL', '2024-07-01', 'QZNEW.US', -sold, sold * 10,
                   account='b', currency=cur, id='sell-b'))
    return book


def _inventory(res, symbol):
    return [r for r in res['inventory'] if r['symbol'] == symbol]


class TestF1USMergerPerAccountRatios(unittest.TestCase):
    """F1: a merger reported per account with different delivered
    ratios. The first row scaled and migrated EVERY account's lots at
    its ratio; the second found the source gone: b sold 42 against 40
    and opened a two-share phantom short, and Form 8949 said 0.

    Formerly: test_fix_filing_audit_2026_10.TestF1USMergerPerAccountRatios"""

    @rule(R.US_CORP_14)
    def test_each_account_lands_on_its_delivery(self):
        r, err = compute(merger_book('usa', 42), 'usa', year=2024)
        f = build_8949(r['transactions'], year=2024)
        self.assertEqual(f['part_II_totals']['gain'], 20.0)
        inv = _inventory(r, 'QZNEW.US')
        self.assertEqual(sorted((i['account'], round(i['qty'], 6))
                                for i in inv if abs(i['qty']) > 1e-9),
                         [('a', 12.0)])
        self.assertNotIn('phantom', err.lower())

    @rule(R.US_CORP_14)
    def test_account_without_its_own_row_keeps_the_first_ratio(self):
        # c holds 20 old shares and has no merger row: it takes the
        # first row's ratio (1), as the Canada fold gives it.
        r, _ = compute(merger_book('usa', 42, orphan=True), 'usa',
                       year=2024)
        f = build_8949(r['transactions'], year=2024)
        self.assertEqual(f['part_II_totals']['gain'], 20.0)
        held = {i['account']: round(i['qty'], 6)
                for i in _inventory(r, 'QZNEW.US') if abs(i['qty']) > 1e-9}
        self.assertEqual(held, {'a': 12.0, 'c': 20.0})

    @rule(R.US_CORP_14)
    def test_symbol_wide_lots_fold_to_the_delivered_total(self):
        # Without per-account basis the lots are symbol-wide: the rows
        # fold into one event at the holdings-weighted ratio, so the
        # lots hold exactly the 54 shares delivered at cost 520.
        r, err = compute(merger_book('usa', 27), 'usa', year=2024,
                         per_account_basis=False)
        self.assertIn('holdings-weighted ratio', err)
        inv = _inventory(r, 'QZNEW.US')
        self.assertAlmostEqual(sum(i['qty'] for i in inv), 27.0, places=6)
        self.assertAlmostEqual(sum(i['total_cost'] for i in inv), 260.0,
                               places=2)

    def test_control_single_account_merger(self):
        b = [t for t in merger_book('usa', 42) if t.account == 'b']
        r, _ = compute(b, 'usa', year=2024)
        self.assertEqual(build_8949(r['transactions'], 2024)
                         ['part_II_totals']['gain'], 20)

    def test_control_same_ratio_rows_are_one_event(self):
        # Both accounts delivered at 1.05 (20 -> 21, 40 -> 42): the rows
        # dedupe to one event that reaches every account, as before.
        book = [tx('BUYSELL', '2023-01-02', 'QZOLD.US', 20, 200,
                   account='a', id='buy-a'),
                tx('BUYSELL', '2023-01-02', 'QZOLD.US', 40, 400,
                   account='b', id='buy-b')]
        for account, old, new in [('a', 20, 21), ('b', 40, 42)]:
            book += merger_rows('usa', account, old, new, 'USD')
        book.append(tx('BUYSELL', '2024-07-01', 'QZNEW.US', -42, 420,
                       account='b', id='sell-b'))
        r, _ = compute(book, 'usa', year=2024)
        self.assertEqual(build_8949(r['transactions'], 2024)
                         ['part_II_totals']['gain'], 20)
        held = {i['account']: round(i['qty'], 6)
                for i in _inventory(r, 'QZNEW.US') if abs(i['qty']) > 1e-9}
        self.assertEqual(held, {'a': 21.0})


if __name__ == '__main__':
    unittest.main()
