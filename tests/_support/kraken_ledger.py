"""Shared test helpers: the Kraken trade and ledger headers, a parse and a ledger.

Shared from: test_fix_a2_kraken
"""
import io
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.lib.brokerages.kraken import KrakenBrokerage
from _siblings import with_siblings


_KT_H = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
         "margin,misc,ledgers\n")
_KL_H = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
         "balance\n")
_KL_H2 = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
          "balance,amountusd,feeusd,feecurrency\n")


def _parse(files, which, cash=True, want_extractor=False):
    with tempfile.TemporaryDirectory() as td:
        for name, text in files.items():
            (Path(td) / name).write_text(text)
        k = with_siblings(KrakenBrokerage())
        k.stablecoins_as_cash = cash
        buf = io.StringIO()
        with redirect_stderr(buf):
            txs = k.parse_file(Path(td) / which)
        if want_extractor:
            return txs, buf.getvalue(), k
        return txs, buf.getvalue()


def _ledger(rows, header=_KL_H):
    return header + "".join(r + "\n" for r in rows)
