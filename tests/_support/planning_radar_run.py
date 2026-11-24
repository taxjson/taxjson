"""Shared test helpers: the wash radar run over taxable rows.

Shared from: test_audit_round2_fixes
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import _paths
from _support.planning_radar_row import base_rows, radar_country


REPO_ROOT = _paths.REPO


def _radar(taxable_txs, date, *extra):
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "t.json"
        f.write_text(json.dumps({"transactions": base_rows(
            taxable_txs, radar_country(extra))}))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar", "--country", "canada",
             "--taxable", str(f), "--date", date, *extra],
            cwd=REPO_ROOT, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout


def _row(action, date, sym, qty, net, account="margin", time="09:30:00", **kw):
    d = {"action": action, "date": date, "time": time, "symbol": sym,
         "quantity": qty, "price": abs(net / qty) if qty else 0,
         "net_amount": abs(net), "currency": "CAD", "account": account}
    d.update(kw)
    return d
