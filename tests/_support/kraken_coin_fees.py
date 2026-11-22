"""Shared test helpers: the Kraken trades and ledger headers, a folder of Kraken files, trades and a ledger whose fees are taken in the coin, and a call with its stderr.

Shared from: test_crypto_parse_hardening
"""
import io
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
from _siblings import with_siblings


def _run(fn, *a, **kw):
    buf = io.StringIO()
    with redirect_stderr(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()


_KT_H = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
         "margin,misc,ledgers\n")
_KL_H = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
         "balance\n")


def _kraken_dir(files):
    from taxjson.lib.brokerages.kraken import KrakenBrokerage
    td = tempfile.TemporaryDirectory()
    for name, text in files.items():
        (Path(td.name) / name).write_text(text)
    return td, lambda: with_siblings(KrakenBrokerage())


# A Kraken trades export and its ledger: two fees taken in the coin
# traded, one in the quote currency.
_COIN_FEE_TRADES = (
    _KT_H +
    # buy 10 SOL for 2000 CAD; fee (5 CAD-equiv) taken in SOL
    "T1,O1,SOL/CAD,2025-06-02 16:00:00.1,buy,limit,200,2000,5,10,,,\n"
    # sell 4 SOL for 800 CAD; fee taken in SOL
    "T2,O2,SOL/CAD,2025-06-03 16:00:00.1,sell,limit,200,800,2,4,,,\n"
    # buy 1 ETH for 3000 CAD; fee in CAD (quote)
    "T3,O3,ETH/CAD,2025-06-04 16:00:00.1,buy,limit,3000,3000,7.5,1,,,\n")
_COIN_FEE_LEDGER = (
    _KL_H +
    "L1,T1,2025-06-02 16:00:00,trade,tradespot,currency,SOL,spot,10,0.025,9.975\n"
    "L2,T1,2025-06-02 16:00:00,trade,tradespot,currency,ZCAD,spot,-2000,0,0\n"
    "L3,T2,2025-06-03 16:00:00,trade,tradespot,currency,SOL,spot,-4,0.01,5.965\n"
    "L4,T2,2025-06-03 16:00:00,trade,tradespot,currency,ZCAD,spot,800,0,800\n"
    "L5,T3,2025-06-04 16:00:00,trade,tradespot,currency,XETH,spot,1,0,1\n"
    "L6,T3,2025-06-04 16:00:00,trade,tradespot,currency,ZCAD,spot,-3000,7.5,0\n")
