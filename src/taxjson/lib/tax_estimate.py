"""Marginal tax ESTIMATION for `taxjson sum --other-income/--other-losses`.

Incremental method: estimated tax attributable to the year's investment
income = tax(other_income + investment income) - tax(other_income).
Stacking the investment income on top of the user's other income is
what makes the bracket placement honest.

ESTIMATES ONLY — never filing numbers. Simplifications are deliberate
and disclosed by the caller:
  - Canada: all Canadian-listed dividends treated as ELIGIBLE
    (38% gross-up + federal/provincial DTC); foreign dividends are
    ordinary income with a 15% treaty-withholding FTC assumed already
    paid; --other-losses are prior-year capital losses in FULL dollars,
    netted against gains before the 50% inclusion; Ontario surtax
    modelled; BPA phase-out and QC abatement NOT modelled.
  - USA: single filer, standard deduction; dividends assumed QUALIFIED
    (stack with LT); PIL ordinary; --other-losses net ST first, then
    LT, excess offsets up to $3,000 of ordinary income; NIIT 3.8%
    over $200k MAGI; no state tax.

`_VINTAGES` holds one table set per published tax year;
`apply_vintage(year)` selects by project year and the printed vintage
discloses the pick — add a new vintage annually.
"""

from typing import Any, Dict, List, Tuple

RATE_VINTAGE = "2025"

_INF = float("inf")

