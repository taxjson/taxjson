#!/usr/bin/env python3
"""
taxjson_t1135.py

CRA Form T1135 (Foreign Income Verification Statement) helper.

Answers the two questions every Canadian filer with foreign securities has:

  1. Do I need to file T1135 this year?  The test is COST-based: total cost
     amount of all specified foreign property (SFP) exceeding CAD $100,000
     at ANY time in the year (ITA 233.3). This tool replays the full
     transaction history of the taxable accounts in base currency, tracks
     per-property cost through the year, and reports the maximum total.

  2. If yes, what goes on the form?  Per foreign property: maximum cost
     amount during the year, cost amount at year end, income (dividends /
     payments in lieu), and gain (loss) on disposition — plus per-country
     aggregates for the "held with a Canadian registered securities dealer"
     category-7 style of reporting.

Inputs are the pipeline's TAXABLE `<account>_base.json` files (full history,
already converted to base currency) and, for the income / gain columns, the
year-scoped `<account>_gains.json` (or `_gains_wash.json`) files. Registered
accounts (RRSP/TFSA/...) are excluded from SFP by law — do not pass them as
FILEs; `--sheltered` gives them as superficial-loss context only.

Cost amount is the ACB the gains engine computes: one full-history engine
pass finds every superficial loss denied in any year, and its s.53(1)(f)
addition joins the walk where the engine applied it.

Domicile classification is by market suffix (.US → USA, .L → GBR,
.AX → AUS; .TO/.V/.CN/.NE → Canadian, i.e. not SFP), overridable per symbol
via a `t1135.map` file:

    # symbol  country      (ISO-3 code, or CA/CANADA/EXCLUDE to exclude)
    ENB.US    CA           # interlisted Canadian corp held on NYSE — not SFP
    GLXY.TO   USA          # foreign corp listed on TSX — still SFP

Caveats printed with every report (also see --help):
  - Amounts are COST (ACB-style). That is the correct basis for the filing
    threshold and for the "maximum cost amount" columns; the category-7
    detailed method wants month-end FAIR MARKET VALUE, which needs price
    data this tool does not fetch.
  - Symbols with no market suffix (typically exchange-held crypto) are
    bucketed as country `CRYPTO` and counted toward the threshold —
    crypto held on a foreign exchange is generally SFP; check where it
    is held and map it (`SYMBOL <ISO3>` or `SYMBOL CA`) in t1135.map.

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
        [--map t1135.map] [--threshold 100000] [--json]

Or through the project wrapper (recommended):  `taxjson t1135`
"""

import argparse
import contextlib
import io
import json
import re
import sys
from decimal import Decimal
from pathlib import Path
from taxjson.lib.cli_diag import guard_main, read_text_utf8, tax_year
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
# listing exchange for the retail case. Interlisted exceptions go in
# t1135.map.
_SUFFIX_COUNTRY: Dict[str, Optional[str]] = {
    "US": "USA",
    "L": "GBR",
    "AX": "AUS",
}
# Canadian exchanges — not specified foreign property. The shared set
# (lib/income_dating): .VN was a '??' REVIEW here (audit A2-1077).
from taxjson.lib.income_dating import CA_LISTING_SUFFIXES as _CA_VENUES
_SUFFIX_COUNTRY.update({_v: None for _v in _CA_VENUES})

# Actions that never move a position or its cost. Mirrors the engines'
# non-capital skip list (core.py) minus ADJUST/SPLIT/OPENING_BALANCE which
# we do consume.
_NON_CAPITAL = ("DIVIDEND", "DIVIDEND_IN_LIEU", "TAX", "INTEREST", "FEE",
                "TRANSFER")

_QTY_EPS = 1e-6

# Sentinel country code for symbols we cannot classify (an unknown market
# suffix). Deliberately ugly so it reads as "review me".
REVIEW = "??"

