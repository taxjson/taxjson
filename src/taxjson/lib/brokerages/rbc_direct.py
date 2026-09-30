"""RBC Direct Investing activity-export parser.

Every row is classified by its ACTIVITY label and the RBC event code that
prefixes the Description ("DIV - ", "MGR - ", "XCH - ", ...), never by
words inside the security name: a name like "DIVIDEND 15 SPLIT CORP" or
"HIGH DIVIDEND INDEX ETF" once turned in-kind transfers and a retraction
into dividends. The export is read strictly — the header row must carry
the real columns, every number must parse, and one date format is chosen
per column for the whole file — because a silently mis-read column is how
a filed return once came out tens of thousands of dollars wrong.

Reorganizations (name changes, reverse/forward splits booked as a
removal + receipt pair, 1-for-1 exchanges, option adjustments) are paired
here via `taxjson.lib.corp_actions.pair_rbc_reorganizations` and emitted
as ONE event each; real mergers ("... MERGER TO ...") and spin-offs are
left to taxjson-corp-actions, which asks for the tax election.
"""

import csv
import io
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib.brokerages.base import (BaseBrokerage, BrokerageParseError,
                                         _parse_div_qty_rate,
                                         is_roc_description)
from taxjson.lib.corp_actions import (
    RBC_REORG_CODES, RbcReorgPairing, pair_rbc_reorganizations,
    rbc_norm_company,
    rbc_is_temp_symbol, rbc_is_option_code, rbc_rights_key,
    rbc_name_similarity, _rbc_removal_names, _rbc_receipt_name,
)


class RbcFormatError(BrokerageParseError):
    """An RBC export the parser refuses to guess about (missing header
    columns, an unparseable number, ambiguous dates, an income row that
    carries shares). Raised instead of silently mis-reading the file."""


# Date formats RBC exports have used, in preference order. The Date and
# Settlement Date columns each get ONE format for the whole file (see
# `_pick_date_formats`): the 2022-vintage export wrote Date as 1/6/2022
# and Settlement Date as 10-Jan-22; later ones "January 30, 2025"; files
# round-tripped through Excel/pandas carry "2026-05-28 00:00:00".
_DATE_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%B %d, %Y",
              "%b %d, %Y", "%m/%d/%Y", "%d/%m/%Y", "%d-%b-%y", "%d-%b-%Y")

# Header columns. Matching is case-insensitive on the trimmed cell.
_COLUMNS = ('Date', 'Activity', 'Symbol', 'Symbol Description', 'Quantity',
            'Price', 'Settlement Date', 'Account', 'Value', 'Amount',
            'Currency', 'Description')
_CANON = {c.lower(): c for c in _COLUMNS}
# Every RBC export vintage seen (2021-2026, 13 distinct files) carries
# all of these; a file without them is not an export this parser knows.
REQUIRED_COLUMNS = ('Date', 'Activity', 'Symbol', 'Quantity', 'Price',
                    'Settlement Date', 'Currency', 'Description')

# The RBC event code that prefixes a Description: "DIV - ...", "MGR -".
_RBC_CODE_RE = re.compile(r'^\s*([A-Z]{2,4})\s*-(?=\s|$)')

# Income is recognised by RBC's code/verb part of the description only —
# never by words in the security name ("DIVIDEND 15 SPLIT CORP").
#   "DIV - <name> CASH DIV  ON  500 SHS ..."   "<name> DIST  ON  400 SHS"
_RBC_INCOME_VERB_RE = re.compile(
    r'^\s*DIV\s*-|\bCASH\s+DIV(?:IDEND)?\s+ON\b|\bDIST\s+ON\b', re.I)
_RBC_BOOK_COST_RE = re.compile(
    r'\bADJUSTMENT\s+TO\s+BOOK\s+COST\s+\$?\s*([\d,]*\.?\d+)', re.I)
_RBC_BOOK_VALUE_RE = re.compile(r'\bBOOK\s+VALUE\s+\$?\s*(-?[\d,]*\.?\d+)',
                                re.I)
_RBC_REINV_PRICE_RE = re.compile(r'\bREINV\s*@\s*[A-Z]{0,2}\$?\s*([\d.]+)',
                                 re.I)

# A retraction/redemption/tender of shares by the issuer (split-share
# corps' monthly and annual retractions): Activity 'Other', code TEN,
# negative Quantity, Value = the proceeds. A disposition like a sale.
_RBC_RETRACTION_RE = re.compile(
    r'^\s*TEN\s*-\s*.*\b(?:RETRACTION|REDEMPTION|REDEEMED)\b', re.I)
# A forward/reverse stock split booked as ONE 'Reorganization' row with the
# net shares moved in Quantity and "... STK SPLIT ON <base> SHS ..." e.g.
#   "DIS - VANGUARD ... GROWTH ETF STK SPLIT ON 14 SHS REC 04/17/26 ..."
_RBC_STK_SPLIT_RE = re.compile(r'\b(?:STK|STOCK|FORWARD|REVERSE)\s+SPLIT\b',
                               re.I)
_RBC_SPLIT_ON_SHS_RE = re.compile(r'\bON\s+([\d,]+(?:\.\d+)?)\s+SHS\b', re.I)
# Legacy description tokens for rows whose Activity label is unknown.
_TRADE_DESC_RE = re.compile(r'\b(?:Buy|Sell)\b')

# The Global X (formerly Horizons) US Dollar Currency ETF trades only on
# the TSX, in a CAD class (DLR.TO) and a USD class (DLR.U.TO). RBC books
# both under the bare symbol "DLR"; the USD row would otherwise become
# DLR.US — the NYSE ticker of Digital Realty Trust, a different security.
# Both issuer spellings seen in real exports: "HORIZONS U S DLR CURRENCY
# ETF" (to ~2023) and "GLOBAL X US DLR CURRENCY ETF".
_RBC_USD_DLR_RE = re.compile(r'\bU\s?\.?\s?S\.?\s+DLR\s+CURRENCY\s+ETF\b',
                             re.I)

# Option description as RBC writes it, with the codes that may prefix it
# (EXP expiry, ASN assignment, XCH adjustment/exchange). Overrides the
# base patterns, which don't know "XCH -".
# The strike may carry a thousands separator ("CALL .BKNG 06/20/25 5,000",
# "PUT .NDX 06/20/25 21,500"): the old ([\d\.]+) stopped at the comma and
# pooled a 5,000 and a 5,025 call as one 5-strike contract (audit S063-15).
_RBC_STRIKE = r'(\d{1,3}(?:,\d{3})+(?:\.\d+)?|[\d\.]+)'
_RBC_OPTION_PATTERNS = (
    re.compile(
        r'^(?:(?:EXP|ASN|XCH)\s*-\s*)?(CALL|PUT)\s+\.?([A-Z0-9\s\.]+?)\s+'
        r'(\d{1,2}/\d{1,2}/\d{2})\s+' + _RBC_STRIKE, re.I),
    re.compile(
        r'ASSIGNMENT OF OPTION.*?(CALL|PUT)\s+\.?([A-Z0-9\s\.]+?)\s+'
        r'(\d{1,2}/\d{1,2}/\d{2})\s+' + _RBC_STRIKE, re.I),
)
# A stock dividend paid in shares ("DIS - <name> STK DIV ON 1390 SHS").
_RBC_STK_DIV_RE = re.compile(r'\bSTK\.?\s+DIV\b|\bSTOCK\s+DIVIDEND\b', re.I)
# An older (RBC Dominion Securities) vintage books a mutual fund's
# reinvested distribution as ONE Dividends row carrying the units:
# "DIV - <fund> As Of 04/07/22 Reinvest @ $21.217", Quantity 6.73, Value
# blank — the whole distribution, reinvested (audit R1-87).
_RBC_REINVEST_AT_RE = re.compile(
    r'\bREINVEST\s*@\s*\$?\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|[\d.]+)', re.I)
# The direction of an in-kind transfer comes from RBC's code or its verb,
# never from a word in the security name ("DELIVERY HERO", audit S016-05).
_RBC_TRANSFER_OUT_RE = re.compile(
    r'^\s*(?:TF[OW]\b|(?:[A-Z]{2,4}\s*-\s*)?(?:TRANSFER\s+OUT|DELIVER)\b)',
    re.I)
_RBC_CIL_FRACTION_RE = re.compile(
    r'CASH\s+IN\s+LIEU\s+(?:OF\s+)?(?:A\s+)?FRAC', re.I)

# Row classes that are recognised NON-events (cash moves, statement
# furniture): counted, never booked, never reported as unclassified.
_NONEVENT_CLASSES = {
    'footer': 'footer/disclaimer line',
    'cash': 'cash deposit/withdrawal/transfer',
    'mtm': 'mark-to-market cash journal',
}

_BASE_TIME = datetime(2000, 1, 1, 9, 30, 0)


def parse_rbc_date(date_str: str):
    """Module-level helper kept for back-compat with callers that import it.

    Raises ValueError on unparseable input rather than silently stamping
    `datetime.now()`. (The parser itself chooses ONE format per column
    for the whole file — see read_rbc_rows.)"""
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime((date_str or '').strip(), fmt)
        except ValueError:
            continue
    raise ValueError(
        f"parse_rbc_date: could not parse {date_str!r} with formats {_DATE_FMTS}"
    )


# ----------------------------------------------------------------- reading

@dataclass
class RbcRow:
    """One data row of an RBC export, strictly parsed."""
    line: int                 # 1-based line in the file
    order: int                # position among data rows (file order)
    activity: str
    symbol: str
    symdesc: str
    desc: str
    currency: str
    code: str                 # RBC event code ("DIV", "MGR", ...) or ''
    qty: float
    price: float
    value: float              # Amount when present, else Value (signed cash)
    date_raw: str
    date: str = ''            # ISO
    settle: str = ''          # ISO, '' when blank
    settle_raw: str = ''
    cls: str = ''             # classify_rbc_row()
    k: int = 0                # intra-day ordinal: 0 = the day's earliest row
    account: str = ''         # the Account column ('' when absent)
    value_blank: bool = False  # the Amount/Value cell was EMPTY (not "0")

    def label(self) -> str:
        return (f"line {self.line}: {self.date_raw} {self.activity or '?'} "
                f"{self.symbol or '-'} qty={self.qty:g} value={self.value:g} "
                f"{self.currency} {self.desc[:70]!r}")


@dataclass
class RbcExport:
    path: Path
    rows: List[RbcRow]
    n_footers: int
    columns: List[str]
    date_fmt: str
    settle_fmt: str
    newest_first: Optional[bool]
    notes: List[str] = field(default_factory=list)


def _err(path: Path, line: int, msg: str) -> RbcFormatError:
    return RbcFormatError(f"{path.name}:{line}: {msg}")


_NUM_RE = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)')
_THOUSANDS_RE = re.compile(r'[+-]?\d{1,3}(?:,\d{3})+(?:\.\d*)?')


def rbc_number(raw: Optional[str], *, path: Path, line: int,
               column: str) -> float:
    """Strict number parse for one cell: blank → 0.0; "1,234.50" (valid
    thousands grouping) and "(12.50)" (accounting negative) accepted;
    anything else — "39 043", "1,5", "12abc" — raises. The shared
    BaseBrokerage.clean_number returns 0.0 on garbage, which is how a
    shifted or localised column silently becomes free shares."""
    s = (raw or '').strip()
    if not s:
        return 0.0
    neg = False
    if s.startswith('(') and s.endswith(')'):
        neg, s = True, s[1:-1].strip()
    if s.startswith('$'):
        s = s[1:].strip()
    elif s[:2] in ('-$', '+$'):
        s = s[0] + s[2:].strip()
    if _THOUSANDS_RE.fullmatch(s):
        s = s.replace(',', '')
    if not _NUM_RE.fullmatch(s):
        raise _err(path, line, f"{column} {raw!r} is not a number this "
                   f"parser can read safely (expected e.g. 1234.5, "
                   f"1,234.50 or (12.50)) — refusing to guess")
    v = float(s)
    if neg:
        if v < 0:
            raise _err(path, line, f"{column} {raw!r}: a parenthesised "
                       f"negative that is also signed — refusing to guess")
        v = -v
    return v