# Three published vintages; `apply_vintage(year)` selects one (exact
# year, else the LATEST table at or before it — a 2027 project runs on
# the 2026 tables until the annual refresh lands, and the printed
# vintage says so). Figures verified 2026-09-09 against the CRA 2026
# indexation release (factor 1.02; Bill C-4 14% full-year), the ON
# (1.019, surtax 5,818/7,446) / BC (Budget 2026: lowest rate 5.60%,
# thresholds x1.022) / AB (Bill 32, x1.02, 8% first bracket) 2026
# figures, and IRS Rev. Proc. 2025-32 + OBBBA — still VERIFY against
# the official rate cards before relying on an estimate near a
# threshold.
_VINTAGES: Dict[str, Dict[str, Any]] = {
    "2024": {
        # CRA 2024 indexation (x1.047). Lowest federal rate still 15%
        # (Bill C-4's cut starts in 2025). The AMT regime is already the
        # post-2024 one (20.5%, exemption at the fourth-bracket floor
        # 173,205), so the shared AMT model applies unchanged.
        "CA_FED_BRACKETS": [(55867, .15), (111733, .205),
                            (173205, .26), (246752, .29), (_INF, .33)],
        "CA_FED_BPA": 15705.0,
        "CA_PROVINCES": {
            # ON x1.045; surtax thresholds 5,554 / 7,108.
            "ON": {"brackets": [(51446, .0505), (102894, .0915),
                                (150000, .1116), (220000, .1216),
                                (_INF, .1316)],
                   "bpa": 12399.0, "dtc_eligible": .10,
                   "surtax": [(5554, .20), (7108, .36)],
                   "amt_factor": .3367},
            "BC": {"brackets": [(47937, .0506), (95875, .077),
                                (110076, .105), (133664, .1229),
                                (181232, .147), (252752, .168),
                                (_INF, .205)],
                   "bpa": 12580.0, "dtc_eligible": .12, "surtax": [],
                   "amt_factor": .337},
            "AB": {"brackets": [(148269, .10), (177922, .12),
                                (237230, .13), (355845, .14),
                                (_INF, .15)],
                   "bpa": 21885.0, "dtc_eligible": .0812, "surtax": [],
                   "amt_factor": .35},
        },
        # IRS Rev. Proc. 2023-34 (single).
        "US_STD_DEDUCTION": 14600.0,
        "US_ORD_BRACKETS": [(11600, .10), (47150, .12), (100525, .22),
                            (191950, .24), (243725, .32),
                            (609350, .35), (_INF, .37)],
        "US_LTCG_BRACKETS": [(47025, .00), (518900, .15), (_INF, .20)],
    },
    "2025": {
        # Bill C-4 cut the lowest federal rate mid-2025: CRA applies a
        # 14.5% BLENDED rate for the 2025 tax year.
        "CA_FED_BRACKETS": [(57375, .145), (114750, .205),
                            (177882, .26), (253414, .29), (_INF, .33)],
        "CA_FED_BPA": 16129.0,
        "CA_PROVINCES": {
            "ON": {"brackets": [(52886, .0505), (105775, .0915),
                                (150000, .1116), (220000, .1216),
                                (_INF, .1316)],
                   "bpa": 12747.0, "dtc_eligible": .10,
                   "surtax": [(5710, .20), (7307, .36)],
                   "amt_factor": .3367},
            "BC": {"brackets": [(49279, .0506), (98560, .077),
                                (113158, .105), (137407, .1229),
                                (186306, .147), (259829, .168),
                                (_INF, .205)],
                   "bpa": 12932.0, "dtc_eligible": .12, "surtax": [],
                   "amt_factor": .337},
            # AB 14->15% boundary corrected 302,757 -> 362,961 (the
            # 2026 threshold 370,220 = 362,961 x 1.02; the old figure
            # matched no published AB year — VERIFY).
            "AB": {"brackets": [(60000, .08), (151234, .10),
                                (181481, .12), (241974, .13),
                                (362961, .14), (_INF, .15)],
                   "bpa": 22323.0, "dtc_eligible": .0812, "surtax": [],
                   "amt_factor": .35},
        },
        "US_STD_DEDUCTION": 15750.0,        # OBBBA
        "US_ORD_BRACKETS": [(11925, .10), (48475, .12), (103350, .22),
                            (197300, .24), (250525, .32),
                            (626350, .35), (_INF, .37)],
        "US_LTCG_BRACKETS": [(48350, .00), (533400, .15), (_INF, .20)],
    },
    "2026": {
        # CRA 2026 indexation (x1.02) + Bill C-4 14% for the full year.
        "CA_FED_BRACKETS": [(58523, .14), (117045, .205),
                            (181440, .26), (258482, .29), (_INF, .33)],
        "CA_FED_BPA": 16452.0,
        "CA_PROVINCES": {
            # ON x1.019 (150k/220k bands unindexed by statute);
            # surtax thresholds 5,818 / 7,446.
            "ON": {"brackets": [(53891, .0505), (107785, .0915),
                                (150000, .1116), (220000, .1216),
                                (_INF, .1316)],
                   "bpa": 12989.0, "dtc_eligible": .10,
                   "surtax": [(5818, .20), (7446, .36)],
                   "amt_factor": .3367},
            # BC Budget 2026: lowest rate 5.06% -> 5.60% (credit rate
            # follows); thresholds x1.022; indexation then paused
            # 2027-2030.
            "BC": {"brackets": [(50363, .056), (100728, .077),
                                (115648, .105), (140430, .1229),
                                (190405, .147), (265545, .168),
                                (_INF, .205)],
                   "bpa": 13217.0, "dtc_eligible": .12, "surtax": [],
                   "amt_factor": .337},
            # AB Bill 32 x1.02; the 8% first bracket indexes from 2026.
            "AB": {"brackets": [(61200, .08), (154259, .10),
                                (185111, .12), (246813, .13),
                                (370220, .14), (_INF, .15)],
                   "bpa": 22769.0, "dtc_eligible": .08, "surtax": [],
                   "amt_factor": .35},
        },
        # IRS Rev. Proc. 2025-32 (single).
        "US_STD_DEDUCTION": 16100.0,
        "US_ORD_BRACKETS": [(12400, .10), (50400, .12), (105700, .22),
                            (201775, .24), (256225, .32),
                            (640600, .35), (_INF, .37)],
        "US_LTCG_BRACKETS": [(49450, .00), (545500, .15), (_INF, .20)],
    },
}

# ------------------------------------------------------------- Canada
CA_FED_BRACKETS = _VINTAGES["2025"]["CA_FED_BRACKETS"]
CA_FED_BPA = _VINTAGES["2025"]["CA_FED_BPA"]
CA_ELIGIBLE_GROSSUP = 1.38
CA_FED_DTC_ELIGIBLE = .150198          # of the GROSSED amount
CA_FOREIGN_WITHHOLDING = .15           # treaty rate assumed already paid
CA_INCLUSION = .50

