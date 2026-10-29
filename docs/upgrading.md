# Upgrading

taxjson is pre-1.0: a release can change a file's format, a command or a
project's layout. This page says how to upgrade, what to do after, and
every change that asks something of you, release by release. The full
history is [CHANGELOG.md](../CHANGELOG.md).

## How to upgrade

Re-run the installer line you installed with:

```bash
bash -c "$(curl -fsSL https://taxjson.com/install.sh)"
tjs --version
```

It upgrades along the channel you installed from and keeps your choice of
the fetch plugin (`--without-fetch` is remembered). Your projects are
never touched by an upgrade; the next command you run in one tells you if
it needs something (a `tjs migrate`, a `tjs update-tobase-map`).

**Channels.** `stable` (the default) is the release that has held up;
`beta` the one being tried; `latest` the newest release, as soon as it is
tagged; `dev` the `main` branch, unreleased; `vX.Y.Z` exactly that
release (a pin — also how you go back). `stable` and `beta` are named in
[`channels.json`](../channels.json) on `main`; `latest` is always the
newest `vX.Y.Z` tag. Pick one with `--channel NAME` (after `_` on the
installer line: `bash -c "$(curl -fsSL https://taxjson.com/install.sh)"
_ --channel beta`) or `TAXJSON_CHANNEL=NAME`; the installer prints
`channel stable → release vX.Y.Z` and remembers the choice in
`~/.config/taxjson/channel`, so re-running it upgrades along the same
channel. A channel never moves an install backwards (name a version to
go back). Only an annotated release tag on `main`'s history is installed.
`tjs channels` shows where every channel points and what this machine
runs; `TAXJSON_DRY_RUN=1` prints what the installer would pick and
stops: an existing install only has its tags fetched, and a missing one
is not created (the channel is resolved in a temporary clone). The installer leaves alone a `taxjson` or `tjs` in
`~/.local/bin` that is not its own link, with a note. How releases are
cut: [releasing.md](releasing.md).

## After an upgrade

In each project you keep working in (each year folder):

1. Run `tjs checklist`. Its set-up section says when the installed
   taxjson is newer than the project's last run, and names any step the
   upgrade opened (a migrate, a tobase.map update).
2. `tjs run`. A command that stops with "run `taxjson migrate`" means an
   old file format: `tjs migrate --dry-run`, then `tjs migrate`.
3. Canada: `tjs update-tobase-map` lists what a newer interlisted list
   changes; `--write` applies it.
4. A filed year: `tjs check-filed` (or the year's `tjs run`) says whether
   the new release moved any filed figure (`filed <year> DRIFTED`). An
   engine fix that moves a filed year is a candidate for an amendment, not
   a silent change.

## Changes that ask something of you

Newest first. Each says what changed and what to do. Releases not listed
needed nothing.

### v0.38.0

- **A redacted copy left in inputs/ is no longer read.** An account
  folder holding a `NAME.redacted.csv` (or `.tt`) that `tjs redact FILE`
  wrote beside its export had those rows booked twice. Run `tjs run`:
  that account's gains, income and holdings drop to their true figures;
  if a return used them, compare. Move the copy out of inputs/ (`tjs
  redact --out DIR FILE` writes it elsewhere).
- **Moves between your own accounts and in-kind moves can change gains
  and costs.** Run `tjs run`, then compare a year already filed (`tjs
  check-filed`):
  - Canada: a merger whose ratio differs by account, after a move of
    your own between two taxable accounts: the share count after the
    merger, the gain on a later sale and the year-end holdings change,
    because they were wrong before.
  - Both countries: where a taxable account's transfer-out is an in-kind
    move to a registered account that the run did not book, and another
    taxable account has a transfer-in of a different quantity of the
    same security within 10 days, that transfer-in is now booked as one
    from outside your books (at its stated book value, else said as
    ATTENTION), not as a move of your own. In Canada the pool's cost
    takes in the stated book value; in the US the receiver's lots carry
    the stated basis, not the sender's. Settle the in-kind move as its
    warning says; the transfer-in's figure no longer changes when you
    do.
  - A taxable leg and a registered leg 11 to 45 days apart are now an
    in-kind move "NOT booked", a warning naming its INKIND line (`tjs
    run --strict` stops). Add that line to book the contribution or
    withdrawal; the contribution's sale or the withdrawal's purchase
    then enters the figures. An unbooked in-kind withdrawal's taxable
    leg is no longer a NO-cost arrival: delete any .tt BUYSELL or
    OPENING added on the old "NO cost" or "missing purchase" advice and
    add the INKIND line instead.
  - US: where a transfer-out had two candidate transfer-ins in your
    other taxable accounts within 10 days, the lots now go to the
    closest-date receiver and the other arrival is booked at its stated
    basis. A pairing that takes part of a row (100 out, 90 in) now
    carries the paired units' lots, so sales that read as a short are in
    the totals, and the run says ATTENTION ... the quantities do not
    pair (`--strict` stops). Moves whose legs differ only by ticker.map
    spelling, or that were delivered in parts, carry lots too.
  - A transfer journal between two listings the run joins (RBC US-dollar
    units, an IB to Questrade listing change) is now a move of your own:
    the per-account books (`<account>.sum`, `tjs list`, `tjs sanity`)
    move the units between the accounts. Canada's pooled filing figures
    do not change; in a US project the lots carry across, so a figure
    there can. Delete any .tt purchase added on the old NO-cost advice.
  - Canada: coins moved between two taxable crypto accounts now show in
    the receiving account's views (`tjs list`, find-missing-history,
    `reports/<account>_holdings.toml`); no filed figure changes. Delete
    any OPENING cost=unknown line written for such a sale.
- **Crypto income and cost can change on a re-run.** Run `tjs run` in a
  project with a Coinbase or Kraken account:
  - Kraken: a reward paid in CAD or EUR whose fee was charged in another
    currency or a coin changes its income (it was the USD net labelled
    as that currency). US projects: a stablecoin reward whose fee was
    paid in another currency now costs its full par value, which lowers
    a later gain on those coins.
  - Coinbase: a reward with no Subtotal and no Price is income at its
    Total less the fee, so the staking income and the coins' cost go
    down by that fee. A Convert with no Subtotal is valued at its Total
    less the fee, with the fee added to the coins received: the proceeds
    of the coins given up and the cost of the coins received both move.
  - A code ticker.map folds into a USD stablecoin with a `GLOBAL` line
    is now US-dollar cash on Coinbase: the stablecoin position goes
    away, and `tjs fx-cash` and the `tjs crypto-sends` pool include it.
  - A Kraken instant trade split across two yearly ledger exports is now
    booked, including one whose receipts (or spends) are themselves split
    between the two: its disposition and acquisition appear, with every
    leg, and the UNBOOKED warnings go. If you entered it by hand in a .tt file, remove that
    entry.
  - `tjs crypto-sends` no longer books a network-fee sale for a send
    that arrived as two deposits in one account: delete the `-fee` line
    an older release wrote to crypto_sends.tt for such a send, and run
    again. Two exports' Send or Receive rows alike but for their ID now
    count as two moves, so crypto-sends can list a send it did not list
    before, which then needs a decision.
  - Rows that were booked now stop the run, naming the line. Coinbase: a
    reward with a negative quantity, Subtotal or Total (remove it from
    the export and enter the correction as .tt lines); a Convert or
    reward whose Total, Subtotal and fee do not add up, or whose fee is
    not less than its Total, and a Convert that charges a fee with
    Subtotal, Total and Price blank (fix the row or download it again).
    Both: two downloads that give one custody row id (a Kraken
    withdrawal or deposit, a Coinbase Send) or one Coinbase reward with
    different content: keep the newer download only, or correct the row.
- **Income and cost read from broker files can change.** Run `tjs run`:
  - Questrade and RBC Direct: a reinvestment reversal now cancels only a
    reinvestment of its own broker account and, at Questrade, its own
    currency. Where one was matched across currencies or accounts, the
    ACB and income change. A reversal with no original of its own
    account and currency is refused: add the missing older export.
  - Questrade: a reinvestment, stock dividend or cash in lieu that a
    later row reverses now drops out of both overlapping downloads even
    when they write its numbers differently (10 and -100 in one, 10.0000
    and -100.00 in the other). One copy stayed booked as phantom units,
    so the ACB of that security and later gains on it change.
  - IB: a dividend or return of capital named by ISIN may move back to
    the listing actually held on its date when the statement has a split
    or ticker change of the other listing; the ACB follows.
  - Webull: one account's downloads pasted into one file can change that
    account's gains, as a day's sale and rebuy now replay in the order
    each download lists them.
  - Generic importer: a key of `[actions]` written with surrounding
    spaces now matches rows, so rows it left unmapped (an UNBOOKED
    warning) are booked.
  - `tjs slip-audit`, a typed T3 with return of capital: if you sold
    units of the fund between its distributions and already pasted the
    older single `ADJUST ... type=roc` line on the last distribution,
    replace it with the new lines (or the fund's own per-distribution
    breakdown). The units sold get a lower ACB, so their gain rises.
  - After a rename-only SPLIT (ratio 0), the new ticker's record-date
    holdings include the renamed shares, so a distribution sized on them
    can change.
- **Canada: a TSX-listed security traded in US dollars can settle a day
  later.** A trade in US dollars on a Canadian market (a fund's
  US-dollar unit, such as QZX.U.TO) whose export prints no settlement
  date (always for IB; for Questrade, RBC or a generic import only when
  the settle cell is blank) now settles on the next day both Canadian
  and US clearing settle, as CDS settles US dollars (tax-logic
  CA-DATE-05). Before, a trade made the day before a US-only holiday
  (Martin Luther King Jr. Day, Memorial Day, Juneteenth, the Fourth of
  July or the Friday it is observed, US Thanksgiving), or on it, settled
  on that holiday. Run `tjs run`: such a trade's cost or proceeds is
  converted at the new day's rate, so its ACB and gain can change, and a
  sale in the last trading days of December can move into the next tax
  year. Canadian-dollar trades and printed settle dates do not change.
- **A `.tt` file written or copied by an older release may need a line
  changed.** Then run `tjs check-tt` and `tjs run`:
  - A warrant or right exercise (from `taxjson-convert-tt`, or copied
    from `tjs events`): write the file again, or add
    `exercise_of=SHARES` to the end of the warrant's ASSIGN line. The
    exercise year's capital loss of the warrants' cost goes away, and
    the shares' cost, and so a later sale's gain, carries it.
  - Dividend, payment-in-lieu, tax or ADJUST lines copied from `tjs
    events`, `divs`, `dil` or `roc`: copy them again, or add `record=`,
    `label=`, `dealer=`, `issuer=` and `type=` by hand. A January trust
    distribution with a December record date moves back to the record
    year, a Canadian dealer's payment in lieu is a deemed dividend
    again, and a `type=roc` ADJUST is return of capital again.
  - An opening balance copied as `BUYSELL DATE 00:00:00 ...`: replace it
    with its `OPENING DATE SYMBOL QTY CUR TOTAL [LOT-DATE]` line (or
    copy it again), which corrects a doubled position or a missing lot
    date.
  - A stock dividend's shares: write the file again, or add
    `type=stock_dividend` to the end of that BUYSELL line. A US sale of
    the old shares then gets its share of their basis.
  - A purchases.tt drafted from IB lots by `tjs find-missing-history
    --write-purchases` in a `tax_date = "settle"` project (Canada's
    default) holds the trade date: change each line's date to the
    settlement date (T+2 before late May 2024, T+1 after, market
    holidays skipped), or draft it again with `--write-purchases
    --force`. A foreign-currency lot's ACB moves by the rate difference
    between the two days, and the superficial-loss window moves with the
    date.
  - A line that gives the same `key=` token twice now stops `tjs run`
    (`tjs check-tt` names it): delete the one you do not mean. If the
    second value was the one you meant, the figures change from what the
    first gave.
  - A purchases draft kept from `tjs find-missing-history ACCOUNT
    --write-purchases` may name a transfer-in's source wrongly, or leave
    out a line the run needs: draft it again with `--write-purchases
    --force`.
- **`tjs t1135` can change: compare it with a T1135 already filed.**
  - The cash an account holds outside Canada now counts toward the
    $100,000 test, a category 1 row at its cost: a crypto account's US
    dollars and USD stablecoins, and the cash of an account whose
    `custodian_country` is outside Canada. A project below the threshold
    before may now need a T1135: run `tjs t1135` again. The cash is read
    on the project's tax_date dates, and its INCOME (and its country's)
    includes the interest credited on it, converted to CAD. A USD
    stablecoin reward is that cash row's income, no longer a `CRYPTO 6`
    row of its own.
  - If `tjs fx-cash --ledger v2` refuses (usually a missing `CASHOPEN
    <Jan 1> USD <units> <cost>` line, `0 0` when none was held), the
    verdict reads NOT SETTLED and the checklist's t1135 step needs
    attention: fix what the ledger names and run `tjs t1135` again. A
    crypto platform's cash shows under country CRYPTO until you set
    `custodian_country` on the account (`"CAN"`: not foreign property).
  - An account held with a custodian outside Canada (a US brokerage
    account): set `custodian_country = "USA"` (ISO alpha-3) under its
    `[accounts.NAME]` table and run `tjs t1135` again. Its Canadian
    issuers' shares become category 6 rows under that country, at the
    account's share of the pooled ACB (its units that day times the
    pool's cost per unit), which can raise the maximum total cost and
    change the filing verdict. Nothing changes while the key is unset.
    In a Canadian project `tjs format`'s per-account reference block now
    lists the key, so a taxjson.toml formatted by an older release shows
    that block as changed until `tjs format` runs again.
  - The cost amounts are now the engine's (`tjs run`'s) pools: they can
    rise for a warrant exercise or an exercised call with a same-day
    sale, and differ for a split inside a trade's settlement lag. A
    foreign futures contract reopened within 30 days of a loss carries
    the denied loss as its cost amount, a row of its own. Either can
    raise the maximum total cost near the $100,000 threshold.
  - INCOME can rise: a dividend paid on Dec 31 that settles in January
    counts in its pay year, and interest rows that name a held foreign
    security (a bond's coupon) are added to that property.
  - A new warning and `! ` line name shares on a Canadian listing whose
    ISIN is foreign. Once you have checked, add `T1135 SYMBOL COUNTRY`
    (or `T1135 SYMBOL CA`) to ticker.map; with the country line their
    cost counts toward the threshold.
  - `--year-wash-only` is ignored, with a note; in `--json`,
    `deferred_wash_not_in_cost` is always `{}` and `full_history_wash`
    always true.
  - `tjs t1135 --json` adds `total_settled`, `cash_accounts`,
    `cash_ledger_status`, `cash_not_counted` and `cash_unknown_cost`,
    and cash rows carry `cash`, `cash_account`, `currency` and
    `cash_why`; existing keys are unchanged. Canada's gains, ACB and
    income figures do not move.
- **Currency gains in crypto-sends and fx-cash can change.** Run `tjs
  run`, then `tjs crypto-sends` and `tjs fx-cash`:
  - crypto-sends (Canada): the US-dollar and stablecoin pool includes a
    Coinbase account's US dollars, so a stablecoin send's currency gain
    and `pool.avg_cost` / `units_now` change for anyone who held USD on
    Coinbase; a coin bought in USD by card or bank, with no USD deposit
    row, now draws on the pool and may show "the USD/stablecoin pool
    went short". A send that drew on a Kraken stablecoin reward whose
    fee was in CAD gets a much lower currency gain. Sends decided self
    (and Kraken Hybrid Earn moves) stay in the pool, so the gains of
    later gifts and payments, and `units_now`, change.
  - With `fx_cash_ledger = "v2"`, a stablecoin gift or payment's
    currency gain belongs to `tjs fx-cash`: put the `fx-cash v2:
    CASHMOVE <date> USD -<units> proceeds=<value>` line crypto-sends
    shows (or `kept` for a self send) in a .tt of the account, and do
    not add crypto-sends' total by hand. If you added it to a v2 figure
    on an older release, take it out. With the default ledger (v1)
    nothing changes: add it by hand. The two figures can differ: v2
    prices against all taxable accounts' US dollars and does not apply
    the superficial-loss rule.
  - Ledger v2: a conversion's commission in the base currency is part of
    its cost (buying foreign currency) or comes off its proceeds
    (selling it), so gains on currency bought from CAD drop by those
    commissions; a v2 figure filed before the upgrade was too high by
    them. A commission charged in the currency sold or bought (a Kraken
    fee in USD on US dollars sold for CAD) is now part of the conversion
    too, not a spend with a gain of its own: for a year with one, run
    `tjs fx-cash --ledger v2` again; the gain moves by about the fee
    times the day's rate less its cost. In a US project, a
    USD-stablecoin reward priced in EUR or another foreign currency no
    longer adds that currency to the pool.
  - Both ledgers: securities moved in kind into or out of a registered
    plan, and transfers arriving at book value, no longer move cash, so
    the figures change, and ledger v2 may now compute where it said NOT
    COMPUTED. An opening balance in a foreign currency (a .tt `OPENING`
    line) no longer takes cash out of the account either: a ledger that
    showed an overdraft or an unreconciled balance on the snapshot day
    may now reconcile, and its figure may change. Ledger v1:
    USD-stablecoin rewards priced in CAD are now taken in as US dollars,
    which changes the USD pool and its ACB.
  - A work/to_base.csv whose rates are to another currency than
    `base_currency` (copied from another project, or older than a base
    change), or that mixes targets, now stops crypto-sends and the
    de-peg rates: delete it and run `tjs run`.
- **Canada: the estimate's foreign tax credit follows forms T2209 and
  T2036.** Run `tjs estimate`, `tjs amt`, `tjs plot-tax` and `tjs
  instalments` again. If the withholding on your foreign dividends is
  more than the federal tax on their share of net income (with 15%
  withheld, a taxable income under about 105,000 in 2025), the federal
  credit drops to basic federal tax x foreign dividends / (net income
  less line 25300); the provincial credit takes the rest, up to the
  province's tax otherwise payable x the same share (Ontario and Alberta
  before their dividend tax credit), and anything beyond that is not
  credited. So the estimate, the instalments built on it and the AMT
  check rise. The provincial share now divides by net income less line
  25300 (it used line 23600). In `--json`, `trace_with.fed_ftc` is now
  the federal credit claimed (it was the foreign tax), with
  `foreign_tax`, `ftc_share`, `fed_ftc_limit` and `prov_ftc_limit`
  beside it; new top-level keys `foreign_tax_creditable`, `ftc_federal`
  and `ftc_provincial`; `ftc_assumed` keeps its value.
