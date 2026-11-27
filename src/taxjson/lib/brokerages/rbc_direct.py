"""RBC Direct Investing activity-export parser.

Every row is classified by its ACTIVITY label and the RBC event code that
prefixes the Description ("DIV - ", "MGR - ", "XCH - ", ...), never by
words inside the security name: a name like "SAMPLE DIVIDEND SPLIT CORP" or
"SAMPLE HIGH DIVIDEND ETF" once turned in-kind transfers and a retraction
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

from taxjson.lib.stage_msg import emit_line
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, NamedTuple

from taxjson.lib.row_types import STOCK_DIVIDEND
# The description helpers Questrade read through RBC's privates until
# step 5f: the toolbox's now.
from taxjson.parsers.toolbox.money import implied_commission_bounds
from taxjson.model import money as M
from taxjson.parsers.toolbox.descriptions import (
    PIL_DESC_RE, REINV_CUR, REINV_CUR_RE, REINV_PRICE_RE,
    reinvest_identity_error, reinvest_row_price, _parse_div_qty_rate)
from taxjson.parsers.toolbox.expiry import (non_option_expiry_booking_date,
                                            option_expiry_booking_date)
from taxjson.parsers.protocol import Outcome, builds
from taxjson.lib.brokerages.base import (BaseBrokerage, BrokerageParseError,
                                         names_underlying,
                                         ticker_map_joins,
                                         warrant_exercise_tt_hint,
                                         pair_warrant_legs,
                                         answered_rename_note,
                                         rename_how, rename_reach,
                                         declared_rename_where,
                                         combined_accounts_note,
                                         combined_accounts_refusal,
                                         DESC_NUMBER_RE, OPTION_STRIKE_RE,
                                         desc_number,
                                         income_facts_from_description,
                                         is_roc_description,
                                         PASTED_EXPORTS_FIX,
                                         shown_name, zero_units_refusal)
from taxjson.parsers.toolbox.assets import QTY_ZERO
from taxjson.lib.corp_actions import (
    RBC_REORG_CODES, RbcReorgPairing, pair_rbc_reorganizations,
    rbc_norm_company, rbc_row_company,
    rbc_is_temp_symbol, rbc_is_option_code, rbc_rights_key,
    rbc_name_similarity, _rbc_removal_names, _rbc_receipt_name,
)
from taxjson.parsers.toolbox.masking import mask_id
from taxjson.lib.symbol_codes import _RBC_CODE_RE


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
# Every RBC export vintage this parser knows carries
# all of these; a file without them is not an export this parser knows.
REQUIRED_COLUMNS = ('Date', 'Activity', 'Symbol', 'Quantity', 'Price',
                    'Settlement Date', 'Currency', 'Description')

# Income is recognised by RBC's code/verb part of the description only —
# never by words in the security name ("SAMPLE DIVIDEND SPLIT CORP").
#   "DIV - <name> CASH DIV  ON  500 SHS ..."   "<name> DIST  ON  400 SHS"
_RBC_INCOME_VERB_RE = re.compile(
    r'^\s*DIV\s*-|\bCASH\s+DIV(?:IDEND)?\s+ON\b|\bDIST\s+ON\b', re.I)
# Amounts in description text are captured with every comma and judged
# by base.desc_number: thousands commas only (a decimal comma is refused,
# never stripped into a 100x amount — audit S016-01 / S064-19).
_RBC_BOOK_COST_RE = re.compile(
    r'\bADJUSTMENT\s+TO\s+BOOK\s+COST\s+\$?\s*' + DESC_NUMBER_RE, re.I)
_RBC_BOOK_VALUE_RE = re.compile(
    r'\bBOOK\s+VALUE\s+\$?\s*-?' + DESC_NUMBER_RE, re.I)

# A forward/reverse stock split booked as ONE 'Reorganization' row with the
# net shares moved in Quantity and "... STK SPLIT ON <base> SHS ..." e.g.
#   "DIS - VANGUARD ... GROWTH ETF STK SPLIT ON 14 SHS REC 04/17/26 ..."
_RBC_STK_SPLIT_RE = re.compile(r'\b(?:STK|STOCK|FORWARD|REVERSE)\s+SPLIT\b',
                               re.I)
_RBC_SPLIT_ON_SHS_RE = re.compile(r'\bON\s+' + DESC_NUMBER_RE + r'\s+SHS\b',
                                  re.I)
# Legacy description tokens for rows whose Activity label is unknown.
_TRADE_DESC_RE = re.compile(r'\b(?:Buy|Sell)\b')
# The verb the export ends a trade's description with ("NAME - Sell"):
# after a separator, so a name ending "E-Buy" is a name.
_TRADE_DESC_VERB_RE = re.compile(r'\s-\s*(Buy|Sell)\s*$')

# A TSX fund's US-dollar class ("... ETF US DOLLAR UNITS", "... U S DLR
# CURRENCY ETF"): RBC books it under the bare symbol in USD, which reads
# as a US listing (ROOT.US — often another, NYSE issuer). No security is
# named in the code (owner, 2026-10-04): the project's ticker.map
# `EXTRACT description words | USD | ROOT.U.TO` line moves the rows, and
# until it does the row's .US listing is said out loud with that line
# (re-audit A2-1043).
from taxjson.lib.markets import USD_UNITS_RE as _RBC_USD_UNITS_RE  # noqa: E402

# Option description as RBC writes it, with the codes that may prefix it
# (EXP expiry, ASN assignment, XCH adjustment/exchange). Overrides the
# base patterns, which don't know "XCH -".
_RBC_OPTION_PATTERNS = (
    re.compile(
        r'^(?:(?:EXP|ASN|XCH)\s*-\s*)?(CALL|PUT)\s+\.?([A-Z0-9\s\.]+?)\s+'
        r'(\d{1,2}/\d{1,2}/\d{2})\s+' + OPTION_STRIKE_RE, re.I),
    re.compile(
        r'ASSIGNMENT OF OPTION.*?(CALL|PUT)\s+\.?([A-Z0-9\s\.]+?)\s+'
        r'(\d{1,2}/\d{1,2}/\d{2})\s+' + OPTION_STRIKE_RE, re.I),
)
# A stock dividend paid in shares ("DIS - <name> STK DIV ON 1390 SHS").
_RBC_STK_DIV_RE = re.compile(r'\bSTK\.?\s+DIV\b|\bSTOCK\s+DIVIDEND\b', re.I)
# An older (RBC Dominion Securities) vintage books a mutual fund's
# reinvested distribution as ONE Dividends row carrying the units:
# "DIV - <fund> As Of 04/07/22 Reinvest @ $21.217", Quantity 6.73, Value
# blank — the whole distribution, reinvested (audit R1-87).
_RBC_REINVEST_AT_RE = re.compile(
    r'\bREINVEST\s*@\s*\$?\s*' + DESC_NUMBER_RE, re.I)
# The direction of an in-kind transfer comes from RBC's code or its verb,
# never from a word in the security name ("DELIVERY HERO", audit S016-05).
_RBC_TRANSFER_OUT_RE = re.compile(
    r'^\s*(?:TF[OW]\b|(?:[A-Z]{2,4}\s*-\s*)?(?:TRANSFER\s+OUT|DELIVER)\b)',
    re.I)
# RBC's open/close marker at the end of an option trade's description:
# "CALL .ABC.B 03/19/27 30 ABC COMMUNICATIONS INC CA CLOSE CONTRACT".
_RBC_OPEN_CLOSE_RE = re.compile(r'\b(OPEN|CLOSE)\s+CONTRACT\b', re.I)
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
    # The Value cell (the cash before commission) of an export that has
    # the Amount column too; None when the export has one money column or
    # the cell is blank. Its sign is a trade's direction (`_refuse_trade_side`).
    gross: Optional[float] = None
    qty_blank: bool = False    # the Quantity cell was EMPTY (not "0")

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
    # What the parse says as an ATTENTION (a day put in the broker's
    # open/close order on the day's own rows alone, backlog TJ-5363).
    attentions: List[str] = field(default_factory=list)
    # The export's own timestamp ("Activity Export as of Jan 5, 2026 at
    # 8:59:00 am ET"), ISO; '' when the preamble has none. RBC does not
    # write the date range chosen in the UI, so this is an upper bound
    # on what the file can contain (audit S063-22).
    as_of: str = ''
    # The demo layout's "Date Range: January 1, 2025 - June 15, 2025"
    # line (the dates the export was asked for), ISO; ('', '') when the
    # preamble has none (backlog TJ-5348: an option series' coverage).
    date_range: Tuple[str, str] = ('', '')


def _err(path: Path, line: int, msg: str) -> RbcFormatError:
    return RbcFormatError(f"{shown_name(path)}:{line}: {msg}")


_NUM_RE = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)', re.ASCII)
# A first group of 0 is a decimal comma ('0,125'), never thousands
# (audit S055-08).
_THOUSANDS_RE = re.compile(r'[+-]?[1-9]\d{0,2}(?:,\d{3})+(?:\.\d*)?',
                           re.ASCII)


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
    if v in (float('inf'), float('-inf')):
        # A 400-digit run overflows (audit S055-09).
        raise _err(path, line, f"{column} {raw!r} is out of range — "
                   f"refusing it")
    if neg:
        if v < 0:
            raise _err(path, line, f"{column} {raw!r}: a parenthesised "
                       f"negative that is also signed — refusing to guess")
        v = -v
    return v


def _canon(cell: str) -> Optional[str]:
    return _CANON.get((cell or '').replace('﻿', '').strip().lower())


# The export's preamble line: "Activity Export as of Jan 5, 2026 at
# 8:59:00 am ET" (older exports: "Activity Export as of Date Jan 5, 2026").
_RBC_AS_OF_RE = re.compile(
    r'\bActivity\s+Export\s+as\s+of\s+(?:Date\s+)?'
    r'([A-Za-z]{3,9})\.?\s+(\d{1,2}),\s*(\d{4})', re.I)


def _month_date(month: str, day: str, year: str) -> str:
    """'Jan' / 'January', '5', '2026' -> '2026-01-05'; '' when it is no
    date."""
    for fmt in ('%b %d %Y', '%B %d %Y'):
        try:
            return datetime.strptime(
                f"{month[:3] if fmt == '%b %d %Y' else month}"
                f" {day} {year}", fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return ''


def _export_as_of(records, hpos: int) -> str:
    """The ISO date of the "Activity Export as of <date>" preamble above
    the header row, or '' when there is none (or it does not parse)."""
    for _line, cells in records[:hpos]:
        m = _RBC_AS_OF_RE.search(' '.join(c for c in cells if c))
        if not m:
            continue
        got = _month_date(m.group(1), m.group(2), m.group(3))
        if got:
            return got
    return ''


# The demo layout's preamble line: "Date Range: January 1, 2025 - June
# 15, 2025" (unquoted, its commas split it into cells: joined again).
_RBC_DATE_RANGE_RE = re.compile(
    r'\bDate\s+Range\s*:?\s*([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s*(\d{4})'
    r'\s*(?:-|\u2013|to)\s*([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s*(\d{4})', re.I)


def _export_date_range(records: List[Tuple[int, List[str]]],
                       hpos: int) -> Tuple[str, str]:
    """(first, last) ISO dates of the "Date Range:" preamble line above
    the header row, or ('', '') when there is none (or it does not
    parse, or runs backwards)."""
    for _line, cells in records[:hpos]:
        m = _RBC_DATE_RANGE_RE.search(' '.join(c for c in cells if c))
        if not m:
            continue
        lo, hi = _month_date(*m.group(1, 2, 3)), _month_date(*m.group(4, 5, 6))
        if lo and hi and lo <= hi:
            return lo, hi
    return '', ''


def rbc_export_as_of(path) -> str:
    """The "Activity Export as of" date (ISO) of an RBC activity export
    read from its first lines only, or '' when the file is not one (or
    cannot be read) — for checks that do not parse the whole file
    (`tjs checklist` inputs-frozen, audit S063-22)."""
    try:
        with open(path, 'rb') as fh:
            head = fh.read(4096)
    except OSError:
        return ''
    try:
        text = (head.decode('utf-16', errors='ignore')
                if head[:2] in (b'\xff\xfe', b'\xfe\xff')
                else head.decode('utf-8-sig', errors='ignore')).lstrip('\ufeff')
    except (UnicodeDecodeError, LookupError):
        return ''
    lines = text.splitlines()[:5]
    return _export_as_of([(i, [ln]) for i, ln in enumerate(lines)],
                         len(lines))


def header_missing(cells) -> Optional[List[str]]:
    """None when `cells` is not an RBC header candidate (no Date and
    Activity columns — a preamble line like "Activity Export as of Date
    Jan 5" is one cell and never qualifies); else the required columns
    it lacks ([] for a complete header). The parser's header test and
    broker detection's (lib/brokerages/detect.py) are this one function."""
    present = {c for c in (_canon(x) for x in cells) if c}
    if 'Date' not in present or 'Activity' not in present:
        return None
    missing = [c for c in REQUIRED_COLUMNS if c not in present]
    if not ({'Value', 'Amount'} & present):
        missing.append('Value (or Amount)')
    return missing


def is_holdings_export(first_text: str) -> bool:
    """The first non-empty line of an RBC Holdings export (positions,
    not transactions) — refused by the parser with its own message."""
    return bool(re.match(r'^\s*Holdings\s+Export\b', first_text, re.I))


def _find_header(records, path: Path):
    """The header row: the first record whose cells include the Date and
    Activity COLUMNS (header_missing). Raises when no such row exists or
    when it lacks a required column."""
    for pos, (line, cells) in enumerate(records):
        missing = header_missing(cells)
        if missing is None:
            continue
        canon = [_canon(c) for c in cells]
        if missing:
            raise _err(path, line, f"RBC header row lacks column(s) "
                       f"{', '.join(missing)} (found: "
                       f"{', '.join(c.strip() for c in cells)}) — not an "
                       f"export layout this parser knows; refusing to "
                       f"guess which column is which")
        return pos, canon
    first = next((' '.join(c.strip() for c in cells if c.strip())
                  for _, cells in records if any(c.strip() for c in cells)),
                 '')
    if is_holdings_export(first):
        # The holdings report (positions at a date), not the activity
        # export (audit R1-332: it died with a bare 'no header' error).
        raise RbcFormatError(
            f"{shown_name(path)}: this is an RBC Holdings export ({first[:50]!r}), "
            f"not an Activity export — it lists positions, not "
            f"transactions; refusing it. Download Activity (all "
            f"transaction types) for the account instead.")
    raise RbcFormatError(
        f"{shown_name(path)}: no RBC header row found (a row with the columns "
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
    return RbcFormatError(f"{shown_name(path)}: mixed date formats in the {column} "
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
        f"{shown_name(path)}: ambiguous dates — the file reads validly as more than "
        f"one of {sorted(set(amb))} (day/month vs month/day) and the "
        f"settlement dates don't settle it. Refusing to guess; re-export "
        f"with month names (RBC's default) or ISO dates.")


def _gross_cell(cell: Dict[str, str], num) -> Optional[float]:
    """The Value cell of a row whose export has the Amount column too
    (`RbcRow.gross`); None otherwise, or when the cell is blank."""
    if 'Amount' in cell and cell.get('Value', ''):
        return num('Value')
    return None


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
        text = text.lstrip('\ufeff')             # a re-save's second BOM
    except UnicodeDecodeError as e:
        raise RbcFormatError(f"{shown_name(path)}: not UTF-8/UTF-16 text ({e}) — "
                             f"re-export the CSV from RBC") from None
    reader = csv.reader(io.StringIO(text))
    records = []
    end = 0
    for cells in reader:
        # The record's FIRST physical line (re-audit A2-1045): line_num
        # is where a record that spans line breaks ENDS, so an error
        # named the end of a swallowed span, not the stray quote.
        records.append((end + 1, cells))
        end = reader.line_num
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
        if all(cell.get(c, '').lower() == c.lower()
               for c in ('Date', 'Activity', 'Symbol')):
            # The header again: two exports pasted into one file. It was
            # refused as "Amount 'Amount' is not a number" (backlog
            # TJ-0099); a plain concatenation (no second header) is
            # still read as newest-first runs (A2-1046).
            raise _err(path, line, f"the header repeats here — two "
                       f"downloads pasted into one file. "
                       f"{PASTED_EXPORTS_FIX}")
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
            n_sw = sum(c.count('\n') for c in cells)
            raise _err(path, line, f"a cell spans a line break "
                       f"({spill[:80]!r}) — an unescaped quote in the "
                       f"Description swallowed the next {n_sw} line(s). "
                       f"Delete the stray double quote on this line of the "
                       f"CSV and re-run; refusing to drop the swallowed "
                       f"row(s)")
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
            account=cell.get('Account', ''), gross=_gross_cell(cell, num),
            value_blank=value_blank, qty_blank=not cell.get('Quantity'),
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
    # Segment index per row: one for a monotone file. Two exports
    # concatenated without a second header go both ways over the whole
    # file (re-audit A2-1046): read as a few monotone runs — split where
    # the dates turn — when one direction needs at most two turns and
    # the other more; a day that spans two runs keeps one common time.
    seg = [0] * len(rows)
    if desc_ok:
        newest_first = True
    elif asc_ok:
        newest_first = False
    else:
        ups = [i + 1 for i, (a, b) in enumerate(zip(dates, dates[1:]))
               if b > a]
        downs = [i + 1 for i, (a, b) in enumerate(zip(dates, dates[1:]))
                 if b < a]
        breaks = (ups if len(ups) < len(downs) else downs
                  if len(downs) < len(ups) else None)
        if breaks is not None and len(breaks) <= 2:
            newest_first = len(ups) < len(downs)
            for b in breaks:
                for i in range(b, len(rows)):
                    seg[i] += 1
            notes.append(f"rows are in {len(breaks) + 1} "
                         f"{'newest' if newest_first else 'oldest'}-first "
                         f"runs (concatenated exports?) — same-day order "
                         f"read per run")
        else:
            newest_first = None
            notes.append("rows are not in date order, so same-day rows "
                         "keep one common time (intra-day order unknown)")
    if newest_first is not None:
        by_day: Dict[str, List[RbcRow]] = {}
        segs_of: Dict[str, set] = {}
        for i, r in enumerate(rows):
            by_day.setdefault(r.date, []).append(r)
            segs_of.setdefault(r.date, set()).add(seg[i])
        for d in [d for d, ss in segs_of.items() if len(ss) > 1]:
            del by_day[d]           # a day in two runs: order unknown (k 0)
        for day_rows in by_day.values():
            n = len(day_rows)
            for i, r in enumerate(day_rows):
                r.k = (n - 1 - i) if newest_first else i
            # An option assignment is ONE event booked as two rows — the
            # ASN option leg and the stock leg at the strike ("...
            # ASSIGNMENT OF OPTION AS OF ..."). They share one time, so the
            # engine's same-timestamp ladder (option leg first) folds the
            # premium into the stock leg (s.49(3) for a call, s.49(3.1)
            # for a put); the stock leg sorting
            # first (it is listed below the ASN row) lost the fold.
            grp = [r for r in day_rows
                   if r.cls == 'assignment' or (
                       r.cls == 'trade'
                       and 'ASSIGNMENT OF OPTION' in r.desc.upper())]
            if grp:
                k0 = min(r.k for r in grp)
                for r in grp:
                    r.k = k0
    return RbcExport(path=path, rows=rows, n_footers=n_footers, notes=notes, date_fmt=date_fmt,
                     columns=[c for c in canon if c], settle_fmt=settle_fmt,
                     newest_first=newest_first, as_of=_export_as_of(records, hpos),
                     date_range=_export_date_range(records, hpos))


# The tax words in the parser's notes, by the project's country
# (taxjson-brokerage --country; `tjs run` always passes it). The
# parser books the same rows either way — only the words differ: a US
# project never reads a T3/T5 slip, an ACB or the ITA (re-audit A2-0729,
# A2-0731, A2-0733, A2-0736, A2-1254, A2-1309, A2-1313, A2-1314,
# A2-1321, A2-1344, A2-1347). None (no --country): neutral words.
RBC_TERMS: Dict[Optional[str], Dict[str, str]] = {
    'canada': {
        'fund_slip': "the fund's T3",
        'fund_slips': "the funds' {y} T3 slips",
        'dist_income': "the fund's T3 (usually box 21)",
        'roc_slip': 'the T3/T5 slip',
        'roc_income': "the fund's T3 (with box 42)",
        'rights': 'nil ACB; ITA s.15(1)(c)',
        'rights_benefit': 'their FMV may be a taxable benefit',
    },
    'usa': {
        'fund_slip': 'Form 1099-DIV',
        'fund_slips': 'the {y} Forms 1099-DIV',
        'dist_income': 'Form 1099-DIV',
        'roc_slip': 'Form 1099-DIV (box 3)',
        'roc_income': 'Form 1099-DIV (box 3 is the nondividend part)',
        'rights': 'zero basis; IRC §305(a) — §307 allocates basis from '
                  'the shares when the rights are worth 15% or more of '
                  'them, or when you elect it',
        'rights_benefit': 'they may be a taxable distribution (§305(b))',
    },
    None: {
        'fund_slip': "the fund's tax slip",
        'fund_slips': "the funds' {y} tax slips",
        'dist_income': "the fund's tax slip",
        'roc_slip': 'the tax slip',
        'roc_income': "the fund's tax slip",
        'rights': 'nil cost',
        'rights_benefit': 'their value may be taxable',
    },
}


def rbc_terms(country: Optional[str]) -> Dict[str, str]:
    """The note words for `country` ('canada' | 'usa' | None); the cost
    noun is lib/country's COST_TERM, the one every report uses."""
    from taxjson.lib.country import COST_TERM
    terms = dict(RBC_TERMS.get(country, RBC_TERMS[None]))
    terms['cost'] = COST_TERM.get(country, COST_TERM[None])
    return terms


# RBC posts year-end book-cost adjustments (a notional distribution, a
# year-end return of capital) dated Dec 31 but only after the fund's
# tax slips are out, in the following spring (2026-09 audit R1-85): an
# export taken before this day of the next year may not hold them. The
# account's `[accounts.<name>] year_end_posting = "MM-DD"` sets the day
# (taxjson-brokerage --year-end-posting); this is its documented
# default.
DEFAULT_YEAR_END_POSTING = "06-30"


def parse_month_day(text) -> tuple:
    """(month, day) of an "MM-DD" string; ValueError naming the value
    when it is not a real day (Feb 29 is refused: not every year has
    it)."""
    import re as _re
    from datetime import date as _date
    s = str(text or "").strip()
    m = _re.fullmatch(r"(\d{1,2})-(\d{1,2})", s)
    if not m:
        raise ValueError(f"{text!r} is not a MM-DD day such as \"06-30\"")
    md = (int(m.group(1)), int(m.group(2)))
    try:
        _date(2001, *md)
    except ValueError:
        raise ValueError(f"{text!r} is not a day of the year (MM-DD)") \
            from None
    return md


def rbc_coverage_messages(exports, year: int, listings=None,
                          today=None,
                          country: Optional[str] = None,
                          year_end_posting: Optional[str] = None
                          ) -> List[str]:
    """Coverage findings for tax year `year` from the exports' "as of"
    timestamps (audit S063-22), one message each:

      * ATTENTION when every export of an RBC ACCOUNT was taken before
        Dec 31 of `year` (a finished year) with trading days left: a
        late-December trade cannot be in any file — the RBC sibling of
        IB's statement-period check. Judged per Account (re-audit
        A2-0272 / A2-0275: another account's later export certified this
        one's December), over trading days (re-audit A2-1049: an export
        of Dec 31 got an inverted range, a Friday one a weekend);
      * a note when the export was taken ON Dec 31 (that day's later
        activity may be missing);
      * a note when the exports holding `year`'s rows were all taken
        before RBC posts the year's back-dated Dec-31 book-cost
        adjustments (by `year_end_posting` of the next year, default
        06-30 — the account's setting) while the account held a
        position at the year end — they may be missing (R1-85).

    `exports`: [(file name, as_of ISO, [row ISO dates][, {accounts}])];
    `listings`: the account context's position timelines (symbol ->
    currency -> _Listing), or None to skip the holding test. Files
    without an as-of line are not judged."""
    from datetime import date as _date
    from taxjson.lib.market_calendar import is_trading_day
    today = today or _date.today()
    y = int(year)
    year_end = f"{y}-12-31"
    dated = [(e[0], e[1], e[2], set(e[3]) if len(e) > 3 and e[3] else {''})
             for e in exports if e[1]]
    out: List[str] = []
    if not dated or today.isoformat() <= year_end:
        return out                      # no timestamps / the year is open
    accounts = sorted({a for *_x, accts in dated for a in accts})
    for acct in accounts:
        mine = [(n, a) for n, a, _d, accts in dated if acct in accts]
        name, last = max(mine, key=lambda x: x[1])
        which = (f" of RBC account {_mask_account(acct)}"
                 if len(accounts) > 1 else "")
        if last >= year_end:
            if last == year_end:
                out.append(
                    f"note: {name}: the latest RBC export{which} was "
                    f"taken on {year_end} itself — activity later that "
                    f"day may be missing. Export {y} again after Jan 31, "
                    f"{y + 1} if you traded on Dec 31.")
            continue
        d = datetime.strptime(last, '%Y-%m-%d').date() + timedelta(days=1)
        end = datetime.strptime(year_end, '%Y-%m-%d').date()
        gap = []
        while d <= end:
            if is_trading_day(d, 'CAD') or is_trading_day(d, 'USD'):
                gap.append(d)
            d += timedelta(days=1)
        if not gap:
            continue                    # only a weekend / holidays left
        who = (f"RBC account {_mask_account(acct)}'s"
               if len(accounts) > 1 else "account's")
        out.append(
            f"warning: ATTENTION: {name}: the {who} latest RBC export was "
            f"taken as of {last} — any trade or "
            f"income from {gap[0].isoformat()} to {year_end} cannot be in "
            f"it and is missing from the {y} books. Export the account's "
            f"activity again (after Jan 31, {y + 1}, so the settlements "
            f"are in) and add the file.")
    if any(m.startswith('warning:') for m in out):
        return out
    posted = _date(y + 1, *parse_month_day(
        year_end_posting or DEFAULT_YEAR_END_POSTING)).isoformat()
    cover = max((a for _, a, ds, _ac in dated
                 if any(x and x <= year_end for x in ds)), default='')
    if not cover or cover >= posted:
        return out
    if listings is not None and not any(
            abs(li.position_on(year_end)) > 1e-9
            for per in listings.values() for li in per.values()):
        return out
    out.append(
        f"note: the account's RBC exports that hold {y} rows were taken "
        f"by {cover}; RBC posts {y}'s year-end book-cost adjustments "
        f"(notional distributions, a year-end return of capital — dated "
        f"{year_end}) only in the spring of {y + 1}, and an export "
        f"starting Jan 1, {y + 1} never holds them. Re-export {y} after "
        f"{posted} (the account's year_end_posting; keep both files: "
        f"overlapping downloads are "
        f"de-duplicated) or check the {rbc_terms(country)['cost']} "
        f"against {rbc_terms(country)['fund_slips'].format(y=y)}.")
    return out


def rbc_time(k: int) -> str:
    """09:30:00 plus `k` seconds — the intra-day ordinal as a time."""
    return (_BASE_TIME + timedelta(seconds=max(0, k))).strftime('%H:%M:%S')


# ---------------------------------------------------------- classification

def rbc_cash_move(r: 'RbcRow', name: str) -> Optional[Dict[str, Any]]:
    """The cash event of one 'cash' row (`classify_rbc_row`: a deposit, a
    withdrawal, a cash transfer — WIR, DEP, TFI ...) for the FX-on-cash
    ledger v2 (lib/cash_events, tax-logic CA-FX-07): a CASHMOVE, signed,
    dated by its settlement date; None for a row that moves no money or
    names no currency. `name`: the export as diagnostics show it. RBC's
    activity export carries no cash balance: a CASHBAL .tt line gives the
    ledger one to reconcile against. The parse builds the file's events
    from the rows it read (the finish, PLAN step 16 commit 7: once
    lib/cash_readers/rbc.py read the export a second time)."""
    from taxjson.lib.privacy import hash_broker_account
    from taxjson.lib import cash_lines as CL
    if abs(r.value) < 1e-9:
        return None
    cur = (r.currency or '').strip().upper()
    if not cur:
        return None
    acct = hash_broker_account(r.account) if r.account.strip() else ''
    desc = re.sub(r'\d', '#', (r.code or r.activity or '').strip())
    ev = CL.move(r.date, cur, r.value, settle=r.settle or r.date,
                 where=name, account=acct, desc=desc)
    ev['line_where'] = f"{name}:{r.line}"
    return ev


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


def _extract_words(symdesc: str, desc: str) -> str:
    """The words a suggested EXTRACT line names: the longest start (up to
    six words) of the security description that the row's Description —
    what EXTRACT matches — also carries; else the Description's first
    words."""
    from taxjson.lib.brokerages.base import extract_words_match
    words = (symdesc or '').split()
    for k in range(min(6, len(words)), 0, -1):
        cand = ' '.join(words[:k])
        if extract_words_match(cand, desc):
            return cand
    return ' '.join((desc or '').split()[:4])


# ------------------------------------------------------- account context
#
# `tjs run` hands every RBC export of one account to ONE
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
    return mask_id(acct, empty='(no Account column)')


def _squash(text: str) -> str:
    return ' '.join((text or '').split()).upper()


def _row_content_key(r) -> Tuple:
    """What makes two rows of two downloads the SAME broker row: every
    column except the position in the file (and the intra-day time
    derived from it)."""
    return (r.date, r.activity.strip().lower(), r.symbol.strip().upper(),
            _squash(r.symdesc), _squash(r.desc), r.currency, r.qty, r.price,
            r.value, r.settle)


def rbc_desc_side(desc: str) -> str:
    """The direction a trade's Description states: 'sell' or 'buy' from
    the verb the export ends it with ("NAME - Sell"); without one, from
    the Buy / Sell word in it (`_TRADE_DESC_RE`, what `classify_rbc_row`
    reads under an Activity it does not know), 'both' when it has the two;
    '' when it has neither."""
    return _desc_side_shown(desc)[0]


def _desc_side_shown(desc: str) -> Tuple[str, str]:
    """`rbc_desc_side` and how a message names what gave it: `ends
    "- Sell"` (the closing verb) or `has the word "Sell"` (a word that may
    be part of the security's name: the message says which, so it does
    not blame a name for a verb)."""
    m = _TRADE_DESC_VERB_RE.search(desc or '')
    if m:
        return m.group(1).lower(), f'ends "- {m.group(1)}"'
    words = sorted(set(_TRADE_DESC_RE.findall(desc or '')))
    if len(words) > 1:
        return 'both', 'has both "Buy" and "Sell"'
    if words:
        return words[0].lower(), f'has the word "{words[0]}"'
    return '', ''


def rbc_trade_side(r) -> str:
    """'sell' | 'buy' | '' for a row: the Activity when it is Buy or Sell;
    for a row classified a trade by its Description alone (an Activity
    this parser does not know, `classify_rbc_row`'s fallback), the
    direction the Description states (TJ-2383, TJ-5021: such a row was
    signed by its Quantity alone, so "NAME - Sell" with RBC's unsigned
    quantity was a purchase, then refused as a shifted column). '' where
    neither says (an exercise, an assignment, an expiry; a Description
    with both words, which `_refuse_trade_side` refuses)."""
    a = (r.activity or '').strip().lower()
    if a in ('buy', 'sell'):
        return a
    if r.cls == 'trade' and a not in ('exercise', 'assignment'):
        side = rbc_desc_side(r.desc)
        return side if side in ('buy', 'sell') else ''
    return ''


def _contract_code(desc: str) -> str:
    """'O' / 'C': the OPEN / CLOSE CONTRACT words RBC ends an option
    trade's Description with; '' when it has neither."""
    m = _RBC_OPEN_CLOSE_RE.search(desc or '')
    if not m:
        return ''
    return 'O' if m.group(1).upper() == 'OPEN' else 'C'


def _signed_qty(r) -> float:
    q = r.qty
    if rbc_trade_side(r) == 'sell' or r.cls == 'retraction':
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
    # The same moves per broker account (the Account column, normalized):
    # [(date, signed quantity)].
    by_account: Dict[str, List[Tuple[str, float]]] = field(
        default_factory=dict)

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

    def account_position_on(self, account: str, date: str) -> float:
        """Broker account `account`'s position at the end of `date`
        (two accounts booked together each split their own position:
        backlog TJ-0672). A row from an export with no Account column
        (filed under '') may be any account's: it counts for every named
        one, and a row of no named account reads the whole listing. The
        layouts mix across years, and a short sold in the older layout
        was no position at all for a split in the newer (TJ-1048)."""
        if not account:
            return self.position_on(date)
        return sum(q for acct in (account, '') if acct in self.by_account
                   for d, q in self.by_account[acct] if d <= date)

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
    # The days `_order_by_open_close` could not prove, each (date, series,
    # file index, the day's rows, their open/close order, the words for
    # it): left in the file's order; the parse states each row's time in
    # the open/close order as a fact (`open_close_time`) and the country's
    # reading of it is characterize/no_clock_order's.
    unproven: List[Tuple[str, str, int, List['RbcRow'], List['RbcRow'], str]] = \
        field(default_factory=list)
    emitted: bool = False
    # file → ids of its reorganization legs that belong to an event
    # booked from ANOTHER file of the account (the removal's file)
    foreign_legs: Dict[str, set] = field(default_factory=dict)
    # Reinvestment reversals paired across ALL of the account's files
    # (_plan_reinvest_reversals): ids of the REI rows a CANCEL cancels,
    # and of the CANCEL rows paired.
    rei_drop: set = field(default_factory=set)
    rei_paired: set = field(default_factory=set)

    def rows(self, key: str) -> List['RbcRow']:
        dup = self.duplicate_of.get(key, {})
        return [r for r in self.exports[key].rows if r.order not in dup]

    def emit(self) -> None:
        if self.emitted:
            return
        self.emitted = True
        for m in self.messages:
            emit_line(m)

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


def temp_symbol_ticker(ctx: RbcAccountContext, r) -> Optional[str]:
    """The listed ticker a row under a temporary code is booked on, by
    its security name; None when no row of the account names it."""
    for key in (rbc_norm_company(rbc_row_company(r)),
                rbc_rights_key(r.desc), rbc_rights_key(r.symdesc)):
        hit = ctx.ticker_for_name(key, r.date) if key else None
        if hit:
            return hit
    return None


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
    for k in files:
        # One export spanning several RBC accounts (re-audit A2-0025):
        # every row lands in the one taxjson account the file sits in,
        # and RBC writes no account type to tell a TFSA from a margin
        # account. Across files is the normal layout (one export per
        # RBC account, several taxable accounts pooled) — not flagged.
        accts = sorted({_norm_account(r.account)
                        for r in exports[k].rows if r.account.strip()})
        if len(accts) > 1 and getattr(helper, 'combined_broker_accounts',
                                      False):
            # combined_broker_accounts = true (owner decision): a NOTE.
            # RBC writes no account type, so on a sheltered label the
            # plans cannot be checked — refused.
            accts = [f"#{i} {_mask_account(a)}"
                     for i, a in enumerate(accts, 1)]
            if getattr(helper, 'account_taxable', None) is False:
                raise combined_accounts_refusal(
                    name_of[k], 'RBC', accts,
                    "an RBC export does not say which plan each account "
                    "is")
            ctx.messages.append(combined_accounts_note(name_of[k], 'RBC',
                                                       accts))
        elif len(accts) > 1:
            accts = [f"#{i} {_mask_account(a)}"
                     for i, a in enumerate(accts, 1)]
            ctx.messages.append(
                f"warning: ATTENTION: {name_of[k]}: the export holds rows "
                f"of {len(accts)} RBC accounts ({', '.join(accts)}) — "
                f"every row is booked to ONE taxjson account. That is "
                f"right only when they are one tax entity (two taxable "
                f"accounts of yours); export a sheltered (tax-"
                f"advantaged) account separately into its own "
                f"inputs/<account>/.")

    live = [(fi, r) for fi, k in enumerate(files) for r in ctx.rows(k)]
    chrono = sorted(live, key=lambda x: (x[1].date, x[1].k, x[0],
                                         -x[1].order))

    # Option code → contract: its chronologically FIRST description in
    # ANY of the account's files (RBC re-describes a contract over time,
    # "CALL .ABC" in one export, "CALL .ABC.B" in a later one; keying each file on its own
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
    # in another file are consumed there. A MERGER spanning two files is
    # taxjson-corp-actions' too: it pairs leftover merger legs across the
    # account's statements (re-audit A2-0214), so the parser counts both
    # legs as its own (re-audit A2-0271: they were two UNBOOKED legs and
    # `run --strict` refused).
    _file_of = {id(r): k for k in files for r in ctx.rows(k)}
    _acct = pair_rbc_reorganizations(
        [r for k in files for r in ctx.rows(k)],
        {id(r): (k,) for k in files for r in ctx.rows(k)})
    _events: Dict[str, list] = {k: [] for k in files}
    _unm: Dict[str, list] = {k: [] for k in files}
    _unm_cil: Dict[str, list] = {k: [] for k in files}
    for ev in _acct.events:
        legs = [x for x in [ev.removal, ev.receipt] + list(ev.cil)
                if x is not None]
        homes = {_file_of[id(x)] for x in legs}
        anchor = _file_of[id(ev.removal)]
        _events[anchor].append(ev)
        for x in legs:
            if _file_of[id(x)] != anchor:
                ctx.foreign_legs.setdefault(_file_of[id(x)], set()).add(id(x))
    for x in _acct.unmatched:
        _unm[_file_of[id(x)]].append(x)
    for x in _acct.unmatched_cil:
        _unm_cil[_file_of[id(x)]].append(x)
    # A spin-off's cash in lieu: taxjson-corp-actions books it with the
    # spin-off's election (CA-CORP-05); each CIL row is consumed in its
    # own file.
    _spin_cil: Dict[str, list] = {k: [] for k in files}
    for sp, cils in _acct.spinoff_cil:
        for c in cils:
            _spin_cil[_file_of[id(c)]].append((sp, [c]))
    adjusts = []
    for k in files:
        ctx.pairings[k] = RbcReorgPairing(events=_events[k],
                                          unmatched=_unm[k],
                                          unmatched_cil=_unm_cil[k],
                                          spinoff_cil=_spin_cil[k])
        # An option adjustment's new code inherits the old contract
        # unless its own trade rows describe another — resolved in date
        # order across files so a later file's close finds it.
        adjusts += [ev for ev in ctx.pairings[k].events
                    if ev.kind == 'option_adjust']
    _inherit_adjusted_codes(ctx, helper, adjusts)
    _order_by_open_close(ctx, helper, live)     # the series now named

    # Security name → ticker, with dates (temporary-code resolution).
    # The name is `rbc_row_company`'s: with no Symbol Description column
    # (the demo layout) a trade's own Description, else a name change
    # under a temporary code found no ticker and stranded the old pool.
    for fi, r in chrono:
        if not r.symbol or rbc_is_temp_symbol(r.symbol) \
                or rbc_is_option_code(r.symbol):
            continue
        for key in (rbc_norm_company(rbc_row_company(r)),
                    rbc_rights_key(r.desc),
                    rbc_rights_key(r.symdesc)):
            if key:
                ent = ctx.names.setdefault(key, [])
                e = (r.date, r.cls == 'reorg', r.symbol)
                if e not in ent:
                    ent.append(e)

    # Listings: which (symbol, currency) lines the account actually
    # trades/holds, under which security names, with a position timeline.
    # A split moves the position too (its signed Quantity is the change,
    # long or short): without it a later split on a short read the
    # pre-split count and could be taken for a reverse split (backlog
    # TJ-0665).
    for fi, r in chrono:
        if (r.cls not in _POSITION_CLASSES and r.cls != 'split') \
                or not r.symbol or not r.currency:
            continue
        if rbc_is_temp_symbol(r.symbol) or rbc_is_option_code(r.symbol) \
                or helper._row_occ(r):
            continue
        li = ctx.listings.setdefault(r.symbol, {}).setdefault(
            r.currency, _Listing(r.symbol, r.currency))
        company = rbc_row_company(r)
        nm = rbc_norm_company(company)
        if nm:
            li.names.setdefault(nm, ' '.join(company.split()))
        li.events.append((r.date, r.k, fi, _signed_qty(r)))
        li.by_account.setdefault(_norm_account(r.account), []).append(
            (r.date, _signed_qty(r)))
    # A split under a temporary code is booked on the ticker its name
    # resolves to (_resolve_temp), so it moves that listing's position:
    # left out, the next split on a short read a stale count and was
    # taken for a reverse split (backlog TJ-1089). It opens no listing.
    for fi, r in chrono:
        if r.cls != 'split' or not r.currency \
                or not rbc_is_temp_symbol(r.symbol) or helper._row_occ(r):
            continue
        li = ctx.listings.get(temp_symbol_ticker(ctx, r) or '', {}).get(
            r.currency)
        if li is not None:
            li.events.append((r.date, r.k, fi, _signed_qty(r)))
            li.by_account.setdefault(_norm_account(r.account), []).append(
                (r.date, _signed_qty(r)))
    for per in ctx.listings.values():
        for li in per.values():
            li.finish()
    _detect_ticker_changes(ctx, helper)
    _plan_reinvest_reversals(ctx, chrono)
    return ctx


def _plan_reinvest_reversals(ctx: RbcAccountContext, chrono) -> None:
    """Each reversed reinvestment (REI with Quantity < 0, Value > 0)
    cancels the latest REI of the same symbol, units and cash on or
    before its date in ANY of the account's files (re-audit A2-1044 /
    A2-1048): the original in December's export and its CANCEL in
    January's was refused, and with an overlapping download the result
    depended on file order (the full file's REI was the skipped copy).
    The original must be in the same broker account (the Account
    column): one account's CANCEL consumed another account's REI
    pooled under the same taxjson account, and both dropped out
    (backlog TJ-0518)."""
    opened: Dict[tuple, List] = {}
    for _fi, r in chrono:
        if r.cls != 'reinvest' or abs(r.qty) < 1e-12 \
                or abs(r.value) < 0.005 or (r.qty > 0) == (r.value > 0):
            continue
        key = (_norm_account(r.account), r.symbol.strip().upper(),
               r.currency, M.units(abs(r.qty), 6), M.units(abs(r.value), 2))
        if r.qty > 0:
            opened.setdefault(key, []).append(r)
            continue
        cands = [o for o in opened.get(key, []) if o.date <= r.date]
        if cands:
            hit = cands[-1]
            opened[key].remove(hit)
            ctx.rei_drop.add(id(hit))
            ctx.rei_paired.add(id(r))


# RBC posts an option's expiry row the next business day: exports that
# reach a week past a contract's expiry would hold it.
_EXPIRY_POSTED_DAYS = 7


def _inherit_adjusted_codes(ctx: RbcAccountContext, helper: Any,
                            adjusts: List[Any]) -> None:
    """An option adjustment's new code inherits the old contract unless
    its own trade rows describe another, in date order across files."""
    for ev in sorted(adjusts, key=lambda e: (e.removal.date, e.removal.k)):
        rem, rc = ev.removal, ev.receipt
        old = ctx.occ_by_code.get(rem.symbol) or helper._row_occ(rem)
        if old and rc.symbol not in ctx.occ_own:
            ctx.occ_by_code[rc.symbol] = old


def _option_row_symbol(ctx: RbcAccountContext, helper: Any,
                       r: RbcRow) -> Optional[str]:
    """The option series a row books, named as `_build_trade` names it
    (the account's code map, else the row's own text); None for a row
    that is no option's."""
    occ = ctx.occ_by_code.get(r.symbol) if rbc_is_option_code(r.symbol) \
        else None
    occ = occ or helper._row_occ(r)
    return helper.apply_currency_suffix(occ, r.currency) if occ else None


def _option_series(ctx: RbcAccountContext, helper: Any,
                   live: List[Tuple[int, RbcRow]]
                   ) -> Tuple[Dict[Tuple[str, str], List[Tuple[int, RbcRow]]],
                              Set[int]]:
    """The account's option rows by (broker account, series), and the
    rows the run books only once although two exports hold them: with no
    Account column `_plan_overlaps` matches nothing, and the run's
    cross-file dedup drops a row whose id a row of another export has
    (an RBC row's id is its content and its intra-day time, `k`)."""
    series: Dict[Tuple[str, str], List[Tuple[int, RbcRow]]] = {}
    blank: Dict[Tuple[int, Tuple[Any, ...], int], List[RbcRow]] = {}
    for fi, r in live:
        sym = _option_row_symbol(ctx, helper, r)
        if not sym:
            continue
        series.setdefault((_norm_account(r.account), sym), []).append(
            (fi, r))
        if not _norm_account(r.account):
            blank.setdefault((fi, _row_content_key(r), r.k), []).append(r)
    seen: Dict[Tuple[Tuple[Any, ...], int], int] = {}
    copies: Set[int] = set()
    for (_fi, key, k), rs in sorted(blank.items(), key=lambda x: x[0][0]):
        have = seen.get((key, k), 0)
        copies.update(id(r) for r in rs[:have])
        seen[(key, k)] = max(have, len(rs))
    return series, copies


def _export_span(exp: RbcExport, dates: List[str]) -> Tuple[str, str]:
    """The days one export holds (`dates`: its rows' of the broker
    account): its "Date Range:" line, the end capped by its "Activity
    Export as of" date (a range asked for need not have passed); else its
    first row to its as-of date; else its first row to its last. An as-of
    date says only that nothing later is in the file."""
    lo, hi = exp.date_range
    if lo and hi:
        return lo, min(hi, exp.as_of) if exp.as_of else hi
    return min(dates), max(max(dates), exp.as_of)


def _stated_end(ctx: RbcAccountContext, acct: str, end: str
                ) -> Optional[Tuple[str, str]]:
    """When no row of the broker account at or after `end`, in an export
    whose span holds `end`, proves that the exports reach it: (that
    export, the note's words for the stated date it is taken to hold
    everything to). An export asked for a shorter range and downloaded
    later, or a range asked for ahead of the download, holds less than
    it says (backlog TJ-5363). None when a row proves it."""
    best: Optional[Tuple[str, str, str]] = None
    for name in ctx.files:
        exp = ctx.exports[name]
        dates = [r.date for r in exp.rows if _norm_account(r.account) == acct]
        if not dates:
            continue
        lo, hi = _export_span(exp, dates)
        if not lo <= end <= hi:
            continue
        if max(dates) >= end:
            return None
        if best is not None and hi <= best[1]:
            continue
        if exp.date_range[0] and hi == exp.date_range[1]:
            what = f"Date Range end {hi}" + (
                '' if exp.as_of else
                ': if the export was taken before then, add the rest')
        else:
            what = f"as-of date {hi}" + (
                '' if exp.date_range[0] else
                ': if the export was asked for a shorter range, add the rest')
        best = (name, hi, f"the rows end {max(dates)}; taken as holding "
                          f"everything to %s {what}")
    return (best[0], best[2]) if best else None


def _stated_words(stated: Optional[Tuple[str, str]], name: str) -> str:
    """The note's clause for `_stated_end`'s finding, said from export
    `name` ('its', or the other export's file name); '' when a row
    proves the end."""
    if not stated:
        return ''
    src, words = stated
    whose = 'its' if src == name else f"{Path(src).name}'s"
    return f" ({words % whose})"


def _next_session(iso: str) -> str:
    """The first day after `iso` that the TSX or the NYSE trades."""
    from taxjson.lib.market_calendar import is_trading_day
    d = datetime.strptime(iso, '%Y-%m-%d').date() + timedelta(days=1)
    while not (is_trading_day(d, 'CAD') or is_trading_day(d, 'USD')):
        d += timedelta(days=1)
    return d.isoformat()


def _covered(ctx: RbcAccountContext, acct: str, start: str,
             end: str) -> bool:
    """The account's exports hold every day from `start` to `end`: one
    export, or exports that overlap or follow on (one begins by the next
    trading session after another ends: Dec 31 then Jan 1 or Jan 2), each
    an export holding rows of the broker account, over `_export_span`.
    A session between two exports that neither holds is a gap: rows the
    data lacks may sit there."""
    spans = []
    for name in ctx.files:
        exp = ctx.exports[name]
        dates = [r.date for r in exp.rows if _norm_account(r.account) == acct]
        if dates:
            spans.append(_export_span(exp, dates))
    reach = ''
    for lo, hi in sorted(spans):
        if lo <= (max(_next_session(reach), start) if reach else start):
            reach = max(reach, hi)
    return reach >= end


def _order_by_open_close(ctx: RbcAccountContext, helper: Any,
                         live: List[Tuple[int, RbcRow]]) -> None:
    """RBC writes no time: `read_rbc_rows` numbers a day's rows from the
    bottom of the export, taking it as newest first. An option's own
    OPEN / CLOSE CONTRACT words can say otherwise: a CLOSE buy-back read
    before the same day's OPEN write it closes was the close of nothing
    held (backlog TJ-5348). Per broker account and series, on a day whose
    rows of it are Buy / Sell rows of one export, an OPEN row of the
    other direction goes just before a CLOSE row that the position
    before it cannot back, as many as the close needs
    (`_open_close_moves`); otherwise the file order stays. Every reader
    then sees one order (lib/unbacked_close: on a day with no clock the
    day's own OPEN rows back a CLOSE first; backlog TJ-5349).

    What was held before the data is unknown, so the order is proven only
    where the data holds the series' life from the first day moved to its
    end (`_order_proven`): its rows net to nothing, the exports cover that
    day through the series' expiry row or a week past its expiry, and a
    replay from nothing held backs every CLOSE. Proven, a day reordered
    is the export's note, naming the rows moved, and the stated date the
    end rests on when no row proves it (`_stated_end`). Not proven, the
    day is reordered all the same and the words are an ATTENTION: the
    order rests on the day's own rows, and it names the export to add
    (backlog TJ-5363). If the CLOSE did close a contract opened before
    the files, the day's OPEN is still open and its own close is then
    the one the position cannot back."""
    from taxjson.lib.option_symbols import parse_option_expiry
    series, copies = _option_series(ctx, helper, live)
    notes: List[Tuple[str, str, int, str, bool]] = []
    for (acct, sym), rows in sorted(series.items()):
        plans = _series_plans(rows, copies)
        if not plans:
            continue
        booked = [(fi, r) for fi, r in rows if id(r) not in copies]
        expired = [r.date for _fi, r in booked if r.cls == 'expiry']
        expiry = parse_option_expiry(sym) or ''
        end = min(expired) if expired else expiry and (
            datetime.strptime(expiry, '%Y-%m-%d')
            + timedelta(days=_EXPIRY_POSTED_DAYS)).strftime('%Y-%m-%d')
        proven = _order_proven(ctx, acct, booked, plans, end)
        stated = _stated_end(ctx, acct, end) if proven else None
        for d, (fi, day, order, moves) in sorted(plans.items()):
            words = '; '.join(_moved_words(c, t) for c, t in moves)
            if not proven:
                ctx.unproven.append((d, sym, fi, day, order, words + _unproven_words(
                    sym, expiry, end, order, moves)))
                continue
            _renumber(day, order)
            notes.append((d, sym, fi, words + _stated_words(stated, ctx.files[fi])))
    for d, sym, fi, words in sorted(notes):
        ctx.exports[ctx.files[fi]].notes.append(_ORDER_SAID % (sym, d, words))


_ORDER_SAID = ("rows of %s on %s put in the broker's open/close order "
               "(RBC writes no time): %s")


def _unproven_of(ctx: RbcAccountContext, key: str) -> Tuple[Dict[int, int], List[str]]:
    """Of the export `key`, the days whose open/close order no export
    proves (`ctx.unproven`): each row's intra-day number in that order
    (by id; the rows keep the file's) and the words for each day. Neutral
    facts: which books read the day in that order is the country's
    (characterize/no_clock_order; backlog TJ-5438)."""
    slots: Dict[int, int] = {}
    said: List[str] = []
    for d, sym, fi, day, order, words in sorted(ctx.unproven, key=lambda x: x[:3]):
        if ctx.files[fi] == key:
            slots.update(_slots(day, order))
            said.append(_ORDER_SAID % (sym, d, words))
    return slots, said


def _order_proven(ctx: RbcAccountContext, acct: str,
                  booked: List[Tuple[int, RbcRow]], plans: Dict[str, Any],
                  end: str) -> bool:
    """The data holds the series' life from the first day moved to its end
    (backlog TJ-5348): its booked rows net to nothing, the account's
    exports cover that day through `end` (`_covered`), and replayed from
    nothing held in the new order every CLOSE finds what it closes
    (`_backed_from_zero`)."""
    return (abs(sum(_signed_qty(r) for _fi, r in booked)) <= 1e-9
            and bool(end) and _covered(ctx, acct, min(plans), end)
            and _backed_from_zero(booked, plans))


def _unproven_words(sym: str, expiry: str, end: str, order: List[RbcRow],
                    moves: List[Tuple[RbcRow, List[RbcRow]]]) -> str:
    """The ATTENTION's second sentence for a day reordered on its own rows
    alone: what the order rests on and the export to add. A day with more
    CLOSE rows of one direction than its OPEN rows back: which of them
    the OPEN rows back is the file's order, said too, when the day holds
    several such CLOSE rows (the others are read as closing a contract
    opened before the files)."""
    upto = (f"through {end} (a week past its {expiry} expiry, or its "
            f"expiry row)" if expiry and end else "through its expiry")
    several = any(
        sum(1 for r in order if _contract_code(r.desc) == 'C'
            and _signed_qty(r) * _signed_qty(c) > 0) > 1
        and sum(abs(_signed_qty(r)) for r in order if _contract_code(r.desc) == 'C'
                and _signed_qty(r) * _signed_qty(c) > 0)
        > sum(abs(_signed_qty(o)) for o in order if _contract_code(o.desc) == 'O'
              and _signed_qty(o) * _signed_qty(c) < 0) + 1e-9
        for c, _t in moves)
    return ((". The day's OPEN rows back fewer contracts than its CLOSE "
             "rows close: which CLOSE row they back is the file's order, "
             "and the rest is read as closing a contract opened before "
             "your files" if several else "")
            + f". The order rests on that day's own rows: the account's "
            f"exports do not hold {sym} from that day {upto} with nothing "
            f"left open, so a contract opened before your files could be "
            f"what the CLOSE row closed. Add the export covering those "
            f"dates; until then the books read the day's OPEN row as what "
            f"it closed (the close of any contract still open is then "
            f"reported by hand).")


def _series_plans(rows: List[Tuple[int, RbcRow]], copies: Set[int]
                  ) -> Dict[str, Tuple[int, List[RbcRow], List[RbcRow],
                                       List[Tuple[RbcRow, List[RbcRow]]]]]:
    """{date: (its export, the day's rows in the readers' order, their
    new order, the moves)} for each day of one series whose rows are Buy
    / Sell rows of one export and are put in the open/close order, from
    the position the rows before the day hold."""
    days: Dict[str, List[Tuple[int, RbcRow]]] = {}
    for fi, r in rows:
        days.setdefault(r.date, []).append((fi, r))
    plans = {}
    held = 0.0                          # the position before the day
    for d in sorted(days):
        day = days[d]
        if len({fi for fi, _r in day}) == 1 and all(
                r.cls == 'trade' and rbc_trade_side(r) in ('buy', 'sell')
                for _fi, r in day):
            plan = _open_close_moves([r for _fi, r in day], held)
            if plan:
                plans[d] = (day[0][0],) + plan
        held += sum(_signed_qty(r) for _fi, r in day if id(r) not in copies)
    return plans


def _open_close_moves(rows: List[RbcRow], held: float
                      ) -> Optional[Tuple[List[RbcRow], List[RbcRow],
                                          List[Tuple[RbcRow, List[RbcRow]]]]]:
    """One day's rows of one series (one export, Buy / Sell rows), from
    the position `held` before the day: walked in the readers' order (the
    intra-day number, a tie the file's), each CLOSE row the position
    cannot back takes the OPEN rows of the other direction after it, in
    their order, until they back it (a row larger than the rest of the
    close moves whole), and they go just before it; nothing else moves.
    (the rows in the readers' order, the new order, [(the CLOSE row, the
    OPEN rows put before it)]), or None when nothing moves."""
    rows = sorted(rows, key=lambda r: (r.k, r.order))
    order = list(rows)
    moves: List[Tuple[RbcRow, List[RbcRow]]] = []
    pos, i = held, 0
    while i < len(order):
        r = order[i]
        q = _signed_qty(r)
        if _contract_code(r.desc) == 'C':
            need = abs(q) - max(-pos if q > 0 else pos, 0.0)
            take: List[RbcRow] = []
            for o in order[i + 1:]:
                if need <= 1e-9:
                    break
                if _contract_code(o.desc) == 'O' and _signed_qty(o) * q < 0:
                    take.append(o)
                    need -= abs(_signed_qty(o))
            if take:
                moves.append((r, take))
                moved = {id(o) for o in take}
                order = order[:i] + take + [o for o in order[i:]
                                            if id(o) not in moved]
                continue                # walk the moved rows from here
        pos += q
        i += 1
    return (rows, order, moves) if moves else None


def _backed_from_zero(booked: List[Tuple[int, RbcRow]],
                      plans: Dict[str, Tuple[int, List[RbcRow],
                                             List[RbcRow], Any]]) -> bool:
    """The series replayed from nothing held, the planned days in their
    new order: True when every CLOSE row (an expiry or an assignment
    closes too) finds the position it closes."""
    rank = {id(r): i for _fi, _day, order, _m in plans.values()
            for i, r in enumerate(order)}
    pos = 0.0
    for _fi, r in sorted(booked, key=lambda x: (
            x[1].date, rank.get(id(x[1]), x[1].k), x[0], x[1].order)):
        q = _signed_qty(r)
        if (_contract_code(r.desc) == 'C'
                or r.cls in ('expiry', 'assignment')) \
                and (-pos if q > 0 else pos) < abs(q) - 1e-9:
            return False
        pos += q
    return True


def _slots(rows: List[RbcRow], order: List[RbcRow]) -> Dict[int, int]:
    """The day's intra-day numbers in the new order, by row id: its own
    slots, strictly rising (a day of one common time gets its own, k 0,
    1, ...)."""
    out: Dict[int, int] = {}
    slots = [r.k for r in rows]
    k = slots[0] - 1
    for s, r in zip(slots, order):
        k = max(s, k + 1)
        out[id(r)] = k
    return out


def _renumber(rows: List[RbcRow], order: List[RbcRow]) -> None:
    """The day's rows given those numbers (`_slots`)."""
    by_id = _slots(rows, order)
    for r in order:
        r.k = by_id[id(r)]


def _moved_words(close: RbcRow, taken: List[RbcRow]) -> str:
    """'the OPEN CONTRACT sale of 3 before the CLOSE CONTRACT purchase of
    3' (the note's words for one move)."""
    def side(r: RbcRow) -> str:
        return 'sale' if _signed_qty(r) < 0 else 'purchase'
    qty = [f"{abs(o.qty):g}" for o in taken]
    opens = (f"{side(taken[0])} of {qty[0]}" if len(qty) == 1 else
             f"{side(taken[0])}s of {', '.join(qty[:-1])} and {qty[-1]}")
    return (f"the OPEN CONTRACT {opens} before the CLOSE CONTRACT "
            f"{side(close)} of {abs(close.qty):g}")


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
                # The parser does not decide; the run's cross-file
                # dedup does, and says so (re-audit A2-1051: 'NOTHING
                # was de-duplicated' contradicted its line).
                ctx.messages.append(
                    f"note: {name_of[a]} and {name_of[b]} share {common} "
                    f"identical row(s) on the same dates but have no Account "
                    f"column, so the RBC parser cannot tell an overlapping "
                    f"re-download of ONE account from two accounts and "
                    f"passes every row on — the run's cross-file "
                    f"de-duplication decides (its 'dedup:' line says what "
                    f"it did). Re-export with the Account column to be "
                    f"sure.")


def _detect_ticker_changes(ctx: RbcAccountContext, helper) -> None:
    """A ticker change RBC applied WITHOUT a reorganization row
    (e.g. ABC → ABD): the old symbol stops with shares still open
    and a new symbol with the same security name (`rbc_row_company`)
    and currency goes
    SHORT by no more than those shares (its first row a sale, or a buy
    followed by a larger sale). The export carries no CUSIP, so this is
    not certain enough to merge silently: an ATTENTION line (on the run
    console — re-audit A2-0096 / A2-0613) with the ticker.map line that
    merges them, on the first line so the console shows it."""
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
                reach = rename_reach(a.events, b.events)
                if reach is None:
                    continue
                open_a, low_b, when_b, first_b = reach
                seen.add((a.symbol, b.symbol, cur))
                sa = helper.apply_currency_suffix(a.symbol, cur)
                sb = helper.apply_currency_suffix(b.symbol, cur)
                if ticker_map_joins(sa, sb):
                    continue    # ticker.map already pools them (A2-1056)
                fb = ctx.exports[ctx.files[b.events[0][2]]].path.name
                declared = declared_rename_where(sa, sb)
                if declared:
                    # A .tt RENAME line already books it: a note.
                    ctx.messages.append(answered_rename_note(
                        fb, "RBC", f"{a.symbol} ({cur})", b.symbol,
                        declared))
                    continue
                how = rename_how(b.first, low_b, when_b, first_b)
                ctx.messages.append(
                    f"warning: ATTENTION: {fb}: RBC symbol {a.symbol} "
                    f"({cur}) looks renamed to {b.symbol} — if they are "
                    f"one security, add to a .tt file of this account:  "
                    f"RENAME {b.first} {sa} {sb}  "
                    f"— {a.symbol} stops on {a.last} with {open_a:g} "
                    f"share(s) still open, and {b.symbol} (same security "
                    f"name {a.names[nm]!r}) {how}. That is a "
                    f"ticker change RBC booked without a reorganization "
                    f"row: as exported it is a stranded long {sa} and a "
                    f"short {sb}.")


class _RbcParse(NamedTuple):
    """What `read` hands the loop and the finish of one export (the
    account context is `_ctx`, the builders' too): the file's key in it,
    the duplicates another export already holds, the rows `read` returns,
    the reorganization pairing's output by its removal's order, the legs
    the pairing booked, and what corp-actions owns (the spin-off
    cash-in-lieu rows, the merger legs). Reset by `read`, released by
    `release` whatever happened."""
    key: str
    dups: Dict[str, str]
    rows: list
    reorg_out: Dict[int, List[Dict[str, Any]]]
    leg_done: set
    spinoff_cil: int
    owned_by_corp_actions: int


# ------------------------------------------------------------------ parser

class RbcBrokerage(BaseBrokerage):
    DEFAULT_ACCOUNT = "RBC"
    SHORT_MARKS = "description"         # "… SHORT. …" / "… COVER SHORT. …"
    NO_CLOCK = "file"                   # no time column: a day's order is the file's

    def _option_description_patterns(self):
        return _RBC_OPTION_PATTERNS

    def parse_option_from_description(self, desc):
        return self.strict_option_from_description(desc)

    # ----------------------------------------------------------- warnings
    def _warn(self, msg: str, *, lint: bool = False,
              unbooked: bool = False, attention: bool = False) -> None:
        """`unbooked`: a real row the parser did NOT book. The
        'warning: UNBOOKED:' prefix is what `tjs run` echoes to the
        console and what `run --strict` refuses on — a lint finding
        alone reached only the .sum banner, and run never passes --lint
        (audit S016-00 / S064-17). `attention`: booked, but on a guess
        or with money the books leave out (a listing, a temporary code,
        a notional distribution's income) — the 'warning: ATTENTION:'
        prefix `tjs run` prints on the console (re-audit A2-0005,
        A2-0007, A2-0612; owner decision S065-12: not an error)."""
        tag = ('UNBOOKED: ' if unbooked
               else 'ATTENTION: ' if attention else '')
        emit_line(f"warning: {tag}{self._fname}: {msg}")
        if lint or unbooked or attention:
            self.lint_findings.append(msg)

    def _note(self, msg: str) -> None:
        emit_line(f"note: {self._fname}: {msg}")

    # Set by taxjson-brokerage (see `prepare_files`) to share identity
    # maps across all of an account's RBC exports; None = this file alone.
    account_context: Optional[RbcAccountContext] = None
    # The project's country, for the wording of the notes only (set by
    # taxjson-brokerage --country; None = neutral words). See RBC_TERMS.
    country: Optional[str] = None

    @property
    def _terms(self) -> Dict[str, str]:
        return rbc_terms(self.country)

    @classmethod
    def prepare_files(cls, paths, combined=False, taxable=None, *, tax_year=None,
                      symbol_codes=None, project=None) -> RbcAccountContext:
        """Read ALL of one account's RBC exports once and build the
        identity maps every per-file parse shares (market currency per
        symbol, option code → contract, name → ticker) plus the overlap
        plan for re-downloads. Account-level warnings print here, once.
        `combined` / `taxable`: the label's combined_broker_accounts and
        type (taxjson-brokerage)."""
        helper = cls()
        helper.combined_broker_accounts = bool(combined)
        helper.account_taxable = taxable
        ctx = build_rbc_account_context(list(paths), helper=helper)
        ctx.emit()
        return ctx

    @staticmethod
    def coverage_messages(ctx: 'RbcAccountContext', year: int,
                          today=None,
                          country: Optional[str] = None,
                          year_end_posting: Optional[str] = None
                          ) -> List[str]:
        """The account's coverage findings for tax year `year` (see
        rbc_coverage_messages); taxjson-brokerage prints them when
        `tjs run` passes the project year (--tax-year)."""
        return rbc_coverage_messages(
            [(ctx.exports[k].path.name, ctx.exports[k].as_of,
              [r.date for r in ctx.exports[k].rows],
              {_norm_account(r.account) for r in ctx.exports[k].rows
               if r.account.strip()})
             for k in ctx.files],
            year, ctx.listings, today=today, country=country,
            year_end_posting=year_end_posting)

    def read(self, path: Path) -> list:
        path = Path(path)
        self._fname = path.name
        self._spinoffs = 0
        self.lint_findings: List[str] = []
        self.zero_tx_reason = None
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
        self._rbc_accounts = {r.account.strip() for r in rows
                              if r.account.strip()}
        # (the days no export proves: facts for characterize/no_clock_order)
        self._open_close_k, self.open_close_days = _unproven_of(ctx, key)
        for n in exp.notes:
            self._note(n)
        for n in exp.attentions:
            self._warn(n, attention=True)
        # The rows this read saw and the loop will not (the template adds
        # every row it iterates): the footers, the duplicates another
        # download holds; each counted as a non-event here.
        self._rows_seen += exp.n_footers + len(dups)
        for _ in range(exp.n_footers):
            self.count_nonevent(_NONEVENT_CLASSES['footer'])
        for other in dups.values():
            self.count_nonevent(f"row already in the overlapping download "
                                f"{other}")
        # A dropped copy's cash is this file's event too (the ledger keeps
        # one of the two, lib/cash_events dedup_across_files).
        for r in exp.rows:
            if r.order in dups and r.cls == 'cash':
                self.note_cash(r)

        # Identity maps shared by every file of the account.
        self._occ_by_code = ctx.occ_by_code
        self._occ_own = ctx.occ_own
        self._untraded_income: Dict[str, set] = {}
        self._listing_warned: set = set()
        self._usd_units_warned: set = set()
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
            if ev.removal.account.strip():      # stamped once, where they are made
                for tx in out:
                    tx.setdefault('broker_account', ev.removal.account.strip())
            anchor = ev.removal.order
            reorg_out.setdefault(anchor, []).extend(out)
        leg_done |= ctx.foreign_legs.get(key, set())
        spinoff_cil = 0
        for _sp, cils in pairing.spinoff_cil:
            for c in cils:
                leg_done.add(id(c))
                spinoff_cil += 1
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

        # What the loop and the finish read of this export (the parse
        # template calls them with the row alone); cleared by finish.
        self._rbc = _RbcParse(key, dups, rows, reorg_out, leg_done,
                              spinoff_cil, owned_by_corp_actions)
        return rows

    def classify(self, r) -> Outcome:
        """What the template does with the row: a leg already booked with
        its partner is consumed (its rows the pairing's), a non-event class
        counted, an unmatched reorganization or cash-in-lieu skipped (warned
        in read), a spin-off left to taxjson-corp-actions, an unknown row
        skipped and warned when it moves shares or cash; the rest build. A
        cash row (a non-event) is noted for the file's cash events first."""
        st = self._rbc
        cls = r.cls
        if cls == 'cash':
            self.note_cash(r)           # its event the finish's (cash_events)
        if id(r) in st.leg_done:
            return Outcome.consumed(st.reorg_out.get(r.order, ()))
        if cls in _NONEVENT_CLASSES:
            return Outcome.nonevent(_NONEVENT_CLASSES[cls])
        if cls in ('reorg', 'cil'):
            # Unmatched legs / CIL: warned above, nothing booked.
            return Outcome.reported(f"unmatched {cls} row (see warning)")
        if cls == 'spinoff':
            # A spin-off needs a TAX ELECTION (s.86.1 or an FMV
            # dividend in kind): owned by taxjson-corp-actions, which
            # prompts for it — booking the shares here at $0 cost was
            # silently choosing neither.
            self._spinoffs += 1
            return Outcome.consumed()
        if cls == 'unknown':
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
            else:
                return Outcome.skip(f"activity {r.activity.strip() or '?'}")
            # Warned UNBOOKED above: counted, not listed again (TJ-2401).
            return Outcome.reported(f"activity {r.activity.strip() or '?'}")
        return Outcome.build()

    def build(self, r) -> "List[Dict[str, Any]] | Outcome":
        """The row's schema rows (_dispatch), each stamped with the row's
        broker account, its description when the builder gave none and
        the overlap tag; an expiry is kept for the template's settlement
        clamp (`_expiries`). A row warned UNBOOKED while building is the
        dispatcher's Outcome, passed to the template."""
        out = self._dispatch(r)
        if isinstance(out, Outcome):
            return out
        tag = self._extra_tag.get(r.order)
        for tx in out:
            if id(r) in self._open_close_k:
                tx['open_close_time'] = rbc_time(self._open_close_k[id(r)])
            # The row's broker account (the Account column): cross-
            # file dedup never collapses two accounts' identical
            # rows (audit A2-0008, A2-0296).
            if r.account.strip():
                tx.setdefault('broker_account', r.account.strip())
            if not tx.get('description') and r.symdesc.strip():
                # A blank Description falls back to the Symbol
                # Description, so a name-keyed --security-overrides
                # rule reaches the row (audit S065-01).
                tx['description'] = r.symdesc.strip()
            if tag:
                tx['description'] = f"{tx.get('description') or ''} {tag}"
            if tx.pop('_expiry', False):
                self._expiries.append(tx)
        return out

    @builds("assign")
    def finish(self, transactions: List[Dict[str, Any]], path: Path
               ) -> List[Dict[str, Any]]:
        """After the loop: the reinvestment reversals and warrant exercises
        paired, the notes on what corp-actions owns, the zero-rows reason,
        the untraded income, the emitted symbols, the file's cash events
        (`cash_events`, one per cash row noted: `rbc_cash_move`; the
        settlement clamp, the split fills and the skip summary are the
        template's)."""
        st = self._rbc
        key, dups, rows, ctx = st.key, st.dups, st.rows, self._ctx
        spinoff_cil = st.spinoff_cil
        owned_by_corp_actions = st.owned_by_corp_actions
        self._pair_reinvest_reversals(transactions)
        self._pair_warrant_exercises(transactions)

        if self._spinoffs:
            # Country-neutral (audit S064-20): corp-actions has election
            # rules for every supported country, and `tjs run` runs
            # it — the old text cited Canadian law and told a US project
            # to add a .tt row the stage already books (double-booked).
            _cil = (f" and {spinoff_cil} cash-in-lieu row(s) for their "
                    f"fractions" if spinoff_cil else "")
            self._note(
                f"{self._spinoffs} RBC spin-off row(s) (DIS - ... SPINOFF){_cil} are "
                f"left to taxjson-corp-actions, which asks for your "
                f"country's tax election (`tjs run` runs it). Parsing "
                f"outside `tjs run`, run taxjson-corp-actions on this "
                f"file too — do not also enter the shares in a .tt file "
                f"(that books them twice).")
        if owned_by_corp_actions:
            self._note(
                f"skipped {owned_by_corp_actions} RBC merger row(s) — these "
                f"are resolved by taxjson-corp-actions (--brokerage "
                f"rbc_direct), which `tjs run` runs. Parsing outside "
                f"`tjs run`, run it on this file too — do not also "
                f"enter the shares in a .tt file (that books them twice).")

        if not transactions:
            # Every row is a copy of a row in an overlapping download,
            # or a leg/reinvestment booked or cancelled from another
            # export of the account: the file is covered, not broken —
            # not taxjson-brokerage's 0-transactions WARNING (re-audit
            # A2-0269: it failed `run --strict` on correct books).
            elsewhere = set(ctx.foreign_legs.get(key, set())) \
                | ctx.rei_drop | ctx.rei_paired
            if dups or any(id(r) in elsewhere for r in rows):
                self.zero_tx_reason = (
                    f"all of its rows are in the account's other RBC "
                    f"export(s) ({', '.join(sorted(set(dups.values()))) or 'booked there'})")
        self._report_untraded_income()
        self._check_emitted_symbols(transactions)
        # The file's cash events, in its rows' order (`note_cash`).
        name = shown_name(path)
        self.cash_events = [ev for c in sorted(self._cash_rows, key=lambda c: c.order)
                            if (ev := rbc_cash_move(c, name)) is not None]
        return transactions

    def release(self) -> None:
        self._rbc = None                # the export's rows and maps released

    def statement_accounts(self) -> set:
        """The Account column's values in the last parsed export (audit
        A2-0008 / A2-0296)."""
        return set(getattr(self, '_rbc_accounts', set()))

    # ------------------------------------------------------ pre-pass maps
    def _row_occ(self, r) -> Optional[str]:
        """OCC symbol from the row's Description, else its Symbol
        Description (some buy rows carry the contract only there).
        None when the Symbol column is the contract's own UNDERLYING
        ticker: that row is the stock leg of an assignment/exercise whose
        text quotes the contract, and it used to become 100x as many
        option contracts with no stock row at all (audit R1-86)."""
        for text in (r.desc, r.symdesc):
            opt = self.parse_option_from_description(text or '')
            if opt:
                sym = (r.symbol or '').strip().upper()
                if sym and not rbc_is_option_code(sym) \
                        and names_underlying(opt['base'], sym):
                    return None
                return self.format_occ_symbol(opt['right'], opt['base'],
                                              opt['expiry'], opt['strike'],
                                              where=f"line {r.line}")
        return None

    def _resolve_temp(self, r) -> str:
        if not rbc_is_temp_symbol(r.symbol):
            return r.symbol
        hit = temp_symbol_ticker(self._ctx, r)
        if hit:
            self._note(f"line {r.line}: RBC temporary code {r.symbol} "
                       f"resolved to {hit} by its security name")
            return hit
        return r.symbol

    def _equity_symbol(self, symbol: str, currency: str, r=None, *,
                       market: bool = False) -> str:
        """Suffix a bare equity symbol by the row's currency — or, for
        income rows (`market=True`), by the LISTING the account holds
        (see `_income_currency`). A row that reads as a TSX fund's
        US-dollar class but becomes a .US listing is an ATTENTION line
        naming the ticker.map EXTRACT line that moves it (none when the
        project's EXTRACT lines already match it). A row an EXTRACT line
        matches gets its symbol here, before the settlement fallback
        reads the listing's market (a TSX US-dollar unit settles on the
        Canadian calendar)."""
        if r is not None:
            _hit = self._extract_symbol(r.desc, currency)
            if _hit:
                return _hit
        if market and r is not None:
            currency = self._income_currency(r) or currency
        out = self.apply_currency_suffix(symbol, currency)
        if (r is not None and out.endswith('.US')
                and _RBC_USD_UNITS_RE.search(f"{r.symdesc} {r.desc}")
                and hasattr(self, '_usd_units_warned')
                and symbol.upper() not in self._usd_units_warned):
            self._usd_units_warned.add(symbol.upper())
            # The TSX unit's spelling: ROOT.U.TO whether the row says
            # ZSP, ZSP.U or ZSP.U.TO (it suggested ZSP.U.TO.U.TO).
            from taxjson.lib.markets import usd_unit_listing
            _unit = usd_unit_listing(symbol)
            if ticker_map_joins(out, _unit):
                return out      # ticker.map already folds them (A2-1056)
            if self._extract_covers(r.desc, currency):
                return out      # an EXTRACT line rewrites the row
            _company = rbc_row_company(r)
            _words = self._usd_units_words(symbol, _company, r.desc)
            self._warn(f"{symbol} ({' '.join(_company.split())!r}) reads "
                       f"as the US-dollar class of a TSX-listed fund but is "
                       f"booked as {out}, a US listing (off the T1135, one "
                       f"pool with IB/Questrade's .U.TO only with a map "
                       f"line). If it trades on the TSX, add to ticker.map:"
                       f"  EXTRACT {_words} | USD | {_unit}",
                       attention=True)
        return out

    def _usd_units_words(self, symbol: str, company: str, desc: str) -> str:
        """The words of the EXTRACT line suggested for `symbol`'s
        US-dollar rows: the longest start (up to six words) of one of
        their company names that EVERY US-dollar row of the symbol in
        the account's exports carries in its Description (what EXTRACT
        matches) and no other symbol's US-dollar row does, so the one
        line moves the fund's trades, transfers and income together.
        Built from the first row alone, a transfer's 'TRANSFER' word
        left the sale under the US listing: the line split the fund and
        the sale lost its purchase (backlog TJ-0438). Else the shortest
        run common to them (cross_listings.extract_words), else the
        first row's words, as before."""
        from taxjson.lib.brokerages.base import extract_words_match
        from taxjson.lib.cross_listings import extract_words
        sym = (symbol or '').strip().upper()
        mine: List[str] = []
        others: List[str] = []
        names: List[str] = [company]
        ctx = getattr(self, '_ctx', None)
        for k in (ctx.files if ctx is not None else ()):
            for o in ctx.rows(k):
                if (o.currency or '').strip().upper() != 'USD' \
                        or not (o.desc or '').strip():
                    continue
                if (o.symbol or '').strip().upper() == sym:
                    mine.append(o.desc)
                    names.append(rbc_row_company(o))
                else:
                    others.append(o.desc)
        if not mine:
            return _extract_words(company, desc)
        seen = set()
        for n in range(6, 0, -1):
            for name in names:
                words = name.split()
                cand = ' '.join(words[:n])
                if len(words) < n or cand in seen:
                    continue
                seen.add(cand)
                if (all(extract_words_match(cand, d) for d in mine)
                        and not any(extract_words_match(cand, d)
                                    for d in others)):
                    return cand
        return (extract_words(mine, others, seeds=names)
                or _extract_words(company, desc))

    def _income_currency(self, r) -> str:
        """The listing (by currency) an income/withholding/ROC/fee row
        belongs to, from the account's own trade rows in ALL its files:
        a TSX stock paying USD keeps .TO; when a bare symbol names two
        securities (a US issuer in USD and a Canadian ETF in CAD; the
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
        name = rbc_norm_company(rbc_row_company(r))
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
            _foreign = [c for c in curs if c != 'CAD']
            if _foreign and all(
                    ticker_map_joins(self.apply_currency_suffix(sym, c),
                                     f"{sym}.{alt}") for c in _foreign):
                continue    # ticker.map already joins the listings (A2-1056)
            kinds_txt = ', '.join(sorted({k for _, k in kinds}))
            msg = (f"{sym}: {kinds_txt} row(s) but no trade rows for {sym} "
                   f"in any RBC file of this account — booked as {listed}, "
                   f"the payment currency's listing. Only if the position "
                   f"is really held under the other listing (a TSX stock "
                   f"paying USD, bought in an export or .tt outside these "
                   f"inputs), add to ticker.map:  TOBASE "
                   f"{self.apply_currency_suffix(sym, 'USD' if 'USD' in curs else curs[0])} "
                   f"{sym}.{alt}")
            if acb and curs != ['CAD']:
                # On the console (re-audit A2-0005): the gain it makes
                # is fileable. TOBASE, not GLOBAL: the USD row must be
                # converted before it meets the CAD pool (a GLOBAL
                # rename stopped the run with a currency mismatch).
                self._warn(msg + " — a return of capital on the wrong "
                           "listing hits an empty pool and becomes a gain.",
                           attention=True)
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
    def _dispatch(self, r) -> "List[Dict[str, Any]] | Outcome":
        cls = r.cls
        if cls in ('trade', 'expiry', 'assignment', 'retraction'):
            tx = self._build_trade(r)
            return [tx] if tx else []
        if cls == 'split':
            tx = self._build_stock_split(r)
            if tx:
                return [tx]
            why = ("has no Quantity (blank or 0) — the split's share "
                   "count is missing" if abs(r.qty) < 1e-9 else
                   "gives no positive split factor from its 'ON N SHS' "
                   "base and Quantity")
            self._warn(f"stock-split row {why} — {r.label()}. NOT booked; "
                       f"fix the row or book the split in a .tt file.",
                       unbooked=True)
            return Outcome.reported("stock-split row not understood (see warning)")
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
                       f"{M.shown(r.value, 2):,.2f} as {cls} and drop the shares; book "
                       f"the row in a .tt file.")
        if cls == 'book-adjust':
            return self._build_book_adjust(r)
        if cls == 'roc':
            return [self._build_roc(r)]
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

    # --------------------------------------------------------------- builders
    def _at(self, r) -> str:
        """'<file>:<line>' for an error naming the row."""
        return f"{self._fname}:{r.line}"

    def _time(self, r) -> str:
        return rbc_time(r.k)

    @builds("split")
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
        base = desc_number(m.group(1), where=self._at(r),
                           field="split base 'ON N SHS'")
        sym_raw = self._resolve_temp(r)
        # On a SHORT pool the split debits shares (a negative quantity
        # for a forward split): (base + received)/base gave 0.95 for a
        # 21-for-20 and 0 (row dropped) for a 2-for-1 (audit S065-00).
        # The account's own rows say which side the pool is on; a long
        # or unknown pool keeps the signed rule (negative = reverse).
        li = self._ctx.listings.get(sym_raw, {}).get(r.currency)
        day_before = (datetime.strptime(r.date, '%Y-%m-%d')
                      - timedelta(days=1)).strftime('%Y-%m-%d')
        if li is not None and li.account_position_on(
                _norm_account(r.account), day_before) < -1e-9:
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

    @builds("transfer")
    def _build_transfer(self, r):
        """An in-kind position transfer (TFI/TFO/TFR). The quantity moves;
        the transferred-in ACB is not booked here (the engine consumes the
        TRANSFER before the ACB pass and missing basis surfaces as
        incomplete history). RBC's "BOOK VALUE nnn" is carried as
        `book_value` evidence: in a taxable account `tjs run` books
        it as the cost of shares that arrive from outside the books
        (lib/transfer_in). Gated by --transfers at the taxjson-brokerage
        wrapper."""
        qty = r.qty
        if qty > 0 and _RBC_TRANSFER_OUT_RE.match(r.desc or ''):
            # Direction from RBC's code/verb only: 'DELIVER' anywhere in
            # the text (a security named DELIVERY HERO) flipped a
            # transfer-IN into an OUT (audit S016-05 / S065-03).
            qty = -qty
        # An in-kind OPTION transfer takes the contract's OCC symbol, as
        # its trades do (audit S065-02: it kept the 7-character RBC code
        # and never netted against the position).
        occ = ((self._occ_by_code.get(r.symbol)
                if rbc_is_option_code(r.symbol) else None)
               or self._row_occ(r))
        tx = {
            'action': 'TRANSFER',
            'date': r.date, 'time': self._time(r),
            'date_settle': r.settle or r.date,
            'symbol': (self.apply_currency_suffix(occ, r.currency) if occ
                       else self._equity_symbol(self._resolve_temp(r),
                                                r.currency, r)),
            'quantity': qty, 'currency': r.currency, 'price': 0.0,
            # Value column (0 on in-kind transfers; the description's
            # BOOK VALUE is carried below as `book_value` evidence).
            'net_amount': abs(r.value),
            'fee': 0.0,
            'account': self.DEFAULT_ACCOUNT, 'description': r.desc,
        }
        if occ:
            tx['multiplier'] = float(self.OPTION_MULTIPLIER)
            tx['contract_size_basis'] = 'assumed'
        m = _RBC_BOOK_VALUE_RE.search(r.desc)
        if m:
            tx['book_value'] = desc_number(m.group(1), where=self._at(r),
                                           field='BOOK VALUE')
        return tx

    @builds("trade", "expiry", "assign")
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
        # A blank settle cell falls back to the cycle of the LISTING's
        # market, not the row currency's (A2-1054): SAMPLF.U.TO bought in USD
        # settles through CDS on the Canadian calendar, skipping the US
        # days CDS settles no US dollars on (TJ-0705).
        from taxjson.lib.dates import settlement_calendar
        _mkt = settlement_calendar(symbol, r.currency)
        if r.settle:
            date_settle = r.settle
        elif occ and not is_assign_leg:
            date_settle = self.settlement_date_t1(r.date, "%Y-%m-%d",
                                                  currency=_mkt)
        else:
            # Equities — and an assignment/exercise OPTION leg, which is
            # one event with its stock leg: T+1 on the option leg alone
            # split the two and let another trade consume the premium
            # (audit S065-06).
            date_settle = self.equity_settlement_date(r.date, _mkt,
                                                      "%Y-%m-%d")
        date = r.date
        # An option EXPIRY has no settlement cycle, and RBC posts it the
        # next business day: book it on the contract's own expiry date
        # with settle == date, so a Dec-31 expiry stays in its year.
        is_expiry = bool(opt) and r.cls == 'expiry'
        if is_expiry:
            date = option_expiry_booking_date(date, opt['expiry'])
            date_settle = date
        elif r.cls == 'expiry':
            # A right / warrant expiry has no settlement cycle either: it
            # kept RBC's next-business-day settle and a Dec-31 expiry's
            # loss moved into the next year (audit S065-04).
            date = non_option_expiry_booking_date(date, desc)
            date_settle = date
        self.check_settle_order(date, date_settle, where=self._at(r),
                                what=repr(desc[:50]))

        qty = r.qty
        is_retraction = r.cls == 'retraction'
        side = self._checked_trade_side(r, bool(occ), bool(
            is_assign_leg or occ and _RBC_OPTION_PATTERNS[1].search(desc)))
        if side == 'sell' or is_retraction:
            qty = -abs(qty)
        price = r.price
        net = r.value
        if (r.value_blank and abs(qty) > 1e-12 and (
                r.cls == 'retraction' or side or activity.strip().lower() in (
                    'exercise', 'assignment'))):
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
            price = M.units(abs(net) / abs(qty), 6)
        # The export never states a contract's size: 100 is assumed
        # unless only a mini's 10 fits the row's Value (B10).
        opt_mult, size_basis = None, ''
        if occ and price and abs(qty) > 1e-12 and r.cls != 'expiry':
            opt_mult, size_basis = self.option_row_multiplier(
                symbol, lambda m: not self._trade_money_problem(
                    r, qty, price, net, True, is_retraction, m))
        self._check_trade_money(r, qty, price, net, bool(occ),
                                is_retraction, mult=opt_mult)
        if not occ and side == 'buy':
            self.warn_zero_cost_buy(self._at(r), r.symbol, qty, price, net)

        is_option_symbol = bool(occ)
        action = 'BUYSELL'
        desc_is_assignment_notice = bool(_RBC_OPTION_PATTERNS[1].search(desc))
        if is_option_symbol and (
            any(x in activity for x in ('Assignment', 'Exercise'))
            or r.code == 'ASN'
            or desc_is_assignment_notice
        ):
            action = 'ASSIGN'
        # A warrant / right EXERCISE (a non-option symbol): the leg that
        # gives up the warrants (qty < 0, no cash) and the leg that
        # receives the shares are paired after the file is read; the
        # warrant leg becomes an ASSIGN naming the shares (A2-0274).
        _ex_role = ''
        if (not is_option_symbol and 'Exercise' in activity
                and abs(qty) > 1e-12):
            _ex_role = 'warrant' if (qty < 0 and abs(net) < 0.005) \
                else ('shares' if qty > 0 else '')

        fee = self.back_compute_fee(qty, price, net, is_option=is_option_symbol,
                                    multiplier=opt_mult)
        if r.cls == 'expiry' and not opt:
            self._note(f"line {r.line}: non-option expiry (rights/warrants) "
                       f"booked as a $0 disposition of {symbol}")

        tx = {
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
                                                   is_option=is_option_symbol,
                                                   multiplier=opt_mult),
            'account': self.DEFAULT_ACCOUNT,
            'description': desc,
            '_expiry': is_expiry,
        }
        if size_basis == 'derived':
            tx['multiplier'] = opt_mult
            tx['contract_size_basis'] = size_basis
        if _ex_role:
            tx['_exercise'] = (_ex_role, r.line)
        oc = self._open_close(r, bool(occ))
        if oc:
            tx['open_close'] = oc
        return tx

    def _pair_warrant_exercises(self, transactions) -> None:
        """Mark each warrant/right `Exercise` leg (the warrants given up,
        no cash) with the share listing the same-date `Exercise` buy
        delivers: an ASSIGN the engines treat like a long call's
        exercise — no disposition, the warrant's cost goes into the
        shares (CA s.49(3); US basis carryover; A2-0274). A leg with no
        single share leg is refused: booked as a sale at 0 its cost
        would become a capital loss."""
        legs = [t for t in transactions if t.get('_exercise')]
        shares = [t for t in legs if t['_exercise'][0] == 'shares']
        warrants = [t for t in legs if t['_exercise'][0] == 'warrant']
        for w, st, cands in pair_warrant_legs(warrants, shares):
            line = w['_exercise'][1]
            if st is None:
                # The .tt form that books it (backlog TJ-0590).
                hint = warrant_exercise_tt_hint(
                    w, [t['symbol'] for t in cands])
                raise _err(Path(self._fname), line,
                    f"an Exercise of {w['symbol']} (a warrant or right) "
                    f"with no same-day Exercise share leg of its own (none "
                    f"whose root is the warrant's, and not the day's one "
                    f"exercise) — refusing to book the "
                    f"warrants as a sale at 0 (their cost belongs in the "
                    f"shares). Book the exercise in a .tt file of this "
                    f"account and remove the row from the export (the "
                    f"file is refused until it is gone): {hint}.")
            w['action'] = 'ASSIGN'
            self._note(f"line {line}: warrant {w['symbol']} exercised into "
                       f"{st['symbol']} — no disposition; the warrant's "
                       f"cost goes into the shares' cost.")
        for t in legs:
            t.pop('_exercise', None)

    def _open_close(self, r, is_option: bool) -> str:
        """The broker's own open/close marker on an option row, as the
        neutral `open_close` evidence code IB rows carry ('O' / 'C'):
        RBC writes "... OPEN CONTRACT" / "... CLOSE CONTRACT" at the end
        of every option trade's description, and an expiry or an
        assignment always closes. Read only for options (audit
        A2-0006): a CLOSE CONTRACT sale whose position the books do not
        hold — a .tt hand-off or an earlier export under another root,
        SAMPLD vs SAMPLD.B — was booked as a NEW written call with no warning;
        with the code, the gains run's broker-closing check names it."""
        if not is_option:
            return ''
        code = _contract_code(r.desc)
        if code:
            return code
        if r.cls in ('expiry', 'assignment') or r.code in ('EXP', 'ASN'):
            return 'C'
        return ''

    def _checked_trade_side(self, r: RbcRow, is_option: bool,
                            assign_leg: bool) -> str:
        """The row's direction (`rbc_trade_side`), once the row is not
        refused for it (`_refuse_trade_side`), for its cash
        (`_refuse_side_against_value`) or for its Quantity
        (`_refuse_trade_qty`)."""
        self._refuse_trade_side(r)
        self._refuse_side_against_value(r, is_option)
        self._refuse_trade_qty(r, assign_leg)
        return rbc_trade_side(r)

    def _refuse_trade_side(self, r: RbcRow) -> None:
        """Refuse a trade row whose Activity and Description name opposite
        directions (Activity Sell, "NAME - Buy": booked by the Activity
        alone, the other never read), and one an unknown Activity leaves
        to a Description that has both words (TJ-5021)."""
        a = r.activity.strip().lower()
        said = rbc_desc_side(r.desc)
        if a in ('buy', 'sell'):
            m = _TRADE_DESC_VERB_RE.search(r.desc or '')
            if m and m.group(1).lower() != a:
                raise _err(Path(self._fname), r.line,
                           f"Activity {r.activity.strip()!r} but the "
                           f"Description ends \"- {m.group(1)}\" "
                           f"({r.label()}) — one says purchase, the other "
                           f"sale; refusing to guess which the row is. "
                           f"Correct the cell that is wrong from the "
                           f"statement (or delete the row and book the "
                           f"trade in a .tt file).")
        elif (r.cls == 'trade' and a not in ('exercise', 'assignment')
              and said == 'both'):
            raise _err(Path(self._fname), r.line,
                       f"Activity {r.activity.strip()!r} is not one this "
                       f"parser knows, and the Description has both \"Buy\" "
                       f"and \"Sell\" ({r.label()}) — refusing to guess "
                       f"which the row is. Set the Activity cell to Buy or "
                       f"Sell from the statement (or delete the row and "
                       f"book the trade in a .tt file).")

    def _refuse_side_against_value(self, r: RbcRow, is_option: bool) -> None:
        """Refuse a trade whose direction and whose cash disagree: a sale's
        cash before commission is cash in, a purchase's cash out (the
        review of TJ-5021: a row read as a sale by a word of its name, or
        a Sell row, with negative cash was booked as a sale with negative
        proceeds). Where the Description gave the direction (an Activity
        not known), the cash must agree both ways. Where the Activity is
        Buy or Sell it gives the direction, and only the dangerous
        contradiction is refused, a sale that pays: a purchase whose cash
        is written positive books by its magnitude, as it always did,
        which is the right figure (the owner's ruling).

        What is read: the Value cell (`gross`, the cash before
        commission) of an export that has the Amount column too, so a
        sale whose commission exceeds its proceeds (a positive Value, a
        negative Amount) books. An export with one money column has only
        the net, and there a sale that pays is either a commission larger
        than its proceeds or a purchase's cash. Where the Description
        gave the direction it is taken for an excess commission only
        while what it pays is less than |Quantity| x Price (with no Price
        nothing says, and the row is left to the money check). Under a
        Sell Activity the Activity rules and the money check bounds the
        commission, as before: a penny option close (proceeds 1.00, a
        commission of 10.95, the net -9.95) pays more than its
        |Quantity| x Price and is an ordinary row of such an export."""
        side = rbc_trade_side(r)
        cash = r.value if r.gross is None else r.gross
        if not side or abs(cash) < 0.005:
            return
        if (side == 'sell') == (cash > 0):
            return
        by_activity = r.activity.strip().lower() == side
        if r.gross is None and side == 'sell':
            gross = abs(r.qty) * r.price * (
                self.OPTION_MULTIPLIER if is_option else 1)
            if by_activity or not r.price or abs(cash) < gross:
                return     # the Activity's sale, an excess commission, no Price
        if by_activity:
            if side == 'buy':
                return
            who = f"Activity {r.activity.strip()!r}"
        else:
            who = (f"the Description {_desc_side_shown(r.desc)[1]} (the "
                   f"Activity {r.activity.strip()!r} is not one this "
                   f"parser knows)")
        sale = side == 'sell'
        money = (f"cash={r.value:g}" if r.gross is None
                 else f"Value={r.gross:g} Amount={r.value:g}")
        row = (f"line {r.line}: {r.date_raw} {r.activity or '?'} "
               f"{r.symbol or '-'} qty={r.qty:g} {money} {r.currency} "
               f"{r.desc[:70]!r}")
        raise _err(Path(self._fname), r.line,
                   f"{who}: a {'sale' if sale else 'purchase'}, but the "
                   f"{'cash' if r.gross is None else 'Value'} "
                   f"{M.shown(cash, 2):,.2f} is cash "
                   f"{'paid out' if sale else 'received'}, a "
                   f"{'purchase' if sale else 'sale'} ({row}) — a "
                   f"sale's cash comes in and a purchase's goes out; "
                   f"refusing to guess which the row is. Correct the cell "
                   f"that is wrong from the statement (the Activity: Buy "
                   f"or Sell; or the cash's sign), or delete the row and "
                   f"book the trade in a .tt file.")

    def _refuse_trade_qty(self, r: RbcRow, assign_leg: bool) -> None:
        """Refuse a trade row whose Quantity contradicts it: a Buy with a
        negative Quantity (re-audit A2-0097: booked as a sale with negative
        proceeds, a 4,880 swing, under --strict; RBC signs a buy positive,
        refused as the generic importer does), and a trade of no units, a
        blank or 0 Quantity, that is no assignment's or exercise's leg (it
        reached `tjs run`'s row check, which stopped the account naming no
        row; the twin of a blank Value, R1-82; backlog TJ-4536)."""
        activity = r.activity.strip()
        if rbc_trade_side(r) == 'buy' and r.qty < 0:
            if activity.lower() == 'buy':
                raise _err(Path(self._fname), r.line,
                           f"a Buy row with a NEGATIVE Quantity {r.qty:g} "
                           f"({r.label()}) — the activity says purchase, "
                           f"the quantity says sale; refusing to guess. "
                           f"Fix the row (or book it in a .tt file).")
            raise _err(Path(self._fname), r.line,
                       f"a row read as a purchase (the Description "
                       f"{_desc_side_shown(r.desc)[1]}; the Activity "
                       f"{activity!r} is not one this parser knows) has a "
                       f"NEGATIVE Quantity {r.qty:g} ({r.label()}) — a "
                       f"purchase by one, a sale by the other; refusing "
                       f"to guess. Set the Activity cell to Buy or Sell "
                       f"from the statement (or book the trade in a .tt "
                       f"file).")
        if abs(r.qty) < QTY_ZERO and not assign_leg:
            raise RbcFormatError(str(zero_units_refusal(
                f"{shown_name(Path(self._fname))}:{r.line}",
                activity or r.cls, '' if r.qty_blank else f"{r.qty:g}")))

    def _check_trade_money(self, r, qty, price, net, is_option,
                           is_retraction, mult=None) -> None:
        """Fail-closed identity for a priced trade (audit S023-19). RBC
        has no commission column: the difference between Value and
        |qty| x Price x multiplier IS the commission, so it must be a
        charge (a buy costs at least its gross, a sale nets at most its
        gross) and of commission size. A Value off by a factor (a
        shifted or mislabelled column) used to book with only a schema
        warning; the same row from Questrade or IB is refused. The
        bounds sit far outside any plausible retail commission."""
        msg = self._trade_money_problem(r, qty, price, net, is_option,
                                        is_retraction, mult)
        if msg:
            raise _err(Path(self._fname), r.line, msg)

    def _trade_money_problem(self, r, qty, price, net, is_option,
                             is_retraction, mult=None) -> str:
        """The _check_trade_money finding for contract size `mult`
        (default: 100 for an option), '' when the row fits."""
        if not price or abs(qty) < 1e-12 or r.cls == 'expiry':
            return ''
        if mult is None:
            mult = self.OPTION_MULTIPLIER if is_option else 1
        gross = abs(qty) * price * mult
        act = rbc_trade_side(r)
        sale = act != 'buy' and (act == 'sell' or is_retraction or qty < 0)
        fee = (gross - net) if sale else (abs(net) - gross)
        low, high = implied_commission_bounds(gross, qty, is_option=is_option)
        if fee < low or (fee > high and not is_retraction):
            return (f"Value {M.shown(net, 2):,.2f} does not fit |Quantity| "
                    f"{abs(qty):g} x Price {price:g}"
                    f"{f' x {mult:g}' if is_option else ''} = {M.shown(gross, 2):,.2f} "
                    f"(implied commission {M.shown(fee, 2):,.2f}) — a wrong or "
                    f"shifted column; refusing to book it "
                    f"({r.desc[:60]!r})")
        return ''

    @builds("stock_dividend")
    def _build_stock_dividend(self, r):
        """A stock dividend paid in shares ("DIS - <name> STK DIV ON N
        SHS"): a NEUTRAL stock-dividend event (a $0 BUYSELL typed
        stock_dividend, as the Questrade and IB parsers emit it); the
        gains engine applies the country's rule (partition INPUTS-01).
        It was an UNCLASSIFIED row left out of the book (or, under
        Dividends, a refusal) and the next full sale went short (audit
        S015-06)."""
        if abs(r.value) > 0.005 or r.qty < 0:
            raise _err(Path(self._fname), r.line,
                       f"a stock-dividend row with "
                       f"{'cash' if abs(r.value) > 0.005 else 'a negative quantity'}"
                       f" ({r.label()}) — not a shape this parser books; "
                       f"book it in a .tt file")
        symbol = self._equity_symbol(self._resolve_temp(r), r.currency, r)
        self._note(f"line {r.line}: stock dividend of {r.qty:g} {symbol} "
                   f"booked as a stock-dividend event — the gains run "
                   f"applies your country's rule to its cost (`tjs "
                   f"tax-logic`).")
        return {
            'action': 'BUYSELL',
            'date': r.date, 'time': self._time(r),
            'date_settle': r.settle or r.date,
            'symbol': symbol, 'quantity': r.qty, 'currency': r.currency,
            'price': 0.0, 'fee': 0.0, 'net_amount': 0.0, 'gross_amount': 0.0,
            'account': self.DEFAULT_ACCOUNT, 'description': r.desc,
            'type': STOCK_DIVIDEND,
        }

    @builds("dividend", "trade")
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
        price = desc_number(m.group(1), where=self._at(r),
                            field="'Reinvest @' price")
        amount = M.units(r.qty * price, 2)
        symbol = self._equity_symbol(r.symbol, r.currency, r, market=True)
        self._note(f"line {r.line}: in-kind reinvested distribution — "
                   f"income {amount:,.2f} {r.currency} and a purchase of "
                   f"{r.qty:g} {symbol} at {price:g}; the distribution's "
                   f"character (dividend, capital gain, ROC) is on "
                   f"{self._terms['fund_slip']}.")
        div = {
            'action': 'DIVIDEND',
            'date': r.date, 'time': '09:30:00', 'date_settle': r.date,
            'symbol': symbol, 'quantity': r.qty, 'currency': r.currency,
            'price': price, 'net_amount': amount, 'gross_amount': amount,
            'type': 'dividend', 'account': self.DEFAULT_ACCOUNT,
            'description': r.desc,
        }
        # The same "REC mm/dd/yy" and distribution facts as a cash
        # distribution (backlog TJ-0741: a December-record January-paid
        # reinvested distribution was dated in the next year).
        div.update(income_facts_from_description(r.desc, r.activity or ''))
        return [div, {
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
        one reinvestment of the same units and cash, in ANY file of the
        account (planned in _plan_reinvest_reversals; the original was
        not booked in its file): both drop out. abs() booked the
        reversal as a SECOND purchase (audit R1-83). A reversal with no
        original in the account's files is refused."""
        for _key, r in self._rei_reversals:
            if id(r) not in self._ctx.rei_paired:
                raise _err(Path(self._fname), r.line,
                           f"a reversed reinvestment ({r.label()}) whose "
                           f"original REI row (on or before it) is in no "
                           f"RBC export of this account — refusing to book "
                           f"it as a second purchase. Add the export that "
                           f"holds the original, or book the correction in "
                           f"a .tt file.")

    @builds("trade")
    def _build_rights(self, r):
        """Rights/warrants distributed to every shareholder ("DIS - RTS
        ... RTS DIST ON N SHS"): no income (ITA s.15(1)(c) excludes rights
        conferred on all shareholders) and a NIL cost, so a $0 acquisition
        is the right booking — said out loud now instead of silently."""
        symbol = self._equity_symbol(self._resolve_temp(r), r.currency, r)
        t = self._terms
        self._note(f"line {r.line}: rights/warrants distribution booked as a "
                   f"$0 acquisition of {r.qty:g} {symbol} ({t['rights']}). "
                   f"If these were NOT issued to all shareholders, "
                   f"{t['rights_benefit']}.")
        return {
            'action': 'BUYSELL',
            'date': r.date, 'time': self._time(r),
            'date_settle': r.settle or r.date,
            'symbol': symbol, 'quantity': r.qty, 'currency': r.currency,
            'price': 0.0, 'fee': 0.0, 'net_amount': 0.0, 'gross_amount': 0.0,
            'account': self.DEFAULT_ACCOUNT, 'description': r.desc,
        }

    @builds("trade")
    def _build_reinvest(self, r):
        """Dividend reinvestment ("REI - ... REINV@C$12.3456"): a PURCHASE
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
                       f"Value {M.shown(r.value, 2):,.2f} of the same sign — a "
                       f"reinvestment buys units for cash (a reversal "
                       f"returns both); refusing to guess "
                       f"({r.desc[:60]!r})")
        rei_key = (self._equity_symbol(r.symbol, r.currency, r),
                   M.units(qty, 6), M.units(net, 2))
        if r.qty < 0:
            self._rei_reversals.append((rei_key, r))
            return []
        if id(r) in self._ctx.rei_drop:
            return []         # cancelled by a reversal (any file)
        self.check_settle_order(r.date, r.settle, where=self._at(r),
                                what=repr(r.desc[:50]))
        m = REINV_PRICE_RE.search(r.desc)
        # 'REINV@C$1,234.56' is 1234.56, not 1 (audit S062-20 /  # pii-ok
        # S064-21); a decimal comma falls back to the cash / units.
        price = r.price or (desc_number(m.group(1), strict=False) if m
                            else None) or M.units(net / qty, 6)
        mc = REINV_CUR_RE.search(r.desc)
        price_cur = (r.currency if r.price else
                     REINV_CUR.get(mc.group(1).upper(), '?') if mc
                     else r.currency)
        bad = reinvest_identity_error(qty, net, price if (r.price or m)
                                      else None, price_cur, r.currency)
        if bad:
            raise _err(Path(self._fname), r.line,
                       f"reinvestment row: {bad} ({r.desc[:60]!r}) — a "
                       f"wrong or shifted column; refusing to book it as "
                       f"the units' cost.")
        price = reinvest_row_price(qty, net, price, price_cur, r.currency)
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
        return [tx]

    @builds("roc", "adjust")
    def _build_book_adjust(self, r) -> List[Dict[str, Any]]:
        """RBC's book-cost adjustments carry the amount in the description,
        not the Value column (Value is 0):
          "... 2022 NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK COST $5293.06"
              → a reinvested (notional) distribution: ACB UP
          "... RETURN OF CAPITAL ADJUSTMENT TO BOOK COST $1.16"
              → ACB DOWN (return of capital).
        The direction comes from the Activity label too: a 'Return of
        Capital' row lowers the ACB whatever its description words
        (re-audit A2-0273 — 'ROC ADJUSTMENT TO BOOK COST' raised it); an
        activity and a description that disagree are refused."""
        m = _RBC_BOOK_COST_RE.search(r.desc)
        amount = (desc_number(m.group(1), where=self._at(r),
                              field='ADJUSTMENT TO BOOK COST') if m else 0.0)
        if amount < 0.005:
            # Nothing to book: a 0.00 ADJUST plus a 'raises its ACB'
            # warning contradicted this line (re-audit A2-1047).
            self._warn(f"$0 book-cost adjustment — {r.label()}; nothing "
                       f"booked.")
            return []
        symbol = self._equity_symbol(r.symbol, r.currency, r, market=True)
        act_roc = (r.activity or '').strip().lower() == 'return of capital'
        notional = bool(re.search(r'\bNOTIONAL\b', r.desc or '', re.I))
        if act_roc and notional:
            raise _err(Path(self._fname), r.line,
                       f"a 'Return of Capital' row describes a NOTIONAL "
                       f"distribution ({r.desc[:80]!r}) — one lowers the "
                       f"{self._terms['cost']}, the other raises it; "
                       f"refusing to guess. Book it in a .tt ADJUST from "
                       f"{self._terms['fund_slip']}.")
        if act_roc or is_roc_description(r.desc):
            # The year-end ROC book-cost row reclassifies part of the
            # cash distributions already booked as income (its Value is
            # 0): the ACB comes down, and the income totals still hold
            # those dollars (re-audit A2-0094).
            self._warn(f"line {r.line}: year-end return of capital "
                       f"{M.shown(amount, 2):,.2f} {r.currency} on {symbol} lowers "
                       f"its {self._terms['cost']}; RBC posts no cash for "
                       f"it, so it is usually part of the cash "
                       f"distributions already in the income totals "
                       f"(counted twice) — take the income from "
                       f"{self._terms['roc_income']}, not from "
                       f"divs-sum.", attention=True)
            return [self.tx_roc_adjust(symbol=symbol, currency=r.currency,
                                       date=r.date, desc=r.desc,
                                       amount=amount, no_cash='book-cost')]
        # The notional (reinvested) distribution is taxable income of the
        # year as well as an ACB increase; the export carries only the
        # book-cost side and taxjson books only that (audit S063-17 —
        # whether to book the income is the owner's call).
        self._warn(f"line {r.line}: notional distribution {M.shown(amount, 2):,.2f} "
                   f"{r.currency} on {symbol} raises its "
                   f"{self._terms['cost']}; the distribution itself is "
                   f"income reported on {self._terms['dist_income']} and "
                   f"is NOT in taxjson's income totals — take it from the "
                   f"slip.", attention=True)
        return [{
            'action': 'ADJUST',
            'date': r.date, 'time': '09:30:00', 'date_settle': r.date,
            'symbol': symbol, 'quantity': 0.0, 'currency': r.currency,
            'net_amount': amount, 'gross_amount': 0.0, 'type': 'dist',
            'account': self.DEFAULT_ACCOUNT, 'description': r.desc,
        }]

    @builds("roc")
    def _build_roc(self, r: RbcRow) -> Dict[str, Any]:
        """A Distributions RETURN OF CAPITAL row: the cost reduction
        (`tx_roc_adjust`); a $0 one warned."""
        if abs(r.value) < 0.005:
            self._warn(f"$0 return-of-capital row — {r.label()}; "
                       f"nothing to book (check "
                       f"{self._terms['roc_slip']}).")
        return self.tx_roc_adjust(
            symbol=self._equity_symbol(r.symbol, r.currency, r, market=True),
            currency=r.currency, date=r.date, desc=r.desc,
            amount=r.value)

    @builds("dividend", "dividend_in_lieu", "tax")
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
            gross_amount = M.units(net / 0.85, 2)
            tax_withheld = M.units(gross_amount - net, 2)
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
        # A payment in lieu of a dividend is its own income kind (re-
        # audit A2-0098: booked as a dividend, a PIL on a US issuer got a
        # foreign tax credit and the US engine called it qualified). RBC
        # Direct is a Canadian dealer: characterize/income_dating deems a PIL on
        # a Canadian issuer a dividend (CA-INC-03); the rest is
        # ordinary income (US-INC-01).
        pil = bool(PIL_DESC_RE.search(desc))
        div = {
            'action': 'DIVIDEND_IN_LIEU' if pil else 'DIVIDEND',
            'date': date, 'time': '09:30:00', 'date_settle': date,
            'symbol': symbol, 'quantity': qty, 'currency': currency,
            'price': rate,
            'net_amount': net, 'gross_amount': gross_amount,
            'type': 'dividend_in_lieu' if pil else 'dividend',
            'account': self.DEFAULT_ACCOUNT,
            'description': desc,
        }
        if pil:
            div['dealer_country'] = 'CA'
        # "REC mm/dd/yy" and the "Distribution" activity / "DIST ON"
        # label, as neutral facts (characterize/income_dating dates the row).
        div.update(income_facts_from_description(desc, r.activity or ''))
        out.append(div)
        return out

    @builds("tax")
    def _build_tax(self, r):
        if not r.symbol.strip():
            # A made-up 'UNKNOWN' symbol split the withholding from its
            # dividend with no message (re-audit A2-1050): refuse.
            raise _err(Path(self._fname), r.line,
                       f"a Taxes row with a blank Symbol ({r.label()}) — "
                       f"the withholding cannot be tied to its security; "
                       f"refusing to book it on a made-up symbol. Fill in "
                       f"the Symbol (or book it in a .tt TAX row).")
        tax_symbol = self._equity_symbol(r.symbol, r.currency, r, market=True)
        return {
            'action': 'TAX',
            'date': r.date, 'time': '09:30:00', 'date_settle': r.date,
            'symbol': tax_symbol, 'quantity': 0.0, 'currency': r.currency,
            # RBC books withholding NEGATIVE (cash out), a refund POSITIVE;
            # the repo-wide TAX convention is positive = tax withheld.
            'net_amount': -r.value, 'type': 'tax',
            'account': self.DEFAULT_ACCOUNT, 'description': r.desc,
        }

    @builds("interest")
    def _build_interest(self, r):
        return {
            'action': 'INTEREST',
            'date': r.date, 'time': '09:30:00', 'date_settle': r.date,
            'symbol': 'CASH', 'quantity': 0.0, 'currency': r.currency,
            'net_amount': r.value,  # keep sign for interest
            'type': 'interest', 'account': self.DEFAULT_ACCOUNT,
            'description': r.desc,
        }

    @builds("fee")
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

    @builds("split", "rename")
    def _emit_option_adjust(self, ev) -> List[Dict[str, Any]]:
        """XCH on an option: RBC removes the contract under one code and
        re-adds it under another (special-dividend strike adjustment, the
        underlying's name change, a security-code change). The SAME
        contract continues — the position, its ACB and its open date carry
        over. RBC's own trade rows keep describing the adjusted contract
        with the ORIGINAL terms (a 40 strike after a $0.50 adjustment to
        39.50; ABC after .ABC1), so the position keeps its original OCC
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
                   f"{tgt_sym} ×{factor:g}{adj}; {self._terms['cost']} "
                   f"and open date carried")
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

    @builds("split", "rename", "roc", "trade")
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
            for nm in (rbc_row_company(rem), old_name):
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
                        f"{tgt_guess}   — {rem.label()}", attention=True)
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
                               f"{M.shown(leg.value, 2):,.2f} that the parser does not "
                               f"understand — NOT booked: {leg.label()}",
                               unbooked=True)

        ratio = ev.ratio
        # SIGNED: a reversal CIL row (negative) cancels a posting; abs()
        # counted it as more proceeds (audit S063-19).
        cil_total = sum(c.value for c in ev.cil)
        if ev.cil and cil_total < -0.005:
            self._warn(f"cash in lieu after the {rem.date} reorganization "
                       f"of {rem.symbol} nets NEGATIVE ({M.shown(cil_total, 2):,.2f}) "
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
                'price': M.units(cil_total / frac, 6), 'fee': 0.0,
                'net_amount': M.units(cil_total, 2), 'gross_amount': 0.0,
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
                self._warn(f"cash in lieu {M.shown(cil_total, 2):,.2f} after the "
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
