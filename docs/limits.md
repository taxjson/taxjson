# What taxjson does not do

taxjson turns broker exports into capital-gains and income figures you can check line by line. This page
says where it stops: what it does not do at all, how far each country is supported, and the limits that
are there by design, each with what to do instead. Bugs (behaviour that is meant to work and does not)
are in [KNOWN_ISSUES.md](../KNOWN_ISSUES.md); problems with a known fix are in
[docs/troubleshooting.md](troubleshooting.md).

## In one page

- **No tax advice.** taxjson computes and shows its work. It does not tell you how to file, and no output
  has been reviewed by a tax professional. Check it against your slips (T5008, T5, T3, 1099-B) and your
  accountant.
- **It does not prepare or file a return.** No T1 or Form 1040, no NETFILE or e-file. It gives you the
  figures for Schedule 3, Form 8949 / Schedule D (and a TurboTax TXF file), T1135 and the income lines;
  you enter them in your tax software.
- **Investment income only.** Employment, business, rental and pension income, credits and deductions
  are not computed. `tjs estimate`, `tjs amt` and `tjs instalments` are planning figures built on the
  books plus the few amounts you type in; they say so in their output.
- **Estimate scope.** Canada: federal plus Ontario, British Columbia or Alberta (other provinces, Quebec
  included, are refused). US: single filer, standard deduction, no state tax.
- **Runs locally; no web UI, no cloud.** Your files stay on your computer. `tjs run` reaches the network
  only for FX rates (Bank of Canada, Yahoo Finance) and missing crypto prices (Yahoo Finance) on a cache
  miss; the commands that quote today's prices (`tjs harvest`, `tjs tips --online`) ask Yahoo Finance
  too. `TAXJSON_OFFLINE=1` forbids all of it. Broker download (`tjs fetch`) is the separate taxjson-fetch
  plugin (Questrade and IBKR Flex). SECURITY.md lists every network call.
- **Only the listed brokers.** Interactive Brokers, Questrade, RBC Direct Investing, Webull, Kraken and
  Coinbase have parsers. Any other broker's CSV is read through a column mapping you write (the generic
  importer: shares, options and futures, not crypto; an exercise or assignment is booked by hand).
- **Your books only.** Trades of a spouse or a company you control are not read unless you add them as
  an account (see "Purchases by an affiliated person" below).
- **No specific-lot identification in the US.** The US engine sells lots first in, first out.
- **Some rules are not modelled** (US §1256 marking, 1040-ES, US AMT, non-eligible dividends in the
  estimate and others below). `tjs tax-logic` lists every rule it does apply.

## Status by country

- **Canada: supported.** It is used on real multi-account books (several brokers, registered and taxable
  accounts, options, crypto), and the books it builds are checked against the brokers' own positions
  exports with `tjs sanity`.
- **United States: experimental.** The rules (FIFO lots, wash sales under §1091, holding periods,
  Form 8949) are implemented and unit-tested, but have not been validated on a real account. Treat its
  output as a draft and check every figure against your 1099-B.
- **Pre-1.0.** The pipeline is stable, but command flags, file layouts and JSON field names can still
  change between releases. Breaking changes, and what to do about each, are listed in
  [docs/upgrading.md](upgrading.md).
## Limitations by design

Each entry says what taxjson does not do and what to do instead. These are choices, missing data in the
exports, or rules outside the scope; they are not bugs. `tjs tax-logic` states the rules that are applied.

### Broker exports

#### Webull exercise/assignment inference

