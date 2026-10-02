# taxjson

[![tests](https://github.com/taxjson/taxjson/actions/workflows/tests.yml/badge.svg)](https://github.com/taxjson/taxjson/actions/workflows/tests.yml)

A free, open-source command-line toolkit for computing **capital gains, dividend income, and wash-sale / superficial-loss adjustments** from raw brokerage CSV exports. Built for filers who want auditable numbers they can reproduce locally — no cloud upload, no signup, no fee.

Website: **[taxjson.com](https://taxjson.com)** · one-line install below · every rule cites its source in [REFERENCES.md](REFERENCES.md).

> **Not tax advice.** This tool produces numbers; it does not give legal or accounting advice. Always reconcile against your broker's official tax slips (T5008, 1099-B, etc.) and consult a qualified professional before filing.

## What it does

> **Scope.** The Canada engine is the supported product: it has been run against real multi-account books for two tax years and reconciled against the brokers' own positions. The **US engine is experimental** — the rules are implemented and unit-tested (§1091, holding periods, Form 8949 codes) but it has not been validated on a real account. Use it, but treat its output as a draft, and please [contribute a redacted export](CONTRIBUTING.md#help-wanted-broker-exports) if you do.

- Parses CSV exports from major retail brokerages into a normalized JSON format
- Computes per-lot cost basis (Canadian ACB or US FIFO) across multiple accounts and years
- Blends multi-account taxable books for the filing numbers: Canadian ACB averages across all non-registered accounts (ITA s.47); US wash sales match across accounts while FIFO basis stays per account
- Detects and applies wash sales (US §1091) and superficial losses (Canada s.40(2)(g))
- Handles option assignments / exercises / expiries, stock splits, mergers, spinoffs, and ticker renames
- Aggregates dividends and interest with per-share rate extraction and withholding-tax back-out
- Exports filing-shaped IRS Form 8949 (code-W wash adjustments + Schedule D totals) and CRA Schedule 3 rows (`taxjson form-export`), and reconciles them against your broker's T5008 / 1099-B slips (`taxjson reconcile-slips`)
- Exports a TurboTax-importable TXF file (`taxjson form-export --form txf --out gains.txf`) so US filings need no manual transcription
- Screens the CRA T1135 foreign-property filing threshold and drafts the per-property / per-country tables (`taxjson t1135`)

Everything runs locally on your machine. Your transaction data never leaves your computer.

## Supported brokerages and countries

| Brokerage             | Equities | Options | Crypto | Notes                                              |
| --------------------- | :------: | :-----: | :----: | -------------------------------------------------- |
| Interactive Brokers   | Yes      | Yes     | —      | Activity Statement CSV; corp-action auto-detection |
| Questrade             | Yes      | Yes     | —      | Account activity CSV                               |
| RBC Direct Investing  | Yes      | Yes     | —      | Transaction history CSV                            |
| Webull                | Yes      | Yes     | —      | Trading Summary CSV (BUY/SELL rows only): a $0 option close is an expiry, or an exercise/assignment when a stock trade at the strike carries Webull's $1.00 charge (see KNOWN_ISSUES). It carries no income — enter T5 interest/dividends as `.tt` `INTEREST`/`DIVIDEND` lines |
| Kraken                | —        | —       | Yes    | Trades + Ledgers CSV (same folder: the ledger says which coin paid each fee; overlapping ledger exports are read once per txid; a ledger trade the trades export lacks, a lone instant-trade leg, or a ledger row of a type the parser does not book that moves a coin (airdrop, conversion, adjustment, margin) is an `UNBOOKED` warning, fatal under `run --strict`) |
| Coinbase              | —        | —       | Yes    | Transaction history CSV                            |
| **Any other broker**  | Yes      | —       | —      | `generic_*.csv` + a TOML column mapping (see `examples/generic_wealthsimple.toml`) |

| Country | Rule set                                                                                     |
| ------- | -------------------------------------------------------------------------------------------- |
| Canada  | ACB cost basis, superficial loss s.40(2)(g), merger s.85.1(5), spinoff s.86.1                |
| US      | **Experimental** — FIFO cost basis, wash sale §1091 (30-day window with replacement-share basis adjustment); not yet validated on real accounts |

### Auto-fetch (skip the manual export)

Questrade and Interactive Brokers accounts can pull activity directly —
declare the source on the account itself:

```toml
[accounts.margin]
type = "taxable"
brokerage = "questrade"
account = "12345678"     # Questrade account number

[accounts.ibkr]
type = "taxable"
brokerage = "ibkr_flex"
query_id = "123456"      # an Activity Flex query: format CSV, with
                         # "include section code and line descriptor" ON,
                         # and the Cash Report section included (parsed
                         # money is reconciled against it)
```

Then `taxjson fetch` (or `taxjson fetch run` to rebuild in the same
breath; a run ends with the broker cross-check when `taxjson.toml`
names the holdings files — see `taxjson sanity`. `fetch --positions`
writes `work/<account>_live_holdings.toml` snapshots of Questrade's
live positions, which can serve as those files). Credentials never go
in `taxjson.toml`: Questrade takes a
refresh token once (`--refresh-token` or `$QUESTRADE_REFRESH_TOKEN`) and
caches the rotated token in `~/.questrade_token` (shared machine-wide —
Questrade runs one rotating chain per API app; `$QUESTRADE_TOKEN_FILE`
overrides); IBKR reads
`$IBKR_FLEX_TOKEN`. Downloads land as `inputs/<account>/questrade_<year>.csv`
(stamped with the config tax year)
(every fetch re-covers the whole tax-year window — from mid-December
of the prior year, so year-boundary trades that settle in January are
never missed — and union-merges into the file, exactly like refreshing
a manual YTD export) and `inputs/<account>/ib_flex.csv` (overwritten — a Flex query
re-covers its whole configured period; the previous file is kept as
`ib_flex.csv.bak`, and a download that would drop activity of the tax
year the old file holds is refused and saved as `ib_flex.csv.new`
instead) — the same formats the parsers
read from manual exports, which keep working side by side.

A manually exported Questrade CSV with rows inside the fetched window
is flagged loudly: the two sources round price/gross differently, so
duplicate rows never dedup and the books would double-count. Re-run
with `--trim-overlap` to trim those rows (the original is kept as
`.bak` and the manual file keeps the pre-window history your cost
basis needs). `--dry-run` previews without writing; `--days N` /
`--from YYYY-MM-DD` override the window; `--year N` backfills a past
tax year into its own `questrade_N.csv`; `--json` emits a machine
summary (files, windows, rows added, activity types, overlaps). Each
fetch also prints a per-activity-type count ("Trades 14, Dividends
6") — a quick sanity check against a mis-scoped window.

### Any other broker (generic importer)

No dedicated parser? Name the export `generic_<anything>.csv` in the account's
inputs folder and describe its layout in a TOML mapping — either a sidecar
`generic_<anything>.csv.toml` (wins) or one shared `generic.toml` in the same
folder. Start from the template in
[`examples/generic_wealthsimple.toml`](./examples/generic_wealthsimple.toml):
map your CSV's header names in `[columns]`, its date format in `[formats]`, and
each action value to one of `buy | sell | dividend | dividend_in_lieu | tax |
interest | fee | skip` in `[actions]` (`dividend_in_lieu` books a payment in
lieu: ordinary income, never a dividend). An optional `[broker] name =
"wealthsimple"` names the real broker; an optional `[broker] account =
"<id>"` (or a per-row `[columns] account = "<header>"`, which wins) names the
broker account the rows belong to, so cross-file dedup keeps identical rows of
two different broker accounts apart (the id is stored hashed, never as is). `taxjson run` then parses that broker's generic files on their own
(`work/<acct>_generic-wealthsimple.json`) and records them as
`generic:wealthsimple`, so the fees report gives each broker its own row.
Without a name, every generic file goes into one `generic` row. Conventions match the hand-written parsers: signed
amounts are preserved, unmapped action values are counted and summarized (never
silently dropped — an unmapped row that carries a quantity or an amount is an
`UNBOOKED` warning on the console, refused by `run --strict` and failed by
`taxjson-brokerage --lint`; map it, or map it to `skip`), and a mapping that
references columns the CSV doesn't have refuses loudly. A `fee` row is booked
positive = charged, like every broker parser: the default `[formats] fee_sign =
"cash"` flips a CSV that shows a charge as negative cash; `fee_sign =
"charged"` takes the cell as is. A buy/sell row's commission keeps its
direction: with an amount column the amount decides (a buy that cost less
than qty × price was credited a rebate), else an explicit `fee_sign` does; a
rebate is booked as a negative fee. With neither, the fee cell is a charge
whatever its sign. Every buy/sell row is cross-checked — |amount|
must equal qty × price (× 100 for an OCC option symbol) ± fee within 1%, a fee
above 5% of the gross needs `[options] allow_large_fees = true`, two fields may
not share one header, and the currency must be mapped or set in `[defaults]`
(no implicit USD) — so a mis-mapped column stops the import instead of booking
wrong money. With no `fee` column mapped, the commission is inferred from
|amount| − qty × price (a commission-inclusive Net column); a row with no price
is checked against its amount instead (a fee at least a buy's whole amount is
refused). A futures symbol (`F:`, `/` or `\` prefix, all spelled `F:`) needs
the `amount` column: the contract size is never guessed. The mapping has no
exercise/assignment target: a $0 option close beside a stock trade at the
strike is an ATTENTION line on the console — map those rows to skip and enter
both legs as `.tt` `ASSIGN` rows so the premium folds into the shares' cost.
UTF-16 exports are read like UTF-8 ones. The import also refuses:

- an unknown section or key in the mapping (`ammount`, `commission`,
  `[format]`, `tax_sgn` …), with a did-you-mean suggestion;
- a dividend/tax/interest/fee action when `amount` is not mapped, or a row of
  that kind with a blank amount cell (it used to be booked as 0);
- a buy/sell row with no quantity, with neither a price nor an amount (the fee
  alone became the cost), or a stock buy at exactly zero cost;
- a quantity or amount sign that contradicts the mapped action — a negative
  quantity under a `buy` action, a positive quantity with a negative amount
  under `sell`, or a positive-quantity sell in a file whose other sells carry
  negative quantities (map buys and sells to separate action values);
- a decimal-comma number (`12,50`, `1.234,56`): only a thousands comma
  (`1,234.56`) is accepted. Re-export with a decimal point.
- a record with more cells than the header, or a cell holding a line break —
  the mark of an unescaped quote in a text cell swallowing the next row;
- a record with fewer cells than the header, a last record that ends on a
  separator with no line break after it, or a currency cell that is not a
  currency code — a cut-off export (`[defaults]` never fills a cell the cut
  took away);
- a `[defaults]` or `[formats]` value that is not a quoted string, and a
  sidecar mapping that is a dangling link (the shared `generic.toml` is never
  used in its place).

**Symbols.** Symbols are upper-cased (`xyz` and `XYZ` are one security). A symbol written with an exchange suffix (`.TO`, `.V`, `.CN`,
`.NE`, `.US`, `.AX`, `.L`) keeps it — `DLR.U.TO` bought in USD stays
`DLR.U.TO`. A bare symbol takes the suffix of the row's currency (`XEI` in CAD
becomes `XEI.TO`, `SPY` in USD `SPY.US`).

**Dates.** Map `date` to the **trade** date. If the export has a settlement
column, map it as `settle` (with `[formats] settle` when its format differs);
otherwise buy/sell rows settle on the standard cycle — T+1 since May 2024, T+2
before, T+3 before September 2017, options T+1 — counted in settlement days of
the listing's market (the Canadian calendar for `.TO`/`.V`/`.CN`/`.NE`, the US
one for `.US`, else the row currency), skipping weekends and holidays. With
`tax_date = "settle"` a sale on Dec 31 therefore lands in January. A settle
date before the trade date, or more than 31 days after it, is refused; one more
than 7 days after it is an ATTENTION line. Futures settle on the trade date, or
on the next settlement day under `futures_settle = "next_day"`. An option
closed at $0 on its expiry day is dated and settled that day (a $0 close posted
up to 7 days after the expiry is moved back to it). Dividend, tax, interest and
fee rows are dated `date`. For a crypto-only export set `[options] settle_on_trade_date =
true` (crypto has no settlement cycle).

Kraken and Coinbase timestamps are UTC; rows are dated in local time
(America/Toronto by default; set `[settings] local_timezone =
"America/Vancouver"` etc., or the `TAXJSON_LOCAL_TZ` environment variable
outside a project, to change it — the setting wins, and a change re-dates the
rows and re-keys crypto sends), so a fill at 03:00 UTC on January 1 belongs to
the previous tax year. USDC/USDT/DAI/PYUSD/GUSD are treated
as US-dollar cash in a Canada project (an approximation; a fill valued in US
dollars more than 2% off 1.00 USD is warned about, a CAD- or EUR-valued one is
not checked) and as property, like any coin, in a US project.

Crypto accounts: in a US project the wash-sale rule is **not** applied to
crypto — the IRS treats digital assets as property, not securities, so §1091
does not reach them and losses are allowed in full (`taxjson run` passes
`--no-wash` automatically and prints a note). In a Canada project crypto stays
superficial-loss-checked: s.54 covers any identical property.

**Sheltered transfers and ambiguous arrival dates.** With `transfers = true`
on a sheltered account, TRANSFER rows count as in-kind contributions /
withdrawals for the superficial-loss walk. But a broker TRANSFER date is an
**arrival** date, not necessarily an acquisition date — a custody move (broker
switch, cross-listing journal, account restatement) lands rows whose dates
mean nothing for s.54. The pipeline nets out the obvious custody churn
(zero-net clusters of the same symbol within days), but **refuses to guess**
whenever the answer could change a tax number: a transfer whose date falls
inside the ±30-day window of a taxable loss sale stops the run with
`AmbiguousTransferDateError` and instructions. You resolve it by *declaring*
what happened in a hand-written `.tt` file in the same account's `inputs/`
directory. A TRANSFER line ending in the token **`DECLARED`** is a deliberate
user statement and satisfies the guard — the token is opt-in precisely so
ordinary `.tt` TRANSFER rows (and json→tt round trips of broker rows) never
gain that authority by accident:

- **Custody move, history lost** — one line:
  `ACQUIRED 2024-09-16 09:30:00 XYZ.US 500 CAD <price> <total> ARRIVED
  2025-04-22` — expands to a BUYSELL dated the *true* acquisition day (real
  cost) plus a DECLARED counter-TRANSFER netting the broker's arrival leg.
  (The two expanded rows remain legal to write by hand.)
- **Broker restatement churn that already nets to zero** but sits too close
  to a taxable trade — usually needs nothing: when three or more symbols in
  the SAME account share zero-net clusters over a common few-day envelope,
  the pipeline detects the account-wide restatement itself and nets the
  whole event (one NOTE names it). Only a one-or-two-symbol churn needs a
  manual attestation — a declared zero-net pair
  (`TRANSFER <date> <time> <sym> N <cur> 0.0 0.0 DECLARED` + the same line
  with `-N`); the run prints this exact form when it refuses such a cluster.
- **Genuine in-kind contribution** — record it as a BUYSELL dated the
  contribution day (that *is* an acquisition by an affiliated person).

## Install

One line, no clone — installs the latest release into `~/.local/share/taxjson` with its own virtualenv and puts `taxjson` on your PATH (re-run to upgrade; `TAXJSON_CHANNEL=dev` tracks `main`):

```bash
bash -c "$(curl -fsSL https://taxjson.com/install.sh)"
```

Then `mkdir -p ~/taxes/2026 && cd ~/taxes/2026 && taxjson init --country canada` (or `--country usa`). See [REFERENCES.md](REFERENCES.md) for the CRA/IRS sources behind every rule and [docs/releasing.md](docs/releasing.md) for how releases are cut.

From source (development):

```bash
git clone https://github.com/taxjson/taxjson.git
cd taxjson
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

Core pipeline has no third-party runtime dependencies. Note that a full
`taxjson run` does reach the Bank of Canada (Valet API) for FX rates —
its legacy noon rate for 2007-05 to 2017-02, Yahoo Finance only for
earlier dates and currencies the Bank does not publish — and Yahoo Finance for un-priced crypto rows, on a cache miss
(`TAXJSON_OFFLINE=1` forbids it — see SECURITY.md for the complete
egress list). Optional extras:

```bash
pip install -e ".[fx]"          # yfinance + pandas: the FX fallback for dates before 2007-05-01 (the Bank's noon rate covers 2007-05..2017-02) and currencies the Bank of Canada doesn't publish, and every rate for a non-CAD base (the Bank of Canada path itself needs no extra)
pip install -e ".[web]"         # local web UI (`taxjson serve`) — included in [all]
pip install -e ".[xlsx]"        # taxjson-xlsx-to-csv, for brokers that only ship Excel
pip install -e ".[all]"         # everything
```

Or run `scripts/dev-setup.sh` for a one-shot venv with the `[web,fx]` extras, then `source setup.sh` to activate it.

## Everyday workflow (`taxjson run`)

For a configured project the entire pipeline runs from a **single command**. Set up a project directory once with `taxjson init --country canada` (or `--country usa` — the flag is required and shapes the scaffold's currencies, tax-date basis, and account folders):

```
taxjson.toml          # year, country, base_currency, and [accounts.*] sections
inputs/
  margin/ tfsa/ rrsp/ lira/ crypto/   # canada scaffold — one folder per account, drop broker CSVs in
  margin/ crypto/ roth/ 401k/         # usa scaffold
work/                 # intermediate per-stage artifacts (rebuildable; gitignored)
reports/              # all outputs land here
```

The full `taxjson.toml` schema and every file the pipeline reads are documented
in [Project layout and configuration](#project-layout-and-configuration) below.

Then, whenever you add new statements:

```bash
taxjson run           # full pipeline, full rebuild — never serves stale data (stages run in-process: no per-stage interpreter start-up)
taxjson run --fast    # incremental: mtime-cached stages with unchanged inputs are skipped
cat reports/margin.sum  # the per-account report
```

`taxjson run` orchestrates everything end to end: parse each broker CSV → apply corporate actions (prompting once for any new merger/spinoff election — saved to `inputs/<account>/manifest.json`, which you should COMMIT; pure ticker renames auto-elect) → merge and FX-convert to the base currency → run the ACB/FIFO gains + wash-sale engine → and write every report to `reports/`:

**Headless / GUI runs**: without a terminal to prompt (or with
`taxjson run --no-input`), unresolved elections never hang or crash the
run — the affected account is deferred, the pending events (with every
option, description, and required hint) are written to
`work/pending_elections.json`, and the run exits **3**. Inspect with
`taxjson elect --pending` (ready-to-copy `--set` lines; `--json` for
machines), resolve each with `taxjson elect <account> --set
<event_id>=<election> [--hint KEY=VALUE]`, and re-run. Exit codes:
0 = success, 1 = failure, 2 = usage, **3 = elections required**.

- `<account>.sum`, `<account>_wash.sum` — realized gains and wash-sale detail
- `wash_radar_<account>.rpt` / `.json` — superficial-loss "safe to sell at a loss?" advisor (the JSON sidecar carries absolute clear dates; the web UI computes countdowns from it at view time)
- `work/<account>_<broker>_transfers.json` — custody-transfer sidecar: TRANSFER rows the parse stage keeps OUT of the books (evidence, not tax events); `taxjson transfers` reads these
- `crosslistings.rpt` — flags cross-listed (`.TO`/`.US`) tickers the radar may not consolidate
- `fees.rpt` — trading fees by brokerage, with comparison stats
- `ccd.rpt`, `leaps.rpt` — cross-account covered-call / long-option views (`leaps.rpt` lists every long option close of any tenor; `taxjson leaps-sum` is the LEAPS-only figure; phantom-basis rows are excluded and counted, as in `ccd-sum`)
- `<account>_holdings.toml` — machine-readable positions (native + base-currency cost; a cost adjustment paid in another currency than its listing — a USD return of capital on a `.TO` stock — is restated in the listing's currency at the row's date for the native view, with a note, and the per-position acquisition/sell `trades` history). `cost_per_share` is `total_cost / quantity`, so for an option it is per contract; divide by `contract_multiplier` for the per-share price the `trades` show (a futures option carries the future's `contract_multiplier` its broker rows declared — IB's instrument list: CL 1000, ES 50 — and none when no row declared it, never the equity 100; a plain future is `asset_type = "future"`)
- `exports/` — SeekingAlpha / FastGraph / TradingView watchlist CSVs

When the year is over, [`docs/filing.md`](./docs/filing.md) is the
checklist that takes a project from "last export dropped in" to a filed
and locked return — which command proves each step, in order — and
`taxjson checklist` runs it: each step auto-detected, `--done ID` for
the ones only you can confirm, `--walk` to go through the open ones.

### Project layout and configuration

The full `taxjson.toml` schema (unknown keys warn at run start, with
did-you-mean suggestions). `taxjson init` writes the same keys in a
canonical, column-aligned layout with every optional switch present as a
commented default, so two years' files diff only where their values
differ (`diff ~/taxes/2025/taxjson.toml ~/taxes/2026/taxjson.toml`):

```toml
[settings]
year = 2026                    # tax year the pipeline reports on
country = "canada"             # canada | ca | usa | us — REQUIRED by every command
base_currency = "CAD"          # the country's currency: CAD for canada, USD for usa (another is refused)
tax_date = "settle"            # settle (CRA default) | trade (IRS default)
# futures_settle = "trade"     # futures & futures options (IB, generic): TRADE date (daily variation
#                              # margin settles the P/L) | next_day (clearing premium date)
# local_timezone = "America/Toronto"  # crypto UTC timestamps are dated in this zone
source_currencies = ["USD"]    # currencies you hold besides base_currency (FX rates fetched)
# province = "ON"              # canada tax-estimate default (ON/BC/AB)
#   Canada-only keys (province, option_*, foreign_return_of_capital, and the
#   [instalments] table / [estimate] deductions & carrying_charges) are refused
#   in a country = "usa" project, naming the key — never silently ignored.
# option_premium_timing = "grant"   # Canada (default): a written option's premium is a gain in
#                                   # the year WRITTEN (ITA s.49(1)); a buy-back is a loss in its
#                                   # own year; assignment folds into the shares. "close" nets
#                                   # premium and close together at the close instead.
option_grant_timing_since = 2025    # contracts written before this year keep close timing — the
#                                   # transition from books filed the old way. `taxjson init` writes
#                                   # it; set it ONCE (first year filed under grant timing) and keep
#                                   # it in every later project (unset, it follows `year` — warned)
# option_buyback_loss_superficial = false # either timing: treat the loss on buying back a written
#                                   # option as superficial when identical options are bought
#                                   # within 30 days and held (strict reading; default off — a
#                                   # closing purchase is not a disposition s.54 reaches)
# fx_cash_gains = true         # end-of-run s.39(1.1) FX-on-cash report (off by default)
# foreign_return_of_capital = "dividend" # IB "(Return of Capital)" from a NON-Canadian
#                                   # issuer: "dividend" (Canada default — ITA s.90(1)) or
#                                   # "acb". Canada-only: a US project always
#                                   # lowers basis (IRC s.301(c)(2)).
# corporate_distributions = ["XYZ.TO"] # Canada-only: Canadian issuers whose
#                                   # "Distribution" / "DIST ON" rows are a
#                                   # CORPORATION's payout (dated when paid), beyond
#                                   # the built-in split-share list — see
#                                   # "Income dating" below
# ric_january_dividends = ["SPY.US 2026-01-30"] # US-only: January fund/REIT
#                                   # dividends received on Dec 31 of the prior
#                                   # year (IRC s.852(b)(7) / s.857(b)(9)):
#                                   # "SYMBOL" (every January one) or
#                                   # "SYMBOL YYYY-01-DD" (that payment)

# Optional — inputs `taxjson estimate` (and the instalments
# current-year basis) uses when the flags aren't given. Only these
# keys belong here:
# [estimate]
# other_income = 120000
# other_losses = 0
# deductions = 0               # Canada: RRSP 20800, FHSA, RPP ... (full under AMT)
# carrying_charges = 0         # Canada: line 22100 (50% under the 2024+ AMT)

# Optional — Canadian tax instalments (`taxjson instalments`, and a
# compact block inside `taxjson estimate`):
# [instalments]
# basis = "current_year"       # current_year | prior_year | cra_reminder
# prescribed_rate = 0.07       # CRA's overdue-tax rate (optional: CRA's
#                              # published quarterly rates are built in
#                              # and used when neither key is set) — or,
#                              # since CRA resets it quarterly and charges
#                              # each day at the rate then in force:
# prescribed_rates = [{ from = "2025-04-01", rate = 0.08 },
#                     { from = "2025-07-01", rate = 0.07 }]
# withheld = 0                 # tax already withheld at source this year
# prior_year_net_tax = 55000   # last year's net tax owing, as CRA's
# second_prior_net_tax = 41000 #   instalment chart defines it: lines
#                              #   42000 + 42200 + 42800 (+ 43200)
#                              #   minus 43700 and the refundable
#                              #   credits — NOT line 48500, which also
#                              #   subtracts the instalments you paid.
#                              # Supply BOTH even on current_year: CRA
#                              # assesses interest on the least amount
#                              # the methods your figures support
#                              # require BY EACH due date, and they
#                              # also decide whether instalments are
#                              # owed at all. A placeholder 0 reads as
#                              # "I owed nothing" and suppresses both.
# paid = [{ date = "2026-03-16", amount = 15000 },
#          { date = "2026-05-20", amount = 12000,
#            note = "prior-year refund transferred to instalments" }]
#                              # a December prepayment of THIS year's
#                              # instalments needs tax_year = <year> on
#                              # its row (credited from January 1)

[accounts.margin]              # one section per folder under inputs/
type = "taxable"               # REQUIRED: taxable | sheltered
# brokerage = "questrade"      # enable `taxjson fetch` for this account
# account = "12345678"         #   (questrade: account number;
# query_id = "123456"          #    ibkr_flex: Flex query id instead)
# holdings = ["~/portoml-run/U1_holdings.toml",   # broker positions files
#             "~/portoml-run/U2_holdings.toml"]   # (`taxjson sanity` with no
#                                                 #  arguments; `run` warns)

[accounts.rrsp]
type = "sheltered"
transfers = true               # keep TRANSFER rows (contributions/withdrawals)
# plan = "rrsp"                # registered-plan kind when the name doesn't say (scan)

[accounts.crypto]
type = "taxable"
crypto = true                  # splices the crypto price filler into the pipeline
```

Files the pipeline reads and writes (all map files are optional):

| Path | Role |
| --- | --- |
| `inputs/<account>/` | Drop broker CSV exports here; any `*.tt` manual-history files too. Only `.csv` and `.tt` files are read: `taxjson run` stops on an Excel export (`.xlsx`/`.xls`) unless its CSV conversion (`taxjson-xlsx-to-csv FILE.xlsx -o FILE.csv`) sits beside it. |
| `inputs/<account>/manifest.json` | Saved corp-action elections — **commit this**. |
| `inputs/<crypto account>/sends.json`, `crypto_sends.tt` | Your decision for each crypto send that did not arrive on another of your exchanges (`self` / `gift` / `payment`, plus a note) — **commit it** — and the `.tt` file `taxjson crypto-sends --write` (and `taxjson run`) generates from it: one BUYSELL at fair value per gift or payment. Never hand-edit the generated file; a hand-written `crypto_sends.tt` is never overwritten. |
| `inputs/slips/` | Broker T5008 / 1099-B slip CSVs for `taxjson reconcile-slips` (the checklist looks here). Not an account folder — needs no `[accounts.slips]`. |
| `ticker.map` | Symbol rules, one per line: `GLOBAL from to` (plain rename, every stage), `TOBASE from to` (cross-listing consolidated in the base pipeline only), `JOURNAL from to` (Norbert's Gambit pair — consolidated AND netted in holdings), `DELETE from` (drop a pure artifact), `DISTINCT a b` (records that two look-alike listings are deliberately separate securities — a CDR vs its US underlying — and silences the scan's MAP-GAP nag and the `crosslistings.rpt` REVIEW; changes no symbol). For TOBASE pairs the holdings view keeps the listings separate **except** where the broker's own transfer rows prove a depot flip — the holdings export applies those evidenced quantities from the transfer sidecars (see `taxjson transfers`), so `JOURNAL` is only for intrinsically fungible classes like DLR's gambit units. Symbols are case-insensitive (upper-cased on load) and matched exactly — a suffix-less `GLOBAL QQOL QQNW` does not touch `QQOL.US`; notes go after `#`. Renames chain (`GLOBAL OLD.US NEW.US` + `TOBASE NEW.US NEW.TO` sends OLD.US to NEW.TO). `taxjson run` refuses a map with a malformed line, a rename cycle, one symbol renamed to two different targets, or a `DISTINCT` pair that the renames pool together. A rule on the shares also renames their options through the root (`BCE…C….US` → `BCE…C….TO`), except where that would land on a contract code the account already trades on the other listing — a USD-strike US option and a CAD-strike Montreal option are different property, so the US one keeps its symbol and the `.sum` DIAGNOSTICS name it. `taxjson init` writes a commented stub. |
| `distributions.map` | Non-cash fund distributions: `SYMBOL RECORD_DATE PER_SHARE` (negative = ROC). |
| `capital_gains_dividends.map` | Canada only: T5 box 18 capital-gains dividends booked as dividends: `SYMBOL YEAR-or-DATE all-or-AMOUNT [ACCOUNT]`. |
| `t1135.map`, `yf_ticker.map`, `sector.map`, `crypto_ticker.map` | Per-symbol overrides: T1135 domicile, yfinance spelling, timeline sectors, crypto Yahoo-collision fixes (`crypto_ticker.map` is read from the project root whatever the cwd; editing it re-prices under `run --fast`). |
| `phantoms.json`, `claimed_losses.txt` | Missing-basis phantoms (auto-applied); losses actually claimed on filed returns (`YEAR AMOUNT`). |
| `work/` | Intermediate per-stage artifacts and price/FX caches. Rebuildable; gitignored. |
| `reports/` | Everything you read: `<account>.sum`, `wash_radar_*`, `fees.rpt`, holdings, `exports/`. Rebuildable. |
| `filed/<year>.json` | Filed-year locks from `taxjson close-year` — **commit these**. |

### Subcommands

| Command | Purpose |
| --- | --- |
| `taxjson run` | Run the full pipeline, rebuilding every stage (the recommended everyday command — results always reflect current inputs, config and code). |
| `taxjson run --fast` | Incremental run: cached stages whose inputs, `taxjson.toml` and installed code are all unchanged are skipped. Input files and the project-root map files are compared by content (size + SHA-256), so a corrected export copied over with an older mtime (`cp -p`, `rsync -a`, unzip) still rebuilds; taxjson's own code is compared by content too (a change since the last complete run rebuilds everything); the rest of the cache is mtime-based. The cache invalidates itself on any of those changing; `--fast` trades that safety net's edge cases for speed. |
| `taxjson run --account NAME` | Re-run a single account. ⚠️ Skips cross-account wash-sale and cross-listing detection — those need a full run. |
| `taxjson sanity ACCOUNT\|FILE.toml\|ACCOUNT=FILE ...` | Cross-check open positions against externally produced holdings `.toml` files (portoml-style `[[holding]]`), per symbol (`--tolerance`, `--json`; exit 1 on any discrepancy). Bare items form one aggregate group (combined positions vs combined holdings — quick, but blind to a position sitting in the wrong account). `ACCOUNT[+ACCOUNT]=FILE[+FILE]` pairs specific accounts with specific files and is checked as its own group — many-to-many because a taxjson account can span several broker accounts (`margin=ibkr.toml+webull.toml`, or repeat `margin=…`) and one broker export can cover several accounts (`rrsp+lira=flex.toml`). Both forms mix freely. With no arguments the pairings come from `taxjson.toml` — each account's `holdings = [...]` — and `taxjson run` finishes with the same check as a warning; an account with open positions but no `holdings` is listed as `UNCHECKED` (the checklist's sanity step is then attention, not done). A holdings row with a quantity but no symbol, or a symbol with no quantity, is refused, not skipped. Option rows whose root the file spells differently (`RCI…` vs taxjson's `RCI.B…`) are matched through the row's `underlying` field. |
| `taxjson run --strict` | Promote per-account validation ERRORs (oversold positions, malformed rows) and an input file that parsed to 0 transactions to fatal instead of publishing reports with a DIAGNOSTICS banner. Recommended for CI/cron. |
| `taxjson run sum` (chaining) | Subcommands chain in one invocation, each with its own flags: `taxjson run close-year check-filed`. Note a chained `--json` command's stdout follows the earlier commands' progress output — pipe consumers should run the JSON command standalone. A failing command stops the chain and its exit code propagates. Option values that collide with command names are handled (`--account sum`); for the rare ambiguous positional, separate with `--`. |
| `taxjson close-year [--filed-dispositions CSV]` | Snapshot the current tax year's filing aggregates to `filed/<year>.json` — the filed-year lock. Commit it with your records. It also records what the next year needs for `taxjson handoff`: every sale, the positions and cost at Dec 31 (superficial-loss deferrals included), and the trades that settle in January. When the return was prepared with another tool, `--filed-dispositions` stores the sales it actually reported (CSV: `symbol,date,qty,proceeds,cost,gain`, optional `account`). `--force` keeps the filed dispositions of the lock it replaces (unless a new CSV is given) and warns when that lock recorded other totals. It refuses books whose last run did not finish (no reports, an unreadable `work/<acct>_base.json`). Without `--force` it refuses a year that has not ended, a year with no disposition and no income in the taxable books (a typo'd `year`), and books built with another option timing than `taxjson.toml` now says (that one even with `--force`). |
| `taxjson check-filed` | Recompute every filed year from the current books and report drift vs the locks; exit 1 on drift. A taxable account the books have but the lock does not (with activity in that year), or a locked account the books no longer have, is drift too; a locked account that is no longer a taxable account in `taxjson.toml` is reported, never recomputed from its old `work/` book. Each year is recomputed with the written-option timing its lock recorded. Dividends and payments in lieu are compared separately, and so are the amounts the export puts on each return line (Schedule 3 line codes, Form 8949 part totals), so a change that moves an amount between lines is drift even when the gain is unchanged; interest, foreign tax withheld and the FX gain on foreign cash are not locked (every OK says so). An unreadable lock is named and counts as a failure. A lock closed under the other country (every lock records its `country`) is refused by name and never recomputed under this project's law; a lock is recomputed on the date basis it recorded, and a note says when this project's `tax_date` or `option_buyback_loss_superficial` now differs from the lock's (its own reports for that year then differ from the filed return). A lock account entry that records none of the locked totals, or whose `form_lines` is not a table, is damaged, never OK. A bad `[settings]` value is refused as a settings error before any lock is checked; when the recompute itself fails on an input the child's own error is shown and the exit code is 2 (1 is drift or a damaged lock). Every full run also auto-checks (`taxjson run --strict` aborts on drift or an unreadable lock). |
| `taxjson events` / `divs` / `dil` / `trades` / `gains` / `fees` / `roc` / `leaps` | Per-transaction views over a look-back window — see below. |
| `taxjson transfers [ACCOUNT]` | Custody-transfer **evidence** view: depot flips, listing journals, broker migrations, and crypto withdrawals/sends (a send that arrived on another of your exchanges is a self-custody move; the rest are gift/payment candidates — see `taxjson crypto-sends`) — the TRANSFER rows the books deliberately exclude (basis comes from buy/sell history). Reads the parse-stage sidecars (`work/<acct>_<broker>_transfers.json`) plus in-book TRANSFERs from `transfers = true` accounts, with the broker's transfer type (InterDepot / Internal / ATON). `--json` for machines. |
| `taxjson crypto-sends [ACCOUNT] [--json]` | Every crypto withdrawal/send that did **not** arrive on another of your exchanges (a send is paired with an arrival of the same coin on another exchange within 3 days, losing at most 10% to the network fee), with your decision — `self` (your own wallet: no tax event), `gift` or `payment` (a disposition at fair market value; a gift under ITA s.69(1)(b)) — or PENDING. For each: the fair value per coin and in CAD with its source (the exchange's spot price when the row carries one — Coinbase; otherwise the Yahoo daily close `fill-crypto` uses, at the send date, times the Bank of Canada rate), and the ready line `BUYSELL <date> <local time> <COIN> -<qty> CAD <price> <proceeds> 0`. A Kraken network fee taken in the coin is already booked by the parser and is not in the quantity; a matched send that arrived short with no fee stated (a Coinbase Send carries its network fee inside the quantity) has the shortfall booked as a sale at fair value — a `<send id>-fee` line in `crypto_sends.tt`, in both countries (in a Canada project a stablecoin's shortfall is US-dollar cash, not a sale). Stablecoins (USDC/USDT/DAI/PYUSD/GUSD) are US-dollar cash in the books, so a gift/payment of one gets no sale line: the command shows the **currency gain** instead — value at the send-date Bank of Canada rate minus the average CAD cost of the USD-cash/stablecoin pool rebuilt from the ledgers — flagged when likely superficial (USD/stablecoins acquired within 30 days and still held), with the year's total. `--set ID=self\|gift\|payment [--note TEXT] [--price P]` records a decision (repeatable; `--price` when the price lookup fails), `--write` regenerates `inputs/<acct>/crypto_sends.tt` (idempotent; it warns when another `.tt` already sells the same coin, date and quantity). `taxjson run` asks at a terminal (self / gift / payment / skip) after the crypto parse and refreshes the file; headless it prints one note. Ids carry exchange, local date/time, coin and quantity — never a txid or address; refs are masked (`LG***`). A `checklist` step. |
| `taxjson roc-sum` | Return-of-capital / ACB-adjustment total per ticker (default: tax year). |
| `taxjson dil-sum` | Payment-in-lieu total per symbol (default: tax year) — DIVIDEND_IN_LIEU rows only, with each row's treatment: ordinary income (no dividend gross-up/credit or qualified rate), except, in a Canada project, a Canadian dealer's payment on a Canadian issuer's share, which ITA s.260 deems a taxable dividend (on the dealer's T5 box 24; counted in `divs-sum` and the estimate's eligible dividends). |
| `taxjson winners [PERIOD] [--top N]` | Per-ticker realized gains RANKED — biggest winners and losers over a window (default: tax year); options grouped under their underlying. A tax-year window (default, `tax_year`, `2025`) follows the project's `tax_date` in `winners`, `gains`, `ccd-sum`, `leaps` and `leaps-sum`: on the settle basis a Dec-31 trade that settles in January belongs to the next year, as in `sum`. These views refuse when `work/` was built for another year than `[settings] year`. |
| `taxjson ccd-sum` | Covered-call (short call) realized-gain summary per underlying over a window (default: tax year) — the windowed query twin of `reports/ccd.rpt`. Covers every account; the total is split into TAXABLE and SHELTERED parts when registered accounts contribute. |
| `taxjson leaps-sum` | Per-contract LEAPS summary — long option buys placed >3 months to expiry (default: tax year); only the long position's dispositions (a later write/buy-back of the same contract is covered-call P&L, in `ccd-sum`); the total is split into TAXABLE and SHELTERED parts when registered accounts contribute. |
| `taxjson instalments` | Canadian tax instalments: what each of the four dates (Mar/Jun/Sep/Dec 15) calls for under your chosen basis, what you have paid, and the **offset interest** plus **s.163.1 penalty** that follow from any gap. The current-year basis is driven by `taxjson estimate` itself (AMT included). Interest uses CRA's published quarterly rates (built in; `prescribed_rate(s)` overrides), credit interest runs from the later of the payment date and January 1, and net interest of $25 or less is not charged; CRA charges instalment interest only if it sent you a reminder for the year, which the report says. Configure `[instalments]` in `taxjson.toml`; `--json` for machines. |
| `taxjson estimate` | The realized-gains summary table followed by the marginal tax **estimate**: tax(other income + investment income) − tax(other income). Canada projects also get an **AMT check** (post-2024 rules: gains at 100%, no DTC, 20.5% over the exemption + provincial piggyback) — shown binding-or-not, with the top-up and 7-year carryforward when it binds. Canada: 50% inclusion, eligible gross-up/DTC, FTC from the books' actual TAX rows, ON/BC/AB (`--province`, or `province` under `[settings]`). `--other-income`/`--other-losses`, and for Canada `--deductions` (RRSP 20800, FHSA, RPP ...) / `--carrying-charges` (line 22100) (or the `[estimate]` config block, which `instalments` reads too), `--verbose` trace, `--json`. Planning numbers, never filing numbers. |
| `taxjson sum` / `list` / `divs-sum` / `trades-sum` / `fees-sum` | Roll-up summaries — see below. `list --date YYYY-MM-DD` shows positions AS OF that date (each account's books recomputed alone via the engine's `--as-of` cutoff, on the project's date basis — the settlement date unless `tax_date = "trade"`, so a sale traded Dec 31 that settles in January is still held at Dec 31, as in the gains year and `t1135`: per-account ACB — not the s.47 blend across taxable accounts that plain `list` and the return use — with in-account deferred wash and phantoms.json applied; the books are already ticker.map-consolidated, and the cross-account wash pass is not in it); plain `list` shows the positions at the end of the books (the header names the date); `list --negative` shows only negative-quantity positions — real shorts, or (in accounts that can't short) missed corporate actions / import gaps. Ends with a **FOR THE RETURN** block over the taxable accounts — Canada: one row per Schedule 3 line (line 4 shares & fund units 13199/13200; line 6 options, futures & other properties 15199/15300; line 7 crypto-assets 15200/15301 — 15199/15300 before 2025) with PROCEEDS, COST(ACB), OUTLAYS, GAIN and the superficial losses DENIED, on the Schedule 3 convention (a short sale's proceeds as PROCEEDS and its cover as ACB, sell commissions as outlays; a denied loss REDUCES the ACB shown so proceeds − ACB − outlays is the allowed gain, the denial going onto the replacement's ACB), plus the `fx-cash` estimate for line 15300; USA: Form 8949's own Part I/II (d) proceeds, (e) cost, (g) adjustment, (h) gain. Rows equal `form-export`'s line totals (each row rounded to the cent, as filed — when that differs from the gains files' unrounded total by a cent or more the block says so, and `--json` carries `engine_gain_unrounded`); `--json` adds the per-account split. |
| `taxjson shares [--options] [--taxable\|--sheltered] [--sort qty] [--json]` | Combined quantity held of each symbol across all accounts (post ticker.map, wash-adjusted where built) with a per-account breakdown and combined book cost; shorts net against longs. Option contracts only with `--options`; futures contracts are left out. Like `list`, it is the end of the books (the header says the date), not the tax year's Dec 31. |
| `taxjson option-boundary [--json]` | Written options whose write and close straddle a tax-year boundary, or that are open at year end: where the premium and any later amount land under ITA s.49 for the timing in force, and — using the `filed/` locks — whether a filed year needs a T1-ADJ (an assignment after the grant year was filed, s.49(4)). |
| `taxjson tax-logic [--country canada\|usa] [--ids] [--json]` | A short statement of every rule taxjson applies for the project's country, one line per rule, with the project's own settings filled in (`tax_date`, option premium timing and `option_grant_timing_since`, `futures_settle`, `foreign_return_of_capital`, `option_buyback_loss_superficial`) — read through the same resolvers the engine uses, so a value the run reads differently or refuses is refused here too: tax-year dating and settle dates (income by pay date; in Canada a Canadian trust's distribution and return of capital by the record date the export prints; in the US the January fund/REIT dividends you list on Dec 31), currency conversion, ACB pooling and identity, Schedule 3 lines, the superficial-loss rule including options, option premium timing and exercise, corporate-action elections, income lines, crypto, the reports, and which settings and commands the project's country refuses. tax-logic is the spec the code is tested against: every statement has a stable rule id (`CA-SL-02`, `US-WASH-01`, ...) that `--ids` shows and `--json` lists, and that the tests cite (see CONTRIBUTING.md). Outside a project, pass `--country`. |
| `taxjson spinoffs [ACCOUNT] [--json]` | Every spin-off in the books: parent and new security, ratio, the election (`taxable_deemed_dividend` is the Canadian default: a dividend equal to the new shares' fair market value, which is also their cost; `rollover_s_86_1` splits the parent's cost with no income, filed with the return, for spin-offs on CRA's list; you give the CAD cost moved to the new shares as `--hint allocated_acb_cad=`), the value per share used, what was booked (income and the new shares' cost), the broker's own value when it reported one (the default uses it when no value is given), and what is held now. In a US project the elections are `taxable_distribution_301` (§301 income at FMV) and `tax_free_355` (basis moved per Form 8937, `--hint allocated_acb=`). Flags a taxable spin-off booked at $0, a basis-allocating election with no allocated cost, a missing election or an ignored event; exit 1 when a taxable one needs attention. |
| `taxjson splits [ACCOUNT] [--json]` | Every split, consolidation and rename with holdings just before and after. Flags a split recorded twice (two sources, close dates), a no-op row, a result with a fractional share (expect cash in lieu), and events that also mention a cash or return-of-capital leg. Exit 1 on a likely double application. |
| `taxjson check-dates [ACCOUNT] [--all] [--json]` | Checks every trade and settlement date the parsers produced against what was traded: crypto any day and hour; futures and futures options Sunday evening to Friday afternoon (no Saturday); US stocks on NYSE days plus the overnight session (Sunday to Thursday from 20:00); options and Canadian listings on exchange days (a Sunday-evening SPX/SPXW/XSP/VIX Global Trading Hours fill is a note). IB fills outside the regular session are already re-dated to their exchange trade date (CME evening futures, Cboe GTH, the US overnight session, ASX and Asian venues: tax-logic CA-DATE-SESSION). Settlement: never before the trade or on a weekend, and normally the standard cycle on either the US or Canadian calendar (a broker's own cycle is a note; corporate events, expiry-day options and `.tt` lines are exempt from the cycle check). ERROR for impossible dates (exit 1), WARN for exchange holidays, NOTE for unusual but explainable dates. A `checklist` step. |
| `taxjson edge-cases [ACCOUNT] [--margin DAYS] [--json]` | Everything whose treatment turns on a boundary, with where it lands and why: trades that settle in a different year than they trade (and the year `tax_date` puts them in), dispositions in the last and first days of a year, written options and expiries across Dec 31, income paid around New Year, crypto near midnight, superficial-loss windows that span Dec 31 and denied losses carried into next year; then every taxable loss with an acquisition (any account) or a sale within `--margin` days (default 3) of day 30, with the day count on both date bases and a flag where the basis decides the verdict; and long calls bought inside a share loss's window (s.54 'right to acquire': replacement property when still held on day 30). A US project is explained with §1091: trade dates, no still-held test (so no sales near day 30 and no held-on-day-30 figures), long calls listed as warnings only, Form 8949 rather than Schedule 3, and no written-option year boundary. |
| `taxjson handoff [--prior PATH] [--json]` | Checks that this year's project starts from exactly what last year's return carried forward, using last year's `close-year` record (`[settings] prior_year_record`, or `filed/<year-1>.json` here). Checks: opening positions and cost at Dec 31 against last year's year-end books; every trade made last year that settles in January is booked here, once; no sale is reported in both years (a closed-year sale that is its own row here is a different sale); rows the two projects put on different sides of Dec 31 — income a trust's record date or a RIC entry moves, a row `local_timezone` re-dates, an overnight fill moved into January — are reported in neither or both years; written options carried out of last year on another premium timing than its record (taxed twice, or in no return). A record closed before its year ended is flagged as a partial-year snapshot. A cost difference is listed with the two consistent choices: keep last year as filed and open with the cost that return implied, or amend it and open with the corrected cost. A record closed under the other country is refused by name. Exit 1 on any problem; `checklist` runs it. |
| `taxjson checklist [--walk] [--done ID] [--skip ID] [--undo ID] [--quick] [--json]` | The filing checklist ([`docs/filing.md`](./docs/filing.md)) as a command: every step is auto-detected by running the command that proves it (run, sanity, find-missing-history, elect, audit, option-boundary, reconcile-slips, form-export, t1135, check-filed, git status); the steps no command can prove are confirmed with `--done` (marks live in `checklist.json`, commit it) and never hide a later finding — a step marked done whose detector finds a problem shows `[!]` (or `[b]` when the detector could not check) with the mark beside it and keeps the list open (`--skip` is the explicit "reviewed, accepted"); form-export is checked against `sum`'s FOR THE RETURN totals and the taxable realized gain. US projects get the US names (1099-B, Form 8949 / Schedule D) and `n/a` for Canada-only steps; a project with no taxable account gets `n/a` for the taxable-only steps. `--walk` visits the open steps one at a time. Exit 1 while anything is open. |
| `taxjson redact FILE... [--out DIR] [--also REGEX] [--check]` | Strips account numbers, names and contact details it recognises (plus wallet addresses and exchange transaction ids, and anything on the private denylist) while keeping every row shape (same-shape placeholders, consistent across the file) so a statement can be shared as a parser sample or bug report. Pattern-based, not a guarantee: **review the output before sharing** — the report lists the free-text lines to read. Writes `NAME.redacted.EXT`; never touches the input; refuses `.xlsx`/binary input (export CSV first); `--check` exits 1 when it finds something. |
| `taxjson sell-check SYMBOL ...` | Sell-side wash check: is selling this ticker **at a loss** today safe? **UNSAFE** when a registered account's recent buy it still holds would deny the loss on the whole position (LOCKED), or an open violation is backed by a registered account's in-window buy; **PARTIAL** when only some units are at risk (the line says how many; the rest of the loss stands); **ACTION** when a violation can be rescued by selling the taxable replacement before the deadline (Canada only — a US wash sale cannot be rescued, and a WASHED row is SAFE\* with the reason); **SAFE\*/SAFE** with the applicable caveats. Whether it *is* a loss at today's price is `harvest`'s job. `--json` for machines; exit 1 on UNSAFE or PARTIAL. |
| `taxjson buy-check SYMBOL ...` | Buy-side wash check: is buying this ticker today safe? **UNSAFE** when a loss was sold within the past 30 days (the rebuy cancels it — permanently if bought sheltered), with the safe-from date when one is determinable (violations defer to `wash-radar` rather than print a date that would invite an early rebuy); **SAFE\*** when buying merely extends an open wash window. Root-matched (`buy-check NU` covers `NU.US` and cross-listings, folding in `ticker.map` pairs); `--json` for machines; exit 1 on unsafe. |
| `taxjson audit [SYMBOL ...]` | The **authoritative justification** of every capital-gain figure: one block per disposition tracing the parsed broker row (nominal currency, original ticker, source file) through the ticker.map rename, the exact FX rate applied (provenance named, recomputed against the base books to the cent), the ACB/FIFO disposition math, and the wash-sale / superficial-loss determination with replacement lots resolved — ending in a tie-out against the pipeline's saved gains files and the full pool trace. Runs the same blended computation the pipeline runs, so the audited numbers ARE the filed numbers. `--summary` for one line per event, `--id/--date/--account` filters, `--json`; exit 1 when any cross-check disagrees. `--year Y` for a locked year (its `filed/Y.json`, or the lock `[settings] prior_year_record` names) recomputes it with the option timing and date basis the lock recorded, with a note. |
| `taxjson wash-radar` / `wash-sales` | Wash-sale radar (forward) and denied-loss report (backward). |
| `taxjson fx-cash` | FX capital gains on foreign-currency **cash** — foreign cash is property, so spending USD realizes the rate move since it was acquired. Canada: ITA s.39(1.1) with the $200/year de minimis; US: the §988 ordinary-income figure. A standalone report reconstructed from the taxable accounts' native books (`--events` for the per-disposal detail, `--json` for machines); changes NO other number. Set `fx_cash_gains = true` under `[settings]` to also print it (and write `reports/fx_cash.rpt`) at the end of every run — off by default. |
| `taxjson watch` | Cron-able change detector: reports only what CHANGED since the last watch run — new/changed/cleared radar advisories, moved clear dates, and (with `--harvest`) the harvestable-now loss total moving more than `--threshold` (default 100). Silent with exit 0 when nothing changed, so a cron line mails only on news; `--exit-code` exits 1 on changes for scripting, `--json` for machines. State: `work/.watch_state.json`; `--state PATH` gives a cron cadence its own baseline (daily and weekly lines can coexist). |
| `taxjson fetch [ACCOUNT ...]` | Download broker activity straight into `inputs/` — Questrade REST API and IBKR Flex Web Service, configured on the account (`brokerage` + `account`/`query_id` under `[accounts.<name>]`). Writes files the existing parsers already read; hand-exported CSVs keep working side by side. Questrade defaults to the whole tax-year window plus the superficial-loss margins (Dec 1 of the prior year through Jan 31 of the next, capped at today; `--year N` backfills a past year, `--from`/`--days` override the window); IBKR re-covers the Flex query's configured period. `--trim-overlap` drops rows your manual exports already cover, `--dry-run` previews. Credentials: `--refresh-token` (Questrade) / `--flex-token` (IBKR); `--positions` ALSO snapshots live Questrade holdings to `work/<account>_live_holdings.toml` (for `taxjson sanity`). Chain it: `taxjson fetch run`. |
| `taxjson scan` | Lint the project for common tax-efficiency mistakes: cross-listed Canadian dividend payers held via the US line in taxable/TFSA, US payers in a TFSA (unrecoverable 15% withholding), and ticker.map cross-listing gaps. `--online` probes yfinance for unmapped .TO twins. Exit 1 on findings. |
| `taxjson t1135` | CRA T1135 foreign-property helper: filing-threshold test + per-property/per-country tables. |
| `taxjson carryover` | Multi-year capital-loss carryforward/carryback ledger (Canada balance + T1A carryback candidates; US ST/LT worksheet). |
| `taxjson form-export` | Filing-shaped output: IRS Form 8949 / CRA Schedule 3 (default follows the country), or a TurboTax-importable TXF via `--form txf [--box A|B|C] --out gains.txf`. |
| `taxjson reconcile-slips SLIP.csv [SLIP.csv ...]` | Diff broker T5008 / 1099-B slips against computed dispositions before filing (exit 1 on mismatch); several slip files (one per broker) are reconciled together. |
| `taxjson help [COMMAND]` | Show top-level help, or help for one subcommand. |
| `taxjson find-missing-history [NAME]` | Report positions with missing cost basis (truncated buy history, or $0-basis corp-action shares) that distort a year's gain; pairs the project's `phantoms.json` already covers are listed apart (COVERED), not as work to do. See "Importing manual cost basis". |
| `taxjson elect` | Review, redo, or non-interactively set (`--set ID=ELECTION`) a corporate-action tax election. |
| `taxjson init --country canada\|usa [PATH] [--year YYYY]` | Scaffold a new project directory (config, currencies, and account folders per jurisdiction; `--force` to overwrite). |
| `taxjson harvest [SYMBOL ...]` | Unrealized gain/(loss) per open position at current prices — "if I sold this today, is it a loss?" Losses first, wash-radar advisory on each loss, `LT_IN` days-to-long-term for US projects. |
| `taxjson serve` | Launch the local web UI (needs the `[web]` extra). |

**Non-cash distributions (`distributions.map`):** Canadian ETFs declare reinvested (phantom) capital-gains distributions — usually each December — that never appear in broker CSVs yet raise your ACB; some funds publish return-of-capital factors only after year-end. Put one line per event in a project-root `distributions.map` (`SYMBOL RECORD_DATE PER_SHARE`, negative for ROC) and `taxjson run` converts them into ACB adjustments for every taxable account holding the fund on the record date (shares covered by `phantoms.json` count as held). The symbol is matched case-insensitively, through your `ticker.map` renames, and along ticker changes (the adjustment lands on the ticker that held the shares on the record date). Only the ACB side is booked: the distribution itself is income for the year on your T3/T5 slip, which taxjson does not add to the estimate or `divs-sum` — the run's NOTE reminds you. The per-share amount is a plain decimal and the date `YYYY-MM-DD` (anything else stops the run); a `0` is a placeholder and is not applied; a symbol and date entered on two lines are both applied (they add) with a warning.

**Capital-gains dividends (`capital_gains_dividends.map`, Canada only):** a split-share or mutual-fund corporation may designate part of a dividend a capital-gains dividend (T5 box 18, line 17400): a capital gain at 50% inclusion, with no gross-up or dividend tax credit. No broker export says which payments those are (IB prints "(Ordinary Dividend)"; RBC and Questrade a plain dividend), so the books carry them as dividends. Copy them from the slip (or IBKR's dividends report, "T5: Capital Gains") into a project-root `capital_gains_dividends.map`, one line each: `SYMBOL WHEN AMOUNT [ACCOUNT]`, where SYMBOL is the books' symbol (a bare root such as `LFE` covers only its Canadian listings — LFE.TO, not LFE.PR.B.TO or LFE.US), WHEN is a year (every dividend of the symbol whose tax date is in that year) or a pay date, AMOUNT is `all` or the box 18 amount in the dividend's currency with a decimal point (a decimal comma is refused) (the total over the matching payments, shared pro rata), and ACCOUNT restricts the line to one account (default: the taxable accounts). Example: `LFE.TO 2025 all`, `XTD.TO 2025-09-10 5.50`. `divs-sum` then lists them under CAPITAL-GAINS DIVIDENDS, apart from the dividend totals, and the Canadian estimate (`taxjson estimate`, and `sum` with `--other-income`) moves them from the grossed-up eligible dividends into capital gains. The ledger and ACB do not change (box 18 does not touch ACB). A line that matches no dividend, an amount above the matching dividends, or matches in two currencies stops the view with the line number. A US project refuses the file.

Query/report commands are detailed below; every command also takes `--help`,
and `taxjson --version` prints the installed version.

### Query & report commands

These read the artifacts of the last `taxjson run` (no recompute). Use the
global `-C DIR` to point at a project that isn't the current directory.

**Per-transaction views** take an optional `PERIOD` and an optional `ACCOUNT`
(omit the account for all accounts merged chronologically, each line prefixed
with the account; name one for pure round-trippable taxtext). `PERIOD` is a
look-back window (`30d` / `6w` / `3m` / `1y`), `mtd` / `ytd` (calendar
month/year to date), `all`, a literal year (`2024`), or **`tax_year`** (bind to
the config tax year — also `ty`). Omitted, it defaults to the config tax year:

**`taxjson events PERIOD [ACCOUNT]`** — all transactions over the window, in
native (pre-base-conversion) currency, taxtext format, oldest→latest. Prints
per-currency `TOTAL BUY / SELL / DIVIDEND` footers.

```
$ taxjson events 5d margin
BUYSELL  2026-06-26  14:23:05  SLV.US   100  USD  53.365  5337.50  1.00
DIVIDEND 2026-06-30  09:30:00  NVDA.US  100  USD  0.25    25.00

TOTAL BUY:      31,447.50 CAD, 29,894.61 USD
TOTAL SELL:     45,886.74 USD
TOTAL DIVIDEND: 1,577.79 USD
```

**`taxjson divs PERIOD [ACCOUNT]`** — `events` filtered to `DIVIDEND` +
`DIVIDEND_IN_LIEU`; footer shows the dividend total per currency.

**`taxjson trades PERIOD [ACCOUNT]`** — `events` filtered to `BUYSELL` +
`ASSIGN`; footer shows buy/sell totals per currency.

Both `trades` and `gains` accept **instrument-class filters**:
`--options`, `--equities`, `--futures`, `--puts`, `--calls`. They combine
with OR (`--equities --calls` = equities *and* calls), and no flag means all
instruments. A futures option (e.g. `F:CL251220P00053000.US`) is genuinely
both a future and an option, so it matches `--futures` **and**
`--options`/`--puts`.

**`taxjson fees [PERIOD] [ACCOUNT]`** — one row per fee-bearing trade in the
window (account, date, symbol, action, fee) plus a per-currency `TOTAL FEES`.
Note: this view
counts standalone `FEE` rows (monthly/market-data charges) that `fees-sum`, a
per-TRADE cost report, deliberately excludes — the two totals differ by those.
It is not the line 22100 figure: trade commissions are already in the ACB and
proceeds, and margin interest (INTEREST rows) is not listed here.

**`taxjson gains PERIOD [ACCOUNT]`** — realized gains in **native** terms
(pre-TOBASE consolidation, pre-currency-to-base), one row per disposition
(date, symbol, qty, currency, proceeds, cost, gain, days), with a per-currency
`TOTAL GAIN`. Crypto has no native gains file and is skipped.

**Roll-up summaries.** `divs-sum` / `trades-sum` / `fees-sum` take an
**optional** `PERIOD` (a window, `mtd`/`ytd`, `all`, or `tax_year`; default: the
config tax year) and optional `ACCOUNT`; a lone non-period argument is read as the account
(`taxjson divs-sum margin`).

**`taxjson sum`** — cross-account realized-gains tables in the base currency,
one row per account (stock / option / realized / dividend / PIL / fees). When
`taxjson.toml` declares both account types, the summary prints a **TAXABLE
ACCOUNTS** table and a **SHELTERED ACCOUNTS** table (each with its own
SUBTOTAL) followed by the **ALL ACCOUNTS** grand total. Every account must
declare its `type` — `taxjson run` refuses a config with an untyped account
(it would otherwise drop out of the return); gains files whose account is no
longer in `taxjson.toml` are shown in their own UNTYPED table.
Single-type projects and `taxjson sum <account>` keep the one-table layout.
Every table's total row is the exact sum of the rows above it, and every
row foots as printed: REALIZED = NON-OPT + OPTION (REALIZED is the gain
rounded once, OPTION takes the cent difference) and TOTAL = REALIZED +
DIVIDEND + PIL, the `reports/<account>.sum` GRAND TOTAL (each figure
matches the .sum to the cent; a grand total summed from cent-rounded rows
can differ from one rounded once by a cent or two). `--json` carries a per-account
`type` and a `subtotals` object alongside `totals`.

```
$ taxjson sum
TAXABLE ACCOUNTS
ACCOUNT    NON-OPT      OPTION       REALIZED     DIVIDEND    PIL        FEES       TOTAL
------------------------------------------------------------------------------------------
margin     24,310.55    -1,204.10    23,106.45    1,842.30    0.00       318.60     24,948.75
...
SUBTOTAL   ...

SHELTERED ACCOUNTS
...

ALL ACCOUNTS
...
TOTAL      31,905.20    -2,617.35    29,287.85    2,611.05    0.00       447.15     31,898.90
```

`TOTAL` = REALIZED + DIVIDEND. NON-OPT is every non-option disposition
(shares, units, futures and crypto); the Schedule 3 / Form 8949 line
split is the FOR THE RETURN block and `taxjson form-export`.

**Tax estimate** — **`taxjson estimate`** (the front door; also
`taxjson sum --other-income ...` to see it under the account table)
computes a marginal tax **estimate** for the
year's investment income, computed incrementally: tax(other income +
investment income) − tax(other income), so the investment income is
bracketed on top of what you already earn. Taxable accounts only.
`taxjson estimate` with no flags uses the `[estimate]` config (or 0 for
both amounts — pure investment-income bracketing).

```
TAX ESTIMATE — canada/ON, rates vintage 2026 (ESTIMATE ONLY, not filing numbers; taxable accounts only)

  Other income                     200,000.00
  Capital gains (taxable)           15,000.00  [30,000.00 realized - 0.00 other losses, x50%]
  Eligible dividends (grossed)       1,380.00  [1,000.00 x1.38, Canadian-listed]
  Foreign dividends                    500.00  [FTC 75.00 — actual TAX rows (capped at 15% of foreign divs)]
  Payments in lieu                       0.00

  Tax with investments: 72,598.76 (federal 44,729.50 + ON 27,869.26)
  Tax on other income alone: 64,721.98
  => ESTIMATED TAX ON INVESTMENT INCOME: 7,876.78 CAD  (25.0% of 31,500.00)
```

The block ends with NOTE lines naming what the provincial figure
included (ON: the surtax and the Ontario Health Premium, base vs with
investments), the phased federal BPA when it applies, the provincial
AMT arithmetic when AMT binds, and — when the project year has no
built-in rate table — which year's tables ran instead (a year before
the earliest table also says the post-2024 AMT shown did not apply).

- **Canada** (`--province` or `province` under `[settings]`; ON/BC/AB):
  `--other-losses` are prior-year capital losses in **full dollars**,
  netted against gains before the 50% inclusion (deducted below net
  income, so they do not restore a phased BPA). `--deductions` (lines
  20700-23500: RRSP, FHSA, RPP ...) and `--carrying-charges` (line
  22100) lower net and taxable income — other income first, then the
  investment income; the AMT base takes the deductions in full and the
  carrying charges at 50%. Deductions not entered are not modelled, so
  an RRSP year left at 0 overstates the tax. Canadian-listed
  dividends are treated as eligible (38% gross-up + DTC) — non-eligible
  dividends are not modelled; foreign dividends as ordinary income, credited (FTC) with the foreign
  tax the books actually withheld (TAX rows, capped at 15% of the
  dividends; the treaty 15% is assumed for an account whose books carry
  no TAX rows). A crypto account's dividends are staking rewards:
  ordinary income, no withholding, no foreign tax credit. Modelled: the
  federal enhanced BPA phase-down (full amount up to the 29% bracket,
  the minimum from the 33% bracket, linear between, on net income),
  Ontario's surtax and Health Premium (up to $900), and the provincial
  AMT — ON 24.63% of the federal excess plus ON surtax on it (2024+;
  2026 assumed until the form is out), BC 33.7% / 34.9% / 40.0% for
  2024 / 2025 / 2026, AB 35%. Foreign tax the federal tax cannot
  absorb goes to the provincial foreign tax credit (form T2036, limited
  to provincial tax x foreign income / net income). The estimate is
  signed: eligible dividends at a low bracket can show a negative
  figure — a saving on the tax of the other income. Not modelled: QC,
  low-income reductions, non-eligible dividends, a prior-year
  minimum tax carryover (T691 Part 8, line 40427, ITA s.120.2) — when
  AMT does not bind, a NOTE names the headroom such a carryover could
  use — non-refundable credits other than the basic personal amount
  (CPP/EI, Canada employment, age, pension, donations ...), the OAS
  recovery tax (s.180.2) and AMT adjustments outside the books (the
  s.110(1)(d) stock-option deduction, donated securities), the FX
  result on foreign cash (line 15300, `taxjson fx-cash`), T3/T5 slip
  capital gains, and — for `taxjson instalments` — CPP/EI payable on
  self-employment earnings (lines 42100/42120, which CRA adds to the
  instalments due); the output says so. See KNOWN_ISSUES.
- **USA**: single filer, standard deduction. ST gains are ordinary; LT
  gains and (assumed-qualified) dividends stack on top at the 0/15/20%
  brackets; losses net ST first, then LT, then up to $3,000 of ordinary
  income (a net-loss year shows a negative estimate — a saving), and
  that deduction also reduces net investment income; the carryforward
  shown counts as used only what taxable income absorbs (Capital Loss
  Carryover Worksheet line 4); NIIT 3.8% above $200k MAGI; no foreign
  tax credit (the withholding in the books is not credited) and no
  state tax.

Add `--verbose` (`-v`) for the **CALCULATION TRACE** — every bracket
slice, credit and surtax tier, side by side for the base and
with-investments runs, ending in the subtraction that produces the
estimate. It shows exactly where each dollar of investment income
landed in the brackets and what the gross-up/DTC did.

Rate tables live in `lib/tax_estimate.py` with a printed vintage —
they need an annual refresh, and the output says so. These are
planning estimates, never filing numbers.

**`taxjson divs-sum [PERIOD] [ACCOUNT]`** — dividends received per ticker over
the window (DIVIDEND rows, plus in a Canada project the payments in lieu
ITA s.260 deems dividends; the others are in `dil-sum`), with
per-currency totals split TAXABLE / SHELTERED when a registered account
contributes — the TAXABLE line is the figure to compare with the T5/T3 slips.
Each row counts in its tax year (see "Income dating"), so a Canadian ETF's
December-record distribution paid in January is in the December year, as on
the T3. A crypto account's DIVIDEND rows are staking rewards (ordinary
income): `divs-sum` names them on an "of which crypto staking" line, and
`sum` / the views / the crypto `.sum` label them as staking.
`winners` prints the same taxable/sheltered split under its ranking, and
`dil-sum` / `roc-sum` / `trades-sum` separate registered accounts the same
way (the T3 box 42 figure is `roc-sum`'s TAXABLE line; T5008 proceeds are
`trades-sum`'s taxable "sold" figure). Every summary TOTAL is the sum of
its printed (cent-rounded) rows.

**Income dating** (`taxjson tax-logic` states each rule with its id):

- A corporation's dividend, Canadian or foreign, and every payment in lieu
  belong to the year they are PAID (ITA s.82(1); US: the pay date).
- Canada: a **Canadian trust's distribution** belongs to the year it became
  PAYABLE (s.104(13)). A row the broker calls a distribution (Questrade/RBC
  "DIST ON ...", RBC activity "Distribution") on a Canadian issuer (a
  Canadian listing, or a CA ISIN) is dated by the record date it prints
  ("REC 12/30/24 PAY 01/06/25" is 2024 income) — in `divs-sum`, the .sum,
  the estimate, instalments and the tax-year window of the `divs` / `roc` /
  `events` views (the row still shows its pay date). Split-share corporations (BK, DF, DFN, DGS,
  ENS, FFN, FTN, GDV, LBS, LCS, LFE, PDV, PIC, PWI, SBC, SBN, WFS, XMF,
  XTD, YCM, and any row whose description says "SPLIT CORP") also say
  "Distribution" but are corporations: dated when paid, like any issuer
  you list in `[settings] corporate_distributions` (an entry covers every
  class and series of its root: `GHI.TO` also covers GHI.PR.B.TO). A
  foreign fund, and a row with no record date, keeps its pay date. A
  record date 92 days or more before the pay date is taken as an export
  error: the pay date is used and the console says so. A distribution its
  record date moves into another year than the payment (a December record
  date paid in January) is listed on the console as `ATTENTION: income
  year:` on every run of either project year — one of the two leaves it
  out, so make sure the return of the record year carries it. That includes every IB row: IB prints
  only the pay date and calls a trust's distribution a dividend, so a
  trust cannot be told from a corporation (the ex date of IB's accruals is
  not used); a December-record trust distribution IB pays in January
  stays in the pay year — compare with the T3.
- Canada: a **Canadian trust's return of capital** (T3 box 42) lowers the
  ACB when it becomes payable (s.53(2)(h)): on its printed record date, so
  a sale between the record date and a January pay date is on the reduced
  ACB (and any s.40(3) gain is in the record year). A corporation's
  (s.53(2)(a)) or a foreign issuer's return of capital lowers it when paid.
  IB prints no record date: a January-paid ROC on a Canadian trust is
  warned about — check the prior year's T3 box 42 and move it to Dec 31
  with the two `.tt` ADJUST lines the warning prints (the warning stops
  once both lines are in the books).
- Canada: a **payment in lieu** on a Canadian issuer's share paid by a
  Canadian dealer (IB's statement names Interactive Brokers Canada Inc.) is
  a taxable dividend (s.260(5)/(5.1)), as the dealer's T5 box 24 reports
  it; any other payment in lieu is ordinary income. US: a substitute
  payment is ordinary, non-qualified income.
- US: a fund (RIC) or REIT dividend declared in October–December and paid
  in January is received on Dec 31 (IRC §852(b)(7), §857(b)(9)). The
  exports cannot tell a fund from a company, so taxjson keeps the pay date,
  warns when a January dividend has an October–December ex or record date,
  and moves the payments you list in `[settings] ric_january_dividends` to
  Dec 31 of the prior year (a bare `SPY` is the fund's US listing SPY.US
  only — not a .TO listing or a preferred class of the same root).
- The slips (T5/T3, 1099-DIV) are authoritative; these rules make the
  planning numbers and the slip tie-outs agree with them.

**`taxjson trades-sum [PERIOD] [ACCOUNT]`** — per ticker: buy/sell counts, value
bought/sold, and fees, with per-currency totals (all accounts; when a
registered account contributes, the taxable accounts' "sold" figure is
printed under them).

**`taxjson fees-sum [PERIOD] [ACCOUNT]`** — trading-fee report by **brokerage**
(commission/fee totals with per-trade averages, $/share, %notional), converted
to the base currency — the same totals `taxjson run` writes to
`reports/fees.rpt` (which is grouped by brokerage only). Broken down **by
account** by default (`--no-by-account` for brokerage-only totals). A `PERIOD`
window scopes it (e.g. `taxjson fees-sum 2025`); `--json` emits machine output.
Its year is each fee's **trade** date, converted at that date's rate (as in
`taxjson fees`), so it differs from `taxjson sum`'s FEES column — which follows
the project's tax_date (settle in Canada) — by the fees of trades that straddle
Dec 31, and by cents of FX. Plain futures fees are their own bucket (not stock
fees, not counted in $/share).

Money is shown to 2 decimals; quantities and per-share prices keep full
precision. Rows with a missing/unparseable date are excluded with a warning.

**`taxjson list [ACCOUNT]`** — open positions per account, taken from the
canonical gains files' `inventory` (the wash-adjusted `<account>_gains_wash.json`
when the pipeline built it, else `<account>_gains.json`) — i.e. **after** `ticker.map` consolidation
(cross-listings like `AEM.US`/`AEM.TO` merged) and base-currency conversion, so
quantity and cost basis match the canonical pipeline (unlike
`reports/<account>_holdings.toml`, which keeps listings separate and native for
live-pricing tools — its `base_total_cost` is per-account and before any
superficial-loss adjustment, s.47 blend or `distributions.map` ACB
adjustment, as its `meta.base_cost_basis` says; its native `total_cost`
leaves the map adjustments out too). One row per (account, symbol) with quantity, base-currency
book cost, cost/share (per SHARE for an equity option — 100 a contract, as
harvest and the holdings report show it), and the position's start date;
fully-closed positions are omitted. Pass an account to scope to one.

```
$ taxjson list
OPEN POSITIONS — CAD, as of the latest data in the books (2026-09-21), basis: wash-adjusted  (...)

ACCOUNT   SYMBOL    QTY   COST       COST/SH   DEFERRED   SINCE
---------------------------------------------------------------------
lira      XEQT.TO   100   4,200.00   42.00     -          2024-11-03
margin    AAPL.US   60    600.00     10.00     150.00     2025-01-15
margin    BNS.TO    50    3,000.00   60.00     -          2025-02-01

3 position(s), total book cost 7,800.00 CAD
```

**`taxjson wash-radar [ACCOUNT]`** — the superficial-loss / wash-sale radar per
taxable account, recomputed **live as of today** (or `--date YYYY-MM-DD`), so
cooling-down windows reflect the current date rather than the last `taxjson run`.
Defaults to every taxable account (sheltered accounts are never `--taxable`
targets); pass an account to scope to one. The combined `sheltered_base.json` is
included automatically for cross-account detection when present. `--verbose` for
more detail; `--all` to also list CLEAR (no-risk) positions. Sections group by
advisory in fixed order (VIOLATION, BLOCKED, LOCKED, EXITABLE — loss OK only with a FULL exit, CAUTION — sheltered leg exited so a loss sale of any size stands unless re-bought within 30 days, COOLING, RISK, CLEAR). The
same reports are written to `reports/wash_radar_<account>.rpt` during `taxjson
run`. A trade is counted from its **trade date** (a sale made today settles
tomorrow but is already in the books), while the ±30-day windows run on
settlement dates, as in the engine. Whether a sale was a loss comes from the
engine's own gains files (the s.47 pool blended across taxable accounts, denied
losses added to cost, option cost folded in on exercise); sales outside the
project's tax year fall back to the radar's own per-account pool. The project's
`phantoms.json` openings are applied as in the gains pass. Whether a loss is
superficial follows the engine's per-holder rule: your taxable accounts (one
pool) and each registered account on its own back a denial only with units they
**bought inside the ±30-day window and still hold** — shares a registered
account held before the window never do, and in Canada a new short sale or
written option is not an acquisition (`country = "usa"` keeps §1091's re-short
rule, and an IRA purchase in the window locks the loss even after the IRA sold).
Each replacement unit backs **one** denial, as in the engine (CA-SL-08): a sale
split into fills, two losses, or a loss whose window closed long ago share a
rebuy, and units an earlier loss already used are not counted again — for an
existing loss or for a sale today (a US IRA purchase the engine already matched
is not "at risk" twice). Quantities on either side of a split are compared in
today's units, and a long call counts at its declared contract size (100 for a
standard equity option). A warrant, a call on an adjusted series (XYZ1) or a
futures option on the loss's contract is a **note to check by hand**, as the
engines flag it (CA-SL-14/15, US-WASH-14/15) — never a VIOLATION. A VIOLATION
names who must sell what to rescue the loss, by the last trade date on the
listing's own calendar (a TSX USD unit trades on TSX days); once that date has
passed it says the loss is denied, with no sell-by date. A LOCKED row states how
many of your taxable shares' loss a sale today would lose. A rebuy denies the
loss only on as many shares as it buys (BLOCKED and `buy-check` print the amount
per unit). A crypto or futures VIOLATION's last day is the settle bound itself or
the last trading day before it (both settle on their trade date). A short
position is worded as one (cover, re-short). A `.tt` line dated after today in a
settle-date project was traded on the last trading day that settles by it, and
is in the books from then. The radar's own pool (used outside the gains files'
year) books a Canadian trust's return of capital on its record date and floors
the ACB at nil (s.40(3)), as the engine does. `buy-check`, `sell-check`, `watch` and `harvest`'s
ADVISORY column read the same radar. Every verdict covers **this project's
accounts only** and says so: a purchase by your spouse or common-law partner or a
corporation you control (Canada: affiliated persons, s.251.1; US: IRS Pub. 550)
also denies a loss (tax-logic CA-PLAN-04 / US-PLAN-04).

**An affiliated person's trades** (spouse or common-law partner, a
corporation you control) are not an account type of `taxjson run`: the
engine applies them only when it is given them (`taxjson-gains
--affiliated`). In a project, declare that person's account as
`type = "sheltered"`: their purchases then deny your loss, for good (the
ACB addition belongs to the affiliated holder, s.53(1)(f)), but the account
also shows in the SHELTERED tables and the radar as if it were your
registered plan — read it as theirs (KNOWN_ISSUES; tax-logic CA-SL-04). The
US engine does the same: §1091(d) adds the disallowed loss to the basis of
the affiliated holder's replacement shares, so in your books it is reported
as permanently disallowed (give them the amount for their basis; tax-logic
US-WASH-16).

In a **US project** the radar applies §1091, not s.54 (tax-logic US-PLAN-01):
windows run on **trade** dates, and each recent loss's verdict is the **US
engine's own**, run on the same books as of the date — purchases in every
account, IRAs included, and no still-held test. A washed loss is listed as
**WASHED** (the disallowed amount is in the replacement's basis, or lost for
good through an IRA purchase); there is no VIOLATION and no "rescue" advice,
because no later sale undoes a wash sale, and `sell-check` never answers ACTION.
A long call bought in the window is a note, not a denial (US-PLAN-02).
This is **forward-looking** (what you can/can't sell or buy now); for a
record of wash sales that already happened, use `wash-sales` below.

**`taxjson wash-sales [ACCOUNT]`** — details each wash sale (superficial loss)
that **occurred** in the tax year and the loss the engine **denied** — which
`<account>.sum` otherwise folds silently into the ticker totals (a disallowed
loss just shows up as a smaller/zero gain, unlabeled). One row per denied
disposition (economic gain/loss, denied amount, allowed loss) plus a denied
total. Reads the canonical gains, preferring the cross-account
`<account>_gains_wash.json` (which also catches registered-account repurchases)
when the pipeline built it.

```
$ taxjson wash-sales
ACCOUNT   DATE         SYMBOL    QTY     PROCEEDS    COST        GAIN       DENIED   ALLOWED
--------------------------------------------------------------------------------------------
margin    2025-10-08   ZZA.US    120     9,840.00    10,320.00   -480.00    400.00   -80.00
margin    2025-10-17   ZZB.US    25      3,150.00    3,700.00    -550.00    550.00   0.00

2 wash sale(s); 950.00 CAD of losses denied.
```

A DENIED loss is added to the cost basis of the repurchased shares (recovered on
a later sale), except any amount permanently denied by a repurchase in a
registered account.

Add **`--explain`** to see *how* each denial was computed — the full ACB /
superficial-loss calculation trace (pool build-up, the triggering repurchase,
the ±30-day affiliated-balance window, and the disallowance math) instead of the
summary table. Country / tax-date / sheltered context come from the project.

```bash
taxjson wash-sales margin --explain     # full calculation trace for each wash sale
taxjson wash-sales --explain            # all accounts
```

(For tracing an arbitrary non-wash disposition, the standalone `taxjson-explain
--symbol XYZ work/<account>_base.json` remains available.)

**Options as replacement property** — a call option is "a right to
acquire" the shares, which ITA s.54 (closing words, para (i)) deems
identical to them. So in the Canada engine a **long call** on the same
shares, opened inside the ±30-day window of a loss on **long shares** and
still held at the end of day 30 (in any of your accounts, registered ones
included), is replacement property: the loss is denied at the contract's
size (100 shares, or the declared size of a mini — `x10` in a `.tt`), and an
option root that drops the share class (`RCI` calls for `RCI.B.TO`, `BRKB`
for `BRK.B`) counts for that class line; the denied amount is added to the **call's** cost (recovered
when the call is sold, or rolled into the shares if it is exercised). A call
held in a registered account makes that part permanent. A buy-to-close of a
written call acquires nothing and never counts.

The rule is one-way, by design:

- shares are **never** replacement property for an option's loss;
- an option's loss is deferred only by repurchasing the **identical
  contract** (same OCC symbol). A different strike or expiry on the same
  shares does not count;
- matching is suffix-exact (a call suffixed `.US` matches `.US` shares, not a
  `.TO` listing, unless ticker.map joins them) and follows ticker renames.

`wash-radar`, `buy-check`, `sell-check` and `edge-cases` apply the same
rule in a Canadian project. A put is a right to sell, so it is never replacement property: not
for shares, and not for a loss on covering a short (a new short sale
acquires nothing either). The experimental US engine does not enforce the
call rule yet; it prints a warning for each case (§1091 "option to
acquire"). The old `cross_asset` setting is retired and ignored.

Some rights to acquire are only **flagged** for a manual check, in both
countries (warn-only, the numbers do not change): a warrant or
subscription right on the loss shares (`right_vs_share_loss`), a call on an
**adjusted** option series (root + digit, e.g. `XYZ1` after a corporate
action on XYZ: its deliverable is not 100 shares; `adjusted_option_vs_loss`),
and a call on the loss's **futures** contract, whatever its spelling
(`F:CLG6` itself, its family root `F:CL`, or another prefix such as
`/CLG6`; `futures_option_vs_loss`; in a US project the note adds that a commodity
future is usually outside §1091). What these convert into is not in the
books, so the engine cannot size a denial.

**`taxjson t1135`** — CRA **Form T1135** (Foreign Income Verification Statement)
helper, for Canadian filers holding foreign securities. Answers the filing
question first: it replays the full history of every **taxable** account in base
currency and reports the **maximum total cost of specified foreign property at
any time in the year** — the ITA 233.3 test ($100,000 threshold; $250,000 for
the detailed method, from the form's instructions). The test sees these
brokerage books only: foreign property held outside them (a foreign bank
account or cash, certificates, foreign real estate) counts toward the same
threshold and must be added by hand. Before Dec 31 the figures run to the
last date in the books, a "below the threshold" verdict says "so far" (the
checklist keeps the step open), and the year-end column is headed with that
date. A long option still held after its expiry date is named (its cost is
still counted). The books must be in the base currency (the converted
`_base.json` files; native-currency rows are refused). If a filing is required, it prints per-property and
per-country tables (maximum cost in year, cost at Dec 31, income, gain/loss)
from the same books the rest of the pipeline reports on, with the project's
`phantoms.json` openings applied exactly as the gains stage applies them.
Registered accounts are excluded by law and never read. A plain futures
contract has no cost amount (nothing is paid to open one), so its notional
stays out of the cost columns and the threshold test; an option on futures
counts at its premium. An assigned written put's premium is deducted from the
shares' cost and an exercised call's cost added to them (s.49(3)/(3.1)), rows
sharing a timestamp follow the engine's order, a split inside a trade's settle
lag re-denominates it like the engine, and the gain column and year-end
position follow the project's `tax_date`. A superficial loss denied in ANY
year is in its replacement's cost (s.53(1)(f)), exactly where the engine put
it: `t1135` runs the engine once over the full history (with the registered
accounts as wash context and the project's option timing, as `carryover`
does) and replays each denial's addition (`--year-wash-only` skips that
pass, adds only the project year's denials and names what that leaves
out). A configured
taxable account with inputs but no books is refused, and books built for
another year are warned about. `--json` for machine-readable output.

```
$ taxjson t1135
Filing requirement (total-cost test, ITA 233.3):
  Maximum total cost of specified foreign property during 2025: 262,500.00 CAD on 2025-06-16
  => T1135 FILING REQUIRED (exceeds 100,000.00 CAD)
  => Detailed method (Part B) required (reached 250,000.00 CAD)

SYMBOL  | COUNTRY | MAX COST IN YR | COST AT DEC 31 | INCOME | GAIN(LOSS) | NOTES
--------+---------+----------------+----------------+--------+------------+------
AAPL.US | USA     |      98,000.00 |      49,000.00 | 132.00 |  12,000.00 |
...
```

Domicile is classified by market suffix (`.US` → USA, `.L` → GBR, `.AX` → AUS;
`.TO`/`.V`/`.CN`/`.NE` → Canadian, i.e. not foreign property). Since domicile —
not listing exchange — is what T1135 cares about, interlisted names can need a
`t1135.map` override in the project root (a BOM is fine; symbols are matched
case-insensitively; an override follows the symbol through a ticker change,
and one that matches nothing in the books is warned about; a COUNTRY that is
neither an ISO 3166 alpha-3 code nor CA/CAN/CANADA/EXCLUDE is ignored with a
did-you-mean warning). A foreign listing whose rows carry a Canadian ISIN
(IB stamps the issuer's country) is named in a warning until you map it:

```
# t1135.map — SYMBOL COUNTRY (ISO-3 code, or CA/EXCLUDE for "not foreign")
ENB.US   CA      # Canadian corp held on NYSE — not specified foreign property
GLXY.TO  USA     # foreign corp listed on TSX — still specified foreign property
```

Symbols with no market suffix (typically exchange-held crypto) are
bucketed as country `CRYPTO` and counted toward the threshold: crypto held
on a foreign exchange is generally specified foreign property, so map each
one in `t1135.map` (`SYMBOL <ISO3>`, or `SYMBOL CA` for a Canadian
platform) once you have checked where it is held. Country `??` marks only
an unknown market suffix, for manual review. Amounts are **cost** (ACB-style) — correct for
the threshold test and the "maximum cost amount" columns; the category-7
detailed method's month-end **fair market value** boxes need your broker's
statements, which this tool does not fetch. Not tax advice.

**`taxjson form-export`** — renders the year's computed gains in the shape the
forms want, so filing day is transcription rather than arithmetic. The form
follows the project country (override with `--form 8949|schedule3`); `--csv
FILE` writes importable rows, `--json` the raw report.

- **`--form 8949`** (US): one row per disposition chunk — description,
  derived acquisition date, sale date, proceeds, cost, **code W** with the
  disallowed wash-sale amount as the column (g) adjustment, and the allowed
  gain in (h) = (d) − (e) + (g) (each row foots on its rounded cents, so
  the part totals and the TXF agree) — split into Part I (short-term) / Part II
  (long-term) with the Schedule D totals per part. Pick the 8949 box (A–F)
  yourself from whether the broker reported basis on your 1099-B. §1256
  contracts — futures, options on futures and broad-based index options
  (SPX, XSP, NDX, RUT, VIX and their weekly roots) — are **not** on Form
  8949: they are kept out of the rows, the totals and the TXF, and listed
  in a **FORM 6781 BY HAND** section with their P/L (the 60/40 split and
  year-end marking are not modelled). Cells are rounded half-up to the cent.
- **`--form schedule3`** (Canada): per-security rows — units, acquisition
  year, proceeds of disposition, ACB, outlays, gain(loss) — routed to the
  Part 3 line for the property type: **line 4** publicly traded shares and
  fund units (13199 / 13200), **line 6** options, futures and other
  properties (15199 / 15300 — T4037 lists options there), **line 7**
  crypto-assets from the `crypto = true` accounts (15200 / 15301; for 2024
  and earlier returns crypto goes on 15199 / 15300), with per-line totals.
  A futures contract is booked on its settled P/L, not its notional (the
  notional never changes hands): the P/L of each close, commissions
  included, is converted at that closing leg's rate and shown the way the
  broker's T5008 shows it — a gain as proceeds with ACB 0, a loss as ACB
  with proceeds 0, no separate outlays.
  Sell-side commissions on long sales are re-split into the outlays
  column (gain unchanged), and so is a written option's commission under
  grant timing: the premium is shown GROSS as proceeds with the write
  commission as an outlay. Under close timing a write's commission, and a
  short sale's opening commission, stay netted into the proceeds with no
  outlay (the closing row does not carry them) — same gain, slightly
  lower proceeds than a broker slip. Every row foots — proceeds − ACB − outlays = the allowed
  gain: a superficial loss denied on the row shows as an ACB reduced by the
  denial, noted per row (the denied amount goes onto the replacement
  property's ACB; a registered-account or affiliated-person denial is noted
  as permanent for this return). A short sale shows what it brought in as
  proceeds and the cover as ACB (a close-timing write for a net debit: no
  proceeds, the debit as an outlay; under grant timing it shows its premium
  and its commission). Units are the contracts or shares disposed of, at
  full precision: under grant timing a written option and its buy-back in
  the same year count once; a buy-back of an earlier year's write (grant or
  close timing) is a disposition of its own. A net commission rebate (a
  negative IB or Questrade commission) is not an outlay: it stays netted in
  the proceeds, so the OUTLAYS column is never negative. Each cell is
  rounded half-up to the cent and the ACB is the row's footing residual,
  never below 0.00.

Both refuse rows in another currency than the return's (CAD for Schedule 3,
USD for 8949/TXF — the native `*_raw_gains.json` beside the converted file),
a `--base-currency` other than that currency, and a file that is not a gains
file (no `transactions` list, or a pre-gains stage file); a disposition with
no currency is warned about. A row whose date is not a string or whose money
field is not a number is refused with the file and row named (every report
reader checks work/ rows this way). Before the tax year has ended the report
says the figures are year-to-date (as `sum`'s FOR THE RETURN block does), and
both print the per-row rounding note `sum` prints. `--csv` is written
through a temporary file, so a failed write leaves the previous CSV intact.

Both read the wash-adjusted gains (the allowed numbers a return reports).
**Phantom-basis (tainted) dispositions are not in the rows or totals** — their
cost is unknown — but they are never dropped silently: a stderr warning names
each one with its proceeds, the text report ends with a **MANUAL REPORTING
REQUIRED** section, the CSV carries them as `MANUAL` rows (blank cost and gain),
the JSON as `manual_reporting_required`, and `taxjson checklist` keeps the
form-export step open until they are reported by hand.

**`taxjson reconcile-slips SLIP.csv`** — diffs the broker's official slips
(CRA **T5008**, IRS **1099-B**) against the computed dispositions, per
symbol: disposition count, quantity, proceeds, and (when the slip carries
cost) basis. CRA/IRS machine-match returns against these slips — run this
before filing and decide, for every flagged row, whether it's a tool-side
problem (dropped rows, missing statement months) or a legitimate,
documentable difference (per-broker box-20 book value vs blended ACB, broker
lot method vs FIFO). Slip headers are matched loosely (`Security`/`Box 16`/
`Box 21`/`Box 20` T5008 spellings work as-is; so do `Symbol`/`Quantity`/
`Proceeds`/`Cost or other basis`, the T5008 box headings and French
headings too; an exact heading wins and two columns that both look like
one amount — `Proceeds` and `Proceeds of disposition`, `Quantity` and
`Qty` — are refused as ambiguous; a ticker column beside a security-name
column is fine). A slip symbol without a market suffix
matches the computed listing of that root (slip `AAPL` ↔ computed `AAPL.US`);
when the books hold two listings of one root (a CDR `AMZN.TO` and `AMZN.US`)
the row is `AMBIGUOUS_LISTING` until the slip CSV names the suffix. Broker
option descriptions (`XYZ 21MAR25 50 C`, `CALL XYZ03/21/25 50`, a strike
with thousands separators `5,000.00`), share
classes (`BRK B`) and the project's `ticker.map` renames (slip `KGC` ↔ books
`K.TO`) are matched. A blank proceeds cell beside a cost is nil proceeds (an
option that expired worthless); a worthless expiry with no slip row is
`NO_SLIP_EXPECTED`, not a failure. Under grant timing an option written this
year and still open at the year end is `NO_SLIP_EXPECTED` too (the premium is
reported in the write year, the broker's slip comes in the close year), and a
close-year slip whose proceeds include an earlier year's write premium
reconciles with a note. A slip row with amounts but no symbol, or
an unreadable quantity, is counted as not reconciled. Net-of-commission slips
are detected and noted. Slips aggregated per type code (IBKR's SHS/OPC/FUT
rows, "Various") cannot be compared — transcribe a per-security CSV. Books
built for another tax year are refused with a rebuild message. Exits 1 when anything doesn't reconcile — cron and pre-filing
checklist friendly. Slip cost differences are reported as *notes*, not
mismatches, because they're often correct (document them, don't "fix" them).

**`taxjson carryover`** — a multi-year **capital-loss carryforward /
carryback ledger** over the taxable accounts' full history (same engine as
the yearly pipeline: wash/superficial-loss adjustments and phantom handling
included; a parity test guarantees each year's net matches the pipeline's
own per-year run). Per year with any disposition:

- **Canada**: net gain/(loss), a running net-capital-loss carryforward
  (losses carry forward indefinitely), and for each loss year the still-open
  **3-year T1A carryback candidates** — capped at each earlier year's
  remaining net gain and never double-counted across loss years.
- **US**: net short-term / long-term, the up-to-$3,000 ordinary-income
  offset (assumed used when available), and the running ST/LT carryover per
  the Schedule D worksheet ordering.

Amounts are 100% gains/losses — Canada applies the 50% inclusion rate on
Schedule 3 / T1A, not here. The ledger shows what the *transaction history*
supports; record what you actually claimed on filed returns in a
`claimed_losses.txt` at the project root (`YEAR AMOUNT` lines, `#` comments
— auto-detected, or pass `--claimed FILE`) and it's folded into the running
balance. **Units:** Canada — the 100% capital loss applied that year, i.e.
the line 25300 amount divided by the inclusion rate (x2 at 50%); US — the
Schedule D line 21 deduction against ordinary income as far as taxable
income absorbed it (line 4 of the next year's Capital Loss Carryover
Worksheet — 0 in a year with negative taxable income; not the line 6/14
carryover coming in). `1,234.56` and `$1,234.56` are accepted, and so is a
UTF-8 BOM; a line that cannot be read (or whose year is not a plausible
tax year) is named, left out, and makes the checklist's carryover step
need attention. A claim equal to the filed (per-row-rounded) Schedule 3
loss consumes the ledger's unrounded loss exactly. A claim
recorded for a year the books show no loss for waits for a later loss, but
only one of the next 3 years (the T1A carryback reach, ITA 111(1)(b));
past that it is reported as unmatched. If the history's first year has
dispositions, the ledger warns that pre-history balances aren't reflected,
and rows before the project year are flagged as rebuilt from this
project's books (opening `*_start.tt` lots plus whatever prior-year exports
are in `inputs/`) and possibly partial — check them against the filed
returns. A year before the project year that has a close-year lock — the
project's own `filed/<year>.json` or the `[settings] prior_year_record` of
the per-year layout — takes the lock's **filed** figure instead (Canada: the
total filed with another tool when the lock has one, else the Schedule 3
gain lines; US: the Form 8949 Part I / Part II gains); a later locked year
is compared with its lock, and an unreadable lock is named. Rows after the
project year (a few January trades in this year's inputs) are partial:
they offer no T1A carry-back and the carryforward stops at the project
year. Every year uses the project's settings, its income dating
(`corporate_distributions`) included. The net per year counts dispositions
plus the T5 box 18 dividends named in `capital_gains_dividends.map`; other
slip capital gains (lines 17400/17600, US Schedule D line 13) and the
line-15300 FX gain on foreign cash (`taxjson fx-cash`) are not in it.
`--json` for machine output.

### Option premiums across a year end (`option_premium_timing`)

Under ITA s.49(1) writing an option is a disposition: the premium is a
capital gain **in the year the option is written**. A later buy-back is a
capital loss in its own year (IT-479R para 29 for calls, para 32 for
puts); expiry adds nothing; an
exercise or assignment folds the premium into the share leg instead and
the grant year is amended (s.49(3) for a call, s.49(3.1) for a put;
s.49(4)). Contracts written and closed in
the same year give the same total either way — only year-straddling
contracts differ. Canada projects use this timing by default; `"close"`
nets at the closing transaction (the US §1234 convention, which the US
engine always uses). `option_grant_timing_since` keeps contracts written
before that year on close timing, so a premium that was open at a prior
year end is not taxed nowhere when you switch. Set it once and never bump
it with `year`: left unset it defaults to the project year, which moves —
a 2026 project would put a contract the 2025 project taxed on grant timing
back on close timing and tax its premium again — so `taxjson run` warns
until it is set, and `taxjson init` writes it. `taxjson option-boundary`
lists every straddling contract and says whether a filed year needs a
T1-ADJ; a contract written in a LOCKED year (`filed/<year>.json`, which
now records the timing that return used) but kept on transition close
timing here is flagged ATTENTION, as is a contract past its expiry date
with no expiry/assignment row in the export. Positions and the holdings export keep the economic
book cost of an open written option; only the year attribution moves. `harvest`, which asks what closing
today would book, leaves a premium already recognised at the write out of the cost, so its UNREALIZED
on a grant-timed written option is the whole buy-back cost (the loss the close books).

Whether the loss on buying back a written option can be *superficial* —
denied because identical options were bought within 30 days and held,
permanently if a registered account holds them — is not settled: s.54
needs "a loss from the disposition of a property" and a closing purchase
disposes of nothing. The default does not apply the rule to buy-backs,
under grant or close timing alike (a contract written before
`option_grant_timing_since` included);
`option_buyback_loss_superficial = true` takes the strict reading.

### Return of capital (ROC)

Return of capital is **not income** — it reduces your position's adjusted
cost base, deferring tax to the eventual sale. The pipeline handles the two
cases differently:

1. **Broker-labeled ROC rows are reclassified automatically.** Any
   Questrade/RBC/IB row whose description says "RETURN OF CAPITAL" is parsed
   as an `ADJUST` that reduces ACB by the cash received (tagged `roc`),
   instead of a dividend. IB reversal rows net out sign-correctly. Note this
   changes regenerated history: income totals drop and later gains rise
   relative to the old (incorrect) dividend treatment. Two IB exceptions:
   a **payment in lieu** labeled "(Return of Capital)" is paid by the share
   borrower and is always income (`DIVIDEND_IN_LIEU`), never an ACB
   reduction; and ROC from a **non-Canadian issuer** (ISIN country not
   `CA`) is booked as a foreign dividend (ITA s.90(1)): a US "return of capital"
   (a distribution beyond earnings and profits) is a cost reduction for
   Canadian purposes only when the issuer really reduced its paid-up
   capital (s.53(2)(b)(ii)); s.90(2)/(3) are foreign-affiliate rules. Set
   `foreign_return_of_capital = "acb"` in `[settings]` to keep the ACB
   treatment instead.
2. **Fund/ETF distribution ROC needs one manual entry per fund per year.**
   Most Canadian ETF/REIT ROC is not labeled in any broker CSV — the split
   only arrives months later on your T3 (box 42). Enter it as a `.tt` ADJUST
   line dated in the tax year, with a **negative** amount (ACB reduction):

   ```
   ADJUST 2025-12-31 12:00:00 XEI.TO CAD -184.23   # T3 box 42 ROC
   ```

**When a ROC lowers the ACB** (see "Income dating"): a Canadian trust's ROC
on the record date the Questrade/RBC row prints (s.53(2)(h), payable); a
corporation's or a foreign issuer's on the pay date; in a US project always
the pay date. IB rows carry no record date, so a January-paid ROC on a
Canadian trust stays on its pay date with a warning.

Inspect what's recorded with `taxjson roc <period>` (every ADJUST row,
taxtext, including the `distributions.map` adjustments `run` books) and
`taxjson roc-sum` (per-ticker capital returned, split into
broker-classified, manual and `distributions.map` rows; it warns when a
symbol has a book ADJUST and a map row on the same date — the same ROC
entered twice). If cumulative ROC ever pushes a
position's ACB below zero, the excess is a deemed capital gain under
s.40(3): the engine books it in the distribution year (a qty-0 row with
no proceeds — T4037: enter 0 on line 13199 and the gain on 13200) and
resets the ACB to nil. A return of capital while you are SHORT is a
compensation payment you make: it lowers the cover's gain.

