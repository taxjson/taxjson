"""Shared test helpers: small Canadian books for the ACB pool's operations.

One account's rows as the engine reads them (a trade, a rename, a return
of capital, a leg of a move between two of the person's accounts, a
listing journal's two legs, an option contract's row), the engine's run
over them, and what the pins read back: each sale's record and each
holding's inventory row. The two unit calls at the end reach states no
book reaches (backlog TJ-4242): they build the pool the walk builds and
call the walk's own function, so the pool's representation is named here
alone (PLAN step 13 changes it). Synthetic data only.

Shared from: PLAN step 13's pool pins
"""
import contextlib
import io
from decimal import Decimal

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.lib.row_types import LISTING_JOURNAL_TYPE, LOT_MOVE_TYPE
from taxjson.model.transaction import TaxTransaction

SYM = 'SYQ.TO'


def trade(date, qty, net, *, sym=SYM, account='b', action='BUYSELL',
          currency='CAD', **kw):
    """A purchase (qty > 0) or sale of `qty` units for `net` dollars; action
    'OPENING_BALANCE' for units held from before the data."""
    return TaxTransaction(
        action=action, date=date, date_settle=date, time='10:00:00',
        symbol=sym, quantity=float(qty),
        price=abs(net / qty) if qty else 0.0, net_amount=float(net),
        currency=currency, account=account, **kw)


def contract(date, qty, net, sym, *, account='b', action='BUYSELL'):
    """An option row: `qty` contracts (negative: written or sold) for
    `net` dollars; action 'ASSIGN' for an assignment or exercise."""
    return TaxTransaction(
        action=action, date=date, date_settle=date, time='10:00:00',
        symbol=sym, quantity=float(qty),
        price=abs(net / qty) / 100 if qty else 0.0, net_amount=float(net),
        currency='CAD', account=account)


def rename(date, old, new, ratio=1.0, *, account='b'):
    """A SPLIT row: `old` becomes `new` (the same symbol: a plain split)."""
    return TaxTransaction(
        action='SPLIT', date=date, date_settle=date, time='09:00:00',
        symbol=old, symbol_new=new if new != old else '', quantity=ratio,
        net_amount=0.0, currency='CAD', account=account)


def return_of_capital(date, amount, *, sym=SYM, account='b', currency='CAD'):
    """A return of capital of `amount` dollars (an ADJUST of -amount)."""
    return TaxTransaction(
        action='ADJUST', type='roc', date=date, date_settle=date,
        time='10:00:00', symbol=sym, quantity=0.0, price=0.0,
        net_amount=-float(amount), currency=currency, account=account)


def own_move(date, qty, frm, to, *, sym=SYM, account='b', n=1):
    """This account's leg of a move of `qty` units (negative: out) between
    two of the person's taxable accounts, as pipeline/stages writes it."""
    return TaxTransaction(
        action='TRANSFER', type=LOT_MOVE_TYPE, date=date, date_settle=date,
        time='12:00:00', symbol=sym, quantity=float(qty), price=0.0,
        net_amount=0.0, currency='CAD', account=account,
        description=f'own-account move #{n}: {frm} -> {to}')


def journal(date, qty, out_sym, in_sym, *, account='b', pair='J1',
            currencies=('CAD', 'CAD'), rate=0.0):
    """The two legs of a broker's journal of `qty` units from one listing
    to the other, both in the book's currency; or, a book kept in each
    listing's own currency, the out-leg's and the in-leg's `currencies`
    and the `rate` on the in-leg (the out-leg's currency in the in-leg's)."""
    def leg(sym, q, currency, price):
        return TaxTransaction(
            action='TRANSFER', type=LISTING_JOURNAL_TYPE, date=date,
            date_settle=date, time='12:00:00', symbol=sym, quantity=float(q),
            price=price, net_amount=0.0, currency=currency, account=account,
            journal_pair=pair)
    return [leg(out_sym, -qty, currencies[0], 0.0), leg(in_sym, qty, currencies[1], rate)]


def denied_loss(sym=SYM, account='b', currency='CAD'):
    """100 units bought for 1000, sold for 500 and bought back two days
    later for 500: the 500 loss is denied (CA-SL-01) and added to the new
    shares' ACB (CA-SL-09), so 100 units are held at 1000, 500 of it the
    denied loss."""
    return [trade('2025-01-06', 100, 1000, sym=sym, account=account, currency=currency),
            trade('2025-02-03', -100, 500, sym=sym, account=account, currency=currency),
            trade('2025-02-05', 100, 500, sym=sym, account=account, currency=currency)]


