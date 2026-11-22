"""Shared test helpers: taxjson-brokerage over crypto files.

Shared from: test_fix_1029_r2_cryptoparse
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths


REPO_ROOT = _paths.REPO


def _brokerage(files, broker, *extra):
    """taxjson-brokerage --country canada over `files` (alone in a
    folder of their own, in name order) with a transfer sidecar: (the
    CompletedProcess, the sidecar's rows or None)."""
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for name, text in sorted(files.items()):
            (Path(td) / name).write_text(text)
            paths.append(str(Path(td) / name))
        side = Path(td) / "side.json"
        env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
        r = subprocess.run(
            python_module_argv("taxjson.bin.taxjson_brokerage",
                               ["--brokerage", broker, "--country", "canada",
                                "--transfers-out", str(side), *extra,
                                *paths]),
            cwd=REPO_ROOT, capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL, timeout=300)
        rows = (json.loads(side.read_text())["transactions"]
                if side.exists() else None)
        return r, rows


_KL_H2 = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
          "balance,amountusd,feeusd,feecurrency\n")
