"""Canadian tax instalments: schedule, offset interest, s.163.1 penalty.

Who this is for: an individual whose NET TAX OWING (tax minus amounts
withheld at source) exceeds $3,000 in the current year AND in either of
the two preceding years must pay by instalment — the classic case being
an investor with no employment withholding, which is exactly the profile
`taxjson estimate` serves.

Four due dates: March 15, June 15, September 15, December 15 (rolled to
the next business day when they land on a weekend).

Three bases, all of which CRA accepts (ITA 156):
  current_year  — 1/4 of THIS year's estimated net tax owing on each
                  date. Lowest total outlay, but interest applies if
                  the estimate proves low.
  prior_year    — 1/4 of last year's net tax owing on each date.
  cra_reminder  — the no-calculation option CRA's reminders use: the
                  first two payments are 1/4 of the SECOND preceding
                  year's net tax owing each; the last two split the
                  remainder of the prior year's. Following the
                  reminder amounts on time is always interest-free,
                  whatever the year turns out to be.

Interest (ITA 161(2)) follows CRA's published A - B method: A is
interest on each required instalment from its due date to the
balance-due date, B is interest on each payment from the later of the
payment date and January 1 to the same date, both compounded daily;
the net (C = A - B) is charged. Credit interest can only reduce a
charge, never produce a refund.

CRA resets the prescribed rate QUARTERLY and applies the rate in
effect on each individual day — a balance spanning a quarter boundary
is charged at the old rate through the end of that quarter and the new
rate from the start of the next, with accrued interest compounding
across the seam. The rate is a config input here, either a single
number or a dated schedule; with neither, the built-in table of CRA's
PUBLISHED quarterly rates (PUBLISHED_RATES) applies, the last one
carried forward past the newest published quarter. The daily walk
looks up the rate in force for each day, and the report always names
the rate(s) it used and where they came from.

Credit (offset) interest on a payment runs from the LATER of the
payment date and January 1 of the tax year. CRA charges the net only
when it exceeds $25, and only when it sent an instalment reminder for
the year showing an amount to pay — the report says so.
https://www.canada.ca/en/revenue-agency/services/payments/payments-cra/individual-payments/income-tax-instalments/interest-penalty-charges.html

Penalty (ITA 163.1) applies only when instalment interest exceeds
$1,000: half of the amount by which the interest exceeds the greater of
$1,000 and 25% of the interest that would have accrued had NO instalment
been paid.

Everything here is an ESTIMATE for planning. CRA computes the real
figures from assessed returns; a payment posted a day differently, or a
prescribed-rate change mid-year, moves the interest.
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

DUE_MONTHS = (3, 6, 9, 12)
DUE_DAY = 15
# The balance-due date (ITA s.248(1) "balance-due day": April 30 of the
# next year for an individual): instalment interest runs to it, and a
# payment dated after it is not an instalment of the year.
BALANCE_DUE_MONTH_DAY = (4, 30)


def balance_due_date(year: int) -> date:
    """April 30 of the year after `year`."""
    return date(int(year) + 1, *BALANCE_DUE_MONTH_DAY)


def balance_due_label() -> str:
    """'April 30' (from the constant)."""
    import calendar
    m, d = BALANCE_DUE_MONTH_DAY
    return f"{calendar.month_name[m]} {d}"
THRESHOLD = 3000.0          # net tax owing above which instalments apply
PENALTY_FLOOR = 1000.0      # interest below this can never be penalized
PENALTY_SHARE = 0.50        # of the excess over the floor
PENALTY_ALT_FRACTION = 0.25  # of the no-payment interest
BASES = ("current_year", "prior_year", "cra_reminder")
INTEREST_MIN = 25.0         # CRA charges instalment interest only above

# CRA's published rate on overdue taxes (instalment interest; the same
# rate prices the credit side), by calendar quarter. Verified
# 2026-09-27 against each quarter's page under
# https://www.canada.ca/en/revenue-agency/services/tax/prescribed-interest-rates.html
# (e.g. .../prescribed-interest-rates/2026-q4.html: "overdue taxes ...
# will be 7%"). Add each quarter as CRA announces it. 2023: Q1 8%,
# Q2-Q4 9% (.../prescribed-interest-rates/2023-q1.html .. 2023-q4.html:
# "The interest rate charged on overdue taxes ... will be 8%" / "9%");
# without them a 2023 year was charged 2024 Q1's 10% all year and the
# report called it the published rate (audit R1-220).
PUBLISHED_RATES: List[Tuple[str, float]] = [
    ("2023-01-01", 0.08), ("2023-04-01", 0.09),
    ("2023-07-01", 0.09), ("2023-10-01", 0.09),
    ("2024-01-01", 0.10), ("2024-04-01", 0.10),
    ("2024-07-01", 0.09), ("2024-10-01", 0.09),
    ("2025-01-01", 0.08), ("2025-04-01", 0.08),
    ("2025-07-01", 0.07), ("2025-10-01", 0.07),
    ("2026-01-01", 0.07), ("2026-04-01", 0.07),
    ("2026-07-01", 0.07), ("2026-10-01", 0.07),
]
PUBLISHED_THROUGH = "2026-12-31"   # last day the table covers
PUBLISHED_FROM = PUBLISHED_RATES[0][0]  # first day the table covers


def published_rates(start: date, end: date) -> List[Dict[str, Any]]:
    """The published schedule trimmed to [start, end] — the segment in
    force on `start` first, then each CHANGE of rate — as a dated
    schedule normalize_rates accepts."""
    s_iso, e_iso = start.isoformat(), end.isoformat()
    out: List[Dict[str, Any]] = []
    for eff, r in PUBLISHED_RATES:
        if eff > e_iso:
            break
        if eff <= s_iso:
            out = [{"from": s_iso, "rate": r}]
        elif not out or out[-1]["rate"] != r:
            out.append({"from": eff, "rate": r})
    if not out:                 # window before the table: earliest
        out = [{"from": s_iso, "rate": PUBLISHED_RATES[0][1]}]
    elif out[0]["from"] > s_iso:
        # The window starts before the table: the earliest rate is
        # carried BACK to the window start (build() flags it as
        # extrapolated) instead of leaving the gap to rate_on's
        # "earliest segment" fallback under the table's own date.
        out[0] = {"from": s_iso, "rate": out[0]["rate"]}
    return out


def _next_business_day(d: date) -> date:
    """Weekend rollover. Statutory holidays are NOT modeled — the four
    instalment dates rarely land on one, and CRA's own rule is 'next
    business day', so a holiday would shift by at most one more day."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def due_dates(year: int) -> List[date]:
    return [_next_business_day(date(year, m, DUE_DAY))
            for m in DUE_MONTHS]


