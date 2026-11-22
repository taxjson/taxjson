"""Transfer branches the mutation run of 2026-11-09 executed but no test
checked (TJ-1896): the Canadian engine's own-move view of the still-held
test (`core._own_move_held`), and the pipeline's pairing, netting and
dropping of transfer rows (`_drop_self_cancelling_transfers`,
`_net_cross_account_transfers`, `_handle_transfers`). Each test pins a
figure (a denied loss, a row kept or dropped) a mutant of the branch
changes. Synthetic data only; tickers and accounts are made up."""
import contextlib
import io
import unittest

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.pipeline import (TransferValidationError,
                                  _drop_self_cancelling_transfers,
                                  _handle_transfers,
                                  _net_cross_account_transfers)
from taxjson.lib.row_types import OWN_MOVE_TYPE
from tax_rules import rule
from taxjson.rules import ids as R


def _tx(action, date, qty, net, *, sym='SYQ.TO', account='b', typ='',
        time='10:00:00'):
    return TaxTransaction(action=action, date=date, date_settle=date,
                          time=time, symbol=sym, quantity=qty,
                          price=abs(net / qty) if qty else 0.0,
                          net_amount=net, currency='CAD', account=account,
                          type=typ)


def _trade(date, qty, net, **kw):
    return _tx('BUYSELL', date, qty, net, **kw)


def _xfer(date, qty, *, sym='SYQ.TO', account='rrsp', time='10:00:00'):
    return _tx('TRANSFER', date, qty, 0.0, sym=sym, account=account,
               time=time)


def _split(date, sym='SYQ.TO', account='b'):
    return TaxTransaction(action='SPLIT', date=date, date_settle=date,
                          time='00:00:01', symbol=sym, quantity=2.0,
                          net_amount=0.0, currency='CAD', account=account)


def _move(date, qty, account):
    """A leg of a move between two of your registered accounts, as the
    pipeline hands it to the engine (balance-only, OWN_MOVE_TYPE)."""
    return _tx('TRANSFER', date, qty, 0.0, account=account,
               typ=OWN_MOVE_TYPE)


# ------------------------------------------------------- core._own_move_held

def _denied(sheltered):
    """Canada: 100 SYQ.TO bought in taxable 'a' in 2024 for 1000 and sold
    on 2025-01-20 for 600 (a 400 loss; the window runs 2024-12-21 to
    2025-02-19), with the registered accounts' rows `sheltered`. Returns
    (denied, denied for good)."""
    main = [_trade('2024-01-08', 100, -1000.0, account='a'),
            _trade('2025-01-20', -100, 600.0, account='a')]
    with contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        res = CanadaTaxRules().compute_gains(
            main, sheltered_transactions=list(sheltered))
    (sale,) = [r for r in res['transactions'] if r.get('qty')]
    return (round(sale.get('disallowed_amount') or 0.0, 2),
            round(sale.get('permanently_disallowed') or 0.0, 2))


_R1_BUY = _trade('2025-01-10', 100, -500.0, account='r1')


class TestOwnMoveWalkEndsOnDay30(unittest.TestCase):
    """`_own_move_held`, the walk's rows: those dated by day 30 (on it
    too) and not gone by its end. r1 buys the replacement in the window
    and moves it to r2; r2's sale decides whether it is held at the end
    of day 30 (s.54)."""

    def _book(self, sale_date):
        return [_R1_BUY, _move('2025-01-15', -100, 'r1'),
                _move('2025-01-15', 100, 'r2'),
                _trade(sale_date, -100, 600.0, account='r2')]

    @rule(R.CA_SL_02, R.CA_SL_08)
    def test_a_sale_on_day_30_leaves_nothing_held(self):
        self.assertEqual(_denied(self._book('2025-02-19')), (0.0, 0.0))

    @rule(R.CA_SL_02, R.CA_SL_08)
    def test_a_sale_after_day_30_does_not_count(self):
        # Sold on day 31: held at the end of day 30, denied for good.
        self.assertEqual(_denied(self._book('2025-02-20')), (400.0, 400.0))
        self.assertEqual(_denied(self._book('2025-03-01')), (400.0, 400.0))


