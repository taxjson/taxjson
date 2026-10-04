import csv
import io
import re
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta


# IB currency → exchange-suffix map. Previously duplicated inline at three
# sites; an unmapped currency (EUR/JPY/CHF/...) silently produced a junk
# suffix like SYM.EUR that no downstream ticker map recognizes, fragmenting
# the position. Centralized + a one-time warning so a held exotic currency is
# surfaced instead of corrupting silently.
_IB_CURRENCY_EXT = {'CAD': 'TO', 'USD': 'US', 'AUD': 'AX', 'GBP': 'L'}
_IB_WARNED_CURRENCIES: set = set()


def _ib_currency_ext(currency: str) -> str:
    ext = _IB_CURRENCY_EXT.get(currency)
    if ext is None:
        if currency not in _IB_WARNED_CURRENCIES:
            _IB_WARNED_CURRENCIES.add(currency)
            print(
                f"warning: IB currency {currency!r} has no exchange-suffix "
                f"mapping; using '.{currency}', which downstream ticker maps "
                f"won't recognize (position may fragment). Add it to "
                f"_IB_CURRENCY_EXT in ib_extractor.py.",
                file=sys.stderr,
            )
        return currency
    return ext


# Trades take their exchange suffix from the trade currency; DIVIDEND/TAX rows
# take theirs from the security's ISIN country. Those disagree for a dual-listed
# name held on a non-domicile exchange — a Canadian-domiciled issuer held on the
# NYSE (SAMPLQ.US) has its dividend stamped SAMPLQ.TO (ISIN 'CA'); an Irish-domiciled
# issuer held as SAMPMB.US gets SAMPMB.L (ISIN 'IE') — orphaning the income onto a
# phantom symbol with no shares. Neither currency nor ISIN is reliable on its
# own (the issuer's TSX listing SAMPLP.TO pays some dividends in USD, so currency would
# wrongly say .US there). The dividend's own ticker (SAMPLP vs SAMPLQ) already names
# the listing, so bind each income row to the suffix of the position actually
# held for that ticker in this statement.
_POSITION_ACTIONS = frozenset({'BUYSELL', 'TRANSFER'})
_INCOME_ACTIONS = frozenset({'DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX'})
_KNOWN_EXT_RE = re.compile(r'^(.*)\.(TO|US|AX|L)$')

# A Corporate Actions row settling the fractional share a split/merger
# ratio leaves over for cash: negative Quantity (the fraction removed),
# the cash in Proceeds. Matched on the phrase alone, wherever it sits
# after the leading `TICKER(ISIN)` token — IB's exact wording for this
# row is unverified against a real statement (KNOWN_ISSUES.md).
_IB_CIL_RE = re.compile(
    r'^\s*([A-Z0-9\s\.]+?)\s*\([^)]*\).*?\bcash\s+in\s+lieu\b', re.IGNORECASE)


def _ib_refine_split_ratio(info: Dict[str, Any]) -> None:
    """Snap an emitted SPLIT to the broker's own leg quantities once both
    legs are known. IB books fractional results to 4 dp, so the text
    ratio leaves ±1e-4 phantom dust; and a whole-share floor with cash
    in lieu (10 sh 1-for-3 → 3 sh + 0.3333 CIL) diverges from the text
    ratio by the settled fraction, so that fraction counts as part of
    the new leg: old × ratio − fraction then lands on whole shares
    exactly. Bounded to near the text ratio — a larger divergence means
    the legs describe something else; keep the text ratio."""
    if not (info['new'] and info['old']):
        return
    # `new` sums the positive legs and `old` the negative ones. On a
    # LONG pool the old shares leave (negative) and the new arrive; on
    # a SHORT pool the signs flip — the positive leg covers the old
    # short and the negative one opens the new — so the long reading
    # gave the reciprocal, which a ratio near 1 (102-for-100) let
    # through the 5% bound (audit S058-20). The reading closer to the
    # text ratio wins; cash in lieu exists only on a long pool.
    text = info['text_ratio']
    cands = [(info['new'] + info['cil']) / info['old']]
    if not info['cil']:
        cands.append(info['old'] / info['new'])
    derived = min(cands, key=lambda r: abs(r / text - 1.0))
    if abs(derived / text - 1.0) < 0.05:
        info['tx']['quantity'] = derived


# A dotted symbol whose last part is a currency code is an IB currency or
# venue line (ABH.CAD: the temporary line a merger fraction sat on), not
# a class share like SAMPMP.B or a unit like SAMPMQ.UN (audit R1-59 / S059-00).
_IB_CURRENCY_TAGS = frozenset({
    'CAD', 'USD', 'EUR', 'GBP', 'AUD', 'CHF', 'JPY', 'HKD', 'SEK', 'NOK',
    'DKK', 'NZD', 'SGD', 'CNH', 'CNY', 'MXN', 'ILS', 'ZAR', 'KRW', 'INR',
    'PLN', 'CZK', 'HUF', 'TRY'})

# Days between a cash-in-lieu row and the split whose fraction it settles.
_IB_CIL_WINDOW = 7


def _days_between(a: str, b: str) -> int:
    return (datetime.strptime(b, "%Y-%m-%d")
            - datetime.strptime(a, "%Y-%m-%d")).days


def _split_known_ext(symbol: str):
    """(root, ext) if `symbol` ends in a known exchange suffix, else (symbol,
    None). Options (OCC) and futures (F:/CASH) have no such suffix → skipped."""
    m = _KNOWN_EXT_RE.match(symbol or '')
    return (m.group(1), m.group(2)) if m else (symbol or '', None)


def _reattribute_income_to_holdings(transactions: List[Dict[str, Any]],
                                    extra_held=None, fallback_held=None,
                                    isins_by_root=None,
                                    mismatches=None, open_qty=None,
                                    ambiguous=None) -> None:
    """Rewrite each DIVIDEND/TAX suffix to match the position held for the same
    ticker in this file, correcting ISIN-vs-listing mismatches. Leaves the
    ISIN-derived suffix untouched when the account holds no position for that
    ticker, or holds it under more than one suffix (ambiguous — don't guess).

    `extra_held` seeds the holdings map with full symbols from the statement's
    Open Positions section. That closes the two gaps of trade-only inference:
    (a) a buy-and-hold name generates dividends in every LATER statement year
    with no trade rows, so trade-derived holdings were empty and the ISIN
    fallback (SAMPMB.L, SAMPLQ.TO) came back every hold-year; (b) an interlisted
    same-ticker name (SAMPLW-class) whose OTHER listing is still held makes the
    root ambiguous, correctly suppressing the rewrite instead of misbinding a
    .TO dividend onto the .US listing traded in this file.

    `fallback_held` (the listings held or traded in the account's OTHER
    statements) is used only for a root this statement has no position
    evidence for: a statement with only a dividend or ROC row kept the ISIN
    suffix and booked the ROC on a phantom listing (audit S060-00).

    `isins_by_root` (the Financial Instrument Information Security IDs per
    ticker) makes the rebind require the SAME security: a row whose ISIN
    names a different issuer than the held listing of that root (a US
    issuer's SAMPMC vs a Canadian issuer's SAMPMC.TO) keeps its ISIN-derived
    suffix (audit S059-24 / S060-19); the (symbol, ISIN) pairs skipped
    are added to `mismatches`. A row whose ISIN, or whose root's listing
    ISIN, is unknown is rebound as before.

    A root held under several listings is narrowed to the listings held
    ON THE PAYMENT DATE: a TSX buy months after an NYSE-line ROC moved
    the ROC onto the TSX line (an EMPTY pool, a false s.40(3) gain, a
    currency clash) and made the row differ between overlapping
    downloads (audit A2-0089). The position on a date is this
    statement's trades and transfers up to it on top of the opening
    position — `open_qty` (the Open Positions quantities, when the
    section is present) less the statement's net, else the least
    opening the trades need. Rows still ambiguous are added to
    `ambiguous` as (symbol, date)."""
    held: Dict[str, set] = {}
    for full in (extra_held or ()):
        root, ext = _split_known_ext(full)
        if ext:
            held.setdefault(root, set()).add(ext)
    for t in transactions:
        if t.get('action') in _POSITION_ACTIONS:
            root, ext = _split_known_ext(t.get('symbol'))
            if ext:
                held.setdefault(root, set()).add(ext)
    fb: Dict[str, set] = {}
    for full in (fallback_held or ()):
        root, ext = _split_known_ext(full)
        if ext and root not in held:
            fb.setdefault(root, set()).add(ext)
    held.update(fb)
    if not held:
        return
    pos: Dict[tuple, list] = {}
    for t in transactions:
        if t.get('action') in _POSITION_ACTIONS:
            root, ext = _split_known_ext(t.get('symbol'))
            if ext:
                pos.setdefault((root, ext), []).append(
                    (t.get('date') or '', float(t.get('quantity') or 0.0)))

    def _held_on(root, ext, day) -> bool:
        rows = sorted(pos.get((root, ext)) or ())
        if not rows:
            return True              # no dated evidence: cannot exclude
        net = sum(q for _d, q in rows)
        if open_qty is not None:
            opening = float(open_qty.get(f"{root}.{ext}", 0.0)) - net
        else:
            run_q, low = 0.0, 0.0
            for _d, q in rows:
                run_q += q
                low = min(low, run_q)
            opening = -low
        q_on = opening + sum(q for d, q in rows if d <= day)
        return abs(q_on) > 1e-9
    for t in transactions:
        # ROC ADJUSTs come out of the same ISIN-suffixed Dividends section
        # as income rows, so they need the same held-listing rebind — an
        # ADJUST on a phantom listing (SAMPMB.L) would reduce nothing.
        is_roc_adjust = (t.get('action') == 'ADJUST'
                         and t.get('type') == 'roc')
        if t.get('action') not in _INCOME_ACTIONS and not is_roc_adjust:
            continue
        root, ext = _split_known_ext(t.get('symbol'))
        if ext is None:
            continue
        suffixes = held.get(root)
        if suffixes and len(suffixes) > 1:
            on_day = {e for e in suffixes
                      if _held_on(root, e, t.get('date') or '')}
            if len(on_day) == 1:
                suffixes = on_day
            elif ambiguous is not None:
                ambiguous.add((t.get('symbol'), t.get('date')))
        if suffixes and len(suffixes) == 1:
            want = next(iter(suffixes))
            if want != ext:
                row_isin = (t.get('_isin') or '').upper()
                known = (isins_by_root or {}).get(root) or set()
                if row_isin and known and row_isin not in known:
                    if mismatches is not None:
                        mismatches.add((t.get('symbol'), row_isin,
                                        f"{root}.{want}"))
                    continue
                t['symbol'] = f"{root}.{want}"


FUTURES_CATEGORIES = ('Futures', 'Options On Futures')
# Row kinds (column 2) IB writes besides Header and Data: roll-ups,
# footnotes, and the blank kind of a short trailing row.
_IB_STRUCTURE_ROW_TYPES = frozenset({'', 'Total', 'SubTotal', 'Notes'})

# Parser warnings about statement COVERAGE or identity that the numbers
# may silently depend on (a statement ending before year end, a missing
# Cash Report, one security under two symbols). `taxjson run` echoes
# lines with this prefix to the console (they used to live only in the
# .diag / .sum DIAGNOSTICS banner).
ATTENTION_PREFIX = "warning: ATTENTION:"
# Known tax events the parser could not book (same prefix Kraken uses):
# echoed to the console by `taxjson run`, fatal under `run --strict`.
UNBOOKED_PREFIX = "warning: UNBOOKED:"
# The security an IB tender allocation DELIVERS: the first token of the
# row's trailing `(TICKER, NAME, ISIN)` parenthetical.
_IB_DELIVERED_RE = re.compile(r'\(\s*([A-Z0-9][A-Z0-9 .]*?)\s*,[^()]*\)\s*$')
_IB_STOCK_DIV_RE = re.compile(
    r'^\s*([A-Z0-9][A-Z0-9 .]*?)\s*\([^)]*\)\s+Stock\s+Dividend\b',
    re.IGNORECASE)


def get_ib_settlement(date_str: str, asset_cat: str,
                      currency: str = 'USD',
                      futures_settle: str = 'trade') -> str:
    """Settlement date for an IB trade (the activity CSV has no settle
    column):
    - Futures and futures options: the TRADE date by default. Their
      profit and loss is settled daily through variation margin, so the
      disposition happens when the position is closed.
      futures_settle='next_day' uses the next settlement day instead
      (the clearing house's option-premium date).
    - Stocks/Warrants: T+1 since the cutover (US 2024-05-28; Canada
      2024-05-27, a TSX trading day the US spent closed for Memorial
      Day), T+2 before, T+3 before 2017-09-05. Other markets by
      currency (lib/dates._T1_CUTOVER): LSE/EU T+2 until 2027-10-11,
      the ASX and the rest T+2.
    - Everything else (equity and index options, bonds): T+1.
    `currency` is the listing's MARKET (lib/dates.market_of), not the
    quote currency: days are counted in that market's settlement
    calendar (US: NYSE + Federal Reserve holidays; Canada: TSX + bank
    holidays), see lib/market_calendar.
    """
    from taxjson.lib.dates import settlement_lag_days
    from taxjson.lib.market_calendar import add_settlement_days
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return date_str
    if asset_cat in FUTURES_CATEGORIES:
        if futures_settle == 'next_day':
            return add_settlement_days(date_str, 1, currency).isoformat()
        return date_str
    is_equity = asset_cat in ('Stocks', 'Warrants')
    days = settlement_lag_days(date_str, currency, is_option=not is_equity)
    return add_settlement_days(date_str, days, currency).isoformat()

from taxjson.lib.core import STOCK_DIVIDEND, is_option_symbol
from taxjson.lib.dates import market_of
from taxjson.lib.brokerages.base import (BaseBrokerage, BrokerageParseError,
                                         combined_accounts_note,
                                         combined_accounts_refusal,
                                         OPTION_STRIKE_RE,
                                         _parse_div_qty_rate,
                                         encode_occ_strike,
                                         option_strike_text,
                                         is_roc_description,
                                         parse_strict_number, shown_name,
                                         ticker_map_joins,
                                         ticker_map_loaded)
from taxjson.lib.corp_actions import (ib_cash_merger, ib_merger_owned,
                                      ib_spinoff_parts, ib_tender_root)
from taxjson.lib.trade_cancel import TRADE_CANCEL_TYPE, pair_cancellations

# Statement sections that are statement METADATA or roll-ups of rows the
# parser reads elsewhere — never tax events of their own. Any section
# NOT in this list and without a parser branch is reported loudly (and
# is a parse ERROR when its header carries money-like columns): a
# renamed or localized money section ("Dividendes", "Transactions")
# used to be counted as a quiet non-event while its rows vanished.
_IB_METADATA_SECTIONS = frozenset({
    'Statement', 'Account Information', 'Account Summary',
    'Net Asset Value', 'Change in NAV', 'Mark-to-Market Performance Summary',
    'Realized & Unrealized Performance Summary',
    'Month & Year to Date Performance Summary',
    'Total P/L for Statement Period', 'Cash Report', 'Interest Accruals',
    'Codes', 'Notes/Legal Notes', 'Financial Instrument Information',
    'Deposits & Withdrawals', 'Forex Balances', 'Forex P/L Details',
    'Net Stock Position Summary', 'Base Currency Exchange Rate',
    'Location of Customer Assets, Positions and Money',
    'Stock Yield Enhancement Program Securities Lent',
    'Stock Yield Enhancement Program Securities Lent Activity',
    'Stock Yield Enhancement Program Securities Lent Interest Details',
    # A per-fill BREAKDOWN of levies IB already includes in the Trades
    # Comm/Fee column (see the Transaction Fees note in parse_file).
    'Transaction Fees',
})
# Money / position sections whose Data rows IB writes at the header's
# full width (a shorter row is a truncated export).
_IB_FULL_WIDTH_SECTIONS = frozenset({
    'Trades', 'Dividends', 'Withholding Tax', 'Interest', 'Fees',
    'Commission Adjustments', 'Corporate Actions', 'Transfers',
    'Options Expirations'})
# Header columns that mark a section as carrying money.
_IB_MONEY_COLUMNS = frozenset({
    'Amount', 'Proceeds', 'Comm/Fee', 'Net Amount', 'Gross Amount',
    'Market Value', 'Cash Amount', 'Notional Value', 'Value', 'Basis',
})

# Cash Report lines the parsed rows must reproduce, per currency, to the
# cent (0.02). Each is compared to the sum of what the parser booked for
# it (see `_ib_cash_booked`).
_IB_CASH_TOL = 0.02
_IB_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_IB_TIME_RE = re.compile(r'^\d{1,2}:\d{2}(?::\d{2})?$')
# FII option symbol, OCC-style with IB's padding: "QZD   261218P00012500".
_IB_FII_OCC_RE = re.compile(r'^([A-Z0-9.]+)\s+(\d{6})([CP])(\d{8})$')
_MON_MAP = {
    'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05',
    'JUN': '06', 'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10',
    'NOV': '11', 'DEC': '12'}
# Income rows take their exchange suffix from the security's ISIN
# country until _reattribute_income_to_holdings rebinds them to the
# listing actually held. An ISIN country outside this map falls back to
# .US — and is reported when no held position confirms it.
_ISIN_EXT = {'CA': 'TO', 'AU': 'AX', 'GB': 'L', 'IE': 'L', 'US': 'US'}


def _isin_ext(isin: str, ticker: str, fallback: set) -> str:
    if not isin or len(isin) < 2:
        return 'US'
    cc = isin[:2].upper()
    if cc in _ISIN_EXT:
        return _ISIN_EXT[cc]
    fallback.add((f"{ticker}.US", cc))
    return 'US'


# Statement titles that are NOT an Activity Statement but share its CSV
# shape (a Realized Summary lists closed lots, not the trades — parsing
# it as activity invents or drops events).
_IB_REFUSED_TITLE_RE = re.compile(
    r'summary|confirmation|performance|mtm|projected|tax', re.IGNORECASE)


def _mask_account(acct: str) -> str:
    """First two characters + *** — account numbers never reach logs."""
    a = (acct or '').strip()
    return (a[:2] + '***') if a else '?'


def _ib_period(raw: str):
    """(start, end) ISO dates of a Statement 'Period' value ('January 1,
    2024 - December 27, 2024'; a one-day period names one date), or
    None when it does not parse."""
    parts = [x.strip() for x in (raw or '').split(' - ')]
    try:
        days = [datetime.strptime(x, "%B %d, %Y").date() for x in parts]
    except ValueError:
        return None
    if len(days) == 1:
        return days[0], days[0]
    if len(days) == 2 and days[0] <= days[1]:
        return days[0], days[1]
    return None


def _ib_no_trading_gap(g0, g1) -> bool:
    """True when every day of the gap [g0, g1] is a weekend day or Jan 1
    / Dec 25 — no exchange trades and IB books nothing then, so a
    Friday-to-Monday split between two statements is not a hole (audit
    A2-0609)."""
    d = g0
    while d <= g1:
        if d.weekday() < 5 and (d.month, d.day) not in ((1, 1), (12, 25)):
            return False
        d += timedelta(days=1)
    return True


def _coverage_groups(periods):
    """{broker account: (merged spans [[start, end, name]], gaps)} of a
    label's IB statement periods — (name, start, end) or (name, start,
    end, accounts) — merged across weekend-only gaps. A statement naming
    no account covers every account of the label."""
    named = sorted(set().union(*(per[3] for per in periods
                                 if len(per) > 3 and per[3])))
    groups: Dict[str, list] = {}
    for per in periods:
        n, a, b = per[0], per[1], per[2]
        accts = (sorted(per[3]) if len(per) > 3 and per[3]
                 else named or [''])
        for acct in accts:
            groups.setdefault(acct, []).append((a, b, n))
    out = {}
    for acct, spans in groups.items():
        spans.sort()
        merged = [[spans[0][0], spans[0][1], spans[0][2]]]
        gaps = []
        for a, b, n in spans[1:]:
            cur = merged[-1]
            if (a <= cur[1] + timedelta(days=1)
                    or _ib_no_trading_gap(cur[1] + timedelta(days=1),
                                          a - timedelta(days=1))):
                if b > cur[1]:
                    cur[1], cur[2] = b, n
            else:
                gaps.append((cur[1] + timedelta(days=1),
                             a - timedelta(days=1), cur[2], n))
                merged.append([a, b, n])
        out[acct] = (merged, gaps)
    return out


def _year_shortfall(merged, tax_year):
    """(end, statement name) when an account's merged statement spans
    stop before Dec 31 of `tax_year` — end None when none covers the
    year but an earlier one exists — else None."""
    from datetime import date as _date
    y0, y1 = _date(tax_year, 1, 1), _date(tax_year, 12, 31)
    covering = [m for m in merged if m[0] <= y1 and m[1] >= y0]
    if not covering:
        last = max(merged, key=lambda m: m[1])
        return (None, last[2]) if last[1] < y0 else None
    end = max(m[1] for m in covering)
    if end >= y1:
        return None
    return end, next(m[2] for m in covering if m[1] == end)


def ib_year_coverage(paths, tax_year: int) -> List[tuple]:
    """[(masked account or '', end or None)] for each IB account whose
    statements among `paths` stop before Dec 31 of `tax_year` (end None:
    no statement covers the year) — the checklist's per-statement test
    (audit A2-0262). Unreadable files are skipped."""
    periods = []
    for path in paths:
        try:
            rows = IbBrokerage._read_rows(Path(path))
            pre = _ib_prescan(rows, shown_name(path))
        except (OSError, UnicodeError, BrokerageParseError, csv.Error):
            continue
        for row in rows:
            if (len(row) >= 4 and row[0] == 'Statement'
                    and row[1] == 'Data' and row[2] == 'Period'):
                span = _ib_period(row[3])
                if span:
                    periods.append((shown_name(path), *span,
                                    frozenset(pre['accounts'])))
                break
    out = []
    for acct, (merged, _gaps) in sorted(_coverage_groups(periods).items()):
        short = _year_shortfall(merged, tax_year)
        if short is not None:
            out.append((_mask_account(acct) if acct else '', short[0]))
    return out


def _warn_coverage_gaps(periods, today=None, tax_year=None) -> None:
    """Warn when an IB account's statements leave days uncovered: a gap
    between two statements (weekend-only gaps excepted), or statements
    that stop before Dec 31 of the tax year (the 2024 statement that
    ended Dec 27 dropped a Dec 30 sale silently — audit R1-2 / R1-195).

    Per broker ACCOUNT (Account Information): one label may hold the
    statements of two IB accounts, and one account's full year hid the
    other's missing half-year (audit A2-0091 / A2-0261).

    `tax_year` (taxjson run passes the project year): an account whose
    statements stop before Dec 31 of that FINISHED year — or hold
    nothing of it — is reported; a statement running into the next year
    covers it (A2-0262 / A2-0609). Without it, the year of the last
    statement is checked when that year is over (the first statement
    may start mid-year: an account opened in May)."""
    from datetime import date as _date
    if not periods:
        return
    today = today or _date.today()
    groups = _coverage_groups(periods)
    many = len(groups) > 1
    for acct, (merged, gaps) in sorted(groups.items()):
        who = (f" (IB account {_mask_account(acct)})"
               if many and acct else '')
        for g0, g1, before, after in gaps:
            print(f"{ATTENTION_PREFIX} IB statements{who} leave "
                  f"{g0.isoformat()} .. {g1.isoformat()} uncovered "
                  f"(between {before} and {after}) — any trade or income "
                  f"in those days is missing from the books. Download "
                  f"the statement for that period.", file=sys.stderr)
        last_end, last_name = merged[-1][1], merged[-1][2]
        if tax_year is not None:
            if today <= _date(tax_year, 12, 31):
                continue                 # the year is still open
            short = _year_shortfall(merged, tax_year)
            if short is None:
                continue
            end, end_name = short
            if end is None:
                print(f"{ATTENTION_PREFIX} {end_name}: the account's IB "
                      f"statements{who} end {last_end.isoformat()} — none "
                      f"covers {tax_year}, so any {tax_year} trade or "
                      f"income is missing from the books. Download the "
                      f"{tax_year} statement.", file=sys.stderr)
            else:
                print(f"{ATTENTION_PREFIX} {end_name}: the account's IB "
                      f"statements{who} end {end.isoformat()}, before the "
                      f"end of {tax_year} — any trade or income from "
                      f"{(end + timedelta(days=1)).isoformat()} to "
                      f"{tax_year}-12-31 is missing from the books. "
                      f"Download the statement that covers the rest of "
                      f"the year.", file=sys.stderr)
            continue
        if (last_end.year < today.year
                and (last_end.month, last_end.day) != (12, 31)):
            print(f"{ATTENTION_PREFIX} {last_name}: the account's IB "
                  f"statements{who} end {last_end.isoformat()}, before the "
                  f"end of {last_end.year} — any trade or income from "
                  f"{(last_end + timedelta(days=1)).isoformat()} to "
                  f"{last_end.year}-12-31 is missing from the books. "
                  f"Download the statement that covers the rest of the "
                  f"year.", file=sys.stderr)


def _ib_cil_unmatched_note(c: Dict[str, Any]) -> str:
    return (f"note: {c['where']}: {c['symbol']}: cash in lieu of "
            f"{c['frac']:g} share(s) on {c['date']} matched no split "
            f"of the symbol within {_IB_CIL_WINDOW} days — booked as a "
            f"sale of the fraction only.")


def unmatched_ca_warning(ca: Dict[str, Any]) -> str:
    return (f"warning: {ca['where']}: IB cancelled (Ca) the corporate "
            f"action {ca['desc']!r} ({ca['qty']:g} on {ca['date']}) but "
            f"its original row is not in this account's statements — "
            f"nothing undone; if the original was booked another way, "
            f"reverse it by hand in a .tt file.")


def _ib_split_datetime(raw: str, where: str):
    """(date, time) from IB's `YYYY-MM-DD, HH:MM:SS`; a date in any
    other shape (Flex `20250328;093000`, `03/28/2025`) is refused —
    it used to pass through verbatim into `date` and misfile the year."""
    parts = (raw or '').replace(',', ' ').split()
    date = parts[0] if parts else ''
    time = parts[1] if len(parts) > 1 else '09:30:00'
    if not _IB_DATE_RE.match(date) or not _IB_TIME_RE.match(time):
        raise BrokerageParseError(
            f"{where}: Date/Time {raw!r} is not IB's 'YYYY-MM-DD, "
            f"HH:MM:SS' — refusing to guess the date")
    hms = [int(x) for x in time.split(':')] + [0]
    h, m, s = hms[0], hms[1], hms[2]
    if h > 23 or m > 59 or s > 59:
        raise BrokerageParseError(
            f"{where}: Date/Time {raw!r} holds an impossible clock time "
            f"(hour 00-23, minute and second 00-59) — refusing to guess "
            f"the date")
    # Zero-padded: the session rules and every (date, time) sort compare
    # the time as TEXT, and '9:45:00' sorts after '20:00:00' (audit
    # A2-0082 / A2-0607).
    return date, f"{h:02d}:{m:02d}:{s:02d}"


