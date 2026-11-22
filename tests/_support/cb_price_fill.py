"""Shared test helpers: Coinbase rows, the brokerage run and the price fill.

Shared from: test_fix_1029_r7c_crypto
"""
import io
import json
import os
import subprocess
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from taxjson.bin import fill_crypto_prices as FC
from taxjson.lib import crypto_prices
from taxjson.lib.dispatch import python_module_argv


_HDR = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
        "Price Currency,Price at Transaction,Subtotal,"
        "Total (inclusive of Fees and/or Spread),Fees and/or Spread,Notes\n")


def _brokerage(td, name, broker, text):
    """`taxjson-brokerage --strict` on one synthetic file: (rc, the
    rows' JSON path, stderr)."""
    d = Path(td) / name
    d.mkdir()
    src = d / ("coinbase.csv" if broker == "coinbase" else "ledgers.csv")
    src.write_text(text)
    r = subprocess.run(
        python_module_argv("taxjson.bin.taxjson_brokerage",
                           ["--brokerage", broker, "--country", "canada",
                            "--strict", str(src)]),
        capture_output=True, text=True,
        env=dict(os.environ, TAXJSON_WIDTH="0",
                 TAXJSON_LOCAL_TZ="America/Toronto"))
    out = d / "parsed.json"
    out.write_text(r.stdout)
    return r.returncode, out, r.stderr


def _fill(path, cache):
    """fill-crypto's _fill on a parsed file with a fixed cache and no
    network: the filled rows."""
    out = io.StringIO()
    with patch.object(crypto_prices, "load_cache", return_value=dict(cache)), \
            patch.object(crypto_prices, "save_cache"), \
            patch.object(crypto_prices, "get_crypto_price",
                         side_effect=AssertionError("network")), \
            redirect_stdout(out), redirect_stderr(io.StringIO()):
        FC._fill(Namespace(input=str(path)))
    return json.loads(out.getvalue())["transactions"]
