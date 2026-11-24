from taxjson.lib.stage_msg import emit_line
from taxjson.lib.out import END_OF_RUN_MARK
import csv
import io
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, NamedTuple

from taxjson.lib.row_types import STOCK_DIVIDEND
from taxjson.lib.cross_listings import _BRW_JOURNAL_RE
from taxjson.lib.symbol_codes import _get_desc_key
from taxjson.parsers.protocol import Outcome, Row, builds
from taxjson.lib.brokerages.base import (BaseBrokerage, BrokerageParseError,
                                         is_taxable_account_type, plan_shelter_of,
                                         check_account_plans,
                                         check_account_plans_across,
                                         names_underlying,
                                         ticker_map_joins,
                                         answered_rename_note,
                                         rename_how, rename_reach,
                                         declared_rename_where,
                                         ticker_map_names,
                                         ticker_map_renames,
                                         DESC_NUMBER_RE,
                                         canonical_ca_listing,
                                         decode_broker_text,
                                         desc_number,
                                         income_facts_from_description,
                                         is_roc_description,
                                         parse_strict_number,
                                         refuse_pasted_overlap,
                                         shown_name, zero_units_refusal)
from taxjson.lib.markets_data import known_suffixes
from taxjson.parsers.toolbox.assets import QTY_ZERO
from taxjson.parsers.toolbox.money import money_close
from taxjson.parsers.toolbox.expiry import (non_option_expiry_booking_date,
                                            option_expiry_booking_date)
from taxjson.model import money as M
from taxjson.parsers.toolbox.descriptions import (PIL_DESC_RE, REINV_CUR,
                                                  _parse_div_qty_rate,
                                                  REINV_CUR_RE,
                                                  REINV_PRICE_RE,
                                                  reinvest_identity_error,
                                                  reinvest_row_price)


_DATE_FMT = "%Y-%m-%d %I:%M:%S %p"
# Every date shape a Questrade export reaches the parser in: the web
# CSV's 12-hour clock, and the ISO forms an .xlsx export turns into
# after pandas ("2025-12-31 00:00:00"), with or without the time.
_DATE_FMTS = (_DATE_FMT, "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
              "%Y-%m-%d")

# The export's columns — ALL required. A renamed/missing column used to
# read as 0 (Commission renamed: every commission vanished; Gross Amount
# renamed: every trade's cost became its commission) or as a default
# currency ('USD') while the parse "succeeded".
_QT_COLUMNS = ('Transaction Date', 'Settlement Date', 'Action', 'Symbol',
               'Description', 'Quantity', 'Price', 'Gross Amount',
               'Commission', 'Net Amount', 'Currency', 'Account #',
               'Activity Type', 'Account Type')


def missing_columns(cells) -> List[str]:
    """The _QT_COLUMNS a header row (the export's FIRST row, as
    _read_qt_rows reads it) lacks. Broker detection uses this same test
    (lib/brokerages/detect.py)."""
    have = {(c or '').strip() for c in cells}
    return [c for c in _QT_COLUMNS if c not in have]


# Registered-plan markers in the Account Type column (a warning that
# matters only in a TAXABLE account stays quiet for these).

# A stock-split distribution (Action DIS) reports the NEW shares in Quantity
# and the held count as "... ON <held> SHS ...". Used to recover the ratio.
_SPLIT_ON_SHS_RE = re.compile(r'\bON\s+' + DESC_NUMBER_RE + r'\s+SH',
                              re.IGNORECASE)

# A STOCK DIVIDEND row (split-share corps like TDb pay non-cash share
# distributions): DIS / 'Dividends' with 'STK DIV' in the description
# and the DELIVERED share count in Quantity. Distinct from STK SPLIT
# (ratio-based) and from cash DIV rows (zero Quantity).
# A US-listed security bought in a CAD-only account (RESP, some RRSPs):
# Questrade settles in CAD and writes the rate into the description —
# Price and Gross Amount are in USD, Net Amount is the CAD actually paid,
# and the Currency column says CAD (the SETTLEMENT currency).
_FX_SETTLED_RE = re.compile(r'EXCHANGE RATE\s+([0-9]+(?:\.[0-9]+)?)', re.IGNORECASE)

# The fraction with every comma captured, judged by desc_number: '1,5'
# read as 1 and '0,5' as no fraction (re-audit A2-1058).
_CIL_RE = re.compile(r'CASH\s+IN\s+LIEU\s+OF\s+' + DESC_NUMBER_RE, re.I)
_STK_DIV_RE = re.compile(r'\bSTK\.?\s+DIV\b|\bSTOCK\s+DIVIDEND\b',
                         re.IGNORECASE)

# Questrade emits internal codes like "S098765" or "A12345" for some
# dividend rows (typically post-transfer-in, before the security is
# linked to its real ticker). Pattern: one letter followed by digits.
_INTERNAL_CODE_RE = re.compile(r'^[A-Z]\d+$')

# Questrade transfer-in rows carry the cost basis (ACB) only in the
# description — "TRANSFER BOOK VALUE <amount>" — no numeric column holds
# it. Extract it so a parsed transfer establishes the right pool size.
_BOOK_VALUE_RE = re.compile(
    r'BOOK\s+VALUE\s+([\d,]+(?:\.\d+)?)', re.IGNORECASE
)
# FCH fee rows name the security only in the description, after the
# share count: "ADR CUSTODY FEE 100 SHARES ABCD RECORD DATE 1/2/25" (Questrade's
# wording; audit R1-77 — only the '# SHARES TKR' placeholder form
# used to match, and the real fee landed on CASH).
_FEE_SHARES_TICKER_RE = re.compile(
    r'(?:#|\b\d[\d,]*(?:\.\d+)?)\s*SHARES\s+([A-Z][A-Z0-9.\-]*)',
    re.IGNORECASE)
# A quantity-bearing zero-cash DIS row of a spinoff / rights chain — a
# corporate-action leg taxjson-corp-actions books (same marker it uses).
_QT_CA_LEG_RE = re.compile(r'\b(SPINOFF|RTS\s+DIST|RIGHTS\s+DIST)\b',
                           re.IGNORECASE)
# A BRW listing journal's book value: "... JOURNAL POSITION FROM CAD BOOK
# VALUE: $2468.13 CNV@ 1.3579" (carried as evidence, like RBC's).
_BRW_BOOK_VALUE_RE = re.compile(
    r'BOOK\s+VALUE:?\s*\$?\s*([\d,]+(?:\.\d+)?)', re.IGNORECASE)
# Every comma captured, judged by desc_number: 'CNV@ 1,3579' read as a
# rate of 1 (re-audit A2-0278).
_BRW_CNV_RE = re.compile(r'\bCNV\s*@\s*' + DESC_NUMBER_RE, re.IGNORECASE)
# A zero-cash row stating a book-cost change ("... RETURN OF CAPITAL
# ADJUSTMENT TO BOOK COST $1.16"), the shape RBC exports.
_QT_BOOK_COST_RE = re.compile(r'\bADJUSTMENT\s+TO\s+BOOK\s+COST\b', re.I)
# A dividend Questrade posts NET of non-resident withholding.
_NONRES_NET_RE = re.compile(r'NON-?RES\w*\.?\s+TAX\s+WITH', re.IGNORECASE)


def _journal_root(symbol: str) -> str:
    """The security behind one listing line: SAMPLF.U.TO and SAMPLF.TO -> SAMPLF
    (a BRW journal moves units between the CAD and USD lines)."""
    from taxjson.lib.markets import strip_listing_suffix
    s = strip_listing_suffix((symbol or '').upper())
    return re.sub(r'\.U$', '', s)


# Questrade writes 'Buy'/'Sell' and upper-case codes (DIV, TF6, DIS, ...).
# An export in another case ('BUY', 'buy') was skipped as an unknown
# action and the sale opened a phantom short (audit R1-71).
_QT_ACTION_CANON = {'BUY': 'Buy', 'SELL': 'Sell'}

# The cash rows the position parser leaves out (recognized non-events)
# and its cash events book (`_qt_cash_kind`): deposits and withdrawals
# (pre-release review M8 — the FX-on-cash ledger v2 computed a Questrade
# account's figure without them). One list for both (TJ-2564).
_QT_CASH_MOVE_CODES = ('CON', 'DEP', 'EFT', 'EWD', 'WDR', 'CTR')
_QT_CASH_MOVE_TYPES = ('Deposits', 'Withdrawals', 'Contributions')


def _canon_action(raw: Optional[str]) -> str:
    a = (raw or '').strip()
    return _QT_ACTION_CANON.get(a.upper(), a.upper())


def _qt_cash_kind(action: str, atype: str) -> bool:
    """Whether a row of this action code (`_canon_action`) and Activity
    Type is one the file's cash events are read from: an FX conversion's
    leg (FXT), a deposit, withdrawal or contribution, a transfer journal
    (TF6), stock-lending income (LFJ). classify notes each (`note_cash`)
    whatever the parse makes of it; the finish reads its Quantity and Net
    Amount, which the parse itself never reads on most of them."""
    return (action == 'FXT' or atype == 'FX conversion'
            or action in _QT_CASH_MOVE_CODES or atype in _QT_CASH_MOVE_TYPES
            or action in ('TF6', 'LFJ') or atype == 'Transfers')


def _is_qt_header(row: Dict[str, Any]) -> bool:
    """The header repeated as a row: two exports concatenated."""
    return [(row.get(c) or '').strip() for c in _QT_COLUMNS[:5]] == \
        list(_QT_COLUMNS[:5])


def _read_qt_rows(path: Path, warn: bool = False) -> List[tuple]:
    """(line number, row) for every data row of a Questrade export. The
    header must carry every column in _QT_COLUMNS. Two exports pasted
    into one file (the header repeated) are read as they are, unless
    they share rows: refused (refuse_pasted_overlap)."""
    path = Path(path)
    text = decode_broker_text(path.read_bytes(), shown_name(path))
    reader = csv.DictReader(io.StringIO(text, newline=''))
    header = [h.strip() if h else h for h in (reader.fieldnames or [])]
    missing = missing_columns(header)
    if missing:
        raise BrokerageParseError(
            f"{shown_name(path)}: Questrade export is missing required "
            f"column(s) {', '.join(repr(c) for c in missing)} — "
            f"refusing to guess (a missing money column read as 0 "
            f"corrupts the return). Export Activity with the "
            f"standard English columns.")
    reader.fieldnames = header
    rows = []
    cut_meta: List[int] = []
    for lineno, row in enumerate(reader, 2):
        extra = [v for v in (row.get(None) or []) if (v or '').strip()]
        vals = [str(v or '').strip() for k, v in row.items()
                if k is not None]
        if not any(vals) and not extra:
            continue              # blank trailer line: not a row
        # A quote left open inside a field swallows the NEXT row(s) into
        # it and shifts the rest of the line into this row's money
        # columns (audit S062-07; RBC's twin is R1-84). Questrade and
        # pandas both escape quotes, so a hand-edited file is refused.
        spill = next((v for k, v in row.items() if k is not None
                      and ('\n' in (v or '') or '\r' in (v or ''))), None)
        if spill is not None or extra:
            raise BrokerageParseError(
                f"{shown_name(path)} line {lineno}: "
                + (f"a cell spans a line break ({spill[:60]!r})"
                   if spill is not None else
                   f"the row has {len(header) + len(row.get(None) or [])} "
                   f"cells for {len(header)} columns (extra: {extra[:3]})")
                + " — an unescaped quote or comma swallowed or shifted "
                  "cells; refusing to guess which row they belong to. "
                  "Fix the stray quote/comma in the CSV and re-run.")
        short = [k for k, v in row.items() if k is not None and v is None]
        _meta = ('Account #', 'Activity Type', 'Account Type')
        if short and all(k in _meta for k in short):
            # Only the trailing account columns are cut: the money and
            # the Action code are all there. Booked, but said (once per
            # file) — the account check and the Activity Type fallbacks
            # cannot see the row.
            if warn and not cut_meta:
                emit_line(f"warning: ATTENTION: {shown_name(path)} line {lineno}: the "
                      f"row has no {', '.join(short)} cell(s) (fewer cells "
                      f"than the header) — booked from its Action code; "
                      f"re-export the file if this is not a hand-made "
                      f"fixture.")
            cut_meta.append(lineno)
            short = []
        if short:
            # Fewer cells than the header (a cut-off row): the missing
            # trailing cells read as blank and the row was booked
            # (re-audit A2-1042, the Questrade half; Webull, RBC, Kraken
            # and IB refuse the same).
            raise BrokerageParseError(
                f"{shown_name(path)} line {lineno}: the row has "
                f"{len(header) - len(short)} cells for {len(header)} "
                f"columns (missing: {', '.join(short[:4])}) — a truncated "
                f"row; refusing to read the missing cells as blank. "
                f"Re-export the file.")
        rows.append((lineno, row))
    segs: List[tuple] = [(2, [])]
    for lineno, row in rows:
        if _is_qt_header(row):
            segs.append((lineno, []))
        else:
            segs[-1][1].append((lineno, tuple(
                str(v or '').strip() for k, v in row.items()
                if k is not None)))
    refuse_pasted_overlap(shown_name(path), segs, "the header")
    return rows


_QT_NUMBER_COLUMNS = ('Quantity', 'Price', 'Gross Amount', 'Commission',
                      'Net Amount')


def blank_symbol_twins(rows) -> List[Tuple[int, int]]:
    """(line of the row that names its symbol, line of the blank one) for
    each pair of `rows` ((line number, row) of one export) that are equal
    in every cell but Symbol, one blank and one not. Questrade's API
    returns a re-fetched activity with its symbol blank, and `tjs fetch`
    before backlog TJ-4955 kept it as a second row: the parser reads the
    blank row's contract from its description and books the trade twice.
    Numbers are compared by value (10 and 10.0), the rest as written.

    A blank-Symbol row with no such partner (a fee, an FX conversion, a
    transfer) is no pair. In a group each blank row is paired with one
    named row, in file order; a blank row beyond the named ones is not
    paired (it is then no copy of them). The header repeated is no row."""
    def cell(col, v):
        v = str(v or '').strip()
        if col in _QT_NUMBER_COLUMNS:
            try:
                d = Decimal(v)
            except (InvalidOperation, ValueError):
                return v
            return d if d.is_finite() else v
        return v
    groups: Dict[tuple, Tuple[List[int], List[int]]] = {}
    for lineno, row in rows:
        if _is_qt_header(row):
            continue
        key = tuple((k, cell(k, v)) for k, v in row.items()
                    if k is not None and k != 'Symbol')
        named, blank = groups.setdefault(key, ([], []))
        (named if (row.get('Symbol') or '').strip() else blank).append(lineno)
    return sorted(pair for named, blank in groups.values()
                  for pair in zip(named, blank))


_QT_TWINS_SHOWN = 3     # pairs a file's ATTENTION names; the rest counted


def _warn_blank_symbol_twins(shown: str, rows) -> None:
    """The file's ATTENTION for its `blank_symbol_twins`: the first
    _QT_TWINS_SHOWN pairs named and the rest counted (a re-run names
    them once these are repaired), nothing dropped (the file alone
    cannot tell a copy from a second fill whose symbol came back
    blank). In `tjs run`'s end-of-run list too (END_OF_RUN_MARK). It
    says what is so of every pair: a pair's rows may both be booked
    (an option, read from its description: the trade counts twice),
    neither (a row with no cash and no units), or the blank one refused
    further on (a share trade with no symbol)."""
    pairs = blank_symbol_twins(rows)
    if not pairs:
        return
    by_line = dict(rows)

    def what(named: int, blank: int) -> str:
        r = by_line[named]
        row = ' '.join(x for x in (
            (r.get('Transaction Date') or '').strip()[:10],
            (r.get('Action') or '').strip(),
            (r.get('Symbol') or '').strip()) if x)
        return f"({row}; the blank one is line {blank})"
    n = len(pairs)
    more = n - _QT_TWINS_SHOWN
    emit_line(
        f"warning: ATTENTION: {END_OF_RUN_MARK}{shown}: "
        + (f"lines {pairs[0][0]} and {pairs[0][1]} differ only by a blank "
           f"Symbol: probably one trade fetched twice {what(*pairs[0])}"
           if n == 1 else
           f"{n} pairs of rows differ only by a blank Symbol, each "
           f"probably one trade fetched twice: "
           + '; '.join(f"lines {a} and {b} {what(a, b)}"
                       for a, b in pairs[:_QT_TWINS_SHOWN])
           + (f"; and {more} more (named once these are repaired)"
              if more > 0 else ""))
        + ". Where both rows of a pair are booked, the position, the "
          "cash and the gain or loss count the trade twice. Run `tjs "
          "fetch` over those dates again: it drops the blank row when "
          "Questrade's download holds the trade once. If the pair "
          "stays, check the broker's statement: delete the blank row "
          "when it shows one trade, keep both when it shows two. Then "
          "`tjs run`.")


