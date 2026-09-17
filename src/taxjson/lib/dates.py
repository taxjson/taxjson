"""UTC-noon epoch date helpers shared by the wash radar and the
safe-to-sell audit.

Noon UTC (matching Perl's timegm(0, 0, 12, ...)) is load-bearing: with
local timestamps a 30-day window that spans a DST transition is 30d±1h
of epoch time, so `days_since <= 30` flips a day early/late at the
window edge — the radar printed "CLEAR: safe to sell" on day 30 across
the November fall-back. UTC noons are exactly 86400s apart, making day
arithmetic exact. These lived as private copies in both bins — the
same drift class as the radar's old private sort ladder (the repo's
highest fix-ratio failure mode).
"""
from datetime import datetime, timedelta, timezone


def date_to_epoch(date_str: str) -> float:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.replace(hour=12, tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


def date_time_to_epoch(date_str: str, time_str: str) -> float:
    try:
        dt_str = f"{date_str} {time_str}"
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return date_to_epoch(date_str)


def epoch_to_date(epoch: float) -> str:
    """Format an epoch produced by date_to_epoch back to YYYY-MM-DD. Must be
    UTC to round-trip (local fromtimestamp would shift the date for TZs east
    of UTC+12/west of UTC-11 and mislabel deadlines)."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")


def noon_utc(dt: datetime) -> datetime:
    """Normalize any datetime to noon UTC on its calendar date — the
    same basis as date_to_epoch, so every days_since is a whole number."""
    return dt.replace(hour=12, minute=0, second=0, microsecond=0,
                      tzinfo=timezone.utc)


# ---------------------------------------------------------- settlement
# One home for the settlement-lag convention. Equities settled T+2 until
# the 2024 cutover (US 2024-05-28; Canada 2024-05-27, a TSX trading day
# the US spent closed for Memorial Day) and T+1 since; options are T+1
# in every era. Weekends are skipped; exchange holidays are NOT modeled
# (documented limitation — a holiday inside the lag makes the true
# settle date one day LATER, so callers deriving a "last safe trade
# date" should treat these as the optimistic bound).

def settlement_lag_days(trade_iso: str, currency: str = 'USD',
                        is_option: bool = False) -> int:
    """Business days between trade and settlement for a trade on
    `trade_iso` (YYYY-MM-DD) in the given market."""
    if is_option:
        return 1
    cutover = ('2024-05-27' if (currency or '').upper() == 'CAD'
               else '2024-05-28')
    return 2 if trade_iso < cutover else 1


def settlement_date(trade_iso: str, currency: str = 'USD',
                    is_option: bool = False) -> str:
    """Era-aware settlement date for a trade dated `trade_iso`;
    returns the input unchanged when it does not parse."""
    try:
        dt = datetime.strptime(trade_iso, "%Y-%m-%d")
    except (TypeError, ValueError):
        return trade_iso
    added = 0
    days = settlement_lag_days(trade_iso, currency, is_option)
    while added < days:
        dt += timedelta(days=1)
        if dt.weekday() < 5:
            added += 1
    return dt.strftime("%Y-%m-%d")


def last_trade_date_settling_by(deadline_iso: str, currency: str = 'USD',
                                is_option: bool = False) -> str:
    """The LAST trade date (a weekday) whose settlement lands on or
    before `deadline_iso`. The superficial-loss rescue test is on the
    SETTLE date (core.py: end_window_date = loss_settle + 30, rescue
    sale's sort/settle date <= that), so a deadline quoted as a settle
    date must be walked back through the lag — and past a weekend
    deadline — before it is safe to hand to a user as "sell by".
    Returns the input unchanged when it does not parse."""
    try:
        dt = datetime.strptime(deadline_iso, "%Y-%m-%d")
    except (TypeError, ValueError):
        return deadline_iso
    for _ in range(14):        # lag <= 2 business days, plus weekends
        iso = dt.strftime("%Y-%m-%d")
        if dt.weekday() < 5 and settlement_date(
                iso, currency, is_option) <= deadline_iso:
            return iso
        dt -= timedelta(days=1)
    return dt.strftime("%Y-%m-%d")
