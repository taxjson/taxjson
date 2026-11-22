"""Shared test helpers: Questrade rows parsed as one account.

Shared from: test_fix_1029_r2_brokerparse
"""
import contextlib
import io
import tempfile
from pathlib import Path

from taxjson.lib.brokerages.questrade import QuestradeBrokerage


QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')
ACCT = '55500001'   # pii-ok (synthetic)


def qrow(td='2025-02-03', action='Buy', sym='SYN', desc='SYN CORP',
         qty='10', price='10', gross='-100', comm='0', net='-100',
         cur='CAD', acct=ACCT, act='Trades', atype='Individual margin'):
    """One Questrade export row (dates as YYYY-MM-DD)."""
    dt = f"{td} 12:00:00 AM"
    cells = [dt, dt, action, sym, desc, qty, price, gross, comm, net, cur,
             acct, act, atype]
    return ','.join(f'"{c}"' if ',' in c else c for c in cells) + '\n'


def qrei(td, cur, qty, net, acct=ACCT, sym='SYN'):
    return qrow(td=td, action='REI', sym=sym, desc='SYN CORP REINV@C$10',
                qty=str(qty), price='10', gross='0', comm='0', net=str(net),
                cur=cur, acct=acct, act='Dividend reinvestment')


def qt_parse(files, taxable=None, combined=False, country=None):
    """Parse {name: body} as ONE account, the way taxjson-brokerage does
    (one shared context, one parser per file, names in order)."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, body in sorted(files.items()):
            p = Path(d) / name
            p.write_text(QH + body, encoding='utf-8')
            paths.append(p)
        err = io.StringIO()
        txs = []
        with contextlib.redirect_stderr(err):
            ctx = QuestradeBrokerage.prepare_files(paths)
            for p in paths:
                par = QuestradeBrokerage()
                par.account_context = ctx
                par.country = country
                par.combined_broker_accounts = combined
                if taxable is not None:
                    par.account_taxable = taxable
                txs.extend(par.parse_file(p))
    return txs, err.getvalue()


def _buys(txs):
    return sorted((t['date'], t['symbol'], t['currency'], t['quantity'])
                  for t in txs if t.get('action') == 'BUYSELL')
