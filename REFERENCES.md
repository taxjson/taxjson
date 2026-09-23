# References

The statutory and administrative sources behind each rule taxjson applies, and the ones behind what it deliberately does **not** do. Section numbers are the *Income Tax Act* (Canada) unless marked IRC (US). Cite these when reporting a bug in tax math: "the engine does X, but T4037 says Y" is the most useful issue this project can receive.

## Canada — capital gains engine

| Rule | What taxjson does | Source | Code |
| --- | --- | --- | --- |
| Capital gain / loss = proceeds − ACB − outlays | Commissions and folded transaction levies (e.g. UK stamp duty) are part of cost on a buy and reduce proceeds on a sell | s.40(1); Guide **T4037** *Capital Gains*, "Calculating your capital gain or loss" | `lib/core.py` (Canada engine) |
| Identical properties: average cost | One ACB pool per identical property across all of the taxpayer's accounts of the same kind; cross-listings of the same share are identical, CDRs are not | s.47(1); **IT-387R2** *Meaning of Identical Properties*; T4037 "Identical properties" | `ticker.map` TOBASE/DISTINCT, `lib/corporate_timeline.py` |
| Disposition date = settlement date | Canada books trades on the settlement date (`tax_date = "settle"`), including the year-end boundary | T4037, "When do you have a capital gain or loss?" (securities are disposed of on the settlement date) | `lib/dates.py`, `event_sort_key` |
| Foreign-currency conversion | Each transaction converted at the Bank of Canada rate for its date; no annual average | Folio **S5-F4-C1** *Income Tax Reporting Currency*; T4037 "Foreign currencies" | `bin/to_base_curr.py` |
| Superficial loss | A loss is denied to the extent the taxpayer **or an affiliated person** acquires identical property in the 61-day window and still holds it at the window's end; the denied amount is added to the ACB of the substituted property. A new short sale or written option is not an acquisition, so it never triggers (the US §1091(e) re-short rule does not apply); a long purchase after a cover loss does | s.54 "superficial loss"; s.40(2)(g)(i); s.53(1)(f); T4037 "Superficial loss"; **s.251.1(1)(g)** (an RRSP/TFSA/RESP trust is affiliated with its annuitant/holder) | `lib/core.py` wash pass, `bin/taxjson_wash_radar.py` |
| Loss denied permanently when the plan holds the property | When the substituted property sits in a registered plan the ACB bump is worthless, so the loss is reported as permanently denied rather than deferred | s.53(1)(f) applied to an exempt trust; CRA's position on RRSP/TFSA repurchases (T4037 example) | `permanently_disallowed` in the wash record |
| Transfers **to** a registered plan at a loss | Loss deemed nil (not merely deferred) | s.40(2)(g)(iv) | KNOWN_ISSUES — declare such moves explicitly |
| Stock splits, consolidations | ACB per share rescaled; trades executed pre-split but settling post-split are re-denominated | s.47; T4037 "Stock splits" | `_redenoms`, `lineage_factor` |
| Return of capital | Reduces the ACB of the units; a distribution that drives the ACB below zero is booked as a deemed gain in that year (qty-0 record) and the ACB resets to nil | s.53(2)(h)(i.1); s.40(3); T4037 "Adjusted cost base — return of capital" | `distributions.map`, `bin/taxjson_apply_distributions.py`; s.40(3) in `lib/core.py` ADJUST branch |
| Reinvested / phantom distributions | Non-cash distributions reported on a T3 increase ACB | s.53(1)(h)-style trust allocations; T4037 "Mutual fund units and reinvested distributions" | `distributions.map` |
| Options | Writing an option is a disposition: the premium is a capital gain in the year WRITTEN (`option_premium_timing = "grant"`, the Canada default); a buy-back is a loss in its own year; expiry adds nothing. If a written put is exercised the premium reduces the cost of the shares acquired; if a written call is exercised it is added to the proceeds; a holder's exercised put reduces the proceeds of the shares delivered; the grant year is amended (s.49(4) — `taxjson option-boundary` says when). `"close"` nets at the closing transaction instead | s.49(1)–(4); **IT-479R** *Transactions in Securities*, paras 21–31 | grant records + `ASSIGN` handling in `lib/core.py`; `lib/option_boundary.py` |
| Foreign spin-offs | The s.86.1 election treats an eligible foreign spin-off as a tax-free distribution with a cost allocation between old and new shares | s.86.1; T4037 "Eligible distributions" | `taxjson elect`, `lib/corp_actions.py` |
| Mergers, exchanges, tenders | Share-for-share exchanges may roll over under s.85.1 or s.86; cash tenders are dispositions | s.85.1, s.86, s.87; T4037 "Shares" | elections in `manifest.json`; `taxjson elect` |
| Inclusion rate | 50% of net gains (the proposed 2024 two-thirds rate was cancelled in 2025) | s.38(a); Schedule 3, line 19900 | `lib/tax_estimate.py` vintages |
| Net capital losses | Carried back three years (T1A) or forward indefinitely against gains only | s.111(1)(b); T4037 "Applying net capital losses" | `taxjson carryover`, `bin/taxjson_filed.py` |
| Schedule 3 / T5008 | Publicly traded shares go on Schedule 3 line 13200 (proceeds 13199); the T5008 the broker files is a slip, not the return — the taxpayer's ACB governs | Schedule 3; Guide **T4091** *T5008 Guide*; T4037 | form export, `taxjson reconcile-slips` |
| Specified foreign property | T1135 required when total **cost** exceeds C$100,000 at any time in the year; US-listed shares in a Canadian account count, registered plans do not | s.233.3; Form **T1135** and its guide | `taxjson t1135` |
| Cryptocurrency | Property, not currency; each disposal (sale, swap, spend, gift) is a disposition; identical-property and superficial-loss rules apply per coin | CRA *Guide for cryptocurrency users and tax professionals*; s.47 | crypto account path, `bin/fill_crypto_prices.py` |
| Crypto gifts and sends | A gift is a disposition at fair market value | s.69(1)(b); the CRA crypto guide | evidence sidecar; see KNOWN_ISSUES (errata) |

