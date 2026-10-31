#!/usr/bin/env python3
"""
taxjson_carryover.py

Multi-year capital-loss carryforward / carryback ledger.

Runs ONE full-history gains pass through lib/pipeline.run_gains (the same
computation the yearly pipeline uses — wash/superficial-loss adjustments,
missing-history openings, tainted exclusion all included) and buckets the ALLOWED
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
loss amount actually applied against that YEAR's return — Canada: the
100% capital loss (line 25300 divided by the inclusion rate, x2 at 50%);
US: the Schedule D line 21 deduction against ordinary income, as far as
taxable income absorbed it (Capital Loss Carryover Worksheet line 4 —
less than line 21 when taxable income is negative). Claims fold into
the running balance (a claim recorded before the loss exists — e.g. a
carryback entered under the target year — is held pending and consumed
when the loss arrives). In a project the claims are taxjson.toml's
`[carryover] claimed = { 2023 = 4000.00 }`, which `tjs carryover`
passes as --claimed-year YEAR=AMOUNT.

Usage:
    taxjson-carryover margin_base.json [more_base.json ...]
        --country canada [--sheltered sheltered_base.json]
        [--incomplete-history PROJECT_DIR] [--claimed FILE | --claimed-year YEAR=AMOUNT ...]
        [--tax-date settle|trade] [--base-currency CAD] [--json]

Or through the project wrapper: `tjs carryover`.
"""

from taxjson.lib.stage_msg import emit_line
import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib.cli_diag import guard_main, read_text_utf8, tax_year
from taxjson.lib.country import add_country_argument, refuse_foreign_flags
from taxjson.lib.core import AmbiguousTransferDateError as _AmbiguousXferErr
from taxjson.lib.pipeline import (GainsRequest, TransferValidationError,
                                  loss_overrides_from_args, run_gains)
from taxjson.lib.report_model import fmt_money
from taxjson.bin.taxjson_convert_currency import norm_currency

_INCOME_ACTIONS = ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST', 'FEE')
# The §1211(b) cap, from the one constant the estimate uses too
# (tax-logic US-RPT-07 states it).
from taxjson.lib.tax_estimate import (  # noqa: E402
    US_ORDINARY_LOSS_CAP as US_ORDINARY_OFFSET, section_1256_split)


# A grouped amount's lead group has no leading zero ('0,125' is not
# 125): the same rule the S055-08 fix put in the broker parsers
# (audit A2-0667).
_AMOUNT_RE = re.compile(
    r"\$?([1-9]\d{0,2}(?:,\d{3})+|\d+)(\.\d+)?|\$?\.\d+")


def _parse_amount(text: str) -> float:
    """A claimed amount as the T1 / notice of assessment prints it:
    plain `1234.56`, grouped `1,234.56`, optionally with a leading `$`
    (S027-13: those forms were dropped with a warning, overstating the
    carryforward). A malformed grouping (`12,34`) is refused — never
    guessed."""
    if _AMOUNT_RE.fullmatch(text) is None:
        return float(text)          # 'nan', '-5', '1e3' -> float()'s rules
    return float(text.lstrip("$").replace(",", ""))


# A claim's YEAR must be a plausible return year — the range
# `tjs init` / [settings] year accept. A typo ('2205', '0') became a
# spurious ledger row that silently consumed the claim (S027-23).
CLAIM_MIN_YEAR = 1900


def _claim_max_year() -> int:
    from datetime import date as _date
    return _date.today().year + 1


def load_claimed(path: Optional[Path],
                 ignored: Optional[List[str]] = None) -> Dict[int, float]:
    """`YEAR AMOUNT` lines -> {year: amount}. A line that cannot be read
    is warned about and skipped; when `ignored` is given each skipped
    line is appended to it (`path:lineno: text`) so the caller can
    report the ledger as incomplete (S001-04: the checklist said
    'present' over a file whose only line was dropped). A UTF-8 BOM
    (Notepad) is not part of the first line (S027-18)."""
    claimed: Dict[int, float] = {}
    if path is None:
        return claimed
    import math
    hi = _claim_max_year()
    for lineno, line in enumerate(read_text_utf8(path).splitlines(), 1):
        stripped = line.split('#', 1)[0].strip()
        if not stripped:
            continue
        parts = stripped.split()
        why = "expected `YEAR AMOUNT` (amount >= 0)"
        try:
            year, amount = int(parts[0]), _parse_amount(parts[1])
            # not-isfinite: 'nan' passed the `< 0` check and poisoned
            # the ledger — APPLIED ballooned to the whole balance with
            # zero diagnostics (REVIEW #38).
            if len(parts) != 2 or amount < 0 or not math.isfinite(amount):
                raise ValueError
            if not CLAIM_MIN_YEAR <= year <= hi:
                why = (f"YEAR {year} is not a plausible tax year "
                       f"({CLAIM_MIN_YEAR}..{hi})")
                raise ValueError
        except (ValueError, IndexError):
            from taxjson.lib.out import warn as _warn
            _warn(f"{path.name}:{lineno}: {why}, got {stripped!r} — line "
                  f"ignored")
            if ignored is not None:
                ignored.append(f"{path.name}:{lineno}: {stripped}")
            continue
        claimed[year] = claimed.get(year, 0.0) + amount
    return claimed


def yearly_nets(results: dict, tax_date: str, s1256: bool = False,
                filed_gap: bool = False) -> Dict[int, Dict[str, float]]:
    """Bucket ALLOWED disposition gains by tax year, using the same
    date-basis rule as the pipeline's --year filter (dispositions key on
    date_settle under the settle basis; income rows are not dispositions
    and are excluded entirely). Tainted rows were already routed to
    manual_reporting_required by run_gains. `s1256` (a US ledger): the
    P/L of §1256 contracts (lib/futures.section_1256_kind) goes 40% to
    the year's short-term and 60% to its long-term net, whatever its
    holding period, as Form 6781 lines 8 and 9 put it on Schedule D
    (US-FUT-03; backlog TJ-0466); `s1256` in the year's dict is its
    net. `filed_gap` (a Canadian ledger): each year's `filed_gap` is its
    net on Schedule 3 less this net: Schedule 3 rounds a gain to the cent
    per row, one row per line and symbol (taxjson_form_export
    build_schedule3; build_canada_ledger; backlog TJ-1195)."""
    from taxjson.lib.futures import section_1256_kind
    date_key = 'date_settle' if tax_date == 'settle' else 'date'
    nets: Dict[int, Dict[str, float]] = {}
    schedule_rows: Dict[int, Dict[Tuple[str, str], float]] = {}
    from taxjson.bin.taxjson_form_export import (_cents, _line_key,
                                                 property_class,
                                                 schedule3_line,
                                                 schedule3_period)
    # The gains-row contract (issue #17): a disposition without its gain
    # or units is refused, never left out of a year's net.
    from taxjson.lib.json_input import check_gains_rows, gains_row_kind
    check_gains_rows(results.get('transactions'), "gains results")
    for t in results.get('transactions', []):
        kind = gains_row_kind(t)
        if kind == 'income':
            continue
        if kind == 'manual' and 'gain' not in t:
            continue
        date = t.get(date_key) or t.get('date') or ''
        if len(date) < 4 or not date[:4].isdigit():
            continue
        year = int(date[:4])
        rec = nets.setdefault(year, {'net': 0.0, 'st': 0.0, 'lt': 0.0,
                                     'dispositions': 0, 'subcent': 0})
        gain = float(t.get('gain') or 0.0)
        rec['net'] += gain
        rec['dispositions'] += 1
        # A gain in whole cents rounds to itself on Schedule 3: only a
        # sub-cent one can leave the filed loss apart from the ledger's
        # (build_canada_ledger's slack; backlog TJ-1018).
        if abs(gain * 100.0 - round(gain * 100.0)) > 1e-4:
            rec['subcent'] += 1
        if filed_gap:
            _key = (schedule3_line(_line_key(property_class(t)), year,
                                   schedule3_period(t, year, date_key))['key'],
                    str(t.get('symbol') or '?'))
            _rows = schedule_rows.setdefault(year, {})
            _rows[_key] = _rows.get(_key, 0.0) + gain
        if s1256 and section_1256_kind(str(t.get('symbol') or '')):
            rec['s1256'] = rec.get('s1256', 0.0) + gain
            _st, _lt = section_1256_split(gain)
            rec['st'] += _st
            rec['lt'] += _lt
        elif t.get('term') == 'SHORT_TERM':
            rec['st'] += gain
        elif t.get('term') == 'LONG_TERM':
            rec['lt'] += gain
    for year, _rows in schedule_rows.items():
        nets[year]['filed_gap'] = (sum(_cents(v) for v in _rows.values())
                                   - nets[year]['net'])
    return nets