# t1135.map COUNTRY vocabulary (S051-16): an ISO 3166-1 alpha-3 code or
# one of the "not foreign property" words; anything else (EXCLUDED, CDN,
# NOT-FOREIGN) became a bogus country and flipped the verdict.
_NOT_FOREIGN_WORDS = ("CA", "CAN", "CANADA", "EXCLUDE")
_ISO3 = frozenset("""
ABW AFG AGO AIA ALA ALB AND ARE ARG ARM ASM ATA ATF ATG AUS AUT AZE BDI
BEL BEN BES BFA BGD BGR BHR BHS BIH BLM BLR BLZ BMU BOL BRA BRB BRN BTN
BVT BWA CAF CAN CCK CHE CHL CHN CIV CMR COD COG COK COL COM CPV CRI CUB
CUW CXR CYM CYP CZE DEU DJI DMA DNK DOM DZA ECU EGY ERI ESH ESP EST ETH
FIN FJI FLK FRA FRO FSM GAB GBR GEO GGY GHA GIB GIN GLP GMB GNB GNQ GRC
GRD GRL GTM GUF GUM GUY HKG HMD HND HRV HTI HUN IDN IMN IND IOT IRL IRN
IRQ ISL ISR ITA JAM JEY JOR JPN KAZ KEN KGZ KHM KIR KNA KOR KWT LAO LBN
LBR LBY LCA LIE LKA LSO LTU LUX LVA MAC MAF MAR MCO MDA MDG MDV MEX MHL
MKD MLI MLT MMR MNE MNG MNP MOZ MRT MSR MTQ MUS MWI MYS MYT NAM NCL NER
NFK NGA NIC NIU NLD NOR NPL NRU NZL OMN PAK PAN PCN PER PHL PLW PNG POL
PRI PRK PRT PRY PSE PYF QAT REU ROU RUS RWA SAU SDN SEN SGP SGS SHN SJM
SLB SLE SLV SMR SOM SPM SRB SSD STP SUR SVK SVN SWE SWZ SXM SYC SYR TCA
TCD TGO THA TJK TKL TKM TLS TON TTO TUN TUR TUV TWN TZA UGA UKR UMI URY
USA UZB VAT VCT VEN VGB VIR VNM VUT WLF WSM YEM ZAF ZMB ZWE
""".split())
# Bucket for suffix-less symbols — equity parsers always stamp a market
# suffix, so these are crypto. Crypto held on a foreign exchange is
# generally specified foreign property (funds/intangibles held outside
# Canada); where it is held decides the country, which the books don't
# carry — so it is counted toward the threshold and flagged for review.
CRYPTO = "CRYPTO"


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
    phantoms.json) each file first gets the phantom OPENING_BALANCE rows
    the gains stage synthesizes for it (phantom_holdings.
    synthesize_openings, per book like pipeline.prepare_books) — without
    them a sale with cut-off history opened a fake short that the next
    real purchases covered at zero cost, so their cost never reached the
    max-cost or Dec-31 columns (R1-321)."""
    txs: List[Dict[str, Any]] = []
    ph = None
    if phantoms is not None:
        from taxjson.lib.phantom_holdings import load_phantoms
        ph = load_phantoms(phantoms)
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
            from taxjson.lib.phantom_holdings import synthesize_openings
            objs = [coerce_transaction_row(t, i, str(p))
                    for i, t in enumerate(rows)]
            objs, _log = synthesize_openings(objs, ph)
            rows = [o.to_dict() for o in objs]
        txs.extend(rows)
    return txs


def load_overrides(path: Optional[Path]) -> Dict[str, Optional[str]]:
    """t1135.map: `SYMBOL COUNTRY` per line, '#' comments. COUNTRY of
    CA/CAN/CANADA/EXCLUDE means "not foreign property"."""
    overrides: Dict[str, Optional[str]] = {}
    if path is None:
        return overrides
    # utf-8-sig: a BOM (Windows editors) became part of the first key
    # and silently disabled that override (S008-03, S051-18).
    for lineno, line in enumerate(
            read_text_utf8(path).splitlines(), 1):
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) != 2:
            print(f"warning: {path.name}:{lineno}: expected `SYMBOL COUNTRY`, "
                  f"got {stripped!r} — line ignored", file=sys.stderr)
            continue
        # Book symbols are upper case: a lower-case 'btc CA' never
        # matched and was ignored without a word (R1-212).
        symbol, code = parts[0].upper(), parts[1].upper()
        if code in _NOT_FOREIGN_WORDS:
            overrides[symbol] = None
        elif code in _ISO3:
            overrides[symbol] = code
        else:
            import difflib
            near = difflib.get_close_matches(
                code, list(_NOT_FOREIGN_WORDS) + sorted(_ISO3), n=1,
                cutoff=0.6)
            hint = f" — did you mean {near[0]}?" if near else ""
            print(f"warning: {path.name}:{lineno}: {parts[1]!r} is not an "
                  f"ISO 3166 alpha-3 country code or "
                  f"{'/'.join(_NOT_FOREIGN_WORDS)}{hint} — line ignored "
                  f"({symbol} keeps its listing-suffix country)",
                  file=sys.stderr)
    return overrides


# ---------------------------------------------------------------- classify

def classify_country(symbol: str,
                     overrides: Dict[str, Optional[str]]) -> Optional[str]:
    """Country code for a symbol, or None when it is not specified foreign
    property (Canadian, or user-excluded). Options classify by their own
    market suffix — the OCC symbol carries it (AAPL250117C00150000.US)."""
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


# ---------------------------------------------------------------- cost walk

class _Pool:
    __slots__ = ("qty", "cost", "tainted", "max_cost_in_year")

    def __init__(self) -> None:
        self.qty = 0.0
        self.cost = Decimal(0)
        self.tainted = False           # phantom OPENING_BALANCE — unknown ACB
        self.max_cost_in_year = 0.0

    def cost_amount(self) -> float:
        """Cost amount of PROPERTY HELD: a short position is a liability,
        not property, so only long cost counts."""
        if self.qty <= _QTY_EPS:
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


def _place_after(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Move each ADJUST carrying `wash_after` (a superficial-loss
    addition the engine applies right after the losing sale — its
    replacement was bought before the sale) to immediately after that
    sale's row, as core._place_wash_adjusts does: every later row,
    including another fill at the same second, sees it."""
    moved = [t for t in rows if t.get("wash_after")]
    if not moved:
        return rows
    ids = {str(t.get("id")) for t in rows if t.get("id") is not None}
    after: Dict[str, List[Dict[str, Any]]] = {}
    for t in moved:
        if t["wash_after"] in ids:
            after.setdefault(t["wash_after"], []).append(t)
    placed = {id(t) for lst in after.values() for t in lst}
    out: List[Dict[str, Any]] = []
    for t in rows:
        if id(t) in placed:
            continue
        out.append(t)
        tid = str(t.get("id")) if t.get("id") is not None else None
        if tid in after and not t.get("wash_after"):
            out.extend(after.pop(tid))
    return out


def _assign_underlying_resolver(transactions: List[Dict[str, Any]]):
    """resolve(row dict) -> the stock symbol an option ASSIGN row's
    premium folds into, by the engine's resolver (same account, class /
    futures-month spelling, a stock trade near the assignment), or None.
    The engine's notes are its own run's business; its ambiguity
    warning is passed on."""
    import contextlib
    import io
    from types import SimpleNamespace
    from taxjson.lib.core import _make_assign_underlying_resolver

    def _ns(t: Dict[str, Any]) -> SimpleNamespace:
        return SimpleNamespace(symbol=str(t.get("symbol") or ""),
                               action=str(t.get("action") or "").upper(),
                               account=t.get("account") or "",
                               date=t.get("date") or "")

    resolver = _make_assign_underlying_resolver(
        [_ns(t) for t in transactions if t.get("symbol")],
        lambda t: t.date)

    def resolve(tx: Dict[str, Any]) -> Optional[str]:
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            out = resolver(_ns(tx))
        for ln in buf.getvalue().splitlines():
            if ln.startswith("warning:"):
                print(f"taxjson-t1135: {ln}", file=sys.stderr)
        return out

    return resolve


