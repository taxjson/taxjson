"""Shared test helpers: an IB statement of a listing traded under a temporary symbol, its instrument rows and the stamp.

Shared from: test_fix_suggest_quality
"""
from _support.ib_statement_rows import FII_H, HEAD, TRADES_H, _trade


STAMP = '20260626175508'


def _fii(syms, conid, isin, name='QZHN CORP', exch='NYSE'):
    return (f'Financial Instrument Information,Data,Stocks,"{syms}",{name},'
            f'{conid},{isin},,{exch},1,,,COMMON,,\n')


# A contract listed as QZHN and as its temporary symbol 2026...QZHN.
_TEMP_SYMBOL_BODY = (HEAD + TRADES_H
                     + _trade('QZHN', '2026-06-01, 10:00:00', 20, 50, -1000)
                     + _trade(f'{STAMP}QZHN', '2026-06-29, 10:00:00', -5, 55,
                              275, code='C')
                     + FII_H + _fii(f'QZHN, {STAMP}QZHN', '990000777',
                                    'US9990007771'))
