"""The Canadian fixed-point loop (`_ca_solve`, step 4o of the engine refactor).

The non-convergence fallback (backlog TJ-2027): a book whose denial pass never
settles stops after 1,000 passes, the last pass's results stand in, the
solver's verdict says so and a warning is emitted. No real book reaches it,
so the loop's post-loop reads of its locals are pinned here.

The forward denial pass (recommendation 10 of the 2026-11-07 re-extraction,
`_WalkFold`): an iteration restores the walk's state checkpointed before the
first row the last pass changed and walks on from there. It must end where a
whole-book walk ends: every figure, message and the pass count. Pinned against
the fixed point itself (the fold's cut forced to 0 is the old loop: a whole-book
walk from a fresh state every pass) on seeded fuzz books, with the trace and the
cost timeline, and on a book whose restore lands between a written option's
grant record and the assignment that retracts it (s.49(3)): the record a lot
points at must stay the list's record across the restore."""
import contextlib
import io
import json
import unittest
from unittest import mock

import _engine
from taxjson.engine.ca.denial import _superficial_loss_pass
from taxjson.engine.assign import _place_assign_options
from taxjson.engine.premium_ledger import _AssignPremiumLedger
from taxjson.engine.ca.fold import _WalkFold
from taxjson.lib import option_symbols as OS
from taxjson.lib import row_types as RT
from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R


class TestSolverFallback(unittest.TestCase):
    def test_a_pass_that_never_settles_reports_the_last_one(self):
        rows = [
            TaxTransaction(action='BUYSELL', date='2025-01-15', symbol='X',
                           quantity=100, price=10.0, net_amount=1000.0,
                           currency='CAD', account='Margin'),
            TaxTransaction(action='BUYSELL', date='2025-06-20', symbol='X',
                           quantity=-40, price=8.0, net_amount=320.0,
                           currency='CAD', account='Margin'),
        ]
        real = _superficial_loss_pass

        def never_settled(*, run, it):
            real(run=run, it=it)
            return True, {}      # "a new denial", every pass

        err, out = io.StringIO(), io.StringIO()
        with _engine.patch_engine('_superficial_loss_pass', never_settled), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            result = CanadaTaxRules().compute_gains(rows)
        self.assertFalse(result['summary']['wash_solver_converged'])
        self.assertEqual(result['summary']['wash_solver_iterations'], 1000)
        self.assertIn('did not converge', err.getvalue() + out.getvalue())
        # The last pass's results stand in: the sale's loss (no repurchase, so
        # the real pass denies nothing) and the pool left.
        gains = [g for g in result['transactions'] if g.get('qty')]
        self.assertEqual(len(gains), 1)
        self.assertAlmostEqual(gains[0]['gain'], 40 * (8.0 - 10.0), places=6)
        self.assertEqual(result['wash_sales'], [])
        self.assertIn('X', str(result['inventory']))


def _T(**kw):
    kw.setdefault('currency', 'CAD')
    kw.setdefault('account', 'Margin')
    kw.setdefault('time', '10:00:00')
    return TaxTransaction(**kw)


def _grant_book():
    """A put written on grant timing (its record is made at the write), eight
    rows of another symbol (so a checkpoint stands between the write and the
    rest), a superficial loss whose rebuy makes the second pass restore to that
    checkpoint, then the put's assignment, which retracts the grant record."""
    rows = [
        _T(action='BUYSELL', date='2024-01-02', symbol='ZZQ', quantity=100, price=10.0, net_amount=1000.0),
        _T(action='BUYSELL', date='2024-01-03', symbol='ZZQ241220P00010000', quantity=-1, price=1.0, net_amount=100.0),
    ]
    for i in range(8):
        q = 10 if i % 2 == 0 else -10
        rows.append(_T(action='BUYSELL', date=f'2024-01-{10 + i:02d}', symbol='QZRX', quantity=q,
                       price=5.0 + i * 0.1, net_amount=abs(q) * (5.0 + i * 0.1)))
    rows += [
        _T(action='BUYSELL', date='2024-02-01', symbol='ZZQ', quantity=-50, price=9.0, net_amount=450.0),
        _T(action='BUYSELL', date='2024-02-10', symbol='ZZQ', quantity=50, price=9.2, net_amount=460.0),
        _T(action='ASSIGN', date='2024-03-15', symbol='ZZQ241220P00010000', quantity=1, price=0.0, net_amount=0.0),
        _T(action='BUYSELL', date='2024-03-15', symbol='ZZQ', quantity=100, price=10.0, net_amount=1000.0),
        _T(action='BUYSELL', date='2024-06-03', symbol='ZZQ', quantity=-200, price=11.0, net_amount=2200.0),
    ]
    return rows, {'option_premium_timing': 'grant', 'option_grant_since': 2024}


