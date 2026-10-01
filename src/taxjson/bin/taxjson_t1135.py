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
accounts (RRSP/TFSA/...) are excluded from SFP by law — do not pass them.

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

Usage:
    taxjson-t1135 --year 2025 margin_base.json crypto_base.json \\
        --gains margin_gains.json --gains crypto_gains.json \\
        [--map t1135.map] [--threshold 100000] [--json]

Or through the project wrapper (recommended):  `taxjson t1135`
"""

import argparse
import json
import re
import sys
from decimal import Decimal
from pathlib import Path
from taxjson.lib.cli_diag import guard_main, read_text_utf8, tax_year
from taxjson.lib.futures import is_plain_future
from taxjson.lib.numeric import positive_float_arg
from taxjson.lib.report_model import fmt_money, load_report_json
from typing import Any, Dict, List, Optional, Tuple

# Filing thresholds, ITA 233.3: SFP total cost > $100,000 CAD at any time in
# the year requires the form; >= $250,000 at any time disqualifies the
# simplified (Part A) method.
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
    # Canadian exchanges — not specified foreign property.
    "TO": None,
    "V": None,
    "CN": None,
    "NE": None,
}

# Actions that never move a position or its cost. Mirrors the engines'
# non-capital skip list (core.py) minus ADJUST/SPLIT/OPENING_BALANCE which
# we do consume.
_NON_CAPITAL = ("DIVIDEND", "DIVIDEND_IN_LIEU", "TAX", "INTEREST", "FEE",
                "TRANSFER")

_QTY_EPS = 1e-6

# Sentinel country code for symbols we cannot classify (an unknown market
# suffix). Deliberately ugly so it reads as "review me".
REVIEW = "??"
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
    return load_report_json(path)


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
        raw = load_json(p)
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
        symbol, code = parts[0], parts[1].upper()
        if code in ("CA", "CAN", "CANADA", "EXCLUDE"):
            overrides[symbol] = None
        else:
            overrides[symbol] = code
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


def walk_costs(transactions: List[Dict[str, Any]], year: int,
               overrides: Dict[str, Optional[str]],
               tax_date: str = "settle") -> Dict[str, Any]:
    """Replay full history in base currency; return per-symbol cost stats
    for `year` plus the maximum TOTAL foreign cost observed in the year
    (the ITA 233.3 filing-threshold test). `overrides` is updated with
    rename targets of overridden symbols (a ticker change keeps its
    t1135.map classification, S051-17)."""
    year_start = f"{year}-01-01"
    year_end = f"{year}-12-31"
    from taxjson.lib.core import is_option_symbol, parse_option_underlying
    # Underlyings that trade as stock here: only their assignments fold
    # the option premium into the shares (a cash-settled index option
    # has no stock leg — same test as the engine).
    stock_symbols = {tx.get("symbol") for tx in transactions
                     if tx.get("symbol")
                     and not is_option_symbol(tx.get("symbol"))}
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
    for tx in sorted(transactions, key=lambda t: _sort_key(t, tax_date)):
        date = _tx_date(tx, tax_date)
        if date > year_end:
            break
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
            pool.cost += Decimal(str(net))
        else:
            # BUYSELL / ASSIGN / EXERCISE-shaped rows: average-cost pool,
            # same conventions as the Canada engine (buy cost added =
            # |net_amount|, which parsers emit fee-inclusive).
            if abs(qty) < _QTY_EPS:
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
                und = parse_option_underlying(symbol)
                _m = re.search(r"\d{6}([CP])\d{8}", symbol)
                right = _m.group(1) if (und and _m) else ""
                closing_units = min(abs(qty), abs(pool.qty))
                if (und and und in stock_symbols
                        and closing_units > _QTY_EPS
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
    }


# ---------------------------------------------------------------- income/gains

class UnreadableGains(SystemExit):
    pass


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
        except (OSError, json.JSONDecodeError) as e:
            # A skipped taxable gains file zeroed that account's income
            # and gain columns with rc 0 (R1-277).
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
                rec["income"] += float(e.get("pil") or 0.0)
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


def wash_adjustments(gains_paths: List[Path],
                     transactions: List[Dict[str, Any]],
                     tax_date: str = "settle") -> List[Dict[str, Any]]:
    """The engine's s.53(1)(f) additions as ADJUST rows for the cost walk.

    A superficial loss the engine denies (s.54) is added to the ACB of
    the substituted property; the T1135 cost amount of capital property
    is its ACB (s.248(1)), so the walk must carry the same addition
    (audit G7-0: the walk replayed base rows only and understated every
    replacement's cost by the denied loss). Each gains file's
    `wash_sales` entry becomes an ADJUST on the replacement lot's symbol
    — only when the lot is one of the walked (taxable) rows; a
    replacement bought in a registered or affiliated account is not the
    filer's foreign property. The row is stamped at the LATER of the
    losing sale and the replacement purchase (a replacement bought
    before the losing sale must not have part of the addition averaged
    out by that sale); an ADJUST sorts after the trades at its stamp.
    The gains files are year-scoped: a loss denied in an EARLIER year is
    not listed (see `_deferred_wash`)."""
    by_id = {str(t.get("id")): t for t in transactions if t.get("id") not in (None, "")}
    out: List[Dict[str, Any]] = []
    seen = set()
    for p in gains_paths:
        try:
            data = load_json(p)
        except (OSError, json.JSONDecodeError):
            continue                    # join_income_gains warns
        if not isinstance(data, dict):
            continue
        for w in data.get("wash_sales") or []:
            lot = by_id.get(str(w.get("trigger_lot_id") or ""))
            if lot is None:
                continue
            parts = str(w.get("adjust_cmd") or "").split()
            sym = parts[3] if len(parts) >= 6 else (lot.get("symbol") or "")
            try:
                amt = float(w.get("amount", parts[5] if len(parts) >= 6 else 0.0))
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
        except (OSError, json.JSONDecodeError):
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
                 tax_date: str = "settle") -> Dict[str, Any]:
    txs = load_transactions(base_paths, phantoms)
    user_keys = set(overrides)
    overrides = dict(overrides)         # the walk adds rename targets
    # The year's denied superficial losses join the walk as ADJUST rows
    # (G7-0); the sort puts each after the trades at its stamp.
    wash = wash_adjustments(gains_paths, txs, tax_date)
    txs = txs + wash
    walk = walk_costs(txs, year, overrides, tax_date)
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
    deferred = _deferred_wash(gains_paths, overrides)
    # What the walk already carries is not "excluded": only a deferral
    # beyond the year's own additions (a loss denied in an earlier year)
    # is left for the note.
    added: Dict[str, float] = {}
    for w in wash:
        added[w["symbol"]] = added.get(w["symbol"], 0.0) + float(w["net_amount"])
    deferred = {k: round(v - added.get(k, 0.0), 2) for k, v in deferred.items()
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
    return {
        "year": year,
        "base_currency": base_currency,
        "max_total_cost": max_total,
        "max_total_date": walk["max_total_date"],
        "filing_threshold": threshold,
        "filing_required": max_total > threshold,
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
        # Superficial losses still in open positions' ACB (s.53(1)(f))
        # beyond those the year's gains files list (added to the walk):
        # a loss denied in an earlier year — the cost columns are low
        # by up to this (KNOWN_ISSUES).
        "deferred_wash_not_in_cost": deferred,
        "tax_date_basis": tax_date,
    }


_money = fmt_money                  # shared report-layer formatter


def render_report(rep: Dict[str, Any]) -> str:
    cur = rep["base_currency"]
    lines: List[str] = []
    lines.append(f"T1135 — Foreign Income Verification Statement helper "
                 f"(tax year {rep['year']}, amounts in {cur})")
    lines.append("")
    lines.append("Filing requirement (total-cost test, ITA 233.3):")
    when = f" on {rep['max_total_date']}" if rep["max_total_date"] else ""
    lines.append(f"  Maximum total cost of specified foreign property during "
                 f"{rep['year']}: {_money(rep['max_total_cost'])} {cur}{when}")
    if rep["filing_required"]:
        lines.append(f"  => T1135 FILING REQUIRED "
                     f"(exceeds {_money(rep['filing_threshold'])} {cur})")
        if rep["simplified_method_available"]:
            lines.append(f"  => Simplified method (Part A) available "
                         f"(stayed under {_money(rep['detailed_threshold'])} {cur})")
        else:
            lines.append(f"  => Detailed method (Part B) required "
                         f"(reached {_money(rep['detailed_threshold'])} {cur})")
    else:
        lines.append(f"  => below the {_money(rep['filing_threshold'])} {cur} "
                     f"threshold — no T1135 required this year")
    _dw = sum((rep.get("deferred_wash_not_in_cost") or {}).values())
    if _dw:
        lines.append(f"  !! cost amounts EXCLUDE {_money(_dw)} {cur} of "
                     f"denied superficial losses added to the ACB of "
                     f"shares still held (s.53(1)(f)): "
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
    if rows:
        header = ("SYMBOL", "COUNTRY", "MAX COST IN YR", "COST AT DEC 31",
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
        cheader = ("COUNTRY", "MAX COST IN YR", "COST AT DEC 31",
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
    lines.append("  - US-situs property inside T1135 does not include US "
                 "property held only through Canadian mutual funds/ETFs.")
    lines.append("  - Not tax advice; reconcile against broker statements "
                 "before filing.")
    return "\n".join(lines)


# ---------------------------------------------------------------- main

@guard_main("taxjson-t1135")
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="CRA T1135 foreign-property helper: filing-threshold "
                    "test + per-property/per-country tables from taxjson "
                    "base/gains files.")
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
    args = parser.parse_args(argv)

    extra = [args.incomplete_history] if args.incomplete_history else []
    for p in args.files + args.gains + extra:
        if not p.exists():
            print(f"taxjson-t1135: no such file: {p}", file=sys.stderr)
            return 2
    if args.map is not None and not args.map.exists():
        print(f"taxjson-t1135: no such map file: {args.map}", file=sys.stderr)
        return 2

    overrides = load_overrides(args.map)
    try:
        rep = build_report(args.files, args.gains, args.year, overrides,
                           args.base_currency.upper(),
                           threshold=args.threshold,
                           detailed_threshold=args.detailed_threshold,
                           phantoms=args.incomplete_history,
                           tax_date=args.tax_date)
    except UnreadableGains as e:
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
