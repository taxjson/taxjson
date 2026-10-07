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
- ``render(country, settings, width=None, ids=False)``: the plain text
  (wrapped at the house width, lib/out);
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
# changes a statement. catalog() renders every combination. YEAR stands
# for "a year is set": variants() renders it as the sample project year.
YEAR = "YEAR"
VARIANT_AXES: Dict[str, Dict[str, Tuple[Any, ...]]] = {
    _C.CANADA: {
        "tax_date": ("settle", "trade"),
        "option_premium_timing": ("grant", "close"),
        "option_grant_timing_since": (None, YEAR),
        "option_buyback_loss_superficial": (False, True),
        "futures_settle": ("trade", "next_day"),
        "foreign_return_of_capital": ("dividend", "acb"),
        "transfers_as_acquisitions": (False, True),
    },
    _C.USA: {
        "tax_date": ("trade", "settle"),
        "futures_settle": ("trade", "next_day"),
        "transfers_as_acquisitions": (False, True),
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
    "leaps_months": "the LEAPS views' cut-off; no tax figure reads it",
}


# Rules the owner named as the Canada/US partition: each needs a
# @rule_absent test (the same synthetic book under the other country
# must NOT apply it). scripts/check_tax_rules.py enforces it, with a
# shrink-only tests/tax_rules/baseline-unpaired.txt for the pairs not
# written yet. Add a rule here when a Phase-B fix makes it one country's.
PARTITION_RULES = frozenset({
    # Canada
    "CA-INKIND-02",    # in-kind contribution booked as a sale (US: warned)
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
    "CA-ACB-12",       # manual missing-history loss check on settle dates
    "CA-OPEN-01",      # opening balance: pooled, not a purchase (US: lot dates)
    "CA-XLIST-03",     # a broker's CAD/USD currency journal joined (US: transfer legs)
    "CA-OPEN-02",      # opening cost at the snapshot day's BoC rate (US: USD only)
    "CA-CRYPTO-02",    # stablecoins as US-dollar cash
    "CA-DATE-01",      # settle-date tax year by default
    "CA-CTRY-02",      # US-only settings/commands/flags refused
    "CA-CTRY-03",      # base currency CAD
    "CA-INC-03",       # s.260 payment in lieu as a dividend (D3)
    "CA-INC-DATE-ROC-TRUST",  # trust ROC on the record date (D4)
    "CA-INC-DATE-TRUST",      # trust distribution by record date (D5)
    "CA-INC-06",       # T5 box 18 capital-gains dividends (map; R1-62)
    "CA-AMT-01",       # minimum tax (s.127.5) and `taxjson amt`
    "CA-AMT-04",       # s.120.2 carryover recovered in the estimate
    "CA-CARRY-01",     # close-year records the net capital loss + AMT
    # United States
    "US-INKIND-01",    # contribution in kind warned, not booked (CA: a sale)
    "US-WASH-01",      # §1091 window on trade dates
    "US-WASH-06",      # no still-held test
    "US-WASH-22",      # a replacement sold before the loss still washes
    "US-BASIS-05",     # an own-account move carries the lots (CA: s.47)
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
    "US-CRYPTO-08",    # under 1e-08 units is zero (CA keeps any amount)
    "US-STKDIV-01",    # stock dividend: §307 basis spread, no §1091
    "US-BASIS-04",     # manual missing-history loss check on trade dates
    "US-OPEN-01",      # opening lot: its own date, never a replacement
    "US-ROC-03",       # ROC with no shares held: not booked (CA books it)
    "US-ROC-04",       # basis increase with no shares: not applied (CA: next ACB)
    "CA-ACB-13",       # basis increase with no shares: next purchase's ACB
    "US-WASH-18",      # futures / futures options outside §1091 (CA denies)
    "US-INC-DATE-RIC", # §852(b)(7) January dividends: warn + list (D8)
    "US-CARRY-01",     # close-year records the ST/LT carryover (CA: one NCL)
    # Planning tools (partition COMMANDS-01/02/05)
    "CA-PLAN-01",      # radar: settle dates, still-held rescue
    "CA-PLAN-02",      # radar: a long call is a replacement
    "US-PLAN-01",      # radar: the US engine's verdict, no rescue
    "US-PLAN-02",      # radar: a long call is a note only
})


def _split_share_roots():
    """The split-share list in force (taxjson/data/markets.toml with the
    project's ticker.map SPLITSHARE lines)."""
    from taxjson.lib.markets import split_share_roots
    return split_share_roots()


def _index_option_roots():
    """US §1256 index option roots in force (lib/markets)."""
    from taxjson.lib.markets import index_option_roots
    return index_option_roots()


def _stable_text() -> str:
    """The USD stablecoins in force, and where the list lives."""
    from taxjson.lib.markets import usd_stablecoins
    return (", ".join(sorted(usd_stablecoins())) + " — market data "
            "shipped in taxjson/data/markets.toml, extended or overridden "
            "by ticker.map `STABLE SYMBOL USD|NO` lines; the run notes once "
            "per coin when the built-in list decided")


def _evening_roots():
    """Option roots with a Cboe evening session in force (lib/markets)."""
    from taxjson.lib.markets import evening_session_roots
    return evening_session_roots()


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


def _transfers_as_acquisitions(settings: Dict[str, Any]) -> bool:
    """[settings] transfers_as_acquisitions, as the engine reads it."""
    from taxjson.lib.pipeline import transfers_as_acquisitions
    try:
        return transfers_as_acquisitions(settings)
    except ValueError:
        return False


def _ownership(country: str) -> List[Rule]:
    """What a project of this country refuses: built from the
    lib/country tables, so the statement cannot drift from them."""
    other = _C.other_country(country)
    p = "CA" if country == _C.CANADA else "US"
    keys = sorted(_C.owners(_C.SETTING_COUNTRY, other))
    cfg = sorted(_C.owners(_C.CONFIG_COUNTRY, other))
    cmds = sorted(_C.owners(_C.COMMAND_COUNTRY, other))
    # A one-country VALUE of a two-country flag (FLAG_VALUE_COUNTRY,
    # "--foreign-roc dividend") reads as the flag with that value.
    flags = sorted(list(_C.owners(_C.FLAG_COUNTRY, other))
                   + [f"{f} {v}" for (f, v), o
                      in _C.FLAG_VALUE_COUNTRY.items() if o == other])
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
             f"other than canada, ca, usa or us is refused. Books built "
             f"under the other country (the country was changed) are "
             f"refused by every report until `taxjson run` rebuilds "
             f"them.",
             keys=("country",)),
        Rule(f"{p}-CTRY-02",
             "Refused in this project: " + "; ".join(parts) + ".",
             keys=tuple(keys) + tuple(cfg)),
        Rule(f"{p}-CTRY-03",
             f"base_currency must be {cur} (the return is filed in {cur}).",
             keys=("base_currency",)),
    ]


def _local_tz(s: Dict[str, Any]) -> Optional[str]:
    """The zone crypto rows are dated in ([settings] local_timezone);
    None when the project names none (there is no default)."""
    tz = s.get("local_timezone")
    return str(tz) if tz else None