def _chain(sym, rows, d1, d2, d3, d4, px_in=20.0):
    """A superficial loss whose denial is re-amounted on the second pass: the
    first loss's bump (placed after its rebuy) raises the pool's ACB, so the
    second sale's loss is larger once it is in, and the third pass walks on
    from the second sale (a mid-book resume for the fold)."""
    rows += [
        _T(action='BUYSELL', date=d1, symbol=sym, quantity=-50, price=15.0, net_amount=750.0),
        _T(action='BUYSELL', date=d2, symbol=sym, quantity=50, price=15.0, net_amount=750.0),
        _T(action='BUYSELL', date=d3, symbol=sym, quantity=-50, price=15.0, net_amount=750.0),
        _T(action='BUYSELL', date=d4, symbol=sym, quantity=50, price=15.0, net_amount=750.0),
    ]


def _filler(rows, n, d0=10, month='02'):
    for i in range(n):
        q = 10 if i % 2 == 0 else -10
        rows.append(_T(action='BUYSELL', date=f'2024-{month}-{d0 + i:02d}', symbol='QZRX',
                       quantity=q, price=5.0, net_amount=50.0))


def _spinoff_book(second_tranche=False):
    """Two s.86.1 spin-offs. The first parent is still held when its ADJUST posts,
    after a return of capital took its cost under its deferred superficial loss
    (`wash_cut`): the deferred share and the cut move to the child through the
    fold's `_cev_stash` and `_cev_cut`, the child row coming after a checkpoint
    (or, `second_tranche`, before it, and a second ADJUST after it reads
    `_cev_child`). The second parent's only holding was sold before its ADJUST
    posts: the s.86.1 cut lowers the earlier sale record's cost, a record changed
    after its row, across the restore."""
    rows = [
        _T(action='BUYSELL', date='2024-01-02', symbol='ZZP', quantity=100, price=10.0, net_amount=1000.0),
        _T(action='BUYSELL', date='2024-01-03', symbol='ZZL', quantity=100, price=20.0, net_amount=2000.0),
        _T(action='BUYSELL', date='2024-01-04', symbol='ZZS', quantity=100, price=5.0, net_amount=500.0),
        _T(action='BUYSELL', date='2024-01-10', symbol='ZZP', quantity=-100, price=8.0, net_amount=800.0),
        _T(action='BUYSELL', date='2024-01-20', symbol='ZZP', quantity=100, price=8.5, net_amount=850.0),
        _T(action='BUYSELL', date='2024-02-01', symbol='ZZS', quantity=-100, price=6.0, net_amount=600.0),
        _T(action='ADJUST', date='2024-02-05', symbol='ZZP', quantity=0, price=0.0, net_amount=-900.0),
    ]
    _chain('ZZL', rows, '2024-01-10', '2024-01-20', '2024-05-01', '2024-05-10')
    spin = [
        _T(action='ADJUST', date='2024-03-01', symbol='ZZP', quantity=0, price=0.0, net_amount=-100.0,
           corp_event_id='E1'),
        _T(action='BUYSELL', date='2024-03-05', symbol='ZZC', quantity=50, price=2.0, net_amount=100.0,
           corp_event_id='E1'),
    ]
    if second_tranche:
        rows += spin
        _filler(rows, 7, d0=10, month='03')
        rows.append(_T(action='ADJUST', date='2024-03-20', symbol='ZZP', quantity=0, price=0.0,
                       net_amount=-25.0, corp_event_id='E1'))
    else:
        _filler(rows, 2)
        rows += spin
    rows += [
        _T(action='ADJUST', date='2024-03-22', symbol='ZZS', quantity=0, price=0.0, net_amount=-150.0,
           corp_event_id='E2', corp_election='rollover_s_86_1', corp_parent_qty=100),
        _T(action='BUYSELL', date='2024-03-22', symbol='ZZD', quantity=30, price=5.0, net_amount=150.0,
           corp_event_id='E2'),
        _T(action='BUYSELL', date='2024-06-03', symbol='ZZL', quantity=-100, price=16.0, net_amount=1600.0),
        _T(action='BUYSELL', date='2024-06-03', symbol='ZZP', quantity=-100, price=9.0, net_amount=900.0),
        _T(action='BUYSELL', date='2024-06-03', symbol='ZZC', quantity=-50, price=9.0, net_amount=450.0),
        _T(action='BUYSELL', date='2024-06-03', symbol='ZZD', quantity=-30, price=6.0, net_amount=180.0),
    ]
    return rows, {}