Webull's Trading Summary has no exercise or assignment code: it shows a $0 option close and a separate
share trade at the strike. taxjson pairs the two (premium folded into the shares' cost or proceeds) only
when the share trade is on the same underlying, in the matching direction, for the contract's deliverable
size, at the strike, settles 1 day before to 7 days after the close, and carries the account's
exercise/assignment charge, `[accounts.<name>] exercise_fee` (for example 1.00). Without `exercise_fee`
nothing is paired; each candidate is named. A trade at the strike with an ordinary commission, or a
quantity that does not match the close one-to-one, is named as a warning, not paired. Exports beside the
file are searched (a Dec 31 assignment whose shares settle in January); exports of another Webull account
never are.
**Instead:** set `exercise_fee` to your broker's charge; book a pair it names but cannot match as `.tt`
ASSIGN lines.
**Code:** `src/taxjson/lib/brokerages/webull.py` — `_mark_assignments`, `_check_zero_closes`

#### Webull exports carry no income

The Trading Summary holds buys and sells only, so Webull dividends and interest are in no input. A DIV or
other row that does appear is a `Warning: UNBOOKED:` line (fatal under `tjs run --strict`).
**Instead:** enter the T5 amounts as `.tt` lines in the account's folder, such as
`INTEREST 2025-12-31 16:00:00 USD 12.34` (a slip with a blank box 27 is CAD) or a `DIVIDEND` line.

#### Kraken fiat conversions are not modeled

A Kraken fill whose traded coin is a fiat currency once stablecoins are folded (`USD/CAD`, `USDC/USD`,
`USDT/CAD`) and an instant trade between two fiat legs are counted as non-events
(`forex conversion … not modeled — KNOWN_ISSUES`); no fake `USD` or `CAD` position is booked. Coinbase
`Buy USDC` / `Sell USDC` rows are treated the same way, and the USDC leg of a `*-USDC` Advanced Trade is
cash. In a Canada project a USD stablecoin is US-dollar cash (a fill more than 2% off 1.00 USD prints a
de-peg warning), so the USD/CAD movement while you hold it is not booked as a gain of the coin. In a US
project every USD stablecoin is property: swaps, rewards and fees in it are sales and purchases of the
coin (tax-logic US-CRYPTO-02).
**Instead:** foreign-cash gains are `tjs fx-cash`'s job (see "Foreign cash" below); its opt-in ledger v2
reads Kraken's and Coinbase's fiat movements.
**Code:** `src/taxjson/lib/brokerages/kraken.py` — `_parse_trades`, `_build_instant_trade`, `kraken_cash_events`

#### IB `Trades / Forex` conversions are not in the position books

An Interactive Brokers currency conversion (asset category `Forex`, such as `USD.CAD`) is counted as a
non-event of the position books (`Trades/Forex (currency conversion, not modeled — KNOWN_ISSUES)`) and
kept for the Cash Report check only; no fake currency position is booked. The default foreign-cash
ledger does not read it, which is one reason its output says NOT RELIABLE.
**Instead:** the opt-in ledger v2 (`fx_cash_ledger = "v2"`) reads each conversion at its actual amounts,
with deposits, withdrawals and the Cash Report balances.
**Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_book_forex`, `ib_cash_events`

#### Kraken staking rewards in older exports carry no price

Kraken ledgers exported before 2026 have no price on staking rewards; newer ones carry `amountusd`, which
prices the reward. An unpriced reward is priced from Yahoo Finance's daily close by
`taxjson-fill-crypto`. A failed lookup is never silent: it is warned, listed as `UNPRICED`, a validation
error on the console and fatal under `tjs run --strict`; a failed price is not cached.
**Instead:** re-export the ledger, or re-run online.
**Code:** `src/taxjson/lib/brokerages/kraken.py` — `_build_staking_reward`; `src/taxjson/bin/fill_crypto_prices.py`

#### RBC foreign dividends are grossed up at 15%

An RBC dividend row marked `NON-RES TAX WITHHELD` carries only the net amount, so the gross is taken as
net / 0.85 (the US treaty rate) whatever the payer's country.
**Instead:** for a payer taxed at another rate, take the gross and the tax from the T5 or NR4 slip.
**Code:** `src/taxjson/lib/brokerages/rbc_direct.py` — `_build_dividend`

#### Questrade dividends with tax withheld are booked at the net amount

Questrade's export gives neither the gross nor the tax of a dividend marked NON-RES TAX WITHHELD, so it is
booked net: income understated, no foreign tax credit. In a taxable account the parse prints an ATTENTION
line listing those dividends. (Interest rows are booked as interest.)
**Instead:** take the gross and the withholding from the T5 or NR4 slip.
**Code:** `src/taxjson/lib/brokerages/questrade.py` — `TAX WITHHELD`

#### Questrade rows carry `commission`, the others `fee`

Questrade transactions record their charge as `commission`; every other parser uses `fee`. Gains are not
affected (the engine works from the net amount, which includes the charge) and the fee reports add both;
only a per-row report that shows one column lists the charge under a different header.
**Code:** `src/taxjson/bin/taxjson_fees.py`; `src/taxjson/lib/core.py` — `_effective_fee_for_trace`

#### RBC identity across years

The RBC parser learns identities (a symbol's listing, an option code's contract, a temporary
reorganization code's company) from all of an account's RBC exports in the project. With only this year's
export, and earlier years entered by hand in a `.tt` file, it cannot: income on a symbol no file trades
keeps the payment currency's listing (a USD return of capital is an ATTENTION line with the `TOBASE` line
that fixes it); an unknown temporary code is assumed to be the receipt's ticker (ATTENTION, with the
`ticker.map` line); an option RBC re-describes between years keeps this year's description, so the `.tt`
must use the same symbol (a close the books cannot back is an ATTENTION naming the contract and the
`GLOBAL` line that joins them). A ticker change RBC booked without a reorganization row is an ATTENTION
line giving the `.tt` line `RENAME <date> OLD NEW` (a note once that line is declared), and
only when an export in the project holds the old symbol's rows: when the old symbol's buys come in only
through the start `.tt`, the parser says nothing, and the sign is that the new symbol goes short while the old one
stays open in `tjs shares`.
**Instead:** keep the earlier years' RBC exports in `inputs/<account>/`, or add the suggested line.
**Code:** `src/taxjson/lib/brokerages/rbc_direct.py` — `build_rbc_account_context`

#### RBC year-end book-cost rows arrive the next spring

RBC posts year-end book-cost adjustments (a notional distribution, a year-end return of capital) dated
Dec 31 but only the following spring, so an export taken before then lacks them. The parse reads each
export's "Activity Export as of" date: when every export of the year was taken before the account's
posting day (`[accounts.<name>] year_end_posting`, default `"06-30"`) and a position was held at the year
end, the `.sum` says the adjustments may be missing; an export taken before Dec 31 is an ATTENTION, and
`tjs checklist` checks the latest RBC export against Jan 31.
**Instead:** export each RBC year with an end date after the following June, and keep the overlapping
files (overlapping downloads of one account are de-duplicated row by row).
**Code:** `src/taxjson/lib/brokerages/rbc_direct.py` — `rbc_coverage_messages`

#### RBC notional distributions raise the cost only

A "NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK COST" row raises the ACB; the distribution itself is income on
the fund's T3 (usually box 21) and is not in taxjson's income totals, as the parse warns. Booking it as
income needs its character, which only the slip gives.
**Instead:** take the income from the T3 (tax-logic CA-DIST-02 / US-DIST-02).
**Code:** `src/taxjson/lib/brokerages/rbc_direct.py` — `_build_book_adjust`

#### Transfers into a taxable account

In a taxable account the broker's transfer rows stay out of the books (in the transfer sidecar). Shares
that arrive from outside your books are booked as a purchase on the arrival date at the book value the
broker states on the row (Questrade "TRANSFER BOOK VALUE", RBC "BOOK VALUE"), with an ATTENTION line every
run; without a stated value (IB's transfer value is a market value) they stay out with no cost, said as
ATTENTION. That book value is the sending broker's record and may not be your ACB or basis. A US lot
booked this way starts its holding period on the arrival date, and the arrival is never treated as a
purchase for the superficial-loss / wash-sale window. An unrelated transfer out and transfer in of the
same security cancel each other.
**Instead:** a `.tt` purchase dated on or before the arrival, or an `OPENING … cost=unknown` line,
replaces the stated value (tax-logic CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV).
**Code:** `src/taxjson/lib/transfer_in.py` — `arrivals`, `attention_lines`

#### Positions reports: what is read

`tjs opening` and `tjs sanity` read an IB Activity Statement's Open Positions, an RBC Holdings Export
(by column label: no real export was available to pin its layout) and a holdings TOML. IB's Open
Positions Lot rows are not read, so a US opening from an IB statement has no lot dates and is skipped.
Questrade, Webull, Coinbase and Kraken have no positions export the parsers know.
**Instead:** list the lots in a holdings TOML (`acquired = "YYYY-MM-DD"`); for Questrade, the
taxjson-fetch plugin's `tjs fetch --positions` writes one.
**Code:** `src/taxjson/lib/positions_reports.py`

#### Export rows modelled without a real sample

A few row shapes were written from the broker's documentation or a made-up row, because no real export
holding them was available: IB's cash-in-lieu row (a Corporate Actions row containing `cash in lieu` with
a negative quantity: booked as a sale of the fraction for its Proceeds and folded into the nearest split
within a week, in this or another statement of the account), IB's `Stock Dividend` row, and a Questrade
reversal (a CIL, REI or stock dividend cancelled in a later export). If a broker words the row
differently, an IB row falls into the unhandled corporate actions (warned at the end of the parse) and an
unpaired Questrade reversal stops the parse; nothing is booked silently.
**Instead:** book the event in a `.tt` file, and send a redacted sample with a bug report so the shape can
be pinned.
**Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_IB_CIL_RE`; `src/taxjson/lib/brokerages/questrade.py` — `_pair_reversals`

#### Trade reversals across export files

An IB cancellation (`Ca`) pairs with its original in the same statement or in another statement of the
same account; overlapping statements of one account keep one book, and different IB accounts never touch.
With no original anywhere, it stays booked as a reversing trade and merge2 warns. A Questrade or RBC
reversal pairs with its original in any export of the same account; with none, the parse stops.
**Instead:** when the original is in no export (the export window starts after it), book the correction in
a `.tt` file.
**Code:** `src/taxjson/lib/trade_cancel.py`; `src/taxjson/lib/brokerages/ib_extractor.py` — `_reconcile_files`

#### Identical rows in two exports are booked once

Exports carry no row id. The same row in two exports of one account is booked once, also when the files
overlap too little to prove they are copies (they share only that row); `tjs run` then prints
`Warning: ATTENTION: dedup: …` naming both files and the row. Two separate identical trades of one account
can only end up split across files when an export is cut up by hand. Rows of different broker accounts are
never collapsed. Identical lines in two `.tt` files are both booked.
**Instead:** if the ATTENTION line names two real trades, enter the second as a `.tt` line.
**Code:** `src/taxjson/bin/taxjson_sort.py` — `plan_dedup`

#### Canadian listings carry no venue

One Canadian security has one symbol whichever broker reports it: `ROOT.TO`, for TSX, TSX Venture, CSE and
Cboe Canada alike (RBC and Webull exports do not name the venue). A `.tt` line is read the same way
(`ABC.V` on a CAD line becomes `ABC.TO`). In a Canada project a `TOBASE` or `DISTINCT` line that writes
`ROOT.V` also covers `ROOT.TO` (tax-logic CA-XLIST-06); a `GLOBAL` line that writes `ROOT.V` splits the
pool, and `taxjson-lint-crosslistings` flags it (CANADIAN VENUE SPLIT). A CSE ticker that duplicates a
different TSX ticker would share its pool.
**Instead:** a price lookup that needs the venue uses a ticker.map `QUOTE` line
(`QUOTE SAMPLY.TO SAMPLY.V`).
**Code:** `src/taxjson/lib/brokerages/base.py` — `canonical_ca_listing`

#### Settlement calendars outside North America skip weekends only

The settlement lag follows the listing's market (`.L`, `.AX` and the North American suffixes, else the row
currency): North America T+1 since May 2024 and T+2 from 2017-09-05; the UK, EU markets and Switzerland
T+2 from 2014-10-06 and T+1 from 2027-10-11; the ASX, NZX, Singapore and Tokyo T+2 from their own dates;
Hong Kong T+2. Any other market is T+3 before 2017-09-05 and T+2 after (no T+1 move modelled). Outside the
US and Canada only weekends are skipped, so a local bank holiday inside the lag can make a settle date a
day early. IB fills on Asian exchanges are dated in the exchange's local time.
**Instead:** where one day matters (a sale in the last days of the year, the edge of a 30-day window),
check the settle date against the broker's confirmation.
**Code:** `src/taxjson/lib/dates.py` — `market_of`; `src/taxjson/lib/market_calendar.py`

