"""The 2026-10 filing-amount audit: five defects that changed exported
filing figures (F1-F5), each with the neighbouring control that passed.

All data is synthetic (made-up tickers, whole-dollar amounts).
"""
import contextlib
import io
import unittest

from taxjson.bin.taxjson_form_export import build_8949, build_schedule3
from taxjson.bin.taxjson_t1135 import walk_costs
from taxjson.lib.core import TaxTransaction
from taxjson.lib.corp_actions import (CorporateAction,
                                      _emit_basis_carryover_rename)
from taxjson.lib.pipeline import GainsRequest, run_gains
from tax_rules import rule
from tax_rules.dual import tx
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


def crypto_book(q=0.0000009):
    return [tx('BUYSELL', d, 'QZCOIN', sign * q, amount, currency='CAD',
               id=ident)
            for d, sign, amount, ident in [
                ('2024-01-02', 1, .18, 'buy'), ('2025-01-02', -1, .09, 'loss'),
                ('2025-01-10', 1, .09, 'rebuy'), ('2025-03-03', -1, .18, 'sale')]]


def stock_dividend_book(**dates):
    """10 shares bought in 2023 for 100; a 10% stock dividend with
    record / ex date 2025-01-10, paid 2025-01-20; 10 more bought for 200
    on 2025-01-15, after the entitlement date; 11 sold on 2025-03-03."""
    return [tx('BUYSELL', '2023-01-02', 'QZSTK.US', 10, 100, id='old'),
            tx('BUYSELL', '2025-01-15', 'QZSTK.US', 10, 200, id='new'),
            tx('BUYSELL', '2025-01-20', 'QZSTK.US', 1, 0,
               type='stock_dividend', id='dividend', **dates),
            tx('BUYSELL', '2025-03-03', 'QZSTK.US', -11, 165, id='sale')]


def _inventory(res, symbol):
    return [r for r in res['inventory'] if r['symbol'] == symbol]


class TestF1USMergerPerAccountRatios(unittest.TestCase):
    """F1: a merger reported per account with different delivered
    ratios. The first row scaled and migrated EVERY account's lots at
    its ratio; the second found the source gone: b sold 42 against 40
    and opened a two-share phantom short, and Form 8949 said 0."""

    @rule("US-CORP-14")
    def test_each_account_lands_on_its_delivery(self):
        r, err = compute(merger_book('usa', 42), 'usa', year=2024)
        f = build_8949(r['transactions'], year=2024)
        self.assertEqual(f['part_II_totals']['gain'], 20.0)
        inv = _inventory(r, 'QZNEW.US')
        self.assertEqual(sorted((i['account'], round(i['qty'], 6))
                                for i in inv if abs(i['qty']) > 1e-9),
                         [('a', 12.0)])
        self.assertNotIn('phantom', err.lower())

    @rule("US-CORP-14")
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

    @rule("US-CORP-14")
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


class TestF2CanadaMicroCryptoDeniedLoss(unittest.TestCase):
    """F2: a sub-micro coin position (0.0000009 QZCOIN) read as FLAT by
    the 1e-6 share epsilon when the denied loss's ADJUST landed: the
    deferral was parked in pending_wash and the closing sale never
    recovered it — Schedule 3 showed the denied 0.09 as a gain."""

    @rule("CA-CRYPTO-09", "CA-SL-09")
    def test_replacement_carries_the_denied_loss(self):
        r, _ = compute(crypto_book(), 'canada', year=2025, spot_crypto=True)
        f = build_schedule3(r['transactions'], year=2025)
        self.assertEqual(f['totals']['gain_all'], 0.0)
        self.assertFalse([i for i in _inventory(r, 'QZCOIN')
                          if abs(i['qty']) > 1e-15], "fully sold")

    def test_control_whole_coin(self):
        r, _ = compute(crypto_book(1), 'canada', year=2025, spot_crypto=True)
        self.assertEqual(build_schedule3(r['transactions'], 2025)
                         ['totals']['gain_all'], 0)