class TestOwnMoveLegsPairTheSameUnitsFirst(unittest.TestCase):
    """`_own_move_held`, the legs' pairing: a leg pairs first with the
    other account's leg of the same units (within 1e-9 of them, scaled
    by their size), only then by the closest date. r1's 100 replacement
    units go to r2; r3's 50 older units go to r4, whose in-leg is dated
    nearer r1's out-leg, and r4 sells 50 before day 30. Paired by date,
    half the replacement would land in r4 and be sold: 200 denied, not
    400."""

    def _book(self, r1_out, r2_in, r3_out, r4_in):
        return [_trade('2024-03-01', -r3_out, -450.0, account='r3'),
                _R1_BUY,
                _move('2025-01-15', r1_out, 'r1'),
                _move('2025-01-17', r2_in, 'r2'),
                _move('2025-01-16', r3_out, 'r3'),
                _move('2025-01-15', r4_in, 'r4'),
                _trade('2025-02-01', -50, 300.0, account='r4')]

    @rule(R.CA_SL_08)
    def test_the_replacement_stays_with_its_own_units(self):
        self.assertEqual(_denied(self._book(-100, 100, -50, 50)),
                         (400.0, 400.0))

    @rule(R.CA_SL_08)
    def test_legs_a_hundred_millionth_apart_are_the_same_units(self):
        # Quantities at the eighth decimal: 1e-8 apart, inside the
        # 1e-9-per-unit tolerance of a 50- or 100-unit leg.
        self.assertEqual(
            _denied(self._book(-100, 100.00000001, -50.00000001, 50)),
            (400.0, 400.0))


class TestOwnMoveLegsPairWithin45Days(unittest.TestCase):
    """`_own_move_held`, the closest-date pairing: an out leg pairs with
    an in leg of other units up to 45 days away (the span a netted
    segment may have), the 45th day too. r1 buys 100 on 2024-12-22 and
    moves them out the next day; 10 land in r4 on 2025-01-20 and 90 in
    r2 on 2025-02-06 (45 days on), all held at day 30."""

    @rule(R.CA_SL_08)
    def test_the_leg_45_days_on_still_carries_the_replacement(self):
        self.assertEqual(_denied([
            _trade('2024-12-22', 100, -500.0, account='r1'),
            _move('2024-12-23', -100, 'r1'),
            _move('2025-01-20', 10, 'r4'),
            _move('2025-02-06', 90, 'r2')]), (400.0, 400.0))


class TestOwnMoveSaleTakesOlderUnitsFirst(unittest.TestCase):
    """`_own_move_held`, a sale's units: the holder's older units first
    (FIFO), its window acquisitions only past them. r1 held 100 from
    2024, bought 100 in the window and sold 100: the 100 it still holds
    at day 30 back the denial. A move of r3's older units to r4 in the
    window brings the walk in; without it the figure is the same."""

    @rule(R.CA_SL_08)
    def test_the_sale_takes_the_older_units(self):
        book = [_trade('2024-03-01', 100, -900.0, account='r1'), _R1_BUY,
                _trade('2025-01-12', -100, 520.0, account='r1')]
        self.assertEqual(_denied(book), (400.0, 400.0))
        self.assertEqual(_denied(book + [
            _trade('2024-03-01', 50, -450.0, account='r3'),
            _move('2025-01-15', -50, 'r3'),
            _move('2025-01-15', 50, 'r4')]), (400.0, 400.0))


# ------------------------------- pipeline._drop_self_cancelling_transfers

def _kept(rows):
    return [(t.symbol, t.date, t.quantity) for t in rows
            if t.action == 'TRANSFER']


class TestATradeOnALegsDateKeepsItsWholeSegment(unittest.TestCase):
    """`_drop_self_cancelling_transfers`' segments (TJ-0360): a trade of
    the symbol in the account dated STRICTLY between two rows ends a
    segment; one on a row's own date does not, so it stays in the
    segment's span and blocks the whole segment. A journal out and back
    (2025-03-10 / 03-12) and a move on 03-15 with a buy that day are one
    segment: nothing nets, and the taxable book's rows stop the run."""

    @rule(R.CA_ACB_10)
    def test_the_journal_before_it_is_kept_too(self):
        rows = [_xfer('2025-03-10', -100, account='a'),
                _xfer('2025-03-12', 100, account='a'),
                _xfer('2025-03-15', -50, account='a', time='09:00:00'),
                _trade('2025-03-15', 5, -50.0, account='a'),
                _xfer('2025-03-15', 50, account='a', time='11:00:00')]
        out, dropped = _drop_self_cancelling_transfers(rows)
        self.assertEqual(dropped, [])
        self.assertEqual(len(_kept(out)), 4)