@dataclass
class QtAccountContext:
    """Identity maps learned from ALL of one account's Questrade exports
    (audit R1-67: a dividend or ROC under an internal code whose trade
    sits in LAST year's export stayed on a phantom code pool and the ROC
    became a s.40(3) gain). A lone file gets a context of its own."""
    files: List[str]
    # desc key -> {(symbol as exported, listing currency), ...}
    desc_to_ticker: Dict[str, set]
    # symbol as exported (upper) -> the listing currencies it trades in
    sym_curs: Dict[str, set]
    # listing symbol -> [(date, signed quantity)] from Trades/Transfers
    timelines: Dict[str, List[Tuple[str, float]]]
    messages: List[str] = field(default_factory=list)
    emitted: bool = False
    # Reversal pairing across ALL of the account's exports (see
    # _plan_qt_reversals): file -> line numbers of originals a reversal
    # cancels (every overlapping copy), and of the reversals paired.
    rev_drop: Dict[str, set] = field(default_factory=dict)
    rev_paired: Dict[str, set] = field(default_factory=dict)
    # Keys learned from Transfers rows that no Trades key prefixes: they
    # may carry the delivering dealer's name after the security's.
    transfer_keys: set = field(default_factory=set)
    # Internal codes `tjs run` resolved from the project's other
    # exports (lib/symbol_codes, `--symbol-codes`): code -> {symbol,
    # currency, how, evidence}; and the codes it could not, with why.
    inferred: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    unresolved: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Codes already warned about: ONE line per code per account, not
    # per file or row.
    code_warned: set = field(default_factory=set)
    # BRW currency journals (_plan_qt_journals): (file, line) -> the
    # leg's listing and its pair id ('' for a lone leg); internal codes
    # resolved from a journal leg of the same name in the same account:
    # code -> {symbol, currency, how, evidence} (symbol_codes' record).
    journal_legs: Dict[Tuple[str, int], Dict[str, Any]] = field(
        default_factory=dict)
    journal_codes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Every broker account (`Account #`) in ANY of the account's exports:
    # its Account Types, and the files (resolved paths) that hold it. The
    # taxable-versus-registered check spans the files (backlog TJ-0527):
    # the same rows split into two exports were booked unchecked.
    plan_types: Dict[str, set] = field(default_factory=dict)
    plan_files: Dict[str, set] = field(default_factory=dict)
    plans_checked: bool = False
    # Planned at the first parse (plan_rows), when the codes `tjs
    # run` resolved are known: the reversal pairing above, and each
    # broker account's signed position per listing (_plan_qt_positions)
    # that tells a split on a short position from a reverse split.
    rows_planned: bool = False
    positions: Dict[Tuple[str, str], List[Tuple[str, float]]] = field(
        default_factory=dict)

    def add_codes_note(self) -> None:
        """ONE note per account for every internal code booked under a
        ticker: those `tjs run` inferred from the other exports and
        those a journal leg of the account names (lib/symbol_codes)."""
        codes = {**self.journal_codes, **self.inferred}
        if codes:
            from taxjson.lib.symbol_codes import codes_note
            self.messages.append(codes_note(codes))

    def key_candidates(self, key: str, transfer_row: bool = False) -> set:
        """{(symbol, currency)} for a description key: the exact key;
        else, for a TRANSFER row (whose description may name the
        delivering dealer after the security), the longest known key
        that is a word prefix of it; else, for any other row, the
        transfer keys it is a word prefix of; else, a class share's
        key (KEY CL X) when exactly one class is known. Derived from the
        rows — no list of dealer names (owner, 2026-10-04)."""
        if not key:
            return set()
        hit = self.desc_to_ticker.get(key)
        if hit:
            return hit
        if transfer_row:
            best = max((k for k in self.desc_to_ticker
                        if key.startswith(k + " ")), key=len, default=None)
            return set(self.desc_to_ticker[best]) if best else set()
        out: set = set()
        for t in self.transfer_keys:
            if t.startswith(key + " "):
                out |= self.desc_to_ticker.get(t, set())
        if out:
            return out
        cls = [k for k in self.desc_to_ticker
               if re.fullmatch(re.escape(key) + r" CL [A-Z]", k)]
        return set(self.desc_to_ticker[cls[0]]) if len(cls) == 1 else set()

    def emit(self) -> None:
        if self.emitted:
            return
        self.emitted = True
        for m in self.messages:
            emit_line(m)

    def plan_rows(self, helper) -> None:
        """The plans that resolve a row's symbol as parse_file does
        (so after `prepare_files` has the inferred codes): once."""
        if self.rows_planned:
            return
        self.rows_planned = True
        _plan_qt_reversals(self, helper)
        _plan_qt_positions(self, helper)

    def position_on(self, listing: str, date: str, *,
                    before: bool = False, account: str = '') -> float:
        """Broker account `account`'s signed position in `listing` on
        `date` (`before`: not counting that day's rows)."""
        return sum(q for d, q in self.positions.get((account, listing), ())
                   if (d < date if before else d <= date))


def build_qt_account_context(paths, *, helper=None) -> QtAccountContext:
    helper = helper or QuestradeBrokerage()
    files = list(dict.fromkeys(str(Path(p).resolve()) for p in paths))
    ctx = QtAccountContext(files=files, desc_to_ticker={}, sym_curs={},
                           timelines={})
    by_name: Dict[tuple, set] = {}
    where: Dict[str, str] = {}
    trade_keys: set = set()
    # {(listing, date, quantity, the row's content): {file: copies}}.
    copies: Dict[tuple, Dict[str, int]] = {}
    for k in files:
        for lineno, row in _read_qt_rows(Path(k)):
            acct = (row.get('Account #') or '').strip()
            if acct and not _is_qt_header(row):
                ctx.plan_types.setdefault(acct, set()).add(
                    (row.get('Account Type') or '').strip())
                ctx.plan_files.setdefault(acct, set()).add(k)
            act = (row.get('Activity Type') or '').strip()
            if act not in ('Trades', 'Transfers'):
                continue
            sym = (row.get('Symbol') or '').strip().lstrip('.').upper()
            cur = (row.get('Currency') or '').strip().upper()
            desc = row.get('Description') or ''
            if cur == 'CAD' and _FX_SETTLED_RE.search(desc):
                cur = 'USD'          # listing currency, not settlement
            if not sym or not cur or _INTERNAL_CODE_RE.match(sym):
                continue
            key = _get_desc_key(desc)
            if key:
                ctx.desc_to_ticker.setdefault(key, set()).add((sym, cur))
                (trade_keys.add(key) if act == 'Trades'
                 else ctx.transfer_keys.add(key))
            if helper.parse_option_from_description(desc):
                continue             # a contract, not a listing
            ctx.sym_curs.setdefault(sym, set()).add(cur)
            dt = helper.parse_date(
                (row.get('Transaction Date') or '').strip(), *_DATE_FMTS)
            try:
                q = parse_strict_number(row.get('Quantity'),
                                        field='Quantity', allow_blank=True,
                                        blank=0.0)
            except BrokerageParseError:
                continue             # parse_file names the row
            action = _canon_action(row.get('Action'))
            if act == 'Trades':
                if action not in ('Buy', 'Sell'):
                    continue
                q = -abs(q) if action == 'Sell' else abs(q)
            if dt is None or abs(q) < 1e-12:
                continue
            listing = helper.apply_currency_suffix(sym, cur)
            try:
                net = _qt_cell_value(row, 'Net Amount')
            except BrokerageParseError:
                net = (row.get('Net Amount') or '').strip()  # parse_file names it
            per = copies.setdefault(
                (listing, dt.strftime('%Y-%m-%d'), q, acct, action,
                 (row.get('Symbol') or '').strip().upper(),
                 ' '.join(desc.split()).upper(), net, cur), {})
            per[k] = per.get(k, 0) + 1
            where.setdefault(listing, Path(k).name)
            if key:
                by_name.setdefault((key, cur), set()).add(listing)
    # A transfer key that a trade key prefixes ("<SECURITY> <DEALER>")
    # is that security's key.
    for tk in sorted(ctx.transfer_keys - trade_keys):
        base = max((k for k in trade_keys if tk.startswith(k + " ")),
                   key=len, default=None)
        if base is None:
            continue
        ctx.desc_to_ticker.setdefault(base, set()).update(
            ctx.desc_to_ticker.pop(tk, set()))
        for (bk, bc) in [x for x in by_name if x[0] == tk]:
            by_name.setdefault((base, bc), set()).update(
                by_name.pop((bk, bc)))
    ctx.transfer_keys -= trade_keys
    ctx.transfer_keys &= set(ctx.desc_to_ticker)
    # A row in two overlapping downloads is ONE broker row: its copies
    # are counted per file (as _plan_qt_positions does). A sale counted
    # twice made a flat listing read short, and a false "looks renamed"
    # ATTENTION followed (backlog TJ-1122).
    for ck, per in copies.items():
        ctx.timelines.setdefault(ck[0], []).extend(
            [(ck[1], ck[2])] * max(per.values()))
    for tl in ctx.timelines.values():
        tl.sort(key=lambda e: (e[0], -e[1]))
    _detect_qt_ticker_changes(ctx, by_name, where)
    _plan_qt_journals(ctx, helper)
    return ctx


def _quiet_resolver(ctx: QtAccountContext, helper) -> 'QuestradeBrokerage':
    """A parser bound to `ctx` that resolves a row's symbol as
    parse_file does (`_resolve_symbol(..., quiet=True)`): the plans key
    a row by the security it is booked on, not by its Symbol cell (bare
    SYN and SYN.TO on a CAD row, or an internal code its name resolves,
    are one listing: backlog TJ-0761)."""
    h = type(helper)()
    h.account_context = ctx
    h._ctx = ctx
    h._ambiguous_warned = set()
    h.lint_findings = []
    return h


def _qt_booked_listing(row, h, rei: bool = False) -> Optional[str]:
    """The listing parse_file books a non-trade row on, or None (no
    Currency: parse_file refuses the row)."""
    cur = (row.get('Currency') or '').strip().upper()
    if not cur:
        return None
    sym, scur = h._resolve_symbol(row, cur, rei=rei, quiet=True)
    sym = (sym or '').replace(' ', '.')
    return h.apply_currency_suffix(sym, scur) if sym else None


def _journal_listing(ctx: QtAccountContext, helper, sym: str, cur: str,
                     partner: str, key: str) -> str:
    """The listing one leg of a BRW currency journal moves: the
    security's line in the leg's currency. Questrade's website export
    names both legs by the bare symbol (QZD); its API export by the
    listing (QZD.TO / QZD.U.TO).

    - A Canadian listing in the Symbol column is the leg's line.
    - The CAD line of a bare symbol is its Canadian listing (SYM.TO).
    - The USD line: the listing this account's own USD rows of the same
      security (same description) are booked under, when exactly one;
      else the market's convention for a Canadian listing's US-dollar
      unit class (markets.usd_unit_listing: SYM.U.TO).
    A ticker.map EXTRACT line still rewrites the row after the parse."""
    from taxjson.lib.markets import usd_unit_listing
    if sym and canonical_ca_listing(sym, cur) is not None:
        return helper.apply_currency_suffix(sym, cur)
    base = sym if sym and not _INTERNAL_CODE_RE.match(sym) else partner
    if cur == 'CAD' and base:
        return helper.apply_currency_suffix(_journal_root(base), 'CAD')
    if cur == 'USD' and base:
        own = sorted({helper.apply_currency_suffix(s, c)
                      for s, c in ctx.desc_to_ticker.get(key, ())
                      if c == 'USD'})
        if len(own) == 1:
            return own[0]
        return usd_unit_listing(_journal_root(base))
    return helper.apply_currency_suffix(sym, cur)


def _plan_qt_journals(ctx: QtAccountContext, helper) -> None:
    """Pair the BRW currency-journal legs of one account (tax-logic
    CA-XLIST-03): an out-leg "<NAME> JOURNAL POSITION TO USD" (-q, in
    CAD) and an in-leg "<NAME> JOURNAL POSITION FROM CAD BOOK VALUE: $X
    CNV@ r" (+q, in USD) — or TO CAD / FROM USD the other way — of the
    same Account #, day, description and quantity are one journal
    between the security's CAD and USD lines (Norbert's gambit). Each
    leg gets its line's listing (_journal_listing) and the pair an id
    the parsed rows carry (`journal_pair`: `tjs run` joins the two
    lines as a ticker.map JOURNAL line would). An internal code whose
    rows have a journal leg's description in the same account and
    currency is that leg's listing (journal_codes). A leg with no
    partner is said as ATTENTION, naming both shapes."""
    legs: List[Dict[str, Any]] = []
    for k in ctx.files:
        for lineno, row in _read_qt_rows(Path(k)):
            if _canon_action(row.get('Action')) != 'BRW':
                continue
            desc = row.get('Description') or ''
            m = _BRW_JOURNAL_RE.search(desc)
            if not m:
                continue
            try:
                q = parse_strict_number(row.get('Quantity'),
                                        field='Quantity', allow_blank=True,
                                        blank=0.0)
            except BrokerageParseError:
                continue                 # parse_file names the row
            dt = helper.parse_date(
                (row.get('Transaction Date') or '').strip(), *_DATE_FMTS)
            if dt is None or abs(q) < 1e-9:
                continue
            legs.append({
                'file': k, 'line': lineno, 'q': q, 'desc': desc,
                'acct': (row.get('Account #') or '').strip(),
                'date': dt.strftime('%Y-%m-%d'),
                'way': m.group(1).upper(), 'to': m.group(2).upper(),
                'cur': (row.get('Currency') or '').strip().upper(),
                'cell': (row.get('Symbol') or '').strip(),
                'sym': (row.get('Symbol') or '').strip().lstrip('.')
                .upper(),
                'key': _get_desc_key(desc), 'pair': ''})
    if not legs:
        return
    n = 0
    for o in legs:
        if o['q'] > 0 or o['way'] != 'TO' or o['pair']:
            continue
        for i in legs:
            if (i['q'] < 0 or i['way'] != 'FROM' or i['pair']
                    or (i['acct'], i['date'], i['key'])
                    != (o['acct'], o['date'], o['key'])
                    or abs(i['q'] + o['q']) > 1e-6
                    or i['cur'] != o['to'] or o['cur'] != i['to']
                    or i['cur'] == o['cur']):
                continue
            n += 1
            o['pair'] = i['pair'] = f"{o['date']}#{n}"
            o['partner'], i['partner'] = i, o
            break
    for g in legs:
        p = g.get('partner')
        psym = p['sym'] if p else ''
        g['listing'] = _journal_listing(
            ctx, helper, g['sym'], g['cur'],
            psym if psym and not _INTERNAL_CODE_RE.match(psym) else '',
            g['key'])
        ctx.journal_legs[(g['file'], g['line'])] = {
            'symbol': g['listing'], 'pair': g['pair']}
    # Codes named by a journal leg's description, account and currency.
    by_name: Dict[Tuple[str, str, str], int] = {}
    for n, g in enumerate(legs):
        if g['pair'] or g['q'] > 0:      # paired legs, and a lone in-leg
            by_name.setdefault((g['acct'], g['key'], g['cur']), n)
    seen: Dict[str, set] = {}
    for k in ctx.files:
        for _ln, row in _read_qt_rows(Path(k)):
            sym = (row.get('Symbol') or '').strip().lstrip('.').upper()
            if not _INTERNAL_CODE_RE.match(sym):
                continue
            desc = row.get('Description') or ''
            if helper.parse_option_from_description(desc):
                continue
            cur = (row.get('Currency') or '').strip().upper()
            if cur == 'CAD' and _FX_SETTLED_RE.search(desc):
                cur = 'USD'              # listing currency
            n = by_name.get(((row.get('Account #') or '').strip(),
                             _get_desc_key(desc), cur))
            seen.setdefault(sym, set()).add(n)
    for code, hits in sorted(seen.items()):
        if len(hits) != 1 or None in hits:
            continue        # rows the journal does not name decide it
        g = legs[next(iter(hits))]
        if any(ticker_map_names(helper.apply_currency_suffix(code, c))
               for c in ('USD', 'CAD', g['cur'])):
            continue        # the user's map decides (as for inference)
        ctx.journal_codes[code] = {
            'symbol': g['listing'], 'currency': g['cur'], 'how': 'journal',
            'evidence': (f"journal {'in' if g['q'] > 0 else 'out'}-leg "
                         f"of {abs(g['q']):g} on {g['date']}, same name "
                         f"in this account")}
    for g in legs:
        moved = g['listing'] or g['cell'].upper()
        if g['pair'] or not moved:
            continue    # (a blank leg nothing names: parse_file refuses it)
        # The journal's source and target currencies, from this leg.
        src, dst = ((g['cur'], g['to']) if g['q'] < 0
                    else (g['to'], g['cur']))
        nm = (g['key'] or g['desc'][:60]).strip()
        ctx.messages.append(
            f"warning: ATTENTION: {shown_name(Path(g['file']))} line "
            f"{g['line']}: a BRW journal row ({g['desc'][:90]!r}, "
            f"{g['q']:+g}) has no partner — a currency journal is TWO "
            f"rows of one account on one day with the same quantity: "
            f"'{nm} JOURNAL POSITION TO {dst}' (-units, in {src}) and "
            f"'{nm} JOURNAL POSITION FROM {src} BOOK VALUE: $X CNV@ r' "
            f"(+units, in {dst}). Booked as a transfer leg of "
            f"{moved} on its own, so its units do not move to the "
            f"other line: check that the export holds both rows "
            f"(re-export the day), or book the move in a .tt file.")


def journal_codes(paths, *, helper=None) -> Dict[str, Dict[str, Any]]:
    """The internal codes of one account's exports that a BRW journal
    leg of the same name names (_plan_qt_journals): code -> {symbol,
    currency, how: "journal", evidence} — `tjs run` records them
    with the inferred ones (lib/symbol_codes), so `tjs transfers`
    lists them."""
    paths = [Path(p) for p in paths]
    if not any(_canon_action(r.get('Action')) == 'BRW'
               for p in paths for _ln, r in _read_qt_rows(p)):
        return {}
    ctx = build_qt_account_context(paths,
                                   helper=helper or QuestradeBrokerage())
    return dict(ctx.journal_codes)


def scan_code_uses(paths, *, helper=None) -> list:
    """[symbol_codes.CodeUse] for the internal codes in one account's
    exports that the account's own trades and transfers do not resolve
    (the rows _resolve_symbol would keep the code on), and for every code
    that carries a spinoff / rights leg (taxjson-corp-actions books the
    chain under the code-level resolution): the description key, every
    distinct description, the row currencies (listing currency for an
    EXCHANGE RATE row), each transfer-in's (date, quantity), and the
    listings the account's own rows name for some of the code's rows
    (`own`) — what `tjs run` needs to resolve the code."""
    from taxjson.lib.symbol_codes import (CodeUse, desc_cut,
                                          questrade_name,
                                          questrade_own_name)
    helper = helper or QuestradeBrokerage()
    paths = [Path(p) for p in paths]
    if not any(_INTERNAL_CODE_RE.match(
            (r.get('Symbol') or '').strip().lstrip('.').upper())
            for p in paths for _ln, r in _read_qt_rows(p)):
        return []                    # the common case: no code at all
    ctx = build_qt_account_context(paths, helper=helper)
    # listing -> the description keys the account's own rows give it
    keys_of: Dict[str, List[str]] = {}
    for key, hits in sorted(ctx.desc_to_ticker.items()):
        for s_, c_ in sorted(hits):
            keys_of.setdefault(helper.apply_currency_suffix(s_, c_),
                               []).append(key)
    uses: Dict[str, Any] = {}
    descs: Dict[str, List[str]] = {}
    own: Dict[str, Dict[str, List[str]]] = {}
    legs: set = set()
    names: Dict[str, Tuple[str, str]] = {}   # code -> (name, desc)
    for k in ctx.files:
        for _ln, row in _read_qt_rows(Path(k)):
            sym = (row.get('Symbol') or '').strip().lstrip('.').upper()
            if not _INTERNAL_CODE_RE.match(sym):
                continue
            desc = row.get('Description') or ''
            if helper.parse_option_from_description(desc):
                continue
            act = (row.get('Activity Type') or '').strip()
            action = _canon_action(row.get('Action'))
            transfer = act == 'Transfers' or action == 'TF6'
            leg = bool(_QT_CA_LEG_RE.search(desc))
            d1 = ' '.join(desc.split())
            if d1 and d1 not in descs.setdefault(sym, []):
                descs[sym].append(d1)
            if leg:
                legs.add(sym)
                if sym not in names:
                    names[sym] = (questrade_own_name(desc), '')
            elif sym not in names or not names[sym][1]:
                names[sym] = (questrade_name(desc), desc)
            # A spinoff leg's own name, never its parent's (the key
            # _get_desc_key reads from "... FROM SEC# n <PARENT> REC").
            key = (_get_desc_key(questrade_own_name(desc)) if leg
                   else _get_desc_key(desc))
            cands = (set() if sym in ctx.journal_codes
                     else ctx.key_candidates(key, transfer_row=transfer))
            for s_, c_ in sorted(cands):
                lst = helper.apply_currency_suffix(s_, c_)
                ks = own.setdefault(sym, {}).setdefault(lst, [])
                if not ks:
                    ks.append(key)
                    ks.extend(x for x in keys_of.get(lst, ()) if x != key)
            if sym in ctx.journal_codes or (cands and not leg):
                continue
            cur = (row.get('Currency') or '').strip().upper()
            if cur == 'CAD' and _FX_SETTLED_RE.search(desc):
                cur = 'USD'
            u = uses.get(sym)
            if u is None:
                u = uses[sym] = CodeUse(code=sym, name='')
            u.rows += 1
            key = questrade_name(desc)
            if transfer and key and not u.name:
                u.name = key
                u.name_cut = desc_cut(desc, key)
            if cur and cur not in u.currencies:
                u.currencies.append(cur)
            if not transfer:
                continue
            try:
                q = parse_strict_number(row.get('Quantity'),
                                        field='Quantity', allow_blank=True,
                                        blank=0.0)
            except BrokerageParseError:
                continue                 # parse_file names the row
            dt = helper.parse_date(
                (row.get('Transaction Date') or '').strip(), *_DATE_FMTS)
            if dt is not None and q > 1e-9:
                u.arrivals.append((dt.strftime('%Y-%m-%d'), q))
    for sym, u in uses.items():
        # A name from any row of the code: a row of its own wording
        # first, else a spinoff leg's own name (never the parent's).
        if not u.name and sym in names:
            nm, d = names[sym]
            u.name = nm
            u.name_cut = desc_cut(d, nm) if d else False
        u.descriptions = list(descs.get(sym, ()))
        if sym in legs or u.rows:
            u.own = dict(own.get(sym, {}))
    return [uses[c] for c in sorted(uses)]


