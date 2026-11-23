"""Shared test helpers: dividend rows, positions and the estimate.

Shared from: test_div_estimate
"""


def _div(d, sym, qty, per, acct="margin", cur="CAD", **kw):
    r = {"action": "DIVIDEND", "date": d, "symbol": sym,
         "quantity": qty, "price": per, "gross_amount": qty * per,
         "net_amount": qty * per, "currency": cur, "account": acct}
    r.update(kw)
    return r


def _est(rows, positions, as_of="2026-07-01", months=12, year=2026,
         fx=None, rules=None):
    from taxjson.lib import div_estimate as DE
    return DE.estimate(positions, DE.payments(rows), as_of=as_of,
                       months=months, tax_year=year, base="CAD",
                       fx=fx or (lambda c: None),
                       income_date=rules.income_date if rules else None)


def _pos(sym, qty, acct="margin", group="taxable"):
    return {"account": acct, "group": group, "symbol": sym, "qty": qty}


def _trade(d, sym, qty, acct="margin", cur="CAD", **kw):
    """A buy (qty > 0) or a sale (qty < 0) of `sym` on `d`: what tells the
    estimate the shares were held on a past date."""
    r = {"action": "BUYSELL", "date": d, "symbol": sym, "quantity": qty,
         "currency": cur, "account": acct}
    r.update(kw)
    return (acct, r)