# ---- Alternative Minimum Tax (post-2024 regime) ----------------------
# The AMT recomputation targets preferentially-taxed income: capital
# gains at 100% inclusion, dividends at their ACTUAL amount with the
# dividend tax credit DENIED, most non-refundable credits (we model
# the BPA) allowed at 50%, then a flat rate over the exemption — which
# is pinned to the start of the fourth federal bracket, so it indexes
# with the bracket table above and the annual refresh updates both.
CA_AMT_RATE = .205
CA_AMT_CREDIT_ALLOWANCE = .50          # of modeled non-refundable credits
CA_AMT_LOSS_ALLOWANCE = .50            # loss carryforwards deduct at 50%


def ca_amt_exemption() -> float:
    return float(CA_FED_BRACKETS[2][0])

CA_PROVINCES: Dict[str, Dict[str, Any]] = _VINTAGES["2025"]["CA_PROVINCES"]

# ---------------------------------------------------------------- USA
US_STD_DEDUCTION = _VINTAGES["2025"]["US_STD_DEDUCTION"]
US_ORD_BRACKETS = _VINTAGES["2025"]["US_ORD_BRACKETS"]
US_LTCG_BRACKETS = _VINTAGES["2025"]["US_LTCG_BRACKETS"]
US_NIIT_RATE = .038
US_NIIT_MAGI_THRESHOLD = 200000.0      # single
US_ORDINARY_LOSS_CAP = 3000.0


def apply_vintage(year) -> str:
    """Select the rate vintage for `year`: exact match, else the
    latest table at or before it (a future year runs on the newest
    published figures; a year BEFORE the earliest table uses the
    earliest — historical estimates on old rates are out of scope;
    the printed vintage always discloses which applied). Mutates
    the module-level tables — the estimate is a one-shot CLI
    computation, and every consumer reads them through this module.
    Returns the vintage applied."""
    global RATE_VINTAGE, CA_FED_BRACKETS, CA_FED_BPA, CA_PROVINCES
    global US_STD_DEDUCTION, US_ORD_BRACKETS, US_LTCG_BRACKETS
    try:
        y = int(year)
    except (TypeError, ValueError):
        # A missing/unparseable year resets to the DEFAULT vintage
        # rather than keeping whatever a previous call applied — an
        # in-process consumer (GUI) estimating a year-less project
        # after a 2026 one must not inherit 2026 tables (round-six
        # audit finding: sticky vintage).
        y = int(min(_VINTAGES, key=int))
    eligible = [v for v in _VINTAGES if int(v) <= y]
    pick = max(eligible, key=int) if eligible else min(_VINTAGES,
                                                       key=int)
    t = _VINTAGES[pick]
    RATE_VINTAGE = pick
    CA_FED_BRACKETS = t["CA_FED_BRACKETS"]
    CA_FED_BPA = t["CA_FED_BPA"]
    CA_PROVINCES = t["CA_PROVINCES"]
    US_STD_DEDUCTION = t["US_STD_DEDUCTION"]
    US_ORD_BRACKETS = t["US_ORD_BRACKETS"]
    US_LTCG_BRACKETS = t["US_LTCG_BRACKETS"]
    return pick


def _bracket_tax(income: float, brackets: List[Tuple[float, float]]) -> float:
    """Progressive tax on `income` over [(top, rate), ...]."""
    tax, lower = 0.0, 0.0
    for top, rate in brackets:
        if income <= lower:
            break
        tax += (min(income, top) - lower) * rate
        lower = top
    return tax


def _stacked_tax(base: float, add: float,
                 brackets: List[Tuple[float, float]]) -> float:
    """Tax on `add` stacked on top of `base` income."""
    return _bracket_tax(base + add, brackets) - _bracket_tax(base, brackets)


