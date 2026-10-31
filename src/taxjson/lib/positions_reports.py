"""Broker positions reports — what a broker says you HOLD on a date.

A positions (holdings) report is not activity: it lists each position
with its quantity and, when the broker states it, its book cost. Two
commands read one: `tjs sanity` compares it with the books, and
`tjs opening` turns it into OPENING rows. `tjs run` never books
one (a positions-only file dropped into inputs/<account>/ is skipped
with a note — lib/brokerages/detect).

Every reader returns the same row (PositionRow): the broker account
MASKED (`account`, for every label) with a one-way key of the whole id
(`account_key`, to tell two accounts apart that mask alike), the
symbol spelled exactly as that broker's TRADE parser spells it
(ticker.map is the caller's job, as for the books), the signed
quantity, the position's currency, and the cost the report states with
what KIND of cost it is:

  average cost   a Canadian broker's book cost / book value (all the
                 identical units averaged), or a holdings file's
                 total_cost / average entry price
  lot basis      the sum of the broker's per-lot cost basis (IB's Cost
                 Basis: each lot at its own price, matched to sales by
                 the account's lot-matching method)
  ""             the report states no cost

Market value is carried separately (`market_value`) and is NEVER a
cost: a report whose only money column is market value has no cost.

Supported reports (detected by CONTENT, never by file name):

  ib_open_positions   an Interactive Brokers Activity Statement's
                      `Open Positions` section (Summary rows; Lot rows
                      are not read — no lot-row layout with acquisition
                      dates is documented in the parser code). Symbols
                      go through the IB trade parser's own helpers
                      (listing venue from the Financial Instrument
                      Information, OCC option symbols with root
                      aliases). Cost = `Cost Basis` in the row's
                      currency; `Value` is the market value. As of the
                      statement period's last day. A consolidated
                      statement ("Accounts Included") lists each
                      position ONCE for all its accounts combined.
  rbc_holdings        an RBC Direct Investing "Holdings Export as of
                      <date>" CSV. The parser code documents only that
                      preamble (rbc_direct.is_holdings_export) and no
                      real sample was available, so the columns are
                      matched by their LABELS: Symbol and Quantity are
                      required, Currency is required to suffix the
                      symbol (.TO / .US, as the RBC trade parser does),
                      a column labelled book cost / book value is the
                      total cost (an average-cost column x quantity
                      otherwise), a label naming CAD or USD sets the
                      cost's currency, Market Value is market value
                      only, and Account is masked.
  holdings_toml       a `[[holding]]` TOML (the file `tjs sanity`
                      has always read; taxjson-fetch's Questrade
                      snapshot; other tools' exports): symbol,
                      quantity, currency, total_cost (or book_cost /
                      cost_basis), else average_entry_price (a price
                      per share) x quantity, x the contract multiplier
                      for an option (100 when none is declared);
                      `cost_kind = "average" | "lots"`; `acquired =
                      "YYYY-MM-DD"` dates THAT lot (several tables of
                      one symbol are several lots); as of [meta]
                      `as_of`, else the date of `generated_at`.

Unsupported (UNSUPPORTED below says why for each): Questrade, Webull,
Coinbase and Kraken have no positions export the parser code knows.
"""

import csv
import hashlib
import io
import math
import re
from dataclasses import dataclass, field
from datetime import date as _date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

COST_AVERAGE = "average cost"
COST_LOTS = "lot basis"
COST_NONE = ""

SUPPORTED: Dict[str, str] = {
    "ib_open_positions": "Interactive Brokers Activity Statement — Open "
                         "Positions section (Cost Basis = lot basis)",
    "rbc_holdings": "RBC Direct Investing Holdings Export (book cost = "
                    "average cost; columns matched by label)",
    "holdings_toml": "[[holding]] TOML (taxjson-fetch's Questrade "
                     "snapshot, hand-typed or another tool's export)",
}

UNSUPPORTED: Dict[str, str] = {
    "questrade": "no positions CSV is known to the Questrade parser; "
                 "`tjs fetch --positions` (the taxjson-fetch plugin) "
                 "writes the account's live positions as a [[holding]] "
                 "TOML, which is supported",
    "webull": "the Webull parser reads the Trading Summary only; no "
              "positions export is documented — type the positions into "
              "a [[holding]] TOML",
    "coinbase": "the Coinbase parser reads transaction history only; no "
                "balances export is documented — use a [[holding]] TOML",
    "kraken": "the Kraken parser reads trades and ledgers; a ledger's "
              "running balance is not a positions report — use a "
              "[[holding]] TOML",
}

_KIND_LABEL = {
    "ib_open_positions": "Interactive Brokers Activity Statement — Open "
                         "Positions",
    "rbc_holdings": "RBC Holdings Export",
    "holdings_toml": "holdings TOML",
}


class PositionsReportError(ValueError):
    """A positions report that cannot be read safely (the message names
    the masked file)."""


class PositionsDateError(PositionsReportError):
    """A holdings TOML date (`[meta] as_of`, `generated_at`, a holding's
    `acquired`) that is not a calendar day. `tjs sanity` refuses the
    file for it, where a malformed cost field only skips the cost."""


class PositionsSnapshotError(PositionsReportError):
    """A holdings TOML that is not a whole, readable snapshot: its
    `[meta] holdings_count` is not its number of `[[holding]]` tables
    (cut short or edited), or a holding's contract size is not a
    positive number (or contradicts `contract_multiplier_unknown`).
    `tjs sanity` refuses the file for it, as for a date that is no day:
    its quantities are never compared as if complete (audit 2026-10-25
    brokers-inputs#1)."""


