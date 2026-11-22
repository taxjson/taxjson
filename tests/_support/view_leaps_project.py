"""Shared test helpers: a LEAPS option, its gain and a project.

Shared from: test_leaps_view
"""
import json
from pathlib import Path


def tx(action="BUYSELL", date="2025-01-10", symbol="AAPL.US", qty=0.0,
       net=0.0, currency="USD"):
    return {"action": action, "date": date, "date_settle": date,
            "time": "09:30:00", "symbol": symbol, "quantity": qty,
            "net_amount": net, "currency": currency}


LEAP = "AAPL270115C00150000.US"        # bought >3mo out → qualifies
NEAR = "AAPL250221C00150000.US"        # bought <3mo out → excluded
SHORT = "AAPL270115P00120000.US"       # only ever sold short → excluded

HISTORY = [
    # Qualifying entry OUTSIDE the 2025 tax year (2024) — its 2025 exit
    # must still appear in the window views.
    tx(date="2024-12-15", symbol=LEAP, qty=2, net=2400.0),
    tx(date="2025-06-02", symbol=LEAP, qty=-1, net=1900.0),
    # Near-dated long buy: 2025-01-10 → 2025-02-21 expiry is under 3 months.
    tx(date="2025-01-10", symbol=NEAR, qty=1, net=300.0),
    # Short premium: never a long buy, must not qualify.
    tx(date="2025-01-10", symbol=SHORT, qty=-1, net=500.0),
    # Plain shares: never in a leaps view.
    tx(date="2025-03-01", symbol="AAPL.US", qty=100, net=20000.0),
]

# Engine-shaped disposition entries (base currency, lot-matched).
LEAP_GAIN = {"symbol": LEAP, "date": "2025-06-02",
             "date_settle": "2025-06-03", "qty": -1,
             "proceeds": 2565.0, "cost": 1620.0, "gain": 945.0,
             "raw_gain": 945.0, "disallowed_amount": 0.0,
             "days_held": 169, "direction": "LONG", "account": "margin"}


def _project(tmp, gains=None, wash_gains=None):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\n\n[accounts.margin]\ntype = "taxable"\n')
    work = root / "work"
    work.mkdir()
    (work / "margin_raw.json").write_text(
        json.dumps({"transactions": HISTORY}))
    if gains is not None:
        (work / "margin_gains.json").write_text(
            json.dumps({"transactions": gains}))
    if wash_gains is not None:
        (work / "margin_gains_wash.json").write_text(
            json.dumps({"transactions": wash_gains}))
    return root