def required_schedule(*, year: int, basis: str,
                      current_net_tax: float,
                      prior_net_tax: Optional[float] = None,
                      second_prior_net_tax: Optional[float] = None
                      ) -> List[Dict[str, Any]]:
    """[{date, amount}] — what each of the four dates calls for."""
    if basis not in BASES:
        raise ValueError(f"basis must be one of {', '.join(BASES)}, "
                         f"got {basis!r}")
    dates = due_dates(year)
    if basis == "current_year":
        amounts = [current_net_tax / 4.0] * 4
    elif basis == "prior_year":
        if prior_net_tax is None:
            raise ValueError("basis 'prior_year' needs "
                             "prior_year_net_tax")
        amounts = [prior_net_tax / 4.0] * 4
    else:
        if prior_net_tax is None or second_prior_net_tax is None:
            raise ValueError("basis 'cra_reminder' needs both "
                             "prior_year_net_tax and "
                             "second_prior_net_tax")
        first = second_prior_net_tax / 4.0
        rest = max(0.0, prior_net_tax - 2 * first) / 2.0
        amounts = [first, first, rest, rest]
    # Round the CUMULATIVE requirement and take differences, so the four
    # amounts add up to the year's figure to the cent: rounding each
    # quarter made 10,000.03 require 4 x 2,500.01 = 10,000.04 and an
    # exact payer MISSED by a cent (S034-14).
    out, run, prev = [], 0.0, 0.0
    for d, a in zip(dates, amounts):
        run += a
        cum = round(run, 2)
        out.append({"date": d.isoformat(), "amount": round(cum - prev, 2)})
        prev = cum
    return out


