"""US corporate-action rules: §1001 taxable exchange, §368(a) tax-free
reorg (basis carryover + holding tacking via the SPLIT model), §356 cash
boot (gain = min(realized, boot), losses unrecognized, §358 basis), §301
distribution, and §355 allocated-basis spinoff — plus generic-emitter
parity with the Canada wrappers and the run-gate flip for country=usa.
"""

import io
import unittest
from contextlib import redirect_stderr

from taxjson.lib.corp_actions import (
    CorporateAction,
    RULES_BY_COUNTRY,
    options_for,
    resolve_event,
)


def merger_event(**overrides):
    kw = dict(
        date='2025-06-20', time='09:30:00', action_type='merger',
        source_symbol='HES.US', source_isin='US42809H1077',
        target_symbol='CVX.US', target_isin='US1667641005',
        ratio_new=1, ratio_old=1,
        qty_disposed=100.0, qty_received=100.0,
        fmv=0.0, currency='USD', target_currency='USD',
        account='margin',
    )
    kw.update(overrides)
    return CorporateAction(**kw)


def spinoff_event(**overrides):
    kw = dict(
        date='2025-04-01', time='09:30:00', action_type='spinoff',
        source_symbol='GE.US', source_isin='US3696043013',
        target_symbol='GEV.US', target_isin='US36828A1016',
        ratio_new=1, ratio_old=4,
        qty_disposed=0.0, qty_received=25.0,
        fmv=0.0, currency='USD', target_currency='USD',
        account='margin',
    )
    kw.update(overrides)
    return CorporateAction(**kw)


class TestUsRegistration(unittest.TestCase):
    def test_usa_in_rules(self):
        self.assertIn('usa', RULES_BY_COUNTRY)
        self.assertIn('merger', RULES_BY_COUNTRY['usa'])
        self.assertIn('spinoff', RULES_BY_COUNTRY['usa'])

    def test_options_include_ignore(self):
        keys = [k for k, _ in options_for('usa', 'merger')]
        self.assertEqual(keys, ['taxable_exchange', 'reorg_368',
                                'reorg_368_boot', 'ignore'])

    def test_no_hardcoded_filing_year_anywhere(self):
        import re
        for country, rules in RULES_BY_COUNTRY.items():
            for rule in rules.values():
                for _key, desc in rule.options:
                    self.assertIsNone(
                        re.search(r'\b20\d\d\b', desc),
                        f"hardcoded year in {country} option text: {desc}")


class TestUsMergerTaxable(unittest.TestCase):
    def test_sell_and_buy_at_fmv(self):
        ev = merger_event(fmv=16000.0, target_fmv=16000.0)
        rows = resolve_event(ev, 'taxable_exchange', country='usa')
        self.assertEqual([r['action'] for r in rows], ['BUYSELL', 'BUYSELL'])
        sell, buy = rows
        self.assertEqual(sell['symbol'], 'HES.US')
        self.assertAlmostEqual(sell['quantity'], -100.0)
        self.assertAlmostEqual(sell['net_amount'], 16000.0, places=2)
        self.assertEqual(buy['symbol'], 'CVX.US')
        self.assertAlmostEqual(buy['net_amount'], 16000.0, places=2)
        self.assertIn('§1001', sell['description'])


class TestUsMergerReorg368(unittest.TestCase):
    def test_split_rename_carries_basis(self):
        ev = merger_event()
        rows = resolve_event(ev, 'reorg_368', country='usa')
        self.assertEqual(len(rows), 1)
        split = rows[0]
        self.assertEqual(split['action'], 'SPLIT')
        self.assertEqual(split['symbol'], 'HES.US')
        self.assertEqual(split['symbol_new'], 'CVX.US')
        self.assertAlmostEqual(split['quantity'], 1.0)
        self.assertIn('§368(a)', split['description'])
        self.assertIn('§1223(1)', split['description'])