class TestF3USStockDividendEntitledLots(unittest.TestCase):
    """F3: the §307 basis spread reached every lot held on the pay
    date, a lot bought after the record date included: the old lot
    became 10.5 shares, the sale of 11 took half a share of the new
    lot, and Form 8949 said 57.50 long-term and -2.02 short-term for
    65 long-term."""

    def _check(self, book):
        r, err = compute(book, 'usa', year=2025)
        f = build_8949(r['transactions'], year=2025)
        self.assertEqual(f['part_II_totals']['gain'], 65.0)
        self.assertEqual(f['part_I_totals']['gain'], 0.0)
        inv = _inventory(r, 'QZSTK.US')
        self.assertAlmostEqual(sum(i['qty'] for i in inv), 10.0, places=6)
        self.assertAlmostEqual(sum(i['total_cost'] for i in inv), 200.0,
                               places=2)
        return err

    @rule("US-STKDIV-01")
    def test_record_and_ex_date(self):
        err = self._check(stock_dividend_book(record_date='2025-01-10',
                                              ex_date='2025-01-10'))
        self.assertIn('10 share(s) bought before 2025-01-10', err)
        self.assertIn('10 bought on or after 2025-01-10 keep their own '
                      'basis', err)
        self.assertNotIn('warning:', err)

    @rule("US-STKDIV-01")
    def test_record_date_alone(self):
        self._check(stock_dividend_book(record_date='2025-01-10'))

    @rule("US-STKDIV-01")
    def test_ex_date_alone(self):
        self._check(stock_dividend_book(ex_date='2025-01-10'))

    @rule("US-STKDIV-04")
    def test_no_date_spreads_over_all_lots_and_warns(self):
        # The broker's row (no record / ex date): every lot held on the
        # pay date shares the basis, as before, and the lot bought 5
        # days before the pay date is named as the open question.
        r, err = compute(stock_dividend_book(), 'usa', year=2025)
        f = build_8949(r['transactions'], year=2025)
        self.assertEqual(f['part_II_totals']['gain'], 57.5)
        # ATTENTION: `tjs run` echoes it on the console.
        self.assertIn('warning: ATTENTION: QZSTK.US: the stock dividend of '
                      '1 share(s) on 2025-01-20 carries no record or '
                      'ex-dividend date', err)
        self.assertIn('10 share(s) bought on 2025-01-15, within 90 days',
                      err)

    @rule("US-STKDIV-04")
    def test_no_date_one_old_lot_is_quiet(self):
        book = [t for t in stock_dividend_book() if t.id != 'new']
        r, err = compute(book, 'usa', year=2025)
        self.assertNotIn('warning:', err)
        self.assertEqual(build_8949(r['transactions'], 2025)
                         ['part_II_totals']['gain'], 65.0)

    @rule("US-STKDIV-01", "US-STKDIV-03")
    def test_no_entitled_lot_is_a_zero_purchase(self):
        # Every lot held was bought after the ex date: the dividend
        # belongs to shares the books do not show.
        book = [t for t in stock_dividend_book(ex_date='2025-01-10')
                if t.id != 'old']
        r, err = compute(book, 'usa', year=2025)
        self.assertIn('none of the 10 share(s) held was bought before its '
                      'entitlement date 2025-01-10', err)
        inv = _inventory(r, 'QZSTK.US')
        self.assertAlmostEqual(sum(i['qty'] for i in inv), 0.0, places=6)
        # 11 sold for 165: 10 at 200 and the $0 share.
        f = build_8949(r['transactions'], year=2025)
        self.assertAlmostEqual(f['part_I_totals']['gain'], -35.0, places=2)

    def test_control_purchase_after_the_pay_date(self):
        b = stock_dividend_book()
        b[1].date = b[1].date_settle = '2025-01-21'
        r, _ = compute(b, 'usa', year=2025)
        f = build_8949(r['transactions'], 2025)
        self.assertEqual(f['part_II_totals']['gain'], 65)
        self.assertEqual(f['part_I_totals']['gain'], 0)


