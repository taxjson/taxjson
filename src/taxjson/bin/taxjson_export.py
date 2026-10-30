#!/usr/bin/env python3
"""
taxjson_export.py

Export holdings as a plain-text report (--report) or a TOML holdings
snapshot (--holdings-toml). The watchlist exports (Seeking Alpha,
FastGraph, TradingView) were removed.

Input may be taxjson_gains.py JSON (an `inventory` section) or a
taxjson holdings TOML snapshot (a `.toml` file of `[[holding]]`
tables, as written by `--holdings-toml`).
"""

from taxjson.lib.stage_msg import emit_line
import argparse
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
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib.tomlcompat import tomllib

from taxjson.lib.ticker_map import (
    is_option_ticker, is_future_ticker, get_base_ticker_info,
)
from taxjson.bin.taxjson_ticker_map import load_map_file


def _passes_filters(item: Dict[str, Any], args) -> bool:
    symbol = item.get("symbol", "")
    # A mixed-currency holding read back is one item per currency, the
    # quantity on the first: each is filtered by the holding's quantity,
    # or --long / --short dropped its other currencies' cost (TJ-0881).
    qty = item.get("holding_qty", item.get("qty", 0))

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
    from taxjson.lib.markets import canadian_suffixes
    is_cad = ext in canadian_suffixes()      # .VN too (lib/markets)
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


def _is_dust(qty: float, total_cost: float, threshold: float,
             unknown: bool = False) -> bool:
    """A sub-fractional residue: tiny quantity AND no material cost.
    Keying on quantity alone hid real crypto lots — 0.0009 BTC is about
    $120 (audit S030-00). A cost that is UNKNOWN (`unknown`: the books'
    total_cost is a 0 placeholder) is not known to be immaterial: such
    a holding is a residue only under a millionth of a unit, an
    exchange's rounding (backlog TJ-0876)."""
    if unknown:
        threshold = min(threshold, _DUST_UNKNOWN_QTY)
    if abs(qty) >= threshold:
        return False
    # Nothing held (a netted JOURNAL pair's leftover cost is FX salad,
    # not a position) — or a residue with no material cost.
    return abs(qty) < 1e-9 or unknown or abs(total_cost) < _DUST_COST


_DUST_COST = 1.0
# Under this many units a holding of unknown cost is a rounding residue
# (a sale of "all" leaving 1e-07 of a coin), as `taxjson run`'s crypto
# export takes it.
_DUST_UNKNOWN_QTY = 1e-6


def _evidence_rows(evidence_paths) -> list:
    """The TRANSFER rows of the transfer sidecars named: each file read
    once, and identical rows of two files kept the way one broker's
    overlapping exports are (taxjson_brokerage._dedup_evidence: per
    content id, the largest count any single file has). The same
    sidecar named twice (`--transfer-evidence` repeated, or two
    spellings of one path) used to double every move it evidences
    (audit 2026-10-24 B11)."""
    from taxjson.bin.taxjson_brokerage import _dedup_evidence
    seen: set = set()
    per_file: list = []
    for p in evidence_paths:
        try:
            key = str(Path(p).resolve())
        except OSError:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        # An unreadable sidecar used to be skipped with a warning: the
        # evidenced flip was silently not applied and the snapshot split
        # one position across two listings at exit 0 (audit S030-19).
        doc = _read_json(p)
        if (doc.get("metadata") or {}).get("kind") != "transfer_sidecar":
            continue
        per_file.append([t for t in doc.get("transactions") or []
                         if isinstance(t, dict)
                         and t.get("action") == "TRANSFER"
                         and t.get("symbol")])
    return _dedup_evidence(per_file)


def _evidenced_journals(rows, same_class) -> list:
    """The journals the sidecar rows evidence, as (date, out_symbol,
    in_symbol, quantity): lib/listing_journals.evidenced_journals (an
    out-leg and an in-leg of two listings of one identity class, netted
    per listing and day, within PAIR_DAYS business days of each other;
    audit 2026-10-24 B12), its note on legs too far apart to pair said
    on stderr."""
    from taxjson.lib.listing_journals import evidenced_journals
    return [(j["date"], j["out_symbol"], j["in_symbol"], j["quantity"])
            for j in evidenced_journals(
                rows, same_class,
                note=lambda s: emit_line(s, file=sys.stderr))]


def _move_units(agg: Dict[str, Dict[str, Any]], frm: str, to: str,
                units: float) -> None:
    """Move `units` of the position under `frm` to `to`, with the
    proportional cost: `units` carries the sign of the position (a
    long's units are positive, a short's negative) and is at most what
    `frm` holds. An emptied bucket is pruned."""
    b = agg[frm]
    frac = units / b["qty"]
    d = agg.setdefault(to, {
        "qty": 0.0, "total_cost": 0.0, "currency": "",
        "position_start_date": None})
    d["qty"] += units
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
    if b.get("mixed_currency"):
        # A bucket already in two currencies (its `currency` is only the
        # first): moved, it is still two, never one cost labelled the
        # first currency (backlog TJ-0882).
        d["mixed_currency"] = True
    if b.get("unknown_cost"):
        d["unknown_cost"] = True        # the moved part's share of it
    # (TJ-1110: the kind of cost)
    if b.get("cost_kinds"):
        d.setdefault("cost_kinds", set()).update(b["cost_kinds"])
    # (TJ-1109: the listing's currency beside a cost in another)
    if b.get("listing_currencies"):
        d.setdefault("listing_currencies", set()).update(
            b["listing_currencies"])
    # (TJ-1031: what the base figures cover moves with the units)
    for key in ("no_base_figure", "snapshot_base"):
        if b.get(key):
            d[key] = True
    psd = b.get("position_start_date")
    if psd and (d.get("position_start_date") is None
                or psd < d["position_start_date"]):
        d["position_start_date"] = psd
    b["total_cost"] -= b["total_cost"] * frac
    for cur in list((b.get("cost_by_currency") or {})):
        b["cost_by_currency"][cur] *= (1 - frac)
    b["qty"] -= units
    if abs(b.get("qty", 0.0)) <= 1e-9 \
            and abs(b.get("total_cost", 0.0)) <= 0.005:
        agg.pop(frm, None)


def _apply_transfer_evidence(agg: Dict[str, Dict[str, Any]],
                             evidence_paths, tmap, renames=None) -> list:
    """Evidence-driven depot flips: re-symbol holdings quantities that
    the TRANSFER sidecar PROVES were journaled between listings of the
    same security — and only those quantities.

    Whether 300 SAMPLI.US became SAMPLI.TO is a FACT recorded by the
    broker's InterDepot rows (or a .tt JOURNAL line's legs, lib/
    dated_events), not a timeless property of the symbol pair (2026-09
    design review) — a legacy ticker.map JOURNAL line is a TOBASE line
    and no longer re-symbols the holdings on its own. So: read the
    sidecars once each (_evidence_rows), pair their legs into journals
    within each map-declared identity class (GLOBAL/TOBASE/JOURNAL
    union) by date (_evidenced_journals), net the journals per symbol,
    and move matched negative→positive residuals between buckets,
    capped at what the bucket actually holds. Shares are never created
    or destroyed: a leg with no partner (broker migrations in/out —
    ATON) is ignored, and a flip that was flipped back nets to zero and
    moves nothing.

    A long's units leave the bucket of the listing they left. A SHORT
    position journaled shows as the opposite legs (+10 on the listing
    it left, -10 on the one it reached): when the listing the units
    "left" holds no long and the one they "reached" holds a short, the
    short moves (audit 2026-10-24 B13). Returns the moves applied as
    (from_symbol, to_symbol, units), the units signed like the
    position (negative: a short), for _replay_moves_on_base.

    `renames` (the map's GLOBAL renames, which a snapshot's symbols
    took when read back, TJ-0883): a leg in the broker's old spelling
    finds the holding under its new name; a leg that names a holding as
    it is stays, for a book that keeps that spelling (backlog TJ-1070).

    `taxjson run` passes no evidence: it books the journals into the
    native books on their dates instead (lib/listing_journals, backlog
    TJ-0361), so a later trade of the new listing finds its units."""
    applied: list = []
    if not evidence_paths or tmap is None:
        return applied
    rows = _evidence_rows(evidence_paths)
    if not rows:
        return applied
    if renames:
        from taxjson.bin.taxjson_ticker_map import map_symbol
        rows = [t if t["symbol"] in agg
                else {**t, "symbol": map_symbol(t["symbol"], renames)}
                for t in rows]
    from taxjson.lib.listing_journals import identity_find
    find = identity_find(tmap)
    journals = _evidenced_journals(
        rows, lambda a, b: find(a) == find(b))
    net: Dict[str, float] = {}
    for _day, out_sym, in_sym, q in journals:
        net[out_sym] = net.get(out_sym, 0.0) - q
        net[in_sym] = net.get(in_sym, 0.0) + q
    by_class: Dict[str, list] = {}
    for s, q in net.items():
        if abs(q) > 1e-9:
            by_class.setdefault(find(s), []).append((s, q))
    for members in by_class.values():
        srcs = [[s, -q] for s, q in members if q < 0]   # units left
        dsts = [[s, q] for s, q in members if q > 0]    # units reached
        if not srcs or not dsts:
            continue
        for src in srcs:
            for dst in dsts:
                if src[1] <= 1e-9:
                    break
                if dst[1] <= 1e-9:
                    continue
                # A long: capped at what the listing it left holds.
                b = agg.get(src[0])
                held = max(0.0, b.get("qty", 0.0)) if b else 0.0
                move = min(src[1], dst[1], held)
                if move > 1e-9:
                    _move_units(agg, src[0], dst[0], move)
                    src[1] -= move
                    dst[1] -= move
                    applied.append((src[0], dst[0], move))
                    emit_line(f"note: applied evidenced depot flip: "
                              f"{move:g} {src[0]} -> {dst[0]} (transfer "
                              f"sidecar).", file=sys.stderr)
                if src[1] <= 1e-9 or dst[1] <= 1e-9:
                    continue
                # A short journaled: held under the listing the legs
                # say the units reached; it moves to the other one.
                b = agg.get(dst[0])
                held = max(0.0, -b.get("qty", 0.0)) if b else 0.0
                move = min(src[1], dst[1], held)
                if move > 1e-9:
                    _move_units(agg, dst[0], src[0], -move)
                    src[1] -= move
                    dst[1] -= move
                    applied.append((dst[0], src[0], -move))
                    emit_line(f"note: applied evidenced depot flip: "
                              f"{-move:g} {dst[0]} -> {src[0]} (a short "
                              f"position; transfer sidecar).",
                              file=sys.stderr)
    return applied


