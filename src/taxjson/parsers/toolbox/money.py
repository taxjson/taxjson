"""A trade row's money identities, the one copy every parser checks a row
with (step 5, TJ-2322): `money_close`, whether a stated amount is the one
its quantity and price imply (each parser keeps its own tolerances: they
follow what the broker writes), its band `money_tolerance`, and
`implied_commission_bounds`, how large
a commission a row's money may imply before a column is taken as wrong or
shifted. The message stays each parser's: it names that broker's columns."""
from typing import Optional, Tuple


def money_tolerance(*, abs_tol: float, rel_tol: float, scale: float,
                    additive: bool = False) -> float:
    """The band an amount may be off by: `abs_tol` or `rel_tol` of `scale`,
    whichever is larger, or their sum when `additive`. `money_close`'s, and
    a check's that bounds a gap on one side (generic's commission fit)."""
    return abs_tol + rel_tol * scale if additive else max(abs_tol, rel_tol * scale)


def money_close(stated: float, expected: float, *, abs_tol: float,
                rel_tol: float, additive: bool = False,
                scale: Optional[float] = None) -> bool:
    """Whether |stated| is |expected| within tolerance: `abs_tol` or
    `rel_tol` of `scale` (the larger of the two amounts by default),
    whichever is larger, or their sum when `additive` (Kraken's slack and
    half a percent). A NaN is never close: the row is refused (the checks
    it replaced, `diff > tol`, let one through; the strict cell parsers
    refuse a NaN cell before it gets here)."""
    a, b = abs(stated), abs(expected)
    s = max(a, b) if scale is None else scale
    return abs(a - b) <= money_tolerance(abs_tol=abs_tol, rel_tol=rel_tol,
                                         scale=s, additive=additive)


def implied_commission_bounds(gross: float, qty: float, *,
                              is_option: bool) -> Tuple[float, float]:
    """The commission a trade row's net amount may imply over its gross
    (|qty| x price x contract size): from a rebate of 5 cents plus half a
    percent up to 250 (an option's: 3 a contract when that is more) plus 5%
    (RBC Direct's and Webull's rows)."""
    low = -(0.05 + 0.005 * gross)
    high = max(250.0, 3.0 * abs(qty) if is_option else 0.0) + 0.05 * gross
    return low, high