def _staged_premium_book():
    """A put assigned with its stock leg three days after the option row and a
    checkpoint between them: the premium the ledger staged at the option row
    must come back for the leg to take (`_AssignPremiumLedger.fold_state`)."""
    rows = [
        _T(action='BUYSELL', date='2024-01-02', symbol='ZZQ', quantity=100, price=10.0, net_amount=1000.0),
        _T(action='BUYSELL', date='2024-01-03', symbol='ZZQ241220P00010000', quantity=-1, price=1.0,
           net_amount=100.0),
    ]
    _filler(rows, 5, d0=10, month='01')
    rows += [
        _T(action='ASSIGN', date='2024-03-15', symbol='ZZQ241220P00010000', quantity=1, price=0.0,
           net_amount=0.0),
        _T(action='BUYSELL', date='2024-03-16', symbol='QZRX', quantity=10, price=5.0, net_amount=50.0),
        _T(action='BUYSELL', date='2024-03-18', symbol='ZZQ', quantity=100, price=10.0, net_amount=1000.0),
        _T(action='BUYSELL', date='2024-04-01', symbol='ZZQ', quantity=-50, price=9.0, net_amount=450.0),
        _T(action='BUYSELL', date='2024-04-10', symbol='ZZQ', quantity=50, price=9.2, net_amount=460.0),
        _T(action='BUYSELL', date='2024-06-03', symbol='ZZQ', quantity=-200, price=11.0, net_amount=2200.0),
    ]
    return rows, {'option_premium_timing': 'grant', 'option_grant_since': 2024}


def _journal_book():
    """A listing journal whose two legs straddle a checkpoint (`_jdone` must come
    back with the out-leg booked, or the in-leg books the pair again)."""
    rows = [
        _T(action='BUYSELL', date='2024-01-02', symbol='ZZQ.TO', quantity=100, price=10.0, net_amount=1000.0),
        _T(action='BUYSELL', date='2024-01-03', symbol='ZZL', quantity=100, price=20.0, net_amount=2000.0),
        _T(action='TRANSFER', date='2024-01-05', symbol='ZZQ.TO', quantity=-100, price=0.0, net_amount=0.0,
           type=RT.LISTING_JOURNAL_TYPE, journal_pair='J1'),
    ]
    _chain('ZZL', rows, '2024-01-10', '2024-01-20', '2024-05-01', '2024-05-10')
    _filler(rows, 6)
    rows += [
        _T(action='TRANSFER', date='2024-03-01', symbol='ZZQ.NE', quantity=100, price=0.0, net_amount=0.0,
           type=RT.LISTING_JOURNAL_TYPE, journal_pair='J1'),
        _T(action='BUYSELL', date='2024-06-03', symbol='ZZL', quantity=-100, price=16.0, net_amount=1600.0),
        _T(action='BUYSELL', date='2024-06-04', symbol='ZZQ.NE', quantity=-100, price=12.0, net_amount=1200.0),
    ]
    return rows, {}


