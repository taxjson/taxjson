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
unmatched surplus is reported as ADDED or REMOVED.

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


def extract_records(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Both transactions JSON and taxjson-gains output use the same top-level
    # key. Be defensive: also accept a bare list.
    if isinstance(doc, list):
        return doc
    return doc.get('transactions', []) or []


def make_key(rec: Dict[str, Any], fields: List[str]) -> Tuple:
    """Composite match key. Floats get rounded so 100.00000001 == 100.00."""
    parts = []
    for f in fields:
        v = rec.get(f)
        if isinstance(v, float):
            parts.append(round(v, 6))
        else:
            parts.append(v)
    return tuple(parts)


def values_equal(a: Any, b: Any, tol: float = 1e-6) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a or 0) - float(b or 0)) < tol
        except (TypeError, ValueError):
            return a == b
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
    return "  ".join(parts) if parts else f"id={(rec.get('id') or '')[:10]}"


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
        default="action,date,time,symbol,quantity,price,account",
        help="Comma-separated fields to use as the match key.",
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


def main():
    args = parse_args()
    match_fields = [f.strip() for f in args.by.split(',') if f.strip()]
    ignore_fields = {f.strip() for f in args.ignore.split(',') if f.strip()}

    # A missing/unreadable/malformed input is a usage error, not a
    # traceback: name the file and exit 2 (the usage-error code every
    # sibling tool uses).
    try:
        old_doc = load_json(Path(args.old))
        new_doc = load_json(Path(args.new))
    except (OSError, json.JSONDecodeError) as e:
        bad = getattr(e, "filename", None) or ""
        which = f" {bad}" if bad else ""
        print(f"taxjson-diff: cannot read input{which}: {e}",
              file=sys.stderr)
        sys.exit(2)
    old_recs = extract_records(old_doc)
    new_recs = extract_records(new_doc)

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

    all_keys = set(old_buckets) | set(new_buckets)
    for k in all_keys:
        olds = old_buckets.get(k, [])
        news = new_buckets.get(k, [])
        n_match = min(len(olds), len(news))
        for i in range(n_match):
            matched.append((olds[i], news[i]))
        # Surplus on either side.
        for r in olds[n_match:]:
            removed.append(r)
        for r in news[n_match:]:
            added.append(r)

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
        return (r.get('date') or '', r.get('symbol') or '', r.get('action') or '')
    added.sort(key=sort_key)
    removed.sort(key=sort_key)
    modified.sort(key=lambda t: sort_key(t[0]))

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
