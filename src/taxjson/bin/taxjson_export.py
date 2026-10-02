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

from taxjson.lib.cli_diag import guard_main
from taxjson.lib.country import country_arg
from taxjson.lib.numeric import nonneg_float_arg
from taxjson.lib.report_model import load_report_json
from taxjson.lib import cli_diag
from taxjson.lib.json_input import InputFileError, read_json_doc

PROG = "taxjson-export"


def _die(msg: str) -> None:
    """A named input that cannot be used stops the tool: exit 2 means
    `taxjson run`'s run_to_file keeps the previous output (its .part
    rename never happens) and fails the stage, instead of publishing an
    empty or partial snapshot at exit 0 (audit R1-291, S030-19)."""
    cli_diag.error(PROG, msg)
    sys.exit(2)


def _read_json(path, **kw):
    kw.setdefault("list_key", None)
    try:
        return read_json_doc(path, **kw)
    except InputFileError as e:
        _die(str(e))


def _read_rows_doc(path, **kw):
    """`_read_json`, plus its `inventory` / `transactions` rows checked
    for type (json_input.check_row_types; a symbol must be text): a
    damaged gains file with "x" in a number field was a float()
    traceback in the aggregation (A2-0793, export part)."""
    from taxjson.lib.json_input import check_row_types
    doc = _read_json(path, **kw)
    for key in ("inventory", "transactions"):
        rows = doc.get(key)
        if rows is None:
            continue
        if not isinstance(rows, list) or any(not isinstance(r, dict)
                                             for r in rows):
            _die(f'{path}: "{key}" must be a list of JSON objects')
        try:
            check_row_types(rows, path, key)
        except InputFileError as e:
            _die(str(e))
        for i, r in enumerate(rows):
            sym = r.get("symbol")
            if sym is not None and not isinstance(sym, str):
                _die(f'{path}: "{key}" row {i}: symbol is {sym!r}, not '
                     f'a ticker — the file is damaged or hand-edited: '
                     f'fix it or re-run `taxjson run`')
    return doc
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
    is_cad = ext in ('TO', 'V', 'CN', 'NE')
    if is_cad and args.no_cad:
        return False
    if ext == 'US' and args.no_usd:
        return False
    # A currency split (--no-cad = the US file, --no-usd = the Canadian
    # file) holds only its own listings: an overseas listing (.L, .AX)
    # passed both filters and landed in BOTH splits (audit S030-06).
    if (args.no_cad or args.no_usd) and not is_cad and ext != 'US':
        return False
    return True


def _find_tv_map(inputs):
    """tv_exchange.map for a stand-alone --tradingview run: next to an
    input, then in the input's parent (the project root for
    work/<acct>_gains.json), then the current directory. The cwd used
    to be searched FIRST and the project root never, so `taxjson -C
    <proj> run` from another directory lost the project's prefixes, or
    picked up a different project's map (audit R1-246, R1-285)."""
    dirs = []
    for p in inputs:
        d = Path(p).resolve().parent
        dirs += [d, d.parent]
    dirs.append(Path.cwd())
    for d in dirs:
        f = d / "tv_exchange.map"
        if f.is_file():
            return f
    return None


def _is_dust(qty: float, total_cost: float, threshold: float) -> bool:
    """A sub-fractional residue: tiny quantity AND no material cost.
    Keying on quantity alone hid real crypto lots — 0.0009 BTC is about
    $120 (audit S030-00)."""
    if abs(qty) >= threshold:
        return False
    # Nothing held (a netted JOURNAL pair's leftover cost is FX salad,
    # not a position) — or a residue with no material cost.
    return abs(qty) < 1e-9 or abs(total_cost) < _DUST_COST


_DUST_COST = 1.0


def process_data_platform(data, args, seen, results, tv_map):
    """Existing behavior: emit platform-formatted ticker strings."""
    inventory = data.get("inventory", [])
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
        # An unreadable sidecar used to be skipped with a warning: the
        # evidenced flip was silently not applied and the snapshot split
        # one position across two listings at exit 0 (audit S030-19).
        doc = _read_json(p)
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
        # The contract size the parser declared (a futures option's CL
        # 1000 — audit S026-22); two inputs that disagree leave it
        # unknown.
        _m = item.get('multiplier')
        if _m:
            if bucket.get('multiplier') not in (None, float(_m)):
                bucket['multiplier_conflict'] = True
            bucket['multiplier'] = float(_m)
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


