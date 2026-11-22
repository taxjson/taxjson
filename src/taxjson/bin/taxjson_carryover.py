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
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from taxjson.lib.cli_diag import guard_main, tax_year
from taxjson.lib.country import add_country_argument, refuse_foreign_flags
from taxjson.engine.rows import AmbiguousTransferDateError as _AmbiguousXferErr
from taxjson.lib.pipeline import (GainsRequest, TransferValidationError,
                                  loss_overrides_from_args, run_gains)
from taxjson.lib.report_fmt import fmt_money
from taxjson.model.result import GainRow
from taxjson.model import money as M
from taxjson.lib.exchange_rates import norm_currency
# The §1211(b) cap, from the one constant the estimate uses too
# (tax-logic US-RPT-07 states it).
from taxjson.lib.tax_estimate import (
    US_ORDINARY_LOSS_CAP as US_ORDINARY_OFFSET, section_1256_split)
from taxjson.lib.carryover_inputs import (
    CLAIM_MIN_YEAR,
    _claim_max_year,
    _finite,
    _form_lines,
    load_claimed,
    us_lock_1256,
    us_lock_gains,
)
from taxjson.lib import out as _out
from taxjson.lib import carry_back as _CB


_INCOME_ACTIONS = ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST', 'FEE')


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
    per row, one row per line and symbol (lib/tax_forms
    build_schedule3; build_canada_ledger; backlog TJ-1195)."""
    from taxjson.lib.futures import section_1256_kind
    date_key = 'date_settle' if tax_date == 'settle' else 'date'
    nets: Dict[int, Dict[str, float]] = {}
    schedule_rows: Dict[int, Dict[Tuple[str, str], float]] = {}
    from taxjson.lib.tax_forms import (_cents, _line_key, property_class, schedule3_line,
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
        gain = float(GainRow(t).gain or 0.0)
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
                        own_year: Optional[int] = None,
                        through_year: Optional[int] = None,
                        locked_opening: Optional[Dict[int, float]] = None,
                        back_caps: Optional[Dict[int, float]] = None,
                        given_back: Optional[Dict[int, float]] = None,
                        stated: Optional[Dict[int, float]] = None) -> Dict[str, Any]:
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
    balance it carried. What that return applied comes first: the claims
    under earlier years take what is left, as the lock's split does
    (backlog TJ-1495). In any locked year a [carryover] claimed entry
    under that year is met by what its lock applied. The rest of it takes
    nothing from a balance the lock carried past that year: only a loss of
    a later year can meet it (TJ-1547, TJ-1548, TJ-1549, TJ-1555, TJ-1558,
    TJ-1565). A lock that records no `applied` says nothing of it, so the
    entry folds. `through_year`: the year the claims still unmet are read as
    of, the project's (the last row otherwise; TJ-1557, TJ-1568).
    `locked_opening`: {year: the balance that lock took in from the record before
    it}: opening above what the rebuilt rows before it left, it reads as those rows
    having carried back what the returns didn't, and the ledger folds again with
    `back_caps` ({year: the most that row may carry back}), the latest takes first
    (TJ-1554). The lock's row says how much of the claims still unmet rests on
    that, `claims_given_back` (TJ-1574, TJ-1583, TJ-1584; `given_back`: {year: what
    the refolds gave back at it}); cents between a lock and its rows aren't a
    claim (TJ-1576, TJ-1585). `stated`: {year: what that rebuilt year's return
    carried back}, the person's answer where the books can't tell: the row takes
    that much, from a loss the books don't hold too, as a lock's does, and no
    later lock's opening gives any of it back (TJ-4880)."""
    locked_opening = locked_opening or {}
    back_caps = back_caps or {}
    given_back, stated = given_back or {}, stated or {}
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
    # {year: how much of the balance came from losses of years after it}, for
    # each year whose entry only such a loss can meet (TJ-1565). The balance
    # is one pool, used oldest first, so a later loss is the last of it: a take
    # is capped by this and the balance. And the rows behind the balance, for
    # the cents of an entry against its lock.
    later_loss: Dict[int, float] = {}
    # The sub-cent loss rows behind the balance since the last lock, or since
    # it was used up: how far a filed figure can sit from it (TJ-1585).
    cents_behind = 0
    unmet_as_of: Dict[int, float] = {}
    expired_as_of: Dict[int, float] = {}
    rebuilt_backs: List[Tuple[int, float]] = []
    for y in years:
        net = nets.get(y, _ZERO)['net']
        loss = max(0.0, -net)
        _gap = locked_opening.get(y, 0.0) - balance
        if rebuilt_backs and _gap > 0.005 * cents_behind + 0.005:
            _given = 0.0
            _caps = dict(back_caps)
            for _ry, _back in reversed(rebuilt_backs):
                _give = min(_back, _gap - _given)
                _caps[_ry] = _back - _give
                _given += _give
            _gb = dict(given_back)
            _gb[y] = _gb.get(y, 0.0) + _given
            return build_canada_ledger(
                nets, claimed, carried, locked_back, locked_applied, own_year,
                through_year, locked_opening, _caps, _gb, stated)
        carried_in = balance
        _expired_before = sum(expired.values())
        _rec_y = nets.get(y, _ZERO)
        # The year's rows that aren't whole cents (every row, from a source
        # that doesn't say): how far its filed figures can sit from the books'.
        _rows_y = (int(_rec_y['subcent'] or 0) if 'subcent' in _rec_y
                   else max(1, int(_rec_y['dispositions'] or 0)))
        for _cy in later_loss:
            later_loss[_cy] += loss
        if loss:
            balance += loss
            _rec = nets.get(y, _ZERO)
            # A year from another source (a lock, a return row alone)
            # names no sub-cent rows: each of its dispositions counts.
            if 'filed_gap' in _rec:
                filed_over -= float(_rec['filed_gap'])
            else:
                slack += 0.005 * _rows_y
                cents_behind += _rows_y
        if claimed.get(y):
            pending_by_year[y] = pending_by_year.get(y, 0.0) + claimed[y]
            # The claim is on that year's return, met yet or not: its gain
            # isn't offered for another carry-back, and meeting it later,
            # or twice over a lock's give-back, doesn't use it again (TJ-1580).
            if y in cb_capacity:
                cb_capacity[y] = max(0.0, cb_capacity[y] - claimed[y])
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
        # What a locked year's return used of the balance, whatever its
        # [carryover] claimed entry says. That entry is the same use, and
        # the rest of it waits for a later loss. The project year's own
        # carry-back is the claims', with no recorded one to cap it: what is
        # left is theirs (TJ-1495); an earlier year's is capped by its
        # lock's below. Two paths: a claim added after that close isn't in
        # its lock's carry-back, and holding out of an earlier year would
        # lower a rebuilt balance on a partial history (TJ-1561).
        return_known = y in carried and y in locked_applied
        return_use = (max(0.0, float(locked_applied[y]))
                      if return_known else 0.0)
        held_out = entry_met = 0.0
        if y in pending_by_year and return_known:
            entry_met = min(pending_by_year[y], return_use)
            pending_by_year[y] -= entry_met
            if entry_met and pending_by_year[y] <= 0.005 * (
                    cents_behind + (0 if loss else _rows_y)) + _EPS:
                # The entry is what was filed; the lock's differs by its
                # rows' cents, the gain's own included (as in the fold
                # below; TJ-1567).
                pending_by_year[y] = 0.0
            later_loss[y] = 0.0
        if y == own_year:
            held_out = min(balance, return_use)
            balance -= held_out
        # The part met from claims recorded against EARLIER years: a
        # T1A carry-back of this year's loss (a claim pending from year
        # C found no balance until now). close-year takes it out of the
        # balance its lock carries forward.
        carried_back = 0.0
        backs: List[Tuple[int, float]] = []
        back_room = stated.get(y, back_caps.get(y))
        for _cy in sorted(pending_by_year):
            if balance <= 0.005:
                break
            take = min(balance, pending_by_year[_cy])
            if _cy in later_loss:
                take = min(take, later_loss[_cy])
            # The room, by what was taken: the cap above came first (TJ-1588).
            if back_room is not None and _cy < y:
                take = min(take, back_room)
            if take <= 0.0:
                continue
            if _cy in later_loss:
                later_loss[_cy] -= take
            if back_room is not None and _cy < y:
                back_room -= take
            balance -= take
            applied += take
            if _cy < y:
                carried_back += take
                backs.append((_cy, take))
            pending_by_year[_cy] -= take
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
        balance += held_out
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
                if _cy in later_loss:
                    later_loss[_cy] += give
                balance += give
                applied -= give
                carried_back -= give
                excess -= give
                backs[_i] = (_cy, take - give)
        if (y in carried and y != own_year) or y in stated:
            # ...and on a history that rebuilds too little to reach it, the
            # lock still took it: the claims it met aren't left for a later
            # year's loss to meet again (TJ-1554), down to its rows' cents
            # (TJ-1577). The project year's carry-back is the claims' (TJ-1579).
            short = float(stated.get(y, locked_back.get(y)) or 0.0) - carried_back
            for _cy in sorted(c for c in pending_by_year if c < y):
                if short <= 0.005:
                    break
                take = min(short, pending_by_year[_cy])
                pending_by_year[_cy] -= take
                if pending_by_year[_cy] <= 0.005 * cents_behind + _EPS:
                    pending_by_year[_cy] = 0.0
                if _cy in later_loss:
                    later_loss[_cy] = max(0.0, later_loss[_cy] - take)
                applied += take
                carried_back += take
                short -= take
                backs.append((_cy, take))
        pending_by_year = {k: v for k, v in pending_by_year.items()
                           if v > 0.005}
        rebuilt_balance = None
        over_balance = None
        if y in carried:
            rebuilt_balance = balance
            balance = max(0.0, float(carried[y]))
            if y == own_year:
                balance = max(0.0, balance - max(
                    0.0, carried_back - float(locked_back.get(y) or 0.0)))
            slack = filed_over = 0.0
            # What the row shows, and what's left of the gain to offer: an
            # entry under the year wins over the lock (0 is one, CA-CARRY-04;
            # TJ-0052, TJ-1569). The fold above used the return's.
            shown_use = entry_met if y in claimed else return_use
            applied += shown_use
            if y in cb_capacity and y not in claimed:
                cb_capacity[y] = max(0.0, cb_capacity[y] - shown_use)
            if (prev_from_lock and y not in claimed and return_use > carried_in
                    and M.cents_apart(return_use, carried_in) > 1):
                # The lock applied more than the record before it
                # carries in: that year closed again since (an
                # amendment) and this return is built on its old
                # balance (backlog TJ-0430).
                over_balance = (return_use, carried_in)

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
                carryback.append({'year': target, 'amount': M.shown(amt, 2)})
                cb_capacity[target] = cap - amt
                remaining -= amt
        row = {
            'year': y,
            'net_gain': M.shown(net, 2),
            'dispositions': nets.get(y, _ZERO)['dispositions'],
            'claimed_applied': M.shown(applied, 2),
            'carried_back': M.shown(carried_back, 2),
            'carryforward_balance': M.shown(balance, 2),
            # The gain the year's return already sheltered — by the
            # losses its lock applied, or by its own [carryover]
            # claimed entry (the total applied on that return, met now
            # or by a later year's carry-back) — is not offered again
            # (backlog TJ-0052, TJ-0223).
            'available_to_apply': M.shown(min(balance, max(
                0.0, net - (max(0.0, float(claimed[y] or 0.0))
                            if y in claimed else return_use))), 2)
            if net > 0 else 0.0,
            'carryback_candidates': carryback,
        }
        if rebuilt_balance is not None:
            row['balance_from_lock'] = True
            # The books' balance at the year end, the losses the lock
            # applied taken out as a claim would be.
            row['rebuilt_balance'] = M.shown(max(
                0.0, rebuilt_balance - return_use), 2)
        if over_balance is not None:
            row['applied_over_balance'] = {
                'applied': M.shown(over_balance[0], 2),
                'carried_in': M.shown(over_balance[1], 2)}
        prev_from_lock = y in carried
        if carried_back > 0.005:
            # The carry-back by the year it went to: handoff checks a
            # claim under the closed year less the part this year's loss
            # carries back to it (backlog TJ-0078).
            _to: Dict[str, float] = {}
            for _cy, take in backs:
                _to[str(_cy)] = _to.get(str(_cy), 0.0) + take
            row['carried_back_to'] = {k: M.shown(v, 2) for k, v in
                                      sorted(_to.items()) if v > 0.005}
        if y in given_back:
            # Of the claims still unmet after this lock's own carry-back, what
            # rests on the give-back: the lock met the rest (TJ-1583, TJ-1584).
            _unmet = (sum(v for c, v in pending_by_year.items() if c < y)
                      + sum(expired.values()) - _expired_before)
            _said = min(given_back[y], _unmet)
            if _said > 0.005:
                row['claims_given_back'] = M.shown(_said, 2)
        rows.append(row)
        if y in carried or balance <= 0.005:
            # The balance restarts, from the lock or from nothing: no row's
            # cents are behind it. After the lock's own snap above.
            cents_behind = 0
        if y in carried:
            rebuilt_backs = []
        elif carried_back > 0.0 and y not in stated:
            rebuilt_backs.append((y, carried_back))
        if through_year is None or y <= through_year:
            unmet_as_of, expired_as_of = dict(pending_by_year), dict(expired)
    out = {'country': 'canada', 'rows': rows,
           'final_carryforward': M.shown(balance, 2)}
    _unmatched = sum(unmet_as_of.values()) + sum(expired_as_of.values())
    if _unmatched > 0.005:
        out['unmatched_claims'] = M.shown(_unmatched, 2)
    # Of those, a claim a later year's loss can still reach (three years, ITA
    # s.111(1)(b)): the entry may be right, or it may not (backlog TJ-1551,
    # TJ-1556). The others, unrounded, so that cents can't make a line of it
    # (TJ-1560). Those past their window have their own line (TJ-1570).
    _now = through_year if through_year is not None else (
        years[-1] if years else None)
    _waits = {c: v for c, v in sorted(unmet_as_of.items())
              if _now is not None and _now < c + 3}
    if _waits:
        out['claims_waiting'] = {str(c): M.shown(v, 2) for c, v in _waits.items()}
    _beyond = sum(v for c, v in unmet_as_of.items() if c not in _waits)
    if _beyond > 0.005:
        out['claims_beyond'] = M.shown(_beyond, 2)
    _exp = {str(k): M.shown(v, 2) for k, v in sorted(expired_as_of.items())
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
            _over[str(c)] = {'claimed': M.shown(float(amt), 2),
                             'net_gain': M.shown(gain, 2),
                             'excess': M.shown(float(amt) - gain, 2)}
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
                    _warn(f"claimed {M.shown(claimed[y], 2):,.2f} for {y} exceeds the "
                          f"{M.shown(offset_cap, 2):,.2f} the worksheet supports — "
                          f"capped")
            else:
                offset = offset_cap
            st_carry, lt_carry = _us_worksheet(st_net, lt_net, offset)
        else:
            offset = 0.0
            st_carry = lt_carry = 0.0
        row = {
            'year': y,
            'net_gain': M.shown(rec['net'], 2),
            'net_st': M.shown(rec['st'], 2),
            'net_lt': M.shown(rec['lt'], 2),
            'dispositions': rec['dispositions'],
            'ordinary_income_offset': M.shown(offset, 2),
        }
        if y in carried:
            row['balance_from_lock'] = True
            row['rebuilt_st_carryover'] = M.shown(st_carry, 2)
            row['rebuilt_lt_carryover'] = M.shown(lt_carry, 2)
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
                    else 'assumed_offset'] = M.shown(offset, 2)
                row['ordinary_income_offset'] = M.shown(used, 2)
        row['st_carryover'] = M.shown(st_carry, 2)
        row['lt_carryover'] = M.shown(lt_carry, 2)
        if abs(float(rec.get('s1256') or 0.0)) > 0.005:
            # The §1256 net in NET ST / NET LT, and the parts: Form
            # 6781's 40% / 60% (US-FUT-03; backlog TJ-0466), or all
            # short-term where a lock closed before the split carried it
            # so (backlog TJ-0611).
            s = M.shown(float(rec['s1256']), 2)
            row['section_1256'] = s
            if rec.get('s1256_unsplit'):
                row['section_1256_unsplit'] = True
                row['section_1256_short_term'] = s
                row['section_1256_long_term'] = 0.0
            else:
                lt = M.shown(section_1256_split(s)[1], 2)
                row['section_1256_short_term'] = M.shown(s - lt, 2) + 0.0
                row['section_1256_long_term'] = lt + 0.0
        rows.append(row)
    return {'country': 'usa', 'rows': rows,
            'final_carryforward': M.shown(st_carry + lt_carry, 2),
            'final_st_carryover': M.shown(st_carry, 2),
            'final_lt_carryover': M.shown(lt_carry, 2)}