class TestUsMergerBoot(unittest.TestCase):
    """§356 math: gain = max(0, min(realized, boot)); SELL proceeds =
    basis + gain (so the engine books exactly the recognized gain);
    BUY basis = proceeds − boot (== §358 basis)."""

    def _rows(self, *, basis, tgt_fmv, boot):
        ev = merger_event(target_fmv=tgt_fmv)
        err = io.StringIO()
        with redirect_stderr(err):
            rows = resolve_event(
                ev, 'reorg_368_boot', country='usa',
                hints={'cash_boot': boot, 'source_basis_total': basis})
        return rows, err.getvalue()

    def test_appreciated_gain_capped_at_boot(self):
        # realized = (1800 + 300) − 1000 = 1100 → recognized = boot = 300
        rows, _ = self._rows(basis=1000.0, tgt_fmv=1800.0, boot=300.0)
        sell, buy = rows
        self.assertAlmostEqual(sell['net_amount'], 1300.0, places=2)  # 1000+300
        self.assertAlmostEqual(buy['net_amount'], 1000.0, places=2)   # §358: 1000−300+300

    def test_boot_exceeds_gain_caps_at_gain(self):
        # realized = (900 + 200) − 1000 = 100 → recognized = 100 (< boot 200)
        rows, _ = self._rows(basis=1000.0, tgt_fmv=900.0, boot=200.0)
        sell, buy = rows
        self.assertAlmostEqual(sell['net_amount'], 1100.0, places=2)  # 1000+100
        self.assertAlmostEqual(buy['net_amount'], 900.0, places=2)    # 1000−200+100

    def test_loss_recognizes_nothing(self):
        # realized = (600 + 100) − 1000 = −300 → recognized = 0 (§356(c))
        rows, _ = self._rows(basis=1000.0, tgt_fmv=600.0, boot=100.0)
        sell, buy = rows
        self.assertAlmostEqual(sell['net_amount'], 1000.0, places=2)  # zero gain
        self.assertAlmostEqual(buy['net_amount'], 900.0, places=2)    # 1000−100+0

    def test_missing_basis_hint_warns(self):
        _rows, err = self._rows(basis=0.0, tgt_fmv=1800.0, boot=300.0)
        self.assertIn('source_basis_total', err)

    def test_engine_books_exactly_the_recognized_gain(self):
        # End-to-end: engine pool basis == hint → booked gain == 300.
        from taxjson.lib.core import USATaxRules, TaxTransaction
        ev = merger_event(target_fmv=1800.0)
        rows = resolve_event(
            ev, 'reorg_368_boot', country='usa',
            hints={'cash_boot': 300.0, 'source_basis_total': 1000.0})
        txs = [TaxTransaction(action='BUYSELL', date='2024-01-10',
                              symbol='HES.US', quantity=100, currency='USD',
                              price=10.0, net_amount=1000.0)]
        txs += [TaxTransaction(**r) for r in rows]
        res = USATaxRules().compute_gains(txs)
        self.assertAlmostEqual(res['summary']['total_gain'], 300.0, places=2)
        cvx = next(h for h in res['inventory'] if h['symbol'] == 'CVX.US')
        self.assertAlmostEqual(cvx['total_cost'], 1000.0, places=2)


class TestUsSpinoff(unittest.TestCase):
    def test_301_distribution(self):
        ev = spinoff_event()
        rows = resolve_event(ev, 'taxable_distribution_301', country='usa',
                             hints={'fmv_per_share': 40.0})
        self.assertEqual([r['action'] for r in rows], ['DIVIDEND', 'BUYSELL'])
        div, buy = rows
        self.assertAlmostEqual(div['net_amount'], 1000.0, places=2)  # 25 × 40
        self.assertEqual(buy['symbol'], 'GEV.US')
        self.assertAlmostEqual(buy['net_amount'], 1000.0, places=2)
        self.assertIn('§301', div['description'])

    def test_355_allocated_basis(self):
        ev = spinoff_event()
        rows = resolve_event(ev, 'tax_free_355', country='usa',
                             hints={'allocated_acb': 800.0})
        self.assertEqual([r['action'] for r in rows], ['BUYSELL', 'ADJUST'])
        buy, adj = rows
        self.assertAlmostEqual(buy['net_amount'], 800.0, places=2)
        self.assertEqual(adj['symbol'], 'GE.US')
        self.assertAlmostEqual(adj['net_amount'], -800.0, places=2)
        self.assertIn('§355', buy['description'])


class TestGenericEmitterParity(unittest.TestCase):
    """The Canada wrappers must emit exactly what they did before the
    generic extraction (descriptions included)."""

    def test_canada_rollover_description_unchanged(self):
        ev = merger_event(source_symbol='SSL.TO', target_symbol='RGLD.US',
                          currency='CAD', target_currency='USD',
                          ratio_new=1, ratio_old=16,
                          qty_disposed=1600.0, qty_received=100.0)
        rows = resolve_event(ev, 'rollover_s_85_1_5', country='canada')
        self.assertEqual(rows[0]['action'], 'SPLIT')
        self.assertIn('s. 85.1(5) rollover elected', rows[0]['description'])
        self.assertIn('100 shares received per 1600 disposed',
                      rows[0]['description'])

    def test_canada_taxable_description_unchanged(self):
        ev = merger_event(source_symbol='SSL.TO', target_symbol='RGLD.US',
                          fmv=25920.67, target_fmv=18600.0,
                          currency='CAD', target_currency='USD')
        rows = resolve_event(ev, 'taxable_disposition', country='canada')
        self.assertIn('(taxable disposition; no CRA election filed)',
                      rows[0]['description'])


if __name__ == '__main__':
    unittest.main()