def walk_costs(transactions: List[Dict[str, Any]], year: int,
               overrides: Dict[str, Optional[str]],
               tax_date: str = "settle",
               today: Optional[str] = None) -> Dict[str, Any]:
    """Replay full history in base currency; return per-symbol cost stats
    for `year` plus the maximum TOTAL foreign cost observed in the year
    (the ITA 233.3 filing-threshold test). `overrides` is updated with
    rename targets of overridden symbols (a ticker change keeps its
    t1135.map classification, S051-17)."""
    year_start = f"{year}-01-01"
    year_end = f"{year}-12-31"
    from taxjson.lib.core import is_option_symbol
    # Underlyings that trade as stock here: only their assignments fold
    # the option premium into the shares (a cash-settled index option
    # has no stock leg — same test as the engine).
    # The engine's own resolver (core._make_assign_underlying_resolver):
    # a root that drops the share class (BRKB -> BRK.B.US, RCI ->
    # RCI.B.TO) resolves to the one stock line the account trades at
    # the assignment, so the premium folds into those shares as in the
    # books (re-audit A2-0328, A2-1106). It is None for a cash-settled
    # index option or a missing stock leg.
    _resolve_underlying = _assign_underlying_resolver(transactions)
    # Splits applied so far per pool: a pre-split execution that
    # settles after the split is re-denominated like the engine does
    # (S008-06).
    applied_splits: Dict[str, List[Tuple[str, float]]] = {}

    pools: Dict[str, _Pool] = {}
    max_total = 0.0
    max_total_date = ""
    baseline_taken = False
    # Plain futures contracts seen (S008-04): a futures position's cost
    # amount is nil — nothing is paid to open it (initial margin is a
    # deposit, variation margin settles daily) — so its notional never
    # enters the pools or the threshold test. Options on futures stay in
    # the walk at their premium cost.
    futures_seen: set = set()

    def total_foreign_cost() -> float:
        return sum(p.cost_amount() for s, p in pools.items()
                   if classify_country(s, overrides) is not None)

    def snapshot(date: str) -> None:
        nonlocal max_total, max_total_date
        for s, p in pools.items():
            if classify_country(s, overrides) is None:
                continue
            c = p.cost_amount()
            if c > p.max_cost_in_year:
                p.max_cost_in_year = c
        tot = total_foreign_cost()
        if tot > max_total:
            max_total, max_total_date = tot, date

    # One corporate split = one application. Every taxable account's
    # parser emits its own SPLIT row for the same event, and this walk
    # pools symbol-globally across all of them — undeduped, two
    # accounts through a 2:1 split scaled the pool 4x (the engines
    # dedupe the same way, core._dedupe_corporate_splits).
    from taxjson.lib.corporate_timeline import split_seen
    seen_splits: set = set()
    last_date = ""
    for tx in _place_after(sorted(transactions,
                                  key=lambda t: _sort_key(t, tax_date))):
        date = _tx_date(tx, tax_date)
        if date > year_end:
            break
        last_date = max(last_date, date)
        if (tx.get("action") or "").upper() == "SPLIT":
            # Same split booked on two dates by two brokers = one event.
            if split_seen(seen_splits, tx.get("symbol") or "",
                          tx.get("date") or "", tx.get("quantity"),
                          tx.get("symbol_new") or "") is not None:
                continue
        # First event inside the year: the Jan-1 state (built from all
        # prior events) itself counts toward the in-year maximum.
        if date >= year_start and not baseline_taken:
            snapshot(year_start)
            baseline_taken = True

        action = (tx.get("action") or "").upper()
        typ = (tx.get("type") or "").lower()
        if action in _NON_CAPITAL or typ in ("dividend", "dividend_in_lieu",
                                             "tax", "interest", "fee"):
            continue
        symbol = tx.get("symbol") or ""
        if not symbol:
            continue
        if is_plain_future(symbol):
            if date >= year_start \
                    and classify_country(symbol, overrides) is not None:
                futures_seen.add(symbol)
            continue
        qty = float(tx.get("quantity") or 0.0)
        net = float(tx.get("net_amount") or 0.0)

        pool = pools.setdefault(symbol, _Pool())

        if action == "OPENING_BALANCE":
            # Phantom opening: shares with unknown ACB. Quantity enters at
            # cost 0 and the pool is flagged so the report can say the cost
            # figures for this symbol are understated.
            pool.qty += qty
            pool.tainted = True
        elif action == "SPLIT":
            ratio = qty
            new_symbol = tx.get("symbol_new") or ""
            if ratio <= 0 and not (new_symbol
                                   and new_symbol != symbol):
                # Non-positive ratio with no rename: nothing to do.
                # A zero-ratio RENAME must still migrate the pool —
                # both engines honor falsy-ratio renames, and dropping
                # the row here stranded the position under the old
                # ticker for the threshold test.
                continue
            if ratio > 0:
                pool.qty *= ratio
                if ratio != 1 and not (new_symbol
                                       and new_symbol != symbol):
                    applied_splits.setdefault(symbol, []).append(
                        (tx.get("date_settle") or tx.get("date") or "",
                         tx.get("time") or "00:00:00", ratio))
            if new_symbol and new_symbol != symbol:
                if symbol in overrides and new_symbol not in overrides:
                    overrides[new_symbol] = overrides[symbol]
                dest = pools.setdefault(new_symbol, _Pool())
                dest.qty += pool.qty
                dest.cost += pool.cost
                dest.tainted = dest.tainted or pool.tainted
                # Carry the year-max so a mid-year rename doesn't reset
                # the "maximum cost during the year" column.
                dest.max_cost_in_year = max(dest.max_cost_in_year,
                                            pool.max_cost_in_year)
                pools[symbol] = _Pool()
        elif action == "ADJUST":
            # The engine's ADJUST rules (CA-ACB-06/07/14), so the cost
            # amount is the engine's ACB (CA-RPT-12; re-audit A2-0034,
            # A2-0115, A2-0321): a return of capital on an EMPTY pool is
            # a s.40(3) gain of its year and never reaches the next
            # position's cost; one beyond the ACB leaves it nil; on a
            # short it is the short seller's payment (no cost amount —
            # cost_amount() clamps shorts to 0). A basis increase on an
            # empty pool still joins the next purchase (CA-ACB-13), and
            # a superficial-loss addition (WASH_) is applied as booked.
            _adj = Decimal(str(net))
            _wash = str(tx.get("id") or "").startswith("WASH_")
            if not _wash and abs(pool.qty) <= _QTY_EPS and net < -0.005:
                _adj = Decimal(0)
            elif not _wash and pool.qty < -_QTY_EPS:
                _adj = -_adj
            pool.cost += _adj
            if not _wash and pool.qty > _QTY_EPS and pool.cost < 0:
                pool.cost = Decimal(0)
        else:
            # BUYSELL / ASSIGN / EXERCISE-shaped rows: average-cost pool,
            # same conventions as the Canada engine (buy cost added =
            # |net_amount|, which parsers emit fee-inclusive).
            # Only an exact zero is skipped, as in the Canada engine
            # (R1-24): the 1e-6 share epsilon dropped staking rewards
            # of under a millionth of a coin and their cost (S052-02).
            if qty == 0:
                continue
            if tax_date != "trade" and tx.get("date") and \
                    tx.get("date_settle") and \
                    tx["date"] < tx["date_settle"]:
                _exec = (tx["date"], tx.get("time") or "00:00:00")
                for spd, spt, r in applied_splits.get(symbol, ()):
                    if _exec < (spd, spt) and spd < tx["date_settle"]:
                        # Executed before a split this pool has already
                        # taken (it settles after): its units are
                        # pre-split — re-denominated as the engine does
                        # (core: split inside a settle lag).
                        qty *= r
            fold = None
            if action == "ASSIGN" and is_option_symbol(symbol):
                und = _resolve_underlying(tx)
                _m = re.search(r"\d{6}([CP])\d{8}", symbol)
                right = _m.group(1) if (und and _m) else ""
                closing_units = min(abs(qty), abs(pool.qty))
                if (und and closing_units > _QTY_EPS
                        and ((pool.qty > 0 and right == "C")
                             or (pool.qty < 0 and right == "P"))):
                    # s.49(3) / 49(3.1): an exercised long call's cost
                    # is added to the shares acquired; an assigned
                    # written put's premium is deducted from their cost.
                    # (A written call / long put disposes of shares —
                    # the premium moves proceeds, not remaining cost.)
                    per = pool.cost / Decimal(str(abs(pool.qty)))
                    amt = per * Decimal(str(closing_units))
                    fold = (und, amt if pool.qty > 0 else -amt)
            is_opening = (pool.qty > _QTY_EPS and qty > 0) or \
                         (pool.qty < -_QTY_EPS and qty < 0) or \
                         (abs(pool.qty) <= _QTY_EPS)
            if is_opening:
                pool.cost += Decimal(str(abs(net)))
                pool.qty += qty
            else:
                closing = min(abs(qty), abs(pool.qty))
                if abs(pool.qty) > _QTY_EPS:
                    per_unit = pool.cost / Decimal(str(abs(pool.qty)))
                else:
                    per_unit = Decimal(0)
                # Long pool: average cost leaves with the shares sold.
                # Short pool: `cost` holds opening proceeds and shrinks the
                # same way on cover — it never surfaces in the report,
                # since cost_amount() clamps short positions to 0 (a short
                # is a liability, not property held).
                pool.cost -= per_unit * Decimal(str(closing))
                if pool.qty > 0:
                    pool.qty -= closing
                else:
                    pool.qty += closing
                if abs(pool.qty) <= _QTY_EPS:
                    pool.qty = 0.0
                    pool.cost = Decimal(0)
                    pool.tainted = False   # drained pool: taint clears
                remainder = abs(qty) - closing
                if remainder > _QTY_EPS:
                    # Crossed zero: the excess opens a position in the
                    # opposite direction at proportional cost.
                    frac = remainder / abs(qty)
                    pool.cost += Decimal(str(abs(net) * frac))
                    pool.qty += remainder * (1 if qty > 0 else -1)
            if fold is not None:
                # R1-202 / R1-276: the premium follows the shares (the
                # option leg sorts before its stock leg, so this lands
                # before the stock purchase's own cost).
                pools.setdefault(fold[0], _Pool()).cost += fold[1]

        if date >= year_start:
            snapshot(date)

    if not baseline_taken:
        # No event fell inside the year — the standing Jan-1 position is
        # still the year's maximum.
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
        if p.qty > _QTY_EPS and is_option_symbol(s)
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
        still_held_unknown = p.tainted and p.qty > _QTY_EPS
        if p.max_cost_in_year <= 0 and year_end_cost <= 0 \
                and not still_held_unknown:
            continue
        per_symbol[s] = {
            "country": country,
            "max_cost": round(p.max_cost_in_year, 2),
            "year_end_cost": round(year_end_cost, 2),
            "unknown_acb": p.tainted,
        }
    return {
        "per_symbol": per_symbol,
        "max_total_cost": round(max_total, 2),
        "max_total_date": max_total_date,
        "futures_symbols": sorted(futures_seen),
        "expired_options_held": expired_held,
        "last_date": last_date,
    }


