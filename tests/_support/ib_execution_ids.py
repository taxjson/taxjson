"""Shared test helpers: an IB trades header with trade and execution ids, a fill row and an opening fill.

Shared from: test_fix_1029_r7c_ibparse
"""
from _support.ib_statement_rows import TRADES_H, _trade


_A = 'U1234567'  # pii-ok: synthetic ids


_IDS_H = TRADES_H.rstrip('\n') + ',Trade ID,IB Execution ID\n'


def _fill(q, price, day, ident):
    return _trade('SYN', day, q, price, -q * price, acct=_A, cur='CAD',
                  code='C' if q < 0 else 'O',
                  disc='Trade').rstrip('\n') + f',{ident},{ident}\n'


_OPEN = _fill(20, 10, '2025-01-02, 10:00:00', 'SYN-OPEN')
