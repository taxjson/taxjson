"""Base class for brokerage parsers.

Every brokerage parser subclasses BaseBrokerage and implements parse_file.
Shared logic — OCC option symbol reconstruction, currency suffix mapping,
fee back-compute, T+1 settlement, robust date parsing — lives here as
helpers so each parser file stays small and focused on its CSV's quirks.

Designed so an AI-generated parser for a new brokerage can produce a short
subclass that delegates to these helpers instead of re-implementing them.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple


def encode_occ_strike(strike) -> str:
    """Encode an option strike as the 8-digit OCC thousandths field.

    Uses Decimal, NOT float: `int(float("4.02") * 1000)` evaluates to
    4019 (the float 4.02 is actually 4.01999999…), not 4020 — mis-encoding
    roughly half of all sub-$200 cent-precision strikes and fragmenting an
    option's identity / cost basis across two symbols. `ticker_map.py`
    already fixed this on the ticker.map side; this is the same fix for the
    broker parsers. `strike` may be a float-as-string or a number."""
    return f"{int(Decimal(str(strike).strip()) * 1000):08d}"


# Return-of-capital marker, shared by every parser so the classification
# can't drift per broker. ROC is NOT dividend income: it reduces the
# position's ACB (emitted as an ADJUST row with negative net_amount via
# `BaseBrokerage.tx_roc_adjust`). Word-boundary so e.g. "RETURNED CAPITAL
# GAINS DISTRIBUTION" phrasing doesn't false-positive.
ROC_DESC_RE = re.compile(r'\bRET(?:URN)?\s+OF\s+CAPITAL\b', re.IGNORECASE)


def is_roc_description(desc: Optional[str]) -> bool:
    return bool(ROC_DESC_RE.search(desc or ''))


# Shared dividend-description parsers. Real broker dividend rows include
# the share count and/or per-share rate as plain English inside the
# Description column; extracting them lets us populate `quantity` and
# `price` on the emitted DIVIDEND record so it self-describes (qty held
# at record date × per-share rate ≈ amount). Falls back to None when
# the pattern doesn't match so the caller can default to zeros.
_DIV_QTY_ON_SHS_RE = re.compile(
    r'\bON\s+([\d,]+(?:\.\d+)?)\s+SH(?:S|RS|ARES)?\b',
    re.IGNORECASE,
)
_DIV_PER_SHARE_RE = re.compile(
    # "USD 0.24 per Share" / "$0.50 PER SHR" / "0.09 per share" — the
    # currency prefix is optional because we don't actually need it
    # here (the row already carries currency).
    r'(?:[A-Z]{3}\s+|\$)?([\d.]+)\s*PER\s*SH(?:R|ARE)?\b',
    re.IGNORECASE,
)
_DIV_CASH_DIVIDEND_RE = re.compile(
    # IB also ships dividend rows whose rate has no "per Share" suffix:
    # "Cash Dividend CAD 0.97 (Ordinary Dividend)". The per-share figure
    # always sits right after "Cash Dividend <CCY>", so anchor on that
    # phrase — matching a bare "<CCY> <number>" anywhere would catch the
    # ISIN or other stray digits.
    r'\bCash\s+Dividend\s+(?:[A-Z]{3}\s+|\$)([\d.]+)',
    re.IGNORECASE,
)


def _parse_div_qty_rate(description: str, amount: float):
    """Return `(quantity, price)` for a dividend row.

    quantity: shares held at the record date (from "ON N SHS" pattern).
    price:    per-share dividend rate (from "X per Share" pattern, or
              derived from amount/quantity when only the count appears).

    Either or both default to 0.0 when the description is silent, in
    which case the emitting record falls back to the historical
    "qty=0, price=0" shape and downstream consumers unchanged.
    """
    qty = 0.0
    rate = 0.0
    m_qty = _DIV_QTY_ON_SHS_RE.search(description or '')
    if m_qty:
        try:
            qty = float(m_qty.group(1).replace(',', ''))
        except ValueError:
            qty = 0.0
    m_rate = _DIV_PER_SHARE_RE.search(description or '')
    if m_rate:
        try:
            rate = float(m_rate.group(1))
        except ValueError:
            rate = 0.0
    if rate == 0.0:
        # IB "Cash Dividend CAD 0.97" form — a rate without "per Share".
        m_cash = _DIV_CASH_DIVIDEND_RE.search(description or '')
        if m_cash:
            try:
                rate = float(m_cash.group(1))
            except ValueError:
                rate = 0.0
    # Derive missing field from the other when we have one + amount.
    if qty > 0 and rate == 0 and amount:
        rate = round(amount / qty, 8)
        # The paid amount is rounded to CENTS, so back-computing
        # manufactures spurious precision: 37 sh paid $20.54 yields
        # 0.55516129 for a dividend actually declared at 0.555 — and
        # the SAME payment in another account, from a broker whose
        # statement states the rate, showed a clean 0.555. Snap to
        # the FEWEST decimals that still explain the paid amount
        # within the half-cent tolerance the quantity snap below
        # uses. A genuinely fine-grained rate (0.0375 on a big
        # position) survives because no shorter form reproduces the
        # cash.
        # Only snap when the shorter form is UNAMBIGUOUS: at small
        # share counts the half-cent cash tolerance admits several
        # candidates, and blindly taking round(quotient, d) picked a
        # NEIGHBOUR of the declared rate (3 sh of a 0.555 dividend
        # gave 0.557) — and could even replace an exact quotient with
        # a worse one (4 sh at 0.0375 -> 0.037). Requiring the
        # neighbours at that precision to fail keeps the raw quotient
        # whenever the data genuinely cannot resolve the rate. At small
        # share counts the shortest consistent form can still be
        # SHORTER than the declared rate (7 sh paying $0.26 fits both
        # 0.037 and a declared 0.0375) — nothing in the data
        # distinguishes them, and the shorter form at least claims no
        # precision the cash cannot back.
        tol = 0.005 + 1e-9
        for _places in range(0, 9):
            cand = round(rate, _places)
            if abs(amount - cand * qty) > tol:
                continue
            step = 10.0 ** -_places
            if any(abs(amount - (cand + d * step) * qty) <= tol
                   for d in (-1, 1)):
                continue                       # ambiguous at this width
            rate = cand
            break
    elif rate > 0 and qty == 0 and amount:
        qty = round(amount / rate, 8)
        # The statement's amount is rounded to CENTS, so the division
        # lands NEAR the true share count, not on it (25 sh x 0.271 =
        # 6.775 -> paid 6.77 -> derived 24.98154982). When the nearest
        # integer count explains the paid amount to within the
        # half-cent rounding IB applies, snap to it. A genuinely
        # fractional DRIP position differs by more than the tolerance
        # unless it's within half a cent of the whole-share payout —
        # in which case the integer is the better estimate anyway.
        nearest = round(qty)
        if nearest > 0 and abs(amount - nearest * rate) <= 0.005 + 1e-9:
            qty = float(nearest)
    return qty, rate


class BaseBrokerage:
    # 100 for options (each contract = 100 shares); 1 for equities/crypto.
    OPTION_MULTIPLIER = 100

    # Currency → ticker-suffix mapping for the apply_currency_suffix helper.
    # Subclasses can override with a narrower or different mapping.
    CURRENCY_EXT_MAP: Dict[str, str] = {
        'CAD': 'TO',
        'USD': 'US',
        'AUD': 'AX',
        'GBP': 'L',
    }

    # Default fallback when CURRENCY_EXT_MAP doesn't contain the currency.
    # Use 'US' to match Webull's behavior; subclasses use this for unknown
    # currencies — most brokerages echo the currency code itself instead.
    CURRENCY_EXT_FALLBACK: Optional[str] = None

    # Default account label used when the source CSV doesn't name an account.
    # The taxjson-brokerage CLI overrides this via --account-name regardless.
    DEFAULT_ACCOUNT: str = "Unknown"

    def __init__(self) -> None:
        # Skipped-row accounting. A row the parser cannot classify must be
        # COUNTED, never silently dropped — the 0-transactions safety net
        # only catches total parser failure, not partial drop (a new
        # broker action code losing rows with zero signal is exactly how
        # taxable events go missing). `_rows_seen`/`_rows_consumed` stay
        # None/0 for parsers that don't do row-level accounting; the ones
        # that do let `taxjson-brokerage --lint` reconcile
        # rows_seen == consumed + skipped exactly.
        self._skip_counts: Dict[str, int] = {}
        self._rows_seen: Optional[int] = None
        self._rows_consumed: int = 0

    def count_skip(self, category: str) -> None:
        self._skip_counts[category] = self._skip_counts.get(category, 0) + 1

    def note_row_consumed(self) -> None:
        self._rows_consumed += 1

    # Skip-category prefix for rows the parser RECOGNIZES as non-events
    # (a fiat deposit, an FX conversion, a statement-metadata section).
    # They still count toward the lint reconciliation, but the summary
    # lists them in their own quieter note instead of the "if any of
    # these are trades/income, the parser needs a new branch" call to
    # action — that wording is for rows the parser could NOT classify.
    KNOWN_NONEVENT_PREFIX = 'non-event '

    def count_nonevent(self, category: str) -> None:
        """count_skip for a row the parser RECOGNIZES as a non-event
        (subtotal, FX conversion, fiat deposit, metadata). Still part
        of the lint reconciliation; reported in the calmer note."""
        self.count_skip(self.KNOWN_NONEVENT_PREFIX + category)

    def emit_skip_summary(self, source_name: str) -> None:
        """One stderr note per parse listing what was dropped and why.
        Quiet when nothing was skipped. Recognized non-events (see
        KNOWN_NONEVENT_PREFIX) get a separate, calmer line."""
        if not self._skip_counts:
            return
        import sys
        pfx = self.KNOWN_NONEVENT_PREFIX
        known = {c: n for c, n in self._skip_counts.items()
                 if c.startswith(pfx)}
        unknown = {c: n for c, n in self._skip_counts.items()
                   if not c.startswith(pfx)}
        if known:
            total = sum(known.values())
            detail = ", ".join(f"{cat[len(pfx):]}: {n}" for cat, n in
                               sorted(known.items()))
            print(f"note: {source_name}: {total} recognized non-event "
                  f"row(s) not translated — {detail}.", file=sys.stderr)
        if unknown:
            total = sum(unknown.values())
            detail = ", ".join(f"{cat}: {n}" for cat, n in
                               sorted(unknown.items()))
            print(f"note: {source_name}: skipped {total} unclassified "
                  f"row(s) — {detail}. If any of these are trades/income, "
                  f"the parser needs a new branch (see CONTRIBUTING).",
                  file=sys.stderr)

    def parse_file(self, path: Path) -> List[Dict[str, Any]]:
        raise NotImplementedError

    # ---------------------------------------------------------------- options

    # The two regex shapes the brokerages produce:
    #   "CALL AAPL 06/20/25 150.00"           — Questrade/Webull/RBC variant
    #   "ASN - CALL .QQZ 06/20/25 30 QQZ HOLDINGS"   — RBC option-leg notification
    #   "ASSIGNMENT OF OPTION ... CALL ..."   — RBC stock-leg notification
    # The base regex captures the common skeleton; subclasses can add their
    # own variants by overriding _option_description_patterns.
    _BASE_OPTION_PATTERNS = (
        re.compile(
            r'^(?:EXP\s*-\s*|ASN\s*-\s*)?(CALL|PUT)\s+\.?([A-Z0-9\s\.]+?)\s+(\d{1,2}/\d{1,2}/\d{2})\s+([\d\.]+)',
            re.IGNORECASE,
        ),
        re.compile(
            r'ASSIGNMENT OF OPTION.*?(CALL|PUT)\s+\.?([A-Z0-9\s\.]+?)\s+(\d{1,2}/\d{1,2}/\d{2})\s+([\d\.]+)',
            re.IGNORECASE,
        ),
    )

    def _option_description_patterns(self) -> Tuple[re.Pattern, ...]:
        return self._BASE_OPTION_PATTERNS

    def parse_option_from_description(self, desc: str) -> Optional[Dict[str, str]]:
        """Extract (right, base_ticker, expiry, strike) from a brokerage's
        free-text option description. Returns None if nothing looks like an
        option string."""
        if not desc:
            return None
        for pat in self._option_description_patterns():
            m = pat.search(desc)
            if m:
                right_str, base, expiry_raw, strike_raw = m.groups()
                return {
                    'right': 'P' if right_str.upper() == 'PUT' else 'C',
                    'base': base.strip().lstrip('.').rstrip('.').replace(' ', '.'),
                    'expiry': expiry_raw,
                    'strike': strike_raw,
                }
        return None

    def format_occ_symbol(self, right: str, base: str, expiry: str, strike: str) -> str:
        """Build an OCC-style option symbol: BASE{YYMMDD}{C/P}{strike*1000:08d}.

        Accepts the expiry as "MM/DD/YY" (two-digit year). Strike may be a
        float-as-string."""
        m, d, y = expiry.split('/')
        expiry_date = f"{y}{int(m):02d}{int(d):02d}"
        return f"{base}{expiry_date}{right}{encode_occ_strike(strike)}"

    # ----------------------------------------------------------- currency ext

    _CURRENCY_SUFFIX_RE = re.compile(r'\.(TO|US|AX|L)$', re.IGNORECASE)

    def apply_currency_suffix(self, symbol: str, currency: str) -> str:
        """Normalize a symbol and append a currency-derived suffix.

        Strips any existing .TO/.US/.AX/.L, replaces spaces with dots, then
        appends the suffix from CURRENCY_EXT_MAP. Unknown currencies fall
        back to CURRENCY_EXT_FALLBACK if set, otherwise echo the currency
        code itself."""
        if not symbol:
            return symbol
        sym = symbol.replace(' ', '.')
        # Questrade spells TSX-Venture listings `.VN`; the live-position
        # side (qt_position_symbol) and the rest of the pipeline use
        # `.V`. Without normalizing here, a Venture trade parsed to the
        # junk symbol `ABC.VN.TO` (the trailing `.TO` from the CAD
        # suffix passed schema validation) while verify/sanity said
        # `ABC.V` — a guaranteed phantom mismatch and a fragmented
        # identity no default ticker.map folds.
        if sym.upper().endswith('.VN'):
            return f"{sym[:-3]}.V"
        if sym.upper().endswith('.V') and currency.upper() == 'CAD':
            return sym[:-2] + '.V'
        sym = self._CURRENCY_SUFFIX_RE.sub('', sym)
        ext = self.CURRENCY_EXT_MAP.get(currency, self.CURRENCY_EXT_FALLBACK or currency)
        return f"{sym}.{ext}"

    def tx_roc_adjust(self, *, symbol: str, currency: str, date: str,
                      desc: str, amount: float,
                      account: Optional[str] = None) -> Dict[str, Any]:
        """One shared shape for a return-of-capital row: an ADJUST that
        REDUCES ACB by the cash received (net_amount = -amount), tagged
        type='roc' so `taxjson roc`/`roc-sum` can report it. `amount` is
        signed cash received — positive for a normal ROC posting, negative
        for a broker reversal row (which then nets out as a positive
        ADJUST). ROC must NOT be booked as dividend income: that
        double-errs (income overstated now, ACB overstated → gains
        understated at sale)."""
        return {
            'action': 'ADJUST',
            'date': date, 'time': '09:30:00', 'date_settle': date,
            'symbol': symbol, 'quantity': 0.0, 'currency': currency,
            'net_amount': -amount, 'gross_amount': 0.0, 'type': 'roc',
            'account': account or getattr(self, 'DEFAULT_ACCOUNT', 'PORTFOLIO'),
            'description': desc,
        }

    # ----------------------------------------------------------- sign / coerce

    @staticmethod
    def signed_quantity(qty: float, action_is_sell: bool) -> float:
        """Convention: buys positive, sells negative. The cost-basis tracker
        keys off this sign — flipping it silently corrupts gain/loss."""
        return -abs(qty) if action_is_sell else abs(qty)

    @staticmethod
    def clean_number(raw: str, default: float = 0.0) -> float:
        """Parse a CSV numeric. Strips commas, currency symbols, and any
        surrounding parentheses. Treats parenthesized values as magnitude,
        not signed negatives — that matches the existing tax-output
        convention (sign comes from the action/qty, not from CSV
        formatting). Returns default on failure."""
        if raw is None or raw == '':
            return default
        s = str(raw).strip()
        if not s:
            return default
        s = s.replace(',', '').replace('$', '').replace('€', '').replace('£', '')
        s = s.replace('(', '').replace(')', '')
        try:
            return float(s)
        except ValueError:
            return default

    # ------------------------------------------------------- fee back-compute

    def back_compute_fee(
        self, qty: float, price: float, net_amount: float, is_option: bool,
        *, min_fee: float = 0.005, sanity_ratio: float = 0.25,
    ) -> float:
        """For brokerages whose CSV doesn't break out fees, infer the fee from
        |qty * price * multiplier - net|.

        Two guards: sub-cent residuals are noise and become 0; residuals
        larger than `sanity_ratio` of |net| are almost certainly a
        units/parsing artifact (e.g. wrong contract multiplier) and also
        become 0 rather than emit a fictitious number."""
        multiplier = self.OPTION_MULTIPLIER if is_option else 1
        theoretical_gross = abs(qty) * price * multiplier
        implicit = abs(theoretical_gross - abs(net_amount))
        if implicit < min_fee:
            return 0.0
        if abs(net_amount) > 1e-9 and implicit > sanity_ratio * abs(net_amount):
            return 0.0
        return round(implicit, 4)

    def theoretical_gross(self, qty: float, price: float, is_option: bool) -> float:
        multiplier = self.OPTION_MULTIPLIER if is_option else 1
        return round(abs(qty) * price * multiplier, 4)

    # -------------------------------------------------------- date / settle

    @staticmethod
    def parse_date(raw: str, *formats: str) -> Optional[datetime]:
        """Try each format in order; return the first that parses. None if
        none match. Strips surrounding whitespace."""
        if not raw:
            return None
        s = raw.strip()
        for fmt in formats:
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def disambiguate_split_fills(transactions: List[Dict[str, Any]]) -> None:
        """Mark the second+ instance of identical-looking rows in this
        file as split fills. Some brokerages emit multiple CSV rows for
        one order that gets filled in pieces at the same price and time;
        they're physically distinct trades that produce byte-identical
        CSV rows. Without this, the deterministic id() hash collapses
        them to one entry under taxjson-sort --dedup.

        Mutates transactions in place. Modifies only the 2nd+ occurrence
        so the first instance keeps its original (stable) id.
        """
        seen: Dict[tuple, int] = {}
        for tx in transactions:
            key = (
                tx.get('date'), tx.get('time'), tx.get('symbol'),
                tx.get('action'), tx.get('quantity'),
                tx.get('price'), tx.get('net_amount'),
                tx.get('account'),
            )
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                existing = tx.get('description') or ''
                tx['description'] = f"{existing} [fill #{seen[key]}]".strip()

    def settlement_date_t1(self, date_str: str, *formats: str) -> str:
        """Add one business day to the given trade date. Used by brokerages
        whose CSV doesn't carry a settlement-date column.

        formats lists strptime patterns to try; if none parse, the input
        string is returned unchanged so callers can degrade gracefully on
        malformed dates."""
        dt = self.parse_date(date_str, *formats) if formats else None
        if dt is None:
            return date_str
        added = 0
        curr = dt
        while added < 1:
            curr += timedelta(days=1)
            if curr.weekday() < 5:
                added += 1
        return curr.strftime("%Y-%m-%d")

    def trade_date_from_settlement(self, settle_str: str,
                                   currency: str = 'USD',
                                   is_option: bool = False,
                                   *formats: str) -> str:
        """BACK-compute the trade date from a settlement date, for brokerages
        whose CSV date column IS the settlement date (Webull, per user
        verification against real statements). Options T+1 in all eras;
        equities T+2 before the T+1 cutover (US 2024-05-28 / CA 2024-05-27),
        T+1 after. Weekends skipped backwards; exchange holidays are NOT
        modeled (documented limitation). Era selection keys off the settle
        date — ambiguous only in the 1-2 day window around the cutover."""
        dt = self.parse_date(settle_str, *formats) if formats else None
        if dt is None:
            return settle_str
        iso = dt.strftime("%Y-%m-%d")
        cutover = ('2024-05-27' if (currency or '').upper() == 'CAD'
                   else '2024-05-28')
        days = 1 if is_option else (2 if iso < cutover else 1)
        removed = 0
        curr = dt
        while removed < days:
            curr -= timedelta(days=1)
            if curr.weekday() < 5:
                removed += 1
        return curr.strftime("%Y-%m-%d")

    def equity_settlement_date(self, date_str: str, currency: str = 'USD',
                               *formats: str) -> str:
        """Era-aware EQUITY settlement for brokerages whose CSV carries no
        settlement column: T+2 before the T+1 cutover, T+1 after, weekends
        skipped (exchange holidays are NOT modeled — documented limitation).
        The cutover is market-specific: US 2024-05-28; Canada 2024-05-27 (a
        TSX trading day — the US was closed for Memorial Day)."""
        dt = self.parse_date(date_str, *formats) if formats else None
        if dt is None:
            return date_str
        # The era/lag rule lives in lib/dates (shared with the wash
        # radar's rescue-deadline walk-back) — one copy, no drift.
        from taxjson.lib.dates import settlement_date
        return settlement_date(dt.strftime("%Y-%m-%d"), currency)
