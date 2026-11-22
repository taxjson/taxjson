"""A denied loss's addition never lands on a marked short's pool (TJ-2698).

The superficial-loss solver adds a denied loss to the replacement's ACB
(s.53(1)(f); CA-SL-09): a virtual ADJUST placed right after the trigger
purchase and keyed, when the trigger's own pool no longer holds the
substituted property at day 30, to the pool that does. Since TJ-2761 a
trigger whose position is sold out before day 30 is no replacement, so in
these books the 02-20 NEW.TO purchase is the replacement itself and the
addition lands after it; the flat-pool wait (CA-SL-19) remains for a
position no purchase opened (a rename, an opening balance), and the short
must never take it then either. A short the broker
marks (CA-DISP-04) has a pool of its own beside the symbol's, and an ADJUST
on a symbol whose only open position is that short is the short seller's
compensation payment (CA-ACB-14): the solver's addition took the same route
and lowered the short's proceeds, so the loss came back on the cover, in its
year, instead of on the replacement's sale. A short never replaces
(CA-SL-07): the addition is the long side's whatever shorts are open.

The books: account b buys 100 OLD.TO for 2000 (2025-01-02), sells them for
1000 (03-03, a loss of 1000), rebuys 100 for 1000 (03-08) and sells them
again (03-13); buys 100 NEW.TO for 1000 (03-26); a SPLIT renames OLD.TO into
NEW.TO (04-01); the 100 NEW.TO are sold for 1200 (06-02). The 03-26 purchase
is inside the split window of the rename (a broker booking the new ticker
early: the same security as OLD.TO, CA-ACB-RENAME), so it is the identical
property held at day 30 (04-02) and the 03-03 loss is denied in full, its
addition routed to NEW.TO's pool. A target's own shares bought before that
window are another security (the owner's ruling of 2026-11-11 on backlog
TJ-2699: tests/test_rename_target_identity.py), which is why these books
date the purchase there; they were dated 02-03 to 02-20 before the ruling
and gave the same figures.

From the branch's first review: an ADJUST that is no denial goes to the
short only from the account that holds it (TJ-2710, TJ-2713); a DISALLOW
is walked before its sale, so it meets a flat main pool only for a cover's
loss, whose row is the short's (TJ-2716); and the one deferral a marked
short's pool does carry, a long put's riding its exercise into the short
its leg opens (TJ-0061), which `_walk_split`'s short-pool merge carries
into a rename's target (TJ-2714). Synthetic books, made-up tickers.
"""
import contextlib
import io
import unittest

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R
from _support.ca_marked_short_books import NEW, OLD, _tr, _rename, _chain, _short, _records
from _support.engine_ca_pool_books import pool_of

PUT = 'ZZQ250620P00010000'


def _adjust(date, sym, net, acct):
    return TaxTransaction(action='ADJUST', date=date, date_settle=date,
                          time='10:00:00', symbol=sym, quantity=0.0,
                          net_amount=net, currency='CAD', account=acct)


def _opt(date, sym, qty, net, acct='m', action='BUYSELL'):
    return TaxTransaction(action=action, date=date, date_settle=date,
                          time='10:00:00', symbol=sym, quantity=qty,
                          price=abs(net / qty), net_amount=net,
                          currency='CAD', account=acct)


