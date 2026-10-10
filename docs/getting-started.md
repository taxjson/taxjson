# Getting started

This guide takes you from nothing to a checked set of numbers for one tax
year. It is written for a Canadian project; a US project uses the same
steps (the differences are noted).

The hard part is not the software. It is **missing history**: your
broker's export almost never reaches back to every purchase you still
hold. Step 5 is about that, and it is the step to read slowly.

Every example below uses made-up data (tickers like `SAMPA.TO`). The
command output is real output from those examples, shortened where it
says `...`.

`tjs` is the short name of `taxjson`; both work. A word you don't know
(ACB, superficial loss, TOBASE, journal ...) is in the
[glossary](glossary.md).

**The reading path.** This guide first; then
[brokers.md](brokers.md) when you download each broker's files (step 3),
and [filing.md](filing.md) when the year is over (step 7). Everything
else is reference: [commands.md](commands.md) (every command),
[settings.md](settings.md) (every setting and file),
[tax-rules.md](tax-rules.md) (the rules), [limits.md](limits.md) (what
taxjson does not do), [troubleshooting.md](troubleshooting.md) (a message
you don't understand).

**Not sure what comes next?** Run `tjs checklist`. It lists every
step of this guide with the command for it; in a project folder it
checks each step, marks which are done and names the next one. It
writes nothing but your own marks, so run it as often as you like.

## 1. Install

```bash
bash -c "$(curl -fsSL https://taxjson.com/install.sh)"
tjs --version
```

This also installs the taxjson-fetch plugin (`taxjson fetch`: Questrade /
IBKR Flex auto-fetch, a separate package that does nothing until you run
it); add `_ --without-fetch` to the installer line to leave it out.
Re-run the same line to upgrade; release channels and what changed
between releases are in [upgrading.md](upgrading.md); installing from a
checkout is in [CONTRIBUTING.md](../CONTRIBUTING.md#setup).

**Try it first (optional).** `tjs init --demo ~/taxjson-demo` makes a
throw-away project of made-up exports (Canada, tax year 2024: two
Interactive Brokers accounts, Questrade, Webull, a TFSA at RBC, Kraken
and Coinbase), its `taxjson.toml` ready (time zone set, nothing to
answer). Then:

```bash
cd ~/taxjson-demo/2024
tjs run          # the first run downloads 2024's exchange rates
tjs sum          # the year's gains, as on the return
tjs checklist    # every step to filing, checked
```

That is what a finished run looks like, before you download anything of
your own. The demo also holds the cases this guide walks through: a sale
with no purchase (`tjs find-missing-history`, step 5), a spin-off with
its election saved (`tjs spinoffs`), a superficial loss
(`tjs wash-sales`) and a written option open at the year end
(`tjs option-boundary`). The same made-up exports, one per broker, are
in the repository's `examples/` folder.

## 2. Create a project

One folder for your taxes, with a project per tax year inside it. Name
the year you are filing:

```bash
tjs init --country canada --year 2025 ~/taxes     # or --country usa
cd ~/taxes/2025
```

`init` writes one folder per account under `~/taxes/inputs/`: `margin`,
`tfsa`, `rrsp` and `crypto` for Canada (`margin`, `roth`, `401k`,
`crypto` for the US) — every year's exports go there — and the year's
project, `~/taxes/2025/`: `taxjson.toml`, a `ticker.map` and an empty
`holdings/`; in Canada also `~/taxes/tobase.map` (below), one file every
year reads. Each account folder's
`README.txt` says which export to download from each broker. Every command
runs in the year's folder.

**Interlisted shares (Canada).** Many Canadian companies trade both on the
TSX and in the US (NYSE, Nasdaq, or over the counter): the two listings are
one security for your ACB and the superficial-loss rule. `tobase.map` holds
those pairs, from the list that ships with taxjson (`TOBASE QZAB.US QZA.TO`,
one line per US listing); you do not edit it. Your own `ticker.map` lines
win over it (`DISTINCT QZAB.US QZA.TO` keeps a pair apart). Look-alike
tickers need no line: taxjson never joins two listings because their
letters match. After an
upgrade, `tjs update-tobase-map` shows what a newer list changes and
`tjs update-tobase-map --write` applies it, for every year at once (the
checklist says when); in a
project made before tobase.map existed, the same command shows which pairs
would change your books before it writes anything ([settings.md](settings.md#tobasemap)).
A project made before v0.27.1 keeps a copy in each year folder: `tjs
migrate` in `~/taxes` makes them one.
(`tjs init --single` makes one folder for one year instead, with
`inputs/` inside it; see "One folder of exports for every year" below.)

Open `2025/taxjson.toml` and make the accounts match yours. A line starting
`## ` is a description; a line starting with a single `# ` is a setting
switched off (`# province = "ON"`): delete the `# ` to switch it on.

- Keep one `[accounts.NAME]` section per account you have, and delete the
  ones you don't. The folder `inputs/NAME/` belongs to that section.
- `type = "taxable"` for a margin or cash account, `"sheltered"` for an
  RRSP, TFSA, IRA and so on. Include the sheltered accounts even though
  they owe no tax: a purchase there can deny a loss in a taxable account.
- If you keep a crypto account, uncomment `local_timezone` under
  `[settings]` and set your zone (for example `"America/Toronto"`). The
  run stops without it, and says so. If you have no crypto, delete
  `[accounts.crypto]` instead.
- US projects: `source_currencies` is left commented, so no foreign
  exchange rates are fetched. Uncomment it only if you hold something in
  another currency.

## 3. Download your broker files

Put each account's files in its own folder, `inputs/<account>/` (beside
the year folders: one folder per account for every year). Any file
name works: taxjson recognises each broker's export by its header, and
`taxjson run` prints which broker it read each file as. Several files for
one account are fine; overlapping rows are read once.

Download **all the history the broker will give you**, not just the tax
year. Your cost for a share sold this year comes from the day you bought
it, which may be years back.

| Broker | Download | Notes |
| --- | --- | --- |
| Interactive Brokers | Activity Statement, CSV | Longest period allowed; one file per year is fine. |
| Questrade | Transaction history (account activity) | Every year available. It downloads as Excel: convert it with `taxjson-xlsx-to-csv FILE.xlsx -o FILE.csv`. |
| RBC Direct Investing | Transaction history, CSV | Every year available. |
| Webull | Trading Summary, CSV | Buys and sells only: enter dividends and interest from the slips ([brokers.md](brokers.md#webull)). |
| Kraken | Trades **and** Ledgers, CSV | Both, into the same folder. |
| Coinbase | Transaction history, CSV | |
| Anything else | Any CSV | A column mapping: [brokers.md, "Any other broker"](brokers.md#any-other-broker-generic-importer). |

Where each download is in the broker's website, how far back it goes and
each broker's quirks: [brokers.md](brokers.md).

Also save, for each account, **two positions reports with book cost**
(the broker may call it "Holdings", "Positions" or "Portfolio"):

1. **at the earliest date your activity download covers** (for example,
   the month-end statement just before it starts), and
2. **today**, or at the end of the tax year.

The first one is how you will fill in history the download is missing
(step 5). The second is what you check the books against (step 6).

taxjson reads two brokers' reports as they are: an **Interactive
Brokers** Activity Statement in CSV (its Open Positions section; the
same file `tjs run` reads) and **RBC Direct Investing**'s Holdings
Export (CSV). For any other broker, a PDF or a screenshot is fine: you
type its positions into a small file (step 5). Keep these reports out
of `inputs/`, or leave them there: `tjs run` skips a positions report.

**Sharing a sample** (a broker taxjson does not read yet, or a bug
report): never share a raw export. First choice: a made-up file in the
same shape (copy the broker's `examples/*_demo.csv` and edit its rows;
made-up amounts are fine). Otherwise run `tjs redact` in the project. It
copies `inputs/` to `inputs_redact/` and replaces account numbers, names
and contact details in the copy, file names included; `inputs/` is not
changed, and the run never reads `inputs_redact/`. It keeps amounts,
prices, dates and symbols, and works from patterns, so it can miss
something: read all of the copy, and share it only after that review. With one folder
of exports for every year, run it in the year's folder: `2025/inputs_redact/`
is then that year as a single-folder project (the shared exports and the
year's slips under `inputs/`, its `holdings/`, `taxjson.toml` and
`ticker.map`), runnable where it is; nothing outside the year folder is
written.

## 4. Run

```bash
tjs run
```

The run lists each account, each file and how many rows it read. When
the books show something incomplete, it ends with a short list of what
to check next, each with the command that shows it:

```
==> margin  (taxable)
Info: File inputs/margin/activity.csv → identified as Questrade
==> Reading 1 file
Info: activity.csv: 6 tax objects
==> Processing corporate actions
==> Sorting, de-duplicating, mapping tickers, converting currency
==> Calculating capital gains
==> Writing holdings reports/margin_holdings.toml
==> Writing summary reports/margin.sum
...
==> Done. Reports are in reports/
==> Before you trust these numbers (docs/getting-started.md, step 5)
Warning: 2 positions sold in 2025 with no purchase in your files, not in missing_history.tt: SAMPA.TO (margin),
SAMPK.TO (margin). Those sales are NOT in `taxjson sum`; run `taxjson find-missing-history`.

Warning: 1 transfer-in from outside your books kept out with no cost: SAMPK.TO (margin). Run `taxjson transfers`.
Info: 1 account with open positions and no holdings file to check them against: margin (4). Run `taxjson sanity` with
the broker's positions.

Info: Then run `taxjson checklist`: it checks every step and names the next one.
```

Every line starts with `==> ` (a step the run is doing), `Info:`,
`Warning:` or `Error:`, or continues the message above it. A message
that takes more than one line is followed by one blank line.

The list can also name positions at a $0 cost (sold this year or still
held) and stocks that paid you income the books do not hold. A run with
nothing to report ends at `Done.`; the same counts are always written to
`reports/run_summary.json`.

Read every line that starts with `Warning:` and every `Info:` line. A warning that an account folder is
empty is expected for an account you have no files for yet.

Exit codes: `0` done, `1` failed (the message names the file or row),
`3` a merger or spin-off needs your decision (`tjs elect --pending` shows
it).

Then look at the result:

```bash
tjs sum          # the year's gains, ending with the lines for your return
tjs list         # what the books think you hold, with book cost
```

**`Done.` does not mean right.** The closing list only shows what the
books themselves can see. Do step 5 before you trust any number.

## 5. Find and fill missing history

Your download has a first day. Anything you bought before it is not in
the books. That shows up in four ways:

| What happened | What you see |
| --- | --- |
| You **sold** shares bought before the download starts | A negative position. The sale is left out of the year's gains; `tjs run` and `tjs sum` say so. |
| You still **hold** shares bought before the download | Nothing negative. The position is missing, or its cost is too low; a dividend on it is the only hint the run can give. |
| Shares **moved in** from another broker | With a book value on the transfer row (Questrade, RBC): booked at that value. Without one: kept out of the books, so the same as the two rows above. |
| Shares from a **corporate action** (spin-off, stock dividend) | Shares with a cost of $0, sold or still held. |

### 5a. Detect

Run these four commands. Each one finds a different case.

**1. Sales with no purchase.**

```bash
tjs find-missing-history
```

```
MISSING COST BASIS — tax year 2025

TRUNCATED HISTORY — positions go short (missing a buy): 2 pair(s)
2 affect tax year 2025; 0 are in registered accounts; 0 do not.

AFFECTS 2025 - missing basis distorts this year's gain; fix before filing:
Symbol    Account  Cur  PeakShort  FirstNeg    InYrSales  InYrProceeds
----------------------------------------------------------------------
SAMPA.TO  margin   CAD   -20.0000  2025-03-10          1        795.05
SAMPK.TO  margin   CAD   -10.0000  2025-05-12          1        115.05
```

Every row marked **AFFECTS 2025** is a sale that is not in `tjs sum` yet
(in Canada also a short whose stock another of your taxable accounts
trades that year: one ACB pool). Rows **ACTIVE IN 2025** trade, move or
pay income in the year without a sale drawing on the missing cost. A
short the broker itself declares (RBC's `SHORT.` description, an IB sale
whose `Code` says it opened a position) is listed apart as a real short,
not missing history.
`tjs list --negative` shows the same positions, under **Missing history (a
sale with no purchase in your files)**, apart from real shorts (the plain
`tjs list` marks them `missing history?`). `tjs run` names them after the
accounts' books (`==> Checking for missing purchase history`) and in its
closing list, and `tjs sum` warns:

```
Warning: 2 position(s) sold in 2025 with no purchase in your files: their gain is NOT in these totals
No .tt OPENING cost=unknown line: SAMPA.TO (margin), SAMPK.TO (margin).
`taxjson find-missing-history` lists them and the fixes (docs/getting-started.md, step 5).
```

A position that went short in an earlier year and has nothing in 2025 (no
trade, transfer or income; in Canada, no other taxable account trading it)
does not change 2025's numbers: `tjs run` counts those in one `Info:` line and
`find-missing-history` lists them under **NOT relevant to 2025**. To stop the
run listing them without hunting the history down:

```bash
tjs find-missing-history --write-missing-history --outside-year
```

It adds only those positions as `OPENING ... cost=unknown` lines to
`inputs/<account>/missing_history.tt` (keeping what is there; see (4)
below); 2025's numbers do not change.

The command ends with what to do next, in the order of 5b below. For an Interactive
Brokers sale, the row also prints IB's own cost for it:

```
SAMPG.US  margin   CAD   -20.0000  2025-03-18          1        714.87
  broker says closing (IB code C): the sale closed a position bought before the data (IB Basis
  301.00 USD) — add the missing purchase; it is not a short sale. `--write-purchases` drafts the
  line from IB's figure for you to review.
```

**2. Positions you still hold.** Nothing in the books can see these, so
compare with the broker. An IB statement or an RBC Holdings Export
(step 3) goes in as it is: `tjs sanity margin=statement.csv`. From any
other broker, type today's positions report into a small file, one
`[[holding]]` per position, using the symbols `tjs list` shows:

```toml
# margin_positions.toml — the broker's positions on 2025-12-31
[[holding]]
symbol = "SAMPB.TO"
quantity = 20

[[holding]]
symbol = "SAMPC.TO"
quantity = 15

# ... one table per position
```

```bash
tjs sanity margin=margin_positions.toml
```

```
ACCOUNTS  SYMBOL    ISSUE                TAXJSON  HOLDINGS  DIFF
----------------------------------------------------------------
margin    SAMPA.TO  MISSING_IN_HOLDINGS      -20         0   -20
margin    SAMPB.TO  MISSING_IN_TAXJSON         0        20   -20
margin    SAMPC.TO  QTY_MISMATCH               5        15   -10
margin    SAMPD.TO  MISSING_IN_TAXJSON         0        10   -10
margin    SAMPK.TO  QTY_MISMATCH             -10        30   -40

5 discrepancy(ies).
Fewer shares in taxjson than at the broker usually means missing history: purchases from before your
download starts, or shares transferred in. See `taxjson find-missing-history`, `taxjson transfers`
and docs/getting-started.md step 5. A trade after your last export is the other usual cause.
```

`SAMPA.TO` is the sale with no purchase from check 1, and `SAMPK.TO`
arrived by transfer with no cost (check 3). The others are new:
`MISSING_IN_TAXJSON` is a position bought entirely before your data.
`QTY_MISMATCH` with fewer shares in taxjson is **partial history**: some
purchases are in the download and some are not, so the cost of every
share sold from that position is wrong too.

To check it on every run, name the file in `taxjson.toml`:

```toml
[accounts.margin]
type     = "taxable"
holdings = ["margin_positions.toml"]
```

`tjs format --write` puts an edited `taxjson.toml` back into the layout
`init` writes.

Add the book cost to each holding (`total_cost = 204.95`) and `sanity`
also compares costs, once the quantities agree (5e). A broker's CSV
report carries its cost already.

**3. Shares that arrived by transfer.**

```bash
tjs transfers
```

```
DATE         ACCOUNT   SYMBOL     QTY   TYPE                        VALUE   FEE   CUR   WHERE     IN_BOOKS
------------------------------------------------------------------------------------------------------------
2024-06-03   margin    SAMPJ.TO    20   SAMPLE_J_CORP_TRANSFE...   600.00     -   CAD   sidecar   book_value
2024-06-03   margin    SAMPK.TO    40   SAMPLE_K_CORP_TRANSFER       0.00     -   CAD   sidecar   NO_COST
```

In a taxable account the transfer rows themselves stay out of the books
(`sidecar`): a transfer is not a purchase. Most are your own moves (a
broker's internal account shuffle, a move between two of your accounts,
a journal between a stock's US- and Canadian-dollar lines): their out
and in rows cancel, and `IN_BOOKS` says `own_move`. What is left came
from **outside your books**, and `IN_BOOKS` says what the books did:

- `book_value`: the broker printed a book value on the row (Questrade's
  `TRANSFER BOOK VALUE`, RBC's `BOOK VALUE`). The shares are booked at
  that value on the transfer date, and every run says so:

  ```
  Warning: Transfer-in: margin: 1 transfer-in(s) from outside your books booked at the ACB the broker states on the row (Questrade: 20 SAMPJ.TO (2024-06-03)). A broker's book value is its own record, not always your ACB: check it. To use your own figure instead, add the original purchase as a .tt BUYSELL line dated on or before the transfer: the book value is then no longer used and this line stops.
  ```

  (US projects: the line adds that the lot's holding period starts on
  the transfer date.)
- `NO_COST`: no book value on the row. `VALUE` is then IB's **market
  value** on the transfer day (never your cost) or RBC's 0. The shares
  stay out of the books, the run prints `ATTENTION: transfer-in: ... have
  NO cost in the books`, and its closing list counts them. Add the
  purchase (5c below).
- `.tt_covers`: your `.tt` purchase covers the row (5c). Nothing more is
  said.
- `missing_history`: an `OPENING ... cost=unknown` line opens the stock
  in that account (5b, step 4): its cost stays unknown and its sales are
  reported by hand, as you declared; the book value is not used.
- `in-kind_contribution` / `in-kind_withdrawal`: shares moved between a
  taxable account and one of your registered accounts (RRSP, TFSA ...;
  IRA in the US). The run books them in the taxable account at their
  fair market value (5c).

Webull ACATS rows are not booked at all: the run prints `Warning:
UNBOOKED: ... Webull ACATS row ...`.

Once you sell `NO_COST` shares, the sale shows up in `find-missing-history`
like any other sale with no purchase.

**4. Shares at $0.**

```bash
tjs list
```

Look at the `COST` column. A position at `0.00` that you did not get for
free is a corporate action or a transfer with no cost. Once such shares
are sold, `find-missing-history` lists them in its own section:

```
## $0-cost corp-action shares that were later sold (inflated gain): 1 pair(s)
...
SAMPQ.TO                 margin     CAD      2.0000 2024-09-16            1         545.05 $0 cost
    └ SAMPLE Q CORP STK DIV ON 20 SHS
```

While you still hold them, `find-missing-history` lists them under
**HELD** and the run's closing list counts them:

```
## $0-cost shares still held (their sale will overstate the gain): 1 pair(s)
HELD - no gain yet; give them their ACB before they are sold:
...
```

(US: a stock dividend's shares share the old shares' basis, so they are
not listed.) They count as covered once a positive `ADJUST` line of the
same stock and account, dated from 31 days before to 7 days after they
arrived, gives them a cost (5d).

### 5b. Fix, in this order

Work down this list for each position you found. Stop at the first one
that works.

```
Is the purchase in an older export you can still download?
├─ yes → (1) download it and drop it in inputs/<account>/
└─ no
   Do you have a positions report from before the download starts
   (quantity and book cost of each position)?
   ├─ yes → (2) `tjs opening`: an opening balance from that report
   └─ no
      Do you have the trade confirmations?
      ├─ yes → (3) one line per purchase, from the confirmations
      └─ no  → (4) an OPENING ... cost=unknown line, and report the sale by hand
```

**(1) Older exports.** The best fix: the books then hold the real
trades, dividends and corporate actions. Add the files to the account's
folder (keep the newer ones) and run again. Overlapping files are fine.

**(2) An opening balance from a positions report.** Take the report
from the day before your download starts (step 3). An IB statement or an
RBC Holdings Export goes in as it is; from any other broker, type it
into a small file with each position's book cost:

```toml
# margin_start.toml — the broker's positions on 2023-12-29
[meta]
as_of = "2023-12-29"

[[holding]]
symbol = "SAMPB.TO"
quantity = 20
currency = "CAD"
total_cost = 204.95

[[holding]]
symbol = "SAMPC.TO"
quantity = 10
currency = "CAD"
total_cost = 204.95

[[holding]]
symbol = "SAMPD.TO"
quantity = 10
currency = "CAD"
total_cost = 504.95
```

```bash
tjs opening margin margin_start.toml      # or: tjs opening margin statement.csv
```

```
3 OPENING line(s) for account margin as of 2023-12-29 -> inputs/margin/opening_2023-12-29.tt
Next: `taxjson run`, then `taxjson find-missing-history` and `taxjson sanity` (it compares quantities and costs).
```

The file it writes holds one line per position:

```
OPENING 2023-12-29 SAMPB.TO 20 CAD 204.95
OPENING 2023-12-29 SAMPC.TO 10 CAD 204.95
OPENING 2023-12-29 SAMPD.TO 10 CAD 504.95
```

- **An opening line is not a purchase.** The superficial-loss rule looks
  at purchases 30 days around a loss; an opening balance is never one,
  so a loss sold a few days after the statement is not denied because of
  it. (A `BUYSELL` line dated on the statement day *would* be a
  purchase. Old opening files written that way still work, but switch
  to `OPENING` lines.)
- The date is the report's own (`--date` when it has none). **The
  opening replaces everything before it** for its stocks: rows of those
  stocks in this account dated on or before that day are left out of
  the books, so a statement that overlaps your download counts nothing
  twice. `tjs run` says so in a `Warning: Opening snapshot:` line.
  Dividends stay.
- Use a report from **before the tax year's first sale** of each stock
  (or first buy that closes a short position): a sale or cover left out
  that way would drop out of the year's gains, so the run stops instead.
- The cost is the report's **book cost**, never its market value. A
  position with no cost in the report, a short position (a written
  option) or a futures contract is listed and skipped: write its line by
  hand.
- A broker's book cost is a good start, not always your ACB: it may not
  include a superficial loss, a return of capital, or the same stock in
  another of your taxable accounts. `tjs sanity` compares the costs and
  says which of these explains each difference (5e).
- A cost in US dollars is converted at the Bank of Canada rate of the
  report's day. A broker's book cost in Canadian dollars for a US stock
  is used as the broker converted it.
- **US projects:** one line **per lot**, each with its real purchase
  date, which decides short-term or long-term. A positions report rarely
  lists lots: put one `[[holding]]` per lot in the file with
  `acquired = "2019-06-03"`, or write the lines by hand:
  `OPENING 2023-12-29 SAMPB.US 20 USD 204.95 2019-06-03`.
- No report to read, only a PDF? Write the lines by hand, in the same
  form: `OPENING <date> <symbol> <qty> <currency> <book cost>`, in any
  `.tt` file of the account's folder.

After `tjs run` the positions appear and the sale's cost is right:

```
ACCOUNT   SYMBOL     QTY     COST   COST/SH   DEFERRED   SINCE
-------------------------------------------------------------------
margin    SAMPB.TO    20   204.95     10.25          -   2023-12-29
margin    SAMPC.TO    15   457.43     30.50          -   2023-12-29
margin    SAMPD.TO    10   504.85     50.48      99.90   2023-12-29
```

(`SAMPD.TO` shows why partial history matters. The download held a buy at
30.00 and a sale at 31.00 nine days later, which looked like a small gain.
With the 10 shares held from before, the sale was really a loss, and a
superficial one: denied, and added to the cost of the shares still held.
The opening balance itself denied nothing.)

**(3) Individual purchases from confirmations.** A `.tt` file in the
account's folder (for example `inputs/margin/margin_start.tt`), one line
per trade, with the real date and the net amount from the
confirmation:

```
# bought 20 SAMPA.TO @ 30.00, $4.95 commission (2019 confirmation)
BUYSELL  2019-06-03  09:30:00  SAMPA.TO  20  CAD  30.00  604.95  4.95
```

`total` for a buy is quantity × price + commission. Check a file before
running with `taxjson-convert-tt --account margin inputs/margin/margin_start.tt`.
The full line format (sales, return of capital, options) is in
[settings.md, ".tt files"](settings.md#tt-files).

**Let the broker's figure draft the lines.** When the broker states what
a sale with no purchase cost (IB's `Basis` on a sale coded closing), or
what a transfer-in was worth on its books (`TRANSFER BOOK VALUE` on a
Questrade or RBC transfer), taxjson can write the lines for you to check:

```bash
tjs find-missing-history --write-purchases
```

```
Wrote 1 draft purchase line(s) for margin to inputs/margin/purchases_draft.tt.txt (1 from IB's Basis).
  1 need the purchase date (YYYY-MM-DD).
```

```
# IB sale 2025-03-18 of 20 SAMPG.US (code C, row 3f***, ib_2025.csv)
# IB Basis 301.00 USD for the 20 sold (no lot detail in the export)
# fill in: the purchase date (the broker does not say when these units were bought).
# check: IB's Basis is the cost of the lots IB closed (FIFO), not your ACB: ...
BUYSELL  YYYY-MM-DD  09:30:00  SAMPG.US  20  USD  15.050000  301.00  0
```

taxjson does not read this file: its name ends in `.txt`. Check each
line against your records, replace every `YYYY-MM-DD` with the real
purchase date (and any `COST` with the real cost), delete what you cannot
vouch for, then rename the file to end in `.tt` (e.g. `purchases.tt`) and
`tjs run`. A line still holding a placeholder stops the run. The date
matters: it is the Bank of Canada rate's date for a US-dollar cost, and
the superficial-loss window's. If the statement lists Closed Lots, IB's
own purchase dates are filled in for you, one line per lot. The broker's
figure is only a start: once you keep a line, the cost is yours. A
second `--write-purchases` keeps your draft unless you pass `--force`.

**(4) What you cannot recover.** If a sale's purchase cannot be found
at all, say how many units you held before your download starts, at an
unknown cost, so the sale is reported apart instead of being guessed. It
is a dated line in the account's `.tt` files:

```
OPENING 2024-12-31 SAMPA.TO 20 cost=unknown reason="bought at my old broker"
```

The command writes these lines for you into
`inputs/<account>/missing_history.tt`, dated the day before the
account's first row, with the units the books lack; open the file,
delete any line that is a real short sale and correct a quantity or
date you know better, then run again:

```bash
tjs find-missing-history --write-missing-history
tjs run
tjs sum
```

Running it again never writes a line twice; a symbol a line already
opens with another quantity is a Warning, and your line stays as it is.
With one folder of exports for every year the file is in the shared
`inputs/<account>/`, so one line serves every year. (Projects of
taxjson before v0.27.0 listed these in `missing_history.json` at the
project root: it is no longer read — every command stops until `tjs
migrate` converts it; docs/troubleshooting.md, "Error: missing_history.json
is no longer read".)

```
Warning: 1 disposition(s) with an unknown cost (no purchase in your files) were routed to manual reporting — these totals EXCLUDE them (`taxjson form-export` lists them in its MANUAL REPORTING section; report them by hand).
```

The sale stays out of the totals, and `tjs form-export` lists it for
you:

```
MANUAL REPORTING REQUIRED — 1 sale(s) with no purchase in your files (unknown cost, .tt OPENING cost=unknown), proceeds 795.05 CAD: NOT in the rows or totals above. Report each by hand once its cost is known (`taxjson find-missing-history`).
SYMBOL   | DATE       | UNITS | PROCEEDS | ACCOUNT
---------+------------+-------+----------+--------
SAMPA.TO | 2025-03-10 |    20 |   795.05 | margin
```

You still have to report that sale on your return, with the best cost
you can support. If you later find the purchase and add it, the line
does nothing any more: the run says `ATTENTION: inputs/margin/missing_history.tt:N
lists ... but its rows never go short any more` and
`find-missing-history` lists it under `STALE`. Delete it.

### 5c. Transfers in

A transfer from another broker is not a purchase. Your cost is what you
paid at the first broker, on the day you bought there. For each `NO_COST`
row in `tjs transfers` (and each `book_value` row whose value is not your
cost), add a `BUYSELL` line dated the **original** purchase, with the
original cost (from the old broker's statements, or the transfer form,
which often shows book cost):

```
# 40 SAMPK.TO moved in from my old broker on 2024-06-03
BUYSELL  2021-03-15  09:30:00  SAMPK.TO  40  CAD  15.00  600.00  0
```

- Once the account's `.tt` purchases of the stock, dated on or before
  the transfer, cover the transferred quantity, the transfer's
  `ATTENTION` line stops and `tjs transfers` shows `.tt_covers`.
- The same line overrides a broker's book value (`book_value`): the
  book value is then no longer used. Add it when the broker's figure is
  not your cost; otherwise the book value is a reasonable start.
- Do not write a `TRANSFER` or `ACQUIRED` line for a taxable account:
  the run stops with `TRANSFER is not allowed in taxable accounts`.
- Do not use IB's transfer `VALUE`: it is the market value, not your
  cost.
- A transfer-in that missing history covers (`missing_history`
  in `tjs transfers`: an `OPENING ... cost=unknown` line):
  `tjs find-missing-history --write-purchases`
  drafts its purchase line from the broker's book value (Questrade,
  RBC), with the purchase date left for you to fill in (see (3) above).
  Once you add it, remove the opening line.
- If you held the same stock at both brokers, both purchases go into one
  ACB.
- For a sheltered account (`transfers = true`) the transfer is booked
  as a purchase at the broker's value. That is fine there: no tax is
  computed on it. For the superficial-loss / wash-sale rule it is a move
  between accounts, not a purchase; the run prints one warning listing
  each transfer-in inside a taxable loss's 30-day window.
- **Shares moved between a taxable account and a registered one** (an
  in-kind contribution to your RRSP or TFSA, or a withdrawal in kind) are
  not a move of your own: ownership changes. The run pairs the taxable
  account's transfer row with the registered account's row of the same
  stock and quantity (within 10 days, across brokers) and books the move
  in the taxable account at the shares' **fair market value** on the
  transfer date. One warning per run lists each move, its value, where
  the value came from, and the gain or the denied loss:
  - Canada, a contribution is a sale at that value. A gain is taxed; a
    loss is **denied for good** (s.40(2)(g)(iv)) — `tjs sum` shows it on
    its own line ("Denied: contribution to a registered plan"), never
    added to an ACB. The plan's purchase counts for the superficial-loss
    rule: a loss on the same stock in a taxable account within 30 days
    is lost for good.
  - Canada, a withdrawal is a purchase at that value (your ACB). From an
    RRSP or RRIF the value is also income on your T4RSP / T4RIF, which
    you enter from the slip; from a TFSA it is not taxed.
  - US: an IRA, Roth or 401(k) takes contributions in cash only, so a
    transfer of shares into one is reported as a likely error and not
    booked. A distribution in kind is a purchase at fair market value
    (your basis; the taxable amount is on Form 1099-R).

  The value comes from, in order: an `INKIND` line in a `.tt` file in the
  taxable account's folder; the market value the broker prints on the
  transfer row (IB's Transfers `Market Value` column); Yahoo's close on
  that day, marked ESTIMATED. With `TAXJSON_OFFLINE` set and no cached
  close the run stops and prints the line to add. The line (no time column; the quantity is
  negative for shares out to the plan, positive for shares back):

  ```
  # 100 SAMPK.TO contributed to my RRSP, at the day's closing price
  INKIND  2024-06-03  SAMPK.TO  -100  CAD  15.00
  ```

  A total works too (`INKIND 2024-06-03 SAMPK.TO -100 CAD 0 750.00`). When
  the plan's account is not in the project, the same line declares the
  move for the taxable account's transfer row; add `plan=rrsp` (or
  `tfsa`, `ira` ...).

  Moves between two of your taxable accounts (or two registered ones)
  pair first and stay moves of your own. When a transfer row could pair
  with more than one account's row, the run does not guess: the warning
  lists the move as ambiguous and NOT booked, with the candidates. Answer
  with the `INKIND` line (it books the move; `plan=` picks the plan), or
  `INKIND 2024-06-03 SAMPK.TO -100 plan=own` for a move of your own. A
  transfer-out the plan received in parts is warned about the same way.
- A position the broker moved from one listing of a stock to another
  (`SAMPK.US` out, `SAMPK.TO` in, the same quantity) is joined into one
  security when the exports' names agree: an `Info: ... joined as one
  security` line names it. When the run cannot be sure, it suggests a
  ticker.map line instead. `tjs ticker-map --suggest` lists every
  ticker.map line the run suggested (listings, Questrade codes, ticker
  changes, coin ids) with its reason; `tjs ticker-map --suggest --write`
  asks for each one and adds the ones you accept.

### 5d. Corporate-action shares at $0

Shares that arrive from a spin-off, merger or stock dividend come with no
cash and may show a cost of `0.00`.

- **Merger or spin-off.** When the run recognises one it asks you once
  how to treat it (headless: it exits `3`, and `tjs elect --pending`
  lists the choices). Answer it; the cost is then split for you. In a
  sheltered account (RRSP, LIRA, TFSA ...) it is not asked: no tax there
  depends on it, so the new shares start at $0 and the run says so in one
  `Info:` line (`sheltered_elections = "ask"` in `[settings]` asks
  anyway).
- **Stock dividend (Canada).** The new shares come in at $0. Their cost
  is the dividend's declared amount, from the issuer's notice or your
  T5. Add it as an `ADJUST` line on the dividend date:

  ```
  # 2 SAMPQ.TO shares paid as a stock dividend, declared at 25.00 each
  ADJUST   2024-09-16  09:30:00  SAMPQ.TO  CAD  50.00
  ```

  `ADJUST` has five fields after the word: date, time, symbol, currency,
  amount. Report the dividend itself from the slip.
- **Stock dividend (US).** Nothing to add: the cost of the shares you
  held is spread over the old and new shares.
- **Anything else at `0.00`.** Find the cost on the issuer's notice and
  enter it the same way. `tjs tax-logic` states each rule taxjson
  applies.

### 5e. Check again

```bash
tjs run                       # no "before you trust these numbers" list after "Done."
tjs find-missing-history      # "No missing-cost-basis issues found"
tjs sanity                    # "OK: tickers and quantities agree"
tjs list                      # no negative quantities, no surprise 0.00
```

With a book cost on each holding, `sanity` then compares costs. The
quantities agree, and one cost differs, with its reason:

```
OK: tickers and quantities agree in every group.

COST — books vs the reports' cost: 3 compared, 2 within tolerance, 1 differ (informational: a broker's book value is not your ACB/basis; never changes the exit code).
ACCOUNTS   SYMBOL     QTY   CUR   BROKER    BOOKS     DIFF   REASON
-----------------------------------------------------------------------------
margin     SAMPD.TO    10     -   404.95   504.85   +99.90   superficial-loss
Reasons:
  superficial-loss: the books' ACB carries denied superficial losses (s.53(1)(f)); a broker's book value does not
```

The broker does not add a denied superficial loss to its book cost; the
books must. The other reasons `sanity` names: `pooled` (the same stock
in another of your taxable accounts; your ACB averages them),
`return-of-capital`, `broker-fx` (the broker converted a US stock's cost
at its own rates), `lot-basis` (IB's cost is per lot; yours is an
average), and `unexplained` — that one is a missing or mis-costed
purchase, an opening line's cost, or the broker's own error, and is
worth a look. A cost difference never changes the exit code.

`sanity` also lists **income on shares the books do not hold**: a
dividend whose description states its share count (`ON 500 SHS`, as RBC
and Questrade write it) for more shares than the books held when it was
earned — on its record date (the row's, or the `REC 09/26/25` its
description prints, under the ticker the shares had that day), else at
any time in the 45 days to the pay date. A
sale after the record date, the dividend paid later with the position
at 0, is no finding. It is the sign of a purchase still missing.

Costs are compared for taxable accounts only: a registered account's
book cost is not a tax cost (an in-kind transfer in resets it to the
market value), so `sanity` says once that it is not compared there; its
quantities are.

## 6. Check against the broker

Before you file, save the broker's **year-end** positions in the year's
`holdings/` folder (a `[[holding]]` TOML per broker account, as a
download tool writes it; `tjs sanity` finds each file's account by its
`[meta] account` or its name, `margin_holdings.toml`), or update the
positions file from step 5 (or give `sanity` the year-end IB statement or
RBC Holdings Export itself), and run `tjs sanity` again. A report dated
before the books' last row is compared with the books' positions on its
own date. Every difference is either a trade after your last export or
something still missing. Once `holdings/` holds a snapshot or
`holdings = [...]` is set (a `.toml` or a broker's report), every run
ends with the same check:

```
==> Checking positions against the broker's holdings files
Info: positions match the broker's holdings files
```

An account with open positions and no `holdings` file is not checked;
the line then ends `(checked accounts only; 1 unchecked: crypto)`.
When it differs, the run says so: on a first project, missing history
is the likely cause (go back to step 5); later, same-day trades not yet
in the CSVs are the usual one.

Also compare the books with the broker's slips (T5008; US: 1099-B):

```bash
tjs reconcile-slips inputs/slips/*.csv
```

In Canada, check the T5 and T3 slips against the books' income too:
import the PDFs CRA My Account shows under "Tax information slips"
(`tjs slip-audit --import-cra <folder> --write`), type them into
`inputs/slips/slips.toml` (`tjs slip-audit --template` prints one to fill
in) or drop IBKR's dividends report
(`U*.YYYY.dividends.csv`) in `inputs/slips/`, then (with one folder of
exports for every year the slips are the year's own: `2025/inputs/slips/`)

```bash
tjs slip-audit
```

It shows each slip box beside the books' figure and prints the lines
that bring the books to the slips (a box 18 capital-gains dividend, a
T3's return of capital).

## 7. The filing checklist

```bash
tjs checklist
```

It runs the checks above and the rest of the filing steps, and marks
each one `[x]` done, `[!]` needs attention, `[ ]` to do or `[m]` for you
to confirm. A missing purchase that affects the year shows as:

```
  [!] missing-history   No position with missing cost basis affects the year
      1 position(s) with missing basis affect 2025: SAMPA.TO (margin)
```

Work through it until every step is done or marked. Each step is
explained in [filing.md](filing.md). `tjs checklist --walk` goes through
the open ones one at a time.

## One folder of exports for every year

`tjs init` makes this layout: the broker exports once, for every year,
and a complete project per tax year beside them.

```
~/taxes/
  inputs/<account>/     every year's exports, .tt lines, manifest.json
  exports/              the newest year's positions and wash radar
  tobase.map            Canada: the interlisted pairs every year reads
                        (`tjs update-tobase-map`)
  2024/                 the 2024 project
    taxjson.toml        year = 2024, inputs_dir = "../inputs",
                        tobase_map = "../tobase.map" (Canada)
    ticker.map          the year's own (wins over tobase.map)
    holdings/           2024's broker positions snapshots
    inputs/slips/       2024's slips (slips.toml, IB's report, T5008)
    filed/ work/ reports/ checklist.json
  2025/                 the 2025 project, the same shape
```

- **Each year is its own project.** Its `taxjson.toml` and `ticker.map`
  are read exactly as a single-folder project's: a change to 2025's map
  never touches 2024's books. Only `inputs/` is shared, through
  `inputs_dir = "../inputs"` in each year's `[settings]`
  ([settings.md](settings.md#folders)) — and in Canada `tobase.map`,
  through `tobase_map = "../tobase.map"`: an update there applies to
  every year (each year's run, `tjs years` and the checklist name it
  as changed; a filed year's `tjs check-filed` shows whether it moved
  the filed figures). Run every command in a year
  folder (`cd 2025`, or `tjs -C 2025 run`); in `~/taxes` itself a
  command that needs a year is refused, naming the year folders.
- **What is shared and what is the year's own.** Shared, in
  `inputs/<account>/`: the broker exports, the `.tt` lines you write,
  `manifest.json` (your corporate-action elections) and `sends.json`
  (your crypto-send decisions). A run never writes there: only a
  decision you make — `tjs elect … --set`, `tjs crypto-sends … --set`,
  or an answer at the run's prompt — is saved there, and it applies to
  every year — and so do the `OPENING ... cost=unknown` lines of
  missing history (step 5). The year's own, in its folder: `taxjson.toml`,
  `ticker.map`, `inputs/slips/`, `holdings/`,
  `checklist.json`, `work/` (where it generates its `crypto_sends.tt`
  from `sends.json` and its own map), `reports/` and `filed/`.
- **Downloads go into `inputs/<account>/`**, all years together (file
  names can carry the period). Each year's books are built from all of
  them, as a single-folder project's are. A folder of an account a year
  does not have (an RRSP split in 2025, seen from 2024) is not read by
  that year, with one `Info:` line.
- **The year's positions snapshots go in its `holdings/`**: keep one taken
  at (or just after) the year end there. `tjs sanity` and the end of
  `tjs run` find them without a setting (step 6).
- **A new year:** `tjs new-year 2026` in `~/taxes` (or any year folder)
  creates `2026/` from the newest earlier year: its `taxjson.toml` (year
  set, `prior_year_record` pointed at `../2025/filed/2025.json`, 2025's
  `[estimate]` and `[instalments]` commented out for reference, and each
  account's `holdings` list commented out: those are 2025's snapshots),
  `ticker.map` copied (it keeps reading the shared `tobase.map`), and
  an empty `holdings/`. The
  missing-history lines are in the shared inputs: the same in every
  year, their quantities fixed.
- **Keeping the years aligned:** a map line or setting added in one year
  is not in the others. `tjs years` lists each year with its state (filed
  or open, last run, whether its inputs changed since) and says when its
  map or settings differ from the newest year's; `tjs years --diff 2024
  2025` shows the differences, and `tjs align --from 2025` in `2026/`
  brings chosen lines and settings over (asked one by one, or `--all`;
  the previous file is kept as `.bak`; 2025 is never written; a map line
  that contradicts this year's is skipped and listed). The accounts'
  `holdings` lists and broker account ids are each year's own: never
  shown as differences or brought over.
- **A filed year and later downloads.** A new export in `inputs/` can
  change a filed year's figures (a late correction, a January
  settlement). That year's `tjs sum` and checklist then say its inputs
  changed since the last run, and `tjs run` in its folder recomputes it
  and warns `filed 2024 DRIFTED` against `filed/2024.json` when the
  filed figures moved; `tjs years` shows which years changed. Amend the
  return, or keep the books consistent with it.
- **`exports/`**: the newest year's full run copies its
  `<account>_holdings.toml` and wash radar there, with a `README.txt`
  naming the year, for other tools to read whatever the year
  (`exports_dir`).
- **Inside one folder.** `inputs_dir`, `holdings_dir` and `exports_dir`
  must stay inside the folder that holds the year folders (`~/taxes`): a
  path or symlink leading further out is refused, and so is one into
  another year's folder (or, for `inputs_dir`, into the year folder
  itself). A command that writes into the shared `inputs/` (`tjs
  opening`, `tjs format-map --write`, `tjs find-missing-history
  --write-purchases`) says the file applies to every year. Keep the whole folder
  in one git repository (`git init` in `~/taxes`): the checklist's
  "inputs committed" step looks at the shared `inputs/` too.

**From a single-folder project.** `tjs migrate --to-years` in it (with
`--dry-run` first) keeps `inputs/` where it is and moves the project's
own files (`taxjson.toml`, `ticker.map`,
`checklist.json`, `work/`, `reports/`, `filed/`, `inputs/slips/` as
`inputs/slips/`, `holdings/`) into a folder named for its year, setting
`inputs_dir` and `exports_dir`; a relative `holdings` or
`prior_year_record` path into a folder that moves with it
(`holdings/margin.toml`) stays as written, any other gains a `../`. It
first refuses an old per-purpose map (`tjs migrate` folds it in), and
lists the files it leaves at the top (no year reads them). To fold several year projects (`~/taxes/2024/`,
`~/taxes/2025/`, each with its own `inputs/`) into one: in a new folder,
`tjs migrate --to-years` the newest one there, copy each older project's
own files (not its `inputs/`) into a folder named for its year, add
`inputs_dir = "../inputs"` to its `[settings]`, and copy any export
the shared `inputs/<account>/` does not have yet (the run reads
overlapping rows once). Then `tjs run` in each year: a filed year's run
says whether its figures still match its lock.

## What the books cannot see

- **A holding with no activity row at all.** Only `sanity` against the
  broker's positions finds it. A dividend on a stock the books do not
  hold is a hint: the run's closing list names such stocks, and `tjs
  divs-sum` lists dividends by stock.
- **A wrong cost with the right quantity** (a transfer or opening line
  with the wrong book cost). `sanity` compares costs when the positions
  report states them (5e); otherwise compare `tjs list` with the broker's
  book cost.
- **The wrong lot in a US sale.** With partial history, FIFO sells the
  oldest lot it can see, which may not be your oldest lot. The gain and
  the short/long-term split are then wrong with nothing negative to
  show it. `sanity` catches the quantity difference; fix it with one
  line per missing lot (5b).
