"""The rows of one sale for Canada's superficial-loss formula (CA-SL-08):
consecutive BUYSELL rows of one account and symbol, the same direction
and date, with no row between them that moves the position, are one sale
split into fills (disposition_groups). The engine's superficial-loss pass
(engine/ca/), the loss overrides (lib/loss_overrides) and the wash radar
read it. Out of lib/core, which imports lib/loss_overrides while the
overrides read it (step 8 of the architecture plan: lib's import cycle)."""

from __future__ import annotations

from typing import Any, Dict, Iterable


def disposition_groups(rows: Iterable[Any]) -> Dict[int, int]:
    """Group the rows of one sale for Canada's superficial-loss formula
    (CA-SL-08): consecutive BUYSELL rows of one account and symbol, the
    same direction and the same date, with no row between them that moves
    that position — a sale split into fills. The clock is no help here:
    a broker stamps the partial fills of one order seconds or minutes
    apart, or all at midnight, so a day's uninterrupted sell-down in one
    account is one sale. `rows` are in processing order. Returns id(row)
    -> id(the group's first row) for every BUYSELL row; income and
    cost-adjustment rows never break a group."""
    out: Dict[int, int] = {}
    prev: Dict[tuple, tuple] = {}
    for t in rows:
        k = (t.account, t.symbol)
        if t.action == 'SPLIT':            # corporate-wide: every account
            for kk in [kk for kk in prev if kk[1] == t.symbol]:
                prev.pop(kk)
            continue
        if t.action != 'BUYSELL' or abs(t.quantity or 0) < 1e-12:
            if t.action in ('ASSIGN', 'TRANSFER', 'OPENING_BALANCE',
                            'BUYSELL'):
                prev.pop(k, None)
            continue
        p = prev.get(k)
        if (p is not None and p[0].date == t.date
                and (p[0].quantity > 0) == (t.quantity > 0)):
            out[id(t)] = p[1]
        else:
            out[id(t)] = id(t)
        prev[k] = (t, out[id(t)])
    return out