def _qt_reversal_kind(row, helper) -> Optional[Tuple[tuple, bool, str]]:
    """(key, is_reversal, date) of a stock-dividend / cash-in-lieu / DRIP
    row the parser pairs with its reversal (the same classification as
    parse_file), else None. Unparseable cells -> None (parse_file names
    the row)."""
    action = _canon_action(row.get('Action'))
    act = (row.get('Activity Type') or '').strip()
    desc = row.get('Description') or ''
    try:
        qty = parse_strict_number(row.get('Quantity'), field='Quantity',
                                  allow_blank=True, blank=0.0)
        net = parse_strict_number(row.get('Net Amount'), field='Net Amount',
                                  allow_blank=True, blank=0.0)
    except BrokerageParseError:
        return None
    dt = helper.parse_date((row.get('Transaction Date') or '').strip(),
                           *_DATE_FMTS)
    if dt is None:
        return None
    date = dt.strftime('%Y-%m-%d')
    if helper._is_stock_split(action, act, desc) \
            and not helper._is_cash_only(row):
        return None
    # The listing each branch of parse_file books the row on (backlog
    # TJ-0761: the Symbol cell as written kept bare SYN and SYN.TO apart).
    if action == 'DIS' and _STK_DIV_RE.search(desc) and abs(qty) > 1e-9:
        sym = _qt_booked_listing(row, helper)
        if sym is None:
            return None
        return ('STK DIV', sym, M.units(abs(qty), 6), 0.0), qty < 0, date
    if action == 'CIL' and _CIL_RE.search(desc):
        try:
            frac = desc_number(_CIL_RE.search(desc).group(1))
        except BrokerageParseError:
            return None                 # parse_file refuses the row
        sym = _qt_booked_listing(row, helper)
        if frac <= 0 or abs(net) <= 0 or sym is None:
            return None
        return ('CIL', sym, M.units(frac, 6), M.units(abs(net), 2)), net < 0, date
    if action == 'REI' or act == 'Dividend reinvestment':
        if abs(qty) <= 1e-12 or abs(net) < 0.005 or (qty > 0) == (net > 0):
            return None
        sym = _qt_booked_listing(row, helper, rei=True)
        if sym is None:
            return None
        return (('REI', sym, M.units(abs(qty), 6), M.units(abs(net), 2)),
                qty < 0, date)
    return None


def _qt_cell_value(row, col: str) -> float:
    """A number cell of a row _qt_reversal_kind classified (so it
    parses) as a value two downloads agree on however they write it:
    10, 10.00 and 10.0000 are one value."""
    v = parse_strict_number(row.get(col), field=col, allow_blank=True,
                            blank=0.0)
    return M.units(v, 9) + 0.0


def _plan_qt_reversals(ctx: QtAccountContext, helper) -> None:
    """Pair every CIL / REI / stock-dividend reversal with its original
    across ALL of the account's exports (re-audit A2-0026, A2-0280,
    A2-0281, A2-0616, A2-1055). Per file, an overlapping older download
    kept the original the newer one reversed (phantom shares), and a
    reversal whose original sat in last year's export refused the whole
    account. A row in two overlapping downloads is ONE broker row (its
    copies are counted per file, as RBC's overlap plan does); a reversal
    cancels the latest original of the same security, quantity and
    amount on or before its own date (re-audit A2-1063), and every copy
    of both rows drops out. The original must be in the same broker
    account (`Account #`) and currency as the reversal: a USD reversal
    cancelled a CAD reinvestment of the same symbol, units and amount,
    and one account's reversal another account's (backlog TJ-0517).
    Copies are matched by the numbers' values, not their spelling: one
    download writing 10 and -100 and another 10.0000 and -100.00 gave
    two originals, and the reversal cancelled one (backlog TJ-0666)."""
    # content key -> per file: [line numbers]
    copies: Dict[tuple, Dict[str, List[int]]] = {}
    meta: Dict[tuple, Tuple[tuple, bool, str]] = {}
    helper = _quiet_resolver(ctx, helper)
    for k in ctx.files:
        for lineno, row in _read_qt_rows(Path(k)):
            kind = _qt_reversal_kind(row, helper)
            if kind is None:
                continue
            acct = (row.get('Account #') or '').strip()
            cur = (row.get('Currency') or '').strip().upper()
            ck = (kind[2], _canon_action(row.get('Action')),
                  (row.get('Symbol') or '').strip().upper(),
                  ' '.join((row.get('Description') or '').split()).upper(),
                  _qt_cell_value(row, 'Quantity'),
                  _qt_cell_value(row, 'Net Amount'), acct, cur)
            copies.setdefault(ck, {}).setdefault(k, []).append(lineno)
            meta[ck] = (kind[0] + (acct, cur), kind[1], kind[2])
    # Logical events: copy i of a content key = the i-th occurrence in
    # every file that has one.
    events = []           # (date, is_rev, key, ck, i)
    for ck, per in copies.items():
        key, is_rev, date = meta[ck]
        for i in range(max(len(v) for v in per.values())):
            events.append((date, is_rev, key, ck, i))
    events.sort(key=lambda e: (e[0], e[1]))
    open_orig: Dict[tuple, List[tuple]] = {}
    for date, is_rev, key, ck, i in events:
        if not is_rev:
            open_orig.setdefault(key, []).append((date, ck, i))
            continue
        cands = [e for e in open_orig.get(key, []) if e[0] <= date]
        if not cands:
            continue                     # unpaired: parse_file refuses it
        hit = cands[-1]
        open_orig[key].remove(hit)
        for (ock, oi), bucket in (((hit[1], hit[2]), ctx.rev_drop),
                                  ((ck, i), ctx.rev_paired)):
            for f, lines in copies[ock].items():
                if oi < len(lines):
                    bucket.setdefault(f, set()).add(lines[oi])


def _qt_position_event(row, h, journal: Optional[Dict[str, Any]] = None
                       ) -> Optional[Tuple[str, str, str, float]]:
    """(broker account, listing, date, signed change in shares) of a row
    that moves a listed position, else None: a Buy or Sell, a transfer,
    an assignment's or exercise's stock leg (its cash says the side), a
    BRW journal leg (`journal`: its planned leg, _plan_qt_journals), and
    a split, stock dividend or reinvestment (whose signed Quantity is the
    change, on a long or a short position) on the listing parse_file
    books it on. Unparseable cells: None (parse_file names the row). A
    stock leg or a journal leg that carried the holding across zero made
    a later split read the wrong side (TJ-4546)."""
    act = (row.get('Activity Type') or '').strip()
    action = _canon_action(row.get('Action'))
    desc = row.get('Description') or ''
    try:
        q = parse_strict_number(row.get('Quantity'), field='Quantity',
                                allow_blank=True, blank=0.0)
    except BrokerageParseError:
        return None
    dt = h.parse_date((row.get('Transaction Date') or '').strip(),
                      *_DATE_FMTS)
    if dt is None or abs(q) < 1e-12:
        return None
    sym = (row.get('Symbol') or '').strip().lstrip('.').upper()
    cur = (row.get('Currency') or '').strip().upper()
    listing = None
    # An assignment's or exercise's leg, whatever its Activity Type (a
    # stock leg under Trades read as no event: review round 1).
    leg = action in ('ASN', 'EX')
    if act in ('Trades', 'Transfers') and not leg:
        try:
            if h.parse_option_from_description(desc):
                return None         # a contract, not a listing
        except BrokerageParseError:
            return None
    if leg:
        stock_leg = _qt_stock_leg(row, h, desc, sym)
        if stock_leg is None:
            return None
        listing, q = stock_leg[0], math.copysign(q or 0.0, stock_leg[1])
    elif act in ('Trades', 'Transfers') and sym and cur \
            and not _INTERNAL_CODE_RE.match(sym):
        # As the timelines read them (build_qt_account_context).
        if act == 'Trades':
            if action not in ('Buy', 'Sell'):
                return None
            q = -abs(q) if action == 'Sell' else abs(q)
        if cur == 'CAD' and _FX_SETTLED_RE.search(desc):
            cur = 'USD'
        listing = h.apply_currency_suffix(sym, cur)
    elif act == 'Transfers':
        listing = _qt_booked_listing(row, h)    # an internal code
    elif action == 'BRW' and 'JOURNAL' in desc.upper():
        listing = ((journal or {}).get('symbol')
                   or h.apply_currency_suffix(
                       (row.get('Symbol') or '').strip().upper(), cur))
    elif h._is_stock_split(action, act, desc) and not h._is_cash_only(row):
        listing = _qt_booked_listing(row, h)
    elif action == 'DIS' and _STK_DIV_RE.search(desc):
        listing = _qt_booked_listing(row, h)
    elif action == 'REI' or act == 'Dividend reinvestment':
        listing = _qt_booked_listing(row, h, rei=True)
    if not listing:
        return None
    return ((row.get('Account #') or '').strip(), listing,
            dt.strftime('%Y-%m-%d'), q)


def _qt_stock_leg(row: Dict[str, Any], h: Any, desc: str,
                  sym: str) -> Optional[Tuple[str, float]]:
    """(listing, side) of an ASN / EX row that is an assignment's or an
    exercise's stock leg, as parse_file books it: a row carrying cash
    whose description names no contract, or names one whose underlying
    the Symbol column is; shares bought (Net < 0, side +1) or delivered.
    Else None (an option leg, a row with no cash, an internal code, as a
    trade under one is, or unreadable cells)."""
    try:
        cash = [parse_strict_number(row.get(c), allow_blank=True, blank=0.0)
                or 0.0 for c in ('Gross Amount', 'Net Amount')]
        opt = h.parse_option_from_description(desc)
    except BrokerageParseError:
        return None
    if not any(abs(c) > 0.005 for c in cash) or (
            opt and not (sym and names_underlying(opt['base'], sym))):
        return None
    cur = (row.get('Currency') or '').strip().upper()
    if not (sym and cur) or _INTERNAL_CODE_RE.match(sym):
        return None             # as for a trade under an internal code
    if cur == 'CAD' and _FX_SETTLED_RE.search(desc):
        cur = 'USD'
    return h.apply_currency_suffix(sym, cur), (1.0 if cash[1] < 0 else -1.0)


def _plan_qt_positions(ctx: QtAccountContext, helper) -> None:
    """Each broker account's signed position per listing, over ALL of
    the account's exports, for _parse_split (a split on a short
    position debits shares; on a long one that is a reverse split). A
    row in two overlapping downloads is ONE broker row (its copies are
    counted per file, as _plan_qt_reversals does): a sale counted twice
    made a long holding read short and a 6-for-5 split 0.8 (backlog
    TJ-0664). An earlier split, stock dividend or reinvestment moves the
    position too: a 2-for-1 on a short of 100 left it -200, not -100
    (TJ-0665). Each broker account is its own position: with
    combined_broker_accounts one account's long made another's short
    split read as a reverse split (TJ-0672)."""
    h = _quiet_resolver(ctx, helper)
    copies: Dict[tuple, Dict[str, int]] = {}
    for k in ctx.files:
        for _lineno, row in _read_qt_rows(Path(k)):
            if _is_qt_header(row):
                continue
            ev = _qt_position_event(row, h, ctx.journal_legs.get((k, _lineno)))
            if ev is None:
                continue
            try:
                ck = (ev, _canon_action(row.get('Action')),
                      (row.get('Symbol') or '').strip().upper(),
                      ' '.join((row.get('Description') or '').split())
                      .upper(),
                      _qt_cell_value(row, 'Net Amount'),
                      (row.get('Currency') or '').strip().upper())
            except BrokerageParseError:
                continue
            per = copies.setdefault(ck, {})
            per[k] = per.get(k, 0) + 1
    for ck, per in copies.items():
        acct, listing, date, q = ck[0]
        ctx.positions.setdefault((acct, listing), []).extend(
            [(date, q)] * max(per.values()))
    for tl in ctx.positions.values():
        tl.sort(key=lambda e: (e[0], -e[1]))

def _detect_qt_ticker_changes(ctx: QtAccountContext, by_name, where) -> None:
    """A ticker change with no corporate-action row (audit S062-06 /
    S063-12): the old symbol stops with shares still open and a symbol
    with the same Description and currency goes SHORT by no more than
    those shares — its first row a sale, or a buy followed by a larger
    sale (re-audit A2-0099). As exported that is a stranded long and a
    short, and the year's gain drops out. The export has no CUSIP, so
    this is not certain enough to merge silently: an ATTENTION line (on
    the run console, re-audit A2-0279) with the ticker.map line first."""
    for (key, _cur), listings in sorted(by_name.items()):
        if len(listings) < 2:
            continue
        for a in sorted(listings):
            for b in sorted(listings):
                ta, tb = ctx.timelines.get(a), ctx.timelines.get(b)
                reach = None if a == b else rename_reach(ta, tb)
                if reach is None:
                    continue
                open_a, low, when, first_b = reach
                if ticker_map_joins(a, b):
                    continue    # ticker.map already pools them (A2-1056)
                declared = declared_rename_where(a, b)
                if declared:
                    # A .tt RENAME line already books it: a note.
                    ctx.messages.append(answered_rename_note(
                        where.get(b, '?'), "Questrade", a, b, declared))
                    continue
                how = rename_how(tb[0][0], low, when, first_b)
                ctx.messages.append(
                    f"warning: ATTENTION: {where.get(b, '?')}: Questrade "
                    f"symbol {a} looks renamed to {b} — if they are one "
                    f"security, add to a .tt file of this account:  "
                    f"RENAME {tb[0][0]} {a} {b}  — {a} "
                    f"stops on {ta[-1][0]} with {open_a:g} share(s) still "
                    f"open, and {b} (same Description {key!r}) {how}. "
                    f"That looks like a ticker change booked without a "
                    f"corporate-action row: as exported it is a stranded "
                    f"long {a} and a short {b} (the sale's gain drops "
                    f"out).")


def _row_clock(dt) -> Tuple[str, str]:
    """(date, time) of a row's Transaction Date: the row's own clock, the
    one rule for every row kind (TJ-2381, TJ-5017). The export stamps most
    rows midnight, so a day's rows tie and keep the file's order
    (CA-DATE-14 / US-DATE-13); a stock dividend, a cash in lieu, a
    reinvestment or an interest row stamped a fixed 09:30:00 sorted after
    a trade, a journal or a transfer listed below it. A return of capital
    alone keeps 09:30:00 (`tx_roc_adjust`, the ruling on TJ-5018)."""
    return dt.strftime('%Y-%m-%d'), dt.strftime('%H:%M:%S')


_REINVESTED_CREDITS = ('DIVIDEND', 'DIVIDEND_IN_LIEU')
_NO_CLOCK = '00:00:00'      # the export's midnight: a row with no clock
_ROC_STAMP = '09:30:00'     # base.tx_roc_adjust's fixed time


def _pays_reinvestment(row: Dict[str, Any], rei: Dict[str, Any]) -> bool:
    """`row` is a credit of the reinvestment's day that can pay it: a
    dividend or a payment in lieu of its security, a return of capital of
    its security (an ADJUST of type roc: cash received lowers the cost, a
    negative amount), or an interest credit, in its currency."""
    if row is rei or (row.get('date'), row.get('currency')) != (
            rei.get('date'), rei.get('currency')):
        return False
    action, net = row.get('action'), row.get('net_amount') or 0.0
    if action == 'INTEREST':
        return net > 0
    if row.get('symbol') != rei.get('symbol'):
        return False
    if action == 'ADJUST':
        return row.get('type') == 'roc' and net < 0
    return action in _REINVESTED_CREDITS and net > 0


def _credits_before_reinvestments(transactions: List[Dict[str, Any]],
                                  reinvestments: set) -> None:
    """A reinvestment is the dependent leg of the distribution that pays
    it: in place, it is ordered after the credits of its day that can
    (`_pays_reinvestment`) and share its clock, and after a return of
    capital's stamp (the owner's rulings on TJ-5265). A credit at the reinvestment's own clock but listed after it
    is moved just before it: rows at one moment keep the emitted order in
    every stage (CA-DATE-14 / US-DATE-13), the cash ledgers' walks too
    (bin/taxjson_fx_cash build_ledger, lib/fx_cash_v2). A return of
    capital is stamped 09:30:00 by every importer (`tx_roc_adjust`), a
    convention, not a clock: a reinvestment it pays that has no clock of
    its own (the export's midnight) takes that stamp, since a
    reinvestment cannot come before the cash that pays it. No real clock
    is lent or changed: a reinvestment with a clock of its own keeps it,
    and a credit at a later real clock stays where its clock puts it. At a fixed 09:30:00 the
    reinvestment followed every credit of its day; at its own clock an
    export listing the REI row first, or paying it by a return of
    capital, overdrew the cash (the review of TJ-5017). Neither row
    touches the other's figure: a credit moves no position and the
    purchase no income."""
    i = 0
    while i < len(transactions):
        r = transactions[i]
        i += 1
        if id(r) not in reinvestments:
            continue
        if r.get('time') == _NO_CLOCK and any(
                t.get('action') == 'ADJUST' and t.get('time') == _ROC_STAMP
                and _pays_reinvestment(t, r) for t in transactions):
            r['time'] = _ROC_STAMP
        late = [j for j in range(i, len(transactions))
                if transactions[j].get('time') == r.get('time')
                and _pays_reinvestment(transactions[j], r)]
        credits = [transactions[j] for j in late]
        for j in reversed(late):
            del transactions[j]
        transactions[i - 1:i - 1] = credits
        i += len(credits)