class TestF4T1135MergerRatios(unittest.TestCase):
    """F4: the T1135 cost walk replayed each account's merger row into
    its symbol-global pool: the first renamed the whole pool at 1, the
    second found the source empty — 52 shares for the 54 delivered, a
    sale of 27 released 270 of the 520 cost, and the year-end cost
    amount was 250 for the engine's 260."""

    @rule("CA-CORP-13", "CA-RPT-12")
    def test_cost_walk_folds_the_ratios_as_the_engine_does(self):
        rows = [t.to_dict() for t in merger_book('canada', 27)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            f = walk_costs(rows, 2025, {})
        self.assertEqual(f['per_symbol']['QZNEW.US']['year_end_cost'], 260.0)
        self.assertEqual(f['per_symbol']['QZNEW.US']['max_cost'], 260.0)
        self.assertEqual(f['max_total_cost'], 260.0)
        self.assertIn('holdings-weighted ratio', err.getvalue())

    @rule("CA-CORP-13")
    def test_control_engine_keeps_the_same_basis(self):
        r, _ = compute(merger_book('canada', 27), 'canada')
        self.assertAlmostEqual(r['inventory'][0]['total_cost'], 260)

    def test_rows_without_a_multi_ratio_event_are_returned_as_given(self):
        from taxjson.bin.taxjson_t1135 import _fold_merger_ratios
        rows = [t.to_dict() for t in merger_book('canada', 27)
                if t.action != 'SPLIT']
        self.assertIs(_fold_merger_ratios(rows), rows)


class TestF5T1135MicroCrypto(unittest.TestCase):
    """F5: a coin held below a millionth of a unit was erased from the
    T1135 by the 1e-6 share epsilon — no property row, cost 0 — while
    the gains inventory kept the units and their cost."""

    @rule("CA-RPT-12", "CA-CRYPTO-09")
    def test_micro_holding_is_property_with_its_cost(self):
        row = tx('BUYSELL', '2024-01-02', 'QZCOIN', .0000009, .18,
                 currency='CAD').to_dict()
        f = walk_costs([row], 2025, {'QZCOIN': 'USA'})
        self.assertEqual(f['max_total_cost'], .18)
        self.assertEqual(f['per_symbol']['QZCOIN']['year_end_cost'], .18)

    @rule("CA-RPT-12", "CA-CRYPTO-09")
    def test_micro_sale_is_a_closing_not_an_opening(self):
        rows = [tx('BUYSELL', '2024-01-02', 'QZCOIN', .0000009, .18,
                   currency='CAD').to_dict(),
                tx('BUYSELL', '2025-02-01', 'QZCOIN', -.0000006, .12,
                   currency='CAD').to_dict()]
        f = walk_costs(rows, 2025, {'QZCOIN': 'USA'})
        self.assertEqual(f['per_symbol']['QZCOIN']['max_cost'], .18)
        self.assertEqual(f['per_symbol']['QZCOIN']['year_end_cost'], .06)

    def test_control_whole_coin(self):
        row = tx('BUYSELL', '2024-01-02', 'QZCOIN', 1, .18,
                 currency='CAD').to_dict()
        self.assertEqual(walk_costs([row], 2025, {'QZCOIN': 'USA'})
                         ['max_total_cost'], .18)

    def test_share_dust_still_counts_as_nothing(self):
        row = tx('BUYSELL', '2024-01-02', 'QZSTK.US', 5e-7, 100,
                 currency='CAD').to_dict()
        self.assertEqual(walk_costs([row], 2025, {})['max_total_cost'], 0)


if __name__ == '__main__':
    unittest.main()