### Income and corporate actions

#### Stock dividends: $0 in Canada until the declared amount is added

Parsers book a stock dividend as a $0 purchase of the new shares. Canada: the shares enter the pool at $0
and count as an acquisition for the superficial-loss rule (tax-logic CA-STKDIV-01); the taxable amount is
the fund's declared amount, which no export carries, so the run prints an ATTENTION line (the run's year,
taxable accounts) until the cost is added. US: a pro-rata stock dividend is not income (§305(a)); the basis
is spread over old and new shares (§307) and the dates carry over (US-STKDIV-01). A taxable US stock
dividend (§305(b)) is not detected. An IB stock dividend paid in another security is not booked (an
`UNBOOKED` line asks for a hand entry).
**Instead:** Canada: add the declared per-share amount as a `[[distributions]]` entry in taxjson.toml (or
a `.tt` ADJUST); that raises the ACB only, and the dividend itself is reported from the T5/T3 slip.
**Code:** `src/taxjson/lib/pipeline.py` — `stock dividend of`

#### IB income rows carry no record date

In a Canada project a Canadian trust's distribution and return of capital are dated by the record date
Questrade and RBC print (s.104(13), s.53(2)(h); tax-logic CA-INC-DATE-TRUST). IB prints only the pay date
and labels a trust's distribution a dividend, so an IB distribution recorded in December and paid in
January stays in the pay year, and a January IB return of capital stays on its pay date (the run warns
and gives the two `.tt` ADJUST lines that move it to Dec 31). A Canadian issuer is recognised by its ISIN
or listing; split-share corporations come from a shipped list (`split_share_corporations` in
`src/taxjson/data/markets.toml`, changed with a ticker.map `SPLITSHARE` line), and a description naming a
split corp counts too.
**Instead:** compare the TAXABLE line of `tjs divs-sum` with the T3, which is authoritative; add any other
corporation that pays "distributions" to `[settings] corporate_distributions`.
**Code:** `src/taxjson/lib/income_dating.py` — `trust_record_date`, `roc_record_date`

