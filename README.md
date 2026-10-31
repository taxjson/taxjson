# <img src="docs/brand/logo.svg" alt="" width="40" height="40" align="top"> taxjson

[![tests](https://github.com/taxjson/taxjson/actions/workflows/tests.yml/badge.svg)](https://github.com/taxjson/taxjson/actions/workflows/tests.yml)

taxjson turns your brokers' CSV exports into the numbers for your tax
return: **capital gains, adjusted cost base (ACB), dividends and interest,
and the superficial-loss rule** (US: wash sales), across all your
accounts and years. It is a free, open-source command-line tool for
people who file their own return and want numbers they can check: every
figure traces back to a broker row and the rule applied to it. It runs on
your own machine — no upload, no account, no fee.

Website: **[taxjson.com](https://taxjson.com)** · every rule cites its
source in [REFERENCES.md](REFERENCES.md).

> **Not tax advice.** taxjson is a calculator that shows its work. It
> does not give legal or accounting advice. Check its numbers against your
> broker's slips (T5008, T5, T3; US 1099-B) and ask a qualified
> professional about anything unusual before you file.

## Status

- **Canada: supported.** Run on real multi-account books and checked
  against the brokers' own positions.
- **United States: experimental.** FIFO basis, §1091 wash sales and Form
  8949 are implemented and unit-tested, but not validated on a real
  account. Treat its output as a draft.
- **Pre-1.0.** A release can change a file format or a command;
  [docs/upgrading.md](docs/upgrading.md) lists each change that asks
  something of you. What taxjson does not do: [docs/limits.md](docs/limits.md).

## Install

One line, no clone — installs the `stable` release into
`~/.local/share/taxjson` with its own virtualenv and puts `tjs` on
your PATH (re-run it to upgrade):

```bash
bash -c "$(curl -fsSL https://taxjson.com/install.sh)"
bash -c "$(curl -fsSL https://taxjson.com/install.sh)" _ --without-fetch   # without the Questrade / IBKR auto-fetch plugin
```

taxjson is not published on PyPI: a `taxjson` or `taxjson-fetch` package
there is not ours — use the installer or a checkout. Release channels (`stable`, `beta`, `latest`,
`dev`, a pinned version): [docs/upgrading.md](docs/upgrading.md#how-to-upgrade).
From a checkout: [CONTRIBUTING.md](CONTRIBUTING.md#setup). Python 3.10+;
the core needs no third-party package (only `tomli` before Python 3.11,
`tzdata` on Windows). The installer also adds the `fx` extra (yfinance
and pandas: the Yahoo Finance rates the Bank of Canada does not
publish) and the taxjson-fetch plugin. Every dependency it installs is
pinned by hash in the release (`requirements/`; SECURITY.md).

## Five-minute start

```bash
mkdir -p ~/taxes && cd ~/taxes
tjs init --country canada   # or usa; inputs/ for every year, 2026/ for this one
cd 2026                     # each year's folder: its taxjson.toml, ticker.map, results
tjs checklist --run         # every step: what is done, what is next
# drop broker CSVs, all years together, into ../inputs/margin/, ../inputs/rrsp/, …
tjs run                     # books, reports, wash pass, broker check
tjs sum                     # the year, by Schedule 3 line
tjs wash-radar              # "can I sell this at a loss today?"
tjs checklist --run         # again at year end: ready to file?
```

For last year's return, `tjs init --country canada --year 2025` and
`cd 2025`.

To try it first on made-up exports (Canada, tax year 2024: five taxable
accounts and a TFSA, a LIRA and an RRSP; the first run downloads that
year's exchange rates):

```bash
tjs init --demo ~/taxjson-demo && cd ~/taxjson-demo/2024
tjs run && tjs sum && tjs checklist --run
```

Then read **[docs/getting-started.md](docs/getting-started.md)**: a
first project step by step, including the part that takes the time —
purchases older than your downloads reach ("missing history").

## Brokers

| Broker | Shares | Options | Crypto | What to download |
| --- | :-: | :-: | :-: | --- |
| Interactive Brokers | Yes | Yes | — | Activity Statement, CSV (or a Flex query) |
| Questrade | Yes | Yes | — | Transaction history (Excel: convert it to CSV) |
| RBC Direct Investing | Yes | Yes | — | Transaction history, CSV |
| Webull | Yes | Yes | — | Trading Summary, CSV (no income rows) |
| Kraken | — | — | Yes | Trades **and** Ledgers, CSV |
| Coinbase | — | — | Yes | Transaction history, CSV |
| **Any other broker** | Yes | — | — | any CSV, with a column mapping |

Where each download is, each broker's quirks, the generic importer, the
auto-fetch plugin and your slips: **[docs/brokers.md](docs/brokers.md)**.

What the exports miss you type into a `.tt` file in the account's
folder — a purchase from before your downloads, a return of capital
([settings.md, ".tt files"](docs/settings.md#tt-files)):

```
BUYSELL 2019-06-03 09:30:00 SAMPA.TO 20 CAD 30.00 604.95 4.95   # from a confirmation
ADJUST 2025-12-31 12:00:00 SAMPLE.TO CAD -120.00   # T3 box 42 ROC
```

A fund's non-cash (reinvested) distribution goes in `[[distributions]]`
in `taxjson.toml`, its per-share amount a
plain decimal in the project's base currency.

## What it does

- Builds the books from every account's exports: ACB (US: FIFO lots),
  corporate actions, options, futures and crypto.
- Applies the superficial-loss rule across all your accounts, registered
  ones included, and tells you before you trade (`tjs wash-radar`,
  `tjs sell-check`).
- Produces the filing numbers: Schedule 3 lines (`tjs sum`,
  `tjs form-export`), T1135, carryovers, the minimum tax (AMT), and checks
  them against your slips (`tjs reconcile-slips`, `tjs slip-audit`).
- Checks itself against your broker's positions (`tjs sanity`) and finds
  missing purchase history (`tjs find-missing-history`).
- Explains every figure (`tjs audit`) and every rule (`tjs tax-logic`).

## What it does not do

- Give tax advice, prepare or file your return.
- Know what is not in your files: holdings at another broker, a
  spouse's trades, purchases older than your downloads (it finds the
  gaps; you fill them).
- Read PDFs or Excel files as activity (convert to CSV; slips are the
  exception).
- Validate the US engine on real accounts (experimental), or US
  §1256, estimated taxes or AMT.
- Upload anything: no web service, no account.

The full list, with what to do instead: [docs/limits.md](docs/limits.md).

## Privacy

Your exports and books stay on your machine. taxjson goes online only
for exchange rates and prices it cannot find in its cache (Bank of
Canada, Yahoo Finance) and, if you use it, the fetch plugin's broker
API; `TAXJSON_OFFLINE=1` turns the lookups off. Every network call and
what it sends: [SECURITY.md](SECURITY.md#network-access-and-data-egress).

## Getting help

1. **`tjs checklist --run`** in your project: the next step and its
   command (`tjs checklist` alone: the last run's verdicts, in a second).
2. **A message you don't understand:** search
   [docs/troubleshooting.md](docs/troubleshooting.md) for its text; an
   entry fixed in a release newer than `tjs --version` is fixed by upgrading.
3. **A bug:** open an issue with the
   [bug-report template](.github/ISSUE_TEMPLATE/bug_report.md). Never
   attach a raw export. Reproduce the problem on a made-up file (copy the
   broker's `examples/*_demo.csv` and edit its rows; made-up amounts are
   fine). Only if that is not possible, `tjs redact` a copy — it strips
   account numbers, names and addresses but keeps amounts and dates — read
   all of it, and attach it only after that review.

## Documentation

| Page | For |
| --- | --- |
| [Getting started](docs/getting-started.md) | your first project, start to finish |
| [Brokers](docs/brokers.md) | downloading each broker's files and slips |
| [Filing checklist](docs/filing.md) | the year end, step by step (`tjs checklist`) |
| [Commands](docs/commands.md) | every command in detail |
| [Settings](docs/settings.md) | `taxjson.toml`, `ticker.map`, `.tt` files, every project file |
| [Tax rules](docs/tax-rules.md) | every rule applied, with its source and rule id |
| [Glossary](docs/glossary.md) | ACB, superficial loss, TOBASE, journal … |
| [Limits](docs/limits.md) | what taxjson does not do; Canada vs US status |
| [Troubleshooting](docs/troubleshooting.md) | a message, its cause and the fix |
| [Upgrading](docs/upgrading.md) | channels, what each release asks of you |
| [Known issues](KNOWN_ISSUES.md) | open bugs |
| [Security](SECURITY.md) | network egress, files on disk, reporting a vulnerability |
| [Contributing](CONTRIBUTING.md) | development setup, tests, adding a broker |
| [References](REFERENCES.md) | the ITA / CRA / IRC source behind each rule |
| [AGENTS.md](AGENTS.md) | notes for AI assistants helping you |
| [Changelog](CHANGELOG.md) · [Deck](docs/deck/taxjson-deck.pdf) · [Examples](examples/) | history · overview slides · demo CSVs |

## Commands

### Subcommands

`tjs` is taxjson's command (`tjs sum`, `tjs -C ~/taxes/2025 stats`; `taxjson`
still runs it too, for old scripts); with no command it prints the help page,
grouped as below. Inside a project the help page leaves out the other
country's commands (`tjs help --all` lists them). Every command takes
`--help`; [docs/commands.md](docs/commands.md) describes each in full.

#### Set up

| Command | Purpose |
| --- | --- |
| [`tjs checklist`](docs/commands.md#tjs-checklist) | Every step from install to filing, checked, ending with the next step's command |
| [`tjs init`](docs/commands.md#tjs-init) | Create the folder of exports for every year and the year's project (`--single`: one folder) |
| [`tjs years`](docs/commands.md#tjs-years) | The year folders sharing one folder of exports, each filed or open |
| [`tjs new-year`](docs/commands.md#tjs-new-year) | Start next year's folder from this year's |
| [`tjs align`](docs/commands.md#tjs-align) | Bring another year's ticker.map lines and settings over |
| [`tjs format`](docs/commands.md#tjs-format) | Lay out `taxjson.toml` like the template `init` writes |
| [`tjs format-map`](docs/commands.md#tjs-format-map) | Lay out `ticker.map` in keyword groups |
| [`tjs migrate`](docs/commands.md#tjs-migrate) | Convert an older project's files (`missing_history.json`, old maps) |
| [`tjs fetch`](docs/commands.md#tjs-fetch) | Download Questrade / IBKR activity (taxjson-fetch plugin) |
| [`tjs elect`](docs/commands.md#tjs-elect) | Review or set a merger or spin-off election |
| [`tjs ticker-map`](docs/commands.md#tjs-ticker-map) | The ticker.map lines the last run suggested; `--write` adds them |
| [`tjs update-tobase-map`](docs/commands.md#tjs-update-tobase-map) | Canada: update the interlisted pairs in `tobase.map` |

#### Build the books

| Command | Purpose |
| --- | --- |
| [`tjs run`](docs/commands.md#tjs-run) | Build the books and every report from your exports (`--fast`, `--strict`, `--account`) |
| [`tjs compare`](docs/commands.md#tjs-compare) | What moved since the run before, a saved snapshot or the reports as filed |
| [`tjs snapshot`](docs/commands.md#tjs-snapshot) | Copy the whole project to `snapshot/<year>.<timestamp>/`, to diff file by file or `compare` later |
| [`tjs crypto-sends`](docs/commands.md#tjs-crypto-sends) | Decide each crypto send that did not arrive in another of your accounts (an arrival from 10 minutes before to 3 days after the send is yours) |
| [`tjs find-missing-history`](docs/commands.md#tjs-find-missing-history) | Sales and holdings whose purchase is not in your files |
| [`tjs opening`](docs/commands.md#tjs-opening) | An opening balance from a broker's positions report |

#### Summaries

| Command | Purpose |
| --- | --- |
| [`tjs amt`](docs/commands.md#tjs-amt) | Canada: the year's minimum tax, line by line |
| [`tjs div-estimate`](docs/commands.md#tjs-div-estimate) | Upcoming dividends from each holding's own history (planning only) |
| [`tjs estimate`](docs/commands.md#tjs-estimate) | Estimated tax on the year's investment income (planning only) |
| [`tjs fx-cash`](docs/commands.md#tjs-fx-cash) | Currency gains on foreign cash |
| [`tjs instalments`](docs/commands.md#tjs-instalments) | Canada: instalments due, paid, and the interest owed |
| [`tjs option-yield`](docs/commands.md#tjs-option-yield) | Option premiums, profit or loss and yield on an underlying over a window (planning only) |
| [`tjs plot-tax`](docs/commands.md#tjs-plot-tax) | ASCII chart of the tax each month's investment income generated, against the year's estimate (planning only) |
| [`tjs stats`](docs/commands.md#tjs-stats) | Win/loss statistics on closed trades |
| [`tjs sum`](docs/commands.md#tjs-sum) | The year's realized gains by account, ending with the lines for your return |
| [`tjs win-rates`](docs/commands.md#tjs-win-rates) | Win rate of your closed trades per ticker, account, month or class |

#### Positions

| Command | Purpose |
| --- | --- |
| [`tjs list`](docs/commands.md#tjs-list) | Open positions per account, with book cost (`--date` for a past day) |
| [`tjs shares`](docs/commands.md#tjs-shares) | Total held per symbol across all accounts |

#### Row listings

| Command | Purpose |
| --- | --- |
| [`tjs cash`](docs/commands.md#tjs-cash) | Cash deposits, withdrawals and conversions over a window, as `.tt` lines |
| [`tjs dil`](docs/commands.md#tjs-dil) | Payments in lieu of dividends over a window |
| [`tjs divs`](docs/commands.md#tjs-divs) | Dividends over a window |
| [`tjs events`](docs/commands.md#tjs-events) | Every transaction over a window |
| [`tjs fees`](docs/commands.md#tjs-fees) | Fees per trade over a window |
| [`tjs gains`](docs/commands.md#tjs-gains) | Realized gains in the trade's own currency |
| [`tjs leaps`](docs/commands.md#tjs-leaps) | Closed LEAPS positions |
| [`tjs roc`](docs/commands.md#tjs-roc) | Return-of-capital and other cost adjustments |
| [`tjs staking`](docs/commands.md#tjs-staking) | Rewards, commissions, net income and acquisition evidence |
| [`tjs trades`](docs/commands.md#tjs-trades) | Buys, sells and assignments |
| [`tjs transfers`](docs/commands.md#tjs-transfers) | Custody transfers the books leave out, and what was done with each |

#### Totals by type

| Command | Purpose |
| --- | --- |
| [`tjs ccd-sum`](docs/commands.md#tjs-ccd-sum) | Covered-call gains per underlying |
| [`tjs dil-sum`](docs/commands.md#tjs-dil-sum) | Payments in lieu per symbol, with their tax treatment |
| [`tjs divs-sum`](docs/commands.md#tjs-divs-sum) | Dividends per ticker |
| [`tjs fees-sum`](docs/commands.md#tjs-fees-sum) | Trading fees by broker |
| [`tjs leaps-sum`](docs/commands.md#tjs-leaps-sum) | LEAPS gains per contract |
| [`tjs losers`](docs/commands.md#tjs-losers) | Biggest realized losers |
| [`tjs roc-sum`](docs/commands.md#tjs-roc-sum) | Return of capital per ticker |
| [`tjs staking-sum`](docs/commands.md#tjs-staking-sum) | Reward and commission totals by exchange and coin |
| [`tjs trades-sum`](docs/commands.md#tjs-trades-sum) | Buys, sells and fees per ticker |
| [`tjs winners`](docs/commands.md#tjs-winners) | Biggest realized winners |

#### Before you trade

| Command | Purpose |
| --- | --- |
| [`tjs wash-radar`](docs/commands.md#tjs-wash-radar) | Open superficial-loss / wash-sale windows as of today |
| [`tjs buy-check`](docs/commands.md#tjs-buy-check) | Would buying this today cancel a recent loss? |
| [`tjs sell-check`](docs/commands.md#tjs-sell-check) | Would selling this at a loss today keep the loss? |
| [`tjs rebuy`](docs/commands.md#tjs-rebuy) | When each ticker sold at a loss can be bought back |
| [`tjs harvest`](docs/commands.md#tjs-harvest) | Unrealized gains and losses at current prices |
| [`tjs tips`](docs/commands.md#tjs-tips) | Tax-efficiency tips for next year |
| [`tjs watch`](docs/commands.md#tjs-watch) | What changed since the last watch (for cron) |

#### Before you file

| Command | Purpose |
| --- | --- |
| [`tjs form-export`](docs/commands.md#tjs-form-export) | Schedule 3 / Form 8949 rows, or a TurboTax TXF |
| [`tjs t1135`](docs/commands.md#tjs-t1135) | Canada: the foreign-property test and tables |
| [`tjs reconcile-slips`](docs/commands.md#tjs-reconcile-slips) | Compare broker T5008 / 1099-B slips with the books |
| [`tjs slip-audit`](docs/commands.md#tjs-slip-audit) | Canada: T5 / T3 slips against the books' income |
| [`tjs carryover`](docs/commands.md#tjs-carryover) | Capital-loss carryforward ledger across years |
| [`tjs option-boundary`](docs/commands.md#tjs-option-boundary) | Canada: written options across a year end |
| [`tjs close-year`](docs/commands.md#tjs-close-year) | Lock the year you filed (`filed/<year>.json`) |
| [`tjs check-filed`](docs/commands.md#tjs-check-filed) | Recompute filed years and report any drift |
| [`tjs handoff`](docs/commands.md#tjs-handoff) | Check this year starts where last year's return ended |

#### Explain and check

| Command | Purpose |
| --- | --- |
| [`tjs audit`](docs/commands.md#tjs-audit) | Trace every capital gain back to its broker row |
| [`tjs wash-sales`](docs/commands.md#tjs-wash-sales) | Each loss denied this year, and why (`--explain`) |
| [`tjs tax-logic`](docs/commands.md#tjs-tax-logic) | Every tax rule applied, a bullet each (`--ids`) |
| [`tjs edge-cases`](docs/commands.md#tjs-edge-cases) | Trades on a year-end or 30-day-window edge |
| [`tjs check-dates`](docs/commands.md#tjs-check-dates) | Trade and settlement dates checked against each market |
| [`tjs check-tt`](docs/commands.md#tjs-check-tt) | Every `.tt` line read as the run reads it, each problem named with its file and line |
| [`tjs sanity`](docs/commands.md#tjs-sanity) | Compare positions (and costs) with the broker's holdings |
| [`tjs journals`](docs/commands.md#tjs-journals) | Broker journals between two listings of one security |
| [`tjs renames`](docs/commands.md#tjs-renames) | Ticker changes as dated events |
| [`tjs spinoffs`](docs/commands.md#tjs-spinoffs) | Spin-offs: election, value used, cost booked |
| [`tjs splits`](docs/commands.md#tjs-splits) | Splits and consolidations, holdings before and after |

#### Maintainer

The release commands, for the machine taxjson is developed on: `tjs help`
lists them only there (`tjs help --all` everywhere).

| Command | Purpose |
| --- | --- |
| [`tjs channels`](docs/commands.md#tjs-channels) | Where each release channel points; what this machine runs |
| [`tjs deploy`](docs/commands.md#tjs-deploy) | Development machine: install a release here |
| [`tjs promote`](docs/commands.md#tjs-promote) | Development machine: point `stable` or `beta` at a release |

#### Tools

| Command | Purpose |
| --- | --- |
| [`tjs redact`](docs/commands.md#tjs-redact) | Copy exports with account numbers, names and addresses removed |
| [`tjs help`](docs/commands.md#tjs-help) | The help page, or one command's (`--all`: both countries') |

## Contributing and licence

Pull requests are welcome: [CONTRIBUTING.md](CONTRIBUTING.md) (every fix
lands with a regression test). MIT licence — see [LICENSE](LICENSE).
