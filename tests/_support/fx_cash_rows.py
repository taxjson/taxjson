"""Shared test helpers: foreign cash rows and their rates.

Shared from: test_fix_a2_fx_cash
"""


_RATES = {"2025-10-10": 1.40, "2025-10-13": 1.40, "2025-10-14": 1.40,
          "2026-01-10": 1.30, "2026-02-10": 1.40, "2026-03-10": 1.35}


def _rate_of(cur, d):
    return _RATES.get(d)


def _tx(action, date, cur, net, qty=0.0, **kw):
    return dict(action=action, date=date,
                date_settle=kw.pop("date_settle", date),
                time=kw.pop("time", "10:00:00"),
                symbol=kw.pop("symbol", "AAA.US"), quantity=qty,
                currency=cur, net_amount=net,
                account=kw.pop("account", "crypto"), **kw)