# IB stamps Trades rows with the US Eastern CLOCK time. These fills have
# an official trade date other than that clock date (tax-logic
# CA-DATE-SESSION / US-DATE-SESSION):
#   * a US-listed stock or ETF filled in the overnight session (20:00 ET
#     onward, Sunday to Thursday nights — and its after-midnight half on
#     a day the NYSE is closed) trades on the NEXT trading day;
#   * a US-dollar futures or futures-option fill in the CME Globex
#     evening session (18:00 ET onward, Sunday to Thursday) or on a
#     weekday the exchange is closed trades on the next trading day;
#   * an option on a root with a Cboe Global Trading Hours session
#     (lib/markets.is_evening_session_root: shipped market data plus
#     ticker.map EVENING lines) filled from 20:15 ET trades on the next
#     trading day;
#   * a fill on the ASX, HKEX, Tokyo, Singapore or NZX exchanges (every
#     asset class) is dated in the exchange's local time.
_IB_OVERNIGHT_OPEN = '20:00:00'
_IB_OVERNIGHT_CLOSE = '04:00:00'        # the session ends 03:50 ET
_IB_CME_EVENING_OPEN = '18:00:00'
_IB_GTH_OPEN = '20:15:00'
_IB_CLOCK_TZ = 'America/New_York'
_IB_LOCAL_TZ_BY_CURRENCY = {'AUD': 'Australia/Sydney',
                            'HKD': 'Asia/Hong_Kong', 'JPY': 'Asia/Tokyo',
                            'SGD': 'Asia/Singapore',
                            # Shanghai/Shenzhen Stock Connect lines
                            # trade in CNH (re-audit A2-1302).
                            'CNH': 'Asia/Shanghai',
                            'NZD': 'Pacific/Auckland'}

def _ib_next_trading_day(d, include_today: bool = False):
    from taxjson.lib.market_calendar import is_trading_day
    nxt = d if include_today else d + timedelta(days=1)
    while not is_trading_day(nxt, 'USD'):
        nxt += timedelta(days=1)
    return nxt


def _ib_market_trade_date(date: str, time: str, asset_cat: str,
                          currency: str, ext: str, symbol: str = ''):
    """(trade date, time, broker stamp) of a Trades row whose exchange
    trade date is not the clock date IB printed; the broker stamp is ''
    when nothing changes. A fill moved to the next trading day is placed
    at 00:00:00 of that day — before its regular session, the fills of
    one night keeping their export (clock) order. A Friday- or
    Saturday-night US fill is left as stamped: there is no session
    then, and `taxjson check-dates` flags it."""
    stamp = f"{date} {time} ET"
    cur = (currency or '').upper()
    zone = _IB_LOCAL_TZ_BY_CURRENCY.get(cur)
    if zone:
        from zoneinfo import ZoneInfo
        clock = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M:%S")
        try:
            local = clock.replace(tzinfo=ZoneInfo(_IB_CLOCK_TZ)).astimezone(
                ZoneInfo(zone))
        except Exception as e:      # ZoneInfoNotFoundError, no tz database
            # An uncaught traceback where the platform has no tz database
            # (Windows without `tzdata`, audit A2-1447). No DST-rule
            # fallback for Sydney/Auckland: the exchange trade date would
            # be guessed.
            raise BrokerageParseError(
                f"{symbol or cur} {date} {time}: the {cur} fill's exchange "
                f"trade date needs the time zone {zone!r}, which this "
                f"system's time-zone database does not have ({e}) — "
                f"install it: pip install tzdata") from None
        ld, lt = local.strftime("%Y-%m-%d"), local.strftime("%H:%M:%S")
        if ld != date:
            return ld, lt, stamp
        return date, time, ''
    d = datetime.strptime(date, "%Y-%m-%d").date()
    sun_thu = d.weekday() in (6, 0, 1, 2, 3)
    from taxjson.lib.market_calendar import is_trading_day
    if asset_cat in FUTURES_CATEGORIES:
        if cur != 'USD':
            return date, time, ''
        if time >= _IB_CME_EVENING_OPEN and sun_thu:
            return _ib_next_trading_day(d).isoformat(), '00:00:00', stamp
        if d.weekday() < 5 and not is_trading_day(d, 'USD'):
            # A holiday session (MLK day): its trade date is the next
            # trading day.
            return (_ib_next_trading_day(d).isoformat(), '00:00:00',
                    stamp)
        return date, time, ''
    if asset_cat == 'Equity and Index Options':
        root = (symbol or '').strip().split(' ')[0].upper()
        if cur == 'USD' and time >= _IB_GTH_OPEN and sun_thu:
            from taxjson.lib.markets import is_evening_session_root
            if is_evening_session_root(root):
                return (_ib_next_trading_day(d).isoformat(), '00:00:00',
                        stamp)
        return date, time, ''
    if asset_cat != 'Stocks' or ext != 'US':
        return date, time, ''
    if time >= _IB_OVERNIGHT_OPEN:
        if sun_thu:
            return _ib_next_trading_day(d).isoformat(), '00:00:00', stamp
        return date, time, ''
    if (time < _IB_OVERNIGHT_CLOSE and d.weekday() < 5
            and not is_trading_day(d, 'USD')):
        # The after-midnight half of an overnight session on an NYSE
        # holiday (audit A2-0597): the same trade date as its
        # pre-midnight half.
        return _ib_next_trading_day(d).isoformat(), '00:00:00', stamp
    return date, time, ''


_IB_INCOME_TICKER_RE = re.compile(
    r'^([A-Z.\d\-]+(?:\s+[A-Z.\d\-]+)*)\s*\(([^)]+)\)')


def _ib_income_ticker(description: str):
    """(ticker, ISIN) of a Dividends / Withholding Tax row: the leading
    `TICKER(ISIN)` token (space-form class tickers 'SAMPLC B' dotted), else
    the first ticker-like word and no ISIN."""
    ticker, isin = 'UNKNOWN', ''
    m = _IB_INCOME_TICKER_RE.search(description or '')
    if m:
        ticker, isin = m.groups()
    else:
        m = re.search(r'([A-Z.\d\-]+)', description or '')
        if m:
            ticker = m.group(1)
    return ticker.replace(' ', '.'), isin


_IB_INCOME_PHRASE_WORDS = frozenset((
    'CASH', 'DIVIDEND', 'DIVIDENDS', 'PAYMENT', 'RETURN', 'WITHHOLDING',
    'INTEREST', 'CHOICE', 'STOCK', 'TAX', 'PER', 'SHARE', 'USD', 'CAD',
    'ORDINARY', 'SPECIAL', 'BONUS', 'CREDIT', 'DEBIT'))


def _ib_income_ticker_strict(description: str, where: str,
                             section: str):
    """`_ib_income_ticker` for a row that is BOOKED: a Dividends or
    Withholding Tax description with no leading `TICKER (ISIN)` token
    is refused naming the file line. The fallback invented UNKNOWN.US or
    a word of the text ('CASH.DIVIDEND...US') and a CAD-paid Canadian
    eligible dividend was estimated as a foreign one (A2-0780). A
    withholding row on credit interest (IB: 'Withholding @ 20% on Credit
    Interest for MAY-2024') names no security: it is booked on CASH, the
    symbol the Interest section books the interest itself on."""
    if _IB_INCOME_TICKER_RE.search(description or ''):
        return _ib_income_ticker(description)
    # An older statement's 'QZNO Return of Capital USD 0.20 per Share':
    # a leading upper-case ticker, then IB's mixed-case wording — kept
    # as before (no ISIN: the issuer country stays unknown). A leading
    # word of the income phrase itself ('CASH DIVIDEND ...') is not a
    # ticker.
    m = re.match(r'([A-Z][A-Z.\d\-]*)\s+(\S.*)$', description or '')
    if (m and m.group(1) not in _IB_INCOME_PHRASE_WORDS
            and re.search(r'[a-z]', m.group(2))):
        return m.group(1), ''
    if section == 'Withholding Tax' and 'interest' in (description
                                                       or '').lower():
        return 'CASH', ''
    raise BrokerageParseError(
        f"{where}: the Description {description!r} has no leading "
        f"'TICKER (ISIN)' token, so the security this {section} row "
        f"belongs to is unknown — restore the row from the original IB "
        f"statement (or download it again)")


def _ib_posted_dividends(rows) -> List[tuple]:
    """(ticker, pay date) of every posted Dividends row of a statement
    (subtotals skipped) — the account-wide evidence that an accrual in
    another statement was paid (audit R1-327)."""
    out: List[tuple] = []
    hm: Dict[str, int] = {}
    for row in rows:
        if len(row) < 2 or row[0] != 'Dividends':
            continue
        if row[1] == 'Header':
            hm = {c: i for i, c in enumerate(row)}
            continue
        if row[1] != 'Data' or not hm:
            continue

        def g(col, _row=row):
            i = hm.get(col)
            v = _row[i].strip() if i is not None and i < len(_row) else ''
            return _norm_ccy(v) if col == 'Currency' else v
        if not g('Currency') or 'Total' in g('Currency'):
            continue
        if _IB_DATE_RE.match(g('Date')):
            out.append((_ib_income_ticker(g('Description'))[0], g('Date')))
    return out


def _ib_accrual_facts(rows) -> List[Dict[str, Any]]:
    """The dividend-accrual facts of a statement's Change in Dividend
    Accruals section (symbol, pay dates, ex date, currency, the Po row's
    rate and share count), one per dividend — the account-wide evidence
    for a posting in ANOTHER statement (audit A2-0601). Read leniently:
    a malformed row is the statement parse's to report."""
    out: Dict[tuple, Dict[str, Any]] = {}
    hm: Dict[str, int] = {}
    for row in rows:
        if len(row) < 2 or row[0] != 'Change in Dividend Accruals':
            continue
        if row[1] == 'Header':
            hm = {c: i for i, c in enumerate(row)}
            continue
        if row[1] != 'Data' or not hm:
            continue

        def g(col, _row=row):
            i = hm.get(col)
            return _row[i].strip() if i is not None and i < len(_row) else ''
        toks = set(re.split(r'[;,\s]+', g('Code')))
        code = 'Po' if 'Po' in toks else 'Re' if 'Re' in toks else ''
        sym = g('Symbol').replace(' ', '.')
        if not code or not sym:
            continue
        ex, pay = g('Ex Date'), g('Pay Date')
        key = ((g('Account'), sym, 'ex', ex) if ex
               else (g('Account'), sym, 'pay', pay))
        m = out.setdefault(key, {'symbol': sym, 'pay_date': pay,
                                 'pay_dates': set(),
                                 'currency': _norm_ccy(g('Currency'))})
        if pay:
            m['pay_date'] = pay
            m['pay_dates'].add(pay)
        if _IB_DATE_RE.match(ex or ''):
            m['ex_date'] = ex
        if code == 'Po':
            for col, fld in (('Gross Rate', 'po_rate'),
                             ('Quantity', 'po_qty')):
                try:
                    v = abs(parse_strict_number(g(col), field=col))
                except (BrokerageParseError, ValueError):
                    v = 0.0
                if v:
                    m[fld] = v
    return list(out.values())


def _norm_ccy(v: str) -> str:
    """A Currency cell as IB writes it: a 3-letter code upper-cased (a
    hand-edited 'usd' became the suffix .usd and split the pool, audit
    S055-17); anything else ('Total', 'Total in CAD') unchanged."""
    v = (v or '').strip()
    return v.upper() if len(v) == 3 and v.isalpha() else v


def _ib_tender_is_placeholder(description: str, qty: float) -> bool:
    """Whether a tender/voluntary-offer leg moves shares onto (a
    `Tendered to` row's positive leg) or off (an allocation's negative
    leg) IB's `.TEN` placeholder line."""
    tender_in = 'tendered to' in (description or '').lower()
    return (tender_in and qty > 0) or (not tender_in and qty < 0)


def _ib_tender_parked(rows, fii=None) -> Dict[str, float]:
    """Shares a statement leaves on the tender placeholder line, per
    suffixed root symbol — the parse's own tally, for the account-wide
    check (a tender in one statement resolved in the next, audit
    S059-06). A `Ca` row takes back what its original parked."""
    out: Dict[str, float] = {}
    hm: Dict[str, int] = {}
    for row in rows:
        if len(row) < 2 or row[0] != 'Corporate Actions':
            continue
        if row[1] == 'Header':
            hm = {c: i for i, c in enumerate(row)}
            continue
        if row[1] != 'Data' or not hm:
            continue

        def g(col, _row=row):
            i = hm.get(col)
            v = _row[i].strip() if i is not None and i < len(_row) else ''
            return _norm_ccy(v) if col == 'Currency' else v
        cur, cat, desc = g('Currency'), g('Asset Category'), g('Description')
        if (not cur or 'Total' in cur or cat.startswith('Total')
                or (cat and cat not in ('Stocks', 'Warrants'))):
            continue
        root = ib_tender_root(desc)
        if root is None:
            continue
        try:
            qty = parse_strict_number(g('Quantity'), field='Quantity')
            proceeds = parse_strict_number(g('Proceeds'), field='Proceeds',
                                           allow_blank=True, blank=0.0)
        except BrokerageParseError:
            continue
        _base = _IB_CURRENCY_EXT.get(cur, cur)
        _e = _base
        if fii:
            for _raw in (root, root.replace('.', ' ')):
                _e = _ib_listing_ext(cat or 'Stocks', _raw, cur, fii)
                if _e != _base:
                    break
        sym = f"{root}.{_e}"
        if 'Ca' in re.split(r'[;,\s]+', g('Code')):
            if _ib_tender_is_placeholder(desc, -qty):
                out[sym] = out.get(sym, 0.0) + qty
            continue
        if abs(proceeds) >= 0.005 and qty > 0:
            continue                         # not a shape the parse books
        if _ib_tender_is_placeholder(desc, qty):
            out[sym] = out.get(sym, 0.0) + qty
    return out


def _ib_require_date(raw: str, where: str, field: str = 'Date') -> str:
    d = (raw or '').strip()
    if not _IB_DATE_RE.match(d):
        raise BrokerageParseError(
            f"{where}: {field} {raw!r} is not YYYY-MM-DD — refusing to "
            f"guess the date")
    return d


def _canonical_root(roots) -> str:
    """The canonical option root among aliases IB lists for ONE conid.
    OCC renames an adjusted contract by appending a digit (QZD ->
    QZD1), so the root every other alias extends is the original;
    otherwise the shortest (then alphabetical) wins."""
    roots = sorted(set(roots), key=lambda r: (len(r), r))
    for r in roots:
        if all(o == r or o.startswith(r) for o in roots):
            return r
    return roots[0]


def _root_aliases(occ_by_conid, underlying_by_conid):
    """(root_alias, alias_conids) for conids listed under several option
    roots (QZD 251121P..., QZD1 251121P... after a corporate action
    renamed the adjusted contract): every alias root maps to the
    canonical one. The canonical root is the contract's UNDERLYING when
    it is one of the roots — after a ticker rename (OLDTKR -> SAMPLE) the
    shorter old root won and the assigned option never met the
    delivered shares (audit S059-11); otherwise _canonical_root."""
    root_alias: Dict[str, str] = {}
    alias_conids: Dict[str, set] = {}
    for conid, occs in occ_by_conid.items():
        roots = {o[0] for o in occs}
        if len(roots) < 2:
            continue
        und = underlying_by_conid.get(conid) or ''
        # Every Underlying any of the account's statements names for the
        # conid: one statement said QZD and a re-download QZD1, and
        # the file being parsed won — one put series split across two
        # symbols (audit A2-0087). Among several, the prefix rule.
        unds = ({u.strip() for u in und} if isinstance(und, (set, frozenset))
                else {und.strip()}) & roots
        canon = (next(iter(unds)) if len(unds) == 1
                 else _canonical_root(unds) if unds
                 else _canonical_root(roots))
        for r in roots:
            if r != canon:
                root_alias[r] = canon
                alias_conids.setdefault(r, set()).add(conid)
    return root_alias, alias_conids


# Canadian listing venues as IB's Financial Instrument Information names
# them (Listing Exch).
_IB_LSE_VENUES = frozenset({'LSE', 'LSEETF', 'LSEIOB1'})
_IB_CA_VENUES = frozenset({'TSE', 'VENTURE', 'TSXV', 'CSE', 'NEO', 'AEQLIT',
                           'PURE', 'OMEGA', 'CHIXCA', 'ALPHA', 'LYNX'})


def _ib_listing_ext(asset_cat: str, raw_symbol: str, currency: str,
                    fii: Dict[tuple, Any]) -> str:
    """The exchange suffix of a stock/warrant row: from the currency,
    except a USD-class unit of a TSX-listed fund ('ZSP.U', 'SAMPLF.U'),
    which is a Canadian listing (SAMPMD.U.TO — the spelling RBC and the
    ticker maps use); `.US` made it a fictional US security (audit
    S010-06). Only the `.U` unit class is re-suffixed: IB's instrument
    list names a single primary listing per symbol, so a USD trade of an
    interlisted ordinary share (SAMPMR on the NYSE) must keep `.US`."""
    ext = _ib_currency_ext(currency)
    s = (raw_symbol or '').strip()
    if asset_cat not in ('Stocks', 'Warrants') or ext in ('TO', 'L'):
        return ext
    info = (fii.get((asset_cat, s))
            or fii.get((asset_cat, re.sub(r'\s+', ' ', s))) or {})
    exch = (info.get('exch') or '').upper()
    if re.search(r'[.\s]U$', s) and exch in _IB_CA_VENUES:
        return 'TO'
    if exch in _IB_LSE_VENUES:
        # A USD line of an LSE-listed fund or GDR (CSPX-style UCITS
        # ETF): an LSE security on the UK cycle, not a fictional `.US`
        # one on the US T+1 calendar (audit A2-0081).
        return 'L'
    return ext


def _ib_stock_symbol(asset_cat: str, raw_symbol: str, currency: str,
                     fii: Dict[tuple, Any]) -> str:
    sym = (raw_symbol or '').strip().replace(' ', '.')
    sym = re.sub(r'\.(TO|US|AX|L)$', '', sym, flags=re.IGNORECASE)
    return f"{sym}.{_ib_listing_ext(asset_cat, raw_symbol, currency, fii)}"


def _warn_stock_aliases(conid_syms: Dict[str, set], where: str,
                        first_seen=None, listing=None,
                        mapping=None) -> None:
    """One stock conid listed under several symbols (a ticker change
    with no corporate-action row): the parser books each symbol as its
    own security, so the position splits into two pools (audit S059-13 /
    S060-17). Said on the console with the ticker.map fix: the OLD
    symbol (first traded) first, each with the listing suffix it is
    booked under (an alphabetical pair with a hard-coded .US joined
    nothing, or renamed new to old — audit A2-0611); quiet once the
    project's ticker.map (`mapping`) joins them."""
    first_seen = first_seen or {}
    listing = listing or {}
    for conid, syms in sorted(conid_syms.items()):
        if len(syms) < 2:
            continue
        order = sorted(syms, key=lambda x: (first_seen.get(x) or '9999',
                                            x))
        full = [listing.get(x) or f"{x}.US" for x in order]
        if ticker_map_loaded():
            # The map `taxjson-brokerage --ticker-map` loaded (A2-1056).
            if all(ticker_map_joins(full[0], f) for f in full[1:]):
                continue
        elif mapping is not None:
            from taxjson.bin.taxjson_ticker_map import map_symbol
            if len({map_symbol(f, mapping) for f in full}) == 1:
                continue
        a, b = full[0], full[-1]
        print(f"{ATTENTION_PREFIX} {where}: IB lists one stock (contract "
              f"id {conid}) under several symbols: {', '.join(order)}"
              f" — a ticker change. Each symbol is booked as its own "
              f"security until you join them in ticker.map, e.g. "
              f"`GLOBAL {a} {b}` (old symbol first, as first traded).",
              file=sys.stderr)


_IB_UNMATCHED_CA_SKIP = ("Corporate Actions Ca row whose original is not "
                         "in this statement (see warning)")


def _flush_ca_side_effects(path, late_warnings, cash_takeovers,
                           corp_owned_rows, stock_dividends, unbooked_ca,
                           unhandled_ca_tickers) -> None:
    """Print one statement's Corporate Actions side-effect lines (see
    IbBrokerage.parse_file's `_print_ca_side_effects`)."""
    for _w in late_warnings:
        print(_w, file=sys.stderr)
    for _ct in cash_takeovers:
        print(f"NOTE: cash takeover booked as a sale: {_ct} "
              f"({shown_name(path)}).", file=sys.stderr)
    if corp_owned_rows:
        print(f"note: {len(corp_owned_rows)} merger/spin-off "
              f"Corporate Action row(s) in {shown_name(path)} are booked by "
              f"taxjson-corp-actions after the tax election (`taxjson "
              f"run` runs it), not by this parser.", file=sys.stderr)
    for _m in stock_dividends:
        print(f"{ATTENTION_PREFIX} {_m}", file=sys.stderr)
    for _m in unbooked_ca:
        print(f"{UNBOOKED_PREFIX} {_m}", file=sys.stderr)
    if unhandled_ca_tickers:
        total = sum(unhandled_ca_tickers.values())
        tickers = ', '.join(sorted(unhandled_ca_tickers.keys()))
        # UNBOOKED (console; fatal under run --strict): a row that
        # moved shares and nothing booked. The old advice — a
        # manual TRANSFER row — is dropped in a taxable account and
        # double-booked what corp-actions already books (R1-140).
        print(
            f"{UNBOOKED_PREFIX} {total} unhandled Corporate Action "
            f"row(s) in {shown_name(path)} for: {tickers}. Only splits, cash "
            f"in lieu, stock dividends, tenders and cash takeovers are "
            f"booked here (mergers and spin-offs by "
            f"taxjson-corp-actions). If the event changed your "
            f"position or basis, book it by hand in a .tt file "
            f"(BUYSELL / SPLIT rows).",
            file=sys.stderr,
        )


def _project_ticker_map(paths):
    """The rename map of the project ticker.map next to inputs/<account>/
    <statement>, or None (a statement outside a project, no map, or a
    map that does not parse)."""
    for path in paths:
        try:
            pp = Path(path).resolve().parents
            root = pp[2] if pp[1].name == 'inputs' else None
        except (IndexError, OSError):
            root = None
        if root is None or not (root / 'ticker.map').is_file():
            continue
        try:
            from taxjson.bin.taxjson_ticker_map import (_parse_map_file,
                                                        merge_renames)
            return merge_renames(_parse_map_file(root / 'ticker.map')[0],
                                 True)
        except Exception:               # the run reports a bad map
            return None
    return None


def _ib_prescan(rows, where: str) -> Dict[str, Any]:
    """Read the statement-level context every row branch needs BEFORE
    the row walk (these sections sit after Trades in IB's layout):
    the title, the Financial Instrument Information multipliers /
    option root aliases / real expiries, the account ids, and the Cash
    Report totals."""
    hm: Dict[str, Dict[str, int]] = {}
    out: Dict[str, Any] = {
        'title': '', 'fii': {}, 'root_alias': {}, 'alias_conids': {},
        'accounts': set(), 'accounts_included': '', 'cash': {},
        'cash_currencies': set(), 'has_cash_report': False,
        'has_order_level': False, 'order_levels': {},
        'stock_isins': {}, 'stock_conid_syms': {}, 'opt_underlying': {},
        'held_rows': [], 'broker_name': '', 'cash_bad': {},
        'first_seen': {}, 'stock_listing': {},
    }
    occ_by_conid: Dict[str, set] = {}
    contract_conids: Dict[tuple, set] = {}
    for row in rows:
        if len(row) < 2:
            continue
        sec, kind = row[0], row[1]
        if kind == 'Header':
            hm[sec] = {c: i for i, c in enumerate(row)}
            continue
        if kind != 'Data' or sec not in hm:
            continue
        h = hm[sec]

        def g(col, _row=row, _h=h):
            i = _h.get(col)
            v = _row[i].strip() if i is not None and i < len(_row) else ''
            return _norm_ccy(v) if col == 'Currency' else v

        if sec == 'Trades' and g('DataDiscriminator') == 'Order':
            out['has_order_level'] = True
        if (sec in ('Trades', 'Transfers', 'Open Positions')
                and g('Asset Category') in ('Stocks', 'Warrants')
                and g('Symbol') and g('Currency')):
            # Listings this statement trades or holds (raw symbol,
            # category, currency) — the account-wide income rebind uses
            # them (prepare_files).
            out['held_rows'].append((g('Symbol'), g('Asset Category'),
                                     g('Currency')))
            _sroot = re.sub(r'\s+', '.', g('Symbol'))
            _sday = (g('Date/Time') or g('Date')).replace(',', ' ').split()
            if _sday and _IB_DATE_RE.match(_sday[0]):
                _prev = out['first_seen'].get(_sroot)
                if _prev is None or _sday[0] < _prev:
                    out['first_seen'][_sroot] = _sday[0]
        if (sec == 'Trades'
                and g('DataDiscriminator') in ('Order', 'Trade')):
            # Quantity per (category, symbol, trade day) and detail
            # level: an execution-level 'Trade' row is a duplicate only
            # when an 'Order' row covers the same symbol and day.
            _okey = (g('Asset Category'), g('Symbol'),
                     (g('Date/Time').replace(',', ' ').split() or [''])[0])
            try:
                _oq = parse_strict_number(g('Quantity'), field='Quantity',
                                          where=where)
            except BrokerageParseError:
                _oq = 0.0
            _lv = out['order_levels'].setdefault(_okey, {})
            _lv[g('DataDiscriminator')] = _lv.get(
                g('DataDiscriminator'), 0.0) + _oq
        acct = g('Account')
        if acct and 'Total' not in acct:
            out['accounts'].add(acct)
        if sec == 'Statement' and g('Field Name') == 'Title':
            out['title'] = g('Field Value')
        elif sec == 'Statement' and g('Field Name') == 'BrokerName':
            # The IB entity that carries the account ("Interactive
            # Brokers Canada Inc.", "Interactive Brokers LLC"): the
            # payer of a payment in lieu (a neutral fact; s.260 is a
            # Canada-project rule in lib/income_dating).
            out['broker_name'] = g('Field Value')
        elif sec == 'Account Information':
            fn = g('Field Name')
            if fn == 'Account':
                v = g('Field Value').split()[0] if g('Field Value') else ''
                if v:
                    out.setdefault('account_field', set()).add(v)
            elif fn == 'Accounts Included':
                out['accounts_included'] = g('Field Value')
                for a in g('Field Value').split(','):
                    if a.strip():
                        out['accounts'].add(a.strip())
        elif sec == 'Financial Instrument Information':
            cat = g('Asset Category')
            mult_raw = g('Multiplier')
            mult_bad = ''
            try:
                mult = (parse_strict_number(mult_raw, field='Multiplier',
                                            where=where)
                        if mult_raw else None)
            except BrokerageParseError:
                mult, mult_bad = None, mult_raw
            info = {'mult': mult, 'mult_bad': mult_bad,
                    'name': g('Description'), 'expiry': g('Expiry'),
                    'conid': g('Conid'), 'exch': g('Listing Exch'),
                    'isin': g('Security ID'), 'underlying': g('Underlying')}
            texts = [s.strip() for s in g('Symbol').split(',')
                     if s.strip()]
            if cat in ('Stocks', 'Warrants'):
                for t in texts:
                    _root = re.sub(r'\s+', '.', t)
                    if info['isin']:
                        out['stock_isins'].setdefault(_root, set()).add(
                            info['isin'].upper())
                    if info['conid']:
                        out['stock_conid_syms'].setdefault(
                            info['conid'], set()).add(_root)
            elif info['conid'] and info['underlying']:
                out['opt_underlying'].setdefault(info['conid'], set()).add(
                    info['underlying'])
            if g('Description'):
                texts.append(g('Description'))
            for t in texts:
                out['fii'].setdefault((cat, t), info)
                out['fii'].setdefault((cat, re.sub(r'\s+', ' ', t)), info)
            if cat == 'Equity and Index Options':
                for t in texts:
                    m = _IB_FII_OCC_RE.match(t)
                    if m:
                        occ_by_conid.setdefault(info['conid'], set()).add(
                            m.groups())
                        contract_conids.setdefault(
                            m.groups()[1:], {}).setdefault(
                            m.group(1), set()).add(info['conid'])
        elif sec == 'Cash Report':
            out['has_cash_report'] = True
            cur = g('Currency')
            line = g('Currency Summary')
            if not cur or cur == 'Base Currency Summary' or not line:
                continue
            out['cash_currencies'].add(cur)
            try:
                tot = parse_strict_number(g('Total'), field='Total',
                                          where=f"{where}: Cash Report "
                                                f"{line} {cur}",
                                          allow_blank=True, blank=0.0)
            except BrokerageParseError:
                # Kept, not skipped: a reconciled line whose broker
                # total cannot be read fails the reconciliation by NAME
                # (it used to compare against an implicit 0.00 — a
                # misleading error, or a silent pass; audit S059-14).
                out['cash_bad'][(line, cur)] = g('Total')
                continue
            out['cash'][(line, cur)] = out['cash'].get((line, cur),
                                                       0.0) + tot
    for _sym, _cat, _cur in out['held_rows']:
        out['stock_listing'].setdefault(
            re.sub(r'\s+', '.', _sym), _ib_stock_symbol(_cat, _sym, _cur,
                                                        out['fii']))
    out['occ_by_conid'] = occ_by_conid
    out['root_alias'], out['alias_conids'] = _root_aliases(
        occ_by_conid, out['opt_underlying'])
    out['contract_conids'] = contract_conids
    for (_cat, _sym, _day), _lv in out['order_levels'].items():
        if ('Order' in _lv and 'Trade' in _lv
                and abs(_lv['Order'] - _lv['Trade']) > 1e-6):
            raise BrokerageParseError(
                f"{where}: Trades {_cat} {_sym} on {_day}: the Order rows "
                f"total {_lv['Order']:g} but the execution-level Trade "
                f"rows total {_lv['Trade']:g} — the two detail levels "
                f"disagree; refusing to pick one. Export one detail "
                f"level (Order) only.")
    # A consolidated statement names its members in "Accounts
    # Included"; a single-account one only in "Account".
    if not out['accounts_included']:
        out['accounts'] |= out.get('account_field', set())
    return out

