"""Shared test helpers: rows, gains and a project with written books.

Shared from: test_fix_a2_dates_views
"""
import json
import tempfile
from pathlib import Path

from taxjson.lib.edge_cases import analyze


def _tx(acct, action, d, settle, sym, qty, net=0.0, price=None, **kw):
    r = {"account": acct, "action": action, "date": d, "date_settle": settle,
         "time": kw.pop("time", "10:00:00"), "symbol": sym,
         "quantity": qty, "net_amount": net,
         "price": abs(net / qty) if price is None and qty else (price or 0.0),
         "currency": kw.pop("currency", "CAD"),
         "id": f"{sym}-{d}-{qty}-{action}"}
    r.update(kw)
    return r


def _gain(acct, d, settle, sym, qty, raw, denied=0.0):
    return {"account": acct, "date": d, "date_settle": settle, "symbol": sym,
            "qty": qty, "gain": raw + denied, "raw_gain": raw,
            "disallowed_amount": denied, "permanently_disallowed": 0.0,
            "proceeds": 1000.0, "cost": 1000.0 - raw, "id": f"g-{sym}-{d}"}


class _Proj:
    """A project folder with work/ books written by the test."""

    def __init__(self, country="canada", year=2025, tax_date=None,
                 accounts=None, **settings):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "work").mkdir()
        s = {"year": year, "country": country}
        if tax_date:
            s["tax_date"] = tax_date
        s.update(settings)
        self.cfg = {"settings": s,
                    "accounts": accounts or {"margin": {"type": "taxable"}}}

    def base(self, acct, rows):
        (self.root / "work" / f"{acct}_base.json").write_text(
            json.dumps({"transactions": rows}))

    def gains(self, acct, rows, inventory=()):
        (self.root / "work" / f"{acct}_gains_wash.json").write_text(
            json.dumps({"transactions": rows, "inventory": list(inventory)}))

    def analyze(self, **kw):
        return analyze(self.root, self.cfg, **kw)

    def close(self):
        self.tmp.cleanup()
