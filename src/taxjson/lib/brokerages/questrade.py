import csv
import io
import math
import re
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

from taxjson.lib.brokerages.base import (BaseBrokerage, BrokerageParseError,
                                         _parse_div_qty_rate,
                                         is_roc_description,
                                         parse_strict_number)


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

# Registered-plan markers in the Account Type column (a warning that
# matters only in a TAXABLE account stays quiet for these).
_QT_REGISTERED_RE = re.compile(
    r'\b(RRSP|RRIF|TFSA|LIRA|LIF|LRSP|RLSP|RESP|FHSA|RDSP|SRRSP|PRPP)\b',
    re.IGNORECASE)

# A stock-split distribution (Action DIS) reports the NEW shares in Quantity
# and the held count as "... ON <held> SHS ...". Used to recover the ratio.
_SPLIT_ON_SHS_RE = re.compile(r'\bON\s+([\d,]+(?:\.\d+)?)\s+SH', re.IGNORECASE)

# A STOCK DIVIDEND row (split-share corps like TDb pay non-cash share
# distributions): DIS / 'Dividends' with 'STK DIV' in the description
# and the DELIVERED share count in Quantity. Distinct from STK SPLIT
# (ratio-based) and from cash DIV rows (zero Quantity).
# A US-listed security bought in a CAD-only account (RESP, some RRSPs):
# Questrade settles in CAD and writes the rate into the description —
# Price and Gross Amount are in USD, Net Amount is the CAD actually paid,
# and the Currency column says CAD (the SETTLEMENT currency).
_FX_SETTLED_RE = re.compile(r'EXCHANGE RATE\s+([0-9]+(?:\.[0-9]+)?)', re.IGNORECASE)

_CIL_RE = re.compile(r'CASH\s+IN\s+LIEU\s+OF\s+([0-9]*\.?[0-9]+)', re.I)
_REINV_PRICE_RE = re.compile(r'REINV@(?:[A-Z]{1,3}\$)?\s*([0-9]+(?:\.[0-9]+)?)', re.I)
_STK_DIV_RE = re.compile(r'\bSTK\.?\s+DIV\b|\bSTOCK\s+DIVIDEND\b',
                         re.IGNORECASE)

# Questrade emits internal codes like "S098765" or "A12345" for some
# dividend rows (typically post-transfer-in, before the security is
# linked to its real ticker). Pattern: one letter followed by digits.
_INTERNAL_CODE_RE = re.compile(r'^[A-Z]\d+$')

# Patterns to strip when building a "description key" for matching
# dividend rows to the underlying trade rows. Mirrors qt_dividends.pl.
_DESC_NOISE_RES = [
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
    # Transfer-specific suffixes — broker names and book-value notes
    # arrive on transfer-in rows from RBC, BMO, TD, CIBC, Scotia.
    re.compile(r'\s+(?:RBC|BMO|TD|CIBC|SCOTIA|NATIONAL BANK)\s.*$', re.IGNORECASE),
    re.compile(r'\s+DOMINION SECURITIES.*$', re.IGNORECASE),
    re.compile(r'\s+TFER\s+(?:FROM|TO).*$', re.IGNORECASE),
    re.compile(r'\s+TRANSFER\s+(?:FROM|TO|BOOK\s+VALUE).*$', re.IGNORECASE),
    re.compile(r'\s+BOOK\s+VALUE.*$', re.IGNORECASE),
    # Cosmetic suffixes seen on various row types.
    re.compile(r'\s+COMMON STOCK.*$', re.IGNORECASE),
    re.compile(r'\s+CLASS A.*$', re.IGNORECASE),
]
_SPINOFF_PARENT_RE = re.compile(
    r'SPINOFF ON .* FROM SEC# \S+ (.*?)\s+REC', re.IGNORECASE
)
# Questrade transfer-in rows carry the cost basis (ACB) only in the
# description — "TRANSFER BOOK VALUE <amount>" — no numeric column holds
# it. Extract it so a parsed transfer establishes the right pool size.
_BOOK_VALUE_RE = re.compile(
    r'BOOK\s+VALUE\s+([\d,]+(?:\.\d+)?)', re.IGNORECASE
)
# FCH fee rows name the security only in the description:
#   "ADR CUSTODY FEE # SHARES TKR RECORD DATE 06/15/25"
_FEE_SHARES_TICKER_RE = re.compile(
    r'#\s*SHARES\s+([A-Z][A-Z0-9.\-]*)', re.IGNORECASE)