### LEAPS views

A **LEAPS** position here is a **long option buy placed more than 3 calendar
months before expiry** (calls and puts alike). Contracts qualify over your
FULL history, so an exit inside the viewing window shows up even when the
qualifying buy predates it; short premium and near-dated buys never qualify.
Both views report the ENGINE's numbers — lot-matched, superficial-loss/
wash-adjusted, base currency — read from the wash-adjusted gains files.

- **`taxjson leaps PERIOD [ACCOUNT]`** — closed LEAPS positions: one row per
  disposition (date, contract, qty, proceeds, cost, gain, days held) plus
  the total realized gain. Partial closes appear as realized; still-open
  contracts are absent (no mark-to-market).
- **`taxjson leaps-sum [PERIOD] [ACCOUNT]`** — the gain summary: per
  contract, quantity closed, proceeds, cost, gain, and expiry, plus the
  total (period defaults to the tax year).

### Tax-efficiency scan (`taxjson scan`)

Lints the whole project for placement mistakes the pipeline can see:

- **US-LISTING** — a cross-listed Canadian issuer (per `ticker.map`, or a
  `.TO` sibling seen anywhere in your data) held via its **US listing** in a
  taxable account or TFSA while receiving dividends. Hold the `.TO` line
  instead: clean eligible-dividend treatment, no USD conversion drag.
