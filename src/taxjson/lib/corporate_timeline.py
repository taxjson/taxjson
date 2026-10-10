"""Authoritative split/rename arithmetic — the SplitTimeline.

Five audit cycles kept finding the same bug class regenerating: split
dedup keys, the (from, to] boundary for cumulative ratios, and rename-chain
following were each hand-implemented at ~7 sites (both engines, the missing-history
walks, the wash radar), and the copies drifted. This module is the single
definition; the call sites delegate.

Two grouping semantics coexist BY DESIGN — they preserve the engines'
existing (and deliberately different) behaviors:

- `factor(symbol, ...)` — MIGRATED schedule (US engine semantics): a
  SPLIT-rename moves the symbol's accumulated events onto the rename
  target at the split's position in the input sequence; queries under the
  old name afterwards see no events. Build order therefore matters — pass
  transactions in the same order the engine iterates them.

- `alias_factor(symbol, ...)` — ALIAS-CLASS events (Canada engine
  semantics): all events of a rename-connected component answer for every
  member symbol, mirroring the engine's `alias_of(t.symbol) == loss_alias`
  filter.

Boundary convention, shared by both engines: the cumulative ratio over
(from_date, to_date] — a split ON from_date is already baked into
quantities denominated at from_date; a split ON to_date is included
(matching main-pass ordering, where SPLIT sorts before same-date trades).
Backward conversions use the reciprocal, guarded so a zero product falls
back to 1.0 (preserving both engines' `if f else` guards).
"""

from taxjson.lib.stage_msg import emit_line
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# --------------------------------------------------------------- ordering
#
# Event ordering IS tax semantics here — it decides what is pre/post split,
# whether the opening balance gets scaled, and which lot FIFO consumes.
# It used to be defined by five hand-maintained sort keys (Canada main
# pass, Canada balance walks, US engine, missing-history walks); two historical
# bugs (the OB/SPLIT 00:00:00 tie, the settle-lagged phase) were drift
# between those copies. `event_sort_key` is the single definition; the
# per-engine DIFFERENCES are explicit profiles, not implicit drift:
#
#   ca_main      (date, phase, trade date, time, priority) — settle-basis
#                                                  dates; rows settling the
#                                                  same day go in trade order
#   ca_balance   (date, phase, trade date, time) — no priority (unchanged
#                                                  legacy behavior of the
#                                                  running-balance walks)
#   us_main      (date, time, priority)         — trade-basis dates; note
#                                                  priority sorts AFTER
#                                                  time, so a noon SPLIT
#                                                  follows a 09:30 BUY —
#                                                  the Canada phase ladder
#                                                  orders the same pair
#                                                  SPLIT-first. Deliberate
#                                                  divergence, pinned in
#                                                  tests.
#   plain_walk   (date, time)                   — the missing-history walks'
#                                                  bare ordering.
#
# The key is the MOMENT and its rung; rows tied on it keep their input
# order (a stable sort). The engines take each input book with its
# accounts' rows in NAME order (`account_tie`, core._by_account_name), so
# rows of two accounts at one moment never depend on the order the
# books were merged in (issue #31) — while "same moment" stays a
# moment, whatever the accounts' names.

class Phase(IntEnum):
    """Canada execution-order phase at a shared sort date. A row that
    SETTLES later than it traded belongs, economically, to its execution:
    at the settle date it must come BEFORE a same-date SPLIT (its shares
    pre-exist the split and get scaled), while rows actually executed on
    the split's effective date are post-split. Splits are effective at
    market OPEN, so same-day executions are post-split regardless of the
    row's clock time."""
    PRE_EXISTING = 0        # OPENING_BALANCE, or settle-lagged execution
    SPLIT = 1
    EXECUTED_TODAY = 2


class WalkPriority(IntEnum):
    """Missing-history-walk tie-break rung. The walks replay position on TRADE
    dates, where every row 'executed today' — so, matching the engines'
    split-effective-at-market-open semantics, a SPLIT precedes same-date
    executions regardless of its clock stamp (IB stamps corp actions
    ~20:25; the old bare (date, time) ordering put those splits AFTER the
    day's trades, disagreeing with the engine replay that later consumes
    the walk's output). OPENING_BALANCE strictly first, same rationale as
    the engines — which is what lets a synthetic opening anchor ON its
    chain's first activity date instead of a fabricated day earlier."""
    OPENING_BALANCE = -1
    SPLIT = 0
    OTHER = 1