- **Canada: the minimum tax's foreign tax credit is s.127.54's, and a
  spin-off's deemed dividend takes no assumed withholding.** In a year
  with foreign dividends where AMT binds, `tjs amt` and `tjs estimate`
  show a higher federal minimum tax, provincial AMT and minimum tax
  carryover. A taxable spin-off's deemed dividend of a foreign issuer,
  in an account whose books carry no TAX row, no longer takes 15%
  assumed withholding: the foreign tax credits fall, and the estimate,
  the instalments and, where AMT binds, the minimum tax and its
  carryover rise, by up to 15% of the deemed dividend. A year closed by
  an older release recorded the lower carryover in `filed/<year>.json`
  (`carryforwards.minimum_tax`), which the next year reads: if AMT bound
  that year with foreign dividends or such a spin-off, close it again
  (`tjs close-year --force`) or enter the carryover from the notice of
  assessment in `[estimate] amt_carryover`. If a broker did withhold on
  the spin-off, add a .tt TAX line. In `--json`, `amt.ftc` is the
  special credit, with `ftc_regular`, `ftc_foreign_taxes` and
  `ftc_income_limit` beside it; `ftc_source` for a mix of TAX rows and
  the assumption starts "TAX rows X + Y assumed" (it said "actual TAX
  rows"); new keys `ftc_basis`, `foreign_tax_from_rows`,
  `foreign_tax_by_assumption` and `foreign_div_noncash`.
- **Canada: the tax on investment income with AMT can drop.** When your
  other income alone owes minimum tax (other income with large
  `carrying_charges` or `deductions`), that top-up is no longer counted
  against the investments: `estimated_tax_with_amt` in `tjs estimate
  --json`, `tjs amt`'s "with AMT and carryover" and `tjs plot-tax`'s
  "With the minimum tax" drop by it. The return's own minimum tax, the
  carryover `tjs close-year` records and the instalments do not move.
  New `--json` keys `amt.base_topup` and `amt.topup_attributed`. The AMT
  CHECK's text changed ("=> TOTAL WITH AMT" is "=> ESTIMATED TAX ON
  INVESTMENT INCOME WITH AMT:", "=> AFTER CARRYOVER" is "... WITH AMT
  AND CARRYOVER:", and the "federal / ON" row is two rows): a script
  that scrapes it should read `--json`.
- **Canada: the foreign tax credit counts each security's withholding
  only up to 15% of that security's own foreign dividends** (ITA
  s.126(7), s.20(11); form T2209), not 15% of all foreign dividends
  together. A security is summed across the taxable accounts that hold
  it. Where one security had more than 15% withheld (a 30% US
  withholding with no treaty form on file) beside a security with less,
  or a spin-off's deemed dividend was in the year, the credit falls and
  the estimated tax rises, by at most the withholding above each
  security's 15%. Run `tjs estimate`, `tjs amt` and `tjs plot-tax`
  again:
  - The --details note names the amount above 15% by security, as
    possibly deductible on line 23200; the estimate does not deduct it
    (a deduction you claim goes in `[estimate] deductions`). The broker
    rounds each 15% up to the cent, so a security can show a cent above
    the limit.
  - A TAX row with no symbol, or whose symbol paid no foreign dividend
    in the year, counts only up to 15% of its account's foreign cash
    dividends that no TAX row names; a note says how much counted.
  - In a year the minimum tax applies, the special foreign tax credit
    can fall with it, and the AMT top-up and the carryover it creates
    can rise: a year already closed keeps what its lock recorded (close
    it again with `tjs close-year --force`).
  - `--json` adds `foreign_withholding_over_cap_by_security` and
    `foreign_withholding_unpaired`; `ftc_source` reads "capped at 15% of
    each security's foreign divs".
- **Canada: in a year the minimum tax applies with foreign tax withheld,
  the minimum tax carryover the year creates and the provincial minimum
  tax change.** Both move only where AMT binds and the special foreign
  tax credit is above the regular federal credit (foreign dividends with
  tax withheld and little regular tax); without foreign tax nothing
  moves. Run `tjs estimate`, `tjs amt` and `tjs instalments` again:
  - The carryover a year creates is now its s.120.2(3) additional tax
    (T691 Part 7 line 14), higher than before by a third of the special
    credit above the regular one: in `tjs amt`'s "Created this year
    (additional tax)", the carryover `tjs estimate` carries forward, and
    what `tjs close-year` records in `filed/<year>.json`
    (`carryforwards.minimum_tax`: `created`, `closing_by_year`,
    `closing`). The year's tax does not change. A year closed by an
    older release keeps the lower figure in its lock, and the next year
    reads it: if AMT bound that year with foreign tax, compare the
    lock's `created` with line 14 of Part 7 of the T691 you filed, then
    close that year again (`tjs close-year --force`) or enter the
    carryover from your notice of assessment in the next project's
    `[estimate] amt_carryover`. In `--json`, `amt.carryforward` and
    `amt.carryover.created` are now that figure (`amt.excess_fed` stays
    the excess), with the new key `amt.additional_tax_sftc_part`.
  - The provincial side follows forms T2036, AB428 and BC428: Alberta's
    minimum tax rises (35% of the minimum amount over basic federal
    tax), as does British Columbia's (its factor of the s.120.2(3)
    additional tax), and Ontario and Alberta may now take a provincial
    foreign tax credit against the minimum tax. So the AMT top-up, the
    tax on investment income with AMT and the instalments' net tax owing
    move; the federal minimum tax does not. `tjs amt --json` adds
    `provincial_ftc_regular`, `provincial_ftc_min_tax`,
    `provincial_ftc_change`, `provincial_amt_base`,
    `provincial_amt_base_kind`, `minimum_amount`, `basic_fed` and
    `additional_tax_s120_2`.
- **Year end and filed years:**
  - `tjs check-filed` (and the drift check in `tjs run`) in a filed year
    whose inputs were frozen from a shared folder may print new notes: a
    shared UTF-16 export with a changed row of the year, a shared generic
    mapping (`<csv>.toml`, `generic.toml`) changed since the close, or a
    shared file it could not compare. `tjs years` may newly mark such a
    year `shared changed` or `shared unchecked`. No figure moves: each
    is a correction in the shared folder that the filed return does not
    have. Compare it with the year's own copy, then amend (copy it in,
    `tjs run`, file the T1-ADJ, `tjs close-year --force`) or keep the
    books as filed.
  - A next year whose `prior_year_record` names a lock from an older
    release with filed dispositions dated outside that lock's year now
    stops in estimate, amt, carryover and handoff. Close that year again
    in its project with `tjs close-year --force --filed-dispositions
    <CSV>`, the CSV holding only that year's sales.
  - A closed year whose lock was deleted (its taxjson.toml still carries
    the freeze note) now stops the next year's estimate, amt, carryover,
    handoff and `fx-cash --ledger v2`: restore filed/<year>.json from
    git or a backup, or close the year again.
  - A closed year with a loss sale sharing its date and time with a
    replacement bought before it: run `tjs close-year --force` for that
    year to rewrite the record's ACB and deferred figures.
  - `tjs carryover` shows less available to apply where a year has its
    own `[carryover] claimed` entry, and in a closed year shows the
    lock's APPLIED and carried balance instead of the books' rebuild, so
    the balance after the year can drop to what close-year carried
    forward.
  - A filed year built on an earlier year's net capital loss that has
    since been closed again with another balance now warns in
    check-filed and the run, and `tjs run --strict` stops. If its return
    applied more than the new balance, it needs a T1-ADJ and a `tjs
    close-year --force` there. A first year closed with no `[estimate]
    other_losses` may get a note giving the net capital loss the books
    carry in: if that loss is yours, set `other_losses` and close again
    (after filing).
  - `tjs years` may newly mark a filed year `shared changed`, and
    check-filed and the run name a shared export that gained rows of
    that year (or a new shared file holding them), a removed duplicate
    row, or a Webull or RBC-dated row that went unjudged before. Compare
    each with its copy in inputs/ (`tjs -C <year> check-filed`); amend
    only if the filed return was missing the row.
  - A year migrated by an older release's `migrate` still shows its
    taxjson.toml as a changed frozen input, because the lock has no
    migrate record: check the edit, then `tjs close-year --force` there
    to record the current file, or leave it. The checklist's filed-lock
    step is now attention for a frozen year whose `[settings]` names
    `inputs_dir` or `tobase_map` again: delete that line, then `tjs
    run`.
  - `tjs handoff` may newly exit 1 on a Dec 30-31 row the broker
    corrected after the close (compare it with the slip and amend for
    the difference it reports), and exits 1 against a record that is not
    the year before the project's (close the year before in its project
    and hand off against that record).
  - Canada: a project on `option_premium_timing = "close"` whose prior
    year's lock records grant timing, or reports a written option's
    premium, gets an ATTENTION row from `tjs option-boundary`, and so
    from the checklist's option-boundary step: set
    `option_premium_timing = "grant"` with `option_grant_timing_since` =
    that year, or amend that year to remove the premium.
  - A `prev` snapshot an older release kept after a `run --account` is
    not marked partial: run fully twice to get a clean `prev`. `tjs
    compare filed` against reports an older release kept shows one
    fees.rpt change once (the "not from a broker export" line for
    checklist_results.json); nothing to do.
  - A year closed as a single-folder project and then migrated (`migrate
    --to-years`): `tjs check-filed` or `tjs years` may now name a shared
    export or decision in ../inputs that changed since its close. No
    figure moves: compare it with the year's copy, then amend or keep
    the books as filed.
