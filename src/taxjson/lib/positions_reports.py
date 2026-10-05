"""Broker positions reports — what a broker says you HOLD on a date.

A positions (holdings) report is not activity: it lists each position
with its quantity and, when the broker states it, its book cost. Two
commands read one: `taxjson sanity` compares it with the books, and
`taxjson opening` turns it into OPENING rows. `taxjson run` never books
one (a positions-only file dropped into inputs/<account>/ is skipped
with a note — lib/brokerages/detect).

Every reader returns the same row (PositionRow): the broker account
MASKED, the symbol spelled exactly as that broker's TRADE parser spells
it (ticker.map is the caller's job, as for the books), the signed
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
  holdings_toml       a `[[holding]]` TOML (the file `taxjson sanity`
                      has always read; taxjson-fetch's Questrade
                      snapshot; other tools' exports): symbol,
                      quantity, currency, total_cost (or book_cost /
                      cost_basis), else average_entry_price x quantity;
                      `cost_kind = "average" | "lots"`; `acquired =
                      "YYYY-MM-DD"` dates THAT lot (several tables of
                      one symbol are several lots); as of [meta]
                      `as_of`, else the date of `generated_at`.

Unsupported (UNSUPPORTED below says why for each): Questrade, Webull,
Coinbase and Kraken have no positions export the parser code knows.
"""

import csv
import io
import math
import re
from dataclasses import dataclass, field
from datetime import date as _date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
                 "`taxjson fetch --positions` (the taxjson-fetch plugin) "
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


# A symbol or currency cell is copied into .tt lines (`taxjson opening`)
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
            continue
        if r[1] != 'Data' or hm is None:
            continue            # Total / SubTotal rows

        def g(col, _r=r):
            i = hm.get(col)
            return _r[i].strip() if i is not None and i < len(_r) else ''

        where = f"{name}:{lineno}"
        discr = g('DataDiscriminator')
        if discr and discr != 'Summary':
            lots += 1
            continue
        cat = g('Asset Category')
        cur = ibx._norm_ccy(g('Currency'))
        raw = g('Symbol')
        if cat in ('Forex', 'Cash') or not raw:
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
        if mult is None:
            info = (fii.get((cat, raw))
                    or fii.get((cat, re.sub(r'\s+', ' ', raw))) or {})
            mult = info.get('mult')
        acct = mask_account(g('Account')) if 'Account' in hm else (
            accounts[0] if len(accounts) == 1 else '')
        rep.rows.append(PositionRow(
            broker='ib', account=acct, symbol=sym, raw_symbol=raw,
            quantity=qty, currency=cur, cost=cost, cost_currency=cur,
            cost_kind=COST_LOTS if cost is not None else COST_NONE,
            market_value=value, as_of=as_of, asset_type=atype,
            multiplier=float(mult or 0.0), source=name))
    if not seen_section:
        raise PositionsReportError(
            f"{name}: an Interactive Brokers statement with no Open "
            f"Positions section — no positions to read")
    if lots:
        rep.notes.append(f"{name}: {lots} Open Positions Lot row(s) not "
                         f"read (the Summary rows carry the position)")
    if skipped_cash:
        rep.notes.append(f"{name}: {skipped_cash} cash/forex position "
                         f"row(s) left out")
    return rep


# ------------------------------------------------------------ RBC

_RBC_COST_TOTAL_RE = re.compile(r'\bbook\s+(cost|value)\b', re.I)
_RBC_COST_AVG_RE = re.compile(r'\baverage\s+(cost|price)\b', re.I)
_RBC_MV_RE = re.compile(r'\bmarket\s+value\b', re.I)
_LABEL_CUR_RE = re.compile(r'\b(CAD|USD)\b')


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
    col = {}
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
        elif _RBC_COST_TOTAL_RE.search(h):
            col.setdefault('cost', i)
        elif _RBC_COST_AVG_RE.search(h):
            col.setdefault('avg', i)
        elif h in ('name', 'description', 'security', 'symbol description'):
            col.setdefault('name', i)
    if 'cur' not in col:
        raise PositionsReportError(
            f"{name}: an RBC Holdings Export with no Currency column — "
            f"cannot tell a TSX listing (.TO) from a US one (.US)")

    def _cost_cur(key: str, row_cur: str) -> str:
        m = _LABEL_CUR_RE.search(header[col[key]])
        return m.group(1) if m else row_cur

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
        if not raw or raw.upper() in ('CASH', 'TOTAL'):
            continue
        where = f"{name}:{lineno}"
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
            sym = ex.apply_currency_suffix(
                ex.format_occ_symbol(opt['right'], opt['base'],
                                     opt['expiry'], opt['strike']), cur)
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
        if acct:
            accts.add(acct)
        rep.rows.append(PositionRow(
            broker='rbc_direct', account=acct, symbol=sym, raw_symbol=raw,
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
    s = str(v or '').strip()
    return s[:10] if _ISO_DAY_RE.match(s[:10]) else ''


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
    if not isinstance(holdings, list):
        raise PositionsReportError(
            f"{name} has no [[holding]] array (a holdings file has "
            f"[[holding]] tables)")
    meta = doc.get('meta') if isinstance(doc.get('meta'), dict) else {}
    as_of = _iso_day(meta.get('as_of')) or _iso_day(meta.get('generated_at'))
    meta_acct = mask_account(meta.get('broker_account')
                             or meta.get('account'))
    rep = PositionsReport(path=path, broker='toml',
                          kind=kind_label('holdings_toml'), as_of=as_of)
    accts = set([meta_acct] if meta_acct else [])
    for n, h in enumerate(holdings, 1):
        where = f"{name}: [[holding]] #{n}"
        if not isinstance(h, dict):
            raise PositionsReportError(f"{where} is not a table")
        atype_raw = str(h.get('asset_type') or '').strip().lower()
        if atype_raw == 'cash':
            continue
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
            continue
        if abs(qty) <= 1e-12:
            continue
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
        cost = None
        for key in ('total_cost', 'book_cost', 'cost_basis'):
            cost = _money(key)
            if cost is not None:
                break
        if cost is None:
            aep = _money('average_entry_price')
            if aep is None:
                aep = _money('cost_per_share')
            if aep is not None:
                cost = aep * abs(qty)
        lot = h.get('acquired')
        lot_date = _iso_day(lot) if lot not in (None, '') else ''
        if lot not in (None, '') and not lot_date:
            raise PositionsReportError(f"{where} ({sym}): acquired "
                                       f"{lot!r} is not a YYYY-MM-DD date")
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
        if acct:
            accts.add(acct)
        atype = ('crypto' if atype_raw == 'crypto'
                 else _asset_type_of(sym))
        mult = _money('multiplier') or _money('contract_multiplier') or 0.0
        rep.rows.append(PositionRow(
            broker='toml', account=acct, symbol=sym, raw_symbol=sym,
            quantity=qty, currency=cur, cost=cost,
            cost_currency=(str(h.get('cost_currency') or '').strip()
                           .upper() or cur),
            cost_kind=kind, market_value=_money('market_value'),
            as_of=as_of, asset_type=atype, lot_date=lot_date,
            multiplier=float(mult), source=name))
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
