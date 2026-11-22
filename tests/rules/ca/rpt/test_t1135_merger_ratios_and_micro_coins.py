"""The 2026-10 filing-amount audit: five defects that changed exported
filing figures (F1-F5), each with the neighbouring control that passed.

All data is synthetic (made-up tickers, whole-dollar amounts).

Formerly: test_fix_filing_audit_2026_10
"""
import contextlib
import io
import unittest

from taxjson.bin.taxjson_t1135 import walk_costs
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


class TestF4T1135MergerRatios(unittest.TestCase):
    """F4: the T1135 cost walk replayed each account's merger row into
    its symbol-global pool: the first renamed the whole pool at 1, the
    second found the source empty — 52 shares for the 54 delivered, a
    sale of 27 released 270 of the 520 cost, and the year-end cost
    amount was 250 for the engine's 260. The walk reads the engine's
    pools now (backlog TJ-0417).

    Formerly: test_fix_filing_audit_2026_10.TestF4T1135MergerRatios"""

    @rule(R.CA_CORP_13, R.CA_RPT_12)
    def test_cost_walk_folds_the_ratios_as_the_engine_does(self):
        rows = [t.to_dict() for t in merger_book('canada', 27)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            f = walk_costs(rows, 2025, {})
        self.assertEqual(f['per_symbol']['QZNEW.US']['year_end_cost'], 260.0)
        self.assertEqual(f['per_symbol']['QZNEW.US']['max_cost'], 260.0)
        self.assertEqual(f['max_total_cost'], 260.0)

    @rule(R.CA_CORP_13)
    def test_control_engine_keeps_the_same_basis(self):
        r, _ = compute(merger_book('canada', 27), 'canada')
        self.assertAlmostEqual(r['inventory'][0]['total_cost'], 260)


class TestF5T1135MicroCrypto(unittest.TestCase):
    """F5: a coin held below a millionth of a unit was erased from the
    T1135 by the 1e-6 share epsilon — no property row, cost 0 — while
    the gains inventory kept the units and their cost.

    Formerly: test_fix_filing_audit_2026_10.TestF5T1135MicroCrypto"""

    @rule(R.CA_RPT_12, R.CA_CRYPTO_09)
    def test_micro_holding_is_property_with_its_cost(self):
        row = tx('BUYSELL', '2024-01-02', 'QZCOIN', .0000009, .18,
                 currency='CAD').to_dict()
        f = walk_costs([row], 2025, {'QZCOIN': 'USA'})
        self.assertEqual(f['max_total_cost'], .18)
        self.assertEqual(f['per_symbol']['QZCOIN']['year_end_cost'], .18)

    @rule(R.CA_RPT_12, R.CA_CRYPTO_09)
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
        """Share dust under a cent of cost is nothing; one that carries a
        real cost is a holding (CA-ACB-01; backlog TJ-0649)."""
        row = tx('BUYSELL', '2024-01-02', 'QZSTK.US', 5e-7, .004,
                 currency='CAD').to_dict()
        self.assertEqual(walk_costs([row], 2025, {})['max_total_cost'], 0)
        row = tx('BUYSELL', '2024-01-02', 'QZSTK.US', 5e-7, 100,
                 currency='CAD').to_dict()
        self.assertEqual(walk_costs([row], 2025, {})['max_total_cost'], 100)


if __name__ == '__main__':
    unittest.main()
