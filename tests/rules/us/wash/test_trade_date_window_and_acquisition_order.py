"""Re-audit-2 tests-tagging round: tests that pin a tax-logic clause no
@rule-tagged test used to kill (every book here is synthetic).

Formerly: test_fix_a2_tagging"""
import contextlib
import io
import unittest

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction
from tax_rules import rule, rule_absent
from taxjson.rules import ids as R


def _row(d, q, px, acct='margin', sym='XYZ.TO', t='10:00:00', cur='CAD'):
    return TaxTransaction(action='BUYSELL', date=d, date_settle=d, time=t,
                          symbol=sym, quantity=float(q), price=float(px),
                          net_amount=round(abs(q * px), 2), currency=cur,
                          account=acct)


def _ca(tax, shel=None, aff=None):
    with contextlib.redirect_stderr(io.StringIO()):
        return CanadaTaxRules().compute_gains(
            tax, sheltered_transactions=shel, affiliated_transactions=aff)



def _us(rows):
    with contextlib.redirect_stderr(io.StringIO()):
        res = USATaxRules().compute_gains(rows)
    return [(e['date'], round(e['gain'], 2), round(e['disallowed_amount'], 2))
            for e in res['transactions'] if 'proceeds' in e]


def _urow(d, q, px, settle=None):
    r = _row(d, q, px, sym='XYZ.US', cur='USD')
    r.date_settle = settle or d
    return r


class TestUsWashStatementsPinned(unittest.TestCase):
    """A2-0838: US statements whose implementing line no tagged test
    killed (USW01 settle basis, USW06 still-held, USB04 phantom, USROC02
    term, USINCDATE ROC record date).

    Formerly: test_fix_a2_tagging.TestUsWashStatementsPinned"""

    @rule(R.US_WASH_01, R.US_DATE_01)
    def test_window_counts_trade_dates(self):
        base = [_urow('2025-01-02', 100, 50), _urow('2025-03-03', -100, 40)]
        # 30 trade days, 35 settle days: washed.
        self.assertEqual(_us(base + [_urow('2025-04-02', 100, 41,
                                           '2025-04-07')]),
                         [('2025-03-03', 0.0, 1000.0)])
        # 31 trade days, 28 settle days (the loss settles late): not.
        late = [_urow('2025-01-02', 100, 50),
                _urow('2025-03-03', -100, 40, '2025-03-07')]
        self.assertEqual(_us(late + [_urow('2025-04-03', 100, 41,
                                           '2025-04-04')]),
                         [('2025-03-03', -1000.0, 0.0)])

    @rule(R.US_WASH_06)
    def test_no_still_held_test(self):
        # The replacement is sold again before day 30: still a wash; the
        # deferral rides it and comes back at its sale.
        self.assertEqual(_us([_urow('2025-01-02', 100, 50),
                              _urow('2025-03-03', -100, 40),
                              _urow('2025-03-10', 100, 41),
                              _urow('2025-03-20', -100, 42)]),
                         [('2025-03-03', 0.0, 1000.0),
                          ('2025-03-20', -900.0, 0.0)])

    @rule(R.US_BASIS_04)
    def test_phantom_lot_loss_is_not_washed(self):
        # A phantom (missing-history) lot whose basis an ADJUST raised,
        # sold at a loss and rebought: listed for manual reporting, never
        # fed to §1091 by the engine.
        ob = TaxTransaction(action='OPENING_BALANCE', date='2025-01-02',
                            time='00:00:00', symbol='XYZ.US', quantity=100.0,
                            price=0.0, net_amount=0.0, currency='USD',
                            account='margin')
        adj = TaxTransaction(action='ADJUST', date='2025-01-10',
                             time='09:30:00', symbol='XYZ.US', quantity=0.0,
                             net_amount=5000.0, currency='USD',
                             account='margin')
        self.assertEqual(_us([ob, adj, _urow('2025-03-03', -100, 10),
                              _urow('2025-03-10', 100, 11)]),
                         [('2025-03-03', -4000.0, 0.0)])

    @rule(R.US_INC_DATE_ROC)
    @rule(R.CA_INC_DATE_ROC_TRUST)
    @rule_absent(R.CA_INC_DATE_ROC_TRUST, country="usa")
    def test_us_income_rules_never_date_roc_by_record_date(self):
        from taxjson.lib.income_dating import IncomeRules
        roc = {"action": "ADJUST", "type": "roc", "symbol": "ZZR.TO",
               "date": "2025-01-08", "record_date": "2024-12-30",
               "net_amount": -500.0}
        self.assertEqual(IncomeRules(country="usa").roc_record_date(roc), "")
        self.assertEqual(IncomeRules(country="canada").roc_record_date(roc),
                         "2024-12-30")



class TestUsReplacementOrder(unittest.TestCase):
    """A2-1506, A2-1511: US-WASH-20 — the order acquired, not Canada's
    post-loss-first order (CA-SL-10).

    Formerly: test_fix_a2_tagging.TestUsReplacementOrder"""

    @rule(R.US_WASH_20)
    def test_earliest_purchase_in_the_window_first(self):
        # A pre-loss (02-20) and a post-loss (03-10) lot, both held: the
        # 02-20 lot takes the deferral, so the FIFO sale of it on 04-15
        # carries the 1,000 (4,600 - 4,500 - 1,000).
        self.assertEqual(_us([_urow('2025-01-02', 100, 50),
                              _urow('2025-02-20', 100, 45),
                              _urow('2025-03-03', -100, 40),
                              _urow('2025-03-10', 100, 41),
                              _urow('2025-04-15', -100, 46)]),
                         [('2025-03-03', 0.0, 1000.0),
                          ('2025-04-15', -900.0, 0.0)])

    @rule(R.CA_SL_10)
    @rule(R.US_WASH_20)
    def test_the_countries_pick_different_lots(self):
        # The same book: Canada matches the purchase AFTER the sale first
        # (CA-SL-10), the US the earliest acquired (Reg. §1.1091-1(c)).
        def book(sym, cur):
            return [_row(d, q, px, sym=sym, cur=cur) for d, q, px in (
                ('2025-01-02', 100, 50), ('2025-02-20', 100, 45),
                ('2025-03-03', -100, 40), ('2025-03-10', 100, 41))]
        ca = book('XYZ.TO', 'CAD')
        res = _ca(ca)
        self.assertEqual([w['trigger_lot_id'] for w in res['wash_sales']],
                         [ca[3].id])
        us = book('XYZ.US', 'USD')
        with contextlib.redirect_stderr(io.StringIO()):
            res = USATaxRules().compute_gains(us)
        self.assertEqual([[w['tx_id'] for w in e['wash_replacements']]
                          for e in res['transactions'] if 'proceeds' in e],
                         [[us[1].id]])

    @rule(R.US_WASH_20)
    def test_losses_claim_in_the_order_sold(self):
        # Two losses, one 100-share rebuy: the earlier loss takes it.
        self.assertEqual(_us([_urow('2025-01-02', 100, 50),
                              _urow('2025-01-03', 100, 50),
                              _urow('2025-03-03', -100, 40),
                              _urow('2025-03-05', -100, 39),
                              _urow('2025-03-10', 100, 41)]),
                         [('2025-03-03', 0.0, 1000.0),
                          ('2025-03-05', -1100.0, 0.0)])


if __name__ == '__main__':
    unittest.main()
