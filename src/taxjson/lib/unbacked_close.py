"""A close the data cannot back: a trade the broker codes as closing
(IB code C) that the position held before it cannot have closed
(unbacked_close), with the position each broker order began from
(OrderStarts: one order's fills share its code). What it closed was
opened before the data: missing history. Out of lib/missing_history,
which the option close check (lib/option_close_check) read for these
while missing_history's callers reach that check (step 8 of the
architecture plan: lib's import cycle)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from taxjson.lib.row_types import open_close_codes


def unbacked_close(tx, prev: float, order_prev: Optional[float] = None
                   ) -> bool:
    """The broker codes this trade as (partly) CLOSING (IB code C) but
    the position the data holds cannot back the close: a sale coded C
    alone that sells more than is held (`prev`, the position before this
    row), a sale coded C;O with no long held when its ORDER began
    (`order_prev`: IB stamps the order's code on every fill of it, so
    the second fill of a "C;O" order that closed 1 and opened 1 carries
    C;O too) — and the mirror cases for a buy. What it closed was
    opened before the data: missing history, never a new short or a new
    long (audit S013-00)."""
    codes = open_close_codes(tx)
    if 'C' not in codes:
        return False
    if order_prev is None:
        order_prev = prev
    q = float(tx.quantity or 0)
    if q < 0:
        if 'O' in codes:
            return order_prev <= 1e-9
        return prev + q < -1e-9
    if q > 0:
        if 'O' in codes:
            return order_prev >= -1e-9
        return prev + q > 1e-9
    return False


class OrderStarts:
    """The position each broker ORDER began from, for unbacked_close:
    consecutive rows of one (symbol, account) with the same clock stamp,
    direction and open/close code are fills of one order."""

    def __init__(self):
        self._last: Dict[Any, Tuple[Any, float]] = {}

    def prev(self, key, tx, prev: float) -> float:
        q = float(tx.quantity or 0)
        sig = (tx.date, tx.time, q > 0, open_close_codes(tx))
        last = self._last.get(key)
        if last is not None and last[0] == sig:
            return last[1]
        self._last[key] = (sig, prev)
        return prev
