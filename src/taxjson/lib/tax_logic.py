"""`taxjson tax-logic`: a short, plain statement of every rule taxjson
applies for one country, with the project's own settings filled in.

Each rule is one line. Where a setting changes the rule, the line states
the value in force and names the key, so the text always matches what the
engine will do for THIS project. Keep it short: this is the summary, the
README and REFERENCES.md carry the detail.
"""
from __future__ import annotations

import textwrap
from typing import Any, Dict, List, Tuple

Section = Tuple[str, List[str]]


def _canada(s: Dict[str, Any]) -> List[Section]:
    basis = s.get("tax_date") or "settle"
    timing = str(s.get("option_premium_timing") or "grant").lower()
    since = s.get("option_grant_timing_since")
    year = s.get("year")
    fut = s.get("futures_settle") or "trade"
    froc = s.get("foreign_return_of_capital") or "dividend"
    buyback = bool(s.get("option_buyback_loss_superficial"))

    if basis == "settle":
        year_rule = ("A trade belongs to the year it SETTLES (tax_date = "
                     "\"settle\", CRA practice): a sale on Dec 31 that "
                     "settles Jan 2 is next year's disposition.")
    else:
        year_rule = ("A trade belongs to the year it is TRADED (tax_date = "
                     "\"trade\"; CRA practice is the settlement date).")
    if timing == "grant":
        start = since or year
        prem = [
            "Writing an option: the premium is a capital gain in the year "
            "it is written (ITA s.49(1); option_premium_timing = \"grant\")"
            + (f", for contracts written from {start}" if start else "")
            + (" (option_grant_timing_since" if since else
               " (defaults to the project year; set "
               "option_grant_timing_since once") + "). Earlier contracts "
            "keep close timing.",
            "Buying it back: the cost is a capital loss in the buy-back "
            "year. Expiry adds nothing. A bought option's cost is a loss "
            "on its expiry date.",
        ]
    else:
        prem = [
            "Writing an option: nothing is taxed until it closes "
            "(option_premium_timing = \"close\").",
            "Buying it back or expiry: the premium minus the cost is a "
            "gain or loss on that date. A bought option's cost is a loss "
            "on its expiry date.",
        ]
    return [
        ("Tax year and dates", [
            year_rule,
            "Settle dates come from the broker when printed. Otherwise: "
            "T+1 (from 2024-05-27 in CAD, 2024-05-28 in USD), T+2 from "
            "2017-09-05, T+3 before; options T+1. Days skip weekends and "
            "settlement holidays (US: NYSE and Federal Reserve holidays; "
            "Canada: TSX holidays, Remembrance Day, Truth and "
            "Reconciliation). The generic importer and crypto settle on "
            "the trade date; an option expiry is dated its expiry day.",
            ("Futures and futures options settle on the TRADE date "
             "(futures_settle = \"trade\": variation margin settles the "
             "P/L daily)." if fut == "trade" else
             "Futures and futures options settle on the next settlement "
             "day (futures_settle = \"next_day\")."),
            "Dividends, interest and other income belong to the year they "
            "are PAID, not the record or ex-dividend date.",
            "Crypto is dated in local time.",
        ]),
        ("Currency", [
            "Every amount is converted to CAD at the Bank of Canada daily "
            "rate for its date (the settle date for trades); a day with "
            "no rate uses a recent previous day, and a longer gap stops "
            "the run. Yahoo is used only before 2017 or for a currency "
            "the Bank does not publish.",
            "Gains on holding foreign cash (s.39(1.1)) are NOT in the "
            "Schedule 3 totals: `taxjson fx-cash` estimates the net gain "
            "beyond the $200 annual exemption.",
        ]),
        ("Cost base (ACB)", [
            "Average cost (s.47): one pool per identical property across "
            "all your taxable accounts. Purchase commissions add to the "
            "ACB; sale commissions are outlays. The single pool needs a "
            "full `taxjson run` (not `--account`, and no elections "
            "pending).",
            "Identical property is the same symbol with its currency "
            "suffix (.TO, .US, .V). Two listings are one security only "
            "when ticker.map joins them. Renames and splits carry the "
            "pool forward.",
            "Accounts typed \"sheltered\" (RRSP, TFSA, FHSA, LIRA, "
            "RESP...) are tracked but kept out of the filing totals. For "
            "the superficial-loss rule they count as affiliated holders.",
            "Return of capital lowers the ACB. Received with no shares "
            "held, or beyond the ACB, it is a capital gain and the ACB is "
            "nil (s.40(3)).",
            ("For IB only, a foreign issuer's return of capital (by ISIN) "
             "is a dividend (s.90(2); foreign_return_of_capital = "
             "\"dividend\"). Other brokers always lower the ACB."
             if froc == "dividend" else
             "Return of capital lowers the ACB for every issuer "
             "(foreign_return_of_capital = \"acb\")."),
            "A transfer into a taxable account stops the run until the "
            "original purchase is declared (.tt ACQUIRED line). Shares "
            "with missing buy history go in phantoms.json: sales that "
            "draw on them are listed for manual reporting and left out "
            "of the totals, with no superficial-loss test, until the "
            "position is fully sold.",
        ]),
        ("Dispositions (Schedule 3)", [
            "Gain = proceeds - ACB - outlays. taxjson reports full gains; "
            "half the net gain is taxable, which you apply on Schedule 3 "
            "(line 12700).",
            "Line 4 (13199/13200): shares, fund units and other "
            "securities. Line 6 (15199/15300): options and futures. Line "
            "7 (15200/15301): accounts marked crypto, from 2025; on "
            "15199/15300 before.",
            "A short sale's gain or loss is realized when it is covered. "
            "Cash-settled options (no stock leg) realize their gain or "
            "loss on the option itself.",
        ]),
        ("Superficial loss (s.54)", [
            "A loss is denied when identical property is acquired within "
            "30 days before or after the sale (settle dates) and is still "
            "held at the end of day 30, in your taxable or sheltered "
            "accounts. (A spouse's trades count only through "
            "`taxjson-gains --affiliated`.)",
            "A long call on the shares is identical property to them (a "
            "right to acquire, s.54 para (i)), at 100 shares per "
            "contract. Shares never replace an option; an option is "
            "replaced only by the identical contract; a put never "
            "replaces the shares.",
            "Only purchases count: writing an option or shorting again "
            "never replaces, including after a loss on covering a short.",
            "Only units acquired in the window and still held count, per "
            "holder. The denied part is loss x (those units / units "
            "sold), capped at the whole loss.",
            "The denied amount is added to the replacement's ACB and "
            "comes back when it is sold. If the replacement is in a "
            "sheltered account, that part is lost for good.",
            ("A loss on buying back a written option is superficial when "
             "the same option is bought, and still held at day 30, "
             "within the window (option_buyback_loss_superficial = true)."
             if buyback else
             "A loss on buying back a grant-timed written option is "
             "exempt from the rule (option_buyback_loss_superficial = "
             "false); close-timed contracts still follow it."),
            "Crypto follows the same rule, pooled across exchanges when "
            "two or more crypto accounts are configured.",
        ]),
        ("Options (s.49)", prem + [
            "Exercise or assignment: the premium folds into the shares' "
            "cost or proceeds (s.49(3), (4)). If the premium's year was "
            "already filed, `taxjson option-boundary` flags the T1-ADJ.",
        ]),
        ("Corporate actions (elections in the account manifest)", [
            "Splits and consolidations scale the quantity; the ACB is "
            "unchanged. Name changes carry the pool automatically.",
            "Mergers: taxable_disposition (old shares sold at FMV; new "
            "shares cost FMV) or rollover_s_85_1_5 (share-for-share; "
            "cost carries over). Cash for fractional shares is handled; "
            "other cash in a merger is not modelled.",
            "Spin-offs: rollover_s_86_1 (ACB split between the two; file "
            "the s.86.1 election) or taxable_deemed_dividend (a dividend "
            "at FMV, which is also the new shares' cost).",
            "ignore skips broker noise only; on a real event it leaves "
            "the books wrong.",
        ]),
        ("Income", [
            "Dividends on Canadian listings (.TO, .V, .CN, .NE) count as "
            "Canadian, all others as foreign. Take the return's line "
            "numbers from your slips.",
            "Payments in lieu of dividends are ordinary income.",
            "Crypto staking rewards are income at fair value when "
            "received; that value is the coins' cost.",
        ]),
        ("Crypto", [
            "Each coin is its own property. A coin-for-coin trade is a "
            "sale of one and a purchase of the other at fair value.",
            "USD stablecoins (USDC, USDT, DAI; also PYUSD and GUSD on "
            "Coinbase) are treated as US-dollar cash, an approximation.",
            "A Kraken withdrawal fee paid in a coin is a sale of that "
            "coin. A trade fee taken in a coin reduces the coins bought "
            "or adds to the coins sold.",
            "Moving coins between your own wallets is not a sale. A gift "
            "or a payment in crypto is a sale at fair value: declare it "
            "in a .tt file.",
        ]),
        ("Reports", [
            "`taxjson t1135`: Form T1135 is required when the total cost "
            "of specified foreign property in taxable accounts exceeds "
            "$100,000 at any time in the year. Country comes from the "
            "listing suffix (t1135.map overrides); crypto held on an "
            "exchange counts.",
            "`taxjson estimate`: federal and provincial tax (ON, BC, AB) "
            "with AMT on top of your other income, for planning only. "
            "Canadian dividends are treated as eligible (38% gross-up "
            "and credit), foreign dividends as ordinary income with "
            "withholding credited up to 15%; interest is left out.",
            "`taxjson edge-cases`: every trade whose year or superficial-"
            "loss verdict turns on a boundary.",
            "`taxjson close-year` records each closed year's sales, "
            "year-end positions and cost, and trades settling in January; "
            "`taxjson handoff` checks the next year starts from exactly "
            "that, so no sale is reported twice or never.",
        ]),
    ]


