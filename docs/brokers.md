# Getting your files from each broker

Which file to download from each broker, where it goes, and what
taxjson checks in it: the detailed companion to step 3 of
[getting-started.md](getting-started.md#3-download-your-broker-files).
Menu paths come from the brokers' public help pages, cited under each
broker; brokers rename menus often, so look for the report by its name.

## The short version

- **Download all the history the broker gives you**, not just the tax
  year. The cost of a share sold this year comes from the day you
  bought it, which may be years back.
- **CSV, not Excel.** `tjs run` reads only `.csv` and `.tt` files and
  stops on a spreadsheet in an inputs folder. Convert one with
  `taxjson-xlsx-to-csv FILE.xlsx -o FILE.csv` (it needs the optional
  `xlsx` extra and prints the install line if it is missing), then move
  the spreadsheet out of `inputs/`.
- **Any file name works.** taxjson recognises each export by its header
  ([How a file's broker is detected](#how-a-files-broker-is-detected)).
  Several files per account are fine: overlapping rows are read once.
  Keep each download in a file of its own: two overlapping downloads
  pasted into one file are refused.
- **Don't edit the files.** Re-saving a CSV in a spreadsheet can drop
  columns or change dates and the encoding.
- **Keep two positions reports with book cost** for each account (the
  broker may call it "Holdings", "Positions" or "Portfolio"):
  1. **for the day your history starts.** `tjs opening ACCOUNT FILE`
     turns it into an opening balance, `inputs/<account>/opening_DATE.tt`
     (getting-started step 5);
  2. **for the end of the tax year.** `tjs sanity` checks the books
     against it (step 6).

  taxjson reads two brokers' reports as they are: an Interactive
  Brokers Activity Statement (its Open Positions section) and an RBC
  Holdings Export. For any other broker, type the positions into a
  `[[holding]]` TOML file ([settings.md](settings.md#holdings-toml)).

### Where the files go

`tjs init` makes one folder of exports shared by every year, with a
project per tax year beside it:

```
~/taxes/
  inputs/<account>/     every year's broker exports (and your .tt files)
  2025/                 the 2025 project: taxjson.toml, ticker.map, ...
    holdings/           2025's year-end positions snapshots
    inputs/slips/       2025's tax slips
```

- **Activity exports** go in `inputs/<account>/`, all years together
  (`<account>` is the `[accounts.<name>]` section of `taxjson.toml`;
  each folder's `README.txt` lists what to download). One broker
  account, one folder: the same exports in two folders are booked twice
  (the run warns).
- **Sheltered accounts too.** An RRSP's or TFSA's purchases count for
  the superficial-loss (US: wash-sale) rule on your taxable accounts.
- **Year-end positions** go in the year's `holdings/` as a
  `[[holding]]` TOML (found by its `[meta] account` or a name like
  `margin_holdings.toml`), or as the broker's own report (an RBC
  Holdings Export or an IB statement, named like `margin_holdings.csv`).
  A report can also go anywhere: name it in the account's
  `holdings = [...]` setting or pass it to `tjs sanity`
  (`tjs sanity margin=FILE`). `tjs run` skips a positions
  report left in an inputs folder.
- **Tax slips** go in the year's own `inputs/slips/` ([Tax
  slips](#tax-slips)).

## Interactive Brokers

**Download:** the **Activity Statement**, format **CSV**.

**Where:** Client Portal, **Performance & Reports › Statements** (or
**Menu › Reporting › Statements**). Click the Run arrow beside the
Activity statement and choose:

- **Period:** **Annually** (one file per year is simplest) or
  **Custom Date Range**.
- **Multi-Account Format:** download each IB account on its own (below).
- **Advanced Options:** language English (the Cash Report check reads
  English numbers).
- **Format:** **CSV**.

**How far back:** IB keeps annual and monthly statements for the five
previous years, daily and custom-range ones for the four previous
calendar years; older ones cost a fee. For purchases before that, use
an opening balance (`tjs opening`) or statements you kept.

**What taxjson checks:**

- **Only the Activity Statement is activity.** A Realized Summary,
  confirmation, performance or tax report is refused, naming its title.
- **The Cash Report.** The money parsed (dividends, payments in lieu,
  withholding, interest, fees, commissions, trades) is compared per
  currency with IB's own Cash Report, within 0.02. A mismatch stops the
  parse (a row dropped, doubled or mis-signed, usually in an edited
  file): download the statement again. Without a Cash Report (a
  customised statement, a Flex query that leaves it out) the check is
  off, and the run says so with an `ATTENTION` line.
- **Coverage.** taxjson joins the Periods of one IB account's statements
  and warns about days none covers, or a last statement ending before
  December 31. Overlapping statements are de-duplicated.
- **Late dividends.** A dividend IB has not yet posted is only an
  accrual (not income), and the run warns: download the statement again
  once it has posted.
- **A dividend or return of capital and the listing held.** The row
  names its security by ISIN, and taxjson books it on the listing the
  statement's dated rows show held that day (its trades, transfers and
  the Open Positions section). A statement with no position in the
  ticker (the income exported apart from the trades) takes the listings
  from the account's other statements, as names: they carry no dates
  there. With two listings named, the row keeps the listing its ISIN
  names and a note says the account may have held the other one. A
  return of capital is never moved off its ISIN listing onto a listing
  the statement's own rows show was not held that day. Wherever a
  return of capital ends on a listing no dated row of its statement
  shows held, while the account's exports name another listing of the
  ticker, `tjs run` (with `--account` too) lists an `ATTENTION`
  (`Undated listing`): the payment and the listing it may belong to,
  then what the books did with it (the cost it reduced, the part booked
  as a capital gain); `tjs run --strict` stops on it and the closing
  list names it. What to do: download a statement that holds the
  account's **Open Positions** section (the **Annually** statement
  does), or every trade of the ticker from before the holding's
  purchase (which may be in an earlier year's statement: keep that one
  too), and keep it in place of the split exports; or, when the two
  listings are one security, add the `TOBASE` line the message names to
  ticker.map. A return of capital is moved onto a listing only another
  statement names when that statement's rows of it could predate the
  payment (its first row on or before it, or neither a coded opening
  purchase nor a transfer in after it; or its Open Positions hold more
  than its rows add); another broker account's statement in the folder
  does not count. A row moved that way that the books then find
  nothing under (a capital gain on a pool empty that day) is listed
  too, with the ISIN's listing as where it may belong.
- **An ISIN that names no market.** A country with a home market in
  taxjson's table (`CA`, `US`, `GB`, `AU`) gives the row its first
  listing. Any other ISIN country (`IE`, `LU`, `DE` ...) does not say
  where the security trades: an Irish fund lists in London, Amsterdam
  or Frankfurt, an Irish company in New York. Such a row is booked only
  on evidence of its listing, in this order: the listing the account
  holds (as above); a ticker.map line that names the symbol the parse
  looked for (`ZZQ.L` for an Irish ISIN, `ZZQ.US` for the others); the
  one listing the account holds the ticker under when its suffix is a
  currency's code (a line IB trades in euros is `ZZQ.EUR`), for
  dividends, payments in lieu and withholding (the run says so once a
  security, "ZZQ income booked on ZZQ.EUR ... by its name alone", and
  names the line that confirms it, `GLOBAL ZZQ.L ZZQ.EUR`, after which
  it is your declaration and not said again; nothing is said where the
  statement's instrument list gives the ticker the row's ISIN); the one
  venue the statement's Financial Instrument
  Information gives the ISIN (its Listing Exch, when taxjson knows it).
  Otherwise the parse stops and says why: the listings the account
  holds and that the statement does not show which the rows belong to
  (a return of capital is never moved onto a holding no dated row
  shows held that day), the venues the instrument list names when
  there are two, or that nothing names a listing. It names the line to
  add: any ticker.map line naming the symbol it looked for
  (`QUOTE ZZQ.L ZZQ.L` for a London line), or `GLOBAL ZZQ.L ZZQ.US` to
  the listing held. A ticker.map line that names another listing of
  the ticker (a `DELETE`, `DISTINCT`, `QUOTE` or `T1135` line written
  for another security) is no evidence, and neither is a `.tt` line (an
  `OPENING` of `ZZQ.L` in the account's `.tt` file): the parse reads
  the statements and ticker.map only.
- **Two distributions on one pay date.** Nothing on a Dividends row
  names the accrual it pays. Where a security has two Change in
  Dividend Accruals rows of one pay date (a regular and a special
  dividend, an ex date each; or two pay dates as near the row's date),
  each row's share count, rate and ex date come from the accrual the
  statement's evidence gives it: the rate the row states, its cash
  against the accrual's shares and rate (with the shares lent), and the
  rows' characters. Where the cash fits more than one way, a way with
  whole share counts, then one with the same shares lent under each
  accrual (or the accruals read as one distribution accrued in parts),
  then one that gives more accruals rows of their own, is taken; where
  a reading set aside is in whole shares too and gives a row another
  share count or rate, the run says so once a security ("the shares
  and rates booked are one of 2 readings"), with both readings and the
  fix. A match that still stays open stops the parse, naming the rows (a
  reversal and its re-post beside a second dividend, none stating a
  rate, is one); writing each row's per-share rate into its Description
  as IB prints it (`USD 0.10 per Share`, in the row's own currency)
  settles it. Two accruals of the same shares and rate give the same
  figures either way: the rows are booked, and an ex date the evidence
  does not settle is left out, said by the run ("no ex date recorded
  for 2 dividend rows"). No booked amount depends on the match.
- **One payment in two exports.** Overlapping statements that hold the
  same dividend or return of capital book it once. Where one statement
  placed it by its dated rows and the other from undated evidence, the
  dated listing is kept and an `ATTENTION` names both rows; where both
  placed it with equal standing on different listings, the run stops,
  naming both files and lines: replace both with one statement of the
  account that holds the trades behind both placings (its Open
  Positions section, or every trade of the ticker from before the
  purchases).
- **Several accounts in one file** get an `ATTENTION` line: every row is
  booked to the taxjson account whose folder holds the file. If both
  are yours and taxable together, set `combined_broker_accounts = true`
  ([settings.md](settings.md#combined_broker_accounts)); an RRSP or
  TFSA goes into its own folder.
- **Futures** need the Financial Instrument Information section (the
  contract size), which a full Activity Statement has.

**Positions report:** the same Activity Statement. `tjs sanity` and
`tjs opening` read its **Open Positions** section (quantity and cost
basis on the period's last day), so the year's statement downloaded
after December 31 is also its year-end positions.

**IBKR's dividends report** (`U*.YYYY.dividends.csv`) is not activity:
it goes in `inputs/slips/` ([Tax slips](#tax-slips)); the run stops on
one in an account folder.

Parser: `src/taxjson/lib/brokerages/ib_extractor.py` — `_reconcile_cash_report`, `_check_statement_kind`, `_warn_coverage_gaps`, `_reattribute_income_to_holdings`, `_ib_cross_income_listings`; the run's flag: `src/taxjson/pipeline/stages.py` — `_flag_undated_listings`.
Troubleshooting: [Interactive Brokers](troubleshooting.md#interactive-brokers).

Sources: [How to Run a Statement](https://ibkrguides.com/clientportal/performanceandstatements/runstatement.htm) (menu path, period, language, format);
[Statements](https://www.ibkrguides.com/clientportal/performanceandstatements/statements.htm) (archive policy). Neither page states a maximum length for a custom
date range.

## Questrade

**Download:** the account's **transaction history** (account
activity), every year available.

**Where:** sign in, **Reports** in the top navigation, **Go to
transaction history**, pick the date range and export. The help page
says the export is an **Excel spreadsheet**: convert it with
`taxjson-xlsx-to-csv` (the parser reads the converted dates). The
converter needs the optional `xlsx` extra, which the plain installer
line leaves out: `TAXJSON_EXTRAS=fx,xlsx bash -c "$(curl -fsSL
https://taxjson.com/install.sh)"`. It writes each cell as the sheet
shows it (a label given twice stays twice, an account number formatted
with leading zeros keeps them); `--sheet NAME` picks a sheet by its
name first (a year tab `2025`), else by its index from 0.

**How far back:** the help page does not say. Set the start before
your first deposit; download one file per year if a long range is
refused.

**What taxjson checks:**

- **All 14 columns:** Transaction Date, Settlement Date, Action, Symbol,
  Description, Quantity, Price, Gross Amount, Commission, Net Amount,
  Currency, Account #, Activity Type, Account Type. A missing or renamed
  column stops detection, naming the columns that are missing.
- **One account per file, or one tax entity.** An export that holds
  rows of two Questrade accounts gets a warning. One that mixes a
  registered plan with a taxable account is refused: export each
  account on its own. In a US project a TFSA, RESP, RDSP or FHSA is a
  taxable account (no treaty deferral), so only an RRSP, RRIF or
  locked-in plan is refused there.
- **Coverage.** Questrade's export does not state its end date, so the
  last row is taken as the end. If an account still holds positions and
  has no row for more than 30 days of the year, the run warns that the
  export may end early.
- **Row order.** The web export lists rows newest first. Rows of one
  day carry no time, so taxjson reads a newest-first file bottom-up:
  same-day rows (a sale and its rebuy, two currency conversions) keep
  the order they happened in, in the books and in the FX-on-cash
  ledger v2 alike. A file whose dates are mixed keeps its own order,
  with a note. Every row keeps the clock its Transaction Date gives
  (midnight in the web export): a stock dividend, a cash in lieu, a
  reinvestment and an interest row as a trade does, so shares a row
  adds are there for a sale, a journal or a transfer listed after it
  on its day. A cash in lieu's two legs (the fraction received, then
  sold) share the row's clock, in that order. A reinvestment follows
  the cash that pays it: a dividend of the same security, or an
  interest credit, at its own clock is ordered before it even where
  the export lists the REI row first. A return of capital is placed at
  09:30:00, as every importer places one; a reinvestment it pays that
  has no clock of its own (the export's midnight) is placed there too,
  after it. A reinvestment with a real clock keeps it, and no credit
  with a later clock moves it.
- **Internal symbol codes** (`X000123`) on some dividend, spin-off and
  journal rows, and currency journals (Norbert's gambit): see
  [troubleshooting.md](troubleshooting.md#questrade-rbc-direct-and-webull).

**Positions report:** taxjson knows no Questrade positions export. Type
the positions into a `[[holding]]` TOML, or let the fetch plugin
snapshot them (`tjs fetch --positions`, below).

**Auto-fetch:** Questrade activity can be downloaded through its API
instead: see [Auto-fetch](#auto-fetch-questrade-interactive-brokers).

Parser: `src/taxjson/lib/brokerages/questrade.py` — `_QT_COLUMNS`, `missing_columns`, `_row_clock`.

Sources: [Checking your transaction history](https://forward.questrade.com/learning/questrade-basics/track-your-account-activity/checking-your-account-activity) (menu path, Excel export; no limit stated).

## RBC Direct Investing

**Download:** the account's **activity export** (transaction history),
CSV, every year available. Its first line reads `Activity Export as of
<date>`, then a header with `Date`, `Activity`, `Symbol`,
`Settlement Date`, ... .

**Where:** no public RBC help page confirms the menu: look for the
account's activity or transaction history page and its export link.

**What taxjson checks:**

- **The "as of" date.** An export holds only what was posted when it
  was taken. If an account's latest export predates December 31 of a
  finished year, the run warns: export the year again after January 31.
- **The June cutoff.** RBC posts the year's December 31 book-cost
  adjustments (notional distributions, a year-end return of capital)
  the next spring, dated December 31, so neither an earlier export nor
  one starting January 1 holds them. Re-export the tax year after the
  account's `year_end_posting` day (default `"06-30"`;
  [settings.md](settings.md#year_end_posting)); until then the run adds
  a note. Keep both files: overlapping RBC downloads are de-duplicated.
- **Strict reading.** Every number must parse and each date column has
  one format, so a re-saved file is refused rather than misread.
- **A trade's direction** is its Activity (Buy, Sell). Under an
  Activity taxjson does not know, a row whose Description has the word
  Buy or Sell is a trade in that direction (`NAME - Sell` is a sale,
  with the Quantity signed or not). A Description with both words, a
  purchase with a negative Quantity, and a Buy or Sell row whose
  Description ends with the other verb (`Sell` beside `NAME - Buy`)
  are refused, naming the line. So is a sale whose Value (the cash
  before commission) is negative, and a row read as a purchase from
  its Description whose Value is positive: a sale's Value is cash in,
  a purchase's cash out. The net Amount is not read for this: a sale
  whose commission is larger than its proceeds still books. A Buy row
  whose cash is written positive is the purchase, by its magnitude. In
  an export with one money column a Sell row is read as before; a row
  read as a sale from its Description that pays is a commission larger
  than its proceeds only while it pays less than Quantity x Price.
- **Notional distributions** raise the ACB; their income is on the
  fund's T3, not in the export.

**Positions report:** the **Holdings Export** (CSV, `Holdings Export
as of <date>`), with book cost, read as is by `tjs sanity` and
`tjs opening` (columns matched by label: Symbol, Quantity, Currency,
book cost or book value as the position's total; a per-share label
such as Average Cost or Book Cost Per Share is multiplied by the
quantity; any other cost label is refused, naming it; a currency the
cost label names, `Book Value (USD)` or `(usd)`, is the cost's). A
label it reads given twice, or a row with a quantity and no symbol
(not cash, not a total), is refused, naming it.

Parser: `src/taxjson/lib/brokerages/rbc_direct.py` — `REQUIRED_COLUMNS`, `rbc_coverage_messages`, `DEFAULT_YEAR_END_POSTING`, `rbc_trade_side`; positions: `src/taxjson/lib/positions_reports.py` — `rbc_holdings`.

Sources: none found (no public RBC Direct Investing help page for these exports).

## Webull

**Download:** the **Trading Summary**, CSV, one per tax year, every
year available. Webull Canada issues it with the T5008 in late
February.

**Where:** in the app, tap the Webull logo, choose the account, then
**Documents**. Webull's help pages do not say where the Trading Summary
sits there or how to get it as CSV.

**What taxjson checks:**

- **Two layouts.** The 2024 Trading Summary has 9 columns, the 2025 one
  10 (Proceeds moved over). Columns are found by header label (Currency,
  Date, Action Code, Symbol, Security Description, Quantity, Price,
  Proceeds), never by position; a file mixing the two, or an unknown
  layout, is refused.
- **Type Code.** `OPC` and `OPP` are options, `SHS`, `STK` and `EQ`
  shares. A trade row's code is checked against its Security
  Description: an option code with no readable contract (`CALL/PUT
  ROOT MM/DD/YY STRIKE`) and a share code beside a contract are
  refused, and so is any other code, naming the line and the code. A
  blank code is read by the description alone.
- **BUY and SELL rows only, no income.** Any other action code (a
  dividend, a transfer) is an `UNBOOKED` warning. Enter the T5's
  dividends and interest as `.tt` lines in the account's folder:

  ```
  DIVIDEND 2025-03-15 16:00:00 SAMPLE.US 50 USD 0.24 12.00
  INTEREST 2025-12-31 16:00:00 USD 4.10
  ```

  (the `.tt` format: [settings.md](settings.md#tt-files)).
- **Exercise and assignment** have no code: an option closed at $0 is
  an expiry, unless a stock trade at the strike carries the account's
  `exercise_fee` (your broker's charge, e.g. `exercise_fee = 1.00` under
  `[accounts.<name>]`; [settings.md](settings.md#exercise_fee)).
  Without it nothing is inferred: each candidate pair is named in the
  account's `.sum` for you to check.
- **Dates are settlement dates.** Keep every year's file in one folder:
  a put assigned December 31 closes in one year's file and its stock
  leg settles in the next.
- **Commission** is the gap between Proceeds and quantity x price; a
  gap that is not commission-sized is refused (a shifted column).

**Positions report:** none taxjson knows. Type the positions into a
`[[holding]]` TOML.

Parser: `src/taxjson/lib/brokerages/webull.py` — `_HEADER_LABELS`, `_TYPE_KINDS`, `_mark_assignments`.

Sources: [How do I get my monthly statement and trade confirmation?](https://www.webull.ca/help/faq/358-How-do-I-get-my-monthly-statement-and-trade-confirmation) (the Documents menu);
[Which tax documentation can I expect to receive?](https://www.webull.ca/help/faq/591-Which-tax-documentation-can-I-expect-to-receive) (T5008 and Trading Summary, late February).

## Wealthsimple

**Unverified.** taxjson reads Wealthsimple's activities export with a
layout it ships, no mapping file of yours needed. The layout was
written from the export's documented columns and has not been checked
against a real file yet. It says so itself (`verified = false`), so
`tjs run` warns once for each account that reads it, again at the end
of the run, and `tjs parser-check` and `taxjson-detect-brokerage` mark
it too. Compare the account's totals (trades, income, cash) with your
statements; if they differ, report it with the export's header row and
made-up rows of the same shape, never real ones
([Something not recognised?](#something-not-recognised)). The warning
goes once a real export has checked the layout.

**Download:** the activities export, as CSV, for all the history the
account has.

**What taxjson checks:**

- **Found by its header.** A CSV with `activity_type`,
  `activity_sub_type` and `net_cash_amount` columns is read with the
  layout, under any file name. A layout of your own that fits the file
  wins, and a project or user layout named `wealthsimple.toml` replaces
  the shipped one ([settings.md](settings.md#generic-importer-mapping), `[layout]`).
- **What it books:** a `Trade` row's `BUY` or `SELL` (its
  `activity_sub_type`; the same word on another activity type is not a
  trade), `Dividend` and `Interest`. A `MoneyMovement` deposit or
  withdrawal (sub-type `EFT`, `E_TRANSFER`, `DEPOSIT`, `WITHDRAWAL` or
  `CONTRIBUTION`) books nothing and is listed as a cash move; one
  carrying shares (a transfer in kind: enter it as a `.tt` TRANSFER
  line) is an `UNBOOKED` warning, as is a `MoneyMovement` of another
  sub-type and any other activity type that moves cash or shares. The
  warning names where an edited copy of the layout goes: the layout is
  `data/layouts/wealthsimple.toml` inside the installed taxjson package
  (`src/taxjson/data/layouts/` in its source).
- **The generic importer's checks** apply: the amount must be quantity
  x price ± the commission, and a sign must agree with its action
  ([settings.md](settings.md#generic-importer-mapping)).
- **The plan type.** The `account_type` column (TFSA, RRSP,
  Non-registered) is checked against the taxjson account's `type`, as a
  Questrade export's Account Type is: a TFSA's rows in a `type =
  "taxable"` account, or a non-registered account's in a `type =
  "sheltered"` one, is an ATTENTION line. A type the check does not
  know is a note.
- **One account per folder.** Put each account's export (a TFSA's apart
  from a non-registered account's) in its own `inputs/<account>/`
  folder. Exports holding rows of several `account_id`s, in one file
  or across the folder's files, are booked to the one taxjson account:
  an ATTENTION line, or a note when the account sets
  `combined_broker_accounts = true`; refused when one of them is a
  registered plan and the taxjson account is taxable.
- **Securities only.** A Wealthsimple Crypto account's export is not
  read as crypto: coins go in a `crypto = true` account as `.tt` lines.

**Positions report:** none taxjson knows. Type the positions into a
`[[holding]]` TOML.

Layout: `src/taxjson/data/layouts/wealthsimple.toml` — `activity_sub_type`, `^MoneyMovement$`, `account_type`, `verified = false`; `src/taxjson/lib/brokerages/generic.py` — `BUILTIN_LAYOUTS`, `shipped_layout_hint`, `_units_of`, `_account_cells`, `_say_unverified`; `src/taxjson/lib/brokerages/base.py` — `check_account_plans`, `check_account_plans_across`.

## Kraken

**Download:** **both** the **Trades** export **and** the **Ledgers**
export, CSV, every year available. Put them side by side in the same
`inputs/<account>/` folder.

**Where:** sign in, click your profile icon, choose **Documents**, then
**Create Export** under Exports. Pick the type (**Trades**, then again
**Ledgers**), a start and end date, and the **CSV** format; keep every
field. Click **Generate**. Kraken does not email you: come back to
Documents and click the download icon once it is active. An export can
take from a few minutes to a week.

**What taxjson checks:**

- **Why both.** The trades export books each fill, but it states every
  fee in the quote currency even when Kraken took the fee in the coin.
  Only the ledger says which coin paid it. Without a ledger beside
  the trades file, the `.sum` warns that the fee currency of those fills
  can't be verified.
- **Both must cover the same dates.** A trade in the ledger that no
  trades export beside it holds is an `UNBOOKED` warning naming its
  dates. A lone instant-trade leg, and a ledger row of a type the parser
  does not book that moves a coin (an airdrop, a conversion, an
  adjustment, margin), are `UNBOOKED` too. `tjs run --strict` stops on
  any `UNBOOKED` line.
- **Overlapping ledgers are read once**, by their ledger `txid` (by
  the books, and by the stablecoin pool `tjs crypto-sends` rebuilds,
  entry by entry).
- **A blank `txid`.** Two fills or ledger rows alike in every cell
  are two events when their `txid` is blank, as in any export without
  ids; an overlapping export holding the same rows is still read once.
- **Cash and its balances** (for `tjs fx-cash --ledger v2`). A deposit,
  a withdrawal and a conversion of currency (or, in a Canadian project,
  of a USD stablecoin) carry the ledger's `txid` and `refid`: two
  deposits alike in day and amount are two, in one export or in two,
  and a row two overlapping exports hold is one. A `txid` that two
  exports hold with different amounts is refused, naming both rows.
  The ledger's `balance` column gives the account's cash: each
  wallet's latest balance (a wallet is its asset code, such as `USDC`
  or `USDC.F`, and the export's `wallet` column where it has one, such
  as `spot / main` or `earn / flexible`), the wallets of one currency
  added up over every ledger export in the folder. The exports may be
  split by date or by wallet: the balances are the same as one export's.
  Rows of an older export without the `wallet` column are the wallet
  their code says (`USDC` the spot wallet, `USDC.F` the flexible Earn
  wallet). A move between two wallets of the account (an Earn
  allocation, a transfer between spot and staking) is no deposit or
  withdrawal: the cash stays in the account's balance. Where cash moved
  to a wallet no export in the folder holds
  (an Earn allocation whose other row is missing), that currency gets
  no balance from that day on and the ledger says which row: add the
  export that holds the wallet. Where rows are missing between two
  exports (the later one begins at another balance than the earlier
  one ended at), the dates between them read the balance the later
  export begins at, and the ledger shows the gap. A wallet whose first
  row begins at a balance is known from the year end before that row;
  earlier dates get no balance of its currency, and a note says so.
- **One `txid` on two rows of a file.** Two identical rows (downloads
  pasted together) are read once; two rows that differ are refused,
  naming both lines: the books keep one row per id, so one trade would
  be dropped.
- **Required columns:** trades `txid`, `pair`, `time`, `type`, `price`,
  `cost`, `fee`, `vol`; ledgers `txid`, `refid`, `time`, `type`,
  `asset`, `amount`, `fee`.

**Positions report:** none taxjson knows (a ledger's running balance is
not one). Use a `[[holding]]` TOML.

Parser: `src/taxjson/lib/brokerages/kraken.py` — `_TRADES_REQUIRED`, `_LEDGER_REQUIRED`, `_check_trade_coverage`.

Sources: [How to export your account history](https://support.kraken.com/ca/articles/208267878-how-to-export-your-account-history) (Documents, Create Export, Trades / Ledgers, CSV, processing time; no date-range limit stated). <!-- pii-ok: Kraken's public article id -->

## Coinbase

**Download:** the **transaction history** report, CSV, all assets and
all transaction types, every year available.

**Where:** Coinbase's help pages refused automated reads, so this is
not confirmed: its "Accessing my account documents" article (seen only
in search results) points to the Statements page, where a custom
statement can be generated for chosen assets, transaction types and
dates as CSV.

**What taxjson checks:**

- **Layouts.** Coinbase has shipped several transaction-history
  layouts; the parser reads them by their column names (Timestamp,
  Transaction Type, Asset, Quantity Transacted, the price and total
  columns), with any preamble lines above the header. A layout it does
  not know is refused, never guessed.
- **Advanced Trade** buys and sells in the transaction history are
  read. Rewards and staking income (several labels) are income plus a
  purchase at fair value. Convert rows are swaps.
- **Sends and arrivals.** A Send or Receive is kept as custody
  evidence, not a sale. `tjs crypto-sends` lists every send that did not
  arrive in another of your crypto accounts and records your decision
  (your own wallet, a gift, a payment) with the sale line it needs.
- **Unknown types.** A row of a type the parser does not book that
  moves coins (an airdrop, a new reward label) is an `UNBOOKED`
  warning: enter it as a `.tt` line.
- **Row IDs.** A row's ID keeps two fills in the same second apart. A
  row without one (an older layout with no ID column, or a blank cell)
  is told apart from an identical row of the same file by its order, and
  the same rows in an overlapping export are still read once (in the
  stablecoin pool `tjs crypto-sends` rebuilds too). One ID on two rows
  of a file: identical rows are read once, and two rows that differ
  are refused, naming both lines. One ID on a cash row (a deposit, a
  withdrawal, a stablecoin buy or sell) in two overlapping exports with
  different amounts, dates or currencies: `tjs cash` and `tjs fx-cash
  --ledger v2` refuse, naming both rows; keep the export with the right
  copy.
- **A blank Total** on a Buy or Sell is derived as Coinbase builds it:
  Subtotal plus the fee on a buy, minus it on a sale (without a
  Subtotal, quantity x price stands in). The books, the FX-on-cash
  ledger v2 and the crypto-sends pool all derive it; a row with neither
  is refused, naming it: fill in the Total from the Coinbase statement.

**Positions report:** none taxjson knows. Use a `[[holding]]` TOML.

Parser: `src/taxjson/lib/brokerages/coinbase.py` — `_HEADER_SYNONYMS`, `_REQUIRED_FIELDS`.

Sources: [Accessing my account documents](https://help.coinbase.com/en/coinbase/taxes/tools/statements) (not fetched; menu path unconfirmed).

### Every crypto account: your time zone

Kraken and Coinbase stamp rows in UTC; taxjson dates them in your time
zone, `[settings] local_timezone` (e.g. `"America/Toronto"`): a fill at
03:00 UTC on January 1 is December 31 in Toronto, in the previous tax
year. There is no default: a project with a `crypto = true` account
stops until it is set ([settings.md](settings.md#local_timezone));
`tjs init` writes this machine's zone when it can.

## Short sales: which purchase covers a short (Canada)

A short the broker marks is a position of its own in a Canadian project (`tjs tax-logic` CA-DISP-04).
Which purchase closes it follows each broker's own convention, read from the export a row came from:

| The row comes from | A short sale is marked | The purchase that covers it |
|---|---|---|
| **RBC Direct Investing** | `SHORT.` in the Description | only a purchase with `COVER SHORT.` in the Description. Every other purchase in that broker's exports is a long purchase, whatever file it is in, even while the account is short. |
| **Interactive Brokers** | Code `O` on the sale (the Code column is required) | a purchase while the account is short and holds no shares of its own covers, whatever its Code (one netted position per account); a purchase coded `O` is a long purchase. |
| **Questrade**, a **`.tt` line**, the **generic importer**, **Webull**, **Wealthsimple** | no marker taxjson reads (how Questrade marks a short account's trades is not known) | a purchase while the account is short and holds no shares of its own covers. |

`tjs run` says when the rows disagree, and never books it silently:

- `Warning: Marked cover: …`: a purchase with no mark (a `.tt` line, say) was read as the cover, and a
  purchase the broker marks as the cover then found less short than it buys. Both rows are named; remove
  the unmarked row if the two are one trade.
- `Warning: Long and short: …`: the account's rows end with shares held and a short of the same shares.
  With RBC this is what a short covered by a purchase without `COVER SHORT.` looks like: the
  export holding the marked cover is missing, or the broker really holds both.

`tjs find-missing-history` reads the covers the same way. A US project nets one position per account
and symbol: the first purchase closes the short, whatever the marks.

## How a file's broker is detected

File names do not decide which parser reads a CSV. For every CSV in
`inputs/<account>/`, `tjs run`:

1. **Honours a generic mapping first.** A `<file>.csv.toml` beside the
   CSV (any file name), or a `generic_*.csv` file with the folder's
   shared `generic.toml`, is configuration: the generic importer reads
   the file, whatever its content.
2. **Reads the content.** Each supported export is recognised by the
   header its own parser requires: IB's `Statement,Header` (or another
   section's `<section>,Header`) rows; Questrade's 14 activity columns;
   Webull's Trading Summary labels (both layouts); RBC Direct's
   activity header (after any preamble); Coinbase's transaction columns
   (every layout the parser reads, preamble lines allowed); Kraken's
   trades or ledgers columns. These signatures never overlap. A file
   that matches two (two exports pasted into one file) stops the run,
   naming both.
   A **positions report** is not activity: an RBC Holdings Export in
   an inputs folder is listed as `→ positions report (RBC Holdings
   Export, as of ...) — not activity; skipped`, and so is a
   `[[holding]]` TOML. An IB Activity Statement is activity whatever
   sections it has; `tjs sanity` and `tjs opening` also read its Open
   Positions.
3. **Falls back to the file name** only when no header matched: a `cb_`
   or `kr_` prefix, or the word `coinbase` or `kraken` in the name (and
   `generic_`, whose missing mapping the importer then names). When the
   header matched and the name suggests another broker, the content
   wins and a note says so.

The parsers, their ids and the spellings `taxjson-brokerage --brokerage`
accepts are listed once, in `src/taxjson/parsers/registry.py`; the
detection above is generated from that list.

The run prints one line per file under its account's step:

```
==> margin  (taxable)
Info: File inputs/margin/activity_2025.csv → identified as Interactive Brokers
Info: File inputs/margin/generic_ws.csv → identified as generic
==> crypto  (taxable, crypto)
Info: File inputs/crypto/export.csv → identified as Kraken
Info: File inputs/crypto/cb_old.csv → identified as Coinbase
Info: cb_old.csv: read as Coinbase by its file name ("cb_") only — its header matches no supported export.
```

How each file was matched is kept in `work/<account>_detect.diag`, and
the notes also appear in the account's `.sum` DIAGNOSTICS; those saved
files mask account-number-like parts of file names (`U5***_2025.csv`).

A file nothing routes stops the run. Check its header first: download
it again with the broker's own columns (the message names the layout it
nearly matched and the columns it lacks). For another broker, add a
generic mapping; as a last resort for a Coinbase or Kraken export,
rename it to start with `cb_` / `kr_`. `taxjson-detect-brokerage FILE`
answers the question for one file.

Code: `src/taxjson/lib/brokerages/detect.py` — `content_matches`, `same_broker_siblings`; `src/taxjson/bin/taxjson_detect_brokerage.py` — `cannot_detect_message`.

## Any other broker (generic importer)

No parser for your broker? Describe its CSV in a small TOML column
mapping, and the generic importer reads it.

- **Where the mapping goes.** A sidecar `<file>.csv.toml` beside the
  CSV (any file name; it wins), or one shared `generic.toml` in the
  folder for every `generic_<anything>.csv`. A mapping with a
  `[signature]` (two or more of the export's header names) reads any
  CSV whose header has them, whatever its name: the folder's
  `generic.toml` for the files beside it, or a layout,
  `layouts/<name>.toml` in the project or in `~/.config/taxjson/layouts/`,
  for every account; a sidecar of one line, `[layout] name = "<name>"`,
  picks a layout by hand (with a `[broker] account` of its own).
  taxjson ships layouts too ([Wealthsimple](#wealthsimple)), weighed
  only when none of yours fits the file.
- **Start from the template**
  [`examples/generic_wealthsimple.toml`](../examples/generic_wealthsimple.toml):
  map your header names in `[columns]` (`date` is the **trade** date),
  the date format in `[formats]`, and each action value to `buy`,
  `sell`, `dividend`, `dividend_in_lieu`, `tax`, `interest`, `fee`,
  `split` (a `ratio` column holds the ratio, new units per old unit),
  `rename` (a `symbol_new` column holds the new ticker), `transfer_in`
  or `transfer_out` (the units; a `book_value` column for a transfer
  in's stated cost), `opening` (a position held at the start of the
  data: its units and total cost, a `lot_date` column for when it
  was bought), `roc` (a return of capital), `adjust` (a cost
  adjustment) or `skip` in `[actions]`. An action value that varies
  (`DIVIDEND XEI`, `DIVIDEND ZQQ`) is matched by a `[[classify]]` rule
  (a regular expression over the action or description cell, naming
  the target), and a quantity, price, symbol or split ratio the export
  prints only in its description comes from an `[extract]` pattern
  (both in [docs/settings.md](settings.md#generic-importer-mapping)).
  Set the currency in `[defaults]` if the CSV
  has no currency column: there is no implicit USD.
- **Name the broker.** `[broker] name = "wealthsimple"` parses that
  broker's generic files on their own and gives it its own row in the
  fees report (`generic:wealthsimple`); without a name every generic
  file shares one `generic` row. `[broker] account` (or a per-row
  account column) keeps two broker accounts' identical rows apart.
- **It refuses rather than guesses.** An unmapped action carrying a
  quantity or amount is `UNBOOKED`; an amount that is not quantity x
  price ± fee, a missing column, an unknown key or a decimal comma stops
  the import.

**What it cannot do:**

- **No crypto.** It books securities only; a generic file in a
  `crypto = true` account is refused. Coins go in a crypto account
  through the Kraken or Coinbase export, or `.tt` lines.
- **An exercise or an assignment is two legs.** Map the option's row
  and the shares' row to `assign_buy` / `assign_sell` (the leg in or
  out; docs/settings.md), so the premium goes into the shares' cost or
  proceeds. A $0 option close beside a stock trade at the strike under
  other targets is an `ATTENTION` line. A warrant's or a right's exercise
  is a `.tt` `ASSIGN` line with `exercise_of=`.
- **Futures need the amount column.** The contract size is never
  guessed.

Every key, refusal, symbol and date rule is in
[settings.md, Generic importer mapping](settings.md#generic-importer-mapping).

Code: `src/taxjson/lib/brokerages/generic.py` — `_load_mapping`.

## Auto-fetch (Questrade, Interactive Brokers)

The **taxjson-fetch** plugin downloads Questrade and Interactive
Brokers activity straight into the inputs folder. It is a separate
package (`packages/taxjson-fetch`) behind `tjs fetch`: the core
holds no broker API client and never reads a broker credential.

**Install.** The one-line installer installs it by default
(`--without-fetch` leaves it out). From a checkout, install the core
first (`pip install -e .`), then the plugin into the same environment:

```bash
pip install --no-deps -e packages/taxjson-fetch
tjs fetch --list            # the installed fetchers
```

taxjson is not published on PyPI, so a `taxjson` or `taxjson-fetch`
package there is not ours: never install either by name from PyPI.
Without the plugin, `tjs fetch` prints the install line and exits 2.

**Configure** the source on each account in `taxjson.toml`:

```toml
[accounts.margin]
type = "taxable"
brokerage = "questrade"
account = "99900001"     # the Questrade account number

[accounts.ibkr]
type = "taxable"
brokerage = "ibkr_flex"
query_id = "123456"      # an Activity Flex Query id
```

For IB, create an **Activity Flex Query** for **one** account (a
download of two accounts is refused): format **CSV**, **Include Section
Code and Line Descriptor** on, and the **Cash Report** section included
(parsed money is reconciled against it). In its delivery settings
pick the date and time formats that give `2025-03-28` and `09:30:00`:
the parser reads only that shape and refuses others, such as Flex's
default-looking `20250328;093000` (taxjson's own check; IB's pages do
not describe it). A download without the
`<section>,Header` shape the IB parser reads is saved as
`ib_flex.csv.unrecognized` and the fetch stops. Then turn on the **Flex
Web Service** (Performance & Reports › Flex Queries › Flex Web Service
Configuration) and generate a token.

**Credentials never go in `taxjson.toml`.** Questrade takes a refresh
token once (`$QUESTRADE_REFRESH_TOKEN`, or `--refresh-token`) and keeps
the rotated token in `~/.questrade_token` (`$QUESTRADE_TOKEN_FILE`
overrides). IBKR reads `$IBKR_FLEX_TOKEN` (or `--flex-token`). Prefer
the environment variables: flags show in the process list.

**Run** `tjs fetch` (every account with a `brokerage`), or
`tjs fetch run` to rebuild the books right after. One fetch runs
per project at a time; a second one waits (Ctrl-C stops it).

**Where the files land.** In the shared inputs folder,
`inputs/<account>/`, so a download applies to every year that reads
it (a filed year reads its own frozen copy):

- `questrade_<year>.csv`, named for the project's tax year. Every fetch
  re-covers the whole tax-year window, **December 1 of the year before
  through January 31 of the year after** (up to today), and merges into
  the file, so year-boundary trades and the 30-day loss windows around
  them are in. `--year N` backfills a past year into its own
  `questrade_N.csv`; `--days N` or `--from YYYY-MM-DD` fetch another
  window.
- `ib_flex.csv`, replaced by each fetch (the Flex query re-covers its
  own period); the previous file is kept as `ib_flex.csv.bak`. A
  download that would drop activity of a year the old file holds is
  refused and saved as `ib_flex.csv.new` instead.
- `--positions` also snapshots Questrade's live positions into the
  year's holdings folder, `holdings/<account>_live_holdings.toml`,
  where `tjs sanity` finds it.

Manual exports keep working beside these files, with one catch: a
**manual Questrade export with rows inside the fetched window** is
flagged, because the API and the export round prices differently, so
the same trade would be counted twice. `--trim-overlap` removes those
rows from the manual file (the original is kept as a `.bak`): only the
fetched account's rows dated inside the window; a row whose `Account #`
is another Questrade account's is left alone.

`--dry-run` shows what would be written; `--json` gives a machine
summary. Each fetch prints a count per activity type ("Trades 14,
Dividends 6"), a quick check against a wrong window. Details:
[packages/taxjson-fetch/README.md](../packages/taxjson-fetch/README.md).

Writing a fetcher for another broker: see CONTRIBUTING.md, "Adding a
broker fetcher".

Sources: [Configure Flex Web Service](https://www.ibkrguides.com/clientportal/performanceandstatements/flex3.htm) (menu path, token);
[Delivery Configuration and General Configuration](https://www.ibkrguides.com/reportingreference/reportguide/delivery%20configuration%20and%20general%20configuration.htm) (CSV format, Include Section Code and Line Descriptor, date and time format
settings; the page does not list the format choices).

## Tax slips

Slips are never booked: they are what you check the books against
before filing. Put them in the year's own `inputs/slips/`
(`~/taxes/2025/inputs/slips/`), never in an account folder. Get them
from your broker's document centre (Webull Canada, for example: T5,
T5008 and Trading Summary in late February, T3 in late March) or, in
Canada, CRA My Account's **View tax information slips** (T5, T3, T5008;
a slip with a wrong SIN or name is not shown there: ask the issuer).

### T5008 (US: 1099-B): `tjs reconcile-slips`

```bash
tjs reconcile-slips inputs/slips/*.csv
```

It compares the slips' dispositions with the books, per symbol. Type
a CSV from the slip (one row per sale or per symbol), or use a CSV the
broker gives; headers are matched loosely (French ones too):

| Column | Accepted headers (any case) |
| --- | --- |
| symbol | `symbol`, `ticker`, `security`, `sym`, `box 17`, `identification of securities` |
| quantity | `quantity`, `qty`, `shares`, `number of shares`, `box 16` |
| proceeds | `proceeds`, `proceeds of disposition`, `gross proceeds`, `box 21` |
| cost (optional) | `cost`, `cost or other basis`, `book value`, `acb`, `box 20` |
| currency (optional) | `currency`, `currency code`, `box 13`, `devise` |

```csv
symbol,quantity,proceeds,cost
SAMPA,100,950.00,800.00
SAMPLE 21MAR25 50 C,1,120.00,
```

- Amounts must be in the project's base currency: a USD T5008 is
  refused (convert boxes 20 and 21 first).
- A bare symbol matches the books' listing of that root; IB and Webull
  option descriptions are understood. A blank proceeds cell beside a
  cost is an option that expired worthless.
- A slip aggregated per type code (SHS, OPC, "Various") cannot be
  compared: transcribe it per security.

Troubleshooting: [`MISMATCH` or `MISSING_FROM_SLIP`](troubleshooting.md#before-you-file).

### T5 and T3 (Canada): `tjs slip-audit`

`tjs slip-audit` compares the slips with the books' income, box by box
(dividends, box 18 capital-gains dividends, foreign income and tax,
return of capital, interest). It reads, from `inputs/slips/`:

- **`slips.toml`**: one `[[slip]]` table per slip, typed from the PDFs.
  `tjs slip-audit --template` prints one to fill in (a T5 per account
  and currency that has income).
- **CRA's slip PDFs**, one per slip from My Account:
  `tjs slip-audit --import-cra <folder or PDFs>` reads each slip's year,
  type, issuer and boxes (never the name, address or SIN) and shows the
  `[[slip]]` tables it would add, each placed in the account (a T3: the
  fund) whose income matches; `--write` appends them to `slips.toml`.
  It needs `pdftotext` (poppler-utils).
- **IBKR's dividends report**, `U*.YYYY.dividends.csv` (Reports › Tax ›
  Dividend report, as CSV), read payment by payment. One per IB
  account; it has no interest, so type T5 box 13 into `slips.toml`.

Keys, boxes and the IB report's columns: [settings.md, inputs/slips/](settings.md#inputsslips-slipstoml-and-ibs-dividends-reports).

### Filed with another tool?

If a past year's return was prepared with another tool,
`tjs close-year --filed-dispositions CSV` records the dispositions it
actually reported (columns `symbol,date,qty,proceeds,cost,gain`, and
optionally `account`), so next year's `tjs handoff` checks against what
was filed.

Sources: [Services in My Account](https://www.canada.ca/en/revenue-agency/services/e-services/digital-services-individuals/account-individuals/about-account/services-my-account.html) (View tax information slips);
[Which tax documentation can I expect to receive?](https://www.webull.ca/help/faq/591-Which-tax-documentation-can-I-expect-to-receive) (Webull's slip dates).

## Something not recognised?

1. Look up the exact message in [troubleshooting.md](troubleshooting.md):
   [Reading the broker files](troubleshooting.md#reading-the-broker-files)
   covers detection, encoding and spreadsheet errors; each broker has a
   section of its own.
2. Still stuck, or it looks like a bug: open an issue with the bug-report
   template. **Never attach a raw export** or anything copied from one.
   Reproduce the problem on a made-up file: start from your broker's
   demo file in `examples/` (`examples/ib_demo.csv`,
   `examples/questrade_demo.csv`, ...), edit its rows to the shape of
   the failing ones with made-up values, check that it fails the same
   way, and attach that. Only if that cannot reproduce it: `tjs redact` makes a copy with
   account numbers and names removed; read all of it before sharing (it
   keeps amounts, dates and symbols).
3. A broker taxjson has no parser for: the generic importer above, and
   a synthetic sample of its export is the most useful contribution
   (CONTRIBUTING.md, "Help wanted: broker exports").
