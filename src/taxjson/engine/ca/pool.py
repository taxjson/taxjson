"""The Canadian ACB pool and the pools of one walk: their owner (PLAN step 13).

A pool's state is written here and nowhere else: `AcbPool`'s fields are
read-only attributes over private slots, changed by its operations alone
(the type checker refuses a write from outside, and one that slipped by
raises). `PoolBook` is the walk's `{key: pool}` with the three ways its keys
change, what a spin-off's parent gives up on its way to the new shares, and
the checkpoint's `save` and `restore`.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from operator import attrgetter
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from taxjson.lib.numeric import D
from taxjson.lib.pool_eps import pool_qty_eps
from taxjson.model import money as M
from taxjson.engine.consts import _MILLI, _NANO, _ZERO
from taxjson.engine.ca.pools import _MATERIAL

_COST_NOISE = Decimal('0.001')      # a drained pool's cost under it is wiped
_NO_PURCHASE = '1970-01-01'         # a pool's last_acq_date before any purchase
# A row leaves the pool long when what it holds after is more than float
# noise of the units (lib/pool_eps's coin tolerance, the finest a pool knows
# without its symbol): `take_pending`.
_LONG_NOISE = 1e-11


_FIELDS = ('qty', 'total_cost', 'currency', 'last_acq_date', 'last_acq_settle', 'opening_acq',
           'position_start_date', 'closed_holding', 'deferred_wash', 'pending_wash', 'wash_cut',
           'cut_late', 'tainted', 'taint_other', 'moved_from', 'unknown_from', 'grants',
           'account_premiums', 'account_lots')


class AcbPool:
    """One security's average-cost pool (s.47). Its fields are read as
    attributes and written by its operations alone (PLAN step 13): `qty`
    (float units, negative while short), `total_cost` (Decimal; a short's:
    its entry proceeds), `currency`, the acquisition dates (`last_acq_date`,
    the window's `last_acq_settle`, an opening row's `opening_acq`),
    `position_start_date`, `closed_holding` (the long holding a sale last
    closed), the denied losses in the cost (`deferred_wash`; `pending_wash`,
    an addition waiting on the pool while it holds no long position, tallied
    there but in no cost; `wash_cut` and `cut_late`, what a return of capital
    took of them), the units of unknown cost (`tainted`,
    until the pool drains, and the causes: `taint_other`, `moved_from`,
    `unknown_from`) and `grants` (a short option pool's written lots).

    A copy the reports read (`snapshot`, `part`, `netted`) is a pool no
    walk writes: the year-end's carries each account's written premium
    (`account_premiums`, `account_lots`; None on a walk's pool)."""

    __slots__ = tuple('_' + _f for _f in _FIELDS)

    def __init__(self, currency: str = '') -> None:
        self._qty: float = 0.0
        self._total_cost: Decimal = Decimal(0)
        self._currency: str = currency
        self._last_acq_date: str = _NO_PURCHASE
        self._last_acq_settle: Optional[str] = None
        self._opening_acq: Optional[str] = None
        self._position_start_date: Optional[str] = None
        self._closed_holding: Optional[Tuple[str, str, int]] = None
        self._deferred_wash: Decimal = _ZERO
        self._pending_wash: Decimal = _ZERO
        self._wash_cut: Decimal = _ZERO
        self._cut_late: Decimal = _ZERO
        self._tainted: bool = False
        self._taint_other: bool = False
        self._moved_from: List[str] = []
        self._unknown_from: List[Dict[str, Any]] = []
        self._grants: List[Dict[str, Any]] = []
        self._account_premiums: Optional[Dict[str, Any]] = None
        self._account_lots: Optional[Dict[str, Any]] = None

    if TYPE_CHECKING:        # read-only properties (set below the class)
        @property
        def qty(self) -> float: ...
        @property
        def total_cost(self) -> Decimal: ...
        @property
        def currency(self) -> str: ...
        @property
        def last_acq_date(self) -> str: ...
        @property
        def last_acq_settle(self) -> Optional[str]: ...
        @property
        def opening_acq(self) -> Optional[str]: ...
        @property
        def position_start_date(self) -> Optional[str]: ...
        @property
        def closed_holding(self) -> Optional[Tuple[str, str, int]]: ...
        @property
        def deferred_wash(self) -> Decimal: ...
        @property
        def pending_wash(self) -> Decimal: ...
        @property
        def wash_cut(self) -> Decimal: ...
        @property
        def cut_late(self) -> Decimal: ...
        @property
        def tainted(self) -> bool: ...
        @property
        def taint_other(self) -> bool: ...
        @property
        def moved_from(self) -> List[str]: ...
        @property
        def unknown_from(self) -> List[Dict[str, Any]]: ...
        @property
        def grants(self) -> List[Dict[str, Any]]: ...
        @property
        def account_premiums(self) -> Optional[Dict[str, Any]]: ...
        @property
        def account_lots(self) -> Optional[Dict[str, Any]]: ...

    @classmethod
    def new(cls, currency: str = '') -> AcbPool:
        """A pool no row has touched: no units, no cost. total_cost is a
        Decimal (no drift over many rows), qty a float; `position_start_date`
        is the trade that opened the current holding (cleared on each drain),
        for the inventory's entry-date price."""
        return cls(currency)

    def drain(self, eps: float, closed: Optional[Tuple[str, int]] = None
              ) -> Optional[Tuple[bool, bool]]:
        """Drain the pool to zero when what a row left of it is no position:
        no units at all, or fewer than `eps` units costing less than
        MATERIAL_COST. A residue under the tolerance that carries real cost
        is a holding, whichever row left it (a sale, an own move's or a
        listing journal's units out): zeroed, its cost was stranded and the
        next sale found an empty pool, opened a short and lost the gain
        (2026-10 audit #101, TJ-0649, TJ-1062, TJ-1141; a journal's and an
        own move's, TJ-4244). A residue of exactly `eps` units is a position.

        Drained: no units; `position_start_date` cleared, so the next opening
        seeds a fresh start (opened, closed, reopened: the date of the second
        opening); no written lots; the unknown-cost units are gone with the
        rest (`untaint`: later purchases form a known ACB); and the cost is
        wiped only when it is effectively zero (an adjustment's amount
        stays). `closed` (the closing row's date and identity; a long
        holding a sale closed): kept as `closed_holding` with the position's
        start, for an s.86.1 spin-off distributed after it
        (`_s861_prior_sales`).

        None when the position stands; else (the pool held unknown-cost
        units, its cost was wiped), for the walk's trace."""
        qty = self._qty
        if not abs(qty) < eps:
            return None
        if qty and not abs(M.of(self._total_cost or 0.0)) < _MATERIAL:
            return None
        self._qty = 0.0
        if closed is not None:
            self._closed_holding = (self._position_start_date or '', closed[0], closed[1])
        self._position_start_date = None
        self._grants = []
        tainted = bool(self._tainted)
        if tainted:
            self.untaint()
        wiped = abs(self._total_cost) < _COST_NOISE
        if wiped:
            self._total_cost = Decimal(0)
        return tainted, wiped

    def close(self, units: float, cost_basis: Any, eps: float) -> None:
        """A sale's (a short's cover's) `units` leave the pool at
        `cost_basis`, which the walk computed: the average cost, or the
        closed written lots' premium (`close_lots`). Deferred-wash dollars
        are part of the ACB, so a partial close releases them in the same
        proportion (a full drain releases all: they were recovered in this
        gain), and what a return of capital took of them (`wash_cut`) with
        them; the share is of the units held BEFORE these leave (none is
        released from a position at or under `eps`, the trade's tolerance)."""
        self._total_cost -= D(cost_basis)
        _pre_abs = abs(self._qty)
        if _pre_abs > eps:
            _pw = self._unpark_tally()
            self._deferred_wash = (
                self._deferred_wash
                * M.of(max(0.0, 1.0 - units
                           / _pre_abs)))
            self._repark_tally(_pw)
            if self._wash_cut:
                self._wash_cut *= M.of(max(
                    0.0, 1.0 - units / _pre_abs))
        self._qty += (units if self._qty < 0 else -units)

    def open_lot(self, *, tx_id: Any, seq: int, units: float, premium: Any,
                 recognise: bool, date: str, year: Any, account: str) -> Dict[str, Any]:
        """Append a short-option lot of `units` whose (signed, net of
        commission) premium is `premium`, the `seq`-th the run wrote;
        returns the lot (its grant records: `rec_ref`, `loss_ref`, set by
        `_ca_open_short_option`)."""
        lot = {'tx_id': tx_id, 'seq': seq, 'units': units,
               'per_unit': (M.of(premium) / M.of(units)) if units > 1e-12 else _ZERO,
               'rec': bool(recognise), 'rec_ref': None,
               'loss_ref': None, 'date': date,
               'year': year, 'account': account}
        self._grants.append(lot)
        return lot

    def record_grant(self, lot: Dict[str, Any], gain: Dict[str, Any]) -> None:
        """`lot` (one of this pool's, `open_lot`) names its grant's gain
        record (s.49(1): the premium recognised at the write), which a
        close of the lot reduces (`close_lots`)."""
        lot['rec_ref'] = gain

    def record_grant_loss(self, lot: Dict[str, Any], loss: Dict[str, Any]) -> None:
        """`lot` names the loss its grant fed to the superficial-loss
        solver (a write whose commission exceeds its premium, CA-SL-12),
        which an assignment of the lot reduces (`close_lots`)."""
        lot['loss_ref'] = loss

    def close_lots(self, closing_qty: float, is_assign: bool,
                   account: Optional[str] = None
                   ) -> Tuple[Decimal, Decimal, float, Dict[str, Dict[str, Any]]]:
        """Consume `closing_qty` units of a SHORT option pool's lots
        FIFO, the closing row's own `account`'s lots first: an
        assignment or buy-back closes the contract written in that
        brokerage account, never one still open in another (backlog
        TJ-0718); another account's lots only when its own run out
        (a contract moved between accounts). A closing row reaches the
        pool only while its own account is short the series
        (_MarkedShortRouter, CA-OPT-12, TJ-2735): a purchase in an
        account that wrote nothing is a bought contract of the
        symbol's long pool. Returns (cost_removed, recognised, grant_units_closed,
        by_year) where cost_removed is the premium leaving the pool
        (positive = premium), recognised the part already booked at
        grant, and by_year {write year: {'units', 'premium'}} the
        recognised lots this close consumed — Schedule 3 and
        reconcile-slips count a buy-back of a SAME-year write once
        with its write, but a buy-back of an earlier year's write
        is a disposition of its own (A2-0320/0650/0651), and a
        broker's close-year slip carries that earlier premium
        (A2-0657)."""
        lots = self._grants or []
        qabs = abs(self._qty)
        total = M.of(self._total_cost)
        rq = sum(l['units'] for l in lots if l['rec'])
        r_amt = sum(M.of(l['units']) * l['per_unit'] for l in lots if l['rec'])
        n_other = qabs - rq
        # What a lot with no recognised premium leaves at: the average
        # of its own account's such lots, so a close never takes
        # another account's premium, with its share of what the lots
        # do not hold (a parked wash deferral) (backlog TJ-1105). One
        # account's lots: the pool's average, the same figure. Each
        # account's lots keep that average for its next close.
        own_avg: Dict[Any, Decimal] = {}
        residue = _ZERO
        if n_other > 1e-9:
            other_avg = (total - r_amt) / M.of(n_other)
            rec_extra = _ZERO
            plain = [l for l in lots if not l['rec']]
            _sums: Dict[Any, List[Any]] = {}
            for l in plain:
                e = _sums.setdefault(l.get('account'), [0.0, _ZERO])
                e[0] += l['units']
                e[1] += M.of(l['units']) * l['per_unit']
            for a, (u, m) in _sums.items():
                own_avg[a] = m / M.of(u) if u > 1e-12 else _ZERO
            for l in plain:
                l['per_unit'] = own_avg[l.get('account')]
            if len(own_avg) > 1:
                residue = ((total - r_amt
                            - sum(m for _u, m in _sums.values()))
                           / M.of(n_other))
            else:
                own_avg = {}
        else:
            # Only recognised lots remain: any residue in the pool
            # (a parked wash deferral) rides them pro rata so the
            # pool still drains to exactly zero.
            other_avg = _ZERO
            rec_extra = (total - r_amt) / M.of(rq) if rq > 1e-9 else _ZERO
        cost = _ZERO
        recognised = _ZERO
        g_units = 0.0
        by_year: Dict[str, Dict[str, Any]] = {}
        rem = closing_qty
        _order = lots
        if account is not None:
            _order = ([l for l in lots if l.get('account') == account]
                      + [l for l in lots
                         if l.get('account') != account])
        for lot in _order:
            if rem <= 1e-9:
                break
            take = min(rem, lot['units'])
            if take <= 1e-12:
                continue
            lot['units'] -= take
            rem -= take
            if not lot['rec']:
                cost += M.of(take) * (own_avg[lot.get('account')] + residue
                                      if own_avg else other_avg)
                continue
            cost += M.of(take) * (lot['per_unit'] + rec_extra)
            if is_assign:
                # s.49(3) (call) / s.49(3.1) (put): the granting is
                # deemed not to be a disposition (s.49(4) reopens
                # the grant year) — retract these units from the grant
                # record; the full premium folds into the share leg.
                _amt = M.of(take) * lot['per_unit']
                ref = lot.get('rec_ref')
                if ref is not None:
                    _q0 = ref['qty']
                    _f = (take / _q0) if _q0 > 1e-12 else 1.0
                    ref['qty'] = _q0 - take
                    ref['gain'] -= _amt
                    ref['taxable_gain'] -= _amt
                    ref['cost'] -= _amt
                    ref['commission'] *= M.of(max(0.0, 1.0 - _f))
                    ref['fee'] *= M.of(max(0.0, 1.0 - _f))
                    if ref['qty'] <= 1e-9:
                        ref['_void'] = True
                lref = lot.get('loss_ref')
                if lref is not None:
                    lref['qty'] -= take  # cov: a2-1596-grant-loss-ref
                    lref['loss_amount'] = max(
                        _ZERO, lref['loss_amount'] + _amt)
                    if lref['qty'] <= 1e-9 or lref['loss_amount'] <= _MILLI:
                        lref['_void'] = True
            else:
                recognised += M.of(take) * lot['per_unit']
                g_units += take
                _by = by_year.setdefault(
                    lot.get('year') or '', {'units': 0.0,
                                            'premium': _ZERO})
                _by['units'] += take
                _by['premium'] += M.of(take) * lot['per_unit']
        # The lots always cover the close (A2-1596): every short
        # opening of an option pool opens a lot (write, crossing
        # sell, missing-history opening), a split scales lots and
        # pool alike, a rename merges both, a drain clears both, and
        # a close takes the same quantity from each — so `rem` ends
        # at zero and no closed unit is left uncosted.
        self._grants = [l for l in lots if l['units'] > 1e-9]
        return cost, recognised, g_units, by_year

    def retag_lots(self, frm: str, to: str, units: float) -> float:
        """A move of `units` written contracts from account `frm` to `to`
        inside one pool (the blended pass, TJ-3192): `frm`'s lots become
        `to`'s, earliest first, a lot split where the move takes part of it
        (the same premium per unit, the same grant record). Returns the
        units carried."""
        rem = units
        out: List[Dict[str, Any]] = []
        for lot in self._grants or []:
            if rem > 1e-9 and lot.get('account') == frm and lot['units'] > 1e-9:
                if lot['units'] <= rem + 1e-9:
                    lot['account'] = to
                    rem -= lot['units']
                else:
                    part = dict(lot, units=rem, account=to)
                    lot['units'] -= rem
                    rem = 0.0
                    out.append(lot)
                    out.append(part)
                    continue
            out.append(lot)
        self._grants = out
        return units - max(rem, 0.0)

    def open(self, units: float, cost: Any, *, date: str, settle: Optional[str],
             lot_date: Optional[str] = None, deferred: Decimal = _ZERO,
             unknown_from: Optional[Iterable[Mapping[str, Any]]] = None,
             carried: Decimal = _ZERO,
             cuts: Sequence[Tuple[Decimal, Decimal]] = ()) -> None:
        """A purchase (or a short's opening sale) of `units` (signed) for
        `cost` (a short's: its entry proceeds) joins the position, or begins
        it. With it: `deferred`, an exercised option's denied loss in the
        premium (TJ-0061); `unknown_from` given, units of unknown cost
        (TJ-0947); `carried` and `cuts`, the share of a spin-off parent's
        denied losses, and of what a return of capital took of them, that
        waited for these new shares (TJ-0062, TJ-1274); and a denied loss's
        addition waiting on the pool, when the position is long
        (`take_pending`).

        The position's start is seeded on the trade that re-opens the pool
        (cleared at the last drain; later buys the same way do not reset
        it). `lot_date` given: an opening position's row, not a purchase
        (CA-OPEN-01): the last-acquisition date (the window signal) is not
        moved, and the days held of a sale from an opening-only pool count
        from the lot date (`opening_acq`). Else `date` is the last
        acquisition, and `settle` the same on the s.54 window's basis
        (CA-SL-01, A2-0958)."""
        self._total_cost += D(cost)
        if unknown_from is not None:
            self.taint(sources=unknown_from)
        if deferred > _NANO:
            self.tally_denied(deferred)
        if carried > _NANO:
            self.tally_denied(carried)
        for _cm, _cl in cuts:
            self.add_wash_cut(_cm, _cl)
        self.take_pending(units)
        if self._position_start_date is None:
            self._position_start_date = lot_date if lot_date is not None else date
        self._qty += units
        if lot_date is not None:
            self._opening_acq = max(
                self._opening_acq or '',
                lot_date)
        else:
            self._last_acq_date = date
            self._last_acq_settle = settle

    def take_pending(self, units: float) -> None:
        """A superficial loss's addition waiting on the pool (CA-SL-19,
        `park`) joins the cost only when a row of `units` (signed) leaves
        the pool long: it raises that long position's ACB, so the next
        disposition recovers the loss. A purchase's and an opening balance's
        alike (TJ-2768: the opening balance added its units without it and
        the denied loss was lost; its own cost stays unknown), and units
        that arrive with no purchase: an own move's, a listing journal's
        (`move_in`, `move_in_from`, `move_beyond`; TJ-4256). A row that
        leaves the pool flat or short leaves it parked: a short is an
        obligation, not property held at day 30, and has no ACB to raise
        (CA-SL-07); the amount waits for the first row that leaves the pool
        long (the owner's ruling on TJ-4258, 2026-11-18). Nothing waits on
        a long position: there this changes nothing."""
        if self._qty + units <= _LONG_NOISE * max(abs(self._qty), abs(units)):
            return
        self._join_pending()

    def _join_pending(self) -> None:
        """The parked addition joins the long position's cost (it is in the
        denied-loss tally already: `park`'s caller tallied it)."""
        _pw, self._pending_wash = self._pending_wash, _ZERO
        if _pw > _NANO:
            self._total_cost += D(_pw)

    def _unpark_tally(self) -> Decimal:
        """Before units leave with their share of the denied losses: the
        parked addition out of the tally (it is in no cost, so no share of
        it leaves; TJ-4258). Returns it for `_repark_tally`."""
        _pw = self._pending_wash
        if _pw:
            self._deferred_wash -= _pw
        return _pw

    def _repark_tally(self, parked: Decimal) -> None:
        """The parked addition `_unpark_tally` took out, back in the tally."""
        if parked:
            self._deferred_wash += parked

    @property
    def denied_in_cost(self) -> Decimal:
        """The denied losses the cost holds: the tally (`deferred_wash`)
        less an addition parked on the pool (`pending_wash`), which no cost
        holds until a long position takes it (TJ-4258). What a disposition
        releases is a share of this."""
        _pw = self._pending_wash
        return self._deferred_wash - _pw if _pw else self._deferred_wash

    def add_cost(self, amount: Any) -> None:
        """An ADJUST's amount as the walk signed it for this pool (a return
        of capital negative; a denied loss's addition; on a short, the short
        seller's payment) joins the cost."""
        self._total_cost += D(amount)

    def park(self, amount: Decimal) -> None:
        """A denied loss's addition that meets a flat pool waits on it
        (`pending_wash`): it belongs to the long position the pool next
        opens (`take_pending`; CA-SL-19)."""
        self._pending_wash = (
            self._pending_wash
            + amount)

    def tally_denied(self, amount: Decimal) -> None:
        """Count `amount` of denied losses among the dollars of the cost
        (`deferred_wash`: a superficial-loss addition placed here, or the
        share a spin-off's parent gave up to this child), so that the
        inventory and the views can tell deferred loss from purchase cost."""
        self._deferred_wash = (
            self._deferred_wash
            + amount)

    def give_up(self, share: Decimal) -> Tuple[Decimal, Decimal]:
        """The ACB a spin-off's parent gives up carries `share` of its
        denied-loss tally to the new shares (TJ-0062), and of what a return
        of capital took of it (TJ-1274): both leave this pool; returns (the
        tally's part, the cut's part). Of the tally, only what a cost holds
        (`denied_in_cost`): an addition parked on the pool is in no ACB the
        parent gives up, and waits on for the long position (TJ-4258,
        TJ-4414)."""
        moved = self.denied_in_cost * share
        self._deferred_wash -= moved
        cut = self._wash_cut * share
        if cut:
            self._wash_cut -= cut
        return moved, cut

    def cut_for_roc(self) -> None:
        """After a return of capital on a long position: the s.53(1)(f)
        additions are part of the ACB, so what the return of capital leaves
        of it (nil past it) is all of them the pool still holds, and a later
        sale does not count them again (CA-RPT-16; backlog TJ-1017). What it
        took is kept apart (`wash_cut`): a reversal brings it back, and the
        sale reports it (`deferred_loss_cut`), the ACB having been short by
        any premium it held as well (TJ-1217, TJ-1219)."""
        _acb_left = max(_ZERO, M.of(self._total_cost))
        if self._deferred_wash > _acb_left:
            _new_cut = self._deferred_wash - _acb_left
            _had_cut = self._wash_cut
            # A premium delivered since the last cut
            # is in the ACB this one reads.
            _late = max(_ZERO, self._cut_late
                        * _had_cut - _new_cut)
            self._wash_cut = _had_cut + _new_cut
            self._cut_late = _late / self._wash_cut
            self._deferred_wash = _acb_left

    def restore_roc(self) -> None:
        """A return of capital reversed: the ACB it gives back holds the
        additions it took (TJ-1219), up to what the ACB holds beyond the
        additions already counted. It returns none of a premium delivered
        since the cut: `cut_late` is per unit of the cut, so it keeps that
        premium's total (TJ-1333)."""
        _back = min(self._wash_cut, max(
            _ZERO, M.of(self._total_cost)
            - self._deferred_wash))
        _late = (self._cut_late
                 * self._wash_cut)
        self._wash_cut -= _back
        if self._wash_cut > _NANO:
            self._cut_late = _late / self._wash_cut
        self._deferred_wash = (
            self._deferred_wash + _back)

    def reset_negative(self) -> Decimal:
        """A return of capital drove the ACB below zero (ITA s.40(3)): the
        ACB is nil; returns the excess, the deemed gain."""
        excess = M.of(-self._total_cost)
        self._total_cost = Decimal(0)
        return excess

    def late_premium(self, premium: Decimal) -> None:
        """A put's premium (negative: it lowers the cost of the shares it
        delivers) arriving after a cut: it is in no ACB the cut read, so it
        is kept per unit of the cut (`cut_late`; `add_wash_cut`)."""
        self._cut_late = (self._cut_late
                            - premium / self._wash_cut)

    def move_in(self, units: float, date: str) -> None:
        """Units that arrive with no purchase row (negative: written
        contracts): a move from another of the person's accounts, an opening
        from before the data. They join the position, which begins on `date`
        when the pool held none; their cost is the caller's to say (`taint`:
        unknown in this book). A denied loss's addition waiting on the pool
        joins them when they leave it long, as it joins a purchase
        (`take_pending`, CA-SL-19; TJ-4256: an own move in opened the
        position without it and the denied loss never came back)."""
        self.take_pending(units)
        if self._position_start_date is None:
            self._position_start_date = date
        self._qty += units

    def move_beyond(self, units: float, date: str, *, restart: bool) -> None:
        """Units out (`units` signed) past what the pool holds, in an own move
        or a journal, or the same units arriving where they went: they have
        no purchase in this book, so the pool is short (or long) by them at
        an unknown cost. `restart`: the position begins on `date` (the
        caller's test: an own move's pool that is not short, a journal's
        that is flat). What waits on the pool joins the position they open
        when it is long; a short they leave takes none and it waits on
        (`take_pending`, TJ-4256, TJ-4258)."""
        self.take_pending(units)
        if restart:
            self._position_start_date = date
        self._qty += units
        self.taint()

    def move_out(self, units: float, how: str, sign: float = 1.0
                 ) -> Tuple[Decimal, Decimal, Decimal]:
        """`units` of the position leave with no sale, at the pool's average
        cost, taking their share of the denied losses in it and of what a
        return of capital took of them. Returns (the cost, the denied losses,
        the cut) that left; the last two are 0 for 'average', which scales
        them in place. Two spellings, kept as they were written, so that no
        figure moves by a rounding:

        'average' (a move to another of the person's accounts, a long's
        units): the cost is total x units / held, multiplied first; the
        denied losses and the cut are scaled by 1 - units / held, a float.

        'fraction' (a listing journal; `sign` -1 for a short's units): the
        fraction units / held first, the cost, the denied losses and the cut
        each times it, and each taken off."""
        if how == 'average':
            held = self._qty
            out_cost = self._total_cost * D(units) / D(held)
            self._total_cost -= out_cost
            _pw = self._unpark_tally()
            self._deferred_wash = (self._deferred_wash
                                     * M.of(max(0.0, 1.0 - units / held)))
            self._repark_tally(_pw)
            if self._wash_cut:
                self._wash_cut *= M.of(max(0.0, 1.0 - units / held))
            self._qty = held - units
            return out_cost, _ZERO, _ZERO
        if how != 'fraction':
            raise ValueError(f"AcbPool.move_out: how is 'average' or 'fraction', not {how!r}")
        held = self._qty * sign
        frac = D(units) / D(held)
        cost = self._total_cost * frac
        dw = self.denied_in_cost * M.of(frac)
        self._total_cost -= cost
        self._deferred_wash = self._deferred_wash - dw
        cut = self._wash_cut * M.of(frac)
        if cut:
            self._wash_cut -= cut
        self._qty -= units * sign
        return cost, dw, cut

    def move_out_lots(self, units: float, account: str) -> Tuple[float, Decimal]:
        """Up to `units` written contracts of `account` leave a short option
        pool with their own lots' premium (a move to another of the person's
        accounts: no close, no gain), first written first. Returns (the
        units its lots held, their premium)."""
        held = abs(self._qty)
        taken, premium = 0.0, _ZERO
        kept: List[Dict[str, Any]] = []
        for lot in self._grants or []:
            take = min(units - taken, lot['units']) if lot.get('account') == account else 0.0
            if take > 1e-9:
                taken += take
                premium += M.of(take) * lot['per_unit']
                lot['units'] -= take
            if lot['units'] > 1e-9:
                kept.append(lot)
        self._grants = kept
        self._total_cost -= D(premium)
        if held > 1e-9 and taken > 0:
            _pw = self._unpark_tally()
            self._deferred_wash = (self._deferred_wash
                                     * M.of(max(0.0, 1.0 - taken / held)))
            self._repark_tally(_pw)
            if self._wash_cut:
                self._wash_cut *= M.of(max(0.0, 1.0 - taken / held))
        self._qty += taken
        return taken, premium

    def move_in_from(self, src: AcbPool, units: float, sign: float, cost: Decimal,
                     dw: Decimal, cut: Decimal, f: Optional[Decimal], eps: float) -> None:
        """A listing journal's units arrive from `src` (`src.move_out`'s
        cost, denied losses and cut, each restated by the factor `f`; None:
        no rate on file, the cost is unknown here): they join this listing's
        pool with the position's start (the earlier of the two), their
        acquisition dates (the later of the two listings', `_merge_dates`:
        CA-XLIST-01, backlog TJ-4241) and the unknown-cost units' causes.
        Into a position facing the other way they are of unknown cost. A
        denied loss's addition waiting on this listing's pool joins them
        when they leave it long (`take_pending`, CA-SL-19; TJ-4256, TJ-4258)."""
        self.take_pending(units * sign)
        if self._qty * sign < -eps:
            self.taint()                  # into an opposite position
        elif abs(self._qty) <= eps or (
                src._position_start_date and (
                    self._position_start_date is None
                    or src._position_start_date
                    < self._position_start_date)):
            self._position_start_date = src._position_start_date
        self._qty += units * sign
        if f is None:
            self.taint()
        else:
            self._total_cost += cost * f
            self._deferred_wash = (self._deferred_wash
                                     + dw * M.of(f))
            if cut:
                self.add_wash_cut(cut * M.of(f), src._cut_late)
        self._merge_dates(src)
        self.taint_from(src)

    def _merge_dates(self, other: AcbPool) -> None:
        """The acquisition dates of two pools merged into this one (a
        rename into a ticker already held, CA-ACB-RENAME; a listing
        journal into a listing already held, CA-XLIST-01): the LATER of
        the two last purchases (`last_acq_date`, with its `last_acq_settle`)
        and the later of the two opening lots' dates (`opening_acq`,
        CA-OPEN-01), as one pool holding both purchases shows (`add`: each
        purchase moves the date on; the opening lots take the `max`). The
        planning views measure the 30-day window from `last_acq_date`, the
        most recent acquisition: the earlier of the two read a holding
        bought into 28 days ago as clear of it (backlog TJ-4241). Only a
        side with no real date gives way, date by date: the sentinel (no
        purchase: a new ticker, a pool no trade made) to the other's
        purchase, no opening lot to the other's lot. A side holding no
        units keeps its real dates, as one pool sold out keeps them for its
        next units (a ticker sold out after a purchase, renamed into: that
        purchase still shows when it is the later; giving way whole showed
        the source's older one, TJ-4413). The settlement date goes with
        the purchase that shows; on one trade date, the later of the two
        settlement dates, as one pool walking both purchases in settlement
        order shows (the target's own kept the window's settle date three
        days early: backlog TJ-2722). The position's start, the continuous
        holding, is not this: the merges keep the earlier."""
        theirs = other._last_acq_date or _NO_PURCHASE
        mine = self._last_acq_date or _NO_PURCHASE
        if theirs != _NO_PURCHASE and (mine == _NO_PURCHASE or theirs > mine):
            self._last_acq_date = theirs
            self._last_acq_settle = other._last_acq_settle
        elif (theirs != _NO_PURCHASE and theirs == mine
              and (other._last_acq_settle or '') > (self._last_acq_settle or '')):
            self._last_acq_settle = other._last_acq_settle
        if other._opening_acq and (not self._opening_acq
                                   or other._opening_acq > self._opening_acq):
            self._opening_acq = other._opening_acq

    def leftover(self, units: float, cost: Decimal, *, deferred: Decimal,
                 date: str, settle: str,
                 unknown_from: Optional[Iterable[Mapping[str, Any]]] = None) -> None:
        """The part of a trade past zero: the position it closed is gone and
        the pool is the new one, facing the other way, of `units` (signed) at
        `cost` (a short's: its entry proceeds), the trade's own share of what
        it brought: `deferred` (an exercised option's denied loss in its
        premium, TJ-0061) and, `unknown_from` given, units of unknown cost
        (delivered by an option held at one, TJ-0947: its OPENING rows the
        source). Nothing of the closed position stays: no cut of its denied
        losses, no written lots; but a denied loss's addition parked on the
        short it closed stays tallied, and joins this position when it is
        long (`take_pending`; TJ-4258). It begins at this trade: `date` is
        its last acquisition and its start, `settle` the same on the s.54
        window's basis."""
        self._qty = units
        self._total_cost = cost
        self._deferred_wash = deferred
        if self._pending_wash:
            self._repark_tally(self._pending_wash)
            if units > 0:
                self._join_pending()
        self._wash_cut = _ZERO
        if unknown_from is not None:
            self.taint(sources=unknown_from)
        self._last_acq_date = date
        self._last_acq_settle = settle
        self._position_start_date = date
        self._grants = []

    def split(self, ratio: float) -> None:
        """A split or consolidation: the units re-denominated by `ratio`, the
        cost as it was. A short option pool's written lots
        follow the contract's re-denomination: the same premium per lot, so
        `ratio` times the units at the premium per unit over `ratio`."""
        self._qty *= ratio
        if ratio and self._grants:
            for _l in self._grants:
                _l['units'] *= ratio
                _l['per_unit'] /= M.of(ratio)

    def merge(self, other: AcbPool, symbol: str, target_symbol: str) -> None:
        """A rename's source pool `other` (under `symbol`) merged into this
        one, the pool of the new ticker `target_symbol` (a SPLIT with
        symbol_new; the long pool's and the short pool's alike, TJ-3193):
        cost and units add, the written lots join in write order, taint
        sticks if either side was tainted (and its cause, an own move's
        units: backlog TJ-0279), a parked denial rides along, the later
        acquisition dates are kept (`_merge_dates`, backlog TJ-4241) and the
        earlier position start. A currency
        mix would be an upstream bug, and a long merged into a short (or the
        reverse) is a close-out the engine does not net: both refused."""
        if self._currency and other._currency \
                and self._currency != other._currency:
            raise ValueError(
                f"SPLIT {symbol}→{target_symbol}: "
                f"currency mismatch "
                f"{other._currency!r} vs "
                f"{self._currency!r}. Run "
                f"taxjson-convert-currency first."
            )
        if (self._qty * other._qty
                < -1e-9):
            # Merging a LONG pool into a SHORT
            # one (or vice versa) is a close-out
            # event, not an addition — blind
            # summing turned short-sale proceeds
            # into phantom COST (FUZZ #F10).
            # Refuse loudly rather than corrupt.
            raise ValueError(
                f"SPLIT {symbol}→{target_symbol}"
                f": rename would merge a "
                f"{'LONG' if other._qty > 0 else 'SHORT'}"
                f" position into an existing "
                f"{'LONG' if self._qty > 0 else 'SHORT'}"
                f" pool — that is a close-out, "
                f"which this engine does not "
                f"net automatically. Record the "
                f"close explicitly (e.g. a .tt "
                f"BUYSELL) before the rename.")
        self._qty += other._qty
        self._total_cost += other._total_cost
        self._deferred_wash = (
            self._deferred_wash
            + other._deferred_wash)
        if other._wash_cut:
            self.add_wash_cut(other._wash_cut,
                              other._cut_late)
        self._grants = sorted(
            self._grants
            + other._grants,
            key=lambda _l: _l.get('seq', 0))
        # Parked deferral dollars ride the rename too: dropping them erased
        # the denied loss's future recovery (2026-09 adversarial audit).
        # Added to the TARGET pool's cost when the merge leaves it long, else
        # kept parked: a short takes none, the addition is the long
        # position's (CA-SL-07; the owner's ruling on TJ-4258). The target's
        # own parked dollars count too: a flat target the rename brings
        # shares into opens here, as a purchase opens a flat pool; left
        # parked they denied the loss for good (issue #91, CA-SL-19).
        # deferred_wash already holds both sides (summed above): only the
        # cost moves.
        _pw = other._pending_wash + self._pending_wash
        other._pending_wash = self._pending_wash = _ZERO
        if _pw > _NANO:
            _reps = pool_qty_eps(target_symbol, self._qty)
            if self._qty > _reps:  # cov: a2-1596-rename-pending-wash
                self._total_cost += D(_pw)
            else:
                self._pending_wash = _pw
        # (and its cause: an own move's
        # units, backlog TJ-0279)
        self.taint_from(other)
        # The LATER of the two last purchases and of the two opening lots
        # (CA-ACB-RENAME, CA-OPEN-01; owner's ruling on backlog TJ-4241):
        # the merged holding is one pool, which shows its most recent
        # purchase, whichever side made it. The planning
        # views read `last_acq_date` as that purchase, the start of the
        # 30-day window: the earlier date (kept here before) read a holding
        # bought into 28 days ago as clear. Only a side with no real date
        # gives way: the sentinel ('1970-01-01') of a pool no purchase made
        # (it once overwrote the target's real date), so a rename onto a
        # new ticker still carries the source's dates (s.85.1(5)); a side
        # sold out keeps its last purchase, as one pool does (TJ-4413).
        self._merge_dates(other)
        self.stamp_currency(other._currency)
        # Preserve the EARLIEST position_start
        # across the merged pools — the combined
        # position's continuous-holding date is
        # whichever side was entered first.
        src_psd = other._position_start_date
        ex_psd = self._position_start_date
        if src_psd and (not ex_psd or src_psd < ex_psd):
            self._position_start_date = src_psd

    def stamp_currency(self, currency: str) -> None:
        """One ACB pool, one currency: a pool that has none yet takes
        `currency` (its first trade's, a journal leg's, a renamed pool's)."""
        if not self._currency:
            self._currency = currency

    def add_wash_cut(self, cut: Decimal, late: Decimal) -> None:
        """Add `cut` to the pool's `wash_cut` (what a return of capital took
        of the denied-loss tally), `late` being its `cut_late`: the premium
        delivered into the pool since the cut, per unit of the cut, which
        scales with it. That premium was not in the ACB the cut read
        (backlog TJ-1309)."""
        had = self._wash_cut
        self._cut_late = ((self._cut_late * had + late * cut)
                            / (had + cut))
        self._wash_cut = had + cut

    def add_sources(self, sources: Iterable[Mapping[str, Any]]) -> None:
        """Keep the OPENING cost=unknown rows on the pool (`unknown_from`),
        each once; nothing else about the pool changes."""
        for _src in sources:
            _uf = self._unknown_from
            if _src not in _uf:
                _uf.append(dict(_src))

    def taint(self, moved_from: Optional[str] = None,
              source: Optional[Mapping[str, Any]] = None,
              sources: Iterable[Mapping[str, Any]] = ()) -> None:
        """Mark the pool as holding units of unknown cost. `moved_from`
        names the account an own move brought them from (_own_move_units);
        any other cause (an opening at an unknown cost, units out beyond
        the pool) is `taint_other`. A sale from a pool whose unknown units
        all came by own moves says so (`own_move_from`): their cost is in
        the blended figures, never a purchase missing from the files
        (backlog TJ-0279). `source`: the OPENING cost=unknown row the units
        came by (_opening_source), kept on the pool as `unknown_from` for
        its dispositions' records (TJ-3209); `sources`: several at once."""
        self._tainted = True
        self.add_sources(([source] if source is not None else []) + list(sources))
        if moved_from is None:
            self._taint_other = True
            return
        _mf = self._moved_from
        if moved_from not in _mf:
            _mf.append(moved_from)

    def taint_from(self, src: AcbPool) -> None:
        """The pool takes `src`'s unknown-cost units (a journal, a rename
        into an existing pool): their taint and its cause."""
        if not src._tainted:
            return
        if src._taint_other or not src._moved_from:
            self.taint()
        for _a in src._moved_from or []:
            self.taint(_a)
        # (the OPENING rows behind the units, as they are: a pool an own
        # move alone tainted carries none, and its cause stays the move's:
        # TJ-3235)
        self.add_sources(src._unknown_from or [])

    def untaint(self) -> None:
        """The pool's unknown-cost units are gone (it drained flat): the
        mark and its causes."""
        self._tainted = False
        self._moved_from = []
        self._taint_other = False
        self._unknown_from = []

    def _copy(self) -> AcbPool:
        out = type(self).__new__(type(self))
        for slot in self.__slots__:
            object.__setattr__(out, slot, getattr(self, slot))
        return out

    def snapshot(self, account_premiums: Optional[Dict[str, Any]],
                 account_lots: Optional[Dict[str, Any]]) -> AcbPool:
        """The pool as it stands, for the year end (`_pools_copy`): its
        units, cost, currency and denied losses, and each account's written
        premium and contracts as the caller tallied them; no other field."""
        out = type(self)(self._currency)
        out._qty, out._total_cost, out._deferred_wash = (
            self._qty, self._total_cost, self._deferred_wash)
        out._account_premiums, out._account_lots = account_premiums, account_lots
        return out

    def part(self, qty: float, total_cost: Decimal, deferred_wash: Decimal,
             grants: Optional[List[Dict[str, Any]]] = None) -> AcbPool:
        """A copy for the reports with one account's part of this short
        pool's units, proceeds and denied losses (and its own written lots,
        `grants`, when it has them): `_short_account_part`."""
        out = self._copy()
        out._qty, out._total_cost, out._deferred_wash = qty, total_cost, deferred_wash
        if grants is not None:
            out._grants = grants
        return out

    def netted(self, short: AcbPool) -> AcbPool:
        """A copy for the reports with `short`'s units, cost and denied
        losses added to this pool's (`_fold_short_futures_pools`)."""
        out = self._copy()
        out._qty = self._qty + short._qty
        out._total_cost = self._total_cost + short._total_cost
        out._deferred_wash = M.of(self._deferred_wash) + M.of(short._deferred_wash)
        return out

    def __deepcopy__(self, memo: Dict[int, Any]) -> AcbPool:
        """A deep copy of every field: `PoolBook.save`'s checkpoint keeps
        one copy per pool object through its memo (a spin-off's child is the
        book's own pool; `copy.deepcopy` records this copy there itself) and
        its atomic rows and records."""
        out = type(self).__new__(type(self))
        for slot in self.__slots__:
            object.__setattr__(out, slot, copy.deepcopy(getattr(self, slot), memo))
        return out


for _f in _FIELDS:      # each field read as an attribute, written by no one outside
    setattr(AcbPool, _f, property(attrgetter('_' + _f)))


class PoolBook(Dict[str, AcbPool]):
    """The pools of one walk by key: a symbol, or its short pool
    (lib/futures.SHORT_POOL_SUFFIX), in the order the walk first opened
    them (the reports' order); and what a spin-off's parent gave up of its
    denied losses, on its way to the new shares (`give_up_to_child`,
    `child_opens`): by (account, corporate event), of the events `spinoffs`
    names (an ACB cut on one symbol, new shares of another)."""

    __slots__ = ('_cev_moves', '_cev_child', '_cev_stash', '_cev_cut')

    def __init__(self, spinoffs: Iterable[Tuple[str, str]] = ()) -> None:
        super().__init__()
        self._cev_moves = frozenset(spinoffs)
        # The new shares' pool, once they arrived; until then the denied
        # losses waiting for them, and what a return of capital took.
        self._cev_child: Dict[Tuple[str, str], AcbPool] = {}
        self._cev_stash: Dict[Tuple[str, str], Decimal] = {}
        self._cev_cut: Dict[Tuple[str, str], List[Tuple[Decimal, Decimal]]] = {}

    def is_spinoff(self, event: Tuple[str, str]) -> bool:
        """Whether (account, corporate event) is a spin-off's set of rows."""
        return event in self._cev_moves

    def give_up_to_child(self, event: Tuple[str, str], parent: AcbPool, share: Decimal) -> None:
        """A spin-off's parent gave up `share` of its ACB: the same share of
        its denied losses goes to the new shares (TJ-0062), and of what a
        return of capital took of them (TJ-1274): to their pool when they
        have arrived, else kept for `child_opens`."""
        moved, cut = parent.give_up(share)
        child = self._cev_child.get(event)
        if child is not None:
            child.tally_denied(moved)
            if cut:
                child.add_wash_cut(cut, parent._cut_late)
        else:
            self._cev_stash[event] = self._cev_stash.get(event, _ZERO) + moved
            if cut:
                self._cev_cut.setdefault(event, []).append((cut, parent._cut_late))

    def child_opens(self, event: Tuple[str, str], child: AcbPool
                    ) -> Tuple[Decimal, Sequence[Tuple[Decimal, Decimal]]]:
        """A spin-off's new shares arrive in `child`: what the parent gave
        up before them (the denied losses, and the cuts with their late
        shares), for `AcbPool.open`; what it gives up later goes to `child`
        directly."""
        self._cev_child[event] = child
        return self._cev_stash.pop(event, _ZERO), self._cev_cut.pop(event, ())

    def save(self, memo: Dict[int, Any]) -> PoolBook:
        """A deep copy for `_WalkFold`'s checkpoint, inside its one deep
        copy (`memo`, which keeps the rows and records atomic): every pool
        once (a rename's aliases, and a spin-off's child, stay the same
        object) and what waits for a spin-off's new shares."""
        out = type(self)(self._cev_moves)
        for k, v in self.items():
            out[k] = copy.deepcopy(v, memo)
        out._cev_child, out._cev_stash, out._cev_cut = copy.deepcopy(
            (self._cev_child, self._cev_stash, self._cev_cut), memo)
        return out

    def restore(self, state: PoolBook) -> None:
        """Take `state` (a `save`, copied again by the caller) in place: the
        walk and the cost timeline's recorders hold this book."""
        self.clear()
        self.update(state)
        self._cev_child, self._cev_stash, self._cev_cut = (
            state._cev_child, state._cev_stash, state._cev_cut)

    def ensure(self, key: str, currency: str = '') -> AcbPool:
        """The pool under `key`, a new one (`AcbPool.new`) when the walk has
        none there yet."""
        pool = self.get(key)
        if pool is None:
            pool = self[key] = AcbPool.new(currency)
        return pool

    def rename(self, old: str, new: str, pool: AcbPool) -> None:
        """`pool`, kept under `old`, is kept under `new` from here on (a
        ticker change with no pool at the new ticker; a split's short pool
        under its own key again): the last of the book's keys."""
        del self[old]
        self[new] = pool

    def __deepcopy__(self, memo: Dict[int, Any]) -> PoolBook:
        return self.save(memo)
