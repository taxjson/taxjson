"""Shared test helpers: a Questrade export with a spin-off and cash in lieu.

Shared from: test_fix_backlog_fxcorp
"""
import os
import subprocess
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv

import _paths


REPO_ROOT = _paths.REPO


def _cli(root, *args):
    e = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             ["-C", str(root), *args])
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, env=e, stdin=subprocess.DEVNULL,
                          timeout=600)


_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Account #,Activity Type,Account Type\n")


_ACCT = "99900001"         # a synthetic broker account id


def _q(d, action, sym, desc, qty, net="0", price="0", gross="0",
       act="Dividends"):
    t = f"{d} 12:00:00 AM"
    return ",".join([t, t, action, sym, desc, qty, price, gross, "0", net,
                     "CAD", _ACCT, act, "Individual margin"]) + "\n"


def _project(root: Path, files, year=2024):
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "work").mkdir()
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "canada"\nprovince = "ON"\n'
        'base_currency = "CAD"\n'
        '[accounts.margin]\ntype = "taxable"\n')
    for name, text in files.items():
        (root / "inputs" / "margin" / name).write_text(text)
    r = _cli(root, "run", "--no-input")
    assert r.returncode in (0, 3), r.stderr[-2000:]


def _qt_project(root, spin_day, cil_day, more=""):
    """A Questrade export: 105 ZZP.TO, a spin-off of 10 ZZS.TO on them
    and 0.5 paid in cash (12.00) on `cil_day`; not yet elected."""
    buy = _q("2024-01-05", "Buy", "ZZP.TO", "ZZP PARENT CORP", "105",
             net="-1050", price="10", gross="-1050", act="Trades")
    m, d = spin_day[5:7], spin_day[8:10]
    dis = _q(spin_day, "DIS", "ZZS.TO",
             f"ZZS SPINCO INC SPINOFF FROM SEC# X123456 ZZP PARENT CORP "
             f"REC {m}/01/24 PAY {m}/{d}/24 ON 105 SHS", "10")
    cil = _q(cil_day, "CIL", "ZZS.TO",
             "ZZS SPINCO INC CASH IN LIEU OF .5 SHS", "0", net="12.00",
             act="Other")
    _project(root, {"qt.csv": _QH + buy + dis + cil + more})
