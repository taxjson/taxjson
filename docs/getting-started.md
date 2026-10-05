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
(`margin`, `roth`, `401k`, `crypto` for the US). Each folder's
`README.txt` says which export to download from each broker.

Open `taxjson.toml` and make the accounts match yours:

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
(the broker may call it "Holdings", "Positions" or "Portfolio"; PDF or a
screenshot is fine, you only need to read it):

1. **at the earliest date your activity download covers** (for example,
   the month-end statement just before it starts), and
2. **today**, or at the end of the tax year.

The first one is how you will fill in history the download is missing
(step 5). The second is what you check the books against (step 6).

## 4. Run

```bash
tjs run
```

The run lists each account, each file and how many rows it read. When
the books show something incomplete, it ends with a short list of what
to check next, each with the command that shows it:

```
==> margin  (taxable)
  parse questrade: 1 file(s)
  activity.csv: 6 tax objects
...
Done. Reports in ~/taxes/2025/reports/

==> before you trust these numbers (docs/getting-started.md, step 5)
  2 position(s) sold in 2025 with no purchase in your files, not in missing_history.json — those sales are NOT in `taxjson sum`: SAMPA.TO (margin), SAMPK.TO (margin). Run `taxjson find-missing-history`.
  1 transfer-in(s) from outside your books kept out with no cost: SAMPK.TO (margin). Run `taxjson transfers`.
  1 account(s) with open positions and no holdings file to check them against: margin (4). Run `taxjson sanity` with the broker's positions.
  Then `taxjson checklist`. Every step is in docs/getting-started.md.
```

The list can also name positions at a $0 cost (sold this year or still
held) and stocks that paid you income the books do not hold. A run with
nothing to report ends at `Done.`; the same counts are always written to
`reports/run_summary.json`.

Read every line that starts with `warning:`, `ATTENTION` or `NOTE`. A
warning that an account folder is empty is expected for an account you
have no files for yet.

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
## Truncated history - positions go short (missing a buy): 2 pair(s)
   2 affect tax year 2025; 0 are in registered accounts; 0 do not.

AFFECTS 2025 - missing basis distorts this year's gain; fix before filing:
------------------------------------------------------------------------------------------------
Symbol                   Account    Cur     PeakShort FirstNeg      InYrSales   InYrProceeds Reg
------------------------------------------------------------------------------------------------
SAMPA.TO                 margin     CAD      -20.0000 2025-03-10            1         795.05
SAMPK.TO                 margin     CAD      -10.0000 2025-05-12            1         115.05
```

Every row marked **AFFECTS 2025** is a sale that is not in `tjs sum` yet.
`tjs list --negative` shows the same positions. `tjs run` names them as
it builds the account and in its closing list, and `tjs sum` warns:

```
taxjson sum: warning: 2 position(s) sold in 2025 with no purchase in your files, not in missing_history.json: their gain is NOT in these totals (SAMPA.TO (margin), SAMPK.TO (margin)). `taxjson find-missing-history` lists them and the fixes (docs/getting-started.md, step 5).
```

The command ends with what to do next, in the order of 5b below. For an Interactive
Brokers sale, the row also prints IB's own cost for it:

```
SAMPG.US                 margin     CAD      -20.0000 2025-03-18            1         714.87
    broker says closing (IB code C): the sale closed a position bought before the data (IB Basis 301.00 USD) — add the missing purchase; it is not a short sale.
```

**2. Positions you still hold.** Nothing in the books can see these, so
compare with the broker. Type today's positions report (step 3) into a
small file, one `[[holding]]` per position, using the symbols `tjs list`
shows:

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
margin     SAMPK.TO   QTY_MISMATCH              -10         30    -40

5 discrepancy(ies).
Fewer shares in taxjson than at the broker usually means missing history: purchases from before your download starts, or shares transferred in. See `taxjson find-missing-history`, `taxjson transfers` and docs/getting-started.md step 5. A trade after your last export is the other usual cause.
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
# Positions files `taxjson sanity` reconciles against. Default: none.
holdings = ["margin_positions.toml"]

# REQUIRED: taxable | sheltered.
type     = "taxable"
```

`tjs format --write` puts an edited `taxjson.toml` back into the layout
`init` writes.

`sanity` compares quantities only. It cannot tell you that a cost is
wrong; that is what the book cost on the broker's report is for (5b).

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
  warning: ATTENTION: transfer-in: margin: 1 transfer-in(s) from outside your books booked at the ACB the broker states on the row (Questrade: 20 SAMPJ.TO (2024-06-03)). A broker's book value is its own record, not always your ACB: check it. To use your own figure instead, add the original purchase as a .tt BUYSELL line dated on or before the transfer: the book value is then no longer used and this line stops.
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
- `missing_history`: `missing_history.json` lists the stock for that
  account (5b, step 4): its cost stays unknown and its sales are
  reported by hand, as you declared; the book value is not used.

Webull ACATS rows are not booked at all: the run prints `warning:
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
not listed.)

### 5b. Fix, in this order

Work down this list for each position you found. Stop at the first one
that works.

```
Is the purchase in an older export you can still download?
├─ yes → (1) download it and drop it in inputs/<account>/
└─ no
   Do you have a statement from before the download starts
   (quantity and book cost of each position)?
   ├─ yes → (2) one opening line per position, from that statement
   └─ no
      Do you have the trade confirmations?
      ├─ yes → (3) one line per purchase, from the confirmations
      └─ no  → (4) missing_history.json, and report the sale by hand