- **MAP-UNUSED** (a note, never a finding) — `ticker.map` rules whose
  FROM symbol matches nothing in the parsed sources. The check is
  root-aware: a rule with no stock rows is still live when option trades
  carry its root (`BCE251121C00050000.US` needs `TOBASE BCE.US BCE.TO`),
  and a rule reached through another rule's target (a rename chain) is
  live. It judges a rule the way the engine applies it: FROM must match
  exactly, so a suffix-less `GLOBAL QQOL QQNW` is reported (with a hint
  to write `QQOL.US`) when the books only carry `QQOL.US`.
  Unused rules are harmless; prune only when you know the symbol will not
  return.
- **CDR-PAIR** (`--online`) — a `.TO` line whose exchange name says CDR
  (Canadian Depositary Receipt, e.g. UNH.TO over UNH.US): same issuer but
  NOT a listing equivalent — fractional, CAD-hedged, floating ratio. Never
  map it; record `DISTINCT UNH.US UNH.TO` in ticker.map to silence the pair.
- **MAP-BAD?** (`--online`) — a defined GLOBAL/TOBASE/JOURNAL pair whose two
  sides name DIFFERENT issuers per the exchanges (or pair a CDR with its
  underlying) — a typo'd pair silently merges two companies' ACB pools.
  With `--online`, held listings are also clustered by exchange-reported
  issuer name, which catches different-root dual listings (BTG.US/BTO.TO)
  that same-root scanning can never see.
