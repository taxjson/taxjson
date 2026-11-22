"""Shared test helpers: the bin tools over gains and slip files.

Shared from: test_fix_l_filing_b
"""
import contextlib
import io
import json
from pathlib import Path


def _run(main, argv):
    """(rc, stdout, stderr) of a bin tool's main(argv)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(argv)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else (
                0 if e.code is None else 1)
            if not isinstance(e.code, int) and e.code is not None:
                err.write(str(e.code))
    return rc, out.getvalue(), err.getvalue()


def _sell(symbol="AAPL.US", date="2025-05-02", qty=-100, proceeds=11990.0,
          cost=10000.0, commission=10.0, fee=0.0, direction="LONG", **extra):
    e = {"date": date, "date_settle": date, "symbol": symbol, "qty": qty,
         "proceeds": proceeds, "cost": cost, "gain": proceeds - cost,
         "disallowed_amount": 0.0, "days_held": 100,
         "direction": direction, "commission": commission, "fee": fee,
         "account": "margin"}
    e.update(extra)
    return e


def _gains(td, entries, name="margin_gains.json"):
    p = Path(td) / name
    p.write_text(json.dumps({"transactions": entries}))
    return p


def _slip(td, text, name="t5008.csv", mode="w"):
    p = Path(td) / name
    if isinstance(text, bytes):
        p.write_bytes(text)
    else:
        p.write_text(text)
    return p


def _g(symbol="AAA.TO", qty=-100.0, proceeds=1000.0, cost=800.0,
       gain=None, direction="LONG", date="2025-05-02", settle=None, **kw):
    e = {"symbol": symbol, "qty": qty, "proceeds": proceeds, "cost": cost,
         "gain": (proceeds - cost) if gain is None else gain,
         "direction": direction, "date": date,
         "date_settle": settle or date, "commission": 0.0, "fee": 0.0,
         "disallowed_amount": 0.0, "permanently_disallowed": 0.0,
         "days_held": 30, "currency": "CAD", "account": "margin"}
    e.update(kw)
    return e