class CaPriority(IntEnum):
    """Canada same-(date, phase, time) tie-break ladder. The OPTION
    leg of an assignment sorts before its STOCK leg: the option leg
    stages the premium the stock leg consumes, and with one shared
    rung a same-timestamp pair's order was input order — stock-first
    silently dropped the premium (2026-09 US-engine audit).

    Plain trades — buys AND sells — share ONE rung, so trades at the
    same moment keep the order the rows arrive in, which is the
    export's row order (tax-logic CA-DATE-14 / US-DATE-13; the US
    ladder never split them). Webull prints no clock time and
    Questrade stamps 00:00:00, so a day's trades tie; a separate BUY
    rung ahead of SELL turned a write listed before its same-day
    buy-back into a long round trip (audit R1-30, owner decision D7)."""
    OPENING_BALANCE = -1    # pre-window position: a same-timestamp SPLIT
    #                         must scale it (parsers stamp splits 00:00:00,
    #                         the OB anchor time)
    DISALLOW = 0
    ASSIGN_OPTION = 1
    ASSIGN_STOCK_OR_SPLIT = 2
    TRADE = 3               # buys and sells: input (export) order
    ADJUST = 4              # after the trades
    OTHER = 5


class UsPriority(IntEnum):
    """US same-(date, time) tie-break ladder. ASSIGN before BUYSELL,
    and the option-leg ASSIGN before the stock-leg ASSIGN (see
    CaPriority). ADJUST after trades, mirroring the Canada convention
    — without a rung, a same-timestamp ADJUST-vs-trade order was
    input order."""
    OPENING_BALANCE = -1
    ASSIGN_OPTION = 0
    ASSIGN_STOCK = 1
    SPLIT = 2
    OTHER = 3
    ADJUST = 4


def _is_option_leg(tx: Any) -> bool:
    """The premium-staging leg of an assignment: an option, or a warrant
    exercise leg the parser marked (`exercise_of`) — it must sort before
    its stock leg like an option's."""
    # Lazy: core imports this module at load time.
    from taxjson.lib.core import exercise_target, is_option_symbol
    return (is_option_symbol(getattr(tx, 'symbol', '') or '')
            or bool(exercise_target(tx)))


def _settle_first(tx: Any) -> str:
    return tx.date_settle if getattr(tx, 'date_settle', '') else tx.date


def _trade_date(tx: Any) -> str:
    return tx.date


def _ca_phase(tx: Any, sort_date: str) -> int:
    if tx.action == 'OPENING_BALANCE':
        return Phase.PRE_EXISTING
    if tx.action == 'SPLIT':
        return Phase.SPLIT
    if tx.date and tx.date < sort_date:
        return Phase.PRE_EXISTING           # settle-lagged: executed earlier
    return Phase.EXECUTED_TODAY


def _ca_exec_date(tx: Any) -> str:
    """Execution-order component of the Canada keys, after the phase.
    Two settle-lagged rows that settle on the SAME day (a Friday trade
    and the next trading day's trade both settling Tuesday over a
    settlement holiday) are taken in TRADE-date order, then clock time:
    a Monday 09:45 buy is not applied before the previous Friday's 15:00
    sale (A2-0067). An opening balance or a split sorts first in its
    phase ('' — they carry no execution of their own)."""
    if tx.action in ('OPENING_BALANCE', 'SPLIT'):
        return ''
    return tx.date or ''


def _ca_priority(tx: Any) -> int:
    if tx.action == 'OPENING_BALANCE':
        return CaPriority.OPENING_BALANCE
    if tx.action == 'DISALLOW':
        return CaPriority.DISALLOW
    if tx.action == 'ASSIGN' and _is_option_leg(tx):
        return CaPriority.ASSIGN_OPTION
    if tx.action in ('ASSIGN', 'SPLIT'):
        return CaPriority.ASSIGN_STOCK_OR_SPLIT
    if tx.action == 'ADJUST':
        # Tested BEFORE the quantity signs: an ADJUST carrying a
        # nonzero quantity (nothing emits one today, but the schema
        # doesn't forbid it) must still ledger as an ADJUST, not
        # masquerade as a BUY/SELL in the tie-break.
        return CaPriority.ADJUST
    if tx.quantity:
        return CaPriority.TRADE
    return CaPriority.OTHER