#### Payments in lieu: what the exports cannot say

In a Canada project a payment in lieu on a Canadian issuer's share paid by a Canadian dealer is an
eligible dividend (s.260; tax-logic CA-INC-03). The dealer comes from the IB statement's BrokerName; an IB
file without that header leaves it unknown and the payment ordinary income. A payment in lieu on a unit
the books show to be a Canadian trust's (its payouts labelled distributions) stays ordinary income; a
trust whose payouts no export labels as distributions is counted as a dividend.
**Instead:** the dealer's T5 is authoritative; compare with `tjs divs-sum` and `tjs dil-sum`.
**Code:** `src/taxjson/lib/income_dating.py` — `pil_is_dividend`, `trust_units`

#### Foreign return of capital is only reclassified for IBKR

In a Canada project an IB "(Return of Capital)" from a non-Canadian ISIN is treated as a dividend (s.90(1))
unless `[settings] foreign_return_of_capital = "acb"`. Questrade and RBC rows carry no ISIN, and a `.US`
listing does not prove a foreign issuer, so their return of capital stays an ACB reduction; so does an IB
row without an ISIN, and every return of capital in a US project.
**Instead:** check a US issuer's return of capital on Questrade or RBC by hand against the slip.
**Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `Return of Capital`

#### US January fund and REIT dividends need a list

