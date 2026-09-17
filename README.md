# taxjson

[![tests](https://github.com/taxjson/taxjson/actions/workflows/tests.yml/badge.svg)](https://github.com/taxjson/taxjson/actions/workflows/tests.yml)

A free, open-source command-line toolkit for computing **capital gains, dividend income, and wash-sale / superficial-loss adjustments** from raw brokerage CSV exports. Built for filers who want auditable numbers they can reproduce locally — no cloud upload, no signup, no fee.

> **Not tax advice.** This tool produces numbers; it does not give legal or accounting advice. Always reconcile against your broker's official tax slips (T5008, 1099-B, etc.) and consult a qualified professional before filing.

## What it does

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
| Webull                | Yes      | Yes     | —      | Account statement CSV; option expiry/assignment rows not yet parsed (see KNOWN_ISSUES) |
| Kraken                | —        | —       | Yes    | Ledgers CSV                                        |
| Coinbase              | —        | —       | Yes    | Transaction history CSV                            |
| **Any other broker**  | Yes      | —       | —      | `generic_*.csv` + a TOML column mapping (see `examples/generic_wealthsimple.toml`) |

| Country | Rule set                                                                                     |
| ------- | -------------------------------------------------------------------------------------------- |
| Canada  | ACB cost basis, superficial loss s.40(2)(g), merger s.85.1(5), spinoff s.86.1                |
| US      | FIFO cost basis, wash sale §1091 (30-day window with replacement-share basis adjustment)     |

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
                         # "include section code and line descriptor" ON
```

Then `taxjson fetch` (or `taxjson fetch run` to rebuild in the same
breath, or `taxjson fetch run verify` to also cross-check the rebuilt
books against the broker's LIVE holdings — `fetch --positions` writes
the same `work/<account>_live_holdings.toml` snapshots without the
comparison). Credentials never go in `taxjson.toml`: Questrade takes a
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
re-covers its whole configured period) — the same formats the parsers
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
each action value to one of `buy | sell | dividend | tax | interest | fee |
skip` in `[actions]`. Conventions match the hand-written parsers: signed
amounts are preserved, unmapped action values are counted and summarized (never
silently dropped), and a mapping that references columns the CSV doesn't have
refuses loudly. Equities/dividends only — no options.

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

Then `mkdir -p ~/taxes/2026 && cd ~/taxes/2026 && taxjson init`. See [REFERENCES.md](REFERENCES.md) for the CRA/IRS sources behind every rule and [docs/releasing.md](docs/releasing.md) for how releases are cut.

From source (development):

```bash
git clone https://github.com/taxjson/taxjson.git
cd taxjson
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

Core pipeline has no third-party runtime dependencies. Note that a full
`taxjson run` does reach Yahoo Finance for FX rates and un-priced crypto
rows on a cache miss (`TAXJSON_OFFLINE=1` forbids it — see SECURITY.md
for the complete egress list). Optional extras:

```bash
pip install -e ".[fx]"          # FX rate fetcher (yfinance + pandas) — needed whenever source_currencies is non-empty (the default scaffold converts USD)
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
taxjson show margin   # print reports/margin.sum
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
- `ccd.rpt`, `leaps.rpt` — cross-account covered-call / LEAPS views
- `<account>_holdings.toml` — machine-readable positions (native + base-currency cost, and the per-position acquisition/sell `trades` history)
- `exports/` — SeekingAlpha / FastGraph / TradingView watchlist CSVs

### Project layout and configuration

The full `taxjson.toml` schema (unknown keys warn at run start, with
did-you-mean suggestions):

```toml
[settings]
year = 2026                    # tax year the pipeline reports on
country = "canada"             # canada | ca | usa | us
base_currency = "CAD"          # report currency; foreign income converted
tax_date = "settle"            # settle (CRA default) | trade (IRS default)
source_currencies = ["USD"]    # currencies you hold besides base_currency (FX rates fetched)
# province = "ON"              # canada tax-estimate default (ON/BC/AB)
# cross_asset = true           # WARN-ONLY option-as-replacement wash triggers
# fx_cash_gains = true         # end-of-run s.39(1.1) FX-on-cash report (off by default)

# Optional — Canadian tax instalments (`taxjson instalments`, and a
# compact block inside `taxjson estimate`):
# [instalments]

# [estimate]                   # inputs `taxjson estimate` (and the
# other_income = 120000        # instalments current-year basis) uses
# other_losses = 0             # when the flags aren't given
# basis = "current_year"       # current_year | prior_year | cra_reminder
# prescribed_rate = 0.08       # CRA's overdue-tax rate — or, since CRA
#                              # resets it quarterly and charges each day
#                              # at the rate then in force:
# prescribed_rates = [{ from = "2026-01-01", rate = 0.08 },
#                     { from = "2026-07-01", rate = 0.09 }]
# withheld = 0                 # tax already withheld at source this year
# prior_year_net_tax = 55000   # last year's net tax owing (line 48500
# second_prior_net_tax = 41000 #   minus withholding, per the NOA).
#                              # Supply BOTH even on current_year: CRA
#                              # assesses interest on the least of the
#                              # methods your figures support, and they
#                              # also decide whether instalments are
#                              # owed at all. A placeholder 0 reads as
#                              # "I owed nothing" and suppresses both.
# paid = [{ date = "2026-03-16", amount = 15000 },
#          { date = "2026-05-20", amount = 12000,
#            note = "prior-year refund transferred to instalments" }]

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
| `inputs/<account>/` | Drop broker CSV exports here; any `*.tt` manual-history files too. |
| `inputs/<account>/manifest.json` | Saved corp-action elections — **commit this**. |
| `ticker.map` | Symbol rules, one per line: `GLOBAL from to` (plain rename, every stage), `TOBASE from to` (cross-listing consolidated in the base pipeline only), `JOURNAL from to` (Norbert's Gambit pair — consolidated AND netted in holdings), `DELETE from` (drop a pure artifact), `DISTINCT a b` (records that two look-alike listings are deliberately separate securities — a CDR vs its US underlying — and silences the scan's MAP-GAP nag; changes no symbol). For TOBASE pairs the holdings view keeps the listings separate **except** where the broker's own transfer rows prove a depot flip — the holdings export applies those evidenced quantities from the transfer sidecars (see `taxjson transfers`), so `JOURNAL` is only for intrinsically fungible classes like DLR's gambit units. `taxjson init` writes a commented stub. |
| `distributions.map` | Non-cash fund distributions: `SYMBOL RECORD_DATE PER_SHARE` (negative = ROC). |
| `t1135.map`, `yf_ticker.map`, `sector.map`, `crypto_ticker.map` | Per-symbol overrides: T1135 domicile, yfinance spelling, timeline sectors, crypto Yahoo-collision fixes. |
| `phantoms.json`, `claimed_losses.txt` | Missing-basis phantoms (auto-applied); losses actually claimed on filed returns (`YEAR AMOUNT`). |
| `work/` | Intermediate per-stage artifacts and price/FX caches. Rebuildable; gitignored. |
| `reports/` | Everything you read: `<account>.sum`, `wash_radar_*`, `fees.rpt`, holdings, `exports/`. Rebuildable. |
| `filed/<year>.json` | Filed-year locks from `taxjson close-year` — **commit these**. |

### Subcommands

| Command | Purpose |
| --- | --- |
| `taxjson run` | Run the full pipeline, rebuilding every stage (the recommended everyday command — results always reflect current inputs, config and code). |
| `taxjson run --fast` | Incremental run: mtime-cached stages whose inputs, `taxjson.toml` and installed code are all unchanged are skipped. The cache invalidates itself on any of those changing; `--fast` trades that safety net's edge cases for speed. |
| `taxjson run --account NAME` | Re-run a single account. ⚠️ Skips cross-account wash-sale and cross-listing detection — those need a full run. |
| `taxjson sanity ACCOUNT\|FILE.toml\|ACCOUNT=FILE ...` | Cross-check open positions against externally produced holdings `.toml` files (portoml-style `[[holding]]`), per symbol (`--tolerance`, `--json`; exit 1 on any discrepancy). Bare items form one aggregate group (combined positions vs combined holdings — quick, but blind to a position sitting in the wrong account). `ACCOUNT[+ACCOUNT]=FILE[+FILE]` pairs specific accounts with specific files and is checked as its own group — many-to-many because a taxjson account can span several broker accounts (`margin=ibkr.toml+webull.toml`, or repeat `margin=…`) and one broker export can cover several accounts (`rrsp+lira=flex.toml`). Both forms mix freely. With no arguments the pairings come from `taxjson.toml` — each account's `holdings = [...]` — and `taxjson run` finishes with the same check as a warning. Option rows whose root the file spells differently (`RCI…` vs taxjson's `RCI.B…`) are matched through the row's `underlying` field. |
| `taxjson run --strict` | Promote per-account validation ERRORs (oversold positions, malformed rows) to fatal instead of publishing reports with a DIAGNOSTICS banner. Recommended for CI/cron. |
| `taxjson show NAME` | Print `reports/NAME.sum`. |
| `taxjson run sum` (chaining) | Subcommands chain in one invocation, each with its own flags: `taxjson run close-year check-filed`. Note a chained `--json` command's stdout follows the earlier commands' progress output — pipe consumers should run the JSON command standalone. Also: `taxjson run close-year check-filed`. A failing command stops the chain and its exit code propagates. Option values that collide with command names are handled (`--account sum`); for the rare ambiguous positional, separate with `--`. |
| `taxjson close-year` | Snapshot the current tax year's filing aggregates to `filed/<year>.json` — the filed-year lock. Commit it with your records. |
| `taxjson check-filed` | Recompute every filed year from the current books and report drift vs the locks (every full run also auto-checks; `--strict` aborts on drift). |
| `taxjson events` / `divs` / `dil` / `trades` / `gains` / `fees` / `roc` / `leaps` | Per-transaction views over a look-back window — see below. |
| `taxjson transfers [ACCOUNT]` | Custody-transfer **evidence** view: depot flips, listing journals, broker migrations, and crypto withdrawals/sends (matched send/arrival pairs read as self-custody moves; unmatched out-legs are the gift/payment candidates — FMV dispositions if they left your ownership) — the TRANSFER rows the books deliberately exclude (basis comes from buy/sell history). Reads the parse-stage sidecars (`work/<acct>_<broker>_transfers.json`) plus in-book TRANSFERs from `transfers = true` accounts, with the broker's transfer type (InterDepot / Internal / ATON). `--json` for machines. |
| `taxjson roc-sum` | Return-of-capital / ACB-adjustment total per ticker (default: tax year). |
| `taxjson dil-sum` | Payment-in-lieu total per symbol (default: tax year) — DIVIDEND_IN_LIEU rows only, split out because they are ordinary income (no dividend gross-up/credit or qualified rate). |
| `taxjson winners [PERIOD] [--top N]` | Per-ticker realized gains RANKED — biggest winners and losers over a window (default: tax year); options grouped under their underlying. |
| `taxjson ccd-sum` | Covered-call (short call) realized-gain summary per underlying over a window (default: tax year) — the windowed query twin of `reports/ccd.rpt`. |
| `taxjson leaps-sum` | Per-contract LEAPS summary — long option buys placed >3 months to expiry (default: tax year). |
| `taxjson instalments` | Canadian tax instalments: what each of the four dates (Mar/Jun/Sep/Dec 15) calls for under your chosen basis, what you have paid, and the **offset interest** plus **s.163.1 penalty** that follow from any gap. The current-year basis is driven by `taxjson estimate` itself (AMT included). Configure `[instalments]` in `taxjson.toml`; `--json` for machines. |
| `taxjson estimate` | The realized-gains summary table followed by the marginal tax **estimate**: tax(other income + investment income) − tax(other income). Canada projects also get an **AMT check** (post-2024 rules: gains at 100%, no DTC, 20.5% over the exemption + provincial piggyback) — shown binding-or-not, with the top-up and 7-year carryforward when it binds. Canada: 50% inclusion, eligible gross-up/DTC, FTC from the books' actual TAX rows, ON/BC/AB (`--province`, or `province` under `[settings]`). `--other-income`/`--other-losses` (or the `[estimate]` config block, which `instalments` reads too), `--verbose` trace, `--json`. Planning numbers, never filing numbers. |
| `taxjson sum` / `list` / `divs-sum` / `trades-sum` / `fees-sum` | Roll-up summaries — see below. `list --date YYYY-MM-DD` shows positions AS OF that date (books recomputed via the engine's `--as-of` cutoff: full ACB + deferred-wash fidelity; pre-wash, pre-ticker.map); `list --negative` shows only negative-quantity positions — real shorts, or (in accounts that can't short) missed corporate actions / import gaps. |
| `taxjson shares [--options] [--taxable\|--sheltered] [--sort qty] [--json]` | Combined quantity held of each symbol across all accounts (post ticker.map, wash-adjusted where built) with a per-account breakdown and combined book cost; shorts net against longs. Option contracts only with `--options`. |
| `taxjson sell-check SYMBOL ...` | Sell-side wash check: is selling this ticker **at a loss** today safe? **UNSAFE** when a recent affiliated buy would deny it (LOCKED — permanently for the registered-matched portion); **ACTION** when a rescueable violation is open (sell the full position before the deadline); **SAFE\*/SAFE** with the applicable caveats. Whether it *is* a loss at today's price is `harvest`'s job. `--json` for machines; exit 1 on unsafe. |
| `taxjson buy-check SYMBOL ...` | Buy-side wash check: is buying this ticker today safe? **UNSAFE** when a loss was sold within the past 30 days (the rebuy cancels it — permanently if bought sheltered), with the safe-from date when one is determinable (violations defer to `wash-radar` rather than print a date that would invite an early rebuy); **SAFE\*** when buying merely extends an open wash window. Root-matched (`buy-check NU` covers `NU.US` and cross-listings, folding in `ticker.map` pairs); `--json` for machines; exit 1 on unsafe. |
| `taxjson audit [SYMBOL ...]` | The **authoritative justification** of every capital-gain figure: one block per disposition tracing the parsed broker row (nominal currency, original ticker, source file) through the ticker.map rename, the exact FX rate applied (provenance named, recomputed against the base books to the cent), the ACB/FIFO disposition math, and the wash-sale / superficial-loss determination with replacement lots resolved — ending in a tie-out against the pipeline's saved gains files and the full pool trace. Runs the same blended computation the pipeline runs, so the audited numbers ARE the filed numbers. `--summary` for one line per event, `--id/--date/--account` filters, `--json`; exit 1 when any cross-check disagrees. |
| `taxjson wash-radar` / `wash-sales` | Wash-sale radar (forward) and denied-loss report (backward). |
| `taxjson fx-cash` | FX capital gains on foreign-currency **cash** — foreign cash is property, so spending USD realizes the rate move since it was acquired. Canada: ITA s.39(1.1) with the $200/year de minimis; US: the §988 ordinary-income figure. A standalone report reconstructed from the taxable accounts' native books (`--events` for the per-disposal detail, `--json` for machines); changes NO other number. Set `fx_cash_gains = true` under `[settings]` to also print it (and write `reports/fx_cash.rpt`) at the end of every run — off by default. |
| `taxjson watch` | Cron-able change detector: reports only what CHANGED since the last watch run — new/changed/cleared radar advisories, moved clear dates, and (with `--harvest`) the harvestable-now loss total moving more than `--threshold` (default 100). Silent with exit 0 when nothing changed, so a cron line mails only on news; `--exit-code` exits 1 on changes for scripting, `--json` for machines. State: `work/.watch_state.json`; `--state PATH` gives a cron cadence its own baseline (daily and weekly lines can coexist). |
| `taxjson verify [ACCOUNT ...]` | Fetch LIVE Questrade holdings (positions only — fast) and cross-check them against the computed books via the sanity machinery: "does my book match the broker right now?" Exit 1 on mismatch — the usual cause is a missed or unparsed transaction. `--tolerance` sets the per-symbol quantity slack (default 1e-4; `0` demands exact). Questrade only — IBKR Flex statements carry no live-position feed. The complete loop: `taxjson fetch run verify`. |
| `taxjson fetch [ACCOUNT ...]` | Download broker activity straight into `inputs/` — Questrade REST API and IBKR Flex Web Service, configured on the account (`brokerage` + `account`/`query_id` under `[accounts.<name>]`). Writes files the existing parsers already read; hand-exported CSVs keep working side by side. Defaults to year-to-date (`--year`/`--from`/`--days` to widen, `--trim-overlap` to drop rows your manual exports already cover, `--dry-run` to preview). Credentials: `--refresh-token` (Questrade) / `--flex-token` (IBKR); `--positions` fetches live holdings instead of activity. Chain it: `taxjson fetch run`. |
| `taxjson scan` | Lint the project for common tax-efficiency mistakes: cross-listed Canadian dividend payers held via the US line in taxable/TFSA, US payers in a TFSA (unrecoverable 15% withholding), and ticker.map cross-listing gaps. `--online` probes yfinance for unmapped .TO twins. Exit 1 on findings. |
| `taxjson t1135` | CRA T1135 foreign-property helper: filing-threshold test + per-property/per-country tables. |
| `taxjson carryover` | Multi-year capital-loss carryforward/carryback ledger (Canada balance + T1A carryback candidates; US ST/LT worksheet). |
| `taxjson form-export` | Filing-shaped output: IRS Form 8949 / CRA Schedule 3 (default follows the country), or a TurboTax-importable TXF via `--form txf [--box A|B|C] --out gains.txf`. |
| `taxjson reconcile-slips SLIP.csv` | Diff broker T5008 / 1099-B slips against computed dispositions before filing (exit 1 on mismatch). |
| `taxjson help [COMMAND]` | Show top-level help, or help for one subcommand. |
| `taxjson find-missing-history [NAME]` | Report positions with missing cost basis (truncated buy history, or $0-basis corp-action shares) that distort a year's gain. See "Importing manual cost basis". |
| `taxjson elect` | Review, redo, or non-interactively set (`--set ID=ELECTION`) a corporate-action tax election. |
| `taxjson init --country canada\|usa [PATH] [--year YYYY]` | Scaffold a new project directory (config, currencies, and account folders per jurisdiction; `--force` to overwrite). |
| `taxjson harvest [SYMBOL ...]` | Unrealized gain/(loss) per open position at current prices — "if I sold this today, is it a loss?" Losses first, wash-radar advisory on each loss, `LT_IN` days-to-long-term for US projects. |
| `taxjson serve` | Launch the local web UI (needs the `[web]` extra). |

**Non-cash distributions (`distributions.map`):** Canadian ETFs declare reinvested (phantom) capital-gains distributions — usually each December — that never appear in broker CSVs yet raise your ACB; some funds publish return-of-capital factors only after year-end. Put one line per event in a project-root `distributions.map` (`SYMBOL RECORD_DATE PER_SHARE`, negative for ROC) and `taxjson run` converts them into ACB adjustments for every taxable account holding the fund on the record date.

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
SUBTOTAL) followed by the **ALL ACCOUNTS** grand total; an account missing a
`type` gets its own UNTYPED table rather than silently joining a bucket.
Single-type projects and `taxjson sum <account>` keep the one-table layout.
Every table's total row is the exact sum of the rows above it, and the
numbers match each `reports/<account>.sum`. `--json` carries a per-account
`type` and a `subtotals` object alongside `totals`.