def _move_short_book():
    """A blended book of two taxable accounts: an own move of A's shares to B
    (the legs the router applies, `_msr_legs`), a short B opens with a broker's
    mark while it holds those shares (`_msr.long` decides that an unmarked
    purchase goes long, not to the cover), a later unmarked purchase and a
    marked cover, and a loss on the shares whose window reads the routed rows
    (`run._short_routed`)."""
    rows = [
        _T(action='BUYSELL', date='2024-01-02', symbol='ZZQ', quantity=100, price=10.0, net_amount=1000.0, account='A'),
        _T(action='BUYSELL', date='2024-01-03', symbol='ZZL', quantity=100, price=20.0, net_amount=2000.0, account='B'),
        _T(action='TRANSFER', date='2024-01-05', symbol='ZZQ', quantity=-100, price=10.0, net_amount=1000.0,
           account='A', type=RT.LOT_MOVE_TYPE, description='own-account move #1: A -> B'),
        _T(action='TRANSFER', date='2024-01-05', symbol='ZZQ', quantity=100, price=10.0, net_amount=1000.0,
           account='B', type=RT.LOT_MOVE_TYPE, description='own-account move #1: A -> B'),
        _T(action='BUYSELL', date='2024-01-08', symbol='ZZQ', quantity=-50, price=11.0, net_amount=550.0,
           account='B', open_close='O'),
    ]
    rows = [r if r.account == 'A' else r for r in rows]
    chain = []
    _chain('ZZL', chain, '2024-01-10', '2024-01-20', '2024-05-01', '2024-05-10')
    for r in chain:
        r.account = 'B'
    rows += chain
    fill = []
    _filler(fill, 3)
    for r in fill:
        r.account = 'B'
    rows += fill
    rows += [
        _T(action='BUYSELL', date='2024-02-20', symbol='ZZQ', quantity=50, price=10.5, net_amount=525.0, account='B'),
        _T(action='BUYSELL', date='2024-06-03', symbol='ZZL', quantity=-100, price=16.0, net_amount=1600.0, account='B'),
        _T(action='BUYSELL', date='2024-06-05', symbol='ZZQ', quantity=50, price=12.0, net_amount=600.0,
           account='B', open_close='C'),
        _T(action='BUYSELL', date='2024-06-10', symbol='ZZQ', quantity=-150, price=9.0, net_amount=1350.0, account='B'),
        _T(action='BUYSELL', date='2024-06-20', symbol='ZZQ', quantity=60, price=9.5, net_amount=570.0, account='B'),
    ]
    return rows, {}


def _late_move_book():
    """TJ-2696: the own move after the first checkpoint, a denial chain before it:
    a pass resuming before the move finds its legs still pending (`_msr_legs`),
    and the long balance they give the router decides that the unmarked purchase
    after the marked short goes long, not to the cover."""
    def b(sym, d, q, px, **kw):
        return _T(action='BUYSELL', date=d, symbol=sym, quantity=q, price=px,
                  net_amount=abs(q) * px, account='B', **kw)
    rows = [b('ZZQ.TO', '2024-01-02', 100, 10.0), b('ZZL.TO', '2024-01-03', 100, 20.0)]
    rows[0].account = 'A'
    chain = []
    _chain('ZZL.TO', chain, '2024-01-10', '2024-01-20', '2024-02-20', '2024-03-01')
    fill = []
    _filler(fill, 8)
    for r in chain + fill:
        r.account = 'B'
        if r.symbol == 'QZRX':
            r.symbol = 'QZRX.TO'
    rows += chain + fill
    for acct, q in (('A', -100), ('B', 100)):
        rows.append(_T(action='TRANSFER', date='2024-03-05', symbol='ZZQ.TO', quantity=q,
                       price=10.0, net_amount=1000.0, account=acct, type=RT.LOT_MOVE_TYPE,
                       description='own-account move #1: A -> B'))
    rows += [b('ZZQ.TO', '2024-03-08', -50, 11.0, open_close='O'),
             b('ZZQ.TO', '2024-06-03', -100, 16.0), b('ZZL.TO', '2024-06-03', -100, 16.0),
             b('ZZQ.TO', '2024-04-05', 50, 12.0),
             b('ZZQ.TO', '2024-06-10', -150, 9.0), b('ZZQ.TO', '2024-06-20', 60, 9.5)]
    return rows, {}