§852(b)(7) / §857(b)(9) put a fund or REIT dividend declared in October to December and paid in January
on Dec 31, but no export says which payer is a fund. A US project keeps the pay date, warns when a
January dividend has an October-December ex date (IB accruals) or record date (Questrade, RBC), and moves
the payments listed in `[settings] ric_january_dividends` to Dec 31 (tax-logic US-INC-DATE-RIC).
**Instead:** list each such payment; Form 1099-DIV is authoritative.
**Code:** `src/taxjson/lib/income_dating.py` — `ric_prior_year`

#### Spin-off default

By default a spin-off is booked as a dividend at fair market value; the estimate then classifies it by the
new company's listing. A Canadian parent's tax-deferred spin-off (a butterfly or s.86 reorganization) has
no election of its own.
**Instead:** elect `rollover_s_86_1` with the allocated ACB, which moves cost the same way.
**Code:** `src/taxjson/lib/corp_actions.py` — `CANADA_SPINOFF`

#### IB stock-plus-cash mergers are not booked

An IB merger that pays shares and cash (`WITH <id> 1 for 2 AND USD 5.00`), or any merger row the parser
does not recognise, becomes an `unsupported` corporate action: `tjs run` stops (exit 3) naming it. Cash
takeovers are booked as sales and share-for-share mergers go through the merger election.
**Instead:** record the exchange by hand in a `.tt` file and elect the event `ignore`.
**Code:** `src/taxjson/lib/corp_actions.py` — `_ib_unsupported_events`

#### A merger's per-account ratios are blended

When one merger shows a slightly different share ratio in each account, the ratios are folded into one
holdings-weighted ratio (with a NOTE). Totals and the shared ACB pool are right; each account's own
superficial-loss balance can be a fraction of a share off.
**Code:** `src/taxjson/lib/core.py` — `_fold_per_account_rename_ratios`

### Engine scope: Canada

#### Superficial-loss attribution between a taxable and a registered account

When a taxable account and an RRSP/TFSA both buy the property inside the window and both still hold it at
day 30, which one absorbs the denial follows a fixed order (purchases after the loss in date order, then
those before it latest first), so the same loss can be permanent (registered) or deferred (taxable).
s.53(1)(f) prescribes no allocation and CRA has published none.
**Instead:** `tjs wash-sales` shows the purchase chosen; keep a `.tt` note of the attribution you intend.
**Code:** `src/taxjson/lib/core.py` — `_holder_rank`

#### RESP accounts are treated as affiliated

Every `type = "sheltered"` account counts as you for the superficial-loss rule. CRA treats an RRSP or TFSA
as affiliated with its holder; whether an RESP subscriber is affiliated is not settled. taxjson takes the
conservative view: an RESP purchase held at the window's end denies the loss permanently.
**Instead:** if you take the other position, adjust by hand.

#### Purchases by an affiliated person

