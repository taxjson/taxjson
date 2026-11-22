"""Shared test helpers: the wash radar's JSON over settled rows.

Shared from: test_wash_radar
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import _paths


REPO_ROOT = _paths.REPO


def _stx(action, date, settle, symbol, qty, price, **kw):
    d = dict(action=action, date=date, time="09:30:00", date_settle=settle,
             symbol=symbol, quantity=qty, currency="CAD", price=price,
             net_amount=abs(qty) * price, fee=0.0, account="margin")
    d.update(kw)
    return d


def _run_json(taxable, date):
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp) / "t.json"
        t.write_text(json.dumps({"transactions": taxable}))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar", "--country", "canada",
             "--taxable", str(t), "--date", date, "--json"],
            cwd=REPO_ROOT, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    return {r["ticker"]: r for s in doc["sections"] for r in s["rows"]}
