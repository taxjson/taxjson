"""A share-exchange merger's shares received inside a loss's window: a
limit the run says, not a rule it applies (docs/limits.md; backlog
TJ-4992, TJ-4410).

A merger booked as a cost carry-over (Canada's s.85.1 rollover, a
sheltered account's merger) is a rename of the pool in the books (a SPLIT
row with `symbol_new`): cost and dates carry, and nothing is acquired.
That is right for a ticker change. In a share exchange the holder does
acquire the acquirer's shares on the exchange date, and the
superficial-loss rule (`tjs tax-logic` CA-SL-01, CA-SL-09, CA-SL-19)
counts purchases only: a loss on the acquirer's shares within 30 days of
the exchange stays allowed in full. The books can tell the two apart by
what the row says: the corporate-action election that emitted it
(`corp_election`), never by guesswork. A SPLIT row with no election, or
a name change's (`rename`), is a ticker change and says nothing here.

The run names each such exchange on its ATTENTION channel
(`say_share_exchanges`, called by lib/pipeline.run_gains); nothing is
denied for it. Canada's alone: the US engine's reorganizations are not
read here.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from taxjson.lib.country import is_canada
from taxjson.lib.json_input import is_disposition
from taxjson.lib.missing_history import loss_window_date
from taxjson.lib.out import END_OF_RUN_MARK
from taxjson.lib.stage_msg import say
from taxjson.model import money as M

# A loss smaller than this is none (a half cent, as the run's other
# loss-window lists read one).
_NO_LOSS = M.of("-0.005")

# The `corp_election` of a SPLIT row that is a share exchange booked as a
# cost carry-over (lib/corp_actions: CANADA_MERGER's rollover option, and
# SHELTERED_DEFAULT on a registered account's merger). `rename` (a name
# change) and a row with no election are ticker changes.
SHARE_EXCHANGE_ELECTIONS = frozenset({"rollover_s_85_1_5", "sheltered_default"})

# The captured head of each line (the run echoes a gains stage's
# ATTENTION lines and lists the marked ones again at its end;
# lib/out.ATTENTION_TOPICS shows the topic).
ATTENTION_SHARE_EXCHANGE = f"warning: ATTENTION: {END_OF_RUN_MARK}share exchange: "


def is_share_exchange(row: Any) -> bool:
    """Whether a book row is a share exchange booked as a rename: a SPLIT
    into another symbol whose election says it is one."""
    return (getattr(row, "action", "") == "SPLIT"
            and bool((getattr(row, "symbol_new", "") or "").strip())
            and (getattr(row, "corp_election", "") or "") in SHARE_EXCHANGE_ELECTIONS)


def _day(v: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(v or "")[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _held_at_day_30(rows: List[Any], symbol: str, loss_day: datetime, country: str,
                    window_days: int) -> bool:
    """Whether the books hold `symbol` at the end of the `window_days`-th day
    after the loss (every account of `rows`, a registered one too: each
    account's own units, its renames and splits followed). The rule needs
    the property held then; with none, the loss is not superficial whatever
    the exchange brought, and nothing is said."""
    units: Dict[Tuple[str, str], float] = {}

    def when(t: Any) -> str:
        return str(loss_window_date({"date": t.date, "date_settle": t.date_settle},
                                    country) or "")[:10]
    last = (loss_day + timedelta(days=window_days)).strftime("%Y-%m-%d")
    for t in sorted(rows, key=lambda t: (when(t), str(getattr(t, "time", "") or ""))):
        if when(t) > last:
            break
        q = float(getattr(t, "quantity", 0) or 0)
        k = (t.account, t.symbol)
        if t.action == "SPLIT":
            new = (getattr(t, "symbol_new", "") or "").strip() or t.symbol
            moved = units.pop(k, 0.0) * q
            units[(t.account, new)] = units.get((t.account, new), 0.0) + moved
        elif t.action in ("BUYSELL", "ASSIGN", "OPENING_BALANCE", "TRANSFER"):
            units[k] = units.get(k, 0.0) + q
    return any(u > 1e-9 for (_a, sym), u in units.items() if sym == symbol)


def share_exchanges_in_loss_windows(losses: Iterable[Dict[str, Any]],
                                    rows: Iterable[Any], country: str,
                                    window_days: int = 30) -> List[Dict[str, Any]]:
    """Each share exchange of `rows` (`is_share_exchange`) dated within
    `window_days` days of a loss in `losses` (gain rows; a loss still
    allowed after any denial) on the shares it brought (the row's
    `symbol_new`), measured on the country's window dates, where the books
    still hold those shares at the end of the 30th day after that loss
    (`_held_at_day_30`). One record per exchange row, naming the nearest
    such loss: {account, old, symbol, date, event, election, loss_date,
    loss_account}."""
    rows = list(rows or ())
    by_sym: Dict[str, List[Tuple[datetime, Dict[str, Any]]]] = {}
    for e in losses or ():
        if not is_disposition(e) or e.get("deemed"):
            continue
        try:
            left = M.of(e.get("taxable_gain", e.get("gain")) or 0)
        except (TypeError, ValueError, ArithmeticError):
            continue
        if left >= _NO_LOSS:
            continue
        d = _day(loss_window_date(e, country))
        if d is not None and e.get("symbol"):
            by_sym.setdefault(str(e["symbol"]), []).append((d, e))
    out: List[Dict[str, Any]] = []
    seen = set()
    for t in rows or ():
        if not is_share_exchange(t):
            continue
        new = (t.symbol_new or "").strip()
        cands = by_sym.get(new)
        td = _day(loss_window_date({"date": t.date, "date_settle": t.date_settle}, country))
        if not cands or td is None:
            continue
        near = [(abs((td - d).days), d, e) for d, e in cands
                if abs((td - d).days) <= window_days
                and _held_at_day_30(rows, new, d, country, window_days)]
        if not near:
            continue
        _gap, _d, e = min(near, key=lambda x: (x[0], x[1]))
        key = (t.account, t.symbol, new, t.date)
        if key in seen:
            continue
        seen.add(key)
        out.append({"account": t.account, "old": t.symbol, "symbol": new,
                    "date": t.date, "event": t.corp_event_id or "",
                    "election": t.corp_election,
                    "loss_date": e.get("date"),
                    "loss_account": e.get("account")})
    out.sort(key=lambda r: (r["date"], r["account"], r["symbol"]))
    return out


def share_exchange_message(x: Dict[str, Any]) -> Tuple[str, List[str], str]:
    """(headline, details, captured line) of the ATTENTION for one record
    of share_exchanges_in_loss_windows."""
    where = f" in {x['loss_account']}" if x.get("loss_account") else ""
    head = (f"share exchange: {x['symbol']}: the {x['loss_date']} loss is not "
            f"tested against the {x['date']} exchange of {x['old']} "
            f"({x['account']})")
    elect = (f"`tjs elections {x['account']} --set {x['event']}=ignore`"
             if x.get("event") else f"`tjs elections {x['account']}`")
    why = (f"The {x['old']} shares became {x['symbol']} shares inside the "
           f"loss's 30-day window, by a share exchange booked as a cost "
           f"carry-over, which is not counted as an acquisition for the "
           f"superficial-loss rule (docs/limits.md): the loss{where} stays "
           f"allowed in full.")
    fix = (f"Review the loss for the rule (s.54). To have the exchange "
           f"counted, elect the event `ignore` ({elect}) and book it by "
           f"hand in a .tt file: a sale of the {x['old']} shares at their "
           f"cost and a purchase of the {x['symbol']} shares at that cost.")
    # (marked for the run's end-of-run list: lib/first_run.parser_warnings)
    return head, [why, fix], f"warning: ATTENTION: {END_OF_RUN_MARK}{head}. {why} {fix}"


def say_share_exchanges(losses: Iterable[Dict[str, Any]], rows: Iterable[Any],
                        country: str) -> List[Dict[str, Any]]:
    """Say each record of share_exchanges_in_loss_windows as an ATTENTION
    of a Canadian gains run (nothing for another country); returns them."""
    if not is_canada(country):
        return []
    found = share_exchanges_in_loss_windows(losses, rows, country)
    for x in found:
        head, details, captured = share_exchange_message(x)
        say("attention", head, details, legacy=captured)
    return found
