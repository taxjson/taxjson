"""When an expiry happened, read the same way by every parser (PLAN step 14
moved these from lib/brokerages/base.py, whose template keeps them as its
helpers): the date an option's expiry row belongs on when the broker posts
it late (`option_expiry_booking_date`, Questrade and RBC Direct), a right's
or a warrant's from its description (`non_option_expiry_booking_date`,
`_DESC_EXPIRY_RE`), and the settle of a trade in the expiring contract on
its expiry day, clamped to the expiry (`clamp_settlement_across`: IB,
Questrade, RBC Direct and Webull, each file's and an account's files'
together)."""
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List


def option_expiry_booking_date(posting_iso: str,
                               expiry_mmddyy: str) -> str:
    """The date an EXPIRED-option row belongs on. Questrade and RBC
    post the expiry on the next business day (a Friday 07/18/25
    expiry arrives dated Monday 07/21), but the contract ceased to
    exist — the disposition happened — on its expiry date; a Dec-31
    expiry posted Jan 2 would otherwise land in the next tax year.
    Returns the contract's own expiry (from the description's
    MM/DD/YY) when it is at most a week before the posting date;
    otherwise the posting date unchanged (a garbled description
    must not relocate the row arbitrarily)."""
    try:
        exp = datetime.strptime(expiry_mmddyy.strip(), "%m/%d/%y")
        post = datetime.strptime(posting_iso, "%Y-%m-%d")
    except (ValueError, AttributeError):
        return posting_iso
    if timedelta(0) < post - exp <= timedelta(days=7):
        return exp.strftime("%Y-%m-%d")
    return posting_iso


# "... AS OF mm/dd/yy EXPIRED", "... WARRANT EXP mm/dd/yy - EXPIRED"
_DESC_EXPIRY_RE = re.compile(
    r'\b(?:AS\s+OF|EXP(?:IRY|IRES|IRED|\.)?)\s+'
    r'(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\b', re.IGNORECASE)


def non_option_expiry_booking_date(posting_iso: str, desc: str) -> str:
    """The date a rights/warrant EXPIRY row belongs on (audit
    S065-04): like an option, an expiring right or warrant has no
    settlement cycle and brokers post it a business day or so late,
    so a Dec-31 expiry posted Jan 2 moved its loss into the next
    year. The expiry date comes from the description's "AS OF
    mm/dd/yy" / "EXP mm/dd/yy" when it is at most a week before the
    posting date; otherwise the posting date is kept."""
    m = _DESC_EXPIRY_RE.search(desc or '')
    if not m:
        return posting_iso
    mm, dd, yy = m.groups()
    return option_expiry_booking_date(
        posting_iso, f"{int(mm):02d}/{int(dd):02d}/{yy[-2:]}")


def clamp_settlement_across(transactions: List[Dict[str, Any]],
                            expiries: List[Dict[str, Any]]) -> None:
    """An option expiry has no settlement cycle: the contract ceases
    to exist on its expiry date, so the expiry row is booked with
    date_settle == date (parsers set that themselves). A trade in
    the SAME contract executed on the expiry day (a 0DTE buy or
    sell-to-open) still carries a T+1 settle that lands AFTER the
    expiry — and Canada orders the book by settle date, so the
    expiry would close a position that doesn't exist yet: a long
    0DTE call that expired worthless read as a $0 short WRITE
    followed by a buy-to-close, and a Dec-31 0DTE trade's settle
    crossed the tax year while its expiry did not. Clamp such a
    trade's settle to the expiry date (never before its own trade
    date). Only contracts with an expiry row in `expiries` are
    touched; everything else keeps its broker/computed settle.

    Mutates `transactions` in place."""
    by_key: Dict[tuple, str] = {}
    for e in expiries:
        key = (e.get('symbol'), e.get('account'))
        d = e.get('date_settle') or e.get('date') or ''
        if d and (key not in by_key or d > by_key[key]):
            by_key[key] = d
    if not by_key:
        return
    for tx in transactions:
        if tx.get('action') not in ('BUYSELL', 'ASSIGN'):
            continue
        exp = by_key.get((tx.get('symbol'), tx.get('account')))
        if not exp:
            continue
        d, s = tx.get('date') or '', tx.get('date_settle') or ''
        if d and s and d <= exp < s:
            tx['date_settle'] = exp
