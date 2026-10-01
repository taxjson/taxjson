"""Marginal tax ESTIMATION for `taxjson sum --other-income/--other-losses`.

Incremental method: estimated tax attributable to the year's investment
income = tax(other_income + investment income) - tax(other_income).
Stacking the investment income on top of the user's other income is
what makes the bracket placement honest.

ESTIMATES ONLY — never filing numbers. Simplifications are deliberate
and disclosed by the caller:
  - Canada: all Canadian-listed dividends treated as ELIGIBLE
    (38% gross-up + federal/provincial DTC; non-eligible dividends are
    NOT modelled); foreign dividends are ordinary income with a 15%
    treaty-withholding FTC assumed already paid (actual TAX rows when
    the books carry them); crypto STAKING rewards are ordinary income
    with no withholding and no FTC; --other-losses are prior-year
    capital losses in FULL dollars, netted against gains before the
    50% inclusion (deducted at line 25300, so they do not lower the
    NET income the federal BPA phase-down reads); --deductions (lines
    20700-23500 the AMT allows in full: RRSP 20800, FHSA, RPP ...) and
    --carrying-charges (line 22100, allowed at 50% in the post-2024
    AMT base) lower NET and taxable income, other income first, then
    investment income; net income floors at zero; the federal enhanced
    BPA phase-down, the Ontario surtax and the Ontario Health Premium
    are modelled; QC abatement and low-income reductions are NOT.
  - USA: single filer, standard deduction; dividends assumed QUALIFIED
    (stack with LT); PIL and staking ordinary; --other-losses net ST
    first, then LT, excess offsets up to $3,000 of ordinary income;
    NIIT 3.8% over $200k MAGI; no state tax.

`_VINTAGES` holds one table set per published tax year;
`apply_vintage(year)` selects by project year and the printed vintage
discloses the pick (with an explicit note when it is not the requested
year) — add a new vintage annually.
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
#
# Re-verified 2026-09-27 (audit/estimate):
#  - ON additional tax for minimum tax = 24.63% of the federal AMT
#    excess from 2024 (33.67% through 2023), and the ON surtax is
#    recomputed on basic ON tax PLUS that additional tax — ON428
#    worksheet 5006-D, "Line 72":
#    https://www.canada.ca/content/dam/cra-arc/formspubs/pbg/5006-d/5006-d-25e.pdf
#    (5006-d-24e.pdf: 24.63%; 5006-d-23e.pdf: 33.67%).
#  - BC minimum-tax factor = BC lowest rate / federal lowest rate,
#    rounded to 0.001 (BC Income Tax Act s.4.8(2)): 2024 .337, 2025
#    .349 (BC428 line 69: .../pbg/5010-c/5010-c-25e.pdf), 2026 .400
#    (5.60/14; gov.bc.ca "B.C. minimum tax ... 40% ... for 2026":
#    https://www2.gov.bc.ca/gov/content/taxes/income-taxes/personal/credits/basic).
#  - AB minimum-tax factor 35% (AB428 line 67, .../pbg/5009-c/5009-c-25e.pdf).
#  - AB eligible DTC 8.12% for 2021+: AB Personal Income Tax Act s.21(c)
#    reads s.121(b)'s 6/11 as 227/770 (227/770 x 0.38 / 1.38 = 8.12% of
#    the grossed-up amount): https://kings-printer.alberta.ca/documents/Acts/A30.pdf
#  - BC 2026 BPA 13,216 (gov.bc.ca basic credits page above).
#  - Federal enhanced BPA phases down on NET income (line 23600) from
#    the start of the 29% bracket to the start of the 33% bracket:
#    https://www.canada.ca/en/revenue-agency/services/tax/individuals/frequently-asked-questions-individuals/adjustment-personal-income-tax-benefit-amounts.html
#    and the line 30000 page (2025: 16,129 -> 14,538 over 177,882-253,414).
#  - Ontario Health Premium (ON428 line 89, unindexed chart, max $900
#    over $200,600 of taxable income):
#    https://www.canada.ca/content/dam/cra-arc/formspubs/pbg/5006-c/5006-c-25e.pdf
_VINTAGES: Dict[str, Dict[str, Any]] = {
    "2024": {
        # CRA 2024 indexation (x1.047). Lowest federal rate still 15%
        # (Bill C-4's cut starts in 2025). The AMT regime is already the
        # post-2024 one (20.5%, exemption at the fourth-bracket floor
        # 173,205), so the shared AMT model applies unchanged.
        "CA_FED_BRACKETS": [(55867, .15), (111733, .205),
                            (173205, .26), (246752, .29), (_INF, .33)],
        "CA_FED_BPA": 15705.0,
        "CA_FED_BPA_MIN": 14156.0,      # at net income >= 246,752
        "CA_PROVINCES": {
            # ON x1.045; surtax thresholds 5,554 / 7,108.
            "ON": {"brackets": [(51446, .0505), (102894, .0915),
                                (150000, .1116), (220000, .1216),
                                (_INF, .1316)],
                   "bpa": 12399.0, "dtc_eligible": .10,
                   "surtax": [(5554, .20), (7108, .36)],
                   "health_premium": True,
                   "amt_factor": .2463},
            "BC": {"brackets": [(47937, .0506), (95875, .077),
                                (110076, .105), (133664, .1229),
                                (181232, .147), (252752, .168),
                                (_INF, .205)],
                   "bpa": 12580.0, "dtc_eligible": .12, "surtax": [],
                   "amt_factor": .337},             # 5.06 / 15
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
        "CA_FED_BPA_MIN": 14538.0,      # at net income >= 253,414
        "CA_PROVINCES": {
            "ON": {"brackets": [(52886, .0505), (105775, .0915),
                                (150000, .1116), (220000, .1216),
                                (_INF, .1316)],
                   "bpa": 12747.0, "dtc_eligible": .10,
                   "surtax": [(5710, .20), (7307, .36)],
                   "health_premium": True,
                   "amt_factor": .2463},
            "BC": {"brackets": [(49279, .0506), (98560, .077),
                                (113158, .105), (137407, .1229),
                                (186306, .147), (259829, .168),
                                (_INF, .205)],
                   "bpa": 12932.0, "dtc_eligible": .12, "surtax": [],
                   "amt_factor": .349},             # 5.06 / 14.5
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
        "CA_FED_BPA_MIN": 14829.0,      # at net income >= 258,482
        "CA_PROVINCES": {
            # ON x1.019 (150k/220k bands unindexed by statute);
            # surtax thresholds 5,818 / 7,446.
            "ON": {"brackets": [(53891, .0505), (107785, .0915),
                                (150000, .1116), (220000, .1216),
                                (_INF, .1316)],
                   "bpa": 12989.0, "dtc_eligible": .10,
                   "surtax": [(5818, .20), (7446, .36)],
                   "health_premium": True,
                   # 5.05 / 20.5, as 2024-2025; ASSUMED until the
                   # 2026 5006-D is published.
                   "amt_factor": .2463, "amt_factor_assumed": True},
            # BC Budget 2026: lowest rate 5.06% -> 5.60% (credit rate
            # follows); thresholds x1.022; indexation then paused
            # 2027-2030.
            "BC": {"brackets": [(50363, .056), (100728, .077),
                                (115648, .105), (140430, .1229),
                                (190405, .147), (265545, .168),
                                (_INF, .205)],
                   "bpa": 13216.0, "dtc_eligible": .12, "surtax": [],
                   "amt_factor": .400},             # 5.60 / 14
            # AB Bill 32 x1.02; the 8% first bracket indexes from 2026.
            "AB": {"brackets": [(61200, .08), (154259, .10),
                                (185111, .12), (246813, .13),
                                (370220, .14), (_INF, .15)],
                   "bpa": 22769.0, "dtc_eligible": .0812,
                   "surtax": [],
                   # 35% as 2024-2025; ASSUMED until the 2026 AB428.
                   "amt_factor": .35, "amt_factor_assumed": True},
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
CA_FED_BPA_MIN = _VINTAGES["2025"]["CA_FED_BPA_MIN"]
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
CA_AMT_CARRYING_CHARGE_ALLOWANCE = .50  # line 22100 deducts at 50%


def ca_amt_exemption() -> float:
    return float(CA_FED_BRACKETS[2][0])


def ca_fed_bpa(net_income: float) -> float:
    """The federal basic personal AMOUNT for a given NET income (line
    23600). The enhanced amount (CA_FED_BPA) phases down linearly to
    CA_FED_BPA_MIN between the start of the 29% bracket and the start
    of the 33% bracket (s.118(1)(c) / the line 30000 Federal
    Worksheet); both ends are the vintage's own bracket thresholds.
    https://www.canada.ca/en/revenue-agency/services/tax/individuals/frequently-asked-questions-individuals/adjustment-personal-income-tax-benefit-amounts.html"""
    start = float(CA_FED_BRACKETS[2][0])
    end = float(CA_FED_BRACKETS[3][0])
    if net_income <= start:
        return float(CA_FED_BPA)
    if net_income >= end:
        return float(CA_FED_BPA_MIN)
    return (CA_FED_BPA - (CA_FED_BPA - CA_FED_BPA_MIN)
            * (net_income - start) / (end - start))


