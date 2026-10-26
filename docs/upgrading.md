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
stops: an existing install only has its tags fetched, and a missing one
is not created (the channel is resolved in a temporary clone). The installer leaves alone a `taxjson` or `tjs` in
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

### v0.36.1

- **`tjs edge-cases` reads the denied losses held at the year end from
  the year-end pools.** Re-run `tjs run` once: a gains file written
  before says "held at the end of the books, which may be after <year>"
  until it is rebuilt. Canada's gains files gain `inventory_year_end`
  (the pools at Dec 31; nothing filed changes), and the report's JSON
  gains `as_of` on each deferred row and `allowed_by` on each loss
  window across Dec 31.

### v0.36.0

- **`tjs checklist --json` is now `schema_version` 3, and `tjs years
  --json` `schema_version` 2.** v0.35.0 renamed the `skipped` status
  and mark to `forced` (in `counts`, a step's `effective` and
  `override`, and the years' `marks`) under the old numbers. Nothing
  else changed: a script that checks the version accepts the new
  number and reads `forced` where v0.34 wrote `skipped`.
- **A `.tt` `NOTOWN` line undoes a pairing that is not a move of your
  own**. A transfer-out and a transfer-in of the same
  stock and quantity in two taxable accounts (within 10 days, or 11 to
  45 days apart with no other candidate) are still paired as one move,
  and nothing changes without the line. When they are not one move
  (`tjs transfers` shows the transfer-in as `own_move`), add `NOTOWN
  <date> <symbol> <qty> <from-account> <from-date>` to a `.tt` file in
  the receiving account's folder (the slow pairing's `Own-account move`
  Info line prints it with `tjs run --details`) and run again: the
  transfer-in is booked as one from outside your books, so the ACB or
  basis of that stock and the gains on its later sales change.
- **Check a redacted copy again before sharing it.** `tjs redact
  --check` now reads a `NAME.redacted.EXT` copy (it skipped one, exit
  0, whatever it held) and passes a clean one; in a year folder it lists
  the comments of taxjson.toml and ticker.map that still hold a name
  ("Read line N of the copy"), which it kept without a word. Run `tjs
  redact --check` on a copy made by an older release, read the lines it
  lists, and fix them before sharing.
