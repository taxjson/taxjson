"""Shared test helpers: IB sells, lots and books for the broker basis drafts.

Shared from: test_basis_drafts
"""
from taxjson.model.transaction import TaxTransaction

from _support.ib_statement_rows import HEAD, TRADES_H, _parse_ib


def _row(disc, sym, when, qty, price='', proceeds='', comm='', code='',
         basis='', cur='USD', cat='Stocks'):
    return (f'Trades,Data,{disc},{cat},{cur},U5550001,{sym},"{when}",'  # pii-ok
            f'{qty},{price},0,{proceeds},{comm},{basis},0,0,{code}\n')


def _sell(sym, when, qty, price, basis, code='C', cur='USD'):
    gross = abs(qty) * price
    return _row('Order', sym, when, qty, price, gross, -1, code,
                -basis if basis else '', cur)


def _lot(sym, opened, qty, price, basis, cur='USD'):
    return _row('ClosedLot', sym, f'{opened}, 09:30:00', qty, price, '',
                '', '', basis, cur)


def _parsed(body, account='margin'):
    _, txs, _ = _parse_ib(HEAD + TRADES_H + body)
    for t in txs:
        t['account'] = account
    return txs


def _book(rows):
    keys = TaxTransaction.__dataclass_fields__
    return [TaxTransaction(**{k: v for k, v in r.items() if k in keys})
            for r in rows]


SINGLE = _sell('QZB', '2025-04-01, 10:00:00', -10, 20, 150)
