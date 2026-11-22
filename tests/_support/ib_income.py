"""Shared test helpers: IB dividends, accruals, cash in lieu and split rows.

Shared from: test_fix_l_ibparse
"""
import inspect

from taxjson.model.transaction import TaxTransaction

from _support.ib_statement_rows import HEAD, TRADES_H


def _book(txs):
    valid = set(inspect.signature(TaxTransaction).parameters)
    return [TaxTransaction(**{k: v for k, v in t.items() if k in valid})
            for t in txs]


def _trades(*rows):
    return HEAD + TRADES_H + ''.join(rows)


_ACC_H = ('Change in Dividend Accruals,Header,Asset Category,Currency,'
          'Account,Symbol,Date,Ex Date,Pay Date,Quantity,Tax,Fee,'
          'Gross Rate,Gross Amount,Net Amount,Code\n')
_DIV_H = 'Dividends,Header,Currency,Account,Date,Description,Amount\n'
_PERIOD = 'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'


def _acc(sym, date, ex, pay, qty, rate, gross, code, cur='USD'):
    return (f'Change in Dividend Accruals,Data,Stocks,{cur},U5550001,'  # pii-ok
            f'{sym},{date},{ex},{pay},{qty},0,0,{rate},{gross},{gross},'
            f'{code}\n')


def _div(sym, isin, date, amount, cur='USD', pil=False, rate=None):
    what = ('Payment in Lieu of Dividend' if pil else
            f'Cash Dividend {cur} {rate} per Share')
    return (f'Dividends,Data,{cur},U5550001,{date},'  # pii-ok
            f'"{sym}({isin}) {what}",{amount}\n')


def _pil(txs):
    return [(t['symbol'], t['quantity'], t['price']) for t in txs
            if t['action'] == 'DIVIDEND_IN_LIEU']


_CIL = ('QZX(US0000000QX1) Cash in Lieu of Fractional Shares (QZX, QZX '
        'CORP, US0000000QX1)')


def _split_desc(new, old):
    return (f'QZX(US0000000QX1) Split {new} for {old} (QZX, QZX CORP, '
            f'US0000000QX1)')


def _ratios(txs):
    return [(t['date'], round(t['quantity'], 7)) for t in txs
            if t['action'] == 'SPLIT']
