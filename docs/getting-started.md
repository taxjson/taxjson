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

`tjs` is the short name of `taxjson`; both work.

## 1. Install

```bash
bash -c "$(curl -fsSL https://taxjson.com/install.sh)"
tjs --version
```

Other ways to install (from a checkout, channels) are in the README's
[Install](../README.md#install) section.

## 2. Create a project

One folder per tax year. Name the year you are filing:

```bash
mkdir -p ~/taxes/2025 && cd ~/taxes/2025
tjs init --country canada --year 2025      # or --country usa
```

`init` writes `taxjson.toml`, a `ticker.map` and one folder per account
under `inputs/`: `margin`, `tfsa`, `rrsp` and `crypto` for Canada
(`margin`, `roth`, `401k`, `crypto` for the US).

Open `taxjson.toml` and make the accounts match yours:

- Keep one `[accounts.NAME]` section per account you have, and delete the
  ones you don't. The folder `inputs/NAME/` belongs to that section.
- `type = "taxable"` for a margin or cash account, `"sheltered"` for an
  RRSP, TFSA, IRA and so on. Include the sheltered accounts even though
  they owe no tax: a purchase there can deny a loss in a taxable account.
- If you keep a crypto account, uncomment `local_timezone` under
  `[settings]` and set your zone (for example `"America/Toronto"`). The
  run stops without it. If you have no crypto, delete `[accounts.crypto]`
  instead.

## 3. Download your broker files

Put each account's files in its own folder, `inputs/<account>/`. Any file
name works: taxjson recognises each broker's export by its header, and
`taxjson run` prints which broker it read each file as. Several files for
one account are fine; overlapping rows are read once.

Download **all the history the broker will give you**, not just the tax
year. Your cost for a share sold this year comes from the day you bought
it, which may be years back.

| Broker | Download | Notes |
| --- | --- | --- |
| Interactive Brokers | Activity Statement, CSV | Longest period allowed; one file per year is fine. |
| Questrade | Account activity, CSV | Every year available. |
| RBC Direct Investing | Transaction history, CSV | Every year available. |
| Webull | Trading Summary, CSV | Buys and sells only: enter dividends and interest from the slips (README, "Supported brokerages"). |
| Kraken | Trades **and** Ledgers, CSV | Both, into the same folder. |
| Coinbase | Transaction history, CSV | |
| Anything else | Any CSV | A column mapping: README, "Any other broker". |

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

## 4. Run

```bash
tjs run
```

A clean first run is short. It lists each account, each file and how many
rows it read, and ends with:

```
==> margin  (taxable)
  parse questrade: 1 file(s)
  activity.csv: 13 tax objects
...
Done. Reports in ~/taxes/2025/reports/
```

Read every line that starts with `warning:` or `ATTENTION`. A warning
that an account folder is empty is expected for an account you have no
files for yet.

Exit codes: `0` done, `1` failed (the message names the file or row),
`3` a merger or spin-off needs your decision (`tjs elect --pending` shows
it).

Then look at the result:

```bash
tjs sum          # the year's gains, ending with the lines for your return
tjs list         # what the books think you hold, with book cost
```

**`done` does not mean right.** A run that ends with `Done.` can still
be missing purchases. Do step 5 before you trust any number.

## 5. Find and fill missing history

Your download has a first day. Anything you bought before it is not in
the books. That shows up in four ways:

| What happened | What you see |
| --- | --- |
| You **sold** shares bought before the download starts | A negative position. The sale is left out of the year's gains, with no warning in `tjs sum`. |
| You still **hold** shares bought before the download | Nothing at all. The position is missing, or its cost is too low. |
| Shares **moved in** from another broker | A transfer row the books leave out, so the same as the two rows above. |
| Shares from a **corporate action** (spin-off, stock dividend) | Shares with a cost of $0. |

### 5a. Detect

Run these four commands. Each one finds a different case.

**1. Sales with no purchase.**

```bash
tjs find-missing-history
```

```
## Truncated history - positions go short (missing a buy): 3 pair(s)
   3 affect tax year 2025; 0 are in registered accounts; 0 do not.

AFFECTS 2025 - missing basis distorts this year's gain; fix before filing:
------------------------------------------------------------------------------------------------
Symbol                   Account    Cur     PeakShort FirstNeg      InYrSales   InYrProceeds Reg
------------------------------------------------------------------------------------------------
SAMPA.TO                 margin     CAD      -20.0000 2025-03-10            1         795.05
SAMPJ.TO                 margin     CAD      -10.0000 2025-05-12            1         445.05
SAMPK.TO                 margin     CAD      -10.0000 2025-05-12            1         115.05
```

Every row marked **AFFECTS 2025** is a sale that is not in `tjs sum` yet.
`tjs list --negative` shows the same positions. `tjs run` does not print
them (except an IB sale the broker codes as closing), so run this command
yourself.

The command ends with what to do next, in the order of 5b below. For an Interactive
Brokers sale, the row also prints IB's own cost for it:

```
SAMPG.US                 margin     CAD      -20.0000 2025-03-18            1         714.87
    broker says closing (IB code C): the sale closed a position bought before the data (IB Basis 301.00 USD) — add the missing purchase; it is not a short sale. `--write-purchases` drafts the line from IB's figure for you to review.
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
ACCOUNTS   SYMBOL     ISSUE                 TAXJSON   HOLDINGS   DIFF
---------------------------------------------------------------------
margin     SAMPA.TO   MISSING_IN_HOLDINGS       -20          0    -20
margin     SAMPB.TO   MISSING_IN_TAXJSON          0         20    -20
margin     SAMPC.TO   QTY_MISMATCH                5         15    -10
margin     SAMPD.TO   MISSING_IN_TAXJSON          0         10    -10
margin     SAMPJ.TO   QTY_MISMATCH              -10         10    -20
margin     SAMPK.TO   QTY_MISMATCH              -10         30    -40
```

`SAMPA.TO` is the sale with no purchase from check 1, and `SAMPJ.TO`
and `SAMPK.TO` arrived by transfer (check 3). The others are new:
`MISSING_IN_TAXJSON` is a position bought entirely before your data.
`QTY_MISMATCH` with fewer shares in taxjson is **partial history**: some
purchases are in the download and some are not, so the cost of every
share sold from that position is wrong too.

To check it on every run, name the file in `taxjson.toml`:

```toml
[accounts.margin]
# Positions files `taxjson sanity` reconciles against. Default: none.
holdings = ["margin_positions.toml"]

# REQUIRED: taxable | sheltered.
type     = "taxable"
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
DATE         ACCOUNT   SYMBOL     QTY   TYPE                        VALUE   FEE   CUR   WHERE
-----------------------------------------------------------------------------------------------
2024-06-03   margin    SAMPJ.TO    20   SAMPLE_J_CORP_TRANSFE...   600.00     -   CAD   sidecar
2024-06-03   margin    SAMPK.TO    40   SAMPLE_K_CORP_TRANSFER       0.00     -   CAD   sidecar
```

In a taxable account these rows are **not** in the books (`sidecar`): a
transfer is not a purchase, and taxjson will not guess a cost. Each
incoming row here needs a purchase line (5c below). `VALUE` is whatever
the broker printed: Questrade's book value, IB's **market value** on the
transfer day (not your cost), RBC's 0.

What the run prints about a transfer depends on the broker:

| Broker | Incoming transfer of shares |
| --- | --- |
| Questrade | Listed by `tjs transfers`. With no book value, the run prints `warning: ATTENTION: ... transfer-in(s) carry no TRANSFER BOOK VALUE`. |
| Interactive Brokers | Listed by `tjs transfers`. No warning. |
| RBC Direct Investing | Listed by `tjs transfers`. No warning. |
| Webull | Not booked. The run prints `warning: UNBOOKED: ... Webull ACATS row ...`. |

Once you sell such shares, the sale shows up in `find-missing-history`
like any other sale with no purchase. While you still hold them, only
`tjs transfers` and `sanity` show them.

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

While you still hold them, only `list` shows them.

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
      └─ no  → (4) missing_history.json, and report the sale by hand
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
  twice. `tjs run` says so in a `warning: ATTENTION: opening:` line.
  Dividends stay.
- Use a report from **before the tax year's first sale** of each stock:
  a sale left out that way would drop out of the year's gains, so the
  run stops instead.
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
The full line format (sales, return of capital, options) is in the
README, "Importing manual cost basis".

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
at all, list it in `missing_history.json` so it is reported apart instead
of being guessed. The command writes the file; open it and delete any
entry that is a real short sale before you run again:

```bash
tjs find-missing-history --write-missing-history
tjs run
tjs sum
```

```
taxjson sum: warning: 1 tainted disposition(s) were routed to manual reporting — these totals EXCLUDE them (`taxjson form-export` lists them in its MANUAL REPORTING section; report them by hand).
```

"Tainted" means "unknown cost". The sale stays out of the totals, and
`tjs form-export` lists it for you:

```
MANUAL REPORTING REQUIRED — 1 sale(s) with no purchase in your files (unknown cost, missing_history.json), proceeds 795.05 CAD: NOT in the rows or totals above. Report each by hand once its cost is known (`taxjson find-missing-history`).
SYMBOL   | DATE       | UNITS | PROCEEDS | ACCOUNT
---------+------------+-------+----------+--------
SAMPA.TO | 2025-03-10 |    20 |   795.05 | margin
```

You still have to report that sale on your return, with the best cost
you can support.

### 5c. Transfers in

A transfer from another broker is not a purchase. Your cost is what you
paid at the first broker, on the day you bought there. For each incoming
row in `tjs transfers`, add a `BUYSELL` line dated the **original**
purchase, with the original cost (from the old broker's statements, or
the transfer form, which often shows book cost):

```
# 20 SAMPJ.TO moved in from my old broker on 2024-06-03
BUYSELL  2021-03-15  09:30:00  SAMPJ.TO  20  CAD  30.00  600.00  0
```

- Do not write a `TRANSFER` or `ACQUIRED` line for a taxable account:
  the run stops with `TRANSFER is not allowed in taxable accounts`.
- Do not use IB's transfer `VALUE`: it is the market value, not your
  cost.
- Questrade's printed book value is not used by taxjson as a cost; you
  still add the line. `tjs find-missing-history --write-purchases` drafts
  it from the book value (an RBC row's too, when its description states
  one), with the purchase date left for you to fill in (see (3) above). A Questrade transfer with no book value keeps
  printing its `ATTENTION` line after you add it; `find-missing-history`
  and `sanity` are the check that it is fixed.
- If you held the same stock at both brokers, both purchases go into one
  ACB.
- For a sheltered account (`transfers = true`) the transfer is booked
  as a purchase at the broker's value. That is fine there: no tax is
  computed on it.

### 5d. Corporate-action shares at $0

Shares that arrive from a spin-off, merger or stock dividend come with no
cash and may show a cost of `0.00`.

- **Merger or spin-off.** When the run recognises one it asks you once
  how to treat it (headless: it exits `3`, and `tjs elect --pending`
  lists the choices). Answer it; the cost is then split for you.
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
tjs run
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
and Questrade write it) while the books held another number on its record
date. It is the sign of a purchase still missing.

