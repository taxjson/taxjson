"""Shared test helpers: IB statement rows and the parse helpers.

Shared from: test_fix_ibparse
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from taxjson.lib.brokerages.ib_extractor import IbBrokerage
import _paths


REPO_ROOT = _paths.REPO

HEAD = ('Statement,Header,Field Name,Field Value\n'
        'Statement,Data,BrokerName,Interactive Brokers\n'
        'Statement,Data,Title,Activity Statement\n')
TRADES_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
            'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
            'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
XFER_H = ('Transfers,Header,Asset Category,Currency,Symbol,Date,Type,'
          'Direction,Xfer Company,Xfer Account,Qty,Xfer Price,'
          'Market Value,Realized P/L,Cash Amount,Code\n')
CA_H = ('Corporate Actions,Header,Asset Category,Currency,Report Date,'
        'Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,'
        'Code\n')


def _trade(sym, when, qty, price, proceeds, comm=0, code='O',
           cat='Stocks', cur='USD', acct='U5550001', disc='Order'):  # pii-ok
    return (f'Trades,Data,{disc},{cat},{cur},{acct},{sym},"{when}",{qty},'
            f'{price},0,{proceeds},{comm},0,0,0,{code}\n')


def _xfer(sym, date, qty, value, typ='ACATS', cat='Stocks', cur='USD',
          code=''):
    return (f'Transfers,Data,{cat},{cur},{sym},{date},{typ},In,'
            f'Other Broker,5550009,{qty},0,{value},0,0,{code}\n')  # pii-ok


def _ca(desc, qty, value=0, proceeds=0, cat='Stocks', cur='USD',
        when='2025-03-02, 20:25:00', code=''):
    return (f'Corporate Actions,Data,{cat},{cur},2025-03-02,"{when}",'
            f'"{desc}",{qty},{proceeds},{value},0,{code}\n')


def _parse_ib(text, name='ib.csv'):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(text, encoding='utf-8')
        parser = IbBrokerage()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            txs = parser.parse_file(p)
    return parser, txs, err.getvalue()


def _brokerage_cli(files, *extra, brokerage='ib'):
    """Run taxjson-brokerage over {name: text}; (rc, json-or-None,
    stderr, {sidecar})."""
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for name, text in files.items():
            p = Path(td) / name
            p.write_text(text, encoding='utf-8')
            if not name.endswith('.txt'):
                paths.append(str(p))
        args = [a.replace('{TD}', td) for a in extra]
        env = dict(os.environ)
        env['PYTHONPATH'] = str(REPO_ROOT / 'src')
        r = subprocess.run(
            [sys.executable, '-m', 'taxjson.bin.taxjson_brokerage',
             '--brokerage', brokerage, *args, *paths],
            capture_output=True, text=True, cwd=REPO_ROOT, env=env)
        side = Path(td) / 'side.json'
        sidecar = (json.loads(side.read_text()) if side.exists() else None)
    out = json.loads(r.stdout) if r.returncode == 0 and r.stdout else None
    return r.returncode, out, r.stderr, sidecar


def _period(start, end):
    return f'Statement,Data,Period,"{start} - {end}"\n'


FII_H = ('Financial Instrument Information,Header,Asset Category,Symbol,'
         'Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,'
         'Expiry,Delivery Month,Type,Strike,Code\n')
DIV_H = 'Dividends,Header,Currency,Date,Description,Amount\n'
WHT_H = 'Withholding Tax,Header,Currency,Date,Description,Amount\n'


def _fii_stock(sym, isin, conid='990000001', exch='NYSE'):
    return (f'Financial Instrument Information,Data,Stocks,"{sym}",{sym} '
            f'CORP,{conid},{isin},,{exch},1,,,COMMON,,\n')


def _parse_account(files):
    """prepare_files + parse_file over {name: text}, like
    taxjson-brokerage. -> (txs, stderr)."""
    err = io.StringIO()
    txs = []
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for name, text in files.items():
            p = Path(td) / name
            p.write_text(text, encoding='utf-8')
            paths.append(p)
        with contextlib.redirect_stderr(err):
            ctx = IbBrokerage.prepare_files(paths)
            for p in paths:
                b = IbBrokerage()
                b.account_context = ctx
                txs += b.parse_file(p)
    return txs, err.getvalue()


def _fii_opt(syms, desc, conid, underlying):
    return (f'Financial Instrument Information,Data,Equity and Index '
            f'Options,"{syms}",{desc},{conid},,{underlying},CBOE,100,'
            f'2026-01-16,2026-01,P,44,\n')
