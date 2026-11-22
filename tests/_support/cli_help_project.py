"""Shared test helpers: a project and the CLI for the help page and messages.

Shared from: test_cli_polish
"""
import os
import subprocess
import sys
from pathlib import Path

import _paths


REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"


def _env():
    return dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
                NO_COLOR="1", COLUMNS="78")


def _cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", *args],
        cwd=cwd or REPO_ROOT, capture_output=True, text=True, env=_env(),
        stdin=subprocess.DEVNULL, timeout=600)


def _toml(country, extra=""):
    base = "USD" if country == "usa" else "CAD"
    return (f'[settings]\nlocal_timezone = "America/Toronto"\nyear = 2026\ncountry = "{country}"\n'
            f'base_currency = "{base}"\nsource_currencies = []\n{extra}'
            f'[accounts.margin]\ntype = "taxable"\n'
            f'[accounts.coins]\ntype = "taxable"\ncrypto = true\n'
            f'[accounts.tfsa]\ntype = "sheltered"\n')


# One book per class (CAD). ABC.TO shares back the covered calls.
MARGIN = """\
BUYSELL 2026-01-05 10:00:00 ABC.TO 500 CAD 40.00 20000.00 0.00
BUYSELL 2026-02-02 10:00:00 ABC260320C00050000.TO -5 CAD 4.00 1995.00 5.00
BUYSELL 2026-03-05 10:00:00 ABC260320C00050000.TO 5 CAD 2.00 1005.00 5.00
BUYSELL 2026-02-02 10:00:00 ABC260417C00055000.TO -5 CAD 1.00 495.00 5.00
BUYSELL 2026-04-17 16:00:00 ABC260417C00055000.TO 5 CAD 0.00 0.00 0.00
BUYSELL 2026-05-04 10:00:00 ABC260619C00045000.TO -2 CAD 3.00 600.00 0.00
ASSIGN 2026-06-19 16:00:00 ABC260619C00045000.TO 2 CAD 0 0
ASSIGN 2026-06-19 16:00:01 ABC.TO -200 CAD 45 9000
BUYSELL 2026-05-04 10:00:00 ABC260717C00060000.TO -5 CAD 2.00 1000.00 0.00
BUYSELL 2026-06-01 10:00:00 ABC260717C00060000.TO 2 CAD 3.00 600.00 0.00
BUYSELL 2026-07-17 16:00:00 ABC260717C00060000.TO 3 CAD 0.00 0.00 0.00
BUYSELL 2026-02-10 10:00:00 XYZ260918P00030000.TO 2 CAD 1.50 300.00 0.00
BUYSELL 2026-03-10 10:00:00 XYZ260918P00030000.TO -1 CAD 2.50 250.00 0.00
BUYSELL 2026-04-10 10:00:00 XYZ260918P00030000.TO -1 CAD 0.50 50.00 0.00
BUYSELL 2026-03-02 10:00:00 SHT.TO -100 CAD 20.00 2000.00 0.00
BUYSELL 2026-04-02 10:00:00 SHT.TO 100 CAD 15.00 1500.00 0.00
BUYSELL 2026-08-03 10:00:00 F:SXFZ6.TO 1 CAD 1000 200000 0 x200
BUYSELL 2026-08-10 10:00:00 F:SXFZ6.TO -1 CAD 1010 202000 0 x200
BUYSELL 2026-02-03 10:00:00 LOS.TO 100 CAD 10.00 1000.00 0.00
BUYSELL 2026-03-03 10:00:00 LOS.TO -100 CAD 8.00 800.00 0.00
BUYSELL 2026-03-24 10:00:00 LOS.TO 50 CAD 8.00 400.00 0.00
"""
COINS = """\
BUYSELL 2026-01-10 10:00:00 BTC 1 CAD 50000 50000 0
BUYSELL 2026-05-10 10:00:00 BTC -0.5 CAD 60000 30000 0
"""
TFSA = """\
BUYSELL 2026-01-12 10:00:00 DEF.TO 10 CAD 10.00 100.00 0.00
BUYSELL 2026-02-12 10:00:00 DEF.TO -10 CAD 12.00 120.00 0.00
"""


def _project(tmp, name, country="canada", extra="", books=None):
    root = Path(tmp) / name
    (root / "inputs").mkdir(parents=True)
    (root / "taxjson.toml").write_text(_toml(country, extra))
    for acct, text in (books or {"margin": MARGIN, "coins": COINS,
                                 "tfsa": TFSA}).items():
        d = root / "inputs" / acct
        d.mkdir()
        (d / f"{acct}.tt").write_text(text)
    return root
