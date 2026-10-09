"""Opening balances: positions taken from a broker's positions report.

An OPENING row (a `.tt` line `OPENING <snapshot-date> <symbol> <qty>
<currency> <total-cost> [<lot-date>]`, written by `taxjson opening`) is
a BUYSELL of type core.OPENING_TYPE dated the snapshot day. It sets the
position and its cost but is not a purchase (tax-logic CA-OPEN-01,
US-OPEN-01): the engines never treat it as superficial-loss / wash-sale
replacement property.

The snapshot already contains everything that happened before it, so
the account's other rows of a snapshot symbol dated on or before the
snapshot day would count the same shares twice. `apply_opening_cutoff`
(merge2, every equity account's books) leaves those rows out —
the CUT-OFF (CA-OPEN-03, US-OPEN-03):

* per account and per symbol: only the symbols the snapshot lists, in
  the account the OPENING rows sit in (another symbol, or the same
  symbol in another account, keeps its rows);
* position rows only (trades, transfers, splits/renames into the
  symbol, cost adjustments); income rows (dividends, interest,
  withholding) always stay;
* a row is "on or before" by its trade date;
* a SALE or a SHORT COVER left out that falls in the project's tax
  year (or later) stops the run: its gain would silently drop out of
  the year. A cover (a buy that closes or crosses a short position) is
  read from the position, walked back from the snapshot's quantity
  (issue #11). Take the snapshot before the year's first sale or cover
  of that symbol instead.

One snapshot date per symbol per account: two OPENING rows of one
symbol with different dates in one account are refused (which rows
would the second snapshot replace?).
"""
from __future__ import annotations

import sys
from typing import Dict, Iterable, List, Optional, Tuple

from taxjson.lib.core import is_opening_row

# Rows a snapshot replaces: everything that moves a position or its
# cost. Income (DIVIDEND, INTEREST, TAX, FEE, DIVIDEND_IN_LIEU) stays.
POSITION_ACTIONS = ('BUYSELL', 'ASSIGN', 'TRANSFER', 'SPLIT', 'ADJUST',
                    'DISALLOW', 'OPENING_BALANCE')

# The console line merge2 writes (echoed by `taxjson run`).
ATTENTION_OPENING = "warning: ATTENTION: opening: "


class OpeningError(ValueError):
    """An opening balance the books cannot use (double count, a sale of
    the tax year left out, a US lot without its date)."""


def _get(t, k, default=''):
    if isinstance(t, dict):
        v = t.get(k, default)
    else:
        v = getattr(t, k, default)
    return default if v is None else v


def snapshots(txs: Iterable) -> Dict[Tuple[str, str], str]:
    """{(account, symbol): snapshot date} of the OPENING rows. Raises
    OpeningError when one symbol of one account has two dates."""
    out: Dict[Tuple[str, str], str] = {}
    for t in txs:
        if not is_opening_row(t):
            continue
        key = (str(_get(t, 'account')), str(_get(t, 'symbol')))
        day = str(_get(t, 'date'))
        prev = out.get(key)
        if prev is not None and prev != day:
            raise OpeningError(
                f"account {key[0]}: {key[1]} has opening balances dated "
                f"{min(prev, day)} and {max(prev, day)} — one snapshot "
                f"date per symbol per account (the later one would "
                f"count the rows between them twice). Keep one "
                f"OPENING snapshot for {key[1]} in {key[0]}.")
        out[key] = day
    return out


def _final_symbol(sym: str, renames: List[Tuple[str, str, str]],
                  until: str) -> str:
    """Follow rename SPLIT rows (date, old, new) dated on or before
    `until` from `sym` to the symbol it became."""
    seen = set()
    for _ in range(len(renames) + 1):
        nxt = None
        for d, old, new in renames:
            if old == sym and d <= until:
                nxt = new
                break
        if nxt is None or nxt in seen:
            return sym
        seen.add(sym)
        sym = nxt
    return sym