- **TFSA-US-DIV** — a US-domiciled dividend payer inside a **TFSA**: the 15%
  US withholding is unrecoverable there. RRSPs are treaty-exempt (never
  flagged); taxable accounts can claim the foreign tax credit.
- **MAP-GAP** — the `ticker.map` comprehensiveness prover: any root seen
  under BOTH a `.TO` and `.US` listing anywhere in the project (holdings,
  dividend history, the map itself) with no GLOBAL/TOBASE entry
  consolidating them. Unmapped pairs split the ACB pool and can blind the
  wash radar. Add `--online` to also probe yfinance for a `.TO` twin of
  every unmapped US-listed dividend payer — candidates to verify, not
  verdicts (same root can be a different issuer).

Registered-plan kinds are inferred from a plan word that is a whole token
of the account name (`tfsa`, `rrsp2`, `my-tfsa`; not `admiral`); override
per account with `plan = "tfsa"` in `taxjson.toml` when a name doesn't say
(an unknown `plan` value is warned about and ignored). An option counts as
a sighting of its underlying's listing for MAP-GAP / US-LISTING. Exit 1 when findings exist, 0 on a clean scan — cron-friendly.

### Tax-loss harvesting (`taxjson harvest`)

"If I sold this today, would it be a loss — and may I claim it?" One table
over every taxable account's OPEN positions: base-currency book cost (from
the wash-adjusted books, so deferred superficial/wash losses are already
folded into the basis), current price (IBKR → yfinance → price cache, with
the source labelled), unrealized gain/(loss), and — on every LOSS row — the
wash radar's advisory with a view-time countdown to the clear date.