def normalize_rates(rate) -> List[Tuple[Optional[str], float]]:
    """Accept a scalar rate or a dated schedule; return
    [(effective_from|None, annual_rate)] sorted by date. A single
    number becomes one open-ended segment."""
    if rate is None:
        return [(None, 0.0)]
    if isinstance(rate, (int, float)):
        return [(None, float(rate))]
    out: List[Tuple[Optional[str], float]] = []
    for row in rate:
        out.append((str(row["from"]), float(row["rate"])))
    return sorted(out, key=lambda t: t[0] or "")


def rate_on(day: date,
            schedule: List[Tuple[Optional[str], float]]) -> float:
    """The annual rate in force on `day` — the latest segment whose
    effective date has arrived. Days before the first dated segment
    use the earliest rate given (the schedule is what the user knows;
    guessing a different rate for the gap would be worse)."""
    current = schedule[0][1] if schedule else 0.0
    iso = day.isoformat()
    for eff, r in schedule:
        if eff is None or eff <= iso:
            current = r
        else:
            break
    return current


def _accrue(required: List[Dict[str, Any]],
            payments: List[Dict[str, Any]],
            rates: List[Tuple[Optional[str], float]],
            end: date) -> Tuple[float, float]:
    """(A, B) — CRA's instalment-interest method, compounded daily at
    the rate in force each day (interest-penalty-charges page cited in
    the module docstring):

      A = interest on each required instalment from the day it was due
          to the balance-due date (`end`);
      B = interest on each payment from the LATER of the payment date
          and January 1 of the tax year, to the same date;
      net = A - B (charged only if over $25; credit never refunds).

    The earlier walk kept separate charge and credit totals, each
    compounding only while the running balance sat on its side, so the
    accrued charge interest FROZE the day the balance was caught up —
    unpaid interest keeps compounding until paid (ITA s.248(11)).
    That understated interest by 2.6-24% whenever instalments were
    caught up before the balance-due date (audit R1-42)."""
    if not required:
        return 0.0, 0.0
    jan1 = date(date.fromisoformat(required[0]["date"]).year, 1, 1)
    if end < jan1:
        return 0.0, 0.0
    # growth[i] = prod over days jan1+i .. end of (1 + rate/365) - 1:
    # what one dollar outstanding from that day earns by `end`.
    days = (end - jan1).days + 1
    growth = [0.0] * days
    factor = 1.0
    for i in range(days - 1, -1, -1):
        factor *= 1 + rate_on(jan1 + timedelta(days=i), rates) / 365.0
        growth[i] = factor - 1.0

    def _interest(amount: float, on: date) -> float:
        on = max(on, jan1)          # credit never before January 1
        if on > end:
            return 0.0
        return amount * growth[(on - jan1).days]

    a = sum(_interest(r["amount"], date.fromisoformat(r["date"]))
            for r in required)
    b = sum(_interest(p["amount"], date.fromisoformat(p["date"]))
            for p in payments)
    return a, b


