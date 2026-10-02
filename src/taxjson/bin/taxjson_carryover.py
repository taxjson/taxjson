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
loss amount actually applied against that YEAR's return — Canada: the
100% capital loss (line 25300 divided by the inclusion rate, x2 at 50%);
US: the Schedule D line 21 deduction against ordinary income, as far as
taxable income absorbed it (Capital Loss Carryover Worksheet line 4 —
less than line 21 when taxable income is negative). Claims fold into
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
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from taxjson.lib.cli_diag import guard_main, read_text_utf8, tax_year
from taxjson.lib.country import add_country_argument, refuse_foreign_flags
from taxjson.lib.core import AmbiguousTransferDateError as _AmbiguousXferErr
from taxjson.lib.pipeline import (GainsRequest, TransferValidationError,
                                  run_gains)
from taxjson.lib.report_model import fmt_money
from taxjson.bin.taxjson_convert_currency import norm_currency

_INCOME_ACTIONS = ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST', 'FEE')
US_ORDINARY_OFFSET = 3000.0


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
# `taxjson init` / [settings] year accept. A typo ('2205', '0') became a
# phantom ledger row that silently consumed the claim (S027-23).
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
            print(f"warning: {path.name}:{lineno}: {why}, got "
                  f"{stripped!r} — line ignored", file=sys.stderr)
            if ignored is not None:
                ignored.append(f"{path.name}:{lineno}: {stripped}")
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
    # balance (an upper bound on its Schedule 3 rows).
    slack = 0.0
    for y in years:
        net = nets.get(y, _ZERO)['net']
        loss = max(0.0, -net)
        if loss:
            balance += loss
            slack += 0.005 * max(1, int(nets.get(y, _ZERO)['dispositions']
                                        or 0))
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
            if 0.0 < pending_by_year[_cy] <= slack:
                # The claim is the filed (per-row-rounded) loss and the
                # unrounded ledger came out a few cents short of it.
                pending_by_year[_cy] = 0.0
        if applied > 0.0 and 0.0 < balance <= slack:
            # ...or a few cents over it: the filed claim used it all.
            balance = 0.0
        if balance <= 0.005:
            slack = 0.0
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
    _unmatched = sum(pending_by_year.values()) + sum(expired.values())
    if _unmatched > 0.005:
        out['unmatched_claims'] = round(_unmatched, 2)
    _exp = {str(k): round(v, 2) for k, v in sorted(expired.items())
            if v > 0.005}
    if _exp:
        out['expired_claims'] = _exp
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


# ------------------------------------------------------- filed-year locks

class LockUnreadable(ValueError):
    """A filed-year lock that could not be read or holds no usable
    figure."""


# The gain lines a return carries (close-year's form_lines keys).
_GAIN_LINES = {'canada': ('13200', '15300', '15301'),
               'usa': ('I_gain', 'II_gain')}

LOCK_SOURCE_TEXT = {
    'filed_totals': "the total filed with another tool: filed_totals",
    'form_lines': "the filed gain lines: form_lines",
    'realized': "the lock's realized total",
}


def _finite(v: Any, what: str) -> float:
    import math
    x = float(v)
    if not math.isfinite(x):
        raise ValueError(f"{what} is not a finite number ({v!r})")
    return x


