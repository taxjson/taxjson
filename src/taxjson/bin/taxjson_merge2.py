#!/usr/bin/env python3
"""
taxjson_merge2.py

Combined pipeline tool. Replaces a chain like:

    taxjson-merge a.json b.json | taxjson-sort --dedup |
        taxjson-validate | taxjson-ticker-map - ticker.map |
        taxjson-convert-currency --to CAD --rates to_cad.csv

with a single invocation:

    taxjson-merge2 \\
        --sort --dedup --validate \\
        --map ticker.map \\
        --to CAD --rates to_cad.csv \\
        a.json b.json > a.base.json

Each stage runs only when its flag is set (or its input file is given),
in this fixed order: merge → sort/dedup → ticker-map → convert-currency →
validate (last so it sees the final shape that downstream tools will).
The pipeline is best-effort on validate: errors are printed to stderr but
don't halt the run, mirroring `taxjson-validate`'s own behaviour. Pass
`--validate-strict` to make any validation error fail the command.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from taxjson.lib.cli_diag import guard_main
from taxjson.lib.core import (
    TaxTransaction, coerce_transaction_row, strip_json_comments,
)
from taxjson.bin.taxjson_sort import (deduplicate, sort_transactions,
                                      source_accounts_of)
from taxjson.bin.taxjson_ticker_map import (
    load_map_file, apply_mapping, apply_drops, merge_renames,
    bare_target_warnings,
    guard_option_listing_collisions,
)
from taxjson.lib.country import country_arg
from taxjson.bin.taxjson_convert_currency import (
    abort_if_currency_uncovered, emit_fallback_summary,
    emit_source_summary, fallback_validation_issues, load_rate_sources,
    load_exchange_rates, process_transactions as convert_transactions,
    positive_rate, reset_fallback_tally, resolve_default_rate,
)
from taxjson.bin.taxjson_validate import validate_transactions as _validate_dict_list
from taxjson.lib.corporate_timeline import normalize_symbol_new
from taxjson.lib.trade_cancel import pair_cancellations


def warn_duplicate_splits(txs, conflicts=None) -> int:
    """Say so when ONE account carries the same split twice — typically a
    broker parser that now books the event itself AND a manual .tt SPLIT
    line written as a workaround before it did. Identical rows (same
    symbol, date, ratio and rename target) are applied ONCE by the engines'
    split dedup, so this is a cleanup warning; rows for the same event with
    DIFFERENT ratios are all applied and would scale the pool twice.
    Copies whose ratios differ only by rounding (2.333333 next to
    2.333333333), on the same date or within the split-date window, are
    one event applied once — said as ATTENTION, which `taxjson run`
    echoes to the console (A2-0070).
    Rows with ratios that really differ are a validation ERROR when the
    caller passes a `conflicts` list (one (context, message) per event):
    one of them is false, so `run --strict` stops, the console says so
    and checklist run-clean is not done (A2-0040).
    Returns the number of duplicated events."""
    from taxjson.lib.corporate_timeline import (SPLIT_DATE_WINDOW_DAYS,
                                                split_ratios_close)
    groups = {}
    for t in txs:
        if t.action != 'SPLIT':
            continue
        key = (t.account, t.symbol, t.date,
               normalize_symbol_new(t.symbol, t.symbol_new))
        groups.setdefault(key, []).append(t)
    n = 0
    for (acct, sym, date, new), rows in sorted(groups.items()):
        if len(rows) < 2:
            continue
        n += 1
        ratios = sorted({round(float(r.quantity or 0), 9) for r in rows})
        tgt = f" -> {new}" if new else ""
        if len(ratios) == 1:
            print(f"warning: duplicate split: {sym}{tgt} x{ratios[0]:g} on "
                  f"{date} appears {len(rows)} times in account {acct} "
                  f"(e.g. the broker parser books it AND a manual .tt SPLIT "
                  f"line) — applied ONCE; delete the manual line.",
                  file=sys.stderr)
        elif all(split_ratios_close(r, ratios[0]) for r in ratios):
            print(f"warning: ATTENTION: split: {sym}{tgt} on {date} appears "
                  f"{len(rows)} times in account {acct} with ratios "
                  f"{ratios} that differ only by rounding — one event, "
                  f"applied ONCE; delete the manual .tt SPLIT line.",
                  file=sys.stderr)
        else:
            prod = 1.0
            for r in ratios:
                prod *= r
            msg = (f"conflicting splits: {sym}{tgt} on {date} in "
                   f"account {acct} has {len(rows)} SPLIT rows with "
                   f"different ratios {ratios} — EACH is applied "
                   f"(x{prod:g} in total); keep only the right one.")
            # ATTENTION + "split: " — `taxjson run` echoes it.
            print(f"warning: ATTENTION: split: {msg}", file=sys.stderr)
            if conflicts is not None:
                conflicts.append((f"SPLIT {sym}{tgt} {date} ({acct})", msg))
    # The same event booked on two dates a few days apart (a .tt line on
    # the ex-date next to the broker's row): applied once, on the earlier
    # date, by the engines' split dedup.
    by_sec = {}
    for (acct, sym, date, new) in groups:
        by_sec.setdefault((acct, sym, new), []).append(date)
    for (acct, sym, new), dates in sorted(by_sec.items()):
        dates.sort()
        for i, d1 in enumerate(dates):
            for d2 in dates[i + 1:]:
                try:
                    gap = (datetime.strptime(d2, "%Y-%m-%d")
                           - datetime.strptime(d1, "%Y-%m-%d")).days
                except ValueError:
                    continue
                if gap > SPLIT_DATE_WINDOW_DAYS:
                    break
                r1 = [float(r.quantity or 0) for r in groups[(acct, sym, d1, new)]]
                r2 = [float(r.quantity or 0) for r in groups[(acct, sym, d2, new)]]
                pair = next(((a, b) for a in r1 for b in r2
                             if split_ratios_close(a, b)), None)
                if pair is None:
                    continue
                n += 1
                tgt = f" -> {new}" if new else ""
                exact = round(pair[0], 9) == round(pair[1], 9)
                head = ("warning: duplicate split:" if exact
                        else "warning: ATTENTION: split:")
                print(f"{head} {sym}{tgt} is booked on {d1} (x{pair[0]:.9g}) "
                      f"and {d2} (x{pair[1]:.9g}) in account {acct} — one "
                      f"event, applied ONCE on {d1}; delete the manual .tt "
                      f"SPLIT line.", file=sys.stderr)
    return n


def _ratio_digits(r: float) -> int:
    return len(f"{float(r):.15g}".replace("-", "").replace(".", ""))


def canonicalize_split_ratios(txs) -> int:
    """Copies of one split in one account whose ratios differ only by
    rounding (the broker's 2.333333333 and a hand-typed 2.333333) take
    the most precise ratio, so the engines' split dedup sees ONE event
    and no 0.0001-share residue is left by the rounded copy (A2-0070).
    Mutates the SPLIT rows in place; returns the number changed."""
    from datetime import datetime as _dt
    from taxjson.lib.corporate_timeline import (SPLIT_DATE_WINDOW_DAYS,
                                                split_ratios_close)
    by_sec = {}
    for t in txs:
        if t.action == 'SPLIT' and float(t.quantity or 0):
            by_sec.setdefault((t.account, t.symbol, normalize_symbol_new(
                t.symbol, t.symbol_new)), []).append(t)
    changed = 0
    for rows in by_sec.values():
        if len(rows) < 2:
            continue
        for a in rows:
            for b in rows:
                if a is b or not split_ratios_close(a.quantity, b.quantity):
                    continue
                try:
                    gap = abs((_dt.strptime(a.date[:10], "%Y-%m-%d")
                               - _dt.strptime(b.date[:10], "%Y-%m-%d")).days)
                except (TypeError, ValueError):
                    continue
                if gap > SPLIT_DATE_WINDOW_DAYS:
                    continue
                if _ratio_digits(b.quantity) > _ratio_digits(a.quantity):
                    a.quantity = float(b.quantity)
                    changed += 1
    return changed


def _pairing_core(description: str) -> str:
    """Normalized core of a dividend/withholding description, used to pair
    a DIVIDEND with its withholding TAX row.

    A broker writes the same event two ways — "<TICKER>(<ISIN>) Cash
    Dividend USD 0.555 per Share (Ordinary Dividend)" for the dividend and
    "... Cash Dividend USD 0.555 per Share - US Tax" for the withholding.
    Stripping the ISIN paren, the trailing " - <Tax...>" suffix and the
    trailing " (<qualifier>)" leaves a shared core, so a regular dividend
    and a Payment-in-Lieu on the same ticker/date don't cross-match.
    """
    s = description or ''
    s = re.sub(r'\([A-Z0-9]+\)', '', s, count=1)      # drop "(ISIN)"
    s = re.split(r'\s+-\s+', s)[0]                     # drop " - US Tax"
    s = re.sub(r'\s*\([^)]*\)\s*$', '', s)             # drop "(Ordinary Dividend)"
    return ' '.join(s.split()).upper()


def reconcile_dividend_tax(txs):
    """Pair each DIVIDEND with its withholding TAX so both records are
    self-describing. Runs post-dedup, so every dividend and every
    withholding row appears exactly once.

      - DIVIDEND.net_amount becomes gross_amount minus the matched
        withholding. A dividend with no withholding row (typically a
        Canadian .TO holding) keeps net_amount equal to gross_amount.
      - each matched TAX row inherits the dividend's share count and a
        per-share withholding rate in `price`.

    Matching is on (symbol, date, description-core), so a regular
    dividend and a same-day Payment-in-Lieu reconcile independently.
    Totals are unaffected: downstream tools read gross_amount for
    dividends and the TAX rows' net_amount — never DIVIDEND.net_amount.
    """
    divs = defaultdict(list)
    taxes = defaultdict(list)
    for t in txs:
        action = (getattr(t, 'action', '') or '').upper()
        key = (getattr(t, 'symbol', None), getattr(t, 'date', None),
               _pairing_core(getattr(t, 'description', '') or ''))
        if action == 'DIVIDEND':
            divs[key].append(t)
        elif action == 'TAX' or (getattr(t, 'type', '') or '').lower() == 'tax':
            taxes[key].append(t)

    for key, dlist in divs.items():
        tlist = taxes.get(key)
        if not tlist:
            continue
        # Signed sum: TAX rows are positive = withheld (parsers enforce it);
        # a refund/reversal arrives NEGATIVE and must NET against the charge
        # rather than add to it — abs() understated DIVIDEND.net_amount on
        # any charge+refund pair.
        total_tax = sum(getattr(t, 'net_amount', 0.0) or 0.0 for t in tlist)
        total_gross = sum(getattr(d, 'gross_amount', 0.0) or 0.0 for d in dlist)
        if total_gross <= 0:
            continue
        # Normally a 1:1 pairing; if a payment is split across rows,
        # apportion the withholding pro-rata by gross.
        for d in dlist:
            g = getattr(d, 'gross_amount', 0.0) or 0.0
            d.net_amount = round(g - total_tax * (g / total_gross), 8)
        total_qty = sum(getattr(d, 'quantity', 0.0) or 0.0 for d in dlist)
        if total_qty > 0:
            for t in tlist:
                t.quantity = total_qty
                # Signed: a refund row keeps its negative per-share rate.
                t.price = round((getattr(t, 'net_amount', 0.0) or 0.0)
                                / total_qty, 8)


def cancel_trade_pairs(txs):
    """Drop each marked trade cancellation (IB `Ca`) with the original it
    reverses; warn about one whose original is in none of the inputs (it
    stays booked as a reversing trade)."""
    partials: list = []
    kept, pairs, unmatched = pair_cancellations(txs, partials=partials)
    for orig, ca, red in partials:
        print(f"note: the broker cancelled (Ca) {-ca.quantity:g} of the "
              f"{orig.symbol} order of {orig.quantity:g} @ {orig.price:g} "
              f"on {orig.date} (one execution); the order is booked as "
              f"{red.quantity:g}.", file=sys.stderr)
    for orig, _ca in pairs:
        print(f"note: dropped the {orig.symbol} trade of "
              f"{orig.quantity:g} @ {orig.price:g} on {orig.date} and "
              f"its broker cancellation (Ca) from another statement.",
              file=sys.stderr)
    for ca in unmatched:
        print(f"warning: {ca.symbol}: the broker cancelled (Ca) a trade "
              f"of {-ca.quantity:g} @ {ca.price:g} on {ca.date}, but the "
              f"original fill is in none of this account's inputs — the "
              f"cancellation stays booked as a reversing trade. Add the "
              f"statement holding the original (or remove both rows by "
              f"hand) so the pair nets out.", file=sys.stderr)
    return kept


def _load_json_files(paths, require_inputs=False):
    """Read transactions from each path and concatenate. Missing files
    warn-and-skip (same behaviour as the legacy taxjson-merge) so a
    stale shell script with a long file list doesn't fail wholesale on
    the first missing optional input.

    When `require_inputs` is True (the orchestrated pipeline passes it),
    a missing file is FATAL too — those inputs are required, and a
    silent skip would emit a partial merge and understate gains.

    An EXISTING file that cannot be read (malformed JSON, no
    'transactions' list, a row the shared loader refuses) is always
    fatal: the old warn-and-skip turned a `#`-commented input into
    "error reading ... skipping" and an EMPTY merge at exit 0
    (stage-tools audit). Rows go through core.coerce_transaction_row —
    the same funnel as load_transactions — so `#` comments, the
    qty→quantity alias and the type guards behave identically here.
    Returns (TaxTransaction list, sources)."""
    merged = []
    sources = []
    had_error = False
    fatal = False
    for p in paths:
        path = Path(p)
        if not path.exists():
            label = 'error' if require_inputs else 'warning'
            tail = '' if require_inputs else ', skipping'
            print(f"{label}: {p} not found{tail}", file=sys.stderr)
            had_error = True
            continue
        try:
            # utf-8-sig: a BOM is dropped (re-audit A2-1412).
            with path.open('r', encoding='utf-8-sig') as f:
                data = json.loads(strip_json_comments(f.read()))
        except Exception as e:
            print(f"error: reading {p}: {e}", file=sys.stderr)
            had_error = fatal = True
            continue
        txs = data.get('transactions', data) if isinstance(data, dict) else data
        if not isinstance(txs, list):
            print(f"error: {p} has no 'transactions' list", file=sys.stderr)
            had_error = fatal = True
            continue
        try:
            txs = [coerce_transaction_row(t, i, f"taxjson-merge2({p})")
                   for i, t in enumerate(txs)]
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            had_error = fatal = True
            continue
        merged.extend(txs)
        # Preserve each source's own metadata block — the legacy
        # taxjson-merge does this and downstream consumers (e.g. tools
        # surfacing the original brokerage id) rely on it. Dropping it
        # in merge2 silently breaks those callers.
        src_meta = data.get('metadata') if isinstance(data, dict) else None
        sources.append({
            'file': str(path),
            'count': len(txs),
            **({'original_metadata': src_meta} if src_meta else {}),
        })
    if require_inputs and had_error:
        print("error: --require-inputs is set and one or more inputs were "
              "missing or unreadable; refusing to emit a partial merge.",
              file=sys.stderr)
        sys.exit(2)                 # an unreadable input (A2-0164)
    if fatal:
        print("error: one or more inputs exist but could not be read; "
              "refusing to emit a partial merge.", file=sys.stderr)
        sys.exit(2)
    if paths and not sources:
        # Every input was missing: an empty merge at exit 0 is the
        # silent-empty-merge hazard in another costume.
        print("error: none of the input files could be read; refusing "
              "to emit an empty merge.", file=sys.stderr)
        sys.exit(2)
    return merged, sources


def _to_tax_transactions(items):
    """Coerce rows into TaxTransaction through the shared loader funnel
    (core.coerce_transaction_row): `qty` alias, known-field filtering
    and the type/finite/date guards. Already-TaxTransaction items pass
    through. Raises ValueError on a malformed row."""
    return [coerce_transaction_row(item, i, "taxjson-merge2")
            for i, item in enumerate(items)]


@guard_main("taxjson-merge2")
def main():
    parser = argparse.ArgumentParser(
        description=(
            "Combined pipeline: merge, sort/dedup, ticker-map, "
            "currency-convert, and validate in one pass."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        'files', nargs='+',
        help="Input JSON files (taxjson schema). Concatenated in order.",
    )
    parser.add_argument(
        '--sort', action='store_true',
        help="Sort merged transactions chronologically by (date, time).",
    )
    parser.add_argument(
        '--dedup', action='store_true',
        help=(
            "Remove duplicate transactions by id (or synthetic UID if no "
            "id is set). Implies --sort."
        ),
    )
    parser.add_argument(
        '--validate', action='store_true',
        help=(
            "Run validation; print issues to stderr after the final stage. "
            "Output is still emitted unless --validate-strict is also set."
        ),
    )
    parser.add_argument(
        '--validate-strict', action='store_true',
        help="Like --validate but exit non-zero if any errors are found.",
    )
    parser.add_argument(
        '--map', dest='map_file', metavar='FILE',
        help="Apply ticker mappings from a `ticker.map` file: one rule "
             "per line, `GLOBAL|TOBASE|JOURNAL FROM TO`, `DELETE SYM` or "
             "`DISTINCT A B`, '#' comments (a line without a keyword is "
             "refused).",
    )
    parser.add_argument(
        '--to', dest='target_currency', metavar='CURR',
        help="Convert all amounts to this target currency (e.g. CAD).",
    )
    parser.add_argument(
        '--rates', dest='rates_file', metavar='FILE',
        help="Historical exchange-rates file consumed by --to.",
    )
    parser.add_argument(
        '--country', type=country_arg, default=None,
        metavar='{canada,ca,usa,us}',
        help="The project's country: required with --to when the book "
             "has futures contracts (their settled P/L follows the "
             "country's lot rule: average cost in Canada, FIFO in the US).",
    )
    parser.add_argument(
        '--default-rate', type=positive_rate, default=None,
        help="Fallback rate when the rates file is missing a date "
             "(default: 1.35). Passing it explicitly also allows a "
             "currency that is entirely absent from --rates to convert "
             "at this rate; without it that is a fatal error.",
    )
    parser.add_argument(
        '--require-inputs', action='store_true',
        help="Fail if any input file is missing or unreadable, instead of "
             "warn-and-skip. Use in orchestrated pipelines where every input "
             "is required — a silent skip emits a partial merge and "
             "understates gains.",
    )
    args = parser.parse_args()

    if args.rates_file and not args.target_currency:
        parser.error("--rates requires --to to also be set")

    # --- Stage 1: merge ------------------------------------------------
    # _load_json_files already coerces every row to TaxTransaction via
    # the shared loader funnel, so the later stages see one shape.
    txs, sources = _load_json_files(args.files, require_inputs=args.require_inputs)

    # --- Stage 2: sort / dedup ----------------------------------------
    if args.sort or args.dedup:
        txs = sort_transactions(txs)
    if args.dedup:
        txs, dropped = deduplicate(
            txs, return_dropped=True, report=sys.stderr,
            source_accounts=source_accounts_of(
                s.get('original_metadata') for s in sources))
        if dropped:
            print(
                f"note: --dedup removed {len(dropped)} duplicate row(s):",
                file=sys.stderr,
            )
            # One line per dropped row so the user can audit which
            # broker/account/date combinations are colliding. Useful
            # when the count is surprising (e.g. an expected 8 grows
            # to 80 because one parser stopped emitting unique ids).
            for tx in dropped:
                date = getattr(tx, 'date', '?')
                time = getattr(tx, 'time', '') or ''
                action = getattr(tx, 'action', '?')
                symbol = getattr(tx, 'symbol', '?')
                qty = getattr(tx, 'quantity', 0) or 0
                price = getattr(tx, 'price', 0) or 0
                net = getattr(tx, 'net_amount', 0) or 0
                account = getattr(tx, 'account', 'default')
                tx_id = getattr(tx, 'id', '') or ''
                print(
                    f"  {date} {time:<8} {action:<10} {symbol:<16} "
                    f"qty={qty:>10} price={price:>10} net={net:>12} "
                    f"account={account} id={tx_id}"
                    + (f" source={tx.source}" if getattr(tx, 'source', '')
                       else ""),
                    file=sys.stderr,
                )

    # --- Stage 2b: broker trade cancellations -------------------------
    # An IB `Ca` row whose original fill sits in ANOTHER statement of the
    # account (a 2026 statement cancelling a 2025 fill) survives the
    # parse as a marked cancellation leg; drop it with its original here
    # (lib/trade_cancel, audit R1-51). Always on: a no-op without marks.
    txs = cancel_trade_pairs(txs)

    # --- Stage 3: ticker-map ------------------------------------------
    if args.map_file:
        tmap = load_map_file(Path(args.map_file))
        # GLOBAL renames always apply; TOBASE/JOURNAL apply only when
        # converting to base (--to) — pre-conversion they'd merge
        # different-currency legs into one ACB pool.
        renames = merge_renames(tmap, to_base=bool(args.target_currency))
        # DELETE first, then rename — the same order as the standalone
        # taxjson-ticker-map (the crypto path). A DELETE line names the
        # broker's RAW symbol; mapping first meant `GLOBAL FOO BAR` +
        # `DELETE FOO` deleted nothing here while the crypto path
        # deleted FOO, so one map file meant two things (stage-tools
        # audit). DELETE lines nuke a ticker's transactions (audited).
        txs = apply_drops(txs, tmap.delete)
        # Dated RENAME lines are EVENTS (lib/renames): a SPLIT row on the
        # date in every account holding the old symbol, and `late=fold`
        # rows re-booked under the new one — on the RAW symbols, before
        # the undated renames below map both (A2-0197).
        from taxjson.lib.renames import RenameConflict, apply_dated_renames
        try:
            txs = apply_dated_renames(txs, tmap.dated)
        except RenameConflict as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        # An option the underlying rule would move onto a contract the
        # book already trades natively keeps its own symbol (R1-16).
        renames = guard_option_listing_collisions(
            [t.symbol for t in txs], renames, prog="taxjson-merge2")
        # A rename to a bare symbol in a listed book (A2-0304).
        for _w in bare_target_warnings([t.symbol for t in txs], renames):
            print(f"warning: ATTENTION: {_w}", file=sys.stderr)
        # apply_mapping mutates and returns the same tx; that's fine here
        # because we built fresh TaxTransaction instances above.
        txs = [apply_mapping(t, renames) for t in txs]

    # Post-mapping (a DELETE'd or renamed row is judged as the engine will
    # see it): one account carrying the same split twice.
    split_conflicts = []
    warn_duplicate_splits(txs, split_conflicts)
    canonicalize_split_ratios(txs)

    # --- Stage 4: currency conversion ---------------------------------
    target_currency = (args.target_currency or '').upper() or None
    fx_issues = {}
    if target_currency:
        if not args.rates_file:
            # Mirror the standalone CLI's loud warning — running merge2's
            # --to without --rates would otherwise stamp every cross-
            # currency row at --default-rate with no signal.
            print(
                f"warning: --to {target_currency} given without --rates; "
                f"every cross-currency row will be converted with the "
                f"hardcoded --default-rate ({resolve_default_rate(args.default_rate)}). Pass "
                f"--rates rates.csv to use real historical rates.",
                file=sys.stderr,
            )
        # Per-run reset so a long-lived process doesn't accumulate stale
        # counts. process_transactions appends to the module-level tally;
        # emit_fallback_summary reads it out after we're done.
        reset_fallback_tally()
        try:
            history = load_exchange_rates(
                Path(args.rates_file) if args.rates_file else None,
                target_curr=target_currency,
            )
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        default_rate = Decimal(str(resolve_default_rate(args.default_rate)))
        try:
            txs = convert_transactions(txs, target_currency, history,
                                       default_rate, country=args.country)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        emit_fallback_summary(default_rate)
        if abort_if_currency_uncovered(
                rates_given=bool(args.rates_file),
                default_rate_explicit=args.default_rate is not None):
            return 1
        emit_source_summary(load_rate_sources(
            Path(args.rates_file) if args.rates_file else None,
            target_currency))
        if args.default_rate is None:
            # Each default-rate row is a validation ERROR (folded into
            # Stage 5's count) unless --default-rate was explicit.
            fx_issues = fallback_validation_issues(target_currency,
                                                   default_rate)
        # Post-conversion INVARIANT: after --to, every row must carry
        # the target currency. A field-conversion failure deliberately
        # leaves the row native (loud stderr) — but letting it flow on
        # meant `taxjson sum` flattened a native amount into a
        # base-labeled total and t1135 tested the CAD threshold with a
        # USD number. Residual native rows now FAIL the stage under
        # --validate/--validate-strict (the pipeline passes
        # --validate), and always warn with a count otherwise.
        residual = [t for t in txs
                    if (t.currency or '').upper() not in ('', target_currency)]
        if residual:
            sample = ", ".join(
                f"{t.symbol} {t.date} ({t.currency})"
                for t in residual[:5])
            more = ("" if len(residual) <= 5
                    else f" (+{len(residual) - 5} more)")
            msg = (f"{len(residual)} row(s) still carry a non-"
                   f"{target_currency} currency after conversion: "
                   f"{sample}{more} — their amounts would silently "
                   f"blend into {target_currency}-labeled totals "
                   f"(sum, t1135 threshold). Fix the rates file "
                   f"coverage and re-run.")
            if args.validate or args.validate_strict:
                print(f"error: {msg}", file=sys.stderr)
                return 1
            print(f"warning: {msg}", file=sys.stderr)

    # --- Stage 4.5: reconcile dividends with their withholding --------
    # Post-dedup and post-conversion: every dividend and withholding row
    # is unique and in the target currency, so pairing is unambiguous.
    reconcile_dividend_tax(txs)

    # --- Stage 5: validate --------------------------------------------
    error_count = 0
    if args.validate or args.validate_strict:
        # taxjson-validate's validator works on plain dicts and prints to
        # stdout; here we want stderr-only so the JSON pipe stays clean.
        # Adapt by capturing its output via the same defaultdict path.
        dict_txs = [t.to_dict() for t in txs]
        issues, warnings = _validate_dict_list(dict_txs, filename='<merged>')
        for _ctx, _errs in fx_issues.items():
            issues[_ctx].extend(_errs)
        for _ctx, _msg in split_conflicts:
            issues[_ctx].append(_msg)
        error_count = sum(len(v) for v in issues.values())
        if error_count:
            print(
                f"validation: {error_count} error(s) across {len(issues)} transaction(s):",
                file=sys.stderr,
            )
            for ctx, errs in sorted(issues.items()):
                for err in errs:
                    print(f"  {ctx}: {err}", file=sys.stderr)
        else:
            # Positive confirmation so the diagnostics section of a .sum
            # report records that validation actually ran and passed.
            print(
                f"OK: <merged>: {len(txs)} transactions validated.",
                file=sys.stderr,
            )

    # --- Emit ----------------------------------------------------------
    # In --validate-strict mode we bail BEFORE writing stdout when there
    # are errors. The old order (write, then exit 1) let shell redirects
    # capture bad JSON alongside the nonzero exit, so a CI step that
    # only checks `$?` would happily publish the file. Refusing to emit
    # makes "broken output" and "nonzero exit" the same observable.
    if args.validate_strict and error_count:
        print(
            f"error: --validate-strict aborted output emit due to "
            f"{error_count} validation error(s) above.",
            file=sys.stderr,
        )
        sys.exit(1)

    output = {
        'transactions': [t.to_dict() for t in txs],
        'metadata': {
            'merged_at': datetime.now(timezone.utc).isoformat(),
            'sources': sources,
            'stages': [
                stage for stage, flag in [
                    ('sort', args.sort or args.dedup),
                    ('dedup', args.dedup),
                    ('ticker_map', bool(args.map_file)),
                    ('convert_currency', bool(target_currency)),
                    ('validate', args.validate or args.validate_strict),
                ] if flag
            ],
            **({'target_currency': target_currency} if target_currency else {}),
        },
    }
    json.dump(output, sys.stdout, indent=2, sort_keys=True)
    print()


if __name__ == '__main__':
    sys.exit(main())