def interest_and_penalty(*, required: List[Dict[str, Any]],
                         payments: List[Dict[str, Any]],
                         annual_rate,
                         end: date) -> Dict[str, Any]:
    """Net instalment interest and any s.163.1 penalty. `annual_rate`
    is a scalar or a dated schedule (see normalize_rates)."""
    rates = normalize_rates(annual_rate)
    charge, credit = _accrue(required, payments, rates, end)
    # Credit interest offsets a charge but never becomes a refund.
    net_computed = max(0.0, charge - credit)
    # CRA charges the difference only "if more than $25".
    net = net_computed if net_computed > INTEREST_MIN else 0.0
    no_pay_charge, _ = _accrue(required, [], rates, end)
    floor = max(PENALTY_FLOOR, PENALTY_ALT_FRACTION * no_pay_charge)
    penalty = (PENALTY_SHARE * (net - floor)) if net > floor else 0.0
    return {"charge_interest": round(charge, 2),
            "credit_interest": round(credit, 2),
            "net_interest": round(net, 2),
            "net_interest_computed": round(net_computed, 2),
            "interest_if_unpaid": round(no_pay_charge, 2),
            "penalty_floor": round(floor, 2),
            "penalty": round(max(0.0, penalty), 2),
            "as_of": end.isoformat(),
            "annual_rate": rates[0][1] if len(rates) == 1 else None,
            "rate_schedule": [{"from": eff, "rate": r}
                              for eff, r in rates]}


def candidate_schedules(*, year: int, current_net_tax: float,
                        prior_net_tax: Optional[float],
                        second_prior_net_tax: Optional[float]
                        ) -> Dict[str, List[Dict[str, Any]]]:
    """Every basis this taxpayer's figures support. CRA computes
    deficient-instalment interest on the LEAST of the current-year,
    prior-year and no-calculation methods (ITA 161(4.01)) — so
    following ANY one of them correctly is interest-free, and a tool
    that charged only against the chosen basis would over-state what
    CRA actually assesses."""
    out = {"current_year": required_schedule(
        year=year, basis="current_year",
        current_net_tax=current_net_tax)}
    if prior_net_tax is not None:
        out["prior_year"] = required_schedule(
            year=year, basis="prior_year",
            current_net_tax=current_net_tax,
            prior_net_tax=prior_net_tax)
        if second_prior_net_tax is not None:
            out["cra_reminder"] = required_schedule(
                year=year, basis="cra_reminder",
                current_net_tax=current_net_tax,
                prior_net_tax=prior_net_tax,
                second_prior_net_tax=second_prior_net_tax)
    return out


LEAST_PER_DATE = "least_per_date"


def least_cumulative_schedule(cands: Dict[str, List[Dict[str, Any]]]
                              ) -> Tuple[str, List[Dict[str, Any]]]:
    """The deemed instalment schedule of ITA 161(4.01): on EACH due date
    the individual is deemed liable for the amount computed by
    "whichever of the methods ... gives rise to the least total amount
    of such parts or instalments required to be paid by the individual
    by that day" (https://laws-lois.justice.gc.ca/eng/acts/I-3.3/
    section-161.html). That is a per-date minimum of the CUMULATIVE
    requirement, not one whole-year method: pricing each method alone
    and taking the cheapest over-stated interest whenever the cheapest
    method changes between dates (audit S034-16).

    Returns (name, schedule): `name` is the candidate the minimum
    equals on every date (current_year first, then prior_year, then
    cra_reminder), else LEAST_PER_DATE."""
    order = [n for n in BASES if n in cands]
    cums = {n: [] for n in order}
    for n in order:
        run = 0.0
        for r in cands[n]:
            run += r["amount"]
            cums[n].append(run)
    dates = [r["date"] for r in cands[order[0]]]
    least = [min(cums[n][i] for n in order) for i in range(len(dates))]
    for n in order:
        if all(abs(cums[n][i] - least[i]) < 0.005
               for i in range(len(dates))):
            return n, cands[n]
    sched, prev = [], 0.0
    for d, cum in zip(dates, least):
        sched.append({"date": d, "amount": round(cum - prev, 2)})
        prev = cum
    return LEAST_PER_DATE, sched


