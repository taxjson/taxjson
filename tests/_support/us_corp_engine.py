"""Shared test helpers: the US engine over spin-off and boot rows.

Shared from: test_engine_check_pins_us_corp
"""
import contextlib
import io

from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.corp_actions import CorporateAction, resolve_event


def _engine(book):
    """The US engine over `book`, per-account basis, every lot at the end
    of 2025 (`lots_at`); stderr captured as `_stderr`, on one line."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = USATaxRules().compute_gains(list(book), per_account_basis=True,
                                        lots_at='2025-12-31')
    r['_stderr'] = ' '.join(err.getvalue().split())
    return r


def _spin_rows(alloc, qty_received, record='', ratio_old=2, account='b'):
    """A §355 spin-off of QZSP from QZRX on 2025-04-01 (the resolver's
    BUYSELL + parent ADJUST pair), paid 1 for `ratio_old`: with a record
    date, the shares paid on are qty_received x ratio_old."""
    ev = CorporateAction(
        date='2025-04-01', time='09:30:00', action_type='spinoff',
        source_symbol='QZRX', source_isin='', target_symbol='QZSP',
        target_isin='', ratio_new=1, ratio_old=ratio_old, qty_disposed=0.0,
        qty_received=qty_received, fmv=0.0, currency='USD',
        target_currency='USD', account=account, event_id='ev-qzsp',
        record_date=record)
    with contextlib.redirect_stderr(io.StringIO()):
        rows = resolve_event(ev, 'tax_free_355', country='usa',
                             hints={'allocated_acb': alloc})
    return [TaxTransaction(**r) for r in rows]


def _boot_rows(give, get, boot, fmv_per_share, date='2025-06-20'):
    """A §356 exchange in 'b' of `give` QZRX for `get` QZNW plus `boot`
    cash (the resolver's two reorg_356 rows; a fraction of `get` is
    dropped, its cash entered as a sale of its own)."""
    ev = CorporateAction(
        date=date, time='09:30:00', action_type='merger',
        source_symbol='QZRX', source_isin='', target_symbol='QZNW',
        target_isin='', ratio_new=1, ratio_old=2, qty_disposed=give,
        qty_received=get, fmv=0.0, currency='USD', target_currency='USD',
        account='b', event_id='ev-qznw')
    with contextlib.redirect_stderr(io.StringIO()):
        rows = resolve_event(ev, 'reorg_368_boot', country='usa',
                             hints={'cash_boot': boot,
                                    'fmv_per_share': fmv_per_share})
    return [TaxTransaction(**r) for r in rows]