# ---------------------------------------------------------------- income/gains

class UnreadableGains(SystemExit):
    pass


class CurrencyMismatch(SystemExit):
    pass


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


def join_income_gains(gains_paths: List[Path], year: int,
                      overrides: Optional[Dict[str, Optional[str]]] = None,
                      tax_date: str = "settle"
                      ) -> Dict[str, Dict[str, float]]:
    """Per-symbol dividend+PIL income and realized gain(loss) for `year`,
    from the pipeline's (already year-scoped) gains files. Defensively
    re-filters by year so a hand-run full-history gains file also works.

    Phantom-basis dispositions (the pipeline's manual_reporting_required
    rows — their 'tainted' key is popped there, so a 'tainted' test alone
    never saw them: audit R1-199) are excluded from the gain column with
    a warning naming them. With `overrides`, only T1135-scope (foreign)
    symbols are named."""
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
        for e in data.get("transactions", []):
            date = _tx_date(e, tax_date)
            if not date.startswith(ystr):
                continue
            symbol = e.get("symbol") or ""
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
                # Tainted dispositions (phantom zero-cost basis) carry a
                # fabricated gain — form-export and carryover exclude
                # them with a warning; the T1135 GAIN(LOSS) column must
                # not silently include what its sibling tools refuse.
                if e.get("tainted"):
                    tainted_skipped += 1
                    continue
                rec["gain"] += float(e.get("gain") or 0.0)
        for e in data.get("manual_reporting_required") or []:
            date = _tx_date(e, tax_date)
            symbol = e.get("symbol") or ""
            if not str(date).startswith(ystr) or not symbol:
                continue
            if (overrides is not None
                    and classify_country(symbol, overrides) is None):
                continue            # domestic: not a T1135 property
            tainted_skipped += 1
            manual_syms.append(symbol)
    if tainted_skipped:
        _named = (f" ({', '.join(sorted(set(manual_syms)))})"
                  if manual_syms else "")
        print(f"warning: {tainted_skipped} tainted disposition(s) with "
              f"phantom cost basis{_named} EXCLUDED from the T1135 "
              f"gain(loss) column — resolve the missing history and "
              f"re-run (matches form-export/carryover).", file=sys.stderr)
    return out