def _short_book():
    """One account: a short opened with a broker's mark before a checkpoint and
    covered after it by a purchase coded C;O (`_msr.short` decides the cover)."""
    rows = [
        _T(action='BUYSELL', date='2024-01-02', symbol='ZZL', quantity=100, price=20.0, net_amount=2000.0),
        _T(action='BUYSELL', date='2024-01-08', symbol='ZZQ', quantity=-10, price=15.0, net_amount=150.0,
           open_close='O'),
    ]
    _chain('ZZL', rows, '2024-01-10', '2024-01-20', '2024-05-01', '2024-05-10')
    _filler(rows, 4)
    rows += [
        _T(action='BUYSELL', date='2024-03-01', symbol='ZZQ', quantity=20, price=14.0, net_amount=280.0,
           open_close='C;O'),
        _T(action='BUYSELL', date='2024-06-03', symbol='ZZL', quantity=-100, price=16.0, net_amount=1600.0),
        _T(action='BUYSELL', date='2024-06-04', symbol='ZZQ', quantity=-10, price=16.0, net_amount=160.0),
    ]
    return rows, {}


def _copy(rows):
    return [TaxTransaction(**t.to_dict()) for t in rows]


class TestMovedOptionRowPlacedOnce(unittest.TestCase):
    """TJ-2686: an option's ASSIGN row moved in front of its earlier stock leg is
    placed once, by identity. It was placed after every row carrying the leg's
    id, and a superficial-loss DISALLOW carries its sale's id, so from the
    solver's second pass on the option row was walked twice (a phantom open
    contract in the inventory, the gains off by its close); a row of a moved
    option's own id was dropped the same way."""

    @rule(R.CA_OPT_08, R.CA_DATE_14)
    def test_the_denied_delivery_legs_option_is_walked_once(self):
        rows = [
            _T(action='BUYSELL', date='2024-01-02', symbol='ZZQ', quantity=100, price=20.0, net_amount=2000.0),
            _T(action='BUYSELL', date='2024-02-01', symbol='ZZQ240315C00015000', quantity=-1, price=1.0,
               net_amount=100.0),
            _T(action='BUYSELL', date='2024-03-14', symbol='ZZQ', quantity=-100, price=15.0, net_amount=1500.0,
               description='ASSIGNED'),
            _T(action='ASSIGN', date='2024-03-15', symbol='ZZQ240315C00015000', quantity=1, price=0.0,
               net_amount=0.0),
            _T(action='BUYSELL', date='2024-03-20', symbol='ZZQ', quantity=100, price=15.0, net_amount=1500.0),
            _T(action='BUYSELL', date='2024-06-03', symbol='ZZQ', quantity=-100, price=16.0, net_amount=1600.0),
        ]
        placed = []
        real = _place_assign_options

        def spy(stream, pairs):
            out = real(stream, pairs)
            placed.append((len(stream), len(out), len(out) - len({id(t) for t in out})))
            return out
        with _engine.patch_engine('_place_assign_options', spy), \
                contextlib.redirect_stderr(io.StringIO()):
            r = CanadaTaxRules().compute_gains(_copy(rows))
        # Every pass places the moved option once: as many rows out as in, no row twice.
        self.assertTrue(placed)
        self.assertEqual(r['summary']['wash_solver_iterations'], 2)
        self.assertTrue(all(n_in == n_out and dups == 0 for n_in, n_out, dups in placed), placed)
        self.assertEqual(r['inventory'], [])                       # no phantom open contract
        # The leg: proceeds 1,500 plus the 100 premium against a cost of 2,000, denied in full;
        # the June sale: cost 1,500 plus the 400 carried, proceeds 1,600.
        self.assertEqual(len(r['wash_sales']), 1)
        self.assertAlmostEqual(r['wash_sales'][0]['amount'], 400.0)
        self.assertAlmostEqual(r['summary']['total_disallowed'], 400.0)
        self.assertAlmostEqual(r['summary']['total_gain'], -300.0)


