#!/usr/bin/env python3
"""
taxjson_fees.py

Trading-fee report, broken down by brokerage, with comparison statistics.

"Trading fees" here means `commission + fee` on BUYSELL / ASSIGN rows only —
the same rows taxjson-gains counts for `summary.total_fees_by_currency`
(dividends, transfers, and other non-trade rows are excluded). Rebates
(negative net fees) ARE included, so a brokerage's true net cost shows.
The YEAR is the TRADE date's and each fee is converted at its trade
date's rate (the per-trade `taxjson fees` view's window); `taxjson sum`
FEES and the .sum use the project's tax_date (settle in Canada) and the
book's converted amounts, so the two differ by the fees of trades that
straddle Dec 31 and by a few cents of FX (audit R1-290).

Brokerage attribution is read from each input file's
`metadata.source_brokerage` (set by taxjson-brokerage at parse time);
transactions themselves carry no broker tag. So this tool consumes the
PARSED per-broker JSONs in the cache — `<account>_<broker>.json` — NOT the
merged/base/gains files (which have already blended brokers together).

Because the parsed files predate the merge's dedup, rows are de-duplicated
with the books' own rule (taxjson_sort.plan_dedup: one id in two overlapping
exports is one row; identical rows in separate .tt files or in statements of
different broker accounts are separate trades), so a re-downloaded,
overlapping statement isn't double-counted and the report agrees with the
books.

Usage:
    taxjson-fees-sum --cache work --year 2026
    taxjson-fees-sum --cache work --year 2026 --to CAD --rates work/to_base.csv
    taxjson-fees-sum --cache work --year 2026 --to CAD --rates work/to_base.csv --json
"""

import argparse
import glob
import json
import statistics
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from taxjson.lib.report_model import fmt_money
from taxjson.lib import cli_diag
from taxjson.lib.cli_diag import guard_main, tax_year
from taxjson.bin.taxjson_convert_currency import (default_rate_for,
                                                  positive_rate)
from typing import Any, Dict, List, Optional

from taxjson.lib.core import convert_currency
from taxjson.bin.taxjson_convert_currency import (
    load_exchange_rates, get_rate_for_date, norm_currency,
    reset_fallback_tally, emit_fallback_summary,
)
from taxjson.lib.ticker_map import is_option_ticker
from taxjson.lib.json_input import load_json_doc_or_exit

PROG = "taxjson-fees-sum"

TRADE_ACTIONS = ("BUYSELL", "ASSIGN")

# convert-tt's source label for hand-entered .tt files (no broker named).
MANUAL_TT = "manual (.tt)"
# A generic-mapping import with no [broker] name (a named one is
# 'generic:<name>').
GENERIC = "generic"


def _iso_date(text: str) -> str:
    """argparse type for --since: a real YYYY-MM-DD date. The filter
    compares date strings, so '2025-6-1' or '2025/06/01' silently
    dropped every fee at exit 0 (S031-06)."""
    from datetime import date
    try:
        if len(text) != 10:
            raise ValueError
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a YYYY-MM-DD date")


def _is_option(symbol: str) -> bool:
    try:
        return bool(is_option_ticker(symbol))
    except Exception:
        return False


def collect_files(positional: List[str], cache: Optional[str]) -> List[Path]:
    """Resolve the input file list. A --cache dir contributes every *.json
    in it (the non-broker ones are filtered out later, by metadata)."""
    paths: List[Path] = [Path(p) for p in positional]
    if cache:
        paths += [Path(p) for p in sorted(glob.glob(str(Path(cache) / "*.json")))]
    seen, out = set(), []
    for p in paths:
        rp = str(p.resolve())
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def new_stats() -> Dict[str, Any]:
    """A single comparable bucket of fee statistics. Used both for a
    brokerage's base-currency roll-up and for its per-currency native splits;
    the math (mean, median, %notional, per-unit) is identical, only the
    amounts differ (converted vs native)."""
    return {
        "trades": 0,
        "fee": 0.0,
        "fees": [],             # per-trade fee, for median
        "shares": 0.0,          # abs qty, stocks only
        "contracts": 0.0,       # abs qty, options only
        "notional": 0.0,        # abs gross_amount, rows with notional > 0 only
        "notional_fee": 0.0,    # fees on those same rows (so %notional is honest)
        "fee_stocks": 0.0,
        "fee_options": 0.0,
        "fee_futures": 0.0,     # plain futures: not shares (S076-12)
    }


