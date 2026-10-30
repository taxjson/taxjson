"""Marginal tax ESTIMATION for `taxjson sum --other-income/--other-losses`.

Incremental method: estimated tax attributable to the year's investment
income = tax(other_income + investment income) - tax(other_income).
Stacking the investment income on top of the user's other income is
what makes the bracket placement honest.

ESTIMATES ONLY — never filing numbers. Simplifications are deliberate
and disclosed by the caller:
  - Canada: all Canadian issuers' dividends treated as ELIGIBLE
    (38% gross-up + federal/provincial DTC; non-eligible dividends are
    NOT modelled); foreign dividends are ordinary income with a 15%
    treaty-withholding FTC assumed already paid (actual TAX rows when
    the books carry them, each security's capped at 15% of its own
    dividends), credited federally up to the T2209 limit
    and the rest provincially up to the T2036 one; the AMT takes the
    s.127.54 special foreign tax credit; crypto STAKING rewards are ordinary income
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
    (stack with LT); PIL and staking ordinary; --other-losses is the
    short-term carryover and --long-term-losses the long-term one, each
    netted in its own column, then across, excess offsets up to $3,000
    of ordinary income; the net of §1256 contracts 40% short-term and
    60% long-term (Form 6781; no year-end marking, no loss carryback);
    NIIT 3.8% over $200k MAGI; no state tax.

`_VINTAGES` holds one table set per published tax year;
`apply_vintage(year)` selects by project year and the printed vintage
discloses the pick (with an explicit note when it is not the requested
year) — add a new vintage annually.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

RATE_VINTAGE = "2025"

_INF = float("inf")


def fmt_pct(rate: float) -> str:
    """A rate as the printed percentage, no float noise and no trailing
    zeros: .150198 -> '15.0198%', .5 -> '50%', .038 -> '3.8%'. Every
    printed rate (the estimate, tax-logic) goes through it, so the text
    follows the constant (one source)."""
    s = f"{rate * 100:.4f}".rstrip("0").rstrip(".")
    return f"{s}%"


def fmt_dollars(amount: float) -> str:
    """'$55,867' (whole dollars, the tables' own precision)."""
    return f"${amount:,.0f}"

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
# Form T2036 note (4), the "provincial tax otherwise payable" its credit
# is limited by (2025 forms): Ontario's is ON428 line 81 worked with
# lines 70 (its dividend tax credit) and 72 at 0, Alberta's AB428 line 63
# (its dividend tax credit) plus line 68 — both before the dividend tax
# credit; British Columbia's is BC428 line 68, after it.
# https://www.canada.ca/content/dam/cra-arc/formspubs/pbg/t2036/t2036-25e.pdf
CA_T2036_DTC_ADDED_BACK = frozenset({"ON", "AB"})
# A year the minimum tax applies (T691 Part 5 line 11 above 0), each
# province's additional tax for minimum tax purposes is its factor of a
# different federal figure (2025 forms): Ontario's of T691 Part 5 line
# 11, the federal excess (Worksheet ON428 line 72); Alberta's of T691
# Part 7 line 1 minus line 2, the minimum amount over basic federal tax,
# before either foreign tax credit (AB428 line 67); British Columbia's
# of T691 Part 7 line 14, the additional tax of s.120.2(3) (BC428 line
# 69). Without a special foreign tax credit above the regular one the
# three are the same figure.
# https://www.canada.ca/content/dam/cra-arc/formspubs/pbg/5006-d/5006-d-25e.pdf
# https://www.canada.ca/content/dam/cra-arc/formspubs/pbg/5009-c/5009-c-25e.pdf
# https://www.canada.ca/content/dam/cra-arc/formspubs/pbg/5010-c/5010-c-25e.pdf
CA_PROV_AMT_BASE = {"ON": "excess", "AB": "before_ftc", "BC": "s120_2"}
# Form T2036 line 2 in a minimum-tax year (note 1): Ontario and Alberta
# enter the special foreign tax credit (T691 Part 4 line 11; Ontario pro
# rata to the non-business taxes, Alberta its non-business part: all of
# it when every foreign tax is non-business tax); British Columbia
# still enters the regular federal credit (T2209 line 3). Alberta's tax
# otherwise payable (note 4: AB428 line 63 plus line 68) includes its
# additional tax for minimum tax purposes; Ontario's (line 81 with line
# 72 at 0) and British Columbia's exclude it. All three deduct the
# credit from a tax that includes it.
CA_T2036_AMT_LINE2_SFTC = frozenset({"ON", "AB"})
CA_T2036_AMT_IN_OTHERWISE = frozenset({"AB"})


def ca_prov_amt_base_text(amt: Dict[str, Any]) -> str:
    """The federal figure a province's minimum-tax factor applies to,
    in words with its form line (`amt`: an `_amt_canada` result)."""
    kind = amt.get("provincial_amt_base_kind") or "excess"
    if kind == "before_ftc":
        return ("the minimum amount over basic federal tax, before the "
                "foreign tax credits (T691 Part 7 line 1 minus line 2; "
                "AB428 line 67)")
    if kind == "s120_2":
        return ("the federal additional tax of s.120.2(3) (T691 Part 7 "
                "line 14; BC428 line 69)")
    return "the federal excess"


def ca_t2036_amt_text(prov_key: Optional[str]) -> str:
    """How form T2036 differs in a minimum-tax year, for `prov_key`."""
    pk = (prov_key or "").upper()
    if pk == "ON":
        return ("line 2 is the special credit; the credit comes off ON "
                "tax with its additional tax for minimum tax purposes")
    if pk == "AB":
        return ("line 2 is the special credit; AB tax otherwise payable "
                "includes its additional tax for minimum tax purposes")
    if pk == "BC":
        return ("line 2 is still the regular federal credit (T2209 line "
                "3); the credit comes off BC tax with its additional tax")
    return "as in a regular year"

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
# The special foreign tax credit (ITA s.127.54(2)): the greater of the
# regular credit (s.126) and the lesser of the "foreign taxes" — 2/3 of
# the non-business-income tax paid (s.127.54(1)) — and 20.5% (A) of the
# foreign income (B). https://laws-lois.justice.gc.ca/eng/acts/I-3.3/section-127.54.html
CA_AMT_SFTC_TAX_SHARE = 2 / 3
CA_AMT_SFTC_RATE = .205


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
# IRC §1256(a)(3): a §1256 contract's gain or loss is 60% long-term and
# 40% short-term, whatever the holding period (Form 6781 lines 8 and 9).
US_1256_LONG_TERM_SHARE = .60


def section_1256_split(net: float) -> Tuple[float, float]:
    """(short-term, long-term) parts of a year's net §1256 gain or loss
    (Form 6781 line 7): 40% to Schedule D line 4 (line 8) and 60% to
    line 11 (line 9), whatever each contract's holding period (IRC
    §1256(a)(3); tax-logic US-FUT-03). The two add up to `net`. Year-end
    marking to market and the §1212(c) loss carryback are not modelled
    (US-FUT-02)."""
    net = float(net or 0.0)
    lt = net * US_1256_LONG_TERM_SHARE
    return net - lt, lt


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
                net_income=None, fed_mtc: float = 0.0,
                prov_mtc: float = 0.0,
                prov_key: Optional[str] = None) -> Dict[str, Any]:
    """Federal + provincial tax for one income mix (all components
    already in their taxed form except the eligible gross-up applied
    here). Floors at zero per jurisdiction. `net_income` (line 23600)
    drives the federal BPA phase-down; it defaults to taxable income
    and differs only by carryforward losses deducted at line 25300.
    `ftc`: the foreign non-business income tax paid on `foreign_div`
    (form T2209 line 1, at most 15% of it); None = 15% assumed.
    `fed_mtc` / `prov_mtc`: a minimum tax carryover applied this year
    (ITA s.120.2) — federal Schedule 1 line 40427, deducted with the
    dividend tax credit before the foreign tax credit; the province's
    share is deducted from basic provincial tax before its surtax
    (tax-logic CA-AMT-04/05). `prov_key` (ON/BC/AB) picks the
    province's T2036 "tax otherwise payable". Returns totals PLUS the
    per-step detail the --verbose trace prints."""
    grossed = eligible_div * CA_ELIGIBLE_GROSSUP
    # Deductions enter `ordinary` as a negative; income never goes
    # below zero (a return does not carry a negative net income).
    ti = max(0.0, ordinary + taxable_gain + foreign_div + grossed)
    net = ti if net_income is None else max(0.0, net_income)

    fed_gross = _bracket_tax(ti, CA_FED_BRACKETS)
    fed_bpa_amount = ca_fed_bpa(net)
    fed_bpa = fed_bpa_amount * CA_FED_BRACKETS[0][1]
    fed_dtc = grossed * CA_FED_DTC_ELIGIBLE
    foreign_tax = max(0.0, ftc if ftc is not None
                      else foreign_div * CA_FOREIGN_WITHHOLDING)
    # Line 42900: after the credits, the dividend tax credit and a
    # minimum tax carryover.
    fed_before_ftc = max(0.0, fed_gross - fed_bpa - fed_dtc
                         - max(0.0, fed_mtc))
    # Form T2209 (ITA s.126(1)(b)): the federal credit is the lesser of
    # the foreign tax and basic federal tax (line 42900 plus the
    # dividend tax credit) x net foreign non-business income / net
    # income, and at most line 42900. Its net income is line 23600
    # less the net capital losses of other years (line 25300): the
    # taxable income here. The foreign share is at most 1 (a foreign
    # income above net income counts as net income). All foreign
    # dividends are one country's. Taking the whole foreign tax
    # against all federal tax credited a low earner's 15% in full
    # (backlog TJ-0367).
    # https://www.canada.ca/content/dam/cra-arc/formspubs/pbg/t2209/t2209-25e.pdf
    share = (min(1.0, foreign_div / ti) if ti > 0 and foreign_div > 0
             else 0.0)
    fed_ftc_limit = (fed_before_ftc + fed_dtc) * share
    fed_ftc = min(foreign_tax, fed_ftc_limit, fed_before_ftc)
    fed = fed_before_ftc - fed_ftc
    # The foreign tax the federal credit does not take goes to the
    # provincial foreign tax credit (form T2036, line 3: T2209 line 1
    # minus line 3), limited to the provincial tax otherwise payable x
    # the same share (line 4) and to the provincial tax itself.
    # https://www.canada.ca/en/revenue-agency/services/forms-publications/forms/t2036.html
    fed_ftc_unused = max(0.0, foreign_tax - fed_ftc)

    # ON428 order: basic = tax - credits (line 62); surtax on basic
    # (line 68); minus DTC (line 70), floored (line 71); the health
    # premium is added at line 89, after every credit.
    prov_gross = _bracket_tax(ti, prov["brackets"])
    prov_bpa = prov["bpa"] * prov["brackets"][0][1]
    basic = max(0.0, prov_gross - prov_bpa - max(0.0, prov_mtc))
    surtax_parts = _surtax_parts(basic, prov)
    surtax = sum(amt for _t, _r, amt in surtax_parts)
    prov_dtc = grossed * prov["dtc_eligible"]
    ohp = (ontario_health_premium(ti) if prov.get("health_premium")
           else 0.0)
    prov_before_ftc = max(0.0, basic + surtax - prov_dtc)
    # T2036 note (4): ON and AB before their dividend tax credit. ON's
    # is line 81 with line 70 at 0: basic tax plus surtax, never the
    # dividend tax credit itself (the same credit in a regular year,
    # where the credit is limited to the tax after the dividend tax
    # credit; not in a minimum-tax year, where line 72 is added).
    _pk = (prov_key or "").upper()
    if _pk == "ON":
        prov_otherwise = basic + surtax
    else:
        prov_otherwise = prov_before_ftc + (
            prov_dtc if _pk in CA_T2036_DTC_ADDED_BACK else 0.0)
    prov_ftc_limit = prov_otherwise * share
    prov_ftc = min(fed_ftc_unused, prov_ftc_limit, prov_before_ftc)
    # The health premium is added after every credit (ON428 line 89).
    p = prov_before_ftc - prov_ftc + ohp

    return {"federal": fed, "provincial": p, "total": fed + p,
            "detail": {
                "ti": ti, "net_income": net,
                "fed_gross": fed_gross, "fed_bpa": fed_bpa,
                "fed_bpa_amount": fed_bpa_amount,
                "fed_dtc": fed_dtc, "foreign_tax": foreign_tax,
                "ftc_share": share, "fed_ftc_limit": fed_ftc_limit,
                "fed_ftc": fed_ftc,
                "fed_ftc_unused": fed_ftc_unused,
                "prov_before_ftc": prov_before_ftc,
                "prov_otherwise": prov_otherwise,
                "prov_ftc_limit": prov_ftc_limit, "prov_ftc": prov_ftc,
                "prov_gross": prov_gross, "prov_bpa": prov_bpa,
                "prov_basic": basic, "surtax_parts": surtax_parts,
                "prov_surtax": surtax,
                "prov_dtc": prov_dtc, "prov_ohp": ohp,
                "fed_mtc": max(0.0, fed_mtc),
                "prov_mtc": max(0.0, prov_mtc),
            }}


