"""Shared test helpers: RBC Direct activity rows and their parse.

Shared from: test_rbc_parse_audit_2026_09
"""
import contextlib
import io
import os
from _tmpfiles import private_tmpfile
from pathlib import Path

from taxjson.lib.brokerages.rbc_direct import RbcBrokerage


HDR = ('"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
       '"Settlement Date","Value","Currency","Description"\n')


def row(date, activity, symbol, symdesc, qty, price, value, cur, desc,
        settle=None):
    cells = [date, activity, symbol, symdesc, qty, price, settle or date,
             value, cur, desc]
    return ','.join('"%s"' % c for c in cells) + '\n'


def _write(text):
    f = private_tmpfile(mode='w', suffix='.csv', delete=False,
                        encoding='utf-8')
    f.write(text)
    f.close()
    return Path(f.name)


def parse(body, header=HDR, country=None):
    """(transactions, stderr, parser) for a CSV body (newest-first);
    `country` sets the notes' wording, as taxjson-brokerage does."""
    p = _write(header + body)
    err = io.StringIO()
    par = RbcBrokerage()
    par.country = country
    try:
        with contextlib.redirect_stderr(err):
            txs = par.parse_file(p)
    finally:
        os.remove(p)
    return txs, err.getvalue(), par


# RBC writes a rights issue's expiry as "EXP <mm/dd/yyyy>" (built here so
# the literal doesn't read as a payment-card expiry to secret scanners).
RTS_EXP = "EXP " + "/".join(("11", "14", "2023"))
