#!/usr/bin/env python3
"""
taxjson_t1135.py

CRA Form T1135 (Foreign Income Verification Statement) helper.

Answers the two questions every Canadian filer with foreign securities has:

  1. Do I need to file T1135 this year?  The test is COST-based: total cost
     amount of all specified foreign property (SFP) exceeding CAD $100,000
     at ANY time in the year (ITA 233.3). This tool runs the gains engine
     over the full transaction history of the taxable accounts in base
     currency, reads each property's cost through the year from the
     engine's pools, and reports the maximum total.

  2. If yes, what goes on the form?  Per foreign property: maximum cost
     amount during the year, cost amount at year end, income (dividends /
     payments in lieu, and the interest an INTEREST row naming the
     property pays), and gain (loss) on disposition — plus per-country
     aggregates for the "held with a Canadian registered securities dealer"
     category-7 style of reporting.

Inputs are the pipeline's TAXABLE `<account>_base.json` files (full history,
already converted to base currency) and, for the income / gain columns, the
year-scoped `<account>_gains.json` (or `_gains_wash.json`) files. Registered
accounts (RRSP/TFSA/...) are excluded from SFP by law — do not pass them as
FILEs; `--sheltered` gives them as superficial-loss context only.

Cost amount is the ACB the gains engine computes: one full-history engine
pass, and its cost timeline (each pool's quantity and cost after every
row that changed it, lib/cost_timeline; backlog TJ-0417) is read day by
day — every superficial loss denied in any year, every option premium and
warrant cost, where the engine applied it.

Domicile classification is by market suffix (.US → USA, .L → GBR,
.AX → AUS; .TO/.V/.CN/.NE → Canadian, i.e. not SFP), overridable per symbol
via `T1135 SYMBOL COUNTRY [CATEGORY]` lines in the project's ticker.map;
each property's form category (1-6) is 6 for an option, a futures
contract or crypto and 2 for any other listed security unless a line
gives it:

    #     symbol  country  (ISO-3 code, or CA/CAN/CANADA/EXCLUDE to exclude)
    T1135 SAMPLW.US  CA       # interlisted Canadian corp held on NYSE — not SFP
    T1135 GLXY.TO USA      # foreign corp listed on TSX — still SFP
    T1135 SAMPLT.US USA 4  # a US trust's units: category 4

Caveats printed with every report (also see --help):
  - Amounts are COST (ACB-style). That is the correct basis for the filing
    threshold and for the "maximum cost amount" columns; the category-7
    detailed method wants month-end FAIR MARKET VALUE, which needs price
    data this tool does not fetch.
  - Symbols with no market suffix (typically exchange-held crypto) are
    bucketed as country `CRYPTO` and counted toward the threshold —
    crypto held on a foreign exchange is generally SFP; check where it
    is held and map it (`T1135 SYMBOL <ISO3>` or `T1135 SYMBOL CA`)
    in ticker.map.

  - The verdict is on these books alone: specified foreign property
    held outside them (a foreign bank account or cash, shares held in
    certificate form, a foreign rental property, ...) counts toward the
    same $100,000 and is not seen — add its cost by hand.
  - Before Dec 31 the figures run only to the last date in the books:
    a "below the threshold" verdict is provisional until the year ends.

Usage:
    taxjson-t1135 --year 2025 margin_base.json crypto_base.json \\
        --gains margin_gains.json --gains crypto_gains.json \\
        [--sheltered sheltered_base.json] [--option-premium-timing grant] \\
        [--map ticker.map] [--threshold 100000] [--json]

Or through the project wrapper (recommended):  `taxjson t1135`
"""

from taxjson.lib.out import unwrapped as _unwrapped
from taxjson.lib.stage_msg import emit_line
import argparse
import contextlib
import io
import re
import sys
from decimal import Decimal
from pathlib import Path
from taxjson.lib.cli_diag import guard_main, tax_year
from taxjson.lib.futures import is_plain_future
from taxjson.lib.numeric import positive_float_arg
from taxjson.lib.report_model import fmt_money
from taxjson.bin.taxjson_convert_currency import norm_currency
from typing import Any, Dict, List, Optional, Tuple

# Filing threshold: ITA 233.3(1) "reporting entity" — SFP total cost more
# than $100,000 CAD at any time in the year requires the form. The
# simplified (Part A) vs detailed (Part B) split at $250,000 (reached at
# any time in the year -> Part B) comes from the CRA Form T1135
# instructions, not the Act (S052-16).
FILING_THRESHOLD = 100_000.0
DETAILED_THRESHOLD = 250_000.0

# Market suffix → ISO-3166 alpha-3 country of the exchange. This is the
# 90% heuristic: domicile (what T1135 cares about) usually matches the
# listing exchange for the retail case. Interlisted exceptions are
# ticker.map T1135 lines.
# The market data's one venue table (lib/markets); a Canadian exchange
# is None — not specified foreign property (.VN was a '??' REVIEW here,
# audit A2-1077).
from taxjson.lib.markets import (known_suffixes as _known_sfx,  # noqa: E402
                                 suffix_country as _sfx_country)
_SUFFIX_COUNTRY: Dict[str, Optional[str]] = {
    _s: (None if _sfx_country(_s) == "CAN" else _sfx_country(_s))
    for _s in _known_sfx()}

# Actions that never move a position or its cost. Mirrors the engines'
# non-capital skip list (core.py) minus ADJUST/SPLIT/OPENING_BALANCE which
# we do consume.
_NON_CAPITAL = ("DIVIDEND", "DIVIDEND_IN_LIEU", "TAX", "INTEREST", "FEE",
                "TRANSFER")


# Sentinel country code for symbols we cannot classify (an unknown market
# suffix). Deliberately ugly so it reads as "review me".
REVIEW = "??"

# The override COUNTRY vocabulary (S051-16) is lib/t1135_country's.
# Bucket for suffix-less symbols — equity parsers always stamp a market
# suffix, so these are crypto. Crypto held on a foreign exchange is
# generally specified foreign property (funds/intangibles held outside
# Canada); where it is held decides the country, which the books don't
# carry — so it is counted toward the threshold and flagged for review.
CRYPTO = "CRYPTO"

# The form's six categories of specified foreign property (its Part B):
# each property row carries one (tax-logic CA-RPT-18).
CATEGORY_NAMES = {
    1: "funds held outside Canada",
    2: "shares of non-resident corporations",
    3: "indebtedness owed by non-residents",
    4: "interests in non-resident trusts",
    5: "real property outside Canada",
    6: "other property outside Canada",
}


# ---------------------------------------------------------------- loading

# Thin alias so existing importers (tests, taxjson_reconcile_slips)
# keep working; the implementation is the shared report-layer loader.
def load_json(path: Path) -> Any:
    # The shared work/ contract (lib/json_input): a wrong-shape book or
    # gains file is a ValueError naming it, not a traceback (S042-18).
    from taxjson.lib.json_input import read_work_doc
    return read_work_doc(path)


