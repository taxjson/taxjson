"""Shared test helpers: a margin project of broker files and the CLI.

Shared from: test_fix_a2_rbcqt
"""
import os
import subprocess
import sys
from pathlib import Path

import _paths


REPO = _paths.REPO


def _cli_run(root, *args):
    env = dict(os.environ, TAXJSON_OFFLINE="1")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=600, env=env)


def _project(root, year, files, extra_settings="", ticker_map=""):
    root = Path(root)
    (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\ncountry = "canada"\n'
        f'base_currency = "CAD"\nsource_currencies = []\n'
        f'option_grant_timing_since = 2024\n{extra_settings}'
        f'[accounts.margin]\ntype = "taxable"\n')
    (root / "ticker.map").write_text(ticker_map)
    for name, text in files.items():
        (root / "inputs" / "margin" / name).write_text(text)
    return root