def bracket_slices(income: float, brackets: List[Tuple[float, float]]
                   ) -> List[Tuple[float, float, float, float]]:
    """[(lower, upper, rate, tax)] for every bracket slice `income`
    touches — the --verbose trace's raw material."""
    out, lower = [], 0.0
    for top, rate in brackets:
        if income <= lower:
            break
        hi = min(income, top)
        out.append((lower, hi, rate, (hi - lower) * rate))
        lower = top
    return out


def stacked_slices(base: float, add: float,
                   brackets: List[Tuple[float, float]]
                   ) -> List[Tuple[float, float, float, float]]:
    """[(lower, upper, rate, tax)] for the slices `add` occupies when
    stacked on top of `base` (the US preferential-income shape)."""
    out, lower = [], 0.0
    for top, rate in brackets:
        lo, hi = max(lower, base), min(top, base + add)
        if hi > lo:
            out.append((lo, hi, rate, (hi - lo) * rate))
        lower = top
    return out


# ------------------------------------------------------------- Canada
def _canada_tax(ordinary: float, taxable_gain: float,
                eligible_div: float, foreign_div: float,
                prov: Dict[str, Any], ftc=None) -> Dict[str, Any]:
    """Federal + provincial tax for one income mix (all components
    already in their taxed form except the eligible gross-up applied
    here). Floors at zero per jurisdiction. Returns totals PLUS the
    per-step detail the --verbose trace prints."""
    grossed = eligible_div * CA_ELIGIBLE_GROSSUP
    ti = ordinary + taxable_gain + foreign_div + grossed

    fed_gross = _bracket_tax(ti, CA_FED_BRACKETS)
    fed_bpa = CA_FED_BPA * CA_FED_BRACKETS[0][1]
    fed_dtc = grossed * CA_FED_DTC_ELIGIBLE
    fed_ftc = (ftc if ftc is not None
               else foreign_div * CA_FOREIGN_WITHHOLDING)
    fed = max(0.0, fed_gross - fed_bpa - fed_dtc - fed_ftc)

    prov_gross = _bracket_tax(ti, prov["brackets"])
    prov_bpa = prov["bpa"] * prov["brackets"][0][1]
    basic = max(0.0, prov_gross - prov_bpa)
    surtax_parts = [(thr, rate, max(0.0, basic - thr) * rate)
                    for thr, rate in prov.get("surtax", [])]
    surtax = sum(amt for _t, _r, amt in surtax_parts)
    prov_dtc = grossed * prov["dtc_eligible"]
    p = max(0.0, basic + surtax - prov_dtc)

    return {"federal": fed, "provincial": p, "total": fed + p,
            "detail": {
                "ti": ti, "fed_gross": fed_gross, "fed_bpa": fed_bpa,
                "fed_dtc": fed_dtc, "fed_ftc": fed_ftc,
                "prov_gross": prov_gross, "prov_bpa": prov_bpa,
                "prov_basic": basic, "surtax_parts": surtax_parts,
                "prov_dtc": prov_dtc,
            }}


def _amt_canada(*, realized: float, eligible_div: float,
                foreign_div: float, pil: float, other_income: float,
                other_losses: float, ftc: float, regular_fed: float,
                prov: Dict[str, Any]) -> Dict[str, Any]:
    """The post-2024 federal AMT check plus the provincial piggyback.
    Pure arithmetic on figures estimate_canada already holds — nothing
    outside this module learns AMT exists. Always returned (binding or
    not): the headroom number is planning information in itself."""
    # Losses deduct at 50% in the AMT base — but only the CLAIMABLE
    # amount, exactly as the regular branch caps them at the year's
    # gains (111(1)(b)). Deducting the whole carryforward POOL let an
    # unused balance that changes nothing in regular tax silently
    # erase a real AMT liability.
    claimable_losses = min(max(0.0, realized), max(0.0, other_losses))
    ati = (max(0.0, realized - CA_AMT_LOSS_ALLOWANCE * claimable_losses)
           + eligible_div + foreign_div + pil + other_income)
    exemption = ca_amt_exemption()
    base = max(0.0, ati - exemption)
    gross = base * CA_AMT_RATE
    bpa_credit = (CA_FED_BPA * CA_FED_BRACKETS[0][1]
                  * CA_AMT_CREDIT_ALLOWANCE)
    # The special foreign tax credit is allowed IN FULL under the AMT
    # rules — deliberately not subject to the 50% credit haircut that
    # applies to the BPA above.
    fed_min = max(0.0, gross - bpa_credit - ftc)
    excess = max(0.0, fed_min - regular_fed)
    prov_amt = excess * float(prov.get("amt_factor") or 0.0)
    return {
        "adjusted_income": round(ati, 2),
        "exemption": round(exemption, 2),
        "rate": CA_AMT_RATE,
        "minimum_fed": round(fed_min, 2),
        "regular_fed": round(regular_fed, 2),
        "excess_fed": round(excess, 2),
        "provincial_amt": round(prov_amt, 2),
        "topup": round(excess + prov_amt, 2),
        "carryforward": round(excess, 2),
        "binding": excess > 0.005,
        "headroom": round(max(0.0, regular_fed - fed_min), 2),
    }