# Ontario Health Premium chart (ON428 line 89; NOT indexed):
# (income floor, premium at the floor, phase-in rate, cap of the band).
# https://www.canada.ca/content/dam/cra-arc/formspubs/pbg/5006-c/5006-c-25e.pdf
ON_HEALTH_PREMIUM = [(20000.0, 0.0, .06, 300.0),
                     (36000.0, 300.0, .06, 450.0),
                     (48000.0, 450.0, .25, 600.0),
                     (72000.0, 600.0, .25, 750.0),
                     (200000.0, 750.0, .25, 900.0)]


def ontario_health_premium(taxable_income: float) -> float:
    """ON428 line 89 on TAXABLE income (line 26000): 0 to $20,000, then
    6%/25% phase-ins to plateaus of 300/450/600/750, and $900 over
    $200,600."""
    prem = 0.0
    for floor, at_floor, rate, cap in ON_HEALTH_PREMIUM:
        if taxable_income > floor:
            prem = min(cap, at_floor + rate * (taxable_income - floor))
    return prem


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
    global CA_FED_BPA_MIN
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
    CA_FED_BPA_MIN = t["CA_FED_BPA_MIN"]
    CA_PROVINCES = t["CA_PROVINCES"]
    US_STD_DEDUCTION = t["US_STD_DEDUCTION"]
    US_ORD_BRACKETS = t["US_ORD_BRACKETS"]
    US_LTCG_BRACKETS = t["US_LTCG_BRACKETS"]
    return pick