- **US: figures that can change.** Run `tjs run`, then `tjs check-filed`
  in a closed year; a drift is reviewed, then amended or the lock
  refreshed with `tjs close-year --force`:
  - `tjs estimate` (and `tjs plot-tax`) falls where a long-term gain or
    qualified dividend lies between the end of the 0% capital-gain band
    and the end of the 12% ordinary band (taxable income of 48,350 to
    48,475 in 2025, 49,450 to 50,400 in 2026), by up to 3% of the part
    in that range.
  - A `[carryover] claimed` entry for the project's own year is now the
    deduction the estimate's carryover after the year and the close-year
    lock count. A lock an older release closed with a claimed entry, or
    with no `[estimate] other_income`, may carry too much: set the
    year's claimed entry (the Schedule D line 21 deduction) and close
    the year again with `tjs close-year --force`. The next year's `tjs
    handoff` flags a claimed entry that differs from the lock's
    deduction.
  - A US lock now records each lot held at Dec 31; one closed by an
    older release has none, and `tjs handoff` checks it by position only
    and says so: close that year again to have its lots checked. An
    opening whose lots differ from the record's makes handoff exit 1
    even when the units and total basis match.
  - Part of a wash-sale replacement lot moved to another of your taxable
    accounts (or journaled to another listing): the basis add and
    holding period split between the parts, so the gain and term of a
    later sale of either part change.
  - A loss on shares sold after a stock dividend, with purchases in the
    window the dividend was paid on, can be disallowed on more shares,
    and the replacement lots' basis changes with it. A sale of entitled
    shares between a stock dividend's ex or record date and its pay date
    has a lower cost, and the new shares carry that basis with the sold
    shares' dates; for a sale in an earlier year or a wash sale the run
    prints an ATTENTION line: move the amount by hand with .tt ADJUST
    rows.
  - A washed loss on a broad-based index option (SPX, XSP, NDX, RUT, VIX
    roots) is now allowed, and the replacement contract's cost falls by
    it; Form 6781's list shows the realized P/L.
  - In a year after a filed year closed with the next January's rows,
    the "wash-sale basis add to the ... sale (filed ...)" row and its
    ATTENTION line go away, and that year's loss is smaller by the add:
    run both years, and correct a return that already has the extra row.
  - Form 8949: a re-short's (d) proceeds and (e) cost both rise by the
    wash-sale loss it carries; (h) and the year's gain do not change.
    The TXF and `sum`'s Part totals change the same way, and a year
    closed on an older release reports drift on the Part I/II proceeds
    and cost lines (the gain lines match).
  - `tjs edge-cases`' "Denied losses carried in positions held at the
    end of <year>" can change in a project on `tax_date = "settle"`
    (Canada too on `"trade"`): a trade on Dec 31 that settles in January
    counts on the project's own dates. No filed figure changes.
- **US: the year's §1256 P/L is split 40% short-term and 60% long-term,
  as Form 6781 splits it.** In a project with futures, options on
  futures or broad-based index options (SPX, NDX, ...), run `tjs run`,
  then `tjs estimate`, `tjs carryover` and `tjs plot-tax`:
  - The estimate on a §1256 gain goes down, because 60% is taxed at the
    long-term rates. NET ST / NET LT and the short- and long-term
    carryover move between the terms; the total carryover does not
    change.
  - A US year already closed with §1256 P/L that carried a loss out: its
    lock carries that P/L all short-term. The next project's `tjs
    carryover` shows "! YEAR: its lock carries ... of §1256 P/L all
    short-term (closed before the 60/40 split)", and the estimate notes
    it. Close that year again with `tjs close-year --force` in its
    project, then check `tjs handoff` in the next project. A lock that
    carried no loss out needs nothing.
  - `--json`: the close-year lock's `carryforwards.capital_loss` records
    `section_1256_gain` (the year's §1256 net) and its parts
    `section_1256_short_term` and `section_1256_long_term`. `tjs
    carryover --json` rows have `section_1256` whenever the year has
    §1256 P/L (before: only with a carryover), its two parts, and
    `section_1256_unsplit` for a lock closed before the split. `tjs
    estimate --json` (US) adds `lt_input`, `section_1256_short_term` and
    `section_1256_long_term`, and `st_input` holds the §1256 short-term
    part, not the whole §1256 net.
- **Inputs that are now refused or read differently:**
  - Shared layout: a spreadsheet in `../inputs/<account>/` stops `tjs
    run` (exit 1) until its CSV conversion sits beside it or it is moved
    out. A subfolder of exports there gets a warning: move its files up
    a level so they are read. That can change a year's figures, because
    those trades were left out.
  - holdings/: a broker positions CSV saved there (an RBC Holdings
    Export, or an IB Activity Statement with Open Positions) is now
    compared by `tjs sanity` and the run's holdings check, so
    differences it skipped can appear. Any other file there makes sanity
    INCOMPLETE until it is moved out or saved as a holdings TOML or
    broker report. An RBC or IB positions file with a row that has a
    quantity and no symbol, a column given twice, or an IB contract size
    of 0 or below is refused; an RBC cost column whose label names a
    currency in lower case or in brackets is read in that currency.
  - `tjs sanity`: a group with a dated holdings file and an undated one,
    where the books changed after the dated file and a file has no
    `[meta] broker_account`, is refused (exit 2); it was compared clean.
    Add `broker_account` to each file's `[meta]`, give the undated file
    an `as_of`, or check one file at a time (`tjs sanity margin=FILE`).
  - `tjs run` stops on a parsed row with an impossible date or a NaN or
    infinite number, naming the file and line, and on an option with an
    impossible expiry or a strike that cannot be written to the 0.001:
    book such a contract in a .tt file under a symbol of your choice.
  - Webull: a file holding two accounts' downloads stops the run: save
    each account's download as its own file, in the folder of the
    taxjson.toml account it belongs to.
  - Generic importer: a mapping with two `[actions]` keys that differ
    only in case or spaces and map to different actions stops the run:
    keep the right line.
  - Questrade: a folder whose exports (in separate files) hold a
    registered plan and a taxable account is refused: move each plan's
    exports into its own inputs/<account>/ folder with the right type.
    Two taxable Questrade accounts in separate files of one folder, or a
    lone export whose Account Type contradicts the folder's type, get an
    ATTENTION line: set `combined_broker_accounts = true` for the
    account if they are meant to be combined, or move the export.
  - Questrade, a US project: a TFSA, RESP, RDSP or FHSA export in a
    `type = "taxable"` account is now booked (beside a margin account's
    exports only with `combined_broker_accounts = true`; such a folder
    was refused). In a `type = "sheltered"` account it gets an ATTENTION
    line (a US return taxes the plan, tax-logic US-BASIS-09), and `tjs
    run --strict` stops until it moves to a taxable account's folder.
    Moving it puts its trades on the return, so the US figures change.
  - `tjs fetch --positions` (Questrade) stops on a malformed positions
    response instead of writing an empty snapshot. A live option's cost
    in holdings/<account>_live_holdings.toml uses the books' contract
    size, so `tjs sanity`'s cost comparison for mini contracts changes;
    an option the books do not hold yet takes the contract size
    Questrade states (a mini contract 10 shares, not 100), and a
    contract adjusted by a corporate action (a root such as SYN1 on SYN)
    is written with `contract_multiplier_unknown = true`, its cost
    unknown until `tjs run` has its trade in the books: run `tjs
    fetch --positions` again for a snapshot that holds an option opened
    since the last `tjs run`, and sanity and `tjs opening` read the
    corrected cost. A manual export named questrade_YYYY.csv is now
    checked for overlap.
  - RBC: the suggested EXTRACT line for US-dollar units has different
    words; a line copied from the old suggestion may match fewer rows:
    compare it with the new one.
  - Questrade Excel exports: convert again a workbook whose header
    repeats a label (the converted file is now refused, like the raw
    one) or whose ids are formatted with leading zeros. `--sheet N`
    picks a sheet named N before the index N. The converter needs the
    xlsx extra (`TAXJSON_EXTRAS=fx,xlsx` on the installer line), which
    now brings openpyxl only, not pandas (pandas still comes with the
    `fx` extra and `all`); nothing taxjson runs needs pandas through
    `xlsx`.
  - An in-kind move valued from a close cache entry whose unit cannot be
    read ("USd") is looked up again online, so its value can change;
    offline the run stops with "cannot be valued" until it is run online
    or an INKIND line is added. Offline `tjs harvest` no longer serves a
    cached quote dated after today or a boolean price, and can stop with
    "no current price in the cache": run once online. A damaged
    rate-cache record (an impossible date) is dropped, and offline runs
    have no rate for its dates until the next online run.
  - A redacted copy made by an older release can still hold a name kept
    by a misread header row (a label word, then a name split over cells:
    `Name,<first>,<last>`), a year turned into 9999, a missing
    prior-year loss record, a missing linked taxjson.toml, or an e-mail
    address in a file name: make it again with `tjs redact --force`,
    then read the whole copy before sharing it. `tjs -C inputs_redact
    redact --check` on a copy laid out as a project also reads its
    settings files, holdings/ and prior/, and exits 1 while the copy
    holds work/ or reports/ from a run of it: delete those before
    sharing.
  - The stand-alone `taxjson-apply-distributions` refuses a book holding
    several accounts' rows: run it per account, or through `tjs run`.
  - `tjs opening --force` refuses a read-only opening_*.tt (`chmod u+w`
    it first) and one that is a link leaving the project (replace the
    link with a copy).
  - A work/ gains file that holds `{}`, or whose DIVIDEND or
    DIVIDEND_IN_LIEU row lacks its amount or a date, is refused by every
    reader of work files: rebuild it with `tjs run`.
  - `tjs elect --redo` no longer clears elections of events that no
    export in the account holds, and is refused for an account with no
    corporate-action export. `tjs elect` keeps a hidden lock file
    `.manifest.json.lock` beside inputs/<account>/manifest.json (the
    project .gitignore's `.*.lock` covers it).
- **Exit statuses that changed** (a script reading the old one should
  test for nonzero):
  - `tjs sanity` exits 2 (was 1) for books it cannot read, an account
    with inputs and no gains file, a project with no gains files at all,
    and an argument it cannot use: a typo'd account, a missing file, a
    malformed ACCOUNT=FILE pair, an account or file in two groups, the
    aggregate form without both an account and a file. A script or cron
    job that read 1 as "positions differ" now sees 2 for these, and when
    none of the configured holdings files could be checked or no
    holdings/ snapshot could be compared; 1 is only a difference in
    positions. An account of taxjson.toml with holdings and no inputs is
    refused as "account 'NAME' has no inputs (inputs/NAME/ is missing):
    nothing to compare" (it read "'NAME' is not an account"; still exit
    2). No figure changes.
  - The views of the books that read work/ (`tjs shares`, `list`,
    `wash-sales`, `gains`, `positions`, `option-boundary`, `roc` /
    `roc-sum`, `divs-sum` and others) exit 2 (was 1) when a work/ file
    cannot be read, and the Error names it work/<file>. So do `tjs sum`,
    `estimate` and `plot-tax` for a work/ gains file (or the
    work/<account>_base.json the capital-gains-dividends map reads), and
    `tjs amt` and `tjs instalments` pass the estimate's 2 through. `tjs
    wash-sales` exits 2 (was 1) when work/ has no gains files, and
    `--explain` when it has no base files: run `taxjson run`.
  - `tjs audit` may now exit 1 on books that passed before: a return of
    capital, split, corporate-action row, opening, dividend (gross) or
    withholding of an audited security that differs from its parsed
    broker row fails (`pool row <id> (<ACTION> <SYMBOL> <date>): AMOUNT
    cross-check FAILED ...`). Run `tjs run` and audit again; a mismatch
    that stays after a fresh run is a bug to report.
  - A manifest.json that cannot be read exits 2 (was 1) in every command
    that reads it, and `tjs elect [ACCOUNT] --json` exits 2 (was 0). A
    flag `tjs elect` refuses (a missing `--hint` value, `--set` beside
    `--pending`, an unknown option, `--event` without `--redo` /
    `--reset`) exits 2 (was 1), its message on stderr as a usage error.
  - `tjs winners` / `losers --top 0`, `tjs edge-cases --margin -1` and
    `tjs crypto-sends --set` without `=` exit 2 (was 1).
  - sell-check, buy-check, rebuy and `wash-sales --explain` exit 2 when
    a project with a registered account has no work/sheltered_base.json
    (run `tjs run`). An unreadable ticker.map exits 2 (was 1) in
    buy-check, sell-check and rebuy; harvest and wash-radar exit 2 on a
    ticker.map with a problem line (fix the line the error names);
    `wash-radar ACCOUNT` with an account not in taxjson.toml exits 2
    (was 1) and lists the configured accounts, and so does naming a
    sheltered account (was 1).
  - `tjs check-dates`: an account whose work/<account>_sources.list is
    missing or unreadable is an ERROR, exit 1: run `tjs run` to rewrite
    it.
  - `tjs audit --year` exits 1 for a close-year lock written under the
    other country. `tjs form-export` exits 2 for a manual-reporting row
    in another currency than the return's: run it on the converted gains
    files. The stand-alone `taxjson-t1135` exits 2 for a gains file with
    short/long-term terms (a US run's): pass a Canadian run's.
  - `tjs init` with a file where the year's holdings/ folder goes exits
    1 with "cannot create 2025/holdings/: it exists and is not a
    directory" before writing anything (it exited 2 after writing part
    of the project).
- **`--json`, text and files that changed:**
  - `tjs years --json`: each year has a new key `shared_not_compared`
    (a list of {file, why}; [] when everything was compared), and
    `shared_exports_changed` can also list a generic mapping (`.toml`).
  - `tjs checklist`: the Next line and some details read differently
    (they name the fix for the finding shown, and run-clean leads with
    the .tt refusal), and work/checklist_results.json entries have a
    `do` field. `--json` `commands` for explore drop `tjs staking
    --details` in a project with no crypto account; in a filed year,
    inputs-frozen lists `tjs check-filed` instead of the download
    commands. A script that matched the old Next text needs updating.
  - `tjs sanity`'s concise output no longer ends in one "! N differ;
    ..." line: it prints one `! ` line per kind of difference, each with
    its command. `sanity --details` and its "N discrepancy(ies)." line,
    which the checklist reads, are unchanged.
  - `tjs find-missing-history`: REMOVE / STALE rows have a third column
    with the line's place (`inputs/margin/hand.tt:1`), and the headings
    read "OPENING cost=unknown entries"; a script reading the rows
    should take the first two words. `tjs align --json`, `tjs years
    --diff --json` and `tjs years --json` add `accounts_only_here` /
    `accounts_only_there` (and `differs_from_newest.accounts`).
  - `tjs align --write` no longer brings over an account table that only
    the other year has, nor comments out the table of an account only
    this year has: copy or delete such a table by hand. A taxjson.toml
    an older release wrote with `# type = "..."` commented out: restore
    taxjson.toml.bak or uncomment the line, then `tjs run`.
  - `tjs edge-cases --json`: `allowed_by` has two US values, `lot_sold`
    and `replacement_used`; each `year_boundary.option_expiries` row
    gains `traded`, `units` and `outcome` (an assigned part and an
    expiring part of one series are two rows; a split series is listed
    under its successor). `tjs renames --json` events have
    `denied_not_in_cost`. The gains files have
    `inventory_year_end_basis`.
  - `tjs form-export --json`: `manual_proceeds` counts long rows only;
    the new `manual_cover_cost` totals the short covers whose proceeds
    are unknown, and each manual row carries `direction`, `cover_cost`
    and `short_sale_proceeds` (null). In the 8949 / Schedule 3 CSV, a
    MANUAL short row has blank proceeds and its amount in the cost / ACB
    column.
  - `tjs wash-sales --json` `totals.denied` (and `permanently_denied`)
    adds the rows' cents and can differ by a cent; the old figure, which
    close-year's lock records, is the new `totals.denied_unrounded`. In
    Canada the gains JSON's `wash_window` records gain `date_settle` and
    `loss_settle`, and `days_from_loss` counts settlement dates, so a
    row that settles outside the window but traded inside it is gone and
    one traded before it but settling inside is added (no figure
    changes; an older traces file keeps the old count until the next
    run).
  - `tjs wash-radar --json`: the WASHED section's `title` differs when
    its rows are ALLOWLOSS filing positions, and the radar adds
    `recent_loss_per_unit`, `recent_loss_per_unit_qty` and `recent_buy`.
    `tjs rebuy --json` rows gain `denied_closed`, and `final` can list a
    loss whose re-entry date has passed. buy-check's text "about $2.00
    of it per unit" reads "about 2.00 CAD of it per unit" (USD in a US
    project).
  - Stand-alone `taxjson-t1135 --json`: `income` and `gain` are null
    (not 0.0) for the properties of accounts no `--gains` file covers,
    and so are the per-country totals that include them; a script that
    adds them must handle null (`tjs t1135` passes every gains file).
    New top-level keys `custodians`, `gains_not_given`,
    `foreign_issuer_symbols` and `foreign_issuer_countries`, a
    `custody_account` field on custody rows, and a flag `--custodian
    ACCOUNT=COUNTRY`.
  - `tjs slip-audit --json`: each `suggestions.tt_lines` entry gains
    `shared`, and its `file` may be `../inputs/<account>/slip-audit.tt`.
    `tjs roc-sum` counts typed `.tt` `type=roc` lines as manual, not as
    broker ROC rows.
  - `tjs option-yield --json`: each row and total gains
    `shares_other_currency`; where the shares held cost another currency
    than the options, `yield_basis` is `other_currency` and `yield` /
    `annualized` are null. An option trade coded closing (IB C) with
    nothing held is counted as unknown-cost (in `closed`, `opened` ""
    and pnl null). `tjs leaps --json` / `tjs leaps-sum --json` for an
    empty window gain `taxable_gain` and `sheltered_gain` (0.0).
  - fx-cash's text tables rename ACQUIRED / DISPOSED to UNITS IN / UNITS
    OUT, GAIN(LOSS) to GAIN(LOSS) <base> and ON DEBT to ON DEBT <base>,
    and "All amounts CAD" is gone (`--json` is unchanged). `tjs cash`
    shows an undeclared base-currency move as `#CASHMOVE <date> CAD
    <amount>` with no `?`, and so does the `tt` field of its `--json`
    rows.
  - work/to_base.csv gains a 7th column (the Bank of Canada's
    publication date) on the weekend and holiday rows the Bank fills;
    readers of 5 or 6 columns are unaffected. `tjs run` rebuilds the
    file once its newest rate is over 3 days old (delete it to see the
    change at once); the evidence then names the publication date with
    "history prior date". Rates and amounts do not change.
  - A Kraken account's `reports/<account>_holdings.toml` writes each
    coin as COIN.KR, not COIN, so a tool reading it keyed on bare coin
    names needs .KR keys (portoml already uses them). In an account
    folder holding several exchanges' exports, a coin only Kraken holds
    is written ZZC.KR, and one that only an exchange with no venue
    suffix (Coinbase) holds, or that more than one exchange holds, is
    written bare (ZZC). Coins under 1e-6 units with a cost under 1 are
    left out and listed in a `# left out as dust` comment. Run `tjs run`
    to rewrite the file.
  - `reports/<account>_holdings.toml` after a full `tjs run`: units
    moved in from another of your accounts now have `total_cost` /
    `base_total_cost`, the cost `tjs list` gives, marked `cost_source =
    "blended"`, where they read 0.0; a listing whose currency is not the
    base currency gets `base_total_cost` only. A holding whose cost the
    run does not have (an `OPENING ... cost=unknown` opening, or
    moved-in units after `tjs run --account`) carries `cost_unknown =
    true` and a comment, and omits `total_cost`, `cost_per_share`,
    `base_total_cost` and `base_cost_per_share`, which read 0.0. A
    reader such as portoml now judges moved-in units at the cost you
    file, and should treat an unknown cost as unknown, not zero
    (portoml's load_cost_basis already skips a holding with no
    `base_cost_per_share`). The gains JSON inventory gains
    `unknown_cost: true` on such positions. Run `tjs run` to rewrite the
    file; nothing filed changes.
  - `reports/<account>.sum`: sales of units moved in from another of
    your accounts move from MANUAL REPORTING REQUIRED to a "MOVED IN
    FROM YOUR OWN ACCOUNT" block, and the HOLDINGS REPORT shows
    `unknown` instead of a partial or zero cost for a holding whose cost
    the account's own books lack (an own move's units, an opening at an
    unknown cost). Filed figures do not change.
  - Windowed views outside the tax year: the `--json` `basis` value
    "pre-wash, full history" is "denials within each account, full
    history". `tjs compare --json` adds `partial_now` /
    `partial_before`. audit, journals, renames, cash, slip-audit,
    reconcile-slips and handoff print the stale-books banner when the
    inputs changed since the last clean run, and a project with one
    export saved in two accounts' folders has run-clean at attention and
    a banner on every report until the copy is removed.
  - The native-currency books: after `tjs run`, a dividend whose
    withholding TAX row is in another currency has a different
    `net_amount` in work/<account>_raw.json and _raw_base.json, and in
    the net column of `tjs divs` / `tjs events` (for example 8.00 USD
    where 7.30 was shown for 10 USD with 2.70 CAD withheld at 1.35). The
    gross, the TAX rows, the base books, the .sum and every filing
    figure are unchanged. If you copied such an events line into a .tt,
    copy it again.
  - After `tjs run`, a short covered before your data (its proceeds
    unknown) no longer gets a "superficial-loss check (manual)" /
    "wash-sale check (manual)" warning: `tjs wash-sales`' MANUAL CHECK
    count drops by one for each, and the checklist's wash-reviewed step
    may close if that was its only open item. No filing figure moves.
  - US gains JSON, `option_replacement_warnings`: `option_qty` is the
    contracts bought (whole), and `covered_shares` the shares counted
    against the loss, rounded to six decimals.
  - `tjs format` now replaces two older Canadian description lines in
    taxjson.toml (option_grant_timing_since's "SET ONCE" wording,
    corporate_distributions' README wording) with the current text
    instead of keeping them as notes: a project formatted by an older
    taxjson shows them as changed once in `tjs format --check`. Values
    are not touched.
  - `tjs audit`: run `tjs run` after upgrading, so the .tt line numbers
    (the converted files' new `metadata.source_lines`) and a corporate
    action's statements (its corp file's new `metadata.statements`) are
    there; older work/ files show neither. The `pool trades` counts can
    rise: trades under an earlier ticker and across a corporate action
    are now included, and corporate-action rows are traced instead of
    counted as having no parsed row. `--json` gains `pool_other_rows`
    (checked, tied, untraced, mismatched, rows) and
    `pool_trades.untraced_rows`, and each row with no parsed row carries
    an `origin`; the texts of `scope.source_amounts` and
    `scope.not_checked` changed, and the footer's `not traced` line
    reads "rows with no parsed row (what made each: --details) and a
    sheltered account's rows: re-run only". The stand-alone
    `taxjson-audit` takes a new optional `--dated-events PATH`
    (work/dated_events.state), which `tjs audit` passes when the file
    exists.
- **Figures a planning view shows can move** (planning figures only;
  nothing filed changes):
  - wash-radar (Canada) measures a loss sale made today from its
    settlement date, and LOCKED / EXITABLE / CAUTION clear dates are the
    first trading day whose sale settles after the buy's settlement +
    30: they can move a day or more earlier, and a position can read
    RISK or CLEAR on a Friday where it read LOCKED. US clear dates that
    fell on a weekend or holiday move to the next trading day. Two
    listings booked as one security now use both exchanges' calendars:
    around a holiday only one keeps, a VIOLATION's sell-by date can be a
    day earlier and a re-entry, safe-from or clear date a day later. In
    Canada a row names its buy by the trade date, not the settlement
    date. `tjs watch` may report the moved dates once, and "advisory
    changed" for a LOCKED, EXITABLE or CAUTION row whose buy settled
    after its trade day, where only the buy's date in the text moved.
  - buy-check's "about X of it per unit": in Canada, with more than one
    loss sale in the window, it adds each sale's loss per unit, so it is
    higher ("for the first N units" when the sales differ in size); in a
    US row that mixes a long loss with a short-cover loss, the amount
    and the wait are the long loss's.
  - `tjs harvest` with IBKR running and a QUOTE line remapping a holding
    to a listing in another currency converts IBKR's quote at its own
    currency's rate: value, unrealized and verdict can change.
  - `tjs div-estimate` forecasts change for a symbol renamed during the
    history, a dividend recorded before a split and paid after it,
    withholding posted in another currency than the payment (converted
    at the books' rate, or flagged and not estimated with no rate), a
    refunded withholding (it follows its reversed payment), and combined
    broker accounts holding a short.
  - `tjs stats` / `tjs win-rates`: a US sale with any lot of unknown
    cost no longer counts as a trade, so TRADES, the win rate and
    NET_P/L can drop; in Canada, with an assigned put whose pooled
    shares were sold (in another taxable account, or in part of a larger
    holding), the share trades' P/L gives up their part of the premium,
    so NET_P/L and wins can drop. An option trade coded closing (IB C)
    with nothing held is no longer a written-option trade. On a
    registered account with `transfers = true`, a withdrawal in kind is
    no longer a trade: trade counts, win rates and the table's P/L can
    change, and the withdrawal's P/L is on its own line ("Moved in kind,
    not trades"; `moved_in_kind` in `--json`). Such a withdrawal out of
    a position with an assigned put takes its share of the premium, and
    the later sale's result changes to match; the premium's total is
    unchanged.
  - `tjs list --date` needs a `tjs run` by this release first (it reads
    the new work/.<gains file>.timeline.json files; the first run after
    upgrading recomputes each account's gains). In Canada a symbol held
    in several taxable accounts shows each account's share of the one
    s.47 ACB, not each account's own ACB, and the "OWN ACB" note is
    gone; the `--json` `basis` text reads e.g. "wash-adjusted, as of D
    by settlement date". The row fields are unchanged.
  - `tjs tips` no longer flags TFSA-US-DIV for a payer that a ticker.map
    `T1135 SYMBOL CAN` line (or the ISIN) says is not US-domiciled; a
    payer with no domicile on record reads "if its issuer is
    US-domiciled".

### v0.37.0

- **The holdings export's cost can change for a holding journaled
  between two listings** (a TSX line to its US line, a Norbert's
  gambit). Run `tjs run`: `reports/<account>_holdings.toml` gives it the
  books' cost (`base_total_cost`) and a `total_cost` in its listing's
  currency, at the journal day's rate, where it had a mixed-currency
  or market-value figure. A plan or script that read the export's cost
  reads the new one. Nothing filed changes.
- **A sheltered account's cost can rise after a move between
  brokers.** A move with a trade of the security between its legs'
  dates, or a few weeks after a listing journal with a sale between
  them, was booked as a sale and a purchase at the rows' values; it is
  now netted. Run `tjs run`: the account's `.sum` loses the gains on
  the transfer-out (and journal) dates, and the security's cost (in the
  books and `reports/<account>_holdings.toml`) carries through the
  move. A plan or script that read the holdings export's cost reads the
  new one. Nothing filed changes (a sheltered account is not on the
  return).
- **`tjs t1135` has a CAT column.** Check each foreign property's
  category before you copy the rows to the form: a fund's or trust's units
  can be another than the 2 taxjson takes for a listed security. Set one
  with `T1135 SYMBOL COUNTRY CATEGORY` in ticker.map. Two rows can also
  appear: a holding at a zero cost, and a position of unknown cost sold
  during the year (flagged "unknown ACB"). The threshold figure does not
  change. A script reading `tjs t1135 --json` finds the same keys, plus
  `category` and `category_from` on each property.
- **`tjs t1135` no longer lists a position held only short.** A written
  option bought back or left to expire during the year, or a short sale
  covered, was a property row at 0.00 cost with its gain; it is now named
  in a `! ` line instead, and its gain leaves the per-country GAIN(LOSS)
  total. If you copied those rows to a T1135, compare. Its gain is still
  in `tjs gains`. `--json` adds `short_only_symbols`.
- **`tjs tax-logic` prints a rule's later sentences as items under its
  first.** The rules and their ids are the same; a script that reads the
  text should read `tjs tax-logic --json`, which is unchanged.
- **A crypto account gets a holdings file.** The next `tjs run` writes
  `reports/<account>_holdings.toml` for each crypto account (and copies
  it to `exports/` when `exports_dir` is set). Nothing else changes.
- **Staking commissions can be explained separately from net income.**
  `tjs staking` lists rewards; `tjs staking-sum` totals them by exchange and
  coin. Re-run `tjs run` after upgrading to preserve source reward evidence
  from Coinbase and Kraken. Existing books remain readable, with missing
  commission evidence shown as unknown. This change adds evidence and
  reports; it does not change the reward income or cost calculation.
  `tjs tax-logic --ids` now states the commission assumptions explicitly.
- **Re-run `tjs run` once.** A US project's gains files gain
  `inventory_year_end` (the lots at the end of Dec 31 of the project
  year; nothing filed changes), which `tjs edge-cases` reads for the
  losses carried into the next year: until a gains file is rebuilt it
  says "held at the end of the books, which may be after <year>". Those
  rows get `as_of` `<year>-12-31` and no longer move with later trades;
  a `tax_date = "settle"` project's say "(by trade date)".
- **US `tjs carryover` can rise in the year after a locked year.** A
  wash-sale loss whose replacement another account bought and sold in
  the locked year is now in its year. Re-run `tjs carryover` and compare
  it with the carryover entered on the next return and `[carryover]
  claimed`; the lock is unchanged. `--json` adds `excluded_unknown_cost`,
  `incomplete_years` and a row's `incomplete`, and
  `first_transaction_year` is no longer null for a crypto-only project.
- **A US lock with a crypto `deferred` above 0.** In
  `filed/<year>.json`, `year_end.crypto.<SYM>.deferred` above 0 is a
  cost the run never had. Run `tjs close-year --force` in that year to
  rewrite `year_end` (the figures and the recorded filed dispositions
  are kept); the next year's `tjs handoff` then agrees.
- **Canada: a new lock's `year_end` `deferred` can be smaller** where a
  sale before Dec 31 released part of a deferral. The `acb` is the same,
  and `tjs handoff` compares only the quantity and the cost: nothing to
  do (`close-year --force` is optional, for the label).
- **`tjs close-year --filed-dispositions` refuses a row of another
  year.** A lock an older release closed with such rows has wrong
  `filed_totals` and, in Canada, a wrong net-capital-loss carry: run
  `tjs close-year --force --filed-dispositions <that year's CSV>`. A
  plain `--force` now refuses such a lock.
- **A hand-edited lock with a malformed `return_row`** fails `tjs
  check-filed` (exit 1) and is refused by the next year's readers:
  restore it from git or close the year again. check-filed's OK line
  now says the RETURN row is not recomputed.
- **Exit statuses that changed** (a script reading the old one):
  - `tjs slip-audit` exits 1 (was 0) when something could not be
    compared: `--json` `status` is `incomplete` and `not_compared` lists
    it. The checklist's `t5-t3` step is blocked or attention where it
    was done: re-run `tjs run`, or accept an IB component taxjson does
    not know with `--done t5-t3 --force`.
  - `tjs t1135` exits 1 with an Error (was 0, with income and gain 0)
    for an account with books and no gains file; `tjs sanity` with no
    arguments exits 1 (was 0, clean and complete) for an account whose
    gains file is missing or cannot be read. Run `tjs run`.
  - `taxjson-reconcile-slips` exits 2 (was 0) for a `--gains` file in
    another currency than the country's; `tjs slip-audit` always passed
    converted files.
  - `tjs check-dates` exits 1 (was 0) when a parsed source that
    `work/<account>_sources.list` names is missing (`--json` error code
    `missing`); `tjs renames` exits 1 without books (was 0, "nothing
    pending"); `tjs splits`, `tjs spinoffs`, `tjs renames` and `tjs
    journals` exit 1 on a damaged book or `cross_listings.state` (was 0,
    nothing found). Run `tjs run`.
  - `tjs check-tt` exits 1 for two known-cost OPENING snapshots of one
    symbol on two dates (was 0, "Every line parses."): keep one.
  - `tjs checklist`: with a tobase.map line the run refuses, step
    `format` is `attention` (was `review` or `done`) and `--only format`
    exits 1; the loader's "ticker.map problem" lines no longer repeat on
    stderr.
  - `tjs buy-check`, `sell-check` and `rebuy` refuse a ticker.map or
    tobase.map with a problem line, exit 2 (they answered from the rest
    of the map). Fix the line it names; treat exit 2 as "no verdict",
    not as SAFE.
  - `tjs opening --date` with an unpadded or impossible day exits 1 and
    writes nothing (was 0, and a file the run refused).
  - `tjs align --write --json` without `--all` exits 2 (it asked the
    questions on a terminal); `--write --all --json` prints JSON, not
    prose.
  - `tjs fetch --list` beside an old per-purpose file such as
    `yf_ticker.map` exits 0 (or 2 when no fetcher is installed) instead
    of 2 with the migrate refusal.
- **`--json` that changed:** `tjs transfers` `arrival` can be
  `UNCLASSIFIED` (where a damaged work/ or an unmatched leg read "own
  move"); `tjs watch` adds `incomplete` (while a book in work/ is
  missing it compares nothing: `changes` `[]`, `tracked` and
  `actionable` null; the exit status is still 0); `wash-radar`, `watch`,
  `rebuy`, `buy-check` and `sell-check` print a document with
  `not_applicable` in a project the wash rule does not reach (they
  printed a sentence); `tjs fees` adds `unlisted_small_fees`;
  `taxjson-fees-sum` takes `--until` and its meta has `until`.
- **Figures a view shows can move** (nothing filed changes):
  - `tjs plot-tax` (Canada) credits the foreign withholding the books
    record, as `tjs estimate` does, not 15%: where it is lower the
    months and the year show more tax, and a withholding posted after
    its dividend moves its credit to that later month.
  - US `tjs stats` and `tjs win-rates` count a sale that took several
    lots as one trade: TRADES, WINS, LOSSES, the rate, the averages and
    the largest win or loss can change; NET_P/L does not. `--json`
    `denied_count` and `tainted_skipped` count sales.
  - `tjs fees` totals can rise by the fees under half a cent it left
    out.
  - A `30d` / `6w` / `3m` / `1y` / `mtd` / `ytd` window ends today: rows
    dated after today leave its totals (`all` or the year keep them).
  - US `tjs renames`: `carried.book_cost` is the FIFO basis of the lots
    carried, as the books have it (it was the average cost).
- **`tjs find-missing-history --write-missing-history`** in a project
  whose `tax_date` is not the default may now write an `OPENING ...
  cost=unknown` line it skipped (a sale traded Dec 31 that settles in
  January); after the next `tjs run` that sale moves from the totals to
  the manually reported sales.
- **`tjs redact`:** a copy an older release made from input that held
  ids already shaped like placeholders (`9990…`, `U999…`) may have
  merged or swapped two accounts. Redact again (`--force`).
- **`tjs migrate`** has the same exit statuses; a refused migrate now
  leaves the shared tobase.map as it was.
- **`tjs audit` compares more, so an audit that passed can fail.** It
  now checks each sale's quantity, proceeds, cost, dates, account, term
  and lots against the saved gains file, a base-currency sale's amount
  against the broker's row, and the audited securities' buys. Run `tjs
  run` before `tjs audit` so work/ is fresh; a FAILED line that stays
  after a fresh run is worth a bug report. Nothing filed changes. The
  footer's `fx cross-check` line is now `sale amounts`, with new `pool
  trades` and `not traced` lines. `--json`:
  - with several books, `total_gain` no longer counts the unknown-cost
    (manual) sales; they are in the new `manual_reporting` (`events`,
    `qty`, `proceeds`);
  - `failed` is true when an account was not audited (exit 1), with the
    new `not_audited` and `coverage_failures`;
  - new `pool_trades` (`checked`, `tied`, `untraced`, `mismatched`) and
    `scope`; an event's `tie_out` has `mismatched`, and a manual event's
    `tie_out.ties` is false when it differs (it was always null);
  - an event's `fx` for a base-currency row carries `nominal_net`,
    `computed_net`, `book_net`, `ties` and `priced_by_fill` (it was only
    from, to and native);
  - `--json --no-trace` events have an empty `trace`; a number that is
    not finite is written as null.

### v0.36.1

- **`tjs edge-cases` reads the denied losses held at the year end from
  the year-end pools.** Re-run `tjs run` once: a gains file written
  before says "held at the end of the books, which may be after <year>"
  until it is rebuilt. Canada's gains files gain `inventory_year_end`
  (the pools at Dec 31; nothing filed changes), and the report's JSON
  gains `as_of` on each deferred row and `allowed_by` on each loss
  window across Dec 31.

### v0.36.0

- **`tjs checklist --json` is now `schema_version` 3, and `tjs years
  --json` `schema_version` 2.** v0.35.0 renamed the `skipped` status
  and mark to `forced` (in `counts`, a step's `effective` and
  `override`, and the years' `marks`) under the old numbers. Nothing
  else changed: a script that checks the version accepts the new
  number and reads `forced` where v0.34 wrote `skipped`.
- **A `.tt` `NOTOWN` line undoes a pairing that is not a move of your
  own**. A transfer-out and a transfer-in of the same
  stock and quantity in two taxable accounts (within 10 days, or 11 to
  45 days apart with no other candidate) are still paired as one move,
  and nothing changes without the line. When they are not one move
  (`tjs transfers` shows the transfer-in as `own_move`), add `NOTOWN
  <date> <symbol> <qty> <from-account> <from-date>` to a `.tt` file in
  the receiving account's folder (the slow pairing's `Own-account move`
  Info line prints it with `tjs run --details`) and run again: the
  transfer-in is booked as one from outside your books, so the ACB or
  basis of that stock and the gains on its later sales change.
- **Check a redacted copy again before sharing it.** `tjs redact
  --check` now reads a `NAME.redacted.EXT` copy (it skipped one, exit
  0, whatever it held) and passes a clean one; in a year folder it lists
  the comments of taxjson.toml and ticker.map that still hold a name
  ("Read line N of the copy"), which it kept without a word. Run `tjs
  redact --check` on a copy made by an older release, read the lines it
  lists, and fix them before sharing.
- **`tjs check-tt` names more of the lines `tjs run` refuses, and exits
  1 on them.** A `TRANSFER` or `ACQUIRED` line in a taxable account
  that the run does not net away, a `SPLIT` ratio of 0 or below, and,
  for a file named by its path, the account's checks across lines (a
  symbol opened twice at an unknown cost, a file name the run refuses)
  used to say "Every line parses.". Run `tjs check-tt` once after
  upgrading and fix what it names; `tjs checklist --run` now marks the
  ".tt lines" step attention while such a line is left. The closing
  line reads "N problems the run refuses" (was "N lines cannot be
  read"), and a FILE argument is also looked for in the project (`-C
  DIR`) and its inputs folder.
- **`tjs new-year 2027` refuses while 2026 has no year folder.** It used
  to start 2027 from the newest earlier year (2025), pointing
  `prior_year_record` at 2025's lock, so the carryforwards skipped
  whatever 2026 used. Run `tjs new-year 2026` first, then `tjs new-year
  2027`. A year an older release started that way: check its
  `prior_year_record` (`grep prior_year_record 2027/taxjson.toml`).
- **A filed year that names the shared folders again is a `Warning:`, and
  `tjs run --strict` stops on it.** A year closed with its inputs frozen
  whose `taxjson.toml` has a live `inputs_dir` or `tobase_map` again reads
  the shared exports, not its frozen copy: it was an `Info:` followed by
  OK. Delete those lines from its `[settings]` (`tjs years` names the
  year). A lock of an earlier year kept in a later year's `filed/` is no
  longer told to delete them: that year reads the shared exports rightly.

- **A usage error exits 2 in every command.** A malformed `--date` or
  time period (`tjs events 1x`), or an account name the project does not
  have or has no books for, exited 1 in some commands (`wash-radar`,
  `list`, `events`, `trades`, `divs-sum` and the other transaction
  views, `winners`, `stats`, `leaps`, `option-yield`, `ccd-sum`, `sum`,
  `fees-sum`, `wash-sales`, `find-missing-history`, `transfers`,
  `journals`) and 2 in others. All exit 2 now, as the exit-code table
  says; exit 1 stays a command's failure or finding. A script that
  tested for 1 there tests for 2.
- **US `tjs harvest`: run `tjs run` once for LT_IN.** The US books now
  carry each position's holding period start (`holding_start_date`: a
  wash-sale replacement's includes the loss shares' days), and LT_IN
  counts from it; books written before the upgrade still count from the
  purchase. `tjs harvest --json` also lists the positions it left out,
  and why, under `omitted`.
- **`tjs div-estimate 0m` (or `0y`) is refused** as `--months 0` is:
  give a window of 1 to 120 months.
- **Canada: a move of your own between two taxable accounts now shows
  in each account** (backlog TJ-0093). `tjs list`, `tjs sanity`, `tjs
  find-missing-history` and the checklist see the receiving account hold
  the moved shares (they read it short, "missing history?", and called a
  correct sale "fix before filing"). No figure you file changes: the
  pooled ACB, Schedule 3 and the superficial-loss result are as before.
  The per-account baselines change: `<account>.sum` moves the units, so
  the sender's holdings drop and the receiver's sales of them are listed
  there for manual reporting (their cost is known only in the blended
  figures). If you wrote an `OPENING … cost=unknown` line for such a
  sale, the run now says its rows never go short any more: delete it and
  run again, and the sale joins the totals at the pool's cost (while the
  line stays, the sale stays out of them, as before).
- **An in-kind move to or from a registered account that is NOT booked
  is flagged at filing time.** `tjs sum` prints a `! ` line for it,
  `sum --json` readiness carries an `in-kind-not-booked` issue (so
  `complete` is false) and the checklist's transfers step needs
  attention. A project that had such a move (the run's "in-kind
  move(s) … NOT booked" warning) shows it after the upgrade: add the
  `INKIND` line `tjs sum --details` prints, then `tjs run`.
- **`sum --json` `run_state_problems` counts one problem per problem.**
  An inputs change ("changed: …; added: …") was two entries, and two
  `run-state` readiness issues; it is one. A reader counting them sees
  fewer.
- **Report commands in a project whose last run had no input** (every
  account's folder empty) stop with "Error: the last run had no input:
  inputs/margin is empty, so it made no books", naming each empty
  folder, instead of "Run `taxjson run` first" (the exit is 1, as
  before).
- **`tjs gains` in a project whose only books are crypto exits 0**
  after its "crypto account … is not shown" line (it exited 1 with
  "run `taxjson run` first").

- **`tjs close-year` stops on uncommitted inputs in a git repository.**
  Inputs, `taxjson.toml` or the maps changed but not committed used to
  be locked without a word; now the close asks on a terminal and,
  piped, refuses naming `inputs-committed`. Commit them, then close; a
  script that closes on purpose passes `--yes`.
- **`tjs close-year --force` no longer locks books whose last run did
  not finish** (no `reports/`): fix the run and `tjs run` first.
- **`tjs close-year` and `tjs years` show realized and disallowed as
  the RETURN row of `tjs sum` files them** (each sale's cents), a cent
  or two from the engine totals they showed; `check-filed` still
  compares the engine totals. A year closed before this release keeps
  showing its totals until it is closed again.
- **`tjs close-year` and `tjs years --json` show income as `tjs sum`'s
  taxable SUBTOTAL adds it** (each account's DIVIDEND and PIL to the
  cent), a cent or so from the lock's total they showed; a year closed
  before shows it too. The lock and what `check-filed` compares are
  unchanged.
- **`tjs carryover` (Canada) takes the project year's NET GAIN(LOSS)
  from the RETURN row of `tjs sum`** (each Schedule 3 row's cents, as
  filed), not the books' unrounded total. A cent or two moves in that
  row and, in a loss year, in the carryforward balance after it
  (CARRYFWD BAL, "Net-capital-loss carryforward after …", `--json`'s
  `carryforward_balance`, `final_carryforward`, `available_to_apply`)
  and the T1A carry-back amounts it suggests or finds (`carryback_candidates`,
  `carried_back`, `carried_back_to`, which `tjs handoff` reads). It now
  agrees with the "Unused capital losses" of `tjs estimate` and what
  `tjs close-year` carries forward, which already used the RETURN row
  (their figures do not move). An earlier year's lock closed by this
  release gives its recorded RETURN row (`return_row`) in place of the
  per-account gain lines, a cent apart where one symbol is sold from
  two accounts. `--json` rows add `net_source` and `engine_net_gain`.
- **`tjs sum` says, above the RETURN row, when the taxable SUBTOTAL
  (or TOTAL) and the RETURN row differ** ("The SUBTOTAL adds the
  account rows, RETURN each sale's cents"): one more line in the default
  view; `--json` is unchanged.
- **`tjs estimate --json` (Canada) adds `losses_carried_back` and
  `losses_carried_forward`** to `estimate`: the part of the year's loss
  `[carryover] claimed` carries back (T1A) and the rest, the two
  figures the console prints. `losses_unused` is unchanged: the figure
  before the carry-back. A script that read `losses_unused` as the
  balance carried forward reads `losses_carried_forward`.
- **A year frozen by close-year no longer calls later years' rows of
  its exports impossible** (`tjs check-dates`, its checklist step, the
  re-close gate), and no longer reports the generated
  `crypto_sends.tt` as a changed frozen input: both need only the
  upgrade, no re-close.
- **`tjs years` shows a filed year whose last run DRIFTED.** Each drift
  check (`tjs run`, `tjs check-filed`) keeps its verdict in
  `work/filed_check.state`; `years` then shows `filed … DRIFTED` with an
  `! ` line naming `tjs -C 2024 check-filed`, and `years --json` has
  `drifted`. A filed year shows it after its next `tjs run` or
  `check-filed`.
- **A filed year's settings that differ from the newest year's are its
  own.** `tjs years` marks them `(filed)` and no longer points at `years
  --diff` for them; `tjs align --write` in a filed year warns before it
  changes the taxjson.toml and ticker.map the lock was closed with.
- **A blended account's `.sum` DIAGNOSTICS names the account of a
  message about another account's file.** The blended pass's messages
  go into every blended account's `<account>_wash.sum`; one about a
  file of another account (`inputs/margin/hand.tt:1 …` in
  `reports/ib_wash.sum`) now starts `[margin] `. The DIAGNOSTICS text
  changes once: a script or diff that reads those lines sees the tag.
  The account's own `.sum` files, and messages about the whole blend,
  are unchanged.
- **A filed year whose lock was deleted stays frozen.** While its
  taxjson.toml still carries close-year's freeze note, a year with no
  `filed/<year>.json` is no longer taken as never closed: `tjs years`
  shows `lock missing` ("restore it or run close-year again"; `--json`
  `filed` true with a `problem`), `tjs check-filed` exits 1 and `tjs
  run` warns, the checklist's lock steps are blocked, and `tjs migrate`
  leaves the year alone. Restore the lock (`git checkout --
  2024/filed/2024.json`, or a backup) or close the year again.
  `new-year` from a frozen year whose lock is missing or damaged now
  reads the shared folders; a year an older release started that way
  carries the freeze note copied from it: delete the note and set
  `inputs_dir` (and `tobase_map`) under its `[settings]`.
- **The checklist's year-end steps.** `lock-committed` needs attention
  when `.gitignore` hides the reports as filed (`filed/<year>/reports/`,
  an older scaffold's unanchored `reports/`): anchor the line as
  `/reports/` and commit them; outside any repository it names `git
  init`. `inputs-frozen` is done once the year is
  filed (a late export is an amendment), and `next-year` in a
  single-folder project names `tjs migrate --to-years`, then `new-year`.
- **`tjs winners` / `tjs losers`: a DENIED column.** When a row shown
  has a superficial-loss (US: wash-sale) denial, the table has DENIED
  before GAIN, so PROCEEDS − COST + DENIED is the GAIN; `--json` rows
  carry `denied` (COST is unchanged). A script that reads the text
  table's columns by position reads one more on such a table.
- **`tjs win-rates SYMBOL --json`:** `denied_total`, `denied_count` and
  `tainted_skipped` cover the symbols named, no longer every ticker.
- **`tjs stats` / `tjs win-rates` after `tjs run`:** a loss denied on
  an option whose replacement was exercised now counts once (the
  shares' sale recovers it), and an s.86.1 spin-off splits the
  recovered loss between the parent and the new shares as it splits
  the ACB. Their NET_P/L can move; no filed figure does.
- **`tjs audit` with a corp-action election pending:** the taxable
  accounts blended with the deferred one are not audited either (they
  have no cross-account pass to tie to) and the warning names them;
  the exit code is 1, as before. Decide the election, then `tjs run`.
- **A purchase line drafted from a transfer stated at 0 needs your
  cost.** `tjs find-missing-history --write-purchases` used to draft a
  transfer-in whose row states a book value of 0 (`NO_COST_(0_stated)`
  in `tjs transfers`) at a cost of 0.00; filled in with only its date,
  its sale was filed at a $0 cost. Now the line reads `COST  COST`. If
  you kept such a line in a `.tt` file, check its cost: unless your cost
  really was nil, put in the real one and `tjs run`.
- **Drafts you kept from IB's Basis may hold an extra line.** When the
  files bought shares between two sales coded closing, the drafts had a
  line for each sale, more than were missing. `tjs list` (or `tjs
  sanity` against the broker's holdings) shows the extra units; remove
  the line for the sale of shares the files bought.
- **A transfer-in only partly covered by `.tt` purchases is drafted**:
  the line is for the other units, at `COST` with a CHECK note.
- **`--write-missing-history` and `--write-purchases` refuse books
  older than the inputs** ("the books in work/ are older than the
  inputs ... nothing was written"): `tjs run` first. `tjs
  find-missing-history`, `tjs sanity` and `tjs transfers` open with the
  "not the clean result of the current inputs" warning then.
- **A written option booked at grant (`option_grant_timing_since`) with
  no purchase in the files** is no longer "NOT in the totals": it is
  listed under `no_purchase_in_totals` (`booked`: `short_cover`) in
  `tjs sum --json`, and `readiness` can be complete. The figures do not
  change.
- **`tjs transfers`' TYPE column** shows the kind of transfer (IB's
  `ACATS`, a crypto exchange's `withdrawal`, else `transfer-in` /
  `transfer-out`), not the broker's description; `--json`'s `type` is
  unchanged. Its `! ` lines are shorter: "N transfer-in(s) with no cost
  (…)" and "N transfer-in(s) only partly covered, NOT booked (…)", the
  fix in `--details`.
- **The checklist's sanity step needs attention when no account has a
  holdings file** and some account holds positions (it was `[m]`, to
  confirm, so `--done sanity` closed it with nothing compared). Save the
  broker's positions snapshots in `holdings/`, or accept the step
  unchecked with `tjs checklist --done sanity --force`. A project whose
  books hold no position keeps the confirm step.
- **`tjs sanity` with no holdings file:** `--json` prints a JSON document
  (`complete: false`, `uncovered_accounts`), not the text; `UNCHECKED`
  counts every account with open positions, registered ones included
  (it counted the taxable accounts with books). A script that read the
  text, or the "with books" wording, reads the new form.
- **`tjs sanity --json` has `accounts_not_checked`** (the accounts whose
  `holdings = [...]` file is missing or unreadable), and `complete` stays
  true when the only note is about a `holdings/` snapshot of an account
  compared with its own `holdings = [...]` files.
- **`tjs sanity` refuses books that are `{}` or a bare list** (`work/<account>_base.json`),
  as it refuses books that are not JSON: re-run `tjs run`.
- **Two downloads pasted into one export file are refused when they
  overlap.** A Questrade, Webull or generic-importer file whose header
  (Webull: the statement's Account Number preamble) repeats, and whose
  two parts share rows, booked the shared rows twice; the run now stops
  with "line N: the header repeats here — two downloads pasted into one
  file". Save each download as its own file in the account's folder:
  overlapping rows of two files are read once. Parts that share no row
  are read as before. Then `tjs run`: the doubled rows leave the books,
  so a filed year that read such a file changes.
- **Two Coinbase or Kraken downloads that disagree on one row are
  refused.** When overlapping downloads of one account carried a row id
  with different content (a restated price or amount), the run kept the
  version of the file whose name sorts first, with no message; it now
  stops with "row id … is in both with DIFFERENT content". Keep the
  newer download only (or correct the row) and `tjs run`.
- **A large export runs in seconds, not minutes.** Canada's
  superficial-loss check walked every row of the books for every loss
  sale (8,000 rows of one symbol: about 2.5 minutes; 50,000 rows: half an
  hour). It now reads each security's rows by date window; no figure
  changes, so a filed year does not drift. Nothing to do but `tjs run`.
- **`tjs amt` / `tjs estimate`: the Ontario share of a minimum-tax
  carryover recovery is smaller, and the surtax it saves is shown
  apart.** "Recovered: ON share" used to be the whole drop in Ontario
  tax; it is now the share deducted from basic Ontario tax (the figure
  for the provincial form's carryover line), with the surtax it saves on
  its own row. The total recovered and the tax do not change. A script
  reading `--json` finds the two parts in `recovered_provincial_credit`
  and `recovered_provincial_surtax` (`recovered_provincial` is still the
  total), and a new lock records `recovered_provincial_credit`.
- **`tjs check-filed` exits 1 on a lock whose `fx_cash_v2` close is
  damaged** (it said OK; the next year's FX-on-cash ledger refuses
  it). Restore the lock from git, or re-close that year.
- **`tjs estimate`'s INSTALMENTS line reads "=> instalments behind"**
  (it said "=> balance due April 30" for the instalments still
  owing). A script that matched the old wording needs the new one.
- **`tjs estimate` in a loss year carries forward only what is not
  carried back** when `[carryover] claimed` records a T1A carry-back
  ("Unused capital losses: … (carry forward; … carried back:
  [carryover] claimed, T1A)"), as close-year does; `--json`'s
  `losses_unused` is unchanged.
- **`tjs carryover`: a closed year with no `[carryover] claimed` entry
  shows APPLIED as what its lock applied** (it showed 0.00), and no
  longer offers that gain again.
- **FX on cash, ledger v2: a USDC reward priced in CAD.** A USDC (or
  other USD stablecoin) reward that Coinbase prices in CAD now brings
  its US dollars in at the income's CAD value. If you added a
  `CASHMOVE` line for it to clear the gap, remove the line (else the
  reward counts twice and the year is NOT COMPUTED), then re-run `tjs
  fx-cash --ledger v2`.
- **FX on cash, ledger v2: moves between your own accounts.** A chain
  of moves on one day (a to b to c) no longer makes up a debt of the
  middle account, and an own move the receiving broker books up to 7
  days before the sending one computes instead of being refused.
  Re-run `tjs fx-cash --ledger v2` before filing its figure: one
  computed with a BORROWED row for such a chain changes.
- **`close-year` with ledger v2 records a book that ends the year at
  zero.** A record written by an older release does not list it: the
  next year still asks for its `CASHOPEN <date> <cur> 0 0` line once
  (or re-close the prior year with `tjs close-year --force` there).
- **`tjs sanity` exits 0 for an s.86.1 spin-off's fraction awaiting
  its cash in lieu** on the snapshot's date (a `fraction` line, and
  `fractions_awaiting_cash_in_lieu` in `--json`), where it reported
  `QTY_MISMATCH` and exit 1.
- **`tjs spinoffs --json`** adds `fmv_currency` (the currency of
  `fmv_per_share`) and `cash_in_lieu`; its `ratio` counts a fraction
  paid in cash.
- **A `.tt` line ends at a line feed only, for every reader of it.** A
  form feed, a vertical tab or a Unicode line separator (U+2028, U+2029,
  U+0085) inside a line, often pasted into a comment, made the run's
  readers of `RENAME`, `JOURNAL`, `ALLOWLOSS`, `INKIND`, the cash lines
  and `OPENING ... cost=unknown` read the text after it as a line of its
  own and book it. Now it stays part of the comment. A project whose
  `.tt` files hold one (`grep -nP '[\x0b\x0c\x{85}\x{2028}\x{2029}]'
  inputs/*/*.tt`) can see its positions, ACB, a superficial-loss denial
  or the FX gain on cash change on the next `tjs run`; put a line you
  meant on a line of its own.
- **`tjs wash-radar --verbose` prints the full report**, as `--details`
  does (every advisory, the definitions, the scope). It used to change
  nothing; a script that passed it now gets the long form.
- **The wash radar's advisories print money as the tables do**
  (thousands separators, the base currency after it: `250.00 CAD`, not
  `$250.00`), in `wash-radar`, `watch`,
  `buy-check`, `sell-check` and the `--json` `advisory` text. The first
  `tjs watch` after the upgrade reports each such advisory as changed,
  once.
- **Re-entry dates are trading days.** `tjs rebuy`'s REBUY ON, the radar's
  COOLING / BLOCKED CLEARS and `tjs buy-check`'s safe-from date are the
  first trading day a buy no longer replaces the loss. In Canada that is
  the first day whose buy settles after the window, up to two days
  earlier than before (never a weekend); in the US a weekend date moves
  to the Monday. From that day the loss no longer shows BLOCKED or
  COOLING. The first `tjs watch` after the upgrade reports the moved
  dates, once.
- **Canada: a buy counts from its trade date in the planning views.**
  `wash-radar`, `rebuy`, `buy-check` and `sell-check` treat a buy made
  today as acquired and held before it settles, so right after a rebuy
  the loss reads VIOLATION (or the position LOCKED) at once, not a day
  or two later.
- **A file you made read-only is no longer rewritten.** `tjs format
  --write`, `format-map --write`, `migrate`, `init --force`, `ticker-map --suggest
  --write` and the other commands that rewrite taxjson.toml, ticker.map,
  tobase.map or slips.toml now refuse one you cannot write ("cannot
  write taxjson.toml: the file is read-only"); before, they replaced it
  and put the read-only mode back. To let them rewrite it, `chmod u+w`
  the file the message names.
- **`tjs fetch --list` works in any folder again.** Since v0.32.0 it
  was refused outside a project ("not a taxjson project"), so the
  installer printed an `Error:` and a blank `taxjson-fetch installed:`
  line. It reads no project; `tjs fetch` itself still needs one.

### v0.35.0

- **`tjs checklist --skip` is gone: `tjs checklist --done ID --force`.**
  A script or a habit that marked a step `--skip ID` now writes `--done
  ID --force`; a plain `--done ID` is refused while the last check flags
  the step. The `skipped` marks already in a project's `checklist.json`
  need nothing: they read as forced done marks (`[f]`, `--json`
  `effective: "forced"`); a script that read `skipped` from `--json`
  (or from `tjs years --json` `marks`) reads `forced`.
- **`tjs fetch` (IBKR Flex) works again.** Every release since the
  multi-account guard was added refused a normal single-account Flex
  download as "contains 3 account statements" (it counted the
  statement's metadata rows). After upgrading, run the fetch again;
  nothing in a project needs changing.
- **A Coinbase or Kraken trades export with one ID on two different rows
  is refused.** The second trade used to be dropped from the books with
  no message; now `tjs run` stops on the file and names both lines (an
  identical repeat is still read once). Delete the row that is a stray
  copy, or give one of two real trades a distinct ID, and re-run: a
  trade that was missing is booked, and the coin's units, cost and gains
  change.
- **`tjs fx-cash --ledger v2` refuses a prior-year close that carries a
  cost below zero, or a cost on no units** (2026-10-25 audit,
  year-end#2): figures a `CASHOPEN` line is refused for and `close-year`
  never writes. A year that computed on such a record (a damaged or
  hand-edited `filed/<year-1>.json`) is now NOT COMPUTED with a `carry`
  problem naming the book: restore the record from git, or re-close that
  year with `fx_cash_ledger = "v2"`.
- **A damaged prior-year lock, or one closed under the other country's
  law, now stops `tjs carryover`, `tjs estimate` and `tjs amt`**
  (2026-10-25 audit year-end#1, robustness#1, year-end#3, year-end#6).
  Before, the carryover ledger dropped a damaged lock with a warning
  and printed the carryforward without that year (0.00 when the books
  do not rebuild it), took the other country's lock as the filed figure
  0.00, and counted a loss twice when a lock's `year` named another
  year; the estimate and `amt` read the other country's lock as
  carrying nothing, and a minimum-tax year written twice in a lock kept
  its second amount. If one of these now stops naming a lock: restore
  it from git, re-close that year in its project, or point
  `[settings] prior_year_record` at this country's lock; then re-run
  and compare the carryforward and the estimate with the last run's.
  `tjs check-filed` and `tjs years` in the closed year now name the
  same lock damaged.
- **Transfer-ins into a taxable account are reconciled more strictly**
  (2026-10-24 audit, B03 / B04 / B40 / B41 / B42 / B50): a transfer-out
  cancels a transfer-in of the same security within 10 days (a slower
  move of your own: the next item; a departure years after an arrival
  never), a listing journal with no pair id and no ticker.map rule only
  between two listings of one root, `.tt` purchases for part of an
  arrival's units book nothing (`PARTLY_covered`, said every run) and a
  stated book value of 0 is no cost. After the upgrade, `tjs run` and
  compare `tjs transfers` with the last run's: an arrival now booked
  that was `own_move` before came from outside your books (or needs a
  `TOBASE` line joining two tickers), unless its line names a
  transfer-out of yours that could be its other end (the next item); a
  `PARTLY_covered` row needs the other units' purchase as a `.tt` line;
  a pre-split `.tt` purchase now covers the post-split units that
  arrived once its `SPLIT` line is in the `.tt` file.
- **A move of your own between two taxable accounts whose legs post 11
  to 45 days apart is one move** (2026-10-25 audit diff-engine#4): a
  transfer-in of the same stock and quantity as a transfer-out of
  another of your taxable accounts 11 to 45 days before it, neither with
  another candidate, books nothing (the shares keep their cost; a US
  project carries the sending account's lots), said as an `Own-account
  move` Info line with both dates. A build with the 10-day window alone
  booked such an arrival at the broker's book value, blending the pooled
  cost, and kept the shares in the sending account: `tjs run` again, and
  the cost and gain of their sale are the purchase's. Legs that are
  ambiguous, partly paired, in one account or further apart stay an
  arrival and a departure: the arrival's ATTENTION line names a
  departure nothing paired up to 45 days before it, and `tjs sanity`
  shows the shares held twice if they are one move.
- **US project: a `[[distributions]]` basis adjustment reaches every lot
  settled by the record date.** The row carries the record date and the
  US engine spreads it over the open lots settled by that date only; it
  is dated before a straddling SALE only, no longer before a straddling
  buy, which had put the whole amount on the lots bought before that buy.
  Re-run `tjs run`: a lot bought after a purchase that straddled a
  record date, and sold since, changes its basis and gain. Canada's pool
  is unchanged. When a trade between a straddling sale and the record
  date keeps the row from reaching exactly the record date's lots, the
  run says so (`warning: ATTENTION: [[distributions]] … misses the lots
  of the trade(s) in between`): check those lots, or enter per-lot
  `.tt` ADJUST rows.
- **An in-kind move of an option is valued and priced at the contract
  size its transfer row declares** (`multiplier`: an adjusted series'
  50, a mini's 10; else a ticker.map `MULT` line, else 100). A move of
  such an option valued from a Yahoo close was worth 100 units per
  contract: re-run `tjs run` and compare `tjs transfers` (the move's
  value and the booked row's price change; a value from an `INKIND` line
  or the broker's market value is unchanged).
- **Canada: `tjs t1135` gives a lower cost amount for shares an assigned
  put delivered, where the delivery also covered a short** (audit D02,
  `CA-RPT-12`). The shares kept were costed at their gross price; their
  share of the put's premium now lowers it, as it lowers their ACB in
  `tjs run`. Re-run `tjs t1135` and compare the maximum and year-end
  cost with a T1135 already filed: both are filing figures.
- **`tjs stats` / `tjs win-rates` count a denied superficial loss (US: a
  disallowed wash-sale loss) once.** The replacement's sale counted it
  again through the amount the rule added to its cost (s.53(1)(f) /
  §1091(d)); it now counts without it, so after a denied loss whose
  replacement was sold the views show more wins and a higher NET_P/L.
  Re-run `tjs run`: the gains files then carry each sale's
  `deferred_loss_recovered`, which the views read (a gains file written
  before the upgrade has none and keeps the old figure). No filing figure
  changes.
- **A declared in-kind move delivered in parts is booked with the plan's
  legs only when that set is the only one** among every candidate leg
  within the window — not the eight nearest. A move booked before by
  such a match may now be listed as "possibly in-kind in parts — NOT
  booked" with the legs: it is the `INKIND` line's value that books the
  taxable side; name the plan's legs with a line per part if the plan
  received them that way.
- **`tjs sanity` refuses a holdings TOML whose `[meta] holdings_count`
  is not its number of `[[holding]]` tables, or whose holding declares a
  `contract_multiplier` (or `multiplier`) that is 0 or negative.**
  It stops with an Error naming the file (exit 2) and compares nothing;
  `tjs run` ends with "holdings check could not run". Download the
  snapshot again, or fix the hand-made file.
- **`tjs sanity` refuses files of one account at different dates that
  do not each name their broker account** (two `holdings/` snapshots
  named only `margin_…` or `[meta] account = "margin"`): they were
  summed. Add `broker_account = "…"` to each file's `[meta]` (one broker
  account's snapshots are then compared one date at a time), or keep one
  snapshot per broker account.

### v0.34.0

- **`tjs checklist` no longer runs the checks: `tjs checklist --run`
  does.** A plain `tjs checklist` shows the verdicts of the last `--run`
  (kept in `work/checklist_results.json`) with when they were made and
  what changed since; until the first `--run` it lists the steps
  unchecked. A script that read `tjs checklist` or `tjs checklist
  --json` for fresh verdicts adds `--run`. `-j N` runs N slow checks at
  a time (default: the CPUs, at most 4).
- **`tjs checklist` prints the whole list before the summary.** The
  default view was the summary alone; the summary (the counts table,
  the `! ` lines, the `Next` line last) is unchanged but now follows
  the list. A script that read the default text and expected the table
  first reads it after the list; `--json` is unchanged.

### v0.33.0

- **Python 3.10 or newer is required** (was 3.9). An install on Python
  3.9 stays on its release; the installer upgrades it once a newer
  Python (3.13 down to 3.10) is first on `PATH`: macOS `brew install
  python`, Debian/Ubuntu `sudo apt-get install -y python3 python3-venv`.
  Why: the fixes to the vulnerabilities reported against the dependency
  locks (urllib3, requests, cryptography, pip, setuptools, anyio,
  soupsieve) all dropped Python 3.9, which reached end of life in
  October 2025.

### v0.32.0

- **RBC export without a `Symbol Description` column, with a name change
  booked under a temporary code: re-run `tjs run`.** The old ticker is
  now found from the buy's Description, so the rename is booked and the
  sale under the new ticker is back in the totals. A `GLOBAL <old>.TO
  <new>.TO` line you added for it can stay.
- **RBC export without a `Symbol Description` column, with an s.86.1
  spin-off: set the election again.** The parent is now found from the
  trade's Description, so the event id names its ticker
  (`…-zzp-zzs-…` instead of `…-x123456-zzs-…`): `tjs elect --pending`.
- **A closed year whose loss you carried back (`[carryover] claimed`
  under an earlier year): `tjs close-year --force` in it.** Its lock
  carried the whole loss forward; the re-close records the balance after
  the carry-back, which the next year's estimate reads.
- **A closed year locked with `--filed-dispositions` whose filed gain
  differs from the books, or a closed loss year that carried a loss back
  after an earlier loss year did: `tjs close-year --force` in it.** Its
  lock carried forward a net capital loss that was too large.
- **A filed year where you ran `tjs align --from <newer year> --write`:
  delete the live `inputs_dir` and `tobase_map` lines from its
  `[settings]`** (the commented ones under `## frozen by` stay), and if
  `tjs migrate` removed its `tobase.map`, rename `tobase.map.bak` back.
  An older `align` wrote them, so the year read the shared exports, not
  its frozen copy; its run now says so.
- **A year you locked before it ended (`tjs years` marks it `filed*`)
  with v0.31.0 or v0.31.1: `tjs close-year --force` in it, then `tjs
  run`.** That close froze a copy of the exports as they were then, so
  later trades never reached the year. The re-close thaws it (the year
  reads the shared exports again; a copy you edited is named and kept)
  and stops; the run then shows the later trades as drift.

### v0.31.1

- **Re-run `tjs run` for a year with an s.86.1 spin-off paid cash for a
  fraction (Questrade or RBC).** The fraction is now sold for the cash on
  its share of the allocated cost (`CA-CORP-05`): its gain and the new
  shares' ACB change slightly.
- **ALLOWLOSS quantities are read strictly.** A quantity with a decimal
  comma (`1,5`) was read as 15 units; it is now refused with the line
  named. Write `1.5`.
- **`[carryover] claimed`: one year spelled two ways stops the run.**
  `2023` and `02023` in one table are one tax year; keep one line.
- **Holdings TOML dates must be real days.** An `as_of`, `generated_at`
  or `acquired` such as `2025-02-30` is now refused, naming the field.
- **FX-on-cash ledger v2: two broker accounts whose hashed ids start
  alike get longer book names.** An `at=` naming the old 4-character book
  is refused, and the message lists the new names; point it at one of
  them. Books without such a clash keep their names.
- **`.gitignore`: anchor the `reports/` line.** The file `taxjson init`
  wrote before this release ignores `reports/` at every depth, which
  also hides `filed/<year>/reports/`, the reports as filed that
  `close-year` keeps since v0.30.4+. In a project you keep in git,
  change `reports/` to `/reports/` (and, in the folder holding the year
  folders, add `/*/reports/`); `close-year` says so when the line is
  still there. New projects get the anchored file.
- **`tjs compare` exits 1 when an account figure moved,** even when every report file matches (it exited 0). A script reading the exit code sees a difference there.
- **An ALLOWLOSS line dated the settlement day of only some fills of a sale now names none,** and the run stops on it: date the line with the sale's trade date.
- **Refused now, named by line:** an IB dividends report with a blank GrossInBase beside a Gross, an impossible date or an amount like `1,5` (download it again); a CRA slip page whose box figure is not an amount (print it again or type the slip into slips.toml); an RBC Holdings Export whose cost column is neither a total nor per share (rename the column).
- **US projects with futures from a generic CSV with a settle column:** run `tjs run` again; a gain can change, up or down, where settle dates ran out of trade order: a partial close now takes the contract traded first (`US-FUT-01`).

### v0.30.1

- **v0.30.0 can no longer be installed; v0.30.1 is the same program.**
  An install that follows a channel (`stable`, `beta`, `latest`)
  upgrades as usual by re-running the installer. One pinned to `v0.30.0`
  (`--channel v0.30.0`, `TAXJSON_CHANNEL=v0.30.0`): pin `v0.30.1`, or
  use `--channel stable`. A clone of the repository keeps working: the
  history did not change.

### v0.30.0

- **Releases v0.15.0 to v0.29.2 were withdrawn.** The public history
  was replaced by a single starting commit, and those releases and their
  tags are gone. An install that follows a channel (`stable`, `beta`,
  `latest`) upgrades as usual by re-running the installer. One pinned to
  a withdrawn version (`--channel v0.28.4`, `TAXJSON_CHANNEL=v0.29.0`)
  can no longer re-install it: re-run the installer with
  `--channel stable` (or pin `v0.30.0`). A clone of the repository made
  before 2026-10-12 shares no history with the new one: clone it again.
- **Two engine fixes can move a filed year.** A superficial loss whose
  replacement shares came into the loss's symbol by a later rename or
  merger (Canada), and a split reported twice around a per-share
  distribution's record date, now give the correct gain; a filed year
  with either shows `filed <year> DRIFTED` on the next run
  ([troubleshooting.md](troubleshooting.md)).
- **`tjs splits` no longer fails on a split reported twice.** Two
  reports of the same split within a week are marked `TWO-SOURCES`
  (applied once, on the earlier date) and exit 0; they were marked
  `TWICE?` with exit 1, though every engine already applied them once.
  `TWICE?` and exit 1 remain for the same ratio 8 to 10 days apart.
- **Blank broker ids (Coinbase, Kraken).** Two identical rows in one
  export whose ID (Coinbase) or `txid` (Kraken) cell is blank are now
  two trades or ledger events, as in an export with no ID column. A
  row pasted into a file twice by hand is then counted twice: remove
  the copy.

### v0.29.1

- **Cached prices are fetched again once.** `harvest` and the other
  planning views keep each cached quote with the Yahoo spelling it was
  fetched under, and an entry an earlier release wrote has none: the
  first run after the upgrade fetches every price again, so an offline
  run (`TAXJSON_OFFLINE=1`) stops until you have run once online. The
  same goes for a cached close of an LSE, JSE or Tel Aviv line used to
  value an in-kind move (troubleshooting.md).
- **An opening snapshot can stop the run.** An `OPENING` line dated
  inside the tax year that would leave out a return of capital of that
  year which may go beyond the cost (a gain of that year) now stops the
  run, naming the row, as a left-out sale already did. Date the
  snapshot before that row, or drop it and give the full history.
- **Canada, futures in two taxable accounts.** The same contract held
  in more than one taxable account is now averaged across them, so a
  close can realize a different gain than before; a filed year with
  such contracts shows `filed <year> DRIFTED` on the next run. Futures
  in one account are unchanged.

### v0.29.0

- **`tjs winners` shows the winners only.** Its text view listed the
  best N and the worst N; it now lists the winners alone (gains above
  zero, best first), and `tjs losers` lists the losers (worst first).
  The total under the table is the shown side's (`WINNERS TOTAL`), with
  the year's `TOTAL REALIZED GAIN` over every ticker on the line below.
  `tjs winners --json` is unchanged; it adds the side's own totals
  (`side_*` keys). A script that read the losers from the text of
  `winners` reads `tjs losers` (or `--json`) instead.

### v0.28.2

- **Same-moment trades of two accounts follow the accounts' names.**
  Rows of two accounts at the same date and time used to follow the
  order the accounts are listed in taxjson.toml; reordering the file
  changed gains without the books being called stale. They now go in
  the accounts' name order, whatever the file's order. A sale with a
  same-moment trade of the same security in another account can move:
  a filed year then shows `filed <year> DRIFTED vs <year>.json` on the
  next `tjs run` ([troubleshooting.md](troubleshooting.md)).
- **Chained commands:** a command name after a command that can take it
  as its own argument is now that argument when it names one of the
  project's accounts (`tjs events sum` with an `[accounts.sum]`), a folder
  (`tjs init sum`) or a symbol (`tjs audit sum`); it chained before. To
  chain after such a command, write `--` between the two: `tjs events --
  sum`. `tjs run sum`, `tjs fetch run` and every chain whose word is no
  account of the project work as before. The value of `-C` is always the
  project folder, even one named like a command (`tjs -C run run sum`).

### v0.28.0

- **Shorter command output.** A command's default output is the
  essentials: a legend before each table, then only the lines to act on
  (starting `! `) or not to miss, one line each, naming the command with
  the detail. Every explanation, caveat and citation is still there
  behind `--details` (`tjs sum --details`, `tjs run --details`);
  `tjs checklist` shows the steps that need attention and the next one
  (`tjs checklist --all` is the full list); `tjs estimate` shows the
  estimate alone (`--details` puts the gains table back in front);
  `tjs years` is a table (`--details` prints each year in words).
  `--json`, the `reports/` files and the run's `work/*.diag` are
  unchanged. A script that read a note from the text output: read
  `--json`, or add `--details`. [output-style.md](output-style.md)
  describes the format, and [troubleshooting.md](troubleshooting.md)
  quotes the one-line forms.
- **`tjs close-year` asks first** when a checklist step before the lock
  needs attention: it lists them and asks on a terminal; without a
  terminal (a script, a pipe) it refuses unless you pass `--yes`.
- **`tjs init` in an empty folder named like a year refuses** (it would
  have built `2026/2026/`): run it in the folder above —
  `mkdir -p ~/taxes && cd ~/taxes && tjs init --country canada && cd 2026`.
  `tjs init --single` still makes one folder where you are.
- **`option_grant_timing_since` (Canada):** a new `taxjson.toml` leaves
  it commented out unless another year folder beside it sets it, and
  `tjs run` warns only when the books hold a written option. A new
  warning says when it is later than `year` ("option_grant_timing_since
  = 2026 is after year = 2025"): usually `year` was lowered with the key
  left as an older `tjs init` wrote it — set it to the first year you
  file under grant timing ([troubleshooting.md](troubleshooting.md)).
- **The help page's Maintainer group.** The release commands
  (`channels`, `deploy`, `promote`) are listed only on a development
  checkout; `tjs help --all` lists them everywhere, and they still run.
- **The docs moved.** README.md is a front page; its reference sections
  moved to their own pages: [commands.md](commands.md) (every command),
  [brokers.md](brokers.md) (getting each broker's files, the generic
  importer, the fetch plugin, slips), [settings.md](settings.md) (project
  files and mapping reference), [tax-rules.md](tax-rules.md) (the rules),
  [limits.md](limits.md) (what taxjson does not do),
  [glossary.md](glossary.md) and this page. KNOWN_ISSUES.md now lists open
  bugs only. A bookmark to a README section lands on the front page; the
  section is in the page above.
- New, nothing to do: `tjs init --demo ~/taxjson-demo` makes a project
  of made-up exports to try first
  ([getting-started.md](getting-started.md#1-install)).

### v0.27.1

- **One shared `tobase.map` (Canada, one folder of exports for every
  year).** The interlisted pairs file sits beside the year folders, and
  each year reads it through `[settings] tobase_map = "../tobase.map"`.
  A project made by v0.27.0 has a copy in each year folder: run
  `tjs migrate` in the folder that holds the years. Identical copies
  become one at once (each kept as `tobase.map.bak`); copies that differ
  are listed with your own lines only an older copy has, and need
  `tjs migrate --write` (the newest year's file is kept). A year folder
  with the setting and a tobase.map of its own is refused, naming both.
- tobase.map no longer carries `DISTINCT` lines for Canadian depositary
  receipts (CDRs): `tjs update-tobase-map --write` retracts the ones
  v0.27.0 wrote. Nothing in the books changes.

### v0.27.0

- **Missing history is a `.tt` line; `missing_history.json` is no longer
  read.** Units held before your files start, at an unknown cost, are
  `OPENING <date> <SYMBOL> <qty> cost=unknown [reason="..."]` lines in the
  account's inputs (`inputs/<account>/missing_history.tt`). A project that
  still has a `missing_history.json` (or the older `phantoms.json`) is
  refused by every command until you run `tjs migrate` (`--dry-run`
  first): each entry becomes a line sized as that project's last run
  opened it; with one folder of exports for every year the year folders'
  files are merged (entries the years disagree on are listed and written
  only with `tjs migrate --write`), and each file is renamed
  `missing_history.json.migrated`.
- `tjs find-missing-history --write-missing-history` no longer takes a
  file name; it writes those lines into `missing_history.tt`. The stage
  tools' `--incomplete-history` now names the project folder.
- Canada: a new project gets a `tobase.map` (the interlisted pairs that
  ship with taxjson); an older project is offered it by
  `tjs update-tobase-map`, which shows which pairs would change your books
  before it writes anything.

### v0.26.0

- **`tjs init DIR` makes one folder of exports for every year.** It
  creates `DIR/inputs/<account>/` (every year's broker exports) and the
  year's project in `DIR/<year>/`. Run commands in the year folder:
  `tjs init ~/taxes && tjs -C ~/taxes run` becomes
  `tjs -C ~/taxes/2025 run`. `tjs init --single DIR` keeps the one-folder
  layout of earlier releases. Existing projects are unchanged;
  `tjs migrate --to-years` converts one (`--dry-run` first).
- In the folder that holds the year folders, a command that needs one
  year's project is refused, naming the year folders (`cd 2025`, or
  `tjs -C 2025 ...`).
- The fetch plugin's `--positions` snapshot moved from `work/` to the
  year's `holdings/` folder, where `tjs sanity` finds it. A fetcher
  plugin of your own: downloads go to `FetchRequest.inputs`
  ([CONTRIBUTING.md](../CONTRIBUTING.md#adding-a-broker-fetcher-taxjson-fetch)).

### v0.25.0

- **Two commands were removed.** The first-run guide command is replaced
  by `tjs checklist` (every step from install to filing), and the old
  map-and-holdings scanning command by `tjs tips` (advice for next year)
  and `tjs ticker-map --suggest` (map lines to add or delete). The
  v0.25.0 section of the CHANGELOG names both. Scripts that called them
  need the new commands.
- `tjs checklist --json` is `schema_version` 2: a step's `stage` is now
  `section` ([settings.md](settings.md#taxjson-checklist---json)).
- `tjs tips` exits 0 with or without tips (2 when the project cannot be
  read), and its `--json` is `schema_version` 2.

### v0.19.0

- The installer installs the taxjson-fetch plugin by default. An install
  that should not have it: re-run the installer with `_ --without-fetch`
  (remembered for later upgrades).

### v0.17.0

- **One mapping file; year data in `taxjson.toml`.** The old per-purpose
  files — `yf_ticker.map`, `crypto_ticker.map`,
  `ticker_extraction_overrides.txt`, `t1135.map` (now `QUOTE`, `CRYPTO`,
  `EXTRACT` and `T1135` lines in `ticker.map`), `claimed_losses.txt`,
  `amt_carryover.txt`, `capital_gains_dividends.map`, `distributions.map`
  (now `[carryover] claimed`, `[estimate] amt_carryover`,
  `[[capital_gains_dividends]]` and `[[distributions]]` in `taxjson.toml`)
  — stop every command until `tjs migrate` converts them (`--dry-run`
  first); each old file is renamed `<name>.migrated`.
- The local web UI (the `serve` command, the `[web]` extra) and the
  watchlist exports (`taxjson-export --seekingalpha`, `--fastgraph`,
  `--tradingview`, the `reports/exports/` files) were removed. A
  `TRADINGVIEW` line in ticker.map is ignored with a note: delete it.
- `taxjson fetch` moved into the separate taxjson-fetch plugin (installed
  by default since v0.19.0).
- `taxjson` with no command prints the help page (exit 0, was 2).

### v0.15.0

- The Qt desktop app (the `gui` command, the `[gui]` extra) was removed.
  Every report it showed is a command.

## Problems an upgrade fixes

A message you see on an older release may be a bug a later release fixed.
Search [troubleshooting.md](troubleshooting.md) for it: every known
problem is there, and an entry whose **Fixed in** is newer than
`tjs --version` is fixed by upgrading. The list below is the older form
of that record and stops at v0.28.0: check `tjs --version` against it;
if the fix is newer, upgrade first.

#### Fixed in v0.28.0
- The first run of a recent project downloads exchange rates back to 2000 ("Info: FX USD→CAD: Bank of Canada Valet for 2,400 dates, Yahoo fallback for …") — fixed in v0.28.0: the window starts a few days before the earliest date in your files
- "Warning: the same broker account (#ab12cd) feeds two taxjson accounts, qt and wb" for the exports of two different brokers that print the same account number — fixed in v0.28.0
- `tjs tips --online` advises "Add `DISTINCT QZD.US QZD.TO` to ticker.map to record this and silence the pair" for a Canadian depositary receipt (look-alike listings are never joined; a `DISTINCT` line already written is harmless) — fixed in v0.28.0

#### Fixed in v0.27.1
- "Info: 1 position(s) go short in margin's data (QZQ.TO): booked as short sales closed by a later purchase" although `missing_history.tt` opens the other listing of a TOBASE pair (QZQ.US) — fixed in v0.27.1
- "warning: inputs/margin/missing_history.tt:1 opens QZQ.TO / margin, but no row in the data has that symbol and account — nothing was applied" for a holding you simply kept (no later trade of it) — fixed in v0.27.1
- `tjs slip-audit --import-cra` hangs on a PDF instead of stopping with "pdftotext took over 60 s" — fixed in v0.27.1

#### Fixed in v0.27.0
- `tjs ticker-map --suggest` lists a pair to verify such as `TOBASE QZOR.US QZOR.TO` for a US share you hold (never add that line: it pools two companies) — fixed in v0.27.0
- `tjs t1135` lists a TSX-held `BEP.UN` (booked as `BEP.US`) under the USA, or warns "`T1135 BEP.UN.TO` matches no symbol in the books" — fixed in v0.27.0; then run `tjs update-tobase-map --write`
- `tjs update-tobase-map --write` adds back a master line you deleted, or duplicates a line you edited — fixed in v0.27.0
- `tjs update-tobase-map` lists no pair for a TSX class share or trust unit (`QZK.B.TO`, `QZR.UN.TO`) that trades over the counter in the US — fixed in v0.27.0; then run `tjs update-tobase-map --write`
- `tjs sanity`: "INCOME ON SHARES THE BOOKS DO NOT HOLD" for a dividend paid after you sold (its record date was before the sale) — fixed in v0.27.0
- `tjs sanity`: "MISSING_IN_HOLDINGS" for a position whose shares bought before your data were sold, although missing history covers them — fixed in v0.27.0

#### Fixed in v0.26.1
- With `TAXJSON_OFFLINE=1` in a US project: "taxjson-to-base-curr needs the [fx] extra for a USD target (Yahoo Finance)", then "Error: stopped at fetching currency rates (CAD, USD) (exit 1)" — fixed in v0.26.1

#### Fixed in v0.26.0
- `tjs check-dates` in a year folder: "out-of-range: … — far outside the project year 2024" for every row of a later year in the shared exports — fixed in v0.26.0

#### Fixed in v0.25.0
- `tjs run` overwrote a file outside the project that a symlink in `work/` (such as `work/loss_overrides.json`) pointed at — fixed in v0.25.0; on an older release, remove the symlinks from `work/`
- IB: "Warning: QZK.US: the broker cancelled (Ca) a trade of 6 @ 10 on …, but the original fill is in none of this account's inputs" after "Info: the broker cancelled (Ca) 4 of the QZK.US order of 10 @ 10 … The order is booked as 6" (an order cancelled in two parts booked a sale that never happened) — fixed in v0.25.0
- IB: "Warning: QZK.US: the broker cancelled (Ca) a trade of 440 @ 10 on …, but the original fill is in none of this account's inputs" after "… cancelled (Ca) 40 of the QZK.US order of 440 @ 10" (a part cancelled, then the whole order) — fixed in v0.25.0
- `tjs fetch`: an empty `questrade_2025.csv` (or `ib_flex.csv`, or the Questrade token file), "FileNotFoundError: … questrade_2025.csv.part", or "Questrade auth failed" right after another fetch succeeded, when two fetches ran at once — fixed in v0.25.0
- `tjs fetch --trim-overlap` copied the original CSV outside the project, to where an `<export>.bak` symlink pointed — fixed in v0.25.0
- `tjs slip-audit --import-cra`: a Webull T5 "from CI INVESTMENT SERVICES INC./CI SERVICES D'INVESTISSEMENT INC" is not imported: "no broker in the books by the issuer's name" — fixed in v0.25.0

#### Fixed in v0.24.2
- "Warning: 1 position at a $0 cost (1 still held): SPNW.TO (margin). Run `taxjson find-missing-history`" for a spin-off you elected with `fmv_per_share=0` (now an Info: the $0 value you declared) — fixed in v0.24.2
- "Warning: ATTENTION: 99900001.csv: RBC symbol QZOLD (USD) looks renamed to QZNEW" (or the Questrade or Webull hint) while a `.tt` line `RENAME <date> QZOLD.US QZNEW.US` already declares the change — fixed in v0.24.2
- `run --fast` keeps the old result after an update that changed only taxjson's market data (no "Rebuilding everything: taxjson's code changed" line) — fixed in v0.24.2; on an older release, run once without `--fast` after updating
- `tjs elect` says "No elections recorded: lira" although the run booked a spin-off there ("sheltered account lira: spin-off … booked at $0 cost") — fixed in v0.24.2
- `tjs form-export` (or `tjs audit`) shows an ALLOWLOSS sale as an ordinary loss: no note, "disallowed 0.00", and `tjs wash-sales --explain` says "no matching gains found" — fixed in v0.24.2
- `tjs slip-audit`: a T5 typed for an IB account and IB's dividends report for the same account counted twice ("Canadian dividends … slip" twice the books) — fixed in v0.24.2
- `taxjson-form-export --csv s3.csv`: "cannot write --csv s3.csv: [Errno 17] File exists: 's3.csv.part'" — fixed in v0.24.2; on an older release, delete the leftover `.part` file once no export is running

#### Fixed in v0.24.1
- Questrade: "Info: Questrade internal symbol codes resolved (1): D0000001 → QZD.US (name match: …)" for a spun-off warrant, then "Warning: Questrade spinoff chain on … is booked under Questrade's INTERNAL code D0000001.US, not a ticker" — fixed in v0.24.1
- No "possible superficial loss across listings" warning for a pair whose own broker names both listings alike, because another account's export names one listing with other share wording ("SUBORD VTG SHS") — fixed in v0.24.1
- `tjs ticker-map --suggest` lists "QZE.US and QZE.TO share their letters but the names are not equal … — verify" for two companies that IB lists under one bare symbol — fixed in v0.24.1
- `tjs ticker-map --suggest` asks to verify a depositary receipt (CDR) or another company that uses the same letters ("if they are one security add `TOBASE QZX.US QZX.TO` …; if not, `DISTINCT QZX.US QZX.TO`"), or `tjs tips` says "US-LISTING … hold QZX.TO instead" — fixed in v0.24.1
- A broker journal split over several rows (one reference: 1000 out of QZD.TO, 600 and 400 into QZD.U.TO) is not joined, or its sale reads as missing history — fixed in v0.24.1
- A correct `TOBASE QZB.US QZA.TO` for a move from IB to Questrade left "Info: 1 position(s) go short in acct's data (QZA.US)" (in a registered account "Warning: Short position: QZA.US …"), or a QZA.US position the broker never held — fixed in v0.24.1
- Questrade or RBC: "Warning: Row check: … BUYSELL QZPIPE.US …: net_amount -44.98 is far from qty*price = 62.90 …" on a dividend reinvestment priced in the other currency (`REINV@C$…` on a USD row) — fixed in v0.24.1
- Questrade: "Warning: Short position: QZP.TO (lira): a registered account (TFSA/RRSP) cannot be short" after a dividend reinvestment (REI) on a USD row — fixed in v0.24.1

#### Fixed in v0.24.0
- "Info: 1 position(s) go short in margin's data (QZN.TO) … Until the purchase is supplied their gain is in no total" for a short sale closed within the year, or `tjs find-missing-history` counting its cover as a sale (InYrSales 2) — fixed in v0.24.0
- A transfer-in from outside your books lost its cost (no "booked at the ACB the broker states on the row" line), or an in-kind move was booked, after a journal between two listings in another account — fixed in v0.24.0
- "Info: 1 position(s) go short in b's data (QZD.U.TO)" after a Norbert's gambit in one account while another account received the same listing by transfer that day — fixed in v0.24.0
- `tjs sanity`: "QZD.U.TO MISSING_IN_TAXJSON" (or `QTY_MISMATCH` on QZD.TO) for a listing the run joined by its transfer journal — fixed in v0.24.0
- A ticker that changed twice (`RENAME` A to B, then B to C) leaves the position in B, and C goes short — fixed in v0.24.0
- "Error: SPLIT QZA.TO→QZB.TO: rename would merge a LONG position into an existing SHORT pool" with a `.tt` `RENAME … late=fold` dated before the broker's own rename row — fixed in v0.24.0
- Canada: a sale of the old ticker on the day of an evening rename row (IB's 20:25 corporate actions, a timed `.tt` SPLIT) goes short although `late=fold` is declared — fixed in v0.24.0
- A SPLIT QZA→QZB and a `RENAME` QZB→QZC on the same date: "RENAME 2025-04-01 QZB.TO QZC.TO books nothing" and QZC goes short — fixed in v0.24.0
- "RENAME 2025-04-01 QZA.TO QZB.TO books nothing" when ticker.map respells the rows (`GLOBAL QZAX.TO QZA.TO`) — fixed in v0.24.0
- A `.tt` RENAME in a securities account moved a coin in a crypto account (or the reverse) — fixed in v0.24.0
- Questrade with `transfers = true`: a currency journal (Norbert's gambit) not joined and the USD sale short, or "joined as one security by their transfer journal: QZD.TO ↔ QZD.U.TO (transfer …)" for it — fixed in v0.24.0
- `run --fast` keeps an IB ticker change dated from another account's IB statement after that statement is removed — fixed in v0.24.0; on an older release, run without `--fast`
- `tjs ticker-map --suggest`: "… not joined automatically: another name of the listings states another share or corporate form (…)" for a broker's own journal (RBC Norbert's gambit, IB InterDepot) — fixed in v0.24.0
- `tjs ticker-map --suggest`: "… not joined automatically: the legs pair with more than one other leg" (two equal gambits days apart) or "… a listing pairs with two other listings" for a broker's own journals — fixed in v0.24.0
- A move between brokers that changes the listing (RBC `TFO` out of QZP.TO, IB `ATON` into QZP.US) is not joined when a weekend falls between the two legs — fixed in v0.24.0

#### Fixed in v0.23.1
- "Error: cannot detect broker for inputs/qt/99900001.csv" (or "Questrade export is missing required column(s) 'Transaction Date'") for a file whose header looks right: it was saved with two byte-order marks — fixed in v0.23.1; on an older release, re-export the file or save it as plain UTF-8

#### Fixed in v0.23.0
- A traceback ending "AttributeError: 'NoneType' object has no attribute 'write'" after `tjs sum >&-`, or `tjs run --no-input 2>&-` stops with exit 1 and no output — fixed in v0.23.0; on an older release, redirect to `/dev/null` instead of closing the stream
- An account number inside a security name, e.g. a `tjs ticker-map --suggest` line or a join warning naming 'QZX CORP TFR TO …' — fixed in v0.23.0