# A quantity-bearing zero-cash DIS row of a spinoff / rights chain — a
# corporate-action leg taxjson-corp-actions books (same marker it uses).
_QT_CA_LEG_RE = re.compile(r'\b(SPINOFF|RTS\s+DIST|RIGHTS\s+DIST)\b',
                           re.IGNORECASE)
# A dividend Questrade posts NET of non-resident withholding.
_NONRES_NET_RE = re.compile(r'NON-?RES\w*\.?\s+TAX\s+WITH', re.IGNORECASE)


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
    return desc.strip().upper()


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
        return cur

    def _is_taxable(self) -> Optional[bool]:
        if self.account_taxable is not None:
            return self.account_taxable
        return self._qt_taxable_hint

    def _resolve_symbol(self, row: Dict[str, Any], currency: str):
        """(symbol, suffix currency) for a non-trade row. Questrade
        writes some rows under an internal code (S098765, a TF6's
        R223608) or a dotted dividend code (.BTO for B2Gold held as
        BTG on the NYSE); ONLY those are rebound to the traded symbol
        of the same security (matched on the description). A real
        ticker is kept: first-match-wins used to rebind, e.g., an
        FTN.PR dividend onto FTN.PRA because both share a description.
        A description that maps to several symbols is never guessed."""
        raw = (row.get('Symbol') or '').strip()
        sym = raw.lstrip('.')
        code_like = (not sym or bool(_INTERNAL_CODE_RE.match(sym))
                     or raw.startswith('.'))
        cands = self._desc_to_ticker.get(
            _get_desc_key(row.get('Description') or ''), set())
        if not code_like:
            bare = re.sub(r'\.TO$', '', sym, flags=re.IGNORECASE)
            others = sorted({s for s, _ in cands} - {bare})
            if others:
                key = (bare, tuple(others))
                if key not in self._ambiguous_warned:
                    self._ambiguous_warned.add(key)
                    print(f"warning: {self._qt_name}: {sym!r} is booked "
                          f"under its own symbol, but the same security "
                          f"({(row.get('Description') or '')[:50]!r}) "
                          f"trades as {', '.join(others)} — if they are "
                          f"one security, fold them with a ticker.map "
                          f"rule.", file=sys.stderr)
            return sym, currency
        if len(cands) == 1:
            return next(iter(cands))
        if len(cands) > 1:
            key = (sym, tuple(sorted(cands)))
            if key not in self._ambiguous_warned:
                self._ambiguous_warned.add(key)
                print(f"warning: {self._qt_name}: {sym or '(blank)'!r} "
                      f"({(row.get('Description') or '')[:60]!r}) matches "
                      f"several traded symbols "
                      f"({', '.join(s for s, _ in sorted(cands))}) — not "
                      f"rebound; map it with a ticker.map rule.",
                      file=sys.stderr)
        return sym, currency

    # ------------------------------------------------------------- parse

    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        # Read once into memory so we can do two passes: first to build a
        # description→(ticker, market-currency) map from trade rows,
        # second to emit transactions (internal-code and dotted-code
        # rows resolve to the traded symbol; see _resolve_symbol).
        self._qt_name = path.name
        self._ambiguous_warned: set = set()
        raw = Path(path).read_bytes()
        text = (raw.decode('utf-16') if raw[:2] in (b'\xff\xfe', b'\xfe\xff')
                else raw.decode('utf-8-sig'))
        reader = csv.DictReader(io.StringIO(text, newline=''))
        header = [h.strip() if h else h for h in (reader.fieldnames or [])]
        missing = [c for c in _QT_COLUMNS if c not in header]
        if missing:
            raise BrokerageParseError(
                f"{path.name}: Questrade export is missing required "
                f"column(s) {', '.join(repr(c) for c in missing)} — "
                f"refusing to guess (a missing money column read as 0 "
                f"corrupts the return). Export Activity with the "
                f"standard English columns.")
        reader.fieldnames = header
        rows = []
        for lineno, row in enumerate(reader, 2):
            vals = [str(v or '').strip() for k, v in row.items()
                    if k is not None]
            if not any(vals):
                continue              # blank trailer line: not a row
            rows.append((lineno, row))

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

        # desc_key -> {(clean_symbol, market_currency), ...}. Built from
        # both Trade AND Transfer rows: a position established purely by
        # transfer-in (typical for LIRA/RRSP carry-overs) still needs its
        # market identity learned somewhere. A key with more than one
        # symbol is ambiguous and never used to rebind.
        self._desc_to_ticker: Dict[str, set] = {}

        def _record_ticker(row):
            sym = (row.get('Symbol') or '').strip().lstrip('.').rstrip()
            sym = re.sub(r'\.TO$', '', sym, flags=re.IGNORECASE)
            cur = (row.get('Currency') or '').strip()
            desc = row.get('Description') or ''
            if cur.upper() == 'CAD' and _FX_SETTLED_RE.search(desc):
                cur = 'USD'          # listing currency, not settlement
            key = _get_desc_key(desc)
            if key and sym and cur and not _INTERNAL_CODE_RE.match(sym):
                self._desc_to_ticker.setdefault(key, set()).add((sym, cur))

        for _, row in rows:
            if (row.get('Activity Type') or '').strip() in ('Trades',
                                                             'Transfers'):
                _record_ticker(row)

        transactions: List[Dict[str, Any]] = []
        expiries: List[Dict[str, Any]] = []     # EXP rows (settle == date)
        ca_legs: List[str] = []                 # quantity-bearing DIS legs
        net_of_tax: List[str] = []              # NON-RES TAX WITHHELD divs
        no_book_value: List[str] = []           # transfer-ins at $0 cost
        # CIL / REI reversals (audit R1-66): a same-code row with the
        # signs negated cancels its original. Originals by key -> list of
        # emitted leg groups; reversal rows -> (key, lineno), paired at
        # the end of the parse (the export may list newest first).
        rev_originals: Dict[tuple, List[List[Dict[str, Any]]]] = {}
        reversals: List[tuple] = []
        self._rows_seen = 0
        for lineno, row in rows:
            self._rows_seen += 1
            action_raw = (row.get('Action') or '').strip()
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
            if self._is_stock_split(action_raw, activity_type, desc):
                tx = self._parse_split(row, currency, desc, lineno)
                if tx:
                    self.note_row_consumed()
                    transactions.append(tx)
                else:
                    self.count_skip("stock-split row not understood "
                                    "(see warning)")
                continue

            if action_raw == 'DIS' and _STK_DIV_RE.search(desc):
                # STOCK dividend: new shares delivered in kind. This
                # row previously fell into the dividend branch, whose
                # zero-cash guard silently DISCARDED it — the position
                # then went phantom-short by the delivered count at the
                # next full sale. The shares enter at zero cost: the
                # taxable amount of a stock dividend is the fund's
                # declared amount, which the CSV doesn't carry — moot
                # in registered accounts; taxable accounts surface it
                # via the zero-basis walk, and distributions.map can
                # supply the declared per-share amount as an ADJUST.
                self.note_row_consumed()
                qty = self._num(row, 'Quantity', lineno)
                if qty > 0:
                    date = self._date(row, 'Transaction Date',
                                      lineno).strftime('%Y-%m-%d')
                    sym_raw, scur = self._resolve_symbol(row, currency)
                    print(f"NOTE: {sym_raw}: stock dividend of {qty:g} "
                          f"share(s) on {date} entered at $0 cost — in "
                          f"a taxable account, add the fund's declared "
                          f"amount via distributions.map for the "
                          f"correct ACB.", file=sys.stderr)
                    transactions.append({
                        'action': 'BUYSELL',
                        'date': date, 'time': '09:30:00',
                        'date_settle': date,
                        'symbol': self.apply_currency_suffix(
                            sym_raw, scur),
                        'quantity': qty, 'currency': currency,
                        'price': 0.0, 'net_amount': 0.0,
                        'gross_amount': 0.0,
                        'account': self.DEFAULT_ACCOUNT,
                        'description': desc,
                    })
                continue

            if action_raw == 'DIV' or activity_type == 'Dividends':
                qty_col = self._num(row, 'Quantity', lineno)
                net_col = self._num(row, 'Net Amount', lineno)
                if abs(qty_col) > 1e-9 and abs(net_col) < 1e-9:
                    # A quantity-bearing, zero-cash DIS row is a
                    # CORPORATE-ACTION leg (warrant spinoff / rights
                    # distribution chain: +100 / -100 / +100), not an
                    # informational dividend. taxjson-corp-actions
                    # books the spinoff chains (run by `taxjson run`);
                    # anything else is unclassified — loud.
                    _sym = (row.get('Symbol') or '').strip() or '?'
                    if _QT_CA_LEG_RE.search(desc):
                        ca_legs.append(f"{_sym} {qty_col:+g}")
                        self.count_nonevent(
                            "DIS corporate-action leg (quantity-bearing; "
                            "booked by taxjson-corp-actions)")
                    else:
                        self.count_skip(
                            f"DIS row with a quantity and no cash "
                            f"({_sym}) — not a dividend, not booked")
                    continue
                tx = self._parse_dividend(row, currency, desc, lineno)
                if tx:
                    self.note_row_consumed()
                    transactions.append(tx)
                    if (_NONRES_NET_RE.search(desc)
                            and tx['action'] == 'DIVIDEND'
                            and float(tx['net_amount']) > 0):
                        net_of_tax.append(
                            f"{tx['symbol']} {tx['date']} "
                            f"{tx['net_amount']:.2f}")
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
                # Previously a silent drop — a new Questrade action code
                # lost rows with zero signal. Count it; the summary at
                # the end of the parse names every dropped category.
                self.count_skip(f"action {action_raw or activity_type or '?'!s}")
                continue

            dt = self._date(row, 'Transaction Date', lineno)
            date = dt.strftime("%Y-%m-%d")
            time = dt.strftime("%H:%M:%S")

            settle_dt = self._date(row, 'Settlement Date', lineno,
                                   required=False)
            if settle_dt:
                date_settle = settle_dt.strftime("%Y-%m-%d")
            else:
                # Era- and market-aware fallback for a BLANK settlement
                # cell only (T+2 pre-cutover equities); a present but
                # unparseable cell is an error above, not a fallback.
                date_settle = self.equity_settlement_date(
                    date, currency, "%Y-%m-%d")

            qty = self._num(row, 'Quantity', lineno)
            opt = self.parse_option_from_description(desc)
            mult = float(self.OPTION_MULTIPLIER) if opt else 1.0
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
                # SIGNED commission. The old abs(Commission) made a
                # REBATE a charge (2x the rebate wrong). The cash truth
                # is Questrade's own Net Amount (a buy's negative, a
                # sell's positive): the commission is a charge when a
                # buy cost MORE than its gross / a sell brought in
                # LESS, a rebate otherwise — and its size must be the
                # Commission column's (checked in _check_trade_money).
                cash = -net_signed if qty > 0 else net_signed
                fx_settled = bool(_FX_SETTLED_RE.search(desc))
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
                                        diff, mult, fx_settled)

            # CAD-settled US trade (see _FX_SETTLED_RE): the security is
            # the US listing, and the ACB is the CAD the account paid —
            # Net Amount — not the USD gross read as if it were CAD
            # (which under-stated a Broadcom buy by the whole exchange
            # rate and filed it as AVGO.TO, a CDR-shaped symbol the
            # DISTINCT rule then kept apart from the real pool).
            listing_currency = currency
            fx_m = _FX_SETTLED_RE.search(desc) if carries_cash else None
            if fx_m and currency.upper() == 'CAD' and float(fx_m.group(1)) > 0:
                rate = float(fx_m.group(1))
                listing_currency = 'USD'
                price = round(price * rate, 8)
                net_col = abs(net_signed)
                if net_col > 0:
                    net = net_col
                else:
                    net = round(net * rate, 8)

            # Option symbol reconstruction from Description; fall back to
            # the bare Symbol column (which is often non-OCC like AAPL.OPT).
            if opt:
                symbol = self.format_occ_symbol(opt['right'], opt['base'], opt['expiry'], opt['strike'])
            else:
                symbol = row.get('Symbol') or ''
            symbol = self.apply_currency_suffix(symbol, listing_currency)
            self.note_row_consumed()

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
                date_settle = date

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
                    qty, price, is_option=bool(opt)),
                # Declared contract size: the schema notional check is
                # an error, not a guess, for rows that carry it.
                'multiplier': mult,
                'account': self.DEFAULT_ACCOUNT,
                # Carry the raw description so a description-keyed
                # --security-overrides rule can correct a mislabeled ticker —
                # IB/RBC/Webull trade rows already do; Questrade's was the gap.
                'description': desc,
            }
            transactions.append(_tx)
            if is_expired and not is_assigned:
                expiries.append(_tx)
        self._pair_reversals(transactions, rev_originals, reversals)
        self.clamp_settlement_to_expiry(transactions, expiries)
        self.disambiguate_split_fills(transactions)
        if ca_legs:
            print(f"note: {path.name}: {len(ca_legs)} quantity-bearing DIS "
                  f"corporate-action leg(s) ({', '.join(ca_legs[:8])}"
                  f"{', ...' if len(ca_legs) > 8 else ''}) are not "
                  f"booked by the parser — taxjson-corp-actions books "
                  f"spinoff/rights chains (`taxjson run` does this); "
                  f"check the position if you parse without it.",
                  file=sys.stderr)
        taxable = self._is_taxable()
        if net_of_tax and taxable is not False:
            print(f"warning: {path.name}: {len(net_of_tax)} dividend(s) "
                  f"marked NON-RES TAX WITHHELD are booked at the NET "
                  f"amount — the export gives neither the gross nor the "
                  f"tax ({'; '.join(net_of_tax[:6])}"
                  f"{'; ...' if len(net_of_tax) > 6 else ''}). In a "
                  f"TAXABLE account the income is understated and the "
                  f"foreign tax credit missing: take the gross and the "
                  f"withholding from the T5/NR4 slip."
                  f"{'' if taxable else ' (Account type unknown — ignore in a registered account.)'}",
                  file=sys.stderr)
        if no_book_value and taxable is not False:
            print(f"warning: {path.name}: {len(no_book_value)} "
                  f"transfer-in(s) carry no TRANSFER BOOK VALUE and are "
                  f"booked at $0 cost ({'; '.join(no_book_value[:6])}"
                  f"{'; ...' if len(no_book_value) > 6 else ''}). In a "
                  f"TAXABLE account the whole sale price would be gain: "
                  f"supply the ACB from the sending broker (a .tt "
                  f"OPENING_BALANCE or TRANSFER row)."
                  f"{'' if taxable else ' (Account type unknown — ignore in a registered account.)'}",
                  file=sys.stderr)
        self.emit_skip_summary(path.name)
        return transactions

    def _check_trade_money(self, lineno, desc, qty, price, gross, comm,
                           diff, mult, fx_settled) -> None:
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

    def _parse_cash_in_lieu(self, row, currency, desc, lineno,
                            rev_originals, reversals):
        """CIL -> a same-day pair (the fraction acquired at $0, sold for
        the cash). SIGNED (audit R1-66): Net Amount > 0 is the cash in
        lieu; Net Amount < 0 is Questrade REVERSING an earlier CIL,
        which cancels that original (paired in _pair_reversals) — abs()
        booked it as a second sale, the cash counted twice as gain."""
        m = _CIL_RE.search(desc)
        frac = float(m.group(1)) if m else 0.0
        net = self._num(row, 'Net Amount', lineno)
        cash = abs(net)
        if frac <= 0 or cash <= 0:
            self.count_skip("CIL row with no fraction/cash")
            return []
        sym_raw, scur = self._resolve_symbol(row, currency)
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
        return legs

    def _pair_reversals(self, transactions, rev_originals, reversals):
        """Each CIL/REI reversal row cancels one original with the same
        symbol, quantity and amount: both drop out. A reversal whose
        original is not in this file is REFUSED — booking it would
        either duplicate the event (the old abs()) or invent one."""
        drop = set()
        for key, lineno, desc in reversals:
            groups = rev_originals.get(key) or []
            if not groups:
                kind, sym, qty, amt = key
                raise BrokerageParseError(
                    f"{self._where(lineno)}: a {kind} reversal "
                    f"({sym} {qty:g}, {amt:,.2f}; {desc[:50]!r}) whose "
                    f"original {kind} row is not in this file — refusing "
                    f"to book it as a second event. If the original is "
                    f"in an earlier export, delete both rows (they "
                    f"cancel) or book the correction in a .tt file.")
            for leg in groups.pop():
                drop.add(id(leg))
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
        if qty <= 0 or net <= 0:
            self.count_skip("REI row with no shares/cost")
            return None
        if (qty_s > 0) == (net_s > 0):
            raise BrokerageParseError(
                f"{self._where(lineno)}: REI row with Quantity {qty_s:g} "
                f"and Net Amount {net_s:,.2f} of the same sign "
                f"({desc[:50]!r}) — a reinvestment buys shares for cash "
                f"(or a reversal returns both); refusing to guess.")
        sym_raw, scur = self._resolve_symbol(row, currency)
        key = ('REI', self.apply_currency_suffix(sym_raw, scur),
               round(qty, 6), round(net, 2))
        if qty_s < 0:
            reversals.append((key, lineno, desc))
            self.note_row_consumed()      # read into parser state
            return None
        date = self._date(row, 'Transaction Date', lineno).strftime('%Y-%m-%d')
        sdt = self._date(row, 'Settlement Date', lineno, required=False)
        date_settle = sdt.strftime('%Y-%m-%d') if sdt else date
        m = _REINV_PRICE_RE.search(desc)
        price = float(m.group(1)) if m else round(net / qty, 8)
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
        rev_originals.setdefault(key, []).append([tx])
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

    def _parse_split(self, row, currency, desc, lineno):
        """Emit a SPLIT scaling the existing pool by (held + received)/held —
        a non-taxable share-count change, not a dividend. Questrade reports the
        NEW shares in Quantity, the held count as 'ON <held> SHS', and a temp
        internal Symbol (e.g. K012006); the real ticker is resolved from a
        trade of the same security, so the SPLIT lands on the pool the user
        actually holds."""
        received = self._num(row, 'Quantity', lineno)
        m = _SPLIT_ON_SHS_RE.search(desc)
        held = float(m.group(1).replace(',', '')) if m else 0.0
        if not m or received == 0 or held <= 0:
            print(f"warning: Questrade stock-split row not understood "
                  f"(need 'ON N SHS' and a nonzero quantity), skipping: "
                  f"{desc!r}", file=sys.stderr)
            return None
        ratio = (held + received) / held

        symbol, scur = self._resolve_symbol(row, currency)
        raw_sym = (row.get('Symbol') or '').strip().lstrip('.')
        if (not symbol or _INTERNAL_CODE_RE.match(symbol)) and (
                symbol == raw_sym):
            print(f"warning: Questrade stock split: couldn't resolve a traded "
                  f"ticker for {symbol!r} ({desc!r}); the SPLIT may not apply "
                  f"to the right pool — add a ticker.map rule if needed.",
                  file=sys.stderr)
        symbol = self.apply_currency_suffix(symbol, scur)

        dt = self._date(row, 'Transaction Date', lineno)
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
        ('.BTO' — B2Gold's dividend on a position held as BTG on the
        NYSE, paid in USD; the row's own currency would yield BTO.US,
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
        symbol_raw, suffix_currency = self._resolve_symbol(row, currency)
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
        return {
            'action': 'DIVIDEND',
            'date': date,
            'time': time,
            'date_settle': date,
            'symbol': symbol,
            'quantity': qty,
            'currency': currency,
            'price': price,
            'net_amount': net,
            'gross_amount': net,
            'type': 'dividend',
            'description': desc,
            'account': self.DEFAULT_ACCOUNT,
        }

    def _parse_transfer(self, row, currency, lineno):
        """Questrade TF6 row: asset transfer in or out. Quantity is signed
        (+ for transfer-in, − for transfer-out).

        Two fields need recovering from elsewhere:
          - Symbol. The TF6 Symbol column is often an internal Questrade
            code (e.g. R223608); it is looked up from a trade row of the
            same security (see _resolve_symbol). Without this the
            transferred position lands under e.g. R223608.US instead of
            O.US. A real ticker on the row is kept.
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

        symbol_raw, suffix_currency = self._resolve_symbol(row, currency)
        symbol_raw = symbol_raw.replace(' ', '.')
        if symbol_raw and _INTERNAL_CODE_RE.match(symbol_raw):
            # A transfer-in of a security never TRADED in this file
            # keeps Questrade's internal code (e.g. R223608): the
            # position then fragments from later trades/dividends
            # booked under the real ticker, with nothing to say why.
            # Parse it anyway (the row is real) but say so loudly —
            # a ticker.map GLOBAL line fixes the identity.
            print(f"warning: questrade transfer on {date} keeps "
                  f"internal symbol code {symbol_raw!r} (no trade row "
                  f"in this file resolves it) — map it to the real "
                  f"ticker with a ticker.map GLOBAL line or the "
                  f"position will fragment.", file=sys.stderr)
        symbol = self.apply_currency_suffix(symbol_raw, suffix_currency) if symbol_raw else ''

        # Cost basis: prefer the "BOOK VALUE" note in the description;
        # fall back to the Net Amount column when it isn't present.
        net = abs(self._num(row, 'Net Amount', lineno))
        m = _BOOK_VALUE_RE.search(desc)
        if m:
            net = parse_strict_number(m.group(1), field='TRANSFER BOOK VALUE',
                                      where=self._where(lineno))
        price = round(net / abs(qty_signed), 8) if abs(qty_signed) > 1e-9 else 0.0
        return {
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
            # Popped by parse_file (the no-book-value report).
            '_book_value': bool(m) or net > 0,
        }
