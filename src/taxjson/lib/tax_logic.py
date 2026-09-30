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
    "CA-ACB-08",       # s.90(2) foreign ROC as a dividend (IB)
    "CA-OPT-01",       # s.49(1) grant timing
    "CA-RPT-01",       # T1135
    "CA-FX-07",        # fx-cash s.39(1.1) $200 exemption
    "CA-FX-04",        # futures P/L on average cost
    "CA-CRYPTO-02",    # stablecoins as US-dollar cash
    "CA-DATE-01",      # settle-date tax year by default
    "CA-DATE-04",      # computed T+1 settlement default
    "CA-CTRY-02",      # US-only settings/commands/flags refused
    "CA-CTRY-03",      # base currency CAD
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
})


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
                 "Settle dates come from the broker when printed."),
            Rule("CA-DATE-04",
                 "Otherwise: T+1 (from 2024-05-27 in CAD, 2024-05-28 in "
                 "USD), T+2 from 2017-09-05, T+3 before; options T+1.",
                 cont=True),
            Rule("CA-DATE-05",
                 "Days skip weekends and settlement holidays (US: NYSE and "
                 "Federal Reserve holidays; Canada: TSX holidays, "
                 "Remembrance Day, Truth and Reconciliation).", cont=True),
            Rule("CA-DATE-06",
                 "The generic importer uses a mapped settle column, else "
                 "this cycle (settle_on_trade_date = true keeps the trade "
                 "date).", cont=True),
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
            Rule("CA-DATE-11",
                 "Interest and other income belong to the year they are "
                 "PAID."),
            Rule("CA-INC-DATE-DIV",
                 "Dividends from corporations, Canadian and foreign, "
                 "belong to the year they are PAID, not the record or "
                 "ex-dividend date."),
            Rule("CA-INC-DATE-PIL",
                 "A payment in lieu of a dividend belongs to the year it "
                 "is paid."),
            Rule("CA-INC-DATE-ROC",
                 "A return of capital lowers the ACB on the date it is "
                 "paid."),
            Rule("CA-INC-DATE-ROC-TRUST",
                 "A Canadian trust's return of capital (T3 box 42) lowers "
                 "the ACB, by law, when it becomes payable; taxjson uses "
                 "the pay date, so one payable in December and paid in "
                 "January lowers the ACB a year late (check a sale made "
                 "between the two dates by hand).", cont=True),
            Rule("CA-INC-DATE-TRUST",
                 "A trust's income distribution is dated by its pay date. "
                 "By law (s.104(13)) it belongs to the year it becomes "
                 "payable: one payable in December and paid in January is "
                 "not moved back yet, so take its year from the T3 slip."),
            Rule("CA-DATE-12", "Crypto is dated in local time."),
        ]),
        ("Currency", [
            Rule("CA-FX-01",
                 "Every amount is converted to CAD at the Bank of Canada "
                 "daily rate for its date (the settle date for trades);",
                 keys=("base_currency",)),
            Rule("CA-FX-02",
                 "a day with no rate uses a recent previous day, and a "
                 "longer gap stops the run.", cont=True),
            Rule("CA-FX-03",
                 "Yahoo is used only before 2017 or for a currency the "
                 "Bank does not publish.", cont=True),
            Rule("CA-FX-04",
                 "A futures contract is booked on its settled P/L: nothing "
                 "is paid to open one, so its notional is never converted. "
                 "Each close's P/L (commissions included, average cost of "
                 "the open contracts) is converted at that closing leg's "
                 "rate;"),
            Rule("CA-FX-05",
                 "Schedule 3 shows a gain as proceeds and a loss as ACB.",
                 cont=True),
            Rule("CA-FX-06", "Options on futures are ordinary options.",
                 cont=True),
            Rule("CA-FX-07",
                 "Gains on holding foreign cash (s.39(1.1)) are NOT in the "
                 "Schedule 3 totals: `taxjson fx-cash` estimates the net "
                 "gain beyond the $200 annual exemption.",
                 keys=("fx_cash_gains",)),
        ]),
        ("Cost base (ACB)", [
            Rule("CA-ACB-01",
                 "Average cost (s.47): one pool per identical property "
                 "across all your taxable accounts."),
            Rule("CA-ACB-02",
                 "Purchase commissions add to the ACB; sale commissions "
                 "are outlays.", cont=True),
            Rule("CA-ACB-03",
                 "The single pool needs a full `taxjson run` (not "
                 "`--account`, and no elections pending).", cont=True),
            Rule("CA-ACB-04",
                 "Identical property is the same symbol with its currency "
                 "suffix (.TO, .US, .V). Two listings are one security "
                 "only when ticker.map joins them. Renames and splits "
                 "carry the pool forward."),
            Rule("CA-ACB-05",
                 "Accounts typed \"sheltered\" (RRSP, TFSA, FHSA, LIRA, "
                 "RESP...) are tracked but kept out of the filing totals. "
                 "For the superficial-loss rule they count as affiliated "
                 "holders."),
            Rule("CA-ACB-06", "Return of capital lowers the ACB."),
            Rule("CA-ACB-07",
                 "Received with no shares held, or beyond the ACB, it is a "
                 "capital gain and the ACB is nil (s.40(3)).", cont=True),
            (Rule("CA-ACB-08",
                  "For IB only, a foreign issuer's return of capital (by "
                  "ISIN) is a dividend (s.90(2); foreign_return_of_capital "
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
                 "(A spouse's trades count only through `taxjson-gains "
                 "--affiliated`.)", cont=True),
            Rule("CA-SL-05",
                 "A long call on the shares is identical property to them "
                 "(a right to acquire, s.54 para (i)), at 100 shares per "
                 "contract."),
            Rule("CA-SL-06",
                 "Shares never replace an option; an option is replaced "
                 "only by the identical contract; a put never replaces "
                 "the shares.", cont=True),
            Rule("CA-SL-07",
                 "Only purchases count: writing an option or shorting "
                 "again never replaces, including after a loss on covering "
                 "a short."),
            Rule("CA-SL-08",
                 "Only units acquired in the window and still held count, "
                 "per holder, and each one backs a single denial (a sale "
                 "split into fills, or two losses, share it). The denied "
                 "part is loss x (those units / units sold), capped at the "
                 "whole loss."),
            Rule("CA-SL-09",
                 "The denied amount is added to the replacement's ACB and "
                 "comes back when it is sold. If the replacement is in a "
                 "sheltered account, that part is lost for good."),
            Rule("CA-SL-10",
                 "Replacements are matched in acquisition order: purchases "
                 "after the sale first, then earlier ones, latest first. "
                 "Purchases at the same moment go to your taxable accounts "
                 "first, then sheltered, then affiliated."),
            (Rule("CA-SL-12",
                  "A loss on buying back a written option is superficial "
                  "when the same option is bought, and still held at day "
                  "30, within the window (option_buyback_loss_superficial "
                  "= true).", keys=("option_buyback_loss_superficial",))
             if buyback else
             Rule("CA-SL-11",
                  "A loss on buying back a written option is exempt from "
                  "the rule under either premium timing "
                  "(option_buyback_loss_superficial = false).",
                  keys=("option_buyback_loss_superficial",))),
            Rule("CA-SL-13",
                 "Crypto follows the same rule, pooled across exchanges "
                 "when two or more crypto accounts are configured."),
        ]),
        ("Options (s.49)", prem + [
            Rule("CA-OPT-06",
                 "Exercise or assignment: the premium folds into the "
                 "shares' cost or proceeds (s.49(3), (4))."),
            Rule("CA-OPT-07",
                 "If the premium's year was already filed, `taxjson "
                 "option-boundary` flags the T1-ADJ.", cont=True),
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
                 "Spin-offs: rollover_s_86_1 (ACB split between the two; "
                 "file the s.86.1 election)"),
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
                 "Payments in lieu of dividends are ordinary income."),
            Rule("CA-INC-04",
                 "Crypto staking rewards are income at fair value when "
                 "received; that value is the coins' cost."),
        ]),
        ("Crypto", [
            Rule("CA-CRYPTO-01",
                 "Each coin is its own property. A coin-for-coin trade is "
                 "a sale of one and a purchase of the other at fair "
                 "value."),
            Rule("CA-CRYPTO-02",
                 "USD stablecoins (USDC, USDT, DAI; also PYUSD and GUSD on "
                 "Coinbase) are treated as US-dollar cash, an "
                 "approximation."),
            Rule("CA-CRYPTO-03",
                 "A Kraken withdrawal fee paid in a coin is a sale of that "
                 "coin."),
            Rule("CA-CRYPTO-04",
                 "A trade fee taken in a coin reduces the coins bought or "
                 "adds to the coins sold.", cont=True),
            Rule("CA-CRYPTO-05",
                 "Moving coins between your own wallets is not a sale. A "
                 "gift or a payment in crypto is a sale at fair value."),
            Rule("CA-CRYPTO-06",
                 "A send that arrives on another of your exchanges within "
                 "3 days is treated as your own move;", cont=True),
            Rule("CA-CRYPTO-07",
                 "for every other send, `taxjson crypto-sends` records "
                 "whether it was your own wallet, a gift or a payment, and "
                 "writes a sale at fair value for each gift or payment to "
                 "crypto_sends.tt: the exchange's price when the row has "
                 "one, otherwise the Yahoo daily close times the Bank of "
                 "Canada rate of the send date.", cont=True),
            Rule("CA-CRYPTO-08",
                 "A gift or payment of a stablecoin is not written as a "
                 "sale (stablecoins are cash in the books). It is a "
                 "currency disposition: the value at the send date's Bank "
                 "of Canada rate minus the average cost of the US-dollar "
                 "and stablecoin pool. A loss is superficial when US "
                 "dollars or stablecoins were acquired within 30 days and "
                 "are still held."),
        ]),
        ("Reports", [
            Rule("CA-RPT-01",
                 "`taxjson t1135`: Form T1135 is required when the total "
                 "cost of specified foreign property in taxable accounts "
                 "exceeds $100,000 at any time in the year."),
            Rule("CA-RPT-02",
                 "Country comes from the listing suffix (t1135.map "
                 "overrides); crypto held on an exchange counts.",
                 cont=True),
            Rule("CA-RPT-03",
                 "`taxjson estimate`: federal and provincial tax (ON, BC, "
                 "AB) with AMT on top of your other income, for planning "
                 "only.", keys=("province",)),
            Rule("CA-RPT-04",
                 "Canadian dividends are treated as eligible (38% gross-up "
                 "and credit),", cont=True),
            Rule("CA-RPT-05",
                 "foreign dividends as ordinary income with withholding "
                 "credited up to 15%;", cont=True),
            Rule("CA-RPT-06", "interest is left out.", cont=True),
            Rule("CA-RPT-07",
                 "`taxjson edge-cases`: every trade whose year or "
                 "superficial-loss verdict turns on a boundary."),
            Rule("CA-RPT-08",
                 "`taxjson close-year` records each closed year's sales, "
                 "year-end positions and cost, and trades settling in "
                 "January; `taxjson handoff` checks the next year starts "
                 "from exactly that, so no sale is reported twice or "
                 "never."),
        ]),
        ("Project country", _ownership(c)),
    ]


