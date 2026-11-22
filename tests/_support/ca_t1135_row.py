"""Shared test helpers: a transaction row for the T1135.

Shared from: test_t1135
"""


def tx(action="BUYSELL", date="2025-01-15", symbol="AAPL.US", qty=0.0,
       net=0.0, symbol_new="", time="09:30:00", **extra):
    d = {
        "action": action, "date": date, "date_settle": date, "time": time,
        "symbol": symbol, "quantity": qty, "net_amount": net,
        "symbol_new": symbol_new, "currency": "CAD",
    }
    d.update(extra)
    return d
