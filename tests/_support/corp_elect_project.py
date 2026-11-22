"""Shared test helpers: a project with pending elections.

Shared from: test_fix_1029_r2_elect
"""
import json
from pathlib import Path


def _project(d, accounts=("margin",), country="canada", pending=None):
    """A project with taxable accounts and work/pending_elections.json."""
    root = Path(d) / "proj"
    (root / "work").mkdir(parents=True)
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True)
    base = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = 2025\ncountry = "{country}"\n'
        f'base_currency = "{base}"\nsource_currencies = []\n'
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    (root / "ticker.map").write_text("")
    if pending is not None:
        (root / "work" / "pending_elections.json").write_text(
            json.dumps(pending))
    return root
