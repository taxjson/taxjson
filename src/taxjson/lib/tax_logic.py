"""`taxjson tax-logic`: a short, plain statement of every rule taxjson
applies for one country, with the project's own settings filled in.

tax-logic is the SPEC the code is compared against. Each statement is a
``Rule`` with a stable id (``CA-...`` in the Canada section, ``US-...``
in the United States section). Ids are never reused: a retired id goes
in ``tests/tax_rules/retired.txt``. A change in tax logic changes (or
adds) a Rule here, in the right country's section, and a test that pins
it carries the id:

    from tax_rules import rule, rule_absent
    @rule("CA-SL-02")                        # pins a Canada statement
    @rule_absent("CA-SL-02", country="usa")  # same book, US: must NOT apply

``scripts/check_tax_rules.py`` (a CI stage) fails on an unknown id, a
Canada-tagged test that drives the US engine (or the reverse), a rule
with no test that is not in the shrink-only baseline, and a setting
that no rule and no ``NON_RULE_SETTINGS`` entry names.

API:
- ``rule_sections(country, settings)`` -> [(title, [Rule])] with the
  settings in force filled in.
- ``sections(country, settings, ids=False)`` -> [(title, [str])]: the
  rendered bullets (Rules marked ``cont`` join the previous bullet).
- ``render(country, settings, width=88, ids=False)``: the plain text;
  ``ids=True`` prefixes each statement with ``[ID]``.
- ``catalog(country=None)`` -> {id: Rule} over every settings variant
  (``VARIANT_AXES``): the full set of ids the checker knows.
- ``rule_country(id)`` -> "canada" | "usa" from the id's prefix.
- ``PARTITION_RULES``: the rules that must carry a @rule_absent test.

Settings are read through the SAME resolvers the engine uses
(lib/country.resolve_tax_date / foreign_roc_mode / futures_settle_mode,
lib/pipeline.option_timing_from_settings), so the text cannot describe
a value the engine reads differently or refuses (partition SPEC-12). An
unknown country raises (lib/country.canonical_country) — it is never
rendered as Canada.

Keep it short: this is the summary, the README and REFERENCES.md carry
the detail.
"""
from __future__ import annotations

import itertools
import textwrap
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib import country as _C

Section = Tuple[str, List[str]]


@dataclass(frozen=True)
class Rule:
    """One statement. `keys`: the [settings] keys (or config paths) the
    statement, and the code behind it, depend on. `cont`: rendered on
    the same bullet as the previous Rule (a multi-claim sentence split
    so each claim has its own id)."""
    id: str
    text: str
    keys: Tuple[str, ...] = ()
    cont: bool = False


RuleSection = Tuple[str, List[Rule]]

# The settings each country's text branches on, with every value that
# changes a statement. catalog() renders every combination.
VARIANT_AXES: Dict[str, Dict[str, Tuple[Any, ...]]] = {
    _C.CANADA: {
        "tax_date": ("settle", "trade"),
        "option_premium_timing": ("grant", "close"),
        "option_grant_timing_since": (None, 2025),
        "option_buyback_loss_superficial": (False, True),
        "futures_settle": ("trade", "next_day"),
        "foreign_return_of_capital": ("dividend", "acb"),
    },
    _C.USA: {
        "tax_date": ("trade", "settle"),
        "futures_settle": ("trade", "next_day"),
    },
}

# [settings] keys that change no tax rule, each with its reason. Every
# key in lib/country.SETTING_COUNTRY must be named by some Rule.keys or
# listed here (scripts/check_tax_rules.py).
NON_RULE_SETTINGS: Dict[str, str] = {
    "year": "which year is reported",
    "prior_year_record": "a file path for the handoff check",
    "source_currencies": "which FX rate series are fetched",
    "cross_asset": "retired; warned about and ignored",
}


# Rules the owner named as the Canada/US partition: each needs a
# @rule_absent test (the same synthetic book under the other country
# must NOT apply it). scripts/check_tax_rules.py enforces it, with a
# shrink-only tests/tax_rules/baseline-unpaired.txt for the pairs not
# written yet. Add a rule here when a Phase-B fix makes it one country's.
PARTITION_RULES = frozenset({
    # Canada
    "CA-SL-01",        # s.54 window on settle dates
    "CA-SL-02",        # s.54 still held at the end of day 30
    "CA-SL-05",        # a long call replaces the shares (enforced)
    "CA-ACB-01",       # s.47 average cost across accounts
    "CA-ACB-07",       # s.40(3) deemed gain on ROC beyond ACB
    "CA-ACB-08",       # s.90(1) foreign ROC as a dividend (IB)
    "CA-OPT-01",       # s.49(1) grant timing
    "CA-RPT-01",       # T1135
    "CA-FX-07",        # fx-cash s.39(1.1) $200 exemption
    "CA-FX-04",        # futures P/L on average cost
    "CA-STKDIV-01",    # stock dividend: $0 acquisition (counts for s.54)
    "CA-ACB-12",       # manual phantom-loss check on settle dates
    "CA-CRYPTO-02",    # stablecoins as US-dollar cash
    "CA-DATE-01",      # settle-date tax year by default
    "CA-CTRY-02",      # US-only settings/commands/flags refused
    "CA-CTRY-03",      # base currency CAD
    "CA-INC-03",       # s.260 payment in lieu as a dividend (D3)
    "CA-INC-DATE-ROC-TRUST",  # trust ROC on the record date (D4)
    "CA-INC-DATE-TRUST",      # trust distribution by record date (D5)
    "CA-INC-06",       # T5 box 18 capital-gains dividends (map; R1-62)
    # United States
    "US-WASH-01",      # §1091 window on trade dates
    "US-WASH-06",      # no still-held test
    "US-WASH-12",      # a long call is a warning only
    "US-HOLD-01",      # short-/long-term
    "US-BASIS-01",     # FIFO per account
    "US-OPT-01",       # §1234 close timing
    "US-SEND-02",      # a gift is not a disposition for the donor
    "US-RPT-01",       # Form 8949
    "US-RPT-04",       # estimate: NIIT, standard deduction
    "US-DATE-01",      # trade-date tax year by default
    "US-CTRY-02",      # Canada-only settings/commands/flags refused
    "US-CTRY-03",      # base currency USD
    "US-FUT-01",       # futures P/L FIFO
    "US-CRYPTO-02",    # stablecoins are property (CA: US-dollar cash)
    "US-STKDIV-01",    # stock dividend: §307 basis spread, no §1091
    "US-BASIS-04",     # manual phantom-loss check on trade dates
    "US-ROC-03",       # ROC with no shares held: not booked (CA books it)
    "US-ROC-04",       # basis increase with no shares: not applied (CA: next ACB)
    "CA-ACB-13",       # basis increase with no shares: next purchase's ACB
    "US-WASH-18",      # futures / futures options outside §1091 (CA denies)
    "US-INC-DATE-RIC", # §852(b)(7) January dividends: warn + list (D8)
    # Planning tools (partition COMMANDS-01/02/05)
    "CA-PLAN-01",      # radar: settle dates, still-held rescue
    "CA-PLAN-02",      # radar: a long call is a replacement
    "US-PLAN-01",      # radar: the US engine's verdict, no rescue
    "US-PLAN-02",      # radar: a long call is a note only
})


def _split_share_roots():
    from taxjson.lib.income_dating import SPLIT_SHARE_ROOTS
    return SPLIT_SHARE_ROOTS


def _corp_list(s: Dict[str, Any]) -> List[str]:
    """[settings] corporate_distributions as the engine reads it."""
    from taxjson.lib.income_dating import SETTING_CORPORATE, _symbols
    return list(_symbols(s.get(SETTING_CORPORATE), SETTING_CORPORATE))


def _ric_list(s: Dict[str, Any]) -> List[str]:
    """[settings] ric_january_dividends as the engine reads it."""
    from taxjson.lib.income_dating import parse_ric_entries
    return [f"{a} {b}".strip() for a, b in
            parse_ric_entries(s.get("ric_january_dividends"))]


def _options(country: str, settings: Dict[str, Any]) -> Dict[str, Any]:
    """The engine's own reading of the option settings."""
    from taxjson.lib.pipeline import option_timing_from_settings
    return option_timing_from_settings(dict(settings, country=country))


def _ownership(country: str) -> List[Rule]:
    """What a project of this country refuses: built from the
    lib/country tables, so the statement cannot drift from them."""
    other = _C.other_country(country)
    p = "CA" if country == _C.CANADA else "US"
    keys = sorted(_C.owners(_C.SETTING_COUNTRY, other))
    cfg = sorted(_C.owners(_C.CONFIG_COUNTRY, other))
    cmds = sorted(_C.owners(_C.COMMAND_COUNTRY, other))
    flags = sorted(_C.owners(_C.FLAG_COUNTRY, other))
    files = sorted(_C.owners(_C.PROJECT_FILE_COUNTRY, other))
    plans = [k for k in _C.PLAN_COUNTRY
             if _C.PLAN_COUNTRY[k] == other]

    def _cmd(c: str) -> str:
        name, _, var = c.partition(":")
        if name == "form-export":
            return f"`form-export --form {var}`"
        if var:
            return f"a `{name}` {var}"
        return f"`{name}`"
    other_name = _C.DISPLAY_NAME[other]
    parts = []
    if keys or cfg:
        parts.append(f"{other_name}-only settings ("
                     + ", ".join(keys + cfg) + ")")
    if cmds:
        parts.append(f"{other_name}-only commands ("
                     + ", ".join(_cmd(c) for c in cmds) + ")")
    if flags:
        parts.append("the flag" + ("s " if len(flags) > 1 else " ")
                     + ", ".join(flags))
    if plans:
        parts.append(f"the {other_name} account plans ([accounts.X] "
                     f"plan = " + ", ".join(plans) + ")")
    if files:
        parts.append(f"the {other_name}-only project file"
                     + ("s " if len(files) > 1 else " ")
                     + ", ".join(files))
    cur = _C.HOME_CURRENCY[country]
    return [
        Rule(f"{p}-CTRY-01",
             f"The project's country is required ([settings] country = "
             f"\"{country}\"); taxjson never assumes one, and a spelling "
             f"other than canada, ca, usa or us is refused.",
             keys=("country",)),
        Rule(f"{p}-CTRY-02",
             "Refused in this project: " + "; ".join(parts) + ".",
             keys=tuple(keys) + tuple(cfg)),
        Rule(f"{p}-CTRY-03",
             f"base_currency must be {cur} (the return is filed in {cur}).",
             keys=("base_currency",)),
    ]


