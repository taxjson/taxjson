"""The assignment premium ledger (engine/premium_ledger.py): what a stock
leg takes of a staged premium comes back from `take` itself (`Taken`:
the amount, the earlier denied loss in it, the unknown cost and its
rows), split by shares when the delivery comes in parts; a checkpoint is
the staged entries alone, and a restore leaves none staged after it; the
look-ahead indexes are read by name (`lookahead`).

PLAN step 13, commit 12. An option's cost rolls into the shares it
delivers (CA-OPT-01), the denied loss in it with it (CA-SL-09). Synthetic
data only.
"""
import contextlib
import copy
import io
import unittest
from decimal import Decimal

from taxjson.engine.premium_ledger import Taken, _AssignPremiumLedger, nothing_taken
from taxjson.lib import option_symbols as OS
from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R

CALL = 'ZZQ250321C00015000'


def _t(action, date, symbol, qty, net, id_, **kw):
    return TaxTransaction(action=action, date=date, time='10:00:00', symbol=symbol,
                          quantity=qty, price=abs(net / qty) if qty else 0.0,
                          net_amount=net, currency='CAD', account='b', id=id_, **kw)


def _ledger(rows, pairs=None):
    return _AssignPremiumLedger(
        rows, lambda t: t.action in ('BUYSELL', 'ASSIGN') and not OS.is_option_symbol(t.symbol),
        pairs)


class _Quiet(unittest.TestCase):
    """The ledger's notes (an assumed contract size, a shared warrant cost)
    go to stderr: kept, not printed."""

    def setUp(self):
        self.said = io.StringIO()
        stack = contextlib.ExitStack()
        stack.enter_context(contextlib.redirect_stderr(self.said))
        self.addCleanup(stack.close)


class TestATakeReturnsItsFacts(_Quiet):

    def _book(self):
        """A long call exercised (1 contract, 100 shares), delivered in
        two legs of 60 and 40."""
        ex = _t('ASSIGN', '2025-03-21', CALL, -1, 0.0, 'x1')
        legs = [_t('BUYSELL', '2025-03-21', 'ZZQ', 60, 900.0, 'l1'),
                _t('BUYSELL', '2025-03-21', 'ZZQ', 40, 600.0, 'l2')]
        return ex, legs, _ledger([ex] + legs)

    @rule(R.CA_OPT_01, R.CA_SL_09)
    def test_two_legs_share_the_premium_and_the_denied_loss_in_it_by_shares(self):
        # 250 of cost, 100 of it an earlier denied loss, from contracts held
        # at an unknown cost named by one opening row.
        ex, legs, led = self._book()
        src = {'date': '2025-01-02', 'symbol': CALL}
        led.stage(ex, 'ZZQ', Decimal(250), Decimal(100), tainted=True, sources=[src])
        first, second = led.take(legs[0]), led.take(legs[1])
        self.assertEqual(first, Taken(Decimal(150), Decimal(60), True, [src], []))
        self.assertEqual(second, Taken(Decimal(100), Decimal(40), True, [src], []))
        self.assertEqual(led.undrained(), {})

    @rule(R.CA_OPT_09, R.CA_SL_09)
    def test_a_warrant_of_no_stated_size_is_shared_by_the_days_deliveries(self):
        # 100 warrants exercised with no shares per warrant stated: its
        # cost of 300 (90 of it an earlier denied loss) is shared by
        # quantity between the day's two deliveries, 60 and 40 shares.
        ex = _t('ASSIGN', '2025-03-21', 'ZZQ.WT', -100, 0.0, 'x1', exercise_of='ZZQ')
        legs = [_t('BUYSELL', '2025-03-21', 'ZZQ', 60, 900.0, 'l1'),
                _t('BUYSELL', '2025-03-21', 'ZZQ', 40, 600.0, 'l2')]
        led = _ledger([ex] + legs)
        led.stage(ex, 'ZZQ', Decimal(300), Decimal(90))
        self.assertEqual(led.take(legs[0]), Taken(Decimal(180), Decimal(54), False, [], []))
        self.assertEqual(led.take(legs[1]), Taken(Decimal(120), Decimal(36), False, [], []))
        self.assertIn('states no shares per warrant', self.said.getvalue())

    @rule(R.CA_OPT_01)
    def test_a_row_with_nothing_staged_takes_nothing(self):
        ex, legs, led = self._book()
        self.assertEqual(led.take(legs[0]), nothing_taken())
        self.assertEqual(nothing_taken(), Taken(Decimal(0), Decimal(0), False, [], []))
        # A take's lists are its own: one take's facts never reach the next.
        self.assertIsNot(nothing_taken().sources, nothing_taken().sources)


class TestACheckpointIsTheStagedEntries(_Quiet):

    @rule(R.CA_OPT_01)
    def test_a_restore_leaves_nothing_staged_after_the_checkpoint(self):
        ex = _t('ASSIGN', '2025-03-21', CALL, -1, 0.0, 'x1')
        other = _t('ASSIGN', '2025-03-21', 'ZZR250321C00015000', -1, 0.0, 'x2')
        legs = [_t('BUYSELL', '2025-03-21', 'ZZQ', 100, 1500.0, 'l1'),
                _t('BUYSELL', '2025-03-21', 'ZZR', 100, 1500.0, 'l2')]
        led = _ledger([ex, other] + legs)
        led.stage(ex, 'ZZQ', Decimal(250))
        saved = copy.deepcopy(led.fold_state())
        led.stage(other, 'ZZR', Decimal(70))            # after the checkpoint
        self.assertEqual(led.take(legs[0]).amount, Decimal(250))
        led.restore(copy.deepcopy(saved))
        self.assertEqual(led.undrained(), {('b', 'ZZQ'): Decimal(250)})
        self.assertEqual(led.take(legs[1]), nothing_taken())
        self.assertEqual(led.take(legs[0]).amount, Decimal(250))


class TestTheLookAheadIndexesByName(_Quiet):

    @rule(R.CA_OPT_01)
    def test_each_index_is_the_one_its_name_says(self):
        ex = _t('ASSIGN', '2025-03-21', CALL, -1, 0.0, 'x1')
        leg = _t('BUYSELL', '2025-03-21', 'ZZQ', 100, 1500.0, 'l1')
        ix = _ledger([ex, leg]).lookahead()
        self.assertEqual(ix.legs, {('b', 'ZZQ'): [(1, '2025-03-21', 100.0)]})
        self.assertEqual(ix.leg_id, {1: 'l1'})
        self.assertEqual(ix.leg_px, {1: Decimal(15)})
        self.assertEqual(ix.leg_row, {'l1': leg})
        self.assertEqual(ix.kept_days, {('b', 'ZZQ'): ['2025-03-21']})
        self.assertEqual(ix.snap, {})
        self.assertEqual(ix.id_pos, {'x1': 0, 'l1': 1})


if __name__ == '__main__':
    unittest.main()
