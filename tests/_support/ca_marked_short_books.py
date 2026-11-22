"""Shared test helpers: a rename's OLD.TO and NEW.TO rows, an account's long side across the rename, a marked short of NEW.TO and the realized rows of a run.

Shared from: test_marked_short_denial
"""
from taxjson.model.transaction import TaxTransaction


OLD, NEW = 'OLD.TO', 'NEW.TO'


def _tr(date, sym, qty, net, acct='b', oc=''):
    return TaxTransaction(action='BUYSELL', date=date, date_settle=date,
                          time='10:00:00', symbol=sym, quantity=qty,
                          price=abs(net / qty), net_amount=net,
                          currency='CAD', account=acct, open_close=oc)


def _rename(date, old, new):
    return TaxTransaction(action='SPLIT', date=date, date_settle=date,
                          time='09:00:00', symbol=old, symbol_new=new,
                          quantity=1.0, net_amount=0.0, currency='CAD',
                          account='b')


def _chain(new_buy_date='2025-03-26', new_oc=''):
    """Account b's long side: the OLD.TO loss and rebuy, the NEW.TO
    purchase (inside the rename's split window), the rename, the sale of
    the NEW.TO shares."""
    return [_tr('2025-01-02', OLD, 100, 2000),
            _tr('2025-03-03', OLD, -100, 1000),       # a loss of 1000
            _tr('2025-03-08', OLD, 100, 1000),        # the trigger,
            _tr('2025-03-13', OLD, -100, 1000),       # sold again
            _tr(new_buy_date, NEW, 100, 1000, oc=new_oc),   # held at day 30
            _rename('2025-04-01', OLD, NEW),
            _tr('2025-06-02', NEW, -100, 1200)]


def _short(open_date='2025-01-10', acct='m', cover=True):
    """A marked short of NEW.TO: opened coded O, covered coded C."""
    rows = [_tr(open_date, NEW, -100, 1000, acct=acct, oc='O')]
    if cover:
        rows.append(_tr('2026-01-10', NEW, 100, 1000, acct=acct, oc='C'))
    return rows


def _records(res):
    """(date, symbol, account, gain, denied) of every realized row."""
    return sorted((e['date'], e['symbol'], e.get('account'),
                   round(e['gain'], 2),
                   round(e.get('disallowed_amount') or 0.0, 2))
                  for e in res['transactions'] if 'gain' in e)
