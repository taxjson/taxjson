import csv
import io
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from taxjson.lib.core import STOCK_DIVIDEND
from taxjson.lib.brokerages.base import (BaseBrokerage, BrokerageParseError,
                                         ticker_map_joins,
                                         ticker_map_renames,
                                         combined_accounts_note,
                                         combined_accounts_refusal,
                                         _parse_div_qty_rate,
                                         DESC_NUMBER_RE,
                                         canonical_ca_listing,
                                         desc_number,
                                         income_facts_from_description,
                                         is_roc_description,
                                         parse_strict_number,
                                         shown_name)


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
_QT_REGISTERED_RE = re.compile(
    r'\b(RRSP|RRIF|TFSA|LIRA|LIF|LRSP|RLSP|RESP|FHSA|RDSP|SRRSP|PRPP)\b',
    re.IGNORECASE)

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
_REINV_PRICE_RE = re.compile(r'REINV@(?:[A-Z]{1,3}\$)?\s*' + DESC_NUMBER_RE,
                             re.I)
_STK_DIV_RE = re.compile(r'\bSTK\.?\s+DIV\b|\bSTOCK\s+DIVIDEND\b',
                         re.IGNORECASE)

# Questrade emits internal codes like "S098765" or "A12345" for some
# dividend rows (typically post-transfer-in, before the security is
# linked to its real ticker). Pattern: one letter followed by digits.
_INTERNAL_CODE_RE = re.compile(r'^[A-Z]\d+$')

# Patterns to strip when building a "description key" for matching
# dividend rows to the underlying trade rows. Mirrors qt_dividends.pl.
_DESC_NOISE_RES = [
    # Event suffixes on quantity-bearing rows (audit S063-03): a stock
    # split / stock dividend ("... STK SPLIT ON 10 SHS", "... STK DIV ON
    # 200 SHS" -- the ' DIV ON' rule below left a stray 'STK'), a DRIP
    # ("... REINV@C$1.23456") and a cash-in-lieu ("... CASH IN LIEU OF
    # .50000"). Stripped FIRST, so the key equals the trade row's.
    re.compile(r'\s+(?:STK\.?|STOCK)\s+(?:SPLIT|DIV(?:IDEND)?)\b.*$',
               re.IGNORECASE),
    re.compile(r'\s+REINV\s*@.*$', re.IGNORECASE),
    re.compile(r'\s+CASH\s+IN\s+LIEU\b.*$', re.IGNORECASE),
    # Dividend-specific suffixes.
    re.compile(r'\s+CASH DIV ON.*$', re.IGNORECASE),
    re.compile(r'\s+RTS DIST ON.*$', re.IGNORECASE),
    re.compile(r'\s+DIST ON.*$', re.IGNORECASE),
    re.compile(r'\s+DIV ON.*$', re.IGNORECASE),
    re.compile(r'\s+DIVIDEND ON.*$', re.IGNORECASE),
    re.compile(r'\s+NON-RES.*TAX.*ON.*$', re.IGNORECASE),
    re.compile(r'\s+TAX WITHHELD ON.*$', re.IGNORECASE),
    re.compile(r'\s+RETURN OF CAPITAL ON.*$', re.IGNORECASE),
    re.compile(r'\s+SPINOFF ON.*$', re.IGNORECASE),
    # Trade-specific suffixes.
    re.compile(r'\s+WE ACTED AS AGENT.*$', re.IGNORECASE),
    re.compile(r'\s+AVG PRICE.*$', re.IGNORECASE),
    re.compile(r'\s+REC\s+\d{2}/\d{2}/\d{2}.*$', re.IGNORECASE),
    # Transfer-specific suffixes. A transfer-in row may also name the
    # delivering dealer after the security ("<SECURITY> <DEALER> 41.75
    # TRANSFER BOOK VALUE ..."): no list of dealer names is kept — such a
    # key is matched to the security's by word prefix
    # (QtAccountContext.key_candidates).
    re.compile(r'\s+TFER\s+(?:FROM|TO).*$', re.IGNORECASE),
    # "... TRANSFER FROM/TO/IN ...", "... TRANSFER BOOK VALUE ..." or a
    # final "TRANSFER" ("<SECURITY> <DEALER> 135.79 TRANSFER"); a name
    # with TRANSFER inside it ("ZZ TRANSFER LP") is left alone.
    re.compile(r'\s+TRANSFER(?:\s+(?:FROM|TO|IN|BOOK\s+VALUE)\b.*)?$',
               re.IGNORECASE),
    re.compile(r'\s+BOOK\s+VALUE.*$', re.IGNORECASE),
    # Cosmetic suffixes seen on various row types.
    re.compile(r'\s+COMMON STOCK.*$', re.IGNORECASE),
]
# A class designation: "CLASS B SUB VTG" and "CL B" are one key, "CL B",
# whatever the letter (only CLASS A used to be stripped, so a class-A
# key lost its letter and a class-B key kept the whole wording).
_CLASS_RE = re.compile(r'\s+CLASS\s+([A-Z])\b.*$', re.IGNORECASE)
_SPINOFF_PARENT_RE = re.compile(
    r'SPINOFF ON .* FROM SEC# \S+ (.*?)\s+REC', re.IGNORECASE
)
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


def _get_desc_key(desc: str) -> str:
    """Build a normalized key for matching dividend descriptions to trade
    descriptions. Strips dividend/trade-specific suffixes and uppercases."""
    if not desc:
        return ''
    m = _SPINOFF_PARENT_RE.search(desc)
    if m:
        desc = m.group(1)
    for pat in _DESC_NOISE_RES:
        desc = pat.sub('', desc)
    desc = _CLASS_RE.sub(lambda m: f" CL {m.group(1).upper()}", desc)
    return re.sub(r'\s+', ' ', desc).strip().upper()


# Questrade writes 'Buy'/'Sell' and upper-case codes (DIV, TF6, DIS, ...).
# An export in another case ('BUY', 'buy') was skipped as an unknown
# action and the sale opened a phantom short (audit R1-71).
_QT_ACTION_CANON = {'BUY': 'Buy', 'SELL': 'Sell'}


def _canon_action(raw: Optional[str]) -> str:
    a = (raw or '').strip()
    return _QT_ACTION_CANON.get(a.upper(), a.upper())


def _read_qt_rows(path: Path, warn: bool = False) -> List[tuple]:
    """(line number, row) for every data row of a Questrade export. The
    header must carry every column in _QT_COLUMNS."""
    path = Path(path)
    raw = path.read_bytes()
    text = (raw.decode('utf-16') if raw[:2] in (b'\xff\xfe', b'\xfe\xff')
            else raw.decode('utf-8-sig'))
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
                print(f"warning: ATTENTION: {shown_name(path)} line {lineno}: the "
                      f"row has no {', '.join(short)} cell(s) (fewer cells "
                      f"than the header) — booked from its Action code; "
                      f"re-export the file if this is not a hand-made "
                      f"fixture.", file=sys.stderr)
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
    return rows


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
            print(m, file=sys.stderr)

    def position_on(self, listing: str, date: str, *,
                    before: bool = False) -> float:
        return sum(q for d, q in self.timelines.get(listing, ())
                   if (d < date if before else d <= date))