def _restated(syms, extra=(), main_extra=()):
    """The strict policy's sheltered invocation (near-trade guard on):
    in registered account 'rrsp', each symbol of `syms` ({symbol: ((date,
    time) of the out leg, (date, time) of the in leg)}) journals 100 units
    out and back, with a taxable sale of the symbol on 2025-06-10, inside
    the guard's 30 days; `extra` / `main_extra` join the two books.
    Returns the symbols whose rows were dropped. Three symbols in one
    event (an account-wide restatement) net; fewer are kept."""
    shel, main = [], []
    for sym, ((d1, t1), (d2, t2)) in syms.items():
        shel += [_xfer(d1, -100, sym=sym, time=t1),
                 _xfer(d2, 100, sym=sym, time=t2)]
        main.append(_trade('2025-06-10', -100, 800.0, sym=sym))
    shel += list(extra)
    main += list(main_extra)
    with contextlib.redirect_stderr(io.StringIO()):
        _out, dropped = _drop_self_cancelling_transfers(
            shel, main_transactions=main)
    return sorted(s for s, _a, _n in dropped)


_A = (('2025-06-15', '09:00:00'), ('2025-06-16', '09:00:00'))
_THREE = {'QQA.TO': _A, 'QQB.TO': _A, 'QQC.TO': _A}


class TestRestatementSegmentsQualifyOutFirst(unittest.TestCase):
    """`_drop_self_cancelling_transfers`' restatement pre-pass, the leg
    order: a segment counts toward the event only when its first leg in
    (date, time) order is an OUT leg (a restatement journals out and
    back; an in-kind contribution then a withdrawal is in-first). A
    zero-quantity row first is no out leg."""

    @rule(R.CA_SL_17)
    def test_three_out_first_symbols_net(self):
        self.assertEqual(_restated(_THREE), sorted(_THREE))

    @rule(R.CA_SL_17)
    def test_an_in_first_segment_is_not_the_third_symbol(self):
        # QQC's in-leg is a day earlier, its out-leg earlier in its day.
        syms = dict(_THREE, **{'QQC.TO': (('2025-06-16', '09:00:00'),
                                          ('2025-06-15', '15:00:00'))})
        self.assertEqual(_restated(syms), [])

    @rule(R.CA_SL_17)
    def test_a_zero_row_first_is_not_an_out_leg(self):
        self.assertEqual(_restated(_THREE, extra=[
            _xfer('2025-06-15', 0.0, sym='QQC.TO', time='08:00:00')]), [])


class TestRestatementSplitInSpanIsSymbolScoped(unittest.TestCase):
    """`_drop_self_cancelling_transfers`' restatement pre-pass, the SPLIT
    discriminators: a SPLIT of the segment's OWN symbol inside its span
    (in the registered book, or in the main book) keeps the segment out
    of the event; another symbol's SPLIT there does not."""

    @rule(R.CA_SL_17)
    def test_a_split_of_the_symbol_in_the_book_keeps_it_out(self):
        self.assertEqual(_restated(_THREE, extra=[
            _split('2025-06-16', 'QQC.TO', 'rrsp')]), [])

    @rule(R.CA_SL_17)
    def test_another_symbols_split_changes_nothing(self):
        self.assertEqual(_restated(_THREE, extra=[
            _split('2025-06-15', 'QQZ.TO', 'rrsp')]), sorted(_THREE))
        self.assertEqual(_restated(_THREE, main_extra=[
            _split('2025-06-15', 'QQZ.TO')]), sorted(_THREE))


class TestRestatementEventSpanCap(unittest.TestCase):
    """`_drop_self_cancelling_transfers`' restatement pre-pass, the
    event cap: segments chain within 3 days of each other into one event
    of at most 7 days (the 7th day too). QQA 06-15..16, QQB 06-17..19 and
    QQC 06-19..22 span exactly 7 days: one event, three symbols."""

    @rule(R.CA_SL_17)
    def test_an_event_of_seven_days_nets(self):
        syms = {'QQA.TO': _A,
                'QQB.TO': (('2025-06-17', '09:00:00'),
                           ('2025-06-19', '09:00:00')),
                'QQC.TO': (('2025-06-19', '10:00:00'),
                           ('2025-06-22', '09:00:00'))}
        self.assertEqual(_restated(syms), sorted(syms))