def _walk_rest(tx: Any) -> Tuple:
    """Missing-history-walk key after the trade date: (group, time, rung).

    group -1: OPENING_BALANCE. group 0: SPLITs, and trades that SETTLE
    after their trade date — ordered among themselves by clock, so a
    trade executed before an evening-batch split (IB 20:25) comes first,
    exactly the trades the engine re-denominates through the split
    (core.py settle-lag rule; audit S021-00: the walk applied the split
    first and invented a short). group 1: every other same-day
    execution (post-split: splits are effective at market open).
    Rung: at one moment a SPLIT first, then buys before sells, so no
    walk depends on the order of tied rows (audit S075-12 / S076-04).
    DELIBERATE divergence from the engines, which take tied trades in
    the export's row order (CA-DATE-14 / US-DATE-13): these walks ask
    whether history is MISSING, and a same-moment sell + buy is no
    evidence of that in either order — reading it buys first never
    invents a false short (or a larger synthesized opening) out of
    two rows that net to nothing."""
    act = tx.action
    if act == 'OPENING_BALANCE':
        return (WalkPriority.OPENING_BALANCE, tx.time or '00:00:00', 0)
    if act == 'SPLIT':
        return (WalkPriority.SPLIT, tx.time or '00:00:00', 0)
    q = float(getattr(tx, 'quantity', 0) or 0)
    rung = 1 if q > 0 else 2
    ds = getattr(tx, 'date_settle', '') or ''
    lagged = (act in ('BUYSELL', 'ASSIGN') and bool(ds) and bool(tx.date)
              and ds > tx.date)
    return ((WalkPriority.SPLIT if lagged else WalkPriority.OTHER),
            tx.time or '00:00:00', rung)


def _us_priority(tx: Any) -> int:
    if tx.action == 'OPENING_BALANCE':
        return UsPriority.OPENING_BALANCE
    if tx.action == 'ASSIGN':
        return (UsPriority.ASSIGN_OPTION if _is_option_leg(tx)
                else UsPriority.ASSIGN_STOCK)
    if tx.action == 'SPLIT':
        return UsPriority.SPLIT
    if tx.action == 'ADJUST':
        return UsPriority.ADJUST
    return UsPriority.OTHER


class RadarPriority(IntEnum):
    """Wash-radar replay tie-break at a shared timestamp. DELIBERATE
    divergence from CaPriority: the radar applies ADJUST rows (transferred
    basis, ROC) BEFORE the day's trades so the pool a trade sees is
    already adjusted, while the engine ledgers ADJUST after BUY/SELL.
    Radar's historical semantics, unchanged — hosting the ladder here just
    means ordering definitions live in ONE file (the radar's fix history
    was dominated by engine ordering fixes never mirrored into its copy).
    Trades share one rung, as in the engines: tied trades replay in the
    export's row order (CA-DATE-14 / US-DATE-13)."""
    ADJUST = 0
    ASSIGN_OR_SPLIT = 1
    TRADE = 2


def radar_priority(tx: Any) -> int:
    """Tie-break rung for the wash-radar pool replay (sorted on
    (epoch, radar_priority) — the radar keys on numeric epochs, so it
    consumes the ladder directly rather than event_sort_key)."""
    action = (tx.action or '').upper()
    if action == 'ADJUST':
        return RadarPriority.ADJUST
    if action in ('ASSIGN', 'SPLIT'):
        return RadarPriority.ASSIGN_OR_SPLIT
    return RadarPriority.TRADE


def account_tie(tx: Any) -> str:
    """A row's account name, the key the engines (and the walks that
    replay them: the wash radar, t1135) stable-sort each input book by
    before ordering it by event_sort_key: rows of DIFFERENT accounts at
    one moment (same date, time and rung) then go in the accounts' NAME
    order, rows of one account keep the export's row order (CA-DATE-14 /
    US-DATE-13). It used to be the accounts' order in taxjson.toml,
    which a reordered file changed without any input changing (issue
    #31), and which every recompute had to rebuild by hand (A2-0497,
    A2-0502). Rows with no account (a single book) tie as one."""
    a = getattr(tx, 'account', '') if not isinstance(tx, dict) \
        else tx.get('account', '')
    return str(a or '')