def _replay_moves_on_base(base_agg: Dict[str, Dict[str, Any]],
                          moves, tmap) -> None:
    """The base inventory must mirror the native pass's applied
    moves exactly. It is keyed like the native one — GLOBAL renames
    applied upstream, cross-listings kept per-listing (a legacy JOURNAL
    line folds nothing any more) — so every move REPLAYS, moving the
    base cost with the shares. Deriving moves independently, or folding
    through the to-base renames, both diverge (round-five finding 5 /
    round-six finding 1). A move's units are signed like the position
    (negative: a short moved — audit 2026-10-24 B13)."""
    if not moves or tmap is None:
        return
    # The base inventory comes from the RAW-base pipeline, which applies
    # GLOBAL renames upstream and keeps cross-listings per-listing (no
    # TOBASE consolidation): folding through merge_renames(to_base=True)
    # here made every TOBASE-pair move a "same key" no-op and the
    # flipped shares' base cost silently vanished (round-six adversarial
    # audit, finding 1 — a HIGH regression over the round-five fix).
    for src, dst, units in moves:
        if src == dst:
            continue
        b = base_agg.get(src)
        if not b:
            continue
        held = b.get("qty", 0.0)
        if units > 0:
            if held <= 1e-9:
                continue
            move = min(units, held)
        else:
            if held >= -1e-9:
                continue
            move = max(units, held)
        _move_units(base_agg, src, dst, move)


def process_data_report(data, args, agg: Dict[str, Dict[str, Any]],
                         mapping: Dict[str, str] = None, drops=None):
    """Report mode: aggregate inventory entries per symbol across files.
    Sums qty and total_cost; tracks currency (warns on mismatch).

    `mapping` (from --map) is applied to each symbol before bucketing, so
    e.g. a Norbert's Gambit SAMPLF.US leg folds into SAMPLF.TO and the
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
        # unknown (said once, the sizes kept for the snapshot's note:
        # audit 2026-10-24 B21), as does a snapshot read back that says
        # so (`contract_multiplier_unknown`).
        _m = item.get('multiplier')
        if _m:
            _mf = float(_m)
            _sizes = bucket.setdefault('multipliers_seen', [])
            if _mf not in _sizes:
                _sizes.append(_mf)
            if bucket.get('multiplier') not in (None, _mf):
                if not bucket.get('multiplier_conflict'):
                    emit_line(
                        f"warning: {sym}: its inputs declare different "
                        f"contract sizes ({', '.join(f'{x:g}' for x in sorted(_sizes))}): "
                        f"contract_multiplier is left unknown, and its "
                        f"cost figures stay per contract (not per share)",
                        file=sys.stderr)
                bucket['multiplier_conflict'] = True
            bucket['multiplier'] = _mf
        if item.get('multiplier_conflict'):
            bucket['multiplier_conflict'] = True
        # Units whose cost the books do not have (the engines'
        # `unknown_cost`: an opening at an unknown cost, or units moved
        # in from another of your accounts in this account's own books):
        # the bucket's total_cost leaves them out (backlog TJ-0598).
        if item.get('unknown_cost'):
            bucket['unknown_cost'] = True
        # What the base-currency figures cover (TJ-1031): an input of
        # the holding with none of its own (`no_base_figure`), and a
        # base bucket holding a snapshot's own figure (`snapshot_base`).
        for key in ('no_base_figure', 'snapshot_base'):
            if item.get(key):
                bucket[key] = True
        # A holding a snapshot read back declares a coin (asset_type =
        # "crypto"): written as one again (backlog TJ-0875).
        if item.get('asset_type') == 'crypto':
            bucket['crypto'] = True
        # Per-currency sub-buckets: JOURNAL folds (SAMPLF.US -> SAMPLF.TO)
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
        # The kind of cost a snapshot read back states (TJ-1110): one
        # kind for the whole bucket, or none written.
        bucket.setdefault('cost_kinds', set()).add(
            item.get('cost_kind') or '')
        cur = item.get('currency') or ''
        # A cost in a currency other than the listing's (TJ-1109):
        # `currency` is the cost's, as the sums here need it.
        _listing = item.get('listing_currency') or cur
        if _listing:
            bucket.setdefault('listing_currencies', set()).add(_listing)
        if cur and bucket['currency'] and cur != bucket['currency']:
            bucket['mixed_currency'] = True
            emit_line(
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
    not declared (never the equity 100 — audit S026-22, S030-09), for
    a line whose inputs declare different sizes (`multiplier_conflict`:
    unknown, never the equity 100 either — audit 2026-10-24 B21) and
    for anything else."""
    if bucket.get('multiplier_conflict'):
        return None
    if bucket.get('multiplier'):
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
        if _is_dust(qty, total, dust_threshold,
                    bool(b.get('unknown_cost'))):
            continue
        # A fully-netted JOURNAL pair leaves a qty-0 inventory row;
        # --dust-threshold 0 keeps it, so guard the division.
        cps = total / qty if abs(qty) > 1e-12 else 0.0
        _m = contract_multiplier_of(sym, b)
        if is_option_ticker(sym) and _m:
            cps /= _m
        # A cost the books do not have (`unknown_cost`: units moved in
        # from another of your accounts, an opening at an unknown cost)
        # reads "unknown", never a cost of 0 (backlog TJ-0279).
        rows.append((sym, qty,
                     None if b.get('unknown_cost') else f"{cps:.4f}",
                     None if b.get('unknown_cost') else f"{total:.2f}",
                     "MIXED" if b.get('mixed_currency')
                     else b['currency']))

    if not rows:
        return []
    unknown = any(r[3] is None for r in rows)
    rows = [(s, q, c or "unknown", tc or "unknown", cur)
            for s, q, c, tc, cur in rows]

    # Determine column widths from data.
    has_currency = any(r[4] for r in rows)
    sym_w = max(6, max(len(r[0]) for r in rows))
    qty_w = max(10, max(len(_fmt_qty(r[1])) for r in rows))
    cps_w = max(10, max(len(r[2]) for r in rows))
    tot_w = max(12, max(len(r[3]) for r in rows))
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
            f"{cps:>{cps_w}}",
            f"{total:>{tot_w}}",
        ]
        if has_currency:
            line_parts.append(f"{cur:<{cur_w}}")
        out.append("  ".join(line_parts))
    if unknown:
        out.append("unknown: some of the units have no cost in these books "
                   "(moved in from another of your accounts, or an opening "
                   "at an unknown cost); the cost you file: `taxjson list`.")
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
    share class (SAMPLD for SAMPLD.B.TO), so `f"{root}.{ext}"` can name a
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


_TOML_ESCAPES = {'\\': '\\\\', '"': '\\"', '\n': '\\n', '\r': '\\r',
                 '\t': '\\t', '\b': '\\b', '\f': '\\f'}


def _toml_str(value: Any) -> str:
    """Render a value as a TOML basic (quoted) string. Every control
    character is escaped (named, else \\uXXXX): TOML refuses a raw one,
    so a symbol carrying an ESC from broker data would otherwise write
    a holdings file nothing can read back."""
    return '"' + ''.join(
        _TOML_ESCAPES.get(c) or (f'\\u{ord(c):04X}'
                                 if ord(c) < 0x20 or ord(c) == 0x7f else c)
        for c in str(value)) + '"'


