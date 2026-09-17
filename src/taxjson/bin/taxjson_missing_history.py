#!/usr/bin/env python3
"""taxjson-missing-history - find stocks with missing cost-basis data.

Two ways acquisition data goes missing, both of which distort a year's gain:

  1. TRUNCATED HISTORY - the export doesn't reach back to when a position was
     opened, so the engine sees only the sale and the running share count goes
     NEGATIVE. The tell-tale sign of a missing buy.

  2. $0-COST CORP ACTION - shares received through a merger/spinoff are booked
     by the broker with value 0, so they enter the pool at a ZERO basis and
     inflate the gain when sold. These never go negative (received +N, sold
     −N), so #1 can't catch them - this is its complement.

Either way the tool splits the findings by whether they bear on a given year:

  * AFFECTS <year>   - has an in-year sale drawing on the missing basis, so it
                       distorts that year's gain. Worth fixing before you file.
  * not relevant     - the issue is confined to other years (or has since
                       resolved). Safe to ignore for <year>.

Feed it the per-account base file the pipeline already builds - the FULL
transaction history, not the year-filtered gains file:

  taxjson-missing-history --year 2025 work/margin_base.json
  taxjson-missing-history --year 2025 work/*_base.json
  taxjson-missing-history work/margin_base.json     # no year scope

To actually fix an AFFECTS row, generate a phantom-opening file and re-run:
  taxjson-gains --year <year> --suggest-phantoms phantoms.json <base.json>
  # review/prune, then: taxjson-gains --incomplete-history phantoms.json ...
"""

import argparse
import sys
from pathlib import Path

from taxjson.lib.core import load_transactions
from taxjson.lib.phantom_holdings import (
    detect_phantoms, assess_tax_year_relevance, detect_zero_basis_acquisitions,
    detect_corp_action_links,
)


def _load_all(paths):
    txs = []
    for p in paths:
        try:
            txs.extend(load_transactions(Path(p)))
        except (OSError, ValueError) as e:
            print(f"error loading {p}: {e}", file=sys.stderr)
    return txs


