"""Option assignments: each ASSIGN paired with its own stock legs and placed
beside them.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib.stage_msg import emit_line
import math
import re
from typing import (
    TYPE_CHECKING, Any, Callable, Dict, Hashable, Iterable, List, NamedTuple, Optional, Sequence,
    Set, Tuple,
)
from datetime import datetime

from taxjson.model import money as M
from taxjson.lib.option_symbols import (
    _FUTURES_PREFIX_RE, exercise_target, is_option_symbol, parse_option_expiry, parse_option_right,
    parse_option_strike, parse_option_underlying, root_matches_stock,
)
from taxjson.lib.contract_size import equity_option_size

if TYPE_CHECKING:
    from taxjson.model.transaction import TaxTransaction
    from taxjson.engine.rows import RowId


# A marked assignment stock leg (the Webull two-row convention) is
# stamped with its option leg's trade date by the parser; allow a short
# posting lag, never more (a marked leg a year later belongs to a
# different assignment).
_MARKED_LEG_MAX_LAG_DAYS = 7


# An assignment's stock leg dated BEFORE its option row (a broker that
# books the shares on the assignment notice and the option on the next
# business day, or the reverse posting order) — up to a weekend apart.
_ASSIGN_LEG_LEAD_DAYS = 3


def _assign_delivery_shares(opt_tx: TaxTransaction) -> Optional[float]:
    """Units an option ASSIGN delivers: contracts x the declared contract
    size (a mini x10, an adjusted deliverable — audit A2-0196), else 100
    per equity option; one futures contract per futures option (its
    declared multiplier is the futures' dollar size, not a unit count).
    A warrant exercise delivers warrants x the declared shares per
    warrant (`multiplier`); unknown (None) when none is declared."""
    q = abs(float(opt_tx.quantity or 0.0))
    if q < 1e-12:
        return None
    if exercise_target(opt_tx):
        m = float(getattr(opt_tx, 'multiplier', 0.0) or 0.0)
        return q * m if m > 0 else None
    if _FUTURES_PREFIX_RE.match(opt_tx.symbol or ''):
        return q
    return q * equity_option_size(opt_tx)


def _assign_direction(opt_tx: TaxTransaction) -> Optional[int]:
    """+1 when the assignment makes the account BUY the underlying (short
    put assigned / long call exercised), -1 when it SELLS."""
    q = float(opt_tx.quantity or 0.0)
    if exercise_target(opt_tx):
        # A warrant is a long call: exercising it (the leg closes the
        # holding, q < 0) buys the shares.
        return 1 if q < 0 else None
    right = parse_option_right(opt_tx.symbol)
    if right not in ('C', 'P') or abs(q) < 1e-12:
        return None
    return 1 if (right == 'P') == (q > 0) else -1


def _signed_day_gap(a: str, b: str) -> Optional[int]:
    """b - a in days, or None when either date is unparsable."""
    try:
        return (datetime.strptime(b, '%Y-%m-%d')
                - datetime.strptime(a, '%Y-%m-%d')).days
    except (TypeError, ValueError):
        return None


class _AssignPairs(dict[Any, List[Any]]):
    """`_pair_assign_legs`' pairs, {option id: [leg id, ...]}, with each
    pair's shares in `qty` ({(option id, leg id): shares}). A leg several
    assignments share (one marked delivery of two puts' shares, TJ-0838)
    is each one's up to its claim (Canada, equal strikes on one day: pro
    rata by what each delivers, TJ-2750), and the ledger folds each
    premium into the shares its own assignment delivered of it, never
    into another's or into none (s.49(3.1); CA-OPT-08; TJ-2719)."""
    __slots__ = ('qty',)

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.qty: Dict[Tuple[Any, Any], float] = {}


def _series_rank(symbol: Optional[str]) -> Tuple[int, str]:
    """An option series' place among those tied for a delivery (CA-OPT-08;
    TJ-2750, TJ-3181): by its expiry as the engine parses it; a symbol
    with no expiry after every one that has, by its text."""
    exp = parse_option_expiry(symbol or '')
    return (0, exp) if exp else (1, symbol or '')


def _pro_rata(total: float,
              members: Iterable[Tuple[Any, float, float, Tuple[Tuple[int, str], int]]]
              ) -> Dict[Any, float]:
    """`total` units split among `members` ([(key, weight, cap, rank)], in
    any order: they are sorted here, the larger weight first, then
    `rank`) in proportion to their weights, none over its cap (a capped
    member's excess goes to the rest, again pro rata). Whole units when
    the total and every cap are whole: a share is exact where it
    divides, else rounded down, and the units that leaves go one each to
    the largest fraction dropped, then by the members' order. A
    fractional total or cap (a warrant's shares, an adjusted
    deliverable) is split exactly (TJ-3180). So the split is the same
    whatever order the input came in (CA-OPT-08; TJ-2750). Returns
    {key: share}."""
    members = sorted(members, key=lambda m: (-m[1], m[3]))
    out = {m[0]: 0.0 for m in members}
    rest = [m for m in members if m[1] > 0 and m[2] > 1e-9]
    rem = total
    while rest:
        tw = sum(m[1] for m in rest)
        full = [m for m in rest if rem * m[1] / tw >= m[2] - 1e-9]
        if not full:
            break
        for m in full:
            out[m[0]] = m[2]
            rem -= m[2]
        rest = [m for m in rest if m not in full]
    if not rest or rem <= 1e-9:
        return out
    tw = sum(m[1] for m in rest)
    if not all(abs(x - math.floor(x + 0.5)) < 1e-9
               for x in [total] + [m[2] for m in members]):
        for m in rest:
            out[m[0]] = rem * m[1] / tw
        return out
    order = []
    for i, m in enumerate(rest):
        raw = rem * m[1] / tw
        out[m[0]] = float(math.floor(raw + 1e-9))     # exact where it divides
        order.append((out[m[0]] - raw, i, m))
    order.sort(key=lambda x: x[:2])
    over = rem - sum(out[m[0]] for m in rest)
    while over > 1e-9:
        placed = False
        for _f, _i, m in order:
            room = m[2] - out[m[0]]
            if room <= 1e-9 or over <= 1e-9:
                continue
            got = min(1.0, room, over)
            out[m[0]] += got
            over -= got
            placed = True
        if not placed:
            break
    return out


class _AssignCand(NamedTuple):
    """One (option, leg) pair that may pair, for
    `_allocate_assign_legs`: `sort` (best first, the competition's key: the
    leg's identity signs, the day gap, after the option row before before it,
    the nearest row; a tied option's the nearest of its peers'), the option's
    and the leg's keys, the leg's quantity, the option's delivered quantity
    (None when unknown), strong identity, `tie` (None, or (the leg's place in
    the stream, at the strike, marked, sized, the day gap, before the option's
    day, strike, kind): a tie group's claim on the leg), `rank` (the option's
    tie-break: series, then row) and `peer` (None, or the key of the tie
    group, the assignments of one strike and kind on one day the option
    belongs to)."""
    sort: Tuple[Any, ...]
    okey: Hashable
    lkey: Hashable
    q: float
    size: Optional[float]
    strong: bool
    tie: Optional[Tuple[Any, ...]]
    rank: Tuple[Tuple[int, str], int]
    peer: Hashable


def _compete(cands: Sequence[_AssignCand]) -> Tuple[_AssignPairs, Dict[Any, Dict[Any, float]]]:
    """Stage one of `_allocate_assign_legs`: the competition for the legs
    exactly as it was before TJ-2750 (`_run_passes` over every candidate,
    each option on its own, a tied member like any other), of which only
    the claims of the other assignments are kept: untied options,
    exercises, other strikes, other kinds. The members of a tie group of
    two or more get their legs again in stage two. Returns (the kept
    claims; the groups' own stage-one claims, {group: {leg: shares}},
    for the record) (TJ-3266)."""
    members: Dict[Any, Set[Any]] = {}
    for c in cands:
        if c.peer is not None:
            members.setdefault(c.peer, set()).add(c.okey)
    group_of = {o: peer for peer, ms in members.items() if len(ms) > 1
                for o in ms}
    pairs = _run_passes([c._replace(tie=None, peer=None) for c in cands])
    kept = _AssignPairs()
    own: Dict[Any, Dict[Any, float]] = {}
    for (okey, lkey), got in pairs.qty.items():
        if okey in group_of:
            took = own.setdefault(group_of[okey], {})
            took[lkey] = took.get(lkey, 0.0) + got
            continue
        kept.setdefault(okey, []).append(lkey)
        kept.qty[(okey, lkey)] = got
    return kept, own


def _run_passes(cands: Iterable[_AssignCand]) -> _AssignPairs:
    """The claims, best pair first, one option at a time: each option its
    best leg first, then the ones still short extra legs on strong
    identity until their delivered quantity is covered. Each leg's shares
    are claimed once; a leg one option already took part of goes to the
    next only while its unclaimed shares cover what that one still needs
    (TJ-0838), never a scrap. The pairing as it was before TJ-2750."""
    cands = sorted(cands, key=lambda c: c.sort)
    pairs = _AssignPairs()
    covered: Dict[Any, float] = {}
    left: Dict[Any, float] = {}

    def need(c: _AssignCand) -> float:
        assert c.size is not None, "read for a sized option only"
        return c.size - covered.get(c.okey, 0.0)

    def claim(c: _AssignCand) -> None:
        rem = left.get(c.lkey, c.q)
        if rem <= 1e-6 or (c.lkey in left and not (
                c.strong and c.size is not None and rem >= need(c) - 1e-6)):
            return
        got = rem if c.size is None else min(rem, need(c))
        left[c.lkey] = rem - got
        covered[c.okey] = covered.get(c.okey, 0.0) + got
        pairs.setdefault(c.okey, []).append(c.lkey)
        pairs.qty[(c.okey, c.lkey)] = pairs.qty.get((c.okey, c.lkey), 0.0) + got
    for c in cands:
        if c.okey not in pairs:
            claim(c)
    for c in cands:
        if (c.strong and c.okey in pairs and c.lkey not in pairs[c.okey]
                and c.size is not None and need(c) > 1e-6):
            claim(c)
    return pairs


def _fill_ties(cands: Sequence[_AssignCand], pairs: _AssignPairs) -> _AssignPairs:
    """Stage two of `_allocate_assign_legs`: the members of each tie
    group of two or more fill their needs (their delivered quantity)
    from what the legs have left after the claims stage one kept
    (`pairs`, added to here). A member's candidates, its plain purchases
    at another price among them (TJ-3267), rank by the strength of the
    match first, in the competition's identity order: a leg at the
    strike, then a marked one, then one of exactly its quantity (across
    the day's legs; TJ-3265, TJ-3274); then the nearest day, a later day
    before an earlier one, then the leg's own place in the stream, then
    the group. At each leg, in
    that order, the members in need for which it ranks alike split it
    pro rata by delivered quantity, each up to what it still needs
    (`_pro_rata`), a leg another assignment partly claimed going whole,
    in their order (the larger, then `rank`), to each it still covers,
    never a scrap (TJ-0838). A need not met at one leg goes on to the
    next, and the legs are swept again while a sweep placed something
    (TJ-3251 to TJ-3253)."""
    members: Dict[Any, Set[Any]] = {}
    for c in cands:
        if c.peer is not None:
            members.setdefault(c.peer, set()).add(c.okey)
    group_of = {o: peer for peer, ms in members.items() if len(ms) > 1
                for o in ms}
    groups = set(group_of.values())
    at: Dict[Tuple[Any, Any], Dict[Any, _AssignCand]] = {}
    for c in cands:
        if c.tie is not None and c.okey in group_of:
            at.setdefault((c.peer, c.lkey), {})[c.okey] = c
    taken: Dict[Any, float] = {}
    covered: Dict[Any, float] = {}
    # What is on each leg from outside a group, which TJ-0838's guard
    # reads: {leg: {group: shares that are not its members' own}}.
    outside: Dict[Any, Dict[Any, float]] = {}

    def note(okey: Any, lkey: Any, got: float) -> None:
        taken[lkey] = taken.get(lkey, 0.0) + got
        covered[okey] = covered.get(okey, 0.0) + got
        for peer in groups - {group_of.get(okey)}:
            outside.setdefault(lkey, {})[peer] = (
                outside.get(lkey, {}).get(peer, 0.0) + got)
    for (okey, lkey), got in pairs.qty.items():
        note(okey, lkey, got)
    legq = {c.lkey: c.q for c in cands}

    def tie(c: _AssignCand) -> Tuple[Any, ...]:
        assert c.tie is not None, "`at` holds tied candidates only"
        return c.tie

    def size(m: _AssignCand) -> float:
        assert m.size is not None, "a tied candidate is sized (`tie` is set with `size`)"
        return m.size

    def strength(c: _AssignCand) -> Tuple[int, int, int]:
        # The competition's identity order: at the strike, marked, then
        # exactly the member's quantity (TJ-3274).
        t = tie(c)
        return (0 if t[1] else 1, 0 if t[2] else 1, 0 if t[3] else 1)

    def give(m: _AssignCand, got: float) -> None:
        note(m.okey, m.lkey, got)
        if m.lkey not in pairs.get(m.okey, ()):
            pairs.setdefault(m.okey, []).append(m.lkey)
        pairs.qty[(m.okey, m.lkey)] = pairs.qty.get((m.okey, m.lkey), 0.0) + got

    def need(m: _AssignCand) -> float:
        return size(m) - covered.get(m.okey, 0.0)

    steps = sorted({(strength(c), tie(c)[4], tie(c)[5], tie(c)[0], c.peer, c.lkey)
                    for ms in at.values() for c in ms.values()})
    placed = True
    for _sweep in range(len(steps) + 1):
        if not placed:
            break
        placed = False
        for cls, _gap, _before, _pos, peer, lkey in steps:
            rem = legq[lkey] - taken.get(lkey, 0.0)
            grp = [m for m in at[(peer, lkey)].values()
                   if strength(m) == cls and need(m) > 1e-6]
            if rem <= 1e-6 or not grp:
                continue
            grp.sort(key=lambda m: (-size(m), m.rank))
            if outside.get(lkey, {}).get(peer, 0.0) > 1e-6:
                for m in grp:
                    if rem >= need(m) - 1e-6:
                        got = need(m)
                        rem -= got
                        give(m, got)
                        placed = True
                continue
            split = _pro_rata(rem, [(m.okey, size(m), need(m), m.rank)
                                    for m in grp])
            for m in grp:
                if split[m.okey] > 1e-9:
                    give(m, split[m.okey])
                    placed = True
    return pairs


def _allocate_assign_legs(cands: Sequence[_AssignCand], tie_pro_rata: bool = False) -> _AssignPairs:
    """The claims of option ASSIGNs on stock legs: the one allocator of
    the engines (`_pair_assign_legs`) and the stats views
    (lib/trade_stats `_assignment_legs`; TJ-3177) over `_AssignCand`s,
    one per (option, leg) that may pair. Without `tie_pro_rata` (the US
    engine, TJ-3173) the competition as it always was (`_run_passes`).
    With it (the Canadian engine, CA-OPT-08) two stages that share no
    key: the competition as before, of which only the other assignments'
    claims are kept, so they are what they were before TJ-2750 on every
    book (`_compete`); then the tied members share what is left, a leg
    of exactly one member's quantity first to it, in an order that reads
    no placement (`_fill_ties`), so among the peers the figures are the
    same in any order of the day's rows (TJ-2750, TJ-3218, TJ-3229,
    TJ-3251 to TJ-3253, TJ-3265 to TJ-3267). Returns {option key: [leg
    key, ...]} with each pair's shares."""
    if not tie_pro_rata:
        return _run_passes(cands)
    kept, _own = _compete(cands)
    return _fill_ties(cands, kept)


def _pair_assign_legs(stream: Iterable[TaxTransaction],
                      in_scope: Callable[[TaxTransaction], bool],
                      underlying_of: Optional[Callable[[TaxTransaction], Optional[str]]] = None,
                      tie_pro_rata: bool = False) -> _AssignPairs:
    """Pair each option ASSIGN in `stream` with its own stock leg(s):
    `_assign_candidates`' pairs that may pair, allocated by
    `_allocate_assign_legs`. Returns {option id: [leg id, ...]} (ids are
    unique after _disambiguate_duplicate_ids), with each pair's shares
    (`_AssignPairs`)."""
    return _allocate_assign_legs(
        _assign_candidates(stream, in_scope, underlying_of, tie_pro_rata),
        tie_pro_rata)


def _assign_candidates(stream: Iterable[TaxTransaction],
                       in_scope: Callable[[TaxTransaction], bool],
                       underlying_of: Optional[Callable[[TaxTransaction], Optional[str]]] = None,
                       tie_pro_rata: bool = False) -> List[_AssignCand]:
    """The pairs that may pair: each option ASSIGN in `stream` with ITS
    OWN stock leg(s), by identity rather than by position (audit
    A2-0050/0051/0052/0195/0203, the R1-33 and S019-00 intents), with
    the competition's key for `_allocate_assign_legs`.

    A candidate leg is a stock BUYSELL or marked ASSIGN row on the
    option's own (account, underlying), in the assignment's share
    direction, dated within _ASSIGN_LEG_LEAD_DAYS before to
    _MARKED_LEG_MAX_LAG_DAYS after the option row. A leg that sorts
    BEFORE its option row is accepted only on strong identity (a marked
    ASSIGN leg, or the delivered quantity at the strike); a leg after it
    needs one identity sign (marked, at the strike, or the delivered
    quantity) — a bare unrelated trade is left to the ledger's proximity
    rule. Pairs are chosen best-first: the strike as the leg's price,
    then a marked leg, then the delivered quantity, then the nearest
    date (after before before), so a plain-convention assignment keeps
    its own BUYSELL leg and never claims another option's marked leg,
    and two same-moment assignments are told apart by strike — never by
    staging order. An assignment filled in several legs takes extra legs
    at its strike (or marked) until its delivered quantity is covered
    (`_allocate_assign_legs`).

    `tie_pro_rata` (the Canadian engine's, CA-OPT-08; the US engine
    pairs as before, US-OPT-05, TJ-3173): assignments a leg cannot tell
    apart — the same kind (a written put with a written put, TJ-3221) at
    one strike on one day, the leg the delivered quantity of none or of
    several of them — share it pro rata by the quantity each delivers,
    each up to what it still needs (TJ-2750), the same in any order of
    that day's rows, option and share rows alike: the tie reads the
    day's gap, never a row's place among the day's rows (TJ-3178), and
    tied candidates rank by the leg's own place in the stream, not its
    distance from the option row, so a tie's deliveries are settled in
    their order, the earliest first (TJ-3218).

    Returns the `_AssignCand`s, one per (option, leg) that may pair."""
    pos: Dict[RowId, int] = {}
    legs: Dict[Tuple[str, str], List[TaxTransaction]] = {}
    opts = []
    for i, t in enumerate(stream):
        if not in_scope(t) or t.action not in ('BUYSELL', 'ASSIGN'):
            continue
        if is_option_symbol(t.symbol) or exercise_target(t):
            if t.action == 'ASSIGN':
                opts.append(t)
                pos[t.id] = i
        elif abs(float(t.quantity or 0.0)) > 1e-12:
            legs.setdefault((t.account, t.symbol), []).append(t)
            pos[t.id] = i
    if not opts or not legs:
        return []
    # The assignments of one strike and kind on one day in one account:
    # peers a leg may not tell apart (TJ-2750). Each competes for a leg
    # on its own, as before; the peers then share what the others left
    # (_allocate_assign_legs), every candidate of theirs carrying `tie`.
    # (account, underlying, strike, (right, quantity > 0), day)
    peers: Dict[Tuple[str, Optional[str], Optional[float], Tuple[Optional[str], bool], str],
                int] = {}
    keyed = []
    for o in opts:
        und = (underlying_of(o) if underlying_of
               else parse_option_underlying(o.symbol))
        strike = (None if exercise_target(o)
                  else parse_option_strike(o.symbol))
        kind = (parse_option_right(o.symbol), float(o.quantity or 0.0) > 0)
        pk = (o.account, und, strike, kind, (o.date or '')[:10])
        peers[pk] = peers.get(pk, 0) + 1
        keyed.append((o, und, strike, kind, pk))
    found = []
    for o, und, strike, kind, pk in keyed:
        lst = legs.get((o.account, und)) if und else None
        if not lst:
            continue
        d = _assign_direction(o)
        size = _assign_delivery_shares(o)
        rank = (_series_rank(o.symbol), pos[o.id])
        tied = tie_pro_rata and peers[pk] > 1 and strike and strike > 0
        for l in lst:
            q = float(l.quantity)
            if d is not None and (1 if q > 0 else -1) != d:
                continue
            gap = _signed_day_gap(o.date or '', l.date or '')
            if gap is None or not (-_ASSIGN_LEG_LEAD_DAYS <= gap
                                   <= _MARKED_LEG_MAX_LAG_DAYS):
                continue
            after = pos[l.id] > pos[o.id]
            marked = l.action == 'ASSIGN'
            at_strike = bool(strike and strike > 0 and abs(
                M.of(l.price or 0.0) - M.of(strike)) <= M.of(max(0.005, 1e-6 * strike)))
            sized = size is not None and abs(abs(q) - size) < 1e-6
            if after:
                if not (marked or at_strike or sized):
                    continue
            elif not (marked or (at_strike and sized)):
                continue
            place = (0 if after else 1, abs(pos[l.id] - pos[o.id]))
            score = (0 if at_strike else 1, 0 if marked else 1,
                     0 if sized else 1, abs(gap)) + place
            strong = marked or at_strike
            tie = ((pos[l.id], at_strike, marked, sized, abs(gap), gap < 0,
                    strike, kind) if tied and size is not None else None)
            found.append(_AssignCand((score, pos[o.id]), o.id, l.id, abs(q),
                                     size, strong, tie, rank,
                                     pk if tie is not None else None))
    return found


def _place_assign_options(stream: List[TaxTransaction],
                          pairs: Optional[Dict[RowId, List[RowId]]]) -> List[TaxTransaction]:
    """Move each paired option ASSIGN row to just before its first stock
    leg when that leg sorts earlier (a leg dated a day or two before the
    option row, A2-0051/0195): the premium is only known once the option
    leg closes, so the option must be processed first. Only the option's
    own row moves; every stock row keeps its place. The pairs name trade
    rows (`_assign_trade_rows`), and the move is by identity: a solver's
    DISALLOW carries its sale's id, and placing the option after every row
    of the leg's id walked it twice once the leg's loss was denied (a
    phantom open contract), while a row of a moved option's id was dropped
    (TJ-2686)."""
    if not pairs:
        return stream
    trade = _assign_trade_rows(stream)
    before: Dict[int, List[Tuple[int, TaxTransaction]]] = {}
    moved = set()
    for oid, lids in pairs.items():
        first = min(lids, key=lambda lid: trade.get(lid, (0,))[0])
        if oid in trade and first in trade and trade[first][0] < trade[oid][0]:
            before.setdefault(id(trade[first][1]), []).append(trade[oid])
            moved.add(id(trade[oid][1]))
    if not moved:
        return stream
    out = []
    for t in stream:
        if id(t) in moved:
            continue
        for _i, o in sorted(before.get(id(t), ()), key=lambda x: x[0]):
            out.append(o)
        out.append(t)
    return out


def _assign_trade_rows(stream: Iterable[TaxTransaction]) -> Dict[RowId, Tuple[int, TaxTransaction]]:
    """{id: (position, row)} of the stream's trade rows, the rows
    `_pair_assign_legs` names (a solver's DISALLOW or ADJUST is never one,
    whatever id it carries)."""
    return {t.id: (i, t) for i, t in enumerate(stream)
            if t.action in ('BUYSELL', 'ASSIGN')}


def _day_gap(a: str, b: str) -> Optional[int]:
    """|a - b| in days for two ISO dates, or None when either is unparsable."""
    try:
        return abs((datetime.strptime(a, '%Y-%m-%d')
                    - datetime.strptime(b, '%Y-%m-%d')).days)
    except (TypeError, ValueError):
        return None


def _split_underlying(sym: str) -> Tuple[str, str]:
    """('SAMPLD', 'TO') for 'SAMPLD.TO'; ('F:SAMPLX', 'US') for 'F:SAMPLX.US'."""
    if '.' in sym:
        base, _, ext = sym.rpartition('.')
        if ext.isalpha() and ext.isupper() and len(ext) <= 3:
            return base, ext
    return sym, ''


def _class_root_matches(root_base: str, stock_base: str) -> bool:
    """The share-class subset of root_matches_stock: an option root that
    drops the class of a class-share line ('SAMPLD' for SAMPLD.B, 'SAMPLCB' or
    'SAMPLC' for SAMPLC.B, 'ABC' for ABC.UN). Never a futures root and never
    an OCC-adjusted (digit) root — those are flagged, not resolved."""
    if (_FUTURES_PREFIX_RE.match(root_base)
            or _FUTURES_PREFIX_RE.match(stock_base)
            or root_base[-1:].isdigit()):
        return False
    head = re.split(r'[.\-]', stock_base)[0]
    if head == stock_base:
        return False
    return (root_base == head
            or root_base == stock_base.replace('.', '').replace('-', ''))


def class_root_aliases(symbols: Iterable[Optional[str]]) -> Dict[str, str]:
    """{option underlying: share line} for an option root that names no
    share line among `symbols` but exactly ONE class share of that root
    on the same market: RBC books a class share's Montreal calls under the root
    SAMPLD (SAMPLD271217C00030000.TO) while the shares are SAMPLD.B.TO; OCC spells
    class B calls SAMPLCB. Used by both engines' call-replacement rules
    (CA-SL-05 / US-WASH-12; audit A2-0015/0016/0207), the way the
    assignment resolver and the reports already map the root (S030-02,
    S040-11, S047-01). A root matching two class lines stays unresolved."""
    syms = {str(x).strip().upper() for x in symbols if x}
    shares = {x for x in syms if not is_option_symbol(x)}
    out: Dict[str, str] = {}
    for x in syms:
        und = parse_option_underlying(x) if is_option_symbol(x) else None
        if not und or und in shares or und in out:
            continue
        r_base, r_ext = _split_underlying(und)
        cands = [sh for sh in shares
                 if _split_underlying(sh)[1] == r_ext
                 and _class_root_matches(r_base, _split_underlying(sh)[0])]
        if len(cands) == 1:
            out[und] = cands[0]
    return out


def _make_assign_underlying_resolver(transactions: Iterable[TaxTransaction],
                                     date_of: Callable[[TaxTransaction], str],
                                     quiet: bool = False
                                     ) -> Callable[[TaxTransaction], Optional[str]]:
    """Return resolve(option_tx) -> underlying stock symbol for an option
    ASSIGN, or None when no stock line in the option's own account
    matches (a cash-settled index option, or a missing stock leg).

    The option root is used as-is when that exact symbol trades as stock
    in the account. Otherwise the root is matched to the account's stock
    lines by class / futures-month / OCC-adjustment spelling
    (root_matches_stock) and must name exactly ONE line that trades
    within _MARKED_LEG_MAX_LAG_DAYS of the assignment (audit R1-35,
    S019-01, S070-17, R1-176: SAMPLD for SAMPLD.B.TO, SAMPLCB for SAMPLC.B.US,
    F:SAMPLX for F:SAMPLXG6.US used to be treated as cash-settled, realizing the
    premium in the wrong year). An ambiguous match is left unresolved
    with a warning naming the candidates. `quiet` drops the per-match
    "resolved" note (a report that re-derives the engine's pairing,
    e.g. option-boundary, A2-0114; the engine run already printed it)."""
    dates: Dict[Tuple[str, str], List[str]] = {}
    for t in transactions:
        if is_option_symbol(t.symbol) or t.action not in ('BUYSELL', 'ASSIGN'):
            continue
        dates.setdefault((t.account, t.symbol), []).append(t.date or '')
    by_acct: Dict[str, List[str]] = {}
    for (acct, sym) in dates:
        by_acct.setdefault(acct, []).append(sym)
    cache: Dict[Tuple[str, str, str], Optional[str]] = {}

    def resolve(tx: TaxTransaction) -> Optional[str]:
        _ex = exercise_target(tx)
        if _ex:
            # A warrant/right exercise names its shares (the parser
            # paired the legs): no root matching.
            return _ex if (tx.account, _ex) in dates else None
        und = parse_option_underlying(tx.symbol)
        if not und:
            return None
        if (tx.account, und) in dates:
            return und
        ck = (tx.account, und, tx.date)
        if ck in cache:
            return cache[ck]
        root_base, ext = _split_underlying(und)
        near = []
        for sym in sorted(by_acct.get(tx.account, ())):
            s_base, s_ext = _split_underlying(sym)
            if s_ext != ext or not root_matches_stock(root_base, s_base):
                continue
            gaps = [_day_gap(d, tx.date or '') for d in dates[(tx.account, sym)]]
            if any(g is not None and g <= _MARKED_LEG_MAX_LAG_DAYS
                   for g in gaps):
                near.append(sym)
        out = None
        if len(near) == 1:
            out = near[0]
            if not quiet:
                emit_line(f"note: {tx.symbol}: option root {und} resolved to "
                      f"{out}, the stock line this account trades at the "
                      f"assignment — the premium rolls into its "
                      f"cost/proceeds.")
        elif len(near) > 1:
            emit_line(f"warning: {tx.symbol}: option root {und} matches "
                  f"several stock lines traded at the assignment "
                  f"({', '.join(near)}) — treated as cash-settled. Map "
                  f"the option to its stock in ticker.map and re-run.")
        cache[ck] = out
        return out

    return resolve