def _local_tz(s: Dict[str, Any]) -> str:
    """The zone crypto rows are dated in, as the parsers read it."""
    from taxjson.lib.brokerages._crypto_common import DEFAULT_LOCAL_TZ
    return str(s.get("local_timezone") or DEFAULT_LOCAL_TZ)


def _canada(s: Dict[str, Any]) -> List[RuleSection]:
    c = _C.CANADA
    basis = _C.resolve_tax_date(c, s.get("tax_date"))
    kw = _options(c, s)
    timing = kw["option_premium_timing"]
    start = kw["option_grant_since"]
    since_set = s.get("option_grant_timing_since") not in (None, "")
    fut = _C.futures_settle_mode(s)
    froc = _C.foreign_roc_mode(dict(s, country=c))
    buyback = kw["option_buyback_loss_superficial"]
    tz = _local_tz(s)

    if basis == "settle":
        year_rule = Rule(
            "CA-DATE-01",
            "A trade belongs to the year it SETTLES (tax_date = "
            "\"settle\", CRA practice): a sale on Dec 31 that settles "
            "Jan 2 is next year's disposition.", keys=("tax_date",))
    else:
        year_rule = Rule(
            "CA-DATE-02",
            "A trade belongs to the year it is TRADED (tax_date = "
            "\"trade\"; CRA practice is the settlement date).",
            keys=("tax_date",))
    tk = ("option_premium_timing", "option_grant_timing_since", "year")
    if timing == "grant":
        prem = [
            Rule("CA-OPT-01",
                 "Writing an option: the premium is a capital gain in the "
                 "year it is written (ITA s.49(1); option_premium_timing = "
                 "\"grant\")"
                 + (f", for contracts written from {start}" if start else "")
                 + (" (option_grant_timing_since" if since_set else
                    " (defaults to the project year; set "
                    "option_grant_timing_since once") + ").", keys=tk),
            Rule("CA-OPT-02", "Earlier contracts keep close timing.",
                 keys=tk, cont=True),
            Rule("CA-OPT-03",
                 "Buying it back: the cost is a capital loss in the "
                 "buy-back year. Expiry adds nothing.", keys=tk),
            Rule("CA-OPT-04",
                 "A bought option's cost is a loss on its expiry date.",
                 cont=True),
        ]
    else:
        prem = [
            Rule("CA-OPT-05",
                 "Writing an option: nothing is taxed until it closes "
                 "(option_premium_timing = \"close\").", keys=tk),
            Rule("CA-OPT-05",
                 "Buying it back or expiry: the premium minus the cost is "
                 "a gain or loss on that date.", keys=tk),
            Rule("CA-OPT-04",
                 "A bought option's cost is a loss on its expiry date.",
                 cont=True),
        ]
    return [
        ("Tax year and dates", [
            year_rule,
            Rule("CA-DATE-03",
                 "Settle dates come from the broker when printed (one "
                 "earlier than the trade date is refused; one more than 7 "
                 "days after it is booked as printed and flagged)."),
            Rule("CA-DATE-04",
                 "Otherwise: T+1 (from 2024-05-27 in CAD and MXN, "
                 "2024-05-28 in USD), T+2 from 2017-09-05, T+3 before; "
                 "other markets T+2 — from 2014-10-06 in the UK, the EU "
                 "and Switzerland (T+1 from 2027-10-11 in every EU "
                 "currency, sterling and the Swiss franc), 2016-03-07 in "
                 "Australia and New Zealand, 2018-12-10 in Singapore and "
                 "2019-07-16 in Japan (T+3 before each), always in Hong "
                 "Kong, and on the North-American dates elsewhere; "
                 "options T+1, but an exercise or assignment takes its "
                 "stock leg's date.", cont=True),
            Rule("CA-DATE-05",
                 "Days skip weekends and settlement holidays (US: NYSE and "
                 "Federal Reserve holidays; Canada: TSX holidays, "
                 "Remembrance Day, Truth and Reconciliation; elsewhere "
                 "weekends only). The cycle and calendar are the listing's "
                 "market, not the quote currency's: an IB US-dollar unit "
                 "listed on the TSX settles on the Canadian calendar, and "
                 "a US-dollar line listed on the LSE is an LSE security "
                 "(.L) on the UK cycle.", cont=True),
            Rule("CA-DATE-06",
                 "The generic importer uses a mapped settle column (one "
                 "more than 31 days after the trade is refused, more than "
                 "7 is flagged), else this cycle (settle_on_trade_date = "
                 "true keeps the trade date); its futures and its $0 "
                 "option closes on the expiry day follow the two rules "
                 "below.", cont=True),
            Rule("CA-DATE-07", "Crypto settles on the trade date;",
                 cont=True),
            Rule("CA-DATE-08", "an option expiry is dated its expiry day.",
                 cont=True),
            (Rule("CA-DATE-09",
                  "Futures and futures options settle on the TRADE date "
                  "(futures_settle = \"trade\": variation margin settles "
                  "the P/L daily).", keys=("futures_settle",))
             if fut == "trade" else
             Rule("CA-DATE-10",
                  "Futures and futures options settle on the next "
                  "settlement day (futures_settle = \"next_day\").",
                  keys=("futures_settle",))),
            Rule("CA-DATE-SESSION",
                 "A trade is dated by its exchange's trade date, not the "
                 "broker's clock: IB stamps US Eastern time, so a US stock "
                 "or ETF filled in the overnight session (20:00 ET or "
                 "later, Sunday to Thursday nights, and its after-midnight "
                 "part on a day the NYSE is closed) trades on the NEXT "
                 "trading day and settles from it (a Dec 30 20:30 fill "
                 "trades Dec 31 and settles in January). So does a "
                 "US-dollar futures or futures-option fill in the CME "
                 "evening session (18:00 ET or later, Sunday to Thursday, "
                 "or on a weekday the exchange is closed) and an SPX, "
                 "SPXW, XSP or VIX option filled in Cboe Global Trading "
                 "Hours (20:15 ET or later). A fill on the ASX, HKEX, "
                 "Tokyo, Singapore or NZX exchanges (an AUD, HKD, JPY, SGD "
                 "or NZD row, any asset class) is dated in the exchange's "
                 "local time. Every other fill keeps the clock date. A "
                 "moved fill sorts before that day's other trades; the "
                 "broker's stamp is kept (broker_time)."),
            Rule("CA-DATE-14",
                 "Rows at the same date and time keep the export's row "
                 "order (Webull and the generic importer print no clock "
                 "time, Questrade stamps midnight): a write listed before "
                 "its same-day buy-back is a write and a buy-back, and a "
                 "sale listed before a same-day rebuy is made from the "
                 "shares held before it. A Questrade file written by "
                 "`taxjson fetch` keeps the API's row order within a day, "
                 "and a re-fetch merge keeps it too. A newest-first export "
                 "is read "
                 "bottom-up; rows of different accounts at one moment "
                 "follow the accounts' order in taxjson.toml. Rows that "
                 "settle on the same day but traded on different days "
                 "(a Friday trade and the next trading day's trade both "
                 "settling after a settlement holiday) go in TRADE order, "
                 "the earlier trade date first. Fixed places "
                 "at one "
                 "moment: an opening balance first, then a split (effective "
                 "at the open), an assignment's option leg before its "
                 "stock leg, then the trades; cost adjustments last."),
            Rule("CA-DATE-11",
                 "Interest and other income belong to the year they are "
                 "PAID."),
            Rule("CA-INC-DATE-DIV",
                 "A corporation's dividend, Canadian or foreign, belongs "
                 "to the year it is PAID (s.82(1)), not the record or "
                 "ex-dividend date."),
            Rule("CA-INC-DATE-PIL",
                 "A payment in lieu of a dividend belongs to the year it "
                 "is paid."),
            Rule("CA-INC-DATE-ROC",
                 "A corporation's return of capital (s.53(2)(a)) and a "
                 "foreign issuer's lower the ACB on the date they are "
                 "PAID."),
            Rule("CA-INC-DATE-ROC-TRUST",
                 "A Canadian trust's return of capital (T3 box 42) lowers "
                 "the ACB when it becomes PAYABLE (s.53(2)(h)): on the "
                 "record date the export prints (Questrade and RBC \"REC "
                 "mm/dd/yy\"), so a sale between the record date and a "
                 "January pay date is on the reduced ACB. IB prints no "
                 "record date: the pay date is used, and a January-paid "
                 "one is warned about (check the prior year's T3 box 42 "
                 "and move it to Dec 31 with a .tt ADJUST pair; the "
                 "warning stops once that pair is in the books). Every "
                 "engine pass dates it the same way (run, audit, explain, "
                 "the web what-if). When the record date falls in the "
                 "year before the pay date, the pay-year run names, as "
                 "ATTENTION, each sale of that earlier year whose ACB it "
                 "lowers (that year may be filed without it).",
                 keys=("corporate_distributions",)),
            Rule("CA-INC-DATE-TRUST",
                 "A Canadian trust's distribution belongs to the year it "
                 "became PAYABLE (s.104(13)): a row the broker calls a "
                 "distribution (\"DIST ON\", RBC \"Distribution\") on a "
                 "Canadian issuer (a Canadian listing or a CA ISIN) is "
                 "dated by its printed record date — in divs-sum, the "
                 ".sum, the estimate, instalments and the divs / roc / "
                 "events views' windows (each row still shows its pay "
                 "date). Split-share "
                 "corporations say \"Distribution\" too but are "
                 "corporations (paid date): "
                 + ", ".join(sorted(_split_share_roots())) + ", any row "
                 "whose description says \"SPLIT CORP\", and the "
                 "issuers in corporate_distributions"
                 + (f" ({', '.join(_corp_list(s))})" if _corp_list(s)
                    else "") + " — every class and series of each root. "
                 "A record date 92 days or more before the pay date (or "
                 "after it) is not used: the pay date is, with an "
                 "ATTENTION line. A distribution its record date moves "
                 "into another year than its pay date is listed on the "
                 "console (ATTENTION): one of the two project years "
                 "leaves it out. The tax withheld on a payment is dated "
                 "with it. A foreign fund keeps the pay date, and so does an IB "
                 "row: IB prints no record date and calls a trust's "
                 "distribution a dividend, so a trust cannot be told from "
                 "a corporation (the ex date IB's accruals give is not "
                 "used). The T3 slip is authoritative.",
                 keys=("corporate_distributions",)),
            Rule("CA-DATE-12",
                 f"Crypto is dated in local time: {tz} ([settings] "
                 f"local_timezone; outside a project TAXJSON_LOCAL_TZ). "
                 f"Changing it re-dates the rows and re-keys crypto "
                 f"sends.", keys=("local_timezone",)),
            Rule("CA-DATE-13",
                 "A .tt line has one date, used as both its trade and its "
                 "settle date; `taxjson-convert-tt` writes a book's rows "
                 "with the project's tax_date."),
        ]),
        ("Currency", [
            Rule("CA-FX-01",
                 "Every amount is converted to CAD at the Bank of Canada "
                 "rate for its date (the settle date for trades);",
                 keys=("base_currency",)),
            Rule("CA-FX-03",
                 "the daily average from 2017-03-01, the noon rate before "
                 "(from 2007-05-01); Yahoo only before May 2007, for a "
                 "currency the Bank does not publish, or a series it "
                 "stopped.", cont=True),
            Rule("CA-FX-02",
                 "The rates file carries each rate over weekends and "
                 "holidays for up to 7 days, and a day with no row there "
                 "uses the latest row of the 5 days before, so a rate up "
                 "to 12 days old is used (real Bank of Canada gaps are 4 "
                 "days or less); a longer gap converts the row at a "
                 "placeholder rate (1.35 for USD->CAD) and is a "
                 "validation ERROR (the .sum "
                 "DIAGNOSTICS, `taxjson checklist`; `run --strict` "
                 "stops), and a currency with no rates at all stops the "
                 "run. `taxjson fx-cash` counts a cash event with no rate "
                 "row in those 5 days as unrated (named in its report).",
                 cont=True),
            Rule("CA-FX-04",
                 "A futures contract is booked on its settled P/L: nothing "
                 "is paid to open one, so its notional is never converted. "
                 "Each close's P/L (commissions included, average cost of "
                 "the open contracts; a negative price keeps its sign) is "
                 "converted at that closing leg's rate;"),
            Rule("CA-FX-05",
                 "Schedule 3 shows a gain as proceeds and a loss as ACB.",
                 cont=True),
            Rule("CA-FX-06", "Options on futures are ordinary options.",
                 cont=True),
            Rule("CA-FX-07",
                 "Gains on holding foreign cash (s.39(1.1)) are NOT in the "
                 "Schedule 3 totals: `taxjson fx-cash` estimates the "
                 "year's net gain or net loss beyond the $200 annual "
                 "exemption (a net gain or loss within $200 is nil), from "
                 "a pooled average cost per currency. Cash moves only on "
                 "a trade for cash, income, withholding and fees; a "
                 "coin-for-coin swap, a fee paid in a coin and a reward "
                 "in a coin move none (a USD stablecoin is US-dollar "
                 "cash, CA-CRYPTO-02).",
                 keys=("fx_cash_gains",)),
        ]),
        ("Cost base (ACB)", [
            Rule("CA-ACB-01",
                 "Average cost (s.47): one pool per identical property "
                 "across all your taxable accounts."),
            Rule("CA-ACB-02",
                 "Purchase commissions add to the ACB; sale commissions "
                 "are outlays.", cont=True),
            Rule("CA-ACB-COMMREFUND",
                 "A commission refunded later (an IB Commission "
                 "Adjustments row naming the trade) is netted against that "
                 "trade's commission: a lower ACB for a purchase, a "
                 "smaller outlay for a sale. The trade may be in another "
                 "statement of the account (a December trade refunded in "
                 "January); a refund naming one execution of an order "
                 "nets against that order. A refund that names no single "
                 "trade stays a separate fee, with a note.", cont=True),
            Rule("CA-ACB-03",
                 "The single pool needs a full `taxjson run` (not "
                 "`--account`, and no elections pending).", cont=True),
            Rule("CA-ACB-04",
                 "Identical property is the same symbol with its listing "
                 "suffix (.TO, .US). A Canadian listing is one symbol "
                 "whatever venue the input names: ROOT.TO, with a TSX "
                 "preferred series dotted (FTN.PR.A.TO) — .V (on a CAD "
                 "row), .VN, .CN and .NE fold into .TO for broker exports "
                 "and .tt lines alike. Two other listings are one "
                 "security only when ticker.map joins them. Renames and "
                 "splits carry the pool forward (CA-ACB-RENAME)."),
            Rule("CA-ACB-RENAME",
                 "A ticker change is a dated event (a broker corporate-"
                 "action row, a .tt SPLIT line, or a ticker.map line "
                 "`RENAME OLD NEW YYYY-MM-DD`): on that date the pool, "
                 "its ACB and acquisition dates carry from OLD to NEW, and "
                 "the superficial-loss rule treats OLD before the date and "
                 "NEW after it as identical property. A trade in OLD after "
                 "the date is a different security unless ticker.map "
                 "says it is the renamed shares (`late=fold`, booked as "
                 "NEW); `late=separate` records another company reusing "
                 "the ticker. Such trades are listed by `taxjson renames` "
                 "and stop `run --strict` until declared. An undated "
                 "rename (GLOBAL, or RENAME without a date) applies to "
                 "every row of OLD.", cont=True),
            Rule("CA-ACB-05",
                 "Accounts typed \"sheltered\" (RRSP, TFSA, FHSA, LIRA, "
                 "RESP...) are tracked but kept out of the filing totals. "
                 "For the superficial-loss rule they count as affiliated "
                 "holders."),
            Rule("CA-STKDIV-01",
                 "A stock dividend's new shares enter the pool at $0 cost. "
                 "Its declared amount (a dividend, and by law also the new "
                 "shares' cost) is not in the broker's export: add it "
                 "(distributions.map or a .tt ADJUST) — that books the ACB "
                 "only; the dividend itself is reported from the T5/T3 "
                 "slip (taxjson does not count it as income). A taxable "
                 "run of the dividend's year says so until the cost is in "
                 "the books. The new shares are "
                 "an acquisition for the superficial-loss rule. Shares of "
                 "ANOTHER security (another class) paid as a stock "
                 "dividend are not booked: the parse says UNBOOKED; enter "
                 "them by hand."),
            Rule("CA-DIST-01",
                 "distributions.map: a non-cash distribution (a reinvested "
                 "capital-gains distribution, a late return-of-capital "
                 "factor) becomes an ACB adjustment sized on the shares "
                 "held on its record date — the settled position, each "
                 "ticker's own shares — and booked on those shares only "
                 "(a trade straddling the record date is not the "
                 "holder's). Its "
                 "income is on the T3/T5 slip; taxjson does not count "
                 "it. A return-of-capital row warns when the book already "
                 "has that ROC or still counts its cash as a dividend."),
            Rule("CA-DIST-02",
                 "An RBC \"NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK COST\" "
                 "row raises the ACB by its amount (a reinvested "
                 "distribution); the distribution itself is income on the "
                 "fund's T3 (usually box 21) and is NOT counted — the "
                 "parse warns: take it from the slip."),
            Rule("CA-DIST-03",
                 "A reinvested cash distribution (DRIP: Questrade REI, RBC "
                 "REI or \"Reinvest @\") is the income row plus a purchase "
                 "of the new units at the amount reinvested — their cost, "
                 "and an acquisition for the superficial-loss rule."),
            Rule("CA-ACB-06", "Return of capital lowers the ACB "
                 "(`roc-sum` totals it against T3 box 42)."),
            Rule("CA-ACB-07",
                 "Received with no shares held, or beyond the ACB, it is a "
                 "capital gain and the ACB is nil (s.40(3)).", cont=True),
            Rule("CA-ACB-13",
                 "A basis increase (a notional distribution) posted after "
                 "the position was fully sold has no shares to raise: it "
                 "goes into the next purchase's ACB, with a warning to "
                 "re-date it before the sale."),
            Rule("CA-ACB-14",
                 "An ADJUST on a short position is the short seller's "
                 "compensation payment: it changes the cover's gain.",
                 cont=True),
            (Rule("CA-ACB-08",
                  "For IB only, a foreign issuer's return of capital (by "
                  "ISIN) is a dividend (s.90(1); foreign_return_of_capital "
                  "= \"dividend\"). Other brokers always lower the ACB.",
                  keys=("foreign_return_of_capital",))
             if froc == "dividend" else
             Rule("CA-ACB-09",
                  "Return of capital lowers the ACB for every issuer "
                  "(foreign_return_of_capital = \"acb\").",
                  keys=("foreign_return_of_capital",))),
            Rule("CA-ACB-10",
                 "A transfer into a taxable account stops the run until "
                 "the original purchase is declared (.tt ACQUIRED line)."),
            Rule("CA-ACB-11",
                 "Shares with missing buy history go in phantoms.json: "
                 "sales that draw on them are listed for manual reporting "
                 "and left out of the totals, with no superficial-loss "
                 "test, until the position is fully sold.", cont=True),
            Rule("CA-ACB-12",
                 "A loss within 30 days (settle dates) of such a sale, or "
                 "such a sale at a loss with a purchase in that window, is "
                 "flagged for a manual superficial-loss check.",
                 cont=True),
        ]),
        ("Dispositions (Schedule 3)", [
            Rule("CA-DISP-01", "Gain = proceeds - ACB - outlays."),
            Rule("CA-DISP-02",
                 "taxjson reports full gains; half the net gain is "
                 "taxable, which you apply on Schedule 3 (line 12700).",
                 cont=True),
            Rule("CA-DISP-03",
                 "Line 4 (13199/13200): shares, fund units and other "
                 "securities. Line 6 (15199/15300): options and futures. "
                 "Line 7 (15200/15301): accounts marked crypto, from 2025; "
                 "on 15199/15300 before."),
            Rule("CA-DISP-04",
                 "A short sale's gain or loss is realized when it is "
                 "covered."),
            Rule("CA-DISP-05",
                 "Cash-settled options (no stock leg) realize their gain "
                 "or loss on the option itself.", cont=True),
            Rule("CA-DISP-06",
                 "A written option's premium recognised at the write "
                 "(grant timing) is shown gross as proceeds, with its "
                 "commission as an outlay, as for a sale (the gain is the "
                 "same)."),
            Rule("CA-DISP-07",
                 "`form-export` gives Schedule 3's year of acquisition as "
                 "the sale's trade date less the days held, counted on "
                 "trade dates: a buy traded in late December that settled "
                 "in January shows the December year."),
            Rule("CA-DISP-08",
                 "Each Schedule 3 cell is rounded half-up to the cent and "
                 "the ACB is the row's footing residual, never below 0.00 "
                 "(a cent of rounding goes to the outlays). A net "
                 "commission rebate (a negative commission or fee) is not "
                 "an outlay: it stays netted in the proceeds, so OUTLAYS "
                 "is never negative."),
        ]),
        ("Superficial loss (s.54)", [
            Rule("CA-SL-01",
                 "A loss is denied when identical property is acquired "
                 "within 30 days before or after the sale (settle dates)"),
            Rule("CA-SL-02", "and is still held at the end of day 30,",
                 cont=True),
            Rule("CA-SL-03", "in your taxable or sheltered accounts.",
                 cont=True),
            Rule("CA-SL-04",
                 "(A spouse's or controlled corporation's trades count only "
                 "when given: `taxjson-gains --affiliated`, or in a project "
                 "their account declared type = \"sheltered\", which "
                 "denies the loss for good and lists that account as if it "
                 "were your registered plan.)", cont=True),
            Rule("CA-SL-05",
                 "A long call on the shares is identical property to them "
                 "(a right to acquire, s.54 para (i)), at its contract size "
                 "(100 shares for a standard equity option, the declared "
                 "size of a mini). A root that drops the share class (RCI "
                 "for RCI.B.TO) names that class line."),
            Rule("CA-SL-06",
                 "Shares never replace an option; an option is replaced "
                 "only by the identical contract; a put never replaces "
                 "the shares.", cont=True),
            Rule("CA-SL-14",
                 "A warrant or right bought in the window is flagged for a "
                 "manual superficial-loss check only (the shares it "
                 "converts into are not in the books).", cont=True),
            Rule("CA-SL-15",
                 "So is a call on an adjusted option series (root + digit, "
                 "e.g. XYZ1) or a futures option on the loss's futures "
                 "contract, however it is spelled (never sized as 100 "
                 "units).", cont=True),
            Rule("CA-SL-07",
                 "Only purchases count: writing an option or shorting "
                 "again never replaces, including after a loss on covering "
                 "a short."),
            Rule("CA-SL-08",
                 "Only units acquired in the window and still held count, "
                 "per holder, and each one backs a single denial (a sale "
                 "split into fills, or two losses, share it; losses at "
                 "the same moment claim in the export's row order). A "
                 "held call contract backs one denial however often its "
                 "series was bought and sold in the window. The denied "
                 "part is loss x (those units / units sold), capped at the "
                 "whole loss."),
            Rule("CA-SL-09",
                 "The denied amount is added to the replacement's ACB from "
                 "its acquisition (a sale listed after it at the same "
                 "moment uses the raised ACB) and "
                 "comes back when it is sold. If the replacement is in a "
                 "sheltered account, that part is lost for good; one "
                 "bought by an affiliated person (--affiliated) is denied "
                 "on your return too, and that person adds it to their "
                 "own ACB (s.53(1)(f))."),
            Rule("CA-SL-10",
                 "Replacements are matched in acquisition order: purchases "
                 "after the sale first, then earlier ones, latest first. "
                 "Purchases at the same moment go to your taxable accounts "
                 "first, then sheltered, then affiliated, then in the "
                 "export's row order (accounts in taxjson.toml order); a "
                 "purchase listed after a sale at the same moment is a "
                 "purchase after it."),
            (Rule("CA-SL-12",
                  "A loss on buying back a written option (or, under "
                  "grant timing, on a write whose commission exceeds its "
                  "premium) is superficial when the same option is "
                  "bought, and still held at day 30, within the window "
                  "(option_buyback_loss_superficial = true).",
                  keys=("option_buyback_loss_superficial",))
             if buyback else
             Rule("CA-SL-11",
                  "A loss on buying back a written option (or, under "
                  "grant timing, on a write whose commission exceeds its "
                  "premium) is exempt from the rule under either premium "
                  "timing (option_buyback_loss_superficial = false).",
                  keys=("option_buyback_loss_superficial",))),
            Rule("CA-SL-13",
                 "Crypto follows the same rule, pooled across exchanges "
                 "when two or more crypto accounts are configured."),
        ]),
        ("Options (s.49)", prem + [
            Rule("CA-OPT-06",
                 "Exercise or assignment: the premium folds into the "
                 "shares' cost or proceeds (s.49(3) for a call, s.49(3.1) "
                 "for a put; the grant year is amended under s.49(4))."),
            Rule("CA-OPT-08",
                 "Each assignment's premium goes to its own stock leg: the "
                 "same account and underlying, the delivered quantity "
                 "(contracts x the declared contract size, else 100; one "
                 "per futures option), priced at the strike, dated up to 3 "
                 "days before or 7 days after the option row. Several "
                 "assignments at one moment are told apart by strike, "
                 "never by row order.", cont=True),
            Rule("CA-OPT-07",
                 "If the premium's year was already filed, `taxjson "
                 "option-boundary` flags the T1-ADJ.", cont=True),
            Rule("CA-OPT-09",
                 "Exercising a warrant or right is not a disposition: its "
                 "cost and the exercise price paid become the shares' ACB "
                 "(s.49(3)). The parser names the shares on the warrant "
                 "leg (IB `Ex` legs, RBC `Exercise` rows, paired by date); "
                 "IB leaves a leg it cannot pair a disposal at 0 with an "
                 "ATTENTION line, RBC refuses the file.", cont=True),
        ]),
        ("Corporate actions (elections in the account manifest)", [
            Rule("CA-CORP-01",
                 "Splits and consolidations scale the quantity; the ACB "
                 "is unchanged."),
            Rule("CA-CORP-02", "Name changes carry the pool automatically.",
                 cont=True),
            Rule("CA-CORP-03",
                 "Mergers: taxable_disposition (old shares sold at FMV; "
                 "new shares cost FMV)"),
            Rule("CA-CORP-04",
                 "or rollover_s_85_1_5 (share-for-share; cost carries "
                 "over).", cont=True),
            Rule("CA-CORP-05",
                 "Cash for fractional shares is handled; other cash in a "
                 "merger is not modelled.", cont=True),
            Rule("CA-CORP-06",
                 "Spin-offs: rollover_s_86_1 (ACB split between the two "
                 "by the CAD amount you enter, s.86.1(3), booked exactly "
                 "even on a foreign listing; file the s.86.1 election)"),
            Rule("CA-CORP-07",
                 "or taxable_deemed_dividend (a dividend at FMV, which is "
                 "also the new shares' cost).", cont=True),
            Rule("CA-CORP-08",
                 "ignore skips broker noise only; on a real event it "
                 "leaves the books wrong."),
        ]),
        ("Income", [
            Rule("CA-INC-01",
                 "Dividends on Canadian listings (.TO, .V, .CN, .NE) count "
                 "as Canadian, all others as foreign."),
            Rule("CA-INC-02",
                 "Take the return's line numbers from your slips.",
                 cont=True),
            Rule("CA-INC-03",
                 "A payment in lieu of a dividend is ordinary income (no "
                 "gross-up or credit), EXCEPT one on a Canadian issuer's "
                 "share (a Canadian listing or a CA ISIN) paid by a "
                 "Canadian dealer (IB's statement names Interactive "
                 "Brokers Canada Inc.; Questrade and RBC Direct are "
                 "Canadian dealers, and their 'IN LIEU OF DIVIDEND' rows "
                 "are payments in lieu): ITA "
                 "s.260(5)/(5.1) deems that a taxable dividend — "
                 "eligible in the estimate, counted in divs-sum, and on "
                 "the dealer's T5 box 24. The slip is authoritative."),
            Rule("CA-INC-04",
                 "Crypto staking rewards are income at fair value when "
                 "received; that value is the coins' cost."),
            Rule("CA-INC-05",
                 "Dividends are booked gross; withholding tax is its own "
                 "TAX row."),
            Rule("CA-INC-06",
                 "A capital-gains dividend (T5 box 18, line 17400: a split-"
                 "share or mutual-fund corporation's, ITA s.130.1(4)/"
                 "s.131(1)) is a capital gain, not a dividend. No export "
                 "labels it, so the books carry it as a dividend; list it "
                 "in capital_gains_dividends.map (symbol — a bare root "
                 "covers only its Canadian listings, never a preferred "
                 "series or a foreign listing —, the year of its tax date "
                 "or its pay date, amount or `all`) and divs-sum shows it "
                 "apart while "
                 "the estimate taxes it as a capital gain (50% inclusion, "
                 "no gross-up or credit) and `taxjson carryover` adds it "
                 "to its year's net capital gain or loss. ACB is "
                 "unchanged."),
        ]),
        ("Crypto", [
            Rule("CA-CRYPTO-01",
                 "Each coin is its own property. A coin-for-coin trade is "
                 "a sale of one and a purchase of the other at fair "
                 "value. Kraken's staked and bonded wallet codes (DOT.S, "
                 "DOT28.S, ETH2, ETH2.S, the .M/.F/.B/.P/.HOLD suffixes) "
                 "name the same coin as the bare code, so a 1:1 swap "
                 "between them is not a sale."),
            Rule("CA-CRYPTO-09",
                 "Any amount of a coin is property: a residue left after a "
                 "sale, however small, stays in the holdings with its "
                 "share of the cost (only arithmetic noise, under a "
                 "hundred-billionth of the position, counts as zero). A "
                 "share position under a millionth of a share counts as "
                 "zero.", cont=True),
            Rule("CA-CRYPTO-02",
                 "USD stablecoins (USDC, USDT, DAI, PYUSD and GUSD, on "
                 "Kraken and Coinbase alike) are treated as US-dollar "
                 "cash, an approximation (their own gain or loss, a "
                 "de-peg, is not computed; a fill valued in US dollars "
                 "more than 2% off 1.00 USD is warned about — a fill "
                 "valued in another currency is not checked)."),
            Rule("CA-CRYPTO-03",
                 "A Kraken fee paid in a coin is a sale of that coin: on a "
                 "move of coins (a withdrawal, a deposit, a transfer to "
                 "another Kraken user or a Hybrid Earn withdrawal), on a "
                 "fiat deposit or withdrawal, or on a staking reward."),
            Rule("CA-CRYPTO-04",
                 "A trade fee taken in a coin reduces the coins bought or "
                 "adds to the coins sold.", cont=True),
            Rule("CA-CRYPTO-05",
                 "Moving coins between your own wallets is not a sale. A "
                 "gift or a payment in crypto is a sale at fair value."),
            Rule("CA-CRYPTO-06",
                 "A send that arrives on another of your exchanges (or the "
                 "same exchange in another crypto account) from 10 minutes "
                 "before it (exchange clocks disagree) to 3 days after it, "
                 "with 90% to 100% of the coins sent — also as two deposits, "
                 "or two sends landing as one deposit — is treated as your "
                 "own move. Sends and arrivals are paired to pair the most "
                 "sends, then lose the fewest coins, then the closest in "
                 "time. A Kraken Hybrid Earn withdrawal is never paired (the "
                 "coins stay on Kraken: your own, decided automatically). A "
                 "saved gift or payment for a send that pairs is not booked "
                 "and is warned about (`run --strict` stops) until you "
                 "confirm it as `self` or unpair it (`--unpair`). When fewer "
                 "coins arrive and the sending "
                 "exchange states no fee (a Coinbase Send hides the "
                 "network fee in the quantity), the coins that did not "
                 "arrive paid the network fee: a sale of them at fair "
                 "value, written to crypto_sends.tt, as a Kraken "
                 "withdrawal fee is (CA-CRYPTO-03; a stablecoin's is cash, "
                 "CA-CRYPTO-02);",
                 cont=True),
            Rule("CA-CRYPTO-07",
                 "for every other send, `taxjson crypto-sends` records "
                 "whether it was your own wallet, a gift or a payment, and "
                 "writes a sale at fair value for each gift or payment to "
                 "crypto_sends.tt: the exchange's price when the row has "
                 "one, otherwise the Yahoo daily close times the Bank of "
                 "Canada rate of the send date, or a price you give "
                 "(`--price`, finite and at least 0.00000001; a network fee "
                 "takes one too). A sale that cannot be priced is not "
                 "booked: it is warned about and `run --strict` stops. "
                 "When sends.json cannot be read, a crypto_sends.tt "
                 "written from earlier decisions is not booked: `taxjson "
                 "run` stops until it is fixed.",
                 cont=True),
            Rule("CA-CRYPTO-08",
                 "A gift or payment of a stablecoin is not written as a "
                 "sale (stablecoins are cash in the books). It is a "
                 "currency disposition: the value at the send date's Bank "
                 "of Canada rate minus the average cost of the US-dollar "
                 "and stablecoin pool. A loss is treated as superficial "
                 "— the whole loss excluded, a conservative reading of "
                 "the pro-rata rule — when US dollars or stablecoins were "
                 "acquired within 30 days and are still held."),
        ]),
        ("Reports", [
            Rule("CA-RPT-01",
                 "`taxjson t1135`: Form T1135 is required when the total "
                 "cost of specified foreign property in taxable accounts "
                 "exceeds $100,000 at any time in the year."),
            Rule("CA-RPT-02",
                 "Country comes from the listing suffix (t1135.map "
                 "overrides; a foreign listing whose rows carry a "
                 "Canadian ISIN is named for a `SYMBOL CA` line, since a "
                 "Canadian corporation's shares are not foreign "
                 "property); crypto held on an exchange counts.",
                 cont=True),
            Rule("CA-RPT-12",
                 "A property's cost amount is its adjusted cost base as "
                 "the gains engine computes it, day by day over the full "
                 "history: a superficial loss denied in any year is added "
                 "to the replacement's cost (s.53(1)(f)), an option's "
                 "premium follows the shares on exercise or assignment "
                 "(s.49(3)), and a futures contract has no cost amount."),
            Rule("CA-RPT-03",
                 "`taxjson estimate`: federal and provincial tax (ON, BC, "
                 "AB) with AMT on top of your other income, for planning "
                 "only.", keys=("province",)),
            Rule("CA-RPT-04",
                 "Canadian dividends (a Canadian issuer: its CA ISIN when "
                 "the export gives one, else a Canadian listing) are "
                 "treated as eligible (38% gross-up "
                 "and credit; a capital-gains dividend in "
                 "capital_gains_dividends.map as a capital gain),",
                 cont=True),
            Rule("CA-RPT-05",
                 "foreign dividends as ordinary income with withholding "
                 "credited up to 15%;", cont=True),
            Rule("CA-RPT-06", "interest is left out.", cont=True),
            Rule("CA-RPT-10",
                 "`taxjson carryover`: the net-capital-loss ledger in 100% "
                 "amounts (the inclusion rate is applied on the return); a "
                 "loss carries forward with no time limit and back up to 3 "
                 "years (form T1A). Every year is recomputed with the "
                 "project's own settings (option timing, tax_date, income "
                 "dating); a year before the project year that has a "
                 "close-year lock (filed/<year>.json or prior_year_record) "
                 "takes the lock's FILED gain instead — the total filed "
                 "with another tool, else the Schedule 3 gain lines — and "
                 "a locked later year is compared with it. A year after "
                 "the project year is partial: no carry-back is offered "
                 "and the carryforward stops at the project year."),
            Rule("CA-RPT-11",
                 "`taxjson instalments`: CRA instalments (ITA s.156) when "
                 "net tax owing exceeds $3,000 this year and in one of the "
                 "two previous years — due March, June, September and "
                 "December 15 (the next business day on a weekend), on the "
                 "current-year, prior-year or CRA-reminder basis, with "
                 "s.161 interest at CRA's prescribed rate. A payment "
                 "made before January 1 counts only when its row says "
                 "`tax_year = YEAR`, and earns credit from January 1."),
            Rule("CA-RPT-07",
                 "`taxjson edge-cases`: every trade whose year or "
                 "superficial-loss verdict turns on a boundary — window "
                 "days counted on settlement dates as the engine counts "
                 "them, whatever tax_date says; income in the year its "
                 "dating rule gives it; crypto by its local and UTC "
                 "dates in local_timezone; written options against the "
                 "filed locks (prior_year_record included)."),
            Rule("CA-RPT-08",
                 "`taxjson close-year` records each closed year's sales, "
                 "year-end positions and cost (each superficial-loss "
                 "addition where the engine lands it, so a January "
                 "replacement's share is not in the Dec 31 cost), and "
                 "trades settling in January; `taxjson handoff` checks the next year starts "
                 "from exactly that, so no sale is reported twice or "
                 "never. It also flags a written option carried out of "
                 "the closed year that this project puts on another "
                 "premium timing than the record (taxed twice, or in no "
                 "return), and income or a sale the two projects date "
                 "on different sides of Dec 31 (a trust's record date, a "
                 "local_timezone re-dating), so it is reported once; "
                 "`option-boundary` and `handoff` read last year's record "
                 "through prior_year_record."),
            Rule("CA-RPT-09",
                 "The record states its country: `check-filed` and "
                 "`handoff` refuse one closed under US rules instead of "
                 "recomputing it under Canadian law.", cont=True),
        ]),
        ("Planning tools (wash radar, sell-check, buy-check, harvest, "
         "watch, web)", [
            Rule("CA-PLAN-01",
                 "They apply the superficial-loss rule above on settle "
                 "dates, each replacement unit backing one denial (an "
                 "earlier loss's claim, even one whose window has closed, "
                 "is spent; quantities across a split are compared in "
                 "today's units): a loss whose replacement is still held "
                 "can be rescued by selling the replacement so that it is "
                 "no longer held when day 30 settles (VIOLATION prints the "
                 "last trade date that does it, on the listing's "
                 "calendar; once that date has passed it says the loss is "
                 "denied)."),
            Rule("CA-PLAN-02",
                 "A long call on the shares bought in the window counts "
                 "as a replacement at its contract size (buy-check states "
                 "the denial per share and per standard contract); a "
                 "warrant, an "
                 "adjusted-series call or a futures option is a note to "
                 "check by hand.", cont=True),
            Rule("CA-PLAN-03",
                 "The web what-if runs a taxable sale on the blended s.47 "
                 "pool of the taxable accounts of its kind, with the "
                 "registered accounts as context, so a sibling account's "
                 "purchase in the window denies the loss as the filing "
                 "would. It prices an option at the contract size the "
                 "book's rows declare (100 for an equity option with "
                 "none), settles the sale on the listing's market "
                 "calendar whatever currency the price is typed in, "
                 "books a trust's return of capital on its record date "
                 "first, refuses a plain futures contract (its gain is "
                 "the settled P/L), and lists the engine's warn-only "
                 "replacement flags for the sale."),
            Rule("CA-PLAN-04",
                 "Their verdicts cover the project's own accounts only and "
                 "say so: a purchase by your spouse or common-law partner, "
                 "or by a corporation you or they control (affiliated "
                 "persons, s.251.1), also makes a loss superficial, and "
                 "those accounts are not in the project."),
        ]),
        ("Project country", _ownership(c)),
    ]