def add_stat(s: Dict[str, Any], fee: float, qty: float,
             is_opt: bool, notional: float, is_fut: bool = False) -> None:
    s["trades"] += 1
    s["fee"] += fee
    s["fees"].append(fee)
    if notional > 0:
        s["notional"] += notional
        s["notional_fee"] += fee
    if is_opt:
        s["contracts"] += qty
        s["fee_options"] += fee
    elif is_fut:
        s["fee_futures"] = s.get("fee_futures", 0.0) + fee
    else:
        s["shares"] += qty
        s["fee_stocks"] += fee


def aggregate(files, *, year, since, to_curr, history, default_rate, by_account,
              deleted=frozenset()):
    """Walk the parsed files, dedup by id, accumulate per-key buckets.
    Each bucket = {'base': stats, 'cur': {CUR: stats}}."""
    buckets: Dict[str, Dict[str, Any]] = {}
    grand = {"base": new_stats(), "cur": {}}
    brokers_seen: set = set()
    skipped_no_broker: List[str] = []
    n_dups = 0
    n_no_id = 0
    n_rows = 0
    files_read = 0
    converting = bool(to_curr)

    def to_base(amount, curr, date):
        if not converting or norm_currency(curr) == norm_currency(to_curr) \
                or not amount:
            return amount
        # The fallback of this direction (audit A2-0148).
        _fb = default_rate_for(curr, to_curr, default_rate)
        rate = get_rate_for_date(curr, date, history, _fb)
        return convert_currency(amount, curr, to_curr,
                                {(curr, to_curr): rate}, float(_fb))

    # Every parsed row first, then ONE dedup over all of them with the
    # books' own rule (taxjson_sort.plan_dedup): an id-only pass here
    # dropped a real commission when two separate exports held identical
    # rows (audit S031-02), and the report has to agree with the books.
    all_rows: List[Dict[str, Any]] = []
    row_broker: List[str] = []
    metas: List[Dict[str, Any]] = []
    for fp in files:
        # A named (or --cache) book that cannot be read stops the
        # report: skipping it dropped a whole broker's fees from the
        # TOTAL at exit 0 (audit S028-09, S035-13).
        data = load_json_doc_or_exit(PROG, fp)
        broker = (data.get("metadata") or {}).get("source_brokerage")
        if not broker:
            skipped_no_broker.append(fp.name)
            continue
        files_read += 1
        brokers_seen.add(broker)
        metas.append(data.get("metadata") or {})
        for tx in data.get("transactions", []):
            if isinstance(tx, dict):
                all_rows.append(tx)
                row_broker.append(broker)

    from taxjson.bin.taxjson_sort import plan_dedup, source_accounts_of
    plan = plan_dedup(all_rows, source_accounts_of(metas))
    dropped = set(plan.drop)

    for i, tx in enumerate(all_rows):
        broker = row_broker[i]
        if tx.get("action") not in TRADE_ACTIONS:
            continue
        # ticker.map DELETE rows never reach the books (merge2 drops
        # them, by the broker's raw symbol): their fees are not fees
        # the books paid either (S038-11).
        if str(tx.get("symbol") or "").upper() in deleted:
            continue
        fee = float(tx.get("commission") or 0) + float(tx.get("fee") or 0)
        if fee == 0:
            continue
        # TRADE-date basis, matching the per-trade `taxjson fees`
        # view — the two windows disagreed at year boundaries
        # (Dec-30 trade settling Jan-2 landed in different years
        # per tool; 2026-09 audit). Fees are incurred at trade.
        date = tx.get("date") or tx.get("date_settle") or ""
        if year and not date.startswith(year):
            continue
        if since and (not date or date < since):
            continue
        if i in dropped:
            n_dups += 1
            continue
        if tx.get("id") is None:
            n_no_id += 1
        n_rows += 1

        curr = tx.get("currency") or "?"
        # A row without an account stamp (hand-run on unstamped
        # JSON) is '<broker>/?', like a missing currency — not the
        # literal 'None' (S031-03).
        key = (f"{broker}/{tx.get('account') or '?'}" if by_account
               else broker)
        is_opt = _is_option(tx.get("symbol") or "")
        from taxjson.lib.futures import is_plain_future
        is_fut = is_plain_future(tx.get("symbol") or "")
        qty = abs(float(tx.get("quantity") or 0))
        notional = abs(float(tx.get("gross_amount") or tx.get("net_amount") or 0))
        fee_base = to_base(fee, curr, date)
        notional_base = to_base(notional, curr, date)

        bucket = buckets.setdefault(key, {"base": new_stats(), "cur": {}})
        for B in (bucket, grand):
            add_stat(B["base"], fee_base, qty, is_opt, notional_base,
                     is_fut)
            add_stat(B["cur"].setdefault(curr, new_stats()),
                     fee, qty, is_opt, notional, is_fut)

    # A generic-imported file whose mapping names its broker is booked
    # as generic:<name>: that broker's fees ARE in the report, so it is
    # not 'fee-free' just because its own-parser exports had none
    # (audit S027-05).
    from taxjson.bin.taxjson_brokerage import _CANONICAL_ID
    _fee_brokers = {k.split("/")[0] for k in buckets}
    _covered = {_CANONICAL_ID.get(b.split(":", 1)[1], b.split(":", 1)[1])
                for b in _fee_brokers if b.startswith("generic:")}
    zero_fee = sorted(b for b in brokers_seen - _fee_brokers
                      - {MANUAL_TT, GENERIC}
                      if _CANONICAL_ID.get(b, b) not in _covered)
    # A hand-entered .tt file names no broker, so its fees cannot be
    # attributed: a broker whose period trades live only in a .tt is
    # not 'fee-free' (R1-101). Neither is one whose fees sit in a
    # generic import with no [broker] name (A2-0646).
    _unnamed = {k.split("/")[0] for k in buckets} & {MANUAL_TT, GENERIC}
    manual_fees = bool(_unnamed)
    info = {
        "files_read": files_read, "rows": n_rows, "dups": n_dups,
        "no_id": n_no_id, "skipped": skipped_no_broker, "zero_fee": zero_fee,
        "manual_fees": manual_fees, "unnamed": sorted(_unnamed),
        "attention": list(plan.attention),
    }
    return buckets, grand, info


