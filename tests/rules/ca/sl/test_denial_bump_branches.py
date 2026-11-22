"""A2-1596: engine branches the suite never executed.

Each branch carries a `# cov: a2-1596-<name>` marker on its first body
line; every test here runs a synthetic book that reaches the branch,
checks the tax result, and asserts through tests/_tripwire.py that the
marked line really ran (so a refactor that strands the branch again
fails here, not silently). Branches proven unreachable were removed
instead; the proofs are in the commit messages and in
TestRemovedBranches below. All data synthetic; account labels fake.

Formerly: test_fix_a2_cov_branches
"""
import contextlib
import io
import unittest

import _engine
from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R
from _tripwire import Tripwire


def _tx(d, q, net, *, sym='XYZ.TO', acct='margin', t='10:00:00',
        action='BUYSELL', cur=None, **kw):
    return TaxTransaction(
        action=action, date=d, date_settle=d, time=t, symbol=sym,
        quantity=float(q), price=abs(float(net) / float(q)) if q else 0.0,
        net_amount=float(net),
        currency=cur or ('USD' if sym.endswith('.US') else 'CAD'),
        account=acct, **kw)


def _run(engine, marker, tax, shel=None, **kw):
    """(result, stderr, branch ran?) for one engine pass."""
    tw = Tripwire(_engine.file_with(marker), marker)
    err = io.StringIO()
    with tw, contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = engine().compute_gains(tax, sheltered_transactions=shel, **kw)
    return res, err.getvalue(), tw.ran(marker)


def _records(res, sym='XYZ.TO'):
    return [(e['date'], round(e['gain'], 2),
             round(e.get('disallowed_amount') or 0.0, 2))
            for e in res['transactions']
            if e.get('symbol') == sym and 'gain' in e]


class TestStaleDisallowRetract(unittest.TestCase):
    """CA-SL-08/09: a pre-loss bump lands after the sale's last losing
    fill — and stays there once a pass put it there.

    Reaching the stale-DISALLOW retraction showed the solver oscillating
    on a two-fill sale: a second fill priced just above the ACB was a
    loss when costed after the bump (so it joined the formula's units
    sold and shrank the bump), a gain once the bump shrank (so the bump
    grew back) — 1000 passes, no convergence, and a summary that
    disagreed with the records. The placement now only moves later
    across passes and an existing bump follows it."""

    @rule(R.CA_SL_08, R.CA_SL_09)
    def test_two_fill_sale_with_a_pre_loss_replacement_converges(self):
        tax = [_tx('2025-01-02', 100, 1000), _tx('2025-01-22', 100, 1000),
               _tx('2025-01-27', -100, 900, t='10:00:00'),
               _tx('2025-01-27', -50, 520, t='10:00:01'),
               _tx('2025-04-01', -50, 550)]
        res, err, ran = _run(CanadaTaxRules,
                             'a2-1596-stale-disallow-retract', tax)
        self.assertTrue(res['summary']['wash_solver_converged'], err)
        # The 100 sold at a loss: min(100 sold, 100 bought in the window,
        # 50 held at day 30) = 50 units denied = 50.00. The gaining fill
        # keeps the pre-bump ACB; the 50 kept carry the bump (550).
        self.assertEqual(_records(res), [('2025-01-27', -50.0, 50.0),
                                         ('2025-01-27', 20.0, 0.0),
                                         ('2025-04-01', 0.0, 0.0)])
        self.assertAlmostEqual(res['summary']['total_disallowed'], 50.0)
        self.assertNotIn('invariant broken', err)
        # On the way the second fill was a loss for one pass and its
        # denial is withdrawn once it is a gain again.
        self.assertTrue(ran, 'stale-DISALLOW retraction not reached')


