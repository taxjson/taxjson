"""Shared test helpers: projects, IB files and the checklist for export coverage.

Shared from: test_fix_1029_r7c_coverage
"""
import json
from datetime import date
from pathlib import Path

from _support.ib_statement_rows import TRADES_H, _trade

from taxjson.lib import checklist as cl
import tomllib
from taxjson.cli.checklist_run import default_run_sub


def _project(root: Path, year: int, accounts, extra: str = "") -> Path:
    head = ('[settings]\nlocal_timezone = "America/Toronto"\n'
            f'year = {year}\ncountry = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\n')
    body = "".join(f'[accounts.{json.dumps(a)}]\ntype = "{t}"\n'
                   for a, t in accounts.items())
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(head + body + extra)
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    (root / "ticker.map").write_text("")
    return root


def _cfg(root: Path):
    return tomllib.loads((root / "taxjson.toml").read_text())


def _ib(acct_id: str, end: str, symbol: str, qty: int = 10) -> str:
    return ('Statement,Header,Field Name,Field Value\n'
            f'Statement,Data,Period,"January 1, 2025 - {end}"\n'
            'Account Information,Header,Field Name,Field Value\n'
            f'Account Information,Data,Account,{acct_id}\n'
            + TRADES_H
            + _trade(symbol, '2025-03-03, 10:00:00', qty, 10, -10 * qty,
                     acct=acct_id, cur='CAD'))


def _checklist(root: Path):
    return cl.d_export_coverage(cl.Ctx(
        root=root, cfg=_cfg(root), year=2025, today=date(2026, 3, 1),
        run_sub=default_run_sub(root)))


_GENERIC_MAP = '''[columns]
date="Date"
action="Action"
symbol="Symbol"
quantity="Quantity"
price="Price"
amount="Amount"
[defaults]
currency="CAD"
[actions]
BUY="buy"
SELL="sell"
[broker]
name="{broker}"
account="{account}"
'''