```
$ taxjson sum
TAXABLE ACCOUNTS
ACCOUNT    STOCK        OPTION       REALIZED     DIVIDEND    PIL        FEES       TOTAL
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

`TOTAL` = REALIZED + DIVIDEND.

**Tax estimate** — **`taxjson estimate`** (the front door; also
`taxjson sum --other-income ...` to see it under the account table)
computes a marginal tax **estimate** for the
year's investment income, computed incrementally: tax(other income +
investment income) − tax(other income), so the investment income is
bracketed on top of what you already earn. Taxable accounts only.
`taxjson estimate` with no flags uses the `[estimate]` config (or 0 for
both amounts — pure investment-income bracketing).

```
TAX ESTIMATE — canada/ON, rates vintage 2025 (ESTIMATE ONLY, not filing numbers; taxable accounts only)

  Other income                     200,000.00
  Capital gains (taxable)           15,000.00  [30,000.00 realized - 0.00 other losses, x50%]
  Eligible dividends (grossed)       1,380.00  [1,000.00 x1.38, Canadian-listed]
  Foreign dividends                    500.00  [FTC assumed 75.00]
  Payments in lieu                       0.00

  Tax with investments: 72,371.28 (federal 45,184.01 + ON 27,187.27)
  Tax on other income alone: 64,694.29
  => ESTIMATED TAX ON INVESTMENT INCOME: 7,677.00 CAD  (24.4% of 31,500.00)
