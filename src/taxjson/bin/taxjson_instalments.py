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

Interest (ITA 161(2)) uses the OFFSET method: charge interest accrues
daily on any shortfall, credit interest accrues daily on early or
excess payments, and the two net out. Credit interest can only reduce
a charge, never produce a refund.

CRA resets the prescribed rate QUARTERLY and applies the rate in
effect on each individual day — a balance spanning a quarter boundary
is charged at the old rate through the end of that quarter and the new
rate from the start of the next, with accrued interest compounding
across the seam. So the rate is a config input here, either a single
number or a dated schedule, and the daily walk looks up the rate in
force for each day. The report always names the rate(s) it used.

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
THRESHOLD = 3000.0          # net tax owing above which instalments apply
PENALTY_FLOOR = 1000.0      # interest below this can never be penalized
PENALTY_SHARE = 0.50        # of the excess over the floor
PENALTY_ALT_FRACTION = 0.25  # of the no-payment interest
BASES = ("current_year", "prior_year", "cra_reminder")


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
    return [{"date": d.isoformat(), "amount": round(a, 2)}
            for d, a in zip(dates, amounts)]


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
    """(charge, credit) interest by the offset method: walk one day at a
    time from the first due date to `end`, accruing daily-compounded
    interest on whichever side the running balance sits, at the rate
    in force THAT day."""
    if not required:
        return 0.0, 0.0
    start = date.fromisoformat(required[0]["date"])
    if payments:
        # CRA's contra interest runs from the DATE OF PAYMENT, so a
        # prepayment made before the first due date must start the
        # walk — anchoring on the first due date silently discarded
        # its credit.
        start = min(start,
                    min(date.fromisoformat(p["date"])
                        for p in payments))
    if end < start:
        return 0.0, 0.0
    charge = credit = 0.0
    d = start
    while d <= end:
        daily = rate_on(d, rates) / 365.0
        due_to_date = sum(r["amount"] for r in required
                          if date.fromisoformat(r["date"]) <= d)
        paid_to_date = sum(p["amount"] for p in payments
                           if date.fromisoformat(p["date"]) <= d)
        balance = due_to_date - paid_to_date
        # Compound on the accrued interest as well as the principal —
        # CRA compounds daily.
        if balance > 0:
            charge += (balance + charge) * daily
        elif balance < 0:
            credit += (-balance + credit) * daily
        d += timedelta(days=1)
    return charge, credit


def interest_and_penalty(*, required: List[Dict[str, Any]],
                         payments: List[Dict[str, Any]],
                         annual_rate,
                         end: date) -> Dict[str, Any]:
    """Net instalment interest and any s.163.1 penalty. `annual_rate`
    is a scalar or a dated schedule (see normalize_rates)."""
    rates = normalize_rates(annual_rate)
    charge, credit = _accrue(required, payments, rates, end)
    # Credit interest offsets a charge but never becomes a refund.
    net = max(0.0, charge - credit)
    no_pay_charge, _ = _accrue(required, [], rates, end)
    floor = max(PENALTY_FLOOR, PENALTY_ALT_FRACTION * no_pay_charge)
    penalty = (PENALTY_SHARE * (net - floor)) if net > floor else 0.0
    return {"charge_interest": round(charge, 2),
            "credit_interest": round(credit, 2),
            "net_interest": round(net, 2),
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


def build(*, year: int, basis: str, current_net_tax: float,
          payments: List[Dict[str, Any]], annual_rate: float,
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
    end = min(today, date(year + 1, 4, 30))
    # Interest is assessed on the CHEAPEST basis the figures support,
    # not necessarily the one being followed for payments.
    cands = candidate_schedules(
        year=year, current_net_tax=current_net_tax,
        prior_net_tax=prior_net_tax,
        second_prior_net_tax=second_prior_net_tax)
    scored = {name: interest_and_penalty(
        required=sched, payments=payments, annual_rate=annual_rate,
        end=end) for name, sched in cands.items()}
    interest_basis = min(scored, key=lambda k: scored[k]["net_interest"])
    ip = scored[interest_basis]
    ip["interest_basis"] = interest_basis
    ip["interest_bases_considered"] = sorted(scored)
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
    # preceding years. Knowing only the current year, the second limb
    # is unknown and we assume it is met (the common case for someone
    # asking); when the prior figures ARE supplied and both sit at or
    # below the threshold, no instalments are required at all.
    priors = [p for p in (prior_net_tax, second_prior_net_tax)
              if p is not None]
    prior_test = ("unknown" if not priors else
                  ("met" if any(p > THRESHOLD for p in priors)
                   else "not_met"))
    governing_total = round(
        sum(r["amount"] for r in cands[interest_basis]), 2)
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
        "required_at_all": (current_net_tax > THRESHOLD
                            and prior_test != "not_met"),
        **ip,
    }


def _wrap_line(text: str) -> str:
    import textwrap
    return textwrap.fill(text, width=78, initial_indent="  ",
                         subsequent_indent="  ")


def render(doc: Dict[str, Any], base: str) -> str:
    """The report — house style, 78 columns."""
    import textwrap
    from taxjson.lib.report_model import fmt_money, render_table

    def wrap(t):
        return textwrap.fill(t, width=78, initial_indent="  ",
                             subsequent_indent="  ")

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
                f"{fmt_money(doc.get('prior_year_net_tax') or 0.0)} "
                f"and "
                f"{fmt_money(doc.get('second_prior_net_tax') or 0.0)} "
                f"— both at or below the threshold."))
            lines.append("")
            lines.append(wrap(
                "If those are placeholders rather than your real "
                "figures, replace them: line 48500 minus amounts "
                "withheld, from each Notice of Assessment. They "
                "decide both whether instalments are owed at all and "
                "which basis CRA charges interest on."))
        return "\n".join(lines)
    body = [[r["date"], fmt_money(r["amount"]),
             fmt_money(r["cumulative_required"]),
             fmt_money(r["cumulative_paid"]), r["status"].upper()]
            for r in doc["required"]]
    foot = [["TOTAL", fmt_money(doc["required_total"]), "",
             fmt_money(doc["paid_total"]), ""]]
    lines += render_table(["DUE", "AMOUNT", "CUM. REQUIRED",
                           "CUM. PAID", "STATUS"],
                          ["<", ">", ">", ">", "<"], body, foot)
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
                f"dates left — the balance is due April 30."))
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
            # 78-column budget: 2 indent + 30 label + 14 money + 2 gap
            # leaves 30 for the note.
            raw = str(p.get("note") or "")
            note = ("  " + (raw if len(raw) <= 30
                            else raw[:29] + "…")) if raw else ""
            lines.append(f"  {p['date']:<30}"
                         f"{fmt_money(p['amount']):>14}{note}")
    lines += ["", f"  INTEREST (offset method, compounded daily, "
                  f"to {doc['as_of']})",
              _wrap_line(f"Rate applied per day: {rate_desc}.")]
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