def load_transactions(paths: List[Path],
                      phantoms: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every row of the base files. With `phantoms` (the project's
    the project folder of the .tt OPENING cost=unknown lines; the parameter keeps its pre-rename name) each
    file first gets the missing-history OPENING_BALANCE rows the gains
    stage synthesizes for it (missing_history.
    synthesize_openings, per book like pipeline.prepare_books) — without
    them a sale with cut-off history opened a fake short that the next
    real purchases covered at zero cost, so their cost never reached the
    max-cost or Dec-31 columns (R1-321)."""
    txs: List[Dict[str, Any]] = []
    ph = None
    if phantoms is not None:
        from taxjson.lib.missing_history import load_missing_history
        ph = load_missing_history(phantoms)
    for p in paths:
        try:
            raw = load_json(p)
            if isinstance(raw, dict) and "transactions" not in raw:
                # Read as an empty book: 'no T1135 required' (S033-01).
                raise ValueError(f"{p}: no 'transactions' list — not a "
                                 f"taxjson base book")
        except ValueError as e:
            # A truncated / wrong-shape base book: one line, not a
            # traceback (S042-18). OSError keeps guard_main's message.
            from taxjson.lib.cli_diag import error as _error
            _error("taxjson-t1135", str(e))
            raise SystemExit(2)
        rows = raw.get("transactions", []) if isinstance(raw, dict) else raw
        rows = [t for t in rows if isinstance(t, dict)]
        if ph:
            from taxjson.lib.core import coerce_transaction_row
            from taxjson.lib.missing_history import synthesize_openings
            objs = [coerce_transaction_row(t, i, str(p))
                    for i, t in enumerate(rows)]
            objs, _log = synthesize_openings(objs, ph)
            rows = [o.to_dict() for o in objs]
        txs.extend(rows)
    return txs


def load_overrides(path: Optional[Path]) -> Dict[str, Optional[str]]:
    """The T1135 domicile overrides of a ticker.map: its `T1135 SYMBOL
    COUNTRY` lines as {SYMBOL: ISO3 code, or None for "not foreign
    property" (CA/CAN/CANADA/EXCLUDE)} — the vocabulary of the old
    t1135.map (lib/t1135_country). A BOM is fine; symbols are
    upper-cased (R1-212).

    A T1135 line that cannot be read (a COUNTRY outside the vocabulary
    — S051-16 —, a wrong shape, one symbol given two countries) or a
    line with no keyword (an old t1135.map line pasted in as is) stops
    the tool naming it: the old file skipped such a line with a warning
    and the symbol silently kept its listing-suffix country, which can
    flip the filing verdict. Other lookup lines' problems are `taxjson
    run`'s to refuse; they do not stop T1135."""
    if path is None:
        return {}
    from taxjson.lib.cli_diag import InputContentError
    from taxjson.lib.t1135_country import override_value
    from taxjson.lib.ticker_map import read_side_rules
    rules = read_side_rules(path)
    if rules.t1135_problems:
        raise InputContentError(_overrides_problem(Path(path),
                                                   rules.t1135_problems))
    return {sym: override_value(code) for sym, code in rules.t1135.items()}


def load_categories(path: Optional[Path]) -> Dict[str, int]:
    """The T1135 categories of a ticker.map's `T1135 SYMBOL COUNTRY
    CATEGORY` lines, {SYMBOL: 1-6}. load_overrides has refused a map
    whose T1135 lines cannot be read."""
    if path is None:
        return {}
    from taxjson.lib.ticker_map import read_side_rules
    return dict(read_side_rules(path).t1135_category)


# side_rules_from_text's no-keyword problem: its place, the line.
_NO_KEYWORD_RE = re.compile(r"^(?P<where>\S+:\d+): line has no ticker\.map "
                            r"keyword \([^)]*\): (?P<line>.*)$")


def _overrides_problem(path: Path, problems: List[str]) -> str:
    """The one-line refusal of a ticker.map whose T1135 lines cannot be
    read: the first problem by its line (`ticker.map:54`, not the
    absolute path), what to write, `taxjson migrate` only when an old
    t1135.map is there to convert — it was named for every problem, and
    then said "nothing to migrate" (backlog TJ-0083)."""
    first = problems[0]
    more = len(problems) - 1
    m = _NO_KEYWORD_RE.match(first)
    if m:
        words = m["line"].strip("'\"").split()
        head = f"{m['where']}: {m['line']} has no ticker.map keyword"
        fix = ("a T1135 override is a `T1135 SYMBOL COUNTRY` line"
               + (f" (`T1135 {' '.join(words)}`)" if len(words) == 2
                  else "")
               + "; another line: fix it or delete it")
    else:
        head = first
        fix = "T1135 overrides are `T1135 SYMBOL COUNTRY` lines"
    if (path.parent / "t1135.map").exists():
        fix += " (`taxjson migrate` converts the old t1135.map)"
    return (head + (f" (and {more} more)" if more else "") + " — " + fix)


# ---------------------------------------------------------------- classify

def classify_country(symbol: str,
                     overrides: Dict[str, Optional[str]]) -> Optional[str]:
    """Country code for a symbol, or None when it is not specified foreign
    property (Canadian, or user-excluded). Options classify by their own
    market suffix — the OCC symbol carries it (SAMPLG250117C00150000.US)."""
    if symbol in overrides:
        return overrides[symbol]
    if "." in symbol:
        suffix = symbol.rsplit(".", 1)[1].upper()
        if suffix in _SUFFIX_COUNTRY:
            return _SUFFIX_COUNTRY[suffix]
        return REVIEW
    # No suffix: equities parsers always stamp one, so this is almost
    # certainly crypto (exchange-held crypto is generally SFP per CRA).
    return CRYPTO


def foreign_isin_symbols(rows: List[Dict[str, Any]],
                         overrides: Dict[str, Optional[str]],
                         user_keys: Any = ()) -> Dict[str, str]:
    """{symbol: the issuer's country (ISO alpha-3, else as the ISIN
    gives it)} of the shares a Canadian listing classifies as not
    foreign property whose rows carry another country's ISIN (IB
    stamps `issuer_country` on a dividend's or a return of capital's
    row): a non-resident corporation's shares are specified foreign
    property wherever they are listed (tax-logic CA-RPT-02; backlog
    TJ-0455). A symbol a ticker.map T1135 line decides (`user_keys`)
    is the user's, never named."""
    from taxjson.lib.core import is_option_symbol
    from taxjson.lib.t1135_country import iso3_of
    out: Dict[str, str] = {}
    for t in rows:
        ic = str(t.get("issuer_country") or "").strip().upper()
        sym = str(t.get("symbol") or "")
        if (not ic or ic in ("CA", "CAN") or not sym or "." not in sym
                or sym in out or sym in user_keys
                or is_option_symbol(sym) or is_plain_future(sym)
                or classify_country(sym, overrides) is not None):
            continue
        out[sym] = iso3_of(ic) or ic
    return out


def property_category(symbol: str, categories: Dict[str, int],
                      kinds: Dict[str, str]) -> Tuple[Optional[int], str]:
    """A property's T1135 category and where it comes from (tax-logic
    CA-RPT-18): the ticker.map line's (`T1135 SYMBOL COUNTRY CATEGORY`);
    6 for crypto (no market suffix, as classify_country reads it), an
    option or a futures contract; none for a security the interlisted
    master records as units or a fund (`kinds`, lib/tobase_map.
    t1135_kinds), named for the user to set; else 2, a listed security
    taken as a corporation's shares."""
    from taxjson.lib.core import is_option_symbol
    if symbol in categories:
        return categories[symbol], "ticker.map"
    if "." not in symbol:
        return 6, "crypto"
    if is_option_symbol(symbol) or is_plain_future(symbol):
        return 6, "derivative"
    if symbol in kinds:
        return None, kinds[symbol]
    return 2, "listing"


# ---------------------------------------------------------------- custody

# The pool key of an account's cash in one currency (lib/t1135_cash;
# tax-logic CA-RPT-20): no listing suffix, no symbol of the books.
CASH_PREFIX = "CASH:"


def cash_key(currency: str, account: str) -> str:
    return f"{CASH_PREFIX}{str(currency).upper()}:{account}"


def load_cash(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    """The `--cash FILE` document (lib/t1135_cash.cash_doc), None
    without one; one that cannot be read stops the tool naming it."""
    if path is None:
        return None
    import json as _json
    from taxjson.lib import t1135_cash as _TC
    from taxjson.lib.cli_diag import InputContentError
    try:
        doc = _json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise InputContentError(f"{path}: the T1135 cash document cannot "
                                f"be read ({e})")
    bad = _TC.check(doc)
    if bad:
        raise InputContentError(f"{path}: {bad}")
    return doc


class Custody:
    """A Canadian issuer's shares held with a custodian outside Canada
    (an account's `custodian_country` in taxjson.toml, --custodian):
    specified foreign property, category 6, under the custodian's
    country (tax-logic CA-RPT-02, CA-RPT-18; backlog TJ-0369). The
    same symbol held in Canada is not.

    The engine pools identical shares over every taxable account (s.47),
    so such an account's part of a pool of a symbol that is not foreign
    property (a Canadian listing, or a `T1135 SYMBOL CA` line) is its
    share of the pooled ACB: its own units that day (`units`, from its
    books as `taxjson list --date` apportions a pool) times the pool's
    cost per unit, under a key of its own, SYMBOL@ACCOUNT (`keys`:
    {key: (symbol, account, country)}; walk_costs). Options and futures
    keep their symbols."""

    SEP = "@"

    def __init__(self, custody: Optional[Dict[str, Optional[str]]],
                 overrides: Dict[str, Optional[str]],
                 rows: List[Dict[str, Any]] = ()) -> None:
        self.custody = {str(a): c for a, c in (custody or {}).items() if c}
        self.overrides = overrides
        self.keys: Dict[str, Tuple[str, str, str]] = {}
        # The cash rows (tax-logic CA-RPT-20, lib/t1135_cash): {key:
        # (currency, account, country)}, and the coins whose rows are
        # an account's US-dollar cash ({(account, COIN): key}: a USD
        # stablecoin's reward is that cash's income, CA-CRYPTO-02).
        self.cash_rows: Dict[str, Tuple[str, str, str]] = {}
        self.cash_coins: Dict[Tuple[str, str], str] = {}
        self._rows = list(rows)
        self._books: Dict[str, List[Dict[str, Any]]] = {}
        # (account, symbol, basis) -> (the rows that move those units,
        # their days); (account, symbol, day, basis) -> units.
        self._held: Dict[Tuple[str, str, str],
                         Tuple[List[Dict[str, Any]], List[str]]] = {}
        self._units: Dict[Tuple[str, str, str, str], float] = {}

    def canadian(self, symbol: str) -> bool:
        """A share whose holding in Canada is not foreign property."""
        from taxjson.lib.core import is_option_symbol
        return (bool(symbol) and "." in symbol
                and classify_country(symbol, self.overrides) is None
                and not is_option_symbol(symbol)
                and not is_plain_future(symbol))

    def key(self, symbol: str, account: str) -> str:
        k = f"{symbol}{self.SEP}{account}"
        self.keys[k] = (symbol, account, self.custody[account])
        self.overrides[k] = self.custody[account]
        return k

    def key_of(self, symbol: str, account: str) -> str:
        """The pool key of `symbol` held in `account` (a USD stablecoin
        whose account's cash is a row: that row's)."""
        ck = self.cash_coins.get((account, str(symbol).upper()))
        if ck:
            return ck
        if account in self.custody and self.canadian(symbol):
            return self.key(symbol, account)
        return symbol

    def cash(self, currency: str, account: str, country: str) -> str:
        """The key of `account`'s cash in `currency`, a row under
        `country` (CRYPTO or ISO alpha-3)."""
        k = cash_key(currency, account)
        self.cash_rows[k] = (currency, account, country)
        self.overrides[k] = country
        return k

    def symbol(self, key: str) -> str:
        if key in self.cash_rows:
            return f"{self.cash_rows[key][0]} cash"
        return self.keys[key][0] if key in self.keys else key

    # The rows that move an account's units (as balance_on reads them).
    MOVES = ("BUYSELL", "ASSIGN", "OPENING_BALANCE", "TRANSFER", "SPLIT")

    def book(self, account: str) -> List[Dict[str, Any]]:
        """The rows that move `account`'s units: its own, and another
        account's split or rename of a Canadian issuer its own books do
        not carry (a .tt RENAME is recorded once; a merger's
        per-account ratio is the account's own row)."""
        if account in self._books:
            return self._books[account]
        from datetime import date as _d
        from taxjson.lib.corporate_timeline import (SPLIT_DATE_WINDOW_DAYS,
                                                    normalize_symbol_new)

        def _event(t: Dict[str, Any]) -> Tuple[str, str]:
            sym = str(t.get("symbol") or "")
            return sym, normalize_symbol_new(sym, t.get("symbol_new"))

        def _day(t: Dict[str, Any]) -> Optional[Any]:
            try:
                return _d.fromisoformat(str(t.get("date") or "")[:10])
            except ValueError:
                return None
        moves = [t for t in self._rows
                 if (t.get("action") or "").upper() in self.MOVES]
        own = [t for t in moves if str(t.get("account") or "") == account]
        own_splits: Dict[Tuple[str, str], List[Any]] = {}
        for t in own:
            if (t.get("action") or "").upper() == "SPLIT":
                own_splits.setdefault(_event(t), []).append(_day(t))
        book = list(own)
        for t in moves:
            if (str(t.get("account") or "") == account
                    or (t.get("action") or "").upper() != "SPLIT"
                    or not self.canadian(str(t.get("symbol") or ""))):
                continue
            d = _day(t)
            if any(o is None or d is None
                   or abs((o - d).days) <= SPLIT_DATE_WINDOW_DAYS
                   for o in own_splits.get(_event(t), ())):
                continue                # the account's own copy
            book.append(dict(t, account=account))
        self._books[account] = book
        return book

    def _held_rows(self, account: str, symbol: str, basis: str
                   ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """The rows of `account`'s book that can move its units of
        `symbol` (those of the symbol and of every symbol a rename led
        to it from), and their days on `basis`."""
        hk = (account, symbol, basis)
        if hk not in self._held:
            book = self.book(account)
            names = {symbol}
            grew = True
            while grew:
                grew = False
                for t in book:
                    if ((t.get("action") or "").upper() == "SPLIT"
                            and str(t.get("symbol_new") or "") in names
                            and str(t.get("symbol") or "") not in names):
                        names.add(str(t.get("symbol") or ""))
                        grew = True
            rows = [t for t in book if str(t.get("symbol") or "") in names]
            self._held[hk] = (rows, sorted({_tx_date(t, basis)
                                            for t in rows
                                            if _tx_date(t, basis)}))
        return self._held[hk]

    def units(self, account: str, symbol: str, date: str,
              basis: str = "settle") -> float:
        """`account`'s units of `symbol` at the end of `date` (on
        `basis`), from its books (taxjson_apply_distributions.
        balance_on, as taxjson-split-gains apportions a pooled
        inventory)."""
        from bisect import bisect_right
        from taxjson.bin.taxjson_apply_distributions import balance_on
        rows, days = self._held_rows(account, symbol, basis)
        i = bisect_right(days, date) - 1
        if i < 0:
            return 0.0
        # The units change only on a row's date: the last one on or
        # before `date` stands for it.
        uk = (account, symbol, days[i], basis)
        if uk not in self._units:
            self._units[uk] = balance_on(rows, symbol, days[i],
                                         date_basis=basis)
        return self._units[uk]

    def change_dates(self, basis: str = "settle") -> Dict[str, set]:
        """{date: the Canadian symbols a custody account's rows move
        that day} — a move between your own accounts leaves the pool as
        it was, yet changes the account's share of it."""
        out: Dict[str, set] = {}
        for acct in self.custody:
            for t in self.book(acct):
                d = _tx_date(t, basis)
                for s in (t.get("symbol"), t.get("symbol_new")):
                    if d and s and self.canadian(str(s)):
                        out.setdefault(d, set()).add(str(s))
        return out


# ---------------------------------------------------------------- cost walk

class _Pool:
    __slots__ = ("symbol", "qty", "cost", "tainted", "max_cost_in_year")

    def __init__(self, symbol: str = "") -> None:
        self.symbol = symbol
        self.qty = 0.0
        self.cost = Decimal(0)
        self.tainted = False           # missing-history OPENING_BALANCE — unknown ACB
        self.max_cost_in_year = 0.0

    def eps(self, *scales: float) -> float:
        """The quantity under which this pool is flat: the engine's own
        tolerance (core.pool_qty_eps) — 1e-6 for shares and options;
        for a coin only float noise, since a holding of a millionth of
        a coin is property with a cost amount (2026-10 filing audit F5:
        the share epsilon dropped it from the T1135)."""
        from taxjson.lib.core import pool_qty_eps
        return pool_qty_eps(self.symbol, self.qty, *scales)

    def cost_amount(self) -> float:
        """Cost amount of PROPERTY HELD: a short position is a liability,
        not property, so only long cost counts."""
        if self.qty <= self.eps():
            return 0.0
        return max(float(self.cost), 0.0)


class _Ev:
    """Attribute view of a base-book row for corporate_timeline's
    event_sort_key."""
    __slots__ = ("action", "date", "date_settle", "time", "quantity",
                 "symbol")

    def __init__(self, tx: Dict[str, Any]) -> None:
        self.action = (tx.get("action") or "").upper()
        self.date = tx.get("date") or ""
        self.date_settle = tx.get("date_settle") or ""
        self.time = tx.get("time") or "00:00:00"
        try:
            self.quantity = float(tx.get("quantity") or 0.0)
        except (TypeError, ValueError):
            self.quantity = 0.0
        self.symbol = tx.get("symbol") or ""


def _tx_date(tx: Dict[str, Any], tax_date: str = "settle") -> str:
    if tax_date == "trade":
        return tx.get("date") or tx.get("date_settle") or ""
    return tx.get("date_settle") or tx.get("date") or ""


def _sort_key(tx: Dict[str, Any], tax_date: str = "settle") -> Tuple:
    """The Canada engine's own ladder (ca_main): at one stamp an
    assignment's option leg precedes its stock leg, trades keep the
    book's row order (the export's, CA-DATE-14), and a settle-lagged
    execution precedes a SPLIT on its settle date — the same order the
    engine walked (S008-05)."""
    from taxjson.lib.corporate_timeline import event_sort_key
    if tax_date == "trade":
        return event_sort_key(_Ev(tx), profile="ca_main",
                              date_of=lambda t: t.date or t.date_settle)
    return event_sort_key(_Ev(tx), profile="ca_main")


def _engine_timeline(transactions: List[Dict[str, Any]],
                     tax_date: str = "settle") -> Dict[str, Any]:
    """The Canada engine's cost timeline of `transactions` (base-book
    rows, one book of the taxable accounts): one full-history run, no
    registered context. build_report passes the project pass's own
    timeline instead (full_history_pass)."""
    from taxjson.lib.core import coerce_transaction_row
    from taxjson.lib.pipeline import GainsRequest, run_gains
    # A row with no symbol moves no pool (and the engine refuses it).
    txs = [coerce_transaction_row(r, i, "taxjson-t1135")
           for i, r in enumerate(transactions) if r.get("symbol")]
    req = GainsRequest(country="canada", year=None, taxable=True,
                       tax_date=tax_date, phantom_hint=False,
                       cost_timeline=True)
    err = io.StringIO()
    with contextlib.redirect_stderr(err), _unwrapped():
        res = run_gains(txs, [], (), req)
    _pass_on_engine_errors(err.getvalue())
    return res.get("cost_timeline") or {}


def _pass_on_engine_errors(text: str) -> None:
    """The engine's diagnostics belong to `taxjson run` (they would
    repeat here); a solver that did not converge is still said."""
    for line in text.splitlines():
        if "did not converge" in line:
            emit_line(line, file=sys.stderr)


def walk_costs(transactions: List[Dict[str, Any]], year: int,
               overrides: Dict[str, Optional[str]],
               tax_date: str = "settle",
               today: Optional[str] = None,
               timeline: Optional[Dict[str, Any]] = None,
               custody: Optional["Custody"] = None,
               cash: Optional[Dict[str, Dict[str, Any]]] = None
               ) -> Dict[str, Any]:
    """Per-symbol cost stats for `year` plus the maximum TOTAL foreign
    cost observed in the year (the ITA 233.3 filing-threshold test),
    from the gains engine's own pools: its cost timeline (lib/
    cost_timeline; backlog TJ-0417) — `timeline`, the project pass's
    (build_report), or one engine run over `transactions`. A cost
    amount is the ACB the engine holds (CA-RPT-12): a warrant's or an
    exercised call's cost in the shares it delivered, a premium in the
    stock leg it belongs to, every superficial-loss addition where the
    engine applied it (the replay this walk once was lost the first and
    applied the second early: backlog TJ-0365, TJ-0366).

    `transactions` give the books' last date (`last_date`). `overrides`
    is updated with rename targets of overridden symbols (a ticker change
    keeps its T1135 override, S051-17).

    With `custody` (Custody), a custody account's part of a pool of a
    Canadian issuer is a pool of its own, SYMBOL@ACCOUNT: its units at
    the end of the day times the pool's cost per unit (s.47: identical
    shares are one pool, their cost amount its ACB), the rest of the
    pool staying under the symbol (tax-logic CA-RPT-02).

    `cash` ({key: {"points": [[date, units, cost]], "unknown"}}, the
    cash rows build_report keys from lib/t1135_cash): an account's cash
    held outside Canada, a pool of its own at the cost of each day's
    end (tax-logic CA-RPT-20), its days on `tax_date`'s basis as the
    engine's (the cash document's `basis`: on trade dates a purchase's
    cash leaves on its trade date, backlog TJ-0627). A day's fall in it
    is taken before that day's engine steps and a rise after them, so
    money that bought a property (or that a sale brought in) is never
    counted twice inside the day. A row of unknown cost is a row even
    with no units."""
    from taxjson.lib import cost_timeline as _CT
    from taxjson.lib.core import is_option_symbol
    year_start = f"{year}-01-01"
    year_end = f"{year}-12-31"
    if timeline is None:
        timeline = _engine_timeline(transactions, tax_date)
    last_date = max((d for d in (_tx_date(t, tax_date)
                                 for t in transactions)
                     if d and d <= year_end), default="")

    pools: Dict[str, _Pool] = {}
    # Each pool's state per account (Canada's are pooled: account "");
    # a symbol's pool is their sum.
    states: Dict[str, Dict[str, Tuple[float, float, bool]]] = {}
    max_total = 0.0
    max_total_date = ""
    baseline_taken = False
    # Plain futures contracts seen (S008-04): a futures position has no
    # cost amount of its own — nothing is paid to open it (initial
    # margin is a deposit, variation margin settles daily) — so its
    # notional never enters the pools or the threshold test; a
    # superficial loss denied on it and added to the replacement
    # (s.53(1)(f)) is its cost amount (backlog TJ-0505). Options on
    # futures stay at their premium cost.
    futures_seen: set = set()
    # Foreign pools held at some point of the year, and those held while
    # their cost was unknown: a holding at a known zero cost is still a
    # property row, and a cost unknown while held leaves the year's
    # maximum unknown even once the shares are sold (the drained pool
    # clears its own flag).
    held_in_year: set = set()
    unknown_in_year: set = set()
    # Foreign pools held long, and held short, at some point of the
    # year: a position only ever short (a written option, a short sale)
    # is an obligation, not property held (CA-RPT-19). Never cleared:
    # a rename carries both to the new symbol and keeps the old one's.
    long_in_year: set = set()
    short_in_year: set = set()

    def total_foreign_cost() -> float:
        return sum(p.cost_amount() for s, p in pools.items()
                   if classify_country(s, overrides) is not None)

    def snapshot(date: str) -> None:
        nonlocal max_total, max_total_date
        for s, p in pools.items():
            if classify_country(s, overrides) is None:
                continue
            c = p.cost_amount()
            # A futures contract is a row only for a cost added to it
            # (apply); held at nil cost it is a note, as it always was.
            if not is_plain_future(s):
                if p.qty > p.eps():
                    held_in_year.add(s)
                    long_in_year.add(s)
                    if p.tainted:
                        unknown_in_year.add(s)
                elif p.qty < -p.eps():
                    short_in_year.add(s)
            if c > p.max_cost_in_year:
                p.max_cost_in_year = c
        tot = total_foreign_cost()
        if tot > max_total:
            max_total, max_total_date = tot, date

    # The date basis the timeline's rows are dated on: a custody
    # account's units are read on it too.
    basis = str(timeline.get("basis") or tax_date)

    def derive(symbol: str, date: str) -> None:
        """The pools of `symbol` from the engine's state: a custody
        account's share of it (its units at the end of `date`, at most
        the pool's, times the pool's cost per unit; none while the pool
        is flat or on the other side), the rest under the symbol."""
        per = states.get(symbol, {})
        q = sum(v[0] for v in per.values())
        cost = sum(v[1] for v in per.values())
        tainted = any(v[2] for v in per.values())
        shares: Dict[str, float] = {}
        if custody is not None and custody.custody \
                and custody.canadian(symbol):
            from taxjson.lib.core import pool_qty_eps
            if abs(q) > pool_qty_eps(symbol, q):
                for acct in custody.custody:
                    u = custody.units(acct, symbol, date, basis)
                    if u * q > 0:
                        shares[acct] = min(abs(u) / abs(q), 1.0)
                tot = sum(shares.values())
                if tot > 1.0:
                    # Two custody accounts' day-end units above an
                    # intra-day pool: their shares of all of it.
                    shares = {a: v / tot for a, v in shares.items()}
            for acct in custody.custody:
                k = f"{symbol}{Custody.SEP}{acct}"
                if acct not in shares and k not in pools:
                    continue
                sh = shares.get(acct, 0.0)
                cp = pools.setdefault(custody.key(symbol, acct), _Pool(k))
                cp.qty = q * sh
                cp.cost = Decimal(str(cost * sh))
                cp.tainted = tainted and sh > 0
        rest = 1.0 - sum(shares.values())
        pool = pools.setdefault(symbol, _Pool(symbol))
        pool.qty = q * rest
        # The timeline's cost is the inventory's (a short's negative):
        # cost_amount() counts a long's only.
        pool.cost = Decimal(str(cost * rest))
        pool.tainted = tainted

    def carry(symbol: str, new_symbol: str) -> None:
        """A rename moves the pool (the target's row follows in the same
        step): the override, the year's maximum and what the year saw
        of it go with it (S051-17)."""
        if symbol in overrides and new_symbol not in overrides:
            overrides[new_symbol] = overrides[symbol]
        pool = pools.get(symbol) or _Pool(symbol)
        dest = pools.setdefault(new_symbol, _Pool(new_symbol))
        dest.max_cost_in_year = max(dest.max_cost_in_year,
                                    pool.max_cost_in_year)
        for seen in (held_in_year, unknown_in_year):
            if symbol in seen:
                seen.discard(symbol)
                seen.add(new_symbol)
        for seen in (long_in_year, short_in_year):
            if symbol in seen:
                seen.add(new_symbol)
        pools[symbol] = _Pool(symbol)

    def apply(r: Dict[str, Any], date: str) -> None:
        symbol = str(r.get("symbol") or "")
        if not symbol:
            return
        cost = float(r.get("total_cost") or 0.0)
        if is_plain_future(symbol):
            if date >= year_start \
                    and classify_country(symbol, overrides) is not None:
                futures_seen.add(symbol)
            # Its cost amount is only what the engine added to its ACB:
            # the superficial losses its deferred_wash tallies, never
            # the notional a book off the settlement basis still
            # carries (lib/futures; backlog TJ-0505).
            cost = min(cost, float(r.get("deferred_wash") or 0.0))
        per = states.setdefault(symbol, {})
        per[str(r.get("account") or "")] = (
            float(r.get("qty") or 0.0), cost, bool(r.get("unknown_cost")))
        derive(symbol, date)
        new_symbol = str(r.get("renamed_to") or "")
        if new_symbol and new_symbol != symbol:
            carry(symbol, new_symbol)
            states[symbol] = {}
            # A custody account's pool goes with it: to its key of the
            # new symbol, or into the symbol when it is foreign itself.
            for acct in (custody.custody if custody is not None else ()):
                k = f"{symbol}{Custody.SEP}{acct}"
                if k in pools:
                    carry(k, custody.key_of(new_symbol, acct))

    # The engine's steps, by date, and after a day's steps the symbols a
    # custody account's rows moved that day (an own move leaves the pool
    # as it was but moves the account's share of it).
    events: List[Tuple[str, int, Any]] = []
    rows = _CT.rows(timeline)
    i = 0
    while i < len(rows):
        date = str(rows[i].get("date") or "")
        seq = rows[i].get("seq")
        j = i
        while (j < len(rows) and rows[j].get("seq") == seq
               and str(rows[j].get("date") or "") == date):
            j += 1
        events.append((date, 0, rows[i:j]))
        i = j
    n_steps = len(events)
    for key in cash or {}:
        pools.setdefault(key, _Pool(key))
    if custody is not None and custody.custody:
        events += [(d, 1, sorted(syms)) for d, syms
                   in custody.change_dates(basis).items()]
    for key, s in sorted((cash or {}).items()):
        prev = (0.0, 0.0)
        for d, u, c in s.get("points") or []:
            new = (float(u), float(c))
            low = new if new[1] <= prev[1] else prev
            events.append((str(d), -1, [(key, low, bool(s.get("unknown")))]))
            events.append((str(d), 2, [(key, new, bool(s.get("unknown")))]))
            prev = new
    if len(events) > n_steps:
        events.sort(key=lambda e: (e[0], e[1]))     # stable: steps in order
    for date, kind, step in events:
        if date > year_end:
            break
        # First change inside the year: the Jan-1 state (built from all
        # prior rows) itself counts toward the in-year maximum.
        if date >= year_start and not baseline_taken:
            snapshot(year_start)
            baseline_taken = True
        if kind == 0:
            # Rows of one step (a rename empties one pool and fills
            # another) land together before the total is taken.
            for r in step:
                apply(r, date)
        elif kind in (-1, 2):
            # An account's cash (CA-RPT-20): its state that day.
            for key, (u, c), unknown in step:
                p = pools.setdefault(key, _Pool(key))
                p.qty, p.cost, p.tainted = u, Decimal(str(c)), unknown
        else:
            for sym in step:
                if sym in states:
                    derive(sym, date)
        if date >= year_start:
            snapshot(date)

    if not baseline_taken:
        # No change fell inside the year — the standing Jan-1 position
        # is still the year's maximum.
        snapshot(year_start)

    # A long option still held after its expiry date (S052-22): the
    # books lack its expiry / exercise row, so its cost stays in the
    # pools — counted (the conservative side) but named, since the
    # contract stopped being property at expiry.
    from taxjson.lib.core import parse_option_expiry
    # Expired by the year end when it expires ON Dec 31 too (re-audit
    # A2-1121, the R1-37 twin); in an unfinished year, only once its
    # expiry day has passed (it still trades that day).

    def _expired(sym: str) -> bool:
        exp = parse_option_expiry(sym) or "9999"
        return exp <= year_end and (not today or exp < today)
    expired_held = sorted(
        s for s, p in pools.items()
        if p.qty > p.eps() and is_option_symbol(s)
        and classify_country(s, overrides) is not None
        and _expired(s))

    per_symbol = {}
    for s, p in pools.items():
        country = classify_country(s, overrides)
        if country is None:
            continue
        year_end_cost = p.cost_amount()
        # A tainted pool still holding shares is foreign property with an
        # UNKNOWN cost — it must appear (flagged), not silently vanish,
        # even though its tracked cost is 0.
        fut = is_plain_future(s)
        still_held_unknown = p.tainted and p.qty > p.eps() and not fut
        cash_unknown = s in (cash or {}) and bool(cash[s].get("unknown"))
        if p.max_cost_in_year <= 0 and year_end_cost <= 0 \
                and not still_held_unknown and s not in held_in_year \
                and not cash_unknown:
            continue
        per_symbol[s] = {
            "country": country,
            "max_cost": round(p.max_cost_in_year, 2),
            "year_end_cost": round(year_end_cost, 2),
            "unknown_acb": ((p.tainted or s in unknown_in_year) and not fut)
                           or cash_unknown,
            "futures": fut,
        }
    return {
        "per_symbol": per_symbol,
        "max_total_cost": round(max_total, 2),
        "max_total_date": max_total_date,
        "futures_symbols": sorted(futures_seen),
        "expired_options_held": expired_held,
        "short_only_symbols": sorted(short_in_year - long_in_year),
        "last_date": last_date,
    }


# ---------------------------------------------------------------- income/gains

class UnreadableGains(SystemExit):
    pass


class CurrencyMismatch(SystemExit):
    pass


class WrongCountryGains(SystemExit):
    pass


# The terms only the US engine writes on a disposition (form-export
# refuses them on Schedule 3 the same way).
_US_TERMS = ("SHORT_TERM", "LONG_TERM")


def check_gains_country(paths: List[Path]) -> None:
    """Refuse a gains file the US engine wrote (backlog TJ-0503): its
    sales carry a short/long-term term, its gains are FIFO lots' and
    its wash sales §1091's, so its GAIN(LOSS) column beside the
    Canadian average-cost pools mixed the two countries' law. An
    unreadable file is join_income_gains' to refuse."""
    for p in paths:
        try:
            data = load_json(p)
        except (OSError, ValueError):
            continue
        rows = data.get("transactions") if isinstance(data, dict) else None
        if any(isinstance(e, dict) and e.get("term") in _US_TERMS
               for e in rows or ()):
            raise WrongCountryGains(
                f"taxjson-t1135: {p}: its sales carry short/long-term "
                f"terms — gains the US engine computed (FIFO lots, wash "
                f"sales). T1135 is a Canadian form: pass the gains file "
                f"of a country=canada run (re-run `taxjson run` in the "
                f"Canadian project).")


def check_currency(paths: List[Path], base_currency: str) -> None:
    """Refuse books whose rows (or metadata.target_currency) are in
    another currency than `base_currency` (S051-15, S052-17): the walk
    sums net_amount as base currency, so a native work/<acct>_raw.json
    or a USD-base book was tested against 100,000 as if it were CAD —
    80,000 USD of US stock read 'no T1135 required'."""
    base = (base_currency or "").upper()
    for p in paths:
        try:
            doc = load_json(p)
        except (OSError, ValueError):
            continue                    # the readers report it
        meta = doc.get("metadata") if isinstance(doc, dict) else None
        tgt = str((meta or {}).get("target_currency") or "").upper()
        rows = (doc.get("transactions") or []) if isinstance(doc, dict) \
            else (doc if isinstance(doc, list) else [])
        other: Dict[str, int] = {}
        for t in rows:
            c = str((t or {}).get("currency") or "").upper() \
                if isinstance(t, dict) else ""
            if c and c != base:
                other[c] = other.get(c, 0) + 1
        if tgt and tgt != base:
            other.setdefault(tgt, 0)
        if other:
            got = ", ".join(f"{c} ({n} row(s))" if n else
                            f"{c} (metadata.target_currency)"
                            for c, n in sorted(other.items()))
            raise CurrencyMismatch(
                f"taxjson-t1135: {p}: amounts in {got}, not "
                f"{base} — the cost walk sums them as {base}. Pass the "
                f"converted books (work/<account>_base.json and the "
                f"<account>_gains*.json beside them; `taxjson t1135` "
                f"does).")


def _file_account(path: Path, data: Any) -> str:
    """The account a gains file is for: its metadata's, else the
    `<account>_gains*.json` name's ("" when neither says)."""
    meta = data.get("metadata") if isinstance(data, dict) else None
    if isinstance(meta, dict) and meta.get("account"):
        return str(meta["account"])
    name = Path(path).name
    return name.rsplit("_gains", 1)[0] if "_gains" in name else ""


def _gains_accounts(path: Path, data: Any) -> set:
    """Every account a gains file names: the file's own
    (_file_account) and its rows'."""
    out = {_file_account(path, data)} - {""}
    if isinstance(data, dict):
        for k in ("transactions", "manual_reporting_required", "inventory"):
            out |= {str(e["account"]) for e in data.get(k) or []
                    if isinstance(e, dict) and e.get("account")}
    return out


def gains_not_given(base_paths: List[Path], rows: List[Dict[str, Any]],
                    gains_paths: List[Path]) -> List[str]:
    """What the books hold that no gains file covers, so its income and
    gain columns are unknown, not 0 (backlog TJ-0355): with no gains
    file, every account the base rows name (else the base files);
    otherwise the accounts the rows name that no gains file names. []
    when a gains file names no account (the coverage cannot be told)."""
    accts = sorted({str(t["account"]) for t in rows if t.get("account")})
    if not gains_paths:
        return accts or [Path(p).name for p in base_paths]
    covered: set = set()
    for p in gains_paths:
        try:
            names = _gains_accounts(p, load_json(p))
        except (OSError, ValueError):
            continue                    # join_income_gains refuses it
        if not names:
            return []
        covered |= names
    return [a for a in accts if a not in covered]


_INCOME_ACTIONS = ("DIVIDEND", "DIVIDEND_IN_LIEU")


def _gains_row_date(e: Dict[str, Any], tax_date: str = "settle") -> str:
    """The date whose year a gains-file row belongs to: an income row's
    the pipeline's (its `income_date` — a Canadian trust's distribution
    on its record date —, else the day it was paid: CA-INC-DATE-DIV,
    never its settlement date: backlog TJ-0488), a disposition's on the
    project's tax_date basis."""
    if (e.get("action") or "") in _INCOME_ACTIONS:
        return str(e.get("income_date") or e.get("date")
                   or e.get("date_settle") or "")
    return _tx_date(e, tax_date)


def _gains_key(e: Dict[str, Any], custody: Optional[Custody],
               file_acct: str) -> str:
    """A gains row's symbol, as its pool is keyed (Custody.key_of; a
    row with no account is its file's)."""
    sym = e.get("symbol") or ""
    if custody is None or not sym:
        return sym
    return custody.key_of(sym, str(e.get("account") or file_acct))


def join_income_gains(gains_paths: List[Path], year: int,
                      overrides: Optional[Dict[str, Optional[str]]] = None,
                      tax_date: str = "settle",
                      custody: Optional[Custody] = None
                      ) -> Dict[str, Dict[str, float]]:
    """Per-symbol dividend+PIL income and realized gain(loss) for `year`,
    from the pipeline's (already year-scoped) gains files. Defensively
    re-filters by year so a hand-run full-history gains file also works:
    a disposition on the `tax_date` basis, income on the date the
    pipeline gives it (_gains_row_date: a dividend paid Dec 31 that
    settles in January is the year's, backlog TJ-0488).

    Unknown-cost dispositions (the pipeline's manual_reporting_required
    rows — their 'tainted' key is popped there, so a 'tainted' test alone
    never saw them: audit R1-199) are excluded from the gain column with
    a warning naming them. With `overrides`, only T1135-scope (foreign)
    symbols are named. With `custody`, a custody account's Canadian
    shares are keyed as its pool (Custody.key_of)."""
    out: Dict[str, Dict[str, float]] = {}
    ystr = str(year)
    tainted_skipped = 0
    manual_syms: List[str] = []
    for p in gains_paths:
        try:
            data = load_json(p)
            from taxjson.lib.json_input import require_gains_doc
            require_gains_doc(data, p)
        except (OSError, ValueError) as e:
            # A skipped taxable gains file zeroed that account's income
            # and gain columns with rc 0 (R1-277); so did a stage file
            # or a JSON without 'transactions' (S033-01).
            raise UnreadableGains(
                f"taxjson-t1135: could not read {p}: {e} — re-run "
                f"`taxjson run` to rebuild it.")
        file_acct = _file_account(p, data)
        for e in data.get("transactions", []):
            date = _gains_row_date(e, tax_date)
            if not date.startswith(ystr):
                continue
            symbol = _gains_key(e, custody, file_acct)
            if not symbol:
                continue
            rec = out.setdefault(symbol, {"income": 0.0, "gain": 0.0})
            action = e.get("action") or ""
            if action == "DIVIDEND":
                rec["income"] += float(e.get("dividend") or 0.0)
            elif action == "DIVIDEND_IN_LIEU":
                # A Canadian dealer's payment in lieu on a Canadian
                # issuer is a s.260 deemed dividend: the run carries it
                # as 'dividend' with pil 0 (re-audit A2-0661; as
                # sum-gains and filed read it).
                rec["income"] += (float(e.get("pil") or 0.0)
                                  + float(e.get("dividend") or 0.0))
            elif "gain" in e:
                # Tainted dispositions (unknown cost, booked at zero) carry a
                # fabricated gain — form-export and carryover exclude
                # them with a warning; the T1135 GAIN(LOSS) column must
                # not silently include what its sibling tools refuse.
                if e.get("tainted"):
                    tainted_skipped += 1
                    continue
                rec["gain"] += float(e.get("gain") or 0.0)
        for e in data.get("manual_reporting_required") or []:
            date = _tx_date(e, tax_date)
            symbol = _gains_key(e, custody, file_acct)
            if not str(date).startswith(ystr) or not symbol:
                continue
            if (overrides is not None
                    and classify_country(symbol, overrides) is None):
                continue            # domestic: not a T1135 property
            tainted_skipped += 1
            manual_syms.append(custody.symbol(symbol) if custody
                               else symbol)
    if tainted_skipped:
        _named = (f" ({', '.join(sorted(set(manual_syms)))})"
                  if manual_syms else "")
        from taxjson.lib.out import warn as _warn
        _warn(f"{tainted_skipped} disposition(s) with an unknown cost (no "
              f"purchase in your files){_named} EXCLUDED from the T1135 "
              f"gain(loss) column",
              details=_dl(["Resolve the missing history and re-run "
                           "(matches form-export and carryover)."]))
    return out


_INCOME_ONLY = ("DIVIDEND", "DIVIDEND_IN_LIEU", "TAX", "INTEREST", "FEE")


def property_interest(rows: List[Dict[str, Any]], year: int,
                      custody: Optional[Custody] = None
                      ) -> Dict[str, float]:
    """{pool key: the year's interest} the books tie to a property: an
    INTEREST row naming a symbol the books trade, open or move (a
    bond's coupon), dated the day it was paid (CA-DATE-11) and signed
    as booked, in the base currency (tax-logic CA-RPT-14; backlog
    TJ-0504). The gains files carry no interest, so the T1135 INCOME
    column of a foreign debt read 0. Interest on an account's cash
    (`CASH`, no symbol) names no property (its cash row's income where
    the account's cash is a row: cash_interest); `custody` keys a
    custody account's Canadian issuer as its pool."""
    held = set()
    for t in rows:
        if (t.get("action") or "").upper() in _INCOME_ONLY:
            continue
        for s in (t.get("symbol"), t.get("symbol_new")):
            if s:
                held.add(str(s))
    held.discard("CASH")
    out: Dict[str, float] = {}
    ystr = str(year)
    for t in rows:
        sym = str(t.get("symbol") or "")
        if ((t.get("action") or "").upper() != "INTEREST"
                or sym not in held
                or not str(t.get("date") or "").startswith(ystr)):
            continue
        try:
            amt = float(t.get("net_amount") or 0.0)
        except (TypeError, ValueError):
            continue
        key = custody.key_of(sym, str(t.get("account") or "")) \
            if custody is not None else sym
        out[key] = out.get(key, 0.0) + amt
    return out


def cash_interest(rows: List[Dict[str, Any]], year: int, custody: Custody,
                  currencies: Dict[str, str]) -> Dict[str, float]:
    """{cash row key: the year's interest its cash earned} (tax-logic
    CA-RPT-14, CA-RPT-20; backlog TJ-0628): an INTEREST row naming no
    symbol (`CASH`) of an account whose cash in the currency it was
    paid in is a row (`currencies`: {row id: currency}, the cash
    document's `interest`; the base books keep only its base amount),
    dated the day it was paid (CA-DATE-11), in the base currency.
    Interest credited only: a negative row is interest charged on a
    debit balance, a debt's cost and no property's income."""
    out: Dict[str, float] = {}
    ystr = str(year)
    for t in rows:
        if ((t.get("action") or "").upper() != "INTEREST"
                or str(t.get("symbol") or "").upper() not in ("", "CASH")
                or not str(t.get("date") or "").startswith(ystr)):
            continue
        cur = currencies.get(str(t.get("id") or ""))
        if not cur:
            continue
        key = cash_key(cur, str(t.get("account") or ""))
        if key not in custody.cash_rows:
            continue
        try:
            amt = float(t.get("net_amount") or 0.0)
        except (TypeError, ValueError):
            continue
        if amt > 0:
            out[key] = out.get(key, 0.0) + amt
    return out


def full_history_pass(base_paths: List[Path],
                      sheltered_paths: List[Path] = (),
                      phantoms: Optional[Path] = None,
                      tax_date: str = "settle",
                      option_timing: Optional[Dict[str, Any]] = None,
                      income_rules: Optional[Dict[str, Any]] = None
                      ) -> Dict[str, Any]:
    """The Canada engine once over the books' FULL history, with its
    cost timeline (lib/cost_timeline; backlog TJ-0417): one `run_gains`
    pass (year=None) over the taxable books together — ITA s.47 pools are
    symbol-global, as in the pipeline's blended pass — with the
    registered accounts as wash context and the project's missing-history
    file and option timing, the way `taxjson carryover` runs it. A loss
    denied in an earlier year whose replacement is still held is in the
    year's ACB (s.53(1)(f)), but not in the year-scoped gains files
    (S008-07, S009-01, S051-21): the timeline's pools carry it."""
    from taxjson.lib.json_input import load_transactions_or_exit
    from taxjson.lib.pipeline import GainsRequest, run_gains
    txs: List[Any] = []
    for p in base_paths:
        txs.extend(load_transactions_or_exit("taxjson-t1135", p))
    sheltered: List[Any] = []
    for p in sheltered_paths or ():
        sheltered.extend(load_transactions_or_exit("taxjson-t1135", p))
    req = GainsRequest(country="canada", year=None, taxable=True,
                       tax_date=tax_date, incomplete_history=phantoms,
                       phantom_hint=False, cost_timeline=True,
                       **(option_timing or {}),
                       # The project's income dating (a listed
                       # corporation's ROC on its pay date), as in the
                       # filing run (audit A2-0339).
                       **(income_rules or {}))
    # The engine's diagnostics belong to `taxjson run` (they would repeat
    # here); this pass only reads its pools.
    err = io.StringIO()
    with contextlib.redirect_stderr(err), _unwrapped():
        res = run_gains(txs, sheltered, (), req)
    _pass_on_engine_errors(err.getvalue())
    return res


def full_history_wash_sales(base_paths: List[Path],
                            sheltered_paths: List[Path] = (),
                            phantoms: Optional[Path] = None,
                            tax_date: str = "settle",
                            option_timing: Optional[Dict[str, Any]] = None,
                            income_rules: Optional[Dict[str, Any]] = None
                            ) -> List[Dict[str, Any]]:
    """Every superficial loss the Canada engine denies over the books'
    full history (full_history_pass)."""
    return list(full_history_pass(base_paths, sheltered_paths, phantoms,
                                  tax_date, option_timing,
                                  income_rules).get("wash_sales") or [])


# ---------------------------------------------------------------- report

def build_report(base_paths: List[Path], gains_paths: List[Path], year: int,
                 overrides: Dict[str, Optional[str]],
                 base_currency: str,
                 threshold: float = FILING_THRESHOLD,
                 detailed_threshold: float = DETAILED_THRESHOLD,
                 phantoms: Optional[Path] = None,
                 tax_date: str = "settle",
                 today: Optional[str] = None,
                 sheltered_paths: List[Path] = (),
                 option_timing: Optional[Dict[str, Any]] = None,
                 full_history: bool = True,
                 income_rules: Optional[Dict[str, Any]] = None,
                 issuer_countries: Optional[Dict[str, str]] = None,
                 key_renames: Optional[Dict[str, str]] = None,
                 categories: Optional[Dict[str, int]] = None,
                 kinds: Optional[Dict[str, str]] = None,
                 custody: Optional[Dict[str, Optional[str]]] = None,
                 cash: Optional[Dict[str, Any]] = None
                 ) -> Dict[str, Any]:
    """The T1135 report model. `today` (ISO date, default the real
    date) decides whether the year is complete: before Dec 31 the
    figures run to the last date in the books and a negative verdict is
    provisional (S051-22, S052-15).

    The cost amounts are the engine's own pools: one run over the whole
    history (`full_history_pass`, with `sheltered_paths` as wash context
    and the project's `option_timing`) and its cost timeline (backlog
    TJ-0417), so a superficial loss denied in ANY year is in the
    replacement's cost. `full_history` is kept for older callers: the
    pools are the engine's either way (the year-only replay it chose is
    gone).

    `issuer_countries` ({symbol: ISO alpha-3}, lib/tobase_map.
    t1135_countries): the country of a foreign issuer the interlisted
    pairs book under its US listing (BMU for a Bermuda issuer, not the
    .US suffix's USA); a user override wins. `key_renames` (the map's
    renames to the base symbol): a user `T1135 SYMBOL` line naming a
    listing a TOBASE line books under another symbol follows it.

    `categories` ({SYMBOL: 1-6}, load_categories) and `kinds` (the
    master's units and funds, lib/tobase_map.t1135_kinds) give each
    property row its T1135 category (property_category).

    `custody` ({account: ISO alpha-3, None for Canada}, the accounts'
    `custodian_country`): a Canadian issuer's shares held in such an
    account are a row of their own, category 6, under that country
    (Custody; tax-logic CA-RPT-02, CA-RPT-18).

    `cash` (lib/t1135_cash.cash_doc, `--cash FILE`): the cash an
    account holds outside Canada, a row per account and currency,
    category 1, counted toward the test at its cost day by day; a USD
    stablecoin's income is its account's US-dollar cash's. A row of
    unknown cost (the FX-on-cash ledger v2 refuses), or an account
    whose cash could not be costed at all, leaves the total not settled
    (`total_settled`); an account whose Canadian dollars the books give
    no balance of is named (`cash_not_counted`). Tax-logic CA-RPT-20.

    The income and gain columns of what no gains file covers
    (gains_not_given) are None, not 0, with a warning (backlog
    TJ-0355)."""
    from datetime import date as _date
    today = today or _date.today().isoformat()
    check_currency(list(base_paths) + list(gains_paths), base_currency)
    # A US-engine gains file beside the Canadian pools (backlog TJ-0503).
    check_gains_country(list(gains_paths))
    txs = load_transactions(base_paths, phantoms)
    key_renames = key_renames or {}
    written: Dict[str, str] = {}        # booked symbol -> as written
    mapped: Dict[str, Optional[str]] = {}
    for k, v in overrides.items():
        nk = key_renames.get(k, k)
        if nk != k and nk in overrides:
            continue        # the user's line for the booked symbol wins
        mapped[nk] = v
        if nk != k:
            written[nk] = k
    user_keys = set(mapped)
    cats: Dict[str, int] = {}
    for k, v in (categories or {}).items():
        nk = key_renames.get(k, k)
        if nk != k and nk in (categories or {}):
            continue        # the user's line for the booked symbol wins
        cats[nk] = v
    overrides = dict(issuer_countries or {})
    overrides.update(mapped)            # the walk adds rename targets
    # A custody account's part of a Canadian issuer's pool is a pool of
    # its own: its units times the pool's cost per unit (Custody).
    cust = Custody(custody, overrides, txs)
    # The cash held outside Canada (CA-RPT-20): a pool per account and
    # currency, at the cost lib/t1135_cash gives each day.
    cash_pools: Dict[str, Dict[str, Any]] = {}
    for s in (cash or {}).get("series") or []:
        k = cust.cash(str(s["currency"]).upper(), str(s["account"]),
                      str(s["country"]))
        cash_pools[k] = {"points": s.get("points") or [],
                         "unknown": bool(s.get("unknown")),
                         "why": str(s.get("why") or "")}
    if cash and cash.get("stablecoins"):
        for acct, country in sorted((cash.get("accounts") or {}).items()):
            k = cash_key("USD", acct)
            if k not in cust.cash_rows:
                cust.cash("USD", acct, str(country))
            for coin in cash["stablecoins"]:
                cust.cash_coins[(str(acct), str(coin).upper())] = k
    # The cost amounts are the engine's pools, read from its cost
    # timeline (backlog TJ-0417): every superficial loss denied in any
    # year is in them where the engine put it (G7-0, S008-07).
    timeline = full_history_pass(
        base_paths, sheltered_paths, phantoms, tax_date, option_timing,
        income_rules).get("cost_timeline") or {}
    walk = walk_costs(txs, year, overrides, tax_date, today=today,
                      timeline=timeline, custody=cust, cash=cash_pools)
    # A category follows its symbol through a rename, as the walk has
    # carried its country.
    for t in sorted((t for t in txs
                     if (t.get("action") or "").upper() == "SPLIT"
                     and t.get("symbol_new")),
                    key=lambda t: _sort_key(t, tax_date)):
        if t.get("symbol") in cats and t["symbol_new"] not in cats:
            cats[t["symbol_new"]] = cats[t["symbol"]]
    inc = join_income_gains(gains_paths, year, overrides, tax_date, cust)
    # The interest a property pays (a foreign bond's coupon) is its
    # income too; the gains files carry none (CA-RPT-14; backlog
    # TJ-0504).
    for key, amt in property_interest(txs, year, cust).items():
        rec = inc.setdefault(key, {"income": 0.0, "gain": 0.0})
        rec["income"] += amt
    # The interest an account's cash earns is that cash row's income
    # (CA-RPT-14; backlog TJ-0628).
    for key, amt in cash_interest(txs, year, cust, dict(
            (cash or {}).get("interest") or {})).items():
        rec = inc.setdefault(key, {"income": 0.0, "gain": 0.0})
        rec["income"] += amt
    # What no gains file covers: its income and gain are unknown, not
    # 0 (backlog TJ-0355) — every row with no gains file, else each
    # symbol the year's rows of an account without one name.
    not_given = gains_not_given(base_paths, txs, gains_paths)
    unknown_inc: set = set()
    if not_given:
        miss = set(not_given)
        for t in txs:
            if str(t.get("account") or "") in miss \
                    and _gains_row_date(t, tax_date).startswith(str(year)):
                for s in (t.get("symbol"), t.get("symbol_new")):
                    if s:
                        unknown_inc.add(cust.key_of(
                            str(s), str(t.get("account") or "")))
        from taxjson.lib.out import warn as _warn
        _warn(f"no gains file (--gains) for {', '.join(not_given)}: the "
              f"INCOME and GAIN(LOSS) columns of "
              f"{'every property' if not gains_paths else 'its properties'}"
              f" read n/a, not 0",
              details=_dl(["Pass each account's <account>_gains.json (or "
                           "_gains_wash.json) with --gains; `taxjson "
                           "t1135` does."]))
    # An override that matches nothing (a ticker change, a ticker.map
    # consolidation, a typo) silently reversed the filing verdict
    # (S051-17): name it.
    seen = {t.get("symbol") for t in txs if t.get("symbol")}
    unused_overrides = sorted(k for k in user_keys if k not in seen)
    for k in unused_overrides:
        from taxjson.lib.out import warn as _warn
        shown = (f"`T1135 {written[k]}` (booked as {k})" if k in written
                 else f"`T1135 {k}`")
        _warn(f"ticker.map: {shown} matches no symbol in the books — "
              f"the override is not applied",
              details=_dl(["Renamed, consolidated by ticker.map, or a "
                           "typo?"]))
    # A Canadian issuer on a foreign listing (its ISIN says CA — IB
    # stamps issuer_country on the rows) is not specified foreign
    # property, yet the listing suffix classifies it foreign. Named, not
    # guessed: the user confirms with a `T1135 SYMBOL CA` line in ticker.map
    # (re-audit A2-0332).
    canadian_issuer = sorted({
        str(t.get("symbol")) for t in txs
        if str(t.get("issuer_country") or "").upper() == "CA"
        and t.get("symbol") and t.get("symbol") not in user_keys
        and classify_country(str(t.get("symbol")), overrides)
        not in (None, CRYPTO)})
    if canadian_issuer:
        from taxjson.lib.out import warn as _warn
        _warn(f"{len(canadian_issuer)} symbol(s) on a foreign listing "
              f"carry a Canadian ISIN: {', '.join(canadian_issuer[:6])}"
              f"{' ...' if len(canadian_issuer) > 6 else ''}",
              details=_dl(["Shares of a Canadian corporation are not "
                           "specified foreign property (unless held with "
                           "a custodian outside Canada: [accounts.NAME] "
                           "custodian_country), but they are counted here "
                           "by their listing — add `T1135 SYMBOL CA` to "
                           "ticker.map once confirmed."]))
    # The mirror case (backlog TJ-0455): a foreign issuer on a Canadian
    # listing (its rows' ISIN names another country — a US corporation
    # bought on the TSX) is specified foreign property wherever listed,
    # yet the listing suffix classifies it Canadian, while the estimate
    # takes its dividends as foreign from the same ISIN. Named with the
    # line to add, not guessed; tobase.map's issuer country, when it
    # knows the issuer, has already classified it (issuer_countries).
    foreign_issuer = foreign_isin_symbols(txs, overrides, user_keys)
    if foreign_issuer:
        from taxjson.lib.out import warn as _warn
        _fi = sorted(foreign_issuer)
        _warn(f"{len(_fi)} symbol(s) on a Canadian listing carry a "
              f"foreign ISIN: "
              f"{', '.join(f'{s} ({foreign_issuer[s]})' for s in _fi[:6])}"
              f"{' ...' if len(_fi) > 6 else ''}",
              details=_dl([f"A non-resident corporation's shares are "
                           f"specified foreign property wherever they "
                           f"are listed, but they are counted here as "
                           f"Canadian by their listing — add "
                           f"`T1135 {_fi[0]} {foreign_issuer[_fi[0]]}` "
                           f"{'(a line for each) ' if len(_fi) > 1 else ''}"
                           f"to ticker.map once confirmed, or `T1135 "
                           f"SYMBOL CA` for a corporation resident in "
                           f"Canada."]))
    futures = set(walk.get("futures_symbols") or ())

    def _category(key: str) -> Tuple[Optional[int], str]:
        # A custody pool: category 6, other property (CA-RPT-18); an
        # account's cash: 1, funds held outside Canada (CA-RPT-20).
        if key in cust.cash_rows:
            return 1, "cash"
        if key in cust.keys:
            return 6, "custody"
        return property_category(key, cats, kinds or {})

    def _money_cols(key: str, ig: Dict[str, float]) -> Dict[str, Any]:
        if not_given and (not gains_paths or key in unknown_inc):
            return {"income": None, "gain": None}
        return {"income": round(ig.get("income", 0.0), 2),
                "gain": round(ig.get("gain", 0.0), 2)}

    def _custody_field(key: str) -> Dict[str, Any]:
        if key in cust.cash_rows:
            cur, acct, _c = cust.cash_rows[key]
            pool = cash_pools.get(key) or {}
            return {"cash": True, "cash_account": acct, "currency": cur,
                    "cash_why": pool.get("why", "")
                    if pool.get("unknown") else ""}
        return ({"custody_account": cust.keys[key][1]}
                if key in cust.keys else {})

    rows = []
    # The cash rows after the properties (CA-RPT-20).
    for symbol in sorted(walk["per_symbol"],
                         key=lambda k: (k in cust.cash_rows, k)):
        s = walk["per_symbol"][symbol]
        ig = inc.get(symbol, {})
        cat, cat_from = _category(symbol)
        rows.append({
            "symbol": cust.symbol(symbol),
            "country": s["country"],
            "category": cat,
            "category_from": cat_from,
            "max_cost": s["max_cost"],
            "year_end_cost": s["year_end_cost"],
            **_money_cols(symbol, ig),
            "unknown_acb": s["unknown_acb"],
            # A futures contract with a superficial loss added to its
            # cost (walk_costs; backlog TJ-0505).
            "futures": bool(s.get("futures")),
            **_custody_field(symbol),
        })
    # Income or gain on a foreign symbol whose cost never showed up in the
    # walk (e.g. fully disposed via a corp action the walk didn't model)
    # still belongs on the form. A position the walk saw only short (a
    # written option bought back or expired, a short sale covered) is
    # not property held: named, not a row (CA-RPT-19).
    short_only = set(walk.get("short_only_symbols") or ())
    short_left_out: List[str] = []
    for symbol, ig in sorted(inc.items()):
        if symbol in walk["per_symbol"]:
            continue
        country = classify_country(symbol, overrides)
        if country is None:
            continue
        if abs(ig.get("income", 0.0)) < 0.005 and abs(ig.get("gain", 0.0)) < 0.005:
            continue
        if symbol in short_only:
            short_left_out.append(cust.symbol(symbol))
            continue
        cat, cat_from = _category(symbol)
        rows.append({
            "symbol": cust.symbol(symbol), "country": country,
            "category": cat, "category_from": cat_from,
            "max_cost": 0.0, "year_end_cost": 0.0,
            **_money_cols(symbol, ig),
            "unknown_acb": False,
            **_custody_field(symbol),
            "futures": is_plain_future(symbol),
        })

    by_country: Dict[str, Dict[str, Optional[float]]] = {}
    for r in rows:
        c = by_country.setdefault(r["country"], {
            "max_cost": 0.0, "year_end_cost": 0.0, "income": 0.0, "gain": 0.0})
        # NOTE: summing per-symbol maxima overstates a true simultaneous
        # per-country maximum (the symbols may not have peaked together) —
        # a conservative upper bound, which is the safe direction for a
        # disclosure form. The filing-threshold test below does NOT use
        # this: it uses the event-wise simultaneous total. An unknown
        # income or gain (None: no gains file) leaves the total unknown.
        for k in c:
            c[k] = (None if c[k] is None or r[k] is None
                    else c[k] + r[k])
    for c in by_country.values():
        for k in c:
            if c[k] is not None:
                c[k] = round(c[k], 2)

    max_total = walk["max_total_cost"]
    year_end = f"{year}-12-31"
    year_complete = today > year_end
    # The cash a ledger could not cost (CA-RPT-20): its rows, and the
    # accounts it could not read at all; either leaves the total open.
    cash_unknown = [r for r in rows if r.get("cash") and r["unknown_acb"]]
    not_counted = list((cash or {}).get("not_counted") or [])
    total_settled = not cash_unknown and not any(
        n.get("unknown") for n in not_counted)
    as_of = year_end if year_complete else (
        walk.get("last_date") or min(today, year_end))
    expired = list(walk.get("expired_options_held") or [])
    if expired:
        from taxjson.lib.out import warn as _warn
        _warn(f"{len(expired)} long option(s) still held in the books "
              f"after their expiry date: {', '.join(expired[:6])}"
              f"{' ...' if len(expired) > 6 else ''}",
              details=_dl(["Their cost is still counted in the T1135 "
                           "figures — add the missing expiry or exercise "
                           "row and re-run."]))
    return {
        "year": year,
        "base_currency": base_currency,
        "max_total_cost": max_total,
        "max_total_date": walk["max_total_date"],
        "filing_threshold": threshold,
        "filing_required": max_total > threshold,
        # The verdict covers these brokerage books only (S052-13): the
        # JSON carries the text report's qualification (A2-0682).
        "scope": "books_only",
        "scope_note": ("on these books only: specified foreign property "
                       "held outside them (a foreign bank account or "
                       "cash held elsewhere, certificate shares, a "
                       "foreign rental) is not counted and adds to the "
                       "same threshold"),
        # The cash held outside Canada in the books (CA-RPT-20): the
        # accounts read for it ({account: country}), whether the
        # ledger that costs it computed, the accounts whose cash is not
        # counted (and why), and whether the total is settled: a cash
        # row of unknown cost leaves it open.
        "cash_accounts": dict((cash or {}).get("accounts") or {}),
        "cash_ledger_status": (cash or {}).get("ledger_status"),
        "cash_not_counted": not_counted,
        "cash_unknown_cost": [_cash_cell(r) for r in cash_unknown],
        "total_settled": total_settled,
        "detailed_threshold": detailed_threshold,
        "simplified_method_available": max_total < detailed_threshold,
        "properties": rows,
        "by_country": by_country,
        "review_symbols": [r["symbol"] for r in rows
                           if r["country"] in (REVIEW, CRYPTO)],
        "crypto_symbols": [r["symbol"] for r in rows
                           if r["country"] == CRYPTO],
        "unknown_acb_symbols": [r["symbol"] for r in rows if r["unknown_acb"]],
        # The rows whose category is the user's to set (units or a fund
        # in the interlisted master): property_category.
        "category_review_symbols": [r["symbol"] for r in rows
                                    if r["category"] is None],
        "futures_symbols": sorted(futures),
        "missing_history_applied": phantoms is not None,
        "unused_overrides": unused_overrides,
        # Superficial losses left out of the cost columns: none — the
        # pools are the engine's, every addition where it applied it
        # (kept for --json readers; --year-wash-only once filled it).
        "deferred_wash_not_in_cost": {},
        "full_history_wash": True,
        "tax_date_basis": tax_date,
        "year_complete": year_complete,
        "as_of": as_of,
        "expired_options_held": expired,
        "canadian_issuer_symbols": canadian_issuer,
        # Shares on a Canadian listing whose ISIN names another country,
        # counted as Canadian (backlog TJ-0455): {symbol: ISO alpha-3}.
        "foreign_issuer_symbols": sorted(foreign_issuer),
        "foreign_issuer_countries": dict(sorted(foreign_issuer.items())),
        # The accounts read as held with a custodian outside Canada
        # ({account: ISO alpha-3}): their Canadian issuers' shares are
        # rows (`custody_account`), category 6, at their share of the
        # pooled ACB (CA-RPT-02, CA-RPT-18).
        "custodians": dict(sorted(cust.custody.items())),
        # What was read without its gains file: the income and gain of
        # its properties are null, not 0 (backlog TJ-0355).
        "gains_not_given": not_given,
        # Held only short in the year, with a gain or income: not rows
        # (CA-RPT-19); their gains are in the capital gains report.
        "short_only_symbols": short_left_out,
    }


