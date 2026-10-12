"""US corporate-action rules: §1001 taxable exchange, §368(a) tax-free
reorg (basis carryover + holding tacking via the SPLIT model), §356 cash
boot (gain = min(realized, boot), losses unrecognized, §358 basis), §301
distribution, and §355 allocated-basis spinoff — plus generic-emitter
parity with the Canada wrappers and the run-gate flip for country=usa.
"""

import io
import unittest
from contextlib import redirect_stderr

from tax_rules import rule

from taxjson.lib.corp_actions import (
    CorporateAction,
    RULES_BY_COUNTRY,
    options_for,
    resolve_event,
)


def merger_event(**overrides):
    kw = dict(
        date='2025-06-20', time='09:30:00', action_type='merger',
        source_symbol='ABC.US', source_isin='US0000ABC001',
        target_symbol='ABD.US', target_isin='US0000ABD002',
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
        source_symbol='ABE.US', source_isin='US0000ABE003',
        target_symbol='ABF.US', target_isin='US0000ABF004',
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

    @rule("US-CORP-08")
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
    @rule("US-CORP-03")
    def test_sell_and_buy_at_fmv(self):
        ev = merger_event(fmv=16000.0, target_fmv=16000.0)
        rows = resolve_event(ev, 'taxable_exchange', country='usa')
        self.assertEqual([r['action'] for r in rows], ['BUYSELL', 'BUYSELL'])
        sell, buy = rows
        self.assertEqual(sell['symbol'], 'ABC.US')
        self.assertAlmostEqual(sell['quantity'], -100.0)
        self.assertAlmostEqual(sell['net_amount'], 16000.0, places=2)
        self.assertEqual(buy['symbol'], 'ABD.US')
        self.assertAlmostEqual(buy['net_amount'], 16000.0, places=2)
        self.assertIn('§1001', sell['description'])


class TestUsMergerReorg368(unittest.TestCase):
    @rule("US-CORP-04")
    def test_split_rename_carries_basis(self):
        ev = merger_event()
        rows = resolve_event(ev, 'reorg_368', country='usa')
        self.assertEqual(len(rows), 1)
        split = rows[0]
        self.assertEqual(split['action'], 'SPLIT')
        self.assertEqual(split['symbol'], 'ABC.US')
        self.assertEqual(split['symbol_new'], 'ABD.US')
        self.assertAlmostEqual(split['quantity'], 1.0)
        self.assertIn('§368(a)', split['description'])
        self.assertIn('§1223(1)', split['description'])


class TestUsMergerBoot(unittest.TestCase):
    """§356 rows (re-audit A2-0066: computed per block by the engine):
    SELL net = amount realized (new shares' value + boot), gross_amount
    = the boot; BUY net = the new shares' value. The engine computes
    gain = max(0, min(realized, boot)) per lot and the §358 basis."""

    def _rows(self, *, tgt_fmv, boot, hints=None):
        ev = merger_event(target_fmv=tgt_fmv)
        err = io.StringIO()
        with redirect_stderr(err):
            rows = resolve_event(
                ev, 'reorg_368_boot', country='usa',
                hints=dict({'cash_boot': boot}, **(hints or {})))
        return rows, err.getvalue()

    def _engine(self, basis, *, tgt_fmv, boot):
        from taxjson.lib.core import USATaxRules, TaxTransaction
        rows, _ = self._rows(tgt_fmv=tgt_fmv, boot=boot)
        txs = [TaxTransaction(action='BUYSELL', date='2024-01-10',
                              symbol='ABC.US', quantity=100, currency='USD',
                              price=basis / 100, net_amount=basis)]
        txs += [TaxTransaction(**r) for r in rows]
        res = USATaxRules().compute_gains(txs)
        abd = next(h for h in res['inventory'] if h['symbol'] == 'ABD.US')
        return res['summary']['total_gain'], abd['total_cost']

    def test_rows_carry_amount_realized_and_boot(self):
        rows, _ = self._rows(tgt_fmv=1800.0, boot=300.0)
        sell, buy = rows
        self.assertAlmostEqual(sell['net_amount'], 2100.0, places=2)
        self.assertAlmostEqual(sell['gross_amount'], 300.0, places=2)
        self.assertAlmostEqual(buy['net_amount'], 1800.0, places=2)
        self.assertEqual({sell['type'], buy['type']}, {'reorg_356'})

    @rule("US-CORP-05")
    def test_appreciated_gain_capped_at_boot(self):
        # realized = (1800 + 300) − 1000 = 1100 → recognized = boot = 300
        gain, basis = self._engine(1000.0, tgt_fmv=1800.0, boot=300.0)
        self.assertAlmostEqual(gain, 300.0, places=2)
        self.assertAlmostEqual(basis, 1000.0, places=2)  # 1000−300+300

    @rule("US-CORP-05")
    def test_boot_exceeds_gain_caps_at_gain(self):
        # realized = (900 + 200) − 1000 = 100 → recognized = 100 (< boot)
        gain, basis = self._engine(1000.0, tgt_fmv=900.0, boot=200.0)
        self.assertAlmostEqual(gain, 100.0, places=2)
        self.assertAlmostEqual(basis, 900.0, places=2)   # 1000−200+100

    @rule("US-CORP-05")
    def test_loss_recognizes_nothing(self):
        # realized = (600 + 100) − 1000 = −300 → recognized = 0 (§356(c))
        gain, basis = self._engine(1000.0, tgt_fmv=600.0, boot=100.0)
        self.assertAlmostEqual(gain, 0.0, places=2)
        self.assertAlmostEqual(basis, 900.0, places=2)   # 1000−100+0

    def test_basis_hint_is_not_needed(self):
        # The engine reads its own lots; an older manifest's
        # source_basis_total is accepted and ignored.
        _rows, err = self._rows(tgt_fmv=1800.0, boot=300.0)
        self.assertNotIn('source_basis_total', err)
        rows2, _ = self._rows(tgt_fmv=1800.0, boot=300.0,
                              hints={'source_basis_total': 5.0})
        self.assertEqual([r['net_amount'] for r in rows2],
                         [r['net_amount'] for r in _rows])

    @rule("US-CORP-05")
    def test_engine_books_exactly_the_recognized_gain(self):
        gain, basis = self._engine(1000.0, tgt_fmv=1800.0, boot=300.0)
        self.assertAlmostEqual(gain, 300.0, places=2)
        self.assertAlmostEqual(basis, 1000.0, places=2)


class TestUsSpinoff(unittest.TestCase):
    @rule("US-CORP-06")
    def test_301_distribution(self):
        ev = spinoff_event()
        rows = resolve_event(ev, 'taxable_distribution_301', country='usa',
                             hints={'fmv_per_share': 40.0})
        self.assertEqual([r['action'] for r in rows], ['DIVIDEND', 'BUYSELL'])
        div, buy = rows
        self.assertAlmostEqual(div['net_amount'], 1000.0, places=2)  # 25 × 40
        self.assertEqual(buy['symbol'], 'ABF.US')
        self.assertAlmostEqual(buy['net_amount'], 1000.0, places=2)
        self.assertIn('§301', div['description'])

    @rule("US-CORP-07")
    def test_355_allocated_basis(self):
        ev = spinoff_event()
        rows = resolve_event(ev, 'tax_free_355', country='usa',
                             hints={'allocated_acb': 800.0})
        self.assertEqual([r['action'] for r in rows], ['BUYSELL', 'ADJUST'])
        buy, adj = rows
        self.assertAlmostEqual(buy['net_amount'], 800.0, places=2)
        self.assertEqual(adj['symbol'], 'ABE.US')
        self.assertAlmostEqual(adj['net_amount'], -800.0, places=2)
        self.assertIn('§355', buy['description'])


class TestGenericEmitterParity(unittest.TestCase):
    """The Canada wrappers must emit exactly what they did before the
    generic extraction (descriptions included)."""

    def test_canada_rollover_description_unchanged(self):
        ev = merger_event(source_symbol='ABG.TO', target_symbol='ABH.US',
                          currency='CAD', target_currency='USD',
                          ratio_new=1, ratio_old=16,
                          qty_disposed=1600.0, qty_received=100.0)
        rows = resolve_event(ev, 'rollover_s_85_1_5', country='canada')
        self.assertEqual(rows[0]['action'], 'SPLIT')
        self.assertIn('s. 85.1 rollover', rows[0]['description'])
        self.assertIn('100 shares received per 1600 disposed',
                      rows[0]['description'])

    def test_canada_taxable_description_unchanged(self):
        ev = merger_event(source_symbol='ABG.TO', target_symbol='ABH.US',
                          fmv=20480.5, target_fmv=15200.0,
                          currency='CAD', target_currency='USD')
        rows = resolve_event(ev, 'taxable_disposition', country='canada')
        self.assertIn('(taxable disposition; gain reported',
                      rows[0]['description'])


if __name__ == '__main__':
    unittest.main()