def _read_wash_sales(gains_paths: List[Path]) -> List[Dict[str, Any]]:
    """The `wash_sales` records of the (year-scoped) gains files."""
    out: List[Dict[str, Any]] = []
    for p in gains_paths:
        try:
            data = load_json(p)
        except (OSError, ValueError):
            continue                    # join_income_gains warns
        if isinstance(data, dict):
            out.extend(w for w in data.get("wash_sales") or []
                       if isinstance(w, dict))
    return out


def full_history_wash_sales(base_paths: List[Path],
                            sheltered_paths: List[Path] = (),
                            phantoms: Optional[Path] = None,
                            tax_date: str = "settle",
                            option_timing: Optional[Dict[str, Any]] = None,
                            income_rules: Optional[Dict[str, Any]] = None
                            ) -> List[Dict[str, Any]]:
    """Every superficial loss the Canada engine denies over the books'
    FULL history: one `run_gains` pass (year=None) over the taxable
    books together — ITA s.47 pools are symbol-global, as in the
    pipeline's blended pass — with the registered accounts as wash
    context and the project's phantoms and option timing, the way
    `taxjson carryover` runs it. A loss denied in an earlier year whose
    replacement is still held is in the year's ACB (s.53(1)(f)), but not
    in the year-scoped gains files (S008-07, S009-01, S051-21)."""
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
                       phantom_hint=False, **(option_timing or {}),
                       # The project's income dating (a listed
                       # corporation's ROC on its pay date), as in the
                       # filing run (audit A2-0339).
                       **(income_rules or {}))
    # The engine's diagnostics belong to `taxjson run` (they would repeat
    # here); this pass only reads where each denial's addition lands. A
    # solver that did not converge is still said.
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(txs, sheltered, (), req)
    for line in err.getvalue().splitlines():
        if "did not converge" in line:
            print(line, file=sys.stderr)
    return list(res.get("wash_sales") or [])


def wash_adjustments(wash_sales: List[Dict[str, Any]],
                     transactions: List[Dict[str, Any]],
                     tax_date: str = "settle") -> List[Dict[str, Any]]:
    """The engine's s.53(1)(f) additions as ADJUST rows for the cost walk.

    A superficial loss the engine denies (s.54) is added to the ACB of
    the substituted property; the T1135 cost amount of capital property
    is its ACB (s.248(1)), so the walk carries the same addition (audit
    G7-0, S008-07). Each record's `adjusts` (the engine's own landings:
    pool symbol, stamp, amount — the poolable share only; a replacement
    in a registered or affiliated account makes the loss permanently
    denied and adds nothing) becomes one ADJUST row. A landing the
    engine applies right after the losing sale carries `wash_after` =
    that sale's id, and the walk places it there.

    Records without `adjusts` (gains files written before it existed)
    fall back to the trigger lot: the row is stamped at the LATER of the
    losing sale and the replacement purchase."""
    by_id = {str(t.get("id")): t for t in transactions if t.get("id") not in (None, "")}
    out: List[Dict[str, Any]] = []
    seen = set()
    for w in wash_sales:
        if isinstance(w.get("adjusts"), list):
            for a in w["adjusts"]:
                try:
                    amt = float(a.get("amount") or 0.0)
                except (TypeError, ValueError):
                    continue
                aid = str(a.get("id") or "")
                if not a.get("symbol") or abs(amt) < 1e-9 or aid in seen:
                    continue
                seen.add(aid)
                row = {"id": aid or f"wash:{w.get('loss_tx_id')}",
                       "action": "ADJUST", "symbol": a["symbol"],
                       "date": a.get("date") or "",
                       "date_settle": (a.get("date_settle")
                                       or a.get("date") or ""),
                       "time": a.get("time") or "00:00:00",
                       "quantity": 0.0, "net_amount": amt,
                       "account": a.get("account") or ""}
                if a.get("after") and str(a["after"]) in by_id:
                    row["wash_after"] = str(a["after"])
                out.append(row)
            continue
        lot = by_id.get(str(w.get("trigger_lot_id") or ""))
        if lot is None:
            continue
        parts = str(w.get("adjust_cmd") or "").split()
        sym = parts[3] if len(parts) >= 6 else (lot.get("symbol") or "")
        try:
            amt = float(parts[5] if len(parts) >= 6 else w.get("amount", 0.0))
        except (TypeError, ValueError):
            continue
        key = (str(w.get("loss_tx_id")), str(w.get("trigger_lot_id")), sym, round(amt, 6))
        if not sym or abs(amt) < 1e-9 or key in seen:
            continue
        seen.add(key)
        at = lot
        loss = by_id.get(str(w.get("loss_tx_id") or ""))
        if loss is not None and _sort_key(loss, tax_date) > _sort_key(lot, tax_date):
            at = loss
        out.append({"id": f"wash:{w.get('loss_tx_id')}:{w.get('trigger_lot_id')}",
                    "action": "ADJUST", "symbol": sym,
                    "date": at.get("date") or "",
                    "date_settle": at.get("date_settle") or at.get("date") or "",
                    "time": at.get("time") or "00:00:00",
                    "quantity": 0.0, "net_amount": amt,
                    "account": lot.get("account") or ""})
    return out