_money = fmt_money                  # shared report-layer formatter


# Essentials first (docs/output-style.md): with --brief (what `taxjson
# t1135` passes unless --details) a message is its headline and the
# report is render_brief; the standalone tool prints everything.
_DETAILS = True


def _cat(row: Dict[str, Any]) -> str:
    """A property row's CAT cell: its T1135 category, `?` for the user
    to set."""
    return "?" if row.get("category") is None else str(row["category"])


def _dl(lines: List[str]) -> List[str]:
    """A message's detail lines: with --details only."""
    return list(lines) if _DETAILS else []


def _amt(v: Optional[float]) -> str:
    """An income or gain cell: n/a when unknown (no gains file read for
    it, backlog TJ-0355)."""
    return "n/a" if v is None else _money(v)


def _custody_note(rep: Dict[str, Any]) -> Optional[str]:
    """The legend line of the custody rows (CA-RPT-02, CA-RPT-18), or
    None."""
    held = [f"{r['symbol']} ({r['custody_account']})"
            for r in rep["properties"] if r.get("custody_account")]
    if not held:
        return None
    return (f"{', '.join(held[:2])}"
            f"{' +' + str(len(held) - 2) + ' more' if len(held) > 2 else ''}"
            f": a Canadian issuer held with a custodian outside Canada, "
            f"CAT 6 under its country")


