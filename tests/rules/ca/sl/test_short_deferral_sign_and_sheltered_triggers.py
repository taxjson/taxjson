"""Regression pins for the 2026-09 deep-audit fixes.

Each test is a distilled repro of a CONFIRMED wrong-number or
wrong-advice finding; the fixed behavior is asserted, so none of them
can quietly return.

Formerly: test_audit_2026_09_fixes
"""
import io
import unittest
from contextlib import redirect_stderr

from taxjson.engine.by_country import get_tax_rules
from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _t(**kw):
    d = dict(action='BUYSELL', date='2026-01-05', time='09:30:00',
             symbol='Q.TO', quantity=100.0, currency='CAD',
             net_amount=1000.0, account='A')
    d.update(kw)
    return TaxTransaction(**d)


class TestEngineShortAdjustSign(unittest.TestCase):
    """Blended book, one symbol: account A short, account B long. A
    short-side superficial loss's deferral ADJUST used to land on the
    (net LONG) global pool with a NEGATIVE sign — reducing long ACB,
    the opposite of a deferral — and the stale DISALLOW from a
    flipped-to-gain iteration then reported a denied 'loss' on a raw
    GAIN. Conservation must hold: realized + still-deferred = no-wash
    baseline.

    Formerly: test_audit_2026_09_fixes.TestEngineShortAdjustSign"""

    def _book(self):
        return [
            # B: plain long position (the global pool's direction).
            _t(account='B', date='2025-01-02', quantity=200,
               net_amount=2000.0),
            # A: short round trip at a loss, replacement short opens
            # in-window.
            _t(account='A', date='2025-01-03', quantity=-100,
               net_amount=800.0),
            _t(account='A', date='2025-01-10', quantity=100,
               net_amount=1000.0),          # cover at a 200 loss
            _t(account='A', date='2025-01-20', quantity=-100,
               net_amount=700.0),           # replacement short
            _t(account='A', date='2025-02-03', quantity=100,
               net_amount=1000.0),          # cover again (loss)
            _t(account='A', date='2025-02-15', quantity=-200,
               net_amount=1200.0),
        ]

    def test_conserves_against_no_wash_baseline(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            wash = get_tax_rules('canada').compute_gains(self._book())
            base = get_tax_rules('canada').compute_gains(
                self._book(), detect_wash_sales=False)
        base_total = sum(g['gain'] for g in base['transactions']
                         if g.get('qty') and 'gain' in g)
        wash_total = sum(g['gain'] for g in wash['transactions']
                         if g.get('qty') and 'gain' in g)
        deferred = sum(float(r.get('deferred_wash') or 0.0)
                       for r in wash.get('inventory') or [])
        self.assertAlmostEqual(wash_total - deferred, base_total,
                               places=2)

    def test_no_disallowance_on_a_raw_gain(self):
        with redirect_stderr(io.StringIO()):
            wash = get_tax_rules('canada').compute_gains(self._book())
        for g in wash['transactions']:
            if not g.get('qty') or 'gain' not in g:
                continue
            raw = float(g.get('raw_gain', g['gain']) or 0)
            dis = float(g.get('disallowed_amount') or 0)
            if dis > 0.001:
                self.assertLess(raw, 0.001,
                                f"disallowance attached to a raw "
                                f"gain: {g}")


@rule(R.CA_SL_03, R.CA_SL_09)
class TestShelteredContributionIsATrigger(unittest.TestCase):
    """Formerly: test_audit_2026_09_fixes.TestShelteredContributionIsATrigger"""
    def _run_with_sheltered(self, spelling, shel_date, strict=False):
        from taxjson.lib.pipeline import prepare_books
        main = [_t(account='margin', date='2024-05-01', quantity=100,
                   net_amount=10000.0),
                _t(account='margin', date='2024-06-10', quantity=-100,
                   net_amount=8000.0)]
        shel = [_t(action=spelling, account='rrsp', date=shel_date,
                   quantity=100, net_amount=8100.0)]
        with redirect_stderr(io.StringIO()):
            m, sh, af, _ = prepare_books(
                main, shel, taxable=False, phantom_hint=False,
                transfers_as_acquisitions=strict)
            return get_tax_rules('canada').compute_gains(
                m, sheltered_transactions=sh,
                affiliated_transactions=af)

    def test_buysell_contribution_in_window_denies(self):
        # The canonical permanently-denied superficial loss, spelled
        # explicitly: taxable loss sale, sheltered BUYSELL acquisition
        # of identical shares inside the window.
        r = self._run_with_sheltered('BUYSELL', '2024-06-20')
        self.assertGreater(sum(
            float(w.get('disallowed_amount') or 0)
            for w in r.get('wash_sales') or []), 0)

    @rule(R.CA_SL_17)
    def test_transfer_in_window_demands_a_declaration(self):
        # transfers_as_acquisitions = true (the strict policy): a
        # TRANSFER's date is a broker ARRIVAL date; if it lands in a
        # trigger window the engine refuses to guess whether it was a
        # contribution (a real acquisition — deny) or a custody move
        # (not an acquisition — allow). The error names both
        # resolutions.
        from taxjson.engine.rows import AmbiguousTransferDateError
        with self.assertRaises(AmbiguousTransferDateError) as cm:
            self._run_with_sheltered('TRANSFER', '2024-06-20',
                                     strict=True)
        self.assertIn('ARRIVAL', str(cm.exception))
        self.assertIn('custody', str(cm.exception))

    def test_transfer_outside_window_neither_triggers_nor_backs(self):
        # An OLD sheltered transfer-in never triggers (s.54(a) needs an
        # in-window acquisition), and — re-premised by the 2026-09
        # engine audit — it does not satisfy the still-held limb
        # either: s.54 asks whether the taxpayer or an affiliated
        # person owns the SUBSTITUTED property (the property acquired
        # in the window) at its end. The in-window taxable rebuy is
        # gone by day +30 and the RRSP's shares predate the window, so
        # the loss is allowed. (This test used to pin a 600 denial
        # backed by the RRSP's old balance — permanent, since the
        # taxable pool held nothing to bump.)
        from taxjson.lib.pipeline import prepare_books
        main = [_t(account='margin', date='2024-05-01', quantity=100,
                   net_amount=10000.0),
                _t(account='margin', date='2024-06-10', quantity=-100,
                   net_amount=8000.0),          # loss 2,000 (20/sh)
                _t(account='margin', date='2024-06-25', quantity=30,
                   net_amount=2400.0),          # in-window trigger
                _t(account='margin', date='2024-07-05', quantity=-30,
                   net_amount=3200.0)]          # gone before +30, at a
                                                # gain even after the
                                                # 600 deferral lands
        shel = [_t(action='TRANSFER', account='rrsp',
                   date='2024-04-01', quantity=100,
                   net_amount=8100.0)]          # balance only
        with redirect_stderr(io.StringIO()):
            m, sh, af, _ = prepare_books(main, shel, taxable=False,
                                         phantom_hint=False)
            r = get_tax_rules('canada').compute_gains(
                m, sheltered_transactions=sh,
                affiliated_transactions=af)
        denied = sum(float(w.get('disallowed_amount') or 0)
                     for w in r.get('wash_sales') or [])
        self.assertAlmostEqual(denied, 0.0, places=2)

    def test_old_sheltered_balance_alone_denies_nothing(self):
        # The user's rule, pinned: no acquisition in the +/-30 window
        # means no superficial loss, no matter what an affiliated
        # account has held for months.
        from taxjson.lib.pipeline import prepare_books
        main = [_t(account='margin', date='2024-05-01', quantity=100,
                   net_amount=10000.0),
                _t(account='margin', date='2024-06-10', quantity=-100,
                   net_amount=8000.0)]
        shel = [_t(action='TRANSFER', account='rrsp',
                   date='2024-04-01', quantity=100,
                   net_amount=8100.0)]
        with redirect_stderr(io.StringIO()):
            m, sh, af, _ = prepare_books(main, shel, taxable=False,
                                         phantom_hint=False)
            r = get_tax_rules('canada').compute_gains(
                m, sheltered_transactions=sh,
                affiliated_transactions=af)
        self.assertEqual(sum(
            float(w.get('disallowed_amount') or 0)
            for w in r.get('wash_sales') or []), 0)

    def test_registered_to_registered_move_is_not_a_trigger(self):
        from taxjson.lib.pipeline import prepare_books
        shel = [_t(action='TRANSFER', account='rrsp',
                   date='2024-06-18', quantity=-100, net_amount=8000.0),
                _t(action='TRANSFER', account='rrsp2',
                   date='2024-06-20', quantity=100, net_amount=8000.0)]
        with redirect_stderr(io.StringIO()):
            _, sh, _, _ = prepare_books([], shel, taxable=False,
                                        phantom_hint=False)
        # Kept only as balance-only TRANSFER rows (never a trigger;
        # the per-holder still-held test needs them — S018-05).
        self.assertEqual([(t.action, t.type) for t in sh],
                         [('TRANSFER', 'own_account_move')] * 2)


if __name__ == '__main__':
    unittest.main()