def estimate_canada(*, realized: float, eligible_div: float,
                    year=None,
                    foreign_div: float, pil: float,
                    other_income: float, other_losses: float,
                    province: str,
                    actual_withheld=None) -> Dict[str, Any]:
    apply_vintage(year)
    prov_key = province.strip().upper()
    if prov_key not in CA_PROVINCES:
        raise ValueError(
            f"unsupported province {province!r} for the estimate "
            f"(supported: {', '.join(sorted(CA_PROVINCES))})")
    prov = CA_PROVINCES[prov_key]

    net_gain = realized - other_losses
    losses_unused = max(0.0, -net_gain)
    taxable_gain = max(0.0, net_gain) * CA_INCLUSION

    # FTC: prefer the ACTUAL withholding recorded in the books
    # (TAX rows), capped at the 15% treaty-creditable ceiling — the
    # flat 15%-of-foreign-dividends assumption stands only when no
    # actual figure is supplied.
    if actual_withheld is not None:
        ftc = min(max(0.0, actual_withheld),
                  foreign_div * CA_FOREIGN_WITHHOLDING)
        ftc_source = "actual TAX rows (capped at 15% of foreign divs)"
    else:
        ftc = foreign_div * CA_FOREIGN_WITHHOLDING
        ftc_source = "assumed 15% of foreign dividends"
    with_inv = _canada_tax(other_income + pil, taxable_gain,
                           eligible_div, foreign_div, prov, ftc=ftc)
    base = _canada_tax(other_income, 0.0, 0.0, 0.0, prov)
    est = max(0.0, with_inv["total"] - base["total"])
    inv_income = realized + eligible_div + foreign_div + pil

    def _totals(d):
        return {k: round(d[k], 2) for k in ("federal", "provincial",
                                            "total")}
    amt = _amt_canada(realized=realized, eligible_div=eligible_div,
                      foreign_div=foreign_div, pil=pil,
                      other_income=other_income,
                      other_losses=other_losses, ftc=ftc,
                      regular_fed=with_inv["federal"], prov=prov)
    return {
        "country": "canada", "province": prov_key,
        "amt": amt,
        "estimated_tax_with_amt": round(est + amt["topup"], 2),
        "vintage": RATE_VINTAGE,
        "taxable_gain": round(taxable_gain, 2),
        "losses_applied": round(min(max(0.0, realized), other_losses), 2),
        "losses_unused": round(losses_unused, 2),
        "grossed_eligible": round(eligible_div * CA_ELIGIBLE_GROSSUP, 2),
        "ftc_assumed": round(ftc, 2),
        "ftc_source": ftc_source,
        "tax_with": _totals(with_inv),
        "tax_base": _totals(base),
        "trace_with": with_inv["detail"],
        "trace_base": base["detail"],
        "estimated_tax": round(est, 2),
        "investment_income": round(inv_income, 2),
        "avg_rate_pct": round(est / inv_income * 100, 2)
        if inv_income > 0 else None,
    }


