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
    flagged country `??` for manual review — CRA generally treats
    foreign-exchange-held crypto as SFP.

Usage:
    taxjson-t1135 --year 2025 margin_base.json crypto_base.json \\
        --gains margin_gains.json --gains crypto_gains.json \\
        [--map t1135.map] [--threshold 100000] [--json]

Or through the project wrapper (recommended):  `taxjson t1135`
"""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
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

# Sentinel country code for symbols we cannot classify (no market suffix —
# typically crypto). Deliberately ugly so it reads as "review me".
REVIEW = "??"


# ---------------------------------------------------------------- loading

# Thin alias so existing importers (tests, taxjson_reconcile_slips)
# keep working; the implementation is the shared report-layer loader.
def load_json(path: Path) -> Any:
    return load_report_json(path)


def load_transactions(paths: List[Path]) -> List[Dict[str, Any]]:
    txs: List[Dict[str, Any]] = []
    for p in paths:
        raw = load_json(p)
        rows = raw.get("transactions", []) if isinstance(raw, dict) else raw
        for t in rows:
            if isinstance(t, dict):
                txs.append(t)
    return txs


def load_overrides(path: Optional[Path]) -> Dict[str, Optional[str]]:
    """t1135.map: `SYMBOL COUNTRY` per line, '#' comments. COUNTRY of
    CA/CAN/CANADA/EXCLUDE means "not foreign property"."""
    overrides: Dict[str, Optional[str]] = {}
    if path is None:
        return overrides
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
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
    return REVIEW


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


def _sort_key(tx: Dict[str, Any]) -> Tuple[str, str]:
    # Settlement basis, matching the project's canonical Canada decision.
    return (tx.get("date_settle") or tx.get("date") or "",
            tx.get("time") or "00:00:00")


def walk_costs(transactions: List[Dict[str, Any]], year: int,
               overrides: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """Replay full history in base currency; return per-symbol cost stats
    for `year` plus the maximum TOTAL foreign cost observed in the year
    (the ITA 233.3 filing-threshold test)."""
    year_start = f"{year}-01-01"
    year_end = f"{year}-12-31"

    pools: Dict[str, _Pool] = {}
    max_total = 0.0
    max_total_date = ""
    baseline_taken = False

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

    for tx in sorted(transactions, key=_sort_key):
        date = tx.get("date_settle") or tx.get("date") or ""
        if date > year_end:
            break
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
            if new_symbol and new_symbol != symbol:
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
    }


# ---------------------------------------------------------------- income/gains

def join_income_gains(gains_paths: List[Path], year: int) -> Dict[str, Dict[str, float]]:
    """Per-symbol dividend+PIL income and realized gain(loss) for `year`,
    from the pipeline's (already year-scoped) gains files. Defensively
    re-filters by year so a hand-run full-history gains file also works."""
    out: Dict[str, Dict[str, float]] = {}
    ystr = str(year)
    tainted_skipped = 0
    for p in gains_paths:
        try:
            data = load_json(p)
        except (OSError, json.JSONDecodeError) as e:
            print(f"warning: could not read {p}: {e}", file=sys.stderr)
            continue
        for e in data.get("transactions", []):
            date = e.get("date_settle") or e.get("date") or ""
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
    if tainted_skipped:
        print(f"warning: {tainted_skipped} tainted disposition(s) with "
              f"phantom cost basis EXCLUDED from the T1135 gain(loss) "
              f"column — resolve the missing history and re-run "
              f"(matches form-export/carryover).", file=sys.stderr)
    return out


# ---------------------------------------------------------------- report

def build_report(base_paths: List[Path], gains_paths: List[Path], year: int,
                 overrides: Dict[str, Optional[str]],
                 base_currency: str,
                 threshold: float = FILING_THRESHOLD,
                 detailed_threshold: float = DETAILED_THRESHOLD) -> Dict[str, Any]:
    txs = load_transactions(base_paths)
    walk = walk_costs(txs, year, overrides)
    inc = join_income_gains(gains_paths, year)

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
        "review_symbols": [r["symbol"] for r in rows if r["country"] == REVIEW],
        "unknown_acb_symbols": [r["symbol"] for r in rows if r["unknown_acb"]],
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
            if r["country"] == REVIEW:
                notes.append("unclassified — review / add to t1135.map")
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
    lines.append("  - Registered accounts (RRSP/TFSA/...) are excluded by "
                 "law and were not read.")
    lines.append("  - US-situs property inside T1135 does not include US "
                 "property held only through Canadian mutual funds/ETFs.")
    lines.append("  - Not tax advice; reconcile against broker statements "
                 "before filing.")
    return "\n".join(lines)


# ---------------------------------------------------------------- main

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
    parser.add_argument("--year", type=int, required=True,
                        help="Tax year")
    parser.add_argument("--map", type=Path, default=None,
                        help="t1135.map override file (SYMBOL COUNTRY lines)")
    parser.add_argument("--base-currency", default="CAD",
                        help="Label for amounts (default: CAD)")
    parser.add_argument("--threshold", type=float, default=FILING_THRESHOLD,
                        help="Filing threshold (default: 100000)")
    parser.add_argument("--detailed-threshold", type=float,
                        default=DETAILED_THRESHOLD,
                        help="Detailed-method threshold (default: 250000)")
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text")
    args = parser.parse_args(argv)

    for p in args.files + args.gains:
        if not p.exists():
            print(f"taxjson-t1135: no such file: {p}", file=sys.stderr)
            return 2
    if args.map is not None and not args.map.exists():
        print(f"taxjson-t1135: no such map file: {args.map}", file=sys.stderr)
        return 2

    overrides = load_overrides(args.map)
    rep = build_report(args.files, args.gains, args.year, overrides,
                       args.base_currency.upper(),
                       threshold=args.threshold,
                       detailed_threshold=args.detailed_threshold)
    if args.json:
        json.dump(rep, sys.stdout, indent=2, sort_keys=True)
        print()
    else:
        print(render_report(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
