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
* a row of an old symbol follows only the renames dated on or after
  it: a ticker reused after its rename (bought under the old name
  later) is another holding and keeps its rows; the pass across
  accounts (taxjson-merge --opening-cutoff) cuts again with every
  account's renames, as a rename is the security's event (TJ-0964);
* a row is "on or before" by its trade date;
* a SALE or a SHORT COVER left out that falls in the project's tax
  year (or later) stops the run: its gain would silently drop out of
  the year. A cover (a buy that closes or crosses a short position) is
  read from the position, walked back from the snapshot's quantity
  (issue #11). So does a RETURN OF CAPITAL (a negative ADJUST) of the
  year that may have gone beyond the cost — a gain of its year (ITA
  s.40(3) / IRC §301(c)(3)) the snapshot's cost cannot carry: read
  from the cost walked back from the snapshot's (issue #77). Take the
  snapshot before the year's first sale, cover or return of capital
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
                  until: str, since: str = '') -> str:
    """Follow rename SPLIT rows (date, old, new) dated from `since` (the
    row's own date) to `until` from `sym` to the symbol it became; each
    later step only by a rename on or after the one before. A rename
    dated before the row is another holding's: a ticker reused after
    its rename (bought under the old name later) keeps its own name."""
    seen = set()
    for _ in range(len(renames) + 1):
        nxt = None
        for d, old, new in renames:
            if old == sym and since <= d <= until:
                nxt = new
                since = d
                break
        if nxt is None or nxt in seen:
            return sym
        seen.add(sym)
        sym = nxt
    return sym


