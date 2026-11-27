"""Option rows the broker marks CLOSING that the books cannot back.

An option trade carries the broker's own open/close marker as the
neutral `open_close` evidence code ('O' / 'C'): IB's Trades Code, RBC's
"... OPEN CONTRACT" / "... CLOSE CONTRACT". A row marked closing that
the data holds nothing to close is missing history for certain — but for
an option it has a commoner cause: the position IS in the books under
another ROOT. RBC re-describes a contract between yearly exports (".ABC"
one year, ".ABC.B" the next; an adjusted ".ABC1" after an XCH), and a .tt
hand-off or an earlier export keeps the old spelling. The close then
opens a NEW written (or long) option, its premium is taxed in full and
the real position stays open — with no warning (audit A2-0006, A2-0095,
A2-0266, A2-0267).

`unbacked_option_closes` walks the books once and returns each such row
with the held contract it most likely closes (same account, expiry,
right, strike and listing; a root that names the same company,
option_symbols.root_matches_stock), so the run can print ONE line with the exact
ticker.map fix. What a CLOSING row closes is lib/unbacked_close's one
rule (backlog TJ-5349); this module adds the related root and the words.
In a Canadian book the row's unbacked units open nothing and are
reported by hand (CA-OPT-12), so the walk leaves them out of the
position and the line says so; the US engine books by position (US-OPT:
its line keeps the old words until the US side follows).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from taxjson.lib.option_symbols import _OCC_OPTION_RE, root_matches_stock
from taxjson.lib.corporate_timeline import (dedupe_split_events, event_sort_key,
                                            normalize_symbol_new)
from taxjson.lib.numeric import D
from taxjson.lib.unbacked_close import ATTENTION_UNBACKED, CloseWalk
from taxjson.model import money as M

_QTY_EPS = 1e-9
_POSITION_ACTIONS = ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE', 'TRANSFER')


def _contract(symbol: str) -> Optional[Tuple[str, str, str]]:
    """(root, OCC block, suffix) of an option symbol, else None."""
    m = _OCC_OPTION_RE.match(symbol or '')
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3) or ''


def _same_company(a: str, b: str) -> bool:
    return a != b and (root_matches_stock(a, b) or root_matches_stock(b, a))


def _by_hand(country: Optional[str]) -> bool:
    """Whether the books report a CLOSING row's unbacked units by hand and
    open nothing for them (Canada, CA-OPT-12), or book the row by
    position (the US engine)."""
    from taxjson.lib.country import is_canada
    return country is not None and is_canada(country)


def unbacked_option_closes(transactions: Iterable[Any],
                           country: Optional[str] = None
                           ) -> List[Dict[str, Any]]:
    """Every option row coded CLOSING (open_close 'C') with units the
    position cannot back (lib/unbacked_close: a sale with no long to
    close, a purchase with no written position), in event order. Each
    item: symbol, account, date, side ('sale'/'purchase'), quantity (the
    row's, signed), `unbacked` (its units past the position), `held` (the
    units the position backed), `money` (the unbacked units' share of the
    row's net amount, a magnitude) and `currency`, `trade` (a Buy / Sell
    row, not an assignment, exercise or expiry), and `partners`: the same
    contract held (opposite side) under a related root. `country`: a
    Canadian book's unbacked units open no position (the walk leaves them
    out); any other book's rows are booked whole."""
    txs = [t for t in transactions
           if getattr(t, 'action', '') in _POSITION_ACTIONS + ('SPLIT',)
           and _contract(getattr(t, 'symbol', '') or '')]
    by_hand = _by_hand(country)
    # A Canadian book in the order its engine books the rows (settlement
    # first, tied trades as the export lists them), so this reading is
    # the books' own; any other in the history walks' order.
    txs.sort(key=lambda t: event_sort_key(
        t, profile='ca_main' if by_hand else 'missing_history_walk'))
    # One split reported twice is ONE event per account (CA-CORP-12).
    txs = dedupe_split_events(txs, per_account=True)
    pos: Dict[Tuple[str, str], float] = {}
    closes = CloseWalk()
    out: List[Dict[str, Any]] = []
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
        b = closes.read(key, t, prev)
        trade = t.action == 'BUYSELL'
        if b.unbacked > _QTY_EPS:
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
                        'unbacked': b.unbacked, 'held': b.backed,
                        'money': abs(M.shown(M.of(t.net_amount or 0) * D(b.unbacked)
                                             / D(abs(q)), 2)),
                        'currency': str(getattr(t, 'currency', '') or ''),
                        'expired': (abs(t.net_amount or 0.0) < 0.005
                                    and abs(t.price or 0.0) < 0.005),
                        'trade': trade, 'partners': partners})
        # (a Canadian book: a Buy / Sell row's unbacked units open nothing)
        gone = b.unbacked if by_hand and trade else 0.0
        pos[key] = prev + q + (gone if q < 0 else -gone)
    return out


