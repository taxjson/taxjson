"""Shared test helpers: a US gains entry for form export.

Shared from: test_form_export
"""


def us_entry(symbol="AAPL.US", date="2025-05-02", qty=-100, proceeds=12000.0,
             cost=10000.0, gain=None, disallowed=0.0, days_held=200,
             term="SHORT_TERM", direction="LONG", **extra):
    e = {
        "date": date, "date_settle": date, "symbol": symbol, "qty": qty,
        "proceeds": proceeds, "cost": cost,
        "gain": (proceeds - cost + disallowed) if gain is None else gain,
        "raw_gain": proceeds - cost,
        "disallowed_amount": disallowed, "days_held": days_held,
        "term": term, "direction": direction, "commission": 0.0, "fee": 0.0,
        "account": "margin", "is_option": False,
    }
    e.update(extra)
    return e