def _tz_rule_text(s: Dict[str, Any]) -> str:
    tz = _local_tz(s)
    now = (f"this project: {tz}" if tz else
           "this project names none, so a crypto account stops the run "
           "until it does")
    return ("Crypto exchange rows are stamped in UTC and dated in the "
            "user's local time zone, [settings] local_timezone (outside a "
            "project the TAXJSON_LOCAL_TZ environment variable). It has no "
            "default: a project with a crypto account and no zone stops "
            "with a message naming the key (and this machine's zone as a "
            f"suggestion) — {now}. Changing it re-dates the rows and "
            "re-keys crypto sends.")


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
    xfer_acq = _transfers_as_acquisitions(s)

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
            Rule("CA-OPT-10",
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
                 "market, not the quote or settlement currency's, in every "
                 "parser (IB, Questrade, RBC and the generic importer, "
                 "one rule): a US-dollar unit listed on the TSX "
                 "(SAMPLF.U.TO) settles on the Canadian calendar, a US stock "
                 "settled in Canadian dollars on the US one, and a "
                 "US-dollar line listed on the LSE (.L) or the ASX (.AX) "
                 "on that market's cycle.", cont=True),
            Rule("CA-DATE-06",
                 "The generic importer uses a mapped settle column (one "
                 "more than 31 days after the trade is refused, more than "
                 "7 is flagged), else this cycle (settle_on_trade_date = "
                 "true keeps the trade date); its futures and its $0 "
                 "option closes on the expiry day follow the two rules "
                 "below.", cont=True),
            Rule("CA-DATE-07", "Crypto settles on the trade date;",
                 cont=True),
            Rule("CA-DATE-08",
                 "an option expiry is dated its expiry day, and so is a "
                 "right or warrant expiry (the date in the row's "
                 "description, \"AS OF\" or \"EXP\", when it is at most "
                 "7 days before the posting date; else the posting "
                 "date), settled the same day.", cont=True),
            Rule("CA-DATE-15",
                 "An expiry the broker posts later (Questrade and RBC post "
                 "it the next business day) is moved back to the "
                 "contract's expiry date when posted at most 7 days after "
                 "it; one posted later keeps its posting date.",
                 cont=True),
            Rule("CA-DATE-16",
                 "A trade in the same contract on its expiry day settles "
                 "no later than the expiry, even when the broker prints a "
                 "later settle date.", cont=True),
            Rule("CA-DATE-17",
                 "Webull prints the SETTLE date: it is the row's settle "
                 "date and the trade date is walked back one settlement "
                 "cycle over business days (a sale printed Jan 2 traded "
                 "Dec 31); an option expiry row's date is the expiry "
                 "itself.", cont=True),
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
                 "or on a weekday the exchange is closed) and a US-dollar "
                 "option filled in Cboe Global Trading Hours (20:15 ET or "
                 "later, Sunday to Thursday) on a root with that session: "
                 + ", ".join(sorted(_evening_roots())) + " (market data "
                 "shipped in taxjson/data/markets.toml, extended or "
                 "overridden by ticker.map `EVENING ROOT [NO]` lines; the "
                 "run notes once per root when the built-in list moved a "
                 "fill). A fill on the ASX, HKEX, "
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
                 "`taxjson fetch` (the taxjson-fetch plugin) keeps the "
                 "API's row order within a day, "
                 "and a re-fetch merge keeps it too. A newest-first export "
                 "is read "
                 "bottom-up; rows of different accounts at one moment "
                 "follow the accounts' order in taxjson.toml, in the run "
                 "and in every recompute of the blended book (check-"
                 "filed, audit, t1135, carryover, wash-sales --explain). "
                 "Rows that "
                 "settle on the same day but traded on different days "
                 "(a Friday trade and the next trading day's trade both "
                 "settling after a settlement holiday) go in TRADE order, "
                 "the earlier trade date first. Fixed places "
                 "at one "
                 "moment: an opening balance first, then a split (effective "
                 "at the open), an assignment's option leg before its "
                 "stock leg, then the trades; cost adjustments last."),
            Rule("CA-DATE-18",
                 "Rows of ONE account at one moment that come from "
                 "different input files follow the files' name order "
                 "(a.tt before b.tt); give such rows distinct times or "
                 "put them in one file.", cont=True),
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
                 "engine pass dates it the same way (run, audit, "
                 "explain). When the record date falls in the "
                 "year before the pay date, the pay-year run names, as "
                 "ATTENTION, each sale of that earlier year whose ACB it "
                 "lowers (that year may be filed without it).",
                 keys=("corporate_distributions",)),
            Rule("CA-INC-DATE-TRUST",
                 "A Canadian trust's distribution belongs to the year it "
                 "became PAYABLE (s.104(13)): a row the broker calls a "
                 "distribution (\"DIST ON\", RBC \"Distribution\") on a "
                 "Canadian issuer (its ISIN country when the export gives "
                 "one, else a Canadian listing) is "
                 "dated by its printed record date — in divs-sum, the "
                 ".sum, the estimate, instalments and the divs / roc / "
                 "events views' windows (each row still shows its pay "
                 "date). Split-share "
                 "corporations say \"Distribution\" too but are "
                 "corporations (paid date): the split-share list — "
                 + ", ".join(sorted(_split_share_roots())) + " (market "
                 "data shipped in taxjson/data/markets.toml, extended or "
                 "overridden by ticker.map `SPLITSHARE ROOT [NO]` lines; "
                 "the run notes once per issuer when the built-in list "
                 "decided) — any row "
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
            Rule("CA-INC-DATE-ISSUER",
                 "The exports do not say which Canadian issuer is a "
                 "trust: for the two record-date rules above every "
                 "Canadian issuer is a trust except the split-share "
                 "corporations and the issuers in corporate_distributions. "
                 "So a corporation's return of capital with a printed "
                 "record date is dated by it until its issuer is listed "
                 "there (when that date crosses a year, the ATTENTION "
                 "line points out a description naming a Corp, Inc or "
                 "Ltd), and the January return-of-capital warning asks "
                 "whether the issuer is a trust rather than assuming it.",
                 keys=("corporate_distributions",)),
            Rule("CA-DATE-12", _tz_rule_text(s),
                 keys=("local_timezone",)),
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
                 "days or less); a row with no rate after that, or a "
                 "currency with no rates at all, stops the run naming the "
                 "row's date and currency pair — taxjson carries no "
                 "built-in rate (the stand-alone converters accept your "
                 "own rate with --default-rate). "
                 "`taxjson fx-cash` counts a cash event with no rate "
                 "row in those 5 days as unrated (named in its report), "
                 "and `taxjson crypto-sends` leaves a send (and a "
                 "stablecoin pool row) with none unpriced.",
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
                 "a trade for cash, income, withholding, fees and the "
                 "cash a corporate action pays (cash in lieu of a "
                 "fraction, whether booked as its own sale or inside an "
                 "exchange's proceeds, and boot); a share-for-share "
                 "exchange, a coin-for-coin swap, a fee paid in a coin "
                 "and a reward in a coin move none (a USD stablecoin is "
                 "US-dollar cash, CA-CRYPTO-02).",
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
            Rule("CA-ACB-CODES",
                 "A broker's internal security code (Questrade writes "
                 "one letter + digits, e.g. X000123, on rows of shares "
                 "transferred in) is the line a currency-journal leg "
                 "of the same description in the same account and "
                 "currency names (Questrade's BRW \"... JOURNAL "
                 "POSITION FROM CAD\": the security's US-dollar line); "
                 "else the security the account's own "
                 "trades of the same description name; else the security "
                 "of the transfer it arrived by (an outgoing transfer of "
                 "the same quantity in another export of the project, up "
                 "to 10 days before the arrival and 3 after, whose "
                 "security name agrees — one candidate only, and none "
                 "naming another share of the company); else, for a "
                 "code with no transfer-in, the one listing in the "
                 "project's books whose name is EQUAL to it, or the one "
                 "listing of the same broker's descriptions of which its "
                 "description is a cut-off prefix (the description is "
                 "exactly the export's width, three strong words "
                 "before the cut, no designator cut off). Names are "
                 "compared once case, punctuation, generic share words, "
                 "corporate-form words (INC, CORP, LTD), common "
                 "abbreviations (RES = RESOURCES, MFG, HLDGS, N V = NV, "
                 "& = AND) and broker boilerplate (REPSTG ..., TRANSFER "
                 "IN ..., a broker's name, IB's /domicile) are set aside. "
                 "The share designators always count: the class letter, "
                 "voting / subordinate / multiple voting, ADR vs "
                 "ordinary, preferred, units, warrants, rights, NEW — "
                 "class A is never booked as class C; only a transfer "
                 "that pairs by quantity and date with no other leg in "
                 "the window tolerates a class letter or ORDINARY / ADR "
                 "stated by ONE broker only, and not when a designator "
                 "was cut off with the boilerplate. Questrade's event "
                 "wording (CASH DIV ON ..., COMMON STOCK ...) is cut "
                 "from its own descriptions only, a designator after "
                 "COMMON STOCK kept. "
                 "Inferred codes are listed in "
                 "one note per account and by `taxjson transfers`; any "
                 "ticker.map rule naming the code (a rename, DELETE, "
                 "DISTINCT, a dated RENAME) wins and is recorded as such; "
                 "a code nothing identifies stays a security of its own "
                 "(ATTENTION, once per code, naming a near match and the "
                 "GLOBAL line to add if it is right). Identification "
                 "only: no tax rule changes.", cont=True),
            Rule("CA-ACB-RENAME",
                 "A ticker change is a dated event in the books (a broker "
                 "corporate-action row; a .tt line `RENAME YYYY-MM-DD OLD "
                 "NEW` in any account's .tt file, which applies to every "
                 "account of the same kind (securities or crypto, "
                 "CA-CRYPTO-RENAME) whose books hold OLD and is recorded once; a "
                 ".tt SPLIT line; or, legacy, a ticker.map line `RENAME "
                 "OLD NEW YYYY-MM-DD`, still read): on that date the pool, "
                 "its ACB and acquisition dates carry from OLD to NEW, and "
                 "the superficial-loss rule treats OLD before the date and "
                 "NEW after it as identical property. A trade in OLD after "
                 "the date is a different security unless the declaration "
                 "says it is the renamed shares (`late=fold`, booked as "
                 "NEW); `late=separate` records another company reusing "
                 "the ticker. Such trades are listed by `taxjson renames` "
                 "and stop `run --strict` until declared; a line's "
                 "late= applies to its own account's late rows and to "
                 "every account without a line of its own (another "
                 "account's line may choose otherwise for its rows). "
                 "Every declaration of one change is one event, dated "
                 "the earliest declared; the changes apply in date "
                 "order, so a ticker changed twice (A to B, then B to "
                 "C) carries the position both times. Declarations that cannot all be true stop the "
                 "run naming the lines: OLD renamed to two symbols, one "
                 "change on two dates more than a week apart, a cycle "
                 "(A to B and B to A), one account choosing both "
                 "late=fold and late=separate. An undated "
                 "rename (GLOBAL, or RENAME without a date) applies to "
                 "every row of OLD. "
                 "IB's temporary symbol (a time stamp YYYYMMDDHHMMSS before "
                 "the ticker, given around a corporate action) listed "
                 "under the ticker's own contract id is that ticker: "
                 "its rows are booked as the ticker, no ticker.map line "
                 "needed (a ticker.map line naming the stamped symbol, in "
                 "any keyword, wins: it keeps its rows); a ticker change "
                 "IB shows only as one contract id under two symbols is "
                 "booked as this dated event (OLD the symbol whose trades "
                 "end first, the date 00:00 on NEW's earliest row of any "
                 "section — a trade, a transfer, a corporate action, a "
                 "dividend, a return of capital; the dates "
                 "are that contract id's own — a ticker another company "
                 "used has its own id), never toward a "
                 "temporary symbol or IB's `.OLD` placeholder, with a "
                 "Warning naming the way out "
                 "(ticker.map `DISTINCT OLD NEW`: two securities; a .tt "
                 "`RENAME ... late=separate`), only when the rows date it: "
                 "every OLD row of any section on an earlier day than "
                 "NEW's earliest row. Otherwise — NEW's rows begin on or "
                 "before OLD's last one, a symbol is listed under several "
                 "contract ids in one statement — nothing is booked and "
                 "an ATTENTION line gives the .tt line `RENAME <date> OLD "
                 "NEW` and why. Nothing is booked from the contract id "
                 "when a corporate action names OLD and NEW together (a "
                 "split or merger that changes the symbol: that row "
                 "books it) or a .tt SPLIT row moves OLD to NEW, nor when "
                 "a declaration renames OLD to another symbol (it "
                 "decides; ATTENTION); a .tt RENAME line or a "
                 "ticker.map rule joining the two books it instead. A "
                 "weaker look-alike (Questrade, RBC, Webull: two symbols "
                 "sharing a description, the new one going short) is only "
                 "suggested, as the .tt line. A RENAME naming an option "
                 "contract or a future is refused: a contract never "
                 "becomes shares (or shares a contract) by a ticker "
                 "change, an option follows its underlying's change (the "
                 "stock's line is the one to write), and another expiry, "
                 "strike or right is another contract. A RENAME between "
                 "two spellings of one listing in the books (a Canadian "
                 "venue folds into .TO: A.TO and A.CN) books nothing, "
                 "said as an Info line. A legacy ticker.map dated RENAME "
                 "naming an option or a future is refused the same "
                 "way.", cont=True),
            Rule("CA-ACB-05",
                 "Accounts typed \"sheltered\" (RRSP, TFSA, FHSA, LIRA, "
                 "RESP...) are tracked but kept out of the filing totals. "
                 "For the superficial-loss rule they count as affiliated "
                 "holders."),
            Rule("CA-STKDIV-01",
                 "A stock dividend's new shares enter the pool at $0 cost. "
                 "Its declared amount (a dividend, and by law also the new "
                 "shares' cost) is not in the broker's export: add it "
                 "([[distributions]] in taxjson.toml or a .tt ADJUST) — "
                 "that books the ACB "
                 "only; the dividend itself is reported from the T5/T3 "
                 "slip (taxjson does not count it as income). A taxable "
                 "run of the dividend's year says so until the cost is in "
                 "the books. The new shares are "
                 "an acquisition for the superficial-loss rule. Shares of "
                 "ANOTHER security (another class) paid as a stock "
                 "dividend are not booked: the parse says UNBOOKED; enter "
                 "them by hand."),
            Rule("CA-DIST-01",
                 "[[distributions]] (taxjson.toml): a non-cash distribution "
                 "(a reinvested capital-gains distribution, a late return-of-capital "
                 "factor) becomes an ACB adjustment sized on the shares "
                 "held on its record date — the settled position, each "
                 "ticker's own shares — and booked on those shares only "
                 "(a trade straddling the record date is not the "
                 "holder's). The per-share amount is in the project's "
                 "base currency (a US-listed fund's USD factor is "
                 "converted by the user first). Its "
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
                 "capital gain and the ACB is nil (s.40(3)); Schedule 3 "
                 "shows that gain with no proceeds (13199 = 0, the gain on "
                 "13200).", cont=True),
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
            Rule("CA-ACB-TRANSFER-BV",
                 "A broker's transfer rows in a taxable account are kept "
                 "out of the books. Shares that arrive from outside your "
                 "books (a transfer-in no transfer-out of yours cancels: "
                 "the same security after ticker.map, the same quantity "
                 "and closest date first, across your taxable accounts, "
                 "or another listing's leg of a journal) take the book "
                 "value the broker states on the row (Questrade, RBC) as "
                 "their ACB on the arrival date, said as ATTENTION. A "
                 "transfer VALUE that is a market value (IB) is never a "
                 "cost: those shares stay out with no ACB, said as "
                 "ATTENTION. A .tt purchase of the security in that "
                 "account dated on or before the arrival, or a "
                 "missing_history.json entry for it (CA-ACB-11), covers "
                 "it instead (no book value, no ATTENTION). The arrival "
                 "is not an acquisition for the superficial-loss "
                 "window."),
            Rule("CA-XLIST-01",
                 "Two listings of one company's same class of shares (a "
                 "TSX line and its NYSE line, a US-dollar and a "
                 "Canadian-dollar line) are identical property: one ACB "
                 "pool, one security for the superficial-loss rule. "
                 "`taxjson run` joins them itself, as a ticker.map TOBASE "
                 "line would, when a transfer journal pairs them uniquely "
                 "(an out-leg of one and an in-leg of the other in your "
                 "accounts, the same quantity, within 5 business days, "
                 "weekends not counted) and the exports' security "
                 "names are EQUAL word for word once normalised (case, "
                 "punctuation, abbreviations, broker wording — a "
                 "dealer's event and trade-confirmation wording such as "
                 "CASH DIV ON, UNSOLICITED, WE ACTED AS PRINCIPAL, AVG "
                 "PRICE, its desk codes (a share designator in it "
                 "kept) — and the "
                 "generic words COMMON / SHARES set aside) — the "
                 "corporate form (LP, CORP, TRUST, FUND differ) and every "
                 "share designator (class, voting, ADR, preferred, unit, "
                 "HEDGED ...) included; a name that is a subset of the "
                 "other, or states a designator the other leaves out, is "
                 "not joined. "
                 "A broker's explicit journal between the two listings — one "
                 "account, one day, the same quantity, both legs in the "
                 "broker's journal wording (RBC's TFR \"TRANSFER TO C$\" / "
                 "\"FROM U$\" with its J reference, IB's InterDepot, "
                 "Questrade's BRW JOURNAL POSITION) — compares the two "
                 "legs' OWN names on that day instead of every name "
                 "either listing ever had (a fund renamed later, a "
                 "listing another broker names with other designators), "
                 "and a corporate-form word (LTD, CORP, INC ...) that ENDS "
                 "one leg's name while the other ends with none (never a "
                 "form word inside a name, never LP: a partnership is "
                 "not the company), or a leading THE, or a "
                 "NEW after a generic share word (\"COM NEW\"), does not "
                 "block it; corporate forms both names state must agree, "
                 "every share designator counts, and a name of either "
                 "listing that names another company refuses. Legs that "
                 "pair on their day pair before legs days apart, and a "
                 "reference the broker writes on both legs of one "
                 "journal (RBC's J~ reference, Questrade's journal pair) "
                 "pairs those two and no others, inside their account, "
                 "before any other leg is looked at: another account's "
                 "transfer of the same listing on that day never "
                 "cancels one of them, and a leg left over from such a "
                 "pair pairs with legs of its own account only. A "
                 "listing that several "
                 "such journals map onto (the base-currency line, its "
                 "other-currency line under two symbols over the years) "
                 "is no ambiguity when the listings mapped onto it are "
                 "one security with each other too — each one's own "
                 "name equal to the shared listing's name of its day "
                 "word for word, or their own names agreeing as a "
                 "journal's legs' must (a PLC and a CORP that each match "
                 "a name stating no form are two listings, suggested); "
                 "and a transfer between two listings "
                 "such a journal joined is part of that join when its "
                 "legs' names agree as a journal's must. "
                 "Each join is a Warning naming the pair and "
                 "the `DISTINCT X Y` ticker.map line that undoes it. A "
                 "ticker.map rule renaming either listing, or a DISTINCT "
                 "line for the pair, always wins; anything less certain "
                 "stays a suggestion (`taxjson ticker-map --suggest`) — "
                 "but two listings whose names name different companies "
                 "(no leading company word in common) are never joined "
                 "nor suggested, and a .US symbol whose rows name two "
                 "different companies, one of them a Canadian-listed "
                 "fund's US-dollar units, is a symbol collision: a "
                 "Warning and the EXTRACT line that gives the fund's "
                 "rows their own symbol (ROOT.U.TO), never a join "
                 "through it."),
            Rule("CA-XLIST-02",
                 "Each listing's symbol is read from the evidence, not "
                 "the row currency alone: Questrade and RBC write a bare "
                 "ticker and a currency, and a broker may file one "
                 "listing on the other currency's row (Questrade files "
                 "interlisted shares that arrived from another broker "
                 "under the TSX ticker on a USD row). Such a symbol is "
                 "the other listing (ROOT.US read as ROOT.TO) when its "
                 "transfer-in is the unique arrival of a transfer out of "
                 "the same quantity within 5 business days, under an EQUAL name, "
                 "of the same ticker's other listing or of ANOTHER ticker "
                 "on the same currency's listing (two US tickers never "
                 "name one company's identical shares); or, for shares "
                 "that arrived on a USD row by a transfer the books do "
                 "not pair, when ROOT.TO is in the project's books under "
                 "an equal name (shares bought at the broker keep the "
                 "listing its USD trade rows name). Never when a ticker.map line names the "
                 "symbol in any keyword (a rename, DELETE, DISTINCT, or a "
                 "lookup line: QUOTE, T1135, CRYPTO, STABLE, MULT, an "
                 "EXTRACT target; `DISTINCT ROOT.US ROOT.TO` keeps the row "
                 "currency's listing), when another broker that names "
                 "its listings trades ROOT.US, when a rename row joins "
                 "the two tickers, or when the account also holds the "
                 "other listing in another currency (then a TOBASE line "
                 "is suggested). Every row of the symbol in that "
                 "broker's exports of the account takes the listing; "
                 "each correction is a Warning, and `taxjson ticker-map "
                 "--suggest` shows the equivalent explicit lines."),
            Rule("CA-XLIST-03",
                 "A broker's currency journal between the Canadian-dollar "
                 "and US-dollar lines of one security (Questrade's BRW "
                 "rows \"<NAME> JOURNAL POSITION TO USD\" and \"<NAME> "
                 "JOURNAL POSITION FROM CAD BOOK VALUE: $X CNV@ r\", or "
                 "TO CAD / FROM USD: one account, one day, one name, the "
                 "same quantity) is not a disposition: the two lines are "
                 "identical property. `taxjson run` joins them as a "
                 "ticker.map TOBASE line would (whether the account "
                 "keeps its transfers in the books, `transfers = true`, "
                 "or aside) — one ACB pool, one "
                 "security for the superficial-loss rule, the journal's "
                 "legs moving the units between the lines in the "
                 "holdings view — with one Warning per account naming "
                 "the `DISTINCT X Y` line that undoes it; a ticker.map "
                 "rule naming either line always wins. The US-dollar "
                 "line is the listing the account's own US-dollar rows "
                 "of the security use, else the TSX convention "
                 "SYMBOL.U.TO (taxjson/data/markets.toml; a ticker.map "
                 "EXTRACT or GLOBAL line overrides it). The journaled "
                 "units keep the pool's ACB (the Canadian-dollar cost of "
                 "the units bought); the book value the in-leg states "
                 "(in the in-leg's currency; the other leg's at the "
                 "stated CNV@ rate) is carried on the legs only where "
                 "they do not net. A journal leg with no partner is a "
                 "transfer leg of its own line, said as ATTENTION. The "
                 "journal's legs move units inside that account only: "
                 "they never pair with another account's transfer (a "
                 "transfer-in from outside the books, an in-kind move)."),
            Rule("CA-XLIST-04",
                 "A journal you declare in an account's .tt file, `JOURNAL "
                 "YYYY-MM-DD FROM TO QTY` (QTY units moved from listing "
                 "FROM to listing TO of one security inside that account: "
                 "a Norbert's gambit, a TSX line moved to its NYSE line), "
                 "is not a disposition: the two listings are identical "
                 "property, joined as a TOBASE line would (the listing in "
                 "the base currency is the one kept) when something "
                 "shows they are listings of one security: the two "
                 "symbols share one root (the symbol without its venue "
                 "and, on a Canadian venue, without the US-dollar line's "
                 ".U: QZG.TO, QZG.U.TO and QZG.US are one root; a class "
                 "or unit designator is part of it) or some name the "
                 "exports give each agrees as a broker journal's two "
                 "legs' names must (CA-XLIST-01), and no name of one "
                 "names another company than every name of the other. "
                 "Otherwise the line stops the run: a deliberate join of "
                 "two symbols is a ticker.map `TOBASE FROM TO` line (the "
                 "line is then booked). The same holds in a US project "
                 "(US-XLIST-03). Its two transfer legs are booked with the "
                 "account's transfer evidence, never as a purchase or a "
                 "sale: the holdings view moves the units, the missing-"
                 "history checks read the journal's days as a journal's "
                 "(buys before sales whatever the broker's clock): the "
                 "legs' own days and, in that account within 5 business "
                 "days of them, a day with a buy of one listing and a "
                 "sale of the same quantity of the other (a gambit's "
                 "trades, its legs dated the settlement day) — the same "
                 "for a broker's journal and a legacy ticker.map JOURNAL "
                 "line (its days of such trades). A TOBASE line names no "
                 "journal day (it says two listings are one security, not "
                 "that units moved), and a sale and a rebuy of one "
                 "listing is never a journal: such a day keeps the "
                 "clock, and a sale with no purchase before it is "
                 "missing history (the run names the .tt JOURNAL line to "
                 "add if the units were journaled). The legs "
                 "move units inside that account only: they cancel each "
                 "other (or the broker's leg the line stands beside) and "
                 "never pair with another account's transfer — not as a "
                 "move of your own, not as the end of a transfer-in from "
                 "outside the books (CA-ACB-TRANSFER-BV), not as an "
                 "in-kind move to or from a plan (CA-INKIND-01); the "
                 "same holds for a broker's journal pair (Questrade's "
                 "BRW pair, RBC's J~ reference on its TFR legs). A journal "
                 "whose two legs the broker's rows already hold (the "
                 "account's out-leg of FROM and in-leg of TO, the same "
                 "quantity, within 5 business days) is booked from them, "
                 "not twice (an Info line); with one leg there, only the "
                 "other is booked. A ticker.map rule naming either "
                 "listing wins; a DISTINCT line keeps them two securities "
                 "(a Warning). An option contract or a future is never "
                 "journaled: such a line is refused, as is one dated in "
                 "the future or moving an implausible quantity. Two "
                 "identical lines are one journal (a Warning), and a "
                 "line moving MORE units than a journal the broker's "
                 "rows already hold between the same two listings on "
                 "the line's date stops the run (it would move those "
                 "units twice); one dated a business day from that "
                 "journal's legs, or on its trades' day, is booked and "
                 "said as a Warning naming both (date it as the broker's "
                 "journal to restate it); a line on another date is "
                 "another journal. The legacy "
                 "ticker.map `JOURNAL FROM TO` "
                 "line is read as `TOBASE FROM TO`, said once per run; "
                 "`taxjson format-map --write` rewrites it."),
            Rule("CA-ACB-11",
                 "Shares sold with no purchase in your files (bought "
                 "before the data starts) go in missing_history.json "
                 "(its old name phantoms.json is still read): sales that "
                 "draw on them have an unknown cost — they are listed "
                 "for manual reporting and left out of the totals, with no superficial-loss "
                 "test, until the position is fully sold.", cont=True),
            Rule("CA-ACB-12",
                 "A loss within 30 days (settle dates) of such a sale, or "
                 "such a sale at a loss with a purchase in that window, is "
                 "flagged for a manual superficial-loss check.",
                 cont=True),
            Rule("CA-ACB-15",
                 "A broker's own figure for such shares — IB's Basis on a "
                 "sale it codes closing, with its Closed Lots when the "
                 "statement lists them, or the book value a transfer-in "
                 "states (Questrade, RBC) — is evidence, never booked. `find-missing-history "
                 "--write-purchases` drafts it as .tt purchase lines in a "
                 "file the run does not read (inputs/<account>/"
                 "purchases_draft.tt.txt); a line you keep (renamed to "
                 ".tt) is your own statement of the cost. IB's figure is "
                 "the cost of the lots IB closed (FIFO), not the ACB, "
                 "which averages every identical share in all your "
                 "taxable accounts (s.47); a non-CAD cost is converted at "
                 "the Bank of Canada rate of the purchase date, so a draft "
                 "without lot detail leaves that date as a placeholder "
                 "the run refuses until you fill it in."),
            Rule("CA-OPEN-01",
                 "An opening balance (`taxjson opening`, a .tt OPENING "
                 "line from a broker's positions report) sets a position "
                 "and its cost on the snapshot day: its shares and cost "
                 "join the s.47 pool (CA-ACB-01), but it is not a "
                 "purchase — never a superficial-loss replacement and "
                 "never 'acquired in the window' (its shares do count as "
                 "held at the end of day 30). A lot date on the line is "
                 "shown (days held, Schedule 3's year of acquisition) and "
                 "does not change the pooled ACB; when it falls within 30 "
                 "days of a loss, the loss is flagged for a manual check "
                 "(that purchase was real). The broker's book cost is "
                 "used as it is: it may leave out a superficial loss, a "
                 "return of capital or the same shares in another "
                 "account (`taxjson sanity` compares costs)."),
            Rule("CA-OPEN-02",
                 "A cost the report states in another currency is "
                 "converted at the Bank of Canada rate of the snapshot day "
                 "(an approximation of the purchase days' rates); a "
                 "broker's book cost in Canadian dollars for a foreign "
                 "listing is used as the broker converted it — the "
                 "report's currency decides.", cont=True),
            Rule("CA-OPEN-03",
                 "The snapshot replaces the account's earlier history of "
                 "its symbols: the account's other trade, transfer, split "
                 "and cost-adjustment rows of a snapshot symbol dated on or "
                 "before the snapshot day are left out of the books "
                 "(income rows stay; other symbols and other accounts keep "
                 "theirs), so no share is counted twice. A sale of the tax "
                 "year among them stops the run; one symbol has one "
                 "snapshot date per account.", cont=True),
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
                 "on 15199/15300 before. The 2024 form splits Part 3 by "
                 "the disposition's date (its tax_date): Period 1, "
                 "January 1 to June 24, 2024, on 10689/10690 (shares) "
                 "and 10693/10694 (options, futures, crypto and other "
                 "properties); Period 2 on the codes above. A security "
                 "sold in both periods has a row in each; slip gains go "
                 "on 17399/17599 (Period 1) and 17400/17600. A 2024 "
                 "close-year lock written before the split is compared "
                 "on the Period 2 codes."),
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
                 "(100 shares for a standard equity option — ASSUMED, and "
                 "noted once per root, when a non-IB export does not state "
                 "it; a mini's 10 when the row's own amount shows it; a "
                 "ticker.map `MULT ROOT N` line overrides). A root that drops "
                 "the share class (SAMPLD "
                 "for SAMPLD.B.TO) names that class line."),
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
                 "e.g. SAMPLE1) or a futures option on the loss's futures "
                 "contract, however it is spelled (never sized as 100 "
                 "units). Nothing is denied for a flag, so `taxjson "
                 "wash-sales` lists each one and the checklist's "
                 "wash-reviewed step stays open until you decide them.",
                 cont=True),
            Rule("CA-SL-07",
                 "Only purchases count: writing an option or shorting "
                 "again never replaces, including after a loss on covering "
                 "a short."),
            Rule("CA-SL-08",
                 "CRA's formula, for each sale on its own: denied units = "
                 "the least of the units sold, the units acquired in the "
                 "window and the units held at the end of day 30 — the "
                 "last two taken per holder (your taxable accounts as one "
                 "pool, each registered or affiliated account alone; a "
                 "call at its contract size) and summed. The same held "
                 "unit may back "
                 "the denials of two sales. A sale split into fills (one "
                 "account's same-day sales with no buy between them) is "
                 "one sale: its fills share the denial pro rata, and a "
                 "replacement's ACB is raised after its last fill. A held "
                 "call contract counts once however often its series was "
                 "bought and sold in the window. The denied part is loss "
                 "x (those units / units sold)."),
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
            (Rule("CA-SL-17",
                  "A registered account's transfer in or out (one no "
                  "move between your own registered accounts and no "
                  "zero-net journal pairs) is booked as an acquisition or "
                  "disposition on its date. A transfer-in inside a loss's "
                  "window stops the run until it is declared: a DECLARED "
                  "counter-TRANSFER .tt pair for a custody move, a "
                  "BUYSELL dated the true day for an in-kind contribution "
                  "(transfers_as_acquisitions = true).",
                  keys=("transfers_as_acquisitions",))
             if xfer_acq else
             Rule("CA-SL-16",
                  "A registered account's transfer in or out is a move "
                  "between accounts, not an acquisition or disposition: "
                  "its shares count as held at day 30, but it never "
                  "replaces a loss, whatever trades sit near it. One "
                  "warning per run lists each transfer-in inside a "
                  "taxable loss's window, and each netted move (between "
                  "two registered accounts, or a zero-net cluster in "
                  "one) with a leg inside it — one leg may have been a "
                  "contribution; an in-kind contribution or a "
                  "purchase recorded as a BUYSELL is counted "
                  "(transfers_as_acquisitions = false).",
                  keys=("transfers_as_acquisitions",))),
        ]),
        ("In-kind moves to and from registered plans", [
            Rule("CA-INKIND-01",
                 "A move of shares between a taxable account and a "
                 "registered account (RRSP, RRIF, TFSA, FHSA, RDSP, "
                 "LIRA/LIF ...) is an in-kind contribution or withdrawal, "
                 "not a custody move: the run pairs a taxable account's "
                 "transfer-out with a registered account's transfer-in "
                 "(or the reverse) of the same security (after ticker.map) "
                 "and quantity within 10 days, across brokers. Legs of one "
                 "account pair first (a journal), then legs of the same "
                 "kind — taxable with taxable, registered with registered, "
                 "the closest date first: a move of your own — across the "
                 "whole project, and only then a taxable leg with a "
                 "registered one. That pair is booked only when neither "
                 "leg has another plausible partner (a leg of either kind "
                 "of the same security and quantity in the window): else "
                 "it is listed NOT booked as ambiguous, naming both "
                 "candidates and the line that settles it (`run --strict` "
                 "stops). A .tt `INKIND` line in the taxable account's "
                 "folder declares and values the move of its transfer row "
                 "(`plan=` picks the plan's leg; with no leg of the "
                 "quantity, the plan's legs that add up to it, else a plan "
                 "outside the project), and `INKIND <date> <symbol> <qty> "
                 "plan=own` declares the row a move of your own. A taxable "
                 "transfer row left unpaired while a registered account "
                 "moved the same security the other way in other "
                 "quantities in the window (a delivery in parts) is warned "
                 "about, not booked (`run --strict` stops). One warning "
                 "per run lists each move, its value and its source, and "
                 "the gain or the denied loss."),
            Rule("CA-INKIND-02",
                 "A contribution in kind is a disposition at fair market "
                 "value on the transfer date (the taxable account's row): "
                 "booked as a sale at that value; a gain is taxed (CRA "
                 "T4040, RC4466)."),
            Rule("CA-INKIND-03",
                 "A loss on it is denied for good (s.40(2)(g)(iv): a "
                 "disposition to an RRSP, RRIF, TFSA, FHSA or RDSP trust; "
                 "an account whose plan is not named is taken as one): "
                 "the loss is nil, not a superficial loss, and never added "
                 "to any ACB; `sum` and form-export show it on its own "
                 "(\"denied: contribution to a registered plan\"), not "
                 "in DENIED. A contribution to an RESP or PRPP is a sale "
                 "at fair market value whose loss is an ordinary one.",
                 cont=True),
            Rule("CA-INKIND-04",
                 "The plan's acquisition is an acquisition of identical "
                 "property for s.54 on its own transfer date, whatever "
                 "transfers_as_acquisitions says: a taxable loss on the "
                 "same security within 30 days before or after it, with "
                 "the plan still holding at day 30, is superficial and "
                 "lost for good (CA-SL-09)."),
            Rule("CA-INKIND-05",
                 "A withdrawal in kind is an acquisition by the taxable "
                 "account at fair market value on the transfer date (its "
                 "ACB; an acquisition for s.54). From an RRSP or RRIF "
                 "that value is also income on the T4RSP / T4RIF, from a "
                 "TFSA it is not taxed: the warning says so; the books "
                 "hold capital property only and do not book the income."),
            Rule("CA-INKIND-06",
                 "The fair market value, in order: the `INKIND` line; the "
                 "market value the broker states on the transfer row (IB's "
                 "Transfers `Market Value` — a value, never a cost "
                 "elsewhere, CA-ACB-"
                 "TRANSFER-BV); else Yahoo's close on the date (or the last "
                 "one before it), marked ESTIMATED (split-adjusted). It is "
                 "converted at the Bank of Canada rate of the date like any "
                 "row. With TAXJSON_OFFLINE and no cached close the run "
                 "stops and names the INKIND line to add; a move with no "
                 "value is listed NOT booked (`run --strict` stops)."),
        ]),
        ("Options (s.49)", prem + [
            Rule("CA-OPT-06",
                 "Exercise or assignment: the premium folds into the "
                 "shares' cost or proceeds (s.49(3) for a call, s.49(3.1) "
                 "for a put; the grant year is amended under s.49(4))."),
            Rule("CA-OPT-08",
                 "Each assignment's premium goes to its own stock leg: the "
                 "same account and underlying, the delivered quantity "
                 "(contracts x the contract size — a ticker.map MULT line, "
                 "else the size the export states (IB) or a mini's 10 its "
                 "own amount shows, else 100 ASSUMED and noted once per "
                 "option root; one per futures option), priced at the "
                 "strike, dated up to 3 "
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
                 "Cash in lieu of a fractional share is a sale of the "
                 "fraction for the cash, on the pool's average cost "
                 "(Questrade books the fraction at $0 the same day "
                 "first).", cont=True),
            Rule("CA-CORP-09",
                 "A merger paid wholly in cash (IB \"Merged(Acquisition) "
                 "FOR CAD 30.00 PER SHARE\") is a sale of the shares at "
                 "the cash proceeds;", cont=True),
            Rule("CA-CORP-10",
                 "one paying shares AND cash is not modelled: it stops "
                 "the run as an UNSUPPORTED event to enter by hand (.tt "
                 "lines).", cont=True),
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
                 "gross-up or credit), EXCEPT one on a Canadian "
                 "corporation's share (a Canadian issuer: its ISIN "
                 "country when the export gives one, else a Canadian "
                 "listing; not a trust's unit, CA-INC-07) paid by a "
                 "Canadian dealer (IB's statement names Interactive "
                 "Brokers Canada Inc.; Questrade and RBC Direct are "
                 "Canadian dealers, and their 'IN LIEU OF DIVIDEND' rows "
                 "are payments in lieu): ITA "
                 "s.260(5)/(5.1) deems that a taxable dividend — "
                 "eligible in the estimate, counted in divs-sum, and on "
                 "the dealer's T5 box 24. The slip is authoritative."),
            Rule("CA-INC-07",
                 "s.260(5) covers shares only: a payment in lieu on a "
                 "Canadian trust's unit (an ETF, REIT or fund unit) is "
                 "ordinary income. A unit is a trust's by the test that "
                 "dates a trust's distribution (CA-INC-DATE-TRUST): the "
                 "books carry a distribution on it (\"DIST ON\", RBC "
                 "\"Distribution\") from a Canadian issuer that is not a "
                 "split-share or listed corporation. A unit whose payouts "
                 "no export calls distributions (IB calls them dividends) "
                 "cannot be told from a share, so its payment in lieu is "
                 "still deemed a dividend — take it from the dealer's "
                 "slip.", cont=True),
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
                 "in taxjson.toml's [[capital_gains_dividends]] (symbol — "
                 "a bare root "
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
                 "value. Kraken's wallet suffixes (DOT.S and the "
                 ".M/.F/.B/.P/.HOLD suffixes) and its bonded-staking codes "
                 "(<COIN><two-digit lock period>.S, e.g. DOT28.S, when the "
                 "coin itself is in the same export; otherwise noted with "
                 "the line to add) name the same coin as the bare code, "
                 "so a 1:1 swap between them is not a sale. Kraken's "
                 "legacy codes (XXBT, XETH, XDG ...) are the common "
                 "tickers (market data, taxjson/data/markets.toml). "
                 "Any other code is a coin of its own unless the "
                 "project's ticker.map folds it with a `GLOBAL CODE COIN` "
                 "line between bare codes, which the Coinbase and Kraken "
                 "parsers apply before they read a row (a staked or "
                 "wrapped code such as ETH2 — taxjson ships no such fold; "
                 "a 1:1 swap between ETH and ETH2 without it is a sale, "
                 "noted once with the line to add)."),
            Rule("CA-CRYPTO-10",
                 "So is Coinbase's ETH2 (its staked ETH) under `GLOBAL "
                 "ETH2 ETH`: it is booked as ETH, and a \"Converted ETH "
                 "to ETH2\" row is not a sale (unequal quantities stop "
                 "the parse).", cont=True),
            Rule("CA-CRYPTO-11",
                 "A Kraken dust sweep (several coins converted at once "
                 "into one receipt) is a sale of each coin: the receipt "
                 "is split over them by the export's amountusd, or "
                 "equally when the export has none (the parse says "
                 "which; a US-dollar leg with no amountusd is its own "
                 "amount). A fiat leg of a sweep (CAD) is cash, not a "
                 "sale: its share of the receipt is a currency "
                 "conversion. A coin leg under 1e-09 units (the books' "
                 "zero; Kraken writes amounts to ten decimals) is left "
                 "out only when its value is negligible too: its own "
                 "amountusd (when the export has one) and the share of "
                 "the other side it would take are each at most 0.01 USD "
                 "(a one-for-one trade: both sides at most 0.01 USD). It "
                 "is then the disposition (or acquisition) of a "
                 "negligible amount: its share of the receipt goes to "
                 "the sweep's other legs by amountusd, and spent coins "
                 "stay in the holdings as a residue (CA-CRYPTO-09); when "
                 "every coin leg is that small no sale is booked. The "
                 "parse names each such leg (coin, amount — a received "
                 "leg net of its fee — and USD value) once per trade. A "
                 "leg that small worth more, or whose value is unknown, "
                 "is refused with the file, the masked refid, the coin "
                 "and the date, never dropped; so is a spend or receive "
                 "row of amount 0. A Kraken coin fee or reward under "
                 "1e-09 units is left out only when its feeusd/amountusd "
                 "is missing or at most 0.01 USD, else refused.",
                 cont=True),
            Rule("CA-CRYPTO-RENAME",
                 "A ticker change of a coin (the same token, which an "
                 "exchange now lists under a new code) is declared like a "
                 "security's: a .tt line `RENAME YYYY-MM-DD OLD NEW` in a "
                 "crypto account's .tt file. It carries the pool and its ACB (CA-CRYPTO-01: each coin is its own property) "
                 "from OLD to NEW on that date: no disposition. A RENAME "
                 "applies only to accounts of its declaring account's "
                 "kind — a crypto account's to the crypto accounts, a "
                 "securities account's to the securities accounts "
                 "(CA-ACB-RENAME): a security's ticker change never moves a "
                 "coin, nor a coin's a security. A swap into a different "
                 "token (a migration to a new chain, a redenomination) "
                 "is not a ticker change: book it as the trade it is."),
            Rule("CA-CRYPTO-09",
                 "Any amount of a coin is property: a residue left after a "
                 "sale, however small, stays in the holdings with its "
                 "share of the cost (only arithmetic noise, under a "
                 "hundred-billionth of the position, counts as zero). A "
                 "share position under a millionth of a share counts as "
                 "zero.", cont=True),
            Rule("CA-CRYPTO-02",
                 "USD stablecoins (" + _stable_text() + "), on "
                 "Kraken and Coinbase alike, are treated as US-dollar "
                 "cash, an approximation (their own gain or loss, a "
                 "de-peg, is not computed; a fill more than 2% off 1.00 "
                 "USD is warned about. A fill valued in another currency "
                 "(CAD, EUR, ...) is first turned into US dollars through "
                 "the day's rates from the run's rates file, the "
                 "conversion stage's own; a fill with no rate for its day "
                 "is said to be unchecked)."),
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
                 "one, otherwise the Yahoo daily close (of `<COIN>-USD`, "
                 "or of the id the project's ticker.map CRYPTO line "
                 "names; there are no built-in coin ids) times the Bank of "
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
                 "exceeds $100,000 at any time in the year. The holdings "
                 "are walked on the project's tax_date basis (settle "
                 "dates by default: a Dec 31 sale that settles in January "
                 "is still held at year end), rows at one moment in the "
                 "gains engine's order."),
            Rule("CA-RPT-13",
                 "Below $250,000 at every time in the year the simplified "
                 "method (Part A) is available; at $250,000 or more the "
                 "detailed method (Part B) is required.", cont=True),
            Rule("CA-RPT-02",
                 "Country comes from the listing suffix (ticker.map "
                 "`T1135 SYMBOL COUNTRY` lines override it — COUNTRY an "
                 "ISO 3166 alpha-3 code, or CA/CAN/CANADA/EXCLUDE for "
                 "not foreign property; a line outside that vocabulary "
                 "stops the report; a foreign listing whose rows carry a "
                 "Canadian ISIN is named for a `T1135 SYMBOL CA` line, "
                 "since a Canadian corporation's shares are not foreign "
                 "property); crypto held on an exchange counts. A T1135 "
                 "line follows its symbol through a rename, and a line "
                 "that matches no symbol in the books is named in a "
                 "warning.",
                 cont=True),
            Rule("CA-RPT-15",
                 "The test covers these books only: specified foreign "
                 "property held outside them (a foreign bank account or "
                 "cash, shares held elsewhere) adds to the same $100,000, "
                 "so the report, its JSON (scope) and the checklist say "
                 "\"on these books\".", cont=True),
            Rule("CA-RPT-12",
                 "A property's cost amount is its adjusted cost base as "
                 "the gains engine computes it, day by day over the full "
                 "history: a superficial loss denied in any year is added "
                 "to the replacement's cost (s.53(1)(f)), an option's "
                 "premium follows the shares on exercise or assignment "
                 "(s.49(3) for a call, s.49(3.1) for a put), and a "
                 "futures contract has no cost amount."),
            Rule("CA-RPT-16",
                 "`taxjson stats` is a view, not a filing number: its "
                 "win/lose statistics count each closed trade's economic "
                 "P/L before any superficial-loss denial (the denied total "
                 "on its own line), over the taxable accounts unless a "
                 "registered account is named. A written option is one "
                 "trade from write to close whatever the premium timing; "
                 "an assigned one keeps its premium as the option's P/L, "
                 "which the view takes back out of the shares the s.49(3) "
                 "/ (3.1) fold put it in."),
            Rule("CA-RPT-03",
                 "`taxjson estimate`: federal and provincial tax (ON, BC, "
                 "AB) with AMT on top of your other income, for planning "
                 "only.", keys=("province",)),
            Rule("CA-RPT-04",
                 "Canadian dividends (a Canadian issuer: its CA ISIN when "
                 "the export gives one, else a Canadian listing) are "
                 f"treated as eligible ({_pct(_te().CA_ELIGIBLE_GROSSUP - 1)} "
                 "gross-up and credit; a capital-gains dividend in "
                 "[[capital_gains_dividends]] as a capital gain),",
                 cont=True),
            Rule("CA-RPT-05",
                 f"foreign dividends as ordinary income with withholding "
                 f"credited up to {_pct(_te().CA_FOREIGN_WITHHOLDING)};",
                 cont=True),
            Rule("CA-RPT-06", "interest is left out.", cont=True),
            Rule("CA-EST-TRUST",
                 "A Canadian trust's distribution (an ETF, REIT or fund "
                 "unit's T3 income) is counted with the eligible "
                 "dividends too: the export does not give the T3 split "
                 "(box 49 eligible dividends, box 26 other income, box "
                 "21 capital gains, box 42 return of capital), so the "
                 "estimate is close for an equity fund that flows out "
                 "eligible dividends and off for a REIT or bond fund — "
                 "the T3 decides.", cont=True),
            Rule("CA-EST-LOSSES",
                 "Net capital losses carried forward (--other-losses, "
                 f"full dollars) are netted against the year's gains "
                 f"before the {_pct(_te().CA_INCLUSION)} inclusion and used only up to them "
                 "(s.111(1)(b)); the rest is shown as unused. They are "
                 "deducted below net income (line 25300), so the "
                 "net-income tests (the BPA phase-down) still see the "
                 "gain."),
            Rule("CA-EST-DEDUCT",
                 "--deductions (lines 20700-23500 the AMT allows in "
                 "full: RRSP, FHSA, RPP ...) and --carrying-charges "
                 "(line 22100) lower net and taxable income, other "
                 "income first, never below zero; the AMT base takes the "
                 "deductions in full and the carrying charges at "
                 f"{_pct(_te().CA_AMT_CARRYING_CHARGE_ALLOWANCE)}."),
            Rule("CA-EST-BPA",
                 "The federal basic personal amount phases down on net "
                 "income from the enhanced amount to the minimum between "
                 "the starts of the 29% and 33% brackets; it is the only "
                 "non-refundable credit modelled.", cont=True),
            Rule("CA-EST-PROV",
                 "Provinces: Ontario (with its surtax and the Ontario "
                 "Health Premium, added after every credit), British "
                 "Columbia and Alberta. Quebec and the other provinces "
                 "are refused (no Quebec abatement, no low-income "
                 "reductions)."),
            Rule("CA-EST-FTC",
                 f"Foreign withholding is credited up to "
                 f"{_pct(_te().CA_FOREIGN_WITHHOLDING)} of the "
                 f"foreign dividends (the books' TAX rows, else "
                 f"{_pct(_te().CA_FOREIGN_WITHHOLDING)} assumed); what federal tax cannot absorb is credited "
                 "against provincial tax (form T2036), limited to the "
                 "provincial tax times foreign income over net income.",
                 cont=True),
            Rule("CA-EST-AMT",
                 f"The AMT check (post-2024 rules): "
                 f"{_pct(_te().CA_AMT_RATE)} over an "
                 "exemption at the start of the 29% bracket, on gains at "
                 f"100% (the claimable carryforward at "
                 f"{_pct(_te().CA_AMT_LOSS_ALLOWANCE)}), dividends at "
                 "their actual amount with no credit, the other income, "
                 f"the BPA credit at {_pct(_te().CA_AMT_CREDIT_ALLOWANCE)} "
                 "and the foreign tax credit in "
                 "full; the provincial share is the province's factor of "
                 "the federal excess (Ontario's surtax recomputed on it)."),
            Rule("CA-EST-VINTAGE",
                 "Rates, brackets and credits are the tax year's own "
                 "table; a year with none uses the newest earlier table "
                 "and a year before the earliest uses the earliest (the "
                 "printed vintage and a note say so — for such an early "
                 "year the AMT shown is the post-2024 regime, which did "
                 "not apply then)."),
            *_ca_estimate_tables(),
            Rule("CA-AMT-01",
                 "`taxjson amt [YEAR]`: the year's minimum tax line by "
                 "line (ITA s.127.5-127.55, form T691) — regular tax, the "
                 "adjusted taxable income (s.127.52) item by item, the "
                 "basic exemption (s.127.53), the rate (s.127.51), the "
                 "basic minimum tax credit (s.127.531) and the special "
                 "foreign tax credit (s.127.54), whether it binds, the "
                 "provincial AMT and the carryover. Every figure is the "
                 "estimate's own, so the two agree to the cent; an "
                 "earlier closed year prints what its close-year lock "
                 "recorded."),
            Rule("CA-AMT-02",
                 "A year whose federal minimum tax exceeds its regular "
                 "federal tax creates a minimum tax carryover equal to "
                 "the excess (its additional tax, s.120.2(3)); the "
                 "provincial AMT is not part of it.", cont=True),
            Rule("CA-AMT-03",
                 "A carryover can be applied only in the 7 years after "
                 "the year it arose (s.120.2): an older one is dropped "
                 "with a note, and one dated the project year or later is "
                 "refused.", cont=True),
            Rule("CA-AMT-04",
                 "The carryovers still open are recovered oldest year "
                 "first, up to the regular federal tax minus the federal "
                 "minimum tax — nothing in a year AMT binds — on federal "
                 "Schedule 1 line 40427, before the foreign tax credit.",
                 cont=True),
            Rule("CA-AMT-05",
                 "The province's share is the federal amount recovered "
                 "times the province's minimum-tax factor (the one "
                 "applied to the federal excess), deducted from basic "
                 "provincial tax before Ontario's surtax.", cont=True),
            Rule("CA-AMT-06",
                 "The estimate is incremental, so it counts against the "
                 "investment income only the change in recovery it "
                 "causes (the full return's recovery minus what the "
                 "other income alone would recover); instalments use "
                 "the full return's recovery.", cont=True),
            Rule("CA-AMT-07",
                 "What carries to the next year is each origin's "
                 "unrecovered amount still inside its 7 years plus the "
                 "year's own excess.", cont=True),
            Rule("CA-AMT-08",
                 "The carryover by year of origin is read from "
                 "[estimate] amt_carryover = { YEAR = AMOUNT } (from the "
                 "notice of assessment or T691); else from the latest "
                 "close-year lock before the project year (filed/<year>."
                 "json or prior_year_record). The estimate and `amt` say "
                 "which.", keys=("[estimate] amt_carryover",
                                 "prior_year_record")),
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
            Rule("CA-CARRY-01",
                 "`taxjson close-year` records in filed/<year>.json the "
                 "year's carry-forwards, from the same estimate: the net "
                 "capital loss carried in, created (the year's net loss), "
                 "applied (up to the year's gains) and carried out (100% "
                 "amounts); and the minimum tax carryover by year of "
                 "origin — opening, expired, recovered, created, carried "
                 "out. With no supported [settings] province it uses a "
                 "federal-only estimate (no provincial tax, credit or "
                 "minimum-tax factor) and says so; the carry-forwards it "
                 "records are federal either way — never another "
                 "province's tables standing in."),
            Rule("CA-CARRY-02",
                 "The estimate's net capital losses are --other-losses, "
                 "else [estimate] other_losses, else the balance the "
                 "latest close-year lock before the project year carried "
                 "out (filed/<year>.json or prior_year_record); input you "
                 "give always wins.", cont=True,
                 keys=("prior_year_record",)),
            Rule("CA-CARRY-03",
                 "It prints where each carry-forward came from, and notes "
                 "a lock older than last year (that year's changes are "
                 "missing).", cont=True),
            Rule("CA-CARRY-04",
                 "`taxjson carryover` takes the balance a close-year lock "
                 "recorded as carried out of its year as the running "
                 "balance at that year end (the lock is the record).",
                 cont=True),
            Rule("CA-CARRY-05",
                 "`taxjson handoff` flags a next-year input that differs "
                 "from what the closed year carried out: [estimate] "
                 "other_losses, [carryover] claimed's entry for that year "
                 "(vs the loss applied), and [estimate] amt_carryover by "
                 "year of origin.",
                 cont=True),
            Rule("CA-RPT-11",
                 "`taxjson instalments`: CRA instalments (ITA s.156) when "
                 f"net tax owing exceeds {_usd(_inst().THRESHOLD)} this "
                 "year and in one of the "
                 "two previous years — due March, June, September and "
                 "December 15 (the next business day on a weekend), on the "
                 "current-year, prior-year or CRA-reminder basis, with "
                 "s.161 interest at CRA's prescribed rate: on each due "
                 "date the least cumulative amount any supported method "
                 "requires by then (s.161(4.01)), interest charged on "
                 "each instalment from its due date less interest "
                 "credited on each payment from its date (CRA's A - B "
                 f"offset method, nothing charged at "
                 f"{_usd(_inst().INTEREST_MIN)} or less). A payment "
                 "made before January 1 counts only when its row says "
                 "`tax_year = YEAR`, and earns credit from January 1."),
            Rule("CA-INST-PRIOR",
                 "The prior-year test fails only when both earlier years' "
                 f"net tax is given and both are {_usd(_inst().THRESHOLD)} "
                 "or less; a year "
                 "not given is assumed to meet it, so instalments are "
                 "reported as required.", cont=True),
            Rule("CA-INST-INTEREST",
                 "Interest credited on early or extra payments only "
                 "offsets the charge: it is never refunded.",
                 cont=True),
            Rule("CA-INST-PENALTY",
                 f"The s.163.1 penalty is {_pct(_inst().PENALTY_SHARE)} of "
                 f"the net interest over the greater of "
                 f"{_usd(_inst().PENALTY_FLOOR)} and "
                 f"{_pct(_inst().PENALTY_ALT_FRACTION)} of the interest had "
                 "nothing been paid.", cont=True),
            *_ca_instalment_tables(),
            Rule("CA-SCAN-01",
                 "`taxjson scan`: a US-listed dividend payer held in a "
                 "TFSA is flagged — the 15% US withholding is "
                 "unrecoverable there, while an RRSP is exempt under the "
                 "Canada-US treaty (not checked) and a taxable account "
                 "can claim the foreign tax credit."),
            Rule("CA-SCAN-02",
                 "A Canadian issuer held through its US listing in a "
                 "taxable account or TFSA while it pays dividends is "
                 "flagged: its Canadian line pays the eligible dividend "
                 "in CAD with no conversion. The Canadian line is the "
                 "ticker.map target, else a listing of the same root on "
                 "any Canadian venue (.TO, .V, .CN, .NE, .VN) seen in the "
                 "books; a DISTINCT pair is not one.", cont=True),
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
        ("Planning tools (wash radar, sell-check, buy-check, "
         "safe-to-sell, harvest, watch)", [
            Rule("CA-PLAN-01",
                 "They apply the superficial-loss rule above on settle "
                 "dates, each sale on its own (a replacement that backs "
                 "an earlier loss, even one whose window has closed, "
                 "backs a sale today too; quantities across a split are "
                 "compared in today's units): a loss whose replacement is still held "
                 "can be rescued by selling the replacement so that it is "
                 "no longer held when day 30 settles (VIOLATION prints the "
                 "last trade date that does it, on the listing's "
                 "calendar; once that date has passed it says the loss is "
                 "denied). Rows that settle the same day are replayed in "
                 "trade-date order, as the engine does, and a written "
                 "option's buy-back loss is exempt (CA-SL-11) outside the "
                 "gains files' year too."),
            Rule("CA-PLAN-02",
                 "A long call on the shares bought in the window counts "
                 "as a replacement at its contract size (buy-check states "
                 "the denial per share and per standard contract); a "
                 "warrant, an "
                 "adjusted-series call or a futures option is a note to "
                 "check by hand, which sell-check and harvest repeat "
                 "whatever the row's verdict.", cont=True),
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
    xfer_acq = _transfers_as_acquisitions(s)
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
                 "market, not the quote or settlement currency's, in every "
                 "parser (IB, Questrade, RBC and the generic importer, "
                 "one rule): a US-dollar unit listed on the TSX "
                 "(SAMPLF.U.TO) settles on the Canadian calendar, a US stock "
                 "settled in Canadian dollars on the US one, and a "
                 "US-dollar line listed on the LSE (.L) or the ASX (.AX) "
                 "on that market's cycle.", cont=True),
            Rule("US-DATE-06",
                 "The generic importer uses a mapped settle column (one "
                 "more than 31 days after the trade is refused, more than "
                 "7 is flagged), else this cycle (settle_on_trade_date = "
                 "true keeps the trade date); its futures and its $0 "
                 "option closes on the expiry day follow the two rules "
                 "below.", cont=True),
            Rule("US-DATE-07", "Crypto settles on the trade date;",
                 cont=True),
            Rule("US-DATE-08",
                 "an option expiry is dated its expiry day, and so is a "
                 "right or warrant expiry (the date in the row's "
                 "description, \"AS OF\" or \"EXP\", when it is at most "
                 "7 days before the posting date; else the posting "
                 "date), settled the same day.", cont=True),
            Rule("US-DATE-14",
                 "An expiry the broker posts later (Questrade and RBC post "
                 "it the next business day) is moved back to the "
                 "contract's expiry date when posted at most 7 days after "
                 "it; one posted later keeps its posting date.",
                 cont=True),
            Rule("US-DATE-15",
                 "A trade in the same contract on its expiry day settles "
                 "no later than the expiry, even when the broker prints a "
                 "later settle date.", cont=True),
            Rule("US-DATE-16",
                 "Webull prints the SETTLE date: it is the row's settle "
                 "date and the trade date is walked back one settlement "
                 "cycle over business days (a sale printed Jan 2 traded "
                 "Dec 31, so under trade dates it is the earlier year's); "
                 "an option expiry row's date is the expiry itself.",
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
                 "or on a weekday the exchange is closed) and a US-dollar "
                 "option filled in Cboe Global Trading Hours (20:15 ET or "
                 "later, Sunday to Thursday) on a root with that session: "
                 + ", ".join(sorted(_evening_roots())) + " (market data "
                 "shipped in taxjson/data/markets.toml, extended or "
                 "overridden by ticker.map `EVENING ROOT [NO]` lines; the "
                 "run notes once per root when the built-in list moved a "
                 "fill). A fill on the ASX, HKEX, "
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
                 "A Questrade file written by `taxjson fetch` (the "
                 "taxjson-fetch plugin) keeps the "
                 "API's row order within a day, and a re-fetch merge keeps "
                 "it too. "
                 "A newest-first export is read bottom-up; rows of "
                 "different accounts at one moment follow the accounts' "
                 "order in taxjson.toml, in the run and in every "
                 "recompute of the blended book (check-filed, audit, "
                 "wash-sales --explain). Fixed places at one moment: an "
                 "opening "
                 "balance first, an assignment's option leg before its "
                 "stock leg, a split before the trades; basis adjustments "
                 "last."),
            Rule("US-DATE-17",
                 "Rows of ONE account at one moment that come from "
                 "different input files follow the files' name order "
                 "(a.tt before b.tt), which decides which lot FIFO "
                 "takes; give such rows distinct times or put them in "
                 "one file.", cont=True),
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
            Rule("US-DATE-11", _tz_rule_text(s),
                 keys=("local_timezone",)),
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
                 "row with no rate after that, or a currency with no "
                 "rates at all, stops the run naming the row's date and "
                 "currency pair — taxjson carries no built-in rate (the "
                 "stand-alone converters accept your own rate with "
                 "--default-rate). `taxjson fx-cash` counts a cash event with no rate row in those "
                 "5 days as unrated (named in its report), and `taxjson "
                 "crypto-sends` leaves a send (and a stablecoin pool row) "
                 "with none unpriced.", cont=True),
            Rule("US-FX-03",
                 "Gains on holding foreign cash (§988) are ordinary "
                 "income, not capital gains, and are NOT in the Form 8949 "
                 "totals: `taxjson fx-cash` estimates the year's net from "
                 "a pooled average cost per currency (fx_cash_gains = "
                 "true runs it after `taxjson run`); the §988(e) "
                 "exclusion for personal transactions is not modelled, "
                 "and there is no $200 annual exemption. The cash a "
                 "corporate action pays (cash in lieu of a fraction, "
                 "§356 boot) is currency received; a share-for-share "
                 "exchange moves none.",
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
            Rule("US-BASIS-CODES",
                 "A broker's internal security code (Questrade writes "
                 "one letter + digits, e.g. X000123, on rows of shares "
                 "transferred in) is the line a currency-journal leg "
                 "of the same description in the same account and "
                 "currency names (Questrade's BRW \"... JOURNAL "
                 "POSITION FROM CAD\": the security's US-dollar line); "
                 "else the security the account's own "
                 "trades of the same description name; else the security "
                 "of the transfer it arrived by (an outgoing transfer of "
                 "the same quantity in another export of the project, up "
                 "to 10 days before the arrival and 3 after, whose "
                 "security name agrees — one candidate only, and none "
                 "naming another share of the company); else, for a "
                 "code with no transfer-in, the one listing in the "
                 "project's books whose name is EQUAL to it, or the one "
                 "listing of the same broker's descriptions of which its "
                 "description is a cut-off prefix (the description is "
                 "exactly the export's width, three strong words "
                 "before the cut, no designator cut off). Names are "
                 "compared once case, punctuation, generic share words, "
                 "corporate-form words (INC, CORP, LTD), common "
                 "abbreviations (RES = RESOURCES, MFG, HLDGS, N V = NV, "
                 "& = AND) and broker boilerplate (REPSTG ..., TRANSFER "
                 "IN ..., a broker's name, IB's /domicile) are set aside. "
                 "The share designators always count: the class letter, "
                 "voting / subordinate / multiple voting, ADR vs "
                 "ordinary, preferred, units, warrants, rights, NEW — "
                 "class A is never booked as class C; only a transfer "
                 "that pairs by quantity and date with no other leg in "
                 "the window tolerates a class letter or ORDINARY / ADR "
                 "stated by ONE broker only, and not when a designator "
                 "was cut off with the boilerplate. Questrade's event "
                 "wording (CASH DIV ON ..., COMMON STOCK ...) is cut "
                 "from its own descriptions only, a designator after "
                 "COMMON STOCK kept. "
                 "Inferred codes are listed in "
                 "one note per account and by `taxjson transfers`; any "
                 "ticker.map rule naming the code (a rename, DELETE, "
                 "DISTINCT, a dated RENAME) wins and is recorded as such; "
                 "a code nothing identifies stays a security of its own "
                 "(ATTENTION, once per code, naming a near match and the "
                 "GLOBAL line to add if it is right). Identification "
                 "only: no tax rule changes.", cont=True),
            Rule("US-BASIS-RENAME",
                 "A ticker change is a dated event in the books (a broker "
                 "corporate-action row; a .tt line `RENAME YYYY-MM-DD OLD "
                 "NEW` in any account's .tt file, which applies to every "
                 "account of the same kind (securities or crypto, "
                 "US-CRYPTO-RENAME) whose books hold OLD and is recorded once; a "
                 ".tt SPLIT line; or, legacy, a ticker.map line `RENAME "
                 "OLD NEW YYYY-MM-DD`, still read): on that date the basis "
                 "lots and their holding periods carry from OLD to NEW, "
                 "and the wash-sale rule treats OLD before the date and "
                 "NEW after it as one security. A trade in OLD after "
                 "the date is a different security unless the declaration "
                 "says it is the renamed shares (`late=fold`, booked as "
                 "NEW); `late=separate` records another company reusing "
                 "the ticker. Such trades are listed by `taxjson renames` "
                 "and stop `run --strict` until declared; a line's "
                 "late= applies to its own account's late rows and to "
                 "every account without a line of its own (another "
                 "account's line may choose otherwise for its rows). "
                 "Every declaration of one change is one event, dated "
                 "the earliest declared; the changes apply in date "
                 "order, so a ticker changed twice (A to B, then B to "
                 "C) carries the position both times. Declarations that cannot all be true stop the "
                 "run naming the lines: OLD renamed to two symbols, one "
                 "change on two dates more than a week apart, a cycle "
                 "(A to B and B to A), one account choosing both "
                 "late=fold and late=separate. An undated "
                 "rename (GLOBAL, or RENAME without a date) applies to "
                 "every row of OLD. "
                 "IB's temporary symbol (a time stamp YYYYMMDDHHMMSS before "
                 "the ticker, given around a corporate action) listed "
                 "under the ticker's own contract id is that ticker: "
                 "its rows are booked as the ticker, no ticker.map line "
                 "needed (a ticker.map line naming the stamped symbol, in "
                 "any keyword, wins: it keeps its rows); a ticker change "
                 "IB shows only as one contract id under two symbols is "
                 "booked as this dated event (OLD the symbol whose trades "
                 "end first, the date 00:00 on NEW's earliest row of any "
                 "section — a trade, a transfer, a corporate action, a "
                 "dividend, a return of capital; the dates "
                 "are that contract id's own — a ticker another company "
                 "used has its own id), never toward a "
                 "temporary symbol or IB's `.OLD` placeholder, with a "
                 "Warning naming the way out "
                 "(ticker.map `DISTINCT OLD NEW`: two securities; a .tt "
                 "`RENAME ... late=separate`), only when the rows date it: "
                 "every OLD row of any section on an earlier day than "
                 "NEW's earliest row. Otherwise — NEW's rows begin on or "
                 "before OLD's last one, a symbol is listed under several "
                 "contract ids in one statement — nothing is booked and "
                 "an ATTENTION line gives the .tt line `RENAME <date> OLD "
                 "NEW` and why. Nothing is booked from the contract id "
                 "when a corporate action names OLD and NEW together (a "
                 "split or merger that changes the symbol: that row "
                 "books it) or a .tt SPLIT row moves OLD to NEW, nor when "
                 "a declaration renames OLD to another symbol (it "
                 "decides; ATTENTION); a .tt RENAME line or a "
                 "ticker.map rule joining the two books it instead. A "
                 "weaker look-alike (Questrade, RBC, Webull: two symbols "
                 "sharing a description, the new one going short) is only "
                 "suggested, as the .tt line. A RENAME naming an option "
                 "contract or a future is refused: a contract never "
                 "becomes shares (or shares a contract) by a ticker "
                 "change, an option follows its underlying's change (the "
                 "stock's line is the one to write), and another expiry, "
                 "strike or right is another contract. A RENAME between "
                 "two spellings of one listing in the books (a Canadian "
                 "venue folds into .TO: A.TO and A.CN) books nothing, "
                 "said as an Info line. A legacy ticker.map dated RENAME "
                 "naming an option or a future is refused the same "
                 "way.", cont=True),
            Rule("US-XLIST-01",
                 "Two listings of one company's same class of shares (a "
                 "US line and its Canadian line, two currency lines of "
                 "one share) are one security: one set of lots, "
                 "identical for the wash-sale rule. An ADR and the "
                 "ordinary shares it represents are never joined "
                 "automatically. `taxjson run` joins two listings itself, "
                 "as a ticker.map TOBASE line would, when a transfer "
                 "journal pairs them uniquely (an out-leg of one and an "
                 "in-leg of the other in your accounts, the same "
                 "quantity, within 5 business days, weekends not "
                 "counted) and the exports' security "
                 "names are EQUAL word for word once normalised (case, "
                 "punctuation, abbreviations, broker wording — a "
                 "dealer's event and trade-confirmation wording such as "
                 "CASH DIV ON, UNSOLICITED, WE ACTED AS PRINCIPAL, AVG "
                 "PRICE, its desk codes (a share designator in it "
                 "kept) — and the "
                 "generic words COMMON / SHARES set aside) — the "
                 "corporate form (LP, CORP, TRUST, FUND differ) and every "
                 "share designator (class, voting, ADR, preferred, unit, "
                 "HEDGED ...) included; a name that is a subset of the "
                 "other, or states a designator the other leaves out, is "
                 "not joined. "
                 "A broker's explicit journal between the two listings — one "
                 "account, one day, the same quantity, both legs in the "
                 "broker's journal wording (RBC's TFR \"TRANSFER TO C$\" / "
                 "\"FROM U$\" with its J reference, IB's InterDepot, "
                 "Questrade's BRW JOURNAL POSITION) — compares the two "
                 "legs' OWN names on that day instead of every name "
                 "either listing ever had (a fund renamed later, a "
                 "listing another broker names with other designators), "
                 "and a corporate-form word (LTD, CORP, INC ...) that ENDS "
                 "one leg's name while the other ends with none (never a "
                 "form word inside a name, never LP: a partnership is "
                 "not the company), or a leading THE, or a "
                 "NEW after a generic share word (\"COM NEW\"), does not "
                 "block it; corporate forms both names state must agree, "
                 "every share designator counts, and a name of either "
                 "listing that names another company refuses. Legs that "
                 "pair on their day pair before legs days apart, and a "
                 "reference the broker writes on both legs of one "
                 "journal (RBC's J~ reference, Questrade's journal pair) "
                 "pairs those two and no others, inside their account, "
                 "before any other leg is looked at: another account's "
                 "transfer of the same listing on that day never "
                 "cancels one of them, and a leg left over from such a "
                 "pair pairs with legs of its own account only. A "
                 "listing that several "
                 "such journals map onto (the base-currency line, its "
                 "other-currency line under two symbols over the years) "
                 "is no ambiguity when the listings mapped onto it are "
                 "one security with each other too — each one's own "
                 "name equal to the shared listing's name of its day "
                 "word for word, or their own names agreeing as a "
                 "journal's legs' must (a PLC and a CORP that each match "
                 "a name stating no form are two listings, suggested); "
                 "and a transfer between two listings "
                 "such a journal joined is part of that join when its "
                 "legs' names agree as a journal's must. "
                 "Each join is a Warning naming the pair and "
                 "the `DISTINCT X Y` ticker.map line that undoes it. A "
                 "ticker.map rule renaming either listing, or a DISTINCT "
                 "line for the pair, always wins; anything less certain "
                 "stays a suggestion (`taxjson ticker-map --suggest`) — "
                 "but two listings whose names name different companies "
                 "(no leading company word in common) are never joined "
                 "nor suggested, and a .US symbol whose rows name two "
                 "different companies, one of them a Canadian-listed "
                 "fund's US-dollar units, is a symbol collision: a "
                 "Warning and the EXTRACT line that gives the fund's "
                 "rows their own symbol (ROOT.U.TO), never a join "
                 "through it. A "
                 "Canadian broker's currency journal (Questrade BRW: a "
                 "security's CAD and US-dollar lines) is not expected "
                 "in a US project: its legs are read as such a transfer "
                 "journal, never joined on the broker's pairing alone."),
            Rule("US-XLIST-02",
                 "Each listing's symbol is read from the evidence, not "
                 "the row currency alone: Questrade and RBC write a bare "
                 "ticker and a currency, and a broker may file one "
                 "listing on the other currency's row (Questrade files "
                 "interlisted shares that arrived from another broker "
                 "under the TSX ticker on a USD row). Such a symbol is "
                 "the other listing (ROOT.US read as ROOT.TO) when its "
                 "transfer-in is the unique arrival of a transfer out of "
                 "the same quantity within 5 business days, under an EQUAL name, "
                 "of the same ticker's other listing or of ANOTHER ticker "
                 "on the same currency's listing (two US tickers never "
                 "name one company's identical shares); or, for shares "
                 "that arrived on a USD row by a transfer the books do "
                 "not pair, when ROOT.TO is in the project's books under "
                 "an equal name (shares bought at the broker keep the "
                 "listing its USD trade rows name). Never when a ticker.map line names the "
                 "symbol in any keyword (a rename, DELETE, DISTINCT, or a "
                 "lookup line: QUOTE, T1135, CRYPTO, STABLE, MULT, an "
                 "EXTRACT target; `DISTINCT ROOT.US ROOT.TO` keeps the row "
                 "currency's listing), when another broker that names "
                 "its listings trades ROOT.US, when a rename row joins "
                 "the two tickers, or when the account also holds the "
                 "other listing in another currency (then a TOBASE line "
                 "is suggested). Every row of the symbol in that "
                 "broker's exports of the account takes the listing; "
                 "each correction is a Warning, and `taxjson ticker-map "
                 "--suggest` shows the equivalent explicit lines."),
            Rule("US-XLIST-03",
                 "A journal you declare in an account's .tt file, `JOURNAL "
                 "YYYY-MM-DD FROM TO QTY` (QTY units moved from listing "
                 "FROM to listing TO of one security inside that account), "
                 "is not a sale: the two listings are one security (one "
                 "set of lots, identical for the wash-sale rule, the lots "
                 "keeping their basis and holding periods), joined as a "
                 "TOBASE line would, when something shows they are "
                 "listings of one security — one root (QZG.US, QZG.TO and "
                 "QZG.U.TO), or names that agree as a broker journal's "
                 "two legs' must, and no names of two different "
                 "companies; otherwise the line stops the run and a "
                 "ticker.map `TOBASE FROM TO` line is the deliberate "
                 "join — the same in a "
                 "Canadian project (CA-XLIST-04); a broker's currency "
                 "journal is not joined on its pairing alone here "
                 "(US-XLIST-01). Its two transfer legs are booked with the "
                 "account's transfer evidence, never as a purchase or a "
                 "sale, and move units inside that account only: they "
                 "never pair with another account's transfer (a move of "
                 "your own, the end of a transfer-in from outside the "
                 "books, an in-kind move), and neither does a broker's "
                 "journal pair (its legs pair inside their account "
                 "first). The missing-history checks read only the "
                 "journal's days as a journal's (buys before sales): "
                 "the legs' own days and, within 5 business days of "
                 "them in that account, a day with a buy of one listing "
                 "and a sale of the same quantity of the other; a "
                 "TOBASE line names no journal day and a sale and rebuy "
                 "of one listing is never one (missing history, with "
                 "the .tt JOURNAL line to add named). A journal whose two legs the broker's rows already "
                 "hold (the same account, symbols and quantity, within 5 "
                 "business days) is booked from them, not twice (an Info "
                 "line); with one leg there, only the other is booked. A "
                 "ticker.map rule naming either listing wins; a DISTINCT "
                 "line keeps them two securities (a Warning). An option "
                 "contract or a future is never journaled: such a line is "
                 "refused, as is one dated in the future or moving an "
                 "implausible quantity; two identical lines are one "
                 "journal (a Warning); a line moving more units than the "
                 "broker's own journal between the same listings on its "
                 "date stops the run, and one dated a business day from "
                 "it (or on its trades' day) is booked and said as a "
                 "Warning. The legacy "
                 "ticker.map `JOURNAL FROM TO` line is read as `TOBASE "
                 "FROM TO`, said once per run; `taxjson format-map "
                 "--write` rewrites it."),
            Rule("US-BASIS-07",
                 "Accounts typed \"sheltered\" (an IRA, Roth IRA, "
                 "401(k)...) are tracked but kept out of the filing "
                 "totals (Form 8949, `sum`, the carryover); for the "
                 "wash-sale rule they count (US-WASH-04, US-WASH-11)."),
            Rule("US-BASIS-05",
                 "A transfer into a taxable account stops the run until "
                 "the original purchase is declared (.tt ACQUIRED line). "
                 "With transfers = false (the default) a move between two "
                 "of your own taxable accounts is not a sale: the run "
                 "pairs its out and in rows (one symbol, within 10 days, "
                 "the same quantity or two deliveries adding up to it; "
                 "coins by the crypto-sends pairing, US-CRYPTO-05) and the "
                 "sending account's lots, first in first out, go to the "
                 "receiving account with their basis and purchase dates "
                 "(holding period). Out and in rows that look like a move "
                 "but do not pair, or a move larger than the lots the "
                 "sender holds, are said ATTENTION (--strict stops); "
                 "those shares' sales are then reported by hand."),
            Rule("US-BASIS-TRANSFER-BV",
                 "A broker's transfer rows in a taxable account are kept "
                 "out of the books. Shares that arrive from outside your "
                 "books (a transfer-in no transfer-out of yours cancels: "
                 "the same security after ticker.map, the same quantity "
                 "and closest date first, across your taxable accounts, "
                 "or another listing's leg of a journal) take the basis "
                 "the broker states on the row (Questrade, RBC) as the "
                 "carryover basis of one lot dated the arrival, said as "
                 "ATTENTION: its holding period starts on the arrival "
                 "date, not the original purchase (§1223 tacking is not "
                 "applied), so enter the original lots with their "
                 "purchase dates for long-term treatment. A transfer "
                 "VALUE that is a market value (IB) is never a basis: "
                 "those shares stay out with no basis, said as "
                 "ATTENTION. A .tt purchase of the security in that "
                 "account dated on or before the arrival, or a "
                 "missing_history.json entry for it, covers it instead. "
                 "The arrival is not a §1091 replacement."),
            Rule("US-DIST-01",
                 "[[distributions]] (taxjson.toml): a non-cash distribution "
                 "(a reinvested capital-gain distribution, a late return-of-capital "
                 "factor) becomes a basis adjustment sized on the shares "
                 "held on its record date — the settled position, each "
                 "ticker's own shares — and booked on those lots only "
                 "(a trade straddling the record date is not the "
                 "holder's). The per-share amount is in the project's "
                 "base currency (a US-listed fund's USD factor is "
                 "converted by the user first). Its "
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
                 "over the open lots, for every issuer wherever it is "
                 "resident — Canada's foreign-issuer rule never applies "
                 "(`roc-sum` totals it against Form 1099-DIV box 3)."),
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
                 "Shares sold with no purchase in your files (bought "
                 "before the data starts) go in missing_history.json "
                 "(its old name phantoms.json is still read): sales that "
                 "draw on them have an unknown cost — they are listed "
                 "for manual reporting and left out of the totals. A loss within 30 days "
                 "(trade dates) of such a sale, or such a sale at a loss "
                 "with a purchase in that window, is flagged for a manual "
                 "wash-sale check (not for crypto accounts)."),
            Rule("US-BASIS-08",
                 "A broker's own figure for such shares — IB's Basis on a "
                 "sale it codes closing, with its Closed Lots when the "
                 "statement lists them, or the book value a transfer-in "
                 "states (Questrade, RBC) — is evidence, never booked. `find-missing-history "
                 "--write-purchases` drafts it as .tt purchase lines in a "
                 "file the run does not read (inputs/<account>/"
                 "purchases_draft.tt.txt): one line per lot IB lists, with "
                 "its purchase date and cost (that lot's basis and holding "
                 "period); without lot detail one line whose purchase date "
                 "is a placeholder the run refuses until you fill it in, "
                 "since the holding period needs the real date. A line "
                 "you keep (renamed to .tt) is your own statement of the "
                 "basis."),
            Rule("US-OPEN-01",
                 "An opening balance (`taxjson opening`, a .tt OPENING "
                 "line from a broker's positions report) is one line per "
                 "lot with the lot's real purchase date: the lot's basis "
                 "is the report's cost, its holding period (US-HOLD-01) "
                 "runs from that date and FIFO places it there. A line "
                 "without a lot date stops the run. It is not a purchase "
                 "on the snapshot day: never a wash-sale replacement; a "
                 "lot whose own date falls within 30 days of a loss is "
                 "flagged for a manual check (that purchase was real)."),
            Rule("US-OPEN-02",
                 "Its cost is in US dollars: a lot in another currency "
                 "stops the run (its basis is the dollar cost on its own "
                 "purchase date — enter it converted).", cont=True),
            Rule("US-OPEN-03",
                 "The snapshot replaces the account's earlier history of "
                 "its symbols: the account's other trade, transfer, split "
                 "and basis-adjustment rows of a snapshot symbol dated on "
                 "or before the snapshot day are left out of the books "
                 "(income rows stay; other symbols and other accounts keep "
                 "theirs), so no share is counted twice. A sale of the tax "
                 "year among them stops the run; one symbol has one "
                 "snapshot date per account.", cont=True),
            Rule("US-STKDIV-01",
                 "A stock dividend is not income (§305(a)): the basis of "
                 "the shares held is spread over the old and new shares "
                 "(§307), the new shares keep the old shares' purchase "
                 "dates (§1223(5)), and they are not a purchase for the "
                 "wash-sale rule (nor a \"recent buy\" in the wash "
                 "radar, sell-check, buy-check or harvest)."),
            Rule("US-STKDIV-02",
                 "A taxable stock dividend (§305(b), e.g. one with a cash "
                 "option) is not detected: enter it by hand. Shares of "
                 "ANOTHER security (another class) paid as a stock "
                 "dividend are not booked: the parse says UNBOOKED; enter "
                 "them and the §307 basis split by hand.", cont=True),
            Rule("US-STKDIV-03",
                 "Received with no shares held (history missing, or sold "
                 "before the pay date), the new shares are booked as a $0 "
                 "purchase with a warning — still not a wash-sale "
                 "replacement: add the missing history (or adjust the "
                 "sold lots) so §307 can spread the basis.",
                 cont=True),
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
            Rule("US-WASH-21",
                 "and a purchase in the account that sells at the loss "
                 "replaces only with the shares of it still unsold at the "
                 "loss: shares sold (first in, first out) before the loss "
                 "no longer wash it — unlike an IRA purchase (US-WASH-11) "
                 "or one in another of your taxable accounts "
                 "(US-WASH-22) —",
                 cont=True),
            Rule("US-WASH-07",
                 "and look-alike securities are not detected.", cont=True),
            Rule("US-WASH-08",
                 "Matching across accounts needs a full `taxjson run` "
                 "(not `--account`)."),
            Rule("US-WASH-20",
                 "Replacements are matched in the order acquired (Reg. "
                 "§1.1091-1(c)): the earliest purchase in the window first, "
                 "before or after the sale alike, and losses in the order "
                 "sold, so an earlier loss takes a shared replacement "
                 "first. Purchases at the same moment go to your taxable "
                 "accounts first, then IRAs, then affiliated accounts, "
                 "then in the export's row order (accounts in "
                 "taxjson.toml order), never by the account's name.",
                 cont=True),
            Rule("US-WASH-09",
                 "The disallowed loss is added to the replacement lot's "
                 "basis"),
            Rule("US-WASH-10", "and its holding period carries over.",
                 cont=True),
            Rule("US-WASH-11",
                 "A replacement bought in an IRA makes it permanent, even "
                 "when the IRA sold it again before your loss.",
                 cont=True),
            Rule("US-WASH-22",
                 "A purchase in ANOTHER of your taxable accounts inside "
                 "the window replaces the loss even when that account "
                 "sold the shares before the loss sale (§1091 has no "
                 "still-held test): the disallowed loss is added to the "
                 "basis of those shares, so that earlier sale's gain "
                 "falls by it, and the loss shares' holding period "
                 "carries over to them — unless that sale's own loss was "
                 "disallowed (then it is named for a manual check and not "
                 "matched). When that sale is in an earlier, filed year "
                 "(filed/<year>.json), the filed year is left as filed: "
                 "the amount is booked as a loss of that term on the loss "
                 "sale's date, and an ATTENTION line names the earlier "
                 "sale, whose return may need an amendment (Form "
                 "1040-X); an earlier year not filed changes, with a "
                 "note."),
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
                 "engine), sized at the contract's size "
                 "(100 shares for a standard equity option — ASSUMED, and "
                 "noted once per root, when a non-IB export does not state "
                 "it; a mini's 10 when the row's own amount shows it; a "
                 "ticker.map `MULT ROOT N` line overrides), each contract "
                 "flagged against one loss's shares only; a buy that "
                 "closes a written call is not an acquisition. A root "
                 "that drops the share class (SAMPLCB for SAMPLC.B) names that "
                 "class line."),
            Rule("US-WASH-14",
                 "A warrant or right bought in the window is flagged for a "
                 "manual wash-sale check only.", cont=True),
            Rule("US-WASH-15",
                 "So is a call on an adjusted option series (root + digit, "
                 "e.g. SAMPLE1) or a futures option on the loss's futures "
                 "contract, however it is spelled (a commodity future is "
                 "usually outside §1091). Nothing is disallowed for a "
                 "flag, so `taxjson wash-sales` lists each one and the "
                 "checklist's wash-reviewed step stays open until you "
                 "decide them.",
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
            (Rule("US-WASH-24",
                  "A retirement account's transfer in or out (one no "
                  "move between your own retirement accounts and no "
                  "zero-net journal pairs) is booked as a purchase or "
                  "sale on its date. A transfer-in inside a loss's window "
                  "stops the run until it is declared: a DECLARED "
                  "counter-TRANSFER .tt pair for a custody move, a "
                  "BUYSELL dated the true day for a purchase "
                  "(transfers_as_acquisitions = true).",
                  keys=("transfers_as_acquisitions",))
             if xfer_acq else
             Rule("US-WASH-23",
                  "A retirement account's transfer in or out (a rollover, "
                  "a custody move) is a move between accounts, not a "
                  "purchase or sale: it never replaces a loss, whatever "
                  "trades sit near it. One warning per run lists each "
                  "transfer-in inside a taxable loss's window, and each "
                  "netted move (between two retirement accounts, or a "
                  "zero-net cluster in one) with a leg inside it; a "
                  "purchase recorded as a BUYSELL is counted "
                  "(transfers_as_acquisitions = false).",
                  keys=("transfers_as_acquisitions",))),
        ]),
        ("In-kind moves to and from retirement accounts", [
            Rule("US-INKIND-01",
                 "An IRA, Roth IRA, 401(k), HSA or 529 takes contributions "
                 "in cash only: a transfer of shares from a taxable account "
                 "into one (the run pairs a taxable account's transfer-out "
                 "with a retirement account's transfer-in of the same "
                 "security and quantity within 10 days, across brokers) "
                 "is a likely error — warned about, NOT booked: the shares "
                 "stay in the taxable books (`run --strict` stops). Legs "
                 "of the same kind pair first across the project, so a "
                 "move between two taxable or two retirement accounts "
                 "stays a move of your own whatever the gap; a taxable leg "
                 "and a retirement leg pair only when neither has another "
                 "plausible partner, else the pair is listed NOT booked as "
                 "ambiguous (`run --strict` stops) until a .tt `INKIND` "
                 "line declares it, or `INKIND <date> <symbol> <qty> "
                 "plan=own` declares the row a move of your own."),
            Rule("US-INKIND-02",
                 "A distribution in kind from a retirement account is an "
                 "acquisition by the taxable account at fair market value "
                 "on the distribution date: its basis, and its holding "
                 "period starts then; a §1091 replacement like any "
                 "purchase. The taxable amount is on Form 1099-R: the "
                 "warning says so; the books do not book the income."),
            Rule("US-INKIND-03",
                 "The fair market value, in order: a .tt `INKIND` line in "
                 "the taxable account's folder; the market value the "
                 "broker states on the transfer row (IB's Transfers "
                 "`Market Value`); "
                 "else Yahoo's close on the date (or the last one before "
                 "it), marked ESTIMATED (split-adjusted). With "
                 "TAXJSON_OFFLINE and no cached close the run stops and "
                 "names the INKIND line to add. One warning per run lists "
                 "each move, its value and its source."),
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
                 "significant holder ("
                 + _corp().us_significant_holder_test()
                 + ") attaches the Reg. §1.368-3 statement),", cont=True),
            Rule("US-CORP-05",
                 "or reorg_368_boot (§356, per lot of old shares — Reg. "
                 "§1.356-1(b): each lot's gain is its share of the new "
                 "shares' value and the cash less its basis, recognised "
                 "up to its share of the cash, a loss never; its new "
                 "shares' basis = its basis - its cash + its gain, and "
                 "they keep its purchase date (§1223(1)); the new shares "
                 "are not a purchase for the wash-sale rule).",
                 cont=True),
            Rule("US-CORP-09",
                 "Cash in lieu of a fractional share is a sale of the "
                 "fraction for the cash (Questrade books the fraction at "
                 "$0 the same day first); FIFO takes the units sold from "
                 "the oldest lot, with its basis and holding period."),
            Rule("US-CORP-10",
                 "A merger paid wholly in cash (IB \"Merged(Acquisition) "
                 "FOR USD 30.00 PER SHARE\") is a sale of the shares at "
                 "the cash proceeds;", cont=True),
            Rule("US-CORP-11",
                 "one paying shares AND cash is not booked from the "
                 "export: it stops the run as an UNSUPPORTED event to "
                 "enter by hand (.tt lines, e.g. per reorg_368_boot's "
                 "§356 rule).", cont=True),
            Rule("US-CORP-06",
                 "Spin-offs: taxable_distribution_301 (a §301 "
                 "distribution: income at FMV, which is also the new "
                 "shares' cost)"),
            Rule("US-CORP-07",
                 "or tax_free_355 (§355: the basis moved to the spin-off "
                 "is the US-dollar amount you give, per the company's "
                 "Form 8937, booked exactly even on a non-US listing; "
                 "§358(b). Every parent lot gives up the same fraction of "
                 "its own basis (Reg. §1.358-2), and each parent block "
                 "gets its block of spun-off shares with that basis and "
                 "the block's purchase date — the holding period tacks, "
                 "§1223(1). It never books a gain: an amount beyond the "
                 "parent's basis is capped at it, with an ATTENTION line. "
                 "The spun-off shares are not a purchase for the "
                 "wash-sale rule. Only a significant distributee (the "
                 "same test) attaches the Reg. §1.355-5 statement).",
                 cont=True),
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
                 "value. Kraken's wallet suffixes (DOT.S and the "
                 ".M/.F/.B/.P/.HOLD suffixes) and its bonded-staking codes "
                 "(<COIN><two-digit lock period>.S, e.g. DOT28.S, when the "
                 "coin itself is in the same export; otherwise noted with "
                 "the line to add) name the same coin as the bare code, "
                 "so a 1:1 swap between them is not a sale. Kraken's "
                 "legacy codes (XXBT, XETH, XDG ...) are the common "
                 "tickers (market data, taxjson/data/markets.toml). "
                 "Any other code is a coin of its own unless the "
                 "project's ticker.map folds it with a `GLOBAL CODE COIN` "
                 "line between bare codes, which the Coinbase and Kraken "
                 "parsers apply before they read a row (a staked or "
                 "wrapped code such as ETH2 — taxjson ships no such fold; "
                 "a 1:1 swap between ETH and ETH2 without it is a sale, "
                 "noted once with the line to add)."),
            Rule("US-CRYPTO-06",
                 "So is Coinbase's ETH2 (its staked ETH) under `GLOBAL "
                 "ETH2 ETH`: it is booked as ETH, and a \"Converted ETH "
                 "to ETH2\" row is not a sale (unequal quantities stop "
                 "the parse).", cont=True),
            Rule("US-CRYPTO-07",
                 "A Kraken dust sweep (several coins converted at once "
                 "into one receipt) is a sale of each coin: the receipt "
                 "is split over them by the export's amountusd, or "
                 "equally when the export has none (the parse says "
                 "which; a US-dollar leg with no amountusd is its own "
                 "amount). A foreign-currency leg of a sweep (CAD) is "
                 "cash, not a sale: its share of the receipt is a "
                 "currency conversion. A coin leg under 1e-09 units "
                 "(Kraken writes amounts to ten decimals) is left out "
                 "only when its value is negligible too: its own "
                 "amountusd (when the export has one) and the share of "
                 "the other side it would take are each at most 0.01 USD "
                 "(a one-for-one trade: both sides at most 0.01 USD). It "
                 "is then the disposition (or acquisition) of a "
                 "negligible amount: its share of the receipt goes to "
                 "the sweep's other legs by amountusd, and spent coins "
                 "stay in the lots as a residue (folded into the sale "
                 "that closes the lot, US-CRYPTO-08); when every coin leg "
                 "is that small no sale is booked. The parse names each "
                 "such leg (coin, amount — a received leg net of its fee "
                 "— and USD value) once per trade. A leg that small worth "
                 "more, or whose value is unknown, is refused with the "
                 "file, the masked refid, the coin and the date, never "
                 "dropped; so is a spend or receive row of amount 0. A "
                 "Kraken coin fee or reward under 1e-09 units is left out "
                 "only when its feeusd/amountusd is missing or at most "
                 "0.01 USD, else refused.", cont=True),
            Rule("US-CRYPTO-RENAME",
                 "A ticker change of a coin (the same token, which an "
                 "exchange now lists under a new code) is declared like a "
                 "security's: a .tt line `RENAME YYYY-MM-DD OLD NEW` in a "
                 "crypto account's .tt file. It carries the basis lots "
                 "and their holding periods from OLD to NEW on that date "
                 "(US-CRYPTO-01: each coin is its own property): no "
                 "disposition. A RENAME applies only to accounts of its "
                 "declaring account's kind — a crypto account's to the "
                 "crypto accounts, a securities account's to the "
                 "securities accounts (US-BASIS-RENAME): a security's "
                 "ticker change never moves a coin, nor a coin's a "
                 "security. A swap into a different token (a migration "
                 "to a new chain, a redenomination) is not a ticker "
                 "change: book it as the trade it is."),
            Rule("US-CRYPTO-08",
                 "The US engine counts less than 1e-08 units as zero: a "
                 "purchase or sale row under 1e-08 units is not booked "
                 "(its units and money are left out of the lots and Form "
                 "8949), a lot residue of at most 1e-08 units is folded "
                 "into the sale that closes the lot (its cost goes with "
                 "that sale), and a sale's excess of at most 1e-08 units "
                 "over the units held opens no position (the whole "
                 "proceeds are on the units held). Each case is named in "
                 "a warning."),
            Rule("US-CRYPTO-02",
                 "USD stablecoins (" + _stable_text() + "), on "
                 "Kraken and Coinbase alike, are property like any coin: "
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
                 "account; a move paired between two of your taxable "
                 "crypto accounts carries the coins that arrived — the "
                 "sending account's lots, first in first out, with their "
                 "basis and purchase dates — to the receiving account "
                 "(US-BASIS-05; the run then blends those crypto accounts, "
                 "with no wash-sale rule). When fewer coins arrive and the "
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
                 "(contracts x the contract size — a ticker.map MULT line, "
                 "else the size the export states (IB) or a mini's 10 its "
                 "own amount shows, else 100 ASSUMED and noted once per "
                 "option root; one per futures option), priced at the "
                 "strike, dated up to 3 "
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
                 "broad-based index option or an option on a future is "
                 "a §1256 contract: kept off Form 8949 and listed for "
                 "Form 6781, as futures are. The index option roots: "
                 + ", ".join(sorted(_index_option_roots())) + " (market "
                 "data shipped in taxjson/data/markets.toml, extended or "
                 "overridden by ticker.map `INDEXOPT ROOT [NO]` lines; "
                 "the run notes once per root when the built-in list "
                 "decided). An index option whose root is not listed is "
                 "filed as an ordinary option."),
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
            Rule("US-RPT-11",
                 "From tax year 2025 a crypto account's dispositions are "
                 "digital assets: their own Form 8949 group on boxes "
                 "G/H/I (short-term) or J/K/L (long-term), with their own "
                 "totals in the export, `sum` and the close-year lock; "
                 "securities stay on A/B/C and D/E/F. Earlier years put "
                 "them with the securities.", cont=True),
            Rule("US-RPT-03",
                 "`--form txf` writes a TurboTax TXF file of the "
                 "securities rows (boxes A-F); boxes G-L have no TXF "
                 "code, so their rows are left out with a warning.",
                 cont=True),
            Rule("US-RPT-09",
                 "Form 8949 cells are rounded half-up to the cent and (h) "
                 "= (d) - (e) + (g) on the rounded cells, so a half-cent "
                 "wash-sale adjustment shows as the allowed gain the other "
                 "reports print."),
            Rule("US-RPT-12",
                 "`taxjson stats` is a view, not a filing number: its "
                 "win/lose statistics count each closed trade's economic "
                 "P/L before any wash-sale denial (the denied total on its "
                 "own line), over the taxable accounts unless a retirement "
                 "account (IRA) is named. A written option is one trade "
                 "from write to close; an assigned one keeps its premium "
                 "as the option's P/L, which the view takes back out of "
                 "the shares' amount realized or basis it was folded "
                 "into."),
            Rule("US-RPT-04",
                 "`taxjson estimate`: federal tax only (single filer, "
                 "standard deduction, NIIT), for planning."),
            Rule("US-RPT-07",
                 "It treats every dividend as qualified, payments in lieu "
                 "and staking as ordinary income, gains with no term as "
                 "short-term, §1256 P/L as short-term (no 60/40 split; "
                 "it names the amount), and a net capital loss as "
                 "offsetting up to "
                 f"{_usd(_te().US_ORDINARY_LOSS_CAP)} of ordinary income; foreign tax credits, "
                 "interest and state tax are left out.", cont=True),
            Rule("US-EST-CARRY-TERM",
                 "A capital loss carryover keeps its term: --other-losses "
                 "is the short-term carryover (Schedule D line 6) and "
                 "--long-term-losses the long-term one (line 14); each "
                 "offsets gains of its own term first, the rest the other "
                 "term's (line 16).", cont=True),
            Rule("US-EST-NIIT-LOSS",
                 f"That up-to-{_usd(_te().US_ORDINARY_LOSS_CAP)} capital "
                 "loss deduction also reduces "
                 "net investment income for NIIT (Form 8960 line 5a).",
                 cont=True),
            Rule("US-EST-CARRY-TI",
                 "The carryforward it shows counts as used only the part "
                 f"of the {_usd(_te().US_ORDINARY_LOSS_CAP)} that taxable "
                 "income absorbs (Capital Loss "
                 "Carryover Worksheet line 4).", cont=True),
            Rule("US-EST-VINTAGE",
                 "Brackets, the standard deduction and the capital-gain "
                 "brackets are the tax year's own table; a year with none "
                 "uses the newest earlier table and a year before the "
                 "earliest uses the earliest (the printed vintage and a "
                 "note say so).", cont=True),
            *_us_estimate_tables(),
            Rule("US-RPT-08",
                 "`taxjson carryover`: the short- and long-term capital "
                 "loss carryover (Schedule D worksheet), assuming the "
                 f"{_usd(_te().US_ORDINARY_LOSS_CAP)} ordinary offset is "
                 "used each year unless "
                 "taxjson.toml's [carryover] claimed records otherwise. A "
                 "year before the "
                 "project year that has a close-year lock (filed/<year>.json "
                 "or prior_year_record) takes the lock's filed Form 8949 "
                 "Part I / Part II gains instead of the rebuilt ones; a "
                 "year after the project year is partial and the carryover "
                 "stops at the project year."),
            Rule("US-CARRY-01",
                 "`taxjson close-year` records in filed/<year>.json the "
                 "short- and long-term capital loss carryover carried in "
                 "and carried out, from the same estimate (the "
                 f"{_usd(_te().US_ORDINARY_LOSS_CAP)} "
                 "deduction counted as used only as far as taxable income "
                 "absorbs it, short-term first)."),
            Rule("US-CARRY-02",
                 "The estimate's carryovers are --other-losses / "
                 "--long-term-losses (or their [estimate] keys); when "
                 "neither is given, the short- and long-term carryover "
                 "the latest close-year lock before the project year "
                 "carried out — it prints where they came from.",
                 cont=True, keys=("prior_year_record",)),
            Rule("US-CARRY-03",
                 "`taxjson handoff` flags [estimate] other_losses / "
                 "long_term_losses that differ from what the closed year "
                 "carried out.", cont=True),
            Rule("US-CARRY-04",
                 "`taxjson carryover` takes the short- and long-term "
                 "carryover a close-year lock recorded as the running "
                 "carryover at that year end.", cont=True),
            Rule("US-AMT-01",
                 "The alternative minimum tax (Form 6251) and its credit "
                 "(Form 8801) are not modelled: there is no `amt` "
                 "command and no minimum tax carryover in a US project.",
                 cont=True),
            Rule("US-RPT-05",
                 "`taxjson edge-cases`: every trade whose tax year or "
                 "wash-sale verdict turns on a boundary, the window on "
                 "trade dates whatever tax_date says; with no still-held "
                 "test, a sale near day 30 decides nothing, a long call "
                 "is listed as a warning only, a stock dividend is not a "
                 "purchase (never an in-window acquisition), and crypto "
                 "has no window."),
            Rule("US-RPT-10",
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
        ("Planning tools (wash radar, sell-check, buy-check, "
         "safe-to-sell, harvest, watch)", [
            Rule("US-PLAN-01",
                 "Each recent loss's verdict is the US engine's own, as "
                 "of the date: the window on trade dates, purchases in "
                 "every account, IRAs included, and no still-held test — "
                 "a washed loss shows as WASHED, and no later sale "
                 "undoes it (the disallowed loss is in the replacement's "
                 "basis, or lost for good when the replacement is in an "
                 "IRA, US-WASH-11)."),
            Rule("US-PLAN-02",
                 "A long call bought in the window is a note only, for an "
                 "existing loss and for a loss sale today, as are a "
                 "warrant, an adjusted-series call and a futures option; "
                 "an IRA purchase the engine already matched to an "
                 "earlier loss is not counted again (share for share), "
                 "and a short position's trigger is a new short sale: "
                 "after a short-cover loss a buy is never a replacement "
                 "(buy-check says so), only a re-short before the window "
                 "closes is. A futures contract or an option on one gets "
                 "no re-entry date (outside §1091, US-WASH-18), and the "
                 "notes are repeated by sell-check and harvest whatever "
                 "the row's verdict.",
                 cont=True),
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
                 "sharing its root (SAMPLT.US) keeps its own verdict under "
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


def render(country: str, settings: Dict[str, Any],
           width: Optional[int] = None, ids: bool = False) -> str:
    """The plain text: a title, then each section's statements as `- `
    items wrapped at `width` (default: the house width, lib/out; 0 = one
    line per statement). Words and rule ids are never broken."""
    from taxjson.lib.out import Doc
    c = _C.canonical_country(country)
    name = ("United States (experimental)" if c == _C.USA
            else _C.DISPLAY_NAME[c])
    year = settings.get("year")
    d = Doc(f"TAX LOGIC — {name}" + (f", tax year {year}" if year else "")
            + ", with this project's settings", width_=width)
    for title, rules in sections(c, settings, ids=ids):
        d.section(title.upper())
        d.items(rules, indent="  ")
    d.blank()
    d.para("Detail and sources: README.md and REFERENCES.md. taxjson "
           "computes; it does not give tax advice.")
    return d.text()


def variants(country: str):
    """Every settings combination of VARIANT_AXES for `country`."""
    c = _C.canonical_country(country)
    axes = VARIANT_AXES[c]
    year = 2026
    for combo in itertools.product(*axes.values()):
        st = {k: (year if v == YEAR else v)
              for k, v in zip(axes, combo) if v is not None}
        st["country"] = c
        st["year"] = year
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


# ---------------------------------------------------------------------
# Law constants, rendered from the constants the code computes with
# (lib/tax_estimate, bin/taxjson_instalments, lib/corp_actions): one
# source, so the statement cannot drift from the number used. Each
# country's tables are rendered only in that country's section.

_PROVINCE_NAMES = {"ON": "Ontario", "BC": "British Columbia",
                   "AB": "Alberta"}


def _te():
    from taxjson.lib import tax_estimate
    return tax_estimate


def _corp():
    from taxjson.lib import corp_actions
    return corp_actions


def _inst():
    from taxjson.bin import taxjson_instalments
    return taxjson_instalments


def _pct(rate: float) -> str:
    from taxjson.lib.tax_estimate import fmt_pct
    return fmt_pct(rate)


def _usd(amount: float) -> str:
    from taxjson.lib.tax_estimate import fmt_dollars
    return fmt_dollars(amount)


def _bands(brackets) -> str:
    """'15% to $55,867, 20.5% to $111,733, 33% above'."""
    out = []
    for upper, rate in brackets:
        out.append(f"{_pct(rate)} above" if upper == float("inf")
                   else f"{_pct(rate)} to {_usd(upper)}")
    return ", ".join(out)


def _vintages() -> List[Tuple[str, Dict[str, Any]]]:
    from taxjson.lib.tax_estimate import _VINTAGES
    return sorted(_VINTAGES.items(), key=lambda kv: int(kv[0]))


def _ca_fed_table() -> str:
    parts = [f"{y}: {_bands(t['CA_FED_BRACKETS'])}; basic personal "
             f"amount {_usd(t['CA_FED_BPA'])} down to "
             f"{_usd(t['CA_FED_BPA_MIN'])}"
             for y, t in _vintages()]
    return ("Federal tax by year (taxable income bands; the basic "
            "personal amount, enhanced down to minimum): "
            + ". ".join(parts) + ".")


def _ca_prov_table(code: str) -> str:
    parts = []
    for y, t in _vintages():
        p = t["CA_PROVINCES"].get(code)
        if p is None:
            continue
        bits = [_bands(p["brackets"]), f"basic personal amount "
                f"{_usd(p['bpa'])}"]
        if p.get("surtax"):
            bits.append("surtax " + " plus ".join(
                f"{_pct(r)} of basic provincial tax over {_usd(thr)}"
                for thr, r in p["surtax"]))
        bits.append(f"minimum tax factor {_pct(p['amt_factor'])}"
                    + (" (assumed until the year's form is published)"
                       if p.get("amt_factor_assumed") else ""))
        parts.append(f"{y}: " + "; ".join(bits))
    return (f"{_PROVINCE_NAMES.get(code, code)} by year: "
            + ". ".join(parts) + ".")


def _ca_provinces() -> List[str]:
    seen: List[str] = []
    for _y, t in _vintages():
        for code in t["CA_PROVINCES"]:
            if code not in seen:
                seen.append(code)
    return seen


def _ca_dtc_text() -> str:
    from taxjson.lib.tax_estimate import (CA_ELIGIBLE_GROSSUP,
                                          CA_FED_DTC_ELIGIBLE)
    prov = []
    for code in _ca_provinces():
        rates = {y: t["CA_PROVINCES"][code]["dtc_eligible"]
                 for y, t in _vintages() if code in t["CA_PROVINCES"]}
        if len(set(rates.values())) == 1:
            prov.append(f"{code} {_pct(next(iter(rates.values())))}")
        else:
            prov.append(f"{code} " + ", ".join(
                f"{_pct(r)} in {y}" for y, r in rates.items()))
    return (f"Eligible dividends are grossed up "
            f"{_pct(CA_ELIGIBLE_GROSSUP - 1)} (x{CA_ELIGIBLE_GROSSUP:g}); "
            f"the dividend tax credit is a share of the grossed-up "
            f"amount: federal {_pct(CA_FED_DTC_ELIGIBLE)}, "
            + ", ".join(prov) + ".")


def _on_health_premium_text() -> str:
    from taxjson.lib.tax_estimate import ON_HEALTH_PREMIUM
    parts = [f"over {_usd(floor)}: {_usd(at)} plus {_pct(rate)} of the "
             f"excess, at most {_usd(cap)}"
             for floor, at, rate, cap in ON_HEALTH_PREMIUM]
    return (f"The Ontario Health Premium on taxable income (not indexed): "
            f"nil to {_usd(ON_HEALTH_PREMIUM[0][0])}; "
            + "; ".join(parts) + ".")


def _ca_prescribed_rates_text() -> str:
    from taxjson.bin.taxjson_instalments import (PUBLISHED_FROM,
                                                 PUBLISHED_RATES,
                                                 PUBLISHED_THROUGH)
    segs: List[str] = []
    last = None
    for eff, r in PUBLISHED_RATES:
        if r != last:
            segs.append(f"{_pct(r)} from {eff}")
            last = r
    return (f"Without [instalments] prescribed_rate(s), the interest rate "
            f"is CRA's published rate on overdue taxes by quarter: "
            + ", ".join(segs) + f". Days after {PUBLISHED_THROUGH} assume "
            f"the last rate ({_pct(PUBLISHED_RATES[-1][1])}) and days "
            f"before {PUBLISHED_FROM} the first; the report says when it "
            f"assumed one.")


def _ca_balance_due_text() -> str:
    from taxjson.bin.taxjson_instalments import balance_due_label
    return (f"Instalment interest runs to the balance-due day, "
            f"{balance_due_label()} of the next year (or today, if "
            f"earlier); a payment dated after it is not an instalment of "
            f"the year.")


def _ca_estimate_tables() -> List[Rule]:
    """The Canadian estimate's per-year tables (after CA-EST-VINTAGE)."""
    out = [Rule("CA-EST-FED-TABLE", _ca_fed_table()),
           Rule("CA-EST-DTC", _ca_dtc_text())]
    for code in _ca_provinces():
        out.append(Rule(f"CA-EST-{code}-TABLE", _ca_prov_table(code)))
    out.append(Rule("CA-EST-OHP", _on_health_premium_text()))
    return out


def _ca_instalment_tables() -> List[Rule]:
    return [Rule("CA-INST-RATES", _ca_prescribed_rates_text(), cont=True),
            Rule("CA-INST-DUE", _ca_balance_due_text(), cont=True)]


def _us_estimate_tables() -> List[Rule]:
    """The US estimate's per-year tables and NIIT (after
    US-EST-VINTAGE)."""
    from taxjson.lib.tax_estimate import (US_NIIT_MAGI_THRESHOLD,
                                          US_NIIT_RATE)
    parts = [f"{y}: standard deduction {_usd(t['US_STD_DEDUCTION'])}; "
             f"ordinary income {_bands(t['US_ORD_BRACKETS'])}; long-term "
             f"gains and qualified dividends "
             f"{_bands(t['US_LTCG_BRACKETS'])}"
             for y, t in _vintages()]
    return [
        Rule("US-EST-TABLE",
             "Federal tax by year (single filer; taxable income bands): "
             + ". ".join(parts) + "."),
        Rule("US-EST-NIIT",
             f"NIIT is {_pct(US_NIIT_RATE)} of the lesser of net "
             f"investment income and modified AGI over "
             f"{_usd(US_NIIT_MAGI_THRESHOLD)} (single filer, IRC §1411).",
             cont=True),
    ]
