"""Settlement calendars for the US and Canadian securities markets.

A trade settles a fixed number of SETTLEMENT days after it is made
(T+1 since 2024-05-27/28, T+2 from 2017-09-05, T+3 before). A settlement
day is a weekday on which the clearing house settles: exchange holidays
and, in the US, bank (Federal Reserve) holidays are not settlement days.
The calendars are rule-based, so every year is covered without a table.

US (DTC): weekends, NYSE holidays, and Federal Reserve holidays. The
Fed list adds Columbus Day and Veterans Day, when the exchanges trade
but trades do not settle (FINRA's holiday settlement schedule).
Canada (CDS): weekends, TSX holidays, and the two bank holidays on which
the TSX trades but nothing settles: Remembrance Day and the National Day
for Truth and Reconciliation (from 2021), each moved to the Monday when it
falls on a weekend. Questrade and RBC printed settle dates and the Bank of
Canada's closures agree on both.

Deliberately NOT modelled:
- One-off exchange closures (days of mourning, 2001-09-11, Hurricane
  Sandy). On 2025-01-09 (President Carter) the NYSE was closed but DTC
  settled, and RBC's printed settle dates agree, so such days stay
  settlement days.
- Currencies other than USD and CAD: weekends only.
- The calendar is keyed on a market currency: every parser passes the
  listing's market (lib/dates.market_of: a USD-quoted TSX listing ->
  'CAD', a CAD-settled US stock -> 'USD'); a symbol with no known
  listing suffix falls back to the row currency.
"""
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import FrozenSet, Optional, Union

DateLike = Union[str, date, datetime]


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th (1-based) `weekday` (Mon=0) of the month; n=-1 is the last."""
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _sat_fri_sun_mon(d: date) -> date:
    """NYSE observance: Saturday -> Friday, Sunday -> Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _sun_mon(d: date) -> Optional[date]:
    """Federal Reserve observance: Sunday -> Monday; a Saturday holiday
    is not observed (the Reserve Banks open the Friday before)."""
    if d.weekday() == 6:
        return d + timedelta(days=1)
    if d.weekday() == 5:
        return None
    return d


@lru_cache(maxsize=None)
def nyse_holidays(year: int) -> FrozenSet[date]:
    """NYSE full-day holidays (regular schedule, no one-off closures)."""
    out = set()
    nyd = date(year, 1, 1)
    if nyd.weekday() == 6:
        out.add(nyd + timedelta(days=1))
    elif nyd.weekday() != 5:          # a Saturday New Year is not moved
        out.add(nyd)
    out.add(_nth_weekday(year, 1, 0, 3))            # Martin Luther King Jr.
    out.add(_nth_weekday(year, 2, 0, 3))            # Washington's Birthday
    out.add(_easter(year) - timedelta(days=2))      # Good Friday
    out.add(_nth_weekday(year, 5, 0, -1))           # Memorial Day
    if year >= 2022:
        out.add(_sat_fri_sun_mon(date(year, 6, 19)))  # Juneteenth
    out.add(_sat_fri_sun_mon(date(year, 7, 4)))     # Independence Day
    out.add(_nth_weekday(year, 9, 0, 1))            # Labor Day
    out.add(_nth_weekday(year, 11, 3, 4))           # Thanksgiving
    out.add(_sat_fri_sun_mon(date(year, 12, 25)))   # Christmas
    return frozenset(d for d in out if d.year == year)


@lru_cache(maxsize=None)
def fed_holidays(year: int) -> FrozenSet[date]:
    """Federal Reserve (US bank) holidays, as observed."""
    days = [date(year, 1, 1),
            _nth_weekday(year, 1, 0, 3),
            _nth_weekday(year, 2, 0, 3),
            _nth_weekday(year, 5, 0, -1),
            date(year, 6, 19) if year >= 2021 else None,
            date(year, 7, 4),
            _nth_weekday(year, 9, 0, 1),
            _nth_weekday(year, 10, 0, 2),           # Columbus Day
            date(year, 11, 11),                     # Veterans Day
            _nth_weekday(year, 11, 3, 4),
            date(year, 12, 25)]
    out = set()
    for d in days:
        if d is None:
            continue
        obs = _sun_mon(d)
        if obs is not None:
            out.add(obs)
    return frozenset(out)