def _cash_cell(row: Dict[str, Any]) -> str:
    """A row's SYMBOL cell: a cash row's names its account (`USD cash
    (crypto)`, CA-RPT-20)."""
    if row.get("cash"):
        return f"{row['symbol']} ({row['cash_account']})"
    return str(row["symbol"])


def _few(names: List[str], n: int = 2) -> str:
    return (", ".join(names[:n])
            + (f" +{len(names) - n} more" if len(names) > n else ""))


def _cash_acts(rep: Dict[str, Any]) -> List[Tuple[str, str]]:
    """The `! ` lines of the cash in the books (CA-RPT-20), each with
    the command that says more: the rows of unknown cost (below the
    threshold the NOT SETTLED verdict names them), the accounts whose
    cash is not counted."""
    out: List[Tuple[str, str]] = []
    unk = rep.get("cash_unknown_cost") or []
    if unk and rep.get("filing_required"):
        out.append((f"{_few(unk, 1)}: cost unknown (fx-cash v2 refuses)",
                    "tjs fx-cash --ledger v2"))
    for n in rep.get("cash_not_counted") or []:
        if n.get("unknown"):
            out.append((f"{n['account']}: cash not counted (fx-cash v2 "
                        f"could not run)", "tjs fx-cash --ledger v2"))
        else:
            out.append((f"{n['account']}: {n.get('currency') or ''} cash "
                        f"not counted: add it by hand",
                        "tjs t1135 --details"))
    return out