```

**(1) Older exports.** The best fix: the books then hold the real
trades, dividends and corporate actions. Add the files to the account's
folder (keep the newer ones) and run again. Overlapping files are fine.

**(2) An opening balance from a statement.** Write the positions you
held on the day before your download starts into a `.tt` file in the
account's folder, for example `inputs/margin/margin_start.tt`. One
`BUYSELL` line per position, with the statement's quantity and book
cost:

```
# Opening balance from the December 2023 statement.
#        date        time      symbol    qty  cur  price    total   fee
BUYSELL  2023-12-29  09:30:00  SAMPB.TO  20   CAD  10.2475  204.95  0
BUYSELL  2023-12-29  09:30:00  SAMPC.TO  10   CAD  20.4950  204.95  0
BUYSELL  2023-12-29  09:30:00  SAMPD.TO  10   CAD  50.4950  504.95  0
```

- `total` is the book cost; `price` is the book cost divided by the
  quantity.
- Use the symbol exactly as `tjs list` spells it (`.TO`, `.US`).
- The date must be **before the first row of your download**.
- **The date matters for losses.** The superficial-loss rule looks at
  purchases 30 days around a loss. An opening line dated within 30 days
  before a loss sale of the same stock counts as a purchase and can deny
  that loss. If you know when you really bought, use that date. If not,
  date the line at least 31 days before your first sale of that stock.
- A broker's book cost is a good start, not always your ACB: it may not
  include a superficial loss, a return of capital, or the same stock in
  another of your taxable accounts. Check the ones that matter.
- **US projects:** one line **per lot**, each with its real purchase
  date. The date decides short-term or long-term.

After `tjs run` the positions appear and the sale's cost is right:

```
ACCOUNT   SYMBOL     QTY      COST   COST/SH   DEFERRED   SINCE
--------------------------------------------------------------------
margin    SAMPB.TO    20    204.95     10.25          -   2023-12-29
margin    SAMPC.TO    15    457.43     30.50          -   2023-12-29
margin    SAMPD.TO    10    504.85     50.48      99.90   2023-12-29
```

(`SAMPD.TO` shows why partial history matters. The download held a buy at
30.00 and a sale at 31.00 nine days later, which looked like a small gain.
With the 10 shares held from before, the sale was really a loss, and a
superficial one: denied, and added to the cost of the shares still held.)

**(3) Individual purchases from confirmations.** The same `.tt` file,
one line per trade, with the real date and the net amount from the
confirmation:

```
# bought 20 SAMPA.TO @ 30.00, $4.95 commission (2019 confirmation)
BUYSELL  2019-06-03  09:30:00  SAMPA.TO  20  CAD  30.00  604.95  4.95
```

`total` for a buy is quantity × price + commission. Check a file before
running with `taxjson-convert-tt --account margin inputs/margin/margin_start.tt`.
The full line format (sales, return of capital, options) is in the
README, "Importing manual cost basis".

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
taxjson sum: warning: 1 disposition(s) with an unknown cost (no purchase in your files) were routed to manual reporting — these totals EXCLUDE them (`taxjson form-export` lists them in its MANUAL REPORTING section; report them by hand).
```

The sale stays out of the totals, and `tjs form-export` lists it for
you:

```
MANUAL REPORTING REQUIRED — 1 sale(s) with no purchase in your files (unknown cost, missing_history.json), proceeds 795.05 CAD: NOT in the rows or totals above. Report each by hand once its cost is known (`taxjson find-missing-history`).
SYMBOL   | DATE       | UNITS | PROCEEDS | ACCOUNT
---------+------------+-------+----------+--------
SAMPA.TO | 2025-03-10 |    20 |   795.05 | margin
```

You still have to report that sale on your return, with the best cost
you can support. If you later find the purchase and add it, the entry
does nothing any more: the run says `ATTENTION: missing_history.json
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
tjs run                       # no "before you trust these numbers" list after "Done."
tjs find-missing-history      # "No missing-cost-basis issues found"
tjs sanity                    # "OK: tickers and quantities agree"
tjs list                      # no negative quantities, no surprise 0.00
```

## 6. Check against the broker

Before you file, update the positions file from step 5 with the broker's
**year-end** positions and run `tjs sanity` again. Every difference is
either a trade after your last export or something still missing. Once
`holdings = [...]` is set, every run ends with the same check:

```
==> holdings sanity (taxjson.toml `holdings`)
...
OK: tickers and quantities agree in every group.
```

When it differs, the run says so: on a first project, missing history
is the likely cause (go back to step 5); later, same-day trades not yet
in the CSVs are the usual one.

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
  hold is a hint: the run's closing list names such stocks, and `tjs
  divs-sum` lists dividends by stock.
- **A wrong cost with the right quantity** (a transfer or opening line
  with the wrong book cost). Compare `tjs list` with the broker's book
  cost.
- **The wrong lot in a US sale.** With partial history, FIFO sells the
  oldest lot it can see, which may not be your oldest lot. The gain and
  the short/long-term split are then wrong with nothing negative to
  show it. `sanity` catches the quantity difference; fix it with one
  line per missing lot (5b).
