import csv
import re
import sys
from pathlib import Path
from typing import List, Dict, Any

from taxjson.lib.brokerages.base import BaseBrokerage, _parse_div_qty_rate, is_roc_description


_DATE_FMT = "%Y-%m-%d %I:%M:%S %p"

# A stock-split distribution (Action DIS) reports the NEW shares in Quantity
# and the held count as "... ON <held> SHS ...". Used to recover the ratio.
_SPLIT_ON_SHS_RE = re.compile(r'\bON\s+([\d,]+(?:\.\d+)?)\s+SH', re.IGNORECASE)

# A STOCK DIVIDEND row (split-share corps like TDb pay non-cash share
# distributions): DIS / 'Dividends' with 'STK DIV' in the description
# and the DELIVERED share count in Quantity. Distinct from STK SPLIT
# (ratio-based) and from cash DIV rows (zero Quantity).
_CIL_RE = re.compile(r'CASH\s+IN\s+LIEU\s+OF\s+([0-9]*\.?[0-9]+)', re.I)
_REINV_PRICE_RE = re.compile(r'REINV@(?:[A-Z]{1,3}\$)?\s*([0-9]+(?:\.[0-9]+)?)', re.I)
_STK_DIV_RE = re.compile(r'\bSTK\.?\s+DIV\b|\bSTOCK\s+DIVIDEND\b',
                         re.IGNORECASE)