def event_sort_key(tx: Any, *, profile: str,
                   date_of: Optional[Callable[[Any], str]] = None) -> Tuple:
    """The one event-ordering definition. `date_of` selects the date basis
    (Canada passes its settle-first accessor, the US engine trade date);
    profiles select the ladder — see the module comment for the table."""
    if profile == 'plain_walk':
        return (tx.date, tx.time or '00:00:00')
    if profile in ('missing_history_walk', 'phantom_walk'):  # old name kept
        return (tx.date,) + _walk_rest(tx)
    # Default date basis per profile: Canada's ladders settle-first, the
    # US ladder the TRADE date — a US caller that omitted date_of got
    # Canada's settle-date ordering (partition ENGINE-13).
    d = (date_of or (_trade_date if profile == 'us_main'
                     else _settle_first))(tx)
    if profile == 'ca_main':
        return (d, _ca_phase(tx, d), _ca_exec_date(tx), tx.time,
                _ca_priority(tx))
    if profile == 'ca_balance':
        return (d, _ca_phase(tx, d), _ca_exec_date(tx), tx.time)
    if profile == 'us_main':
        return (d, tx.time, _us_priority(tx))
    raise ValueError(f"unknown sort profile {profile!r}")


def normalize_symbol_new(symbol: str, symbol_new: Any) -> str:
    """Canonical spelling of a SPLIT's rename target: IB stamps
    symbol_new equal to the symbol for a plain split while RBC/Questrade
    leave it '' — same event, different spelling. '' means "no rename"."""
    new_sym = (symbol_new or '').strip()
    return '' if new_sym == symbol else new_sym


def split_event_key(symbol: str, date: str, ratio: Any, symbol_new: Any,
                    account: Optional[str] = None) -> Tuple:
    """Identity of one corporate split event, for dedup. Global by default
    (a split is a property of the security); pass `account` for the
    per-account walks (missing-history detection, radar pools) where the same
    event must apply once per account pool. Known limitation (documented
    at the original core site): two brokers reporting slightly different
    ratios for the same event won't collapse."""
    new_sym = normalize_symbol_new(symbol, symbol_new)
    rounded = round(float(ratio or 0), 9)
    if account is None:
        return (symbol, date, rounded, new_sym)
    return (symbol, account, date, rounded, new_sym)


# Brokers date one corporate split differently: IB can book a 10:1 split on
# one day and Questrade the same split a few days later. A key on the exact date kept both copies and
# scaled the pool by ratio**2 (2026-09 audit). Copies of the same split
# (symbol, ratio, rename target[, account]) within this many calendar
# days are ONE event — use `split_seen`, not a bare `key in seen`. Two
# genuine splits of one security at the same ratio a week apart do not
# happen.
SPLIT_DATE_WINDOW_DAYS = 7

# Events already noted, so the note prints once per event per process.
_SPLIT_DATE_NOTES: Set[Tuple] = set()


def _shift_date(date: str, days: int) -> Optional[str]:
    from datetime import datetime, timedelta
    try:
        d = datetime.strptime((date or '')[:10], '%Y-%m-%d')
    except ValueError:
        return None
    return (d + timedelta(days=days)).strftime('%Y-%m-%d')


def split_seen(seen: Set, symbol: str, date: str, ratio: Any,
               symbol_new: Any, account: Optional[str] = None,
               *, window: int = SPLIT_DATE_WINDOW_DAYS) -> Optional[str]:
    """Split-event membership with a date tolerance. Returns None and
    records the event when no copy of it is in `seen` yet; returns the
    date of the copy already recorded (possibly `date` itself) when this
    row repeats an event within `window` days. `seen` is a plain set of
    split_event_key tuples, so shared-set call sites keep working."""
    key = split_event_key(symbol, date, ratio, symbol_new, account=account)
    if key in seen:
        return date
    for off in range(1, window + 1):
        for sign in (-1, 1):
            other = _shift_date(date, sign * off)
            if other is not None and split_event_key(
                    symbol, other, ratio, symbol_new,
                    account=account) in seen:
                return other
    # A copy whose ratio was ROUNDED (a hand-typed .tt line 2.333333, or
    # convert-tt's 8 decimals, next to the parser's 2.333333333) is the
    # same event too: it scaled the pool twice (A2-0070).
    prior = _near_ratio_copy(seen, key, account is not None, window)
    if prior is not None:
        note_split_ratio_conflict(symbol, prior[0], date, prior[1],
                                  float(ratio or 0))
        return prior[0]
    seen.add(key)
    return None


# Two copies of one split agree on the ratio to this RELATIVE tolerance
# (rounding to 6 decimals or more); genuine distinct splits differ by far
# more.
SPLIT_RATIO_REL_TOL = 1e-6


def split_ratios_close(a: Any, b: Any) -> bool:
    a, b = float(a or 0), float(b or 0)
    return abs(a - b) <= SPLIT_RATIO_REL_TOL * max(abs(a), abs(b))