class TestRestatementCandidatesSpan45DaysAtMost(unittest.TestCase):
    """`_drop_self_cancelling_transfers`' restatement pre-pass, the
    candidates' span: a segment is a candidate when it spans 45 days at
    most (the drop loop's own limit). The events are built from the
    candidates in start order, and one that would stretch an event past
    7 days is skipped, the chain going on with the next (the owner's
    decision on backlog TJ-2725, 2026-11-11: skip the segment rather than
    end the chain). QQD's segment starts on 06-16, between QQA/QQB
    (06-15) and QQC (06-17): over 45 days it is no candidate, and the
    three net as one event; at 45 days it is one, is skipped, and the
    three still net as one event (before the decision it ended the event
    after two symbols, and all three were kept), while QQD's own
    segment, near no taxable trade, nets."""

    def _qqd(self, last):
        return [_xfer('2025-06-16', -100, sym='QQD.TO'),
                _xfer('2025-07-11', 50, sym='QQD.TO'),
                _xfer(last, 50, sym='QQD.TO')]

    def _syms(self):
        return {'QQA.TO': _A, 'QQB.TO': _A,
                'QQC.TO': (('2025-06-17', '09:00:00'),
                           ('2025-06-18', '09:00:00'))}

    @rule(R.CA_SL_17)
    def test_a_46_day_segment_is_no_candidate(self):
        self.assertEqual(_restated(self._syms(),
                                   extra=self._qqd('2025-08-01')),
                         ['QQA.TO', 'QQB.TO', 'QQC.TO'])

    @rule(R.CA_SL_17)
    def test_a_45_day_segment_is_one(self):
        self.assertEqual(_restated(self._syms(),
                                   extra=self._qqd('2025-07-31')),
                         ['QQA.TO', 'QQB.TO', 'QQC.TO', 'QQD.TO'])

    @rule(R.CA_SL_17)
    def test_a_long_segment_first_is_skipped_too(self):
        # QQD starts on 06-14, before the three: it is an event of its
        # own (one symbol), and the three chain on the next round.
        qqd = [_xfer('2025-06-14', -100, sym='QQD.TO'),
               _xfer('2025-07-11', 50, sym='QQD.TO'),
               _xfer('2025-07-29', 50, sym='QQD.TO')]
        self.assertEqual(_restated(self._syms(), extra=qqd),
                         ['QQA.TO', 'QQB.TO', 'QQC.TO', 'QQD.TO'])

    @rule(R.CA_SL_17)
    def test_a_skipped_segment_opens_the_next_event(self):
        # QQA, QQB, QQC out and back 06-01 to 06-02; QQD 06-05 to 06-10
        # (skipped by the first event: it would span 9 days); QQE and QQF
        # 06-09 to 06-10, past the first event's pad. The skipped QQD goes
        # back to the pool and opens the second event, which QQE and QQF
        # join: both events net (backlog TJ-3426: chained only among the
        # skipped, QQD stood alone and QQE, QQF were two symbols).
        def legs(a, b):
            return ((a, '09:00:00'), (b, '09:00:00'))
        syms = {'QQA.TO': legs('2025-06-01', '2025-06-02'),
                'QQB.TO': legs('2025-06-01', '2025-06-02'),
                'QQC.TO': legs('2025-06-01', '2025-06-02'),
                'QQD.TO': legs('2025-06-05', '2025-06-10'),
                'QQE.TO': legs('2025-06-09', '2025-06-10'),
                'QQF.TO': legs('2025-06-09', '2025-06-10')}
        self.assertEqual(_restated(syms), sorted(syms))

    @rule(R.CA_SL_17)
    def test_a_skipped_segment_is_no_third_symbol(self):
        # Without QQC, QQA and QQB are two symbols: the skipped QQD does
        # not make them three, and they are kept for the guard.
        syms = {'QQA.TO': _A, 'QQB.TO': _A}
        self.assertEqual(_restated(syms, extra=self._qqd('2025-07-31')),
                         ['QQD.TO'])