def _engine_clock(tx: Dict[str, Any]) -> str:
    """The row's clock as the engine compares it: its loader writes
    '9:30:00' or '9:30' as HH:MM:SS (core.py, audit S071-13) and a row
    with none takes the default. Text, whatever a hand-made book holds
    (the loader refuses that; backlog TJ-1233, TJ-1234)."""
    raw = tx.get("time", "09:30:00")
    text = "09:30:00" if raw is None else str(raw).strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    return (f"{int(m.group(1)):02d}:{m.group(2)}:{m.group(3) or '00'}"
            if m else text)


def _replay_key(tx: Dict[str, Any], profile: str) -> Tuple:
    """The order the country's engine replays the book row `tx` in
    (lib/corporate_timeline event_sort_key, `profile` from
    lib/country engine_sort_profile): Canada's settlement-date ladder
    takes a split ahead of every execution of its day whatever its
    clock time (backlog TJ-0884) and rows settling on one day in trade
    order; the US engine sorts by trade date and clock (backlog
    TJ-0886). A row with no time takes the engine's default."""
    from types import SimpleNamespace
    from taxjson.lib.corporate_timeline import event_sort_key
    try:
        q = float(tx.get("quantity") or 0.0)
    except (TypeError, ValueError):
        q = 0.0
    view = SimpleNamespace(
        action=str(tx.get("action") or ""),
        symbol=str(tx.get("symbol") or ""),
        date=str(tx.get("date") or ""),
        date_settle=str(tx.get("date_settle") or ""),
        time=_engine_clock(tx) if tx.get("time") else "09:30:00",
        quantity=q, exercise_of=str(tx.get("exercise_of") or ""))
    return event_sort_key(view, profile=profile)


def _lag_split_factors(rows) -> Dict[int, float]:
    """{row number: split ratio} for the trades of `rows` ((key, number,
    row) tuples) the Canadian engine re-denominates: a split inside the
    settle lag, after the execution and before the settle date, reaches
    the pool ahead of the trade, so the trade is booked in post-split
    units (core.py compute_gains, corporate_timeline
    split_in_settle_lag). A rename-split there the engine refuses, so it
    scales nothing here. One split per split_seen, as the replay."""
    from taxjson.lib.corporate_timeline import (normalize_symbol_new,
                                                split_in_settle_lag,
                                                split_seen)
    splits: List[Dict[str, Any]] = []
    seen: set = set()
    for _key, _i, tx in rows:
        if tx.get("action") != "SPLIT":
            continue
        new = normalize_symbol_new(tx["symbol"], tx.get("symbol_new"))
        if split_seen(seen, tx["symbol"], tx.get("date") or "",
                      tx.get("quantity"), new) is None \
                and tx.get("date") and not new:
            splits.append(tx)
    out: Dict[int, float] = {}
    if not splits:
        return out
    for _key, i, tx in rows:
        if tx.get("action") not in ("BUYSELL", "ASSIGN"):
            continue
        f = 1.0
        for sp in splits:
            if sp["symbol"] != tx["symbol"] or not split_in_settle_lag(
                    str(tx.get("date") or ""), _engine_clock(tx),
                    str(tx.get("date_settle") or ""),
                    str(sp.get("date_settle") or sp.get("date")),
                    _engine_clock(sp)):
                continue
            try:
                f *= float(sp.get("quantity") or 0) or 1.0
            except (TypeError, ValueError):
                pass
        if abs(f - 1.0) > 1e-12:
            out[i] = f
    return out