# ---------------------------------------------------------------- metrics

def metrics(s: Dict[str, Any]) -> Dict[str, float]:
    n = s["trades"]
    return {
        "total": s["fee"],
        "trades": n,
        "mean": s["fee"] / n if n else 0.0,
        "median": statistics.median(s["fees"]) if s["fees"] else 0.0,
        "pct_notional": (s["notional_fee"] / s["notional"] * 100)
        if s["notional"] else 0.0,
        "per_share": s["fee_stocks"] / s["shares"] if s["shares"] else 0.0,
        "per_contract": s["fee_options"] / s["contracts"] if s["contracts"] else 0.0,
        "stock_fee": s["fee_stocks"],
        "option_fee": s["fee_options"],
        "futures_fee": s.get("fee_futures", 0.0),
    }


# ---------------------------------------------------------------- text render

_money = fmt_money                  # shared report-layer formatter


_COLS = (f"{'BROKERAGE':<24} {'CUR':<4} {'TRADES':>7} {'TOTAL':>14} "
         f"{'MEAN':>9} {'MEDIAN':>9} {'%NOTNL':>8} {'$/UNIT':>9} "
         f"{'$/CONTR':>9}")


def _row(name: str, s: Dict[str, Any], cur: str = "") -> str:
    m = metrics(s)
    return (f"{name:<24} {cur:<4} {m['trades']:>7d} {_money(m['total']):>14} "
            f"{_money(m['mean']):>9} {_money(m['median']):>9} "
            f"{m['pct_notional']:>7.3f}% {m['per_share']:>9.4f} "
            f"{m['per_contract']:>9.4f}")


