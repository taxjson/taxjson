"""Shared body of taxjson-ccd-gains (SHORT calls) and taxjson-leaps-gains
(LONG options): select the option rows of one direction from gains JSON,
group them by underlying and render the per-underlying tables and the
summary.

One implementation for both views (the two files were copies and drifted,
audit S028-08):

* direction: the row's own `direction`; a legacy row without one is
  inferred from gain == ±(proceeds - cost), and a BREAK-EVEN row (which
  satisfies both orientations) is LONG — the guard `taxjson ccd-sum` has
  used since the 2026-09 audit (S028-08);
* per-unit columns are derived from the row (cost / |qty| ...): the gains
  JSON never carried cost_per_share & co., so every row printed 0.0000
  (R1-239). The unit is one contract for an option (qty counts contracts);
* totals are per currency: a native (pre-conversion) gains file mixes USD
  and CAD rows, and adding them into one unlabelled TOTAL was meaningless
  (S028-11). A single-currency report keeps its one-line totals and names
  the currency;
* inputs follow lib/json_input: an unreadable, non-UTF-8 or wrong-shape
  file is a one-line refusal with a non-zero exit (S051-11, S079-11).
"""

import argparse
import json
import sys
from typing import Dict, List

from taxjson.lib import cli_diag
from taxjson.lib.json_input import (InputFileError, read_json_doc,
                                    require_gains_doc)
from taxjson.lib.ticker_map import (get_option_type, get_underlying,
                                    is_option_ticker)


def _direction(tx) -> str:
    direction = tx.get('direction')
    if direction is not None:
        return direction
    cost = float(tx.get('cost', 0.0) or 0.0)
    proceeds = float(tx.get('proceeds', 0.0) or 0.0)
    gain = float(tx.get('gain', 0.0) or 0.0)
    if abs(gain) >= 0.01 and abs(gain - (cost - proceeds)) < 0.01:
        return 'SHORT'
    return 'LONG'


def process_data(data, groups, *, direction: str, calls_only: bool):
    """Add the matching rows of one gains document to `groups`
    ({underlying: {'lines': [...], 'total_gain': {CUR: x}}}; the tainted
    count under '_tainted')."""
    for tx in data.get('transactions', []) or []:
        if not isinstance(tx, dict):
            continue
        symbol = tx.get('symbol', '') or ''
        if not is_option_ticker(symbol):
            continue
        if calls_only and get_option_type(symbol) != 'C':
            continue
        if _direction(tx) != direction:
            continue
        if tx.get('tainted'):
            # Phantom-basis rows carry a fabricated cost: excluded (and
            # counted) exactly as ccd-sum / leaps-sum and every
            # filing-facing consumer do (audit R1-173).
            groups['_tainted'] = groups.get('_tainted', 0) + 1
            continue
        underlying = get_underlying(symbol)
        if not underlying:
            continue
        g = groups.setdefault(underlying, {'lines': [], 'total_gain': {}})
        g['lines'].append(tx)
        cur = tx.get('currency') or '?'
        g['total_gain'][cur] = (g['total_gain'].get(cur, 0.0)
                                + float(tx.get('gain', 0.0) or 0.0))


def _z(v: float) -> float:
    """-0.00 prints as 0.00."""
    return v + 0.0 if abs(v) >= 0.005 else 0.0


def _per_unit(tx, key: str, field: str) -> float:
    if tx.get(key) is not None:
        return float(tx[key])
    qty = abs(float(tx.get('qty', 0) or 0))
    return float(tx.get(field, 0) or 0) / qty if qty else 0.0


def _money_by_cur(d: Dict[str, float]) -> str:
    return ", ".join(f"{_z(v):,.2f} {c}" for c, v in sorted(d.items()))


def load_inputs(prog: str, files: List[str]):
    """The gains documents named on the command line (stdin when none),
    or a one-line refusal and exit."""
    if not files:
        from taxjson.lib.report_model import strip_report_comments
        try:
            content = strip_report_comments(sys.stdin)
        except UnicodeDecodeError as e:
            cli_diag.error(prog, f"<stdin>: not UTF-8 text ({e.reason})")
            sys.exit(1)
        if not content.strip():
            cli_diag.error(prog, "no input provided")
            sys.exit(1)
        try:
            doc = json.loads(content)
        except ValueError as e:
            cli_diag.error(prog, f"<stdin>: not valid JSON ({e})")
            sys.exit(1)
        if isinstance(doc, list):
            doc = {'transactions': doc}
        if not isinstance(doc, dict):
            cli_diag.error(prog, "<stdin>: expected a JSON object")
            sys.exit(1)
        try:
            require_gains_doc(doc, "<stdin>")
        except InputFileError as e:
            cli_diag.error(prog, str(e))
            sys.exit(1)
        return [doc]
    docs = []
    for path in files:
        try:
            # A stage file or a non-gains JSON printed TOTAL 0.00
            # (S033-01).
            docs.append(require_gains_doc(read_json_doc(path), path))
        except InputFileError as e:
            # A report missing an input is not a report: the old code
            # printed TOTAL 0.00 and exited 0 (audit R1-173).
            cli_diag.error(prog, f"cannot load {e}")
            sys.exit(1)
    return docs


