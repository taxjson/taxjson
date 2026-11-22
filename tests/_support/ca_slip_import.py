"""Shared test helpers: Questrade rows, a project and slip tables for the CRA slip importer.

Shared from: test_fix_1029_r7e_slips
"""
from pathlib import Path

from _qa_project import tj
from taxjson.lib import cra_slips as CS
from _support.ca_slip_rows import QT


CA_HEAD = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
           'country = "canada"\nbase_currency = "CAD"\n'
           'source_currencies = ["USD"]\n')
QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
           "Activity Type,Account #,Account Type\n")


def _qt(acct, *rows):
    return QT_HEAD + "".join(f"{r},{acct},Margin\n" for r in rows)


def _qt_buy(sym, day="2025-01-06"):
    return (f"{day},{day},Buy,{sym},{sym} CORP,10,10.00,-100.00,0,-100.00,"
            f"CAD,Trades")


def _qt_div(sym, amt, day="2025-03-14"):
    return (f"{day},{day},DIV,{sym},{sym} CASH DIV ON 10 SHS REC 03/01/25 "
            f"PAY 03/14/25,0,0,{amt:.2f},0,{amt:.2f},CAD,Dividends")


def _cli_project(td, files):
    root = Path(td) / "p"
    for rel, text in files.items():
        p = root / "inputs" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (root / "taxjson.toml").write_text(
        CA_HEAD + '[accounts.margin]\ntype = "taxable"\n', encoding="utf-8")
    tj(root, "run", "--no-input")
    return root


def _slip_tables(root):
    p = root / "inputs" / "slips" / "slips.toml"
    if not p.is_file():
        return []
    return [ln for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip() == "[[slip]]"]


def _groups(a_rows, b_rows):
    return [CS.Group("margin", ("a" * 10,), "questrade", ("a.csv",), a_rows),
            CS.Group("margin", ("b" * 10,), "questrade", ("b.csv",), b_rows)]


def _place(boxes, groups, account=None):
    (pl,) = CS.place([CS.CraSlip("t5.pdf", 2025, "T5", "original", QT,
                                 boxes=boxes)], groups, 2025, {},
                     account=account)
    return pl


def _existing(index=1, status="original", boxes=None, keys=("k" * 10,),
              issuer=QT, source="", imported=True):
    return CS.Existing(index, "T5", "margin", "", issuer, status, "CAD",
                       boxes if boxes is not None else {"24": 100.0},
                       frozenset(keys), imported, source=source)
