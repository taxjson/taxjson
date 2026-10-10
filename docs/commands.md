# Command reference

Every `taxjson` command, what it does, and what it writes. The README
lists the commands one line each; this page is the long form. The tax
law behind the figures is in [tax-rules.md](tax-rules.md), every
`taxjson.toml` key and project file in [settings.md](settings.md), and a
first project from start to finish in
[getting-started.md](getting-started.md).

`tjs` is the same program as `taxjson` under a shorter name (`tjs sum`,
`tjs -C ~/taxes/2025 stats`). `taxjson` (or `tjs`) with no command prints
the help page; `taxjson COMMAND --help` explains one command, and
`taxjson --version` prints the installed version. The global `-C DIR`
runs a command in a project that is not the current directory.

The help page and this page group the commands by what they are for.
Inside a project the help page leaves out the other country's commands
(`taxjson help --all` lists every one, marked (Canada) / (USA)). Running
another country's command in a project is refused with the reason.

Contents:

- [Everyday workflow (`taxjson run`)](#everyday-workflow-taxjson-run)
- [The workflow, step by step (`taxjson checklist`)](#the-workflow-step-by-step-taxjson-checklist)
- [Set up](#set-up)
- [Build the books](#build-the-books)
- [Summaries](#summaries)
- [Positions](#positions)
- [Row listings](#row-listings)
- [Totals by type](#totals-by-type)
- [Before you trade](#before-you-trade)
- [Before you file](#before-you-file)
- [Explain and check](#explain-and-check)
- [Maintainer](#maintainer)
- [Tools](#tools)
- [Renames, non-cash distributions and capital-gains dividends](#renames-non-cash-distributions-and-capital-gains-dividends)
- [Standalone analysis tools](#standalone-analysis-tools)

## Everyday workflow (`taxjson run`)

For a configured project the whole pipeline runs from one command. Set
up a project once with `taxjson init --country canada` (or
`--country usa`; the flag is required and shapes the scaffold's
currencies, tax-date basis and account folders). By default `init` makes
one folder of exports for every year, with the year's project beside it:

```
DIR/
  inputs/<account>/     every year's broker exports, one folder per account
                        (each folder's README.txt says which export to
                        download per broker); canada: margin/ tfsa/ rrsp/
                        crypto/, usa: margin/ roth/ 401k/ crypto/
  tobase.map            Canada: the interlisted pairs every year reads
  .gitignore
  2025/                 the year's project (YYYY/, from --year)
    taxjson.toml        year, country, base_currency, [accounts.*];
                        inputs_dir = "../inputs", exports_dir = "../exports"
    ticker.map          the year's symbol rules, commented
    holdings/           the broker's positions snapshots for that year
    work/               per-stage intermediate files (rebuildable; gitignored)
    reports/            every report lands here
```

Every other command runs in the year folder (`taxjson -C DIR/2025 run`);
`taxjson new-year 2026` adds the next one beside it. `taxjson init
--single` makes the older layout instead: one folder for one year, with
`taxjson.toml`, `ticker.map`, `inputs/<account>/`, `work/` and
`reports/` all in `DIR` (`taxjson -C DIR run`). Add an `[accounts.NAME]`
section and an `inputs/NAME/` folder for any other account (a LIRA, an
IRA). See "One folder of exports for every year" in
[getting-started.md](getting-started.md#one-folder-of-exports-for-every-year).

Every `taxjson.toml` key and every file the pipeline reads is described
in [settings.md](settings.md). The books in `work/` belong to the country
they were built under: after changing `country`, every report refuses
them until `taxjson run` rebuilds them (their figures follow the other
country's law).

Then, whenever you add new statements:

```bash
taxjson run             # full pipeline, full rebuild: never serves stale data
taxjson run --fast      # incremental: stages whose inputs are unchanged are skipped
cat reports/margin.sum  # the per-account report
```

The stages run in-process (no interpreter start-up per stage).
`taxjson run` does everything end to end: it parses each broker file,
applies corporate actions, merges and converts to the base currency,
runs the ACB / FIFO gains engine with the superficial-loss / wash-sale
pass across all your accounts, and writes every report to `reports/`.

A new merger or spin-off asks once for its tax election. The answer is
saved to `inputs/<account>/manifest.json`, which you should commit. A
pure ticker rename elects itself, and a sheltered account's merger or
spin-off is booked without asking unless `sheltered_elections = "ask"`.
`taxjson elect` reviews or changes an election later.

**Headless runs.** Without a terminal to prompt (or with `taxjson run
--no-input`), an unresolved election never hangs or crashes the run. The
affected account is deferred, the pending events (with every option,
description and required hint) are written to
`work/pending_elections.json`, and the run exits **3**. Inspect them with
`taxjson elect --pending` (ready-to-copy `--set` lines; `--json` for
machines), resolve each with `taxjson elect <account> --set
<event_id>=<election> [--hint KEY=VALUE]`, and run again.

**Exit codes**, the same for every command:

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | failure, or a command's finding (drift, a handoff problem, a lint hit) |
| 2 | usage error, or a named input or output that cannot be read or written (missing, a directory, not UTF-8, not valid JSON) |
| 3 | elections required (`taxjson run`) |
| 130 | interrupted (Ctrl-C) |
| 141 | the reader of stdout went away (`taxjson trades \| head`) |

Every `taxjson` command and `taxjson-*` tool reports an unreadable input
in one line, never a Python traceback. Only one `taxjson run` runs in a
project at a time (`work/.run.lock`); a second one refuses.

What a run writes:

- `reports/<account>.sum`, `reports/<account>_wash.sum`: realized gains
  and the wash-sale / superficial-loss detail.
- `reports/wash_radar_<account>.rpt` and `.json`: the superficial-loss
  "safe to sell at a loss?" advisor (the JSON sidecar carries absolute
  clear dates; `taxjson harvest` computes countdowns from it).
- `work/<account>_<broker>_transfers.json`: the custody-transfer
  sidecar, the TRANSFER rows the parse stage keeps out of the books
  (evidence, not tax events); `taxjson transfers` reads these.
- `work/<account>_transfer_costs.json`: the acquisitions a taxable
  account's books take for shares that arrived by transfer from outside
  your books at a book value the broker states on the row (Questrade,
  RBC); see "Transfers in" in
  [getting-started.md](getting-started.md#5c-transfers-in).
- `work/<account>_own_moves.json`: US projects only. The moves between
  two of your own taxable accounts that the run paired from that
  evidence (and the crypto-sends pairing), as TRANSFER legs in both
  accounts' books; the US engine hands the sender's FIFO lots (basis,
  purchase dates) to the receiver with no sale (tax-logic US-BASIS-05).
- `reports/crosslistings.rpt`: cross-listed (`.TO` / `.US`) tickers the
  radar may not consolidate.
- `reports/fees.rpt`: trading fees by brokerage, with comparison stats.
- `reports/ccd.rpt`, `reports/leaps.rpt`: cross-account covered-call and
  long-option views. `leaps.rpt` lists every long option close of any
  tenor; `taxjson leaps-sum` is the LEAPS-only figure. Rows with an
  unknown cost (no purchase in your files) are left out and counted, as
  in `taxjson ccd-sum`.
- `reports/<account>_holdings.toml`: machine-readable positions, with
  native and base-currency cost and the per-position acquisition / sale
  `trades` history. A cost adjustment paid in another currency than the
  listing's (a USD return of capital on a `.TO` stock) is restated in the
  listing's currency at the row's date for the native view, with a note.
  `cost_per_share` is `total_cost / quantity`, so for an option it is
  per contract; divide by `contract_multiplier` for the per-share price
  the `trades` show. A futures option carries the future's
  `contract_multiplier` that its broker rows declared (IB's instrument
  list: CL 1000, ES 50) and none when no row declared it, never the
  equity 100; a plain future is `asset_type = "future"`.

**What to check next.** A full run ends (after `Done.` and the holdings
check) with a short summary of what the books themselves show is still
incomplete. It is silent when there is nothing:

```
==> Before you trust these numbers (docs/getting-started.md, step 5)
Warning: NOT in the totals: 1 sale with no purchase (SAMPA.TO) — `taxjson find-missing-history`
Warning: 1 position at a $0 cost (SAMPQ.TO) — `taxjson find-missing-history`
Warning: 1 transfer-in kept out with no cost (SAMPK.TO) — `taxjson transfers`
Info: 1 account with positions not checked against the broker's holdings — `taxjson sanity`
Info: Then run `taxjson checklist` (the next step); each message's detail: `taxjson run --details`
```

One line per finding (at most six under the heading), each naming the
command with the detail; `taxjson run --details` words each in full
("1 position sold in 2025 with no purchase in your files, not in
missing_history.tt: SAMPA.TO (margin). Those sales are NOT in `taxjson
sum`; run `taxjson find-missing-history`."). The same counts are written
to `reports/run_summary.json`. A taxable
account's positions that go short (sales with no purchase in the files)
are also named on the console as the run builds that account.

When the year is over, [filing.md](filing.md) is the checklist that
takes a project from "last export dropped in" to a filed and locked
return, with the command that proves each step. `taxjson checklist`
runs it, from set-up to year end.

## The workflow, step by step (`taxjson checklist`)

`tjs checklist` is the map of the whole job: run it whenever you are not
sure what comes next. Outside a project it prints every step; inside one
it checks each, marks it from what the checks and the project's files
say, and ends with the next step's command. It writes nothing to the
project but your own marks (`checklist.json`). The flags are under
[`taxjson checklist`](#taxjson-checklist) below.

`--json` prints the same list as one document, a stable schema
(`schema_version` 2) described in
[settings.md](settings.md#taxjson-checklist---json).

## Commands

One section per group of the help page, in its order. The report and
query commands read what the last `taxjson run` wrote to `work/` (no
recompute); every command takes `--help`.

A command prints the essentials first ([output-style.md](output-style.md)):
a one- or two-line legend above each table, the figures, then one line
per thing to act on (starting `! `, naming the command with the detail).
A command that leaves something out ends by naming `--details`, which
prints it all: the explanations, notes, caveats and citations, nothing
dropped. `--json` is the same either way.

### Set up

#### taxjson checklist

`taxjson checklist [--all] [--walk] [--done ID] [--skip ID] [--undo ID] [--note TEXT] [--reset] [--only ID] [--quick] [--show] [--json]`

Every step from install to filing, in order, each with the exact
command(s) and a one-line why, in ten sections: Set up (install or
upgrade, `init`, the accounts), Get your files (full year plus January,
each broker's exports reaching the year end, the sheltered and crypto
accounts), Build the books (`run`, elections), Fill the gaps (missing
history, transfers, ticker.map, journals, renames, crypto sends, `.tt`
lines, return of capital), Tidy the config (`format`, committed inputs),
Check (`tips`, `sanity`, `edge-cases`, option-boundary, the
superficial-loss / wash-sale denials, filing positions, `check-dates`,
`audit`, `handoff`), Results (`sum`, the slips, `form-export`, T1135,
carryover, FX on cash, carrying charges, estimate, AMT), Through the
year, Share safely (a redacted sample) and Year end (`close-year`, the
lock committed, next year's project, the notice of assessment).

Inside a project (a `taxjson.toml` here, or `-C DIR`) each step is
checked: a check runs the command that proves it (sanity,
find-missing-history, elect, audit, option-boundary, handoff,
reconcile-slips, form-export, t1135, carryover, fx-cash, amt,
check-filed, git status) or reads the project's files. The default view
is the essentials: the done count, one row per section with its counts
(done, attention, blocked, to do, to confirm, to read), one `! ` line per
step needing attention or blocked (`! 10 missing-history: … — tjs
find-missing-history`; steps blocked for one reason share a line), and
the next step last. `--all` (or `--details`) is the full list: each step
shows done `[x]`, needs attention `[!]`, to do `[ ]`, blocked `[b]`
(usually: run `taxjson run` first), to confirm `[m]`, yours to run and
read `[?]`, n/a `[-]` or skipped `[~]`, with its commands and why; the
first step to do or needing attention is marked `[>]`, and the last line
names it with its command.

Nothing in the project is written but `checklist.json` (the marks;
commit it). The steps no command can prove are confirmed with `--done
ID` (`--note TEXT` stores a note with the mark). A mark never hides a
later finding: a step marked done whose detector finds a problem shows
`[!]` (or `[b]` when the detector could not check) with the mark beside
it and keeps the list open. `--skip ID` is the explicit "reviewed,
accepted"; a `[?]` step can be ticked off the same way. `--undo ID`
removes one mark, `--reset` removes them all (deletes `checklist.json`),
and `--show` prints the list after a `--done` / `--skip` / `--undo`.
`--only ID` checks one step, `--walk` visits the open steps one at a
time, and `--quick` skips the slow checks (audit, sanity, ...).

form-export is checked against `sum`'s FOR THE RETURN totals and the
taxable realized gain. US projects get the US names (1099-B, Form 8949 /
Schedule D), `n/a` for Canada-only steps and only their own commands; a
project with no taxable account gets `n/a` for the taxable-only steps.
Exit 1 while anything is open (a `[?]` step never keeps it open).
Outside a project it prints the steps as a guide, exit 0. `--json`: a
stable schema ([settings.md](settings.md#taxjson-checklist---json)).

#### taxjson init

`taxjson init --country canada|usa [PATH] [--year YYYY] [--single] [--force]`

Scaffold a new project. By default it makes **one folder of exports for
every year**: `PATH/inputs/<account>/` (every year's broker exports,
shared, each folder with a README.txt saying what to download), a
`.gitignore`, in Canada `PATH/tobase.map` (the interlisted pairs every
year reads), and the year's folder `PATH/YYYY/`, a complete project: its
own `taxjson.toml` (with `inputs_dir = "../inputs"` and `exports_dir =
"../exports"`), its `ticker.map`, and an empty `holdings/` for the
broker's positions snapshots. `--year` picks the year (default: the
current year). `--single` scaffolds one folder for one year instead
(`inputs/` inside it). See "One folder of exports for every year" in
[getting-started.md](getting-started.md#one-folder-of-exports-for-every-year).
`--force` re-templates an existing `taxjson.toml` (kept as `.bak`).

The generated `taxjson.toml` lists every key the country's projects
read, documented: the scaffold's values active, every other key
commented out with its default (or an example where it has none), each
key under its description, `[settings]` in groups and the other tables
alphabetical, with one `=` column per table. `local_timezone` is set to
this machine's IANA zone when it can be read (else left commented: the
default zone). The `ticker.map` is laid out in keyword groups, with a
commented-out example in each.

#### taxjson years

`taxjson years [--json] [--diff YEAR YEAR]`

The year projects sharing one folder of exports (run it there or in a
year folder): each year filed (its `filed/<year>.json` lock, its date
and totals) or open, its last full run, whether the inputs changed since
then (for a filed year: `taxjson run` there rechecks the filed figures
against the lock), whether its `ticker.map` rules and settings differ
from the newest year's, and its checklist marks. `--diff A B`: the rules
and keys that differ between two years. `--json`: a stable schema
([settings.md](settings.md#taxjson-years---json)).

#### taxjson new-year

`taxjson new-year YYYY`

Start the year's folder beside the others from the previous year's: its
`taxjson.toml` (year set, `prior_year_record` pointed at last year's
lock, last year's `[estimate]` / `[instalments]` and the accounts'
`holdings` commented out for reference) and its `ticker.map` copied, and
an empty `holdings/`. In Canada the new year keeps reading the
`tobase.map` every year shares (a year from before v0.27.1 that has its
own copy passes it on), and keeps the option grant-timing cutoff the
previous year applied by default (`option_grant_timing_since`, set to
that year when it was not set). The files are made in a staging folder
and moved into place when complete: a failure leaves no `taxjson.toml`
behind, so the command can be run again. It prints where to save the
year's downloads.

#### taxjson align

`taxjson align --from YEAR [--write [--all]] [--json]`

The `ticker.map` rules and `taxjson.toml` keys of another year's project
that differ from this one's (the year's own keys and the accounts' ids
and `holdings` aside; an array of tables such as `[[distributions]]` is
one key; `accounts (order)` when the accounts are in another order). A
file that cannot be read is an error, never "the same". `--write` brings
the other year's into this project, asked one by one on a terminal
(`--all`: every one), keeping the previous file as `.bak`; the other year
is never written. Every chosen change is made and read back first: the
files are written only when all of them can be (otherwise nothing is),
and the accounts' order is shown, never rewritten.

#### taxjson format

`taxjson format [--write [--no-backup] | --check]`

Lay an existing `taxjson.toml` out like the template `init` writes, so
two years' files diff only where their values differ. Every key of the
project's country goes in its place in its table (its `[settings]`
group, or alphabetical; an account's `type` first): the ones you set
active with your values, the rest commented with their default, each
under its description (account tables compact), one `=` column per
table, accounts in your order, `[[...]]` entries in order, values in
canonical TOML (strings quoted, dates as dates). The tables come in a
fixed order: `[settings]`, then the accounts, then `[estimate]`,
`[carryover]`, `[[distributions]]` and, in Canada,
`[[capital_gains_dividends]]` and `[instalments]`.

Nothing is lost. Keys the template does not know stay in their table
under a "Not in the template" line, alphabetically (and are named on the
console). A trailing comment moves onto its own line just above its key
or table line (with the `#` lines that continue it). A comment block
stays above the key or table that follows it and moves with it (a block
holding a commented-out key of your own, `# province = "BC"`, goes to
that key; a block a blank line separates from the next table stays at
the end of its table). A multi-line value with comments inside is kept
as written; anything it cannot place goes to a "Your notes (kept by tjs
format)" block at the end. Comment lines that are the template's own
text (or an earlier `init`'s) are regenerated.

The parsed configuration before and after must be identical, or nothing
is written. Default: a dry run printing a unified diff. `--write` writes
it (atomically, the file's mode kept, the old file saved as
`taxjson.toml.bak`, or the next free `.bakN`; `--no-backup` skips that).
`--check` exits 1 when the file is not formatted or a migration is
pending (CI). Formatting a formatted file changes nothing. Like every
command it refuses a config the config check refuses, and a project with
old per-purpose files (`taxjson migrate` first).

#### taxjson format-map

`taxjson format-map [--write [--no-backup] | --check]`

Lay `ticker.map` out the way `init` writes it: a short header saying
what the file is, then the rules in groups in a fixed order, each under
a `## --- <Group> ---` heading naming its keywords: Spellings (`GLOBAL`,
and `RENAME` without a date), Listings of one security (`TOBASE`,
`DISTINCT`), Clean-up (`DELETE`), Dated events (legacy dated `RENAME` and
`JOURNAL` lines, only until they are migrated), Lookups (`QUOTE`,
`EXTRACT`, `CRYPTO`, `T1135` and the market lists `STABLE` ... `VENUE`),
then Retired (`TRADINGVIEW`) and Unrecognized only when the file has such
lines.

It also migrates the legacy dated events: each `JOURNAL A B` becomes
`TOBASE A B`, and each dated `RENAME OLD NEW YYYY-MM-DD [late=…]` moves,
with its comments, to `inputs/<account>/renames.tt` as `RENAME
YYYY-MM-DD OLD NEW [late=…]`. It goes to the first account whose books
carry the change and whose own `.tt` lines of it agree, one per account
kind; a `.tt` RENAME applies to every account of its kind, securities or
crypto. It simulates the run first and writes nothing (exit 2, naming
the lines) unless the books stay the same, the project's own `.tt`
RENAME lines included.

Within a group your order is kept (the first matching `EXTRACT` wins). A
rule line gets one space between fields (`EXTRACT` keeps its ` | `
separators), the keyword upper case and an inline `# note` kept beside
it; exact duplicate lines are dropped (named on the console). A comment
block directly above a line (no blank line between) moves with it, and
so does one directly below a line that a blank line ends. A
commented-out rule (`# GLOBAL A B`) moves like a comment, or goes to its
keyword's group on its own; any other comment block stays under the
group heading it sits in (in a file never formatted: before the rule
that follows it, or at the top when it comes before every rule). Comment
lines are kept byte for byte (trailing blanks dropped); the header and
group text it writes, and an older `init` template's comment paragraphs,
are regenerated. A line `taxjson run` cannot use (no keyword, malformed,
a second target for one symbol) is kept exactly as written, with its
comments, in the Unrecognized group at the end, and the console names
the problem of each.

The parsed map before and after (plus the moved lines) must mean the
same and every comment be kept, or nothing is written. Default: a dry
run printing a unified diff, the `.tt` additions and the lines per
group. `--write` writes it (the old file kept as `ticker.map.bak`, or
the next free `.bakN`; `--no-backup` skips that). `--check` exits 1 when
the file is not formatted, a migration is pending or `taxjson run`
refuses the map (CI). Formatting a formatted file changes nothing.

#### taxjson migrate

`taxjson migrate [--dry-run] [--write] [--to-years]`

Moves an older project's files into the ones that hold them now.

- `missing_history.json` becomes dated `.tt` lines (`OPENING <date>
  <SYMBOL> <qty> cost=unknown` in `inputs/<account>/missing_history.tt`),
  each entry sized as the project's run sizes it. In a year folder of
  the shared layout every year folder's file is merged (disagreements
  listed, written only with `--write`, the newest year's view; entries
  that open nothing dropped) and each file renamed
  `missing_history.json.migrated`.
- `yf_ticker.map`, `crypto_ticker.map`, `ticker_extraction_overrides.txt`
  and `t1135.map` become `QUOTE` / `CRYPTO` / `EXTRACT` / `T1135` lines
  appended to `ticker.map`; `amt_carryover.txt`, `claimed_losses.txt`,
  `capital_gains_dividends.map` and `distributions.map` become
  `[estimate] amt_carryover`, `[carryover] claimed`,
  `[[capital_gains_dividends]]` and `[[distributions]]` in
  `taxjson.toml`. Each file is read with its old rules (what it meant
  before is what the new lines mean). The lines are appended (a key under
  an existing `[estimate]` / `[carryover]` header goes right below it):
  your content and comments are never rewritten, and each old file is
  renamed `<name>.migrated`, never deleted. It refuses, writing nothing,
  when an old line cannot be read or ticker.map / taxjson.toml already
  holds a conflicting entry (an identical one is skipped). While any of
  those old files is in the project, every other command stops (exit 2)
  naming it and this command. A leftover `tv_exchange.map` (the removed
  TradingView export) is only renamed `tv_exchange.map.migrated`, and
  stops nothing meanwhile (`taxjson run` notes it once).
- `--to-years` turns a single-folder project into one folder of exports
  for every year: `inputs/` stays, shared, and the project's own files
  (`taxjson.toml`, `ticker.map`, `missing_history.json`,
  `checklist.json`, `work/`, `reports/`, `filed/`, `inputs/slips/`) move
  into the folder of its year, with `inputs_dir` and `exports_dir` set.
  In Canada `tobase.map` stays at the top, the one file every year reads
  (`tobase_map = "../tobase.map"`).
- In a multi-year project made before v0.27.1 (run in the folder holding
  the years, or in a year folder) it makes the years' `tobase.map`
  copies one file beside the year folders that every year reads.
  Identical copies are merged at once (each year's `taxjson.toml` gains
  `tobase_map = "../tobase.map"`, each copy kept as `tobase.map.bak`);
  copies that differ are listed with the lines of yours only an older
  copy has, and written only with `--write` (the newest year's file
  kept).

`--dry-run` prints the lines it would append and the moves.

#### taxjson fetch

`taxjson fetch [ACCOUNT ...] [--list] [--fetcher NAME] [--dry-run] [--json] [--year N] [--days N] [--from YYYY-MM-DD] [--refresh-token TOKEN] [--flex-token TOKEN] [--positions] [--trim-overlap]`

Download broker activity straight into `inputs/` through an installed
fetcher plugin (`--list` names them; with none installed it prints one
install line, exit 2). The taxjson-fetch plugin covers the Questrade
REST API and the IBKR Flex Web Service. The installer installs it by
default (`--without-fetch` leaves it out); from a checkout, `pip install
--no-deps -e packages/taxjson-fetch` after the core; it is not on PyPI.
Each account is configured with `brokerage` plus `account` / `query_id`
under `[accounts.<name>]`. It writes files the existing parsers already
read; hand-exported CSVs keep working side by side.

Questrade defaults to the whole tax-year window plus the
superficial-loss margins (Dec 1 of the prior year through Jan 31 of the
next, capped at today); `--year N` backfills a past year, `--from` /
`--days` override the window. IBKR re-covers the Flex query's configured
period. `--trim-overlap` drops rows your manual Questrade exports
already cover (originals kept as `.bak`), `--dry-run` previews.
Credentials: `--refresh-token` (Questrade) and `--flex-token` (IBKR, else
`$IBKR_FLEX_TOKEN`). `--positions` also snapshots live Questrade holdings
to the year's holdings folder as `holdings/<account>_live_holdings.toml`,
where `taxjson sanity` finds it. `--json` prints a machine-readable
summary. Chain it: `taxjson fetch run`.

#### taxjson elect

`taxjson elect [ACCOUNT] [--redo] [--reset] [--event ID] [--set EVENT_ID=ELECTION] [--hint KEY=VALUE] [--pending] [--json]`

Lists the corporate-action events in the books (mergers, spin-offs,
reorganisations) with the tax election recorded for each. `--redo`
clears the election(s) and asks again now; `--reset` clears them so the
next `taxjson run` asks; `--event ID` limits either to one event.
`--set EVENT_ID=ELECTION` writes one election without asking (scripts,
CI), with `--hint KEY=VALUE` for the values an election needs (for
example `--hint fmv_per_share=12.5`); `taxjson elect ACCOUNT` lists each
event's choices. `--pending` shows what a `--no-input` run left
unresolved (`work/pending_elections.json`), with ready-to-copy `--set`
lines; `--json` with it prints the raw document.

#### taxjson ticker-map

`taxjson ticker-map --suggest [--write [--all]] [--json]`

Every ticker.map line the last `taxjson run` suggested, each with its
reason, read from `work/` (the run's `.diag` files and states):

- two listings a transfer journal pairs that the run did not join itself
  (`TOBASE`; never two listings whose names name different companies);
- a Questrade internal code with a likely ticker;
- a ticker change Questrade or RBC shows (`GLOBAL`);
- a ticker change IB shows only as one contract id under two symbols (a
  dated `RENAME OLD NEW YYYY-MM-DD`: OLD the symbol whose rows end first,
  the date the first row of the other). IB's temporary time-stamped
  symbols (`20260101093000QZX`) are folded onto their ticker by the <!-- pii-ok: synthetic timestamp -->
  parser and never suggested; a ticker.map line naming the stamped
  symbol, in any keyword, keeps it as written;
- a coin's Yahoo id (`CRYPTO`);
- an `EXTRACT` line (plus its `JOURNAL`) for a **symbol collision**: a
  `.US` symbol whose rows name two different companies, one of them a
  Canadian-listed fund's US-dollar units (a TSX currency fund's US-dollar
  unit booked `.US` beside an NYSE stock of the same root). The run says
  it as a `Warning:`, and the suggested line moves the fund's rows to its
  `ROOT.U.TO` (the TSX unit-class convention in `markets.toml`), its
  words the shortest run common to every description of that fund in the
  project and in no other row's. When no such run exists the line is a
  template with a placeholder, listed but never written. Two names alone
  are no collision: a company that renamed itself has two.

A line the map already answers (the same line, a rule already mapping
the symbol, `DISTINCT`, a dated `RENAME` of the pair, an `EXTRACT` for
the same rows) is left out, and listed as such; of two lines for the
same rows the first is offered, the other listed as covered by it. A
hint the run gives under a condition ("only if the position is really
held under the other listing", "if it is the same security") is
suggested only when the project's books (parsed exports, `.tt` and
`OPENING` lines, the `holdings` files) hold every symbol the line joins:
income on a US stock no RBC file trades is never moved to a `.TO`
listing the project does not have.

Also listed:

- each pair of a `.US` and a Canadian listing of one root that the map
  does not answer (MAP-GAP, "To verify": `--write` asks `TOBASE`,
  `DISTINCT` or skip on a terminal; `--all` writes only the lines the
  run's evidence names, never one of these). Its reason says whether the
  exports name them alike or the names were not compared. A shared root
  is a candidate, not proof. A pair the exports show apart is not listed
  and needs no `DISTINCT` line: the Canadian line named as a depositary
  receipt (a CDR) or listed on a receipt venue, or names of two
  different companies. A loss on one listing with the other listing
  bought within 30 days is a `TOBASE` suggestion;
- never written, each rename rule no symbol of the books reaches
  ("Unused rules, delete?"). This is root-aware: a rule with no stock
  rows is still live when option trades carry its root
  (`ABC271217C00050000.US` needs `TOBASE ABC.US ABC.TO`), and a rule
  reached through another rule's target (a rename chain) is live. FROM
  must match exactly, so a suffix-less `GLOBAL QQOL QQNW` is listed (with
  a hint to write `QQOL.US`) when the books only carry `QQOL.US`. Unused
  rules are harmless; delete one by hand only when you know the symbol
  will not return;
- in Canada, your `TOBASE` / `DISTINCT` lines that `tobase.map` already
  states identically ("covered: delete?"). On a terminal `--write` asks
  keep or delete for each (keep is the default); `--all` never deletes
  one.

`--write` appends the chosen lines to `ticker.map`, each under a `#`
comment with the date and reason: on a terminal it asks for each one
(y/n/q), `--all` adds every one the evidence names. The old file is kept
as `ticker.map.bak` (or the next free `.bakN`), and nothing is written if
the new map would contradict itself. Run `taxjson run` again to apply
them. `--json`: `suggestions` (the evidence lines only), `verify` (the
pairs to verify), `skipped` and `unused`, each record with `kind` and
`certainty` (`evidence`: the run's evidence names the line; `verify`:
yours to decide); under `--write` the plain lines go to stderr.
[settings.md](settings.md#taxjson-ticker-map---suggest---json) has the
schema.

#### taxjson update-tobase-map

`taxjson update-tobase-map [--write [--no-backup]] [--json]` (Canada)

Bring the project's `tobase.map` (the interlisted master's pairs: a TSX
share's US exchange and OTC listings, one security; tax-logic
CA-XLIST-06) in step with the master this taxjson installs. It lists:

- the lines added (new interlistings);
- ended interlistings (annotated `until=`, never deleted);
- retracted lines (removed only when unedited; an edited one is
  flagged);
- your edits (kept; the master's line for the same listing is not added
  beside them);
- master lines you deleted (recorded as `# removed:`, never added back);
- ticker changes (the new line added, the old kept; the dated `.tt`
  `RENAME` event to write only when the two do not already book as one);
- the pairs `ticker.map` decides otherwise (ticker.map wins; reported
  only);
- the `DISTINCT` lines earlier versions wrote for a depositary receipt
  (retracted: look-alike listings are never joined, and the master keeps
  a CDR apart from its US share without a line).

Without a `tobase.map` it shows which pairs would change the books. In a
multi-year project the years read one `tobase.map` beside the year
folders (`[settings] tobase_map = "../tobase.map"`): run it in any year
folder, or in the folder holding them, and it updates that file for
every year (`taxjson migrate` there makes the per-year copies of a
project made before v0.27.1 one file). Dry run by default; `--write`
applies it (the previous file kept as `tobase.map.bak`). A US project is
refused (Canada only for now). See [settings.md](settings.md#tobasemap).

### Build the books

#### taxjson run

`taxjson run [--fast] [--account NAME] [--strict] [--no-input]`

Run the full pipeline, rebuilding every stage: the recommended everyday
command, so results always reflect the current inputs, config and code.
What it does and writes is in
[Everyday workflow](#everyday-workflow-taxjson-run) above. The run ends
with the broker positions cross-check (`taxjson sanity`) when the
project has holdings files.

- `--fast`: an incremental run. Cached stages whose inputs,
  `taxjson.toml` and installed code are all unchanged are skipped. Input
  files and the project-root map files are compared by content (size +
  SHA-256), so a corrected export copied over with an older mtime
  (`cp -p`, `rsync -a`, unzip) still rebuilds; taxjson's own code and its
  shipped market data (`data/markets.toml`) are compared by content too
  (a change since the last complete run rebuilds everything); the rest of
  the cache is mtime-based. `--fast` trades that safety net's edge cases
  for speed.
- `--account NAME`: re-run a single account. It skips the cross-account
  wash-sale and cross-listing detection, which need a full run.
- `--strict`: per-account validation ERRORs (oversold positions,
  malformed rows) and an input file that parsed to 0 transactions stop
  the run instead of publishing reports with a DIAGNOSTICS banner. Also
  fatal: UNBOOKED rows, an undecided crypto send, a gift or payment send
  with no fair value, a send booked twice (a hand-written `.tt` line next
  to `crypto_sends.tt`), `work/` books of an account no longer in
  `taxjson.toml`, and filed-year drift. Recommended for CI and cron.
- `--no-input`: never prompt. Unresolved elections are written to
  `work/pending_elections.json` and the run exits 3 (see
  [Headless runs](#everyday-workflow-taxjson-run)).

Commands chain in one invocation, each with its own flags: `taxjson run
sum`, `taxjson run close-year check-filed`. A chained `--json` command's
stdout follows the earlier commands' progress output, so pipe consumers
should run the JSON command on its own. A failing command stops the
chain and its exit code is the chain's. The whole chain is checked
before any of it runs. `-C DIR` (before the first command) applies to
every command, whatever the folder is called (`taxjson -C run run sum`).
A command name starts the next command only when the command before it
cannot take the word: an option's value (`--account sum`), an account
of the project (`taxjson events sum` with an `[accounts.sum]`), a folder
(`taxjson init sum`) or a symbol (`taxjson audit sum`) stays the
command's own. `--` always separates two commands: `taxjson events --
sum` runs `events`, then `sum`. With no account of that name, `taxjson
events sum` and `taxjson fetch run` chain as written.

#### taxjson crypto-sends

`taxjson crypto-sends [ACCOUNT] [--set ID=DECISION] [--unpair] [--unset ID] [--note TEXT] [--price P] [--write] [--json]`

Every crypto withdrawal or send that did **not** arrive in another of
your crypto accounts, with your decision or PENDING. A send is paired
with an arrival of the same coin on another exchange, or on the same
exchange in another account, from 10 minutes before to 3 days after the
send, losing at most 10% to the network fee; a paired send is your own
move. The decisions are `self` (your own wallet: no tax event), `gift`
(Canada only: a disposition at fair market value, ITA s.69(1)(b)) or
`payment` (a disposition at fair market value).

For each send it shows the fair value per coin and in CAD with its
source (the exchange's spot price when the row carries one, as Coinbase
does; otherwise the Yahoo daily close at the send date, times the Bank of
Canada rate) and the ready line `BUYSELL <date> <local time> <COIN> -<qty>
CAD <price> <proceeds> 0`. A Kraken network fee taken in the coin is
already booked by the parser and is not in the quantity. A matched send
that arrived short with no fee stated (a Coinbase Send carries its
network fee inside the quantity) has the shortfall booked as a sale at
fair value: a `<send id>-fee` line in `crypto_sends.tt`, in both
countries (in a Canada project a stablecoin's shortfall is US-dollar
cash, not a sale).

US-dollar stablecoins (the shipped list in `markets.toml`: USDC, USDT,
DAI, PYUSD, GUSD, RLUSD, FDUSD; a ticker.map `STABLE` line adds or
removes one) are US-dollar cash in a Canadian project's books, so a gift
or payment of one gets no sale line. The command shows the **currency
gain** instead: the value at the send-date Bank of Canada rate minus the
average CAD cost of the USD-cash / stablecoin pool rebuilt from the
ledgers, flagged when likely superficial (USD or stablecoins acquired
within 30 days and still held), with the year's total.

- `--set ID=self|gift|payment [--note TEXT] [--price P]` records a
  decision (repeatable; `--price` when the price lookup fails). A `-fee`
  id takes `ID-fee=fee --price P`. A US project refuses `gift`.
- `--unset ID` removes one (the send is undecided again).
- `--set ID=gift|payment --unpair` keeps a send the tool paired with an
  arrival unpaired (that arrival was unrelated). Without `--unpair` a
  saved gift or payment on a paired send is not booked and is warned
  about, and `run --strict` stops.
- `--write` regenerates `inputs/<acct>/crypto_sends.tt` (idempotent; it
  warns when another `.tt` already sells the same coin, date and
  quantity).

`taxjson run` asks at a terminal (self / gift / payment / skip) after
the crypto parse and refreshes the file; headless it prints one note.
While another crypto account's exports have not been parsed yet, a send
to it would look unmatched: `--set` / `--write` refuse until `taxjson
run` reads them. Ids carry exchange, local date and time, coin and
quantity, never a txid or address; refs are masked (`LG***`). A
`checklist` step.

#### taxjson find-missing-history

`taxjson find-missing-history [ACCOUNT] [--year YEAR] [--include-options] [--write-missing-history [--all-history] [--outside-year]] [--write-purchases [FILE]] [--force]`

Report positions with a missing cost basis (a truncated buy history, or
$0-basis corporate-action shares) that distort a year's gain. Pairs the
accounts' `OPENING ... cost=unknown` lines already cover are listed
apart (COVERED), not as work to do. `--include-options` checks option
positions too; `--year` scopes what counts as relevant (default: the
config year).

- `--write-missing-history` writes the candidates instead, as `OPENING
  <date> <SYMBOL> <qty> cost=unknown` lines in
  `inputs/<account>/missing_history.tt` (merged with the lines there).
  `--outside-year` adds only the positions with no row in the tax year;
  `--all-history` writes every candidate, not only those affecting the
  tax year.
- `--write-purchases [FILE]` drafts `.tt` purchase lines from the
  broker's own cost (IB's `Basis` on a closing sale, with one line per
  lot when the statement lists Closed Lots; the book value a Questrade or
  RBC transfer-in states) into `inputs/<account>/purchases_draft.tt.txt`,
  which the run does not read until you review it and rename it to
  `.tt`. An existing draft is kept unless `--force` (then as `.bak` or
  the next free `.bakN`).

Step 5 of [getting-started.md](getting-started.md#5-find-and-fill-missing-history)
walks through filling the gaps.

#### taxjson opening

`taxjson opening ACCOUNT FILE [--date D] [--dry-run] [--force]`

An **opening balance** from a broker's positions report: an Interactive
Brokers Activity Statement (its Open Positions section), an RBC Holdings
Export, or a `[[holding]]` TOML. It writes `inputs/ACCOUNT/opening_<date>.tt`,
one `OPENING <date> <symbol> <qty> <currency> <total-cost> [<lot-date>]`
line per long position with the report's **book cost** (never its market
value). A position with no cost, a short or a futures contract is listed
and skipped, as is one whose symbol, currency or lot date a `.tt` line
cannot carry; a report cell with a control character is refused. The
date is the report's own (`--date` when it has none). `--dry-run` prints
the lines instead; `--force` replaces an existing file, keeping it as
`.bak` (or the next free `.bakN`).

An OPENING line sets the position and its cost but is **not a
purchase**: no superficial-loss / wash-sale window, no "recent buy"
(CA-OPEN-01 / US-OPEN-01). The snapshot replaces the account's earlier
rows of its symbols: trades, transfers, renames and cost adjustments
dated on or before it are left out of the books with an `ATTENTION:
opening:` line (income rows stay); a left-out sale or short cover of the
tax year stops the run (CA-OPEN-03). A US project needs one line per lot
with its purchase date and a US-dollar cost (a holdings TOML with
`acquired = "YYYY-MM-DD"` per lot); Canada pools the lines (s.47) and
converts a foreign-currency cost at the snapshot day's Bank of Canada
rate (a broker's CAD book value is used as is).

### Summaries

#### taxjson amt

`taxjson amt [YEAR] [--other-income AMT] [--other-losses AMT] [--deductions AMT] [--carrying-charges AMT] [--province P] [--json]` (Canada)

The year's **minimum tax (AMT)** line by line (ITA s.127.5-127.55, form
T691): regular tax; the adjusted taxable income item by item (gains at
100%, other years' net capital losses and carrying charges at 50%,
dividends without the gross-up, deductions in full); the basic
exemption; the 20.5% rate; the credits allowed (BPA credit at 50%,
foreign tax credit in full); whether it binds; the provincial AMT; the
carryover it **creates**; the carryovers **available** by year of origin
with their 7-year limit (ITA s.120.2); what is **recovered** this year
(up to regular tax minus minimum tax, line 40427); and what carries
forward. Every figure is `taxjson estimate`'s own, with the same flags.
`YEAR` = an earlier closed year prints what its close-year lock
recorded. Refused in a US project (Form 6251 is not modelled). The
carryover rules:
[tax-rules.md](tax-rules.md#alternative-minimum-tax-and-the-minimum-tax-carryover).

#### taxjson estimate

`taxjson estimate [--other-income AMT] [--other-losses AMT] [--long-term-losses AMT] [--deductions AMT] [--carrying-charges AMT] [--province P] [--verbose] [--json]`

A marginal tax **estimate** for the year's investment income
(`--details` puts the realized-gains summary table of `taxjson sum` in
front of it), computed incrementally:
tax(other income + investment income) − tax(other income), so the
investment income is bracketed on top of what you already earn. Taxable
accounts only. With no flags it uses the `[estimate]` block of
`taxjson.toml`, which `taxjson instalments` reads too; without it, other
income is 0 (pure investment-income bracketing) and the loss carryover
is the latest close-year lock's carry-forward, else 0. `taxjson sum
--other-income ...` shows the same estimate after the FOR THE RETURN
block.
Canada projects also get an **AMT check** (post-2024 rules: gains at
100%, no DTC, 20.5% over the exemption plus the provincial piggyback),
shown binding or not, with the top-up and the 7-year carryforward when
it binds. Planning numbers, never filing numbers.

<pre>
TAX ESTIMATE — canada/ON, rates vintage 2026
ESTIMATE ONLY, not filing numbers: taxable accounts only; [ ] says how an amount was built.
  Other income                      200,000.00 <!-- pii-ok -->
  Capital gains (taxable)            15,000.00  [30,000.00 realized - 0.00 other losses, x50%] <!-- pii-ok -->
  Eligible dividends (grossed)        1,380.00  [1,000.00 x1.38] <!-- pii-ok -->
  Foreign dividends                     500.00  [FTC 75.00, from the TAX rows]
  Payments in lieu                        0.00

  Tax with investments:  72,598.76  (federal 44,729.50 + ON 27,869.26) <!-- pii-ok -->
  Tax on other income alone:  64,721.98 <!-- pii-ok -->
  => ESTIMATED TAX ON INVESTMENT INCOME: 7,876.78 CAD  (25.0% of 31,500.00) <!-- pii-ok -->
...
More: tjs estimate --details (assumptions, 3 note(s))
Gains by account and the lines for the return: tjs sum
</pre>

The AMT check and the `! ` lines (sales with no purchase, FX on foreign
cash) follow the estimate. `--details` writes each bracket in full
("[1,000.00 x1.38, Canadian issuers, trust distributions included]"), <!-- pii-ok -->
the assumptions, and note lines naming what the provincial figure
included (ON: the surtax and the Ontario Health Premium, base vs with
investments), the phased federal BPA when it applies, the provincial AMT
arithmetic when AMT binds, and, when the project year has no built-in
rate table, which year's tables ran instead (a year before the earliest
table also says the post-2024 AMT shown did not apply).

- **Canada** (`--province` or `province` under `[settings]`; ON / BC /
  AB): 50% inclusion, eligible gross-up and DTC, a foreign tax credit
  from the books' actual TAX rows. `--other-losses` are prior-year
  capital losses in **full dollars**, netted against gains before the
  50% inclusion (deducted below net income, so they do not restore a
  phased BPA). `--deductions` (lines 20700-23500: RRSP, FHSA, RPP ...)
  and `--carrying-charges` (line 22100) lower net and taxable income:
  other income first, then the investment income; the AMT base takes the
  deductions in full and the carrying charges at 50%. Deductions not
  entered are not modelled, so an RRSP year left at 0 overstates the tax.
  Canadian issuers' dividends are treated as eligible (38% gross-up +
  DTC); non-eligible dividends are not modelled, and a Canadian trust's
  distribution (ETF, REIT or fund units) is grossed up as an eligible
  dividend too (the printed assumptions and the row say so), because the
  export does not carry its T3 split (box 49 eligible dividends, 26 other
  income, 21 capital gains, 42 return of capital): take the real split
  from the T3. Foreign dividends are ordinary income, credited (FTC) with
  the foreign tax the books actually withheld (TAX rows, capped at 15% of
  the dividends; the treaty 15% is assumed for an account whose books
  carry no TAX rows). A crypto account's dividends are staking rewards:
  ordinary income, no withholding, no foreign tax credit. Modelled: the
  federal enhanced BPA phase-down (full amount up to the 29% bracket, the
  minimum from the 33% bracket, linear between, on net income), Ontario's
  surtax and Health Premium (up to $900), and the provincial AMT: ON
  24.63% of the federal excess plus ON surtax on it (2024+; 2026 assumed
  until the form is out), BC 33.7% / 34.9% / 40.0% for 2024 / 2025 /
  2026, AB 35%. Foreign tax the federal tax cannot absorb goes to the
  provincial foreign tax credit (form T2036, limited to provincial tax x
  foreign income / net income). The estimate is signed: eligible
  dividends at a low bracket can show a negative figure, a saving on the
  tax of the other income. A prior-year minimum tax carryover (ITA
  s.120.2, line 40427) is applied when it is entered or last year's
  close-year lock carries it; with none, a NOTE names the headroom one
  could use. Not modelled: QC, low-income reductions, non-eligible
  dividends, non-refundable credits other than the basic personal amount
  (CPP/EI, Canada employment, age, pension, donations ...), the OAS
  recovery tax (s.180.2) and AMT adjustments outside the books (the
  s.110(1)(d) stock-option deduction, donated securities), the FX result
  on foreign cash (line 15300, `taxjson fx-cash`), T3/T5 slip capital
  gains, and, for `taxjson instalments`, CPP/EI payable on
  self-employment earnings (lines 42100/42120, which CRA adds to the
  instalments due); the output says so. See
  [KNOWN_ISSUES.md](../KNOWN_ISSUES.md).
- **USA**: single filer, standard deduction. Short-term gains are
  ordinary; long-term gains and (assumed-qualified) dividends stack on
  top at the 0/15/20% brackets. A capital loss carryover keeps its term:
  `--other-losses` is the short-term carryover (Schedule D line 6) and
  `--long-term-losses` (or `[estimate] long_term_losses`) the long-term
  one (line 14). Each nets against its own term's gains first, then the
  other term's, then up to $3,000 of ordinary income (a net-loss year
  shows a negative estimate, a saving), and that deduction also reduces
  net investment income; the carryforward shown counts as used only what
  taxable income absorbs (Capital Loss Carryover Worksheet line 4). NIIT
  3.8% above $200k MAGI; no foreign tax credit (the withholding in the
  books is not credited) and no state tax.

Add `--verbose` (`-v`) for the **CALCULATION TRACE**: every bracket
slice, credit and surtax tier, side by side for the base and
with-investments runs, ending in the subtraction that produces the
estimate. It shows exactly where each dollar of investment income landed
in the brackets and what the gross-up and DTC did. `--json` prints the
whole document (accounts, totals, estimate, and instalments when
configured).

Rate tables live in `src/taxjson/lib/tax_estimate.py` with a printed
vintage: they need an annual refresh, and the output says so.

Where the carried-in amounts come from when you give no flag (the latest
close-year lock), and what `close-year` and `handoff` do with them: see
[tax-rules.md](tax-rules.md#net-capital-losses-carryforward-and-carry-back)
and
[tax-rules.md](tax-rules.md#alternative-minimum-tax-and-the-minimum-tax-carryover).

#### taxjson fx-cash

`taxjson fx-cash [--ledger v1|v2] [--events] [--cash-events] [--json]`

FX capital gains on foreign-currency **cash**: foreign cash is property,
so spending USD realizes the rate move since it was acquired. Canada:
ITA s.39(1.1) with the $200/year de minimis; US: the §988
ordinary-income figure. The default ledger (v1) reads only the trades
and income of the taxable books (no conversion, deposit, withdrawal or
margin balance), so it says **NOT RELIABLE — do not file this figure**
first and shows no reportable figure. `--ledger v2` (or
`fx_cash_ledger = "v2"`; opt-in, under audit) also reads the conversions,
deposits / withdrawals and statement balances the exports carry and the
`.tt` lines `FXCONV` / `CASHMOVE` / `CASHOPEN` / `CASHBAL`, models
foreign-currency margin debt, reconciles each account to its statement,
and says NOT COMPUTED with what to add rather than guess (`--cash-events`
lists what it read). `--events` gives the per-disposal detail, `--json`
the machine form. It changes NO other number. Set `fx_cash_gains = true`
under `[settings]` to also print it (and write `reports/fx_cash.rpt`) at
the end of every run; off by default.

#### taxjson instalments

`taxjson instalments [--json]` (Canada)

Canadian tax instalments: what each of the four dates (Mar / Jun / Sep /
Dec 15) calls for under your chosen basis, what you have paid, and the
**offset interest** plus **s.163.1 penalty** that follow from any gap.
The current-year basis is driven by `taxjson estimate` itself (AMT
included). Interest uses CRA's published quarterly rates (built in;
`prescribed_rate(s)` overrides), credit interest runs from the later of
the payment date and January 1, and net interest of $25 or less is not
charged. CRA charges instalment interest only if it sent you a reminder
for the year, which the report says. Configure `[instalments]` in
`taxjson.toml` ([settings.md](settings.md#instalments)); `--json` for
machines.

#### taxjson stats

`taxjson stats [YEAR] [ACCOUNT] [--all-history] [--json]`

Win/lose statistics on closed trades, one row per asset class (long
shares and ETFs, short shares, long options, written options, futures,
crypto) plus a total: trades, wins, losses, win rate, net P/L, average
and largest win and loss, profit factor (gross wins / gross losses).
Economic P/L in the base currency **before** any superficial-loss /
wash-sale denial (the denied total is its own line); taxable accounts
unless a sheltered one is named. `YEAR` defaults to the project's year
and takes any window token too (`30d`, `ytd`, `all` ...);
`--all-history` covers every closed trade the books hold. One trade per
closing disposition; a written option is one trade from write to close
whatever `option_premium_timing` says (premium minus the buy-back, or
the premium kept at expiry or assignment; an assigned option's premium
is taken back out of the shares the tax rules fold it into). A view,
never a filing number.

#### taxjson sum

`taxjson sum [ACCOUNT] [--other-income AMT] [--other-losses AMT] [--long-term-losses AMT] [--deductions AMT] [--carrying-charges AMT] [--province P] [--verbose] [--json]`

Cross-account realized-gains tables for the tax year in the base
currency, one row per account (NON-OPT / OPTION / REALIZED / DIVIDEND /
PIL / FEES / TOTAL). It takes no period by design: it reports the tax
year's filing basis from the built books (the windowed views are
`winners`, `ccd-sum` and `leaps-sum`). When `taxjson.toml` declares both
account types, the summary prints a **TAXABLE ACCOUNTS** table and a
**SHELTERED ACCOUNTS** table (each with its own SUBTOTAL) followed by the
**ALL ACCOUNTS** grand total. Every account must declare its `type`:
`taxjson run` refuses a config with an untyped account (it would
otherwise drop out of the return); gains files whose account is no
longer in `taxjson.toml` are shown in their own UNTYPED table.
Single-type projects and `taxjson sum <account>` keep the one-table
layout.

Every table's total row is the exact sum of the rows above it, and every
row foots as printed: REALIZED = NON-OPT + OPTION (REALIZED is the gain
rounded once, OPTION takes the cent difference) and TOTAL = REALIZED +
DIVIDEND + PIL, the `reports/<account>.sum` GRAND TOTAL (each figure
matches the `.sum` to the cent; a grand total summed from cent-rounded
rows can differ from one rounded once by a cent or two). FEES is shown
for reference and is not subtracted: commissions are already in the
proceeds and the cost. NON-OPT is every non-option disposition (shares,
units, futures and crypto); the Schedule 3 / Form 8949 line split is the
FOR THE RETURN block and `taxjson form-export`. A crypto account's
DIVIDEND column is staking rewards. `--json` carries a per-account
`type` and a `subtotals` object alongside `totals`.

```
$ taxjson sum
REALIZED-GAINS SUMMARY — CAD, tax year 2025, basis: wash-adjusted
REALIZED = NON-OPT (shares, units, futures, crypto) + OPTION; TOTAL = REALIZED + DIVIDEND + PIL.

TAXABLE ACCOUNTS
ACCOUNT  NON-OPT   OPTION  REALIZED  DIVIDEND   PIL   FEES   TOTAL
------------------------------------------------------------------
margin    900.00  -100.00    800.00     80.00  0.00  15.00  880.00
...
SUBTOTAL  ...

SHELTERED ACCOUNTS — not taxable
...

ALL ACCOUNTS — sheltered included
...
TOTAL     950.00  -200.00    750.00    120.00  0.00  25.00  870.00
```

The summary ends with a **FOR THE RETURN** block over the taxable
accounts:

- **Canada**: one row per Schedule 3 line (line 4 shares and fund units
  13199/13200; line 6 options, futures and other properties 15199/15300;
  line 7 crypto-assets 15200/15301, which were 15199/15300 before 2025;
  for 2024, January 1 – June 24 on the Period 1 codes 10689/10690 and
  10693/10694) with PROCEEDS, COST(ACB), OUTLAYS, GAIN and the
  superficial losses DENIED, on the Schedule 3 convention: a short sale's
  proceeds as PROCEEDS and its cover as ACB, sell commissions as outlays;
  a denied loss REDUCES the ACB shown so proceeds − ACB − outlays is the
  allowed gain, the denial going onto the replacement's ACB. Plus the
  FX-on-cash line for line 15300 (NOT RELIABLE from the default ledger;
  the opt-in ledger v2's figure, or why it did not compute).
- **USA**: Form 8949's own Part I / II (d) proceeds, (e) cost, (g)
  adjustment, (h) gain (from 2025 the crypto accounts' digital-asset
  boxes G/H/I and J/K/L on rows of their own).

The rows equal `form-export`'s line totals, each row rounded to the
cent, as filed. When that differs from the gains files' unrounded total
gain or denied amount (US: the (g) adjustment) by a cent or more,
`taxjson sum --details` says so under the block, and `--json` carries `engine_gain_unrounded` and
`engine_denied_unrounded`; `--json` adds the per-account split. Before
the tax year has ended the figures are year-to-date, and the block says
so.

`sum` warns, one `! ` line each after the block (each warning in full
with `--details`), about what its totals leave out: dispositions with an
unknown cost that missing history (`OPENING ... cost=unknown` lines)
routed to manual reporting, and, whatever the broker, the tax year's
sales with no purchase in your files that no `OPENING ... cost=unknown`
line covers (the books hold them as an open short, so their gain is in
no total; `taxjson find-missing-history` lists them). `--json` carries
the counts as `unknown_cost_routed` / `unknown_cost_included` (the older
names `tainted_routed` / `tainted_included` are kept, same values:
"tainted" is the engine's word for an unknown cost) and the uncovered
sales as `no_purchase_uncovered`.

The estimate flags (`--other-income`, `--other-losses` ...) add the tax
estimate block of [`taxjson estimate`](#taxjson-estimate) under the
tables; `--verbose` adds its calculation trace.

### Positions

#### taxjson list

`taxjson list [ACCOUNT] [YYYY-MM-DD] [--date YYYY-MM-DD] [--negative] [--json]`

Open positions per account, taken from the gains files' `inventory` (the
wash-adjusted `<account>_gains_wash.json` when the pipeline built it,
else `<account>_gains.json`): that is, **after** `ticker.map`
consolidation (cross-listings like `SAMPLM.US` / `SAMPLM.TO` merged) and
base-currency conversion, so quantity and cost basis match the pipeline.
One row per (account, symbol) with quantity, base-currency book cost,
cost per share (per SHARE for an equity option, 100 a contract, as
`harvest` and the holdings report show it) and the position's start
date; fully closed positions are omitted. Pass an account to scope to
one. Plain `list` shows the positions at the end of the books (the
header names the date), not the tax year's Dec 31.

`reports/<account>_holdings.toml` differs on purpose: it keeps listings
separate and native for live-pricing tools, and its `base_total_cost` is
per account and before the run's cross-account and loss-deferral
adjustments and any `[[distributions]]` adjustment (Canada:
superficial-loss adjustments and the s.47 blend; USA: wash-sale basis
adjustments on per-account FIFO), as its `meta.base_cost_basis` says in
the project's own terms; its native `total_cost` leaves the map
adjustments out too.

`list --date YYYY-MM-DD` (or the date as a word: `list margin
2026-04-28`, `list 2026-04-28`) shows positions AS OF that date. Each
account's books are recomputed alone with the engine's `--as-of` cutoff,
on the project's date basis: the settlement date unless `tax_date =
"trade"`, so a sale traded Dec 31 that settles in January is still held
at Dec 31, as in the gains year and `t1135`. In Canada that is the
per-account ACB, not the s.47 blend across taxable accounts that plain
`list` and the return use; in the US it is the per-account FIFO basis
the return uses. The in-account deferred wash and the missing-history
lines are applied; the books are already ticker.map-consolidated, and
the cross-account wash pass is not in it.

A short that is a purchase missing from your files (a sale with nothing
to close: no broker short-sale marker, or a sale the broker coded
closing, IB code `C`, or any short in a registered account) is marked
`missing history?` in a NOTE column (`"missing_history_suspect": true`
in `--json`): the same pairs `find-missing-history` reports and `taxjson
run` warns about. `list --negative` shows only negative-quantity
positions, in two sections, **Short positions** (a short the broker
marks, an option or a future sold to open) and **Missing history**, and
ends with the command that records the second kind as openings
(`taxjson find-missing-history --write-missing-history --all-history`;
`--outside-year` for only those that do not touch the tax year).

```
$ taxjson list
OPEN POSITIONS — CAD, as of the latest data in the books (2026-09-21)
COST: book cost in CAD after ticker.map, basis: wash-adjusted; DEFERRED: denied losses in it.
ACCOUNT  SYMBOL     QTY    COST  COST/SH  DEFERRED  SINCE
------------------------------------------------------------
lira     XEQT.TO     10  420.00    42.00         -  2024-11-03
margin   SAMPLG.US   30  300.00    10.00    150.00  2025-01-15
margin   SAMPNG.TO    4  240.00    60.00         -  2025-02-01

3 position(s), total book cost 960.00 CAD
DEFERRED: 150.00 CAD of the book cost is denied superficial losses (s.54).
More: tjs list --details (notes)
```

#### taxjson shares

`taxjson shares [--options] [--taxable | --sheltered] [--sort symbol|qty] [--json]`

The combined quantity held of each symbol across all accounts (after
ticker.map, wash-adjusted where built) with a per-account breakdown and
the combined book cost; shorts net against longs. Option contracts only
with `--options`; futures contracts are left out. Like `list`, it is the
end of the books (the header says the date), not the tax year's Dec 31.

### Row listings

These views take an optional `PERIOD` and an optional `ACCOUNT`. Omit
the account for all accounts merged chronologically, each line prefixed
with the account; name one for pure round-trippable `.tt` text. `PERIOD`
is a look-back window (`30d` / `6w` / `3m` / `1y`), `mtd` / `ytd`
(calendar month / year to date), `all`, a literal year (`2024`), or
**`tax_year`** (the config tax year; also `ty`). Omitted, it defaults to
the config tax year. Every view takes `--json`.

Money is shown to 2 decimals; quantities and per-share prices keep full
precision. Rows with a missing or unparseable date are excluded with a
warning.

#### taxjson dil

`taxjson dil [PERIOD] [ACCOUNT]`

Like `events`, but only payments in lieu (DIVIDEND_IN_LIEU rows): what
you received while your shares were lent out or short over the ex-date,
in their own currency.

#### taxjson divs

`taxjson divs [PERIOD] [ACCOUNT]`

`events` filtered to `DIVIDEND` + `DIVIDEND_IN_LIEU`; the footer shows
the dividend total per currency.

#### taxjson events

`taxjson events [PERIOD] [ACCOUNT]`

All transactions over the window, in native (pre-base-conversion)
currency, `.tt` text format, oldest to latest. Prints per-currency
`TOTAL BUY / SELL / DIVIDEND` footers.

```
$ taxjson events 5d margin
BUYSELL  2026-06-26  14:23:05  SAMPLE.US    10  USD  50.00   501.00  1.00
DIVIDEND 2026-06-30  09:30:00  ABC.US   100  USD  0.25    25.00

TOTAL BUY:      501.00 USD
TOTAL DIVIDEND: 25.00 USD
```

#### taxjson fees

`taxjson fees [PERIOD] [ACCOUNT]`

One row per fee-bearing trade in the window (account, date, symbol,
action, fee) plus a per-currency `TOTAL FEES`. This view counts
standalone `FEE` rows (monthly or market-data charges) that `fees-sum`,
a per-TRADE cost report, deliberately excludes: the two totals differ by
those. It is not the line 22100 figure: trade commissions are already in
the ACB and proceeds, and margin interest (INTEREST rows) is not listed
here.

#### taxjson gains

`taxjson gains [PERIOD] [ACCOUNT] [--options] [--equities] [--futures] [--puts] [--calls]`

Realized gains in **native** terms (before TOBASE consolidation and
before conversion to the base currency), one row per disposition (date,
symbol, qty, currency, proceeds, cost, gain, days), with a per-currency
`TOTAL GAIN`. Crypto has no native gains file and is skipped.

`gains` and `trades` take **instrument-class filters**: `--options`,
`--equities`, `--futures`, `--puts`, `--calls`. They combine with OR
(`--equities --calls` = equities *and* calls), and no flag means all
instruments. A futures option (e.g. `F:SAMPLX251220P00053000.US`) is
both a future and an option, so it matches `--futures` **and**
`--options` / `--puts`.

#### taxjson leaps

`taxjson leaps [PERIOD] [ACCOUNT]`

Closed LEAPS positions: one row per disposition (date, contract, qty,
proceeds, cost, gain, days held) plus the total realized gain. Partial
closes appear as realized; still-open contracts are absent (no
mark-to-market).

A **LEAPS** position here is a **long option buy placed more than
`[settings] leaps_months` calendar months before expiry** (default 9,
the market convention; calls and puts alike). The setting changes only
these views, never a tax figure. Contracts qualify over your FULL
history, so an exit inside the viewing window shows up even when the
qualifying buy predates it; short premium and near-dated buys never
qualify. `leaps` and `leaps-sum` report the ENGINE's numbers
(lot-matched, superficial-loss / wash-adjusted, base currency), read
from the wash-adjusted gains files.

#### taxjson roc

`taxjson roc [PERIOD] [ACCOUNT]`

Like `events`, but only ADJUST rows: return-of-capital ACB / basis
reductions as the broker classified them, manual `.tt` adjustments, and
the `[[distributions]]` adjustments `run` books.

#### taxjson trades

`taxjson trades [PERIOD] [ACCOUNT] [--options] [--equities] [--futures] [--puts] [--calls]`

`events` filtered to `BUYSELL` + `ASSIGN`; the footer shows buy / sell
totals per currency. The instrument-class filters are described under
[`taxjson gains`](#taxjson-gains).

#### taxjson transfers

`taxjson transfers [ACCOUNT] [--json]`

The custody-transfer **evidence** view: depot flips, listing journals,
broker migrations, and crypto withdrawals and sends (a send that arrived
in another of your crypto accounts is a self-custody move; the rest are
gift or payment candidates, see `taxjson crypto-sends`; in a US project
only a payment is a sale). These are the TRANSFER rows the books
deliberately exclude (cost comes from the buy and sell history). It
reads the parse-stage sidecars (`work/<acct>_<broker>_transfers.json`)
plus the in-book TRANSFERs of `transfers = true` accounts, with the
broker's transfer type (InterDepot / Internal / ATON). A SYMBOL CODES
section lists the Questrade internal codes the run booked under an
inferred ticker, with the evidence, and those it could not identify.
`--json` for machines (`symbol_codes`).

### Totals by type

The roll-up summaries take an **optional** `PERIOD` (a window, `mtd` /
`ytd`, `all`, a year, or `tax_year`; default: the config tax year) and an
optional `ACCOUNT`; a lone non-period argument is read as the account
(`taxjson divs-sum margin`). Every summary TOTAL is the sum of its
printed (cent-rounded) rows. A tax-year window (the default, `tax_year`,
`2025`) follows the project's `tax_date` in `winners`, `gains`,
`ccd-sum`, `leaps` and `leaps-sum`: on the settle basis a Dec-31 trade
that settles in January belongs to the next year, as in `sum`. These
views refuse when `work/` was built for another year than `[settings]
year`.

#### taxjson ccd-sum

`taxjson ccd-sum [PERIOD] [ACCOUNT]`

Covered-call (short call) realized-gain summary per underlying over a
window (default: the tax year): the windowed twin of
`reports/ccd.rpt`. Covers every account; the total is split into TAXABLE
and SHELTERED parts when registered accounts contribute.

#### taxjson dil-sum

`taxjson dil-sum [PERIOD] [ACCOUNT]`

Payment-in-lieu total per symbol (DIVIDEND_IN_LIEU rows only), with each
row's treatment: ordinary income (no dividend gross-up or credit, no
qualified rate), except, in a Canada project, a Canadian dealer's
payment on a Canadian issuer's share, which ITA s.260 deems a taxable
dividend (on the dealer's T5 box 24; counted in `divs-sum` and the
estimate's eligible dividends). Registered accounts are split out as in
`divs-sum`.

#### taxjson divs-sum

`taxjson divs-sum [PERIOD] [ACCOUNT]`

Dividends received per ticker over the window (DIVIDEND rows, plus in a
Canada project the payments in lieu ITA s.260 deems dividends; the
others are in `dil-sum`), with per-currency totals split TAXABLE /
SHELTERED when a registered account contributes. The TAXABLE line is the
figure to compare with the T5 / T3 slips. Each row counts in its tax
year, so a Canadian ETF's December-record distribution paid in January
is in the December year, as on the T3; the dating rules are in
[tax-rules.md](tax-rules.md#income-dating-dividends-trust-distributions-and-roc-record-dates).
A crypto account's DIVIDEND rows are staking rewards (ordinary income):
`divs-sum` names them on an "of which crypto staking" line, and `sum`,
the views and the crypto `.sum` label them as staking. The T5 box 18
capital-gains dividends listed in `[[capital_gains_dividends]]` are
shown apart, under CAPITAL-GAINS DIVIDENDS (see
[Capital-gains dividends](#capital-gains-dividends) below).

`winners` prints the same taxable / sheltered split under its ranking,
and `dil-sum` / `roc-sum` / `trades-sum` separate registered accounts
the same way (the T3 box 42 figure is `roc-sum`'s TAXABLE line; T5008
proceeds are `trades-sum`'s taxable "sold" figure).

#### taxjson fees-sum

`taxjson fees-sum [PERIOD] [ACCOUNT] [--by-account | --no-by-account] [--json]`

Trading-fee report by **brokerage** (commission and fee totals with
per-trade averages, $/share, % of notional), converted to the base
currency: the same totals `taxjson run` writes to `reports/fees.rpt`
(which is grouped by brokerage only). Broken down **by account** by
default (`--no-by-account` for brokerage totals only). A `PERIOD` window
scopes it (e.g. `taxjson fees-sum 2025`). Its year is each fee's
**trade** date, converted at that date's rate (as in `taxjson fees`), so
it differs from `taxjson sum`'s FEES column, which follows the project's
`tax_date` (settle in Canada), by the fees of trades that straddle Dec 31
and by cents of FX. Plain futures fees are their own bucket (not stock
fees, not counted in $/share).

#### taxjson leaps-sum

`taxjson leaps-sum [PERIOD] [ACCOUNT]`

The LEAPS gain summary (see [`taxjson leaps`](#taxjson-leaps) for what
counts as LEAPS): per contract, quantity closed, proceeds, cost, gain
and expiry, plus the total (period defaults to the tax year). Only the
long position's dispositions count; a later write and buy-back of the
same contract is covered-call P&L, in `ccd-sum`. The total is split into
TAXABLE and SHELTERED parts when registered accounts contribute.

#### taxjson roc-sum

`taxjson roc-sum [PERIOD] [ACCOUNT]`

Return-of-capital total per ticker (default: the tax year): the ACB
(Canada, T3 box 42) or basis (USA, Form 1099-DIV box 3) adjustments,
split into broker-classified, manual and `[[distributions]]` rows. It
warns when a symbol has a book ADJUST and a `[[distributions]]` entry on
the same date (the same ROC entered twice). Use `taxjson roc <period>`
for every ADJUST row as `.tt` text.

Most fund and REIT return of capital is in no broker export: enter it
from the T3 (box 42) as a `.tt` ADJUST line with a negative amount, or as
a `[[distributions]]` entry, never both:

```
ADJUST 2025-12-31 12:00:00 SAMPLE.TO CAD -120.00   # T3 box 42 ROC
```

How each kind of return of capital is booked, when it lowers the ACB,
and what happens when the ACB goes below zero:
[tax-rules.md](tax-rules.md#return-of-capital). See also
[Non-cash distributions](#non-cash-distributions) below.

#### taxjson trades-sum

`taxjson trades-sum [PERIOD] [ACCOUNT]`

Per ticker: buy and sell counts, value bought and sold, and fees, with
per-currency totals (all accounts; when a registered account
contributes, the taxable accounts' "sold" figure is printed under them).

#### taxjson winners

`taxjson winners [PERIOD] [ACCOUNT] [--top N] [--json]`

Per-ticker realized gains RANKED: the biggest winners and losers over a
window (default: the tax year), with options grouped under their
underlying. `--top N` shows the top and bottom N (default 10; `--json`
carries all). The taxable / sheltered split is printed under the
ranking.

### Before you trade

#### taxjson wash-radar

`taxjson wash-radar [ACCOUNT] [--date YYYY-MM-DD] [--verbose] [--all] [--json]`

The superficial-loss / wash-sale radar per taxable account, recomputed
**live as of today** (or `--date`), so cooling-down windows reflect the
current date rather than the last `taxjson run`. It is forward-looking:
what you can or cannot sell or buy now; for the wash sales that already
happened, use [`taxjson wash-sales`](#taxjson-wash-sales). It defaults
to every taxable account (sheltered accounts are never targets); pass an
account to scope to one. The combined `sheltered_base.json` is included
automatically for cross-account detection when present. `--verbose` for
more detail; `--all` to also list CLEAR (no-risk) positions; `--json`
for the structured document with absolute `clears_at` dates. The same
reports are written to `reports/wash_radar_<account>.rpt` during
`taxjson run`.

The default view is one STATUS table, one row per position (the most
urgent status first), then one `! ` line per status that asks something
of you and the scope in a few words:

```
WASH RADAR — superficial losses (s.54, settlement dates), as of 2026-10-10
STATUS: what a loss sale or a buy does today; CLEARS: the day its window closes.
STATUS  TICKER                 TAXABLE  SHELTERED  CLEARS
---------------------------------------------------------
RISK    QZQ.US                     100         50  -
CLEAR   SAMPA.TO                   100          0  -

! RISK: QZQ.US: no sheltered buy (DRIP) 30 days after a loss sale — tjs wash-radar --details
More: tjs wash-radar --details; not checked: affiliated persons' buys (ITA s.251.1)
```

`--details` groups the positions by advisory in a fixed order, each
section with its definition and per-row advisories: VIOLATION, BLOCKED,
LOCKED, EXITABLE (a loss is OK only with a FULL exit), CAUTION (the
sheltered leg exited, so a loss sale of any size stands unless re-bought
within 30 days), COOLING, RISK, CLEAR.

A trade is counted from its **trade date** (a sale made today settles
tomorrow but is already in the books), while the ±30-day windows run on
settlement dates, as in the engine. Whether a sale was a loss comes from
the engine's own gains files (the s.47 pool blended across taxable
accounts, denied losses added to cost, option cost folded in on
exercise); sales outside the project's tax year fall back to the radar's
own per-account pool. The project's missing-history openings
(`OPENING ... cost=unknown`) are applied as in the gains pass.

Whether a loss is superficial follows the engine's per-holder rule: your
taxable accounts (one pool) and each registered account on its own back
a denial only with units they **bought inside the ±30-day window and
still hold**. Shares a registered account held before the window never
do, and in Canada a new short sale or written option is not an
acquisition. In Canada each sale is judged on its own, as in the engine
(CA-SL-08, CRA's formula: the least of units sold, units acquired in the
window and units held at day 30): a rebuy still held backs two losses,
or an old loss and a sale today, alike; only the fills of one sale (the
same day, one account, no buy between them) share it. Quantities on
either side of a split are compared in today's units, and a long call
counts at its declared contract size (100 for a standard equity option).
A warrant, a call on an adjusted series (SAMPLE1) or a futures option on
the loss's contract is a **note to check by hand**, as the engines flag
it (CA-SL-14/15, US-WASH-14/15), never a VIOLATION.

A VIOLATION names who must sell what to rescue the loss, by the last
trade date on the listing's own calendar (a TSX USD unit trades on TSX
days); once that date has passed it says the loss is denied, with no
sell-by date. A LOCKED row states how many of your taxable shares' loss
a sale today would lose. A rebuy denies the loss only on as many shares
as it buys (BLOCKED and `buy-check` print the amount per unit). A crypto
or futures VIOLATION's last day is the settle bound itself or the last
trading day before it (both settle on their trade date). A short
position is worded as one (cover, re-short). A `.tt` line dated after
today in a settle-date project was traded on the last trading day that
settles by it, and is in the books from then. The radar's own pool (used
outside the gains files' year) books a Canadian trust's return of
capital on its record date and floors the ACB at nil (s.40(3)), as the
engine does. `buy-check`, `sell-check`, `watch` and `harvest`'s ADVISORY
column read the same radar.

Every verdict covers **this project's accounts only** and says so: a
purchase by your spouse or common-law partner or a corporation you
control (Canada: affiliated persons, s.251.1; US: IRS Pub. 550) also
denies a loss (tax-logic CA-PLAN-04 / US-PLAN-04). How to bring an
affiliated person's trades into the books, and what the denial then
does: [tax-rules.md](tax-rules.md#registered-and-affiliated-holders-permanent-denial).
When a call option counts as replacement property for a share loss:
[tax-rules.md](tax-rules.md#superficial-loss-s54).

In a **US project** the radar applies §1091, not s.54 (tax-logic
US-PLAN-01): windows run on **trade** dates, and each recent loss's
verdict is the **US engine's own**, run on the same books as of the
date: purchases in every account, IRAs included, and no still-held test
(§1091's re-short rule applies, and an IRA purchase in the window locks
the loss even after the IRA sold). Each replacement share is matched
once (a US IRA purchase the engine already matched is not "at risk"
twice). A washed loss is listed as **WASHED** (the disallowed amount is
in the replacement's basis, or lost for good through an IRA purchase);
there is no VIOLATION and no "rescue" advice, because no later sale
undoes a wash sale, and `sell-check` never answers ACTION. A long call
bought in the window is a note, not a denial (US-PLAN-02).

#### taxjson buy-check

`taxjson buy-check SYMBOL ... [--json]`

Is buying this ticker today safe? **UNSAFE** when a loss was sold within
the past 30 days (the rebuy cancels it, permanently if bought in a
sheltered account), with the safe-from date when one can be determined
(violations defer to `wash-radar` rather than print a date that would
invite an early rebuy); **SAFE\*** when buying merely extends an open
wash window. Root-matched (`buy-check SAMPLU` covers `SAMPLU.US` and
cross-listings, folding in `ticker.map` pairs). `--json` for machines;
exit 1 when any symbol is unsafe.

#### taxjson sell-check

`taxjson sell-check SYMBOL ... [--json]`

Is selling this ticker **at a loss** today safe? **UNSAFE** when a
registered account's recent buy it still holds would deny the loss on
the whole position (LOCKED), or an open violation is backed by a
registered account's in-window buy; **PARTIAL** when only some units are
at risk (the line says how many; the rest of the loss stands);
**ACTION** when a violation can be rescued by selling the taxable
replacement before the deadline (Canada only: a US wash sale cannot be
rescued, and a WASHED row is SAFE\* with the reason); **SAFE\*** /
**SAFE** with the applicable caveats (the window caveat in full with
`--details`). Whether it *is* a loss at today's
price is `harvest`'s job. `--json` for machines; exit 1 on UNSAFE or
PARTIAL.

#### taxjson harvest

`taxjson harvest [SYMBOL ...] [--crypto] [--options] [--no-ibkr] [--ibkr-port PORT] [--verbose] [--json]`

"If I sold this today, would it be a loss, and may I claim it?" One
table over every taxable account's OPEN positions: base-currency book
cost (from the wash-adjusted books, so deferred superficial / wash
losses are already folded into the basis), current price (IBKR →
yfinance → price cache, with the source labelled), unrealized gain or
loss, and, on every LOSS row, the wash radar's advisory with a view-time
countdown to the clear date. Losses come first. US projects get an
`LT_IN` column: days until the position is long-term.

```bash
taxjson harvest                 # all open positions, harvestable losses first
taxjson harvest AAA.TO BBB.US   # just these symbols
taxjson harvest --json          # machine-readable
```

```
HARVEST — unrealized open positions, CAD, basis: wash-adjusted
Losses first. PRICE: ^ IBKR, + yfinance, * cache. ADVISORY: the wash radar's.
ACCOUNT  SYMBOL  TX_QTY  SH_QTY      PRICE  UNREALIZED     PCT  VERDICT
                                       CAD         CAD
-----------------------------------------------------------------------
margin   AAA.TO     100      25   10.8500+     -155.00  -12.5%  LOSS
margin   BBB.US       5       -  130.4240^      106.62   19.5%  GAIN
-----------------------------------------------------------------------
TOTAL    -            -       -          -      -48.38   -2.7%  -

ACCOUNT  SYMBOL  ADVISORY                        TX_ADD             SH_ADD
------------------------------------------------------------------------------------
margin   AAA.TO  LOCKED(clears:2026-08-09,+25d)  2026-07-08(-7d)    2026-07-02(-13d)
margin   BBB.US  -                               2026-03-02(-135d)  -

ACCOUNT  SYMBOL   COST/SH       EXIT@
                      CAD      native
-------------------------------------
margin   AAA.TO   12.4000  12.6480CAD
margin   BBB.US  109.1000           -

HARVESTABLE LOSSES (CAD, cumulative): now 0.00 | <=7d 0.00 | <=14d 0.00 | <=30d 155.00

More: tjs harvest --details; not checked: affiliated persons' buys (ITA s.251.1)
```

A table wider than the output width (the terminal's, or 120 columns off
a terminal; [output-style.md](output-style.md)) is split, as above, into
tables that fit, each led by ACCOUNT and SYMBOL: the position and its
verdict, the radar's advisory with the last buys, the cost with the
break-even price. With `TAXJSON_WIDTH=0` it is one table with every
column.

Everything is base currency: `PRICE` is the native quote already
FX-converted, with its source marked (`^` IBKR live, `+` yfinance, `*`
price cache). The `HARVESTABLE LOSSES` line schedules the paper losses
by when the radar says they become claimable: `now` (no lock), then
cumulatively within 7 / 14 / 30 days from the clear dates. These are
estimates at today's prices, and any new buy on either side pushes a
clear date out. The TOTAL row's `PCT` is the total unrealized over the
gross cost of the listed positions (a short's credited proceeds do not
net against long cost). An input harvest cannot read stops it (exit 2).

- `RISK` losses count as claimable **now** (the superficial-loss rule
  needs an acquisition inside the ±30-day window, not mere sheltered
  ownership) but carry a forward caveat: an affiliated buy (a DRIP is the
  classic) within 30 days *after* the sale denies the loss permanently,
  so pause sheltered adds first.
- A `LOCKED` loss counts as claimable now except for the units a
  registered account bought in the window and still holds
  (`LOCKED(at-risk:4/100sh,…)`).
- A `VIOLATION` whose rescue deadline has passed
  (`VIOLATION(deadline-passed:…)`) is not claimable now: a sale today
  waits until the registered account's last in-window buy ages out (31
  days), or has no clear date without the sheltered books.
- When the radar reports are older than the books (after `run
  --account`), harvest runs the radar live.

`EXIT@` is the **native-currency price at which a full exit today books
no base-currency loss**: the position's base book cost converted back at
today's FX rate, plus a 2% buffer for fees, slippage and FX drift
between the quoted rate and the actual fill or settlement conversion.
Shown on long LOSS rows only: sell above it and the sale books no loss
even after the buffer.

`TX_QTY` is the taxable accounts' shares; `SH_QTY` is the total held
across sheltered accounts (RRSP / TFSA / IRA / …). `TX_ADD` / `SH_ADD`
name the last ACQUISITION on each side as `DATE(-Nd)` (signed days:
negative = past, positive = future; ADVISORY clear dates count up). Both
sides extend the wash window, which is why the radar's clear date can be
later than the sheltered trades alone imply: a **taxable** rebuy within
30 days of a loss sale defers the loss into the new shares' basis and
restarts the clock (`TX_ADD` makes that visible), while a **sheltered**
add within 30 days either side makes the loss **permanently denied**
(CRA s.40(2)(g) via the affiliated trust; IRS Rev. Rul. 2008-5 for IRAs).
Both use the engine's `last_acq_date` (any add, even to an old position:
a DRIP buy last week counts). Gains files written before the field
existed show `-` with a warning, never an approximation, which could
only overstate the age and green-light a denied loss; run `taxjson run`
again to refresh. The command passes every sheltered account's gains
file automatically; standalone callers of `taxjson-harvest` use
`--sheltered work/rrsp_gains.json` (repeatable).

Crypto accounts (`crypto = true`) are **excluded by default** (the price
chain serves stock snapshots; crypto symbols mostly fail to price): pass
`--crypto` to include them. A coin is quoted under the same Yahoo
spelling the books were priced with (the project's ticker.map `CRYPTO`
lines, else `SYMBOL-USD`). In a **US** project a crypto account's losses
are outside the wash-sale rule (US-WASH-13): they count as claimable now
and the ADVISORY reads `no-wash-rule(crypto)`; a Canadian crypto loss
stays under the superficial-loss rule like a share.

Option positions (e.g. LEAPS) are also excluded by default. Pass
`--options` to include them: they price **only** through the IBKR tier
(plus the cache), because yfinance option chains are too stale or wide
for illiquid strikes; with no TWS / Gateway running the contracts are
listed as unpriced rather than marked from a bad source (`--no-ibkr`
skips the IBKR tier; `--ibkr-port` picks the port, default 4001).
Option rows show `PRICE` and `COST/SH` in per-share premium terms
(`UNREALIZED` carries the contract size the rows declare: a `.tt` line's
`xN`, a broker's multiplier, and ×100 when none is declared), a `DTE`
days-to-expiry column appears, and the premium currency follows the
underlying's listing. A contract that ticker.map renamed onto another
listing's code is quoted as the contract actually held. The radar does
not track option contracts: rebuying the *same* contract within 30 days
of a loss sale still triggers the wash / superficial rule even though
ADVISORY shows `-`. Futures and futures options never appear (no live
tier serves them).

An LSE (`.L`) quote that does not say whether it is in pence or pounds
(a price-cache entry written before the unit was recorded, or a tier
that reports no unit) is left out with a warning, never valued as
pounds. In a US project an open short shows `ST` under `LT_IN`: covering
it is short-term (US-HOLD-03). `--verbose` prints the per-tier
price-chain diagnostics; `--json` carries the scope note (`scope_note`)
like the radar's. Under grant timing, a written option's UNREALIZED is
the whole buy-back cost, because the premium was already recognised at
the write ([tax-rules.md](tax-rules.md#options-premium-timing-s49)).

#### taxjson tips

`taxjson tips [--online] [--json]`

Advice for next year on where you hold what. It changes no number of
this year:

- **US-LISTING**: a cross-listed Canadian issuer (per `ticker.map`, or a
  Canadian listing of the same root seen in your data that the exports
  do not show to be another security, a CDR or another company;
  "verify" unless the names agree) held via its **US listing** in a
  taxable account or TFSA while receiving dividends. Hold the `.TO` line
  instead: clean eligible-dividend treatment, no USD conversion drag. An
  option counts as a sighting of its underlying's listing.
- **TFSA-US-DIV**: a US-domiciled dividend payer inside a **TFSA**: the
  15% US withholding is unrecoverable there. RRSPs are treaty-exempt
  (never flagged); taxable accounts can claim the foreign tax credit.
- **`--online`** (Yahoo Finance; candidates to verify, not verdicts):
  **MAP-GAP?**, a `.TO` twin of every unmapped US-listed dividend payer
  (the same root can be a different issuer), and held listings clustered
  by exchange-reported issuer name, which catches different-root dual
  listings (SAMPLQ.US / SAMPLP.TO); **MAP-BAD?**, a defined GLOBAL /
  TOBASE / JOURNAL pair whose two sides name DIFFERENT issuers per the
  exchanges (or pair a CDR with its underlying): a typo'd pair silently
  merges two companies' ACB pools; **CDR-PAIR**, a `.TO` line whose
  exchange name says CDR (Canadian Depositary Receipt, e.g. SAMPLR.TO
  over SAMPLR.US): the same issuer but NOT a listing equivalent
  (fractional, CAD-hedged, floating ratio). Never map it; nothing else
  to do: look-alike listings are never joined, so it needs no
  `DISTINCT` line.

Registered-plan kinds are inferred from a plan word that is a whole
token of the account name (`tfsa`, `rrsp2`, `my-tfsa`; not `admiral`);
override per account with `plan = "tfsa"` in `taxjson.toml` when a name
does not say (an unknown `plan` value is warned about and ignored).
Plans belong to one country: Canada tfsa, rrsp, rrif, lira, lif, lrif,
fhsa, resp, rdsp, prpp; the US ira, roth, 401k, 403b, 457b, sep, hsa,
529 (plus `taxable` / `sheltered` in both). The other country's plan is
refused, and a plan that contradicts the account's `type` is warned
about: the type decides.

Exit 0 with or without tips (advice never fails a command); exit 2 when
the project cannot be read (no holdings reports: run `taxjson run`).
`--json` (schema_version 2): `findings` (`check`, `account`, `symbol`,
`message`) and `unscanned`. The map's own hygiene (listing pairs to
verify, unused rules, cross-listing losses) is
[`taxjson ticker-map --suggest`](#taxjson-ticker-map)'s job.

#### taxjson watch

`taxjson watch [--harvest] [--threshold AMT] [--no-ibkr] [--state PATH] [--exit-code] [--json]`

A cron-able change detector: it reports only what CHANGED since the
last watch run: new, changed or cleared radar advisories, moved clear
dates, and (with `--harvest`, which runs `taxjson harvest --json` and
needs a price source) the harvestable-now loss total moving more than
`--threshold` (default 100, base currency). A report ends with the scope
line (verdicts cover this project's accounts only: CA-PLAN-04 /
US-PLAN-04). It is silent with exit 0 when nothing changed, so a cron
line mails only on news; `--exit-code` exits 1 on changes for scripting,
`--json` for machines. State: `work/.watch_state.json`; `--state PATH`
gives a cron cadence its own baseline (daily and weekly lines can
coexist). `--no-ibkr` is passed on to harvest.

### Before you file

#### taxjson form-export

`taxjson form-export [--form 8949|schedule3|txf] [--box A|B|C] [--out FILE] [--csv FILE] [--json]`

Renders the year's computed gains in the shape the forms want, so filing
day is transcription rather than arithmetic. The form follows the
project country (`--form 8949` in a US project, `--form schedule3` in a
Canadian one; the other country's form is refused). `--csv FILE` also
writes importable rows, `--json` the raw report. A US project can also
write a TurboTax-importable TXF: `--form txf [--box A|B|C] --out
gains.txf`.

- **`--form 8949`** (US): one row per disposition chunk: description,
  derived acquisition date, sale date, proceeds, cost, **code W** with the
  disallowed wash-sale amount as the column (g) adjustment, and the
  allowed gain in (h) = (d) − (e) + (g) (each row foots on its rounded
  cents, so the part totals and the TXF agree), split into Part I
  (short-term) / Part II (long-term) with the Schedule D totals per part.
  Pick the 8949 box (A–F) yourself from whether the broker reported basis
  on your 1099-B. From tax year 2025 the `crypto = true` accounts' sales
  are digital assets: their own group on boxes G/H/I (short-term) and
  J/K/L (long-term) with their own totals (in `sum`'s FOR THE RETURN and
  the close-year lock too); the TXF carries only boxes A–F and leaves
  those rows out with a warning (`--box` picks the TXF's securities
  pairing: A/D basis-reported, the default; B/E; C/F). §1256 contracts
  (futures, options on futures and broad-based index options: SPX, XSP,
  NDX, RUT, VIX and their weekly roots) are **not** on Form 8949: they are
  kept out of the rows, the totals and the TXF, and listed in a **FORM
  6781 BY HAND** section with their P/L (the 60/40 split and year-end
  marking are not modelled). Cells are rounded half-up to the cent.
- **`--form schedule3`** (Canada): per-security rows (units, acquisition
  year, proceeds of disposition, ACB, outlays, gain or loss) routed to
  the Part 3 line for the property type: **line 4** publicly traded
  shares and fund units (13199 / 13200), **line 6** options, futures and
  other properties (15199 / 15300; T4037 lists options there), **line 7**
  crypto-assets from the `crypto = true` accounts (15200 / 15301; for
  2024 and earlier returns crypto goes on 15199 / 15300), with per-line
  totals. The 2024 form splits Part 3 by period: dispositions from
  January 1 to June 24, 2024 go on 10689 / 10690 (shares) and 10693 /
  10694 (options, futures, crypto and other properties), the rest on the
  codes above, so a security sold in both periods has two rows; slip
  gains go on 17399 / 17599 for Period 1. A 2024 close-year lock written
  before this split is compared on the Period 2 codes (`check-filed` says
  so in a note).

  A futures contract is booked on its settled P/L, not its notional (the
  notional never changes hands): the P/L of each close, commissions
  included, is converted at that closing leg's rate and shown the way the
  broker's T5008 shows it: a gain as proceeds with ACB 0, a loss as ACB
  with proceeds 0, no separate outlays. Sell-side commissions on long
  sales are re-split into the outlays column (gain unchanged), and so is
  a written option's commission under grant timing: the premium is shown
  GROSS as proceeds with the write commission as an outlay. Under close
  timing a write's commission, and a short sale's opening commission,
  stay netted into the proceeds with no outlay (the closing row does not
  carry them): the same gain, slightly lower proceeds than a broker slip.

  Every row foots: proceeds − ACB − outlays = the allowed gain. A
  superficial loss denied on the row shows as an ACB reduced by the
  denial, noted per row (the denied amount goes onto the replacement
  property's ACB; a registered-account or affiliated-person denial is
  noted as permanent for this return). A short sale shows what it brought
  in as proceeds and the cover as ACB (a close-timing write for a net
  debit: no proceeds, the debit as an outlay; under grant timing it shows
  its premium and its commission). Units are the contracts or shares
  disposed of, at full precision: under grant timing a written option
  and its buy-back in the same year count once; a buy-back of an earlier
  year's write (grant or close timing) is a disposition of its own. A net
  commission rebate (a negative IB or Questrade commission) is not an
  outlay: it stays netted in the proceeds, so the OUTLAYS column is never
  negative. Each cell is rounded half-up to the cent and the ACB is the
  row's footing residual, never below 0.00.

Both forms refuse rows in another currency than the return's (CAD for
Schedule 3, USD for 8949 / TXF: the native `*_raw_gains.json` beside the
converted file) and a file that is not a gains file (no `transactions`
list, or a pre-gains stage file); a disposition with no currency is
warned about. A row whose date is not a string or whose money field is
not a number is refused with the file and row named (every report reader
checks `work/` rows this way). Before the tax year has ended the report
says the figures are year-to-date (as `sum`'s FOR THE RETURN block
does), and both print the per-row rounding note `sum` prints. `--csv` is
written through a temporary file, so a failed write leaves the previous
CSV intact.

Both read the wash-adjusted gains (the allowed numbers a return
reports). **Sales with no purchase in your files (an unknown cost) are
not in the rows or totals**, since their cost is unknown, but they are
never dropped silently: a stderr warning names each one with its
proceeds, the text report ends with a **MANUAL REPORTING REQUIRED**
section, the CSV carries them as `MANUAL` rows (blank cost and gain),
the JSON as `manual_reporting_required`, and `taxjson checklist` keeps
the form-export step open until they are reported by hand.

#### taxjson t1135

`taxjson t1135 [--json]` (Canada)

A helper for CRA **Form T1135** (Foreign Income Verification Statement),
for Canadian filers holding foreign securities. It answers the filing
question first: it replays the full history of every **taxable** account
in the base currency and reports the **maximum total cost of specified
foreign property at any time in the year**, the ITA 233.3 test ($100,000
threshold; $250,000 for the detailed method, from the form's
instructions). The test sees these brokerage books only: foreign
property held outside them (a foreign bank account or cash,
certificates, foreign real estate) counts toward the same threshold and
must be added by hand. Before Dec 31 the figures run to the last date in
the books, a "below the threshold" verdict says "so far" (the checklist
keeps the step open), and the year-end column is headed with that date.
A long option still held after its expiry date is named (its cost is
still counted). The books must be in the base currency (the converted
`_base.json` files; native-currency rows are refused).

If a filing is required, it prints per-property and per-country tables
(maximum cost in the year, cost at Dec 31, income, gain or loss) from the
same books the rest of the pipeline reports on, with the project's
missing-history openings applied exactly as the gains stage applies
them. Registered accounts are excluded by law and never read. A plain
futures contract has no cost amount (nothing is paid to open one), so
its notional stays out of the cost columns and the threshold test; an
option on futures counts at its premium. An assigned written put's
premium is deducted from the shares' cost and an exercised call's cost
added to them (s.49(3)/(3.1)), rows sharing a timestamp follow the
engine's order, a split inside a trade's settle lag re-denominates it
like the engine, and the gain column and year-end position follow the
project's `tax_date`. A superficial loss denied in ANY year is in its
replacement's cost (s.53(1)(f)), exactly where the engine put it:
`t1135` runs the engine once over the full history (with the registered
accounts as wash context and the project's option timing, as `carryover`
does) and replays each denial's addition (the standalone
`taxjson-t1135 --year-wash-only` skips that pass, adds only the project
year's denials and names what that leaves out). A configured taxable account with inputs but no books is refused,
and books built for another year are warned about. `--json` for
machine-readable output.

<pre>
$ taxjson t1135
T1135 — Foreign Income Verification Statement helper, tax year 2025, CAD

FILING REQUIREMENT — total-cost test, ITA 233.3
Maximum total cost in 2025: 262,500.00 CAD on 2025-06-16 <!-- pii-ok -->
...

PER PROPERTY — taxable accounts only
Amounts are cost (ACB), not market value; MAX COST IN YR: the most held at once.
SYMBOL     COUNTRY  MAX COST IN YR  COST AT DEC 31  INCOME  GAIN(LOSS)
----------------------------------------------------------------------
SAMPLG.US  USA              980.00          490.00  132.00      120.00
...

PER COUNTRY — upper-bound aggregates
...
Not counted: foreign property outside these books (bank accounts, cash) — tjs t1135 --details
</pre>

The verdict lines under FILING REQUIREMENT say whether a filing is
required and whether the detailed method (Part B) applies; a `! ` line
names a row to check by hand (crypto: where it is held), and
`--details` adds the notes behind each.

Domicile is classified by market suffix (`.US` → USA, `.L` → GBR, `.AX`
→ AUS; `.TO` / `.V` / `.CN` / `.NE` → Canadian, i.e. not foreign
property). Since domicile, not listing exchange, is what T1135 cares
about, interlisted names can need a `T1135 SYMBOL COUNTRY` line in the
project's `ticker.map` (symbols are matched case-insensitively; an
override follows the symbol through a ticker change, and one that
matches nothing in the books is warned about; a COUNTRY that is neither
an ISO 3166 alpha-3 code nor CA / CAN / CANADA / EXCLUDE stops `taxjson
run` and `taxjson t1135` with a did-you-mean hint, like any malformed
ticker.map line). In a Canadian project, tobase.map books an interlisted
pair under the issuer's home listing and gives a foreign issuer's pair
the issuer's country (`country=`: a Bermuda issuer's US listing is BMU,
not USA); a `T1135` line naming a listing that a `TOBASE` line books
under another symbol applies to that symbol. These were once the lines
of a separate `t1135.map`; `taxjson migrate` converts one. A foreign
listing whose rows carry a Canadian ISIN (IB stamps the issuer's
country) is named in a warning until you map it:

```
# ticker.map — T1135 SYMBOL COUNTRY (ISO-3 code, or CA/EXCLUDE for "not foreign")
T1135 SAMPLW.US   CA      # Canadian corp held on NYSE — not specified foreign property
T1135 SAMPLV.TO   USA     # foreign corp listed on TSX — still specified foreign property
```

Options on a pooled Canadian issuer are a filing position taxjson takes
(tax-logic CA-RPT-17): a US-listed option on a Canadian issuer whose US
and Canadian listings one `TOBASE` line (tobase.map's included) pools
under the Canadian listing is booked under the Canadian listing's option
code and is not counted as specified foreign property. The reasoning: a
right to acquire property is caught as specified foreign property when
that property is (s.233.3(1)), and shares of a corporation resident in
Canada are not; the contract's US listing and clearing are not read as
making it property held outside Canada. CRA's T1135 guidance does not
address listed options; a `T1135` line on the contract's booked symbol
sets another country.

Symbols with no market suffix (typically exchange-held crypto) are
bucketed as country `CRYPTO` and counted toward the threshold: crypto
held on a foreign exchange is generally specified foreign property, so
map each one in `ticker.map` (`T1135 SYMBOL <ISO3>`, or `T1135 SYMBOL CA`
for a Canadian platform) once you have checked where it is held. Country
`??` marks only an unknown market suffix, for manual review. Amounts are
**cost** (ACB-style), correct for the threshold test and the "maximum
cost amount" columns; the category-7 detailed method's month-end **fair
market value** boxes need your broker's statements, which this tool does
not fetch. Not tax advice. The rule: [tax-rules.md](tax-rules.md#t1135-foreign-property).

#### taxjson reconcile-slips

`taxjson reconcile-slips SLIP.csv [SLIP.csv ...] [--tolerance N] [--json]`

Diffs the broker's official slips (CRA **T5008**, IRS **1099-B**) against
the computed dispositions, per symbol: disposition count, quantity,
proceeds, and (when the slip carries cost) basis. Several slip files
(one per broker) are reconciled together. CRA and the IRS machine-match
returns against these slips: run this before filing and decide, for
every flagged row, whether it is a tool-side problem (dropped rows,
missing statement months) or a legitimate, documentable difference
(per-broker box-20 book value vs the blended ACB, the broker's lot
method vs FIFO). `--tolerance` is the absolute per-symbol tolerance
(default 1.00).

Slip headers are matched loosely: `Security` / `Box 16` / `Box 21` /
`Box 20` T5008 spellings work as-is; so do `Symbol` / `Quantity` /
`Proceeds` / `Cost or other basis`, the T5008 box headings and French
headings. An exact heading wins, and two columns that both look like one
amount (`Proceeds` and `Proceeds of disposition`, `Quantity` and `Qty`)
are refused as ambiguous; a ticker column beside a security-name column
is fine. A slip symbol without a market suffix matches the computed
listing of that root (slip `SAMPLG` ↔ computed `SAMPLG.US`); when the
books hold two listings of one root (a CDR `SAMPLB.TO` and `SAMPLB.US`)
the row is `AMBIGUOUS_LISTING` until the slip CSV names the suffix.
Broker option descriptions (`SAMPLE 21MAR25 50 C`, `CALL SAMPLE03/21/25
50`, a strike with thousands separators), share classes (`SAMPLC B`) and
the project's `ticker.map` renames (slip `SAMPLK` ↔ books `SAMPLJ.TO`)
are matched.

A blank proceeds cell beside a cost is nil proceeds (an option that
expired worthless); a worthless expiry with no slip row is
`NO_SLIP_EXPECTED`, not a failure. Under grant timing an option written
this year and still open at the year end is `NO_SLIP_EXPECTED` too (the
premium is reported in the write year, the broker's slip comes in the
close year), and a close-year slip whose proceeds include an earlier
year's write premium reconciles with a note. A slip row with amounts but
no symbol, or an unreadable quantity, is counted as not reconciled.
Net-of-commission slips are detected and noted. Slips aggregated per
type code (IBKR's SHS / OPC / FUT rows, "Various") cannot be compared:
transcribe a per-security CSV. Books built for another tax year are
refused with a rebuild message.

Exits 1 when anything does not reconcile (cron and pre-filing checklist
friendly). Slip cost differences are reported as *notes*, not
mismatches, because they are often correct (document them, do not "fix"
them). A US project's notes name the 1099-B and FIFO basis per account,
a Canada project's the T5008 and the blended ACB. The standalone
`taxjson-reconcile-slips` needs `--country` (it sets the base currency
the slip amounts must be in and the default `--date-basis`: settlement
date for Canada, trade date for the USA); `taxjson reconcile-slips`
passes both.

#### taxjson slip-audit

`taxjson slip-audit [ACCOUNT] [--template] [--import-cra PDF|DIR ... [--write]] [--tolerance N] [--json]` (Canada)

Checks the T5 and T3 slips against the books' income, per account and
slip box: Canadian dividends (T5 24/10, T3 49/23), box 18 capital-gains
dividends against `[[capital_gains_dividends]]`, foreign income and tax
withheld (T5 15/16, T3 24/25/33/34), return of capital (T3 42) against
the books' ADJUST rows, interest (T5 13). The slips come from three
places in `inputs/slips/`:

- `slips.toml`, typed from the slips (`taxjson slip-audit --template`
  prints one, a T5 per account and currency with income; the format is in
  [settings.md](settings.md#inputsslips-slipstoml-and-ibs-dividends-reports));
- the PDFs CRA My Account shows under "Tax information slips"
  (`taxjson slip-audit --import-cra <folder>`: one layout for every
  issuer; each slip is placed in the account, broker account and fund
  whose payments it matches, a T5 by its issuer, the broker's name or its
  carrying dealer's (Webull Canada's slips come from CI Investment
  Services; `src/taxjson/data/slip_issuers.toml`), each slip once; an
  amended slip replaces its original; `--write` appends them to
  `slips.toml`);
- IBKR's dividends report (`U*.YYYY.dividends.csv`, its per-payment
  T5 / T3 split), matched to the account whose books carry that IB
  account, payment by payment (a USD-base account's payments converted to
  CAD at the Bank of Canada rate of each pay date).

A slip in USD (RBC, Webull) is compared in USD, and its CAD is shown
both ways: each payment at the Bank of Canada rate of its date (what the
books do) and the year's average rate (the mean of the Bank's daily
rates in the FX cache); for a CAD slip holding converted payments (IBKR)
the report says which of the two its figure is closer to. It lists
payments on the slip and not in the books (and the reverse), trust
distributions counted in another year by their record date, accounts
with income and no slip, slips with no income behind them, and prints
the exact `[[capital_gains_dividends]]` entries and `.tt` lines
(`ADJUST ... type=roc`, and a negative `DIVIDEND` when the dividend row
holds the return of capital) that bring the books to the slips.
`--tolerance` is the allowed difference per box in CAD (default 1.00; a
box holding converted money also allows 0.5% of that part). It changes
nothing; exit 1 on a finding. `--json`: a stable schema
([settings.md](settings.md#taxjson-slip-audit---json)). The checklist's
`t5-t3` step runs it and is answered per account with `--done`.

#### taxjson carryover

`taxjson carryover [--json]`

A multi-year **capital-loss carryforward / carryback ledger** over the
taxable accounts' full history (the same engine as the yearly pipeline:
wash / superficial-loss adjustments and missing-history openings
included; a parity test guarantees each year's net matches the
pipeline's own per-year run). Per year with any disposition:

- **Canada**: net gain or loss, a running net-capital-loss carryforward
  (losses carry forward indefinitely), and for each loss year the
  still-open **3-year T1A carryback candidates**, capped at each earlier
  year's remaining net gain and never double-counted across loss years.
- **US**: net short-term / long-term, the up-to-$3,000 ordinary-income
  offset (assumed used when available), and the running ST / LT
  carryover per the Schedule D worksheet ordering.

Amounts are 100% gains and losses: Canada applies the 50% inclusion rate
on Schedule 3 / T1A, not here. The ledger shows what the *transaction
history* supports; record what you actually claimed on filed returns in
`taxjson.toml`, `[carryover]` then `claimed = { 2023 = 400.00, 2024 = 150 }`
(one amount per tax year), and it is folded into the running balance.
**Units:** Canada, the 100% capital loss applied that year, i.e. the
line 25300 amount divided by the inclusion rate (x2 at 50%); US, the
Schedule D line 21 deduction against ordinary income as far as taxable
income absorbed it (line 4 of the next year's Capital Loss Carryover
Worksheet, 0 in a year with negative taxable income; not the line 6/14
carryover coming in). An amount must be a number of 0 or more, unquoted,
and a year a plausible tax year; anything else stops every command
naming the entry. (The standalone `taxjson-carryover` still takes a
`--claimed FILE` of `YEAR AMOUNT` lines, or `--claimed-year
YEAR=AMOUNT`.) A claim equal to the filed (per-row-rounded) Schedule 3
loss consumes the ledger's unrounded loss exactly. A claim recorded for
a year the books show no loss for waits for a later loss, but only one
of the next 3 years (the T1A carryback reach, ITA 111(1)(b)); past that
it is reported as unmatched.

If the history's first year has dispositions, the ledger warns that
pre-history balances are not reflected, and rows before the project
year are flagged as rebuilt from this project's books (opening lots plus
whatever prior-year exports are in `inputs/`) and possibly partial:
check them against the filed returns. A year before the project year
that has a close-year lock (the project's own `filed/<year>.json` or the
`[settings] prior_year_record` of the per-year layout) takes the lock's
**filed** figure instead (Canada: the total filed with another tool when
the lock has one, else the Schedule 3 gain lines; US: the Form 8949 Part
I / Part II gains), and the lock's recorded balance becomes the running
balance at its year end; a later locked year is compared with its lock,
and an unreadable lock is named. Rows after the project year (a few
January trades in this year's inputs) are partial: they offer no T1A
carry-back and the carryforward stops at the project year. Every year
uses the project's settings, its income dating
(`corporate_distributions`) included. The net per year counts
dispositions plus the T5 box 18 dividends named in
`[[capital_gains_dividends]]`; other slip capital gains (lines
17400/17600, US Schedule D line 13) and the line-15300 FX gain on foreign
cash (`taxjson fx-cash`) are not in it. The rules:
[tax-rules.md](tax-rules.md#net-capital-losses-carryforward-and-carry-back).

#### taxjson option-boundary

`taxjson option-boundary [--json]` (Canada)

Written options whose write and close straddle a tax-year boundary, or
that are open at year end: where the premium and any later amount land
under ITA s.49 for the timing in force, and, using the `filed/` locks,
whether a filed year needs a T1-ADJ (an assignment after the grant year
was filed, s.49(4)). A contract written in a LOCKED year but kept on
transition close timing here is flagged ATTENTION, as is a contract past
its expiry date with no expiry or assignment row in the export. How
`option_premium_timing` and `option_grant_timing_since` work:
[tax-rules.md](tax-rules.md#options-premium-timing-s49).

#### taxjson close-year

`taxjson close-year [--year YEAR] [--force] [--filed-dispositions CSV]`

Snapshot the current tax year's filing aggregates to
`filed/<year>.json`, the filed-year lock that `check-filed` (and every
full run) guards. Commit it with your records. It also records what the
next year needs for `taxjson handoff`: every sale, the positions and cost
at Dec 31 (superficial-loss deferrals included), and the trades that
settle in January. And it records the year's carry-forwards (the net
capital loss; US: the short- and long-term capital loss carryover; in
Canada, the minimum tax carryover by year of origin) from the year's own
estimate: set `[estimate] other_income` before closing, since the
minimum tax depends on it. A Canadian project with no supported
`province` gets a federal-only estimate, said in the output (every
figure carried forward is federal anyway). See
[tax-rules.md](tax-rules.md#net-capital-losses-carryforward-and-carry-back).

When the return was prepared with another tool, `--filed-dispositions`
stores the sales it actually reported (CSV:
`symbol,date,qty,proceeds,cost,gain`, optional `account`); the next
year's `taxjson handoff` checks doubles against these. `--year` must
match `[settings] year` (a guard). `--force` replaces an existing lock
(re-filed or amended years only): it keeps the filed dispositions of the
lock it replaces (unless a new CSV is given) and warns when that lock
recorded other totals. It refuses books whose last run did not finish
(no reports, an unreadable `work/<acct>_base.json`). Without `--force`
it refuses a year that has not ended and a year with no disposition and
no income in the taxable books (a typo'd `year`); books built with
another option timing than `taxjson.toml` now says are refused even
with `--force`.

#### taxjson check-filed

`taxjson check-filed`

Recompute every filed year from the current books and report drift
against the locks; exit 1 on drift. A taxable account the books have but
the lock does not (with activity in that year), or a locked account the
books no longer have, is drift too; a locked account that is no longer a
taxable account in `taxjson.toml` is reported, never recomputed from its
old `work/` book. Each year is recomputed with the written-option timing
its lock recorded. Dividends and payments in lieu are compared
separately, and so are the amounts the export puts on each return line
(Schedule 3 line codes, Form 8949 part totals), so a change that moves
an amount between lines is drift even when the gain is unchanged;
interest, foreign tax withheld and the FX gain on foreign cash are not
locked (every OK says so).

An unreadable lock is named and counts as a failure. A lock closed under
the other country (every lock records its `country`) is refused by name
and never recomputed under this project's law. A lock is recomputed on
the date basis it recorded, and a note says when this project's
`tax_date` or `option_buyback_loss_superficial` now differs from the
lock's (its own reports for that year then differ from the filed
return). A lock account entry that records none of the locked totals, or
whose `form_lines` is not a table, is damaged, never OK. A bad
`[settings]` value is refused as a settings error before any lock is
checked; when the recompute itself fails on an input, the child's own
error is shown and the exit code is 2 (1 is drift or a damaged lock).
Every full run also checks the locks (`taxjson run --strict` stops on
drift or an unreadable lock).

#### taxjson handoff

`taxjson handoff [--prior PATH] [--json]`

Checks that this year's project starts from exactly what last year's
return carried forward, using last year's `close-year` record
(`--prior`, else `[settings] prior_year_record`, else
`filed/<year-1>.json` here). It checks:

- opening positions and cost at Dec 31 against last year's year-end
  books;
- every trade made last year that settles in January is booked here,
  once;
- no sale is reported in both years (a closed-year sale that is its own
  row here is a different sale);
- rows the two projects put on different sides of Dec 31 (income a
  trust's record date or a RIC entry moves, a row `local_timezone`
  re-dates, an overnight fill moved into January) are reported in neither
  or both years;
- written options carried out of last year on another premium timing
  than its record (taxed twice, or in no return);
- the carry-forward inputs (`[estimate] other_losses` /
  `long_term_losses`, `[carryover] claimed`'s entry for that year,
  `[estimate] amt_carryover`) against what the record carried out: your
  notice of assessment decides which is right.

A record closed before its year ended is flagged as a partial-year
snapshot. A cost difference is listed with the two consistent choices:
keep last year as filed and open with the cost that return implied, or
amend it and open with the corrected cost. A record closed under the
other country is refused by name. Exit 1 on any problem; `checklist`
runs it.

### Explain and check

#### taxjson audit

`taxjson audit [SYMBOL ...] [--id ID] [--date D] [--account NAME] [--year Y | --all-years] [--summary] [--no-trace] [--no-color] [--json]`

The **authoritative justification** of every capital-gain figure: one
block per disposition tracing the parsed broker row (nominal currency,
original ticker, source file) through the ticker.map rename, the exact
FX rate applied (provenance named, recomputed against the base books to
the cent), the ACB / FIFO disposition math, and the wash-sale /
superficial-loss determination with replacement lots resolved, ending in
a tie-out against the pipeline's saved gains files and the full pool
trace. It runs the same blended computation the pipeline runs, so the
audited numbers ARE the filed numbers. Exit 1 when any cross-check
disagrees.

`--summary` prints one line per event (to find ids to dive into). The
filters: symbol prefixes, `--id` (a transaction id prefix), `--date`,
`--account` (display only: the computation stays blended). Each event
prints its row's own id unmasked, on purpose: it is the `--id` handle
(for Kraken the exchange's ledger txid, an exchange reference; see
[SECURITY.md](../SECURITY.md)). `--year Y` audits a locked year (its
`filed/Y.json`, or the lock `[settings] prior_year_record` names),
recomputed with the option timing and date basis the lock recorded, with
a note; `--all-years` audits every year on the books. `--no-trace` omits
the engine pool traces, `--no-color` turns colour off (`NO_COLOR` is
honoured too), `--json` prints the full audit.

#### taxjson wash-sales

`taxjson wash-sales [ACCOUNT] [--explain] [--json]`

Each wash sale (superficial loss) that **occurred** in the tax year and
the loss the engine **denied**, which `<account>.sum` otherwise folds
silently into the ticker totals (a disallowed loss just shows up as a
smaller or zero gain, unlabelled). One row per denied disposition
(economic gain or loss, denied amount, allowed loss) plus a denied
total, then the warn-only flags (warrants, adjusted series, futures
options) that need a manual check. It reads the gains files, preferring
the cross-account `<account>_gains_wash.json` (which also catches
registered-account repurchases) when the pipeline built it. This is the
backward-looking record; for what you may sell or buy now, use
[`taxjson wash-radar`](#taxjson-wash-radar).

```
$ taxjson wash-sales
SUPERFICIAL LOSSES — CAD, tax year 2025, basis: wash-adjusted
DENIED: the loss the superficial-loss rule (s.54) denies; ALLOWED: the loss you claim.
ACCOUNT  DATE        SYMBOL  QTY  PROCEEDS    COST     GAIN  DENIED  ALLOWED
-----------------------------------------------------------------------------
margin   2025-10-08  ZZA.US   10    820.00  860.00   -40.00   30.00   -10.00
margin   2025-10-17  ZZB.US    5    630.00  740.00  -110.00  110.00     0.00

2 superficial loss(es); 140.00 CAD of losses denied.
More: tjs wash-sales --details (what DENIED means); --explain: each denial's trace
```

`--details` adds WHAT DENIED MEANS, the filing positions and the
manual-check flags in full; a line under the total says how much of the
denied loss sits in open positions (`taxjson list`, DEFERRED).

A DENIED loss is added to the ACB of the substituted property
(s.53(1)(f); recovered on a later sale), except any amount permanently
denied by a repurchase in a registered account. A Canadian project
titles the report SUPERFICIAL LOSSES; a US one WASH SALES, with the
§1091 basis wording. The table fits the width
([output-style.md](output-style.md)): a long option symbol drops the
COST, then the PROCEEDS column, and a narrow terminal gets one record
per denial.

Add **`--explain`** to see *how* each denial was computed: for each one
its figures, the ACB pool's history (US: the basis lots), the triggering
repurchase and the disallowance math, and the ±30-day window as a table
of the same-symbol rows with their roles, instead of the summary table.
Country, tax-date and sheltered context come from the project.

```bash
taxjson wash-sales margin --explain     # full calculation trace for each wash sale
taxjson wash-sales --explain            # all accounts
```

For tracing any other disposition, the standalone `taxjson-explain
--country canada --symbol SAMPLE work/<account>_base.json` remains
available (`--country usa` in a US project). When a call option, a
warrant or another right to acquire counts as replacement property, and
which cases are only flagged:
[tax-rules.md](tax-rules.md#superficial-loss-s54); a registered or
affiliated holder's repurchase:
[tax-rules.md](tax-rules.md#registered-and-affiliated-holders-permanent-denial).

#### taxjson tax-logic

`taxjson tax-logic [--country canada|usa] [--ids] [--json]`

A short statement of every rule taxjson applies for the project's
country, one line per rule, with the project's own settings filled in
(`tax_date`, option premium timing and `option_grant_timing_since`,
`futures_settle`, `foreign_return_of_capital`,
`option_buyback_loss_superficial`), read through the same resolvers the
engine uses, so a value the run reads differently or refuses is refused
here too. It covers tax-year dating and settle dates (income by pay
date; in Canada a Canadian trust's distribution and return of capital by
the record date the export prints; in the US the January fund / REIT
dividends you list on Dec 31), currency conversion, ACB pooling and
identity, Schedule 3 lines, the superficial-loss rule including options,
option premium timing and exercise, corporate-action elections, income
lines, crypto, the reports, and which settings and commands the
project's country refuses. tax-logic is the spec the code is tested
against: every statement has a stable rule id (`CA-SL-02`,
`US-WASH-01`, ...) that `--ids` shows and `--json` lists, and that the
tests cite (see [CONTRIBUTING.md](../CONTRIBUTING.md)). Canadian and US
rules never mix. Outside a project, pass `--country`. The rules with
their sources: [tax-rules.md](tax-rules.md).

#### taxjson edge-cases

`taxjson edge-cases [ACCOUNT] [--margin DAYS] [--json]`

Everything whose treatment turns on a boundary, with where it lands and
why: trades that settle in a different year than they trade (and the
year `tax_date` puts them in), dispositions in the last and first days
of a year, written options and expiries across Dec 31, income paid
around New Year, crypto near midnight, renames, superficial-loss windows
that span Dec 31 and denied losses carried into next year. Then every
taxable loss with an acquisition (any account) or a sale within
`--margin` days (default 3, at least 0) of day 30, with the day count on
the engine's window dates (settlement dates in Canada, trade dates in
the US, whatever `tax_date` says: it decides only the year) and the
other date for reference; and long calls bought inside a share loss's
window (s.54 "right to acquire": replacement property when still held on
day 30; a buy-to-close of a written call is not one).

Positions count opening balances, missing-history lines and splits;
income lands in the year income dating gives it (a Canadian trust's
December record date, payments in lieu and trust ROC included); crypto
rows are listed when their local date (`local_timezone`) and UTC date
fall in different years; written options are judged against the filed
locks, `prior_year_record` included, with the timing each lock records
(as `option-boundary` does); an option assigned on its expiry date is
described as an assignment. An unreadable work file stops the command
with its name. A US project is explained with §1091: trade dates, no
still-held test (so no sales near day 30 and no held-on-day-30 figures),
long calls listed as warnings only, no window for crypto (property, not
a security), Form 8949 rather than Schedule 3, and no written-option
year boundary.

#### taxjson check-dates

`taxjson check-dates [ACCOUNT] [--all] [--json]`

Checks every trade and settlement date the parsers produced against
what was traded: crypto any day and hour; futures and futures options
Sunday evening to Friday afternoon (no Saturday); US stocks on NYSE days
plus the overnight session (Sunday to Thursday from 20:00); options and
Canadian listings on exchange days (a Sunday-evening SPX / SPXW / XSP /
VIX Global Trading Hours fill is a note). IB fills outside the regular
session are already re-dated to their exchange trade date (CME evening
futures, Cboe GTH, the US overnight session, ASX and Asian venues:
tax-logic CA-DATE-SESSION).

Settlement: never before the trade or on a weekend (a futures fill from
a Sunday-evening Globex session, dated and settled on its clock day, is
a note), and normally the standard cycle on either the US or Canadian
calendar (a broker's own cycle is a note; corporate events, expiry-day
options and `.tt` lines are exempt from the cycle check). An option
expiry row that settles after the contract's expiry date is a WARN (on a
settle basis it moves the expiry to the next year). A `.tt` line carries
its settlement date, so it may be up to one settlement cycle after
today. A parsed file that cannot be read is an ERROR, and an invalid
`futures_settle` is refused as `run` refuses it. ERROR for impossible
dates (exit 1), WARN for exchange holidays, NOTE for unusual but
explainable dates. It lists the first 10 rows per kind (`--all`: every
row). A `checklist` step.

#### taxjson sanity

`taxjson sanity [ACCOUNT | FILE.toml | ACCOUNT[+ACCOUNT]=FILE[+FILE] ...] [--tolerance N] [--cost-tolerance N] [--json]`

Cross-checks the open positions against holdings files produced outside
taxjson (a `[[holding]]` array of `symbol` / `quantity` tables), per
symbol; exit 1 on any discrepancy. Holdings files are optional: a
project without them runs, and `sanity` then says which accounts it
could not check.

- Bare items form one aggregate group (combined positions vs combined
  holdings: quick, but blind to a position sitting in the wrong
  account).
- `ACCOUNT[+ACCOUNT]=FILE[+FILE]` pairs specific accounts with specific
  files and is checked as its own group. It is many-to-many because a
  taxjson account can span several broker accounts
  (`margin=ibkr.toml+webull.toml`, or repeat `margin=…`) and one broker
  export can cover several accounts (`rrsp+lira=flex.toml`). Both forms
  mix freely.
- With no arguments the pairings come from `taxjson.toml` (each
  account's `holdings = [...]`), and `taxjson run` finishes with the same
  check as a warning. An account without `holdings = [...]` is compared
  with its snapshots in the year's `holdings/` folder (`holdings_dir`):
  each file goes to the account whose `account` or `broker_accounts`
  holds its `[meta] broker_account` (else its `[meta] account`), else the
  account its name starts with, compared at its `as_of` (else
  `generated_at`) date; a snapshot newer than the books is compared with
  the latest books, with a note; one with only its `[meta]` (no
  `[[holding]]`) is an account that holds nothing. An
  account with open positions and no holdings at all is listed as
  `UNCHECKED` (the checklist's sanity step is then attention, not done).

A holdings row with a quantity but no symbol, or a symbol with no
quantity, is refused, not skipped. Option rows whose root the file
spells differently (`RCI…` vs taxjson's `RCI.B…`) are matched through
the row's `underlying` field. A FILE may also be the broker's own
positions report (an IB Activity Statement with Open Positions, an RBC
Holdings Export: the same readers as `taxjson opening`); one dated
before the books' last row is compared with the books' positions **on
its date**. `--tolerance` is the quantity tolerance (default 0.0001).

After the quantities, a **COST** section compares the books' cost with
the report's (`--cost-tolerance`, default 1.00 or 0.1%, whichever is
larger): Canada's filing ACB (s.47, pooled, superficial losses added)
against the broker's book value, or a foreign-currency cost against the
account's native-currency books. Each difference gets a reason:
`superficial-loss`, `pooled`, `return-of-capital`, `broker-fx`,
`lot-basis` (US: `wash-sale`, `lot-method`) or `unexplained`. A dividend
row that states its share count (`ON 500 SHS`) while the books held
another number on its record date is listed under **INCOME ON SHARES THE
BOOKS DO NOT HOLD**. Both sections are informational: the exit code
stays the quantity check's, and `--json` adds `cost`,
`cost_differences` and `income_share_mismatches`. The holdings formats
are in [settings.md](settings.md#holdings-toml).

#### taxjson journals

`taxjson journals [--account NAME] [--year YYYY] [--pending] [--json]`

Every broker journal between two listings of one security, per account,
read from the last run (no network): its date, FROM → TO, quantity,
broker, how it was found (a Questrade BRW journal, an RBC journal
transfer with or without its `J~` reference, an IB InterDepot, a move
across brokers with a listing change, a `.tt` or ticker.map line) and its
state:

- **joined**: one security in the books; the `TOBASE` / `JOURNAL` line
  that pools them (your `ticker.map:N` or the run's own join) and the
  `DISTINCT` line that undoes it;
- **suggested**: not joined; the reason, and the `.tt` line `JOURNAL
  <date> FROM TO QTY` or the ticker.map line that settles it;
- **refused**: kept apart by a `DISTINCT` line or another line of your
  ticker.map (a decision made, never pending), or because the legs name
  two companies; the reason, and the undo.

`--year` keeps the journals dated in one year. `--pending` lists only
the pending ones (suggested, or refused with no line of yours behind it)
and exits 1 when there is one (`taxjson checklist` runs it). `--json` is
a stable schema ([settings.md](settings.md#taxjson-journals---json)).

#### taxjson renames

`taxjson renames [ACCOUNT] [--pending] [--json]`

Every ticker change in the books as a dated event: its date, its source
(a broker corporate-action row, IB's one contract id under two symbols,
a `.tt` RENAME or SPLIT line, a legacy ticker.map `RENAME` line), and
per account the position and book cost it carried. Then every trade in
an old ticker after its rename, with how its declaration resolves it;
the look-alike renames the Questrade, RBC and Webull exports show (an
old symbol that stops with shares open, a new one that starts with a
sale, one security name) as **suggested**, each with the `.tt` line
`RENAME <date> OLD NEW` that books it; and the undated ticker.map renames
(source: legacy undated map), with the dated form when a broker row
gives the date. Exit 1 while a late trade is undeclared. `--pending`
lists only the undeclared late trades, the suggestions and the declared
renames that book nothing, and exits 1 when there is one. How a dated
rename is booked: [Renames](#renames) below.

#### taxjson spinoffs

`taxjson spinoffs [ACCOUNT] [--json]`

Every spin-off in the books: parent and new security, ratio, the
election, the value per share used, what was booked (income and the new
shares' cost), the broker's own value when it reported one (the default
uses it when no value is given), and what is held now. In Canada
`taxable_deemed_dividend` is the default: a dividend equal to the new
shares' fair market value, which is also their cost;
`rollover_s_86_1` splits the parent's cost with no income, filed with
the return, for spin-offs on CRA's list (give the CAD cost moved to the
new shares as `--hint allocated_acb_cad=`). In a US project the
elections are `taxable_distribution_301` (§301 income at FMV) and
`tax_free_355` (basis moved per Form 8937, `--hint allocated_acb=`). A
sheltered account's spin-off booked without asking shows
`sheltered_default` ($0 cost for the new shares). It flags a taxable
spin-off booked at $0, a basis-allocating election with no allocated
cost, a missing election or an ignored event; exit 1 when a taxable one
needs attention. The rules:
[tax-rules.md](tax-rules.md#spin-offs-s861-rollover-or-taxable-deemed-dividend).

#### taxjson splits

`taxjson splits [ACCOUNT] [--json]`

Every split, consolidation and rename with the holdings just before and
after. It flags a split recorded twice (two sources, close dates), a
no-op row, a result with a fractional share (expect cash in lieu), and
events that also mention a cash or return-of-capital leg. Exit 1 on a
likely double application.

### Maintainer

The release commands, for the machine taxjson is developed on: `taxjson
help` lists them only on a development checkout (`taxjson help --all`
lists them everywhere), and they run everywhere. They read a git
checkout of taxjson, never a tax project: `-C` does not apply to them.

#### taxjson channels

`taxjson channels [all] [--offline] [--json]`

Where each release channel points (`stable` and `beta` as
`channels.json` on `main` names them, `latest` the newest release tag),
what this machine's production copy (`~/.local/share/taxjson`, or
`TAXJSON_PROD_DIR`) runs and on which channel, and the newest 20
releases (`all`: every one) with date, first CHANGELOG entry and
←stable / beta / latest / this-box marks. It reads the development
checkout (`TAXJSON_DEV_DIR`, or the git checkout this package is an
editable install of), else the production copy's clone, after one `git
fetch` of that clone's own remote; offline (or `TAXJSON_OFFLINE=1`, or
`--offline`) it says so and shows what the clone knows. The same page:
`scripts/channels.sh`.

#### taxjson deploy

`taxjson deploy [vX.Y.Z]`

Development machine only: put the newest release (or the one named) on
this machine's production copy now, through the installer's upgrade
path. The remembered channel is kept; a channel never moves an install
backwards, so the copy stays there until its channel passes it. Refused
(exit 2) where no development checkout is found.

#### taxjson promote

`taxjson promote [vX.Y.Z] [stable|beta]`

Development machine only: point `stable` (the default) or `beta` at a
release, through `scripts/promote.sh`: the tag must exist, the checkout
must be on `main` with a clean `channels.json`, and moving a channel
backwards asks first. It commits "Promote vX.Y.Z to stable" and pushes;
no tag, no rebuild. Without a version: the release this machine's
production copy runs. Refused (exit 2) where no development checkout is
found. The release process: [releasing.md](releasing.md).

### Tools

#### taxjson redact

`taxjson redact [FILE ...] [--out DIR] [--also REGEX] [--no-denylist] [--force] [--check]`

Strips the account numbers, names and contact details it recognises
from broker exports while keeping every row shape, so a statement can be
shared as a parser sample or bug report. It is pattern-based, not a
guarantee: **review the output before sharing**; the report lists the
free-text lines to read.

**With no FILE**, run in a project (or `-C DIR`): it copies the whole
`inputs/` folder to `inputs_redact/` (or `--out DIR`), with the same
folders and every text file (CSVs, `.tt`, `.json` / `.toml` sidecars,
`README.txt`), and redacts the COPY. You get two folders: the originals
untouched, and a redacted inputs tree `taxjson run` can still parse
(brokers are detected by content). Ids get the same placeholder in every
file and file name; a file or folder name holding an account number is
renamed (`55500001.csv` becomes a same-shape placeholder such as
`99900001.csv`, kept unique) <!-- pii-ok: synthetic ids --> and the
old → new map is printed on the console only, never written into the
copy. Binary files (`.xlsx`, `.pdf`, `.zip`) are not copied: each is
named in a warning to handle by hand. Hidden files and Office lock files
are left out; a symlinked folder is not followed, and a file symlink
pointing outside `inputs/` is not copied (named in a warning). An
existing `inputs_redact/` is replaced only with `--force` (built in a
temporary folder beside it, then renamed into place; the old copy is
deleted, not kept) and only when `taxjson redact` made it (its
`.taxjson-redacted` marker); a symlink there is refused. `--check`
reports per file what would be replaced (counts only) and writes
nothing. `taxjson run` never reads `inputs_redact/`, and `taxjson
init`'s `.gitignore` lists it.

In a year folder of one folder of exports for every year
(`inputs_dir`), `inputs_redact/` is written in the year folder as that
year's single-folder project: `inputs/` (the shared exports, a file link
allowed anywhere in the folder holding the years, then the year's own
`inputs/slips/`), `holdings/`, `taxjson.toml` without the folder
settings, and `ticker.map`. The ids are replaced consistently in all of
them (a holdings file's `[meta] account` and name included, and the
configuration's own `account`, `broker_accounts` and `query_id`), as are
e-mail addresses and denylist / `--also` matches in every file and
contact details in the settings files' comments. The copied
`taxjson.toml` must read as TOML before anything is written; `taxjson -C
2025/inputs_redact run` runs it. An id only `taxjson.toml` names is
replaced in the exports of a single-folder project too.

**With FILEs**: it strips the account numbers, names and contact details
it recognises (plus wallet addresses and exchange transaction ids, and
anything on the private denylist) while keeping every row shape, with
same-shape placeholders consistent across every file of one run (so a
redacted Kraken trades + ledgers set still links up). It writes
`NAME.redacted.EXT` beside each file (or into `--out DIR`), never touches
the input, and refuses `.xlsx` and other binary input (export CSV
first). `--check` exits 1 when it finds something, an account id or
denylisted word in the file NAME included.

The private denylist is `~/.config/taxjson/pii-denylist` (or
`$TAXJSON_PII_DENYLIST`); `--no-denylist` ignores it, and `--also REGEX`
adds a pattern (repeatable). The denylist must be UTF-8 text (a BOM is
fine); a UTF-16, non-UTF-8, unreadable or directory denylist, or an
invalid pattern, stops the run (exit 2, nothing written).

#### taxjson help

`taxjson help [COMMAND] [--all]`

The top-level help page, or the help for one command. `--all` also lists
the other country's commands, which a project's help page leaves out.

## Renames, non-cash distributions and capital-gains dividends

How the books handle three things the exports cannot fully say. The
`taxjson.toml` keys are in [settings.md](settings.md), the law in
[tax-rules.md](tax-rules.md).

### Renames

A ticker change is a dated event in the books. On its date the
position, its ACB (US: the basis lots and their holding periods) and the
acquisition dates carry from the old symbol to the new one, and the
superficial-loss / wash-sale rule treats the old symbol before the date
and the new one after it as one security.

The event comes from:

- the broker: IB Corporate Actions, the corp-action stage's `rename`
  election, or IB's one contract id under two symbols. The last is
  booked with a Warning naming the way out (`DISTINCT OLD NEW` in
  ticker.map, or `late=separate`), dated at the new symbol's earliest row
  of any section when that contract id's own rows date it (the old
  symbol's last trade, transfer, corporate action or return of capital on
  an earlier day, read from every IB account of the project, one date in
  all); otherwise nothing is booked from the contract id and an
  ATTENTION line gives the `.tt` line, and nothing is booked from it when
  an IB corporate action books the change itself;
- a `.tt` line `RENAME <date> OLD NEW [late=…]` in any account's folder
  (or the legacy ticker.map line `RENAME OLD NEW YYYY-MM-DD`). It books
  the rename in every account of its kind (securities, or crypto for a
  coin's new ticker) that held OLD before the date and is recorded once:
  declarations of one change in several files are one event, dated the
  earliest; changes apply in date order; declarations that cannot all be
  true (a cycle, one change on two dates) stop the run;
- a `.tt` line `SPLIT <date> <time> OLD NEW 1` (that account only).

Nothing is added where the broker already booked it; a broker rename of
OLD to another symbol stops the run.

After the date the old ticker is NOT automatically the same security: a
trade in it after the rename date is either the broker still booking the
renamed shares under the old ticker, or another company that now uses
the ticker. `taxjson renames` lists such trades, `taxjson run` prints an
ATTENTION line and `run --strict` stops until the dated `.tt` line says
which: `late=fold` books those rows as the new symbol, `late=separate`
keeps them a separate security (the default while undeclared). A line's
choice covers its own account and every account without a line of its
own that held OLD before the date (another account's rows in the old
ticker stay listed until its own line says), and the late rows are the
ones the country's engine takes after the account's own rename row
(Canada: every trade executed on its date; US: trade date and clock
time). A declaration that books nothing (no account of its kind holds
OLD before the date) gets a Warning naming the line, is listed by
`taxjson renames` and stops `run --strict`. An undated rule (`GLOBAL OLD
NEW`, or `RENAME OLD NEW`) still renames every row of OLD at any date.

A warrant or right exercised into shares is not a disposition either:
the warrant's cost goes into the shares (IB and RBC pair the two legs;
tax-logic CA-OPT-09 / US-OPT-06).

### Non-cash distributions

Canadian ETFs declare reinvested (non-cash) capital-gains distributions,
usually each December, that never appear in broker CSVs yet raise your
ACB; some funds publish return-of-capital factors only after year-end.
Put one `[[distributions]]` table per event in `taxjson.toml` (`symbol =
"XYZQ.TO"`, `record_date = 2025-12-29`, `per_share = 0.4297`, negative
for ROC) and `taxjson run` converts them into ACB adjustments for every
taxable account holding the fund on the record date (shares an `OPENING
... cost=unknown` line opens count as held). The symbol is matched
case-insensitively, through your `ticker.map` renames, and along ticker
changes (the adjustment lands on the ticker that held the shares on the
record date).

Only the ACB side is booked: the distribution itself is income for the
year on your T3 / T5 slip, which taxjson does not add to the estimate or
`divs-sum` (the run's NOTE reminds you). The per-share amount is a plain
decimal in the project's base currency (the books it adjusts are
already converted, so convert a US-listed fund's published USD factor at
the record date's rate first; the run's NOTE names the currency) and the
record date a TOML date (`2025-12-29`, or the string `"2025-12-29"`;
anything else stops every command). A `0` is a placeholder and is not
applied; a symbol and date entered in two tables are both applied (they
add) with a warning. `taxjson roc` and `taxjson roc-sum` show the
adjustments. The keys:
[settings.md](settings.md#distributions); the rule:
[tax-rules.md](tax-rules.md#non-cash-and-reinvested-distributions).

### Capital-gains dividends

Canada only. A split-share or mutual-fund corporation may designate part
of a dividend a capital-gains dividend (T5 box 18, line 17400): a
capital gain at 50% inclusion, with no gross-up or dividend tax credit.
No broker export says which payments those are (IB prints "(Ordinary
Dividend)"; RBC and Questrade a plain dividend), so the books carry them
as dividends. Copy them from the slip (or the broker's dividends report,
"T5: Capital Gains") into `taxjson.toml`, one
`[[capital_gains_dividends]]` table each:

- `symbol` is the books' symbol (a bare root such as `"SAMPMI"` covers
  only its Canadian listings: SAMPMI.TO, not SAMPMI.PR.B.TO or
  SAMPMI.US);
- then exactly one of `year` (every dividend of the symbol whose tax date
  is in that year; for a trust distribution dated by its record date, the
  record date's year) or `date` (one payment's pay date, or its record
  date when the books date it by the record date);
- `amount` is `"all"` or the box 18 amount in the dividend's currency
  (the total over the matching payments, shared pro rata);
- an optional `account` restricts the entry to one configured account
  (default: the taxable accounts).

Example: `symbol = "SAMPMI.TO"`, `year = 2025`, `amount = "all"`; or
`symbol = "SAMPMG.TO"`, `date = 2025-09-10`, `amount = 5.50`.
`divs-sum` then lists them under CAPITAL-GAINS DIVIDENDS, apart from the
dividend totals, and the Canadian estimate (`taxjson estimate`, and
`sum` with `--other-income`) moves them from the grossed-up eligible
dividends into capital gains. The ledger and ACB do not change (box 18
does not touch ACB). An entry that matches no dividend, an amount above
the matching dividends, or matches in two currencies stops the view
naming the entry (`[[capital_gains_dividends]] #2`); a malformed or
repeated entry stops every command. A US project refuses the table.

## Standalone analysis tools

Run directly (not `taxjson <sub>`); pass the relevant JSON or cache. All
print to stdout.

| Tool | Purpose |
| --- | --- |
| `taxjson-fees-sum --cache work --year YYYY --to CAD --rates work/to_base.csv` | Trading fees by brokerage with comparison stats (average and median per trade, $/share, % of notional). Add `--json` for machine output. |
| `taxjson-lint-crosslistings --taxable work/margin_base.json --sheltered work/sheltered_base.json --map ticker.map` | Flag cross-listed (`.TO` / `.US`) tickers the wash radar may not consolidate (also run automatically → `reports/crosslistings.rpt`). A listing counts when the books hold its shares or options on it; share positions follow splits; `DISTINCT` pairs are OK. |
| `taxjson-sum-gains work/margin_gains.json` | The per-account gains summary behind `reports/<account>.sum`. |
| `taxjson-explain --country canada --symbol SYMBOL work/<account>_base.json` | The ACB / superficial-loss trace of any disposition (`--country usa` in a US project). |