class TestMainBookSplitOnALegsDateBlocks(unittest.TestCase):
    """`_drop_self_cancelling_transfers`, the sheltered invocation's
    main-book SPLIT check (both policies; the default one here, CA-SL-16):
    a main-book SPLIT of the symbol inside the segment's span, on a leg's
    own date, keeps the legs (they may be in different share terms); a
    SPLIT of another symbol, or a main-book trade of the symbol, does
    not (the default policy nets a custody move whatever trades sit
    near it)."""

    def _kept(self, main):
        shel = [_xfer('2025-06-15', -100), _xfer('2025-06-16', 100)]
        with contextlib.redirect_stderr(io.StringIO()):
            out, _ = _drop_self_cancelling_transfers(
                shel, main_transactions=main, near_trade_guard=False)
        return len(_kept(out))

    @rule(R.CA_SL_16)
    def test_a_split_of_the_symbol_keeps_the_legs(self):
        self.assertEqual(self._kept([_split('2025-06-16')]), 2)

    @rule(R.CA_SL_16)
    def test_another_symbols_split_or_a_trade_does_not(self):
        self.assertEqual(self._kept([_split('2025-06-16', 'QQZ.TO')]), 0)
        self.assertEqual(self._kept([_trade('2025-06-16', -10, 80.0)]), 0)


# --------------------------------- pipeline._net_cross_account_transfers

class TestCrossAccountMovesPairTwoAccounts(unittest.TestCase):
    """`_net_cross_account_transfers`, a segment that does not net whole
    (`_moves`): a move is a leg out of one account and a leg of the same
    units into ANOTHER, opposite signs. A same-account pair the
    per-account dropper kept (a trade on a leg's date) is no such move,
    and a zero-quantity row pairs with nothing."""

    def _net(self, rows):
        moved = []
        out = _net_cross_account_transfers(rows, netted_out=moved,
                                           near_trade_guard=False)
        return len(out), len(moved)

    @rule(R.CA_SL_16)
    def test_a_same_account_pair_is_no_move(self):
        self.assertEqual(self._net([
            _xfer('2025-06-10', -100, account='rrsp'),
            _xfer('2025-06-11', 100, account='rrsp'),
            _xfer('2025-06-20', 30, account='tfsa')]), (3, 0))

    @rule(R.CA_SL_16)
    def test_a_zero_row_pairs_with_nothing(self):
        self.assertEqual(self._net([
            _xfer('2025-06-10', -100, account='r1'),
            _xfer('2025-06-11', 60, account='r2'),
            _xfer('2025-06-12', 0.0, account='r3')]), (3, 0))


# ------------------------------------------- pipeline._handle_transfers

class TestTaxableTransferRefusalNamesItsRows(unittest.TestCase):
    """`_handle_transfers`, a taxable book's TRANSFER rows (CA-ACB-10:
    they stop the run): the refusal lists the first five TRANSFER rows
    (no other row), counts the rest, and names the accounts of the
    TRANSFER rows only."""

    def _error(self, rows):
        with self.assertRaises(TransferValidationError) as cm:
            with contextlib.redirect_stderr(io.StringIO()):
                _handle_transfers(rows, [], taxable=True)
        msg = str(cm.exception)
        listed = [ln.split()[0] for ln in msg.splitlines()
                  if ln.startswith('  20')]
        return msg.splitlines()[0], listed, msg

    @rule(R.CA_ACB_10)
    def test_only_transfer_rows_are_listed_and_named(self):
        head, listed, _ = self._error([
            _trade('2025-03-01', 10, -100.0, account='b'),
            _xfer('2025-03-03', 100, account='a')])
        self.assertTrue(head.startswith('taxable account a: '), head)
        self.assertEqual(listed, ['2025-03-03'])

    @rule(R.CA_ACB_10)
    def test_five_are_listed_the_rest_counted(self):
        rows = [_xfer(f'2025-03-0{i}', 10, sym=f'QQ{i}.TO', account='a')
                for i in range(1, 8)]
        _head, listed, msg = self._error(rows)
        self.assertEqual(listed, [f'2025-03-0{i}' for i in range(1, 6)])
        self.assertIn('(+2 more)', msg)


if __name__ == '__main__':
    unittest.main()