def _canon(cell: str) -> Optional[str]:
    return _CANON.get((cell or '').replace('﻿', '').strip().lower())


def _find_header(records, path: Path):
    """The header row: the first record whose cells include the Date and
    Activity COLUMNS (a preamble line like "Activity Export as of Date
    Jan 5" is one cell and never qualifies). Raises when no such row
    exists or when it lacks a required column."""
    for pos, (line, cells) in enumerate(records):
        canon = [_canon(c) for c in cells]
        present = {c for c in canon if c}
        if 'Date' not in present or 'Activity' not in present:
            continue
        missing = [c for c in REQUIRED_COLUMNS if c not in present]
        if not ({'Value', 'Amount'} & present):
            missing.append('Value (or Amount)')
        if missing:
            raise _err(path, line, f"RBC header row lacks column(s) "
                       f"{', '.join(missing)} (found: "
                       f"{', '.join(c.strip() for c in cells)}) — not an "
                       f"export layout this parser knows; refusing to "
                       f"guess which column is which")
        return pos, canon
    raise RbcFormatError(
        f"{path.name}: no RBC header row found (a row with the columns "
        f"{', '.join(REQUIRED_COLUMNS)} and Value/Amount). Is this an "
        f"RBC Direct Investing activity export?")


def _parse_all(values: List[str], fmt: str) -> Optional[Tuple[datetime, ...]]:
    out = []
    for v in values:
        try:
            out.append(datetime.strptime(v, fmt))
        except ValueError:
            return None
    return tuple(out)


def _column_formats(values: List[str]) -> Dict[Tuple, str]:
    """{parsed-dates tuple: first format producing it} over the formats
    that parse EVERY value. Formats that read the column identically
    (e.g. %B / %b for "May 5, 2023") collapse into one entry."""
    groups: Dict[Tuple, str] = {}
    for fmt in _DATE_FMTS:
        res = _parse_all(values, fmt)
        if res is not None:
            groups.setdefault(res, fmt)
    return groups


def _no_format_error(path: Path, column: str, values, lines):
    for v, ln in zip(values, lines):
        if all(_parse_all([v], f) is None for f in _DATE_FMTS):
            return _err(path, ln, f"{column} {v!r} is not a date format RBC "
                        f"exports")
    # Every value parses under SOME format, just not one format for all.
    seen: Dict[str, Tuple[str, int]] = {}
    for v, ln in zip(values, lines):
        f = next(f for f in _DATE_FMTS if _parse_all([v], f) is not None)
        seen.setdefault(f, (v, ln))
    ex = '; '.join(f"{v!r} (line {ln})" for v, ln in list(seen.values())[:3])
    return RbcFormatError(f"{path.name}: mixed date formats in the {column} "
                          f"column — {ex}. Refusing to guess; re-export.")


def _pick_date_formats(path: Path, rows: List[RbcRow]) -> Tuple[str, str]:
    """ONE format per column for the whole file. When two formats read a
    column differently (03/04/2024: March 4 or April 3?), the one whose
    settlement dates follow their trade dates by 0-10 days on (nearly)
    every row wins; otherwise the file is ambiguous and we raise."""
    dvals = [r.date_raw for r in rows]
    dlines = [r.line for r in rows]
    s_rows = [r for r in rows if r.settle_raw]
    svals = [r.settle_raw for r in s_rows]
    dg = _column_formats(dvals) if dvals else {(): _DATE_FMTS[0]}
    if not dg:
        raise _no_format_error(path, 'Date', dvals, dlines)
    sg = _column_formats(svals) if svals else {(): _DATE_FMTS[0]}
    if not sg:
        raise _no_format_error(path, 'Settlement Date', svals,
                               [r.line for r in s_rows])
    if len(dg) == 1 and len(sg) == 1:
        return next(iter(dg.values())), next(iter(sg.values()))
    idx = {id(r): i for i, r in enumerate(rows)}
    scored = []
    for dres, dfmt in dg.items():
        for sres, sfmt in sg.items():
            ok = n = 0
            for j, r in enumerate(s_rows):
                lag = (sres[j] - dres[idx[id(r)]]).days
                n += 1
                ok += 0 <= lag <= 10
            scored.append((ok / n if n else 0.0, dfmt, sfmt))
    good = [s for s in scored if s[0] >= 0.9]
    if len(good) == 1:
        return good[0][1], good[0][2]
    amb = [f for f in list(dg.values()) + list(sg.values())]
    raise RbcFormatError(
        f"{path.name}: ambiguous dates — the file reads validly as more than "
        f"one of {sorted(set(amb))} (day/month vs month/day) and the "
        f"settlement dates don't settle it. Refusing to guess; re-export "
        f"with month names (RBC's default) or ISO dates.")


def read_rbc_rows(path: Path) -> RbcExport:
    """Read and strictly validate an RBC activity export. Shared by the
    brokerage parser and taxjson-corp-actions so both see the same rows."""
    path = Path(path)
    raw = path.read_bytes()
    try:
        if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
            text = raw.decode('utf-16')           # Excel "Unicode text" save
        else:
            text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as e:
        raise RbcFormatError(f"{path.name}: not UTF-8/UTF-16 text ({e}) — "
                             f"re-export the CSV from RBC") from None
    reader = csv.reader(io.StringIO(text))
    records = []
    for cells in reader:
        records.append((reader.line_num, cells))
    hpos, canon = _find_header(records, path)
    ncol = len(canon)
    rows: List[RbcRow] = []
    n_footers = 0
    n_rejoined = 0
    for line, cells in records[hpos + 1:]:
        if not any((c or '').strip() for c in cells):
            continue                                   # blank line
        # Statement furniture: a one-cell line ("Disclaimers", the
        # exchange-rate footnotes) wherever the columns are.
        if len(cells) < ncol and sum(1 for c in cells if (c or '').strip()) <= 1:
            only = next((c.strip() for c in cells if (c or '').strip()), '')
            if any(_parse_all([only], f) for f in _DATE_FMTS):
                raise _err(path, line, f"row holds only the date {only!r} — "
                           f"truncated row; refusing to guess the rest")
            n_footers += 1
            continue
        extra = [c for c in cells[ncol:] if (c or '').strip()]
        if extra:
            if canon[-1] != 'Description':
                raise _err(path, line, f"row has {len(cells)} cells but the "
                           f"header has {ncol} (extra: {extra[:3]}) — an "
                           f"unquoted comma shifts every column after it; "
                           f"refusing to guess")
            # Description is the LAST column: an unquoted comma inside it
            # ("BAL   31,498-") only spills the description itself into
            # extra cells; nothing before it moved. Re-join it. (A comma
            # in an EARLIER column would shift Quantity/Value/Currency,
            # which the strict number and currency checks below refuse.)
            cells = cells[:ncol - 1] + [','.join(cells[ncol - 1:])]
            n_rejoined += 1
        cell = {}
        for i, name in enumerate(canon):
            if name and name not in cell:
                cell[name] = (cells[i] if i < len(cells) else '').strip()
        # Statement furniture: text in the first column only ("Disclaimers",
        # the exchange-rate footnotes) — no activity, no money, no shares.
        if not cell.get('Activity') and not any(
                cell.get(c) for c in ('Symbol', 'Quantity', 'Price', 'Value',
                                      'Amount', 'Currency', 'Description')):
            n_footers += 1
            continue
        if len(cells) < ncol:
            raise _err(path, line, f"row has {len(cells)} cells but the "
                       f"header has {ncol} — truncated row; refusing to "
                       f"guess which columns are missing")
        spill = next((c for c in cells if '\n' in (c or '')
                      or '\r' in (c or '')), None)
        if spill is not None:
            # RBC writes quotes inside a Description unescaped; one that
            # ends right after its opening quote keeps the CSV field open
            # across the line break and swallows the NEXT export row (a
            # whole trade) into this Description (audit R1-84).
            raise _err(path, line, f"a cell spans a line break "
                       f"({spill[:80]!r}) — an unescaped quote in the "
                       f"Description swallowed the next row. Delete the "
                       f"stray double quote in the CSV and re-run; "
                       f"refusing to drop the swallowed row")
        num = lambda col: rbc_number(cell.get(col), path=path, line=line,
                                     column=col)
        amount = cell.get('Amount', '')
        value = num('Amount') if amount else num('Value')
        value_blank = not (amount or cell.get('Value', ''))
        desc = cell.get('Description', '')
        m = _RBC_CODE_RE.match(desc)
        r = RbcRow(
            line=line, order=len(rows),
            activity=cell.get('Activity', ''),
            symbol=cell.get('Symbol', ''),
            symdesc=cell.get('Symbol Description', ''),
            desc=desc,
            currency=cell.get('Currency', '').upper(),
            code=m.group(1).upper() if m else '',
            qty=num('Quantity'), price=num('Price'), value=value,
            date_raw=cell.get('Date', ''),
            account=cell.get('Account', ''),
            value_blank=value_blank,
        )
        r.settle_raw = cell.get('Settlement Date', '')
        if not r.date_raw:
            raise _err(path, line, "row has no Date")
        if r.currency and not re.fullmatch(r'[A-Z]{3}', r.currency):
            raise _err(path, line, f"Currency {r.currency!r} is not a "
                       f"3-letter code — the row's columns are shifted or "
                       f"mislabelled; refusing to guess")
        if not r.currency and (r.qty or r.value):
            raise _err(path, line, "row moves shares or cash but has no "
                       "Currency — refusing to assume CAD")
        rows.append(r)

    date_fmt, settle_fmt = _pick_date_formats(path, rows)
    notes = []
    if n_rejoined:
        notes.append(f"{n_rejoined} row(s) had an unquoted comma inside the "
                     f"Description (the last column) — re-joined")
    if '%d/%m' in date_fmt or '%d/%m' in settle_fmt:
        notes.append("dates read as DAY/MONTH/YEAR (the file proves it: "
                     "some day > 12) — RBC's own exports use month/day")
    for r in rows:
        r.date = datetime.strptime(r.date_raw, date_fmt).strftime('%Y-%m-%d')
        r.settle = (datetime.strptime(r.settle_raw, settle_fmt)
                    .strftime('%Y-%m-%d') if r.settle_raw else '')
        r.cls = classify_rbc_row(r)

    # RBC has no time column. The export lists rows newest-first, so the
    # file order within a day is the reverse of the real order; number the
    # day's rows from its earliest (the bottom) so the emitted times keep
    # a same-day sell-then-rebuy in its real sequence.
    dates = [r.date for r in rows]
    desc_ok = all(a >= b for a, b in zip(dates, dates[1:]))
    asc_ok = all(a <= b for a, b in zip(dates, dates[1:]))
    newest_first: Optional[bool]
    if desc_ok:
        newest_first = True
    elif asc_ok:
        newest_first = False
    else:
        newest_first = None
        notes.append("rows are not in date order, so same-day rows keep "
                     "one common time (intra-day order unknown)")
    if newest_first is not None:
        by_day: Dict[str, List[RbcRow]] = {}
        for r in rows:
            by_day.setdefault(r.date, []).append(r)
        for day_rows in by_day.values():
            n = len(day_rows)
            for i, r in enumerate(day_rows):
                r.k = (n - 1 - i) if newest_first else i
            # An option assignment is ONE event booked as two rows — the
            # ASN option leg and the stock leg at the strike ("...
            # ASSIGNMENT OF OPTION AS OF ..."). They share one time, so the
            # engine's same-timestamp ladder (option leg first) folds the
            # premium into the stock leg (s.49(3)); the stock leg sorting
            # first (it is listed below the ASN row) lost the fold.
            grp = [r for r in day_rows
                   if r.cls == 'assignment' or (
                       r.cls == 'trade'
                       and 'ASSIGNMENT OF OPTION' in r.desc.upper())]
            if grp:
                k0 = min(r.k for r in grp)
                for r in grp:
                    r.k = k0
    return RbcExport(path=path, rows=rows, n_footers=n_footers,
                     columns=[c for c in canon if c], date_fmt=date_fmt,
                     settle_fmt=settle_fmt, newest_first=newest_first,
                     notes=notes)


