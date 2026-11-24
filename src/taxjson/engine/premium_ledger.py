"""The premiums an option assignment stages for its own stock legs.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib.stage_msg import emit_line
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple, TypeAlias

from taxjson.model import money as M
from taxjson.lib.option_symbols import (
    exercise_target, is_option_symbol, parse_option_strike, parse_option_underlying,
)
from taxjson.lib.row_types import is_opening_row
from taxjson.model.evidence import (UNKNOWN_OPENING_LEG, cut_leg_action, cut_leg_symbol,
                                    parse_cut_leg, split_cut_legs)

from taxjson.engine.assign import (
    _MARKED_LEG_MAX_LAG_DAYS, _ASSIGN_LEG_LEAD_DAYS, _assign_delivery_shares, _assign_direction,
    _day_gap,
)
from taxjson.engine.consts import _ZERO, _TWO_PERCENT

if TYPE_CHECKING:
    from decimal import Decimal

    from taxjson.model.transaction import TaxTransaction
    from taxjson.engine.rows import RowId

# An (account, symbol): a stock line in one account, the key the ledger
# stages an assignment's premium under (the symbol its underlying).
_LegKey: TypeAlias = Tuple[str, str]
# One staged premium (`stage`): src (the option row's id), amt and dw
# (Decimal: the amount and its deferral part), tainted, sources and
# chunks (lists of dicts), sym, und, dir (+1, -1 or None), shares (None:
# unknown), contracts, date, legs (its paired legs' ids); took and
# split_said once a same-day delivery shares it (_take_entry).
_PremiumEntry: TypeAlias = Dict[str, Any]
# `fold_state`: the staged entries (a take's facts are its return value,
# `Taken`: nothing of a take outlives its row).
_LedgerState: TypeAlias = Dict[_LegKey, List[_PremiumEntry]]


class Taken(NamedTuple):
    """What a stock trade absorbs of the staged premiums (`take`):
    `amount`; `deferred`, the part of it that is an earlier superficial
    loss in the option's cost (TJ-0061); `tainted`, some of it is an
    unknown-cost option's (TJ-0947), and `sources`, the OPENING
    cost=unknown rows behind that (TJ-3209); `chunks`, the exercised
    replacement units' chunks it carried (the US engine, TJ-2737)."""
    amount: Decimal
    deferred: Decimal
    tainted: bool
    sources: List[Dict[str, Any]]
    chunks: List[Dict[str, Any]]


def nothing_taken() -> Taken:
    """A row that takes no staged premium."""
    return Taken(_ZERO, _ZERO, False, [], [])


class LedgerIndexes(NamedTuple):
    """The ledger's look-ahead indexes, built once from the stream
    (`lookahead`): `legs`, each (account, symbol)'s stock legs as (stream
    position, date, quantity); `leg_id` and `leg_px`, a leg's id and price
    by its position; `leg_row`, a stock row by its id; `kept_days`, the
    days an (account, symbol) has a stock row; `snap`, its OPENING
    snapshot's day; `id_pos`, the first stream position of each row id.
    A solver's row (a DISALLOW, an ADJUST) enters none but `id_pos`
    (TJ-2689)."""
    legs: Dict[_LegKey, List[Tuple[int, str, float]]]
    leg_id: Dict[int, RowId]
    leg_px: Dict[int, Decimal]
    leg_row: Dict[RowId, TaxTransaction]
    kept_days: Dict[_LegKey, List[str]]
    snap: Dict[_LegKey, str]
    id_pos: Dict[RowId, int]


