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
  `margin_holdings.toml`). A broker's own report can go anywhere: name
  it in the account's `holdings = [...]` setting or pass it to
  `tjs sanity` (`tjs sanity margin=FILE`). `tjs run` skips a positions
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

Parser: `src/taxjson/lib/brokerages/ib_extractor.py` — `_reconcile_cash_report`, `_check_statement_kind`, `_warn_coverage_gaps`.
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
`taxjson-xlsx-to-csv` (the parser reads the converted dates).

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
  account on its own.
- **Coverage.** Questrade's export does not state its end date, so the
  last row is taken as the end. If an account still holds positions and
  has no row for more than 30 days of the year, the run warns that the
  export may end early.
- **Internal symbol codes** (`X000123`) on some dividend, spin-off and
  journal rows, and currency journals (Norbert's gambit): see
  [troubleshooting.md](troubleshooting.md#questrade-rbc-direct-and-webull).

**Positions report:** taxjson knows no Questrade positions export. Type
the positions into a `[[holding]]` TOML, or let the fetch plugin
snapshot them (`taxjson fetch --positions`, below).

**Auto-fetch:** Questrade activity can be downloaded through its API
instead: see [Auto-fetch](#auto-fetch-questrade-interactive-brokers).

Parser: `src/taxjson/lib/brokerages/questrade.py` — `_QT_COLUMNS`, `missing_columns`.

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
- **Notional distributions** raise the ACB; their income is on the
  fund's T3, not in the export.

**Positions report:** the **Holdings Export** (CSV, `Holdings Export
as of <date>`), with book cost, read as is by `tjs sanity` and
`tjs opening` (columns matched by label: Symbol, Quantity, Currency,
book cost or book value).

Parser: `src/taxjson/lib/brokerages/rbc_direct.py` — `REQUIRED_COLUMNS`, `rbc_coverage_messages`, `DEFAULT_YEAR_END_POSTING`; positions: `src/taxjson/lib/positions_reports.py` — `rbc_holdings`.

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

Parser: `src/taxjson/lib/brokerages/webull.py` — `_HEADER_LABELS`, `_mark_assignments`.

Sources: [How do I get my monthly statement and trade confirmation?](https://www.webull.ca/help/faq/358-How-do-I-get-my-monthly-statement-and-trade-confirmation) (the Documents menu);
[Which tax documentation can I expect to receive?](https://www.webull.ca/help/faq/591-Which-tax-documentation-can-I-expect-to-receive) (T5008 and Trading Summary, late February).

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
- **Overlapping ledgers are read once**, by their ledger `txid`.
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
  folder for every `generic_<anything>.csv`.
- **Start from the template**
  [`examples/generic_wealthsimple.toml`](../examples/generic_wealthsimple.toml):
  map your header names in `[columns]` (`date` is the **trade** date),
  the date format in `[formats]`, and each action value to `buy`,
  `sell`, `dividend`, `dividend_in_lieu`, `tax`, `interest`, `fee` or
  `skip` in `[actions]`. Set the currency in `[defaults]` if the CSV
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
- **No exercise or assignment.** A $0 option close beside a stock
  trade at the strike is an `ATTENTION` line. Map those rows to `skip`
  and enter both legs as `.tt` `ASSIGN` lines, so the premium goes into
  the shares' cost.
- **Futures need the amount column.** The contract size is never
  guessed.

Every key, refusal, symbol and date rule is in
[settings.md, Generic importer mapping](settings.md#generic-importer-mapping).

Code: `src/taxjson/lib/brokerages/generic.py` — `_load_mapping`.

## Auto-fetch (Questrade, Interactive Brokers)

The **taxjson-fetch** plugin downloads Questrade and Interactive
Brokers activity straight into the inputs folder. It is a separate
package (`packages/taxjson-fetch`) behind `taxjson fetch`: the core
holds no broker API client and never reads a broker credential.

**Install.** The one-line installer installs it by default
(`--without-fetch` leaves it out). From a checkout, install the core
first (`pip install -e .`), then the plugin into the same environment:

```bash
pip install --no-deps -e packages/taxjson-fetch
taxjson fetch --list            # the installed fetchers
```

taxjson is not published on PyPI, so a `taxjson` or `taxjson-fetch`
package there is not ours: never install either by name from PyPI.
Without the plugin, `taxjson fetch` prints the install line and exits 2.

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

**Run** `taxjson fetch` (every account with a `brokerage`), or
`taxjson fetch run` to rebuild the books right after. One fetch runs
per project at a time; a second one waits (Ctrl-C stops it).

**Where the files land.** In the shared inputs folder,
`inputs/<account>/`, so a download applies to every year:

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
  where `tjs sanity` and the end of `tjs run` find it.

Manual exports keep working beside these files, with one catch: a
**manual Questrade export with rows inside the fetched window** is
flagged, because the API and the export round prices differently, so
the same trade would be counted twice. `--trim-overlap` removes those
rows from the manual file (the original is kept as a `.bak`).

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
