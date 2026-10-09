"""Helpers of the external QA suite's regression tests (synthetic data):
a project folder with inputs, `taxjson -C <project> ...` as a person
piping the output runs it (width 120), the gains rows of an account."""
import json
import os
import subprocess
import sys
from pathlib import Path

import _hermetic  # noqa: F401  (a synthetic HOME, offline: tests/_hermetic)

REPO = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, TAXJSON_OFFLINE="1", PYTHONPATH=str(REPO / "src"),
           NO_COLOR="1")
ENV.pop("TAXJSON_WIDTH", None)          # piped: width 120, as a person


def toml(country="canada", year=2024, extra_accounts=""):
    if country == "usa":
        head = ('[settings]\nlocal_timezone = "America/New_York"\n'
                f'year = {year}\ncountry = "usa"\nbase_currency = "USD"\n'
                'source_currencies = ["CAD"]\n')
    else:
        head = ('[settings]\nlocal_timezone = "America/Toronto"\n'
                f'year = {year}\ncountry = "canada"\nbase_currency = "CAD"\n'
                'source_currencies = ["USD"]\n'
                f'option_grant_timing_since = {year}\n')
    return head + '[accounts.margin]\ntype = "taxable"\n' + extra_accounts


def tj(root, *args, check=True):
    r = subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                        "-C", str(root), *args], capture_output=True,
                       text=True, env=ENV, stdin=subprocess.DEVNULL,
                       check=False)
    if check and r.returncode != 0:
        raise AssertionError(f"{args}: rc {r.returncode}\n"
                             f"{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
    return r


def project(tmp, name, files, country="canada", ticker_map=None,
            extra_accounts=""):
    root = Path(tmp) / name
    for rel, text in files.items():
        p = root / "inputs" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (root / "taxjson.toml").write_text(toml(country,
                                            extra_accounts=extra_accounts))
    if ticker_map is not None:
        (root / "ticker.map").write_text(ticker_map)
    return root


def gains(root, account="margin"):
    p = root / "work" / f"{account}_gains_wash.json"
    if not p.exists():
        p = root / "work" / f"{account}_gains.json"
    return [t for t in json.loads(p.read_text())["transactions"]]


def console(r):
    return r.stdout + r.stderr