def _amt_canada(*, realized: float, eligible_div: float,
                foreign_div: float, pil: float, other_income: float,
                other_losses: float, ftc: float, regular_fed: float,
                prov: Dict[str, Any], fed_bpa_amount=None,
                prov_basic: float = 0.0, deductions: float = 0.0,
                carrying_charges: float = 0.0,
                regular_ftc: float = 0.0,
                prov_key: Optional[str] = None,
                regular: Optional[Dict[str, Any]] = None
                ) -> Dict[str, Any]:
    """The post-2024 federal AMT check plus the provincial piggyback.
    Pure arithmetic on figures estimate_canada already holds — nothing
    outside this module learns AMT exists. Always returned (binding or
    not): the headroom number is planning information in itself.
    `pil` here is every ordinary investment amount (PIL + staking).
    `ftc`: the foreign non-business income tax paid on `foreign_div`
    (T2209 line 1); `regular_ftc`: the federal foreign tax credit of
    the regular tax (s.126, T2209 line 3). `prov_key` (ON/BC/AB) picks
    the province's additional tax for minimum tax purposes and its
    T2036 in a minimum-tax year (None: the federal excess, the regular
    T2036); `regular`: the regular tax's detail (`_canada_tax`), whose
    provincial tax and foreign tax credit the minimum-tax year's are
    compared with (tax-logic CA-EST-AMT, CA-EST-FTC).
    `ati_lines` is the adjusted taxable income line by line (ITA
    s.127.52), the breakdown `taxjson amt` prints — the SAME figures
    the total is built from (tax-logic CA-AMT-01)."""
    # Losses deduct at 50% in the AMT base — but only the CLAIMABLE
    # amount, exactly as the regular branch caps them at the year's
    # gains (111(1)(b)). Deducting the whole carryforward POOL let an
    # unused balance that changes nothing in regular tax silently
    # erase a real AMT liability.
    claimable_losses = min(max(0.0, realized), max(0.0, other_losses))
    loss_allowed = CA_AMT_LOSS_ALLOWANCE * claimable_losses
    gains_net = max(0.0, realized - loss_allowed)
    cc_allowed = CA_AMT_CARRYING_CHARGE_ALLOWANCE * carrying_charges
    # Deductions: RRSP/FHSA/RPP-type amounts in full; interest and
    # carrying charges to earn property income (line 22100) at 50%
    # under the post-2024 rules.
    ati = max(0.0,
              gains_net
              + eligible_div + foreign_div + pil + other_income
              - deductions
              - cc_allowed)
    exemption = ca_amt_exemption()
    base = max(0.0, ati - exemption)
    gross = base * CA_AMT_RATE
    # The BPA claimed on the return (phased down by net income) is the
    # amount the 50% AMT allowance applies to.
    bpa_amount = (CA_FED_BPA if fed_bpa_amount is None
                  else fed_bpa_amount)
    bpa_credit = (bpa_amount * CA_FED_BRACKETS[0][1]
                  * CA_AMT_CREDIT_ALLOWANCE)
    # The special foreign tax credit (s.127.54(2)): the greater of the
    # regular credit and the lesser of 2/3 of the foreign tax and 20.5%
    # of the foreign income. Not the 50% allowance of the BPA above —
    # but not the whole withholding either: allowing it "in full" took
    # 100% of the foreign tax, so the minimum tax and the carryover it
    # creates came out low (backlog TJ-0368).
    sftc_taxes = CA_AMT_SFTC_TAX_SHARE * max(0.0, ftc)
    sftc_income = CA_AMT_SFTC_RATE * max(0.0, foreign_div)
    sftc = max(max(0.0, regular_ftc), min(sftc_taxes, sftc_income))
    # T691 Part 1 line 103 (the minimum amount, before the special
    # credit) and Part 5 line 3 (net minimum tax payable).
    minimum_amount = max(0.0, gross - bpa_credit)
    fed_min = max(0.0, minimum_amount - sftc)
    excess = max(0.0, fed_min - regular_fed)
    binding = excess > 0.005
    # Basic federal tax (T691 Part 2 line 7): the regular federal tax
    # before its foreign tax credit.
    basic_fed = max(0.0, regular_fed) + max(0.0, regular_ftc)
    # s.120.2(3) (T691 Part 7 line 14): the minimum amount less basic
    # federal tax and the 2/3 share (foreign taxes of s.127.54(1) over
    # the foreign tax paid) of the special credit above the regular
    # one. Only a year the minimum tax applies has one. It is the
    # minimum tax carryover the year creates (T691 Part 8 line 14),
    # which the excess is only when the special credit is the regular
    # one: the excess takes that credit off in full (backlog TJ-0626).
    # https://laws-lois.justice.gc.ca/eng/acts/I-3.3/section-120.2.html
    sftc_over = max(0.0, sftc - max(0.0, regular_ftc))
    ratio = CA_AMT_SFTC_TAX_SHARE if ftc > 0 else 0.0
    sftc_part = sftc_over * ratio if binding else 0.0
    s120_2 = (max(0.0, minimum_amount - basic_fed - sftc_part)
              if binding else 0.0)
    before_ftc = (max(0.0, minimum_amount - basic_fed) if binding
                  else 0.0)
    pk = (prov_key or "").upper()
    base_kind = CA_PROV_AMT_BASE.get(pk, "excess")
    prov_base = {"excess": excess, "before_ftc": before_ftc,
                 "s120_2": s120_2}[base_kind]
    factor = float(prov.get("amt_factor") or 0.0)
    prov_add = prov_base * factor
    # ON: the surtax is recomputed on basic ON tax PLUS the additional
    # tax (5006-D "Line 72", lines 2-8); BC/AB have no surtax.
    prov_surtax_extra = 0.0
    if prov_add > 0 and prov.get("surtax"):
        prov_surtax_extra = (
            sum(a for _t, _r, a in _surtax_parts(prov_basic + prov_add,
                                                 prov))
            - sum(a for _t, _r, a in _surtax_parts(prov_basic, prov)))
    prov_amt = prov_add + prov_surtax_extra
    # Form T2036 in a year the minimum tax applies (note 1, note 4):
    # line 2 is the special credit for ON and AB, still T2209 line 3
    # for BC; AB's tax otherwise payable includes its additional tax;
    # the credit comes off a provincial tax that includes it. The
    # regular credit started from the regular federal one and was
    # never recomputed (backlog TJ-0591).
    rg = regular or {}
    prov_ftc_regular = max(0.0, float(rg.get("prov_ftc") or 0.0))
    prov_ftc_min = prov_ftc_regular
    if binding and pk in CA_PROV_AMT_BASE and ftc > 0 and rg:
        line2 = (sftc if pk in CA_T2036_AMT_LINE2_SFTC
                 else max(0.0, regular_ftc))
        line3 = max(0.0, ftc - line2)
        otherwise = max(0.0, float(rg.get("prov_otherwise") or 0.0))
        if pk in CA_T2036_AMT_IN_OTHERWISE:
            otherwise += prov_amt
        line4 = otherwise * float(rg.get("ftc_share") or 0.0)
        payable = (max(0.0, float(rg.get("prov_before_ftc") or 0.0))
                   + prov_amt)
        prov_ftc_min = min(line3, line4, otherwise, payable)
    # More provincial tax than the regular year's credit leaves: a
    # positive change adds to the top-up, a negative one takes off it.
    prov_ftc_change = prov_ftc_regular - prov_ftc_min
    # The foreign tax neither credit takes on the return: the minimum-tax
    # year's credits (the special one and T2036's) when AMT binds, else
    # the regular year's. It may be deductible on line 23200 (s.20(12));
    # the estimate does not deduct it (backlog TJ-0592).
    if binding:
        not_credited = max(0.0, ftc - sftc - prov_ftc_min)
    else:
        not_credited = max(0.0, ftc - max(0.0, regular_ftc)
                           - prov_ftc_regular)
    # (label, amount, reference) — signed as they enter the total
    # (before its floor at zero). A net capital loss of the year enters
    # as zero: it is not deductible from other income in either base.
    lines = [
        ("Capital gains at 100% (regular: 50%)", max(0.0, realized),
         "s.127.52(1)(d)"),
        ("Net capital losses of other years at 50%", -loss_allowed,
         "s.127.52(1); regular line 25300"),
        ("Eligible dividends, actual amount (no gross-up)",
         eligible_div, "s.127.52(1)"),
        ("Foreign dividends", foreign_div, ""),
        ("Payments in lieu and staking (ordinary)", pil, ""),
        ("Other income", other_income, "[estimate] other_income"),
        ("Deductions in full (RRSP, FHSA, RPP ...)", -deductions,
         "lines 20700-23500"),
        ("Carrying charges at 50%", -cc_allowed,
         "s.127.52(1); line 22100"),
    ]
    return {
        "adjusted_income": round(ati, 2),
        "ati_lines": [{"label": lb, "amount": round(float(a), 2) + 0.0,
                       "ref": ref}
                      for lb, a, ref in lines],
        "exemption": round(exemption, 2),
        "subject": round(base, 2),
        "rate": CA_AMT_RATE,
        "gross_minimum": round(gross, 2),
        "bpa_amount": round(bpa_amount, 2),
        "bpa_credit": round(bpa_credit, 2),
        "minimum_amount": round(minimum_amount, 2),
        "ftc": round(sftc, 2),
        "ftc_regular": round(max(0.0, regular_ftc), 2),
        "ftc_foreign_taxes": round(sftc_taxes, 2),
        "ftc_income_limit": round(sftc_income, 2),
        "minimum_fed": round(fed_min, 2),
        "regular_fed": round(regular_fed, 2),
        "basic_fed": round(basic_fed, 2),
        "excess_fed": round(excess, 2),
        # s.120.2(3), T691 Part 7 line 14 (BC's base, and the minimum
        # tax carryover the year creates), and the part of the special
        # credit above the regular one it takes off (Part 7 line 12).
        "additional_tax_s120_2": round(s120_2, 2),
        "additional_tax_sftc_part": round(sftc_part, 2),
        "provincial_factor": factor,
        "provincial_factor_assumed": bool(prov.get("amt_factor_assumed")),
        # The federal figure the factor applies to and which one it is
        # (excess: T691 Part 5 line 11; before_ftc: Part 7 line 1 minus
        # line 2; s120_2: Part 7 line 14).
        "provincial_amt_base": round(prov_base, 2),
        "provincial_amt_base_kind": base_kind,
        "provincial_amt_basic": round(prov_add, 2),
        "provincial_amt_surtax": round(prov_surtax_extra, 2),
        "provincial_amt": round(prov_amt, 2),
        # Form T2036 in the regular year and in the minimum-tax year,
        # and what the difference adds to the top-up.
        "provincial_ftc_regular": round(prov_ftc_regular, 2),
        "provincial_ftc_min_tax": round(prov_ftc_min, 2),
        "provincial_ftc_change": round(prov_ftc_change, 2) + 0.0,
        # The foreign tax no credit takes (backlog TJ-0592): the
        # minimum-tax year's when AMT binds.
        "foreign_tax_not_credited": round(not_credited, 2) + 0.0,
        "topup": round(excess + prov_amt + prov_ftc_change, 2),
        # The minimum tax carryover the year creates: its additional tax
        # of s.120.2(3), not the excess (backlog TJ-0626).
        "carryforward": round(s120_2, 2),
        "binding": binding,
        "headroom": round(max(0.0, regular_fed - fed_min), 2),
        # What a minimum tax carryover can take this year (s.120.2(1)(b)):
        # the federal tax before the deductions of s.120.2, s.126 and
        # s.127 (basic federal tax, before the foreign tax credit) less
        # the minimum amount of s.127.51 (before the special credit).
        # The headroom above, after both foreign tax credits, let a
        # carryover lapse that the law allows (backlog TJ-0719).
        "recovery_limit": round(max(0.0, basic_fed - minimum_amount), 2),
    }


