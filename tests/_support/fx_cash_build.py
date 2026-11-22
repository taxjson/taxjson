"""Shared test helpers: a .tt cash line, the FX-on-cash build over cash lines and a series' value at a day.

Shared from: test_fix_1029_r7d_cashcrypto
"""
from taxjson.lib import cash_lines as CL
from taxjson.lib import fx_cash_v2 as V2


A = "a/manual"


def tt(line, book=A, n=1):
    """A .tt cash line as lib/cash_lines reads it, in `book`."""
    label = book.split("/", 1)[0]
    ev = CL.parse_line(line, f"inputs/{label}/book.tt:{n}")
    ev.update(label=label, file="book.tt", book=book, book_problem="")
    return ev


def build(lines, native=(), rate=1.0, year=2025, **kw):
    kw.setdefault("country", "canada")
    rate_of = rate if callable(rate) else (lambda c, d: rate)
    cash = {"events": [], "lines": list(lines), "problems": [],
            "stablecoins_as_cash": True}
    return V2.build(list(native), cash, "CAD", year, rate_of, **kw)


# transit inside the year
def _at(points, d):
    """A series' value (units, cost) at the end of day `d`."""
    v = (0.0, 0.0)
    for p in points:
        if p[0] <= d:
            v = (p[1], p[2])
    return v
