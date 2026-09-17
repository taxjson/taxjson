#!/usr/bin/env python3
"""
taxjson_carryover.py

Multi-year capital-loss carryforward / carryback ledger.

Runs ONE full-history gains pass through lib/pipeline.run_gains (the same
computation the yearly pipeline uses — wash/superficial-loss adjustments,
phantom handling, tainted exclusion all included) and buckets the ALLOWED
dispositions by tax year on the country's date basis. Then:

  CANADA  — per year: net capital gain/(loss); a running net-capital-loss
            balance (losses carry forward indefinitely); for each loss
            year, the still-open 3-year T1A carryback candidates, capped
            at each earlier year's remaining net gain.
  USA     — the capital-loss carryover worksheet shape: net short-term and
            long-term per year, the up-to-$3,000 ordinary-income offset
            (assumed used when available; override per year via
            --claimed), and the running ST/LT carryover split, following
            the Schedule D worksheet ordering (cross-netting first, the
            deduction absorbs short-term loss first).

All amounts are 100% capital gains/losses (pre-inclusion-rate). Canada:
apply the 50% inclusion rate on Schedule 3 / T1A, not here.

The ledger computes what the TRANSACTION HISTORY supports. What you
actually claimed on filed returns may differ — record reality in a
`--claimed FILE` (lines of `YEAR AMOUNT`, `#` comments): each line is the
loss amount actually applied against that YEAR's return. Claims fold into
the running balance (a claim recorded before the loss exists — e.g. a
carryback entered under the target year — is held pending and consumed
when the loss arrives).

Usage:
    taxjson-carryover margin_base.json [more_base.json ...]
        --country canada [--sheltered sheltered_base.json]
        [--incomplete-history phantoms.json] [--claimed claimed_losses.txt]
        [--tax-date settle|trade] [--base-currency CAD] [--json]

Or through the project wrapper: `taxjson carryover`.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from taxjson.lib.cli_diag import note
from taxjson.lib.core import load_transactions
from taxjson.lib.core import AmbiguousTransferDateError as _AmbiguousXferErr
from taxjson.lib.pipeline import (GainsRequest, TransferValidationError,
                                  run_gains)
from taxjson.lib.report_model import fmt_money

_INCOME_ACTIONS = ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST', 'FEE')
US_ORDINARY_OFFSET = 3000.0


def load_claimed(path: Optional[Path]) -> Dict[int, float]:
    claimed: Dict[int, float] = {}
    if path is None:
        return claimed
    for lineno, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        stripped = line.split('#', 1)[0].strip()
        if not stripped:
            continue
        parts = stripped.split()
        try:
            year, amount = int(parts[0]), float(parts[1])
            import math
            # not-isfinite: 'nan' passed the `< 0` check and poisoned
            # the ledger — APPLIED ballooned to the whole balance with
            # zero diagnostics (REVIEW #38).
            if len(parts) != 2 or amount < 0 or not math.isfinite(amount):
                raise ValueError
        except (ValueError, IndexError):
            print(f"warning: {path.name}:{lineno}: expected `YEAR AMOUNT` "
                  f"(amount >= 0), got {stripped!r} — line ignored",
                  file=sys.stderr)
            continue
        claimed[year] = claimed.get(year, 0.0) + amount
    return claimed


def yearly_nets(results: dict, tax_date: str) -> Dict[int, Dict[str, float]]:
    """Bucket ALLOWED disposition gains by tax year, using the same
    date-basis rule as the pipeline's --year filter (dispositions key on
    date_settle under the settle basis; income rows are not dispositions
    and are excluded entirely). Tainted rows were already routed to
    manual_reporting_required by run_gains."""
    date_key = 'date_settle' if tax_date == 'settle' else 'date'
    nets: Dict[int, Dict[str, float]] = {}
    for t in results.get('transactions', []):
        if t.get('action') in _INCOME_ACTIONS:
            continue
        if 'gain' not in t or 'qty' not in t:
            continue
        date = t.get(date_key) or t.get('date') or ''
        if len(date) < 4 or not date[:4].isdigit():
            continue
        year = int(date[:4])
        rec = nets.setdefault(year, {'net': 0.0, 'st': 0.0, 'lt': 0.0,
                                     'dispositions': 0})
        gain = float(t.get('gain') or 0.0)
        rec['net'] += gain
        rec['dispositions'] += 1
        if t.get('term') == 'SHORT_TERM':
            rec['st'] += gain
        elif t.get('term') == 'LONG_TERM':
            rec['lt'] += gain
    return nets


def build_canada_ledger(nets: Dict[int, Dict[str, float]],
                        claimed: Dict[int, float]) -> Dict[str, Any]:
    # Union of book years and CLAIM years: a claim is applied on the
    # year of the RETURN it appears on, which may have no dispositions
    # in the broker book at all (T3 distributions, a no-trade year).
    # Iterating only disposition years silently ignored those claims —
    # the carryforward stayed overstated with no warning (REVIEW #23).
    years = sorted(set(nets) | set(claimed))
    _ZERO = {'net': 0.0, 'dispositions': 0}
    rows: List[Dict[str, Any]] = []
    balance = 0.0          # net-capital-loss carryforward (positive)
    # Claims pending consumption, keyed by the year they were RECORDED
    # against (a T1A carryback is entered under its TARGET year, often
    # before the loss exists in the book). The capacity reduction must
    # key on that recorded year too — reducing the year that merely
    # CONSUMED the pending claim (the loss year) left the target year's
    # capacity intact, and the ledger re-suggested a T1A carryback the
    # user had already filed.
    pending_by_year: Dict[int, float] = {}
    # Remaining carryback capacity per gain year (advisory; reduced by
    # claims against that year and by earlier loss-years' suggestions so
    # two loss years never point at the same dollar of gain).
    cb_capacity = {y: max(0.0, nets[y]['net']) for y in sorted(nets)}
    for y in years:
        net = nets.get(y, _ZERO)['net']
        loss = max(0.0, -net)
        if loss:
            balance += loss
        if claimed.get(y):
            pending_by_year[y] = pending_by_year.get(y, 0.0) + claimed[y]
        # Fold claims oldest-recorded-first: each consumed dollar
        # shelters the gains of ITS recorded year, whose carryback
        # capacity it therefore consumes.
        applied = 0.0
        for _cy in sorted(pending_by_year):
            if balance <= 0.005:
                break
            take = min(balance, pending_by_year[_cy])
            if take <= 0.0:
                continue
            balance -= take
            applied += take
            pending_by_year[_cy] -= take
            if _cy in cb_capacity:
                cb_capacity[_cy] = max(0.0, cb_capacity[_cy] - take)
        pending_by_year = {k: v for k, v in pending_by_year.items()
                           if v > 0.005}

        carryback: List[Dict[str, float]] = []
        if loss:
            # Cap by the carryforward actually left AFTER folding the
            # recorded claims: a loss already (partly) consumed by a
            # filed T1A must not generate fresh carryback suggestions
            # for the consumed share (re-audit).
            remaining = min(loss, balance)
            for target in range(y - 3, y):
                cap = cb_capacity.get(target, 0.0)
                if cap <= 0.005 or remaining <= 0.005:
                    continue
                amt = min(cap, remaining)
                carryback.append({'year': target, 'amount': round(amt, 2)})
                cb_capacity[target] = cap - amt
                remaining -= amt
        rows.append({
            'year': y,
            'net_gain': round(net, 2),
            'dispositions': nets.get(y, _ZERO)['dispositions'],
            'claimed_applied': round(applied, 2),
            'carryforward_balance': round(balance, 2),
            'available_to_apply': round(min(balance, max(0.0, net)), 2)
                                  if net > 0 else 0.0,
            'carryback_candidates': carryback,
        })
    out = {'country': 'canada', 'rows': rows,
           'final_carryforward': round(balance, 2)}
    _unmatched = sum(pending_by_year.values())
    if _unmatched > 0.005:
        out['unmatched_claims'] = round(_unmatched, 2)
    return out


def _us_worksheet(st_net: float, lt_net: float, allowed: float):
    """Schedule D carryover worksheet: cross-net first, the ordinary-income
    deduction absorbs short-term loss before long-term. Returns
    (st_carry, lt_carry) as positive loss magnitudes."""
    if st_net < 0 and lt_net > 0:
        combined = st_net + lt_net
        st_after = max(0.0, -combined)
        lt_after = 0.0
    elif lt_net < 0 and st_net > 0:
        combined = st_net + lt_net
        lt_after = max(0.0, -combined)
        st_after = 0.0
    else:
        st_after = max(0.0, -st_net)
        lt_after = max(0.0, -lt_net)
    ded_st = min(allowed, st_after)
    ded_lt = min(allowed - ded_st, lt_after)
    return st_after - ded_st, lt_after - ded_lt


def build_usa_ledger(nets: Dict[int, Dict[str, float]],
                     claimed: Dict[int, float]) -> Dict[str, Any]:
    # Every RETURN year matters, not just disposition years: while a
    # carryover exists, each intervening year's return absorbs up to
    # $3,000 against ordinary income (or the claimed_losses.txt
    # override, 0 included), so iterating `sorted(nets)` alone silently
    # skipped gap years and overstated the final carryover. Walk the
    # full span of book+claim years; emit a row for a no-disposition
    # year only when it actually does something (carry enters it or a
    # claim targets it). This is the USA twin of the Canada REVIEW #23
    # union fix.
    all_years = set(nets) | set(claimed)
    years = list(range(min(all_years), max(all_years) + 1)) if all_years else []
    _ZERO = {'net': 0.0, 'st': 0.0, 'lt': 0.0, 'dispositions': 0}
    rows: List[Dict[str, Any]] = []
    st_carry = lt_carry = 0.0        # positive loss magnitudes
    for y in years:
        rec = nets.get(y, _ZERO)
        if (y not in nets and y not in claimed
                and st_carry + lt_carry <= 0.005):
            continue
        st_net = rec['st'] - st_carry
        lt_net = rec['lt'] - lt_carry
        combined = st_net + lt_net
        if combined < 0:
            offset_cap = min(US_ORDINARY_OFFSET, -combined)
            if y in claimed:
                # Override the assumed usage with what was actually
                # deducted that year (0 is legitimate: no return filed /
                # no income to offset).
                offset = min(claimed[y], offset_cap)
                if claimed[y] > offset_cap + 0.005:
                    print(f"warning: claimed {claimed[y]:,.2f} for {y} "
                          f"exceeds the {offset_cap:,.2f} the worksheet "
                          f"supports — capped.", file=sys.stderr)
            else:
                offset = offset_cap
            st_carry, lt_carry = _us_worksheet(st_net, lt_net, offset)
        else:
            offset = 0.0
            st_carry = lt_carry = 0.0
        rows.append({
            'year': y,
            'net_gain': round(rec['net'], 2),
            'net_st': round(rec['st'], 2),
            'net_lt': round(rec['lt'], 2),
            'dispositions': rec['dispositions'],
            'ordinary_income_offset': round(offset, 2),
            'st_carryover': round(st_carry, 2),
            'lt_carryover': round(lt_carry, 2),
        })
    return {'country': 'usa', 'rows': rows,
            'final_carryforward': round(st_carry + lt_carry, 2),
            'final_st_carryover': round(st_carry, 2),
            'final_lt_carryover': round(lt_carry, 2)}


_money = fmt_money                  # shared report-layer formatter


def render(ledger: Dict[str, Any], cur: str, first_tx_year: Optional[int],
           claimed_used: bool) -> str:
    lines: List[str] = []
    country = ledger['country']
    lines.append(f"CAPITAL-LOSS CARRYOVER LEDGER — {country} "
                 f"(amounts in {cur}, 100% gains/losses, allowed i.e. "
                 f"post-superficial-loss)")
    lines.append("")
    rows = ledger['rows']
    if not rows:
        lines.append("No dispositions found.")
        return "\n".join(lines)
    if country == 'canada':
        header = ("YEAR", "NET GAIN(LOSS)", "APPLIED", "CARRYFWD BAL",
                  "NOTES")
        table = []
        for r in rows:
            notes = []
            if r['available_to_apply'] > 0.005:
                notes.append(f"carryforward available: apply up to "
                             f"{_money(r['available_to_apply'])}")
            for cb in r['carryback_candidates']:
                notes.append(f"{_money(cb['amount'])} can be carried back "
                             f"to {cb['year']} via T1A")
            table.append((str(r['year']), _money(r['net_gain']),
                          _money(r['claimed_applied']),
                          _money(r['carryforward_balance']),
                          "; ".join(notes)))
    else:
        header = ("YEAR", "NET ST", "NET LT", "3K OFFSET",
                  "ST CARRY", "LT CARRY")
        table = [(str(r['year']), _money(r['net_st']), _money(r['net_lt']),
                  _money(r['ordinary_income_offset']),
                  _money(r['st_carryover']), _money(r['lt_carryover']))
                 for r in rows]
    widths = [max(len(header[i]), *(len(row[i]) for row in table))
              for i in range(len(header))]
    text_cols = {0, len(header) - 1} if country == 'canada' else {0}
    def fmt(row):
        return " | ".join(
            (row[i].ljust(widths[i]) if i in text_cols
             else row[i].rjust(widths[i]))
            for i in range(len(row))).rstrip()
    lines.append(fmt(header))
    lines.append("-+-".join("-" * w for w in widths))
    lines.extend(fmt(row) for row in table)
    lines.append("")
    if country == 'canada':
        lines.append(f"  Net-capital-loss carryforward after "
                     f"{rows[-1]['year']}: {_money(ledger['final_carryforward'])}")
        if ledger.get('unmatched_claims'):
            lines.append(f"  warning: {_money(ledger['unmatched_claims'])} "
                         f"of --claimed amounts exceed the losses this "
                         f"history supports — check the claimed file.")
    else:
        lines.append(f"  Carryover after {rows[-1]['year']}: "
                     f"ST {_money(ledger['final_st_carryover'])} + "
                     f"LT {_money(ledger['final_lt_carryover'])}")
    lines.append("")
    lines.append("Notes:")
    lines.append("  - Amounts are 100% capital gains/losses. Canada: apply "
                 "the 50% inclusion rate on Schedule 3 / form T1A; the "
                 "official 'net capital loss' balance CRA tracks is the "
                 "inclusion-rate-adjusted figure.")
    lines.append("  - The ledger reflects what the transaction history "
                 "supports" +
                 (" plus your --claimed records." if claimed_used else
                  "; what you actually claimed on filed returns may "
                  "differ. Record reality with --claimed FILE "
                  "(`YEAR AMOUNT` lines)."))
    if country == 'canada':
        lines.append("  - Carryback candidates are advisory, capped at each "
                     "earlier year's remaining net gain, never "
                     "double-counted across loss years, and only look back "
                     "3 years (ITA 111(1)(b)).")
    else:
        lines.append("  - The $3,000 ordinary-income offset is ASSUMED used "
                     "whenever available; override a year with a --claimed "
                     "line (0 is valid).")
    if first_tx_year is not None and rows and rows[0]['year'] <= first_tx_year:
        lines.append(f"  - warning: this history starts in {first_tx_year} — "
                     f"if you traded before then, earlier gains/losses (and "
                     f"any pre-{first_tx_year} carryforward) are NOT "
                     f"reflected. Reconcile the opening balance against "
                     f"your CRA/IRS records.")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Multi-year capital-loss carryforward/carryback ledger "
                    "over full-history taxjson books.")
    parser.add_argument("files", nargs="+", type=Path, metavar="FILE",
                        help="TAXABLE <account>_base.json files (full "
                             "history, base currency)")
    parser.add_argument("--crypto", action="append", type=Path,
                        default=[], metavar="FILE",
                        help="Crypto base book(s). For USA these run "
                             "in a separate no-wash pass (IRS: §1091 "
                             "does not reach digital assets) matching "
                             "the filing pipeline; for Canada they "
                             "fold into the main run (superficial "
                             "loss covers identical property).")
    parser.add_argument("--sheltered", action="append", type=Path,
                        default=[], help="Sheltered book(s) for wash-window "
                                         "context (repeatable)")
    parser.add_argument("--country", default=None,
                        choices=["canada", "ca", "usa", "us"],
                        help="Country for tax rules (default: canada, with "
                             "a stderr note when omitted)")
    parser.add_argument("--tax-date", choices=["settle", "trade"],
                        default=None,
                        help="Year-attribution basis (default: country-aware)")
    parser.add_argument("--incomplete-history", type=Path, default=None,
                        help="phantoms.json for truncated-history openings")
    parser.add_argument("--claimed", type=Path, default=None,
                        help="`YEAR AMOUNT` lines: losses actually applied "
                             "on filed returns")
    parser.add_argument("--base-currency", default="CAD",
                        help="Label for amounts (default: CAD)")
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text")
    args = parser.parse_args(argv)

    if args.country is None:
        # Silent default was audit finding A4; `taxjson carryover` always
        # passes --country, so the note only reaches direct CLI users.
        note("taxjson-carryover", "--country not given; assuming canada")
        args.country = "canada"

    for p in args.files + args.sheltered:
        if not p.exists():
            print(f"taxjson-carryover: no such file: {p}", file=sys.stderr)
            return 2
    if args.claimed is not None and not args.claimed.exists():
        print(f"taxjson-carryover: no such file: {args.claimed}",
              file=sys.stderr)
        return 2

    transactions = []
    for p in args.files:
        transactions.extend(load_transactions(p))
    sheltered = []
    for p in args.sheltered:
        sheltered.extend(load_transactions(p))

    country = ('usa' if args.country.strip().lower() in ('us', 'usa')
               else 'canada')
    crypto_txs = []
    for cp in args.crypto:
        crypto_txs.extend(load_transactions(cp))
    if country == 'canada' and crypto_txs:
        # Symbol-global ACB pools: crypto symbols don't collide with
        # equity ones, so one blended run is equivalent to the
        # pipeline's separate crypto pass.
        transactions = transactions + crypto_txs
        crypto_txs = []
    # Match the FILING pipeline's basis exactly (2026-09 audit: the
    # ledger computed on a different basis than the return — US
    # multi-account books need per-account FIFO lots, and US crypto
    # loses §1091 entirely).
    req = GainsRequest(country=country, year=None, taxable=True,
                       tax_date=args.tax_date,
                       incomplete_history=args.incomplete_history,
                       phantom_hint=False,
                       per_account_basis=(country == 'usa'))
    try:
        results = run_gains(transactions, sheltered, (), req)
    except (TransferValidationError, _AmbiguousXferErr) as exc:
        print(f"taxjson-carryover: error: {exc}", file=sys.stderr)
        return 1
    nets = yearly_nets(results, req.effective_tax_date())
    if crypto_txs:                       # USA crypto: no-wash pass
        req_c = GainsRequest(country=country, year=None, taxable=True,
                             tax_date=args.tax_date,
                             incomplete_history=args.incomplete_history,
                             phantom_hint=False, no_wash=True,
                             per_account_basis=True)
        try:
            res_c = run_gains(crypto_txs, sheltered, (), req_c)
        except (TransferValidationError, _AmbiguousXferErr) as exc:
            print(f"taxjson-carryover: error: {exc}", file=sys.stderr)
            return 1
        for yr, vals in yearly_nets(
                res_c, req_c.effective_tax_date()).items():
            dst = nets.setdefault(yr, {})
            for k, v in vals.items():
                dst[k] = dst.get(k, 0.0) + v
    claimed = load_claimed(args.claimed)

    if country == 'canada':
        ledger = build_canada_ledger(nets, claimed)
    else:
        ledger = build_usa_ledger(nets, claimed)

    tx_years = [int(t.date[:4]) for t in transactions
                if t.date[:4].isdigit()]
    first_tx_year = min(tx_years) if tx_years else None
    ledger['first_transaction_year'] = first_tx_year
    if results.get('manual_reporting_required'):
        n = len(results['manual_reporting_required'])
        print(f"warning: {n} tainted disposition(s) with phantom cost basis "
              f"are EXCLUDED from the ledger (see "
              f"manual_reporting_required in the gains output) — their "
              f"years' nets are incomplete until resolved.", file=sys.stderr)

    if args.json:
        ledger["currency"] = args.base_currency.upper()
        json.dump(ledger, sys.stdout, indent=2, sort_keys=True)
        print()
    else:
        print(render(ledger, args.base_currency.upper(), first_tx_year,
                     claimed_used=bool(claimed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
