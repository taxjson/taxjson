"""Shared test helpers: a Questrade margin export and its config, a project of it, the report views' project of raw rows and the CLI.

Shared from: test_fix_l_runcore_a
"""
import json
import os
import subprocess
import sys
from pathlib import Path
import _paths


REPO_ROOT = _paths.REPO

_CONFIG = """\
[settings]
local_timezone = "America/Toronto"
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []
option_grant_timing_since = 2025

[accounts.margin]
type = "taxable"
"""

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")

# Buy 100 @ 10.00 (comm 9.95) -> ACB 1009.95; sell 100 @ 15.00 (comm
# 9.95) -> proceeds 1490.05; gain 480.10.
_MARGIN_CSV = _QT_HEADER + (
    "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,ISHARES COMP,"
    "100,10.00,1000.00,9.95,-1009.95,CAD,55500001,Trades,Individual\n"
    "2025-06-20 10:15:00 AM,2025-06-23 12:00:00 AM,Sell,XEI.TO,ISHARES COMP,"
    "-100,15.00,1500.00,9.95,1490.05,CAD,55500001,Trades,Individual\n")


def _run_cli(root, *args, env=None):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e.update(env or {})
    pre = ["-C", str(root)] if root is not None else []
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", *pre, *args],
        cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _project(tmp, config=_CONFIG, csv=_MARGIN_CSV):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(config)
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "questrade_2025.csv").write_text(csv)
    return root


def _with_setting(line):
    return _CONFIG.replace("source_currencies = []\n",
                           f"source_currencies = []\n{line}\n")


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))


def _row(action, date, symbol, **kw):
    d = {"action": action, "date": date, "time": "10:00:00",
         "symbol": symbol, "currency": "CAD"}
    d.update(kw)
    return d


_MIXED_CFG = _CONFIG + ('[accounts.rrsp]\ntype = "sheltered"\n'
                        '[accounts.kr]\ntype = "taxable"\n'
                        'crypto = true\n')


def _views_project(tmp):
    """A project the report views read: margin's and rrsp's raw rows and
    a crypto account's staking reward, written to its work/."""
    root = _project(tmp, _MIXED_CFG)
    work = root / "work"
    _write(work / "margin_raw.json", {"transactions": [
        _row("DIVIDEND", "2025-05-01", "AAA.TO", gross_amount=30.0,
             net_amount=30.0),
        _row("DIVIDEND_IN_LIEU", "2025-05-15", "AAA.TO",
             gross_amount=10.0, net_amount=10.0),
        _row("ADJUST", "2025-06-01", "AAA.TO", net_amount=-5.0,
             type="roc"),
        _row("BUYSELL", "2025-07-01", "AAA.TO", quantity=-10,
             price=10.0, net_amount=100.0)]})
    _write(work / "rrsp_raw.json", {"transactions": [
        _row("DIVIDEND_IN_LIEU", "2025-05-15", "BBB.TO",
             gross_amount=900.0, net_amount=900.0),
        _row("ADJUST", "2025-06-01", "BBB.TO", net_amount=-500.0,
             type="roc"),
        _row("BUYSELL", "2025-07-01", "BBB.TO", quantity=-10,
             price=500.0, net_amount=5000.0)]})
    _write(work / "kr_filled.json", {"transactions": [
        _row("DIVIDEND", "2025-05-03", "ETH", gross_amount=7.0,
             net_amount=7.0)]})
    return root
