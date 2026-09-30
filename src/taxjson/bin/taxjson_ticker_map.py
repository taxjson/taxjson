#!/usr/bin/env python3
"""
taxjson_ticker_map.py

Usage:
  Summary Mode (No map file):
    taxjson-ticker-map input.json > ticker_map.json
    Generates a mapping of all unique tickers in the input to their defaults.

  Transform Mode (With map file):
    taxjson-ticker-map input.json ticker.map > mapped_input.json
    Applies the mappings from ticker.map to all transactions and outputs a new JSON file.
    Options are mapped by applying the underlying mapping (e.g. AEM.US -> AEM.TO applies to AEM250101C00100000.US -> AEM250101C00100000.TO).
"""

import argparse
import json
import sys
import re
from collections import namedtuple
from pathlib import Path
from typing import Dict, List, Any
from datetime import datetime

from taxjson.lib.core import TaxTransaction, load_transactions
from taxjson.lib.ticker_map import map_ticker

# A ticker-map line is `KEYWORD from [to]`. One file, four rule types:
#   GLOBAL  from to   — a plain rename; applies in every stage (a
#                       security's true ticker, e.g. DFDV1 -> DFDV).
#   TOBASE  from to   — currency-equivalent consolidation; applies only
#                       when converting to base currency (the main
#                       pipeline). The raw holdings view keeps the two
#                       listings separate.
#   JOURNAL from to   — like TOBASE in the main pipeline, AND nets the
#                       two legs together in the holdings view (a
#                       Norbert's Gambit pair, e.g. DLR.U.TO / DLR.TO).
#   DELETE  from      — nuke that ticker's transactions (a pure artifact).
#   DISTINCT a b      — declares two look-alike listings are SEPARATE
#                       securities (a CDR vs its US underlying); changes
#                       no symbol, silences the scan's MAP-GAP nag.
_MAP_KEYWORDS = ("GLOBAL", "TOBASE", "JOURNAL", "DELETE", "DISTINCT")

# Parsed map: glob/tobase/journal are {from: to}; delete is {symbol,...};
# distinct is a set of frozenset pairs the user declares are SEPARATE
# securities despite looking like one (CDRs vs their US underlying:
# UNH.TO is a fractional CAD-hedged receipt over UNH.US, not a listing
# equivalent — pooling their ACB would be wrong). DISTINCT changes no
# symbol; it silences the scan's MAP-GAP nagging for that pair and
# records the judgment in the map file where it belongs.
TickerMap = namedtuple("TickerMap",
                       ["glob", "tobase", "journal", "delete",
                        "distinct"])


def _parse_map_file(file_path: Path):
    """(TickerMap, problems, notes). `problems` are the lines that
    could not be parsed — each rule on them is DROPPED — as
    `<file>:<lineno>: <message>: '<line>'`; `notes` are harmless
    no-op lines (a DISTINCT that pairs a symbol with itself)."""
    glob: Dict[str, str] = {}
    tobase: Dict[str, str] = {}
    journal: Dict[str, str] = {}
    delete = set()
    distinct = set()
    problems: List[str] = []
    notes: List[str] = []
    buckets = {"GLOBAL": glob, "TOBASE": tobase, "JOURNAL": journal}
    with file_path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            where = f"{file_path.name}:{lineno}"
            parts = line.split()
            kw = parts[0].upper()
            if kw not in _MAP_KEYWORDS:
                problems.append(
                    f"{where}: line has no GLOBAL/TOBASE/JOURNAL/DELETE/"
                    f"DISTINCT keyword: {line!r}")
                continue
            if kw == "DELETE":
                if len(parts) >= 2:
                    delete.add(parts[1])
                else:
                    problems.append(f"{where}: DELETE line needs a "
                                    f"symbol: {line!r}")
            elif kw == "DISTINCT":
                if len(parts) >= 3:
                    if parts[1] == parts[2]:
                        notes.append(f"{where}: DISTINCT pairs a symbol "
                                     f"with itself (no effect): {line!r}")
                        continue
                    distinct.add(frozenset((parts[1], parts[2])))
                else:
                    problems.append(f"{where}: DISTINCT line needs "
                                    f"`a b`: {line!r}")
            else:
                if len(parts) >= 3:
                    buckets[kw][parts[1]] = parts[2]
                else:
                    problems.append(f"{where}: {kw} line needs `from to` "
                                    f"(two symbols separated by a space): "
                                    f"{line!r}")
    return TickerMap(glob, tobase, journal, delete, distinct), problems, notes


