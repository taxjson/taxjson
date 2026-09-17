import csv
import re
import sys
from pathlib import Path
from typing import List, Dict, Any

from taxjson.lib.brokerages.base import BaseBrokerage, _parse_div_qty_rate, is_roc_description
from taxjson.lib.corp_actions import is_rbc_merger_row, is_rbc_cil_row


# Some RBC exports (notably ones round-tripped through Excel/pandas) ship the
# Date / Settlement Date columns as ISO datetimes — "2026-05-28 00:00:00" —
# rather than "January 30, 2025" / "01/30/2025". Without these, parse_date
# returned None and the builders fell back to the RAW string, leaking a
# " 00:00:00" suffix that the gains engine's `strptime(date, '%Y-%m-%d')`
# later choked on. Datetime form first so it fully consumes the time part.
_DATE_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%B %d, %Y", "%m/%d/%Y")

# Word-boundary match for dividend-class descriptions. `\b` keeps
# "Distribution" / "Dividend" while rejecting "Redistribution" etc.
_DIV_DESC_RE = re.compile(r'\b(?:Dividend|Distribution|Dist\.)', re.IGNORECASE)

# A forward/reverse stock split, booked by RBC as a 'Reorganization' row with
# the net shares received (or removed) in Quantity and "...STK SPLIT ON <base>
# SHS..." in the description, e.g.:
#   "DIS - VANGUARD ... GROWTH ETF STK SPLIT ON 14 SHS REC 04/17/26 ..."
_RBC_STK_SPLIT_RE = re.compile(r'\b(?:STK|STOCK|FORWARD|REVERSE)\s+SPLIT\b', re.I)
_RBC_SPLIT_ON_SHS_RE = re.compile(r'\bON\s+([\d,]+(?:\.\d+)?)\s+SHS\b', re.I)


def parse_rbc_date(date_str: str):
    """Module-level helper kept for back-compat with callers that import it.

    Raises ValueError on unparseable input rather than silently stamping
    `datetime.now()` — the old fallback would corrupt year filters and
    holding-period math in subtle ways. The class method `self.parse_date`
    (from BaseBrokerage) is the modern path and returns None on failure,
    letting RbcBrokerage skip-or-error per its own logic.
    """
    from datetime import datetime
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    raise ValueError(
        f"parse_rbc_date: could not parse {date_str!r} with formats {_DATE_FMTS}"
    )


