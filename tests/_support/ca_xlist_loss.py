"""Shared test helpers: a loss on one listing, the other bought in 30 days.

Shared from: test_fix_qa_f3_xlist_loss
"""
from _qa_project import gains, project


QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
           "Activity Type,Account #,Account Type\n")
NAME = "ZZXCORP INTERNATIONAL INC COM"


def qt_row(day, settle, action, sym, qty, price, cur, name=NAME,
           acct="55500001", kind="Margin"):              # pii-ok
    gross = qty * price
    return (f"{day},{settle},{action},{sym},{name},{qty},{price:.2f},"
            f"{-gross:.2f},0,{-gross:.2f},{cur},Trades,{acct},{kind}\n")


F3_ROWS = (qt_row("2024-01-02", "2024-01-04", "Buy", "ZZX.TO", 100, 70, "CAD")
           + qt_row("2024-03-01", "2024-03-05", "Sell", "ZZX.TO", -100, 60,
                    "CAD")
           + qt_row("2024-03-11", "2024-03-13", "Buy", "ZZX", 100, 45, "USD"))


def f3_project(tmp, name, rows=F3_ROWS, country="canada", ticker_map=None,
               extra=None, extra_accounts=""):
    files = {"margin/questrade_2024.csv": QT_HEAD + rows}
    files.update(extra or {})
    return project(tmp, name, files, country=country, ticker_map=ticker_map,
                   extra_accounts=extra_accounts)


def zzx_loss(root):
    return [g for g in gains(root) if g.get("symbol") == "ZZX.TO"
            and g.get("direction") == "LONG"]