def map_file_problems(file_path: Path) -> List[str]:
    """The ticker-map lines that cannot be parsed (their rules would be
    dropped), each as `<file>:<lineno>: <message>`. `taxjson run`
    refuses a map with any: a dropped TOBASE/GLOBAL/JOURNAL rule
    silently changes ACB pools and the Schedule 3 gain (S009-03)."""
    return _parse_map_file(file_path)[1]


def load_map_file(file_path: Path) -> "TickerMap":
    """Parse a keyword-prefixed ticker-map file into a TickerMap. A
    malformed line is skipped with a warning naming its line number
    (`taxjson run` refuses such a map up front — map_file_problems)."""
    tmap, problems, notes = _parse_map_file(file_path)
    for msg in problems:
        print(f"warning: {msg} — skipping", file=sys.stderr)
    for msg in notes:
        print(f"warning: {msg} — skipping", file=sys.stderr)
    return tmap


def merge_renames(tmap: "TickerMap", to_base: bool) -> Dict[str, str]:
    """Build the symbol-rename dict for a merge stage. GLOBAL renames
    always apply; TOBASE and JOURNAL renames apply only when converting
    to base currency (`to_base`) — pre-gains they'd otherwise create a
    mixed-currency ACB pool, which only the conversion stage resolves."""
    renames = dict(tmap.glob)
    if to_base:
        renames.update(tmap.tobase)
        renames.update(tmap.journal)
    return renames


def apply_drops(transactions: List[TaxTransaction], drops) -> List[TaxTransaction]:
    """Remove transactions whose symbol is in `drops`.

    For each dropped ticker, prints a NOTE — count, net quantity, net
    amount — so the deletion is auditable rather than silent. If a
    dropped ticker's net quantity looks like a real position
    (|net| >= 1 share) a loud warning is printed, since DROP discards
    its cost basis. Returns the kept transactions."""
    if not drops:
        return transactions

    def _field(tx, name):
        v = tx.get(name) if isinstance(tx, dict) else getattr(tx, name, 0)
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    kept: List[TaxTransaction] = []
    removed: Dict[str, list] = {}
    for tx in transactions:
        sym = tx.get('symbol') if isinstance(tx, dict) else getattr(tx, 'symbol', None)
        new_sym = (tx.get('symbol_new') if isinstance(tx, dict)
                   else getattr(tx, 'symbol_new', None)) or None
        if sym in drops:
            removed.setdefault(sym, []).append(tx)
        elif new_sym and new_sym in drops:
            # A SPLIT-rename TARGETING a dropped ticker would rename a
            # live pool into the dropped identity (whose trades are
            # gone) — the rename row goes with the ticker.
            removed.setdefault(new_sym, []).append(tx)
        else:
            kept.append(tx)

    for sym in sorted(removed):
        rows = removed[sym]
        net_qty = sum(_field(t, 'quantity') for t in rows)
        net_amt = sum(_field(t, 'net_amount') for t in rows)
        print(
            f"NOTE: ticker-map DROP removed {len(rows)} {sym} row(s) "
            f"(net qty {net_qty:.4f}, net amount {net_amt:.2f}).",
            file=sys.stderr,
        )
        if abs(net_qty) >= 1.0:
            print(
                f"warning: dropped ticker {sym} has a net quantity of "
                f"{net_qty:.4f} — that looks like a real position, and "
                f"DROP discards its cost basis. Remove the DROP line if "
                f"{sym} is not a pure artifact.",
                file=sys.stderr,
            )
    return kept

def generate_summary(transactions: List[TaxTransaction]) -> Dict[str, Any]:
    unique_tickers = set()
    for tx in transactions:
        if tx.symbol:
            unique_tickers.add(tx.symbol)
    
    mappings = {}
    for ticker in sorted(unique_tickers):
        mapped = map_ticker(ticker, "CAD")
        mappings[ticker] = mapped
        
    return {
        "mappings": mappings,
        "total_unique_tickers": len(mappings),
        "generated_at": datetime.now().strftime("%Y-%m-%d")
    }

def map_symbol(symbol: str, mapping: Dict[str, str]) -> str:
    """The rename a mapping implies for ONE symbol — exact match first,
    else options map through their UNDERLYING (AEM.US -> AEM.TO also
    renames AEM260116C00150000.US -> ...TO). Pure-string twin of
    apply_mapping so non-transaction consumers (taxjson sanity) apply
    the same consolidation the pipeline does."""
    if symbol in mapping:
        return mapping[symbol]
    # Option check (e.g. AEM260116C00150000.US or F:CL251220P00053000.US)
    match = re.match(r'^((?:F:)?[A-Z0-9\.]+?)(\d{6}[CP]\d+)\.(\S+)$',
                     symbol, re.IGNORECASE)
    if match:
        underlying, contract, ext = match.groups()
        lookup = f"{underlying}.{ext}"
        if lookup in mapping:
            target_full = mapping[lookup]
            if '.' in target_full:
                target_underlying, target_ext = target_full.rsplit('.', 1)
            else:
                target_underlying, target_ext = target_full, ext
            return f"{target_underlying}{contract}.{target_ext}"
    return symbol


