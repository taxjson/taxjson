"""Shared test helpers: RBC Direct rows and the parse helpers.

Shared from: test_fix_rbc
"""
import contextlib
import io
import tempfile
from pathlib import Path

from taxjson.lib.brokerages.rbc_direct import RbcBrokerage


ACCT = "55500001"  # pii-ok (synthetic)
HDR = ('"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
       '"Settlement Date","Account","Value","Currency","Description"\n')
HDR_NOACCT = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
              '"Price","Settlement Date","Value","Currency","Description"\n')


def row(date, activity, symbol, symdesc, qty, price, value, cur, desc,
        settle=None, acct=ACCT):
    cells = [date, activity, symbol, symdesc, qty, price, settle or date]
    if acct is not None:
        cells.append(acct)
    cells += [value, cur, desc]
    return ','.join('"%s"' % c for c in cells) + '\n'


def parse_files(files, header=HDR):
    """Parse {name: body} as ONE account (the way taxjson-brokerage does:
    shared account context, one extractor per file, files in name order).
    Returns (transactions, stderr, [parsers])."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, body in sorted(files.items()):
            p = Path(d) / name
            p.write_text(header + body, encoding='utf-8')
            paths.append(p)
        err = io.StringIO()
        txs, pars = [], []
        with contextlib.redirect_stderr(err):
            ctx = RbcBrokerage.prepare_files(paths)
            for p in paths:
                par = RbcBrokerage()
                par.account_context = ctx
                txs.extend(par.parse_file(p))
                pars.append(par)
    return txs, err.getvalue(), pars


def parse_one(body, header=HDR):
    """A single file, no shared context (the old entry point)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / 'rbc.csv'
        p.write_text(header + body, encoding='utf-8')
        err = io.StringIO()
        par = RbcBrokerage()
        with contextlib.redirect_stderr(err):
            txs = par.parse_file(p)
    return txs, err.getvalue(), par


NWC = "NORTHWIND CAPITAL CORPORATION COMMON STOCK"
RQA_ROWS = [
    row("September 6, 2023", "Sell", "RQB", NWC, "-1170", "15", "17540.05",
        "USD", "NORTHWIND CAPITAL CORPORATION UNSOLICITED CA"),
    row("May 30, 2023", "Buy", "RQA", NWC, "450", "13", "-5859.95", "USD",
        "SOUTHWIND CAPITAL CORPORATION UNSOLICITED DA"),
    row("April 11, 2023", "Buy", "RQA", NWC, "720", "13", "-9369.95", "USD",
        "SOUTHWIND CAPITAL CORPORATION UNSOLICITED DA"),
]
ABC_REM = row("April 18, 2026", "Reorganization", "A012345", "ABQCORP INC",
              "-100", "", "0", "CAD", "NAC - ABQCORP INC NAME CHANGE TO NEWQO INC")
ABC_REC = row("April 18, 2026", "Reorganization", "NEWQ", "NEWQO INC COM", "100",
              "", "0", "CAD", "NAC - NEWQO INC COM RESULT OF NAME CHANGE")
ABC_SELL = row("August 3, 2026", "Sell", "NEWQ", "NEWQO INC COM", "-100", "12",
               "1200", "CAD", "NEWQO INC COM UNSOLICITED")