def _no_gains_note(rep: Dict[str, Any]) -> Optional[str]:
    """What the INCOME and GAIN(LOSS) n/a cells mean, or None."""
    ng = rep.get("gains_not_given") or []
    if not ng:
        return None
    return (f"INCOME and GAIN(LOSS) n/a: no gains file (--gains) for "
            f"{', '.join(ng[:2])}"
            f"{' +' + str(len(ng) - 2) + ' more' if len(ng) > 2 else ''}")


def render_brief(rep: Dict[str, Any]) -> str:
    """The default view: the filing verdict and its figure, the two
    tables under a legend, then one `! ` line per thing to act on and
    what the test leaves out — the notes and every caveat with
    --details (render_report)."""
    from taxjson.lib import out as _out
    cur = rep["base_currency"]
    complete = rep.get("year_complete", True)
    as_of = rep.get("as_of") or f"{rep['year']}-12-31"
    cmd = "tjs t1135 --details"
    lines: List[str] = [
        f"T1135 — Foreign Income Verification Statement helper, tax year "
        f"{rep['year']}, {cur}", "",
        "FILING REQUIREMENT — total-cost test, ITA 233.3"]
    when = f" on {rep['max_total_date']}" if rep["max_total_date"] else ""
    lines.append(f"Maximum total cost in {rep['year']}"
                 f"{'' if complete else ' so far'}: "
                 f"{_money(rep['max_total_cost'])} {cur}{when}")
    thr = f"{_money(rep['filing_threshold'])} {cur}"
    settled = rep.get("total_settled", True)
    if rep["filing_required"]:
        part = ("simplified method (Part A)"
                if rep["simplified_method_available"]
                else "detailed method (Part B)")
        lines.append(f"=> T1135 FILING REQUIRED (over {thr}): {part}")
    elif not settled:
        # Cash of unknown cost (CA-RPT-20): no verdict below the line.
        lines.append(f"=> NOT SETTLED: below the {thr} threshold"
                     f"{'' if complete else ' so far'}, but some cash's "
                     f"cost is unknown — tjs fx-cash --ledger v2")
    elif complete:
        lines.append(f"=> below the {thr} threshold on these books: "
                     f"no T1135 required")
    else:
        lines.append(f"=> below the {thr} threshold so far (books "
                     f"through {as_of}): re-run after Dec 31")
    acts: List[str] = []
    cash_acts = _cash_acts(rep)
    if rep.get("expired_options_held"):
        _ex = rep["expired_options_held"]
        acts.append(f"{len(_ex)} expired long option(s) still counted at "
                    f"cost: {', '.join(_ex[:2])}"
                    f"{' +' + str(len(_ex) - 2) + ' more' if len(_ex) > 2 else ''}")
    if rep.get("canadian_issuer_symbols"):
        _ci = rep["canadian_issuer_symbols"]
        acts.append(f"{len(_ci)} Canadian-ISIN symbol(s) counted as foreign:"
                    f" {', '.join(_ci[:2])}"
                    f"{' +' + str(len(_ci) - 2) + ' more' if len(_ci) > 2 else ''}")
    if rep.get("foreign_issuer_symbols"):
        _fi = rep["foreign_issuer_symbols"]
        acts.append(f"{len(_fi)} foreign-ISIN symbol(s) counted as "
                    f"Canadian: {', '.join(_fi[:2])}"
                    f"{' +' + str(len(_fi) - 2) + ' more' if len(_fi) > 2 else ''}")
    _ng = _no_gains_note(rep)
    if _ng:
        acts.append(_ng)
    rows = rep["properties"]
    end_col = "COST AT DEC 31" if complete else f"COST AT {as_of}"
    _so = rep.get("short_only_symbols") or []
    if _so:
        acts.append(f"{_so[0]}"
                    f"{' +' + str(len(_so) - 1) + ' more' if len(_so) > 1 else ''}"
                    f": held only short: an obligation, not a row")
    if rows:
        by_note: Dict[str, List[str]] = {}
        for r in rows:
            if r["unknown_acb"] and not r.get("cash"):
                by_note.setdefault("unknown ACB, cost understated",
                                   []).append(r["symbol"])
            if r["country"] == REVIEW:
                by_note.setdefault("unclassified: add a ticker.map T1135 "
                                   "line", []).append(r["symbol"])
            elif r["country"] == CRYPTO:
                # A crypto platform's cash too: its country is its
                # account's custodian_country (CA-RPT-20; --details).
                by_note.setdefault("crypto, check where held",
                                   []).append(_cash_cell(r))
            if r["category"] is None:
                by_note.setdefault(f"{r['category_from']}s, not shares: "
                                   f"set the T1135 category",
                                   []).append(r["symbol"])
        for n, syms in by_note.items():
            acts.append(f"{', '.join(syms[:3])}"
                        f"{' +' + str(len(syms) - 3) + ' more' if len(syms) > 3 else ''}"
                        f": {n}")
        lines.append("")
        lines.append("PER PROPERTY — taxable accounts only")
        lines.append("Amounts are cost (ACB), not market value; MAX COST "
                     "IN YR: the most held at once.")
        if any(r.get("cash") for r in rows):
            lines.append("CAT: the form's category (1 cash; 2 shares; 6 "
                         "options, futures, crypto); a ticker.map T1135 "
                         "line sets another.")
        else:
            lines.append("CAT: the form's category (2 shares; 6 options, "
                         "futures, crypto); ticker.map `T1135 SYMBOL "
                         "COUNTRY CAT` sets another.")
        _cn = _custody_note(rep)
        if _cn:
            lines.append(_cn + ".")
        table = [(_cash_cell(r), r["country"], _cat(r), _money(r["max_cost"]),
                  _money(r["year_end_cost"]), _amt(r["income"]),
                  _amt(r["gain"])) for r in rows]
        lines.extend(_out.fit_table(
            ("SYMBOL", "COUNTRY", "CAT", "MAX COST IN YR", end_col,
             "INCOME", "GAIN(LOSS)"), table,
            aligns=["<", "<", "<", ">", ">", ">", ">"], drop=(5,)))
        lines.append("")
        lines.append("PER COUNTRY — upper-bound aggregates")
        ctable = [(c, _money(v["max_cost"]), _money(v["year_end_cost"]),
                   _amt(v["income"]), _amt(v["gain"]))
                  for c, v in sorted(rep["by_country"].items())]
        lines.extend(_out.fit_table(
            ("COUNTRY", "MAX COST IN YR", end_col, "INCOME", "GAIN(LOSS)"),
            ctable, aligns=["<", ">", ">", ">", ">"]))
    else:
        lines.append("")
        lines.append("No specified foreign property found in the inputs.")
    lines.append("")
    # The cash the test could not count comes first: it decides
    # whether the verdict stands (CA-RPT-20).
    for a, c in cash_acts:
        lines.append(_out.act(a, c))
    for a in acts[:4]:
        lines.append(_out.act(a, cmd))
    if len(acts) > 4:
        lines.append(_out.act(f"{len(acts) - 4} more", cmd))
    lines.append("Not counted: foreign property outside these books (a "
                 f"foreign bank account, cash held elsewhere) — {cmd}")
    return "\n".join(lines)


