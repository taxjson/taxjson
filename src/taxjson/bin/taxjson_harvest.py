#!/usr/bin/env python3
"""
taxjson_harvest.py

Unrealized gain/(loss) per OPEN position at current prices — the
tax-loss-harvest view. "If I sold this today, is it a loss?"

Positions (quantity + base-currency book cost, post ticker.map and post
split/wash adjustments) come from the canonical per-account gains files'
`inventory` — the same basis `taxjson list` shows. Current prices resolve
through the shared chain: IBKR -> yfinance -> price cache. Each LOSS row
is annotated with the wash radar's advisory when a radar JSON sidecar is
supplied, so "is it a loss?" and "may I claim it?" land on one line.

Rows sort harvestable-losses-first. For a usa project, LT IN shows the
days until the position turns long-term — an approximation from the
position's start date (per-lot terms are decided by the engine at sale
time; a position built across dates can split ST/LT).

CASH.* rows, futures and futures options are always skipped. OCC
option positions (e.g. LEAPS) are skipped by default; `--options`
includes them, priced ONLY through the IBKR tier (+ cache) with a DTE
column — no gateway means "unpriced", never a mark from a bad source.

Usage:
    taxjson-harvest work/margin_gains_wash.json [more ...]
        [--radar reports/wash_radar_margin.json]
        [--symbol AAA.TO] [--json]

Or through the project wrapper: `taxjson harvest [SYMBOL]`.
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from taxjson.lib.cli_diag import note, warn
from taxjson.lib.core import (is_option_symbol, parse_option_expiry,
                              parse_option_underlying)
from taxjson.lib.price_chain import (DEFAULT_IBKR_HOST, DEFAULT_IBKR_PORT,
                                     DEFAULT_MAX_CACHE_AGE_DAYS,
                                     fetch_option_prices, fetch_prices,
                                     latest_rate, load_fx_history,
                                     load_yf_map, quote_currency,
                                     yf_symbol_for)
from taxjson.lib.ticker_map import is_future_ticker

# Equity options premium-quote in per-share terms; a contract covers
# 100 shares and the books carry the x100 amounts.
OPTION_MULTIPLIER = 100.0
from taxjson.lib.report_model import (fmt_money,
                                      gains_basis_label, load_report_json)

PROG = "taxjson-harvest"

_GAINS_SUFFIXES = ("_gains_wash.json", "_gains.json")


def _account_of(path: Path) -> str:
    for suf in _GAINS_SUFFIXES:
        if path.name.endswith(suf):
            return path.name[: -len(suf)]
    return path.stem


from taxjson.lib.report_model import fmt_qty as _qfmt  # noqa: E402


def load_positions(files: List[Path],
                   include_options: bool = False) -> List[Dict[str, Any]]:
    """Open positions from each gains file's `inventory`: one dict per
    (account, symbol) with qty, base-currency book cost and start date.
    CASH rows are always skipped. OCC option rows are skipped unless
    `include_options` (they price only through the IBKR tier); futures
    and futures options stay out either way — no live tier serves
    them."""
    out: List[Dict[str, Any]] = []
    for p in files:
        try:
            data = load_report_json(p)
        except Exception as e:
            warn(PROG, f"could not read {p}: {e}")
            continue
        acct = _account_of(p)
        for h in data.get("inventory") or []:
            sym = str(h.get("symbol") or "")
            qty = float(h.get("qty", 0) or 0)
            if not sym or qty == 0:
                continue
            if sym.startswith("CASH.") or is_future_ticker(sym):
                continue
            is_opt = is_option_symbol(sym)
            if is_opt and not include_options:
                continue
            out.append({
                "account": acct,
                "symbol": sym,
                "qty": qty,
                "cost": float(h.get("total_cost", 0) or 0),
                "deferred_wash": float(h.get("deferred_wash", 0) or 0),
                "start": str(h.get("position_start_date") or "") or None,
                "is_option": is_opt,
            })
    return out


def load_inventory_agg(files: List[Path],
                       column: str) -> Dict[str, Dict[str, Any]]:
    """Per-symbol aggregate over a set of gains files' inventories:
    {symbol: {qty, last_add}}. `last_add` is the most recent acquisition
    across those accounts (`last_acq_date`) — the date the 30-day
    superficial-loss / wash window measures from. Used for both sides:
    the TAXABLE inputs feed TX_ADD, the --sheltered files feed
    SH_QTY/SH_ADD (a recent add on EITHER side extends the clear date).

    NO position_start_date fallback: the position may have opened long
    before its latest add (DRIP/auto-buys), so the fallback could only
    OVERSTATE the age — reading "34d, clear of the window" when the
    true answer is "13d, permanently denied" (a real user hit exactly
    this). Files predating the field show '-' and a warning instead."""
    out: Dict[str, Dict[str, Any]] = {}
    stale: List[str] = []
    for p in files:
        try:
            data = load_report_json(p)
        except Exception as e:
            warn(PROG, f"could not read {p}: {e}")
            continue
        file_has_field = False
        for h in data.get("inventory") or []:
            sym = str(h.get("symbol") or "")
            qty = float(h.get("qty", 0) or 0)
            if not sym or qty == 0:
                continue
            if "last_acq_date" in h:
                file_has_field = True
            add = str(h.get("last_acq_date") or "") or None
            rec = out.setdefault(sym, {"qty": 0.0, "last_add": None})
            rec["qty"] += qty
            if add and (rec["last_add"] is None or add > rec["last_add"]):
                rec["last_add"] = add
        if not file_has_field and (data.get("inventory") or []):
            stale.append(Path(p).name)
    if stale:
        warn(PROG, f"{', '.join(stale)} predate the last_acq_date field — "
                   f"{column} shows '-' for those holdings; re-run "
                   f"`taxjson run` to refresh.")
    return out


def _days_from_today(iso: Optional[str],
                     today: Optional[date] = None) -> Optional[int]:
    """Signed day offset from today: negative in the past ("-13d" =
    13 days ago), positive in the future. One sign convention across
    TX_ADD/SH_ADD (adds, negative) and ADVISORY clears (future,
    positive)."""
    if not iso:
        return None
    try:
        return (date.fromisoformat(iso) - (today or date.today())).days
    except ValueError:
        return None


def _add_display(rec: Optional[Dict[str, Any]]) -> str:
    """`DATE(-Nd)` cell for a last-acquisition aggregate; '-' when
    unknown. Single token — the report table splits on whitespace."""
    add = (rec or {}).get("last_add")
    d = _days_from_today(add)
    if add is None or d is None:
        return "-"
    return f"{add}({d:+d}d)"


def load_radar(paths: List[Path]) -> Dict[str, Dict[str, Any]]:
    """{ticker: {category, advisory, clears_at}} from wash-radar JSON
    sidecars (the pipeline writes one per taxable equity account)."""
    out: Dict[str, Dict[str, Any]] = {}
    for p in paths:
        try:
            doc = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            warn(PROG, f"could not read radar sidecar {p}: {e}")
            continue
        for sec in doc.get("sections") or []:
            for r in sec.get("rows") or []:
                t = r.get("ticker")
                if t and t not in out:
                    out[t] = {"category": r.get("category") or "",
                              "advisory": r.get("advisory") or "",
                              "clears_at": r.get("clears_at")}
    return out


# Source marks on the PRICE cell (replaces the SRC column):
#   ^ IBKR live   + yfinance   * price cache   ? anything else
_SOURCE_MARKS = {"ibkr": "^", "yfinance": "+", "cache": "*"}


def _price_display(price_base: float, source: Optional[str]) -> str:
    _src = source or ""
    if _src.startswith("cache"):
        # price_chain labels cache quotes "cache:{age}d" — exact-key
        # lookup rendered them as '?' (unknown source) instead of the
        # legend's '*' (2026-09 audit).
        _src = "cache"
    return f"{price_base:,.4f}{_SOURCE_MARKS.get(_src, '?')}"


def _recovery_schedule(rows: List[Dict[str, Any]],
                       today: Optional[date] = None) -> Dict[str, float]:
    """CUMULATIVE harvestable-loss amounts by wait time, from each LOSS
    row's radar clear date: `now` (no lock, or already cleared), then
    within 7/14/30 days, `later` (clears past 30d), and `no_clear`
    (no radar data, or an unparseable clear date). RISK counts as
    `now`: s.40(2)(g) needs an acquisition inside the window, so with
    no buys in the past 30 days the loss is claimable TODAY — the
    sheltered holding is a forward-window caveat (pause DRIPs for 30
    days after selling), not a lock. Estimates: today's prices, and
    any NEW buy pushes a clear date out."""
    t = today or date.today()
    out = {"now": 0.0, "7d": 0.0, "14d": 0.0, "30d": 0.0,
           "later": 0.0, "no_clear": 0.0}
    for r in rows:
        if r.get("verdict") != "LOSS":
            continue
        loss = abs(float(r.get("unrealized") or 0.0))
        rec = r.get("radar")
        cat = (rec or {}).get("category") or ""
        clears = (rec or {}).get("clears_at")
        if rec is None:
            # NO radar data ≠ no lock: without the sidecar (partial
            # run, direct invocation) a genuinely LOCKED position —
            # where harvesting now is permanently denied — was summed
            # into "claimable now". Unknown goes to no_clear, and the
            # advisory column shows the gap.
            out["no_clear"] += loss
            continue
        if cat in ("CLEAR", "RISK", "VIOLATION", "EXITABLE", "CAUTION",
                   "BLOCKED"):
            # RISK = sellable now with a forward-window caveat — the
            # loss is claimable today; only a sheltered add in the 30
            # days AFTER the sale would (permanently) deny it.
            # VIOLATION/EXITABLE/CAUTION: claimable NOW by a FULL
            # exit — their clears_at is a deadline/expiry, not an
            # availability date. Bucketing them by clears_at planned
            # the harvest for AFTER the point of no return (a
            # VIOLATION's date is the LAST day the loss can be
            # rescued; 2026-09 audit).
            # BLOCKED = a recent loss with NO in-window acquisition,
            # still holding: a further loss sale TODAY is clean too
            # (s.40(2)(g) needs an acquisition inside the window) —
            # its clears_at is the "don't REBUY before" date, not a
            # lock on selling. Treating it like LOCKED pushed the
            # loss into the 30d bucket (2026-09 audit).
            days = 0
        elif clears:
            try:
                days = max(0, (date.fromisoformat(clears) - t).days)
            except ValueError:
                out["no_clear"] += loss
                continue
        else:
            out["no_clear"] += loss
            continue
        if days <= 0:
            out["now"] += loss
        elif days <= 7:
            out["7d"] += loss
        elif days <= 14:
            out["14d"] += loss
        elif days <= 30:
            out["30d"] += loss
        else:
            out["later"] += loss
    # Cumulative: what's claimable now is also claimable by +7d, etc.
    out["7d"] += out["now"]
    out["14d"] += out["7d"]
    out["30d"] += out["14d"]
    out["later"] += out["30d"]
    return {k: round(v, 2) for k, v in out.items()}


def _advisory_display(rec: Optional[Dict[str, Any]],
                      today: Optional[date] = None) -> str:
    """Short advisory cell: CATEGORY plus the absolute clear date and a
    VIEW-TIME countdown from the sidecar's clears_at (never the stale
    generation-day text) — "clears on DATE, in Nd", matching the radar
    report's CLEARS column. Single token — the report table splits
    cells on whitespace."""
    if not rec:
        return "no-radar-data"
    cat = rec.get("category") or "-"
    clears = rec.get("clears_at")
    if clears:
        try:
            days = (date.fromisoformat(clears) - (today or date.today())).days
        except ValueError:
            days = None
        if days is not None and days > 0:
            if cat == "BLOCKED":
                # Sellable at a loss NOW; the date is the earliest
                # safe REBUY. "clears:" read as "cannot sell until".
                return f"{cat}(sell-ok,no-rebuy-until:{clears},{days:+d}d)"
            if cat == "VIOLATION":
                # Last TRADE date to rescue the loss by a full exit.
                return f"{cat}(sell-by:{clears},{days:+d}d)"
            return f"{cat}(clears:{clears},{days:+d}d)"
        if days is not None and days <= 0 and cat in ("LOCKED", "BLOCKED",
                                                      "COOLING"):
            # The window has passed since the sidecar was generated —
            # bare "LOCKED" misled; say it cleared.
            return f"{cat}(cleared:{clears})"
        if days is not None and days <= 0 and cat == "VIOLATION":
            return f"{cat}(deadline-passed:{clears})"
    return cat


_STALE_RATE_DAYS = 7

# Break-even exit buffer: the EXIT@ column adds 2% above the strict
# no-loss price to absorb fees, slippage, and FX drift between the
# quoted rate and the actual fill/settlement conversion.
_BREAKEVEN_BUFFER = 1.02


def _days_to_long_term(start: Optional[str],
                       today: Optional[date] = None) -> Optional[int]:
    """Days until the position's holding turns long-term (>1 year from
    the position start date). 0 means already long-term. Approximation:
    per-lot terms are decided by the engine at sale time — so the
    boundary is the ENGINE's `held_more_than_one_year` (Pub 550 "more
    than one year" = disposition AFTER the anniversary; Rev. Rul. 66-7
    end-of-month rule), not a fixed 366-day offset, which was a day
    EARLY whenever the year after acquisition held a Feb 29 (acq
    2023-06-01 showed LT on 2024-06-01; the engine termed that sale
    SHORT_TERM)."""
    if not start:
        return None
    try:
        acquired = datetime.strptime(start, "%Y-%m-%d").date()
    except ValueError:
        return None
    from taxjson.lib.core import held_more_than_one_year
    # First date the engine calls long-term: the anniversary (or the
    # 1st of the following month for an end-of-month buy) is never LT
    # itself, so start there and step forward — at most a few days.
    lt_from = acquired.replace(year=acquired.year + 1,
                               day=min(acquired.day, 28))
    for _ in range(40):
        if held_more_than_one_year(start, lt_from.isoformat()):
            break
        lt_from += timedelta(days=1)
    return max(0, (lt_from - (today or date.today())).days)


def main(argv: Optional[List[str]] = None,
         fetchers: Optional[List] = None,
         option_fetchers: Optional[List] = None) -> int:
    p = argparse.ArgumentParser(
        prog=PROG,
        description="Unrealized gain/(loss) per open position at current "
                    "prices (IBKR -> yfinance -> cache) — the "
                    "tax-loss-harvest view, losses first, with the wash "
                    "radar's advisory on each loss.")
    p.add_argument("files", nargs="+", metavar="FILE",
                   help="Canonical per-account gains JSONs (prefer the "
                        "_gains_wash.json files — the filing basis)")
    p.add_argument("--radar", action="append", default=[], metavar="FILE",
                   help="wash_radar_<account>.json sidecar(s) for the "
                        "ADVISORY column; repeatable")
    p.add_argument("--sheltered", action="append", default=[],
                   metavar="FILE",
                   help="SHELTERED accounts' gains JSONs; adds SH_QTY "
                        "(shares held sheltered) and SH_ADD (date the "
                        "sheltered side last acquired, as DATE(-Nd) — "
                        "the 30-day superficial-loss window measures "
                        "from there); repeatable. The taxable inputs' "
                        "own last adds always show as TX_ADD")
    p.add_argument("--symbol", action="append", default=[], metavar="NAME",
                   help="Only these symbols (case-insensitive; repeatable)")
    p.add_argument("--options", action="store_true",
                   help="Include OCC option positions (e.g. LEAPS). "
                        "Options price ONLY through the IBKR tier (+ "
                        "cache) — with no gateway running they are "
                        "listed as unpriced, never marked from a bad "
                        "source. Adds a DTE (days-to-expiry) column")
    p.add_argument("--country", default=None,
                   help="Project country (adds the LT IN column for usa; "
                        "default: canada, with a stderr note when omitted)")
    p.add_argument("--base-currency", default="CAD", metavar="CURR",
                   help="Base currency of the books (default: %(default)s). "
                        "Quotes in other currencies convert via --rates")
    p.add_argument("--rates", type=Path, default=None, metavar="FILE",
                   help="FX rates file (the pipeline's to_base.csv; "
                        "default: to_base.csv next to the first input). "
                        "Non-base quotes with no usable rate are omitted "
                        "with a warning — never mixed in unconverted")
    p.add_argument("--no-ibkr", action="store_true",
                   help="Skip the IBKR tier (no TWS/Gateway running)")
    p.add_argument("--ibkr-host", default=DEFAULT_IBKR_HOST)
    p.add_argument("--ibkr-port", type=int, default=DEFAULT_IBKR_PORT,
                   help="4001 Gateway live, 7496 TWS live (default: 4001)")
    p.add_argument("--price-cache", type=Path, default=None, metavar="FILE",
                   help="Price cache path (default: .price_cache.json next "
                        "to the first input)")
    p.add_argument("--price-cache-age", type=int,
                   default=DEFAULT_MAX_CACHE_AGE_DAYS, metavar="DAYS",
                   help="Days before a cache-served price warns "
                        "(default: %(default)s)")
    p.add_argument("--json", action="store_true",
                   help="Emit the report as JSON instead of text")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show per-tier price-chain diagnostics")
    args = p.parse_args(argv)

    if args.country is None:
        note(PROG, "--country not given; assuming canada")
        args.country = "canada"
    is_usa = args.country.strip().lower() in ("us", "usa")

    files = [Path(f) for f in args.files]
    positions = load_positions(files, include_options=args.options)
    if args.symbol:
        want = {s.upper() for s in args.symbol}
        positions = [r for r in positions if r["symbol"].upper() in want]
    if not positions:
        scope = (f" for {', '.join(args.symbol)}" if args.symbol else "")
        if args.json:
            print(json.dumps({"rows": [], "totals": {}}, indent=2,
                             sort_keys=True))
        else:
            print(f"No open positions{scope}.")
        return 0

    radar = load_radar([Path(r) for r in args.radar])
    # TX_ADD: last acquisition across the TAXABLE inputs themselves — a
    # taxable rebuy extends the wash window too (the AVGO case: a margin
    # buy pushed the clear date past what the sheltered adds implied).
    taxable_agg = load_inventory_agg(files, "TX_ADD")
    sheltered = load_inventory_agg([Path(f) for f in args.sheltered],
                                   "SH_QTY/SH_ADD")
    show_sheltered = bool(args.sheltered)
    basis = gains_basis_label(files)

    tickers = sorted({r["symbol"] for r in positions
                      if not r.get("is_option")})
    option_tickers = sorted({r["symbol"] for r in positions
                             if r.get("is_option")})
    total = len(tickers) + len(option_tickers)
    print(f"Resolving current prices for {total} symbol(s) "
          f"(IBKR -> yfinance -> cache"
          f"{'; options: IBKR -> cache' if option_tickers else ''})...",
          file=sys.stderr if args.json else sys.stdout)

    external_map = load_yf_map([Path(".")] + [f.parent for f in files])
    yf_map: Dict[str, str] = {}
    for t in tickers:
        if t in external_map:
            yf_map[t] = external_map[t][0]      # spelling only: positions
            continue                            # are in CURRENT tickers
        yf = yf_symbol_for(t)
        if yf is not None:
            yf_map[t] = yf

    cache_path = args.price_cache or (files[0].parent / ".price_cache.json")
    quotes = fetch_prices(yf_map, cache_path=cache_path,
                          max_cache_age_days=args.price_cache_age,
                          use_ibkr=not args.no_ibkr,
                          ibkr_host=args.ibkr_host,
                          ibkr_port=args.ibkr_port,
                          verbose=args.verbose,
                          fetchers=fetchers)
    if option_tickers:
        quotes.update(fetch_option_prices(
            option_tickers, cache_path=cache_path,
            max_cache_age_days=args.price_cache_age,
            use_ibkr=not args.no_ibkr,
            ibkr_host=args.ibkr_host, ibkr_port=args.ibkr_port,
            verbose=args.verbose, fetchers=option_fetchers))
    unpriced = sorted(set(tickers) - set(quotes))
    if unpriced:
        warn(PROG, f"no price from any tier for: {', '.join(unpriced)} "
                   f"— rows omitted.")
    unpriced_opts = sorted(set(option_tickers) - set(quotes))
    if unpriced_opts:
        warn(PROG, f"{len(unpriced_opts)} option position(s) unpriced "
                   f"(options quote only through IBKR — start TWS/"
                   f"Gateway): {', '.join(unpriced_opts)} — rows "
                   f"omitted.")

    # FX: book costs are BASE currency; quotes arrive in the quoted
    # symbol's native currency. Convert every non-base quote or drop the
    # row loudly — a USD price against CAD basis overstated a real
    # user's ETN.US loss by the full FX factor.
    base = args.base_currency.upper()
    rates_path = args.rates or (files[0].parent / "to_base.csv")
    fx_history = load_fx_history(rates_path, base)
    today_iso = date.today().isoformat()
    stale_warned: set = set()

    rows: List[Dict[str, Any]] = []
    tot_cost = tot_value = 0.0
    for r in positions:
        q = quotes.get(r["symbol"])
        if q is None:
            continue
        if r.get("is_option"):
            # Premium currency follows the UNDERLYING's listing — the
            # OCC string itself may carry no suffix (or embed it before
            # the contract block), which would misread as USD.
            qcur = quote_currency(
                parse_option_underlying(r["symbol"]) or r["symbol"])
        else:
            qcur = quote_currency(yf_map.get(r["symbol"], r["symbol"]))
        fx = 1.0
        if qcur != base:
            fx, rate_date = latest_rate(fx_history, qcur, today_iso)
            if fx is None:
                warn(PROG, f"no {qcur}->{base} rate in {rates_path} — "
                           f"{r['symbol']} omitted (run `taxjson run` to "
                           f"refresh rates).")
                continue
            age = (date.today() - date.fromisoformat(rate_date)).days
            if age > _STALE_RATE_DAYS and qcur not in stale_warned:
                stale_warned.add(qcur)
                warn(PROG, f"{qcur}->{base} rate is {age}d old "
                           f"({rate_date}) — run `taxjson run` to refresh.")
        mult = OPTION_MULTIPLIER if r.get("is_option") else 1.0
        value = r["qty"] * q.price * mult * fx
        unreal = value - r["cost"]
        pct = (unreal / abs(r["cost"]) * 100) if r["cost"] else 0.0
        verdict = ("LOSS" if unreal < -0.005
                   else "GAIN" if unreal > 0.005 else "FLAT")
        # NATIVE-currency price at which a full exit TODAY books no
        # base-currency loss: the (base-CCY) book cost converted back
        # into the trading currency at today's rate, per share/contract
        # unit, plus the 2% buffer. Long positions only — a short's
        # exit is a cover with inverted semantics.
        exit_native = None
        if (unreal < -0.005 and r["qty"] > 0 and r["cost"] > 0
                and fx > 0):
            exit_native = round(
                r["cost"] / (r["qty"] * mult * fx) * _BREAKEVEN_BUFFER,
                4)
        expiry = (parse_option_expiry(r["symbol"])
                  if r.get("is_option") else None)
        dte = None
        if expiry:
            try:
                dte = (date.fromisoformat(expiry) - date.today()).days
            except ValueError:
                pass
        sh = sheltered.get(r["symbol"])
        tx = taxable_agg.get(r["symbol"])
        rows.append({
            **r,
            "multiplier": mult,
            "expiry": expiry,
            "dte": dte,
            "taxable_last_add": (tx or {}).get("last_add"),
            "taxable_last_add_days": _days_from_today((tx or {}).get(
                "last_add")),
            "sheltered_qty": (sh or {}).get("qty", 0.0),
            "sheltered_last_add": (sh or {}).get("last_add"),
            "sheltered_last_add_days": _days_from_today((sh or {}).get(
                "last_add")),
            "price": q.price,
            "price_base": q.price * fx,
            "price_currency": qcur,
            "fx_rate": fx,
            "breakeven_exit_native": exit_native,
            "breakeven_exit_currency": (qcur if exit_native is not None else None),
            "price_source": q.source,
            "value": value,
            "unrealized": unreal,
            "pct": pct,
            "verdict": verdict,
            "days_to_long_term": (_days_to_long_term(r["start"])
                                  if is_usa else None),
            "radar": radar.get(r["symbol"]),
        })
        tot_cost += r["cost"]
        tot_value += value
    rows.sort(key=lambda x: x["unrealized"])    # harvestable losses first

    schedule = _recovery_schedule(rows)
    totals = {
        "cost": round(tot_cost, 2),
        "value": round(tot_value, 2),
        "unrealized": round(tot_value - tot_cost, 2),
        "currency": args.base_currency,
        "positions": len(rows),
        "basis": basis,
        "harvestable": schedule,
    }
    if args.json:
        print(json.dumps({"rows": rows, "totals": totals}, indent=2,
                         sort_keys=True))
        return 0

    # Everything is BASE currency: PRICE is the native quote x FX, with
    # its source marked (^ IBKR, + yfinance, * cache). TX_QTY is this
    # taxable account's shares; SH_QTY (with --sheltered) is the
    # sheltered side's total. TX_ADD / SH_ADD name the last acquisition
    # on each side as DATE(-Nd): BOTH extend the wash window — a
    # taxable rebuy defers the loss, a sheltered add denies it
    # permanently. Signed days: negative = past (adds), positive =
    # future (ADVISORY clear dates).
    show_options = bool(args.options)
    # Money columns carry their currency on a SECOND header line
    # ("CAD" under COST/SH etc.) — stacked, so the label never widens
    # a column the data hasn't already widened. EXIT@ values embed
    # their own (native) currency per row.
    _bc = args.base_currency
    header = ["ACCOUNT", "SYMBOL", "TX_QTY"]
    if show_sheltered:
        header.append("SH_QTY")
    header += [f"COST/SH\n{_bc}", f"PRICE\n{_bc}", "EXIT@\nnative",
               f"UNREALIZED\n{_bc}", "PCT", "VERDICT"]
    if show_options:
        header.append("DTE")
    if is_usa:
        header.append("LT_IN")
    header.append("TX_ADD")
    if show_sheltered:
        header.append("SH_ADD")
    header.append("ADVISORY")
    aligns = ["<", "<", ">"] + ([">"] if show_sheltered else []) \
        + [">", ">", ">", ">", ">", "<"] \
        + (["<"] if show_options else []) \
        + (["<"] if is_usa else []) + ["<"] \
        + (["<"] if show_sheltered else []) + ["<"]
    body = []
    for r in rows:
        # Per-share terms for BOTH cells: an option's book cost covers
        # qty contracts x 100 shares, so divide by the multiplier too —
        # COST/SH then compares 1:1 against the per-share premium.
        denom = r["qty"] * r.get("multiplier", 1.0)
        cps = r["cost"] / denom if denom else 0.0
        cells = [r["account"], r["symbol"], _qfmt(r["qty"])]
        if show_sheltered:
            cells.append(_qfmt(r["sheltered_qty"])
                         if r["sheltered_qty"] else "-")
        _be = r.get("breakeven_exit_native")
        cells += [f"{cps:,.4f}",
                  _price_display(r["price_base"], r["price_source"]),
                  (f"{_be:,.4f}{r['breakeven_exit_currency']}"
                   if _be is not None else "-"),
                  fmt_money(r["unrealized"]),
                  f"{r['pct']:.1f}%", r["verdict"]]
        if show_options:
            d = r.get("dte")
            cells.append("-" if d is None
                         else ("EXP" if d <= 0 else f"{d}d"))
        if is_usa:
            d = r["days_to_long_term"]
            cells.append("LT" if d == 0 else (f"{d}d" if d is not None
                                              else "-"))
        cells.append(_add_display(taxable_agg.get(r["symbol"])))
        if show_sheltered:
            cells.append(_add_display(sheltered.get(r["symbol"])))
        cells.append(_advisory_display(r["radar"])
                     if r["verdict"] == "LOSS" else "-")
        body.append([str(c) for c in cells])
    total_cells = ["TOTAL", "-", "-", "-", "-", "-",
                   fmt_money(tot_value - tot_cost),
                   (f"{(tot_value - tot_cost) / abs(tot_cost) * 100:.1f}%"
                    if tot_cost else "0.0%"), "-"]
    if show_sheltered:
        total_cells.insert(3, "-")
    if show_options:
        total_cells.append("-")                 # DTE
    if is_usa:
        total_cells.append("-")
    total_cells.append("-")                     # TX_ADD
    if show_sheltered:
        total_cells.append("-")                 # SH_ADD
    total_cells.append("-")                     # ADVISORY

    print(f"\nHARVEST — unrealized open positions, {args.base_currency}, "
          f"basis: {basis}  (PRICE marks its source: ^ IBKR, "
          f"+ yfinance, * cache; losses first; ADVISORY from the "
          f"wash radar)")
    print()
    from taxjson.lib.report_model import render_table
    for line in render_table(header, aligns, body,
                             foot=[total_cells], gap="   "):
        print(line)

    # When can the paper losses become CLAIMED losses? Cumulative
    # schedule from the radar's clear dates.
    if any(r["verdict"] == "LOSS" for r in rows):
        cur = args.base_currency
        parts = [f"now {fmt_money(schedule['now'])}"]
        for k in ("7d", "14d", "30d"):
            parts.append(f"<={k} {fmt_money(schedule[k])}")
        if schedule["later"] > schedule["30d"]:
            parts.append(f"later {fmt_money(schedule['later'])}")
        line = f"\nHARVESTABLE LOSSES ({cur}, cumulative): " + \
               " | ".join(parts)
        if schedule["no_clear"]:
            line += (f"  [+{fmt_money(schedule['no_clear'])} with no "
                     f"clear date — see ADVISORY]")
        print(line)
        _risk_now = sum(abs(float(r.get("unrealized") or 0.0))
                        for r in rows
                        if r.get("verdict") == "LOSS"
                        and ((r.get("radar") or {}).get("category")
                             == "RISK"))
        if _risk_now > 0.005:
            print(f"RISK rows ({fmt_money(_risk_now)}) count as "
                  f"claimable now — no buys inside the past 30 days; "
                  f"pause DRIPs/sheltered adds for 30 days AFTER "
                  f"selling or the denial is permanent.")
        _blocked_now = sum(abs(float(r.get("unrealized") or 0.0))
                           for r in rows
                           if r.get("verdict") == "LOSS"
                           and ((r.get("radar") or {}).get("category")
                                == "BLOCKED"))
        if _blocked_now > 0.005:
            print(f"BLOCKED rows ({fmt_money(_blocked_now)}) count as "
                  f"claimable now — a recent loss with no buys inside "
                  f"its window; selling more at a loss today is clean. "
                  f"Do NOT rebuy before the no-rebuy-until date or "
                  f"BOTH losses are denied.")
        print("Estimates at today's prices; any new buy on either side "
              "pushes a clear date out.")
    legend = ["TX_ADD: last buy in the taxable accounts — a rebuy "
              "within 30 days of a loss sale defers the loss (it moves "
              "into the new shares' basis) and extends the clear date.",
              "EXIT@: the NATIVE-currency price at which a full exit "
              "today books no base-currency loss — book cost converted "
              "at today's FX rate, plus a 2% buffer for fees/slippage/"
              "FX drift. LOSS rows (long) only."]
    if show_options:
        legend.append("Options: PRICE and COST/SH are per-share premium "
                      "(UNREALIZED carries the x100 contract "
                      "multiplier); DTE = days to expiry. The radar "
                      "does not track option contracts — rebuying the "
                      "SAME contract within 30 days of a loss sale "
                      "still triggers the wash/superficial rule.")
    if show_sheltered:
        legend.append("SH_QTY: shares held across sheltered accounts. "
                      "SH_ADD: last sheltered buy — one within 30 days "
                      "either side of a loss sale makes the loss "
                      "PERMANENTLY denied, not deferred.")
    legend.append("Signed days: -Nd = N days ago, +Nd = N days ahead "
                  "(ADVISORY clear dates). The radar's clear date runs "
                  "from the MOST RECENT buy on either side.")
    tot_deferred = sum(float(r.get("deferred_wash") or 0) for r in rows)
    if tot_deferred > 0.005:
        carriers = sum(1 for r in rows
                       if float(r.get("deferred_wash") or 0) > 0.005)
        legend.append(f"DEFERRED WASH: {fmt_money(tot_deferred)} of the "
                      f"book cost across {carriers} position(s) is "
                      f"prior DENIED losses — UNREALIZED on those rows "
                      f"includes recycled loss, not only new loss "
                      f"(per-row amounts in --json / `taxjson list`).")
    print("\n" + "\n".join(legend))
    if is_usa:
        print("\nLT_IN approximates from the position start date; per-lot "
              "ST/LT is decided by the engine at sale time.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