def vintage_notes(year, pick: str, *, country: str = "canada"
                  ) -> List[str]:
    """Plain-language notes when the applied vintage is not the
    requested tax year — the estimate otherwise silently ran a 2027
    project on 2026 tables, or a 2023 project on 2024 tables AND the
    post-2024 AMT (which did not exist in 2023)."""
    try:
        y = int(year)
    except (TypeError, ValueError):
        return [f"No tax year set — the {pick} rate tables were used."]
    if str(y) == pick:
        return []
    earliest = min(_VINTAGES, key=int)
    if y < int(earliest):
        msg = (f"Tax year {y} predates the earliest built-in rate "
               f"vintage ({earliest}); the {pick} brackets, credits and "
               f"thresholds were used, so the figures are indicative "
               f"only.")
        if country == "canada":
            msg += (f" The AMT check applies the post-2024 regime "
                    f"(20.5% over an exemption at the 29% bracket, "
                    f"gains at 100%); {y} fell under the old AMT (15% "
                    f"over $40,000, gains at 80%), so the AMT figures "
                    f"do not apply as shown.")
        return [msg]
    return [f"Tax year {y} has no built-in rate vintage yet; the {pick} "
            f"tables were used (brackets, credits and thresholds not "
            f"indexed to {y})."]


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
def _surtax_parts(basic: float, prov: Dict[str, Any]
                  ) -> List[Tuple[float, float, float]]:
    return [(thr, rate, max(0.0, basic - thr) * rate)
            for thr, rate in prov.get("surtax", [])]