A loss is also superficial when your spouse or a corporation you control buys the property within 30
days (s.54; in the US §1091 reaches a spouse's purchase). `taxjson.toml` accounts are `taxable` or
`sheltered`, and `tjs run` never reads anyone else's trades, so such a loss is allowed and nothing warns.
**Instead:** add the affiliated person's account with `type = "sheltered"`: the loss is then denied
(permanently for you, as s.53(1)(f) puts the cost on their shares). It also shows in the sheltered tables
as if it were your plan; read it as theirs (tax-logic CA-SL-04).

#### A loss across two unjoined listings is flagged, not denied

Two listings of one security (a TSX line and its NYSE line) are one property when ticker.map, tobase.map
(Canada), a `.tt` JOURNAL line or a transfer journal joins them. Otherwise a loss on one listing, with the
other listing of the same root bought within 30 days under an equal name (or one differing only in
voting-share wording), is a run Warning, a `tjs ticker-map --suggest` line and a `tjs run --strict` stop,
but the loss stays allowed. Different roots, other name differences, known depositary-receipt pairs and
`.tt`-only books are not flagged. A name match is evidence, not proof, so it never joins on its own.
**Instead:** answer each Warning with the `TOBASE` or `DISTINCT` line it names; `tjs tips --online` groups
different-root listings by issuer name.
**Code:** `src/taxjson/lib/xlist_loss_radar.py` (tax-logic CA-XLIST-05 / US-XLIST-04)

#### `days_held` counts from trade dates

The days-held figure counts from trade dates while every other Canadian date is settlement-based. Canada
has no holding-period rule, but `tjs form-export` derives Schedule 3's year of acquisition from it, so a
lot bought on a late-December trade date that settled in January shows the earlier year (tax-logic
CA-DISP-07).

#### Carryover has no inclusion-rate adjustment for pre-2001 losses

The carryover ledger is at 100%, with the 50% inclusion applied on the T1A: right for losses after 2000. A
pre-2001 net capital loss (at a 3/4 or 2/3 rate) entered with `--claimed` is not rescaled (s.111(1.1)).
**Instead:** convert such a loss yourself before entering it.
**Code:** `src/taxjson/bin/taxjson_carryover.py`

#### reconcile-slips cannot read per-type-code T5008s

`tjs reconcile-slips` compares per security. IBKR issues one T5008 row per type code (SHS, OPC, WTS, FUT)
marked "Various", which cannot be compared, and a single broker's slip cannot be scoped to that broker's
sales when one account mixes brokers (the gains rows carry no broker). It reads T5008 / 1099-B only, not
dividend slips (`tjs slip-audit` checks T5/T3).
**Instead:** transcribe a per-security CSV from the slip.
**Code:** `src/taxjson/bin/taxjson_reconcile_slips.py`

### Engine scope: United States

#### US: specific-lot identification is not supported (FIFO only)

The US engine sells lots first in, first out (Reg. 1.1012-1(c)(1)). Specific identification and a broker's
other default (such as highest cost) are not modelled, so a 1099-B computed under specific ID will not
match per lot; year totals agree only once every lot is sold.
**Instead:** set the broker's lot method to FIFO, or reconcile by hand.

#### §1256 (60/40 mark-to-market) is not implemented

Futures and broad-based index options run through the ordinary FIFO engine: no year-end marking, no 60/40
split. The US filing outputs (`tjs form-export --form 8949` / `txf`, `tjs sum`'s FOR THE RETURN block, the
close-year lock) recognise them (plain futures, options on futures, a list of index-option roots) and keep
them off Form 8949, listing each with its P/L for Form 6781.
**Instead:** report them on Form 6781 from your 1099-B. An index option whose root is not in the list is
filed as an ordinary option: add it with a ticker.map `INDEXOPT ROOT` line.
**Code:** `src/taxjson/lib/futures.py` — `section_1256_kind`

#### US estimated taxes (1040-ES) are not modeled

`tjs instalments` models the Canadian instalment regime only and refuses in a US project. The US regime
(four due dates, the 90% / 100% / 110% safe harbours, the annualized-income method, the Form 2210 penalty)
differs in every detail.
**Instead:** use the Form 1040-ES worksheets; `tjs estimate` still gives the investment-income tax.
**Code:** `src/taxjson/bin/taxjson_instalments.py`

#### US AMT is not computed

Only the Canadian estimate has an AMT check. Under US AMT, long-term gains and qualified dividends keep
their rates, so investment income alone rarely triggers it; the usual triggers (ISO exercises,
private-activity bond interest) are not in broker exports. `tjs amt` refuses in a US project.
**Instead:** run Form 6251 with your full return data.

#### US: options as replacement property are advisory-only

§1091(a) covers "a contract or option to acquire". A call bought inside the window of a stock loss is
warned about, but the loss stays allowed (the Canada engine enforces its call rule).
**Instead:** treat the warning as an instruction and adjust the loss yourself.
**Code:** `src/taxjson/lib/core.py` — `detect_option_replacement_matches`

#### US: §1091(e)(1) — a long sale after a short-cover loss is not a wash trigger

Only another short sale (§1091(e)(2)) registers as a replacement for a loss on closing a short. A cover
that flips the position long, followed by a sale inside the window, leaves the cover loss allowed (tax-logic
US-WASH-19). Same-year totals match; the year and the 8949 code W can differ.

#### US: a replacement bought and sold in the loss's own account before the loss does not wash it

An IRA purchase (US-WASH-11) or a purchase in another taxable account (US-WASH-22) washes a loss even when
sold before it. Within the loss's own account, a replacement bought and sold before the loss shares were
bought is not matched (US-WASH-21): applying the rule there would chain through every buy/sell cycle.

#### Sub-micro quantity tolerance in the US engine

A US row under 1e-8 units is left out, a lot residue of at most 1e-8 units is folded into the closing sale,
and a sale's excess of at most 1e-8 units opens no position; each case is named in a warning (tax-logic
US-CRYPTO-08). The effect is cents.

### Estimate, instalments and AMT

`tjs estimate`, `tjs amt` and `tjs instalments` are planning figures, labelled as estimates in their
output and never filing numbers.

#### Non-eligible dividends are estimated as eligible

Canada: every Canadian dividend gets the eligible gross-up and credit; non-eligible dividends (small
business corporations, some REIT and LP distributions) are taxed higher, so the estimate understates them.
T5 box 18 capital-gains dividends named in `[[capital_gains_dividends]]` are taxed as capital gains; T3
trust allocations are not split by type. US: every dividend is treated as qualified (REITs, some foreign
payers and short-held shares are not). Brokers' exports do not carry the slip box.
**Instead:** use the T5/T3 or 1099-DIV for the return.
**Code:** `src/taxjson/lib/tax_estimate.py` — `CA_ASSUMPTIONS`, `estimate_usa`