def _qt_real_order(path: Path, rows) -> List[tuple]:
    """Rows of one day tie (Questrade stamps 00:00:00) and keep the
    order they are emitted in (CA-DATE-14 / US-DATE-13): a
    newest-first export is read bottom-up so that order is the real
    one. Decided per header-delimited SEGMENT (re-audit A2-0100): two
    newest-first exports concatenated (the header repeated, which
    the parse accepts) went both ways over the whole file and were
    read top-down — a same-day sale replayed after its rebuy. The
    parse reads its rows through it (`_in_real_order`), and its cash
    events keep that order (`_cash_events_of_rows`; once a second
    reading of the file, which left the "not in date order" note to
    the parse, PLAN step 16 commit 9)."""
    segs: List[List[tuple]] = [[]]
    for item in rows:
        r = item[1]
        if [(r.get(c) or '').strip() for c in _QT_COLUMNS[:5]] == \
                list(_QT_COLUMNS[:5]):
            segs.append([item])          # the header opens a segment
        else:
            segs[-1].append(item)
    out: List[tuple] = []
    mixed = False
    for seg in segs:
        head = [x for x in seg[:1]
                if [(x[1].get(c) or '').strip()
                    for c in _QT_COLUMNS[:5]] == list(_QT_COLUMNS[:5])]
        body = seg[len(head):]
        dates = [BaseBrokerage.parse_date(
            (r.get('Transaction Date') or '')[:10], '%Y-%m-%d')
            for _, r in body]
        ds = [d for d in dates if d]
        if BaseBrokerage.newest_first(dates):
            body = body[::-1]
        elif not all(a <= b for a, b in zip(ds, ds[1:])):
            mixed = True
        out.extend(head + body)
    if mixed:
        emit_line(f"note: {shown_name(path)}: rows are not in date order, "
                  f"so same-day rows keep the file's order (intra-day "
                  f"order unknown) — check a same-day sale and rebuy.")
    return out


class _QtShape(NamedTuple):
    """A trade row's shape (`_trade_shape`): what the money and the row
    read of it."""
    carries_cash: Any
    date: Any
    date_settle: Any
    is_assigned: Any
    is_expired: Any
    opt: Any
    qty: Any
    time: Any


class _QtMoney(NamedTuple):
    """A trade row's money (`_trade_money`): what the row reads of it."""
    comm: Any
    listing_currency: Any
    mult: Any
    net: Any
    price: Any
    size_basis: Any


class _QtRow(Row):
    """A row of the export as `read` returns it: its cells, its line,
    whether it is the header repeated; what `classify` learned for `build`
    (the action, the activity type, the description, the currency, the
    builder and, for a trade, its shape flags; the quantity a blank 0)."""
    __slots__ = ("header", "action_raw", "activity_type", "desc",
                 "currency", "is_trade", "code_expired", "code_assigned",
                 "word_assigned", "is_expired", "is_assigned", "q")

    def __init__(self, cells: Dict[str, Any], line: int, header: bool = False):
        super().__init__(cells, line)
        self.header = header
        self.action_raw = self.activity_type = self.desc = self.currency = None
        self.q = None
        self.is_trade = self.code_expired = self.code_assigned = None
        self.word_assigned = self.is_expired = self.is_assigned = None


@dataclass
class _QtParse:
    """What `read` hands the loop and the finish of one export: the file,
    and what the rows accumulate for the finish (the quantity-bearing DIS
    legs, the net-of-tax dividends, the $0 transfer-ins, the BRW journals
    and their rows, the reversal originals and the reversals, the
    stock-dividend notes). Released by `release` whatever happened."""
    path: Path
    shown: str                      # the file as the messages name it
    key: str                        # the file's key in the account context (its resolved path)
    ca_legs: List[str] = field(default_factory=list)
    net_of_tax: List[str] = field(default_factory=list)
    no_book_value: List[str] = field(default_factory=list)
    journals: List[str] = field(default_factory=list)
    journal_txs: List[Dict[str, Any]] = field(default_factory=list)
    # CIL / REI reversals (audit R1-66): a same-code row with the signs
    # negated cancels its original. Originals by key -> list of emitted
    # leg groups; reversal rows -> (key, lineno, desc), paired at the end
    # of the parse (the export may list newest first).
    rev_originals: Dict[tuple, List[List[Dict[str, Any]]]] = field(default_factory=dict)
    reversals: List[tuple] = field(default_factory=list)
    # Stock-dividend notes, printed only for the ones a reversal does not
    # cancel (re-audit A2-1060).
    sd_notes: List[tuple] = field(default_factory=list)
    # id(original leg group) -> its line (the account-wide plan names
    # originals by line).
    orig_line: Dict[int, int] = field(default_factory=dict)
    # id(row) of each reinvestment's purchase (`_credits_before_reinvestments`).
    reinvestments: set = field(default_factory=set)