def _print_section(title, rows, *, show_year_cols):
    if not rows:
        return
    print(f"\n{title}")
    if show_year_cols:
        hdr = (f"{'Symbol':<24} {'Account':<10} {'Cur':<4} {'PeakShort':>12} "
               f"{'FirstNeg':<12} {'InYrSales':>10} {'InYrProceeds':>14} {'Reg'}")
    else:
        hdr = (f"{'Symbol':<24} {'Account':<10} {'Cur':<4} {'PeakShort':>12} "
               f"{'FirstNeg':<12} {'Sales<0':>10} {'EndPos':>12} {'Reg'}")
    print("-" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        c = r.candidate
        reg = "REG" if c.registered else ""
        if show_year_cols:
            print(f"{c.symbol:<24} {c.account:<10} {(c.currency or '?'):<4} "
                  f"{c.peak_short:12.4f} {c.first_negative_date:<12} "
                  f"{r.in_year_dispositions:10d} {r.in_year_proceeds:14.2f} {reg}")
        else:
            print(f"{c.symbol:<24} {c.account:<10} {(c.currency or '?'):<4} "
                  f"{c.peak_short:12.4f} {c.first_negative_date:<12} "
                  f"{r.in_year_dispositions:10d} {c.end_position:12.4f} {reg}")


def _print_zero_section(title, rows):
    if not rows:
        return
    print(f"\n{title}")
    hdr = (f"{'Symbol':<24} {'Account':<10} {'Cur':<4} {'ZeroQty':>10} "
           f"{'AcqDate':<12} {'InYrSales':>10} {'InYrProceeds':>14} {'Why'}")
    print("-" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        why = "corp action" if r.looks_corp_action else "$0 cost"
        print(f"{r.symbol:<24} {r.account:<10} {(r.currency or '?'):<4} "
              f"{r.zero_cost_qty:10.4f} {r.acquisition_date:<12} "
              f"{r.in_year_dispositions:10d} {r.in_year_proceeds:14.2f} {why}")
        if r.description:
            print(f"    └ {r.description}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", metavar="FILE",
                    help="per-account base JSON (full history), e.g. "
                         "work/<account>_base.json")
    ap.add_argument("--year", type=int, metavar="YYYY",
                    help="tax year to assess relevance against; omit to list "
                         "every short position with no year scope")
    ap.add_argument("--account", metavar="NAME",
                    help="only report this account")
    ap.add_argument("--include-options", action="store_true",
                    help="also include OCC option symbols (a negative option "
                         "position is normal sell-to-open, so skipped by default)")
    args = ap.parse_args(argv)

    txs = _load_all(args.files)
    if not txs:
        print("No transactions loaded.", file=sys.stderr)
        return 1

    # --- 0. Reconstruct split-across-symbols mergers first, so the two halves
    #        (old removed / new received) are reported as one event rather than
    #        an unrelated short + $0-basis buy. ---
    links = detect_corp_action_links(txs, include_options=args.include_options)
    if args.account:
        links = [l for l in links if l.account == args.account]
    linked_old = {(l.old_symbol, l.account) for l in links}
    linked_new = {(l.new_symbol, l.account) for l in links}

    # --- 1. Truncated history (positions go negative) ---
    candidates = detect_phantoms(txs, include_options=args.include_options)
    if args.account:
        candidates = [c for c in candidates if c.account == args.account]
    short_rows = [r for r in assess_tax_year_relevance(txs, candidates, args.year)
                  if (r.candidate.symbol, r.candidate.account) not in linked_old]

    # --- 2. $0-cost corp-action acquisitions later sold ---
    zero_rows = [r for r in detect_zero_basis_acquisitions(
                    txs, args.year, include_options=args.include_options)
                 if (r.symbol, r.account) not in linked_new
                 and (not args.account or r.account == args.account)]

    if not short_rows and not zero_rows and not links:
        scope = f" (account {args.account})" if args.account else ""
        print(f"No missing-cost-basis issues found{scope}: no negative "
              "holdings, no $0-cost corp-action shares sold, no mergers.")
        return 0

    yr = args.year

    # === Section 0: reconstructed mergers ===
    if links:
        print(f"\n## Reconstructed mergers (old symbol -> new symbol): "
              f"{len(links)} event(s)")
        for l in links:
            ratio = f", ratio {l.ratio:g} new=1 old" if l.ratio else ""
            print(f"  {l.date} [{l.account}]  {l.old_symbol} "
                  f"({l.old_company or '?'}) -> {l.new_symbol} "
                  f"({l.new_company or '?'}){ratio}")
            print(f"      {l.old_qty:g} {l.old_symbol} removed; "
                  f"{l.new_qty:g} {l.new_symbol} received at $0 basis.")
            print(f"      The old shares' ACB is missing (their purchase isn't "
                  f"in your data). Supply it so {l.new_symbol} carries the "
                  f"correct basis - otherwise {l.new_symbol}'s sale gain is "
                  f"overstated by that amount.")
    # === Section 1: truncated history ===
    if short_rows:
        print(f"\n## Truncated history - positions go short (missing a buy): "
              f"{len(short_rows)} pair(s)")
        if yr:
            affects = [r for r in short_rows if r.affects_year]
            ignorable = [r for r in short_rows if not r.affects_year]
            print(f"   {len(affects)} affect tax year {yr}; {len(ignorable)} do not.")
            _print_section(f"AFFECTS {yr} - missing basis distorts this year's "
                           "gain; fix before filing:", affects, show_year_cols=True)
            _print_section(f"NOT relevant to {yr} - short only from other-year "
                           "sales (or since drained); safe to ignore:",
                           ignorable, show_year_cols=True)
        else:
            _print_section("Short positions (all history):", short_rows,
                           show_year_cols=False)

    # === Section 2: $0-cost corp-action acquisitions ===
    if zero_rows:
        print(f"\n## $0-cost corp-action shares that were later sold "
              f"(inflated gain): {len(zero_rows)} pair(s)")
        rel = [r for r in zero_rows if r.affects_year] if yr else zero_rows
        irr = [r for r in zero_rows if not r.affects_year] if yr else []
        if yr:
            print(f"   {len(rel)} affect tax year {yr}; {len(irr)} do not.")
        _print_zero_section(
            (f"AFFECTS {yr} - sold this year against a $0 basis; the gain is "
             "overstated by the missing basis:") if yr
            else "Acquired at $0 cost and sold (all history):", rel)
        if irr:
            _print_zero_section(
                f"NOT relevant to {yr} - sold in other years; safe to ignore:",
                irr)

    if (yr and (any(r.affects_year for r in short_rows)
                or any(r.affects_year for r in zero_rows))):
        print(f"\nTo fix truncated history: taxjson-gains --year {yr} "
              "--suggest-phantoms phantoms.json <base.json>, review/prune, then "
              "re-run with --incomplete-history phantoms.json.\nTo fix a $0-cost "
              "corp action: declare it (merger/spinoff basis) so the received "
              "shares carry the correct ACB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