@lru_cache(maxsize=None)
def tsx_holidays(year: int) -> FrozenSet[date]:
    """TSX (and CDS settlement) holidays."""
    out = set()

    def weekend_to_monday(d: date) -> date:
        return d + timedelta(days=(7 - d.weekday()) % 7) \
            if d.weekday() >= 5 else d

    out.add(weekend_to_monday(date(year, 1, 1)))    # New Year's Day
    if year >= 2008:
        out.add(_nth_weekday(year, 2, 0, 3))        # Family Day (Ontario)
    out.add(_easter(year) - timedelta(days=2))      # Good Friday
    may24 = date(year, 5, 24)                       # Victoria Day: the
    out.add(may24 - timedelta(days=may24.weekday()))  # Monday before May 25
    out.add(weekend_to_monday(date(year, 7, 1)))    # Canada Day
    out.add(_nth_weekday(year, 8, 0, 1))            # Civic Holiday
    out.add(_nth_weekday(year, 9, 0, 1))            # Labour Day
    out.add(_nth_weekday(year, 10, 0, 2))           # Thanksgiving
    xmas, boxing = date(year, 12, 25), date(year, 12, 26)
    if xmas.weekday() == 5:                         # Sat/Sun -> Mon/Tue
        out.update({date(year, 12, 27), date(year, 12, 28)})
    elif xmas.weekday() == 6:                       # Sun/Mon -> Mon + Tue
        out.update({boxing, date(year, 12, 27)})
    elif xmas.weekday() == 4:                       # Fri/Sat -> Fri + Mon
        out.update({xmas, date(year, 12, 28)})
    else:
        out.update({xmas, boxing})
    return frozenset(out)


@lru_cache(maxsize=None)
def cds_holidays(year: int) -> FrozenSet[date]:
    """Canadian settlement holidays: TSX holidays plus the bank-only ones."""
    def weekend_to_monday(d: date) -> date:
        return d + timedelta(days=(7 - d.weekday()) % 7) \
            if d.weekday() >= 5 else d
    extra = {weekend_to_monday(date(year, 11, 11))}      # Remembrance Day
    if year >= 2021:
        extra.add(weekend_to_monday(date(year, 9, 30)))  # Truth and Reconciliation
    return frozenset(tsx_holidays(year) | extra)


def market_for(currency: Optional[str]) -> Optional[str]:
    cur = (currency or '').upper()
    if cur == 'USD':
        return 'US'
    if cur == 'CAD':
        return 'CA'
    return None


def _as_date(d: DateLike) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


def is_settlement_day(d: DateLike, currency: Optional[str] = 'USD') -> bool:
    d = _as_date(d)
    if d.weekday() >= 5:
        return False
    mkt = market_for(currency)
    if mkt == 'US':
        return d not in nyse_holidays(d.year) and d not in fed_holidays(d.year)
    if mkt == 'CA':
        return d not in cds_holidays(d.year)
    return True


def is_trading_day(d: DateLike, currency: Optional[str] = 'USD') -> bool:
    """Exchange open (US: NYSE schedule; Columbus/Veterans Day trade)."""
    d = _as_date(d)
    if d.weekday() >= 5:
        return False
    mkt = market_for(currency)
    if mkt == 'US':
        return d not in nyse_holidays(d.year)
    if mkt == 'CA':
        return d not in tsx_holidays(d.year)
    return True


def add_settlement_days(d: DateLike, n: int,
                        currency: Optional[str] = 'USD') -> date:
    """The n-th settlement day after `d` (n >= 0; n == 0 returns d)."""
    cur = _as_date(d)
    added = 0
    while added < n:
        cur += timedelta(days=1)
        if is_settlement_day(cur, currency):
            added += 1
    return cur


def sub_settlement_days(d: DateLike, n: int,
                        currency: Optional[str] = 'USD') -> date:
    """The LATEST trading day whose n-day settlement lands on `d`.

    Around a US bank holiday two trading days can share one settle date
    (a Friday and Columbus Day both settle on the Tuesday); the later
    one is returned. When no trading day settles exactly on `d` (d is
    not itself a settlement day), the latest one settling before it is
    returned."""
    target = _as_date(d)
    cur = target
    for _ in range(40):
        cur -= timedelta(days=1)
        if is_trading_day(cur, currency) and \
                add_settlement_days(cur, n, currency) <= target:
            return cur
    return target - timedelta(days=n)