@dataclass
class PositionRow:
    broker: str
    account: str
    symbol: str
    raw_symbol: str
    quantity: float
    currency: str
    cost: Optional[float]
    cost_currency: str
    cost_kind: str
    market_value: Optional[float]
    as_of: str
    asset_type: str
    lot_date: str = ''
    multiplier: float = 0.0
    source: str = ''
    # A holdings file's cost in the books' base currency at the
    # acquisition dates (taxjson-export's base_total_cost /
    # base_currency), when it states one: the tax cost an opening keeps
    # (backlog TJ-0990). None / '' when the file gives none.
    base_cost: Optional[float] = None
    base_currency: str = ''
    # A one-way key of the whole broker account id (account_key()):
    # two accounts that mask alike (the same first two characters) keep
    # distinct keys. Never shown; `account` is the label.
    account_key: str = field(default='', repr=False)


@dataclass
class PositionsReport:
    path: Path
    broker: str
    kind: str
    as_of: str
    accounts: List[str] = field(default_factory=list)
    rows: List[PositionRow] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def mask_account(acct) -> str:
    """First two characters + *** (the parsers' rule); '' for none."""
    a = str(acct or '').strip()
    return (a[:2] + '***') if a else ''


def account_key(acct) -> str:
    """A one-way key of a broker account id (case and surrounding space
    ignored), '' for none: equal for one account, distinct for two that
    mask alike. Never a label: `mask_account` is."""
    a = str(acct or '').strip().upper()
    if not a:
        return ''
    return hashlib.sha256(f"taxjson-position-account:{a}"
                          .encode('utf-8')).hexdigest()[:16]


def kind_label(kind: str) -> str:
    return _KIND_LABEL.get(kind, kind)


# ------------------------------------------------------------ detection

_HOLDINGS_PREAMBLE_RE = re.compile(
    r'^\s*Holdings\s+Export\b(?:\s+as\s+of\s+(?:Date\s+)?'
    # The comma may be a CSV cell break ("...Jan 5,2026 at ..." unquoted).
    r'([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s*(\d{4}))?', re.I)


def _rows(text: str, limit: Optional[int] = None) -> List[List[str]]:
    from taxjson.lib.brokerages.detect import csv_rows
    return csv_rows(text, limit)


def _first_text(rows) -> str:
    for r in rows:
        if r and any(c.strip() for c in r):
            return " ".join(c.strip() for c in r if c.strip())
    return ''