## Canada — income and the tax estimate

| Rule | What taxjson does | Source | Code |
| --- | --- | --- | --- |
| Eligible dividends | 38% gross-up, federal credit 15.0198% of the grossed-up amount, provincial credit by province | s.82(1)(b)(ii), s.121(b); provincial acts; T5 box 24/25/26 | `lib/tax_estimate.py` (`dtc_eligible`) |
| Non-eligible dividends | **Not modelled** — every Canadian dividend is treated as eligible (15% gross-up / 9.0301% credit not applied) | s.82(1)(b)(i), s.121(a); T5 box 10/11/12 | KNOWN_ISSUES |
| Foreign dividends | Ordinary income; the 15% treaty withholding is taken as the foreign tax credit | s.126(1); Form **T2209**; Canada–US treaty Art. X | `lib/tax_estimate.py` |
| Withholding above the treaty rate | **Not modelled** (the excess is deductible under s.20(11)/(12), not creditable) | s.20(11), s.20(12) | — |
| Alternative minimum tax | 2024+ regime: 20.5% rate, basic exemption indexed, 100% of capital gains included, 50% of most credits allowed | s.127.5–127.55 (as amended 2024) | `CA_AMT_*` constants |
| Instalments and interest | Current-year / prior-year / CRA-reminder methods; interest at the prescribed rate on the shortfall, offset rules | s.156(1), s.161(2), s.161(4.01) | `taxjson instalments` |
| Federal and provincial brackets, BPA, surtaxes | One vintage per year, indexed | s.117, s.118(1.1) and provincial acts; CRA "Canadian income tax rates for individuals — current and previous years" | `_VINTAGES` in `lib/tax_estimate.py` |
| Capital vs income character | Assumed capital for all securities, **including short sales** — CRA's stated position (IT-479R para 18) is that short-sale gains and losses are on income account unless the s.39(4) election (Form **T123**) is in force; the tool does not take that position, and short records carry `direction = SHORT` so they can be moved. The "adventure in the nature of trade" question is the user's | s.39(4); IT-479R paras 9–20 | README (scope) |
| FX gains on cash balances | `taxjson fx-cash`: foreign cash is property; the $200 de minimis applies symmetrically to an individual's net FX gain/loss for the year | s.39(1.1) (individuals, since 2016; formerly s.39(2)); **IT-95R** *Foreign Exchange Gains and Losses* | `bin/taxjson_fx_cash.py` |

## United States

| Rule | What taxjson does | Source | Code |
| --- | --- | --- | --- |
| Wash sales | Loss disallowed when substantially identical stock is bought within 30 days before or after; basis of the replacement is increased; holding period tacks | **IRC §1091**; Treas. Reg. **§1.1091-1**; §1223(3); **Publication 550** "Wash Sales" | US engine in `lib/core.py` |
| IRA repurchases | Loss permanently disallowed, no basis adjustment | **Rev. Rul. 2008-5** | US engine |
| Holding period | Long-term after more than one year; an asset bought on the last day of a month is long-term from the first day of the corresponding month a year later | §1222; **Rev. Rul. 66-7** | `held_more_than_one_year` |
| Lot identification | FIFO only (specific identification is not modelled) | Reg. §1.1012-1(c) | README (scope) |
| Options | Premiums and assignments per Pub 550 "Options" | §1234; Pub 550 | `ASSIGN` handling |
| Qualified dividends, NIIT | Estimate stacks qualified dividends with long-term gains; 3.8% NIIT over the MAGI threshold | §1(h)(11); §1411 | `lib/tax_estimate.py` |
| Forms | Form 8949 / Schedule D codes and adjustment columns | Form 8949 instructions | form export |

## How to use this file

- A rule with a source is a **claim** the engine makes; tests pin each one. If the source and the engine disagree, the source wins — file an issue quoting it.
- A rule marked **not modelled** is scope, not a bug; the README and KNOWN_ISSUES say what to do by hand.
- Rate tables carry their year in `_VINTAGES`; the printed estimate names the vintage it used. Adding a year means adding a block from CRA's published tables, not editing an old one.
- Interpretation Bulletins (IT-479R, IT-387R2, IT-95R) are archived by CRA but remain its stated positions until a Folio replaces them.