```

- **Canada** (`--province` or `province` under `[settings]`; ON/BC/AB):
  `--other-losses` are prior-year capital losses in **full dollars**,
  netted against gains before the 50% inclusion. Canadian-listed
  dividends are treated as eligible (38% gross-up + DTC); foreign
  dividends as ordinary income with the 15% treaty withholding assumed
  creditable. Ontario's surtax is modelled; the BPA phase-out and QC
  are not.
- **USA**: single filer, standard deduction. ST gains are ordinary; LT
  gains and (assumed-qualified) dividends stack on top at the 0/15/20%
  brackets; losses net ST first, then LT, then up to $3,000 of ordinary
  income (a net-loss year shows a negative estimate — a saving); NIIT
  3.8% above $200k MAGI; no state tax.

Add `--verbose` (`-v`) for the **CALCULATION TRACE** — every bracket
slice, credit and surtax tier, side by side for the base and
with-investments runs, ending in the subtraction that produces the
estimate. It shows exactly where each dollar of investment income
landed in the brackets and what the gross-up/DTC did.

Rate tables live in `lib/tax_estimate.py` with a printed vintage —
they need an annual refresh, and the output says so. These are
planning estimates, never filing numbers.

**`taxjson divs-sum [PERIOD] [ACCOUNT]`** — dividends received per ticker over
the window, with a per-currency grand total.

**`taxjson trades-sum [PERIOD] [ACCOUNT]`** — per ticker: buy/sell counts, value
bought/sold, and fees, with per-currency totals.

**`taxjson fees-sum [PERIOD] [ACCOUNT]`** — trading-fee report by **brokerage**
(commission/fee totals with per-trade averages, $/share, %notional), converted
to the base currency — the same report `taxjson run` writes to
`reports/fees.rpt`. Broken down **by account** by default (`--no-by-account` for
brokerage-only totals). A `PERIOD` window scopes it (e.g. `taxjson fees-sum
2025`); `--json` emits machine output.

Money is shown to 2 decimals; quantities and per-share prices keep full
precision. Rows with a missing/unparseable date are excluded with a warning.

**`taxjson list [ACCOUNT]`** — open positions per account, taken from the
canonical gains files' `inventory` (the wash-adjusted `<account>_gains_wash.json`
when the pipeline built it, else `<account>_gains.json`) — i.e. **after** `ticker.map` consolidation
(cross-listings like `AEM.US`/`AEM.TO` merged) and base-currency conversion, so
quantity and cost basis match the canonical pipeline (unlike
`reports/<account>_holdings.toml`, which keeps listings separate and native for
live-pricing tools). One row per (account, symbol) with quantity, base-currency
book cost, cost/share, and the position's start date; fully-closed positions are
omitted. Pass an account to scope to one.

```
$ taxjson list
OPEN POSITIONS — CAD, as of tax year 2026, basis: wash-adjusted  (...)

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
advisory in fixed order (VIOLATION, BLOCKED, LOCKED, EXITABLE — loss OK only with a FULL exit, CAUTION — sheltered leg exited so a full-exit loss stands unless re-bought within 30 days, COOLING, RISK, CLEAR). The
same reports are written to `reports/wash_radar_<account>.rpt` during `taxjson
run`. This is **forward-looking** (what you can/can't sell or buy now); for a
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

**Option-as-replacement wash triggers (warn-only)** — set `cross_asset =
true` in `[settings]` (or pass `--cross-asset` to `taxjson-gains`) to also
scan each realized loss for OPTION acquisitions that would deny it:

- a loss on **long shares** with a **long call** on the same underlying
  bought inside the ±30-day window (CRA s.54 "a right to acquire" / IRS
  §1091 "option to acquire"), and
- a loss from **closing a short** with a **long put** bought in the window.

This is deliberately asymmetric: the underlying shares are **never**
replacement property for a long option's loss, and option losses themselves
wash only when the **identical contract** (same OCC symbol) is repurchased —
a near-identical contract (same underlying, one strike or expiry away) is
NOT matched; that judgment call is left to you. Matching is suffix-exact
(an option suffixed `.US` matches shares held as `.US`, not a `.TO`
listing) and follows ticker renames.

Findings are **warnings only** — computed numbers never change. Each carries
the loss, the option acquisition, the rule that fired, and (Canada) whether
the option is still held at the end of the +30-day window, since s.54 also
requires that. They appear in the gains JSON under
`option_replacement_warnings` and as `warning:` lines in the `.sum`
DIAGNOSTICS banner. Review them with your accountant; a future release may
offer enforcement.

**`taxjson t1135`** — CRA **Form T1135** (Foreign Income Verification Statement)
helper, for Canadian filers holding foreign securities. Answers the filing
question first: it replays the full history of every **taxable** account in base
currency and reports the **maximum total cost of specified foreign property at
any time in the year** — the ITA 233.3 test ($100,000 threshold; $250,000 for
the detailed method). If a filing is required, it prints per-property and
per-country tables (maximum cost in year, cost at Dec 31, income, gain/loss)
from the same books the rest of the pipeline reports on. Registered accounts
are excluded by law and never read. `--json` for machine-readable output.

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
`t1135.map` override in the project root:

```
# t1135.map — SYMBOL COUNTRY (ISO-3 code, or CA/EXCLUDE for "not foreign")
ENB.US   CA      # Canadian corp held on NYSE — not specified foreign property
GLXY.TO  USA     # foreign corp listed on TSX — still specified foreign property
```

Symbols with no market suffix (typically exchange-held crypto) are flagged
country `??` for manual review. Amounts are **cost** (ACB-style) — correct for
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
  gain in (h) = (d) − (e) + (g) — split into Part I (short-term) / Part II
  (long-term) with the Schedule D totals per part. Pick the 8949 box (A–F)
  yourself from whether the broker reported basis on your 1099-B.
- **`--form schedule3`** (Canada): per-security rows — units, acquisition
  year, proceeds of disposition, ACB, outlays, gain(loss) — with sell-side
  commissions re-split into the outlays column (gain unchanged), superficial
  losses already denied and noted per row, and the line 13199 / 13200 totals.

Both read the wash-adjusted gains (the allowed numbers a return reports) and
**skip tainted dispositions with a warning** — phantom-basis rows are routed
to `manual_reporting_required` and must be resolved, not filed.

**`taxjson reconcile-slips SLIP.csv`** — diffs the broker's official slips
(CRA **T5008**, IRS **1099-B**) against the computed dispositions, per
symbol: disposition count, quantity, proceeds, and (when the slip carries
cost) basis. CRA/IRS machine-match returns against these slips — run this
before filing and decide, for every flagged row, whether it's a tool-side
problem (dropped rows, missing statement months) or a legitimate,
documentable difference (per-broker box-20 book value vs blended ACB, broker
lot method vs FIFO). Slip headers are matched loosely (`Security`/`Box 16`/
`Box 21`/`Box 20` T5008 spellings work as-is; so do `Symbol`/`Quantity`/
`Proceeds`/`Cost or other basis`), market suffixes are stripped for matching
(slip `AAPL` ↔ computed `AAPL.US`), and net-of-commission slips are detected
and noted. Exits 1 when anything doesn't reconcile — cron and pre-filing
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
balance. If the history's first year has dispositions, the ledger warns
that pre-history balances aren't reflected. `--json` for machine output.

### Return of capital (ROC)

Return of capital is **not income** — it reduces your position's adjusted
cost base, deferring tax to the eventual sale. The pipeline handles the two
cases differently:

1. **Broker-labeled ROC rows are reclassified automatically.** Any
   Questrade/RBC/IB row whose description says "RETURN OF CAPITAL" is parsed
   as an `ADJUST` that reduces ACB by the cash received (tagged `roc`),
   instead of a dividend. IB reversal rows net out sign-correctly. Note this
   changes regenerated history: income totals drop and later gains rise
   relative to the old (incorrect) dividend treatment.
2. **Fund/ETF distribution ROC needs one manual entry per fund per year.**
   Most Canadian ETF/REIT ROC is not labeled in any broker CSV — the split
   only arrives months later on your T3 (box 42). Enter it as a `.tt` ADJUST
   line dated in the tax year, with a **negative** amount (ACB reduction):

   ```
   ADJUST 2025-12-31 12:00:00 XEI.TO CAD -184.23   # T3 box 42 ROC
   ```

Inspect what's recorded with `taxjson roc <period>` (every ADJUST row,
taxtext) and `taxjson roc-sum` (per-ticker capital returned, split into
broker-classified vs manual rows). If cumulative ROC ever pushes a
position's ACB below zero, the excess is a deemed capital gain under
s.40(3) — the engine flags this rather than computing it.

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
  and a suffix-less code matches with or without a currency suffix.
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