# Questrade emits internal codes like "S032771" or "A12345" for some
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

    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        # Read once into memory so we can do two passes: first to build a
        # description→(ticker, market-currency) map from trade rows,
        # second to emit transactions. This resolves two distinct
        # dividend symbol problems:
        #   - Internal codes like S032771 mapping back to the real ticker
        #     (e.g. SSL) when the trade describes the same security.
        #   - Cross-listing weirdness: Canadian companies dual-listed in
        #     the US sometimes emit dividends under one ticker (e.g.
        #     .BTO) while trades use the other (e.g. BTG). The trade's
        #     symbol+currency wins — the user's ticker.map then resolves
        #     the merged identity downstream.
        with open(path, 'r', encoding='utf-8-sig') as f:
            rows = list(csv.DictReader(f))

        # desc_key -> (clean_symbol, market_currency). Built from both Trade
        # AND Transfer rows: a position established purely by transfer-in
        # (typical for LIRA/RRSP carry-overs) still needs its market
        # identity learned somewhere. Trades are preferred (first-wins via
        # setdefault) since their symbol is the most reliable.
        desc_to_ticker: Dict[str, tuple] = {}

        def _record_ticker(row):
            sym = (row.get('Symbol') or '').strip().lstrip('.').rstrip()
            sym = re.sub(r'\.TO$', '', sym, flags=re.IGNORECASE)
            cur = (row.get('Currency') or '').strip() or 'USD'
            desc = row.get('Description') or ''
            key = _get_desc_key(desc)
            if key and sym and not _INTERNAL_CODE_RE.match(sym):
                desc_to_ticker.setdefault(key, (sym, cur))

        # Pass 1a: trades (highest-fidelity source).
        for row in rows:
            if (row.get('Activity Type') or '').strip() == 'Trades':
                _record_ticker(row)
        # Pass 1b: transfers (fills gaps for positions never traded).
        for row in rows:
            if (row.get('Activity Type') or '').strip() == 'Transfers':
                _record_ticker(row)

        transactions: List[Dict[str, Any]] = []
        self._rows_seen = 0
        for row in rows:
            if not row:
                continue
            self._rows_seen += 1
            action_raw = (row.get('Action') or '').strip()
            activity_type = (row.get('Activity Type') or '').strip()
            desc = row.get('Description') or ''
            currency = row.get('Currency') or 'USD'

            # Dispatch by Action/Activity Type. Questrade uses short
            # codes (DIV, TF6, Buy, Sell, EXP, ASN, EX) plus a longer
            # Activity Type label for context.

            # A stock split arrives as a DIS row tagged 'Dividends' with
            # 'STK SPLIT' in the description — must be checked BEFORE the
            # dividend branch, which would otherwise drop it as a $0 dividend
            # and lose the split shares entirely.
            if self._is_stock_split(action_raw, activity_type, desc):
                tx = self._parse_split(row, currency, desc, desc_to_ticker)
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
                qty = self.clean_number(row.get('Quantity'))
                if qty > 0:
                    dt = self.parse_date(
                        row.get('Transaction Date') or '', _DATE_FMT)
                    date = (dt.strftime('%Y-%m-%d') if dt
                            else (row.get('Transaction Date') or ''))
                    sym_raw = (row.get('Symbol') or '').strip().lstrip('.')
                    resolved = desc_to_ticker.get(_get_desc_key(desc))
                    scur = currency
                    if resolved and resolved[0]:
                        sym_raw, scur = resolved
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
                tx = self._parse_dividend(row, currency, desc, desc_to_ticker)
                if tx:
                    self.note_row_consumed()
                    transactions.append(tx)
                else:
                    # Zero-net informational row: was marked consumed
                    # with nothing emitted, which the lint reconciled
                    # but the skip summary never showed.
                    self.count_nonevent("zero-net dividend row "
                                        "(informational)")
                continue

            if action_raw == 'TF6' or activity_type == 'Transfers':
                tx = self._parse_transfer(row, currency, desc_to_ticker)
                if tx:
                    self.note_row_consumed()
                    transactions.append(tx)
                else:
                    self.count_nonevent("cash-only transfer journal")
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

            if action_raw == 'FCH' or activity_type == 'Fees and rebates':
                # Account/custody charges (ADR custody fee, ...). Was
                # an unclassified skip — the fee never reached the
                # fee totals.
                tx = self._parse_fee(row, currency, desc)
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
                legs = self._parse_cash_in_lieu(row, currency, desc,
                                                desc_to_ticker)
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
                # only in the description ("REINV@C$8.33966"). A plain
                # BUYSELL at that cost; the residual dividend cash
                # (dividend − cost) stays as cash. Was a counted skip —
                # DRIP shares never entered inventory.
                tx = self._parse_reinvestment(row, currency, desc,
                                              desc_to_ticker)
                if tx:
                    self.note_row_consumed()   # builder counts its skip
                    transactions.append(tx)
                continue

            is_trade = action_raw in ('Buy', 'Sell')
            is_expired = action_raw == 'EXP' or ' - EXPIRED' in desc.upper()
            is_assigned = (
                action_raw in ('ASN', 'EX')
                or 'ASSIGNMENT' in desc.upper()
                or 'EXERCISE' in desc.upper()
            )
            if not (is_trade or is_expired or is_assigned):
                # Previously a silent drop — a new Questrade action code
                # lost rows with zero signal. Count it; the summary at
                # the end of the parse names every dropped category.
                self.count_skip(f"action {action_raw or activity_type or '?'!s}")
                continue
            self.note_row_consumed()

            date_raw = row.get('Transaction Date') or ''
            dt = self.parse_date(date_raw, _DATE_FMT)
            date = dt.strftime("%Y-%m-%d") if dt else date_raw
            time = dt.strftime("%H:%M:%S") if dt else "00:00:00"

            settle_raw = row.get('Settlement Date') or ''
            settle_dt = self.parse_date(settle_raw, _DATE_FMT)
            if settle_dt:
                date_settle = settle_dt.strftime("%Y-%m-%d")
            else:
                # Era- and market-aware fallback (T+2 pre-cutover equities;
                # degraded path — the CSV normally carries the column).
                date_settle = self.equity_settlement_date(
                    date_raw, currency, _DATE_FMT, "%Y-%m-%d")

            qty = self.clean_number(row.get('Quantity'))
            if is_trade:
                qty = self.signed_quantity(qty, action_is_sell=(action_raw == 'Sell'))
            # EXP / ASN / EX rows: the CSV quantity is already signed to
            # close the open position — a long option expires or is
            # exercised with a NEGATIVE quantity, a short with a positive
            # one. signed_quantity() would force it positive and add a
            # phantom contract instead of netting the position to zero.
            price = self.clean_number(row.get('Price')) if not (is_expired or is_assigned) else 0.0
            gross = abs(self.clean_number(row.get('Gross Amount')))
            comm = abs(self.clean_number(row.get('Commission')))

            if is_expired or is_assigned:
                net = 0.0
                comm = 0.0
                gross = 0.0
            else:
                net = (gross + comm) if qty > 0 else (gross - comm)

            # Option symbol reconstruction from Description; fall back to
            # the bare Symbol column (which is often non-OCC like AAPL.OPT).
            opt = self.parse_option_from_description(desc)
            if opt:
                symbol = self.format_occ_symbol(opt['right'], opt['base'], opt['expiry'], opt['strike'])
            else:
                symbol = row.get('Symbol') or ''
            symbol = self.apply_currency_suffix(symbol, currency)

            transactions.append({
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
                'account': self.DEFAULT_ACCOUNT,
                # Carry the raw description so a description-keyed
                # --security-overrides rule can correct a mislabeled ticker —
                # IB/RBC/Webull trade rows already do; Questrade's was the gap.
                'description': desc,
            })
        self.disambiguate_split_fills(transactions)
        self.emit_skip_summary(path.name)
        return transactions

    def _parse_cash_in_lieu(self, row, currency, desc, desc_to_ticker):
        m = _CIL_RE.search(desc)
        frac = float(m.group(1)) if m else 0.0
        cash = abs(self.clean_number(row.get('Net Amount')))
        if frac <= 0 or cash <= 0:
            self.count_skip("CIL row with no fraction/cash")
            return []
        dt = self.parse_date(row.get('Transaction Date') or '', _DATE_FMT)
        date = dt.strftime('%Y-%m-%d') if dt else (row.get('Transaction Date') or '')
        sym_raw = (row.get('Symbol') or '').strip().lstrip('.')
        resolved = desc_to_ticker.get(_get_desc_key(desc))
        scur = currency
        if resolved and resolved[0]:
            sym_raw, scur = resolved
        sym = self.apply_currency_suffix(sym_raw, scur)
        base = {'date': date, 'date_settle': date, 'symbol': sym,
                'currency': currency, 'commission': 0.0,
                'account': self.DEFAULT_ACCOUNT, 'description': desc}
        return [
            {**base, 'action': 'BUYSELL', 'time': '09:30:00',
             'quantity': frac, 'price': 0.0, 'net_amount': 0.0,
             'gross_amount': 0.0},
            {**base, 'action': 'BUYSELL', 'time': '09:30:01',
             'quantity': -frac, 'price': round(cash / frac, 8),
             'net_amount': cash, 'gross_amount': cash},
        ]

    def _parse_fee(self, row, currency, desc):
        """FCH / 'Fees and rebates' row → FEE. Questrade signs Net
        Amount as cash (a charge NEGATIVE, a rebate POSITIVE); the repo
        FEE convention is positive = charged, so the sign flips. Bound
        to the ticker the description names ("# SHARES TKR") so the
        fee report can group it; CASH when it names none. None on a
        zero amount (nothing to book)."""
        net = self.clean_number(row.get('Net Amount'))
        if abs(net) < 1e-9:
            return None
        dt = self.parse_date(row.get('Transaction Date') or '', _DATE_FMT)
        date = dt.strftime('%Y-%m-%d') if dt else (row.get('Transaction Date') or '')
        time = dt.strftime('%H:%M:%S') if dt else '09:30:00'
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

    def _parse_reinvestment(self, row, currency, desc, desc_to_ticker):
        qty = abs(self.clean_number(row.get('Quantity')))
        net = abs(self.clean_number(row.get('Net Amount')))
        if qty <= 0 or net <= 0:
            self.count_skip("REI row with no shares/cost")
            return None
        dt = self.parse_date(row.get('Transaction Date') or '', _DATE_FMT)
        date = dt.strftime('%Y-%m-%d') if dt else (row.get('Transaction Date') or '')
        sdt = self.parse_date(row.get('Settlement Date') or '', _DATE_FMT)
        date_settle = sdt.strftime('%Y-%m-%d') if sdt else date
        m = _REINV_PRICE_RE.search(desc)
        price = float(m.group(1)) if m else round(net / qty, 8)
        sym_raw = (row.get('Symbol') or '').strip().lstrip('.')
        resolved = desc_to_ticker.get(_get_desc_key(desc))
        scur = currency
        if resolved and resolved[0]:
            sym_raw, scur = resolved
        return {
            'action': 'BUYSELL',
            'date': date, 'time': '09:30:00', 'date_settle': date_settle,
            'symbol': self.apply_currency_suffix(sym_raw, scur),
            'quantity': qty, 'currency': currency,
            'price': price, 'net_amount': net, 'gross_amount': net,
            'commission': 0.0,
            'account': self.DEFAULT_ACCOUNT,
            'description': desc,
        }

    @staticmethod
    def _is_stock_split(action_raw, activity_type, desc):
        """A forward stock split distribution. Questrade books it as a DIS /
        'Dividends' row with 'STK SPLIT'/'STOCK SPLIT' in the description. The
        marker is specific enough not to match a normal trade whose name
        mentions a due-bill split."""
        du = (desc or '').upper()
        return ('STK SPLIT' in du or 'STOCK SPLIT' in du) and (
            action_raw == 'DIS' or activity_type == 'Dividends')

    def _parse_split(self, row, currency, desc, desc_to_ticker):
        """Emit a SPLIT scaling the existing pool by (held + received)/held —
        a non-taxable share-count change, not a dividend. Questrade reports the
        NEW shares in Quantity, the held count as 'ON <held> SHS', and a temp
        internal Symbol (e.g. K012006); the real ticker is resolved from a
        trade of the same security (desc_to_ticker), so the SPLIT lands on the
        pool the user actually holds."""
        received = self.clean_number(row.get('Quantity'))
        m = _SPLIT_ON_SHS_RE.search(desc)
        held = float(m.group(1).replace(',', '')) if m else 0.0
        if not m or received == 0 or held <= 0:
            print(f"warning: Questrade stock-split row not understood "
                  f"(need 'ON N SHS' and a nonzero quantity), skipping: "
                  f"{desc!r}", file=sys.stderr)
            return None
        ratio = (held + received) / held

        key = _get_desc_key(desc)
        resolved = desc_to_ticker.get(key)
        symbol = resolved[0] if resolved else (row.get('Symbol') or '')
        if not resolved:
            print(f"warning: Questrade stock split: couldn't resolve a traded "
                  f"ticker for {symbol!r} ({desc!r}); the SPLIT may not apply "
                  f"to the right pool — add a ticker.map rule if needed.",
                  file=sys.stderr)
        symbol = self.apply_currency_suffix(symbol, currency)

        date_raw = row.get('Transaction Date') or ''
        dt = self.parse_date(date_raw, _DATE_FMT)
        date = dt.strftime("%Y-%m-%d") if dt else date_raw
        time = dt.strftime("%H:%M:%S") if dt else "00:00:00"
        return {
            'action': 'SPLIT',
            'date': date,
            'time': time,
            'date_settle': date,
            'symbol': symbol,
            'symbol_new': '',
            'quantity': ratio,
            'currency': currency,
            'price': 0.0,
            'net_amount': 0.0,
            'account': self.DEFAULT_ACCOUNT,
            'description': desc,
        }

    def _parse_dividend(self, row, currency, desc, desc_to_ticker):
        """Questrade DIV row: cash dividend on a security. Net Amount holds
        the dividend value (currency-of-the-row); Gross is typically 0.

        Symbol resolution is delicate. Questrade emits the dividend's
        symbol independently of the trade rows, which causes two issues:
          1. Internal codes like 'S032771' for securities that were
             transferred in (no native Questrade trade history).
          2. Cross-listing weirdness: e.g. a US-listed B2Gold (BTG) holding
             whose dividend rows reference the Canadian listing (.BTO)
             but are paid in USD. Using the dividend row's own currency
             for the suffix would yield BTO.US — wrong on both axes.

        Resolution: if the dividend's cleaned description matches a trade
        row's, use the TRADE'S symbol and currency for the emitted
        symbol (which the user's ticker.map then folds into the
        canonical identity). Currency field on the transaction stays
        as the dividend's own currency so the user knows how it was paid.
        """
        # Sign-preserving (schema convention): the Dividends activity
        # type also carries NON-RES TAX WITHHELD debits, reversals, and
        # ROC reversals as NEGATIVE Net Amounts. abs() booked all of
        # those as positive income (and a ROC reversal as a second ACB
        # reduction); keeping the sign lets downstream summing net them
        # out and honors tx_roc_adjust's signed-amount contract.
        net = self.clean_number(row.get('Net Amount'))
        if abs(net) < 1e-9:
            return None  # informational row with no cash flow
        date_raw = row.get('Transaction Date') or ''
        dt = self.parse_date(date_raw, _DATE_FMT)
        date = dt.strftime("%Y-%m-%d") if dt else date_raw
        time = dt.strftime("%H:%M:%S") if dt else "09:30:00"
        symbol_raw = (row.get('Symbol') or '').strip().lstrip('.')
        suffix_currency = currency

        # Always consult the desc map when we have any of:
        #   - internal-code symbol (e.g. S032771)
        #   - bare symbol that mismatches the trades' identifier (e.g. .BTO
        #     vs trades' BTG)
        # We can't tell at parse time whether the symbol mismatches, so we
        # do the lookup unconditionally and prefer the trade's identity
        # when one exists. No match → fall through with the row's values.
        resolved = desc_to_ticker.get(_get_desc_key(desc))
        if resolved:
            trade_sym, trade_currency = resolved
            # Use the trade's symbol+currency only if they actually differ
            # from the dividend's — otherwise the suffix calc is the same.
            if trade_sym and (trade_sym != symbol_raw or trade_currency != currency):
                symbol_raw = trade_sym
                suffix_currency = trade_currency

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

    def _parse_transfer(self, row, currency, desc_to_ticker):
        """Questrade TF6 row: asset transfer in or out. Quantity is signed
        (+ for transfer-in, − for transfer-out).

        Two fields need recovering from elsewhere:
          - Symbol. The TF6 Symbol column is an internal Questrade code
            (e.g. R223608), so the real ticker is looked up from a trade
            row of the same security via desc_to_ticker (keyed on the
            company description). Without this the transferred position
            lands under e.g. R223608.US instead of O.US.
          - Cost basis. The TF6 Net Amount column is 0; Questrade puts
            the transferred-in book value (ACB) in the description as
            "TRANSFER BOOK VALUE <amount>". It's parsed from there.

        taxjson-brokerage's --transfers flag controls whether these
        reach the gains engine."""
        qty_signed = self.clean_number(row.get('Quantity'))
        if abs(qty_signed) < 1e-9:
            return None  # cash-only journal, not a security transfer
        desc = row.get('Description') or ''
        date_raw = row.get('Transaction Date') or ''
        dt = self.parse_date(date_raw, _DATE_FMT)
        date = dt.strftime("%Y-%m-%d") if dt else date_raw
        time = dt.strftime("%H:%M:%S") if dt else "09:30:00"

        symbol_raw = (row.get('Symbol') or '').strip().lstrip('.').replace(' ', '.')
        suffix_currency = currency
        resolved = desc_to_ticker.get(_get_desc_key(desc))
        if resolved and resolved[0]:
            symbol_raw, suffix_currency = resolved
        elif symbol_raw and _INTERNAL_CODE_RE.match(symbol_raw):
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
        net = abs(self.clean_number(row.get('Net Amount')))
        m = _BOOK_VALUE_RE.search(desc)
        if m:
            try:
                net = float(m.group(1).replace(',', ''))
            except ValueError:
                pass
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
        }
