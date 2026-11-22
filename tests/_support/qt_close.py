"""Shared test helpers: Questrade rows and projects of taxable and sheltered accounts.

Shared from: test_fix_1029_r7e_close
"""


_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _project(root, accounts, year=2025, extra=""):
    """A Canadian project of `accounts` ({name: type}; a sheltered one a
    TFSA with transfers kept) and its input folders."""
    head = ('[settings]\nlocal_timezone = "America/Toronto"\n'
            f'year = {year}\ncountry = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\n')
    body = "".join(
        f'[accounts.{a}]\ntype = "{t}"\n'
        + ('plan = "tfsa"\ntransfers = true\n' if t == "sheltered" else "")
        for a, t in accounts.items())
    (root / "taxjson.toml").write_text(head + body + extra)
    (root / "ticker.map").write_text("")
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    return root


def _row(day, action, sym, qty, price, net, kind="Trades", desc="SYN CORP",
         num="99900001", typ="Margin"):
    return (f"{day},{day},{action},{sym},{desc},{qty},{price},{net},0,{net},"
            f"CAD,{kind},{num},{typ}\n")