def _usa(s: Dict[str, Any]) -> List[Section]:
    basis = s.get("tax_date") or "trade"
    return [
        ("Tax year and dates", [
            ("A trade belongs to the year it is TRADED (tax_date = "
             "\"trade\", IRS)." if basis == "trade" else
             "A trade belongs to the year it SETTLES (tax_date = "
             "\"settle\"; the IRS uses the trade date)."),
            "Income belongs to the year it is paid.",
        ]),
        ("Currency", [
            "Amounts are in USD. Other currencies are converted at the "
            "Yahoo Finance daily rate for the settle date; a day with no "
            "rate uses a recent previous day.",
        ]),
        ("Basis and holding period", [
            "First in, first out per account (specific-lot "
            "identification is not supported). Purchase commissions add "
            "to basis; sale commissions reduce proceeds.",
            "Long-term when held more than one year (Rev. Rul. 66-7 for "
            "month-end purchases); otherwise short-term. A stand-alone "
            "short sale is short-term.",
        ]),
        ("Wash sales (§1091)", [
            "A loss is disallowed, share for share, when the same "
            "security (or the identical option) is bought within 30 days "
            "before or after the sale (trade dates), in any of your "
            "accounts, IRAs included. Re-shorting after a short-cover "
            "loss also counts. There is no still-held test, and "
            "look-alike securities are not detected.",
            "Matching across accounts needs a full `taxjson run` (not "
            "`--account`).",
            "The disallowed loss is added to the replacement lot's basis "
            "and its holding period carries over. A replacement bought in "
            "an IRA makes it permanent.",
            "A long call bought in the window is flagged as a warning "
            "only (\"option to acquire\" is not enforced by the US "
            "engine).",
            "Crypto is not subject to the wash-sale rule.",
        ]),
        ("Options", [
            "Premiums are taxed when the position closes (§1234). "
            "Exercise or assignment folds the premium into the stock's "
            "basis or proceeds. Cash-settled options realize on the "
            "option.",
            "Not modelled: §1256 60/40 contracts, §1233 and §1259.",
        ]),
        ("Reports", [
            "`taxjson form-export --form 8949`: Form 8949 rows (Part I "
            "short-term, Part II long-term; wash sales as code W). "
            "`--form txf` writes a TurboTax TXF file.",
            "`taxjson estimate`: federal tax only (single filer, standard "
            "deduction, NIIT), for planning.",
        ]),
    ]


def sections(country: str, settings: Dict[str, Any]) -> List[Section]:
    c = (country or "canada").strip().lower()
    return _usa(settings) if c in ("usa", "us") else _canada(settings)


def render(country: str, settings: Dict[str, Any], width: int = 88) -> str:
    c = (country or "canada").strip().lower()
    name = "United States (experimental)" if c in ("usa", "us") else "Canada"
    year = settings.get("year")
    out = [f"TAX LOGIC — {name}" + (f", tax year {year}" if year else "")
           + ", with this project's settings", ""]
    for title, rules in sections(country, settings):
        out.append(title.upper())
        for r in rules:
            out.append(textwrap.fill(r, width=width, initial_indent="  - ",
                                     subsequent_indent="    "))
        out.append("")
    out.append("Detail and sources: README.md and REFERENCES.md. taxjson "
               "computes; it does not give tax advice.")
    return "\n".join(out)