# ---- Minimum tax carryover (ITA s.120.2) ------------------------------
# The federal "additional tax" of a year (minimum tax over regular tax,
# s.120.2(3)) carries forward 7 years and is deducted (T1 Schedule 1
# line 40427, T691 Part 8) oldest year first, limited to the year's
# regular tax above its minimum tax.
CA_AMT_CARRY_YEARS = 7


def ca_amt_carryover(year, available: Optional[Dict[Any, float]],
                     headroom: float,
                     claim: Optional[float] = None) -> Dict[str, Any]:
    """Apply a minimum tax carryover to tax year `year`. `available`:
    {year of origin: unapplied amount} as on the notice / T691;
    `headroom`: the limit of s.120.2(1)(b) for `year`, `_amt_canada`'s
    `recovery_limit`: basic federal tax (before the foreign tax credit)
    minus the minimum amount of s.127.51 (0 when AMT binds). Origins
    older than `year` - 7 have expired (dropped, listed); an origin of `year` or later is refused
    (ValueError) — a carryover comes from an EARLIER year. Recovery
    takes the oldest origins first (tax-logic CA-AMT-03/04). `claim`:
    the amount worth claiming, when less than the limit (s.120.2(1)
    allows what is claimed, up to the limit; `_useful_claim`): an origin
    in its last usable year is still claimed up to the limit, since it
    lapses otherwise. Amounts in, cents out; `remaining_by_year` keeps
    the origins still open after this year (an origin of `year` - 7 is
    used up or gone)."""
    y = int(year)
    usable: Dict[int, float] = {}
    expired: Dict[int, float] = {}
    for origin, amount in (available or {}).items():
        o = int(origin)
        a = float(amount)
        if a <= 0.005:
            continue
        if o >= y:
            raise ValueError(
                f"a minimum tax carryover from {o} cannot be applied in "
                f"{y}: it carries forward from an EARLIER year (the "
                f"year of origin is the year the minimum tax was paid)")
        if o < y - CA_AMT_CARRY_YEARS:
            expired[o] = expired.get(o, 0.0) + a
        else:
            usable[o] = usable.get(o, 0.0) + a
    room = max(0.0, float(headroom))
    if claim is not None:
        last = sum(a for o, a in usable.items()
                   if o <= y - CA_AMT_CARRY_YEARS)
        room = min(room, max(0.0, float(claim), last))
    recovered: Dict[int, float] = {}
    for o in sorted(usable):
        take = min(usable[o], room)
        if take > 0.005:
            recovered[o] = take
            room -= take
    remaining = {o: usable[o] - recovered.get(o, 0.0) for o in usable}
    lapsing = {o: a for o, a in remaining.items()
               if o <= y - CA_AMT_CARRY_YEARS and a > 0.005}
    open_after = {o: a for o, a in remaining.items()
                  if o > y - CA_AMT_CARRY_YEARS and a > 0.005}

    def _c(d):
        return {str(k): round(float(v), 2) for k, v in sorted(d.items())}
    return {
        "year": y,
        "available_by_year": _c(usable),
        "last_year_by_origin": {str(o): o + CA_AMT_CARRY_YEARS
                                for o in sorted(usable)},
        "available": round(float(sum(usable.values())), 2),
        "expired_by_year": _c(expired),
        "limit": round(max(0.0, float(headroom)), 2),
        "recovered_by_year": _c(recovered),
        "recovered_federal": round(float(sum(recovered.values())), 2),
        "lapsing_by_year": _c(lapsing),
        "remaining_by_year": _c(open_after),
    }


