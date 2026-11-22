"""The grant-timing option writes whose close no record carries (an
expiry adds none): `tjs ccd-sum`, `tjs winners` and `tjs losers` count
them (cli/totals). Above
lib/core, whose option-symbol parsing they read; out of lib/report_model,
which the core's own modules import (step 8 of the architecture plan:
lib's import cycle)."""

from datetime import date as _date

from taxjson.lib.option_symbols import parse_option_expiry


def grant_write_closes(transactions, inventory, year=None,
                       splits=()) -> "dict[int, float]":
    """{index in `transactions`: contracts closed} for the grant-timing
    WRITE records (``grant``) that are the only record of their close
    (grant_write_expiries, without the expiry dates)."""
    return {i: q for i, (q, _e) in grant_write_expiries(
        transactions, inventory, year, splits).items()}


def grant_write_expiries(transactions, inventory, year=None,
                         splits=()) -> "dict[int, tuple]":
    """{index in `transactions`: (contracts closed, the date they
    expired)} for the grant-timing WRITE records (``grant``) that are
    the only record of their close.

    Under grant timing (ITA s.49(1)) the premium is recognised at the
    WRITE; a buy-back adds its own closing record, but an expiry adds
    none. The views count closing records only, so an expired write
    showed 0 closes / 0 contracts while close timing showed 1 / 5
    (audit A2-0693, regression of S024-01 / S028-07).

    Per series, in date order: each WRITE queues its contracts, each
    non-grant SHORT record of the same symbol consumes from the queue
    (FIFO; one with nothing queued closes a write from an earlier year).
    `splits`, the book's SPLIT rows of the option series (date, symbol,
    symbol_new, quantity = the ratio; one event once): a split or
    rename carries the series' queued contracts into the new series,
    re-denominated (a write renamed, then bought back in the new
    series, was left queued under the old one and read as expired:
    backlog TJ-0903). What is left, less the contracts still written at
    the end of the data (``inventory``, a negative quantity), expired
    when the series' expiry date has passed by the end of ``year`` (or
    today, if earlier): it is attributed to the earliest remaining
    writes, in the series' units at the expiry, with that expiry date
    (a view's window takes the close on it, not on the write's date:
    backlog TJ-0778). A series expiring later is open at the year end,
    or closed in a later year's books, and is not counted."""
    bound = _date.today().isoformat()
    if year:
        bound = min(bound, f"{year}-12-31")
    txs = list(transactions or ())
    # (date, rank, order, kind, payload): on a split's day the writes
    # come first, then the closes of the series it renames away, the
    # split, the other closes.
    split_rows = []
    for s in splits or ():
        if not isinstance(s, dict) or not s.get("symbol"):
            continue
        try:
            r = float(s.get("quantity") or 0.0)
        except (TypeError, ValueError):
            continue
        old = str(s["symbol"])
        new = str(s.get("symbol_new") or "").strip() or old
        split_rows.append((str(s.get("date") or "")[:10], old, new,
                           r if r > 0 else 1.0))
    renamed_away = {(d, old) for d, old, _n, _r in split_rows}
    events = []
    has_grant = False
    for i, t in enumerate(txs):
        if not isinstance(t, dict) or not t.get("symbol"):
            continue
        if not (t.get("grant") or t.get("direction") == "SHORT"):
            continue
        d = str(t.get("date") or "")
        sym = str(t["symbol"])
        if t.get("grant"):
            has_grant = True
            rank = 0
        else:
            rank = 1 if (d[:10], sym) in renamed_away else 3
        events.append((d, rank, i, "row", i))
    if not has_grant:
        return {}
    for k, s in enumerate(split_rows):
        events.append((s[0], 2, k, "split", s))
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    queues: "dict[str, list]" = {}           # symbol: [[index, remaining]]
    eps = 1e-9
    for _d, _rank, _o, kind, payload in events:
        if kind == "split":
            _sd, old, new, ratio = payload
            moved = queues.pop(old, [])
            if not moved:
                continue
            for ent in moved:
                ent[1] *= ratio
            dest = queues.setdefault(new, [])
            dest.extend(moved)
            dest.sort(key=lambda ent: (str(txs[ent[0]].get("date") or ""),
                                       ent[0]))
            continue
        i = payload
        t = txs[i]
        try:
            q = abs(float(t.get("qty") or 0.0))
        except (TypeError, ValueError):
            continue
        sym = str(t["symbol"])
        if t.get("grant"):
            queues.setdefault(sym, []).append([i, q])
            continue
        queue = queues.get(sym) or []
        while q > eps and queue:
            take = min(q, queue[0][1])
            queue[0][1] -= take
            q -= take
            if queue[0][1] <= eps:
                queue.pop(0)
    open_short: "dict[str, float]" = {}
    for r in inventory or ():
        try:
            q = float(r.get("qty") or 0.0)
        except (AttributeError, TypeError, ValueError):
            continue
        if q < 0:
            sym = str(r.get("symbol"))
            open_short[sym] = open_short.get(sym, 0.0) - q
    out: "dict[int, tuple]" = {}
    for sym, queue in queues.items():
        exp = parse_option_expiry(sym)
        if exp is None or exp > bound:
            continue
        # Contracts still written at the end are the LATEST writes.
        still = open_short.get(sym, 0.0)
        for ent in reversed(queue):
            take = min(still, ent[1])
            ent[1] -= take
            still -= take
        for i, left in queue:
            if left > eps:
                q0, _e = out.get(i, (0.0, exp))
                out[i] = (q0 + left, exp)
    return out