#### Estimate classifies dividends by listing when the books carry no ISIN

The issuer's country comes from its ISIN when the books carry one (IB rows); otherwise from the listing
the dividend is booked under (`.TO` Canadian, `.US` foreign). In a Canada project tobase.map books a
known interlisted share under its home listing, so those are right; a pair the shipped list lacks, a
ticker.map line that keeps the two lines apart, or a depositary receipt is still classified by its
listing. The foreign tax credit uses the books' TAX rows (15% assumed only when there are none) and is
capped at 15% of the foreign dividends, not at the Canadian tax on them.
**Instead:** `tjs tips` flags a Canadian payer held on its US line when it sees the `.TO` line.
**Code:** `src/taxjson/bin/taxjson_run.py` — `_issuer_is_canadian_by_symbol`

#### Credits, OAS recovery tax and AMT adjustments outside the books

The only non-refundable credit modelled is the basic personal amount: CPP/EI, the employment, age and
pension amounts and donations cannot be entered. There is no Old Age Security input, so the recovery tax
investment income can trigger is left out. The AMT check sees only the books and `other_income`: the
stock-option deduction add-back, donated securities and other credits are not modelled, so it can say
"not binding" when AMT binds. The printed assumptions line says so.
**Instead:** fold the effect into `other_income` / `--deductions`, or read the figure as
investment-income only.
**Code:** `src/taxjson/lib/tax_estimate.py` — `estimate_canada`, `_amt_canada`

#### FX on cash, slip gains and self-employment CPP/EI are not in the estimate

The estimate counts the books' sales and income only: the FX result on foreign cash (`tjs fx-cash`),
T3 box 21 capital gains (and T5 box 18 amounts not named in `[[capital_gains_dividends]]`) are not in it,
and the instalment schedule has no input for CPP/EI on self-employment earnings, which CRA adds to the
instalment amount. The output's assumptions line and the instalments `NOT MODELLED` note say so.
**Instead:** fold a material FX or slip gain into `other_income`, and add self-employment CPP/EI to the
instalments you pay.

### Foreign cash (fx-cash)

#### FX on foreign cash: ledger v2 limits

The default ledger (v1) is flagged NOT RELIABLE everywhere and never shows a reportable figure. The opt-in
ledger v2 (`fx_cash_ledger = "v2"`) refuses (NOT COMPUTED, each problem listed) rather than guess, so on
real books it needs `.tt` lines for what no export carries: a `CASHOPEN` for its first year, a `CASHMOVE`
for each deposit and withdrawal (or `fx_cash_inflow_cost = "spot"`), and a `CASHBAL` for every account
whose export has no balance (RBC, Questrade, Webull, Coinbase, a bank). It reads Questrade's FX
conversions, deposits, withdrawals and cash-only transfers, but no Webull or generic-importer cash event:
such an account is refused until its `.tt` cash lines are declared complete (`CASHBOOK <book> complete`).
IB's daily futures cash settlement is not modelled (an IB account holding futures at a statement end does
not reconcile), and a combined IB statement is one cash account. Rows from corporate actions,
crypto-for-crypto swaps and fees or rewards paid in a coin move no foreign cash; the cash a corporate
action paid (cash in lieu, boot) is ledgered.
**Instead:** use v2 only with those lines declared; it stays opt-in until it has been checked on real
books.
**Code:** `src/taxjson/lib/fx_cash_v2.py` — `build`; `src/taxjson/lib/cash_events.py` — `collect`; `src/taxjson/bin/taxjson_fx_cash.py` — `_non_cash`

### Reports and project files

#### `<account>.sum` vs `<account>_wash.sum`

Each taxable account gets two summaries. `<account>.sum` is the account computed alone (no other account,
no sheltered context). `<account>_wash.sum` comes from one blended run over all taxable accounts (Canada:
one ACB pool per security across accounts, s.47; US: §1091 across accounts) plus the sheltered accounts
when there are any; it is written only by a full `tjs run`. **File from `<account>_wash.sum`.** The pair is
deliberate: comparing them shows which losses were denied only because of another account. Neither file
labels its role at the top. Their TOTAL PROCEEDS / TOTAL COST lines are the engine's signed figures (short
covers and written-option buy-backs count as negative proceeds), not Schedule 3 cells.
**Instead:** take Schedule 3 / Form 8949 figures from `tjs form-export` or the FOR THE RETURN block of
`tjs sum`.
**Code:** `src/taxjson/bin/taxjson_run.py` — `stage_account`, `stage_blended_wash_pass`

#### Futures are booked on their settled P/L

