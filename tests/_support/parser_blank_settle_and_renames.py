"""Shared test helpers: Questrade, RBC Direct and generic buys with a blank settlement cell parsed to the date the parser fills in; Questrade and Webull ticker-rename rows; taxjson-brokerage over files and a ticker.map.

Shared from: test_fix_def_rest
"""
import tempfile
from pathlib import Path

from _support.parser_qt_rbc_rows import q, qt_parse
from _support.rbc_rows import parse_one as rbc_parse, row as rbc_row
from _support.generic_parse import _parse as gen_parse, _toml as gen_toml
import _paths

import os
import subprocess
import sys

from _support.wb_summary_2025 import _H25 as WB_H25, _PRE as WB_PRE


def _qt_settle(td, sym, cur, desc="QZU FUND WE ACTED AS AGENT", net=None):
    body = q(td=td, sd='', sym=sym, desc=desc, qty='10', price='10',
             gross='-100', comm='0', net=net or '-100', cur=cur)
    txs, err, _ = qt_parse(body)
    assert len(txs) == 1, err
    return txs[0]['symbol'], txs[0]['date_settle']


def _rbc_settle(td_long, sym, symdesc, cur):
    txs, err, _ = rbc_parse(rbc_row(td_long, "Buy", sym, symdesc, "10", "10",
                                    "-100", cur, f"{symdesc} UNSOLICITED",
                                    settle=" "))
    assert len(txs) == 1, err
    return txs[0]['symbol'], txs[0]['date_settle']


def _gen_settle(td, sym, cur):
    txs, err, _ = gen_parse(
        "Date,Type,Ticker,Qty,Price,Amount,Fee,Currency\n"
        f"{td},BUY,{sym},10,10,-100,0,{cur}\n", gen_toml())
    assert len(txs) == 1, err
    return txs[0]['symbol'], txs[0]['date_settle']


REPO = _paths.REPO

QT_RENAME = (q(sym='QQOL', desc='QQ HOLDINGS CORP WE ACTED AS AGENT',
               qty='500', price='10', gross='-5000', comm='0', net='-5000')
             + q(td='2025-09-10', action='Sell', sym='QQNW',
                 desc='QQ HOLDINGS CORP WE ACTED AS AGENT', qty='-500',
                 price='20', gross='10000', comm='0', net='10000'))
WB_RENAME = (WB_PRE + WB_H25
             + 'USD,10-03-2025,BUY,QQOL,QQ HOLDINGS CORP,SHS,500,10.00,,'
               '"(5,002.99)"\n'
             'USD,12-06-2025,SELL,QQNW,QQ HOLDINGS CORP,SHS,-500,20.00,,'
             '"9,997.01"\n')


def _brokerage(brokerage, files, tmap=None, *extra):
    """taxjson-brokerage over {name: text} (+ a ticker.map text):
    (rc, stderr)."""
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for name, text in files.items():
            p = Path(td) / name
            p.write_text(text, encoding='utf-8')
            paths.append(str(p))
        args = list(extra)
        if tmap is not None:
            m = Path(td) / 'ticker.map'
            m.write_text(tmap)
            args += ['--ticker-map', str(m)]
        env = dict(os.environ, PYTHONPATH=str(REPO / 'src'))
        r = subprocess.run(
            [sys.executable, '-m', 'taxjson.bin.taxjson_brokerage',
             '--brokerage', brokerage, *args, *paths],
            capture_output=True, text=True, cwd=REPO, env=env,
            stdin=subprocess.DEVNULL)
    return r.returncode, r.stderr
