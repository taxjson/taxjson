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
    Options are mapped by applying the underlying mapping (e.g. SAMPLM.US -> SAMPLM.TO applies to SAMPLM250101C00100000.US -> SAMPLM250101C00100000.TO).

ticker.map is the project's one mapping file. Rename rules: GLOBAL, TOBASE,
JOURNAL, DELETE, DISTINCT, RENAME. Lookups that change no symbol (read by
other tools; this one skips them): QUOTE SYMBOL YAHOO_SYMBOL [RATIO],
CRYPTO SYMBOL YAHOO_ID, EXTRACT description words | CURRENCY | SYMBOL,
T1135 SYMBOL COUNTRY.
A TRADINGVIEW line (the removed TradingView export) is ignored. See
docs/settings.md, "ticker.map".
"""

from taxjson.lib.stage_msg import emit_line
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List
from datetime import datetime

from taxjson.lib import out as _out
from taxjson.lib.cli_diag import guard_main
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.ticker_map import map_ticker
from taxjson.lib.ticker_map_rules import bare_target_warnings, map_symbol, merge_renames
from taxjson.lib.map_file import load_map_file
from taxjson.model import money as M


def apply_drops(transactions: List[TaxTransaction], drops) -> List[TaxTransaction]:
    """Remove transactions whose symbol is in `drops`.

    For each dropped ticker, prints a NOTE — count, net quantity, net
    amount — so the deletion is auditable rather than silent. If a
    dropped ticker's net quantity looks like a real position
    (|net| >= 1 share) a loud warning is printed, since DELETE discards
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

    def _cash(tx):
        """Signed cash of one row: a trade's net_amount is a magnitude
        with the direction in the quantity sign (a buy pays it, a sale
        receives it); income rows carry signed cash; TAX and FEE are
        positive = paid. Summing the raw net_amount added a buy's cost
        to a sale's proceeds (audit S052-23)."""
        act = str((tx.get('action') if isinstance(tx, dict)
                   else getattr(tx, 'action', '')) or '').upper()
        net = _field(tx, 'net_amount')
        if act in ('BUYSELL', 'ASSIGN'):
            return -net if _field(tx, 'quantity') > 0 else net
        if act in ('TAX', 'FEE'):
            return -net
        return net

    for sym in sorted(removed):
        rows = removed[sym]
        net_qty = sum(_field(t, 'quantity') for t in rows)
        net_cash = sum(_cash(t) for t in rows)
        emit_line(f"NOTE: ticker-map DELETE removed {len(rows)} {sym} row(s) "
            f"(net qty {net_qty:.4f}, net cash {M.shown(net_cash, 2):+.2f}).")
        if abs(net_qty) >= 1.0:
            emit_line(f"warning: dropped ticker {sym} has a net quantity of "
                f"{net_qty:.4f} — that looks like a real position, and "
                f"DELETE discards its cost basis. Remove the `DELETE "
                f"{sym}` line from ticker.map if "
                f"{sym} is not a pure artifact.")
    return kept

def generate_summary(transactions: List[TaxTransaction]) -> Dict[str, Any]:
    unique_tickers = set()
    for tx in transactions:
        if tx.symbol:
            unique_tickers.add(tx.symbol)
    
    # Only the symbol's own normalisation (a dotted option string to
    # OCC form): the summary used to "map" every .US listing to .TO
    # (a hard-coded CAD target), a rename that is not a listing in a
    # Canadian book and never applies in a US one (re-audit A2-1367).
    # Cross-listing renames are ticker.map lines the user writes.
    mappings = {}
    for ticker in sorted(unique_tickers):
        mapped = map_ticker(ticker)
        mappings[ticker] = mapped
        
    return {
        "mappings": mappings,
        "total_unique_tickers": len(mappings),
        "generated_at": datetime.now().strftime("%Y-%m-%d")
    }