# ---------------------------------------------------------------- USA
def _usa_tax(ordinary: float, pref: float) -> Dict[str, Any]:
    """Ordinary income at the ordinary brackets; preferential income
    (LT + qualified) stacked ON TOP at the LTCG brackets — the standard
    worksheet shape. Standard deduction reduces ordinary first, then
    preferential. NIIT on investment income over the MAGI threshold is
    added by the caller (it needs the investment split)."""
    ord_taxable = max(0.0, ordinary - US_STD_DEDUCTION)
    unused_ded = max(0.0, US_STD_DEDUCTION - ordinary)
    pref_taxable = max(0.0, pref - unused_ded)
    ord_tax = _bracket_tax(ord_taxable, US_ORD_BRACKETS)
    pref_tax = _stacked_tax(ord_taxable, pref_taxable, US_LTCG_BRACKETS)
    return {"ordinary": ord_tax, "preferential": pref_tax,
            "total": ord_tax + pref_tax,
            "detail": {"ord_taxable": ord_taxable,
                       "pref_taxable": pref_taxable}}


def estimate_usa(*, st: float, lt: float, qualified_div: float,
                 year=None,
                 pil: float, other_income: float,
                 other_losses: float) -> Dict[str, Any]:
    apply_vintage(year)
    # Carryover losses net ST first (highest-taxed), then LT; excess
    # offsets up to $3,000 of ordinary income; the rest carries on.
    loss = other_losses
    st_net = st - min(loss, max(0.0, st)) if st > 0 else st
    loss = max(0.0, loss - max(0.0, st - st_net))
    lt_net = lt - min(loss, max(0.0, lt)) if lt > 0 else lt
    loss = max(0.0, loss - max(0.0, lt - lt_net))
    # Schedule D cross-netting BEFORE any clamping: a loss in one
    # character offsets the other's gain (the residual loss keeps its
    # own character). Clamping first silently discarded the losing
    # side whenever the year was net-positive with mixed signs —
    # ST -1,000 / LT +5,000 taxed the full 5,000 (REVIEW #24).
    if st_net < 0.0 < lt_net:
        move = min(-st_net, lt_net)
        st_net += move
        lt_net -= move
    elif lt_net < 0.0 < st_net:
        move = min(-lt_net, st_net)
        lt_net += move
        st_net -= move
    # Net negative capital result also offsets ordinary (cap 3,000).
    net_cap = st_net + lt_net
    ordinary_offset = min(US_ORDINARY_LOSS_CAP, loss + max(0.0, -net_cap))
    st_net, lt_net = max(0.0, st_net), max(0.0, lt_net)

    inv_ordinary = st_net + pil - ordinary_offset
    inv_pref = lt_net + qualified_div
    with_inv = _usa_tax(other_income + inv_ordinary, inv_pref)
    base = _usa_tax(other_income, 0.0)

    inv_income_for_niit = st_net + lt_net + qualified_div + pil
    magi = other_income + inv_ordinary + inv_pref
    niit = US_NIIT_RATE * min(
        max(0.0, inv_income_for_niit),
        max(0.0, magi - US_NIIT_MAGI_THRESHOLD))

    # Signed: a net-loss year (the $3,000 ordinary offset) legitimately
    # REDUCES tax vs the base — report the saving as a negative estimate.
    est = with_inv["total"] - base["total"] + niit
    inv_income = st + lt + qualified_div + pil

    def _totals(d):
        return {k: round(d[k], 2) for k in ("ordinary", "preferential",
                                            "total")}
    return {
        "country": "usa", "vintage": RATE_VINTAGE,
        "st_net": round(st_net, 2), "lt_net": round(lt_net, 2),
        "ordinary_offset": round(ordinary_offset, 2),
        "losses_unused": round(loss + max(0.0, -net_cap)
                               - ordinary_offset, 2),
        "niit": round(niit, 2),
        "niit_base": round(min(max(0.0, inv_income_for_niit),
                               max(0.0, magi - US_NIIT_MAGI_THRESHOLD)), 2),
        "magi": round(magi, 2),
        "tax_with": _totals(with_inv),
        "tax_base": _totals(base),
        "trace_with": with_inv["detail"],
        "trace_base": base["detail"],
        "estimated_tax": round(est, 2),
        "investment_income": round(inv_income, 2),
        "avg_rate_pct": round(est / inv_income * 100, 2)
        if inv_income > 0 else None,
    }