def main(*, prog: str, description: str, direction: str, calls_only: bool,
         title: str, row_total_label: str, summary_title: str,
         summary_col: str):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("files", nargs="*", metavar="FILE",
                        help="Input JSON files from taxjson_gains.py "
                             "(default: stdin)")
    args = parser.parse_args()

    groups: Dict = {}
    for doc in load_inputs(prog, args.files):
        process_data(doc, groups, direction=direction,
                     calls_only=calls_only)
    tainted = groups.pop('_tainted', 0)

    grand: Dict[str, float] = {}
    for g in groups.values():
        for c, v in g['total_gain'].items():
            grand[c] = grand.get(c, 0.0) + v
    single = len(grand) <= 1
    only_cur = next(iter(grand), '')

    for und in sorted(groups):
        print(f"{title} — {und}")
        print()
        headers = ["DATE", "SYMBOL", "QTY", "CUR", "COST/QTY", "PROC/QTY",
                   "GAIN/QTY", "COST", "PROCEEDS", "GAIN", "DAYS"]
        print(f"{headers[0]:<12} {headers[1]:<26} {headers[2]:>10} "
              f"{headers[3]:<5} {headers[4]:>15} {headers[5]:>15} "
              f"{headers[6]:>15} {headers[7]:>15} {headers[8]:>15} "
              f"{headers[9]:>15} {headers[10]:>6}")
        print("-" * 159)   # header rule matches the table width
        # Per-unit columns (per contract for options) keep 4-decimal
        # precision; the money columns are 2dp with thousands separators.
        for tx in groups[und]['lines']:
            print(f"{tx.get('date'):<12} "
                  f"{tx.get('symbol'):<26} "
                  f"{float(tx.get('qty', 0)):10.4f} "
                  f"{(tx.get('currency') or '?'):<5} "
                  f"{_z(_per_unit(tx, 'cost_per_share', 'cost')):15.4f} "
                  f"{_z(_per_unit(tx, 'proceeds_per_share', 'proceeds')):15.4f} "
                  f"{_z(_per_unit(tx, 'gain_per_share', 'gain')):15.4f} "
                  f"{_z(float(tx.get('cost', 0) or 0)):15,.2f} "
                  f"{_z(float(tx.get('proceeds', 0) or 0)):15,.2f} "
                  f"{_z(float(tx.get('gain', 0) or 0)):15,.2f} "
                  f"{int(tx.get('days_held', 0) or 0):6}")
        tg = groups[und]['total_gain']
        if single:
            print(f"\nTOTAL {und} {row_total_label}: "
                  f"{_z(sum(tg.values())):,.2f}\n")
        else:
            print(f"\nTOTAL {und} {row_total_label}: "
                  f"{_money_by_cur(tg)}\n")

    print(summary_title)
    print()
    if single:
        print(f"{'SYMBOL':<15} {summary_col:>20}")
        print("-" * 36)
        items = sorted(groups.items(),
                       key=lambda x: sum(x[1]['total_gain'].values()),
                       reverse=True)
        for und, g in items:
            print(f"{und:<15} {_z(sum(g['total_gain'].values())):20,.2f}")
        print("-" * 36)
        print(f"{'TOTAL':<15} {_z(sum(grand.values())):20,.2f}")
        if only_cur:
            print(f"(amounts in {only_cur})")
    else:
        print(f"{'SYMBOL':<15} {'CUR':<5} {summary_col:>20}")
        print("-" * 42)
        rows = sorted(((und, c, v) for und, g in groups.items()
                       for c, v in g['total_gain'].items()),
                      key=lambda r: (r[1], -r[2], r[0]))
        for und, c, v in rows:
            print(f"{und:<15} {c:<5} {_z(v):20,.2f}")
        print("-" * 42)
        for c in sorted(grand):
            print(f"{'TOTAL':<15} {c:<5} {_z(grand[c]):20,.2f}")
        print("(mixed currencies: totals are per currency — run on the "
              "converted gains file for one base-currency total)")
    if tainted:
        print(f"\nNOTE: {tainted} tainted disposition(s) with phantom cost "
              f"basis excluded (report them by hand; form-export lists "
              f"them).")
