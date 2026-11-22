"""Shared test helpers: holdings files and gains for tjs sanity.

Shared from: test_sanity
"""
import json


def _gains(root, acct, inv):
    (root / "work").mkdir(exist_ok=True)
    (root / "work" / f"{acct}_gains.json").write_text(json.dumps(
        {"summary": {"year": "2026"}, "transactions": [],
         "inventory": [{"symbol": s, "qty": q, "total_cost": 1.0}
                       for s, q in inv.items()]}))


def _holdings_toml(path, account, holdings):
    rows = "".join(
        f'[[holding]]\nsymbol = "{s}"\nquantity = {q}\n'
        f'asset_type = "equity"\n\n' for s, q in holdings.items())
    path.write_text(f'schema_version = "1.0"\n[meta]\n'
                    f'account = "{account}"\n\n{rows}')