def guard_option_listing_collisions(symbols, mapping: Dict[str, str],
                                    prog: str = "taxjson-ticker-map"
                                    ) -> Dict[str, str]:
    """`mapping` plus an identity rule for every OPTION the underlying
    rule would move onto a contract code the book already carries
    under that code natively (R1-16). `TOBASE ABC.US ABC.TO` renames
    ABC271217C00030000.US (a USD-strike OCC contract) to ...TO — when
    the book also trades the Montreal ABC271217C00030000.TO (a
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
            emit_line(f"{prog}: warning: the underlying rule would rename option "
                  f"{sym} to {target}, a different listed contract this book "
                  f"also trades (strike currency and clearing house differ, so "
                  f"they are not identical property) — kept separate as {sym}. "
                  f"Add an exact rule for {sym} in ticker.map if they really "
                  f"are one contract.")
    return out


def apply_mapping(tx: TaxTransaction, mapping: Dict[str, str]) -> TaxTransaction:
    mapped_symbol = map_symbol(tx.symbol, mapping)
    if mapped_symbol != tx.symbol:
        tx.symbol = mapped_symbol
    # A SPLIT-rename's TARGET must consolidate under the same identity
    # as the trades it renames INTO. Mapping only tx.symbol left e.g.
    # `ABC.US -> symbol_new=ABD.US` pointing at an orphan ABD.US pool
    # while the later ABD trades mapped to ABD.TO — the engine renamed
    # the basis into a pool no sale ever draws from (phantom short +
    # stranded ACB).
    new_sym = getattr(tx, 'symbol_new', '') or ''
    if new_sym:
        mapped_new = map_symbol(new_sym, mapping)
        if mapped_new != new_sym:
            tx.symbol_new = mapped_new
    # A warrant exercise's share target (exercise_of) names the shares
    # the rows map: left unmapped, the engine found no such shares and
    # booked the exercise as cash-settled (backlog TJ-0978).
    ex_of = getattr(tx, 'exercise_of', '') or ''
    if ex_of:
        mapped_ex = map_symbol(ex_of, mapping)
        if mapped_ex != ex_of:
            tx.exercise_of = mapped_ex
    return tx

@guard_main("taxjson-ticker-map")
def main():
    parser = argparse.ArgumentParser(
        description="Apply ticker.map's rename rules to the symbols of a "
                    "taxjson file\n(option symbols follow their "
                    "underlying), or, with no map, list its symbols.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "ticker.map keywords (one rule per line, notes after `#`):\n"
            "  renames:  GLOBAL from to | TOBASE from to | DELETE sym |\n"
            "            DISTINCT a b | RENAME from to (undated: GLOBAL)\n"
            "  legacy (dated events now go in a .tt file, date first):\n"
            "            JOURNAL from to (read as TOBASE) |\n"
            "            RENAME from to YYYY-MM-DD [late=fold|late=separate]\n"
            "  lookups (change no symbol; read by other tools):\n"
            "            QUOTE SYMBOL YAHOO_SYMBOL [QTY_RATIO]   price lookups\n"
            "            CRYPTO SYMBOL YAHOO_ID                a coin's Yahoo id\n"
            "            EXTRACT words | CURRENCY | SYMBOL     parser symbol override\n"
            "            T1135 SYMBOL COUNTRY                  T1135 domicile (ISO-3,\n"
            "                                                  or CA/EXCLUDE)\n"
            "The old yf_ticker.map, crypto_ticker.map,\n"
            "ticker_extraction_overrides.txt and t1135.map are folded in by\n"
            "`tjs migrate`.\n"
            "A TRADINGVIEW line (the removed TradingView export) is ignored."))
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
    parser.add_argument("--kind", choices=("securities", "crypto"),
                        default="",
                        help="The accounts' kind: a dated RENAME a .tt "
                             "line of a crypto account declares books "
                             "only in crypto accounts, a securities "
                             "account's only in securities accounts "
                             "(`tjs run` passes it; default: every "
                             "dated RENAME).")
    from taxjson.lib.country import country_arg
    parser.add_argument("--country", type=country_arg, default=None,
                        metavar="{canada,ca,usa,us}",
                        help="The project's country: the engine order "
                             "that decides a dated RENAME's late rows "
                             "on the rename row's day (`tjs run` "
                             "passes it).")
    args = parser.parse_args()
    if args.map_flag and args.map_file:
        # Two map sources: the positional one used to win silently
        # (audit S053-00).
        emit_line("taxjson-ticker-map: error: give the map once — either "
              "the positional MAP_FILE or --map, not both")
        sys.exit(2)
    if args.map_flag:
        args.map_file = args.map_flag

    from taxjson.lib.json_input import load_transactions_or_exit
    transactions = load_transactions_or_exit("taxjson-ticker-map",
                                             args.input)

    if not args.map_file:
        # Summary Mode
        output_data = generate_summary(transactions)
        json.dump(output_data, sys.stdout, indent=2, sort_keys=True)
        # The document's closing newline: a live exact Doc, written where
        # the print() was.
        d = _out.Doc(exact=True, width_=0, live=True)
        d.blank()
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
        # Dated RENAME lines are events (lib/renames), booked on the raw
        # symbols like taxjson-merge2 does.
        from taxjson.lib.renames import RenameConflict, apply_dated_renames
        try:
            transactions = apply_dated_renames(transactions, tmap.dated,
                                               kind=args.kind,
                                               country=args.country or "",
                                               mapping=mapping)
        except RenameConflict as e:
            emit_line(f"taxjson-ticker-map: error: {e}")
            sys.exit(1)
        mapping = guard_option_listing_collisions(
            [t.symbol for t in transactions], mapping)
        for _w in bare_target_warnings([t.symbol for t in transactions],
                                       mapping):
            emit_line(f"warning: ATTENTION: {_w}")
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
            }
        }
        json.dump(output_data, sys.stdout, indent=2, sort_keys=True)
        # The document's closing newline: a live exact Doc, written where
        # the print() was.
        d = _out.Doc(exact=True, width_=0, live=True)
        d.blank()

if __name__ == "__main__":
    main()