def contract_multiplier_of(sym: str, bucket: Dict[str, Any]):
    """The contract size of a held line: the size its rows declared
    (`multiplier` on the inventory — a futures option's CL 1000), else
    100 for an equity option; None for a futures option whose size was
    not declared (never the equity 100 — audit S026-22, S030-09) and
    for anything else."""
    if bucket.get('multiplier') and not bucket.get('multiplier_conflict'):
        return float(bucket['multiplier'])
    if is_option_ticker(sym) and not is_future_ticker(sym):
        return float(_OPTION_CONTRACT_MULTIPLIER)
    return None


def render_report(agg: Dict[str, Dict[str, Any]],
                   dust_threshold: float = 1e-9,
                   year: Any = None) -> List[str]:
    """Format the aggregated holdings as a fixed-width text table.

    Rows are sorted alphabetically by symbol. Cost/share for stocks is
    total_cost / qty. For options the convention is per-underlying-share
    (option chains and brokerage tickets quote in those units), so the
    per-contract figure is divided by the 100-share contract multiplier.
    Shorts naturally produce positive cost/share since total_cost flips
    sign with qty.

    Positions with |quantity| below `dust_threshold` and no material
    cost are dropped — a sub-fractional residue (e.g. -3.3e-05 shares
    left by a corp-action ratio) is float noise, not a real holding.

    A futures option's cost/share is divided by the contract size its
    rows declared (CL 1000 — audit S026-22); with none declared it stays
    per contract (never the equity 100 — S030-09).

    The inventory is the END of the data, not a tax-year end; the title
    says so (`year` names the report's tax year — audit S030-01).
    """
    rows = []
    for sym in sorted(agg.keys()):
        b = agg[sym]
        qty = b['qty']
        total = b['total_cost']
        if _is_dust(qty, total, dust_threshold):
            continue
        # A fully-netted JOURNAL pair leaves a qty-0 inventory row;
        # --dust-threshold 0 keeps it, so guard the division.
        cps = total / qty if abs(qty) > 1e-12 else 0.0
        _m = contract_multiplier_of(sym, b)
        if is_option_ticker(sym) and _m:
            cps /= _m
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
        (" HOLDINGS REPORT - open positions at the end of the data"
         + (f" (not {year}-12-31 positions; `taxjson list --date` gives "
            f"a date)" if year else "")
         + " - Sorted by: ticker"),
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


