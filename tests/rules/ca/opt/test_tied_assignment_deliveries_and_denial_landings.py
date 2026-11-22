"""Four Canadian-engine figure bugs: TJ-2711, TJ-2719, TJ-2720 and TJ-2750.

TJ-2711: a denied loss's addition went to the pool right after the trigger
purchase (CA-SL-10), even when that position was sold again before day 30
and the shares held at day 30 were bought later: the addition was sold with
the rebuy, the rebuy's sale listed as a second superficial loss, and so on
down the window, each denial keeping the total and moving the year. Under
s.54 the substituted property is identical property acquired in the window
and owned at its end; s.53(1)(f) adds the loss to its ACB. The rule
(`_sl_sold_out`, `_sl_sale_alloc`, `_sl_landing_row`; CA-SL-09, CA-SL-10,
CA-SL-19; TJ-2753, TJ-2754, TJ-2760 to TJ-2762): a trigger whose position
is sold out before day 30 is not a replacement; the sale's denied units go
to the replacements held at day 30, in CA-SL-10's order, each up to what
it opened, as if the round trip were not in the book. Only when no
purchase opened the held position (the shares come by a rename or merger,
an opening balance) does the addition wait from the sell-out on the flat
pool for them. A position held through day 30 keeps the placement at the
trigger (A2-0555), whatever is sold of it before day 30 (TJ-2755, decided:
the pooled reading, CA-SL-09).

TJ-2719: one share delivery of two put assignments at one moment took the
premiums in the option rows' staging order: the first owner took the whole
leg and the second, with no later leg of its own, was drained into no shares,
so a put's premium lowered the cost of the other put's shares and the sale
between the two deliveries moved. Each assignment's premium lowers the cost
of the shares its own assignment delivered (s.49(3.1); CA-OPT-06/08):
`_pair_assign_legs` records each pair's shares and the ledger's `take` caps
each owner at its claim.

TJ-2750: which of two same-strike puts assigned at one moment a shared
delivery belonged to still followed the option rows' order (CA-OPT-08 tells
same-moment assignments apart by strike and has no tie-break for equal
strikes). The owner's ruling (2026-11-11): a delivery the strike cannot tell
apart is theirs pro rata by contracts, each up to what it still needs, in
whole shares (`_pro_rata`: the shares rounding down leaves go to the largest
fraction dropped, then the larger assignment, then the earlier series), so
the figures are the same in any row order. A delivery of exactly one
assignment's quantity is still that assignment's, unless another tied
assignment delivers that quantity too.

The review of that fix (TJ-3173 to TJ-3182): the tie-break is Canada's
alone (`tie_pro_rata`, the Canadian engine's keyword; the US engine pairs
as before, tests/rules/ca/opt/test_assignment_premium_to_own_stock_leg.py);
only equal strikes tie; the tie reads the day, never a row's place among
the day's rows, so the option rows may sit anywhere around the deliveries;
a leg another assignment already took part of keeps TJ-0838's guard (whole,
while what is left covers each); `_pro_rata` sorts its members itself and
splits a fractional total or cap exactly; the earlier expiry, not the
symbol's text, takes the remainder; the stats and rename views pair through
the engine's own allocator (`_allocate_assign_legs`) and agree with it.

The second review (TJ-3218 to TJ-3222): a tied option's candidates rank by
the leg's own place, so a tie's deliveries are settled in their order (the
remainder rule on the earliest first) whatever the option rows' placement;
only assignments of one kind are peers (an exercise beside an assignment
pairs as before); the twins order rows as the engine does (`event_sort_key`)
and fold a leg's own assignments before an unpaired credit; several
deliveries of exactly one peer's quantity go to it earliest first; a plain
purchase listed before its option row is outside the class (TJ-3223).

The focused review (TJ-3229 to TJ-3234): a tie group competes with any
other assignment (an exercise, another strike) for a delivery at its
nearest member's row, as before; only among its members are the legs
settled in their own order (the nearest day, a later day before an earlier
one, then the stream's order: `_allocate_assign_legs`' second pass). The
twins count peers among the books' assignment rows (a peer with no credit
is a phantom member), keep a row's own time, and find a row the Canadian
order copied (a split in its settlement lag) by its source.

TJ-2720: a sale split into fills had each fill's denied units backed by the
day-30 holders in listing order, so the fill priced lower took the first
holder whole: the split between a taxable replacement (deferred) and a
registered one (lost for good) depended on which fill was listed first. The
sale's denied units are allocated over its triggers once, in acquisition
order against the sale's last fill (CA-SL-10), each holder up to its units
held at day 30, and each fill takes its units' share of every trigger's
take (`_sl_sale_alloc`; CA-SL-08/09; TJ-2756, TJ-2758: a purchase between
two fills is a purchase before the sale).

Synthetic books, made-up tickers and accounts. Each book runs with the
solver on and off where it can: the premium fold (TJ-2719) does not depend
on the solver, and a denial moves a loss between years without changing
the total (TJ-2711), or lowers it by exactly the part lost for good
(TJ-2720).

Formerly: test_ca_figure_bugs
"""
import contextlib
import dataclasses
import io
import itertools
import unittest

from taxjson.lib import trade_stats as TS
from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.engine.assign import _pair_assign_legs, _pro_rata, _series_rank
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.option_symbols import is_option_symbol
from taxjson.lib.renames import _assign_costs
from tax_rules import rule
from taxjson.rules import ids as R
from _support.engine_rules import assert_rule, rules_of

# Under aliases: its `_chain` and `_records` are not this module's.
from _support.ca_marked_short_books import (
    _chain as _msd_chain, _records as _msd_records, _short as _msd_short,
)

OLD, NEW, SYQ = 'OLD.TO', 'NEW.TO', 'SYQ.TO'
RRSP = '99900001'


def _tr(date, sym, qty, net, acct='b', time='10:00:00', action='BUYSELL'):
    return TaxTransaction(action=action, date=date, date_settle=date,
                          time=time, symbol=sym, quantity=float(qty),
                          price=abs(net / qty) if qty else 0.0,
                          net_amount=float(net), currency='CAD',
                          account=acct)


def _rename(date, old, new):
    return TaxTransaction(action='SPLIT', date=date, date_settle=date,
                          time='09:00:00', symbol=old, symbol_new=new,
                          quantity=1.0, net_amount=0.0, currency='CAD',
                          account='b')


