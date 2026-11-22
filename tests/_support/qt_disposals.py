"""Shared test helpers: a Questrade buy and its disposal rows.

Shared from: test_fix_1029_r7d_rbcqt
"""
from _support.parser_qt_rbc_rows import q


def qt_dis(child, n, name=None, sym=None):
    name = name or f'{child} CORP'
    return q(td='2025-08-25', action='DIS', sym=child if sym is None else sym,
             desc=f'{name} SPINOFF ON 100 SHS FROM SEC# K000001 ALPHA CORP '
                  'REC 08/21/25 PAY 08/25/25',
             qty=str(n), price='0', gross='0', comm='0', net='0', cur='CAD',
             act='Dividends', atype='Cash')


QT_BUY = q(td='2025-03-03', action='Buy', sym='ALPH',
           desc='ALPHA CORP WE ACTED AS AGENT', qty='100', price='1',
           gross='-100', comm='0', net='-100', cur='CAD', atype='Cash')
