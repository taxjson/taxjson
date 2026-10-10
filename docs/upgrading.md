# Upgrading

taxjson is pre-1.0: a release can change a file's format, a command or a
project's layout. This page says how to upgrade, what to do after, and
every change that asks something of you, release by release. The full
history is [CHANGELOG.md](../CHANGELOG.md).

## How to upgrade

Re-run the installer line you installed with:

```bash
bash -c "$(curl -fsSL https://taxjson.com/install.sh)"
tjs --version
```

It upgrades along the channel you installed from and keeps your choice of
the fetch plugin (`--without-fetch` is remembered). Your projects are
never touched by an upgrade; the next command you run in one tells you if
it needs something (a `tjs migrate`, a `tjs update-tobase-map`).

**Channels.** `stable` (the default) is the release that has held up;
`beta` the one being tried; `latest` the newest release, as soon as it is
tagged; `dev` the `main` branch, unreleased; `vX.Y.Z` exactly that
release (a pin — also how you go back). `stable` and `beta` are named in
[`channels.json`](../channels.json) on `main`; `latest` is always the
newest `vX.Y.Z` tag. Pick one with `--channel NAME` (after `_` on the
installer line: `bash -c "$(curl -fsSL https://taxjson.com/install.sh)"
_ --channel beta`) or `TAXJSON_CHANNEL=NAME`; the installer prints
`channel stable → release vX.Y.Z` and remembers the choice in
`~/.config/taxjson/channel`, so re-running it upgrades along the same
channel. A channel never moves an install backwards (name a version to
go back). Only an annotated release tag on `main`'s history is installed.
`tjs channels` shows where every channel points and what this machine
runs; `TAXJSON_DRY_RUN=1` prints what the installer would pick and
changes nothing. The installer leaves alone a `taxjson` or `tjs` in
`~/.local/bin` that is not its own link, with a note. How releases are
cut: [releasing.md](releasing.md).

## After an upgrade

In each project you keep working in (each year folder):

1. Run `tjs checklist`. Its set-up section says when the installed
   taxjson is newer than the project's last run, and names any step the
   upgrade opened (a migrate, a tobase.map update).
2. `tjs run`. A command that stops with "run `taxjson migrate`" means an
   old file format: `tjs migrate --dry-run`, then `tjs migrate`.
3. Canada: `tjs update-tobase-map` lists what a newer interlisted list
   changes; `--write` applies it.