def apply_opening_cutoff(txs: List, *, year: Optional[int] = None,
                         country: Optional[str] = None,
                         base_currency: Optional[str] = None,
                         report=None) -> List:
    """The rows of `txs` minus those an opening snapshot replaces (see
    the module docstring). `year` (the project's tax year) arms the
    refusal of a left-out sale in that year or later; `country` = usa
    refuses an OPENING without its lot date (US-OPEN-01) or in another
    currency than `base_currency` (US-OPEN-02). Prints one ATTENTION
    line per account with rows left out to `report` (stderr)."""
    report = report if report is not None else sys.stderr
    snaps = snapshots(txs)
    if not snaps:
        return txs
    if (country or '').lower() == 'usa':
        for t in txs:
            if not is_opening_row(t):
                continue
            if not str(_get(t, 'lot_date')):
                raise OpeningError(
                    f"account {_get(t, 'account')}: OPENING "
                    f"{_get(t, 'date')} {_get(t, 'symbol')} has no lot "
                    f"date — in a US project each lot's acquisition date "
                    f"decides its holding period (short- or long-term): "
                    f"write one OPENING line per lot with its purchase "
                    f"date as the last field (US-OPEN-01).")
            cur = str(_get(t, 'currency')).upper()
            if base_currency and cur and cur != base_currency.upper():
                raise OpeningError(
                    f"account {_get(t, 'account')}: OPENING "
                    f"{_get(t, 'date')} {_get(t, 'symbol')} is in {cur} — "
                    f"a US lot's basis is its {base_currency.upper()} "
                    f"cost on its own purchase date: enter the lot in "
                    f"{base_currency.upper()} (US-OPEN-02).")
    renames = sorted(
        (str(_get(t, 'date')), str(_get(t, 'symbol')),
         str(_get(t, 'symbol_new')).strip())
        for t in txs
        if _get(t, 'action') == 'SPLIT'
        and str(_get(t, 'symbol_new')).strip()
        and str(_get(t, 'symbol_new')).strip() != _get(t, 'symbol'))
    by_acct: Dict[str, Dict[str, str]] = {}
    for (acct, sym), day in snaps.items():
        by_acct.setdefault(acct, {})[sym] = day
    kept: List = []
    dropped: Dict[str, List] = {}
    for t in txs:
        acct = str(_get(t, 'account'))
        snap = by_acct.get(acct)
        if (not snap or is_opening_row(t)
                or _get(t, 'action') not in POSITION_ACTIONS):
            kept.append(t)
            continue
        day = str(_get(t, 'date'))
        sym = str(_get(t, 'symbol'))
        hit = None
        for s, d in snap.items():
            if day <= d and _final_symbol(sym, renames, d) == s:
                hit = s
                break
        if hit is None:
            kept.append(t)
            continue
        dropped.setdefault(acct, []).append((t, hit))
    if year:
        bad = _realizations_left_out(dropped, txs, by_acct, year,
                                     renames)
        if bad:
            t, hit, kind, closed = bad[0]
            acct = str(_get(t, 'account'))
            n_sale = sum(1 for b in bad if b[2] == 'sale')
            n_cover = len(bad) - n_sale
            what = ' and '.join(
                x for x in (f"{n_sale} sale(s)" if n_sale else '',
                            f"{n_cover} short cover(s)" if n_cover else '')
                if x)
            qty = abs(float(_get(t, 'quantity', 0.0) or 0.0))
            first = (f"{_get(t, 'date')} cover of {closed:g} "
                     f"{_get(t, 'symbol')}: a buy of {qty:g} that closes "
                     f"a short position" if kind == 'cover' else
                     f"{_get(t, 'date')} {qty:g} {_get(t, 'symbol')}")
            raise OpeningError(
                f"account {acct}: the opening balance of "
                f"{hit} dated {by_acct[acct][hit]}"
                f" would leave out {what} of the {year} tax "
                f"year (first: {first}) — their gains would drop out of "
                f"the year. Take the snapshot from a statement before "
                f"the year's first sale or cover (e.g. December 31 of "
                f"the year before), or remove the OPENING line(s).")
    for acct in sorted(dropped):
        rows = dropped[acct]
        syms = sorted({hit for _t, hit in rows})
        days = sorted(set(by_acct[acct].values()))
        print(f"{ATTENTION_OPENING}{acct}: the opening snapshot "
              f"({', '.join(days)}) replaces {len(rows)} earlier row(s) "
              f"of {', '.join(syms[:6])}"
              f"{f' (+{len(syms) - 6} more)' if len(syms) > 6 else ''}"
              f" — left out of the books so the shares are not counted "
              f"twice (income rows stay).", file=report)
    return kept


