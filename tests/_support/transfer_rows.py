"""Shared test helpers: a transaction row for transfer handling.

Shared from: test_transfer_handling
"""
from taxjson.model.transaction import TaxTransaction


def _tx(action, date, symbol, qty, price=0.0, net=0.0, account='Margin',
        currency='USD', **extra):
    return TaxTransaction(
        action=action, date=date, symbol=symbol, quantity=qty,
        price=price, net_amount=net or abs(qty * price),
        account=account, currency=currency, **extra,
    )
