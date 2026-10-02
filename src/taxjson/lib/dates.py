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
# One home for the settlement-lag convention, per market (keyed on the
# trade currency, which stands in for the market):
#   T+3 -> T+2: North America 2017-09-05 (US and Canada together; Mexico
#     kept with them); the UK, the EU markets and Switzerland 2014-10-06;
#     Australia and New Zealand 2016-03-07; Singapore 2018-12-10; Japan
#     2019-07-16; Hong Kong has been T+2 throughout. Any other market
#     follows the North-American dates (not researched market by market).
#     (Audit A2-0704: every non-North-American market inherited the US
#     T+3 era before 2017, so a late-December 2016 LSE/ASX sale landed in
#     2017; A2-1195: Tokyo got T+2 two years early.)
#   T+2 -> T+1: per _T1_CUTOVER — US 2024-05-28; Canada and Mexico
#     2024-05-27 (a TSX trading day the US spent closed for Memorial
#     Day); the UK, the EU markets and Switzerland 2027-10-11 (A2-0705:
#     every EU currency, not only the euro); every other market stays
#     T+2.
# Options are T+1 in every era.
# The lag is counted in SETTLEMENT days of the trade's market
# (lib/market_calendar: US = NYSE + Federal Reserve holidays, Canada =
# TSX + Remembrance Day + Truth and Reconciliation; elsewhere weekends
# only), so a holiday inside the lag moves the settle date later, as the
# clearing houses do.

T3_TO_T2 = '2017-09-05'

# Currencies of the EU markets outside the euro area (they move with the
# euro markets: one EU regulation, CSDR).
_EU_NON_EURO = ('SEK', 'DKK', 'PLN', 'CZK', 'HUF', 'RON', 'BGN')

# The T+3 -> T+2 move, by trade currency ('' = T+2 throughout).
_T2_CUTOVER = {'USD': T3_TO_T2, 'CAD': T3_TO_T2, 'MXN': T3_TO_T2,
               'GBP': '2014-10-06', 'EUR': '2014-10-06',
               'CHF': '2014-10-06', 'NOK': '2014-10-06',
               **{c: '2014-10-06' for c in _EU_NON_EURO},
               'AUD': '2016-03-07', 'NZD': '2016-03-07',
               'SGD': '2018-12-10', 'JPY': '2019-07-16', 'HKD': ''}

# The T+1 cutovers, by trade currency. North America moved in May
# 2024 (Mexico with Canada); the UK, the EU and Switzerland move on
# 2027-10-11. Every other market (the ASX, Hong Kong, Japan, ...) is
# T+2 (audit G5-0: LSE and ASX fills settled on the US T+1 cycle, so a
# sale on the second-to-last trading day landed in the wrong year).
_T1_CUTOVER = {'USD': '2024-05-28', 'CAD': '2024-05-27',
               'MXN': '2024-05-27', 'GBP': '2027-10-11',
               'EUR': '2027-10-11', 'CHF': '2027-10-11',
               **{c: '2027-10-11' for c in _EU_NON_EURO}}


def settlement_lag_days(trade_iso: str, currency: str = 'USD',
                        is_option: bool = False) -> int:
    """Settlement days between trade and settlement for a trade on
    `trade_iso` (YYYY-MM-DD) in the given market."""
    if is_option:
        return 1
    cur = (currency or '').upper() or 'USD'
    if trade_iso < _T2_CUTOVER.get(cur, T3_TO_T2):
        return 3
    cutover = _T1_CUTOVER.get(cur)
    if cutover is None:
        return 2
    return 2 if trade_iso < cutover else 1


def settlement_date(trade_iso: str, currency: str = 'USD',
                    is_option: bool = False) -> str:
    """Era- and holiday-aware settlement date for a trade dated
    `trade_iso`; returns the input unchanged when it does not parse."""
    from taxjson.lib.market_calendar import add_settlement_days
    try:
        datetime.strptime(trade_iso, "%Y-%m-%d")
    except (TypeError, ValueError):
        return trade_iso
    days = settlement_lag_days(trade_iso, currency, is_option)
    return add_settlement_days(trade_iso, days, currency).isoformat()


def last_trade_date_settling_by(deadline_iso: str, currency: str = 'USD',
                                is_option: bool = False) -> str:
    """The LAST trading day whose settlement lands on or before
    `deadline_iso`. The superficial-loss rescue test is on the SETTLE
    date (core.py: end_window_date = loss_settle + 30, rescue sale's
    sort/settle date <= that), so a deadline quoted as a settle date
    must be walked back through the lag, past weekends and holidays,
    before it is safe to hand to a user as "sell by".
    Returns the input unchanged when it does not parse."""
    from taxjson.lib.market_calendar import is_trading_day
    try:
        dt = datetime.strptime(deadline_iso, "%Y-%m-%d")
    except (TypeError, ValueError):
        return deadline_iso
    for _ in range(21):        # lag <= 3 settlement days, plus holidays
        iso = dt.strftime("%Y-%m-%d")
        if is_trading_day(iso, currency) and settlement_date(
                iso, currency, is_option) <= deadline_iso:
            return iso
        dt -= timedelta(days=1)
    return dt.strftime("%Y-%m-%d")