class TestGrantLossRefOnAssignment(unittest.TestCase):
    """CA-SL-12 + CA-OPT-06: under grant timing a write whose commission
    exceeds its premium is a loss on the grant; when the option is then
    assigned, s.49(3.1) says the granting was no disposition, so the
    assigned contracts leave the grant record AND the loss the
    superficial-loss solver sees (the loss_ref reduction)."""

    P = 'XYZ251219P00010000.TO'

    def _book(self, assign_n):
        b = [_tx('2025-11-03', -2, -20, sym=self.P, commission=30.0)]
        if assign_n:
            b += [_tx('2025-11-20', assign_n, 0, sym=self.P,
                      action='ASSIGN', t='16:00:00'),
                  _tx('2025-11-20', 100 * assign_n, 1000 * assign_n,
                      t='16:00:00'),
                  _tx('2026-03-02', -100 * assign_n, 1200 * assign_n)]
        return b

    def _go(self, assign_n):
        # The same put bought in the RRSP inside the window and held at
        # day 30 replaces the grant loss (denied for good).
        rrsp = [_tx('2025-11-05', 2, 10, sym=self.P, acct='rrsp')]
        return _run(CanadaTaxRules, 'a2-1596-grant-loss-ref',
                    self._book(assign_n), rrsp,
                    option_premium_timing='grant', option_grant_since=2025,
                    option_buyback_loss_superficial=True)

    @rule(R.CA_SL_12, R.CA_OPT_01, R.CA_OPT_06)
    def test_partly_assigned_loss_write_denies_only_the_rest(self):
        res, err, ran = self._go(0)
        self.assertEqual(_records(res, self.P), [('2025-11-03', 0.0, 20.0)])
        res, err, ran = self._go(1)
        self.assertTrue(ran, 'grant loss_ref reduction not reached')
        # One contract assigned: its -10 premium folds into the shares
        # (ACB 1010); the grant record and its denial keep one contract.
        self.assertEqual(_records(res, self.P), [('2025-11-03', 0.0, 10.0)])
        self.assertEqual([round(w['disallowed_amount'], 2)
                          for w in res['wash_sales']], [10.0])
        self.assertEqual(_records(res), [('2026-03-02', 190.0, 0.0)])

    @rule(R.CA_SL_12, R.CA_OPT_06)
    def test_fully_assigned_loss_write_denies_nothing(self):
        res, err, ran = self._go(2)
        self.assertTrue(ran)
        self.assertEqual(_records(res, self.P), [])
        self.assertEqual(res['wash_sales'], [])
        self.assertEqual(_records(res), [('2026-03-02', 380.0, 0.0)])


class TestUsOtherScopeBalances(unittest.TestCase):
    """US-WASH-05 / US-STKDIV-01: an IRA's missing-history opening
    balance and its stock dividends move the IRA's running balance, so
    a later IRA sale drawing on them closes a long — it is neither a
    re-short that replaces a short-cover loss nor a sale "beyond its
    recorded balance"."""

    S = 'XYZ.US'

    def _tax(self):
        # A short-cover loss of 200 in the taxable account.
        return [_tx('2025-03-03', -100, 1000, sym=self.S),
                _tx('2025-03-13', 100, 1200, sym=self.S)]

    @rule(R.US_WASH_05, R.US_WASH_04)
    def test_ira_opening_balance_backs_a_later_ira_sale(self):
        ira = [_tx('2024-01-02', 100, 500, sym=self.S, acct='ira',
                   action='OPENING_BALANCE'),
               _tx('2025-03-18', -100, 1100, sym=self.S, acct='ira')]
        res, err, ran = _run(USATaxRules, 'a2-1596-us-other-opening',
                             self._tax(), ira)
        self.assertTrue(ran, 'other-scope opening balance not reached')
        self.assertEqual(_records(res, self.S),
                         [('2025-03-13', -200.0, 0.0)])
        self.assertNotIn('beyond its recorded balance', err)

    @rule(R.US_STKDIV_01, R.US_WASH_04)
    def test_ira_stock_dividend_backs_a_later_ira_sale(self):
        ira = [_tx('2025-01-06', 100, 1000, sym=self.S, acct='ira'),
               _tx('2025-02-14', 10, 0, sym=self.S, acct='ira',
                   type='stock_dividend'),
               _tx('2025-03-18', -110, 1100, sym=self.S, acct='ira')]
        res, err, ran = _run(USATaxRules, 'a2-1596-us-other-stock-div',
                             self._tax(), ira)
        self.assertTrue(ran, 'other-scope stock dividend not reached')
        self.assertEqual(_records(res, self.S),
                         [('2025-03-13', -200.0, 0.0)])
        self.assertNotIn('beyond its recorded balance', err)


