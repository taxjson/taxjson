"""Shared test helpers: the CLI and year folders for tjs redact.

Shared from: test_fix_1029_r2_redact
"""
import os
import subprocess
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv


CFG = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
       'country = "canada"\nbase_currency = "CAD"\nsource_currencies = []\n'
       '[accounts.margin]\ntype = "taxable"\n')
TT = ("BUYSELL 2025-01-03 10:00:00 SYN.TO 10 CAD 10 100 0\n"
      "BUYSELL 2025-02-03 10:00:00 SYN.TO -10 CAD 20 200 0\n")


def tjs(root, *args, cwd=None):
    """`tjs -C root <args>`, captured at width 0, offline."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        cwd=cwd or root, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=600)


def year_folder(top: Path, year: int = 2025, extra: str = "",
                tt: str = TT) -> Path:
    """top/<year>, a year folder of a project whose exports every year
    shares (top/inputs/margin/trades.tt); `extra` ends its taxjson.toml
    (the [accounts.margin] table)."""
    p = top / str(year)
    p.mkdir(parents=True)
    acct = top / "inputs" / "margin"
    if not acct.is_dir():
        acct.mkdir(parents=True)
        (acct / "trades.tt").write_text(tt)
    (p / "taxjson.toml").write_text(
        CFG.format(year=year).replace(
            "[accounts.margin]", 'inputs_dir = "../inputs"\n'
                                 '[accounts.margin]') + extra)
    (p / "ticker.map").write_text("")
    return p


def toml(path: Path) -> dict:
    import tomllib
    return tomllib.loads(path.read_text(encoding="utf-8"))