def render_text(buckets, grand, info, *, to_curr, by_account, year,
                default_rate, scope=None) -> List[str]:
    converting = bool(to_curr)
    out: List[str] = []
    title = "TRADING FEES BY " + ("ACCOUNT / BROKERAGE" if by_account else "BROKERAGE")
    if scope:
        title += f"  ({scope})"
    elif year:
        # The window is the TRADE date (fees are incurred at the trade),
        # not the project's tax_date — say so where the year is named
        # (A2-1102: the .sum and trades-sum place a Dec 30 trade that
        # settles in January in the next year).
        title += f"  (tax year {year}, by TRADE date)"
    if converting:
        title += f"   [all amounts in {to_curr}]"
    out.append(title)
    out.append("")
    out.append(_COLS)
    out.append("-" * len(_COLS))

    if converting:
        rows = sorted(buckets.items(),
                      key=lambda kv: kv[1]["base"]["fee"], reverse=True)
        for name, b in rows:
            out.append(_row(name, b["base"], to_curr))
        out.append("-" * len(_COLS))
        out.append(_row("TOTAL", grand["base"], to_curr))
        # Stock vs option split (absolute), and native composition.
        out.append("")
        # Non-option = shares, units, futures and crypto (a coin
        # exchange's fees were labelled 'stocks', audit S031-04).
        out.append("Non-option vs option fees:")
        for name, b in rows:
            m = metrics(b["base"])
            out.append(f"  {name:<22} non-option {_money(m['stock_fee'])}  "
                       f"options {_money(m['option_fee'])}")
        mixed = [(n, b) for n, b in rows
                 if any(c != to_curr for c in b["cur"])]
        if mixed:
            out.append("")
            out.append(f"Native fees folded into the {to_curr} totals above:")
            for name, b in mixed:
                comp = ", ".join(f"{_money(c['fee'])} {cur}"
                                 for cur, c in sorted(b["cur"].items()))
                out.append(f"  {name:<22} {comp}")
    else:
        # Native: one row per (brokerage, currency) so every average is
        # within a single currency. Sort brokers by total native fee.
        rows = sorted(buckets.items(),
                      key=lambda kv: sum(c["fee"] for c in kv[1]["cur"].values()),
                      reverse=True)
        for name, b in rows:
            for cur in sorted(b["cur"]):
                out.append(_row(name, b["cur"][cur], cur))
        out.append("-" * len(_COLS))
        for cur in sorted(grand["cur"]):
            out.append(_row("TOTAL", grand["cur"][cur], cur))
        out.append("")
        out.append("Note: amounts are in each fee's native currency. Pass "
                   "--to CAD --rates <file> for one combined total.")

    # Provenance + data-quality footer (in stdout so it survives even when
    # the pipeline runs this stage with stderr discarded).
    out.append("")
    out.append("-" * len(_COLS))
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out.append(f"Generated {gen} | files: {info['files_read']} | "
               f"fee rows: {info['rows']} | dups collapsed: {info['dups']}"
               + (f" | rows w/o id: {info['no_id']}" if info['no_id'] else ""))
    if info["zero_fee"]:
        if info.get("manual_fees"):
            _un = info.get("unnamed") or [MANUAL_TT]
            out.append(f"Brokers with no fees in their own exports this "
                       f"period: {', '.join(info['zero_fee'])} — the "
                       f"{' / '.join(_un)} fees above are not attributed "
                       f"to a broker and may belong to one of them"
                       + (" (give the generic mapping a [broker] name to "
                          "attribute them)" if GENERIC in _un else "")
                       + ".")
        else:
            out.append(f"Brokers with NO fees in this period: "
                       f"{', '.join(info['zero_fee'])}")
    if info["skipped"]:
        # In --cache mode most files legitimately lack a brokerage tag, so a
        # full dump is noise; name them only when the list is short.
        if len(info["skipped"]) <= 6:
            out.append(f"Skipped {len(info['skipped'])} non-broker file(s): "
                       f"{', '.join(info['skipped'])}")
        else:
            out.append(f"Skipped {len(info['skipped'])} file(s) with no "
                       f"source_brokerage tag.")
    # FX fallback warning, surfaced into the report body.
    import taxjson.bin.taxjson_convert_currency as _cc
    if _cc._DEFAULT_RATE_FALLBACKS:
        emit_fallback_summary(default_rate, stream=_StdoutList(out))
    out.append("")
    out.append("Definitions: TRADES = BUYSELL/ASSIGN rows with a non-zero fee "
               "(rebates included); MEAN/MEDIAN are per-trade fee; %NOTNL = "
               "fees as a percent of gross trade value (rows with notional > 0); "
               "$/UNIT is non-option fee per unit (share, coin, "
               "contract of a future), $/CONTR is option fee per "
               "contract.")
    return out