def render_report(rep: Dict[str, Any]) -> str:
    """The console report, in the house style (docs/output-style.md):
    the filing verdict, the per-property and per-country tables (fitted
    to the width) with each property's notes listed under them, then
    the notes. `--json` is the machine form."""
    from taxjson.lib import out as _out
    cur = rep["base_currency"]
    complete = rep.get("year_complete", True)
    as_of = rep.get("as_of") or f"{rep['year']}-12-31"
    so_far = "" if complete else f" (books through {as_of})"
    lines: List[str] = []

    def para(text: str, indent: str = "", hang: Optional[str] = None):
        lines.extend(_out.wrap(text, None, indent,
                               indent if hang is None else hang))

    def item(text: str):
        para(text, "- ", "  ")

    lines.append(f"T1135 — Foreign Income Verification Statement helper, "
                 f"tax year {rep['year']}, {cur}")
    lines.append("")
    lines.append("FILING REQUIREMENT (total-cost test, ITA 233.3)")
    when = f" on {rep['max_total_date']}" if rep["max_total_date"] else ""
    para(f"Maximum total cost of specified foreign property during "
         f"{rep['year']}{so_far}: {_money(rep['max_total_cost'])} "
         f"{cur}{when}", "  ", "    ")
    if rep["filing_required"]:
        para(f"=> T1135 FILING REQUIRED "
             f"(exceeds {_money(rep['filing_threshold'])} {cur})",
             "  ", "     ")
        if rep["simplified_method_available"]:
            para(f"=> Simplified method (Part A) available "
                 f"(stayed under {_money(rep['detailed_threshold'])} "
                 f"{cur}{' so far' if not complete else ''}; "
                 f"T1135 instructions)", "  ", "     ")
        else:
            para(f"=> Detailed method (Part B) required "
                 f"(reached {_money(rep['detailed_threshold'])} "
                 f"{cur}; T1135 instructions)", "  ", "     ")
    elif not rep.get("total_settled", True):
        # Cash of unknown cost (CA-RPT-20): no verdict below the line.
        para(f"=> NOT SETTLED: below the "
             f"{_money(rep['filing_threshold'])} {cur} threshold "
             f"{'on these books' if complete else f'so far (books through {as_of})'}"
             f", but the cost of some cash in them is unknown (see the "
             f"notes) — the total may be higher", "  ", "     ")
    elif complete:
        para(f"=> below the {_money(rep['filing_threshold'])} {cur} "
             f"threshold on these books — no T1135 required this "
             f"year unless foreign property outside them (see "
             f"notes) takes the total over", "  ", "     ")
    else:
        # In-year (S051-22, S052-15): ITA 233.3 counts cost at ANY time
        # up to Dec 31 — a mid-year 'not required' is not a verdict.
        para(f"=> below the {_money(rep['filing_threshold'])} {cur} "
             f"threshold so far (books through {as_of}) — the test "
             f"runs to Dec 31; re-run after the year ends", "  ", "     ")
    warns: List[str] = []
    if rep.get("expired_options_held"):
        _ex = rep["expired_options_held"]
        warns.append(f"{len(_ex)} long option(s) still held after their "
                     f"expiry date ({', '.join(_ex[:6])}"
                     f"{' ...' if len(_ex) > 6 else ''}) are counted at "
                     f"cost — the books lack their expiry/exercise row.")
    if rep.get("canadian_issuer_symbols"):
        _ci = rep["canadian_issuer_symbols"]
        warns.append(f"{len(_ci)} symbol(s) with a Canadian ISIN on a "
                     f"foreign listing are counted as foreign property "
                     f"({', '.join(_ci[:6])}{' ...' if len(_ci) > 6 else ''})"
                     f" — a Canadian corporation's shares are not; map "
                     f"them `T1135 SYMBOL CA` in ticker.map once confirmed.")
    if rep.get("foreign_issuer_symbols"):
        _fc = rep.get("foreign_issuer_countries") or {}
        _fi = rep["foreign_issuer_symbols"]
        warns.append(f"{len(_fi)} symbol(s) with a foreign ISIN on a "
                     f"Canadian listing are counted as Canadian ("
                     f"{', '.join(f'{s} {_fc.get(s, REVIEW)}' for s in _fi[:6])}"
                     f"{' ...' if len(_fi) > 6 else ''}) — a non-resident "
                     f"corporation's shares are specified foreign property "
                     f"wherever listed; map them `T1135 SYMBOL COUNTRY` in "
                     f"ticker.map once confirmed.")
    _ng = _no_gains_note(rep)
    if _ng:
        warns.append(_ng + " — pass each account's gains file; `taxjson "
                           "t1135` does.")
    # The cash in the books the test could not count (CA-RPT-20).
    for r in rep["properties"]:
        if r.get("cash") and r["unknown_acb"]:
            warns.append(f"{_cash_cell(r)}: its cost is unknown — the "
                         f"FX-on-cash ledger v2 refuses"
                         + (f" ({r['cash_why']})" if r.get("cash_why")
                            else "")
                         + "; `taxjson fx-cash --ledger v2` lists what it "
                           "needs. Its figures are the ledger's partial "
                           "reading, so the total is not settled.")
    for n in rep.get("cash_not_counted") or []:
        cur_ = f"{n['currency']} " if n.get("currency") else ""
        warns.append(f"{n['account']}: its {cur_}cash is not counted — "
                     f"{n.get('why') or '?'}. Add its cost by hand"
                     + (" (the total is not settled)." if n.get("unknown")
                        else "."))
    for w in warns:
        para(_out.label("warning", stream=sys.stdout) + w, "  ", "    ")
    lines.append("")

    rows = rep["properties"]
    # The column is the cost at the last date of the books while the year
    # is open — 'COST AT DEC 31' labelled a September figure (S051-22).
    end_col = "COST AT DEC 31" if complete else f"COST AT {as_of}"
    if rows:
        header = ("SYMBOL", "COUNTRY", "CAT", "MAX COST IN YR", end_col,
                  "INCOME", "GAIN(LOSS)")
        table = []
        # A property's notes are listed under the table, one line per
        # note with the symbols it applies to (a prose column made the
        # rows run off the width).
        by_note: Dict[str, List[str]] = {}
        for r in rows:
            notes = []
            if r.get("cash"):
                notes.append(
                    f"cash held outside Canada ([accounts."
                    f"{r['cash_account']}]): funds, CAT 1, at their cost — "
                    f"Canadian dollars at their amount, another currency "
                    f"at the FX-on-cash ledger v2's pooled average cost"
                    + (" (US dollars and USD stablecoins together)"
                       if r.get("currency") == "USD" else ""))
                if r["unknown_acb"]:
                    notes.append("cash of unknown cost (the ledger "
                                 "refuses): the total is not settled")
            elif r["unknown_acb"]:
                notes.append("unknown ACB (bought before the data) — cost "
                             "understated")
            if r.get("futures") and (r["max_cost"] > 0
                                     or r["year_end_cost"] > 0):
                notes.append("futures — no cost of its own; its cost "
                             "amount is a superficial loss added to it "
                             "(s.53(1)(f))")
            elif r.get("futures"):
                notes.append("futures — cost amount nil")
            if r["country"] == REVIEW:
                notes.append("unclassified — review / add a ticker.map T1135 line")
            elif r["country"] == CRYPTO and r.get("cash"):
                notes.append("a crypto platform's cash — check where held; "
                             "its account's custodian_country in "
                             "taxjson.toml sets the country")
            elif r["country"] == CRYPTO:
                notes.append("crypto — check where held (see the notes)")
            if r["category"] is None:
                notes.append(f"{r['category_from']}s in the interlisted "
                             f"master, not a corporation's shares — set "
                             f"the category: `T1135 SYMBOL COUNTRY CAT` "
                             f"in ticker.map")
            if r.get("custody_account"):
                notes.append(f"a Canadian issuer held with a custodian "
                             f"outside Canada ([accounts."
                             f"{r['custody_account']}] custodian_country): "
                             f"other property (CAT 6) under the "
                             f"custodian's country")
            for n in notes:
                by_note.setdefault(n, []).append(_cash_cell(r))
            table.append((_cash_cell(r), r["country"], _cat(r),
                          _money(r["max_cost"]), _money(r["year_end_cost"]),
                          _amt(r["income"]), _amt(r["gain"])))
        lines.append("PER PROPERTY (taxable accounts only)")
        lines.extend(_out.fit_table(header, table,
                                    aligns=["<", "<", "<", ">", ">", ">",
                                            ">"],
                                    drop=(5,)))
        for n, syms in by_note.items():
            item(f"{', '.join(syms)}: {n}")
        lines.append("")

        lines.append("PER COUNTRY (upper-bound aggregates)")
        cheader = ("COUNTRY", "MAX COST IN YR", end_col,
                   "INCOME", "GAIN(LOSS)")
        ctable = [(c, _money(v["max_cost"]), _money(v["year_end_cost"]),
                   _amt(v["income"]), _amt(v["gain"]))
                  for c, v in sorted(rep["by_country"].items())]
        lines.extend(_out.fit_table(cheader, ctable,
                                    aligns=["<", ">", ">", ">", ">"]))
        lines.append("")
    else:
        lines.append("No specified foreign property found in the inputs.")
        lines.append("")

    lines.append("NOTES")
    _dated = ("trade-dated: tax_date = \"trade\""
              if rep.get("tax_date_basis") == "trade"
              else "settlement-dated")
    item(f"Amounts are COST (ACB-style, {_dated}): the filing "
         "threshold and the 'maximum cost amount' columns. The "
         "category-7 method's month-end FAIR MARKET VALUE is not "
         "fetched: take it from the broker's month-end statements.")
    item("Per-country MAX COST sums per-symbol maxima (an upper "
         "bound); the threshold test uses the true simultaneous total.")
    item("INCOME: the year's dividends and payments in lieu, and the "
         "interest the books tie to a property (an INTEREST row naming "
         "its symbol: a bond's coupon), each dated when paid. Interest "
         "credited on an account's cash is the income of its cash row "
         "where the cash is one (in the currency it was paid in), else "
         "no property's; interest charged is no row's. A USD "
         "stablecoin's reward is the income of its account's USD cash "
         "row.")
    item("CAT is the form's category: "
         + ", ".join(f"{n} {name}" for n, name in CATEGORY_NAMES.items())
         + ". taxjson takes 6 for an option, a futures contract or "
           "crypto and 2 for any other listed security; units of a fund, "
           "trust or partnership may be another category: check them and "
           "set it with `T1135 SYMBOL COUNTRY CAT` in ticker.map.")
    item(".TO/.V/.CN/.NE symbols count as Canadian. A foreign corp on "
         "a Canadian exchange is still SFP (`T1135 SYMBOL <ISO3>` in "
         "ticker.map); a Canadian corp on a US exchange is not "
         "(`T1135 SYMBOL CA`), unless a custodian outside Canada holds "
         "it (`custodian_country` of its account in taxjson.toml: "
         "category 6 under that country).")
    if rep.get("futures_symbols"):
        item(f"Futures contracts ({len(rep['futures_symbols'])}: "
             f"{', '.join(rep['futures_symbols'][:6])}"
             f"{' ...' if len(rep['futures_symbols']) > 6 else ''}) "
             "carry no cost amount of their own (margin is a deposit): "
             "their notional is out of the cost columns and the "
             "threshold, and only a superficial loss added to one "
             "(s.53(1)(f)) is its cost; options on futures count at "
             "their premium.")
    if rep.get("short_only_symbols"):
        _so = rep["short_only_symbols"]
        item(f"Held only short in {rep['year']} ({', '.join(_so[:6])}"
             f"{' ...' if len(_so) > 6 else ''}): a written option or a "
             f"short sale is an obligation, not property held, so it is "
             f"not a row and adds nothing to the totals; its gain or "
             f"loss is in the capital gains (`tjs gains`). A position "
             f"held long at any time in the year keeps its row.")
    if rep.get("crypto_symbols"):
        item("CRYPTO rows: crypto on a foreign exchange is generally "
             "SFP under that exchange's country (`T1135 SYMBOL <ISO3>` "
             "in ticker.map); on a Canadian platform it may not be "
             "(`T1135 SYMBOL CA` once checked). Until then it counts toward "
             "the threshold.")
    item("Registered accounts (RRSP/TFSA/...) are excluded by law and "
         "were not read.")
    item("Only these brokerage books: foreign bank accounts, cash held "
         "elsewhere, certificated shares or a foreign property count "
         "toward the same threshold; add their cost by hand.")
    _ca = rep.get("cash_accounts") or {}
    item("Cash in the books is counted where an account holds it outside "
         "Canada: a crypto account's (country CRYPTO unless its "
         "custodian_country says) and an account whose custodian_country "
         "is outside Canada — a row per account and currency, CAT 1, at "
         "its cost day by day (Canadian dollars where the books give the "
         "balance; another currency at the FX-on-cash ledger v2's pooled "
         "cost); cash at a broker in Canada is not foreign property"
         + (f". Read for: {', '.join(f'{a} ({c})' for a, c in sorted(_ca.items()))}."
            if _ca else "."))
    item("US property held only through Canadian funds is not US-situs "
         "property here.")
    item("Not tax advice; reconcile against broker statements before "
         "filing.")
    return "\n".join(lines)