def _rename(d, old, new):
    return TaxTransaction(action='SPLIT', date=d, date_settle=d,
                          time='00:00:01', symbol=old, symbol_new=new,
                          quantity=1.0, currency='CAD', account='margin')


class TestRoutedBumpLandsOnAnotherPool(unittest.TestCase):
    """CA-SL-09 with a rename into a live symbol (CA-ACB-RENAME): before
    the rename date OLD and NEW's rows inside the rename's split window
    are one identical property (NEW's earlier rows are another security:
    TJ-2699), but the engine keeps their pools apart until the rename
    merges them. A bump
    whose trigger's own pool no longer holds the substituted property at
    day 30 lands on the pool that does — after the purchase that opened
    the position held at day 30 when one does (TJ-2711), else FLAT (parked
    until the pool's next opening, or carried by the rename into the
    target pool)."""

    O, N = 'OLD.TO', 'NEW.TO'

    def _sym_records(self, res):
        return sorted((e['symbol'], e['date'], round(e['gain'], 2),
                       round(e.get('disallowed_amount') or 0.0, 2))
                      for e in res['transactions']
                      if e.get('symbol') in (self.O, self.N)
                      and 'gain' in e)

    @rule(R.CA_SL_09, R.CA_ACB_RENAME)
    def test_bump_routed_to_a_short_pool(self):
        """The routed pool is short (an unmarked sale past the holding)
        when the loss is denied and the purchase that covers it opens the
        100 held at day 30. The bump used to land on the short pool
        (lowering its entry proceeds, `a2-1596-wash-short-pool-sign`) and
        the cover's loss was denied again onto the 100; since TJ-2711 it
        goes after that purchase, on the long position, so the branch is
        not reached by this book: the cover is the short's own 0 and the
        one denial comes back on the June sale (cost 2000 = 1000 paid +
        the 1000 deferred). The branch stays for a short pool brought
        long by no purchase row. The NEW purchase is dated inside the
        rename's split window (TJ-2699: a week earlier it would be another
        security and the loss allowed)."""
        O, N = self.O, self.N
        tax = [_tx('2025-01-02', 100, 2000, sym=O),
               _tx('2025-03-03', -100, 1000, sym=O),      # loss 1000
               _tx('2025-03-08', 100, 1000, sym=O),       # trigger,
               _tx('2025-03-13', -100, 1000, sym=O),      # sold again
               _tx('2025-01-10', -100, 1000, sym=N),      # NEW short
               _tx('2025-03-26', 200, 2000, sym=N),       # cover + 100
               _rename('2025-04-01', O, N),
               _tx('2025-06-02', -100, 1200, sym=N)]
        res, err, ran = _run(CanadaTaxRules,
                             'a2-1596-wash-short-pool-sign', tax)
        self.assertFalse(ran, 'the bump landed on the short pool')
        self.assertTrue(res['summary']['wash_solver_converged'], err)
        self.assertEqual(self._sym_records(res), [
            ('NEW.TO', '2025-03-26', 0.0, 0.0),
            ('NEW.TO', '2025-06-02', -800.0, 0.0),
            ('OLD.TO', '2025-03-03', 0.0, 1000.0),
            ('OLD.TO', '2025-03-13', 0.0, 0.0)])
        self.assertEqual([(a['date'], a['symbol']) for w in res['wash_sales']
                          for a in w['adjusts']], [('2025-03-26', N)])
        self.assertAlmostEqual(res['summary']['total_gain'], -800.0)

    @rule(R.CA_SL_09, R.CA_SL_10, R.CA_ACB_RENAME)
    def test_a_bump_lands_on_the_target_pool_that_holds_the_rest(self):
        """The cross-pool routing (`_adjust_land[trg.id] = _pool_repr[_best]`,
        the `a2-1596-wash-cross-pool` tripwire) under TJ-2699 (TJ-3294):
        an OLD loss 02-25, the rebuy of 02-27 sold down to 40 on 03-12,
        100 NEW bought 03-26 (inside the rename's split window) and held,
        day 30 (03-27) before the 04-01 rename. The 1000 is backed by the
        40 OLD held and the 100 NEW: what OLD's pool cannot carry lands
        on NEW's. The 03-12 sale 0, the 06-02 sale of 140 -720 (1680
        against 2400: 400 + 1000 + the 1000 denied)."""
        old, new = self.O, self.N
        tax = [_tx('2025-01-02', 100, 2000, sym=old),
               _tx('2025-02-25', -100, 1000, sym=old),    # loss 1000
               _tx('2025-02-27', 100, 1000, sym=old),     # trigger,
               _tx('2025-03-12', -60, 600, sym=old),      # 40 kept
               _tx('2025-03-26', 100, 1000, sym=new),     # in the split window
               _rename('2025-04-01', old, new),
               _tx('2025-06-02', -140, 1680, sym=new)]
        res, err, ran = _run(CanadaTaxRules, 'a2-1596-wash-cross-pool', tax)
        self.assertTrue(ran, 'the cross-pool routing was not reached')
        self.assertTrue(res['summary']['wash_solver_converged'], err)
        self.assertEqual(self._sym_records(res), [
            ('NEW.TO', '2025-06-02', -720.0, 0.0),
            ('OLD.TO', '2025-02-25', 0.0, 1000.0),
            ('OLD.TO', '2025-03-12', 0.0, 0.0)])
        self.assertEqual([round(w['disallowed_amount'], 2)
                          for w in res['wash_sales']], [1000.0])

    @rule(R.CA_SL_09, R.CA_SL_19, R.CA_ACB_RENAME)
    def test_bump_parked_on_a_flat_pool_rides_the_rename(self):
        """The addition waiting on a flat pool reaches the target with the
        rename that brings the shares in (TJ-2754's book): OLD's rebuy sold
        out, no purchase opens the held position, BBB's 100 held since
        October renamed into OLD inside the window and within a week of
        the loss (TJ-2699: with the rename of 12-20 first written, the
        loss is OLD's own row before the rename's split window, another
        security than BBB's shares, and nothing is parked)."""
        old = self.O
        tax = [_tx('2024-10-01', 100, 1500, sym='BBB.TO'),
               _tx('2024-11-01', 100, 2000, sym=old),
               _tx('2024-12-03', -100, 1000, sym=old),      # loss 1000
               _tx('2024-12-04', 100, 1000, sym=old),       # trigger,
               _tx('2024-12-06', -100, 1000, sym=old),      # sold out
               _rename('2024-12-09', 'BBB.TO', old),
               _tx('2025-06-02', -100, 1200, sym=old)]
        res, err, ran = _run(CanadaTaxRules,
                             'a2-1596-rename-pending-wash', tax)
        self.assertTrue(ran, 'parked bump across a rename not reached')
        june = next(e for e in res['transactions']
                    if e.get('symbol') == old and e['date'] == '2025-06-02')
        # 1500 paid for BBB's shares + the 1000 waiting from the sell-out.
        self.assertEqual((round(june['cost'], 2), round(june['gain'], 2)),
                         (2500.0, -1300.0))
        self.assertEqual([(w['loss_tx']['date'], w['disallowed_amount'])
                          for w in res['wash_sales']],
                         [('2024-12-03', 1000.0)])

    @rule(R.CA_SL_19, R.CA_ACB_RENAME, R.CA_OPEN_01)
    def test_an_opening_balance_takes_the_parked_bump_before_the_rename(self):
        """OLD held at day 30 only through missing history: the parked
        bump used to ride the rename into NEW; since TJ-2768 the opening
        balance takes it as a purchase does, and the rename carries OLD's
        cost. The June sale's cost is the deferred 1000 either way (the
        shares themselves are missing history: listed, not totalled); the
        rename's branch is not reached by this book. The loss is OLD's
        own, the source's (as first written it was NEW's, more than a
        week before the rename: under TJ-2699 another security than OLD's
        opening balance, so nothing is denied or parked)."""
        old, new = self.O, self.N
        tax = [_tx('2025-01-02', 100, 2000, sym=old),
               _tx('2025-02-03', -100, 1000, sym=old),      # loss 1000
               _tx('2025-02-05', 100, 1000, sym=old),       # trigger,
               _tx('2025-02-10', -100, 1000, sym=old),      # sold again
               _tx('2025-02-20', 100, 500, sym=old, action='OPENING_BALANCE'),
               _rename('2025-04-01', old, new),
               _tx('2025-06-02', -100, 1200, sym=new)]
        res, err, ran = _run(CanadaTaxRules,
                             'a2-1596-rename-pending-wash', tax)
        self.assertFalse(ran, 'the rename found a parked bump')
        june = next(e for e in res['transactions']
                    if e.get('symbol') == new and e['date'] == '2025-06-02')
        self.assertEqual((round(june['cost'], 2), bool(june['tainted'])),
                         (1000.0, True))
        self.assertEqual([(w['loss_tx']['date'], w['disallowed_amount'])
                          for w in res['wash_sales']],
                         [('2025-02-03', 1000.0)])