_money = fmt_money                  # shared report-layer formatter


# ------------------------------------------------------- filed-year locks

class LockUnreadable(ValueError):
    """A filed-year lock that could not be read or holds no usable
    figure."""


# The gain lines a return carries (close-year's form_lines keys).
# 10690/10694: the 2024 form's Period 1 lines (A2-0166).
_GAIN_LINES = {'canada': ('13200', '15300', '15301', '10690', '10694'),
               'usa': ('I_gain', 'II_gain')}

# A carry-forward's `opening_source` when the person gave the figure.
TYPED_OPENING = ("[estimate] other_losses", "--other-losses")

LOCK_SOURCE_TEXT = {
    'filed_totals': "the total filed with another tool: filed_totals",
    'return_row': "the RETURN row close-year recorded: return_row",
    'form_lines': "the filed gain lines: form_lines",
    'realized': "the lock's realized total",
}


def _record_closing_now(lock: Path, opening_source: str) -> Optional[float]:
    """The closing the record a lock's opening came from carries now, when it
    can be found and is that year's: `opening_source` is its label, a path
    relative to the lock's project (the folder above filed/) or absolute, then
    " (the YYYY close-year record)". None when it can't be read: the lock's
    word stands (TJ-1587)."""
    from taxjson.lib import filed_locks
    rec = filed_locks.record_of_label(lock, opening_source)
    if rec is None:
        return None
    _year, p = rec
    try:
        doc = filed_locks.load_lock_json(p)
        if int(doc.get('year')) != _year:
            return None
        v = float(doc['carryforwards']['net_capital_loss']['closing'])
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    return v if math.isfinite(v) else None


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
    reader of a lock applies (filed_locks.read_lock: unreadable,
    damaged — its carryforwards block too — or closed under the other
    country's law), whose filed dispositions hold another year's rows
    (backlog TJ-0347), or whose numbers are missing or not finite (NaN,
    Infinity), raises LockUnreadable naming the lock and the fix — it
    never silently matches (A2-0336, A2-1131; 2026-10-25 audit
    year-end#1, robustness#1)."""
    from taxjson.lib import filed_locks
    try:
        doc = filed_locks.read_lock(Path(path), {"country": country},
                                      prior=True, carried=True)
    except filed_locks.LockProblem as e:
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
                     else filed_locks.file_year(Path(path))),
            'dispositions': int(totals.get('dispositions') or 0),
            'realized': realized, 'filed_totals_gain': ft_gain,
            # Taken before its year ended (close-year --force): a
            # snapshot, not the record of a filed return.
            'partial': bool(filed_locks.partial_year_note(
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
                # What it took in from the record before it. A figure from
                # elsewhere ([estimate] other_losses, the books) says nothing of
                # what that record's return carried back (TJ-1574).
                _ncl = cf['net_capital_loss']
                _os = str(_ncl.get('opening_source') or '')
                if _os.startswith(TYPED_OPENING) and _ncl.get('opening') is not None:
                    # The person's own figure for the balance carried in: a
                    # fact about the years before it (TJ-4885).
                    out['opening_typed'] = _finite(_ncl['opening'],
                                                   'net_capital_loss.opening')
                if 'close-year record)' in _os and _ncl.get('opening') is not None:
                    _op = _finite(_ncl['opening'], 'net_capital_loss.opening')
                    _now = _record_closing_now(path, _os)
                    if _now is not None and M.cents_apart(_now, _op) > 1:
                        # That record was closed again since (TJ-0430): the
                        # lock's opening says nothing of what was carried back
                        # until this year is closed again (TJ-1587).
                        out['opening_stale'] = {'opening': _op,
                                                'record_now': _now}
                    else:
                        out['opening'] = _op
                # ...and the earlier losses it applied (locked_applied), if
                # it says.
                if cf['net_capital_loss'].get('applied') is not None:
                    out['applied'] = _finite(
                        cf['net_capital_loss']['applied'],
                        'net_capital_loss.applied')
    except (KeyError, TypeError, ValueError) as e:
        raise LockUnreadable(
            f"{path}: no usable filed figure ({type(e).__name__}: {e}) — "
            f"{filed_locks.LOCK_FIX}") from None
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


def _final_line(ledger: Dict[str, Any], final_year: int) -> str:
    """Canada: the balance carried out of the final year, or that the ledger
    can't tell it, when it differs with what a rebuilt year's return carried
    back (`_canada_ledger`; backlog TJ-1574). Two equal amounts are one
    amount: the question is then the year's carry-back alone
    (`_cant_tell_lines`; backlog TJ-4884)."""
    ct = _CB.cant_tell(ledger)
    amount = _money(ledger['final_carryforward'])
    if ct and abs(ct['if_all']['carryforward'] - ct['if_none']['carryforward']) > 0.005:
        amount = (f"can't tell ({_money(ct['if_all']['carryforward'])} if "
                  f"{_CB.years_text(ct['years'])} carried back what these "
                  f"books show, {_money(ct['if_none']['carryforward'])} if "
                  f"nothing)")
    return f"Net-capital-loss carryforward after {final_year}: {amount}"


def _cant_tell_lines(ledger: Dict[str, Any], final_year: int) -> List[str]:
    """Canada: the final year's carry-back, when the ledger can't tell it."""
    ct = _CB.cant_tell(ledger)
    if not ct:
        return []
    return [f"T1A carry-back of {final_year}'s loss: can't tell "
            f"({_money(ct['if_all']['carried_back'])} if "
            f"{_CB.years_text(ct['years'])} carried back what these books "
            f"show, {_money(ct['if_none']['carried_back'])} if nothing)"]


def _stated_lines(rows: Any) -> List[str]:
    """Canada: a carry-back that is the person's figure, or one a balance they
    gave settles, said to be, with where its bounds come from."""
    out = [f"{r['year']}: carried back {_money(r['carried_back_stated'])}, "
           f"your figure ([carryover] carried_back), not these books'"
           for r in rows if r.get('carried_back_stated') is not None]
    for r in rows:
        inf = r.get('carried_back_inferred')
        if not inf or r['year'] != inf['years'][0]:
            continue                # (years settled together: one line)
        out.append(
            f"{_CB.years_text(inf['years'])}: carried back at least "
            f"{_money(inf['at_least'])}"
            + (" in all" if len(inf['years']) > 1 else "")
            + f" (these books leave {_money(inf['books_leave'])}, you carried "
            f"{_money(inf['carried_in'])} into {inf['into']}: [estimate] "
            f"other_losses), at most {_money(inf['at_most'])} ("
            + ("that balance again, after the lock before"
               if inf['ceiling'] == 'balance'
               else "the [carryover] claimed amounts in reach")
            + "); from the books' nets: a T3 or FX gain on that return changes "
            "it, and [carryover] carried_back overrides it")
    return out


def _before_books_lines(ledger: Dict[str, Any]) -> List[str]:
    """Canada: what the person's entries say a balance they gave holds from
    before the books."""
    return [f"Your [carryover] carried_back entries leave {_money(h['leaves'])} "
            f"carried into {h['into']}; the {_money(h['carried_in'])} you carried "
            f"in holds {_money(h['before_books'])} from before these books"
            for h in ledger.get('carried_in_before_books') or []]


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
        lines.append(_final_line(ledger, _final_year))
        lines.extend(_cant_tell_lines(ledger, _final_year) + _stated_lines(rows)
                     + _before_books_lines(ledger))
    else:
        _st = ledger['final_st_carryover']
        _lt = ledger['final_lt_carryover']
        lines.append(f"Carryover after {_final_year}: "
                     f"{_money(M.shown(_st + _lt, 2))} (ST {_money(_st)} + "
                     f"LT {_money(_lt)})")
    acts: List[str] = []
    cmd = "tjs carryover --details"
    if country == 'canada':
        if _CB.cant_tell(ledger):
            acts.append(_CB.cant_tell_act(ledger))
        for r in rows:
            if r['available_to_apply'] > 0.005:
                acts.append(f"{r['year']}: apply up to "
                            f"{_money(r['available_to_apply'])} of the "
                            f"carryforward")
            for cb in r['carryback_candidates']:
                acts.append(f"{r['year']}: {_money(cb['amount'])} can be "
                            f"carried back to {cb['year']} (T1A)")
        for _y, _amt in (ledger.get('claims_waiting') or {}).items():
            acts.append(f"{_money(_amt)} claimed for {_y} beyond the losses "
                        f"this history supports; a loss by {int(_y) + 3} can "
                        f"still meet it")
        if ledger.get('claims_beyond'):
            acts.append(f"{_money(ledger['claims_beyond'])} claimed "
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
            if _ob:
                pass
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
    _render_balance(ledger, rows, country, para, in_project, _W, _claims)
    _render_notes(lines, country, item, _claims, claimed_used, in_project, ledger, rows, _W)
    _render_row_notes(rows, item, _W, ledger, country, _claims, first_tx_year)
    return "\n".join(lines)


def _render_balance(ledger: Dict[str, Any], rows: Any, country: Any, para: Any, in_project: bool,
                    _W: str, _claims: str) -> None:
    """carryover: the balance carried out of the final year, and in Canada
    the warnings on claims the history does not support."""
    _final_year = ledger.get('final_year', rows[-1]['year'])
    if country == 'canada':
        para(_final_line(ledger, _final_year))
        for _line in _cant_tell_lines(ledger, _final_year):
            para(_line)
        _check = ("taxjson.toml's [carryover] claimed." if in_project
                  else "the claimed file.")
        for _y, _amt in (ledger.get('claims_waiting') or {}).items():
            para(f"{_W}{_money(_amt)} of {_claims} amounts under {_y} exceed "
                 f"the losses this history supports. A loss of a year up to "
                 f"{int(_y) + 3} can still carry back to {_y} (T1A); if none "
                 f"will, check {_check}", "", "  ")
        if ledger.get('claims_beyond'):
            para(f"{_W}{_money(ledger['claims_beyond'])} "
                 f"of {_claims} amounts exceed the losses this "
                 f"history supports — check {_check}", "", "  ")
        for _y, _amt in (ledger.get('expired_claims') or {}).items():
            para(f"{_W}{_money(_amt)} claimed for {_y} "
                 f"was never met by a loss of {_y} or earlier "
                 f"in these books, and a later loss can reach "
                 f"back only 3 years (ITA 111(1)(b)) — it came "
                 f"from losses before this history, so it does "
                 f"not reduce the carryforward shown.", "", "  ")
        for _line in _stated_lines(rows) + _before_books_lines(ledger):
            para(f"- {_line}.", "", "  ")
        if _CB.cant_tell(ledger):
            para(f"{_W}{_CB.cant_tell_text(ledger)}. The rows above are "
                 f"working, not an answer. {_CB.CANT_TELL_WAYS}", "", "  ")
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


def _render_notes(lines: List[str], country: Any, item: Any, _claims: str, claimed_used: bool,
                  in_project: bool, ledger: Dict[str, Any], rows: Any, _W: str) -> None:
    """carryover: the NOTES heading and its notes on the ledger, the
    country's rules, filing positions, the scope and unread claims."""
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
            elif r.get('net_source') == 'filed_return':
                item(f"{r['year']}'s NET GAIN(LOSS) has the return filed with "
                     f"another tool; the books' total is "
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


def _render_row_notes(rows: Any, item: Any, _W: str, ledger: Dict[str, Any], country: Any,
                      _claims: str, first_tx_year: Optional[int]) -> None:
    """carryover: the notes on particular rows: partial years, filed
    figures and balances from locks, slip gains, prior years, and
    where the history starts."""
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


@guard_main("taxjson-carryover", value_errors=True)
def main(argv: Optional[List[str]] = None) -> int:
    parser = _carryover_parser()
    _carryover_options(parser)
    args = parser.parse_args(argv)
    global _DETAILS
    _DETAILS = not args.brief
    _stop, _rc = _carryover_check_args(args)
    if _stop:
        return _rc

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
        _out.put(f"taxjson-carryover: --base-currency {base_cur} does not "
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

    (_stop, _rc, country, lock_info, locked_years, locked_records, crypto_txs,
     transactions) = _carryover_locks(args, crypto_loaded, transactions)
    if _stop:
        return _rc
    _stop, _rc, req, results, nets, manual = _carryover_gains(args, locked_years, locked_records,
                                                              country, transactions, sheltered,
                                                              crypto_txs)
    if _stop:
        return _rc
    claimed_ignored: List[str] = []
    try:
        claimed = load_claimed(args.claimed, ignored=claimed_ignored)
    except (OSError, UnicodeDecodeError) as exc:
        # A directory or an unreadable file: one line, not a traceback
        # (S027-19).
        _out.put(f"taxjson-carryover: cannot read claimed file "
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
        _out.put(f"taxjson-carryover: {exc}", file=sys.stderr)
        return 2
    (slip_by, seeded, engine_net, filed_net, ledger, first_tx_year) = _carryover_ledger(
        slip, lock_info, args, country, nets, return_row, claimed, transactions, crypto_txs,
        results, claimed_ignored)
    _carryover_rows(args, ledger, seeded, country, slip_by, engine_net, filed_net, lock_info, filed)
    _carryover_manual(manual, req, ledger)

    # A live exact Doc: each line printed when it is added, as the
    # print() it replaces printed it.
    d = _out.Doc(exact=True, width_=0, live=True)
    if args.json:
        ledger["currency"] = args.base_currency.upper()
        if args.missing_book:
            ledger["missing_books"] = list(args.missing_book)
        from taxjson.lib.json_contract import versioned
        from taxjson.lib.json_input import dump_filing_json
        dump_filing_json(versioned(ledger, "carryover"), sys.stdout)
        d.blank()
    elif args.brief:
        d.line(render_brief(ledger, args.base_currency.upper(),
                            first_tx_year))
    else:
        d.line(render(ledger, args.base_currency.upper(), first_tx_year,
                      claimed_used=bool(claimed),
                      in_project=args.claims_in_project))
    return 0


def _carryover_parser() -> argparse.ArgumentParser:
    """carryover: the parser, with its book, claim, year and lock options."""
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
    parser.add_argument("--missing-book", action="append", default=[],
                        metavar="ACCOUNT",
                        help="A taxable account with no base file (the "
                             "wrapper passes them): --json names them in "
                             "missing_books, for a caller that needs the "
                             "whole ledger.")
    parser.add_argument("--filed-lock", action="append", default=[],
                        metavar="YEAR=PATH",
                        help="A filed year's close-year lock (the wrapper "
                             "passes filed/<year>.json and the "
                             "prior_year_record lock). A year before "
                             "--project-year takes the lock's FILED "
                             "figure instead of the rebuilt one; a later "
                             "year is compared with it. An unreadable "
                             "lock is named.")
    parser.add_argument("--filed-net", type=float, default=None,
                        help="Canada: the project year's gain on a return "
                             "filed with another tool, for its NET GAIN(LOSS) "
                             "(close-year, before its lock exists).")
    parser.add_argument("--on-books", action="store_true",
                        help="Canada: fold the project year on its books, "
                             "with its own lock not read at all (`tjs "
                             "estimate`, and so a plain close-year).")
    parser.add_argument("--return-row", action="append", default=[],
                        metavar="YEAR=GAIN",
                        help="Canada: a year's gain as the return files "
                             "it (`tjs sum`'s RETURN row, each "
                             "Schedule 3 row's cents): that year's NET "
                             "GAIN(LOSS) instead of the engine's unrounded "
                             "total (the wrapper passes the project "
                             "year's); a year a lock seeds keeps the "
                             "lock's figure.")
    parser.add_argument("--carried-back-year", action="append", default=[],
                        metavar="YEAR=AMOUNT",
                        help="Canada: the net capital loss that year's return "
                             "carried back (T1A), where the ledger says it "
                             "can't tell ([carryover] carried_back; the "
                             "wrapper passes it).")
    parser.add_argument("--carried-in", type=float, default=None, metavar="AMOUNT",
                        help="Canada: the net capital loss the estimate takes "
                             "into the project year (the wrapper passes it).")
    parser.add_argument("--carried-in-typed", action="store_true",
                        help="Canada: --carried-in is the person's own figure "
                             "([estimate] other_losses), not a lock's.")
    parser.add_argument("--slip-gains", action="append", default=[],
                        metavar="YEAR=AMOUNT",
                        help="Canada: capital gains from slips (T5 box 18 "
                             "dividends in [[capital_gains_dividends]]) "
                             "that year — added to the year's net (the "
                             "wrapper passes them).")
    return parser


def _carryover_options(parser: argparse.ArgumentParser) -> None:
    """carryover: the parser's loss-override, income-dating, currency,
    output, option-timing and wrapper options."""
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


def _carryover_ledger(slip: List[Any], lock_info: Dict[int, Dict[str, Any]],
                      args: argparse.Namespace, country: Any, nets: Dict[int, Dict[str, float]],
                      return_row: Dict[int, float], claimed: Dict[int, float],
                      transactions: List[Any], crypto_txs: List[Any], results: Dict[Any, Any],
                      claimed_ignored: List[str]) -> Tuple[
        Dict[int, float], Dict[int, Dict[str, Any]], Dict[int, float], Any, Dict[str, Any],
        Optional[int]]:
    """carryover: the ledger: the nets the locks seed (a year before
    the project year takes its lock's filed figure and balance), the
    RETURN rows, the filed net and the slip gains; with the books' first
    year, the scope note, the filing positions, the claims not read."""
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
    locked_opening: Dict[int, float] = {}
    typed_opening: Dict[int, float] = {}
    own_year: Optional[int] = None
    own_filed: Optional[float] = None
    locked_deduction: Dict[int, float] = {}
    for y, info in lock_info.items():
        if (args.project_year is not None and y < args.project_year
                and info.get('carried') is not None):
            carried[y] = info['carried']
            locked_back[y] = float(info.get('carried_back') or 0.0)
            if info.get('applied') is not None:
                locked_applied[y] = float(info['applied'])
            if info.get('opening') is not None:
                locked_opening[y] = float(info['opening'])
            typed_opening.update({y: info['opening_typed']} if 'opening_typed' in info else {})
            if info.get('opening_stale'):
                from taxjson.lib.out import warn as _warn
                _st = info['opening_stale']
                _warn(f"{y}: {info['path']} opened with "
                      f"{M.shown(_st['opening'], 2):,.2f}, but the record it took that "
                      f"from now carries {M.shown(_st['record_now'], 2):,.2f}: {y} was "
                      f"not closed again after that record changed",
                      details=[f"The ledger takes nothing from that opening. "
                               f"Close {y} again in its project, then this "
                               f"year."])
            if info.get('deduction') is not None:
                locked_deduction[y] = float(info['deduction'])
        elif (country == 'canada' and y == args.project_year
              and info.get('carried') is not None
              and not info.get('partial')
              and not args.on_books and args.filed_net is None):
            # The project year closed: its row shows what its lock
            # applied and carried into the next year, as a year before
            # it does — the ledger offered again the loss close-year
            # had used (backlog TJ-0433). Its net is what was filed when the
            # lock records a return filed with another tool, and the books'
            # otherwise (TJ-1473, TJ-1485, TJ-1486): only a lock read here, so
            # never a damaged or partial one, and not by the split a close is
            # about to record, or the lock being replaced would reach it
            # (TJ-1473, TJ-1559).
            own_year = y
            own_filed = info.get('filed_totals_gain')
            carried[y] = info['carried']
            locked_back[y] = float(info.get('carried_back') or 0.0)
            if info.get('applied') is not None:
                locked_applied[y] = float(info['applied'])
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
    # The one answer to what the project year's net is. See `tjs tax-logic`
    # CA-CARRY-01 (backlog TJ-1493).
    filed_net = args.filed_net if args.filed_net is not None else (
        None if args.on_books else own_filed)
    if filed_net is not None and args.project_year is not None:
        return_row[args.project_year] = filed_net
        if args.filed_net is None:
            # Its lock's slips too, as a year before it takes them: not a map
            # added after that return was filed (backlog TJ-1494).
            locked_slip = lock_info[args.project_year].get('slip_gains')
            slip_by.pop(args.project_year, None)
            if locked_slip is not None:
                slip_by[args.project_year] = float(locked_slip)
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
        ledger = _canada_ledger(args, nets, claimed, carried, locked_back,
                                locked_applied, own_year, locked_opening, typed_opening)
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
    return slip_by, seeded, engine_net, filed_net, ledger, first_tx_year


def _stated_backs(args: argparse.Namespace) -> Dict[int, float]:
    """--carried-back-year YEAR=AMOUNT ([carryover] carried_back): {year: the
    net capital loss that year's return carried back}; a line that is no such
    amount stops the ledger."""
    out: Dict[int, float] = {}
    for item in args.carried_back_year:
        try:
            y_s, v_s = item.split("=", 1)
            y, v = int(y_s), _finite(v_s, "--carried-back-year")
            if v < 0 or y in out:
                raise ValueError
        except ValueError:
            _out.put(f"taxjson-carryover: --carried-back-year expects "
                     f"YEAR=AMOUNT, an amount >= 0, once a year, got {item!r}",
                     file=sys.stderr)
            raise SystemExit(2) from None
        out[y] = v
    return out


def _year_figures(ledger: Dict[str, Any], year: int,
                  carried_in: Optional[float]) -> Dict[str, Any]:
    """What a year's close records, read off a Canadian ledger: its carry-back
    and the loss carried out of it, as `tjs estimate` and `tjs close-year`
    split it. `carried_in`: the balance the estimate takes in ([estimate]
    other_losses, else the last lock's); with the year's net (the RETURN row
    and the T5 box 18 gains, the figure the estimate nets too) it gives the
    loss the estimate has left, which caps the row's carry-back: a gain year
    applies the balance to its gain first. None (the tool run alone): the
    row's own carry-back and the ledger's balance."""
    upto = [r for r in ledger['rows'] if r['year'] <= year]
    row = upto[-1] if upto and upto[-1]['year'] == year else {}
    back = row.get('carried_back', 0.0)
    if carried_in is None:
        return {'carried_back': back, 'carried_back_to': row.get('carried_back_to', {}),
                'carryforward': upto[-1]['carryforward_balance'] if upto else 0.0}
    left = max(0.0, carried_in - (row.get('net_gain') or 0.0))
    took = min(back, left)
    return {'carried_back': M.shown(took, 2),
            'carried_back_to': row.get('carried_back_to', {}) if took > back - 0.005 else {},
            'carryforward': M.shown(left - took, 2), 'loss_left': M.shown(left, 2)}


def _refuse_stated(text: str) -> None:
    from taxjson.lib.out import error as _error
    _error(text, prog="taxjson-carryover")
    raise SystemExit(1)


class _Readings:
    """What a Canadian ledger can and can't tell about the years it rebuilds.

    A year before the project year with no lock is rebuilt, and the fold meets
    a [carryover] claimed amount from its loss or the balance it took in; the
    return may have carried back nothing (the claim was a later year's T1A),
    or the same from a loss these books no longer hold. A lock after it that
    opened from that year's record checks the row, when it opened no higher
    than the row left (`claims_given_back` otherwise); every other rebuilt
    year is unchecked.

    Two readings bracket what the unchecked years could have carried back:
    the least and the most (claims met oldest first, the earliest year first,
    as everywhere in the ledger). A balance the person gave narrows them:
    [estimate] other_losses of the project year, or of the first lock after
    those years (its opening, when typed in), is the balance carried in. The
    unchecked years' losses, less what they applied to their gains, less what
    they carried back, plus whatever they started from, leave that balance.
    After a lock the start is known and the balance says what they carried
    back; with no lock before them a loss from before the books may be in the
    balance, so it sets a floor only: at least what the books leave less the
    balance (the owner's ruling of 2026-11-21: the books are not taken as the
    whole history). A year's own loss is not applied in that year; what a gain
    year with no [carryover] claimed entry applied is anything from nothing
    to its gain (the deduction is the taxpayer's choice, ITA s.111(1)(b)),
    which lowers the floor. Floor and ceiling (the claims those years could
    meet) may meet in one figure. A balance no reading fits narrows nothing.

    The question is the estimate's: with a loss left to split in the project
    year, does its carry-back differ between the readings? Then the ledger
    says so and its rows are working; otherwise it folds on the most the
    facts allow, and a year the balance settles says where its figure came
    from (backlog TJ-1574, TJ-4880, TJ-4884, TJ-4885)."""

    def __init__(self, build: Any, year: Optional[int], carried: Dict[int, Any],
                 locked_opening: Dict[int, float], typed: Dict[int, float],
                 carried_in: Optional[float]) -> None:
        self.build, self.year, self.carried = build, year, carried
        self.locked_opening, self.typed, self.carried_in = locked_opening, typed, carried_in
        self.locks = sorted(y for y in carried if year is not None and y < year)

    @staticmethod
    def back(ledger: Dict[str, Any], y: int) -> float:
        return next((r['carried_back'] for r in ledger['rows'] if r['year'] == y), 0.0)

    def unchecked(self, given: Dict[int, float]) -> List[int]:
        base, out = self.build(given), []
        for r in base['rows'] if self.year is not None else ():
            y = r['year']
            if y >= self.year or y in self.carried or y in given:
                continue
            after = next((k for k in self.locks if k > y), None)
            if after is None or after not in self.locked_opening or next(
                    x for x in base['rows'] if x['year'] == after).get('claims_given_back'):
                out.append(y)
        return out

    def _spans(self, free: List[int]) -> List[Tuple[Optional[int], List[int]]]:
        """The unchecked years, grouped by the year they lead into: the first
        lock after them, else the project year."""
        spans: Dict[int, List[int]] = {}
        for y in free:
            into = next((k for k in self.locks if k > y), self.year)
            spans.setdefault(into, []).append(y)
        return sorted(spans.items())

    def _range(self, given: Dict[int, float], into: int, years: List[int],
               took: Dict[int, float]) -> Tuple[float, float, Optional[float], float]:
        """(least, most, the balance given, what the books leave) for what those
        years carried back in all. With a lock before them the balance they
        start from is known, and the balance given says what they carried back
        (a range, with a gain year between). With none, a loss from before the
        books may be in the balance given: it sets a floor only."""
        most = sum(took[y] for y in years)
        fact = self.typed.get(into)
        if fact is None:
            return 0.0, most, None, 0.0
        rows = [r for r in self.build({**given, **dict.fromkeys(years, 0.0)})['rows']
                if r['year'] < into]
        left = rows[-1]['carryforward_balance'] if rows else 0.0
        applied = sum(r['available_to_apply'] for r in rows
                      if r['year'] >= years[0] and r['year'] not in self.carried)
        low = max(0.0, left - fact - applied)
        high = min(most, left - fact) if any(k < years[0] for k in self.locks) else most
        if low > high + 0.005:
            return 0.0, most, None, left    # (no reading leaves that balance)
        return low, max(low, high), fact, left

    def _typed_spans(self, ledger: Dict[str, Any], given: Dict[int, float]) -> Any:
        """(into, the balance given, the least and the most `ledger` leaves
        there, whether a lock comes before) for each balance the person gave
        that follows years they answered for."""
        for into, fact in sorted(self.typed.items()):
            before = max((k for k in self.locks if k < into), default=None)
            rows = [r for r in ledger['rows'] if r['year'] < into]
            span = [r for r in rows if (before is None or r['year'] > before)]
            if not rows or not any(r['year'] in given for r in span):
                continue
            left = rows[-1]['carryforward_balance']
            least = max(0.0, left - sum(r['available_to_apply'] for r in span))
            yield into, fact, least, left, before is not None

    def clash(self, ledger: Dict[str, Any], given: Dict[int, float]
              ) -> Optional[Tuple[int, float, float, float]]:
        """(into, the balance given, the least and the most `ledger` leaves
        there) for a balance the person gave that their [carryover]
        carried_back entries contradict: one below what they leave, whatever
        the gains between took. One above it is a contradiction only after a
        lock: with none, the rest is a loss from before the books."""
        for into, fact, least, left, fixed in self._typed_spans(ledger, given):
            if fact < least - 0.005 or (fixed and fact > left + 0.005):
                return into, fact, least, left
        return None

    def before_books(self, ledger: Dict[str, Any], given: Dict[int, float]
                     ) -> List[Dict[str, Any]]:
        """What the person's entries say was in a balance they gave from before
        the books: [{into, carried_in, leaves, before_books}]."""
        return [{'into': into, 'carried_in': M.shown(fact, 2), 'leaves': M.shown(left, 2),
                 'before_books': M.shown(fact - left, 2)}
                for into, fact, _least, left, fixed in self._typed_spans(ledger, given)
                if not fixed and fact > left + 0.005]

    def question(self, given: Dict[int, float]) -> Dict[str, Any]:
        """{asked: the `carry_back_cant_tell` entry or None, free: the unchecked
        years, fold: {year: what the ledger's rows take, where a balance given
        limits them}, settled: {year: (amount, balance, into, least, most, what
        the books leave)}, a year the balance settles alone; inferred: {year:
        the `carried_back_inferred` entry} of every year a balance settles,
        alone or with others (`years`: their total is what is settled)}."""
        free = self.unchecked(given)
        low: Dict[int, float] = {}
        high: Dict[int, float] = {}
        fold: Dict[int, float] = {}
        settled: Dict[int, Tuple[float, ...]] = {}
        inferred: Dict[int, Dict[str, Any]] = {}
        open_years: List[int] = []
        for into, years in self._spans(free):
            parts = []
            for side in (low, high):
                took = {y: self.back(self.build({**given, **side}, {}), y) for y in years}
                parts.append((took, self._range({**given, **side}, into, years, took)))
            for side, (took, _rng), amount in ((low, parts[0], parts[0][1][0]),
                                              (high, parts[1], parts[1][1][1])):
                for y in years:
                    side[y] = min(took[y], amount)
                    amount -= side[y]
                if amount > 0.005:              # (a loss the books don't hold)
                    side[years[-1]] += amount
            fact = parts[1][1][2]
            if fact is not None:
                fold.update((y, high[y]) for y in years)
            if fact is not None and all(abs(low[y] - high[y]) <= 0.005 for y in years):
                takers = [y for y in years if high[y] > 0.005 or parts[1][0][y] > 0.005]
                if len(takers) == 1:
                    settled[takers[0]] = (high[takers[0]], fact, into) + (
                        parts[1][1][0], parts[1][1][1], parts[1][1][3])
                _low, _high, _fact, _left = parts[1][1]
                # After a lock the balance is the ceiling too, when it is
                # under what the claims would allow.
                _most = sum(parts[1][0][y] for y in years)
                inferred.update((y, {
                    'carried_in': M.shown(fact, 2), 'into': int(into),
                    'at_least': M.shown(_low, 2), 'at_most': M.shown(_high, 2),
                    'books_leave': M.shown(_left, 2), 'years': takers,
                    'ceiling': 'balance' if _high < _most - 0.005 else 'claims',
                    'basis': 'books_net'}) for y in takers)
            else:
                open_years += years
        year = self.year or 0
        none = _year_figures(self.build({**given, **low}), year, self.carried_in)
        every = _year_figures(self.build({**given, **high}), year, self.carried_in)
        out = {'asked': None, 'free': free, 'fold': fold, 'settled': settled,
               'inferred': inferred}
        if self.carried_in is None:
            # The tool run alone: no estimate to ask; a year with a loss of
            # its own, whose row's carry-back differs.
            if not any(r['year'] == year and r['net_gain'] < -0.005
                       for r in self.build(given)['rows']):
                return out
        elif none['loss_left'] <= 0.005 and every['loss_left'] <= 0.005:
            return out
        if not open_years or abs(none['carried_back'] - every['carried_back']) <= 0.005:
            return out
        # The years to ask about: each one that carries back in some reading
        # (with those before it at nothing, the next one can).
        could: List[int] = []
        while True:
            rows = self.build({**given, **dict.fromkeys(could, 0.0),
                               **{y: v for y, v in high.items() if y not in open_years}}, {})
            more = [y for y in open_years if y not in could
                    and (high[y] > 0.005 or self.back(rows, y) > 0.005)]
            if not more:
                break
            could += more
        out['asked'] = {
            'years': sorted(could), 'if_none': none, 'if_all': every,
            'books': {str(y): M.shown(high[y], 2) for y in sorted(could)},
            'least': {str(y): M.shown(low[y], 2) for y in sorted(could)},
            'answered': {str(y): M.shown(v, 2) for y, v in sorted(given.items())}}
        return out


def _canada_ledger(args: argparse.Namespace, nets: Dict[int, Dict[str, float]],
                   claimed: Dict[int, float], carried: Dict[int, Any],
                   locked_back: Dict[int, float], locked_applied: Dict[int, float],
                   own_year: Optional[int], locked_opening: Dict[int, float],
                   typed_opening: Optional[Dict[int, float]] = None) -> Dict[str, Any]:
    """carryover: Canada's ledger, with what it can't tell (`_Readings`) and
    the person's answers. `typed_opening`: {lock year: the opening its lock
    took from [estimate] other_losses}; --carried-in / --carried-in-typed: the
    balance the estimate takes into the project year, and whether the person
    gave it. [carryover] carried_back answers a question per year: used where
    the ledger asks, noted as no longer needed where a lock, the books or a
    balance given now say the same or nothing rests on it, refused where it
    contradicts them or is more than the year could carry back (the owner's
    ruling of 2026-11-21; backlog TJ-1574, TJ-4880)."""
    year = args.project_year
    stated = _stated_backs(args)
    carried_in = getattr(args, 'carried_in', None)
    typed = dict(typed_opening or {})
    if year is not None and carried_in is not None and getattr(args, 'carried_in_typed', False):
        typed[year] = carried_in

    def build(given: Dict[int, float], opening: Any = locked_opening) -> Dict[str, Any]:
        return build_canada_ledger(
            nets, claimed, carried, locked_back, locked_applied, own_year=own_year,
            through_year=year, locked_opening=opening, stated=given)

    readings = _Readings(build, year, carried, locked_opening, typed, carried_in)
    first = readings.question({})
    asked = first['asked']['years'] if first['asked'] else []
    use = {y: v for y, v in stated.items() if y in asked}
    known = build(first['fold'])
    for y, v in sorted(stated.items()):
        if y in use:
            continue
        if year is None or y >= year:
            _refuse_stated(
                f"[carryover] carried_back names {y}, not a year before the "
                f"project year: its carry-back is what `tjs carryover` "
                f"computes; remove that entry from taxjson.toml")
        moot = y in first['free'] and y not in first['settled']
        shows = (float(locked_back.get(y) or 0.0) if y in carried
                 else first['settled'][y][0] if y in first['settled']
                 else _Readings.back(known, y))
        by = (f"{y}'s close-year lock records" if y in carried
              else (f"the {M.shown(first['settled'][y][1], 2):,.2f} carried into "
                    f"{first['settled'][y][2]} ([estimate] other_losses) says")
              if y in first['settled'] else "the books and the lock after it show")
        if not moot and abs(v - shows) > 0.005:
            _refuse_stated(
                f"[carryover] carried_back says {y} carried back "
                f"{M.shown(v, 2):,.2f}, but {by} {M.shown(shows, 2):,.2f}: the "
                f"line answers `tjs carryover` only where it can't tell; remove "
                f"that entry from taxjson.toml")
        from taxjson.lib.out import note as _note
        _note(f"[carryover] carried_back's entry for {y} is no longer needed: "
              + (f"nothing of {year} rests on it" if moot else f"{by} the same")
              + "; it can be removed from taxjson.toml")
    final = readings.question(use)
    ledger = build({**use, **final['fold']})
    for r in ledger['rows']:
        y = r['year']
        if y in final['inferred']:
            r['carried_back_inferred'] = final['inferred'][y]
        if y not in use:
            continue
        if use[y] - r['carried_back'] > 0.005:
            _refuse_stated(
                f"[carryover] carried_back says {y} carried back "
                f"{M.shown(use[y], 2):,.2f}, more than the "
                f"{M.shown(r['carried_back'], 2):,.2f} of [carryover] claimed "
                f"amounts a {y} loss could meet (those under the 3 "
                f"years before it, not yet met): correct that entry in "
                f"taxjson.toml")
        r['carried_back_stated'] = r['carried_back']
    clash = readings.clash(ledger, use)
    if clash and not readings.clash(known, {y: 0.0 for y in use}):
        _into, _fact, _least, _most = clash
        _refuse_stated(
            "[carryover] carried_back ("
            + ", ".join(f"{y} = {M.shown(v, 2):.2f}" for y, v in sorted(use.items()))
            + f") leaves {M.shown(_most, 2):,.2f} carried into {_into}"
            + (f" ({M.shown(_least, 2):,.2f} if the gains between took all they "
               f"could)" if _most - _least > 0.005 else "")
            + f", but [estimate] other_losses says {M.shown(_fact, 2):,.2f}"
            + ("" if _fact > _most else ", less than that")
            + ": correct whichever is wrong in taxjson.toml")
    held = readings.before_books(ledger, use)
    if held:
        ledger['carried_in_before_books'] = held
    if final['asked']:
        ledger['carry_back_cant_tell'] = final['asked']
    return ledger


def _carryover_rows(args: argparse.Namespace, ledger: Dict[str, Any],
                    seeded: Dict[int, Dict[str, Any]], country: Any, slip_by: Dict[int, float],
                    engine_net: Dict[int, float], filed_net: Any,
                    lock_info: Dict[int, Dict[str, Any]], filed: Dict[int, float]) -> None:
    """carryover: the rows marked against the project year (those before
    it rebuilt, those after it partial, the balance stopping there) and
    against the filed figures (a row its lock seeds, or one that
    differs from its lock)."""
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
                              final_carryforward=M.shown(st + lt, 2))
    for r in ledger['rows']:
        y = r['year']
        if slip_by.get(y):
            r['slip_gains'] = M.shown(slip_by[y], 2)
        if y in engine_net:
            # NET GAIN(LOSS) is the RETURN row; the rebuild's own total
            # beside it (TJ-0208).
            r['net_source'] = ('filed_return' if filed_net is not None
                               and y == args.project_year else 'return_row')
            r['engine_net_gain'] = M.shown(engine_net[y], 2)
        if y in seeded:
            sd = seeded[y]
            r.update(from_lock=True, filed_lock=sd['path'],
                     filed_source=sd['source'],
                     filed_realized=M.shown(sd['figure'], 2),
                     rebuilt_net_gain=M.shown(sd['rebuilt'], 2))
            if sd.get('opening_stale'):
                r['opening_stale'] = dict(sd['opening_stale'])
            if abs(sd['rebuilt'] - sd['figure']) > 0.01:
                from taxjson.lib.out import note as _note
                _note(f"{y}: the ledger uses the filed figure "
                      f"{M.shown(sd['figure'], 2):,.2f} from {sd['path']}",
                      details=_dl([f"{LOCK_SOURCE_TEXT[sd['source']]}; this "
                               f"project's books rebuild "
                               f"{M.shown(sd['rebuilt'], 2):,.2f} for {y}"
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
                      f"{M.shown(sd['filed_totals_gain'], 2):,.2f} filed with another "
                      f"tool, which has no short/long-term split",
                      details=_dl([f"The ledger uses the lock's own Part "
                                   f"I/II split ({M.shown(sd['figure'], 2):,.2f}) — "
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
        r['filed_realized'] = M.shown(fig, 2)
        r['filed_source'] = src
        # The return's lines are rounded per row; the ledger is not.
        tol = 0.01 if src == 'realized' else max(
            0.01, 0.005 * int(r.get('dispositions') or 0))
        if abs(net - fig) > tol:
            r['differs_from_filed'] = True
            from taxjson.lib.out import warn as _warn
            _warn(f"{y}: the ledger's net {M.shown(net, 2):,.2f} differs from the "
                  f"filed lock's {M.shown(fig, 2):,.2f}",
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


def _carryover_manual(manual: List[Dict[str, Any]], req: GainsRequest,
                      ledger: Dict[str, Any]) -> None:
    """carryover: the unknown-cost dispositions the ledger leaves out,
    listed, their years marked incomplete, and a warning."""
    if manual:
        # Machine-readable too (TJ-0323): each excluded disposition
        # (`excluded_unknown_cost`), and the years whose nets lack them
        # (`incomplete_years`; each such row `incomplete: true`).
        _mk = ('date_settle' if req.effective_tax_date() == 'settle'
               else 'date')
        excl: List[Dict[str, Any]] = []
        for t in manual:
            d = str(t.get(_mk) or t.get('date') or '')
            g = GainRow(t)
            excl.append({
                'year': int(d[:4]) if d[:4].isdigit() else None,
                'date': t.get('date'), 'symbol': t.get('symbol'),
                'account': t.get('account'),
                'qty': abs(float(g.quantity or 0.0)),
                'proceeds': M.shown(float(g.proceeds or 0.0), 2)})
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


def _carryover_check_args(args: argparse.Namespace) -> Tuple[bool, Union[int, Any]]:
    """carryover: the arguments refused before any book is read (the
    other country's flags, a currency that is not the return's, no
    books, a missing file, claims given twice or a claimed file that
    does not exist); the timing note."""
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
        return True, 2

    if not args.files and not args.crypto:
        # A crypto-only project gives its books as --crypto only: fed
        # positionally they lost the US no-wash pass (US-WASH-13; audit
        # A2-0146, A2-0411, A2-0412).
        emit_line("taxjson-carryover: error: give at least one base book "
                  "(FILE or --crypto FILE)", file=sys.stderr)
        return True, 2
    for p in args.files + args.crypto + args.sheltered:
        if not p.exists():
            _out.put(f"taxjson-carryover: no such file: {p}", file=sys.stderr)
            return True, 2
    if args.claimed is not None and args.claimed_year:
        _out.put("taxjson-carryover: give the claimed amounts once — "
                 "--claimed FILE or --claimed-year YEAR=AMOUNT, not both",
                 file=sys.stderr)
        return True, 2
    if args.claimed is not None and not args.claimed.exists():
        _out.put(f"taxjson-carryover: no such file: {args.claimed}",
                 file=sys.stderr)
        return True, 2
    return False, None


def _carryover_locks(args: argparse.Namespace, crypto_loaded: List[Any],
                     transactions: List[Any]) -> Tuple[
        bool, Union[int, Any], Any, Any, Any, Any, Any, Any]:
    """carryover: the filed-year locks read (each year's figure, record
    and balance; a malformed YEAR=PATH, an unreadable lock or one for
    another year refused), and Canada's crypto books folded in."""
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
            _out.put(f"taxjson-carryover: --filed-lock expects YEAR=PATH, got "
                     f"{item!r}", file=sys.stderr)
            return True, 2, None, None, None, None, None, None
        from taxjson.lib.filed_locks import file_year as _file_year
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
                return True, 1, None, None, None, None, None, None
            _out.put(f"taxjson-carryover: --filed-lock {item}: the lock is "
                     f"for {_file_year(Path(lp))}, not {y} — give it as "
                     f"{_file_year(Path(lp))}={lp}", file=sys.stderr)
            return True, 2, None, None, None, None, None, None
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
                return True, 1, None, None, None, None, None, None
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
            _out.put(f"taxjson-carryover: --filed-lock {item}: the lock "
                     f"records year {info['year']}, not {y} — give it as "
                     f"{info['year']}={lp}", file=sys.stderr)
            return True, 2, None, None, None, None, None, None
        info['path'] = lp
        lock_info[y] = info
    # Every lock the wrapper names, readable or not: the pipeline's
    # --locked-year flags (filed_locks.locked_year_flags) do the same.
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
    return False, None, country, lock_info, locked_years, locked_records, crypto_txs, transactions


def _carryover_gains(args: argparse.Namespace, locked_years: Tuple[int, ...],
                     locked_records: Tuple[str, ...], country: Any, transactions: List[Any],
                     sheltered: List[Any],
                     crypto_txs: List[Any]) -> Tuple[bool, Union[int, Any], Any, Any, Any, Any]:
    """carryover: the engine run on the filing pipeline's basis, the
    yearly nets and the unknown-cost dispositions, with the US crypto
    books' no-wash pass added in."""
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
        return True, 1, None, None, None, None
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
            return True, 1, None, None, None, None
        for yr, vals in yearly_nets(
                res_c, req_c.effective_tax_date()).items():
            dst = nets.setdefault(yr, {})
            for k, v in vals.items():
                dst[k] = dst.get(k, 0.0) + v
        manual += list(res_c.get('manual_reporting_required') or [])
    return False, None, req, results, nets, manual


if __name__ == "__main__":
    sys.exit(main())