class _StdoutList:
    """Adapt emit_fallback_summary(stream=...) to append into our line list."""
    def __init__(self, lines):
        self.lines = lines

    def write(self, text):
        text = text.rstrip("\n")
        if text:
            self.lines.append(text)


# ---------------------------------------------------------------- json render

def render_json(buckets, grand, info, *, to_curr, by_account, year,
                since=None) -> str:
    def serialize(bucket):
        # The no-data path hands us a bare {"total": 0.0} placeholder —
        # indexing bucket["base"] raised KeyError and broke the FUZZ #H
        # no-data-is-success rule for --json only (REVIEW #14).
        if "base" not in bucket:
            zero = {"trades": 0, "fee": 0.0, "fees": [],
                    "notional_fee": 0.0, "notional": 0.0,
                    "fee_stocks": 0.0, "shares": 0.0,
                    "fee_options": 0.0, "contracts": 0.0}
            return {**metrics(zero), "by_currency": {}}
        base_m = metrics(bucket["base"])
        if to_curr is None and len(bucket["cur"]) > 1:
            # Not converted: the "base" bucket adds USD to CAD. The text
            # report refuses that total; the JSON summed the units into
            # total/mean/%notional (S031-05). by_currency only.
            base_m = {k: None for k in base_m}
        d = {**base_m,
             "by_currency": {cur: metrics(s) for cur, s in bucket["cur"].items()}}
        return d
    doc = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
            "year": year, "since": since, "base_currency": to_curr,
            "date_basis": "trade",
            "group_by": "account_brokerage" if by_account else "brokerage",
            "files_read": info["files_read"], "fee_rows": info["rows"],
            "dups_collapsed": info["dups"], "rows_without_id": info["no_id"],
            "skipped_files": info["skipped"],
            "zero_fee_brokers": info["zero_fee"],
            "manual_tt_fees_unattributed": bool(info.get("manual_fees")),
        },
        "brokerages": {name: serialize(b) for name, b in buckets.items()},
        "total": serialize(grand),
    }
    return json.dumps(doc, indent=2)


