"""Re-audit-2 tests-tagging round: tests that pin a tax-logic clause no
@rule-tagged test used to kill (every book here is synthetic)."""
import contextlib
import io
import unittest

from taxjson.lib.core import CanadaTaxRules, TaxTransaction, USATaxRules
from tax_rules import rule, rule_absent


def _row(d, q, px, acct='margin', sym='XYZ.TO', t='10:00:00', cur='CAD'):
    return TaxTransaction(action='BUYSELL', date=d, date_settle=d, time=t,
                          symbol=sym, quantity=float(q), price=float(px),
                          net_amount=round(abs(q * px), 2), currency=cur,
                          account=acct)


def _ca(tax, shel=None, aff=None):
    with contextlib.redirect_stderr(io.StringIO()):
        return CanadaTaxRules().compute_gains(
            tax, sheltered_transactions=shel, affiliated_transactions=aff)


def _sales(res):
    return [(e['date'], round(e['disallowed_amount'], 2),
             round(e.get('permanently_disallowed', 0.0), 2))
            for e in res['transactions'] if 'proceeds' in e]


class TestCaSameMomentHolderRank(unittest.TestCase):
    """A2-0487: CA-SL-10's same-moment tie-break (taxable, then sheltered,
    then affiliated) was pinned only by books where the export order
    already put the taxable row first. Purchases BEFORE the loss are
    matched latest first, so with no holder rank the last-listed
    (registered / affiliated) row of a same-moment pair would back the
    denial and make it permanent."""

    BOOK = [_row('2025-01-02', 100, 50), _row('2025-02-20', 50, 45),
            _row('2025-03-03', -50, 40)]

    @rule("CA-SL-10")
    def test_pre_loss_same_moment_taxable_before_tfsa(self):
        res = _ca(self.BOOK, [_row('2025-02-20', 50, 45, 'tfsa')])
        self.assertEqual(_sales(res), [('2025-03-03', 416.67, 0.0)])

    @rule("CA-SL-10")
    def test_pre_loss_same_moment_taxable_before_affiliated(self):
        res = _ca(self.BOOK, aff=[_row('2025-02-20', 50, 45, 'spouse')])
        self.assertEqual(_sales(res), [('2025-03-03', 416.67, 0.0)])

    @rule("CA-SL-10")
    def test_pre_loss_purchases_latest_first(self):
        # A2-0838 (SL10 mutant): the TFSA bought first, the taxable
        # account later, both before the loss: the LATER (taxable)
        # purchase backs the denial, so it is deferred, not permanent.
        tax = [_row('2025-01-02', 100, 50), _row('2025-02-20', 50, 45),
               _row('2025-03-03', -50, 40)]
        res = _ca(tax, [_row('2025-02-10', 50, 45, 'tfsa')])
        self.assertEqual(_sales(res), [('2025-03-03', 416.67, 0.0)])
        # Bought the other way round, the later TFSA purchase backs it.
        tax[1] = _row('2025-02-10', 50, 45)
        res = _ca(tax, [_row('2025-02-20', 50, 45, 'tfsa')])
        self.assertEqual(_sales(res), [('2025-03-03', 416.67, 416.67)])


class TestBaselineStatementsPinned(unittest.TestCase):
    """A2-0851: statements the unpinned baseline still listed."""

    @rule("CA-DISP-04")
    def test_ca_short_is_realized_on_the_cover(self):
        # Short in December, covered in January: one disposition, dated
        # (and settled) on the cover, gain = proceeds - cost of cover.
        res = _ca([_row('2024-12-16', -100, 50), _row('2025-01-15', 100, 40)])
        rows = [(e['date'], e['date_settle'], round(e['gain'], 2))
                for e in res['transactions'] if 'proceeds' in e]
        self.assertEqual(rows, [('2025-01-15', '2025-01-15', 1000.0)])
        res = _ca([_row('2024-12-16', -100, 50), _row('2025-01-15', 100, 60)])
        self.assertEqual([round(e['gain'], 2) for e in res['transactions']
                          if 'proceeds' in e], [-1000.0])

    @rule("US-WASH-03")
    def test_us_identical_option_rebought_washes_the_option_loss(self):
        c = 'XYZ250620C00050000.US'
        rows = [_row('2025-02-03', 1, 5, sym=c, cur='USD'),
                _row('2025-03-03', -1, 2, sym=c, cur='USD'),
                _row('2025-03-10', 1, 2.5, sym=c, cur='USD')]
        for r in rows:
            r.net_amount = round(abs(r.quantity * r.price * 100), 2)
        with contextlib.redirect_stderr(io.StringIO()):
            res = USATaxRules().compute_gains(rows)
        sale, = [e for e in res['transactions'] if 'proceeds' in e]
        self.assertAlmostEqual(sale['disallowed_amount'], 300.0, places=2)
        lot, = [i for i in res['inventory'] if i['symbol'] == c]
        self.assertAlmostEqual(lot['total_cost'], 550.0, places=2)



def _opt(d, q, px, sym='XYZ250620C00050000.TO', **kw):
    r = _row(d, q, px, sym=sym, **kw)
    r.net_amount = round(abs(q * px * 100), 2)
    return r


class TestCaOptionLossReplacement(unittest.TestCase):
    """A2-0838 (SL06 mutants): CA-SL-06 — an option's loss is replaced
    only by the identical contract, never by the shares or another
    series."""

    BOOK = [_opt('2025-02-03', 1, 5), _opt('2025-03-03', -1, 2)]

    def _denied(self, extra):
        return [round(e['disallowed_amount'], 2)
                for e in _ca(self.BOOK + extra)['transactions']
                if 'proceeds' in e]

    @rule("CA-SL-06")
    def test_shares_never_replace_an_option(self):
        self.assertEqual(self._denied([_row('2025-03-10', 100, 50)]), [0.0])

    @rule("CA-SL-06")
    def test_another_series_never_replaces_an_option(self):
        self.assertEqual(self._denied(
            [_opt('2025-03-10', 1, 1.5, 'XYZ250620C00055000.TO')]), [0.0])
        self.assertEqual(self._denied(
            [_opt('2025-03-10', 1, 1.5, 'XYZ250919C00050000.TO')]), [0.0])

    @rule("CA-SL-06")
    def test_the_identical_contract_does(self):
        self.assertEqual(self._denied([_opt('2025-03-10', 1, 1.5)]), [300.0])



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
    term, USINCDATE ROC record date)."""

    @rule("US-WASH-01", "US-DATE-01")
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

    @rule("US-WASH-06")
    def test_no_still_held_test(self):
        # The replacement is sold again before day 30: still a wash; the
        # deferral rides it and comes back at its sale.
        self.assertEqual(_us([_urow('2025-01-02', 100, 50),
                              _urow('2025-03-03', -100, 40),
                              _urow('2025-03-10', 100, 41),
                              _urow('2025-03-20', -100, 42)]),
                         [('2025-03-03', 0.0, 1000.0),
                          ('2025-03-20', -900.0, 0.0)])

    @rule("US-BASIS-04")
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

    @rule("US-INC-DATE-ROC")
    @rule("CA-INC-DATE-ROC-TRUST")
    @rule_absent("CA-INC-DATE-ROC-TRUST", country="usa")
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
    post-loss-first order (CA-SL-10)."""

    @rule("US-WASH-20")
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

    @rule("CA-SL-10")
    @rule("US-WASH-20")
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

    @rule("US-WASH-20")
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