def build(*, year: int, basis: str, current_net_tax: float,
          payments: List[Dict[str, Any]], annual_rate=None,
          prior_net_tax: Optional[float] = None,
          second_prior_net_tax: Optional[float] = None,
          as_of: Optional[date] = None) -> Dict[str, Any]:
    """The whole instalment picture for one tax year."""
    today = as_of or date.today()
    required = required_schedule(
        year=year, basis=basis, current_net_tax=current_net_tax,
        prior_net_tax=prior_net_tax,
        second_prior_net_tax=second_prior_net_tax)
    # Interest runs to the balance-due date (April 30 following the
    # year) or to today, whichever comes first — a year in progress
    # keeps accruing.
    end = min(today, balance_due_date(year))
    # No configured rate: CRA's published quarterly rates for the
    # window (the last one carried forward past the table's end).
    rate_source = "configured"
    if annual_rate is None:
        annual_rate = published_rates(date(year, 1, 1), end)
        rate_source = "published"
    # Interest is assessed on the LEAST cumulative requirement by each
    # due date across the methods the figures support (ITA 161(4.01)),
    # not necessarily the basis being followed for payments. The same
    # deemed schedule prices the s.163.1 no-payment base (161(4.01)
    # applies "for the purposes of subsection (2) and section 163.1").
    cands = candidate_schedules(
        year=year, current_net_tax=current_net_tax,
        prior_net_tax=prior_net_tax,
        second_prior_net_tax=second_prior_net_tax)
    interest_basis, governing = least_cumulative_schedule(cands)
    ip = interest_and_penalty(required=governing, payments=payments,
                              annual_rate=annual_rate, end=end)
    ip["interest_basis"] = interest_basis
    ip["governing_schedule"] = governing
    ip["rate_source"] = rate_source
    ip["rate_extrapolated_after"] = (rate_source == "published"
                                     and end.isoformat() > PUBLISHED_THROUGH)
    ip["rate_extrapolated_before"] = (
        rate_source == "published"
        and date(year, 1, 1).isoformat() < PUBLISHED_FROM)
    if rate_source == "configured":
        # A configured dated schedule whose first segment starts after
        # January 1: rate_on carries that first rate BACK over the
        # earlier days — say so instead of printing a start date the
        # computation did not use (S034-15).
        _segs = normalize_rates(annual_rate)
        _first = _segs[0][0] if _segs else None
        if _first and _first > date(year, 1, 1).isoformat():
            ip["rate_assumed_before"] = _first
            ip["rate_extrapolated_before"] = True
    ip["rate_extrapolated"] = (ip["rate_extrapolated_after"]
                               or ip["rate_extrapolated_before"])
    ip["interest_bases_considered"] = sorted(cands)
    paid_total = sum(p["amount"] for p in payments
                     if date.fromisoformat(p["date"]) <= today)
    req_total = sum(r["amount"] for r in required)
    rows = []
    for r in required:
        rd = date.fromisoformat(r["date"])
        # Clamped to today: a FUTURE-dated payment must not make a
        # past due date look covered (the column then contradicted
        # its own TOTAL, which is as-of-today).
        matched = sum(p["amount"] for p in payments
                      if date.fromisoformat(p["date"])
                      <= min(rd, today))
        cum_req = sum(x["amount"] for x in required
                      if x["date"] <= r["date"])
        # Status as of the DUE DATE, then as of today — a date missed
        # on time but covered since is LATE (interest ran, but nothing
        # is outstanding), which "SHORT" conflated with still owing.
        if rd > today:
            status = "upcoming"
        elif matched + 1e-6 >= cum_req:
            status = "paid"
        elif paid_total + 1e-6 >= cum_req:
            status = "late"
        else:
            status = "missed"
        rows.append({**r,
                     "cumulative_required": round(cum_req, 2),
                     "cumulative_paid": round(matched, 2),
                     "status": status})
    remaining_dates = [r for r in required
                       if date.fromisoformat(r["date"]) > today]
    # "Behind" means behind on what is DUE: counting not-yet-due
    # instalments told a perfectly on-schedule mid-year taxpayer they
    # were "Behind by $X" (2026-09 audit). The year's remaining total
    # is reported separately.
    due_total = sum(r["amount"] for r in required
                    if date.fromisoformat(r["date"]) <= today)
    shortfall = max(0.0, round(due_total, 2) - round(paid_total, 2))
    remaining_total = max(0.0, req_total - max(paid_total, due_total))
    # CRA's requirement test has TWO limbs: net tax owing over the
    # threshold in the current year AND in either of the two
    # preceding years (s.156.1(1)). One known year over the threshold
    # meets the limb; it FAILS only when BOTH years are known and both
    # sit at or below it. Anything else is unknown and assumed met (the
    # common case for someone asking) — one low year with the other
    # unset read as "not met" and printed the unset year as 0.00
    # (audit R1-214).
    priors = [p for p in (prior_net_tax, second_prior_net_tax)
              if p is not None]
    if any(p > THRESHOLD for p in priors):
        prior_test = "met"
    elif len(priors) == 2:
        prior_test = "not_met"
    else:
        prior_test = "unknown"
    governing_total = round(sum(r["amount"] for r in governing), 2)
    required_at_all = (current_net_tax > THRESHOLD
                       and prior_test != "not_met")
    if not required_at_all:
        # s.156.1(1) waives instalments, and s.161(2) charges instalment
        # interest only on someone required to pay them: the JSON said
        # required_at_all=false next to a shortfall and net interest
        # (R1-222). The schedule stays, for reference.
        shortfall = remaining_total = 0.0
        remaining_dates = []
        for k in ("charge_interest", "credit_interest", "net_interest",
                  "net_interest_computed", "penalty"):
            ip[k] = 0.0
    return {
        "year": year, "basis": basis,
        "payments": list(payments),
        "required": rows,
        "required_total": round(req_total, 2),
        "paid_total": round(paid_total, 2),
        "shortfall": round(shortfall, 2),
        "due_to_date": round(due_total, 2),
        "remaining_total": round(remaining_total, 2),
        "per_remaining_date": (
            round((shortfall + remaining_total)
                  / len(remaining_dates), 2)
            if remaining_dates and (shortfall + remaining_total) > 0
            else 0.0),
        "remaining_dates": [r["date"] for r in remaining_dates],
        "current_net_tax": round(current_net_tax, 2),
        "prior_year_test": prior_test,
        "prior_year_net_tax": prior_net_tax,
        "second_prior_net_tax": second_prior_net_tax,
        "governing_required_total": governing_total,
        "required_at_all": required_at_all,
        **ip,
    }