def _near_ratio_copy(seen: Set, key: Tuple, per_account: bool,
                     window: int) -> Optional[Tuple[str, float]]:
    """(date, ratio) of a recorded copy of `key`'s event whose ratio
    differs only by rounding, within `window` days; None otherwise."""
    from datetime import datetime
    if per_account:
        sym, acct, date, ratio, new = key
    else:
        sym, date, ratio, new = key
        acct = None
    try:
        d0 = datetime.strptime((date or '')[:10], '%Y-%m-%d')
    except ValueError:
        d0 = None
    best = None
    for k in seen:
        if not isinstance(k, tuple) or len(k) != len(key):
            continue
        if per_account:
            ks, ka, kd, kr, kn = k
            if ka != acct:
                continue
        else:
            ks, kd, kr, kn = k
        if ks != sym or kn != new or kr == ratio \
                or not split_ratios_close(kr, ratio):
            continue
        if kd != date:
            try:
                dk = datetime.strptime((kd or '')[:10], '%Y-%m-%d')
            except ValueError:
                continue
            if d0 is None or abs((dk - d0).days) > window:
                continue
        if best is None or kd < best[0]:
            best = (kd, kr)
    return best


def note_split_ratio_conflict(symbol: str, kept: str, dropped: str,
                             kept_ratio: float, dropped_ratio: float,
                             stream=None) -> None:
    """One stderr note per event: the same split was booked twice with
    ratios that differ only by rounding, and is applied once."""
    import sys
    k = ('ratio', symbol, round(kept_ratio, 6), min(kept, dropped),
         max(kept, dropped))
    if k in _SPLIT_DATE_NOTES:
        return
    _SPLIT_DATE_NOTES.add(k)
    when = (f"on {kept}" if kept == dropped
            else f"on {min(kept, dropped)} and {max(kept, dropped)}")
    emit_line(f"note: split {symbol} is booked twice {when} with ratios "
              f"{kept_ratio:.9g} and {dropped_ratio:.9g} (one rounded) — one "
              f"corporate event; applied ONCE, x{kept_ratio:.9g} on {kept}.",
              file=stream or sys.stderr)


def note_split_date_conflict(symbol: str, kept: str, dropped: str,
                             ratio: Any, stream=None) -> None:
    """One stderr note per event: the same split was booked on two dates
    and is applied once, on `kept`."""
    import sys
    if kept == dropped:
        return
    k = (symbol, round(float(ratio or 0), 9), min(kept, dropped),
         max(kept, dropped))
    if k in _SPLIT_DATE_NOTES:
        return
    _SPLIT_DATE_NOTES.add(k)
    emit_line(f"note: split {symbol} x{float(ratio or 0):g} is booked on two "
              f"dates ({min(kept, dropped)} and {max(kept, dropped)}) — one "
              f"corporate event reported by two sources; applied ONCE, on "
              f"{kept}.", file=stream or sys.stderr)


def cumulative_factor(events: List[Tuple[str, float]],
                      from_date: str, to_date: str) -> float:
    """Cumulative split ratio converting a quantity denominated at
    `from_date` into `to_date` units: multiply by every ratio in
    (min, max]; going backwards returns the reciprocal (1.0 when the
    product is zero — a zero ratio must not divide)."""
    if from_date == to_date:
        return 1.0
    lo, hi = ((from_date, to_date) if from_date < to_date
              else (to_date, from_date))
    f = 1.0
    for d, r in events:
        if lo < d <= hi:
            f *= r
    if from_date < to_date:
        return f
    return 1.0 / f if f else 1.0