def _when(t, index: int) -> Tuple[str, str, int]:
    """A row's place in the walk: its date, its time (a missing time is
    00:00:00, as the engines order it) and its place in the input."""
    return (str(_get(t, 'date')), str(_get(t, 'time') or '00:00:00'),
            index)


def _realizations_left_out(dropped: Dict[str, List], txs: List,
                           by_acct: Dict[str, Dict[str, str]],
                           year: int,
                           renames: Optional[List[Tuple[str, str, str]]]
                           = None) -> List[Tuple]:
    """The left-out rows that realize a gain or loss in `year` or later,
    as (row, snapshot symbol, 'sale' | 'cover', quantity closed),
    earliest first.

    A SALE (a BUYSELL / ASSIGN of negative quantity) always counts —
    it may sell held shares or write an option, whose premium is
    income of the write date in a Canadian grant-timing book. A
    positive-quantity row counts when it closes a SHORT position (a
    cover, or a buy that crosses from short to long; issue #11): it
    realizes the short sale's gain. The position each row met is read
    BACKWARD from the snapshot's own quantity (the snapshot is the
    truth on its day), so a book whose history starts after the shares
    were bought does not mistake a later buy for a cover.

    A split or consolidation is an event of the security, not of an
    account: the walk undoes every SPLIT row up to the snapshot day that
    leads to the snapshot's symbol, whichever account's export carries
    it (each event once), and none of the account's own twice."""
    from taxjson.lib.corporate_timeline import normalize_symbol_new
    renames = renames or []
    where = {id(t): i for i, t in enumerate(txs)}
    splits = [(i, t) for i, t in enumerate(txs)
              if _get(t, 'action') == 'SPLIT']
    open_qty: Dict[Tuple[str, str], float] = {}
    for t in txs:
        if is_opening_row(t):
            key = (str(_get(t, 'account')), str(_get(t, 'symbol')))
            open_qty[key] = (open_qty.get(key, 0.0)
                             + float(_get(t, 'quantity', 0.0) or 0.0))
    bad: List[Tuple] = []
    for acct, rows in dropped.items():
        groups: Dict[str, List] = {}
        for t, hit in rows:
            if _get(t, 'action') != 'SPLIT':
                groups.setdefault(hit, []).append((where[id(t)], t))
        for hit, grp in groups.items():
            day = by_acct[acct][hit]
            seen = set()
            for i, t in splits:
                d = str(_get(t, 'date'))
                sym = str(_get(t, 'symbol'))
                if d > day or _final_symbol(sym, renames, day) != hit:
                    continue
                key = (d, sym, normalize_symbol_new(
                    sym, str(_get(t, 'symbol_new'))),
                    round(float(_get(t, 'quantity', 0.0) or 0.0), 9))
                if key not in seen:
                    seen.add(key)
                    grp.append((i, t))
            grp.sort(key=lambda it: _when(it[1], it[0]))
            pos: Dict[str, float] = {hit: open_qty.get((acct, hit), 0.0)}
            for _i, t in reversed(grp):
                act = _get(t, 'action')
                sym = str(_get(t, 'symbol'))
                q = float(_get(t, 'quantity', 0.0) or 0.0)
                if act == 'SPLIT':
                    # Undo `old -> new` at `ratio`: before it, the new
                    # symbol's position was the old one's (taken as
                    # nil of its own) and 1/ratio the size.
                    ratio = q if abs(q) > 1e-12 else 1.0
                    dst = str(_get(t, 'symbol_new')).strip() or sym
                    after = pos.pop(dst, 0.0)
                    pos[sym] = after / ratio
                    continue
                if act not in ('BUYSELL', 'ASSIGN', 'TRANSFER',
                               'OPENING_BALANCE'):
                    continue
                before = pos.get(sym, 0.0) - q
                pos[sym] = before
                if act not in ('BUYSELL', 'ASSIGN'):
                    continue
                last = max(str(_get(t, 'date')),
                           str(_get(t, 'date_settle')) or '')
                if last[:4] < str(year):
                    continue
                if q < 0:
                    bad.append((t, hit, 'sale', -q))
                elif q > 0 and before < -1e-9:
                    bad.append((t, hit, 'cover', min(q, -before)))
    bad.sort(key=lambda b: _when(b[0], where[id(b[0])]))
    return bad
