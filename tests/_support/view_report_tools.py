"""Shared test helpers: the report tools run as a child process (`python -m taxjson.bin.<tool>`) and their rows.

Shared from: test_fixl_planning_reports
"""
import os
import subprocess
import sys
import _paths


REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"


def _env(seed=None):
    env = dict(os.environ, PYTHONPATH=str(SRC), NO_COLOR="1")
    if seed is not None:
        env["PYTHONHASHSEED"] = str(seed)
    return env


def _tool(module, *args, stdin=None, seed=None, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", f"taxjson.bin.{module}", *map(str, args)],
        cwd=cwd or REPO_ROOT, capture_output=True, text=True,
        input=stdin, env=_env(seed))


def _row(date_, symbol, qty, net, account="margin", action="BUYSELL",
         currency="CAD", **kw):
    r = dict(action=action, date=date_, date_settle=date_, time="10:00:00",
             symbol=symbol, quantity=qty, net_amount=net, currency=currency,
             account=account, price=abs(net / qty) if qty else 0.0)
    r.update(kw)
    return r
