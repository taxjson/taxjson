"""Shared test helpers: Coinbase rows and the Kraken ledger header.

Shared from: test_fix_m_parsers2_crypto
"""
import contextlib
import io
import tempfile
from pathlib import Path


CB_HEADER = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
             "Price Currency,Price at Transaction,Subtotal,"
             "Total (inclusive of fees and/or spread),Fees and/or Spread,"
             "Notes\n")
KR_LEDGER_H = ("txid,refid,time,type,subtype,aclass,subclass,asset,wallet,"
               "amount,fee,balance\n")


def _cb_row(cid, ts, typ, asset, qty, ccy, price, sub, total, fee,
            notes=""):
    return (f'{cid},{ts},{typ},{asset},{qty},{ccy},"{price}","{sub}",'
            f'"{total}","{fee}","{notes}"\n')


def _parse_cb(text, header=CB_HEADER):
    from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "cb_2025.csv"
        p.write_text(header + text)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            txs = CoinbaseBrokerage().parse_file(p)
    return txs, err.getvalue()


def _bs(txs):
    return [t for t in txs if t["action"] == "BUYSELL"]
