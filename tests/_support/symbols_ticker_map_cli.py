"""Shared test helpers: the CLI for ticker-map --suggest.

Shared from: test_fix_ticker_map_suggest
"""
import os
import subprocess
import sys

import _paths


REPO = _paths.REPO


def _tjs(root, *args, stdin=None):
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"),
               TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL if stdin is None else stdin, timeout=300)
