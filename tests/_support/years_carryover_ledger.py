"""Shared test helpers: the carryover ledger.

Shared from: test_fix_tj1422_ledger
"""
import os
import subprocess

from taxjson.lib.dispatch import python_module_argv
import _paths


REPO_ROOT = _paths.REPO
_PYPATH = os.pathsep.join(str(REPO_ROOT / p) for p in (
    "src", "packages/taxjson-fetch/src"))
_TOML = ('[settings]\nyear = 2023\ncountry = "canada"\n'
         'base_currency = "CAD"\nsource_currencies = []\n'
         'tax_date = "settle"\nprovince = "ON"\n'
         'inputs_dir = "../inputs"\n\n'
         '[accounts.margin]\ntype = "taxable"\n\n'
         '[estimate]\nother_income = 50000\n')
_CLAIM = '\n[carryover]\nclaimed = { 2022 = 40.00 }\n'


def _cli(root, *args, broken=None, width="0"):
    env = dict(os.environ, TAXJSON_WIDTH=width, NO_COLOR="1",
               TAXJSON_OFFLINE="1")
    env["PYTHONPATH"] = os.pathsep.join(
        ([str(broken)] if broken else [])
        + [_PYPATH, env.get("PYTHONPATH", "")])
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *map(str, args)]),
        cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=env, timeout=900)


_GAIN_2022 = ("BUYSELL 2022-02-01 09:30:00 ZZQA.TO 50 CAD 10.00 -500.00 0.00\n"
              "BUYSELL 2022-08-01 09:30:00 ZZQA.TO -50 CAD 12.00 600.00 0.00\n")