def _canada_tax(ordinary: float, taxable_gain: float,
                eligible_div: float, foreign_div: float,
                prov: Dict[str, Any], ftc=None,
                net_income=None) -> Dict[str, Any]:
    """Federal + provincial tax for one income mix (all components
    already in their taxed form except the eligible gross-up applied
    here). Floors at zero per jurisdiction. `net_income` (line 23600)
    drives the federal BPA phase-down; it defaults to taxable income
    and differs only by carryforward losses deducted at line 25300.
    Returns totals PLUS the per-step detail the --verbose trace
    prints."""
    grossed = eligible_div * CA_ELIGIBLE_GROSSUP
    # Deductions enter `ordinary` as a negative; income never goes
    # below zero (a return does not carry a negative net income).
    ti = max(0.0, ordinary + taxable_gain + foreign_div + grossed)
    net = ti if net_income is None else max(0.0, net_income)

    fed_gross = _bracket_tax(ti, CA_FED_BRACKETS)
    fed_bpa_amount = ca_fed_bpa(net)
    fed_bpa = fed_bpa_amount * CA_FED_BRACKETS[0][1]
    fed_dtc = grossed * CA_FED_DTC_ELIGIBLE
    fed_ftc = (ftc if ftc is not None
               else foreign_div * CA_FOREIGN_WITHHOLDING)
    fed_before_ftc = max(0.0, fed_gross - fed_bpa - fed_dtc)
    fed = max(0.0, fed_before_ftc - fed_ftc)
    # Foreign tax the federal tax cannot absorb is not lost: the
    # provincial foreign tax credit (form T2036, the provincial
    # counterpart of ITA s.126(1)) takes the foreign non-business tax
    # paid MINUS the federal credit claimed, limited to the provincial
    # tax otherwise payable x net foreign non-business income / net
    # income. https://www.canada.ca/en/revenue-agency/services/forms-publications/forms/t2036.html
    fed_ftc_unused = max(0.0, fed_ftc - fed_before_ftc)

    # ON428 order: basic = tax - credits (line 62); surtax on basic
    # (line 68); minus DTC (line 70), floored (line 71); the health
    # premium is added at line 89, after every credit.
    prov_gross = _bracket_tax(ti, prov["brackets"])
    prov_bpa = prov["bpa"] * prov["brackets"][0][1]
    basic = max(0.0, prov_gross - prov_bpa)
    surtax_parts = _surtax_parts(basic, prov)
    surtax = sum(amt for _t, _r, amt in surtax_parts)
    prov_dtc = grossed * prov["dtc_eligible"]
    ohp = (ontario_health_premium(ti) if prov.get("health_premium")
           else 0.0)
    prov_before_ftc = max(0.0, basic + surtax - prov_dtc)
    share = (min(1.0, foreign_div / net) if net > 0 and foreign_div > 0
             else 0.0)
    prov_ftc = min(fed_ftc_unused, prov_before_ftc * share)
    # The health premium is added after every credit (ON428 line 89).
    p = prov_before_ftc - prov_ftc + ohp

    return {"federal": fed, "provincial": p, "total": fed + p,
            "detail": {
                "ti": ti, "net_income": net,
                "fed_gross": fed_gross, "fed_bpa": fed_bpa,
                "fed_bpa_amount": fed_bpa_amount,
                "fed_dtc": fed_dtc, "fed_ftc": fed_ftc,
                "fed_ftc_unused": fed_ftc_unused, "prov_ftc": prov_ftc,
                "prov_gross": prov_gross, "prov_bpa": prov_bpa,
                "prov_basic": basic, "surtax_parts": surtax_parts,
                "prov_surtax": surtax,
                "prov_dtc": prov_dtc, "prov_ohp": ohp,
            }}