class SplitTimeline:
    """Split schedule + rename equivalence for one transaction universe."""

    def __init__(self) -> None:
        self._schedule: Dict[str, List[Tuple[str, float]]] = {}
        self._parent: Dict[str, str] = {}
        self._class_events: Optional[Dict[str, List[Tuple[str, float]]]] = None
        # Raw event rows in input order — (date, symbol, ratio, new_sym)
        # with new_sym == '' for a plain split. Unlike _schedule (which
        # migrates events onto class roots) this keeps each event
        # attached to the symbol whose shares it actually scales, which
        # is what lineage_factor needs: a rename-split A->B scales A
        # shares only, never B shares already outstanding.
        self._events: List[Tuple[str, str, float, str]] = []
        self._events_sorted: Optional[
            List[Tuple[str, str, float, str]]] = None
        # Rename events on TRADE dates — (date, old, new) — for the
        # dated identity classes (class_at). Built lazily.
        self._renames: List[Tuple[str, str, str]] = []
        self._away: Optional[Dict[str, List[str]]] = None
        self._eparent: Dict[str, str] = {}

    # ------------------------------------------------------------ build

    @classmethod
    def from_transactions(cls, txs, *,
                          date_of: Optional[Callable[[Any], str]] = None
                          ) -> "SplitTimeline":
        """Build from transactions IN THE GIVEN ORDER (migration semantics
        are positional). `date_of` selects the date basis per row — the US
        engine passes nothing (trade date), the Canada engine passes its
        settle-aware sort-date accessor. Assumes the caller already deduped
        corporate events where required (both engines dedupe at entry)."""
        tl = cls()
        date_of = date_of or (lambda t: t.date)
        for t in txs:
            if getattr(t, 'action', None) != 'SPLIT':
                continue
            sym = t.symbol
            ratio = t.quantity
            tl._events.append((
                date_of(t), sym, float(ratio or 0.0),
                normalize_symbol_new(sym,
                                     getattr(t, 'symbol_new', ''))))
            if ratio:
                tl._schedule.setdefault(sym, []).append(
                    (date_of(t), float(ratio)))
            target = (getattr(t, 'symbol_new', '') or '').strip()
            if target and target != sym:
                tl._renames.append((str(t.date or date_of(t) or ''),
                                    sym, target))
                # Union for the alias-class view. Rename applies even when
                # the ratio is falsy (both engines behave this way).
                ra, rb = tl._find(sym), tl._find(target)
                if ra != rb:
                    tl._parent[ra] = rb
                    # The absorbed root may already carry schedule
                    # events (same-day chains processed out of input
                    # order: B->C before A->B leaves A's ratio parked
                    # under B) — merge them onto the surviving root.
                    if ra in tl._schedule:
                        tl._schedule.setdefault(rb, []).extend(
                            tl._schedule.pop(ra))
                # Migrate the accumulated schedule onto the CLASS ROOT
                # (US semantics — later queries under the old name see
                # nothing). The literal target may itself have been
                # renamed already; migrating to it positionally made
                # factor() input-order-dependent for same-day chained
                # renames, corrupting §1091 unit conversion.
                root = tl._find(target)
                if sym != root and sym in tl._schedule:
                    tl._schedule.setdefault(root, []).extend(
                        tl._schedule.pop(sym))
        return tl

    # ------------------------------------------------------------ renames

    def _find(self, s: str) -> str:
        while self._parent.get(s, s) != s:
            s = self._parent[s]
        return s

    def canonical(self, symbol: str) -> str:
        """Equivalence-class representative across SPLIT-rename chains
        (identity for symbols never renamed). DATE-BLIND: every row of a
        renamed symbol is in the class, even rows after the rename —
        use class_at for identical-property matching."""
        return self._find(symbol)

    # ------------------------------------------------------ dated classes
    #
    # A rename is a DATED event (owner decision on audit A2-0197): OLD
    # up to the rename date and NEW after it are one security; OLD after
    # the date is NOT automatically that security (the broker
    # may still book the renamed shares under OLD, or another company
    # may now use the ticker — `taxjson renames` lists such rows and the
    # user declares which in ticker.map). Each symbol therefore has one
    # identity per "epoch": epoch 0 before its first rename away, epoch
    # k after its k-th. A rename at date d joins OLD's identity just
    # before d with NEW's identity at d.

    def _epoch_build(self) -> None:
        if self._away is not None:
            return
        from datetime import datetime
        kept: List[Tuple[str, str, str]] = []
        last: Dict[Tuple[str, str], str] = {}
        # Copies of one rename (two brokers, two dates within the split
        # window) are one event, on the earliest date.
        for d, o, n in sorted(set(self._renames)):
            p = last.get((o, n))
            if p is not None:
                try:
                    gap = (datetime.strptime(d[:10], '%Y-%m-%d')
                           - datetime.strptime(p[:10], '%Y-%m-%d')).days
                except ValueError:
                    gap = None
                if gap is not None and gap <= SPLIT_DATE_WINDOW_DAYS:
                    continue
            last[(o, n)] = d
            kept.append((d, o, n))
        away: Dict[str, List[str]] = {}
        for d, o, _n in kept:
            away.setdefault(o, [])
            if d not in away[o]:
                away[o].append(d)
        for k in away:
            away[k].sort()
        self._away = away
        for d, o, n in kept:
            ra = self._efind(self._node(o, self._epoch(o, d, before=True)))
            rb = self._efind(self._node(n, self._epoch(n, d, before=True)))
            if ra != rb:
                self._eparent[ra] = rb

    def _epoch(self, sym: str, date: Optional[str], *,
               before: bool = False) -> int:
        import bisect
        dates = (self._away or {}).get(sym)
        if not dates:
            return 0
        if date is None:
            return len(dates)
        d = str(date)[:10]
        # A row ON the rename date is still the renamed security (the
        # broker books the change on that day; a trade under the old
        # ticker the same day is no reuse — S069-14): only rows AFTER
        # the date are a new identity. `before` is the same boundary,
        # kept for callers that ask for the identity a SPLIT acts on.
        del before
        return bisect.bisect_left(dates, d)

    def _node(self, sym: str, epoch: int) -> str:
        if epoch <= 0:
            return sym
        return f"{sym}@{(self._away or {})[sym][epoch - 1]}"

    def _efind(self, s: str) -> str:
        while self._eparent.get(s, s) != s:
            s = self._eparent[s]
        return s

    def class_at(self, symbol: str, date: Optional[str] = None, *,
                 before: bool = False) -> str:
        """Identical-property class of `symbol` as of `date` (a TRADE
        date; None = now, after every event). A row ON a rename date is
        still the renamed security; only a row AFTER the date is a new
        identity. `before=True` (the identity a SPLIT row dated `date`
        acts on) is the same boundary. Equals the date-blind
        canonical() class for every row of a book in which no renamed
        ticker trades again after its rename date."""
        self._epoch_build()
        return self._efind(self._node(
            symbol, self._epoch(symbol, date, before=before)))

    def class_of_row(self, t: Any) -> str:
        """class_at for a transaction row: its trade date; a SPLIT row
        belongs to the identity whose shares it acts on (just before
        its date)."""
        return self.class_at(getattr(t, 'symbol', '') or '',
                             (getattr(t, 'date', '') or
                              getattr(t, 'date_settle', '') or None),
                             before=getattr(t, 'action', '') == 'SPLIT')

    def renamed_away(self) -> Dict[str, List[str]]:
        """{old symbol: [rename dates]} (trade dates, copies merged)."""
        self._epoch_build()
        return {k: list(v) for k, v in (self._away or {}).items()}

    # ------------------------------------------------------------ factors

    def factor(self, symbol: str, from_date: str, to_date: str) -> float:
        """(from, to] cumulative ratio using the MIGRATED schedule — the
        events live under the rename target once a rename is processed.
        This is the US §1091 semantics: replacement records stay in their
        own trade-date units forever and convert at match time."""
        return cumulative_factor(self._schedule.get(symbol, []),
                                 from_date, to_date)

    def alias_factor(self, symbol: str, from_date: str, to_date: str) -> float:
        """(from, to] cumulative ratio over ALL events of `symbol`'s
        rename-equivalence class — the Canada engine's semantics, where a
        loss on the pre-rename ticker measures windows across the whole
        chain."""
        if self._class_events is None:
            grouped: Dict[str, List[Tuple[str, float]]] = {}
            for sym, events in self._schedule.items():
                grouped.setdefault(self._find(sym), []).extend(events)
            self._class_events = grouped
        events = self._class_events.get(self._find(symbol), [])
        return cumulative_factor(events, from_date, to_date)

    def _lineage_end_state(self, symbol: str, from_date: str, *,
                           inclusive: bool = False
                           ) -> Tuple[float, str]:
        """Forward-simulate ONE share of `symbol`, denominated at
        `from_date`, through every later corporate event that touches
        its lineage: an event applies only when the symbol it NAMES is
        the share's current symbol at that point (so a rename-split
        A->B never scales shares already trading as B). Returns
        (factor, final_symbol) after the last event in the book.
        Boundary matches cumulative_factor's (from, to] convention —
        an event ON from_date is already baked into a quantity
        denominated at that date; pass inclusive=True for rows that
        PRE-EXIST their sort date (opening balances, settle-lagged
        executions), whose shares a same-date event does scale."""
        if self._events_sorted is None:
            self._events_sorted = sorted(
                self._events, key=lambda e: e[0])
        qty, sym = 1.0, symbol
        evs = self._events_sorted
        i = 0
        while i < len(evs):
            d = evs[i][0]
            j = i
            while j < len(evs) and evs[j][0] == d:
                j += 1
            day = evs[i:j]
            i = j
            if d < from_date or (d == from_date and not inclusive):
                continue
            # One day's events follow the share's chain, not the input
            # order: A->B and B->C on one day take an A share to C
            # whichever row is listed first, as factor() and
            # alias_factor() already do (A2-0983 / S021-04). Each event
            # applies at most once; a plain split leaves the symbol, so
            # it is applied on its own pass.
            used = [False] * len(day)
            moved = True
            while moved:
                moved = False
                for k, (_d, e_sym, ratio, new_sym) in enumerate(day):
                    if used[k] or e_sym != sym:
                        continue
                    used[k] = True
                    moved = True
                    if ratio:
                        qty *= ratio
                    if new_sym:
                        sym = new_sym
                        break
        return qty, sym

    def end_factor(self, symbol: str, from_date: str, *,
                   inclusive: bool = False) -> float:
        """Factor taking a quantity of `symbol` denominated at
        `from_date` into the units it has after every later event in
        the book (the wash radar compares quantities from different
        dates in these 'today' units — audit A2-0382)."""
        f, _sym = self._lineage_end_state(symbol, from_date,
                                          inclusive=inclusive)
        return f or 1.0

    def lineage_factor(self, symbol: str, from_date: str,
                       ref_symbol: str, ref_date: str, *,
                       from_inclusive: bool = False,
                       ref_inclusive: bool = False) -> float:
        """Factor converting a quantity of `symbol` denominated at
        `from_date` into `ref_symbol`'s `ref_date` denomination,
        applying ONLY events that actually scale shares along each
        side's own lineage path. This is the per-raw-symbol
        replacement for alias_factor in the Canada balance walks:
        alias_factor pools every ratio of the rename class, so a
        rename-split A->B (ratio r) into a symbol that ALREADY TRADES
        divided native B shares — which the event never scaled — by r
        as well (2026-09 adversarial audit, finding 1).

        Both sides are projected forward through the whole event book;
        when the lineages converge on the same final symbol, the ratio
        of the projections is the exact path factor (shared future
        events cancel). For lineages that never converge (rename
        classes connected only through a dead branch — malformed
        books) this falls back to the legacy class-wide alias_factor
        rather than guessing a path."""
        f_a, s_a = self._lineage_end_state(symbol, from_date,
                                           inclusive=from_inclusive)
        # ref_inclusive: the REFERENCE quantity (a settle-lagged loss
        # sale processed in the pre-existing phase) is denominated
        # BEFORE a same-date event, so the ref projection must apply
        # that event too — otherwise a SPLIT dated exactly on the loss
        # settle date is baked into one side only and the denial
        # scales by the split ratio (2026-09 round-four audit,
        # finding 3: a 2:1 split on the settle date doubled the
        # denied amount).
        f_r, s_r = self._lineage_end_state(ref_symbol, ref_date,
                                           inclusive=ref_inclusive)
        if s_a != s_r or not f_a or not f_r:
            return self.alias_factor(symbol, from_date, ref_date)
        return f_a / f_r

    # ------------------------------------------------------------ dedup

    @staticmethod
    def dedupe(txs: List[Any], seen: Optional[Set] = None, *,
               per_account: bool = False) -> List[Any]:
        """Drop repeated SPLIT rows for the same corporate event (each
        brokerage parser emits its own copy per account with distinct ids,
        so --dedup can't collapse them). Pass a shared `seen` to dedupe
        across several lists (the engines share one across the
        taxable/sheltered/affiliated inputs); `per_account=True` keys the
        event per account for the walks whose pools are per-account."""
        if seen is None:
            seen = set()
        # Earliest copy first: a split booked on two dates by two
        # brokers (split_seen's window) is applied on the EARLIER date,
        # so post-split trades in between never meet a pre-split pool.
        keep = set()
        splits = [t for t in txs if getattr(t, 'action', None) == 'SPLIT']
        for t in sorted(splits, key=lambda t: t.date or ''):
            prior = split_seen(
                seen, t.symbol, t.date, t.quantity,
                getattr(t, 'symbol_new', ''),
                account=(getattr(t, 'account', '') if per_account
                         else None))
            if prior is None:
                keep.add(id(t))
            else:
                note_split_date_conflict(t.symbol, prior, t.date,
                                         t.quantity)
        out = [t for t in txs
               if getattr(t, 'action', None) != 'SPLIT' or id(t) in keep]
        return out
