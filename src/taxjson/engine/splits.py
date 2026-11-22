"""Corporate splits and renames: repeated splits dropped, per-account rename
ratios folded into one event.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib.stage_msg import emit_line
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, TypeAlias

from taxjson.lib.corporate_timeline import (
    SplitTimeline, event_sort_key, normalize_symbol_new, split_seen, SPLIT_DATE_WINDOW_DAYS,
)

from taxjson.model import money as M
from taxjson.model.transaction import TaxTransaction

from taxjson.engine.assign import _day_gap

# A rename-SPLIT row of one corporate event: (list index, row index, row)
# in the (taxable, sheltered, affiliated) lists; an event's key: (symbol,
# earliest date, rename target).
_RenameRow: TypeAlias = Tuple[int, int, TaxTransaction]
_RenameKey: TypeAlias = Tuple[str, str, str]


def _dedupe_corporate_splits(txs: List[TaxTransaction],
                             seen: Set[Tuple[Any, ...]]) -> List[TaxTransaction]:
    """Drop repeated SPLIT rows for the same corporate event.

    A stock split is a property of the SECURITY, not of an account: every
    brokerage parser emits its own SPLIT row per account, and their ids differ
    (account/description feed compute_id), so `--dedup` can't collapse them.
    On merged multi-account input — one account fed by several brokers, or the
    combined sheltered context file — the same event then arrives N times and
    the engines' symbol-global pools/walks get scaled by ratio**N (e.g. two
    accounts holding one stock through its split doubled the pool).

    Keyed on (symbol, date, ratio, symbol_new); `seen` is shared across the
    taxable/sheltered/affiliated lists so a split present in more than one
    list is still applied exactly once. Limitation: two brokers reporting
    slightly different ratios for the same event (rounding) won't collapse.

    Delegates to SplitTimeline.dedupe — the one definition of split-event
    identity (see lib/corporate_timeline.py).
    """
    return SplitTimeline.dedupe(txs, seen)


def _rename_ratio_groups(lists: Sequence[Sequence[TaxTransaction]]
                         ) -> Dict[_RenameKey, List[_RenameRow]]:
    """The rename-SPLIT rows of each corporate event, keyed (symbol,
    earliest date, target) -> [(list index, row index, row)]. One merger
    booked by two brokers on different dates (IB 06-11, RBC 06-15) is
    ONE event: rows of a (symbol, target) pair within
    SPLIT_DATE_WINDOW_DAYS of each other form one group (audit S071-01
    — an exact-date key left the second broker's row to find an empty
    pool and drop its extra share)."""
    by_pair: Dict[Tuple[str, str], List[_RenameRow]] = {}
    for li, lst in enumerate(lists):
        for i, t in enumerate(lst):
            if t.action != 'SPLIT' or not t.date:
                continue
            new = normalize_symbol_new(t.symbol, getattr(t, 'symbol_new', ''))
            if not new:
                continue
            by_pair.setdefault((t.symbol, new), []).append((li, i, t))
    groups: Dict[_RenameKey, List[_RenameRow]] = {}
    for (sym, new), rows in by_pair.items():
        rows.sort(key=lambda r: r[2].date)
        cluster: List[_RenameRow] = []
        for r in rows:
            if cluster:
                gap = _day_gap(cluster[-1][2].date, r[2].date)
                if gap is None or gap > SPLIT_DATE_WINDOW_DAYS:
                    groups[(sym, cluster[0][2].date, new)] = cluster
                    cluster = []
            cluster.append(r)
        if cluster:
            groups[(sym, cluster[0][2].date, new)] = cluster
    return groups


def _multi_ratio_rename_groups(lists: Sequence[Sequence[TaxTransaction]]
                               ) -> Dict[_RenameKey, List[_RenameRow]]:
    """The events of _rename_ratio_groups whose rows carry more than one
    ratio: a merger's per-account EMPIRICAL deliveries (12 -> 12 and
    40 -> 42; corp_actions._emit_basis_carryover_rename)."""
    return {k: rows for k, rows in _rename_ratio_groups(lists).items()
            if len({M.units(float(r[2].quantity or 0), 9) for r in rows}) > 1}


def _fold_per_account_rename_ratios(taxable: List[TaxTransaction],
                                    sheltered: List[TaxTransaction],
                                    affiliated: List[TaxTransaction],
                                    *, profile: str = 'ca_balance',
                                    date_of: Optional[Callable[[TaxTransaction], str]] = None,
                                    moves: Sequence[TaxTransaction] = ()
                                    ) -> Tuple[List[TaxTransaction], List[TaxTransaction],
                                               List[TaxTransaction]]:
    """One merger, per-account EMPIRICAL ratios -> one corporate event.

    corp_actions emits a rename-SPLIT per account with the ratio the
    broker actually delivered (qty_received / qty_disposed, so a snapped
    fractional entitlement leaves no dust). Two accounts of one merger
    can therefore carry different ratios (12 ABC -> 12 ABD, 40 -> 42).
    Canada pools are symbol-global (s.47), so those rows neither dedupe
    (the ratio is part of the event key) nor scale their own account:
    the first renamed the WHOLE pool at its account's ratio and the
    second found no pool — shares went missing and the gain moved
    (2026-09 engine audit: fewer shares booked than were held).

    Rows of one event (same symbol, date and rename target) with more
    than one ratio are folded into ONE row whose ratio is the
    balance-weighted mean sum(q_a * r_a) / sum(q_a) over the TAXABLE
    accounts' holdings just before the event (all scopes when the
    taxable book holds none) — the pool lands on exactly the shares the
    broker delivered in total, cost preserved. Accounts holding the
    symbol without a row of their own keep the first row's ratio, as
    before. Per-account balances in the wash walks become the weighted
    share of that total (a fraction of a share off per account).

    `profile` / `date_of` order the balance walk: the Canada engine's
    settle-first ladder (the default); a US book whose lots are
    symbol-wide (no per-account basis) and the T1135 cost walk pass
    their own. `moves`: the legs of the moves between two of your
    taxable accounts that the caller's book no longer holds (the Canada
    engine books none: one ACB pool, _own_move_view): they move the
    TAXABLE balances the weights read, and are never returned (backlog
    TJ-0280: a merger after such a move weighed the holdings without
    it)."""
    lists = (taxable, sheltered, affiliated)
    todo = _multi_ratio_rename_groups(lists)
    if not todo:
        return taxable, sheltered, affiliated

    def _sd(t: TaxTransaction) -> str:
        return t.date_settle or t.date

    if date_of is None:
        date_of = _sd
    drop = set()
    replace: Dict[Tuple[int, int], TaxTransaction] = {}
    for (sym, d, new), rows in todo.items():
        first = rows[0][2]
        split_key = event_sort_key(first, profile=profile, date_of=date_of)
        ratio_of: Dict[str, float] = {}
        for _li, _i, t in rows:
            ratio_of.setdefault(t.account, float(t.quantity or 0))
        r0 = float(first.quantity or 0)

        def _weights(scopes: Tuple[int, ...]) -> Dict[str, float]:
            # Every symbol's balance per account up to the event, the
            # renames replayed: shares bought under an earlier ticker
            # and renamed INTO `sym` weigh too (reading only `sym`'s
            # own rows found no weights after a prior rename, and the
            # first row then migrated the whole pool: 2026-10 audit
            # R01). An own-account move (TRANSFER) moves them.
            bal: Dict[str, Dict[str, float]] = {}
            seen_plain: Set[Tuple[Any, ...]] = set()
            seen_rename = set()
            src = [t for li in scopes
                   for t in list(lists[li]) + (list(moves) if li == 0
                                               else [])
                   if event_sort_key(t, profile=profile,
                                     date_of=date_of) < split_key]
            src.sort(key=lambda t: event_sort_key(
                t, profile=profile, date_of=date_of))
            for t in src:
                if t.action in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE',
                                'TRANSFER'):
                    b = bal.setdefault(t.symbol, {})
                    b[t.account] = (b.get(t.account, 0.0)
                                    + float(t.quantity or 0))
                elif t.action == 'SPLIT':
                    tgt = normalize_symbol_new(t.symbol, t.symbol_new)
                    if not tgt:
                        if split_seen(seen_plain, t.symbol, t.date,
                                      t.quantity, '') is not None:
                            continue
                        for a in bal.get(t.symbol, {}):
                            bal[t.symbol][a] *= float(t.quantity or 0)
                        continue
                    ev_key = (t.symbol, t.date, tgt)
                    if ev_key in seen_rename:
                        continue
                    seen_rename.add(ev_key)
                    # One rename event: each account's own ratio where
                    # it has a row, else the first row's.
                    ev_ratio: Dict[str, float] = {}
                    for u in src:
                        if (u.action == 'SPLIT' and u.symbol == t.symbol
                                and u.date == t.date
                                and normalize_symbol_new(
                                    u.symbol, u.symbol_new) == tgt):
                            ev_ratio.setdefault(u.account,
                                                float(u.quantity or 0))
                    r_first = float(t.quantity or 0)
                    moved = bal.pop(t.symbol, {})
                    dst = bal.setdefault(tgt, {})
                    for a, q in moved.items():
                        dst[a] = dst.get(a, 0.0) + q * ev_ratio.get(a, r_first)
            return {a: q for a, q in bal.get(sym, {}).items() if q > 1e-9}

        w = _weights((0,)) or _weights((0, 1, 2))
        tot = sum(w.values())
        if tot <= 1e-9:
            continue
        r_eff = sum(q * ratio_of.get(a, r0) for a, q in w.items()) / tot
        replace[(rows[0][0], rows[0][1])] = TaxTransaction(
            **{**first.to_dict(), 'quantity': r_eff, 'id': first.id})
        for li, i, _t in rows[1:]:
            drop.add((li, i))
        emit_line(f"NOTE: {sym} -> {new} on {d}: per-account merger ratios "
              f"{sorted(set(M.units(v, 6) for v in ratio_of.values()))} "
              f"applied to the symbol-wide pool as one event at the "
              f"holdings-weighted ratio {r_eff:.6g}.")
    out = []
    for li, lst in enumerate(lists):
        out.append([replace.get((li, i), t) for i, t in enumerate(lst)
                    if (li, i) not in drop])
    return out[0], out[1], out[2]
