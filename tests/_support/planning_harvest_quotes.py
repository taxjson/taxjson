"""Shared test helpers: a project dated this year and its quotes.

Shared from: test_fix_1029_r7d_harvest
"""
import json
from datetime import date
from pathlib import Path


def _project(tmp, accounts, country="canada", ticker_map=""):
    """A project of `accounts` ({name: type}) dated this year."""
    root = Path(tmp) / "p"
    base = "CAD" if country == "canada" else "USD"
    lines = [f'[settings]\nyear = {date.today().year}\n'
             f'country = "{country}"\nbase_currency = "{base}"\n'
             f'source_currencies = []\n']
    for name, kind in accounts.items():
        lines.append(f'[accounts.{name}]\ntype = "{kind}"\n')
        (root / "inputs" / name).mkdir(parents=True)
    (root / "taxjson.toml").write_text("".join(lines))
    (root / "ticker.map").write_text(ticker_map)
    return root


def _quotes(root, prices):
    today = date.today().isoformat()
    (root / "work" / ".price_cache.json").write_text(json.dumps(
        {s: {"price": p, "currency": "CAD", "quote_symbol": s, "asof": today}
         for s, p in prices.items()}))