def lock_figure(path: Path, country: str) -> Dict[str, Any]:
    """What a close-year lock says was FILED for its year: {figure,
    source, st, lt, dispositions}. The figure is, by preference, the
    gain the return reported with another tool (filed_totals.gain;
    Canada — it has no term split), else the sum of the return's gain
    lines (form_lines: Schedule 3 13200/15300/15301, Form 8949 Part I/II
    — per-row rounded, what the return carries; audit A2-1132), else
    the lock's realized total. A lock that cannot be read, or whose
    numbers are missing or not finite (NaN, Infinity), raises
    LockUnreadable — it never silently matches (A2-0336, A2-1131)."""
    try:
        doc = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeDecodeError) as e:
        raise LockUnreadable(str(e)) from None
    if not isinstance(doc, dict):
        raise LockUnreadable("not a close-year lock (not a JSON object)")
    try:
        totals = doc.get('totals')
        if not isinstance(totals, dict):
            raise ValueError("no totals table")
        realized = _finite(totals['realized'], 'totals.realized')
        accts = doc.get('accounts')
        lines: Optional[Dict[str, float]] = None
        if isinstance(accts, dict) and accts and all(
                isinstance(a, dict) and isinstance(a.get('form_lines'), dict)
                for a in accts.values()):
            lines = {}
            for a in accts.values():
                for k, v in a['form_lines'].items():
                    lines[str(k)] = lines.get(str(k), 0.0) + _finite(
                        v, f"form_lines {k}")
        ft = doc.get('filed_totals')
        ft_gain = (_finite(ft['gain'], 'filed_totals.gain')
                   if isinstance(ft, dict) and ft.get('gain') is not None
                   else None)
        out: Dict[str, Any] = {
            'dispositions': int(totals.get('dispositions') or 0),
            'realized': realized, 'filed_totals_gain': ft_gain}
        if country == 'usa':
            if lines is not None:
                st = lines.get('I_gain', 0.0)
                lt = lines.get('II_gain', 0.0)
                source = 'form_lines'
            else:
                st = _finite(totals.get('st_gain') or 0.0, 'totals.st_gain')
                lt = _finite(totals.get('lt_gain') or 0.0, 'totals.lt_gain')
                # A gain with no term counts short-term (US-RPT-07).
                st += realized - (st + lt)
                source = 'realized'
            out.update(figure=st + lt, st=st, lt=lt, source=source)
        else:
            if ft_gain is not None:
                fig, source = ft_gain, 'filed_totals'
            elif lines is not None:
                fig = sum(lines.get(k, 0.0) for k in _GAIN_LINES['canada'])
                source = 'form_lines'
            else:
                fig, source = realized, 'realized'
            out.update(figure=fig, st=0.0, lt=0.0, source=source)
    except (KeyError, TypeError, ValueError) as e:
        raise LockUnreadable(f"{type(e).__name__}: {e}") from None
    return out

# S028-03: what NET GAIN(LOSS) leaves out. A net capital loss nets
# every taxable capital gain of the year (ITA 111(8) "net capital
# loss"), including slip gains and the s.39(1.1) FX gain.
SCOPE_NOTE = {
    'canada': ("NET GAIN(LOSS) counts the dispositions in these books "
               "(plus the T5 box 18 dividends capital_gains_dividends.map "
               "names, when `taxjson carryover` passes them). Other "
               "capital gains reported on slips (T3 box 21, T5 "
               "box 18 -> lines 17400/17600) and the ITA s.39(1.1) FX "
               "gain or loss on foreign cash (line 15300, `taxjson "
               "fx-cash`) are NOT included; in a loss year they change "
               "the net capital loss, so the carryforward and the "
               "carryback caps shown are off by that amount."),
    'usa': ("Net ST/LT count the dispositions in these books only. "
            "Capital gain distributions (1099-DIV box 2a -> Schedule D "
            "line 13) are NOT included; in a loss year they change the "
            "carryover shown."),
}