def _usa(s: Dict[str, Any]) -> List[RuleSection]:
    c = _C.USA
    basis = _C.resolve_tax_date(c, s.get("tax_date"))
    fut = _C.futures_settle_mode(s)
    return [
        ("Tax year and dates", [
            (Rule("US-DATE-01",
                  "A trade belongs to the year it is TRADED (tax_date = "
                  "\"trade\", IRS).", keys=("tax_date",))
             if basis == "trade" else
             Rule("US-DATE-02",
                  "A trade belongs to the year it SETTLES (tax_date = "
                  "\"settle\"; the IRS uses the trade date).",
                  keys=("tax_date",))),
            Rule("US-DATE-04",
                 "Settle dates come from the broker when printed (one "
                 "earlier than the trade date is refused; one more than 7 "
                 "days after it is booked as printed and flagged). "
                 "Otherwise: T+1 (from 2024-05-28 in USD, 2024-05-27 in "
                 "CAD and MXN), T+2 from 2017-09-05, T+3 before; other "
                 "markets T+2 — from 2014-10-06 in the UK, the EU and "
                 "Switzerland (T+1 from 2027-10-11 in every EU currency, "
                 "sterling and the Swiss franc), 2016-03-07 in Australia "
                 "and New Zealand, 2018-12-10 in Singapore and 2019-07-16 "
                 "in Japan (T+3 before each), always in Hong Kong, and on "
                 "the North-American dates elsewhere; options T+1, but an "
                 "exercise or assignment takes its stock leg's date."),
            Rule("US-DATE-05",
                 "Days skip weekends and settlement holidays (US: NYSE and "
                 "Federal Reserve holidays; Canada: TSX holidays, "
                 "Remembrance Day, Truth and Reconciliation; elsewhere "
                 "weekends only). The cycle and calendar are the listing's "
                 "market, not the quote currency's: an IB US-dollar unit "
                 "listed on the TSX settles on the Canadian calendar, and "
                 "a US-dollar line listed on the LSE is an LSE security "
                 "(.L) on the UK cycle.", cont=True),
            Rule("US-DATE-06",
                 "The generic importer uses a mapped settle column (one "
                 "more than 31 days after the trade is refused, more than "
                 "7 is flagged), else this cycle (settle_on_trade_date = "
                 "true keeps the trade date); its futures and its $0 "
                 "option closes on the expiry day follow the two rules "
                 "below.", cont=True),
            Rule("US-DATE-07", "Crypto settles on the trade date;",
                 cont=True),
            Rule("US-DATE-08", "an option expiry is dated its expiry day.",
                 cont=True),
            (Rule("US-DATE-09",
                  "Futures and futures options settle on the TRADE date "
                  "(futures_settle = \"trade\": variation margin settles "
                  "the P/L daily).", keys=("futures_settle",))
             if fut == "trade" else
             Rule("US-DATE-12",
                  "Futures and futures options settle on the next "
                  "settlement day (futures_settle = \"next_day\").",
                  keys=("futures_settle",))),
            Rule("US-DATE-SESSION",
                 "A trade is dated by its exchange's trade date, not the "
                 "broker's clock: IB stamps US Eastern time, so a US stock "
                 "or ETF filled in the overnight session (20:00 ET or "
                 "later, Sunday to Thursday nights, and its after-midnight "
                 "part on a day the NYSE is closed) trades on the NEXT "
                 "trading day and settles from it (a Dec 30 20:30 fill "
                 "trades Dec 31 and settles in January). So does a "
                 "US-dollar futures or futures-option fill in the CME "
                 "evening session (18:00 ET or later, Sunday to Thursday, "
                 "or on a weekday the exchange is closed) and an SPX, "
                 "SPXW, XSP or VIX option filled in Cboe Global Trading "
                 "Hours (20:15 ET or later). A fill on the ASX, HKEX, "
                 "Tokyo, Singapore or NZX exchanges (an AUD, HKD, JPY, SGD "
                 "or NZD row, any asset class) is dated in the exchange's "
                 "local time. Every other fill keeps the clock date. A "
                 "moved fill sorts before that day's other trades; the "
                 "broker's stamp is kept (broker_time)."),
            Rule("US-DATE-13",
                 "Rows at the same date and time keep the export's row "
                 "order (Webull and the generic importer print no clock "
                 "time, Questrade stamps midnight): a write listed before "
                 "its same-day buy-back is a short sale closed by the "
                 "buy-back, and FIFO takes same-moment lots in that order. "
                 "A Questrade file written by `taxjson fetch` keeps the "
                 "API's row order within a day, and a re-fetch merge keeps "
                 "it too. "
                 "A newest-first export is read bottom-up; rows of "
                 "different accounts at one moment follow the accounts' "
                 "order in taxjson.toml. Fixed places at one moment: an "
                 "opening "
                 "balance first, an assignment's option leg before its "
                 "stock leg, a split before the trades; basis adjustments "
                 "last."),
            Rule("US-DATE-03",
                 "Interest and other income belong to the year they are "
                 "paid."),
            Rule("US-INC-DATE-DIV",
                 "Dividends and payments in lieu belong to the year they "
                 "are paid, not the record or ex-dividend date (the "
                 "January fund and REIT dividends below aside)."),
            Rule("US-INC-DATE-ROC",
                 "A return of capital (nondividend distribution) lowers "
                 "basis on the date it is paid."),
            Rule("US-DATE-10",
                 "A .tt line has one date, used as both its trade and its "
                 "settle date; `taxjson-convert-tt` writes a book's rows "
                 "with the project's tax_date."),
            Rule("US-DATE-11",
                 f"Crypto is dated in local time: {_local_tz(s)} "
                 f"([settings] local_timezone; outside a project "
                 f"TAXJSON_LOCAL_TZ). Changing it re-dates the rows and "
                 f"re-keys crypto sends.", keys=("local_timezone",)),
            Rule("US-INC-DATE-RIC",
                 "A fund (RIC) or REIT dividend declared in October-"
                 "December, payable to holders of record then, and paid in "
                 "January is received on Dec 31 (§852(b)(7), §857(b)(9)). "
                 "The exports do not say which payer is a fund: taxjson "
                 "keeps the pay date, WARNS about a January dividend whose "
                 "ex or record date is in October-December, and dates the "
                 "payments listed in ric_january_dividends (\"SYMBOL\" or "
                 "\"SYMBOL YYYY-01-DD\"; a bare root is that fund's US "
                 "listing only, never another class or a .TO listing"
                 + (f"; now: {', '.join(_ric_list(s))}" if _ric_list(s)
                    else "") + ") on Dec 31 of the prior year; a moved "
                 "payment is listed on the console (ATTENTION) in both "
                 "project years, since one of them leaves it out, and the "
                 "tax withheld on it moves with it. Form "
                 "1099-DIV is authoritative.",
                 keys=("ric_january_dividends",)),
        ]),
        ("Currency", [
            Rule("US-FX-01", "Amounts are in USD.",
                 keys=("base_currency",)),
            Rule("US-FX-02",
                 "Other currencies are converted at the Yahoo Finance "
                 "daily rate for the settle date. The rates file carries "
                 "each rate over weekends and holidays for up to 7 days, "
                 "and a day with no row there uses the latest row of the "
                 "5 days before, so a rate up to 12 days old is used; a "
                 "longer gap converts the row at a placeholder rate (for "
                 "CAD->USD the inverse of 1.35, never a USD->CAD rate) "
                 "and is a validation ERROR (`run --strict` stops), and a "
                 "currency with no rates at all stops the run. `taxjson "
                 "fx-cash` counts a cash event with no rate row in those "
                 "5 days as unrated (named in its report).", cont=True),
            Rule("US-FX-03",
                 "Gains on holding foreign cash (§988) are ordinary "
                 "income, not capital gains, and are NOT in the Form 8949 "
                 "totals: `taxjson fx-cash` estimates the year's net from "
                 "a pooled average cost per currency (fx_cash_gains = "
                 "true runs it after `taxjson run`); the §988(e) "
                 "exclusion for personal transactions is not modelled, "
                 "and there is no $200 annual exemption.",
                 keys=("fx_cash_gains",)),
        ]),
        ("Basis and holding period", [
            Rule("US-BASIS-01", "First in, first out per account"),
            Rule("US-BASIS-02",
                 "(specific-lot identification is not supported).",
                 cont=True),
            Rule("US-BASIS-03",
                 "Purchase commissions add to basis; sale commissions "
                 "reduce proceeds.", cont=True),
            Rule("US-BASIS-COMMREFUND",
                 "A commission refunded later (an IB Commission "
                 "Adjustments row naming the trade) is netted against that "
                 "trade's commission: a lower basis for a purchase, higher "
                 "proceeds for a sale. The trade may be in another "
                 "statement of the account (a December trade refunded in "
                 "January); a refund naming one execution of an order "
                 "nets against that order. A refund that names no single "
                 "trade stays a separate fee, with a note.", cont=True),
            Rule("US-HOLD-01",
                 "Long-term when held more than one year, otherwise "
                 "short-term"),
            Rule("US-HOLD-02", "(Rev. Rul. 66-7 for month-end purchases).",
                 cont=True),
            Rule("US-HOLD-03", "A stand-alone short sale is short-term.",
                 cont=True),
            Rule("US-BASIS-06",
                 "Identical property is the same symbol with its listing "
                 "suffix (.US, .TO). A Canadian listing is ROOT.TO "
                 "whatever venue the input names (.V on a CAD row, .VN, "
                 ".CN, .NE; a dotted preferred series), for broker "
                 "exports and .tt lines alike; two other listings are one "
                 "security only when ticker.map joins them; renames are "
                 "dated events (US-BASIS-RENAME)."),
            Rule("US-BASIS-RENAME",
                 "A ticker change is a dated event (a broker corporate-"
                 "action row, a .tt SPLIT line, or a ticker.map line "
                 "`RENAME OLD NEW YYYY-MM-DD`): on that date the basis "
                 "lots and their holding periods carry from OLD to NEW, "
                 "and the wash-sale rule treats OLD before the date and "
                 "NEW after it as one security. A trade in OLD after "
                 "the date is a different security unless ticker.map says "
                 "it is the renamed shares (`late=fold`, booked as NEW); "
                 "`late=separate` records another company reusing the "
                 "ticker. Such trades are listed by `taxjson renames` and "
                 "stop `run --strict` until declared. An undated rename "
                 "(GLOBAL, or RENAME without a date) applies to every row "
                 "of OLD.", cont=True),
            Rule("US-BASIS-05",
                 "A transfer into a taxable account stops the run until "
                 "the original purchase is declared (.tt ACQUIRED line). "
                 "With transfers = false (the default) a move between two "
                 "of your own taxable accounts is flagged ATTENTION: the "
                 "lot keeps its basis and purchase date, but the books do "
                 "not carry it to the receiving account, so its sales "
                 "there are reported by hand (--strict stops)."),
            Rule("US-DIST-01",
                 "distributions.map: a non-cash distribution (a reinvested "
                 "capital-gain distribution, a late return-of-capital "
                 "factor) becomes a basis adjustment sized on the shares "
                 "held on its record date — the settled position, each "
                 "ticker's own shares — and booked on those lots only "
                 "(a trade straddling the record date is not the "
                 "holder's). Its "
                 "income is on Form 1099-DIV; taxjson does not count it. "
                 "A return-of-capital row warns when the book already has "
                 "that ROC or still counts its cash as a dividend."),
            Rule("US-DIST-02",
                 "An RBC \"NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK COST\" "
                 "row raises the basis by its amount (a reinvested "
                 "distribution); the distribution itself is income on Form "
                 "1099-DIV and is NOT counted — the parse warns."),
            Rule("US-DIST-03",
                 "A reinvested cash dividend (DRIP: Questrade REI, RBC REI "
                 "or \"Reinvest @\") is the income row plus a purchase of "
                 "the new shares at the amount reinvested — their basis, "
                 "and a purchase for the wash-sale rule."),
            Rule("US-ROC-01",
                 "A return of capital (nondividend distribution, "
                 "§301(c)(2)) lowers the basis of the shares held, pro rata "
                 "over the open lots, for every issuer (`roc-sum` totals it "
                 "against Form 1099-DIV box 3)."),
            Rule("US-ROC-02",
                 "The part beyond a lot's basis is a capital gain in the "
                 "year received (§301(c)(3)), short- or long-term by that "
                 "lot's holding period; the basis stays at zero.",
                 cont=True),
            Rule("US-ROC-03",
                 "Received with no shares held, it is not applied: taxjson "
                 "warns, and you report it by hand.", cont=True),
            Rule("US-ROC-04",
                 "A basis increase (a notional distribution) with no long "
                 "shares held — after a full sale, or while short — is not "
                 "applied either: taxjson warns on the console (ATTENTION) "
                 "and you adjust the sale by hand."),
            Rule("US-BASIS-04",
                 "Shares with missing buy history go in phantoms.json: "
                 "sales that draw on them are listed for manual reporting "
                 "and left out of the totals. A loss within 30 days "
                 "(trade dates) of such a sale, or such a sale at a loss "
                 "with a purchase in that window, is flagged for a manual "
                 "wash-sale check (not for crypto accounts)."),
            Rule("US-STKDIV-01",
                 "A stock dividend is not income (§305(a)): the basis of "
                 "the shares held is spread over the old and new shares "
                 "(§307), the new shares keep the old shares' purchase "
                 "dates (§1223(5)), and they are not a purchase for the "
                 "wash-sale rule."),
            Rule("US-STKDIV-02",
                 "A taxable stock dividend (§305(b), e.g. one with a cash "
                 "option) is not detected: enter it by hand. Shares of "
                 "ANOTHER security (another class) paid as a stock "
                 "dividend are not booked: the parse says UNBOOKED; enter "
                 "them and the §307 basis split by hand.", cont=True),
        ]),
        ("Wash sales (§1091)", [
            Rule("US-WASH-01",
                 "A loss is disallowed when the same security is bought "
                 "within 30 days before or after the sale (trade dates),"),
            Rule("US-WASH-04",
                 "in any of your accounts, IRAs included;", cont=True),
            Rule("US-WASH-02", "share for share;", cont=True),
            Rule("US-WASH-03",
                 "the identical option is a replacement too.", cont=True),
            Rule("US-WASH-05",
                 "Re-shorting after a short-cover loss also counts.",
                 cont=True),
            Rule("US-WASH-06", "There is no still-held test,", cont=True),
            Rule("US-WASH-17",
                 "but shares (or shorts) closed by the same sale (or "
                 "cover) — one row, or the same-second fills of one order "
                 "— never replace each other's losses; shares kept after "
                 "that sale still do,", cont=True),
            Rule("US-WASH-07",
                 "and look-alike securities are not detected.", cont=True),
            Rule("US-WASH-08",
                 "Matching across accounts needs a full `taxjson run` "
                 "(not `--account`)."),
            Rule("US-WASH-09",
                 "The disallowed loss is added to the replacement lot's "
                 "basis"),
            Rule("US-WASH-10", "and its holding period carries over.",
                 cont=True),
            Rule("US-WASH-11",
                 "A replacement bought in an IRA makes it permanent, even "
                 "when the IRA sold it again before your loss.",
                 cont=True),
            Rule("US-WASH-16",
                 "A purchase by your spouse or a corporation you control "
                 "in the window disallows the loss too, when their trades "
                 "are given (`taxjson-gains --affiliated`; in a project, "
                 "declare their account type = \"sheltered\", which also "
                 "lists it as if it were your IRA). §1091(d) adds the "
                 "loss to the basis of THEIR replacement shares, so in "
                 "your books it is reported as permanently disallowed: "
                 "give them the amount for their basis."),
            Rule("US-WASH-12",
                 "A long call bought in the window is flagged as a warning "
                 "only (\"option to acquire\" is not enforced by the US "
                 "engine), sized at the contract's size (100 shares for a "
                 "standard equity option, the declared size of a mini), "
                 "each contract "
                 "flagged against one loss's shares only; a buy that "
                 "closes a written call is not an acquisition. A root "
                 "that drops the share class (BRKB for BRK.B) names that "
                 "class line."),
            Rule("US-WASH-14",
                 "A warrant or right bought in the window is flagged for a "
                 "manual wash-sale check only.", cont=True),
            Rule("US-WASH-15",
                 "So is a call on an adjusted option series (root + digit, "
                 "e.g. XYZ1) or a futures option on the loss's futures "
                 "contract, however it is spelled (a commodity future is "
                 "usually outside §1091).",
                 cont=True),
            Rule("US-WASH-19",
                 "Not modelled: a SALE of the same stock within 30 days of "
                 "a short-cover loss (§1091(e)(1)) does not disallow it; "
                 "only re-shorting does."),
            Rule("US-WASH-18",
                 "A loss on a futures contract, or on an option on one, is "
                 "never disallowed: a §1256 contract is not stock or "
                 "securities. A re-purchase in the window is flagged for "
                 "a manual check."),
            Rule("US-WASH-13",
                 "Accounts marked crypto are not subject to the wash-sale "
                 "rule."),
        ]),
        ("Corporate actions (elections in the account manifest)", [
            Rule("US-CORP-01",
                 "Splits and consolidations scale the quantity; the basis "
                 "and purchase dates are unchanged."),
            Rule("US-CORP-02", "Name changes carry the lots automatically.",
                 cont=True),
            Rule("US-CORP-03",
                 "Mergers: taxable_exchange (§1001: old shares sold at "
                 "FMV; new shares cost FMV),"),
            Rule("US-CORP-04",
                 "reorg_368 (all-stock §368(a) reorganization: basis "
                 "carries over, §358, and the holding period tacks, "
                 "§1223(1); tax-free by law when it qualifies, and only a "
                 "significant holder attaches the Reg. §1.368-3 "
                 "statement),", cont=True),
            Rule("US-CORP-05",
                 "or reorg_368_boot (§356: gain recognised up to the cash "
                 "received, a loss never; new basis = old basis - cash + "
                 "gain; the holding period restarts in this model).",
                 cont=True),
            Rule("US-CORP-06",
                 "Spin-offs: taxable_distribution_301 (a §301 "
                 "distribution: income at FMV, which is also the new "
                 "shares' cost)"),
            Rule("US-CORP-07",
                 "or tax_free_355 (§355: the basis moved to the spin-off "
                 "is the US-dollar amount you give, per the company's "
                 "Form 8937, booked exactly even on a non-US listing; "
                 "§358(b); only a significant distributee attaches the "
                 "Reg. §1.355-5 statement).", cont=True),
            Rule("US-CORP-08",
                 "ignore skips broker noise only; on a real event it "
                 "leaves the books wrong."),
        ]),
        ("Income", [
            Rule("US-INC-01",
                 "A payment in lieu of a dividend (a substitute payment) "
                 "is ordinary, non-qualified income (its own entry, never "
                 "a basis reduction), whoever the issuer or the dealer. "
                 "Form 1099-MISC / 1099-DIV is authoritative."),
            Rule("US-INC-02",
                 "Crypto staking rewards are ordinary income at fair value "
                 "when received; that value is the coins' cost."),
            Rule("US-INC-03",
                 "Dividends are booked gross; withholding tax is its own "
                 "TAX row (a foreign tax credit is not computed)."),
        ]),
        ("Crypto", [
            Rule("US-CRYPTO-01",
                 "Each coin is its own property. A coin-for-coin trade is "
                 "a sale of one and a purchase of the other at fair "
                 "value. Kraken's staked and bonded wallet codes (DOT.S, "
                 "DOT28.S, ETH2, ETH2.S, the .M/.F/.B/.P/.HOLD suffixes) "
                 "name the same coin as the bare code, so a 1:1 swap "
                 "between them is not a sale."),
            Rule("US-CRYPTO-02",
                 "USD stablecoins (USDC, USDT, DAI, PYUSD and GUSD, on "
                 "Kraken and Coinbase alike) are property like any coin: "
                 "buying one is a purchase, selling or spending one is a "
                 "sale (a de-peg is a gain or loss), and a payment in one "
                 "is written as a sale. A swap against a stablecoin, a "
                 "reward or a fee in one is valued at its 1.00 USD par "
                 "(on Kraken ahead of any USD value the export states; a "
                 "Coinbase row keeps the value Coinbase states for it, "
                 "par when it states none); a sale for dollars at the "
                 "fill's price."),
            Rule("US-CRYPTO-03",
                 "A Kraken fee paid in a coin is a sale of that coin: on a "
                 "move of coins (a withdrawal, a deposit, a transfer to "
                 "another Kraken user or a Hybrid Earn withdrawal), on a "
                 "fiat deposit or withdrawal, or on a staking reward."),
            Rule("US-CRYPTO-04",
                 "A trade fee taken in a coin reduces the coins bought or "
                 "adds to the coins sold.", cont=True),
            Rule("US-CRYPTO-05",
                 "A send that arrives on another of your exchanges (or the "
                 "same exchange in another crypto account) from 10 minutes "
                 "before it to 3 days after it, with 90% to 100% of the "
                 "coins sent — also as two deposits, or two sends landing "
                 "as one deposit — is treated as your own move; sends and "
                 "arrivals are paired to pair the most sends, then lose the "
                 "fewest coins, then the closest in time, and a Kraken "
                 "Hybrid Earn withdrawal is never paired. Basis stays per "
                 "account and is not carried from one crypto account to "
                 "another: a move paired between two accounts is warned "
                 "about and `run --strict` stops (keep both exchanges in "
                 "one crypto account). When fewer coins arrive and the "
                 "sending "
                 "exchange states no fee (a Coinbase Send hides the "
                 "network fee in the quantity), the coins that did not "
                 "arrive paid the network fee: a sale of them at fair "
                 "value (a stablecoin's at its 1.00 USD par), written to "
                 "crypto_sends.tt, as a Kraken withdrawal fee is "
                 "(US-CRYPTO-03)."),
        ]),
        ("Crypto sends", [
            Rule("US-SEND-01",
                 "Paying with crypto is a sale at fair value; `taxjson "
                 "crypto-sends` records it (`payment`) and writes the sale "
                 "to crypto_sends.tt. When sends.json cannot be read, a "
                 "crypto_sends.tt written from earlier decisions is not "
                 "booked: `taxjson run` stops until it is fixed."),
            Rule("US-SEND-02",
                 "A gift is not a sale for the donor, so `gift` is refused "
                 "in a US project: record it as `self`. A gift already "
                 "saved in sends.json (carried over, copied or edited) is "
                 "never written as a sale: `crypto-sends --write` and "
                 "`taxjson run` stop until it is reclassified.",
                 cont=True),
        ]),
        ("Options", [
            Rule("US-OPT-01",
                 "Premiums are taxed when the position closes (§1234)."),
            Rule("US-OPT-02",
                 "Exercise or assignment folds the premium into the "
                 "stock's basis or proceeds.", cont=True),
            Rule("US-OPT-05",
                 "Each assignment's premium goes to its own stock leg: the "
                 "same account and underlying, the delivered quantity "
                 "(contracts x the declared contract size, else 100; one "
                 "per futures option), priced at the strike, dated up to 3 "
                 "days before or 7 days after the option row. Several "
                 "assignments at one moment are told apart by strike, "
                 "never by row order.", cont=True),
            Rule("US-OPT-03", "Cash-settled options realize on the option.",
                 cont=True),
            Rule("US-OPT-06",
                 "Exercising a warrant or right is not a sale: its basis "
                 "and the exercise price paid become the shares' basis, "
                 "and the shares' holding period starts at the exercise. "
                 "The parser names the shares on the warrant leg (IB `Ex` "
                 "legs, RBC `Exercise` rows, paired by date); IB leaves a "
                 "leg it cannot pair a disposal at 0 with an ATTENTION "
                 "line, RBC refuses the file.", cont=True),
            Rule("US-OPT-04",
                 "Not modelled: §1256 60/40 contracts, §1233 and §1259. A "
                 "broad-based index option (SPX, XSP, NDX, RUT, VIX, DJX, "
                 "OEX and their weekly roots) or an option on a future is "
                 "a §1256 contract: kept off Form 8949 and listed for "
                 "Form 6781, as futures are."),
        ]),
        ("Futures", [
            Rule("US-FUT-01",
                 "A futures contract is booked on its settled P/L: nothing "
                 "is paid to open one, so its notional is never converted "
                 "or reported. A close's P/L (commissions included; a "
                 "negative price keeps its sign) is "
                 "taken first in, first out from the open contracts, and "
                 "a non-USD contract's P/L is converted at the closing "
                 "leg's rate. A fill at a negative price keeps its signed "
                 "money (a buy then receives cash: a negative cost)."),
            Rule("US-FUT-02",
                 "Not modelled: §1256 year-end marking to market and the "
                 "60/40 split; report them on Form 6781 by hand. "
                 "`form-export` (8949 and TXF) and `sum` leave every "
                 "§1256 contract out of the Form 8949 rows and totals and "
                 "list it, with its P/L, for Form 6781.",
                 cont=True),
        ]),
        ("Reports", [
            Rule("US-RPT-01",
                 "`taxjson form-export --form 8949`: Form 8949 rows (Part "
                 "I short-term, Part II long-term;"),
            Rule("US-RPT-02", "wash sales as code W).", cont=True),
            Rule("US-RPT-03",
                 "`--form txf` writes a TurboTax TXF file.", cont=True),
            Rule("US-RPT-09",
                 "Form 8949 cells are rounded half-up to the cent and (h) "
                 "= (d) - (e) + (g) on the rounded cells, so a half-cent "
                 "wash-sale adjustment shows as the allowed gain the other "
                 "reports print."),
            Rule("US-RPT-04",
                 "`taxjson estimate`: federal tax only (single filer, "
                 "standard deduction, NIIT), for planning."),
            Rule("US-RPT-07",
                 "It treats every dividend as qualified, payments in lieu "
                 "and staking as ordinary income, gains with no term as "
                 "short-term, §1256 P/L as short-term (no 60/40 split; "
                 "it names the amount), and a net capital loss as "
                 "offsetting up to "
                 "$3,000 of ordinary income; foreign tax credits, "
                 "interest and state tax are left out.", cont=True),
            Rule("US-EST-NIIT-LOSS",
                 "That up-to-$3,000 capital loss deduction also reduces "
                 "net investment income for NIIT (Form 8960 line 5a).",
                 cont=True),
            Rule("US-EST-CARRY-TI",
                 "The carryforward it shows counts as used only the part "
                 "of the $3,000 that taxable income absorbs (Capital Loss "
                 "Carryover Worksheet line 4).", cont=True),
            Rule("US-RPT-08",
                 "`taxjson carryover`: the short- and long-term capital "
                 "loss carryover (Schedule D worksheet), assuming the "
                 "$3,000 ordinary offset is used each year unless "
                 "claimed_losses.txt records otherwise. A year before the "
                 "project year that has a close-year lock (filed/<year>.json "
                 "or prior_year_record) takes the lock's filed Form 8949 "
                 "Part I / Part II gains instead of the rebuilt ones; a "
                 "year after the project year is partial and the carryover "
                 "stops at the project year."),
            Rule("US-RPT-05",
                 "`taxjson edge-cases`: every trade whose tax year or "
                 "wash-sale verdict turns on a boundary, the window on "
                 "trade dates whatever tax_date says; with no still-held "
                 "test, a sale near day 30 decides nothing, a long call "
                 "is listed as a warning only, and crypto has no "
                 "window."),
            Rule("US-RPT-09",
                 "`taxjson checklist`'s slip step names Form 1099-B for "
                 "securities and, from tax year 2025, Form 1099-DA for a "
                 "broker's digital-asset (crypto) sales (gross proceeds "
                 "only for 2025; basis for covered assets from 2026)."),
            Rule("US-RPT-06",
                 "`taxjson close-year` records each closed year's sales, "
                 "year-end positions and basis (a disallowed loss "
                 "included in the replacement's basis, as `list` shows), "
                 "its country and date basis; `handoff` also flags "
                 "income the two projects date on different sides of "
                 "Dec 31 (a RIC January dividend kept in one and not the "
                 "other); `check-filed` and `handoff` refuse a record "
                 "closed under Canadian rules instead of recomputing it "
                 "under US law."),
        ]),
        ("Planning tools (wash radar, sell-check, buy-check, harvest, "
         "watch, web)", [
            Rule("US-PLAN-01",
                 "Each recent loss's verdict is the US engine's own, as "
                 "of the date: the window on trade dates, purchases in "
                 "every account, IRAs included, and no still-held test — "
                 "a washed loss shows as WASHED, and no later sale "
                 "undoes it (the disallowed loss is in the replacement's "
                 "basis)."),
            Rule("US-PLAN-02",
                 "A long call bought in the window is a note only, for an "
                 "existing loss and for a loss sale today, as are a "
                 "warrant, an adjusted-series call and a futures option; "
                 "an IRA purchase the engine already matched to an "
                 "earlier loss is not counted again (share for share), "
                 "and a short position's trigger is a new short sale.",
                 cont=True),
            Rule("US-PLAN-03",
                 "The web what-if runs a sale with every taxable "
                 "account's purchases and the IRAs as wash-sale context, "
                 "on the account's own FIFO basis. It prices an option "
                 "at the contract size the book's rows declare (100 for "
                 "an equity option with none), settles the sale on the "
                 "listing's market calendar whatever currency the price "
                 "is typed in, refuses a plain futures contract (its "
                 "gain is the settled P/L), and lists the engine's "
                 "warn-only replacement flags for the sale (a long call "
                 "bought in the window, US-WASH-12)."),
            Rule("US-PLAN-04",
                 "Their verdicts cover the project's own accounts only and "
                 "say so: a purchase by your spouse or by a corporation "
                 "you control also makes a loss a wash sale (IRS Pub. "
                 "550), and those accounts are not in the project."),
            Rule("US-PLAN-05",
                 "harvest counts a loss in an account marked crypto as "
                 "claimable now, with no wash-sale advice: those "
                 "accounts are outside the wash-sale rule (US-WASH-13); "
                 "buy-check and sell-check answer a coin held there "
                 "(a bare symbol, ETH) the same way, and an equity "
                 "sharing its root (ETH.US) keeps its own verdict under "
                 "its own name."),
        ]),
        ("Project country", _ownership(c)),
    ]


