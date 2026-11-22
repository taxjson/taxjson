"""Shared test helpers: Canadian engine runs over .tt books.

Shared from: test_fix_1029_r7_coreca
"""
import contextlib
import io

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction


def _tt(content, account='margin'):
    """`ACTION date time symbol qty cur price net [fee] [settle]` rows."""
    out = []
    for line in content.strip().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        p = line.split()
        kw = dict(action=p[0], date=p[1], time=p[2], symbol=p[3],
                  quantity=float(p[4]), currency=p[5], price=float(p[6]),
                  net_amount=float(p[7]), account=account)
        if len(p) > 8:
            kw['fee'] = float(p[8])
        if len(p) > 9:
            kw['date_settle'] = p[9]
        out.append(TaxTransaction(**kw))
    return out


def _run(txs, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = CanadaTaxRules().compute_gains(txs, **kw)
    return res, err.getvalue()


def _split(date, old, new, ratio, account):
    return TaxTransaction(action='SPLIT', date=date, time='09:30:00',
                          symbol=old, symbol_new=new, quantity=ratio,
                          account=account, currency='CAD')


def _fut(i, date, qty, net, account='a', sym='F:SYQH6.US'):
    return TaxTransaction(id=i, action='BUYSELL', date=date,
                          date_settle=date, time='10:00:00', symbol=sym,
                          quantity=qty, net_amount=net, currency='CAD',
                          account=account)


def _settled(book):
    from taxjson.lib.futures import settle_futures
    out = []
    for a in sorted({t.account for t in book}):
        out += settle_futures([t for t in book if t.account == a],
                              'average')[0]
    return out