@guard_main("taxjson-fees-sum")
def main():
    p = argparse.ArgumentParser(
        description="Report trading fees by brokerage, with comparison stats.")
    p.add_argument("files", nargs="*",
                   help="Parsed per-broker JSONs (e.g. work/margin_ib.json). "
                        "Files without metadata.source_brokerage are skipped.")
    p.add_argument("--cache", metavar="DIR",
                   help="Add every *.json in DIR (non-broker files are skipped).")
    p.add_argument("--year", type=tax_year, metavar="YYYY",
                   help="Only count fees whose TRADE date is in this year "
                        "(the settlement date only when a row has no trade "
                        "date). Default: all years.")
    p.add_argument("--since", metavar="YYYY-MM-DD", type=_iso_date,
                   help="Only count fees on/after this date — the cutoff "
                        "channel `taxjson fees-sum PERIOD` drives.")
    p.add_argument("--to", dest="to_curr", metavar="CURR",
                   # 'cad' / ' CAD' converted every CAD fee at the
                   # --default-rate fallback (A2-0645).
                   type=lambda v: norm_currency(v) or None,
                   help="Convert every fee to CURR for one comparable total. "
                        "Requires --rates.")
    p.add_argument("--rates", metavar="FILE",
                   help="Historical FX rates file (e.g. work/to_base.csv).")
    p.add_argument("--default-rate", type=positive_rate, default=None,
                   help="FX fallback when a date/currency is missing "
                        "(default 1.35 for USD->CAD, its inverse for "
                        "CAD->USD); usage is reported, not silent.")
    p.add_argument("--ticker-map", metavar="FILE",
                   help="The project's ticker.map: rows of a DELETE'd "
                        "symbol are left out, as the books leave them out.")
    p.add_argument("--by-account", action="store_true",
                   help="Break down by account/brokerage instead of brokerage.")
    p.add_argument("--json", action="store_true",
                   help="Emit machine-readable JSON instead of a text table.")
    args = p.parse_args()

    if args.to_curr and not args.rates:
        p.error("--to requires --rates")

    files = collect_files(args.files, args.cache)
    if not files and not args.cache:
        p.error("no input files (pass parsed per-broker JSONs or --cache DIR)")
    # A --cache dir with zero parsed per-broker JSONs is a real state
    # (brand-new project, nothing imported yet) and the pipeline calls
    # us unconditionally — exit 2 here crashed the whole first
    # `taxjson run` of a wizard-created project with a raw traceback
    # (REVIEW #13; same no-data-is-success rule as FUZZ #H). Fall
    # through: aggregate([]) yields the honest empty report.

    history = {}
    if args.to_curr:
        try:
            history = load_exchange_rates(Path(args.rates),
                                          target_curr=args.to_curr)
        except ValueError as e:
            # A NaN/0/negative rate: one line, exit 2, as `taxjson
            # audit` says it (re-audit A2-1423).
            cli_diag.error(PROG, str(e))
            sys.exit(2)

    reset_fallback_tally()
    # aggregate() and the renderers match dates by string prefix; --year is
    # int-typed at the CLI (A1 convention) but flows through as a string.
    year = str(args.year) if args.year is not None else None
    deleted = frozenset()
    if args.ticker_map:
        from taxjson.bin.taxjson_ticker_map import load_map_file
        deleted = frozenset(str(x).upper() for x in
                            load_map_file(Path(args.ticker_map)).delete)
    buckets, grand, info = aggregate(
        files, year=year, since=args.since, to_curr=args.to_curr,
        history=history, default_rate=args.default_rate,
        by_account=args.by_account, deleted=deleted)
    scope = f"since {args.since}" if args.since else None

    if not buckets:
        # No-data is SUCCESS (exit-code convention: 0 = success incl.
        # no-data). Returning 1 here crashed the whole `taxjson run`
        # pipeline for anyone whose broker charges no commissions —
        # found independently by four fuzz agents (FUZZ-2026-07 #H).
        if args.json:
            print(render_json({}, {"total": 0.0}, info,
                              to_curr=args.to_curr,
                              by_account=args.by_account, year=year,
                              since=args.since))
        else:
            scope_s = scope or (f"tax year {year}" if year else "all")
            print(f"No trading fees found ({scope_s}).")
        if info["skipped"]:
            print(f"({len(info['skipped'])} file(s) had no source_brokerage and "
                  f"were skipped: {', '.join(info['skipped'])})", file=sys.stderr)
        return 0

    if args.json:
        print(render_json(buckets, grand, info, to_curr=args.to_curr,
                          by_account=args.by_account, year=year,
                          since=args.since))
    else:
        for line in render_text(buckets, grand, info, to_curr=args.to_curr,
                                by_account=args.by_account, year=year,
                                default_rate=args.default_rate, scope=scope):
            print(line)

    # Also emit the fallback summary to stderr (the .diag path) so it's caught
    # whether or not the body was read.
    emit_fallback_summary(args.default_rate)
    if info["dups"]:
        print(f"\n({info['dups']} duplicate row(s) collapsed: the same "
              f"row in overlapping exports.)", file=sys.stderr)
    for line in info.get("attention") or ():
        print(f"warning: ATTENTION: {line}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