# ---------------------------------------------------------------- main

def _custodian_arg(text: str) -> Tuple[str, Optional[str]]:
    """--custodian ACCOUNT=COUNTRY: (account, ISO alpha-3, None for
    Canada)."""
    from taxjson.lib.t1135_country import parse_custodian_country
    acct, sep, country = str(text).partition("=")
    if not sep or not acct.strip():
        raise argparse.ArgumentTypeError(
            f"{text!r}: expected ACCOUNT=COUNTRY, e.g. margin=USA")
    try:
        return acct.strip(), parse_custodian_country(country)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"{text!r}: {e}")


@guard_main("taxjson-t1135")
def main(argv: Optional[List[str]] = None) -> int:
    _doc = __doc__ or ""
    _cav = _doc[_doc.find("Caveats printed"):_doc.find("Usage:")].rstrip()
    parser = argparse.ArgumentParser(
        prog="taxjson-t1135",
        description="CRA T1135 foreign-property helper: filing-threshold "
                    "test + per-property/per-country tables from taxjson "
                    "base/gains files.",
        # The docstring's caveats, as it promises (S052-04).
        epilog=_cav or None,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", type=Path, metavar="FILE",
                        help="TAXABLE <account>_base.json files (full "
                             "history, base currency)")
    parser.add_argument("--gains", action="append", type=Path, default=[],
                        help="Year-scoped <account>_gains.json for the "
                             "income and gain(loss) columns (repeatable)")
    parser.add_argument("--year", type=tax_year, required=True,
                        help="Tax year")
    parser.add_argument("--map", type=Path, default=None,
                        help="The ticker.map whose `T1135 SYMBOL COUNTRY` "
                             "lines override a symbol's domicile")
    parser.add_argument("--base-currency", default="CAD",
                        type=norm_currency,
                        help="Label for amounts (default: CAD)")
    parser.add_argument("--threshold", type=positive_float_arg, default=FILING_THRESHOLD,
                        help="Filing threshold (default: 100000)")
    parser.add_argument("--detailed-threshold", type=positive_float_arg,
                        default=DETAILED_THRESHOLD,
                        help="Detailed-method threshold (default: 250000)")
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text")
    parser.add_argument("--tax-date", choices=("settle", "trade"),
                        default="settle",
                        help="Date basis the project's gains files use "
                             "(the wrapper passes the project's): the "
                             "gain column and the year-end position "
                             "follow it (default: settle, CRA).")
    parser.add_argument("--incomplete-history", type=Path, default=None,
                        metavar="DIR",
                        help="the project folder (its .tt OPENING "
                             "cost=unknown lines): add the same "
                             "missing-history openings the gains stage "
                             "adds (the project wrapper passes it)")
    # The full-history superficial-loss pass (S008-07) needs the same
    # inputs the filing pipeline's wash pass has; the wrapper passes the
    # project's.
    parser.add_argument("--sheltered", action="append", type=Path,
                        default=[], metavar="FILE",
                        help="Registered accounts' base book (wash-sale "
                             "context only — never counted as foreign "
                             "property); repeatable")
    parser.add_argument("--option-premium-timing", choices=["grant", "close"],
                        default=None,
                        help="Written-option premium timing the gains "
                             "files use (default close, with a note: "
                             "`taxjson t1135` passes the project's)")
    parser.add_argument("--option-grant-since", type=tax_year, default=None,
                        metavar="YEAR",
                        help="With grant timing: contracts written before "
                             "YEAR keep close timing")
    parser.add_argument("--transfers-as-acquisitions", action="store_true",
                        help="Count a sheltered account's unmatched TRANSFER rows as "
                             "acquisitions/disposals for the superficial-loss / wash-sale "
                             "window (strict; [settings] transfers_as_acquisitions = true). "
                             "Default: a custody move, held but never a purchase.")
    parser.add_argument("--option-buyback-wash", action="store_true",
                        help="Grant timing: a written option's buy-back "
                             "loss can be superficial")
    from taxjson.lib.pipeline import (add_loss_override_args,
                                      loss_overrides_from_args as _lo_args)
    add_loss_override_args(parser)
    parser.add_argument("--corporate-distribution", action="append",
                        default=None, metavar="SYMBOL",
                        help="A Canadian issuer whose distributions are a "
                             "corporation's (ROC dated when paid) in the "
                             "full-history pass; repeatable ([settings] "
                             "corporate_distributions)")
    parser.add_argument("--custodian", action="append", default=[],
                        type=_custodian_arg, metavar="ACCOUNT=COUNTRY",
                        help="The rows of ACCOUNT (their `account`) are "
                             "held with a custodian in COUNTRY (ISO "
                             "alpha-3; CAN: Canada, the default): its "
                             "Canadian issuers' shares are foreign "
                             "property, category 6, under COUNTRY; "
                             "repeatable (`taxjson t1135` passes each "
                             "account's custodian_country)")
    parser.add_argument("--cash", type=Path, default=None, metavar="FILE",
                        help="The cash the accounts hold outside Canada, "
                             "day by day at its cost (a JSON document "
                             "`taxjson t1135` writes from the FX-on-cash "
                             "ledger v2, lib/t1135_cash): a row per "
                             "account and currency, category 1, counted "
                             "toward the threshold")
    # Retired with the replay it chose (backlog TJ-0417): the cost
    # amounts are the engine's own pools. Accepted so old scripts run.
    parser.add_argument("--year-wash-only", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--brief", action="store_true",
                        help="The essentials only (what `taxjson t1135` prints "
                             "without --details): the notes and each "
                             "message's detail are left out")
    args = parser.parse_args(argv)
    global _DETAILS
    _DETAILS = not args.brief
    if args.year_wash_only:
        emit_line("taxjson-t1135: note: --year-wash-only is ignored — the "
                  "cost amounts are the gains engine's own pools, every "
                  "year's denied losses in them.", file=sys.stderr)

    extra = [args.incomplete_history] if args.incomplete_history else []
    extra += list(args.sheltered)
    if args.cash is not None:
        extra.append(args.cash)
    for p in args.files + args.gains + extra:
        if not p.exists():
            print(f"taxjson-t1135: no such file: {p}", file=sys.stderr)
            return 2
    if args.map is not None and not args.map.exists():
        print(f"taxjson-t1135: no such ticker.map: {args.map}",
              file=sys.stderr)
        return 2

    overrides = load_overrides(args.map)
    categories = load_categories(args.map)
    cash = load_cash(args.cash)
    if cash is not None and str(cash.get("basis") or "settle") \
            != args.tax_date:
        # Its days on another basis than the walk's: the cash that paid
        # for shares counted with them (backlog TJ-0627).
        from taxjson.lib.out import error as _error
        _error(f"{args.cash}: the T1135 cash document is dated on the "
               f"{cash.get('basis') or 'settle'} basis, --tax-date is "
               f"{args.tax_date}", prog="taxjson-t1135",
               details=["`taxjson t1135` writes it on the project's "
                        "tax_date."])
        return 2
    # The interlisted pairs (lib/tobase_map): a foreign issuer's country,
    # the units and funds whose category is the user's, and the renames
    # a user T1135 key follows.
    issuer_countries: Dict[str, str] = {}
    kinds: Dict[str, str] = {}
    key_renames: Dict[str, str] = {}
    if args.map is not None:
        from taxjson.lib.tobase_map import t1135_countries, t1135_kinds
        issuer_countries = t1135_countries(args.map)
        kinds = t1135_kinds(args.map)
        try:
            from taxjson.bin.taxjson_ticker_map import (_parse_map_file,
                                                        merge_renames)
            key_renames = {str(k).upper(): str(v).upper() for k, v in
                           merge_renames(_parse_map_file(args.map)[0],
                                         to_base=True).items()}
        except Exception:                               # noqa: BLE001
            key_renames = {}
    if args.option_premium_timing is None:
        # The T1135 test is Canadian; a Canada project's run uses grant
        # timing from the project year — say so instead of silently
        # disagreeing with it, as taxjson-gains does (re-audit A2-1361).
        emit_line("taxjson-t1135: note: --option-premium-timing not given — "
                  "using close timing. `taxjson run` on a Canada project uses "
                  "grant timing from the project year; pass "
                  "--option-premium-timing grant --option-grant-since YEAR "
                  "to match it.", file=sys.stderr)
        args.option_premium_timing = "close"
    if args.base_currency.upper() != "CAD":
        # The thresholds are CAD amounts (ITA s.233.3): a USD book
        # tested against "100,000 USD" read 80,000 USD (about 110,000
        # CAD) as 'no T1135 required' at exit 0 (S052-17, re-audit
        # A2-0660). Refused, never relabelled.
        print(f"taxjson-t1135: --base-currency "
              f"{args.base_currency.upper()}: the T1135 test is in CAD "
              f"(ITA s.233.3) — pass books converted to CAD (a Canada "
              f"project's work/<account>_base.json; `taxjson t1135` "
              f"does).", file=sys.stderr)
        return 2
    try:
        rep = build_report(args.files, args.gains, args.year, overrides,
                           args.base_currency.upper(),
                           threshold=args.threshold,
                           detailed_threshold=args.detailed_threshold,
                           phantoms=args.incomplete_history,
                           tax_date=args.tax_date,
                           sheltered_paths=args.sheltered,
                           option_timing=dict(
                               option_premium_timing=(
                                   args.option_premium_timing),
                               option_grant_since=args.option_grant_since,
                               option_buyback_loss_superficial=(
                                   args.option_buyback_wash),
                               transfers_as_acquisitions=(
                                   args.transfers_as_acquisitions)),
                           issuer_countries=issuer_countries,
                           key_renames=key_renames,
                           categories=categories, kinds=kinds,
                           custody=dict(args.custodian), cash=cash,
                           income_rules=dict(corporate_distributions=tuple(
                               args.corporate_distribution or ()),
                               # The ALLOWLOSS positions the books took
                               # (lib/loss_overrides): no ACB addition.
                               loss_overrides=_lo_args(args)))
    except (UnreadableGains, CurrencyMismatch, WrongCountryGains) as e:
        print(e.code, file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        from taxjson.lib.out import error as _error
        _error(f"could not read the base books: {e}",
               prog="taxjson-t1135",
               details=["Re-run `taxjson run` to rebuild them."])
        return 2
    if args.json:
        from taxjson.lib.json_input import dump_filing_json
        dump_filing_json(rep, sys.stdout)
        print()
    elif args.brief:
        print(render_brief(rep))
    else:
        print(render_report(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