class TestLedgerLookaheadIndexes(unittest.TestCase):
    """TJ-2689: the assignment ledger looks ahead (`take`: the later legs, the
    reserved legs, the same-day parts; `stage`: the kept days, the snapshots,
    the leg rows), and the fold's premise is that a solver's row (a DISALLOW or
    ADJUST, whatever id it carries) enters none of those indexes, and stands in
    `_id_pos` only at its sale's moment with no trade between them."""

    def test_a_solver_row_is_in_no_lookahead_index(self):
        sale = _T(action='BUYSELL', date='2024-03-14', symbol='ZZQ', quantity=-100, price=15.0,
                  net_amount=1500.0, id='s1')
        rows = [
            _T(action='BUYSELL', date='2024-01-01', symbol='ZZQ', quantity=100, price=0.0,
               net_amount=0.0, id='ob', type=RT.OPENING_TYPE),
            _T(action='BUYSELL', date='2024-02-01', symbol='ZZQ240315C00015000', quantity=-1, price=1.0,
               net_amount=100.0, id='o1'),
            _T(action='DISALLOW', date='2024-03-14', symbol='ZZQ', quantity=100, price=0.0, net_amount=400.0,
               id='s1'),
            _T(action='DISALLOW', date='2024-03-14', symbol='ZZQ', quantity=1, price=0.0, net_amount=1.0,
               id='s9'),
            sale,
            _T(action='ASSIGN', date='2024-03-15', symbol='ZZQ240315C00015000', quantity=1, price=0.0,
               net_amount=0.0, id='o1a'),
            _T(action='ADJUST', date='2024-03-20', symbol='ZZQ', quantity=0, price=0.0, net_amount=400.0,
               id='WASH_s1__b1'),
            _T(action='BUYSELL', date='2024-03-20', symbol='ZZQ', quantity=100, price=15.0, net_amount=1500.0,
               id='b1'),
        ]
        solver = {i for i, t in enumerate(rows) if t.action in ('DISALLOW', 'ADJUST')}
        led = _AssignPremiumLedger(
            rows, lambda t: t.action in ('BUYSELL', 'ASSIGN') and not OS.is_option_symbol(t.symbol),
            {'o1a': ['s1']})
        ix = led.lookahead()
        self.assertFalse({i for lst in ix.legs.values() for i, _d, _q in lst} & solver)
        self.assertFalse(set(ix.leg_id) & solver)
        self.assertFalse(set(ix.leg_px) & solver)
        self.assertTrue(all(t.action in ('BUYSELL', 'ASSIGN') for t in ix.leg_row.values()))
        self.assertEqual(sum(len(v) for v in ix.kept_days.values()), 2)      # the sale and the rebuy
        self.assertEqual(ix.snap, {('Margin', 'ZZQ'): '2024-01-01'})
        # The sale's id stands at its DISALLOW's place, with only solver rows between.
        p, q = ix.id_pos['s1'], rows.index(sale)
        self.assertLess(p, q)
        self.assertTrue(all(rows[i].action == 'DISALLOW' for i in range(p, q)))