def _deferred_wash(gains_paths: List[Path],
                   overrides: Dict[str, Optional[str]]) -> Dict[str, float]:
    """{foreign symbol: denied superficial loss still in its ACB at year
    end} from the gains files' inventory. The max per symbol, not the
    sum: every account's inventory row of a blended symbol carries the
    blended pool's deferral."""
    out: Dict[str, float] = {}
    for p in gains_paths:
        try:
            data = load_json(p)
        except (OSError, ValueError):
            continue
        for h in data.get("inventory") or []:
            sym = h.get("symbol") or ""
            try:
                dw = float(h.get("deferred_wash") or 0.0)
            except (TypeError, ValueError):
                continue
            if dw > 0.005 and classify_country(sym, overrides) is not None:
                out[sym] = round(max(out.get(sym, 0.0), dw), 2)
    return out


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
                 income_rules: Optional[Dict[str, Any]] = None
                 ) -> Dict[str, Any]:
    """The T1135 report model. `today` (ISO date, default the real
    date) decides whether the year is complete: before Dec 31 the
    figures run to the last date in the books and a negative verdict is
    provisional (S051-22, S052-15).

    `full_history` (the default) runs the engine once over the whole
    history (`full_history_wash_sales`, with `sheltered_paths` as wash
    context and the project's `option_timing`) so a superficial loss
    denied in ANY year is in the replacement's cost; off, only the
    gains files' (year-scoped) denials are."""
    from datetime import date as _date
    today = today or _date.today().isoformat()
    check_currency(list(base_paths) + list(gains_paths), base_currency)
    txs = load_transactions(base_paths, phantoms)
    user_keys = set(overrides)
    overrides = dict(overrides)         # the walk adds rename targets
    # Every denied superficial loss joins the walk as ADJUST rows where
    # the engine put it (G7-0, S008-07): the full-history pass covers
    # losses denied before the project year.
    if full_history:
        wash_sales = full_history_wash_sales(
            base_paths, sheltered_paths, phantoms, tax_date, option_timing,
            income_rules)
    else:
        wash_sales = _read_wash_sales(gains_paths)
    year_end_key = f"{year}-12-31"
    wash = [w for w in wash_adjustments(wash_sales, txs, tax_date)
            if _tx_date(w, tax_date) <= year_end_key]
    txs = txs + wash
    walk = walk_costs(txs, year, overrides, tax_date, today=today)
    inc = join_income_gains(gains_paths, year, overrides, tax_date)
    # An override that matches nothing (a ticker change, a ticker.map
    # consolidation, a typo) silently reversed the filing verdict
    # (S051-17): name it.
    seen = {t.get("symbol") for t in txs if t.get("symbol")}
    unused_overrides = sorted(k for k in user_keys if k not in seen)
    for k in unused_overrides:
        print(f"warning: t1135.map: {k!r} matches no symbol in the books "
              f"(renamed, consolidated by ticker.map, or a typo?) — the "
              f"override is not applied.", file=sys.stderr)
    # A Canadian issuer on a foreign listing (its ISIN says CA — IB
    # stamps issuer_country on the rows) is not specified foreign
    # property, yet the listing suffix classifies it foreign. Named, not
    # guessed: the user confirms with a `SYMBOL CA` line in t1135.map
    # (re-audit A2-0332).
    canadian_issuer = sorted({
        str(t.get("symbol")) for t in txs
        if str(t.get("issuer_country") or "").upper() == "CA"
        and t.get("symbol") and t.get("symbol") not in user_keys
        and classify_country(str(t.get("symbol")), overrides)
        not in (None, CRYPTO)})
    if canadian_issuer:
        print(f"warning: {len(canadian_issuer)} symbol(s) on a foreign "
              f"listing carry a Canadian ISIN ({', '.join(canadian_issuer[:6])}"
              f"{' ...' if len(canadian_issuer) > 6 else ''}): shares of a "
              f"Canadian corporation are not specified foreign property, "
              f"but they are counted here by their listing — add "
              f"`SYMBOL CA` to t1135.map once confirmed.", file=sys.stderr)
    deferred: Dict[str, float] = {}
    if not full_history:
        # Year-only mode: a loss denied in an earlier year whose
        # replacement is still held is not in the walk. The gains files'
        # inventory deferral beyond the year's own additions bounds it
        # (that inventory is as of the books' last row, so it can also
        # carry a later year's deferral — an upper bound).
        added: Dict[str, float] = {}
        for w in wash:
            added[w["symbol"]] = (added.get(w["symbol"], 0.0)
                                  + float(w["net_amount"]))
        deferred = {k: round(v - added.get(k, 0.0), 2)
                    for k, v in _deferred_wash(gains_paths,
                                               overrides).items()
                    if v - added.get(k, 0.0) > 0.005}
    futures = set(walk.get("futures_symbols") or ())

    rows = []
    for symbol in sorted(walk["per_symbol"]):
        s = walk["per_symbol"][symbol]
        ig = inc.get(symbol, {})
        rows.append({
            "symbol": symbol,
            "country": s["country"],
            "max_cost": s["max_cost"],
            "year_end_cost": s["year_end_cost"],
            "income": round(ig.get("income", 0.0), 2),
            "gain": round(ig.get("gain", 0.0), 2),
            "unknown_acb": s["unknown_acb"],
            "futures": False,
        })
    # Income or gain on a foreign symbol whose cost never showed up in the
    # walk (e.g. fully disposed via a corp action the walk didn't model)
    # still belongs on the form.
    for symbol, ig in sorted(inc.items()):
        if symbol in walk["per_symbol"]:
            continue
        country = classify_country(symbol, overrides)
        if country is None:
            continue
        if abs(ig.get("income", 0.0)) < 0.005 and abs(ig.get("gain", 0.0)) < 0.005:
            continue
        rows.append({
            "symbol": symbol, "country": country,
            "max_cost": 0.0, "year_end_cost": 0.0,
            "income": round(ig.get("income", 0.0), 2),
            "gain": round(ig.get("gain", 0.0), 2),
            "unknown_acb": False,
            "futures": is_plain_future(symbol),
        })

    by_country: Dict[str, Dict[str, float]] = {}
    for r in rows:
        c = by_country.setdefault(r["country"], {
            "max_cost": 0.0, "year_end_cost": 0.0, "income": 0.0, "gain": 0.0})
        # NOTE: summing per-symbol maxima overstates a true simultaneous
        # per-country maximum (the symbols may not have peaked together) —
        # a conservative upper bound, which is the safe direction for a
        # disclosure form. The filing-threshold test below does NOT use
        # this: it uses the event-wise simultaneous total.
        c["max_cost"] += r["max_cost"]
        c["year_end_cost"] += r["year_end_cost"]
        c["income"] += r["income"]
        c["gain"] += r["gain"]
    for c in by_country.values():
        for k in c:
            c[k] = round(c[k], 2)

    max_total = walk["max_total_cost"]
    year_end = f"{year}-12-31"
    year_complete = today > year_end
    as_of = year_end if year_complete else (
        walk.get("last_date") or min(today, year_end))
    expired = list(walk.get("expired_options_held") or [])
    if expired:
        print(f"warning: {len(expired)} long option(s) still held in the "
              f"books after their expiry date ({', '.join(expired[:6])}"
              f"{' ...' if len(expired) > 6 else ''}): their cost is still "
              f"counted in the T1135 figures — add the missing expiry or "
              f"exercise row and re-run.", file=sys.stderr)
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
                       "cash, certificate shares, a foreign rental) is "
                       "not counted and adds to the same threshold"),
        "detailed_threshold": detailed_threshold,
        "simplified_method_available": max_total < detailed_threshold,
        "properties": rows,
        "by_country": by_country,
        "review_symbols": [r["symbol"] for r in rows
                           if r["country"] in (REVIEW, CRYPTO)],
        "crypto_symbols": [r["symbol"] for r in rows
                           if r["country"] == CRYPTO],
        "unknown_acb_symbols": [r["symbol"] for r in rows if r["unknown_acb"]],
        "futures_symbols": sorted(futures),
        "phantoms_applied": phantoms is not None,
        "unused_overrides": unused_overrides,
        # Only with the full-history pass off (--year-wash-only):
        # superficial losses the engine's inventory still defers
        # (s.53(1)(f)) beyond the year's own additions — the cost
        # columns are low by up to this. Empty otherwise: the walk
        # carries every addition where the engine applied it.
        "deferred_wash_not_in_cost": deferred,
        "full_history_wash": bool(full_history),
        "tax_date_basis": tax_date,
        "year_complete": year_complete,
        "as_of": as_of,
        "expired_options_held": expired,
        "canadian_issuer_symbols": canadian_issuer,
    }