def _useful_claim(tax_after, most: float) -> float:
    """The smallest claim (to the cent, up) of at most `most` that
    gives the tax `most` gives. `tax_after(claim)` is the total tax:
    once the foreign tax credit takes all of the federal tax, a further
    dollar of carryover only replaces a dollar of that credit, so it
    gives up carryover for no saving (backlog TJ-1030)."""
    if most <= 0.005:
        return most
    tol = 1e-6
    floor = tax_after(most)
    if tax_after(0.0) <= floor + tol:
        return 0.0
    lo, hi = 0.0, most
    while hi - lo > 0.001:
        mid = (lo + hi) / 2.0
        if tax_after(mid) <= floor + tol:
            hi = mid
        else:
            lo = mid
    return min(most, math.ceil(round(hi * 100.0, 6)) / 100.0)


def ca_withholding_limit(entries) -> Dict[str, Any]:
    """The foreign tax form T2209 counts when the books pair each
    withholding with the property it was withheld on: ITA s.126(7)'s
    non-business-income tax leaves out the tax s.20(11) makes
    deductible, the tax on income from a property above 15% of that
    property's income. Each entry's withholding (its TAX rows, net and
    at least 0, plus the 15% assumed for an account whose books carry no
    TAX row) counts up to 15% of its own foreign dividends; the rest is
    above the limit (backlog TJ-0638; tax-logic CA-EST-FTC).

    `entries`: [{"security", "dividends", "withheld", "assumed"}] — a
    security's dividends and withholding across the taxable accounts,
    or, with "security" empty and its "account", the TAX rows of an
    account the books cannot pair with a security (no symbol, or one
    with no foreign dividend in the year), whose "dividends" are the
    account's foreign cash dividends none of its TAX rows names (within
    each security's room the paired withholding leaves). Returns the
    foreign tax counted (`creditable`), its assumed part, the
    withholding above the limit (`over_cap`, by security in
    `over_by_security`) and the unpaired TAX rows: withheld, counted,
    their accounts."""
    creditable = assumed = over = 0.0
    over_by: Dict[str, float] = {}
    unpaired = unpaired_counted = 0.0
    unpaired_accts: List[str] = []
    for e in entries:
        cap = CA_FOREIGN_WITHHOLDING * max(0.0, float(
            e.get("dividends") or 0.0))
        a = max(0.0, float(e.get("assumed") or 0.0))
        w = max(0.0, float(e.get("withheld") or 0.0)) + a
        cred = min(w, cap)
        creditable += cred
        assumed += min(a, cred)
        o = w - cred
        over += o
        sym = str(e.get("security") or "")
        if sym:
            if o > 0.005:
                over_by[sym] = over_by.get(sym, 0.0) + o
        elif w > 0.005:
            unpaired += w
            unpaired_counted += cred
            acct = str(e.get("account") or "")
            if acct and acct not in unpaired_accts:
                unpaired_accts.append(acct)
    return {"creditable": creditable, "assumed": assumed,
            "over_cap": over, "over_by_security": over_by,
            "unpaired_withheld": unpaired,
            "unpaired_counted": unpaired_counted,
            "unpaired_accounts": unpaired_accts}


def _over_cap_parts(over_by: Dict[str, float], unpaired_over: float,
                    shown: int = 3) -> str:
    """'ZZQ.US 150.00, TAX rows with no security 10.00': the largest
    amounts above the limit, then how many more (`--json` lists each)."""
    from taxjson.lib.report_model import fmt_money as m
    items = sorted(over_by.items(), key=lambda kv: (-kv[1], kv[0]))
    parts = [f"{k} {m(v)}" for k, v in items[:shown]]
    if len(items) > shown:
        parts.append(f"{len(items) - shown} more")
    if unpaired_over > 0.005:
        parts.append(f"TAX rows with no security {m(unpaired_over)}")
    return ", ".join(parts)


def ca_foreign_deduction_text(not_credited: float, over_cap: float,
                              limit: Optional[Dict[str, Any]] = None
                              ) -> str:
    """The foreign tax no credit takes and the withholding above the
    15% the credit counts, named as possibly deductible on line 23200
    (form T2209's general information: ITA s.20(12) and s.20(11)), which
    the estimate does not deduct (backlog TJ-0592, the owner's option
    b): a deduction the user claims goes in [estimate] deductions. ""
    when both are 0. `limit`: `ca_withholding_limit`'s result when the
    withholding was paired with its securities: the amount above 15% is
    then each security's, and the largest are named (backlog
    TJ-0638)."""
    from taxjson.lib.report_model import fmt_money as m
    parts = []
    if not_credited > 0.005:
        parts.append(f"the {m(not_credited)} neither credit takes "
                     f"(ITA s.20(12))")
    if over_cap > 0.005 and limit is not None:
        by = _over_cap_parts(
            limit.get("over_by_security") or {},
            float(limit.get("unpaired_withheld") or 0.0)
            - float(limit.get("unpaired_counted") or 0.0))
        parts.append(f"the {m(over_cap)} withheld above "
                     f"{fmt_pct(CA_FOREIGN_WITHHOLDING)} of each "
                     f"security's foreign dividends ("
                     + (by + "; " if by else "") + "ITA s.20(11))")
    elif over_cap > 0.005:
        parts.append(f"the {m(over_cap)} withheld above "
                     f"{fmt_pct(CA_FOREIGN_WITHHOLDING)} of the foreign "
                     f"dividends (ITA s.20(11))")
    if not parts:
        return ""
    it = "them" if len(parts) > 1 else "it"
    return (" and ".join(parts) + f" may be deductible on line 23200 "
            f"(form T2209); the estimate does not deduct {it}: a "
            f"deduction you claim goes in [estimate] deductions.")


def _claim_kept_note(carry: Dict[str, Any]) -> str:
    """The sentence on a carryover the limit had room for and the claim
    leaves open (tax-logic CA-AMT-04); "" with none."""
    from taxjson.lib.report_model import fmt_money as m
    kept = float(carry.get("claim_kept") or 0.0)
    if kept <= 0.005:
        return ""
    return (f"Minimum tax carryover: {m(kept)} more fits the limit, but "
            f"claiming it lowers no tax: it would only take the place of "
            f"the foreign tax credit, or of a tax already nil. It stays "
            f"open for a later year (ITA s.120.2(1): the amount claimed, "
            f"up to the limit).")


