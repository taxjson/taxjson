"""Shared test helpers: Questrade rows, T1135 projects and cash books.

Shared from: test_fix_1029_r7e_cash
"""
import json

from _qa_project import tj


_Q_HDR = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
          "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
          "Activity Type,Account #,Account Type\n")


def _qt(td, sd, action, net, acct):
    kind = "Withdrawals" if action == "WDR" else "Deposits"
    what = "WITHDRAWAL" if action == "WDR" else "DEPOSIT"
    return (f"{td},{sd},{action},,{what},0,0,0,0,{net:.2f},USD,{kind},"
            f"{acct},Margin\n")


def _t1135_project(root, accounts, files, tax_date=None):
    """`accounts`: {name: country or None}; `files`: {account: {file:
    text}}; every .tt also gets a zero CAD opening and balance."""
    root.mkdir()
    head = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
            'country = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = ["USD"]\nfx_cash_ledger = "v2"\n')
    if tax_date:
        head += f'tax_date = "{tax_date}"\n'
    for a, cc in accounts.items():
        head += f'[accounts.{a}]\ntype = "taxable"\n'
        if cc:
            head += f'custodian_country = "{cc}"\n'
    (root / "taxjson.toml").write_text(head)
    (root / "ticker.map").write_text("")
    (root / "tobase.map").write_text("")
    for a, fs in files.items():
        (root / "inputs" / a).mkdir(parents=True)
        for name, text in fs.items():
            if name.endswith(".tt"):
                text += ("CASHOPEN 2025-01-01 CAD 0 0\n"
                         "CASHBAL 2025-12-31 CAD 0\n")
            (root / "inputs" / a / name).write_text(text)


def _t1135(root):
    tj(root, "run", "--no-input")
    return json.loads(tj(root, "t1135", "--json").stdout)


def _tt(open_units, open_cost, move=None, move_units=0, bal=0):
    t = f"CASHOPEN 2025-01-01 USD {open_units} {open_cost}\n"
    if move:
        t += f"CASHMOVE {move} USD {move_units} own\n"
    return t + f"CASHBAL 2025-12-31 USD {bal}\n"


# account's own units
def _generic(rows):
    return "Date,Action,Symbol,Quantity,Price,Amount\n" + "".join(
        f"{d},{a},{sym},{q},10,{-q * 10}\n" for d, a, sym, q in rows)
