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
* a SALE left out that falls in the project's tax year (or later) stops
  the run: its gain would silently drop out of the year. Take the
  snapshot before the year's first sale of that symbol instead.

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
        bad = []
        for acct, rows in dropped.items():
            for t, hit in rows:
                q = float(_get(t, 'quantity', 0.0) or 0.0)
                if (_get(t, 'action') in ('BUYSELL', 'ASSIGN') and q < 0):
                    last = max(str(_get(t, 'date')),
                               str(_get(t, 'date_settle')) or '')
                    if last[:4] >= str(year):
                        bad.append((t, hit))
        if bad:
            t, hit = bad[0]
            raise OpeningError(
                f"account {_get(t, 'account')}: the opening balance of "
                f"{hit} dated {by_acct[str(_get(t, 'account'))][hit]}"
                f" would leave out {len(bad)} sale(s) of the {year} tax "
                f"year (first: {_get(t, 'date')} "
                f"{abs(float(_get(t, 'quantity', 0.0))):g} "
                f"{_get(t, 'symbol')}) — their gains would drop out of "
                f"the year. Take the snapshot from a statement before "
                f"the year's first sale (e.g. December 31 of the year "
                f"before), or remove the OPENING line(s).")
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
