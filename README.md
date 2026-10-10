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
`~/.local/share/taxjson` with its own virtualenv and puts `taxjson` on
your PATH, with `tjs` as its short name (re-run it to upgrade):

```bash
bash -c "$(curl -fsSL https://taxjson.com/install.sh)"
bash -c "$(curl -fsSL https://taxjson.com/install.sh)" _ --without-fetch   # without the Questrade / IBKR auto-fetch plugin
```

taxjson is not published on PyPI: a `taxjson` or `taxjson-fetch` package
there is not ours — use the installer or a checkout. Release channels (`stable`, `beta`, `latest`,
`dev`, a pinned version): [docs/upgrading.md](docs/upgrading.md#how-to-upgrade).
From a checkout: [CONTRIBUTING.md](CONTRIBUTING.md#setup). Python 3.9+;
the core needs no third-party package (only `tomli` before Python 3.11).

## Five-minute start

```bash
mkdir -p ~/taxes && cd ~/taxes
tjs init --country canada   # or usa; inputs/ for every year, 2026/ for this one
cd 2026                     # each year's folder: its taxjson.toml, ticker.map, results
tjs checklist               # every step: what is done, what is next
# drop broker CSVs, all years together, into ../inputs/margin/, ../inputs/rrsp/, …
tjs run                     # books, reports, wash pass, broker check
tjs sum                     # the year, by Schedule 3 line
tjs wash-radar              # "can I sell this at a loss today?"
tjs checklist               # again at year end: ready to file?
```

For last year's return, `tjs init --country canada --year 2025` and
`cd 2025`.

<!-- TODO-SYNC: feat/newuser-fixes adds a demo project (`tjs init --demo` or `tjs demo`); add its one line here. -->

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

1. **`tjs checklist`** in your project: the next step and its command.
2. **A message you don't understand:** search
   [docs/troubleshooting.md](docs/troubleshooting.md) for its text; on an
   older release, also [docs/upgrading.md](docs/upgrading.md#problems-an-upgrade-fixes).
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
| [Upgrading](docs/upgrading.md) | channels, breaking changes, fixes by release |
| [Known issues](KNOWN_ISSUES.md) | open bugs |
| [Security](SECURITY.md) | network egress, files on disk, reporting a vulnerability |
| [Contributing](CONTRIBUTING.md) | development setup, tests, adding a broker |
| [References](REFERENCES.md) | the ITA / CRA / IRC source behind each rule |
| [AGENTS.md](AGENTS.md) | notes for AI assistants helping you |
| [Changelog](CHANGELOG.md) · [Deck](docs/deck/taxjson-deck.pdf) · [Examples](examples/) | history · overview slides · demo CSVs |

## Commands

### Subcommands

`tjs` is the same program as `taxjson` under a shorter name (`tjs sum`,
`tjs -C ~/taxes/2025 stats`); with no command it prints the help page,
grouped as below. Inside a project the help page leaves out the other
country's commands (`taxjson help --all` lists them). Every command takes
`--help`; [docs/commands.md](docs/commands.md) describes each in full.

#### Set up

| Command | Purpose |
| --- | --- |
| [`taxjson checklist`](docs/commands.md#taxjson-checklist) | Every step from install to filing, checked, ending with the next step's command |
| [`taxjson init`](docs/commands.md#taxjson-init) | Create the folder of exports for every year and the year's project (`--single`: one folder) |
| [`taxjson years`](docs/commands.md#taxjson-years) | The year folders sharing one folder of exports, each filed or open |
| [`taxjson new-year`](docs/commands.md#taxjson-new-year) | Start next year's folder from this year's |
| [`taxjson align`](docs/commands.md#taxjson-align) | Bring another year's ticker.map lines and settings over |
| [`taxjson format`](docs/commands.md#taxjson-format) | Lay out `taxjson.toml` like the template `init` writes |
| [`taxjson format-map`](docs/commands.md#taxjson-format-map) | Lay out `ticker.map` in keyword groups |
| [`taxjson migrate`](docs/commands.md#taxjson-migrate) | Convert an older project's files (`missing_history.json`, old maps) |
| [`taxjson fetch`](docs/commands.md#taxjson-fetch) | Download Questrade / IBKR activity (taxjson-fetch plugin) |
| [`taxjson elect`](docs/commands.md#taxjson-elect) | Review or set a merger or spin-off election |
| [`taxjson ticker-map`](docs/commands.md#taxjson-ticker-map) | The ticker.map lines the last run suggested; `--write` adds them |
| [`taxjson update-tobase-map`](docs/commands.md#taxjson-update-tobase-map) | Canada: update the interlisted pairs in `tobase.map` |

#### Build the books

| Command | Purpose |
| --- | --- |
| [`taxjson run`](docs/commands.md#taxjson-run) | Build the books and every report from your exports (`--fast`, `--strict`, `--account`) |
| [`taxjson crypto-sends`](docs/commands.md#taxjson-crypto-sends) | Decide each crypto send that did not arrive in another of your accounts (an arrival from 10 minutes before to 3 days after the send is yours) |
| [`taxjson find-missing-history`](docs/commands.md#taxjson-find-missing-history) | Sales and holdings whose purchase is not in your files |
| [`taxjson opening`](docs/commands.md#taxjson-opening) | An opening balance from a broker's positions report |

#### Summaries

| Command | Purpose |
| --- | --- |
| [`taxjson amt`](docs/commands.md#taxjson-amt) | Canada: the year's minimum tax, line by line |
| [`taxjson estimate`](docs/commands.md#taxjson-estimate) | Estimated tax on the year's investment income (planning only) |
| [`taxjson fx-cash`](docs/commands.md#taxjson-fx-cash) | Currency gains on foreign cash |
| [`taxjson instalments`](docs/commands.md#taxjson-instalments) | Canada: instalments due, paid, and the interest owed |
| [`taxjson stats`](docs/commands.md#taxjson-stats) | Win/loss statistics on closed trades |
| [`taxjson sum`](docs/commands.md#taxjson-sum) | The year's realized gains by account, ending with the lines for your return |

#### Positions

| Command | Purpose |
| --- | --- |
| [`taxjson list`](docs/commands.md#taxjson-list) | Open positions per account, with book cost (`--date` for a past day) |
| [`taxjson shares`](docs/commands.md#taxjson-shares) | Total held per symbol across all accounts |

#### Row listings

| Command | Purpose |
| --- | --- |
| [`taxjson dil`](docs/commands.md#taxjson-dil) | Payments in lieu of dividends over a window |
| [`taxjson divs`](docs/commands.md#taxjson-divs) | Dividends over a window |
| [`taxjson events`](docs/commands.md#taxjson-events) | Every transaction over a window |
| [`taxjson fees`](docs/commands.md#row-listings) | Fees per trade over a window |
| [`taxjson gains`](docs/commands.md#taxjson-gains) | Realized gains in the trade's own currency |
| [`taxjson leaps`](docs/commands.md#taxjson-leaps) | Closed LEAPS positions |
| [`taxjson roc`](docs/commands.md#taxjson-roc) | Return-of-capital and other cost adjustments |
| [`taxjson trades`](docs/commands.md#taxjson-trades) | Buys, sells and assignments |
| [`taxjson transfers`](docs/commands.md#taxjson-transfers) | Custody transfers the books leave out, and what was done with each |

#### Totals by type

| Command | Purpose |
| --- | --- |
| [`taxjson ccd-sum`](docs/commands.md#taxjson-ccd-sum) | Covered-call gains per underlying |
| [`taxjson dil-sum`](docs/commands.md#taxjson-dil-sum) | Payments in lieu per symbol, with their tax treatment |
| [`taxjson divs-sum`](docs/commands.md#taxjson-divs-sum) | Dividends per ticker |
| [`taxjson fees-sum`](docs/commands.md#taxjson-fees-sum) | Trading fees by broker |
| [`taxjson leaps-sum`](docs/commands.md#taxjson-leaps-sum) | LEAPS gains per contract |
| [`taxjson roc-sum`](docs/commands.md#taxjson-roc-sum) | Return of capital per ticker |
| [`taxjson trades-sum`](docs/commands.md#taxjson-trades-sum) | Buys, sells and fees per ticker |
| [`taxjson winners`](docs/commands.md#taxjson-winners) | Biggest realized winners and losers |

#### Before you trade

| Command | Purpose |
| --- | --- |
| [`taxjson wash-radar`](docs/commands.md#taxjson-wash-radar) | Open superficial-loss / wash-sale windows as of today |
| [`taxjson buy-check`](docs/commands.md#taxjson-buy-check) | Would buying this today cancel a recent loss? |
| [`taxjson sell-check`](docs/commands.md#taxjson-sell-check) | Would selling this at a loss today keep the loss? |
| [`taxjson harvest`](docs/commands.md#taxjson-harvest) | Unrealized gains and losses at current prices |
| [`taxjson tips`](docs/commands.md#taxjson-tips) | Tax-efficiency tips for next year |
| [`taxjson watch`](docs/commands.md#taxjson-watch) | What changed since the last watch (for cron) |

#### Before you file

| Command | Purpose |
| --- | --- |
| [`taxjson form-export`](docs/commands.md#taxjson-form-export) | Schedule 3 / Form 8949 rows, or a TurboTax TXF |
| [`taxjson t1135`](docs/commands.md#taxjson-t1135) | Canada: the foreign-property test and tables |
| [`taxjson reconcile-slips`](docs/commands.md#taxjson-reconcile-slips) | Compare broker T5008 / 1099-B slips with the books |
| [`taxjson slip-audit`](docs/commands.md#taxjson-slip-audit) | Canada: T5 / T3 slips against the books' income |
| [`taxjson carryover`](docs/commands.md#taxjson-carryover) | Capital-loss carryforward ledger across years |
| [`taxjson option-boundary`](docs/commands.md#taxjson-option-boundary) | Canada: written options across a year end |
| [`taxjson close-year`](docs/commands.md#taxjson-close-year) | Lock the year you filed (`filed/<year>.json`) |
| [`taxjson check-filed`](docs/commands.md#taxjson-check-filed) | Recompute filed years and report any drift |
| [`taxjson handoff`](docs/commands.md#taxjson-handoff) | Check this year starts where last year's return ended |

#### Explain and check

| Command | Purpose |
| --- | --- |
| [`taxjson audit`](docs/commands.md#taxjson-audit) | Trace every capital gain back to its broker row |
| [`taxjson wash-sales`](docs/commands.md#taxjson-wash-sales) | Each loss denied this year, and why (`--explain`) |
| [`taxjson tax-logic`](docs/commands.md#taxjson-tax-logic) | Every tax rule applied, one line each (`--ids`) |
| [`taxjson edge-cases`](docs/commands.md#taxjson-edge-cases) | Trades on a year-end or 30-day-window edge |
| [`taxjson check-dates`](docs/commands.md#taxjson-check-dates) | Trade and settlement dates checked against each market |
| [`taxjson sanity`](docs/commands.md#taxjson-sanity) | Compare positions (and costs) with the broker's holdings |
| [`taxjson journals`](docs/commands.md#taxjson-journals) | Broker journals between two listings of one security |
| [`taxjson renames`](docs/commands.md#taxjson-renames) | Ticker changes as dated events |
| [`taxjson spinoffs`](docs/commands.md#taxjson-spinoffs) | Spin-offs: election, value used, cost booked |
| [`taxjson splits`](docs/commands.md#taxjson-splits) | Splits and consolidations, holdings before and after |

#### Maintainer

The release commands, for the machine taxjson is developed on: `taxjson help`
lists them only there (`taxjson help --all` everywhere).

| Command | Purpose |
| --- | --- |
| [`taxjson channels`](docs/commands.md#taxjson-channels) | Where each release channel points; what this machine runs |
| [`taxjson deploy`](docs/commands.md#taxjson-deploy) | Development machine: install a release here |
| [`taxjson promote`](docs/commands.md#taxjson-promote) | Development machine: point `stable` or `beta` at a release |

#### Tools

| Command | Purpose |
| --- | --- |
| [`taxjson redact`](docs/commands.md#taxjson-redact) | Copy exports with account numbers, names and addresses removed |
| [`taxjson help`](docs/commands.md#taxjson-help) | The help page, or one command's (`--all`: both countries') |

## Contributing and licence

Pull requests are welcome: [CONTRIBUTING.md](CONTRIBUTING.md) (every fix
lands with a regression test). MIT licence — see [LICENSE](LICENSE).