def _load_trade_events(paths, mapping=None, drops=None,
                       current_position_only=True,
                       country=None) -> Dict[str, List[Dict[str, Any]]]:
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
    `position_start_date`: the last time the balance left zero or changed
    sign. A trade that crosses zero (10 held, 20 sold) opens the new
    position with its leftover, and is kept as that part alone (a SELL of
    10 at its price); the old position's events go (audit 2026-10-24 B22).
    Rows are replayed in the order `country`'s engine takes them
    (_replay_key; Canada's without a country) but each event keeps its
    trade `date` for charting; a rename merges the two symbols' events
    in that order (audit 2026-10-24 B44). An opening balance (a BUYSELL
    of type core.OPENING_TYPE) sets the balance and is no purchase: it
    is not charted (backlog TJ-0887). Canada's engine books a trade
    whose settle lag holds a split in post-split units: so does the
    balance here, the event keeping the row's own quantity and price
    (_lag_split_factors; backlog TJ-1072)."""
    mapping = mapping or {}
    drops = drops or set()
    from taxjson.lib.core import is_opening_row
    from taxjson.lib.corporate_timeline import (account_tie,
                                                normalize_symbol_new,
                                                split_seen)
    from taxjson.lib.country import CANADA, engine_sort_profile
    profile = engine_sort_profile(country or CANADA)
    rows: List[Any] = []
    for p in paths:
        # require_key: a book without the list ({"Transactions": ...})
        # read as no trades (issue #6).
        data = _read_rows_doc(p, list_key="transactions",
                              require_key="transactions")
        for tx in data["transactions"]:
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
            rows.append((_replay_key(tx, profile), len(rows), tx))
    # Stable: tied trades keep the file's row order, as the engines
    # replay them (CA-DATE-14 / US-DATE-13; audit S030-05), rows of
    # several accounts at one moment in the accounts' name order
    # (account_tie, as the engines take them).
    rows.sort(key=lambda r: (r[0], account_tie(r[2]), r[1]))
    lag = (_lag_split_factors(rows)
           if profile == engine_sort_profile(CANADA) else {})

    # Per symbol, (replay order, event): the order merges a renamed
    # symbol's events into the new symbol's in sequence (B44).
    events: Dict[str, List[Any]] = {}
    balance: Dict[str, float] = {}
    seen: set = set()
    for order, (_key, _i, tx) in enumerate(rows):
        sym = mapping.get(tx["symbol"], tx["symbol"])
        factor = lag.get(_i, 1.0)
        if tx.get("action") == "SPLIT":
            # SPLIT rows scale the running balance and follow renames:
            # a split-blind walk kept closed rounds of a split symbol
            # and lost a renamed position's acquisitions (audit S030-04).
            # One event per split, whichever account's file carries
            # it: the balance below sums every account's shares, so an
            # account-keyed event scaled the sum once per account
            # (re-audit A2-1590).
            new = normalize_symbol_new(tx["symbol"], tx.get("symbol_new"))
            if split_seen(seen, tx["symbol"], tx.get("date") or "",
                          tx.get("quantity"), new) is not None:
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
                    # In replay order, not the old symbol's events
                    # behind the new one's (B44).
                    merged = events.get(new, []) + events.pop(sym)
                    merged.sort(key=lambda e: e[0])
                    events[new] = merged
            continue
        try:
            qty = float(tx.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        if qty == 0 or sym in drops:
            continue
        before = balance.get(sym, 0.0)
        after = before + qty * factor
        balance[sym] = after
        flat = abs(after) < 1e-6
        # Long to short (or back) in one row: a new position (B22).
        crossed = (not flat and abs(before) >= 1e-6
                   and (before > 0) != (after > 0))
        if (tx.get("action") in ("TRANSFER", "OPENING_BALANCE")
                or is_opening_row(tx)):
            # Units moved in or out, or held at an opening snapshot (no
            # trade to chart: an opening is no purchase on its snapshot
            # day, TJ-0887): the running balance only.
            if current_position_only and (flat or crossed):
                events[sym] = []
            continue
        ev = {
            "date": tx.get("date"),
            "action": "BUY" if qty > 0 else "SELL",
            "qty": abs(qty),
            "price": float(tx.get("price") or 0.0),
        }
        if current_position_only and flat:
            # position flat -> the current round starts after this row
            events[sym] = []
        elif current_position_only and crossed:
            # The part of this row that opens the new position (in the
            # row's own units, as its price is).
            ev["qty"] = abs(after) / factor
            events[sym] = [(order, ev)]
        else:
            events.setdefault(sym, []).append((order, ev))
    by_symbol = {s: [e for _o, e in evs] for s, evs in events.items()
                 if evs}
    return by_symbol


def _venue_suffixes(specs, books=()) -> Dict[str, str]:
    """{coin: venue suffix} for a crypto account of several exchanges
    (--venue-book BROKER=FILE, each exchange's parsed rows and transfer
    rows): a coin the rows of one exchange alone hold carries that
    exchange's suffix (data/markets.toml [crypto_venues]); one several
    exchanges hold, or one an exchange with no suffix holds, none — never
    another exchange's (backlog TJ-0603). A coin renamed in the
    account's books (`books`: a dated RENAME's SPLIT row) keeps its
    venues under the new name."""
    from taxjson.lib.corporate_timeline import normalize_symbol_new
    from taxjson.lib.markets import crypto_venue_suffix
    seen: Dict[str, set] = {}
    for spec in specs:
        broker, sep, path = str(spec).partition("=")
        if not sep or not broker or not path:
            _die(f"--venue-book {spec!r}: expected BROKER=FILE")
        sfx = (crypto_venue_suffix(broker) or "").strip(". ")
        data = _read_rows_doc(path, list_key="transactions",
                              require_key="transactions")
        for tx in data["transactions"]:
            if isinstance(tx, dict) and tx.get("symbol"):
                seen.setdefault(str(tx["symbol"]), set()).add(sfx)
    renames = []
    for p in books:
        data = _read_rows_doc(p, list_key="transactions",
                              require_key="transactions")
        for tx in data["transactions"]:
            if (isinstance(tx, dict) and tx.get("action") == "SPLIT"
                    and tx.get("symbol")):
                new = normalize_symbol_new(tx["symbol"],
                                           tx.get("symbol_new"))
                if new and new != tx["symbol"]:
                    renames.append((str(tx.get("date") or ""),
                                    str(tx["symbol"]), new))
    for _d, old, new in sorted(set(renames)):
        if old in seen:
            seen.setdefault(new, set()).update(seen[old])
    return {c: next(iter(v)) for c, v in seen.items()
            if len(v) == 1 and next(iter(v))}


def _own_move_arrivals(paths, mapping=None
                       ) -> Dict[str, List[Tuple[str, float, str]]]:
    """{symbol: [(date, units, from account)]}: the units that arrived
    in the book(s) by a move of your own from another of your taxable
    accounts (the run's lot-move in-legs, lib/core LOT_MOVE_TYPE), whose
    cost one account's own books do not have (backlog TJ-0598)."""
    from taxjson.lib.core import LOT_MOVE_TYPE
    mapping = mapping or {}
    out: Dict[str, List[Tuple[str, float, str]]] = {}
    for p in paths:
        data = _read_rows_doc(p, list_key="transactions",
                              require_key="transactions")
        for tx in data["transactions"]:
            if not (isinstance(tx, dict) and tx.get("action") == "TRANSFER"
                    and tx.get("type") == LOT_MOVE_TYPE):
                continue
            try:
                q = float(tx.get("quantity") or 0.0)
            except (TypeError, ValueError):
                continue
            if q <= 0 or not tx.get("symbol"):
                continue
            m = _OWN_MOVE_FROM.match(str(tx.get("description") or ""))
            sym = mapping.get(tx["symbol"], tx["symbol"])
            out.setdefault(sym, []).append(
                (str(tx.get("date") or ""), q, m.group(1) if m else ""))
    return out


# "own-account move #N: FROM -> TO (...)": a lot-move leg's description
# (taxjson run's _move_legs).
_OWN_MOVE_FROM = re.compile(r"own-account move #\d+: (\S+) -> \S+")


def _own_move_currency(paths, mapping=None) -> Dict[str, str]:
    """{symbol: currency} of the units moved in from another of your
    accounts (the lot-move in-legs' own currency: their transfer row's),
    where all of a symbol's legs say one."""
    from taxjson.lib.core import LOT_MOVE_TYPE
    mapping = mapping or {}
    seen: Dict[str, set] = {}
    for p in paths:
        data = _read_rows_doc(p, list_key="transactions",
                              require_key="transactions")
        for tx in data["transactions"]:
            if not (isinstance(tx, dict) and tx.get("action") == "TRANSFER"
                    and tx.get("type") == LOT_MOVE_TYPE
                    and tx.get("symbol")):
                continue
            try:
                if float(tx.get("quantity") or 0.0) <= 0:
                    continue
            except (TypeError, ValueError):
                continue
            sym = mapping.get(tx["symbol"], tx["symbol"])
            seen.setdefault(sym, set()).add(
                str(tx.get("currency") or "").upper())
    return {s: next(iter(c)) for s, c in seen.items()
            if len(c) == 1 and next(iter(c))}


def _blended_fill(agg: Dict[str, Dict[str, Any]], blended_paths,
                  account: Optional[str], to_canonical,
                  base_currency: Optional[str], dust: float,
                  leg_currency: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """{holding symbol: {total_cost, currency, native}}: the cost after
    the passes across accounts (`taxjson run`'s <account>_gains_wash.json,
    the blended pass split per account: what `taxjson list` shows) of
    each holding whose own books have none (`unknown_cost`: units moved
    in from another of your accounts) — the account's share of the ACB
    pool (Canada, s.47) or its lots with the basis the move carried (US)
    — when the figure is that holding's alone: the one listing of the
    account that maps to it, the same quantity, a cost the pass has
    (never a pool whose cost is unknown there too), in the base
    currency. `native`: the holding's own currency (its books', else
    the move's legs'), so the figure is its total_cost too when the two
    are one (backlog TJ-0598)."""
    rows: Dict[str, List[Dict[str, Any]]] = {}
    for p in blended_paths or ():
        data = _read_rows_doc(p, require_key="inventory")
        for r in data["inventory"]:
            if not isinstance(r, dict):
                continue
            if account and r.get("account") not in (None, "", account):
                continue
            rows.setdefault(str(r.get("symbol") or ""), []).append(r)
    held = {s: b for s, b in agg.items()
            if not _is_dust(b['qty'], b['total_cost'], dust,
                            bool(b.get('unknown_cost')))}
    by_canon: Dict[str, List[str]] = {}
    for s in held:
        by_canon.setdefault(to_canonical(s), []).append(s)
    out: Dict[str, Dict[str, Any]] = {}
    for s, b in held.items():
        c = to_canonical(s)
        # (A move of your own brings units held long.)
        if (not b.get('unknown_cost') or b['qty'] <= 0
                or len(by_canon[c]) != 1):
            continue
        cand = rows.get(c) or []
        curs = {str(r.get('currency') or '').upper() for r in cand}
        if not cand or len(curs) != 1 or any(r.get('unknown_cost')
                                             for r in cand):
            continue
        try:
            q = sum(float(r.get('qty') or 0.0) for r in cand)
            cost = sum(float(r.get('total_cost') or 0.0) for r in cand)
        except (TypeError, ValueError):
            continue
        if abs(q - b['qty']) > max(1e-6, 1e-9 * abs(q)):
            continue
        cur = next(iter(curs))
        if not cur or (base_currency and cur != base_currency.upper()):
            continue
        out[s] = {"total_cost": cost, "currency": cur,
                  "native": (str(b.get('currency') or '').upper()
                             or leg_currency.get(s, ''))}
    return out


def _blended_note(sym: str, b: Dict[str, Any], country,
                  arrivals: Dict[str, List[Tuple[str, float, str]]],
                  fill: Dict[str, Any]) -> List[str]:
    """The comment under a holding whose cost is the figure after the
    passes across accounts (`cost_source = "blended"`, backlog TJ-0598):
    the moves that brought units with no cost in this account's own
    books, and what the figure is."""
    import textwrap
    psd = str(b.get("position_start_date") or "")
    moved = [a for a in arrivals.get(sym) or [] if a[0] >= psd]
    what = ("; ".join(
        f"{q:g} moved in from {frm or 'another of your accounts'} (your "
        f"own account) on {d}" for d, q, frm in moved)
        or "some of these units")
    from taxjson.lib.country import basis_pooled_across_accounts
    fig = ("this account's share of the ACB pool across your taxable "
           "accounts (s.47)" if country and basis_pooled_across_accounts(
               country) else
           "the basis of this account's lots (the moved lots' basis "
           "carried with them)" if country else "this account's cost")
    both = fill["native"] == fill["currency"]
    native = fill["native"] or "the listing's currency"
    text = (f"cost_source = blended: {what}, a cost this account's own "
            f"books do not have. "
            + ("total_cost and base_total_cost are" if both else
               f"total_cost and cost_per_share ({native}) are left out "
               f"(cost_unknown); base_total_cost is")
            + f" {fig} after the passes across accounts, as `taxjson "
              f"list` gives it, not this account's own books.")
    return ["# " + ln for ln in textwrap.wrap(text, 70)]


def _cost_unknown_note(sym: str, b: Dict[str, Any], country,
                       arrivals: Dict[str, List[Tuple[str, float, str]]]
                       ) -> List[str]:
    """The comment under a holding whose cost is unknown in the account's
    own books (`cost_unknown = true`, backlog TJ-0598): the moves of your
    own that brought units of the current position, and where the cost
    is."""
    import textwrap
    psd = str(b.get("position_start_date") or "")
    moved = [a for a in arrivals.get(sym) or [] if a[0] >= psd]
    if b.get("blended_left_out"):
        # (TJ-1071)
        text = ("cost_unknown: an input's cost for these units is the "
                "figure after the passes across accounts (cost_source = "
                "\"blended\"), and another input holds them too: one sum "
                "of the two is no cost of either kind, so total_cost and "
                "cost_per_share are left out.")
    elif moved:
        text = "; ".join(
            f"{q:g} moved in from {frm or 'another of your accounts'} "
            f"(your own account) on {d}" for d, q, frm in moved)
        text = (f"cost_unknown: {text}: their cost is not in this "
                f"account's own books, so total_cost and cost_per_share "
                f"are left out.")
        if country:
            from taxjson.lib.country import basis_pooled_across_accounts
            text += (" The ACB is one pool across your taxable accounts: "
                     "`taxjson list` gives this account's share."
                     if basis_pooled_across_accounts(country) else
                     " The basis moved with the lots: `taxjson list` "
                     "gives it.")
    else:
        text = ("cost_unknown: some of these units have no cost in this "
                "account's own books (an opening at an unknown cost), so "
                "total_cost and cost_per_share are left out.")
    return ["# " + ln for ln in textwrap.wrap(text, 70)]


def render_holdings_toml(agg: Dict[str, Dict[str, Any]], args,
                         base_agg: Dict[str, Dict[str, Any]] = None,
                         trades_by_symbol: Dict[str, List[Dict[str, Any]]] = None,
                         arrivals: Dict[str, List[Tuple[str, float, str]]] = None,
                         blended: Dict[str, Dict[str, Any]] = None,
                         move_currency: Dict[str, str] = None
                         ) -> List[str]:
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
    gain/loss against a base-currency price without re-running FX.

    `blended` (_blended_fill): a holding whose own books have no cost
    for units moved in from another of your accounts takes the figure
    after the passes across accounts, `cost_source = "blended"`
    (backlog TJ-0598). `move_currency` (_own_move_currency): the
    currency of units moved in, for a holding whose books have none."""
    base_agg = base_agg or {}
    blended = blended or {}
    move_currency = move_currency or {}
    dust = getattr(args, 'dust_threshold', 1e-9)
    # A crypto account's books (--crypto): every holding is a coin, its
    # quantity and per-unit cost kept to significant digits — a sum of
    # rewards carries float noise (0.05000000000000002), and six
    # decimals of a low-priced token's cost (0.000012345) lose a tenth.
    crypto = bool(getattr(args, 'crypto', False))

    # The exchange's venue suffix on a coin (BTC.KR for Kraken), the
    # form its own holdings snapshot uses: a bare coin name read as a
    # share of that ticker (backlog TJ-0418). An account of several
    # exchanges: each coin its own venue's (`coin_suffix`, from
    # --venue-book), bare when several hold it (backlog TJ-0603).
    _sfx = str(getattr(args, 'symbol_suffix', None) or "").strip(". ")
    _per_coin = getattr(args, 'coin_suffix', None)

    def _shown(sym: str) -> str:
        if not crypto:
            return sym
        s = _per_coin.get(sym, "") if _per_coin is not None else _sfx
        return f"{sym}.{s.upper()}" if s else sym

    def _unit(v: float, places: int, coin: bool = crypto) -> float:
        return float(f"{v:.10g}") if coin else round(v, places)
    rows = []
    dropped = []
    dropped_unknown = []
    for sym in sorted(agg):
        b = agg[sym]
        # (a cost unknown here or in the base books: no figure says it
        # is immaterial — TJ-0876)
        _unk = bool(b.get('unknown_cost')
                    or (base_agg.get(sym) or {}).get('unknown_cost'))
        if _is_dust(b['qty'], b['total_cost'], dust, _unk):
            if _unk and abs(b['qty']) >= 1e-9:
                # A residue of unknown cost: said in the file too.
                dropped_unknown.append((_shown(sym), b['qty']))
            elif crypto and abs(b['qty']) >= 1e-9:
                # A coin's rounding residue (a sale of "all" leaving
                # 1e-07): left out, and said in the file (TJ-0418).
                dropped.append((_shown(sym), b['qty']))
            continue
        rows.append((sym, b['qty'], b['total_cost'], b['currency'],
                     b.get('position_start_date'), b))

    # The day of the snapshot(s) read back (TJ-0874): their [meta]
    # as_of, kept apart from the day this file is generated.
    as_of = getattr(args, 'snapshot_as_of', None)
    lines = [
        "# taxjson holdings snapshot — generated by taxjson-export.",
        "# Regenerated on every run; do not hand-edit. Cost basis is a",
        f"# snapshot as of meta.{'as_of' if as_of else 'generated_at'}, "
        f"not a live valuation.",
        "# cost_per_share = total_cost / quantity: per HOLDING UNIT, so",
        "# for an option it is per CONTRACT (the per-share price is",
        "# cost_per_share / contract_multiplier) — the same convention as",
        "# the broker holdings files this schema is shared with.",
        'schema_version = "1.2"',
        "",
        "[meta]",
        f"generated_at = {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
    ]
    if as_of:
        lines.append(f"as_of = {as_of}")        # a bare TOML date
    # The account the snapshots read back name, when they all name one
    # (TJ-1111): `holdings_dir` claims a file by these keys first.
    _meta_acct = args.account_name or getattr(args, 'meta_account', None)
    if _meta_acct:
        lines.append(f"account = {_toml_str(_meta_acct)}")
    if getattr(args, 'meta_broker_account', None):
        lines.append(f"broker_account = "
                     f"{_toml_str(args.meta_broker_account)}")
    src = ", ".join(Path(p).name for p in args.inputs) or "<stdin>"
    lines.append(f"source = {_toml_str(src)}")
    lines.append(f"holdings_count = {len(rows)}")
    if dropped:
        lines.append(f"# left out as dust (under {dust:g} units and a cost "
                     f"under {_DUST_COST:g}): "
                     + ", ".join(f"{s} {q:.3g}" for s, q in dropped))
    if dropped_unknown:
        lines.append(f"# left out as dust (under {min(dust, _DUST_UNKNOWN_QTY):g} "
                     f"units, cost unknown): "
                     + ", ".join(f"{s} {q:.3g}" for s, q in dropped_unknown))
    if base_agg:
        # base_total_cost comes from a separate per-account, per-listing
        # engine pass with no superficial-loss adjustment and no s.47
        # blend across taxable accounts: a denied loss's bump and the
        # blended cost are NOT in it, so it can call a position a gain
        # that is a tax loss (S037-24). Said in the file itself.
        # [[distributions]] adjustments are booked in the base books
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
        if crypto:
            # A crypto account's books themselves (the run's prices
            # and rates): both costs are that one figure.
            _cross = (' (a superficial loss with another account, the '
                      's.47 blend)' if _c == 'canada' else '')
            lines.append(f'base_cost_basis = "this crypto account\'s own '
                         f'books, before the passes across accounts'
                         f'{_cross}; total_cost is the same figure; '
                         f'the filing {COST_TERM[_c]} is `taxjson list`"')
        else:
            lines.append(f'base_cost_basis = "per-account, per-listing, '
                         f'before {_adj} and '
                         f'[[distributions]] adjustments (total_cost '
                         f'excludes those too; the filing {COST_TERM[_c]} '
                         f'is `taxjson list`)"')
    if blended:
        lines.append('# except a holding with cost_source = "blended": '
                     'its figures are the ones')
        lines.append("# after the passes across accounts (units moved in "
                     "from another of")
        lines.append("# your accounts, whose cost these books do not have).")
    lines.append("")

    held_stock = [s for s in agg
                  if not _parse_option(s) and not is_future_ticker(s)]
    for sym, qty, total_cost, currency, position_start_date, b in rows:
        opt = _parse_option(sym)
        if opt:
            opt['underlying'] = _resolve_underlying(opt['underlying'],
                                                    held_stock)
        is_fut = is_future_ticker(sym)
        # A coin: the account's (--crypto), or one a snapshot read back
        # declares (asset_type = "crypto", backlog TJ-0875).
        coin = crypto or bool(b.get('crypto'))
        mixed = bool(b.get('mixed_currency'))
        cps = total_cost / qty if qty else 0.0
        # The one listing currency of a cost in another (TJ-1109).
        _listings = b.get('listing_currencies') or ()
        listing = next(iter(_listings)) if len(_listings) == 1 else None
        lines.append("[[holding]]")
        lines.append(f"symbol = {_toml_str(_shown(sym))}")
        if args.account_name:
            lines.append(f"account = {_toml_str(args.account_name)}")
        lines.append(f"asset_type = "
                     f"{_toml_str('crypto' if coin else 'option' if opt else 'future' if is_fut else 'equity')}")
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
            elif b.get('multiplier_conflict'):
                # The inputs disagree on the size: unknown, said in a
                # key a reader keeps (this tool reads it back), never
                # the equity 100 (audit 2026-10-24 B21).
                _sizes = ", ".join(f"{x:g}" for x in
                                   sorted(b.get('multipliers_seen') or []))
                lines.append("contract_multiplier_unknown = true")
                lines.append("# contract_multiplier unknown: the inputs "
                             "declare different contract sizes"
                             + (f" ({_sizes})" if _sizes else "")
                             + "; cost_per_share below is per CONTRACT")
            else:
                # A futures option's multiplier is the future's (CL
                # 1000, micro contracts 0.1 ...), not the equity 100:
                # omitted rather than guessed when no row declared it
                # (audit S030-09).
                lines.append("# futures option: contract_multiplier "
                             "unknown here (not the equity 100)")
        lines.append(f"quantity = "
                     f"{float(f'{qty:.15g}') if coin else float(qty)!r}")
        base = base_agg.get(sym)
        # Units whose cost the account's own books do not have (moved in
        # from another of your accounts, an opening at an unknown cost):
        # no cost figure at all, never a cost of 0 a reader would apply
        # (backlog TJ-0598).
        unknown = bool(b.get('unknown_cost')
                       or (base is not None and base.get('unknown_cost')))
        # Moved-in units' cost from the passes across accounts, the
        # figure `taxjson list` shows (backlog TJ-0598).
        fill = blended.get(sym) if unknown and not mixed else None
        if fill and fill["native"] == fill["currency"]:
            _ft = fill["total_cost"]
            lines.append(f"currency = {_toml_str(fill['currency'])}")
            lines.append(f"total_cost = {round(_ft, 4)!r}")
            lines.append(f"cost_per_share = "
                         f"{_unit(_ft / qty if qty else 0.0, 6, coin)!r}")
            lines.append('cost_source = "blended"')
            lines.extend(_blended_note(sym, b,
                                       getattr(args, 'country', None),
                                       arrivals or {}, fill))
        elif unknown:
            _cur = (listing or currency or (fill or {}).get("native")
                    or move_currency.get(sym))
            if _cur and not mixed:
                lines.append(f"currency = {_toml_str(_cur)}")
            lines.append("cost_unknown = true")
            if fill:
                lines.append('cost_source = "blended"')
                lines.extend(_blended_note(sym, b,
                                           getattr(args, 'country', None),
                                           arrivals or {}, fill))
            else:
                lines.extend(_cost_unknown_note(
                    sym, b, getattr(args, 'country', None),
                    arrivals or {}))
        elif mixed:
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
            if currency and listing and listing != currency:
                # The cost's own label, as the positions reader takes it:
                # a CAD book value of a USD listing is no USD cost
                # (backlog TJ-1109).
                lines.append(f"currency = {_toml_str(listing)}")
                lines.append(f"cost_currency = {_toml_str(currency)}")
            elif currency:
                lines.append(f"currency = {_toml_str(currency)}")
            lines.append(f"total_cost = {round(total_cost, 4)!r}")
            lines.append(f"cost_per_share = {_unit(cps, 6, coin)!r}")
        # What kind of cost it is (`average` or `lots`, TJ-1110), when
        # every input of the holding says the same.
        _kinds = b.get('cost_kinds') or set()
        if not unknown and any(_kinds):
            if len(_kinds) == 1:
                lines.append(f"cost_kind = {_toml_str(next(iter(_kinds)))}")
            else:
                emit_line(f"warning: {sym}: its inputs state different kinds "
                          f"of cost: cost_kind is left out for it",
                          file=sys.stderr)
        expected_base = getattr(args, 'base_currency', None)
        # A base bucket whose figures are in more than one currency has
        # no single base-currency total: its flat figure called a CAD +
        # USD sum entirely CAD (audit 2026-10-24 B23); omitted and said.
        base_mixed = (base is not None and not unknown
                      and bool(base.get('mixed_currency')))
        base_ok = (base is not None and not unknown and base.get('currency')
                   and not base_mixed
                   and (expected_base is None
                        or base['currency'].upper() == expected_base.upper()))
        if base_mixed:
            _curs = ", ".join(sorted(base.get('cost_by_currency') or [])) \
                or "more than one currency"
            lines.append(f"# base_total_cost omitted: this holding's "
                         f"base-currency figures are in {_curs}, so no "
                         f"one base-currency total exists (a cost was "
                         f"not converted)")
            emit_line(f"warning: {sym}: its base-currency figures are in "
                      f"{_curs}: base_total_cost is omitted for it (a "
                      f"cost was not converted to the base currency)",
                      file=sys.stderr)
        # A snapshot's own base figure read back covers that snapshot's
        # units alone: beside an input with no base figure, or on top of
        # --base-gains for the same units, it is not the holding's base
        # cost (backlog TJ-1031). --base-gains holds the same positions
        # as the inputs, by contract: every input is covered there.
        base_partial = bool(
            base is not None and not unknown and not base_mixed
            and base.get('snapshot_base')
            and ((b.get('no_base_figure')
                  and not getattr(args, 'base_gains', None))
                 or abs(float(base.get('qty') or 0.0) - qty) > 1e-6))
        if base_partial:
            base_ok = False
            lines.append("# base_total_cost omitted: the base-currency "
                         "figures read are not of this whole holding's "
                         "units")
            emit_line(f"warning: {sym}: its base-currency figures are not "
                      f"of the whole holding's units: base_total_cost is "
                      f"omitted for it (one input's figure is not the cost "
                      f"of the summed quantity)", file=sys.stderr)
        if fill:
            _ft = fill["total_cost"]
            lines.append(f"base_currency = {_toml_str(fill['currency'])}")
            lines.append(f"base_total_cost = {round(_ft, 4)!r}")
            lines.append(f"base_cost_per_share = "
                         f"{_unit(_ft / qty if qty else 0.0, 6, coin)!r}")
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
            lines.append(f"base_cost_per_share = {_unit(base_cps, 6, coin)!r}")
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
    """Adapt a holdings TOML document (`[[holding]]` tables: this tool's
    snapshot, taxjson-fetch's Questrade snapshot, a hand-typed file) to
    the `inventory`-shaped dict the rest of this tool consumes:
    `quantity` maps to `qty`, `contract_multiplier` (or `multiplier`) to
    `multiplier`; symbol / currency / position_start_date pass through.

    The cost is read as the shared positions reader reads it
    (lib/positions_reports): total_cost, book_cost or cost_basis, else
    average_entry_price (a price per SHARE: x the contract size for an
    option) x quantity, else cost_per_share (per holding unit) x
    quantity; a holding with none of them is `cost_unknown`, never a
    cost of 0 (backlog TJ-0873). Kept as well: asset_type = "crypto"
    (TJ-0875), the base-currency companion (base_currency /
    base_total_cost: the `base_inventory` rows), a cost_source =
    "blended" figure (`blended`, as _blended_fill gives it; TJ-0733) and
    a cost_currency apart from the listing's (TJ-1109).
    [meta] holdings_count must be the number of [[holding]] tables, as
    the positions reader requires (TJ-0877); `as_of` is the snapshot's
    day ([meta] as_of, else the day of generated_at; TJ-0874). A cash
    line (asset_type = "cash") is no holding. TOML parses a bare
    YYYY-MM-DD as a date object, so the adapter stringifies it."""
    holdings = doc.get("holding")
    if holdings is None:
        # An empty taxjson snapshot has no [[holding]] entries but has
        # its [meta] table; anything else (a [[holdings]] typo, another
        # tool's file) used to export NOTHING at exit 0 while `taxjson
        # sanity` refused the same file (audit S030-10).
        from taxjson.lib.holdings_dir import empty_snapshot
        if empty_snapshot(doc):
            holdings = []
        else:
            _die(f"{path}: no [[holding]] array (a taxjson holdings "
                 f"snapshot is expected); tables found: "
                 f"{', '.join(sorted(doc)) or 'none'}")
    if not isinstance(holdings, list) or any(
            not isinstance(h, dict) for h in holdings):
        _die(f"{path}: 'holding' must be an array of tables ([[holding]])")
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    count = meta.get("holdings_count")
    if count is not None and (isinstance(count, bool)
                              or not isinstance(count, int)
                              or count != len(holdings)):
        # The writer's own count: a file with fewer tables than it
        # declares is cut short or edited. Re-written, its holdings
        # would be declared complete (backlog TJ-0877; the positions
        # reader refuses it the same way, audit 2026-10-24 B37).
        _die(f"{path}: [meta] holdings_count = {count!r} but the file has "
             f"{len(holdings)} [[holding]] table(s) — the snapshot is "
             f"incomplete or edited; download it again")
    from taxjson.lib.positions_reports import (PositionsReportError,
                                               toml_as_of)
    try:
        as_of = toml_as_of(meta, str(path))
    except PositionsReportError as e:
        _die(str(e))
    import math

    def _num(h, i, sym, key):
        """The finite number `key` holds, None when it is absent."""
        v = h.get(key)
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(v):
            _die(f"{path}: [[holding]] {i} ({sym}): {key} is {v!r}, "
                 f"not a number")
        return float(v)

    def _flag(h, i, sym, key):
        v = h.get(key)
        if v is not None and not isinstance(v, bool):
            _die(f"{path}: [[holding]] {i} ({sym}): {key} is {v!r}, not "
                 f"true or false")
        return bool(v)

    inventory = []
    base_inventory = []
    blended: Dict[str, Dict[str, Any]] = {}
    symbols: List[str] = []
    lots: List[Tuple[int, str]] = []
    for i, h in enumerate(holdings, start=1):
        # A quantity 'abc' was a float() traceback in --report and
        # --holdings-toml (A2-1441): refused here, naming the row.
        sym = h.get("symbol")
        if not isinstance(sym, str) or not sym.strip():
            _die(f"{path}: [[holding]] {i}: symbol is {sym!r}, not a "
                 f"ticker")
        atype = str(h.get("asset_type") or "").strip().lower()
        if atype == "cash":
            continue                    # a cash line, not a holding
        qty = _num(h, i, sym, "quantity")
        qty = 0.0 if qty is None else qty
        psd = h.get("position_start_date")
        if psd is not None and not isinstance(psd, str):
            psd = psd.isoformat()
        # The contract size this tool wrote (`contract_multiplier`), or
        # the fetch snapshot's `multiplier`: read back, or a 10-share
        # option came back as the default 100.
        mult = None
        for key in ("contract_multiplier", "multiplier"):
            mult = _num(h, i, sym, key)
            if mult is not None:
                if mult <= 0:
                    _die(f"{path}: [[holding]] {i} ({sym}): {key} is "
                         f"{h.get(key)!r}, not a positive number")
                break
        # The unknown state this tool wrote (its inputs disagreed on the
        # size): read back, or the re-read invented the equity 100
        # (audit 2026-10-24 B21).
        _unknown = _flag(h, i, sym, "contract_multiplier_unknown")
        _flags: Dict[str, Any] = (
            {"multiplier_conflict": True} if _unknown else {})
        if atype == "crypto":
            _flags["asset_type"] = "crypto"
        # One lot of a purchase day (`acquired`): --holdings-toml refuses
        # the file (`main`), as one table per symbol cannot keep it. The
        # kind of cost is kept (TJ-1110).
        if h.get("acquired") not in (None, ""):
            lots.append((i, sym))
        _ck = str(h.get("cost_kind") or "").strip().lower()
        if _ck and _ck not in ("average", "lots"):
            _die(f"{path}: [[holding]] {i} ({sym}): cost_kind {_ck!r} must "
                 f"be \"average\" or \"lots\"")
        if _ck:
            _flags["cost_kind"] = _ck
        # A cost this tool left out (`cost_unknown`, backlog TJ-0598):
        # read back as unknown, never as a cost of 0.
        if _flag(h, i, sym, "cost_unknown"):
            _flags["unknown_cost"] = True
        cur = h.get("currency", "")
        # (`main` counts the inputs that hold a blended holding: a row at
        # quantity 0 holds nothing, unless it is the blended figure's
        # own; backlog TJ-1232.)
        if qty or h.get("cost_source") == "blended":
            symbols.append(sym)
        # The base-currency companion this tool wrote; a cost_source =
        # "blended" holding's figure is the one after the passes across
        # accounts (TJ-0598), read back as that (TJ-0733).
        has_base = False
        btot = _num(h, i, sym, "base_total_cost")
        bcur = h.get("base_currency")
        if btot is not None and isinstance(bcur, str) and bcur.strip():
            if h.get("cost_source") == "blended":
                # (`main` takes it for the holding it alone makes up)
                blended[sym] = {"total_cost": btot,
                                "currency": bcur.strip().upper(),
                                "native": str(cur or "").strip().upper()}
            else:
                base_inventory.append({
                    "symbol": sym, "qty": qty, "total_cost": btot,
                    "currency": bcur.strip(), "position_start_date": psd,
                    "multiplier": mult, "snapshot_base": True,
                    **({"unknown_cost": True}
                       if _flags.get("unknown_cost") else {})})
                has_base = True
        if not has_base and qty:
            _flags["no_base_figure"] = True     # (TJ-1031)
        if h.get("mixed_currency"):
            # A JOURNAL-folded cross-currency bucket this tool wrote: no
            # flat total_cost, one total_cost_<cur> per currency. Read
            # as zero cost, silently (2026-10 audit #64); one item per
            # currency (the quantity on the first) rebuilds the bucket's
            # per-currency costs in the aggregation. Each carries the
            # holding's quantity for the --long / --short filter, or
            # the zero-quantity ones were filtered out (TJ-0881).
            parts = []
            for k in sorted(h):
                if not (isinstance(k, str) and k.startswith("total_cost_")
                        and len(k) > len("total_cost_")):
                    continue
                parts.append((k[len("total_cost_"):].upper(),
                              _num(h, i, sym, k)))
            if not parts:
                _die(f"{path}: [[holding]] {i} ({sym}): mixed_currency = "
                     f"true but no total_cost_<currency> figure — its "
                     f"cost is unknown; regenerate the file with "
                     f"`taxjson run`")
            for j, (pcur, cost) in enumerate(parts):
                inventory.append({
                    "symbol": sym,
                    "qty": qty if j == 0 else 0,
                    "holding_qty": qty,
                    "total_cost": cost,
                    "currency": pcur,
                    "position_start_date": psd,
                    "multiplier": mult,
                    **_flags,
                })
            continue
        cost = None
        for key in ("total_cost", "book_cost", "cost_basis"):
            cost = _num(h, i, sym, key)
            if cost is not None:
                break
        if cost is None:
            aep = _num(h, i, sym, "average_entry_price")
            if aep is not None:
                # Questrade's averageEntryPrice is per SHARE: an option
                # contract is its declared size, else the equity
                # option's 100; a futures option's size, or one the
                # inputs disagree on, unknown (never the equity 100).
                if is_option_ticker(sym):
                    size = (None if _unknown else mult if mult
                            else None if is_future_ticker(sym)
                            else float(_OPTION_CONTRACT_MULTIPLIER))
                else:
                    size = mult or 1.0
                if size is not None:
                    cost = aep * qty * size
            else:
                # cost_per_share as this tool writes it: per HOLDING
                # UNIT (per contract for an option), never scaled.
                cps = _num(h, i, sym, "cost_per_share")
                if cps is not None:
                    cost = cps * qty
        if cost is None and not _flags.get("unknown_cost"):
            _flags["unknown_cost"] = True
        # A cost in cost_currency (a broker's CAD book value of a USD
        # listing, as the positions reader keeps it apart): the row's
        # figure is in that currency, never relabelled the listing's
        # (backlog TJ-1109). With no cost there is nothing it labels.
        row_cur = cur
        _ccur = str(h.get("cost_currency") or "").strip().upper()
        if (cost is not None and _ccur
                and _ccur != str(cur or "").strip().upper()):
            row_cur = _ccur
            if str(cur or "").strip():
                _flags["listing_currency"] = str(cur).strip().upper()
        inventory.append({
            "symbol": sym,
            "qty": qty,
            "total_cost": cost if cost is not None else 0.0,
            "currency": row_cur,
            "position_start_date": psd,
            "multiplier": mult,
            **_flags,
        })
    return {"inventory": inventory, "base_inventory": base_inventory,
            "blended": blended, "symbols": symbols, "as_of": as_of,
            "lots": lots, "account": meta.get("account"),
            "broker_account": meta.get("broker_account")}


# Options of removed exports: {option: what to say}. Refused by name
# (exit 2) before argparse, which would only say "unrecognized".
_LEFT = "--report and --holdings-toml remain"
_REMOVED_OPTIONS = {
    "--tradingview": f"the TradingView watchlist export was removed — "
                     f"{_LEFT}",
    "--tv-map": "the TradingView watchlist export (and its --tv-map) was "
                "removed",
    "--seekingalpha": f"the Seeking Alpha watchlist export was removed — "
                      f"{_LEFT}",
    "--fastgraph": f"the FastGraph watchlist export was removed — {_LEFT}",
}


def _refuse_removed_options(argv) -> None:
    for a in argv:
        if a == "--":
            break
        opt = a.split("=", 1)[0]
        if opt in _REMOVED_OPTIONS:
            _die(f"{opt}: {_REMOVED_OPTIONS[opt]}")


@guard_main("taxjson-export")
def main():
    parser = argparse.ArgumentParser(
        description="Export holdings as a plain-text report (--report) or "
                    "a TOML snapshot (--holdings-toml).",
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

    # Output mode (one is required: with none the tool used to print a
    # bare ticker list, the removed watchlist exports' path).
    group = parser.add_mutually_exclusive_group(required=True)
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
        "--crypto", action="store_true",
        help="The inputs are a crypto account's books (`taxjson run` "
             "passes them twice: as the holdings and as --base-gains): "
             "each --holdings-toml holding is asset_type \"crypto\", its "
             "quantity and per-unit costs kept to significant digits.",
    )
    parser.add_argument(
        "--symbol-suffix", default=None, metavar="SFX",
        help="With --crypto: write each coin as COIN.SFX (`taxjson run` "
             "passes the exchange's venue suffix from data/markets.toml "
             "[crypto_venues], KR for Kraken: the form the exchange's own "
             "holdings snapshot uses).",
    )
    parser.add_argument(
        "--venue-book", action="append", default=[],
        metavar="BROKER=FILE",
        help="With --crypto, an account holding several exchanges' "
             "exports (`taxjson run` passes each exchange's parsed rows "
             "and transfer rows): a coin one exchange alone holds is "
             "written with that exchange's venue suffix, one several "
             "hold (or held at an exchange with none) bare. Replaces "
             "--symbol-suffix.",
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
             "Norbert's Gambit legs (e.g. a `SAMPLF.US SAMPLF.TO` line folds "
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
    parser.add_argument(
        "--blended-gains", action="append", default=[], metavar="FILE",
        help="For --holdings-toml: the account's books after the passes "
             "across accounts (`taxjson run` passes work/<account>"
             "_gains_wash.json after its blended pass). A holding whose "
             "own books have no cost for units moved in from another of "
             "your accounts takes its figure there (`taxjson list`'s), "
             "marked cost_source = \"blended\".",
    )

    _refuse_removed_options(sys.argv[1:])
    args = parser.parse_args()

    agg: Dict[str, Dict[str, Any]] = {}

    # The holdings stay per listing: a journal between two listings is
    # moved by its transfer legs (the broker's, or a .tt JOURNAL line's —
    # lib/dated_events) through _apply_transfer_evidence, a legacy
    # ticker.map JOURNAL line being a TOBASE line. DELETE is honored
    # (idempotent — the merge already applied it).
    if args.map_file:
        _tmap = load_map_file(Path(args.map_file))
        holdings_map, holdings_drops = {}, _tmap.delete
    else:
        _tmap = None
        holdings_map, holdings_drops = {}, set()

    def dispatch(data):
        process_data_report(data, args, agg, holdings_map, holdings_drops)

    # A holdings snapshot read back has had no rename pass: the map's
    # GLOBAL renames (chains to their end, an option through its
    # underlying) apply to its symbols here (backlog TJ-0883). A gains
    # file of `taxjson run` has them already.
    toml_renames: Dict[str, str] = {}
    if _tmap is not None:
        from taxjson.bin.taxjson_ticker_map import map_symbol, merge_renames
        try:
            toml_renames = merge_renames(_tmap, False)
        except ValueError as e:
            _die(str(e))
    # What the snapshots read back carry beside their holdings
    # (_holdings_toml_to_inventory): base-currency companions, the
    # figures after the passes across accounts, and each file's day.
    toml_base: List[Dict[str, Any]] = []
    toml_blended: Dict[str, Dict[str, Any]] = {}
    holders: Dict[str, int] = {}
    toml_days: List[Tuple[str, str]] = []
    toml_accounts: List[Tuple[Any, Any]] = []
    json_inputs = not args.inputs

    def dispatch_toml(conv, input_path):
        def _ren(sym):
            return map_symbol(sym, toml_renames) if toml_renames else sym
        for item in conv["inventory"] + conv["base_inventory"]:
            item["symbol"] = _ren(item["symbol"])
        for sym in conv["symbols"]:
            holders[_ren(sym)] = holders.get(_ren(sym), 0) + 1
        for sym, fill in conv["blended"].items():
            toml_blended[_ren(sym)] = fill
        toml_base.extend(conv["base_inventory"])
        toml_days.append((str(input_path), conv["as_of"]))
        toml_accounts.append((conv["account"], conv["broker_account"]))
        dispatch(conv)

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
                    # (a leading BOM dropped: re-audit A2-1409)
                    with open(input_path, 'rb') as f:
                        _raw = f.read()
                    doc = tomllib.loads(
                        _raw.removeprefix(b"\xef\xbb\xbf").decode("utf-8"))
                except (tomllib.TOMLDecodeError, OSError,
                        UnicodeDecodeError) as e:
                    _die(f"{input_path}: not a readable TOML file ({e})")
                _conv = _holdings_toml_to_inventory(doc, input_path)
                if args.platform == "holdings_toml" and _conv["lots"]:
                    # One table per symbol here: the lots would be merged
                    # and their dates lost, and `taxjson opening` needs a
                    # US lot's date for its holding period (backlog
                    # TJ-1110). The --report table is of totals.
                    _n, _s = _conv["lots"][0]
                    _die(f"{input_path}: [[holding]] {_n} ({_s}) is a lot "
                         f"with its own purchase day (`acquired`): "
                         f"--holdings-toml writes one table per symbol, so "
                         f"the lots would be merged and their dates lost. "
                         f"`taxjson sanity` and `taxjson opening` read this "
                         f"file as it is; --report sums it")
                dispatch_toml(_conv, input_path)
                continue
            data = _read_rows_doc(input_path, require_key="inventory")
            if not isinstance(data["inventory"], list):
                _die(f"{input_path}: 'inventory' must be a list")
            year = year or (data.get("summary") or {}).get("year")
            json_inputs = True
            for item in data["inventory"]:
                if item.get("symbol") and item.get("qty"):
                    holders[item["symbol"]] = holders.get(item["symbol"],
                                                          0) + 1
                    item["no_base_figure"] = True       # (TJ-1031)
            dispatch(data)

    # The snapshot's day (backlog TJ-0874): the day of the snapshots read
    # back, when every input is one of that day. Snapshots of different
    # days are no one snapshot; a day beside other inputs (a gains file,
    # an undated snapshot) is not the day of all of them: said.
    # (The --report table is of the books' positions: no date of its
    # own.)
    args.snapshot_as_of = None
    _days = (sorted({d for _p, d in toml_days if d})
             if args.platform == "holdings_toml" else [])
    if len(_days) > 1:
        _die("the holdings snapshots are of different days ("
             + ", ".join(f"{Path(p).name}: {d or 'no date'}"
                         for p, d in toml_days)
             + "): one snapshot holds one day's positions — export them "
               "one at a time")
    if _days:
        if json_inputs or any(not d for _p, d in toml_days):
            emit_line(f"warning: the snapshot's day {_days[0]} is not "
                      f"written as [meta] as_of: the other inputs are not "
                      f"of that day (meta.generated_at dates the file)",
                      file=sys.stderr)
        else:
            args.snapshot_as_of = _days[0]

    # The account of the snapshots read back (backlog TJ-1111): carried
    # when every input is a snapshot naming the same one. A file that
    # names none, or a gains file, is not known to be of that account.
    def _one_account(k):
        names = {str(a[k] if a[k] is not None else "").strip()
                 for a in toml_accounts}
        return (names.pop() or None
                if len(names) == 1 and not json_inputs else None)
    args.meta_account = _one_account(0)
    args.meta_broker_account = _one_account(1)

    if args.platform == "report":
        for line in render_report(agg, args.dust_threshold, year=year):
            print(line)
        return

    # --holdings-toml.
    # Base-currency companion inventory (optional). Aggregated with the
    # same JOURNAL/DELETE map as the native holdings so symbol keys line
    # up; values are in the base currency for downstream gain/loss checks.
    base_agg: Dict[str, Dict[str, Any]] = {}
    for bp in args.base_gains:
        process_data_report(_read_rows_doc(bp, require_key="inventory"),
                            args, base_agg, holdings_map, holdings_drops)
    # A snapshot read back: its own base-currency figures (TJ-0733).
    if toml_base:
        process_data_report({"inventory": toml_base}, args, base_agg,
                            holdings_map, holdings_drops)
    # Per-symbol acquisition/sell events (optional), in native currency,
    # from the pre-gains transaction file(s). Mapped/dropped the same way
    # as the holdings so events attach to the right (netted) symbol key.
    trades_by_symbol = (
        _load_trade_events(args.trades, holdings_map, holdings_drops,
                           country=args.country)
        if args.trades else None)
    # Evidence-driven depot flips (see _apply_transfer_evidence):
    # applied to BOTH inventories so symbol keys stay aligned.
    _moves = _apply_transfer_evidence(
        agg, args.transfer_evidence, _tmap, toml_renames)
    _replay_moves_on_base(base_agg, _moves, _tmap)
    # Several exchanges in one crypto account: each coin its own
    # venue's suffix (TJ-0603).
    args.coin_suffix = (_venue_suffixes(args.venue_book, args.trades or ())
                        if args.venue_book else None)
    # The moves of your own that brought units with no cost in these
    # books: named under a holding whose cost is unknown (TJ-0598).
    arrivals = (_own_move_arrivals(args.trades, holdings_map)
                if args.trades else None)
    move_currency = (_own_move_currency(args.trades, holdings_map)
                     if args.trades else {})
    # Their cost after the passes across accounts (TJ-0598).
    blended = None
    if args.blended_gains:
        from taxjson.bin.taxjson_ticker_map import map_symbol, raw_renames
        _canon = raw_renames(_tmap, True) if _tmap is not None else {}

        def to_canonical(sym: str) -> str:
            seen = {sym}
            while True:
                nxt = map_symbol(sym, _canon)
                if nxt in seen:
                    return sym
                seen.add(nxt)
                sym = nxt
        blended = _blended_fill(
            agg, args.blended_gains, args.account_name, to_canonical,
            args.base_currency, args.dust_threshold, move_currency)
    # A snapshot's cost_source = "blended" figures read back (TJ-0733):
    # kept for a holding no other input adds to, in the expected base
    # currency.
    for sym, fill in sorted(toml_blended.items()):
        b = agg.get(sym)
        if b is None or (blended or {}).get(sym):
            continue
        if holders.get(sym, 0) != 1:
            # Left out for real (backlog TJ-1071): a figure in the base
            # currency was already in the holding's total_cost, summed
            # with the other input's and no longer labelled blended.
            b["unknown_cost"] = True
            b["blended_left_out"] = True
            emit_line(f"warning: {sym}: its cost after the passes across "
                      f"accounts (cost_source = \"blended\") is left out: "
                      f"more than one input holds it, so the holding is "
                      f"written cost_unknown, with no total_cost or "
                      f"base_total_cost", file=sys.stderr)
            continue
        if (args.base_currency
                and fill["currency"] != args.base_currency.upper()):
            continue
        blended = blended or {}
        blended[sym] = fill
        b["unknown_cost"] = True      # (its figure is the fill's)
    print("\n".join(render_holdings_toml(agg, args, base_agg,
                                         trades_by_symbol, arrivals,
                                         blended, move_currency)))


if __name__ == "__main__":
    main()
