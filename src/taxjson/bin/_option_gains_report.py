"""Shared body of taxjson-ccd-gains (SHORT calls) and taxjson-leaps-gains
(LONG options): select the option rows of one direction from gains JSON,
group them by underlying and render the per-underlying tables and the
summary.

One implementation for both views (the two files were copies and drifted,
audit S028-08):

* direction: the row's own `direction`; a legacy row without one is
  inferred from gain == ±(proceeds - cost), and a BREAK-EVEN row (which
  satisfies both orientations) is LONG — the guard `tjs ccd-sum` has
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
from typing import Any, Dict, List

from taxjson.lib import cli_diag
from taxjson.lib import out as _out
from taxjson.lib.json_input import (InputFileError, read_json_doc,
                                    require_gains_doc, require_row_lists)
from taxjson.lib.ticker_map import (get_option_type, get_underlying,
                                    is_option_ticker)
from taxjson.model.result import GainRow
from taxjson.model import money as M


def _direction(v: GainRow) -> str:
    direction = v.get('direction')
    if direction is not None:
        return direction
    cost = float(v.cost or 0.0)
    proceeds = float(v.proceeds or 0.0)
    gain = float(v.gain or 0.0)
    if abs(gain) >= 0.01 and abs(gain - (cost - proceeds)) < 0.01:
        return 'SHORT'
    return 'LONG'


def _require_text_symbols(doc, path):
    """``doc`` when each manual row's symbol is text: process_data reads
    it as an option ticker (a list was a traceback, backlog TJ-1294). The
    `transactions` rows have their own check (require_gains_doc's)."""
    for row in doc.get('manual_reporting_required') or []:
        symbol = row.get('symbol')
        if symbol and not isinstance(symbol, str):
            raise InputFileError(f'{path}: a "manual_reporting_required" '
                                 f'row\'s "symbol" must be text')
    return doc


def process_data(data, groups, *, direction: str, calls_only: bool):
    """Add the matching rows of one gains document to `groups`
    ({underlying: {'lines': [...], 'total_gain': {CUR: x}}}; the tainted
    count under '_tainted')."""
    # An SAMPLD...TO call on SAMPLD.B.TO shares groups under the class share,
    # not a phantom SAMPLD.TO (S040-11).
    from taxjson.lib.ticker_map import class_share_aliases
    # The held shares count too: a covered call's SAMPLD.B.TO is usually
    # only in the inventory (re-audit A2-0715).
    aliases = class_share_aliases(
        t.get('symbol') for t in (data.get('transactions', []) or [])
        + (data.get('inventory', []) or [])
        if isinstance(t, dict))
    for tx in data.get('transactions', []) or []:
        if not isinstance(tx, dict):
            continue
        symbol = tx.get('symbol', '') or ''
        if not is_option_ticker(symbol):
            continue
        if calls_only and get_option_type(symbol) != 'C':
            continue
        v = GainRow(tx)                 # its figures (step 6c(6c2))
        if _direction(v) != direction:
            continue
        if tx.get('tainted'):
            # Unknown-cost rows carry a fabricated cost: excluded (and
            # counted) exactly as ccd-sum / leaps-sum and every
            # filing-facing consumer do (audit R1-173).
            groups['_tainted'] = groups.get('_tainted', 0) + 1
            continue
        underlying = get_underlying(symbol)
        if not underlying:
            continue
        underlying = aliases.get(underlying.upper(), underlying)
        g = groups.setdefault(underlying, {'lines': [], 'total_gain': {}})
        g['lines'].append(tx)
        cur = tx.get('currency') or '?'
        g['total_gain'][cur] = (g['total_gain'].get(cur, 0.0)
                                + float(v.gain or 0.0))
    # The pipeline moves unknown-cost dispositions out of `transactions`
    # into manual_reporting_required: they are counted too, or the
    # report read TOTAL 0.00 over a sale to report by hand (backlog
    # TJ-1008). A legacy row flagged inline was counted above.
    seen = {str(tx.get('id')) for tx in data.get('transactions', []) or []
            if isinstance(tx, dict) and tx.get('tainted')
            and tx.get('id') is not None}
    for tx in data.get('manual_reporting_required', []) or []:
        if not isinstance(tx, dict):
            continue
        symbol = tx.get('symbol', '') or ''
        if not is_option_ticker(symbol):
            continue
        if calls_only and get_option_type(symbol) != 'C':
            continue
        if (tx.get('direction') or 'LONG') != direction:
            continue
        if tx.get('id') is not None and str(tx.get('id')) in seen:
            continue
        groups['_tainted'] = groups.get('_tainted', 0) + 1


def _legs(v: GainRow, direction: str):
    """(first, second) money columns of a row. LONG: (cost, proceeds).
    SHORT: (premium, buy-back) in real-world orientation, as ccd-sum and
    Schedule 3 show them — a row with an explicit direction carries the
    engines' signed legs (cost = -premium, proceeds = -buyback) and
    printed as a NEGATIVE cost / proceeds; a legacy inferred row carries
    them swapped and positive (S028-04)."""
    c = float(v.cost or 0)
    p = float(v.proceeds or 0)
    if direction != 'SHORT':
        return c, p
    return (c, p) if v.get('direction') is None else (-c, -p)


def _z(v: float) -> float:
    """-0.00 prints as 0.00."""
    return v + 0.0 if abs(v) >= 0.005 else 0.0


def _per_unit(v: GainRow, key: str, figure) -> float:
    """The row's `key` (a per-share figure it may carry), else `figure`
    (its cost, proceeds or gain) over its units."""
    if v.get(key) is not None:
        return float(v.get(key))
    qty = abs(float(v.quantity or 0))
    return float(figure or 0) / qty if qty else 0.0


def _money_by_cur(d: Dict[str, float]) -> str:
    return ", ".join(f"{M.shown(_z(v), 2):,.2f} {c}" for c, v in sorted(d.items()))


def load_inputs(prog: str, files: List[str]):
    """The gains documents named on the command line (stdin when none),
    or a one-line refusal and exit."""
    if not files:
        from taxjson.lib.report_model import strip_report_comments
        import io
        try:
            content = strip_report_comments(
                io.StringIO(cli_diag.read_stdin_utf8()))
        except (UnicodeDecodeError, cli_diag.InputReadError) as e:
            cli_diag.error(prog, f"<stdin>: not UTF-8 text "
                                 f"({getattr(e, 'reason', e)})")
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
            sys.exit(2)
        try:
            _require_text_symbols(require_row_lists(
                require_gains_doc(doc, "<stdin>"), "<stdin>"), "<stdin>")
        except InputFileError as e:
            cli_diag.error(prog, str(e))
            sys.exit(2)
        return [doc]
    docs = []
    for path in files:
        try:
            # A stage file or a non-gains JSON printed TOTAL 0.00
            # (S033-01).
            docs.append(_require_text_symbols(require_row_lists(
                require_gains_doc(read_json_doc(path), path), path), path))
        except InputFileError as e:
            # A report missing an input is not a report: the old code
            # printed TOTAL 0.00 and exited 0 (audit R1-173).
            cli_diag.error(prog, f"cannot load {e}")
            sys.exit(2)             # an unreadable input (A2-0164)
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

    # The report: a live exact Doc, each line written as it is added, as
    # the raw print() wrote it (nothing here wraps, so no width is
    # resolved). A row that stops the report (a figure that will not
    # format), or a reader that has gone, stops it where the prints did.
    d = _out.Doc(exact=True, width_=0, live=True)
    for und in sorted(groups):
        _underlying_lines(d, und, groups, direction, title, row_total_label,
                          single)
    _summary_lines(d, groups, grand, tainted, single, only_cur, summary_title,
                   summary_col)


def _underlying_lines(d: _out.Doc, und: str, groups: Dict[str, Any],
                      direction: str, title: str, row_total_label: str,
                      single: bool) -> None:
    """One underlying's table and its total line, through the live Doc
    `d`."""
    d.line(f"{title} — {und}")
    d.blank()
    headers = (["DATE", "SYMBOL", "QTY", "CUR", "PREM/QTY", "BUYB/QTY",
                "GAIN/QTY", "PREMIUM", "BUYBACK", "GAIN", "DAYS"]
               if direction == 'SHORT' else
               ["DATE", "SYMBOL", "QTY", "CUR", "COST/QTY", "PROC/QTY",
                "GAIN/QTY", "COST", "PROCEEDS", "GAIN", "DAYS"])
    d.line(f"{headers[0]:<12} {headers[1]:<26} {headers[2]:>10} "
           f"{headers[3]:<5} {headers[4]:>15} {headers[5]:>15} "
           f"{headers[6]:>15} {headers[7]:>15} {headers[8]:>15} "
           f"{headers[9]:>15} {headers[10]:>6}")
    d.line("-" * 159)   # header rule matches the table width
    # Per-unit columns (per contract for options) keep 4-decimal
    # precision; the money columns are 2dp with thousands separators.
    for tx in groups[und]['lines']:
        v = GainRow(tx)
        a, b = _legs(v, direction)
        a_u = _per_unit(v, 'cost_per_share', v.cost)
        b_u = _per_unit(v, 'proceeds_per_share', v.proceeds)
        if direction == 'SHORT' and v.get('direction') is not None:
            a_u, b_u = -a_u, -b_u       # same orientation as a, b
        d.line(f"{tx.get('date'):<12} "
               f"{tx.get('symbol'):<26} "
               f"{float(v.quantity or 0):10.4f} "
               f"{(tx.get('currency') or '?'):<5} "
               f"{M.shown(_z(a_u), 4):15.4f} "
               f"{M.shown(_z(b_u), 4):15.4f} "
               f"{M.shown(_z(_per_unit(v, 'gain_per_share', v.gain)), 4):15.4f} "
               f"{M.shown(_z(a), 2):15,.2f} "
               f"{M.shown(_z(b), 2):15,.2f} "
               f"{M.shown(_z(float(v.gain or 0)), 2):15,.2f} "
               f"{int(tx.get('days_held', 0) or 0):6}")
    tg = groups[und]['total_gain']
    if single:
        d.line(f"\nTOTAL {und} {row_total_label}: "
               f"{M.shown(_z(sum(tg.values())), 2):,.2f}\n")
    else:
        d.line(f"\nTOTAL {und} {row_total_label}: "
               f"{_money_by_cur(tg)}\n")


def _summary_lines(d: _out.Doc, groups: Dict[str, Any],
                   grand: Dict[str, float], tainted: int, single: bool,
                   only_cur: str, summary_title: str,
                   summary_col: str) -> None:
    """The summary (each underlying's total, then the grand total) and the
    unknown-cost note, through the live Doc `d`."""
    d.line(summary_title)
    d.blank()
    if single:
        d.line(f"{'SYMBOL':<15} {summary_col:>20}")
        d.line("-" * 36)
        items = sorted(groups.items(),
                       key=lambda x: sum(x[1]['total_gain'].values()),
                       reverse=True)
        for und, g in items:
            d.line(f"{und:<15} {M.shown(_z(sum(g['total_gain'].values())), 2):20,.2f}")
        d.line("-" * 36)
        d.line(f"{'TOTAL':<15} {M.shown(_z(sum(grand.values())), 2):20,.2f}")
        if only_cur:
            d.line(f"(amounts in {only_cur})")
    else:
        d.line(f"{'SYMBOL':<15} {'CUR':<5} {summary_col:>20}")
        d.line("-" * 42)
        rows = sorted(((und, c, v) for und, g in groups.items()
                       for c, v in g['total_gain'].items()),
                      key=lambda r: (r[1], -r[2], r[0]))
        for und, c, v in rows:
            d.line(f"{und:<15} {c:<5} {M.shown(_z(v), 2):20,.2f}")
        d.line("-" * 42)
        for c in sorted(grand):
            d.line(f"{'TOTAL':<15} {c:<5} {M.shown(_z(grand[c]), 2):20,.2f}")
        d.line("(mixed currencies: totals are per currency — run on the "
               "converted gains file for one base-currency total)")
    if tainted:
        d.line(f"\nNOTE: {tainted} disposition(s) with an unknown cost "
               f"(no purchase in your files) excluded (report them by "
               f"hand; form-export lists them).")
