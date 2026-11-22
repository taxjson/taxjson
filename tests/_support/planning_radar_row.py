"""Shared test helpers: one row for the wash radar.

Shared from: test_fix_a2_pins1_02_radar
"""


def _row(d, sym, qty, px, acct="margin", action="BUYSELL", settle=None,
         currency="USD", rid=None, net=None, **kw):
    r = {"action": action, "date": d, "date_settle": settle or d,
         "time": "10:00:00", "symbol": sym, "quantity": qty, "price": px,
         "net_amount": (-qty * px if net is None else net),
         "currency": currency, "account": acct, "commission": 0.0}
    if rid:
        r["id"] = rid
    r.update(kw)
    return r
