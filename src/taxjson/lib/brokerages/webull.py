import contextlib
import csv
import io
import re
import sys
from pathlib import Path
from typing import List, Dict, Any

from taxjson.lib.brokerages.base import (BaseBrokerage, BrokerageParseError,
                                         OPTION_STRIKE_RE, parse_strict_number,
                                         read_broker_text,
                                         shown_name)
from taxjson.lib.core import is_option_symbol, parse_option_expiry


# Webull's option descriptions run the ticker directly into the date with
# no separator: "CALL ABBV01/17/25 190". The base regex requires whitespace
# between the two — override it here.
_WEBULL_OPTION_RE = re.compile(
    r'(CALL|PUT)\s+([A-Z.\d]+?)\s*(\d{2}/\d{2}/\d{2})\s+'
    + OPTION_STRIKE_RE, re.IGNORECASE
)


# First cells a cut-short data row can start with ('C' .. 'USD').
_CURRENCY_PREFIXES = frozenset({'U', 'US', 'USD', 'C', 'CA', 'CAD'})


class WebullBrokerage(BaseBrokerage):
    DEFAULT_ACCOUNT = "WB"
    # Webull's CSVs only carry USD/CAD positions; anything else falls back to US.
    CURRENCY_EXT_MAP = {'USD': 'US', 'CAD': 'TO'}
    CURRENCY_EXT_FALLBACK = 'US'

    def _option_description_patterns(self):
        return (_WEBULL_OPTION_RE,)

    def statement_accounts(self) -> set:
        """The broker account the last parsed export names in its
        preamble ('Account Number / Numéro de compte'); empty when it
        names none. Cross-file dedup tells two Webull accounts' identical
        rows apart by it (audit A2-0286)."""
        return set(getattr(self, '_accounts', None) or ())

    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        self._accounts = set()
        parsed = self._parse_rows(path)
        if parsed is None:
            return []
        transactions, expiries, skipped_actions = parsed
        self._accounts = set(getattr(self, '_last_accounts', ()) or ())
        # Assignment pairing looks at the Webull exports BESIDE this one
        # too (audit S065-24): the files are settlement-dated, so a put
        # assigned on Dec 31 closes in one year's file while its stock
        # leg settles in January in the next. Sibling rows only take
        # part in the pairing; this file's rows are what is returned.
        pool, pool_exp = list(transactions), list(expiries)
        for sib in self._sibling_exports(path):
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    sp = WebullBrokerage()._parse_rows(sib)
            except (ValueError, OSError, UnicodeDecodeError):
                continue        # reported when that file is parsed
            if sp:
                pool.extend(sp[0])
                pool_exp.extend(sp[1])
        self._mark_assignments(pool, pool_exp, own=transactions,
                               source=path.name)
        self._check_zero_closes(pool, expiries, path.name)
        self.clamp_settlement_to_expiry(transactions, expiries)
        # Ticker-change hint over every export of the folder (audit
        # A2-1067 / A2-1070): a rename across yearly files is named
        # when the file holding the new symbol's short is parsed.
        self._warn_ticker_changes(pool, path.name, own=transactions)
        for t in pool:
            t.pop('_under', None)
            t.pop('_where', None)
        # Parser-level disambiguation so a downstream `taxjson-sort --dedup`
        # can't collapse byte-identical split-fill rows.
        self.disambiguate_split_fills(transactions)
        if skipped_actions:
            detail = ", ".join(f"{len(v)}× {a}" for a, v in
                               sorted(skipped_actions.items()))
            # Expiry and exercise/assignment ARE booked (from the $0
            # BUY/SELL rows); only these other action codes are not
            # (audit S065-22). A skipped row that moves shares or cash
            # is UNBOOKED — the channel `taxjson run` echoes to the
            # console and `--strict` refuses, as every other parser
            # uses (audit A2-0285 / A2-0288 / A2-0289 / A2-1069).
            print(
                f"warning: Webull parser books only BUY/SELL rows — "
                f"skipped {sum(len(v) for v in skipped_actions.values())} "
                f"row(s) with other action codes ({detail}); those "
                f"events (a dividend, a transfer) are NOT booked. Enter "
                f"any that matter via a .tt file, and check no position "
                f"is left holding shares it no longer has.",
                file=sys.stderr,
            )
            for code, rows in sorted(skipped_actions.items()):
                for where, sym, qty, amount in rows:
                    if not (qty or amount):
                        continue
                    what = ", ".join(x for x in (
                        f"quantity {qty}" if qty else "",
                        f"amount {amount}" if amount else "") if x)
                    print(f"warning: UNBOOKED: {where}: Webull {code} row "
                          f"{sym or '-'} ({what}) is not booked — enter "
                          f"it as a .tt line if it is a dividend, a "
                          f"transfer or another taxable event.",
                          file=sys.stderr)
        return transactions

    @staticmethod
    def _sibling_exports(path: Path) -> List[Path]:
        """The other Webull exports in the same folder (`wb_*.csv` or
        `*webull*.csv`)."""
        try:
            siblings = sorted(path.parent.iterdir())
        except OSError:
            return []
        out = []
        for p in siblings:
            n = p.name.lower()
            if (p != path and p.is_file() and n.endswith('.csv')
                    and (n.startswith('wb_') or 'webull' in n)):
                out.append(p)
        return out

    def _parse_rows(self, path: Path):
        """(transactions, expiries, skipped_actions) for one export, or
        None when it has no Trading Summary header. No assignment
        pairing — parse_file does that across the folder."""
        # The shared decoder (audit A2-0101 / A2-1451): a UTF-16 re-save
        # is read like the IB/Questrade/RBC twins read it, never refused
        # as 'not UTF-16'.
        content = read_broker_text(path)
        lines = content.splitlines()

        header_index = self._find_header(lines)
        if header_index == -1:
            return None
        account = self._preamble_account(lines[:header_index])
        self._last_accounts = {account} if account else set()

        reader = csv.reader(io.StringIO("\n".join(lines[header_index:])))
        # Resolve columns by their HEADER LABELS, never by position. The
        # 2024 Trading Summary has 9 columns (Proceeds in col 8); the 2025
        # one inserted an empty column, moving Proceeds to col 9. A
        # position-based read of one layout on the other puts the amount
        # in the wrong field — the silent zero-cost failure that inflated
        # a filed return. Unknown layouts fail loudly.
        # The bilingual header cells hold embedded newlines, so the header
        # is the first complete CSV RECORD, not the first physical line.
        header_row = next(csv.reader(io.StringIO(
            "\n".join(lines[header_index:]))), [])
        cols = self._resolve_columns(header_row, path)
        transactions: List[Dict[str, Any]] = []
        data_rows = 0
        current_symbol = ""
        current_is_under = False
        current_description = ""
        current_type = ""
        # Webull's parser only books BUY/SELL. Blank continuation rows are
        # expected and harmless, but a row with a REAL non-trade action
        # (dividend, option assignment/expiry, transfer) would be dropped
        # silently, leaving a phantom open position. Count those so the loss
        # is surfaced rather than invisible.
        skipped_actions: Dict[str, List[tuple]] = {}
        # Option expiry rows (see is_expiry below).
        expiries: List[Dict[str, Any]] = []

        def cell(row, key):
            j = cols.get(key)
            return row[j].strip() if j is not None and j < len(row) else ""

        width = len(header_row)
        for row in reader:
            # File line of this record (the header record is the first
            # one the reader returns; line_num counts physical lines
            # read so far, so a record's LAST physical line).
            where = f"{shown_name(path)} line {header_index + reader.line_num}"
            if (row and len(row) < width and any(c.strip() for c in row)
                    and row[0].strip().upper() in _CURRENCY_PREFIXES):
                # A data row cut short — the last line of a download
                # that stopped early ('CAD,12-12-2024,'): it used to
                # fall through as a preamble or blank continuation row
                # and the trade vanished at rc 0 (audit A2-0028).
                raise BrokerageParseError(
                    f"{where}: a row starting {','.join(row)[:40]!r} has "
                    f"{len(row)} cell(s) but the header has {width} — the "
                    f"row is cut short (an export that stopped early?). "
                    f"Re-download the file; refusing to drop the row.")
            if not row or len(row) < 3:
                continue
            currency = cell(row, 'currency').upper()
            date_raw = cell(row, 'date')
            action_raw = cell(row, 'action').upper()
            is_trade = action_raw in ('BUY', 'SELL')
            if currency not in ('USD', 'CAD'):
                if is_trade:
                    # A trade row whose currency cannot be read used to
                    # vanish here, before row accounting saw it (audit
                    # R1-92): the disposition was simply lost.
                    raise BrokerageParseError(
                        f"{where}: {action_raw} row has currency "
                        f"{currency!r} — Webull Trading Summaries carry "
                        f"USD or CAD; refusing to drop the trade. Fix "
                        f"the cell (a continuation row repeats its "
                        f"security's currency).")
                continue            # repeated page headers, preamble
            if not is_trade:
                # A populated action we don't handle is a real dropped event;
                # an empty action is just a blank continuation row.
                if action_raw:
                    skipped_actions.setdefault(action_raw, []).append(
                        (where, cell(row, 'symbol').lstrip('@'),
                         cell(row, 'quantity'), cell(row, 'proceeds')))
                elif any(cell(row, k) for k in ('date', 'quantity',
                                                'price', 'proceeds')):
                    # A blank Action cell on a row that carries a date,
                    # quantity, price or proceeds is a damaged trade
                    # row, not a continuation row: the sale vanished at
                    # rc 0 (audit A2-0788; the blank-Date twin R1-97).
                    raise BrokerageParseError(
                        f"{where}: a row with a blank Action Code carries "
                        f"trade cells (date {cell(row, 'date') or '-'}, "
                        f"quantity {cell(row, 'quantity') or '-'}, "
                        f"proceeds {cell(row, 'proceeds') or '-'}) — "
                        f"refusing to drop the trade or guess BUY/SELL. "
                        f"Restore the Action Code from the original "
                        f"export.")
                continue
            if not date_raw:
                # A dated trade row with its Date cut off was reported as
                # an 'unhandled action' and dropped (audit R1-97).
                raise BrokerageParseError(
                    f"{where}: {action_raw} row has a blank Date — "
                    f"refusing to drop the trade or guess its date.")
            if any('\n' in c or '\r' in c for c in row):
                # Real exports put line breaks only in the bilingual
                # header cells. One inside a data row is a quoted cell
                # that never closed and swallowed the following rows
                # (audit S066-04).
                raise BrokerageParseError(
                    f"{where}: {action_raw} row has a cell spanning "
                    f"several lines — an unescaped quote (\") in a "
                    f"Description swallowed the rows after it. Fix the "
                    f"quote in the CSV.")

            data_rows += 1
            if len(row) != width:
                # A row wider or narrower than its header (a 2025-layout
                # row under the 9-column 2024 header, a hand edit) reads
                # every cell after the gap from the wrong column: the
                # Proceeds of a priced sale came back blank -> $0
                # (audit R1-91).
                raise BrokerageParseError(
                    f"{where}: {action_raw} row has {len(row)} cells but "
                    f"the header has {width} — misaligned columns "
                    f"(two export layouts mixed in one file, a hand "
                    f"edit, or a row cut short); refusing to read "
                    f"Proceeds from the wrong column.")
            symbol_raw = cell(row, 'symbol')
            description_raw = cell(row, 'description')
            qty_raw = cell(row, 'quantity')
            price_raw = cell(row, 'price')
            proceeds_raw = cell(row, 'proceeds')

            if symbol_raw:
                _new_symbol = symbol_raw.lstrip('@')
                if _new_symbol != current_symbol:
                    # Description carry-over is for CONTINUATION fills
                    # of the SAME security. A new symbol with a blank
                    # Description cell must not inherit the previous
                    # security's text — an equity row after an option
                    # row would re-parse as that option (KNOWN_ISSUES
                    # "Webull blank-Description carry-over").
                    current_description = ""
                    current_type = ""
                current_symbol = _new_symbol
                # `@ROOT` is how Webull names an option row's
                # underlying; any other Symbol cell is not one.
                current_is_under = symbol_raw.startswith('@')
            if description_raw:
                current_description = description_raw
            # Type Code (OPC option / SHS shares), carried over blank
            # continuation rows like the description.
            type_raw = cell(row, 'type').upper()
            if type_raw:
                current_type = type_raw

            dt = self.parse_date(date_raw, "%d-%m-%Y", "%Y-%m-%d")
            if dt is None:
                # Passed through verbatim before, as both date and
                # settle date, with only a schema warning (audit
                # A2-0618).
                raise BrokerageParseError(
                    f"{where}: {action_raw} row has Date {date_raw!r} — "
                    f"not DD-MM-YYYY (Webull's format) or YYYY-MM-DD; "
                    f"refusing to guess the date.")
            date_str = dt.strftime("%Y-%m-%d")

            # Strict parses named by file line (audit R1-91): a blank,
            # garbage ('N/A') or decimal-comma cell is an error, never a
            # silent 0. Price and Proceeds may be blank only together —
            # an option expiry row. A priced trade with no Proceeds used
            # to book $0 (the back-computed fee then absorbed the whole
            # gross, so even --strict passed).
            qty = parse_strict_number(qty_raw, field='Quantity',
                                      where=where)
            price = parse_strict_number(price_raw, field='Price',
                                        where=where, allow_blank=True,
                                        blank=0.0)
            if abs(price) > 1e-9 and not proceeds_raw:
                raise BrokerageParseError(
                    f"{where}: {action_raw} {qty_raw} @ {price_raw} has a "
                    f"blank Proceeds cell — a priced trade must carry its "
                    f"cash; refusing to book it at $0.")
            # Webull prints a buy's proceeds in accounting parentheses
            # ("(1,234.56)"), read as NEGATIVE: cash out. A buy's cost is
            # booked as a magnitude (direction lives in the quantity
            # sign; an unparenthesised buy amount reads the same). A
            # SALE keeps its sign: a debit there is a close whose
            # commission exceeded the gross (a $0.00 option close with
            # a fee) — negative proceeds, which abs() used to book as
            # cash RECEIVED (audit R1-100).
            _signed = parse_strict_number(
                proceeds_raw, field='Proceeds', where=where,
                allow_blank=True, blank=0.0)
            if action_raw == 'BUY' and qty < 0 and _signed > 0:
                # The quantity AND the cash say SALE under a BUY label
                # (re-audit A2-0287): it was booked as a purchase, also
                # under --strict. Refused, as the generic importer does.
                raise BrokerageParseError(
                    f"{where}: a BUY row with a NEGATIVE Quantity "
                    f"{qty_raw} and cash IN (Proceeds {proceeds_raw}) — "
                    f"both say sale; refusing to guess.")
            net_amount = abs(_signed) if action_raw == 'BUY' else _signed
            qty = self.signed_quantity(qty, action_is_sell=(action_raw == 'SELL'))

            opt = self.parse_option_from_description(current_description)
            # The row's Type Code decides the security type when it is
            # there (audit S065-17): an OPC row whose description is not
            # a readable contract was booked as 1 SHARE of the
            # underlying, and an SHS row whose description happened to
            # parse as a contract as 100 x the shares.
            if current_type == 'OPC' and not opt:
                raise BrokerageParseError(
                    f"{where}: {action_raw} row has Type Code OPC (an "
                    f"option) but its Security Description "
                    f"{current_description!r} is not a readable contract "
                    f"(CALL/PUT ROOT MM/DD/YY STRIKE) — refusing to book "
                    f"it as shares.")
            if current_type == 'SHS' and opt:
                raise BrokerageParseError(
                    f"{where}: {action_raw} row has Type Code SHS "
                    f"(shares) but its Security Description "
                    f"{current_description!r} reads as an option "
                    f"contract — refusing to guess which it is.")
            under_sym = ''
            if opt:
                symbol = self.format_occ_symbol(opt['right'], opt['base'], opt['expiry'], opt['strike'])
                is_option = True
                # The row's own Symbol column (`@ZZS`) names the
                # underlying; the description root can differ (an
                # adjusted contract's `ZZS1`) — audit S066-02.
                if current_is_under:
                    under_sym = self.apply_currency_suffix(
                        current_symbol, currency)
            else:
                symbol = current_symbol
                is_option = False
            symbol = self.apply_currency_suffix(symbol, currency)

            # Webull's CSV Date column IS the SETTLEMENT date (user-verified
            # against real Webull statements) — keep it as date_settle
            # verbatim, and BACK-compute the trade date (options T+1;
            # equities T+2 pre-cutover, T+1 after). The trade date feeds the
            # trade-basis consumers: the US engine's year attribution, FIFO
            # acquisition order, and the §1091 window. (An earlier fix
            # wrongly assumed the CSV date was the TRADE date and added the
            # settlement lag ON TOP of an already-settled date.)
            # EXCEPT an option expiry row (no price, no proceeds): its
            # Date is the EXPIRY date itself — there is no settlement
            # cycle to walk back. Shifting it a business day earlier
            # made a 0DTE long that expired worthless close BEFORE the
            # buy that opened it: a phantom $0 short WRITE. Kept on the
            # expiry date, stamped at the close (after every same-day
            # 09:30 trade), and the same-day opening trade's settle is
            # clamped to the expiry (clamp_settlement_to_expiry below).
            is_expiry = (is_option and abs(price) < 1e-9
                         and abs(net_amount) < 1e-9)
            if (not is_option and abs(price) < 1e-9
                    and abs(net_amount) < 1e-9):
                # Shares at no price and no cash: a row whose Price and
                # Proceeds were cut (a spreadsheet re-save pads the
                # cells back), or a transfer/journal row — booking it
                # put the shares in at $0 or took the sale's proceeds
                # to $0 at rc 0 (audit A2-0102 / A2-0619). The generic
                # importer refuses the same row.
                raise BrokerageParseError(
                    f"{where}: {action_raw} {qty_raw} {current_symbol} has "
                    f"no Price and no Proceeds — a share trade at $0 is a "
                    f"cut row or a transfer, not a trade; refusing to book "
                    f"it at $0. Fix the cells, or enter a transfer as a "
                    f".tt line.")
            if not is_expiry and abs(price) > 1e-9:
                self._check_trade_money(where, action_raw, qty, price,
                                        net_amount, is_option)
            if is_expiry:
                trade_date, row_time = date_str, '16:00:00'
            else:
                trade_date = self.trade_date_from_settlement(
                    date_str, currency, is_option, "%Y-%m-%d")
                row_time = '09:30:00'
            _tx = {
                'action': 'BUYSELL',
                'date': trade_date,
                'time': row_time,
                'date_settle': date_str,
                'symbol': symbol,
                'quantity': qty,
                'currency': currency,
                'price': price,
                'fee': self.back_compute_fee(qty, price, net_amount, is_option),
                'net_amount': net_amount,
                'gross_amount': self.theoretical_gross(qty, price, is_option),
                'account': self.DEFAULT_ACCOUNT,
                'description': current_description,
                '_where': where,
            }
            if account:
                # The broker account the export names (cross-file dedup
                # keeps two accounts' identical rows apart: A2-0286).
                _tx['broker_account'] = account
            if under_sym:
                _tx['_under'] = under_sym
            transactions.append(_tx)
            if is_expiry:
                expiries.append(_tx)
        if len(transactions) != data_rows:
            # Row accounting: every dated BUY/SELL data row becomes one
            # transaction. A mismatch means a row was dropped or split.
            raise BrokerageParseError(
                f"{path}: Webull row accounting failed — {data_rows} "
                f"BUY/SELL rows but {len(transactions)} transactions")
        transactions = self._oldest_first(transactions)
        return transactions, expiries, skipped_actions

    def _oldest_first(self, transactions):
        """The rows in replay order. Webull groups its rows by security
        and prints no clock time, so rows at one date keep the order the
        parser emits them (tax-logic CA-DATE-14 / US-DATE-13). A
        newest-first export is read bottom-up: each security group whose
        dates never increase is reversed, and a group with one date
        follows the file's other groups when they all agree (audit
        A2-1068)."""
        groups: Dict[str, List[int]] = {}
        for i, t in enumerate(transactions):
            groups.setdefault(t['symbol'], []).append(i)
        verdict: Dict[str, bool] = {}
        for sym, idx in groups.items():
            ds = [transactions[i]['date_settle'] for i in idx]
            if len(set(ds)) > 1:
                verdict[sym] = self.newest_first(ds)
        default = set(verdict.values()) == {True}
        out = list(transactions)
        for sym, idx in groups.items():
            if verdict.get(sym, default):
                for i, t in zip(idx, [transactions[i] for i in idx][::-1]):
                    out[i] = t
        return out

    @staticmethod
    def _preamble_account(lines) -> str:
        """The broker account id the preamble names ('Account Number /
        Numéro de compte:,,,,,,,<id>,' or 'Account Number: <id>'), or ''."""
        for line in lines:
            cells = next(csv.reader([line]), [])
            if not cells or not cells[0].strip().lower().startswith(
                    'account number'):
                continue
            head, _, rest = cells[0].partition(':')
            for c in [rest] + cells[1:]:
                c = c.strip()
                if c:
                    return c
        return ''

    def _check_trade_money(self, where, action, qty, price, net,
                           is_option) -> None:
        """Fail-closed identity for a priced trade (audit S023-19, the
        RBC check's twin). The Trading Summary has no commission column:
        the gap between Proceeds and |qty| x Price x multiplier IS the
        commission, so it must be a charge (a buy costs at least its
        gross, a sale nets at most it) of commission size. A Proceeds
        cell off by a factor (a shifted or mislabelled column) used to
        book with only a schema warning; Questrade, IB and RBC refuse
        it. Real Webull commissions are a few dollars."""
        mult = self.OPTION_MULTIPLIER if is_option else 1
        gross = abs(qty) * abs(price) * mult
        fee = (gross - net) if action == 'SELL' else (net - gross)
        low = -(0.05 + 0.005 * gross)
        high = (max(250.0, 3.0 * abs(qty) if is_option else 0.0)
                + 0.05 * gross)
        if fee < low or fee > high:
            raise BrokerageParseError(
                f"{where}: {action} Proceeds {net:,.2f} does not fit "
                f"|Quantity| {abs(qty):g} x Price {abs(price):g}"
                f"{' x 100' if is_option else ''} = {gross:,.2f} (implied "
                f"commission {fee:,.2f}) — a wrong or shifted column; "
                f"refusing to book it.")

    # Header label -> field. Matching is on lowercased label text with
    # newlines folded, so the bilingual two-line cells ("Currency\nDevise")
    # match. Order matters only for readability.
    _HEADER_LABELS = (
        ('currency', 'currency'),
        ('date', 'date'),
        ('action', 'action code'),
        ('symbol', 'symbol'),
        ('description', 'security description'),
        ('type', 'type code'),
        ('quantity', 'quantity'),
        ('price', 'price'),
        ('proceeds', 'proceeds'),
    )
    _REQUIRED = ('currency', 'date', 'action', 'symbol', 'description',
                 'quantity', 'price', 'proceeds')

    @classmethod
    def _resolve_columns(cls, header: List[str], path) -> Dict[str, int]:
        labels = [" ".join(str(c).split()).lower() for c in header]
        cols: Dict[str, int] = {}
        for key, needle in cls._HEADER_LABELS:
            # 'date' must not match 'Settlement date'-style labels of
            # other columns; the Trading Summary's date column is
            # labelled exactly "Date".
            hits = [j for j, lab in enumerate(labels)
                    if (lab == needle if key == 'date' else needle in lab)]
            if len(hits) > 1:
                # First-match used to win: an inserted 'Gross Proceeds'
                # or 'Price Currency' column silently became the amount
                # or the price (audit R1-98).
                raise BrokerageParseError(
                    f"{path}: ambiguous Webull export layout — "
                    f"{len(hits)} header labels contain {needle!r}: "
                    f"{[header[j] for j in hits]!r}. Refusing to guess "
                    f"which column is the {key}.")
            if hits:
                cols[key] = hits[0]
        missing = [k for k in cls._REQUIRED if k not in cols]
        if missing:
            raise BrokerageParseError(
                f"{path}: unrecognised Webull export layout — could not "
                f"find column(s) {', '.join(missing)} in the header "
                f"{header!r}. Refusing to guess column positions.")
        return cols

    # Webull's exercise/assignment charge on the stock leg: every real
    # assignment/exercise seen carries exactly $1.00 (net = qty x strike
    # +/- 1), while ordinary stock trades carry the regular commission
    # (~$2.91-4.13, or $0 in a commission-free promotion). The fee is
    # the only evidence in the Trading Summary that separates the two.
    _EXERCISE_FEE = 1.00

    def _mark_assignments(self, transactions, expiries, own=None,
                          source='') -> None:
        """A Webull option closed at price 0 is an expiry — UNLESS shares
        of the underlying change hands at the strike within a few days in
        the matching quantity and direction, carrying Webull's $1.00
        exercise/assignment charge: then it was ASSIGNED (short) or
        EXERCISED (long). Webull's Trading Summary shows both only as a
        $0 option close plus an ordinary stock trade at the strike.
        Booking it as an expiry realizes the premium as its own gain or
        loss instead of folding it into the shares' cost or proceeds
        (ITA s.49(3) for a call, s.49(3.1) for a put; a holder's exercise
        adds the option cost to the shares). Mark both legs ASSIGN — the
        engine's two-row convention.

        The pairing is an inference, so every pair is named on stderr,
        and a trade at the strike carrying an ordinary commission is
        NOT paired (a limit order at a round strike after a worthless
        expiry — audits R1-15/R1-94/R1-175) but named as a candidate.
        Candidates are matched by smallest settle gap across ALL
        options (audit S066-12: file order used to decide). `own` (the
        rows of the file being parsed) limits the messages to pairs
        that touch it; `transactions` may include sibling exports."""
        from datetime import date as _d
        own_ids = ({id(t) for t in own} if own is not None
                   else {id(t) for t in transactions})
        cands = []          # (gap, option index, stock index)
        rejected = []       # (option index, stock index)
        # A $0 close and a stock trade at the strike with the $1.00
        # charge whose quantities do not match one-to-one (2 contracts
        # vs two 100-share rows): named, never silently an expiry
        # (audit A2-0617).
        qty_mismatch = []   # (option index, stock index)
        meta = []
        for oi, opt in enumerate(expiries):
            m = re.match(r'^([A-Z.\d]+?)(\d{6})([CP])(\d{8})\.(\w+)$',
                         opt['symbol'])
            if not m:
                meta.append(None)
                continue
            under = opt.get('_under') or f"{m.group(1)}.{m.group(5)}"
            right, strike = m.group(3), int(m.group(4)) / 1000.0
            # The law the message cites follows the project's country
            # (re-audit A2-0723): ITA s.49(3) / s.49(3.1) in Canada,
            # Rev. Rul. 78-182 in the US, none without a country.
            _c = self.law('s.49(3)' if right == 'C' else 's.49(3.1)',
                          'Rev. Rul. 78-182')
            meta.append((strike, f" ({_c})" if _c else ""))
            contracts = abs(float(opt['quantity']))
            closed_short = float(opt['quantity']) > 0   # BUY at 0 closes a write
            # Short put / long call -> shares arrive (BUY);
            # short call / long put -> shares leave (SELL).
            want_buy = (right == 'P') == closed_short
            try:
                od = _d.fromisoformat(opt['date'])
            except ValueError:
                continue
            for i, t in enumerate(transactions):
                if t is opt or t['symbol'] != under:
                    continue
                if t.get('broker_account') != opt.get('broker_account'):
                    continue        # another Webull account's export
                q = float(t['quantity'])
                if (q > 0) != want_buy:
                    continue
                if abs(float(t['price']) - strike) > 0.005:
                    continue
                try:
                    gap = (_d.fromisoformat(t['date_settle']) - od).days
                except ValueError:
                    continue
                if not -1 <= gap <= 7:
                    continue
                exercise_fee = abs(abs(float(t.get('fee') or 0.0))
                                   - self._EXERCISE_FEE) <= 0.011
                if abs(abs(q) - contracts * 100) > 1e-6:
                    if exercise_fee:
                        qty_mismatch.append((oi, i))
                    continue
                if not exercise_fee:
                    rejected.append((oi, i))
                    continue
                cands.append((gap, oi, i))
        used_opt, used_stock = set(), set()
        for gap, oi, i in sorted(cands):
            if oi in used_opt or i in used_stock:
                continue
            used_opt.add(oi)
            used_stock.add(i)
            opt, stock = expiries[oi], transactions[i]
            opt['action'] = 'ASSIGN'
            stock['action'] = 'ASSIGN'
            if id(opt) in own_ids or id(stock) in own_ids:
                print(f"note: Webull {source}: inferred an exercise/"
                      f"assignment — {opt['symbol']} closed at $0 on "
                      f"{opt['date']} + {abs(float(stock['quantity'])):g} "
                      f"{stock['symbol']} at the strike {meta[oi][0]:g} "
                      f"settling {stock['date_settle']} (fee "
                      f"{float(stock.get('fee') or 0):.2f}); both legs "
                      f"booked ASSIGN (the premium folds into the shares' "
                      f"cost or proceeds{meta[oi][1]}). Check it against "
                      f"the statement.",
                      file=sys.stderr)
            # The shares are acquired/delivered ON the exercise, so the
            # stock leg's trade date is the option leg's date, stamped
            # just after it — both engines then see the option leg
            # stage the premium before the stock leg consumes it.
            stock['date'] = opt['date']
            stock['time'] = '16:00:01'
            # ...and the option leg settles with its stock leg: an
            # exercise or assignment takes its stock leg's date
            # (tax-logic CA-DATE-04 / US-DATE-04; IB's S058-01 twin).
            # On the expiry date alone an unrelated trade settling
            # between the two legs sorted inside the pair (audit
            # A2-1065 / A2-1071).
            opt['date_settle'] = stock['date_settle']
        for oi, i in rejected:
            if oi in used_opt or i in used_stock:
                continue
            opt, stock = expiries[oi], transactions[i]
            if not (id(opt) in own_ids or id(stock) in own_ids):
                continue
            print(f"warning: Webull {source}: {opt['symbol']} closed at "
                  f"$0 on {opt['date']} and "
                  f"{abs(float(stock['quantity'])):g} {stock['symbol']} "
                  f"traded at the strike {meta[oi][0]:g} settling "
                  f"{stock['date_settle']} with a "
                  f"{float(stock.get('fee') or 0):.2f} commission — an "
                  f"ordinary trade's fee, not Webull's $1.00 exercise/"
                  f"assignment charge, so exercise/assignment was NOT "
                  f"inferred: booked as an expiry plus a separate trade. "
                  f"If the statement shows an exercise/assignment, the "
                  f"premium belongs in the shares' cost or proceeds"
                  f"{meta[oi][1]} — see "
                  f"KNOWN_ISSUES 'Webull exercise/assignment inference'.",
                  file=sys.stderr)
        named = set()
        for oi, i in qty_mismatch:
            if oi in used_opt or i in used_stock or oi in named:
                continue
            opt, stock = expiries[oi], transactions[i]
            if not (id(opt) in own_ids or id(stock) in own_ids):
                continue
            named.add(oi)
            print(f"warning: Webull {source}: {opt['symbol']} "
                  f"({abs(float(opt['quantity'])):g} contract(s)) closed at "
                  f"$0 on {opt['date']} and {abs(float(stock['quantity'])):g} "
                  f"{stock['symbol']} traded at the strike {meta[oi][0]:g} "
                  f"settling {stock['date_settle']} with Webull's $1.00 "
                  f"exercise/assignment charge, but the quantities differ "
                  f"(the close or the stock leg is split across rows), so "
                  f"exercise/assignment was NOT inferred: booked as an "
                  f"expiry plus a separate trade. If the statement shows "
                  f"an exercise/assignment, the premium belongs in the "
                  f"shares' cost or proceeds{meta[oi][1]} — book it by hand (see "
                  f"KNOWN_ISSUES 'Webull exercise/assignment inference').",
                  file=sys.stderr)

    def _check_zero_closes(self, pool, expiries, source) -> None:
        """A $0 option row (no Price, no Proceeds) is booked only as a
        close: at its expiry, or earlier as the option leg of a paired
        exercise/assignment. A $0 row that OPENS a position (a write or
        a buy with no premium: before the expiry with nothing held, or
        on the side of what is held) is a cut or mangled row and is
        refused; a $0 close before the expiry that found no stock leg is
        named (audit A2-0284). At the expiry a row with nothing held in
        the data closes a position bought before the first export."""
        def key(t):
            return (t['date'], t['time'])
        for e in expiries:
            if e['action'] == 'ASSIGN':
                continue
            held = sum(float(t['quantity']) for t in pool
                       if t is not e and t['symbol'] == e['symbol']
                       and t.get('broker_account') == e.get('broker_account')
                       and key(t) <= key(e))
            q = float(e['quantity'])
            where = e.get('_where') or source
            exp = parse_option_expiry(e['symbol'])
            at_expiry = bool(exp) and e['date'] >= exp
            # At the expiry, nothing held in the data is a position
            # opened before the first export (missing history, not a
            # cut row); before it, or against a position on the same
            # side, a $0 row cannot be a close.
            if (abs(held) < 1e-9 and not at_expiry) \
                    or (abs(held) > 1e-9 and ((held > 0) == (q > 0)
                                              or abs(q) > abs(held) + 1e-9)):
                raise BrokerageParseError(
                    f"{where}: {'BUY' if q > 0 else 'SELL'} "
                    f"{abs(q):g} {e['symbol']} at $0 (no Price, no "
                    f"Proceeds) opens a position (held before it: "
                    f"{held:g}) — only a close at expiry or an "
                    f"exercise/assignment is booked at $0; a cut or "
                    f"mangled row? Refusing to book a premium-free "
                    f"{'write' if q < 0 else 'buy'}.")
            if exp and not at_expiry:
                print(f"warning: Webull {source}: {e['symbol']} closed at "
                      f"$0 on {e['date']}, before its expiry {exp}, and no "
                      f"stock leg at the strike was found — booked as an "
                      f"expiry (the premium is realized on its own). An "
                      f"early exercise/assignment folds the premium into "
                      f"the shares: check the statement.", file=sys.stderr)

    @staticmethod
    def _warn_ticker_changes(transactions, source, own=None) -> None:
        """Webull identifies a security by its Symbol column only. A
        ticker change with no reorganization row shows up as two
        symbols sharing one Security Description, the new one going
        SHORT — a phantom long plus a short (audit S066-10). The new
        symbol may open with the sale or with a small buy before a sale
        larger than it (audit A2-0290), and the old symbol may sit in an
        earlier yearly export (A2-1067 / A2-1070): `transactions` is
        every export of the folder, and a pair is named only when the
        row that takes the new symbol short is one of `own` (this
        file's rows), so it is named once. Name the pair; a dated ticker.map
        RENAME joins them."""
        own_ids = ({id(t) for t in own} if own is not None
                   else {id(t) for t in transactions})
        groups: Dict[tuple, Dict[str, List[Dict[str, Any]]]] = {}
        for t in sorted(transactions, key=lambda t: (t['date'],
                                                     t['time'])):
            desc = (t.get('description') or '').strip().upper()
            if not desc or is_option_symbol(t['symbol']):
                continue
            groups.setdefault((desc, t['currency'],
                               t.get('broker_account') or ''), {}
                              ).setdefault(t['symbol'], []).append(t)
        for (desc, _cur, _acct), syms in groups.items():
            if len(syms) < 2:
                continue
            ordered = list(syms.items())
            for (prev_sym, _rows), (sym, rows) in zip(ordered, ordered[1:]):
                held = 0.0
                for t in rows:
                    held += float(t['quantity'])
                    if held < -1e-9:
                        break
                else:
                    continue
                if id(t) not in own_ids:
                    continue
                # ATTENTION: on the run console (re-audit A2-0279; the
                # plain warning reached only the .sum).
                print(f"warning: ATTENTION: Webull {source}: {sym} goes "
                      f"short with "
                      f"a SALE on {t['date']} and shares the Security "
                      f"Description {desc!r} with {prev_sym} — likely a "
                      f"ticker change Webull reported without a "
                      f"reorganization row. If so, add the dated change "
                      f"`RENAME {prev_sym} {sym} {rows[0]['date']}` (the "
                      f"first {sym} row here; use the broker's change date "
                      f"if you know it) to ticker.map so both are one "
                      f"position (`taxjson renames`).",
                      file=sys.stderr)

    @staticmethod
    def _find_header(lines):
        """Locate the CSV header row in a Webull statement.

        Webull's export shape varies across years and account types:
          1. Single-line header with all three column markers
             (`Currency`, `Date`, `Action Code` on the same line) —
             the modern demo / 2024-25 format.
          2. Two-line header where `Currency` lives on one row and
             `Date`/`Action Code` on the next — some older exports.
          3. Headers that don't carry the literal "Action Code"
             column name at all — last-ditch we scan for the first
             data row containing a BUY/SELL action and return the
             line just before it.

        The parser's downstream row-filter (row[0] must be USD/CAD,
        row[2] must be BUY/SELL) skips any non-data row this
        heuristic accidentally selects, so a loose match is safe.
        A 0-tx parse of a non-empty file is also caught by
        taxjson-brokerage's per-file warning, which is the real
        safety net here.

        (Cycle-4 simplification dropped paths 2 and 3 — the strict
        single-line match returned -1 on Webull export variants that
        had only `Currency` and `Date` on the header line, silently
        producing 0 transactions until the per-file count surfaced
        the gap.)
        """
        # Strict: all three markers on one line (demo / current Webull).
        for i, line in enumerate(lines):
            if "Currency" in line and "Date" in line and "Action Code" in line:
                return i
        # Looser: split header across two physical lines.
        for i, line in enumerate(lines):
            if "Currency" in line and i + 1 < len(lines) and "Date" in lines[i + 1]:
                return i
        # Last-ditch: a data row tells us where the header was.
        for i, line in enumerate(lines):
            if ",BUY," in line or ",SELL," in line:
                return max(0, i - 1)
        return -1