def rbc_time(k: int) -> str:
    """09:30:00 plus `k` seconds — the intra-day ordinal as a time."""
    return (_BASE_TIME + timedelta(seconds=max(0, k))).strftime('%H:%M:%S')


# ---------------------------------------------------------- classification

def classify_rbc_row(r) -> str:
    """The one classification of an RBC row, by Activity label and RBC
    code (never by words in the security name). Returns one of:
    trade, expiry, assignment, retraction, split, reorg, cil, spinoff,
    rights, reinvest, book-adjust, roc, dividend, tax, interest, fee,
    transfer, cash, mtm, unknown."""
    a = (r.activity or '').strip().lower()
    code = r.code
    d = (r.desc or '').upper()
    has_sym = bool((r.symbol or '').strip())
    q = r.qty
    if a in ('buy', 'sell', 'exercise', 'assignment'):
        return 'trade'
    # Cash in lieu of a FRACTIONAL share, whatever the activity label —
    # but only then: "CASH IN LIEU OF DIVIDEND" elsewhere is income.
    if 'CASH IN LIEU' in d and (a == 'reorganization'
                                or _RBC_CIL_FRACTION_RE.search(d)):
        # (A name starting FRAC — FRACTYL HEALTH — used to make its
        # CASH IN LIEU OF DIVIDEND a fractional-share CIL; audit S064-05.)
        return 'cil'
    if a == 'transfers':
        if has_sym and abs(q) > 1e-12:
            return 'transfer'
        if not has_sym and abs(q) < 1e-12:
            return 'cash'
        return 'unknown'
    if a in ('deposits & contributions', 'withdrawals & de-registrations'):
        return 'cash' if (not has_sym and abs(q) < 1e-12) else 'unknown'
    if a == 'reorganization':
        if code == 'EXP':
            return 'expiry'
        if code == 'CIL' or 'CASH IN LIEU' in d:
            return 'cil'
        if code == 'DIS':
            if _RBC_STK_SPLIT_RE.search(d) and _RBC_SPLIT_ON_SHS_RE.search(d):
                return 'split'
            if _RBC_STK_DIV_RE.search(d) and abs(q) > 1e-12:
                return 'stock-dividend'
            if re.search(r'\bSPIN\s?OFF\b', d):
                return 'spinoff'
            if re.search(r'\b(?:RTS|WTS)\b.*\bDIST\b', d):
                return 'rights'
            return 'unknown'
        if code in RBC_REORG_CODES:
            return 'reorg'
        if not code and _RBC_STK_SPLIT_RE.search(d) \
                and _RBC_SPLIT_ON_SHS_RE.search(d):
            return 'split'
        return 'unknown'
    if a == 'other':
        if code == 'ASN':
            return 'assignment'
        if code == 'EXP':
            return 'expiry'
        if code == 'TEN':
            return 'retraction' if q < 0 else 'unknown'
        if code == 'MKT' and not has_sym and abs(q) < 1e-12:
            return 'mtm'
        return 'unknown'
    if 'dividend' in a or 'distribution' in a:
        if code == 'REI':
            return 'reinvest'
        if abs(q) > 1e-12 and _RBC_STK_DIV_RE.search(d):
            return 'stock-dividend'
        if q > 1e-12 and _RBC_REINVEST_AT_RE.search(d):
            return 'reinvest-in-kind'
        if _RBC_BOOK_COST_RE.search(d):
            return 'book-adjust'
        if is_roc_description(d):
            return 'roc'
        return 'dividend'
    if a == 'return of capital':
        return 'book-adjust' if _RBC_BOOK_COST_RE.search(d) else 'roc'
    if a == 'taxes':
        return 'tax'
    if a == 'interest':
        return 'interest'
    if a == 'fees':
        return 'fee'
    # An Activity label this parser has never seen: fall back to the
    # description's CODE / verb part only.
    if code == 'EXP':
        return 'expiry'
    if code == 'ASN':
        return 'assignment'
    if code == 'TEN' and q < 0:
        return 'retraction'
    if code == 'REI':
        return 'reinvest'
    if code in ('DIV',) or _RBC_INCOME_VERB_RE.search(d):
        if _RBC_BOOK_COST_RE.search(d):
            return 'book-adjust'
        return 'roc' if is_roc_description(d) else 'dividend'
    if code == 'RTC' or (not code and is_roc_description(d)):
        return 'roc'
    if code == 'NRT':
        return 'tax'
    if code == 'INT' or re.match(r'^\s*INT\s+FR\b', d):
        return 'interest'
    if code == 'FCH':
        return 'fee'
    if not code and _TRADE_DESC_RE.search(r.desc or '') and abs(q) > 1e-12:
        return 'trade'
    return 'unknown'


# ------------------------------------------------------- account context
#
# `taxjson run` hands every RBC export of one account to ONE
# taxjson-brokerage call, but each file used to be parsed with identity
# maps learned from that file alone: a symbol's market currency, an
# option code's contract, a temporary code's company. Split the same rows
# across yearly downloads and the answer changed (a TSX stock's USD
# dividend became .US, an option's close became a new written option, a
# name change stranded the old pool). The context below is built ONCE
# from all of an account's files and shared by every per-file parse; a
# lone file gets a context of its own, so one file and many files follow
# the same rules.

# Rows that move (or prove) a position in a listed security.
_POSITION_CLASSES = ('trade', 'expiry', 'assignment', 'retraction',
                     'reinvest', 'rights', 'transfer', 'reorg',
                     'stock-dividend', 'reinvest-in-kind')
# How long before an income row a listing's activity still counts as
# "held" for it (record date → pay date, plus a sale after the record
# date).
_HELD_WINDOW_DAYS = 45


def _norm_account(acct: str) -> str:
    s = (acct or '').strip()
    digits = re.sub(r'\D', '', s)
    return digits or s.upper()


def _mask_account(acct: str) -> str:
    return (acct[:2] + '***') if acct else '(no Account column)'


def _squash(text: str) -> str:
    return ' '.join((text or '').split()).upper()


def _row_content_key(r) -> Tuple:
    """What makes two rows of two downloads the SAME broker row: every
    column except the position in the file (and the intra-day time
    derived from it)."""
    return (r.date, r.activity.strip().lower(), r.symbol.strip().upper(),
            _squash(r.symdesc), _squash(r.desc), r.currency, r.qty, r.price,
            r.value, r.settle)


def _signed_qty(r) -> float:
    q = r.qty
    a = (r.activity or '').strip().lower()
    if a == 'sell' or r.cls == 'retraction':
        return -abs(q)
    if r.cls == 'transfer' and q > 0 and _RBC_TRANSFER_OUT_RE.match(
            r.desc or ''):
        return -q
    return q


def _days(a: str, b: str) -> int:
    return (datetime.strptime(a, '%Y-%m-%d')
            - datetime.strptime(b, '%Y-%m-%d')).days


@dataclass
class _Listing:
    """One (bare symbol, currency) line as the account's rows show it."""
    symbol: str
    currency: str
    names: Dict[str, str] = field(default_factory=dict)   # norm → raw
    events: List[Tuple[str, int, int, float]] = field(default_factory=list)

    def finish(self) -> None:
        self.events.sort()

    @property
    def first(self) -> str:
        return self.events[0][0]

    @property
    def last(self) -> str:
        return self.events[-1][0]

    def position_on(self, date: str) -> float:
        return sum(q for d, _k, _f, q in self.events if d <= date)

    def held_on(self, date: str) -> bool:
        if abs(self.position_on(date)) > 1e-9:
            return True
        return any(0 <= _days(date, d) <= _HELD_WINDOW_DAYS
                   for d, _k, _f, _q in self.events)


@dataclass
class RbcAccountContext:
    """Identity maps and the overlap plan for ALL of an account's RBC
    exports (see the section comment above)."""
    files: List[str]                                   # resolved paths
    exports: Dict[str, 'RbcExport']
    # file → {row.order: the earlier file already holding that row}
    duplicate_of: Dict[str, Dict[int, str]]
    # file → {row.order: tag} for an identical row the earlier download
    # holds FEWER copies of (kept, tagged so its id stays distinct)
    extra_tag: Dict[str, Dict[int, str]]
    pairings: Dict[str, Any]
    occ_own: Dict[str, str]          # option code → first own row's OCC
    occ_by_code: Dict[str, str]      # ... plus codes that inherit via XCH
    names: Dict[str, List[Tuple[str, bool, str]]]      # key → (date, reorg?, sym)
    listings: Dict[str, Dict[str, _Listing]]           # symbol → cur → line
    messages: List[str] = field(default_factory=list)
    emitted: bool = False
    # file → ids of its reorganization legs that belong to an event
    # booked from ANOTHER file of the account (the removal's file)
    foreign_legs: Dict[str, set] = field(default_factory=dict)

    def rows(self, key: str) -> List['RbcRow']:
        dup = self.duplicate_of.get(key, {})
        return [r for r in self.exports[key].rows if r.order not in dup]

    def emit(self) -> None:
        if self.emitted:
            return
        self.emitted = True
        for m in self.messages:
            print(m, file=sys.stderr)

    def ticker_for_name(self, key: str, date: str, *,
                        before_only: bool = False) -> Optional[str]:
        """The listed ticker a security name traded under around `date`:
        the latest row strictly before it, else (for a trade row under a
        temporary code) a non-reorganization row that day, else the
        earliest after. `before_only` (a reorganization's REMOVAL leg,
        whose old ticker must predate the event) stops after step 1."""
        ent = self.names.get(key) if key else None
        if not ent:
            return None
        before = [e for e in ent if e[0] < date]
        if before:
            return max(before, key=lambda e: e[0])[2]
        if before_only:
            return None
        same = [e for e in ent if e[0] == date and not e[1]]
        if same:
            return same[0][2]
        after = [e for e in ent if e[0] > date]
        return min(after, key=lambda e: e[0])[2] if after else None