class TestForwardDenialPass(unittest.TestCase):
    def _both(self, rows, shel=(), **kw):
        """(the fold's result, the fixed point's result, the fold's resume points):
        the same book with the fold as it is and with its cut forced to 0."""
        starts = []
        real_restore = _WalkFold.restore

        def noting(self, **k):
            real_restore(self, **k)
            starts.append(self.start)
        with contextlib.redirect_stderr(io.StringIO()):     # the notes said once a process
            CanadaTaxRules().compute_gains(_copy(rows), sheltered_transactions=_copy(shel), **kw)
        out = []
        for cut in (None, lambda self, it, ctx: 0):
            err = io.StringIO()
            with contextlib.ExitStack() as es:
                es.enter_context(contextlib.redirect_stderr(err))
                es.enter_context(mock.patch.object(_WalkFold, 'restore', noting))
                if cut is not None:
                    es.enter_context(mock.patch.object(_WalkFold, '_cut', cut))
                r = CanadaTaxRules().compute_gains(_copy(rows), sheltered_transactions=_copy(shel), **kw)
            out.append((json.dumps(r, sort_keys=True, default=str), err.getvalue()))
        return out[0], out[1], starts

    def test_the_grant_record_a_lot_points_at_survives_a_restore(self):
        rows, kw = _grant_book()
        fold, fixed, starts = self._both(rows, **kw)
        self.assertEqual(fold, fixed)
        self.assertGreater(max(starts), 0, starts)       # the fold did resume mid-book
        r = json.loads(fold[0])
        self.assertEqual(r['summary']['wash_solver_iterations'], 2)
        self.assertEqual(len(r['wash_sales']), 1)

    def test_books_of_every_state_the_checkpoint_keeps_equal_the_fixed_point(self):
        """TJ-2692: a spin-off (the stash, a record cut after its row), a journal
        (`_jdone`), an own move with a marked short (the router, `_short_routed`),
        an own move after the first checkpoint (`_msr_legs`, TJ-2696) and a
        marked short alone (the router), each resuming
        mid-book on a third pass, with and without the trace and the timeline."""
        for name, book in (('spinoff', _spinoff_book), ('spinoff2', lambda: _spinoff_book(True)),
                           ('premium', _staged_premium_book), ('journal', _journal_book),
                           ('move+short', _move_short_book), ('late move', _late_move_book),
                           ('short', _short_book)):
            rows, kw = book()
            for extra in ({}, {'trace': True, 'cost_timeline': 'settle', 'year_end': '2024-02-15'}):
                with self.subTest(book=name, **extra):
                    fold, fixed, starts = self._both(rows, **kw, **extra)
                    self.assertEqual(fold[0], fixed[0])
                    self.assertEqual(sorted(set(fold[1].splitlines())),
                                     sorted(set(fixed[1].splitlines())))
                    self.assertGreater(max(starts), 0, starts)
                    r = json.loads(fold[0])
                    self.assertGreaterEqual(r['summary']['wash_solver_iterations'],
                                            2 if name == 'premium' else 3, name)

    def test_seeded_books_equal_the_fixed_point(self):
        from _support.engine_fuzz_books import make_book
        resumed = 0
        for seed in range(40):
            txs, shel = make_book(seed)
            kw = ({'trace': True, 'cost_timeline': 'settle', 'year_end': '2025-06-30'}
                  if seed % 3 == 0 else {})
            with self.subTest(seed=seed):
                fold, fixed, starts = self._both(txs, shel, **kw)
                self.assertEqual(fold[0], fixed[0])
                # The same messages, in order; a message a prefix row said is
                # not said again, where the whole-book walk said it once per pass.
                self.assertEqual(sorted(set(fold[1].splitlines())),
                                 sorted(set(fixed[1].splitlines())))
                resumed += max(starts) > 0
        self.assertGreater(resumed, 5)


if __name__ == '__main__':
    unittest.main()