def rule_sections(country: str, settings: Dict[str, Any]
                  ) -> List[RuleSection]:
    """[(title, [Rule])] for `country` with `settings` in force. An
    unknown country raises lib/country.CountryError."""
    c = _C.canonical_country(country)
    return _usa(settings) if c == _C.USA else _canada(settings)


def _bullets(rules: List[Rule], ids: bool = False) -> List[str]:
    out: List[str] = []
    last_id = None
    for r in rules:
        # A claim split around another (US-WASH-01's sentence) shows its
        # id once per bullet.
        tag = f"[{r.id}] " if ids and r.id != last_id else ""
        text = tag + r.text
        if r.cont and out:
            out[-1] = f"{out[-1]} {text}"
        else:
            out.append(text)
        last_id = r.id
    return out


def sections(country: str, settings: Dict[str, Any],
             ids: bool = False) -> List[Section]:
    """[(title, [bullet])]: the rendered statements."""
    return [(t, _bullets(rs, ids)) for t, rs in
            rule_sections(country, settings)]


def render(country: str, settings: Dict[str, Any], width: int = 88,
           ids: bool = False) -> str:
    c = _C.canonical_country(country)
    name = ("United States (experimental)" if c == _C.USA
            else _C.DISPLAY_NAME[c])
    year = settings.get("year")
    out = [f"TAX LOGIC — {name}" + (f", tax year {year}" if year else "")
           + ", with this project's settings", ""]
    for title, rules in sections(c, settings, ids=ids):
        out.append(title.upper())
        for r in rules:
            # An id never wraps at its hyphens ([CA-SL-\n02]).
            out.append(textwrap.fill(r, width=width, initial_indent="  - ",
                                     subsequent_indent="    ",
                                     break_on_hyphens=not ids))
        out.append("")
    out.append("Detail and sources: README.md and REFERENCES.md. taxjson "
               "computes; it does not give tax advice.")
    return "\n".join(out)


