"""Option rows the broker marks CLOSING that the books cannot back.

An option trade carries the broker's own open/close marker as the
neutral `open_close` evidence code ('O' / 'C'): IB's Trades Code, RBC's
"... OPEN CONTRACT" / "... CLOSE CONTRACT". A row marked closing that
the data holds nothing to close is missing history for certain — but for
an option it has a commoner cause: the position IS in the books under
another ROOT. RBC re-describes a contract between yearly exports (".RCI"
in 2024, ".RCI.B" in 2025; an adjusted ".TRX1" after an XCH), and a .tt
hand-off or an earlier export keeps the old spelling. The close then
opens a NEW written (or long) option, its premium is taxed in full and
the real position stays open — with no warning (audit A2-0006, A2-0095,
A2-0266, A2-0267).

`unbacked_option_closes` walks the books once and returns each such row
with the held contract it most likely closes (same account, expiry,
right, strike and listing; a root that names the same company,
core._root_matches_stock), so the run can print ONE line with the exact
ticker.map fix. Country-neutral: it reads positions, not tax rules.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from taxjson.lib.core import _OCC_OPTION_RE, _root_matches_stock
from taxjson.lib.corporate_timeline import (event_sort_key,
                                            normalize_symbol_new)
from taxjson.lib.missing_history import (OrderStarts, open_close_codes,
                                          unbacked_close)

_QTY_EPS = 1e-9
_POSITION_ACTIONS = ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE', 'TRANSFER')


def _contract(symbol: str) -> Optional[Tuple[str, str, str]]:
    """(root, OCC block, suffix) of an option symbol, else None."""
    m = _OCC_OPTION_RE.match(symbol or '')
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3) or ''


def _same_company(a: str, b: str) -> bool:
    return a != b and (_root_matches_stock(a, b) or _root_matches_stock(b, a))


def unbacked_option_closes(transactions: Iterable[Any]) -> List[Dict[str, Any]]:
    """Every option row coded CLOSING (open_close 'C') whose position
    the books cannot back — a sale with no long to close, a purchase
    with no written position — in event order. Each item: symbol,
    account, date, side ('sale'/'purchase'), quantity, and `partners`:
    the same contract held (opposite side) under a related root."""
    txs = [t for t in transactions
           if getattr(t, 'action', '') in _POSITION_ACTIONS + ('SPLIT',)
           and _contract(getattr(t, 'symbol', '') or '')]
    txs.sort(key=lambda t: event_sort_key(t, profile='missing_history_walk'))
    pos: Dict[Tuple[str, str], float] = {}
    orders = OrderStarts()
    out: List[Dict[str, Any]] = []
    # Rows coded OPEN later the same day (RBC prints no clock time, so a
    # day's write and its buy-back can sort either way): the close is
    # backed when the day's own openings back it.
    later_open: Dict[Tuple[str, str, str], List[Any]] = {}
    for t in txs:
        if 'O' in open_close_codes(t):
            later_open.setdefault((t.account or '', t.symbol, t.date),
                                  []).append(t)
    for t in txs:
        acct = t.account or ''
        key = (acct, t.symbol)
        q = float(t.quantity or 0.0)
        if t.action == 'SPLIT':
            ratio = q if q > 0 else 1.0
            new = normalize_symbol_new(t.symbol, t.symbol_new) or t.symbol
            p = pos.pop(key, 0.0) * ratio
            nk = (acct, new)
            pos[nk] = pos.get(nk, 0.0) + p
            continue
        prev = pos.get(key, 0.0)
        order_prev = orders.prev(key, t, prev)
        day = later_open.get((acct, t.symbol, t.date), [])
        if t in day:
            day.remove(t)
        pending = sum(float(o.quantity or 0.0) for o in day
                      if (float(o.quantity or 0.0) > 0) != (q > 0))
        if 'C' in open_close_codes(t) and unbacked_close(t, prev, order_prev) \
                and unbacked_close(t, prev + pending, order_prev + pending):
            root, block, sfx = _contract(t.symbol)
            want_long = q < 0          # a sale closes a long
            partners = []
            for (a2, s2), p2 in sorted(pos.items()):
                if a2 != acct or s2 == t.symbol:
                    continue
                c2 = _contract(s2)
                if not c2 or c2[1] != block or c2[2] != sfx \
                        or not _same_company(root, c2[0]):
                    continue
                if (p2 > _QTY_EPS) if want_long else (p2 < -_QTY_EPS):
                    partners.append((s2, p2))
            out.append({'symbol': t.symbol, 'account': acct,
                        'date': t.date, 'quantity': q,
                        'side': 'sale' if q < 0 else 'purchase',
                        'partners': partners})
        pos[key] = prev + q
    return out


def unbacked_option_close_messages(transactions: Iterable[Any]) -> List[str]:
    """The run's console lines (ATTENTION) for unbacked_option_closes:
    one per (symbol, account), naming the ticker.map line when the
    same contract is held under a related root."""
    seen = set()
    msgs: List[str] = []
    for f in unbacked_option_closes(transactions):
        k = (f['symbol'], f['account'])
        if k in seen:
            continue
        seen.add(k)
        sale = f['side'] == 'sale'
        opened = 'written' if sale else 'long'
        missing = 'purchase' if sale else 'write'
        head = (f"warning: ATTENTION: {f['symbol']} ({f['account']}): the "
                f"broker marks the {f['side']} on {f['date']} CLOSING "
                f"(open/close code C), but the books hold no "
                f"{'long' if sale else 'written'} position in "
                f"{f['symbol']} to close — it is booked as a NEW "
                f"{opened} option")
        if len(f['partners']) == 1:
            p, pq = f['partners'][0]
            msgs.append(
                f"{head}, and the books still hold {pq:g} {p} (the same "
                f"expiry, right and strike under another root: the "
                f"broker re-described the contract, e.g. RBC's RCI → "
                f"RCI.B or an adjusted TRX → TRX1). If it is the same "
                f"contract, add to ticker.map:  GLOBAL {p} {f['symbol']}"
                f"  (or write the .tt with {f['symbol']}).")
        elif f['partners']:
            names = ', '.join(p for p, _ in f['partners'])
            msgs.append(
                f"{head}; the books hold the same expiry, right and strike "
                f"under other roots ({names}) — map the one it closes "
                f"with a ticker.map GLOBAL line.")
        else:
            msgs.append(
                f"{head} until the missing {missing} is supplied (`taxjson "
                f"find-missing-history`), or the opening position is in "
                f"a .tt / earlier export under another symbol (map it "
                f"with a ticker.map GLOBAL line).")
    return msgs