def _usa(s: Dict[str, Any]) -> List[RuleSection]:
    c = _C.USA
    basis = _C.resolve_tax_date(c, s.get("tax_date"))
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
            Rule("US-DATE-03",
                 "Interest and other income belong to the year they are "
                 "paid."),
            Rule("US-INC-DATE-DIV",
                 "Dividends and payments in lieu belong to the year they "
                 "are paid, not the record or ex-dividend date."),
            Rule("US-INC-DATE-ROC",
                 "A return of capital (nondividend distribution) lowers "
                 "basis on the date it is paid."),
            Rule("US-INC-DATE-RIC",
                 "Not applied yet: a mutual-fund or REIT dividend declared "
                 "in October-December and paid in January belongs to the "
                 "declaration year (§852(b)(7), §857(b)(9)); taxjson dates "
                 "it by the pay date, so use the year on Form 1099-DIV."),
        ]),
        ("Currency", [
            Rule("US-FX-01", "Amounts are in USD.",
                 keys=("base_currency",)),
            Rule("US-FX-02",
                 "Other currencies are converted at the Yahoo Finance "
                 "daily rate for the settle date; a day with no rate uses "
                 "a recent previous day.", cont=True),
        ]),
        ("Basis and holding period", [
            Rule("US-BASIS-01", "First in, first out per account"),
            Rule("US-BASIS-02",
                 "(specific-lot identification is not supported).",
                 cont=True),
            Rule("US-BASIS-03",
                 "Purchase commissions add to basis; sale commissions "
                 "reduce proceeds.", cont=True),
            Rule("US-HOLD-01",
                 "Long-term when held more than one year, otherwise "
                 "short-term"),
            Rule("US-HOLD-02", "(Rev. Rul. 66-7 for month-end purchases).",
                 cont=True),
            Rule("US-HOLD-03", "A stand-alone short sale is short-term.",
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
                 "A replacement bought in an IRA makes it permanent.",
                 cont=True),
            Rule("US-WASH-12",
                 "A long call bought in the window is flagged as a warning "
                 "only (\"option to acquire\" is not enforced by the US "
                 "engine)."),
            Rule("US-WASH-13",
                 "Crypto is not subject to the wash-sale rule."),
        ]),
        ("Crypto sends", [
            Rule("US-SEND-01",
                 "Paying with crypto is a sale at fair value; `taxjson "
                 "crypto-sends` records it (`payment`) and writes the sale "
                 "to crypto_sends.tt."),
            Rule("US-SEND-02",
                 "A gift is not a sale for the donor, so `gift` is refused "
                 "in a US project: record it as `self`.", cont=True),
        ]),
        ("Options", [
            Rule("US-OPT-01",
                 "Premiums are taxed when the position closes (§1234)."),
            Rule("US-OPT-02",
                 "Exercise or assignment folds the premium into the "
                 "stock's basis or proceeds.", cont=True),
            Rule("US-OPT-03", "Cash-settled options realize on the option.",
                 cont=True),
            Rule("US-OPT-04",
                 "Not modelled: §1256 60/40 contracts, §1233 and §1259."),
        ]),
        ("Futures", [
            Rule("US-FUT-01",
                 "A futures contract is booked on its settled P/L: nothing "
                 "is paid to open one, so its notional is never converted "
                 "or reported. A close's P/L (commissions included) is "
                 "taken first in, first out from the open contracts, and "
                 "a non-USD contract's P/L is converted at the closing "
                 "leg's rate."),
            Rule("US-FUT-02",
                 "Not modelled: §1256 year-end marking to market and the "
                 "60/40 split; report them on Form 6781 by hand.",
                 cont=True),
        ]),
        ("Reports", [
            Rule("US-RPT-01",
                 "`taxjson form-export --form 8949`: Form 8949 rows (Part "
                 "I short-term, Part II long-term;"),
            Rule("US-RPT-02", "wash sales as code W).", cont=True),
            Rule("US-RPT-03",
                 "`--form txf` writes a TurboTax TXF file.", cont=True),
            Rule("US-RPT-04",
                 "`taxjson estimate`: federal tax only (single filer, "
                 "standard deduction, NIIT), for planning."),
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