def build_rbc_account_context(paths, *, helper=None) -> RbcAccountContext:
    """Read every export of one account and build the shared maps."""
    helper = helper or RbcBrokerage()
    keys = [str(Path(p).resolve()) for p in paths]
    exports = {}
    for k, p in zip(keys, paths):
        if k not in exports:
            exports[k] = read_rbc_rows(Path(p))
    files = list(dict.fromkeys(keys))
    name_of = {k: exports[k].path.name for k in files}
    ctx = RbcAccountContext(files=files, exports=exports, duplicate_of={},
                            extra_tag={}, pairings={}, occ_own={},
                            occ_by_code={}, names={}, listings={})
    _plan_overlaps(ctx, name_of)

    live = [(fi, r) for fi, k in enumerate(files) for r in ctx.rows(k)]
    chrono = sorted(live, key=lambda x: (x[1].date, x[1].k, x[0],
                                         -x[1].order))

    # Option code → contract: its chronologically FIRST description in
    # ANY of the account's files (RBC re-describes a contract over time,
    # 8D*** "CALL .RCI" later "CALL .RCI.B"; keying each file on its own
    # text split one position across two symbols).
    where: Dict[str, str] = {}
    others: Dict[str, Dict[str, str]] = {}
    for fi, r in chrono:
        if not r.symbol or r.cls == 'reorg':
            continue
        occ = helper._row_occ(r)
        if not occ:
            continue
        if r.symbol not in ctx.occ_own:
            ctx.occ_own[r.symbol] = occ
            where[r.symbol] = name_of[files[fi]]
        elif occ != ctx.occ_own[r.symbol]:
            others.setdefault(r.symbol, {}).setdefault(occ, name_of[files[fi]])
    ctx.occ_by_code.update(ctx.occ_own)
    for code, oth in sorted(others.items()):
        span = ', '.join(f"{o} in {f}" for o, f in sorted(oth.items()))
        ctx.messages.append(
            f"warning: {where[code]}: RBC code {code} is described as more "
            f"than one contract ({ctx.occ_own[code]} first, in "
            f"{where[code]}; then {span}) — keeping the FIRST so the "
            f"position stays one symbol across every file of the account. "
            f"Check the contract terms.")

    # Reorganization pairing across ALL of the account's files: a
    # year-end event whose removal is in December's export and whose
    # receipt or cash-in-lieu posts in January's was two UNMATCHED legs
    # telling the user to add a statement already given (audit
    # S064-14). Each event is booked from its removal's file; its legs
    # in another file are consumed there. A MERGER spanning two files
    # stays unmatched here: taxjson-corp-actions owns mergers and pairs
    # per file, so the parser must not consume legs it cannot book.
    _file_of = {id(r): k for k in files for r in ctx.rows(k)}
    _acct = pair_rbc_reorganizations(
        [r for k in files for r in ctx.rows(k)])
    _events: Dict[str, list] = {k: [] for k in files}
    _unm: Dict[str, list] = {k: [] for k in files}
    _unm_cil: Dict[str, list] = {k: [] for k in files}
    for ev in _acct.events:
        legs = [x for x in [ev.removal, ev.receipt] + list(ev.cil)
                if x is not None]
        homes = {_file_of[id(x)] for x in legs}
        anchor = _file_of[id(ev.removal)]
        if len(homes) > 1 and ev.kind == 'merger':
            for x in legs:
                (_unm_cil if x in ev.cil else _unm)[_file_of[id(x)]].append(x)
            continue
        _events[anchor].append(ev)
        for x in legs:
            if _file_of[id(x)] != anchor:
                ctx.foreign_legs.setdefault(_file_of[id(x)], set()).add(id(x))
    for x in _acct.unmatched:
        _unm[_file_of[id(x)]].append(x)
    for x in _acct.unmatched_cil:
        _unm_cil[_file_of[id(x)]].append(x)
    adjusts = []
    for k in files:
        ctx.pairings[k] = RbcReorgPairing(events=_events[k],
                                          unmatched=_unm[k],
                                          unmatched_cil=_unm_cil[k])
        # An option adjustment's new code inherits the old contract
        # unless its own trade rows describe another — resolved in date
        # order across files so a later file's close finds it.
        adjusts += [ev for ev in ctx.pairings[k].events
                    if ev.kind == 'option_adjust']
    for ev in sorted(adjusts, key=lambda e: (e.removal.date, e.removal.k)):
        rem, rc = ev.removal, ev.receipt
        old = ctx.occ_by_code.get(rem.symbol) or helper._row_occ(rem)
        if old and rc.symbol not in ctx.occ_own:
            ctx.occ_by_code[rc.symbol] = old

    # Security name → ticker, with dates (temporary-code resolution).
    for fi, r in chrono:
        if not r.symbol or rbc_is_temp_symbol(r.symbol) \
                or rbc_is_option_code(r.symbol):
            continue
        for key in (rbc_norm_company(r.symdesc), rbc_rights_key(r.desc),
                    rbc_rights_key(r.symdesc)):
            if key:
                ent = ctx.names.setdefault(key, [])
                e = (r.date, r.cls == 'reorg', r.symbol)
                if e not in ent:
                    ent.append(e)

    # Listings: which (symbol, currency) lines the account actually
    # trades/holds, under which security names, with a position timeline.
    for fi, r in chrono:
        if r.cls not in _POSITION_CLASSES or not r.symbol or not r.currency:
            continue
        if rbc_is_temp_symbol(r.symbol) or rbc_is_option_code(r.symbol) \
                or helper._row_occ(r):
            continue
        li = ctx.listings.setdefault(r.symbol, {}).setdefault(
            r.currency, _Listing(r.symbol, r.currency))
        nm = rbc_norm_company(r.symdesc)
        if nm:
            li.names.setdefault(nm, ' '.join(r.symdesc.split()))
        li.events.append((r.date, r.k, fi, _signed_qty(r)))
    for per in ctx.listings.values():
        for li in per.values():
            li.finish()
    _detect_ticker_changes(ctx, helper)
    return ctx


def _plan_overlaps(ctx: RbcAccountContext, name_of: Dict[str, str]) -> None:
    """Overlapping downloads of the SAME RBC account (a re-download of a
    15-month window next to last year's file): a row already in an
    earlier file is skipped in a later one. The match ignores the row's
    position in the file (RBC re-orders a day's rows between downloads,
    and the intra-day time comes from that position), and counts copies:
    two genuinely identical fills on one day stay two. Only rows that
    carry the same non-blank Account are ever matched."""
    kept: Dict[Tuple, int] = {}          # (acct, content key) → copies kept
    kept_in: Dict[Tuple, str] = {}
    blank: Dict[str, Dict[Tuple, int]] = {}
    for k in ctx.files:
        exp = ctx.exports[k]
        groups: Dict[Tuple, List] = {}
        for r in exp.rows:
            groups.setdefault((_norm_account(r.account), _row_content_key(r)),
                              []).append(r)
        dup: Dict[int, str] = {}
        tag: Dict[int, str] = {}
        spans: Dict[str, List[str]] = {}
        for gkey, rs in groups.items():
            acct = gkey[0]
            if not acct:
                blank.setdefault(k, {})[gkey[1]] = len(rs)
                continue
            have = kept.get(gkey, 0)
            n_dup = min(have, len(rs))
            for r in rs[:n_dup]:
                dup[r.order] = kept_in[gkey]
                spans.setdefault(kept_in[gkey], []).append(r.date)
            if have and len(rs) > have:
                for r in rs[n_dup:]:
                    tag[r.order] = (f"[extra copy, not in overlapping "
                                    f"{name_of[kept_in[gkey]]}]")
            if len(rs) > have:
                kept[gkey] = len(rs)
                kept_in.setdefault(gkey, k)
        if dup:
            ctx.duplicate_of[k] = {o: name_of[f] for o, f in dup.items()}
        if tag:
            ctx.extra_tag[k] = tag
        for other, dates in sorted(spans.items()):
            accts = sorted({_mask_account(_norm_account(r.account))
                            for r in exp.rows
                            if r.order in dup and dup[r.order] == other})
            ctx.messages.append(
                f"note: {name_of[k]}: {len(dates)} row(s) dated "
                f"{min(dates)}..{max(dates)} are already in "
                f"{name_of[other]} (an overlapping download of the same RBC "
                f"account {', '.join(accts)}) — skipped, not booked twice")
    # Files without an Account column: two downloads of one account and
    # two accounts look the same, so nothing is matched — say so when
    # they share rows.
    bk = [k for k in ctx.files if k in blank]
    for i, a in enumerate(bk):
        for b in bk[i + 1:]:
            common = sum(min(n, blank[b].get(ck, 0))
                         for ck, n in blank[a].items())
            if common:
                ctx.messages.append(
                    f"warning: {name_of[a]} and {name_of[b]} share {common} "
                    f"identical row(s) on the same dates but have no Account "
                    f"column, so an overlapping re-download of ONE account "
                    f"cannot be told from two accounts — NOTHING was "
                    f"de-duplicated. If they are the same account, trim the "
                    f"overlap from one file (or re-export with the Account "
                    f"column).")


def _detect_ticker_changes(ctx: RbcAccountContext, helper) -> None:
    """A ticker change RBC applied WITHOUT a reorganization row
    (ORCC → OBDC in 2023): the old symbol stops with shares still open
    and a new symbol with the same Symbol Description and currency opens
    with a SALE those shares cover. The export carries no CUSIP, so this
    is not certain enough to merge silently: warn with the ticker.map
    line that merges them."""
    by_name: Dict[Tuple[str, str], List[_Listing]] = {}
    for per in ctx.listings.values():
        for li in per.values():
            for nm in li.names:
                by_name.setdefault((li.currency, nm), []).append(li)
    seen = set()
    for (cur, nm), lis in sorted(by_name.items()):
        for a in lis:
            for b in lis:
                if a is b or (a.symbol, b.symbol, cur) in seen:
                    continue
                if not a.events or not b.events or a.last > b.first:
                    continue
                open_a = a.position_on(a.last)
                first_b = next((q for d, _k, _f, q in b.events
                                if abs(q) > 1e-9), 0.0)
                if open_a <= 1e-9 or first_b >= -1e-9 \
                        or -first_b > open_a + 1e-6:
                    continue
                seen.add((a.symbol, b.symbol, cur))
                sa = helper.apply_currency_suffix(a.symbol, cur)
                sb = helper.apply_currency_suffix(b.symbol, cur)
                fb = ctx.exports[ctx.files[b.events[0][2]]].path.name
                ctx.messages.append(
                    f"warning: {fb}: RBC symbol {a.symbol} ({cur}) stops on "
                    f"{a.last} with {open_a:g} share(s) still open, and "
                    f"{b.symbol} — same Symbol Description "
                    f"{a.names[nm]!r} — first appears on {b.first} with a "
                    f"SALE of {-first_b:g}. That is a ticker change RBC "
                    f"booked without a reorganization row: as exported it is "
                    f"a stranded long {sa} and a short {sb}. If they are the "
                    f"same security, add this line to ticker.map:\n"
                    f"    GLOBAL {sa} {sb}")


# ------------------------------------------------------------------ parser