def variants(country: str):
    """Every settings combination of VARIANT_AXES for `country`."""
    c = _C.canonical_country(country)
    axes = VARIANT_AXES[c]
    for combo in itertools.product(*axes.values()):
        st = {k: v for k, v in zip(axes, combo) if v is not None}
        st["country"] = c
        st["year"] = 2026
        yield st


def catalog(country: Optional[str] = None) -> Dict[str, Rule]:
    """{id: Rule} over every settings variant (the first rendering of
    each id, `keys` merged across its renderings). The full set of ids
    tests may name."""
    out: Dict[str, Rule] = {}
    for c in ([_C.canonical_country(country)] if country
              else list(_C.COUNTRIES)):
        for st in variants(c):
            for _t, rules in rule_sections(c, st):
                for r in rules:
                    prev = out.get(r.id)
                    if prev is None:
                        out[r.id] = r
                    elif not set(r.keys) <= set(prev.keys):
                        out[r.id] = Rule(prev.id, prev.text,
                                         tuple(sorted(set(prev.keys)
                                                      | set(r.keys))),
                                         prev.cont)
    return out


def rule_country(rule_id: str) -> str:
    """The country a rule id belongs to, from its prefix."""
    if rule_id.startswith("CA-"):
        return _C.CANADA
    if rule_id.startswith("US-"):
        return _C.USA
    raise ValueError(f"rule id {rule_id!r} has no CA-/US- prefix")