```bash
taxjson harvest                 # all open positions, harvestable losses first
taxjson harvest AAA.TO BBB.US   # just these symbols
taxjson harvest --json          # machine-readable
```

```
HARVEST — unrealized open positions, CAD, basis: wash-adjusted  (all amounts CAD; PRICE marks its source: ^ IBKR, + yfinance, * cache; losses first; ADVISORY from the wash radar)

ACCOUNT   SYMBOL   TX_QTY   SH_QTY   COST/SH   PRICE      EXIT@        UNREALIZED   PCT      VERDICT   TX_ADD               SH_ADD               ADVISORY
                                     CAD       CAD        native       CAD
--------------------------------------------------------------------------------------------------------------------------------------------
margin    AAA.TO   100      25       12.4000   10.8500+   12.6480CAD   -155.00      -12.5%   LOSS      2026-07-08(-7d)      2026-07-02(-13d)     LOCKED(clears:2026-08-09,+25d)
margin    BBB.US   50       -        109.1000  130.4240^  -            1,066.20     19.5%    GAIN      2026-03-02(-135d)    -                    -
--------------------------------------------------------------------------------------------------------------------------------------------
TOTAL     -        -        -        -         -          911.20       13.6%    -         -                    -                    -

HARVESTABLE LOSSES (CAD, cumulative): now 0.00 | <=7d 0.00 | <=14d 0.00 | <=30d 155.00
```