class RbcBrokerage(BaseBrokerage):
    DEFAULT_ACCOUNT = "RBC"

    def _option_description_patterns(self):
        return _RBC_OPTION_PATTERNS

    def parse_option_from_description(self, desc):
        opt = super().parse_option_from_description(desc)
        if opt:
            opt['strike'] = opt['strike'].replace(',', '')
        return opt

    # ----------------------------------------------------------- warnings
    def _warn(self, msg: str, *, lint: bool = False,
              unbooked: bool = False) -> None:
        """`unbooked`: a real row the parser did NOT book. The
        'warning: UNBOOKED:' prefix is what `taxjson run` echoes to the
        console and what `run --strict` refuses on — a lint finding
        alone reached only the .sum banner, and run never passes --lint
        (audit S016-00 / S064-17)."""
        tag = 'UNBOOKED: ' if unbooked else ''
        print(f"warning: {tag}{self._fname}: {msg}", file=sys.stderr)
        if lint or unbooked:
            self.lint_findings.append(msg)

    def _note(self, msg: str) -> None:
        print(f"note: {self._fname}: {msg}", file=sys.stderr)

    # Set by taxjson-brokerage (see `prepare_files`) to share identity
    # maps across all of an account's RBC exports; None = this file alone.
    account_context: Optional[RbcAccountContext] = None

    @classmethod
    def prepare_files(cls, paths) -> RbcAccountContext:
        """Read ALL of one account's RBC exports once and build the
        identity maps every per-file parse shares (market currency per
        symbol, option code → contract, name → ticker) plus the overlap
        plan for re-downloads. Account-level warnings print here, once."""
        ctx = build_rbc_account_context(list(paths), helper=cls())
        ctx.emit()
        return ctx

    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        path = Path(path)
        self._fname = path.name
        self.lint_findings: List[str] = []
        key = str(path.resolve())
        ctx = self.account_context
        if ctx is None or key not in ctx.exports:
            ctx = build_rbc_account_context([path], helper=self)
            ctx.emit()
        self._ctx = ctx
        exp = ctx.exports[key]
        dups = ctx.duplicate_of.get(key, {})
        self._extra_tag = ctx.extra_tag.get(key, {})
        rows = ctx.rows(key)
        for n in exp.notes:
            self._note(n)
        self._rows_seen = len(exp.rows) + exp.n_footers
        for _ in range(exp.n_footers):
            self.count_nonevent(_NONEVENT_CLASSES['footer'])
        for other in dups.values():
            self.count_nonevent(f"row already in the overlapping download "
                                f"{other}")

        # Identity maps shared by every file of the account.
        self._occ_by_code = ctx.occ_by_code
        self._occ_own = ctx.occ_own
        self._untraded_income: Dict[str, set] = {}
        self._listing_warned: set = set()
        self._rei_originals: Dict[tuple, List[Dict[str, Any]]] = {}
        self._rei_reversals: List[tuple] = []

        pairing = ctx.pairings[key]
        reorg_out: Dict[int, List[Dict[str, Any]]] = {}
        owned_by_corp_actions = 0
        leg_done = set()
        for ev in pairing.events:
            legs = [ev.removal, ev.receipt] + list(ev.cil)
            for leg in legs:
                if leg is not None:
                    leg_done.add(id(leg))
            if ev.kind == 'merger':
                owned_by_corp_actions += sum(1 for x in legs if x is not None)
                continue
            out = self._emit_reorg_event(ev)
            anchor = ev.removal.order
            reorg_out.setdefault(anchor, []).extend(out)
        leg_done |= ctx.foreign_legs.get(key, set())
        for leg in pairing.unmatched:
            self._warn(
                f"UNMATCHED reorganization leg — {leg.label()}. RBC books a "
                f"reorganization as a removal (negative quantity) plus a "
                f"receipt (positive); this one has no partner within 7 days, "
                f"so NOTHING was booked for it. Add the event by hand (a .tt "
                f"SPLIT/BUYSELL) or include the statement with the other leg.",
                unbooked=True)
        for c in pairing.unmatched_cil:
            self._warn(
                f"cash-in-lieu row with no reorganization to fold into — "
                f"{c.label()}. The cash was NOT booked; add the fractional "
                f"disposition by hand.", unbooked=True)

        transactions: List[Dict[str, Any]] = []
        expiries: List[Dict[str, Any]] = []
        spinoffs = 0
        for r in rows:
            cls = r.cls
            if id(r) in leg_done:
                self.note_row_consumed()
                transactions.extend(reorg_out.get(r.order, []))
                continue
            if cls in _NONEVENT_CLASSES:
                self.count_nonevent(_NONEVENT_CLASSES[cls])
                continue
            if cls in ('reorg', 'cil'):
                # Unmatched legs / CIL: warned above, nothing booked.
                self.count_skip(f"unmatched {cls} row (see warning)")
                continue
            if cls == 'spinoff':
                # A spin-off needs a TAX ELECTION (s.86.1 or an FMV
                # dividend in kind): owned by taxjson-corp-actions, which
                # prompts for it — booking the shares here at $0 cost was
                # silently choosing neither.
                self.note_row_consumed()
                spinoffs += 1
                continue
            if cls == 'unknown':
                self.count_skip(f"activity {r.activity.strip() or '?'}")
                if (r.activity.strip().lower() == 'transfers'
                        and r.symbol.strip()):
                    # A security transfer with a BLANK quantity read as 0
                    # and was dropped with no console line — and with it
                    # the superficial-loss guard on the transfer-in
                    # (audit S063-21).
                    self._warn(
                        f"Transfers row for {r.symbol} has no quantity — "
                        f"{r.label()}. It was NOT booked; fix the "
                        f"Quantity cell (a transfer always moves shares).",
                        unbooked=True)
                elif abs(r.qty) > 1e-12 or abs(r.value) > 0.005:
                    self._warn(
                        f"UNCLASSIFIED row that moves "
                        f"{'shares' if abs(r.qty) > 1e-12 else 'cash'} — "
                        f"{r.label()}. It was NOT booked. If it is a trade, "
                        f"income or corporate action, the RBC parser needs a "
                        f"new branch (see CONTRIBUTING).", unbooked=True)
                continue
            self.note_row_consumed()
            out = self._dispatch(r)
            tag = self._extra_tag.get(r.order)
            for tx in out:
                if not tx.get('description') and r.symdesc.strip():
                    # A blank Description falls back to the Symbol
                    # Description, so a name-keyed --security-overrides
                    # rule reaches the row (audit S065-01).
                    tx['description'] = r.symdesc.strip()
                if tag:
                    tx['description'] = f"{tx.get('description') or ''} {tag}"
                if tx.pop('_expiry', False):
                    expiries.append(tx)
            transactions.extend(out)
        self._pair_reinvest_reversals(transactions)

        if spinoffs:
            self._note(
                f"{spinoffs} RBC spin-off row(s) (DIS - ... SPINOFF) are "
                f"left to taxjson-corp-actions, which asks for the tax "
                f"election: ITA s.86.1 (eligible foreign spin-off, ACB "
                f"allocated from the parent) or a taxable dividend in kind "
                f"at FMV (the default if no election is filed). `taxjson "
                f"run` invokes it for canada; otherwise these shares are "
                f"NOT recorded anywhere — enter them via a .tt file.")
        if owned_by_corp_actions:
            self._note(
                f"skipped {owned_by_corp_actions} RBC merger row(s) — these "
                f"are resolved by taxjson-corp-actions (--brokerage "
                f"rbc_direct). `taxjson run` invokes it when your country "
                f"has election rules (canada today); otherwise these share "
                f"movements are NOT recorded anywhere — enter them manually "
                f"via a .tt file.")

        self._report_untraded_income()
        self._check_emitted_symbols(transactions)
        self.clamp_settlement_to_expiry(transactions, expiries)
        # Parser-level disambiguation so a downstream `taxjson-sort
        # --dedup` can't collapse byte-identical split-fill rows.
        self.disambiguate_split_fills(transactions)
        self.emit_skip_summary(path.name)
        return transactions

    # ------------------------------------------------------ pre-pass maps
    def _row_occ(self, r) -> Optional[str]:
        """OCC symbol from the row's Description, else its Symbol
        Description (two real 2024 buys carried the contract only there).
        None when the Symbol column is the contract's own UNDERLYING
        ticker: that row is the stock leg of an assignment/exercise whose
        text quotes the contract, and it used to become 100x as many
        option contracts with no stock row at all (audit R1-86)."""
        for text in (r.desc, r.symdesc):
            opt = self.parse_option_from_description(text or '')
            if opt:
                sym = (r.symbol or '').strip().upper()
                if sym and sym == opt['base'].upper() \
                        and not rbc_is_option_code(sym):
                    return None
                return self.format_occ_symbol(opt['right'], opt['base'],
                                              opt['expiry'], opt['strike'])
        return None

    def _resolve_temp(self, r) -> str:
        if not rbc_is_temp_symbol(r.symbol):
            return r.symbol
        for key in (rbc_norm_company(r.symdesc), rbc_rights_key(r.desc),
                    rbc_rights_key(r.symdesc)):
            hit = self._ctx.ticker_for_name(key, r.date) if key else None
            if hit:
                self._note(f"line {r.line}: RBC temporary code {r.symbol} "
                           f"resolved to {hit} by its security name")
                return hit
        return r.symbol

    def _equity_symbol(self, symbol: str, currency: str, r=None, *,
                       market: bool = False) -> str:
        """Suffix a bare equity symbol by the row's currency — or, for
        income rows (`market=True`), by the LISTING the account holds
        (see `_income_currency`) — with the built-in USD DLR ETF rule
        (the USD class of the TSX-listed US-dollar ETF is DLR.U.TO)."""
        if r is not None and currency == 'USD' and symbol.upper() in (
                'DLR', 'DLR.U') and (_RBC_USD_DLR_RE.search(r.symdesc or '')
                                     or _RBC_USD_DLR_RE.search(r.desc or '')):
            return 'DLR.U.TO'
        if market and r is not None:
            currency = self._income_currency(r) or currency
        return self.apply_currency_suffix(symbol, currency)

    def _income_currency(self, r) -> str:
        """The listing (by currency) an income/withholding/ROC/fee row
        belongs to, from the account's own trade rows in ALL its files:
        a TSX stock paying USD keeps .TO; when a bare symbol names two
        securities (HCA Healthcare in USD and a Hamilton ETF in CAD; the
        NVIDIA stock and its CAD CDR), the row goes to the line with the
        same Symbol Description, else the one held at the time, else the
        one in the row's currency. A symbol the files never trade keeps
        the payment currency (said once at the end)."""
        if not r.symbol:
            return r.currency
        per = self._ctx.listings.get(r.symbol)
        if not per:
            self._untraded_income.setdefault(r.symbol, set()).add(
                (r.currency, r.cls))
            return r.currency
        name = rbc_norm_company(r.symdesc)
        if len(per) == 1:
            (cur, li), = per.items()
            if (cur != r.currency and name and li.names
                    and name not in li.names
                    and (r.symbol, cur) not in self._listing_warned):
                self._listing_warned.add((r.symbol, cur))
                self._warn(
                    f"{r.currency} {r.cls} row(s) for {r.symbol} "
                    f"({' '.join(r.symdesc.split())!r}) booked on the only "
                    f"listing the account trades, "
                    f"{self.apply_currency_suffix(r.symbol, cur)} "
                    f"({', '.join(sorted(map(repr, li.names.values())))}) — "
                    f"the names differ; if this is another security, map it "
                    f"with a ticker.map line. First: {r.label()}")
            return cur
        named = [c for c, li in per.items() if name and name in li.names]
        if len(named) == 1:
            return named[0]
        pool = named or sorted(per)
        held = [c for c in pool if per[c].held_on(r.date)]
        if len(held) == 1:
            return held[0]
        if r.currency in (held or pool):
            return r.currency
        if (r.symbol, '*') not in self._listing_warned:
            self._listing_warned.add((r.symbol, '*'))
            self._warn(
                f"{r.cls} row for {r.symbol} in {r.currency}: the account "
                f"trades {r.symbol} as {', '.join(sorted(per))} listings and "
                f"neither the Symbol Description nor the holdings tell which "
                f"one this belongs to — booked on "
                f"{self.apply_currency_suffix(r.symbol, r.currency)}. Check "
                f"it: {r.label()}")
        return r.currency

    def _report_untraded_income(self) -> None:
        """Income on a symbol no file of the account trades: the listing
        comes from the payment currency alone. A TSX stock paying USD
        whose buys sit in an earlier year's (absent) export — or in a
        hand-written .tt — then gets a .US identity; for a ROC or book
        adjustment that moves ACB on an empty pool, so warn."""
        for sym, kinds in sorted(self._untraded_income.items()):
            curs = sorted({c for c, _ in kinds})
            acb = any(cls in ('roc', 'book-adjust') for _, cls in kinds)
            listed = ', '.join(self.apply_currency_suffix(sym, c)
                               for c in curs)
            alt = 'TO' if 'USD' in curs else 'US'
            kinds_txt = ', '.join(sorted({k for _, k in kinds}))
            msg = (f"{sym}: {kinds_txt} row(s) but no trade rows for {sym} "
                   f"in any RBC file of this account — booked as {listed}, "
                   f"the payment currency's listing. Only if the position "
                   f"is really held under the other listing (a TSX stock "
                   f"paying USD, bought in an export or .tt outside these "
                   f"inputs), add to ticker.map:  GLOBAL "
                   f"{self.apply_currency_suffix(sym, curs[0])} "
                   f"{sym}.{alt}")
            if acb and curs != ['CAD']:
                self._warn(msg + " — a return of capital on the wrong "
                           "listing hits an empty pool and becomes a gain.")
            elif curs != ['CAD']:
                self._note(msg)

    def _check_emitted_symbols(self, txs) -> None:
        bad: Dict[str, int] = {}
        for t in txs:
            for key in ('symbol', 'symbol_new'):
                s = (t.get(key) or '')
                root = self._CURRENCY_SUFFIX_RE.sub('', s)
                if root and (rbc_is_temp_symbol(root)
                             or rbc_is_option_code(root)):
                    bad[s] = bad.get(s, 0) + 1
        for s, n in sorted(bad.items()):
            self._warn(
                f"emitted symbol {s} ({n} row(s)) is an RBC internal code "
                f"(temporary reorganization id or 7-character option "
                f"code), not a listed ticker — the position will not line "
                f"up with the rest of its history. Map it with a ticker.map "
                f"GLOBAL line once you know the real ticker.")

    # ---------------------------------------------------------- dispatch
    def _dispatch(self, r) -> List[Dict[str, Any]]:
        cls = r.cls
        if cls in ('trade', 'expiry', 'assignment', 'retraction'):
            tx = self._build_trade(r)
            return [tx] if tx else []
        if cls == 'split':
            tx = self._build_stock_split(r)
            if tx:
                return [tx]
            self._warn(f"stock-split row without a usable 'ON N SHS' base "
                       f"— {r.label()}. NOT booked.", unbooked=True)
            return []
        if cls == 'stock-dividend':
            return [self._build_stock_dividend(r)]
        if cls == 'reinvest-in-kind':
            return self._build_reinvest_in_kind(r)
        if cls == 'rights':
            return [self._build_rights(r)]
        if cls == 'reinvest':
            return self._build_reinvest(r)
        if cls == 'transfer':
            return [self._build_transfer(r)]
        # Income / book-cost branches below never move shares.
        if abs(r.qty) > 1e-12:
            raise _err(Path(self._fname), r.line,
                       f"a {cls} row carries quantity {r.qty:g} — an income "
                       f"row never moves shares, and this is not a form "
                       f"the parser books with shares (a stock dividend "
                       f"says STK DIV / STOCK DIVIDEND, an in-kind "
                       f"reinvestment 'Reinvest @ $price') "
                       f"({r.desc[:80]!r}). Refusing to book "
                       f"{r.value:,.2f} as {cls} and drop the shares; book "
                       f"the row in a .tt file.")
        if cls == 'book-adjust':
            return [self._build_book_adjust(r)]
        if cls == 'roc':
            if abs(r.value) < 0.005:
                self._warn(f"$0 return-of-capital row — {r.label()}; "
                           f"nothing to book (check the T3/T5 slip).")
            return [self.tx_roc_adjust(
                symbol=self._equity_symbol(r.symbol, r.currency, r, market=True),
                currency=r.currency, date=r.date, desc=r.desc,
                amount=r.value)]
        if cls == 'dividend':
            if abs(r.value) < 0.005:
                self._warn(f"$0 dividend row — {r.label()}; booked as a $0 "
                           f"dividend (check whether it adjusts book cost).")
            return self._build_dividend(r)
        if cls == 'tax':
            return [self._build_tax(r)]
        if cls == 'interest':
            return [self._build_interest(r)]
        if cls == 'fee':
            return [self._build_fee(r)]
        raise AssertionError(f"unhandled RBC row class {cls!r}")

    # --------------------------------------------------------- classifiers
    @staticmethod
    def _is_blank_row(row) -> bool:
        """True for an all-empty CSV line (or none at all). DictReader
        yields extra unnamed cells as a list under the None key."""
        if not row:
            return True
        for v in row.values():
            if isinstance(v, list):
                v = ''.join(x or '' for x in v)
            if (v or '').strip():
                return False
        return True

    @staticmethod
    def _is_dividend(activity, desc):
        """Dividend by ACTIVITY label, or by RBC's code/verb part of the
        description ("DIV - ", "CASH DIV ON", "DIST ON") — never by a
        word inside the security name ("DIVIDEND 15 SPLIT CORP")."""
        a = (activity or '').lower()
        if 'dividend' in a or 'distribution' in a:
            return True
        return bool(_RBC_INCOME_VERB_RE.search(desc or ''))

    @staticmethod
    def classify_row(row) -> str:
        return classify_rbc_row(row)

    # --------------------------------------------------------------- builders
    def _time(self, r) -> str:
        return rbc_time(r.k)

    def _build_stock_split(self, r):
        """A stock split → SPLIT scaling the existing pool by
        (base + received) / base, where `received` is the net shares
        moved (positive forward, negative reverse) and `base` is the
        pre-split count from "...ON <base> SHS". None when the ratio
        can't be derived."""
        m = _RBC_SPLIT_ON_SHS_RE.search(r.desc or '')
        received = r.qty
        if not m or abs(received) < 1e-9:
            return None
        base = float(m.group(1).replace(',', ''))
        sym_raw = self._resolve_temp(r)
        # On a SHORT pool the split debits shares (a negative quantity
        # for a forward split): (base + received)/base gave 0.95 for a
        # 21-for-20 and 0 (row dropped) for a 2-for-1 (audit S065-00).
        # The account's own rows say which side the pool is on; a long
        # or unknown pool keeps the signed rule (negative = reverse).
        li = self._ctx.listings.get(sym_raw, {}).get(r.currency)
        day_before = (datetime.strptime(r.date, '%Y-%m-%d')
                      - timedelta(days=1)).strftime('%Y-%m-%d')
        if li is not None and li.position_on(day_before) < -1e-9:
            received = -received
        factor = (base + received) / base if base > 0 else 0.0
        if factor <= 0:
            return None
        return {
            'action': 'SPLIT',
            'date': r.date, 'time': self._time(r), 'date_settle': r.date,
            'symbol': self._equity_symbol(sym_raw, r.currency, r),
            'symbol_new': '',
            'quantity': factor,
            'currency': r.currency,
            'price': 0.0,
            'net_amount': 0.0,
            'account': self.DEFAULT_ACCOUNT,
            'description': r.desc,
        }

    def _build_transfer(self, r):
        """An in-kind position transfer (TFI/TFO/TFR). The quantity moves;
        the transferred-in ACB is not booked here (the engine consumes the
        TRANSFER before the ACB pass and missing basis surfaces as
        incomplete history). RBC's "BOOK VALUE nnn" is carried as
        `book_value` EVIDENCE only — how transfers are booked is
        unchanged. Gated by --transfers at the taxjson-brokerage wrapper."""
        qty = r.qty
        if qty > 0 and _RBC_TRANSFER_OUT_RE.match(r.desc or ''):
            # Direction from RBC's code/verb only: 'DELIVER' anywhere in
            # the text (a security named DELIVERY HERO) flipped a
            # transfer-IN into an OUT (audit S016-05 / S065-03).
            qty = -qty
        tx = {
            'action': 'TRANSFER',
            'date': r.date, 'time': self._time(r),
            'date_settle': r.settle or r.date,
            'symbol': self._equity_symbol(self._resolve_temp(r), r.currency, r),
            'quantity': qty, 'currency': r.currency, 'price': 0.0,
            # Value column; RBC ships 0 for in-kind transfers.
            'net_amount': abs(r.value),
            'fee': 0.0,
            'account': self.DEFAULT_ACCOUNT, 'description': r.desc,
        }
        m = _RBC_BOOK_VALUE_RE.search(r.desc)
        if m:
            tx['book_value'] = abs(float(m.group(1).replace(',', '')))
        return tx

    def _build_trade(self, r):
        activity, desc = r.activity, r.desc
        opt = None
        if rbc_is_option_code(r.symbol) and r.symbol in self._occ_by_code:
            occ = self._occ_by_code[r.symbol]
        else:
            occ = self._row_occ(r)
        _sym = (r.symbol or '').strip().upper()
        if occ and _sym and not rbc_is_option_code(_sym) \
                and not rbc_is_temp_symbol(_sym):
            # A listed ticker in the Symbol column whose text names a
            # contract on ANOTHER underlying: not a shape any RBC export
            # has shown — refuse rather than pick one identity.
            raise _err(Path(self._fname), r.line,
                       f"Symbol {r.symbol!r} is a listed ticker but the "
                       f"description names option contract {occ} "
                       f"({r.desc[:70]!r}) — refusing to guess which one "
                       f"the row trades")
        if occ:
            opt = self.parse_option_from_description(r.desc) \
                or self.parse_option_from_description(r.symdesc)
            symbol = self.apply_currency_suffix(occ, r.currency)
        else:
            symbol = self._equity_symbol(self._resolve_temp(r), r.currency, r)

        is_assign_leg = (r.cls == 'assignment' or r.code == 'ASN'
                         or any(x in activity for x in ('Assignment',
                                                        'Exercise')))
        if r.settle:
            date_settle = r.settle
        elif occ and not is_assign_leg:
            date_settle = self.settlement_date_t1(r.date, "%Y-%m-%d",
                                                  currency=r.currency)
        else:
            # Equities — and an assignment/exercise OPTION leg, which is
            # one event with its stock leg: T+1 on the option leg alone
            # split the two and let another trade consume the premium
            # (audit S065-06).
            date_settle = self.equity_settlement_date(r.date, r.currency,
                                                      "%Y-%m-%d")
        date = r.date
        # An option EXPIRY has no settlement cycle, and RBC posts it the
        # next business day: book it on the contract's own expiry date
        # with settle == date, so a Dec-31 expiry stays in its year.
        is_expiry = bool(opt) and r.cls == 'expiry'
        if is_expiry:
            date = self.option_expiry_booking_date(date, opt['expiry'])
            date_settle = date
        elif r.cls == 'expiry':
            # A right / warrant expiry has no settlement cycle either: it
            # kept RBC's next-business-day settle and a Dec-31 expiry's
            # loss moved into the next year (audit S065-04).
            date = self.non_option_expiry_booking_date(date, desc)
            date_settle = date

        qty = r.qty
        is_retraction = r.cls == 'retraction'
        if activity.strip().lower() == 'sell' or is_retraction:
            qty = -abs(qty)
        price = r.price
        net = r.value
        if (r.value_blank and abs(qty) > 1e-12 and (
                r.cls == 'retraction' or activity.strip().lower() in (
                    'buy', 'sell', 'exercise', 'assignment'))):
            # A BLANK cash cell read as 0: a sale booked with no proceeds
            # (its whole ACB a loss), a buy at no cost (audit R1-82). A
            # real $0 trade carries an explicit "0".
            raise _err(Path(self._fname), r.line,
                       f"a {activity.strip() or r.cls} row has a BLANK "
                       f"Value — refusing to book it as $0 "
                       f"({r.label()}). Fill in the cash amount (an "
                       f"explicit 0 if there really was none).")
        if is_retraction and not price and qty:
            # RBC leaves Price blank on retractions; per-share = Value/Qty.
            price = round(abs(net) / abs(qty), 6)
        self._check_trade_money(r, qty, price, net, bool(occ),
                                is_retraction)

        is_option_symbol = bool(occ)
        action = 'BUYSELL'
        desc_is_assignment_notice = bool(_RBC_OPTION_PATTERNS[1].search(desc))
        if is_option_symbol and (
            any(x in activity for x in ('Assignment', 'Exercise'))
            or r.code == 'ASN'
            or desc_is_assignment_notice
        ):
            action = 'ASSIGN'

        fee = self.back_compute_fee(qty, price, net, is_option=is_option_symbol)
        if r.cls == 'expiry' and not opt:
            self._note(f"line {r.line}: non-option expiry (rights/warrants) "
                       f"booked as a $0 disposition of {symbol}")

        return {
            'action': action,
            'date': date,
            'time': '16:00:00' if is_expiry else self._time(r),
            'date_settle': date_settle,
            'symbol': symbol,
            'quantity': qty,
            'currency': r.currency,
            'price': price,
            'fee': fee,
            # RBC's Value is signed cash flow: a sell is normally positive,
            # but a sell whose commission exceeds its gross is NEGATIVE and
            # the engine books it as negative proceeds.
            'net_amount': net if qty < 0 else abs(net),
            'gross_amount': self.theoretical_gross(qty, price,
                                                   is_option=is_option_symbol),
            'account': self.DEFAULT_ACCOUNT,
            'description': desc,
            '_expiry': is_expiry,
        }

    def _check_trade_money(self, r, qty, price, net, is_option,
                           is_retraction) -> None:
        """Fail-closed identity for a priced trade (audit S023-19). RBC
        has no commission column: the difference between Value and
        |qty| x Price x multiplier IS the commission, so it must be a
        charge (a buy costs at least its gross, a sale nets at most its
        gross) and of commission size. A Value off by a factor (a
        shifted or mislabelled column) used to book with only a schema
        warning; the same row from Questrade or IB is refused. The
        bounds sit far outside every real RBC row (commission $0-$132)."""
        if not price or abs(qty) < 1e-12 or r.cls == 'expiry':
            return
        mult = self.OPTION_MULTIPLIER if is_option else 1
        gross = abs(qty) * price * mult
        act = (r.activity or '').strip().lower()
        sale = act != 'buy' and (act == 'sell' or is_retraction or qty < 0)
        fee = (gross - net) if sale else (abs(net) - gross)
        low = -(0.05 + 0.005 * gross)
        high = (max(250.0, 3.0 * abs(qty) if is_option else 0.0)
                + 0.05 * gross)
        if fee < low or (fee > high and not is_retraction):
            raise _err(Path(self._fname), r.line,
                       f"Value {net:,.2f} does not fit |Quantity| "
                       f"{abs(qty):g} x Price {price:g}"
                       f"{' x 100' if is_option else ''} = {gross:,.2f} "
                       f"(implied commission {fee:,.2f}) — a wrong or "
                       f"shifted column; refusing to book it "
                       f"({r.desc[:60]!r})")

    def _build_stock_dividend(self, r):
        """A stock dividend paid in shares ("DIS - <name> STK DIV ON N
        SHS"): the delivered shares enter at $0 cost, the convention the
        Questrade parser uses (KNOWN_ISSUES 'stock dividends enter the
        book at $0 cost'). It was an UNCLASSIFIED row left out of the
        book (or, under Dividends, a refusal) and the next full sale
        went short (audit S015-06)."""
        if abs(r.value) > 0.005 or r.qty < 0:
            raise _err(Path(self._fname), r.line,
                       f"a stock-dividend row with "
                       f"{'cash' if abs(r.value) > 0.005 else 'a negative quantity'}"
                       f" ({r.label()}) — not a shape this parser books; "
                       f"book it in a .tt file")
        symbol = self._equity_symbol(self._resolve_temp(r), r.currency, r)
        self._note(f"line {r.line}: stock dividend of {r.qty:g} {symbol} "
                   f"entered at $0 cost — in a taxable account add the "
                   f"declared amount (the T5 / distributions.map) for the "
                   f"correct ACB.")
        return {
            'action': 'BUYSELL',
            'date': r.date, 'time': self._time(r),
            'date_settle': r.settle or r.date,
            'symbol': symbol, 'quantity': r.qty, 'currency': r.currency,
            'price': 0.0, 'fee': 0.0, 'net_amount': 0.0, 'gross_amount': 0.0,
            'account': self.DEFAULT_ACCOUNT, 'description': r.desc,
        }

    def _build_reinvest_in_kind(self, r) -> List[Dict[str, Any]]:
        """The RBC Dominion Securities vintage's reinvested distribution:
        ONE Dividends row with the units and "Reinvest @ $p" (no cash
        row anywhere). It is the whole distribution: income of units x p
        AND a purchase of the units at p (audit R1-87 — it was refused
        as 'mis-coded or misread')."""
        if abs(r.value) > 0.005:
            raise _err(Path(self._fname), r.line,
                       f"a 'Reinvest @' row carries both units and cash "
                       f"({r.label()}) — refusing to guess which is the "
                       f"distribution")
        m = _RBC_REINVEST_AT_RE.search(r.desc)
        price = float(m.group(1).replace(',', ''))
        amount = round(r.qty * price, 2)
        symbol = self._equity_symbol(r.symbol, r.currency, r, market=True)
        self._note(f"line {r.line}: in-kind reinvested distribution — "
                   f"income {amount:,.2f} {r.currency} and a purchase of "
                   f"{r.qty:g} {symbol} at {price:g}; the distribution's "
                   f"character (dividend, capital gain, ROC) is on the "
                   f"fund's T3.")
        return [{
            'action': 'DIVIDEND',
            'date': r.date, 'time': '09:30:00', 'date_settle': r.date,
            'symbol': symbol, 'quantity': r.qty, 'currency': r.currency,
            'price': price, 'net_amount': amount, 'gross_amount': amount,
            'type': 'dividend', 'account': self.DEFAULT_ACCOUNT,
            'description': r.desc,
        }, {
            'action': 'BUYSELL',
            'date': r.date, 'time': self._time(r),
            'date_settle': r.settle or r.date,
            'symbol': symbol, 'quantity': r.qty, 'currency': r.currency,
            'price': price, 'fee': 0.0, 'net_amount': amount,
            'gross_amount': amount,
            'account': self.DEFAULT_ACCOUNT, 'description': r.desc,
        }]

    def _pair_reinvest_reversals(self, transactions) -> None:
        """Each reversed reinvestment (Quantity < 0, Value > 0) cancels
        one reinvestment of the same units and cash in this file: both
        drop out. abs() booked the reversal as a SECOND purchase (audit
        R1-83). A reversal with no original is refused."""
        drop = set()
        for key, r in self._rei_reversals:
            origs = self._rei_originals.get(key) or []
            if not origs:
                raise _err(Path(self._fname), r.line,
                           f"a reversed reinvestment ({r.label()}) whose "
                           f"original REI row is not in this file — "
                           f"refusing to book it as a second purchase. "
                           f"Delete both rows if the original is in an "
                           f"earlier export, or book the correction in a "
                           f".tt file.")
            drop.add(id(origs.pop()))
        if drop:
            transactions[:] = [t for t in transactions if id(t) not in drop]

    def _build_rights(self, r):
        """Rights/warrants distributed to every shareholder ("DIS - RTS
        ... RTS DIST ON N SHS"): no income (ITA s.15(1)(c) excludes rights
        conferred on all shareholders) and a NIL cost, so a $0 acquisition
        is the right booking — said out loud now instead of silently."""
        symbol = self._equity_symbol(self._resolve_temp(r), r.currency, r)
        self._note(f"line {r.line}: rights/warrants distribution booked as a "
                   f"$0 acquisition of {r.qty:g} {symbol} (nil ACB; ITA "
                   f"s.15(1)(c)). If these were NOT issued to all "
                   f"shareholders, their FMV may be a taxable benefit.")
        return {
            'action': 'BUYSELL',
            'date': r.date, 'time': self._time(r),
            'date_settle': r.settle or r.date,
            'symbol': symbol, 'quantity': r.qty, 'currency': r.currency,
            'price': 0.0, 'fee': 0.0, 'net_amount': 0.0, 'gross_amount': 0.0,
            'account': self.DEFAULT_ACCOUNT, 'description': r.desc,
        }

    def _build_reinvest(self, r):
        """Dividend reinvestment ("REI - ... REINV@C$32.2399"): a PURCHASE
        of the reinvested units at the cash reinvested. The distribution
        itself arrives as its own DIV/DIST row, so income is untouched —
        booking this row as a negative dividend netted the income away
        and dropped the units."""
        qty = abs(r.qty)
        net = abs(r.value)
        if qty < 1e-12 or net < 0.005:
            raise _err(Path(self._fname), r.line,
                       f"reinvestment row without units or cash "
                       f"({r.desc[:80]!r})")
        if (r.qty > 0) == (r.value > 0):
            raise _err(Path(self._fname), r.line,
                       f"reinvestment row with Quantity {r.qty:g} and "
                       f"Value {r.value:,.2f} of the same sign — a "
                       f"reinvestment buys units for cash (a reversal "
                       f"returns both); refusing to guess "
                       f"({r.desc[:60]!r})")
        rei_key = (self._equity_symbol(r.symbol, r.currency, r),
                   round(qty, 6), round(net, 2))
        if r.qty < 0:
            self._rei_reversals.append((rei_key, r))
            return []
        m = _RBC_REINV_PRICE_RE.search(r.desc)
        price = r.price or (float(m.group(1)) if m else round(net / qty, 6))
        tx = {
            'action': 'BUYSELL',
            'date': r.date, 'time': self._time(r),
            'date_settle': r.settle or r.date,
            'symbol': self._equity_symbol(r.symbol, r.currency, r),
            'quantity': qty, 'currency': r.currency, 'price': price,
            'fee': self.back_compute_fee(qty, price, net, is_option=False),
            'net_amount': net,
            'gross_amount': self.theoretical_gross(qty, price, is_option=False),
            'account': self.DEFAULT_ACCOUNT, 'description': r.desc,
        }
        self._rei_originals.setdefault(rei_key, []).append(tx)
        return [tx]

    def _build_book_adjust(self, r):
        """RBC's book-cost adjustments carry the amount in the description,
        not the Value column (Value is 0):
          "... 2022 NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK COST $5293.06"
              → a reinvested (notional) distribution: ACB UP
          "... RETURN OF CAPITAL ADJUSTMENT TO BOOK COST $1.16"
              → ACB DOWN (return of capital)."""
        m = _RBC_BOOK_COST_RE.search(r.desc)
        amount = float(m.group(1).replace(',', '')) if m else 0.0
        if amount < 0.005:
            self._warn(f"$0 book-cost adjustment — {r.label()}; nothing "
                       f"booked.")
        symbol = self._equity_symbol(r.symbol, r.currency, r, market=True)
        if is_roc_description(r.desc):
            return self.tx_roc_adjust(symbol=symbol, currency=r.currency,
                                      date=r.date, desc=r.desc, amount=amount)
        # The notional (reinvested) distribution is taxable income of the
        # year as well as an ACB increase; the export carries only the
        # book-cost side and taxjson books only that (audit S063-17 —
        # whether to book the income is the owner's call).
        self._warn(f"line {r.line}: notional distribution {amount:,.2f} "
                   f"{r.currency} on {symbol} raises its ACB; the "
                   f"distribution itself is income reported on the fund's "
                   f"T3 (usually box 21) and is NOT in taxjson's income "
                   f"totals — take it from the slip.")
        return {
            'action': 'ADJUST',
            'date': r.date, 'time': '09:30:00', 'date_settle': r.date,
            'symbol': symbol, 'quantity': 0.0, 'currency': r.currency,
            'net_amount': amount, 'gross_amount': 0.0, 'type': 'dist',
            'account': self.DEFAULT_ACCOUNT, 'description': r.desc,
        }

    def _build_dividend(self, r) -> List[Dict[str, Any]]:
        # `currency` is the dividend's payment currency (stays on the
        # record); the suffix follows the symbol's MARKET currency so a
        # cross-listed Canadian stock paying USD keeps its .TO identity.
        symbol = self._equity_symbol(r.symbol, r.currency, r, market=True)
        desc, net, currency, date = r.desc, r.value, r.currency, r.date
        is_net_tax = 'NON-RES TAX WITHHELD' in desc.upper()
        # Sign-preserving: a reversal row arrives NEGATIVE and must net
        # against the original posting.
        gross_amount = net
        out: List[Dict[str, Any]] = []
        if is_net_tax:
            # RBC doesn't expose the treaty rate; 15% is the common case.
            gross_amount = round(net / 0.85, 2)
            tax_withheld = round(gross_amount - net, 2)
        qty, rate = _parse_div_qty_rate(desc, gross_amount)
        if is_net_tax:
            out.append({
                'action': 'TAX',
                'date': date, 'time': '09:30:00', 'date_settle': date,
                'symbol': symbol, 'quantity': 0.0, 'currency': currency,
                'net_amount': tax_withheld, 'type': 'tax',
                'account': self.DEFAULT_ACCOUNT,
                'description': f"{desc} (Implied Tax)",
            })
        out.append({
            'action': 'DIVIDEND',
            'date': date, 'time': '09:30:00', 'date_settle': date,
            'symbol': symbol, 'quantity': qty, 'currency': currency,
            'price': rate,
            'net_amount': net, 'gross_amount': gross_amount,
            'type': 'dividend', 'account': self.DEFAULT_ACCOUNT,
            'description': desc,
        })
        return out

    def _build_tax(self, r):
        tax_symbol = (self._equity_symbol(r.symbol, r.currency, r, market=True)
                      if r.symbol else 'UNKNOWN')
        return {
            'action': 'TAX',
            'date': r.date, 'time': '09:30:00', 'date_settle': r.date,
            'symbol': tax_symbol, 'quantity': 0.0, 'currency': r.currency,
            # RBC books withholding NEGATIVE (cash out), a refund POSITIVE;
            # the repo-wide TAX convention is positive = tax withheld.
            'net_amount': -r.value, 'type': 'tax',
            'account': self.DEFAULT_ACCOUNT, 'description': r.desc,
        }

    def _build_interest(self, r):
        return {
            'action': 'INTEREST',
            'date': r.date, 'time': '09:30:00', 'date_settle': r.date,
            'symbol': 'CASH', 'quantity': 0.0, 'currency': r.currency,
            'net_amount': r.value,  # keep sign for interest
            'type': 'interest', 'account': self.DEFAULT_ACCOUNT,
            'description': r.desc,
        }

    def _build_fee(self, r):
        """A fee charged against a holding (FCH: the depositary's ADR
        custody fee). Positive net_amount = fee charged, as Questrade's
        FEE rows."""
        return {
            'action': 'FEE',
            'date': r.date, 'time': '09:30:00', 'date_settle': r.date,
            'symbol': (self._equity_symbol(r.symbol, r.currency, r, market=True)
                       if r.symbol else 'CASH'),
            'quantity': 0.0, 'currency': r.currency,
            'net_amount': -r.value, 'type': 'fee',
            'account': self.DEFAULT_ACCOUNT, 'description': r.desc,
        }

    # ------------------------------------------------------ reorganizations
    def _emit_reorg_event(self, ev) -> List[Dict[str, Any]]:
        if ev.kind == 'reversal':
            self._note(f"RBC reversal pair on {ev.removal.date} "
                       f"({ev.removal.symbol}, {abs(ev.removal.qty):g}) nets "
                       f"to zero — booked nothing: {ev.receipt.desc[:70]!r}")
            return []
        if ev.kind == 'option_adjust':
            return self._emit_option_adjust(ev)
        return self._emit_stock_reorg(ev)

    def _emit_option_adjust(self, ev) -> List[Dict[str, Any]]:
        """XCH on an option: RBC removes the contract under one code and
        re-adds it under another (special-dividend strike adjustment, the
        underlying's name change, a security-code change). The SAME
        contract continues — the position, its ACB and its open date carry
        over. RBC's own trade rows keep describing the adjusted contract
        with the ORIGINAL terms (TOU 64 after the $0.50 adjustment to
        63.50; TRP after .TRP1), so the position keeps its original OCC
        symbol unless the new code's own trade rows — in ANY file of the
        account, so a close in next year's export counts — describe a
        different contract, in which case it is renamed (factor 1)."""
        rem, rc = ev.removal, ev.receipt
        old = self._occ_by_code.get(rem.symbol) or self._row_occ(rem)
        new_own = self._occ_own.get(rc.symbol)
        described = self._row_occ(rc)
        if not old:
            self._warn(f"option adjustment on {rem.date}: cannot tell which "
                       f"contract {rem.symbol} is — {rem.label()}. Nothing "
                       f"booked.", unbooked=True)
            return []
        if abs(abs(rem.qty) - rc.qty) > 1e-9:
            self._warn(f"option adjustment on {rem.date} removes "
                       f"{abs(rem.qty):g} but adds {rc.qty:g} contracts "
                       f"({rem.symbol}→{rc.symbol}) — booked the ratio.",
                       lint=True)
        old_sym = self.apply_currency_suffix(old, rem.currency)
        target = new_own or old
        if not new_own:
            self._occ_by_code[rc.symbol] = old
        tgt_sym = self.apply_currency_suffix(target, rc.currency)
        adj = (f" (RBC now describes it as {described})"
               if described and described != old else '')
        if adj and not new_own:
            # No file of the account trades the new code: a later export
            # (another project, opened here via .tt) may close it under
            # the new description — say how to keep one symbol.
            adj += (f"; if a later export closes it as {described}, add to "
                    f"ticker.map:  GLOBAL "
                    f"{self.apply_currency_suffix(described, rc.currency)} "
                    f"{self.apply_currency_suffix(old, rem.currency)}")
        factor = rc.qty / abs(rem.qty) if rem.qty else 1.0
        if tgt_sym == old_sym and abs(factor - 1.0) < 1e-12:
            self._note(f"option adjustment {rem.date}: {rem.symbol}→{rc.symbol} "
                       f"is the same contract; position continues as "
                       f"{old_sym}{adj} — nothing to book "
                       f"({rem.desc[:60]!r})")
            return []
        self._note(f"option adjustment {rem.date}: {old_sym} continues as "
                   f"{tgt_sym} ×{factor:g}{adj}; ACB and open date carried")
        return [{
            'action': 'SPLIT',
            'date': rem.date, 'time': rbc_time(max(rem.k, rc.k)),
            'date_settle': rem.date,
            'symbol': old_sym,
            'symbol_new': '' if tgt_sym == old_sym else tgt_sym,
            'quantity': factor, 'currency': rem.currency,
            'price': 0.0, 'net_amount': 0.0,
            'account': self.DEFAULT_ACCOUNT,
            'description': (f"RBC option adjustment {rem.symbol}→{rc.symbol}: "
                            f"{rem.desc} | {rc.desc}"),
        }]

    def _emit_stock_reorg(self, ev) -> List[Dict[str, Any]]:
        """A name change / reverse or forward split / 1-for-1 exchange that
        RBC booked as a removal (often under a temporary code) plus a
        receipt: ONE SPLIT scaling (and, if the ticker changed, renaming)
        the pool — cost and acquisition dates carried. With a stated ratio
        and cash-in-lieu, the pool is scaled by the ratio and the
        fractional share is sold for the cash; a MER row's "ROC OF C$x"
        is booked as a return-of-capital ADJUST."""
        rem, rc = ev.removal, ev.receipt
        removed, received = abs(rem.qty), rc.qty
        src_raw = rem.symbol
        how = ''
        if rbc_is_temp_symbol(src_raw) or not src_raw:
            # The old company's ticker from any row of the account's
            # files that predates the event (its buys are usually in an
            # earlier year's export).
            old_name, _new_name = _rbc_removal_names(rem)
            hit = None
            for nm in (rem.symdesc, old_name):
                key = rbc_norm_company(nm) if nm else ''
                hit = self._ctx.ticker_for_name(key, rem.date,
                                                before_only=True)
                if hit:
                    break
            if hit:
                src_raw, how = hit, f" (temporary code {rem.symbol} = {hit} by name)"
            else:
                src_raw = rc.symbol
                how = (f" (temporary code {rem.symbol or '-'} assumed to be "
                       f"the receipt's ticker {rc.symbol})")
                same_co = rbc_name_similarity(
                    old_name, _rbc_receipt_name(rc)) >= 0.8
                if not (same_co and abs(removed - received) > 1e-9):
                    tgt_guess = self._equity_symbol(rc.symbol, rc.currency, rc)
                    self._warn(
                        f"reorganization on {rem.date}: the removal is "
                        f"booked under RBC temporary code {rem.symbol or '-'} "
                        f"({old_name or 'no name'!r}) and no row in this "
                        f"account's RBC files names that company's ticker, "
                        f"so it is ASSUMED to be the receipt's {tgt_guess}. "
                        f"If the old position was held under another ticker "
                        f"(its buys in an export or .tt not in this "
                        f"account's inputs), that pool is stranded and the "
                        f"{tgt_guess} sale goes short. Fix: add the export "
                        f"holding its buys, or add to ticker.map:  GLOBAL "
                        f"<old ticker>.{tgt_guess.rsplit('.', 1)[-1]} "
                        f"{tgt_guess}   — {rem.label()}", lint=True)
        src = self._equity_symbol(src_raw, rem.currency, rem)
        tgt = self._equity_symbol(rc.symbol, rc.currency, rc)
        time = rbc_time(max(rem.k, rc.k))
        out: List[Dict[str, Any]] = []
        desc = (f"RBC {rem.code or 'reorg'} reorganization {src}→{tgt} "
                f"({removed:g} removed, {received:g} received): "
                f"{rem.desc} | {rc.desc}")

        # Cash on the legs: a MER row's return of capital; anything else is
        # not understood and is said out loud.
        if ev.roc_amount > 0.005:
            out.append(self.tx_roc_adjust(
                symbol=src, currency=rem.currency, date=rem.date,
                desc=f"{rem.desc} (return of capital in the reorganization)",
                amount=ev.roc_amount))
        else:
            for leg in (rem, rc):
                if abs(leg.value) > 0.005:
                    self._warn(f"reorganization leg carries cash "
                               f"{leg.value:,.2f} that the parser does not "
                               f"understand — NOT booked: {leg.label()}",
                               unbooked=True)

        ratio = ev.ratio
        # SIGNED: a reversal CIL row (negative) cancels a posting; abs()
        # counted it as more proceeds (audit S063-19).
        cil_total = sum(c.value for c in ev.cil)
        if ev.cil and cil_total < -0.005:
            self._warn(f"cash in lieu after the {rem.date} reorganization "
                       f"of {rem.symbol} nets NEGATIVE ({cil_total:,.2f}) "
                       f"— a reversal with no posting; NOT booked.",
                       unbooked=True)
        frac = (removed * ratio - received) if ratio else 0.0
        sell = None
        if ratio and cil_total > 0.005 and frac > 1e-6:
            factor = ratio
            c0 = min(ev.cil, key=lambda c: (c.date, c.k))
            sell = {
                'action': 'BUYSELL',
                'date': c0.date, 'time': rbc_time(c0.k),
                'date_settle': c0.settle or c0.date,
                'symbol': tgt, 'quantity': -frac,
                'currency': c0.currency,
                'price': round(cil_total / frac, 6), 'fee': 0.0,
                'net_amount': round(cil_total, 2), 'gross_amount': 0.0,
                'account': self.DEFAULT_ACCOUNT,
                'description': (f"cash in lieu of {frac:.6g} fractional "
                                f"share(s) of {tgt} after the {rem.date} "
                                f"reorganization: "
                                + ' | '.join(c.desc for c in ev.cil)),
            }
        else:
            factor = received / removed if removed else 0.0
            if ratio and abs(removed * ratio - received) >= 1.0 - 1e-9:
                self._warn(f"reorganization {src}→{tgt} on {rem.date}: the "
                           f"stated ratio {ratio:g} gives "
                           f"{removed * ratio:g} shares but RBC delivered "
                           f"{received:g} — booked what was delivered.",
                           lint=True)
            if cil_total > 0.005:
                self._warn(f"cash in lieu {cil_total:,.2f} after the "
                           f"{rem.date} reorganization of {src} cannot be "
                           f"tied to a fractional share (no stated ratio) "
                           f"— NOT booked.", unbooked=True)
        if factor <= 0:
            self._warn(f"reorganization {src}→{tgt} on {rem.date} has no "
                       f"usable factor — NOT booked: {rem.label()}", unbooked=True)
            return out
        if tgt == src and abs(factor - 1.0) < 1e-12:
            self._note(f"{rem.date}: {rem.code} {src} {removed:g}→{received:g} "
                       f"is a 1-for-1 name change/exchange{how} — no "
                       f"position effect, nothing booked")
        else:
            self._note(f"{rem.date}: {rem.code} {src}→{tgt} ×{factor:.6g}"
                       f"{how} — one SPLIT, cost carried")
            out.append({
                'action': 'SPLIT',
                'date': rem.date, 'time': time, 'date_settle': rem.date,
                'symbol': src,
                'symbol_new': '' if tgt == src else tgt,
                'quantity': factor, 'currency': rem.currency,
                'price': 0.0, 'net_amount': 0.0,
                'account': self.DEFAULT_ACCOUNT, 'description': desc,
            })
        if sell:
            out.append(sell)
        return out
