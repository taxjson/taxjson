import csv
import io
import re
import sys
from pathlib import Path
from typing import List, Dict, Any

from taxjson.lib.brokerages.base import (BaseBrokerage, BrokerageParseError,
                                         parse_strict_number)


# Webull's option descriptions run the ticker directly into the date with
# no separator: "CALL ABBV01/17/25 190". The base regex requires whitespace
# between the two — override it here.
_WEBULL_OPTION_RE = re.compile(
    r'(CALL|PUT)\s+([A-Z.\d]+?)\s*(\d{2}/\d{2}/\d{2})\s+([\d\.]+)', re.IGNORECASE
)


class WebullBrokerage(BaseBrokerage):
    DEFAULT_ACCOUNT = "WB"
    # Webull's CSVs only carry USD/CAD positions; anything else falls back to US.
    CURRENCY_EXT_MAP = {'USD': 'US', 'CAD': 'TO'}
    CURRENCY_EXT_FALLBACK = 'US'

    def _option_description_patterns(self):
        return (_WEBULL_OPTION_RE,)

    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        with open(path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        lines = content.splitlines()

        header_index = self._find_header(lines)
        if header_index == -1:
            return []

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
        current_description = ""
        # Webull's parser only books BUY/SELL. Blank continuation rows are
        # expected and harmless, but a row with a REAL non-trade action
        # (dividend, option assignment/expiry, transfer) would be dropped
        # silently, leaving a phantom open position. Count those so the loss
        # is surfaced rather than invisible.
        skipped_actions: Dict[str, int] = {}
        # Option expiry rows (see is_expiry below).
        expiries: List[Dict[str, Any]] = []

        def cell(row, key):
            j = cols.get(key)
            return row[j].strip() if j is not None and j < len(row) else ""

        width = len(header_row)
        for row in reader:
            if not row or len(row) < 3:
                continue
            # File line of this record (the header record is the first
            # one the reader returns; line_num counts physical lines
            # read so far, so a record's LAST physical line).
            where = f"{path.name} line {header_index + reader.line_num}"
            currency = cell(row, 'currency')
            if currency not in ('USD', 'CAD'):
                continue            # repeated page headers, preamble
            date_raw = cell(row, 'date')
            action_raw = cell(row, 'action')
            if not date_raw or action_raw not in ('BUY', 'SELL'):
                # A populated action we don't handle is a real dropped event;
                # an empty action is just a blank continuation row.
                if action_raw:
                    skipped_actions[action_raw] = skipped_actions.get(action_raw, 0) + 1
                continue

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
                    f"(two export layouts mixed in one file, or a hand "
                    f"edit); refusing to read Proceeds from the wrong "
                    f"column.")
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
                current_symbol = _new_symbol
            if description_raw:
                current_description = description_raw

            dt = self.parse_date(date_raw, "%d-%m-%Y")
            date_str = dt.strftime("%Y-%m-%d") if dt else date_raw

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
            # ("(1,352.97)"); read as NEGATIVE, and the trade convention
            # wants the magnitude (direction lives in the quantity
            # sign), so take abs() here.
            net_amount = abs(parse_strict_number(
                proceeds_raw, field='Proceeds', where=where,
                allow_blank=True, blank=0.0))
            qty = self.signed_quantity(qty, action_is_sell=(action_raw == 'SELL'))

            opt = self.parse_option_from_description(current_description)
            if opt:
                symbol = self.format_occ_symbol(opt['right'], opt['base'], opt['expiry'], opt['strike'])
                is_option = True
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
            }
            transactions.append(_tx)
            if is_expiry:
                expiries.append(_tx)
        self._mark_assignments(transactions, expiries)
        self.clamp_settlement_to_expiry(transactions, expiries)
        if len(transactions) != data_rows:
            # Row accounting: every dated BUY/SELL data row becomes one
            # transaction. A mismatch means a row was dropped or split.
            raise ValueError(
                f"{path}: Webull row accounting failed — {data_rows} "
                f"BUY/SELL rows but {len(transactions)} transactions")
        # Parser-level disambiguation so a downstream `taxjson-sort --dedup`
        # can't collapse byte-identical split-fill rows.
        self.disambiguate_split_fills(transactions)
        if skipped_actions:
            detail = ", ".join(f"{n}× {a}" for a, n in sorted(skipped_actions.items()))
            print(
                f"warning: Webull parser only books BUY/SELL — skipped "
                f"{sum(skipped_actions.values())} row(s) with unhandled "
                f"actions ({detail}). Dividends/option assignment/expiry are "
                f"NOT booked; verify no open position is left phantom.",
                file=sys.stderr,
            )
        return transactions

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
            for j, lab in enumerate(labels):
                if needle in lab and j not in cols.values():
                    # 'date' must not match 'Settlement date'-style
                    # labels of other columns; the Trading Summary's
                    # date column is labelled exactly "Date".
                    if key == 'date' and lab != 'date':
                        continue
                    cols[key] = j
                    break
        missing = [k for k in cls._REQUIRED if k not in cols]
        if missing:
            raise ValueError(
                f"{path}: unrecognised Webull export layout — could not "
                f"find column(s) {', '.join(missing)} in the header "
                f"{header!r}. Refusing to guess column positions.")
        return cols

    def _mark_assignments(self, transactions, expiries) -> None:
        """A Webull option closed at price 0 is an expiry — UNLESS shares
        of the underlying change hands at the strike within a few days in
        the matching quantity and direction: then it was ASSIGNED (short)
        or EXERCISED (long). Webull's Trading Summary shows both only as
        a $0 option close plus an ordinary stock trade at the strike.
        Booking it as an expiry realizes the premium as its own gain or
        loss instead of folding it into the shares' cost (ITA s.49(3);
        a holder's exercise adds the option cost to the shares). Mark both
        legs ASSIGN — the engine's two-row convention."""
        from datetime import date as _d
        used = set()
        for opt in expiries:
            m = re.match(r'^([A-Z.\d]+?)(\d{6})([CP])(\d{8})\.(\w+)$',
                         opt['symbol'])
            if not m:
                continue
            under = f"{m.group(1)}.{m.group(5)}"
            right, strike = m.group(3), int(m.group(4)) / 1000.0
            contracts = abs(float(opt['quantity']))
            closed_short = float(opt['quantity']) > 0   # BUY at 0 closes a write
            # Short put / long call -> shares arrive (BUY);
            # short call / long put -> shares leave (SELL).
            want_buy = (right == 'P') == closed_short
            try:
                od = _d.fromisoformat(opt['date'])
            except ValueError:
                continue
            best = None
            for i, t in enumerate(transactions):
                if i in used or t is opt or t['symbol'] != under:
                    continue
                q = float(t['quantity'])
                if (q > 0) != want_buy or abs(abs(q) - contracts * 100) > 1e-6:
                    continue
                if abs(float(t['price']) - strike) > 0.005:
                    continue
                try:
                    gap = (_d.fromisoformat(t['date_settle']) - od).days
                except ValueError:
                    continue
                if -1 <= gap <= 7 and (best is None or gap < best[0]):
                    best = (gap, i)
            if best is not None:
                used.add(best[1])
                opt['action'] = 'ASSIGN'
                stock = transactions[best[1]]
                stock['action'] = 'ASSIGN'
                # The shares are acquired/delivered ON the exercise, so the
                # stock leg's trade date is the option leg's date, stamped
                # just after it — both engines then see the option leg
                # stage the premium before the stock leg consumes it.
                stock['date'] = opt['date']
                stock['time'] = '16:00:01'

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