Everything is base currency — `PRICE` is the native quote already
FX-converted, with its source marked (`^` IBKR live, `+` yfinance,
`*` price cache). The `HARVESTABLE LOSSES` line schedules the paper
losses by when the radar says they become claimable: `now` (no lock),
then cumulatively within 7/14/30 days from the clear dates — estimates
at today's prices, and any new buy on either side pushes a clear date
out. The TOTAL row's `PCT` is the total unrealized over the gross cost of the
listed positions (a short's credited proceeds do not net against long cost).
An input harvest cannot read stops it (exit 2). `RISK` losses count as claimable **now** — the superficial-loss rule
needs an acquisition inside the ±30-day window, not mere sheltered
ownership — but carry a forward caveat: an affiliated buy (a DRIP is
the classic) within 30 days *after* the sale denies the loss
permanently, so pause sheltered adds first. A `LOCKED` loss counts as
claimable now except for the units a registered account bought in the
window and still holds (`LOCKED(at-risk:4/100sh,…)`). When the radar
reports are older than the books (after `run --account`), harvest runs
the radar live. With `--options`, a contract that ticker.map renamed
onto another listing's code is quoted as the contract actually held.
Accounts marked `crypto = true` are **excluded by default** (the price
chain serves stock snapshots; crypto symbols mostly fail to price) —
pass `--crypto` to include them. A coin is quoted under the same Yahoo
spelling the books were priced with (the built-ins plus the project's
`crypto_ticker.map`). In a **US** project a crypto account's losses are
outside the wash-sale rule (US-WASH-13): they count as claimable now and
the ADVISORY reads `no-wash-rule(crypto)`; a Canadian crypto loss stays
under the superficial-loss rule like a share. A `VIOLATION` whose rescue
deadline has passed (`VIOLATION(deadline-passed:…)`) is not claimable
now: a sale today waits until the registered account's last in-window
buy ages out (31 days), or has no clear date without the sheltered
books. An LSE (`.L`) quote that does not say whether it is in pence or
pounds — a price-cache entry written before the unit was recorded, or a
tier that reports no unit — is left out with a warning, never valued as
pounds. In a US project an open short shows `ST` under `LT_IN`: covering
it is short-term (US-HOLD-03). `--json` carries the scope note
(`scope_note`) like the radar's.

`EXIT@` is the **native-currency price at which a full exit today books no
base-currency loss** — the position's base book cost converted back at today's
FX rate, plus a 2% buffer for fees, slippage, and FX drift between the quoted
rate and the actual fill/settlement conversion. Shown on long LOSS rows only:
sell above it and the sale books no loss even after the buffer.

Option positions (e.g. LEAPS) are also excluded by default. Pass
`--options` to include them: they price **only** through the IBKR tier
(plus the cache) — yfinance option chains are too stale/wide for
illiquid strikes, so with no TWS/Gateway running the contracts are
listed as unpriced rather than marked from a bad source. Option rows
show `PRICE` and `COST/SH` in per-share premium terms (`UNREALIZED`
carries the contract size the rows declare — a `.tt` line's `xN`, a
broker's multiplier — and ×100 when none is declared), a `DTE` days-to-expiry column
appears, and premium currency follows the underlying's listing. Note
the radar does not track option contracts — rebuying the *same*
contract within 30 days of a loss sale still triggers the
wash/superficial rule even though ADVISORY shows `-`. Futures and
futures options never appear (no live tier serves them).

`TX_QTY` is the taxable account's shares; `SH_QTY` is the total held across
sheltered accounts (RRSP/TFSA/IRA/…). `TX_ADD` / `SH_ADD` name the last
ACQUISITION on each side as `DATE(-Nd)` (signed days: negative = past,
positive = future — ADVISORY clear dates count up). Both sides extend
the wash window, which is why the radar's clear date can be later than
the sheltered trades alone imply: a **taxable** rebuy within 30 days of
a loss sale defers the loss into the new shares' basis and restarts the
clock (`TX_ADD` makes that visible), while a **sheltered** add within 30
days either side makes the loss **permanently denied** (CRA s.40(2)(g)
via the affiliated trust; IRS Rev. Rul. 2008-5 for IRAs). Both use the
engine's `last_acq_date` (any add, even to an old position — a DRIP buy
last week counts). Gains files written before the field existed show
`-` with a warning — never an approximation, which could only overstate
the age and green-light a denied loss; re-run `taxjson run` to refresh.
The wrapper passes every sheltered account's gains file automatically;
standalone callers use `--sheltered work/rrsp_gains.json` (repeatable).

### Standalone analysis tools

Run directly (not `taxjson <sub>`); pass the relevant JSON/cache. All emit to
stdout.

| Tool | Purpose |
| --- | --- |
| `taxjson-fees-sum --cache work --year YYYY --to CAD --rates work/to_base.csv` | Trading fees by brokerage with comparison stats (avg/median per trade, $/share, %notional). Add `--json` for machine output. |
| `taxjson-lint-crosslistings --taxable work/margin_base.json --sheltered work/sheltered_base.json --map ticker.map` | Flag cross-listed (`.TO`/`.US`) tickers the wash radar may not consolidate (also run automatically → `reports/crosslistings.rpt`). A listing counts when the books hold its shares or options on it; share positions follow splits; `DISTINCT` pairs are OK. |
| `taxjson-sum-gains work/margin_gains.json` | The per-account gains summary behind `reports/<account>.sum`. |

## Importing manual cost basis

When an export doesn't reach back to when a position was opened (or corp-action
shares landed at $0 basis), the engine is missing acquisition data and a year's
gain is wrong. Find the affected positions:

```bash
taxjson find-missing-history            # all accounts, current tax year
taxjson find-missing-history margin     # one account
```

Rows marked **AFFECTS `<year>`** have an in-year sale drawing on the missing
basis — fix those before filing. (Rows "not relevant" only touch other years.)
A short the broker itself declares is listed apart as a real short, not
missing history: RBC's `SHORT.` description, or an IB sale whose Trades
`Code` says it OPENED a position (`O`, or `C;O` — closed the long and opened
the short in one order). The other way round, an IB sale coded `C` (closing)
with no position in the data to close sold something bought before the data:
it is always listed, also for an option or a future (which are otherwise
skipped as normal sell-to-open), with IB's own `Basis` for it, and
`option-boundary` calls it a missing purchase instead of a write.

To fix one, import the real trades from your brokerage confirmations as a
TaxTrak **`.tt`** file in the account's input folder — e.g. add lines to
`inputs/margin/margin_start.tt` (any `*.tt` in the folder is picked up). One
space-separated line per trade:

```
BUYSELL  <date>  <time>  <symbol>  <qty>  <currency>  <price>  <total>  <fee>
```

| field | notes |
| --- | --- |
| `date` / `time` | `YYYY-MM-DD` / `HH:MM:SS` (time REQUIRED — the parser's field positions depend on it; `09:30:00` is fine). A `.tt` line has a **single date**, used as both the trade and settlement date — enter the date matching your `tax_date` setting (**settlement date** when `tax_date = "settle"`). Lines with the same date and time are taken in file order, as rows of a broker export are (tax-logic CA-DATE-14 / US-DATE-13): write a same-day sell and rebuy in the order they happened. |
| ADJUST lines | `ADJUST date time symbol CURRENCY amount` — FIVE payload fields, not the BUYSELL shape (negative amount = ACB reduction, e.g. T3 box-42 ROC). |
| `symbol` | with exchange suffix — `AGI.TO`, `XYZ.US` (match how the account labels it; options use OCC, e.g. `ALA250117C00036000.TO`). Upper-cased on read (`agi.to` is `AGI.TO`); a suffix that is not a market (`XYZ.TSX`, `XYZ.CA`) is a warning naming the line, since it would be a separate ACB pool. Futures lines (`F:`/`/`) skip the total-vs-qty×price typo check unless the line ends with the contract size (below). |
| `x<size>` | optional, last on a BUYSELL/ASSIGN line: the contract size — `x1000` for a CL futures option, `x50` for ES, `x0.1` for a micro crypto future. The typo check then uses qty×price×size (an equity option is checked at 100 without it), and the size is kept on the row for the holdings export. `taxjson-convert-tt book.json` writes it for futures rows and for any option whose size is not 100. |
| `qty` | shares — **positive = buy, negative = sell** |
| `price` | per-share price |
| `total` | net cash amount: **buy = qty×price + commission; sell = qty×price − commission** (your confirmation's net amount), written as a positive number. A sale whose commission exceeds its gross (a penny option close) has a NEGATIVE total, qty×price − commission (e.g. `-8.95` for a 0.01 close with a 9.95 commission): it is read when the line's commission explains it (a futures line also needs its `x<size>`); any other negative sell total is refused (a cash-signed `-2000` used to be booked as negative proceeds). `0` there leaves the excess commission out of the loss and warns. |
| `fee` | commission (optional) |
| income facts | optional `key=value` tokens at the END of a DIVIDEND / DIVIDEND_IN_LIEU / TAX / ADJUST line: `record=YYYY-MM-DD` (the record date that dates trust income and ROC), `ex=YYYY-MM-DD`, `label=distribution`, `dealer=CA` / `issuer=CA` (a Canadian dealer's payment in lieu is an s.260 deemed dividend), and on ADJUST `type=roc` (a return of capital, listed by `roc`). `taxjson-convert-tt book.json` writes them, so a json→tt→json round trip keeps them. Not part of the row id. |
| INTEREST lines | `INTEREST date time CURRENCY amount` — income with no symbol, e.g. a T5 box-13 interest figure a broker's trade export does not carry (Webull): `INTEREST 2025-12-31 16:00:00 USD 1149.27`. |
| FEE lines | `FEE date time CURRENCY amount` — a charge is POSITIVE, a refund or rebate NEGATIVE (the sign `fees` and `fx-cash` read; the opposite of a cash-statement sign). |

Example — a confirmation for "bought 100 XYZ.US @ $45.00, $5 commission":

```
BUYSELL  2022-05-10  09:30:00  XYZ.US  100  USD  45.00  4505.00  5.00
```

Then rebuild and re-check:

```bash
taxjson run
taxjson find-missing-history margin     # the fixed tickers should drop off
```

`taxjson run` merges, sorts, and de-dups the `.tt` rows into the account. Lines
starting with `#` are comments. Numbers use a decimal point; a thousands comma
(`1,234.56`) is fine, a decimal comma (`48,24`) is refused. Validate a single file first with
`taxjson-convert-tt --account margin inputs/margin/margin_start.tt` (it
prints the parsed JSON and errors loudly on a malformed line). The reverse
direction, `taxjson-convert-tt book.json out.tt`, writes each row's
**settlement** date (`--date-basis trade` for a trade-basis project) and
counts the rows whose two dates differ; `taxjson events PERIOD ACCOUNT` (one
account, pure taxtext) does the same on a settle-basis project. Two identical
`ACQUIRED` lots arriving the same day are two arrival legs (their counter
transfers are combined, not de-duplicated away).

**Duplicate rows across files.** Overlapping exports of one account (a
re-download, a 2025 export that runs into January next to the 2026 one) hold
the same rows twice, and the books keep each row once. The rule, which the
fees report uses too:
- every parser that sees the broker account (the IB statement's account,
  the Questrade `Account #` and RBC `Account` columns, the Webull preamble,
  a generic mapping's account column or `[broker].account`) stamps each row
  with it, hashed; rows of two DIFFERENT broker accounts are never one row,
  whatever the files look like (two accounts with the same holdings get the
  same distributions);
- two identical rows in ONE file are one row, unless the parser marked them
  as separate fills (`[fill #2]`);
- the same row in two exports is one row when the files overlap as copies:
  on the dates both files cover, one file's rows are a subset of the
  other's, and they share at least two rows;
- identical lines in two `.tt` files are separate records, so both are
  booked; a `.tt` line equal to an exported row stands for that row (each
  exported row absorbs at most one `.tt` line), and the result does not
  depend on the order the files are listed in;
- anything else (the files share only that one row, the files disagree on
  the dates they both cover, or a `.tt` line equals an exported row) is booked
  once, and `taxjson run` prints `warning: ATTENTION: dedup: ...` with both
  file names. When the files disagree, the line also names the rows only one
  of them holds: a newer statement that restated a row (a commission refund
  folded into the trade, a cancelled trade) leaves the older version booked
  too, so keep only the newer file. If they really are two trades, enter the
  second one as a `.tt` line. If a `.tt` line was typed into two files,
  delete one copy;
- a `.tt` line that repeats an exported row by hand never has the row's id
  (the export's description and settle date differ), so both are booked:
  `taxjson run` prints `warning: ATTENTION: dedup: <file>.tt line ...
  repeats the exported row ...` when the symbol, quantity, money and trade
  or settle date match. Delete the `.tt` line if it is that trade;
- one broker account's export placed under two `inputs/<account>/` folders
  is booked in both: `taxjson run` prints an ATTENTION line naming the two
  taxjson accounts (the same file copied into two folders is named file by
  file, for every broker and for `.tt` files), and `run --strict` stops.

### When you can't get the real cost basis

For positions where no confirmation is recoverable, mark the missing opening as
a **phantom** instead. A phantom is a synthetic zero-info opening balance: it
lets the engine drain the position without inventing a cost, and any
disposition drawing on it is pulled out of the gains total and surfaced
separately under `manual_reporting_required` (so it's flagged for you rather
than silently mis-computed). Import the real basis wherever you can *first* —
phantom only what's left over. To emit a candidate file for the leftover
truncated-history rows:

```bash
taxjson find-missing-history --gen-phantoms phantoms.json    # all accounts
taxjson find-missing-history margin --gen-phantoms phantoms.json
```

This writes only the truncated-history rows (positions that go negative — what
phantoms fix); $0-cost corp-action rows are left out because those need a
merger/spinoff basis, not a synthetic opening. By default it emits only rows
affecting the tax year; add `--all-history` for every candidate. It never
overwrites an existing file (a reviewed `phantoms.json` keeps your prunes and
hand-added pairs): write to a new file and merge, or pass `--force` (the old
file is kept as `<file>.bak`). A same-day Norbert's-gambit pair folded by a
ticker.map `JOURNAL` line is not offered as a candidate.

`taxjson run` (and t1135, wash-radar, option-boundary) still applies an entry
the detection would not propose — a short the broker marks as a short sale
(RBC `SHORT.`, IB code `O`) or an option the broker never coded closing — but
prints an `ATTENTION` line for it, and `find-missing-history` lists it under
"REMOVE from phantoms.json" (the checklist flags it). A buy the broker marks as
covering a short (RBC `COVER SHORT.`, IB code `C`) with no short in the data is
reported as missing history too: the short was opened before the data.

**Review the file and delete any entry that's actually a real short position.**
Then, if it's saved as `phantoms.json` at the project root, `taxjson run`
auto-detects it (like `ticker.map`) and feeds it to every account's gains run
via `--incomplete-history` — editing the file re-runs gains. Entries are keyed
by account name: after renaming an account in `taxjson.toml`, update the
`"account"` of its entries too (`taxjson run` stops and names any entry whose
account is not in `[accounts]`). In the manual
pipeline, pass it yourself: `taxjson-gains --incomplete-history phantoms.json …`.

## Quickstart (manual pipeline)

The commands below drive the pipeline stage by stage — handy for one-off files or scripting. For a configured project, prefer `taxjson run` above.

```bash
# 1. Convert a broker CSV to normalized JSON. --country picks the one
#    country-specific parse choice (IB foreign return of capital: ITA
#    s.90(1) dividend in Canada, a basis reduction in the US).
taxjson-brokerage --brokerage ib --account margin --country ca activity.csv > margin.json

# 2. Merge each ACCOUNT'S broker files into one per-account JSON.
#    Sheltered accounts must NOT be merged into the taxable input —
#    RRSP/TFSA/IRA trades aren't part of the taxable ACB/FIFO pool
#    and pass to taxjson-gains via `--sheltered` instead (so they
#    still feed cross-account wash-sale detection).
#    --dedup / --validate are opt-in; without them merge2 just concatenates.
taxjson-merge2 --dedup --validate margin.json > taxable.json
taxjson-merge2 --dedup            rrsp.json   > sheltered.json

# 3. Compute gains for a tax year. --taxable enables wash-sale /
#    superficial-loss detection (ITA s. 40(2)(g) in Canada, IRC §1091
#    in the US); --sheltered passes registered-account context for
#    cross-account matching (Rev. Rul. 2008-5 / affiliated-balance).
taxjson-gains --country ca --year 2025 --taxable \
    --sheltered sheltered.json taxable.json > gains.json

# 4. Summarize for filing
taxjson-sum-gains gains.json       # realized gains/losses (reads gains.json)
taxjson-sum-income combined.json   # dividends/interest (reads merged JSON)
```

Each command takes `--help`. The full pipeline is composable — outputs from one stage feed the next, all in JSON.

## Documentation

- [taxjson.com](https://taxjson.com) — the website; [`docs/deck/taxjson-deck.pdf`](./docs/deck/taxjson-deck.pdf) — a twelve-slide overview
- [`REFERENCES.md`](./REFERENCES.md) — the ITA / CRA / IRC source behind every rule, and every deliberate non-feature
- [`KNOWN_ISSUES.md`](./KNOWN_ISSUES.md) — known limitations and deferred fixes
- [`docs/filing.md`](./docs/filing.md) — the filing checklist: every step from frozen inputs to the `close-year` lock, with the command that proves it
- [`docs/releasing.md`](./docs/releasing.md) — the dev/release scheme (`main` vs `vX.Y.Z` tags) and how a release is cut
- [`CHANGELOG.md`](./CHANGELOG.md) — release history
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — how to run tests and submit changes
- [`SECURITY.md`](./SECURITY.md) — vulnerability reporting policy
- [`examples/`](./examples/) — synthetic CSV + walkthrough

## Verification

The numbers this tool emits go on real returns, so correctness is
defended in layers rather than by tests alone:

- **The broker's positions are the external check.** `taxjson sanity`
  compares the positions the books say you hold with the positions your
  broker's own export says you hold, account by account; `taxjson run`
  ends with it when `taxjson.toml` names the holdings files. Internal
  reports can all agree and still be wrong — this is the check that
  found a phantom 8.4-share residue and a split option class that every
  other report had accepted.
- **The audit command is the authority.** `taxjson audit` recomputes
  every disposition from the parsed broker row through FX, ACB/FIFO
  and the wash determination, and ties each figure out against the
  pipeline's saved gains — exit 1 on any disagreement. Every other
  filing command (`sum`, `carryover`, `form-export`, `t1135`) is
  cross-checked against it.
- **Property fuzzers.** Three seeded generators assert conservation,
  determinism and ordering laws over thousands of random books per
  run (engine, custody transfers, settle-lag/split interactions);
  they are what still finds engine bugs after seven audit rounds.
- **Mutation testing** of the wash-sale regions, so a boundary that
  no test pins gets noticed.
- **Independent audits.** Eight rounds plus a pre-release security and
  correctness audit of the public surfaces — adversarial reviews,
  hand-computed statutory scenarios, parser coverage against real
  exports, the installer and the privacy gate — with every confirmed
  finding reproduced, fixed and pinned. `KNOWN_ISSUES.md` lists what was
  deliberately left, with the reasoning.
- **Nothing personal leaves the machine.** `scripts/check-pii.sh` runs
  in every gate and as the `pre-push` hook, fails closed, and reads a
  private denylist kept outside the repository; `taxjson redact` strips
  the account numbers, names and contact details it recognises from an
  export — review the output before sharing it. One exception is by
  design: git publishes the author, committer and tagger name and e-mail
  of every pushed commit and tag. The hook refuses any other identity
  that hits the scan but only warns about your own configured
  `user.name` / `user.email`, so set a pseudonym and a
  `users.noreply.github.com` address before pushing if you do not want
  your name public.
- **Filed-year locks.** `taxjson close-year` snapshots a filed year;
  every later run recomputes it and reports drift.

`scripts/ci.sh` runs the whole gate locally (see CONTRIBUTING.md).

## Status

Pre-1.0. The core pipeline (parse → merge → gains → summarize) is stable and covered by 2,100+ tests and three property fuzzers, but expect occasional breaking changes to CLI flags and JSON field names until 1.0.

The tests run against synthetic fixtures and check that the code implements the rules as written here — they are not an assurance that the rules themselves are correctly interpreted for your situation, and no output has been reviewed by a tax professional. The planning commands (`estimate`, `instalments`, the AMT check, `fx-cash`) are explicitly estimates: they say so in their own output, and they should be checked against your assessment or your accountant before you rely on them. Report anything that looks wrong.

## Contributing

PRs welcome. See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for the test-driven workflow — every fix should land with a regression test.

## License

MIT — see [`LICENSE`](./LICENSE).