def guard_option_listing_collisions(symbols, mapping: Dict[str, str],
                                    prog: str = "taxjson-ticker-map"
                                    ) -> Dict[str, str]:
    """`mapping` plus an identity rule for every OPTION the underlying
    rule would move onto a contract code the book already carries
    under that code natively (R1-16). `TOBASE BCE.US BCE.TO` renames
    BCE270115C00025000.US (a USD-strike OCC contract) to ...TO — when
    the book also trades the Montreal BCE270115C00025000.TO (a
    CAD-strike CDCC contract), the rename pooled two different
    properties' ACB (not identical under s.47/s.54) and changed the
    reported gain without a word. The US contract now keeps its own
    symbol, and each one is named on stderr. An exact rule for the
    option symbol itself still wins (it is the user's explicit
    choice)."""
    natives = set(s for s in symbols if s)
    out = dict(mapping)
    for sym in sorted(natives):
        if sym in mapping:
            continue                    # explicit rule for this symbol
        target = map_symbol(sym, mapping)
        if target != sym and target in natives:
            out[sym] = sym
            print(f"{prog}: warning: the underlying rule would rename option "
                  f"{sym} to {target}, a different listed contract this book "
                  f"also trades (strike currency and clearing house differ, so "
                  f"they are not identical property) — kept separate as {sym}. "
                  f"Add an exact rule for {sym} in ticker.map if they really "
                  f"are one contract.", file=sys.stderr)
    return out


def apply_mapping(tx: TaxTransaction, mapping: Dict[str, str]) -> TaxTransaction:
    mapped_symbol = map_symbol(tx.symbol, mapping)
    if mapped_symbol != tx.symbol:
        tx.symbol = mapped_symbol
    # A SPLIT-rename's TARGET must consolidate under the same identity
    # as the trades it renames INTO. Mapping only tx.symbol left e.g.
    # `HES.US -> symbol_new=CVX.US` pointing at an orphan CVX.US pool
    # while the later CVX trades mapped to CVX.TO — the engine renamed
    # the basis into a pool no sale ever draws from (phantom short +
    # stranded ACB).
    new_sym = getattr(tx, 'symbol_new', '') or ''
    if new_sym:
        mapped_new = map_symbol(new_sym, mapping)
        if mapped_new != new_sym:
            tx.symbol_new = mapped_new
    return tx

def main():
    parser = argparse.ArgumentParser(description="Map tickers in a tax.json file")
    parser.add_argument("input", help="Input JSON file with transactions")
    parser.add_argument("map_file", nargs="?", help="Optional map file (ticker.map). If provided, outputs updated JSON.")
    parser.add_argument("--map", dest="map_flag", default=None,
                        help="Same as the positional map file (for "
                             "callers that prefer a flag).")
    parser.add_argument("--global-only", action="store_true",
                        help="Apply only GLOBAL renames and DELETEs — "
                             "the crypto pipeline's subset (TOBASE/"
                             "JOURNAL assume exchange-suffixed "
                             "cross-listings).")
    args = parser.parse_args()
    if args.map_flag and not args.map_file:
        args.map_file = args.map_flag

    transactions = load_transactions(Path(args.input))

    if not args.map_file:
        # Summary Mode
        output_data = generate_summary(transactions)
        json.dump(output_data, sys.stdout, indent=2, sort_keys=True)
        print()
    else:
        # Transform Mode — standalone tool applies the full map
        # (GLOBAL + TOBASE + JOURNAL renames, plus DELETE).
        tmap = load_map_file(Path(args.map_file))
        mapping = merge_renames(tmap,
                                to_base=not args.global_only)
        # DELETE first, then rename: a DELETE line names the broker's
        # RAW symbol. taxjson-merge2 applies the same order so one map
        # file means one thing on the equity and crypto paths.
        transactions = apply_drops(transactions, tmap.delete)
        mapping = guard_option_listing_collisions(
            [t.symbol for t in transactions], mapping)
        updated_transactions = []

        for tx in transactions:
            # We copy to avoid mutating the original if we cared, but we're just outputting
            updated_tx = apply_mapping(tx, mapping)
            updated_transactions.append(updated_tx.to_dict())

        output_data = {
            "transactions": updated_transactions,
            "metadata": {
                "source_file": args.input,
                "map_file": args.map_file,
                "mapped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }
        json.dump(output_data, sys.stdout, indent=2, sort_keys=True)
        print()

if __name__ == "__main__":
    main()
