"""Shared test helpers: the self-cancelling transfer dropper and its rows.

Shared from: test_mutation_pins
"""
import io
from contextlib import redirect_stderr

from taxjson.model.transaction import TaxTransaction
from taxjson.lib.pipeline import _drop_self_cancelling_transfers


def _tx(action, date, symbol, qty, net=0.0, account='RRSP',
        currency='CAD', **extra):
    return TaxTransaction(action=action, date=date, symbol=symbol,
                          quantity=qty, net_amount=net, account=account,
                          currency=currency, **extra)


def _drop(shel, main=None):
    """Run the dropper capturing stderr; returns (out, dropped, err)."""
    err = io.StringIO()
    with redirect_stderr(err):
        out, dropped = _drop_self_cancelling_transfers(
            shel, main_transactions=main)
    return out, dropped, err.getvalue()
