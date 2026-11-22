"""Shared test helpers: Questrade rows of shares bought on one listing and journaled to the other, as projects.

Shared from: test_fix_cross_listings
"""
from pathlib import Path

from taxjson.lib.country import COUNTRIES
from tax_rules.dual import projects_both


_QT = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Account #,Activity Type,Account Type\n")


def _qt(date, action, sym, desc, qty, price, net, cur, act="Trades"):
    return (f"{date} 09:30:00 AM,{date} 12:00:00 AM,{action},{sym},{desc},"
            f"{qty},{price:.2f},{abs(qty) * price:.2f},0.00,{net:.2f},"
            f"{cur},55500001,{act},Individual\n")


def _journal(cur, *, in_desc="SAMPQ ENERGY INC TRANSFER"):
    """A taxable account: 100 bought as SAMPQ, journaled to the SAMPR
    line (the same company's other listing), sold as SAMPR."""
    return (_QT
            + _qt("2025-01-10", "Buy", "SAMPQ", "SAMPQ ENERGY INC", 100,
                  30.0, -3000.0, cur)
            + _qt("2025-03-03", "TF6", "SAMPQ", "SAMPQ ENERGY INC TRANSFER",
                  -100, 0.0, 0.0, cur, act="Transfers")
            + _qt("2025-03-04", "TF6", "SAMPR", in_desc, 100, 0.0, 0.0, cur,
                  act="Transfers")
            + _qt("2025-06-10", "Sell", "SAMPR", "SAMPQ ENERGY INC", -100,
                  45.0, 4500.0, cur))


def _projects(tmp, tail="", **kw):
    out = {}
    for c in COUNTRIES:
        cur = "CAD" if c == "canada" else "USD"
        files = {"inputs/margin/questrade.csv": _journal(cur, **kw)}
        if tail:
            files["ticker.map"] = tail
        out[c] = projects_both(
            Path(tmp) / c, files=files,
            canada={"source_currencies": []},
            usa={"source_currencies": []})[c]
    return out
