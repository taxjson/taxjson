"""Shared test helpers: the T1135 cost walk's rows and report.

Shared from: test_fix_a2_pins2_t1135
"""
import contextlib
import io
import json
import tempfile
from pathlib import Path


def _r(action, date, symbol, qty, net, settle=None, time="10:00:00",
       **kw):
    """A base-book row; net is signed as the parsers write it (a buy
    negative, a sale positive)."""
    row = {"action": action, "date": date, "date_settle": settle or date,
           "time": time, "symbol": symbol, "quantity": qty,
           "net_amount": net, "currency": "CAD", "account": "margin",
           "id": kw.pop("id", f"{action}-{date}-{symbol}-{qty}-{time}")}
    row.update(kw)
    return row


def _walk(rows, overrides=None, **kw):
    from taxjson.bin.taxjson_t1135 import walk_costs
    with contextlib.redirect_stderr(io.StringIO()):
        return walk_costs(rows, 2025, dict(overrides or {}),
                          today="2026-06-01", **kw)


def _report(base_rows, gains_doc_by_name=None, year=2025, **kw):
    from taxjson.bin.taxjson_t1135 import build_report
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "margin_base.json"
        base.write_text(json.dumps({"transactions": base_rows}))
        gains = []
        for name, doc in (gains_doc_by_name or {}).items():
            g = Path(td) / name
            g.write_text(json.dumps(doc))
            gains.append(g)
        kw.setdefault("today", "2026-06-01")
        with contextlib.redirect_stderr(io.StringIO()):
            return build_report([base], gains, year, {}, "CAD", **kw)
