"""Shared test helpers: the engine's gains for an opening short.

Shared from: test_fix_1029_r2_review
"""


def _short_cover_gains(country):
    """The engine's gains for an opening short of unknown proceeds (10
    SYN.US, before the data) covered on 2025-06-02 for 500."""
    import contextlib
    import io
    from taxjson.model.transaction import TaxTransaction as T
    from taxjson.lib.pipeline import GainsRequest, run_gains
    cur = "CAD" if country == "canada" else "USD"

    def tx(i, a, d, q, n=0.0, sym="SYN.US"):
        return T(id=i, action=a, date=d, date_settle=d, symbol=sym,
                 quantity=q, net_amount=n, currency=cur, account="margin")
    with contextlib.redirect_stderr(io.StringIO()):
        return run_gains(
            [tx("open", "OPENING_BALANCE", "2024-01-02", -10),
             tx("cover", "BUYSELL", "2025-06-02", 10, -500.0),
             tx("b", "BUYSELL", "2025-01-06", 5, -50.0, "ZZQ.US"),
             tx("s", "BUYSELL", "2025-02-03", -5, 60.0, "ZZQ.US")],
            req=GainsRequest(country=country, year=2025, taxable=True,
                             phantom_hint=False))