_money = fmt_money                  # shared report-layer formatter


def render_report(rep: Dict[str, Any]) -> str:
    cur = rep["base_currency"]
    complete = rep.get("year_complete", True)
    as_of = rep.get("as_of") or f"{rep['year']}-12-31"
    so_far = "" if complete else f" (books through {as_of})"
    lines: List[str] = []
    lines.append(f"T1135 — Foreign Income Verification Statement helper "
                 f"(tax year {rep['year']}, amounts in {cur})")
    lines.append("")
    lines.append("Filing requirement (total-cost test, ITA 233.3):")
    when = f" on {rep['max_total_date']}" if rep["max_total_date"] else ""
    lines.append(f"  Maximum total cost of specified foreign property during "
                 f"{rep['year']}{so_far}: {_money(rep['max_total_cost'])} "
                 f"{cur}{when}")
    if rep["filing_required"]:
        lines.append(f"  => T1135 FILING REQUIRED "
                     f"(exceeds {_money(rep['filing_threshold'])} {cur})")
        if rep["simplified_method_available"]:
            lines.append(f"  => Simplified method (Part A) available "
                         f"(stayed under {_money(rep['detailed_threshold'])} "
                         f"{cur}{' so far' if not complete else ''}; "
                         f"T1135 instructions)")
        else:
            lines.append(f"  => Detailed method (Part B) required "
                         f"(reached {_money(rep['detailed_threshold'])} "
                         f"{cur}; T1135 instructions)")
    elif complete:
        lines.append(f"  => below the {_money(rep['filing_threshold'])} {cur} "
                     f"threshold on these books — no T1135 required this "
                     f"year unless foreign property outside them (see "
                     f"notes) takes the total over")
    else:
        # In-year (S051-22, S052-15): ITA 233.3 counts cost at ANY time
        # up to Dec 31 — a mid-year 'not required' is not a verdict.
        lines.append(f"  => below the {_money(rep['filing_threshold'])} {cur} "
                     f"threshold so far (books through {as_of}) — the test "
                     f"runs to Dec 31; re-run after the year ends")
    if rep.get("expired_options_held"):
        _ex = rep["expired_options_held"]
        lines.append(f"  !! {len(_ex)} long option(s) still held after their "
                     f"expiry date ({', '.join(_ex[:6])}"
                     f"{' ...' if len(_ex) > 6 else ''}) are counted at "
                     f"cost — the books lack their expiry/exercise row.")
    if rep.get("canadian_issuer_symbols"):
        _ci = rep["canadian_issuer_symbols"]
        lines.append(f"  !! {len(_ci)} symbol(s) with a Canadian ISIN on a "
                     f"foreign listing are counted as foreign property "
                     f"({', '.join(_ci[:6])}{' ...' if len(_ci) > 6 else ''})"
                     f" — a Canadian corporation's shares are not; map "
                     f"them `SYMBOL CA` in t1135.map once confirmed.")
    _dw = sum((rep.get("deferred_wash_not_in_cost") or {}).values())
    if _dw:
        lines.append(f"  !! cost amounts EXCLUDE {_money(_dw)} {cur} of "
                     f"denied superficial losses the engine added to the "
                     f"ACB of property still held (s.53(1)(f)): "
                     + ", ".join(f"{k} {_money(v)}" for k, v in sorted(
                         rep["deferred_wash_not_in_cost"].items())[:6])
                     + " — the true cost amounts are higher by up to "
                       "that much.")
        if (not rep["filing_required"]
                and rep["max_total_cost"] + _dw > rep["filing_threshold"]):
            lines.append(f"  !! with them the maximum could exceed "
                         f"{_money(rep['filing_threshold'])} {cur} — the "
                         f"'no T1135 required' verdict is NOT reliable; "
                         f"work the cost out by hand.")
    lines.append("")

    rows = rep["properties"]
    # The column is the cost at the last date of the books while the year
    # is open — 'COST AT DEC 31' labelled a September figure (S051-22).
    end_col = "COST AT DEC 31" if complete else f"COST AT {as_of}"
    if rows:
        header = ("SYMBOL", "COUNTRY", "MAX COST IN YR", end_col,
                  "INCOME", "GAIN(LOSS)", "NOTES")
        table = []
        for r in rows:
            notes = []
            if r["unknown_acb"]:
                notes.append("unknown ACB (phantom opening) — cost understated")
            if r.get("futures"):
                notes.append("futures — cost amount nil")
            if r["country"] == REVIEW:
                notes.append("unclassified — review / add to t1135.map")
            elif r["country"] == CRYPTO:
                notes.append("crypto — check where held (see notes)")
            table.append((r["symbol"], r["country"], _money(r["max_cost"]),
                          _money(r["year_end_cost"]), _money(r["income"]),
                          _money(r["gain"]), "; ".join(notes)))
        widths = [max(len(header[i]), *(len(row[i]) for row in table))
                  for i in range(len(header))]
        def fmt(row: Tuple[str, ...]) -> str:
            cells = []
            for i, cell in enumerate(row):
                # Left-align text columns, right-align money.
                cells.append(cell.ljust(widths[i]) if i in (0, 1, 6)
                             else cell.rjust(widths[i]))
            return " | ".join(cells).rstrip()
        lines.append("PER PROPERTY (taxable accounts only)")
        lines.append(fmt(header))
        lines.append("-+-".join("-" * w for w in widths))
        lines.extend(fmt(row) for row in table)
        lines.append("")

        lines.append("PER COUNTRY (upper-bound aggregates)")
        cheader = ("COUNTRY", "MAX COST IN YR", end_col,
                   "INCOME", "GAIN(LOSS)")
        ctable = [(c, _money(v["max_cost"]), _money(v["year_end_cost"]),
                   _money(v["income"]), _money(v["gain"]))
                  for c, v in sorted(rep["by_country"].items())]
        cwidths = [max(len(cheader[i]), *(len(row[i]) for row in ctable))
                   for i in range(len(cheader))]
        def cfmt(row: Tuple[str, ...]) -> str:
            return " | ".join(
                (row[i].ljust(cwidths[i]) if i == 0 else row[i].rjust(cwidths[i]))
                for i in range(len(row))).rstrip()
        lines.append(cfmt(cheader))
        lines.append("-+-".join("-" * w for w in cwidths))
        lines.extend(cfmt(row) for row in ctable)
        lines.append("")
    else:
        lines.append("No specified foreign property found in the inputs.")
        lines.append("")

    lines.append("Notes:")
    lines.append("  - Amounts are COST (ACB-style, settlement-dated), the "
                 "correct basis for the filing threshold and the 'maximum "
                 "cost amount' columns. The category-7 detailed method asks "
                 "for month-end FAIR MARKET VALUE, which this tool does not "
                 "fetch — use your broker's month-end statements for those "
                 "boxes.")
    lines.append("  - Per-country MAX COST sums per-symbol maxima (an upper "
                 "bound); the filing-threshold test uses the true "
                 "simultaneous total.")
    lines.append("  - .TO/.V/.CN/.NE symbols are treated as Canadian (not "
                 "SFP). A foreign-domiciled corp listed on a Canadian "
                 "exchange IS still SFP — add `SYMBOL <ISO3>` to t1135.map. "
                 "Conversely a Canadian corp held on a US exchange is NOT "
                 "SFP — add `SYMBOL CA`.")
    if rep.get("futures_symbols"):
        lines.append(f"  - Futures contracts ({len(rep['futures_symbols'])}: "
                     f"{', '.join(rep['futures_symbols'][:6])}"
                     f"{' ...' if len(rep['futures_symbols']) > 6 else ''}) "
                     "carry NO cost amount: nothing is paid to open one "
                     "(initial margin is a deposit, variation margin "
                     "settles daily), so their notional is left out of the "
                     "cost columns and the threshold test. Options on "
                     "futures count at their premium cost.")
    if rep.get("crypto_symbols"):
        lines.append("  - CRYPTO rows (symbols with no market suffix): crypto "
                     "held on a FOREIGN exchange or platform is generally "
                     "specified foreign property — report it under that "
                     "exchange's country (add `SYMBOL <ISO3>` to t1135.map). "
                     "Crypto held with a Canadian platform may not be; add "
                     "`SYMBOL CA` once you have checked. Until then it is "
                     "counted toward the threshold (the conservative side).")
    lines.append("  - Registered accounts (RRSP/TFSA/...) are excluded by "
                 "law and were not read.")
    lines.append("  - The test covers these brokerage books only. Specified "
                 "foreign property held outside them — a foreign bank "
                 "account or cash, shares held in certificate form, a "
                 "foreign rental property — counts toward the same "
                 "threshold at the same time: add its cost by hand.")
    lines.append("  - US-situs property inside T1135 does not include US "
                 "property held only through Canadian mutual funds/ETFs.")
    lines.append("  - Not tax advice; reconcile against broker statements "
                 "before filing.")
    return "\n".join(lines)


