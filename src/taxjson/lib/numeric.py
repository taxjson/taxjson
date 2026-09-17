"""Shared numeric helpers for taxjson.

The engines accumulate monetary values across thousands of transactions; doing
that in float drifts at the 1e-15 scale per add and can produce visible cents
of error at output time. We use Decimal for persistent accumulators inside the
engines and convert to float only at the JSON boundary.

Tax calculations also require half-up rounding at presentation boundaries;
Python's built-in ``round()`` uses banker's rounding which produces results
that disagree with what users see on broker statements and tax forms.
"""

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Union


def D(value: Union[float, int, str, Decimal, None]) -> Decimal:
    """Coerce a numeric input to Decimal without losing precision.

    Going through ``str()`` is essential — ``Decimal(0.1)`` captures the
    binary-float representation noise, while ``Decimal(str(0.1))`` gives
    the user-visible 0.1.
    """
    if value is None:
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def round_half_up(value: Union[float, Decimal], places: int = 4) -> float:
    """Round ``value`` to ``places`` decimal places using ROUND_HALF_UP."""
    if value is None:
        return value
    try:
        quantum = Decimal(10) ** -places
        return float(D(value).quantize(quantum, rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return float(value) if not isinstance(value, float) else value


# Field names whose values are NOT money — share counts (fractional shares
# from DRIPs can have 5–8 dp), running balances, and day counts. Rounding
# these to 4 dp would silently lose precision that the engine needs to
# track correctly across many small accumulations.
_QTY_FIELDS = frozenset({
    'qty', 'quantity',
    'match_qty', 'qty_total',
    'loss_qty', 'disallowed_qty',
    'pool_qty', 'pool_qty_after',
    'running_bal', 'bal_at_end',
    'days_held', 'days_from_loss', 'hold_days',
})


def round_floats(obj: Any, places: int = 4, _key: str = None) -> Any:
    """Recursively round all floats/Decimals in a JSON-shaped object to
    `places` decimals, EXCEPT for values under quantity-like keys (see
    _QTY_FIELDS). Share counts, running balances, and day counts pass
    through unchanged so fractional-share precision is preserved."""
    if isinstance(obj, (float, Decimal)):
        if _key in _QTY_FIELDS:
            return float(obj) if isinstance(obj, Decimal) else obj
        return round_half_up(obj, places)
    if isinstance(obj, dict):
        return {k: round_floats(v, places, _key=k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [round_floats(x, places, _key=_key) for x in obj]
    return obj