def _preamble_date(first: str) -> str:
    m = _HOLDINGS_PREAMBLE_RE.match(first or '')
    if not m or not m.group(1):
        return ''
    mon, day, yr = m.group(1), m.group(2), m.group(3)
    for fmt, mm in (('%b %d %Y', mon[:3]), ('%B %d %Y', mon)):
        try:
            return datetime.strptime(f"{mm} {day} {yr}",
                                     fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return ''


def _ib_has_open_positions(rows) -> bool:
    return any(len(r) >= 2 and r[0].strip() == 'Open Positions'
               and r[1] == 'Header' for r in rows)


def text_kind(text: str) -> Optional[str]:
    """The positions-report kind of decoded CSV text, or None."""
    from taxjson.lib.brokerages.detect import looks_like_ib_rows
    from taxjson.lib.brokerages.rbc_direct import is_holdings_export
    rows = _rows(text)
    if looks_like_ib_rows(rows) and _ib_has_open_positions(rows):
        return 'ib_open_positions'
    if is_holdings_export(_first_text(rows[:40])):
        return 'rbc_holdings'
    return None


def positions_only_text(text: str) -> Optional[Tuple[str, str]]:
    """(kind, as_of) when decoded CSV text is a positions-ONLY report
    (never activity), else None. An IB Activity Statement is activity
    whatever sections it carries."""
    from taxjson.lib.brokerages.rbc_direct import is_holdings_export
    rows = _rows(text, 40)
    first = _first_text(rows)
    if is_holdings_export(first):
        return 'rbc_holdings', _preamble_date(first)
    return None


def _is_holdings_toml(path: Path) -> bool:
    from taxjson.lib.tomlcompat import tomllib
    if tomllib is None:
        return False
    try:
        doc = tomllib.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    return isinstance(doc, dict) and isinstance(doc.get('holding'), list)


def _decode(path: Path) -> str:
    from taxjson.lib.brokerages.base import (BrokerageParseError,
                                             decode_broker_text, shown_name)
    try:
        return decode_broker_text(path.read_bytes(), shown_name(path))
    except BrokerageParseError as e:
        raise PositionsReportError(str(e)) from e
    except OSError as e:
        raise PositionsReportError(
            f"{shown_name(path)}: cannot read it ({e.strerror or e})") from e


def detect_positions(path) -> Optional[str]:
    """'ib_open_positions' | 'rbc_holdings' | 'holdings_toml' | None —
    by content (a .toml file is a holdings TOML when it has a
    [[holding]] array)."""
    path = Path(path)
    if path.suffix.lower() == '.toml':
        return 'holdings_toml' if _is_holdings_toml(path) else None
    try:
        return text_kind(_decode(path))
    except PositionsReportError:
        return None


def positions_only(path) -> Optional[str]:
    """Like detect_positions, but only for files that are positions
    reports and NOT activity exports (an IB Activity Statement with an
    Open Positions section is activity too -> None)."""
    path = Path(path)
    if path.suffix.lower() == '.toml':
        return 'holdings_toml' if _is_holdings_toml(path) else None
    try:
        hit = positions_only_text(_decode(path))
    except PositionsReportError:
        return None
    return hit[0] if hit else None


# ------------------------------------------------------------ helpers

def _num(raw, *, field: str, where: str, blank_ok: bool) -> Optional[float]:
    from taxjson.lib.brokerages.base import (BrokerageParseError,
                                             parse_strict_number)
    try:
        v = parse_strict_number(raw, field=field, where=where,
                                allow_blank=blank_ok, blank=None)
    except BrokerageParseError as e:
        raise PositionsReportError(str(e)) from e
    if v is not None and not math.isfinite(v):
        raise PositionsReportError(f"{where}: {field} is not finite")
    return v


# A symbol or currency cell is copied into .tt lines (`tjs opening`)
# and reports: a control character (a newline) or, in a symbol taxjson
# builds, whitespace is refused, never carried (security review M2).
_CTRL_RE = re.compile('[\x00-\x1f\x7f\x85\u2028\u2029]')
_CCY_RE = re.compile(r'[A-Z]{3}\Z')


def _safe_cell(value: str, *, field: str, where: str,
               spaces_ok: bool = False) -> str:
    """`value` unchanged, or PositionsReportError naming the row when it
    carries a control character (or any whitespace, unless spaces_ok:
    a broker's raw option symbol has spaces)."""
    if _CTRL_RE.search(value) or (not spaces_ok
                                  and re.search(r'\s', value)):
        raise PositionsReportError(
            f"{where}: {field} {ascii(value)} has a control character or "
            f"whitespace in it — not a {field.lower()} taxjson reads; fix "
            f"the file (an unreadable row is never skipped)")
    return value


def _check_currency(cur: str, *, field: str, where: str) -> str:
    if not _CCY_RE.match(cur):
        raise PositionsReportError(
            f"{where}: {field} {ascii(cur)} is not a 3-letter currency "
            f"code — fix the file (an unreadable row is never skipped)")
    return cur


def _refuse_duplicate_labels(name: str, header: List[str],
                             read: List[str], lineno: int) -> None:
    """A column label the reader reads that the header has twice: which
    one is the position's is a guess (two Quantity columns of 10 and
    100), so the file is refused, naming it (backlog TJ-0530)."""
    seen: Dict[str, int] = {}
    for c in header:
        k = c.replace('\ufeff', '').strip().lower()
        seen[k] = seen.get(k, 0) + 1
    twice = [c for c in read if seen.get(c.lower(), 0) > 1]
    if twice:
        raise PositionsReportError(
            f"{name}:{lineno}: the header has {twice[0]!r} "
            f"{seen[twice[0].lower()]} times — refusing to guess which "
            f"column is the position's; keep one of them")


def _blank_symbol_error(where: str, qty: float,
                        what: str = '') -> PositionsReportError:
    """A security row with a quantity and no symbol: the snapshot would
    be short by it, so the file is refused (backlog TJ-0529)."""
    return PositionsReportError(
        f"{where}: a position of {qty:g}"
        + (f" ({what})" if what else "")
        + " with no Symbol — the snapshot would leave it out, and an "
          "unreadable row is never skipped: add its symbol, or delete "
          "the row if it is a holding taxjson does not book (a GIC)")


def _asset_type_of(symbol: str) -> str:
    from taxjson.lib.core import is_option_symbol
    if symbol.startswith('F:'):
        return 'future'
    if is_option_symbol(symbol):
        return 'option'
    return 'stock'


# ------------------------------------------------------------ IB

def _read_ib(path: Path, text: str) -> PositionsReport:
    from taxjson.lib.brokerages.base import BrokerageParseError, shown_name
    from taxjson.lib.brokerages import ib_extractor as ibx
    name = shown_name(path)
    rows = list(csv.reader(io.StringIO(text, newline='')))
    try:
        pre = ibx._ib_prescan(rows, name)
    except BrokerageParseError as e:
        raise PositionsReportError(str(e)) from e
    fii = pre['fii']
    ex = ibx.IbBrokerage()
    ex._ib_pre = pre
    ex._placeholder_warned = set()
    aliased: Dict[str, str] = {}
    as_of = ''
    for r in rows:
        if len(r) >= 4 and r[0] == 'Statement' and r[1] == 'Data' \
                and r[2] == 'Period':
            span = ibx._ib_period(r[3])
            if span:
                as_of = span[1].isoformat()
            break
    accounts = sorted({mask_account(a) for a in pre['accounts']})
    rep = PositionsReport(path=path, broker='ib',
                          kind=kind_label('ib_open_positions'),
                          as_of=as_of, accounts=accounts)
    if not as_of:
        rep.notes.append(f"{name}: no statement Period — the positions "
                         f"have no as-of date")
    if pre.get('accounts_included') and len(pre['accounts']) > 1:
        rep.notes.append(
            f"{name}: a consolidated statement of {len(pre['accounts'])} "
            f"accounts ({', '.join(accounts)}): each position is listed "
            f"once, for all of them combined")
    hm: Optional[Dict[str, int]] = None
    lots = skipped_cash = 0
    seen_section = False
    # (category, symbol, currency) of the Lot rows that hold a quantity,
    # and of the Summary rows: a lot with no Summary row of its security
    # is a position this reader would leave out (backlog TJ-0969).
    lot_keys: Dict[tuple, str] = {}
    summary_keys: set = set()
    for lineno, r in enumerate(rows, 1):
        if len(r) < 2 or r[0].strip() != 'Open Positions':
            continue
        if r[1] == 'Header':
            hm = {c.strip(): i for i, c in enumerate(r)}
            seen_section = True
            missing = [c for c in ('Asset Category', 'Currency', 'Symbol',
                                   'Quantity') if c not in hm]
            if missing:
                raise PositionsReportError(
                    f"{name}:{lineno}: Open Positions header lacks "
                    f"{', '.join(missing)} — not a layout this reader "
                    f"knows; refusing to guess which column is which")
            _refuse_duplicate_labels(name, r[2:], list(_IB_READ), lineno)
            continue
        if r[1] != 'Data' or hm is None:
            continue            # Total / SubTotal rows

        def g(col, _r=r):
            i = hm.get(col)
            return _r[i].strip() if i is not None and i < len(_r) else ''

        where = f"{name}:{lineno}"
        discr = g('DataDiscriminator')
        _pkey = (g('Asset Category'), g('Symbol'),
                 ibx._norm_ccy(g('Currency')))
        if discr and discr != 'Summary':
            lots += 1
            if _pkey[0] not in ('Forex', 'Cash'):
                try:
                    _lq = _num(g('Quantity'), field='Quantity',
                               where=where, blank_ok=True)
                except PositionsReportError:
                    _lq = 1.0       # unreadable: not known to be empty
                if _lq:
                    lot_keys.setdefault(_pkey, where)
            continue
        summary_keys.add(_pkey)
        cat = g('Asset Category')
        cur = ibx._norm_ccy(g('Currency'))
        raw = g('Symbol')
        if cat in ('Forex', 'Cash'):
            skipped_cash += 1
            continue
        if not raw:
            # A security row (not Forex or Cash) with no symbol is
            # refused when it holds a quantity: it was left out as
            # "cash/forex" and the snapshot read as complete (TJ-0529).
            _q = _num(g('Quantity'), field='Quantity', where=where,
                      blank_ok=True)
            if _q:
                raise _blank_symbol_error(where, _q)
            skipped_cash += 1
            continue
        _safe_cell(raw, field='Symbol', where=where, spaces_ok=True)
        _check_currency(cur, field='Currency', where=where)
        qty = _num(g('Quantity'), field='Quantity', where=where,
                   blank_ok=False)
        if abs(qty) < 1e-12:
            continue
        cost = _num(g('Cost Basis'), field='Cost Basis', where=where,
                    blank_ok=True) if 'Cost Basis' in hm else None
        value = _num(g('Value'), field='Value', where=where,
                     blank_ok=True) if 'Value' in hm else None
        mult = _num(g('Mult'), field='Mult', where=where,
                    blank_ok=True) if 'Mult' in hm else None
        try:
            if cat in ('Equity and Index Options', 'Options On Futures'):
                occ = ex._option_symbol(raw, cat, fii, pre['root_alias'],
                                        aliased, where)
                occ = ibx._IB_EXT_RE.sub('', occ)
                sym = f"{occ}.{ibx._ib_currency_ext(cur)}"
                if cat == 'Options On Futures':
                    sym = f"F:{sym}"
                atype = 'option'
            elif cat == 'Futures':
                sym = (f"F:{ibx._IB_EXT_RE.sub('', raw.replace(' ', '.'))}"
                       f".{ibx._ib_currency_ext(cur)}")
                atype = 'future'
            elif cat in ('Stocks', 'Warrants'):
                sym = ibx._ib_stock_symbol(cat, raw, cur, fii)
                atype = 'stock'
            else:
                sym = (f"{ibx._IB_EXT_RE.sub('', raw.replace(' ', '.'))}"
                       f".{ibx._ib_currency_ext(cur)}")
                atype = 'other'
        except BrokerageParseError as e:
            raise PositionsReportError(str(e)) from e
        _safe_cell(sym, field='Symbol', where=where)
        _from = 'Mult'
        if mult is None:
            info = (fii.get((cat, raw))
                    or fii.get((cat, re.sub(r'\s+', ' ', raw))) or {})
            mult = info.get('mult')
            _from = 'Financial Instrument Information Multiplier'
            if mult is None and info.get('mult_bad'):
                raise PositionsReportError(
                    f"{where} ({raw}): the {_from} {info['mult_bad']!r} "
                    f"is not a number — fix the file (an unreadable row "
                    f"is never skipped)")
        if mult is not None and not (math.isfinite(mult) and mult > 0):
            # A contract size is a positive count of units, as the
            # holdings TOML reader and the trade importer require: a 0
            # or negative one was returned as a valid position (backlog
            # TJ-0532).
            raise PositionsReportError(
                f"{where} ({raw}): {_from} {mult:g} is not a positive "
                f"contract size — fix the file (an unreadable row is "
                f"never skipped)")
        if 'Account' in hm:
            acct, akey = mask_account(g('Account')), account_key(
                g('Account'))
        else:
            acct = accounts[0] if len(accounts) == 1 else ''
            # A consolidated statement lists one position for all its
            # accounts combined: no one account's key.
            akey = (account_key(next(iter(pre['accounts'])))
                    if len(pre['accounts']) == 1 else '')
        rep.rows.append(PositionRow(
            broker='ib', account=acct, account_key=akey, symbol=sym,
            raw_symbol=raw,
            quantity=qty, currency=cur, cost=cost, cost_currency=cur,
            cost_kind=COST_LOTS if cost is not None else COST_NONE,
            market_value=value, as_of=as_of, asset_type=atype,
            multiplier=float(mult or 0.0), source=name))
    if not seen_section:
        raise PositionsReportError(
            f"{name}: an Interactive Brokers statement with no Open "
            f"Positions section — no positions to read")
    _uncovered = sorted(k for k in lot_keys if k not in summary_keys)
    if _uncovered:
        _syms = ', '.join(f"{k[1] or '(no symbol)'} {k[2]}".strip()
                          for k in _uncovered[:5])
        _more = (f" and {len(_uncovered) - 5} more"
                 if len(_uncovered) > 5 else '')
        raise PositionsReportError(
            f"{lot_keys[_uncovered[0]]}: Open Positions Lot rows of "
            f"{_syms}{_more} have no Summary row — this reader takes "
            f"each position from its Summary row, so the snapshot would "
            f"leave them out. Export the statement with the Summary "
            f"level (the default Activity Statement) and use that file")
    if lots:
        rep.notes.append(f"{name}: {lots} Open Positions Lot row(s) not "
                         f"read (the Summary rows carry the position)")
    if skipped_cash:
        rep.notes.append(f"{name}: {skipped_cash} cash/forex position "
                         f"row(s) left out")
    return rep


# The Open Positions columns the IB reader reads (each must be there
# once: _refuse_duplicate_labels).
_IB_READ = ('DataDiscriminator', 'Asset Category', 'Currency', 'Symbol',
            'Quantity', 'Cost Basis', 'Value', 'Mult', 'Account')


# ------------------------------------------------------------ RBC

_RBC_COST_TOTAL_RE = re.compile(r'\bbook\s+(cost|value)\b', re.I)
_RBC_COST_AVG_RE = re.compile(r'\baverage\s+(cost|price)\b', re.I)
# A per-share qualifier on a cost or price label: the figure is per
# share, whatever else the label says ('Book Cost Per Share', 'Average
# Book Cost', 'Book Cost/Share' — audit finding 46: read as totals).
_RBC_PER_SHARE_RE = re.compile(
    r'\bper\s+share\b|/\s*share\b|\baverage\b|\bavg\b', re.I)
_RBC_COST_WORD_RE = re.compile(r'\bcost\b|\bbook\s+value\b', re.I)
_RBC_PRICE_WORD_RE = re.compile(r'\bprice\b', re.I)
# A price is a cost only when averaged ('Average Price', 'Avg Price'):
# 'Price Per Share' is the market price, not a cost column.
_RBC_AVERAGE_RE = re.compile(r'\baverage\b|\bavg\b', re.I)
_RBC_TOTAL_WORD_RE = re.compile(r'\btotal\b', re.I)
_RBC_MV_RE = re.compile(r'\bmarket\s+value\b', re.I)
# A currency a cost label names: an upper-case code anywhere ("Book
# Value USD"), or one in any case in brackets ("Book Value (usd)") —
# known codes only (lib/markets), so a word is never read as one.
_LABEL_CODE_RE = re.compile(r'(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])')
_LABEL_BRACKET_RE = re.compile(r'[(\[]\s*([A-Za-z]{3})\s*[)\]]')


def _label_currency(name: str, label: str) -> Optional[str]:
    """The currency a cost column's label names, or None. Two different
    ones are refused (backlog TJ-0531: "Book Value (usd)" was read in
    the row's currency, an explicit EUR relabelled CAD)."""
    from taxjson.lib.markets import fiat_currencies
    fiat = fiat_currencies()
    found = []
    for m in list(_LABEL_CODE_RE.finditer(label)) \
            + list(_LABEL_BRACKET_RE.finditer(label)):
        c = m.group(1).upper()
        if c in fiat and c not in found:
            found.append(c)
    if len(found) > 1:
        raise PositionsReportError(
            f"{name}: cost column {label!r} names {' and '.join(found)} "
            f"— refusing to guess which currency the cost is in; rename "
            f"the column")
    return found[0] if found else None


def _rbc_cost_label_error(name: str, label: str) -> PositionsReportError:
    """A cost column whose label says neither a total nor a per-share
    figure: refused, never guessed (a per-share cost read as a total
    understates the position's cost by its share count)."""
    return PositionsReportError(
        f"{name}: cost column {label!r} is neither a total (Book Cost, "
        f"Book Value) nor a per-share figure (Average Cost, Book Cost "
        f"Per Share) — refusing to guess which; rename the column to "
        f"one of these")


def _read_rbc(path: Path, text: str) -> PositionsReport:
    from taxjson.lib.brokerages.base import shown_name
    from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
    name = shown_name(path)
    rows = _rows(text)
    first = _first_text(rows)
    as_of = _preamble_date(first)
    hpos = None
    for i, r in enumerate(rows):
        labels = [c.replace('﻿', '').strip().lower() for c in r]
        if 'symbol' in labels and any(l.startswith('quantity')
                                      for l in labels):
            hpos = i
            break
    if hpos is None:
        raise PositionsReportError(
            f"{name}: an RBC Holdings Export with no header row naming "
            f"Symbol and Quantity — refusing to guess its columns")
    header = [c.replace('﻿', '').strip() for c in rows[hpos]]
    low = [h.lower() for h in header]
    col: Dict[str, int] = {}
    for i, h in enumerate(low):
        if h == 'symbol':
            col.setdefault('symbol', i)
        elif h.startswith('quantity'):
            col.setdefault('qty', i)
        elif h == 'currency':
            col.setdefault('cur', i)
        elif h.startswith('account'):
            col.setdefault('acct', i)
        elif _RBC_MV_RE.search(h):
            col.setdefault('mv', i)
        elif (_RBC_PER_SHARE_RE.search(h)
              and (_RBC_COST_WORD_RE.search(h)
                   or (_RBC_PRICE_WORD_RE.search(h)
                       and _RBC_AVERAGE_RE.search(h)))):
            # Per share before the total match: 'Book Cost Per Share'
            # names a book cost but is not the position's total.
            if _RBC_TOTAL_WORD_RE.search(h):
                raise _rbc_cost_label_error(name, header[i])
            col.setdefault('avg', i)
        elif _RBC_COST_TOTAL_RE.search(h):
            col.setdefault('cost', i)
        elif _RBC_COST_AVG_RE.search(h):
            col.setdefault('avg', i)
        elif _RBC_COST_WORD_RE.search(h):
            raise _rbc_cost_label_error(name, header[i])
        elif h in ('name', 'description', 'security', 'symbol description'):
            col.setdefault('name', i)
        elif h in ('product', 'product type', 'asset class'):
            col.setdefault('product', i)
    if 'cur' not in col:
        raise PositionsReportError(
            f"{name}: an RBC Holdings Export with no Currency column — "
            f"cannot tell a TSX listing (.TO) from a US one (.US)")
    # A column it reads that the header has twice is refused, never the
    # first one taken (backlog TJ-0530).
    _refuse_duplicate_labels(name, header, [header[i] for i in col.values()],
                             hpos + 1)
    _label_cur = {k: _label_currency(name, header[col[k]])
                  for k in ('cost', 'avg') if k in col}

    def _cost_cur(key: str, row_cur: str) -> str:
        return _label_cur.get(key) or row_cur

    ex = RbcBrokerage()
    rep = PositionsReport(path=path, broker='rbc_direct',
                          kind=kind_label('rbc_holdings'), as_of=as_of)
    if not as_of:
        rep.notes.append(f"{name}: no date in the Holdings Export "
                         f"preamble — the positions have no as-of date")
    if 'cost' not in col and 'avg' not in col:
        rep.notes.append(f"{name}: no book-cost column — quantities only "
                         f"(market value is never read as a cost)")
    accts = set()
    for lineno, r in enumerate(rows[hpos + 1:], hpos + 2):
        def g(key, _r=r):
            i = col.get(key)
            return _r[i].strip() if i is not None and i < len(_r) else ''
        raw = g('symbol')
        where = f"{name}:{lineno}"
        if not raw and g('qty') and not re.search(
                r'\bcash\b|\btotal', f"{g('name')} {g('product')}", re.I):
            # A row with a quantity and no symbol is a security the
            # snapshot would be short by, unless it is cash or a total
            # (backlog TJ-0529).
            try:
                _q = _num(g('qty'), field='Quantity', where=where,
                          blank_ok=True)
            except PositionsReportError:
                _q = None           # a footer's text, not a quantity
            if _q:
                raise _blank_symbol_error(where, _q, g('name'))
        if not raw or raw.upper() == 'TOTAL':
            continue
        if raw.upper() == 'CASH' and not (
                'product' in col and g('product')
                and not re.search(r'\bcash\b', g('product'), re.I)):
            # A cash balance -- unless the row's Product names a
            # security (Equities, ETFs): CASH is also a listed ticker,
            # and that holding was dropped (backlog TJ-0968).
            continue
        qty = _num(g('qty'), field='Quantity', where=where, blank_ok=False)
        if abs(qty) < 1e-12:
            continue
        _safe_cell(raw, field='Symbol', where=where, spaces_ok=True)
        cur = g('cur').upper()
        if not cur:
            raise PositionsReportError(f"{where}: {raw}: no Currency")
        _check_currency(cur, field='Currency', where=where)
        opt = (ex.parse_option_from_description(g('name'))
               or ex.parse_option_from_description(raw))
        if opt:
            from taxjson.lib.brokerages.base import BrokerageParseError
            try:
                occ = ex.format_occ_symbol(opt['right'], opt['base'],
                                           opt['expiry'], opt['strike'],
                                           where=where)
            except BrokerageParseError as e:
                raise PositionsReportError(str(e)) from None
            sym = ex.apply_currency_suffix(occ, cur)
        else:
            sym = ex.apply_currency_suffix(raw.upper(), cur)
        _safe_cell(sym, field='Symbol', where=where)
        cost = None
        cost_cur = cur
        if 'cost' in col and g('cost'):
            cost = _num(g('cost'), field=header[col['cost']], where=where,
                        blank_ok=True)
            cost_cur = _cost_cur('cost', cur)
        elif 'avg' in col and g('avg'):
            avg = _num(g('avg'), field=header[col['avg']], where=where,
                       blank_ok=True)
            if avg is not None:
                cost = avg * abs(qty) * (100.0 if opt else 1.0)
                cost_cur = _cost_cur('avg', cur)
        mv = (_num(g('mv'), field=header[col['mv']], where=where,
                   blank_ok=True) if 'mv' in col else None)
        acct = mask_account(g('acct')) if 'acct' in col else ''
        akey = account_key(g('acct')) if 'acct' in col else ''
        if acct:
            accts.add(acct)
        rep.rows.append(PositionRow(
            broker='rbc_direct', account=acct, account_key=akey,
            symbol=sym, raw_symbol=raw,
            quantity=qty, currency=cur, cost=cost, cost_currency=cost_cur,
            cost_kind=COST_AVERAGE if cost is not None else COST_NONE,
            market_value=mv, as_of=as_of,
            asset_type='option' if opt else 'stock',
            multiplier=100.0 if opt else 1.0, source=name))
    rep.accounts = sorted(accts)
    return rep


# ------------------------------------------------------------ TOML

_VENUE_SFX_RE = re.compile(r'^([A-Za-z0-9]+)\.[A-Za-z]{2,3}$')
_ISO_DAY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _iso_day(v) -> str:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, _date):
        return v.isoformat()
    s = str(v or '').strip()[:10]
    if not _ISO_DAY_RE.match(s):
        return ''
    try:
        _date.fromisoformat(s)      # 2025-02-30 is digit-shaped, not a day
    except ValueError:
        return ''
    return s