class _AssignPremiumLedger:
    """Staged option-assignment premiums, each paired with ITS OWN stock
    leg(s) (audit R1-28/32/34/178, S070-18/20, S071-11, A2-0050/0052/
    0196/0203).

    An option ASSIGN stages the amount its stock leg must absorb (the
    negative of the option's would-be gain: a BUY subtracts it from cost,
    a SELL adds it to proceeds) with the share direction the assignment
    implies (short put assigned / long call exercised -> the account
    BUYS; short call assigned / long put exercised -> it SELLS) and the
    share count it delivers (_assign_delivery_shares: the declared
    contract size, else 100; one per futures option).

    `pairs` ({option id: [leg id, ...]}, from _pair_assign_legs) names
    each assignment's own stock leg(s). A paired leg takes its own
    option's entry first; an entry whose paired leg is still to come is
    RESERVED — no other trade may take it (an unrelated trade sorted in
    between, a same-moment leg of another strike, or a plain-convention
    leg next to another option's marked leg).

    Unpaired entries keep the proximity rule: a stock trade takes only
    entries on its own (account, symbol) whose option leg traded within
    _MARKED_LEG_MAX_LAG_DAYS, in its own direction, per share: a
    spread's put premium goes to the put's shares and the call premium
    to the call's; two legs of one assignment split it; an exact-size
    leg later in the window keeps its entry from a smaller unrelated
    trade sorted in between. The last matching leg in the window takes
    any residual (mini / adjusted deliverables). An entry no leg claims
    in its window is never folded into an unrelated trade months later —
    it stays undrained and the end-of-run warning names it.

    An assignment dated on or before an OPENING snapshot of its
    underlying in its account, with no stock leg of that symbol left
    up to the snapshot day (lib/opening cut them), stages nothing: the
    snapshot's cost already holds its premium, which neither the
    snapshot row nor a later purchase may take again (backlog
    TJ-0962) — unless its own leg dated after the snapshot is marked, or
    is a purchase at the strike the snapshot is too small to hold, which
    then does not hold it, unless the snapshot replaced the delivery's
    row (TJ-1167, TJ-1297, TJ-1332). So does an assignment whose
    delivery, under the underlying's name of that day, a snapshot of the
    symbol it was renamed to replaced (`_renamed_into_snapshot`,
    TJ-5005)."""

    def __init__(self, stream: Iterable[TaxTransaction],
                 is_leg: Callable[[TaxTransaction], bool],
                 pairs: Optional[Dict[RowId, List[RowId]]] = None) -> None:
        self._pos: Dict[int, int] = {}
        self._id_pos: Dict[RowId, int] = {}
        self._legs: Dict[_LegKey, List[Tuple[int, str, float]]] = {}
        # A leg's id by its stream position (an unsized warrant's
        # same-day deliveries, _same_day_later; TJ-0954).
        self._leg_id: Dict[int, RowId] = {}
        self._leg_px: Dict[int, Decimal] = {}
        self._snap: Dict[_LegKey, str] = {}
        self._snap_qty: Dict[_LegKey, float] = {}
        self._snap_cut: Dict[_LegKey, List[str]] = {}
        # The share rows a snapshot of ANOTHER symbol replaced, by the
        # (account, symbol) they were under: a rename made them the
        # snapshot's (lib/opening `_note_cut_legs`; TJ-5005).
        # Each is [snapshot day, the leg unread, its shares no exercise
        # has claimed yet (None: unread), the snapshot's symbol]: one
        # exercise claims a leg's shares, never two. `_renamed_held`: each exercise's verdict,
        # by the option row (its id), made once.
        self._snap_was: Dict[_LegKey, List[List[Any]]] = {}
        self._renamed_held: Dict[Any, bool] = {}
        self._kept_days: Dict[_LegKey, List[str]] = {}
        # A stock row by its id (a paired leg dated after a snapshot,
        # _delivered_after_snapshot; TJ-1167).
        self._leg_row: Dict[RowId, TaxTransaction] = {}
        for i, t in enumerate(stream):
            self._pos[id(t)] = i
            if t.id is not None:
                self._id_pos.setdefault(t.id, i)
            if is_opening_row(t):
                k = (t.account, t.symbol)
                self._snap[k] = max(self._snap.get(k, ''),
                                    (t.date or '')[:10])
                self._snap_qty[k] = (self._snap_qty.get(k, 0.0)
                                     + float(t.quantity or 0.0))
                for leg in split_cut_legs(t.opening_cut_legs):
                    if cut_leg_action(leg) == UNKNOWN_OPENING_LEG:
                        # An OPENING cost=unknown line the snapshot
                        # replaced: no share row, never a delivery.
                        continue
                    was = cut_leg_symbol(leg)
                    if was:
                        self._snap_was.setdefault((t.account, was), []).append(
                            [(t.date or '')[:10], leg, None, t.symbol])
                    else:
                        self._snap_cut.setdefault(k, []).append(leg)
            elif (t.action in ('BUYSELL', 'ASSIGN')
                    and not is_option_symbol(t.symbol)):
                self._kept_days.setdefault((t.account, t.symbol), []).append(
                    (t.date or '')[:10])
                if t.id is not None:
                    self._leg_row.setdefault(t.id, t)
            if is_leg(t):
                self._legs.setdefault((t.account, t.symbol), []).append(
                    (i, t.date or '', float(t.quantity or 0.0)))
                self._leg_id[i] = t.id
                self._leg_px[i] = (M.of(t.price or 0.0)
                                   or abs(M.of(t.net_amount or 0.0)
                                          / M.of(t.quantity or 1.0)))
        self._pairs = {o: list(ls) for o, ls in (pairs or {}).items()}
        # Each pair's shares (`_AssignPairs.qty`): of a leg several
        # assignments share, what is each one's (TJ-2719).
        self._pair_qty: Dict[Tuple[RowId, RowId], float] = dict(
            getattr(pairs, 'qty', None) or {})
        # A leg's own assignments, in staging order (one delivery may
        # carry several: _pair_assign_legs, TJ-0838).
        self._leg_owners: Dict[RowId, List[RowId]] = {}
        for o, ls in self._pairs.items():
            for l in ls:
                self._leg_owners.setdefault(l, []).append(o)
        self._e: Dict[_LegKey, List[_PremiumEntry]] = {}

    def lookahead(self) -> LedgerIndexes:
        """The indexes `take` and `stage` look ahead in, by name."""
        return LedgerIndexes(self._legs, self._leg_id, self._leg_px, self._leg_row,
                             self._kept_days, self._snap, self._id_pos)

    _direction = staticmethod(_assign_direction)

    def stage(self, opt_tx: TaxTransaction, underlying: str, amount: Decimal,
              deferred: Decimal = _ZERO,
              tainted: bool = False,
              sources: Optional[List[Dict[str, Any]]] = None,
              chunk: Optional[Dict[str, Any]] = None) -> None:
        """`deferred`: the part of `amount` that is an earlier
        superficial loss added to the option's cost (its pool's
        deferred_wash share): the stock leg's pool tallies it, so the
        economic P/L views count that loss once (backlog TJ-0061).
        `tainted`: the contracts were held at an unknown cost (an
        OPENING cost=unknown): `amount` is not their premium, and the
        stock leg that takes it carries the unknown cost on
        (`Taken.tainted`; backlog TJ-0947). `sources`: the OPENING
        cost=unknown rows the contracts came by (the option pool's
        `unknown_from`), carried to the shares' records (TJ-3209).
        `chunk` (the US engine): the exercised units' replacement
        chunk (`_us_record_left_shares`; None: not recorded), handed
        to the stock leg that takes the amount (`Taken.chunks`), whose
        lots it names (TJ-2737)."""
        key = (opt_tx.account, underlying)
        if (self._in_snapshot(key, (opt_tx.date or '')[:10])
                and not self._delivered_after_snapshot(opt_tx, key)):
            self._say_amount_on_delivered_row(opt_tx, underlying)
            return
        if self._renamed_into_snapshot(opt_tx, key):
            return
        lst = self._e.setdefault(key, [])
        for e in lst:
            if e['src'] == opt_tx.id:
                e['amt'] += amount
                e['dw'] += deferred
                e['tainted'] = e['tainted'] or bool(tainted)
                e['sources'] += [s for s in (sources or [])
                                 if s not in e['sources']]
                e['chunks'] += [chunk] if chunk is not None else []
                return
        shares = _assign_delivery_shares(opt_tx)
        lst.append({'src': opt_tx.id, 'amt': amount, 'dw': deferred,
                    'tainted': bool(tainted),
                    'sources': [dict(s) for s in (sources or [])],
                    'chunks': [chunk] if chunk is not None else [],
                    'sym': opt_tx.symbol or '', 'und': underlying,
                    'dir': self._direction(opt_tx),
                    'shares': shares if shares and shares > 1e-9 else None,
                    'contracts': abs(float(opt_tx.quantity or 0.0)),
                    'date': opt_tx.date or '',
                    'legs': self._pairs.get(opt_tx.id, [])})

    def _in_snapshot(self, key: _LegKey, day: str) -> bool:
        """Does an OPENING snapshot of `key` (account, underlying), on or
        after `day`, hold this assignment's delivery (no stock leg of
        it kept from `day` to the snapshot day)? TJ-0962."""
        snap = self._snap.get(key)
        if not snap or not day or day > snap:
            return False
        return not any(day <= d <= snap
                       for d in self._kept_days.get(key, ()))

    def _say_amount_on_delivered_row(self, opt_tx: TaxTransaction,
                                     snapshot_of: str) -> None:
        """An exercise or assignment row that carries an amount beyond its
        fee, booked as DELIVERED into shares an opening snapshot holds:
        nothing more is booked for it, so if the amount was a cash
        settlement its gain or loss is in no row. The row's cash cannot
        tell a settlement from a commission or cash in lieu (owner,
        2026-11-23: read as delivered, as before, and said): one
        ATTENTION a row, with both readings and what settles each."""
        net = abs(M.of(opt_tx.net_amount or 0.0))
        if (net < M.of(0.005)
                or abs(net - abs(M.of(opt_tx.fee or 0.0))) < M.of(0.005)):
            return                      # no amount, or its fee alone
        memo = ('amount', opt_tx.id if opt_tx.id is not None else id(opt_tx))
        if memo in self._renamed_held:
            return
        self._renamed_held[memo] = True
        emit_line(
            f"warning: ATTENTION: {opt_tx.symbol}: the exercise or "
            f"assignment row of {(opt_tx.date or '')[:10]} carries "
            f"{M.shown(net, 2):,.2f} {opt_tx.currency or ''} and is booked "
            f"as DELIVERED: its shares are in the opening snapshot of "
            f"{snapshot_of}, whose cost is taken to hold the premium, so "
            f"nothing more is booked for the row and that amount is in no "
            f"gain. If shares were delivered, write the row with no "
            f"amount (a charge or cash in lieu as a row of its own). If "
            f"it was settled in cash, write it as a closing BUYSELL of "
            f"the option at the settlement amount: an ASSIGN row is read "
            f"as a delivery where its underlying trades as stock.")

    def renamed_delivery_underlying(self, opt_tx: TaxTransaction) -> Optional[str]:
        """The underlying an exercise or assignment names (its option
        root, or a warrant's exercise target) when a snapshot of the
        symbol it was renamed to replaced its delivery
        (`_renamed_into_snapshot`), else None. For the engines' walks:
        such an exercise is a delivery whose premium the snapshot's cost
        holds, never a cash settlement, though no stock row of the old
        name is left in the books for the engines' resolver to find
        (TJ-5005)."""
        und = exercise_target(opt_tx) or parse_option_underlying(opt_tx.symbol)
        if und and self._renamed_into_snapshot(opt_tx, (opt_tx.account, und)):
            return und
        return None

    def _renamed_into_snapshot(self, opt_tx: TaxTransaction, key: _LegKey) -> bool:
        """Did an OPENING snapshot of the symbol `key`'s underlying was
        renamed to replace this assignment's delivery? The option names
        the underlying of its day, and so does the delivery: a snapshot
        after the rename is of the new symbol, which `_in_snapshot` does
        not find, so the premium the snapshot's cost already holds was
        staged under the old name and taken by a later, unrelated
        purchase under that name, or realized on the option as a cash
        settlement when no row of the old name was left (TJ-5005, the
        branch TJ-0962 left open).

        The delivery is among the share rows the snapshot replaced under
        the old name (`_snap_was`), with the guards of the same-symbol
        case: the exercise is dated on or before the snapshot
        (`_in_snapshot`); the row is in the assignment's direction and
        pairing window, and is marked ASSIGN or at the strike
        (`_delivery_cut`); the rows come to the shares it delivers (one
        row when its size is unknown), and those shares are claimed: a
        row is one exercise's delivery, never a second one's. Not when a
        marked delivery of its own is still in the books: that row takes
        the premium (TJ-1167). The verdict is made once for an exercise
        (the walks ask again). Rows in the window that are neither
        marked nor at the strike are not taken for the delivery, and the
        run says which row to mark."""
        memo = opt_tx.id if opt_tx.id is not None else id(opt_tx)
        if memo not in self._renamed_held:
            self._renamed_held[memo] = self._claim_renamed_cut(opt_tx, key)
        return self._renamed_held[memo]

    def _claim_renamed_cut(self, opt_tx: TaxTransaction, key: _LegKey) -> bool:
        """`_renamed_into_snapshot`'s verdict, claiming the cut rows'
        shares when it is yes."""
        cut = self._snap_was.get(key)
        if not cut or any(
                leg is not None and leg.action == 'ASSIGN'
                for leg in map(self._leg_row.get,
                               self._pairs.get(opt_tx.id, ()))):
            return False
        day = (opt_tx.date or '')[:10]
        d = self._direction(opt_tx)
        size = _assign_delivery_shares(opt_tx)
        strike = (None if exercise_target(opt_tx) or size is None
                  else parse_option_strike(opt_tx.symbol))

        def at_strike(px: Decimal) -> bool:
            return bool(strike and abs(px - M.of(strike))
                        <= M.of(max(0.005, 1e-6 * strike)))
        marked: List[List[Any]] = []
        priced: List[List[Any]] = []
        plain = ''
        for entry in cut:
            snap, raw, left = entry[:3]
            leg = parse_cut_leg(raw)
            qty = float(leg.quantity)
            if left is None:
                entry[2] = left = abs(qty)
            gap = _day_gap(leg.date, day)
            if (not day or day > snap or left <= 1e-9 or gap is None
                    or gap > (_ASSIGN_LEG_LEAD_DAYS if leg.date < day
                              else _MARKED_LEG_MAX_LAG_DAYS)
                    or (d is not None and (1 if qty > 0 else -1) != d)):
                continue
            if leg.action == 'ASSIGN':
                marked.append(entry)
            elif at_strike(M.of(leg.price)):
                priced.append(entry)
            else:
                plain = plain or leg.date
        fit = marked + priced
        have = sum(e[2] for e in fit)
        enough = bool(fit) and (not size or have >= size - 1e-6)
        by_mark = bool(marked) and (
            not size or sum(e[2] for e in marked) >= size - 1e-6)
        later = (None if by_mark or not enough
                 else self._row_after_renamed_snapshot(opt_tx, fit, d, at_strike))
        if enough and later is None:
            need = size or fit[0][2]
            for e in fit:
                took = min(need, e[2])
                e[2] -= took
                need -= took
                if need <= 1e-9:
                    break
            self._say_amount_on_delivered_row(
                opt_tx, f"{fit[0][3]} ({key[1]} renamed)")
            return True
        head = (f"{opt_tx.symbol}: exercised or assigned on {day}, and "
                f"{key[1]} was renamed before an opening snapshot replaced "
                f"its rows")
        # What does settle it: the premium does not follow a rename to a
        # row under the new name (the option names the old one: backlog
        # TJ-5120), so marking that row is no remedy.
        rewrite = (f"write the option under the renamed ticker "
                   f"({later[2]}), and its premium rolls into that row"
                   if later else "")
        if later and later[0]:
            emit_line(
                f"warning: ATTENTION: {head}: its delivery, the ASSIGN row "
                f"of {later[1]}, follows the snapshot under the new name "
                f"{later[2]}. The option names {key[1]}, so its premium is "
                f"not rolled into those shares: it stays on the option, as "
                f"a cash settlement would. To roll it, {rewrite}.")
        elif later or plain or fit:
            why = (f"a replaced row at the strike and the row of {later[1]} "
                   f"after the snapshot can each be its delivery" if later
                   else f"the replaced rows marked ASSIGN or at the strike "
                        f"come to {have:g} shares, fewer than it delivers"
                   if fit else
                   f"the replaced row of {plain} is neither marked ASSIGN "
                   f"nor at the strike")
            emit_line(
                f"warning: ATTENTION: {head}: {why}, so the snapshot's cost "
                f"is not taken to hold its premium. If a replaced row is "
                f"its delivery, write that row as ASSIGN"
                + (f"; if the row of {later[1]} is, write it as ASSIGN and "
                   f"{rewrite}" if later else "") + ".")
        return False

    def _row_after_renamed_snapshot(self, opt_tx: TaxTransaction,
                                    fit: List[List[Any]], d: Optional[int],
                                    at_strike: Callable[[Decimal], bool]
                                    ) -> Optional[Tuple[bool, str, str]]:
        """A share row of the snapshot's (renamed) symbol dated after the
        snapshot that can be this exercise's delivery instead of the
        rows the snapshot replaced: in its direction and posting lag,
        marked ASSIGN or at the strike: (is it marked, its day, its
        symbol), a marked row first; None when there is none. A plain
        row at the strike before the snapshot and another after it
        cannot be told apart."""
        day = (opt_tx.date or '')[:10]
        found = None
        for snap, sym in sorted({(e[0], e[3]) for e in fit}):
            for (i, when, q) in self._legs.get((opt_tx.account, sym), ()):
                gap = _day_gap(when[:10], day)
                if (when[:10] <= snap or gap is None
                        or gap > _MARKED_LEG_MAX_LAG_DAYS or abs(q) < 1e-12
                        or (d is not None and (1 if q > 0 else -1) != d)):
                    continue
                row = self._leg_row.get(self._leg_id.get(i))
                if row is not None and row.action == 'ASSIGN':
                    return True, when[:10], sym
                if at_strike(self._leg_px.get(i, _ZERO)):
                    found = found or (False, when[:10], sym)
        return found

    def _delivered_after_snapshot(self, opt_tx: TaxTransaction, key: _LegKey) -> bool:
        """Is one of this assignment's own legs dated after the snapshot
        of `key`, by strong identity? The snapshot does not hold those
        shares, so the premium is theirs (TJ-1167). A marked ASSIGN leg
        is. A plain row at the option's strike is only when it is a
        purchase and the snapshot holds fewer shares than the assignment
        delivers, and the snapshot did not replace the delivery's own row
        (TJ-0962, TJ-1297, TJ-1332). Nor does a plain purchase just after
        it, merely of the delivered size. Where the books cannot tell, a
        plain row at the strike is an ordinary trade, and the run says
        which row to mark (TJ-1325)."""
        snap = self._snap.get(key, '')
        size = _assign_delivery_shares(opt_tx)
        strike = (None if exercise_target(opt_tx) or size is None
                  else parse_option_strike(opt_tx.symbol))
        plain = []
        for l in self._pairs.get(opt_tx.id, ()):
            leg = self._leg_row.get(l)
            if leg is None or (leg.date or '')[:10] <= snap:
                continue
            if leg.action == 'ASSIGN':
                return True
            if (strike and strike > 0
                    and abs(M.of(leg.price or 0.0) - M.of(strike))
                    <= M.of(max(0.005, 1e-6 * strike))):
                plain.append(leg)
        if not plain or self._delivery_cut(opt_tx, key, strike):
            return False
        if (self._direction(opt_tx) == 1
                and size is not None
                and self._snap_qty.get(key, 0.0) < size - 1e-6):
            return True
        emit_line(
            f"warning: ATTENTION: {opt_tx.symbol}: if the row of "
            f"{(plain[0].date or '')[:10]} at the strike is its delivery, "
            f"write it as ASSIGN. Exercised or assigned on "
            f"{(opt_tx.date or '')[:10]}, on or before the opening snapshot "
            f"of {key[1]} ({snap}): the snapshot's cost is taken to hold its "
            f"premium, and that row to be an ordinary trade.")
        return False

    def _delivery_cut(self, opt_tx: TaxTransaction, key: _LegKey,
                      strike: Optional[float]) -> bool:
        """Did the snapshot of `key` replace a share row of this
        assignment: in its direction and pairing window, marked or at its
        strike (lib/opening `_note_cut_legs`)?"""
        day = (opt_tx.date or '')[:10]
        d = self._direction(opt_tx)
        size = _assign_delivery_shares(opt_tx)
        for raw in self._snap_cut.get(key, ()):
            leg = parse_cut_leg(raw)
            cday, action = leg.date, leg.action
            qty, px = float(leg.quantity), M.of(leg.price)
            if size and abs(qty) < size - 1e-6:
                continue                # too few shares to be the delivery
            gap = _day_gap(cday, day)
            if gap is None or gap > (_ASSIGN_LEG_LEAD_DAYS if cday < day
                                     else _MARKED_LEG_MAX_LAG_DAYS):
                continue
            if d is not None and (1 if qty > 0 else -1) != d:
                continue
            if action == 'ASSIGN' or (
                    strike and abs(px - M.of(strike))
                    <= M.of(max(0.005, 1e-6 * strike))):
                return True
        return False

    @staticmethod
    def _in_window(e: _PremiumEntry, date: str) -> bool:
        g = _day_gap(e['date'], date)
        return g is None or g <= _MARKED_LEG_MAX_LAG_DAYS

    def _reserved(self, e: _PremiumEntry, idx: int) -> bool:
        """Is this entry held for a paired leg that has not traded yet?"""
        return any(self._id_pos.get(l, -1) > idx for l in e['legs'])

    def _later_legs(self, e: _PremiumEntry, acct: str, sym: str, idx: int) -> List[float]:
        out = []
        for (i, d, q) in self._legs.get((acct, sym), ()):
            if i <= idx or abs(q) < 1e-12:
                continue
            if e['dir'] is not None and (1 if q > 0 else -1) != e['dir']:
                continue
            if self._in_window(e, d):
                out.append(abs(q))
        return out

    def _same_day_later(self, e: _PremiumEntry, acct: str, sym: str, idx: int, date: str,
                        need: float) -> float:
        """Shares of the later legs on `date` in the entry's direction
        that no other assignment owns: the rest of a delivery in parts
        when the entry's size is unknown (a warrant exercised with no
        shares per warrant stated; TJ-0954). The parts of one delivery
        are at one price, with no sale of the shares between them, and
        come to no more shares than the warrants (a larger size is
        stated, `x<n>`): the first row that cannot be one ends them, so
        a market trade of the day keeps its own cost (TJ-1190)."""
        tot = 0.0
        room = e['contracts'] - e.get('took', 0.0) - need
        px = self._leg_px.get(idx, _ZERO)
        for (i, d, q) in self._legs.get((acct, sym), ()):
            if i <= idx or d != date or abs(q) < 1e-12:
                continue
            if e['dir'] is not None and (1 if q > 0 else -1) != e['dir']:
                break
            owners = self._leg_owners.get(self._leg_id.get(i), ())
            if owners and e['src'] not in owners:
                continue
            leg_px = self._leg_px.get(i, _ZERO)
            if (tot + abs(q) > room + 1e-9
                    or (px > 0 and leg_px > 0
                        and abs(leg_px - px) > _TWO_PERCENT * px)):
                break
            tot += abs(q)
        return tot

    def _take_entry(self, lst: List[_PremiumEntry], e: _PremiumEntry, need: float,
                    later: Sequence[object], acc: List[Any],
                    rest_today: float = 0.0) -> Tuple[Decimal, float]:
        """Take up to `need` shares of entry `e`: (amount, shares); its
        deferral share adds to the take's (`acc`: [deferred, tainted,
        sources, chunks]), its unknown cost taints it. `rest_today`: for an entry of unknown size, the
        shares of the same-day deliveries still to come (_same_day_later):
        the amount is shared by quantity with them, never all taken by
        the first part (TJ-0954)."""
        if e.get('tainted'):
            acc[1] = True
            for _s in e.get('sources') or []:
                if _s not in acc[2]:
                    acc[2].append(_s)
        for _c in e.get('chunks') or []:
            if not any(_c is _k for _k in acc[3]):
                acc[3].append(_c)
        if e['shares'] is None and rest_today > 1e-9 and need > 1e-9:
            frac = need / (need + rest_today)
            part = e['amt'] * M.of(frac)
            dw = e['dw'] * M.of(frac)
            e['amt'] -= part
            e['dw'] -= dw
            e['took'] = e.get('took', 0.0) + need
            acc[0] += dw
            if not e.get('split_said'):
                e['split_said'] = True
                emit_line(
                    f"warning: ATTENTION: {e['sym']}: the exercise on "
                    f"{e['date']} states no shares per warrant, so its "
                    f"cost is shared by quantity among the "
                    f"{need + rest_today:g} {e['und']} shares delivered "
                    f"that day; if those are not all its shares, give "
                    f"the exercise row its size (`x<shares per "
                    f"warrant>`).")
            return part, need
        if e['shares'] is None:
            lst.remove(e)
            acc[0] += e['dw']
            return e['amt'], 0.0
        take_sh = min(need, e['shares'])
        if take_sh >= e['shares'] - 1e-9 or not later:
            lst.remove(e)
            acc[0] += e['dw']
            return e['amt'], take_sh
        part = e['amt'] * M.of(take_sh) / M.of(e['shares'])
        dw = e['dw'] * M.of(take_sh) / M.of(e['shares'])
        e['amt'] -= part
        e['dw'] -= dw
        e['shares'] -= take_sh
        acc[0] += dw
        return part, take_sh

    def take(self, tx: TaxTransaction) -> Taken:
        """What this stock trade absorbs of the staged premiums: the
        amount, the deferral part of it, whether any of it is an
        unknown-cost option's (TJ-0947) and from which rows, and the
        chunks it carried (`Taken`)."""
        key = (tx.account, tx.symbol)
        lst = self._e.get(key)
        if not lst:
            return nothing_taken()
        acc: List[Any] = [_ZERO, False, [], []]     # deferred, tainted, sources, chunks
        idx = self._pos.get(id(tx), -1)
        q = float(tx.quantity or 0.0)
        need = abs(q)
        sign = 1 if q > 0 else -1
        date = tx.date or ''
        total = _ZERO
        # 1. This leg's own assignment (paired by identity). Of a leg
        # several assignments share, each takes its own shares of it
        # (its pair's claim) and no more: walked in staging order, the
        # first owner took the whole leg and the next, with no later
        # leg of its own, was drained into no shares at all, so a put's
        # premium lowered the cost of shares the other put delivered
        # and the sale between the two deliveries moved (TJ-2719).
        owners = self._leg_owners.get(tx.id, ())
        if owners:
            for e in [e for e in lst if e['src'] in owners]:
                later = [l for l in e['legs']
                         if self._id_pos.get(l, -1) > idx]
                claim = self._pair_qty.get((e['src'], tx.id))
                want = need if claim is None else min(need, claim)
                if want <= 1e-9 and e['shares'] is not None:
                    continue
                amt, took = self._take_entry(
                    lst, e, want, later, acc,
                    self._same_day_later(e, tx.account, tx.symbol, idx,
                                         date, want)
                    if e['shares'] is None else 0.0)
                total += amt
                need -= took
        # 2. Unpaired (or no-longer-reserved) entries by proximity.
        cands = [e for e in lst if self._in_window(e, date)
                 and not self._reserved(e, idx)]
        same = [e for e in cands if e['dir'] in (None, sign)]
        # An opposite-direction entry is taken only when no leg of its
        # own direction is still coming in its window (a parser whose
        # option-leg sign disagrees must not strand the premium).
        other = [e for e in cands if e['dir'] not in (None, sign)
                 and not self._later_legs(e, tx.account, tx.symbol, idx)]
        chosen = same or other
        chosen.sort(key=lambda e: 0 if (e['shares'] is not None and abs(
            e['shares'] - need) < 1e-6) else 1)
        for e in chosen:
            if need <= 1e-9:
                break
            later = self._later_legs(e, tx.account, tx.symbol, idx)
            if (e['shares'] is not None
                    and abs(e['shares'] - need) > 1e-6
                    and any(abs(l - e['shares']) < 1e-6 for l in later)):
                continue    # reserved for its exact-size leg
            amt, took = self._take_entry(
                lst, e, need, later, acc,
                self._same_day_later(e, tx.account, tx.symbol, idx, date,
                                     need)
                if e['shares'] is None else 0.0)
            total += amt
            need -= took
        if not lst:
            self._e.pop(key, None)
        return Taken(total, *acc)

    def undrained(self) -> Dict[_LegKey, Decimal]:
        out: Dict[_LegKey, Decimal] = {}
        for k, lst in self._e.items():
            for e in lst:
                out[k] = out.get(k, _ZERO) + e['amt']
        return out

    def fold_state(self) -> _LedgerState:
        """What a walk changes here: the staged entries (the Canadian
        re-fold's checkpoint copies them, `_WalkFold`). A take's facts are
        read by the row that took them and by no later row."""
        return self._e

    def restore(self, state: _LedgerState) -> None:
        """Back to a `fold_state` (one copied from an earlier iteration's
        ledger: the indexes are the stream's, the entries carry none)."""
        self._e.clear()
        self._e.update(state)
