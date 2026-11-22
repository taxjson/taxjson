"""Shared test helpers: IB trade rows, futures instruments and a parse.

Shared from: test_parser_audit_2026_09b
"""
import contextlib
import io
import os
from _tmpfiles import private_tmpfile
from pathlib import Path


def _parse(parser, content, suffix='.csv', prefix='tmp'):
    with private_tmpfile('w', suffix=suffix, prefix=prefix,
                         delete=False,
                         encoding='utf-8') as f:
        f.write(content)
        name = f.name
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            txs = parser.parse_file(Path(name))
    finally:
        os.remove(name)
    return txs, err.getvalue()


IB_TRADES_HDR = (
    'Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,'
    'Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,'
    'Realized P/L,MTM P/L,Code\n')


def _ib_fii_futures(sym, mult):
    """Financial Instrument Information for a synthetic future. Added
    in the 2026-09 parse hardening: a futures contract size can no
    longer be guessed (it used to be taken as 1), so a fixture must
    state it the way a real statement does."""
    return ('Financial Instrument Information,Header,Asset Category,'
            'Symbol,Description,Conid,Underlying,Listing Exch,Multiplier,'
            'Expiry,Delivery Month,Code\n'
            f'Financial Instrument Information,Data,Futures,{sym},'
            f'{sym} TEST,999000001,{sym[:3]},CME,{mult},2026-06-26,'
            f'2026-06,\n')


def _ib_trade(cat, sym, when, qty, price, proceeds, comm, code):
    return (f'Trades,Data,Order,{cat},USD,{sym},"{when}",{qty},{price},0,'
            f'{proceeds},{comm},0,0,0,{code}\n')
