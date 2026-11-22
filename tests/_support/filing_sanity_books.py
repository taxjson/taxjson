"""Shared test helpers: a one-account project with books and holdings.

Shared from: test_fix_audit_1024_sanity_slips
"""
import json
import os
import subprocess
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv


def _sanity_project(root: Path, *, inventory_qty=20, base_text=None,
                    snapshots=(("2026-01-15", 10), ("2026-02-15", 20))):
    """A one-account project with built books and holdings/ snapshots:
    10 ZZQ.US bought 2026-01-01 and 10 more 2026-02-01 (20 held now)."""
    (root / "work").mkdir()
    (root / "holdings").mkdir()
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.margin]\ntype = "taxable"\naccount = "99900001"\n')
    inv = [{"symbol": "ZZQ.US", "qty": inventory_qty, "total_cost": 200,
            "currency": "CAD"}]
    (root / "work" / "margin_gains.json").write_text(json.dumps(
        {"inventory": inv, "summary": {"year": "2026"}}))
    base = [dict(date=d, date_settle=d, time="10:00:00", symbol="ZZQ.US",
                 quantity=10, action="BUYSELL", account="margin")
            for d in ("2026-01-01", "2026-02-01")]
    (root / "work" / "margin_base.json").write_text(
        base_text if base_text is not None
        else json.dumps({"transactions": base}))
    for i, (day, q) in enumerate(snapshots):
        rows = (f'[[holding]]\nsymbol = "ZZQ.US"\nquantity = {q}\n'
                f'asset_type = "equity"\n' if q else "")
        (root / "holdings" / f"margin_{i}.toml").write_text(
            f'schema_version = "1.0"\n[meta]\naccount = "margin"\n'
            f'broker_account = "99900001"\nas_of = "{day}"\n' + rows)


def _sanity(root: Path):
    r = subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), "sanity", "--json"],
        capture_output=True, text=True, env=dict(os.environ))
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        doc = None
    return r.returncode, doc, r.stderr