def build_canada_ledger(nets: Dict[int, Dict[str, float]],
                        claimed: Dict[int, float],
                        carried: Optional[Dict[int, float]] = None,
                        locked_back: Optional[Dict[int, float]] = None,
                        locked_applied: Optional[Dict[int, float]] = None,
                        own_year: Optional[int] = None
                        ) -> Dict[str, Any]:
    """`carried`: {year: balance a close-year lock recorded as carried
    out of that year} — at the end of that year the running balance
    becomes the recorded one (the lock is the record; the books of a
    later project rebuild earlier years only partly). Claims still
    pending stay pending (a T1A carry-back recorded under its target
    year). `locked_back`: {year: the carry-back that lock recorded
    (carried_back; 0 when it records none)} — a locked year meets
    claims under earlier years only up to it, and the rest of those
    claims stays pending for a later year's loss (CA-CARRY-04).
    `locked_applied`: {year: the earlier losses that lock recorded as
    applied on its return} — with no claim under that year, the row's
    APPLIED is it, and that much of the year's gain is used: no
    carryforward left to apply to it, no carry-back room (backlog
    TJ-0052). `own_year`: the project year when `carried` holds its own
    lock's balance (a closed year; backlog TJ-0433): its row shows what
    the lock applied and carried, but its carry-back is the claims'
    (close-year and handoff read it, a re-close included), and a
    carry-back claimed beyond the one the lock took comes off the
    balance it carried."""
    carried = carried or {}
    locked_back = locked_back or {}
    locked_applied = locked_applied or {}
    # Union of book years and CLAIM years: a claim is applied on the
    # year of the RETURN it appears on, which may have no dispositions
    # in the broker book at all (T3 distributions, a no-trade year).
    # Iterating only disposition years silently ignored those claims —
    # the carryforward stayed overstated with no warning (REVIEW #23).
    years = sorted(set(nets) | set(claimed) | set(carried))
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
    # Claims whose carryback window closed before any loss met them.
    expired: Dict[int, float] = {}
    # Remaining carryback capacity per gain year (advisory; reduced by
    # claims against that year and by earlier loss-years' suggestions so
    # two loss years never point at the same dollar of gain).
    cb_capacity = {y: max(0.0, nets[y]['net']) for y in sorted(nets)}
    # The return files a loss as the SUM OF PER-ROW-ROUNDED Schedule 3
    # amounts; this ledger sums unrounded gains. A claim equal to the
    # filed loss can differ from the ledger's by up to half a cent per
    # row — a fixed 0.005 slack left a phantom cents carryforward or
    # warned that a correct claim exceeds the losses (S027-10, S028-00).
    # The slack therefore grows with the dispositions behind the
    # balance (an upper bound on its Schedule 3 rows) — only those whose
    # gain is not whole cents: rows in whole cents foot exactly however
    # they are grouped, and a 0.005-per-row slack zeroed the 0.02 left
    # of 100.00 of such losses after a 99.98 claim (backlog TJ-1018).
    # A year with its `filed_gap` (yearly_nets) needs no bound: only that
    # real gap is absorbed, and only on its side, or a partial claim
    # landing within the bound lost what it left (backlog TJ-1195).
    # `filed_over`: the filed loss above the ledger's (below it, when
    # negative). `_EPS` absorbs the float residue of an exact claim.
    slack = 0.0
    filed_over = 0.0
    _EPS = 1e-6
    # The year before is a lock's record: the balance carried into this
    # one is the record's, not a rebuild (a partial history rebuilds
    # less).
    prev_from_lock = False
    for y in years:
        net = nets.get(y, _ZERO)['net']
        loss = max(0.0, -net)
        if loss:
            balance += loss
            _rec = nets.get(y, _ZERO)
            # A year from another source (a lock, a return row alone)
            # names no sub-cent rows: each of its dispositions counts.
            if 'filed_gap' in _rec:
                filed_over -= float(_rec['filed_gap'])
            else:
                slack += 0.005 * (int(_rec['subcent'] or 0)
                                  if 'subcent' in _rec
                                  else max(1, int(_rec['dispositions'] or 0)))
        if claimed.get(y):
            pending_by_year[y] = pending_by_year.get(y, 0.0) + claimed[y]
        # A claim recorded against year C can only be met by a loss
        # of a year up to C+3: a net capital loss carries BACK at most
        # three years (ITA s.111(1)(b); form T1A). Past that window a
        # still-pending claim can never be matched — a later loss must
        # not silently absorb it (S001-03: a pre-book 2021 claim ate
        # 3,000 of a 2025 loss's carryforward).
        for _cy in [c for c in pending_by_year if y > c + 3]:
            expired[_cy] = expired.get(_cy, 0.0) + pending_by_year.pop(_cy)
        # Fold claims oldest-recorded-first: each consumed dollar
        # shelters the gains of ITS recorded year, whose carryback
        # capacity it therefore consumes.
        applied = 0.0
        # The part met from claims recorded against EARLIER years: a
        # T1A carry-back of this year's loss (a claim pending from year
        # C found no balance until now). close-year takes it out of the
        # balance its lock carries forward.
        carried_back = 0.0
        backs: List[Tuple[int, float]] = []
        for _cy in sorted(pending_by_year):
            if balance <= 0.005:
                break
            take = min(balance, pending_by_year[_cy])
            if take <= 0.0:
                continue
            balance -= take
            applied += take
            if _cy < y:
                carried_back += take
                backs.append((_cy, take))
            pending_by_year[_cy] -= take
            if _cy in cb_capacity:
                cb_capacity[_cy] = max(0.0, cb_capacity[_cy] - take)
            if 0.0 < pending_by_year[_cy] <= (slack + max(0.0, filed_over)
                                              + _EPS):
                # The claim is the filed (per-row-rounded) loss and the
                # unrounded ledger came out a few cents short of it.
                pending_by_year[_cy] = 0.0
        if applied > 0.0 and 0.0 < balance <= (slack + max(0.0, -filed_over)
                                               + _EPS):
            # ...or a few cents over it: the filed claim used it all.
            balance = 0.0
        if balance <= 0.005:
            slack = filed_over = 0.0
        if y in carried and y != own_year:
            # The lock is the record of this year's return: of the
            # claims under earlier years, it took only the carry-back it
            # recorded. The rest of a claim (the total applied on that
            # earlier return, `claimed` is one amount per year) is met
            # by a later year's loss — resetting the balance to the
            # lock's dropped it, and the later lock carried it forward
            # (workflow audit 2026-10-23 item 2). The latest claims
            # taken give back first: the fold took the oldest first.
            # The target year's carry-back capacity stays used: the
            # claim is still on its return.
            excess = carried_back - max(0.0, float(locked_back.get(y)
                                                   or 0.0))
            for _i in range(len(backs) - 1, -1, -1):
                if excess <= 0.005:
                    break
                _cy, take = backs[_i]
                give = min(take, excess)
                pending_by_year[_cy] = pending_by_year.get(_cy, 0.0) + give
                applied -= give
                carried_back -= give
                excess -= give
                backs[_i] = (_cy, take - give)
        pending_by_year = {k: v for k, v in pending_by_year.items()
                           if v > 0.005}
        rebuilt_balance = None
        over_balance = None
        # The losses the lock applied on this year's return: with no
        # claim under the year the row showed APPLIED 0.00 while its
        # balance fell by them, and offered them again (TJ-0052).
        lock_applied = 0.0
        if y in carried:
            rebuilt_balance = balance
            balance = max(0.0, float(carried[y]))
            if y == own_year:
                balance = max(0.0, balance - max(
                    0.0, carried_back - float(locked_back.get(y) or 0.0)))
            slack = filed_over = 0.0
            if y not in claimed:
                lock_applied = min(max(0.0, float(locked_applied.get(y)
                                                  or 0.0)),
                                   max(0.0, net))
                applied += lock_applied
                if y in cb_capacity:
                    cb_capacity[y] = max(0.0, cb_capacity[y]
                                         - lock_applied)
                if prev_from_lock and lock_applied > rebuilt_balance + 0.005:
                    # The lock applied more than the record before it
                    # carries in: that year closed again since (an
                    # amendment) and this return is built on its old
                    # balance (backlog TJ-0430).
                    over_balance = (lock_applied, rebuilt_balance)

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
        row = {
            'year': y,
            'net_gain': round(net, 2),
            'dispositions': nets.get(y, _ZERO)['dispositions'],
            'claimed_applied': round(applied, 2),
            'carried_back': round(carried_back, 2),
            'carryforward_balance': round(balance, 2),
            # The gain the year's return already sheltered — by the
            # losses its lock applied, or by its own [carryover]
            # claimed entry (the total applied on that return, met now
            # or by a later year's carry-back) — is not offered again
            # (backlog TJ-0052, TJ-0223).
            'available_to_apply': round(min(balance, max(
                0.0, net - lock_applied
                - max(0.0, float(claimed.get(y) or 0.0)))), 2)
            if net > 0 else 0.0,
            'carryback_candidates': carryback,
        }
        if rebuilt_balance is not None:
            row['balance_from_lock'] = True
            # The books' balance at the year end, the losses the lock
            # applied taken out as a claim would be.
            row['rebuilt_balance'] = round(max(
                0.0, rebuilt_balance - lock_applied), 2)
        if over_balance is not None:
            row['applied_over_balance'] = {
                'applied': round(over_balance[0], 2),
                'carried_in': round(over_balance[1], 2)}
        prev_from_lock = y in carried
        if carried_back > 0.005:
            # The carry-back by the year it went to: handoff checks a
            # claim under the closed year less the part this year's loss
            # carries back to it (backlog TJ-0078).
            _to: Dict[str, float] = {}
            for _cy, take in backs:
                _to[str(_cy)] = _to.get(str(_cy), 0.0) + take
            row['carried_back_to'] = {k: round(v, 2) for k, v in
                                      sorted(_to.items()) if v > 0.005}
        rows.append(row)
    out = {'country': 'canada', 'rows': rows,
           'final_carryforward': round(balance, 2)}
    _unmatched = sum(pending_by_year.values()) + sum(expired.values())
    if _unmatched > 0.005:
        out['unmatched_claims'] = round(_unmatched, 2)
    _exp = {str(k): round(v, 2) for k, v in sorted(expired.items())
            if v > 0.005}
    if _exp:
        out['expired_claims'] = _exp
    # A claim above its year's net gain: a net capital loss applied in a
    # year (carried forward or back) is at most that year's taxable
    # capital gains (ITA s.111(1.1)). The ledger applies the claim as
    # recorded (slip gains and the FX gain on cash are not in the nets,
    # so the return can have more gain), and says so: the estimate caps
    # it and carries the rest (backlog TJ-0432). A year with no
    # disposition in these books is not judged.
    _over: Dict[str, Dict[str, float]] = {}
    for c, amt in sorted(claimed.items()):
        rec = nets.get(c) or _ZERO
        n_disp = int(rec.get('dispositions') or 0)
        if not n_disp:
            continue
        gain = max(0.0, float(rec.get('net') or 0.0))
        if float(amt or 0.0) > gain + 0.005 * n_disp + 0.005:
            _over[str(c)] = {'claimed': round(float(amt), 2),
                             'net_gain': round(gain, 2),
                             'excess': round(float(amt) - gain, 2)}
    if _over:
        out['claims_over_gain'] = _over
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
                     claimed: Dict[int, float],
                     carried: Optional[Dict[int, Any]] = None,
                     locked_deduction: Optional[Dict[int, float]] = None,
                     through_year: Optional[int] = None
                     ) -> Dict[str, Any]:
    """`carried`: {year: (st, lt) carryover a close-year lock recorded
    out of that year} — the running carryover becomes it at the end of
    that year (US-CARRY-04). The row's 3K OFFSET is then the deduction
    that lock counted as used (`locked_deduction`: {year: amount}), or
    for a lock that records none, what its balance implies (the year's
    loss less the balance carried): the row adds up to the balance it
    shows, never the [carryover] claimed or assumed offset beside a
    balance that did not take it (backlog TJ-0464). `through_year`
    (the project year): the walk goes on to it, so a loss still carried
    after the last year with a sale or a claim takes each later year's
    deduction up to that year (backlog TJ-0896)."""
    carried = carried or {}
    locked_deduction = locked_deduction or {}
    # Every RETURN year matters, not just disposition years: while a
    # carryover exists, each intervening year's return absorbs up to
    # $3,000 against ordinary income (or the [carryover] claimed
    # override, 0 included), so iterating `sorted(nets)` alone silently
    # skipped gap years and overstated the final carryover. Walk the
    # full span of book+claim years; emit a row for a no-disposition
    # year only when it actually does something (carry enters it or a
    # claim targets it). This is the USA twin of the Canada REVIEW #23
    # union fix.
    all_years = set(nets) | set(claimed) | set(carried)
    last = max(all_years) if all_years else None
    if last is not None and through_year is not None:
        # A trailing year with no sale and no claim still takes its
        # deduction while a carryover exists (backlog TJ-0896).
        last = max(last, int(through_year))
    years = list(range(min(all_years), last + 1)) if all_years else []
    _ZERO = {'net': 0.0, 'st': 0.0, 'lt': 0.0, 'dispositions': 0}
    rows: List[Dict[str, Any]] = []
    st_carry = lt_carry = 0.0        # positive loss magnitudes
    for y in years:
        rec = nets.get(y, _ZERO)
        if (y not in nets and y not in claimed and y not in carried
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
                    from taxjson.lib.out import warn as _warn
                    _warn(f"claimed {claimed[y]:,.2f} for {y} exceeds the "
                          f"{offset_cap:,.2f} the worksheet supports — "
                          f"capped")
            else:
                offset = offset_cap
            st_carry, lt_carry = _us_worksheet(st_net, lt_net, offset)
        else:
            offset = 0.0
            st_carry = lt_carry = 0.0
        row = {
            'year': y,
            'net_gain': round(rec['net'], 2),
            'net_st': round(rec['st'], 2),
            'net_lt': round(rec['lt'], 2),
            'dispositions': rec['dispositions'],
            'ordinary_income_offset': round(offset, 2),
        }
        if y in carried:
            row['balance_from_lock'] = True
            row['rebuilt_st_carryover'] = round(st_carry, 2)
            row['rebuilt_lt_carryover'] = round(lt_carry, 2)
            st_carry, lt_carry = (max(0.0, float(carried[y][0])),
                                  max(0.0, float(carried[y][1])))
            if y in locked_deduction:
                used = max(0.0, float(locked_deduction[y]))
            else:
                used = max(0.0, min(US_ORDINARY_OFFSET,
                                    -combined - st_carry - lt_carry))
            if abs(used - offset) > 0.005:
                # What [carryover] claimed (claimed_offset) or the
                # assumption would have deducted.
                row['claimed_offset' if y in claimed
                    else 'assumed_offset'] = round(offset, 2)
                row['ordinary_income_offset'] = round(used, 2)
        row['st_carryover'] = round(st_carry, 2)
        row['lt_carryover'] = round(lt_carry, 2)
        if abs(float(rec.get('s1256') or 0.0)) > 0.005:
            # The §1256 net in NET ST / NET LT, and the parts: Form
            # 6781's 40% / 60% (US-FUT-03; backlog TJ-0466), or all
            # short-term where a lock closed before the split carried it
            # so (backlog TJ-0611).
            s = round(float(rec['s1256']), 2)
            row['section_1256'] = s
            if rec.get('s1256_unsplit'):
                row['section_1256_unsplit'] = True
                row['section_1256_short_term'] = s
                row['section_1256_long_term'] = 0.0
            else:
                lt = round(section_1256_split(s)[1], 2)
                row['section_1256_short_term'] = round(s - lt, 2) + 0.0
                row['section_1256_long_term'] = lt + 0.0
        rows.append(row)
    return {'country': 'usa', 'rows': rows,
            'final_carryforward': round(st_carry + lt_carry, 2),
            'final_st_carryover': round(st_carry, 2),
            'final_lt_carryover': round(lt_carry, 2)}


_money = fmt_money                  # shared report-layer formatter


# ------------------------------------------------------- filed-year locks

class LockUnreadable(ValueError):
    """A filed-year lock that could not be read or holds no usable
    figure."""


# The gain lines a return carries (close-year's form_lines keys).
# 10690/10694: the 2024 form's Period 1 lines (A2-0166).
_GAIN_LINES = {'canada': ('13200', '15300', '15301', '10690', '10694'),
               'usa': ('I_gain', 'II_gain')}

LOCK_SOURCE_TEXT = {
    'filed_totals': "the total filed with another tool: filed_totals",
    'return_row': "the RETURN row close-year recorded: return_row",
    'form_lines': "the filed gain lines: form_lines",
    'realized': "the lock's realized total",
}


def _finite(v: Any, what: str) -> float:
    import math
    x = float(v)
    if not math.isfinite(x):
        raise ValueError(f"{what} is not a finite number ({v!r})")
    return x


def _form_lines(doc: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """The lock's return lines (every account's form_lines added up),
    None when an account records none. ValueError on a line that is not
    a finite number."""
    accts = doc.get('accounts')
    if not (isinstance(accts, dict) and accts and all(
            isinstance(a, dict) and isinstance(a.get('form_lines'), dict)
            for a in accts.values())):
        return None
    lines: Dict[str, float] = {}
    for a in accts.values():
        for k, v in a['form_lines'].items():
            lines[str(k)] = lines.get(str(k), 0.0) + _finite(
                v, f"form_lines {k}")
    return lines


def us_lock_1256(doc: Dict[str, Any]) -> Tuple[float, bool]:
    """(the §1256 net a US lock records — its Form 6781 line, form_lines
    `6781_gain`; 0.0 for a lock without one — and whether its carryover
    took that net all short-term: a carry-forward block without the
    Form 6781 parts, `section_1256_long_term`, was closed before taxjson
    split it 60/40 (US-FUT-03; backlog TJ-0611)). Only a lock that
    carried a loss out counts: the split moves a carryover between its
    terms, never its total, so one that carried none is right as it
    is. ValueError / TypeError on a figure that is not a number."""
    lines = _form_lines(doc)
    s = float((lines or {}).get('6781_gain') or 0.0)
    cl = (doc.get('carryforwards') or {}).get('capital_loss') \
        if isinstance(doc.get('carryforwards'), dict) else None
    unsplit = (isinstance(cl, dict) and abs(s) > 0.005
               and 'section_1256_long_term' not in cl
               and (float(cl.get('st_closing') or 0.0)
                    + float(cl.get('lt_closing') or 0.0)) > 0.005)
    return s, unsplit


def us_lock_gains(doc: Dict[str, Any]) -> Tuple[float, float, str]:
    """(short-term, long-term, source) a US lock says was FILED: the Form
    8949 Part I / Part II gains with the §1256 net of Form 6781 (form
    lines `6781_gain`) added 40% short-term and 60% long-term (lines 8
    and 9; US-FUT-03), or all short-term where the lock's carryover took
    it so (closed before the split: us_lock_1256) — the figures its
    carryover was built on (backlog TJ-0611: the row left the §1256 net
    out, and did not add up to the balance carried); else its totals (a
    gain with no term short-term, US-RPT-07). KeyError / TypeError /
    ValueError on a lock without usable figures."""
    lines = _form_lines(doc)
    if lines is not None:
        st, lt = lines.get('I_gain', 0.0), lines.get('II_gain', 0.0)
        s, unsplit = us_lock_1256(doc)
        if unsplit:
            st += s
        elif abs(s) > 0.005:
            s_st, s_lt = section_1256_split(s)
            st, lt = st + s_st, lt + s_lt
        return st, lt, 'form_lines'
    totals = doc['totals']
    realized = _finite(totals['realized'], 'totals.realized')
    st = _finite(totals.get('st_gain') or 0.0, 'totals.st_gain')
    lt = _finite(totals.get('lt_gain') or 0.0, 'totals.lt_gain')
    st += realized - (st + lt)
    return st, lt, 'realized'


def lock_figure(path: Path, country: str) -> Dict[str, Any]:
    """What a close-year lock says was FILED for its year: {figure,
    source, st, lt, dispositions}. The figure is, by preference, the
    gain the return reported with another tool (filed_totals.gain;
    Canada — it has no term split), else (Canada) the RETURN row
    close-year recorded (return_row.realized), else the sum of the
    return's gain lines (form_lines: Schedule 3 13200/15300/15301 and
    2024's Period 1 10690/10694, Form 8949 Part I/II
    — per-row rounded, what the return carries; audit A2-1132), else
    the lock's realized total. A lock that fails the one check every
    reader of a lock applies (taxjson_filed.read_lock: unreadable,
    damaged — its carryforwards block too — or closed under the other
    country's law), whose filed dispositions hold another year's rows
    (backlog TJ-0347), or whose numbers are missing or not finite (NaN,
    Infinity), raises LockUnreadable naming the lock and the fix — it
    never silently matches (A2-0336, A2-1131; 2026-10-25 audit
    year-end#1, robustness#1)."""
    from taxjson.bin import taxjson_filed
    try:
        doc = taxjson_filed.read_lock(Path(path), {"country": country},
                                      prior=True, carried=True)
    except taxjson_filed.LockProblem as e:
        raise LockUnreadable(str(e)) from None
    try:
        totals = doc.get('totals')
        if not isinstance(totals, dict):
            raise ValueError("no totals table")
        realized = _finite(totals['realized'], 'totals.realized')
        lines = _form_lines(doc)
        ft = doc.get('filed_totals')
        ft_gain = (_finite(ft['gain'], 'filed_totals.gain')
                   if isinstance(ft, dict) and ft.get('gain') is not None
                   else None)
        out: Dict[str, Any] = {
            # The year the lock records (read_lock held it to the
            # file's name): a YEAR=PATH binding is checked against it
            # (backlog TJ-0904).
            'year': (int(doc['year']) if isinstance(doc.get('year'), int)
                     and not isinstance(doc.get('year'), bool)
                     else taxjson_filed.file_year(Path(path))),
            'dispositions': int(totals.get('dispositions') or 0),
            'realized': realized, 'filed_totals_gain': ft_gain,
            # Taken before its year ended (close-year --force): a
            # snapshot, not the record of a filed return.
            'partial': bool(taxjson_filed.partial_year_note(
                doc, doc.get('year')))}
        if country == 'usa':
            st, lt, source = us_lock_gains(doc)
            out.update(figure=st + lt, st=st, lt=lt, source=source)
            if source == 'form_lines':
                # The §1256 net in st / lt (TJ-0466, TJ-0611), and
                # whether the lock carried it all short-term.
                s, unsplit = us_lock_1256(doc)
                if abs(s) > 0.005:
                    out['section_1256'] = s
                    if unsplit:
                        out['section_1256_unsplit'] = True
        else:
            # The RETURN row close-year recorded (`tjs sum`'s, all
            # the taxable accounts' Schedule 3 rows together): what
            # close-year and `tjs years` show (TJ-0192, TJ-0208).
            # check-filed does not recompute it, but read_lock refuses
            # one that is not a finite number (lock_shape_problem,
            # backlog TJ-0274); the type check stays for a hand-built
            # document.
            rr = (doc.get('return_row') or {}).get('realized') \
                if isinstance(doc.get('return_row'), dict) else None
            rr_gain = (float(rr) if isinstance(rr, (int, float))
                       and not isinstance(rr, bool)
                       and math.isfinite(float(rr)) else None)
            if ft_gain is not None:
                fig, source = ft_gain, 'filed_totals'
            elif rr_gain is not None:
                fig, source = rr_gain, 'return_row'
            elif lines is not None:
                fig = sum(lines.get(k, 0.0) for k in _GAIN_LINES['canada'])
                source = 'form_lines'
            else:
                fig, source = realized, 'realized'
            out.update(figure=fig, st=0.0, lt=0.0, source=source)
            # The year's slip capital gains (T5 box 18) close-year
            # recorded beside the RETURN row: part of the year's net,
            # the carry-back room it offers (backlog TJ-0898).
            if doc.get('slip_gains') is not None:
                out['slip_gains'] = _finite(doc['slip_gains'], 'slip_gains')
        # The balance close-year recorded as carried into the next year
        # (lib/carryforward.record_block; tax-logic CA-CARRY-04 /
        # US-CARRY-04). None for a lock written before it did.
        cf = doc.get('carryforwards')
        if isinstance(cf, dict):
            if country == 'usa' and isinstance(cf.get('capital_loss'),
                                               dict):
                c = cf['capital_loss']
                out['carried'] = (_finite(c['st_closing'], 'st_closing'),
                                  _finite(c['lt_closing'], 'lt_closing'))
                # The deduction it counted as used (None for a lock
                # written before it recorded one; backlog TJ-0464).
                if c.get('deduction_used') is not None:
                    out['deduction'] = _finite(c['deduction_used'],
                                               'deduction_used')
            elif country != 'usa' and isinstance(
                    cf.get('net_capital_loss'), dict):
                out['carried'] = _finite(cf['net_capital_loss']['closing'],
                                         'net_capital_loss.closing')
                # The T1A carry-back close-year took out of it (0 when
                # it records none: build_canada_ledger's locked_back).
                out['carried_back'] = _finite(
                    cf['net_capital_loss'].get('carried_back') or 0.0,
                    'net_capital_loss.carried_back')
                # ...and the earlier losses it applied (locked_applied).
                out['applied'] = _finite(
                    cf['net_capital_loss'].get('applied') or 0.0,
                    'net_capital_loss.applied')
    except (KeyError, TypeError, ValueError) as e:
        raise LockUnreadable(
            f"{path}: no usable filed figure ({type(e).__name__}: {e}) — "
            f"{taxjson_filed.LOCK_FIX}") from None
    return out

# S028-03: what NET GAIN(LOSS) leaves out. A net capital loss nets
# every taxable capital gain of the year (ITA 111(8) "net capital
# loss"), including slip gains and the s.39(1.1) FX gain.
SCOPE_NOTE = {
    'canada': ("NET GAIN(LOSS) counts the dispositions in these books "
               "(plus the T5 box 18 dividends [[capital_gains_dividends]] "
               "names, when `tjs carryover` passes them). Other "
               "capital gains reported on slips (T3 box 21, T5 "
               "box 18 -> lines 17400/17600) and the ITA s.39(1.1) FX "
               "gain or loss on foreign cash (line 15300, `tjs "
               "fx-cash`) are NOT included; in a loss year they change "
               "the net capital loss, so the carryforward and the "
               "carryback caps shown are off by that amount."),
    'usa': ("Net ST/LT count the dispositions in these books only. "
            "Capital gain distributions (1099-DIV box 2a -> Schedule D "
            "line 13) are NOT included; in a loss year they change the "
            "carryover shown."),
}


def _authority(country: str) -> str:
    """Whose carryover balance the filed return feeds (re-audit A2-1246
    / A2-1293: a US ledger named CRA's)."""
    return ("the IRS Schedule D carryover" if country == 'usa'
            else "CRA's loss balance")


# Essentials first (docs/output-style.md): with --brief (what `tjs
# carryover` passes unless --details) a message is its headline and the
# ledger is render_brief; the standalone tool prints everything.
_DETAILS = True


def _dl(lines: List[str]) -> List[str]:
    """A message's detail lines: with --details only."""
    return list(lines) if _DETAILS else []


def _no_rows(ledger: Dict[str, Any]) -> str:
    """The ledger's line when no year has a disposition with a known
    cost: one whose unknown-cost sales were all excluded is not "no
    dispositions" (backlog TJ-0323)."""
    n = len(ledger.get('excluded_unknown_cost') or [])
    if n:
        return (f"No disposition with a known cost: {n} with an unknown "
                f"cost excluded (no purchase in your files).")
    return "No dispositions found."


def render_brief(ledger: Dict[str, Any], cur: str,
                 first_tx_year: Optional[int]) -> str:
    """The default view: a legend, the table, the balance, then one
    `! ` line per thing to act on and the pointer to --details (the
    NOTES `render` prints)."""
    from taxjson.lib import out as _out
    country = ledger['country']
    rule = 'wash-sale' if country == 'usa' else 'superficial-loss'
    lines = [f"CAPITAL-LOSS CARRYOVER LEDGER — {country}, {cur}"]
    rows = ledger['rows']
    if not rows:
        lines.append(_no_rows(ledger))
        return "\n".join(lines)
    if country == 'canada':
        lines.append(f"100% amounts after the {rule} rule; CARRYFWD "
                     f"BAL: the net capital loss left.")
        header = ("YEAR", "NET GAIN(LOSS)", "APPLIED", "CARRYFWD BAL")
        table = [(str(r['year']), _money(r['net_gain']),
                  _money(r['claimed_applied']),
                  _money(r['carryforward_balance'])) for r in rows]
        aligns = ["<", ">", ">", ">"]
    else:
        lines.append(f"100% amounts, after the {rule} rule; 3K OFFSET: "
                     f"used against ordinary income.")
        header = ("YEAR", "NET ST", "NET LT", "3K OFFSET",
                  "ST CARRY", "LT CARRY")
        table = [(str(r['year']), _money(r['net_st']), _money(r['net_lt']),
                  _money(r['ordinary_income_offset']),
                  _money(r['st_carryover']), _money(r['lt_carryover']))
                 for r in rows]
        aligns = ["<", ">", ">", ">", ">", ">"]
    lines.extend(_out.fit_table(header, table, aligns=aligns))
    lines.append("")
    _final_year = ledger.get('final_year', rows[-1]['year'])
    if country == 'canada':
        lines.append(f"Net-capital-loss carryforward after {_final_year}: "
                     f"{_money(ledger['final_carryforward'])}")
    else:
        _st = ledger['final_st_carryover']
        _lt = ledger['final_lt_carryover']
        lines.append(f"Carryover after {_final_year}: "
                     f"{_money(round(_st + _lt, 2))} (ST {_money(_st)} + "
                     f"LT {_money(_lt)})")
    acts: List[str] = []
    cmd = "tjs carryover --details"
    if country == 'canada':
        for r in rows:
            if r['available_to_apply'] > 0.005:
                acts.append(f"{r['year']}: apply up to "
                            f"{_money(r['available_to_apply'])} of the "
                            f"carryforward")
            for cb in r['carryback_candidates']:
                acts.append(f"{r['year']}: {_money(cb['amount'])} can be "
                            f"carried back to {cb['year']} (T1A)")
        if ledger.get('unmatched_claims'):
            acts.append(f"{_money(ledger['unmatched_claims'])} claimed "
                        f"beyond the losses this history supports")
        for _y, _amt in (ledger.get('expired_claims') or {}).items():
            acts.append(f"{_money(_amt)} claimed for {_y} met no loss "
                        f"in these books")
        for _y, _c in (ledger.get('claims_over_gain') or {}).items():
            acts.append(f"{_y}: claimed {_money(_c['claimed'])}, "
                        f"{_money(_c['excess'])} above its net gain")
        for r in rows:
            _ob = r.get('applied_over_balance')
            if _ob:
                acts.append(f"{r['year']}: its lock applied "
                            f"{_money(_ob['applied'])}, more than the "
                            f"{_money(_ob['carried_in'])} carried in")
            elif (r.get('balance_from_lock')
                  and abs(r['rebuilt_balance']
                          - r['carryforward_balance']) > 0.005):
                acts.append(f"{r['year']}: its lock carried out "
                            f"{_money(r['carryforward_balance'])}; these "
                            f"books, {_money(r['rebuilt_balance'])}")
    if ledger.get('claimed_ignored'):
        acts.append(f"{len(ledger['claimed_ignored'])} claimed line(s) "
                    f"unreadable, not applied")
    for r in rows:
        if r.get('section_1256_unsplit'):
            acts.append(f"{r['year']}: its lock carries "
                        f"{_money(r['section_1256'])} of §1256 P/L all "
                        f"short-term (closed before the 60/40 split)")
    for r in rows:
        if r.get('claimed_offset') is not None:
            acts.append(f"{r['year']}: claimed "
                        f"{_money(r['claimed_offset'])}, its lock "
                        f"deducted {_money(r['ordinary_income_offset'])}")
    after = [r['year'] for r in rows if r.get('after_project_year')]
    if after:
        acts.append(f"Rows after {ledger.get('project_year')} are partial "
                    f"(that year's project has the ledger)")
    prior = [r['year'] for r in rows if r.get('prior_year')]
    if prior:
        acts.append(f"Rows before {ledger.get('project_year')} are "
                    f"rebuilt, maybe partial: check the filed return")
    if (first_tx_year is not None and rows
            and rows[0]['year'] <= first_tx_year
            and not any(r.get('balance_from_lock')
                        and r['year'] >= first_tx_year - 1
                        for r in rows)):
        acts.append(f"History starts in {first_tx_year}: earlier losses "
                    f"are not in the balance")
    if ledger.get('filing_positions'):
        acts.append(f"{len(ledger['filing_positions'])} loss(es) claimed "
                    f"against the {rule} rule (ALLOWLOSS) counted")
    shown = acts[:4]
    lines.append("")
    for a in shown:
        lines.append(_out.act(a, cmd))
    if len(acts) > len(shown):
        lines.append(_out.act(f"{len(acts) - len(shown)} more", cmd))
    lines.append(("Not in the nets: slip capital gains, FX on cash"
                  if country == 'canada' else
                  "Not in the nets: 1099-DIV capital gain distributions")
                 + f" — {cmd}")
    return "\n".join(lines)


def render(ledger: Dict[str, Any], cur: str, first_tx_year: Optional[int],
           claimed_used: bool, in_project: bool = False) -> str:
    """The console ledger in the house style (docs/output-style.md): a
    table fitted to the width, each year's carry notes listed under it,
    then the notes. `--json` is the machine form. `in_project`: run by
    `tjs carryover`, whose claims are taxjson.toml's [carryover]
    claimed — it rejects --claimed FILE (backlog TJ-0145)."""
    from taxjson.lib import out as _out
    lines: List[str] = []
    _claims = ("[carryover] claimed" if in_project else "--claimed")
    # `Warning: ` shown to a person, `warning: ` captured (lib/out.label).
    _W = _out.label("warning", stream=sys.stdout)

    def para(text: str, indent: str = "", hang: Optional[str] = None):
        lines.extend(_out.wrap(text, None, indent,
                               indent if hang is None else hang))

    def item(text: str, indent: str = ""):
        para(text, indent + "- ", indent + "  ")

    country = ledger['country']
    lines.append(f"CAPITAL-LOSS CARRYOVER LEDGER — {country}, {cur}")
    para(f"Amounts are 100% gains and losses, allowed (post-"
         f"{'wash-sale' if country == 'usa' else 'superficial-loss'}).")
    lines.append("")
    rows = ledger['rows']
    if not rows:
        lines.append(_no_rows(ledger))
        return "\n".join(lines)
    year_notes: List[str] = []
    if country == 'canada':
        header = ("YEAR", "NET GAIN(LOSS)", "APPLIED", "CARRYFWD BAL")
        table = []
        for r in rows:
            notes = []
            if r['available_to_apply'] > 0.005:
                notes.append(f"carryforward available: apply up to "
                             f"{_money(r['available_to_apply'])}")
            for cb in r['carryback_candidates']:
                notes.append(f"{_money(cb['amount'])} can be carried back "
                             f"to {cb['year']} via T1A")
            if notes:
                year_notes.append(f"{r['year']}: " + "; ".join(notes))
            table.append((str(r['year']), _money(r['net_gain']),
                          _money(r['claimed_applied']),
                          _money(r['carryforward_balance'])))
        aligns = ["<", ">", ">", ">"]
    else:
        header = ("YEAR", "NET ST", "NET LT", "3K OFFSET",
                  "ST CARRY", "LT CARRY")
        table = [(str(r['year']), _money(r['net_st']), _money(r['net_lt']),
                  _money(r['ordinary_income_offset']),
                  _money(r['st_carryover']), _money(r['lt_carryover']))
                 for r in rows]
        aligns = ["<", ">", ">", ">", ">", ">"]
    lines.extend(_out.fit_table(header, table, aligns=aligns))
    for n in year_notes:
        item(n)
    lines.append("")
    _final_year = ledger.get('final_year', rows[-1]['year'])
    if country == 'canada':
        para(f"Net-capital-loss carryforward after {_final_year}: "
             f"{_money(ledger['final_carryforward'])}")
        if ledger.get('unmatched_claims'):
            para(f"{_W}{_money(ledger['unmatched_claims'])} "
                 f"of {_claims} amounts exceed the losses this "
                 f"history supports — check "
                 + ("taxjson.toml's [carryover] claimed." if in_project
                    else "the claimed file."), "", "  ")
        for _y, _amt in (ledger.get('expired_claims') or {}).items():
            para(f"{_W}{_money(_amt)} claimed for {_y} "
                 f"was never met by a loss of {_y} or earlier "
                 f"in these books, and a later loss can reach "
                 f"back only 3 years (ITA 111(1)(b)) — it came "
                 f"from losses before this history, so it does "
                 f"not reduce the carryforward shown.", "", "  ")
        for _y, _c in (ledger.get('claims_over_gain') or {}).items():
            para(f"{_W}{_claims} for {_y} is {_money(_c['claimed'])}, "
                 f"{_money(_c['excess'])} more than {_y}'s net gain "
                 f"{_money(_c['net_gain'])}: a net capital loss applied "
                 f"in a year is at most its taxable capital gains (ITA "
                 f"s.111(1.1)). The ledger applies the claim as recorded, "
                 f"so its balance is up to {_money(_c['excess'])} below "
                 f"`tjs estimate`'s, which applies no more than the "
                 f"gain. If {_y}'s return had other capital gains (slips, "
                 f"the FX gain on cash: not in the nets), the claim "
                 f"stands; else correct it.", "", "  ")
        for r in rows:
            _ob = r.get('applied_over_balance')
            if _ob:
                para(f"{_W}{r['year']}'s close-year lock applied "
                     f"{_money(_ob['applied'])} of earlier net capital "
                     f"losses, more than the {_money(_ob['carried_in'])} "
                     f"the record before it carries in: that year was "
                     f"closed again since (an amendment). No more than "
                     f"the balance carried in can be applied (ITA "
                     f"s.111(1)(b)): {r['year']}'s return needs a T1-ADJ "
                     f"too, and its lock closed again (`tjs "
                     f"check-filed` in its project names it).", "", "  ")
    else:
        para(f"Carryover after {_final_year}: "
             f"ST {_money(ledger['final_st_carryover'])} + "
             f"LT {_money(ledger['final_lt_carryover'])}")
    lines.append("")
    lines.append("NOTES")
    if country == 'usa':
        item("Amounts are 100% capital gains/losses, carried short- and "
             "long-term (Schedule D Capital Loss Carryover Worksheet).")
    else:
        item("Amounts are 100% capital gains/losses. Apply the 50% "
             "inclusion rate on Schedule 3 / T1A: CRA's 'net capital "
             "loss' balance is that figure.")
    item("The ledger is what the transaction history supports"
         + (f" plus your {_claims} records." if claimed_used else
            "; what you claimed on filed returns may differ: "
            + ("record it in taxjson.toml, [carryover] then `claimed = "
               "{ YEAR = AMOUNT }`." if in_project else
               "--claimed FILE (`YEAR AMOUNT` lines).")))
    if country == 'canada':
        item("Carryback candidates are advisory: capped at each earlier "
             "year's remaining net gain, 3 years back (ITA 111(1)(b)).")
        # The project year's net is the RETURN row (TJ-0208).
        for r in ledger['rows']:
            if (r.get('net_source') == 'return_row'
                    and abs(r['net_gain'] - r.get('slip_gains', 0.0)
                            - r['engine_net_gain']) >= 0.005):
                item(f"{r['year']}'s NET GAIN(LOSS) has the RETURN row "
                     f"of `tjs sum` (each Schedule 3 row's cents, as "
                     f"filed); the books' unrounded total is "
                     f"{_money(r['engine_net_gain'])}.")
    else:
        item("The $3,000 ordinary-income offset is assumed used whenever "
             f"available; a {_claims} "
             + ("entry" if in_project else "line")
             + " overrides a year (0 is valid).")
        for r in rows:
            if r.get('section_1256') is None:
                continue
            if r.get('section_1256_unsplit'):
                # A lock closed before the split (backlog TJ-0611).
                item(f"{_W}{r['year']}'s close-year lock was closed "
                     f"before taxjson split §1256 P/L as Form 6781 does: "
                     f"its carryover, and its row here, count its "
                     f"{_money(r['section_1256'])} of §1256 P/L as "
                     f"short-term, where Form 6781 makes 60% of it "
                     f"({_money(section_1256_split(r['section_1256'])[1])}"
                     f") long-term. Close {r['year']} again with --force "
                     f"in its project.")
                continue
            item(f"{r['year']}: NET ST and NET LT take its "
                 f"{_money(r['section_1256'])} of §1256 P/L (futures, "
                 f"futures options, broad-based index options) as Form "
                 f"6781 splits it, whatever the holding period: "
                 f"{_money(r['section_1256_short_term'])} short-term "
                 f"(40%), {_money(r['section_1256_long_term'])} long-term "
                 f"(60%). Not modelled: a contract still open at Dec 31 "
                 f"is not marked to market, and a net §1256 loss is not "
                 f"carried back (§1212(c)) — docs/limits.md.")
    if ledger.get('filing_positions'):
        _fps = ledger['filing_positions']
        item(f"Includes {len(_fps)} filing position(s) taken against the "
             + ("wash-sale rule (§1091)" if country == 'usa'
                else "superficial-loss rule (s.54)")
             + " (.tt ALLOWLOSS): "
             + "; ".join(f"{fp['date'][:4]} {fp['symbol']} loss "
                         f"{_money(fp['loss'])} claimed, the rule would "
                         f"{'disallow' if country == 'usa' else 'deny'} "
                         f"{_money(fp['would_disallow'])} ({fp['where']})"
                         for fp in _fps)
             + ". The nets above count each loss as allowed; delete the "
               "line to apply the rule.")
    if ledger.get('scope_note'):
        # (the --json note keeps its "->"; a wrapped line must not
        # start with one)
        item(ledger['scope_note'].replace(" -> ", " to "))
    if ledger.get('claimed_ignored'):
        item(f"{_W}{len(ledger['claimed_ignored'])} "
             f"claimed line(s) could not be read and are NOT "
             f"applied, so the carryforward shown is overstated "
             f"by them: "
             + "; ".join(ledger['claimed_ignored'][:3])
             + (" ..." if len(ledger['claimed_ignored']) > 3
                else ""))
    after = [r['year'] for r in rows if r.get('after_project_year')]
    if after:
        # A US individual has no carry-back (no T1A): its ledger's
        # note says only where the carryover stops (backlog TJ-0473).
        item(f"{_W}the rows after {ledger.get('project_year')}"
             f" ({', '.join(str(y) for y in after)}) are partial —"
             f" only the trades of that year this project's inputs"
             f" happen to hold. "
             + ("They offer no T1A carry-back and the carryforward"
                if country == 'canada' else "The carryover")
             + f" above stops at "
             f"{ledger.get('project_year')}; that year's own "
             f"project computes its ledger.")
    seeded = [r for r in rows if r.get('from_lock')]
    if seeded:
        item("; ".join(
            f"{r['year']} is the filed figure from {r['filed_lock']} "
            f"({LOCK_SOURCE_TEXT.get(r['filed_source'], r['filed_source'])})"
            for r in seeded)
            + " — not rebuilt from this project's books.")
    from_lock = [r for r in rows if r.get('balance_from_lock')]
    if from_lock:
        item("; ".join(
            f"the balance carried out of {r['year']} is the one its "
            f"close-year lock recorded"
            + (f" (these books rebuild "
               f"{_money(r['rebuilt_balance'])})"
               if country == 'canada'
               and abs(r['rebuilt_balance'] - r['carryforward_balance'])
               > 0.005 else "")
            + (f", its 3K OFFSET the deduction the lock counted as used "
               f"({_claims} says {_money(r['claimed_offset'])}: close "
               f"{r['year']} again with --force in its project, or fix "
               f"the entry)"
               if r.get('claimed_offset') is not None else
               ", its 3K OFFSET the deduction the lock counted as used"
               if r.get('assumed_offset') is not None else "")
            for r in from_lock)
            + " — the lock is the record of the filed return.")
    slips = [r for r in rows if r.get('slip_gains')]
    if slips:
        item("Includes the capital-gains dividends named in "
             "[[capital_gains_dividends]] (T5 box 18): "
             + ", ".join(f"{r['year']} {_money(r['slip_gains'])}"
                         for r in slips) + ".")
    prior = [r['year'] for r in rows if r.get('prior_year')]
    if prior:
        py = ledger.get('project_year')
        item(f"{_W}the rows before {py} "
             f"({', '.join(str(y) for y in prior)}) are rebuilt "
             f"from this project's books — opening *_start.tt "
             f"lots plus whatever prior-year exports are in "
             f"inputs/ — and may be partial (a missing export "
             f"shows a smaller gain, or a loss that never "
             f"happened). Verify each against the filed return "
             f"({'Schedule D' if country == 'usa' else 'Schedule 3'}"
             f") before trusting a "
             f"carryforward or carryback from it.")
    if (first_tx_year is not None and rows
            and rows[0]['year'] <= first_tx_year
            and not any(r.get('balance_from_lock')
                        and r['year'] >= first_tx_year - 1
                        for r in rows)):
        item(f"{_W}this history starts in {first_tx_year} — "
             f"if you traded before then, earlier gains/losses (and "
             f"any pre-{first_tx_year} carryforward) are NOT "
             f"reflected. Reconcile the opening balance against "
             + ("your prior Schedule D (its Capital Loss "
                "Carryover Worksheet)." if country == 'usa' else
                "your CRA records (Notice of Assessment / My "
                "Account).")
             )
    return "\n".join(lines)


@guard_main("taxjson-carryover", value_errors=True)
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Multi-year capital-loss carryforward/carryback ledger "
                    "over full-history taxjson books.")
    parser.add_argument("files", nargs="*", type=Path, metavar="FILE",
                        help="TAXABLE <account>_base.json files (full "
                             "history, base currency); a crypto-only "
                             "project passes only --crypto books")
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
    add_country_argument(parser)
    parser.add_argument("--tax-date", choices=["settle", "trade"],
                        default=None,
                        help="Year-attribution basis (default: country-aware)")
    parser.add_argument("--incomplete-history", type=Path, default=None,
                        metavar="DIR",
                        help="the project folder: its accounts' .tt "
                             "OPENING cost=unknown lines (truncated-history "
                             "openings)")
    parser.add_argument("--claimed", type=Path, default=None,
                        help="`YEAR AMOUNT` lines: losses actually applied "
                             "on filed returns. Canada: the 100%% capital "
                             "loss applied that year — the line 25300 "
                             "amount divided by the inclusion rate (x2 at "
                             "50%%). US: the Schedule D line 21 deduction "
                             "against ordinary income that year, as far "
                             "as taxable income absorbed it (line 4 of "
                             "the next year's Capital Loss Carryover "
                             "Worksheet; 0 when taxable income was "
                             "negative) — not the line 6/14 carryover "
                             "coming in. (`tjs carryover` passes the "
                             "project's [carryover] claimed as "
                             "--claimed-year instead.)")
    parser.add_argument("--claimed-year", action="append", default=[],
                        metavar="YEAR=AMOUNT",
                        help="One year's claimed amount (same meaning as "
                             "a --claimed line); repeatable. The wrapper "
                             "passes taxjson.toml's [carryover] claimed "
                             "this way. Not combined with --claimed.")
    parser.add_argument("--project-year", type=tax_year, default=None,
                        metavar="YEAR",
                        help="The project's tax year: earlier rows are "
                             "flagged as rebuilt from this project's "
                             "books and possibly partial.")
    parser.add_argument("--filed", action="append", default=[],
                        metavar="YEAR=REALIZED",
                        help="A filed year's locked realized total "
                             "(filed/<year>.json; the wrapper passes "
                             "them): a ledger row that disagrees is "
                             "flagged — the return, not this recompute, "
                             "is what the carryover balance (CRA's, or "
                             "the IRS Schedule D worksheet's) is built "
                             "on.")
    parser.add_argument("--filed-lock", action="append", default=[],
                        metavar="YEAR=PATH",
                        help="A filed year's close-year lock (the wrapper "
                             "passes filed/<year>.json and the "
                             "prior_year_record lock). A year before "
                             "--project-year takes the lock's FILED "
                             "figure instead of the rebuilt one; a later "
                             "year is compared with it. An unreadable "
                             "lock is named.")
    parser.add_argument("--return-row", action="append", default=[],
                        metavar="YEAR=GAIN",
                        help="Canada: a year's gain as the return files "
                             "it (`tjs sum`'s RETURN row, each "
                             "Schedule 3 row's cents): that year's NET "
                             "GAIN(LOSS) instead of the engine's unrounded "
                             "total (the wrapper passes the project "
                             "year's); a year a lock seeds keeps the "
                             "lock's figure.")
    parser.add_argument("--slip-gains", action="append", default=[],
                        metavar="YEAR=AMOUNT",
                        help="Canada: capital gains from slips (T5 box 18 "
                             "dividends in [[capital_gains_dividends]]) "
                             "that year — added to the year's net (the "
                             "wrapper passes them).")
    from taxjson.lib.pipeline import add_loss_override_args
    add_loss_override_args(parser)
    parser.add_argument("--corporate-distribution", action="append",
                        default=None, metavar="SYMBOL",
                        help="Canada: a Canadian issuer whose "
                             "distributions are a corporation's (ROC "
                             "dated when paid); repeatable ([settings] "
                             "corporate_distributions).")
    parser.add_argument("--ric-january-dividend", action="append",
                        default=None, metavar="\"SYMBOL [YYYY-01-DD]\"",
                        help="USA: a January fund/REIT dividend received "
                             "Dec 31 of the prior year; repeatable "
                             "([settings] ric_january_dividends).")
    parser.add_argument("--base-currency", default=None,
                        type=norm_currency,
                        help="The books' currency: must be the country's "
                             "own (CAD for canada, USD for usa — the "
                             "default); any other is refused, as are rows "
                             "in another currency (not just a label)")
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text")
    # The written-option premium timing the filing pipeline uses (the
    # `tjs carryover` wrapper passes the project's settings): without
    # them the ledger computed every year on close timing while the
    # returns were filed on grant timing (2026-09 audit: filed 2025 -601
    # vs ledger -1,000 on the same book).
    parser.add_argument("--option-premium-timing", choices=["grant", "close"],
                        default=None,
                        help="Canada only: written-option premium timing "
                             "(grant — ITA s.49(1) — or close; default "
                             "close). Refused with --country usa.")
    parser.add_argument("--option-grant-since", type=tax_year, default=None,
                        metavar="YEAR",
                        help="With grant timing: contracts written before "
                             "YEAR keep close timing.")
    parser.add_argument("--transfers-as-acquisitions", action="store_true",
                        help="Count a sheltered account's unmatched TRANSFER rows as "
                             "acquisitions/disposals for the superficial-loss / wash-sale "
                             "window (strict; [settings] transfers_as_acquisitions = true). "
                             "Default: a custody move, held but never a purchase.")
    parser.add_argument("--option-buyback-wash", action="store_true",
                        help="Canada, grant timing: buy-back loss of a "
                             "written option can be superficial.")
    # The wrapper's claims are taxjson.toml's [carryover] claimed: the
    # notes name that, not --claimed FILE, which `tjs carryover`
    # rejects (backlog TJ-0145).
    parser.add_argument("--claims-in-project", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--brief", action="store_true",
                        help="The essentials only (what `tjs carryover` prints "
                             "without --details): the notes and each "
                             "message's detail are left out")
    args = parser.parse_args(argv)
    global _DETAILS
    _DETAILS = not args.brief
    refuse_foreign_flags(args, "taxjson-carryover")
    # Standalone runs default to close timing while a Canada project
    # uses grant timing: say so, as taxjson-gains does (R1-177, A2-0677).
    from taxjson.bin.taxjson_gains import _timing_default_note
    _timing_default_note(args, "taxjson-carryover")
    from taxjson.lib.country import base_currency_problem, home_currency
    if args.base_currency in (None, ""):
        args.base_currency = home_currency(args.country)
    # A Canada ledger in USD feeds T1A / line 25300 in the wrong
    # currency (audit A2-0351; t1135 refuses the same).
    _bp = base_currency_problem(args.country, args.base_currency)
    if _bp:
        emit_line(f"taxjson-carryover: error: --base-currency "
                  f"{args.base_currency}: "
                  + _bp.replace("[settings] base_currency is", "the books are")
                   .replace('set base_currency', 'pass books converted to')
                  , file=sys.stderr)
        return 2

    if not args.files and not args.crypto:
        # A crypto-only project gives its books as --crypto only: fed
        # positionally they lost the US no-wash pass (US-WASH-13; audit
        # A2-0146, A2-0411, A2-0412).
        emit_line("taxjson-carryover: error: give at least one base book "
                  "(FILE or --crypto FILE)", file=sys.stderr)
        return 2
    for p in args.files + args.crypto + args.sheltered:
        if not p.exists():
            print(f"taxjson-carryover: no such file: {p}", file=sys.stderr)
            return 2
    if args.claimed is not None and args.claimed_year:
        print("taxjson-carryover: give the claimed amounts once — "
              "--claimed FILE or --claimed-year YEAR=AMOUNT, not both",
              file=sys.stderr)
        return 2
    if args.claimed is not None and not args.claimed.exists():
        print(f"taxjson-carryover: no such file: {args.claimed}",
              file=sys.stderr)
        return 2

    from taxjson.lib.json_input import load_transactions_or_exit as _ltx
    # The ledger is labelled in --base-currency and its balance feeds
    # T1A / line 25300: a native USD book (a raw broker file) printed a
    # 5,000 USD loss as a 5,000.00 CAD carryforward (S028-02).  # pii-ok
    from taxjson.lib.country import home_currency as _home_currency
    _home = _home_currency(args.country)
    base_cur = str(args.base_currency or _home).strip().upper()
    if base_cur != _home:
        # A matching --base-currency dodged the row check: a Canada
        # ledger in USD, or the US $3,000 ordinary-income offset applied
        # to CAD amounts, at exit 0 (A2-1118).
        print(f"taxjson-carryover: --base-currency {base_cur} does not "
              f"match the {args.country} return's currency {_home} — "
              f"pass the {_home} books (work/<account>_base.json, or "
              f"`tjs carryover` in the project)", file=sys.stderr)
        return 2
    args.base_currency = base_cur

    def _load_base(p: Path):
        txs = _ltx("taxjson-carryover", p)
        # metadata.target_currency names the book's currency for rows
        # that carry none (t1135.check_currency honours it; A2-0351).
        try:
            _doc = json.loads(Path(p).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, UnicodeDecodeError):
            _doc = None
        _meta = _doc.get("metadata") if isinstance(_doc, dict) else None
        _tgt = str((_meta or {}).get("target_currency") or "").strip().upper() \
            if isinstance(_meta, dict) else ""
        if _tgt and _tgt != base_cur:
            raise ValueError(
                f"{p} is in {_tgt} (metadata.target_currency) but "
                f"--base-currency is {base_cur} — pass the base-currency "
                f"books (work/<account>_base.json, or `tjs carryover` "
                f"in the project)")
        bad = sorted({str(t.currency).strip().upper() for t in txs
                      if str(t.currency or "").strip()
                      and str(t.currency).strip().upper() != base_cur})
        if bad:
            raise ValueError(
                f"{p} holds rows in {', '.join(bad)} but --base-currency "
                f"is {base_cur} — pass the base-currency books "
                f"(work/<account>_base.json, or `tjs carryover` in "
                f"the project)")
        return txs

    transactions = []
    try:
        for p in args.files:
            transactions.extend(_load_base(p))
        crypto_loaded = [_load_base(cp) for cp in args.crypto]
    except ValueError as exc:
        emit_line(f"taxjson-carryover: error: {exc}", file=sys.stderr)
        return 2
    sheltered = []
    for p in args.sheltered:
        sheltered.extend(_ltx("taxjson-carryover", p))

    country = args.country
    # Filed-year locks: a year before the project year is rebuilt from
    # this project's books and may be partial (a later project holds
    # last year only as a start .tt), so the ledger takes what the lock
    # says was FILED — the return CRA's balance is built on (A2-0121,
    # A2-0338, A2-0666). The project year (and later) is compared.
    # Read before the gains run: a US wash-sale basis add whose
    # replacement was sold in a locked year is booked in the loss's
    # year, as the pipeline books it (US-WASH-22) — run unlocked, the
    # add went to the locked year, whose lock figure then replaced it,
    # and the loss was in no year (backlog TJ-0296).
    lock_info: Dict[int, Dict[str, Any]] = {}
    lock_years: List[int] = []
    for item in args.filed_lock:
        try:
            y_s, lp = item.split("=", 1)
            y = int(y_s)
        except ValueError:
            print(f"taxjson-carryover: --filed-lock expects YEAR=PATH, got "
                  f"{item!r}", file=sys.stderr)
            return 2
        from taxjson.bin.taxjson_filed import file_year as _file_year
        if _file_year(Path(lp)) not in (None, y):
            # filed/2024.json given as 2021: the filed return of one
            # year taken for another's (backlog TJ-0904). A lock whose
            # own year field disagrees with its file name is damaged:
            # `tjs carryover` keys a prior_year_record by that field, so
            # the binding is the wrapper's, not the user's — say what
            # read_lock says (restore or re-close), not YEAR=PATH advice
            # (backlog TJ-1075).
            try:
                lock_figure(Path(lp), country)
            except LockUnreadable as exc:
                from taxjson.lib.out import error as _error
                _error(str(exc), prog="taxjson-carryover")
                return 1
            print(f"taxjson-carryover: --filed-lock {item}: the lock is "
                  f"for {_file_year(Path(lp))}, not {y} — give it as "
                  f"{_file_year(Path(lp))}={lp}", file=sys.stderr)
            return 2
        lock_years.append(y)
        try:
            info = lock_figure(Path(lp), country)
        except LockUnreadable as exc:
            if args.project_year is not None and y < args.project_year:
                # A year before the project year takes the lock's filed
                # figure and the balance it carried: dropping the lock
                # with a warning left the year out and printed a 0.00
                # carryforward at exit 0, where the estimate, amt,
                # handoff and close-year refuse the same lock
                # (2026-10-25 audit robustness#1, year-end#1). The
                # estimate's own words, one message (its last line is
                # what `tjs checklist` shows).
                from taxjson.lib.out import error as _error
                _error(str(exc), prog="taxjson-carryover")
                return 1
            # The project year (or a later one) is only compared with
            # its lock: the row stands, unchecked.
            from taxjson.lib.out import warn as _warn
            # A warning, not a refusal (backlog TJ-0069): the row is
            # this project's books either way — the lock gives it no
            # figure — and check-filed, the run and `tjs years`
            # report the damage itself.
            _warn(f"{y}: the filed-year lock {lp} could not be read or "
                  f"is damaged — the {y} row is not checked against the "
                  f"filed return",
                  details=_dl([f"{exc}", "`tjs check-filed` checks "
                               "the lock."]))
            continue
        if info.get('year') is not None and info['year'] != y:
            # A lock named anything (a prior_year_record) whose own
            # year is another (backlog TJ-0904).
            print(f"taxjson-carryover: --filed-lock {item}: the lock "
                  f"records year {info['year']}, not {y} — give it as "
                  f"{info['year']}={lp}", file=sys.stderr)
            return 2
        info['path'] = lp
        lock_info[y] = info
    # Every lock the wrapper names, readable or not: the pipeline's
    # --locked-year flags (taxjson_filed.locked_year_flags) do the same.
    locked_years = (tuple(sorted(set(lock_years))) if country == 'usa'
                    else ())
    # Their files: a basis add a lock's own row already has stays in
    # that sale, as the pipeline keeps it (backlog TJ-0463).
    locked_records = (tuple(str(i.split("=", 1)[1])
                            for i in args.filed_lock if "=" in i)
                      if country == 'usa' else ())
    crypto_txs = [t for txs in crypto_loaded for t in txs]
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
    _opt = dict(option_premium_timing=args.option_premium_timing,
                option_grant_since=args.option_grant_since,
                option_buyback_loss_superficial=args.option_buyback_wash,
                transfers_as_acquisitions=args.transfers_as_acquisitions,
                # The project's income dating, as every filing run uses
                # it: a listed corporation's ROC on its pay date
                # (CA-INC-DATE-ROC; audit A2-0123, A2-0339, A2-0341).
                corporate_distributions=tuple(
                    args.corporate_distribution or ()),
                ric_january_dividends=tuple(
                    args.ric_january_dividend or ()),
                loss_overrides=loss_overrides_from_args(args),
                locked_years=locked_years,
                locked_records=locked_records)
    req = GainsRequest(country=country, year=None, taxable=True,
                       tax_date=args.tax_date,
                       incomplete_history=args.incomplete_history,
                       phantom_hint=False,
                       per_account_basis=(country == 'usa'), **_opt)
    try:
        results = run_gains(transactions, sheltered, (), req)
    except (TransferValidationError, _AmbiguousXferErr) as exc:
        emit_line(f"taxjson-carryover: error: {exc}", file=sys.stderr)
        return 1
    nets = yearly_nets(results, req.effective_tax_date(),
                       s1256=(country == 'usa'),
                       filed_gap=(country == 'canada'))
    # The unknown-cost dispositions every computation left out of the
    # nets (the equity run's and the US crypto pass's): the US crypto
    # pass's were dropped in silence (backlog TJ-0323).
    manual: List[Dict[str, Any]] = list(
        results.get('manual_reporting_required') or [])
    if crypto_txs:                       # USA crypto: no-wash pass
        req_c = GainsRequest(country=country, year=None, taxable=True,
                             tax_date=args.tax_date,
                             incomplete_history=args.incomplete_history,
                             phantom_hint=False, no_wash=True,
                             per_account_basis=True, **_opt)
        try:
            res_c = run_gains(crypto_txs, sheltered, (), req_c)
        except (TransferValidationError, _AmbiguousXferErr) as exc:
            emit_line(f"taxjson-carryover: error: {exc}", file=sys.stderr)
            return 1
        for yr, vals in yearly_nets(
                res_c, req_c.effective_tax_date()).items():
            dst = nets.setdefault(yr, {})
            for k, v in vals.items():
                dst[k] = dst.get(k, 0.0) + v
        manual += list(res_c.get('manual_reporting_required') or [])
    claimed_ignored: List[str] = []
    try:
        claimed = load_claimed(args.claimed, ignored=claimed_ignored)
    except (OSError, UnicodeDecodeError) as exc:
        # A directory or an unreadable file: one line, not a traceback
        # (S027-19).
        print(f"taxjson-carryover: cannot read claimed file "
              f"{args.claimed}: {exc}", file=sys.stderr)
        return 2

    def _year_amount(item: str, flag: str):
        try:
            y, v = item.split("=", 1)
            return int(y), _finite(v, flag)
        except ValueError:
            raise ValueError(f"{flag} expects YEAR=AMOUNT (a finite "
                             f"number), got {item!r}") from None

    try:
        slip = [_year_amount(i, "--slip-gains") for i in args.slip_gains]
        for y, v in (_year_amount(i, "--claimed-year")
                     for i in args.claimed_year):
            if v < 0:
                raise ValueError(f"--claimed-year {y}: the amount must be "
                                 f">= 0, got {v!r}")
            # The claimed file's year range (S027-23): a typo such as
            # 2205 ran the ledger through invented returns to 2205 and
            # used the whole loss up (backlog TJ-1002).
            if not CLAIM_MIN_YEAR <= y <= _claim_max_year():
                raise ValueError(f"--claimed-year {y}: {y} is not a "
                                 f"plausible tax year ({CLAIM_MIN_YEAR}.."
                                 f"{_claim_max_year()})")
            claimed[y] = claimed.get(y, 0.0) + v
        filed: Dict[int, float] = dict(
            _year_amount(i, "--filed") for i in args.filed)
        return_row: Dict[int, float] = dict(
            _year_amount(i, "--return-row") for i in args.return_row)
    except ValueError as exc:
        print(f"taxjson-carryover: {exc}", file=sys.stderr)
        return 2
    # --slip-gains in a US ledger is refused up front with the other
    # one-country flags (FLAG_COUNTRY; refuse_foreign_flags).
    _zero = {'net': 0.0, 'st': 0.0, 'lt': 0.0, 'dispositions': 0}
    slip_by: Dict[int, float] = {}
    for y, v in slip:
        slip_by[y] = slip_by.get(y, 0.0) + v
    # The locks read above: a year before the project year takes the
    # lock's filed figure and the balance it carried.
    seeded: Dict[int, Dict[str, Any]] = {}
    carried: Dict[int, Any] = {}
    locked_back: Dict[int, float] = {}
    locked_applied: Dict[int, float] = {}
    own_year: Optional[int] = None
    locked_deduction: Dict[int, float] = {}
    for y, info in lock_info.items():
        if (args.project_year is not None and y < args.project_year
                and info.get('carried') is not None):
            carried[y] = info['carried']
            locked_back[y] = float(info.get('carried_back') or 0.0)
            locked_applied[y] = float(info.get('applied') or 0.0)
            if info.get('deduction') is not None:
                locked_deduction[y] = float(info['deduction'])
        elif (country == 'canada' and y == args.project_year
              and info.get('carried') is not None
              and not info.get('partial')):
            # The project year closed: its row shows what its lock
            # applied and carried into the next year, as a year before
            # it does — the ledger offered again the loss close-year
            # had used (backlog TJ-0433). Its net is still the books'.
            own_year = y
            carried[y] = info['carried']
            locked_back[y] = float(info.get('carried_back') or 0.0)
            locked_applied[y] = float(info.get('applied') or 0.0)
        if args.project_year is not None and y < args.project_year:
            rebuilt = dict(nets.get(y, _zero))
            nets[y] = {'net': info['figure'], 'st': info['st'],
                       'lt': info['lt'],
                       'dispositions': max(int(rebuilt.get('dispositions')
                                               or 0),
                                           int(info['dispositions']))}
            if info.get('section_1256') is not None:
                # The lock's §1256 net, in its st / lt as its carryover
                # took it (TJ-0466, TJ-0611).
                nets[y]['s1256'] = float(info['section_1256'])
                if info.get('section_1256_unsplit'):
                    nets[y]['s1256_unsplit'] = True
            seeded[y] = {'rebuilt': float(rebuilt.get('net') or 0.0),
                         'had_rows': bool(rebuilt.get('dispositions')),
                         **info}
            if info.get('slip_gains') is not None:
                # The slip gains the lock recorded, as filed: this
                # project's inputs seldom hold that year's slips any
                # more (backlog TJ-0898).
                slip_by[y] = float(info['slip_gains'])
    # A year's net as the return files it (`tjs sum`'s RETURN row,
    # each Schedule 3 row's cents), not the engine's unrounded total a
    # cent or two apart: the carryforward balance, and the carry-back
    # close-year records, are built on the return (backlog TJ-0208). A
    # year a lock seeds keeps the lock's filed figure.
    engine_net: Dict[int, float] = {}
    for y, v in return_row.items():
        if y in seeded:
            continue
        rec = nets.setdefault(y, dict(_zero))
        engine_net[y] = float(rec.get('net') or 0.0)
        rec['net'] = v
        if 'filed_gap' in rec:
            rec['filed_gap'] = 0.0      # (the filed figure itself)
    for y, v in slip_by.items():
        rec = nets.setdefault(y, dict(_zero))
        rec['net'] = rec.get('net', 0.0) + v

    if country == 'canada':
        ledger = build_canada_ledger(nets, claimed, carried, locked_back,
                                     locked_applied, own_year=own_year)
    else:
        ledger = build_usa_ledger(nets, claimed, carried, locked_deduction,
                                  through_year=args.project_year)

    # Every book's rows, the US crypto pass's too: a crypto-only US
    # ledger said its history started nowhere (TJ-0323).
    tx_years = [int(t.date[:4]) for t in transactions + crypto_txs
                if t.date[:4].isdigit()]
    first_tx_year = min(tx_years) if tx_years else None
    ledger['first_transaction_year'] = first_tx_year
    ledger['scope_note'] = SCOPE_NOTE[country]
    # The .tt ALLOWLOSS filing positions the nets include (CA-SL-18 /
    # US-WASH-25): the ledger counted the claimed losses without a word
    # (pre-release review).
    from taxjson.lib import loss_overrides as _LO
    _fp = _LO.positions(
        [it for it in (results.get('loss_overrides') or [])
         if it.get('status') == _LO.STATUS_APPLIED])
    if _fp:
        ledger['filing_positions'] = [
            {k: v for k, v in fp.items() if k != 'in_year'} for fp in _fp]
    if claimed_ignored:
        ledger['claimed_ignored'] = claimed_ignored
    if args.project_year is not None:
        # R1-279: a year before the project's is rebuilt from THIS
        # project's books — opening *_start.tt lots plus whatever
        # prior-year exports sit in inputs/ — and can be partial even
        # when the history starts earlier (a start lot dated 2023 hid
        # a 2024 row that held 52k of a filed 731k).
        ledger['project_year'] = args.project_year
        for r in ledger['rows']:
            r['prior_year'] = (r['year'] < args.project_year
                               and r['year'] not in seeded)
            # A row after the project year holds only the trades this
            # project's inputs happen to carry into it (a few January
            # rows): partial, no T1A suggestion, and the carryforward
            # stops at the project year (audit A2-0665).
            if r['year'] > args.project_year:
                r['after_project_year'] = True
                if 'carryback_candidates' in r:
                    r['carryback_candidates'] = []
        done = [r for r in ledger['rows']
                if r['year'] <= args.project_year]
        if len(done) < len(ledger['rows']):
            last = done[-1] if done else None
            ledger['final_year'] = args.project_year
            if country == 'canada':
                ledger['final_carryforward'] = (
                    last['carryforward_balance'] if last else 0.0)
            else:
                st = last['st_carryover'] if last else 0.0
                lt = last['lt_carryover'] if last else 0.0
                ledger.update(final_st_carryover=st,
                              final_lt_carryover=lt,
                              final_carryforward=round(st + lt, 2))
    for r in ledger['rows']:
        y = r['year']
        if slip_by.get(y):
            r['slip_gains'] = round(slip_by[y], 2)
        if y in engine_net:
            # NET GAIN(LOSS) is the RETURN row; the rebuild's own total
            # beside it (TJ-0208).
            r['net_source'] = 'return_row'
            r['engine_net_gain'] = round(engine_net[y], 2)
        if y in seeded:
            sd = seeded[y]
            r.update(from_lock=True, filed_lock=sd['path'],
                     filed_source=sd['source'],
                     filed_realized=round(sd['figure'], 2),
                     rebuilt_net_gain=round(sd['rebuilt'], 2))
            if abs(sd['rebuilt'] - sd['figure']) > 0.01:
                from taxjson.lib.out import note as _note
                _note(f"{y}: the ledger uses the filed figure "
                      f"{sd['figure']:,.2f} from {sd['path']}",
                      details=_dl([f"{LOCK_SOURCE_TEXT[sd['source']]}; this "
                               f"project's books rebuild "
                               f"{sd['rebuilt']:,.2f} for {y}"
                               + ("" if sd['had_rows'] else
                                  " (no disposition of that year here)")
                               + " — a year before the project year is "
                                 "rebuilt from opening lots and whatever "
                                 "prior exports are in inputs/, so the "
                                 "lock is what " + _authority(country)
                               + " is built on."]))
            if (country == 'usa' and sd.get('filed_totals_gain') is not None
                    and abs(sd['filed_totals_gain'] - sd['figure']) > 0.01):
                from taxjson.lib.out import warn as _warn
                _warn(f"{y}: {sd['path']} records "
                      f"{sd['filed_totals_gain']:,.2f} filed with another "
                      f"tool, which has no short/long-term split",
                      details=_dl([f"The ledger uses the lock's own Part "
                                   f"I/II split ({sd['figure']:,.2f}) — "
                                   f"enter the filed carryover with "
                                   + ("[carryover] claimed"
                                      if args.claims_in_project
                                      else "--claimed")
                                   + " if they differ."]))
            continue
        if y in lock_info:
            info = lock_info[y]
            fig, src, where = info['figure'], info['source'], info['path']
        elif y in filed:
            fig, src, where = filed[y], 'realized', f"filed/{y}.json"
        else:
            continue
        net = float(r.get('net_gain') or 0.0) - slip_by.get(y, 0.0)
        if src == 'realized' and y in engine_net:
            # The lock's realized is the engine total rounded once:
            # like with like, not the RETURN row's cents (TJ-0208).
            net = engine_net[y]
        r['filed_realized'] = round(fig, 2)
        r['filed_source'] = src
        # The return's lines are rounded per row; the ledger is not.
        tol = 0.01 if src == 'realized' else max(
            0.01, 0.005 * int(r.get('dispositions') or 0))
        if abs(net - fig) > tol:
            r['differs_from_filed'] = True
            from taxjson.lib.out import warn as _warn
            _warn(f"{y}: the ledger's net {net:,.2f} differs from the "
                  f"filed lock's {fig:,.2f}",
                  details=_dl([f"{LOCK_SOURCE_TEXT[src]}"
                           + (", as filed with another tool"
                              if src == 'filed_totals' else "")
                           + f" ({where}). This ledger recomputes "
                           f"every year with this project's settings ("
                           + ("tax_date" if country == 'usa' else
                              "option premium timing and "
                              "option_grant_timing_since, tax_date")
                           + "); the lock is what was filed and what "
                           + _authority(country) + " is built on — check "
                           "the settings or use `tjs check-filed`."]))
    if manual:
        # Machine-readable too (TJ-0323): each excluded disposition
        # (`excluded_unknown_cost`), and the years whose nets lack them
        # (`incomplete_years`; each such row `incomplete: true`).
        _mk = ('date_settle' if req.effective_tax_date() == 'settle'
               else 'date')
        excl: List[Dict[str, Any]] = []
        for t in manual:
            d = str(t.get(_mk) or t.get('date') or '')
            excl.append({
                'year': int(d[:4]) if d[:4].isdigit() else None,
                'date': t.get('date'), 'symbol': t.get('symbol'),
                'account': t.get('account'),
                'qty': abs(float(t.get('qty') or 0.0)),
                'proceeds': round(float(t.get('proceeds') or 0.0), 2)})
        ledger['excluded_unknown_cost'] = excl
        _inc = sorted({e['year'] for e in excl if e['year'] is not None})
        ledger['incomplete_years'] = _inc
        for r in ledger['rows']:
            if r['year'] in _inc:
                r['incomplete'] = True
        from taxjson.lib.out import warn as _warn
        _warn(f"{len(excl)} disposition(s) with an unknown cost (no "
              f"purchase in your files) are EXCLUDED from the ledger"
              + (f" ({', '.join(str(y) for y in _inc)})" if _inc else ""),
              details=_dl(["See manual_reporting_required in the gains "
                           "output; their years' nets are incomplete "
                           "until resolved."]))

    if args.json:
        ledger["currency"] = args.base_currency.upper()
        from taxjson.lib.json_input import dump_filing_json
        dump_filing_json(ledger, sys.stdout)
        print()
    elif args.brief:
        print(render_brief(ledger, args.base_currency.upper(),
                           first_tx_year))
    else:
        print(render(ledger, args.base_currency.upper(), first_tx_year,
                     claimed_used=bool(claimed),
                     in_project=args.claims_in_project))
    return 0


if __name__ == "__main__":
    sys.exit(main())
