"""Shared test helpers: a tips project, its holdings and dividends.

Shared from: test_tips
"""
import json
import subprocess
import sys
from pathlib import Path
import _paths


REPO_ROOT = _paths.REPO


def _run(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         "tips", *args],
        cwd=REPO_ROOT, capture_output=True, text=True)


def _suggest_json(root):
    """`tjs ticker-map --suggest --json` of the project."""
    r = subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         "ticker-map", "--suggest", "--json"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    return r, json.loads(r.stdout)


def _holdings_toml(*symbols):
    out = ['schema_version = "1.2"']
    for sym in symbols:
        out.append(f'[[holding]]\nsymbol = "{sym}"\nquantity = 100.0\n'
                   f'currency = "USD"\ntotal_cost = 1000.0\n'
                   f'cost_per_share = 10.0')
    return "\n".join(out) + "\n"


def _raw_json(*div_symbols):
    return json.dumps({"transactions": [
        {"action": "DIVIDEND", "date": "2026-03-01", "symbol": s,
         "quantity": 0, "gross_amount": 10.0, "net_amount": 10.0,
         "currency": "USD"} for s in div_symbols]})


def _project(tmp, *, accounts, holdings, raws, ticker_map=None):
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "reports").mkdir()
    acct_toml = "".join(
        f'[accounts.{n}]\ntype = "{t}"\n' for n, t in accounts)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\n'
        'base_currency = "CAD"\n' + acct_toml)
    for name, text in holdings.items():
        (root / "reports" / f"{name}_holdings.toml").write_text(text)
    for name, text in raws.items():
        (root / "work" / f"{name}_raw.json").write_text(text)
    if ticker_map:
        (root / "ticker.map").write_text(ticker_map)
    return root