A futures fill that opens a position carries no money; the close carries the realized P/L (commissions on
both legs), converted at the closing leg's rate. So the `.sum` shows cost 0 and proceeds = the P/L,
holdings show an open futures position at cost 0, and Schedule 3 shows a gain as proceeds and a loss as
ACB, the way a broker's T5008 does. The P/L is realized at the close, not marked daily. A futures row
other than a trade (an opening balance, a transfer) stops the conversion. US books close partial
positions FIFO; §1256 is above.
**Code:** `src/taxjson/lib/futures.py`

#### T1135 sees only the brokerage books

`tjs t1135` sums the cost of the foreign property in the taxable accounts' books. Foreign property held
elsewhere (a foreign bank account, certificates, real estate, a debt owed by a non-resident) counts
toward the same $100,000 and is not seen; the report says "on these books", and "so far" until the year
has ended. Its cost amounts are the books' ACB: a transferred-in position with no history opened by an
`OPENING … cost=unknown` line has no cost and is flagged "cost understated" while held.
**Instead:** add the property held elsewhere yourself; declare the real history of transferred-in shares.
**Code:** `src/taxjson/bin/taxjson_t1135.py` — `build_report`

#### `tjs audit` and the gains traces show a row's own id

The id is printed unmasked so it can be pasted into `--id`. For most brokers it is a content hash, but a
Kraken row's id is the exchange's own ledger reference, which the Kraken parser's own messages mask.
**Instead:** review audit and trace output before sharing it (SECURITY.md).
**Code:** `src/taxjson/bin/taxjson_audit.py`; `src/taxjson/lib/trace_format.py`

#### Old per-purpose project files stop the run until migrated

`yf_ticker.map`, `crypto_ticker.map`, `ticker_extraction_overrides.txt`, `t1135.map`,
`amt_carryover.txt`, `claimed_losses.txt`, `capital_gains_dividends.map`, `distributions.map`,
`missing_history.json` and `phantoms.json` are no longer read: their contents now live in ticker.map,
taxjson.toml and `.tt` lines. While one is in a project, every command except `init`, `help`, `migrate`
and `checklist` (which lists it) stops naming it; there is no silent fallback. `tv_exchange.map` stops
nothing (nothing reads it).
**Instead:** run `tjs migrate`, check the result, then delete the `.migrated` copies it leaves.
**Code:** `src/taxjson/lib/migrate.py` — `legacy_files`, `legacy_message`

#### The intra-day FX rate is fetched silently

For a base currency other than CAD, a failed intra-day spot fetch (usually a closed market) prints
nothing; the historical rates are unaffected, and a failed historical download is warned.
**Code:** `src/taxjson/bin/to_base_curr.py` — `_spot_row`
## How the numbers are checked

The figures go on real returns, so correctness is checked in layers, not by tests alone.

- **The broker's positions are the outside check.** `tjs sanity` compares the positions the books say
  you hold with the positions in your broker's own export, account by account; `tjs run` ends with it
  when `taxjson.toml` names the holdings files. Every internal report can agree and still be wrong; this
  is the check that finds a stray fractional share or a split option class.
- **`tjs audit` is the authority.** It recomputes every sale from the parsed broker row through FX,
  ACB or FIFO and the superficial-loss / wash-sale decision, and ties each figure to the pipeline's
  saved gains; any disagreement exits 1. The other filing commands (`sum`, `carryover`, `form-export`,
  `t1135`) are checked against it.
- **Property fuzzers.** Seeded generators build thousands of random books per run and check
  conservation, determinism and ordering laws (the engine, custody transfers, settlement-lag and split
  interactions).
- **Mutation testing** of both gains engines (the ACB pool and the superficial-loss window; FIFO lots
  and §1091 matching), so a boundary no test pins gets noticed.
- **`tjs tax-logic` is the spec.** It states every rule the engines apply, each with a stable id. Tests
  are tagged with the ids they pin, and the gate (`scripts/check_tax_rules.py`) fails on an unknown id,
  a test that mixes the two countries, or a new rule with no test. Canadian and US rules never mix: a
  one-country setting, flag or command is refused in the other country's project.
- **Independent reviews.** Eight review rounds, a security review of the public surfaces and two
  full-coverage audits of the whole codebase: adversarial reviews, hand-computed statutory scenarios,
  parser coverage against real exports, real multi-account books, the installer and the privacy gate.
  Every confirmed finding was reproduced, fixed and pinned with a test.
- **Nothing personal leaves the machine.** `scripts/check-pii.sh` runs in every gate and as the
  pre-push hook and fails closed; `tjs redact` strips the account numbers, names and contact details it
  recognises from an export (review the result before sharing it). CONTRIBUTING.md has the details,
  including what git itself publishes about you.
- **Filed-year locks.** `tjs close-year` snapshots a filed year; every later run recomputes it and
  reports any drift.

What the tests do not prove: they run on synthetic files and check that the code applies the rules as
written in `tjs tax-logic`. They are not an assurance that a rule is interpreted correctly for your
situation. `scripts/ci.sh` runs the whole gate locally (CONTRIBUTING.md).
