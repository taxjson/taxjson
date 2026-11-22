"""Shared test helpers: option assignment rows and both engines' runs.

Shared from: test_fix_a2_engine_assign
"""
import contextlib
import io

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction


def _tx(action, date, symbol, qty, price, net, *, time='10:00:00',
        acct='margin', mult=0.0, cur=None):
    return TaxTransaction(
        action=action, date=date, date_settle=date, time=time,
        symbol=symbol, quantity=float(qty), price=float(price),
        net_amount=float(net),
        currency=cur or ('CAD' if symbol.endswith('.TO') else 'USD'),
        account=acct, multiplier=mult)


def _run(engine, book, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = engine().compute_gains(book, **kw)
    return res, err.getvalue()


def _held_cost(res, sym):
    return round(sum(h['total_cost'] for h in res['inventory']
                     if h['symbol'] == sym), 2)


_ENGINES = (('canada', CanadaTaxRules, 'TO'), ('usa', USATaxRules, 'US'))
