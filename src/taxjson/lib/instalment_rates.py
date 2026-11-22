"""The CRA's instalment figures (ITA 156, 161(2), 163.1): the four due dates
(DUE_MONTHS, DUE_DAY), the balance-due date (balance_due_date,
balance_due_label), the $3,000 threshold and the penalty's floor and
shares, the three bases CRA accepts (BASES), and its published quarterly
prescribed rates (PUBLISHED_RATES, PUBLISHED_FROM to PUBLISHED_THROUGH;
published_rates trims them to a window). bin/taxjson_instalments.py builds
the schedule and the interest from them; tax_logic and the config template
read them from here. bin binds them when it is imported, and tax_logic and
published_rates read them here at each call: a test that changes one patches
it in this module, and bin's copies as well when bin's own code must see it.
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Tuple


DUE_MONTHS = (3, 6, 9, 12)
DUE_DAY = 15
# The balance-due date (ITA s.248(1) "balance-due day": April 30 of the
# next year for an individual): instalment interest runs to it, and a
# payment dated after it is not an instalment of the year.
BALANCE_DUE_MONTH_DAY = (4, 30)


def balance_due_date(year: int) -> date:
    """April 30 of the year after `year`."""
    return date(int(year) + 1, *BALANCE_DUE_MONTH_DAY)


def balance_payment_deadline(year: int) -> date:
    """The last day a payment of `year`'s balance is on time: the
    balance-due date, or the next business day when it falls on a
    weekend (CRA accepts a payment on that day as on time; statutory
    holidays are not modelled, as for the quarterly dates). A payment
    dated up to it belongs to the year (backlog TJ-0915); interest still
    runs to balance_due_date, so it earns no credit."""
    return _next_business_day(balance_due_date(year))


def balance_due_label() -> str:
    """'April 30' (from the constant)."""
    import calendar
    m, d = BALANCE_DUE_MONTH_DAY
    return f"{calendar.month_name[m]} {d}"
THRESHOLD = 3000.0          # net tax owing above which instalments apply
PENALTY_FLOOR = 1000.0      # interest below this can never be penalized
PENALTY_SHARE = 0.50        # of the excess over the floor
PENALTY_ALT_FRACTION = 0.25  # of the no-payment interest
BASES = ("current_year", "prior_year", "cra_reminder")
INTEREST_MIN = 25.0         # CRA charges instalment interest only above

# CRA's published rate on overdue taxes (instalment interest; the same
# rate prices the credit side), by calendar quarter. Verified
# 2026-09-27 against each quarter's page under
# https://www.canada.ca/en/revenue-agency/services/tax/prescribed-interest-rates.html
# (e.g. .../prescribed-interest-rates/2026-q4.html: "overdue taxes ...
# will be 7%"). Add each quarter as CRA announces it. 2023: Q1 8%,
# Q2-Q4 9% (.../prescribed-interest-rates/2023-q1.html .. 2023-q4.html:
# "The interest rate charged on overdue taxes ... will be 8%" / "9%");
# without them a 2023 year was charged 2024 Q1's 10% all year and the
# report called it the published rate (audit R1-220).
PUBLISHED_RATES: List[Tuple[str, float]] = [
    ("2023-01-01", 0.08), ("2023-04-01", 0.09),
    ("2023-07-01", 0.09), ("2023-10-01", 0.09),
    ("2024-01-01", 0.10), ("2024-04-01", 0.10),
    ("2024-07-01", 0.09), ("2024-10-01", 0.09),
    ("2025-01-01", 0.08), ("2025-04-01", 0.08),
    ("2025-07-01", 0.07), ("2025-10-01", 0.07),
    ("2026-01-01", 0.07), ("2026-04-01", 0.07),
    ("2026-07-01", 0.07), ("2026-10-01", 0.07),
]
PUBLISHED_THROUGH = "2026-12-31"   # last day the table covers
PUBLISHED_FROM = PUBLISHED_RATES[0][0]  # first day the table covers


def published_rates(start: date, end: date) -> List[Dict[str, Any]]:
    """The published schedule trimmed to [start, end] — the segment in
    force on `start` first, then each CHANGE of rate — as a dated
    schedule normalize_rates accepts."""
    s_iso, e_iso = start.isoformat(), end.isoformat()
    out: List[Dict[str, Any]] = []
    for eff, r in PUBLISHED_RATES:
        if eff > e_iso:
            break
        if eff <= s_iso:
            out = [{"from": s_iso, "rate": r}]
        elif not out or out[-1]["rate"] != r:
            out.append({"from": eff, "rate": r})
    if not out:                 # window before the table: earliest
        out = [{"from": s_iso, "rate": PUBLISHED_RATES[0][1]}]
    elif out[0]["from"] > s_iso:
        # The window starts before the table: the earliest rate is
        # carried BACK to the window start (build() flags it as
        # extrapolated) instead of leaving the gap to rate_on's
        # "earliest segment" fallback under the table's own date.
        out[0] = {"from": s_iso, "rate": out[0]["rate"]}
    return out


def _next_business_day(d: date) -> date:
    """Weekend rollover. Statutory holidays are NOT modeled — the four
    instalment dates rarely land on one, and CRA's own rule is 'next
    business day', so a holiday would shift by at most one more day."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d