# `Commission Adjustments` description: `Refund (QZA, -150 2024-02-07)`
# — the ticker sits first inside the parenthetical.
_IB_COMM_ADJ_TICKER_RE = re.compile(r'\(\s*([A-Z0-9][A-Z0-9 .\-]*?)\s*,')
# ... and the trade it adjusts: `Refund (QZA, 250, 2024-05-14)`.
_IB_COMM_ADJ_TRADE_RE = re.compile(
    r'\(\s*([A-Z0-9][A-Z0-9 .\-]*?)\s*,\s*([-+]?[\d,]*\.?\d+)\s*,?\s*'
    r'(\d{4}-\d{2}-\d{2})\s*\)')


def _ib_trade_key(t: Dict[str, Any], negate: bool = False) -> tuple:
    """A Trades row's identity across overlapping statements of one
    account: symbol, currency, trade date and clock time, quantity and
    price (the money columns may differ — a refund folded in one copy).
    `negate` gives the key of the original a `Ca` row reverses."""
    q = float(t.get('quantity') or 0.0)
    return (t.get('symbol'), t.get('currency'), t.get('date'),
            t.get('time') or '', round(-q if negate else q, 9),
            round(float(t.get('price') or 0.0), 8))


def _ib_xfer_cancels(t: Dict[str, Any], leg: Dict[str, Any]) -> bool:
    """True when `leg` (a Transfers `Ca` kept as a reversing leg, its
    description suffixed ' (Ca)') reverses the TRANSFER row `t`: same
    symbol and description, the negated quantity, dated on or before
    the cancellation."""
    desc = leg.get('description') or ''
    return (t is not leg and t.get('action') == 'TRANSFER'
            and t.get('symbol') == leg.get('symbol')
            and desc.endswith(' (Ca)')
            and t.get('description') == desc[:-len(' (Ca)')]
            and (t.get('date') or '') <= (leg.get('date') or '')
            and abs(float(t.get('quantity') or 0)
                    + float(leg.get('quantity') or 0)) < 1e-9)


def _ib_in_period(ex, t: Dict[str, Any]) -> bool:
    """True unless `t` is dated before its statement's period (a
    rebook of an earlier statement's row)."""
    ps = getattr(ex, 'period_start', '') or ''
    return not ps or (t.get('date') or '') >= ps


def _ib_refund_hits(adj: Dict[str, Any], txs) -> List[Dict[str, Any]]:
    """The trade(s) of `txs` a Commission Adjustments refund names
    (ticker, signed quantity, trade date). An exact quantity first; else
    the ONE same-symbol, same-date, same-sign trade whose quantity
    covers the named one — IB names an execution of a statement that
    lists only the Order row (a 'Refund (QZW, -40, ...)' for a
    -50 order, audit A2-0604)."""
    def _base(t):
        return (t.get('action') == 'BUYSELL'
                and t.get('type') != TRADE_CANCEL_TYPE
                and t.get('currency') == adj['currency']
                and _split_known_ext(t.get('symbol'))[0] == adj['ticker']
                and adj['trade_date'] in (
                    t.get('date'), (t.get('broker_time') or '')[:10]))
    cands = [t for t in txs if _base(t)]
    exact = [t for t in cands
             if abs(float(t.get('quantity') or 0) - adj['qty']) < 1e-9]
    if exact:
        return exact
    q = adj['qty']
    return [t for t in cands
            if (float(t.get('quantity') or 0) > 0) == (q > 0)
            and abs(float(t.get('quantity') or 0)) >= abs(q) - 1e-9]


def _ib_option_ref(ex, raw: str, fii, root_alias, aliased_roots,
                   where: str) -> Optional[str]:
    """The OCC symbol (no exchange suffix) of an equity option named in
    a Commission Adjustments description ('QZK 21MAR25 10 C'), or None
    for a stock ticker: the refund of an option trade never matched the
    OCC-coded trade (audit A2-1032)."""
    from taxjson.lib.core import is_option_symbol
    if not re.search(r'\s', raw or ''):
        return None
    occ = ex._option_symbol(raw, 'Equity and Index Options', fii,
                            root_alias, aliased_roots, where, strict=False)
    return occ if is_option_symbol(occ) else None


def _ib_fold_refund(t: Dict[str, Any], amount: float) -> None:
    """Net a commission refund (cash, positive) into its trade: the fee
    falls, a purchase costs less and a sale brings in more
    (tax-logic CA-ACB-COMMREFUND / US-BASIS-COMMREFUND)."""
    t['fee'] = round(float(t.get('fee') or 0.0) - amount, 10)
    if float(t.get('quantity') or 0) > 0:
        t['net_amount'] = round(t['net_amount'] - amount, 10)
    else:
        t['net_amount'] = round(t['net_amount'] + amount, 10)