def _run(book, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = CanadaTaxRules().compute_gains(book, **kw)
    return res, err.getvalue()


def _inventory(res):
    """(symbol, qty, total_cost, deferred_wash) of every inventory row."""
    return [(e['symbol'], round(e['qty'], 2),
             round(float(e['total_cost']), 2),
             round(float(e.get('deferred_wash') or 0.0), 2))
            for e in res['inventory']]


# The long side's figures when the addition reaches the replacement: the
# 03-03 loss denied in full, the NEW.TO shares sold at a cost of 2000
# (1000 paid + the 1000 denied).
_LONG = [('2025-03-03', OLD, 'b', 0.0, 1000.0),
         ('2025-03-13', OLD, 'b', 0.0, 0.0),
         ('2025-06-02', NEW, 'b', -800.0, 0.0)]


def _cover(acct):
    """The short's own gain: opened and covered at 1000, nothing."""
    return ('2026-01-10', NEW, acct, 0.0, 0.0)


class TestDenialBesideAMarkedShort(unittest.TestCase):

    def _check(self, book, expected):
        res, err = _run(book)
        self.assertTrue(res['summary']['wash_solver_converged'], err)
        self.assertEqual(_records(res), expected, err)
        self.assertEqual([round(w['disallowed_amount'], 2)
                          for w in res['wash_sales']], [1000.0], err)
        return res

    @rule(R.CA_SL_09, R.CA_SL_19, R.CA_DISP_04)
    def test_a_short_in_another_account_takes_none_of_the_denial(self):
        """The 03-08 rebuy is sold out before day 30: the 03-26 NEW.TO
        purchase is the replacement and the addition lands after it, the
        other account's short in nobody's way (before TJ-2761 the
        addition waited on NEW.TO's flat pool from the rebuy, which the
        short routing once took). The short's proceeds are its own (it
        realized +200 on 06-02 and -1000 on the cover)."""
        self._check(_chain() + _short(), _LONG + [_cover('m')])

    @rule(R.CA_SL_07, R.CA_DISP_04)
    def test_the_shorts_pool_carries_no_deferred_loss(self):
        """With the short still open at the end, its inventory row holds
        the short sale's proceeds and no deferred loss (it held 1000)."""
        res, err = _run(_chain() + _short(cover=False))
        rows = [e for e in res['inventory'] if e['symbol'] == NEW]
        self.assertEqual(
            [(round(e['qty'], 2), round(float(e['total_cost']), 2),
              round(float(e.get('deferred_wash') or 0.0), 2))
             for e in rows], [(-100.0, -1000.0, 0.0)], err)
        self.assertEqual(_records(res), _LONG, err)

    @rule(R.CA_SL_09, R.CA_DISP_04)
    def test_a_short_in_the_same_account_takes_none_of_it(self):
        """Account b holds the short itself; its 03-26 purchase is coded
        O, so it opens a long beside the short rather than covering it
        (CA-DISP-04). The same figures as across accounts (it realized
        +200 and -1000 here too)."""
        self._check(_chain(new_oc='O') + _short(acct='b'),
                    _LONG + [_cover('b')])

    @rule(R.CA_SL_19)
    def test_a_short_opened_after_the_bump_was_never_in_its_way(self):
        """The short opens on 03-15, between the sold-out rebuy and the
        03-26 purchase that takes the addition. Unchanged by the fix."""
        self._check(_chain() + _short(open_date='2025-03-15'),
                    _LONG + [_cover('m')])

    @rule(R.CA_SL_09, R.CA_ACB_RENAME)
    def test_a_replacement_pool_open_at_the_bump_takes_it(self):
        """100 NEW.TO bought on 01-20 too, before the loss: the pool holds
        shares when the addition is placed, and the short beside it never
        mattered. Those shares are another security for the denial
        (bought before the rename's split window: TJ-2699), so the 03-26
        purchase is the whole replacement; the addition joins the pool
        of 200 at 3000 all the same (one pool per symbol), and the 06-02
        sale of 100 realizes -300 (1200 against 1500); the 100 left are
        at 1500 and carry half of the denied amount. Before the ruling
        this book had no 03-26 purchase and the 01-20 shares were the
        replacement (-800 on 06-02)."""
        res = self._check(_chain() + [_tr('2025-01-20', NEW, 100, 1000)]
                          + _short(),
                          _LONG[:2] + [('2025-06-02', NEW, 'b', -300.0, 0.0),
                                       _cover('m')])
        self.assertIn((NEW, 100.0, 1500.0, 500.0), _inventory(res))

    @rule(R.CA_SL_09, R.CA_SL_10, R.CA_ACB_RENAME)
    def test_a_replacement_bought_before_the_loss_takes_the_addition_at_the_loss(self):
        """A trigger bought before the loss (the pin the 01-20 purchase
        carried before TJ-2699 made it another security; TJ-3294): 100
        NEW.TO bought 03-26, inside the rename's split window, then the
        OLD.TO loss on 03-28. The shares are held at day 30 and the
        addition is placed at the loss sale, the later of the two
        (CA-SL-09): the loss denied, -800 on 06-02, the short's cover 0."""
        book = [_tr('2025-01-02', OLD, 100, 2000),
                _tr('2025-03-26', NEW, 100, 1000),
                _tr('2025-03-28', OLD, -100, 1000),
                _rename('2025-04-01', OLD, NEW),
                _tr('2025-06-02', NEW, -100, 1200)] + _short()
        res = self._check(book, [('2025-03-28', OLD, 'b', 0.0, 1000.0),
                                 ('2025-06-02', NEW, 'b', -800.0, 0.0),
                                 _cover('m')])
        self.assertEqual([a['after'] for w in res['wash_sales']
                          for a in w['adjusts']], [book[2].id])

    @rule(R.CA_ACB_14)
    def test_a_compensation_adjust_while_only_the_short_is_open_stays_its(self):
        """An ADJUST that is no denial (a dividend compensation debited
        while short) on a symbol whose only open position is the marked
        short is the short's: it lowers the cover's gain by its amount."""
        res, err = _run([_tr('2025-01-10', NEW, -100, 1000, acct='m', oc='O'),
                         _adjust('2025-02-01', NEW, 50, 'm'),
                         _tr('2025-03-01', NEW, 100, 1000, acct='m', oc='C')])
        self.assertEqual(_records(res),
                         [('2025-03-01', NEW, 'm', -50.0, 0.0)], err)
        self.assertIn('lands on a SHORT position', err)
        self.assertEqual(res['wash_sales'], [])

    @rule(R.CA_ACB_14)
    def test_an_adjust_in_an_account_that_holds_no_short_waits_on_the_symbols_pool(self):
        """TJ-2710: the book plus an ADJUST of 1000 on NEW.TO in account
        b on 03-08 (a hand entry of the solver's addition), the solver
        off. Account b holds no short, so the ADJUST is its own cost
        adjustment: it stays on the symbol's pool, flat that day (the
        run warns, as for any ADJUST on a flat pool), and joins the
        03-26 purchase: -800 on 06-02, the cover 0. It was booked as
        account m's compensation payment (+200 and -1000)."""
        res, err = _run(_chain() + _short()
                        + [_adjust('2025-03-08', NEW, 1000, 'b')],
                        detect_wash_sales=False)
        self.assertEqual(_records(res),
                         [('2025-03-03', OLD, 'b', -1000.0, 0.0),
                          ('2025-03-13', OLD, 'b', 0.0, 0.0),
                          ('2025-06-02', NEW, 'b', -800.0, 0.0),
                          _cover('m')], err)
        self.assertIn('hits an EMPTY pool', err)
        self.assertNotIn('lands on a SHORT position', err)
        self.assertEqual(res['wash_sales'], [])

    @rule(R.CA_ACB_14, R.CA_SL_09)
    def test_the_same_with_the_solver_on_adds_the_denial_too(self):
        """The same book, the solver on: the hand ADJUST and the denied
        1000 both wait for the 03-26 shares (a cost of 3000): -1800 on
        06-02, the cover 0."""
        self._check(_chain() + _short()
                    + [_adjust('2025-03-08', NEW, 1000, 'b')],
                    [('2025-03-03', OLD, 'b', 0.0, 1000.0),
                     ('2025-03-13', OLD, 'b', 0.0, 0.0),
                     ('2025-06-02', NEW, 'b', -1800.0, 0.0),
                     _cover('m')])

    @rule(R.CA_SL_07, R.CA_DISP_04)
    def test_a_covers_denied_loss_lists_the_shorts_pool_in_its_window(self):
        """A DISALLOW is walked before its sale, so the main pool is flat
        at one only when the loss is a marked short's cover's: account m
        covers at 1200 the short it opened at 1000 (a loss of 200), and
        account b's purchase on 02-08, held at day 30, denies it (the
        addition raises those shares to 1200: 0 on 06-02). The window
        table's DISALLOW line shows the short's pool, -100 units at
        1000, the pool the loss is of (TJ-2716)."""
        res, err = _run([_tr('2025-01-10', NEW, -100, 1000, acct='m', oc='O'),
                         _tr('2025-02-03', NEW, 100, 1200, acct='m', oc='C'),
                         _tr('2025-02-08', NEW, 100, 1000),
                         _tr('2025-06-02', NEW, -100, 1200)], trace=True)
        self.assertEqual(_records(res),
                         [('2025-02-03', NEW, 'm', 0.0, 200.0),
                          ('2025-06-02', NEW, 'b', 0.0, 0.0)], err)
        trace = res['wash_sales'][0]['trace']
        lines = [ln for ln in trace if '| DISALLOW |' in ln]
        self.assertEqual(len(lines), 1, trace)
        cells = [c.strip() for c in lines[0].lstrip('# ').split('|')]
        self.assertEqual(cells[4], 'DISALLOW', cells)
        self.assertEqual(cells[11:14], ['-100.0000', '1000.00', '10.0000'],
                         cells)


class TestAShortPoolsOwnDeferral(unittest.TestCase):
    """The one deferral a marked short's pool carries: a long put's,
    riding the put's exercise into the short its leg opens (TJ-0061).
    `_walk_split`'s short-pool merge carries it into a rename's target
    (kept for it: TJ-2714)."""

    def _book(self):
        """Account m: a put bought for 200, sold for 100 (a loss of 100),
        rebought within the window and held at day 30 (the loss denied,
        the put's ACB 200); exercised on 03-03 with no shares held, so
        its leg sells 100 ZZQ short at 10, coded O."""
        return [_opt('2025-01-05', PUT, 1, 200),
                _opt('2025-01-20', PUT, -1, 100),
                _opt('2025-01-25', PUT, 1, 100),
                _opt('2025-03-03', PUT, -1, 0.0, action='ASSIGN'),
                _tr('2025-03-03', 'ZZQ', -100, 1000, acct='m', oc='O')]

    @rule(R.CA_SL_09, R.CA_DISP_04)
    def test_a_puts_denied_loss_rides_its_exercise_into_the_short(self):
        """The short's proceeds are lowered by the put's cost (1000 less
        200) and its pool carries the put's deferred 100."""
        res, err = _run(self._book())
        self.assertEqual(_records(res),
                         [('2025-01-20', PUT, 'm', 0.0, 100.0)], err)
        self.assertEqual(_inventory(res), [('ZZQ', -100.0, -800.0, 100.0)],
                         err)

    @rule(R.CA_DISP_04)
    def test_a_rename_merges_it_into_the_targets_short(self):
        """Account m is short 100 ZZR at 1000 already when ZZQ is renamed
        into ZZR: the merged short holds both and the 100 deferred."""
        res, err = _run(self._book()
                        + [_tr('2025-02-01', 'ZZR', -100, 1000, acct='m', oc='O'),
                           _rename('2025-04-01', 'ZZQ', 'ZZR')])
        self.assertEqual(_inventory(res), [('ZZR', -200.0, -1800.0, 100.0)],
                         err)


def _opening(date, sym, qty, acct='b'):
    """An opening balance (missing history): units at an unknown cost."""
    return TaxTransaction(action='OPENING_BALANCE', date=date, date_settle=date,
                          time='09:00:00', symbol=sym, quantity=qty, price=0.0,
                          net_amount=0.0, currency='CAD', account=acct)


def _wait_chain():
    """TJ-2768's book: account b buys 100 OLD.TO for 2000 (2024-11-01),
    sells them for 1000 (12-03, a loss of 1000), rebuys 100 for 1000
    (12-08) and sells them again (12-13); an opening balance of 100 on
    12-20 is the position held at day 30, and no purchase opened it, so
    the denied 1000 waits on the flat pool from the sell-out (CA-SL-19)
    and joins the opening balance; the 100 are sold for 1200 (2025-06-02:
    +200)."""
    return [_tr('2024-11-01', OLD, 100, 2000),
            _tr('2024-12-03', OLD, -100, 1000),
            _tr('2024-12-08', OLD, 100, 1000),
            _tr('2024-12-13', OLD, -100, 1000),
            _opening('2024-12-20', OLD, 100),
            _tr('2025-06-02', OLD, -100, 1200)]


def _short_of(sym, cover='2025-07-10', acct='m'):
    """A marked short of `sym` in account m from 2024-11-10, covered on
    `cover` (None: still open)."""
    rows = [_tr('2024-11-10', sym, -100, 1000, acct=acct, oc='O')]
    if cover:
        rows.append(_tr(cover, sym, 100, 1000, acct=acct, oc='C'))
    return rows


_WAIT_LONG = [('2024-12-03', OLD, 'b', 0.0, 1000.0),
              ('2024-12-13', OLD, 'b', 0.0, 0.0),
              ('2025-06-02', OLD, 'b', 200.0, 0.0)]


def _ye_rows(res):
    """(account, qty, total_cost, deferred_wash, short_pool) of every
    `inventory_year_end` row."""
    return [(e.get('account', ''), round(e['qty'], 2),
             round(float(e['total_cost']), 2),
             round(float(e.get('deferred_wash') or 0.0), 2),
             bool(e.get('short_pool')))
            for e in res['inventory_year_end']]


def _tl_rows(doc, sym):
    """(date, account, qty, total_cost, deferred_wash) of a timeline's
    rows of `sym`, in the engine's order."""
    from taxjson.lib import cost_timeline as CT
    return [(r['date'], r.get('account') or '', round(r['qty'], 2),
             round(float(r['total_cost']), 2),
             round(float(r.get('deferred_wash') or 0.0), 2))
            for r in CT.rows(doc) if r['symbol'] == sym]


class TestTheWaitingRowBesideAMarkedShort(unittest.TestCase):
    """TJ-2712 (the owner's ruling of 2026-11-11): while a marked short of
    the symbol is open and a denied loss's addition waits on the symbol's
    flat pool (CA-SL-19), the symbol's row keeps its waiting state, 0
    units and the deferred amount, and the short shows in its own section:
    the timeline's `shorts` part, and an account row (`short_pool`) in the
    year-end inventory and the dated list, as a short beside a long. The
    timeline's state for a flat symbol used to be the short pool's and the
    year-end snapshot put the short under the symbol, so between the
    sell-out and the opening the 1000 showed nowhere. A waiting amount is
    no position: `tjs list --date` and the year-end inventory list no row
    for it, as without the short."""

    def _run(self, book, **kw):
        res, err = _run(book, year_end='2024-12-15', cost_timeline='settle',
                        **kw)
        self.assertTrue(res['summary']['wash_solver_converged'], err)
        return res, err

    @rule(R.CA_SL_19, R.CA_DISP_04)
    def test_the_timeline_keeps_the_symbols_waiting_row(self):
        """The 12-13 sell-out drains the pool (the short stands in for it,
        -100 at 1000, as for any flat symbol), then the addition's row is
        the symbol's own: 0 units, cost 0, 1000 deferred, until the
        opening balance takes it on 12-20. The short has its own rows in
        `shorts`. Read on 12-15 as `tjs list --date` reads them, the
        pools hold the wait, the held list is empty and the short is
        account m's row. The figures are the no-short book's plus the
        cover (0)."""
        from taxjson.lib import cost_timeline as CT
        res, err = self._run(_wait_chain() + _short_of(OLD))
        self.assertEqual(_records(res),
                         _WAIT_LONG + [('2025-07-10', OLD, 'm', 0.0, 0.0)], err)
        tl = res['cost_timeline']
        rows = _tl_rows(tl, OLD)
        self.assertEqual(
            [r for r in rows if '2024-12-13' <= r[0] <= '2024-12-20'],
            [('2024-12-13', '', -100.0, -1000.0, 0.0),
             ('2024-12-13', '', 0.0, 0.0, 1000.0),
             ('2024-12-20', '', 100.0, 1000.0, 1000.0)], rows)
        # (the pool drained with nothing waiting: the short's row again)
        self.assertEqual(rows[-2:], [('2025-06-02', '', -100.0, -1000.0, 0.0),
                                     ('2025-07-10', '', 0.0, 0.0, 0.0)], rows)
        self.assertEqual(_tl_rows(tl['shorts'], OLD),
                         [('2024-11-10', 'm', -100.0, -1000.0, 0.0),
                          ('2025-07-10', 'm', 0.0, 0.0, 0.0)])
        (pooled,) = CT.pools_on(tl, '2024-12-15').values()
        self.assertEqual((pooled['qty'], pooled['total_cost'],
                          pooled['deferred_wash']), (0.0, 0.0, 1000.0))
        self.assertEqual(CT.inventory_on(tl, '2024-12-15'), [])
        self.assertEqual([(r['account'], r['qty'], r['total_cost'],
                           r['short_pool'])
                          for r in CT.short_rows_on(tl, '2024-12-15')],
                         [('m', -100.0, -1000.0, True)])

    @rule(R.CA_SL_19, R.CA_DISP_04)
    def test_the_year_end_inventory_lists_the_short_on_its_own_row(self):
        """At a year end inside the wait the short is account m's row
        (`short_pool`), not the symbol's; the symbol's own row, 0 units,
        is no position, so there is none, as without the short."""
        res, err = self._run(_wait_chain() + _short_of(OLD))
        self.assertEqual(_ye_rows(res),
                         [('m', -100.0, -1000.0, 0.0, True)], err)
        res, err = self._run(_wait_chain())
        self.assertEqual(_records(res), _WAIT_LONG, err)
        self.assertEqual(_ye_rows(res), [], err)

    @rule(R.CA_DISP_04, R.CA_SL_19)
    def test_after_the_wait_the_long_and_the_short_have_both_rows(self):
        """Once the opening balance takes the addition, the symbol's row
        is the long (100, cost 1000, 1000 deferred) and the short is
        beside it: the shape a short beside a long always had."""
        res, err = _run(_wait_chain() + _short_of(OLD), year_end='2024-12-25',
                        cost_timeline='settle')
        self.assertEqual(_ye_rows(res),
                         [('', 100.0, 1000.0, 1000.0, False),
                          ('m', -100.0, -1000.0, 0.0, True)], err)

    @rule(R.CA_SL_19, R.CA_DISP_04)
    def test_a_short_covered_inside_the_wait(self):
        """The short covered on 12-16, inside the wait: the symbol's row
        stays the wait (its state unchanged, no row for the cover), the
        short's own rows end, and on 12-18 nothing is held."""
        from taxjson.lib import cost_timeline as CT
        res, err = _run(_wait_chain() + _short_of(OLD, cover='2024-12-16'),
                        year_end='2024-12-18', cost_timeline='settle')
        self.assertEqual(_records(res),
                         sorted(_WAIT_LONG
                                + [('2024-12-16', OLD, 'm', 0.0, 0.0)]), err)
        self.assertEqual(_ye_rows(res), [], err)
        tl = res['cost_timeline']
        self.assertEqual([r for r in _tl_rows(tl, OLD) if r[0] == '2024-12-16'],
                         [])
        (pooled,) = CT.pools_on(tl, '2024-12-18').values()
        self.assertEqual((pooled['qty'], pooled['deferred_wash']),
                         (0.0, 1000.0))
        self.assertEqual(CT.short_rows_on(tl, '2024-12-18'), [])

    @rule(R.CA_SL_19, R.CA_DISP_04)
    def test_a_sub_cent_residue_of_the_deferred_amount_is_no_wait(self):
        """TJ-3285: the rebuy of 100 sold as 99.9999999 shares on
        2025-03-01 (a dust close) leaves about 1e-6 of the denied 1000
        in the tally, m's short open throughout. Nothing waits: from
        03-01 the symbol's row is the short's and the year end lists the
        short as the symbol's row, as with nothing denied. A wait is a
        material deferred amount (half a cent) or a parked one."""
        from taxjson.engine.ca.pools import _pool_waits
        chain = [_tr('2024-11-01', OLD, 100, 2000),
                 _tr('2024-12-03', OLD, -100, 1000),
                 _tr('2024-12-08', OLD, 100, 1000),
                 _tr('2025-03-01', OLD, -99.9999999, 1300)]
        res, err = _run(chain + _short_of(OLD), year_end='2025-03-15',
                        cost_timeline='settle')
        self.assertEqual(_records(res),
                         [('2024-12-03', OLD, 'b', 0.0, 1000.0),
                          ('2025-03-01', OLD, 'b', -700.0, 0.0),
                          ('2025-07-10', OLD, 'm', 0.0, 0.0)], err)
        self.assertEqual(_ye_rows(res), [('', -100.0, -1000.0, 0.0, False)],
                         err)
        self.assertEqual([r for r in _tl_rows(res['cost_timeline'], OLD)
                          if r[0] >= '2025-03-01'],
                         [('2025-03-01', '', -100.0, -1000.0, 0.0),
                          ('2025-07-10', '', 0.0, 0.0, 0.0)])
        self.assertFalse(_pool_waits(pool_of(denied='0.000001')))
        self.assertTrue(_pool_waits(pool_of(denied='0.005')))
        self.assertTrue(_pool_waits(pool_of(parked='0.001')))

    @rule(R.CA_FX_04, R.CA_ACB_01)
    def test_a_futures_short_with_nothing_waiting_is_unchanged(self):
        """A short future alone is the symbol's row, in the year-end
        inventory and the timeline (TJ-0650); beside a long contract it
        is account b's row (TJ-1103). Neither waits, so the fix leaves
        them as they were."""
        from _support.ca_engine_runs import _fut, _settled
        from taxjson.lib import cost_timeline as CT
        res, err = _run(_settled([_fut('short', '2025-04-15', -1, 100,
                                       account='b')]),
                        year_end='2025-12-31', cost_timeline='settle')
        self.assertEqual(_ye_rows(res), [('', -1.0, 0.0, 0.0, False)], err)
        self.assertEqual(CT.short_rows_on(res['cost_timeline'], '2025-12-31'),
                         [])
        res, err = _run(_settled([_fut('old', '2025-01-06', 1, 100),
                                  _fut('loss', '2025-03-03', -1, 50),
                                  _fut('rep', '2025-03-05', 1, 100),
                                  _fut('short', '2025-04-15', -1, 100,
                                       account='b')]),
                        year_end='2025-12-31', cost_timeline='settle')
        self.assertEqual(_ye_rows(res), [('', 1.0, 50.0, 50.0, False),
                                         ('b', -1.0, 0.0, 0.0, True)], err)
        self.assertEqual([(r['account'], r['qty']) for r in
                          CT.short_rows_on(res['cost_timeline'], '2025-12-31')],
                         [('b', -1.0)])


if __name__ == '__main__':
    unittest.main()
