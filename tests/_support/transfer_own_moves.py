"""Shared test helpers: a move of your own between two taxable accounts.

Shared from: test_decisions_2026_10_26_ownmove
"""
import os
import subprocess
from pathlib import Path

from tax_rules.dual import settings_for

from taxjson.lib.dispatch import python_module_argv
import _paths


REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=600)


_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _row(d, ds, act, q, price, gross, comm, net, acct, desc="ZZB MINES LTD",
         kind="Trades"):
    return (f"{d},{ds},{act},ZZB,{desc},{q},{price},{gross},{comm},{net},"
            f"CAD,{kind},{acct},Margin\n")


def _move(d, q, acct, desc):
    return _row(d, d, "TF6", q, "0.00", "0.00", "0.00", "0.00", acct,
                desc=f"ZZB MINES LTD {desc}", kind="Transfers")


def _project(root: Path, variant: str = "base") -> Path:
    """margin buys 100 ZZB at 10.00 on 2024-03-04 and moves 50 to margin2
    on 2024-05-01 (Questrade TF6 rows; the in row states a book value of
    500.00); margin2 bought 100 at 20.00 on 2024-03-05 and sells 150 at
    30.00 on 2024-10-01. Variants: "slow" (the in row 19 days later),
    "sameday" (margin2 holds nothing else and sells the 50 the day they
    arrive), "loss" (margin sells 50 at a loss 6 days before it moves
    the other 50; margin2 still holds them at the year end)."""
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        settings_for("canada", year=2024, source_currencies=[])
        + '[accounts.margin]\ntype = "taxable"\n'
        + '[accounts.margin2]\ntype = "taxable"\n')
    for a in ("margin", "margin2"):
        (root / "inputs" / a).mkdir(parents=True)
    (root / "holdings").mkdir()
    in_d = "2024-05-20" if variant == "slow" else "2024-05-01"
    a1 = 99900001
    a2 = 99900003
    m1 = _row("2024-03-04", "2024-03-06", "Buy", 100, "10.00", "-1000.00",
              "-4.95", "-1004.95", a1)
    m2 = _row("2024-03-05", "2024-03-07", "Buy", 100, "20.00", "-2000.00",
              "-4.95", "-2004.95", a2)
    sell2 = _row("2024-10-01", "2024-10-03", "Sell", -150, "30.00",
                 "4500.00", "-4.95", "4495.05", a2)
    out1 = _move("2024-05-01", -50, a1, "TFER TO 99900003")
    in2 = _move(in_d, 50, a2, "TRANSFER BOOK VALUE 500.00")
    held = {"margin": 50, "margin2": 0}
    if variant == "sameday":
        m2 = ""
        sell2 = _row(in_d, "2024-05-03", "Sell", -50, "25.00", "1250.00",
                     "-4.95", "1245.05", a2)
    if variant == "loss":
        m1 = (_row("2024-01-10", "2024-01-12", "Buy", 100, "10.00",
                   "-1000.00", "-4.95", "-1004.95", a1)
              + _row("2024-04-25", "2024-04-29", "Sell", -50, "5.00",
                     "250.00", "-4.95", "245.05", a1))
        m2 = sell2 = ""
        held = {"margin": 0, "margin2": 50}
    (root / "inputs/margin/questrade_2024.csv").write_text(_QH + m1 + out1)
    (root / "inputs/margin2/questrade_2024.csv").write_text(
        _QH + m2 + in2 + sell2)
    for a, q in held.items():
        (root / "holdings" / f"{a}_holdings.toml").write_text(
            '[meta]\nas_of = "2024-12-31"\n'
            + (f'\n[[holding]]\nsymbol = "ZZB.TO"\nquantity = {q}\n'
               if q else ""))
    return root
