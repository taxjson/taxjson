"""Shared test helpers: a harvest project and today's sales.

Shared from: test_fix_1029_r7e_planning
"""
import json
from datetime import date

from _qa_project import tj
from _support.planning_harvest_quotes import _project, _quotes


def _harvest_project(tmp, accounts, books):
    root = _project(tmp, accounts)
    for acct, text in books.items():
        (root / "inputs" / acct / "trades.tt").write_text(text)
    tj(root, "run", "--no-input")
    return root


def _harvestable(root, symbol="ZZSYN.TO", price=10.0):
    _quotes(root, {symbol: price})
    doc = json.loads(tj(root, "harvest", "--no-ibkr", "--json").stdout)
    return doc["totals"]["harvestable"]


def _sales_today(root, acct):
    doc = json.loads((root / "work" / f"{acct}_gains_wash.json").read_text())
    today = date.today().isoformat()
    return [t for t in doc["transactions"] if t.get("date") == today]