def _canada_notes(prov_key: str, prov: Dict[str, Any],
                  with_inv: Dict[str, Any], base: Dict[str, Any],
                  amt: Dict[str, Any], staking: float,
                  over_cap: float = 0.0,
                  limit: Optional[Dict[str, Any]] = None,
                  year=None) -> List[str]:
    """What the provincial calculation included, in words — the
    printed estimate names the surtax/health premium it applied.
    `over_cap`: the foreign withholding above the 15% the credit
    counts (named with the foreign tax no credit takes); `limit`:
    `ca_withholding_limit`'s result when the withholding was paired
    with its securities (backlog TJ-0638)."""
    from taxjson.lib.report_model import fmt_money as m

    def _sentence(text: str) -> str:
        return text[:1].upper() + text[1:]
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
               f"of {ca_prov_amt_base_text(amt)} "
               f"({m(amt['provincial_amt_basic'])})")
        if amt["provincial_amt_surtax"] > 0.005:
            txt += (f" + {prov_key} surtax on it "
                    f"({m(amt['provincial_amt_surtax'])})")
        notes.append(txt + ".")
    if amt["binding"] and abs(float(amt.get("carryforward", 0.0))
                              - amt["excess_fed"]) > 0.005:
        # The carryover is s.120.2(3)'s additional tax, which takes off
        # only 2/3 of the special credit above the regular one (backlog
        # TJ-0626).
        notes.append(
            f"Minimum tax carryover created: {m(amt['carryforward'])}, "
            f"the additional tax of s.120.2(3) (T691 Part 7 line 14): "
            f"the minimum amount {m(amt['minimum_amount'])} less basic "
            f"federal tax {m(amt['basic_fed'])} and less "
            f"{m(amt.get('additional_tax_sftc_part', 0.0))}, 2/3 of the "
            f"special foreign tax credit above the regular one. The "
            f"federal excess, {m(amt['excess_fed'])}, takes that credit "
            f"off in full.")
    if amt["binding"] and amt["provincial_factor_assumed"]:
        notes.append(f"The {RATE_VINTAGE} {prov_key} minimum-tax factor "
                     f"({amt['provincial_factor'] * 100:.2f}%) is "
                     f"ASSUMED unchanged until the {RATE_VINTAGE} "
                     f"provincial form is published.")
    # A TAX row the books cannot pair with a security's dividends
    # (backlog TJ-0638): how the estimate counted it.
    unpaired = float((limit or {}).get("unpaired_withheld") or 0.0)
    if unpaired > 0.005:
        counted = float(limit.get("unpaired_counted") or 0.0)
        accts = ", ".join(limit.get("unpaired_accounts") or [])
        yr = f" in {year}" if year else ""
        notes.append(
            f"Foreign tax withheld with no security: {m(unpaired)} of "
            f"TAX rows ({accts}) name no security, or one with no "
            f"foreign dividend{yr}, so the estimate cannot pair them "
            f"with their dividends; they count up to "
            f"{fmt_pct(CA_FOREIGN_WITHHOLDING)} of their account's "
            f"foreign cash dividends that none of its TAX rows names "
            f"(within each security's {fmt_pct(CA_FOREIGN_WITHHOLDING)} "
            f"that its paired withholding leaves): {m(counted)} "
            f"counted"
            + (f", {m(unpaired - counted)} not." if unpaired - counted
               > 0.005 else "."))
    # The foreign tax no credit takes and the withholding above 15% may
    # be deductible on line 23200; the estimate names them with the
    # credits of the return (the minimum-tax year's when AMT binds) and
    # deducts neither (backlog TJ-0592).
    deduction_named = False
    if tw.get("fed_ftc_unused", 0.0) > 0.005:
        lost = max(0.0, tw["fed_ftc_unused"] - tw.get("prov_ftc", 0.0))
        txt = (
            f"Foreign tax credit: of the {m(tw['foreign_tax'])} foreign "
            f"tax, federal tax takes {m(tw['fed_ftc'])} (form T2209: at "
            f"most basic federal tax x the foreign dividends' "
            f"{tw['ftc_share'] * 100:.2f}% share of net income, ITA "
            f"s.126(1)) and {prov_key} tax {m(tw['prov_ftc'])} (form "
            f"T2036: at most {prov_key} tax otherwise payable x the same "
            f"share)"
            + (f"; {m(lost)} is not credited." if lost > 0.005 else "."))
        ded = ("" if amt["binding"]
               else ca_foreign_deduction_text(lost, over_cap, limit))
        if ded:
            txt += " " + _sentence(ded)
            deduction_named = True
        notes.append(txt)
    if amt["binding"] and tw.get("foreign_tax", 0.0) > 0.005 and (
            abs(amt.get("provincial_ftc_change", 0.0)) > 0.005
            or tw.get("fed_ftc_unused", 0.0) > 0.005):
        # The minimum-tax year's credits (backlog TJ-0591).
        lost = max(0.0, tw["foreign_tax"] - amt["ftc"]
                   - amt["provincial_ftc_min_tax"])
        txt = (
            f"Foreign tax credit with AMT: the special foreign tax "
            f"credit takes {m(amt['ftc'])} of the "
            f"{m(tw['foreign_tax'])} (T691 Part 4) and {prov_key} tax "
            f"{m(amt['provincial_ftc_min_tax'])} (form T2036 in a "
            f"minimum-tax year: {ca_t2036_amt_text(prov_key)})"
            + (f"; {m(lost)} is not credited." if lost > 0.005 else "."))
        ded = ca_foreign_deduction_text(lost, over_cap, limit)
        if ded:
            txt += " " + _sentence(ded)
            deduction_named = True
        notes.append(txt)
    if not deduction_named:
        # Every credit takes the foreign tax counted, but some was
        # withheld above 15%.
        ded = ca_foreign_deduction_text(
            float(amt.get("foreign_tax_not_credited") or 0.0), over_cap,
            limit)
        if ded:
            notes.append("Foreign tax not credited: " + ded)
    carry = amt.get("carryover") or {}
    if carry.get("entered"):
        src = carry.get("source") or "the input"
        if carry.get("recovered_federal", 0.0) > 0.005:
            _sur = float(carry.get("recovered_provincial_surtax") or 0.0)
            _share = carry.get("recovered_provincial_credit",
                               carry["recovered_provincial"])
            _red = float(carry.get("recovered_credits_reduced") or 0.0)
            notes.append(
                f"Minimum tax carryover applied (ITA s.120.2; T691 Part "
                f"8, line 40427): {m(carry['recovered_federal'])} federal"
                f" + {m(_share)} {prov_key}"
                + (f" (and {m(_sur)} of {prov_key} surtax it saves)"
                   if abs(_sur) > 0.005 else "")
                + f", oldest "
                f"year first, limited to the {m(carry['limit'])} by which "
                f"basic federal tax (before the foreign tax credit) "
                f"exceeds the minimum amount (s.120.2(1)(b)) — from "
                f"{src}."
                + (f" The lower tax limits the foreign tax credit (and "
                   f"any dividend tax credit) by {m(_red)}, so tax falls "
                   f"by {m(carry.get('recovered', 0.0))}."
                   if abs(_red) > 0.005 else ""))
        elif amt["binding"]:
            notes.append(f"Minimum tax carryover available "
                         f"({m(carry['available'])}, from {src}) is not "
                         f"recovered this year: AMT binds, so regular tax "
                         f"does not exceed the minimum (ITA s.120.2).")
        if _claim_kept_note(carry):
            notes.append(_claim_kept_note(carry))
        for o, a in (carry.get("expired_by_year") or {}).items():
            notes.append(f"The {o} minimum tax carryover ({m(a)}, from "
                         f"{src}) expired after {int(o) + 7}: it carries "
                         f"forward 7 years only (ITA s.120.2) — dropped.")
    elif not amt["binding"] and amt.get("recovery_limit",
                                        amt["headroom"]) > 0.005:
        # ITA s.120.2: minimum tax paid in the 7 preceding years is
        # creditable against regular tax above the minimum
        # (T691 Part 8, T1 line 40427, provincial piggyback e.g. ON428
        # line 59). https://laws-lois.justice.gc.ca/eng/acts/I-3.3/section-120.2.html
        notes.append(f"If you paid minimum tax (AMT) in any of the 7 "
                     f"preceding years, its carryover (T691 Part 8, "
                     f"line 40427; ITA s.120.2) can reduce federal tax "
                     f"by up to "
                     f"{m(amt.get('recovery_limit', amt['headroom']))} "
                     f"(basic federal tax less the minimum amount, "
                     f"s.120.2(1)(b)), plus the "
                     f"{prov_key} share — none is entered: put it in "
                     f"taxjson.toml's [estimate] amt_carryover = {{ YEAR "
                     f"= AMOUNT }} (by year of origin, from your notice "
                     f"of assessment or T691), "
                     f"or close last year with `taxjson close-year` so "
                     f"this year reads it from the lock.")
    if staking > 0.005:
        notes.append(f"Crypto staking rewards ({m(staking)}) are taxed "
                     f"as ordinary income — no withholding, no foreign "
                     f"tax credit.")
    return notes