4. A filed year: `tjs check-filed` (or the year's `tjs run`) says whether
   the new release moved any filed figure (`filed <year> DRIFTED`). An
   engine fix that moves a filed year is a candidate for an amendment, not
   a silent change.

## Changes that ask something of you

Newest first. Each says what changed and what to do. Releases not listed
needed nothing.

### v0.28.2

- **Chained commands:** a command name after a command that can take it
  as its own argument is now that argument when it names one of the
  project's accounts (`tjs events sum` with an `[accounts.sum]`), a folder
  (`tjs init sum`) or a symbol (`tjs audit sum`); it chained before. To
  chain after such a command, write `--` between the two: `tjs events --
  sum`. `tjs run sum`, `tjs fetch run` and every chain whose word is no
  account of the project work as before. The value of `-C` is always the
  project folder, even one named like a command (`tjs -C run run sum`).

### v0.28.0

- **Shorter command output.** A command's default output is the
  essentials: a legend before each table, then only the lines to act on
  (starting `! `) or not to miss, one line each, naming the command with
  the detail. Every explanation, caveat and citation is still there
  behind `--details` (`tjs sum --details`, `tjs run --details`);
  `tjs checklist` shows the steps that need attention and the next one
  (`tjs checklist --all` is the full list); `tjs estimate` shows the
  estimate alone (`--details` puts the gains table back in front);
  `tjs years` is a table (`--details` prints each year in words).
  `--json`, the `reports/` files and the run's `work/*.diag` are
  unchanged. A script that read a note from the text output: read
  `--json`, or add `--details`. [output-style.md](output-style.md)
  describes the format, and [troubleshooting.md](troubleshooting.md)
  quotes the one-line forms.
- **`tjs close-year` asks first** when a checklist step before the lock
  needs attention: it lists them and asks on a terminal; without a
  terminal (a script, a pipe) it refuses unless you pass `--yes`.
- **`tjs init` in an empty folder named like a year refuses** (it would
  have built `2026/2026/`): run it in the folder above —
  `mkdir -p ~/taxes && cd ~/taxes && tjs init --country canada && cd 2026`.
  `tjs init --single` still makes one folder where you are.
- **`option_grant_timing_since` (Canada):** a new `taxjson.toml` leaves
  it commented out unless another year folder beside it sets it, and
  `tjs run` warns only when the books hold a written option. A new
  warning says when it is later than `year` ("option_grant_timing_since
  = 2026 is after year = 2025"): usually `year` was lowered with the key
  left as an older `tjs init` wrote it — set it to the first year you
  file under grant timing ([troubleshooting.md](troubleshooting.md)).
- **The help page's Maintainer group.** The release commands
  (`channels`, `deploy`, `promote`) are listed only on a development
  checkout; `tjs help --all` lists them everywhere, and they still run.
- **The docs moved.** README.md is a front page; its reference sections
  moved to their own pages: [commands.md](commands.md) (every command),
  [brokers.md](brokers.md) (getting each broker's files, the generic
  importer, the fetch plugin, slips), [settings.md](settings.md) (project
  files and mapping reference), [tax-rules.md](tax-rules.md) (the rules),
  [limits.md](limits.md) (what taxjson does not do),
  [glossary.md](glossary.md) and this page. KNOWN_ISSUES.md now lists open
  bugs only. A bookmark to a README section lands on the front page; the
  section is in the page above.
- New, nothing to do: `tjs init --demo ~/taxjson-demo` makes a project
  of made-up exports to try first
  ([getting-started.md](getting-started.md#1-install)).

### v0.27.1

- **One shared `tobase.map` (Canada, one folder of exports for every
  year).** The interlisted pairs file sits beside the year folders, and
  each year reads it through `[settings] tobase_map = "../tobase.map"`.
  A project made by v0.27.0 has a copy in each year folder: run
  `tjs migrate` in the folder that holds the years. Identical copies
  become one at once (each kept as `tobase.map.bak`); copies that differ
  are listed with your own lines only an older copy has, and need
  `tjs migrate --write` (the newest year's file is kept). A year folder
  with the setting and a tobase.map of its own is refused, naming both.
- tobase.map no longer carries `DISTINCT` lines for Canadian depositary
  receipts (CDRs): `tjs update-tobase-map --write` retracts the ones
  v0.27.0 wrote. Nothing in the books changes.

### v0.27.0

- **Missing history is a `.tt` line; `missing_history.json` is no longer
  read.** Units held before your files start, at an unknown cost, are
  `OPENING <date> <SYMBOL> <qty> cost=unknown [reason="..."]` lines in the
  account's inputs (`inputs/<account>/missing_history.tt`). A project that
  still has a `missing_history.json` (or the older `phantoms.json`) is
  refused by every command until you run `tjs migrate` (`--dry-run`
  first): each entry becomes a line sized as that project's last run
  opened it; with one folder of exports for every year the year folders'
  files are merged (entries the years disagree on are listed and written
  only with `tjs migrate --write`), and each file is renamed
  `missing_history.json.migrated`.
- `tjs find-missing-history --write-missing-history` no longer takes a
  file name; it writes those lines into `missing_history.tt`. The stage
  tools' `--incomplete-history` now names the project folder.
- Canada: a new project gets a `tobase.map` (the interlisted pairs that
  ship with taxjson); an older project is offered it by
  `tjs update-tobase-map`, which shows which pairs would change your books
  before it writes anything.

### v0.26.0

- **`tjs init DIR` makes one folder of exports for every year.** It
  creates `DIR/inputs/<account>/` (every year's broker exports) and the
  year's project in `DIR/<year>/`. Run commands in the year folder:
  `tjs init ~/taxes && tjs -C ~/taxes run` becomes
  `tjs -C ~/taxes/2025 run`. `tjs init --single DIR` keeps the one-folder
  layout of earlier releases. Existing projects are unchanged;
  `tjs migrate --to-years` converts one (`--dry-run` first).
- In the folder that holds the year folders, a command that needs one
  year's project is refused, naming the year folders (`cd 2025`, or
  `tjs -C 2025 ...`).
- The fetch plugin's `--positions` snapshot moved from `work/` to the
  year's `holdings/` folder, where `tjs sanity` finds it. A fetcher
  plugin of your own: downloads go to `FetchRequest.inputs`
  ([CONTRIBUTING.md](../CONTRIBUTING.md#adding-a-broker-fetcher-taxjson-fetch)).

### v0.25.0

- **Two commands were removed.** The first-run guide command is replaced
  by `tjs checklist` (every step from install to filing), and the old
  map-and-holdings scanning command by `tjs tips` (advice for next year)
  and `tjs ticker-map --suggest` (map lines to add or delete). The
  v0.25.0 section of the CHANGELOG names both. Scripts that called them
  need the new commands.
- `tjs checklist --json` is `schema_version` 2: a step's `stage` is now
  `section` ([settings.md](settings.md#taxjson-checklist---json)).
- `tjs tips` exits 0 with or without tips (2 when the project cannot be
  read), and its `--json` is `schema_version` 2.

### v0.19.0

- The installer installs the taxjson-fetch plugin by default. An install
  that should not have it: re-run the installer with `_ --without-fetch`
  (remembered for later upgrades).

### v0.17.0

- **One mapping file; year data in `taxjson.toml`.** The old per-purpose
  files — `yf_ticker.map`, `crypto_ticker.map`,
  `ticker_extraction_overrides.txt`, `t1135.map` (now `QUOTE`, `CRYPTO`,
  `EXTRACT` and `T1135` lines in `ticker.map`), `claimed_losses.txt`,
  `amt_carryover.txt`, `capital_gains_dividends.map`, `distributions.map`
  (now `[carryover] claimed`, `[estimate] amt_carryover`,
  `[[capital_gains_dividends]]` and `[[distributions]]` in `taxjson.toml`)
  — stop every command until `tjs migrate` converts them (`--dry-run`
  first); each old file is renamed `<name>.migrated`.
- The local web UI (the `serve` command, the `[web]` extra) and the
  watchlist exports (`taxjson-export --seekingalpha`, `--fastgraph`,
  `--tradingview`, the `reports/exports/` files) were removed. A
  `TRADINGVIEW` line in ticker.map is ignored with a note: delete it.
- `taxjson fetch` moved into the separate taxjson-fetch plugin (installed
  by default since v0.19.0).
- `taxjson` with no command prints the help page (exit 0, was 2).

### v0.15.0

- The Qt desktop app (the `gui` command, the `[gui]` extra) was removed.
  Every report it showed is a command.

## Problems an upgrade fixes

A message you see on an older release may be a bug a later release fixed.
Check `tjs --version` against the list below; if the fix is newer, upgrade
first. Problems that still need something from you are in
[troubleshooting.md](troubleshooting.md).

#### Fixed in v0.28.0
- The first run of a recent project downloads exchange rates back to 2000 ("Info: FX USD→CAD: Bank of Canada Valet for 2,400 dates, Yahoo fallback for …") — fixed in v0.28.0: the window starts a few days before the earliest date in your files
- "Warning: the same broker account (#ab12cd) feeds two taxjson accounts, qt and wb" for the exports of two different brokers that print the same account number — fixed in v0.28.0
- `tjs tips --online` advises "Add `DISTINCT QZD.US QZD.TO` to ticker.map to record this and silence the pair" for a Canadian depositary receipt (look-alike listings are never joined; a `DISTINCT` line already written is harmless) — fixed in v0.28.0

#### Fixed in v0.27.1
- "Info: 1 position(s) go short in margin's data (QZQ.TO): booked as short sales closed by a later purchase" although `missing_history.tt` opens the other listing of a TOBASE pair (QZQ.US) — fixed in v0.27.1
- "warning: inputs/margin/missing_history.tt:1 opens QZQ.TO / margin, but no row in the data has that symbol and account — nothing was applied" for a holding you simply kept (no later trade of it) — fixed in v0.27.1
- `tjs slip-audit --import-cra` hangs on a PDF instead of stopping with "pdftotext took over 60 s" — fixed in v0.27.1

#### Fixed in v0.27.0
- `tjs ticker-map --suggest` lists a pair to verify such as `TOBASE QZOR.US QZOR.TO` for a US share you hold (never add that line: it pools two companies) — fixed in v0.27.0
- `tjs t1135` lists a TSX-held `BEP.UN` (booked as `BEP.US`) under the USA, or warns "`T1135 BEP.UN.TO` matches no symbol in the books" — fixed in v0.27.0; then run `tjs update-tobase-map --write`
- `tjs update-tobase-map --write` adds back a master line you deleted, or duplicates a line you edited — fixed in v0.27.0
- `tjs update-tobase-map` lists no pair for a TSX class share or trust unit (`QZK.B.TO`, `QZR.UN.TO`) that trades over the counter in the US — fixed in v0.27.0; then run `tjs update-tobase-map --write`
- `tjs sanity`: "INCOME ON SHARES THE BOOKS DO NOT HOLD" for a dividend paid after you sold (its record date was before the sale) — fixed in v0.27.0
- `tjs sanity`: "MISSING_IN_HOLDINGS" for a position whose shares bought before your data were sold, although missing history covers them — fixed in v0.27.0

#### Fixed in v0.26.1
- With `TAXJSON_OFFLINE=1` in a US project: "taxjson-to-base-curr needs the [fx] extra for a USD target (Yahoo Finance)", then "Error: stopped at fetching currency rates (CAD, USD) (exit 1)" — fixed in v0.26.1

#### Fixed in v0.26.0
- `tjs check-dates` in a year folder: "out-of-range: … — far outside the project year 2024" for every row of a later year in the shared exports — fixed in v0.26.0

#### Fixed in v0.25.0
- `tjs run` overwrote a file outside the project that a symlink in `work/` (such as `work/loss_overrides.json`) pointed at — fixed in v0.25.0; on an older release, remove the symlinks from `work/`
- IB: "Warning: QZK.US: the broker cancelled (Ca) a trade of 6 @ 10 on …, but the original fill is in none of this account's inputs" after "Info: the broker cancelled (Ca) 4 of the QZK.US order of 10 @ 10 … The order is booked as 6" (an order cancelled in two parts booked a sale that never happened) — fixed in v0.25.0
- IB: "Warning: QZK.US: the broker cancelled (Ca) a trade of 440 @ 10 on …, but the original fill is in none of this account's inputs" after "… cancelled (Ca) 40 of the QZK.US order of 440 @ 10" (a part cancelled, then the whole order) — fixed in v0.25.0
- `tjs fetch`: an empty `questrade_2025.csv` (or `ib_flex.csv`, or the Questrade token file), "FileNotFoundError: … questrade_2025.csv.part", or "Questrade auth failed" right after another fetch succeeded, when two fetches ran at once — fixed in v0.25.0
- `tjs fetch --trim-overlap` copied the original CSV outside the project, to where an `<export>.bak` symlink pointed — fixed in v0.25.0
- `tjs slip-audit --import-cra`: a Webull T5 "from CI INVESTMENT SERVICES INC./CI SERVICES D'INVESTISSEMENT INC" is not imported: "no broker in the books by the issuer's name" — fixed in v0.25.0

#### Fixed in v0.24.2
- "Warning: 1 position at a $0 cost (1 still held): SPNW.TO (margin). Run `taxjson find-missing-history`" for a spin-off you elected with `fmv_per_share=0` (now an Info: the $0 value you declared) — fixed in v0.24.2
- "Warning: ATTENTION: 99900001.csv: RBC symbol QZOLD (USD) looks renamed to QZNEW" (or the Questrade or Webull hint) while a `.tt` line `RENAME <date> QZOLD.US QZNEW.US` already declares the change — fixed in v0.24.2
- `run --fast` keeps the old result after an update that changed only taxjson's market data (no "Rebuilding everything: taxjson's code changed" line) — fixed in v0.24.2; on an older release, run once without `--fast` after updating
- `tjs elect` says "No elections recorded: lira" although the run booked a spin-off there ("sheltered account lira: spin-off … booked at $0 cost") — fixed in v0.24.2
- `tjs form-export` (or `tjs audit`) shows an ALLOWLOSS sale as an ordinary loss: no note, "disallowed 0.00", and `tjs wash-sales --explain` says "no matching gains found" — fixed in v0.24.2
- `tjs slip-audit`: a T5 typed for an IB account and IB's dividends report for the same account counted twice ("Canadian dividends … slip" twice the books) — fixed in v0.24.2
- `taxjson-form-export --csv s3.csv`: "cannot write --csv s3.csv: [Errno 17] File exists: 's3.csv.part'" — fixed in v0.24.2; on an older release, delete the leftover `.part` file once no export is running

#### Fixed in v0.24.1
- Questrade: "Info: Questrade internal symbol codes resolved (1): D0000001 → QZD.US (name match: …)" for a spun-off warrant, then "Warning: Questrade spinoff chain on … is booked under Questrade's INTERNAL code D0000001.US, not a ticker" — fixed in v0.24.1
- No "possible superficial loss across listings" warning for a pair whose own broker names both listings alike, because another account's export names one listing with other share wording ("SUBORD VTG SHS") — fixed in v0.24.1
- `tjs ticker-map --suggest` lists "QZE.US and QZE.TO share their letters but the names are not equal … — verify" for two companies that IB lists under one bare symbol — fixed in v0.24.1
- `tjs ticker-map --suggest` asks to verify a depositary receipt (CDR) or another company that uses the same letters ("if they are one security add `TOBASE QZX.US QZX.TO` …; if not, `DISTINCT QZX.US QZX.TO`"), or `tjs tips` says "US-LISTING … hold QZX.TO instead" — fixed in v0.24.1
- A broker journal split over several rows (one reference: 1000 out of QZD.TO, 600 and 400 into QZD.U.TO) is not joined, or its sale reads as missing history — fixed in v0.24.1
- A correct `TOBASE QZB.US QZA.TO` for a move from IB to Questrade left "Info: 1 position(s) go short in acct's data (QZA.US)" (in a registered account "Warning: Short position: QZA.US …"), or a QZA.US position the broker never held — fixed in v0.24.1
- Questrade or RBC: "Warning: Row check: … BUYSELL QZPIPE.US …: net_amount -44.98 is far from qty*price = 62.90 …" on a dividend reinvestment priced in the other currency (`REINV@C$…` on a USD row) — fixed in v0.24.1
- Questrade: "Warning: Short position: QZP.TO (lira): a registered account (TFSA/RRSP) cannot be short" after a dividend reinvestment (REI) on a USD row — fixed in v0.24.1

#### Fixed in v0.24.0
- "Info: 1 position(s) go short in margin's data (QZN.TO) … Until the purchase is supplied their gain is in no total" for a short sale closed within the year, or `tjs find-missing-history` counting its cover as a sale (InYrSales 2) — fixed in v0.24.0
- A transfer-in from outside your books lost its cost (no "booked at the ACB the broker states on the row" line), or an in-kind move was booked, after a journal between two listings in another account — fixed in v0.24.0
- "Info: 1 position(s) go short in b's data (QZD.U.TO)" after a Norbert's gambit in one account while another account received the same listing by transfer that day — fixed in v0.24.0
- `tjs sanity`: "QZD.U.TO MISSING_IN_TAXJSON" (or `QTY_MISMATCH` on QZD.TO) for a listing the run joined by its transfer journal — fixed in v0.24.0
- A ticker that changed twice (`RENAME` A to B, then B to C) leaves the position in B, and C goes short — fixed in v0.24.0
- "Error: SPLIT QZA.TO→QZB.TO: rename would merge a LONG position into an existing SHORT pool" with a `.tt` `RENAME … late=fold` dated before the broker's own rename row — fixed in v0.24.0
- Canada: a sale of the old ticker on the day of an evening rename row (IB's 20:25 corporate actions, a timed `.tt` SPLIT) goes short although `late=fold` is declared — fixed in v0.24.0
- A SPLIT QZA→QZB and a `RENAME` QZB→QZC on the same date: "RENAME 2025-04-01 QZB.TO QZC.TO books nothing" and QZC goes short — fixed in v0.24.0
- "RENAME 2025-04-01 QZA.TO QZB.TO books nothing" when ticker.map respells the rows (`GLOBAL QZAX.TO QZA.TO`) — fixed in v0.24.0
- A `.tt` RENAME in a securities account moved a coin in a crypto account (or the reverse) — fixed in v0.24.0
- Questrade with `transfers = true`: a currency journal (Norbert's gambit) not joined and the USD sale short, or "joined as one security by their transfer journal: QZD.TO ↔ QZD.U.TO (transfer …)" for it — fixed in v0.24.0
- `run --fast` keeps an IB ticker change dated from another account's IB statement after that statement is removed — fixed in v0.24.0; on an older release, run without `--fast`
- `tjs ticker-map --suggest`: "… not joined automatically: another name of the listings states another share or corporate form (…)" for a broker's own journal (RBC Norbert's gambit, IB InterDepot) — fixed in v0.24.0
- `tjs ticker-map --suggest`: "… not joined automatically: the legs pair with more than one other leg" (two equal gambits days apart) or "… a listing pairs with two other listings" for a broker's own journals — fixed in v0.24.0
- A move between brokers that changes the listing (RBC `TFO` out of QZP.TO, IB `ATON` into QZP.US) is not joined when a weekend falls between the two legs — fixed in v0.24.0

#### Fixed in v0.23.1
- "Error: cannot detect broker for inputs/qt/99900001.csv" (or "Questrade export is missing required column(s) 'Transaction Date'") for a file whose header looks right: it was saved with two byte-order marks — fixed in v0.23.1; on an older release, re-export the file or save it as plain UTF-8

#### Fixed in v0.23.0
- A traceback ending "AttributeError: 'NoneType' object has no attribute 'write'" after `tjs sum >&-`, or `tjs run --no-input 2>&-` stops with exit 1 and no output — fixed in v0.23.0; on an older release, redirect to `/dev/null` instead of closing the stream
- An account number inside a security name, e.g. a `tjs ticker-map --suggest` line or a join warning naming 'QZX CORP TFR TO …' — fixed in v0.23.0
