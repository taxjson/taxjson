"""Shared test helpers: Questrade rows and their parse.

Shared from: test_questrade_parse_hardening
"""
import contextlib
import io
import os
from _tmpfiles import private_tmpfile
from pathlib import Path

from taxjson.lib.brokerages.questrade import QuestradeBrokerage


H = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
     'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
     'Activity Type,Account Type\n')
ACCT = '55500001'  # pii-ok


def row(td='2026-02-02 12:00:00 AM', sd='2026-02-03 12:00:00 AM',
        action='Buy', sym='QZA', desc='QZA CORP WE ACTED AS AGENT', qty='10',
        price='50', gross='-500', comm='-4.95', net='-504.95', cur='USD',
        act='Trades', atype='Individual margin'):
    cells = [td, sd, action, sym, desc, qty, price, gross, comm, net, cur,
             ACCT, act, atype]
    return ','.join(f'"{c}"' if ',' in c else c for c in cells) + '\n'


def _parse(text, taxable=None, encoding='utf-8'):
    with private_tmpfile('w', suffix='.csv', delete=False,
                         encoding=encoding) as f:
        f.write(text)
        name = f.name
    p = QuestradeBrokerage()
    if taxable is not None:
        p.account_taxable = taxable
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            txs = p.parse_file(Path(name))
    finally:
        os.remove(name)
    return p, txs, err.getvalue()