CA_ASSUMPTIONS = (
    "Assumes: Canadian issuers' dividends are all ELIGIBLE (non-eligible "
    "dividends would be taxed higher) — a Canadian trust's distribution "
    "(ETF, REIT or fund units) is grossed up as an eligible dividend "
    "too (its T3 split into eligible dividends, other income, capital "
    "gains and return of capital is not in the export; the T3 "
    "decides) — except the T5 "
    "box 18 capital-gains "
    "dividends named in [[capital_gains_dividends]] (taxed as capital "
    "gains); foreign withholding creditable "
    "up to " + fmt_pct(CA_FOREIGN_WITHHOLDING) + " of each security's "
    "foreign dividends (its TAX rows paired with its dividends by "
    "symbol, across the taxable accounts; a TAX row that names no "
    "security, or one with no foreign dividend in the year, up to "
    + fmt_pct(CA_FOREIGN_WITHHOLDING) + " of its account's foreign "
    "cash dividends that none of its TAX rows names, within each "
    "security's " + fmt_pct(CA_FOREIGN_WITHHOLDING) + " that its "
    "paired withholding leaves) "
    "and up to the Canadian tax on their share of net income (forms "
    "T2209 and T2036, all foreign dividends as one country's and all "
    "foreign tax as non-business income tax); foreign tax that neither "
    "credit takes, and withholding above " + fmt_pct(
        CA_FOREIGN_WITHHOLDING) + " of a security's dividends, is not "
    "deducted (either may be "
    "deductible on line 23200, ITA s.20(12) and s.20(11), in a "
    "minimum-tax year too: a deduction you claim goes in [estimate] "
    "deductions); crypto staking is "
    "ordinary income; no QC abatement or "
    "low-income reductions; interest income and interest paid are "
    "not included — each account's .sum shows interest earned ('CASH "
    "INTEREST') and interest charged ('INTEREST PAID') per currency, "
    "so take interest income from there (or the T5) and the margin "
    "interest paid to line 22100 (--carrying-charges); "
    "FX gains/losses on foreign cash (s.39(1.1), line 15300 — `taxjson "
    "fx-cash`), T3 box 21 capital gains and T5 box 18 amounts not "
    "named in [[capital_gains_dividends]] are not included; "
    "deductions below line 15000 only as entered (--deductions, "
    "--carrying-charges); a prior-year minimum tax carryover only as "
    "entered ([estimate] amt_carryover) or carried "
    "by last year's close-year lock; the basic "
    "personal amount is the only non-refundable credit (no CPP/EI, "
    "Canada employment, age, pension or donation credits); no OAS "
    "recovery tax; the AMT sees only these books and other income (no "
    "stock-option deduction add-back or donated securities).")


# The provincial "table" of a federal-only estimate: nothing provincial.
FEDERAL_ONLY_PROVINCE: Dict[str, Any] = {
    "brackets": [(_INF, 0.0)], "bpa": 0.0, "dtc_eligible": 0.0,
    "surtax": [], "amt_factor": 0.0}