def _run(book, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = CanadaTaxRules().compute_gains(book, **kw)
    return res, err.getvalue()


def _records(res):
    """(date, symbol, gain, denied, cost) of every realized row."""
    return sorted((e['date'], e['symbol'], round(e['gain'], 2),
                   round(e.get('disallowed_amount') or 0.0, 2),
                   round(e.get('cost') or 0.0, 2))
                  for e in res['transactions'] if 'gain' in e)


def _inventory(res):
    """(symbol, qty, total_cost, deferred_wash) of every inventory row."""
    return [(e['symbol'], round(e['qty'], 2),
             round(float(e['total_cost']), 2),
             round(float(e.get('deferred_wash') or 0.0), 2))
            for e in res['inventory']]


def _total(res):
    return round(sum(e['gain'] for e in res['transactions']
                     if 'gain' in e), 2)


def _adjust_rules(res):
    """For each denial, the rule each of its listed additions names (the
    engine's `rules`, PLAN step 11), in the order of `_adjust_after`."""
    return [[rules_of(res, w['loss_tx_id']).get(a['id']) for a in w['adjusts']]
            for w in res['wash_sales']]


def _adjust_after(res, book):
    """For each denial, the date of the row its taxable addition is placed
    after (`after`: the row's id), in the order the denials are listed."""
    dates = {t.id: t.date for t in book}
    return [[dates.get(a['after']) for a in w['adjusts']]
            for w in res['wash_sales']]


# ---------------------------------------------------------------- TJ-2711

def _chain(sym_late, rename=False):
    """100 OLD.TO bought for 2000 (11-01), sold for 1000 (12-03: a loss of
    1000), rebought for 1000 (12-08) and sold for 1000 (12-13); 100
    `sym_late` bought for 1000 (12-15) and sold for 1500 (12-18); 100
    bought for 1000 (12-20) and held past day 30 (01-02), sold for 1200
    on 2025-06-02. With `rename`, OLD.TO is renamed into the late symbol
    on 12-22, inside the split window of both late purchases (the
    reviewer's book had it on 2025-04-01: under TJ-2699 the late
    purchases are then the target's own shares, another security, and
    the loss is allowed); otherwise every row is OLD.TO."""
    rows = [_tr('2024-11-01', OLD, 100, 2000),
            _tr('2024-12-03', OLD, -100, 1000),
            _tr('2024-12-08', OLD, 100, 1000),
            _tr('2024-12-13', OLD, -100, 1000),
            _tr('2024-12-15', sym_late, 100, 1000),
            _tr('2024-12-18', sym_late, -100, 1500),
            _tr('2024-12-20', sym_late, 100, 1000)]
    if rename:
        rows.append(_rename('2024-12-22', OLD, NEW))
    rows.append(_tr('2025-06-02', sym_late, -100, 1200))
    return rows


class TestTheAdditionGoesToTheSharesHeldAtDayThirty(unittest.TestCase):
    """TJ-2711: one denied loss, the 1000 of 12-03, added to the 12-20
    shares' ACB (2000): the 12-13 sale 0, the 12-18 sale +500 in 2024, the
    06-02 sale -800 in 2025. Before the fix the addition joined the 12-08
    rebuy (or, in the rename book, the 12-15 opening) and was denied again
    down the window: 2024 showed 0 and 2025 -300, with two or three
    superficial-loss rows for one denied loss."""

    def _check(self, book, late):
        on, err = _run(book)
        self.assertTrue(on['summary']['wash_solver_converged'], err)
        self.assertEqual(_records(on), [
            ('2024-12-03', OLD, 0.0, 1000.0, 2000.0),
            ('2024-12-13', OLD, 0.0, 0.0, 1000.0),
            ('2024-12-18', late, 500.0, 0.0, 1000.0),
            ('2025-06-02', late, -800.0, 0.0, 2000.0)], err)
        # One denial; the 12-08 and 12-15 purchases are sold out before
        # day 30, so the 12-20 purchase is the replacement.
        self.assertEqual(_adjust_after(on, book), [['2024-12-20']], err)
        # The 12-20 purchase is the replacement: the addition (CA-SL-09).
        self.assertEqual(_adjust_rules(on), [[R.CA_SL_09]])
        self.assertEqual(on['summary']['total_disallowed'], 1000.0)
        # The solver off: the raw loss in 2024 and the paid cost on 06-02;
        # the denial moves 1000 between the years and keeps the total.
        off, _ = _run(book, detect_wash_sales=False)
        self.assertEqual(_records(off), [
            ('2024-12-03', OLD, -1000.0, 0.0, 2000.0),
            ('2024-12-13', OLD, 0.0, 0.0, 1000.0),
            ('2024-12-18', late, 500.0, 0.0, 1000.0),
            ('2025-06-02', late, 200.0, 0.0, 1000.0)])
        self.assertEqual(_total(on), _total(off))
        self.assertEqual(_total(on), -300.0)

    @rule(R.CA_SL_09)
    def test_the_plain_book_every_row_the_loss_symbol(self):
        self._check(_chain(OLD), OLD)

    @rule(R.CA_SL_09, R.CA_SL_19, R.CA_ACB_RENAME)
    def test_the_rename_book(self):
        """The reviewer's book: the shares held at day 30 are NEW.TO's, a
        pool flat when the loss was denied; the rename (12-22) comes
        within a week of the NEW.TO purchases, which are the same
        security as OLD.TO's (a broker booking the new ticker early,
        TJ-2699). NEW.TO's 12-20 purchase is the replacement (the 12-08
        and 12-15 ones are sold out before day 30), not the 12-15 opening
        the symbol's flat pool first took."""
        self._check(_chain(NEW, rename=True), NEW)

    @rule(R.CA_SL_08, R.CA_SL_09)
    def test_a_partial_denial_lands_on_the_fewer_shares_held(self):
        """Sold 100, rebought 100 and sold again, then 40 bought and held:
        40 of the 100 units are denied (400), added to the 40 shares' ACB
        (800). The 12-13 sale is 0 and the 06-02 sale of the 40 for 600
        is -200; before the fix the 400 joined the rebuy, 160 of it was
        denied again on 12-13 and the 40 sold at +40."""
        book = [_tr('2024-11-01', OLD, 100, 2000),
                _tr('2024-12-03', OLD, -100, 1000),
                _tr('2024-12-08', OLD, 100, 1000),
                _tr('2024-12-13', OLD, -100, 1000),
                _tr('2024-12-20', OLD, 40, 400),
                _tr('2025-06-02', OLD, -40, 600)]
        on, err = _run(book)
        self.assertEqual(_records(on), [
            ('2024-12-03', OLD, -600.0, 400.0, 2000.0),
            ('2024-12-13', OLD, 0.0, 0.0, 1000.0),
            ('2025-06-02', OLD, -200.0, 0.0, 800.0)], err)
        self.assertEqual(_adjust_after(on, book), [['2024-12-20']], err)
        off, _ = _run(book, detect_wash_sales=False)
        self.assertEqual(_total(on), _total(off))

    @rule(R.CA_SL_09, R.CA_SL_10)
    def test_a_position_never_sold_out_keeps_the_trigger(self):
        """The common case: the rebuy's position is held through day 30
        (50 of its 100 sold on 12-13), so the addition stays after the
        trigger and the 12-13 sale takes its share of it, as before the
        fix (the golden ledgers pin whole books). The 12-13 sale's own
        denial has a pre-loss trigger (the 12-08 rebuy) and is placed
        after that sale, as before."""
        book = [_tr('2024-11-01', OLD, 100, 2000),
                _tr('2024-12-03', OLD, -100, 1000),
                _tr('2024-12-08', OLD, 100, 1000),
                _tr('2024-12-13', OLD, -50, 600),
                _tr('2025-06-02', OLD, -50, 700)]
        on, err = _run(book)
        self.assertEqual(_records(on), [
            ('2024-12-03', OLD, -500.0, 500.0, 2000.0),
            ('2024-12-13', OLD, 0.0, 150.0, 750.0),
            ('2025-06-02', OLD, -200.0, 0.0, 900.0)], err)
        self.assertEqual(_adjust_after(on, book),
                         [['2024-12-08'], ['2024-12-13']], err)
        off, _ = _run(book, detect_wash_sales=False)
        self.assertEqual(_total(on), _total(off))

    @rule(R.CA_SL_09, R.CA_ACB_RENAME)
    def test_a_rename_that_brings_the_shares_in_takes_no_wait(self):
        """Issue #91: BBB bought 14 days before, AAA bought and sold at a
        loss of 2000, BBB renamed AAA five days later. No wait happens:
        BBB's purchase, in the window and the same property once renamed
        (TJ-2699), is the replacement like any other, so the addition goes
        onto BBB.TO's pool on the loss date (CA-SL-09's old-ticker clause,
        its record's rule) and the rename carries it to AAA's shares
        (every denied dollar back on the two sales). The owner's ruling on
        TJ-4153, question 1 (backlog TJ-4145): the engine is right here and
        in the next test's book, where the old ticker was bought before
        the window and the addition waits (CA-SL-19)."""
        book = [_tr('2025-01-06', 'BBB.TO', 100, 5000),
                _tr('2025-01-10', 'AAA.TO', 100, 10000),
                _tr('2025-01-20', 'AAA.TO', -100, 8000),
                _rename('2025-01-25', 'BBB.TO', 'AAA.TO'),
                _tr('2025-03-03', 'AAA.TO', -40, 4000),
                _tr('2025-04-01', 'AAA.TO', -60, 6000)]
        on, err = _run(book)
        self.assertEqual(_records(on), [
            ('2025-01-20', 'AAA.TO', 0.0, 2000.0, 10000.0),
            ('2025-03-03', 'AAA.TO', 1200.0, 0.0, 2800.0),
            ('2025-04-01', 'AAA.TO', 1800.0, 0.0, 4200.0)], err)
        self.assertEqual(_inventory(on), [])
        assert_rule(self, on, book[2].id, R.CA_SL_09,
                    record=f"WASH_{book[2].id}__{book[0].id}")
        off, _ = _run(book, detect_wash_sales=False)
        self.assertEqual(_total(on), _total(off))

    @rule(R.CA_SL_19, R.CA_ACB_RENAME)
    def test_an_old_ticker_bought_before_the_window_waits_for_the_rename(self):
        """CA-SL-19's first case (book B2 of the ruling on TJ-4153,
        question 1): BBB bought before the window, AAA bought and sold at
        a loss of 2000 with 60 BBB held at day 30 (1200 denied), 40 BBB sold
        two days later at a gain, BBB renamed AAA on 01-25. The only
        purchase in the window is AAA's own, which the loss sale sold out;
        the shares held at day 30 are BBB's, brought in by the rename. So
        the 1200 waits on AAA.TO's flat pool from the loss sale to the
        rename (the record names CA-SL-19): the 01-22 sale carries none of
        it (cost 2000, BBB's own), the 04-01 sale all of it (cost 3000 +
        1200). Both books total the cash, 1000."""
        book = [_tr('2024-11-06', 'BBB.TO', 100, 5000),
                _tr('2025-01-10', 'AAA.TO', 100, 10000),
                _tr('2025-01-20', 'AAA.TO', -100, 8000),
                _tr('2025-01-22', 'BBB.TO', -40, 3200),
                _rename('2025-01-25', 'BBB.TO', 'AAA.TO'),
                _tr('2025-04-01', 'AAA.TO', -60, 4800)]
        on, err = _run(book)
        self.assertEqual(_records(on), [
            ('2025-01-20', 'AAA.TO', -800.0, 1200.0, 10000.0),
            ('2025-01-22', 'BBB.TO', 1200.0, 0.0, 2000.0),
            ('2025-04-01', 'AAA.TO', 600.0, 0.0, 4200.0)], err)
        self.assertEqual(_inventory(on), [])
        assert_rule(self, on, book[2].id, R.CA_SL_19,
                    record=f"WASH_{book[2].id}__{book[1].id}")
        off, _ = _run(book, detect_wash_sales=False)
        self.assertEqual((_total(on), _total(off)), (1000.0, 1000.0))

    @rule(R.CA_SL_09, R.CA_SL_07, R.CA_DISP_04)
    def test_tj_2698s_books_place_the_addition_after_the_replacement(self):
        """TJ-2698's books (tests/test_marked_short_denial.py): the loss
        symbol's rebuy sold again and the NEW.TO shares bought 03-26
        (inside the rename's split window, TJ-2699) held at day 30, with
        a marked short of NEW.TO open in another account or the same one.
        The figures are those tests' own; the 03-08 rebuy is sold out
        before day 30, so the 03-26 NEW.TO purchase is the replacement
        and the addition lands after it (no wait on the flat pool, no
        short in its way)."""
        for book in (_msd_chain() + _msd_short(),
                     _msd_chain(new_oc='O') + _msd_short(acct='b'),
                     _msd_chain()):
            with self.subTest(rows=len(book)):
                res, err = _run(book)
                self.assertTrue(res['summary']['wash_solver_converged'], err)
                self.assertEqual(
                    [r for r in _msd_records(res) if r[0] == '2025-06-02'],
                    [('2025-06-02', NEW, 'b', -800.0, 0.0)], err)
                self.assertEqual(_adjust_after(res, book),
                                 [['2025-03-26']], err)

    @rule(R.CA_SL_05, R.CA_SL_09)
    def test_a_call_bought_sold_and_bought_again_carries_one_denial(self):
        """TJ-2753: 100 ZZC bought for 2000, sold 02-03 for 1000; a call
        on ZZC bought 02-08 for 200, sold 02-13 for 200, bought again
        02-20 for 200 and held past day 30, sold 05-02 for 300. The 02-08
        contract is sold out before day 30, so the 02-20 contract is the
        replacement and takes the 1000: the 02-13 sale 0, the 05-02 sale
        -900 (cost 1200). Before the fix the addition joined the 02-08
        contract and the 02-13 sale was a second superficial loss of
        1000."""
        call = 'ZZC250620C00010000'
        book = [_tr('2025-01-02', 'ZZC', 100, 2000),
                _tr('2025-02-03', 'ZZC', -100, 1000),
                _tr('2025-02-08', call, 1, 200),
                _tr('2025-02-13', call, -1, 200),
                _tr('2025-02-20', call, 1, 200),
                _tr('2025-05-02', call, -1, 300)]
        on, err = _run(book)
        self.assertEqual(_records(on), [
            ('2025-02-03', 'ZZC', 0.0, 1000.0, 2000.0),
            ('2025-02-13', call, 0.0, 0.0, 200.0),
            ('2025-05-02', call, -900.0, 0.0, 1200.0)], err)
        self.assertEqual(_adjust_after(on, book), [['2025-02-20']], err)
        off, _ = _run(book, detect_wash_sales=False)
        self.assertEqual(_total(on), _total(off))

    @rule(R.CA_SL_09, R.CA_SL_19, R.CA_ACB_RENAME)
    def test_a_rename_refills_the_sold_out_position(self):
        """TJ-2754: BBB.TO 100 held since 10-01; OLD.TO 100 bought 11-01
        for 2000, sold 12-03 for 1000, rebought 12-04 and sold 12-06;
        BBB.TO renamed into OLD.TO 12-09 (inside the window, and within
        a week of the loss: the renamed shares are identical property,
        TJ-2699); OLD.TO sold 2025-06-02 for 1200. The addition waits
        from the 12-06 sell-out and the rename's 100 shares take it: the
        12-06 sale 0 and the 06-02 sale -1300 (1500 + 1000). Before the
        fix it joined the rebuy and its sale was a second superficial
        loss. As first written (rebuy 12-08, sold 12-13, the rename
        12-20) the loss is OLD.TO's own row more than a week before the
        rename, another security than BBB.TO's shares: it is allowed and
        the 06-02 sale realizes -300 at BBB.TO's 1500 (TJ-2699)."""
        book = [_tr('2024-10-01', 'BBB.TO', 100, 1500),
                _tr('2024-11-01', OLD, 100, 2000),
                _tr('2024-12-03', OLD, -100, 1000),
                _tr('2024-12-04', OLD, 100, 1000),
                _tr('2024-12-06', OLD, -100, 1000),
                _rename('2024-12-09', 'BBB.TO', OLD),
                _tr('2025-06-02', OLD, -100, 1200)]
        on, err = _run(book)
        self.assertEqual(_records(on), [
            ('2024-12-03', OLD, 0.0, 1000.0, 2000.0),
            ('2024-12-06', OLD, 0.0, 0.0, 1000.0),
            ('2025-06-02', OLD, -1300.0, 0.0, 2500.0)], err)
        self.assertEqual(_adjust_after(on, book), [['2024-12-06']], err)
        # The addition waits from the sell-out (`_sl_landing_row`): CA-SL-19's
        # record, against the plain book's CA-SL-09 (`_check`).
        self.assertEqual(_adjust_rules(on), [[R.CA_SL_19]])
        assert_rule(self, on, book[2].id, R.CA_SL_01, record=book[2].id)
        off, _ = _run(book, detect_wash_sales=False)
        self.assertEqual(_total(on), _total(off))

    @rule(R.CA_SL_19, R.CA_ACB_RENAME)
    def test_a_rename_refills_the_position_the_loss_sold_out(self):
        """CA-SL-19's second case with no rebuy: BBB.TO 100 held since
        10-01; OLD.TO 100 bought 11-25 for 2000 and sold 12-03 for 1000,
        the position sold out by the loss; BBB.TO renamed into OLD.TO
        12-09; OLD.TO sold 2025-06-02 for 1200. No purchase opened the
        position held at day 30: the addition waits on the flat pool from
        the sale and the rename's shares take it (the 06-02 sale -1300 at
        2500). The pool is flat at the addition's placement already, so
        nothing moves it (`_sl_landing_row` gives no row), and its record
        names CA-SL-19 all the same (the review of step 11, F1)."""
        book = [_tr('2024-10-01', 'BBB.TO', 100, 1500),
                _tr('2024-11-25', OLD, 100, 2000),
                _tr('2024-12-03', OLD, -100, 1000),
                _rename('2024-12-09', 'BBB.TO', OLD),
                _tr('2025-06-02', OLD, -100, 1200)]
        on, err = _run(book)
        self.assertEqual(_records(on), [
            ('2024-12-03', OLD, 0.0, 1000.0, 2000.0),
            ('2025-06-02', OLD, -1300.0, 0.0, 2500.0)], err)
        self.assertEqual(_adjust_after(on, book), [['2024-12-03']], err)
        assert_rule(self, on, book[2].id, R.CA_SL_01, record=book[2].id)
        assert_rule(self, on, book[2].id, R.CA_SL_19,
                    record=f"WASH_{book[2].id}__{book[1].id}")
        off, _ = _run(book, detect_wash_sales=False)
        self.assertEqual(_total(on), _total(off))

    @rule(R.CA_SL_09, R.CA_SL_10)
    def test_part_of_the_last_position_sold_before_day_30(self):
        """TJ-2755 (decided: as the engine stands) on TJ-2761's shape: the
        sold-out 12-08 rebuy is no replacement; the 12-15 purchase of 30
        takes 300 and the 12-20 purchase of 80 takes 700 (CA-SL-10, each
        up to what it opened), as without the round trip and as the base
        gave. The 12-16 sale of 10 carries its third of the 300 (cost
        200, a loss of 100 denied again onto the 12-20 shares), as a
        partial sale of a held trigger's position does (A2-0555), as
        CA-SL-09 says. s.54(b) read strictly would leave the 12-16 sale
        at its own cost of 100 (gain 0, no denial), the 300 on the 20
        held, for the same -800 on 06-02; the pooled reading was kept,
        with or without a round trip."""
        book = [_tr('2024-11-01', OLD, 100, 2000),
                _tr('2024-12-03', OLD, -100, 1000),
                _tr('2024-12-08', OLD, 100, 1000),
                _tr('2024-12-13', OLD, -100, 1000),
                _tr('2024-12-15', OLD, 30, 300),
                _tr('2024-12-16', OLD, -10, 100),
                _tr('2024-12-20', OLD, 80, 800),
                _tr('2025-06-02', OLD, -100, 1200)]
        on, err = _run(book)
        self.assertEqual(_records(on), [
            ('2024-12-03', OLD, 0.0, 1000.0, 2000.0),
            ('2024-12-13', OLD, 0.0, 0.0, 1000.0),
            ('2024-12-16', OLD, 0.0, 100.0, 200.0),
            ('2025-06-02', OLD, -800.0, 0.0, 2000.0)], err)
        self.assertEqual(_adjust_after(on, book),
                         [['2024-12-15', '2024-12-20'], ['2024-12-20']], err)
        off, _ = _run(book, detect_wash_sales=False)
        self.assertEqual(_total(on), _total(off))

    @rule(R.CA_SL_09, R.CA_SL_10)
    def test_a_sold_out_rebuy_is_no_replacement(self):
        """TJ-2761 (the second review's books A and A2): the sold-out
        12-08 rebuy, then the held position in two purchases of unequal
        size (30 on 12-15, 80 on 01-02, day 30) with a sale of 10 at a
        gain between them, across the year end. The 12-15 purchase takes
        300 and the 01-02 one 700, with or without the round trip: the
        12-31 sale +300 (2024) and the 06-02 sale -700 (2025). The first
        round put all 1000 on the 12-15 purchase (12-31 +66.67, 06-02
        -466.67: 2024 understated by 233)."""
        trip = [_tr('2024-12-08', OLD, 100, 1000),
                _tr('2024-12-13', OLD, -100, 1000)]
        rest = [_tr('2024-12-15', OLD, 30, 300),
                _tr('2024-12-31', OLD, -10, 500),
                _tr('2025-01-02', OLD, 80, 800),
                _tr('2025-06-02', OLD, -100, 1200)]
        head = [_tr('2024-11-01', OLD, 100, 2000),
                _tr('2024-12-03', OLD, -100, 1000)]
        want = [('2024-12-03', OLD, 0.0, 1000.0, 2000.0),
                ('2024-12-31', OLD, 300.0, 0.0, 200.0),
                ('2025-06-02', OLD, -700.0, 0.0, 1900.0)]
        for name, book in (('A', head + trip + rest), ('A2', head + rest)):
            with self.subTest(book=name):
                on, err = _run(book)
                recs = [r for r in _records(on) if r[0] != '2024-12-13']
                self.assertEqual(recs, want, err)
                self.assertEqual(_adjust_after(on, book),
                                 [['2024-12-15', '2025-01-02']], err)
                # The report names the replacements the addition lands on
                # (TJ-2766 c), never the sold-out rebuy.
                w, = on['wash_sales']
                ids = {t.date: t.id for t in book}
                self.assertEqual(w['trigger_lot_id'], ids['2024-12-15'], err)
                self.assertEqual(
                    [e['replacement_lot_ids'] for e in on['transactions']
                     if e['date'] == '2024-12-03'],
                    [[ids['2024-12-15'], ids['2025-01-02']]], err)
                off, _ = _run(book, detect_wash_sales=False)
                self.assertEqual(_total(on), _total(off))

    @rule(R.CA_SL_03, R.CA_SL_09, R.CA_SL_10)
    def test_a_registered_purchase_before_the_held_taxable_one(self):
        """TJ-2762 (books G and G2, U for an affiliated holder): the
        taxable rebuy sold out, an RRSP purchase on 12-10 and a taxable
        one on 12-20 both held at day 30. The RRSP purchase comes first
        in CA-SL-10's order, so the 1000 is lost for good whether the
        round trip is in the book or not (the base deferred it onto the
        12-20 shares when the round trip was there)."""
        trip = [_tr('2024-12-08', OLD, 100, 1000),
                _tr('2024-12-13', OLD, -100, 1000)]
        head = [_tr('2024-11-01', OLD, 100, 2000),
                _tr('2024-12-03', OLD, -100, 1000)]
        tail = [_tr('2024-12-20', OLD, 100, 1000),
                _tr('2025-06-02', OLD, -100, 1200)]
        other = [_tr('2024-12-10', OLD, 100, 1000, acct=RRSP)]
        for name, book, kw in (('G', head + trip + tail,
                                {'sheltered_transactions': other}),
                               ('G2', head + tail,
                                {'sheltered_transactions': other}),
                               ('U', head + trip + tail,
                                {'affiliated_transactions': other})):
            with self.subTest(book=name):
                on, err = _run(book, **kw)
                recs = [r for r in _records(on) if r[0] != '2024-12-13']
                self.assertEqual(recs, [
                    ('2024-12-03', OLD, 0.0, 1000.0, 2000.0),
                    ('2025-06-02', OLD, 200.0, 0.0, 1000.0)], err)
                self.assertEqual(
                    [e['permanently_disallowed'] for e in on['transactions']
                     if e['date'] == '2024-12-03'], [1000.0], err)
                self.assertEqual(_adjust_after(on, book), [[]], err)

    @rule(R.CA_SL_03, R.CA_SL_09, R.CA_SL_10)
    def test_a_registered_holders_pool_is_judged_on_its_own_balance(self):
        """TJ-2767 (books RS1, RS1b, RS1c): whether a registered trigger's
        position is sold out before day 30 is read from the RRSP's own
        running balance, not from the taxable pool's snapshot its rows
        inherit. RS1: the taxable pool flat 12-03 to 12-20; the RRSP buys
        100 on 12-10 and 50 on 12-15 and holds: the 12-10 purchase is the
        earliest replacement, 1000 lost for good (06-02 +200). RS1b: the
        RRSP buys 100 on 12-10 and sells 50 on 12-15, holding 50: 500
        lost and 500 deferred (06-02 -300). RS1c: the taxable rebuy on
        12-14, before the RRSP's second purchase: 1000 lost. The base's
        figures all three; the branch's second round read the RRSP's
        12-10 purchase as sold out in RS1 and RS1b (500 / 500 and 1000
        deferred)."""
        head = [_tr('2024-11-01', OLD, 100, 2000),
                _tr('2024-12-03', OLD, -100, 1000)]
        tail = [_tr('2025-06-02', OLD, -100, 1200)]
        for name, taxable, rrsp, perm, gain, cost in (
                ('RS1', head + [_tr('2024-12-20', OLD, 100, 1000)] + tail,
                 [_tr('2024-12-10', OLD, 100, 1000, acct=RRSP),
                  _tr('2024-12-15', OLD, 50, 500, acct=RRSP)],
                 1000.0, 200.0, 1000.0),
                ('RS1b', head + [_tr('2024-12-20', OLD, 100, 1000)] + tail,
                 [_tr('2024-12-10', OLD, 100, 1000, acct=RRSP),
                  _tr('2024-12-15', OLD, -50, 600, acct=RRSP)],
                 500.0, -300.0, 1500.0),
                ('RS1c', head + [_tr('2024-12-14', OLD, 100, 1000)] + tail,
                 [_tr('2024-12-10', OLD, 100, 1000, acct=RRSP),
                  _tr('2024-12-15', OLD, 50, 500, acct=RRSP)],
                 1000.0, 200.0, 1000.0)):
            with self.subTest(book=name):
                on, err = _run(taxable, sheltered_transactions=rrsp)
                self.assertEqual(_records(on), [
                    ('2024-12-03', OLD, 0.0, 1000.0, 2000.0),
                    ('2025-06-02', OLD, gain, 0.0, cost)], err)
                self.assertEqual(
                    [e['permanently_disallowed'] for e in on['transactions']
                     if e['date'] == '2024-12-03'], [perm], err)
                # Nothing stays in the inventory: the solver's total is
                # the raw one plus the part lost for good.
                off, _ = _run(taxable, sheltered_transactions=rrsp,
                              detect_wash_sales=False)
                self.assertEqual(round(_total(on) - _total(off), 2), perm)

    @rule(R.CA_SL_19, R.CA_OPEN_01)
    def test_an_opening_balance_takes_the_waiting_addition(self):
        """TJ-2768 (book OB1): the rebuy sold out 12-13; an OPENING_BALANCE
        of 100 on 12-20 opens the position held at day 30 (missing
        history: its own cost unknown, the pool tainted). The 1000 waits
        from the sell-out and the opening balance takes it as a purchase
        would: the 06-02 sale costs 1000 (+200), the total +200 as with
        the solver off. Before, the opening balance added its units
        without the wait's pop and the 1000 was lost (06-02 +1200)."""
        book = [_tr('2024-11-01', OLD, 100, 2000),
                _tr('2024-12-03', OLD, -100, 1000),
                _tr('2024-12-08', OLD, 100, 1000),
                _tr('2024-12-13', OLD, -100, 1000),
                _tr('2024-12-20', OLD, 100, 1000, action='OPENING_BALANCE'),
                _tr('2025-06-02', OLD, -100, 1200)]
        on, err = _run(book)
        self.assertEqual(_records(on), [
            ('2024-12-03', OLD, 0.0, 1000.0, 2000.0),
            ('2024-12-13', OLD, 0.0, 0.0, 1000.0),
            ('2025-06-02', OLD, 200.0, 0.0, 1000.0)], err)
        self.assertEqual(_adjust_after(on, book), [['2024-12-13']], err)
        off, _ = _run(book, detect_wash_sales=False)
        self.assertEqual(_total(on), _total(off))
        self.assertEqual(_total(on), 200.0)

    @rule(R.CA_SL_09, R.CA_ACB_RENAME)
    def test_a_rename_inside_the_window_is_no_landing_row(self):
        """TJ-2760 (books B, B2, B5): the rebuy sold out 12-13, OLD.TO
        renamed NEW.TO inside the window (12-15; on the reopening's day
        listed before it; as a 2:1 split-rename), 100 NEW.TO bought 12-20
        and held. The 12-20 purchase is the replacement and takes the
        1000: the 06-02 sale -800 (cost 2000). The first round took the
        rename row as the sell-out, keyed the addition to OLD.TO after its
        pool was gone, and the 1000 vanished (06-02 +200, the total not
        conserved)."""
        head = [_tr('2024-11-01', OLD, 100, 2000),
                _tr('2024-12-03', OLD, -100, 1000),
                _tr('2024-12-08', OLD, 100, 1000),
                _tr('2024-12-13', OLD, -100, 1000)]
        two = _rename('2024-12-15', OLD, NEW)
        two.quantity = 2.0
        for name, book, qty in (
                ('B', head + [_rename('2024-12-15', OLD, NEW),
                              _tr('2024-12-20', NEW, 100, 1000),
                              _tr('2025-06-02', NEW, -100, 1200)], 100),
                ('B2', head + [_rename('2024-12-20', OLD, NEW),
                               _tr('2024-12-20', NEW, 100, 1000),
                               _tr('2025-06-02', NEW, -100, 1200)], 100),
                ('B5', head + [two, _tr('2024-12-20', NEW, 200, 1000),
                               _tr('2025-06-02', NEW, -200, 1200)], 200)):
            with self.subTest(book=name):
                on, err = _run(book)
                self.assertEqual(_records(on), [
                    ('2024-12-03', OLD, 0.0, 1000.0, 2000.0),
                    ('2024-12-13', OLD, 0.0, 0.0, 1000.0),
                    ('2025-06-02', NEW, -800.0, 0.0, 2000.0)], err)
                self.assertEqual(_adjust_after(on, book), [['2024-12-20']], err)
                self.assertEqual(_inventory(on), [], err)
                off, _ = _run(book, detect_wash_sales=False)
                self.assertEqual(_total(on), _total(off))

    @rule(R.CA_SL_09, R.CA_SL_19)
    def test_the_texts_cases_a_partial_loss_and_two_sell_outs(self):
        """TJ-2765 (books N and X). N: the held position opens with 100
        bought 12-15, 50 sold 12-16 for 400 (a loss on its own cost), 50
        bought 12-20: the 12-15 purchase takes the 1000, the 12-16 sale
        carries half (cost 1000, a loss of 600 denied again onto the
        12-20 shares), the 06-02 sale -900. X: 100 bought 12-15 and sold
        12-18 for 1500, 100 bought 12-20: both round trips are sold out,
        the 12-20 purchase is the replacement, the 12-18 sale +500 and
        the 06-02 sale -800."""
        head = [_tr('2024-11-01', OLD, 100, 2000),
                _tr('2024-12-03', OLD, -100, 1000),
                _tr('2024-12-08', OLD, 100, 1000),
                _tr('2024-12-13', OLD, -100, 1000)]
        n = head + [_tr('2024-12-15', OLD, 100, 1000),
                    _tr('2024-12-16', OLD, -50, 400),
                    _tr('2024-12-20', OLD, 50, 500),
                    _tr('2025-06-02', OLD, -100, 1200)]
        on, err = _run(n)
        self.assertEqual(_records(on), [
            ('2024-12-03', OLD, 0.0, 1000.0, 2000.0),
            ('2024-12-13', OLD, 0.0, 0.0, 1000.0),
            ('2024-12-16', OLD, 0.0, 600.0, 1000.0),
            ('2025-06-02', OLD, -900.0, 0.0, 2100.0)], err)
        self.assertEqual(_adjust_after(on, n),
                         [['2024-12-15'], ['2024-12-20']], err)
        x = head + [_tr('2024-12-15', OLD, 100, 1000),
                    _tr('2024-12-18', OLD, -100, 1500),
                    _tr('2024-12-20', OLD, 100, 1000),
                    _tr('2025-06-02', OLD, -100, 1200)]
        on, err = _run(x)
        self.assertEqual(_records(on), [
            ('2024-12-03', OLD, 0.0, 1000.0, 2000.0),
            ('2024-12-13', OLD, 0.0, 0.0, 1000.0),
            ('2024-12-18', OLD, 500.0, 0.0, 1000.0),
            ('2025-06-02', OLD, -800.0, 0.0, 2000.0)], err)
        self.assertEqual(_adjust_after(on, x), [['2024-12-20']], err)


# ---------------------------------------------------------------- TJ-2720

def _fills(low_first):
    """200 SYQ.TO bought for 2000; sold as two fills on 02-03, 100 for 600
    and 100 for 650 (one sale, a loss of 750), listed low fill first or
    high fill first; a taxable rebuy of 100 for 600 on 02-04 and an RRSP
    purchase of 100 on 02-06, both held. Returns (taxable, sheltered)."""
    low = _tr('2025-02-03', SYQ, -100, 600)
    high = _tr('2025-02-03', SYQ, -100, 650)
    first, second = (low, high) if low_first else (high, low)
    first.time, second.time = '10:00:00', '10:00:01'
    return ([_tr('2025-01-02', SYQ, 200, 2000), first, second,
             _tr('2025-02-04', SYQ, 100, 600)],
            [_tr('2025-02-06', SYQ, 100, 600, acct=RRSP)])


class TestASalesFillsSplitTheDenialByTheUnitsHeld(unittest.TestCase):
    """TJ-2720: the sale's 750 is denied in full (200 sold, 200 acquired,
    200 held); the taxable account holds 100 at day 30 and the RRSP 100,
    so 375 is deferred (the taxable ACB 975) and 375 lost for good, in
    either listing order. Before the fix the fill listed first took the
    taxable replacement whole: 400 and 350, or 350 and 400."""

    def _check(self, low_first):
        taxable, sheltered = _fills(low_first)
        on, err = _run(taxable, sheltered_transactions=sheltered)
        self.assertTrue(on['summary']['wash_solver_converged'], err)
        self.assertEqual(_records(on), [
            ('2025-02-03', SYQ, 0.0, 350.0, 1000.0),
            ('2025-02-03', SYQ, 0.0, 400.0, 1000.0)], err)
        self.assertEqual(_inventory(on), [(SYQ, 100.0, 975.0, 375.0)], err)
        # Each fill's denial: half to the taxable replacement (its ACB
        # addition), half to the RRSP's (permanent).
        perm = sorted(round(e['permanently_disallowed'], 2)
                      for e in on['transactions'] if 'gain' in e)
        self.assertEqual(perm, [175.0, 200.0], err)
        adds = sorted(round(a['amount'], 2) for w in on['wash_sales']
                      for a in w['adjusts'])
        self.assertEqual(adds, [175.0, 200.0], err)
        # The solver off: the whole loss; on, the denial keeps the
        # deferred part in the ACB and loses exactly the RRSP's part.
        off, _ = _run(taxable, sheltered_transactions=sheltered,
                      detect_wash_sales=False)
        self.assertEqual(_total(off), -750.0)
        self.assertEqual(_total(on), 0.0)
        self.assertEqual(round(_total(off) + 375.0 + sum(perm), 2), 0.0)

    @rule(R.CA_SL_08, R.CA_SL_09)
    def test_the_low_fill_listed_first(self):
        self._check(low_first=True)

    @rule(R.CA_SL_08, R.CA_SL_09)
    def test_the_high_fill_listed_first(self):
        self._check(low_first=False)

    @rule(R.CA_SL_08, R.CA_SL_10)
    def test_the_taxable_replacement_first_takes_the_whole_draw(self):
        """TJ-2757: 100 sold as two fills of 50 (300 and 325); the taxable
        account rebuys 100 on 02-04 and an RRSP buys 100 on 02-06, both
        held. 100 units are denied (375); the sale draws on the triggers
        in order (CA-SL-10), the taxable rebuy first up to its 100 held:
        all 375 deferred (ACB 975), none lost. Not a split by holders."""
        low = _tr('2025-02-03', SYQ, -50, 300)
        high = _tr('2025-02-03', SYQ, -50, 325, time='10:00:01')
        on, err = _run([_tr('2025-01-02', SYQ, 100, 1000), low, high,
                        _tr('2025-02-04', SYQ, 100, 600)],
                       sheltered_transactions=[
                           _tr('2025-02-06', SYQ, 100, 600, acct=RRSP)])
        self.assertEqual(_records(on), [
            ('2025-02-03', SYQ, 0.0, 175.0, 500.0),
            ('2025-02-03', SYQ, 0.0, 200.0, 500.0)], err)
        self.assertEqual(_inventory(on), [(SYQ, 100.0, 975.0, 375.0)], err)
        self.assertEqual([e['permanently_disallowed']
                          for e in on['transactions'] if 'gain' in e],
                         [0.0, 0.0], err)

    @rule(R.CA_SL_08, R.CA_SL_10)
    def test_a_purchase_between_two_fills_is_before_the_sale(self):
        """TJ-2756: 100 sold as two fills of 50 at 10:00 and 11:00; the
        RRSP buys 100 that day; the taxable account rebuys 100 the next
        day. The sale's moment is its last fill (CA-SL-08's placement),
        so an RRSP purchase at 09:30 or 10:30 is before the sale and the
        next day's taxable rebuy, after it, takes the whole 100 units
        (CA-SL-10): 400 deferred, ACB 1000, whatever the fills' clock.
        At 11:30 the RRSP purchase is after the sale and the earliest
        replacement: 400 lost for good. (Fill by fill, the base gave 200
        and 200 for 10:30 and the first fill's view for both fills on the
        branch's first round.)"""
        for when, perm in (('09:30:00', 0.0), ('10:30:00', 0.0),
                           ('11:30:00', 400.0)):
            with self.subTest(when=when):
                on, err = _run(
                    [_tr('2025-01-02', SYQ, 100, 1000),
                     _tr('2025-02-03', SYQ, -50, 300, time='10:00:00'),
                     _tr('2025-02-03', SYQ, -50, 300, time='11:00:00'),
                     _tr('2025-02-04', SYQ, 100, 600)],
                    sheltered_transactions=[
                        _tr('2025-02-03', SYQ, 100, 600, acct=RRSP,
                            time=when)])
                self.assertEqual(
                    sorted(round(e['permanently_disallowed'], 2)
                           for e in on['transactions'] if 'gain' in e),
                    [perm / 2, perm / 2], err)
                self.assertEqual(_inventory(on),
                                 [(SYQ, 100.0, 1000.0 - perm, 400.0 - perm)],
                                 err)

    @rule(R.CA_SL_08, R.CA_SL_10)
    def test_the_sales_moment_is_its_last_losing_fill(self):
        """TJ-2763 (book J2): 100 sold on 02-03 as 40 for 240 (10:00),
        40 for 240 (11:00) and 20 for 300 at 12:00 (a gain); the RRSP
        buys 100 at 11:30, between the last losing fill and the gain
        fill; a taxable rebuy on 02-04. The sale's moment is its last
        losing fill (where CA-SL-08 places a pre-loss addition), so the
        11:30 purchase is after the sale and the earliest replacement:
        the 80 denied units are its, 320 lost for good; the gain fill is
        its own +100."""
        on, err = _run(
            [_tr('2025-01-02', SYQ, 100, 1000),
             _tr('2025-02-03', SYQ, -40, 240, time='10:00:00'),
             _tr('2025-02-03', SYQ, -40, 240, time='11:00:00'),
             _tr('2025-02-03', SYQ, -20, 300, time='12:00:00'),
             _tr('2025-02-04', SYQ, 100, 600)],
            sheltered_transactions=[
                _tr('2025-02-03', SYQ, 100, 600, acct=RRSP, time='11:30:00')])
        self.assertEqual(
            [(round(e['gain'], 2), round(e['permanently_disallowed'], 2))
             for e in on['transactions'] if 'gain' in e],
            [(0.0, 160.0), (0.0, 160.0), (100.0, 0.0)], err)
        self.assertEqual(_inventory(on), [(SYQ, 100.0, 600.0, 0.0)], err)

    @rule(R.CA_SL_08)
    def test_two_taxable_replacements_with_a_sale_between_them(self):
        """TJ-2764 (book Q): taxable only; 200 sold as two fills (600 and
        650), 100 rebought 02-04, 50 of them sold 02-06, 100 bought 02-10.
        Each fill takes its units' share of both replacements' takes, so
        the 02-06 sale between the two purchases costs 487.50 with 137.50
        denied (the base, fill by fill: 493.75 and 143.75); the totals and
        the inventory are the base's."""
        low = _tr('2025-02-03', SYQ, -100, 600)
        high = _tr('2025-02-03', SYQ, -100, 650, time='10:00:01')
        on, err = _run([_tr('2025-01-02', SYQ, 200, 2000), low, high,
                        _tr('2025-02-04', SYQ, 100, 600),
                        _tr('2025-02-06', SYQ, -50, 350),
                        _tr('2025-02-10', SYQ, 100, 600)])
        sale = [e for e in on['transactions'] if e['date'] == '2025-02-06']
        self.assertEqual([(round(e['cost'], 2),
                           round(e['disallowed_amount'], 2)) for e in sale],
                         [(487.5, 137.5)], err)
        # 50 at 487.50 and 100 at 600 + 187.50 + 137.50.
        self.assertEqual(_inventory(on), [(SYQ, 150.0, 1412.5, 512.5)], err)

    @rule(R.CA_SL_08)
    def test_one_holder_two_fills_is_unchanged(self):
        """The common case: both fills backed by one taxable rebuy of 200.
        The whole 750 is deferred onto it in either order, as before."""
        for low_first in (True, False):
            taxable, _ = _fills(low_first)
            taxable[-1] = _tr('2025-02-04', SYQ, 200, 1200)
            on, err = _run(taxable)
            self.assertEqual(_inventory(on),
                             [(SYQ, 200.0, 1950.0, 750.0)], err)
            self.assertEqual(_total(on), 0.0)


# ---------------------------------------------------------------- TJ-2719

PUT_A, PUT_B = 'SYQ251212P00012000.TO', 'SYQ260116P00012000.TO'


def _assigned(a_first, first, sold, proceeds, second):
    """Put A (2 contracts, written for 200) and put B (1 contract, written
    for 300), both 12-strike, assigned at one moment, A's or B's row listed
    first; the shares come as a marked delivery of `first` at 12, a sale
    of `sold` for `proceeds`, a marked delivery of `second` at 12."""
    opts = [_tr('2025-12-12', PUT_A, 2, 0, acct='m', time='16:00:00',
                action='ASSIGN'),
            _tr('2025-12-12', PUT_B, 1, 0, acct='m', time='16:00:00',
                action='ASSIGN')]
    if not a_first:
        opts.reverse()
    return ([_tr('2025-11-03', PUT_A, -2, 200, acct='m'),
             _tr('2025-11-04', PUT_B, -1, 300, acct='m')] + opts + [
            _tr('2025-12-12', SYQ, first, 12 * first, acct='m',
                time='16:00:01', action='ASSIGN'),
            _tr('2025-12-12', SYQ, -sold, proceeds, acct='m',
                time='16:00:02'),
            _tr('2025-12-12', SYQ, second, 12 * second, acct='m',
                time='16:00:03', action='ASSIGN')])


class TestEachPutsPremiumStaysOnItsOwnShares(unittest.TestCase):
    """TJ-2719: 300 shares at 12 (3600) less the two premiums (500) is
    3100 of cost however the delivery is split; what the first delivery
    carries is at most the premium of the shares it holds. TJ-2750: each
    delivery is A's and B's pro rata by contracts (2:1), so the figures
    are the same with either option row first. Each book runs with the
    solver on and off: the fold does not depend on it."""

    def _figures(self, book):
        out = []
        for solver in (True, False):
            res, err = _run(book, detect_wash_sales=solver)
            self.assertEqual(res['wash_sales'], [], err)
            out.append(([(round(r['cost'], 2), round(r['gain'], 2))
                         for r in res['transactions']
                         if r.get('raw_gain') is not None],
                        _inventory(res)))
        self.assertEqual(out[0], out[1])
        return out[0]

    @rule(R.CA_OPT_06, R.CA_OPT_08)
    def test_two_deliveries_of_150_with_a_sale_between(self):
        """The entry's book (TJ-2719, TJ-2750). Each 150-share delivery
        is A's 100 (2/3) and B's 50 (1/3): 100 x 1 + 50 x 3 = 250 of
        premium, so each costs 1800 - 250 = 1550; the sale of the first
        for 1900 gains 350 and the second is held at 1550, in either
        row order. Before TJ-2750's fix the row order decided which put
        the first delivery was: with B's row first it was A's 150 (1650
        sold, 1450 held) and with A's row first B's 100 and A's 50 (1450
        sold for a gain of 450, 1650 held). Before TJ-2719's fix A's row
        first took 450 of premium on the first 150 shares (1350, a gain
        of 550) and left 1750 on the held 150."""
        # TJ-2750: one answer in either order.
        self.assertEqual(self._figures(_assigned(False, 150, 150, 1900, 150)),
                         ([(1550.0, 350.0)], [(SYQ, 150.0, 1550.0, 0.0)]))
        self.assertEqual(self._figures(_assigned(True, 150, 150, 1900, 150)),
                         ([(1550.0, 350.0)], [(SYQ, 150.0, 1550.0, 0.0)]))

    @rule(R.CA_OPT_06, R.CA_OPT_08)
    def test_a_delivery_of_250_then_50(self):
        """The remainder case (TJ-2750). 250 shares at 2:1 are 166.67
        and 83.33: 166 and 83 in whole shares, and the share left goes
        to A, whose fraction dropped (.67) is the larger: A 167, B 83;
        167 x 1 + 83 x 3 = 416 of premium, 3000 - 416 = 2584 sold for
        3100, a gain of 516. The 50 then delivered are what each still
        needs, A's 33 and B's 17 (33 x 1 + 17 x 3 = 84): 600 - 84 = 516
        held; 2584 + 516 = 3100, the 3600 less the 500 of premium.
        Before TJ-2750's fix the 50 were one put's last 50 by row order
        (A's row first: B's at 3 a share, 550 held and 2550 sold; B's
        first: A's at 1, 450 held and 2650 sold); before TJ-2719's fix
        they carried none in either order (600) and the 250 all 500."""
        # TJ-2750: one answer in either order.
        self.assertEqual(self._figures(_assigned(True, 250, 250, 3100, 50)),
                         ([(2584.0, 516.0)], [(SYQ, 50.0, 516.0, 0.0)]))
        self.assertEqual(self._figures(_assigned(False, 250, 250, 3100, 50)),
                         ([(2584.0, 516.0)], [(SYQ, 50.0, 516.0, 0.0)]))

    @rule(R.CA_OPT_06, R.CA_OPT_08)
    def test_one_assignment_one_delivery_is_unchanged(self):
        """The common case: a put's own delivery takes its whole premium;
        a second put's delivery, listed first or second, its own."""
        book = [_tr('2025-11-03', PUT_A, -2, 200, acct='m'),
                _tr('2025-11-04', PUT_B, -1, 300, acct='m'),
                _tr('2025-12-12', PUT_A, 2, 0, acct='m', time='16:00:00',
                    action='ASSIGN'),
                _tr('2025-12-12', PUT_B, 1, 0, acct='m', time='16:00:00',
                    action='ASSIGN'),
                _tr('2025-12-12', SYQ, 200, 2400, acct='m',
                    time='16:00:01', action='ASSIGN'),
                _tr('2025-12-12', SYQ, -200, 2500, acct='m',
                    time='16:00:02'),
                _tr('2025-12-12', SYQ, 100, 1200, acct='m',
                    time='16:00:03', action='ASSIGN')]
        self.assertEqual(self._figures(book),
                         ([(2200.0, 300.0)], [(SYQ, 100.0, 900.0, 0.0)]))


# ---------------------------------------------------------------- TJ-2750

PUT_C = 'SYQ260220P00012000.TO'


def _three_assigned(order, first, sold, proceeds, second):
    """Puts A (2 contracts, written for 200), B (1, for 300) and C (1,
    for 100), all 12-strike, assigned at one moment with their rows in
    `order` (a string of A, B and C); the shares a marked delivery of
    `first` at 12, a sale of `sold` for `proceeds`, a marked delivery of
    `second` at 12."""
    opts = {'A': _tr('2025-12-12', PUT_A, 2, 0, acct='m', time='16:00:00',
                     action='ASSIGN'),
            'B': _tr('2025-12-12', PUT_B, 1, 0, acct='m', time='16:00:00',
                     action='ASSIGN'),
            'C': _tr('2025-12-12', PUT_C, 1, 0, acct='m', time='16:00:00',
                     action='ASSIGN')}
    return ([_tr('2025-11-03', PUT_A, -2, 200, acct='m'),
             _tr('2025-11-04', PUT_B, -1, 300, acct='m'),
             _tr('2025-11-05', PUT_C, -1, 100, acct='m')]
            + [opts[k] for k in order] + [
            _tr('2025-12-12', SYQ, first, 12 * first, acct='m',
                time='16:00:01', action='ASSIGN'),
            _tr('2025-12-12', SYQ, -sold, proceeds, acct='m',
                time='16:00:02'),
            _tr('2025-12-12', SYQ, second, 12 * second, acct='m',
                time='16:00:03', action='ASSIGN')])


class TestAThreeWayTieSharesEachDeliveryProRata(unittest.TestCase):
    """TJ-2750, three same-strike puts (2, 1 and 1 contracts; 1, 3 and 1
    of premium a share) and deliveries of 250 and 150 that are the
    quantity of none of them. The 250 at 2:1:1 are 125, 62.5 and 62.5:
    125, 62 and 62 whole, and the share left goes to the larger of the
    two halves dropped, equal, so to the earlier series, B: 125 x 1 +
    63 x 3 + 62 x 1 = 376 of premium, 3000 - 376 = 2624 sold for 3100,
    a gain of 476. The 150 are what each still needs, 75, 37 and 38
    (75 + 111 + 38 = 224): 1800 - 224 = 1576 held. The six row orders
    agree, and 2624 + 1576 = 4200 is the 4800 less the 600 of premium."""

    @rule(R.CA_OPT_06, R.CA_OPT_08)
    def test_every_row_order_gives_the_same_figures(self):
        for order in itertools.permutations('ABC'):
            with self.subTest(order=''.join(order)):
                res, err = _run(_three_assigned(order, 250, 250, 3100, 150))
                self.assertEqual(res['wash_sales'], [], err)
                self.assertEqual(
                    [(round(r['cost'], 2), round(r['gain'], 2))
                     for r in res['transactions']
                     if r.get('raw_gain') is not None], [(2624.0, 476.0)])
                self.assertEqual(_inventory(res),
                                 [(SYQ, 150.0, 1576.0, 0.0)])


class TestProRataShares(unittest.TestCase):
    """`_pro_rata`, the split behind TJ-2750: by weight, exact where it
    divides, else whole units with the remainder to the largest fraction
    dropped and then the members' order (the larger weight, then rank);
    none over its cap, a capped member's excess going to the rest. The
    members are sorted inside, so the input's order is nothing (TJ-3180),
    and a fractional total or cap is split exactly, not in whole units."""

    @rule(R.CA_OPT_08)
    def test_exact_shares_and_a_remainder(self):
        self.assertEqual(_pro_rata(150, [('a', 200, 200, 0), ('b', 100, 100, 1)]),
                         {'a': 100.0, 'b': 50.0})
        self.assertEqual(_pro_rata(250, [('a', 200, 200, 0), ('b', 100, 100, 1)]),
                         {'a': 167.0, 'b': 83.0})
        # Equal fractions: the members' order (the larger first, then
        # the rank _pair_assign_legs gives: the earlier expiry).
        self.assertEqual(_pro_rata(250, [('a', 200, 200, 0), ('b', 100, 100, 1),
                                         ('c', 100, 100, 2)]),
                         {'a': 125.0, 'b': 63.0, 'c': 62.0})

    @rule(R.CA_OPT_08)
    def test_the_input_order_is_nothing(self):
        # TJ-3180: the same split however the members are listed.
        self.assertEqual(_pro_rata(250, [('b', 100, 100, 1), ('a', 200, 200, 0)]),
                         {'a': 167.0, 'b': 83.0})
        self.assertEqual(_pro_rata(250, [('c', 100, 100, 2), ('b', 100, 100, 1),
                                         ('a', 200, 200, 0)]),
                         {'a': 125.0, 'b': 63.0, 'c': 62.0})
        self.assertEqual(_pro_rata(1, [('b', 100, 100, 1), ('a', 100, 100, 0)]),
                         {'a': 1.0, 'b': 0.0})

    @rule(R.CA_OPT_08)
    def test_a_fractional_total_or_cap_is_split_exactly(self):
        # TJ-3180: 1.5 shares of two equal assignments are 0.75 each,
        # not 1 and 0.5; a fractional cap keeps every share exact.
        self.assertEqual(_pro_rata(1.5, [('a', 100, 100, 0), ('b', 100, 100, 1)]),
                         {'a': 0.75, 'b': 0.75})
        self.assertEqual(_pro_rata(0.5, [('b', 100, 100, 1), ('a', 100, 100, 0)]),
                         {'a': 0.25, 'b': 0.25})
        self.assertEqual(_pro_rata(10, [('a', 1, 0.4, 0), ('b', 1, 0.4, 1),
                                        ('c', 1, 100, 2)]),
                         {'a': 0.4, 'b': 0.4, 'c': 9.2})
        got = _pro_rata(0.3, [('a', 1, 0.1, 0), ('b', 2, 0.2, 1)])
        self.assertAlmostEqual(got['a'], 0.1)
        self.assertAlmostEqual(got['b'], 0.2)

    @rule(R.CA_OPT_08)
    def test_caps_hold_and_pass_the_excess_on(self):
        self.assertEqual(_pro_rata(50, [('a', 200, 33.0, 0), ('b', 100, 17.0, 1)]),
                         {'a': 33.0, 'b': 17.0})
        self.assertEqual(_pro_rata(150, [('a', 200, 75.0, 0), ('b', 100, 37.0, 1),
                                         ('c', 100, 38.0, 2)]),
                         {'a': 75.0, 'b': 37.0, 'c': 38.0})
        # More than the members need: the rest is left unplaced.
        self.assertEqual(_pro_rata(400, [('a', 200, 200, 0), ('b', 100, 100, 1)]),
                         {'a': 200.0, 'b': 100.0})


# ------------------------------------------- the review, TJ-3173 to TJ-3182

PUT_10 = 'SYQ251212P00010000.TO'


def _two_puts(order, a, b, legs, puts=(PUT_A, PUT_B), leg_px=12):
    """Puts A (`a`: contracts, premium) and B (`b`), assigned at one
    moment with their rows in `order` ('AB' or 'BA'); then `legs`, in
    order: ('D', qty) a marked delivery at `leg_px`, ('S', qty, proceeds)
    a sale."""
    opts = {'A': _tr('2025-12-12', puts[0], a[0], 0, acct='m',
                     time='16:00:00', action='ASSIGN'),
            'B': _tr('2025-12-12', puts[1], b[0], 0, acct='m',
                     time='16:00:00', action='ASSIGN')}
    rows = [_tr('2025-11-03', puts[0], -a[0], a[1], acct='m'),
            _tr('2025-11-04', puts[1], -b[0], b[1], acct='m')]
    rows += [opts[k] for k in order]
    for n, leg in enumerate(legs, 1):
        if leg[0] == 'D':
            rows.append(_tr('2025-12-12', SYQ, leg[1], leg_px * leg[1],
                            acct='m', time=f'16:00:{n:02d}', action='ASSIGN'))
        else:
            rows.append(_tr('2025-12-12', SYQ, -leg[1], leg[2], acct='m',
                            time=f'16:00:{n:02d}'))
    return rows


def _fig(book, **kw):
    res, err = _run(book, **kw)
    return ([(round(r['cost'], 2), round(r['gain'], 2))
             for r in res['transactions'] if r.get('raw_gain') is not None],
            _inventory(res))


def _dict_rows(book):
    """The book as the views read it: dict rows with ids ('r0', 'r1',
    ...), an option's price per share and its contract size."""
    out = []
    for n, t in enumerate(book):
        d = dataclasses.asdict(t)
        d['id'] = d['_acct'] = f'r{n}'
        d['_acct'] = t.account
        if is_option_symbol(t.symbol):
            d['multiplier'] = 100
            d['price'] = (abs(t.net_amount) / abs(t.quantity) / 100
                          if t.quantity else 0.0)
        out.append(d)
    return out


class TestTwoEqualSizePutsShareEachSizedDelivery(unittest.TestCase):
    """TJ-3175: two 1-contract puts at one strike (premiums 100 and 300),
    delivered as two 100-share rows with a sale of 100 between them. Each
    row is the delivered quantity of both, so the size tells them apart no
    better than the strike: each row is half each, 50 x 1 + 50 x 3 = 200
    of premium, 1200 - 200 = 1000 sold for 1300 (a gain of 300) and 1000
    held, in either order. Before TJ-2750 the nearer row took the first
    delivery whole (900 or 1100 sold)."""

    @rule(R.CA_OPT_06, R.CA_OPT_08)
    def test_each_row_is_half_each(self):
        legs = [('D', 100), ('S', 100, 1300), ('D', 100)]
        for order in ('AB', 'BA'):
            with self.subTest(order=order):
                self.assertEqual(_fig(_two_puts(order, (1, 100), (1, 300), legs)),
                                 ([(1000.0, 300.0)], [(SYQ, 100.0, 1000.0, 0.0)]))


class TestDifferentStrikesPairAsBefore(unittest.TestCase):
    """TJ-3176: only equal strikes tie. A 10-strike put (premium 100) and
    a 12-strike put (300), 1 contract each, delivered as two marked
    100-share rows priced 11 (neither strike) with a sale between them:
    the pairing is as it was, the row nearer the first delivery claims it
    (the ruling and CA-OPT-08's tie-break do not reach different strikes).
    A's row first: B's 300 off the first row, 1100 - 300 = 800 sold for
    1300, 1000 held; B's first: 1000 sold, 800 held."""

    @rule(R.CA_OPT_06, R.CA_OPT_08)
    def test_the_nearer_row_still_takes_the_first_delivery(self):
        legs = [('D', 100), ('S', 100, 1300), ('D', 100)]
        puts = (PUT_10, PUT_B)
        self.assertEqual(_fig(_two_puts('AB', (1, 100), (1, 300), legs, puts, 11)),
                         ([(800.0, 500.0)], [(SYQ, 100.0, 1000.0, 0.0)]))
        self.assertEqual(_fig(_two_puts('BA', (1, 100), (1, 300), legs, puts, 11)),
                         ([(1000.0, 300.0)], [(SYQ, 100.0, 800.0, 0.0)]))


class TestTheDaysRowOrderNeverDecides(unittest.TestCase):
    """TJ-3178: the entry's book with the two option rows anywhere among
    the day's rows: before both deliveries, between them, after the sale
    (the sequence's order as seconds, 16:00:00 on, or every row at one
    time). The tie reads the day's gap, never a row's place among the
    day's rows, so each 150 is A's 100 and B's 50 in all 20 placements:
    1550 sold for 1900, 1550 held. The review's A, delivery, B, sale,
    delivery gave 1650 and 1450 (B's row stood after the first delivery,
    so A's claim on it outranked B's)."""

    ROWS = {'A': (PUT_A, 2, 0, 'ASSIGN'), 'B': (PUT_B, 1, 0, 'ASSIGN'),
            '1': (SYQ, 150, 1800, 'ASSIGN'), 'S': (SYQ, -150, 1900, 'BUYSELL'),
            '2': (SYQ, 150, 1800, 'ASSIGN')}

    def _book(self, seq, one_time):
        rows = [_tr('2025-11-03', PUT_A, -2, 200, acct='m'),
                _tr('2025-11-04', PUT_B, -1, 300, acct='m')]
        for i, k in enumerate(seq):
            sym, q, net, action = self.ROWS[k]
            rows.append(_tr('2025-12-12', sym, q, net, acct='m', action=action,
                            time='16:00:00' if one_time else f'16:00:{i:02d}'))
        return rows

    @rule(R.CA_OPT_06, R.CA_OPT_08)
    def test_all_twenty_placements_agree(self):
        seqs = [''.join(p) for p in itertools.permutations('AB1S2')
                if p.index('1') < p.index('S') < p.index('2')]
        self.assertEqual(len(seqs), 20)
        for one_time in (False, True):
            for seq in seqs:
                with self.subTest(seq=seq, one_time=one_time):
                    self.assertEqual(
                        _fig(self._book(seq, one_time)),
                        ([(1550.0, 350.0)], [(SYQ, 150.0, 1550.0, 0.0)]))


class TestAPartlyClaimedLegKeepsTheGuard(unittest.TestCase):
    """TJ-3179: a leg another assignment already took part of is not
    split pro rata; it is the tied ones' whole, in their order, each while
    what is left covers it (TJ-0838's guard). C, a 10-strike put (100
    shares, premium 50), and A (2 contracts, 200) and B (1 contract,
    300) at 12, all assigned at one moment; a marked 130-share row at 10,
    a sale of 130 for 1500, a marked 250-share row at 12 (the book is
    short 20 shares). C takes 100 of the 130 (its strike). A and B share
    the 250 at 2:1: 167 and 83, so A still needs 33 and B 17. Of the 30
    left of the 130, B's 17 fit and A's 33 do not: B takes 17, A none
    (pro rata would have given them 20 and 10). The sale's 130 carry C's
    50 and 17 x 3 = 51: 1300 - 101 = 1199, a gain of 301; the 250 held
    carry A's whole 200 (its last leg) and B's 249: 3000 - 449 = 2551.
    The same in the six row orders."""

    @rule(R.CA_OPT_06, R.CA_OPT_08)
    def test_the_rest_goes_whole_to_the_one_it_covers(self):
        opts = {'A': _tr('2025-12-12', PUT_A, 2, 0, acct='m', time='16:00:00',
                         action='ASSIGN'),
                'B': _tr('2025-12-12', PUT_B, 1, 0, acct='m', time='16:00:00',
                         action='ASSIGN'),
                'C': _tr('2025-12-12', PUT_10, 1, 0, acct='m', time='16:00:00',
                         action='ASSIGN')}
        for order in itertools.permutations('ABC'):
            book = ([_tr('2025-11-03', PUT_A, -2, 200, acct='m'),
                     _tr('2025-11-04', PUT_B, -1, 300, acct='m'),
                     _tr('2025-11-05', PUT_10, -1, 50, acct='m')]
                    + [opts[k] for k in order] + [
                    _tr('2025-12-12', SYQ, 130, 1300, acct='m',
                        time='16:00:01', action='ASSIGN'),
                    _tr('2025-12-12', SYQ, -130, 1500, acct='m',
                        time='16:00:02'),
                    _tr('2025-12-12', SYQ, 250, 3000, acct='m',
                        time='16:00:03', action='ASSIGN')])
            with self.subTest(order=''.join(order)):
                self.assertEqual(_fig(book),
                                 ([(1199.0, 301.0)], [(SYQ, 250.0, 2551.0, 0.0)]))


class TestTheEarlierExpiryTakesTheRemainder(unittest.TestCase):
    """TJ-3181: the remainder's tie-break among equal sizes and equal
    fractions is the earlier expiry as the engine parses it, not the
    symbol's text. B is an adjusted series (`SYQ1`, expiring 2026-02-20)
    whose symbol sorts before C's (`SYQ`, 2026-01-16): the 250's odd
    share goes to C. A: 125, B: 62, C: 63; then 75, 38, 37 of the 150.
    The pairing is read directly, with the underlying resolved to the
    shares (an adjusted root names them through the resolver)."""

    PUT_B1, PUT_C = 'SYQ1260220P00012000.TO', 'SYQ260116P00012000.TO'

    @rule(R.CA_OPT_08)
    def test_the_rank_is_by_expiry(self):
        self.assertLess(_series_rank(self.PUT_C), _series_rank(self.PUT_B1))
        self.assertLess(self.PUT_B1, self.PUT_C)          # the text order
        self.assertLess(_series_rank(self.PUT_C), _series_rank('ZZZ.TO'))
        self.assertLess(_series_rank('AAA.TO'), _series_rank('ZZZ.TO'))

    @rule(R.CA_OPT_08)
    def test_the_odd_share_goes_to_the_earlier_expiry(self):
        for order in ('ABC', 'CBA', 'BCA'):
            opts = {'A': _tr('2025-12-12', PUT_A, 2, 0, acct='m',
                             time='16:00:00', action='ASSIGN'),
                    'B': _tr('2025-12-12', self.PUT_B1, 1, 0, acct='m',
                             time='16:00:00', action='ASSIGN'),
                    'C': _tr('2025-12-12', self.PUT_C, 1, 0, acct='m',
                             time='16:00:00', action='ASSIGN')}
            legs = [_tr('2025-12-12', SYQ, 250, 3000, acct='m',
                        time='16:00:01', action='ASSIGN'),
                    _tr('2025-12-12', SYQ, 150, 1800, acct='m',
                        time='16:00:03', action='ASSIGN')]
            rows = [opts[k] for k in order] + legs
            for n, t in enumerate(rows):
                t.id = f'id{n}'
            pairs = _pair_assign_legs(rows, lambda _t: True, lambda _o: SYQ,
                                      tie_pro_rata=True)
            with self.subTest(order=order):
                self.assertEqual(
                    [pairs.qty[(opts[k].id, legs[0].id)] for k in 'ABC'],
                    [125.0, 62.0, 63.0])
                self.assertEqual(
                    [pairs.qty[(opts[k].id, legs[1].id)] for k in 'ABC'],
                    [75.0, 38.0, 37.0])


def _placements(opt_rows, share_rows, one_time=False):
    """Every placement of the option rows among the share rows (the share
    rows' order kept): {tag: rows}, the times the sequence's seconds or
    one time."""
    out = {}
    tags = [f'O{i}' for i in range(len(opt_rows))] + [
        f'S{i}' for i in range(len(share_rows))]
    for perm in itertools.permutations(tags):
        s_idx = [perm.index(f'S{i}') for i in range(len(share_rows))]
        if s_idx != sorted(s_idx):
            continue
        rows = []
        for k, tag in enumerate(perm):
            src = (opt_rows if tag[0] == 'O' else share_rows)[int(tag[1:])]
            rows.append(dataclasses.replace(
                src, time='16:00:00' if one_time else f'16:00:{k:02d}'))
        out[''.join(perm)] = rows
    return out


def _opt(sym, q):
    return _tr('2025-12-12', sym, q, 0, acct='m', time='16:00:00',
               action='ASSIGN')


def _leg(qty, net, action='ASSIGN'):
    return _tr('2025-12-12', SYQ, qty, net, acct='m', time='16:00:00',
               action=action)


class TestTiedDeliveriesSettleInTheirOwnOrder(unittest.TestCase):
    """TJ-3218: the remainder rule is applied on the tie's deliveries in
    their own order, the earliest first, whatever the option rows'
    placement. The three-way book (A 2 contracts written for 200, B 1 for
    300, C 1 for 100; marked 250, a sale for 3100, marked 150): the 250
    is settled first (A 125, B 63, C 62: 2624 sold, a gain of 476) and
    the 150 then follows (75, 37, 38: 1576 held) in all 120 placements
    of the three option rows; with the 150 settled first, wherever an
    option row stood nearer it, it was 2626 and 1574. A 3-contract put
    written for 300 and a 1-contract
    put written for 300, delivered as 150 (sold for 1900) then 250: the
    150 at 3:1 is 112.5 and 37.5, A's 113 (equal fractions: the larger
    first) and B's 37, 113 + 111 = 224 of premium, 1576 sold (a gain of
    324); the 250 then 187 and 63 (187 + 189 = 376, 2624 held), in all
    20 placements; it was 1574 sold where the 250 came first."""

    @rule(R.CA_OPT_06, R.CA_OPT_08)
    def test_the_three_way_book_in_every_placement(self):
        ws = [_tr('2025-11-03', PUT_A, -2, 200, acct='m'),
              _tr('2025-11-04', PUT_B, -1, 300, acct='m'),
              _tr('2025-11-05', PUT_C, -1, 100, acct='m')]
        o = [_opt(PUT_A, 2), _opt(PUT_B, 1), _opt(PUT_C, 1)]
        legs = [_leg(250, 3000), _leg(-250, 3100, 'BUYSELL'), _leg(150, 1800)]
        rows = _placements(o, legs)
        self.assertEqual(len(rows), 120)
        for tag, book in rows.items():
            with self.subTest(placement=tag):
                self.assertEqual(_fig(ws + book),
                                 ([(2624.0, 476.0)], [(SYQ, 150.0, 1576.0, 0.0)]))

    @rule(R.CA_OPT_06, R.CA_OPT_08)
    def test_the_three_to_one_book_in_every_placement(self):
        ws = [_tr('2025-11-03', PUT_A, -3, 300, acct='m'),
              _tr('2025-11-04', PUT_B, -1, 300, acct='m')]
        o = [_opt(PUT_A, 3), _opt(PUT_B, 1)]
        legs = [_leg(150, 1800), _leg(-150, 1900, 'BUYSELL'), _leg(250, 3000)]
        rows = _placements(o, legs)
        self.assertEqual(len(rows), 20)
        for tag, book in rows.items():
            with self.subTest(placement=tag):
                self.assertEqual(_fig(ws + book),
                                 ([(1576.0, 324.0)], [(SYQ, 250.0, 2624.0, 0.0)]))


class TestSeveralDeliveriesOfOnePeersQuantity(unittest.TestCase):
    """TJ-3220: A (2 contracts, written for 200) and B (1, for 300),
    delivered as three marked 100-share rows with a sale of 100 for 1300
    after the first. Each row is exactly B's quantity and none is A's: the
    earliest is B's (1200 - 300 = 900 sold, a gain of 400) and the other
    two A's (2400 - 200 = 2200 held), in all 30 placements of the option
    rows; it was B's nearest row by distance (900 or 1100)."""

    @rule(R.CA_OPT_06, R.CA_OPT_08)
    def test_the_earliest_sized_delivery_is_its(self):
        o = [_opt(PUT_A, 2), _opt(PUT_B, 1)]
        legs = [_leg(100, 1200), _leg(-100, 1300, 'BUYSELL'), _leg(100, 1200),
                _leg(100, 1200)]
        rows = _placements(o, legs)
        self.assertEqual(len(rows), 30)
        ws = [_tr('2025-11-03', PUT_A, -2, 200, acct='m'),
              _tr('2025-11-04', PUT_B, -1, 300, acct='m')]
        for tag, book in rows.items():
            with self.subTest(placement=tag):
                self.assertEqual(_fig(ws + book),
                                 ([(900.0, 400.0)], [(SYQ, 200.0, 2200.0, 0.0)]))


class TestAnExerciseBesideAnAssignmentPairsAsBefore(unittest.TestCase):
    """TJ-3221: only assignments of one kind are peers. A long 12-call
    exercised (bought for 150) beside a short 12-put assigned (written for
    300), two marked 100-share rows with a sale of 100 for 1300 between:
    the pairing is as it was, the row nearer the first delivery claims it.
    The call's row first: the put's 300 off the first row, 900 sold (a
    gain of 400), the call's 150 on the 100 held (1350). The put's row
    first: the call's shares sold at 1350 (a loss of 50, superficial: the
    second row is a replacement, so 50 deferred onto the 100 held at
    950 + 50) — not half each (1125) as a tie would give."""

    CALL = 'SYQ251212C00012000.TO'

    def _book(self, order):
        o = {'C': _opt(self.CALL, -1), 'P': _opt(PUT_B, 1)}
        return ([_tr('2025-11-03', self.CALL, 1, -150, acct='m'),
                 _tr('2025-11-04', PUT_B, -1, 300, acct='m')]
                + [o[k] for k in order]
                + [_tr('2025-12-12', SYQ, 100, 1200, acct='m',
                       time='16:00:01', action='ASSIGN'),
                   _tr('2025-12-12', SYQ, -100, 1300, acct='m',
                       time='16:00:02'),
                   _tr('2025-12-12', SYQ, 100, 1200, acct='m',
                       time='16:00:03', action='ASSIGN')])

    @rule(R.CA_OPT_06, R.CA_OPT_08, R.CA_OPT_09)
    def test_the_nearer_row_takes_the_first_delivery(self):
        self.assertEqual(_fig(self._book('CP')),
                         ([(900.0, 400.0)], [(SYQ, 100.0, 1350.0, 0.0)]))
        res, _ = _run(self._book('PC'))
        self.assertEqual([(round(r['cost'], 2), round(r['gain'], 2),
                           round(r.get('disallowed_amount') or 0.0, 2))
                          for r in res['transactions']
                          if r.get('raw_gain') is not None],
                         [(1350.0, 0.0, 50.0)])
        self.assertEqual(_inventory(res), [(SYQ, 100.0, 950.0, 50.0)])


class TestTheTwinsFollowTheEnginesOrder(unittest.TestCase):
    """TJ-3219: the stats twins place the rows as the engine does
    (`event_sort_key`): with every row of the day at one time, the engine
    walks the option rows first, then the deliveries, then the sale (a
    sale at the deliveries' moment comes after them). A (200) and B (100)
    as three marked 100-rows and a sale of 100 at one time: the sale
    draws 100 of the 300 pooled shares (3100 of cost: 166.67 of premium),
    and the pool's put credits and the per-sale credits say the same in
    all 30 placements. The 250/50 book at one time, A-opt, D250, B-opt,
    sale, D50: the Canadian sale is 250 of 300 (416.67), the twins agree.
    An unpaired credit (the three-way book's A, whose 200 shares no leg
    covers) drains into what a leg's own assignments leave, after them:
    the twins fold B's 100 of the first 250 before A's 150 (450), as the
    engine's ledger does."""

    def _twins(self, book, canada=True):
        rows = _dict_rows(book)
        credits = TS.written_option_trades(rows, lambda x: x['date'])['credits']
        out = {}
        if canada:
            out['pooled'] = {k[1]: round(v, 4) for k, v in
                             TS.pooled_put_credits({'m': rows}, credits).items()}
        out['by_sale'] = {k: round(v, 4) for k, v in TS.credits_by_sale(
            rows, credits, fifo=not canada).items()}
        return out

    @rule(R.CA_RPT_16, R.CA_OPT_08)
    def test_three_rows_and_a_sale_at_one_time(self):
        ws = [_tr('2025-11-03', PUT_A, -2, 200, acct='m'),
              _tr('2025-11-04', PUT_B, -1, 300, acct='m')]
        o = [_opt(PUT_A, 2), _opt(PUT_B, 1)]
        legs = [_leg(100, 1200), _leg(-100, 1300, 'BUYSELL'), _leg(100, 1200),
                _leg(100, 1200)]
        for tag, rows in _placements(o, legs, one_time=True).items():
            with self.subTest(placement=tag):
                book = ws + rows
                res, _ = _run(book)
                sale = [r for r in res['transactions']
                        if r.get('raw_gain') is not None]
                self.assertEqual(round(1200 - sale[0]['cost'], 4), 166.6667)
                sid = f'r{2 + rows.index(next(r for r in rows if r.quantity < 0))}'
                self.assertEqual(self._twins(book),
                                 {'pooled': {sid: 166.6667},
                                  'by_sale': {sid: 166.6667}})

    @rule(R.CA_RPT_16, R.CA_OPT_08)
    def test_the_250_50_book_at_one_time(self):
        book = ([_tr('2025-11-03', PUT_A, -2, 200, acct='m'),
                 _tr('2025-11-04', PUT_B, -1, 300, acct='m'),
                 _opt(PUT_A, 2), _leg(250, 3000), _opt(PUT_B, 1),
                 _leg(-250, 3100, 'BUYSELL'), _leg(50, 600)])
        res, _ = _run(book)
        sale = [r for r in res['transactions'] if r.get('raw_gain') is not None]
        self.assertEqual(round(3000 - sale[0]['cost'], 4), 416.6667)
        self.assertEqual(self._twins(book),
                         {'pooled': {'r5': 416.6667}, 'by_sale': {'r5': 416.6667}})

    @rule(R.CA_RPT_16, R.CA_OPT_08)
    def test_an_unpaired_credit_drains_after_the_legs_own(self):
        # The three-way book with C's row between the first delivery and
        # the sale: the engine and the twins agree, in either account
        # treatment, that B's claim comes before A's unpaired drain.
        ws = [_tr('2025-11-03', PUT_A, -2, 200, acct='m'),
              _tr('2025-11-04', PUT_B, -1, 300, acct='m'),
              _tr('2025-11-05', PUT_C, -1, 100, acct='m')]
        o = [_opt(PUT_A, 2), _opt(PUT_B, 1), _opt(PUT_C, 1)]
        legs = [_leg(250, 3000), _leg(-250, 3100, 'BUYSELL'), _leg(150, 1800)]
        for tag in ('O0O1S0O2S1S2', 'O0S0O1O2S1S2', 'S0O0O1O2S1S2'):
            book = ws + _placements(o, legs)[tag]
            with self.subTest(placement=tag):
                res, _ = _run(book)
                sale = [r for r in res['transactions']
                        if r.get('raw_gain') is not None]
                sid = f'r{3 + book[3:].index(next(r for r in book[3:] if r.quantity < 0))}'
                want = round(3000 - sale[0]['cost'], 4)
                self.assertEqual(self._twins(book),
                                 {'pooled': {sid: want}, 'by_sale': {sid: want}})


CALL_12 = 'SYQ251212C00012000.TO'
OTHER = 'ZZQ.TO'


class TestATieGroupCompetesAtItsNearestRow(unittest.TestCase):
    """TJ-3229: against another assignment a tie group competes for a
    delivery as any assignment does, by the nearest row (the group's row
    its nearest member's); only among its members do the deliveries
    settle in their own order. A 12-call bought for 150 and exercised
    beside written 12-puts A (1 contract, 100) and B (1 contract, 300),
    three marked 100-rows and a sale of 100 for 1300 after the first.
    The three option rows just before the deliveries: B's row is the
    nearest, so the puts take the first row (half each: 50 + 150 = 200
    of premium, 1000 sold, a gain of 300) and the second, the call the
    third (1200 + 150): 2350 held. Twelve other rows between the puts
    and the call: the call's row is the nearest, so the call takes the
    first row (1350 sold, a loss of 50 the next row makes superficial)
    and the puts the other two (2000 + 50 deferred). An untied 10-put
    (C, 1 contract, 50) beside the tied 12-puts (A 2 contracts, B 1),
    marked rows priced 11 (neither strike) of 100, 100 and 200 with a
    sale of 100 after the first: each row is exactly one assignment's
    quantity, so C takes the first 100, B the second and A the 200,
    whichever option row comes first (1050 sold, a gain of 250; 2800
    held)."""

    def _call_and_puts(self, filler):
        ws = [_tr('2025-11-03', CALL_12, 1, -150, acct='m'),
              _tr('2025-11-04', PUT_A, -1, 100, acct='m'),
              _tr('2025-11-05', PUT_B, -1, 300, acct='m')]

        def opt(sym, q, sec):
            return _tr('2025-12-12', sym, q, 0, acct='m',
                       time=f'16:00:{sec:02d}', action='ASSIGN')
        if filler:
            # The puts' rows, twelve rows of another symbol, the call's.
            rows = ([opt(PUT_A, 1, 0), opt(PUT_B, 1, 1)]
                    + [_tr('2025-12-12', OTHER, 10, 100, acct='m',
                           time=f'16:00:{k:02d}') for k in range(1, 13)]
                    + [opt(CALL_12, -1, 13)])
        else:
            rows = [opt(CALL_12, -1, 0), opt(PUT_A, 1, 1), opt(PUT_B, 1, 2)]
        return ws + rows + [
            _tr('2025-12-12', SYQ, 100, 1200, acct='m', time='16:00:15',
                action='ASSIGN'),
            _tr('2025-12-12', SYQ, -100, 1300, acct='m', time='16:00:16'),
            _tr('2025-12-12', SYQ, 100, 1200, acct='m', time='16:00:17',
                action='ASSIGN'),
            _tr('2025-12-12', SYQ, 100, 1200, acct='m', time='16:00:18',
                action='ASSIGN')]

    @rule(R.CA_OPT_06, R.CA_OPT_08, R.CA_OPT_09)
    def test_the_puts_nearer_take_the_first_delivery(self):
        self.assertEqual(_fig(self._call_and_puts(filler=False)),
                         ([(1000.0, 300.0)], [(SYQ, 200.0, 2350.0, 0.0)]))

    @rule(R.CA_OPT_06, R.CA_OPT_08, R.CA_OPT_09)
    def test_the_call_nearer_takes_it(self):
        res, _ = _run(self._call_and_puts(filler=True))
        self.assertEqual([(round(r['cost'], 2), round(r['gain'], 2),
                           round(r.get('disallowed_amount') or 0.0, 2))
                          for r in res['transactions']
                          if r.get('raw_gain') is not None],
                         [(1350.0, 0.0, 50.0)])
        self.assertEqual(_inventory(res), [(OTHER, 120.0, 1200.0, 0.0),
                                           (SYQ, 200.0, 2050.0, 50.0)])

    @rule(R.CA_OPT_06, R.CA_OPT_08)
    def test_an_untied_put_of_another_strike_off_strike(self):
        ws = [_tr('2025-11-03', PUT_A, -2, 200, acct='m'),
              _tr('2025-11-04', PUT_B, -1, 300, acct='m'),
              _tr('2025-11-05', PUT_10, -1, 50, acct='m')]
        o = {'A': _tr('2025-12-12', PUT_A, 2, 0, acct='m', time='16:00:00',
                      action='ASSIGN'),
             'B': _tr('2025-12-12', PUT_B, 1, 0, acct='m', time='16:00:01',
                      action='ASSIGN'),
             'C': _tr('2025-12-12', PUT_10, 1, 0, acct='m', time='16:00:02',
                      action='ASSIGN')}
        legs = [_tr('2025-12-12', SYQ, 100, 1100, acct='m', time='16:00:03',
                    action='ASSIGN'),
                _tr('2025-12-12', SYQ, -100, 1300, acct='m', time='16:00:04'),
                _tr('2025-12-12', SYQ, 100, 1100, acct='m', time='16:00:05',
                    action='ASSIGN'),
                _tr('2025-12-12', SYQ, 200, 2200, acct='m', time='16:00:06',
                    action='ASSIGN')]
        for order in ('CAB', 'ABC', 'BCA'):
            with self.subTest(order=order):
                self.assertEqual(_fig(ws + [o[k] for k in order] + legs),
                                 ([(1050.0, 250.0)], [(SYQ, 300.0, 2800.0, 0.0)]))


class TestAnAbsentPeerStillTakesItsShares(unittest.TestCase):
    """TJ-3231: A written for 300 in the books, B's write outside them
    (only its ASSIGN row); marked 100, a sale of 100 for 1300, marked 100.
    The engine ties A and B (peers among the assignment rows): each row
    is half each, the sale carries A's 150 (1050 sold, a gain of 250),
    and the twins say 150 too, counting peers among the books' rows and
    pairing B as a member with no premium."""

    def _book(self, order):
        o = {'A': _opt(PUT_A, 1), 'B': _opt(PUT_B, 1)}
        return ([_tr('2025-11-03', PUT_A, -1, 300, acct='m')]
                + [o[k] for k in order]
                + [_tr('2025-12-12', SYQ, 100, 1200, acct='m', time='16:00:01',
                       action='ASSIGN'),
                   _tr('2025-12-12', SYQ, -100, 1300, acct='m',
                       time='16:00:02'),
                   _tr('2025-12-12', SYQ, 100, 1200, acct='m', time='16:00:03',
                       action='ASSIGN')])

    @rule(R.CA_RPT_16, R.CA_OPT_08)
    def test_half_each_and_the_twins_agree(self):
        for order in ('AB', 'BA'):
            with self.subTest(order=order):
                book = self._book(order)
                sales, inv = _fig(book)
                self.assertEqual(sales, [(1050.0, 250.0)])
                self.assertIn((SYQ, 100.0, 1050.0, 0.0), inv)
                rows = _dict_rows(book)
                credits = TS.written_option_trades(
                    rows, lambda x: x['date'])['credits']
                self.assertEqual({k[1]: round(v, 4) for k, v in
                                  TS.pooled_put_credits({'m': rows}, credits).items()},
                                 {'r4': 150.0})
                self.assertEqual({k: round(v, 4) for k, v in TS.credits_by_sale(
                    rows, credits, fifo=False).items()}, {'r4': 150.0})


class TestTheLaterDayComesBeforeTheEarlier(unittest.TestCase):
    """TJ-3232: among peers the deliveries settle in their own order: the
    nearest day first, a later day before an earlier one, then the day's
    own order. A (3 contracts written for 300) and B (1 contract, 300)
    assigned 12-12; a marked 150 on 12-11, a sale of 150 for 1900 on
    12-13, a marked 250 on 12-13: the 250 (the day after) is settled
    first, 188 and 62 (equal halves: the larger first), then the 150 (the
    day before), 112 and 38: 112 + 114 = 226 of premium on the sold 150
    (1574, a gain of 326) and 188 + 186 = 374 on the 250 held (2626).
    With A of 2 contracts (200) and B of 1 (300), B's quantity delivered
    on 12-11 and 12-13 and A's 200 on 12-12: B's is the 12-13 row, A's
    the 12-12, and the 12-11 row is no one's (1200 held at cost): the 100
    sold on 12-13 come out of a 300-share pool at 3400 (1133.33, a gain
    of 166.67), 3166.67 held."""

    @rule(R.CA_OPT_06, R.CA_OPT_08)
    def test_the_day_after_is_settled_first(self):
        book = [_tr('2025-11-03', PUT_A, -3, 300, acct='m'),
                _tr('2025-11-04', PUT_B, -1, 300, acct='m'),
                _tr('2025-12-11', SYQ, 150, 1800, acct='m', time='16:00:01',
                    action='ASSIGN'),
                _opt(PUT_A, 3),
                _tr('2025-12-12', PUT_B, 1, 0, acct='m', time='16:00:01',
                    action='ASSIGN'),
                _tr('2025-12-13', SYQ, -150, 1900, acct='m', time='10:00:00'),
                _tr('2025-12-13', SYQ, 250, 3000, acct='m', time='16:00:01',
                    action='ASSIGN')]
        self.assertEqual(_fig(book),
                         ([(1574.0, 326.0)], [(SYQ, 250.0, 2626.0, 0.0)]))

    @rule(R.CA_OPT_06, R.CA_OPT_08)
    def test_of_two_sized_rows_the_day_after_is_its(self):
        book = [_tr('2025-11-03', PUT_A, -2, 200, acct='m'),
                _tr('2025-11-04', PUT_B, -1, 300, acct='m'),
                _tr('2025-12-11', SYQ, 100, 1200, acct='m', time='16:00:01',
                    action='ASSIGN'),
                _opt(PUT_A, 2),
                _tr('2025-12-12', PUT_B, 1, 0, acct='m', time='16:00:01',
                    action='ASSIGN'),
                _tr('2025-12-12', SYQ, 200, 2400, acct='m', time='16:00:02',
                    action='ASSIGN'),
                _tr('2025-12-13', SYQ, -100, 1300, acct='m', time='10:00:00'),
                _tr('2025-12-13', SYQ, 100, 1200, acct='m', time='16:00:01',
                    action='ASSIGN')]
        self.assertEqual(_fig(book),
                         ([(1133.33, 166.67)], [(SYQ, 300.0, 3166.67, 0.0)]))


class TestARowWithNoTimeSortsFirst(unittest.TestCase):
    """TJ-3233: a put's marked 100 delivery with no time, a sale of 100 for
    1300 at 09:00:00 the same day, a purchase at 10:00:00. The engine
    sorts the row with no time first at its date, so the sale sells the
    delivery (1200 - 100 = 1100, a gain of 200); the twins, keeping the
    row's own time, say 100 on the sale too (a 09:30:00 default put the
    delivery after the sale and the premium on nothing)."""

    @rule(R.CA_RPT_16, R.CA_OPT_08)
    def test_the_twins_sort_it_first_too(self):
        book = [_tr('2025-11-03', PUT_A, -1, 100, acct='m'),
                _tr('2025-12-12', PUT_A, 1, 0, acct='m', time='',
                    action='ASSIGN'),
                _tr('2025-12-12', SYQ, 100, 1200, acct='m', time='',
                    action='ASSIGN'),
                _tr('2025-12-12', SYQ, -100, 1300, acct='m', time='09:00:00'),
                _tr('2025-12-12', SYQ, 100, 1200, acct='m', time='10:00:00')]
        self.assertEqual(_fig(book),
                         ([(1100.0, 200.0)], [(SYQ, 100.0, 1200.0, 0.0)]))
        rows = _dict_rows(book)
        credits = TS.written_option_trades(rows, lambda x: x['date'])['credits']
        self.assertEqual({k[1]: round(v, 4) for k, v in
                          TS.pooled_put_credits({'m': rows}, credits).items()},
                         {'r3': 100.0})
        self.assertEqual({k: round(v, 4) for k, v in TS.credits_by_sale(
            rows, credits, fifo=False).items()}, {'r3': 100.0})


class TestASplitInTheDeliverysSettlementLag(unittest.TestCase):
    """TJ-3234: a put (1 contract written for 100) assigned 12-12, its
    marked 100-share delivery traded 12-12 and settling 12-16, a 2:1
    split on 12-15, the 200 post-split shares sold 12-17 for 2600. The
    engine re-denominates the delivery through the split (200 shares at
    1200 less the premium: 1100 sold, a gain of 1500). The Canadian order
    copies that row; the twins find the copy's place by its source row
    and say 100 on the sale."""

    @rule(R.CA_RPT_16, R.CA_OPT_08, R.CA_CORP_01)
    def test_the_twins_find_the_copied_row(self):
        def tx(action, date, settle, time, sym, q, net):
            return TaxTransaction(action=action, date=date, date_settle=settle,
                                  time=time, symbol=sym, quantity=float(q),
                                  price=abs(net / q) if q else 0.0,
                                  net_amount=float(net), currency='CAD',
                                  account='m')
        book = [tx('BUYSELL', '2025-11-03', '2025-11-04', '10:00:00', PUT_A,
                   -1, 100),
                tx('ASSIGN', '2025-12-12', '2025-12-12', '16:00:00', PUT_A, 1, 0),
                tx('ASSIGN', '2025-12-12', '2025-12-16', '16:00:01', SYQ, 100,
                   1200),
                TaxTransaction(action='SPLIT', date='2025-12-15',
                               date_settle='2025-12-15', time='09:00:00',
                               symbol=SYQ, symbol_new='', quantity=2.0,
                               net_amount=0.0, currency='CAD', account='m'),
                tx('BUYSELL', '2025-12-17', '2025-12-18', '10:00:00', SYQ,
                   -200, 2600)]
        self.assertEqual(_fig(book), ([(1100.0, 1500.0)], []))
        rows = _dict_rows(book)
        credits = TS.written_option_trades(rows, lambda x: x['date'])['credits']
        self.assertEqual({k[1]: round(v, 4) for k, v in
                          TS.pooled_put_credits({'m': rows}, credits).items()},
                         {'r4': 100.0})
        self.assertEqual({k: round(v, 4) for k, v in TS.credits_by_sale(
            rows, credits, fifo=False).items()}, {'r4': 100.0})


class TestTheStatsAndRenameViewsAgreeWithTheEngine(unittest.TestCase):
    """TJ-3177: the stats views (lib/trade_stats: the pool's put credits
    and the per-sale credits) and the rename view's book cost
    (lib/renames `_assign_costs`) pair through the engine's own allocator
    and read each assignment's claim of a shared leg, so on the tied books
    they say what the gains report says, in either row order: 250 of
    premium on the sale of the first 150 (and 250 on the 150 held); 416
    on the 250 sold and 84 on the 50 held."""

    def _premium_on_sale(self, book, sold):
        res, _ = _run(book)
        sale = [r for r in res['transactions'] if r.get('raw_gain') is not None]
        self.assertEqual(len(sale), 1)
        return round(12 * sold - sale[0]['cost'], 4)

    @rule(R.CA_RPT_16, R.CA_OPT_08)
    def test_the_stats_views_fold_each_claim(self):
        for a_first, args, want in ((True, (150, 150, 1900, 150), 250.0),
                                    (False, (150, 150, 1900, 150), 250.0),
                                    (True, (250, 250, 3100, 50), 416.0),
                                    (False, (250, 250, 3100, 50), 416.0)):
            with self.subTest(a_first=a_first, legs=args):
                book = _assigned(a_first, *args)
                self.assertEqual(self._premium_on_sale(book, args[1]), want)
                rows = _dict_rows(book)
                credits = TS.written_option_trades(
                    rows, lambda x: x['date'])['credits']
                pooled = TS.pooled_put_credits({'m': rows}, credits)
                self.assertEqual({k: round(v, 4) for k, v in pooled.items()},
                                 {('m', 'r5'): want})
                by_sale = TS.credits_by_sale(rows, credits, fifo=False)
                self.assertEqual({k: round(v, 4) for k, v in by_sale.items()},
                                 {'r5': want})

    @rule(R.CA_ACB_RENAME, R.CA_OPT_08)
    def test_the_rename_view_splits_by_the_claims(self):
        for a_first, args, want in ((True, (150, 150, 1900, 150), (-250.0, -250.0)),
                                    (False, (150, 150, 1900, 150), (-250.0, -250.0)),
                                    (True, (250, 250, 3100, 50), (-416.0, -84.0)),
                                    (False, (250, 250, 3100, 50), (-416.0, -84.0))):
            with self.subTest(a_first=a_first, legs=args):
                rows = _dict_rows(_assigned(a_first, *args))
                out = _assign_costs(rows, 'canada')
                got = {r['id']: round(out[id(r)], 4) for r in rows
                       if id(r) in out}
                self.assertEqual(got, {'r4': want[0], 'r6': want[1]})


if __name__ == '__main__':
    unittest.main()
