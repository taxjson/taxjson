"""Shared test helpers: the engine over .tt rows, by year.

Shared from: test_fix_m_engine
"""
import contextlib
import io

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


def _by_year(result):
    out = {}
    for r in result['transactions']:
        y = (r.get('date_settle') or r['date'])[:4]
        out[y] = round(out.get(y, 0.0) + r['gain'], 2)
    return out


def _run(engine, txs, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = engine.compute_gains(txs, **kw)
    return res, err.getvalue()