class IbBrokerage(BaseBrokerage):
    # How an IB "(Return of Capital)" distribution from a NON-Canadian
    # issuer (ISIN country != CA) is booked: "acb" (default — the
    # issuer's own designation, a basis reduction: the neutral fact) or
    # "dividend" (ITA s.90(1): a non-resident corporation's distribution
    # is a dividend unless it really reduces paid-up capital, which a US
    # "return of capital" — a distribution beyond E&P — need not; s.90(2)
    # is the foreign-AFFILIATE rule and does not apply to a portfolio
    # holding. Canadian law, so only a Canada project asks for it). Set by taxjson-brokerage --foreign-roc / --country,
    # which `taxjson run` passes from the project (lib/country
    # .foreign_roc_mode; partition INPUTS-03).
    foreign_return_of_capital = 'acb'
    # 'trade' (default) | 'next_day': settle date of futures and futures
    # options ([settings] futures_settle, passed by taxjson-brokerage).
    futures_settle = 'trade'

    # Account-wide context (set by taxjson-brokerage from prepare_files).
    account_context = None
    # Set by taxjson-brokerage when it will run reconcile_files: the
    # Corporate Actions side-effect lines (stock-dividend ATTENTION,
    # cash-takeover NOTE, ...) wait in `ca_side_effects` until the
    # cross-statement Ca pass, so an event another statement cancels
    # prints nothing (re-audit A2-1091).
    defer_ca_messages = False
    ca_side_effects = None

    @classmethod
    def prepare_files(cls, paths, tax_year=None, combined=False,
                      taxable=None) -> Dict[str, Any]:
        """Read ALL of one account's IB statements once, before any is
        parsed: statement periods (coverage check below, per broker
        account and against `tax_year` when taxjson-brokerage passes it)
        and the facts a per-file parse cannot see alone. Account-level
        warnings print here, once."""
        ctx: Dict[str, Any] = {
            'periods': [], 'occ_by_conid': {}, 'contract_conids': {},
            'opt_underlying': {}, 'stock_conid_syms': {},
            'stock_isins': {}, 'held': set(), 'posted_dividends': [],
            'tender_parked': {}, 'accrual_facts': [],
            'file_accounts': {}, 'first_seen': {}, 'stock_listing': {}}
        for path in paths:
            name = Path(path).name
            try:
                rows = cls._read_rows(Path(path))
                pre = _ib_prescan(rows, shown_name(path))
            except (OSError, UnicodeError, BrokerageParseError):
                continue                 # parse_file reports it
            accts = frozenset(pre['accounts'])
            ctx['file_accounts'][name] = accts
            for row in rows:
                if (len(row) >= 4 and row[0] == 'Statement'
                        and row[1] == 'Data' and row[2] == 'Period'):
                    span = _ib_period(row[3])
                    if span:
                        ctx['periods'].append((shown_name(path), *span,
                                               accts))
                    break
            # Identity facts learned from ANY of the account's
            # statements: an option root alias listed only in last
            # year's instrument list (audit S059-15), a stock renamed
            # between statements (S060-17), the listings held for the
            # income rebind (S060-00).
            for conid, occs in pre['occ_by_conid'].items():
                ctx['occ_by_conid'].setdefault(conid, set()).update(occs)
            for key, by_root in pre['contract_conids'].items():
                tgt = ctx['contract_conids'].setdefault(key, {})
                for root, ids in by_root.items():
                    tgt.setdefault(root, set()).update(ids)
            for conid, unds in pre['opt_underlying'].items():
                ctx['opt_underlying'].setdefault(conid, set()).update(unds)
            for conid, syms in pre['stock_conid_syms'].items():
                ctx['stock_conid_syms'].setdefault(conid, set()).update(syms)
            for root, ids in pre['stock_isins'].items():
                ctx['stock_isins'].setdefault(root, set()).update(ids)
            for root, d in pre['first_seen'].items():
                if d < ctx['first_seen'].get(root, '9999'):
                    ctx['first_seen'][root] = d
            for root, full in pre['stock_listing'].items():
                ctx['stock_listing'].setdefault(root, full)
            for sym, cat, cur in pre['held_rows']:
                ctx['held'].add(_ib_stock_symbol(cat, sym, cur, pre['fii']))
            # Postings and accrual facts carry their statement's broker
            # accounts: another IB account's dividend never pays this
            # account's accrual (audit A2-1035 / A2-1039).
            ctx['posted_dividends'].extend(
                (name, t, d, accts) for t, d in _ib_posted_dividends(rows))
            ctx['accrual_facts'].extend(
                dict(m, file=name, accounts=accts)
                for m in _ib_accrual_facts(rows))
            for sym, n in _ib_tender_parked(rows, pre['fii']).items():
                ctx['tender_parked'][sym] = (
                    ctx['tender_parked'].get(sym, 0.0) + n)
        _warn_coverage_gaps(ctx['periods'], tax_year=tax_year)
        _accts = set().union(*ctx['file_accounts'].values()) \
            if ctx['file_accounts'] else set()
        _spread = len(_accts) > 1 and not any(
            a >= _accts for a in ctx['file_accounts'].values())
        if _spread and combined:
            # combined_broker_accounts = true: a NOTE (refused on a
            # sheltered label — no plan per account in IB statements).
            _masked = sorted(_mask_account(a) for a in _accts)
            if taxable is False:
                raise combined_accounts_refusal(
                    "this account's IB statements", 'IB', _masked,
                    "an IB statement does not say which plan each "
                    "account is")
            print(combined_accounts_note("this account's IB statements",
                                         'IB', _masked), file=sys.stderr)
        elif _spread:
            # Separate statements of several IB accounts in one label:
            # booked as ONE account (right only for one tax entity).
            print(f"{ATTENTION_PREFIX} this account's IB statements "
                  f"belong to {len(_accts)} IB accounts ("
                  f"{', '.join(sorted(_mask_account(a) for a in _accts))}"
                  f") — every row is booked to ONE account label. That is "
                  f"right only when they are one tax entity (e.g. two "
                  f"taxable margin accounts); give a sheltered (tax-"
                  f"advantaged) account its own folder.", file=sys.stderr)
        _warn_stock_aliases(ctx['stock_conid_syms'],
                            'the account\'s IB statements',
                            first_seen=ctx['first_seen'],
                            listing=ctx['stock_listing'],
                            mapping=_project_ticker_map(paths))
        return ctx

    @classmethod
    def reconcile_files(cls, parsed) -> None:
        """See `_reconcile_files`; then every statement's deferred
        Corporate Actions side-effect lines print, minus the events the
        cross-statement pass undid (re-audit A2-1091)."""
        try:
            cls._reconcile_files(parsed)
        finally:
            for _p, ex, _t in parsed:
                flush = getattr(ex, 'ca_side_effects', None)
                if flush is not None:
                    ex.ca_side_effects = None
                    flush()
                name = getattr(ex, 'deferred_skip_summary', None)
                if name is not None:
                    ex.deferred_skip_summary = None
                    ex.emit_skip_summary(name)

    def resolve_unmatched_ca(self, key_fn, key) -> None:
        """A `Ca` row this statement could not pair was paired by the
        cross-statement pass: it is consumed, not a skipped row (its
        'original is not in this statement (see warning)' skip line
        named a warning that never comes)."""
        for i, ca in enumerate(getattr(self, 'unmatched_ca', None) or ()):
            if key_fn(ca) != key:
                continue
            self.unmatched_ca.pop(i)
            cat = _IB_UNMATCHED_CA_SKIP
            if self._skip_counts.get(cat):
                self._skip_counts[cat] -= 1
                if not self._skip_counts[cat]:
                    del self._skip_counts[cat]
                self.note_row_consumed()
            return

    @classmethod
    def _reconcile_files(cls, parsed) -> None:
        """Cross-statement pass over one account's parsed statements
        (taxjson-brokerage calls it with [(path, extractor, txs)], every
        txs list mutated in place), before the books de-duplicate the
        overlapping rows.

        Each statement pairs a `Ca` cancellation with its original,
        folds a commission refund into its trade and joins a cash in
        lieu to its split ON ITS OWN. An overlapping download of the
        same account taken earlier (before IB posted the cancellation or
        the refund) still holds the unadjusted row, which no longer
        matches the adjusted copy, so dedup kept both: a cancelled split
        or sale stayed booked, a refunded buy was booked twice (audit
        A2-0023 / A2-0024 / A2-0088 / A2-0258 / A2-0259 / A2-1038). And
        an adjustment whose row is in ANOTHER statement (a December
        trade refunded in January, a Dec 30 split's cash in lieu paid
        Jan 5) was never applied (A2-0605 / A2-1032 / A2-1036 /
        A2-1037). Here every adjustment one statement made is repeated
        on the account's other statements that hold the same original
        row but not the adjustment, and the unmatched ones are offered
        to the other statements. Statements of DIFFERENT broker accounts
        (Account Information) never touch each other."""
        files = [(p, ex, txs) for p, ex, txs in parsed
                 if isinstance(ex, IbBrokerage)]
        if not files:
            return

        def same_acct(a, b) -> bool:
            x, y = a.statement_accounts(), b.statement_accounts()
            return not x or not y or bool(x & y)

        def nm(i) -> str:
            return shown_name(files[i][0])

        # --- 1. Corporate Actions `Ca` rows.
        def _ca_key(ca):
            return (ca['desc'], ca['date'], round(ca['qty'], 9),
                    ca.get('currency') or '', ca.get('cat') or '')

        cas: Dict[tuple, Dict[str, Any]] = {}
        for i, (_p, ex, _t) in enumerate(files):
            for ca in getattr(ex, 'ca_seen', None) or ():
                e = cas.setdefault(_ca_key(ca), {
                    'ca': ca, 'odate': None, 'seen': set()})
                e['seen'].add(i)
            for ca, odate in getattr(ex, 'ca_pairs', None) or ():
                cas[_ca_key(ca)]['odate'] = odate
        for e in cas.values():
            ca, seen = e['ca'], e['seen']
            others = [j for j in range(len(files) - 1, -1, -1)
                      if j not in seen and any(
                          same_acct(files[j][1], files[k][1])
                          for k in seen)]
            odate = e['odate']
            undone = []
            if odate is None:
                # Paired nowhere: the original sits in another
                # statement — the latest one first (audit S059-04).
                for j in others:
                    eff = files[j][1].ca_undo(ca)
                    if eff:
                        odate = eff['date']
                        undone.append(j)
                        break
            for j in others:
                if j in undone or odate is None:
                    continue
                if files[j][1].ca_undo(ca, exact_date=odate):
                    undone.append(j)
            src = nm(min(seen))
            for j in undone:
                print(f"note: {src}: IB cancelled (Ca) {ca['desc']!r} "
                      f"({ca['qty']:g} on {ca['date']}); its original in "
                      f"{nm(j)} is undone.", file=sys.stderr)
            if odate is None:
                # No statement holds an original in its period: the Ca
                # undoes the row dated before its own statement's period
                # (held by parse_file, audit A2-1559) — on every copy.
                for k in sorted(seen):
                    eff = files[k][1].ca_undo(ca, rebook_ok=True)
                    if eff:
                        odate = eff['date']
                        print(f"note: {nm(k)}: IB cancelled (Ca) "
                              f"{ca['desc']!r} ({ca['qty']:g} on "
                              f"{ca['date']}); no other statement holds "
                              f"its original — the row of {eff['date']} "
                              f"in this statement is undone.",
                              file=sys.stderr)
            if odate is None:
                print(unmatched_ca_warning(ca), file=sys.stderr)
            else:
                for k in seen:
                    files[k][1].resolve_unmatched_ca(_ca_key, _ca_key(ca))

        # --- 2. Cash in lieu and its split.
        cils: Dict[tuple, Dict[str, Any]] = {}
        for i, (_p, ex, _t) in enumerate(files):
            open_keys = {(c['symbol'], c['date'], round(c['frac'], 6))
                         for c in getattr(ex, 'unmatched_cil', None) or ()}
            for c in getattr(ex, 'unmatched_cil', None) or ():
                cils.setdefault((c['symbol'], c['date'],
                                 round(c['frac'], 6)),
                                {'c': c, 'joined': False, 'seen': set()})
            for key in getattr(ex, 'cil_seen', None) or ():
                e = cils.setdefault(key, {
                    'c': {'symbol': key[0], 'date': key[1],
                          'frac': key[2], 'where': nm(i)},
                    'joined': False, 'seen': set()})
                e['seen'].add(i)
                if key not in open_keys:
                    e['joined'] = True
        for key, e in cils.items():
            hit = False
            for j, (_p, ex, _t) in enumerate(files):
                if j in e['seen'] or not any(
                        same_acct(ex, files[k][1]) for k in e['seen']):
                    continue
                if ex.cil_join(e['c']):
                    hit = True
                    print(f"note: {nm(j)}: {key[0]}: cash in lieu of "
                          f"{key[2]:g} share(s) on {key[1]} (in "
                          f"{e['c']['where']}) joined the split it "
                          f"settles.", file=sys.stderr)
            if not hit and not e['joined']:
                print(_ib_cil_unmatched_note(e['c']), file=sys.stderr)

        # --- 3. Trades `Ca` cancellations.
        pairs: Dict[tuple, int] = {}
        for _p, ex, _t in files:
            per: Dict[tuple, int] = {}
            for o, _c in getattr(ex, 'trade_pairs', None) or ():
                k = _ib_trade_key(o)
                per[k] = per.get(k, 0) + 1
            for k, n in per.items():
                pairs[k] = max(pairs.get(k, 0), n)
        for k, n in pairs.items():
            owners = [i for i, (_p, ex, _t) in enumerate(files)
                      if any(_ib_trade_key(o) == k
                             for o, _c in getattr(ex, 'trade_pairs', ()))]
            for j, (_p, ex, txs) in enumerate(files):
                if (j in owners or k in getattr(ex, 'trade_ca_keys', ())
                        or not any(same_acct(ex, files[i][1])
                                   for i in owners)):
                    continue
                hits = [t for t in txs
                        if t.get('action') in ('BUYSELL', 'ASSIGN')
                        and t.get('type') != TRADE_CANCEL_TYPE
                        and _ib_trade_key(t) == k][-n:]
                if not hits:
                    continue
                gone = {id(t) for t in hits}
                txs[:] = [t for t in txs if id(t) not in gone]
                print(f"note: {nm(j)}: the {k[0]} trade of {k[4]:g} @ "
                      f"{k[5]:g} on {k[2]} was cancelled (Ca) by IB in "
                      f"{nm(owners[0])} — dropped from this overlapping "
                      f"statement too.", file=sys.stderr)

        # --- 3b / 4b. A Trades or Transfers `Ca` row that parse_file
        # held: IB cancelled a row of an EARLIER statement and rebooked
        # it in this one, both dated before this statement's period. The
        # Ca cancels the original in the account's other statements —
        # on every one that holds it in its period (overlapping copies)
        # — and the rebook stays. Paired in its own statement (as a
        # lone statement is) only when no statement holds the original
        # (audit A2-0886 / A2-1560). It used to pair with the rebook in
        # its own statement, and step 3 then dropped the earlier
        # original as an overlapping copy: neither was booked.
        def _held_cross(attr, match, what):
            done: set = set()
            for i, (_p, ex, txs) in enumerate(files):
                for ca in list(getattr(ex, attr, None) or ()):
                    if not any(t is ca for t in txs):
                        continue
                    key = (ca.get('symbol'), ca.get('currency'),
                           ca.get('date'), ca.get('time') or '',
                           round(float(ca.get('quantity') or 0), 9),
                           ca.get('description'))
                    hit = key in done
                    for j, (_p2, ex2, txs2) in enumerate(files):
                        if hit or j == i or not same_acct(ex, ex2):
                            continue
                        cands = [t for t in txs2 if match(t, ca)
                                 and _ib_in_period(ex2, t)]
                        if not cands:
                            continue
                        same = [t for t in cands
                                if t.get('date') == ca.get('date')
                                and (t.get('time') or '')
                                == (ca.get('time') or '')]
                        orig = (same or cands)[-1]
                        for k, (_p3, ex3, txs3) in enumerate(files):
                            if k == i or not same_acct(ex, ex3):
                                continue
                            dup = [t for t in txs3 if match(t, ca)
                                   and _ib_in_period(ex3, t)
                                   and t.get('date') == orig.get('date')
                                   and (t.get('time') or '')
                                   == (orig.get('time') or '')]
                            if dup:
                                txs3[:] = [t for t in txs3
                                           if t is not dup[-1]]
                                print(f"note: {nm(i)}: IB cancelled (Ca) "
                                      f"the {ca['symbol']} {what} of "
                                      f"{-float(ca['quantity']):g} on "
                                      f"{orig.get('date')} — its original "
                                      f"in {nm(k)} is dropped; the "
                                      f"rebooked row in {nm(i)} is kept.",
                                      file=sys.stderr)
                        hit = True
                    if hit:
                        done.add(key)
                        txs[:] = [t for t in txs if t is not ca]

        from taxjson.lib.trade_cancel import cancels as _cancels
        _held_cross('held_trade_cas', _cancels, 'trade')
        _held_cross('held_xfer_cas', _ib_xfer_cancels, 'transfer')
        for _p, ex, txs in files:
            nm_ = shown_name(_p)
            held_t = [c for c in getattr(ex, 'held_trade_cas', None) or ()
                      if any(t is c for t in txs)]
            if held_t:
                kept, tpairs, unpaired = pair_cancellations(txs)
                txs[:] = kept
                for _o, _c in tpairs:
                    print(f"note: {nm_}: IB cancelled (Ca) the "
                          f"{_o['symbol']} trade of {_o['quantity']:g} @ "
                          f"{_o['price']:g} on {_o['date']} — no other "
                          f"statement holds its original; the trade and "
                          f"its cancellation are both dropped.",
                          file=sys.stderr)
                for _c in unpaired:
                    if any(_c is h for h in held_t):
                        print(f"note: {nm_}: IB cancelled (Ca) a "
                              f"{_c['symbol']} trade of "
                              f"{-_c['quantity']:g} @ {_c['price']:g} on "
                              f"{_c['date']} whose original row is in "
                              f"none of the account's statements — kept "
                              f"as a cancellation leg.", file=sys.stderr)
            for leg in [c for c in getattr(ex, 'held_xfer_cas', None) or ()
                        if any(t is c for t in txs)]:
                cands = [t for t in txs if _ib_xfer_cancels(t, leg)]
                same = [t for t in cands if t.get('date') == leg['date']]
                if cands:
                    orig = (same or cands)[-1]
                    txs[:] = [t for t in txs
                              if t is not orig and t is not leg]
                    print(f"note: {nm_}: IB cancelled (Ca) the "
                          f"{leg['symbol']} transfer of "
                          f"{-leg['quantity']:g} on {orig.get('date')} — "
                          f"no other statement holds its original; the "
                          f"transfer and its cancellation are both "
                          f"dropped.", file=sys.stderr)

        # --- 4. Transfers `Ca` cancellations.
        xpairs: Dict[tuple, tuple] = {}
        for i, (_p, ex, _t) in enumerate(files):
            for ok, ck in getattr(ex, 'xfer_pairs', None) or ():
                xpairs.setdefault(ok, (ck, i))
        for ok, (ck, owner) in xpairs.items():
            for j, (_p, ex, txs) in enumerate(files):
                if (j == owner or ck in getattr(ex, 'xfer_ca_keys', ())
                        or not same_acct(ex, files[owner][1])):
                    continue
                hit = next((t for t in reversed(txs)
                            if t.get('action') == 'TRANSFER'
                            and (t.get('symbol'), t.get('description'),
                                 t.get('date'),
                                 float(t.get('quantity') or 0)) == ok),
                           None)
                if hit is None:
                    continue
                txs[:] = [t for t in txs if t is not hit]
                print(f"note: {nm(j)}: the {ok[0]} transfer of {ok[3]:g} "
                      f"on {ok[2]} was cancelled (Ca) by IB in "
                      f"{nm(owner)} — dropped from this overlapping "
                      f"statement too.", file=sys.stderr)

        # --- 5. Commission refunds.
        folds: Dict[tuple, tuple] = {}
        for i, (_p, ex, _t) in enumerate(files):
            for f in getattr(ex, 'refund_folds', None) or ():
                folds.setdefault(f['refund'], (f, i))

        def _drop_fee_rows(rkey):
            for _p, ex, txs in files:
                for adj in getattr(ex, 'unmatched_refunds', None) or ():
                    if adj['key'] == rkey:
                        txs[:] = [t for t in txs if t is not adj['fee_tx']]

        for rkey, (f, owner) in folds.items():
            for j, (_p, ex, txs) in enumerate(files):
                if (j == owner or rkey in getattr(ex, 'refund_seen', ())
                        or not same_acct(ex, files[owner][1])):
                    continue
                hit = next((t for t in txs
                            if t.get('action') == 'BUYSELL'
                            and _ib_trade_key(t) == f['key']
                            and abs(float(t.get('fee') or 0.0)
                                    - f['fee']) < 1e-9), None)
                if hit is not None:
                    _ib_fold_refund(hit, f['amount'])
                    print(f"note: {nm(j)}: the commission refund of "
                          f"{f['amount']:+.2f} on the {f['key'][0]} trade "
                          f"of {f['key'][2]} (in {nm(owner)}) is folded "
                          f"into this overlapping statement's copy too.",
                          file=sys.stderr)
            # An overlapping copy of the REFUND (its trade not in that
            # statement) must not stay as a FEE row next to the fold.
            _drop_fee_rows(rkey)
        open_refunds: Dict[tuple, tuple] = {}
        for i, (_p, ex, _t) in enumerate(files):
            for adj in getattr(ex, 'unmatched_refunds', None) or ():
                if adj['key'] not in folds:
                    open_refunds.setdefault(adj['key'], (adj, i))
        for rkey, (adj, owner) in open_refunds.items():
            hits = []
            for j, (_p, ex, txs) in enumerate(files):
                if j != owner and same_acct(ex, files[owner][1]):
                    hits += _ib_refund_hits(adj, txs)
            keys = {_ib_trade_key(t) for t in hits}
            if len(keys) == 1:
                for t in hits:
                    _ib_fold_refund(t, adj['amount'])
                _drop_fee_rows(rkey)
                print(f"note: {nm(owner)}: commission adjustment of "
                      f"{adj['amount']:+.2f} {adj['currency']} on "
                      f"{adj['date']} ({adj['ticker']} {adj['qty']:g} on "
                      f"{adj['trade_date']}) folded into its trade in the "
                      f"account's other statement.", file=sys.stderr)
            else:
                print(f"note: {nm(owner)}: commission adjustment of "
                      f"{adj['amount']:+.2f} {adj['currency']} on "
                      f"{adj['date']} ({adj['ticker']} {adj['qty']:g} on "
                      f"{adj['trade_date']}) matches "
                      f"{'no' if not keys else len(keys)} trade(s) in "
                      f"the account's statements — kept as a FEE row, "
                      f"outside the trade's cost; adjust the trade by "
                      f"hand if it is in a taxable account.",
                      file=sys.stderr)

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _read_rows(path: Path) -> List[List[str]]:
        """All CSV rows. utf-8-sig handles a BOM gracefully — IB's web
        export occasionally ships one and plain utf-8 reads it as a data
        byte, breaking the very first section-tag match; a UTF-16 BOM
        (a spreadsheet's "Unicode text" save) is decoded instead of
        failing on byte 0xFF."""
        from taxjson.lib.brokerages.base import decode_broker_text
        text = decode_broker_text(Path(path).read_bytes(), shown_name(path))
        return list(csv.reader(io.StringIO(text, newline='')))

    @staticmethod
    def _cell(row: List[str], header_map: Dict[str, int], col: str) -> str:
        i = header_map.get(col)
        v = row[i].strip() if i is not None and i < len(row) else ''
        return _norm_ccy(v) if col == 'Currency' else v

    @staticmethod
    def _check_statement_kind(pre: Dict[str, Any], path: Path) -> None:
        """Refuse an IB report that shares the Activity Statement's CSV
        shape but not its meaning: a Realized Summary lists realized
        LOTS, so reading it as activity books phantom trades and none of
        the income."""
        title = (pre.get('title') or '').strip()
        if not title or 'activity' in title.lower():
            return
        if _IB_REFUSED_TITLE_RE.search(title):
            raise BrokerageParseError(
                f"{shown_name(path)}: this is an IB {title!r} report, not an "
                f"Activity Statement — refusing to read it as trades and "
                f"income. Download Reports > Statements > Activity (CSV) "
                f"instead.")
        print(f"warning: {shown_name(path)}: IB statement title {title!r} is not "
              f"'Activity Statement' — parsing it as one; check the "
              f"result.", file=sys.stderr)

    @staticmethod
    def _multiplier(asset_cat: str, raw_symbol: str, fii: Dict[tuple, Any],
                    where: str) -> float:
        """Contract size for a row: IB's own Financial Instrument
        Information Multiplier when the statement lists the instrument
        (1000 for a crude-oil future, 50 for an index e-mini, 0.1 for a
        micro future, equity option 100); otherwise 1 per share/warrant and 100 per equity option.
        A futures / futures-option contract size cannot be guessed, so
        its absence fails the parse."""
        s = (raw_symbol or '').strip()
        info = (fii.get((asset_cat, s))
                or fii.get((asset_cat, re.sub(r'\s+', ' ', s))))
        if info and info.get('mult'):
            return float(info['mult'])
        if (info and info.get('mult_bad')
                and asset_cat not in ('Stocks', 'Warrants')):
            # The statement names a derivative's contract size in a
            # form the parser cannot read: never fall back to the 100
            # guess (S059-14). A share is 1 whatever the cell says.
            raise BrokerageParseError(
                f"{where}: {asset_cat} {s!r}: the Financial Instrument "
                f"Information Multiplier {info['mult_bad']!r} is not a "
                f"number this parser reads — refusing to guess the "
                f"contract size.")
        if asset_cat in ('Stocks', 'Warrants'):
            return 1.0
        if asset_cat == 'Equity and Index Options':
            return 100.0
        raise BrokerageParseError(
            f"{where}: {asset_cat} {s!r} has no multiplier in the "
            f"statement's Financial Instrument Information section — a "
            f"futures contract size (1000 for crude oil, 50 for an "
            f"index e-mini, ...) cannot be guessed. Export the full Activity Statement "
            f"(it lists every instrument).")

    @staticmethod
    def _security_name(asset_cat: str, raw_symbol: str,
                       fii: Dict[tuple, Any]) -> str:
        """The Financial Instrument Information name of a stock row
        ('SAMPLE PLATFORMS INC-CDR'), or '' — the row's description stays
        the raw symbol (the security overrides key on it)."""
        if asset_cat not in ('Stocks', 'Warrants'):
            return ''
        s = (raw_symbol or '').strip()
        info = (fii.get((asset_cat, s))
                or fii.get((asset_cat, re.sub(r'\s+', ' ', s))) or {})
        name = (info.get('name') or '').strip()
        return name if name and name != s else ''

    def _check_symbol_tag(self, root: str, where: str) -> None:
        """Say once per symbol when an IB stock symbol carries a
        currency/venue tag (ABH.CAD): it is booked as a security of
        its own, apart from the plain listing."""
        root = (root or '').strip().replace(' ', '.')
        if '.' not in root:
            return
        base, tag = root.rsplit('.', 1)
        if tag.upper() not in _IB_CURRENCY_TAGS:
            return
        seen = getattr(self, '_tag_warned', None)
        if seen is None:
            seen = self._tag_warned = set()
        if root in seen:
            return
        seen.add(root)
        print(f"warning: {where}: IB symbol {root!r} ends in the "
              f"currency/venue tag .{tag} — booked as a security of its "
              f"own, apart from {base}. If it is the same security (IB's "
              f"temporary line for a merger fraction, say), join or drop "
              f"it in ticker.map.", file=sys.stderr)

    def _check_trade_money(self, where: str, symbol: str, qty: float,
                           price: float, proceeds: float, comm: float,
                           mult: float, check_notional: bool,
                           exact: bool = False) -> None:
        """Per-row money check that FAILS CLOSED: a header-only column
        swap (Proceeds <-> Comm/Fee) or a wrong multiplier is refused
        instead of booked. A Proceeds sign that contradicts the quantity
        only warns (the magnitude is what is booked; the Cash Report
        reconciliation catches a real sign error)."""
        gross = abs(proceeds)
        if check_notional:
            expected = abs(qty) * abs(price) * mult
            # Futures are outside the Cash Report's Trades reconciliation
            # (they settle through "Cash Settling MTM"), so this check is
            # their only guard: cent-level, as IB's own futures rows are
            # exact. The 0.1% band let a CL leg move ~57 silently (audit
            # G7-3); securities keep it — the Cash Report backs them.
            tol = (max(0.02, 1e-6 * max(expected, gross)) if exact
                   else max(0.02, 0.001 * max(expected, gross)))
            if abs(gross - expected) > tol:
                raise BrokerageParseError(
                    f"{where}: {symbol}: |Proceeds| {gross:,.2f} is not "
                    f"|Quantity| {abs(qty):g} x T. Price {price:g} x "
                    f"multiplier {mult:g} = {expected:,.2f} — a swapped "
                    f"or mislabelled column, or a wrong contract "
                    f"multiplier; refusing to book it.")
            if ((qty > 0 and proceeds > 0.005)
                    or (qty < 0 and proceeds < -0.005)) and not (
                        price < 0 and exact) and not getattr(
                        self, '_sign_warned', False):
                self._sign_warned = True
                print(f"warning: {where}: {symbol}: Proceeds "
                      f"{proceeds:,.2f} has the opposite sign to IB's "
                      f"convention for Quantity {qty:g} (a buy's proceeds "
                      f"are negative); the magnitude is booked — check "
                      f"the export (first such row only).",
                      file=sys.stderr)
        # Commissions are small next to the trade: a percentage of the
        # gross plus a per-contract/per-share allowance and a floor.
        allowed = 0.25 * gross + abs(qty) + 10.0
        if abs(comm) > allowed:
            raise BrokerageParseError(
                f"{where}: {symbol}: Comm/Fee {comm:,.2f} is implausible "
                f"for a trade of gross {gross:,.2f} (qty {qty:g}) — a "
                f"swapped or mislabelled column; refusing to book it.")

    def _book_forex(self, row: List[str], header_map: Dict[str, int],
                    where: str, book) -> None:
        """A Trades/Forex conversion is not a tax event here, but its
        cash IS in the Cash Report's Trades and Commissions lines — book
        it for the reconciliation only."""
        self.require_columns(header_map, ('Symbol', 'Quantity',
                                          'Proceeds', 'Currency'),
                             section='Trades (Forex)', where=where)
        sym = self._cell(row, header_map, 'Symbol')
        base, _, quote = sym.partition('.')
        if not base or not quote:
            raise BrokerageParseError(
                f"{where}: Forex symbol {sym!r} is not BASE.QUOTE")
        qty = parse_strict_number(self._cell(row, header_map, 'Quantity'),
                                  field='Quantity', where=where)
        proceeds = parse_strict_number(
            self._cell(row, header_map, 'Proceeds'), field='Proceeds',
            where=where)
        book('Trades (Sales + Purchase)', quote, proceeds)
        book('Trades (Sales + Purchase)', base, qty)
        comm_col = next((c for c in header_map
                         if re.match(r'^Comm in [A-Z]{3}$', c or '')), None)
        if comm_col:
            book('Commissions', comm_col[-3:], parse_strict_number(
                self._cell(row, header_map, comm_col), field=comm_col,
                where=where, allow_blank=True, blank=0.0))
        elif 'Comm/Fee' in header_map:
            book('Commissions',
                 self._cell(row, header_map, 'Currency') or quote,
                 parse_strict_number(
                     self._cell(row, header_map, 'Comm/Fee'),
                     field='Comm/Fee', where=where, allow_blank=True,
                     blank=0.0))

    def _option_symbol(self, raw: str, asset_cat: str, fii: Dict[tuple, Any],
                       root_alias: Dict[str, str],
                       aliased_roots: Dict[str, str], where: str,
                       strict: bool = True) -> str:
        """OCC symbol for an IB option row ("ABC 15JAN27 12.5 P",
        futures-option monthly "CL MAR27 40 P", legacy "SPX 20241220 P
        4000"). The root is canonicalized through the statement's
        option-root aliases (one conid listed as QZD 251121P... and
        QZD1 251121P... after a corporate action), so the opening and
        the assigned leg share ONE symbol and the premium folds."""
        symbol = raw

        def _strike(text: str) -> str:
            # The shared strike reader refuses a decimal comma; name
            # the row so the user can find it (audit A2-1041).
            try:
                return option_strike_text(text)
            except BrokerageParseError as exc:
                raise BrokerageParseError(
                    f"{where}: {asset_cat} symbol {raw!r}: {exc}") from exc

        # Strikes may carry a thousands separator (5,000): OPTION_STRIKE_RE
        # tries the grouped form first; the commas are dropped below.
        opt_match = re.search(r'^(.+?)\s+(\d{2})([A-Z]{3})(\d{2})\s+' + OPTION_STRIKE_RE + r'\s+([PC])$', raw)
        opt_match_monthly = (re.search(r'^(.+?)\s+([A-Z]{3})(\d{2})\s+' + OPTION_STRIKE_RE + r'\s+([PC])$', raw)
                             if not opt_match else None)
        # Anchored at the end: a decimal-comma strike ('4,00') was read
        # as its integer part (audit A2-1041).
        opt_match_legacy = (re.search(r'^(.+?)\s+(\d{8})\s+([PC])\s+' + OPTION_STRIKE_RE + r'\s*$', raw)
                            if not (opt_match or opt_match_monthly) else None)
        if opt_match:
            base, day, mon, yr, strike, right = opt_match.groups()
            strike = _strike(strike)
            month = _MON_MAP.get(mon.upper())
            if month:
                if asset_cat == 'Equity and Index Options':
                    base = self._alias_root(base.strip(), f"{yr}{month}{day}",
                                            right, strike, root_alias,
                                            aliased_roots, where)
                base = base.replace(' ', '.')
                symbol = f"{base}{yr}{month}{day}{right}{encode_occ_strike(strike)}"
        elif opt_match_monthly:
            base, mon, yr, strike, right = opt_match_monthly.groups()
            strike = _strike(strike)
            month = _MON_MAP.get(mon.upper())
            if month:
                base = base.replace(' ', '.')
                # A monthly futures option names only the DELIVERY
                # month; the contract's real last trading day is in
                # the Financial Instrument Information (CL JAN26 ->
                # 2025-12-16). The old placeholder day "20" invented
                # an expiry — in the wrong month, possibly the wrong
                # tax year.
                s = raw.strip()
                info = (fii.get((asset_cat, s))
                        or fii.get((asset_cat, re.sub(r'\s+', ' ', s))))
                exp = (info or {}).get('expiry') or ''
                if _IB_DATE_RE.match(exp):
                    ymd = f"{exp[2:4]}{exp[5:7]}{exp[8:10]}"
                else:
                    ymd = f"{yr}{month}20"
                    if raw not in self._placeholder_warned:
                        self._placeholder_warned.add(raw)
                        print(f"warning: {where}: {raw!r}: no expiry in "
                              f"the Financial Instrument Information — "
                              f"placeholder day 20 used in the option "
                              f"symbol (it will not match a statement "
                              f"that has the real expiry).",
                              file=sys.stderr)
                symbol = f"{base}{ymd}{right}{encode_occ_strike(strike)}"
        elif opt_match_legacy:
            base, exp, right, strike = opt_match_legacy.groups()
            strike = _strike(strike)
            base = base.replace(' ', '.')
            symbol = f"{base}{exp[2:]}{right}{encode_occ_strike(strike)}"
        # Strip all spaces as a fallback / cleanup (an OCC-padded
        # symbol 'QZY   250321C00050000' is already the contract).
        symbol = symbol.replace(' ', '')
        if strict and not is_option_symbol(symbol):
            # An option description no form reads ('XSP 16JAN26 6,85 P',
            # a decimal-comma strike) became a raw non-option symbol —
            # a phantom security, silently (audit A2-1041).
            raise BrokerageParseError(
                f"{where}: {asset_cat} symbol {raw!r} is not an option "
                f"description this parser reads (UNDERLYING DDMMMYY "
                f"STRIKE P|C, a decimal point in the strike) — refusing "
                f"to guess the contract. Re-export the statement in "
                f"English.")
        return symbol

    def _alias_root(self, base: str, yymmdd: str, right: str, strike: str,
                    root_alias: Dict[str, str],
                    aliased_roots: Dict[str, str], where: str) -> str:
        canon = root_alias.get(base)
        if not canon:
            return base
        key = (yymmdd, right, encode_occ_strike(strike))
        conids = self._ib_pre.get('contract_conids', {}).get(key, {})
        a, c = conids.get(base, set()), conids.get(canon, set())
        if a and c and not (a & c):
            # Two DIFFERENT listed contracts share the key under both
            # roots (a new standard series next to the adjusted one):
            # they are not the same security — keep them apart.
            print(f"warning: {where}: option root {base} is an alias of "
                  f"{canon} elsewhere in this statement, but "
                  f"{base} {yymmdd}{right} is a different contract than "
                  f"{canon} {yymmdd}{right} — not merged.",
                  file=sys.stderr)
            return base
        if base not in aliased_roots:
            aliased_roots[base] = canon
            print(f"note: option root {base} is IB's post-corporate-"
                  f"action alias of {canon} (same contract id in the "
                  f"Financial Instrument Information) — booked as "
                  f"{canon} so its opening and closing/assigned legs "
                  f"share one symbol.", file=sys.stderr)
        return canon

    def _reconcile_cash_report(self, pre: Dict[str, Any],
                               booked: Dict[tuple, float],
                               path: Path) -> None:
        """Every money row the parser booked must reproduce IB's own
        Cash Report, per currency, within 0.02: Dividends, Payment In
        Lieu, Withholding Tax, Broker Interest, Other Fees, Commissions
        (+ Transaction Fees, a breakdown of the same charges) and Trades
        (Sales + Purchase). A mismatch means a row was dropped, doubled
        or mis-signed — the parse FAILS rather than emit a book that
        disagrees with the broker."""
        if not pre['has_cash_report']:
            # Flex queries and customized statements can leave the
            # section out; every guard that rides on the reconciliation
            # (a dropped, doubled or word-matched money row) is then
            # off — said on the console, not silently (audit R1-53).
            if any(booked.values()):
                print(f"{ATTENTION_PREFIX} {shown_name(path)}: the statement has "
                      f"no Cash Report — parsed money is NOT reconciled "
                      f"against IB's own totals. Include the Cash Report "
                      f"section in the export (Flex: add it to the query).",
                      file=sys.stderr)
            return
        if not pre['cash_currencies']:
            print(f"note: {shown_name(path)}: the Cash Report has no per-currency "
                  f"rows — parsed money not reconciled against it.",
                  file=sys.stderr)
            return
        lines = (
            ('Dividends', ('Dividends',)),
            ('Payment In Lieu of Dividends',
             ('Payment In Lieu of Dividends',)),
            ('Withholding Tax', ('Withholding Tax',)),
            ('Broker Interest Paid and Received',
             ('Broker Interest Paid and Received',)),
            ('Other Fees', ('Other Fees',)),
            ('Commissions', ('Commissions', 'Transaction Fees')),
            ('Trades (Sales + Purchase)',
             ('Trades (Sales)', 'Trades (Purchase)')),
        )
        currencies = set(pre['cash_currencies']) | {c for _, c in booked}
        bad = []
        unread = sorted(f"{ln} {cur} {raw!r}"
                        for (ln, cur), raw in pre.get('cash_bad', {}).items()
                        if any(ln in cr for _, cr in lines))
        if unread:
            raise BrokerageParseError(
                f"{shown_name(path)}: the Cash Report total of "
                f"{'; '.join(unread)} is not a number this parser reads — "
                f"the parsed rows cannot be reconciled against it. "
                f"Re-export the statement (English, decimal point).")
        for cur in sorted(currencies):
            for label, cr_lines in lines:
                broker = sum(pre['cash'].get((ln, cur), 0.0)
                             for ln in cr_lines)
                mine = booked.get((label, cur), 0.0)
                if abs(broker - mine) > _IB_CASH_TOL:
                    bad.append(f"{cur} {' + '.join(cr_lines)}: parsed "
                               f"{mine:,.2f} vs Cash Report {broker:,.2f} "
                               f"(diff {mine - broker:+,.2f})")
        if bad:
            raise BrokerageParseError(
                f"{shown_name(path)}: parsed rows do not reconcile with IB's own "
                f"Cash Report — {'; '.join(bad)}. A money row was "
                f"dropped, doubled or mis-signed; refusing to emit a book "
                f"that disagrees with the broker.")

    def statement_accounts(self) -> set:
        """Account ids named by the last parsed statement (Account
        Information / Accounts Included / the per-row Account column)."""
        pre = getattr(self, '_ib_pre', None) or {}
        return set(pre.get('accounts') or ())

    def _pair_warrant_exercises(self, warrant_legs, share_legs) -> None:
        """Mark each warrant exercise leg with the share listing it
        delivers: the one share leg coded Ex on the same date and in the
        same currency (several: the one whose root starts the warrant's
        symbol). An unpaired leg keeps the old booking with an
        ATTENTION line."""
        def _root(sym: str) -> str:
            return re.sub(r'\.(TO|US|AX|L|V|CN|NE)$', '', sym or '')
        used = set()
        for w, where, code in warrant_legs:
            cands = [t for t in share_legs if id(t) not in used
                     and t['date'] == w['date']
                     and t.get('currency') == w.get('currency')]
            if len(cands) > 1:
                wr = _root(w['symbol']).replace('.', '')
                cands = [t for t in cands
                         if wr.startswith(_root(t['symbol']).replace('.', ''))]
            if len(cands) == 1:
                st = cands[0]
                used.add(id(st))
                w['exercise_of'] = st['symbol']
                w['date_settle'] = st['date_settle']
                print(f"note: {where}: warrant {w['symbol']} exercised into "
                      f"{st['symbol']} ({-float(w['quantity']):g} warrants "
                      f"-> {float(st['quantity']):g} shares) — no "
                      f"disposition; the warrant's cost goes into the "
                      f"shares' cost.", file=sys.stderr)
                continue
            print(f"{ATTENTION_PREFIX} {where}: warrant {w['symbol']} "
                  f"exercised ({-float(w['quantity']):g}, code {code}) but "
                  f"{'no' if not cands else 'more than one'} share leg "
                  f"coded Ex on {w['date']} names the shares — booked as a "
                  f"disposal at 0, so the warrant's cost becomes a capital "
                  f"loss instead of part of the shares' cost"
                  f"{self.law(' (ITA s.49(3))', ' (US basis carryover)')}. "
                  f"Correct it by hand: the warrant's cost belongs in the "
                  f"shares acquired.", file=sys.stderr)

    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        transactions = []
        # Corporate Actions rows that aren't SPLIT or Spinoff (e.g.
        # Mergers/Acquisitions, name changes) aren't auto-translated to
        # transactions — the row formats are too varied to handle safely
        # without per-event judgement. Track them per ticker so we can
        # surface a warning at end of parse, prompting the user to add a
        # manual TRANSFER entry if the event affects taxable basis.
        unhandled_ca_tickers: Dict[str, int] = {}
        # Cash takeovers booked as sales, and merger rows left to
        # taxjson-corp-actions — one note each at end of parse.
        cash_takeovers: List[str] = []
        # Warnings about a translated Corporate Actions row, printed at
        # the end of the parse so a later `Ca` of the row withdraws them.
        late_warnings: List[str] = []
        corp_owned_rows: List[str] = []
        # Corporate Actions rows that are real events nothing books (an
        # option adjustment, a spin-off debit on a short parent, a
        # share-for-share tender): one UNBOOKED line each at the end.
        unbooked_ca: List[str] = []
        # Stock dividends booked at $0 cost: one ATTENTION line each.
        stock_dividends: List[str] = []

        # Fee rows that lack both a Date column and a "for Mmm YYYY" hint
        # in the description. Collected and reported as one summary line
        # at the end of parse_file so the user sees a single audible
        # signal instead of one warning per skipped fee.
        skipped_dateless_fees: List[float] = []

        # (symbol, date, ratio) of SPLIT rows already emitted from the
        # Corporate Actions section — a reverse split's paired negative+
        # positive legs must yield ONE SPLIT row. Each entry keeps the
        # emitted transaction plus the legs' share quantities so the
        # ratio can be refined to the broker's own rounding once both
        # legs have been seen (IB books fractional results to 4 dp:
        # 7,000 shares 1-for-3 becomes 2333.3333, not 7000/3 — using
        # the text ratio leaves ±1e-4 phantom dust in the pool).
        emitted_splits: Dict[tuple, Dict[str, Any]] = {}
        # Cash-in-lieu fractions seen BEFORE a split of their symbol
        # within a week — folded into that split's ratio when it is
        # emitted (see _ib_refine_split_ratio); one never claimed is
        # said at the end.
        pending_cil: List[Dict[str, Any]] = []
        # (symbol, date, fraction) of every cash-in-lieu row booked: an
        # overlapping statement that holds the same row joins it to its
        # own split (reconcile_files).
        cil_seen: set = set()

        # Full symbols from the Open Positions section, used to seed the
        # income-reattribution holdings map (covers buy-and-hold years with
        # no trade rows, and disambiguates interlisted same-ticker names).
        open_position_syms: set = set()
        open_position_qty: Dict[str, float] = {}

        # Year-end dividend accruals. IB accrues a declared dividend
        # ('Po' code) and reverses the accrual ('Re') once the cash
        # actually posts into the Dividends section. We do NOT treat an
        # accrual as income — it's an estimate (often a stale per-share
        # rate, and denominated in the account base currency rather than
        # the dividend's native currency), and booking it would also risk
        # double-counting once the real Dividends row lands in a later
        # statement. Instead we net Po/Re per dividend and, at end of
        # parse, warn about any accrual that's still open and has no
        # matching posted Dividends row in this file — that's income IB
        # has declared but not yet booked, so the user should re-download
        # a more recent statement before filing.
        accrual_net: Dict[tuple, float] = {}
        accrual_meta: Dict[tuple, Dict[str, str]] = {}
        # (symbol, pay_date) -> ex date, from the same section.
        accrual_ex: Dict[tuple, str] = {}
        # (ticker, date) of every posted Dividends row in this file, used
        # to suppress an accrual warning when the cash dividend is already
        # present. The Dividends row's Date is the dividend's pay date.
        posted_dividend_keys: List[tuple] = []
        # Statement period end (ISO). An accrual whose pay date is after
        # the period end is just a normal pending dividend, not a missed
        # one — only accruals payable *within* the period are surprising.
        statement_period_end = ''

        # Explicit currency conversions (Trades / Forex rows, e.g.
        # USD.CAD). Counted, never translated: no downstream consumer
        # models a cash conversion as a disposition (`taxjson fx-cash`
        # reconstructs FX cash gains from security cash flows only —
        # KNOWN_ISSUES "IB Trades/Forex conversions are not modeled").
        # Emitting them as a BUYSELL of a phantom `USD`/`CASH.USD`
        # asset would corrupt the position book, so they get one
        # explicit note instead of vanishing into the asset filter.
        forex_rows = 0

        # Expiry rows (settle == date), for the end-of-parse clamp of
        # same-contract trades whose T+1 settle falls after the expiry
        # (BaseBrokerage.clamp_settlement_to_expiry).
        expiry_txs: List[Dict[str, Any]] = []
        # Assignment / exercise legs: the option side (ASSIGN) and the
        # stock side (a BUYSELL coded A or Ex), paired after the loop.
        assign_option_legs: List[Dict[str, Any]] = []
        assign_stock_legs: List[Dict[str, Any]] = []
        # Warrant exercises: (warrant leg, where, code) and the share
        # legs coded Ex, paired after the loop (A2-0090).
        warrant_ex_legs: List[Tuple[Dict[str, Any], str, str]] = []
        share_ex_legs: List[Dict[str, Any]] = []

        # `Transaction Fees` rows (UK Stamp Tax, SEC/FINRA-style
        # levies) are a per-fill BREAKDOWN of charges IB ALREADY
        # includes in the trade's Comm/Fee: an LSE buy's Comm/Fee
        # already holds the stamp levy (its Basis is the gross plus
        # that Comm/Fee), the stamp-tax rows sum to part of it, and the
        # Cash Report's Commissions + Transaction Fees lines add up to
        # the sum of the Comm/Fee column. Folding the levy into the
        # trade AGAIN (the earlier behaviour) charged it twice.
        # The section is therefore a recognized non-event: the Cash
        # Report reconciliation below checks the identity
        #   sum(Comm/Fee) == Commissions + Transaction Fees
        # per currency, so a statement layout that ever EXCLUDED the
        # levy from Comm/Fee would fail loudly instead of under-booking.

        # Cash booked per (Cash Report line, currency), for the end-of-
        # parse reconciliation against IB's own Cash Report.
        cash_booked: Dict[tuple, float] = {}

        def _book(line: str, cur: str, amt: float) -> None:
            cash_booked[(line, cur)] = cash_booked.get((line, cur),
                                                       0.0) + amt

        # Corporate Actions rows already translated, so a later `Ca`
        # (cancellation) row can find and undo its original — a
        # cancelled split/merger/cash-in-lieu used to stay booked.
        ca_effects: List[Dict[str, Any]] = []
        # (symbol, ISIN country) of income rows whose country has no
        # suffix mapping (fell back to .US).
        isin_fallback: set = set()
        pending_ca: List[Dict[str, Any]] = []
        # Every Corporate Actions `Ca` row of this statement, and the
        # ones paired here with their original's date: an OVERLAPPING
        # statement of the account that holds the original but not the
        # cancellation (a download taken before IB posted it) must undo
        # its copy too, or dedup keeps the cancelled row (audit A2-0023
        # / A2-0024; IbBrokerage.reconcile_files).
        ca_rows_seen: List[Dict[str, Any]] = []
        ca_pairs: List[tuple] = []
        # Transfers rows coded `Ca` listed before their original (or
        # whose original is in an earlier statement): consumed by a
        # later original, else kept as a reversing leg at the end.
        pending_xfer_ca: List[Dict[str, Any]] = []
        # Commission Adjustments rows that name their trade (ticker,
        # signed quantity, trade date): folded into that trade after the
        # walk (tax-logic CA-ACB-COMMREFUND / US-BASIS-COMMREFUND).
        comm_adjustments: List[Dict[str, Any]] = []
        # Trades rows coded `Ca` (cancelled): paired with their original
        # after the loop (lib/trade_cancel).
        trade_cancels: List[Dict[str, Any]] = []
        # Unknown (non-allowlisted) sections already warned about.
        unknown_sections: set = set()

        def _ca_apply_undo(eff: Dict[str, Any]) -> None:
            """Undo one translated Corporate Actions row (its `Ca`
            cancellation arrived)."""
            eff['consumed'] = True
            drop = {id(t) for t in eff.get('txs', ())}
            kind = eff['kind']
            if kind == 'split':
                info = eff['split']
                if eff.get('renames'):
                    info['tx']['symbol_new'] = info['tx']['symbol']
                if eff['qty'] > 0:
                    info['new'] -= eff['qty']
                elif eff['qty'] < 0:
                    info['old'] += eff['qty']
                if abs(info['new']) < 1e-9 and abs(info['old']) < 1e-9:
                    # No leg left: the split never happened.
                    drop.add(id(info['tx']))
                    emitted_splits.pop(info['key'], None)
                else:
                    info['tx']['quantity'] = info['text_ratio']
                    _ib_refine_split_ratio(info)
            elif kind == 'cil':
                if eff.get('msg') in late_warnings:
                    late_warnings.remove(eff['msg'])
                info = eff.get('split')
                if info is not None:
                    info['cil'] -= eff['frac']
                    info['tx']['quantity'] = info['text_ratio']
                    _ib_refine_split_ratio(info)
                else:
                    pending_cil[:] = [c for c in pending_cil
                                      if c['eff'] is not eff]
            elif kind == 'tender':
                tl = eff['tender']
                tl['parked'] -= eff.get('parked', 0.0)
                if eff.get('cash'):
                    tl['cash_qty'] -= -eff['qty']
                    tl['cash'] -= eff['cash']
            elif kind == 'cash_takeover':
                if eff.get('msg') in cash_takeovers:
                    cash_takeovers.remove(eff['msg'])
            elif kind in ('corp_owned', 'spinoff'):
                if eff['desc'] in corp_owned_rows:
                    corp_owned_rows.remove(eff['desc'])
            elif kind == 'unbooked':
                if eff['msg'] in unbooked_ca:
                    unbooked_ca.remove(eff['msg'])
            elif kind == 'stockdiv':
                if eff['msg'] in stock_dividends:
                    stock_dividends.remove(eff['msg'])
            elif kind == 'unhandled':
                t = eff['ticker']
                unhandled_ca_tickers[t] = unhandled_ca_tickers.get(t, 0) - 1
                if unhandled_ca_tickers[t] <= 0:
                    del unhandled_ca_tickers[t]
            tl = eff.get('tender')
            if tl is not None:
                # The cancelled journal row leaves the tender note's row
                # tally and dates (audit A2-1031 / A2-1034).
                tl['rows'] -= 1
                if eff['date'] in tl['dates']:
                    tl['dates'].remove(eff['date'])
            _cat = eff.get('skip_cat')
            if _cat and self._skip_counts.get(_cat):
                # The cancelled original is resolved, not skipped: the
                # skip line and the UNBOOKED tally agree (audit R1-329).
                self._skip_counts[_cat] -= 1
                if not self._skip_counts[_cat]:
                    del self._skip_counts[_cat]
                self.note_row_consumed()
            if drop:
                transactions[:] = [t for t in transactions
                                   if id(t) not in drop]

        def _ca_matches(ca: Dict[str, Any], eff: Dict[str, Any]) -> bool:
            # The currency and asset category are part of the row's
            # identity: a CAD leg's Ca undid a USD-leg split with the
            # same description (audit A2-0603).
            return (not eff['consumed'] and eff['desc'] == ca['desc']
                    and abs(eff['qty'] + ca['qty']) < 1e-9
                    and (eff.get('currency') or '') == (ca.get('currency')
                                                        or '')
                    and (eff.get('cat') or '') == (ca.get('cat') or ''))

        def _ca_undo(ca: Dict[str, Any], same_date_only: bool = False,
                     exact_date: Optional[str] = None,
                     rebook_ok: bool = False):
            """A `Ca` row: undo the latest matching original (same
            description, currency and category, negated quantity, dated
            on or before the cancellation; same date preferred).
            `same_date_only` (the row walk) leaves a Ca with no
            same-date original waiting, so a rebooked row listed before
            the original is never the one undone (audit R1-300); the
            end of the walk and the account's other statements take the
            latest earlier original. `exact_date` (an overlapping copy
            of the statement that already paired the Ca) undoes only
            the original dated that day. A row dated before the
            statement's period is a rebook of an earlier statement's
            row, undone only with `rebook_ok` (reconcile_files, when no
            statement holds the original). Returns the undone
            original's record, or None."""
            cands = [e for e in ca_effects if _ca_matches(ca, e)
                     and e['date'] <= ca['date']
                     and (exact_date is None or e['date'] == exact_date)
                     and (rebook_ok or not _before_period(e['date']))]
            if not cands:
                return None
            same = [e for e in cands if e['date'] == ca['date']]
            if same_date_only and not same:
                return None
            eff = (same or cands)[-1]
            _ca_apply_undo(eff)
            return eff

        def _ca_record(eff: Dict[str, Any]) -> None:
            """Remember a translated row; a same-date `Ca` row that
            arrived BEFORE it (same description, negated quantity)
            undoes it now."""
            ca_effects.append(eff)
            if _before_period(eff['date']):
                return                    # a rebook: reconcile_files
            for i, ca in enumerate(pending_ca):
                if _ca_matches(ca, eff) and eff['date'] == ca['date']:
                    pending_ca.pop(i)
                    _ca_apply_undo(eff)
                    ca_pairs.append((ca, eff['date']))
                    self.note_row_consumed()      # the waiting Ca row
                    return

        # Tender / voluntary-offer journals (Corporate Actions rows
        # matched by corp_actions.ib_tender_root), keyed by the
        # suffixed ROOT symbol. IB parks tendered shares on a `.TEN`
        # placeholder line with zero-proceeds legs and journals them
        # back (or settles them) on allocation. Zero-proceeds legs are
        # recognized non-events (the position never economically
        # changed); a leg carrying cash is a DISPOSITION and is booked
        # as a sale — never dropped into the unhandled tally. Per root:
        # rows seen, shares still parked on the `.TEN` line, cash
        # dispositions booked, dates — for the end-of-parse notes.
        tender_legs: Dict[str, Dict[str, Any]] = {}

        # Row-level accounting (base.py zero-drop policy): every Data
        # row is either consumed (emitted, or read into parser state)
        # or counted under a skip label, so `taxjson-brokerage --lint`
        # can reconcile rows_seen == consumed + skipped. Labels with the
        # KNOWN_NONEVENT_PREFIX are rows the parser recognizes as
        # non-events (subtotals, metadata sections) and reports in the
        # calmer of the two summary notes.
        self._rows_seen = 0
        _NE = self.KNOWN_NONEVENT_PREFIX
        # Transfers `Ca` rows and the (original, Ca) pairs consumed here
        # (reconcile_files: an overlapping statement's copy of the
        # cancelled transfer, audit A2-1038).
        self.xfer_ca_keys: set = set()
        self.xfer_pairs: List[tuple] = []
        self._placeholder_warned: set = set()
        self._sign_warned = False
        self._tag_warned: set = set()

        rows = self._read_rows(path)
        pre = _ib_prescan(rows, shown_name(path))
        ctx = self.account_context
        # The statement's first day. A row dated before it (a Trades,
        # Transfers or Corporate Actions row) is IB's cancel-and-rebook
        # of a row of an EARLIER statement: under the account context
        # its `Ca` looks for that statement's original first
        # (reconcile_files) and pairs with the rebook in this statement
        # only when no statement holds one (audit A2-0886 / A2-1559 /
        # A2-1560). A lone parse pairs in this statement, as before.
        self.period_start = ''
        for _r in rows:
            if (len(_r) >= 4 and _r[0] == 'Statement' and _r[1] == 'Data'
                    and _r[2] == 'Period'):
                _span = _ib_period(_r[3])
                if _span:
                    self.period_start = _span[0].isoformat()
                break

        def _before_period(d) -> bool:
            return bool(ctx is not None and self.period_start
                        and (d or '') < self.period_start)
        self.held_trade_cas: List[Dict[str, Any]] = []
        self.held_xfer_cas: List[Dict[str, Any]] = []
        if ctx:
            # Option-root aliases from ALL of the account's statements
            # (audit S059-15: next year's instrument list may name only
            # the adjusted root).
            occ = {c: set(v) for c, v in pre['occ_by_conid'].items()}
            for c, v in ctx.get('occ_by_conid', {}).items():
                occ.setdefault(c, set()).update(v)
            und = {c: set(v) for c, v in
                   ctx.get('opt_underlying', {}).items()}
            for c, v in pre['opt_underlying'].items():
                und.setdefault(c, set()).update(v)
            pre['root_alias'], pre['alias_conids'] = _root_aliases(occ, und)
            for key, by_root in ctx.get('contract_conids', {}).items():
                tgt = pre['contract_conids'].setdefault(key, {})
                for root, ids in by_root.items():
                    tgt.setdefault(root, set()).update(ids)
        else:
            _warn_stock_aliases(pre['stock_conid_syms'], shown_name(path),
                                first_seen=pre['first_seen'],
                                listing=pre['stock_listing'])
        self._ib_pre = pre
        self._check_statement_kind(pre, path)
        fii = pre['fii']
        # Neutral payer fact for income rows (lib/income_dating decides
        # what it means): the IB entity named in the statement header.
        _bn = (pre.get('broker_name') or '').lower()
        dealer_country = ('CA' if 'canada' in _bn
                          else 'US' if _bn.rstrip('. ').endswith('llc')
                          else '')
        root_alias = pre['root_alias']
        aliased_roots: Dict[str, str] = {}
        if len(pre['accounts']) > 1 and self.combined_broker_accounts:
            # The label declares every broker account in its statements
            # is the user's and taxable together (owner decision): a
            # NOTE. IB's statement names no plan per account, so on a
            # sheltered label that cannot be checked — refused.
            _masked = sorted(_mask_account(a) for a in pre['accounts'])
            if self.account_taxable is False:
                raise combined_accounts_refusal(
                    shown_name(path), 'IB', _masked,
                    "an IB statement does not say which plan each "
                    "account is")
            print(combined_accounts_note(shown_name(path), 'IB', _masked),
                  file=sys.stderr)
        elif len(pre['accounts']) > 1:
            # ATTENTION (the run's console): a TFSA/RRSP inside a
            # consolidated statement lands in this book (audit A2-0610).
            print(f"{ATTENTION_PREFIX} {shown_name(path)}: IB statement spans "
                  f"{len(pre['accounts'])} accounts ("
                  f"{', '.join(sorted(_mask_account(a) for a in pre['accounts']))}"
                  f") — every row is booked to ONE account label. That "
                  f"is right only when they are one tax entity (e.g. two "
                  f"taxable margin accounts); export a registered "
                  f"account (TFSA/RRSP) separately.", file=sys.stderr)
        header_maps = {} # section -> header_map
        header_lens: Dict[str, int] = {}
        unknown_row_types: Dict[str, int] = {}

        for lineno, row in enumerate(rows, 1):
            if not row:
                continue

            section = row[0]
            type_ = row[1] if len(row) > 1 else ''

            if type_ == 'Header':
                header_maps[section] = {col: i for i, col in enumerate(row)}
                header_lens[section] = len(row)
                continue

            if type_ != 'Data':
                if type_ not in _IB_STRUCTURE_ROW_TYPES:
                    # Not a row kind IB writes ('data' after a
                    # spreadsheet re-save, say): counted and said, never
                    # skipped unseen (audit S060-07).
                    self._rows_seen += 1
                    self.count_skip(f"row of unknown type {type_!r}")
                    unknown_row_types[type_] = (
                        unknown_row_types.get(type_, 0) + 1)
                continue

            self._rows_seen += 1

            header_map = header_maps.get(section)
            if not header_map:
                self.count_skip(f"section {section}: Data row with "
                                f"no Header row")
                continue
            if (section in _IB_FULL_WIDTH_SECTIONS
                    and len(row) < header_lens.get(section, 0)):
                # A row cut short (a truncated or hand-edited export)
                # read its missing cells as blank: a Trades row without
                # its Code booked an assignment leg as a plain trade,
                # silently (audit A2-1042 — the Webull/RBC/Kraken
                # refusal). IB writes every money row at full width.
                raise BrokerageParseError(
                    f"{shown_name(path)} line {lineno}: {section} row has "
                    f"{len(row)} cells but its header has "
                    f"{header_lens[section]} — the row is cut short; "
                    f"refusing to read its missing cells as blank. "
                    f"Re-download the statement.")

            if section == 'Trades' or section == 'Options Expirations':
                where = f"{shown_name(path)} line {lineno} ({section})"
                if section == 'Trades':
                    self.require_columns(header_map, ('Asset Category',),
                                         section=section, where=where)
                    asset_cat = self._cell(row, header_map,
                                           'Asset Category')
                else:
                    # The row's own Asset Category when the section has
                    # one: a futures-option expiry read as an equity
                    # option became a phantom non-F: contract while the
                    # real position never closed (audit R1-56).
                    asset_cat = (self._cell(row, header_map,
                                            'Asset Category')
                                 if 'Asset Category' in header_map
                                 else 'Equity and Index Options')

                # Roll-up rows carry the asset category of what
                # they sum (`Total,Forex,...`), so they must be
                # recognized BEFORE the per-category dispatch or a
                # Forex subtotal counts as one more conversion.
                _pre_disc = self._cell(row, header_map, 'DataDiscriminator')
                if _pre_disc in ('SubTotal', 'Total'):
                    self.count_skip(f"{_NE}Trades roll-up row "
                                    f"({_pre_disc})")
                    continue

                if asset_cat not in ('Stocks', 'Equity and Index Options', 'Futures', 'Options On Futures', 'Warrants'):
                    if asset_cat == 'Forex':
                        # One detail level only (as for securities):
                        # Order rows when the file has them.
                        if (_pre_disc == 'Order'
                                or (_pre_disc in ('', 'Trade')
                                    and not pre['has_order_level'])):
                            forex_rows += 1
                            self._book_forex(row, header_map, where,
                                             _book)
                        self.count_skip(
                            f"{_NE}Trades/Forex (currency conversion, "
                            f"not modeled — KNOWN_ISSUES)")
                    elif asset_cat in ('Total', '') or 'Total' in asset_cat:
                        self.count_skip(f"{_NE}Trades subtotal row")
                    else:
                        # Bonds, CFDs, Mutual Funds, a renamed category
                        # — a real asset class the parser has no branch
                        # for. A row that moves money is a disposition
                        # or acquisition this parser would DROP: an
                        # error, like an unknown money section (audit
                        # S060-08 — it was a counted skip, caught only
                        # when a Cash Report happened to be present).
                        _raw_money = (self._cell(row, header_map,
                                                 'Proceeds')
                                      or self._cell(row, header_map,
                                                    'Notional Value'))
                        try:
                            _moves = abs(parse_strict_number(
                                _raw_money, field='Proceeds', where=where,
                                allow_blank=True, blank=0.0)) > 0.005
                        except BrokerageParseError:
                            _moves = True
                        if _moves:
                            raise BrokerageParseError(
                                f"{where}: Trades row in asset category "
                                f"{asset_cat!r} "
                                f"({self._cell(row, header_map, 'Symbol')}"
                                f") moves money, and this parser has no "
                                f"branch for that category — refusing to "
                                f"drop it. Book it by hand in a .tt file "
                                f"and remove the rows from the export, or "
                                f"report the category.")
                        self.count_skip(f"Trades/{asset_cat}")
                    continue

                # Flex queries configured with several detail levels
                # list each fill more than once (Order + Trade +
                # ClosedLot rows for the SAME execution); without
                # this filter each level emitted a BUYSELL and
                # split-fill disambiguation then PROTECTED the
                # duplicate from dedup — every trade double-counted.
                # Accept exactly one level, preferring 'Order' when
                # both appear; roll-up rows are skipped silently
                # (they are structure, not data).
                if 'DataDiscriminator' in header_map:
                    _disc = _pre_disc
                    if _disc in ('ClosedLot', 'SubTotal', 'Total'):
                        self.count_skip(f"{_NE}Trades roll-up row "
                                        f"({_disc})")
                        continue
                    # A Trade row is skipped only when an Order row
                    # covers the SAME symbol and day (audit S060-10: a
                    # file-global flag set by the first Order row
                    # dropped every later Trade-only fill of any symbol,
                    # and a Trade row listed before its Order row was
                    # booked twice). The prescan refuses a file whose
                    # two levels disagree on the quantity.
                    if _disc == 'Trade' and 'Order' in pre[
                            'order_levels'].get(
                            (asset_cat, self._cell(row, header_map,
                                                   'Symbol'),
                             (self._cell(row, header_map, 'Date/Time')
                              .replace(',', ' ').split() or [''])[0]),
                            {}):
                        self.count_skip(f"{_NE}Trades execution-level "
                                        f"duplicate of an Order row")
                        continue
                    if _disc and _disc not in ('Order', 'Trade'):
                        self.count_skip(f"Trades DataDiscriminator "
                                        f"{_disc}")
                        continue

                # Every money/quantity/price/date/currency column is
                # REQUIRED and resolved by header name — a renamed or
                # missing column used to read as 0 (Comm/Fee renamed:
                # every fee vanished; Proceeds renamed: every gross 0)
                # while the parse "succeeded".
                proceeds_key = ('Proceeds' if 'Proceeds' in header_map
                                else 'Notional Value')
                if section == 'Trades':
                    self.require_columns(
                        header_map,
                        ('Currency', 'Symbol', 'Date/Time', 'Quantity',
                         'T. Price', ('Proceeds', 'Notional Value'),
                         'Comm/Fee', 'Code'),
                        section=section, where=where)
                else:
                    # An expiry carries no money; its money columns
                    # are optional here, but parsed strictly when
                    # present.
                    self.require_columns(
                        header_map,
                        ('Currency', 'Symbol', 'Date/Time', 'Quantity'),
                        section=section, where=where)

                def _num(col, _where=where, _row=row, _hm=header_map,
                         optional=False):
                    if col not in _hm:
                        if optional:
                            return 0.0
                        raise BrokerageParseError(
                            f"{_where}: missing column {col!r}")
                    return parse_strict_number(
                        self._cell(_row, _hm, col), field=col,
                        where=_where)

                symbol = self._cell(row, header_map, 'Symbol')
                # Preserve the raw symbol string as `description`
                # so taxjson-brokerage's security-overrides can
                # rewrite mislabeled IB tickers (every other
                # broker emits a `description` field; IB Trades
                # was the lone gap — without it the override
                # silently no-op'd on IB rows). For options the
                # raw symbol is the verbose `SAMPMN 16JAN26 100 P`
                # form, which is exactly what the user keys on
                # in ticker.map `EXTRACT` lines.
                description = symbol
                currency = self._cell(row, header_map, 'Currency')
                if not symbol or not currency:
                    raise BrokerageParseError(
                        f"{where}: blank Symbol/Currency on a "
                        f"{asset_cat} trade row")
                date, time = _ib_split_datetime(
                    self._cell(row, header_map, 'Date/Time'), where)
                # The exchange's trade date when it is not the ET clock
                # date (an overnight-session US fill, an ASX fill):
                # tax-logic CA-DATE-SESSION / US-DATE-SESSION.
                _listing = (_ib_listing_ext(asset_cat, symbol, currency,
                                            fii)
                            if asset_cat in ('Stocks', 'Warrants') else '')
                date, time, broker_time = _ib_market_trade_date(
                    date, time, asset_cat, currency, _listing,
                    symbol=symbol)
                # Settled in the listing's market (lib/dates.market_of,
                # the rule every parser shares): a USD unit on the TSX
                # through CDS, a USD line on the LSE on the UK cycle.
                date_settle = get_ib_settlement(
                    date, asset_cat,
                    market_of(f".{_listing}" if _listing else "", currency),
                    futures_settle=self.futures_settle)
                qty = _num('Quantity')
                opt_exp = section == 'Options Expirations'
                price = _num('T. Price', optional=opt_exp)
                proceeds_signed = _num(proceeds_key, optional=opt_exp)
                comm_signed = _num('Comm/Fee', optional=opt_exp)
                gross_proceeds = abs(proceeds_signed)
                mult = self._multiplier(asset_cat, symbol, fii, where)
                self._check_trade_money(
                    where, symbol, qty, price, proceeds_signed,
                    comm_signed, mult,
                    check_notional=(proceeds_key in header_map),
                    exact=asset_cat in FUTURES_CATEGORIES)

                # SIGNED commission. IB reports a charge NEGATIVE and a
                # rebate (option exchange/ORF rebates, a cancelled
                # trade's refunded commission) POSITIVE. The engine's
                # `fee` is positive = charged, so fee = -Comm/Fee, and
                # a rebate is a NEGATIVE fee. abs() turned every rebate
                # into a charge — a net error of 2x the rebate on each
                # rebated fill.
                # Cash-true net: a buy costs -(Proceeds + Comm/Fee), a
                # sell brings in Proceeds + Comm/Fee.
                comm_fee = 0.0 - comm_signed   # (no -0.0 for a zero charge)
                net_amount = (gross_proceeds + comm_fee) if qty > 0 else (gross_proceeds - comm_fee)
                if asset_cat == 'Futures':
                    # A futures price can be negative (WTI, April 2020):
                    # the notional's SIGN is kept — a sale at -37.63
                    # pays 37,630, a buy at -37.63 receives it. The
                    # magnitude booked a loss as a gain (audit A2-0092;
                    # S053-13 only silenced the schema error). Same
                    # numbers as before for a positive price.
                    net_amount = (-(proceeds_signed + comm_signed)
                                  if qty > 0
                                  else proceeds_signed + comm_signed)
                action = 'BUYSELL'
                # IB packs multiple per-trade codes into one cell
                # (separators are `;`, `,`, or whitespace) — e.g.
                # `O;P`, `A`, `C;Ep`. Split into tokens and match `A`
                # exactly so codes that merely *contain* the letter
                # A (e.g. `Au`, `AEx`, `ADR`) don't get reclassified
                # as an option assignment. The zero-price guard
                # already limits damage, but the token match is the
                # principled check.
                code = self._cell(row, header_map, 'Code')
                code_tokens = re.split(r'[;,\s]+', code)
                # `Ex` (exercise) is the holder-side twin of `A`
                # (assignment): IB closes the option leg at T. Price
                # 0 with `C;Ex` and books the stock leg (`Ex;O` for
                # a call, `C;Ex` for a put) at the strike. Booked as
                # a plain BUYSELL, the option's premium was realized
                # as a 100% loss on the option instead of rolling
                # into the stock leg's cost (call) / proceeds (put)
                # the way an assignment's does. The zero-price
                # guard keeps the stock leg (price = strike) a
                # BUYSELL, which is the leg that pops the premium.
                if (('A' in code_tokens or 'Ex' in code_tokens)
                        and abs(price) < 1e-5):
                    action = 'ASSIGN'

                # Expiry: the `Ep` code, a row of the Options
                # Expirations section, or a zero-price zero-proceeds
                # CLOSE of an option that isn't an assignment/
                # exercise. An expiry has no settlement cycle — the
                # contract ceases to exist on its expiry date — so
                # date_settle == date. The T+1 applied to every
                # Trades row pushed a Dec-31 expiry into the NEXT
                # tax year on the (Canadian) settle-date basis.
                # Assignment/exercise option legs keep T+1: their
                # premium rolls into the stock leg, which really
                # settles T+1, and the pair must share a settle
                # date so no unrelated same-underlying trade can
                # consume the staged premium in between.
                is_expiry = (
                    section == 'Options Expirations'
                    or 'Ep' in code_tokens
                    or (asset_cat in ('Equity and Index Options',
                                      'Options On Futures')
                        and action != 'ASSIGN'
                        and 'C' in code_tokens
                        and abs(price) < 1e-9
                        and gross_proceeds < 1e-9))
                if is_expiry and date:
                    date_settle = date

                if asset_cat in ('Equity and Index Options', 'Options On Futures'):
                    symbol = self._option_symbol(
                        symbol, asset_cat, fii, root_alias,
                        aliased_roots, where)
                    if asset_cat == 'Options On Futures':
                        symbol = f"F:{symbol}"
                elif asset_cat == 'Futures':
                    symbol = symbol.replace(' ', '.')
                    symbol = f"F:{symbol}"
                else:
                    symbol = symbol.replace(' ', '.')
                    self._check_symbol_tag(re.sub(
                        r'\.(TO|US|AX|L)$', '', symbol, flags=re.IGNORECASE),
                        where)

                # Remove common known exchange extensions to avoid doubling
                symbol = re.sub(r'\.(TO|US|AX|L)$', '', symbol, flags=re.IGNORECASE)

                ext = (_ib_listing_ext(asset_cat, description, currency, fii)
                       if asset_cat in ('Stocks', 'Warrants')
                       else _ib_currency_ext(currency))
                full_symbol = f"{symbol}.{ext}"

                _trade_tx = {
                    'action': action,
                    'date': date,
                    'time': time,
                    'date_settle': date_settle,
                    'symbol': full_symbol,
                    'quantity': qty,
                    'currency': currency,
                    'price': price,
                    'fee': comm_fee,
                    'net_amount': net_amount,
                    'gross_amount': gross_proceeds,
                    # Contract size (IB's Financial Instrument
                    # Information Multiplier; 100 per equity option,
                    # 1000 per crude-oil future, 0.1 per micro, 1 per
                    # share). The schema notional check uses it —
                    # futures used to trip it on every row.
                    'multiplier': mult,
                    'account': 'IB',
                    'description': description,
                }
                if broker_time:
                    _trade_tx['broker_time'] = broker_time
                # IB's open/close marker (O = opening, C = closing; a
                # sale coded `C;O` closed a long and opened a short in
                # one fill), in IB's order. The engine books by sign;
                # the missing-history checks read this to tell a real
                # short (O) from a sale of a position bought before the
                # data (C) — audit S013-00 / S058-02 / S060-12. A `Ca`
                # cancellation row reverses another fill: its marker
                # says nothing about this account's position.
                _oc = [c for c in code_tokens if c in ('O', 'C')]
                if _oc and 'Ca' not in code_tokens:
                    _trade_tx['open_close'] = ';'.join(_oc)
                    if _oc == ['C'] and 'Basis' in header_map:
                        # IB's cost of what the closing trade closed —
                        # evidence for the missing-history report, in
                        # the trade's own currency (never booked).
                        _braw = self._cell(row, header_map, 'Basis')
                        try:
                            _bv = parse_strict_number(
                                _braw, field='Basis', where=where) \
                                if _braw.strip() else None
                        except (BrokerageParseError, ValueError):
                            _bv = None
                        if _bv is not None and abs(_bv) > 1e-9:
                            _trade_tx['broker_basis'] = (
                                f"{abs(_bv):,.2f} {currency}")
                _name = self._security_name(asset_cat, description, fii)
                if _name:
                    _trade_tx['security_name'] = _name
                # `Ca` = IB CANCELLED an earlier fill: this row reverses
                # it (opposite quantity, same date/time and price). It
                # used to book as an ordinary trade — a phantom round
                # trip whose rebooking turned an allowed loss into two
                # superficial-loss denials (audit R1-51). Marked here,
                # dropped with its original after the loop; an original
                # in an earlier statement is paired by taxjson-merge2.
                # The Cash Report booking below keeps both rows (IB's
                # own totals include both).
                if (asset_cat in ('Stocks', 'Warrants') and qty > 0
                        and section == 'Trades'
                        and abs(price) < 1e-9 and gross_proceeds < 0.005
                        and 'Ca' not in code_tokens
                        and not ('A' in code_tokens or 'Ex' in code_tokens)):
                    # A stock BUY at ZERO cost books a $0 ACB, and the
                    # whole sale later becomes gain: almost always a
                    # transfer or journal row (the generic importer
                    # refuses it, R1-120; audit A2-0263).
                    print(f"{ATTENTION_PREFIX} {where}: {full_symbol}: a "
                          f"buy of {qty:g} at ZERO cost (T. Price and "
                          f"Proceeds 0) — booked at $0 cost. A zero-cost "
                          f"buy is almost always a transfer or journal "
                          f"row: give the real cost in a .tt file and "
                          f"remove the row, if so.", file=sys.stderr)
                if 'Ca' in code_tokens:
                    # (description stays the raw symbol: the security
                    # overrides key on it, and the original must get the
                    # same rewrite for the pair to match.)
                    _trade_tx['type'] = TRADE_CANCEL_TYPE
                    trade_cancels.append(_trade_tx)
                transactions.append(_trade_tx)
                if is_expiry:
                    expiry_txs.append(_trade_tx)
                if action == 'ASSIGN' and asset_cat == 'Equity and Index Options':
                    assign_option_legs.append(_trade_tx)
                elif (asset_cat in ('Stocks', 'Warrants')
                      and ('A' in code_tokens or 'Ex' in code_tokens)):
                    assign_stock_legs.append(_trade_tx)
                if 'Ex' in code_tokens and 'Ca' not in code_tokens:
                    if (asset_cat == 'Warrants' and action == 'ASSIGN'
                            and qty < 0):
                        warrant_ex_legs.append((_trade_tx, where, code))
                    elif asset_cat == 'Stocks' and qty > 0:
                        share_ex_legs.append(_trade_tx)
                # Cash Report: futures settle daily through "Cash
                # Settling MTM", never through Trades (Sales/Purchase).
                if asset_cat != 'Futures':
                    _book('Trades (Sales + Purchase)', currency,
                          proceeds_signed)
                _book('Commissions', currency, comm_signed)
                self.note_row_consumed()

            elif section == 'Dividends':
                # Required columns resolved by header name; a missing
                # one FAILS the parse (it used to fall back to row[0] —
                # the literal section name — and later to a loud skip
                # that still lost the income).
                where = f"{shown_name(path)} line {lineno} ({section})"
                self.require_columns(
                    header_map, ('Currency', 'Date', 'Description',
                                 'Amount'), section=section, where=where)
                currency = self._cell(row, header_map, 'Currency')
                description = self._cell(row, header_map, 'Description')
                # IB emits per-currency subtotal rows ('Total' / 'Total
                # in CAD' in the CURRENCY cell) — skip before parsing.
                # Not by a word in the description: a fund named "Total
                # Return" paid a dividend that vanished (audit S058-03).
                if 'Total' in currency or (not currency
                                           and 'Total' in description):
                    self.count_nonevent(f"{section} subtotal row")
                    continue
                date = _ib_require_date(
                    self._cell(row, header_map, 'Date'), where)
                # SIGN-PRESERVING: IB posts re-characterizations as a
                # negative reversal row plus a corrected positive row.
                # abs() here used to book all three legs as income
                # (+250 / −250 / +240 → 740 instead of 240); keeping
                # the sign lets the reversal net out downstream.
                amount = parse_strict_number(
                    self._cell(row, header_map, 'Amount'), field='Amount',
                    where=where)

                ticker, isin = _ib_income_ticker_strict(description,
                                                        where, section)
                # A currency-tagged line (SAMPLE.CAD) is its own security
                # here as in Trades: say so once (re-audit A2-1493).
                self._check_symbol_tag(ticker, where)
                # Record (ticker, pay date) so the accrual diagnostic
                # below can tell whether this dividend's cash has
                # already been booked in this file.
                posted_dividend_keys.append((ticker, date))
                ext = _isin_ext(isin, ticker, isin_fallback)

                # IB's PIL marker is "Payment in Lieu of Dividend" (note
                # the lowercase 'in'); the previous match string had a
                # capital 'I' and silently missed every PIL row. Match
                # case-insensitively against both phrasings used by IB.
                desc_lower = description.lower()
                is_pil = ('payment in lieu of dividend' in desc_lower
                          or 'in lieu of dividend' in desc_lower)
                _book('Payment In Lieu of Dividends' if is_pil
                      else 'Dividends', currency, amount)
                if is_roc_description(description):
                    # IB's "(Return of Capital)" label is the ISSUER's
                    # designation, which is only an ACB reduction for
                    # Canadian purposes in the Canadian-issuer case.
                    #  * A PAYMENT IN LIEU is paid by the share
                    #    borrower, not the issuer: it can never reduce
                    #    ACB, whatever the underlying distribution was
                    #    — income (falls through to the PIL branch).
                    #  * A non-resident corporation's distribution on
                    #    a portfolio share is a foreign DIVIDEND (ITA
                    #    s.90(1)); a US "return of capital" (no E&P)
                    #    is a cost reduction only when the issuer
                    #    really reduced its paid-up capital
                    #    (s.53(2)(b)(ii)) — s.90(2)/(3) are the
                    #    foreign-affiliate rules (audit S058-07).
                    #    Canada default: foreign dividend; [settings]
                    #    foreign_return_of_capital = "acb" books the
                    #    cost reduction.
                    #  * Canadian issuer (T3 box 42 style): ACB
                    #    reduction, as before.
                    # No ISIN → issuer unknown → kept as ADJUST.
                    _issuer_cc = isin[:2].upper() if len(isin) >= 2 else ''
                    _foreign = bool(_issuer_cc) and _issuer_cc != 'CA'
                    if is_pil:
                        description = (
                            f"{description} [payment in lieu of an "
                            f"IB-designated return of capital: paid by "
                            f"the share borrower — income, never an ACB "
                            f"reduction]")
                    elif (_foreign and self.foreign_return_of_capital
                          != 'acb'):
                        description = (
                            f"{description} [IB-designated return of "
                            f"capital, treated as a foreign dividend (ITA "
                            f"s.90(1)); a cost reduction only if the "
                            f"issuer reduced its paid-up capital, "
                            f"s.53(2)(b)(ii)]")
                    else:
                        # `amount` stays signed so IB's negative
                        # reversal rows net out (they become positive
                        # ADJUSTs).
                        transactions.append(self.tx_roc_adjust(
                            symbol=f"{ticker}.{ext}", currency=currency,
                            date=date, desc=description, amount=amount,
                            account='IB'))
                        transactions[-1]['_isin'] = isin
                        if len(isin) >= 2:
                            transactions[-1]['issuer_country'] = \
                                isin[:2].upper()
                        self.note_row_consumed()
                        continue
                action = 'DIVIDEND_IN_LIEU' if is_pil else 'DIVIDEND'
                # Type 'dividend' makes downstream tools skip ACB; PIL
                # uses a different type so dividend gain-entry emission
                # can exclude it cleanly (PIL is interest income, not
                # an eligible/qualified dividend).
                tx_type = 'dividend_in_lieu' if is_pil else 'dividend'

                # IB dividend descriptions include "USD 0.24 per
                # Share" — extract the per-share rate so the record
                # reconciles against the user's pool size and T5/
                # 1099-DIV. Qty is back-computed from amount/rate
                # (IB ships gross on the Dividend row; withholding
                # is in a separate section). Falls back to qty=
                # price=0 when the pattern doesn't match.
                qty, price = _parse_div_qty_rate(description, amount)
                transactions.append({
                    'action': action,
                    'date': date,
                    'time': '09:30:00',
                    'date_settle': date,
                    'symbol': f"{ticker}.{ext}",
                    'quantity': qty,
                    'price': price,
                    'currency': currency,
                    'net_amount': amount,
                    'gross_amount': amount,
                    'type': tx_type,
                    'account': 'IB',
                    'description': description,
                    '_isin': isin,      # income rebind check; popped
                })
                if len(isin) >= 2:
                    transactions[-1]['issuer_country'] = isin[:2].upper()
                if dealer_country:
                    transactions[-1]['dealer_country'] = dealer_country
                self.note_row_consumed()

            elif section == 'Open Positions':
                # Not emitted as transactions — read purely to seed the
                # income-reattribution holdings map (see
                # _reattribute_income_to_holdings). Summary rows only
                # (Lot rows repeat the same symbol per acquisition).
                try:
                    discr = row[header_map['DataDiscriminator']]
                    asset_cat = row[header_map['Asset Category']]
                    currency = _norm_ccy(row[header_map['Currency']])
                    symbol = row[header_map['Symbol']]
                    qty_raw = row[header_map['Quantity']]
                except (KeyError, IndexError):
                    self.count_skip(f"malformed {section} row")
                    continue
                if discr != 'Summary' or asset_cat != 'Stocks':
                    # Lot rows, option/futures summaries, subtotals:
                    # nothing the holdings seed needs.
                    self.count_nonevent(f"{section} row (not a stock "
                                        f"summary)")
                    continue
                try:
                    # Strict: a decimal comma is refused (a skip), not
                    # read 100x too large.
                    if abs(parse_strict_number(qty_raw,
                                               field='Quantity')) < 1e-9:
                        self.count_nonevent(f"{section} zero-quantity "
                                            f"row")
                        continue
                except ValueError:
                    self.count_skip(f"malformed {section} row")
                    continue
                self._check_symbol_tag(re.sub(
                    r'\.(TO|US|AX|L)$', '', symbol.strip(),
                    flags=re.IGNORECASE), shown_name(path))
                _op_full = _ib_stock_symbol(asset_cat, symbol, currency,
                                            fii)
                open_position_syms.add(_op_full)
                try:
                    open_position_qty[_op_full] = (
                        open_position_qty.get(_op_full, 0.0)
                        + parse_strict_number(qty_raw, field='Quantity'))
                except ValueError:
                    pass
                self.note_row_consumed()      # read into parser state

            elif section == 'Statement':
                # Capture the statement's period-end date so the
                # accrual diagnostic can tell a payable-now dividend
                # from a normal future-dated pending accrual.
                fn_idx = header_map.get('Field Name')
                fv_idx = header_map.get('Field Value')
                if (fn_idx is not None and fv_idx is not None
                        and fv_idx < len(row)
                        and row[fn_idx] == 'Period'):
                    raw = row[fv_idx]
                    end_part = raw.split(' - ')[-1].strip()
                    try:
                        statement_period_end = datetime.strptime(
                            end_part, "%B %d, %Y").strftime("%Y-%m-%d")
                    except ValueError:
                        statement_period_end = ''
                    self.note_row_consumed()  # read into parser state
                else:
                    self.count_nonevent("Statement metadata row")

            elif section == 'Change in Dividend Accruals':
                # Net 'Po' (accrual posted) against 'Re' (reversed) per
                # dividend. Keyed on (account, symbol, ex date, pay
                # date) — a Po/Re pair for the same dividend shares all
                # four and nets to zero; an un-reversed Po stays
                # positive. Not emitted as a transaction; only feeds
                # the end-of-parse diagnostic.
                # IB packs several codes into the cell ('ADR;Po' on an
                # ADR's accrual): match the Po/Re TOKEN, not the cell.
                _acc_tokens = set(re.split(r'[;,\s]+', self._cell(
                    row, header_map, 'Code')))
                code = ('Po' if 'Po' in _acc_tokens
                        else 'Re' if 'Re' in _acc_tokens else '')
                if code not in ('Po', 'Re'):
                    self.count_nonevent(f"{section} row without a "
                                        f"Po/Re code (subtotal)")
                    continue
                symbol = (row[header_map['Symbol']]
                          if 'Symbol' in header_map
                          and header_map['Symbol'] < len(row) else '')
                if not symbol:
                    self.count_nonevent(f"{section} row without a "
                                        f"symbol (subtotal)")
                    continue
                symbol = symbol.replace(' ', '.')
                account = (row[header_map['Account']]
                           if 'Account' in header_map
                           and header_map['Account'] < len(row) else '')
                ex_date = (row[header_map['Ex Date']]
                           if 'Ex Date' in header_map
                           and header_map['Ex Date'] < len(row) else '')
                pay_date = (row[header_map['Pay Date']]
                            if 'Pay Date' in header_map
                            and header_map['Pay Date'] < len(row) else '')
                currency = _norm_ccy(row[header_map['Currency']]
                                     if 'Currency' in header_map
                                     and header_map['Currency'] < len(row)
                                     else '')
                gross_idx = header_map.get('Gross Amount')
                if gross_idx is None or gross_idx >= len(row):
                    self.count_skip(f"malformed {section} row")
                    continue
                try:
                    gross = parse_strict_number(row[gross_idx],
                                                field='Gross Amount')
                except (ValueError, IndexError) as e:
                    # Name the row by its symbol and date only: the full
                    # row carries the IB account id.
                    _sym_i = header_map.get('Symbol')
                    _dt_i = header_map.get('Ex Date', header_map.get('Date'))
                    _sym = (row[_sym_i] if _sym_i is not None
                            and _sym_i < len(row) else '?')
                    _dt = (row[_dt_i] if _dt_i is not None
                           and _dt_i < len(row) else '?')
                    print(f"warning: skipping malformed IB {section} row "
                          f"({e}): symbol {_sym}, date {_dt}",
                          file=sys.stderr)
                    self.count_skip(f"malformed {section} row")
                    continue
                # Keyed on the EX date, not the pay date: IB revises
                # a dividend's pay date between the Po and the Re row
                # (an accrual can post with one pay date and reverse
                # with the next day's), and a pay-date key split the pair into two
                # half-open accruals — a false "accrued but not
                # booked" warning for a dividend already paid. The
                # pay date is only a fallback key when IB omits the
                # ex date. Latest row's pay date wins for display.
                key = ((account, symbol, 'ex', ex_date) if ex_date
                       else (account, symbol, 'pay', pay_date))
                accrual_net[key] = accrual_net.get(key, 0.0) + gross
                self.note_row_consumed()      # read into parser state
                _meta = accrual_meta.setdefault(key, {
                    'symbol': symbol, 'pay_date': pay_date,
                    'currency': currency, 'pay_dates': set(),
                })
                if pay_date:
                    _meta['pay_date'] = pay_date
                    _meta['pay_dates'].add(pay_date)
                if re.fullmatch(r'\d{4}-\d{2}-\d{2}', ex_date or ''):
                    _meta['ex_date'] = ex_date
                # The per-share rate and share count of the dividend, so
                # a Payment-in-Lieu row — which has neither in its own
                # description — can be reconciled to shares below. Taken
                # from the Po (posting) row, in the accrual's currency:
                # IB's Re row may carry a rate in the PAYMENT currency
                # (a USD accrual reversed at the CAD rate), so the first
                # row seen made the result depend on row order (audit
                # S060-13 / G3-0).
                if code == 'Po':
                    for _col, _fld in (('Gross Rate', 'po_rate'),
                                       ('Quantity', 'po_qty')):
                        _ci = header_map.get(_col)
                        if _ci is None or _ci >= len(row):
                            continue
                        try:
                            _v = abs(parse_strict_number(row[_ci],
                                                         field=_col))
                        except (ValueError, IndexError):
                            _v = 0.0
                        if _v:
                            _meta[_fld] = _v
                # The ex-dividend date of the dividend paid on pay_date
                # (a neutral fact on the posted Dividends row below; a
                # US project reads it for §852(b)(7), lib/income_dating).
                if (pay_date and re.fullmatch(r'\d{4}-\d{2}-\d{2}',
                                              ex_date or '')):
                    accrual_ex.setdefault((symbol, pay_date), ex_date)

            elif section == 'Withholding Tax':
                # Required columns — see the Dividends section.
                where = f"{shown_name(path)} line {lineno} ({section})"
                self.require_columns(
                    header_map, ('Currency', 'Date', 'Description',
                                 'Amount'), section=section, where=where)
                currency = self._cell(row, header_map, 'Currency')
                description = self._cell(row, header_map, 'Description')
                if 'Total' in currency:
                    self.count_nonevent(f"{section} subtotal row")
                    continue
                date = _ib_require_date(
                    self._cell(row, header_map, 'Date'), where)
                # IB books withholding as a NEGATIVE amount (cash out)
                # and a refund/correction as POSITIVE. The repo-wide
                # TAX convention is positive = tax withheld (RBC emits
                # it that way), so flip the sign rather than abs() it:
                # a charge stays positive and a refund nets NEGATIVE
                # instead of double-counting as more tax paid.
                _wht_cash = parse_strict_number(
                    self._cell(row, header_map, 'Amount'), field='Amount',
                    where=where)
                amount = -_wht_cash
                _book('Withholding Tax', currency, _wht_cash)

                # Description shape:  "SAMPLG (US0000000001) Cash Dividend..."
                # — pull the ticker AND the ISIN so the market suffix
                # comes from the security's country (same isin_map the
                # dividend handler uses above) rather than hardcoding
                # `.US`. Hardcoded `.US` fragmented the TAX symbol
                # from non-US dividends on the same security, so the
                # foreign-tax-credit pairing broke for any non-US
                # holding.
                ticker, isin = _ib_income_ticker_strict(description,
                                                        where, section)
                self._check_symbol_tag(ticker, where)       # A2-1493

                ext = (None if ticker == 'CASH'
                       else _isin_ext(isin, ticker, isin_fallback))

                transactions.append({
                    'action': 'TAX',
                    'date': date,
                    'time': '09:30:00',
                    'date_settle': date,
                    'symbol': f"{ticker}.{ext}" if ext else ticker,
                    'quantity': 0.0,
                    'currency': currency,
                    'net_amount': amount,
                    'type': 'tax',
                    'account': 'IB',
                    'description': description,
                    '_isin': isin,      # income rebind check; popped
                })
                self.note_row_consumed()

            elif section == 'Interest':
                # Required columns — see the Dividends section.
                where = f"{shown_name(path)} line {lineno} ({section})"
                self.require_columns(
                    header_map, ('Currency', 'Date', 'Description',
                                 'Amount'), section=section, where=where)
                currency = self._cell(row, header_map, 'Currency')
                description = self._cell(row, header_map, 'Description')
                if 'Total' in currency:
                    self.count_nonevent(f"{section} subtotal row")
                    continue
                date = _ib_require_date(
                    self._cell(row, header_map, 'Date'), where)
                amount = parse_strict_number(
                    self._cell(row, header_map, 'Amount'), field='Amount',
                    where=where)
                _book('Broker Interest Paid and Received', currency, amount)

                transactions.append({
                    'action': 'INTEREST',
                    'date': date,
                    'time': '09:30:00',
                    'date_settle': date,
                    'symbol': 'CASH',
                    'quantity': 0.0,
                    'currency': currency,
                    'net_amount': amount, # Preserve sign for interest paid vs charged
                    'type': 'interest',
                    'account': 'IB',
                    'description': description
                })
                self.note_row_consumed()

            elif section == 'Fees':
                # Required columns — see the Dividends section. (Date
                # may be BLANK on a row: the "for Mmm YYYY" fallback
                # below; the COLUMN must exist.)
                where = f"{shown_name(path)} line {lineno} ({section})"
                self.require_columns(
                    header_map, ('Currency', 'Date', 'Description',
                                 'Amount'), section=section, where=where)
                currency = self._cell(row, header_map, 'Currency')
                description = self._cell(row, header_map, 'Description')
                date = self._cell(row, header_map, 'Date')
                # Subtotal rows: 'Total' / 'Total in CAD' in the
                # Currency cell, OR a blank Currency with 'Total' in
                # the Subtitle/Description. The blank-currency form
                # used to fall through (defaulted to CAD, no date,
                # no 'for Mmm YYYY' hint) into the dateless-fees
                # warning, which then reported the subtotal ON TOP
                # of the rows it summed — a doubled total.
                _subtitle = (row[header_map['Subtitle']]
                             if 'Subtitle' in header_map
                             and header_map['Subtitle'] < len(row)
                             else '')
                # (Not by a word in the description: a "Nasdaq
                # TotalView" market-data charge is a real fee, audit
                # S058-03.)
                if ('Total' in currency or not currency
                        or 'Total' in _subtitle):
                    self.count_nonevent(f"{section} subtotal row")
                    continue

                # IB books a charge NEGATIVE (cash out) and a
                # refund/reversal POSITIVE. Repo FEE convention
                # (taxjson_fx_cash, .tt round-trip): positive =
                # charged — flip, like the Withholding Tax
                # branch does for TAX, so a market-data charge
                # is an outflow and a reversal nets against it.
                _fee_cash = parse_strict_number(
                    self._cell(row, header_map, 'Amount'), field='Amount',
                    where=where)
                amount = -_fee_cash
                if date:
                    date = _ib_require_date(date, where)
                else:
                    # Extract date from description if possible (e.g., "for Feb 2025")
                    date_match = re.search(r'for\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})', description, re.IGNORECASE)
                    if date_match:
                        mon, yr = date_match.groups()
                        mon_map = {
                            'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05', 'JUN': '06',
                            'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
                        }
                        date = f"{yr}-{mon_map.get(mon.upper())}-01"
                    else:
                        # No date column AND no "for Mmm YYYY" hint. The
                        # old fallback stamped a literal "2025-01-01"
                        # which silently mis-files the row in any other
                        # tax year. Skip + accumulate; a single summary
                        # line fires at the end of parse_file.
                        skipped_dateless_fees.append(amount)
                        self.count_skip(f"{section} row with no date "
                                        f"(see warning)")
                        continue

                _book('Other Fees', currency, _fee_cash)
                transactions.append({
                    'action': 'FEE',
                    'date': date,
                    'time': '09:30:00',
                    'date_settle': date,
                    'symbol': 'CASH',
                    'quantity': 0.0,
                    'currency': currency,
                    'net_amount': amount,
                    'type': 'fee',
                    'account': 'IB',
                    'description': description
                })
                self.note_row_consumed()

            elif section == 'Transaction Fees':
                # A per-fill BREAKDOWN of levies already inside the
                # trade's Comm/Fee (see the note at the top of
                # parse_file) — a recognized non-event.
                currency = self._cell(row, header_map, 'Currency')
                _tf_cat = self._cell(row, header_map, 'Asset Category')
                if (not currency or 'Total' in currency
                        or _tf_cat.startswith('Total')):
                    self.count_nonevent(f"{section} subtotal row")
                    continue
                self.count_nonevent(
                    f"{section} row (breakdown of a levy already in the "
                    f"trade's Comm/Fee)")

            elif section == 'Commission Adjustments':
                # Post-trade commission corrections, e.g.
                #   USD,2025-02-10,"Refund (QZW, -20 2025-01-07)",1.10
                # IB's Amount is signed cash (a refund POSITIVE).
                # Emitted as a FEE with the repo sign (positive =
                # charged) so a refund is a NEGATIVE fee that nets
                # against the original commission in fee totals.
                # Bound to the ticker named in the description
                # (the fee report groups by symbol); CASH when the
                # description names none.
                where = f"{shown_name(path)} line {lineno} ({section})"
                self.require_columns(
                    header_map, ('Currency', 'Date', 'Description',
                                 'Amount'), section=section, where=where)
                currency = self._cell(row, header_map, 'Currency')
                description = self._cell(row, header_map, 'Description')
                if not currency or 'Total' in currency:
                    self.count_nonevent(f"{section} subtotal row")
                    continue
                date = _ib_require_date(
                    self._cell(row, header_map, 'Date'), where)
                amount = parse_strict_number(
                    self._cell(row, header_map, 'Amount'), field='Amount',
                    where=where)
                _book('Commissions', currency, amount)
                _m = _IB_COMM_ADJ_TICKER_RE.search(description or '')
                if _m:
                    _raw_tk = _m.group(1).strip()
                    _occ = _ib_option_ref(self, _raw_tk, fii, root_alias,
                                          aliased_roots, where)
                    if _occ:
                        symbol = f"{_occ}.{_ib_currency_ext(currency)}"
                    else:
                        _tk = re.sub(r'\.(TO|US|AX|L)$', '',
                                     _raw_tk.replace(' ', '.'),
                                     flags=re.IGNORECASE)
                        # The listing rule of the trades (S010-06): a
                        # TSX USD unit's refund sits on SAMPMD.U.TO with its
                        # trades, not SAMPMD.U.US (audit A2-0086).
                        symbol = (f"{_tk}."
                                  f"{_ib_listing_ext('Stocks', _raw_tk, currency, fii)}")
                else:
                    symbol = 'CASH'
                transactions.append({
                    'action': 'FEE',
                    'date': date,
                    'time': '09:30:00',
                    'date_settle': date,
                    'symbol': symbol,
                    'quantity': 0.0,
                    'currency': currency,
                    'net_amount': -amount,
                    'type': 'fee',
                    'account': 'IB',
                    'description': f"{description} (Commission "
                                   f"Adjustments)",
                })
                self.note_row_consumed()
                _tm = _IB_COMM_ADJ_TRADE_RE.search(description or '')
                if _tm:
                    try:
                        _aq = parse_strict_number(_tm.group(2),
                                                  field='quantity')
                    except BrokerageParseError:
                        _aq = None
                    if _aq:
                        _adj_tk = _tm.group(1).strip()
                        _adj_occ = _ib_option_ref(self, _adj_tk, fii,
                                                  root_alias, aliased_roots,
                                                  where)
                        comm_adjustments.append({
                            'fee_tx': transactions[-1], 'amount': amount,
                            'ticker': (_adj_occ or re.sub(
                                r'\.(TO|US|AX|L)$', '',
                                _adj_tk.replace(' ', '.'),
                                flags=re.IGNORECASE)),
                            'qty': _aq, 'trade_date': _tm.group(3),
                            'currency': currency, 'date': date,
                            'key': (currency, date, round(amount, 6),
                                    description)})

            elif section == 'Corporate Actions':
                # Required columns resolved by header name; a missing
                # one fails the parse (Quantity/Value/Proceeds are the
                # share and cash legs, Code carries the `Ca` marker a
                # restatement depends on).
                where = f"{shown_name(path)} line {lineno} ({section})"
                self.require_columns(
                    header_map, ('Currency', 'Date/Time', 'Description',
                                 'Quantity', 'Proceeds', 'Value', 'Code'),
                    section=section, where=where)
                currency = self._cell(row, header_map, 'Currency')
                description = self._cell(row, header_map, 'Description')
                # Subtotal rows (Asset Category 'Total', blank
                # Currency) must be recognized BEFORE the currency
                # → suffix lookup: the blank currency used to reach
                # _ib_currency_ext and fire the "IB currency '' has
                # no exchange-suffix mapping" warning on every
                # statement with a Corporate Actions total.
                _ca_cat = self._cell(row, header_map, 'Asset Category')
                if (not currency or 'Total' in currency
                        or _ca_cat.startswith('Total')):
                    self.count_nonevent(f"{section} subtotal row")
                    continue

                date, time = _ib_split_datetime(
                    self._cell(row, header_map, 'Date/Time'), where)
                qty = parse_strict_number(
                    self._cell(row, header_map, 'Quantity'),
                    field='Quantity', where=where)
                val = parse_strict_number(
                    self._cell(row, header_map, 'Value'), field='Value',
                    where=where)
                # Cash actually paid (cash in lieu, a cash tender).
                proceeds = parse_strict_number(
                    self._cell(row, header_map, 'Proceeds'),
                    field='Proceeds', where=where)

                ext = _ib_currency_ext(currency)

                def _cx(ticker, _cat=_ca_cat or 'Stocks', _cur=currency,
                        _base=ext):
                    """The listing suffix of a corporate action's
                    stock: the S010-06 / A2-0081 listing rule of the
                    trades (a TSX USD unit stays SAMPMD.U.TO, an LSE USD line
                    .L) — the currency suffix alone put a split or cash
                    takeover of ZSP.U on ZSP.U.US while its trades sat
                    on ZSP.U.TO (audit A2-0086)."""
                    for _raw in (ticker, ticker.replace('.', ' ')):
                        _e = _ib_listing_ext(_cat, _raw, _cur, fii)
                        if _e != _base:
                            return _e
                    return _base

                # The security the row DELIVERS: the first token of its
                # trailing `(TICKER, NAME, ISIN)` parenthetical (the
                # tender branch's S013-06 reading, now shared by the
                # split, stock-dividend and cash-in-lieu branches —
                # audit A2-0085 / A2-0093 / A2-0257 / A2-1033).
                _dm_all = _IB_DELIVERED_RE.search(description)
                _deliv = (_dm_all.group(1).strip().replace(' ', '.')
                          if _dm_all else '')

                # Cancel/rebook restatements: IB re-lists a corrected
                # corporate action as original + `Ca` cancellation
                # (same description, negated quantity) + rebooked
                # rows. Every translated row records its effect in
                # `ca_effects`; a Ca row UNDOES its original's effect
                # — a cancelled split's legs come off the split (the
                # SPLIT row goes when no leg is left), a cancelled
                # cash-in-lieu / tender sale / spinoff removes the
                # rows it emitted, a cancelled untranslated merger
                # leaves the unhandled tally. Only the split branch
                # used to be spared the doubling; a restated split
                # 3:1 -> 2:1 emitted BOTH ratios. A Ca row whose
                # original is not (yet) seen waits for it; unmatched
                # at end of file it is a LOUD skip.
                _cacode = self._cell(row, header_map, 'Code')
                if 'Ca' in re.split(r'[;,\s]+', _cacode or ''):
                    _ca = {'desc': description, 'date': date,
                           'qty': qty, 'where': where,
                           'currency': currency, 'cat': _ca_cat}
                    ca_rows_seen.append(_ca)
                    _orig = _ca_undo(_ca, same_date_only=True)
                    if _orig:
                        ca_pairs.append((_ca, _orig['date']))
                        self.note_row_consumed()  # undid its original
                    else:
                        pending_ca.append(_ca)
                    continue

                # Tender / voluntary-offer journals (see tender_legs
                # above). A zero-proceeds leg only moves shares
                # between the ticker and its `.TEN` placeholder —
                # a recognized non-event, netted and noted at end
                # of parse. A leg carrying cash on a negative
                # quantity is the ACCEPTED cash tender: the shares
                # are gone and the cash is proceeds — booked as a
                # sale of the ROOT symbol (the placeholder never
                # entered the book) with a loud NOTE, so the
                # disposition can't vanish into the unhandled
                # tally the way every other non-split/spinoff
                # corporate action does.
                _eff = {'desc': description, 'date': date, 'qty': qty,
                        'kind': 'none', 'txs': [], 'consumed': False,
                        'currency': currency, 'cat': _ca_cat}

                def _ne(label, _eff=_eff):
                    # A recognized non-event Corporate Actions row: the
                    # label is kept on its effect, so a later `Ca` of the
                    # row takes it back out of the tally (audit A2-0600
                    # / A2-1030 / A2-1034).
                    _cat = _NE + label
                    self.count_skip(_cat)
                    _eff['skip_cat'] = _cat

                def _unbooked(msg, _eff=_eff):
                    unbooked_ca.append(msg)
                    _cat = f"{section} row not booked (see UNBOOKED warning)"
                    _eff.update(kind='unbooked', msg=msg, skip_cat=_cat)
                    self.count_skip(_cat)
                    _ca_record(_eff)

                # Every branch below builds STOCK symbols (ticker + the
                # currency suffix, multiplier 1). An option or futures
                # row (a contract adjustment on a split) booked there
                # became an equity SPLIT on an invented symbol while the
                # real contract never rolled (audit S058-16).
                if _ca_cat and _ca_cat not in ('Stocks', 'Warrants'):
                    if qty != 0 or abs(proceeds) > 0.005:
                        _unbooked(
                            f"{shown_name(path)} {date}: {_ca_cat} corporate "
                            f"action {description[:90]!r} (quantity "
                            f"{qty:g}) — contract adjustments are not "
                            f"translated; book the old and the adjusted "
                            f"contract by hand in a .tt file.")
                    else:
                        _ne(f"{section} zero-quantity row")
                        _ca_record(_eff)
                    continue

                _troot = ib_tender_root(description)
                if _troot is not None:
                    _tsym = f"{_troot}.{_cx(_troot)}"
                    _tl = tender_legs.setdefault(_tsym, {
                        'rows': 0, 'parked': 0.0, 'cash_qty': 0.0,
                        'cash': 0.0, 'dates': [], 'currency': currency})
                    _tl['rows'] += 1
                    _tl['dates'].append(date)
                    _eff['tender'] = _tl
                    # Which leg touches the placeholder line is
                    # fixed by the row kind, not by the leading
                    # token (IB may reuse the root's description
                    # on both legs): a `Tendered to` row's POSITIVE
                    # leg moves shares INTO the placeholder, a
                    # `Voluntary Offer Allocation` row's NEGATIVE
                    # leg moves them OUT.
                    _tender_in = 'tendered to' in description.lower()
                    _is_placeholder = _ib_tender_is_placeholder(
                        description, qty)
                    _eff.update(kind='tender', tender=_tl,
                                parked=qty if _is_placeholder else 0.0)
                    # An allocation that DELIVERS another security (a
                    # share-for-share exchange offer) is a merger, not a
                    # journal: it used to be netted as a no-op, the old
                    # shares staying in the book and the acquirer's
                    # never arriving (audit S013-06).
                    _dm = _IB_DELIVERED_RE.search(description)
                    _delivered = (_dm.group(1).strip().replace(' ', '.')
                                  if _dm else '')
                    if (not _tender_in and qty > 0 and _delivered
                            and _delivered.upper() != _troot.upper()):
                        _tl['foreign'] = True
                        _unbooked(
                            f"{shown_name(path)} {date}: tender/exchange offer "
                            f"for {_tsym} delivered {qty:g} {_delivered} "
                            f"(another security) — a share-for-share "
                            f"exchange is a disposition of {_tsym}"
                            f"{self.law(' (or a s.85.1 rollover)', ' (or a §354/§368 reorganization)', ' unless a tax-deferred rollover applies')}"
                            f": book it by hand in a .tt file.")
                        continue
                    if abs(proceeds) < 0.005:
                        if _is_placeholder:
                            _tl['parked'] += qty
                        _ne(f"{section} tender/voluntary-offer share "
                            f"journal (zero proceeds)")
                        _ca_record(_eff)
                        continue
                    if qty < 0:
                        _cash = abs(proceeds)
                        _ttx = {
                            'action': 'BUYSELL',
                            'date': date,
                            'time': time,
                            'date_settle': date,
                            'symbol': _tsym,
                            'quantity': qty,
                            'currency': currency,
                            'price': round(_cash / -qty, 8),
                            'fee': 0.0,
                            'net_amount': _cash,
                            'gross_amount': _cash,
                            'multiplier': 1.0,
                            'account': 'IB',
                            'description': description,
                        }
                        transactions.append(_ttx)
                        _tl['cash_qty'] += -qty
                        _tl['cash'] += _cash
                        if _is_placeholder:
                            _tl['parked'] += qty
                        _eff.update(txs=[_ttx], cash=_cash)
                        self.note_row_consumed()
                        _ca_record(_eff)
                        continue
                    # Cash on a POSITIVE leg is a shape we have not
                    # seen — loud bucket, never a silent drop.
                    _cat = (f"{section} tender row with proceeds on a "
                            f"positive quantity")
                    self.count_skip(_cat)
                    _eff['skip_cat'] = _cat
                    _ca_record(_eff)
                    continue

                # Split 3 for 2. NOT gated on qty > 0: a reverse split's
                # share-reduction leg arrives with NEGATIVE quantity (and
                # some events ship ONLY that leg) — the old `qty > 0`
                # gate silently dropped it, leaving the pool 10x too big.
                # The ratio math (new/old from the description) is
                # sign-independent; paired negative+positive legs are
                # collapsed by the per-file (symbol, date, ratio) key.
                handled = False

                # Cash in lieu of the fractional share a ratio left
                # over: a disposition of that fraction for the cash
                # — the same shape the RBC parser's CIL rows take.
                # Left unbooked, the fraction sat in the pool
                # forever and the cash was never proceeds. Checked
                # before the split branch so a CIL description
                # that also names the split is not read as a leg.
                cil_match = _IB_CIL_RE.search(description)
                if cil_match and qty < 0:
                    ticker = cil_match.group(1).strip().replace(' ', '.')
                    if (_deliv and _deliv.upper() != ticker.upper()
                            and not _deliv.upper().endswith('.OLD')):
                        # The fraction is of the DELIVERED security (a
                        # merger's or a renaming split's new line): the
                        # parent may no longer exist (audit A2-1033).
                        ticker = _deliv
                    symbol = f"{ticker}.{_cx(ticker)}"
                    self._check_symbol_tag(ticker, where)
                    # Proceeds is the cash paid (a required column; a
                    # blank one is refused). Value is IB's MARKET value
                    # of the fraction, never cash: an explicit 0 used
                    # to be replaced by it, booking a gain nobody was
                    # paid (audit S058-17).
                    cash = abs(proceeds)
                    _cil_msg = None
                    if cash < 0.005:
                        _cil_msg = (
                            f"warning: {where}: {symbol}: cash in lieu "
                            f"of {-qty:g} share(s) with Proceeds 0 — "
                            f"booked as a disposal for no cash (IB's "
                            f"Value {val:,.2f} is a market value, not "
                            f"cash paid). Check the statement.")
                        late_warnings.append(_cil_msg)
                    _ctx = {
                        'action': 'BUYSELL',
                        'date': date,
                        'time': time,
                        'date_settle': date,
                        'symbol': symbol,
                        'quantity': qty,
                        'currency': currency,
                        'price': round(cash / -qty, 8),
                        'net_amount': cash,
                        'multiplier': 1.0,
                        'account': 'IB',
                        'description': description
                    }
                    transactions.append(_ctx)
                    cil_seen.add((symbol, date, round(-qty, 6)))
                    _eff.update(kind='cil', txs=[_ctx], symbol=symbol,
                                frac=-qty, split=None, msg=_cil_msg)
                    # The fraction belongs to the split of this symbol
                    # NEAREST its date, within a week, in either row
                    # order: "the latest split on or before" folded an
                    # unrelated event's fraction into a split months
                    # later, and a CIL dated before its legs joined a
                    # split or not depending on row order (audit
                    # S058-18 / S060-14).
                    _near = [(abs(_days_between(d, date)), d, i)
                             for (s_, d, _r), i in emitted_splits.items()
                             if symbol in (s_, i['tx'].get('symbol_new'))
                             and abs(_days_between(d, date)) <= _IB_CIL_WINDOW]
                    if _near:
                        _info = min(_near, key=lambda x: (x[0], x[1]))[2]
                        _info['cil'] += -qty
                        _ib_refine_split_ratio(_info)
                        _eff['split'] = _info
                    else:
                        pending_cil.append({'symbol': symbol, 'date': date,
                                            'frac': -qty, 'eff': _eff})
                    handled = True

                # Ratio terms may be comma-grouped ('Split 1 for 1,000'):
                # the old [\d\.]+ stopped at the comma and read it as 1
                # for 1 (audit S058-19). Parsed strictly.
                split_match = re.search(r'^([A-Z0-9\s\.]+)\s*\(([^)]+)\)\s+Split\s+([\d\.,]+)\s+for\s+([\d\.,]+)', description, re.IGNORECASE)
                if split_match and not handled:
                    ticker = split_match.group(1).strip().replace(' ', '.')
                    self._check_symbol_tag(ticker, where)
                    new_sh = parse_strict_number(split_match.group(3),
                                                 field='split ratio',
                                                 where=where)
                    old_sh = parse_strict_number(split_match.group(4),
                                                 field='split ratio',
                                                 where=where)
                    ratio = new_sh / old_sh if old_sh != 0 else 1.0
                    symbol = f"{ticker}.{_cx(ticker)}"
                    split_key = (symbol, date, round(ratio, 9))
                    info = emitted_splits.get(split_key)
                    if info is None:
                        tx = {
                            'action': 'SPLIT',
                            'date': date,
                            'time': time,
                            'date_settle': date,
                            'symbol': symbol,
                            'symbol_new': symbol,
                            'quantity': ratio,
                            'currency': currency,
                            'account': 'IB',
                            'description': description
                        }
                        transactions.append(tx)
                        info = {'tx': tx, 'new': 0.0, 'old': 0.0,
                                'text_ratio': ratio, 'key': split_key,
                                'cil': 0.0}
                        emitted_splits[split_key] = info
                        for _c in [c for c in pending_cil
                                   if c['symbol'] == symbol
                                   and abs(_days_between(c['date'], date))
                                   <= _IB_CIL_WINDOW]:
                            info['cil'] += _c['frac']
                            _c['eff']['split'] = info
                            pending_cil.remove(_c)
                    if qty > 0:
                        info['new'] += qty
                    elif qty < 0:
                        info['old'] += -qty
                    _ib_refine_split_ratio(info)
                    _eff.update(kind='split', split=info)
                    if (qty > 0 and _deliv
                            and _deliv.upper() != ticker.upper()):
                        # The new leg names ANOTHER line: a split that
                        # also renames the security (OLDT 1-for-10 into
                        # NEWT). The pool moves to it — it stayed on the
                        # old ticker and the NEWT sale went short (audit
                        # A2-0085 / A2-0257). Tax-logic CA-ACB-04.
                        info['tx']['symbol_new'] = f"{_deliv}.{_cx(_deliv)}"
                        _eff['renames'] = True
                        for _c in [c for c in pending_cil
                                   if c['symbol'] == info['tx']['symbol_new']
                                   and abs(_days_between(c['date'], date))
                                   <= _IB_CIL_WINDOW]:
                            info['cil'] += _c['frac']
                            _c['eff']['split'] = info
                            pending_cil.remove(_c)
                        _ib_refine_split_ratio(info)
                    handled = True

                # Spinoff 1 for 10. Gated on `not handled` so a
                # description that happens to contain both "Split"
                # and "Spinoff" keywords is not read twice (the split
                # branch above already consumed it). A spin-off is a
                # TAX ELECTION (a dividend in kind at FMV, or s.86.1 /
                # a Canadian butterfly's ACB allocation), so its rows
                # are taxjson-corp-actions' — which carries IB's Value
                # as the broker FMV. This branch used to book every
                # one as a dividend at |Value| with no election
                # (2026-09 audit). Recognized, not booked, here.
                # A spin-off DEBIT (negative quantity) is a short
                # parent's delivery obligation for the spun-off shares.
                # taxjson-corp-actions books only the long side, so it
                # used to fall into the unhandled tally as a
                # "non-spin-off" row (audit S058-22).
                if (qty < 0 and not handled
                        and ib_spinoff_parts(description) is not None):
                    _unbooked(
                        f"{shown_name(path)} {date}: spin-off debit of {qty:g} on "
                        f"a SHORT parent position ({description[:90]!r}, "
                        f"Value {val:,.2f}) — the short spun-off position "
                        f"is not booked; add it by hand in a .tt file.")
                    continue
                if (qty > 0 and not handled
                        and ib_spinoff_parts(description) is not None):
                    _eff.update(kind='spinoff')
                    corp_owned_rows.append(description)
                    _ne(f"{section} spin-off row (booked by "
                        f"taxjson-corp-actions after the election)")
                    _ca_record(_eff)
                    continue

                # A cash takeover ('Merged(Acquisition) FOR USD 30.00
                # PER SHARE'): the shares are bought out for cash — a
                # disposition at the cash amount, booked as a sale. It
                # used to fall into the unhandled NOTE and the position
                # stayed in inventory forever (2026-09 audit).
                _cash_m = ib_cash_merger(description)
                if _cash_m is not None and not handled:
                    _ticker, _ccur, _per_sh = _cash_m
                    if qty < 0:
                        _cash = abs(proceeds) or round(_per_sh * -qty, 2)
                        _ttx = {
                            'action': 'BUYSELL',
                            'date': date,
                            'time': time,
                            'date_settle': date,
                            'symbol': f"{_ticker}.{_cx(_ticker)}",
                            'quantity': qty,
                            'currency': currency,
                            'price': round(_cash / -qty, 8),
                            'fee': 0.0,
                            'net_amount': _cash,
                            'gross_amount': _cash,
                            'multiplier': 1.0,
                            'account': 'IB',
                            'description': description,
                        }
                        transactions.append(_ttx)
                        _ct_msg = (f"{_ticker}.{_cx(_ticker)} {-qty:g} sh for "
                                   f"{_cash:.2f} {currency} on {date}")
                        cash_takeovers.append(_ct_msg)
                        _eff.update(kind='cash_takeover', txs=[_ttx],
                                    msg=_ct_msg)
                        self.note_row_consumed()
                    else:
                        _cat = (f"{section} cash-takeover row with a "
                                f"positive quantity")
                        self.count_skip(_cat)
                        _eff['skip_cat'] = _cat
                    _ca_record(_eff)
                    continue

                # A stock dividend: new shares delivered in kind. They
                # used to be dropped (a phantom short at the next full
                # sale — audit S058-24). Emitted as a NEUTRAL stock-
                # dividend event (a $0 BUYSELL typed stock_dividend, IB's
                # Value in the note): the gains engine applies the
                # country's rule (partition INPUTS-01).
                _sd = _IB_STOCK_DIV_RE.match(description)
                if _sd and not handled:
                    _sd_tk = _sd.group(1).strip().replace(' ', '.')
                    if (qty > 0 and _deliv
                            and _deliv.upper() != _sd_tk.upper()):
                        # Shares of ANOTHER security (another class:
                        # class A paying class C) are not new shares of the
                        # parent: booked into its pool they inflated it
                        # and the delivered line's sale went short. The
                        # cost split between two securities is each
                        # country's rule (CA-STKDIV-01, US-STKDIV-02):
                        # not guessed here (audit A2-0093).
                        _unbooked(
                            f"{shown_name(path)} {date}: stock dividend "
                            f"on {_sd_tk} paid {qty:g} share(s) of "
                            f"ANOTHER security, {_deliv} "
                            f"({description[:90]!r}) — not booked: enter "
                            f"the new shares and their cost by hand in a "
                            f".tt file (BUYSELL; a US filer also moves "
                            f"part of {_sd_tk}'s basis, §307).")
                        continue
                    if qty > 0:
                        _sym = f"{_sd_tk}.{_cx(_sd_tk)}"
                        _stx = {
                            'action': 'BUYSELL', 'date': date, 'time': time,
                            'date_settle': date, 'symbol': _sym,
                            'quantity': qty, 'currency': currency,
                            'price': 0.0, 'fee': 0.0, 'net_amount': 0.0,
                            'gross_amount': 0.0, 'multiplier': 1.0,
                            'account': 'IB', 'description': description,
                            'type': STOCK_DIVIDEND,
                        }
                        transactions.append(_stx)
                        _msg = (f"{_sym}: stock dividend of {qty:g} "
                                f"share(s) on {date} (IB Value {val:,.2f} "
                                f"{currency}) booked as a stock-dividend "
                                f"event — the gains run applies your "
                                f"country's rule to its cost (`taxjson "
                                f"tax-logic`).")
                        stock_dividends.append(_msg)
                        _eff.update(kind='stockdiv', txs=[_stx], msg=_msg)
                        self.note_row_consumed()
                        _ca_record(_eff)
                    else:
                        _unbooked(f"{shown_name(path)} {date}: stock dividend row "
                                  f"with quantity {qty:g} "
                                  f"({description[:90]!r}) — not booked.")
                    continue

                # A share-for-share merger (or a merger shape nothing
                # can book, which becomes a blocking `unsupported`
                # event): taxjson-corp-actions owns it, after the
                # election. Not an unhandled row.
                if not handled and ib_merger_owned(description):
                    corp_owned_rows.append(description)
                    _eff.update(kind='corp_owned')
                    _ne(f"{section} merger row (booked by "
                        f"taxjson-corp-actions after the election)")
                    _ca_record(_eff)
                    continue

                # Anything else (Merger/Acquisition, name change, etc.)
                # — IB's row format varies by event type and matching
                # paired rows safely is tricky. Track per ticker so we
                # can warn the user once at end of parse. We pull a
                # rough ticker out of the description's leading token.
                if handled:
                    self.note_row_consumed()
                elif qty != 0:
                    m = re.match(r'\s*([A-Z0-9\.]{1,12})', description)
                    ticker = m.group(1) if m else '<unknown>'
                    unhandled_ca_tickers[ticker] = unhandled_ca_tickers.get(ticker, 0) + 1
                    _cat = (f"{section} row not translated (see UNBOOKED "
                            f"warning)")
                    self.count_skip(_cat)
                    _eff.update(kind='unhandled', ticker=ticker,
                                skip_cat=_cat)
                else:
                    _ne(f"{section} zero-quantity row")
                _ca_record(_eff)

            elif section == 'Transfers':
                # Required columns resolved by header name; a missing
                # one fails the parse.
                where = f"{shown_name(path)} line {lineno} ({section})"
                self.require_columns(
                    header_map, ('Asset Category', 'Type', 'Symbol',
                                 'Currency', 'Date', 'Qty',
                                 'Market Value', 'Code'),
                    section=section, where=where)
                asset_cat = self._cell(row, header_map, 'Asset Category')
                transfer_type = self._cell(row, header_map, 'Type')
                symbol = self._cell(row, header_map, 'Symbol')
                # The description names the SECURITY (the raw IB symbol,
                # as on Trades rows) next to the transfer kind: the
                # security overrides key on it, and a kind-only
                # description ('ACATS') left the transfer on the
                # un-overridden listing — the pool split (audit S059-03).
                xfer_desc = f"{transfer_type} ({symbol})"
                currency = self._cell(row, header_map, 'Currency')
                qty_str = self._cell(row, header_map, 'Qty')
                if not asset_cat or asset_cat.startswith('Total'):
                    self.count_nonevent(f"{section} subtotal row")
                    continue
                if asset_cat not in ('Stocks', 'Equity and Index Options',
                                     'Warrants', 'Futures',
                                     'Options On Futures'):
                    # Cash movements (no security) are custody of
                    # money, not property; any other asset class is a
                    # security the parser has no branch for — loud.
                    if asset_cat in ('Cash', 'Forex') or not symbol:
                        self.count_nonevent(f"{section} cash row")
                    else:
                        self.count_skip(f"{section}/{asset_cat}")
                    continue
                # A security transfer without a quantity moves shares
                # we cannot count: refused (it was a calm non-event,
                # and the position change was lost — audit R1-55).
                qty = parse_strict_number(qty_str, field='Qty',
                                          where=where)
                date = _ib_require_date(
                    self._cell(row, header_map, 'Date'), where)

                # Skip cash transfers
                if symbol.startswith('CASH.'):
                    self.count_nonevent(f"{section} cash transfer")
                    continue

                total_cost = parse_strict_number(
                    self._cell(row, header_map, 'Market Value'),
                    field='Market Value', where=where)

                multiplier = self._multiplier(asset_cat, symbol, fii,
                                              where)
                if asset_cat in ('Equity and Index Options',
                                 'Options On Futures'):
                    symbol = self._option_symbol(
                        symbol, asset_cat, fii, root_alias,
                        aliased_roots, where)
                    if asset_cat == 'Options On Futures':
                        symbol = f"F:{symbol}"
                elif asset_cat == 'Futures':
                    symbol = f"F:{symbol.replace(' ', '.')}"
                else:
                    symbol = symbol.replace(' ', '.')
                    self._check_symbol_tag(re.sub(
                        r'\.(TO|US|AX|L)$', '', symbol, flags=re.IGNORECASE),
                        where)

                symbol = re.sub(r'\.(TO|US|AX|L)$', '', symbol, flags=re.IGNORECASE)
                ext = (_ib_listing_ext(asset_cat, self._cell(
                           row, header_map, 'Symbol'), currency, fii)
                       if asset_cat in ('Stocks', 'Warrants')
                       else _ib_currency_ext(currency))
                symbol = f"{symbol}.{ext}"

                abs_qty = abs(qty)
                price = (round(abs(total_cost) / (abs_qty * multiplier), 8)
                         if abs_qty > 1e-6 and multiplier else 0.0)

                # Cancel/rebook: IB lists a reversed ACATS/ATON leg
                # as original + `Ca` cancellation (opposite qty) +
                # rebooked rows — a transfer reversed and rebooked
                # twice reads Out, Ca, Out, Ca, Out: arithmetically -N,
                # but each Ca leg reads as a +N ACQUISITION to the
                # superficial-loss walk. The Ca row consumes its
                # original wherever it sits in the statement: the
                # latest earlier-or-same-dated one before it (same
                # date preferred — a Ca posted days later used to miss
                # it, audit R1-61), else the next one after it (a Ca
                # listed first, R1-300). Only a Ca whose original is in
                # an earlier statement stays as a netting leg (end of
                # the walk).
                _xcode = self._cell(row, header_map, 'Code')
                _xleg = {'symbol': symbol, 'desc': xfer_desc,
                         'qty': qty, 'date': date}
                if 'Ca' in re.split(r'[;,\s]+', _xcode or ''):
                    self.xfer_ca_keys.add((symbol, xfer_desc, date, qty))
                    _hits = [
                        _i for _i, _t in enumerate(transactions)
                        if (_t.get('action') == 'TRANSFER'
                            and _t.get('symbol') == symbol
                            and (_t.get('date') or '') <= date
                            and abs(float(_t.get('quantity') or 0)
                                    + qty) < 1e-9
                            and _t.get('description') == xfer_desc
                            and not _before_period(_t.get('date')))]
                    _same = [_i for _i in _hits
                             if transactions[_i].get('date') == date]
                    if _hits:
                        _xo = transactions[(_same or _hits)[-1]]
                        self.xfer_pairs.append((
                            (symbol, xfer_desc, _xo.get('date'),
                             float(_xo.get('quantity') or 0)),
                            (symbol, xfer_desc, date, qty)))
                        del transactions[(_same or _hits)[-1]]
                        self.note_row_consumed()  # + original
                    else:
                        pending_xfer_ca.append(dict(
                            _xleg, currency=currency, price=price,
                            net=abs(total_cost), kind=transfer_type))
                    continue

                _waiting = [_c for _c in pending_xfer_ca
                            if _c['symbol'] == symbol
                            and _c['desc'] == xfer_desc
                            and date <= _c['date']
                            and abs(_c['qty'] + qty) < 1e-9]
                if _waiting and _before_period(date):
                    _waiting = []               # a rebook: reconcile_files
                if _waiting:
                    _c = next((_c for _c in _waiting if _c['date'] == date),
                              _waiting[0])
                    pending_xfer_ca.remove(_c)
                    self.xfer_pairs.append((
                        (symbol, xfer_desc, date, qty),
                        (symbol, xfer_desc, _c['date'], _c['qty'])))
                    self.note_row_consumed()      # this original
                    self.note_row_consumed()      # the waiting Ca row
                    continue

                transactions.append({
                    'action': 'TRANSFER',
                    'date': date,
                    'time': '09:30:00',
                    'date_settle': date,
                    'symbol': symbol,
                    'quantity': qty,
                    'currency': currency,
                    'price': price,
                    'net_amount': abs(total_cost),
                    'account': 'IB',
                    'description': xfer_desc
                })
                _name = self._security_name(
                    asset_cat, self._cell(row, header_map, 'Symbol'), fii)
                if _name:
                    transactions[-1]['security_name'] = _name
                self.note_row_consumed()

            elif section in _IB_METADATA_SECTIONS:
                # Statement metadata / roll-ups (Account Information,
                # Net Asset Value, Cash Report, Mark-to-Market,
                # Financial Instrument Information, ...) — not events:
                # counted under the calmer note, never dropped
                # unaccounted.
                self.count_nonevent(f"section {section} (not "
                                    f"translated)")
            else:
                # A section with no branch and not on the metadata
                # allowlist: a renamed/localized money section
                # ("Dividendes", "Transactions") or one IB added.
                # With money-like columns it is an ERROR (its cash
                # would silently vanish); otherwise a loud skip.
                _money = sorted(_IB_MONEY_COLUMNS & set(header_map))
                if _money:
                    raise BrokerageParseError(
                        f"{shown_name(path)} line {lineno}: unknown IB section "
                        f"{section!r} carries money columns "
                        f"({', '.join(_money)}) and has no parser branch "
                        f"— refusing to drop its rows. Re-export the "
                        f"statement in English, or report the section.")
                if section not in unknown_sections:
                    unknown_sections.add(section)
                    print(f"warning: {shown_name(path)}: unknown IB section "
                          f"{section!r} (no parser branch, not known "
                          f"metadata) — its rows are skipped; check "
                          f"they carry no trades or income.",
                          file=sys.stderr)
                self.count_skip(f"section {section} (unknown)")

        if unknown_row_types:
            _kinds = ', '.join(f"{k!r} x{n}"
                               for k, n in sorted(unknown_row_types.items()))
            if self._rows_seen == sum(unknown_row_types.values()):
                raise BrokerageParseError(
                    f"{shown_name(path)}: the statement has Header rows but no "
                    f"'Data' rows (row types {_kinds}) — not IB's CSV "
                    f"layout; refusing to read it as an empty statement. "
                    f"Re-download the Activity Statement CSV.")
            print(f"warning: {shown_name(path)}: rows of a type IB does not "
                  f"write were skipped ({_kinds}) — check they carry no "
                  f"trades or income.", file=sys.stderr)

        # Transfers `Ca` rows no original in this statement claimed: the
        # original is in an earlier statement — kept as a reversing leg.
        for _c in pending_xfer_ca:
            _leg = {
                'action': 'TRANSFER', 'date': _c['date'],
                'time': '09:30:00', 'date_settle': _c['date'],
                'symbol': _c['symbol'], 'quantity': _c['qty'],
                'currency': _c['currency'], 'price': _c['price'],
                'net_amount': _c['net'], 'account': 'IB',
                'description': f"{_c['desc']} (Ca)",
            }
            if any(_ib_xfer_cancels(t, _leg) for t in transactions):
                # Only a rebook dated before this statement's period is
                # here: the original is looked for in the account's
                # other statements first (reconcile_files).
                self.held_xfer_cas.append(_leg)
            else:
                print(f"note: {_c['symbol']}: IB cancelled a "
                      f"{_c['kind']} transfer of {-_c['qty']:g} on "
                      f"{_c['date']} whose original row is not in this "
                      f"statement — kept as a reversing TRANSFER leg.",
                      file=sys.stderr)
            transactions.append(_leg)
            self.note_row_consumed()

        # Corporate Actions `Ca` rows with no same-date original: the
        # latest earlier original in this statement, if any.
        for _ca in list(pending_ca):
            _orig = _ca_undo(_ca)
            if _orig:
                pending_ca.remove(_ca)
                ca_pairs.append((_ca, _orig['date']))
                self.note_row_consumed()

        # Trades `Ca` rows (keys of the originals they reverse) and the
        # pairs dropped here — offered to the account's overlapping
        # statements (reconcile_files, audit A2-0259 / A2-0088).
        self.trade_ca_keys = {_ib_trade_key(c, negate=True)
                              for c in trade_cancels}
        self.trade_pairs: List[tuple] = []
        # A Ca dated before the statement's period (its candidates are
        # too: a Ca shares its original's date) waits for
        # reconcile_files, which looks in the account's earlier
        # statements first (audit A2-0886).
        self.held_trade_cas = [c for c in trade_cancels
                               if _before_period(c.get('date'))]
        _held = {id(c) for c in self.held_trade_cas}
        if trade_cancels and len(_held) < len(trade_cancels):
            _partials: list = []
            _kept, _pairs, _unpaired = pair_cancellations(
                [t for t in transactions if id(t) not in _held],
                partials=_partials)
            _kept += [t for t in transactions if id(t) in _held]
            self.trade_pairs = [(dict(_o), dict(_c)) for _o, _c in _pairs]
            # A Ca of one execution of a multi-fill order (A2-0298).
            for _o, _c, _r in _partials:
                print(f"note: {shown_name(path)}: IB cancelled (Ca) "
                      f"{-_c['quantity']:g} of the {_o['symbol']} order of "
                      f"{_o['quantity']:g} @ {_o['price']:g} on "
                      f"{_o['date']} (one execution) — the order is "
                      f"booked as {_r['quantity']:g}.", file=sys.stderr)
            _gone = {id(t) for pr in _pairs for t in pr}
            transactions[:] = _kept
            expiry_txs[:] = [t for t in expiry_txs if id(t) not in _gone]
            for _o, _c in _pairs:
                print(f"note: {shown_name(path)}: IB cancelled (Ca) the "
                      f"{_o['symbol']} trade of {_o['quantity']:g} @ "
                      f"{_o['price']:g} on {_o['date']} — the trade and "
                      f"its cancellation are both dropped.",
                      file=sys.stderr)
            for _c in _unpaired:
                print(f"note: {shown_name(path)}: IB cancelled (Ca) a "
                      f"{_c['symbol']} trade of {-_c['quantity']:g} @ "
                      f"{_c['price']:g} on {_c['date']} whose original "
                      f"row is not in this statement — kept as a "
                      f"cancellation leg; taxjson-merge2 (taxjson run) "
                      f"drops it with the original from the account's "
                      f"other statement.", file=sys.stderr)

        # A commission refund (or correction) naming its trade changes
        # what that trade cost: the outlay is the commission net of the
        # refund (tax-logic CA-ACB-COMMREFUND / US-BASIS-COMMREFUND). It
        # was a stand-alone FEE row the gains never saw — a refunded
        # purchase commission stayed in the ACB (audit R1-58). Folded
        # into the ONE matching trade of this statement; otherwise kept
        # as the FEE row, and said.
        # A refund whose trade is in ANOTHER statement of the account
        # (a December trade refunded in January) is folded there by
        # reconcile_files (audit A2-0605 / A2-1032 / A2-1036); every
        # fold made here is repeated on an overlapping statement's copy
        # of the trade that lacks the refund row, so dedup still sees one
        # trade (A2-0258 / A2-0088).
        _folded = 0
        self.refund_folds: List[Dict[str, Any]] = []
        self.unmatched_refunds: List[Dict[str, Any]] = []
        self.refund_seen = {_adj['key'] for _adj in comm_adjustments}
        for _adj in comm_adjustments:
            _hits = _ib_refund_hits(_adj, transactions)
            if len(_hits) != 1:
                if not _hits and ctx is not None:
                    # The account's other statements are searched once
                    # every one is parsed; the note is said there.
                    self.unmatched_refunds.append(_adj)
                    continue
                print(f"note: {shown_name(path)}: commission adjustment of "
                      f"{_adj['amount']:+.2f} {_adj['currency']} on "
                      f"{_adj['date']} ({_adj['ticker']} {_adj['qty']:g} on "
                      f"{_adj['trade_date']}) matches "
                      f"{'no' if not _hits else len(_hits)} trade(s) in "
                      f"this statement — kept as a FEE row, outside the "
                      f"trade's cost; adjust the trade by hand if it is "
                      f"in a taxable account.", file=sys.stderr)
                continue
            _t = _hits[0]
            self.refund_folds.append({
                'key': _ib_trade_key(_t), 'fee': float(_t.get('fee') or 0.0),
                'amount': _adj['amount'], 'refund': _adj['key']})
            _ib_fold_refund(_t, _adj['amount'])   # cash: a refund is +
            transactions[:] = [t for t in transactions
                               if t is not _adj['fee_tx']]
            _folded += 1
        if _folded:
            print(f"note: {shown_name(path)}: {_folded} commission "
                  f"adjustment(s) folded into the trade(s) they name "
                  f"(a refund lowers a purchase's cost / raises a sale's "
                  f"proceeds).", file=sys.stderr)

        # `Ca` rows whose original never appeared: a cancellation of
        # something booked in an EARLIER statement (or a restatement
        # this parser cannot pair). Nothing is guessed — loud skip.
        # Kept for taxjson-brokerage, which offers each unmatched Ca row
        # to the account's OTHER statements (a split booked in the 2025
        # statement and cancelled in the 2026 one — audit S059-04).
        # Under an account context it also prints the warning for the
        # rows no statement matched; a lone parse warns here.
        self.ca_undo = _ca_undo
        self.unmatched_ca = list(pending_ca)
        self.ca_seen = ca_rows_seen
        self.ca_pairs = ca_pairs
        for _ca in pending_ca:
            if self.account_context is None:
                print(unmatched_ca_warning(_ca), file=sys.stderr)
            self.count_skip(_IB_UNMATCHED_CA_SKIP)

        # Transaction Fees are inside Comm/Fee while the Cash Report
        # books them on their own line, so the commission identity is
        #   sum(Comm/Fee) + Commission Adjustments
        #     == Commissions + Transaction Fees.
        self._reconcile_cash_report(pre, cash_booked, path)

        # A fraction whose split is in ANOTHER statement of the account
        # (a Dec 30 reverse split, its cash in lieu paid Jan 5) joins it
        # there (reconcile_files, audit A2-1037): the split's new leg
        # counts the fraction, and the sale below removes it once.
        def _cil_join(c: Dict[str, Any]) -> bool:
            near = [(abs(_days_between(d, c['date'])), d, i)
                    for (s_, d, _r), i in emitted_splits.items()
                    if c['symbol'] in (s_, i['tx'].get('symbol_new'))
                    and abs(_days_between(d, c['date'])) <= _IB_CIL_WINDOW]
            if not near:
                return False
            info = min(near, key=lambda x: (x[0], x[1]))[2]
            info['cil'] += c['frac']
            _ib_refine_split_ratio(info)
            return True

        self.cil_join = _cil_join
        self.cil_seen = cil_seen
        self.unmatched_cil = [
            {'symbol': _c['symbol'], 'date': _c['date'], 'frac': _c['frac'],
             'where': shown_name(path)} for _c in pending_cil]
        if ctx is None:
            for _c in self.unmatched_cil:
                print(_ib_cil_unmatched_note(_c), file=sys.stderr)

        for _tsym, _tl in sorted(tender_legs.items()):
            if _tl['rows'] <= 0:
                continue                # every row was cancelled (Ca)
            _dates = ', '.join(sorted(set(_tl['dates'])))
            if _tl['cash_qty'] > 0:
                # taxjson-corp-actions skips tender rows, so no election
                # can replace this sale (audit S059-05): the advice is a
                # hand booking.
                print(
                    f"NOTE: {_tsym}: tender/voluntary offer settled for "
                    f"cash — {_tl['cash_qty']:g} share(s) disposed for "
                    f"{_tl['cash']:.2f} {_tl['currency']} ({_dates}); "
                    f"booked as a sale. If the offer also delivered new "
                    f"shares (a share-for-share or mixed exchange), this "
                    f"sale is wrong: remove the tender rows from the export "
                    f"and book the exchange by hand in a .tt file.",
                    file=sys.stderr)
            _acct_parked = ((ctx or {}).get('tender_parked') or {}).get(
                _tsym)
            if (abs(_tl['parked']) > 1e-9 and _acct_parked is not None
                    and abs(_acct_parked) < 1e-9):
                # Parked in one statement, resolved in another of the
                # account's (audit S059-06): no outcome is missing.
                print(
                    f"note: {_tsym}: {_tl['rows']} tender/voluntary-offer "
                    f"journal row(s) ({_dates}) moved {_tl['parked']:g} "
                    f"share(s) to or from the tender placeholder; the "
                    f"account's other statement completes the round trip "
                    f"— recognized no-op, nothing booked.", file=sys.stderr)
            elif abs(_tl['parked']) > 1e-9:
                print(
                    f"warning: {_tsym}: {_tl['parked']:g} share(s) still "
                    f"sit on the tender placeholder line at period end "
                    f"({_dates}) — the offer's outcome (shares returned "
                    f"or cash paid) is in a later statement; nothing was "
                    f"booked for them here.", file=sys.stderr)
            elif _tl['cash_qty'] == 0 and not _tl.get('foreign'):
                print(
                    f"note: {_tsym}: {_tl['rows']} tender/voluntary-offer "
                    f"journal row(s) ({_dates}) moved shares to and from "
                    f"the tender placeholder with no cash — recognized "
                    f"no-op, nothing booked.", file=sys.stderr)

        # Back-fill quantity/price for dividend rows that had no per-share rate
        # in their own description — chiefly Payment-in-Lieu rows, which IB
        # ships as just "…Payment in Lieu of Dividend" with no rate or share
        # count. The rate lives in the Change in Dividend Accruals section
        # (same dividend, keyed by symbol + pay date), so shares = amount /
        # rate — reconciling a PIL exactly like a normal dividend. Only qty and
        # price are set; net_amount (the income) is untouched.
        def _days_apart(a: str, b: str) -> Optional[int]:
            try:
                return abs((datetime.strptime(a, "%Y-%m-%d")
                            - datetime.strptime(b, "%Y-%m-%d")).days)
            except (TypeError, ValueError):
                return None

        def _same_acct(accts) -> bool:
            mine = set(pre['accounts'])
            return not accts or not mine or bool(set(accts) & mine)

        # The accrual facts of the account's OTHER statements (same IB
        # account): a dividend whose Po/Re rows sit in the previous
        # statement kept no ex date or share count (audit A2-0601).
        _ext_metas = [m for m in ((ctx or {}).get('accrual_facts') or ())
                      if m.get('file') != path.name
                      and _same_acct(m.get('accounts'))]

        def _accrual_for(ticker: str, date: str, currency: str):
            """The accrual of the dividend a posting on `date` pays: same
            ticker, a pay date within a week (the exact date first), the
            posting's currency first — two listings of one ticker can pay
            on the same day (audit S057-22 / S058-05), and IB posts some
            dividends a day or more after the accrued pay date
            (S058-12). This statement's accruals first, then the
            account's other statements'."""
            best = None
            for _n, meta in enumerate(list(accrual_meta.values())
                                      + _ext_metas):
                if meta['symbol'] != ticker:
                    continue
                dists = [d for d in (_days_apart(pd, date) for pd in
                                     (meta['pay_dates'] or {meta['pay_date']}))
                         if d is not None]
                if not dists or min(dists) > 7:
                    continue
                rank = (min(dists), meta['currency'] != currency,
                        _n >= len(accrual_meta))
                if best is None or rank < best[0]:
                    best = (rank, meta)
            return best[1] if best else None

        for tx in transactions:
            if tx.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU'):
                continue
            ticker = (tx.get('symbol') or '').rsplit('.', 1)[0]
            meta = _accrual_for(ticker, tx.get('date') or '',
                                tx.get('currency') or '')
            # The ex date of the accrual this posting pays — matched the
            # way the share count is (a posting a day after the accrued
            # pay date kept no ex date, so the US §852(b)(7) advisory
            # never fired: audit A2-0602).
            _ex = (meta or {}).get('ex_date') or accrual_ex.get(
                (ticker, tx.get('date')))
            if _ex and _ex <= (tx.get('date') or ''):
                tx['ex_date'] = _ex
            if tx.get('price'):          # description already gave a rate
                continue
            # SIGNED amount: a reversal row back-computes a NEGATIVE share
            # count, matching _parse_div_qty_rate's convention (qty carries
            # the row's sign; the positive per-share rate stays positive).
            amt = float(tx.get('net_amount') or 0.0)
            if meta is None or not amt:
                continue
            rate = meta.get('po_rate') or 0.0
            if meta['currency'] == tx.get('currency') and rate > 0:
                tx['price'] = rate
                q = round(amt / rate, 8)
                # Same cent-rounding snap as _parse_div_qty_rate: the
                # paid amount is rounded to cents, so the division
                # lands near — not on — the true share count.
                nearest = round(q)
                if nearest != 0 and abs(abs(amt) - abs(nearest) * rate) <= 0.005 + 1e-9:
                    q = float(nearest)
                tx['quantity'] = q
            elif meta.get('po_qty') and not any(
                    o is not tx and o.get('action') == 'DIVIDEND'
                    and o.get('symbol') == tx.get('symbol')
                    and o.get('date') == tx.get('date')
                    for o in transactions):
                # The accrual is in another currency than the cash (IB
                # accrues a CAD-paid dividend in USD): its rate does not
                # apply, but its share count does when this row is the
                # whole payment (no ordinary dividend beside it).
                q = meta['po_qty'] if amt > 0 else -meta['po_qty']
                tx['quantity'] = q
                tx['price'] = round(abs(amt) / meta['po_qty'], 8)

        def _print_ca_side_effects() -> None:
            """The lines a translated Corporate Actions row leaves (a
            late cash in lieu, a cash takeover, a stock dividend, an
            unbooked or unhandled row). `_ca_apply_undo` takes an undone
            event's line out of these lists, so they print only after
            every cancellation that can reach this statement has run."""
            _flush_ca_side_effects(path, late_warnings, cash_takeovers,
                                   corp_owned_rows, stock_dividends,
                                   unbooked_ca, unhandled_ca_tickers)

        if self.defer_ca_messages:
            # taxjson-brokerage runs reconcile_files, whose Ca pass can
            # undo an event of this statement from another one (re-audit
            # A2-1091): it prints the lines after that pass.
            self.ca_side_effects = _print_ca_side_effects
        else:
            _print_ca_side_effects()

        if skipped_dateless_fees:
            total = sum(skipped_dateless_fees)
            print(
                f"warning: skipped {len(skipped_dateless_fees)} IB Fees row(s) "
                f"with no date and no 'for Mmm YYYY' hint (total: {total:.2f}). "
                f"Edit the source CSV to add dates if these should appear in "
                f"FEE totals.",
                file=sys.stderr,
            )

        # Open accruals: a positive net (Po not yet reversed) whose cash
        # dividend isn't already posted in this file's Dividends section,
        # and whose pay date falls on or before the statement period end
        # (a future-dated accrual is a normal pending dividend, not a
        # missed one). When the period end is unknown, don't filter on it.
        # Each posted dividend pays ONE accrual: exact pay-date matches
        # first (every accrual, reversed or not), then a posting within a
        # week for the accruals left — so another week's dividend of a
        # weekly payer no longer hides an unpaid one (audit S059-08).
        # Postings in the account's OTHER statements count too: a
        # December accrual is often paid in the next year's statement
        # (R1-327).
        postings = list(posted_dividend_keys) + [
            (_s, _d) for _n, _s, _d, _a in
            ((ctx or {}).get('posted_dividends') or ())
            if _n != path.name and _same_acct(_a)]
        used = [False] * len(postings)

        def _claim(meta, window: int) -> bool:
            pds = meta['pay_dates'] or {meta['pay_date']}
            best = None
            for _i, (_s, _d) in enumerate(postings):
                if used[_i] or _s != meta['symbol']:
                    continue
                dists = [x for x in (_days_apart(_d, pd) for pd in pds)
                         if x is not None]
                if dists and min(dists) <= window and (
                        best is None or min(dists) < best[0]):
                    best = (min(dists), _i)
            if best is None:
                return False
            used[best[1]] = True
            return True

        paid = set()
        for key, meta in accrual_meta.items():
            if _claim(meta, 0):
                paid.add(key)
        for want_open in (False, True):
            for key, meta in accrual_meta.items():
                if key in paid or (accrual_net[key] > 0.01) != want_open:
                    continue
                if _claim(meta, 7):
                    paid.add(key)

        open_accruals = []
        for key, net in accrual_net.items():
            if net <= 0.01 or key in paid:
                continue
            meta = accrual_meta[key]
            if (statement_period_end and meta['pay_date']
                    and meta['pay_date'] > statement_period_end):
                continue
            open_accruals.append(
                (meta['symbol'], meta['pay_date'], net, meta['currency']))
        if open_accruals:
            open_accruals.sort()
            details = '; '.join(
                f"{sym} pay:{pay or '?'} ~{amt:.2f} {cur}".rstrip()
                for sym, pay, amt, cur in open_accruals
            )
            # ATTENTION: income the books may be missing (the run's
            # console, not only .sum/.diag — audit A2-0264).
            print(
                f"{ATTENTION_PREFIX} {len(open_accruals)} dividend(s) in {shown_name(path)} are "
                f"accrued but not yet booked as posted dividends ({details}). "
                f"IB books the cash with a lag — accruals are estimates and "
                f"are NOT counted as income. If a more recent statement "
                f"doesn't already show these as posted dividends, re-download "
                f"it before filing so the income is captured.",
                file=sys.stderr,
            )

        # Bind each dividend/withholding-tax row to the listing actually held
        # for its ticker (fixes ISIN-country vs listing-exchange mismatches on
        # dual-listed names). Runs after the full file is parsed so it sees
        # every position regardless of section order.
        _isin_mismatch: set = set()
        _ambiguous: set = set()
        _reattribute_income_to_holdings(
            transactions, extra_held=open_position_syms,
            fallback_held=(ctx or {}).get('held'),
            isins_by_root={**(ctx or {}).get('stock_isins', {}),
                           **pre['stock_isins']},
            mismatches=_isin_mismatch,
            open_qty=(open_position_qty if 'Open Positions' in header_maps
                      else None),
            ambiguous=_ambiguous)
        for _sym, _day in sorted(_ambiguous):
            print(f"note: {shown_name(path)}: income on {_sym} of {_day} "
                  f"kept its ISIN listing: the account held more than one "
                  f"listing of the ticker that day (add a ticker.map rule "
                  f"if it belongs to the other).", file=sys.stderr)
        for t in transactions:
            t.pop('_isin', None)
        for _sym, _isin, _held in sorted(_isin_mismatch):
            print(f"warning: {shown_name(path)}: income on {_sym} (ISIN {_isin}) "
                  f"was NOT moved to the held listing {_held}: that "
                  f"listing is a different security (another ISIN) with "
                  f"the same ticker.", file=sys.stderr)
        if isin_fallback:
            _held = set(open_position_syms) | {
                t.get('symbol') for t in transactions
                if t.get('action') in _POSITION_ACTIONS}
            _still = sorted(
                (sym, cc) for sym, cc in isin_fallback
                if sym not in _held and any(
                    t.get('symbol') == sym for t in transactions
                    if t.get('action') in _INCOME_ACTIONS
                    or t.get('action') == 'ADJUST'))
            if _still:
                print(f"warning: {shown_name(path)}: income booked on "
                      f"{', '.join(f'{s} (ISIN {c})' for s, c in _still)}"
                      f" — the ISIN country has no exchange-suffix "
                      f"mapping, so .US was assumed and no position in "
                      f"this statement confirms that listing. Check the "
                      f"symbol (a ticker.map rule fixes it).",
                      file=sys.stderr)

        # An assignment's option leg settles with its STOCK leg: the
        # premium rolls into the delivered shares, so the pair must share
        # a settle date or an unrelated same-underlying trade sorting
        # between them consumes the staged premium. Before the T+1
        # cutover the option class settled T+1 and the stock T+2 (audit
        # S058-01). Cash-settled contracts have no stock leg: unchanged.
        _stock_by_key = {}
        for _st in assign_stock_legs:
            _root, _ext = _split_known_ext(_st['symbol'])
            _stock_by_key.setdefault((_root, _ext, _st['date']), _st)
        def _root_forms(r):
            # The option root as the stock leg may spell it: itself, the
            # adjusted contract's root without OCC's digit (QZX1 ->
            # QZX), a class share without its dot (SAMPLCB vs SAMPLC.B) —
            # the exact-root match left those legs on two settle dates
            # (audit A2-1028).
            base = re.sub(r'\d+$', '', r) or r
            return [r, base]

        _stock_flat = {}
        for (_r, _e, _d), _st in _stock_by_key.items():
            _stock_flat.setdefault((_r.replace('.', ''), _e, _d), _st)
        for _ol in assign_option_legs:
            _om = re.match(r'^(.+?)\d{6}[CP]\d{8}\.(\w+)$', _ol['symbol'])
            _st = None
            if _om:
                for _r in _root_forms(_om.group(1)):
                    _st = (_stock_by_key.get((_r, _om.group(2), _ol['date']))
                           or _stock_flat.get((_r.replace('.', ''),
                                               _om.group(2), _ol['date'])))
                    if _st is not None:
                        break
            if _st is not None:
                _ol['date_settle'] = _st['date_settle']

        # A warrant exercise (warrant leg `C;Ex` at 0, shares `Ex;O` at
        # the exercise price) is not a disposition: the warrant leg names
        # the shares it delivers (`exercise_of`) and the engines roll its
        # cost into them like a long call's premium (CA s.49(3); US basis
        # carryover — tax-logic CA-OPT-09 / US-OPT-06, A2-0090). A leg
        # whose shares cannot be told apart stays a disposal at 0, said.
        self._pair_warrant_exercises(warrant_ex_legs, share_ex_legs)

        self.clamp_settlement_to_expiry(transactions, expiry_txs)

        # Parser-level disambiguation so a downstream `taxjson-sort --dedup`
        # can't collapse byte-identical split-fill rows. IB occasionally
        # emits sub-second split fills that collide at our second-precision
        # `time` field.
        self.disambiguate_split_fills(transactions)
        # One note for recognized non-events (Forex conversions,
        # subtotals, metadata sections) and one for rows the parser
        # could not classify — the other parsers already do this; IB
        # counted but never reported. Deferred with the Corporate Actions
        # lines: a Ca row another statement resolves is not a skip.
        if self.defer_ca_messages:
            self.deferred_skip_summary = shown_name(path)
        else:
            self.emit_skip_summary(shown_name(path))
        return transactions
