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
# One home for the settlement-lag convention. Equities settled T+3 until
# 2017-09-05 (US and Canada moved together), T+2 until the 2024 cutover
# (US 2024-05-28; Canada 2024-05-27, a TSX trading day the US spent
# closed for Memorial Day) and T+1 since; other markets per _T1_CUTOVER;
# options are T+1 in every era.
# The lag is counted in SETTLEMENT days of the trade's market
# (lib/market_calendar: US = NYSE + Federal Reserve holidays, Canada =
# TSX + Remembrance Day + Truth and Reconciliation), so a holiday inside
# the lag moves the settle date later, as the clearing houses do.

T3_TO_T2 = '2017-09-05'

# The T+1 cutovers, by trade currency (standing in for the market: the
# callers key the calendar on currency). North America moved in May
# 2024 (Mexico with Canada); the UK, the EU and Switzerland move on
# 2027-10-11. Every other market (the ASX, Hong Kong, Japan, ...) is
# T+2 (audit G5-0: LSE and ASX fills settled on the US T+1 cycle, so a
# sale on the second-to-last trading day landed in the wrong year).
_T1_CUTOVER = {'USD': '2024-05-28', 'CAD': '2024-05-27',
               'MXN': '2024-05-27', 'GBP': '2027-10-11',
               'EUR': '2027-10-11', 'CHF': '2027-10-11'}


def settlement_lag_days(trade_iso: str, currency: str = 'USD',
                        is_option: bool = False) -> int:
    """Settlement days between trade and settlement for a trade on
    `trade_iso` (YYYY-MM-DD) in the given market."""
    if is_option:
        return 1
    if trade_iso < T3_TO_T2:
        return 3
    cur = (currency or '').upper() or 'USD'
    cutover = _T1_CUTOVER.get(cur)
    if cutover is None:
        return 2
    return 2 if trade_iso < cutover else 1


# A Canadian listing's suffix (TSX, TSX-V, CSE, NEO / Cboe Canada).
CA_LISTING_SUFFIXES = (".TO", ".V", ".CN", ".NE", ".VN")


def listing_market_currency(symbol: str, fallback=None):
    """The settlement calendar of a symbol's LISTING, as the currency key
    the calendar helpers take: 'CAD' for a Canadian listing (an option on
    one included), 'USD' for a US one (and an F:/'/' futures contract) —
    whatever currency the trade is priced in. A TSX USD-class unit such
    as DLR.U.TO settles through CDS on the Canadian calendar, and an
    AEM.US sale priced in CAD on the US one (audit A2-0375 / A2-1183).
    `fallback` for any other symbol."""
    s = str(symbol or "").strip().upper()
    if s.endswith(CA_LISTING_SUFFIXES):
        return 'CAD'
    if s.endswith(".US") or s.startswith(("F:", "/", "\\")):
        return 'USD'
    return fallback


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
