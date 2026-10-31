# Command reference

Every `taxjson` command, what it does, and what it writes. The README
lists the commands one line each; this page is the long form. The tax
law behind the figures is in [tax-rules.md](tax-rules.md), every
`taxjson.toml` key and project file in [settings.md](settings.md), and a
first project from start to finish in
[getting-started.md](getting-started.md).

`tjs` is taxjson's command (`tjs sum`, `tjs -C ~/taxes/2025 stats`; `taxjson`
still runs it too, for old scripts). `tjs` with no command prints
the help page; `tjs COMMAND --help` explains one command, and
`tjs --version` prints the installed version. The global `-C DIR`
runs a command in a project that is not the current directory.

The help page and this page group the commands by what they are for.
Inside a project the help page leaves out the other country's commands
(`tjs help --all` lists every one, marked (Canada) / (USA)). Running
another country's command in a project is refused with the reason.

Contents:

- [Everyday workflow (`tjs run`)](#everyday-workflow-taxjson-run)
- [The workflow, step by step (`tjs checklist`)](#the-workflow-step-by-step-taxjson-checklist)
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

## Everyday workflow (`tjs run`)

For a configured project the whole pipeline runs from one command. Set
up a project once with `tjs init --country canada` (or
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

Every other command runs in the year folder (`tjs -C DIR/2025 run`);
`tjs new-year 2026` adds the next one beside it. `tjs init
--single` makes the older layout instead: one folder for one year, with
`taxjson.toml`, `ticker.map`, `inputs/<account>/`, `work/` and
`reports/` all in `DIR` (`tjs -C DIR run`). Add an `[accounts.NAME]`
section and an `inputs/NAME/` folder for any other account (a LIRA, an
IRA). See "One folder of exports for every year" in
[getting-started.md](getting-started.md#one-folder-of-exports-for-every-year).

Every `taxjson.toml` key and every file the pipeline reads is described
in [settings.md](settings.md). The books in `work/` belong to the country
they were built under: after changing `country`, every report refuses
them until `tjs run` rebuilds them (their figures follow the other
country's law).

Then, whenever you add new statements:

```bash
tjs run             # full pipeline, full rebuild: never serves stale data
tjs run --fast      # incremental: stages whose inputs are unchanged are skipped
cat reports/margin.sum  # the per-account report
```

The stages run in-process (no interpreter start-up per stage).
`tjs run` does everything end to end: it parses each broker file,
applies corporate actions, merges and converts to the base currency,
runs the ACB / FIFO gains engine with the superficial-loss / wash-sale
pass across all your accounts, and writes every report to `reports/`.

A new merger or spin-off asks once for its tax election. The answer is
saved to `inputs/<account>/manifest.json`, which you should commit. A
pure ticker rename elects itself, and a sheltered account's merger or
spin-off is booked without asking unless `sheltered_elections = "ask"`.
`tjs elect` reviews or changes an election later.

**Headless runs.** Without a terminal to prompt (or with `tjs run
--no-input`), an unresolved election never hangs or crashes the run. The
affected account is deferred, the pending events (with every option,
description and required hint) are written to
`work/pending_elections.json`, and the run exits **3**. Inspect them with
`tjs elect --pending` (ready-to-copy `--set` lines; `--json` for
machines), resolve each with `tjs elect <account> --set
<event_id>=<election> [--hint KEY=VALUE]`, and run again.

**Exit codes**, the same for every command:

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | failure, or a command's finding (drift, a handoff problem, a lint hit) |
| 2 | usage error, or a named input or output that cannot be read or written (missing, a directory, not UTF-8, not valid JSON) |
| 3 | elections required (`tjs run`) |
| 130 | interrupted (Ctrl-C) |
| 141 | the reader of stdout went away (`tjs trades \| head`) |

Every `taxjson` command and `taxjson-*` tool reports an unreadable input
in one line, never a Python traceback. Only one `tjs run` runs in a
project at a time (`work/.run.lock`); a second one refuses.

What a run writes:

- `reports/<account>.sum`, `reports/<account>_wash.sum`: realized gains
  and the wash-sale / superficial-loss detail.
- `reports/wash_radar_<account>.rpt` and `.json`: the superficial-loss
  "safe to sell at a loss?" advisor (the JSON sidecar carries absolute
  clear dates; `tjs harvest` computes countdowns from it).
- `work/<account>_<broker>_transfers.json`: the custody-transfer
  sidecar, the TRANSFER rows the parse stage keeps out of the books
  (evidence, not tax events); `tjs transfers` reads these.
- `work/<account>_transfer_costs.json`: the acquisitions a taxable
  account's books take for shares that arrived by transfer from outside
  your books at a book value the broker states on the row (Questrade,
  RBC); see "Transfers in" in
  [getting-started.md](getting-started.md#5c-transfers-in).
- `work/<account>_own_moves.json`: the moves between two of your own
  taxable accounts that the run paired from that evidence, as TRANSFER
  legs in both accounts' books. US: the crypto-sends pairing's too, and
  the US engine hands the sender's FIFO lots (basis, purchase dates) to
  the receiver with no sale (tax-logic US-BASIS-05). Canada (shares,
  and coins the crypto-sends pairing moves between two of your crypto
  accounts): the units leave the sender and join the receiver in each
  account's own books, so `list`, `sanity` and `find-missing-history`
  see the receiver hold them; the ACB stays one pool, and the blended
  pass books no leg (tax-logic CA-ACB-OWN-MOVE).
- `reports/crosslistings.rpt`: cross-listed (`.TO` / `.US`) tickers the
  radar may not consolidate.
- `work/compare/prev/`: the reports the run replaced, with the inputs
  they were built from, for `tjs compare` (a full run's: the first
  `--account` run after a full run keeps that run's there, and the full
  run after it leaves them).
- `reports/fees.rpt`: trading fees by brokerage, with comparison stats.
- `reports/ccd.rpt`, `reports/leaps.rpt`: cross-account covered-call and
  long-option views. `leaps.rpt` lists every long option close of any
  tenor; `tjs leaps-sum` is the LEAPS-only figure. Rows with an
  unknown cost (no purchase in your files) are left out and counted, as
  in `tjs ccd-sum`.
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
  equity 100; a plain future is `asset_type = "future"`. When the
  inputs disagree on an option's contract size, `contract_multiplier`
  is omitted and `contract_multiplier_unknown = true` says so (the
  sizes seen are in a comment). A base-currency figure (`base_total_cost`)
  is omitted, with a comment, when the holding's base-currency costs
  are in more than one currency. Units journaled from one listing to
  the other (a TSX line to its US line) carry their cost there on the
  journal's date, `total_cost` restating it in the listing's currency
  at that day's rate. A crypto account's file comes from
  its own books (the run's crypto prices and exchange rates): each coin
  is `asset_type = "crypto"`, its cost in the base currency (`currency`
  and `base_currency` are the same, and so are `total_cost` and
  `base_total_cost`), its quantity and per-unit cost kept to
  significant digits. A Kraken account's coins are written `COIN.KR`,
  as Kraken's own holdings snapshot writes them (an exchange with no
  venue suffix in `data/markets.toml`, such as Coinbase, keeps the bare
  coin). An account folder holding several exchanges' exports writes
  each coin with the suffix of the one exchange whose rows hold it,
  and a coin more than one holds bare; a coin under a millionth of a unit with a cost under 1 (an
  exchange's rounding residue) is left out, named in a comment. A
  holding whose cost the account's own books do not have (units moved
  in from another of your accounts, an opening at an unknown cost)
  carries `cost_unknown = true` and no cost figure, with a comment
  naming the move; `tjs list` gives the cost you file. A full run
  then writes units moved in from another of your accounts at that
  cost, the figure after the passes across accounts (this account's
  share of the ACB pool in Canada, the moved lots' basis in the US),
  marked `cost_source = "blended"`: as `total_cost` and
  `base_total_cost` when the listing is in the base currency, as
  `base_total_cost` alone (the listing's own cost unknown) when not.
  `tjs run --account` runs no such pass and leaves them unknown.

**What to check next.** A full run ends (after `Done.` and the holdings
check) with a short summary of what the books themselves show is still
incomplete. It is silent when there is nothing:

```
==> Before you trust these numbers (docs/getting-started.md, step 5)
Warning: NOT in the totals: 1 sale with no purchase (SAMPA.TO) — `tjs find-missing-history`
Warning: 1 position at a $0 cost (SAMPQ.TO) — `tjs find-missing-history`
Warning: 1 transfer-in kept out with no cost (SAMPK.TO) — `tjs transfers`
Info: 1 account with positions not checked against the broker's holdings — `tjs sanity`
Info: Then `tjs checklist --run` (the next step); each message's detail: `tjs run --details`
```

One line per finding (at most six under the heading), each naming the
command with the detail; `tjs run --details` words each in full
("1 position sold in 2025 with no purchase in your files, not in
missing_history.tt: SAMPA.TO (margin). Those sales are NOT in `tjs
sum`; run `tjs find-missing-history`."). The same counts are written
to `reports/run_summary.json`. A taxable
account's positions that go short (sales with no purchase in the files)
are also named on the console as the run builds that account.

When the year is over, [filing.md](filing.md) is the checklist that
takes a project from "last export dropped in" to a filed and locked
return, with the command that proves each step. `tjs checklist`
runs it, from set-up to year end.

## The workflow, step by step (`tjs checklist`)

`tjs checklist` is the map of the whole job: run it whenever you are not
sure what comes next. Outside a project it prints every step; inside one
`tjs checklist --run` checks each, marks it from what the checks and the
project's files say, and ends with the next step's command. The verdicts
are kept in `work/checklist_results.json`: `tjs checklist` alone shows
them as they are, in a second, with when they were made and what changed
since. Besides that file in `work/` (the run's cache) it writes nothing
to the project but your own marks (`checklist.json`). The flags are
under [`tjs checklist`](#tjs-checklist) below.

`--json` prints the same list as one document, a stable schema
(`schema_version` 3) described in
[settings.md](settings.md#tjs-checklist---json).

## Commands

One section per group of the help page, in its order. The report and
query commands read what the last `tjs run` wrote to `work/` (no
recompute); every command takes `--help`.

A command prints the essentials first ([output-style.md](output-style.md)):
a one- or two-line legend above each table, the figures, then one line
per thing to act on (starting `! `, naming the command with the detail).
A command that leaves something out ends by naming `--details`, which
prints it all: the explanations, notes, caveats and citations, nothing
dropped. `--json` is the same either way.

### Set up

#### tjs checklist

`tjs checklist [--run [-j N] [--quick]] [--section N[,N]] [--all] [--walk] [--done ID[,ID] [--force]] [--undo ID[,ID]] [--note TEXT] [--reset] [--only ID[,ID]] [--show] [--json] [--details]`

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

Inside a project (a `taxjson.toml` here, or `-C DIR`) `--run` checks
each step: a check runs the command that proves it (sanity,
find-missing-history, elect, audit, option-boundary, handoff,
reconcile-slips, form-export, t1135, carryover, fx-cash, amt,
check-filed, git status) or reads the project's files. The slow checks
(each a sub-command reading the books) run `-j N` at a time (default:
the CPUs, at most 4; `-j 1` one after another), and every verdict is
kept in `work/checklist_results.json`. Without `--run` the kept verdicts
are shown as they are, in a second: a line says when they were made and
what moved since (the inputs, a `tjs run`, an upgrade), and the
steps never checked say so; with nothing kept yet (no `--run`, or a
`work/` the run cleared) the steps are listed unchecked. Your marks are
applied as they are now either way. The view is the list, then the
summary. The list: every step, in its section, as done `[x]`, needs
attention `[!]`, to do `[ ]`, blocked `[b]` (usually: run `tjs run`
first), to confirm `[m]`, yours to run and read `[?]`, n/a `[-]` or
done over a finding you accepted `[f]`, with its number, its id and
what the check found; an
open step shows its commands, and the first step to do or needing
attention is marked `[>]` and shows how and why (`--all`, or
`--details`: every step's how, commands and why, the done ones too).
The summary: one row per section with its counts (done, attention,
blocked, to do, to confirm, to read), one `! ` line per step needing
attention or blocked (`! 10 missing-history: … — tjs
find-missing-history`; steps blocked for one reason share a line), the
legend, and the next step last with its command. `--section N` is one
section of it (its number, 1-10, or its name: `--section 4`, `--section
"fill the gaps"`, `--section fill`), `--section 6,7` (or `--section 6
--section 7`) several, in the list's order: shown alone, checked alone
with `--run` (the other sections' verdicts kept), walked alone with
`--walk`; with nothing left in it, the last line says so and names the
next open step elsewhere.

The `format` step reads the map `tjs run` reads, ticker.map with
its tobase.map: a line the run refuses in either needs attention, with
the count, the files and the first line ("1 tobase.map problem(s)
`tjs run` refuses — tobase.map:1: …"; `../tobase.map:N` when the years
share one), and the checks after it do not print the loader's
`Warning: ticker.map problem:` lines again.

Nothing in the project is written but `checklist.json` (the marks;
commit it) and the kept verdicts in `work/` (the run's cache, never
committed). The steps no command can prove are confirmed with `--done
ID`, ID the step's id or its number in the list, several at once with
commas (`--done roc-entered` and `--done 18` are the same mark;
`--done export-coverage,inputs-frozen` marks both; `--note TEXT` stores a note with
it). A mark never hides a
later finding: a step marked done whose detector finds a problem shows
`[!]` (or `[b]` when the detector could not check) with the mark beside
it and keeps the list open, and a `--done ID` is refused while the
step's kept verdict for this tax year is attention or blocked (fix it
and `--run --only ID`, or `--done ID --force`). A question the files
cannot answer (export-coverage's gap, option-boundary's transition
contract, a t5-t3 slip, fx-cash's v2 figure to review) is answered by a
plain `--done`, for the questions asked then; the same steps' other
findings (fx-cash's NOT RELIABLE default ledger, a contract that needs
a T1-ADJ, a blocked check) take `--force` like any other. `--done ID
--force` is the explicit "reviewed, accepted": shown `[f]` with the
finding beside it, counted as done, stored in `checklist.json` as
`"forced": true` (the `--skip` of releases before v0.35 and its
`skipped` marks read as this; `--skip ID` now stops, naming `--done ID
--force`); a `[?]` step is ticked off with a plain
`--done`. `--undo ID` removes one mark,
`--reset` removes them all (deletes `checklist.json`), and `--show`
prints the list after a `--done` / `--undo`.
`--only ID` shows one step (its id or number; with `--run`: checks that
one alone and keeps its verdict), `--walk` visits the open steps one at
a time, checking each, and `--run --quick` skips the slow checks
(audit, sanity, ...), keeping their last verdicts.

form-export is checked against `sum`'s FOR THE RETURN totals and the
taxable realized gain. US projects get the US names (1099-B, Form 8949 /
Schedule D), `n/a` for Canada-only steps and only their own commands
(`--done`, `--undo` and `--only` also take `1099-b` or `1099-da` for the
t5008 step, `1099-div` or `1099-int` for t5-t3); a
project with no taxable account gets `n/a` for the taxable-only steps.
Exit 1 while anything is open (a `[?]` step never keeps it open).
Outside a project it prints the steps as a guide, exit 0. `--json`: a
stable schema ([settings.md](settings.md#tjs-checklist---json)).

#### tjs init

`tjs init --country canada|usa [PATH] [--year YYYY] [--single] [--demo] [--force] [--details]`

Scaffold a new project. By default it makes **one folder of exports for
every year**: `PATH/inputs/<account>/` (every year's broker exports,
shared, each folder with a README.txt saying what to download), a
`.gitignore`, in Canada `PATH/tobase.map` (the interlisted pairs every
year reads), and the year's folder `PATH/YYYY/`, a complete project: its
own `taxjson.toml` (with `inputs_dir = "../inputs"` and `exports_dir =
"../exports"`), its `ticker.map`, and an empty `holdings/` for the
broker's positions snapshots. `--year` picks the year (default: the
current year). `--year` of a year before the existing year folders adds
that year (`new-year` only goes forward): the shared `inputs/` is theirs,
so no folder is made there for a template account it lacks, and a note
names the year whose `[accounts.*]` tables to copy (the new
`taxjson.toml` has the template's). A folder in `inputs/` that no
year's config names gets a note too (add its section or remove it).
`--single` scaffolds one folder for one year instead
(`inputs/` inside it). See "One folder of exports for every year" in
[getting-started.md](getting-started.md#one-folder-of-exports-for-every-year).
`--force` re-templates an existing `taxjson.toml` (kept as `.bak`; `ticker.map` and `inputs/` are left alone); one you made read-only is refused ("cannot write taxjson.toml: the file is read-only", exit 2, nothing written), as every command that rewrites a file you keep refuses it. A folder made for the project (`mkdir -p ~/taxes` before the init) is made owner-only, as the scaffold's own folders are.

The generated `taxjson.toml` lists every key the country's projects
read, documented: the scaffold's values active, every other key
commented out with its default (or an example where it has none), each
key under its description, `[settings]` in groups and the other tables
alphabetical, with one `=` column per table. `local_timezone` is set to
this machine's IANA zone when it can be read (else left commented: the
default zone). The `ticker.map` is laid out in keyword groups, with a
commented-out example in each.

#### tjs years

`tjs years [--json] [--diff YEAR YEAR] [--details]`

The year projects sharing one folder of exports (run it there or in a
year folder): each year filed (its `filed/<year>.json` lock, its date
and totals) or open, its last full run, whether the inputs changed since
then (for a filed year: `tjs run` there rechecks the filed figures
against the lock), whether its last run found it DRIFTED from the lock
(`tjs run` and `check-filed` keep that verdict in
`work/filed_check.state`), whether its `ticker.map` rules and settings
differ from the newest year's (a filed year's are marked `(filed)`:
its own, not a step), and its checklist marks. A year closed with its
inputs frozen stays frozen while its lock is damaged (`lock damaged`)
or deleted (`lock missing`) and its `taxjson.toml` still carries the
freeze note (a year closed as a single folder, then migrated, shows it by
the reports kept in `filed/<year>/reports/`): the line under the table says to restore the lock (from
git or a backup) or run `close-year` again, and `migrate` leaves the
year alone. `--diff A B`: the rules
and keys that differ between two years. `--json`: a stable schema
([settings.md](settings.md#tjs-years---json)).

#### tjs new-year

In a single-folder project (no year folders) both `years` and
`new-year` point at `tjs migrate --to-years`, which makes the
project the first year folder.

`tjs new-year YYYY [--details]`

Start the year's folder beside the others from the previous year's: its
`taxjson.toml` (year set, `prior_year_record` pointed at last year's
lock, last year's `[estimate]` / `[instalments]` and the accounts'
`holdings` commented out for reference — each key once, last year's
value in the place of the template's example — written in the template
layout `tjs format` keeps) and its `ticker.map` copied, and an empty
`holdings/` (or the folder inside the year `holdings_dir` names; a shared
one beside the years is only named). In Canada the new year keeps reading the
`tobase.map` every year shares (a year from before v0.27.1 that has its
own copy passes it on; a filed year's frozen copy is its own by design,
which `tjs years` says as "frozen copy of its own"), and keeps the
option grant-timing cutoff the
previous year applied by default (`option_grant_timing_since`, set to
that year when it was not set). The files are made in a staging folder
and moved into place when complete: a failure leaves no `taxjson.toml`
behind, so the command can be run again. It prints where to save the
year's downloads. When the previous year was closed with its inputs
frozen (`close-year`: it reads its own copy of the exports), the new
year names the shared folders again (`inputs_dir`, `tobase_map`: the
ones the lock recorded, else `../inputs` and `../tobase.map` beside
the year folders). The previous year is the year before: when it has no
folder (`new-year 2027` with 2025 the newest), nothing is created and
the error names `tjs new-year` for the missing year first, since
the older year's carryforwards would skip whatever the missing one used.

#### tjs align

`tjs align --from YEAR [--write [--all]] [--json]`

The `ticker.map` rules and `taxjson.toml` keys of another year's project
that differ from this one's (the year's own keys and the accounts' ids
and `holdings` aside, and `inputs_dir` / `tobase_map` when either year
was closed with its inputs frozen: a filed year keeps reading its own
copy; an array of tables such as `[[distributions]]` is
one key; `accounts (order)` when the accounts are in another order). A
file that cannot be read is an error, never "the same". `--write` brings
the other year's into this project, asked one by one on a terminal
(`--all`: every one), keeping the previous file as `.bak`; the other year
is never written. Every chosen change is made and read back first: the
files are written only when all of them can be (otherwise nothing is),
and the accounts' order is shown, never rewritten. In a filed year
(its lock taken after the year ended) `--write` first warns that it
changes the settings the lock was closed with: the year's run then
names them as frozen inputs that changed.

`--json` prints the differences (`map_only_here`, `map_only_there`,
`tobase_only_here`, `tobase_only_there`, `keys`). With `--write --all`
it prints them once the files are written, with the outcome: `written`,
`files` (each with its `backup`), `map_lines_added`, `map_lines_skipped`
(each line with its `problems`), `keys_set`, `keys_not_changed` (a
lower `requires_taxjson`, the accounts' order), `filed_year` and
`warnings` (also on stderr). `--write --json` without `--all` is
refused: the questions would mix with the JSON.

#### tjs format

`tjs format [--write [--no-backup] | --check] [--details]`

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
the end of its table). In a commented-out table, a commented-out key of
your own with no template example line for it beside it takes the
example's place, aligned (one line per key: uncommenting the table never
gives a key twice). A multi-line value with comments inside is kept
as written; anything it cannot place goes to a "Your notes (kept by tjs
format)" block at the end. Comment lines that are the template's own
text (or an earlier `init`'s) are regenerated.

The parsed configuration before and after must be identical, or nothing
is written. Default: a dry run printing a unified diff. `--write` writes
it (atomically, the file's mode kept, the old file saved as
`taxjson.toml.bak`, or the next free `.bakN`; `--no-backup` skips that).
A taxjson.toml you made read-only is refused, not replaced (`chmod u+w`
it first); so is a read-only ticker.map, tobase.map or slips.toml by
the commands that rewrite them. `--check` exits 1 when the file is not formatted or a migration is
pending (CI). Formatting a formatted file changes nothing. Like every
command it refuses a config the config check refuses, and a project with
old per-purpose files (`tjs migrate` first).

#### tjs format-map

`tjs format-map [--write [--no-backup] | --check]`

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
keyword's group on its own (the examples `tjs init` writes always
do); any other comment block stays under the
group heading it sits in (in a file never formatted: before the rule
that follows it, or at the top when it comes before every rule). Comment
lines are kept byte for byte (trailing blanks dropped); the header and
group text it writes, and an older `init` template's comment paragraphs,
are regenerated. A line `tjs run` cannot use (no keyword, malformed,
a second target for one symbol) is kept exactly as written, with its
comments, in the Unrecognized group at the end, and the console names
the problem of each.

The parsed map before and after (plus the moved lines) must mean the
same and every comment be kept, or nothing is written. Default: a dry
run printing a unified diff, the `.tt` additions and the lines per
group. `--write` writes it (the old file kept as `ticker.map.bak`, or
the next free `.bakN`; `--no-backup` skips that). `--check` exits 1 when
the file is not formatted, a migration is pending or `tjs run`
refuses the map (CI). Formatting a formatted file changes nothing.

#### tjs migrate

`tjs migrate [--dry-run] [--write] [--to-years] [--details]`

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
  those old files is in the project, every other command that reads the
  project stops (exit 2) naming it and this command (`fetch --list` and
  `check-tt FILE.tt` read none: they run). A leftover `tv_exchange.map` (the removed
  TradingView export) is only renamed `tv_exchange.map.migrated`, and
  stops nothing meanwhile (`tjs run` notes it once).
- `--to-years` turns a single-folder project into one folder of exports
  for every year: `inputs/` stays, shared, and the project's own files
  (`taxjson.toml`, `ticker.map`, `missing_history.json`,
  `checklist.json`, `work/`, `reports/`, `filed/`, `inputs/slips/`) move
  into the folder of its year, with `inputs_dir` and `exports_dir` set.
  In Canada `tobase.map` stays at the top, the one file every year reads
  (`tobase_map = "../tobase.map"`). A year closed with its inputs
  frozen (`close-year`) gets its own copy of the account folders (and
  `tobase.map`) in its year folder and keeps reading it; the shared
  ones at the top serve the years to come.
- In a multi-year project made before v0.27.1 (run in the folder holding
  the years, or in a year folder) it makes the years' `tobase.map`
  copies one file beside the year folders that every year reads.
  Identical copies are merged at once (each year's `taxjson.toml` gains
  `tobase_map = "../tobase.map"`, each copy kept as `tobase.map.bak`);
  copies that differ are listed with the lines of yours only an older
  copy has, and written only with `--write` (the newest year's file
  kept).

Every part is planned before the first one writes: an old file it
cannot convert stops it with nothing changed, the shared `tobase.map`
included. A refusal after an earlier part wrote (a `missing_history.json`
entry that needs `tjs run` first) names what was done and kept.

`--dry-run` prints the lines it would append and the moves.

#### tjs fetch

`tjs fetch [ACCOUNT ...] [--list] [--fetcher NAME] [--dry-run] [--json] [--year N] [--days N] [--from YYYY-MM-DD] [--refresh-token TOKEN] [--flex-token TOKEN] [--positions] [--trim-overlap]`

Download broker activity straight into `inputs/` through an installed
fetcher plugin (`--list` names them, in any folder: it reads no project;
with none installed it prints one install line, exit 2). The taxjson-fetch plugin covers the Questrade
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
where `tjs sanity` finds it. An option's contract size is the books'
when they hold the contract, else the one Questrade's symbol lookup
states (unknown for a contract a corporate action adjusted: the shares
it delivers are not its size); with neither, 100 shares is assumed and
a note says so.
`--json` prints a machine-readable
summary. Chain it: `tjs fetch run`.

#### tjs elect

`tjs elect [ACCOUNT] [--redo] [--reset] [--event ID] [--set EVENT_ID=ELECTION] [--hint KEY=VALUE] [--pending] [--json] [--details]`

Lists the corporate-action events in the books (mergers, spin-offs,
reorganisations) with the tax election recorded for each. `--redo`
clears the election(s) and asks again now (an election whose event no
export in the account's folder holds is kept: there is nothing to ask
about); `--reset` clears them so the
next `tjs run` asks; `--event ID` limits either to one event
(`--redo --event ID` asks about that event only; an event that has no
election yet waits for `tjs run`), and takes an election saved under
an older id by either id: the one `tjs elect ACCOUNT` lists or the
one the run gives the event. Two
elect commands at once each keep their change: the manifest is changed
under a lock (`inputs/<account>/.manifest.json.lock`).
`--set EVENT_ID=ELECTION` writes one election without asking (scripts,
CI), with `--hint KEY=VALUE` for the values an election needs (for
example `--hint fmv_per_share=12.5`); `tjs elect ACCOUNT` lists each
event's choices. `--pending` shows what a `--no-input` run left
unresolved (`work/pending_elections.json`; `tjs elect ACCOUNT
--pending`: that account's), with ready-to-copy `--set`
lines (a spin-off's fraction paid in cash is named: "cash in lieu: 0.5
of the 10.5 new shares, paid in cash: …"); `--json` with it prints the
raw document: under `accounts`, each account's `pending` events. An
event's `summary` (`2024-06-14 spinoff: ZZP.TO → ZZS.TO (10.0-for-105.0,
ratio 0.0952381, FMV 0.00 CAD)`) is its identity, which every saved
manifest matches on: its ratio counts the shares delivered, never a
fraction paid in cash, and it stays that way. The fields carry the
facts: `qty_received` and `ratio_new` / `ratio_old` (the shares
delivered, as in the summary), and for a fraction paid in cash
`fraction_in_lieu` (the fraction of a new share, on top of
`qty_received`; 0 when the export does not size it), `cash_in_lieu`
(the cash paid for it, in `cash_in_lieu_currency`) and
`cash_in_lieu_date`. The shares the holding was entitled to are
`qty_received` + `fraction_in_lieu` (the `10.5 for 105` that `tjs
spinoffs` shows). `--pending` takes none of `--set`, `--hint`, `--redo`,
`--reset` or `--event`, and `--set` takes only the project's country's
elections (a pending file written before `country` changed is to be
rebuilt by a `tjs run --no-input`). What elect refuses (a flag, an
account, an event id, an election or a hint it does not take) exits 2
with nothing saved. `tjs elect [ACCOUNT] --json` lists each account's
saved elections and the events the run booked by the sheltered default
(`"saved": false`); a manifest.json that cannot be read is the
account's `"error"`, and the command exits 2.

#### tjs ticker-map

`tjs ticker-map --suggest [--write [--all]] [--json] [--details]`

Every ticker.map line the last `tjs run` suggested, each with its
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
the new map would contradict itself. Run `tjs run` again to apply
them. `--json`: `suggestions` (the evidence lines only), `verify` (the
pairs to verify), `skipped` and `unused`, each record with `kind` and
`certainty` (`evidence`: the run's evidence names the line; `verify`:
yours to decide); under `--write` the plain lines go to stderr.
[settings.md](settings.md#tjs-ticker-map---suggest---json) has the
schema.

#### tjs update-tobase-map

`tjs update-tobase-map [--write [--no-backup]] [--json] [--details]` (Canada)

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
every year (`tjs migrate` there makes the per-year copies of a
project made before v0.27.1 one file). Dry run by default; `--write`
applies it (the previous file kept as `tobase.map.bak`). A US project is
refused (Canada only for now). See [settings.md](settings.md#tobasemap).

### Build the books

#### tjs run

`tjs run [--fast] [--account NAME] [--strict] [--no-input] [--details]`

Run the full pipeline, rebuilding every stage: the recommended everyday
command, so results always reflect the current inputs, config and code.
What it does and writes is in
[Everyday workflow](#everyday-workflow-taxjson-run) above. The run ends
with the broker positions cross-check (`tjs sanity`) when the
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
- A full run ends with the checks a complete book allows, each a
  `Warning:` with its answer: a loss on one listing with the other
  listing bought in the window (the `TOBASE` / `DISTINCT` line to add),
  and a loss on a fund with a fund ticker.map declares `SIMILAR` bought
  in the window (the pair, the dates, the line; nothing denied, the
  decision is yours: docs/settings.md `SIMILAR`).
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

Commands chain in one invocation, each with its own flags: `tjs run
sum`, `tjs run close-year check-filed`. A chained `--json` command's
stdout follows the earlier commands' progress output, so pipe consumers
should run the JSON command on its own. A failing command stops the
chain and its exit code is the chain's. The whole chain is checked
before any of it runs. `-C DIR` (before the first command; `--dir DIR`,
or any abbreviation argparse takes, `--di DIR`, the same) applies to
every command, whatever the folder is called (`tjs -C run run sum`).
A command name starts the next command only when the command before it
cannot take the word: an option's value (`--account sum`), an account
of the project (`tjs events sum` with an `[accounts.sum]`), a folder
(`tjs init sum`) or a symbol (`tjs audit sum`) stays the
command's own. `--` always separates two commands: `tjs events --
sum` runs `events`, then `sum`. With no account of that name, `tjs
events sum` and `tjs fetch run` chain as written.

#### tjs compare

`tjs compare [NAME] [--save NAME] [--delete NAME] [--list] [--details] [--json]`

Compare `reports/` with a snapshot of an earlier run: did a batch of
corrected exports, a new `.tt` line or an upgrade change the result?
`NAME` is `prev` (the default: the run before — every full `tjs
run` keeps the reports it is about to replace in `work/compare/prev/`;
after a `tjs run --account`, which rebuilds one account's reports,
it stays the last full run's, and a `Partial:` line says when
`reports/` or the snapshot mix in such a run, `partial_now` /
`partial_before` in `--json`),
`filed` (the reports as filed, kept by `tjs close-year` in
`filed/<year>/reports/`) or a snapshot saved with `--save NAME`
(`work/compare/NAME/`, kept until `--delete NAME`; `--list` names
them), or a full copy made by [`tjs snapshot`](#tjs-snapshot),
named `<year>.<timestamp>` or given by its folder (`tjs compare
../snapshot/2025.20261030-101000`: any folder holding a `snapshot.json`, a
relative path from your shell's folder, else the project's; its `taxjson` version is named too, `taxjson_before`
in `--json`, when another one is running). Each snapshot records what its reports were built from: the
inputs fingerprint the run recorded (name and SHA-256 of every file it
read) and each taxable account's filing aggregates.

The default view is one line saying whether the reports match, then a
table of the account figures that moved (realized, disallowed, income,
dividend, dispositions; the US term gains too), BEFORE, AFTER and
CHANGE, the accounts that did not move, the report files that differ
(a line that only says when a file was written is ignored) and the
inputs that changed, were added or vanished since the snapshot.
`--details` adds a unified diff of each changed report. `--json` is the
same document for scripts. Exit 1 when a report differs or an account
figure moved (the first line says when the report files match but a
figure moved), 0 when neither did (a changed input that moved nothing is
reported, exit 0), 2 when there is nothing to compare with (no
`reports/`, no run kept yet, no snapshot of that name) or a report
folder or file cannot be read.

#### tjs snapshot

`tjs snapshot [--json]`

Copy the whole project to `snapshot/<year>.<timestamp>/`, a full record of
this result: the reports, the books in `work/`, the inputs, the settings and
maps, the holdings and what was filed. It is laid out as the project is, so
you can compare any file yourself (`diff -r reports
snapshot/2025.20261030-101000/reports`), `tjs compare
2025.20261030-101000` reads it, and `taxjson` runs inside it.

In a project of year folders, `snapshot/` sits beside them. The copy holds
this year under its own name (`2025/`) next to the shared folders and the
shared `tobase.map` its settings name, so `../inputs` still leads to the copy's
inputs, and each other year's `filed/`. Nothing else beside the year folders
is copied. In a single-folder project, `snapshot/` is in the project and the
copy sits at the top of the snapshot's folder.

Not copied: `.git`, `work/compare/`, `inputs_redact/`, `snapshot/` itself. A
link stays a link; one leading out of the copy gets a warning, since what it
leads to is not in the snapshot. A snapshot is never replaced: remove one with
`tjs compare --delete NAME`. `tjs compare --list` names this year's.

Exit 2, leaving nothing behind, when `reports/` is empty or something cannot be
copied, when `snapshot` is a link, or when it is a folder the project reads.
A snapshot holds your real figures and exports. `tjs init`'s `.gitignore`
keeps it out of git, so keep it in a backup; a `.gitignore` written by an
earlier release does not list it.

#### tjs crypto-sends

`tjs crypto-sends [ACCOUNT] [--set ID=DECISION] [--unpair] [--unset ID] [--note TEXT] [--price P] [--write] [--json] [--details]`

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
Kraken ledgers and Coinbase exports (their US dollars and stablecoins:
a stablecoin bought or sold for US dollars moves only its fee or spread;
a send decided `self` stays in the pool, and coins coming back from your
wallet are not bought again; overlapping exports read once),
flagged when likely superficial (USD or stablecoins acquired
within 30 days and still held), with the year's total. Which figure
counts depends on the FX-on-cash ledger: the default ledger v1 does not
read the send, so the total is added by hand to the year's net currency
gain; ledger v2 (`fx_cash_ledger = "v2"`) reads it as US dollars going
out and owns the gain. There each decided stablecoin send shows its
`fx-cash v2` line, `CASHMOVE <date> USD -<units> proceeds=<value>` for a
gift or payment (`kept` for a send to your own wallet), to put in a
`.tt` file of the account; `tjs fx-cash` then counts the gain, against
the pool of all your taxable accounts' US dollars, and the total here is
only a check: never add it too.

- `--set ID=self|gift|payment [--note TEXT] [--price P]` records a
  decision (repeatable; `--price` when the price lookup fails). A `-fee`
  id takes `ID-fee=fee --price P`. A US project refuses `gift`.
- `--unset ID` removes one (the send is undecided again).
- `--set ID=gift|payment --unpair` keeps a send the tool paired with an
  arrival unpaired (that arrival was unrelated). Without `--unpair` a
  saved gift or payment on a paired send is not booked and is warned
  about, and `run --strict` stops.
- `--write` regenerates the account's `crypto_sends.tt` (idempotent; it
  warns when another `.tt` already sells the same coin, date and
  quantity): `inputs/<acct>/` in a single-folder project, the year's
  `work/crypto_sends/<acct>/` when the year folder shares one folder of
  exports (what `tjs init` makes; a run never writes the shared folder).
  The line it prints names the file.

`tjs run` asks at a terminal (self / gift / payment / skip) after
the crypto parse and refreshes the file; headless it prints one note.
While another crypto account's exports have not been parsed yet, a send
to it would look unmatched: `--set` / `--write` refuse until `tjs
run` reads them. Ids carry exchange, local date and time, coin and
quantity, never a txid or address; refs are masked (`LG***`). A
`checklist` step.

#### tjs find-missing-history

`tjs find-missing-history [ACCOUNT] [--year YEAR] [--include-options] [--write-missing-history [--all-history] [--outside-year]] [--write-purchases [FILE]] [--force] [--details]`

Report positions with a missing cost basis (a truncated buy history, or
$0-basis corporate-action shares) that distort a year's gain. Pairs the
accounts' `OPENING ... cost=unknown` lines already cover are listed
apart (COVERED), not as work to do. A line whose position never goes
short without it (its purchase is in the books now) is listed under
STALE: the run still opens its units at an unknown cost, so delete it
(the run and `tjs checklist` say so too). A STALE or REMOVE row names
the file and line it is on (`inputs/margin/hand.tt:1`). `--include-options` checks option
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
  `.tt`. A transfer stating a book value of 0 (a placeholder, never your
  cost) or one your `.tt` purchases cover in part (the line is for the
  other units) is drafted with `COST` to fill in. An existing draft is
  kept unless `--force` (then as `.bak` or the next free `.bakN`).
- Both write flags read the books in `work/`: when the inputs changed
  since the last `tjs run`, they write nothing and say to run it
  first. The report itself (and `tjs sanity`, `tjs transfers`)
  opens with "these books are not the clean result of the current
  inputs" then.

Step 5 of [getting-started.md](getting-started.md#5-find-and-fill-missing-history)
walks through filling the gaps.

#### tjs opening

`tjs opening ACCOUNT FILE [--date D] [--dry-run] [--force] [--details]`

An **opening balance** from a broker's positions report: an Interactive
Brokers Activity Statement (its Open Positions section), an RBC Holdings
Export, or a `[[holding]]` TOML. It writes `inputs/ACCOUNT/opening_<date>.tt`,
one `OPENING <date> <symbol> <qty> <currency> <total-cost> [<lot-date>]`
line per long position with the report's **book cost** (never its market
value), and an option's contract size when it is not 100 (`x10`). A relative FILE is looked for from the shell's folder, then
from the project's. A position with no cost, a short or a futures contract is listed
and skipped, as is one whose symbol, currency or lot date a `.tt` line
cannot carry; a report cell with a control character is refused. The
date is the report's own (`--date` when it has none), written
YYYY-MM-DD as a `.tt` line takes it (`--date 2025-1-1` is refused,
naming 2025-01-01); every line is read back as `tjs run` reads it
before the file is written. `--dry-run` prints
the lines instead; `--force` replaces an existing file, keeping it as
`.bak` (or the next free `.bakN`). A report of several broker accounts
is refused (two ids are two accounts even when they show alike, masked
to their first two characters; a consolidated IB statement is one
position for all its accounts), and so is a symbol another `.tt` of the
account (`.TT` too) already has an OPENING line for.

An OPENING line sets the position and its cost but is **not a
purchase**: no superficial-loss / wash-sale window, no "recent buy"
(CA-OPEN-01 / US-OPEN-01). The snapshot replaces the account's earlier
rows of its symbols: trades, transfers, renames and cost adjustments
dated on or before it are left out of the books with an `ATTENTION:
opening:` line (income rows stay; a ticker reused after its rename into
a snapshot symbol is another holding and keeps its rows); a left-out
sale, short cover or return of capital that may go beyond the cost, of
the tax year, stops the run (CA-OPEN-03). A US project needs one line per lot
with its purchase date and a US-dollar cost (a holdings TOML with
`acquired = "YYYY-MM-DD"` per lot); Canada pools the lines (s.47) and
converts a foreign-currency cost at the snapshot day's Bank of Canada
rate (a broker's CAD book value is used as is).

### Summaries

#### tjs amt

`tjs amt [YEAR] [--other-income AMT] [--other-losses AMT] [--long-term-losses AMT] [--deductions AMT] [--carrying-charges AMT] [--province P] [--json] [--details]` (Canada)

The year's **minimum tax (AMT)** line by line (ITA s.127.5-127.55, form
T691): regular tax; the adjusted taxable income item by item (gains at
100%, other years' net capital losses and carrying charges at 50%,
dividends without the gross-up, deductions in full); the basic
exemption; the 20.5% rate; the credits allowed (BPA credit at 50%;
the special foreign tax credit of s.127.54: the greater of the regular
credit and the lesser of 2/3 of the foreign tax and 20.5% of the foreign
income); whether it binds; the provincial AMT; the
carryover it **creates**; the carryovers **available** by year of origin
with their 7-year limit (ITA s.120.2); what is **recovered** this year
(up to basic federal tax before the foreign tax credit minus the minimum
amount, s.120.2(1)(b); line 40427; only while the claim still lowers tax,
unless the origin is in its last usable year; the rest stays open), with
the province's
share (deducted from basic provincial tax: the provincial form's
carryover line), in Ontario the surtax that share saves, and the foreign
tax credit the lower tax limits, each on a row of
its own; and what carries forward. Every figure is `tjs estimate`'s own, with the same flags.
`YEAR` = an earlier closed year prints what its close-year lock
recorded. Refused in a US project (Form 6251 is not modelled). The
carryover rules:
[tax-rules.md](tax-rules.md#alternative-minimum-tax-and-the-minimum-tax-carryover).

#### tjs div-estimate

`tjs div-estimate [PERIOD] [ACCOUNT] [--months N] [--date YYYY-MM-DD] [--json] [--details]`

Upcoming dividends estimated from each held position's **own dividend
history in the books** (planning numbers only, never a filing figure).
Per payer it reads the payment frequency from its past pay dates (the
grid — weekly, monthly, quarterly, semi-annual or annual — most of the
last payments fall on; a special beside a regular payment or a stretch
the position was not held does not break it; no grid, or a single
payment, is flagged and estimated pro rata from its 12-month amount per
share; a reversal posted later nets against the payment it cancels, and
a row with no share count takes the account's nearest one; a payment
before a stock split is read in today's shares, whatever `--date`; a
dividend on part of the shares beside a payment in lieu on the lent
rest is one payment on all of them; a dividend charged on a short
position — the account short the symbol in the books on its record
date, splits, renames and `OPENING … cost=unknown` lines counted,
whatever share count the row prints — is an expense, not a correction
of earlier income; a dividend paid while the books put the account
short still counts, and a warning, one per account and security,
names `tjs find-missing-history`: the books likely miss a trade),
its latest
and trailing-12-month amount per share, and per position the shares held now (the last `tjs run`'s
native holdings, one row per listing and account); the next pay date and
amount follow the payer's own schedule (latest per-share amount x
shares held), in the currency that account's latest payment was paid in
(payments in another currency, another broker's conversion, are flagged
and left out). The totals are the rest of the tax year and the next N
months (default 12; `--months N`, or a forward `PERIOD` such as `6m` or
`1y`; a window of 0, `0m`, is refused like `--months 0`), split
taxable / sheltered, in the base currency at the latest
rate the books have (`work/to_base.csv`; no network), with withholding
where the latest payment in that account shows a rate (its TAX rows
over its gross, every broker account of a combined account together).
A `--date` and window that reach past 9999-12-31 are refused.
Each projected payment counts in its tax year by the same rule as
`divs-sum`: its pay date, or a Canadian trust's record date (a
December-record distribution paid in January is the December year's);
in a US project a fund listed in `ric_january_dividends` pays its
January dividend in the year before (tax-logic `CA-PLAN-06` /
`US-PLAN-07`). A payer whose expected payment is missing from the books
is flagged (two missing: the projection stops); positions with no
dividend history are left out and counted in one line; crypto accounts
are not read (staking is not a dividend). `ACCOUNT` estimates that
account's positions (the payer's history still comes from every
account); `--date` sets the as-of day (default today; the shares are
always the books' current holdings). `--details` lists each payer's
schedule, the projected payments with their tax dates, and the positions
left out.

#### tjs estimate

`tjs estimate [--other-income AMT] [--other-losses AMT] [--long-term-losses AMT] [--deductions AMT] [--carrying-charges AMT] [--province P] [--verbose] [--json] [--details]`

A marginal tax **estimate** for the year's investment income
(`--details` puts the realized-gains summary table of `tjs sum` in
front of it), computed incrementally:
tax(other income + investment income) − tax(other income), so the
investment income is bracketed on top of what you already earn. Taxable
accounts only. With no flags it uses the `[estimate]` block of
`taxjson.toml`, which `tjs instalments` reads too; without it, other
income is 0 (pure investment-income bracketing) and the loss carryover
is the latest close-year lock's carry-forward, else 0. A lock that is
damaged or closed under the other country's law is refused by name (the
check every lock reader applies), never read as carrying nothing.
`tjs sum --other-income ...` shows the same estimate after the FOR
THE RETURN block.
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
  the dividends of the security they were withheld on, summed across the
  taxable accounts, so excess withholding on one security takes no room
  from another's; a TAX row with no symbol, or one with no foreign
  dividend in the year, up to 15% of its account's cash dividends that no
  TAX row names, said in a note; the treaty 15% is assumed for an account
  whose books carry no TAX rows, but not on a spin-off's deemed dividend,
  which is paid in shares). The Foreign dividends row says which part is
  which:
  "[FTC 75.00, from the TAX rows]", "[FTC 75.00, assumed 15%]" or
  "[FTC 75.00: TAX rows 30.00 + assumed 15% 45.00]"; `--json` carries
  them as `foreign_tax_from_rows` and `foreign_tax_by_assumption`
  (`ftc_basis` names the mix) and the deemed dividends as
  `foreign_div_noncash`. A crypto account's dividends are staking rewards:
  ordinary income, no withholding, no foreign tax credit. Modelled: the
  federal enhanced BPA phase-down (full amount up to the 29% bracket, the
  minimum from the 33% bracket, linear between, on net income), Ontario's
  surtax and Health Premium (up to $900), and the provincial AMT: ON
  24.63% of the federal excess plus ON surtax on it (2024+; 2026 assumed
  until the form is out), BC 33.7% / 34.9% / 40.0% for 2024 / 2025 /
  2026 of the federal additional tax of s.120.2(3) (T691 Part 7 line
  14), AB 35% of the minimum amount over basic federal tax (Part 7 line
  1 minus line 2); in a year AMT binds, the provincial foreign tax
  credit is form T2036's for a minimum-tax year (ON and AB start from
  the special foreign tax credit), and its change from the regular
  year's is a row of the top-up. The federal foreign tax credit is limited as form T2209
  limits it: to basic federal tax x foreign income / net income (net of
  line 25300); the foreign tax it leaves goes to the provincial foreign
  tax credit (form T2036, limited to the provincial tax otherwise payable
  x the same share — Ontario's and Alberta's before their dividend tax
  credit); the Foreign dividends row says "[FTC 53.48 of 150.00, ...]"
  when part of it is not credited, and `--details` names each credit,
  and what neither credit takes and any withholding above 15% of a
  security's dividends (the largest by security; `--json`:
  `foreign_withholding_over_cap_by_security`) as
  possibly deductible on line 23200 (ITA s.20(12), s.20(11)): the
  estimate does not deduct them (a deduction you claim goes in
  `[estimate] deductions`).
  All foreign dividends count as one country's. The estimate is signed: eligible
  dividends at a low bracket can show a negative figure, a saving on the
  tax of the other income. A prior-year minimum tax carryover (ITA
  s.120.2, line 40427) is applied when it is entered or last year's
  close-year lock carries it; with none, a NOTE names the limit one
  could use (basic federal tax less the minimum amount, s.120.2(1)(b)). Not modelled: QC, low-income reductions, non-eligible
  dividends, non-refundable credits other than the basic personal amount
  (CPP/EI, Canada employment, age, pension, donations ...), the OAS
  recovery tax (s.180.2) and AMT adjustments outside the books (the
  s.110(1)(d) stock-option deduction, donated securities), the FX result
  on foreign cash (line 15300, `tjs fx-cash`), T3/T5 slip capital
  gains, and, for `tjs instalments`, CPP/EI payable on
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
  taxable income absorbs (Capital Loss Carryover Worksheet line 4). The
  year's net of §1256 contracts goes 40% to the short-term and 60% to
  the long-term gains, as Form 6781 does (no year-end marking, no loss
  carryback). NIIT
  3.8% above $200k MAGI; no foreign tax credit (the withholding in the
  books is not credited) and no state tax.

Add `--verbose` (`-v`) for the **CALCULATION TRACE**: every bracket
slice, credit and surtax tier, side by side for the base and
with-investments runs, ending in the subtraction that produces the
estimate. It shows exactly where each dollar of investment income landed
in the brackets and what the gross-up and DTC did. `--json` prints the
whole document (accounts, totals, estimate, and instalments when
configured). In Canada its `estimate` carries the year's unused net
capital loss three ways: `losses_unused`, the figure before any
carry-back; `losses_carried_back`, the part `[carryover] claimed`
under the 3 earlier years carries back (T1A, as `tjs carryover`
folds it; 0 when none); and `losses_carried_forward`, `losses_unused`
less it, the "Unused capital losses … (carry forward)" figure.

Rate tables live in `src/taxjson/lib/tax_estimate.py` with a printed
vintage: they need an annual refresh, and the output says so.

Where the carried-in amounts come from when you give no flag (the latest
close-year lock), and what `close-year` and `handoff` do with them: see
[tax-rules.md](tax-rules.md#net-capital-losses-carryforward-and-carry-back)
and
[tax-rules.md](tax-rules.md#alternative-minimum-tax-and-the-minimum-tax-carryover).

#### tjs fx-cash

`tjs fx-cash [--ledger v1|v2] [--events] [--cash-events] [--json] [--details]`

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
lists what it read: a broker's move a `.tt` `CASHMOVE` declares is
listed once, with the line in a DECLARED BY column; none read says
so). Its table gives each currency's UNITS IN and UNITS OUT (units of
that currency; `acquired` / `disposed` in `--json`) and the gains in the
base currency. `--events` gives the per-disposal detail, `--json`
the machine form. It changes NO other number. Set `fx_cash_gains = true`
under `[settings]` to also print it (and write `reports/fx_cash.rpt`) at
the end of every run; off by default.

#### tjs instalments

`tjs instalments [--json] [--details]` (Canada)

Canadian tax instalments: what each of the four dates (Mar / Jun / Sep /
Dec 15) calls for under your chosen basis, what you have paid, and the
**offset interest** plus **s.163.1 penalty** that follow from any gap.
The current-year basis is driven by `tjs estimate` itself (AMT
included). Interest uses CRA's published quarterly rates (built in;
`prescribed_rate(s)` overrides), credit interest runs from the later of
the payment date and January 1, and net interest of $25 or less is not
charged. CRA charges instalment interest only if it sent you a reminder
for the year, which the report says. Configure `[instalments]` in
`taxjson.toml` ([settings.md](settings.md#instalments)); `--json` for
machines.

#### tjs option-yield

`tjs option-yield [UNDERLYING...] [PERIOD] [ACCOUNT] [--json]`

How your option trades on an underlying did over a window (planning
numbers only, never a filing figure): `tjs option-yield QQQ 1y`
for the last year's QQQ options, `tjs option-yield 2025 margin`
for every underlying traded in that account in 2025. The positionals
are underlyings (`QQQ` matches QQQ on any market, `QQQ.US` that
listing, `SYN.B` the class share SYN.B.US; a class share's option root,
`SYNB` for SYN.B.TO, is that share line when it names exactly one),
a window (`30d`, `6m`, `1y`, `mtd`, `ytd`, `all`, a tax year;
default: the tax year) and an account (default: all), in any order.
Three sections: the contracts closed in the window, each with how it
closed (expired, bought back, assigned, sold, exercised; transferred
out: no money and no P/L here), its days
open, premium, the money of the close and its P/L; the contracts open
at the window's end (one past its expiry with no expiry or assignment
row in the books is marked); then the summary per underlying — the
contracts written and bought, the money received and paid, the
realized P/L, the win rate (the contracts closed at a profit over the
contracts closed in the window), the shares of the underlying held
now and their cost, and the yield on that cost, annualized — with a
row per account and side where there are several, and the totals per
currency. The
contracts are matched per account and series from the books, first in
first out: a sale with no long position writes, a purchase with no
short position buys; the opposite trade closes the oldest open
contract; a close at price 0 with no money but its fees is an expiry
(the fee comes off its P/L); an `ASSIGN` row is an assignment
(written) or an exercise (bought). A contract size (`x1000`) one row
of a series declares sizes the rows that state none. In a Canadian
project an option split or rename one blended taxable account reports
moves the series' contracts of each of them, as the gains do. The yield is
the window's realized P/L over the cost of the underlying's shares
held now in the accounts read (the books' holdings: a covered-call
writer's capital), annualized by 365 / the window's days; where no
shares are held (naked or cash-secured contracts) it is over the
average strike notional open in the window (strike x contract size x
contracts, each weighted by its days open inside the window) and is
marked `*`. Shares whose cost is in another currency than the options'
(a USD call on shares bought in CAD) give no yield: the cost is never
converted here, so the row's yield is `-` and `--json` names those
shares and their cost under `shares_other_currency` (`yield_basis`
`other_currency`). Shares held partly at an unknown cost (an `OPENING
… cost=unknown` line) give no yield either (share cost `?`,
`shares_cost_unknown`). A tax year not yet begun is an empty window.
A contract opened before the data (a missing-history
opening) has no premium: its money is unknown and the row is flagged.
So has one a trade the broker codes closing (IB code C) closes with
nothing held to close: that sale closes a long bought before the data
(that buy a write bought back), never a new write or long (`--json`:
`opened` ""); `stats` and `win-rates` count it as unknown-cost.
A `TRANSFER` row moves contracts out of the account (the oldest open
contracts of the series, "transferred") or in (a contract whose
premium is in the books it came from: unknown here, and said so); a
move out and back within days that nets to zero is a no-op, as the
gains run reads it.
The tax treatment of each premium (ITA s.49 timing, §1234) is in the
gains, not here; `tjs option-boundary` is the year-end check of
written options.

#### tjs plot-tax

`tjs plot-tax [--other-income AMT] [--other-losses AMT] [--long-term-losses AMT] [--deductions AMT] [--carrying-charges AMT] [--province P] [--json]`

An ASCII chart of the tax the year's investment income generated month
by month, against the year's estimate (planning numbers only, never a
filing figure). The estimator `tjs estimate` uses runs once per
month on the taxable accounts' gains and income dated up to that
month's end, on the same inputs (`[estimate]` other income and the
flags, the carry-forwards, deductions, province, and in Canada the
foreign tax withheld: the books' `TAX` rows dated by the month's end,
the 15% assumption only for an account whose books record none in the
year); a month's tax is
that run less the month before's, so a loss month is negative and a
month that crosses a bracket costs more than the same income earlier
in the year. The chart is one bar per month (`#`) with the year's
estimate spread evenly over the months as a dashed level; the table
under it has each month's investment income, tax and the running
total, then the year's figures (the estimated tax on the year's
investment income, and with the minimum tax where it binds). The
running total through December is the year's estimate on the same
inputs; what `tjs estimate` dates to the year rather than to a
month (a T5 box 18 capital-gains dividend) is reported as one line;
in a US project each month splits the §1256 P/L it holds 40% short-term
/ 60% long-term, as the year's estimate does. Rows are dated as the gains files place them
(settlement date on a settle-basis project). `--json` emits the
document. Tax-logic `CA-PLAN-08` / `US-PLAN-09`.

#### tjs stats

`tjs stats [YEAR] [ACCOUNT] [--all-history] [--json] [--details]`

Win/lose statistics on closed trades, one row per asset class (long
shares and ETFs, short shares, long options, written options, futures,
crypto) plus a total: trades, wins, losses, win rate, net P/L, average
and largest win and loss, profit factor (gross wins / gross losses).
Economic P/L in the base currency **before** any superficial-loss /
wash-sale denial (the denied total is its own line): a denied loss
counts once, in the loss sale's P/L, and the replacement's sale counts
without the denied loss the rule added to its cost (`raw_gain` plus the
row's `deferred_loss_recovered`); taxable accounts unless a sheltered one
is named. `YEAR` defaults to the project's year
and takes any window token too (`30d`, `ytd`, `all` ...);
`--all-history` covers every closed trade the books hold. A window outside the tax year (`all`, `2y`, another year) is read from the full-history books, built on demand from the same consolidated base-currency book the tax year's gains come from (`work/<account>_gains_all.json`, across the taxable accounts as the tax year's; superficial-loss / wash-sale denials among the taxable accounts only), said in an `Info:` line.
One trade per
closing disposition (a US sale that takes several FIFO lots is one trade,
its lots' P/L added; one with any lot of unknown cost is left out whole
and counted as unknown-cost, never a trade of its known lots; the denied
and unknown-cost counts are dispositions too); a written option is one trade from write to close
whatever `option_premium_timing` says (premium minus the buy-back, or
the premium kept at expiry or assignment; an assigned option's premium
is taken back out of the shares the tax rules fold it into: in Canada an
assigned put's out of every later sale of the pooled shares, in any
account of the pool, by its part of the pool, as the average cost holds
it). A move in kind out of (or into) an account, such as a registered
account's withdrawal in kind (`transfers = true`), is no trade: its
P/L is a line of its own under the table (`--json`: `moved_in_kind`).
A view, never a filing number.

#### tjs sum

`tjs sum [ACCOUNT] [--other-income AMT] [--other-losses AMT] [--long-term-losses AMT] [--deductions AMT] [--carrying-charges AMT] [--province P] [--verbose] [--json] [--details]`

Cross-account realized-gains tables for the tax year in the base
currency, one row per account (NON-OPT / OPTION / REALIZED / DIVIDEND /
PIL / FEES / TOTAL). It takes no period by design: it reports the tax
year's filing basis from the built books (the windowed views are
`winners`, `ccd-sum` and `leaps-sum`). When `taxjson.toml` declares both
account types, the summary prints a **TAXABLE ACCOUNTS** table and a
**SHELTERED ACCOUNTS** table (each with its own SUBTOTAL) followed by the
**ALL ACCOUNTS** grand total. Every account must declare its `type`:
`tjs run` refuses a config with an untyped account (it would
otherwise drop out of the return); gains files whose account is no
longer in `taxjson.toml` are shown in their own UNTYPED table.
Single-type projects and `tjs sum <account>` keep the one-table
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
FOR THE RETURN block and `tjs form-export`. A crypto account's
DIVIDEND column is staking rewards. `--json` carries a per-account
`type` and a `subtotals` object alongside `totals`.

```
$ tjs sum
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
`tjs sum --details` says so under the block, and `--json` carries `engine_gain_unrounded` and
`engine_denied_unrounded`; `--json` adds the per-account split. The
taxable accounts' SUBTOTAL (TOTAL in the one-table layout) adds the
account rows instead, each rounded once: when it and the RETURN row's
gain differ, the block's legend says so in one line ("The SUBTOTAL adds
the account rows, RETURN each sale's cents"). Before
the tax year has ended the figures are year-to-date, and the block says
so.

`sum` warns, one `! ` line each after the block (each warning in full
with `--details`), about what its totals leave out: dispositions with an
unknown cost that missing history (`OPENING ... cost=unknown` lines)
routed to manual reporting, and, whatever the broker, the tax year's
sales with no purchase in your files that no `OPENING ... cost=unknown`
line covers (the books hold them as an open short, so their gain is in
no total; `tjs find-missing-history` lists them). `--json` carries
the counts as `unknown_cost_routed` / `unknown_cost_included` (the older
names `tainted_routed` / `tainted_included` are kept, same values:
"tainted" is the engine's word for an unknown cost) and the uncovered
sales as `no_purchase_uncovered`.

The estimate flags (`--other-income`, `--other-losses` ...) add the tax
estimate block of [`tjs estimate`](#tjs-estimate) under the
tables; `--verbose` adds its calculation trace.

`--json` carries a `readiness` record: `complete` (true only when nothing below is open) and `issues`, each with its `kind`, what it is and the command that resolves it — `run-state` (the books are not the clean result of the current inputs), `unknown-cost` (sales left out of the totals), `no-purchase` (sales in no total), `conservation` (a cost or quantity the engine's conservation check found the books lost track of) `in-kind-not-booked` (an in-kind move to or from a registered account the run could not value: its sale or purchase is not in the books) `unconsumed-premium` (an option assignment whose premium found no stock leg to fold into: it is in no gain) and `compensation-not-settled` (US: dividend compensation paid on a short still open at the last row and inside its 45 days, so this year's income moves if it is covered in time). The figures themselves are the same either way. A conservation problem and an in-kind move not booked are also `! ` lines under the
tables (`--details`: the `INKIND` line that values the move).

#### tjs win-rates

`tjs win-rates [SYMBOL...] [PERIOD] [ACCOUNT] [--by symbol|account|month|class] [--all-history] [--json]`

The win rate of every closed trade in the window, one row per share
listing (an option counts under its underlying, so `tjs win-rates
QQQ` is the QQQ shares and the QQQ options together) plus a total:
trades, wins, losses, win rate, net P/L, average win and loss, profit
factor, and the asset classes the row's trades fall in. `--by account`,
`--by month` (the month of the close) and `--by class` group the same
trades differently. The positionals are symbols (`QQQ` matches QQQ on
any market, `QQQ.US` that listing, `SYN.B` the class share SYN.B.US;
a digit-leading listing such as `1234.HK` is a symbol), a window
(`30d`, `6m`, `1y`, `mtd`, `ytd`, `all`, a tax year; default: the tax
year) and an account (default: every taxable account; a sheltered
account only when named), in any order; an account whose name is also
a period (`2025`) is that account beside a period (`all 2025`) or
`--all-history`, and alone the period, with a warning.
`--all-history` covers every closed trade the books hold. A window outside the tax year (`all`, `2y`, another year) is read from the full-history books, built on demand from the same consolidated base-currency book the tax year's gains come from (`work/<account>_gains_all.json`, across the taxable accounts as the tax year's; superficial-loss / wash-sale denials among the taxable accounts only), said in an `Info:` line.
The trades, their economic P/L (in the base currency,
**before** any superficial-loss / wash-sale denial; the denied total is
its own line; with symbols given, it and the unknown-cost count cover
those symbols only) and the counting rules are `tjs stats`'s: one
trade per closing disposition, a written option one trade from write to
close. A break-even trade is neither a win nor a loss. A view, never a
filing number.

### Positions

#### tjs list

`tjs list [ACCOUNT] [YYYY-MM-DD] [--date YYYY-MM-DD] [--negative] [--json] [--details]`

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
2026-04-28`, `list 2026-04-28`) shows positions AS OF that date, on the
project's date basis: the settlement date unless `tax_date = "trade"`,
so a sale traded Dec 31 that settles in January is still held at Dec 31,
as in the gains year and `t1135`. The figures are the engine's own pools
on that day, from the same pass plain `list` reads (the run writes them
per day with the gains, `work/.<file>.timeline.json`): in Canada each
account's share of the s.47 ACB across the taxable accounts, in the US
the account's own FIFO lots, with the deferred losses, the
missing-history lines and the units a move between your own accounts
brought, at their cost. A project last run by an older taxjson has no
such file: `tjs run` first.

A short that is a purchase missing from your files (a sale with nothing
to close: no broker short-sale marker, or a sale the broker coded
closing, IB code `C`, or any short in a registered account) is marked
`missing history?` in a NOTE column (`"missing_history_suspect": true`
in `--json`): the same pairs `find-missing-history` reports and `tjs
run` warns about. `list --negative` shows only negative-quantity
positions, in two sections, **Short positions** (a short the broker
marks, an option or a future sold to open) and **Missing history**, and
ends with the command that records the second kind as openings
(`tjs find-missing-history --write-missing-history --all-history`;
`--outside-year` for only those that do not touch the tax year).

```
$ tjs list
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

#### tjs shares

`tjs shares [--options] [--taxable | --sheltered] [--sort symbol|qty] [--json]`

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
the config tax year. A look-back or to-date window ends today: a row
dated later (a trade entered ahead) is in `all` and its year, not in
`30d`, `mtd` or `ytd`. Every view takes `--json`.

Money is shown to 2 decimals; quantities and per-share prices keep full
precision. A line ends with the `.tt` tokens `taxjson-convert-tt` writes
for the row: a contract size (`x1000`), a warrant exercise's
`exercise_of=`, and an income row's facts (`record=`, `label=`, `ex=`,
`dealer=`, `issuer=`, an ADJUST's `type=`), so a copied line re-imports
with the same tax year and treatment. Rows with a missing or
unparseable date are excluded with a warning.

#### tjs cash

`tjs cash [PERIOD] [ACCOUNT] [--json]`

The cash events of the taxable accounts over the window: the
conversions, deposits and withdrawals the exports carry (Interactive
Brokers, RBC Direct, Questrade, Kraken, Coinbase) and the project's
`.tt` cash lines — the rows `tjs fx-cash --ledger v2` reads. Each is
printed as the `.tt` line that declares it, in the forms of
[settings.md](settings.md#tt-files) (`FXCONV`, `CASHMOVE`, `CASHOPEN`;
a fee a cash event charged as a `#FEE` comment line, cash one brought in
— a stock-lending receipt, a rebate — as a `#CREDIT` one, the stablecoin
side of a Coinbase Convert as a `#TRADE` one), oldest to latest,
the columns lined up, the broker's description (or the `.tt` file and
line) after `#`:

```
CASHOPEN 2025-01-01 USD    0.00 0.00
CASHMOVE 2025-01-15 USD +500.00 ?             # Wire in
FXCONV   2025-03-05 USD  400.00 CAD 542.00    # Forex USD.CAD
CASHMOVE 2025-09-30 USD -250.00 kept          # Withdrawal; declared in inputs/margin/cash.tt:3

TOTAL DEPOSITS:    500.00 USD
TOTAL WITHDRAWALS: 250.00 USD
TOTAL CONVERTED:   400.00 USD->CAD
```

A broker's move that no `.tt` line declares shows `?` where its `own`
/ `kept` / `spot` / `cost=` / `proceeds=` goes: copy the line into a
`.tt` file of the account and put the declaration there (the ledger then
reads the move as declared). A move in the base currency (a deposit of
Canadian dollars in a CAD project) is no foreign cash and needs no
declaration: it is listed as a `#CASHMOVE` comment line, counted in the
totals. A `.tt` line that matches a broker's move
is shown once, as that move with its declaration; one that matches none
is a move of its own. Statement balances (`CASHBAL`) are not listed: the
ledger reconciles them. A line of a book other than the one a new
`.tt` file of its account folder reads into (a book kept by hand with
`at=`, a file's `CASHBOOK` book, one of several broker books) ends with
its `at=<book>`, so a copy stays in that book. The totals are per
currency (deposits, withdrawals, fees paid, other credits) and per
currency pair (converted). It needs a run
(the account books name each file's broker account) and reads nothing
for a sheltered account. `--json`: `rows` (each event with its `tt`
line), `totals` (`deposits`, `withdrawals`, `converted`, `fees`,
`credits`), `problems`.

#### tjs dil

`tjs dil [PERIOD] [ACCOUNT] [--json]`

Like `events`, but only payments in lieu (DIVIDEND_IN_LIEU rows): what
you received while your shares were lent out or short over the ex-date,
in their own currency.

#### tjs divs

`tjs divs [PERIOD] [ACCOUNT] [--json]`

`events` filtered to `DIVIDEND` + `DIVIDEND_IN_LIEU`; the footer shows
the dividend total per currency. A row's fields are the action, date,
time, symbol, shares, currency, per-share amount and gross amount; a
row whose broker books the withholding on the payment itself (IB)
ends with one more field, the net amount after tax, so the line
re-imports as gross and net. A broker that posts the tax as its own
row (Questrade, RBC) shows the gross alone, and `00:00:00` where the
export carries no time.

#### tjs events

`tjs events [PERIOD] [ACCOUNT] [--json]`

All transactions over the window, in native (pre-base-conversion)
currency, `.tt` text format, oldest to latest, the columns lined up
with a single-space gap (the padding is whitespace, so a line copies
into a `.tt` file as it is). Prints per-currency `TOTAL BUY / SELL /
DIVIDEND` footers. With an account named, the rows carry that account's
settlement dates; the merged view with no account carries trade dates.

```
$ tjs events 5d margin
BUYSELL  2026-06-26 14:23:00 SAMPLE.US  10 USD 50.00 501.00 1.00
DIVIDEND 2026-06-30 09:30:00 ABC.US    100 USD  0.25  25.00

TOTAL BUY:      501.00 USD
TOTAL DIVIDEND: 25.00 USD
```

#### tjs fees

`tjs fees [PERIOD] [ACCOUNT] [--json]`

One row per fee-bearing trade in the window (account, date, symbol,
action, fee) plus a per-currency `TOTAL FEES`, the sum of every fee
before rounding. A fee under half a cent is in the total but not
listed (the count line says how many; `--json`: `unlisted_small_fees`).
This view counts
standalone `FEE` rows (monthly or market-data charges) that `fees-sum`,
a per-TRADE cost report, deliberately excludes: the two totals differ by
those. It is not the line 22100 figure: trade commissions are already in
the ACB and proceeds, and margin interest (INTEREST rows) is not listed
here.

#### tjs gains

`tjs gains [PERIOD] [ACCOUNT] [--options] [--equities] [--futures] [--puts] [--calls] [--json]`

Realized gains in **native** terms (before TOBASE consolidation and
before conversion to the base currency), one row per disposition (date,
symbol, qty, currency, proceeds, cost, gain, days), with a per-currency
`TOTAL GAIN`. Crypto has no native gains file and is skipped (a
project whose only books are crypto: the note says so, exit 0).

`gains` and `trades` take **instrument-class filters**: `--options`,
`--equities`, `--futures`, `--puts`, `--calls`. They combine with OR
(`--equities --calls` = equities *and* calls), and no flag means all
instruments. A futures option (e.g. `F:SAMPLX251220P00053000.US`) is
both a future and an option, so it matches `--futures` **and**
`--options` / `--puts`.

#### tjs leaps

`tjs leaps [PERIOD] [ACCOUNT] [--json]`

Closed LEAPS positions: one row per disposition (date, contract, qty,
proceeds, cost, gain, days held) plus the total realized gain. Partial
closes appear as realized; still-open contracts are absent (no
mark-to-market). A window outside the tax year (`all`, `2y`, another year) is read from the full-history books, built on demand from the same consolidated base-currency book the tax year's gains come from (`work/<account>_gains_all.json`, across the taxable accounts as the tax year's; superficial-loss / wash-sale denials among the taxable accounts only), said in an `Info:` line.

A **LEAPS** position here is a **long option buy placed more than
`[settings] leaps_months` calendar months before expiry** (default 9,
the market convention; calls and puts alike). The setting changes only
these views, never a tax figure. Contracts qualify over your FULL
history, so an exit inside the viewing window shows up even when the
qualifying buy predates it; short premium and near-dated buys never
qualify. `leaps` and `leaps-sum` report the ENGINE's numbers
(lot-matched, superficial-loss / wash-adjusted, base currency), read
from the wash-adjusted gains files.

#### tjs roc

`tjs roc [PERIOD] [ACCOUNT] [--json]`

Like `events`, but only ADJUST rows: return-of-capital ACB / basis
reductions as the broker classified them, manual `.tt` adjustments, and
the `[[distributions]]` adjustments `run` books.

#### tjs trades

`tjs trades [PERIOD] [ACCOUNT] [--options] [--equities] [--futures] [--puts] [--calls] [--json]`

`events` filtered to `BUYSELL` + `ASSIGN`; the footer shows buy / sell
totals per currency. The instrument-class filters are described under
[`tjs gains`](#tjs-gains).

#### tjs transfers

`tjs transfers [ACCOUNT] [--json] [--details]`

The custody-transfer **evidence** view: depot flips, listing journals,
broker migrations, and crypto withdrawals and sends (a send that arrived
in another of your crypto accounts is a self-custody move; the rest are
gift or payment candidates, see `tjs crypto-sends`; in a US project
only a payment is a sale). These are the TRANSFER rows the books
deliberately exclude (cost comes from the buy and sell history). It
reads the parse-stage sidecars (`work/<acct>_<broker>_transfers.json`)
plus the in-book TRANSFERs of `transfers = true` accounts, with the
transfer's kind in TYPE: the broker's where it states one (IB's ACATS /
InterDepot / Internal, a crypto exchange's withdrawal), else
`transfer-in` / `transfer-out` (`journal` for a `.tt` JOURNAL line's
legs; `--json` keeps the row's own text in `type`). IN_BOOKS says
what the books did with a taxable account's transfer-in (`--details`
explains each value): `own_move` when a transfer-out of yours cancels
it — within 10 days, or a transfer-out of the same stock and quantity
in another of your taxable accounts 11 to 45 days before it when no
other transfer could pair with either (said by `tjs run` as an
`Own-account move` Info line with both dates; when the two rows are not
one move, a `.tt` `NOTOWN` line in the receiving account's folder
undoes that pairing: docs/settings.md) — else `book_value`,
`NO_COST`, `.tt_covers`, `PARTLY_covered`, `missing_history` or an
in-kind move (docs/getting-started.md, step 5a). `UNCLASSIFIED`: the
classification could not be made (a damaged file in `work/`, named by a
warning; `tjs run` rebuilds it) — never read as your own move. A SYMBOL CODES
section lists the Questrade internal codes the run booked under an
inferred ticker, with the evidence, and those it could not identify.
`--json` for machines (`symbol_codes`).

### Totals by type

The roll-up summaries take an **optional** `PERIOD` (a window, `mtd` /
`ytd`, `all`, a year, or `tax_year`; default: the config tax year; a
window, `mtd` and `ytd` end today, as in the row listings) and an
optional `ACCOUNT`; a lone non-period argument is read as the account
(`tjs divs-sum margin`). Every summary TOTAL is the sum of its
printed (cent-rounded) rows. A tax-year window (the default, `tax_year`,
`2025`) follows the project's `tax_date` in `winners`, `losers`, `gains`,
`ccd-sum`, `leaps` and `leaps-sum`: on the settle basis a Dec-31 trade
that settles in January belongs to the next year, as in `sum`. These
views refuse when `work/` was built for another year than `[settings]
year`.

#### tjs ccd-sum

`tjs ccd-sum [PERIOD] [ACCOUNT] [--json]`

Covered-call (short call) realized-gain summary per underlying over a
window (default: the tax year): the windowed twin of
`reports/ccd.rpt`. Covers every account; the total is split into TAXABLE
and SHELTERED parts when registered accounts contribute. A window outside the tax year (`all`, `2y`, another year) is read from the full-history books, built on demand from the same consolidated base-currency book the tax year's gains come from (`work/<account>_gains_all.json`, across the taxable accounts as the tax year's; superficial-loss / wash-sale denials among the taxable accounts only), said in an `Info:` line.

#### tjs dil-sum

`tjs dil-sum [PERIOD] [ACCOUNT] [--json]`

Payment-in-lieu total per symbol (DIVIDEND_IN_LIEU rows only), with each
row's treatment: ordinary income (no dividend gross-up or credit, no
qualified rate), except, in a Canada project, a Canadian dealer's
payment on a Canadian issuer's share, which ITA s.260 deems a taxable
dividend (on the dealer's T5 box 24; counted in `divs-sum` and the
estimate's eligible dividends). Registered accounts are split out as in
`divs-sum`. In a US project, dividend compensation paid on a short sale
closed within 45 days is shown as `capitalized(§263(h))` and left out of
the total, as in `sum`: it is the cover's cost (`--json`:
`totals_capitalized`). `dil` keeps its row and moves the total the same
way (`--json`: `totals.capitalized_263h`).

#### tjs divs-sum

`tjs divs-sum [PERIOD] [ACCOUNT] [--json]`

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

`winners` and `losers` print the same taxable / sheltered split under their ranking,
and `dil-sum` / `roc-sum` / `trades-sum` separate registered accounts
the same way (the T3 box 42 figure is `roc-sum`'s TAXABLE line; T5008
proceeds are `trades-sum`'s taxable "sold" figure).

#### tjs staking

`tjs staking [PERIOD] [ACCOUNT] [--broker NAME] [--symbol COIN] [--details] [--json]`

`staking` shows one row per crypto reward. [`staking-sum`](#tjs-staking-sum) groups the rewards
by exchange, coin and account tax status. Both default to the configured tax
year and crypto accounts, and use the final base-currency books from the
last run. A lone account name selects that account. For example:

```bash
tjs staking-sum 2025 crypto
tjs staking 2025 crypto --broker kraken --symbol SOL --details
tjs staking-sum 2025 crypto --json
```

The main columns explain the reward's value before commission, the exchange's
commission, and the net income recorded in the books. Commissions shown here
are already included in net income; they are not an additional deduction.
The acquisition entry is part of the same reward, never a second income event.
Other reward types and rewards whose source type is unknown are identified
separately, so a promotional reward is not silently called staking.
The totals cover booked reward events. Kraken rewards fully consumed by their
fee, or omitted under the existing dust rules, have no income entry and are
not in these totals; the parser's non-event diagnostics identify those cases.

`--details` shows the gross, withheld and received coin quantities with their
units, native valuation and exchange rate when saved, the price source and
dates, source file and row/transaction ID, and the acquisition cost separately
from income. Fees paid in another asset can leave the reward's acquired
quantity and cost higher than its net income. The report explains the actual
book entries; it does not recalculate them or fetch today's prices.

Unknown commissions are shown as unknown, not zero. Summaries identify
incomplete evidence and keep known commission subtotals distinct from complete
totals. Old books still show their recorded income; rebuild with `tjs run`
after upgrading to retain the original exchange's commission evidence. A
source export that does not state a commission remains unknown after a rebuild.
Coin quantities are never added across different assets. JSON carries the
full evidence and coverage information for either command.

`tjs tax-logic --ids` states the assumptions behind the current calculation:
CA-INC-STAKING for Canada and US-INC-STAKING for the US. The report's net-reward
presentation is a disclosed calculation convention, not a determination that
a gross-income/separate-expense treatment is unavailable. `fees` and
`fees-sum` continue to report their existing fee categories; they do not count
these commissions a second time.

#### tjs staking-sum

`tjs staking-sum [PERIOD] [ACCOUNT] [--broker NAME] [--symbol COIN] [--details] [--json]`

Summarizes the rewards shown by [`staking`](#tjs-staking), grouped by
exchange, coin and account tax status. It uses the same period and account
selection, commission evidence and saved valuations. `--details` adds the
underlying reward evidence; `--json` includes the individual rewards and
summary groups together. Unknown commissions remain unknown. NET INCOME is
after the commission the exchange kept: a commission shown is already out of
it, and a view with one ends with a line that says so.

#### tjs fees-sum

`tjs fees-sum [PERIOD] [ACCOUNT] [--by-account | --no-by-account] [--json] [--details]`

Trading-fee report by **brokerage** (commission and fee totals with
per-trade averages, $/share, % of notional), converted to the base
currency: the same totals `tjs run` writes to `reports/fees.rpt`
(which is grouped by brokerage only). Broken down **by account** by
default (`--no-by-account` for brokerage totals only). A `PERIOD` window
scopes it (e.g. `tjs fees-sum 2025`; a look-back window through today,
as the row listings). Its year is each fee's
**trade** date, converted at that date's rate (as in `tjs fees`), so
it differs from `tjs sum`'s FEES column, which follows the project's
`tax_date` (settle in Canada), by the fees of trades that straddle Dec 31
and by cents of FX. Plain futures fees are their own bucket (not stock
fees, not counted in $/share).

#### tjs leaps-sum

`tjs leaps-sum [PERIOD] [ACCOUNT] [--json]`

The LEAPS gain summary (see [`tjs leaps`](#tjs-leaps) for what
counts as LEAPS): per contract, quantity closed, proceeds, cost, gain
and expiry, plus the total (period defaults to the tax year). Only the
long position's dispositions count; a later write and buy-back of the
same contract is covered-call P&L, in `ccd-sum`. The total is split into
TAXABLE and SHELTERED parts when registered accounts contribute. A window outside the tax year (`all`, `2y`, another year) is read from the full-history books, built on demand from the same consolidated base-currency book the tax year's gains come from (`work/<account>_gains_all.json`, across the taxable accounts as the tax year's; superficial-loss / wash-sale denials among the taxable accounts only), said in an `Info:` line.

#### tjs losers

`tjs losers [PERIOD] [ACCOUNT] [--top N] [--json]`

The biggest realized **losers**: `winners`' other side, from the same
code — per ticker, gains below zero only, worst first, options grouped
under their underlying, the losers' total with its taxable / sheltered
split. `--json` carries every ticker, worst first, with the losers' own
totals as `side_*` keys. A loss denied by the superficial-loss or
wash-sale rule is not in it (the engine-allowed amount is what ranks);
`tjs wash-sales` lists those. When a row shown has a denial, a
DENIED column (the denied loss) sits before GAIN, so PROCEEDS − COST +
DENIED is the GAIN; `--json` rows carry `denied`. A window outside the tax year (`all`, `2y`, another year) is read from the full-history books, built on demand from the same consolidated base-currency book the tax year's gains come from (`work/<account>_gains_all.json`, across the taxable accounts as the tax year's; superficial-loss / wash-sale denials among the taxable accounts only), said in an `Info:` line.

#### tjs roc-sum

`tjs roc-sum [PERIOD] [ACCOUNT] [--json]`

Return-of-capital total per ticker (default: the tax year): the ACB
(Canada, T3 box 42) or basis (USA, Form 1099-DIV box 3) adjustments,
split into broker-classified, manual (a `.tt` line, `type=roc` or not)
and `[[distributions]]` rows. An
ADJUST that is no return of capital is listed apart under **OTHER ACB
ADJUSTMENTS**, outside the totals compared with the slip: a reinvested
`[[distributions]]` entry or an RBC notional distribution (the ACB
rises), a spin-off's parent cost reduction (its cost moves to the new
shares); `--json` lists them as `other_adjustments`. A `.tt` ADJUST
without a type, or with `type=roc`, is a return of capital; `type=dist`
is a distribution that raises the ACB. It warns when a symbol has a book
ADJUST and a `[[distributions]]` entry on the same date (the same ROC
entered twice). Use `tjs roc <period>` for every ADJUST row as `.tt`
text.

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

#### tjs trades-sum

`tjs trades-sum [PERIOD] [ACCOUNT] [--json]`

Per ticker: buy and sell counts, value bought and sold, and fees, with
per-currency totals (all accounts; when a registered account
contributes, the taxable accounts' "sold" figure is printed under them).

#### tjs winners

`tjs winners [PERIOD] [ACCOUNT] [--top N] [--json]`

The biggest realized **winners** over a window (default: the tax year):
per ticker, gains above zero only, best first, with options grouped under
their underlying. `--top N` shows the first N (default 10), and the
lines under the table total what the view is about: the winners' total
(how many are shown), its taxable / sheltered split when a registered
account contributes, and the year's total realized gain over every
ticker (with an ACCOUNT, that account's). A DENIED column and `denied`
are as in `tjs losers`. `--json` carries every ticker, best first,
with the overall totals as before, and the winners' own (`side_tickers`,
`side_total_gain`, `side_total_gain_taxable`,
`side_total_gain_sheltered`). Before v0.29.0 the text view also listed
the worst N under the best: that is `tjs losers` now. A window outside the tax year (`all`, `2y`, another year) is read from the full-history books, built on demand from the same consolidated base-currency book the tax year's gains come from (`work/<account>_gains_all.json`, across the taxable accounts as the tax year's; superficial-loss / wash-sale denials among the taxable accounts only), said in an `Info:` line.

### Before you trade

#### tjs wash-radar

`tjs wash-radar [ACCOUNT] [--date YYYY-MM-DD] [--verbose] [--all] [--json] [--details]`

The superficial-loss / wash-sale radar per taxable account, recomputed
**live as of today** (or `--date`), so cooling-down windows reflect the
current date rather than the last `tjs run`. It is forward-looking:
what you can or cannot sell or buy now; for the wash sales that already
happened, use [`tjs wash-sales`](#tjs-wash-sales). The day each
loss sale's window closes, ticker by ticker: [`tjs rebuy`](#tjs-rebuy). It defaults
to every taxable account (sheltered accounts are never targets: naming
one is refused as a usage error, exit 2, as an unknown account is);
pass an account to show only its listings (a `ROWS:` line says so). The rows are
scoped, not the books: every taxable account's buys, holdings and losses
still decide each status (TAXABLE is all your taxable accounts), as the
engine pools them, so a rebuy in another account shows. The combined
`sheltered_base.json` is included automatically for cross-account
detection when present. `--verbose` (`-v`) is
`--details`. CLEAR (no-risk) positions are in the default view; `--all`
adds the OTHER rows, listings with no open position and no loss in the
window (held nowhere, nothing to watch). `--json`
for the structured document with absolute `clears_at` dates. The same
reports are written to `reports/wash_radar_<account>.rpt` during
`tjs run`. With ticker.map `SIMILAR` lines, a `SIMILAR` block lists
each group that has a member with an advisory, every member with its
status (the `--json` document's `similar`): look-alike funds are two
securities for the rule, so one's status is a caveat for the others,
not a verdict.

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
settlement dates, as in the engine. In Canada a loss sale today is
measured from its own settlement too (a Friday sale settles the Monday,
so a buy that settled 32 days before that Monday no longer locks it),
and the date a LOCKED, EXITABLE or CAUTION row clears is the first
trading day whose sale settles after the buy's settlement + 30 on the
listing's calendar (CA-PLAN-01); in a US project, the first trading day
from the 31st day after the buy's trade date (US-PLAN-01). Whether a
sale was a loss comes from the engine's own gains files (the s.47 pool
blended across taxable
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

A project with no account the rule reaches (sheltered accounts only, or
a US project whose taxable accounts are all `crypto = true`: US-WASH-13)
has nothing to check: `wash-radar`, `watch`, `rebuy`, `buy-check` and
`sell-check` say so in one line and exit 0. With `--json` each prints
its usual document with nothing in it (no `sections`, `rows`, `results`
or `changes`; `watch` records no baseline) and `not_applicable` giving
the reason; `wash-radar ACCOUNT --json` on a US project's crypto account
does the same.

#### tjs buy-check

`tjs buy-check SYMBOL ... [--json] [--details]`

Is buying this ticker today safe? **UNSAFE** when a loss was sold within
the past 30 days (the rebuy cancels it, permanently if bought in a
sheltered account), with the safe-from date when one can be determined
(for a violation its re-entry date, `rebuy`'s REBUY ON, never the
sell-by date wash-radar's CLEARS shows for it); **SAFE\*** when buying merely extends an open
wash window. Root-matched (`buy-check SAMPLU` covers `SAMPLU.US` and
cross-listings, folding in `ticker.map` pairs). A fund ticker.map
declares `SIMILAR` to the queried one that was sold at a loss within
the past 30 days adds a caveat line (**SAFE\***, never UNSAFE: the
books keep the two apart). `--json` for machines;
exit 1 when any symbol is unsafe. Every loss whose window is open, with the
day it closes: [`rebuy`](#tjs-rebuy). A `ticker.map` or `tobase.map`
line that cannot be parsed, or rules that contradict each other, stop it
before any verdict, as they stop `tjs run` (exit 2; `sell-check` and
`rebuy` too): the map decides which listings are one security, and a
dropped rule hid a cross-listed loss.

#### tjs sell-check

`tjs sell-check SYMBOL ... [--json] [--details]`

Is selling this ticker **at a loss** today safe? **UNSAFE** when a
registered account's recent buy it still holds would deny the loss on
the whole position (LOCKED), or an open violation is backed by a
registered account's in-window buy; **PARTIAL** when only some units are
at risk (the line says how many; the rest of the loss stands);
**ACTION** when a violation can be rescued by selling the taxable
replacement before the deadline (Canada only: a US wash sale cannot be
rescued, and a WASHED row is SAFE\* with the reason); **SAFE\*** /
**SAFE** with the applicable caveats (the window caveat in full with
`--details`); a fund ticker.map declares `SIMILAR` to the queried one
bought within the past 30 days is one such caveat. Whether it *is* a loss at today's
price is `harvest`'s job. `--json` for machines; exit 1 on UNSAFE or
PARTIAL.

#### tjs rebuy

`tjs rebuy [SYMBOL ...] [--date YYYY-MM-DD] [--json] [--details]`

"I sold at a loss: when can I buy it back?" Every ticker sold at a loss
whose repurchase window is still open, with the date of the sale, the
loss, the accounts that sold, and **REBUY ON**: the first day a buy no
longer cancels the loss (with the days left). It is the wash radar's own
date, not a second computation, and always a trading day on the
listing's calendar: in Canada the first day whose buy settles after the
window of the latest loss sale closes, 30 days after its **settlement**
date (s.54): the window's last day when a trade then settles the next
day, the Friday before when it closes on a weekend; in the US the first
trading day from the 31st day after the latest loss sale's **trade**
date that a buy can still disallow (§1091: a loss the engine already disallowed in full shows
"now (washed)", and after a loss on covering a short only a new short
sale replaces it). A buy in **any** of your accounts counts, an RRSP or
TFSA (an IRA) included, and there the loss is denied for good. Option
series are listed under their underlying, each with its own date (only
the same series replaces an option's loss; in Canada a long call
replaces the shares). The listings `ticker.map` and `tobase.map` join
count as one security, as in `buy-check`: `rebuy XYZ.US` finds a loss
booked as `XYZ.TO`, and `--details` names the other listings a buy of
which counts. A loss already bought back that can still be rescued is an
`! ` line with the sell-by date (`wash-radar` has the detail); each loss
is judged on its own, so when another loss of the ticker is past its last
rescue trade date the line names the loss a sale keeps, and `--details`
calls the other's denied units final; tickers
whose window closed in the last 7 days are one line (their table with
`--details`). `--date` sets the as-of day; with SYMBOLs only their rows
are shown, and a SYMBOL with none is an `! ` line (not in the books, or no
loss with an open window) counting the other tickers that have one. Exit 0 (advice, never a failure). How it differs:
[`wash-radar`](#tjs-wash-radar) is the status of every holding (what
a sale **or** a buy does today, in every account), and
[`buy-check`](#tjs-buy-check) answers one ticker you are about to buy
**today**; `rebuy` is the list of every loss whose window is open and the
day each one closes (tax-logic `CA-PLAN-05` / `US-PLAN-06`). With a ticker.map `SIMILAR` line, `--details` also names the similar listings a buy of which is flagged, not counted (the `--json` row's `similar`).

#### tjs harvest

`tjs harvest [SYMBOL ...] [--crypto] [--options] [--no-ibkr] [--ibkr-port PORT] [--verbose] [--json] [--details]`

"If I sold this today, would it be a loss, and may I claim it?" One
table over every taxable account's OPEN positions: base-currency book
cost (from the wash-adjusted books, so deferred superficial / wash
losses are already folded into the basis), current price (IBKR →
yfinance → price cache, with the source labelled), unrealized gain or
loss, and, on every LOSS row, the wash radar's advisory with a view-time
countdown to the clear date. Losses come first. US projects get an
`LT_IN` column: days until the position is long-term, counted from the
earliest lot's holding period (a wash-sale replacement's includes the
days the loss shares were held, US-WASH-10).

```bash
tjs harvest                 # all open positions, harvestable losses first
tjs harvest AAA.TO BBB.US   # just these symbols
tjs harvest --json          # machine-readable
```

A symbol ticker.map or tobase.map books under another listing
(`TOBASE ZZX.US ZZX.TO`) shows that listing's row, with an Info line
naming it.

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
only overstate the age and green-light a denied loss; run `tjs run`
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

Option positions (e.g. LEAPS) are also excluded by default, with a
note naming them. Pass
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
pounds. A price-cache entry serves only the Yahoo spelling it was
fetched for: after a `QUOTE` or `CRYPTO` line changes a symbol's
spelling (or for an entry an earlier release wrote without it) it is not
used, with a warning, until it is fetched again online. In a US project an open short shows `ST` under `LT_IN`: covering
it is short-term (US-HOLD-03). `--verbose` prints the per-tier
price-chain diagnostics; `--json` carries the scope note (`scope_note`)
like the radar's, and `omitted`: each position left out (no price, no
rate for its quote currency, a unit or currency it cannot read) with
its reason. A quote in a currency the books have no rate for is named in
one warning: `tjs run` fetches the `source_currencies` only, so add the
currency there. Under grant timing, a written option's UNREALIZED is
the whole buy-back cost, because the premium was already recognised at
the write ([tax-rules.md](tax-rules.md#options-premium-timing-s49)).

#### tjs tips

`tjs tips [--online] [--json] [--details]`

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
  The withholding follows the issuer's country, not its listing: a
  `ticker.map` `T1135 SYMBOL COUNTRY` line, else the export's ISIN, else
  the interlisted master (`tobase.map`). A US-listed payer domiciled
  elsewhere (`T1135 SYMBOL CAN`) is not flagged; one nothing records is
  flagged as a condition ("if its issuer is US-domiciled").
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
the project cannot be read (no holdings reports: run `tjs run`).
`--json` (schema_version 2): `findings` (`check`, `account`, `symbol`,
`message`) and `unscanned`. The map's own hygiene (listing pairs to
verify, unused rules, cross-listing losses) is
[`tjs ticker-map --suggest`](#tjs-ticker-map)'s job.

#### tjs watch

`tjs watch [--harvest] [--threshold AMT] [--no-ibkr] [--state PATH] [--exit-code] [--json]`

A cron-able change detector: it reports only what CHANGED since the
last watch run: new, changed or cleared radar advisories, moved clear
dates, and (with `--harvest`, which runs `tjs harvest --json` and
needs a price source) the harvestable-now loss total moving more than
`--threshold` (default 100, base currency). A report ends with the scope
line (verdicts cover this project's accounts only: CA-PLAN-04 /
US-PLAN-04). It is silent with exit 0 when nothing changed, so a cron
line mails only on news; `--exit-code` exits 1 on changes for scripting,
`--json` for machines. State: `work/.watch_state.json`; `--state PATH`
gives a cron cadence its own baseline (daily and weekly lines can
coexist). `--no-ibkr` is passed on to harvest.

A watch compares only complete books: when a book the radar reads is
missing from `work/` (a taxable account's `<account>_base.json`, or
`sheltered_base.json` in a project with a TFSA, RRSP or IRA account), a
LOCKED name would read CLEAR, so it compares nothing, keeps the saved
baseline (records none on a first run) and says so in a `Warning:`,
exit 0. `--json` has `incomplete`, the missing files (`[]` on a complete
run), with no `changes` and `tracked` / `actionable` null. Run `tjs
run` to rebuild them.

### Before you file

#### tjs form-export

`tjs form-export [--form 8949|schedule3|txf] [--box A|B|C] [--out FILE] [--csv FILE] [--json] [--details]`

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
  A short sale shows what it brought in as (d) and the cover as (e); a
  re-short that replaced an earlier short's washed loss carries that loss
  in (e), as the 1099-B does, not out of (d).
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
  6781 BY HAND** section with their P/L (Form 6781 itself is by hand:
  its year-end marking is not modelled; its 60/40 split is what the
  estimate and `tjs carryover` use). Cells are rounded half-up to
  the cent.
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
the JSON as `manual_reporting_required`, and `tjs checklist` keeps
the form-export step open until they are reported by hand. `--json`
carries the same `readiness` record as `tjs sum --json`.

#### tjs t1135

`tjs t1135 [--json] [--details]` (Canada)

A helper for CRA **Form T1135** (Foreign Income Verification Statement),
for Canadian filers holding foreign securities. It answers the filing
question first: it replays the full history of every **taxable** account
in the base currency and reports the **maximum total cost of specified
foreign property at any time in the year**, the ITA 233.3 test ($100,000
threshold; $250,000 for the detailed method, from the form's
instructions). The test sees these brokerage books only: foreign
property held outside them (a foreign bank account, cash held
elsewhere, certificates, foreign real estate) counts toward the same
threshold and must be added by hand. Before Dec 31 the figures run to the last date in
the books, a "below the threshold" verdict says "so far" (the checklist
keeps the step open), and the year-end column is headed with that date.
A long option still held after its expiry date is named (its cost is
still counted). A position held only short in the year (a written
option bought back or left to expire, a short sale covered) is not a
row: it is an obligation, not property held, so a `! ` line names it
and its gain stays in `tjs gains` (tax-logic CA-RPT-19; `--json`:
`short_only_symbols`). A position held long at any time keeps its row. The books must be in the base currency (the converted
`_base.json` files; native-currency rows are refused).

If a filing is required, it prints per-property and per-country tables
(maximum cost in the year, cost at Dec 31, income, gain or loss) from the
same books the rest of the pipeline reports on (income: the year's
dividends and payments in lieu, dated when paid, and the interest an
INTEREST row naming the property pays, such as a foreign bond's coupon;
tax-logic CA-RPT-14), with the project's
missing-history openings applied exactly as the gains stage applies
them. Registered accounts are excluded by law and never read. A plain
futures contract has no cost amount of its own (nothing is paid to open
one), so its notional stays out of the cost columns and the threshold
test; a superficial loss denied on one and added to the replacement
contract (s.53(1)(f)) is its cost amount, and makes it a row. An
option on futures counts at its premium. An assigned written put's
premium is deducted from the shares' cost and an exercised call's cost
added to them (s.49(3)/(3.1)), rows sharing a timestamp follow the
engine's order, a split inside a trade's settle lag re-denominates it
like the engine, and the gain column and year-end position follow the
project's `tax_date`. The cost amounts are the gains engine's own pools:
`t1135` runs the engine once over the full history (with the registered
accounts as wash context and the project's option timing, as `carryover`
does) and reads each pool's cost day by day from its cost timeline, so
a superficial loss denied in ANY year is in its replacement's cost
(s.53(1)(f)) exactly where the engine put it, and a warrant's or an
exercised call's cost is in the shares it delivered (s.49(3)). (The
standalone `taxjson-t1135 --year-wash-only` is ignored, with a note.) A configured taxable account with inputs but no books is refused,
so is an account whose books are there without its gains file
(`work/<account>_gains*.json`, where the income and gain columns come
from: they would read 0), and books built for another year are warned
about. `--json` for machine-readable output.

A Canadian company's shares held at a broker outside Canada are
foreign property: set the account's custodian country in
taxjson.toml (`custodian_country = "USA"` under its
`[accounts.NAME]`, docs/settings.md) and its Canadian issuers are
rows of category 6 under that country, at that account's share of
the ACB pooled over every taxable account (its units times the
pool's cost per unit; tax-logic CA-RPT-02; `--json`: `custodians`,
each row's `custody_account`). The
stand-alone `taxjson-t1135` takes it as `--custodian ACCOUNT=COUNTRY`;
read without an account's gains file (`--gains`) it shows that
account's income and gain as n/a (`null`, `gains_not_given`), not 0.

The cash an account in the books holds outside Canada is foreign
property too (category 1, tax-logic CA-RPT-20): a crypto account's
(country CRYPTO until its `custodian_country` says where the platform
is; `"CAN"` makes it not foreign) and every currency of an account whose
`custodian_country` is outside Canada. Each account and currency is a
row (`USD cash (crypto)`) at its cost day by day: a foreign currency at
the FX-on-cash ledger v2's pooled average cost (the figures `tjs
fx-cash --ledger v2` works from; a crypto account's USD stablecoins are
its US dollars, and a stablecoin reward is that row's income), Canadian
dollars at their amount where the books give the balance (a `CASHOPEN`
line or a statement balance at the start of the year, and the year's
statement balances reconciled), on the project's `tax_date` basis as
the securities are (with `tax_date = "trade"` a purchase's cash
leaves, and a sale's proceeds come in, on its trade date). A day's
fall in the cash is taken before that day's trades and a rise after
them, so the money that bought a coin is not counted twice. The
interest credited on the cash (an INTEREST row naming no symbol) is
the income of the row of the currency it was paid in; interest
charged on a debit balance is no row's. Where the ledger refuses, the
row's cost is unknown: below the threshold the verdict reads `NOT
SETTLED` and names `tjs fx-cash --ledger v2` (`--json`:
`total_settled` false, `cash_unknown_cost`), and the checklist's t1135
step needs attention until that ledger computes (a `CASHOPEN` line for
each currency at the
start of the year is the usual fix). Where the books give no
Canadian-dollar balance, a `! ` line says the account's cash is not
counted, to add by hand (`cash_not_counted`).
Cash at a broker in Canada is not foreign property and is not read.

<pre>
$ tjs t1135
T1135 — Foreign Income Verification Statement helper, tax year 2025, CAD

FILING REQUIREMENT — total-cost test, ITA 233.3
Maximum total cost in 2025: 262,500.00 CAD on 2025-06-16 <!-- pii-ok -->
...

PER PROPERTY — taxable accounts only
Amounts are cost (ACB), not market value; MAX COST IN YR: the most held at once.
CAT: the form's category (2 shares; 6 options, futures, crypto); ticker.map `T1135 SYMBOL COUNTRY CAT` sets another.
SYMBOL     COUNTRY  CAT  MAX COST IN YR  COST AT DEC 31  INCOME  GAIN(LOSS)
---------------------------------------------------------------------------
SAMPLG.US  USA      2            980.00          490.00  132.00      120.00
...

PER COUNTRY — upper-bound aggregates
...
Not counted: foreign property outside these books (a foreign bank account, cash held elsewhere) — tjs t1135 --details
</pre>

The verdict lines under FILING REQUIREMENT say whether a filing is
required and whether the detailed method (Part B) applies; a `! ` line
names a row to check by hand (crypto: where it is held), and
`--details` adds the notes behind each.

CAT is the form's category of each property (tax-logic CA-RPT-18),
taken from the instrument: 1 (funds held outside Canada) for an
account's cash, 6 (other property) for an option, a futures
contract or crypto, 2 (shares of non-resident corporations) for any
other listed security. A fund's or a trust's units may belong in
another category, so check the 2s that are not shares. Units or a fund
the interlisted master knows (a Bermuda partnership's units, booked
under its US listing) show `?` and a `! ` line. A category after the
country of a `T1135` line sets it:

```
# ticker.map
T1135 SAMPLT.US  USA 4    # a US trust's units: category 4
T1135 SAMPLU.US  BMU 6    # partnership units
```

Domicile is classified by market suffix (`.US` → USA, `.L` → GBR, `.AX`
→ AUS; `.TO` / `.V` / `.CN` / `.NE` → Canadian, i.e. not foreign
property). Since domicile, not listing exchange, is what T1135 cares
about, interlisted names can need a `T1135 SYMBOL COUNTRY` line in the
project's `ticker.map` (symbols are matched case-insensitively; an
override follows the symbol through a ticker change, and one that
matches nothing in the books is warned about; a COUNTRY that is neither
an ISO 3166 alpha-3 code nor CA / CAN / CANADA / EXCLUDE stops `tjs
run` and `tjs t1135` with a did-you-mean hint, like any malformed
ticker.map line). In a Canadian project, tobase.map books an interlisted
pair under the issuer's home listing and gives a foreign issuer's pair
the issuer's country (`country=`: a Bermuda issuer's US listing is BMU,
not USA); a `T1135` line naming a listing that a `TOBASE` line books
under another symbol applies to that symbol. These were once the lines
of a separate `t1135.map`; `tjs migrate` converts one. A foreign
listing whose rows carry a Canadian ISIN (IB stamps the issuer's
country) is named in a warning until you map it, and so is a Canadian
listing whose rows carry another country's ISIN (a US corporation
bought on the TSX: `--json` `foreign_issuer_symbols`), which is counted
as Canadian until a `T1135` line gives its country:

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

#### tjs reconcile-slips

`tjs reconcile-slips SLIP.csv [SLIP.csv ...] [--tolerance N] [--json] [--details]`

Diffs the broker's official slips (CRA **T5008**, IRS **1099-B**) against
the computed dispositions, per symbol: disposition count, quantity,
proceeds, and (when the slip carries cost) basis. Several slip files
(one per broker) are reconciled together. With no slip saved yet, the
documented `inputs/slips/*.csv` is refused with "no slip CSV matches
... nothing to reconcile yet" (exit 2): save the slip there, or, with
no slip to reconcile, `tjs checklist --done t5008`. CRA and the IRS machine-match
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
year's write premium reconciles with a note. The cover of a short sale
that is not in your files has unknown proceeds: its symbol is
`UNKNOWN_PROCEEDS` (a failure: the slip's proceeds cannot be checked),
or `MISSING_FROM_SLIP` without a slip row. A slip row with amounts but
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
date for Canada, trade date for the USA); `tjs reconcile-slips`
passes both. A `--gains` file whose dispositions (or its
`metadata.target_currency`) are in another currency than the country's
(the native `*_raw_gains.json`) is refused with an Error naming it (exit
2), before anything is compared.

#### tjs slip-audit

`tjs slip-audit [ACCOUNT] [--template] [--import-cra PDF|DIR ... [--write]] [--tolerance N] [--json] [--details]` (Canada)

Checks the T5 and T3 slips against the books' income, per account and
slip box: Canadian dividends (T5 24/10, T3 49/23), box 18 capital-gains
dividends against `[[capital_gains_dividends]]`, foreign income and tax
withheld (T5 15/16, T3 24/25/33/34), return of capital (T3 42) against
the books' ADJUST rows, interest (T5 13). The slips come from three
places in `inputs/slips/`:

- `slips.toml`, typed from the slips (`tjs slip-audit --template`
  prints one, a T5 per account and currency with income; the format is in
  [settings.md](settings.md#inputsslips-slipstoml-and-ibs-dividends-reports));
- the PDFs CRA My Account shows under "Tax information slips"
  (`tjs slip-audit --import-cra <folder>`: one layout for every
  issuer; each slip is placed in the account, broker account and fund
  whose payments it matches, a T5 by its issuer, the broker's name or its
  carrying dealer's (Webull Canada's slips come from CI Investment
  Services; `src/taxjson/data/slip_issuers.toml`), each slip once; an
  amended slip replaces its original, or with none in `slips.toml`
  the earlier amended slip (the issuer's second amendment); `--write`
  appends them to `slips.toml`);
- IBKR's dividends report (`U*.YYYY.dividends.csv`, its per-payment
  T5 / T3 split), matched to the account whose books carry that IB
  account, payment by payment (a USD-base account's payments converted to
  CAD at the Bank of Canada rate of each pay date).

A slip in USD (RBC, Webull) is compared in USD, and its CAD is shown
both ways: each payment at the Bank of Canada rate of its date (what the
books do) and the year's average rate (the mean of the Bank's daily
rates in the FX cache); for a CAD slip holding converted payments (IBKR)
the report says which of the two its figure is closer to. It lists
payments on the slip and not in the books (and the reverse), a payment
whose only book row near its date is in another currency (never
matched across currencies; a security's CAD and USD slip amounts are
compared apart), trust
distributions counted in another year by their record date, accounts
with income and no slip, slips with no income behind them, and prints
the exact `[[capital_gains_dividends]]` entries and `.tt` lines
(`ADJUST ... type=roc`, and a negative `DIVIDEND` when the dividend row
holds the return of capital) that bring the books to the slips, with
the file they go in (the account's `slip-audit.tt` in the folder the
year reads its exports from: `../inputs/<account>/` with one folder of
exports for every year, read by every year); where
the books hold part of a payment's return of capital, the line is the
rest, and a note names the existing line to change instead.
`--tolerance` is the allowed difference per box in CAD (default 1.00; a
box holding converted money also allows 0.5% of that part; a slip in
another currency is allowed its worth in that currency, at the year's
average rate). It changes
nothing; exit 1 on a finding, and on anything it could not compare (NOT
COMPARED, `--json` `not_compared`: an account with inputs and no
readable books, an IB report no account's books carry, an IB component
taxjson does not know, a `broker_key` no broker account matches) — the
audit is then incomplete, never "Every slip agrees". `--json`: a stable
schema ([settings.md](settings.md#tjs-slip-audit---json)). The
checklist's `t5-t3` step runs it and is answered per account with
`--done`; it is blocked while an account's books cannot be read, and
needs attention while something else is not compared.

#### tjs carryover

`tjs carryover [--json] [--details]`

A multi-year **capital-loss carryforward / carryback ledger** over the
taxable accounts' full history (the same engine as the yearly pipeline:
wash / superficial-loss adjustments and missing-history openings
included; a parity test guarantees each year's net matches the
pipeline's own per-year run). In Canada the project year's net is the
RETURN row of `tjs sum` (each Schedule 3 row's cents, as filed),
not the books' unrounded total a cent or two apart (`--json`: `net_source`
`"return_row"` and `engine_net_gain`). Per year with any disposition:

- **Canada**: net gain or loss, a running net-capital-loss carryforward
  (losses carry forward indefinitely), and for each loss year the
  still-open **3-year T1A carryback candidates**, capped at each earlier
  year's remaining net gain and never double-counted across loss years.
- **US**: net short-term / long-term, the up-to-$3,000 ordinary-income
  offset (assumed used when available), and the running ST / LT
  carryover per the Schedule D worksheet ordering; while a carryover
  remains, each year up to the project year has a row, a year with no
  sale taking its offset too. A year's net of §1256
  contracts (futures, broad-based index options) is in NET ST 40% and
  NET LT 60%, as Form 6781 puts it on Schedule D (`--json`: the row's
  `section_1256`, `section_1256_short_term`, `section_1256_long_term`).

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
it is reported as unmatched. A claim above its year's net gain is
applied as recorded and named ("2025: claimed 100.00, 80.00 above its
net gain"): a net capital loss applied in a year is at most its taxable
capital gains (ITA s.111(1.1)), so it stands only when the return had
gains the nets leave out (slips, the FX gain on cash); `tjs
estimate` and close-year apply no more than the gain and carry the rest.

If the history's first year has dispositions, the ledger warns that
pre-history balances are not reflected, and rows before the project
year are flagged as rebuilt from this project's books (opening lots plus
whatever prior-year exports are in `inputs/`) and possibly partial:
check them against the filed returns. A year before the project year
that has a close-year lock (the project's own `filed/<year>.json` or the
`[settings] prior_year_record` of the per-year layout) takes the lock's
**filed** figure instead (Canada: the total filed with another tool when
the lock has one, else the RETURN row close-year recorded, else the
Schedule 3 gain lines; US: the Form 8949 Part
I / Part II gains with the lock's Form 6781 line split 40% / 60%, or all
short-term for a lock closed before taxjson split it, as that lock's
carryover took it: a `!` line says so, and closing that year again with
`--force` splits it), and the lock's recorded balance becomes the running
balance at its year end (US: the year's 3K OFFSET is then the deduction
the lock counted as used, and a `[carryover] claimed` entry for the year
that differs from it is a `!` line). With no `[carryover] claimed` entry for such a
year, its APPLIED is the losses its lock applied, and that much of its
gain is not offered again. Of the claims under earlier years, a locked
year takes only the carry-back its lock recorded: the rest of such a
claim (the total applied on that return) waits for a later year's loss.
Such a lock that cannot be used (unreadable, damaged — its carry-forward
block included — or closed under the other country's law: the check
every lock reader applies) stops the ledger, naming the lock and the
fix, as the estimate does. A closed project year (its own lock, taken
after the year ended) shows the same way what its lock applied and the
balance it carried out, so a loss close-year used is not offered again;
its NET GAIN(LOSS) stays the books', and its carry-back the claims'. A
locked year that applied more than the record before it carries in (that
year amended and closed again since) is named: its return needs a T1-ADJ
too. Where a lock's balance differs from the books' (a first year closed
with no [estimate] other_losses), a `! ` line gives both. The project
year (its NET GAIN(LOSS)) or a later locked year is
compared with its lock; a lock of such a year that is damaged or cannot
be read is named in a warning (the row is the books', the lock gives it
no figure; `tjs check-filed` checks the lock). Rows after the project year (a few
January trades in this year's inputs) are partial: they offer no T1A
carry-back and the carryforward stops at the project year. Every year
uses the project's settings, its income dating
(`corporate_distributions`) included; in a US project a wash-sale basis
add whose replacement was sold in a locked year is booked in the loss's
year, as `tjs run` books it (US-WASH-22). A sale with an unknown
cost (no purchase in the books; a crypto account's too) is in no year's
net: a warning counts them with their years, and `--json` lists each
(`excluded_unknown_cost`: year, date, symbol, account, qty, proceeds)
with `incomplete_years`, each such year's row `incomplete: true`. The net per year counts
dispositions plus the T5 box 18 dividends named in
`[[capital_gains_dividends]]`; other slip capital gains (lines
17400/17600, US Schedule D line 13) and the line-15300 FX gain on foreign
cash (`tjs fx-cash`) are not in it. The rules:
[tax-rules.md](tax-rules.md#net-capital-losses-carryforward-and-carry-back).

#### tjs option-boundary

`tjs option-boundary [--json] [--details]` (Canada)

Written options whose write and close straddle a tax-year boundary, or
that are open at year end: where the premium and any later amount land
under ITA s.49 for the timing in force, and, using the `filed/` locks,
whether a filed year needs a T1-ADJ (an assignment after the grant year
was filed, s.49(4)): none once that year's lock, re-closed after the
amendment, no longer reports the premium. A lock that is damaged or was
closed under the other country is named, and its option timing taken as
unknown. A contract written in a LOCKED year but kept on
transition close timing here is flagged ATTENTION, as is one on
`option_premium_timing = "close"` whose write year's lock records grant
timing or reports the premium (that return taxed it already), and a contract past
its expiry date with no expiry or assignment row in the export. How
`option_premium_timing` and `option_grant_timing_since` work:
[tax-rules.md](tax-rules.md#options-premium-timing-s49).

#### tjs close-year

`tjs close-year [--year YEAR] [--force] [--yes] [--filed-dispositions CSV] [--details]`

Snapshot the current tax year's filing aggregates to
`filed/<year>.json`, the filed-year lock that `check-filed` (and every
full run) guards. Commit it with your records. The lock records which
taxjson computed it (`taxjson`: the version, and for a git install the
commit, the release tag it is at and whether a file was modified). It also records what the
next year needs for `tjs handoff`: every sale, the positions and cost
at Dec 31 (superficial-loss deferrals included: what the sales before
Dec 31 left of each; a US crypto account's without the wash-sale rule,
as the run computes it), and the trades that
settle in January. And it records the year's carry-forwards (the net
capital loss; US: the short- and long-term capital loss carryover; in
Canada, the minimum tax carryover by year of origin) from the year's own
estimate: set `[estimate] other_income` before closing, since the
minimum tax depends on it. With no `[estimate] other_losses` and no
earlier close-year record the year starts from no net capital loss; when
the books (`tjs carryover`) carry one into it, a note gives the
amount and the `[estimate] other_losses` line that takes it in. US: the
carryover counts the $3,000 deduction as used as far as the estimate's
taxable income absorbs it, or as the year's `[carryover] claimed` entry
says (the deduction the return took); the lock records the deduction it
counted, and with neither that entry nor `[estimate] other_income`
close-year says how much of the deduction it left unused. The part of
the net capital loss that
`[carryover] claimed` carries back to an earlier year (T1A) is taken
out of what it carries forward; with `--filed-dispositions` (Canada)
the net capital loss is the filed return's, its gain standing in for
the books' sales, and the summary shows both. The summary's realized
and disallowed are the return's (`tjs sum`'s RETURN row: each sale's
cents, recorded in the lock as `return_row`, which `tjs years` shows
too), and its income is `tjs sum`'s taxable SUBTOTAL (each
account's DIVIDEND and PIL to the cent, as `tjs years --json` shows
it); the drift check compares the engine's totals, a cent or two apart
on a large year (`--details` names both). In Canada the year's T5 box
18 capital-gains dividends `[[capital_gains_dividends]]` names are
recorded beside it as `slip_gains`: the next year's `tjs carryover`
adds them to the year's filed net, the carry-back room it offers. A Canadian project with no supported
`province` gets a federal-only estimate, said in the output (every
figure carried forward is federal anyway). See
[tax-rules.md](tax-rules.md#net-capital-losses-carryforward-and-carry-back).

When the return was prepared with another tool, `--filed-dispositions`
stores the sales it actually reported (CSV:
`symbol,date,qty,proceeds,cost,gain`, optional `account`); the next
year's `tjs handoff` checks doubles against these. Every row is
dated in the year being closed (the date the return used): a row of
another year is refused, naming its line, before anything is written.
`--year` must
match `[settings] year` (a guard). `--force` replaces an existing lock
(re-filed or amended years only): it keeps the filed dispositions of the
lock it replaces (unless a new CSV is given; one of them dated in
another year is refused, asking for the CSV) and warns when that lock
recorded other totals, and when a later year's lock (its folder beside
this one) took in the net capital loss the replaced lock carried and
this one carries another: that return was built on the old balance and
needs amending too. It refuses, even with `--force`, books whose
last run did not finish (no reports, an unreadable
`work/<acct>_base.json`) and books the cross-account pass did not
build (a `run --account`, or an account deferred at pending elections,
which it names); the refusal offers `--force` only where it would lock
the books. Without `--force`
it refuses a year that has not ended and a year with no disposition and
no income in the taxable books (a typo'd `year`); books built with
another option timing than `taxjson.toml` now says are refused even
with `--force`. It also stops while checklist items before the lock
still need attention (it lists them, on a terminal above its question
too: a January export not in yet, missing history, uncommitted inputs
in a git repository, an unreliable fx-cash year ...):
on a terminal it asks whether to lock anyway, piped it refuses unless
`--yes` is given. A project that is not a git repository always has
`inputs-committed` open, so there `--yes` (or answering yes) is the
way to close.

It also **freezes the inputs** the filed books were built from, so
the year can be rebuilt as filed years later while the shared exports
move on with the next year. With exports shared by every year
(`inputs_dir`), every file the run read is copied into this
project's own `inputs/<account>/` (and the shared `tobase.map`
beside `ticker.map`; the `crypto_sends.tt` the year generated in
`work/` goes into `inputs/<account>/`, where the frozen year writes
it), and `taxjson.toml` stops naming the shared
ones (`inputs_dir` and `tobase_map` commented out under a `## frozen
by` note): from then on this year reads its own copy, like a
single-folder project. A project that already reads its own files
copies nothing. Either way the lock records the name and SHA-256 of
every input (`frozen`), `check-filed` names a frozen input that
changes afterwards, and `reports/` is kept as `filed/<year>/reports/`
for `tjs compare filed`. It refuses, writing nothing, when
`inputs/<account>/` already holds files this project does not read
(move them away, or into the shared folder if they belong to the
books). `tjs new-year` points the next year at the shared
folders again. A year that has not ended (`--force`) is not frozen:
the lock records the hashes only (`frozen.partial`), the year keeps
reading the shared exports, and a later trade shows as drift until
the close after filing freezes the whole year. A partial lock that
an older release froze is thawed by `--force` first (taxjson.toml
names the shared folders again, the unchanged copies are removed; a
copy edited since is named and kept), which stops for a `tjs run`
before the year is closed.

#### tjs check-filed

`tjs check-filed [--details]`

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
locked, and the RETURN row close-year recorded (`return_row`) is not
recomputed (every OK says so; one that is not a table of finite
numbers is a damaged lock). A lock that froze its inputs (every lock
`close-year` writes now) names each frozen input that changed since
the year was closed, so a DRIFT is not blamed on the code, and a DRIFT
names the taxjson the lock was closed with: this one (the change is in
the inputs or the settings) or another (the releases between them say
what each moved; a lock closed before this was recorded names none). It names each
shared export it was copied from that gained, changed or lost a row of
that year or earlier since (a corrected sale, or the late-December
trades a re-download has, saved in the shared folder: the filed year
reads its copy and the next year's `handoff` compares only Dec 31
positions and cost, so this is where it shows), and each export put in
an account's shared folder since the close that holds a row of that
year or earlier no frozen copy of the account has. Rows of later years
are no change, nor is a row one frozen copy of the account already has
(an overlapping download); the rows are counted, so one of two
identical rows removed is a lost row. A row's year is its earliest
date, in any form a supported export writes it (Webull's DD-MM-YYYY,
RBC Direct's "January 30, 2025"); each file is read as the parsers
read it (a UTF-16 export too). It names a shared generic mapping
(`<csv>.toml`, `generic.toml`) that now reads one of the year's CSVs
holding a row of that year otherwise than the copy (another column,
action or format; a comment is no change), and a file of that
comparison it could not read (not UTF-8 or UTF-16 text, not TOML), as
not compared. When the year's
`taxjson.toml` names the shared `inputs_dir` or `tobase_map` again (it
no longer reads its frozen copy), that is a warning, and `run
--strict` stops on it; so is the year's own lock having taken in a net
capital loss from the earlier year's close-year record that the record,
closed again since (an amendment), no longer carries: the warning gives
both balances and what the return applied (more than the new balance:
it needs a T1-ADJ too). A lock of an earlier year kept in this year's
`filed/` is checked from the inputs this year reads, as they are now.
In the folder holding the year folders, or in a year with no lock of
its own, it names the year folders that hold one (`tjs -C 2024
check-filed`).

An unreadable lock is named and counts as a failure; so does a lock
deleted while the year's `taxjson.toml` still carries close-year's
freeze note, or its `filed/<year>/reports/` still holds the reports
close-year kept ("filed/2024.json is missing: restore it or run close-year
again"; `tjs run` warns the same). A lock closed under
the other country (every lock records its `country`) is refused by name
and never recomputed under this project's law. A lock is recomputed on
the date basis it recorded, and a note says when this project's
`tax_date` or `option_buyback_loss_superficial` now differs from the
lock's (its own reports for that year then differ from the filed
return). A lock account entry that records none of the locked totals, or
whose `form_lines` is not a table, is damaged, never OK; so is a lock
the next year's estimate, `carryover` or `handoff` would refuse (a
carry-forward block without its closing balance, with a figure that is
not a number, a year of origin twice or a negative amount; a `year`
other than its file's), and a lock whose `fx_cash_v2` close the next
year's FX-on-cash ledger would refuse (not a table of books for its
year, a book with no finite units and cost). A bad
`[settings]` value is refused as a settings error before any lock is
checked; when the recompute itself fails on an input, the child's own
error is shown and the exit code is 2 (1 is drift or a damaged lock).
Every full run also checks the locks (`tjs run --strict` stops on
drift or an unreadable lock).

#### tjs handoff

`tjs handoff [--prior PATH] [--json] [--details]`

Checks that this year's project starts from exactly what last year's
return carried forward, using last year's `close-year` record
(`--prior`, else `[settings] prior_year_record`, else
`filed/<year-1>.json` here). It checks:

- opening positions and cost at Dec 31 against last year's year-end
  books (US: lot by lot too, each lot's account, acquisition and
  holding-period dates, units and basis in FIFO order, since a sale
  takes a lot's basis and term; a record closed before it kept its lots
  is checked by position, with a note);
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
  `long_term_losses`, `[carryover] claimed`'s entry for that year less
  the part this project's own loss carries back to it (T1A), `[estimate]
  amt_carryover`) against what the record carried out: your notice of
  assessment decides which is right. US: the closed year's `[carryover]
  claimed` entry against the deduction its record counted as used (a
  record closed before it recorded one: what its balance implies).

A record closed before its year ended is flagged as a partial-year
snapshot, and a record of another year than the one before this
project's (a 2024 record in a 2026 project) as a problem of the record:
the year-end in between is not checked. An early-January row the broker corrected since the closed
project read it (same date and quantity, another amount) is the same
row: a note gives both amounts. A cost difference is listed with the two
consistent choices: keep last year as filed and open with the cost that
return implied (the record's), or amend it and open with the corrected
cost (this project's). A record closed under the
other country is refused by name. Exit 1 on any problem; `checklist`
runs it.

### Explain and check

#### tjs audit

`tjs audit [SYMBOL ...] [--id ID] [--date D] [--account NAME] [--year Y | --all-years] [--summary] [--no-trace] [--no-color] [--json] [--details]`

The **authoritative justification** of every capital-gain figure: one
block per disposition tracing the parsed broker row (nominal currency,
original ticker, source file) through the ticker.map rename, the exact
FX rate applied (provenance named, recomputed against the base books to
the cent), the ACB / FIFO disposition math, and the wash-sale /
superficial-loss determination with replacement lots resolved, ending in
a tie-out against the pipeline's saved gains files and the full pool
trace. It runs the same computations the pipeline runs — one blended
pass over the taxable equity accounts, one over the crypto accounts the
run pools (a Canadian project's two or more; a US project's once coins
moved between two of them, with no wash-sale rule), each other crypto
account on its own, with the project's filed years (a US wash-sale basis
add into a filed year is booked in the loss's year) — so the audited
numbers are the run's numbers (what the checks cover: below). Exit 1
when any cross-check disagrees,
and `--json`'s `failed` says the same. An account the last run deferred
at a corp-action election (`work/<account>_pending_elections.json`) is
left out and named: its book is incomplete, so nothing of it can tie
until the election is made (`tjs elect --pending`, then `tjs
run`). So are the accounts of every pooled group, also with `--account`:
while an election is pending the run runs no cross-account pass, so
their saved gains are not the figures the audit recomputes. An account
left out (no books yet, or an election) is listed in `--json` under
`not_audited` (`account`, `reason`: `no_books`, `elections_pending`,
`no_cross_account_pass`), with its line in `coverage_failures`.
`total_gain` is the computed dispositions' gain; an unknown-cost
disposition (reported by hand) is counted in `manual_reporting`
(`events`, `qty`, `proceeds`) only.

What it checks, and what it does not (the footer's `not traced` line;
`--details` and `--json`'s `scope` list it all):

- **The tie-out** compares each sale's quantity, proceeds, cost, gain,
  disallowed and permanently disallowed amounts, symbol, dates, account,
  direction and term with the saved gains file, each lot's acquired date
  and share (US: one Form 8949 row per lot), and the replacement lots;
  an unknown-cost sale's quantity, proceeds and names with its MANUAL
  REPORTING REQUIRED row. `--json`: each event's `tie_out.mismatched`
  (`field`, `engine`, `saved`, a lot's `lot`).
- **Source amounts:** each sale's net amount against its parsed broker
  row (`sale amounts`): times the rates file's rate when converted, and
  equal when already in the base currency; for a crypto book from the
  row the fill stage priced, for a futures fill its settled P/L. The
  audited securities' other trades (their buys, earlier sales and
  assignments, up to 31 days after the last audited sale) and the
  wash-sale / superficial-loss replacements the sales name are checked
  the same way (`pool trades`; `--json` `pool_trades`: `checked`,
  `tied`, `untraced`, `mismatched`, and `untraced_rows`: each row with
  no parsed row, with its `origin`). So are those securities' rows that
  are not trades: return of capital and the other cost adjustments,
  reinvested distributions, splits and ticker changes, corporate-action
  rows, transfers, openings, and their dividends, withholding and fees
  (`other pool rows`; a dividend's gross amount, as the merge nets the
  withholding into its net amount). The securities read are the audited
  ones under their earlier tickers too (a rename) and with the other
  securities of a corporate action of theirs (a spin-off's parent, a
  merger's old shares). `--json` `pool_other_rows`: `checked`, `tied`,
  `untraced`, `mismatched`, and `rows` (each row's `id`, `action`,
  `type`, `symbol`, `date`, `quantity`, `field` and `amount`, `ties`,
  and its parsed file, `source`, with the `.tt` `line`, or its
  `origin`). A `.tt` row shows its line beside it (`[line 4]`).
- **Rows with no parsed row** (`with no parsed row`) are re-run, not
  traced, and named with what made each (`--details`; `--json`
  `origin`): a `[[distributions]]` table, a `.tt` RENAME line (with its
  place, from `work/dated_events.state`), an `OPENING … cost=unknown`
  line, a corporate action, a transfer in or an in-kind move the run
  booked.
- **Not checked:** a sheltered account's rows; the rows of securities
  not audited and rows on no security (interest, account fees); how a
  parser read the broker's export; a crypto price the fill stage looked
  up; days held and the descriptive fields. The engine re-runs on the
  converted base books, so a clean audit says the saved figures are
  what those books give, and that the traced amounts are the broker's.

`--summary` prints one line per event (to find ids to dive into). The
filters: symbol prefixes, `--id` (a transaction id prefix), `--date`,
`--account` (display only: the computation stays blended). Each event
prints its row's own id unmasked, on purpose: it is the `--id` handle
(for Kraken the exchange's ledger txid, an exchange reference; see
[SECURITY.md](../SECURITY.md)). `--year Y` audits a locked year (its
`filed/Y.json`, or the lock `[settings] prior_year_record` names),
recomputed with the option timing and date basis the lock recorded, with
a note; `--all-years` audits every year on the books. `--no-trace` omits
the engine pool traces (they are not computed, and each `--json`
event's `trace` is empty; `--summary` computes none either),
`--no-color` turns colour off (`NO_COLOR` is honoured too), `--json`
prints the full audit.

#### tjs wash-sales

`tjs wash-sales [ACCOUNT] [--explain] [--json] [--details]`

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
[`tjs wash-radar`](#tjs-wash-radar).

```
$ tjs wash-sales
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
denied loss sits in open positions (`tjs list`, DEFERRED).

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
Country, tax-date and sheltered context come from the project. With an
ACCOUNT it shows that account's denials only, computed on the pooled
books as the table is (another account's buy still backs them). In
Canada the window and its DAY column count settlement dates (a SETTLED
column when they differ from the trade dates), and a denial backed by
more than one acquisition names each.

```bash
tjs wash-sales margin --explain     # full calculation trace for each wash sale
tjs wash-sales --explain            # all accounts
```

For tracing any other disposition, the standalone `taxjson-explain
--country canada --symbol SAMPLE work/<account>_base.json` remains
available (`--country usa` in a US project; `--account NAME` shows one
account's dispositions of a merged book). When a call option, a
warrant or another right to acquire counts as replacement property, and
which cases are only flagged:
[tax-rules.md](tax-rules.md#superficial-loss-s54); a registered or
affiliated holder's repurchase:
[tax-rules.md](tax-rules.md#registered-and-affiliated-holders-permanent-denial).

#### tjs tax-logic

`tjs tax-logic [--country canada|usa] [--ids] [--json]`

A short statement of every rule taxjson applies for the project's
country, a bullet per rule (its first sentence; its other sentences, and
a long sentence's clauses, are items under it; `--json` keeps each
statement whole), with the project's own settings filled in
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
rules never mix. Outside a project, pass `--country`: the rules with the
default settings (the title says so). The rules with
their sources: [tax-rules.md](tax-rules.md).

#### tjs edge-cases

`tjs edge-cases [ACCOUNT] [--margin DAYS] [--json] [--details]`

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
other date for reference (in the US, never the shares the loss's own
sale, or an earlier sale in its account, closed: shares closed by the
same sale never replace each other); and long calls bought inside a share loss's
window (s.54 "right to acquire": replacement property when still held on
day 30; a buy-to-close of a written call is not one).

The denied losses carried into next year are the engine's pools
(Canada) or lots (US) at the end of Dec 31 of the project year (`as_of`
in `--json`), so a later trade does not change them, and a December
loss whose replacement is bought in January is in none of them (the
window across Dec 31 lists it). The day is read on
the project's `tax_date` dates: a sale traded Dec 31 that settles in
January is next year's on settlement dates (the lots it sells still
held at the year end) and this year's on trade dates (gone); a gains
file written before a version that did this reads the engine's own
dates (settlement in Canada, trade in the US) and the project is told
which. A gains file written
before a version that kept them says its figure is at the end of the
books (`as_of` `end_of_data`) until `tjs run` rebuilds it. A loss
window across Dec 31 that the engine denied nothing of says why, from
what the books show (`allowed_by`: `buyback_exempt`, `filing_position`,
`in_kind_contribution`, `not_held_day30`, counting the loss's holder and
each holder that acquired in the window, as the engine does; in the US
`lot_sold`, every
purchase in the window being part of the lot the sale sold, or
`replacement_used`, some of them a replacement another loss's wash sale
used and the rest the lot sold; or `unknown`, which claims no reason).

Positions count opening balances, missing-history lines and splits;
income lands in the year income dating gives it (a Canadian trust's
December record date, payments in lieu and trust ROC included); crypto
rows are listed when their local date (`local_timezone`) and UTC date
fall in different years; written options are judged against the filed
locks, `prior_year_record` included, with the timing each lock records
(as `option-boundary` does); an option assigned on its expiry date is
described as an assignment. An option expiring at a year end is followed
through its series' splits and renames (the successor series is listed,
never the retired one), and its row says what the expiry day did with
the contracts held into it (ON EXPIRY DAY: those a priced trade closed,
those assigned or exercised, those that expire; `--json`: `held` before
the day, `traded`, and `units` and `outcome` for the row's own part; an
assigned part is a row of its own). An unreadable work file stops the command
with its name. A US project is explained with §1091: trade dates, no
still-held test (so no sales near day 30 and no held-on-day-30 figures),
long calls listed as warnings only, no window for crypto (property, not
a security), Form 8949 rather than Schedule 3, and no written-option
year boundary.

#### tjs check-dates

`tjs check-dates [ACCOUNT] [--all] [--json] [--details]`

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
options and `.tt` lines are exempt from the cycle check). A corporate
event booked as a trade row (a stock dividend, a $0 spin-off or
reorganisation leg) is dated the day the broker posts it, a weekend
included: it is checked only for a settle date before its date. An option
expiry row that settles after the contract's expiry date is a WARN (on a
settle basis it moves the expiry to the next year). A `.tt` line carries
its settlement date, so it may be up to one settlement cycle after
today. A parsed file that cannot be read is an ERROR (`unreadable`), so
is one the account's sources list names that `work/` lacks (`missing`:
re-run `tjs run`), and an invalid
`futures_settle` is refused as `run` refuses it. A date before 1900, or
after the year following the project's, is out of range, except with
exports shared by every year (`inputs_dir`) and in a year
`close-year` froze from them (its copy holds the same rows), or froze
as a single-folder project that `migrate --to-years` then moved into
its year folder (the `inputs/` beside it now shared): later
years' rows are expected there, counted in one Info line
(`--details`). ERROR for impossible
dates (exit 1), WARN for exchange holidays, NOTE for unusual but
explainable dates. It lists the first 10 rows per kind (`--all`: every
row). A `checklist` step.

#### tjs check-tt

`tjs check-tt [ACCOUNT | FILE.tt ...] [--account NAME] [--crypto | --equity] [--json]`

Reads every line of the `.tt` files with the parsers `tjs run` uses
and names each problem with its file and line. The run (and
`taxjson-convert-tt`) stop at the first malformed line; this goes on,
so one pass shows them all. An ERROR is a line the run would refuse: a
mistyped action (with the nearest valid one), a bad date, time or
number (a decimal comma), a missing field (named, with the action's
form), a stray column, a `SPLIT` ratio of 0 or below, an `OPENING` or
`JOURNAL` line in a crypto account, a `TRANSFER` or `ACQUIRED` line in
a `type = "taxable"` account that the run does not net away (a zero-net
pair, or a line an `OPENING` snapshot of the symbol replaces, is
fine), a symbol opened twice at an unknown cost, known-cost `OPENING`
snapshots of one symbol in one account with two dates, two `RENAME`
declarations that disagree, a file name the run refuses. A WARNING is
what the run would say and book anyway: a bare symbol in a share
account, a suffix that is no market, a total off its quantity x price
+/- fee, a last line with no line end, a `TRANSFER` line in a taxable
account with `transfers = true` (refused unless it cancels a TRANSFER
row of the exports, which check-tt does not read), a `NOTOWN` line in
an account whose transfer rows are never paired (sheltered, crypto,
`transfers = true`) or naming an account taxjson.toml does not declare
(the run says it undoes no pairing). Lines are numbered
as the run and an editor number them. With no item it checks
every account's `inputs/<account>/*.tt` (the shared folder with
`inputs_dir`); an account name, that account's files; a path ending in
`.tt`, that file, read as the account its folder names (`--account
NAME` to say which; outside a project `--crypto` or `--equity` says the
account's kind, else the bare-symbol warning is off). A file the run
reads for its account gets the account's checks across lines too (as
with the account's name), and a problem of another of its files that
names it is listed. A relative FILE is looked for from the shell's
folder, then from the project's (`-C DIR`), and a name check-tt prints
(`inputs/<account>/x.tt`) in the project's inputs folder. Every file
checked is listed with its account, its line, row and event counts and
its status: `ok`, or its errors and warnings; the problems follow, one
line each. Nothing is written; exit 1 on an error, 2 on a missing file
or account. A `checklist` step names it (".tt lines"): `checklist
--run` reads the files as check-tt does and marks the step attention
while a line the run refuses is left, naming the first.

#### tjs sanity

`tjs sanity [ACCOUNT | FILE.toml | ACCOUNT[+ACCOUNT]=FILE[+FILE] ...] [--tolerance N] [--cost-tolerance N] [--json] [--details]`

Cross-checks the open positions against holdings files produced outside
taxjson (a `[[holding]]` array of `symbol` / `quantity` tables), per
symbol; exit 1 on any discrepancy, and only then. An argument it cannot
use (neither an account nor a file, a malformed pair, an account or file
in two groups, the aggregate form without one of each) is a usage error,
exit 2. Holdings files are optional: a
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
  account's `holdings = [...]`), and `tjs run` finishes with the same
  check as a warning. An account without `holdings = [...]` is compared
  with its snapshots in the year's `holdings/` folder (`holdings_dir`):
  each file goes to the account whose `account` or `broker_accounts`
  holds its `[meta] broker_account` (else its `[meta] account`), else the
  account its name starts with, compared at its `as_of` (else
  `generated_at`) date (an RBC Holdings Export or IB statement saved
  there is read too, claimed by its name, `margin_holdings.csv`; any
  other file there is named as not read and counted on the
  `INCOMPLETE:` line); a snapshot newer than the books is compared with
  the latest books, with a note; one with only its `[meta]` (no
  `[[holding]]`) is an account that holds nothing. Several snapshots of
  the same broker account at different dates are compared one date at
  a time, each against the books on its own day (never summed); files
  of one account at different dates that do not each name their broker
  account (`[meta] broker_account`) are refused, since one broker
  account at two dates cannot then be told from two broker accounts
  (add `broker_account` to each, or keep one snapshot per broker
  account); a file with no date is of the books' last day, so beside
  one dated before it the files are of two dates too. Files of one
  date are summed; so are different broker
  accounts' files at different dates, compared with the latest books
  (the books do not say which broker account a trade was in), with a
  note naming what changed after the earliest date. Snapshots of one
  broker account at several dates beside a file that is not at each of
  them are refused; the error names a run of its own for a date that
  has every broker account's snapshot (a repeated `ACCOUNT=` merges
  into one group). An
  account with open positions and no holdings at all is listed as
  `UNCHECKED` (the checklist's sanity step is then attention, not done,
  also when no account has a holdings file). `INCOMPLETE` counts the
  accounts whose `holdings = [...]` file is missing or unreadable (and,
  in `--json`, `accounts_not_checked`) and a `holdings/` snapshot no
  account claims; both make `--json` `complete` false (when none of
  them could be compared, the error says to fix the paths, or to claim each
  snapshot for its account, exit 2: nothing was compared). With no
  arguments, an account whose books cannot be read, or that has inputs
  and no gains file (its run failed), stops the check with an Error
  naming it (exit 2, an input it cannot read, as for such an account
  named in the arguments and for a work/ with no gains file; exit 1
  stays a difference in positions): it is never left out of the
  coverage. So does an account of taxjson.toml with holdings and no
  inputs at all: the Error names its inputs folder (`inputs/NAME/` is
  missing or holds no export), since it has no books to compare.
- A relative FILE is looked for from the shell's folder, then from the
  project's (`tjs -C ~/taxes/2024 sanity ib=ib_positions.toml`
  finds the project's copy).

A holdings row with a quantity but no symbol, or a symbol with no
quantity, is refused, not skipped. So is a holdings TOML that is not a
whole snapshot (a `[meta] holdings_count` that is not its number of
`[[holding]]` tables, a contract size that is not a positive number) or
that has a date that is no calendar day: sanity stops with an Error
naming the file (exit 2) and compares nothing. A cost field it cannot
read only leaves that file's cost out (a note says so). Option rows
whose root the file spells differently (`RCI…` vs taxjson's `RCI.B…`) are matched through
the row's `underlying` field. A FILE may also be the broker's own
positions report (an IB Activity Statement with Open Positions, an RBC
Holdings Export: the same readers as `tjs opening`); one dated
before the books' last row is compared with the books' positions **on
its date**. `--tolerance` is the quantity tolerance (default 0.0001).
An s.86.1 spin-off's fraction the books hold on the snapshot's date
because its cash in lieu is paid after it (a December spin-off paid
in January) is no difference: a `fraction` line names it (`--json`:
`fractions_awaiting_cash_in_lieu`).

After the quantities, a **COST** section compares the books' cost with
the report's (`--cost-tolerance`, default 1.00 or 0.1%, whichever is
larger): Canada's filing ACB (s.47, pooled, superficial losses added)
against the broker's book value, or a foreign-currency cost against the
account's native-currency books. Each difference gets a reason:
`superficial-loss`, `pooled`, `return-of-capital`, `spin-off` (a
spin-off's election moved part of the parent's cost to the new shares),
`broker-fx`, `lot-basis` (US: `wash-sale`, `lot-method`) or
`unexplained`. A dividend
row that states its share count (`ON 500 SHS`) while the books held
another number on its record date is listed under **INCOME ON SHARES THE
BOOKS DO NOT HOLD** (a check of the books alone: it runs with no
holdings file too). Both sections are informational: the exit code
stays the quantity check's, and `--json` adds `cost`,
`cost_differences` and `income_share_mismatches`. The holdings formats
are in [settings.md](settings.md#holdings-toml).

#### tjs journals

`tjs journals [--account NAME] [--year YYYY] [--pending] [--json] [--details]`

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
and exits 1 when there is one (`tjs checklist` runs it). `--json` is
a stable schema ([settings.md](settings.md#tjs-journals---json)).
A damaged `work/cross_listings.state` (not JSON, another format, a
record list or record that cannot be read) is refused, naming why: run
`tjs run`.

#### tjs renames

`tjs renames [ACCOUNT] [--pending] [--json] [--details]`

Every ticker change in the books as a dated event: its date, its source
(a broker corporate-action row, IB's one contract id under two symbols,
a `.tt` RENAME or SPLIT line, a legacy ticker.map `RENAME` line), and
per account the position and book cost it carried (`tjs renames
ACCOUNT`: that account's only; shares an assigned put or an exercised
call delivered carry the option's premium as the books do; a sale
before it takes cost by the country's rule: Canada's average cost, the
US's oldest lots first; no superficial-loss or wash-sale adjustment: when
the gains files add a denied loss on the old symbol to a replacement's
cost by the rename date, the view says so with the amount, also
`denied_not_in_cost` in `--json`, and `tjs list` shows the adjusted
cost). Then every trade in
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

#### tjs spinoffs

`tjs spinoffs [ACCOUNT] [--json] [--details]`

Every spin-off in the books: parent and new security, ratio (counting a
fraction paid in cash: `10.5 for 105`), the election, the value per
share used (in its own currency: the broker's value in the broker's),
what was booked (income and the new shares' cost), the cash in lieu of
a fraction, the broker's own value when it reported one (the default
uses it when no value is given), and what is held now. In Canada
`taxable_deemed_dividend` is the default: a dividend equal to the new
shares' fair market value, which is also their cost;
`rollover_s_86_1` splits the parent's cost with no income, filed with
the return, for a foreign parent's spin-offs on CRA's list (give the CAD
cost moved to the new shares as `--hint allocated_acb_cad=`; a Canadian
parent's butterfly booked the same way has nothing to file). In a US project the
elections are `taxable_distribution_301` (§301 income at FMV) and
`tax_free_355` (basis moved per Form 8937, `--hint allocated_acb=`). A
sheltered account's spin-off booked without asking shows
`sheltered_default` ($0 cost for the new shares). It flags a taxable
spin-off booked at $0, a basis-allocating election with no allocated
cost, a missing election or an ignored event; exit 1 when a taxable one
needs attention. The rules:
[tax-rules.md](tax-rules.md#spin-offs-s861-rollover-or-taxable-deemed-dividend).

#### tjs splits

`tjs splits [ACCOUNT] [--json] [--details]`

Every split, consolidation and rename with the holdings just before and
after. It notes a split recorded twice within a week (two sources:
TWO-SOURCES, one event applied once, on the earliest date) and flags
the same ratio again 8 to 10 days apart (TWICE?: both are applied), a
no-op row, a result with a fractional share (expect cash in lieu; one
whose cash-in-lieu sale of the fraction is booked within 60 days is
noted CIL, not counted as needing attention), and
events that also mention a cash or return-of-capital leg. Exit 1 on a
likely double application (TWICE?). `splits`, `spinoffs` and `renames`
need the books (`tjs run` first) and refuse a damaged one, naming
the file and the row.

### Maintainer

The release commands, for the machine taxjson is developed on: `tjs
help` lists them only on a development checkout (`tjs help --all`
lists them everywhere), and they run everywhere. They read a git
checkout of taxjson, never a tax project: `-C` does not apply to them.

#### tjs channels

`tjs channels [all] [--offline] [--json]`

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

#### tjs deploy

`tjs deploy [vX.Y.Z]`

Development machine only: put the newest release (or the one named) on
this machine's production copy now, through the installer's upgrade
path. The remembered channel is kept; a channel never moves an install
backwards, so the copy stays there until its channel passes it. Refused
(exit 2) where no development checkout is found.

#### tjs promote

`tjs promote [vX.Y.Z] [stable|beta]`

Development machine only: point `stable` (the default) or `beta` at a
release, through `scripts/promote.sh`: the tag must exist, the checkout
must be on `main` with a clean `channels.json`, and moving a channel
backwards asks first. It commits "Promote vX.Y.Z to stable", lands it on
`main` (`scripts/land.sh`), then tags that commit
`channels-YYYYMMDDTHHMMSSZ` (UTC) and pushes the tag: the fast-lane tag
that asks the publisher to copy `channels.json` to the public repo on its
next run. No new release tag, no rebuild. Without a version: the release
this machine's production copy runs. Refused (exit 2) where no development checkout is
found. The release process: [releasing.md](releasing.md).

### Tools

#### tjs redact

`tjs redact [FILE ...] [--out DIR] [--also REGEX] [--no-denylist] [--force] [--check] [--details]`

Strips the account numbers, names and contact details it recognises
from broker exports while keeping every row shape, so a statement can be
shared as a parser sample or bug report. It is pattern-based, not a
guarantee: **review the output before sharing**; the report lists the
free-text lines to read (among them a description that starts with a
bare FROM or TO and a name, `FROM SAM SAMPLE`: listed, not replaced).

**With no FILE**, run in a project (or `-C DIR`): it copies the whole
`inputs/` folder to `inputs_redact/` (or `--out DIR`; a relative DIR is
taken from the shell's folder, with `-C` too), with the same
folders and every text file (CSVs, `.tt`, `.json` / `.toml` sidecars,
`README.txt`), and redacts the COPY. You get two folders: the originals
untouched, and a redacted inputs tree `tjs run` can still parse
(brokers are detected by content). Ids get the same placeholder in every
file and file name; a file or folder name holding an account number is
renamed (`55500001.csv` becomes a same-shape placeholder such as
`99900001.csv`, kept unique) <!-- pii-ok: synthetic ids --> and the
old → new map is printed on the console only, never written into the
copy. An e-mail address in a name gets the stand-in address it gets in
a file; a name that reads like a person's (`Jordan Sampleperson -
Questrade 2024.csv`) is listed in a warning to rename by hand, not
renamed (an account's folder would no longer match the configuration).
Binary files (`.xlsx`, `.pdf`, `.zip`) are not copied: each is
named in a warning to handle by hand. Hidden files and Office lock files
are left out; a symlinked folder is not followed, and a file symlink
pointing outside `inputs/` is not copied (named in a warning). An
existing `inputs_redact/` is replaced only with `--force` (built in a
temporary folder beside it, then renamed into place; the old copy is
deleted, not kept) and only when `tjs redact` made it (its
`.taxjson-redacted` marker); a symlink there is refused. `--check`
reports per file what would be replaced (counts only) and writes
nothing. The closing line names the copy as `inputs_redact/` in the
project's own folder, and by its full path otherwise (`-C DIR`, `--out
DIR`). `tjs run` never reads `inputs_redact/`, and `tjs
init`'s `.gitignore` lists it.

In a year folder of one folder of exports for every year
(`inputs_dir`), `inputs_redact/` is written in the year folder as that
year's single-folder project: `inputs/` (the shared exports, a file link
allowed anywhere in the folder holding the years, then the year's own
`inputs/slips/`), `holdings/`, `taxjson.toml` without the folder
settings, and `ticker.map` (a link to a file in the folder holding the
project is copied as that file; one pointing elsewhere is named in a
warning, not followed); and `prior/<name>`, the close-year record
of an earlier year the estimate carries its losses from (the latest
before the year: the one `prior_year_record` names, or the year's own
`filed/<year>.json`), with the copy's `prior_year_record` naming it (a
`prior_year_record` naming no file is commented out in the copy). Each
string of that record naming a folder of this computer (an absolute or
home path, such as the shared `inputs_dir` its year was frozen from, or
a `../` path that leaves the folder holding the years or passes through
a folder with a name-like name) becomes `<redacted path>` (counted as a
folder path of this computer); the figures the copy's estimate reads
from it are kept. The
ids are replaced consistently in all of
them (a holdings file's `[meta] account` and name included, and the
configuration's own `account`, `broker_accounts` and `query_id`, a short
one only in its own key's value), as are
e-mail addresses and denylist / `--also` matches in every file and
contact details in the settings files' comments (a comment that still
holds name-like words or a long number is listed with the lines to
read, as an export's free text is). The copied
`taxjson.toml` must read as TOML before anything is written; `tjs -C
2025/inputs_redact run` runs it (and writes `work/` and `reports/` inside
the copy, whose files hold this computer's folder paths: delete them
before sharing it). `tjs -C 2025/inputs_redact redact --check`
checks such a copy whole: every file it is made of (inputs/, holdings/,
prior/ and the settings files), and anything else at its top, such as
what a run of it wrote, is named as a finding (exit 1), as is a folder
path of this computer left in its `prior/` record (a copy an older
taxjson wrote), with its line. An id only `taxjson.toml` names is
replaced in the exports of a single-folder project too.

**With FILEs**: it strips the account numbers, names and contact details
it recognises (plus wallet addresses and exchange transaction ids, and
anything on the private denylist) while keeping every row shape, with
same-shape placeholders consistent across every file of one run (so a
redacted Kraken trades + ledgers set still links up); a placeholder is
never another id the input holds, so two accounts never share one. It writes
`NAME.redacted.EXT` beside each file (or into `--out DIR`), never touches
the input, and refuses `.xlsx` and other binary input (export CSV
first). `tjs run` does not read such a copy in a folder that also
holds an export (a warning names it: read too, its rows would be booked
twice); a folder of redacted copies alone is read. `--check` exits 1 when it finds something, an account id or
denylisted word in the file NAME included. A redacted copy (a
`NAME.redacted.EXT` file, or a file in a tree holding the
`.taxjson-redacted` marker) is skipped when writing; `--check` reads it,
taking its own placeholders (`99900001`, `U9990001`, the pseudonyms and
the stand-in e-mail address) as redacted already, so it passes a clean
copy and finds what was added to one.

The private denylist is `~/.config/taxjson/pii-denylist` (or
`$TAXJSON_PII_DENYLIST`); `--no-denylist` ignores it, and `--also REGEX`
adds a pattern (repeatable). The denylist must be UTF-8 text (a BOM is
fine); a UTF-16, non-UTF-8, unreadable or directory denylist, or an
invalid pattern, stops the run (exit 2, nothing written).

#### tjs help

`tjs help [COMMAND] [--all]`

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
the ticker. `tjs renames` lists such trades, `tjs run` prints an
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
`tjs renames` and stops `run --strict`. An undated rule (`GLOBAL OLD
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
for ROC) and `tjs run` converts them into ACB adjustments for every
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
add) with a warning. `tjs roc` and `tjs roc-sum` show the
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
dividend totals, and the Canadian estimate (`tjs estimate`, and
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