class TestRemovedBranches(unittest.TestCase):
    """Branches removed as unreachable (A2-1596), with the invariant
    that made each dead:

    * _short_lot_close's `rem > 0` remainder: every short opening of an
      option pool under grant timing opens a lot (a write, the short
      leftover of a crossing sell, a missing-history short opening), a
      split scales the lots with the pool, a rename merges both, a drain
      clears both and a close takes the same quantity from each, so the
      lots always cover the closed quantity.
    * _opening_qty's short side: every caller asks for the long side,
      because only a long acquisition replaces in Canada (CA-SL-07).
    * The US engine's per-symbol move of replacement records at a
      rename: they are keyed by the dated identity class (_rep_key ->
      SplitTimeline.class_at), whose root is never the renamed-away
      ticker, so the moved key never existed."""

    P = 'XYZ251219C00010000.TO'

    @rule(R.CA_OPT_01, R.CA_OPT_03)
    def test_grant_lots_cover_every_close(self):
        # Long 1, sell 3 (closes 1, writes 2), write 1 more, buy back 3:
        # the pool drains to zero and the records add up to the cash.
        book = [_tx('2025-03-03', 1, 100, sym=self.P),
                _tx('2025-03-10', -3, 450, sym=self.P),
                _tx('2025-03-17', -1, 120, sym=self.P),
                _tx('2025-04-01', 3, 240, sym=self.P)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            res = CanadaTaxRules().compute_gains(
                book, option_premium_timing='grant', option_grant_since=2025)
        self.assertEqual([i for i in res['inventory']
                          if i['symbol'] == self.P
                          and abs(i.get('qty') or 0) > 1e-9], [])
        total = round(sum(e['gain'] for e in res['transactions']
                          if e.get('symbol') == self.P and 'gain' in e), 2)
        self.assertEqual(total, round(-100 + 450 + 120 - 240, 2))


if __name__ == '__main__':
    unittest.main()
