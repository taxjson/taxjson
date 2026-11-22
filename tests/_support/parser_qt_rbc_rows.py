"""Shared test helpers: Questrade and RBC Direct rows and their parse.

Shared from: test_fix_rbcqt
"""
import contextlib
import io
import tempfile
from pathlib import Path

from taxjson.lib.brokerages.questrade import QuestradeBrokerage

from taxjson.lib.brokerages.rbc_direct import RbcBrokerage


QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')
ACCT = '55500001'  # pii-ok (synthetic)


def q(td='2025-02-03', sd=None, action='Buy', sym='QZA',
      desc='QZA CORP WE ACTED AS AGENT', qty='10', price='50', gross='-500',
      comm='-4.95', net='-504.95', cur='USD', act='Trades',
      atype='Individual margin'):
    """One Questrade export row (dates as YYYY-MM-DD; '' = blank)."""
    def dt(x):
        return f"{x} 12:00:00 AM" if x else ''
    sd = td if sd is None else sd
    cells = [dt(td), dt(sd), action, sym, desc, qty, price, gross, comm, net,
             cur, ACCT, act, atype]
    return ','.join(f'"{c}"' if ',' in c else c for c in cells) + '\n'


def qdiv(sym, desc, net, td='2025-06-02', cur='USD', qty='0',
         action='DIV'):
    return q(td=td, action=action, sym=sym, desc=desc, qty=qty, price='0',
             gross='0', comm='0', net=net, cur=cur, act='Dividends')


def qt_parse(*bodies, taxable=None):
    """Parse one or more Questrade files as ONE account (the way
    taxjson-brokerage does). Returns (transactions, stderr, parsers)."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for i, body in enumerate(bodies):
            p = Path(d) / f"questrade_{2025 + i}.csv"
            p.write_text(QH + body, encoding='utf-8')
            paths.append(p)
        err = io.StringIO()
        txs, pars = [], []
        with contextlib.redirect_stderr(err):
            prep = getattr(QuestradeBrokerage, 'prepare_files', None)
            ctx = prep(paths) if prep else None
            for p in paths:
                par = QuestradeBrokerage()
                par.account_context = ctx
                if taxable is not None:
                    par.account_taxable = taxable
                txs.extend(par.parse_file(p))
                pars.append(par)
    return txs, err.getvalue(), pars


def of(txs, **kw):
    return [t for t in txs if all(t.get(k) == v for k, v in kw.items())]


RH = ('"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
      '"Settlement Date","Account","Value","Currency","Description"\n')


def rrow(date, activity, symbol, symdesc, qty, price, value, cur, desc,
         settle=None):
    cells = [date, activity, symbol, symdesc, qty, price,
             date if settle is None else settle, ACCT, value, cur, desc]
    return ','.join('"%s"' % c for c in cells) + '\n'


def rbc_parse(*bodies, raw=False):
    """Parse RBC files as ONE account. Returns (txs, stderr, parsers)."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for i, body in enumerate(bodies):
            p = Path(d) / f"rbc_{2025 + i}.csv"
            p.write_text(body if raw else RH + body, encoding='utf-8')
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


RBUY = rrow("March 3, 2025", "Buy", "XYZ", "XYZ CORP", "100", "50",
            "-5009.95", "CAD", "XYZ CORP UNSOLICITED DA")