def _resolve_underlying(root_sym: str, held_stock) -> str:
    """The listing an option's ROOT names. Montreal/OCC roots drop the
    share class (RCI for RCI.B.TO), so `f"{root}.{ext}"` can name a
    listing that does not exist (audit S030-02). When exactly one held
    stock line matches the root (same rule the engine's assignment
    resolver uses), that line is the underlying; otherwise the root
    spelling stays."""
    from taxjson.lib.core import _root_matches_stock, _split_underlying
    if root_sym in held_stock:
        return root_sym
    root_base, ext = _split_underlying(root_sym)
    hits = []
    for s in held_stock:
        s_base, s_ext = _split_underlying(s)
        if s_ext == ext and _root_matches_stock(root_base, s_base):
            hits.append(s)
    return hits[0] if len(hits) == 1 else root_sym


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
    determines action (positive → BUY, negative → SELL). SPLIT rows scale
    the running balance and carry the events across a rename.

    With `current_position_only` (the default), only the events that make up
    the CURRENT position are kept: events are trimmed to those after the last
    time the running balance returned to zero. A ticker bought, fully sold,
    then re-bought reports only the latest round — the events that actually
    contribute to the live cost basis. This boundary equals the gains engine's
    `position_start_date`. Ordering uses settlement date (matching the engine)
    but each event keeps its trade `date` for charting."""
    mapping = mapping or {}
    drops = drops or set()
    from taxjson.lib.corporate_timeline import (normalize_symbol_new,
                                                split_seen)
    rows: List[Any] = []
    for p in paths:
        data = _read_rows_doc(p, list_key="transactions")
        for tx in data.get("transactions", []):
            if not isinstance(tx, dict):
                continue
            act = tx.get("action")
            sym = tx.get("symbol")
            # TRANSFER / OPENING_BALANCE rows move units in or out too:
            # left out, a transferred-in position's later round looked
            # flat and its events were trimmed away (re-audit A2-1215).
            if act not in ("BUYSELL", "ASSIGN", "SPLIT", "TRANSFER",
                           "OPENING_BALANCE") or not sym:
                continue
            rows.append(((tx.get("date_settle") or tx.get("date") or "",
                          tx.get("time") or "",
                          # a split applies before the day's trades
                          0 if act == "SPLIT" else 1), len(rows), tx))
    # Stable: tied trades keep the file's row order, as the engines
    # replay them (CA-DATE-14 / US-DATE-13; audit S030-05).
    rows.sort(key=lambda r: (r[0], r[1]))

    events: Dict[str, List[Dict[str, Any]]] = {}
    balance: Dict[str, float] = {}
    seen: set = set()
    for _key, _i, tx in rows:
        sym = mapping.get(tx["symbol"], tx["symbol"])
        if tx.get("action") == "SPLIT":
            # SPLIT rows scale the running balance and follow renames:
            # a split-blind walk kept closed rounds of a split symbol
            # and lost a renamed position's acquisitions (audit S030-04).
            new = normalize_symbol_new(tx["symbol"], tx.get("symbol_new"))
            if split_seen(seen, tx["symbol"], tx.get("date") or "",
                          tx.get("quantity"), new,
                          account=tx.get("account")) is not None:
                continue                       # duplicate split row
            try:
                ratio = float(tx.get("quantity") or 0) or 1.0
            except (TypeError, ValueError):
                ratio = 1.0
            if sym in balance:
                balance[sym] *= ratio
            new = mapping.get(new, new) if new else ""
            if new and new != sym:
                if sym in balance:
                    balance[new] = balance.get(new, 0.0) + balance.pop(sym)
                if sym in events:
                    events.setdefault(new, []).extend(events.pop(sym))
            continue
        try:
            qty = float(tx.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        if qty == 0 or sym in drops:
            continue
        if tx.get("action") in ("TRANSFER", "OPENING_BALANCE"):
            # Units moved in or out (no trade to chart): the running
            # balance only.
            balance[sym] = balance.get(sym, 0.0) + qty
            if current_position_only and abs(balance[sym]) < 1e-6:
                events[sym] = []
            continue
        events.setdefault(sym, []).append({
            "date": tx.get("date"),
            "action": "BUY" if qty > 0 else "SELL",
            "qty": abs(qty),
            "price": float(tx.get("price") or 0.0),
        })
        balance[sym] = balance.get(sym, 0.0) + qty
        if current_position_only and abs(balance[sym]) < 1e-6:
            # position flat -> the current round starts after this row
            events[sym] = []
    by_symbol = {s: evs for s, evs in events.items() if evs}
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
        if _is_dust(b['qty'], b['total_cost'], dust):
            continue
        rows.append((sym, b['qty'], b['total_cost'], b['currency'],
                     b.get('position_start_date'), b))

    lines = [
        "# taxjson holdings snapshot — generated by taxjson-export.",
        "# Regenerated on every run; do not hand-edit. Cost basis is a",
        "# snapshot as of meta.generated_at, not a live valuation.",
        "# cost_per_share = total_cost / quantity: per HOLDING UNIT, so",
        "# for an option it is per CONTRACT (the per-share price is",
        "# cost_per_share / contract_multiplier) — the same convention as",
        "# the broker holdings files this schema is shared with.",
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
    if base_agg:
        # base_total_cost comes from a separate per-account, per-listing
        # engine pass with no superficial-loss adjustment and no s.47
        # blend across taxable accounts: a denied loss's bump and the
        # blended cost are NOT in it, so it can call a position a gain
        # that is a tax loss (S037-24). Said in the file itself.
        # distributions.map adjustments are booked in the base books
        # only (amounts in the base currency), so neither cost here has
        # them (audit A2-0226): said too.
        # Each country's own words (re-audit A2-0745): a US project has
        # wash-sale adjustments and no s.47 blend.
        _c = getattr(args, 'country', None)
        _adj = {'canada': 'superficial-loss adjustments, the s.47 blend',
                'usa': 'wash-sale basis adjustments (§1091(d))',
                None: 'loss-denial adjustments, any cross-account '
                      'pooling'}[_c]
        from taxjson.lib.country import COST_TERM
        lines.append(f'base_cost_basis = "per-account, per-listing, before '
                     f'{_adj} and '
                     f'distributions.map adjustments (total_cost excludes '
                     f'those too; the filing {COST_TERM[_c]} is '
                     f'`taxjson list`)"')
    lines.append("")

    held_stock = [s for s in agg
                  if not _parse_option(s) and not is_future_ticker(s)]
    for sym, qty, total_cost, currency, position_start_date, b in rows:
        opt = _parse_option(sym)
        if opt:
            opt['underlying'] = _resolve_underlying(opt['underlying'],
                                                    held_stock)
        is_fut = is_future_ticker(sym)
        mixed = bool(b.get('mixed_currency'))
        cps = total_cost / qty if qty else 0.0
        lines.append("[[holding]]")
        lines.append(f"symbol = {_toml_str(sym)}")
        if args.account_name:
            lines.append(f"account = {_toml_str(args.account_name)}")
        lines.append(f"asset_type = "
                     f"{_toml_str('option' if opt else 'future' if is_fut else 'equity')}")
        if opt:
            lines.append(f"underlying = {_toml_str(opt['underlying'])}")
            lines.append(f"right = {_toml_str(opt['right'])}")
            lines.append(f"strike = {opt['strike']!r}")
            if opt['expiry']:
                # TOML local date — bare, unquoted.
                lines.append(f"expiry = {opt['expiry']}")
            _cm = contract_multiplier_of(sym, b)
            if _cm:
                # The declared size (a futures option: the future's —
                # CL 1000, ES 50 — audit S026-22), else the equity 100.
                lines.append(f"contract_multiplier = {_cm:g}")
                # Unit reminder next to the figure a reader is most
                # likely to misread (2026-09 audit S030-08): the
                # trades' prices are per share, cost_per_share is per
                # contract.
                lines.append("# cost_per_share below is per CONTRACT; "
                             "trades[].price is per "
                             + ("unit of the underlying" if is_fut
                                else "share"))
            else:
                # A futures option's multiplier is the future's (CL
                # 1000, micro contracts 0.1 ...), not the equity 100:
                # omitted rather than guessed when no row declared it
                # (audit S030-09).
                lines.append("# futures option: contract_multiplier "
                             "unknown here (not the equity 100)")
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


def _holdings_toml_to_inventory(doc: Dict[str, Any],
                                path: Any = "<toml>") -> Dict[str, Any]:
    """Adapt a taxjson holdings TOML document (`[[holding]]` tables) to
    the `inventory`-shaped dict the rest of this tool consumes: `quantity`
    maps to `qty`; symbol / total_cost / currency / position_start_date
    pass through. The TOML [meta] table and derived `cost_per_share`
    are ignored. TOML parses a bare YYYY-MM-DD as a date object, so the
    adapter stringifies it for downstream consistency."""
    holdings = doc.get("holding")
    if holdings is None:
        # An empty taxjson snapshot has no [[holding]] entries but has
        # its [meta] table; anything else (a [[holdings]] typo, another
        # tool's file) used to export NOTHING at exit 0 while `taxjson
        # sanity` refused the same file (audit S030-10).
        if "meta" in doc and "schema_version" in doc:
            holdings = []
        else:
            _die(f"{path}: no [[holding]] array (a taxjson holdings "
                 f"snapshot is expected); tables found: "
                 f"{', '.join(sorted(doc)) or 'none'}")
    if not isinstance(holdings, list) or any(
            not isinstance(h, dict) for h in holdings):
        _die(f"{path}: 'holding' must be an array of tables ([[holding]])")
    import math
    inventory = []
    for i, h in enumerate(holdings, start=1):
        # A quantity 'abc' was a float() traceback in --report and
        # --holdings-toml and exported silently in --seekingalpha /
        # --tradingview (A2-1441): refused here, naming the row.
        sym = h.get("symbol")
        if not isinstance(sym, str) or not sym.strip():
            _die(f"{path}: [[holding]] {i}: symbol is {sym!r}, not a "
                 f"ticker")
        for f in ("quantity", "total_cost"):
            v = h.get(f, 0)
            if isinstance(v, bool) or not isinstance(v, (int, float)) \
                    or not math.isfinite(v):
                _die(f"{path}: [[holding]] {i} ({sym}): {f} is {v!r}, "
                     f"not a number")
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


@guard_main("taxjson-export")
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
        "--tv-map", metavar="FILE", default=None,
        help="tv_exchange.map for --tradingview (`taxjson run` passes the "
             "project's). Default: the first tv_exchange.map found next "
             "to an input or in its parent directory (the project root "
             "for work/*_gains.json), then in the current directory.")
    parser.add_argument(
        "--account-name", default=None,
        help="Account name to stamp into the --holdings-toml output.",
    )
    parser.add_argument(
        "--country", type=country_arg, default=None,
        metavar="{canada,ca,usa,us}",
        help="Words the --holdings-toml cost note in the country's terms "
             "(neutral without it); `taxjson run` passes it.",
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
        # nonneg_float_arg: nan / inf hid every zero-cost holding and
        # a negative value was accepted (re-audit A2-1214/1216/1217).
        "--dust-threshold", type=nonneg_float_arg, default=1e-3,
        metavar="QTY",
        help="Drop --report / --holdings-toml positions whose absolute "
             "quantity is below this (default: 0.001) AND whose absolute "
             "total cost is below 1.00 — sub-fractional residue left by "
             "corp-action ratios and float arithmetic. A small quantity "
             "that cost something (0.0009 BTC) is a holding and is kept. "
             "Pass 0 to keep every position.",
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
        map_file = (Path(args.tv_map) if args.tv_map
                    else _find_tv_map(args.inputs))
        if map_file is not None:
            try:
                # utf-8-sig: a BOM became part of the first key and
                # its rule was dropped in silence (A2-0806 / A2-1410).
                text = map_file.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError) as e:
                _die(f"{map_file}: cannot read ({e})")
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    tv_map[parts[0]] = parts[1]
                else:
                    # Warn like the sibling map loaders do (audit
                    # S077-07: silently dropped).
                    print(f"warning: {map_file}: expected `SYMBOL "
                          f"EXCHANGE`, got {line!r} — line ignored",
                          file=sys.stderr)

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

    # Every named input must load: a missing, truncated or wrong-shape
    # file used to print one stderr line and carry on, so the tool wrote
    # a valid EMPTY (or partial) holdings snapshot at exit 0 and the run
    # reported every position as closed (audit R1-291, S030-19, S030-14).
    year = None
    if not args.inputs:
        try:
            data = load_report_json(None)
        except (ValueError, OSError) as e:
            _die(f"<stdin>: not valid JSON ({e})")
        if not isinstance(data, dict) or "inventory" not in data:
            _die("<stdin>: no 'inventory' section (a taxjson-gains JSON "
                 "is expected)")
        year = (data.get("summary") or {}).get("year")
        dispatch(data)
    else:
        for input_path in args.inputs:
            if str(input_path).lower().endswith(".toml"):
                if tomllib is None:
                    _die(f"{input_path}: reading TOML holdings needs "
                         f"Python 3.11+ or the `tomli` package")
                try:
                    with open(input_path, 'rb') as f:
                        doc = tomllib.load(f)
                except (tomllib.TOMLDecodeError, OSError,
                        UnicodeDecodeError) as e:
                    _die(f"{input_path}: not a readable TOML file ({e})")
                dispatch(_holdings_toml_to_inventory(doc, input_path))
                continue
            data = _read_rows_doc(input_path, require_key="inventory")
            if not isinstance(data["inventory"], list):
                _die(f"{input_path}: 'inventory' must be a list")
            year = year or (data.get("summary") or {}).get("year")
            dispatch(data)

    if args.platform == "report":
        for line in render_report(agg, args.dust_threshold, year=year):
            print(line)
        return

    if args.platform == "holdings_toml":
        # Base-currency companion inventory (optional). Aggregated with the
        # same JOURNAL/DELETE map as the native holdings so symbol keys line
        # up; values are in the base currency for downstream gain/loss checks.
        base_agg: Dict[str, Dict[str, Any]] = {}
        for bp in args.base_gains:
            process_data_report(_read_rows_doc(bp, require_key="inventory"),
                                args, base_agg, holdings_map, holdings_drops)
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
