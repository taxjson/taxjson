#!/usr/bin/env python3
"""
taxjson_export.py

Export holdings to various platform formats, or as a plain-text /
TOML holdings report.

Input may be taxjson_gains.py JSON (an `inventory` section) or a
taxjson holdings TOML snapshot (a `.toml` file of `[[holding]]`
tables, as written by `--holdings-toml`).
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from taxjson.lib.report_model import load_report_json
from taxjson.lib import cli_diag

PROG = "taxjson-export"
from typing import Any, Dict, List

from taxjson.lib.tomlcompat import tomllib

from taxjson.lib.ticker_map import (
    is_option_ticker, is_future_ticker, get_base_ticker_info,
    format_ticker_for_platform,
)
from taxjson.bin.taxjson_ticker_map import load_map_file


def _passes_filters(item: Dict[str, Any], args) -> bool:
    symbol = item.get("symbol", "")
    qty = item.get("qty", 0)

    if args.long and qty <= 0:
        return False
    if args.short and qty >= 0:
        return False

    is_opt = is_option_ticker(symbol)
    is_fut = is_future_ticker(symbol)
    is_eq = not is_opt and not is_fut

    if is_opt and args.no_options:
        return False
    if is_fut and args.no_futures:
        return False
    if is_eq and args.no_equities:
        return False

    _, ext = get_base_ticker_info(symbol)
    # All Canadian market suffixes, not just TSX: TSXV (.V), CSE (.CN),
    # and NEO (.NE) are CAD listings too — matching only .TO let them
    # pass BOTH --no-cad and --no-usd and land in every currency-split
    # export.
    if ext in ('TO', 'V', 'CN', 'NE') and args.no_cad:
        return False
    if ext == 'US' and args.no_usd:
        return False
    return True


def process_data_platform(data, args, seen, results, tv_map):
    """Existing behavior: emit platform-formatted ticker strings."""
    inventory = data.get("inventory", [])
    if not inventory:
        if "inventory" not in data:
            if len(args.inputs) <= 1:
                cli_diag.error(PROG, "no 'inventory' section found in input JSON")
            return

    for item in inventory:
        if not _passes_filters(item, args):
            continue
        formatted = format_ticker_for_platform(item.get("symbol"), args.platform, tv_map)
        if formatted and formatted not in seen:
            results.append(formatted)
            seen.add(formatted)


def _apply_transfer_evidence(agg: Dict[str, Dict[str, Any]],
                             evidence_paths, tmap) -> None:
    """Evidence-driven depot flips: re-symbol holdings quantities that
    the TRANSFER sidecar PROVES were journaled between listings of the
    same security — and only those quantities.

    A JOURNAL map line re-symbols unconditionally, which is right for
    intrinsically fungible classes (DLR.U.TO/DLR.TO exist for
    Norbert's Gambit) but wrong as a blanket statement for ordinary
    cross-listings: whether 300 OR.US became OR.TO is a FACT recorded
    by the broker's InterDepot rows, not a timeless property of the
    symbol pair (2026-09 design review). So: net the sidecar's
    TRANSFER quantities per symbol, and within each map-declared
    identity class (GLOBAL/TOBASE/JOURNAL union) move matched
    negative→positive residuals between buckets, capped at what the
    source bucket actually holds. Shares are never created or
    destroyed: unpaired residuals (broker migrations in/out — ATON)
    are ignored, and a flip that was flipped back nets to zero and
    moves nothing.

    Caveats (round-five audit): residual matching is quantity-based
    with no temporal pairing — an out-leg and in-leg YEARS apart in
    one sidecar still match if nothing else nets them (rare: sidecars
    are per-broker and custody churn clusters tightly). Passing the
    same sidecar twice on a direct CLI invocation doubles the move up
    to the held-quantity cap — the orchestrator's per-broker glob
    cannot duplicate."""
    import json as _json
    applied: list = []
    if not evidence_paths or tmap is None:
        return applied
    # Evidence symbols go through the SAME journal fold the buckets
    # did — a journal pair's out/in legs then land on one key and
    # cancel, so intrinsically-fungible (JOURNAL) classes are never
    # evidence-moved on top of their fold (round-five audit finding 4:
    # the un-folded net re-symboled shares already sold through the
    # journaled-to listing).
    _fold = dict(tmap.journal)
    net: Dict[str, float] = {}
    for p in evidence_paths:
        try:
            doc = _json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, _json.JSONDecodeError) as e:
            print(f"warning: could not read transfer evidence {p}: {e}",
                  file=sys.stderr)
            continue
        if (doc.get("metadata") or {}).get("kind") != "transfer_sidecar":
            continue
        for t in doc.get("transactions") or []:
            if t.get("action") != "TRANSFER":
                continue
            s = _fold.get(t.get("symbol") or "", t.get("symbol") or "")
            if s:
                net[s] = net.get(s, 0.0) + float(t.get("quantity") or 0)
    if not net:
        return applied
    # Identity classes: union-find over every map pair.
    parent: Dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for d in (tmap.glob, tmap.tobase, tmap.journal):
        for a, b in d.items():
            parent[find(a)] = find(b)
    by_class: Dict[str, list] = {}
    for s, q in net.items():
        if abs(q) > 1e-9:
            by_class.setdefault(find(s), []).append((s, q))
    for members in by_class.values():
        srcs = [[s, -q] for s, q in members if q < 0]
        dsts = [[s, q] for s, q in members if q > 0]
        if not srcs or not dsts:
            continue                      # migration in/out — not a flip
        for src in srcs:
            b = agg.get(src[0])
            for dst in dsts:
                if src[1] <= 1e-9:
                    break
                if dst[1] <= 1e-9 or b is None:
                    continue
                move = min(src[1], dst[1], max(0.0, b.get("qty", 0.0)))
                if move <= 1e-9:
                    continue
                frac = move / b["qty"]
                d = agg.setdefault(dst[0], {
                    "qty": 0.0, "total_cost": 0.0, "currency": "",
                    "position_start_date": None})
                d["qty"] += move
                d["total_cost"] += b["total_cost"] * frac
                d.setdefault("cost_by_currency", {})
                for cur, amt in (b.get("cost_by_currency") or {}).items():
                    d["cost_by_currency"][cur] = (
                        d["cost_by_currency"].get(cur, 0.0) + amt * frac)
                if (b.get("currency") and d.get("currency")
                        and b["currency"] != d["currency"]):
                    d["mixed_currency"] = True
                elif b.get("currency") and not d.get("currency"):
                    d["currency"] = b["currency"]
                psd = b.get("position_start_date")
                if psd and (d.get("position_start_date") is None
                            or psd < d["position_start_date"]):
                    d["position_start_date"] = psd
                b["total_cost"] -= b["total_cost"] * frac
                for cur in list((b.get("cost_by_currency") or {})):
                    b["cost_by_currency"][cur] *= (1 - frac)
                b["qty"] -= move
                src[1] -= move
                dst[1] -= move
                applied.append((src[0], dst[0], move))
                print(f"note: applied evidenced depot flip: {move:g} "
                      f"{src[0]} -> {dst[0]} (transfer sidecar).",
                      file=sys.stderr)
            if b is not None and abs(b.get("qty", 0.0)) <= 1e-9 \
                    and abs(b.get("total_cost", 0.0)) <= 0.005:
                agg.pop(src[0], None)
    return applied


def _replay_moves_on_base(base_agg: Dict[str, Dict[str, Any]],
                          moves, tmap) -> None:
    """The base inventory must mirror the native pass's applied
    moves exactly. It is keyed like the native one — GLOBAL renames
    applied upstream, JOURNAL folded at aggregation, cross-listings
    kept per-listing — so endpoints fold through the JOURNAL set only
    (a journal pair's move is a no-op on its folded bucket; a TOBASE
    pair's move REPLAYS, moving the base cost with the shares).
    Deriving moves independently, or folding through the to-base
    renames, both diverge (round-five finding 5 / round-six
    finding 1)."""
    if not moves or tmap is None:
        return
    # Fold endpoints through the SAME rename set base_agg was built
    # with — the JOURNAL fold only. The base inventory comes from the
    # RAW-base pipeline, which applies GLOBAL renames upstream and
    # keeps cross-listings per-listing (no TOBASE consolidation), so
    # folding through merge_renames(to_base=True) here made every
    # TOBASE-pair move a "same key" no-op and the flipped shares'
    # base cost silently vanished (round-six adversarial audit,
    # finding 1 — a HIGH regression over the round-five fix).
    ren = dict(tmap.journal)
    for src, dst, qty in moves:
        bs = ren.get(src, src)
        bd = ren.get(dst, dst)
        if bs == bd:
            continue
        b = base_agg.get(bs)
        if not b or b.get("qty", 0.0) <= 1e-9:
            continue
        move = min(qty, b["qty"])
        frac = move / b["qty"]
        d = base_agg.setdefault(bd, {
            "qty": 0.0, "total_cost": 0.0,
            "currency": b.get("currency", ""),
            "position_start_date": None})
        d["qty"] += move
        d["total_cost"] += b["total_cost"] * frac
        d.setdefault("cost_by_currency", {})
        for cur, amt in (b.get("cost_by_currency") or {}).items():
            d["cost_by_currency"][cur] = (
                d["cost_by_currency"].get(cur, 0.0) + amt * frac)
        b["total_cost"] -= b["total_cost"] * frac
        for cur in list((b.get("cost_by_currency") or {})):
            b["cost_by_currency"][cur] *= (1 - frac)
        b["qty"] -= move
        if abs(b.get("qty", 0.0)) <= 1e-9 \
                and abs(b.get("total_cost", 0.0)) <= 0.005:
            base_agg.pop(bs, None)


def process_data_report(data, args, agg: Dict[str, Dict[str, Any]],
                         mapping: Dict[str, str] = None, drops=None):
    """Report mode: aggregate inventory entries per symbol across files.
    Sums qty and total_cost; tracks currency (warns on mismatch).

    `mapping` (from --map) is applied to each symbol before bucketing, so
    e.g. a Norbert's Gambit DLR.US leg folds into DLR.TO and the
    offsetting quantities net out. This runs here, post-gains, rather
    than before the gains engine — the legs are in different currencies
    and a single ACB pool must be one currency. `drops` (the map file's
    `<symbol> DROP` lines) excludes a ticker's inventory entirely."""
    if mapping is None:
        mapping = {}
    if drops is None:
        drops = set()
    inventory = data.get("inventory", [])
    if not inventory and "inventory" not in data:
        if len(args.inputs) <= 1:
            cli_diag.error(PROG, "no 'inventory' section found in input JSON")
        return
    for item in inventory:
        if not _passes_filters(item, args):
            continue
        sym = item.get("symbol")
        if not sym:
            continue
        sym = mapping.get(sym, sym)
        if sym in drops:
            continue
        bucket = agg.setdefault(sym, {'qty': 0.0, 'total_cost': 0.0,
                                      'currency': '',
                                      'position_start_date': None})
        bucket['qty'] += float(item.get('qty', 0))
        bucket['total_cost'] += float(item.get('total_cost', 0))
        # Per-currency sub-buckets: JOURNAL folds (DLR.US -> DLR.TO)
        # deliberately merge cross-currency listings, and summing their
        # NATIVE costs into one number made total_cost currency salad.
        # The mixed-flag consumer blanks the flat figure downstream.
        _c = item.get('currency') or '?'
        bucket.setdefault('cost_by_currency', {})
        bucket['cost_by_currency'][_c] = (
            bucket['cost_by_currency'].get(_c, 0.0)
            + float(item.get('total_cost', 0)))
        # Carry the EARLIEST position_start across all inputs that
        # contribute to this symbol's bucket (e.g. when aggregating
        # gains JSONs from multiple accounts via `taxjson-export`).
        psd = item.get('position_start_date')
        if psd:
            if bucket['position_start_date'] is None or psd < bucket['position_start_date']:
                bucket['position_start_date'] = psd
        cur = item.get('currency') or ''
        if cur and bucket['currency'] and cur != bucket['currency']:
            bucket['mixed_currency'] = True
            print(
                f"warning: {sym} appears in multiple inputs with mismatched currencies "
                f"({bucket['currency']} vs {cur}); native total_cost is "
                f"omitted for it (per-currency figures kept; "
                f"base_total_cost is unaffected)",
                file=sys.stderr,
            )
        elif cur:
            bucket['currency'] = cur


_OPTION_CONTRACT_MULTIPLIER = 100


def render_report(agg: Dict[str, Dict[str, Any]],
                   dust_threshold: float = 1e-9) -> List[str]:
    """Format the aggregated holdings as a fixed-width text table.

    Rows are sorted alphabetically by symbol. Cost/share for stocks is
    total_cost / qty. For options the convention is per-underlying-share
    (option chains and brokerage tickets quote in those units), so the
    per-contract figure is divided by the 100-share contract multiplier.
    Shorts naturally produce positive cost/share since total_cost flips
    sign with qty.

    Positions with |quantity| below `dust_threshold` are dropped — a
    sub-fractional residue (e.g. -3.3e-05 shares left by a corp-action
    ratio) is float noise, not a real holding.
    """
    rows = []
    for sym in sorted(agg.keys()):
        b = agg[sym]
        qty = b['qty']
        total = b['total_cost']
        if abs(qty) < dust_threshold:
            continue
        # A fully-netted JOURNAL pair leaves a qty-0 inventory row;
        # --dust-threshold 0 keeps it, so guard the division.
        cps = total / qty if abs(qty) > 1e-12 else 0.0
        if is_option_ticker(sym):
            cps /= _OPTION_CONTRACT_MULTIPLIER
        rows.append((sym, qty, cps, total,
                     "MIXED" if b.get('mixed_currency')
                     else b['currency']))

    if not rows:
        return []

    # Determine column widths from data.
    has_currency = any(r[4] for r in rows)
    sym_w = max(6, max(len(r[0]) for r in rows))
    qty_w = max(10, max(len(_fmt_qty(r[1])) for r in rows))
    cps_w = max(10, max(len(f"{r[2]:.4f}") for r in rows))
    tot_w = max(12, max(len(f"{r[3]:.2f}") for r in rows))
    cur_w = max(3, max((len(r[4]) for r in rows), default=3)) if has_currency else 0

    header_parts = [
        f"{'TICKER':<{sym_w}}",
        f"{'QTY':>{qty_w}}",
        f"{'COST/SHARE':>{cps_w}}",
        f"{'TOTAL COST':>{tot_w}}",
    ]
    if has_currency:
        header_parts.append(f"{'CUR':<{cur_w}}")
    header = "  ".join(header_parts)
    sep = "-" * len(header)

    banner_w = max(len(header), 100)
    out = [
        "",
        "=" * banner_w,
        " HOLDINGS REPORT - Sorted by: ticker",
        "=" * banner_w,
        header,
        sep,
    ]
    for sym, qty, cps, total, cur in rows:
        line_parts = [
            f"{sym:<{sym_w}}",
            f"{_fmt_qty(qty):>{qty_w}}",
            f"{cps:>{cps_w}.4f}",
            f"{total:>{tot_w}.2f}",
        ]
        if has_currency:
            line_parts.append(f"{cur:<{cur_w}}")
        out.append("  ".join(line_parts))
    return out


# Alias: tests and older callers import `_fmt_qty` from here.
from taxjson.lib.report_model import fmt_qty6 as _fmt_qty  # noqa: E402


# --------------------------------------------------------- TOML holdings

# OCC option symbol: base ticker + YYMMDD + C/P + strike*1000, optional
# market suffix (and optional F:/slash futures prefix on the base).
_OCC_RE = re.compile(
    r'^((?:F:|[\\/])?[A-Z0-9.]{1,10}?)'
    r'(\d{6})([CP])(\d+)'
    r'(?:\.([A-Z]+))?$'
)


def _parse_option(symbol: str):
    """Break an OCC option symbol into underlying/right/strike/expiry,
    or return None if `symbol` isn't an option."""
    m = _OCC_RE.match(symbol or '')
    if not m:
        return None
    base, ymd, right, strike_raw, ext = m.groups()
    try:
        expiry = datetime.strptime(ymd, "%y%m%d").date().isoformat()
    except ValueError:
        expiry = None
    return {
        'underlying': f"{base}.{ext}" if ext else base,
        'right': 'call' if right == 'C' else 'put',
        'strike': int(strike_raw) / 1000.0,
        'expiry': expiry,
    }


def _toml_str(value: Any) -> str:
    """Render a value as a TOML basic (quoted) string."""
    s = str(value).replace('\\', '\\\\').replace('"', '\\"')
    s = s.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
    return f'"{s}"'


def _load_trade_events(paths, mapping=None, drops=None,
                       current_position_only=True) -> Dict[str, List[Dict[str, Any]]]:
    """Group BUYSELL/ASSIGN rows from pre-gains transaction file(s) into a
    per-symbol list of {date, action, qty, price} events, in native currency.

    `mapping`/`drops` (the holdings --map) are applied to the symbol so events
    attach to the same key the aggregated holding uses. Quantity sign
    determines action (positive → BUY, negative → SELL).

    With `current_position_only` (the default), only the events that make up
    the CURRENT position are kept: events are trimmed to those after the last
    time the running balance returned to zero. A ticker bought, fully sold,
    then re-bought reports only the latest round — the events that actually
    contribute to the live cost basis. This boundary equals the gains engine's
    `position_start_date`. Ordering uses settlement date (matching the engine)
    but each event keeps its trade `date` for charting."""
    mapping = mapping or {}
    drops = drops or set()
    raw: Dict[str, List[Dict[str, Any]]] = {}
    for p in paths:
        try:
            data = load_report_json(p)
        except (json.JSONDecodeError, FileNotFoundError, OSError) as e:
            print(f"Error loading trades {p}: {e}", file=sys.stderr)
            continue
        for tx in data.get("transactions", []):
            if tx.get("action") not in ("BUYSELL", "ASSIGN"):
                continue
            qty = float(tx.get("quantity") or 0)
            if qty == 0:
                continue
            sym = tx.get("symbol")
            if not sym:
                continue
            sym = mapping.get(sym, sym)
            if sym in drops:
                continue
            raw.setdefault(sym, []).append({
                "sort_key": (tx.get("date_settle") or tx.get("date") or "",
                             tx.get("time") or ""),
                "date": tx.get("date"),
                "qty": qty,  # signed
                "price": float(tx.get("price") or 0.0),
            })
    by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for sym, evs in raw.items():
        evs.sort(key=lambda e: e["sort_key"])
        if current_position_only:
            run, start = 0.0, 0
            for i, e in enumerate(evs):
                run += e["qty"]
                if abs(run) < 1e-6:      # position flat → current round starts after
                    start = i + 1
            evs = evs[start:]
        by_symbol[sym] = [{
            "date": e["date"],
            "action": "BUY" if e["qty"] > 0 else "SELL",
            "qty": abs(e["qty"]),
            "price": e["price"],
        } for e in evs]
    return by_symbol


def render_holdings_toml(agg: Dict[str, Dict[str, Any]], args,
                         base_agg: Dict[str, Dict[str, Any]] = None,
                         trades_by_symbol: Dict[str, List[Dict[str, Any]]] = None) -> List[str]:
    """Render aggregated holdings as a TOML document: a [meta] table plus
    one [[holding]] array-of-tables entry per open position. Designed as a
    machine-readable, human-legible handoff for live-pricing / trading
    tools — `total_cost` is the source of truth; `cost_per_share` is
    derived convenience. Quantities keep their sign, so a short shows
    negative. Positions with |quantity| below args.dust_threshold are
    dropped — a sub-fractional residue is float noise, not a holding.

    `base_agg` (optional) is the same aggregation computed on
    base-currency-converted gains; when a symbol is present there, the
    holding also carries `base_currency`, `base_total_cost` and
    `base_cost_per_share`, letting downstream tools judge a position's
    gain/loss against a base-currency price without re-running FX."""
    base_agg = base_agg or {}
    dust = getattr(args, 'dust_threshold', 1e-9)
    rows = []
    for sym in sorted(agg):
        b = agg[sym]
        if abs(b['qty']) < dust:
            continue
        rows.append((sym, b['qty'], b['total_cost'], b['currency'],
                     b.get('position_start_date'), b))

    lines = [
        "# taxjson holdings snapshot — generated by taxjson-export.",
        "# Regenerated on every run; do not hand-edit. Cost basis is a",
        "# snapshot as of meta.generated_at, not a live valuation.",
        'schema_version = "1.2"',
        "",
        "[meta]",
        f"generated_at = {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
    ]
    if args.account_name:
        lines.append(f"account = {_toml_str(args.account_name)}")
    src = ", ".join(Path(p).name for p in args.inputs) or "<stdin>"
    lines.append(f"source = {_toml_str(src)}")
    lines.append(f"holdings_count = {len(rows)}")
    lines.append("")

    for sym, qty, total_cost, currency, position_start_date, b in rows:
        opt = _parse_option(sym)
        mixed = bool(b.get('mixed_currency'))
        cps = total_cost / qty if qty else 0.0
        lines.append("[[holding]]")
        lines.append(f"symbol = {_toml_str(sym)}")
        if args.account_name:
            lines.append(f"account = {_toml_str(args.account_name)}")
        lines.append(f"asset_type = {_toml_str('option' if opt else 'equity')}")
        if opt:
            lines.append(f"underlying = {_toml_str(opt['underlying'])}")
            lines.append(f"right = {_toml_str(opt['right'])}")
            lines.append(f"strike = {opt['strike']!r}")
            if opt['expiry']:
                # TOML local date — bare, unquoted.
                lines.append(f"expiry = {opt['expiry']}")
            lines.append("contract_multiplier = 100")
        lines.append(f"quantity = {float(qty)!r}")
        if mixed:
            # A JOURNAL-folded cross-currency bucket: summing native
            # USD+CAD costs into one number was currency salad. The
            # flat figures are omitted; per-currency components and
            # base_total_cost (converted, below) carry the truth.
            lines.append("mixed_currency = true")
            for _c, _v in sorted((b.get('cost_by_currency')
                                  or {}).items()):
                lines.append(f"total_cost_{_c.lower()} = "
                             f"{round(_v, 4)!r}")
        else:
            if currency:
                lines.append(f"currency = {_toml_str(currency)}")
            lines.append(f"total_cost = {round(total_cost, 4)!r}")
            lines.append(f"cost_per_share = {round(cps, 6)!r}")
        base = base_agg.get(sym)
        expected_base = getattr(args, 'base_currency', None)
        base_ok = (base is not None and base.get('currency')
                   and (expected_base is None
                        or base['currency'].upper() == expected_base.upper()))
        if base_ok:
            base_total = base['total_cost']
            # Prefer the base bucket's own quantity for the per-share figure;
            # it equals the native qty today (FX conversion never touches
            # quantity) but is the correct denominator if the two passes ever
            # diverge. Fall back to native qty if base qty is degenerate.
            base_qty = base.get('qty') or qty
            base_cps = base_total / base_qty if base_qty else 0.0
            lines.append(f"base_currency = {_toml_str(base['currency'])}")
            lines.append(f"base_total_cost = {round(base_total, 4)!r}")
            lines.append(f"base_cost_per_share = {round(base_cps, 6)!r}")
        if position_start_date:
            # TOML local date — bare YYYY-MM-DD, unquoted, parses
            # back as a date object via tomllib.
            lines.append(f"position_start_date = {position_start_date}")
        if trades_by_symbol is not None:
            # Native-currency acquisition/sell events for this symbol, for
            # charting/annotation downstream. Emitted as a TOML array of
            # inline tables; dates are bare TOML local dates.
            evs = trades_by_symbol.get(sym, [])
            if evs:
                lines.append("trades = [")
                for e in evs:
                    lines.append(
                        f"  {{ date = {e['date']}, action = {_toml_str(e['action'])}, "
                        f"qty = {float(e['qty'])!r}, price = {float(e['price'])!r} }},")
                lines.append("]")
            else:
                lines.append("trades = []")
        lines.append("")
    return lines


def _holdings_toml_to_inventory(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Adapt a taxjson holdings TOML document (`[[holding]]` tables) to
    the `inventory`-shaped dict the rest of this tool consumes: `quantity`
    maps to `qty`; symbol / total_cost / currency / position_start_date
    pass through. The TOML [meta] table and derived `cost_per_share`
    are ignored. TOML parses a bare YYYY-MM-DD as a date object, so the
    adapter stringifies it for downstream consistency."""
    inventory = []
    for h in doc.get("holding", []):
        psd = h.get("position_start_date")
        if psd is not None and not isinstance(psd, str):
            psd = psd.isoformat()
        inventory.append({
            "symbol": h.get("symbol"),
            "qty": h.get("quantity", 0),
            "total_cost": h.get("total_cost", 0),
            "currency": h.get("currency", ""),
            "position_start_date": psd,
        })
    return {"inventory": inventory}


def main():
    parser = argparse.ArgumentParser(
        description="Export holdings to various formats or as a text report.",
    )
    parser.add_argument(
        "--transfer-evidence", action="append", default=[],
        metavar="SIDECAR",
        help="Transfer-sidecar JSON(s) (work/<acct>_<broker>_transfers"
             ".json). Holdings mode applies EVIDENCED depot flips from "
             "them: quantities the broker's transfer rows prove moved "
             "between listings of one security (per the --map identity "
             "classes) are re-symboled — and only those quantities.")
    parser.add_argument(
        "inputs", nargs="*",
        help="Input files: taxjson_gains.py JSON, or holdings TOML "
             "snapshots (.toml, detected by extension). Default: stdin (JSON).",
    )

    # Output mode (mutually exclusive)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--seekingalpha", action="store_const", dest="platform", const="seekingalpha")
    group.add_argument("--tradingview", action="store_const", dest="platform", const="tradingview")
    group.add_argument("--fastgraph", action="store_const", dest="platform", const="fastgraph")
    group.add_argument(
        "--report", action="store_const", dest="platform", const="report",
        help="Output a plain-text holdings table: Ticker, Qty, Cost/Share, Total Cost.",
    )
    group.add_argument(
        "--holdings-toml", action="store_const", dest="platform", const="holdings_toml",
        help="Output holdings as a TOML document (a [meta] table plus one "
             "[[holding]] entry per position) — a machine-readable handoff "
             "for live-pricing / trading tools.",
    )
    parser.add_argument(
        "--account-name", default=None,
        help="Account name to stamp into the --holdings-toml output.",
    )
    parser.add_argument(
        "--map", dest="map_file", metavar="FILE", default=None,
        help="Ticker-map file applied while aggregating --report / "
             "--holdings-toml output. Use it to net cross-currency "
             "Norbert's Gambit legs (e.g. a `DLR.US DLR.TO` line folds "
             "the USD leg into the CAD symbol so the two cancel).",
    )

    # Filters
    parser.add_argument("--no-equities", action="store_true")
    parser.add_argument("--no-options", action="store_true")
    parser.add_argument("--no-futures", action="store_true", default=True)
    parser.add_argument("--futures", action="store_false", dest="no_futures")
    parser.add_argument("--no-cad", action="store_true")
    parser.add_argument("--no-usd", action="store_true")
    parser.add_argument("--short", action="store_true", help="Only short positions")
    parser.add_argument("--long", action="store_true", help="Only long positions")
    parser.add_argument(
        "--dust-threshold", type=float, default=1e-3, metavar="QTY",
        help="Drop --report / --holdings-toml positions whose absolute "
             "quantity is below this (default: 0.001). Filters out "
             "sub-fractional residue left by corp-action ratios and "
             "float arithmetic. Pass 0 to keep every position.",
    )
    parser.add_argument(
        "--base-gains", action="append", default=[], metavar="FILE",
        help="Base-currency gains JSON(s) for --holdings-toml mode. These "
             "hold the SAME positions as the native inputs but with amounts "
             "currency-converted to the base currency (per-lot, at "
             "acquisition-date FX), so each holding can carry "
             "base_currency / base_total_cost / base_cost_per_share. "
             "Symbols must line up 1:1 with the native inputs (i.e. produced "
             "from the same pre-conversion merge, so no TOBASE "
             "cross-listing consolidation).",
    )
    parser.add_argument(
        "--base-currency", metavar="CURR", default=None,
        help="Expected currency of --base-gains figures (e.g. CAD). When set, "
             "a base bucket whose currency does not match is skipped rather "
             "than emitted — a guard against a partially-failed FX conversion "
             "leaving a holding in its source currency and mislabelling it as "
             "the base figure.",
    )
    parser.add_argument(
        "--trades", action="append", default=[], metavar="FILE",
        help="Pre-gains transaction JSON(s) (e.g. the merged <account>_raw.json) "
             "for --holdings-toml mode. Each holding then carries a `trades` "
             "array of the acquisition/sell events that make up its CURRENT "
             "position ({date, action, qty, price}) in NATIVE currency — so a "
             "downstream tool can annotate the live entries/exits on a chart. "
             "Events from a prior round that was fully closed out are excluded "
             "(trimmed to the last flat-to-now segment, matching "
             "position_start_date). Prices are verbatim (never FX-converted).",
    )

    args = parser.parse_args()

    seen = set()
    results: List[str] = []
    agg: Dict[str, Dict[str, Any]] = {}
    tv_map: Dict[str, str] = {}

    # Load TradingView map if needed
    if args.platform == "tradingview":
        search_dirs = [Path(".")]
        for input_path in args.inputs:
            search_dirs.append(Path(input_path).parent)
        for d in search_dirs:
            map_file = d / "tv_exchange.map"
            if map_file.exists():
                with open(map_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        parts = line.split()
                        if len(parts) >= 2:
                            tv_map[parts[0]] = parts[1]
                break

    # The holdings aggregation applies JOURNAL renames — they net
    # offsetting cross-currency legs (Norbert's Gambit) here, post-gains,
    # since they can't be merged before the gains engine. DELETE is
    # honored too (idempotent — the merge already applied it).
    if args.map_file:
        _tmap = load_map_file(Path(args.map_file))
        holdings_map, holdings_drops = _tmap.journal, _tmap.delete
    else:
        _tmap = None
        holdings_map, holdings_drops = {}, set()

    def dispatch(data):
        if args.platform in ("report", "holdings_toml"):
            process_data_report(data, args, agg, holdings_map, holdings_drops)
        else:
            process_data_platform(data, args, seen, results, tv_map)

    if not args.inputs:
        try:
            dispatch(load_report_json(None))
        except json.JSONDecodeError as e:
            print(f"Error loading stdin: {e}", file=sys.stderr)
    else:
        for input_path in args.inputs:
            if str(input_path).lower().endswith(".toml"):
                if tomllib is None:
                    print(f"Error loading {input_path}: reading TOML holdings "
                          f"needs Python 3.11+ or the `tomli` package",
                          file=sys.stderr)
                    continue
                try:
                    with open(input_path, 'rb') as f:
                        dispatch(_holdings_toml_to_inventory(tomllib.load(f)))
                except (tomllib.TOMLDecodeError, OSError) as e:
                    print(f"Error loading {input_path}: {e}", file=sys.stderr)
                continue
            try:
                dispatch(load_report_json(input_path))
            except (json.JSONDecodeError, FileNotFoundError) as e:
                print(f"Error loading {input_path}: {e}", file=sys.stderr)

    if args.platform == "report":
        for line in render_report(agg, args.dust_threshold):
            print(line)
        return

    if args.platform == "holdings_toml":
        # Base-currency companion inventory (optional). Aggregated with the
        # same JOURNAL/DELETE map as the native holdings so symbol keys line
        # up; values are in the base currency for downstream gain/loss checks.
        base_agg: Dict[str, Dict[str, Any]] = {}
        for bp in args.base_gains:
            try:
                process_data_report(load_report_json(bp), args, base_agg,
                                    holdings_map, holdings_drops)
            except (json.JSONDecodeError, FileNotFoundError) as e:
                print(f"Error loading base-gains {bp}: {e}", file=sys.stderr)
        # Per-symbol acquisition/sell events (optional), in native currency,
        # from the pre-gains transaction file(s). Mapped/dropped the same way
        # as the holdings so events attach to the right (netted) symbol key.
        trades_by_symbol = (
            _load_trade_events(args.trades, holdings_map, holdings_drops)
            if args.trades else None)
        # Evidence-driven depot flips (see _apply_transfer_evidence):
        # applied to BOTH inventories so symbol keys stay aligned.
        _moves = _apply_transfer_evidence(
            agg, args.transfer_evidence, _tmap)
        _replay_moves_on_base(base_agg, _moves, _tmap)
        print("\n".join(render_holdings_toml(agg, args, base_agg,
                                             trades_by_symbol)))
        return

    if not results:
        return

    if args.platform == "tradingview":
        # Sort by the bare ticker, ignoring any EXCHANGE: prefix, so
        # ABC and TSX:ABC land next to each other — no manual re-sort
        # needed after importing the watchlist into TradingView.
        results.sort(key=lambda s: (s.rsplit(":", 1)[-1], s))
        print("\n".join(results))
    else:
        results.sort()
        # SeekingAlpha & FastGraph: comma-joined on one line
        print(", ".join(results))


if __name__ == "__main__":
    main()