class RbcBrokerage(BaseBrokerage):
    DEFAULT_ACCOUNT = "RBC"

    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
        start_idx = 0
        for i, line in enumerate(lines):
            if "Activity" in line and "Date" in line:
                start_idx = i
                break

        # First pass: learn each symbol's market currency from trade rows.
        # Same regression class as Questrade's BTO/B2GOLD: a Canadian
        # stock paying a USD dividend would otherwise get a .US suffix
        # from the dividend row's currency, splitting its identity from
        # the trades' .TO listing.
        # Blank trailer lines (RBC exports end with a few) parse as a
        # dict of empty strings, not an empty dict — they used to be
        # counted as `activity ?` skips in the summary. Not rows.
        rows = [r for r in csv.DictReader(lines[start_idx:])
                if not self._is_blank_row(r)]
        symbol_currency: Dict[str, str] = {}
        for row in rows:
            sym = (row.get('Symbol') or '').strip()
            cur = (row.get('Currency') or '').strip()
            if not sym or not cur:
                continue
            if not self._is_trade_like(row.get('Activity') or '', row.get('Description') or ''):
                continue
            symbol_currency.setdefault(sym, cur)

        transactions: List[Dict[str, Any]] = []
        merger_rows = 0
        self._rows_seen = 0
        for row in rows:
            self._rows_seen += 1
            activity = row.get('Activity') or ''
            desc = row.get('Description') or ''
            symbol = row.get('Symbol') or ''
            currency = row.get('Currency') or 'CAD'
            date_raw = row.get('Date') or ''
            dt = self.parse_date(date_raw, *_DATE_FMTS)
            date = dt.strftime("%Y-%m-%d") if dt else date_raw
            # `Amount` is net of commission; `Value` is the gross
            # qty*price notional. Use `Amount` so back_compute_fee can
            # recover the commission — falling back to `Value` only if
            # an export variant omits the `Amount` column.
            net = self.clean_number(row.get('Amount') or row.get('Value'))

            # Merger removal/receipt rows are owned by taxjson-corp-actions
            # (the election machinery), NOT the brokerage parser — emitting
            # them here as $0 trades is what produced the phantom-short +
            # $0-basis-acquirer bug. Skip them; the corp-actions stage (which
            # the run flow now invokes for RBC) resolves the merger properly.
            if is_rbc_merger_row(activity, desc):
                merger_rows += 1
                self.note_row_consumed()   # owned by taxjson-corp-actions
                continue

            # Cash-in-lieu of fractional shares is also a 'Reorganization'
            # row but carries no quantity — left to _is_trade_like it would
            # become an invalid 0-quantity BUYSELL. taxjson-corp-actions
            # folds it into the merger event (as the fractional disposition),
            # so skip it here.
            if is_rbc_cil_row(activity, desc):
                merger_rows += 1
                self.note_row_consumed()   # folded into the merger event
                continue

            # A stock split is a 'Reorganization' row too. Model it as a
            # SPLIT that scales the existing pool — NOT a $0-cost BUYSELL of
            # the received shares (which strands them at zero basis and
            # leaves the pre-split lot's cost orphaned).
            if self._is_stock_split(activity, desc):
                split_tx = self._build_stock_split(row, desc, symbol, currency, date)
                if split_tx:
                    self.note_row_consumed()
                    transactions.append(split_tx)
                    continue
                # Couldn't derive a ratio — fall through so the shares
                # aren't silently dropped (best-effort, still visible).

            if self._is_trade_like(activity, desc):
                self.note_row_consumed()
                tx = self._build_trade(row, activity, desc, symbol, currency, date, date_raw, net)
                if tx:
                    transactions.append(tx)
            elif is_roc_description(desc):
                self.note_row_consumed()
                # BEFORE the dividend branch: ROC rows arrive with
                # dividend-class activity labels but are ACB reductions,
                # not income.
                market_currency = symbol_currency.get(symbol, currency)
                transactions.append(self.tx_roc_adjust(
                    symbol=self.apply_currency_suffix(symbol, market_currency),
                    currency=currency, date=date, desc=desc, amount=net))
            elif self._is_dividend(activity, desc):
                self.note_row_consumed()
                # Use the trade's market currency for the suffix (when
                # we've seen this symbol traded); keep the dividend's
                # actual payment currency on the record.
                market_currency = symbol_currency.get(symbol, currency)
                transactions.extend(
                    self._build_dividend(symbol, currency, date, desc, net,
                                         suffix_currency=market_currency)
                )
            elif self._is_tax(activity, desc):
                self.note_row_consumed()
                # Same market-currency suffix as the dividend branch —
                # a USD-paid dividend on a .TO listing otherwise left
                # the TAX row on a phantom .US symbol, so per-symbol
                # dividend/tax pairing (merge2, sum-income NET, t1135
                # foreign classification) never matched them up.
                market_currency = symbol_currency.get(symbol, currency)
                transactions.append(self._build_tax(
                    symbol, currency, date, desc, net,
                    suffix_currency=market_currency))
            elif self._is_interest(activity, desc):
                self.note_row_consumed()
                transactions.append(self._build_interest(currency, date, desc, net))
            elif self._is_transfer(activity, desc, symbol):
                self.note_row_consumed()
                tx = self._build_transfer(row, desc, symbol, currency, date)
                if tx:
                    transactions.append(tx)
            else:
                # The elif ladder used to fall off the end silently — a
                # new RBC activity/description variant lost the row with
                # zero signal. Count it instead.
                self.count_skip(f"activity {activity.strip() or '?'!s}")
        # Parser-level disambiguation so a downstream `taxjson-sort --dedup`
        # can't collapse byte-identical split-fill rows. RBC's CSV rarely
        # collides at second precision but the protection is defensive
        # and free when there's nothing to mark.
        self.disambiguate_split_fills(transactions)
        self.emit_skip_summary(path.name)
        if merger_rows:
            print(
                f"note: skipped {merger_rows} RBC merger row(s) — these are "
                f"resolved by taxjson-corp-actions (--brokerage rbc_direct). "
                f"`taxjson run` invokes it when your country has election "
                f"rules (canada today); otherwise these share movements are "
                f"NOT recorded anywhere — enter them manually via a .tt "
                f"file.",
                file=sys.stderr,
            )
        return transactions

    # ----------------------------------------------------------- classifiers
    @staticmethod
    def _is_blank_row(row) -> bool:
        """True for an all-empty CSV line (or none at all). DictReader
        yields extra unnamed cells as a list under the None key."""
        if not row:
            return True
        for v in row.values():
            if isinstance(v, list):
                v = ''.join(x or '' for x in v)
            if (v or '').strip():
                return False
        return True

    @staticmethod
    def _is_trade_like(activity, desc):
        return any(x in activity for x in ('Buy', 'Sell', 'Reorganization', 'Exercise', 'Assignment')) or \
               any(x in desc for x in ('Buy', 'Sell', 'EXP', 'ASN', 'Expired', 'Assignment', 'EXPIRED'))

    @staticmethod
    def _is_stock_split(activity, desc):
        """A forward/reverse stock split booked as a 'Reorganization' row."""
        return ('Reorganization' in (activity or '')) and \
            bool(_RBC_STK_SPLIT_RE.search(desc or ''))

    @staticmethod
    def _is_dividend(activity, desc):
        if any(x in activity for x in ('Dividends', 'Distribution')):
            return True
        # Word-boundary match so "Redistribution" / "Redistributed"
        # don't get treated as dividend rows (the bare `'Dist' in desc`
        # substring match did). `Dist.` (the period abbreviation) still
        # matches because `\b` allows the trailing dot as a non-word
        # boundary.
        return bool(_DIV_DESC_RE.search(desc))

    @staticmethod
    def _is_tax(activity, desc):
        return 'Taxes' in activity or 'Tax' in desc

    @staticmethod
    def _is_interest(activity, desc):
        return 'Interest' in activity or 'Interest' in desc

    @staticmethod
    def _is_transfer(activity, desc, symbol):
        # RBC's in-kind security transfers use Activity 'Transfers' and carry
        # a Symbol + Quantity (e.g. "TFI - QUALCOMM INC DTC 8396"). Cash moves
        # ("Deposits & Contributions" / "Withdrawals & De-registrations") have
        # no symbol and aren't position events — only a symboled row counts.
        return 'Transfers' in activity and bool((symbol or '').strip())

    # --------------------------------------------------------------- builders
    def _build_stock_split(self, row, desc, symbol, currency, date):
        """A stock split → SPLIT scaling the existing pool by
        (base + received) / base, where `received` is the net shares
        moved (Quantity: positive for a forward split, negative for a
        reverse) and `base` is the pre-split count from "...ON <base> SHS".

        Returns None when the ratio can't be derived (no "ON N SHS" or a
        zero net quantity), so the caller can fall back rather than drop
        the row. A SPLIT scales whatever the pool holds, so if the
        pre-split lot predates the imported history the shares only
        materialize once that opening position is supplied (e.g. a manual
        .tt lot) — same as any other truncated-history position."""
        m = _RBC_SPLIT_ON_SHS_RE.search(desc or '')
        received = self.clean_number(row.get('Quantity'))
        if not m or abs(received) < 1e-9:
            return None
        base = float(m.group(1).replace(',', ''))
        factor = (base + received) / base if base > 0 else 0.0
        if factor <= 0:
            return None
        return {
            'action': 'SPLIT',
            'date': date, 'time': '09:30:00', 'date_settle': date,
            'symbol': self.apply_currency_suffix(symbol, currency),
            'symbol_new': '',
            'quantity': factor,
            'currency': currency,
            'price': 0.0,
            'net_amount': 0.0,
            'account': self.DEFAULT_ACCOUNT,
            'description': desc,
        }

    def _build_transfer(self, row, desc, symbol, currency, date):
        """An in-kind position transfer. RBC ships no book value (Value=0), so
        the transferred-in ACB is unknown — emitting the TRANSFER keeps the
        position math correct (the engine consumes it before the ACB pass) and
        the missing basis surfaces downstream as incomplete history, instead of
        the position silently looking like a phantom short. Gated by the
        --transfers flag at the taxjson-brokerage wrapper, like IB/Questrade."""
        qty = self.clean_number(row.get('Quantity'))
        if abs(qty) < 1e-9:
            return None
        # Direction: RBC usually signs an out-transfer negative, but if a
        # 'TFO'/'TFW'/out/deliver row arrives positive, flip it.
        du = desc.upper()
        if qty > 0 and (du.startswith(('TFO', 'TFW')) or 'TRANSFER OUT' in du
                        or 'DELIVER' in du):
            qty = -qty
        settle_raw = row.get('Settlement Date') or ''
        settle_dt = self.parse_date(settle_raw, *_DATE_FMTS)
        date_settle = settle_dt.strftime("%Y-%m-%d") if settle_dt else date
        return {
            'action': 'TRANSFER',
            'date': date, 'time': '09:30:00', 'date_settle': date_settle,
            'symbol': self.apply_currency_suffix(symbol, currency),
            'quantity': qty, 'currency': currency, 'price': 0.0,
            # Value column is the book value; RBC ships 0 for DTC transfers.
            'net_amount': abs(self.clean_number(row.get('Value') or row.get('Amount'))),
            'fee': 0.0,
            'account': self.DEFAULT_ACCOUNT, 'description': desc,
        }
    def _build_trade(self, row, activity, desc, symbol, currency, date, date_raw, net):
        opt = self.parse_option_from_description(desc)
        if opt:
            symbol = self.format_occ_symbol(opt['right'], opt['base'], opt['expiry'], opt['strike'])
        symbol = self.apply_currency_suffix(symbol, currency)

        settle_raw = row.get('Settlement Date') or ''
        settle_dt = self.parse_date(settle_raw, *_DATE_FMTS)
        if settle_dt:
            date_settle = settle_dt.strftime("%Y-%m-%d")
        elif opt:
            # Options settle T+1 in all eras.
            date_settle = self.settlement_date_t1(date_raw, "%m/%d/%Y")
        else:
            # Era- and market-aware fallback (T+2 pre-cutover equities).
            date_settle = self.equity_settlement_date(date_raw, currency,
                                                      "%m/%d/%Y")

        qty = self.clean_number(row.get('Quantity'))
        if 'Sell' in activity or 'Sell' in desc:
            qty = -abs(qty)
        price = self.clean_number(row.get('Price'))

        # Detect option ASSIGNMENT (NOT expiry). ASSIGN means the option leg
        # gets folded into an underlying stock event downstream; EXPIRY
        # realizes the premium gain/loss in place.
        is_option_symbol = bool(re.search(r'\d{6}[CP]\d+', symbol))
        action = 'BUYSELL'
        desc_upper = desc.upper()
        # The "ASSIGNMENT OF OPTION" pattern is the second regex in the base
        # class — we re-check it here to know whether the desc came from the
        # option-leg-notice variant (only that one means ASSIGN).
        desc_is_assignment_notice = bool(self._BASE_OPTION_PATTERNS[1].search(desc))
        if is_option_symbol and (
            any(x in activity for x in ('Assignment', 'Exercise'))
            or re.match(r'^\s*ASN\s*-\s*', desc_upper)
            or desc_is_assignment_notice
        ):
            action = 'ASSIGN'

        fee = self.back_compute_fee(qty, price, net, is_option=is_option_symbol)

        return {
            'action': action,
            'date': date,
            'time': '09:30:00',
            'date_settle': date_settle,
            'symbol': symbol,
            'quantity': qty,
            'currency': currency,
            'price': price,
            'fee': fee,
            'net_amount': abs(net),
            'gross_amount': self.theoretical_gross(qty, price, is_option=is_option_symbol),
            'account': self.DEFAULT_ACCOUNT,
            # Carry the security description so downstream tools (e.g. a
            # description-keyed --security-overrides rule) can correct a
            # mislabeled ticker. Dividend/tax/interest rows already do.
            'description': desc,
        }

    def _build_dividend(self, symbol, currency, date, desc, net, *, suffix_currency=None):
        # `currency` is the dividend's actual payment currency (stays on
        # the emitted record). `suffix_currency` is the symbol's market
        # currency derived from trade rows — wins for the .TO/.US suffix
        # so a cross-listed Canadian stock paying USD doesn't fragment
        # its ticker identity. Falls back to `currency` when we don't
        # have a trade-derived hint.
        symbol = self.apply_currency_suffix(symbol, suffix_currency or currency)
        is_net_tax = 'NON-RES TAX WITHHELD' in desc.upper()
        # Sign-preserving (schema convention): a reversal/correction row
        # arrives with a NEGATIVE Amount and must net against the
        # original posting — abs() booked it as MORE income. The
        # withholding gross-up below scales sign-preservingly too, so a
        # reversed net dividend also reverses its implied TAX row.
        gross_amount = net
        out: List[Dict[str, Any]] = []
        if is_net_tax:
            # RBC doesn't expose the treaty-specific withholding rate in
            # the CSV — only that withholding was applied. The 15%
            # estimate is the most common case (US withholding on
            # cross-border income) but real rates vary by treaty. See
            # memory:known_limitations.md.
            gross_amount = round(net / 0.85, 2)
            tax_withheld = round(gross_amount - net, 2)
        # Pull qty + per-share rate from the description so the emitted
        # DIVIDEND record is self-describing (qty × rate ≈ gross).
        # RBC's CSV usually carries only "ON N SHS", so `rate` is
        # back-computed from amount/qty when only the count is found.
        # Derivation uses GROSS: the description's stated per-share rate
        # is the DECLARED (pre-withholding) rate, so a net-of-tax row
        # derived against NET understated the position (a $1.00/sh
        # dividend netting $85 gave qty 85 for a 100-share holding) or,
        # with only "ON N SHS", stamped the net rate as `price`. For
        # rows without withholding gross == net, so this is unchanged.
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
        out.append({
            'action': 'DIVIDEND',
            'date': date, 'time': '09:30:00', 'date_settle': date,
            'symbol': symbol, 'quantity': qty, 'currency': currency,
            'price': rate,
            'net_amount': net, 'gross_amount': gross_amount,
            'type': 'dividend', 'account': self.DEFAULT_ACCOUNT,
            'description': desc,
        })
        return out

    def _build_tax(self, symbol, currency, date, desc, net,
                   suffix_currency=None):
        tax_symbol = (self.apply_currency_suffix(
            symbol, suffix_currency or currency) if symbol else 'UNKNOWN')
        return {
            'action': 'TAX',
            'date': date, 'time': '09:30:00', 'date_settle': date,
            'symbol': tax_symbol, 'quantity': 0.0, 'currency': currency,
            # RBC books a withholding charge as a NEGATIVE Amount (cash
            # out) and a refund/adjustment as POSITIVE. The repo-wide TAX
            # convention is positive = tax withheld, so flip the sign
            # rather than abs() it (mirrors ib_extractor's Withholding
            # Tax branch): a refund nets NEGATIVE instead of
            # double-counting as more tax paid.
            'net_amount': -net, 'type': 'tax',
            'account': self.DEFAULT_ACCOUNT, 'description': desc,
        }

    def _build_interest(self, currency, date, desc, net):
        return {
            'action': 'INTEREST',
            'date': date, 'time': '09:30:00', 'date_settle': date,
            'symbol': 'CASH', 'quantity': 0.0, 'currency': currency,
            'net_amount': net,  # keep sign for interest
            'type': 'interest', 'account': self.DEFAULT_ACCOUNT,
            'description': desc,
        }