def render(ledger: Dict[str, Any], cur: str, first_tx_year: Optional[int],
           claimed_used: bool) -> str:
    lines: List[str] = []
    country = ledger['country']
    lines.append(f"CAPITAL-LOSS CARRYOVER LEDGER — {country} "
                 f"(amounts in {cur}, 100% gains/losses, allowed i.e. "
                 f"post-{'wash-sale' if country == 'usa' else 'superficial-loss'})")
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
    _final_year = ledger.get('final_year', rows[-1]['year'])
    if country == 'canada':
        lines.append(f"  Net-capital-loss carryforward after "
                     f"{_final_year}: {_money(ledger['final_carryforward'])}")
        if ledger.get('unmatched_claims'):
            lines.append(f"  warning: {_money(ledger['unmatched_claims'])} "
                         f"of --claimed amounts exceed the losses this "
                         f"history supports — check the claimed file.")
        for _y, _amt in (ledger.get('expired_claims') or {}).items():
            lines.append(f"  warning: {_money(_amt)} claimed for {_y} "
                         f"was never met by a loss of {_y} or earlier "
                         f"in these books, and a later loss can reach "
                         f"back only 3 years (ITA 111(1)(b)) — it came "
                         f"from losses before this history, so it does "
                         f"not reduce the carryforward shown.")
    else:
        lines.append(f"  Carryover after {_final_year}: "
                     f"ST {_money(ledger['final_st_carryover'])} + "
                     f"LT {_money(ledger['final_lt_carryover'])}")
    lines.append("")
    lines.append("Notes:")
    if country == 'usa':
        lines.append("  - Amounts are 100% capital gains/losses, carried "
                     "over short- and long-term on the Schedule D "
                     "Capital Loss Carryover Worksheet.")
    else:
        lines.append("  - Amounts are 100% capital gains/losses. Apply "
                     "the 50% inclusion rate on Schedule 3 / form T1A; "
                     "the official 'net capital loss' balance CRA tracks "
                     "is the inclusion-rate-adjusted figure.")
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
    if ledger.get('scope_note'):
        lines.append("  - " + ledger['scope_note'])
    if ledger.get('claimed_ignored'):
        lines.append(f"  - warning: {len(ledger['claimed_ignored'])} "
                     f"claimed line(s) could not be read and are NOT "
                     f"applied, so the carryforward shown is overstated "
                     f"by them: "
                     + "; ".join(ledger['claimed_ignored'][:3])
                     + (" ..." if len(ledger['claimed_ignored']) > 3
                        else ""))
    after = [r['year'] for r in rows if r.get('after_project_year')]
    if after:
        lines.append(f"  - warning: the rows after {ledger.get('project_year')}"
                     f" ({', '.join(str(y) for y in after)}) are partial —"
                     f" only the trades of that year this project's inputs"
                     f" happen to hold. They offer no T1A carry-back and the"
                     f" carryforward above stops at "
                     f"{ledger.get('project_year')}; that year's own "
                     f"project computes its ledger.")
    seeded = [r for r in rows if r.get('from_lock')]
    if seeded:
        lines.append("  - " + "; ".join(
            f"{r['year']} is the filed figure from {r['filed_lock']} "
            f"({LOCK_SOURCE_TEXT.get(r['filed_source'], r['filed_source'])})"
            for r in seeded)
            + " — not rebuilt from this project's books.")
    slips = [r for r in rows if r.get('slip_gains')]
    if slips:
        lines.append("  - Includes the capital-gains dividends named in "
                     "capital_gains_dividends.map (T5 box 18): "
                     + ", ".join(f"{r['year']} {_money(r['slip_gains'])}"
                                 for r in slips) + ".")
    prior = [r['year'] for r in rows if r.get('prior_year')]
    if prior:
        py = ledger.get('project_year')
        lines.append(f"  - warning: the rows before {py} "
                     f"({', '.join(str(y) for y in prior)}) are rebuilt "
                     f"from this project's books — opening *_start.tt "
                     f"lots plus whatever prior-year exports are in "
                     f"inputs/ — and may be partial (a missing export "
                     f"shows a smaller gain, or a loss that never "
                     f"happened). Verify each against the filed return "
                     f"({'Schedule D' if country == 'usa' else 'Schedule 3'}"
                     f") before trusting a "
                     f"carryforward or carryback from it.")
    if first_tx_year is not None and rows and rows[0]['year'] <= first_tx_year:
        lines.append(f"  - warning: this history starts in {first_tx_year} — "
                     f"if you traded before then, earlier gains/losses (and "
                     f"any pre-{first_tx_year} carryforward) are NOT "
                     f"reflected. Reconcile the opening balance against "
                     f"your {'IRS' if country == 'usa' else 'CRA'} "
                     f"records.")
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
                        help="phantoms.json for truncated-history openings")
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
                             "coming in.")
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
                             "is what CRA's balance is built on.")
    parser.add_argument("--filed-lock", action="append", default=[],
                        metavar="YEAR=PATH",
                        help="A filed year's close-year lock (the wrapper "
                             "passes filed/<year>.json and the "
                             "prior_year_record lock). A year before "
                             "--project-year takes the lock's FILED "
                             "figure instead of the rebuilt one; a later "
                             "year is compared with it. An unreadable "
                             "lock is named.")
    parser.add_argument("--slip-gains", action="append", default=[],
                        metavar="YEAR=AMOUNT",
                        help="Canada: capital gains from slips (T5 box 18 "
                             "dividends in capital_gains_dividends.map) "
                             "that year — added to the year's net (the "
                             "wrapper passes them).")
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
    # `taxjson carryover` wrapper passes the project's settings): without
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
    parser.add_argument("--option-buyback-wash", action="store_true",
                        help="Canada, grant timing: buy-back loss of a "
                             "written option can be superficial.")
    args = parser.parse_args(argv)
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
        print(f"taxjson-carryover: error: --base-currency "
              f"{args.base_currency}: "
              + _bp.replace("[settings] base_currency is", "the books are")
                   .replace('set base_currency', 'pass books converted to')
              , file=sys.stderr)
        return 2

    if not args.files and not args.crypto:
        # A crypto-only project gives its books as --crypto only: fed
        # positionally they lost the US no-wash pass (US-WASH-13; audit
        # A2-0146, A2-0411, A2-0412).
        print("taxjson-carryover: error: give at least one base book "
              "(FILE or --crypto FILE)", file=sys.stderr)
        return 2
    for p in args.files + args.crypto + args.sheltered:
        if not p.exists():
            print(f"taxjson-carryover: no such file: {p}", file=sys.stderr)
            return 2
    if args.claimed is not None and not args.claimed.exists():
        print(f"taxjson-carryover: no such file: {args.claimed}",
              file=sys.stderr)
        return 2

    from taxjson.lib.json_input import load_transactions_or_exit as _ltx
    # The ledger is labelled in --base-currency and its balance feeds
    # T1A / line 25300: a native USD book (a raw broker file) printed a
    # 5,000 USD loss as a 5,000.00 CAD carryforward (S028-02).
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
              f"`taxjson carryover` in the project)", file=sys.stderr)
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
                f"books (work/<account>_base.json, or `taxjson carryover` "
                f"in the project)")
        bad = sorted({str(t.currency).strip().upper() for t in txs
                      if str(t.currency or "").strip()
                      and str(t.currency).strip().upper() != base_cur})
        if bad:
            raise ValueError(
                f"{p} holds rows in {', '.join(bad)} but --base-currency "
                f"is {base_cur} — pass the base-currency books "
                f"(work/<account>_base.json, or `taxjson carryover` in "
                f"the project)")
        return txs

    transactions = []
    try:
        for p in args.files:
            transactions.extend(_load_base(p))
        crypto_loaded = [_load_base(cp) for cp in args.crypto]
    except ValueError as exc:
        print(f"taxjson-carryover: error: {exc}", file=sys.stderr)
        return 2
    sheltered = []
    for p in args.sheltered:
        sheltered.extend(_ltx("taxjson-carryover", p))

    country = args.country
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
                # The project's income dating, as every filing run uses
                # it: a listed corporation's ROC on its pay date
                # (CA-INC-DATE-ROC; audit A2-0123, A2-0339, A2-0341).
                corporate_distributions=tuple(
                    args.corporate_distribution or ()),
                ric_january_dividends=tuple(
                    args.ric_january_dividend or ()))
    req = GainsRequest(country=country, year=None, taxable=True,
                       tax_date=args.tax_date,
                       incomplete_history=args.incomplete_history,
                       phantom_hint=False,
                       per_account_basis=(country == 'usa'), **_opt)
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
                             per_account_basis=True, **_opt)
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
        filed: Dict[int, float] = dict(
            _year_amount(i, "--filed") for i in args.filed)
    except ValueError as exc:
        print(f"taxjson-carryover: {exc}", file=sys.stderr)
        return 2
    # --slip-gains in a US ledger is refused up front with the other
    # one-country flags (FLAG_COUNTRY; refuse_foreign_flags).
    _zero = {'net': 0.0, 'st': 0.0, 'lt': 0.0, 'dispositions': 0}
    slip_by: Dict[int, float] = {}
    for y, v in slip:
        slip_by[y] = slip_by.get(y, 0.0) + v
    # Filed-year locks: a year before the project year is rebuilt from
    # this project's books and may be partial (a later project holds
    # last year only as a start .tt), so the ledger takes what the lock
    # says was FILED — the return CRA's balance is built on (A2-0121,
    # A2-0338, A2-0666). The project year (and later) is compared.
    seeded: Dict[int, Dict[str, Any]] = {}
    lock_info: Dict[int, Dict[str, Any]] = {}
    for item in args.filed_lock:
        try:
            y_s, lp = item.split("=", 1)
            y = int(y_s)
        except ValueError:
            print(f"taxjson-carryover: --filed-lock expects YEAR=PATH, got "
                  f"{item!r}", file=sys.stderr)
            return 2
        try:
            info = lock_figure(Path(lp), country)
        except LockUnreadable as exc:
            print(f"warning: {y}: the filed-year lock {lp} could not be "
                  f"read ({exc}) — the {y} row is not checked against the "
                  f"filed return. Fix or restore the lock (it is the "
                  f"record of that return).", file=sys.stderr)
            continue
        info['path'] = lp
        lock_info[y] = info
        if args.project_year is not None and y < args.project_year:
            rebuilt = dict(nets.get(y, _zero))
            nets[y] = {'net': info['figure'], 'st': info['st'],
                       'lt': info['lt'],
                       'dispositions': max(int(rebuilt.get('dispositions')
                                               or 0),
                                           int(info['dispositions']))}
            seeded[y] = {'rebuilt': float(rebuilt.get('net') or 0.0),
                         'had_rows': bool(rebuilt.get('dispositions')),
                         **info}
    for y, v in slip_by.items():
        rec = nets.setdefault(y, dict(_zero))
        rec['net'] = rec.get('net', 0.0) + v

    if country == 'canada':
        ledger = build_canada_ledger(nets, claimed)
    else:
        ledger = build_usa_ledger(nets, claimed)

    tx_years = [int(t.date[:4]) for t in transactions
                if t.date[:4].isdigit()]
    first_tx_year = min(tx_years) if tx_years else None
    ledger['first_transaction_year'] = first_tx_year
    ledger['scope_note'] = SCOPE_NOTE[country]
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
        if y in seeded:
            sd = seeded[y]
            r.update(from_lock=True, filed_lock=sd['path'],
                     filed_source=sd['source'],
                     filed_realized=round(sd['figure'], 2),
                     rebuilt_net_gain=round(sd['rebuilt'], 2))
            if abs(sd['rebuilt'] - sd['figure']) > 0.01:
                print(f"note: {y}: the ledger uses the filed figure "
                      f"{sd['figure']:,.2f} from {sd['path']} "
                      f"({LOCK_SOURCE_TEXT[sd['source']]}); this "
                      f"project's books rebuild {sd['rebuilt']:,.2f} for "
                      f"{y}" + ("" if sd['had_rows'] else
                                " (no disposition of that year here)")
                      + " — a year before the project year is rebuilt "
                        "from opening lots and whatever prior exports "
                        "are in inputs/, so the lock is what CRA's loss "
                        "balance is built on.", file=sys.stderr)
            if (country == 'usa' and sd.get('filed_totals_gain') is not None
                    and abs(sd['filed_totals_gain'] - sd['figure']) > 0.01):
                print(f"warning: {y}: {sd['path']} records "
                      f"{sd['filed_totals_gain']:,.2f} filed with another "
                      f"tool, which has no short/long-term split; the "
                      f"ledger uses the lock's own Part I/II split "
                      f"({sd['figure']:,.2f}) — enter the filed "
                      f"carryover with --claimed if they differ.",
                      file=sys.stderr)
            continue
        if y in lock_info:
            info = lock_info[y]
            fig, src, where = info['figure'], info['source'], info['path']
        elif y in filed:
            fig, src, where = filed[y], 'realized', f"filed/{y}.json"
        else:
            continue
        net = float(r.get('net_gain') or 0.0) - slip_by.get(y, 0.0)
        r['filed_realized'] = round(fig, 2)
        r['filed_source'] = src
        # The return's lines are rounded per row; the ledger is not.
        tol = 0.01 if src == 'realized' else max(
            0.01, 0.005 * int(r.get('dispositions') or 0))
        if abs(net - fig) > tol:
            r['differs_from_filed'] = True
            print(f"warning: {y}: the ledger's net "
                  f"{net:,.2f} differs from the filed lock's "
                  f"{fig:,.2f} — {LOCK_SOURCE_TEXT[src]}"
                  + (", as filed with another tool"
                     if src == 'filed_totals' else "")
                  + f" ({where}). This ledger recomputes "
                  f"every year with this project's settings (option "
                  f"premium timing and option_grant_timing_since, "
                  f"tax_date); the lock is what was filed and what "
                  f"CRA's loss balance is built on — check the settings "
                  f"or use `taxjson check-filed`.", file=sys.stderr)
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