Registered-plan kinds are inferred from account names (`tfsa`, `rrsp`, …);
override per account with `plan = "tfsa"` in `taxjson.toml` when a name
doesn't say. Exit 1 when findings exist, 0 on a clean scan — cron-friendly.

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
out. `RISK` losses count as claimable **now** — the superficial-loss rule
needs an acquisition inside the ±30-day window, not mere sheltered
ownership — but carry a forward caveat: an affiliated buy (a DRIP is
the classic) within 30 days *after* the sale denies the loss
permanently, so pause sheltered adds first.
Accounts marked `crypto = true` are **excluded by default** (the price
chain serves stock snapshots; crypto symbols mostly fail to price) —
pass `--crypto` to include them.

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
carries the ×100 contract multiplier), a `DTE` days-to-expiry column
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
| `taxjson-lint-crosslistings --taxable work/margin_base.json --sheltered work/sheltered_base.json --map ticker.map` | Flag cross-listed (`.TO`/`.US`) tickers the wash radar may not consolidate (also run automatically → `reports/crosslistings.rpt`). |
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

To fix one, import the real trades from your brokerage confirmations as a
TaxTrak **`.tt`** file in the account's input folder — e.g. add lines to
`inputs/margin/margin_start.tt` (any `*.tt` in the folder is picked up). One
space-separated line per trade:

```
BUYSELL  <date>  <time>  <symbol>  <qty>  <currency>  <price>  <total>  <fee>
```

| field | notes |
| --- | --- |
| `date` / `time` | `YYYY-MM-DD` / `HH:MM:SS` (time REQUIRED — the parser's field positions depend on it; `09:30:00` is fine). A `.tt` line has a **single date**, used as both the trade and settlement date — enter the date matching your `tax_date` setting (**settlement date** when `tax_date = "settle"`). |
| ADJUST lines | `ADJUST date time symbol CURRENCY amount` — FIVE payload fields, not the BUYSELL shape (negative amount = ACB reduction, e.g. T3 box-42 ROC). |
| `symbol` | with exchange suffix — `AGI.TO`, `XYZ.US` (match how the account labels it; options use OCC, e.g. `ALA250117C00036000.TO`) |
| `qty` | shares — **positive = buy, negative = sell** |
| `price` | per-share price |
| `total` | net cash amount: **buy = qty×price + commission; sell = qty×price − commission** (your confirmation's net amount) |
| `fee` | commission (optional) |

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
starting with `#` are comments. Validate a single file first with
`taxjson-convert-tt --account margin inputs/margin/margin_start.tt` (it
prints the parsed JSON and errors loudly on a malformed line).

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
affecting the tax year; add `--all-history` for every candidate.

**Review the file and delete any entry that's actually a real short position.**
Then, if it's saved as `phantoms.json` at the project root, `taxjson run`
auto-detects it (like `ticker.map`) and feeds it to every account's gains run
via `--incomplete-history` — editing the file re-runs gains. In the manual
pipeline, pass it yourself: `taxjson-gains --incomplete-history phantoms.json …`.

## Quickstart (manual pipeline)

The commands below drive the pipeline stage by stage — handy for one-off files or scripting. For a configured project, prefer `taxjson run` above.

```bash
# 1. Convert a broker CSV to normalized JSON
taxjson-brokerage --brokerage ib --account margin activity.csv > margin.json

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

- [`KNOWN_ISSUES.md`](./KNOWN_ISSUES.md) — known limitations and deferred fixes
- [`CHANGELOG.md`](./CHANGELOG.md) — release history
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — how to run tests and submit changes
- [`SECURITY.md`](./SECURITY.md) — vulnerability reporting policy
- [`examples/`](./examples/) — synthetic CSV + walkthrough

## Verification

The numbers this tool emits go on real returns, so correctness is
defended in layers rather than by tests alone:

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
- **Independent audits.** Seven rounds so far — adversarial reviews,
  hand-computed statutory scenarios, security/I/O boundary — with
  every confirmed finding fixed and pinned. `KNOWN_ISSUES.md` lists
  what was deliberately left, with the reasoning.
- **Filed-year locks.** `taxjson close-year` snapshots a filed year;
  every later run recomputes it and reports drift.

`scripts/ci.sh` runs the whole gate locally (see CONTRIBUTING.md).

## Status

Pre-1.0. The core pipeline (parse → merge → gains → summarize) is stable and covered by 1,800+ tests, but expect occasional breaking changes to CLI flags and JSON field names until 1.0.

The tests run against synthetic fixtures and check that the code implements the rules as written here — they are not an assurance that the rules themselves are correctly interpreted for your situation, and no output has been reviewed by a tax professional. The planning commands (`estimate`, `instalments`, the AMT check, `fx-cash`) are explicitly estimates: they say so in their own output, and they should be checked against your assessment or your accountant before you rely on them. Report anything that looks wrong.

## Contributing

PRs welcome. See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for the test-driven workflow — every fix should land with a regression test.

## License

MIT — see [`LICENSE`](./LICENSE).