def _by_hand_head(f: Dict[str, Any]) -> str:
    """The Canadian line's first sentences. The headline (what the run's
    one-line console shows) names the series, the account, the day and
    the units reported by hand; then what the broker says, what the
    account holds, and what the books do with the units past it."""
    sale = f['side'] == 'sale'
    kind = 'bought' if sale else 'written'
    n, held, q = f['unbacked'], f['held'], abs(f['quantity'])
    holds = (f"holds {held:g} {kind} contract{'' if held == 1 else 's'}"
             if held > _QTY_EPS else f"holds no {kind} contract")
    closed = (f"{n:g} of them closed contracts {kind}" if held > _QTY_EPS
              else f"it closed contracts {kind}")
    said = (f"The broker marks the {f['side']} of {q:g} CLOSING and the "
            f"account {holds}: {closed} before your files")
    if not f['trade']:
        return (f"warning: ATTENTION: {f['symbol']} ({f['account']}) "
                f"{f['date']}, {n:g} closed with nothing held: nothing is "
                f"booked for them. {said}.")
    units = f"{n:g}" + (f" of {q:g}" if held > _QTY_EPS else "")
    money = f"{f['money']:.2f}" + (f" {f['currency']}" if f['currency'] else '')
    did = 'expired' if f.get('expired') else ('sold' if sale else 'bought') + ' to close'
    return (f"{ATTENTION_UNBACKED}{f['symbol']} ({f['account']}) {f['date']}, "
            f"{units} {did}: reported by "
            f"hand. {said} (`tjs find-missing-history --include-options`). "
            f"Not in `tjs sum`: "
            + (f"proceeds {money}, cost unknown; no written contract is added."
               if sale else
               f"cost {money}, premium unknown; no long is added."))


def unbacked_option_close_messages(transactions: Iterable[Any],
                                   country: Optional[str] = None
                                   ) -> List[str]:
    """The run's console lines (ATTENTION) for unbacked_option_closes,
    naming the ticker.map line when the same contract is held under a
    related root. A Canadian book (`country`): one per row, saying what is
    reported by hand (CA-OPT-12). Any other: one per (symbol, account),
    the row booked by position."""
    by_hand = _by_hand(country)
    seen = set()
    msgs: List[str] = []
    for f in unbacked_option_closes(transactions, country):
        k = (f['symbol'], f['account'])
        if k in seen and not by_hand:
            continue
        seen.add(k)
        sale = f['side'] == 'sale'
        opened = 'written' if sale else 'long'
        missing = 'purchase' if sale else 'write'
        if by_hand:
            head = _by_hand_head(f)
            if len(f['partners']) == 1:
                p, pq = f['partners'][0]
                msgs.append(
                    f"{head} The books still hold {pq:g} {p} (the same "
                    f"expiry, right and strike under another root: the "
                    f"broker re-described the contract, e.g. RBC's ABC → "
                    f"ABC.B or an adjusted ABC → ABC1). If it is the same "
                    f"contract, add to ticker.map:  GLOBAL {p} {f['symbol']}"
                    f"  (or write the .tt with {f['symbol']}).")
            elif f['partners']:
                names = ', '.join(p for p, _ in f['partners'])
                msgs.append(
                    f"{head} The books hold the same expiry, right and "
                    f"strike under other roots ({names}): map the one it "
                    f"closes with a ticker.map GLOBAL line.")
            else:
                # (no OPENING line where the account's own contracts back a
                # part: dated before them, it would put their documented
                # close at an unknown premium too, TJ-5379)
                line = ("" if f['held'] > _QTY_EPS else
                        ", or its `OPENING ... cost=unknown` line")
                msgs.append(
                    f"{head} Add the {missing} (the earlier export or a .tt "
                    f"line{line}), or map the symbol it was opened under (a "
                    f"ticker.map GLOBAL line).")
            continue
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
                f"broker re-described the contract, e.g. RBC's ABC → "
                f"ABC.B or an adjusted ABC → ABC1). If it is the same "
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
                f"{head} until the missing {missing} is supplied (`tjs "
                f"find-missing-history`), or the opening position is in "
                f"a .tt / earlier export under another symbol (map it "
                f"with a ticker.map GLOBAL line).")
    return msgs