class QuestradeBrokerage(BaseBrokerage):
    DEFAULT_ACCOUNT = "Questrade"

    # Whether the account is TAXABLE: True / False when the caller knows
    # (`taxjson-brokerage --account-type`, which `tjs run` passes),
    # None = infer from the export's Account Type column. Only gates
    # warnings that matter in a taxable account (a dividend booked net
    # of non-resident withholding, a transfer-in with no book value).
    account_taxable: Optional[bool] = None

    # ------------------------------------------------------------ helpers

    _qt: Optional["_QtParse"] = None    # the parse's state, None between parses

    @property
    def _qt_name(self) -> str:
        """The file as the messages name it: the parse's; '' between
        parses (a bare parser's option parse names nothing)."""
        return self._qt.shown if self._qt is not None else ''

    def _where_name(self) -> str:
        return self._qt_name

    def _where(self, lineno: int) -> str:
        return f"{self._qt_name} line {lineno}"

    def _num(self, row: Dict[str, Any], col: str, lineno: int) -> float:
        """A REQUIRED numeric cell, parsed strictly (see
        base.parse_strict_number): garbage or a blank is an error, never
        0."""
        return parse_strict_number(row.get(col), field=col,
                                   where=self._where(lineno))

    def _date(self, row: Dict[str, Any], col: str, lineno: int,
              required: bool = True):
        """A date cell in any shape Questrade exports reach us in (see
        _DATE_FMTS). A blank optional cell -> None; a cell that is
        present but unparseable is an ERROR — it used to fall through
        verbatim into `date` (or silently to a computed settlement)."""
        raw = (row.get(col) or '').strip()
        if not raw:
            if required:
                raise BrokerageParseError(
                    f"{self._where(lineno)}: required {col} is blank")
            return None
        dt = self.parse_date(raw, *_DATE_FMTS)
        if dt is None:
            raise BrokerageParseError(
                f"{self._where(lineno)}: {col} {raw!r} is not a date this "
                f"parser reads (YYYY-MM-DD[ HH:MM:SS[ AM|PM]]) — refusing "
                f"to guess")
        return dt

    def _currency(self, row: Dict[str, Any], lineno: int) -> str:
        cur = (row.get('Currency') or '').strip()
        if not cur:
            raise BrokerageParseError(
                f"{self._where(lineno)}: blank Currency — the suffix and "
                f"the FX rate both depend on it; refusing to assume USD")
        # A currency code is case-blind: 'usd' became the suffix .usd,
        # a pool apart from SAMPLE.US (audit S055-17).
        return cur.upper()

    def _is_taxable(self) -> Optional[bool]:
        if self.account_taxable is not None:
            return self.account_taxable
        return self._qt_taxable_hint

    def apply_currency_suffix(self, symbol: str, currency: str) -> str:
        """Questrade names the LISTING in the symbol: SAMPLZ.TO is a TSX
        listing and a bare symbol a US one. A .TO symbol keeps .TO
        whatever currency the row settled in — SAMPLF.U.TO / XUS.U.TO trade
        on the TSX in USD and became SAMPLF.U.US, a separate pool from the
        same units at RBC or IB (which a JOURNAL rule then never met),
        and a US-looking symbol on the T1135 (audit R1-68; the generic
        importer's R1-122). Anything else follows the base rule."""
        sym = (symbol or '').strip().replace(' ', '.')
        if sym.upper().endswith('.TO') and len(sym) > 3:
            return canonical_ca_listing(sym, 'CAD')
        return super().apply_currency_suffix(symbol, currency)

    def parse_option_from_description(self, desc):
        # A strike only partly read ('2,50' as 2) is refused (A2-1041).
        return self.strict_option_from_description(desc)

    @classmethod
    def prepare_files(cls, paths, symbol_codes=None, *, tax_year=None, combined=False,
                      taxable=None, project=None) -> QtAccountContext:
        """Read ALL of one account's Questrade exports once and build the
        identity maps every per-file parse shares (description ->
        traded symbol, a symbol's listing currency, position timelines).
        Account-level warnings print here, once. `symbol_codes`: the
        record `tjs run` wrote (lib/symbol_codes) — the internal
        codes it resolved from the project's other exports, listed in
        ONE note."""
        ctx = build_qt_account_context(list(paths), helper=cls())
        if symbol_codes:
            from taxjson.lib.symbol_codes import read_state
            st = read_state(Path(symbol_codes))
            ctx.unresolved = dict(st.get('unresolved') or {})
            for code, r in sorted((st.get('resolved') or {}).items()):
                if not isinstance(r, dict) or not r.get('symbol'):
                    continue
                # A ticker.map rule naming the code (any rule: a rename,
                # DELETE, DISTINCT, a dated RENAME) wins over the
                # inference — the same test `tjs run` records.
                if any(ticker_map_names(cls().apply_currency_suffix(
                        code, c)) for c in ('USD', 'CAD',
                                            r.get('currency') or 'USD')):
                    continue
                ctx.inferred[code.upper()] = r
        ctx.add_codes_note()
        ctx.emit()
        return ctx

    # Set by taxjson-brokerage (see `prepare_files`); None = this file alone.
    account_context: Optional[QtAccountContext] = None

    def _unbooked(self, lineno: int, msg: str, label: str) -> Outcome:
        """A real row the parser could not book: `tjs run` echoes
        the UNBOOKED prefix to the console and `run --strict` refuses
        to publish (the old skip note reached only the .sum banner).
        The row's answer, the reported class under `label`: counted, and
        not listed again in the skip summary, which the warning would
        repeat (TJ-2377, TJ-2401)."""
        emit_line(f"warning: UNBOOKED: {self._where(lineno)}: {msg}")
        return Outcome.reported(label)

    def _in_real_order(self, path: Path, rows) -> List[tuple]:
        """Rows in the order they happened: `_qt_real_order`."""
        return _qt_real_order(path, rows)

    def _check_account_mix(self, path: Path, cells) -> None:
        """One export holding rows of SEVERAL Questrade accounts (re-audit
        A2-0025): every row is booked to the one taxjson account the file
        sits in. A registered plan's rows (Account Type TFSA/RRSP/...)
        in a TAXABLE account — or a taxable account's rows in a
        registered one — put the wrong trades in the books: refused.
        Any other mix (two taxable accounts) is booked as one and said
        on the console.

        The same holds ACROSS the account's exports (backlog TJ-0527):
        the TFSA's rows in a file of their own beside the margin
        account's were booked unchecked. The account's exports of ONE
        Questrade account whose own Account Type contradicts the taxjson
        account (a LIRA export in a taxable folder, a margin export in a
        sheltered one) are booked as the folder says, with an ATTENTION
        line (backlog TJ-0477): they were booked with no word."""
        ctx = self._ctx
        if ctx is not None and not ctx.plans_checked:
            ctx.plans_checked = True
            # One Questrade account, or several no one export holds all
            # of (an export holding them all is checked below): base.py.
            check_account_plans_across(self, ctx.files, ctx.plan_types,
                                       ctx.plan_files, broker="Questrade",
                                       column="Account Type")
        accts: Dict[str, set] = {}
        for r in cells:
            a = (r.get('Account #') or '').strip()
            if a:
                accts.setdefault(a, set()).add(
                    (r.get('Account Type') or '').strip())
        if len(accts) > 1:
            check_account_plans(self, shown_name(path), accts,
                                "the export holds", broker="Questrade",
                                column="Account Type")

    def _listing_currency(self, sym: str, currency: str) -> str:
        """The currency whose suffix a real (non-code) symbol takes: a
        symbol with no Canadian venue suffix that the account trades in
        exactly ONE listing currency keeps that listing — a USD stock
        bought from the CAD side (EXCHANGE RATE) paid a CAD dividend or
        ROC that landed on a phantom .TO pool (audit R1-68)."""
        if canonical_ca_listing(sym, currency) is not None:
            return currency
        curs = self._ctx.sym_curs.get(sym.upper()) if self._ctx else None
        if curs and len(curs) == 1:
            return next(iter(curs))
        return currency

    def _inferred_code(self, sym: str) -> Optional[Tuple[str, str]]:
        """(listing, currency) `tjs run` inferred for internal code
        `sym` from the project's other exports (lib/symbol_codes), or
        None."""
        r = self._ctx.inferred.get((sym or '').upper()) if self._ctx \
            else None
        if not r:
            return None
        return str(r['symbol']), str(r.get('currency') or '')

    def _journal_code(self, sym: str) -> Optional[Tuple[str, str]]:
        """(listing, currency) a BRW journal leg of the account names
        for internal code `sym` (_plan_qt_journals), or None."""
        r = self._ctx.journal_codes.get((sym or '').upper()) if self._ctx \
            else None
        return (str(r['symbol']), str(r['currency'])) if r else None

    def _rei_listing(self, row: Dict[str, Any], sym: str, cur: str
                     ) -> Tuple[str, str]:
        """(symbol, suffix currency) of a reinvestment (REI) row: a DRIP
        buys units of a position the account already holds, so a bare
        ticker on the other currency's row (Questrade pays a TSX
        stock's dividend on the USD side: `QZP` on a USD row) is the
        listing the account trades under the same name — as the dotted
        dividend it reinvests binds — not the row currency's (QZP.US: a
        listing no market may have, or another company's). Only when
        the account trades exactly ONE listing of that name, on the same
        root, and not the row currency's (GitHub issue #3)."""
        from taxjson.lib.cross_listings import listing_root
        own = self.apply_currency_suffix(sym, cur)
        cands = self._ctx.key_candidates(
            _get_desc_key(row.get('Description') or '')) if self._ctx \
            else set()
        listings = {self.apply_currency_suffix(s, c): (s, c)
                    for s, c in cands}
        if own in listings or len(listings) != 1:
            return sym, cur
        (lst, (s, c)), = listings.items()
        if listing_root(lst) != listing_root(own):
            return sym, cur
        return s, c

    def _resolve_symbol(self, row: Dict[str, Any], currency: str,
                        lineno: Optional[int] = None, rei: bool = False,
                        quiet: bool = False):
        """(symbol, suffix currency) for a non-trade row. Questrade
        writes some rows under an internal code (S098765, a TF6's
        R123456) or a dotted dividend code (.SAMPLP for an issuer held as
        SAMPLQ on the NYSE); ONLY those are rebound to the traded symbol
        of the same security (matched on the description, in ANY of
        the account's exports). A real ticker is kept: first-match-wins
        used to rebind, e.g., an FTN.PR dividend onto FTN.PRA because
        both share a description. A description that maps to several
        symbols is never guessed, and an internal code nothing resolves
        is said out loud (a ticker.map GLOBAL line fixes it). `quiet`
        (the account context's plans, which read every row before the
        parse does): the same answer, with no warning and no state."""
        raw = (row.get('Symbol') or '').strip().upper()
        sym = raw.lstrip('.')
        code_like = (not sym or bool(_INTERNAL_CODE_RE.match(sym))
                     or raw.startswith('.'))
        cands = self._ctx.key_candidates(
            _get_desc_key(row.get('Description') or ''),
            transfer_row=((row.get('Activity Type') or '').strip()
                          == 'Transfers'))
        if not code_like:
            cur = self._listing_currency(sym, currency)
            if rei:
                # A reinvestment buys the account's held listing
                # (_rei_listing).
                bound = self._rei_listing(row, sym, cur)
                if bound != (sym, cur):
                    return bound
            own = self.apply_currency_suffix(sym, cur)
            others = sorted({self.apply_currency_suffix(s, c)
                             for s, c in cands} - {own})
            if quiet or all(ticker_map_joins(own, o) for o in others):
                others = []     # ticker.map already folds them (A2-1056)
            if others:
                key = (own, tuple(others))
                if key not in self._ambiguous_warned:
                    self._ambiguous_warned.add(key)
                    emit_line(f"warning: {self._qt_name}: {sym!r} is booked "
                          f"under its own symbol, but the same security "
                          f"({(row.get('Description') or '')[:50]!r}) "
                          f"trades as {', '.join(others)} — if they are "
                          f"one security and ticker.map does not already "
                          f"fold them, add a ticker.map rule.")
            return sym, cur
        # A code a BRW journal leg of the same name in the same account
        # names (_plan_qt_journals): that leg's line, in its currency.
        _jc = self._ctx.journal_codes.get(sym)
        if _jc is not None:
            return str(_jc['symbol']), str(_jc['currency'])
        # The code-level resolution (`tjs run`, lib/symbol_codes)
        # first: one code is one security, so every row of it is booked
        # under ONE listing — a row whose own description matches
        # another listing (a warrant code's plain-worded row, the
        # common's name) does not split it (GitHub issue #4).
        inferred = (self._inferred_code(sym)
                    if _INTERNAL_CODE_RE.match(sym) else None)
        if inferred is not None:
            return inferred
        if len(cands) == 1:
            return next(iter(cands))
        if quiet:
            return sym, currency
        if len(cands) > 1:
            key = (sym, tuple(sorted(cands)))
            if ticker_map_renames(self.apply_currency_suffix(sym, currency)):
                self._ambiguous_warned.add(key)  # mapped (A2-1056)
            if key not in self._ambiguous_warned:
                self._ambiguous_warned.add(key)
                emit_line(f"warning: {self._qt_name}: {sym or '(blank)'!r} "
                      f"({(row.get('Description') or '')[:160]!r}) matches "
                      f"several traded symbols "
                      f"({', '.join(s for s, _ in sorted(cands))}) — not "
                      f"rebound; map it with a ticker.map rule (moot if "
                      f"ticker.map already maps it).")
        elif (_INTERNAL_CODE_RE.match(sym)
              and sym not in self._ctx.code_warned
              and not ticker_map_renames(
                  self.apply_currency_suffix(sym, currency))
              and not ticker_map_names(
                  self.apply_currency_suffix(sym, currency))):
            # A row booked under Questrade's internal code that no trade
            # or transfer in any export of the account resolves: the
            # position fragments (a ROC hits an empty pool and becomes a
            # gain; stock-dividend / DRIP / spinoff shares sit on a
            # phantom pool while the sale goes short). Loud, and a lint
            # finding (audit R1-67, S062-24, R1-3).
            # ONE line per code per account (not per file or row).
            self._ctx.code_warned.add(sym)
            where = self._where(lineno) if lineno else self._qt_name
            # Printed at parse time, before ticker.map is applied: say
            # it is moot once the line exists (audit S063-10).
            _key = self.apply_currency_suffix(sym, currency)
            _un = self._ctx.unresolved.get(sym) or {}
            _why = (f" ({_un['detail']})" if _un.get('detail') else "")
            msg = (f"{where}: {(row.get('Action') or '').strip() or '?'} "
                   f"row keeps internal symbol code {sym!r} "
                   f"({(row.get('Description') or '')[:160]!r}) — no trade "
                   f"or transfer in this account's exports resolves it, "
                   f"and no transfer or name elsewhere in the project "
                   f"identifies it{_why}. "
                   f"Unless ticker.map already maps {_key}, add "
                   f"GLOBAL {_key} "
                   f"<TICKER>.{self.apply_currency_suffix('X', currency)[2:]}"
                   f" or the position will fragment.")
            # ATTENTION: on the run console (re-audit A2-0027 — a ROC
            # on the code is a phantom gain with rc 0).
            emit_line(f"warning: ATTENTION: {msg}")
            self.lint_findings.append(msg)
        return sym, currency

    # ------------------------------------------------------------- parse

    def read(self, path: Path) -> List["_QtRow"]:
        """The account context (built for this file alone when the tool gave
        none), the rows in book order (`_in_real_order`), the taxable hint
        from the account types, the account mix checked; what the later
        phases need as `_qt`."""
        # The description -> (ticker, listing currency) map and the
        # position timelines come from ALL of the account's exports (the
        # shared context taxjson-brokerage builds via prepare_files); a
        # lone file gets a context of its own.
        path = Path(path)
        # Masked like every diagnostic (a default download name is the
        # account number).
        self._qt = _QtParse(path, shown_name(path), str(path.resolve()))
        self._ambiguous_warned: set = set()
        self.lint_findings: List[str] = []
        ctx = self.account_context
        if ctx is None or self._qt.key not in ctx.files:
            ctx = build_qt_account_context([path], helper=self)
            ctx.add_codes_note()
            ctx.emit()
        self._ctx = ctx
        ctx.plan_rows(self)
        self._desc_to_ticker = ctx.desc_to_ticker
        raw = _read_qt_rows(path, warn=True)
        # A fetch file of an older `tjs fetch`: said, never dropped
        # (backlog TJ-4955).
        _warn_blank_symbol_twins(self._qt.shown, raw)
        rows = self._in_real_order(path, raw)
        qrows = [_QtRow(row, lineno, header=_is_qt_header(row))
                 for lineno, row in rows]
        data = [r.cells for r in qrows if not r.header]    # the header repeated is no row

        # Taxable hint from the Account Type column: a registered-plan
        # marker -> sheltered; "margin"/"cash" without one -> taxable;
        # anything else ("API" from `tjs fetch`) -> unknown.
        _types = {(c.get('Account Type') or '').strip() for c in data}
        _types.discard('')
        if _types and all(plan_shelter_of(self.plans_taxed, t) for t in _types):
            self._qt_taxable_hint = False
        elif _types and all(is_taxable_account_type(self.plans_taxed, t)
                            for t in _types):
            self._qt_taxable_hint = True
        else:
            self._qt_taxable_hint = None
        self._check_account_mix(path, data)

        # Each row's broker account (`Account #`): every row emitted for
        # a CSV row carries it as `broker_account`, so cross-file dedup
        # never collapses two accounts' identical rows (audit A2-0008).
        self._qt_accounts = {(c.get('Account #') or '').strip() for c in data} - {''}
        return qrows

    def classify(self, r) -> Outcome:
        """The row's action and activity type read; the header repeated
        mid-file and an FX conversion non-events; its currency; then the
        chain by action code (the builder kept on the row for `build`):
        the stock split, the stock dividend, the dividend, the transfer,
        the fee, the cash-only rows (a deposit or withdrawal a non-event,
        the interest, stock-lending income a skip noted as unbooked), the
        cash in lieu, the reinvestment, the BRW journal, an action the
        parser does not know (a skip, noted when it moves shares), else
        the trade with its shape flags. A cash row (`_qt_cash_kind`) is
        noted for the file's cash events first, whatever the chain makes
        of it."""
        row, lineno = r.cells, r.line
        action_raw = r.action_raw = _canon_action(row.get('Action'))
        activity_type = r.activity_type = (row.get('Activity Type') or '').strip()
        desc = r.desc = row.get('Description') or ''
        if r.header:
            # The header repeated mid-file (concatenated exports).
            return Outcome.nonevent("repeated header row")
        if _qt_cash_kind(action_raw, activity_type):
            self.note_cash(r)           # its event the finish's (cash_events)
        if action_raw == 'FXT' or activity_type == 'FX conversion':
            # "CONVERSION - USD/CAD": the account's own cash moving
            # between denominations. Not a disposition of property
            # in this model — FX cash gains are reconstructed by
            # `tjs fx-cash` from security cash flows
            # (KNOWN_ISSUES) — so a recognized non-event, not an
            # unclassified action.
            return Outcome.nonevent(f"FX conversion "
                                    f"({action_raw or activity_type})")
        currency = r.currency = self._currency(row, lineno)
        # A stock split arrives as a DIS row tagged 'Dividends' with
        # 'STK SPLIT' in the description — checked BEFORE the dividend
        # branch, which would otherwise drop it as a $0 dividend.
        if (self._is_stock_split(action_raw, activity_type, desc)
                and not self._is_cash_only(row)):
            return r.build(self._build_split)
        if (action_raw == 'DIS' and _STK_DIV_RE.search(desc)
                and abs(self._num(row, 'Quantity', lineno)) > 1e-9):
            return r.build(self._build_stock_dividend)
        if action_raw == 'DIV' or activity_type == 'Dividends':
            return r.build(self._build_dividend)
        if action_raw == 'TF6' or activity_type == 'Transfers':
            return r.build(self._build_transfer)
        if action_raw == 'FCH' or activity_type == 'Fees and rebates':
            return r.build(self._build_fee)
        # The quantity, a blank 0, read once for the chain (the builders
        # that need it read it strictly, a blank an error, as before).
        _cash_q = r.q = parse_strict_number(
            row.get('Quantity'), field='Quantity',
            where=self._where(lineno), allow_blank=True, blank=0.0)
        if abs(_cash_q) < 1e-9 and not (row.get('Symbol') or '').strip():
            if (activity_type in _QT_CASH_MOVE_TYPES
                    or action_raw in _QT_CASH_MOVE_CODES):
                return Outcome.nonevent(f"cash deposit/withdrawal "
                                        f"({action_raw or activity_type})")
            if action_raw == 'INT' or activity_type == 'Interest':
                return r.build(self._build_interest)
            if action_raw == 'LFJ':
                _net = self._num(row, 'Net Amount', lineno)
                if abs(_net) >= 0.005:
                    return self._unbooked(
                        lineno, f"stock-lending income {M.shown(_net, 2):,.2f} "
                        f"{currency} ({desc[:50]!r}) — NOT booked; "
                        f"take it from the slip (a T5 / 1099-MISC).",
                        "stock-lending income (LFJ)")
        if action_raw == 'CIL' and _CIL_RE.search(desc):
            return r.build(self._build_cil)
        if action_raw == 'REI' or activity_type == 'Dividend reinvestment':
            return r.build(self._build_reinvestment)
        r.is_trade = action_raw in ('Buy', 'Sell')
        _du = desc.upper()
        r.code_expired = action_raw == 'EXP'
        r.code_assigned = action_raw in ('ASN', 'EX')
        word_expired = ' - EXPIRED' in _du
        r.word_assigned = 'ASSIGNMENT' in _du or 'EXERCISE' in _du
        r.is_expired = r.code_expired or word_expired
        r.is_assigned = r.code_assigned or r.word_assigned
        if not (r.is_trade or r.is_expired or r.is_assigned):
            _q = r.q
            if action_raw == 'BRW' and 'JOURNAL' in _du and abs(_q) > 1e-9:
                return r.build(self._build_journal)
            label = f"action {action_raw or activity_type or '?'!s}"
            if abs(_q) > 1e-9:
                return self._unbooked(
                    lineno, f"action {action_raw or '?'!r} moves "
                    f"{_q:g} {(row.get('Symbol') or '').strip() or '?'}"
                    f" ({desc[:60]!r}) — not an action the parser "
                    f"knows; NOT booked. Book it in a .tt file.", label)
            return Outcome.skip(label)
        return r.build(self._build_trade)

    def build(self, r) -> "List[Dict[str, Any]] | Outcome":
        """The builder classify chose, run; the row's account stamped on
        what it books (as the one loop stamped each row's rows)."""
        out = r.builder(r)
        acct = (r.cells.get('Account #') or '').strip()
        if acct and not isinstance(out, Outcome):
            for tx in out:
                tx.setdefault('broker_account', acct)
        return out

    @builds("split")
    def _build_split(self, r) -> "List[Dict[str, Any]] | Outcome":
        """A stock split (a DIS row tagged 'Dividends' with 'STK SPLIT' in
        the description, checked before the dividend branch, which would
        drop it as a $0 dividend): booked, or a skip noted as unbooked."""
        row, lineno, desc = r.cells, r.line, r.desc
        currency = r.currency
        net = parse_strict_number(row.get('Net Amount'), field='Net Amount',
                                  where=self._where(lineno), allow_blank=True,
                                  blank=0.0) or 0.0
        if abs(net) > 0.005:
            # Shares and cash on one split row: the cash was dropped
            # without a word (TJ-4548; R1-72 refuses the stock dividend's).
            raise BrokerageParseError(
                f"{self._where(lineno)}: a stock-split DIS row carries both a "
                f"quantity ({self._num(row, 'Quantity', lineno):g}) and cash "
                f"({M.shown(net, 2):,.2f}; {desc[:50]!r}) — refusing to guess "
                f"what the cash is. Split it into a share row at Net Amount "
                f"0.00 and a cash row of what the cash is (a DIV row for a "
                f"dividend), or book it in a .tt file.")
        tx = self._parse_split(row, currency, desc, lineno)
        if tx:
            return [tx]
        else:
            return self._unbooked(lineno, f"stock-split row not "
                                  f"understood ({desc[:60]!r}) — NOT "
                                  f"booked; the position's share count "
                                  f"is wrong until it is (a .tt SPLIT).",
                                  "stock-split row not understood (see warning)")

    @builds("stock_dividend")
    def _build_stock_dividend(self, r) -> "List[Dict[str, Any]] | Outcome":
        """A stock-dividend DIS row (shares, no cash): the shares at $0 as a
        stock-dividend event, its reversal recorded for the pairing after
        the loop (consumed, nothing booked)."""
        st = self._qt
        row, lineno, desc = r.cells, r.line, r.desc
        currency = r.currency
        rev_originals, reversals, sd_notes = st.rev_originals, st.reversals, st.sd_notes
        qty = self._num(row, 'Quantity', lineno)
        net_sd = parse_strict_number(
            row.get('Net Amount'), field='Net Amount',
            where=self._where(lineno), allow_blank=True, blank=0.0)
        if abs(net_sd) > 0.005:
            # Shares AND cash on one row (audit R1-72): booking
            # the shares dropped the cash.
            raise BrokerageParseError(
                f"{self._where(lineno)}: a stock-dividend DIS row "
                f"carries both a quantity ({qty:g}) and cash "
                f"({M.shown(net_sd, 2):,.2f}; {desc[:50]!r}) — refusing to "
                f"guess which part is the dividend. Split it into "
                f"a share row and a cash row, or book it in a .tt "
                f"file.")
        date, time = _row_clock(self._date(row, 'Transaction Date', lineno))
        sym_raw, scur = self._resolve_symbol(row, currency, lineno)
        sym = self.apply_currency_suffix(sym_raw, scur)
        sd_key = ('STK DIV', sym, M.units(abs(qty), 6), 0.0)
        if qty < 0:
            # A negated STK DIV row is Questrade REVERSING a
            # stock dividend: it cancels the original (paired at
            # the end of the parse). It used to be counted as
            # consumed and dropped, leaving phantom shares that
            # diluted the ACB (audit R1-65).
            reversals.append((sd_key, lineno, desc))
            return []
        sd_tx = {
            'action': 'BUYSELL',
            'date': date, 'time': time,
            'date_settle': date,
            'symbol': sym,
            'quantity': qty, 'currency': currency,
            'price': 0.0, 'net_amount': 0.0,
            'gross_amount': 0.0,
            'account': self.DEFAULT_ACCOUNT,
            'description': desc,
            'type': STOCK_DIVIDEND,
        }
        _grp = [sd_tx]
        rev_originals.setdefault(sd_key, []).append(_grp)
        self._qt.orig_line[id(_grp)] = lineno
        sd_notes.append((sd_tx,
                         f"NOTE: {sym_raw}: stock dividend of "
                         f"{qty:g} share(s) on {date} booked as a "
                         f"stock-dividend event — the gains run "
                         f"applies your country's rule to its cost "
                         f"(`tjs tax-logic`)."))
        return [sd_tx]

    @builds("dividend", "dividend_in_lieu", "roc")
    def _build_dividend(self, r) -> "List[Dict[str, Any]] | Outcome":
        """A DIV row: a quantity-bearing DIS leg is corp-actions' (a non-event)
        or unbooked (a skip); else the dividend (`_parse_dividend`), a
        net-of-tax one noted, a book-cost adjustment a skip noted as
        unbooked, a zero-net row a non-event."""
        st = self._qt
        row, lineno, desc = r.cells, r.line, r.desc
        currency, action_raw = r.currency, r.action_raw
        ca_legs, net_of_tax = st.ca_legs, st.net_of_tax
        qty_col = self._num(row, 'Quantity', lineno)
        net_col = self._num(row, 'Net Amount', lineno)
        if abs(qty_col) > 1e-9 and abs(net_col) >= 1e-9:
            # Shares AND cash on one income row (audit R1-72):
            # the dividend branch booked the cash and silently
            # dropped the delivered shares.
            raise BrokerageParseError(
                f"{self._where(lineno)}: a {action_raw or 'DIS'} "
                f"row carries both a quantity ({qty_col:g}) and "
                f"cash ({M.shown(net_col, 2):,.2f}; {desc[:50]!r}) — refusing "
                f"to book the cash and drop the shares. Split it "
                f"into a share row and a cash row, or book it in "
                f"a .tt file.")
        if abs(qty_col) > 1e-9:
            # A quantity-bearing, zero-cash DIS row is a
            # CORPORATE-ACTION leg (warrant spinoff / rights
            # distribution chain: +100 / -100 / +100), not an
            # informational dividend. taxjson-corp-actions
            # books the spinoff chains (run by `tjs run`);
            # anything else is unclassified — loud.
            _sym = (row.get('Symbol') or '').strip() or '?'
            if _QT_CA_LEG_RE.search(desc):
                ca_legs.append(f"{_sym} {qty_col:+g}")
                if _INTERNAL_CODE_RE.match(_sym.upper()):
                    # Say it when nothing resolves the code
                    # (audit R1-3): the chain would book on it.
                    self._resolve_symbol(row, currency, lineno)
                return Outcome.nonevent("DIS corporate-action leg (quantity-bearing; "
                            "booked by taxjson-corp-actions)")
            else:
                return self._unbooked(
                    lineno, f"DIS row moves {qty_col:g} {_sym} "
                    f"with no cash ({desc[:60]!r}) — not a "
                    f"dividend, split, stock dividend or spinoff "
                    f"leg the parser knows; NOT booked. Book it "
                    f"in a .tt file.", f"DIS row with a quantity and no cash "
                    f"({_sym}) — not a dividend, not booked")
        tx = self._parse_dividend(row, currency, desc, lineno)
        if tx:
            if (_NONRES_NET_RE.search(desc)
                    and tx['action'] in ('DIVIDEND',
                                         'DIVIDEND_IN_LIEU')
                    and float(tx['net_amount']) > 0):
                net_of_tax.append(
                    f"{tx['symbol']} {tx['date']} "
                    f"{M.shown(tx['net_amount'], 2):.2f}")
            return [tx]
        elif _QT_BOOK_COST_RE.search(desc):
            # A zero-cash row that STATES a book-cost change
            # (RBC's year-end ROC / notional distribution shape,
            # re-audit A2-1062): the ACB moves and nothing here
            # books it — not an informational row.
            return self._unbooked(
                lineno, f"{(row.get('Symbol') or '').strip() or '?'}"
                f": the description states a book-cost adjustment "
                f"({desc[:80]!r}) on a row with no cash — NOT "
                f"booked; the ACB is wrong until it is. Book it as "
                f"a .tt ADJUST (a return of capital lowers the "
                f"ACB, a notional distribution raises it).",
                "book-cost adjustment row (see warning)")
        else:
            # Zero-net informational row: was marked consumed
            # with nothing emitted, which the lint reconciled
            # but the skip summary never showed.
            return Outcome.nonevent("zero-net dividend row "
                                        "(informational)")

    @builds("transfer")
    def _build_transfer(self, r) -> "List[Dict[str, Any]] | Outcome":
        """A TF6 row (`_parse_transfer`): a transfer-in at $0 noted; a
        cash-only journal a non-event."""
        st = self._qt
        row, lineno, currency = r.cells, r.line, r.currency
        no_book_value = st.no_book_value
        tx = self._parse_transfer(row, currency, lineno)
        if tx:
            if (tx['quantity'] > 0 and not tx.get('_book_value')):
                no_book_value.append(
                    f"{tx['quantity']:g} {tx['symbol']} "
                    f"({tx['date']})")
            tx.pop('_book_value', None)
            return [tx]
        else:
            return Outcome.nonevent("cash-only transfer journal")

    @builds("fee")
    def _build_fee(self, r) -> "List[Dict[str, Any]] | Outcome":
        """An FCH row (`_parse_fee`); a zero-amount one a non-event."""
        row, lineno, desc = r.cells, r.line, r.desc
        currency = r.currency
        tx = self._parse_fee(row, currency, desc, lineno)
        if tx:
            return [tx]
        else:
            return Outcome.nonevent("zero-amount fee row")

    @builds("interest")
    def _build_interest(self, r) -> "List[Dict[str, Any]] | Outcome":
        """A cash-only INT row: the interest (a zero one a non-event)."""
        row, lineno, desc = r.cells, r.line, r.desc
        currency = r.currency
        _net = self._num(row, 'Net Amount', lineno)
        if abs(_net) < 0.005:
            return Outcome.nonevent("zero interest row")
        _day, _clock = _row_clock(self._date(row, 'Transaction Date', lineno))
        return [{
            'action': 'INTEREST',
            'date': _day,
            'time': _clock,
            'date_settle': _day,
            'symbol': 'CASH', 'quantity': 0.0,
            'currency': currency, 'net_amount': _net,
            'type': 'interest',
            'account': self.DEFAULT_ACCOUNT,
            'description': desc,
        }]

    @builds("transfer")
    def _build_journal(self, r) -> "List[Dict[str, Any]] | Outcome":
        """A BRW journal row moving units between the CAD and USD lines of
        one security: a TRANSFER leg, its pair and book value when the
        description and the account context give them."""
        st = self._qt
        row, lineno, desc = r.cells, r.line, r.desc
        currency, _q = r.currency, r.q
        journals, journal_txs, key = st.journals, st.journal_txs, st.key
        # A currency journal's leg (_plan_qt_journals): its
        # line's listing — the website export names both
        # legs by the bare symbol, and the USD leg is the
        # security's US-dollar class (SAMPLF.U.TO), not a
        # US listing. A leg with a blank Symbol takes its
        # partner's security.
        _jl = self._ctx.journal_legs.get(
            (key, lineno)) or {}
        if not (row.get('Symbol') or '').strip() and not _jl.get('symbol'):
            # Booked as a TRANSFER of symbol '' (TJ-4541); a paired
            # leg's partner names it (review round 1).
            raise BrokerageParseError(
                f"{self._where(lineno)}: a BRW journal row has no Symbol "
                f"({desc[:60]!r}, {_q:+g}) — refusing to book a move of no "
                f"security. Write the security's symbol in the cell (its "
                f"partner row names it), or book the move in a .tt file.")
        _bv = _BRW_BOOK_VALUE_RE.search(desc)
        _date = self._date(row, 'Transaction Date', lineno)
        _jtx = {
            'action': 'TRANSFER',
            'date': _date.strftime('%Y-%m-%d'),
            'time': _date.strftime('%H:%M:%S'),
            'date_settle': _date.strftime('%Y-%m-%d'),
            'symbol': _jl.get('symbol')
            or self.apply_currency_suffix(
                (row.get('Symbol') or '').strip().upper(),
                currency),
            'quantity': _q, 'currency': currency,
            'price': 0.0, 'net_amount': 0.0,
            'gross_amount': 0.0,
            'account': self.DEFAULT_ACCOUNT,
            'description': desc,
        }
        if _jl.get('pair'):
            # The pair's id: `tjs run` joins the two lines
            # as a ticker.map JOURNAL line would (Canada,
            # tax-logic CA-XLIST-03; lib/cross_listings).
            _jtx['journal_pair'] = _jl['pair']
        if _bv:
            _jtx['book_value'] = parse_strict_number(
                _bv.group(1), field='BOOK VALUE',
                where=self._where(lineno))
            _cnv = _BRW_CNV_RE.search(desc)
            if _cnv:
                _jtx['_cnv'] = desc_number(
                    _cnv.group(1), where=self._where(lineno),
                    field='CNV@ rate')
        journals.append(f"{_jtx['symbol']} {_q:+g}")
        journal_txs.append(_jtx)
        return [_jtx]

    @builds("trade")
    def _build_cil(self, r) -> "List[Dict[str, Any]] | Outcome":
        """A CIL row (`_parse_cash_in_lieu`): the legs, or the Outcome the
        builder found (no fraction or cash: a skip; a reversal read into
        the pairing: consumed, nothing booked)."""
        st = self._qt
        return self._parse_cash_in_lieu(r.cells, r.currency, r.desc, r.line,
                                        st.rev_originals, st.reversals)

    @builds("trade")
    def _build_reinvestment(self, r) -> "List[Dict[str, Any]] | Outcome":
        """An REI row (`_parse_reinvestment`): the reinvestment, or the Outcome
        the builder found (no shares and no cash: a non-event; a reversal
        read into the pairing: consumed, nothing booked)."""
        st = self._qt
        tx = self._parse_reinvestment(r.cells, r.currency, r.desc, r.line,
                                      st.rev_originals, st.reversals)
        if isinstance(tx, Outcome):
            return tx
        st.reinvestments.add(id(tx))
        return [tx]

    def _trade_shape(self, r) -> "_QtShape | Outcome":
        """The row's date and time, the option parsed from the description
        (none when the Symbol column names the underlying), the settle the
        export gives, the quantity, the multiplier assumed for an option,
        whether the row carries cash and what that makes of the expiry and
        assignment flags (an expiry or an option leg carrying cash refused;
        a zero-cash row that is no option leg a skip), a Buy with a
        negative quantity refused, the quantity signed."""
        row, lineno, desc = r.cells, r.line, r.desc
        action_raw, activity_type = r.action_raw, r.activity_type
        is_trade, code_expired = r.is_trade, r.code_expired
        code_assigned, word_assigned = r.code_assigned, r.word_assigned
        is_expired = r.is_expired
        dt = self._date(row, 'Transaction Date', lineno)
        date = dt.strftime("%Y-%m-%d")
        time = dt.strftime("%H:%M:%S")

        opt = self.parse_option_from_description(desc)
        _sym_col = (row.get('Symbol') or '').strip().upper()
        if opt and _sym_col and names_underlying(opt['base'],
                                                  _sym_col):
            # The Symbol column names the UNDERLYING's listed ticker:
            # this is the stock leg of an assignment/exercise whose
            # description quotes the contract ("ABC CORP ASSIGNMENT
            # OF OPTION CALL ABC 05/16/25 50"). Taking the option
            # identity from the text booked -100 contracts at $0
            # and dropped the sale (audit S062-19).
            opt = None
        settle_dt = self._date(row, 'Settlement Date', lineno,
                               required=False)
        # A BLANK settlement cell falls back to the standard cycle,
        # computed once the symbol (its listing) is known below.
        date_settle = (settle_dt.strftime("%Y-%m-%d") if settle_dt
                       else None)

        qty = self._num(row, 'Quantity', lineno)
        # The row's OWN money decides whether it is a zero-cash
        # option leg (audit R1-63). A word in the description used
        # to zero price, cash and commission on ANY row: an ASN
        # stock leg (100 @ strike, Net -5000) was booked at $0 cost,
        # a Buy/Sell of "ACME EXERCISE EQUIPMENT" vanished, and a
        # "... RIGHTS - EXPIRED" sale lost its proceeds. Only a row
        # with no cash is an expiry / assignment option leg; a row
        # WITH cash keeps it (an ASN/EX stock leg stays the marked
        # ASSIGN leg, which consumes the staged premium).
        _where = self._where(lineno)
        carries_cash = any(
            abs(parse_strict_number(row.get(c), field=c, where=_where,
                                    allow_blank=True, blank=0.0))
            > 0.005 for c in ('Gross Amount', 'Net Amount'))
        if carries_cash:
            if code_expired:
                raise BrokerageParseError(
                    f"{_where}: an EXP (expiry) row carries cash "
                    f"(Gross {row.get('Gross Amount')!r}, Net "
                    f"{row.get('Net Amount')!r}; {desc[:50]!r}) — an "
                    f"expiry has none; refusing to guess whether it "
                    f"is a sale.")
            if code_assigned and opt:
                raise BrokerageParseError(
                    f"{_where}: an {action_raw} option leg carries "
                    f"cash ({desc[:50]!r}) — a cash-settled exercise "
                    f"is not a shape this parser books; refusing to "
                    f"guess.")
            is_expired = False
            is_assigned = code_assigned       # the marked stock leg
            if is_assigned:
                # Direction from the cash: shares bought (Net < 0)
                # or delivered (Net > 0) on the assignment.
                _net = parse_strict_number(
                    row.get('Net Amount'), field='Net Amount',
                    where=_where)
                qty = abs(qty) if _net < 0 else -abs(qty)
        else:
            # A zero-cash row: an option's expiry/assignment leg, or
            # an expiring right (EXP / "- EXPIRED") at $0.
            is_assigned = code_assigned or (word_assigned and bool(opt))
            if is_assigned and not opt:
                raise BrokerageParseError(
                    f"{_where}: an {action_raw} row for "
                    f"{(row.get('Symbol') or '').strip()!r} is not an "
                    f"option and carries no cash ({desc[:50]!r}) — "
                    f"booking the shares at $0 would misstate the "
                    f"ACB; refusing to guess.")
            if not (is_trade or is_expired or is_assigned):
                return Outcome.skip(f"action {action_raw or activity_type or '?'!s} "
                        f"(zero-cash, not an option leg)")
        if is_trade:
            if abs(qty) < QTY_ZERO:
                # A trade of no units, with cash or without, stopped `tjs
                # run` at the schema check, naming no row (TJ-4543).
                raise zero_units_refusal(_where, action_raw,
                                         row.get('Quantity'))
            if action_raw == 'Buy' and qty < 0:
                # Questrade signs a buy positive and a sale negative;
                # a Buy with a negative Quantity was flipped to a buy
                # silently (re-audit A2-1057). Refused, as the
                # generic importer does.
                raise BrokerageParseError(
                    f"{self._where(lineno)}: a Buy row with a NEGATIVE "
                    f"Quantity {qty:g} ({desc[:50]!r}) — the action "
                    f"says purchase, the quantity says sale; refusing "
                    f"to guess.")
            qty = self.signed_quantity(qty, action_is_sell=(action_raw == 'Sell'))
        return _QtShape(carries_cash=carries_cash, date=date, date_settle=date_settle,
                        is_assigned=is_assigned, is_expired=is_expired, opt=opt, qty=qty,
                        time=time)

    def _trade_money(self, r, sh) -> "_QtMoney":
        """The price, the net and the commission: none for an expiry or an
        assignment without cash; else read from the row, the option size
        fitted to the gross, the commission's sign from the cash against
        the gross (an FX-settled row compared at its rate) and the money
        checked; an FX-settled CAD row relisted in USD at the rate."""
        row, lineno, desc = r.cells, r.line, r.desc
        currency = r.currency
        carries_cash, is_assigned = sh.carries_cash, sh.is_assigned
        is_expired, opt, qty = sh.is_expired, sh.opt, sh.qty
        mult = float(self.OPTION_MULTIPLIER) if opt else 1.0
        # The export never states a contract's size: 100 is assumed
        # unless the row's Gross shows a mini (B10).
        size_basis = 'assumed' if opt else ''
        if (is_expired or is_assigned) and not carries_cash:
            price = 0.0
            net = 0.0
            comm = 0.0
        else:
            price = self._num(row, 'Price', lineno)
            gross_signed = self._num(row, 'Gross Amount', lineno)
            comm_signed = self._num(row, 'Commission', lineno)
            net_signed = self._num(row, 'Net Amount', lineno)
            gross = abs(gross_signed)
            if opt and abs(price) > 0:
                def _fits(m, _q=qty, _p=price, _g=gross):
                    exp = abs(_q) * abs(_p) * m
                    return money_close(_g, exp, abs_tol=0.02, rel_tol=0.002)
                mult, size_basis = self.option_row_multiplier(
                    (row.get('Symbol') or '').strip() or desc[:30],
                    _fits)
            # SIGNED commission. The old abs(Commission) made a
            # REBATE a charge (2x the rebate wrong). The cash truth
            # is Questrade's own Net Amount (a buy's negative, a
            # sell's positive): the commission is a charge when a
            # buy cost MORE than its gross / a sell brought in
            # LESS, a rebate otherwise — and its size must be the
            # Commission column's (checked in _check_trade_money).
            cash = -net_signed if qty > 0 else net_signed
            fx_settled = bool(_FX_SETTLED_RE.search(desc))
            _rate = 1.0
            if r.is_trade and not opt and not price and abs(gross) > 0.005:
                # Shares bought or sold at Price 0 with a Gross: booked at
                # price 0, the Gross unchecked (the check needs a price;
                # TJ-4543). An assignment's or exercise's stock leg at
                # Price 0 is booked at its cash, as before (review round 1).
                raise BrokerageParseError(
                    f"{self._where(lineno)}: {r.action_raw} {qty:g} "
                    f"{(row.get('Symbol') or '').strip()} at Price 0 with "
                    f"Gross Amount {M.shown(gross_signed, 2):,.2f} — a share "
                    f"trade at Price 0 carrying cash: the Price cell is cut "
                    f"or the columns are shifted; refusing to book it. Fill "
                    f"the Price from the statement (the row is then checked "
                    f"against its Gross), or delete the row and enter the "
                    f"trade as a .tt line.")
            if fx_settled:
                # Net is CAD, Gross USD: compare in USD.
                _fx = _FX_SETTLED_RE.search(desc)
                _rtxt = _fx.group(1) if _fx else '0'
                _rate = float(_rtxt)
                if _rate <= 0:
                    # Booked on the CAD listing at the USD price (TJ-4542).
                    raise BrokerageParseError(
                        f"{self._where(lineno)}: a CAD-settled trade states "
                        f"EXCHANGE RATE {_rtxt} ({desc[:60]!r}) — no "
                        f"rate converts its USD price; refusing to book it. "
                        f"Correct the rate and the Net Amount from the trade "
                        f"confirmation.")
                cash_cmp = cash / _rate
            else:
                cash_cmp = cash
            diff = (cash_cmp - gross) if qty > 0 else (gross - cash_cmp)
            comm = (math.copysign(abs(comm_signed), diff)
                    if abs(comm_signed) > 0 else 0.0)
            net = (gross + comm) if qty > 0 else (gross - comm)
            self._check_trade_money(lineno, desc, qty, price,
                                    gross_signed, comm_signed,
                                    diff, mult, fx_settled,
                                    cash=cash, rate=_rate)

        # CAD-settled US trade (see _FX_SETTLED_RE): the security is
        # the US listing, and the ACB is the CAD the account paid —
        # Net Amount — not the USD gross read as if it were CAD
        # (which under-stated a Broadcom buy by the whole exchange
        # rate and filed it as SAMPMF.TO, a CDR-shaped symbol the
        # DISTINCT rule then kept apart from the real pool).
        listing_currency = currency
        fx_m = _FX_SETTLED_RE.search(desc) if carries_cash else None
        if fx_m and currency.upper() == 'CAD' and float(fx_m.group(1)) > 0:
            rate = float(fx_m.group(1))
            listing_currency = 'USD'
            price = M.units(price * rate, 8)
            # SIGNED, like the non-FX branch (audit S014-06): a buy's
            # cost is -Net, a sale's proceeds +Net — abs() turned a
            # sale whose commission exceeded its gross (Net < 0)
            # into positive proceeds.
            if abs(net_signed) > 0:
                net = -net_signed if qty > 0 else net_signed
            else:
                net = M.units(net * rate, 8)
            # The commission in CAD too (re-audit A2-0615): it stayed
            # the USD-sized figure on a row whose price, gross and
            # net are CAD, so gross + commission != net and the fees
            # report / Schedule 3 outlays were short by the rate.
            # The CAD truth is the cash: the gap between Net and the
            # CAD gross.
            if comm:
                _gross_cad = abs(qty) * price * mult
                comm = M.units((net - _gross_cad) if qty > 0
                               else (_gross_cad - net), 2)
        return _QtMoney(comm=comm, listing_currency=listing_currency, mult=mult, net=net, price=price, size_basis=size_basis)

    @builds("trade", "expiry", "assign")
    def _build_trade(self, r) -> "List[Dict[str, Any]] | Outcome":
        """A Buy / Sell, an expiry or an assignment (the flags classify set):
        its shape (`_trade_shape`; the skip it may find), its money
        (`_trade_money`), then the symbol (an option's OCC form, an internal
        code resolved), the settle the market gives when the export does
        not, an expiry dated its booking day, the dates checked, the row
        (an expiry kept for the template's clamp)."""
        sh = self._trade_shape(r)
        if isinstance(sh, Outcome):
            return sh
        m = self._trade_money(r, sh)
        row, lineno, desc = r.cells, r.line, r.desc
        currency = r.currency
        code_assigned, word_assigned = r.code_assigned, r.word_assigned
        date, date_settle = sh.date, sh.date_settle
        is_assigned, is_expired = sh.is_assigned, sh.is_expired
        opt, qty, time = sh.opt, sh.qty, sh.time
        comm, listing_currency, mult, net = m.comm, m.listing_currency, m.mult, m.net
        price, size_basis = m.price, m.size_basis
        if opt:
            symbol = self.format_occ_symbol(
                opt['right'], opt['base'], opt['expiry'], opt['strike'],
                where=self._where(lineno))
        else:
            # Upper-cased: 'sample.to' split the pool from SAMPLE.TO
            # (audit R1-71).
            symbol = (row.get('Symbol') or '').strip().upper()
            if symbol.startswith('.') and set(
                    symbol.strip('.').split('.')) <= known_suffixes() | {'U'}:
                # '.TO' was booked as a symbol of its own (TJ-4543).
                raise BrokerageParseError(
                    f"{self._where(lineno)}: {r.action_raw} row's Symbol "
                    f"{symbol!r} is a listing suffix with no ticker "
                    f"(missing required field 'symbol') — refusing to book "
                    f"it. Fill in the security's symbol from the statement.")
            # An internal code the run resolved from the project's
            # other exports (a later sale of transferred-in shares).
            # Or one a BRW journal leg of the account names (the
            # USD class of a security journaled from its CAD line).
            _inf = (self._journal_code(symbol.lstrip('.'))
                    or self._inferred_code(symbol.lstrip('.'))
                    if _INTERNAL_CODE_RE.match(symbol.lstrip('.'))
                    else None)
            if _inf is not None:
                symbol, listing_currency = _inf
        symbol = self.apply_currency_suffix(symbol, listing_currency)
        if date_settle is None:
            # The LISTING's market decides the cycle and calendar,
            # not the row currency (A2-1052 / A2-1054): SAMPLF.U.TO in
            # USD settles through CDS (on the days both Canada and
            # the US settle, TJ-0705), a CAD-settled US stock on the
            # US calendar (lib/dates.settlement_calendar, the rule
            # every parser shares).
            from taxjson.lib.dates import settlement_calendar
            _mkt = settlement_calendar(symbol, listing_currency)
            if opt and not (code_assigned or word_assigned):
                # Options settle T+1 in every era; the equity
                # fallback (T+2 before the 2024 cutover) moved a
                # Dec-28 option sale into the next tax year (audit
                # R1-194). An exercise/assignment leg keeps its stock
                # leg's cycle — the two rows are one event.
                date_settle = self.settlement_date_t1(
                    date, "%Y-%m-%d", currency=_mkt)
            else:
                # Era- and market-aware fallback for a BLANK
                # settlement cell only (T+2 pre-cutover equities); a
                # present but unparseable cell is an error above.
                date_settle = self.equity_settlement_date(
                    date, _mkt, "%Y-%m-%d")

        if is_expired and not is_assigned:
            # An expiry has no settlement cycle, and Questrade posts
            # it the NEXT business day (a Friday expiry arrives
            # dated Monday): book it on the contract's own expiry
            # date, settle == date. The blank-settle fallback above
            # also added T+1 on top — a Dec-31 expiry crossed into
            # the next tax year either way.
            if opt:
                date = option_expiry_booking_date(
                    date, opt['expiry'])
                time = '16:00:00'
            else:
                # A right / warrant: its expiry date from the
                # description too (audit S065-04).
                date = non_option_expiry_booking_date(date, desc)
            date_settle = date
        self.check_settle_order(date, date_settle,
                                where=self._where(lineno),
                                what=repr(desc[:50]))

        _tx = {
            'action': 'ASSIGN' if is_assigned else 'BUYSELL',
            'date': date,
            'time': time,
            'date_settle': date_settle,
            'symbol': symbol,
            'quantity': qty,
            'currency': currency,
            'price': price,
            'commission': comm,
            'net_amount': net,
            # ×100 contract multiplier for options (mirrors RBC /
            # Webull's theoretical_gross): qty*price alone carried a
            # notional 100× too small into fee bucketing and per-row
            # reports.
            'gross_amount': self.theoretical_gross(
                qty, price, is_option=bool(opt), multiplier=mult),
            # Declared contract size: the schema notional check is
            # an error, not a guess, for rows that carry it. Its
            # basis says whether the export showed it (a mini's
            # Gross) or 100 was assumed (the engine notes that).
            'multiplier': mult,
            'account': self.DEFAULT_ACCOUNT,
            # Carry the raw description so a description-keyed
            # --security-overrides rule can correct a mislabeled ticker —
            # IB/RBC/Webull trade rows already do; Questrade's was the gap.
            'description': desc,
        }
        if size_basis:
            _tx['contract_size_basis'] = size_basis
        if not opt and not is_expired and not is_assigned:
            self.warn_zero_cost_buy(self._where(lineno), symbol, qty,
                                    price, net)
        if is_expired and not is_assigned:
            self._expiries.append(_tx)
        return [_tx]

    def finish(self, transactions: List[Dict[str, Any]], path: Path
               ) -> List[Dict[str, Any]]:
        """After the loop: the reversals paired with their originals, the
        stock-dividend notes of the rows kept, the corp-actions legs, the
        net-of-tax dividends and the $0 transfer-ins noted, the journal
        pairs noted (their costing follows the template's marks:
        `after_marks`); the file's cash events (`cash_events`, or why not,
        `cash_problem`: `_cash_events_of_rows`)."""
        st = self._qt
        rev_originals, reversals, sd_notes = st.rev_originals, st.reversals, st.sd_notes
        ca_legs, net_of_tax, no_book_value = st.ca_legs, st.net_of_tax, st.no_book_value
        journals, shown = st.journals, st.shown
        self._pair_reversals(transactions, rev_originals, reversals)
        _credits_before_reinvestments(transactions, st.reinvestments)
        _kept = {id(t) for t in transactions}
        for _tx, _msg in sd_notes:
            if id(_tx) in _kept:
                emit_line(_msg)
        if ca_legs:
            emit_line(f"note: {shown}: {len(ca_legs)} quantity-bearing DIS "
                  f"corporate-action leg(s) ({', '.join(ca_legs[:8])}"
                  f"{', ...' if len(ca_legs) > 8 else ''}) are not "
                  f"booked by the parser — taxjson-corp-actions books "
                  f"spinoff/rights chains (`tjs run` does this); "
                  f"check the position if you parse without it.")
        taxable = self._is_taxable()
        if net_of_tax and taxable is not False:
            # ATTENTION (re-audit A2-0276 / A2-0282): income and the
            # foreign tax credit are wrong until the slip is used.
            emit_line(f"warning: ATTENTION: {shown}: {len(net_of_tax)} "
                  f"dividend(s) "
                  f"marked NON-RES TAX WITHHELD are booked at the NET "
                  f"amount — the export gives neither the gross nor the "
                  f"tax ({'; '.join(net_of_tax[:6])}"
                  f"{'; ...' if len(net_of_tax) > 6 else ''}). In a "
                  f"TAXABLE account the income is understated and the "
                  f"foreign tax credit missing: take the gross and the "
                  f"withholding from "
                  f"{self.law('the T5/NR4 slip', 'the year-end tax statement (Form 1099-DIV / 1042-S, or the NR4 a Canadian payer issues)', 'the year-end tax slip')}"
                  f"."
                  f"{'' if taxable else ' (Account type unknown — ignore in a registered account.)'}")
        if (no_book_value and taxable is not False
                and not self.transfer_costs_checked_downstream):
            # What actually happens (audit S063-00): a TAXABLE account's
            # TRANSFER rows are custody evidence kept OUT of the books,
            # so the shares never enter the pool and their later sale
            # reads short — no gain at all. The remedy the pipeline
            # accepts is the real acquisition as a backdated .tt BUYSELL
            # (or a .tt OPENING cost=unknown line); a .tt TRANSFER is refused in a taxable
            # account and there is no OPENING_BALANCE .tt action.
            # ATTENTION (re-audit A2-0276 / A2-0283): the sale reads
            # as a short and the year's gain is missing.
            emit_line(f"warning: ATTENTION: {shown}: {len(no_book_value)} "
                  f"transfer-in(s) carry no TRANSFER BOOK VALUE "
                  f"({'; '.join(no_book_value[:6])}"
                  f"{'; ...' if len(no_book_value) > 6 else ''}). In a "
                  f"TAXABLE account transfers are kept out of the books, "
                  f"so these shares have no cost and their sale will "
                  f"read as a short: book the real acquisition (date, "
                  f"quantity, the sending broker's "
                  f"{self.law('ACB', 'cost basis', 'cost')}) as a .tt BUYSELL "
                  f"row, or open it at an unknown cost with a .tt OPENING "
                  f"cost=unknown line (`tjs find-missing-history "
                  f"--write-missing-history`)."
                  f"{'' if taxable else ' (Account type unknown — ignore in a registered account.)'}")
        if journals:
            emit_line(f"note: {shown}: {len(journals)} BRW journal row(s) "
                  f"move units between the CAD and USD lines of one "
                  f"security ({', '.join(journals[:6])}) — booked as "
                  f"TRANSFER legs; in a Canadian project `tjs run` "
                  f"joins a paired journal's two lines as one security, "
                  f"as a ticker.map TOBASE line (e.g. TOBASE "
                  f"SAMPLF.U.TO SAMPLF.TO) would, so the pair nets out.")
        self._cash_events_from(self._cash_events_of_rows)
        return transactions

    def _cash_events_of_rows(self) -> List[Dict[str, Any]]:
        """The file's cash events for the FX-on-cash ledger v2
        (lib/cash_events, tax-logic CA-FX-07), from the cash rows classify
        noted (`note_cash`): each FX conversion (FXT — Questrade prints its
        two legs as two rows of one account and day, the currency left
        negative and the one received positive) as one FXCONV; each
        deposit, withdrawal or contribution (CON, DEP, EFT, EWD, WDR, CTR)
        and each cash-only transfer journal (TF6 with no units) as a
        CASHMOVE; stock-lending income (LFJ, which the books do not carry)
        as a FLOW. A conversion leg with no other leg that day is a
        CASHMOVE the user declares. The export carries no cash balance: a
        CASHBAL .tt line gives the ledger one.

        The rows were noted in the order they happened (`_qt_real_order`:
        a newest-first export bottom-up), and the events keep that order —
        v2 replays one day's events in intake order. The two legs of one
        conversion are adjacent: a leg pairs with the account's previous
        conversion leg that day when that one moved the other way in
        another currency. Read in file order, a newest-first export
        replayed two same-day conversions backward and paired a leg with
        the wrong conversion (audit finding 75). A cash row whose Quantity
        or Net Amount cannot be read (`_cash_amounts`), or whose
        Transaction Date is not a date (`_cash_day`), raises
        BrokerageParseError naming the line: the file's `cash_problem`
        (`_cash_events_from`; the ledger computes nothing), never a refused
        parse. Once lib/cash_readers/questrade.py read the export a second
        time (PLAN step 16 commit 9)."""
        from taxjson.lib.privacy import hash_broker_account
        from taxjson.lib import cash_lines as CL
        name = self._qt.shown
        # One slot per event, in real order; a conversion takes the slot of
        # its first leg and fills it when its second leg arrives.
        out: List[Optional[Dict[str, Any]]] = []
        # (account, day, settle) -> (slot, leg) of the leg awaiting its pair
        pending: Dict[Tuple[str, str, str], Tuple[int, Dict[str, Any]]] = {}

        def _lone(key, slot, leg):
            acct, d, s = key
            ev = CL.move(d, leg["cur"], leg["net"], settle=s, where=name,
                         account=acct,
                         desc="FXT conversion leg with no other leg")
            ev['line_where'] = leg["where"]
            out[slot] = ev

        def _fx_leg(key, leg):
            prev = pending.pop(key, None)
            if prev is not None:
                slot, first = prev
                if (first["cur"] != leg["cur"]
                        and (first["net"] < 0) != (leg["net"] < 0)):
                    o, i = (first, leg) if first["net"] < 0 else (leg, first)
                    acct, d, s = key
                    ev = CL.conv(d, o["cur"], -o["net"], i["cur"], i["net"],
                                 settle=s, where=name, account=acct,
                                 desc="FXT")
                    ev['line_where'] = o["where"]
                    out[slot] = ev
                    return
                _lone(key, slot, first)
            out.append(None)
            pending[key] = (len(out) - 1, leg)

        for r in self._cash_rows:
            row, lineno = r.cells, r.line
            action, atype = r.action_raw, r.activity_type
            qty, net = self._cash_amounts(r)
            cur = (row.get('Currency') or '').strip().upper()
            if not cur or abs(net) < 0.005:
                continue
            is_fx = action == 'FXT' or atype == 'FX conversion'
            is_move = (abs(qty) < 1e-9 and not (row.get('Symbol') or '').strip()
                       and (atype in _QT_CASH_MOVE_TYPES
                            or action in _QT_CASH_MOVE_CODES))
            is_tf = (abs(qty) < 1e-9 and (action == 'TF6'
                                          or atype == 'Transfers'))
            is_lfj = action == 'LFJ'
            if not (is_fx or is_move or is_tf or is_lfj):
                continue
            d = self._cash_day(row, 'Transaction Date', lineno)
            s = self._cash_day(row, 'Settlement Date', lineno) or d
            raw_acct = (row.get('Account #') or '').strip()
            acct = hash_broker_account(raw_acct) if raw_acct else ''
            where = f"{name}:{lineno}"
            if is_fx:
                _fx_leg((acct, d, s), {"cur": cur, "net": net, "where": where})
                continue
            if is_lfj:
                ev = CL.flow(d, cur, net, settle=s, where=name, account=acct,
                             desc="LFJ stock-lending income")
            else:
                ev = CL.move(d, cur, net, settle=s, where=name, account=acct,
                             desc=action or atype)
            ev['line_where'] = where
            out.append(ev)
        for key, (slot, leg) in list(pending.items()):
            _lone(key, slot, leg)
        return [ev for ev in out if ev is not None]

    def _cash_amounts(self, r) -> Tuple[float, float]:
        """A noted cash row's Quantity and Net Amount, a blank 0. One that
        cannot be read is refused BY NAME: the parse counts a deposit,
        withdrawal, conversion or cash-only journal as a non-event without
        reading its Net Amount, so nothing else named it, and the row
        vanished from a ledger that then computed (2026-10 audit C01). The
        refusal is the file's `cash_problem`, never a refused parse."""
        row, lineno, name = r.cells, r.line, self._qt.shown
        try:
            qty = parse_strict_number(row.get('Quantity'), field='Quantity',
                                      where=f"{name} line {lineno}",
                                      allow_blank=True, blank=0.0)
            net = parse_strict_number(row.get('Net Amount'),
                                      field='Net Amount',
                                      where=f"{name} line {lineno}",
                                      allow_blank=True, blank=0.0)
        except (ValueError, BrokerageParseError) as e:
            msg = str(e)
            if not msg.startswith(name):
                msg = f"{name} line {lineno}: {msg}"
            raise BrokerageParseError(
                f"{msg} — a {r.action_raw or r.activity_type} cash row the "
                f"FX-on-cash ledger reads; fix the cell in the export") from None
        return qty, net

    def _cash_day(self, row: Dict[str, Any], col: str, lineno: int) -> Optional[str]:
        """A cash row's day (YYYY-MM-DD) from `col`: None for a blank or
        unreadable Settlement Date (the event settles on its trade day); a
        Transaction Date that is not one raises BrokerageParseError (the
        file's `cash_problem`: the parse never reads a conversion's or a
        deposit's dates)."""
        raw = (row.get(col) or '').strip()
        dt = self.parse_date(raw, *_DATE_FMTS) if raw else None
        if dt is None and col == 'Transaction Date':
            raise BrokerageParseError(f"{self._qt.shown} line {lineno}: {col} "
                                      f"{raw!r} is not a date")
        return dt.strftime('%Y-%m-%d') if dt else None

    def after_marks(self, transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The journal pairs costed from their book values: after the
        template's split-fill marks, as the one loop had it, so the marks
        see the legs as the loop made them and the ids stay as they were
        (TJ-2246)."""
        self._cost_journal_pairs(self._qt.journal_txs)
        return transactions

    def release(self) -> None:
        self._qt = None                 # the export's pairings and notes released

    def statement_accounts(self) -> set:
        """The `Account #` values of the last parsed export (audit
        A2-0008: Questrade exports name their account, so statements of
        two broker accounts are told apart like IB's)."""
        return set(getattr(self, '_qt_accounts', set()))

    @staticmethod
    def _cost_journal_pairs(legs: List[Dict[str, Any]]) -> None:
        """Carry the journaled units' cost on both BRW legs: the IN leg
        states the book value in its own currency ("BOOK VALUE: $2468.13
        CNV@ 1.3579"), the OUT leg is the same cost at the stated rate.
        Where the pair does not net (no JOURNAL rule, or a sheltered
        custody view that rewrites TRANSFER to BUYSELL at its net), the
        cost moves with the units instead of a $0-cost lot."""
        rates = {id(t): t.pop('_cnv', None) for t in legs}
        for i in legs:
            if i['quantity'] <= 0 or not i.get('book_value'):
                continue
            bv = float(i['book_value'])
            i['net_amount'] = i['gross_amount'] = bv
            i['price'] = M.units(bv / i['quantity'], 8)
            # The SAME security's other line only (re-audit A2-1061: two
            # journals on one date swapped their costs): SAMPLF.U.TO and
            # SAMPLF.TO share the root SAMPLF.
            # A paired currency journal's legs carry its id
            # (_plan_qt_journals).
            out = next((o for o in legs if o['quantity'] < 0
                        and o['date'] == i['date']
                        and (o.get('journal_pair') == i['journal_pair']
                             if i.get('journal_pair') else
                             _journal_root(o['symbol'])
                             == _journal_root(i['symbol']))
                        and abs(o['quantity'] + i['quantity']) < 1e-9
                        and not o.get('net_amount')), None)
            if out is None:
                continue
            rate = rates.get(id(i))
            o_bv = (M.units(bv * rate, 2)
                    if rate and out['currency'] != i['currency'] else bv)
            out['net_amount'] = out['gross_amount'] = o_bv
            out['price'] = M.units(o_bv / abs(out['quantity']), 8)

    def _check_trade_money(self, lineno, desc, qty, price, gross, comm,
                           diff, mult, fx_settled, *, cash=None,
                           rate=1.0) -> None:
        """Per-row money check that FAILS CLOSED: |Net Amount| must
        differ from |Gross Amount| by exactly |Commission| (`diff`), and
        |Gross| must be |qty| x Price x multiplier (100 per option). A
        header-only column swap or a mislabelled column used to book
        silently; a CAD-settled US trade (EXCHANGE RATE in the
        description) nets in CAD and is exempt from the first
        identity."""
        where = self._where(lineno)
        if abs(price) > 0:
            expected = abs(qty) * abs(price) * mult
            if not money_close(gross, expected, abs_tol=0.02, rel_tol=0.002):
                raise BrokerageParseError(
                    f"{where}: |Gross Amount| {M.shown(abs(gross), 2):,.2f} is not "
                    f"|Quantity| {abs(qty):g} x Price {price:g} x "
                    f"{mult:g} = {M.shown(expected, 2):,.2f} ({desc[:50]!r}) — a "
                    f"swapped or mislabelled column; refusing to book "
                    f"it.")
        if not fx_settled and abs(abs(diff) - abs(comm)) > 0.011:
            raise BrokerageParseError(
                f"{where}: Net Amount differs from Gross Amount "
                f"{M.shown(gross, 2):,.2f} by {M.shown(abs(diff), 2):,.2f}, not by the "
                f"Commission {M.shown(abs(comm), 2):,.2f} ({desc[:50]!r}) — a "
                f"swapped or mislabelled column; refusing to book it.")
        if fx_settled and cash is not None:
            # CAD-settled US trade (audit R1-73): the CAD cash (a buy's
            # cost, a sale's proceeds) must be the USD Gross at the
            # stated rate, give or take the Commission (Questrade's
            # column may carry it in either currency). The exemption
            # above let a wrong or column-swapped CAD Net book; so did
            # a Net of 0, booked at Gross x rate (TJ-4542).
            expected = abs(gross) * rate
            resid = abs(cash - expected)
            tol = 0.011 + 1e-5 * abs(cash)
            if not any(abs(resid - c) <= tol
                       for c in (abs(comm), abs(comm) * rate)):
                raise BrokerageParseError(
                    f"{where}: CAD Net Amount {M.shown(abs(cash), 2):,.2f} is not the USD "
                    f"Gross {M.shown(abs(gross), 2):,.2f} x EXCHANGE RATE {rate:g} = "
                    f"{M.shown(expected, 2):,.2f} give or take the Commission "
                    f"{M.shown(abs(comm), 2):,.2f} ({desc[:50]!r}) — a wrong or "
                    f"swapped column; refusing to book it. Correct the Net "
                    f"Amount and the rate from the trade confirmation.")

    def _parse_cash_in_lieu(self, row, currency, desc, lineno,
                            rev_originals, reversals):
        """CIL -> a same-day pair (the fraction acquired at $0, sold for
        the cash), both at the row's own clock (`_row_clock`): the sale is
        the generated leg and has no clock of its own, so it takes its
        parent's and is emitted right after it; rows at one moment keep
        the emitted order (CA-DATE-14 / US-DATE-13), so nothing of the
        day comes between the two. SIGNED (audit R1-66): Net Amount > 0
        is the cash in lieu; Net Amount < 0 is Questrade REVERSING an
        earlier CIL, which cancels that original (paired in
        _pair_reversals) — abs() booked it as a second sale, the cash
        counted twice as gain."""
        m = _CIL_RE.search(desc)
        frac = (desc_number(m.group(1), where=self._where(lineno),
                            field='CASH IN LIEU fraction') if m else 0.0)
        net = self._num(row, 'Net Amount', lineno)
        cash = abs(net)
        if frac <= 0 or cash <= 0:
            return Outcome.skip("CIL row with no fraction/cash")
        sym_raw, scur = self._resolve_symbol(row, currency, lineno)
        sym = self.apply_currency_suffix(sym_raw, scur)
        key = ('CIL', sym, M.units(frac, 6), M.units(cash, 2))
        if net < 0:
            reversals.append((key, lineno, desc))
            return Outcome.consumed()     # read into parser state
        date, time = _row_clock(self._date(row, 'Transaction Date', lineno))
        base = {'date': date, 'time': time, 'date_settle': date, 'symbol': sym,
                'currency': currency, 'commission': 0.0,
                'account': self.DEFAULT_ACCOUNT, 'description': desc}
        legs = [
            {**base, 'action': 'BUYSELL',
             'quantity': frac, 'price': 0.0, 'net_amount': 0.0,
             'gross_amount': 0.0},
            {**base, 'action': 'BUYSELL',
             'quantity': -frac, 'price': M.units(cash / frac, 8),
             'net_amount': cash, 'gross_amount': cash},
        ]
        rev_originals.setdefault(key, []).append(legs)
        self._qt.orig_line[id(legs)] = lineno
        return legs

    def _pair_reversals(self, transactions, rev_originals, reversals):
        """Each CIL/REI/stock-dividend reversal row cancels one original
        with the same symbol, quantity and amount: both drop out. The
        pairing is planned across ALL of the account's exports
        (_plan_qt_reversals): an original in last year's export is
        cancelled there, and every overlapping copy of it drops out. A
        reversal whose original is in no export of the account is
        REFUSED — booking it would either duplicate the event (the old
        abs()) or invent one."""
        key_f = self._qt.key
        paired = self._ctx.rev_paired.get(key_f, set())
        dropped = self._ctx.rev_drop.get(key_f, set())
        for key, lineno, desc in reversals:
            if lineno not in paired:
                kind, sym, qty, amt = key
                raise BrokerageParseError(
                    f"{self._where(lineno)}: a {kind} reversal "
                    f"({sym} {qty:g}, {M.shown(amt, 2):,.2f}; {desc[:50]!r}) whose "
                    f"original {kind} row (on or before it) is in no "
                    f"export of this account — refusing to book it as a "
                    f"second event. Add the export that holds the "
                    f"original, or book the correction in a .tt file.")
        drop = set()
        for groups in rev_originals.values():
            for grp in groups:
                if self._qt.orig_line.get(id(grp)) in dropped:
                    drop.update(id(leg) for leg in grp)
        if drop:
            transactions[:] = [t for t in transactions
                               if id(t) not in drop]

    def _parse_fee(self, row, currency, desc, lineno):
        """FCH / 'Fees and rebates' row → FEE. Questrade signs Net
        Amount as cash (a charge NEGATIVE, a rebate POSITIVE); the repo
        FEE convention is positive = charged, so the sign flips. Bound
        to the ticker the description names ("# SHARES TKR") so the
        fee report can group it; CASH when it names none. None on a
        zero amount (nothing to book)."""
        net = self._num(row, 'Net Amount', lineno)
        if abs(net) < 1e-9:
            return None
        dt = self._date(row, 'Transaction Date', lineno)
        date = dt.strftime('%Y-%m-%d')
        time = dt.strftime('%H:%M:%S')
        m = _FEE_SHARES_TICKER_RE.search(desc or '')
        # Upper case, as every other row's symbol this parser reads (a fee
        # row's alone kept the cell's: backlog TJ-4601, TJ-4605).
        sym_raw = (m.group(1) if m
                   else (row.get('Symbol') or '').strip().lstrip('.')).upper()
        symbol = self.apply_currency_suffix(sym_raw, currency) if sym_raw else 'CASH'
        return {
            'action': 'FEE',
            'date': date, 'time': time, 'date_settle': date,
            'symbol': symbol, 'quantity': 0.0, 'currency': currency,
            'net_amount': -net, 'type': 'fee',
            'account': self.DEFAULT_ACCOUNT,
            'description': desc,
        }

    def _parse_reinvestment(self, row, currency, desc, lineno,
                            rev_originals, reversals):
        """REI (DRIP) -> a BUYSELL buy. SIGNED (audit R1-66): shares in
        (Quantity > 0) paid for (Net Amount < 0) is the purchase; the
        negated row (Quantity < 0, Net > 0) is Questrade REVERSING it,
        which cancels the original (paired in _pair_reversals) — abs()
        booked it as a second buy, phantom shares in the pool."""
        qty_s = self._num(row, 'Quantity', lineno)
        net_s = self._num(row, 'Net Amount', lineno)
        qty, net = abs(qty_s), abs(net_s)
        if qty <= 1e-12 and net < 0.005:
            return Outcome.nonevent("REI row with no shares and no cash")
        if qty <= 1e-12 or net < 0.005:
            # Units with no cash (an in-kind reinvestment: is there a
            # separate cash DIV row? the export does not say) or cash
            # with no units. Skipping it lost the units and left a
            # phantom short at the next sale, under a note that said the
            # row had no shares (audit S063-06). RBC refuses the same.
            raise BrokerageParseError(
                f"{self._where(lineno)}: REI (reinvestment) row with "
                f"Quantity {qty_s:g} and Net Amount {M.shown(net_s, 2):,.2f} "
                f"({desc[:50]!r}) — a reinvestment buys units for cash; "
                f"with {'no cash' if qty > 1e-12 else 'no units'} the "
                f"parser cannot tell the purchase (and any income) apart. "
                f"Book it in a .tt file (the units at the REINV@ price) "
                f"and delete the row.")
        if (qty_s > 0) == (net_s > 0):
            raise BrokerageParseError(
                f"{self._where(lineno)}: REI row with Quantity {qty_s:g} "
                f"and Net Amount {M.shown(net_s, 2):,.2f} of the same sign "
                f"({desc[:50]!r}) — a reinvestment buys shares for cash "
                f"(or a reversal returns both); refusing to guess.")
        sym_raw, scur = self._resolve_symbol(row, currency, lineno,
                                             rei=True)
        key = ('REI', self.apply_currency_suffix(sym_raw, scur),
               M.units(qty, 6), M.units(net, 2))
        if qty_s < 0:
            reversals.append((key, lineno, desc))
            return Outcome.consumed()     # read into parser state
        date, time = _row_clock(self._date(row, 'Transaction Date', lineno))
        sdt = self._date(row, 'Settlement Date', lineno, required=False)
        date_settle = sdt.strftime('%Y-%m-%d') if sdt else date
        self.check_settle_order(date, date_settle, where=self._where(lineno),
                                what=repr(desc[:50]))
        m = REINV_PRICE_RE.search(desc)
        # 'REINV@C$1,234.56' is 1234.56, not 1 (audit S062-20); a  # pii-ok
        # decimal comma falls back to the cash / units.
        price = desc_number(m.group(1), strict=False) if m else None
        mc = REINV_CUR_RE.search(desc)
        price_cur = (REINV_CUR.get(mc.group(1).upper(), '?') if mc
                     else currency)
        bad = reinvest_identity_error(qty, net, price, price_cur, currency)
        if bad:
            raise BrokerageParseError(
                f"{self._where(lineno)}: REI row: {bad} ({desc[:50]!r}) "
                f"— a wrong or shifted column; refusing to book it as the "
                f"units' cost (re-audit A2-0268).")
        # A REINV@C$ price on a USD row (U$ on a CAD row) is another
        # currency's number: booked at the cash per unit instead.
        price = reinvest_row_price(qty, net, price, price_cur, currency)
        if not price:
            price = M.units(net / qty, 8)
        tx = {
            'action': 'BUYSELL',
            'date': date, 'time': time, 'date_settle': date_settle,
            'symbol': key[1],
            'quantity': qty, 'currency': currency,
            'price': price, 'net_amount': net, 'gross_amount': net,
            'commission': 0.0,
            'account': self.DEFAULT_ACCOUNT,
            'description': desc,
        }
        if scur and scur != currency:
            # The listing's currency is not the cash's (a TSX listing's
            # DRIP paid on the USD side, _rei_listing): the native books
            # restate the row in the listing's currency.
            tx['listing_currency'] = scur
        _grp = [tx]
        rev_originals.setdefault(key, []).append(_grp)
        self._qt.orig_line[id(_grp)] = lineno
        return tx

    @staticmethod
    def _is_stock_split(action_raw, activity_type, desc):
        """A forward stock split distribution. Questrade books it as a DIS /
        'Dividends' row with 'STK SPLIT'/'STOCK SPLIT' in the description. The
        marker is specific enough not to match a normal trade whose name
        mentions a due-bill split."""
        du = (desc or '').upper()
        return ('STK SPLIT' in du or 'STOCK SPLIT' in du) and (
            action_raw == 'DIS' or activity_type == 'Dividends')

    @staticmethod
    def _is_cash_only(row) -> bool:
        """Cash and no shares: a dividend, whatever its description
        says ('... POST STOCK SPLIT' dropped the income into the split
        branch — audit S063-07). Unparseable cells: not cash-only (the
        branch that reads them names the row)."""
        try:
            qty = parse_strict_number(row.get('Quantity'), allow_blank=True,
                                      blank=0.0)
            net = parse_strict_number(row.get('Net Amount'),
                                      allow_blank=True, blank=0.0)
        except BrokerageParseError:
            return False
        return abs(qty) < 1e-9 and abs(net) >= 0.005

    def _parse_split(self, row, currency, desc, lineno):
        """Emit a SPLIT scaling the existing pool by (held + received)/held —
        a non-taxable share-count change, not a dividend. Questrade reports the
        NEW shares in Quantity, the held count as 'ON <held> SHS', and a temp
        internal Symbol (e.g. X000001); the real ticker is resolved from a
        trade of the same security, so the SPLIT lands on the pool the user
        actually holds."""
        received = self._num(row, 'Quantity', lineno)
        m = _SPLIT_ON_SHS_RE.search(desc)
        # A decimal comma ('ON 1,5 SHS') is refused, never read as 15
        # (audit S062-13): the ratio rescales the whole pool.
        held = (desc_number(m.group(1), where=self._where(lineno),
                            field="split base 'ON N SHS'") if m else 0.0)
        if not m or received == 0 or held <= 0:
            emit_line(f"warning: Questrade stock-split row not understood "
                  f"(need 'ON N SHS' and a nonzero quantity), skipping: "
                  f"{desc!r}")
            return None
        symbol, scur = self._resolve_symbol(row, currency, lineno)
        raw_sym = (row.get('Symbol') or '').strip().lstrip('.').upper()
        if not symbol and not raw_sym:
            emit_line(f"warning: Questrade stock split: couldn't resolve a traded "
                  f"ticker for {symbol!r} ({desc!r}); the SPLIT may not apply "
                  f"to the right pool — add a ticker.map rule if needed.")
        symbol = self.apply_currency_suffix(symbol, scur)

        dt = self._date(row, 'Transaction Date', lineno)
        # On a SHORT position the split DEBITS shares (Quantity < 0 for
        # a forward split): (held + received)/held gave 0.95 for a
        # 21-for-20 and 0 for a 2-for-1 (audit S063-08). The account's
        # own trades say which side the pool is on; a long (or unknown)
        # pool keeps the signed rule (a negative quantity is then a
        # reverse split).
        # The broker account's own position (backlog TJ-0672), with
        # earlier splits replayed and overlapping copies counted once
        # (_plan_qt_positions).
        pos = self._ctx.position_on(
            symbol, dt.strftime("%Y-%m-%d"), before=True,
            account=(row.get('Account #') or '').strip()) \
            if self._ctx else 0.0
        if pos < -1e-9:
            ratio = (held - received) / held
        else:
            ratio = (held + received) / held
        if ratio <= 0:
            emit_line(f"warning: Questrade stock-split row gives a ratio of "
                  f"{ratio:g} for {symbol} ({desc!r}) — not booked.")
            return None
        return {
            'action': 'SPLIT',
            'date': dt.strftime("%Y-%m-%d"),
            'time': dt.strftime("%H:%M:%S"),
            'date_settle': dt.strftime("%Y-%m-%d"),
            'symbol': symbol,
            'symbol_new': '',
            'quantity': ratio,
            'currency': currency,
            'price': 0.0,
            'net_amount': 0.0,
            'account': self.DEFAULT_ACCOUNT,
            'description': desc,
        }

    def _parse_dividend(self, row, currency, desc, lineno):
        """Questrade DIV row: cash dividend on a security. Net Amount holds
        the dividend value (currency-of-the-row); Gross is typically 0.

        Symbol resolution (see _resolve_symbol): an internal code
        ('S098765', a transferred-in security) or a dotted dividend code
        ('.SAMPLP' — the issuer's dividend on a position held as SAMPLQ on the
        NYSE, paid in USD; the row's own currency would yield SAMPLP.US,
        wrong on both axes) is rebound to the TRADED symbol+currency of
        the same security, which the user's ticker.map then folds into
        the canonical identity. A real ticker is kept. The currency
        field on the transaction stays the dividend's own currency so
        the user knows how it was paid.
        """
        # Sign-preserving (schema convention): the Dividends activity
        # type also carries NON-RES TAX WITHHELD debits, reversals, and
        # ROC reversals as NEGATIVE Net Amounts. abs() booked all of
        # those as positive income (and a ROC reversal as a second ACB
        # reduction); keeping the sign lets downstream summing net them
        # out and honors tx_roc_adjust's signed-amount contract.
        net = self._num(row, 'Net Amount', lineno)
        if abs(net) < 1e-9:
            return None  # informational row with no cash flow
        dt = self._date(row, 'Transaction Date', lineno)
        date = dt.strftime("%Y-%m-%d")
        time = dt.strftime("%H:%M:%S")
        symbol_raw, suffix_currency = self._resolve_symbol(row, currency,
                                                           lineno)
        symbol = self.apply_currency_suffix(symbol_raw, suffix_currency) if symbol_raw else ''
        # Surface the share count and implied per-share rate so the
        # dividend record reconciles against the T5 / per-share rate
        # check. Questrade encodes the count as "ON <N> SHS" in the
        # description (matches the Open Text 150 SHS / Freehold 3000
        # SHS / etc. real samples). Falls back to qty=price=0 when the
        # pattern doesn't match — same as today's behavior.
        if is_roc_description(desc):
            # Reuses the symbol resolution above (internal-code and
            # cross-listing repair) — ROC reduces the SAME pool the
            # dividends belong to.
            return self.tx_roc_adjust(symbol=symbol, currency=currency,
                                      date=date, desc=desc, amount=net)
        qty, price = _parse_div_qty_rate(desc, net)
        # A substitute payment ("SUBST PAY ... IN LIEU OF DIVIDEND") is
        # its own income kind, paid by a Canadian dealer (re-audit
        # A2-0098; characterize/income_dating applies CA-INC-03 / US-INC-01).
        pil = bool(PIL_DESC_RE.search(desc))
        tx = {
            'action': 'DIVIDEND_IN_LIEU' if pil else 'DIVIDEND',
            'date': date,
            'time': time,
            'date_settle': date,
            'symbol': symbol,
            'quantity': qty,
            'currency': currency,
            'price': price,
            'net_amount': net,
            'gross_amount': net,
            'type': 'dividend_in_lieu' if pil else 'dividend',
            'description': desc,
            'account': self.DEFAULT_ACCOUNT,
        }
        if pil:
            tx['dealer_country'] = 'CA'
        # "DIST ON ... REC mm/dd/yy": the record date and the
        # distribution label, as neutral facts (characterize/income_dating).
        tx.update(income_facts_from_description(desc))
        return tx

    def _parse_transfer(self, row, currency, lineno):
        """Questrade TF6 row: asset transfer in or out. Quantity is signed
        (+ for transfer-in, − for transfer-out).

        Two fields need recovering from elsewhere:
          - Symbol. The TF6 Symbol column is often an internal Questrade
            code (e.g. X000002); it is looked up from a trade row of the
            same security (see _resolve_symbol). Without this the
            transferred position lands under e.g. X000002.US instead of
            SAMPLA.US. A real ticker on the row is kept.
          - Cost basis. The TF6 Net Amount column is 0; Questrade puts
            the transferred-in book value (ACB) in the description as
            "TRANSFER BOOK VALUE <amount>". It's parsed from there; a
            transfer-in without it is reported (taxable accounts).

        taxjson-brokerage's --transfers flag controls whether these
        reach the gains engine."""
        qty_signed = self._num(row, 'Quantity', lineno)
        if abs(qty_signed) < 1e-9:
            return None  # cash-only journal, not a security transfer
        desc = row.get('Description') or ''
        dt = self._date(row, 'Transaction Date', lineno)
        date = dt.strftime("%Y-%m-%d")
        time = dt.strftime("%H:%M:%S")

        # A transferred OPTION (audit S062-14): the contract identity and
        # the x100 multiplier come from the description, exactly as on
        # the Buy/Sell/EXP path — the Symbol column (QZSL16Oct26C62.50)
        # put the transfer and the later close on two symbols.
        opt = self.parse_option_from_description(desc)
        if opt:
            symbol = self.apply_currency_suffix(
                self.format_occ_symbol(opt['right'], opt['base'],
                                       opt['expiry'], opt['strike'],
                                       where=self._where(lineno)),
                currency)
        else:
            # An internal code no trade resolves is warned about (and
            # kept) by _resolve_symbol.
            symbol_raw, suffix_currency = self._resolve_symbol(
                row, currency, lineno)
            symbol_raw = symbol_raw.replace(' ', '.')
            symbol = (self.apply_currency_suffix(symbol_raw, suffix_currency)
                      if symbol_raw else '')

        # Cost basis: prefer the "BOOK VALUE" note in the description;
        # fall back to the Net Amount column when it isn't present.
        net = abs(self._num(row, 'Net Amount', lineno))
        m = _BOOK_VALUE_RE.search(desc)
        if m:
            net = parse_strict_number(m.group(1), field='TRANSFER BOOK VALUE',
                                      where=self._where(lineno))
        mult = float(self.OPTION_MULTIPLIER) if opt else 1.0
        price = (M.units(net / (abs(qty_signed) * mult), 8)
                 if abs(qty_signed) > 1e-9 else 0.0)
        tx = {
            'action': 'TRANSFER',
            'date': date,
            'time': time,
            'date_settle': date,
            'symbol': symbol,
            'quantity': qty_signed,
            'currency': currency,
            'price': price,
            'net_amount': net,
            'gross_amount': net,
            'account': self.DEFAULT_ACCOUNT,
            # Carried like every other row so a description-keyed
            # --security-overrides rule reaches transfers too (audit
            # R1-69: the override moved the trades and dividends of a
            # mislabelled listing but not its transfer-in).
            'description': desc,
            # Popped by parse_file (the no-book-value report).
            '_book_value': bool(m) or net > 0,
        }
        if m:
            # The broker's STATED book value: evidence the run may book
            # as the incoming shares' cost (lib/transfer_in) — the Net
            # Amount column never is.
            tx['book_value'] = net
        if opt:
            tx['multiplier'] = mult
            tx['contract_size_basis'] = 'assumed'
        return tx