def _wrap_line(text: str) -> str:
    """A report paragraph at the house width (lib/out), indented two."""
    from taxjson.lib.out import fill
    return fill(text, indent="  ")


def render(doc: Dict[str, Any], base: str) -> str:
    """The report — house style (docs/output-style.md)."""
    from taxjson.lib.report_model import fmt_money, render_table

    wrap = _wrap_line

    def _prior_text(v):
        # Never print an unset year as a figure.
        return "not set" if v is None else fmt_money(v)

    _BASIS_LABEL = {
        "current_year": "current-year option (1/4 of this year's "
                        "estimated net tax owing)",
        "prior_year": "prior-year option (1/4 of last year's net tax "
                      "owing)",
        "cra_reminder": "no-calculation option (CRA reminder amounts)",
    }
    lines = [f"TAX INSTALMENTS — {doc['year']}, {base}",
             f"Basis: "
             f"{_BASIS_LABEL.get(doc['basis'], doc['basis'])}",
             f"(ESTIMATE ONLY — CRA computes the real figures from "
             f"assessed returns)", ""]
    if not doc["required_at_all"]:
        if doc["current_net_tax"] <= THRESHOLD:
            lines.append(wrap(
                f"Estimated net tax owing "
                f"{fmt_money(doc['current_net_tax'])} is at or below "
                f"the {fmt_money(THRESHOLD)} threshold — no "
                f"instalments required for {doc['year']}."))
        else:
            lines.append(wrap(
                f"No instalments required for {doc['year']}: CRA asks "
                f"for them only when net tax owing exceeds "
                f"{fmt_money(THRESHOLD)} in the current year AND in "
                f"either of the two preceding years. This year is "
                f"{fmt_money(doc['current_net_tax'])}, but the "
                f"configured prior years are "
                f"{_prior_text(doc.get('prior_year_net_tax'))} and "
                f"{_prior_text(doc.get('second_prior_net_tax'))} "
                f"— both at or below the threshold."))
            lines.append("")
            lines.append(wrap(
                "If those are placeholders rather than your real "
                "figures, replace them with each year's net tax "
                "owing as CRA's instalment chart defines it: lines "
                "42000 + 42200 + 42800 (+ 43200) minus 43700 and the "
                "refundable credits — not line 48500, which also "
                "subtracts the instalments paid. They "
                "decide both whether instalments are owed at all and "
                "which basis CRA charges interest on."))
        return "\n".join(lines)
    body = [[r["date"], fmt_money(r["amount"]),
             fmt_money(r["cumulative_required"]),
             fmt_money(r["cumulative_paid"]), r["status"].upper()]
            for r in doc["required"]]
    foot = [["TOTAL", fmt_money(doc["required_total"]), "",
             fmt_money(doc["paid_total"]), ""]]
    lines += [ln.rstrip() for ln in render_table(
        ["DUE", "AMOUNT", "CUM. REQUIRED", "CUM. PAID", "STATUS"],
        ["<", ">", ">", ">", "<"], body, foot)]
    lines.append("")
    if doc["shortfall"] > 0.005:
        if doc["remaining_dates"]:
            lines.append(wrap(
                f"Behind by {fmt_money(doc['shortfall'])} on the "
                f"dates due so far — "
                f"{fmt_money(doc['per_remaining_date'])} on each of "
                f"the {len(doc['remaining_dates'])} remaining date(s) "
                f"({', '.join(doc['remaining_dates'])}) catches the "
                f"whole year up."))
        else:
            lines.append(wrap(
                f"Behind by {fmt_money(doc['shortfall'])} with no "
                f"dates left — the balance is due "
                f"{balance_due_label()}."))
    elif doc.get("remaining_total", 0.0) > 0.005:
        lines.append(wrap(
            f"On schedule so far. "
            f"{fmt_money(doc['remaining_total'])} remains this year — "
            f"{fmt_money(doc['per_remaining_date'])} on each of the "
            f"{len(doc['remaining_dates'])} remaining date(s) "
            f"({', '.join(doc['remaining_dates'])})."))
    else:
        lines.append(wrap("Schedule is met — no shortfall."))
    sched = doc.get("rate_schedule") or []
    if len(sched) <= 1:
        rate_desc = f"{(doc.get('annual_rate') or 0.0) * 100:.2f}%"
    else:
        rate_desc = ", ".join(
            f"{seg['rate'] * 100:.2f}%"
            + (f" from {seg['from']}" if seg["from"] else "")
            for seg in sched)
    paid_rows = doc.get("payments") or []
    if paid_rows:
        lines += ["", "  PAYMENTS APPLIED"]
        for p in paid_rows:
            # A 78-column budget: 2 indent + 30 label + 14 money + 2
            # gap leaves 30 for the note.
            raw = str(p.get("note") or "")
            note = ("  " + (raw if len(raw) <= 30
                            else raw[:29] + "…")) if raw else ""
            lines.append(f"  {p['date']:<30}"
                         f"{fmt_money(p['amount']):>14}{note}")
    rate_line = f"Rate applied per day: {rate_desc}"
    if doc.get("rate_source") == "published":
        rate_line += (" — CRA's published quarterly rate(s), built in "
                      "(set prescribed_rate(s) to override)")
        if doc.get("rate_extrapolated_before"):
            rate_line += (f"; days before {PUBLISHED_FROM} are outside "
                          f"the built-in table and ASSUME its earliest "
                          f"rate — set prescribed_rates for them")
        if doc.get("rate_extrapolated_after", doc.get("rate_extrapolated")
                   and not doc.get("rate_extrapolated_before")):
            rate_line += (f"; days after {PUBLISHED_THROUGH} assume "
                          f"the last published rate")
    elif doc.get("rate_assumed_before"):
        rate_line += (f"; days before {doc['rate_assumed_before']} ASSUME "
                      f"the first configured rate — add a segment from "
                      f"{doc['year']}-01-01 to set them")
    lines += ["", f"  INTEREST (offset method, compounded daily, "
                  f"to {doc['as_of']})",
              _wrap_line(rate_line + ".")]
    considered = doc.get("interest_bases_considered") or []
    gov = doc.get("interest_basis")
    if (doc.get("governing_required_total") or 0.0) <= 0.005:
        lines.append(wrap(
            f"No interest can arise: the governing "
            f"{(gov or '').replace('_', '-')} basis requires NOTHING "
            f"(its net tax owing is recorded as "
            f"{fmt_money(doc.get('prior_year_net_tax') or 0.0)}). If "
            f"that is a placeholder rather than your real prior-year "
            f"figure, replace it — otherwise this report understates "
            f"what CRA would charge."))
    elif gov == LEAST_PER_DATE:
        per = ", ".join(fmt_money(r["amount"])
                        for r in doc.get("governing_schedule") or [])
        lines.append(_wrap_line(
            f"Interest is assessed on the least total required BY EACH "
            f"due date across the methods your figures support (ITA "
            f"161(4.01)) — here no single method: {per}."))
    elif gov and gov != doc["basis"]:
        lines.append(_wrap_line(
            f"Interest is assessed on the {gov.replace('_', '-')} "
            f"basis — CRA charges on the LEAST of the methods your "
            f"figures support (ITA 161(4.01)), which here is cheaper "
            f"than the {doc['basis'].replace('_', '-')} schedule you "
            f"are paying against."))
    elif len(considered) > 1:
        lines.append(_wrap_line(
            f"Cheapest of the {len(considered)} methods your figures "
            f"support, as CRA assesses it (ITA 161(4.01))."))
    else:
        lines.append(_wrap_line(
            "Only the current-year method is computable — add "
            "prior_year_net_tax (and second_prior_net_tax) so the "
            "cheaper basis CRA would actually charge on can be "
            "considered."))
    for label, key in (("Charge interest", "charge_interest"),
                       ("Credit interest (offset)", "credit_interest"),
                       ("Net instalment interest", "net_interest")):
        lines.append(f"  {label:<30}{fmt_money(doc[key]):>14}")
    computed = doc.get("net_interest_computed", doc["net_interest"])
    if doc["net_interest"] <= 0.005 < computed:
        lines.append(wrap(
            f"Not charged: CRA bills instalment interest only when it "
            f"exceeds {fmt_money(INTEREST_MIN)} (computed "
            f"{fmt_money(computed)})."))
    elif doc["net_interest"] > 0.005:
        lines.append(wrap(
            f"CRA charges this only if it sent you an instalment "
            f"reminder for {doc['year']} showing an amount to pay (and "
            f"only above {fmt_money(INTEREST_MIN)}); with no reminder, "
            f"no instalment interest is charged."))
    if doc["penalty"] > 0.005:
        lines.append(f"  {'s.163.1 PENALTY':<30}"
                     f"{fmt_money(doc['penalty']):>14}")
        lines.append(wrap(
            f"Penalty applies because net interest "
            f"{fmt_money(doc['net_interest'])} exceeds the greater of "
            f"{fmt_money(PENALTY_FLOOR)} and 25% of the "
            f"{fmt_money(doc['interest_if_unpaid'])} that would have "
            f"accrued had nothing been paid "
            f"({fmt_money(doc['penalty_floor'])}); the penalty is half "
            f"the excess."))
    elif doc["net_interest"] > 0.005:
        lines.append(wrap(
            f"No s.163.1 penalty — it applies only once net interest "
            f"exceeds {fmt_money(doc['penalty_floor'])} (the greater "
            f"of {fmt_money(PENALTY_FLOOR)} and 25% of the "
            f"no-payment interest)."))
    return "\n".join(lines)
