"""Shared test helpers: spin-off and split rows, events and projects.

Shared from: test_corp_views
"""
import json
import tempfile
from pathlib import Path


def _proj(rows, elections=None, sheltered=False):
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "work").mkdir()
    (root / "inputs" / "m").mkdir(parents=True)
    (root / "work" / "m_base.json").write_text(json.dumps(rows))
    (root / "inputs" / "m" / "elections.json").write_text(
        json.dumps({"elections": elections or {}}))
    cfg = {"settings": {"base_currency": "CAD", "country": "canada"},
           "accounts": {"m": {"type": "sheltered" if sheltered
                              else "taxable"}}}
    return td, root, cfg


def _row(action, date, sym, qty, net=0.0, **kw):
    d = {"action": action, "date": date, "time": "09:30:00", "symbol": sym,
         "quantity": qty, "net_amount": net}
    d.update(kw)
    return d


EV = "20240513-prnt-spnc-35b9"
SPIN_DESC = "Spinoff PRNT.US\u2192SPNC.US (deemed dividend at FMV)"
SUMMARY = ("2024-05-13 spinoff: PRNT.US → SPNC.US (20.0-for-80.0, ratio "
           "0.25, FMV 830.00 USD)")
