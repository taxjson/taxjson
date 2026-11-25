"""The marked-short router: which rows go to a symbol's marked-short pool, and
a move's legs placed.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib.stage_msg import emit_line
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, TypeAlias

from taxjson.lib.option_symbols import is_option_symbol, parse_option_expiry
from taxjson.lib.row_types import is_opening_row
from taxjson.lib.pool_eps import pool_qty_eps
from taxjson.model.transaction import TaxTransaction
from taxjson.engine.journal import _is_listing_journal
from taxjson.engine.rows import not_a_purchase

from taxjson.engine.ca.pools import _ShortUnits
from taxjson.engine.ca.run_helpers import _futures_names
from taxjson.engine.ca.own_moves import _move_sign, _is_lot_move

if TYPE_CHECKING:
    from taxjson.engine.rows import RowId
    from taxjson.engine.ca.pool import PoolBook

# Each account's units on one side ({(symbol, account): units}): the
# router's `short` and `long`.
_Positions: TypeAlias = Dict[Tuple[str, str], float]
# (short, long): what the re-fold's checkpoint copies (fold_state).
_RouterState: TypeAlias = Tuple[_Positions, _Positions]
# A move's reading (move_pair): (kind, sender, receiver, units, note).
_Reading: TypeAlias = Tuple[str, str, str, float, str]
# A pending move leg (_drain_move_legs): (key, order, item), the item a leg
# row or ('leg', row), a share leg's deferred leave.
_PendingLeg: TypeAlias = Tuple[Tuple[Any, ...], int, Any]
# A row's order key for the router (event_sort_key, or _router_move_key).
_SortKey: TypeAlias = Callable[[TaxTransaction], Tuple[Any, ...]]


class _MarkedShortRouter:
    """Which rows of the Canadian walk belong to a symbol's marked-short
    pool (lib/futures.SHORT_POOL_SUFFIX; CA-DISP-04, backlog TJ-0721):
    each account's units short there and its units in the pooled long,
    so an unmarked purchase covers the account's own short only while it
    holds no long itself, and a sale coded C;O closes the account's own
    long and opens a short only past it. A row that does both is cut in
    two first (_split_crossing_marked_rows), so a row belongs wholly to
    one pool.

    Which purchase closes a marked short follows the broker's convention,
    which each row carries (`short_marks`, stamped from its parser; backlog
    TJ-4993, TJ-1357):
      - a broker that marks a short account's trades in the description
        (RBC: "SHORT." / "COVER SHORT."): only the marked
        purchase closes the short, and every unmarked purchase of that
        broker's exports is a long purchase, whatever file it is in;
      - any other row (IB, whose account holds one netted position;
        Questrade, whose marking is not known, TJ-5236; a .tt line; the
        generic importer): the reading above, a purchase covers while the
        account is short and holds no long.
    `note` remembers a purchase read as a cover by that last reading, and
    a marked cover that then finds less short than it buys is one of
    `late_covers`; `both_open` lists the accounts that end long and short
    the same shares. The run says each (`_say_late_covers`,
    `_say_both_open`): never silent.

    An option series' written contracts are that pool too (CA-OPT-12,
    backlog TJ-2735): a written option is an obligation of the account
    that wrote it, not property, so the account's own position decides
    with no broker marker: a sale past the account's own bought
    contracts writes, a purchase covers the account's own written
    contracts first, and a purchase in another account never closes
    them. Bought contracts stay the symbol's one pool across the
    taxable accounts, as shares are (CA-ACB-01)."""

    def __init__(self) -> None:
        from taxjson.lib.missing_history import _BROKER_SHORT_RE
        from taxjson.lib.row_types import open_close_codes
        self._short_re = _BROKER_SHORT_RE
        self._codes = open_close_codes
        # The last unmarked purchase read as a cover, by position (`note`), and
        # each (that purchase, the marked cover that found the short gone).
        self._fallback: Dict[Tuple[str, str], TaxTransaction] = {}
        self.late_covers: List[Tuple[TaxTransaction, TaxTransaction]] = []
        self.short: _Positions = _ShortUnits()      # by symbol too (TJ-4978)
        self.long: _Positions = {}
        # A close row's contracts past the account's own (_is_close_row),
        # by row id: they close nothing and open nothing (TJ-3303, TJ-3302).
        self.excess: Dict[RowId, float] = {}

    @staticmethod
    def _is_close_row(t: TaxTransaction) -> bool:
        """An option row that only closes the account's own contracts: an
        assignment or exercise (an ASSIGN row), or an expiry (a BUYSELL
        with no money dated on or after the series' expiry, as the row
        check reads one) in an account holding the contracts it expires;
        never a position opened, so one past the account's own contracts
        has an excess. A no-money BUYSELL before the expiry, or in an
        account holding none, cannot be told from a purchase or sale at 0
        and stays one (_walk_option)."""
        if t.action == 'ASSIGN':
            return True
        if (t.action != 'BUYSELL' or not_a_purchase(t)
                or abs(t.price or 0) >= 0.005 or abs(t.net_amount or 0) >= 0.005):
            return False
        exp = parse_option_expiry(t.symbol or '')
        if not exp:
            return False
        return (t.date or '') >= exp

    def _opens_short(self, t: TaxTransaction) -> bool:
        d = (t.description or '').upper()
        if 'COVER SHORT.' in d:
            return False
        return (bool(self._short_re.search(d))
                or self._codes(t) == ('O',))

    def _covers(self, t: TaxTransaction) -> bool:
        return (bool(self._short_re.search((t.description or '').upper()))
                or 'C' in self._codes(t))

    def _both_coded(self, t: TaxTransaction) -> bool:
        return set(self._codes(t)) == {'C', 'O'}

    @staticmethod
    def _is_stock(t: TaxTransaction) -> bool:
        return not (is_option_symbol(t.symbol)
                    or (t.type or '') == 'futures_settlement')

    @staticmethod
    def _is_option(t: TaxTransaction) -> bool:
        return is_option_symbol(t.symbol)

    def routes(self, t: TaxTransaction) -> bool:
        """Whether the positions read row `t` (a share or an option row)."""
        return self._is_stock(t) or self._is_option(t)

    @staticmethod
    def _is_trade(t: TaxTransaction) -> bool:
        return t.action == 'BUYSELL' and not not_a_purchase(t)

    def _wants_short(self, t: TaxTransaction, q: float, k: Tuple[str, str], eps: float) -> bool:
        if q < 0:
            if self._opens_short(t):
                return True
            return (self._both_coded(t)
                    and self.long.get(k, 0.0) <= eps)
        return (q > 0 and self.short.get(k, 0.0) > eps
                and (self._covers(t)
                     or (self._codes(t) != ('O',)
                         and (t.short_marks or '') != 'description'
                         and self.long.get(k, 0.0) <= eps)))

    def note(self, t: TaxTransaction) -> None:
        """Before row `t` (as the broker gave it, uncut) is walked: remember an
        unmarked share purchase about to be read as a cover, and record a
        marked cover that finds less short than it buys after such a purchase
        (`late_covers`; TJ-5235). A short the account opens afterwards is a
        new one: what closes it says nothing of the remembered purchase."""
        q = float(t.quantity or 0.0)
        if not q or not (self._is_stock(t) and self._is_trade(t)):
            return
        k = (t.symbol, t.account)
        eps = pool_qty_eps(t.symbol, q)
        if q < 0:
            if self._wants_short(t, q, k, eps):
                self._fallback.pop(k, None)
            return
        short = self.short.get(k, 0.0)
        if self._covers(t):
            if short < q - eps and k in self._fallback:
                self.late_covers.append((self._fallback.pop(k), t))
        elif short > eps and self._wants_short(t, q, k, eps):
            self._fallback[k] = t

    def both_open(self) -> List[Tuple[str, str, float, float]]:
        """(symbol, account, units held, units short) of each account that
        holds shares and is short the same shares, as the walk stands."""
        out = []
        for (sym, acct), units in self.short.items():
            held = self.long.get((sym, acct), 0.0)
            eps = pool_qty_eps(sym, units, held)
            if units > eps and held > eps and not is_option_symbol(sym):
                out.append((sym, acct, held, units))
        return sorted(out)

    def crossing_units(self, t: TaxTransaction) -> Optional[float]:
        """The units of a trade's first part when it crosses from one
        pool to the other (a cover past the account's short, a C;O sale
        past its long; an option purchase past the account's written
        contracts, an option sale past its bought ones); None for a row
        that stays in one pool."""
        if self._is_option(t):
            if t.action != 'BUYSELL' or not_a_purchase(t) or self._is_close_row(t):
                return None             # (a close row's excess closes nothing: _walk_option)
            q = float(t.quantity or 0.0)
            k = (t.symbol, t.account)
            eps = pool_qty_eps(t.symbol, q)
            held = self.short.get(k, 0.0) if q > 0 else self.long.get(k, 0.0)
            return held if eps < held < abs(q) - eps else None
        if not (self._is_stock(t) and self._is_trade(t)):
            return None
        q = float(t.quantity or 0.0)
        k = (t.symbol, t.account)
        eps = pool_qty_eps(t.symbol, q)
        if q > 0 and self._wants_short(t, q, k, eps):
            units = self.short.get(k, 0.0)
            return units if q > units + eps else None
        if q < 0 and self._both_coded(t) and not self._opens_short(t):
            units = self.long.get(k, 0.0)
            return units if eps < units and -q > units + eps else None
        return None

    def walk(self, t: TaxTransaction) -> bool:
        """Book a row; True when it belongs to the short pool."""
        if self._is_option(t):
            return self._walk_option(t)
        if not self._is_stock(t):
            return False
        q = float(t.quantity or 0.0)
        k = (t.symbol, t.account)
        if self._is_trade(t):
            if self._wants_short(t, q, k, pool_qty_eps(t.symbol, q)):
                self.short[k] = self.short.get(k, 0.0) - q
                return True
            self.long[k] = self.long.get(k, 0.0) + q
        elif _is_lot_move(t) and q < 0:
            # Units moved out never leave less than none: a later buy
            # is a long of the account's own (backlog TJ-1180).
            self.long[k] = max(self.long.get(k, 0.0) + q, 0.0)
        elif t.action == 'OPENING_BALANCE' and q < 0:
            # A short held before the data (a .tt short line, TJ-0844):
            # never netted against the long the pool holds.
            self.short[k] = self.short.get(k, 0.0) - q
            return True
        elif (t.action in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE')
                or _is_lot_move(t) or _is_listing_journal(t)):
            # Held units that are no trade of this rule: they are the
            # account's long all the same.
            self.long[k] = self.long.get(k, 0.0) + q
        return False

    def _walk_option(self, t: TaxTransaction) -> bool:
        """An option row: the account's own position decides (CA-OPT-12,
        backlog TJ-2735). A sale past the account's bought contracts
        writes; a purchase covers the account's written contracts first;
        a row that does both was cut in two (crossing_units). An opening
        position (an OPENING_BALANCE row, a .tt OPENING line) is a short
        of its own when written, never netted against the account's
        bought contracts (as a share short's, TJ-0844; backlog TJ-3196);
        a move between two of the person's accounts carries written
        contracts to the receiving account (move_pair in the blended walk,
        move_leg_kind for an account's own leg; TJ-3192), else bought ones
        (TJ-1180). An assignment, exercise or expiry (_is_close_row) closes
        the account's own contracts only: its contracts past them are its
        `excess`, which the walk books as nothing (TJ-3273, TJ-3302,
        TJ-3303); a no-money BUYSELL in an account holding none of the
        contracts it would expire is a purchase or sale at 0."""
        q = float(t.quantity or 0.0)
        k = (t.symbol, t.account)
        if t.action == 'OPENING_BALANCE' or is_opening_row(t):
            if q < 0:
                self.short[k] = self.short.get(k, 0.0) - q
                return True
            self.long[k] = self.long.get(k, 0.0) + q
        elif t.action in ('BUYSELL', 'ASSIGN'):
            eps = pool_qty_eps(t.symbol, q)
            close = self._is_close_row(t)
            if q < 0:
                held = self.long.get(k, 0.0)
                if held > eps or (close and t.action == 'ASSIGN'):
                    if close and held < -q - eps:
                        self.excess[t.id] = -q - max(held, 0.0)
                    self.long[k] = max(held + q, 0.0)
                    return False
                self.short[k] = self.short.get(k, 0.0) - q
                return True
            held = self.short.get(k, 0.0)
            if q > 0 and (held > eps or (close and t.action == 'ASSIGN')):
                if close and held < q - eps:
                    self.excess[t.id] = q - max(held, 0.0)
                self.short[k] = max(held - q, 0.0)
                return True
            self.long[k] = self.long.get(k, 0.0) + q
        elif _is_lot_move(t):
            kind = self.move_leg_kind(t)
            if kind == 'written_out':
                self.move_out('written', t.symbol, t.account, abs(q))
                return True
            if kind == 'written_in':
                self.move_in('written', t.symbol, t.account, abs(q))
                return True
            self.long[k] = (max(self.long.get(k, 0.0) + q, 0.0) if q < 0
                            else self.long.get(k, 0.0) + q)
        elif _is_listing_journal(t):
            self.long[k] = (max(self.long.get(k, 0.0) + q, 0.0) if q < 0
                            else self.long.get(k, 0.0) + q)
        return False

    def move_leg_kind(self, t: TaxTransaction) -> str:
        """A lot-move leg of an option series read on its own (an account's
        own view, a replay of one account's rows), by the move's sign
        convention (_move_sign; backlog TJ-3224, TJ-3263). Signed by
        direction: a negative leg leaves ('written_out' when the account's
        short holds its units, else 'bought'), a positive one arrives (a
        bought contract: an arriving contract's kind is not knowable
        here). Signed by the position's change: a positive leg is a written
        contract leaving when the short holds its units, else a bought one
        arriving; a negative leg a bought contract leaving when the long
        holds its units, else a written one arriving ('written_in'). Not
        known: a short account's leg leaves written, a negative leg in a
        flat account arrives written, else bought by its sign."""
        q = float(t.quantity or 0.0)
        k = (t.symbol, t.account)
        eps = pool_qty_eps(t.symbol, q)
        sh, lg = self.short.get(k, 0.0), self.long.get(k, 0.0)
        sign = _move_sign(t)
        if sign == 'direction':
            return 'written_out' if q < 0 and sh >= -q - eps else 'bought'
        if sign == 'change':
            if q > 0:
                return 'written_out' if sh >= q - eps else 'bought'
            return 'bought' if lg >= -q - eps else 'written_in'
        if sh > eps:
            return 'written_out'
        if q < 0 and lg <= eps:
            return 'written_in'
        return 'bought'

    def move_readings(self, a: TaxTransaction, b: TaxTransaction) -> List[Tuple[str, str, str]]:
        """The readings of an own move's two legs (one description, one
        moment) that fit, each (kind, sender, receiver), in the rows' sign's
        own order (_move_sign, backlog TJ-3243): a reading fits when its
        sender holds at least the units moved of that kind (TJ-3260,
        TJ-3261). Signed by direction: the negative leg's account sends,
        written contracts when its short holds the units, bought ones when
        its long does. Signed by the position's change: the negative leg's
        account sends bought contracts, or the positive leg's account sends
        written ones. Not known: those and the direction reading (the
        negative leg's account sending written contracts), bought first."""
        n, p, units, eps = self._move_np(a, b)
        sym = a.symbol
        sh_n, sh_p = self.short.get((sym, n), 0.0), self.short.get((sym, p), 0.0)
        lg_n = self.long.get((sym, n), 0.0)
        sign = _move_sign(a)
        if sign == 'direction':
            readings = [('written', n, p, sh_n >= units - eps),
                        ('bought', n, p, lg_n >= units - eps)]
        else:
            readings = [('bought', n, p, lg_n >= units - eps),
                        ('written', n, p, sign != 'change' and sh_n >= units - eps),
                        ('written', p, n, sh_p >= units - eps)]
        return [r[:3] for r in readings if r[3]]

    @staticmethod
    def _move_np(a: TaxTransaction, b: TaxTransaction) -> Tuple[str, str, float, float]:
        """(the negative leg's account, the positive leg's, the units, the
        units' tolerance) of a move's two legs."""
        qa = float(a.quantity or 0.0)
        units = abs(qa)
        neg, pos = (a, b) if qa < 0 else (b, a)
        return neg.account, pos.account, units, pool_qty_eps(a.symbol, units)

    def move_pair(self, a: TaxTransaction, b: TaxTransaction, final: bool = False) -> _Reading:
        """(kind, sender, receiver, units, note) of an own move's two legs:
        the first reading that fits (move_readings), sure when it is the
        only one, note 'ambiguous' when more than one fits (the walk says
        a Warning). None fits: kind '', the move is read again later that
        day (TJ-3242, TJ-3256), or, with `final` (its day is over), the
        fallback: bought contracts leaving the negative leg's account,
        whatever the rows' sign and whatever the accounts hold (the
        receiver then holds a router long the pool does not), note
        'fallback' (a Warning too; backlog TJ-3269)."""
        n, p, units, _eps = self._move_np(a, b)
        fits = self.move_readings(a, b)
        if fits:
            return fits[0] + (units, 'ambiguous' if len(fits) > 1 else '')
        if not final:
            return '', n, p, units, ''
        return 'bought', n, p, units, 'fallback'

    def move_fits(self, reading: _Reading, sym: str) -> bool:
        """Whether a move's reading (move_pair) still fits: its sender holds
        at least its units of that kind."""
        kind, snd, _rcv, units = reading[:4]
        held = (self.short if kind == 'written' else self.long).get((sym, snd), 0.0)
        return held >= units - pool_qty_eps(sym, units)

    def move_in(self, kind: str, sym: str, acct: str, units: float) -> None:
        """Moved units arrive in `acct`: written contracts to its short, bought to its long."""
        k = (sym, acct)
        d = self.short if kind == 'written' else self.long
        d[k] = d.get(k, 0.0) + units

    def move_out(self, kind: str, sym: str, acct: str, units: float) -> None:
        """Moved units leave `acct` (never below none, TJ-1180)."""
        k = (sym, acct)
        d = self.short if kind == 'written' else self.long
        d[k] = max(d.get(k, 0.0) - units, 0.0)

    def walk_futures_short(self, t: TaxTransaction) -> None:
        """A short futures leg (lib/futures.engine_pool_key): its pool is
        by the row's own direction, only whose units they are is kept."""
        k = (t.symbol, t.account)
        self.short[k] = self.short.get(k, 0.0) - float(t.quantity or 0.0)

    def split(self, symbol: str, target: str, ratio: float) -> None:
        """A split or rename applies to both positions alike."""
        for d in (self.short, self.long):
            for ka in [k for k in d if k[0] == symbol]:
                units = d[ka] * ratio
                del d[ka]
                kt = (target, ka[1])
                d[kt] = d.get(kt, 0.0) + units

    def fold_state(self) -> _RouterState:
        """The two positions (the Canadian re-fold's checkpoint copies them)."""
        return (self.short, self.long)

    def restore(self, state: _RouterState) -> None:
        """Back to a `fold_state`, in place (the shorts recorder reads `short`)."""
        for dst, src in zip((self.short, self.long), state):
            dst.clear()
            for k, units in src.items():        # item by item: `short` keeps its index
                dst[k] = units


def _as_row(row: Any) -> TaxTransaction:
    """A book row as the router reads one (a dict of a JSON book made a
    TaxTransaction of its own fields)."""
    if isinstance(row, TaxTransaction):
        return row
    names = TaxTransaction.__dataclass_fields__
    return TaxTransaction(**{k: v for k, v in row.items()
                             if k in names and v is not None})


class MarkedPositions:
    """An account's units short and held of one security, as the walk pools
    them: what lib/stock_dividend_cover asks of a Canadian book (its
    `positions`). The router itself, row by row in the caller's order, a
    crossing trade cut as the walk cuts it (`crossing_units`, `_row_part`):
    a broker-marked short stays apart from the shares the account holds
    (CA-DISP-04, backlog TJ-4993), so "short on the entitlement date" is
    that pool, never the account's net position. An unmarked sale past
    the shares held leaves the pooled long below 0: short too."""

    def __init__(self) -> None:
        self._router = _MarkedShortRouter()

    def before(self, row: Any) -> Tuple[float, float]:
        """(units short, units held) of the row's security in its account."""
        t = _as_row(row)
        k = (t.symbol, t.account)
        held = self._router.long.get(k, 0.0)
        return self._router.short.get(k, 0.0) + max(-held, 0.0), max(held, 0.0)

    def walk(self, row: Any, owed: bool = False) -> None:
        """Book `row` (`owed`: a stock dividend a short still open makes
        good, written as the passes get it: its quantity negative)."""
        t = _as_row(row)
        if t.action == 'SPLIT':
            self._router.split(t.symbol, (t.symbol_new or '').strip() or t.symbol,
                               float(t.quantity or 0.0))
            return
        if owed:
            k = (t.symbol, t.account)       # (the short grows, whichever pool holds it)
            if self._router.short.get(k, 0.0) > 0:
                self._router.short[k] += float(t.quantity or 0.0)
            else:
                self._router.long[k] = self._router.long.get(k, 0.0) - float(t.quantity or 0.0)
            return
        if t.action == 'TRANSFER' and not (_is_lot_move(t) or _is_listing_journal(t)):
            # A plain transfer in or out (a sheltered book's, which the
            # walk gets as held units): the account's long.
            k, q = (t.symbol, t.account), float(t.quantity or 0.0)
            held = self._router.long.get(k, 0.0) + q
            self._router.long[k] = max(held, 0.0) if q < 0 else held
            return
        units = self._router.crossing_units(t)
        if units is None:
            self._router.walk(t)
            return
        first = _row_part(t, units, 'C', t.id)
        self._router.walk(first)
        self._router.walk(_row_part(t, abs(float(t.quantity)) - units, 'O',
                                    f"{t.id}#2", rest_of=first))


def _say_late_covers(pairs: Sequence[Tuple[TaxTransaction, TaxTransaction]]) -> None:
    """One ATTENTION per marked cover that found the short already covered
    by an unmarked purchase (a .tt line, a row of an export with no cover
    marks; TJ-5235): both rows, what the books did, the remedy."""
    for first, marked in pairs:
        def row(t: TaxTransaction) -> str:
            return f"{float(t.quantity):g} on {t.date}" + (f" ({t.source})" if t.source else "")
        emit_line(f"warning: ATTENTION: marked cover: {marked.symbol} ({marked.account}): the purchase of "
                  f"{row(first)} carries no cover mark and was read as covering the account's short, and "
                  f"the broker's marked cover of {row(marked)} then found less short than it buys. The "
                  f"marked purchase's units past the short are booked as a long purchase: one short is "
                  f"covered twice, or the first purchase bought shares. If the two rows are one trade, "
                  f"remove the unmarked one; if the first bought shares, the short it was read as "
                  f"covering was not its own: correct or remove the row that opens that short.")


def _say_both_open(found: Sequence[Tuple[str, str, float, float]]) -> None:
    """One ATTENTION per account whose rows end with shares held and a short
    of the same shares open (TJ-4993): both positions and the remedy."""
    for sym, acct, held, short in found:
        emit_line(f"warning: ATTENTION: long and short: {sym} ({acct}): the account's rows end with "
                  f"{held:g} held and {short:g} short of the same shares. A purchase closes a short the "
                  f"broker marks only when it is marked as its cover (RBC: `COVER SHORT.` in "
                  f"the description; an IB buy coded C), so a purchase with no mark stays a long beside "
                  f"the short. If the broker holds both, nothing is wrong. If the short was covered, the "
                  f"export that holds the marked cover is missing: add it (`tjs list` shows both rows).")


def _row_part(t: TaxTransaction, units: float, code: str, part_id: Optional[str], *,
              rest_of: Optional[TaxTransaction] = None) -> 'TaxTransaction':
    """`units` of trade `t`, its money pro rata (`rest_of`: the part
    already cut, this one takes what it left, so the two add up to the
    row exactly). `code`: what this part does, the broker's own letter."""
    q = float(t.quantity)
    sign = 1.0 if q > 0 else -1.0
    frac = units / abs(q)

    def share(field: str) -> float:
        v = float(getattr(t, field) or 0.0)
        if rest_of is not None:
            return v - float(getattr(rest_of, field) or 0.0)
        return v * frac
    return replace(t, quantity=sign * units, proceeds=share('proceeds'),
                   commission=share('commission'), fee=share('fee'),
                   net_amount=share('net_amount'),
                   gross_amount=share('gross_amount'),
                   open_close=code, id=part_id)


def _router_move_key(t: TaxTransaction, date_of: Callable[[TaxTransaction], str]) -> Tuple[str, int]:
    """Where a blended book's move leg (dropped from its rows,
    _own_move_view) falls for _MarkedShortRouter, against a row's
    event_sort_key 'ca_main': the start of its day. The drain
    (_drain_move_legs) reads a move's two legs together there: the
    receiving account's units arrive at the start of the day and the
    sending account's leave after the day's rows, as the missing-history
    walks read them (TJ-0093), so a sale of the moved units that day is no
    short, and a buy-back of moved written contracts that day is a
    buy-back (TJ-3225)."""
    return (date_of(t), -1)


def _drain_move_legs(router: _MarkedShortRouter, pending: List[_PendingLeg],
                     limit: Optional[Tuple[Any, ...]], pools: Optional[PoolBook] = None,
                     warned: Optional[Set[str]] = None,
                     on_written: Optional[Callable[[List[TaxTransaction]], Any]] = None,
                     limit_date: Optional[str] = None) -> None:
    """Walk the own-move legs of `pending` (sorted entries (key, i, item):
    a leg keyed at the start of its day, _router_move_key, a move read
    again later that day, or a share leg's deferred leave) whose key is
    below `limit` (None: all) into `router`; `limit_date` is the limit
    row's trade date (its sort date when not given).

    A day's moves of one series are read together, each move's two legs
    (one description) by their rows' sign convention and the accounts'
    positions (_MarkedShortRouter.move_pair; backlog TJ-3224, TJ-3243):
    first every move read on the positions as they stand, applied in
    chain order (a move whose sender receives from no other read move
    first, a sure reading before an ambiguous one), then the unread ones
    read again on the updated positions, while any gets a reading
    (TJ-3256, TJ-3260). A move is read only once every trade dated before
    its date is walked, whatever that trade's settlement: before a row
    dated earlier than the move, the day's legs wait (TJ-3271). A move no
    reading fits is read again after the next row of its day (a written
    contract moved the day it is written takes effect right after the
    write, TJ-3242), and once its day is over (the limit row's trade date
    is past the move's date: its trades dated on or before the move's date
    are in, whatever their settlement, TJ-3258) takes the fallback, bought
    contracts leaving the negative leg's account whatever the sign
    (TJ-3269), with a Warning. A move read more than one way at any read
    of its day (more than one reading fits) is named by the Warning too,
    said once per move (`warned`: the run's set), whether it is booked as
    the sign's own reading or, once the day's other moves leave it one
    reading, as that one (TJ-3272).

    A move of an option series takes effect at once on both sides,
    written or bought: the sender's position leaves and the receiver's
    arrives, so the receiver never holds a long the pool does not
    (TJ-3246, TJ-3262); a written move's lots are re-tagged to the
    receiver (AcbPool.retag_lots, with `pools`), and `on_written(legs)`
    tells a caller of one. A share leg keeps TJ-0093's order: the long's
    in leg now, its out leg at the day's end. A leg with no partner in
    the book is read on its own (move_leg_kind)."""
    import bisect
    _SHORT_SFX, _ = _futures_names()

    def _push(key: Tuple[Any, ...], i: int, item: Any) -> None:
        bisect.insort(pending, (key, i, item), key=lambda e: e[:2])

    while pending and (limit is None or pending[0][0] < limit):
        key, i, item = pending.pop(0)
        if isinstance(item, tuple):
            router.walk(item[1])
            continue
        q = float(item.quantity or 0.0)
        if not is_option_symbol(item.symbol):
            if q > 0:
                router.walk(item)
            else:
                _push((key[0], 99), i, ('leg', item))
            continue
        if limit is not None and (limit_date or limit[0]) < item.date:
            # The limit row is dated before the move (it settles on or
            # after the move's day): read first, the leg waits (TJ-3271).
            _push(limit, i, item)
            continue
        # This day's legs of the series, still pending at this key.
        day = [(key, i, item)]
        j = 0
        while j < len(pending) and pending[j][0] == key:
            e = pending[j]
            if not isinstance(e[2], tuple) and e[2].symbol == item.symbol:
                day.append(pending.pop(j))
            else:
                j += 1
        by_desc: Dict[str, List[_PendingLeg]] = {}
        for e in day:
            by_desc.setdefault(e[2].description or f'#{e[1]}', []).append(e)
        pairs = [g for g in by_desc.values() if len(g) == 2]
        singles = [e for g in by_desc.values() if len(g) != 2 for e in g]
        over = limit is None or (limit_date or limit[0]) > item.date
        sym = item.symbol
        final = False
        read: Dict[int, _Reading] = {}         # each sure reading, kept while it fits
        amb: Dict[int, List[Tuple[str, str, str]]] = {}           # a move once read more than one way: those readings
        while pairs:
            for g in pairs:
                r = read.get(id(g))
                if r is not None and r[4] != 'fallback' and not router.move_fits(r, sym):
                    del read[id(g)]         # its units went to an earlier move: read again
                if id(g) not in read:
                    r = router.move_pair(g[0][2], g[1][2], final=final)
                    if r[0]:
                        read[id(g)] = r
                    if r[4] == 'ambiguous':
                        amb[id(g)] = router.move_readings(g[0][2], g[1][2])
            fits = [(g, read[id(g)]) for g in pairs if id(g) in read]
            if not fits:
                if final or not over:
                    break
                final = True            # the day is over: the fallback for the rest
                continue
            receivers = {r[2] for g, r in fits}
            # One move at a time: a sure reading first (an ambiguous one is
            # read again once the sure ones are applied: its ambiguity may
            # go), chain-first among them (a move whose sender receives from
            # no other read move); a sure reading stands while its units fit.
            g, (kind, snd, rcv, units, note) = min(
                fits, key=lambda e: (e[1][4] == 'ambiguous', e[1][1] in receivers, pairs.index(e[0])))
            for g2 in list(read):
                if g2 != id(g) and read[g2][4] == 'ambiguous':
                    del read[g2]
            pairs.remove(g)
            lead = g[0][2]
            if (note or id(g) in amb) and warned is not None and lead.description not in warned:
                warned.add(lead.description)
                others = [r for r in amb.get(id(g), ()) if r[:2] != (kind, snd)]
                emit_line(_move_note(lead, kind, snd, rcv, units, note or 'ambiguous', others))
            if kind == 'written':
                router.move_out('written', sym, snd, units)
                router.move_in('written', sym, rcv, units)
                if pools is not None and pools.get(sym + _SHORT_SFX) is not None:
                    pools[sym + _SHORT_SFX].retag_lots(snd, rcv, units)
                if on_written is not None:
                    on_written([g[0][2], g[1][2]])
            else:
                router.move_out('bought', sym, snd, units)
                router.move_in('bought', sym, rcv, units)
        for g in pairs:                 # no reading fits yet: after the next row
            for e in g:
                assert limit is not None, "with no limit the fallback reads every move (move_pair)"
                _push(limit, e[1], e[2])
        for e in singles:
            leg = e[2]
            lq = float(leg.quantity or 0.0)
            kind = router.move_leg_kind(leg)
            if kind == 'written_out':
                router.move_out('written', sym, leg.account, abs(lq))
            elif kind == 'written_in' or lq > 0:
                router.walk(leg)
            else:
                router.move_out('bought', sym, leg.account, abs(lq))


def _move_note(leg: TaxTransaction, kind: str, snd: str, rcv: str, units: float, note: str,
               others: Sequence[Tuple[str, str, str]] = (), width: int = 120) -> str:
    """The Warning of a move read more than one way at some read of its
    day (backlog TJ-3243, TJ-3256, TJ-3259, TJ-3272) or by the fallback
    (TJ-3269): its first line, within `width` (the shown line, its label
    relabelled, is a character shorter), always names the series, the
    date and the reading booked; the accounts' names give way first, the
    receiver's dropped from it, then the sender's cut (TJ-3270). The
    second line (a continuation, shown with --details) names the move
    whole when the first line could not, and `others`, the (kind, account)
    readings it also read as."""
    pre = f"warning: ATTENTION: own-account move: {leg.symbol} {leg.date}"
    fb = note == 'fallback'
    booked = f"booked as {units:g} {kind} leaving "
    heads = [f"{pre} {snd}->{rcv}: {'no account holds what it moves; ' if fb else ''}{booked}{snd}",
             f"{pre}: {'none held; ' if fb else ''}{booked}{snd}"]
    head = next((h for h in heads if len(h) <= width), None)
    if head is None:
        stem = heads[1][:len(heads[1]) - len(snd)]
        head = stem + snd[:max(width - len(stem) - 2, 1)] + '..'
    move = '' if head == heads[0] else f"The move: {units:g} {kind}, {snd} -> {rcv}. "
    if fb:
        rest = ("No account holds what it moves (a write or purchase is missing from the "
                "files: tjs find-missing-history); booked as bought contracts leaving the "
                "account whose row is negative, so both accounts' figures for the series are off.")
    else:
        also = ' or '.join(f"{a}'s {k} contracts leaving" for k, a, *_ in others)
        rest = (f"It also reads as {also}; " if also else '') + (
            "if the move was otherwise, both accounts' figures for the series are off.")
    return f"{head}\n    {move}{rest}"


def _place_own_move_legs(transactions: List[TaxTransaction], sort_key: _SortKey,
                         date_of: Callable[[TaxTransaction], str],
                         moves: Sequence[TaxTransaction] = ()) -> List[TaxTransaction]:
    """A one-account book's rows with each own-move leg of an option series
    that its account holds nothing of at the start of the leg's day placed
    right after the account's first trade of the series that day (the leg's
    time set past it): a written move takes effect at the first moment of
    its day at which the sender holds what it moves (TJ-3242: it wrote the
    contract that day). The rows' order otherwise, and the leg's id, stay.
    A blended book (`moves`: the legs it holds no row of) is left alone."""
    if moves or not any(_is_lot_move(t) and is_option_symbol(t.symbol) for t in transactions):
        return transactions
    from datetime import datetime, timedelta
    rows = sorted(transactions, key=sort_key)
    router = _MarkedShortRouter()
    placed: Dict[int, TaxTransaction] = {}
    for i, t in enumerate(rows):
        if t.action == 'SPLIT':
            router.split(t.symbol, (t.symbol_new or '').strip() or t.symbol,
                         float(t.quantity or 0.0))
            continue
        if _is_lot_move(t) and is_option_symbol(t.symbol):
            k = (t.symbol, t.account)
            q = float(t.quantity or 0.0)
            eps = pool_qty_eps(t.symbol, q)
            # The sender's leg by its rows' sign convention (a written
            # contract leaving: - by direction, + as the position's change).
            sender = (q < 0) if _move_sign(t) == 'direction' else (q > 0 and _move_sign(t) == 'change')
            if sender and router.short.get(k, 0.0) <= eps and router.long.get(k, 0.0) <= eps:
                # The account's first trade of the series dated on or before
                # the move's date, whatever its settlement (TJ-3258).
                anchor = next((u for u in rows[i + 1:]
                               if u.date <= t.date and u.account == t.account
                               and u.symbol == t.symbol
                               and u.action in ('BUYSELL', 'ASSIGN')), None)
                if anchor is not None:
                    try:
                        _at = (datetime.strptime(anchor.time or '00:00:00', '%H:%M:%S')
                               + timedelta(seconds=1)).strftime('%H:%M:%S')
                    except ValueError:
                        _at = anchor.time
                    placed[id(t)] = replace(t, time=_at, date_settle=date_of(anchor))
        router.walk(t)
    if not placed:
        return transactions
    return [placed.get(id(t), t) for t in transactions]


def _option_crossing_possible(transactions: List[TaxTransaction], moves: Sequence[TaxTransaction],
                              sort_key: _SortKey, move_key: Optional[_SortKey]) -> bool:
    """Whether some account trades an option series past its own position
    (a sale past its bought contracts, a purchase past its written ones), so
    the cut below has work: each (series, account)'s own rows replayed alone
    (an option's routing reads nothing else; backlog TJ-3199). A series a
    SPLIT row acts on is replayed whole (its positions move between keys)."""
    groups: Dict[Any, List[TaxTransaction]] = {}
    legs: Dict[str, List[_PendingLeg]] = {}
    moved = {m.symbol for m in moves if is_option_symbol(m.symbol)}
    for t in transactions:
        if not is_option_symbol(t.symbol):
            continue
        if t.action == 'SPLIT':
            return True
        if t.action in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE') or _is_lot_move(t):
            # A series with move legs: every account's rows together,
            # so the legs pair as the walk pairs them (TJ-3224).
            groups.setdefault(t.symbol if t.symbol in moved
                              else (t.symbol, t.account), []).append(t)
    for i, m in enumerate(moves):
        if is_option_symbol(m.symbol):
            assert move_key is not None, "moves come with their key (CanadaTaxRules._prepare)"
            legs.setdefault(m.symbol, []).append((move_key(m), i, m))
    for gk, rows in groups.items():
        if not any(t.action == 'BUYSELL' for t in rows):
            continue
        pending = sorted(legs.get(gk, []), key=lambda e: e[:2])
        if len(rows) < 2 and not pending:
            continue
        router = _MarkedShortRouter()
        for t in sorted(rows, key=sort_key):
            _drain_move_legs(router, pending, sort_key(t), limit_date=t.date)
            if router.crossing_units(t) is not None:
                return True
            router.walk(t)
    return False


def _split_crossing_marked_rows(transactions: List['TaxTransaction'],
                                sort_key: _SortKey, moves: Sequence[TaxTransaction] = (),
                                move_key: Optional[_SortKey] = None) -> List['TaxTransaction']:
    """The taxable rows with each trade that crosses between the short
    pool and the long one cut in two, in the walk's order
    (_MarkedShortRouter.crossing_units): a buy past the account's own
    short covers the short and buys long with the rest (CA-DISP-04; all
    of it in the short pool, the rest was a long no later sale could
    find, backlog TJ-1021), and a sale coded C;O past the account's own
    long sells that long and opens a short with the rest, never another
    account's shares (backlog TJ-1023). An option trade past the
    account's own position is cut the same way (CA-OPT-12, TJ-2735): a
    purchase past its written contracts buys long with the rest, a sale
    past its bought contracts writes the rest. The part that realizes
    keeps the row's id, so its gain is the row's. `moves`: the legs of
    own moves the book no longer holds, for what each account holds
    (TJ-1180)."""
    router = _MarkedShortRouter()
    if not (any((t.action == 'BUYSELL'
                 and (router._opens_short(t) or 'O' in router._codes(t)))
                or (t.action == 'OPENING_BALANCE'
                    and float(t.quantity or 0.0) < 0)
                for t in transactions)
            or _option_crossing_possible(transactions, moves, sort_key, move_key)):
        return transactions             # no marked short, no option trade past a position
    parts: Dict[int, Tuple[TaxTransaction, TaxTransaction]] = {}
    pending: List[_PendingLeg] = []
    if moves:
        assert move_key is not None, "moves come with their key (CanadaTaxRules._prepare)"
        pending = sorted(((move_key(m), i, m) for i, m in enumerate(moves)),
                         key=lambda e: e[:2])
    for t in sorted(transactions, key=sort_key):
        _drain_move_legs(router, pending, sort_key(t), limit_date=t.date)
        if t.action == 'SPLIT':
            router.split(t.symbol, (t.symbol_new or '').strip() or t.symbol,
                         float(t.quantity or 0.0))
            continue
        router.note(t)
        units = router.crossing_units(t)
        if units is None:
            router.walk(t)
            continue
        first = _row_part(t, units, 'C', t.id)
        rest = _row_part(t, abs(float(t.quantity)) - units, 'O',
                         f"{t.id}#2", rest_of=first)
        router.walk(first)
        router.walk(rest)
        parts[id(t)] = (first, rest)
    _say_late_covers(router.late_covers)
    _say_both_open(router.both_open())
    if not parts:
        return transactions
    out: List['TaxTransaction'] = []
    for t in transactions:
        out.extend(parts.get(id(t), (t,)))
    return out