def estimate_canada(*, realized: float, eligible_div: float,
                    year=None,
                    foreign_div: float, pil: float,
                    other_income: float, other_losses: float,
                    province: Optional[str],
                    actual_withheld=None,
                    staking: float = 0.0,
                    deductions: float = 0.0,
                    carrying_charges: float = 0.0,
                    capital_gains_dividends: float = 0.0,
                    amt_carryover: Optional[Dict[Any, float]] = None,
                    amt_carryover_source: Optional[str] = None,
                    withheld_assumed: float = 0.0,
                    foreign_div_noncash: float = 0.0,
                    withholding_by_security=None
                    ) -> Dict[str, Any]:
    """`actual_withheld`: the foreign tax withheld (the books' TAX rows,
    plus the 15% assumption for an account whose books carry none);
    None: no account has a TAX row, 15% is assumed on all of
    `foreign_div` but `foreign_div_noncash`. `withheld_assumed`: of
    `actual_withheld`, the part assumed. `foreign_div_noncash`: of
    `foreign_div`, the spin-off deemed dividends of the accounts with
    no TAX row — no cash paid, so no withholding is assumed on them.
    `withholding_by_security`: `ca_withholding_limit`'s entries — the
    same withholding paired with each security's foreign dividends,
    which caps it at 15% of each security's own (ITA s.20(11),
    s.126(7); backlog TJ-0638); None: the 15% caps `foreign_div` as a
    whole (a caller with no pairing).
    `amt_carryover`: {year of origin: unapplied minimum tax
    carryover} (ITA s.120.2), None when nothing was entered;
    `amt_carryover_source` names where it came from (printed).
    `deductions`: amounts deducted at lines 20700-23500 that the
    AMT allows in full (RRSP 20800, FHSA 20805, RPP 20700, ...);
    `carrying_charges`: line 22100 interest and carrying charges,
    allowed at 50% in the post-2024 AMT base. Both lower net and
    taxable income, other income first.
    `capital_gains_dividends`: T5 box 18 (line 17400) — a capital gain
    of the shareholder (ITA s.130.1(4)/s.131(1); tax-logic CA-INC-06),
    taxed with `realized` at the inclusion rate; the caller has already
    taken it OUT of `eligible_div`."""
    import math
    cg_div = float(capital_gains_dividends or 0.0)
    realized = float(realized) + cg_div
    # Eligible dividend compensation paid on a short (a Canadian
    # dealer's negative payment in lieu, s.260) nets against the
    # eligible dividends received only down to zero (s.82(1)(a.1)): the
    # excess is not negative dividend income, so it neither lowers the
    # gross-up and credit below zero nor the tax on other income
    # (backlog TJ-0727; tax-logic CA-RPT-04).
    eligible_net = float(eligible_div)
    eligible_div = max(0.0, eligible_net)
    eligible_excess = max(0.0, -eligible_net)
    for _n, _v in (("deductions", deductions),
                   ("carrying_charges", carrying_charges)):
        if not math.isfinite(float(_v)) or float(_v) < 0:
            raise ValueError(f"{_n} must be a non-negative finite "
                             f"amount, got {_v!r}")
    deductions = float(deductions)
    carrying_charges = float(carrying_charges)
    ded_total = deductions + carrying_charges
    pick = apply_vintage(year)
    if province is None:
        # Federal only (close-year with no supported province): no
        # provincial tax, credit, surtax, premium or minimum-tax factor;
        # every federal figure is the same as under any province.
        prov_key = None
        prov = FEDERAL_ONLY_PROVINCE
    else:
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
    # flat 15%-of-foreign-dividends assumption stands only for the
    # dividends of accounts whose books carry no TAX row, and never for
    # a spin-off's deemed dividend: no cash is paid, so nothing is
    # withheld (backlog TJ-0422). `withheld_assumed` is the assumed
    # part of `actual_withheld`; `foreign_div_noncash` the deemed
    # dividends of those accounts. The source says which part is which.
    pct = fmt_pct(CA_FOREIGN_WITHHOLDING)
    noncash = min(max(0.0, float(foreign_div_noncash or 0.0)),
                  max(0.0, foreign_div))
    nc_note = (f"; none on {noncash:,.2f} of spin-off deemed dividends, "
               f"paid in shares" if noncash > 0.005 else "")
    # Paired with its securities, the withholding counts up to 15% of
    # each security's own foreign dividends: excess withholding on one
    # security is not credited against another's room (ITA s.20(11),
    # s.126(7); backlog TJ-0638; tax-logic CA-EST-FTC).
    limit = (ca_withholding_limit(withholding_by_security)
             if withholding_by_security is not None else None)
    of_divs = ("each security's foreign divs" if limit is not None
               else "foreign divs")
    if actual_withheld is not None:
        if limit is not None:
            ftc = limit["creditable"]
            ftc_by_assumption = min(limit["assumed"], max(0.0, ftc))
        else:
            ftc = min(max(0.0, actual_withheld),
                      foreign_div * CA_FOREIGN_WITHHOLDING)
            ftc_by_assumption = min(
                max(0.0, float(withheld_assumed or 0.0)), max(0.0, ftc))
        if ftc_by_assumption <= 0.005:
            ftc_by_assumption = 0.0
        # The two parts foot to the foreign tax counted as printed: a
        # security's capped withholding leaves fractions of a cent.
        ftc_by_assumption = round(ftc_by_assumption, 2)
        ftc_from_rows = round(round(ftc, 2) - ftc_by_assumption, 2)
        if ftc_by_assumption > 0.005:
            ftc_basis = "tax_rows_and_assumed"
            ftc_source = (f"TAX rows {ftc_from_rows:,.2f} + "
                          f"{ftc_by_assumption:,.2f} assumed ({pct} of "
                          f"the foreign dividends of accounts with no "
                          f"TAX row{nc_note}), capped at {pct} of "
                          f"{of_divs}")
        else:
            ftc_basis = "tax_rows"
            ftc_source = f"actual TAX rows (capped at {pct} of {of_divs})"
    else:
        ftc = (limit["creditable"] if limit is not None
               else (foreign_div - noncash) * CA_FOREIGN_WITHHOLDING)
        ftc_by_assumption, ftc_from_rows = ftc, 0.0
        ftc_basis = "assumed"
        ftc_source = f"assumed {pct} of foreign dividends{nc_note}"
    # The withholding above the 15% the credit counts: not credited, and
    # possibly deductible on line 23200 (s.20(11)); named, not deducted
    # (backlog TJ-0592). The assumption alone is never above it.
    if actual_withheld is None:
        over_cap = 0.0
    elif limit is not None:
        over_cap = limit["over_cap"]
    else:
        over_cap = max(0.0, float(actual_withheld)
                       - max(0.0, foreign_div) * CA_FOREIGN_WITHHOLDING)
    # Net income (line 23600) = taxable income + the carryforward
    # losses deducted below it at line 25300 (x50%).
    grossed = eligible_div * CA_ELIGIBLE_GROSSUP
    net_income = (other_income + pil + staking - ded_total
                  + taxable_gain + foreign_div + grossed
                  + CA_INCLUSION * losses_applied)
    with_inv = _canada_tax(other_income + pil + staking - ded_total,
                           taxable_gain, eligible_div, foreign_div, prov,
                           ftc=ftc, net_income=net_income,
                           prov_key=prov_key)
    base = _canada_tax(other_income - ded_total, 0.0, 0.0, 0.0, prov,
                       prov_key=prov_key)
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
                      carrying_charges=carrying_charges,
                      regular_ftc=with_inv["detail"]["fed_ftc"],
                      prov_key=prov_key, regular=with_inv["detail"])
    # The other income alone (its carrying charges and deductions too)
    # can owe minimum tax. The estimate is incremental, so only the
    # top-up the investment income adds is counted against it: adding
    # the whole return's top-up charged the other income's own AMT to
    # investment income of 0 (backlog TJ-0489; tax-logic CA-EST-AMT).
    base_amt = _amt_canada(
        realized=0.0, eligible_div=0.0, foreign_div=0.0, pil=0.0,
        other_income=other_income, other_losses=0.0, ftc=0.0,
        regular_fed=base["federal"], prov=prov,
        fed_bpa_amount=base["detail"]["fed_bpa_amount"],
        prov_basic=base["detail"]["prov_basic"],
        deductions=deductions, carrying_charges=carrying_charges,
        prov_key=prov_key, regular=base["detail"])
    amt["base_topup"] = base_amt["topup"]
    amt["topup_attributed"] = round(amt["topup"] - base_amt["topup"], 2)
    # Minimum tax carryover (s.120.2): recovered against the regular
    # federal tax above the minimum, the province's share at its
    # minimum-tax factor. The estimate is incremental, so only the
    # recovery the investment income changes is attributed to it: the
    # other income alone would recover `base_recovered` (tax-logic
    # CA-AMT-04..07).
    recovered_attr = 0.0
    if year is not None:
        try:
            _cy = int(year)
        except (TypeError, ValueError):
            _cy = None
    else:
        _cy = None
    if _cy is not None:
        factor = float(prov.get("amt_factor") or 0.0)

        def _after(claim: float) -> Dict[str, Any]:
            return _canada_tax(other_income + pil + staking - ded_total,
                               taxable_gain, eligible_div, foreign_div,
                               prov, ftc=ftc, net_income=net_income,
                               fed_mtc=claim, prov_mtc=claim * factor,
                               prov_key=prov_key)
        c = ca_amt_carryover(_cy, amt_carryover or {},
                             amt["recovery_limit"])
        # The limit is a ceiling: claim what still lowers tax, and keep
        # the rest open (s.120.2(1), tax-logic CA-AMT-04, TJ-1030).
        _most = c["recovered_federal"]
        _useful = _useful_claim(lambda x: _after(x)["total"], _most)
        if _useful < _most - 0.005:
            c = ca_amt_carryover(_cy, amt_carryover or {},
                                 amt["recovery_limit"], claim=_useful)
        fed_rec = c["recovered_federal"]
        after = _after(fed_rec)
        prov_rec = with_inv["provincial"] - after["provincial"]
        # Of the provincial drop, the share deducted from basic
        # provincial tax (what the provincial form's carryover line
        # takes); the rest is the surtax the lower basic tax saves.
        # Labelled "before ON surtax", the whole drop was printed as the
        # share (backlog TJ-0016).
        prov_credit = (with_inv["detail"]["prov_basic"]
                       - after["detail"]["prov_basic"])
        # The surtax the lower basic tax saves, from the surtax itself:
        # the rest of the provincial drop is the foreign tax credit the
        # lower tax limits (form T2036), which the drop reported as an
        # Alberta surtax that does not exist (backlog TJ-0653).
        prov_sur = (with_inv["detail"]["prov_surtax"]
                    - after["detail"]["prov_surtax"])
        fed_rec_t = with_inv["federal"] - after["federal"]
        bc = ca_amt_carryover(_cy, amt_carryover or {},
                              base_amt["recovery_limit"])
        b_after = _canada_tax(other_income - ded_total, 0.0, 0.0, 0.0,
                              prov, fed_mtc=bc["recovered_federal"],
                              prov_mtc=bc["recovered_federal"] * factor,
                              prov_key=prov_key)
        base_rec = base["total"] - b_after["total"]
        recovered = fed_rec_t + prov_rec
        recovered_attr = recovered - base_rec
        # The year's own carryover is its additional tax of s.120.2(3)
        # (T691 Part 7 line 14 to Part 8 line 14), not the excess: the
        # two differ when the special foreign tax credit is above the
        # regular one (backlog TJ-0626; tax-logic CA-AMT-02).
        created = amt["carryforward"]
        closing = dict(c["remaining_by_year"])
        if created > 0.005:
            closing[str(_cy)] = created
        # Line 40427 is the nominal amount recovered (what the origins
        # give up, `recovered_federal` from ca_amt_carryover); federal
        # tax falls by less when the foreign tax credit, limited by the
        # lower tax, falls too. The tax drop was reported as the credit
        # (backlog TJ-0653).
        fed_nominal = c["recovered_federal"]
        _reduced = fed_nominal + prov_credit + prov_sur - recovered
        # With no credit limited, the share and the surtax foot to the
        # rounded provincial drop, as they did.
        _sur_out = (round(round(prov_rec, 2) - round(prov_credit, 2), 2)
                    if abs(prov_rec - prov_credit - prov_sur) < 0.005
                    else round(prov_sur, 2))
        c.update({
            "entered": amt_carryover is not None,
            "source": amt_carryover_source,
            "recovered_federal": round(fed_nominal, 2),
            # What federal and provincial tax actually fall by.
            "recovered_federal_tax": round(fed_rec_t, 2),
            "recovered_provincial": round(prov_rec, 2),
            "recovered_provincial_credit": round(prov_credit, 2),
            "recovered_provincial_surtax": _sur_out + 0.0,
            # The foreign tax credits (and any dividend tax credit
            # floored at the tax) the lower tax takes back: the parts
            # less it foot to the drop, `recovered` (to the cent).
            "recovered_credits_reduced": round(_reduced, 2) + 0.0,
            "recovered": round(recovered, 2),
            "base_recovered": round(base_rec, 2),
            "attributed": round(recovered_attr, 2),
            "created": created,
            "closing_by_year": dict(sorted(closing.items())),
            "closing": round(float(sum(closing.values())), 2),
        })
        # What the claim leaves open though the limit had room for it.
        _kept = round(_most - fed_rec, 2)
        if _kept > 0.005:
            c["claim_kept"] = _kept
        amt["carryover"] = c
    notes = vintage_notes(year, pick)
    if eligible_excess > 0.005:
        notes.append(
            f"Eligible dividend compensation paid (on a short, s.260) "
            f"exceeds the eligible dividends received by "
            f"{eligible_excess:,.2f}: it offsets them only down to zero "
            f"(ITA s.82(1)(a.1)), so the excess is not deducted from "
            f"other income here.")
    if prov_key is None:
        notes.append("Federal only: no supported [settings] province, so "
                     "no provincial tax, credit or minimum tax is "
                     "included.")
        _kept_note = _claim_kept_note((amt.get("carryover") or {}))
        if _kept_note:
            notes.append(_kept_note)
    else:
        notes += _canada_notes(prov_key, prov, with_inv, base, amt,
                               staking, over_cap=over_cap, limit=limit,
                               year=year)
    return {
        "country": "canada", "province": prov_key,
        "federal_only": prov_key is None,
        "amt": amt,
        # The investment income's tax with the top-up it adds and the
        # change in carryover recovery it causes (incremental, like
        # `estimated_tax`).
        "estimated_tax_with_amt": round(est + amt["topup"]
                                        - base_amt["topup"]
                                        - recovered_attr, 2),
        "losses_opening": round(max(0.0, other_losses), 2),
        "losses_created": round(max(0.0, -realized), 2),
        "vintage": RATE_VINTAGE,
        "notes": notes,
        "assumptions": CA_ASSUMPTIONS,
        "taxable_gain": round(taxable_gain, 2),
        "capital_gains_dividends": round(cg_div, 2),
        "losses_applied": round(losses_applied, 2),
        "losses_unused": round(losses_unused, 2),
        "grossed_eligible": round(grossed, 2),
        # The eligible dividends taxed (net of compensation paid, at
        # least 0) and the compensation paid above the dividends
        # received, which is not deducted (backlog TJ-0727).
        "eligible_dividends": round(eligible_div, 2),
        "eligible_compensation_excess": round(eligible_excess, 2) + 0.0,
        "staking": round(staking, 2),
        "deductions": round(deductions, 2),
        "carrying_charges": round(carrying_charges, 2),
        # The foreign tax counted (T2209 line 1: the TAX rows capped at
        # 15%, or 15% assumed — `ftc_source` says which) and what the
        # federal (T2209) and provincial (T2036) credits take of it.
        # `ftc_assumed` is its old name, kept for scripts that read it
        # (it was the foreign tax whether assumed or not: TJ-0345).
        "foreign_tax_creditable": round(ftc, 2),
        "ftc_federal": round(with_inv["detail"]["fed_ftc"], 2),
        "ftc_provincial": round(with_inv["detail"]["prov_ftc"], 2),
        # What neither credit takes of it (the regular year's; the
        # minimum-tax year's is amt.foreign_tax_not_credited) and the
        # withholding above the 15% counted: both may be deductible on
        # line 23200 (s.20(12), s.20(11)) and are not deducted (backlog
        # TJ-0592).
        "foreign_tax_not_credited": round(max(
            0.0, ftc - with_inv["detail"]["fed_ftc"]
            - with_inv["detail"]["prov_ftc"]), 2) + 0.0,
        "foreign_withholding_over_cap": round(over_cap, 2) + 0.0,
        # Of it, each security's (paired by the books' symbols), and the
        # TAX rows the books cannot pair with a security: what they
        # withheld, what counted and their accounts (backlog TJ-0638).
        "foreign_withholding_over_cap_by_security": {
            k: round(v, 2) for k, v in sorted(
                ((limit or {}).get("over_by_security") or {}).items())},
        "foreign_withholding_unpaired": {
            "withheld": round(float((limit or {}).get(
                "unpaired_withheld") or 0.0), 2) + 0.0,
            "counted": round(float((limit or {}).get(
                "unpaired_counted") or 0.0), 2) + 0.0,
            "accounts": list((limit or {}).get("unpaired_accounts")
                             or [])},
        "ftc_assumed": round(ftc, 2),
        "ftc_source": ftc_source,
        # Of the foreign tax counted, the part from the TAX rows and the
        # part assumed at 15% (`ftc_basis`: tax_rows, assumed or
        # tax_rows_and_assumed); the spin-off deemed dividends nothing
        # was assumed withheld on (backlog TJ-0422).
        "ftc_basis": ftc_basis,
        "foreign_tax_from_rows": round(ftc_from_rows, 2) + 0.0,
        "foreign_tax_by_assumption": round(ftc_by_assumption, 2) + 0.0,
        "foreign_div_noncash": round(noncash, 2) + 0.0,
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
    preferential. The total is at most the tax on all of the taxable
    income at the ordinary rates (Pub 505 Worksheet 2-7 lines 39-40, the
    Qualified Dividends and Capital Gain Tax Worksheet's last lines;
    tax-logic US-EST-CEILING): the 0% capital-gain band can end below the
    12% ordinary one, and a gain stacked in between paid 15% where the
    ordinary rate is 12% (backlog TJ-0509). `preferential` is then what
    is left of the ceiling after the ordinary tax. NIIT on investment
    income over the MAGI threshold is added by the caller (it needs the
    investment split)."""
    ord_taxable = max(0.0, ordinary - US_STD_DEDUCTION)
    unused_ded = max(0.0, US_STD_DEDUCTION - ordinary)
    pref_taxable = max(0.0, pref - unused_ded)
    ord_tax = _bracket_tax(ord_taxable, US_ORD_BRACKETS)
    pref_tax = _stacked_tax(ord_taxable, pref_taxable, US_LTCG_BRACKETS)
    all_ordinary = _bracket_tax(ord_taxable + pref_taxable, US_ORD_BRACKETS)
    capped = all_ordinary < ord_tax + pref_tax - 1e-9
    total = all_ordinary if capped else ord_tax + pref_tax
    return {"ordinary": ord_tax, "preferential": total - ord_tax,
            "total": total,
            "detail": {"ord_taxable": ord_taxable,
                       "pref_taxable": pref_taxable,
                       "pref_stacked": pref_tax,
                       "all_ordinary": all_ordinary,
                       "ceiling": capped}}


def estimate_usa(*, st: float, lt: float, qualified_div: float,
                 year=None,
                 pil: float, other_income: float,
                 other_losses: float,
                 lt_losses: float = 0.0,
                 deduction_claimed: Optional[float] = None,
                 section_1256: float = 0.0
                 ) -> Dict[str, Any]:
    """`st` / `lt`: the year's short- and long-term gains other than
    §1256 contracts (Form 8949); `section_1256`: the year's net gain or
    loss of §1256 contracts (Form 6781 line 7), added 40% to the
    short-term and 60% to the long-term figure (Form 6781 lines 8 and
    9; tax-logic US-FUT-03, backlog TJ-0466).
    `other_losses`: the SHORT-term capital loss carryover (Schedule D
    line 6); `lt_losses`: the LONG-term one (line 14). Each nets in its
    own column first (lines 7 and 15), then the columns cross-net
    (line 16) and a net loss offsets up to $3,000 of ordinary income
    (tax-logic US-EST-CARRY-TERM; re-audit A2-0481 / A2-0809: one
    carryover applied to short-term gains first understated the tax of
    a long-term carryover). `deduction_claimed`: the year's [carryover]
    claimed entry, the deduction the filed return's taxable income
    absorbed (the next year's Capital Loss Carryover Worksheet line 4):
    the carryover after the year counts it as used, as far as the
    year's net loss allows, instead of what the estimate's own taxable
    income absorbs (tax-logic US-EST-CARRY-TI; backlog TJ-0464)."""
    import math
    for _n, _v in (("other_losses", other_losses),
                   ("lt_losses", lt_losses),
                   ("deduction_claimed", deduction_claimed or 0.0)):
        if not math.isfinite(float(_v)) or float(_v) < 0:
            raise ValueError(f"{_n} must be a non-negative finite "
                             f"amount, got {_v!r}")
    if not math.isfinite(float(section_1256 or 0.0)):
        raise ValueError(f"section_1256 must be a finite amount, got "
                         f"{section_1256!r}")
    pick = apply_vintage(year)
    # Form 6781 lines 8 and 9 go to Schedule D lines 4 and 11: the §1256
    # net joins each term's gains before any carryover or netting.
    s1256_st, s1256_lt = section_1256_split(section_1256)
    st = float(st) + s1256_st
    lt = float(lt) + s1256_lt
    st_net = st - float(other_losses)
    lt_net = lt - float(lt_losses)
    loss = 0.0
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
    # The residual loss of each term after cross-netting (at most one
    # is non-zero): what carries over, less the ordinary-income offset
    # actually used, short-term first (Capital Loss Carryover Worksheet;
    # tax-logic US-CARRY-01).
    st_loss_left, lt_loss_left = max(0.0, -st_net), max(0.0, -lt_net)
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
    if deduction_claimed is not None:
        # The filed return's deduction, as the carryover ledger takes it
        # (src/taxjson/bin/taxjson_carryover.py build_usa_ledger): the
        # lock carried the whole loss when the estimate had no other
        # income (backlog TJ-0464).
        offset_used = min(ordinary_offset, float(deduction_claimed))
    ded_st = min(offset_used, st_loss_left)
    ded_lt = min(offset_used - ded_st, lt_loss_left)
    st_carry_after = st_loss_left - ded_st
    lt_carry_after = lt_loss_left - ded_lt
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
        # Each term's gains before the carryovers, the §1256 parts in.
        "st_input": round(st, 2), "lt_input": round(lt, 2),
        # The two parts to the cent add up to the net shown.
        "section_1256_gain": round(float(section_1256 or 0.0), 2),
        "section_1256_short_term": round(
            round(float(section_1256 or 0.0), 2) - round(s1256_lt, 2), 2)
        + 0.0,
        "section_1256_long_term": round(s1256_lt, 2) + 0.0,
        "st_net": round(st_net, 2), "lt_net": round(lt_net, 2),
        "ordinary_offset": round(ordinary_offset, 2),
        "offset_used_for_carryover": round(offset_used, 2),
        "offset_used_from": ("claimed" if deduction_claimed is not None
                             else "taxable_income"),
        "losses_unused": round(loss + max(0.0, -net_cap)
                               - offset_used, 2),
        "st_carryover_after": round(st_carry_after, 2),
        "lt_carryover_after": round(lt_carry_after, 2),
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