def apply_opening_cutoff(txs: List, *, year: Optional[int] = None,
                         country: Optional[str] = None,
                         base_currency: Optional[str] = None,
                         tax_date: Optional[str] = None,
                         report=None,
                         cut: Optional[List] = None,
                         earlier_cut: Optional[List] = None) -> List:
    """The rows of `txs` minus those an opening snapshot replaces (see
    the module docstring). `year` (the project's tax year) arms the
    refusal of a left-out sale in that year or later, dated on the
    project's `tax_date` basis (settle | trade; the country's default
    when not given: lib/country.resolve_tax_date); `country` = usa
    refuses an OPENING without its lot date (US-OPEN-01) or in another
    currency than `base_currency` (US-OPEN-02). Prints one ATTENTION
    line per account with rows left out to `report` (stderr).

    `cut`: a list that receives the rows left out. `earlier_cut`: the
    rows an earlier pass already left out (the accounts' own merges, for
    the pass across accounts): read by the walk back from the snapshot,
    which needs the whole history, never judged again (backlog
    TJ-1201)."""
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
            if day <= d and _final_symbol(sym, renames, d,
                                          since=day) == s:
                hit = s
                break
        if hit is None:
            kept.append(t)
            continue
        dropped.setdefault(acct, []).append((t, hit))
    if year:
        bad = _realizations_left_out(dropped, txs, by_acct, year,
                                     renames, country=country,
                                     tax_date=tax_date,
                                     earlier=earlier_cut)
        if bad:
            raise OpeningError(_refusal(bad, by_acct, year, country))
    if cut is not None:
        cut.extend(t for rows in dropped.values() for t, _hit in rows)
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
                           = None, *, country: Optional[str] = None,
                           tax_date: Optional[str] = None,
                           earlier: Optional[List] = None
                           ) -> List[Tuple]:
    """The left-out rows that realize a gain or loss in `year` or later,
    as (row, snapshot symbol, 'sale' | 'cover' | 'roc', quantity closed
    or the return of capital's amount), earliest first.

    A SALE (a BUYSELL / ASSIGN of negative quantity) always counts —
    it may sell held shares or write an option, whose premium is
    income of the write date in a Canadian grant-timing book. A
    positive-quantity row counts when it closes a SHORT position (a
    cover, or a buy that crosses from short to long; issue #11): it
    realizes the short sale's gain. The position each row met is read
    BACKWARD from the snapshot's own quantity (the snapshot is the
    truth on its day), so a book whose history starts after the shares
    were bought does not mistake a later buy for a cover.

    A RETURN OF CAPITAL (a negative ADJUST; issue #77) counts when it
    may have gone beyond the cost — a gain of its year (CA s.40(3), US
    §301(c)(3)) a snapshot cannot carry: when no long shares were held
    at it, or the cost right after it, walked back from the snapshot's
    total cost (undoing the buys and cost adjustments between), is nil
    or unknown (another currency, a transfer, a sale between), or — US
    lots carry their own basis (US-ROC-02) — a snapshot lot bought on or
    before it has a nil basis. A ROC the cost shows was within it is
    history the snapshot replaces.

    A split or consolidation is an event of the security, not of an
    account: the walk undoes every SPLIT row up to the snapshot day that
    leads to the snapshot's symbol, whichever account's export carries
    it (each event once), and none of the account's own twice.

    A row's year follows the project's date basis (`tax_date`, else the
    country's default: Canada settles, the US trades): on trade dates a
    long sale traded Dec 31 that settles in January is the earlier
    year's (backlog TJ-0961). A cover keeps its later date on either
    basis: a short sale's loss can fall in the year it is delivered.

    `earlier`: rows an earlier pass already left out, walked for their
    units with a snapshot this pass left rows out of, and never counted
    themselves (backlog TJ-1201)."""
    from taxjson.lib.corporate_timeline import split_copies
    from taxjson.lib.country import CountryError, resolve_tax_date
    renames = renames or []
    earlier = list(earlier or [])
    only_walked = {id(t) for t in earlier}
    if earlier:
        txs = earlier + list(txs)
        renames = sorted(set(renames) | {
            (str(_get(t, 'date')), str(_get(t, 'symbol')),
             str(_get(t, 'symbol_new')).strip())
            for t in earlier if _get(t, 'action') == 'SPLIT'
            and str(_get(t, 'symbol_new')).strip()
            and str(_get(t, 'symbol_new')).strip() != _get(t, 'symbol')})
    try:
        on_trade = (resolve_tax_date(country, tax_date) == 'trade'
                    if country else tax_date == 'trade')
    except (CountryError, KeyError, ValueError):
        on_trade = False
    where = {id(t): i for i, t in enumerate(txs)}
    splits = [(i, t) for i, t in enumerate(txs)
              if _get(t, 'action') == 'SPLIT']
    open_qty: Dict[Tuple[str, str], float] = {}
    # (currency, total cost) of each snapshot — currency None when its
    # lots are in more than one — and its lots (lot date, cost).
    open_cost: Dict[Tuple[str, str], Tuple[Optional[str], float]] = {}
    lots: Dict[Tuple[str, str], List[Tuple[str, float]]] = {}
    for t in txs:
        if is_opening_row(t):
            key = (str(_get(t, 'account')), str(_get(t, 'symbol')))
            open_qty[key] = (open_qty.get(key, 0.0)
                             + float(_get(t, 'quantity', 0.0) or 0.0))
            cur = str(_get(t, 'currency')).upper()
            amt = abs(float(_get(t, 'net_amount', 0.0) or 0.0))
            pc, pa = open_cost.get(key, (cur, 0.0))
            open_cost[key] = (cur if pc == cur else None, pa + amt)
            lots.setdefault(key, []).append(
                (str(_get(t, 'lot_date')), amt))
    # Each split event counts once whichever account (or source) carries
    # it, copies on nearby dates included (split_copies, CA-CORP-12).
    split_dups = split_copies([t for _i, t in splits])
    bad: List[Tuple] = []
    for acct, rows in dropped.items():
        groups: Dict[str, List] = {}
        for t, hit in rows:
            if _get(t, 'action') != 'SPLIT':
                groups.setdefault(hit, []).append((where[id(t)], t))
        for hit, grp in groups.items():
            day = by_acct[acct][hit]
            for t in earlier:
                d = str(_get(t, 'date'))
                if (str(_get(t, 'account')) == acct
                        and _get(t, 'action') != 'SPLIT' and d <= day
                        and _final_symbol(str(_get(t, 'symbol')), renames,
                                          day, since=d) == hit):
                    grp.append((where[id(t)], t))
            for i, t in splits:
                d = str(_get(t, 'date'))
                sym = str(_get(t, 'symbol'))
                if d > day or _final_symbol(sym, renames, day,
                                             since=d) != hit \
                        or id(t) in split_dups:
                    continue
                grp.append((i, t))
            grp.sort(key=lambda it: _when(it[1], it[0]))
            pos: Dict[str, float] = {hit: open_qty.get((acct, hit), 0.0)}
            # The cost, walked back the same way (issue #77): None once
            # a row's cost effect is not known here (another currency,
            # a sale, a transfer, a cost the walk cannot undo).
            snap_cur, snap_cost = open_cost.get((acct, hit), (None, None))
            cost: Dict[str, Optional[float]] = {hit: snap_cost}
            for _i, t in reversed(grp):
                act = _get(t, 'action')
                sym = str(_get(t, 'symbol'))
                q = float(_get(t, 'quantity', 0.0) or 0.0)
                net = float(_get(t, 'net_amount', 0.0) or 0.0)
                same_cur = (snap_cur is not None and str(
                    _get(t, 'currency')).upper() == snap_cur)
                last = max(str(_get(t, 'date')),
                           str(_get(t, 'date_settle')) or '')
                in_year = last[:4] >= str(year)
                # On trade dates a sale (and a return of capital) is of
                # the year it is traded; a cover keeps the later date.
                traded_in_year = (str(_get(t, 'date'))[:4] >= str(year)
                                  if on_trade else in_year)
                if act == 'SPLIT':
                    # Undo `old -> new` at `ratio`: before it, the new
                    # symbol's position was the old one's (taken as
                    # nil of its own) and 1/ratio the size. The cost
                    # moves with it, unchanged.
                    ratio = q if abs(q) > 1e-12 else 1.0
                    dst = str(_get(t, 'symbol_new')).strip() or sym
                    after = pos.pop(dst, 0.0)
                    pos[sym] = after / ratio
                    cost[sym] = cost.pop(dst, 0.0)
                    continue
                if act in ('ADJUST', 'DISALLOW'):
                    if id(t) in only_walked:
                        continue
                    c = cost.get(sym, 0.0)
                    roc = (act == 'ADJUST' and net < -0.005
                           and not str(_get(t, 'id')).startswith('WASH_'))
                    if roc and traded_in_year and (
                            pos.get(sym, 0.0) <= 1e-9 or not same_cur
                            or c is None or c <= 0.005
                            or _nil_lot_held(lots.get((acct, hit), ()),
                                             str(_get(t, 'date')),
                                             country)):
                        # No long shares, or no cost left after it: it
                        # may have gone beyond the cost.
                        bad.append((t, hit, 'roc', -net))
                    cost[sym] = (None if (c is None or not same_cur
                                          or (roc and c <= 0.005))
                                 else c - net)
                    continue
                if act not in ('BUYSELL', 'ASSIGN', 'TRANSFER',
                               'OPENING_BALANCE'):
                    continue
                before = pos.get(sym, 0.0) - q
                pos[sym] = before
                if id(t) in only_walked:
                    # Its units only: this pass's rows are in the base
                    # currency, the account's merge cut it before that.
                    continue
                c = cost.get(sym, 0.0)
                cost[sym] = (c - abs(net)
                             if (act == 'BUYSELL' and q > 0
                                 and before >= -1e-9 and same_cur
                                 and c is not None)
                             else None)
                if act not in ('BUYSELL', 'ASSIGN'):
                    continue
                if q < 0:
                    if traded_in_year:
                        bad.append((t, hit, 'sale', -q))
                elif q > 0 and before < -1e-9 and in_year:
                    bad.append((t, hit, 'cover', min(q, -before)))
    bad.sort(key=lambda b: _when(b[0], where[id(b[0])]))
    return bad