def run_canada(rows, **kw):
    """(result, stderr) of the Canadian engine over `rows`."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = CanadaTaxRules().compute_gains(list(rows), **kw)
    return res, err.getvalue()


def sale(res, date, sym=SYM):
    """The one record with units dated `date` on `sym`."""
    (rec,) = [r for r in res['transactions']
              if r.get('qty') and r['date'] == date and r['symbol'] == sym]
    return rec


def holding(res, sym=SYM, account=None):
    """The inventory row of `sym` (of `account`'s short beside a long)."""
    (row,) = [i for i in res['inventory'] if i['symbol'] == sym
              and (account is None or i.get('account') == account)]
    return row


def flat_pool(parked=None):
    """A pool as the walk first makes it (no units, no cost); with `parked`,
    a denied loss's addition waiting on it (parked, and tallied in the
    denied losses, as `_walk_adjust` does on a flat pool)."""
    from taxjson.engine.ca.pool import AcbPool
    pool = AcbPool.new('CAD')
    if parked is not None:
        pool.park(Decimal(parked))
        pool.tally_denied(Decimal(parked))
    return pool


def pool_of(qty=0.0, cost=0, denied=0, parked=0, currency=''):
    """A pool holding `qty` units (negative: a short) for `cost` (a short's:
    its entry proceeds), `denied` of denied losses in it and `parked` of
    them waiting on it, made by the pool's own operations: for a test of
    what reads a pool (the reports' views, the conservation check)."""
    from taxjson.engine.ca.pool import AcbPool
    pool = AcbPool.new(currency)
    if qty:
        pool.open(float(qty), Decimal(str(cost)), date='2025-01-06', settle='2025-01-06')
    elif cost:
        pool.add_cost(Decimal(str(cost)))
    if denied:
        pool.tally_denied(Decimal(str(denied)))
    if parked:
        pool.park(Decimal(str(parked)))
    return pool


def pool_fields(pool):
    """Every field of a pool, by name (the class reads them as attributes)."""
    return {slot[1:]: getattr(pool, slot[1:]) for slot in type(pool).__slots__}


def open_with_parked(parked, qty):
    """The cost (a short's: its entry proceeds) of a flat pool holding a
    denied loss's `parked` addition once a position of `qty` units opens
    on it with no money of its own."""
    pool = flat_pool(parked)
    pool.take_pending(float(qty))
    return pool.total_cost, pool.pending_wash or None


def trade_with_parked(parked, qty, cost):
    """The cost (a short's: its entry proceeds) of a flat pool holding a
    denied loss's `parked` addition once a trade of `qty` units for `cost`
    opens a position on it."""
    pool = flat_pool(parked)
    pool.open(float(qty), Decimal(cost), date='2025-03-03', settle='2025-03-03')
    return pool.total_cost, pool.pending_wash or None


def move_beyond_with_parked(parked, qty):
    """The cost (a short's: its entry proceeds) of a flat pool holding a
    denied loss's `parked` addition once `qty` units with no purchase in
    the book (negative: units out past the holding, the pool short by
    them) open a position on it."""
    pool = flat_pool(parked)
    pool.move_beyond(float(qty), '2025-03-03', restart=True)
    return pool.total_cost, pool.pending_wash or None


def journal_short_into_parked(parked, qty, proceeds):
    """The entry proceeds of a flat pool holding a denied loss's `parked`
    addition once a short of `qty` units, opened for `proceeds` on another
    listing, is journaled to it."""
    src = pool_of(-float(qty), proceeds)
    dst = flat_pool(parked)
    cost, dw, cut = src.move_out(float(qty), 'fraction', -1.0)
    dst.move_in_from(src, float(qty), -1.0, cost, dw, cut, Decimal(1), 1e-9)
    return dst.qty, dst.total_cost, dst.pending_wash or None


def rename_parked_into(parked, qty, cost):
    """The cost of the target pool (`qty` units at `cost`; a short's cost
    is its entry proceeds) after a rename merges into it a flat source
    pool holding a denied loss's `parked` addition."""
    target = flat_pool()
    if qty:
        target.open(float(qty), Decimal(cost), date='2025-01-06', settle='2025-01-06')
    source = flat_pool(parked)
    target.merge(source, 'OLD.TO', 'NEW.TO')
    return target.total_cost, target.pending_wash or None
