#!/usr/bin/env python3
"""
taxjson_diff.py

Compare two taxjson-format JSON files (transactions or gains output) and
report ADDED / REMOVED / MODIFIED records. Useful for verifying parser
changes (e.g. re-parsing after a fee-handling fix) and for auditing what
moved between two runs of taxjson-gains.

Records are matched by a composite key — not by `id` — because tx ids are
hashes that include the fields you're often trying to change (commission,
fee, account, net_amount). Pure id matching would miss "same trade,
different parse." The default match key is:

    action, date, time, symbol, quantity, price, account

If multiple records share that key on either side (true duplicates), the
identical ones are paired first, then the rest in a stable order; the
unmatched surplus is reported as ADDED or REMOVED. A pure re-ordering of
the same book reports no change.

Examples:
    taxjson-diff before.json after.json
    taxjson-diff --summary before.json after.json
    taxjson-diff --by action,date,symbol,quantity old.json new.json
    taxjson-diff --ignore id,description --limit 20 a.json b.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

from taxjson.lib.cli_diag import guard_main
from taxjson.lib.report_model import load_report_json
from typing import Any, Dict, List, Tuple


COLORS = {
    'add':    '\033[1;32m',  # bold green
    'remove': '\033[1;31m',  # bold red
    'mod':    '\033[1;33m',  # bold yellow
    'header': '\033[1;36m',  # bold cyan
    'dim':    '\033[2m',
    'reset':  '\033[0m',
}


def colorize(text: str, key: str, use_color: bool) -> str:
    if not use_color:
        return text
    return f"{COLORS[key]}{text}{COLORS['reset']}"


def load_json(path: Path) -> Dict[str, Any]:
    # Comment-stripping loader — shared report-layer implementation.
    return load_report_json(path)


_MANUAL = 'manual_reporting_required'
_DEFAULT_BY = "action,date,time,symbol,quantity,price,account"


def extract_records(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Both transactions JSON and taxjson-gains output use the same top-level
    # key. Be defensive: also accept a bare list.
    if isinstance(doc, list):
        return doc
    recs = list(doc.get('transactions', []) or [])
    # The pipeline moves unknown-cost dispositions OUT of transactions
    # into manual_reporting_required; a diff that read only transactions
    # said "no change" when such a hand-reported disposition appeared,
    # vanished or moved (2026-09 audit S029-16). They are compared too,
    # matched only against each other (the section is part of the key).
    for r in doc.get(_MANUAL) or []:
        if isinstance(r, dict):
            recs.append({**r, '_section': _MANUAL})
    return recs


def _key_float(v: float) -> float:
    """Float noise off a key value: 6 decimals for ordinary magnitudes,
    7 significant digits below 1 — a sub-micro crypto quantity
    (9.4e-7 vs 5.6e-7) rounded to 6 dp collapsed to 0.000001 and a real
    change read as 'unchanged' (audit S029-18)."""
    if v != 0 and abs(v) < 1:
        return float(f"{v:.7g}")
    return round(v, 6)


def make_key(rec: Dict[str, Any], fields: List[str]) -> Tuple:
    """Composite match key. Floats get rounded so 100.00000001 == 100.00.
    The record's section (transactions vs manual_reporting_required)
    always leads the key."""
    parts = [rec.get('_section') if isinstance(rec, dict) else None]
    for f in fields:
        v = rec.get(f)
        if isinstance(v, float):
            parts.append(_key_float(v))
        else:
            parts.append(v)
    return tuple(parts)


def values_equal(a: Any, b: Any, tol: float = 1e-6) -> bool:
    """Floats are equal when they differ by less than `tol` absolute AND
    relative — the absolute floor alone called 9.4e-7 and 5.6e-7 equal
    (S029-18)."""
    if isinstance(a, float) or isinstance(b, float):
        try:
            fa, fb = float(a or 0), float(b or 0)
        except (TypeError, ValueError):
            return a == b
        d = abs(fa - fb)
        return d < tol and d <= tol * max(abs(fa), abs(fb), 1e-9)
    # Treat None and '' as equal (common when one parser sets a default).
    if (a is None or a == '') and (b is None or b == ''):
        return True
    return a == b


def fmt_val(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4f}"
    if v is None:
        return ""
    return str(v)


def field_diffs(old: Dict[str, Any], new: Dict[str, Any], ignore: set) -> List[Tuple[str, Any, Any]]:
    """Per-field diffs between two matched records. Returns [(field, old, new), ...]."""
    keys = (set(old.keys()) | set(new.keys())) - ignore
    diffs = []
    for k in sorted(keys):
        ov = old.get(k)
        nv = new.get(k)
        if not values_equal(ov, nv):
            diffs.append((k, ov, nv))
    return diffs


def short_label(rec: Dict[str, Any]) -> str:
    """One-line identifier used in headers. Order chosen for human scanning."""
    parts = []
    for k in ('action', 'date', 'symbol', 'quantity', 'qty', 'price', 'account'):
        if k in rec and rec[k] not in (None, ''):
            parts.append(f"{k}={fmt_val(rec[k])}")
    label = ("  ".join(parts) if parts
             else f"id={(rec.get('id') or '')[:10]}")
    if rec.get('_section'):
        label = f"[{rec['_section']}]  {label}"
    return label


def parse_args():
    parser = argparse.ArgumentParser(
        description="Diff two taxjson JSON files (transactions or gains output).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("old", help="Path to the OLD file (left side of diff)")
    parser.add_argument("new", help="Path to the NEW file (right side of diff)")
    parser.add_argument(
        "--by",
        default=None,
        help="Comma-separated fields to use as the match key (default: "
             f"{_DEFAULT_BY}). A field no record carries is refused.",
    )
    parser.add_argument(
        "--ignore",
        default="id",
        help=(
            "Comma-separated fields to skip when comparing matched records. "
            "Default 'id' (it's a hash of the others and would just echo the "
            "real change)."
        ),
    )
    parser.add_argument("--summary", action="store_true", help="Counts only, no per-record details.")
    parser.add_argument("--limit", type=int, default=0, help="Show at most N records per section (0 = unlimited).")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors.")
    parser.add_argument(
        "--show-unchanged",
        action="store_true",
        help="Also list records that matched with no field-level differences.",
    )
    return parser.parse_args()


@guard_main("taxjson-diff")
def main():
    args = parse_args()
    explicit_by = args.by is not None
    match_fields = [f.strip() for f in (args.by or _DEFAULT_BY).split(',')
                    if f.strip()]
    ignore_fields = {f.strip() for f in args.ignore.split(',') if f.strip()}

    # A missing/unreadable/malformed input is a usage error, not a
    # traceback: name the file and exit 2 (the usage-error code every
    # sibling tool uses).
    from taxjson.lib.json_input import InputFileError, read_json_doc
    try:
        old_doc = read_json_doc(args.old)
        new_doc = read_json_doc(args.new)
    except InputFileError as e:
        print(f"taxjson-diff: cannot read input {e}", file=sys.stderr)
        sys.exit(2)
    old_recs = extract_records(old_doc)
    new_recs = extract_records(new_doc)

    # A --by field no record on either side carries keys every record
    # as None and pairs rows by list order: 'modified' noise for a typo
    # like --by symbl (audit R1-265).
    present = set()
    for r in old_recs + new_recs:
        if isinstance(r, dict):
            present.update(r.keys())
    unknown = [f for f in match_fields if f not in present]
    if explicit_by and unknown and (old_recs or new_recs):
        print(f"taxjson-diff: error: --by field(s) {', '.join(unknown)} "
              f"appear in no record of either file (fields present: "
              f"{', '.join(sorted(present))})", file=sys.stderr)
        sys.exit(2)

    # Bucket records by composite key (lists, to handle true duplicates).
    old_buckets: Dict[Tuple, List[Dict[str, Any]]] = {}
    for r in old_recs:
        old_buckets.setdefault(make_key(r, match_fields), []).append(r)
    new_buckets: Dict[Tuple, List[Dict[str, Any]]] = {}
    for r in new_recs:
        new_buckets.setdefault(make_key(r, match_fields), []).append(r)

    added: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    matched: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []  # (old, new)

    # Sorted keys (by their repr: keys mix str/None/float) so the
    # listing and a --limit subset are the same on every run — set order
    # varied with PYTHONHASHSEED (audit S029-15).
    all_keys = sorted(set(old_buckets) | set(new_buckets), key=repr)
    for k in all_keys:
        olds = list(old_buckets.get(k, []))
        news = list(new_buckets.get(k, []))
        # Records sharing a key: pair the IDENTICAL ones first, so a
        # pure re-ordering of same-key rows (two INTEREST rows on one
        # day) is no change — positional pairing reported them as each
        # other's modification (audit G7-1).
        rest_new = []
        for n in news:
            hit = next((i for i, o in enumerate(olds)
                        if not field_diffs(o, n, ignore_fields)), None)
            if hit is None:
                rest_new.append(n)
            else:
                matched.append((olds.pop(hit), n))
        n_match = min(len(olds), len(rest_new))
        for i in range(n_match):
            matched.append((olds[i], rest_new[i]))
        # Surplus on either side.
        removed.extend(olds[n_match:])
        added.extend(rest_new[n_match:])

    # Split matched into modified vs unchanged.
    modified: List[Tuple[Dict[str, Any], Dict[str, Any], List[Tuple[str, Any, Any]]]] = []
    unchanged_count = 0
    unchanged_samples: List[Dict[str, Any]] = []
    for o, n in matched:
        diffs = field_diffs(o, n, ignore_fields)
        if diffs:
            modified.append((o, n, diffs))
        else:
            unchanged_count += 1
            if args.show_unchanged:
                unchanged_samples.append(n)

    # Stable sort each section for readable output. Coerce to '' because
    # some records (TAX, INTEREST, FEE on a CASH symbol) may have None for
    # symbol or other key fields, and Python 3 won't compare str < None.
    def sort_key(r):
        # The full match key breaks ties so same-day rows keep one order.
        return (r.get('date') or '', r.get('symbol') or '',
                r.get('action') or '', repr(make_key(r, match_fields)),
                json.dumps(r, sort_keys=True, default=str))
    added.sort(key=sort_key)
    removed.sort(key=sort_key)
    modified.sort(key=lambda t: sort_key(t[0]))
    unchanged_samples.sort(key=sort_key)

    use_color = (
        sys.stdout.isatty()
        and not args.no_color
        and not os.environ.get('NO_COLOR')
    )

    # Header + summary.
    print(colorize(f"# taxjson-diff: {args.old}  →  {args.new}", 'header', use_color))
    print(colorize(
        f"# match key: {','.join(match_fields)}    ignore: {','.join(sorted(ignore_fields)) or '(none)'}",
        'dim', use_color,
    ))
    summary_parts = [
        f"{len(added)} added",
        f"{len(removed)} removed",
        f"{len(modified)} modified",
        f"{unchanged_count} unchanged",
    ]
    print(colorize(f"# {' | '.join(summary_parts)}", 'header', use_color))

    if args.summary:
        return

    def print_section(title, items, color_key, render):
        if not items:
            return
        print()
        shown = items[:args.limit] if args.limit > 0 else items
        print(colorize(f"# === {title} ({len(items)}{', showing '+str(len(shown)) if args.limit and len(shown)<len(items) else ''}) ===", color_key, use_color))
        for it in shown:
            render(it)

    print_section(
        "REMOVED", removed, 'remove',
        lambda r: print(colorize(f"- {short_label(r)}", 'remove', use_color)),
    )
    print_section(
        "ADDED", added, 'add',
        lambda r: print(colorize(f"+ {short_label(r)}", 'add', use_color)),
    )

    def render_mod(t):
        old, new, diffs = t
        print()
        print(colorize(f"~ {short_label(new)}", 'mod', use_color))
        for field, ov, nv in diffs:
            print(colorize(f"    - {field}: {fmt_val(ov)}", 'remove', use_color))
            print(colorize(f"    + {field}: {fmt_val(nv)}", 'add', use_color))

    print_section("MODIFIED", modified, 'mod', render_mod)

    if args.show_unchanged and unchanged_samples:
        shown = unchanged_samples[:args.limit] if args.limit > 0 else unchanged_samples
        print()
        print(colorize(f"# === UNCHANGED ({unchanged_count}{', showing '+str(len(shown)) if args.limit and len(shown)<unchanged_count else ''}) ===", 'dim', use_color))
        for r in shown:
            print(colorize(f"  {short_label(r)}", 'dim', use_color))


if __name__ == "__main__":
    main()
