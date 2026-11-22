"""Shared test helpers: the Coinbase header and its parse.

Shared from: test_fix_a2_coinbase
"""
import contextlib
import io
import tempfile
from pathlib import Path

from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage


_HDR = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
        "Price Currency,Price at Transaction,Subtotal,"
        "Total (inclusive of fees and/or spread),Fees and/or Spread,Notes\n")


def _parse(body, header=_HDR, stablecoins_as_cash=True):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "cb_2025.csv"
        f.write_text("Transactions\n" + header + body)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            parser = CoinbaseBrokerage()
            parser.stablecoins_as_cash = stablecoins_as_cash
            tx = parser.parse_file(f)
        return tx, err.getvalue(), parser