def toml_as_of(meta: Any, name: str) -> str:
    """A holdings TOML's date: [meta] as_of, else the day of [meta]
    generated_at ('' when it has neither). PositionsReportError naming
    the field when one is given but is not a calendar day (2025-02-30):
    the date decides which books the file is compared with. generated_at
    is a note of when the file was made: only a date-shaped value that is
    no calendar day is refused; other text in it is ignored, as before."""
    meta = meta if isinstance(meta, dict) else {}
    for key in ('as_of', 'generated_at'):
        v = meta.get(key)
        if v in (None, '') or _iso_day(v):
            continue
        if key == 'as_of' or _ISO_DAY_RE.match(str(v).strip()[:10]):
            raise PositionsDateError(
                f"{name}: [meta] {key} {str(v)!r} is not a calendar "
                f"date (YYYY-MM-DD)")
    return _iso_day(meta.get('as_of')) or _iso_day(meta.get('generated_at'))


def _read_toml(path: Path) -> PositionsReport:
    from taxjson.lib.brokerages.base import shown_name
    from taxjson.lib.tomlcompat import tomllib
    name = shown_name(path)
    if tomllib is None:
        raise PositionsReportError("reading a holdings TOML needs tomllib "
                                   "(Python 3.11+) or tomli")
    try:
        doc = tomllib.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, UnicodeDecodeError) as e:
        raise PositionsReportError(f"{name}: cannot parse it: {e}") from e
    holdings = doc.get('holding') if isinstance(doc, dict) else None
    from taxjson.lib.holdings_dir import empty_snapshot
    if holdings is None and empty_snapshot(doc):
        holdings = []               # the account holds nothing (#46)
    if not isinstance(holdings, list):
        raise PositionsReportError(
            f"{name} has no [[holding]] array (a holdings file has "
            f"[[holding]] tables)")
    meta = doc.get('meta') if isinstance(doc.get('meta'), dict) else {}
    count = meta.get('holdings_count')
    if count is not None and (isinstance(count, bool)
                              or not isinstance(count, int)
                              or count != len(holdings)):
        # The writer's own row count (taxjson-fetch, taxjson-export): a
        # file with fewer [[holding]] tables than it declares is cut
        # short (a copy that stopped part-way, a hand edit) — refused,
        # not compared as if complete (audit 2026-10-24 B37).
        raise PositionsSnapshotError(
            f"{name}: [meta] holdings_count = {count!r} but the file has "
            f"{len(holdings)} [[holding]] table(s) — the snapshot is "
            f"incomplete or edited; download it again (an unreadable "
            f"file is never compared as if complete)")
    as_of = toml_as_of(meta, name)
    meta_acct = mask_account(meta.get('broker_account')
                             or meta.get('account'))
    meta_key = account_key(meta.get('broker_account')
                           or meta.get('account'))
    rep = PositionsReport(path=path, broker='toml',
                          kind=kind_label('holdings_toml'), as_of=as_of)
    accts = set([meta_acct] if meta_acct else [])

    def _row(n: int, h: Any) -> None:
        where = f"{name}: [[holding]] #{n}"
        if not isinstance(h, dict):
            raise PositionsReportError(f"{where} is not a table")
        atype_raw = str(h.get('asset_type') or '').strip().lower()
        if atype_raw == 'cash':
            return
        sym = str(h.get('symbol') or '').strip()
        qraw = h.get('quantity')
        if qraw is None or (isinstance(qraw, str) and not qraw.strip()):
            raise PositionsReportError(
                f"{where} ({sym or '?'}): no quantity — fix the file (an "
                f"unreadable row is never skipped)")
        if isinstance(qraw, bool):
            raise PositionsReportError(
                f"{where} ({sym or '?'}): quantity {qraw!r} is not a "
                f"number")
        try:
            qty = float(qraw)
        except (TypeError, ValueError):
            raise PositionsReportError(
                f"{where} ({sym or '?'}): quantity {qraw!r} is not a "
                f"number") from None
        if not math.isfinite(qty):
            raise PositionsReportError(f"{where} ({sym or '?'}): quantity "
                                       f"is not finite")
        if not sym:
            if abs(qty) > 1e-12:
                raise PositionsReportError(
                    f"{where}: quantity {qty:g} but no symbol — it cannot "
                    f"be compared; fix the file")
            return
        if abs(qty) <= 1e-12:
            return
        _safe_cell(sym, field='symbol', where=where)
        for key in ('currency', 'cost_currency'):
            if isinstance(h.get(key), str):
                _safe_cell(h[key].strip(), field=key, where=f"{where} ({sym})")
        if isinstance(h.get('acquired'), str):
            _safe_cell(h['acquired'].strip(), field='acquired',
                       where=f"{where} ({sym})", spaces_ok=True)
        if atype_raw == 'crypto' and _VENUE_SFX_RE.match(sym):
            sym = _VENUE_SFX_RE.match(sym).group(1)

        def _money(key):
            v = h.get(key)
            if v is None or (isinstance(v, str) and not v.strip()):
                return None
            if isinstance(v, bool):
                raise PositionsReportError(f"{where} ({sym}): {key} "
                                           f"{v!r} is not a number")
            try:
                f = float(v)
            except (TypeError, ValueError):
                raise PositionsReportError(f"{where} ({sym}): {key} "
                                           f"{v!r} is not a number") from None
            if not math.isfinite(f):
                raise PositionsReportError(f"{where} ({sym}): {key} is "
                                           f"not finite")
            return f
        atype = ('crypto' if atype_raw == 'crypto'
                 else _asset_type_of(sym))
        mult = 0.0
        for key in ('multiplier', 'contract_multiplier'):
            try:
                m = _money(key)
            except PositionsReportError as e:
                # A contract size that is no number: the file is
                # refused, as for a size of 0 (audit 2026-10-25).
                raise PositionsSnapshotError(str(e)) from None
            if m is None:
                continue
            if m <= 0:
                # A contract size is a positive count of units; a
                # negative one turned a long option's cost negative
                # (audit 2026-10-24 B36). Refused, like taxjson-export's
                # own read-back.
                raise PositionsSnapshotError(
                    f"{where} ({sym}): {key} {m:g} is not a positive "
                    f"number — fix the file (an unreadable row is never "
                    f"skipped)")
            mult = m
            break
        # taxjson-export writes `contract_multiplier_unknown = true` when
        # its inputs disagree on an option's contract size (audit
        # 2026-10-24 B21): the size is unknown, never the equity 100.
        unknown = h.get('contract_multiplier_unknown')
        if unknown is not None and not isinstance(unknown, bool):
            raise PositionsSnapshotError(
                f"{where} ({sym}): contract_multiplier_unknown "
                f"{unknown!r} is not true or false")
        if unknown and mult:
            raise PositionsSnapshotError(
                f"{where} ({sym}): contract_multiplier_unknown = true "
                f"beside a contract size of {mult:g} — the file "
                f"contradicts itself; keep one of the two")
        cost = None
        for key in ('total_cost', 'book_cost', 'cost_basis'):
            cost = _money(key)
            if cost is not None:
                break
        if cost is None:
            aep = _money('average_entry_price')
            if aep is not None:
                # Questrade's averageEntryPrice (the `tjs fetch
                # --positions` snapshot) is a price per SHARE: an option
                # contract is `multiplier` shares, else the equity
                # option's 100 (as the RBC reader and sanity's per-share
                # view take it; 2026-10 audit #28).
                if unknown and atype == 'option':
                    # A per-share price times an unknown contract size
                    # is no cost: left unknown rather than invented.
                    cost = None
                else:
                    cost = aep * abs(qty) * (
                        mult if mult else 100.0 if atype == 'option'
                        else 1.0)
            else:
                # taxjson-export's own cost_per_share is per HOLDING
                # UNIT (per contract for an option: its file header
                # says so), so it is never scaled.
                cps = _money('cost_per_share')
                if cps is not None:
                    cost = cps * abs(qty)
        # taxjson-export's base-currency companion of the cost (the
        # books' cost at the acquisition dates' rates): kept beside the
        # native cost, never in its place (backlog TJ-0990).
        base_cost = _money('base_total_cost')
        base_cur = str(h.get('base_currency') or '').strip().upper()
        if base_cost is None or not base_cur:
            base_cost, base_cur = None, ''
        lot = h.get('acquired')
        lot_date = _iso_day(lot) if lot not in (None, '') else ''
        if lot not in (None, '') and not lot_date:
            raise PositionsDateError(f"{where} ({sym}): acquired "
                                     f"{lot!r} is not a calendar date "
                                     f"(YYYY-MM-DD)")
        ck = str(h.get('cost_kind') or '').strip().lower()
        if ck and ck not in ('average', 'lots'):
            raise PositionsReportError(f"{where} ({sym}): cost_kind "
                                       f"{ck!r} must be \"average\" or "
                                       f"\"lots\"")
        kind = (COST_NONE if cost is None
                else COST_LOTS if ck == 'lots' or (not ck and lot_date)
                else COST_AVERAGE)
        cur = str(h.get('currency') or '').strip().upper()
        acct = mask_account(h.get('account')) or meta_acct
        akey = account_key(h.get('account')) or meta_key
        if acct:
            accts.add(acct)
        rep.rows.append(PositionRow(
            broker='toml', account=acct, account_key=akey, symbol=sym,
            raw_symbol=sym,
            quantity=qty, currency=cur, cost=cost,
            cost_currency=(str(h.get('cost_currency') or '').strip()
                           .upper() or cur),
            cost_kind=kind, market_value=_money('market_value'),
            as_of=as_of, asset_type=atype, lot_date=lot_date,
            multiplier=float(mult), source=name, base_cost=base_cost,
            base_currency=base_cur))

    # Every row is read before a refusal of one of its fields is
    # raised: a malformed cost on row 1 hid a contract size of -50 or
    # an impossible `acquired` on row 3, and `tjs sanity`, which skips
    # only the cost for the first kind, compared the file (audit
    # 2026-10-25 brokers-inputs#1). A refusal of the whole file wins.
    first: Optional[PositionsReportError] = None
    for n, h in enumerate(holdings, 1):
        try:
            _row(n, h)
        except (PositionsDateError, PositionsSnapshotError):
            raise
        except PositionsReportError as e:
            if first is None:
                first = e
    if first is not None:
        raise first
    rep.accounts = sorted(accts)
    return rep


# ------------------------------------------------------------ entry

def read_positions(path) -> PositionsReport:
    """Read any supported positions report (see the module docstring).
    Raises PositionsReportError naming the masked file when the file
    is not one, or cannot be read safely."""
    from taxjson.lib.brokerages.base import shown_name
    path = Path(path)
    if not path.is_file():
        raise PositionsReportError(f"{shown_name(path)}: no such file")
    if path.suffix.lower() == '.toml':
        return _read_toml(path)
    text = _decode(path)
    kind = text_kind(text)
    if kind == 'ib_open_positions':
        return _read_ib(path, text)
    if kind == 'rbc_holdings':
        return _read_rbc(path, text)
    from taxjson.lib.brokerages.detect import looks_like_ib_text
    if looks_like_ib_text(text):
        raise PositionsReportError(
            f"{shown_name(path)}: an Interactive Brokers statement with no "
            f"Open Positions section — no positions to read")
    raise PositionsReportError(
        f"{shown_name(path)}: not a positions report taxjson reads "
        f"(supported: {', '.join(kind_label(k) for k in SUPPORTED)}; "
        f"Questrade, Webull, Coinbase and Kraken have none — use a "
        f"[[holding]] TOML)")