def _nil_lot_held(lots, day: str, country: Optional[str]) -> bool:
    """US only (each lot keeps its own basis, US-ROC-02): a snapshot lot
    bought on or before `day` with a nil basis — a return of capital on
    `day` may have gone beyond that lot's basis even when the position's
    total cost stayed above nil. Canada pools the cost (the total
    decides)."""
    from taxjson.lib.country import is_usa
    if not is_usa(country or ''):
        return False
    return any(d and d <= day and amt <= 0.005 for d, amt in lots)


def _refusal(bad: List[Tuple], by_acct: Dict[str, Dict[str, str]],
             year: int, country: Optional[str]) -> str:
    """The OpeningError text for the left-out realizations `bad`
    (from _realizations_left_out), naming the first."""
    from taxjson.lib.country import is_usa
    t, hit, kind, closed = bad[0]
    acct = str(_get(t, 'account'))
    n = {k: sum(1 for b in bad if b[2] == k)
         for k in ('sale', 'cover', 'roc')}
    what = ' and '.join(
        x for x in (f"{n['sale']} sale(s)" if n['sale'] else '',
                    f"{n['cover']} short cover(s)" if n['cover'] else '',
                    f"{n['roc']} return(s) of capital" if n['roc'] else '')
        if x)
    qty = abs(float(_get(t, 'quantity', 0.0) or 0.0))
    if kind == 'cover':
        first = (f"{_get(t, 'date')} cover of {closed:g} "
                 f"{_get(t, 'symbol')}: a buy of {qty:g} that closes "
                 f"a short position")
    elif kind == 'roc':
        first = (f"{_get(t, 'date')} return of capital of {closed:g} "
                 f"{str(_get(t, 'currency')).upper()} on "
                 f"{_get(t, 'symbol')}, which may go beyond the cost")
    else:
        first = f"{_get(t, 'date')} {qty:g} {_get(t, 'symbol')}"
    why = ''
    if n['roc']:
        law = ('IRC §301(c)(3)' if is_usa(country or '')
               else 'ITA s.40(3)')
        why = (f" A return of capital beyond the cost is a gain of its "
               f"year ({law}); the snapshot's cost cannot show it.")
    return (f"account {acct}: the opening balance of "
            f"{hit} dated {by_acct[acct][hit]}"
            f" would leave out {what} of the {year} tax "
            f"year (first: {first}) — their gains would drop out of "
            f"the year.{why} Take the snapshot from a statement before "
            f"the year's first sale, cover or return of capital (e.g. "
            f"December 31 of the year before), or remove the OPENING "
            f"line(s).")