def _amt_canada(*, realized: float, eligible_div: float,
                foreign_div: float, pil: float, other_income: float,
                other_losses: float, ftc: float, regular_fed: float,
                prov: Dict[str, Any], fed_bpa_amount=None,
                prov_basic: float = 0.0, deductions: float = 0.0,
                carrying_charges: float = 0.0) -> Dict[str, Any]:
    """The post-2024 federal AMT check plus the provincial piggyback.
    Pure arithmetic on figures estimate_canada already holds — nothing
    outside this module learns AMT exists. Always returned (binding or
    not): the headroom number is planning information in itself.
    `pil` here is every ordinary investment amount (PIL + staking)."""
    # Losses deduct at 50% in the AMT base — but only the CLAIMABLE
    # amount, exactly as the regular branch caps them at the year's
    # gains (111(1)(b)). Deducting the whole carryforward POOL let an
    # unused balance that changes nothing in regular tax silently
    # erase a real AMT liability.
    claimable_losses = min(max(0.0, realized), max(0.0, other_losses))
    # Deductions: RRSP/FHSA/RPP-type amounts in full; interest and
    # carrying charges to earn property income (line 22100) at 50%
    # under the post-2024 rules.
    ati = max(0.0,
              max(0.0, realized - CA_AMT_LOSS_ALLOWANCE * claimable_losses)
              + eligible_div + foreign_div + pil + other_income
              - deductions
              - CA_AMT_CARRYING_CHARGE_ALLOWANCE * carrying_charges)
    exemption = ca_amt_exemption()
    base = max(0.0, ati - exemption)
    gross = base * CA_AMT_RATE
    # The BPA claimed on the return (phased down by net income) is the
    # amount the 50% AMT allowance applies to.
    bpa_amount = (CA_FED_BPA if fed_bpa_amount is None
                  else fed_bpa_amount)
    bpa_credit = (bpa_amount * CA_FED_BRACKETS[0][1]
                  * CA_AMT_CREDIT_ALLOWANCE)
    # The special foreign tax credit is allowed IN FULL under the AMT
    # rules — deliberately not subject to the 50% credit haircut that
    # applies to the BPA above.
    fed_min = max(0.0, gross - bpa_credit - ftc)
    excess = max(0.0, fed_min - regular_fed)
    factor = float(prov.get("amt_factor") or 0.0)
    prov_add = excess * factor
    # ON: the surtax is recomputed on basic ON tax PLUS the additional
    # tax (5006-D "Line 72", lines 2-8); BC/AB have no surtax.
    prov_surtax_extra = 0.0
    if prov_add > 0 and prov.get("surtax"):
        prov_surtax_extra = (
            sum(a for _t, _r, a in _surtax_parts(prov_basic + prov_add,
                                                 prov))
            - sum(a for _t, _r, a in _surtax_parts(prov_basic, prov)))
    prov_amt = prov_add + prov_surtax_extra
    return {
        "adjusted_income": round(ati, 2),
        "exemption": round(exemption, 2),
        "rate": CA_AMT_RATE,
        "bpa_credit": round(bpa_credit, 2),
        "minimum_fed": round(fed_min, 2),
        "regular_fed": round(regular_fed, 2),
        "excess_fed": round(excess, 2),
        "provincial_factor": factor,
        "provincial_factor_assumed": bool(prov.get("amt_factor_assumed")),
        "provincial_amt_basic": round(prov_add, 2),
        "provincial_amt_surtax": round(prov_surtax_extra, 2),
        "provincial_amt": round(prov_amt, 2),
        "topup": round(excess + prov_amt, 2),
        "carryforward": round(excess, 2),
        "binding": excess > 0.005,
        "headroom": round(max(0.0, regular_fed - fed_min), 2),
    }