def build_qt_account_context(paths, *, helper=None) -> QtAccountContext:
    helper = helper or QuestradeBrokerage()
    files = list(dict.fromkeys(str(Path(p).resolve()) for p in paths))
    ctx = QtAccountContext(files=files, desc_to_ticker={}, sym_curs={},
                           timelines={})
    by_name: Dict[tuple, set] = {}
    where: Dict[str, str] = {}
    trade_keys: set = set()
    for k in files:
        for lineno, row in _read_qt_rows(Path(k)):
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
            ctx.timelines.setdefault(listing, []).append(
                (dt.strftime('%Y-%m-%d'), q))
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
    for tl in ctx.timelines.values():
        tl.sort(key=lambda e: (e[0], -e[1]))
    _detect_qt_ticker_changes(ctx, by_name, where)
    _plan_qt_reversals(ctx, helper)
    return ctx


def _qt_reversal_kind(row, helper) -> Optional[Tuple[tuple, bool, str]]:
    """(key, is_reversal, date) of a stock-dividend / cash-in-lieu / DRIP
    row the parser pairs with its reversal (the same classification as
    parse_file), else None. Unparseable cells -> None (parse_file names
    the row)."""
    action = _canon_action(row.get('Action'))
    act = (row.get('Activity Type') or '').strip()
    desc = row.get('Description') or ''
    sym = (row.get('Symbol') or '').strip().lstrip('.').upper()
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
    if action == 'DIS' and _STK_DIV_RE.search(desc) and abs(qty) > 1e-9:
        return ('STK DIV', sym, round(abs(qty), 6), 0.0), qty < 0, date
    if action == 'CIL' and _CIL_RE.search(desc):
        try:
            frac = desc_number(_CIL_RE.search(desc).group(1))
        except BrokerageParseError:
            return None                 # parse_file refuses the row
        if frac <= 0 or abs(net) <= 0:
            return None
        return ('CIL', sym, round(frac, 6), round(abs(net), 2)), net < 0, date
    if action == 'REI' or act == 'Dividend reinvestment':
        if abs(qty) <= 1e-12 or abs(net) < 0.005 or (qty > 0) == (net > 0):
            return None
        return (('REI', sym, round(abs(qty), 6), round(abs(net), 2)),
                qty < 0, date)
    return None


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
    of both rows drops out."""
    # content key -> per file: [line numbers]
    copies: Dict[tuple, Dict[str, List[int]]] = {}
    meta: Dict[tuple, Tuple[tuple, bool, str]] = {}
    for k in ctx.files:
        for lineno, row in _read_qt_rows(Path(k)):
            kind = _qt_reversal_kind(row, helper)
            if kind is None:
                continue
            ck = (kind[2], _canon_action(row.get('Action')),
                  (row.get('Symbol') or '').strip().upper(),
                  ' '.join((row.get('Description') or '').split()).upper(),
                  (row.get('Quantity') or '').strip(),
                  (row.get('Net Amount') or '').strip(),
                  (row.get('Account #') or '').strip())
            copies.setdefault(ck, {}).setdefault(k, []).append(lineno)
            meta[ck] = kind
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
                if a == b or not ta or not tb or ta[-1][0] > tb[0][0]:
                    continue
                open_a = sum(q for _d, q in ta)
                run = low = 0.0
                when = ''
                for d, q in tb:
                    run += q
                    if run < low - 1e-9:
                        low, when = run, d
                first_b = next((q for _d, q in tb if abs(q) > 1e-9), 0.0)
                if (open_a <= 1e-9 or low >= -1e-9
                        or -low > open_a + 1e-6):
                    continue
                if ticker_map_joins(a, b):
                    continue    # ticker.map already pools them (A2-1056)
                how = (f"first appears on {tb[0][0]} with a SALE of "
                       f"{-first_b:g}" if first_b < 0 else
                       f"first appears on {tb[0][0]} and goes {-low:g} "
                       f"short on {when}")
                ctx.messages.append(
                    f"warning: ATTENTION: {where.get(b, '?')}: Questrade "
                    f"symbol {a} looks renamed to {b} — if they are one "
                    f"security add to ticker.map:  GLOBAL {a} {b}  — {a} "
                    f"stops on {ta[-1][0]} with {open_a:g} share(s) still "
                    f"open, and {b} (same Description {key!r}) {how}. "
                    f"That looks like a ticker change booked without a "
                    f"corporate-action row: as exported it is a stranded "
                    f"long {a} and a short {b} (the sale's gain drops "
                    f"out).")


class QuestradeBrokerage(BaseBrokerage):
    DEFAULT_ACCOUNT = "Questrade"

    # Whether the account is TAXABLE: True / False when the caller knows
    # (`taxjson-brokerage --account-type`, which `taxjson run` passes),
    # None = infer from the export's Account Type column. Only gates
    # warnings that matter in a taxable account (a dividend booked net
    # of non-resident withholding, a transfer-in with no book value).
    account_taxable: Optional[bool] = None

    # ------------------------------------------------------------ helpers

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
        from taxjson.lib.brokerages.rbc_direct import (
            strict_option_from_description)
        return strict_option_from_description(self, desc)

    @classmethod
    def prepare_files(cls, paths) -> QtAccountContext:
        """Read ALL of one account's Questrade exports once and build the
        identity maps every per-file parse shares (description ->
        traded symbol, a symbol's listing currency, position timelines).
        Account-level warnings print here, once."""
        ctx = build_qt_account_context(list(paths), helper=cls())
        ctx.emit()
        return ctx

    # Set by taxjson-brokerage (see `prepare_files`); None = this file alone.
    account_context: Optional[QtAccountContext] = None

    def _unbooked(self, lineno: int, msg: str) -> None:
        """A real row the parser could not book: `taxjson run` echoes
        the UNBOOKED prefix to the console and `run --strict` refuses
        to publish (the old skip note reached only the .sum banner)."""
        print(f"warning: UNBOOKED: {self._where(lineno)}: {msg}",
              file=sys.stderr)

    def _in_real_order(self, path: Path, rows) -> List[tuple]:
        """Rows of one day tie (Questrade stamps 00:00:00) and keep the
        order they are emitted in (CA-DATE-14 / US-DATE-13): a
        newest-first export is read bottom-up so that order is the real
        one. Decided per header-delimited SEGMENT (re-audit A2-0100): two
        newest-first exports concatenated (the header repeated, which
        the parse accepts) went both ways over the whole file and were
        read top-down — a same-day sale replayed after its rebuy."""
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
            dates = [self.parse_date(
                (r.get('Transaction Date') or '')[:10], '%Y-%m-%d')
                for _, r in body]
            ds = [d for d in dates if d]
            if self.newest_first(dates):
                body = body[::-1]
            elif not all(a <= b for a, b in zip(ds, ds[1:])):
                mixed = True
            out.extend(head + body)
        if mixed:
            print(f"note: {shown_name(path)}: rows are not in date order, so "
                  f"same-day rows keep the file's order (intra-day order "
                  f"unknown) — check a same-day sale and rebuy.",
                  file=sys.stderr)
        return out

    def _check_account_mix(self, path: Path, rows) -> None:
        """One export holding rows of SEVERAL Questrade accounts (re-audit
        A2-0025): every row is booked to the one taxjson account the file
        sits in. A registered plan's rows (Account Type TFSA/RRSP/...)
        in a TAXABLE account — or a taxable account's rows in a
        registered one — put the wrong trades in the books: refused.
        Any other mix (two taxable accounts) is booked as one and said
        on the console."""
        accts: Dict[str, set] = {}
        for _ln, r in rows:
            a = (r.get('Account #') or '').strip()
            if a:
                accts.setdefault(a, set()).add(
                    (r.get('Account Type') or '').strip())
        if len(accts) < 2:
            return

        _no = {a: i for i, a in enumerate(sorted(accts), 1)}

        def _mask(a: str) -> str:
            return f"#{_no[a]} {a[:2]}***"
        desc = ', '.join(f"{_mask(a)} ({'/'.join(sorted(t - {''})) or '?'})"
                         for a, t in sorted(accts.items()))
        taxable = self.account_taxable       # None: the caller did not say
        wrong = []
        for a, types in sorted(accts.items()):
            reg = any(_QT_REGISTERED_RE.search(t) for t in types)
            tax = any(re.search(r'margin|cash', t, re.I)
                      and not _QT_REGISTERED_RE.search(t) for t in types)
            if (taxable is True and reg) or (taxable is False and tax):
                wrong.append(_mask(a))
        if not wrong and self.combined_broker_accounts:
            # combined_broker_accounts = true (owner decision): a NOTE —
            # on a sheltered label only when every account is the SAME
            # registered plan (Questrade's Account Type names it).
            masked = [_mask(a) for a in sorted(accts)]
            if taxable is False:
                plans = {m.group(1).upper()
                         for types in accts.values() for t in types
                         for m in [_QT_REGISTERED_RE.search(t)] if m}
                untyped = any(not any(_QT_REGISTERED_RE.search(t)
                                      for t in types)
                              for types in accts.values())
                if len(plans) != 1 or untyped:
                    raise combined_accounts_refusal(
                        shown_name(path), 'Questrade', masked,
                        f"their Account Types differ ({desc})"
                        if len(plans) > 1 else
                        f"the Account Type does not name one plan for "
                        f"every account ({desc})")
            print(combined_accounts_note(shown_name(path), 'Questrade',
                                         masked), file=sys.stderr)
            return
        if wrong:
            raise BrokerageParseError(
                f"{shown_name(path)}: the export holds rows of {len(accts)} "
                f"Questrade accounts ({desc}); {', '.join(wrong)} "
                f"{'is a registered plan' if taxable else 'is a taxable account'}"
                f" but the file sits in a "
                f"{'taxable' if taxable else 'registered'} taxjson account, "
                f"so its trades would be booked there — refusing. Export "
                f"each Questrade account separately into its own "
                f"inputs/<account>/ folder.")
        print(f"warning: ATTENTION: {shown_name(path)}: the export holds rows of "
              f"{len(accts)} Questrade accounts ({desc}) — every row is "
              f"booked to ONE account. That is right only when they are one "
              f"tax entity (two taxable accounts of yours); export a "
              f"registered plan (TFSA/RRSP) separately.", file=sys.stderr)

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

    def _resolve_symbol(self, row: Dict[str, Any], currency: str,
                        lineno: Optional[int] = None):
        """(symbol, suffix currency) for a non-trade row. Questrade
        writes some rows under an internal code (S098765, a TF6's
        R123456) or a dotted dividend code (.SAMPLP for an issuer held as
        SAMPLQ on the NYSE); ONLY those are rebound to the traded symbol
        of the same security (matched on the description, in ANY of
        the account's exports). A real ticker is kept: first-match-wins
        used to rebind, e.g., an FTN.PR dividend onto FTN.PRA because
        both share a description. A description that maps to several
        symbols is never guessed, and an internal code nothing resolves
        is said out loud (a ticker.map GLOBAL line fixes it)."""
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
            own = self.apply_currency_suffix(sym, cur)
            others = sorted({self.apply_currency_suffix(s, c)
                             for s, c in cands} - {own})
            if all(ticker_map_joins(own, o) for o in others):
                others = []     # ticker.map already folds them (A2-1056)
            if others:
                key = (own, tuple(others))
                if key not in self._ambiguous_warned:
                    self._ambiguous_warned.add(key)
                    print(f"warning: {self._qt_name}: {sym!r} is booked "
                          f"under its own symbol, but the same security "
                          f"({(row.get('Description') or '')[:50]!r}) "
                          f"trades as {', '.join(others)} — if they are "
                          f"one security and ticker.map does not already "
                          f"fold them, add a ticker.map rule.",
                          file=sys.stderr)
            return sym, cur
        if len(cands) == 1:
            return next(iter(cands))
        if len(cands) > 1:
            key = (sym, tuple(sorted(cands)))
            if ticker_map_renames(self.apply_currency_suffix(sym, currency)):
                self._ambiguous_warned.add(key)  # mapped (A2-1056)
            if key not in self._ambiguous_warned:
                self._ambiguous_warned.add(key)
                print(f"warning: {self._qt_name}: {sym or '(blank)'!r} "
                      f"({(row.get('Description') or '')[:60]!r}) matches "
                      f"several traded symbols "
                      f"({', '.join(s for s, _ in sorted(cands))}) — not "
                      f"rebound; map it with a ticker.map rule (moot if "
                      f"ticker.map already maps it).",
                      file=sys.stderr)
        elif (_INTERNAL_CODE_RE.match(sym) and sym not in self._code_warned
              and not ticker_map_renames(
                  self.apply_currency_suffix(sym, currency))):
            # A row booked under Questrade's internal code that no trade
            # or transfer in any export of the account resolves: the
            # position fragments (a ROC hits an empty pool and becomes a
            # gain; stock-dividend / DRIP / spinoff shares sit on a
            # phantom pool while the sale goes short). Loud, and a lint
            # finding (audit R1-67, S062-24, R1-3).
            self._code_warned.add(sym)
            where = self._where(lineno) if lineno else self._qt_name
            # Printed at parse time, before ticker.map is applied: say
            # it is moot once the line exists (audit S063-10).
            _key = self.apply_currency_suffix(sym, currency)
            msg = (f"{where}: {(row.get('Action') or '').strip() or '?'} "
                   f"row keeps internal symbol code {sym!r} "
                   f"({(row.get('Description') or '')[:60]!r}) — no trade "
                   f"or transfer in this account's exports resolves it. "
                   f"Unless ticker.map already maps {_key}, add "
                   f"GLOBAL {_key} "
                   f"<TICKER>.{self.apply_currency_suffix('X', currency)[2:]}"
                   f" or the position will fragment.")
            # ATTENTION: on the run console (re-audit A2-0027 — a ROC
            # on the code is a phantom gain with rc 0).
            print(f"warning: ATTENTION: {msg}", file=sys.stderr)
            self.lint_findings.append(msg)
        return sym, currency

    # ------------------------------------------------------------- parse

    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        # The description -> (ticker, listing currency) map and the
        # position timelines come from ALL of the account's exports (the
        # shared context taxjson-brokerage builds via prepare_files); a
        # lone file gets a context of its own.
        path = Path(path)
        self._qt_name = path.name
        self._qt_path = path
        self._ambiguous_warned: set = set()
        self._code_warned: set = set()
        self.lint_findings: List[str] = []
        ctx = self.account_context
        if ctx is None or str(path.resolve()) not in ctx.files:
            ctx = build_qt_account_context([path], helper=self)
            ctx.emit()
        self._ctx = ctx
        self._desc_to_ticker = ctx.desc_to_ticker
        rows = self._in_real_order(path, _read_qt_rows(path, warn=True))

        # Taxable hint from the Account Type column: a registered-plan
        # marker -> sheltered; "margin"/"cash" without one -> taxable;
        # anything else ("API" from `taxjson fetch`) -> unknown.
        _types = {(r.get('Account Type') or '').strip() for _, r in rows}
        _types.discard('')
        if _types and all(_QT_REGISTERED_RE.search(t) for t in _types):
            self._qt_taxable_hint = False
        elif _types and all(re.search(r'margin|cash', t, re.I)
                            and not _QT_REGISTERED_RE.search(t)
                            for t in _types):
            self._qt_taxable_hint = True
        else:
            self._qt_taxable_hint = None
        self._check_account_mix(path, rows)

        transactions: List[Dict[str, Any]] = []
        expiries: List[Dict[str, Any]] = []     # EXP rows (settle == date)
        ca_legs: List[str] = []                 # quantity-bearing DIS legs
        net_of_tax: List[str] = []              # NON-RES TAX WITHHELD divs
        no_book_value: List[str] = []           # transfer-ins at $0 cost
        journals: List[str] = []                # BRW listing journals
        journal_txs: List[Dict[str, Any]] = []
        # CIL / REI reversals (audit R1-66): a same-code row with the
        # signs negated cancels its original. Originals by key -> list of
        # emitted leg groups; reversal rows -> (key, lineno), paired at
        # the end of the parse (the export may list newest first).
        rev_originals: Dict[tuple, List[List[Dict[str, Any]]]] = {}
        reversals: List[tuple] = []
        # id(original leg group) -> its line (the account-wide plan
        # names originals by line); stock-dividend notes printed only
        # for the ones a reversal does not cancel (re-audit A2-1060).
        self._orig_line: Dict[int, int] = {}
        sd_notes: List[tuple] = []
        self._rows_seen = 0
        # Each row's broker account (`Account #`): every row emitted for
        # a CSV row carries it as `broker_account`, so cross-file dedup
        # never collapses two accounts' identical rows (audit A2-0008).
        self._qt_accounts = {(r.get('Account #') or '').strip()
                             for _, r in rows} - {''}
        _acct_from, _acct = 0, ''
        for lineno, row in rows:
            for _t in transactions[_acct_from:]:
                if _acct:
                    _t.setdefault('broker_account', _acct)
            _acct_from = len(transactions)
            _acct = (row.get('Account #') or '').strip()
            self._rows_seen += 1
            action_raw = _canon_action(row.get('Action'))
            activity_type = (row.get('Activity Type') or '').strip()
            desc = row.get('Description') or ''
            if [(row.get(c) or '').strip() for c in _QT_COLUMNS[:5]] == \
                    list(_QT_COLUMNS[:5]):
                # The header repeated mid-file (concatenated exports).
                self.count_nonevent("repeated header row")
                continue
            if action_raw == 'FXT' or activity_type == 'FX conversion':
                # "CONVERSION - USD/CAD": the account's own cash moving
                # between denominations. Not a disposition of property
                # in this model — FX cash gains are reconstructed by
                # `taxjson fx-cash` from security cash flows
                # (KNOWN_ISSUES) — so a recognized non-event, not an
                # unclassified action.
                self.count_nonevent(f"FX conversion "
                                    f"({action_raw or activity_type})")
                continue
            currency = self._currency(row, lineno)

            # Dispatch by Action/Activity Type. Questrade uses short
            # codes (DIV, TF6, Buy, Sell, EXP, ASN, EX) plus a longer
            # Activity Type label for context.

            # A stock split arrives as a DIS row tagged 'Dividends' with
            # 'STK SPLIT' in the description — must be checked BEFORE the
            # dividend branch, which would otherwise drop it as a $0 dividend
            # and lose the split shares entirely.
            if (self._is_stock_split(action_raw, activity_type, desc)
                    and not self._is_cash_only(row)):
                tx = self._parse_split(row, currency, desc, lineno)
                if tx:
                    self.note_row_consumed()
                    transactions.append(tx)
                else:
                    self.count_skip("stock-split row not understood "
                                    "(see warning)")
                    self._unbooked(lineno, f"stock-split row not "
                                   f"understood ({desc[:60]!r}) — NOT "
                                   f"booked; the position's share count "
                                   f"is wrong until it is (a .tt SPLIT).")
                continue

            if (action_raw == 'DIS' and _STK_DIV_RE.search(desc)
                    and abs(self._num(row, 'Quantity', lineno)) > 1e-9):
                # STOCK dividend: new shares delivered in kind. This
                # row previously fell into the dividend branch, whose
                # zero-cash guard silently DISCARDED it — the position
                # then went phantom-short by the delivered count at the
                # next full sale. Emitted as a NEUTRAL stock-dividend
                # event (a $0 BUYSELL typed stock_dividend): the gains
                # engine applies the country's rule (partition
                # INPUTS-01); the declared amount is not in the CSV.
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
                        f"({net_sd:,.2f}; {desc[:50]!r}) — refusing to "
                        f"guess which part is the dividend. Split it into "
                        f"a share row and a cash row, or book it in a .tt "
                        f"file.")
                date = self._date(row, 'Transaction Date',
                                  lineno).strftime('%Y-%m-%d')
                sym_raw, scur = self._resolve_symbol(row, currency, lineno)
                sym = self.apply_currency_suffix(sym_raw, scur)
                sd_key = ('STK DIV', sym, round(abs(qty), 6), 0.0)
                self.note_row_consumed()
                if qty < 0:
                    # A negated STK DIV row is Questrade REVERSING a
                    # stock dividend: it cancels the original (paired at
                    # the end of the parse). It used to be counted as
                    # consumed and dropped, leaving phantom shares that
                    # diluted the ACB (audit R1-65).
                    reversals.append((sd_key, lineno, desc))
                    continue
                sd_tx = {
                    'action': 'BUYSELL',
                    'date': date, 'time': '09:30:00',
                    'date_settle': date,
                    'symbol': sym,
                    'quantity': qty, 'currency': currency,
                    'price': 0.0, 'net_amount': 0.0,
                    'gross_amount': 0.0,
                    'account': self.DEFAULT_ACCOUNT,
                    'description': desc,
                    'type': STOCK_DIVIDEND,
                }
                transactions.append(sd_tx)
                _grp = [sd_tx]
                rev_originals.setdefault(sd_key, []).append(_grp)
                self._orig_line[id(_grp)] = lineno
                sd_notes.append((sd_tx,
                                 f"NOTE: {sym_raw}: stock dividend of "
                                 f"{qty:g} share(s) on {date} booked as a "
                                 f"stock-dividend event — the gains run "
                                 f"applies your country's rule to its cost "
                                 f"(`taxjson tax-logic`)."))
                continue

            if action_raw == 'DIV' or activity_type == 'Dividends':
                qty_col = self._num(row, 'Quantity', lineno)
                net_col = self._num(row, 'Net Amount', lineno)
                if abs(qty_col) > 1e-9 and abs(net_col) >= 1e-9:
                    # Shares AND cash on one income row (audit R1-72):
                    # the dividend branch booked the cash and silently
                    # dropped the delivered shares.
                    raise BrokerageParseError(
                        f"{self._where(lineno)}: a {action_raw or 'DIS'} "
                        f"row carries both a quantity ({qty_col:g}) and "
                        f"cash ({net_col:,.2f}; {desc[:50]!r}) — refusing "
                        f"to book the cash and drop the shares. Split it "
                        f"into a share row and a cash row, or book it in "
                        f"a .tt file.")
                if abs(qty_col) > 1e-9:
                    # A quantity-bearing, zero-cash DIS row is a
                    # CORPORATE-ACTION leg (warrant spinoff / rights
                    # distribution chain: +100 / -100 / +100), not an
                    # informational dividend. taxjson-corp-actions
                    # books the spinoff chains (run by `taxjson run`);
                    # anything else is unclassified — loud.
                    _sym = (row.get('Symbol') or '').strip() or '?'
                    if _QT_CA_LEG_RE.search(desc):
                        ca_legs.append(f"{_sym} {qty_col:+g}")
                        if _INTERNAL_CODE_RE.match(_sym.upper()):
                            # Say it when nothing resolves the code
                            # (audit R1-3): the chain would book on it.
                            self._resolve_symbol(row, currency, lineno)
                        self.count_nonevent(
                            "DIS corporate-action leg (quantity-bearing; "
                            "booked by taxjson-corp-actions)")
                    else:
                        self.count_skip(
                            f"DIS row with a quantity and no cash "
                            f"({_sym}) — not a dividend, not booked")
                        self._unbooked(
                            lineno, f"DIS row moves {qty_col:g} {_sym} "
                            f"with no cash ({desc[:60]!r}) — not a "
                            f"dividend, split, stock dividend or spinoff "
                            f"leg the parser knows; NOT booked. Book it "
                            f"in a .tt file.")
                    continue
                tx = self._parse_dividend(row, currency, desc, lineno)
                if tx:
                    self.note_row_consumed()
                    transactions.append(tx)
                    if (_NONRES_NET_RE.search(desc)
                            and tx['action'] in ('DIVIDEND',
                                                 'DIVIDEND_IN_LIEU')
                            and float(tx['net_amount']) > 0):
                        net_of_tax.append(
                            f"{tx['symbol']} {tx['date']} "
                            f"{tx['net_amount']:.2f}")
                elif _QT_BOOK_COST_RE.search(desc):
                    # A zero-cash row that STATES a book-cost change
                    # (RBC's year-end ROC / notional distribution shape,
                    # re-audit A2-1062): the ACB moves and nothing here
                    # books it — not an informational row.
                    self.count_skip("book-cost adjustment row (see "
                                    "warning)")
                    self._unbooked(
                        lineno, f"{(row.get('Symbol') or '').strip() or '?'}"
                        f": the description states a book-cost adjustment "
                        f"({desc[:80]!r}) on a row with no cash — NOT "
                        f"booked; the ACB is wrong until it is. Book it as "
                        f"a .tt ADJUST (a return of capital lowers the "
                        f"ACB, a notional distribution raises it).")
                else:
                    # Zero-net informational row: was marked consumed
                    # with nothing emitted, which the lint reconciled
                    # but the skip summary never showed.
                    self.count_nonevent("zero-net dividend row "
                                        "(informational)")
                continue

            if action_raw == 'TF6' or activity_type == 'Transfers':
                tx = self._parse_transfer(row, currency, lineno)
                if tx:
                    self.note_row_consumed()
                    transactions.append(tx)
                    if (tx['quantity'] > 0 and not tx.get('_book_value')):
                        no_book_value.append(
                            f"{tx['quantity']:g} {tx['symbol']} "
                            f"({tx['date']})")
                    tx.pop('_book_value', None)
                else:
                    self.count_nonevent("cash-only transfer journal")
                continue

            if action_raw == 'FCH' or activity_type == 'Fees and rebates':
                # Account/custody charges (ADR custody fee, ...). Was
                # an unclassified skip — the fee never reached the
                # fee totals.
                tx = self._parse_fee(row, currency, desc, lineno)
                if tx:
                    self.note_row_consumed()
                    transactions.append(tx)
                else:
                    self.count_nonevent("zero-amount fee row")
                continue

            _cash_q = parse_strict_number(
                row.get('Quantity'), field='Quantity',
                where=self._where(lineno), allow_blank=True, blank=0.0)
            if abs(_cash_q) < 1e-9 and not (row.get('Symbol') or '').strip():
                # Cash-only rows (re-audit A2-0277 / A2-0614): they were
                # 'unclassified ... needs a new branch'.
                if (activity_type in ('Deposits', 'Withdrawals',
                                      'Contributions')
                        or action_raw in ('CON', 'DEP', 'EFT', 'EWD',
                                          'WDR', 'CTR')):
                    self.count_nonevent(f"cash deposit/withdrawal "
                                        f"({action_raw or activity_type})")
                    continue
                if action_raw == 'INT' or activity_type == 'Interest':
                    # Interest credited (+) or margin interest charged
                    # (-), sign kept, as RBC's interest rows.
                    _net = self._num(row, 'Net Amount', lineno)
                    if abs(_net) < 0.005:
                        self.count_nonevent("zero interest row")
                        continue
                    _d = self._date(row, 'Transaction Date', lineno)
                    self.note_row_consumed()
                    transactions.append({
                        'action': 'INTEREST',
                        'date': _d.strftime('%Y-%m-%d'),
                        'time': '09:30:00',
                        'date_settle': _d.strftime('%Y-%m-%d'),
                        'symbol': 'CASH', 'quantity': 0.0,
                        'currency': currency, 'net_amount': _net,
                        'type': 'interest',
                        'account': self.DEFAULT_ACCOUNT,
                        'description': desc,
                    })
                    continue
                if action_raw == 'LFJ':
                    # Stock-lending income: real income no branch books.
                    _net = self._num(row, 'Net Amount', lineno)
                    if abs(_net) >= 0.005:
                        self.count_skip("stock-lending income (LFJ)")
                        self._unbooked(
                            lineno, f"stock-lending income {_net:,.2f} "
                            f"{currency} ({desc[:50]!r}) — NOT booked; "
                            f"take it from the slip (a T5 / 1099-MISC).")
                        continue

            if action_raw == 'CIL' and _CIL_RE.search(desc):
                # Cash in lieu of a FRACTIONAL share (a stock dividend
                # or consolidation that would have delivered x.5
                # shares delivers x whole ones + cash). The fraction
                # never entered inventory (the DIS row books the whole
                # shares), so the disposition is a same-day pair: the
                # fraction acquired at its stock-dividend cost ($0 —
                # the same convention as the DIS branch) and sold for
                # the cash. In a taxable account the cash is the gain;
                # was a counted skip.
                legs = self._parse_cash_in_lieu(row, currency, desc, lineno,
                                                rev_originals, reversals)
                if legs:
                    # Consumed only on emission — the builder counts
                    # its own skip, and consuming AND skipping the same
                    # row put the lint reconciliation below zero.
                    self.note_row_consumed()
                    transactions.extend(legs)
                continue

            if action_raw == 'REI' or activity_type == 'Dividend reinvestment':
                # DRIP purchase. Questrade reports the cash dividend as
                # its own Dividends row (booked above) and the
                # reinvestment as REI: Quantity = shares bought, Net
                # Amount = −cost, Price column 0 with the unit price
                # only in the description ("REINV@C$7.12345"). A plain
                # BUYSELL at that cost; the residual dividend cash
                # (dividend − cost) stays as cash. Was a counted skip —
                # DRIP shares never entered inventory.
                tx = self._parse_reinvestment(row, currency, desc, lineno,
                                              rev_originals, reversals)
                if tx:
                    self.note_row_consumed()   # builder counts its skip
                    transactions.append(tx)
                continue

            is_trade = action_raw in ('Buy', 'Sell')
            _du = desc.upper()
            code_expired = action_raw == 'EXP'
            code_assigned = action_raw in ('ASN', 'EX')
            word_expired = ' - EXPIRED' in _du
            word_assigned = 'ASSIGNMENT' in _du or 'EXERCISE' in _du
            is_expired = code_expired or word_expired
            is_assigned = code_assigned or word_assigned
            if not (is_trade or is_expired or is_assigned):
                _q = parse_strict_number(
                    row.get('Quantity'), field='Quantity',
                    where=self._where(lineno), allow_blank=True, blank=0.0)
                if action_raw == 'BRW' and 'JOURNAL' in _du and abs(_q) > 1e-9:
                    # "... JOURNAL POSITION TO USD" / "FROM CAD": units
                    # journaled between the CAD and USD lines of ONE
                    # security (Norbert's gambit, SAMPLF.TO <-> SAMPLF.U.TO).
                    # Booked the way RBC's TFR journal legs are: a
                    # TRANSFER per leg, the USD leg on its TSX listing
                    # (SAMPLF.U.TO). A ticker.map JOURNAL rule makes the two
                    # lines one pool and the pair nets out; the legs used
                    # to be skipped, leaving the units on SAMPLF.TO.
                    _bv = _BRW_BOOK_VALUE_RE.search(desc)
                    _date = self._date(row, 'Transaction Date', lineno)
                    _jtx = {
                        'action': 'TRANSFER',
                        'date': _date.strftime('%Y-%m-%d'),
                        'time': _date.strftime('%H:%M:%S'),
                        'date_settle': _date.strftime('%Y-%m-%d'),
                        'symbol': self.apply_currency_suffix(
                            (row.get('Symbol') or '').strip().upper(),
                            currency),
                        'quantity': _q, 'currency': currency,
                        'price': 0.0, 'net_amount': 0.0,
                        'gross_amount': 0.0,
                        'account': self.DEFAULT_ACCOUNT,
                        'description': desc,
                    }
                    if _bv:
                        _jtx['book_value'] = parse_strict_number(
                            _bv.group(1), field='BOOK VALUE',
                            where=self._where(lineno))
                        _cnv = _BRW_CNV_RE.search(desc)
                        if _cnv:
                            _jtx['_cnv'] = desc_number(
                                _cnv.group(1), where=self._where(lineno),
                                field='CNV@ rate')
                    self.note_row_consumed()
                    transactions.append(_jtx)
                    journals.append(f"{_jtx['symbol']} {_q:+g}")
                    journal_txs.append(_jtx)
                    continue
                # Previously a silent drop — a new Questrade action code
                # lost rows with zero signal. Count it; the summary at
                # the end of the parse names every dropped category.
                self.count_skip(f"action {action_raw or activity_type or '?'!s}")
                if abs(_q) > 1e-9:
                    # A row that moves shares and is not booked changes
                    # the position: say so where `taxjson run` shows it
                    # (audit R1-71; the note reached only the .sum).
                    self._unbooked(
                        lineno, f"action {action_raw or '?'!r} moves "
                        f"{_q:g} {(row.get('Symbol') or '').strip() or '?'}"
                        f" ({desc[:60]!r}) — not an action the parser "
                        f"knows; NOT booked. Book it in a .tt file.")
                continue

            dt = self._date(row, 'Transaction Date', lineno)
            date = dt.strftime("%Y-%m-%d")
            time = dt.strftime("%H:%M:%S")

            opt = self.parse_option_from_description(desc)
            _sym_col = (row.get('Symbol') or '').strip().upper()
            from taxjson.lib.brokerages.rbc_direct import _names_underlying
            if opt and _sym_col and _names_underlying(opt['base'],
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
            mult = float(self.OPTION_MULTIPLIER) if opt else 1.0
            # The export never states a contract's size: 100 is assumed
            # unless the row's Gross shows a mini (B10).
            size_basis = 'assumed' if opt else ''
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
                    self.count_skip(
                        f"action {action_raw or activity_type or '?'!s} "
                        f"(zero-cash, not an option leg)")
                    continue
            if is_trade:
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
            # EXP / ASN / EX option legs: the CSV quantity is already
            # signed to close the open position — a long option expires
            # or is exercised with a NEGATIVE quantity, a short with a
            # positive one. signed_quantity() would force it positive
            # and add a phantom contract instead of netting it to zero.

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
                        return abs(_g - exp) <= max(0.02, 0.002 * max(exp,
                                                                      _g))
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
                if fx_settled:
                    # Net is CAD, Gross USD: compare in USD.
                    _fx = _FX_SETTLED_RE.search(desc)
                    _rate = float(_fx.group(1)) or 1.0
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
                price = round(price * rate, 8)
                # SIGNED, like the non-FX branch (audit S014-06): a buy's
                # cost is -Net, a sale's proceeds +Net — abs() turned a
                # sale whose commission exceeded its gross (Net < 0)
                # into positive proceeds.
                if abs(net_signed) > 0:
                    net = -net_signed if qty > 0 else net_signed
                else:
                    net = round(net * rate, 8)
                # The commission in CAD too (re-audit A2-0615): it stayed
                # the USD-sized figure on a row whose price, gross and
                # net are CAD, so gross + commission != net and the fees
                # report / Schedule 3 outlays were short by the rate.
                # The CAD truth is the cash: the gap between Net and the
                # CAD gross.
                if comm:
                    _gross_cad = abs(qty) * price * mult
                    comm = round((net - _gross_cad) if qty > 0
                                 else (_gross_cad - net), 2)

            # Option symbol reconstruction from Description; fall back to
            # the bare Symbol column (which is often non-OCC like SAMPLG.OPT).
            if opt:
                symbol = self.format_occ_symbol(opt['right'], opt['base'], opt['expiry'], opt['strike'])
            else:
                # Upper-cased: 'sample.to' split the pool from SAMPLE.TO
                # (audit R1-71).
                symbol = (row.get('Symbol') or '').strip().upper()
            symbol = self.apply_currency_suffix(symbol, listing_currency)
            self.note_row_consumed()
            if date_settle is None:
                # The LISTING's market decides the cycle and calendar,
                # not the row currency (A2-1052 / A2-1054): SAMPLF.U.TO in
                # USD settles through CDS, a CAD-settled US stock on the
                # US calendar (lib/dates.market_of, the rule every parser
                # shares).
                from taxjson.lib.dates import market_of
                _mkt = market_of(symbol, listing_currency)
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
                    date = self.option_expiry_booking_date(
                        date, opt['expiry'])
                    time = '16:00:00'
                else:
                    # A right / warrant: its expiry date from the
                    # description too (audit S065-04).
                    date = self.non_option_expiry_booking_date(date, desc)
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
            transactions.append(_tx)
            if is_expired and not is_assigned:
                expiries.append(_tx)
        self._pair_reversals(transactions, rev_originals, reversals)
        _kept = {id(t) for t in transactions}
        for _tx, _msg in sd_notes:
            if id(_tx) in _kept:
                print(_msg, file=sys.stderr)
        self.clamp_settlement_to_expiry(transactions, expiries)
        self.disambiguate_split_fills(transactions)
        if ca_legs:
            print(f"note: {shown_name(path)}: {len(ca_legs)} quantity-bearing DIS "
                  f"corporate-action leg(s) ({', '.join(ca_legs[:8])}"
                  f"{', ...' if len(ca_legs) > 8 else ''}) are not "
                  f"booked by the parser — taxjson-corp-actions books "
                  f"spinoff/rights chains (`taxjson run` does this); "
                  f"check the position if you parse without it.",
                  file=sys.stderr)
        taxable = self._is_taxable()
        if net_of_tax and taxable is not False:
            # ATTENTION (re-audit A2-0276 / A2-0282): income and the
            # foreign tax credit are wrong until the slip is used.
            print(f"warning: ATTENTION: {shown_name(path)}: {len(net_of_tax)} "
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
                  f"{'' if taxable else ' (Account type unknown — ignore in a registered account.)'}",
                  file=sys.stderr)
        if (no_book_value and taxable is not False
                and not self.transfer_costs_checked_downstream):
            # What actually happens (audit S063-00): a TAXABLE account's
            # TRANSFER rows are custody evidence kept OUT of the books,
            # so the shares never enter the pool and their later sale
            # reads short — no gain at all. The remedy the pipeline
            # accepts is the real acquisition as a backdated .tt BUYSELL
            # (or missing_history.json); a .tt TRANSFER is refused in a taxable
            # account and there is no OPENING_BALANCE .tt action.
            # ATTENTION (re-audit A2-0276 / A2-0283): the sale reads
            # as a short and the year's gain is missing.
            print(f"warning: ATTENTION: {shown_name(path)}: {len(no_book_value)} "
                  f"transfer-in(s) carry no TRANSFER BOOK VALUE "
                  f"({'; '.join(no_book_value[:6])}"
                  f"{'; ...' if len(no_book_value) > 6 else ''}). In a "
                  f"TAXABLE account transfers are kept out of the books, "
                  f"so these shares have no cost and their sale will "
                  f"read as a short: book the real acquisition (date, "
                  f"quantity, the sending broker's "
                  f"{self.law('ACB', 'cost basis', 'cost')}) as a .tt BUYSELL "
                  f"row, or declare it in missing_history.json (`taxjson "
                  f"find-missing-history --write-missing-history`)."
                  f"{'' if taxable else ' (Account type unknown — ignore in a registered account.)'}",
                  file=sys.stderr)
        for _t in transactions[_acct_from:]:
            if _acct:
                _t.setdefault('broker_account', _acct)
        self._cost_journal_pairs(journal_txs)
        if journals:
            print(f"note: {shown_name(path)}: {len(journals)} BRW journal row(s) "
                  f"move units between the CAD and USD lines of one "
                  f"security ({', '.join(journals[:6])}) — booked as "
                  f"TRANSFER legs; a ticker.map JOURNAL rule (e.g. "
                  f"JOURNAL SAMPLF.U.TO SAMPLF.TO) makes the lines one pool so "
                  f"the pair nets out.", file=sys.stderr)
        self.emit_skip_summary(path.name)
        return transactions

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
            i['price'] = round(bv / i['quantity'], 8)
            # The SAME security's other line only (re-audit A2-1061: two
            # journals on one date swapped their costs): SAMPLF.U.TO and
            # SAMPLF.TO share the root SAMPLF.
            out = next((o for o in legs if o['quantity'] < 0
                        and o['date'] == i['date']
                        and _journal_root(o['symbol'])
                        == _journal_root(i['symbol'])
                        and abs(o['quantity'] + i['quantity']) < 1e-9
                        and not o.get('net_amount')), None)
            if out is None:
                continue
            rate = rates.get(id(i))
            o_bv = (round(bv * rate, 2)
                    if rate and out['currency'] != i['currency'] else bv)
            out['net_amount'] = out['gross_amount'] = o_bv
            out['price'] = round(o_bv / abs(out['quantity']), 8)

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
            if abs(abs(gross) - expected) > max(0.02, 0.002 * max(
                    expected, abs(gross))):
                raise BrokerageParseError(
                    f"{where}: |Gross Amount| {abs(gross):,.2f} is not "
                    f"|Quantity| {abs(qty):g} x Price {price:g} x "
                    f"{mult:g} = {expected:,.2f} ({desc[:50]!r}) — a "
                    f"swapped or mislabelled column; refusing to book "
                    f"it.")
        if not fx_settled and abs(abs(diff) - abs(comm)) > 0.011:
            raise BrokerageParseError(
                f"{where}: Net Amount differs from Gross Amount "
                f"{gross:,.2f} by {abs(diff):,.2f}, not by the "
                f"Commission {abs(comm):,.2f} ({desc[:50]!r}) — a "
                f"swapped or mislabelled column; refusing to book it.")
        if fx_settled and cash is not None and abs(cash) > 0:
            # CAD-settled US trade (audit R1-73): the CAD cash (a buy's
            # cost, a sale's proceeds) must be the USD Gross at the
            # stated rate, give or take the Commission (Questrade's
            # column may carry it in either currency). The exemption
            # above let a wrong or column-swapped CAD Net book.
            expected = abs(gross) * rate
            resid = abs(cash - expected)
            tol = 0.011 + 1e-5 * abs(cash)
            if not any(abs(resid - c) <= tol
                       for c in (abs(comm), abs(comm) * rate)):
                raise BrokerageParseError(
                    f"{where}: CAD Net Amount {abs(cash):,.2f} is not the USD "
                    f"Gross {abs(gross):,.2f} x EXCHANGE RATE {rate:g} = "
                    f"{expected:,.2f} give or take the Commission "
                    f"{abs(comm):,.2f} ({desc[:50]!r}) — a wrong or "
                    f"swapped column; refusing to book it.")

    def _parse_cash_in_lieu(self, row, currency, desc, lineno,
                            rev_originals, reversals):
        """CIL -> a same-day pair (the fraction acquired at $0, sold for
        the cash). SIGNED (audit R1-66): Net Amount > 0 is the cash in
        lieu; Net Amount < 0 is Questrade REVERSING an earlier CIL,
        which cancels that original (paired in _pair_reversals) — abs()
        booked it as a second sale, the cash counted twice as gain."""
        m = _CIL_RE.search(desc)
        frac = (desc_number(m.group(1), where=self._where(lineno),
                            field='CASH IN LIEU fraction') if m else 0.0)
        net = self._num(row, 'Net Amount', lineno)
        cash = abs(net)
        if frac <= 0 or cash <= 0:
            self.count_skip("CIL row with no fraction/cash")
            return []
        sym_raw, scur = self._resolve_symbol(row, currency, lineno)
        sym = self.apply_currency_suffix(sym_raw, scur)
        key = ('CIL', sym, round(frac, 6), round(cash, 2))
        if net < 0:
            reversals.append((key, lineno, desc))
            self.note_row_consumed()      # read into parser state
            return []
        date = self._date(row, 'Transaction Date', lineno).strftime('%Y-%m-%d')
        base = {'date': date, 'date_settle': date, 'symbol': sym,
                'currency': currency, 'commission': 0.0,
                'account': self.DEFAULT_ACCOUNT, 'description': desc}
        legs = [
            {**base, 'action': 'BUYSELL', 'time': '09:30:00',
             'quantity': frac, 'price': 0.0, 'net_amount': 0.0,
             'gross_amount': 0.0},
            {**base, 'action': 'BUYSELL', 'time': '09:30:01',
             'quantity': -frac, 'price': round(cash / frac, 8),
             'net_amount': cash, 'gross_amount': cash},
        ]
        rev_originals.setdefault(key, []).append(legs)
        self._orig_line[id(legs)] = lineno
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
        key_f = str(self._qt_path.resolve())
        paired = self._ctx.rev_paired.get(key_f, set())
        dropped = self._ctx.rev_drop.get(key_f, set())
        for key, lineno, desc in reversals:
            if lineno not in paired:
                kind, sym, qty, amt = key
                raise BrokerageParseError(
                    f"{self._where(lineno)}: a {kind} reversal "
                    f"({sym} {qty:g}, {amt:,.2f}; {desc[:50]!r}) whose "
                    f"original {kind} row (on or before it) is in no "
                    f"export of this account — refusing to book it as a "
                    f"second event. Add the export that holds the "
                    f"original, or book the correction in a .tt file.")
        drop = set()
        for groups in rev_originals.values():
            for grp in groups:
                if self._orig_line.get(id(grp)) in dropped:
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
        sym_raw = (m.group(1) if m
                   else (row.get('Symbol') or '').strip().lstrip('.'))
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
            self.count_nonevent("REI row with no shares and no cash")
            return None
        if qty <= 1e-12 or net < 0.005:
            # Units with no cash (an in-kind reinvestment: is there a
            # separate cash DIV row? the export does not say) or cash
            # with no units. Skipping it lost the units and left a
            # phantom short at the next sale, under a note that said the
            # row had no shares (audit S063-06). RBC refuses the same.
            raise BrokerageParseError(
                f"{self._where(lineno)}: REI (reinvestment) row with "
                f"Quantity {qty_s:g} and Net Amount {net_s:,.2f} "
                f"({desc[:50]!r}) — a reinvestment buys units for cash; "
                f"with {'no cash' if qty > 1e-12 else 'no units'} the "
                f"parser cannot tell the purchase (and any income) apart. "
                f"Book it in a .tt file (the units at the REINV@ price) "
                f"and delete the row.")
        if (qty_s > 0) == (net_s > 0):
            raise BrokerageParseError(
                f"{self._where(lineno)}: REI row with Quantity {qty_s:g} "
                f"and Net Amount {net_s:,.2f} of the same sign "
                f"({desc[:50]!r}) — a reinvestment buys shares for cash "
                f"(or a reversal returns both); refusing to guess.")
        sym_raw, scur = self._resolve_symbol(row, currency, lineno)
        key = ('REI', self.apply_currency_suffix(sym_raw, scur),
               round(qty, 6), round(net, 2))
        if qty_s < 0:
            reversals.append((key, lineno, desc))
            self.note_row_consumed()      # read into parser state
            return None
        date = self._date(row, 'Transaction Date', lineno).strftime('%Y-%m-%d')
        sdt = self._date(row, 'Settlement Date', lineno, required=False)
        date_settle = sdt.strftime('%Y-%m-%d') if sdt else date
        self.check_settle_order(date, date_settle, where=self._where(lineno),
                                what=repr(desc[:50]))
        m = _REINV_PRICE_RE.search(desc)
        # 'REINV@C$1,234.56' is 1234.56, not 1 (audit S062-20); a
        # decimal comma falls back to the cash / units.
        price = desc_number(m.group(1), strict=False) if m else None
        from taxjson.lib.brokerages.rbc_direct import (
            _REINV_CUR, _REINV_CUR_RE, reinvest_identity_error)
        mc = _REINV_CUR_RE.search(desc)
        bad = reinvest_identity_error(
            qty, net, price,
            _REINV_CUR.get(mc.group(1).upper(), '?') if mc else currency,
            currency)
        if bad:
            raise BrokerageParseError(
                f"{self._where(lineno)}: REI row: {bad} ({desc[:50]!r}) "
                f"— a wrong or shifted column; refusing to book it as the "
                f"units' cost (re-audit A2-0268).")
        if not price:
            price = round(net / qty, 8)
        tx = {
            'action': 'BUYSELL',
            'date': date, 'time': '09:30:00', 'date_settle': date_settle,
            'symbol': key[1],
            'quantity': qty, 'currency': currency,
            'price': price, 'net_amount': net, 'gross_amount': net,
            'commission': 0.0,
            'account': self.DEFAULT_ACCOUNT,
            'description': desc,
        }
        _grp = [tx]
        rev_originals.setdefault(key, []).append(_grp)
        self._orig_line[id(_grp)] = lineno
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
            print(f"warning: Questrade stock-split row not understood "
                  f"(need 'ON N SHS' and a nonzero quantity), skipping: "
                  f"{desc!r}", file=sys.stderr)
            return None
        symbol, scur = self._resolve_symbol(row, currency, lineno)
        raw_sym = (row.get('Symbol') or '').strip().lstrip('.').upper()
        if not symbol and not raw_sym:
            print(f"warning: Questrade stock split: couldn't resolve a traded "
                  f"ticker for {symbol!r} ({desc!r}); the SPLIT may not apply "
                  f"to the right pool — add a ticker.map rule if needed.",
                  file=sys.stderr)
        symbol = self.apply_currency_suffix(symbol, scur)

        dt = self._date(row, 'Transaction Date', lineno)
        # On a SHORT position the split DEBITS shares (Quantity < 0 for
        # a forward split): (held + received)/held gave 0.95 for a
        # 21-for-20 and 0 for a 2-for-1 (audit S063-08). The account's
        # own trades say which side the pool is on; a long (or unknown)
        # pool keeps the signed rule (a negative quantity is then a
        # reverse split).
        pos = self._ctx.position_on(symbol, dt.strftime("%Y-%m-%d"),
                                    before=True) if self._ctx else 0.0
        if pos < -1e-9:
            ratio = (held - received) / held
        else:
            ratio = (held + received) / held
        if ratio <= 0:
            print(f"warning: Questrade stock-split row gives a ratio of "
                  f"{ratio:g} for {symbol} ({desc!r}) — not booked.",
                  file=sys.stderr)
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
        # A2-0098; lib/income_dating applies CA-INC-03 / US-INC-01).
        from taxjson.lib.brokerages.rbc_direct import PIL_DESC_RE
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
        # distribution label, as neutral facts (lib/income_dating).
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
                                       opt['expiry'], opt['strike']),
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
        price = (round(net / (abs(qty_signed) * mult), 8)
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
