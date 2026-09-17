import csv
import io
import re
import sys
from pathlib import Path
from typing import List, Dict, Any

from taxjson.lib.brokerages.base import BaseBrokerage


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
        transactions: List[Dict[str, Any]] = []
        current_symbol = ""
        current_description = ""
        # Webull's parser only books BUY/SELL. Blank continuation rows are
        # expected and harmless, but a row with a REAL non-trade action
        # (dividend, option assignment/expiry, transfer) would be dropped
        # silently, leaving a phantom open position. Count those so the loss
        # is surfaced rather than invisible.
        skipped_actions: Dict[str, int] = {}

        for row in reader:
            if not row or len(row) < 3:
                continue
            currency = row[0].strip()
            if currency not in ('USD', 'CAD'):
                continue
            date_raw = row[1].strip()
            action_raw = row[2].strip()
            if not date_raw or action_raw not in ('BUY', 'SELL'):
                # A populated action we don't handle is a real dropped event;
                # an empty action is just a blank continuation row.
                if action_raw:
                    skipped_actions[action_raw] = skipped_actions.get(action_raw, 0) + 1
                continue

            symbol_raw = row[3].strip() if len(row) > 3 else ""
            description_raw = row[4].strip() if len(row) > 4 else ""
            qty_raw = row[6].strip() if len(row) > 6 else ""
            price_raw = row[7].strip() if len(row) > 7 else ""
            # 2024 format had Proceeds in col 8; 2025 added an empty col 8
            # and shifted Proceeds to col 9. Prefer 9, fall back to 8.
            proceeds_raw = row[9].strip() if len(row) > 9 else ""
            if not proceeds_raw and len(row) > 8:
                proceeds_raw = row[8].strip()

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

            qty = self.clean_number(qty_raw)
            price = self.clean_number(price_raw)
            net_amount = self.clean_number(proceeds_raw)
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
            trade_date = self.trade_date_from_settlement(
                date_str, currency, is_option, "%Y-%m-%d")
            transactions.append({
                'action': 'BUYSELL',
                'date': trade_date,
                'time': '09:30:00',
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
            })
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
