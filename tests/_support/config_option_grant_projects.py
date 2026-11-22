"""Shared test helpers: projects with option_grant_timing_since: a written option and its buyback, Questrade and Webull rows, and the config and checklist context.

Shared from: test_fix_newuser_signals
"""
from pathlib import Path

from taxjson.lib import checklist as cl
import tomllib
from taxjson.cli.checklist_run import default_run_sub


def flat(text):
    return " ".join(text.split())


def toml(year, country="canada", since=None, extra=""):
    if country == "usa":
        head = ('[settings]\nlocal_timezone = "America/New_York"\n'
                f'year = {year}\ncountry = "usa"\nbase_currency = "USD"\n'
                'source_currencies = ["CAD"]\n')
    else:
        head = ('[settings]\nlocal_timezone = "America/Toronto"\n'
                f'year = {year}\ncountry = "canada"\nbase_currency = "CAD"\n'
                'source_currencies = ["USD"]\n'
                f'option_grant_timing_since = {since or year}\n')
    return head + '[accounts.margin]\ntype = "taxable"\n' + extra


def make(tmp, name, year, files, **kw):
    root = Path(tmp) / name
    for rel, text in files.items():
        p = root / "inputs" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (root / "taxjson.toml").write_text(toml(year, **kw))
    return root


def cfg_of(root):
    return tomllib.loads((root / "taxjson.toml").read_text())


def ctx_of(root, year, today):
    return cl.Ctx(root=root, cfg=cfg_of(root), year=year, today=today,
                  run_sub=default_run_sub(root))


OPT = "ZZQ261218C00050000.TO"
WRITE_BUYBACK = (f"BUYSELL 2025-12-15 10:00:00 {OPT} -1 CAD 4 400\n"
                 f"BUYSELL 2026-01-12 10:00:00 {OPT} 1 CAD 1 100\n")
QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
           "Activity Type,Account #,Account Type\n")


def qt_row(day, settle, action, sym, qty, price, cur, name,
           acct="55500001", kind="Margin"):              # pii-ok
    gross = qty * price
    return (f"{day},{settle},{action},{sym},{name},{qty},{price:.2f},"
            f"{-gross:.2f},0,{-gross:.2f},{cur},Trades,{acct},{kind}\n")


WB_HEAD = ('"Currency","Date","Action Code","Symbol","Security Description",'
           '"Type Code","Quantity","Price","Proceeds"\n')


def webull(end, rows):
    return ("Webull Securities (Canada) Ltd.\nSynthetic Demo Statement\n"
            "Account Number: 12345678\n"
            f"Date Range: January 1 2025 - {end}\n\n" + WB_HEAD + rows)


WB_OPEN_CALLS = (
    'USD,15-01-2025,BUY,@ZZAA,ZZAA HOLDINGS INC,EQ,100,85.00,"(8,500.00)"\n'
    'USD,20-03-2025,SELL,@ZZAA,ZZAA HOLDINGS INC,EQ,100,90.00,"8,995.05"\n'
    'USD,22-04-2025,BUY,@ZZBB,CALL ZZBB01/16/26 45,OPC,14,1.55,'
    '"(2,184.75)"\n')