## 6. Check against the broker

Before you file, update the positions file from step 5 with the broker's
**year-end** positions (or give `sanity` the year-end IB statement or
RBC Holdings Export itself) and run `tjs sanity` again. A report dated
before the books' last row is compared with the books' positions on its
own date. Every difference is either a trade after your last export or
something still missing. Once `holdings = [...]` is set (a `.toml` or a
broker's report), every run ends with the same check:

```
==> holdings sanity (taxjson.toml `holdings`)
...
OK: tickers and quantities agree in every group.
```

When it differs, the run says `same-day trades not yet in the CSVs are
the usual cause`. On a first project, missing history is the more likely
one: go back to step 5.

Also compare the books with the broker's slips (T5008; US: 1099-B):

```bash
tjs reconcile-slips inputs/slips/*.csv
```

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

## What the books cannot see

- **A holding with no activity row at all.** Only `sanity` against the
  broker's positions finds it. A dividend on a stock the books do not
  hold is a hint: `tjs divs-sum` lists dividends by stock.
- **A wrong cost with the right quantity** (a transfer or opening line
  with the wrong book cost). `sanity` compares costs when the positions
  report states them (5e); otherwise compare `tjs list` with the broker's
  book cost.
- **The wrong lot in a US sale.** With partial history, FIFO sells the
  oldest lot it can see, which may not be your oldest lot. The gain and
  the short/long-term split are then wrong with nothing negative to
  show it. `sanity` catches the quantity difference; fix it with one
  line per missing lot (5b).
