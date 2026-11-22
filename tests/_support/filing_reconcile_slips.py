"""Shared test helpers: reconcile-slips runs and option rows.

Shared from: test_fix_1029_r7_reconcile
"""
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from taxjson.bin.taxjson_reconcile_slips import main


OPT = "SYN251219C00001000.TO"


def _run(td, slip_text, entries=None, doc=None, country="canada",
         year=2025):
    """main() on a slip CSV and one gains file: (exit code, report)."""
    slip = Path(td) / "slip.csv"
    slip.write_text(slip_text)
    g = Path(td) / "margin_gains.json"
    g.write_text(json.dumps(doc if doc is not None
                            else {"transactions": entries or []}))
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main([str(slip), "--gains", str(g), "--country", country,
                   "--year", str(year), "--json", "--tolerance", "0.01"])
    try:
        rep = json.loads(out.getvalue())
    except ValueError:
        rep = None
    return rc, rep, err.getvalue()


def _short(symbol=OPT, qty=1, proceeds=-0.0, cost=-10.0, date="2025-06-20",
           **extra):
    """An engine SHORT row: `cost` the negated short-sale (write)
    proceeds, `proceeds` the negated cover cost."""
    e = {"date": date, "date_settle": date, "symbol": symbol, "qty": qty,
         "proceeds": proceeds, "cost": cost, "gain": proceeds - cost,
         "commission": 0.0, "fee": 0.0, "direction": "SHORT",
         "is_option": True, "account": "margin", "currency": "CAD"}
    e.update(extra)
    return e


OPT25 = "SYN250620C00001000.TO"


def _write(date, premium, units=1, commission=0.0, symbol=OPT25):
    """A grant-timing WRITE record (engine shape: cost -net premium)."""
    return _short(symbol=symbol, qty=units, proceeds=-0.0,
                  cost=-(premium - commission), date=date, grant=True,
                  commission=commission)


def _buyback(date, paid, closed, units=1, symbol=OPT25):
    """A buy-back row closing grant lots: `closed` {write year:
    premium} (the engine's grant_closed)."""
    return _short(symbol=symbol, qty=units, proceeds=-paid, cost=-0.0,
                  date=date, grant=False,
                  grant_closed={y: {"premium": p, "units": units}
                                for y, p in closed.items()})