# ---------------------------------------------------------------- main

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
                        help="t1135.map override file (SYMBOL COUNTRY lines)")
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
                        metavar="PHANTOMS_JSON",
                        help="phantoms.json: add the same phantom "
                             "openings the gains stage adds (the project "
                             "wrapper passes the project's file)")
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
    parser.add_argument("--option-buyback-wash", action="store_true",
                        help="Grant timing: a written option's buy-back "
                             "loss can be superficial")
    parser.add_argument("--corporate-distribution", action="append",
                        default=None, metavar="SYMBOL",
                        help="A Canadian issuer whose distributions are a "
                             "corporation's (ROC dated when paid) in the "
                             "full-history pass; repeatable ([settings] "
                             "corporate_distributions)")
    parser.add_argument("--year-wash-only", action="store_true",
                        help="Skip the full-history engine pass: only the "
                             "gains files' (project-year) denied losses "
                             "are added to cost, and the report names "
                             "what that leaves out")
    args = parser.parse_args(argv)

    extra = [args.incomplete_history] if args.incomplete_history else []
    extra += list(args.sheltered)
    for p in args.files + args.gains + extra:
        if not p.exists():
            print(f"taxjson-t1135: no such file: {p}", file=sys.stderr)
            return 2
    if args.map is not None and not args.map.exists():
        print(f"taxjson-t1135: no such map file: {args.map}", file=sys.stderr)
        return 2

    overrides = load_overrides(args.map)
    if args.option_premium_timing is None:
        # The T1135 test is Canadian; a Canada project's run uses grant
        # timing from the project year — say so instead of silently
        # disagreeing with it, as taxjson-gains does (re-audit A2-1361).
        print("taxjson-t1135: note: --option-premium-timing not given — "
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
                                   args.option_buyback_wash)),
                           full_history=not args.year_wash_only,
                           income_rules=dict(corporate_distributions=tuple(
                               args.corporate_distribution or ())))
    except (UnreadableGains, CurrencyMismatch) as e:
        print(e.code, file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        print(f"taxjson-t1135: could not read the base books: {e} — "
              f"re-run `taxjson run` to rebuild them.", file=sys.stderr)
        return 2
    if args.json:
        json.dump(rep, sys.stdout, indent=2, sort_keys=True)
        print()
    else:
        print(render_report(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
