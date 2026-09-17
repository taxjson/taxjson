"""Authoritative split/rename arithmetic — the SplitTimeline.

Five audit cycles kept finding the same bug class regenerating: split
dedup keys, the (from, to] boundary for cumulative ratios, and rename-chain
following were each hand-implemented at ~7 sites (both engines, the phantom
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

from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# --------------------------------------------------------------- ordering
#
# Event ordering IS tax semantics here — it decides what is pre/post split,
# whether the opening balance gets scaled, and which lot FIFO consumes.
# It used to be defined by five hand-maintained sort keys (Canada main
# pass, Canada balance walks, US engine, phantom walks); two historical
# bugs (the OB/SPLIT 00:00:00 tie, the settle-lagged phase) were drift
# between those copies. `event_sort_key` is the single definition; the
# per-engine DIFFERENCES are explicit profiles, not implicit drift:
#
#   ca_main      (date, phase, time, priority)  — settle-basis dates
#   ca_balance   (date, phase, time)            — no priority (unchanged
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
#   plain_walk   (date, time)                   — the phantom walks'
#                                                  bare ordering.

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
    """Phantom-walk tie-break rung. The walks replay position on TRADE
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
    silently dropped the premium (2026-09 US-engine audit)."""
    OPENING_BALANCE = -1    # pre-window position: a same-timestamp SPLIT
    #                         must scale it (parsers stamp splits 00:00:00,
    #                         the OB anchor time)
    DISALLOW = 0
    ASSIGN_OPTION = 1
    ASSIGN_STOCK_OR_SPLIT = 2
    BUY = 3
    SELL = 4
    ADJUST = 5              # after BUY/SELL
    OTHER = 6


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
    # Lazy: core imports this module at load time.
    from taxjson.lib.core import is_option_symbol
    return is_option_symbol(getattr(tx, 'symbol', '') or '')


def _settle_first(tx: Any) -> str:
    return tx.date_settle if getattr(tx, 'date_settle', '') else tx.date


def _ca_phase(tx: Any, sort_date: str) -> int:
    if tx.action == 'OPENING_BALANCE':
        return Phase.PRE_EXISTING
    if tx.action == 'SPLIT':
        return Phase.SPLIT
    if tx.date and tx.date < sort_date:
        return Phase.PRE_EXISTING           # settle-lagged: executed earlier
    return Phase.EXECUTED_TODAY


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
    if tx.quantity > 0:
        return CaPriority.BUY
    if tx.quantity < 0:
        return CaPriority.SELL
    return CaPriority.OTHER


def _walk_priority(tx: Any) -> int:
    if tx.action == 'OPENING_BALANCE':
        return WalkPriority.OPENING_BALANCE
    if tx.action == 'SPLIT':
        return WalkPriority.SPLIT
    return WalkPriority.OTHER


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
    was dominated by engine ordering fixes never mirrored into its copy)."""
    ADJUST = 0
    ASSIGN_OR_SPLIT = 1
    BUY = 2
    SELL = 3


def radar_priority(tx: Any) -> int:
    """Tie-break rung for the wash-radar pool replay (sorted on
    (epoch, radar_priority) — the radar keys on numeric epochs, so it
    consumes the ladder directly rather than event_sort_key)."""
    action = (tx.action or '').upper()
    if action == 'ADJUST':
        return RadarPriority.ADJUST
    if action in ('ASSIGN', 'SPLIT'):
        return RadarPriority.ASSIGN_OR_SPLIT
    return RadarPriority.BUY if tx.quantity > 0 else RadarPriority.SELL


def event_sort_key(tx: Any, *, profile: str,
                   date_of: Optional[Callable[[Any], str]] = None) -> Tuple:
    """The one event-ordering definition. `date_of` selects the date basis
    (Canada passes its settle-first accessor, the US engine trade date);
    profiles select the ladder — see the module comment for the table."""
    if profile == 'plain_walk':
        return (tx.date, tx.time or '00:00:00')
    if profile == 'phantom_walk':
        return (tx.date, _walk_priority(tx), tx.time or '00:00:00')
    d = (date_of or _settle_first)(tx)
    if profile == 'ca_main':
        return (d, _ca_phase(tx, d), tx.time, _ca_priority(tx))
    if profile == 'ca_balance':
        return (d, _ca_phase(tx, d), tx.time)
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
    per-account walks (phantom detection, radar pools) where the same
    event must apply once per account pool. Known limitation (documented
    at the original core site): two brokers reporting slightly different
    ratios for the same event won't collapse."""
    new_sym = normalize_symbol_new(symbol, symbol_new)
    rounded = round(float(ratio or 0), 9)
    if account is None:
        return (symbol, date, rounded, new_sym)
    return (symbol, account, date, rounded, new_sym)


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
        (identity for symbols never renamed)."""
        return self._find(symbol)

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
            # Stable date sort: same-day chains keep input order (the
            # same positional semantics as from_transactions).
            self._events_sorted = sorted(
                self._events, key=lambda e: e[0])
        qty, sym = 1.0, symbol
        for d, e_sym, ratio, new_sym in self._events_sorted:
            if d < from_date or (d == from_date and not inclusive):
                continue
            if e_sym != sym:
                continue
            if ratio:
                qty *= ratio
            if new_sym:
                sym = new_sym
        return qty, sym

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

    def splits_between(self, symbol: str, lo: str, hi: str
                       ) -> List[Tuple[str, float]]:
        """The (lo, hi] event rows themselves, migrated-schedule view."""
        return [(d, r) for d, r in self._schedule.get(symbol, [])
                if lo < d <= hi]

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
        out = []
        for t in txs:
            if getattr(t, 'action', None) == 'SPLIT':
                key = split_event_key(
                    t.symbol, t.date, t.quantity,
                    getattr(t, 'symbol_new', ''),
                    account=(getattr(t, 'account', '') if per_account
                             else None))
                if key in seen:
                    continue
                seen.add(key)
            out.append(t)
        return out