def _canada_notes(prov_key: str, prov: Dict[str, Any],
                  with_inv: Dict[str, Any], base: Dict[str, Any],
                  amt: Dict[str, Any], staking: float) -> List[str]:
    """What the provincial calculation included, in words — the
    printed estimate names the surtax/health premium it applied."""
    from taxjson.lib.report_model import fmt_money as m
    tw, tb = with_inv["detail"], base["detail"]
    notes = []
    extras = []
    if prov.get("surtax"):
        tiers = " + ".join(f"{r * 100:.0f}% over {t:,.0f}"
                           for t, r in prov["surtax"])
        extras.append(f"{prov_key} surtax ({tiers} of basic "
                      f"{prov_key} tax): {m(tw['prov_surtax'])} with "
                      f"investments, {m(tb['prov_surtax'])} on other "
                      f"income alone")
    if prov.get("health_premium"):
        extras.append(f"Ontario Health Premium: "
                      f"{m(tw['prov_ohp'])} with investments, "
                      f"{m(tb['prov_ohp'])} on other income alone "
                      f"(max 900.00 over 200,600 taxable)")
    if extras:
        notes.append("Included in the " + prov_key + " figure — "
                     + "; ".join(extras) + ".")
    else:
        notes.append(f"{prov_key} has no provincial surtax or health "
                     f"premium; none applied.")
    if abs(tw["fed_bpa_amount"] - CA_FED_BPA) > 0.005 \
            or abs(tb["fed_bpa_amount"] - CA_FED_BPA) > 0.005:
        notes.append(
            f"Federal BPA phased down by net income: "
            f"{m(tw['fed_bpa_amount'])} with investments, "
            f"{m(tb['fed_bpa_amount'])} on other income alone (full "
            f"{CA_FED_BPA:,.0f} up to {CA_FED_BRACKETS[2][0]:,.0f}, "
            f"{CA_FED_BPA_MIN:,.0f} from {CA_FED_BRACKETS[3][0]:,.0f}).")
    if amt["binding"] and amt["provincial_factor"]:
        txt = (f"{prov_key} AMT = {amt['provincial_factor'] * 100:.2f}% "
               f"of the federal excess ({m(amt['provincial_amt_basic'])})")
        if amt["provincial_amt_surtax"] > 0.005:
            txt += (f" + {prov_key} surtax on it "
                    f"({m(amt['provincial_amt_surtax'])})")
        notes.append(txt + ".")
    if amt["binding"] and amt["provincial_factor_assumed"]:
        notes.append(f"The {RATE_VINTAGE} {prov_key} minimum-tax factor "
                     f"({amt['provincial_factor'] * 100:.2f}%) is "
                     f"ASSUMED unchanged until the {RATE_VINTAGE} "
                     f"provincial form is published.")
    if tw.get("prov_ftc", 0.0) > 0.005:
        notes.append(f"Foreign tax the federal tax could not absorb "
                     f"({m(tw['fed_ftc_unused'])}) is credited against "
                     f"{prov_key} tax (form T2036): "
                     f"{m(tw['prov_ftc'])}.")
    if not amt["binding"] and amt["headroom"] > 0.005:
        # ITA s.120.2: minimum tax paid in the 7 preceding years is
        # creditable against regular tax above the minimum
        # (T691 Part 8, T1 line 40427, provincial piggyback e.g. ON428
        # line 59). https://laws-lois.justice.gc.ca/eng/acts/I-3.3/section-120.2.html
        notes.append(f"If you paid minimum tax (AMT) in any of the 7 "
                     f"preceding years, its carryover (T691 Part 8, "
                     f"line 40427; ITA s.120.2) can reduce federal tax "
                     f"by up to the "
                     f"{m(amt['headroom'])} headroom, plus the "
                     f"{prov_key} share — not modelled, so the estimate "
                     f"and the instalments overstate the tax by what "
                     f"you can apply.")
    if staking > 0.005:
        notes.append(f"Crypto staking rewards ({m(staking)}) are taxed "
                     f"as ordinary income — no withholding, no foreign "
                     f"tax credit.")
    return notes