- **`tjs check-tt` names more of the lines `tjs run` refuses, and exits
  1 on them.** A `TRANSFER` or `ACQUIRED` line in a taxable account
  that the run does not net away, a `SPLIT` ratio of 0 or below, and,
  for a file named by its path, the account's checks across lines (a
  symbol opened twice at an unknown cost, a file name the run refuses)
  used to say "Every line parses.". Run `tjs check-tt` once after
  upgrading and fix what it names; `tjs checklist --run` now marks the
  ".tt lines" step attention while such a line is left. The closing
  line reads "N problems the run refuses" (was "N lines cannot be
  read"), and a FILE argument is also looked for in the project (`-C
  DIR`) and its inputs folder.
- **`tjs new-year 2027` refuses while 2026 has no year folder.** It used
  to start 2027 from the newest earlier year (2025), pointing
  `prior_year_record` at 2025's lock, so the carryforwards skipped
  whatever 2026 used. Run `tjs new-year 2026` first, then `tjs new-year
  2027`. A year an older release started that way: check its
  `prior_year_record` (`grep prior_year_record 2027/taxjson.toml`).
- **A filed year that names the shared folders again is a `Warning:`, and
  `tjs run --strict` stops on it.** A year closed with its inputs frozen
  whose `taxjson.toml` has a live `inputs_dir` or `tobase_map` again reads
  the shared exports, not its frozen copy: it was an `Info:` followed by
  OK. Delete those lines from its `[settings]` (`tjs years` names the
  year). A lock of an earlier year kept in a later year's `filed/` is no
  longer told to delete them: that year reads the shared exports rightly.

- **A usage error exits 2 in every command.** A malformed `--date` or
  time period (`tjs events 1x`), or an account name the project does not
  have or has no books for, exited 1 in some commands (`wash-radar`,
  `list`, `events`, `trades`, `divs-sum` and the other transaction
  views, `winners`, `stats`, `leaps`, `option-yield`, `ccd-sum`, `sum`,
  `fees-sum`, `wash-sales`, `find-missing-history`, `transfers`,
  `journals`) and 2 in others. All exit 2 now, as the exit-code table
  says; exit 1 stays a command's failure or finding. A script that
  tested for 1 there tests for 2.
- **US `tjs harvest`: run `tjs run` once for LT_IN.** The US books now
  carry each position's holding period start (`holding_start_date`: a
  wash-sale replacement's includes the loss shares' days), and LT_IN
  counts from it; books written before the upgrade still count from the
  purchase. `tjs harvest --json` also lists the positions it left out,
  and why, under `omitted`.
- **`tjs div-estimate 0m` (or `0y`) is refused** as `--months 0` is:
  give a window of 1 to 120 months.
- **Canada: a move of your own between two taxable accounts now shows
  in each account** (backlog TJ-0093). `tjs list`, `tjs sanity`, `tjs
  find-missing-history` and the checklist see the receiving account hold
  the moved shares (they read it short, "missing history?", and called a
  correct sale "fix before filing"). No figure you file changes: the
  pooled ACB, Schedule 3 and the superficial-loss result are as before.
  The per-account baselines change: `<account>.sum` moves the units, so
  the sender's holdings drop and the receiver's sales of them are listed
  there for manual reporting (their cost is known only in the blended
  figures). If you wrote an `OPENING … cost=unknown` line for such a
  sale, the run now says its rows never go short any more: delete it and
  run again, and the sale joins the totals at the pool's cost (while the
  line stays, the sale stays out of them, as before).
- **An in-kind move to or from a registered account that is NOT booked
  is flagged at filing time.** `tjs sum` prints a `! ` line for it,
  `sum --json` readiness carries an `in-kind-not-booked` issue (so
  `complete` is false) and the checklist's transfers step needs
  attention. A project that had such a move (the run's "in-kind
  move(s) … NOT booked" warning) shows it after the upgrade: add the
  `INKIND` line `tjs sum --details` prints, then `tjs run`.
- **`sum --json` `run_state_problems` counts one problem per problem.**
  An inputs change ("changed: …; added: …") was two entries, and two
  `run-state` readiness issues; it is one. A reader counting them sees
  fewer.
- **Report commands in a project whose last run had no input** (every
  account's folder empty) stop with "Error: the last run had no input:
  inputs/margin is empty, so it made no books", naming each empty
  folder, instead of "Run `taxjson run` first" (the exit is 1, as
  before).
- **`tjs gains` in a project whose only books are crypto exits 0**
  after its "crypto account … is not shown" line (it exited 1 with
  "run `taxjson run` first").

- **`tjs close-year` stops on uncommitted inputs in a git repository.**
  Inputs, `taxjson.toml` or the maps changed but not committed used to
  be locked without a word; now the close asks on a terminal and,
  piped, refuses naming `inputs-committed`. Commit them, then close; a
  script that closes on purpose passes `--yes`.
- **`tjs close-year --force` no longer locks books whose last run did
  not finish** (no `reports/`): fix the run and `tjs run` first.
- **`tjs close-year` and `tjs years` show realized and disallowed as
  the RETURN row of `tjs sum` files them** (each sale's cents), a cent
  or two from the engine totals they showed; `check-filed` still
  compares the engine totals. A year closed before this release keeps
  showing its totals until it is closed again.
- **`tjs close-year` and `tjs years --json` show income as `tjs sum`'s
  taxable SUBTOTAL adds it** (each account's DIVIDEND and PIL to the
  cent), a cent or so from the lock's total they showed; a year closed
  before shows it too. The lock and what `check-filed` compares are
  unchanged.
- **`tjs carryover` (Canada) takes the project year's NET GAIN(LOSS)
  from the RETURN row of `tjs sum`** (each Schedule 3 row's cents, as
  filed), not the books' unrounded total. A cent or two moves in that
  row and, in a loss year, in the carryforward balance after it
  (CARRYFWD BAL, "Net-capital-loss carryforward after …", `--json`'s
  `carryforward_balance`, `final_carryforward`, `available_to_apply`)
  and the T1A carry-back amounts it suggests or finds (`carryback_candidates`,
  `carried_back`, `carried_back_to`, which `tjs handoff` reads). It now
  agrees with the "Unused capital losses" of `tjs estimate` and what
  `tjs close-year` carries forward, which already used the RETURN row
  (their figures do not move). An earlier year's lock closed by this
  release gives its recorded RETURN row (`return_row`) in place of the
  per-account gain lines, a cent apart where one symbol is sold from
  two accounts. `--json` rows add `net_source` and `engine_net_gain`.
- **`tjs sum` says, above the RETURN row, when the taxable SUBTOTAL
  (or TOTAL) and the RETURN row differ** ("The SUBTOTAL adds the
  account rows, RETURN each sale's cents"): one more line in the default
  view; `--json` is unchanged.
- **`tjs estimate --json` (Canada) adds `losses_carried_back` and
  `losses_carried_forward`** to `estimate`: the part of the year's loss
  `[carryover] claimed` carries back (T1A) and the rest, the two
  figures the console prints. `losses_unused` is unchanged: the figure
  before the carry-back. A script that read `losses_unused` as the
  balance carried forward reads `losses_carried_forward`.
- **A year frozen by close-year no longer calls later years' rows of
  its exports impossible** (`tjs check-dates`, its checklist step, the
  re-close gate), and no longer reports the generated
  `crypto_sends.tt` as a changed frozen input: both need only the
  upgrade, no re-close.
- **`tjs years` shows a filed year whose last run DRIFTED.** Each drift
  check (`tjs run`, `tjs check-filed`) keeps its verdict in
  `work/filed_check.state`; `years` then shows `filed … DRIFTED` with an
  `! ` line naming `tjs -C 2024 check-filed`, and `years --json` has
  `drifted`. A filed year shows it after its next `tjs run` or
  `check-filed`.
- **A filed year's settings that differ from the newest year's are its
  own.** `tjs years` marks them `(filed)` and no longer points at `years
  --diff` for them; `tjs align --write` in a filed year warns before it
  changes the taxjson.toml and ticker.map the lock was closed with.
- **A blended account's `.sum` DIAGNOSTICS names the account of a
  message about another account's file.** The blended pass's messages
  go into every blended account's `<account>_wash.sum`; one about a
  file of another account (`inputs/margin/hand.tt:1 …` in
  `reports/ib_wash.sum`) now starts `[margin] `. The DIAGNOSTICS text
  changes once: a script or diff that reads those lines sees the tag.
  The account's own `.sum` files, and messages about the whole blend,
  are unchanged.
- **A filed year whose lock was deleted stays frozen.** While its
  taxjson.toml still carries close-year's freeze note, a year with no
  `filed/<year>.json` is no longer taken as never closed: `tjs years`
  shows `lock missing` ("restore it or run close-year again"; `--json`
  `filed` true with a `problem`), `tjs check-filed` exits 1 and `tjs
  run` warns, the checklist's lock steps are blocked, and `tjs migrate`
  leaves the year alone. Restore the lock (`git checkout --
  2024/filed/2024.json`, or a backup) or close the year again.
  `new-year` from a frozen year whose lock is missing or damaged now
  reads the shared folders; a year an older release started that way
  carries the freeze note copied from it: delete the note and set
  `inputs_dir` (and `tobase_map`) under its `[settings]`.
- **The checklist's year-end steps.** `lock-committed` needs attention
  when `.gitignore` hides the reports as filed (`filed/<year>/reports/`,
  an older scaffold's unanchored `reports/`): anchor the line as
  `/reports/` and commit them; outside any repository it names `git
  init`. `inputs-frozen` is done once the year is
  filed (a late export is an amendment), and `next-year` in a
  single-folder project names `tjs migrate --to-years`, then `new-year`.
- **`tjs winners` / `tjs losers`: a DENIED column.** When a row shown
  has a superficial-loss (US: wash-sale) denial, the table has DENIED
  before GAIN, so PROCEEDS − COST + DENIED is the GAIN; `--json` rows
  carry `denied` (COST is unchanged). A script that reads the text
  table's columns by position reads one more on such a table.
- **`tjs win-rates SYMBOL --json`:** `denied_total`, `denied_count` and
  `tainted_skipped` cover the symbols named, no longer every ticker.
- **`tjs stats` / `tjs win-rates` after `tjs run`:** a loss denied on
  an option whose replacement was exercised now counts once (the
  shares' sale recovers it), and an s.86.1 spin-off splits the
  recovered loss between the parent and the new shares as it splits
  the ACB. Their NET_P/L can move; no filed figure does.
- **`tjs audit` with a corp-action election pending:** the taxable
  accounts blended with the deferred one are not audited either (they
  have no cross-account pass to tie to) and the warning names them;
  the exit code is 1, as before. Decide the election, then `tjs run`.
- **A purchase line drafted from a transfer stated at 0 needs your
  cost.** `tjs find-missing-history --write-purchases` used to draft a
  transfer-in whose row states a book value of 0 (`NO_COST_(0_stated)`
  in `tjs transfers`) at a cost of 0.00; filled in with only its date,
  its sale was filed at a $0 cost. Now the line reads `COST  COST`. If
  you kept such a line in a `.tt` file, check its cost: unless your cost
  really was nil, put in the real one and `tjs run`.
- **Drafts you kept from IB's Basis may hold an extra line.** When the
  files bought shares between two sales coded closing, the drafts had a
  line for each sale, more than were missing. `tjs list` (or `tjs
  sanity` against the broker's holdings) shows the extra units; remove
  the line for the sale of shares the files bought.
- **A transfer-in only partly covered by `.tt` purchases is drafted**:
  the line is for the other units, at `COST` with a CHECK note.
- **`--write-missing-history` and `--write-purchases` refuse books
  older than the inputs** ("the books in work/ are older than the
  inputs ... nothing was written"): `tjs run` first. `tjs
  find-missing-history`, `tjs sanity` and `tjs transfers` open with the
  "not the clean result of the current inputs" warning then.
- **A written option booked at grant (`option_grant_timing_since`) with
  no purchase in the files** is no longer "NOT in the totals": it is
  listed under `no_purchase_in_totals` (`booked`: `short_cover`) in
  `tjs sum --json`, and `readiness` can be complete. The figures do not
  change.
- **`tjs transfers`' TYPE column** shows the kind of transfer (IB's
  `ACATS`, a crypto exchange's `withdrawal`, else `transfer-in` /
  `transfer-out`), not the broker's description; `--json`'s `type` is
  unchanged. Its `! ` lines are shorter: "N transfer-in(s) with no cost
  (…)" and "N transfer-in(s) only partly covered, NOT booked (…)", the
  fix in `--details`.
- **The checklist's sanity step needs attention when no account has a
  holdings file** and some account holds positions (it was `[m]`, to
  confirm, so `--done sanity` closed it with nothing compared). Save the
  broker's positions snapshots in `holdings/`, or accept the step
  unchecked with `tjs checklist --done sanity --force`. A project whose
  books hold no position keeps the confirm step.
- **`tjs sanity` with no holdings file:** `--json` prints a JSON document
  (`complete: false`, `uncovered_accounts`), not the text; `UNCHECKED`
  counts every account with open positions, registered ones included
  (it counted the taxable accounts with books). A script that read the
  text, or the "with books" wording, reads the new form.
- **`tjs sanity --json` has `accounts_not_checked`** (the accounts whose
  `holdings = [...]` file is missing or unreadable), and `complete` stays
  true when the only note is about a `holdings/` snapshot of an account
  compared with its own `holdings = [...]` files.
- **`tjs sanity` refuses books that are `{}` or a bare list** (`work/<account>_base.json`),
  as it refuses books that are not JSON: re-run `tjs run`.
- **Two downloads pasted into one export file are refused when they
  overlap.** A Questrade, Webull or generic-importer file whose header
  (Webull: the statement's Account Number preamble) repeats, and whose
  two parts share rows, booked the shared rows twice; the run now stops
  with "line N: the header repeats here — two downloads pasted into one
  file". Save each download as its own file in the account's folder:
  overlapping rows of two files are read once. Parts that share no row
  are read as before. Then `tjs run`: the doubled rows leave the books,
  so a filed year that read such a file changes.
- **Two Coinbase or Kraken downloads that disagree on one row are
  refused.** When overlapping downloads of one account carried a row id
  with different content (a restated price or amount), the run kept the
  version of the file whose name sorts first, with no message; it now
  stops with "row id … is in both with DIFFERENT content". Keep the
  newer download only (or correct the row) and `tjs run`.
- **A large export runs in seconds, not minutes.** Canada's
  superficial-loss check walked every row of the books for every loss
  sale (8,000 rows of one symbol: about 2.5 minutes; 50,000 rows: half an
  hour). It now reads each security's rows by date window; no figure
  changes, so a filed year does not drift. Nothing to do but `tjs run`.
- **`tjs amt` / `tjs estimate`: the Ontario share of a minimum-tax
  carryover recovery is smaller, and the surtax it saves is shown
  apart.** "Recovered: ON share" used to be the whole drop in Ontario
  tax; it is now the share deducted from basic Ontario tax (the figure
  for the provincial form's carryover line), with the surtax it saves on
  its own row. The total recovered and the tax do not change. A script
  reading `--json` finds the two parts in `recovered_provincial_credit`
  and `recovered_provincial_surtax` (`recovered_provincial` is still the
  total), and a new lock records `recovered_provincial_credit`.
- **`tjs check-filed` exits 1 on a lock whose `fx_cash_v2` close is
  damaged** (it said OK; the next year's FX-on-cash ledger refuses
  it). Restore the lock from git, or re-close that year.
- **`tjs estimate`'s INSTALMENTS line reads "=> instalments behind"**
  (it said "=> balance due April 30" for the instalments still
  owing). A script that matched the old wording needs the new one.
- **`tjs estimate` in a loss year carries forward only what is not
  carried back** when `[carryover] claimed` records a T1A carry-back
  ("Unused capital losses: … (carry forward; … carried back:
  [carryover] claimed, T1A)"), as close-year does; `--json`'s
  `losses_unused` is unchanged.
- **`tjs carryover`: a closed year with no `[carryover] claimed` entry
  shows APPLIED as what its lock applied** (it showed 0.00), and no
  longer offers that gain again.
- **FX on cash, ledger v2: a USDC reward priced in CAD.** A USDC (or
  other USD stablecoin) reward that Coinbase prices in CAD now brings
  its US dollars in at the income's CAD value. If you added a
  `CASHMOVE` line for it to clear the gap, remove the line (else the
  reward counts twice and the year is NOT COMPUTED), then re-run `tjs
  fx-cash --ledger v2`.
- **FX on cash, ledger v2: moves between your own accounts.** A chain
  of moves on one day (a to b to c) no longer makes up a debt of the
  middle account, and an own move the receiving broker books up to 7
  days before the sending one computes instead of being refused.
  Re-run `tjs fx-cash --ledger v2` before filing its figure: one
  computed with a BORROWED row for such a chain changes.
- **`close-year` with ledger v2 records a book that ends the year at
  zero.** A record written by an older release does not list it: the
  next year still asks for its `CASHOPEN <date> <cur> 0 0` line once
  (or re-close the prior year with `tjs close-year --force` there).
- **`tjs sanity` exits 0 for an s.86.1 spin-off's fraction awaiting
  its cash in lieu** on the snapshot's date (a `fraction` line, and
  `fractions_awaiting_cash_in_lieu` in `--json`), where it reported
  `QTY_MISMATCH` and exit 1.
- **`tjs spinoffs --json`** adds `fmv_currency` (the currency of
  `fmv_per_share`) and `cash_in_lieu`; its `ratio` counts a fraction
  paid in cash.
- **A `.tt` line ends at a line feed only, for every reader of it.** A
  form feed, a vertical tab or a Unicode line separator (U+2028, U+2029,
  U+0085) inside a line, often pasted into a comment, made the run's
  readers of `RENAME`, `JOURNAL`, `ALLOWLOSS`, `INKIND`, the cash lines
  and `OPENING ... cost=unknown` read the text after it as a line of its
  own and book it. Now it stays part of the comment. A project whose
  `.tt` files hold one (`grep -nP '[\x0b\x0c\x{85}\x{2028}\x{2029}]'
  inputs/*/*.tt`) can see its positions, ACB, a superficial-loss denial
  or the FX gain on cash change on the next `tjs run`; put a line you
  meant on a line of its own.
- **`tjs wash-radar --verbose` prints the full report**, as `--details`
  does (every advisory, the definitions, the scope). It used to change
  nothing; a script that passed it now gets the long form.
- **The wash radar's advisories print money as the tables do**
  (thousands separators, the base currency after it: `250.00 CAD`, not
  `$250.00`), in `wash-radar`, `watch`,
  `buy-check`, `sell-check` and the `--json` `advisory` text. The first
  `tjs watch` after the upgrade reports each such advisory as changed,
  once.
- **Re-entry dates are trading days.** `tjs rebuy`'s REBUY ON, the radar's
  COOLING / BLOCKED CLEARS and `tjs buy-check`'s safe-from date are the
  first trading day a buy no longer replaces the loss. In Canada that is
  the first day whose buy settles after the window, up to two days
  earlier than before (never a weekend); in the US a weekend date moves
  to the Monday. From that day the loss no longer shows BLOCKED or
  COOLING. The first `tjs watch` after the upgrade reports the moved
  dates, once.
- **Canada: a buy counts from its trade date in the planning views.**
  `wash-radar`, `rebuy`, `buy-check` and `sell-check` treat a buy made
  today as acquired and held before it settles, so right after a rebuy
  the loss reads VIOLATION (or the position LOCKED) at once, not a day
  or two later.
- **A file you made read-only is no longer rewritten.** `tjs format
  --write`, `format-map --write`, `migrate`, `init --force`, `ticker-map --suggest
  --write` and the other commands that rewrite taxjson.toml, ticker.map,
  tobase.map or slips.toml now refuse one you cannot write ("cannot
  write taxjson.toml: the file is read-only"); before, they replaced it
  and put the read-only mode back. To let them rewrite it, `chmod u+w`
  the file the message names.
- **`tjs fetch --list` works in any folder again.** Since v0.32.0 it
  was refused outside a project ("not a taxjson project"), so the
  installer printed an `Error:` and a blank `taxjson-fetch installed:`
  line. It reads no project; `tjs fetch` itself still needs one.

### v0.35.0

- **`tjs checklist --skip` is gone: `tjs checklist --done ID --force`.**
  A script or a habit that marked a step `--skip ID` now writes `--done
  ID --force`; a plain `--done ID` is refused while the last check flags
  the step. The `skipped` marks already in a project's `checklist.json`
  need nothing: they read as forced done marks (`[f]`, `--json`
  `effective: "forced"`); a script that read `skipped` from `--json`
  (or from `tjs years --json` `marks`) reads `forced`.
- **`tjs fetch` (IBKR Flex) works again.** Every release since the
  multi-account guard was added refused a normal single-account Flex
  download as "contains 3 account statements" (it counted the
  statement's metadata rows). After upgrading, run the fetch again;
  nothing in a project needs changing.
- **A Coinbase or Kraken trades export with one ID on two different rows
  is refused.** The second trade used to be dropped from the books with
  no message; now `tjs run` stops on the file and names both lines (an
  identical repeat is still read once). Delete the row that is a stray
  copy, or give one of two real trades a distinct ID, and re-run: a
  trade that was missing is booked, and the coin's units, cost and gains
  change.
- **`tjs fx-cash --ledger v2` refuses a prior-year close that carries a
  cost below zero, or a cost on no units** (2026-10-25 audit,
  year-end#2): figures a `CASHOPEN` line is refused for and `close-year`
  never writes. A year that computed on such a record (a damaged or
  hand-edited `filed/<year-1>.json`) is now NOT COMPUTED with a `carry`
  problem naming the book: restore the record from git, or re-close that
  year with `fx_cash_ledger = "v2"`.
- **A damaged prior-year lock, or one closed under the other country's
  law, now stops `tjs carryover`, `tjs estimate` and `tjs amt`**
  (2026-10-25 audit year-end#1, robustness#1, year-end#3, year-end#6).
  Before, the carryover ledger dropped a damaged lock with a warning
  and printed the carryforward without that year (0.00 when the books
  do not rebuild it), took the other country's lock as the filed figure
  0.00, and counted a loss twice when a lock's `year` named another
  year; the estimate and `amt` read the other country's lock as
  carrying nothing, and a minimum-tax year written twice in a lock kept
  its second amount. If one of these now stops naming a lock: restore
  it from git, re-close that year in its project, or point
  `[settings] prior_year_record` at this country's lock; then re-run
  and compare the carryforward and the estimate with the last run's.
  `tjs check-filed` and `tjs years` in the closed year now name the
  same lock damaged.
- **Transfer-ins into a taxable account are reconciled more strictly**
  (2026-10-24 audit, B03 / B04 / B40 / B41 / B42 / B50): a transfer-out
  cancels a transfer-in of the same security within 10 days (a slower
  move of your own: the next item; a departure years after an arrival
  never), a listing journal with no pair id and no ticker.map rule only
  between two listings of one root, `.tt` purchases for part of an
  arrival's units book nothing (`PARTLY_covered`, said every run) and a
  stated book value of 0 is no cost. After the upgrade, `tjs run` and
  compare `tjs transfers` with the last run's: an arrival now booked
  that was `own_move` before came from outside your books (or needs a
  `TOBASE` line joining two tickers), unless its line names a
  transfer-out of yours that could be its other end (the next item); a
  `PARTLY_covered` row needs the other units' purchase as a `.tt` line;
  a pre-split `.tt` purchase now covers the post-split units that
  arrived once its `SPLIT` line is in the `.tt` file.
- **A move of your own between two taxable accounts whose legs post 11
  to 45 days apart is one move** (2026-10-25 audit diff-engine#4): a
  transfer-in of the same stock and quantity as a transfer-out of
  another of your taxable accounts 11 to 45 days before it, neither with
  another candidate, books nothing (the shares keep their cost; a US
  project carries the sending account's lots), said as an `Own-account
  move` Info line with both dates. A build with the 10-day window alone
  booked such an arrival at the broker's book value, blending the pooled
  cost, and kept the shares in the sending account: `tjs run` again, and
  the cost and gain of their sale are the purchase's. Legs that are
  ambiguous, partly paired, in one account or further apart stay an
  arrival and a departure: the arrival's ATTENTION line names a
  departure nothing paired up to 45 days before it, and `tjs sanity`
  shows the shares held twice if they are one move.
- **US project: a `[[distributions]]` basis adjustment reaches every lot
  settled by the record date.** The row carries the record date and the
  US engine spreads it over the open lots settled by that date only; it
  is dated before a straddling SALE only, no longer before a straddling
  buy, which had put the whole amount on the lots bought before that buy.
  Re-run `tjs run`: a lot bought after a purchase that straddled a
  record date, and sold since, changes its basis and gain. Canada's pool
  is unchanged. When a trade between a straddling sale and the record
  date keeps the row from reaching exactly the record date's lots, the
  run says so (`warning: ATTENTION: [[distributions]] … misses the lots
  of the trade(s) in between`): check those lots, or enter per-lot
  `.tt` ADJUST rows.
- **An in-kind move of an option is valued and priced at the contract
  size its transfer row declares** (`multiplier`: an adjusted series'
  50, a mini's 10; else a ticker.map `MULT` line, else 100). A move of
  such an option valued from a Yahoo close was worth 100 units per
  contract: re-run `tjs run` and compare `tjs transfers` (the move's
  value and the booked row's price change; a value from an `INKIND` line
  or the broker's market value is unchanged).
- **Canada: `tjs t1135` gives a lower cost amount for shares an assigned
  put delivered, where the delivery also covered a short** (audit D02,
  `CA-RPT-12`). The shares kept were costed at their gross price; their
  share of the put's premium now lowers it, as it lowers their ACB in
  `tjs run`. Re-run `tjs t1135` and compare the maximum and year-end
  cost with a T1135 already filed: both are filing figures.
- **`tjs stats` / `tjs win-rates` count a denied superficial loss (US: a
  disallowed wash-sale loss) once.** The replacement's sale counted it
  again through the amount the rule added to its cost (s.53(1)(f) /
  §1091(d)); it now counts without it, so after a denied loss whose
  replacement was sold the views show more wins and a higher NET_P/L.
  Re-run `tjs run`: the gains files then carry each sale's
  `deferred_loss_recovered`, which the views read (a gains file written
  before the upgrade has none and keeps the old figure). No filing figure
  changes.
- **A declared in-kind move delivered in parts is booked with the plan's
  legs only when that set is the only one** among every candidate leg
  within the window — not the eight nearest. A move booked before by
  such a match may now be listed as "possibly in-kind in parts — NOT
  booked" with the legs: it is the `INKIND` line's value that books the
  taxable side; name the plan's legs with a line per part if the plan
  received them that way.
- **`tjs sanity` refuses a holdings TOML whose `[meta] holdings_count`
  is not its number of `[[holding]]` tables, or whose holding declares a
  `contract_multiplier` (or `multiplier`) that is 0 or negative.**
  It stops with an Error naming the file (exit 2) and compares nothing;
  `tjs run` ends with "holdings check could not run". Download the
  snapshot again, or fix the hand-made file.
- **`tjs sanity` refuses files of one account at different dates that
  do not each name their broker account** (two `holdings/` snapshots
  named only `margin_…` or `[meta] account = "margin"`): they were
  summed. Add `broker_account = "…"` to each file's `[meta]` (one broker
  account's snapshots are then compared one date at a time), or keep one
  snapshot per broker account.

### v0.34.0

- **`tjs checklist` no longer runs the checks: `tjs checklist --run`
  does.** A plain `tjs checklist` shows the verdicts of the last `--run`
  (kept in `work/checklist_results.json`) with when they were made and
  what changed since; until the first `--run` it lists the steps
  unchecked. A script that read `tjs checklist` or `tjs checklist
  --json` for fresh verdicts adds `--run`. `-j N` runs N slow checks at
  a time (default: the CPUs, at most 4).
- **`tjs checklist` prints the whole list before the summary.** The
  default view was the summary alone; the summary (the counts table,
  the `! ` lines, the `Next` line last) is unchanged but now follows
  the list. A script that read the default text and expected the table
  first reads it after the list; `--json` is unchanged.

### v0.33.0

- **Python 3.10 or newer is required** (was 3.9). An install on Python
  3.9 stays on its release; the installer upgrades it once a newer
  Python (3.13 down to 3.10) is first on `PATH`: macOS `brew install
  python`, Debian/Ubuntu `sudo apt-get install -y python3 python3-venv`.
  Why: the fixes to the vulnerabilities reported against the dependency
  locks (urllib3, requests, cryptography, pip, setuptools, anyio,
  soupsieve) all dropped Python 3.9, which reached end of life in
  October 2025.

### v0.32.0

- **RBC export without a `Symbol Description` column, with a name change
  booked under a temporary code: re-run `tjs run`.** The old ticker is
  now found from the buy's Description, so the rename is booked and the
  sale under the new ticker is back in the totals. A `GLOBAL <old>.TO
  <new>.TO` line you added for it can stay.
- **RBC export without a `Symbol Description` column, with an s.86.1
  spin-off: set the election again.** The parent is now found from the
  trade's Description, so the event id names its ticker
  (`…-zzp-zzs-…` instead of `…-x123456-zzs-…`): `tjs elect --pending`.
- **A closed year whose loss you carried back (`[carryover] claimed`
  under an earlier year): `tjs close-year --force` in it.** Its lock
  carried the whole loss forward; the re-close records the balance after
  the carry-back, which the next year's estimate reads.
- **A closed year locked with `--filed-dispositions` whose filed gain
  differs from the books, or a closed loss year that carried a loss back
  after an earlier loss year did: `tjs close-year --force` in it.** Its
  lock carried forward a net capital loss that was too large.
- **A filed year where you ran `tjs align --from <newer year> --write`:
  delete the live `inputs_dir` and `tobase_map` lines from its
  `[settings]`** (the commented ones under `## frozen by` stay), and if
  `tjs migrate` removed its `tobase.map`, rename `tobase.map.bak` back.
  An older `align` wrote them, so the year read the shared exports, not
  its frozen copy; its run now says so.
- **A year you locked before it ended (`tjs years` marks it `filed*`)
  with v0.31.0 or v0.31.1: `tjs close-year --force` in it, then `tjs
  run`.** That close froze a copy of the exports as they were then, so
  later trades never reached the year. The re-close thaws it (the year
  reads the shared exports again; a copy you edited is named and kept)
  and stops; the run then shows the later trades as drift.

### v0.31.1

- **Re-run `tjs run` for a year with an s.86.1 spin-off paid cash for a
  fraction (Questrade or RBC).** The fraction is now sold for the cash on
  its share of the allocated cost (`CA-CORP-05`): its gain and the new
  shares' ACB change slightly.
- **ALLOWLOSS quantities are read strictly.** A quantity with a decimal
  comma (`1,5`) was read as 15 units; it is now refused with the line
  named. Write `1.5`.
- **`[carryover] claimed`: one year spelled two ways stops the run.**
  `2023` and `02023` in one table are one tax year; keep one line.
- **Holdings TOML dates must be real days.** An `as_of`, `generated_at`
  or `acquired` such as `2025-02-30` is now refused, naming the field.
- **FX-on-cash ledger v2: two broker accounts whose hashed ids start
  alike get longer book names.** An `at=` naming the old 4-character book
  is refused, and the message lists the new names; point it at one of
  them. Books without such a clash keep their names.
- **`.gitignore`: anchor the `reports/` line.** The file `taxjson init`
  wrote before this release ignores `reports/` at every depth, which
  also hides `filed/<year>/reports/`, the reports as filed that
  `close-year` keeps since v0.30.4+. In a project you keep in git,
  change `reports/` to `/reports/` (and, in the folder holding the year
  folders, add `/*/reports/`); `close-year` says so when the line is
  still there. New projects get the anchored file.
- **`tjs compare` exits 1 when an account figure moved,** even when every report file matches (it exited 0). A script reading the exit code sees a difference there.
- **An ALLOWLOSS line dated the settlement day of only some fills of a sale now names none,** and the run stops on it: date the line with the sale's trade date.
- **Refused now, named by line:** an IB dividends report with a blank GrossInBase beside a Gross, an impossible date or an amount like `1,5` (download it again); a CRA slip page whose box figure is not an amount (print it again or type the slip into slips.toml); an RBC Holdings Export whose cost column is neither a total nor per share (rename the column).
- **US projects with futures from a generic CSV with a settle column:** run `tjs run` again; a gain can change, up or down, where settle dates ran out of trade order: a partial close now takes the contract traded first (`US-FUT-01`).

### v0.30.1

- **v0.30.0 can no longer be installed; v0.30.1 is the same program.**
  An install that follows a channel (`stable`, `beta`, `latest`)
  upgrades as usual by re-running the installer. One pinned to `v0.30.0`
  (`--channel v0.30.0`, `TAXJSON_CHANNEL=v0.30.0`): pin `v0.30.1`, or
  use `--channel stable`. A clone of the repository keeps working: the
  history did not change.

### v0.30.0

- **Releases v0.15.0 to v0.29.2 were withdrawn.** The public history
  was replaced by a single starting commit, and those releases and their
  tags are gone. An install that follows a channel (`stable`, `beta`,
  `latest`) upgrades as usual by re-running the installer. One pinned to
  a withdrawn version (`--channel v0.28.4`, `TAXJSON_CHANNEL=v0.29.0`)
  can no longer re-install it: re-run the installer with
  `--channel stable` (or pin `v0.30.0`). A clone of the repository made
  before 2026-10-12 shares no history with the new one: clone it again.
- **Two engine fixes can move a filed year.** A superficial loss whose
  replacement shares came into the loss's symbol by a later rename or
  merger (Canada), and a split reported twice around a per-share
  distribution's record date, now give the correct gain; a filed year
  with either shows `filed <year> DRIFTED` on the next run
  ([troubleshooting.md](troubleshooting.md)).
- **`tjs splits` no longer fails on a split reported twice.** Two
  reports of the same split within a week are marked `TWO-SOURCES`
  (applied once, on the earlier date) and exit 0; they were marked
  `TWICE?` with exit 1, though every engine already applied them once.
  `TWICE?` and exit 1 remain for the same ratio 8 to 10 days apart.
- **Blank broker ids (Coinbase, Kraken).** Two identical rows in one
  export whose ID (Coinbase) or `txid` (Kraken) cell is blank are now
  two trades or ledger events, as in an export with no ID column. A
  row pasted into a file twice by hand is then counted twice: remove
  the copy.

### v0.29.1

- **Cached prices are fetched again once.** `harvest` and the other
  planning views keep each cached quote with the Yahoo spelling it was
  fetched under, and an entry an earlier release wrote has none: the
  first run after the upgrade fetches every price again, so an offline
  run (`TAXJSON_OFFLINE=1`) stops until you have run once online. The
  same goes for a cached close of an LSE, JSE or Tel Aviv line used to
  value an in-kind move (troubleshooting.md).
- **An opening snapshot can stop the run.** An `OPENING` line dated
  inside the tax year that would leave out a return of capital of that
  year which may go beyond the cost (a gain of that year) now stops the
  run, naming the row, as a left-out sale already did. Date the
  snapshot before that row, or drop it and give the full history.
- **Canada, futures in two taxable accounts.** The same contract held
  in more than one taxable account is now averaged across them, so a
  close can realize a different gain than before; a filed year with
  such contracts shows `filed <year> DRIFTED` on the next run. Futures
  in one account are unchanged.

### v0.29.0

- **`tjs winners` shows the winners only.** Its text view listed the
  best N and the worst N; it now lists the winners alone (gains above
  zero, best first), and `tjs losers` lists the losers (worst first).
  The total under the table is the shown side's (`WINNERS TOTAL`), with
  the year's `TOTAL REALIZED GAIN` over every ticker on the line below.
  `tjs winners --json` is unchanged; it adds the side's own totals
  (`side_*` keys). A script that read the losers from the text of
  `winners` reads `tjs losers` (or `--json`) instead.

### v0.28.2

- **Same-moment trades of two accounts follow the accounts' names.**
  Rows of two accounts at the same date and time used to follow the
  order the accounts are listed in taxjson.toml; reordering the file
  changed gains without the books being called stale. They now go in
  the accounts' name order, whatever the file's order. A sale with a
  same-moment trade of the same security in another account can move:
  a filed year then shows `filed <year> DRIFTED vs <year>.json` on the
  next `tjs run` ([troubleshooting.md](troubleshooting.md)).
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
Search [troubleshooting.md](troubleshooting.md) for it: every known
problem is there, and an entry whose **Fixed in** is newer than
`tjs --version` is fixed by upgrading. The list below is the older form
of that record and stops at v0.28.0: check `tjs --version` against it;
if the fix is newer, upgrade first.

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
