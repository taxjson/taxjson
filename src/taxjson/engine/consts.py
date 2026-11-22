"""The engines' money constants, and how a trace shows a figure.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Union

from taxjson.model import money as M

if TYPE_CHECKING:
    from decimal import Decimal


# The engines' money constants, each read once (TJ-3089), not parsed from
# the float's repr at every use: M.of of the literal, its exponent kept
# (M.of(0.0) is Decimal('0.0'), written as before).
_ZERO = M.of(0.0)
_ONE = M.of(1.0)
_HALF = M.of(0.5)
_FIFTY = M.of(50.0)
_TWO_CENTS = M.of(0.02)
_TWO_PERCENT = M.of(0.02)
_CENT = M.of(0.01)
_HALF_CENT = M.of(0.005)
_MILLI = M.of(0.001)
_MICRO = M.of(1e-6)
_NANO = M.of(1e-9)
_MINUS_HALF_CENT = M.of(-0.005)
_MINUS_MILLI = M.of(-0.001)


def _shown_if_float(x: Union[float, Decimal], places: int) -> Union[float, Decimal]:
    """A trace or warning figure its row holds as a float or as a Decimal,
    as it prints: a float cut by `M.shown` (half up from its repr, step
    6h(4)), a Decimal as it is, which its format cuts half up in the
    engine's context (TJ-3344)."""
    return M.shown(x, places) if isinstance(x, float) else x