CA_ASSUMPTIONS = (
    "Assumes: Canadian-listed dividends are all ELIGIBLE (non-eligible "
    "dividends would be taxed higher); foreign withholding creditable "
    "up to 15%; crypto staking is ordinary income; no QC abatement or "
    "low-income reductions; interest income and interest paid are "
    "not included and no taxjson view totals them — take them from the "
    "broker statements (the rows are listed by `taxjson events`); "
    "deductions below line 15000 only as entered (--deductions, "
    "--carrying-charges); no prior-year minimum tax carryover; the basic "
    "personal amount is the only non-refundable credit (no CPP/EI, "
    "Canada employment, age, pension or donation credits); no OAS "
    "recovery tax; the AMT sees only these books and other income (no "
    "stock-option deduction add-back or donated securities).")


def estimate_canada(*, realized: float, eligible_div: float,
                    year=None,
                    foreign_div: float, pil: float,
                    other_income: float, other_losses: float,
                    province: str,
                    actual_withheld=None,
                    staking: float = 0.0,
                    deductions: float = 0.0,
                    carrying_charges: float = 0.0) -> Dict[str, Any]:
    """`deductions`: amounts deducted at lines 20700-23500 that the
    AMT allows in full (RRSP 20800, FHSA 20805, RPP 20700, ...);
    `carrying_charges`: line 22100 interest and carrying charges,
    allowed at 50% in the post-2024 AMT base. Both lower net and
    taxable income, other income first."""
    import math
    for _n, _v in (("deductions", deductions),
                   ("carrying_charges", carrying_charges)):
        if not math.isfinite(float(_v)) or float(_v) < 0:
            raise ValueError(f"{_n} must be a non-negative finite "
                             f"amount, got {_v!r}")
    deductions = float(deductions)
    carrying_charges = float(carrying_charges)
    ded_total = deductions + carrying_charges
    pick = apply_vintage(year)
    prov_key = province.strip().upper()
    if prov_key not in CA_PROVINCES:
        raise ValueError(
            f"unsupported province {province!r} for the estimate "
            f"(supported: {', '.join(sorted(CA_PROVINCES))})")
    prov = CA_PROVINCES[prov_key]

    net_gain = realized - other_losses
    losses_unused = max(0.0, -net_gain)
    taxable_gain = max(0.0, net_gain) * CA_INCLUSION
    losses_applied = min(max(0.0, realized), max(0.0, other_losses))
    # Staking rewards are ordinary income (like PIL): no gross-up,
    # no withholding, no FTC.
    staking = max(0.0, float(staking or 0.0))

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
    # Net income (line 23600) = taxable income + the carryforward
    # losses deducted below it at line 25300 (x50%).
    grossed = eligible_div * CA_ELIGIBLE_GROSSUP
    net_income = (other_income + pil + staking - ded_total
                  + taxable_gain + foreign_div + grossed
                  + CA_INCLUSION * losses_applied)
    with_inv = _canada_tax(other_income + pil + staking - ded_total,
                           taxable_gain, eligible_div, foreign_div, prov,
                           ftc=ftc, net_income=net_income)
    base = _canada_tax(other_income - ded_total, 0.0, 0.0, 0.0, prov)
    # Signed, like the US branch: eligible dividends at a low bracket
    # carry a negative marginal rate (the DTC exceeds the tax on the
    # grossed-up amount), so the investment income can LOWER the tax on
    # the other income. Flooring at 0 printed "WITH - BASE = 0.00" under
    # a negative difference (R1-47).
    est = with_inv["total"] - base["total"]
    inv_income = realized + eligible_div + foreign_div + pil + staking

    def _totals(d):
        return {k: round(d[k], 2) for k in ("federal", "provincial",
                                            "total")}
    amt = _amt_canada(realized=realized, eligible_div=eligible_div,
                      foreign_div=foreign_div, pil=pil + staking,
                      other_income=other_income,
                      other_losses=other_losses, ftc=ftc,
                      regular_fed=with_inv["federal"], prov=prov,
                      fed_bpa_amount=with_inv["detail"]["fed_bpa_amount"],
                      prov_basic=with_inv["detail"]["prov_basic"],
                      deductions=deductions,
                      carrying_charges=carrying_charges)
    notes = (vintage_notes(year, pick)
             + _canada_notes(prov_key, prov, with_inv, base, amt,
                             staking))
    return {
        "country": "canada", "province": prov_key,
        "amt": amt,
        "estimated_tax_with_amt": round(est + amt["topup"], 2),
        "vintage": RATE_VINTAGE,
        "notes": notes,
        "assumptions": CA_ASSUMPTIONS,
        "taxable_gain": round(taxable_gain, 2),
        "losses_applied": round(losses_applied, 2),
        "losses_unused": round(losses_unused, 2),
        "grossed_eligible": round(grossed, 2),
        "staking": round(staking, 2),
        "deductions": round(deductions, 2),
        "carrying_charges": round(carrying_charges, 2),
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
    pick = apply_vintage(year)
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

    # Form 8960 line 5a takes the capital gain or LOSS from Form 1040
    # line 7, which in a net-loss year is the (up to) $3,000 s.1211(b)
    # deduction — it reduces net investment income too (Reg.
    # 1.1411-4(d)); clamping the nets at 0 and stopping there overstated
    # NIIT by up to 3.8% x 3,000 (S077-24, S078-02).
    inv_income_for_niit = (st_net + lt_net + qualified_div + pil
                           - ordinary_offset)
    magi = other_income + inv_ordinary + inv_pref
    niit = US_NIIT_RATE * min(
        max(0.0, inv_income_for_niit),
        max(0.0, magi - US_NIIT_MAGI_THRESHOLD))

    # The carryforward counts as USED only the part of the offset the
    # year's taxable income absorbs: Capital Loss Carryover Worksheet
    # line 4 = min(line 7 loss, max(0, taxable income + that loss)),
    # taxable income = AGI - standard deduction, possibly negative
    # (IRC s.1212(b)(2); S078-01: 7,000 carried instead of 10,000 at
    # zero other income).
    taxable_income = magi - US_STD_DEDUCTION
    offset_used = min(ordinary_offset,
                      max(0.0, taxable_income + ordinary_offset))
    # Signed: a net-loss year (the $3,000 ordinary offset) legitimately
    # REDUCES tax vs the base — report the saving as a negative estimate.
    est = with_inv["total"] - base["total"] + niit
    inv_income = st + lt + qualified_div + pil

    def _totals(d):
        return {k: round(d[k], 2) for k in ("ordinary", "preferential",
                                            "total")}
    return {
        "country": "usa", "vintage": RATE_VINTAGE,
        "notes": vintage_notes(year, pick, country="usa"),
        "st_net": round(st_net, 2), "lt_net": round(lt_net, 2),
        "ordinary_offset": round(ordinary_offset, 2),
        "offset_used_for_carryover": round(offset_used, 2),
        "losses_unused": round(loss + max(0.0, -net_cap)
                               - offset_used, 2),
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
