"""Second-audit DEFERRED findings, the rest (A2-0590, A2-1014, A2-1091,
A2-1052/A2-1054, A2-1056). Every fixture is synthetic: invented tickers, fake
account ids and made-up amounts.

Formerly: test_fix_def_rest"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


# ------------------------------------------ A2-1056: hints the map answers
import os


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


# --------------------------- A2-1014: fx-cash sees a corp action's cash
from taxjson.bin.taxjson_fx_cash import build_ledger
from taxjson.lib.tx_load import coerce_transaction_row
from taxjson.lib import corp_actions as CA_


def _corp_event(**kw):
    base = dict(date='2025-10-22', time='09:30:00', action_type='merger',
                source_symbol='QZOLD.US', source_isin='US9990003001',
                target_symbol='QZNEW.US', target_isin='US9990003002',
                ratio_new=1, ratio_old=10, qty_disposed=1000.0,
                qty_received=100.0, fmv=5000.0, currency='USD',
                target_fmv=5000.0, target_currency='USD',
                account='margin', event_id='ev1')
    base.update(kw)
    return CA_.CorporateAction(**base)


def _usd_acquired(rows):
    """Rows through the books' loader (unknown keys dropped), then the
    fx-cash ledger: the USD the event put in the pool."""
    loaded = [coerce_transaction_row(dict(r, corp_event_id='ev1'), i,
                                     'test').to_dict()
              for i, r in enumerate(rows)]
    doc = build_ledger(loaded, 'CAD', {}, 2025,
                       rate_of=lambda c, d: 1.35)
    return doc['per_currency'].get('USD', {}).get('acquired', 0.0)


@rule(R.CA_FX_07)
class TestFxCashCountsCorpActionCash(unittest.TestCase):
    """A2-1014: cash a corporate action paid (cash in lieu folded into a
    sale's proceeds, boot, a spin-off's fractional share) was classified
    by description text, so only the s.85.1 path's standalone leg was
    seen. The emitters now put the cash on the row as structured
    evidence (corp_cash) and fx-cash counts it.

    Formerly: test_fix_def_rest.TestFxCashCountsCorpActionCash"""

    def test_taxable_merger_cash_in_lieu(self):
        ev = _corp_event(cash_in_lieu=25.0, cash_in_lieu_currency='USD')
        rows = CA_._emit_taxable_exchange(ev, {}, description_base='M')
        self.assertAlmostEqual(_usd_acquired(rows), 25.0)

    def test_all_fractional_taxable_merger_is_all_cash(self):
        ev = _corp_event(qty_disposed=5.0, qty_received=0.5,
                         target_fmv=25.0)
        rows = CA_._emit_taxable_exchange(ev, {}, description_base='M')
        self.assertAlmostEqual(_usd_acquired(rows), 25.0)

    def test_deemed_dividend_spinoff_fraction(self):
        ev = _corp_event(action_type='spinoff', qty_received=10.5,
                         target_fmv=210.0)
        rows = CA_._canada_spinoff_deemed_dividend(ev, 'x', {})
        # 0.5 share x 20.00 paid in cash
        self.assertAlmostEqual(_usd_acquired(rows), 10.0)

    def test_allocated_spinoff_fraction(self):
        ev = _corp_event(action_type='spinoff', qty_received=10.5,
                         target_fmv=210.0)
        rows = CA_._emit_allocated_basis_spinoff(
            ev, {'allocated_acb': 100.0}, description_base='S')
        self.assertAlmostEqual(_usd_acquired(rows), 10.0)


    def test_share_for_share_legs_stay_non_cash(self):
        ev = _corp_event()
        rows = CA_._emit_taxable_exchange(ev, {}, description_base='M')
        self.assertAlmostEqual(_usd_acquired(rows), 0.0)

    def test_rollover_standalone_leg_still_counts_once(self):
        ev = _corp_event(qty_received=99.0, cash_in_lieu=20.0,
                         cash_in_lieu_currency='USD')
        rows = CA_._canada_merger_rollover(ev, 'rollover_s_85_1_5', {})
        self.assertAlmostEqual(_usd_acquired(rows), 20.0)


class TestFxCashCountsBoot(unittest.TestCase):
    """Formerly: test_fix_def_rest.TestFxCashCountsBoot"""
    @rule(R.US_FX_03)
    def test_boot_exchange_cash(self):
        ev = _corp_event()
        rows = CA_._emit_boot_exchange(
            ev, {'cash_boot': 1000.0, 'source_basis_total': 4000.0})
        self.assertAlmostEqual(_usd_acquired(rows), 1000.0)


if __name__ == "__main__":
    unittest.main()
