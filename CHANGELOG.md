# Changelog

## Unreleased

### Added

- **`taxjson slip-audit` (Canada): your T5 and T3 slips against the
  books' income**, per account and slip box: Canadian dividends, box 18
  capital-gains dividends, foreign income and tax withheld, return of
  capital, interest. Type the slips into `inputs/slips/slips.toml`
  (`--template` prints one per account), or drop IBKR's dividends report
  (`U*.YYYY.dividends.csv`) in `inputs/slips/`: it is read payment by
  payment with its T5/T3 split (the holder's name is never read) and
  matched to the account whose books carry that IB account. A USD slip is
  compared in USD with both CAD conversions shown (each payment's Bank of
  Canada daily rate, and the year's average); for a CAD slip holding
  converted payments it says which one the slip is closer to. It lists
  payments missing on either side, trust distributions counted in another
  year by their record date, accounts with income and no slip, and prints
  the `[[capital_gains_dividends]]` entries and `.tt` return-of-capital
  lines that bring the books to the slips. `--json` has a stable schema
  (docs/settings.md); exit 1 on a finding. The checklist's `t5-t3` step
  runs it (answered per account with `--done`) and quick-start has a
  slip-audit step. Tax-logic `CA-SLIP-01` to `CA-SLIP-03`.
- `taxjson reconcile-slips inputs/slips/*.csv` skips an IBKR dividends
  report in the folder with a note instead of failing on it, and the
  checklist's T5008 step no longer counts it as a T5008.

- **A filing position against one superficial-loss denial (US: one wash
  sale), declared and listed**: a `.tt` line `ALLOWLOSS <sale date>
  <symbol> [<qty>] reason="..."` in the taxable account that sold keeps
  that sale's loss allowed, with no ACB (US: basis) added to the
  replacement; every other sale's verdict is unchanged. A line that names
  no denied sale, or two, stops the run naming it. Each position is one
  Warning per run and is listed with the denial the rule would make and
  why (the replacement, its account, the days from the sale on settle and
  on trade dates): `taxjson sum` (FILING POSITIONS; `filing_positions` in
  `--json`), `taxjson wash-sales` and the checklist's new filing-positions
  step. tax-logic CA-SL-18 / US-WASH-25 state it as your position, not the
  rule's test; CA-SL-01 and US-WASH-01 now say the window is counted on
  settle (Canada) or trade (US) dates whatever `tax_date` says.
- **FX on foreign cash: an opt-in ledger v2, under audit**
  (`[settings] fx_cash_ledger = "v2"` or `taxjson fx-cash --ledger v2`):
  besides the trades and income it reads currency conversions (IB Forex
  trades, Kraken fiat trades, Coinbase stablecoin buys and sells),
  deposits and withdrawals (IB Deposits & Withdrawals, RBC cash rows,
  Kraken and Coinbase fiat moves) and statement balances (the IB Cash
  Report, Kraken's ledger balances), plus the new `.tt` lines `FXCONV`,
  `CASHMOVE`, `CASHOPEN`, `CASHBAL` and `CASHBOOK` for what no export
  carries and whose account a `.tt` file's cash is. A
  conversion counts at the amount actually paid or received, a move
  between your own accounts is not a disposition, money from outside the
  books costs what you declare (`spot`, or `fx_cash_inflow_cost = "spot"`,
  for the day's rate), a negative broker balance is a debt in that
  currency realised when repaid, each account is reconciled to its
  statement balances, and the year opens with the pool `close-year`
  recorded. Anything missing makes it NOT COMPUTED with the list of what
  to add, never a guessed figure; a computed figure is labelled "v2
  (opt-in, under audit)" and the checklist keeps it at attention until you
  mark it reviewed. tax-logic CA-FX-07 / US-FX-03 state the method.

### Changed

- **The default FX-on-cash figure is no longer presented as reportable**:
  it never read conversions, deposits/withdrawals or margin balances, so
  `taxjson sum` (FOR THE RETURN), `taxjson fx-cash` (first line), the
  end-of-run note and the checklist now say "FX on foreign cash: NOT
  RELIABLE for <year> — <n> in-year overdrafts (<amount> <currency>);
  conversions, deposits/withdrawals and margin balances are not read; do
  not file this figure". `sum --json` and `fx-cash --json` carry
  `"reliable": false` and the reasons, with the raw figures under
  `unreliable_raw`.

## v0.24.1 (2026-10-08)

### Added

- **`taxjson run` asks about written options carried in from last year**:
  a contract written before `option_grant_timing_since` (a new project's
  default is its own year) and bought back or expired this year is taxed at
  the close, right only if last year's return did not report its premium.
  The run now warns with the premium at stake and the question; `taxjson
  option-boundary`, the checklist's option-boundary step and quick-start's
  new option-timing step ask it too, until the setting is lowered to the
  write year or the step is marked done. A `checklist --done
  option-boundary` mark answers the contracts asked when it was made: a
  contract the books gain later is asked again. Never asked in a US project.

- **A broker whose exports stop while it holds positions is named**: for
  each account and broker, exports that end before the tax year's end (or
  before today in the year still running) — an IB statement's period, an
  RBC export's as-of date, a Webull export's date range or trading-summary
  year, else the last row — while the broker still holds positions there
  are a run Warning ("<broker> exports for <account> end <date> with open
  positions ...; download the rest of the year"), the checklist's new
  export-coverage step and quick-start's inputs step. A position counts
  only when the broker's own rows (an export named with its account
  number included, transfer legs read through the books' journals and
  `TOBASE` lines) leave it open and the account's books hold it too (a
  broker's sale of calls an opening `.tt` line bought is no written call
  at that broker), and only while the account's later rows of any source
  dated inside the gap (a `.tt` close, another broker's sale, a
  transfer-out) do not close it; when `.tt` lines closed them all, an
  Info line says no export is needed. An option that expired after the
  export's end with no expiry row is named as such. If the broker had no
  activity in the account after that end, `taxjson checklist --done
  export-coverage` answers it — for that account, broker and end only: a
  new gap or a later end asks again.

- **`taxjson list` takes the date as a word too**: `taxjson list margin
  2026-04-28` and `taxjson list 2026-04-28` are `--date 2026-04-28` (a
  YYYY-MM-DD word is the date, any other word the account).

### Changed

- **A sheltered account's corporate-action election says what it
  changes**: still asked (it sets the holdings' cost in the books), the
  prompt and `taxjson elect --pending` now say there is no tax in the
  account and that its holdings still count for the superficial-loss rule
  (a US project: the wash-sale rule).

- **`taxjson scan` stops asking about pairs the exports show apart**: a
  US and a Canadian listing that share a root are still a MAP-GAP
  candidate, but not when the Canadian line is a depositary receipt (a
  CDR or ADR word in its name, or a receipt venue) or the two names share
  no company word, even with spaces and hyphens set aside ("OPEN QZX" and
  "OPENQZX" stay a candidate, to verify) — such a pair needs no
  `DISTINCT` line, and US-LISTING no longer suggests holding it. The MAP-GAP message says whether the
  names agree ("carry the same name … add `TOBASE`; if not, `DISTINCT`")
  or were not compared (verify first); `reports/crosslistings.rpt`
  and `ticker-map --suggest`'s conditional hints follow the same rule.
  `DISTINCT` lines stay valid.

### Fixed

- **The cross-listing loss warning judges the trades' own names**: a loss on
  one listing and a purchase of the other within 30 days were dropped in
  silence when another account's export named one listing with other share
  wording ("... SUBORD VTG SHS"), although the loss's and the purchase's own
  broker named both listings alike. The radar now compares the names of the
  loss's rows and the purchase's rows (their account and broker); names of
  one company that differ only in the voting-share phrase one of them ends
  with ("... SUBORD VTG SHS") are flagged too, said as such — never a
  depositary receipt, a class letter or two companies, and never for the
  same words inside a company's name ("NON STOP CORP" is not "STOP
  CORP").

- **A correct ticker.map `TOBASE` line no longer splits a transfer between
  brokers.** IB moved a US listing out (`QZB.US`) and Questrade's export
  booked the arrival under the company's TSX root on a USD row (`QZA`); with
  `TOBASE QZB.US QZA.TO` in the map, the out-leg became `QZA.TO` while the
  in-leg stayed `QZA.US` (the account also held `QZA.TO`), so the units
  left one security and arrived in another: a withdrawal at fair value in a
  registered account, a later sale left out of every total in a taxable one,
  and nothing on the console. The map's renames now apply to the out-leg
  first, and the in-leg is joined to the listing the map books the out-leg
  as (keeping the base currency's listing, as any join does). A transfer
  pair the map books as two different symbols is a Warning naming both
  legs and the line that books them as one; `run --strict` stops on it —
  only when the legs' names agree, and never for a pair a `DISTINCT` line
  keeps apart.

- **A Questrade internal code of a spun-off warrant no longer resolves to the
  common stock** (GitHub issue #4). A code whose descriptions state a
  warrant, right, preferred share or class letter anywhere is never
  booked as a listing whose name states none (and the other way round): one
  plain-worded row no longer decides. "UNITS" is not such a word (a trust's
  or fund's "UNITS DIST ON ..." row would have kept its code from the trust's
  listing, and the sale out of every total), and a spun-off code is read by
  its own name, never its parent's class letter. The account's own later trade under the
  real ticker, described like the code's rows, resolves the code first. The
  corporate-action stage books a spinoff chain under the code as the
  symbol-code stage resolved it, so the run no longer prints a resolution and
  a "booked under Questrade's INTERNAL code" warning for the same code, and
  the chain and the later sale meet in one position.

- **A `DISTINCT` line written with the bare US ticker answers the pair**:
  `DISTINCT QZX QZX.TO` also keeps `QZX.US` and `QZX.TO` apart (the books
  spell a broker's bare US ticker `.US`), so the cross-listing loss
  warning, `taxjson scan` and `ticker-map --suggest` stop asking, with an
  Info line. A `GLOBAL` / `TOBASE` / `JOURNAL` / undated `RENAME` line so
  written is not re-read (it would move pools): when the books hold the
  US listing and not the bare symbol, the run warns that it joins nothing,
  naming the line to write.

- **A `.tt` JOURNAL line between a CDR and its US share needs evidence**:
  a listing written on a venue that lists depositary receipts (Cboe Canada,
  `QZG.NE`; marked in markets.toml) or named as a receipt in the exports
  ("... CDR") is its own security, so sharing the root QZG with `QZG.US`
  no longer counts as evidence: the names must agree, else the run stops
  naming the line. Only one side's receipt evidence counts: two lines both
  on Cboe Canada (an ETF's CAD and USD units, `QZG.NE` / `QZG.U.NE`) or two
  names that both carry a receipt word inside the company's name ("QZX
  SPONSORED HLDGS INC") are not told apart by it (also in `taxjson
  scan`); a receipt word after the name ("... INC CDR") still counts.

- **A `.tt` JOURNAL line can say it is a separate journal**: a trailing
  `separate` (`JOURNAL 2025-05-07 QZD.TO QZD.U.TO 1500 separate`) books the
  line as a journal of its own, in full, and silences the "booked in full
  beside the broker's journal" Warning, which nothing could silence
  before (docs/settings.md).

- **A broker journal split over several rows is one journal everywhere**:
  a reference shared by more than two legs (1000 out, 600 + 400 in) was
  read three ways by the cross-listing join, the transfer-in check and the
  missing-history walk. One rule now: one journal when its out-legs name
  one listing, its in-legs one, the units balance and the legs are within
  5 business days; otherwise no journal for any of them.

- **`taxjson format-map --write` homes a dated RENAME where every
  account keeps its `late=`**: when the default home would take the map
  line's `late=` away from an account that relies on it, the line goes to
  an account whose copy keeps every choice; when none does, the refusal
  names each such account and the `.tt` line to add. An account that never
  held the old ticker is no longer counted as changed.

- **A DRIP priced in the other currency no longer warns**: a Questrade or
  RBC reinvestment whose description quotes the price in the other
  currency (`REINV@C$` on a USD row, `REINV@U$` on a CAD row) is booked at
  the cash per unit, so the row check no longer flags it (the cost was
  already right; RBC's fee worked out from the foreign price is gone). A
  same-currency mismatch still warns.

- **A Questrade dividend reinvestment books the account's held listing**
  (GitHub issue #3): an REI row with the bare TSX ticker on a USD row was
  booked `ROOT.US` while the dividend it reinvests bound to `ROOT.TO`, so
  one share split into +1 / -1 across two listings (a false short in a
  registered account). It now books the listing the account trades under
  the same name and root; and when another account's evidence proved the
  same broker files that security's TSX listing on USD rows, the units that
  came into the account on USD rows by a transfer or a reinvestment are
  read as `ROOT.TO` too (a purchase on a USD trade row keeps the US
  listing). The reinvestment's cash is restated in the listing's currency
  in the native-currency books, so the holdings report is still written
  and `taxjson scan` still runs.


## v0.24.0 (2026-10-07)

### Added

- **`taxjson quick-start` lists every step from install to filing**
  (`tjs quick-start`), each with the exact command(s) and a one-line why:
  install or upgrade, `init`, the accounts, the broker files, `run`, filling
  the gaps (missing history, transfers, ticker.map, journals, renames, crypto
  sends, `.tt` lines), tidying the config, the checks (`scan`, `sanity`,
  `edge-cases`, `wash-sales`, `check-dates`, `checklist`), the results and
  filing outputs, sharing a redacted sample, and the year end. In a project
  folder (or `-C DIR`) each step is marked done, needs attention, to do,
  yours to review or n/a from the project's own files, and the next step is
  named with its command. It only reads files: it runs nothing, writes
  nothing and goes online for nothing. `--all` shows every step in full;
  `--json` is a stable schema (docs/settings.md). `taxjson init` and the
  run's closing list now point to it. A test fails when a new command is in
  no step (or is not listed as left out on purpose).
- **`taxjson format-map` lays out ticker.map** (`tjs format-map`), as
  `taxjson format` does taxjson.toml: a short header saying what the file
  holds, then the rules in groups in a fixed order (Spellings, Listings of
  one security, Clean-up, Dated events, Lookups), your order kept within a
  group, one space between fields, exact duplicate lines dropped. A comment
  directly above a line moves with it, and every comment line is kept. A
  line `taxjson run` cannot use stays as written in an "Unrecognized" group
  at the end, and the console names its problem. The parsed map must stay
  identical, or nothing is written. Default: a diff; `--write` (with a
  `ticker.map.bak` unless `--no-backup`); `--check` exits 1 when the file is
  not formatted. `taxjson init` now writes ticker.map in this layout.
- **`taxjson journals` lists every broker journal between two listings**
  (`tjs journals`): per account, each journal's date, FROM → TO, quantity,
  broker, how it was found (a Questrade BRW journal, an RBC journal
  transfer with or without its `J~` reference, an IB InterDepot, a move
  across brokers that changes the listing, a `.tt` or ticker.map line) and
  its state: joined (the `TOBASE` / `JOURNAL` line that pools the two
  listings, yours or the run's own, and the `DISTINCT` line that undoes
  it), suggested (the reason, and the `.tt` line `JOURNAL <date> FROM TO
  QTY` or the ticker.map line that settles it) or refused (kept apart by a
  `DISTINCT` line or another line of your ticker.map — a decision made,
  never pending — or the legs name two companies: the reason and the
  undo). Read-only, from the last run. `--account`, `--year`, `--json` (a
  stable schema, docs/settings.md), and `--pending` (only the pending
  journals: suggested, or refused with no line of yours behind it; exit 1
  when there is one). `taxjson checklist` has a journals
  step.
- **`taxjson renames` names each rename's source** (a broker row, an IB
  contract id, a `.tt` line, a ticker.map line; an undated ticker.map rule
  is a "legacy undated map") in a table, and lists the look-alike renames
  the Questrade, RBC and Webull exports show ("looks renamed") as
  suggested, each with the `.tt` line `RENAME <date> OLD NEW` and the
  dated ticker.map line that book it. `--pending` lists only the
  undeclared late trades and the suggestions and exits 1 when there is
  one; the checklist's renames step is [!] while a suggestion is open.

### Changed

- **Journals and ticker changes are dated events in the books.** ticker.map
  now holds only standing truths about securities. A journal between two
  listings and a ticker change happen on a date, so they are `.tt` lines of
  an account, written date first: `JOURNAL <date> FROM TO <qty>` books the
  move's two transfer legs and joins the two listings as one security (no
  disposition, in both countries — when the two symbols share one root or
  the exports' names agree, else the run stops and a ticker.map `TOBASE`
  line is the deliberate join; a journal the broker's rows already hold
  is said and booked once, and one with a single broker leg gets the
  other), and `RENAME <date> OLD NEW [late=fold|late=separate]`, declared
  in any account's folder, applies to every account of the same kind
  (securities, or crypto for a coin's new ticker) holding the old symbol
  and is recorded once. Every declaration of one change, in any file, is
  one event dated the earliest; the changes apply in date order, so a
  ticker that changed twice carries the position twice. A line's `late=`
  covers its own account's late rows and every account without a line of
  its own; another account may declare its own. A malformed line stops the
  run, naming the form, and so do declarations that cannot all be true
  (one ticker renamed to two symbols, one change on two dates, a cycle such
  as A to B plus B to A), naming every line. So does a line naming an
  option contract or a future (a contract never becomes shares by a
  journal or a ticker change; an option follows its underlying's
  rename), one dated in the future, a JOURNAL of an implausible
  quantity, and a JOURNAL moving more units than the broker's own
  journal between the same listings. Two identical JOURNAL lines are one
  journal, with a Warning.
- **IB's ticker changes are booked.** When IB lists one contract id under
  two symbols, the change is booked as a dated rename event, with a
  Warning naming the way out (`DISTINCT` in ticker.map for two securities,
  `late=separate` for another company reusing the old ticker). It is booked
  on the new symbol's earliest row of any section (a trade, a split, a
  return of capital, a dividend), so a new-symbol event before its first
  trade is kept, and only when the old symbol's last row that moves a
  position or its cost (a trade, a transfer, a corporate action, a return
  of capital — not a dividend, withholding or payment in lieu) is on an
  earlier day. Every IB account of the project is read together: one
  decision and one date in all of them.
  Otherwise — the rows overlap, or one statement lists the symbol under
  two contract ids (another company used the ticker) — nothing is booked
  and an ATTENTION
  line gives the `.tt` line `RENAME <date> OLD NEW` (the new symbol's
  earliest row in any account) and why. Nothing is
  booked on top of an IB corporate action the parse books as the change
  (a split that also renames, a merger) or a `.tt` SPLIT row between them,
  nor when a declaration renames the old symbol elsewhere; a corporate
  action naming both that books nothing (a CUSIP/ISIN change row) leaves
  the contract id's verdict in force. A declared date after an IB
  account's first new-symbol trade stops the run naming that account and
  day, and a declaration the other way round (new to old) is an ATTENTION
  line. IB's `.OLD` placeholder is
  never a rename target. The weaker
  look-alike hints of Questrade, RBC and Webull stay suggestions, now
  printed as the `.tt` line.
- **Legacy ticker.map lines keep working.** A `JOURNAL A B` line is read as
  `TOBASE A B` and a dated `RENAME` line as the event, with one Warning per
  run. A JOURNAL line no longer folds the two listings in the holdings view
  on its own: the journal's legs move the units (the broker's, or a `.tt`
  JOURNAL line's when the export lacks them); the tax books are unchanged,
  and the Warning says so.
- **`taxjson format-map` migrates the dated events.** It rewrites each
  `JOURNAL` line as `TOBASE` and moves each dated `RENAME` line, with its
  comments, to `inputs/<account>/renames.tt` (in the first account whose
  books carry the change, one per account kind); the dry run shows both,
  and names a `.tt` JOURNAL line where the holdings show a migrated
  journal's listings long and short. It simulates the run first: unless the
  rename events booked after the move — the project's own `.tt` lines
  included — are the ones booked now, nothing is written (exit 2, naming
  the lines). Every target is checked before the first write (a link to
  outside the project, of a file or an `inputs/<account>` folder, is
  refused), a block a `.tt` file already holds is not added twice, and
  `--check` also fails on a map `taxjson run` refuses. The missing-history
  checks read a `TOBASE` line between a fund's two currency lines as the
  journal the `JOURNAL` line was. `taxjson init`
  writes no dated event in ticker.map; the account READMEs show the `.tt`
  lines.
- **Every dated event is recorded with its source.** `work/dated_events.state`
  lists each journal and rename with where it came from (`tt`, `map`,
  `ib-conid`, `broker`) and its place; `taxjson renames --json` and the
  cross-listing state carry the source too. A run that stops removes the
  last run's record rather than leave it describing other books.

### Fixed

- **The cross-listing loss check finds a class share's US line written
  without the class letter**, scales the other listing's units for a split
  or consolidation when it tests that they are still held at day 30
  (Canada), and lists at most 20 pairs on the run's console, counting the
  rest in one line (`taxjson scan` lists every one). Equal names are still
  required.
- **The `.tt` total warning quotes an `ACQUIRED` line as written** and
  gives the price that agrees with its total instead of a fee column the
  line does not have; the warning and the `--strict` stop name the file
  the same way the "Reading" step line does (an account-number-like part
  masked).
- **The mid-run short-sale note says what this run booked.** It read the
  last run's cross-account wash file, rebuilt only after the note: after
  adding the purchase that closes a short sale and re-running, it still
  said "in no total" while the closing summary said the sale was in the
  totals (and the reverse after removing it).
- **A journal no longer hides an unrelated sale with no purchase near it.**
  The missing-history checks read a day near a journal's legs buys first
  when it held a buy of one listing and a sale of the same quantity of the
  other — any quantity, either way, any number of days. A sale with no
  purchase on one listing and a buy of the other a few days from an
  unrelated journal between the two listings was read as that journal's
  trades and said nowhere. Now only the one nearest day whose trades are the
  journal's own counts: its quantity, the listing it moves from bought and
  the listing it moves to sold.
- **A loss on one listing with the other listing bought within 30 days is
  flagged.** Two listings of one security (a TSX line and its NYSE line)
  that the books keep apart kept such a loss allowed with nothing said.
  Now a loss on one listing with another listing of the same root, under
  an equal name, bought within 30 days in any of your accounts (Canada:
  still held at day 30) is a Warning naming the `TOBASE` line that makes
  them one security and the `DISTINCT` line that keeps them two;
  `taxjson ticker-map --suggest` offers the `TOBASE` line, `taxjson scan`
  lists it (XLIST-LOSS) and `run --strict` stops until one is in
  ticker.map. Both countries (the US: a wash sale, no still-held test).
- **A `.tt` total that disagrees with its quantity, price and fee is said
  on the console.** A line whose total was more than 1% off quantity x
  price + fee was booked as written with the warning only in the `.sum`
  DIAGNOSTICS. The run now shows it as a Warning naming the file and line,
  both figures and that the total is what is booked (on every run),
  `run --strict` stops on it and `taxjson checklist`'s run-clean step
  lists it.
- **A short sale closed within the year is no longer said to be in no
  total.** The run's mid-run note now follows how the gains engine booked
  the sale, as the closing summary and `taxjson sum` do, and
  `taxjson find-missing-history` counts only sales in its in-year sales
  and proceeds (the covering purchase was counted as a second sale).
- **`late=fold` re-books only the rows after the account's own rename
  row.** With a broker rename row a few days after the declared date, a
  sale of the old ticker between the two was re-booked as the new symbol
  before the rename row moved the position: Canada stopped ("would merge a
  LONG position into an existing SHORT pool"), the US engine dropped the
  sale's gain. The rows folded are now the ones `taxjson renames` calls
  late.
- **Canada: an old-ticker trade on the day of an evening rename row is
  late.** The rows `late=fold` re-books (and `taxjson renames` lists) are
  now the ones the country's engine takes after the account's own rename
  row. The Canada engine orders by settlement date and takes a ticker
  change ahead of every trade executed that day, so a morning sale before
  an evening corporate-action row (IB's 20:25) stayed in the old ticker,
  went short and left the total; with no declaration, `run --strict` let
  it pass. The US order (trade date and clock time) is unchanged.
- **A same-day chain through a broker's rename row books.** A broker's or
  a `.tt` SPLIT A to B and a declared `RENAME` B to C on the same date
  booked nothing for B to C ("books nothing") and C went short.
- **A rename of a symbol ticker.map respells books.** With `GLOBAL RAW
  OLD` in ticker.map, a `.tt` `RENAME <date> OLD NEW` booked nothing on
  the RAW rows; it now books on every raw spelling mapped onto OLD, and
  the refusal of a line naming the raw spelling gives the line to write.
- **`run --strict` stops on a declared rename that books nothing**, like
  the other unresolved rename items.
- **`run --fast` re-parses an IB account when the project's last other
  IB statement is removed.** The ticker change one contract id shows
  stayed dated from the removed statement's rows.
- **An event's `late=` applies to the accounts that held the old ticker.**
  Another account that bought the old ticker only after the change (maybe
  another company's shares) had its rows folded into the new symbol by a
  `late=fold` it never declared; they are now kept, listed as undeclared
  until a line of its own says.
- **An undated ticker.map line never joins an option to its stock.**
  `GLOBAL`, `TOBASE`, a legacy `JOURNAL` or a `RENAME` without a date that
  joined an option contract or a future with a share listing (or two
  different contracts) was accepted silently and pooled them; `taxjson run`
  now refuses it naming the line, and `taxjson format-map` files it under
  Unrecognized. A respelling of one contract (the same expiry, right,
  strike and market) is still allowed.
- **`taxjson format-map --write` no longer refuses moves that keep the
  books.** An account with no books yet of the other kind (a crypto account
  with no inputs, for a securities ticker change) counted as a change, and
  a moved line went to the first account even when that account's own
  `.tt` line chose the other `late=`; such an account is now left out, and
  the line goes to an account whose own lines agree.
- **A declared rename that books nothing is said.** A `.tt` (or legacy
  ticker.map) RENAME no account's books carry (a typo of the symbol, a late
  date) gets a Warning naming the line, and `taxjson renames` lists it
  (`--pending` counts it; `--json` lists it under a new `unused` key: its
  date, old and new symbols, `late`, source, places and `.tt` line, and
  `pending` counts it). A chain declared for one day (A to B, then B to
  C) books both links.
- **A journal between two listings stays inside its account.** The
  transfer pairing pooled every account's transfer legs, so a journal's
  legs in one account (a Questrade BRW pair, RBC's `J~` legs, a `.tt`
  `JOURNAL` line) could cancel another account's transfer-in from outside
  the books (its stated book value dropped, the sale reading the other
  account's cost) or pair with a taxable transfer-out as an in-kind
  contribution to a plan. A journal's legs now cancel within their own
  pair, and RBC's `J~` reference is read on RBC's TFR rows only.
- **A Questrade currency journal is joined with `transfers = true` too.**
  The parser pairs the two BRW legs of a journal between a security's CAD
  and USD lines, but the pair id reached only the transfer sidecar: in an
  account that keeps its transfers in the books the run never saw the
  journal, and joined the two lines only when their names were equal word
  for word. The id is now kept on the book rows as well.
- **No false short after a Norbert's gambit without a ticker.map `JOURNAL`
  line.** RBC books a gambit as a buy of one line and a sale of the other on
  one day, with the journal's transfer legs dated the settlement day; its row
  clock puts the sale first. The missing-history checks read such a day buys
  first only for a `JOURNAL` line, so with a `TOBASE` line, or with the run's
  own join, they reported a short with no purchase. They now read every
  journal the books show the same way (a join of the run, a Questrade pair
  id, RBC's J~ reference on the two legs, a `.tt` `JOURNAL` line), the legs
  in-leg first — on the journal's own days only: its legs' days and the day
  of its trades (a buy of one listing and a sale of the same quantity of
  the other). A `JOURNAL` line is still read, on its days of such trades.
  A `TOBASE` line names no journal: read as one on every day of its
  symbols it hid a genuinely missing purchase (a sale with no purchase on
  one listing and a buy of the other, or a later sale and rebuy after a
  gambit). A day that looks like a journal with no evidence of one is
  named with the `.tt` `JOURNAL` line to add.
- **"NOT in `taxjson sum`" only when it is true.** The closing summary and
  `taxjson sum` said a sale with no purchase in the files was missing from
  the totals even when the gains engine had booked it (as a short sale a later
  purchase of the year closed, or from a purchase it reads first). The claim
  is now made only for sales the gains files lack; a short closed within the
  year is a Warning saying it is in the totals at the later purchase's cost,
  and a sale the engine matched is an Info line. `sum --json` lists those as
  `no_purchase_in_totals`.
- **A broker's own journal between two listings is joined on its legs'
  names.** An RBC Norbert's gambit (`TFR … TRANSFER TO C$` / `FROM U$`), an
  IB InterDepot listing flip or a Questrade BRW journal compared every name
  either listing ever had, so a fund renamed after the journal, or a listing
  another broker spells without LTD, left the pair as a suggestion. Such a
  pair (one account, one day, the same quantity, both legs in the broker's
  journal wording) now compares its two legs' own names; a corporate-form
  word ending one name only (not one inside a name, never `LP`), a leading
  `THE` and a `COM NEW` spelling no longer block it.
  Another share class or company still refuses.
- **Equal journals days apart pair on their own day.** Two gambits of the
  same size two days apart were "the legs pair with more than one other
  leg"; journal legs unique on their day now pair first, and a reference
  both legs carry (RBC's `J~…`, Questrade's journal pair) is their pair id.
  A listing that several journals map onto (a fund's US-dollar line under
  two symbols over the years) is joined to each — when those listings are
  one security with each other too (a PLC and a CORP that each match a
  name stating no form are not).
- **A journal's legs pair inside their account before any other
  transfer.** The cross-listing pairing first cancelled same-listing legs
  across all accounts, so another account's transfer-in of the same
  listing on the day of an RBC gambit's `J~` legs (or a Questrade BRW
  pair in a US project) took one of the journal's legs: the journal was
  only suggested and its sale read as a short. Legs that carry the
  broker's reference now pair in their account first, in both countries,
  and a journal's leg never cancels another account's transfer.
- **A `.tt` `JOURNAL` line bigger than the broker's journal a day away is
  said.** Such a line on the broker's own date already stopped the run;
  dated a business day off (RBC dates a gambit's trades a day before its
  `J~` legs) it was booked in full on top of the broker's journal without
  a word. It is still booked, with a Warning naming both and the date to
  use to restate the broker's journal.
- **A share class or warrant is not a listing.** A `.tt` `JOURNAL` line
  between two symbols with one root joins them; the root dropped any last
  dotted part, so two classes (`QZB.A`, `QZB.B`) or a warrant and a London
  line (`QZA.WS`, `QZA.L`) joined with no evidence. Only a venue suffix of
  `data/markets.toml` is set aside now. And two partners of one hub
  listing whose names state different corporate forms (an LP and a CORP)
  are no longer pooled through it.
- **A move between brokers over a weekend pairs.** The transfer pairing
  window is 5 business days instead of 5 calendar days, and so is the
  window in which a Questrade or RBC transfer-in reads its listing from
  another broker's transfer out.
- **`taxjson sanity` folds the listings the run joined.** A broker position
  on a listing that `taxjson run` joined to another by its transfer journal
  read as missing from the books; sanity now applies the run's own joins
  (`work/ticker.map.effective`) as well as ticker.map.

## v0.23.1 (2026-10-06)

### Fixed

- **A broker export with two byte-order marks is read.** A file saved as
  "CSV UTF-8" by a tool that adds its own byte-order mark in front of one
  the file already had stopped with "cannot detect broker" (or a Questrade
  "missing required column"), although its header was right. Every leading
  mark is now dropped, for every broker.

## v0.23.0 (2026-10-06)

### Fixed

- **No account number in a security name.** An RBC or Questrade transfer row
  whose description ends with a bare account reference (`TO ACCOUNT n`,
  `TFR TO n`, `TFR FROM n`) with no `TRANSFER` word before it kept the account
  number in the security name the run compares and shows (warnings,
  `taxjson ticker-map --suggest`). The reference is now cut either way.
- **A ticker.map line naming an IB temporary symbol now wins over the fold.**
  The parser folds IB's time-stamped symbol onto its ticker; it did so even
  when a ticker.map line (`GLOBAL`, `RENAME`, `TOBASE`, `DISTINCT`, an
  `EXTRACT` target, `QUOTE` …) named the stamped symbol, so the line never
  applied. Such a symbol now keeps its rows and the line decides; the Info
  line says so.
- **A lookup line in ticker.map now stops the listing correction for its
  symbol.** The run reads a Questrade or RBC bare ticker on a USD row as the
  TSX listing when the books show it, unless ticker.map names the symbol; a
  `QUOTE` or `T1135` line written for the `.US` spelling did not count, so
  the rows moved away from the line. A line naming the symbol in any keyword
  now keeps the row currency's listing.
- **A command started with its output or error stream closed works again.**
  `taxjson sum >&-` printed a traceback and `taxjson run --no-input 2>&-`
  stopped with exit 1 and no output (both exit 0 on v0.22.0): the start-up
  wrapper that spaces console messages wrapped the missing stream too. A
  missing stream is now left alone.
- **`taxjson ticker-map --suggest` no longer offers a conditional hint as a
  plain suggestion.** A parser hint phrased "only if …" (RBC's dividend on a
  symbol no RBC file trades, which names `TOBASE ROOT.US ROOT.TO`; RBC's
  re-described option; IB's currency-tagged symbol; a Questrade code's
  look-alike) is offered only when the project's books hold every symbol the
  line joins. For a US stock whose other listing appears nowhere in the
  project, `--suggest --write` wrote a line moving its rows to a TSX listing
  that does not exist. A suggestion covered by another suggestion is now
  listed under "Covered by another suggestion", not "Already answered by
  ticker.map".
- **A Questrade currency journal (Norbert's gambit) from the website export
  is booked as one.** Its two BRW rows (`... JOURNAL POSITION TO USD` /
  `... JOURNAL POSITION FROM CAD BOOK VALUE ...`, or the reverse) name the
  bare symbol: the USD leg became a US listing and the later sale, written
  under Questrade's internal code, read as a short in find-missing-history.
  The parser now pairs the legs, books the USD leg on the security's
  US-dollar line (the account's own USD rows, else `SYMBOL.U.TO` by the TSX
  convention in `data/markets.toml`), books the code under that line (in the
  one internal-codes note), and a Canadian run joins the two lines as a
  ticker.map `JOURNAL` line would (tax-logic CA-XLIST-03). A leg with no
  partner is an ATTENTION line naming both row shapes.
- **IB temporary symbols are folded onto their ticker.** Around a corporate
  action IB lists the old contract under a time-stamped symbol (the stamp
  YYYYMMDDHHMMSS, then the ticker) beside the ticker itself. Its rows are now
  booked as the ticker, with one Info line naming the fold; the "several
  symbols" hint no longer suggested mapping the real ticker onto the temporary
  one.
- **A ticker change IB shows only through one contract id is suggested as a
  dated rename** (`RENAME OLD NEW YYYY-MM-DD`: OLD is the symbol whose rows end
  first, the date the first row of the one that continues), not an undated
  `GLOBAL` line, and never toward a temporary symbol. `taxjson ticker-map
  --suggest` reads it, and a dated RENAME of the pair in ticker.map answers it.
- **No TOBASE / JOURNAL suggestion between two different companies.** A
  transfer journal between two listings whose security names share no leading
  company word is neither joined nor suggested.
- **Symbol collisions are detected.** A `.US` symbol whose rows name two
  different companies, one of them a Canadian-listed fund's US-dollar units (a
  TSX currency fund's US-dollar unit booked `.US` beside an NYSE stock of the
  same root at another broker), is now a `Warning:` on the run's console, and `taxjson ticker-map --suggest` offers the
  `EXTRACT words | USD | ROOT.U.TO` line that separates the fund's rows (its
  words the shortest run common to every description of the fund in the
  project and in no other row's), plus the `JOURNAL` pairing the unit with its
  Canadian-dollar line. Where no such run of words exists, the line is a
  template with a placeholder: listed, never written by `--write`.
  The suggestions now include `EXTRACT` lines, such as RBC's US-dollar unit
  hint. The TSX unit-class spelling (`ROOT.U.TO`) is a convention in
  `markets.toml`.
- **A Questrade or RBC symbol filed on the other currency's row is read as
  the listing the books show.** Questrade's website export files interlisted
  shares that arrived from another broker under the TSX ticker on a USD row;
  the listing came from the currency, so the transfer-in became a `.US`
  listing that does not exist and the cross-listing join pooled the company
  under it. When the transfer journal pairs it with another US ticker's
  transfer out (or the same ticker's other listing) under an equal name, or
  shares that arrived by an unpaired transfer have their `.TO` listing in the
  books under an equal name, every row of the
  symbol is booked as the `.TO` listing, with a Warning naming the
  `DISTINCT` line that undoes it; `taxjson ticker-map --suggest` shows the
  explicit lines (tax-logic CA-XLIST-02 / US-XLIST-02).

- **Cross-listing auto-join: a dealer's trade-confirmation wording is no longer
  part of the security name.** An RBC or Questrade trade row's
  "UNSOLICITED WE ACTED AS PRINCIPAL AVG PRICE SHOWN-DETAILS ON REQ DA" left the
  word AS (a corporate form) in the name, so a real pair of listings stayed a
  suggestion. RBC rows are now read through their own name path (the event code
  and dividend / transfer wording cut too, so another account's number no longer
  shows in a suggestion); a share designator in the cut wording is kept and
  another class is still refused (CA-XLIST-01 / US-XLIST-01).

### Changed

- **taxjson.toml: the file header sits on `[settings]`, and a blank line
  separates `[settings]` from its first group**, so the table line stands apart
  from the data. `taxjson format` applies it to an existing file.
- **Wider output.** On a terminal, text now wraps at the terminal's full
  width, up to 160 columns (it was capped at 100); piped or redirected it
  wraps at 120 (was 100). `TAXJSON_WIDTH` still sets the width, and 0 still
  turns wrapping off. What the run writes to `work/` and `reports/` is
  unchanged.
- **Messages without indented continuation lines.** On the `taxjson run`
  console and in every command's `Info:` / `Warning:` / `Error:` messages, a
  message that wraps, its details and its `- ` items continue at the left
  margin instead of two spaces in, and a message that takes more than one
  line is followed by one blank line (a one-line message or a `==>` step is
  not; the output never ends with a blank line). `taxjson checklist` shows
  its progress as `==> Checking ...` steps.

### Added

- **AGENTS.md and a knowledge pack for AI assistants.** AGENTS.md says how to
  help someone run taxjson (the checks to run first, privacy, no tax advice)
  and how to change the code (the gate, releases, the conventions the tests
  enforce). `docs/troubleshooting.md` gives each known problem as the message
  a user sees, how to check it, the cause, the fix and the code;
  `docs/architecture-map.md` gives each feature's files and functions.
  `tests/test_knowledge_pack.py` keeps them true: every file, symbol and
  command they name must exist, every "Fixed in" must be a release, and they
  must hold no personal data.
- **Bug reports: one Markdown template, `.github/ISSUE_TEMPLATE/bug_report.md`,
  replaces the `bug_report.yml` form.** It asks for the version, Python, OS,
  the console's messages and the checklist and sanity summaries, never for
  amounts, account numbers or names, and for a made-up input built from the
  `examples/*_demo.csv` files instead of a real export. AGENTS.md tells an AI
  assistant to build the report the same way and show it before filing.
- **`docs/tax-rules.md` and `docs/settings.md`.** The tax rules page takes each
  rule taxjson applies (Canada and the United States in separate parts) and
  gives its source from REFERENCES.md, its `taxjson tax-logic` rule ids, the
  code that implements it and its known limits. The settings page covers every
  taxjson.toml key and every project file (ticker.map, `.tt` lines,
  missing_history.json, the generic importer mapping, holdings TOML, elections,
  crypto send decisions) with the meaning, default, country, when to change it
  and an example. `tests/test_settings_doc.py` fails when the validator accepts
  a key or tax-logic adds a rule that the pages do not cover.

- **In-kind moves between a taxable and a registered account are booked.**
  `taxjson run` pairs a taxable account's transfer-out with a registered
  account's transfer-in of the same stock and quantity within 10 days (or the
  reverse), across brokers; a move between two taxable or two registered
  accounts stays a move of your own. Canada: a contribution in kind is a sale
  at fair market value on the transfer date — a gain is taxed, a loss is
  denied for good (s.40(2)(g)(iv)), shown by `taxjson sum` and form-export on
  its own line and never added to an ACB — and the plan's purchase counts for
  the superficial-loss rule whatever `transfers_as_acquisitions` says. A
  withdrawal in kind is a purchase at fair market value (the run notes the
  T4RSP / T4RIF income; a TFSA withdrawal is not taxed), so its later sale no
  longer reads as a short. US: a transfer of shares into an IRA, Roth or
  401(k) is warned about and not booked (contributions are cash only); a
  distribution in kind is a purchase at fair market value. The value comes
  from a new `.tt` `INKIND` line, else the market value IB prints on the
  transfer row, else Yahoo's close on the day (marked ESTIMATED; with
  `TAXJSON_OFFLINE` and no cached close the run stops and prints the line to
  add). One warning per run lists each move; `taxjson transfers` labels the
  rows. Tax-logic CA-INKIND-01..06, US-INKIND-01..03.
  Pairing order: legs of the same kind (taxable with taxable, registered with
  registered) pair first across the project, so two moves of your own are
  never booked as a contribution and a withdrawal whatever their gaps; a
  taxable leg pairs with a registered one only when neither has another
  plausible partner, else the pair is listed NOT booked as ambiguous with the
  line that settles it (`run --strict` stops). `INKIND <date> <symbol> <qty>
  plan=own` declares a transfer row a move of your own. A transfer-out a plan
  received in parts (silent before) is warned about, and an `INKIND` line for
  it books the move with the plan's rows that add up to it. Two identical
  moves on one day are two sales (the books' merge kept one). The close cache
  is keyed by the Yahoo spelling too, an `INKIND` value that is not a finite
  amount is refused, and an empty `plan=` names the values it takes.


## v0.22.0 (2026-10-06)

### Added

- **`taxjson ticker-map --suggest [--write]`** (help group "Set up") lists
  every ticker.map line the last run suggested — listings a transfer journal
  pairs that the run did not join, a Questrade code with a likely ticker, a
  ticker change IB, Questrade or RBC shows, a coin's Yahoo id — each with its
  reason. `--write` appends the chosen ones (asked one by one on a terminal;
  `--all` adds every one), each under a comment, never a line the map already
  answers, keeping `ticker.map.bak`.

- **`taxjson redact` with no file copies the project's inputs and redacts the copy.** Run in a
  project (or with `-C DIR`), it copies the whole `inputs/` folder to `inputs_redact/` (or `--out
  DIR`), keeping every folder and text file (exports, `.tt` files, sidecars, READMEs), and redacts
  the copy: you get two folders, the originals untouched and a redacted inputs tree that `taxjson
  run` still parses. An id gets the same placeholder in every file, and a file or folder name that
  holds an account number is renamed to a unique placeholder; the old and new names are printed on
  the console only. Binary files are not copied (each is named in a warning). An existing copy is
  replaced only with `--force`, never through a symlink; `--check` reports what it would replace
  and writes nothing. `taxjson run` never reads `inputs_redact/`, and the `.gitignore` that
  `taxjson init` writes lists it. `taxjson redact FILE ...` works as before.

### Fixed

- **Two listings are joined by their transfer journal only when their names are
  equal.** The automatic join used to accept a name that is part of the other's
  or states a share class the other leaves out, so "<NAME> CORP" could join
  "<NAME> CORP CL B", a partnership's LP units its exchangeable corporation, a
  bank another issuer whose name adds "OF CANADA", and an index ETF its
  currency-hedged line. Now every word counts once case, punctuation,
  abbreviations, broker wording and the words COMMON / SHARES are set aside —
  the corporate form (LP, CORP, TRUST, FUND) and every designator included;
  anything else stays a `taxjson ticker-map --suggest` line. Each join is now a
  `Warning:` naming the pair and the `DISTINCT` line that undoes it.

- **A Questrade code is no longer matched to a longer name without evidence of a
  cut.** A description whose last word is a complete word starting a longer one
  (PARTNERS / PARTNERSHIP) matched that longer name; a cut-off match now needs
  the description to be exactly the export's width.

- **Another broker's security name keeps its class.** Questrade's event wording
  (dividend, transfer and "COMMON STOCK ..." suffixes) was cut from every
  broker's names, so "<NAME> INC COMMON STOCK CLASS C" lost its class. It is now
  cut from Questrade descriptions only, a designator after COMMON STOCK is kept,
  and a one-sided class letter or ORDINARY / ADR is tolerated only when the
  transfer pairs with no other leg and no designator went with cut-off wording.

- **A name with a very long run of spaces is read at once** (it took over a
  minute).

- **A joined or suggested listing is one ticker.map token**: a symbol holding
  `#` or a Unicode line separator is never written into the map.

- **`taxjson ticker-map --suggest` no longer offers a rule quoted inside a
  broker's description** in a message; only the message's own suggestion
  counts.

- **A netted move inside a loss's window is listed.** With the default transfer
  policy, a move between two of your registered accounts, or a zero-net
  transfer cluster in one, was netted out silently even inside a taxable loss's
  30-day window; it is now in the run's one transfer warning ("moved rrsp→tfsa
  … inside the … loss window — if one leg was a contribution, the loss may be
  superficial"). A split in the taxable books during such a move still stops
  it from being netted, whatever the policy.

- **`taxjson wash-radar` follows `transfers_as_acquisitions`.** The radar
  always netted transfers the strict way and never counted one as a purchase;
  it now uses the project's policy (default: account moves; strict: a
  sheltered transfer-in counts as an acquisition), and `taxjson run` passes it.

- **A short carried through a rename is judged with its new symbol.** A
  position that went short under an old ticker and was renamed (a broker's
  rename, a `.tt` line, a dated ticker.map RENAME) was listed as having no
  effect on the year even when the new ticker traded in it, so
  `find-missing-history --write-missing-history --outside-year` could record it
  and move the year's gain. The new symbol's activity now counts for the old
  pair.

- **`--outside-year` reports and writes safely.** It counts only the entries
  this run adds, refuses a `missing_history.json` entry that is not an object
  before writing anything, and keeps a symlinked file a link (its target
  rewritten, a backup kept; a link leaving the project is refused).

- **`taxjson ticker-map --write` keeps a symlinked ticker.map a link** (its
  target rewritten with its permissions and a backup), and refuses a link
  leaving the project, as `migrate` and `format` do.

- **A Questrade dividend row's code is matched by the security's name, not the
  event wording.** "<NAME> CASH DIV ON … SHS REC … PAY …" and "<NAME> SUBST PAY ON …
  IN LIEU OF DIVIDEND" are compared as <NAME>, so the same security's plain name
  elsewhere at the broker identifies the code. The warning quotes the full description.

- **Questrade internal symbol codes pair with more transfers.** The names of the two legs of a
  transfer are now compared with common abbreviations read as the word (RES / RESOURCES, MFG, HLDGS,
  INTL, `N V` / NV, `&` / AND ...) and broker boilerplate cut (a depositary's `REPSTG 5 COM ...`,
  `TRANSFER IN INTERACTIVE BROKER...`, IB's `/CAYMAN ISL` domicile); a class letter or ORDINARY /
  ADR stated by one broker only no longer refuses a transfer that pairs by quantity and date (two
  different ones still do). A code named only by a description Questrade cut off matches the one
  listing whose Questrade description in another account starts with it. Another share class or
  company is still never joined, and a near miss names the `GLOBAL` line to add.
  `symbol_codes.names_agree` is the one test, for reuse.

### Changed

- **A sheltered account's transfer is an account move, not a purchase.** A
  TRANSFER-in to an RRSP/TFSA (IRA) dated inside a taxable loss's 30-day window
  no longer stops `taxjson run` with an "arrival date" error, and zero-net
  transfer clusters near a trade are netted without asking for a DECLARED
  attestation. The shares still count as held; the run prints one warning
  listing each transfer-in inside a loss's window, and each netted move or
  zero-net cluster with a leg inside one, so an in-kind contribution can be
  recorded as a BUYSELL. `[settings] transfers_as_acquisitions = true`
  restores the old strict treatment (tax-logic CA-SL-16/17, US-WASH-23/24).
- **Two listings moved by a transfer journal are joined automatically.** When a
  broker journals a position from one listing to another (an out-leg of one
  symbol and an in-leg of another, the same quantity, within 5 days, in your
  accounts), the pair is unique and the exports' security names are equal
  word for word (the corporate form and every share designator included),
  `taxjson run` books the two listings as one security, as a ticker.map
  `TOBASE` line would, and says so in one `Warning:` per account naming each
  pair and the `DISTINCT` line that undoes it. A ticker.map rule renaming either listing, or a
  `DISTINCT` line for the pair, wins; anything less certain stays a
  suggestion. Every equity
  account is now read in the run's first pass when there are two or more.
- **`taxjson run` lists only the short positions that bear on the tax year.** A position that
  went short with no purchase in your files is listed one by one when a row of the tax year draws
  on it or touches it (Canada: or another taxable account trades the symbol that year, one ACB
  pool); the others are one `Info:` line under the new step `==> Checking for missing purchase
  history`, after every account's books. `find-missing-history` sorts by the same test (new
  ACTIVE section; a position still short at the year's start says so), and
  `find-missing-history --write-missing-history --outside-year` adds the others to
  missing_history.json so the run stops listing them, without changing the year's numbers. The
  .diag files and the .sum DIAGNOSTICS keep every line.
- **`taxjson list` tells a missing purchase from a real short.** A short that is a sale with
  nothing to close (no short-sale marker, a sale the broker coded closing, or any short in a
  registered account) is marked `missing history?` (JSON: `missing_history_suspect`), and
  `list --negative` lists **Short positions** and **Missing history** apart, ending with the
  command that records them — the pairs `find-missing-history` reports.
- **Each engine message once per run.** A short-position warning said by an account's gains and
  again by the blended pass (or a failed stage's echo, or a registered account's short read as
  context by a taxable pass) is shown once.
- **The run's console in order.** Lines no longer interleave through a pipe (`2>&1 | tee`); the
  checks before the first stage are under `==> Checking the project`; a failed stage's last line
  repeats its error; the resolved Questrade codes note shows one code per line.
- **taxjson.toml descriptions use the full 100-column width.** `taxjson init` and
  `taxjson format` wrapped `## ` prose at 79 columns; they now wrap at 100, the house width.
  `taxjson format` reflows an existing file's template text.


## v0.21.0 (2026-10-06)

### Changed

- **`taxjson run` reads as a short list of steps and messages.** Every
  console line now starts with `==> ` (a step the run is doing, in plain
  words: `==> Reading 2 files`, `==> Processing corporate actions`,
  `==> Calculating capital gains`, `==> Writing summary reports/tfsa.sum`),
  `Info:`, `Warning:` or `Error:`, or two spaces when the line above
  continues; there are no blank lines and no nested indentation. Each
  input file is one line, `Info: File inputs/<account>/<file> → identified
  as <broker>` (how it was recognised stays in
  `work/<account>_detect.diag`); the internal native-currency holdings
  stages are no longer listed; the end-of-run summary is one `Warning:`
  or `Info:` per finding; the holdings check names what differs in a few
  lines (`taxjson sanity` still has the tables). Lines that need action
  are plain `Warning:` lines with a capitalised topic (`Warning: Short
  position: ...`); the word ATTENTION, the stage program names and the
  `(content: ...)` detail stay in the captured text only. Frequent parser
  notes are shown shorter (Kraken's matched trade rows, ignored cash
  rows, fees paid in the coin and dust sweeps, rows skipped as not tax
  events, the crypto-sends hint). Every other command's messages follow
  the same line rule: a detail or list item continues two spaces in. The
  captured text — work/*.diag, the `.sum` DIAGNOSTICS, reports/,
  `--json`, exit codes — is byte for byte unchanged.

### Fixed

- **`taxjson run` shows each parser message once.** With two or more
  taxable accounts (or two crypto accounts) the run reads every one of
  them first, so a transfer (or a coin send) pairs across your accounts;
  each account's books then read its files again and printed the
  parser's warnings and counts a second time. The second read now takes
  the first one's result when the command and every file it reads are
  unchanged — each message appears once, under the first pass, and the
  run is faster. A message the first pass gives (a refusal under
  `--strict`, a file that parsed to no rows, an account with no input
  files) is still shown, once. A US project's "wash-sale rule NOT
  applied to crypto" note is said once per run, not once per crypto
  account. work/, reports/ and the `.sum` files are unchanged.

- **Kraken: a dust-sweep leg smaller than the books' zero no longer stops
  the crypto account.** Kraken writes a swept coin's amount to ten
  decimals, so a "convert small balances" sweep can spend a few
  ten-billionths of a coin; that leg became a trade of 0 units, the
  check refused it and the whole account's files went unbooked. A coin
  leg under 1e-09 units is now left out when it is worth nothing too —
  its own USD value and the share of the other side it would take are
  each at most 0.01 USD (in a one-for-one trade, both sides): the
  receipt is split over the sweep's other legs by their USD value, spent
  coins stay in the holdings as a residue, and one note per trade names
  each such leg with its amount (a received leg net of its fee) and USD
  value, calling it a dust sweep only when it is one and a purchase an
  acquisition. A sweep made only of such legs books no sale; a fiat leg
  (CAD) stays cash; a US-dollar leg with no USD value counts at its own
  amount. A leg that small which carries real value (or whose value is
  unknown), and a spend or receive row of amount 0, stop the account
  with a message naming the file, the masked refid, the coin and the
  date — a real sale, purchase or receipt is never dropped. A Kraken
  coin fee or reward that small is left out only when its USD value is
  missing or at most 0.01 USD, otherwise refused the same way. A sweep
  with no such leg books as before.
- **A zero-unit trade names its file and row.** Should one still reach
  the check, the error names the source file, the row id (masked), the
  coin and the date, says that the account's files are not booked until
  it is fixed and what to do, instead of `tx[947]`.
- **The run console no longer slows down on a very long line.** Two of
  the patterns that shorten frequent notes took time growing with the
  square of a line's leading spaces; they now match in one pass, and a
  line over 2000 characters is shown as is.
- **An offline run no longer says it is downloading rates.** With
  `TAXJSON_OFFLINE=1` the rate step reads `Loading cached USD → CAD
  rates`, which is what it does.
- **The run's holdings check no longer reads as an all-clear for
  accounts it did not check.** When some accounts with open positions
  have no holdings file, the run says `positions match the broker's
  holdings files (checked accounts only; N unchecked: ...)` and names
  them.
- **A holdings file stays readable whatever a symbol holds.** Every
  control character in a written string is escaped, so a symbol carrying
  one (an escape character from broker data) no longer writes a TOML
  file that cannot be read back.
- **A parser's skipped-rows note names the file as every other message
  does**, with an account-number-shaped part of the name masked and
  control characters escaped (Kraken, Coinbase, Questrade, RBC and
  generic CSV files).


## v0.20.0 (2026-10-06)

### Changed

- **Messages start with their label: `Info:`, `Warning:`, `Error:`.**
  Every informational line, warning and error a person sees now begins
  with its capitalised label (`Warning: ATTENTION: <topic>: ...` for the
  lines that need action), with the detail lines indented under it as
  before. The command you typed is no longer named in front of each
  message; a line relayed from another program (a stage's error the run
  echoes, a child command's warning) names that program right after the
  label. The old `note:` and `NOTE:` lines are `Info:` lines. A usage
  error is `Error: ...` under the usage line. What a program reads is
  unchanged: with the output captured (`TAXJSON_WIDTH=0`, a stage's
  work/ and reports/ files, the `.diag` behind the `.sum` DIAGNOSTICS,
  what the checklist reads) messages keep the lower-case
  `taxjson <command>: warning: ...` form, and `--json` and exit codes do
  not change. docs/output-style.md states the rule.
- **Questrade internal symbol codes are resolved from your other
  exports.** A Questrade website export writes shares transferred in from
  another broker under an internal code (`X000123`) on the transfer-in,
  its dividends and later rows; each code was an ATTENTION line asking for
  a ticker.map GLOBAL line. `taxjson run` now parses the other accounts
  first and books every row of such a code under the ticker of the
  transfer it arrived by (the other broker's transfer out of the same
  quantity a few days earlier, whose security name matches), else of the
  one listing in your books with the same name; the run says so in ONE
  note per account with the evidence, and `taxjson transfers` lists the
  codes. A code nothing identifies keeps its ATTENTION line, once per
  code instead of once per file, with any near miss named. A ticker.map
  line for the code still wins. Tax-logic `CA-ACB-CODES` /
  `US-BASIS-CODES`.
- **The run console names input files as they are on disk.** The
  per-file detection lines, the parse counts and the "cannot detect
  broker" message showed account-number-like parts of file names masked,
  so two exports named after account numbers could read alike. The
  saved `.diag` and `.sum` files keep masking them (Questrade's parse
  warnings now do too: they named the file unmasked).
- **Prose comments in taxjson.toml start with `##`.** `taxjson init` and
  `taxjson format` now write every prose comment line (the file header,
  the `## --- Project ---` group headings, each key's and table's
  description, the account reference block's descriptions, the note on
  adding accounts) as `## text`, so it no longer looks like a setting
  switched off. A commented-out key or table keeps a single `# `
  (`# province = "ON"`, `# [instalments]`, every line of a commented-out
  multi-line value): deleting the `# ` still switches it on. A key's
  end-of-line comment (`tax_date = "settle"  # settle | trade`) keeps its
  single `#`, since it follows a value and cannot be mistaken for a
  commented-out line. `taxjson format` writes your own comment lines the
  same way: a line whose text is TOML (a key and value, known or not, a
  table line, or a line of a commented-out multi-line value) stays
  `# text`; any other comment line becomes `## text`, whatever number of
  `#` it started with, its text unchanged. A file written in the old
  single-`#` layout formats cleanly: its prose is recognised as template
  text and rewritten, not kept as notes.


### Fixed

- **A Questrade internal code is never booked under another share of the
  same company.** The name match behind the new internal-code inference
  ignored the share class and similar words, so a code whose description
  named class A shares could be booked under the class C listing (and
  class B subordinate voting under class A voting, ordinary shares under
  the ADR, "NEW X INC" under "X CORP"). The class letter, voting rights,
  ADR / ordinary, preferred, units, warrants, rights and NEW now always
  count; only corporate-form words such as INC or CORP are set aside. A
  match by name alone must be exact and name one listing, and is used
  only for a code that arrived by no transfer; anything less is not
  applied, and the code's ATTENTION line names the likely listing with
  the ticker.map GLOBAL line to add if it is right. A transfer of another
  class of the company in the same window makes the pairing ambiguous,
  and a one-word name pairs only with itself.
- **Any ticker.map rule for a Questrade internal code wins over the
  inference.** A DELETE, DISTINCT or dated RENAME line naming the code was
  overridden: the code's rows were rebooked under the inferred ticker
  (past the DELETE), and `taxjson transfers` showed the inference instead
  of the rule. The run and the parser now use one test (does any rule
  name this symbol), and the transfers view says the code is booked by
  its ticker.map rule.
- **A damaged work/ record of the inferred codes is ignored with one
  warning** instead of a traceback or a wrong booking (a wrong shape, a
  symbol that is not a plain listing, a control character, or JSON nested
  too deep).
- **A file name holding a newline or another control character stays one
  line.** The run console now names files as they are on disk; such a
  name could start a line of its own there and in the saved `.diag`
  files a later stage reads back. Newline, carriage return, tab and every
  other control character in a file name are shown escaped (`\n`,
  `\x1b`). Lines relayed with their `Info:` / `Warning:` label (a plugin's
  progress lines, `taxjson-validate`'s findings) show control characters
  from the data escaped too, as every other message already did.

## v0.19.0 (2026-10-05)

### Changed

- **The installer installs taxjson-fetch by default.** The broker-fetch
  plugin (`taxjson fetch` for Questrade and IBKR Flex) stays its own
  package with its own dependencies, loaded through an entry point, but
  the one-line installer now puts it into the same environment unless
  told not to: `--without-fetch` (or `TAXJSON_WITH_FETCH=0`) leaves it
  out and removes it from an install that has it. The opt-out is
  remembered in `~/.config/taxjson/fetch` like the channel, so a re-run
  or `taxjson deploy` keeps it out; `--with-fetch` (or
  `TAXJSON_WITH_FETCH=1`), still accepted, puts it back. The plugin
  sends nothing unless `taxjson fetch` is run for an account with a
  `brokerage` (SECURITY.md). The hint printed when no fetcher is
  installed says to re-run the installer. An existing core-only install
  gains the plugin on its next upgrade, and the installer says so in one
  line: "adding taxjson-fetch (installed by default since v0.19.0;
  re-run with --without-fetch to keep it out)". The remembered file
  accepts `off`/`0`/`no`/`false` and `on`/`1`/`yes`/`true` in any case
  (anything else is reported and the default applies); it and the
  remembered channel are written mode 600 in a private directory,
  replacing (never writing through) a symlink at their path. The plugin
  is installed with `--no-deps`: its only dependency is the core from
  the same checkout.
- **Every command's refusal reads `taxjson <command>: error: ...`**, wrapped
  at the house width with its details indented under it, and the
  `taxjson-*` tools' warnings and notes wrap the same way. What a program
  captures (a stage's work/ and reports/ files, the `.diag` behind the
  `.sum` DIAGNOSTICS, what the checklist reads) is never wrapped, so those
  bytes do not change. Report notes that wrapped at 78 columns wrap at the
  house width (100, or the terminal's).
- **One output style, first for `elect` and `wash-sales`.** Prose wraps
  at 100 columns (at most the terminal's width; `TAXJSON_WIDTH` sets it,
  `0` turns wrapping off), sections are separated by one blank line,
  lists hang under their text, and a long message is a one-line headline
  with indented detail lines (docs/output-style.md). `taxjson elect`
  lists each election with its event, hints and notes aligned and the
  manifest path relative to the project; `elect --pending` numbers each
  option with its description, the hint it needs and the ready `--set`
  line, and ends with one line saying what to do (the checklist shows
  it); the election prompt wraps its descriptions. `taxjson wash-sales`
  fits its table to the width (a long symbol drops COST, then PROCEEDS,
  then lists one record per denial) and explains DENIED as a list;
  `--explain` prints each denial as a block — figures, the pool history,
  the denial, the ±30-day window as a table with a legend — instead of
  the `#`-commented trace (`taxjson audit` and trace files keep it). No
  figure, `--json` output or file changes.
- **The position, period and roll-up views, `stats` and `crypto-sends`
  in the house output style.** `list`, `shares`, `events`/`trades`/
  `divs`/`dil`/`fees`/`gains`/`leaps`/`roc`/`transfers`, the `-sum`
  roll-ups, `winners`, `stats` and `crypto-sends` put their context
  under a short title, fit their tables to the width (the least
  important columns go first — `stats` drops the largest win and loss,
  `transfers` the fee and the sidecar/book column — then one record per
  row), wrap their notes, and print their definitions as lists.
  `fees-sum` (and `reports/fees.rpt`, a report for reading) shows the
  non-option / option split as its own table and the native currencies
  as aligned lines; `crypto-sends` lists each send's facts aligned under
  its id with the ready `.tt` line never wrapped, and the stablecoin
  currency gain per year under its own heading. Their notes, warnings
  and refusals read `taxjson <command>: note|warning|error: ...` with
  the detail indented below. `events PERIOD ACCOUNT` stays re-importable
  `.tt` text, byte for byte; no figure, `--json` output or other file
  changes.
- **Control characters in broker data never reach the terminal.** Text
  shown to a person (anything printed wrapped: messages, notes, tables,
  the run's echoed ATTENTION lines, the closing first-run list) shows a
  control character other than newline and tab as the visible text
  `\x1b` (an ESC sequence in a symbol, a BEL, a carriage return), so an
  export cannot drive the terminal. Captured output (work/, reports/, a
  stage's `.diag`, the `.sum`) keeps its bytes.
- **The "export does not state the contract size" note is one note.** A
  command shown to a person names every option root it assumed a
  100-share contract for in one note with the single `MULT <ROOT> N`
  advice, instead of a line per root; captured output (a stage's `.diag`,
  the `.sum` DIAGNOSTICS) keeps its line per root, and so does a stderr a
  command redirected in-process (the engine run that `wash-sales`' radar
  and `t1135` silence stays silent).
- **The explain-and-check commands in the house output style.**
  `taxjson audit` prints each disposition's pool history in the report
  layout `wash-sales --explain` uses (no `#` column; the trace files keep
  it), wraps its sections under the label gutter and its warnings, and
  keeps the reconciliation's tie-out line whole for the checklist.
  `taxjson checklist` puts each step's detail under its title, one blank
  line between steps, stage headings in capitals; a mark beside a finding
  reads "the detector still says" (the `!!` is gone). `find-missing-history`
  has a title, short capitalised section headings, fitted tables with each
  row's broker note indented under it, the broker-marked shorts as a list
  ending in one "nothing to fix" line, and its fix as a numbered list.
  `edge-cases` and `check-dates` print short headings and fitted tables
  with each row's reason under it. `sanity` lists each group as aligned
  `label:  value` lines (the holdings files relative to the project),
  fits its quantity and cost tables and wraps its notes. `spinoffs`,
  `splits` and `renames` align their figures, wrap their notes and print
  the ticker.map lines to copy on their own lines. `tax-logic` wraps at
  the house width (it wrapped at 88; the rule text is unchanged).
  `channels`, `redact` and `opening` wrap their messages (`NOTE:` is now
  `note:`), and every command's help page wraps at the house width (it
  was capped at 78 columns). The lines the checklist reads from these
  commands keep their text, no figure, `--json` output or file changes,
  and every exit code is the same.
- **The filing and planning views in the house style.** `taxjson sum` and
  `estimate` fit their account tables and the FOR THE RETURN table to the
  width (FEES, then PIL, then DENIED go first when it is short; the
  denied total is in the notes) and list the return's notes, the estimate
  notes and its assumptions as `- ` items instead of one-line paragraphs;
  an estimate row's bracketed note wraps under itself. `form-export`'s
  console view drops the ` | ` table for a fitted one, lists each row's
  note under its table (`- SYMBOL: ...`) and aligns the line totals —
  the `--csv`, `--json` and TXF exports are unchanged. `t1135`,
  `carryover` and `reconcile-slips` do the same (a property's or a
  year's notes under the table, a symbol's detail wrapped under it, a
  NOTES section); `amt`, `instalments`, `option-boundary`, `close-year`,
  `check-filed` and `handoff` wrap at the house width with their notes
  as lists and paths relative to the project. Their warnings and errors
  are a one-line headline with indented details (`!!`, `NOTE:`,
  `WARNING:` and `->` retired); `check-filed` keeps `DRIFTED` on the
  warning's first line. No figure, `--json` output or file changes.
- **`taxjson run`, `init`, `format`, `migrate` and `fetch` in the house
  style.** The run's console wraps every message at the house width: a
  stage's ATTENTION or UNBOOKED line keeps its marker first line and shows
  the rest as an indented headline and detail; the run's own warnings and
  refusals are a headline plus details (`NOTE:`, `WARNING:`, `!!`, `!`
  and `->` retired); the files it writes are named relative to the
  project (`wrote reports/margin.sum`); the "before you trust these
  numbers" summary lists one finding per item. `init` shows its next
  steps wrapped with the `taxjson -C ... run` line to copy on its own,
  and `fetch --list` puts each fetcher's description under it. The
  parsers', merge's and the gains engine's notes and warnings read the
  same way when a person runs them; captured by the run, their text in
  `work/*.diag` and the `.sum` DIAGNOSTICS is byte for byte as before.
  The run's closing summary names every account it counts ("5
  account(s) with open positions and no holdings file" named only four;
  it now names up to six and says how many more). No figure, `--json`
  output or file changes.
- **taxjson.toml reads in groups and compactly** (`taxjson init`, `taxjson
  format`). `[settings]` comes in groups — Project, Currencies, Options,
  Income, Futures — each under a `# --- Name ---` heading, keys
  alphabetical within a group and a blank line between groups.
  Descriptions stay on the line above each key, but there is no blank
  line between keys inside a group or table, and a key whose comment is
  just its list of values (`tax_date = "settle"  # settle | trade`) — or
  what `true` means for a switch — carries it at the end of its line,
  aligned within the group. `[accounts.NAME]` tables are
  key lines only, `type` first (the commented reference block documents
  every account key once). The fetch keys (`brokerage`, `account`,
  `query_id`) are documented without naming a broker. `taxjson format`
  re-lays an existing file into this layout without changing what it
  configures; the previous layout's descriptions are recognised and
  regenerated, never kept as your notes.

- **The planning commands in the house output style.** `taxjson
  wash-radar` prints one section per advisory category (its title and
  row count) with a TICKER / TAXABLE / SHELTERED / CLEARS table and each
  advisory once, wrapped, under the rows it applies to (a warn-only flag
  as its own `note:` line), then the definitions as a list and the
  scope paragraph — not a pipe table hundreds of columns wide.
  `taxjson harvest` fits the width: a table too wide for it is split
  into tables that fit, each led by ACCOUNT and SYMBOL — the position
  and its verdict, the radar's advisory with the last buys, the cost
  with the break-even price (narrower still, one block per position) —
  and the column notes are a list under COLUMNS, the rest under NOTES;
  its pricing progress is a note on stderr. `buy-check` and `sell-check` list each symbol's reasons as
  items under its verdict; `scan`, `watch` (each change an item) and
  `fx-cash` (the cash events as a table) wrap and separate their
  sections; the scope paragraph these share wraps. Their errors and
  warnings are a headline with indented details. No figure, `--json`
  output or `reports/wash_radar_*.json` changes, and `fx-cash` still
  ends with the line the checklist shows.


### Fixed

- **`taxjson format` keeps a wrapped end-of-line comment together.** A
  trailing comment the old aligned layout continued on the lines under
  it (`#` lines indented under the comment, or padded as
  `#      #   more text`) now moves above its key or table line as one
  block, padding dropped; before, only the first line moved and the rest
  went to the next key. A plain `# comment` line, one after a blank
  line, or one under a line with no trailing comment is still the next
  key's.


## v0.18.0 (2026-10-05)

### Security (pre-release review)

- **Backups never overwrite a backup or follow a symlink.** `taxjson
  opening --force`, `find-missing-history --write-purchases --force` and
  `--write-missing-history --force` now keep the replaced file the way
  `init --force` and `format --write` keep `taxjson.toml`: as `.bak`, or
  the next free `.bakN`, written fresh. A second `--force` used to
  replace the only copy of the first version, and the opening backup
  followed a symlink planted at its name.
- **`taxjson opening` writes only cells of a safe shape.** A symbol,
  currency or lot date from a positions report is written into a `.tt`
  line only when it is a plain symbol, a 3-letter code and a
  `YYYY-MM-DD` day; anything else is listed and skipped. The IB, RBC and
  holdings-TOML readers refuse a symbol or currency cell with a control
  character or whitespace in it (a newline could book a line of its
  own), and an IB or RBC currency that is not a 3-letter code. The
  `# From:` header line drops control characters of the file name.
- **Questrade descriptions with long whitespace runs parse quickly.**
  The description key collapses whitespace before its suffix patterns,
  which took minutes on a crafted row; the keys are unchanged.
- **Purchase drafts are written only under a configured account's
  folder.** An account name read from the books that is not a valid
  name or not an account in `taxjson.toml` is refused before it names a
  folder.
- **`init` never writes through a dangling symlink** at `ticker.map`,
  `.gitignore` or an input folder's `README.txt`: the link is left as
  is, with a note.
- The cannot-detect message masks an account id in the file's name, as
  other diagnostics do; a channel tag with a trailing newline is no
  longer taken for a release tag.

### Broker positions reports

- **One reader for each broker's positions report.** A new module reads
  what a broker says you hold on a date into one common row: the
  account masked, the symbol spelled as that broker's trade parser
  spells it, the quantity, the currency, and the cost with what kind of
  cost it is (a Canadian broker's average book cost, or a sum of
  per-lot cost basis). Market value is kept apart and never read as a
  cost. Supported: the Open Positions section of an Interactive Brokers
  Activity Statement, RBC Direct Investing's Holdings Export (columns
  matched by their labels), and the `[[holding]]` TOML (with an
  optional acquisition date per lot). Questrade, Webull, Coinbase and
  Kraken have no positions export the parsers know; Questrade's live
  positions come through the fetch plugin's TOML.
- **A positions report in an inputs folder no longer stops the run.**
  An RBC Holdings Export was routed to the RBC parser, which refused
  it and failed the run; it is now recognised as a positions report,
  listed with the other files as skipped, and never parsed as trades.
  `taxjson-detect-brokerage` prints `positions:rbc_holdings` for it.

### Opening balances (owner request)

- **`taxjson opening ACCOUNT FILE`** turns a broker's positions report
  (one of the readers above) into an opening balance:
  `inputs/<account>/opening_<date>.tt`, one `OPENING` line per long
  position with its quantity and the report's book cost. A position the
  report gives no cost for, a short position or a futures contract is
  listed and left out; `--dry-run` prints the lines instead.
- **An opening balance is not a purchase.** The new `.tt` action
  `OPENING <date> <symbol> <qty> <currency> <total-cost> [<lot-date>]`
  sets a position and its cost on the statement day, but it never
  replaces a superficial loss or a wash sale and is never a recent buy
  in the planning tools. A purchase line dated on the statement day
  was one, and could deny a loss sold in the following 30 days. Old
  hand-written opening lines keep working unchanged.
- **The snapshot replaces the history before it.** The account's
  trades, transfers, renames and cost adjustments of a snapshot symbol
  dated on or before the snapshot day are left out of the books (with
  an ATTENTION line), so a statement that overlaps the download never
  counts a share twice; income rows stay. A left-out sale of the tax
  year, two snapshot dates for one symbol, or an OPENING line in a
  crypto account stops the run.
- Canada: the lines join the pooled ACB, and a foreign-currency cost is
  converted at the snapshot day's Bank of Canada rate (a broker's book
  value in Canadian dollars is used as it is). United States: one line
  per lot with its purchase date, which sets the holding period and the
  FIFO order; a line without a date or with a non-dollar cost stops the
  run. A lot whose own date falls within 30 days of a loss is flagged
  for a manual check. `taxjson tax-logic` states the rules (CA-OPEN-01
  to 03, US-OPEN-01 to 03).
- An opening balance and a transfer-in booked at the broker's book value
  share one rule: shares held but not acquired on that date, never a
  replacement or a recent buy (the wash radar, edge-cases and the
  manual-check flags included). A transfer-in dated on or before an
  opening snapshot of the security is covered by it, never booked at
  its book value as well. The `inputs/<account>/README.txt` that `init`
  writes points to `taxjson opening`.

### `taxjson sanity` compares costs

- **Reads the brokers' positions reports directly** — an IB Activity
  Statement with Open Positions, an RBC Holdings Export — besides the
  holdings TOML, in its arguments and in `holdings = [...]`. A report
  dated before the books' last row is compared with the books'
  positions on its date.
- **Costs, with a reason.** After the quantities, the books' cost is
  compared with the report's for every position that ties: Canada's
  filing ACB (pooled across taxable accounts, superficial losses added)
  against the broker's book value, or a foreign-currency cost against
  the account's own native-currency books; US basis against the
  broker's lot basis. Each difference gets a reason (superficial loss,
  pooling across accounts, return of capital, the broker's currency
  conversion, lot basis against average cost; US: wash-sale additions,
  the broker's lot method) or "unexplained". Informational: the exit
  code is still the quantity check's; `--cost-tolerance` sets the
  margin and `--json` adds the rows.
- **Income on shares the books do not hold.** A dividend row whose
  description states its share count (`ON 500 SHS`) while the books
  held another number on its record date is listed — the usual sign of
  missing history.

### Guidance for a first project (getting-started study)

- **`taxjson run` ends with what to check next.** After `Done.` (and the
  holdings check), a short list counts what the books show is still
  incomplete: sales in the tax year with no purchase in your files that
  `missing_history.json` does not cover, positions at a $0 cost (sold
  this year or still held), shares transferred in from outside your
  books with no cost, accounts with open positions and no holdings file,
  and securities that paid you income the books do not hold. Each line
  names the command that lists it, and the list points to the
  getting-started guide. Nothing is printed when every count is zero;
  the counts always go to `reports/run_summary.json`. A taxable account's
  positions that go short are named on the console as the account is
  built.
- **`taxjson sum` warns about sales with no purchase** that
  `missing_history.json` does not list, whatever the broker: their gain
  is in no total. `--json` adds `no_purchase_uncovered`.
- **"Tainted" is now "unknown cost"** in every message (sum, form-export,
  carryover, t1135, ccd-sum, winners, leaps, reconcile-slips, the
  traces). The JSON keys keep their names; `sum --json` adds
  `unknown_cost_routed` / `unknown_cost_included` beside
  `tainted_routed` / `tainted_included`.
- **`find-missing-history` lists $0-cost shares still held** (HELD), not
  only those already sold, and an entry of `missing_history.json` whose
  purchase is now in the books (STALE: it does nothing; the run also
  says so, in one ATTENTION line). In a US project a stock dividend's shares are
  no longer listed as $0-cost: they share the old shares' basis.
- **Transfers into a taxable account use the broker's stated book
  value.** Shares that arrive from outside your books (a transfer-in that
  no transfer-out of yours cancels) are booked at the book value the
  broker prints on the row (Questrade, RBC), with an ATTENTION line
  naming the broker; a transfer value that is a market value (IB) is
  never used, and such shares are said to have no cost. A `.tt` purchase
  of the security dated on or before the transfer covers it: the book
  value is then not used and the line stops — the override, with nothing
  to configure. A `missing_history.json` entry for the security covers
  it too (your declared unknown cost is kept). US projects: the lot's holding period starts on the
  arrival date (said in the line). The arrival is never the purchase
  that makes a loss superficial or a wash sale. `taxjson transfers` has
  an IN_BOOKS column for each transfer-in. Tax-logic
  `CA-ACB-TRANSFER-BV` / `US-BASIS-TRANSFER-BV`.
- **Messages.** The missing-time-zone refusal says to delete
  `[accounts.crypto]` if you have no crypto; a holdings mismatch names
  missing history as the likely cause on a first project; a failed stage
  is described in words ("reading the broker files for account margin
  (activity.csv)"), not as its command line; each `inputs/<account>/
  README.txt` written by `init` lists which export to download from
  each broker, and the positions reports to keep.
- **A US scaffold fetches no exchange rates.** `source_currencies` is
  left commented in a US project's `taxjson.toml`, with a note on when
  to set it.

### Purchase drafts from the broker's cost (owner request)

- **`taxjson find-missing-history --write-purchases [FILE]`** drafts
  `.tt` purchase lines from the broker's own cost evidence: IB's `Basis`
  on a sale it codes closing with no purchase in your files, and a
  book value a Questrade or RBC transfer-in states. The draft goes to
  `inputs/<account>/purchases_draft.tt.txt`, which the run does not read;
  each line sits under notes naming its source row (ids masked), the
  broker's figure and what to check. You review it and rename it to
  `.tt`. Nothing is booked automatically.
- **One line per lot when IB lists them.** The IB parser now keeps a
  statement's Closed Lots (open date, quantity, cost) on the closing sale
  as evidence. Without them the line's date is the literal `YYYY-MM-DD`,
  which the `.tt` reader refuses until it is replaced, so an unedited
  draft cannot be booked. When your data held part of the sale, the cost
  is left as `COST` the same way, with the arithmetic in the note.
- **Each country's caveats.** Canada: the line stays in the broker's
  currency and is converted at the Bank of Canada rate of the date you
  fill in; IB's figure is the cost of the lots IB closed, not the ACB,
  and a draft says so, louder when another taxable account trades the
  same security (tax-logic CA-ACB-15). United States: a lot's date and
  cost are its basis and holding period (US-BASIS-08).
- **Never overwrites a draft** without `--force` (the old one is kept as
  `.bak`). A file name ending in `.tt` or `.csv` is refused, since the
  run would read it before review. Sheltered accounts, futures, real
  shorts, moves between your own accounts, transfers whose shares your
  files already acquire and sales with no broker figure are not
  drafted; the draft lists them at the end.

### Broker detection reads the file, not its name (owner request)

- **Content first, for every supported export.** `taxjson run` now
  recognises Coinbase and Kraken exports by their header too (every
  Coinbase layout the parser reads, including the preamble lines above
  the header; Kraken trades and ledgers), alongside Interactive Brokers,
  Questrade, Webull (both Trading Summary layouts) and RBC Direct. Each
  signature is the parser's own required-column test, so detection and
  parsing cannot drift, and the signatures never overlap: a test checks
  every sample of each broker against every detector. A file whose
  content matches two exports stops the run, naming both, instead of
  one being picked.
- **A generic mapping is configuration.** A `<file>.csv.toml` sidecar
  (now on any file name) or a `generic_` file with the folder's
  `generic.toml` is honoured before the content. A `generic_` name with
  no mapping no longer overrides a recognised header.
- **The file name is only a fallback.** The `cb_` / `kr_` prefixes and
  the words coinbase / kraken route a file only when no header matched;
  when the header matched and the name says otherwise, the content wins
  and a note says so. RBC files are no longer routed by brand words in
  a preamble without the RBC header (the parser refused them anyway).
- **Every file and how it was detected is printed** by `taxjson run`
  (and `--fast`), one line per file under its account, kept in
  `work/<account>_detect.diag`; the notes reach the `.sum` DIAGNOSTICS.
  `taxjson-detect-brokerage` applies the same rules and prints the same
  line. The "cannot detect broker" message now says to check the
  header first (naming the layout the file nearly matched), then a
  generic mapping, then the rename fallback.
- The Kraken and Webull parsers find a file's companion exports in the
  folder (a trades export's ledger, a Webull export's other years) by
  the same detection, so neutrally named companions are found too.

### Getting started

- **A getting-started guide**, `docs/getting-started.md`, linked from the
  top of the README and from `taxjson help`: install, `init`, which
  exports to download, the first run, and a step-by-step way to find and
  fill missing purchase history (sales with no purchase, holdings with
  missing or partial history, transfers in, $0-cost corporate-action
  shares), then `sanity` and `checklist`.
- **`find-missing-history` ends with the fixes in order**: an older
  export, then the purchase as a `.tt` line (the original purchase for
  shares transferred in), and `missing_history.json` only for what cannot
  be recovered; the $0-cost hint names the stock-dividend `ADJUST` (Canada),
  the election for a merger or spin-off, and the purchase for a transfer.
- **A stock dividend given its cost by an `ADJUST` line is no longer
  reported as $0-cost shares** by `find-missing-history` (and the
  checklist's missing-history step), matching the run, which already
  treated it as fixed.
- Webull: the UNBOOKED line for a non-trade row now says how to enter a
  transfer in (a `.tt` BUYSELL at the original purchase), not a `.tt`
  TRANSFER, which a taxable account refuses.
- A fresh install no longer prints a `SyntaxWarning` from the generic
  importer on its first run.

### An ordered taxjson.toml (owner request)

- **Keys in alphabetical order, one `=` column per table.** The file
  `taxjson init` writes and `taxjson format` produces lists the keys of
  every table — `[settings]`, each `[accounts.NAME]`, the account-key
  reference block, `[estimate]`, `[carryover]`, `[instalments]`, each
  `[[...]]` entry — alphabetically, set and commented-out keys in one
  sequence, with every key line of a table padded so its `=` signs line
  up. The tables keep their fixed order (settings, then the accounts in
  your order, then the year-data tables), and so do `[[...]]` entries. The
  in-table group headings are gone.
- **Descriptions above the keys, no end-of-line comments.** Each key's
  description is now on the line(s) above it, wrapped, and a blank line
  separates one documented key from the next; a commented-out key is
  always `# key = default` under its description.
- **`taxjson format` reorders what you wrote.** Keys added by hand anywhere
  in a table move to their alphabetical place, with the comments above
  them; your trailing comments move onto their own line just above the key
  or table line; a comment block holding a commented-out key of your own
  goes to that key; descriptions an earlier `taxjson init` wrote at the end
  of the line are recognised and regenerated in the new place. As before,
  the formatted file must load to the same configuration or nothing is
  written, and formatting twice changes nothing.

### No built-in security data in the flow (owner request)

- **One shipped market-data file.** The market facts and security lists
  taxjson cannot read from an export — listing suffixes and their
  currencies, fiat currencies, US-dollar stablecoins, Canadian split-share
  corporations, US §1256 index-option roots, Cboe evening-session roots,
  Kraken's legacy asset codes and IB's listing venues — now live in one
  labelled file, `taxjson/data/markets.toml`, read by one module. A
  project's `ticker.map` extends or overrides it one symbol at a time with
  `STABLE`, `SPLITSHARE`, `INDEXOPT`, `EVENING`, `MULT` and `VENUE` lines,
  and the run prints one note per symbol whenever a built-in entry decided
  an outcome, naming the line that would change it.
- **Split-share corporations (Canada)** are read from the market-data file
  and the project's `SPLITSHARE ROOT [NO]` lines instead of a list in the
  code; `taxjson tax-logic` prints the list in force.
- **US §1256 index-option roots and Cboe evening-session roots** are read
  from the market-data file and the project's `INDEXOPT` / `EVENING` lines.
  `taxjson tax-logic` lists every root in force (it used to name four of the
  five evening-session roots, missing the weekly VIX root, and summarised
  the index roots as "and their weekly roots").
- **Crypto: one stablecoin list, no built-in staked-coin fold.** The USD
  stablecoins are one list (the market data, extended or overridden by
  `STABLE` lines): Kraken's private copy of the stablecoins that end in "USD"
  is gone, so RLUSD and FDUSD are now US-dollar cash in a Canadian project
  like the other stablecoins. Kraken's legacy asset codes (XXBT, XETH …) come
  from the market data; its bonded-staking codes are derived from their shape
  (`<COIN><two-digit lock period>.S`, folded when the coin is in the same
  export, otherwise noted with the line to add) instead of a list of lock
  periods; legacy concatenated pairs split on the fiat and stablecoin lists.
  Coinbase's and Kraken's ETH2 are no longer folded into ETH by built-in
  code: add `GLOBAL ETH2 ETH` to ticker.map (both parsers apply bare-code
  `GLOBAL` lines before reading a row); a 1:1 swap between a coin and a code
  that extends it is noted once with that line.
- **One venue and currency table.** The ten-odd copies of the listing
  suffix, currency-to-suffix, ISIN-country and fiat-currency tables in the
  parsers, the engine helpers and the reports (they disagreed: `.VN` was
  missing from several, corporate actions turned `X.V` into `X.V.TO`) all
  read the market-data file now. IB's listing venues are part of it; a
  venue it lacks keeps its currency's suffix and is noted once with the
  `VENUE` line to add.
- **RBC: no named currency ETF.** The rule that moved one named TSX
  US-dollar ETF's USD rows to its `.U.TO` class is gone; a TSX fund's
  US-dollar class is moved by the project's `EXTRACT description words |
  USD | ROOT.U.TO` line, and until there is one the row's `.US` booking is
  an ATTENTION line that prints that line (it used to suggest a `GLOBAL`
  rename, which would also move a real US listing of the same symbol).
- **`taxjson tax-logic` states every law constant the estimate uses**,
  rendered from the same constants the code computes with: per-year
  federal and provincial brackets, rates and basic personal amounts
  (Canada) or standard deduction, ordinary and long-term-gain brackets
  (US); the dividend gross-up and credit rates; the Ontario surtax and
  Health Premium chart; each province's minimum-tax factor (marked where
  assumed); NIIT's rate and threshold; CRA's prescribed interest rates and
  the rule past the table's end; the April 30 balance-due day; the US
  significant-holder thresholds for a reorganization statement. The
  estimate's printed rates come from the same constants.
- **The LEAPS cut-off is a setting.** `taxjson leaps` / `leaps-sum` count a
  long option bought more than `[settings] leaps_months` months before
  expiry (default 9, the market convention; it used to be a fixed 3). Views
  only: no tax figure changes. Set `leaps_months = 3` to keep the old view.
- **Crypto needs your time zone.** Kraken and Coinbase rows (stamped in UTC)
  were dated in Eastern time unless `[settings] local_timezone` said
  otherwise, so a midnight fill near December 31 could land in the wrong
  year for anyone living elsewhere, without a word. There is no default
  zone now: a project with a crypto account and no `local_timezone` stops,
  naming the key and suggesting this machine's zone (`taxjson init` writes
  it when it can read it; `taxjson format` and `migrate` still run).
  Outside a project the parsers need `TAXJSON_LOCAL_TZ`.
- **close-year never borrows a province.** A Canadian project with no
  supported `province` had its carry-forwards estimated on Ontario's
  tables; it now uses a federal-only estimate and says so (the recorded
  figures are federal either way).
- **Webull exercise/assignment charge is a setting.** The parser no longer
  assumes a $1.00 charge: `[accounts.<name>] exercise_fee = 1.00` states
  the account's exercise/assignment charge, and only then is a $0 option
  close plus a stock trade at the strike carrying exactly that charge
  booked as an exercise/assignment. Without it nothing is inferred and each
  such pair is named on the console for you to check. A Webull account
  that relied on the old inference needs the line.
- **RBC's year-end posting day is a setting.** The note that an RBC export
  taken before the broker posts the year's back-dated Dec-31 book-cost rows
  may lack them uses `[accounts.<name>] year_end_posting = "MM-DD"`
  (default `"06-30"`, the previous fixed day) instead of a constant.
- **Option contract sizes are stated, derived or noted.** Only the IB export
  states an option's contract size. For the other brokers a row whose own
  amount fits quantity x price x 10 but not x 100 is now booked as a mini
  (10 shares, with a note) instead of being refused; otherwise 100 is still
  used, and the run says once per option root when that assumed size
  decided an exercise, an assignment or a replacement quantity. A
  `MULT ROOT N` line in ticker.map sets the size for a mini or an adjusted
  series. Ordinary 100-share options book exactly as before.
- **No built-in exchange rate.** A row whose date has no rate in the
  rates file (after the weekend/holiday carry-forward and the 5-day
  look-back) used to be converted at a placeholder 1.35 USD->CAD rate and
  flagged as a validation error; it now stops the conversion, naming the
  row's date and currency pair. The stand-alone converters still accept
  your own rate with `--default-rate`.
- **Yahoo spellings without a special case.** The price chain's Yahoo
  spelling no longer carries a rule for one named US class share; any
  class or series letter is spelled the Yahoo way (`ZZQ.C.TO` ->
  `ZZQ-C.TO`, which the old `.A`/`.B`-only replace missed). `taxjson scan
  --online` now asks Yahoo for that spelling (or the symbol's `QUOTE`
  line) instead of the book symbol, which returned nothing for every US
  listing, so a wrongly paired map entry can be flagged.
- **`taxjson scan` finds a Canadian twin on any Canadian venue.** The
  US-LISTING and MAP-GAP checks looked for a US listing's Canadian line
  only as `ROOT.TO`; a TSX Venture, CSE or Cboe Canada line of the same
  root (or a ticker.map target there) now counts too.
- **Questrade income matching keeps no list of dealer names.** A
  dividend row under an internal code is matched to its security through
  the description; a transfer-in row naming the delivering dealer after
  the security used to be matched only for a hard-coded list of Canadian
  banks and one US broker. Any dealer is now matched (the security's own
  description is a word prefix of the transfer's), and a class
  designation keeps its letter for every class (`CLASS B` and `CL B`
  are one key; only `CLASS A` used to be stripped).
- **IB return of capital: Canada is never "home" in a US project.** The
  IB parser's foreign-issuer test (a non-Canadian issuer's return of
  capital is a dividend, ITA s.90(1)) now runs only in a Canadian
  project; in a US project every issuer's return of capital lowers
  basis, a Canadian one included, even if the parser is asked otherwise.

### Command line

- **`taxjson init` writes only its scaffold accounts** (Canada: margin, TFSA, RRSP,
  crypto; US: margin, Roth, 401(k), crypto) and a one-line note on adding
  more — no commented example account. `taxjson format` drops the earlier
  example from an existing file.
- **`taxjson init` scaffolds the common accounts:** margin, TFSA, RRSP and a
  crypto account in Canada; margin, Roth, 401(k) and a crypto account in the
  US. Any other account (a LIRA, an RESP, a traditional IRA …) is one more
  section and `inputs/` folder; the generated `taxjson.toml` shows a
  commented example of one for the country.
- **The generated `taxjson.toml` documents every key.** Every `[settings]`
  key, every account key and every table (`[estimate]`, `[carryover]`,
  `[[distributions]]`, and in Canada `[[capital_gains_dividends]]` and
  `[instalments]`) is listed, grouped and column-aligned: the values the
  scaffold sets are active, everything else is commented out with a
  one-line description and its default. A Canadian file lists only the keys
  a Canadian project accepts, a US file only the US ones. A test derives
  the full key list from the config check, so a new key without template
  documentation fails CI. `local_timezone` is written with this machine's
  zone when it can be read. The examples in the generated files (and in the
  `ticker.map` stub) use placeholder tickers, not real securities.
- **New `taxjson format`.** Lays an existing `taxjson.toml` out like the
  template — your values in their places, the rest documented and
  commented, accounts and `[[...]]` entries in your order — keeping unknown
  keys (flagged) and your comments; it writes only when the parsed
  configuration is unchanged. A dry run shows the diff; `--write` applies
  it with a `taxjson.toml.bak` backup (`--no-backup` to skip); `--check`
  exits 1 when the file is not formatted.

### Privacy

- **Private figure list.** `scripts/check-pii.sh --collect-amounts
  PROJECT_DIR...` records the distinctive money figures of the
  maintainer's own project outputs as salted SHA-256 hashes (mode 0600,
  no plain figures; default `~/.config/taxjson/pii-amounts`, override
  `TAXJSON_PII_AMOUNTS`). Every check-pii mode — tree scan, pre-push diff
  and per-commit scan, commit and tag messages, ref names — then refuses a
  line holding one of them, naming the file and line only; there is no
  `pii-ok` escape. A missing list is skipped (contributors have none).
- **The private figure list also covers the raw exports.** `--collect-amounts`
  now reads every text file under each project's `inputs/` as well and
  lists its distinctive values: amounts with cents from six digits up,
  numbers with three or more decimals (prices, rates, coin quantities),
  broker reference codes and clock times next to their date. Short,
  round and public values (month-name dates, ISINs, CUSIPs, times on the
  minute) are not listed. Binary files there are named, not read.
  Comments, examples and tests that matched such values now use synthetic
  ones.
- **No figures from real books in the repository.** CONTRIBUTING states
  the rule: code, tests, docs, the CHANGELOG and commit messages use
  synthetic inputs only, and CHANGELOG entries describe changes in words.
  Test fixtures, README sample outputs and older CHANGELOG entries that
  carried such figures were reworded or given fresh synthetic numbers;
  every test pins the same behaviour.
- **No trades from real books in the repository.** Test fixtures,
  examples, source comments and documentation no longer mirror real
  trades — under their own or disguised tickers: no real trade dates,
  symbol and quantity pairs, option contracts or corporate-action cases.
  Each was replaced by a fictional case with its own dates, quantities
  and contracts, and the expected results were recomputed so every test
  pins the same behaviour; comments describe the shape in words.


### Crypto

- **Removed the built-in crypto id table.** taxjson no longer carries
  Yahoo ids for any coin: a coin is priced as `<SYMBOL>-USD` unless the
  project's ticker.map maps it with a `CRYPTO SYMBOL YAHOO_ID` line (the
  full pair as Yahoo shows it, ending in `-USD`, is accepted too). A
  project that relied on the removed table needs the line: when a
  coin's default id fails, the warning names the coin and the line to
  add (Yahoo numbers a shared ticker; find the id on finance.yahoo.com),
  and `taxjson run` prints an ATTENTION line, with the exact line, when
  a coin is priced under its default id while the price cache holds
  prices of a numbered id of the same ticker, or when its Yahoo close is
  far off the coin's own trade prices near the date. `taxjson init`'s
  ticker.map example uses a placeholder coin.

## v0.17.0 (2026-10-04)

### Security (pre-release review)

- **Install lines never name a PyPI package.** taxjson and taxjson-fetch
  are not published on PyPI yet, so a package under either name there is
  not ours. Every hint — `taxjson fetch` with no plugin, the `[fx]`,
  `[ibkr]` and `[xlsx]` extras, README, the plugin's README — now names
  the installer (re-run with `--with-fetch`, or `TAXJSON_EXTRAS=...`) or
  an editable install from a checkout, and says the PyPI names are not
  ours.
- **No write through a planted symlink.** Every write-then-rename
  (`work/*.part` stage outputs, `.sum` reports, `migrate`'s
  `.migrate.part`, `write_text_atomic`, the gains traces, report.json,
  watch / checklist state, form-export CSV/TXF, close-year locks, `.bak`
  copies) creates its temporary file fresh (an existing entry is removed;
  the create refuses a symlink; owner-only, fsync'd) and renames it into
  place, so a symlink at the temporary or the final name is replaced, never
  written through. `taxjson migrate` refuses a `ticker.map` /
  `taxjson.toml` symlinked outside the project, before writing anything.
- **pre-push scans every pushed commit**, not only the net diff: a value
  added in one commit and removed in the next is refused too. The diff
  scan also refuses a money amount (thousands separators and cents) added
  to a CHANGELOG or markdown doc line or a code comment; `pii-ok` on the
  line lets a synthetic one through.
- **`TAXJSON_OFFLINE` stops `taxjson fetch`** with one line before any
  fetcher runs (`--list` still works).
- taxjson-fetch: the Questrade `account` from taxjson.toml must be digits
  only (checked before the refresh token is spent) and is percent-quoted
  into the API path; a login response whose access or refresh token holds
  whitespace or control characters is one clean error line that never
  shows the token (was a traceback printing the Authorization header).
- fill-crypto quotes the coin's Yahoo id into the request URL and closes
  the response.
- The `[fx]` extra's comment in pyproject.toml dates the Yahoo fallback
  as the code does (before 2007-05-01).

### Release channels: tag → latest, promote → beta / stable (owner request)

- **New installs take `stable`**, a release named in `channels.json` on
  `main` (`stable` and `beta`); `latest` is always the newest `vX.Y.Z`
  tag. `scripts/release.sh` makes a release `latest` and nothing more;
  `scripts/promote.sh vX.Y.Z [stable|beta]` moves a channel (tag must
  exist, from `main`, clean `channels.json`, asks before moving a channel
  backwards; commits "Promote vX.Y.Z to stable" and pushes; trailers only
  from `TAXJSON_PROMOTE_TRAILERS`). docs/releasing.md has the rhythm.
- **Installer:** `--channel stable|beta|latest|dev|vX.Y.Z` (or
  `TAXJSON_CHANNEL`; the old `release` means `latest`), printed as
  `channel stable → release vX.Y.Z` and remembered in
  `~/.config/taxjson/channel`, so re-running upgrades along the same
  channel. A channel never moves an install backwards (a version does).
  Only an annotated release tag on `main`'s history is installed.
  `TAXJSON_DRY_RUN=1` shows the pick and changes nothing. A `taxjson` or
  `tjs` in the bin directory that is not the installer's own link (a
  file, or a link to another program) is left alone with a note — a
  foreign link used to be replaced silently, a file stopped the install.
  The `[fx]` fallback warning now names the right date (2007-05-01).
- **`taxjson channels [all] [--json] [--offline]`** (and
  `scripts/channels.sh`): where stable / beta / latest point, what this
  machine's production copy runs and on which channel, and the newest 20
  releases with ←stable / beta / latest / this-box marks; offline it says
  so. On a development machine, **`taxjson promote [vX.Y.Z]
  [stable|beta]`** and **`taxjson deploy [vX.Y.Z]`** (the production copy
  → the newest or the named release, through the installer); elsewhere
  they refuse with a pointer to the installer. Help group "Release".
- `scripts/check-consistency.sh` checks that `channels.json` parses and
  names existing tags.

### One mapping file, year data in taxjson.toml (owner request)

- `ticker.map` is the project's one mapping file. Three lookup keywords
  join the rename rules: `QUOTE SYMBOL YAHOO_SYMBOL [RATIO]` (was
  `yf_ticker.map`; price lookups in `harvest` and the price chain),
  `CRYPTO SYMBOL YAHOO_ID` (was `crypto_ticker.map`;
  fill-crypto, crypto-sends and harvest) and `EXTRACT description words |
  CURRENCY | SYMBOL` (was `ticker_extraction_overrides.txt`; the parsers'
  symbol-extraction overrides — `taxjson-brokerage --security-overrides`
  now takes a ticker.map and reads its EXTRACT lines). A malformed
  lookup line stops `taxjson run` like any other ticker.map line.
- Hand-entered year data lives in `taxjson.toml`, checked by every
  command (types, dates, duplicates, the account an entry names, the
  country): `[estimate] amt_carryover` (the `amt_carryover.txt` reader is
  gone), `[carryover] claimed = { YEAR = AMOUNT }` (was
  `claimed_losses.txt`; `taxjson carryover` passes it to
  `taxjson-carryover --claimed-year` and has no `--claimed` option any
  more), `[[capital_gains_dividends]]` (Canada only; was
  `capital_gains_dividends.map`) and `[[distributions]]` (was
  `distributions.map`; `taxjson-apply-distributions --config
  taxjson.toml` replaces `--map`).
- New `taxjson migrate [--dry-run]` converts an older project: the old
  files' lines are appended to ticker.map / taxjson.toml with the old
  readers' meaning (the user's content and comments are never
  rewritten), each old file is renamed `<name>.migrated`, and it refuses,
  writing nothing, on an unreadable line or a conflicting entry. While
  one of the old files is in a project every other command stops
  (exit 2) naming it — no silent fallback.
- The `run --fast` and checklist input fingerprints track ticker.map
  (and taxjson.toml, as before) instead of the old files.

### Minimum tax detail and carry-forwards (owner request)

- New `taxjson amt [YEAR]` (Canada): the year's minimum tax line by line —
  regular tax, adjusted taxable income item by item (ITA s.127.52), basic
  exemption, rate, credits allowed, whether it binds, the provincial AMT,
  the carryover created, the carryovers available by year of origin with
  their 7-year limit (s.120.2), what is recovered this year and what
  carries forward. Every figure is the estimate's own. A US project
  refuses it (Form 6251 is not modelled). A `checklist` step.
- The estimate now applies a prior-year minimum tax carryover: from
  `[estimate] amt_carryover = { YEAR = AMOUNT }` (by year of origin),
  else from the latest
  close-year lock before the project year — oldest first, up to regular
  federal tax minus federal minimum tax, the province's share at its
  minimum-tax factor; expired years drop off with a note. Instalments use
  the full recovery; the incremental estimate counts the change the
  investment income causes. It no longer says "no prior-year minimum tax
  carryover".
- `taxjson close-year` records the year's carry-forwards in
  `filed/<year>.json` from the same estimate (Canada: net capital loss
  carried in / created / applied / carried out, and the minimum tax
  carryover by year of origin; US: the short-/long-term capital loss
  carryover). The next year's `estimate` uses them when you enter none
  (and says where each figure came from), `carryover` starts from the
  recorded balance, and `handoff` flags an input that differs from it.

### T1135 overrides move into ticker.map (owner request)

- `t1135.map` is folded into ticker.map: a symbol's T1135 domicile is a
  `T1135 SYMBOL COUNTRY` line (COUNTRY: an ISO 3166 alpha-3 code, or
  CA/CAN/CANADA/EXCLUDE for "not foreign property" — the old file's
  vocabulary). `taxjson t1135` reads the project's ticker.map and
  `taxjson-t1135 --map` takes a ticker.map. An override still follows
  its symbol through a ticker change and is named when it matches
  nothing. A T1135 line that cannot be read (a country outside the
  vocabulary, a wrong shape, one symbol given two countries) now stops
  `taxjson run` and `taxjson t1135` with a did-you-mean hint, like any
  malformed ticker.map line — the old file skipped it with a warning and
  the symbol kept its listing country. A `t1135.map` left in a project
  stops every command until `taxjson migrate` converts it (with the old
  file's rules: a line it ignored becomes a comment) and renames it
  `t1135.map.migrated`.
- `sector.map` is gone from the docs: nothing has read it since the
  timeline view went with the desktop app.

### Removed

- The remaining watchlist exports are gone: `taxjson-export
  --seekingalpha` and `--fastgraph` (asking for either is now a clear
  error naming the removal) and `taxjson run`'s exports stage with them
  (its thirteen `reports/exports/*_SA.csv` / `*_FG.csv` files and the
  "==> exports" step). A full run removes the `*_SA.csv`, `*_FG.csv` and
  `*_TV.txt` files an earlier run left in `reports/exports/`, and the
  folder once it is empty, in one line; a file of your own there is
  kept. `taxjson-export` now needs `--report` or `--holdings-toml` (with
  neither it used to print a bare ticker list). The holdings TOMLs,
  `--report`, `--holdings-toml` and ticker.map `QUOTE` lines are
  unchanged.
- The TradingView watchlist export is gone: `taxjson-export
  --tradingview` and `--tv-map` (asking for either is now a clear error
  naming the removal) and the four `reports/exports/*_TV.txt` files of
  `taxjson run`'s exports stage; each run removes a `*_TV.txt` an earlier
  run left there. A `TRADINGVIEW` line in ticker.map is ignored, with one
  NOTE per run asking you to delete it (never an error). A leftover
  `tv_exchange.map` stops nothing (it is ignored, with a NOTE);
  `taxjson migrate` renames it to `tv_exchange.map.migrated` without
  converting it.
- The local web UI is gone: `taxjson serve` (and its `--host`, `--port`
  and `--token` flags), the `src/taxjson/web` package and the `[web]`
  extra (FastAPI, Uvicorn, Jinja2, python-multipart; the `[dev]` extra no
  longer pulls httpx). The reports, the wash-radar JSON sidecar (read by
  `harvest --radar`) and every other command are unchanged; the
  what-if's tax-logic statements CA-PLAN-03 / US-PLAN-03 are retired
  with it. The installer's default extras are now `fx`.

### Changed: broker fetch is a separate package

- `taxjson fetch` moved out of the core into the optional
  **taxjson-fetch** distribution (`packages/taxjson-fetch`: the
  Questrade REST API and IBKR Flex Web Service clients, the
  `~/.questrade_token` handling, the window / merge / `--trim-overlap`
  logic, `--positions`, and their tests). The core holds no broker
  client and reads no broker credential; its network egress is FX rates
  and crypto prices only (SECURITY.md).
- The core keeps `taxjson fetch` as a thin dispatcher over fetchers
  registered under the entry-point group `taxjson.fetchers`: `--list`
  names the installed ones, each account's `brokerage` picks its
  fetcher (`--fetcher NAME` narrows it), and `--json` / `--dry-run`
  work as before. With none installed it prints one line — re-run the
  installer with `--with-fetch` (or `pip install -e
  packages/taxjson-fetch` from a checkout) — and exits 2. With the
  plugin installed, every option and output is unchanged.
- `brokerage` / `account` / `query_id` under `[accounts.<name>]` stay
  valid without the plugin (`taxjson run` adds a one-line note); with
  fetchers installed, the brokerage check names the brokerages they
  serve.
- The installer installs the core only; `--with-fetch` (or
  `TAXJSON_WITH_FETCH=1`) adds taxjson-fetch, and an install that has
  it keeps it on upgrade. `scripts/dev-setup.sh` installs it;
  `scripts/ci.sh` runs its tests and lints `packages/`;
  `scripts/release.sh` bumps both versions in lockstep. README and
  CONTRIBUTING document the plugin interface for other brokers.

### Command line (owner requests)

- The slide deck (`docs/deck/`) describes taxjson as it is now: `tjs`,
  the grouped commands, tax-logic as the spec, CRA's per-sale
  superficial-loss formula, income dating, warrant exercises, dated
  renames, minimum tax and carry-forwards, the US engine's per-lot
  reorganisations and own-account moves, the fetch plugin, no web UI,
  and the two full audits. README's Verification section names the
  audits, the mutation testing and the tax-logic gate; the test count
  is 6,500+.
- `taxjson` with no command (or only `-C DIR`) prints the help page and
  exits 0; it was a usage error (exit 2).
- The help page groups the commands by what they are for (Set up, Build
  the books, Summaries, Positions, Row listings, Totals by type, Before
  you trade, Before you file, Explain and check, Tools; alphabetical within
  the four reading groups) instead of one flat list; the README's command table
  uses the same groups. Inside a project the page leaves out the other
  country's commands and says how many it hid; `taxjson help --all` lists
  every command, marking the one-country ones (Canada) / (USA), as the
  page does outside a project.
- The help page lists each command on one short line (what it does, in
  plain words); `taxjson COMMAND -h` carries the full description. Stale
  wording is gone (`run --no-input` named a GUI, `sanity` a
  private tool's file format, `--fast` an mtime cache), and the standalone
  tools that printed no description or a vague one (taxjson-fill-crypto,
  taxjson-convert-currency, taxjson-wash-radar, taxjson-safe-to-sell,
  taxjson-ticker-map, taxjson-validate, taxjson-split-gains and others)
  now say what they do.
- `tjs` is a short name for `taxjson` (the same program; usage lines show
  the name used). The installer links it beside `taxjson`.
- New `taxjson stats [YEAR] [ACCOUNT] [--all-history] [--json]`: win/lose
  statistics on closed trades per asset class (long and short shares,
  long and written options, futures, crypto) plus a total — trades,
  wins, losses, win rate, net P/L, average and largest win and loss,
  profit factor. Economic P/L in the base currency before any
  superficial-loss / wash-sale denial (the denied total on its own line),
  taxable accounts unless a sheltered one is named. A written option is
  one trade from write to close under either premium timing; an assigned
  one's premium counts on the option and is taken back out of the shares
  (tax-logic CA-RPT-16 / US-RPT-12).

### US engine (owner request: deferred re-audit work)

- US wash sale: a purchase in another of your taxable accounts inside
  the 61-day window now replaces a loss even when that account sold the
  shares before the loss sale (§1091 has no still-held test; re-audit
  A2-0544). The disallowed loss is added to the basis of that earlier
  sale (its gain falls; the loss shares' holding period carries over).
  When that sale is in a filed year (`filed/<year>.json`), the filed year
  is left as filed: the amount is booked as a loss on the loss sale's
  date and an ATTENTION line names the earlier sale, whose return may
  need an amendment (tax-logic US-WASH-22).
- US §355 spin-off (`tax_free_355`): every parent lot now gives up the
  same fraction of its own basis (Reg. §1.358-2), and the spun-off shares
  are one block per parent block with that block's purchase date and
  holding period (§1223(1)). They were spread by share count over the
  parent's lots, all dated on the spin date, and a low-basis lot could
  book a §301(c)(3) "deemed gain" on a tax-free spin-off (re-audit
  A2-0065). An allocation beyond the parent's basis is capped at it with
  an ATTENTION line; the spun-off shares are not a wash-sale replacement
  (§1091(a): not acquired by purchase).
- US `reorg_368_boot` (§356): the recognized gain is now computed per lot
  of old shares (Reg. §1.356-1(b)): each lot's realized gain is its share
  of the new shares' value and the cash less its own basis, recognized up
  to its share of the cash, never a loss; its new shares get basis − cash
  + gain and keep its purchase date (§1223(1)). The whole pool was one
  engineered sale before: with lots of different basis one lot booked a
  gain and another a LOSS, and the holding period restarted (re-audit
  A2-0066). The engine uses your own lots, so `source_basis_total` is no
  longer asked (an older manifest's value is ignored).
- US: a move of shares or coins between two of your own taxable accounts
  now carries the lots (re-audit A2-0032 securities, A2-0003 crypto).
  `taxjson run` pairs the move's out and in rows (securities from the
  transfer evidence: one symbol within 10 days, the same quantity or two
  deliveries adding up to it; coins from the crypto-sends pairing), adds
  them to both accounts' books as `work/<account>_own_moves.json`, and the
  US engine hands the sending account's FIFO lots — basis and purchase
  dates — to the receiving one with no sale. Before, the receiver's sale
  read as a short with no basis and the sender kept a phantom long (since
  the first re-audit fix: an ATTENTION line and a `--strict` stop, now
  removed). US crypto accounts with such a move run one blended crypto
  pass (FIFO per account, no wash-sale rule); `check-filed` recomputes
  the same way. Rows that look like a move but do not pair, and a move larger
  than the sender's lots, are ATTENTION (`--strict` stops). Canada pools
  the ACB across the accounts (s.47) and is unchanged (tax-logic
  US-BASIS-05, US-CRYPTO-05).

### Second-audit deferred items

- A blank settlement cell now settles on the listing's market in every
  parser: a Questrade or RBC US-dollar TSX unit (SAMPLF.U.TO) on the Canadian
  calendar, a Questrade CAD-settled US stock on the US one, and a generic
  `.L`/`.AX` line priced in USD on the UK/ASX cycle (A2-1052, A2-1054).
- The Questrade, RBC and Webull "looks renamed" hints, RBC's untraded-income
  listing hint and IB's one-contract-two-symbols ATTENTION are dropped once
  ticker.map joins the pair: `taxjson run` now passes the map to
  `taxjson-brokerage --ticker-map` (a map edit re-parses); `--lint` keeps
  them (A2-1056).
- IB: a stock dividend or cash takeover that a later statement's `Ca`
  row cancels no longer leaves its ATTENTION / NOTE line (or the Ca row's
  "original is not in this statement" skip line) in the run output: under
  `taxjson-brokerage` those lines print after the cross-statement pass
  (A2-1091).
- Canada: the stablecoin de-peg warning now also checks fills valued in
  CAD, EUR or another fiat (Coinbase rows priced in CAD, Kraken `USDC/CAD`
  or `USDT/EUR` pairs, ledger stablecoin-to-fiat conversions): `taxjson run`
  passes its rates file to `taxjson-brokerage --rates`, and the fill is
  turned into US dollars at the day's rate before the 2% test; a fill with
  no rate is named as unchecked (A2-0590).
- fx-cash now counts the cash a corporate action pays wherever the emitter
  puts it: cash in lieu folded into a taxable exchange's proceeds, an
  all-fractional merger, a spin-off's fractional share and §356 boot (the
  rows carry it as `corp_cash`; the description wording no longer decides)
  (A2-1014).

### Renames, ticker.map and warrants

- `taxjson-ticker-map` summary mode no longer "maps" every `.US`
  listing to `.TO` (a hard-coded CAD target, wrong beside a US project
  and not a real listing in a Canadian one); it lists the symbols with
  only their option-string normalisation (re-audit A2-1367).
- Renames are dated events (owner decision, audit A2-0197). On its date
  a ticker change carries the position, the ACB / basis lots and the
  acquisition dates from the old symbol to the new one, and the
  superficial-loss / wash-sale rule treats the old symbol before the
  date and the new one after it as one security — in both engines, the
  wash radar and buy/sell-check. A trade in the old ticker after the
  rename date is now a separate security (it used to be pooled with
  the renamed holding for the window): `taxjson run` prints an
  ATTENTION line, `run --strict` stops, and ticker.map declares which it
  is — `RENAME OLD NEW YYYY-MM-DD late=fold` (the broker still books the
  renamed shares under the old ticker: booked as NEW) or `late=separate`
  (another company reuses the ticker). A dated `RENAME OLD NEW
  YYYY-MM-DD` line also books the rename itself when no broker row does;
  `RENAME OLD NEW` without a date means `GLOBAL OLD NEW`, as before.
  New `taxjson renames [ACCOUNT] [--json]`: every rename with its date,
  source, the position and book cost it carried, and the late trades;
  in the checklist (`renames`) and `taxjson edge-cases` (CA-ACB-RENAME /
  US-BASIS-RENAME).
- Exercising a warrant or right is no longer a disposal at 0 (owner
  decision, audit A2-0090 / A2-0274): the warrant's cost and the
  exercise price become the shares' cost (ITA s.49(3); US basis
  carryover, holding period from the exercise). IB pairs a Warrants leg
  coded `Ex` with the same-day share leg coded `Ex`; RBC pairs an
  `Exercise` of the warrants with the same-day `Exercise` of the shares
  and refuses an exercise with no share leg (CA-OPT-09 / US-OPT-06).
- A `.tt` SPLIT or rename of a USD stock no longer stops `taxjson run`
  (exit 1, "Currency mismatch") in the native-holdings pass, in either
  country: a SPLIT carries no money, so its currency stamp is no longer
  checked against the pool's (re-audit A2-0010, regression of R1-126).
- **A rename to a bare symbol is an ATTENTION line.** A ticker.map
  rule (`GLOBAL RY.TO RY`) or a `ticker_extraction_overrides.txt` line
  that turns a listed symbol into a bare one used to be accepted in
  silence; the bare symbol is read as crypto or an unknown listing, so
  a Canadian eligible dividend became a foreign one with an assumed
  foreign tax credit. `taxjson run` now prints it on the console
  (audit A2-0304).
- IB: a warrant exercise leg (booked as a disposal at 0 — the warrant's
  cost becomes a loss instead of part of the shares' cost) is an
  ATTENTION line; the fix is deferred (KNOWN_ISSUES, audit A2-0090).
- A ticker that trades again after a rename moved it to a new symbol is
  flagged (`warning: ATTENTION: ... after its rename ...`) in both
  countries: the superficial-loss / wash-sale rule treats it as the
  renamed security, which is wrong if another company now uses the
  ticker. The class is unchanged — the export cannot tell the two cases
  apart (A2-0197; CA-ACB-04 / US-BASIS-06).
- **ticker.map tools.** The DELETE audit note prints the signed net cash
  (a buy and a sale no longer add up) and names the `DELETE` line;
  `taxjson-ticker-map` refuses a positional map plus `--map`; `merge2
  --map` help describes the keyword format.
- **ticker.map means one thing everywhere.** Rule symbols are
  upper-cased on load (a lower-case rule used to rename nothing while
  `taxjson scan` called it live), a BOM and inline `# notes` are
  stripped, and renames chain to their end (`GLOBAL OLD.US NEW.US` plus
  `TOBASE NEW.US NEW.TO` now sends OLD.US to NEW.TO instead of splitting
  the pool into a phantom short). `taxjson run` refuses a map with a
  rename cycle, one symbol renamed to two different targets, or a
  `DISTINCT` pair that the renames pool together. Scan's MAP-UNUSED
  note follows chains and judges a rule the way the engine applies it:
  a suffix-less `GLOBAL QQOL QQNW` that matches only `QQOL.US` is
  reported with a hint to write the suffixed form. A malformed
  `ticker_extraction_overrides.txt` line now stops the run by
  file:line instead of being skipped.
- **A malformed `ticker.map` line stops the run.** A typo such as
  `TOBASE SAMPLE.US=SAMPLE.TO` or `TOBSE ...` dropped that rule, which changed
  ACB pools and the Schedule 3 gain, and the warning reached only
  `reports/*.sum` while `run` and `run --strict` exited 0. `taxjson run`
  now refuses the map, listing each bad line as `ticker.map:<line>`.
- **TOBASE no longer pools a US option into a Montreal contract.** When
  a share rule's root rename would move a US-listed option onto a
  contract code the account also trades on the Montreal Exchange, the
  US contract keeps its own symbol (different strike currency and
  clearing house: not identical property) and the `.sum` DIAGNOSTICS
  name it; before, the two ACBs were pooled and the gain changed
  silently.

### Tax rules and engine: Canada

- The superficial-loss solver no longer oscillates on a sale split into
  fills when a later fill is priced just above the ACB and the
  replacement was bought before the sale: the bump moved before and
  after that fill on alternate passes (a loss, then a gain), so 1000
  passes ended unconverged with a summary that disagreed with the
  records. The bump's place after the sale's last losing fill now only
  moves later and an existing bump follows it (re-audit A2-1596).
- A Canadian superficial loss is now sized with CRA's formula for each
  sale on its own (owner decision, audit A2-0167): the denied units are
  the least of the units sold, the units acquired in the window and the
  units held at day 30, so a replacement still held backs the denial of
  every sale in its window — in the same account, across the taxable
  accounts' one pool, in a registered or affiliated account and for a
  call at its contract size. A held unit used to back one denial only;
  books with two loss sales around one rebuy can show more denied (a
  taxable deferral comes back when the replacement is sold). The fills
  of one sale (one account's same-day sales with no buy between them)
  share the denial pro rata — whatever their order or prices — and a
  replacement bought before the sale takes the ACB increase after the
  sale's last fill, so the result no longer depends on how many
  seconds apart the fills are (R1-31). The wash radar, sell-check and
  safe-to-sell follow: a replacement that backs an earlier loss puts a
  sale today at risk too. The US engine (each replacement share matched
  once) is unchanged (CA-SL-08, CA-PLAN-01).
- A warrant/right, adjusted-series or futures-option flag in a loss's
  window (warn-only: nothing is denied) now keeps the checklist's
  `wash-reviewed` step open and is listed by `taxjson wash-sales`
  (text and `--json` `manual_check_flags`); both said "no superficial
  losses / no losses were denied" over it (CA-SL-15 / US-WASH-15,
  audit A2-0413). A Canada project's `wash-sales` says "No superficial
  losses", a US one "No wash sales" (A2-1366).
- Settlement cycles per market: the UK, EU and Swiss markets were T+2
  from 2014-10-06, the ASX and NZX from 2016-03-07, Singapore from
  2018-12-10, Tokyo from 2019-07-16, Hong Kong throughout — they all
  inherited North America's T+3 era before 2017-09-05 (and Tokyo got
  T+2 two years early); every EU currency, not only the euro, moves to
  T+1 on 2027-10-11. tax-logic CA-DATE-04 / US-DATE-04 state it, with
  Mexico's 2024 T+1 move (re-audit A2-0704, A2-1195, A2-0705). No
  2024-2026 trade changes.
- A long option expiring ON Dec 31 with no expiry row is warned about in
  that year's project; ccd.rpt / leaps / ccd-sum / the .sum name a
  covered call's held class share (SAMPLD.B.TO) even when the shares were
  not sold; `taxjson transfers` refuses to run without taxjson.toml and
  says when an account's base book is missing (re-audit A2-0716,
  A2-0715, A2-0717, A2-1232).
- The superficial-loss trace, the `wash-sales` footer and the `sum`
  filing note no longer call an affiliated person's denial "lost for
  good": that person adds it to their own ACB (re-audit A2-1225,
  A2-1233).
- A position that goes short where no short can exist — a registered
  account (TFSA/RRSP/IRA), a crypto account's spot coins, or a sale the
  broker codes CLOSING with nothing held — is now an `ATTENTION: short:`
  line on the console, and `run --strict` refuses it (missing history; a
  registered short hid a superficial-loss denial). taxjson-gains takes
  `--spot-crypto`, which `taxjson run` passes for crypto accounts
  (re-audit A2-0395, A2-0137, A2-1223).
- Grant timing: an expired written option counts as a close in ccd-sum,
  winners and the .sum TRADES line, as under close timing (re-audit
  A2-0693).
- **Futures at a negative price, and futures schema checks.** A
  plain-futures buy at a negative price (WTI, April 2020) received cash;
  its negative net is now accepted by the schema and booked as a
  negative cost, where the magnitude the schema forced booked the loss
  as a gain (tax-logic CA-FX-04 / US-FUT-01). The negative-price
  exemption covers every futures prefix (`/` and `\` as well as `F:`)
  in the schema and `taxjson-validate`; a futures row with no declared
  contract size no longer gets a guessed-size ATTENTION (every
  generic-importer futures row did); and an option expiry row dated
  after its expiry day is a schema warning (audit A2-0302, A2-1082,
  A2-1087, A2-1088, A2-1089).
- **Long calls as replacements: class-share roots, mini contracts and
  futures options.** A call booked under the root that drops the share
  class (SAMPLD for SAMPLD.B.TO, SAMPLCB for SAMPLC.B) is now a call on that class
  line: Canada denies the loss and the US warns (A2-0015/0016/0207). A
  call's replacement units are its declared contract size (a `x10` mini
  replaces 10 shares, not 100) in both engines (A2-0049/0957). A futures
  option on the loss's own futures contract is flagged for a manual
  check in both countries instead of being enforced as a 100-unit call
  in Canada (A2-0014/0056). tax-logic CA-SL-05/15, US-WASH-12/15.
- Canada: a coin rebuy under a millionth of a unit now backs its share
  of a superficial loss; the solver's zero is the pool's own (relative
  for a coin) instead of a fixed 1e-6 (A2-0552; cents at most).
- **Canada superficial loss: one held unit backs one denial, and
  same-moment rows follow the export order.** A registered account's
  units claimed by an earlier loss, or a call contract claimed through a
  row sold since, could back a second denial (A2-0012/0057/0198). Same-
  moment losses, rebuys and triggers were ordered by the rows' content
  hash or account label, so a one-cent price change could move a denial
  or turn a deferral permanent; they now follow the main pass's order
  (export row order, accounts in taxjson.toml order — CA-DATE-14): a
  rebuy listed after a same-moment loss sale is a purchase after it
  (A2-0058/0193/0059/0551/0192/0961/0965). A deferred loss's bump now
  reaches a same-moment sale listed after the replacement purchase
  (A2-0555). tax-logic CA-SL-08/09/10 state it.
- **An option assignment's premium goes to its own stock leg (both
  countries).** The premium ledger used to pair an assignment with the
  first marked stock leg within 7 days, or with whichever option was
  staged first: a plain (IB/RBC) assignment next to a Webull marked leg
  took the other assignment's premium and the other premium was lost;
  a stock leg dated a day before its option row dropped the premium
  from every year; same-moment assignments swapped premiums by row
  order; and a x10 mini option was sized at 100 shares, so one of two
  mini assignments was never folded. Each assignment is now paired by
  identity (same account and underlying, the delivered quantity at the
  declared contract size, the strike as the leg's price, a leg dated up
  to 3 days before or 7 days after the option row), and the
  "unconsumed" warning names that window instead of saying the leg
  never arrived. tax-logic CA-OPT-08 / US-OPT-05 (audit A2-0050,
  A2-0051, A2-0052, A2-0195, A2-0196, A2-0203).
- **Canada: rows that settle on the same day go in trade order.** Over a
  settlement holiday a Friday trade and the next trading day's trade
  settle together; the engine took them by clock time, so a Monday
  09:45 buy was applied before the previous Friday's 15:00 sale (wrong
  ACB, a false superficial loss). They now go in trade-date order
  (tax-logic CA-DATE-14) (A2-0067).
- **A phantoms.json entry on a real short or a written option is
  flagged.** An entry for a short the broker marks as a short sale
  (RBC `SHORT.`, IB code `O`) or for an option the broker never coded
  closing is still applied, but every applier (run, t1135, wash-radar,
  option-boundary, apply-distributions) prints an ATTENTION line, the
  run echoes it, and `find-missing-history` and the checklist ask to
  remove it (audit A2-0308, A2-0310, A2-0311, A2-0637, A2-0638,
  A2-0639).
- **A futures option keeps its contract size.** The size IB's instrument
  list declares (CL 1000, ES 50, micro 0.1) is kept on option and futures
  rows and on the year-end inventory: the holdings export writes it as
  `contract_multiplier` and divides COST/SHARE by it, `taxjson list` too,
  the web what-if prices a futures option with it (still refused when no
  row declares one), and a `.tt` line may end in `x1000` so its total is
  checked at the real size (audit S026-22).
- **Adjusted-series and futures calls are flagged as possible
  replacement property.** A call on an adjusted option series (root +
  digit, e.g. `SAMPLE1`) or on the loss's futures contract by its family
  root (`F:SAMPLX` after a loss on `F:SAMPLXG6`) bought inside a loss's window
  is named for a manual superficial-loss / wash-sale check, the way a
  warrant is, in both countries; warn-only, the numbers do not change
  (audit S069-23; tax-logic CA-SL-15, US-WASH-15).
- **Two byte-identical rows each keep their own superficial-loss /
  wash-sale result** (hand-made JSON passed to `taxjson-gains`): they
  shared one id, so only one took its denial or basis bump. The engine
  now books them as separate trades and says so in a NOTE.
- **Canada, grant timing: a write whose commission exceeds its premium**
  follows the same rule as a buy-back loss: exempt from the
  superficial-loss rule unless `option_buyback_loss_superficial = true`,
  and when it is denied the grant record now carries the denial (the
  summary and wash-sales used to show a denial the record did not, with
  an "invariant broken" warning). A phantom (tainted) pool's write loss
  never feeds the rule. tax-logic CA-SL-11/12.
- **Rows at the same moment keep the export's row order (Canada).**
  Webull prints no clock time, so every row is stamped 09:30:00, and the
  Canada engine put a buy before a sell at one moment: a write listed
  before its same-day buy-back became a long round trip, whose loss a
  re-buy within 30 days could deny. Trades at one moment now follow the
  export's row order in both countries (the US engine already did); the
  fixed places (opening balance, split, assignment legs, adjustments)
  stay. The wash radar and `t1135` follow (a same-moment sale listed
  before the purchase is a short covered at once, not property held
  for the T1135 cost test); the missing-history walks
  still read a same-moment pair buys first (they only look for missing
  history). Questrade and generic-importer files listed newest first are
  read bottom-up, as RBC's always were. tax-logic CA-DATE-14 /
  US-DATE-13 (audit R1-30).
- **Missing-history and phantom tools agree with the engine.**
  `find-missing-history`, `--gen-phantoms`/`--suggest-phantoms` and the
  phantom openings now: order same-moment rows buys first and put a trade
  executed before an evening split (settling after it) ahead of the
  split, as the engine does (no invented short, no order-dependent
  opening size); count the in-year BUY that covers a short carried in
  as affecting the year (the report said "safe to ignore" while the
  year booked the cover); date a row by the project's `tax_date` (a
  Dec-31 trade settling in January belongs to January's year); follow
  renames (a clean sale after a rename is not flagged, a $0-cost
  position renamed before its sale is); and take "registered" from the
  account's configured type, not its name. `phantoms.json` symbols are
  matched case-insensitively and a listed pair that matches no row is
  named as such; both ends of a rename chain listed give one opening
  whatever the hash seed.
- **Phantom-basis superficial-loss warnings are shown.** A clean loss
  within 30 days of a phantom-basis sale, and a phantom-basis loss with
  a rebuy in its window, were written only into the gains JSON (and
  dropped by the per-account split); they now print as `warning:` lines
  (so they reach the DIAGNOSTICS banner) and stay in each account's
  `_gains_wash.json`. US engine: an IRA-to-IRA move the tool nets out no
  longer triggers "sells beyond its recorded balance".
- **Superficial-loss rule fixes.** A cover that also opens a long (buy
  150 while short 100) now counts its own 50 new shares as substituted
  property, as two separate rows already did. In a blended pass the
  cover-vs-acquisition test uses the pooled s.47 balance, so account B's
  rebuy after selling shares only account A held is an acquisition. A
  contract that expires before day 30 is not "still held", even with
  no expiry row. `option_buyback_loss_superficial = false` (the
  default) now exempts a written option's buy-back loss under close
  timing and for contracts written before `option_grant_timing_since`
  too, not only grant-timed lots. A warrant or right bought in the
  window (`SLH.WT.TO`, `ABC.RT.TO`) is named in an option-replacement
  warning for review. US engine: a registered or spouse account's
  buy-to-close is no longer a §1091 replacement.
- **Same-moment rows no longer depend on a content hash or a 1-second
  gap.** The superficial-loss balance walk (Canada) and the US
  replacement-lot order broke ties by each row's hash, so a one-cent
  price or description change could flip a denial or move a US wash
  deferral to the other lot; ties now keep the main pass's order (its
  buy-before-sell rung, then the export's row order). A denied loss's
  cost bump for a pre-loss rebuy now applies right after the loss row,
  so another fill of the same order sees it whether it came 0, 1 or 2
  seconds later. A bump for a rebuy booked under the old ticker on a
  rename's own date lands on the pool that holds those shares instead
  of vanishing into the empty new-ticker pool.
- **Each option assignment's premium goes to its own stock leg.** Both
  engines staged every assignment's premium per (account, underlying)
  and handed the whole sum to the first stock trade that came along: a
  spread assigned on one day put the put's premium and the call's into
  one leg, two stock legs of one assignment did not share it, and a
  missing stock leg let an unrelated trade months later absorb it. The
  premium is now matched to the leg in the assignment's direction
  (buy for an assigned put / exercised call, sell otherwise), per share,
  within 7 days of the option leg; a premium no leg claims is named in
  the end-of-run "unconsumed" warning instead of moving into another
  trade (and another year).
- **Option roots that differ from the stock ticker.** An assignment of
  a Montreal `SAMPLD` option into `SAMPLD.B.TO`, an OCC `SAMPLCB` option into
  `SAMPLC.B.US`, or a futures option into its dated contract (`F:SAMPLX` into
  `F:CLG6.US`) was treated as cash-settled, so the premium was realized
  in the wrong year. The option is now matched to the one stock line in
  its account that trades at the assignment (a note names it); an
  ambiguous match warns and stays cash-settled.
- **Settle dates that pair across legs and files.** An IB assignment's
  option leg now settles with its stock leg (before the 2024 T+1 cutover
  the option settled a day earlier, and a same-day trade could consume
  the premium), and a Dec-31 0DTE option trade is clamped to its expiry
  even when the expiry row sits in the next yearly export (audit S058-01,
  S055-22).
- **A penny option close no longer stops `taxjson run`.** A sell whose
  commission exceeds its gross (closing at 0.01) nets negative proceeds;
  the schema used to refuse it on the always-strict parse, although the
  engine books it. An assignment stock leg's money is now checked
  against quantity x strike like any trade (audit S017-00, S017-02).
- **Futures are booked on their settled P/L, not their notional.** Each
  leg's notional (quantity x price x multiplier) was converted to CAD at
  its own date's rate, so the CAD gain carried FX on money that never
  changed hands, and Schedule 3 line 6 showed the notional as proceeds
  and ACB. A futures fill that opens a position now carries nothing; a
  close carries the realized native P/L (commissions included), converted
  at the closing leg's rate. Line 6 shows a gain as proceeds and a loss as
  ACB (the T5008 shape); `sum`, `form-export`, `audit` (which re-derives
  the P/L from the broker rows) and `fx-cash` agree. Real books moved
  in both years. Options on futures are unchanged.
- **`phantoms.json` openings reach the superficial-loss context.** An
  opening for a registered (or affiliated) account was applied to that
  account's own report but not to the context the taxable gains are
  tested against, so a TFSA with truncated history looked short and its
  in-window rebuy did not deny the taxable loss (permanently, as s.54
  requires). The openings now apply to every book.
- **Same-moment replacements: taxable first, then registered.** When a
  taxable rebuy and a TFSA/RRSP buy carried the same timestamp (common
  with Webull and Questrade stamps), a hash of the rows decided whether
  a superficial loss was deferred into the taxable ACB or lost for good,
  so a one-cent change on the TFSA row could move the taxable gain.
  At a tie the taxable acquisition now takes the denial first, then
  sheltered, then affiliated accounts (both engines; `taxjson
  tax-logic` lists the rule).
- **Moving shares between your own registered accounts no longer flips
  a superficial loss.** A custody move such as rrsp -> rrsp2 was netted
  out of the superficial-loss context, but the "still held at day 30"
  test runs per account: the receiving account looked short (a
  permanent denial for its in-window rebuy was missed) and the sending
  account looked long (a denial was invented). Netted moves now stay in
  the context as balance-only rows; they are never replacement
  property. The misleading "rrsp2 go short" hint goes away with it.
- **An IB/RBC-style assignment keeps its own premium when a Webull-style
  assignment on the same stock comes later.** The option premium of a
  plain-convention assignment (stock leg booked as an ordinary buy/sell)
  was held back for ANY later marked `ASSIGN` stock leg on that stock in
  the account, even a year later, and moved to that year. A marked leg
  now reserves only the premium of its own option leg (same account and
  underlying, within 7 days).
- **One replacement backs one denial.** A 100-share rebuy (or one call)
  inside the window of a sale split into ten fills denied every fill's
  loss in full: ten times the loss it could back. Replacement units are
  now claimed in a fixed order, each by one denied unit, across fills
  and across losses; a call that expires before day 30 is not held on
  day 30. Real books moved by small amounts.
- **`cross_asset` retired; puts are never replacement property.** A put
  is a right to sell, so the old warn-only "long put vs short-cover loss"
  scan is gone, and with the call rule enforced nothing is left opt-in.
  The rule is: an option (a long call) replaces shares, never the
  reverse; an option is replaced only by the identical contract. The
  `cross_asset` key and `--cross-asset` flag are accepted and ignored
  (`run` says so); the US engine prints its call warning without them.
- **Calls are replacement property for share losses (s.54 para (i)).** A
  long call on the same shares, opened within 30 days of a loss on long
  shares and still held on day 30 (any account, registered included), now
  denies the loss at 100 shares per contract; the denied amount is added
  to the call's cost (permanent when the call is in a registered
  account). It was a warn-only scan behind `cross_asset`. One-way by
  design: shares never replace an option, and only the identical contract
  replaces an option. `wash-radar`, `buy-check`, `sell-check` and
  `edge-cases` follow the same rule (buy-check and sell-check no longer
  let an option's row decide a share trade or another series' trade).
- **Settle dates skip holidays.** Brokers that don't print a settle date
  (IB, and Webull's trade date worked back from its settle date) were
  dated by skipping weekends only, so trades
  settled on closed days (Good Friday, Labour Day, Jan 1).
  That picked the wrong day's Bank of Canada rate and could move a
  repurchase into or out of the 30-day superficial-loss window. A new
  rule-based calendar (`lib/market_calendar.py`) counts settlement days:
  US = NYSE plus Federal Reserve holidays (Columbus and Veterans Day trade
  but don't settle); Canada = TSX plus Remembrance Day and the National
  Day for Truth and Reconciliation. It was checked against every settle
  date Questrade and RBC print. Equities before 2017-09-05 now settle T+3.
- **Futures settle on the trade date.** IB futures and futures options
  were given the stock T+1 settle date, so a close on Dec 31 landed in the
  next tax year. Variation margin settles their profit and loss daily, so
  the disposition is now the trade date. `[settings] futures_settle =
  "next_day"` restores the clearing house's premium date.
- **Futures shorts are not "truncated history" candidates** — an IB
  futures sell-to-open is excluded from the go-short hint like an option
  write (`--include-options` still shows them).
- **`option_grant_timing_since` no longer drifts with `year`**: unset, it
  defaults to the project year, so consecutive default projects taxed a
  year-straddling premium twice (+399 in 2025, +298 in 2026 for a 298
  economic gain). `taxjson init` now writes the key uncommented; `taxjson
  run` and `option-boundary` warn on every run while a Canadian grant-
  timing project leaves it unset; `close-year` records the timing in
  `filed/<year>.json`; and `option-boundary` flags ATTENTION on a
  contract written in a locked year but kept on transition close timing
  here (definite when the lock records grant timing).
- **Superficial loss (s.54): only substituted property backs a denial.**
  A denial now needs units acquired inside the 61-day window that the
  same holder (the taxable pool, or one registered / affiliated account)
  still owns at its end. Units a registered account held before the
  window no longer back one: a taxable rebuy sold again inside the
  window, with an RRSP holding shares bought years earlier, was denied
  PERMANENTLY (-1,000 → now allowed); a partly-sold taxable rebuy now
  defers only what is still held instead of turning the rest permanent.
  (A short taxable balance at day 30 also no longer offsets a registered
  account's still-held acquisition.)
- **Superficial loss: a trigger's side of the loss follows the pool's
  own ordering.** A rebuy that settles on the loss sale's settlement
  date but traded the day before (a later clock time) is in the pool
  the sale draws from; it was treated as bought after the loss, its
  basis bump landed before the sale and inflated the loss it deferred
  (-1,000 / 250 denied instead of -800 / 200), and some short-cover
  books never converged. Pre/post-loss is now decided by the phase
  ladder the pool replays with.
- **Negative proceeds (Canada).** A sell whose commission exceeds its
  gross — closing a worthless option at $0.01, writing one for less
  than the fee — now books its proceeds signed (negative) instead of
  as a positive amount (the loss was understated by twice the
  shortfall). Buys are unchanged.
- **s.49 grant timing: an assignment folds each premium once.** Every
  short opening — a write before `option_grant_since`, the short
  leftover of a sell that crosses zero — is now a lot, consumed FIFO by
  write date. An ASSIGN that consumed a lot with no grant record used to
  fold the premium into the shares on top of the grant already
  recognised (a book that totals 450 under close timing totalled 650
  under grant timing with `since = 2025`); totals are now identical
  under both settings (fuzzed with assignments and every `since`).
  A close-timing (pre-`since`) lot now closes before a later grant lot
  and at its own premium, and a buy-back of a grant lot is a loss of
  exactly the amount paid even when other lots were written at other
  premiums — both move amounts between years, never the total.
- **LSE, ASX and other non-North-American shares settle T+2.** The
  settlement lag followed the US T+1 cycle for every currency but CAD, so
  a GBP or AUD sale on the second-to-last trading day of the year landed
  in that year; GBP/EUR/CHF now move to T+1 on 2027-10-11 and every
  other non-North-American currency stays T+2.
- **An option held past its expiry is named.** `taxjson run` warns (on
  the console and in the `.sum` DIAGNOSTICS) for every option a taxable
  account still holds after its expiry date — the export dropped the
  expiry, assignment or exercise row. For a long contract the premium
  paid is an unbooked loss of the expiry year; option-boundary covered
  written contracts only.

### Tax rules and engine: United States (experimental)

- US-WASH-20 also says a same-moment tie never goes by the account's
  name, and US-STKDIV-03 that the $0 purchase of a stock dividend with
  no shares held is never a wash-sale replacement; each is pinned by an
  engine test (re-audit A2-0485, A2-1486).
- tax-logic states that a US taxable purchase whose shares were sold
  (first in, first out) before a loss no longer washes it, unlike an IRA
  purchase (US-WASH-21); the US engine's unreachable 'consumed
  replacement' branches are removed (re-audit A2-0817).
- **US: an unapplied return of capital or notional distribution is on
  the console.** A basis adjustment the US engine cannot apply (no
  shares held after a full sale, or the position short) printed only to
  the .sum; `taxjson run` now echoes it as `warning: ATTENTION:
  unapplied basis adjustment: ...`, and a basis increase is no longer
  called a return of capital (A2-0199, A2-0964). The US stock-dividend
  notes print only for the tax year's dividends (A2-0956).
- **US: a loss on a futures contract (or an option on one) is no longer
  disallowed as a wash sale.** A §1256 contract is not stock or
  securities; a re-bought F:CLG7 had its whole loss disallowed with no
  flag. The re-purchase is now flagged for a manual check
  (`futures_vs_loss`); Canada keeps denying (s.54 covers any property)
  (tax-logic US-WASH-18; A2-0053).
- **US: same-moment replacement lots of two accounts follow the
  taxjson.toml order**, as US-DATE-13 states, not the account label:
  renaming an account moved a wash-sale deferral to the other account's
  lot (A2-0200, A2-0208).
- **US: a stock dividend posted after the shares were sold is not a
  wash-sale purchase** (US-STKDIV-01); it washed part of the loss. The
  warning now names the sold-before-paid case (A2-0205).
- **US: wash-sale replacement lots keep the right shares, units and
  holding periods.** A replacement bought before a split got the
  disallowed loss on the pre-split share count (half the matched
  shares), creating a fake loss and an inflated gain at its sale
  (A2-0054); a later purchase matched by two losses became one merged
  block with an averaged bump and the earliest tacked date, so a
  short-term block was reported long-term (A2-0060: now one block per
  matched loss); a short-side replacement bigger than the loss spread
  the proceeds reduction over every share of the new short, moving loss
  into a later year (A2-0206: now share for share, as on the long side).
- **US: shares closed by one sale never wash each other.** A sale that
  closed an old lot together with shares bought in the last 30 days
  washed the old lot's loss into the very shares it was selling, split
  them, and cascaded one chunk at a time: a 1-share old lot sold with
  100 recent shares gave 101 Form 8949 rows (100 code W, 55,500 of
  adjustments on a 1,060 loss, the short-term loss reported long-term),
  and a 0.001-share lot 100,001 rows. Shares (or shorts) closed by the
  same sale or cover — one row, or the same-second fills of one order —
  are no longer replacements for each other; shares kept after the sale
  still are (tax-logic US-WASH-17; audit A2-0001, A2-0017, A2-0553).
  The wash radar's US EXITABLE advice now says the full exit must be
  one order.
- US `reorg_368_boot`: the cash boot and the new shares' value (in the
  new listing's currency) and the old basis (in US dollars) are combined
  in USD, and each leg is booked in its own listing's currency at the
  effective date's rate; the hints were added unconverted across
  currencies (A2-0216). The prompts name each hint's currency.
- **US: a §355 spin-off's `allocated_acb` is read in US dollars** on any
  listing (A2-0973): on a TSX-listed parent it is converted at the
  spin-off date's rate, so the moved basis is exactly the USD figure;
  it used to be read as CAD. The prompt and US-CORP-07 say so.
- US: a stock dividend paid after the shares were sold (between the
  record and pay dates) no longer asks for "the missing purchase
  history"; the warning says the §307 allocation reaches the sold lots
  and must be adjusted by hand (A2-0562).
- **US: a move between two of your own taxable accounts is flagged.**
  The per-account lots do not carry the moved lot's basis, so the
  receiving account's sale read as a short and the run exited 0; the
  run now prints ATTENTION for each such move and `--strict` stops
  (tax-logic US-BASIS-05; the carry itself is in KNOWN_ISSUES, audit
  A2-0032).
- **US: a spouse's replacement purchase is a permanent denial in your
  books.** With affiliated trades given (`taxjson-gains --affiliated`),
  a loss whose replacement your spouse or controlled corporation bought
  was reported as a deferral (permanently_disallowed 0) that no lot in
  your books carried, so it never came back and the wash conservation
  did not foot. §1091(d) puts the basis adjustment on THEIR shares: the
  loss is now permanently disallowed here, as for an IRA replacement,
  and the trace says the basis goes to the affiliated holder. tax-logic
  states the rule (US-WASH-16) (partition SPEC-30, ENGINE-I1).
- **US: FIFO is per account on any merged book.** `taxjson-gains
  --country usa` (and the library's GainsRequest) now keeps FIFO lots
  per account by default, as `taxjson run`'s blended pass always did;
  a direct run on a merged book used to pool the accounts' lots
  (tax-logic US-BASIS-01, partition SPEC-30). `--per-account-basis` is
  still accepted.
- **US mergers and spin-offs: non-recognition is not optional.** The
  option text no longer offers `taxable_exchange` for a qualifying
  §368(a) reorganization "you aren't claiming", and the end-of-run
  FILING REQUIRED reminder no longer fires for `reorg_368`,
  `reorg_368_boot` or `tax_free_355`: only a significant holder (5% of a
  public company, 1% of a private one, or a $1M basis) attaches the
  Reg. §1.368-3 / §1.355-5 statement (S073-00).
- **US: the call-as-replacement warning is sized.** Only the opening
  part of a call purchase counts (a buy that closes a written call is
  not an acquisition), each contract stands for 100 shares and is used
  against one loss's shares only, and the warning says how much of the
  loss is at risk. In a year-scoped run a prior year's warning no
  longer reaches this year's diagnostics, and `summary.count` counts the
  records in the file. tax-logic US-WASH-12.
- **US: January fund/REIT dividends.** A January dividend with an
  October–December ex or record date is warned about (§852(b)(7),
  §857(b)(9)); `[settings] ric_january_dividends` (US-only) moves the
  listed payments to Dec 31 of the prior year. tax-logic
  US-INC-DATE-RIC.
- **US: a return of capital beyond basis is booked (§301(c)(3)).** It
  was a warning only, and the excess came back as extra gain when the
  shares were sold — right total, wrong year. Now the part beyond each
  lot's basis is a capital gain on the distribution date (short- or
  long-term by that lot's holding period) and the basis stays at zero;
  Form 8949 describes the row as a nondividend distribution in excess of
  basis. A return of capital received with no shares held is still a
  warning (report it by hand). tax-logic US-ROC-01/02/03.
- **US projects: an IB return of capital reduces basis by default.** A
  `country = "usa"` project no longer books an issuer-designated return
  of capital as a dividend under Canada's s.90(2) rule; it is a
  nondividend distribution (IRC s.301(c)(2)) unless `[settings]
  foreign_return_of_capital` says otherwise (audit S013-01).

### Canada/USA partition and tax-logic

- tax-logic states the date rules the parsers already apply: an expiry
  posted at most 7 days late is moved back to the contract's expiry
  date, a same-contract trade on the expiry day settles no later than
  the expiry, Webull's printed date is the settle date with the trade
  date walked back one cycle, and same-moment rows of one account from
  two input files follow the files' name order (CA-DATE-15..18, US-
  DATE-14..17; re-audit A2-0483, A2-1478, A2-0823).
- tax-logic gives the close-timing buy-back claim its own id (CA-OPT-10;
  CA-OPT-05 named two claims), each pinned on the engine (re-audit
  A2-0826).
- tax-logic states rules the code already applies: a US project keeps
  sheltered (IRA) accounts out of Form 8949 and the totals (US-
  BASIS-07); the T1135 test covers these books only (CA-RPT-15); the
  radar's IRA replacement is lost for good (US-PLAN-01); cash in lieu of
  a fraction is a sale of it (CA-CORP-05, US-CORP-09: in the US the
  units come from the oldest lot), an all-cash merger is a sale and a
  stock-and-cash merger stops the run (CA-CORP-09/10, US-CORP-10/11)
  (re-audit A2-0822, A2-1473, A2-0821, A2-1474, A2-1475, A2-0824,
  A2-1477).
- tax-logic states what the code already did: US-WASH-20 (US replacements
  match in the order acquired, Reg. §1.1091-1(c), losses in the order
  sold, same-moment purchases taxable, then IRA, then affiliated),
  US-STKDIV-03 (a stock dividend with no shares held is a warned $0
  purchase), CA-ACB-07 (a s.40(3) deemed gain shows on Schedule 3 with no
  proceeds) and CA-DATE-08 / US-DATE-08 (a right or warrant expiry is
  dated like an option's) (re-audit A2-1506, A2-1511, A2-0495, A2-0845,
  A2-0833).
- `scripts/check_tax_rules.py` check 8 also fails on a source message
  that refuses an option for one country ("--x ... is Canada-only")
  when lib/country does not own it, and checks PLAN_COUNTRY's owners;
  tax-logic's CTRY-02 lists `--foreign-roc dividend` (audit A2-0719,
  merged with partD's table).
- One-country wording: `taxjson elect --set` names a US election key in
  a US project (not the Canadian s.85.1 one), the retired `cross_asset`
  warning states the US rule (a long call is only flagged) in a US
  project, and `taxjson-gains --help` says `--option-premium-timing` /
  `--per-account-basis` are refused in the other country, as they are
  (audit A2-0718, A2-0724, A2-1241, A2-1273).
- `[accounts.X] plan` kinds belong to one country (lib/country
  PLAN_COUNTRY): the other country's plan is refused (CA-CTRY-02 /
  US-CTRY-02), US `hsa`, `403b`, `457b`, `sep`, `529` and Canadian `lif`,
  `lrif`, `rdsp`, `prpp` are known, the "did you mean" hint names only
  the project country's plans, and a registered plan on a taxable account
  is warned about (the scan treats it as taxable) (audit A2-0739,
  A2-1272, A2-1332).
- US projects' `roc-sum` (and the roc/roc-sum help), `divs-sum`,
  `trades-sum`, `leaps` / `leaps-sum` / `ccd-sum`, `audit` and
  `missing-history` name basis, Form 1099-DIV (box 3 for nondividend
  distributions), Form 8949 and IRA / tax-advantaged accounts instead of
  ACB, T3 box 42, T5/T3 slips, Schedule 3 and "registered" (audit
  A2-0439, A2-0741, A2-1265, A2-1269, A2-1271, A2-1324, A2-1354,
  A2-1358, A2-1359).
- A Canadian project's `wash-sales` report is titled SUPERFICIAL LOSSES,
  counts "superficial loss(es)", says the denial goes onto the ACB of the
  substituted property (s.53(1)(f)), and its `--explain` trace and the
  single-account run note name the superficial-loss rule; the US keeps
  the wash-sale wording (audit A2-0748, A2-1249, A2-1326, A2-1352,
  A2-1360, A2-1371, A2-1372).
- tax-logic CA-RPT-12 cites s.49(3) for a call and s.49(3.1) for a put.
- `scripts/check_tax_rules.py` checks the country-ownership tables stay
  complete (owner and reason for every entry, every one-country flag a
  real CLI option the refusal reads, every one-country command a
  `taxjson` subcommand, every `[settings]` key the code reads listed,
  and every option whose help says "Canada only" / "US only" in a
  table). `--foreign-roc dividend` (ITA s.90(1)) and
  `taxjson-carryover --slip-gains` (T5 box 18) are now in the tables.
- An invalid `ric_january_dividends` / `corporate_distributions` entry
  is refused with an example listing of the setting's own country
  (SAMPLE.US for the US-only RIC list), and a bad `--ric-january-dividend`
  flag is named as the flag, not as a `[settings]` key (re-audit
  A2-1306).
- US projects no longer see Canadian forms and terms (audit A2-0735 and
  siblings): the account `.sum` points to Form 8949 / basis (Schedule
  3 / ACB in Canada; `taxjson-sum-gains --country`), `taxjson audit`'s
  totals note names Form 8949 rows, `roc-sum` names 1099-DIV box 3 and
  basis, `trades-sum` and `find-missing-history` call a sheltered
  account an IRA, `carryover` names the IRS only, `handoff` speaks of
  deferred wash-sale losses (§1091(d)), the holdings TOML cost note has
  no s.47 blend (`taxjson-export --country`), and the checklist's
  estimate, sheltered-inputs, run-clean, elections and n/a steps use
  US wording. A US crypto book's audit trace no longer says "wash sale
  §1091". The Canada engine's non-convergence warning says
  "superficial-loss solver" (it said "CRA wash-sale solver").
- `taxjson-apply-distributions` without `--country` writes "(cost up)"
  instead of Canada's "(ACB up)" into the book row (A2-1234).
- tax-logic states engine behaviour that only KNOWN_ISSUES described:
  Canada's basis increase on an emptied pool goes to the next purchase
  and an ADJUST on a short is the short seller's compensation payment
  (CA-ACB-13/14); the US leaves both unapplied (US-ROC-04); §1091(e)(1)
  is not modelled (US-WASH-19); an IRA buy sold before the loss still
  makes it permanent (US-WASH-11); Schedule 3's acquisition year comes
  from trade-date days held (CA-DISP-07) (A2-0062, A2-0962, A2-0963,
  A2-0964).
- **tax-logic states RBC notional distributions and DRIP** (CA-DIST-02/03,
  US-DIST-02/03).
- **tax-logic states what US projects run.** The US section gains the
  rules its input stages and engine already applied: settlement cycles
  and holidays, the generic importer's settle column, crypto and expiry
  dating, `futures_settle`, the FX gap handling, identity and ticker.map,
  the transfer stop, the corporate-action elections (`taxable_exchange`,
  `reorg_368`, `reorg_368_boot`, `taxable_distribution_301`,
  `tax_free_355`), payments in lieu, staking income, dividends booked
  gross with withholding as its own row, and the crypto parser rules
  (coin-for-coin trades, fees in coin, the 3-day pairing, stablecoins as
  US-dollar cash — an approximation). Two Canadian statements were wrong
  and are corrected: the FX source (Bank of Canada noon rate before March
  2017 from May 2007; Yahoo only before that) and the rate-gap rule (a
  longer gap is a validation ERROR that `run --strict` stops on, not an
  automatic stop). A stablecoin traded more than 2% off 1.00 USD on
  Coinbase or a Kraken USD pair now prints a warning with the de-peg
  amount the approximation leaves out.
- **Shared helpers no longer carry one country's law.** The warrant /
  right replacement warning said "the loss may be superficial" in US runs
  (now "may be a wash sale"); the §355 tax-free spin-off could book
  Canada's s.86.1(3) `allocated_acb_cad` hint in CAD if called directly
  (only the Canadian election reads it now); the registered-account
  fallback for unconfigured accounts knew only Canadian plan names (IRA,
  Roth, 401(k), HSA ... are recognised; by country when it is known).
- **Manual loss checks next to phantom-basis sales follow the country.**
  With `phantoms.json`, the "check by hand" warnings measured the window
  on settle dates in every project (the US rule runs on trade dates) and
  the partial-taint one on trade dates (Canada's runs on settle dates),
  and both said "superficial-loss" in US projects, US crypto included.
  Now: settle dates and ITA s.54 in Canada, trade dates and "wash-sale
  check (§1091)" in the US, and none where wash detection is off (US
  crypto). Warnings only; no numbers change.
- **Standalone `taxjson-brokerage` no longer applies Canadian law by
  default.** Its `--foreign-roc` defaulted to `dividend` (ITA s.90(2)),
  so the documented manual pipeline for a US book turned a US issuer's
  return of capital into dividend income. It now takes `--country`
  (canada: s.90(2) dividend; usa: a basis reduction, and `--foreign-roc
  dividend` is refused); with neither flag the return of capital lowers
  the cost and a note says so. `taxjson run` passes both flags, so
  project numbers are unchanged.
- **Stock dividends follow the country.** The IB, Questrade and RBC
  parsers booked a stock dividend as a $0 purchase in every project, so
  in a US project the new shares were a short-term zero-basis lot and a
  wash-sale replacement. The parsers now emit a neutral stock-dividend
  event; the US engine applies §305(a)/§307 (the basis is spread over old
  and new shares, the purchase date carries over, no wash sale), and the
  Canada engine keeps the $0 acquisition (an s.54 acquisition) with the
  "add the declared amount" note, now printed by the gains run instead
  of the parser. Canadian numbers are unchanged. tax-logic CA-STKDIV-01,
  US-STKDIV-01/02.
- **Futures follow the country, not the base currency.** The settled-P/L
  booking of futures (no notional, each close's P/L at its own rate) ran
  only for a CAD target, so a US project kept FX on the notional of a
  non-USD contract, and the US engine booked a settlement row with its
  sign inverted (a short closed at +5,000 was -5,000). Both countries now
  use the settlement basis, chosen by the project's country
  (`taxjson-convert-currency` / `taxjson-merge2 --country`, required when
  a book has futures); a partial close takes average cost in Canada and
  FIFO in the US. Canadian numbers are unchanged. tax-logic US-FUT-01/02.
- **No Canadian terms in US output.** `wash-sales`, `carryover`,
  `crypto-sends`, `fx-cash`, `reconcile-slips`, `buy-check`, `sum`,
  `checklist`, the radar and the web pages named s.54, "superficial",
  "registered account", 50% inclusion / T1A, line 22100, T5008 box 20 or
  s.39(1.1) in US projects; each now names the project's own law.
- **tax-logic states fx-cash §988 and the US estimate's assumptions**
  (US-FX-03, US-RPT-07, US-RPT-08): §988 gains are ordinary and not on
  Form 8949, with no $200 exemption; the estimate treats every dividend
  as qualified, gains with no term as short-term, and leaves out foreign
  tax credits, interest and state tax.
- **Canadian and US law no longer mix through the country setting.**
  Every command, the web UI and every standalone tool read the country
  through one resolver: `[settings] country` is required (a missing one
  was Canada everywhere but `run` — Schedule 3, T1135 and s.39(1.1) were
  printed over US books) and only canada / ca / usa / us are accepted
  (`taxjson-audit` read "United States" as Canada). The standalone tools
  (`taxjson-gains`, `-explain`, `-audit`, `-carryover`, `-harvest`,
  `-wash-radar`, `-safe-to-sell`, `-corp-actions`) now require
  `--country` instead of assuming Canada; `taxjson` passes it for you.
- **Settings, flags and commands belong to a country.** A Canada-only
  setting in a US project (`province`, `option_premium_timing`,
  `option_grant_timing_since`, `option_buyback_loss_superficial`,
  `foreign_return_of_capital`, `[instalments]`, `[estimate]
  deductions`/`carrying_charges`) is refused by every command, naming
  the key — it was silently ignored, and `foreign_return_of_capital =
  "dividend"` (ITA s.90(2)) was even applied to a US filer's IB return of
  capital. `taxjson-gains`/`-explain`/`-audit`/`-carryover` refuse the
  Canada-only option flags with `--country usa` and
  `--per-account-basis` with `--country canada`; `estimate`/`sum`
  refuse `--province`, `--deductions` and `--carrying-charges` in a US
  project (`--province XX` was silently ignored). A `tax_date` that
  departs from the country's practice is accepted with a warning. `t1135`,
  `instalments`, `option-boundary` and `form-export --form schedule3`
  are refused in a US project (they gave CRA advice), and `form-export
  --form 8949`/`txf` in a Canada project with a country message.
  `base_currency` must be the country's currency (CAD / USD): a US
  project in CAD produced Form 8949 in CAD at Bank of Canada rates; an
  unset `base_currency` means the country's currency, not CAD.
- **`taxjson tax-logic` is the spec, with rule ids.** Every statement has
  a stable id (`--ids` shows them, `--json` lists them) that the tests
  cite; `scripts/check_tax_rules.py` (a CI stage) checks the links. It
  reads the settings through the same resolvers the engine uses (a
  `" Grant "` timing rendered close timing; `"nextday"` / `"ACB"` were
  described although the run refuses them), refuses an unknown country
  instead of rendering Canada, and now states the income dating rules
  explicitly (how dividends, payments in lieu, returns of capital and
  trust distributions are dated in each country: CA-INC-DATE-TRUST,
  CA-INC-DATE-ROC-TRUST, US-INC-DATE-RIC), plus which settings and
  commands the project's country refuses.
- **`taxjson tax-logic`.** A one-screen statement of every rule taxjson
  applies for the project's country (or `--country`), one line per rule
  (citing the Act where the rule comes from it), with the project's settings filled in
  where they change the answer. `--json` for machines.
- `find-missing-history --gen-phantoms` passes the project country
  (no more "--country not given; assuming canada" per account).

### Income: dividends, distributions and return of capital

- Canada: a payment in lieu on a Canadian trust's unit (an ETF, REIT or
  fund unit the books show to be a trust's: they carry a distribution on
  it) is ordinary income, no longer an ITA s.260 deemed dividend grossed
  up in the estimate; s.260(5) covers shares only. A unit whose payouts
  no export calls distributions (IB) still reads as a share (re-audit
  A2-1465; CA-INC-03 / CA-INC-07).
- Canada income dating: the January return-of-capital warning no longer
  calls every Canadian issuer a trust — it asks, and listing a
  corporation in [settings] corporate_distributions stops it; tax-logic
  states that every Canadian issuer counts as a trust for the record-
  date rules unless it is on the corporate list (CA-INC-DATE-ISSUER),
  that the issuer's ISIN country decides over its listing, and that a
  payment in lieu on a Canadian ETF or REIT unit is deemed a dividend
  because the export cannot tell a unit from a share (CA-INC-07) (re-
  audit A2-0810, A2-1466, A2-1470, A2-1465, A2-1468).
- `taxjson-sum-income` reads its rows through the same checks as
  taxjson-gains: an impossible date, a NaN/inf amount or a text amount is
  a one-line error with exit 2 instead of being summed (or a traceback)
  (A2-1448).
- edge-cases "Income paid around New Year" uses income dating: a
  Canadian trust's distribution with a December record date lands in
  the record year (it said the pay year), and payments in lieu and
  trust returns of capital are listed (re-audit A2-0387, A2-1207,
  A2-1209).
- Canada stock-dividend ATTENTION: printed only by a taxable run of the
  dividend's own year, quiet once an ADJUST adds the cost, and worded
  right — adding the cost books the ACB only; the dividend is reported
  from the slip. Income-dating advice is no longer printed for sheltered
  books, and a TRANSFER in a taxable account names the account instead
  of a `--taxable` flag the user never passed (re-audit A2-0709,
  A2-0711, A2-1220, A2-1224, A2-1218, A2-1221).
- The tax withheld on a dividend now moves with it when income dating
  re-dates the payment (a US January RIC dividend, a Canadian trust's
  record-date distribution), so one payment's income and withholding are
  in the same year; a listed US January RIC dividend is named on the
  console as `ATTENTION: income year:` in both project years (re-audit
  A2-0396, A2-0398).
- A pay-year run names, as ATTENTION, each prior-year sale whose ACB a
  December-record trust return of capital lowers (that year may be
  filed without it) (re-audit A2-0039; the audit / explain / what-if
  record-date dating of A2-0139, A2-0201, A2-0397 is A2-0033 /
  A2-1175).
- **`taxjson audit` / `taxjson-explain` recompute the books the way the
  run does.** They now apply the Canadian trust ROC record date
  (CA-INC-DATE-ROC-TRUST, with the project's corporate_distributions),
  test the grant-timing since-year on the project's tax date, and use
  per-account FIFO on a US book by default, so a correct project no
  longer fails its tie-out (re-audit A2-0033, A2-0327, A2-0314,
  A2-0317, A2-0315, A2-0318). The audit traces and ties out a s.40(3)
  deemed gain (a return of capital on an empty pool or beyond the ACB,
  A2-0316), ties phantom-basis sales to the MANUAL REPORTING REQUIRED
  list instead of calling them "stale or truncated saved books" with
  exit 1 (A2-0640, A2-1098), and quotes an option's per-share price at
  its declared contract size (A2-1099).
- `divs` / `roc` / `events`: a tax-year window places income and ROC
  rows by their tax date, as `divs-sum` / `roc-sum` / the .sum do (a
  Canadian trust's December record date, a listed US RIC January
  dividend); the row still shows its pay date and a note says why it is
  in the window (re-audit A2-0326, A2-0641, A2-0642, A2-0655, A2-1109,
  A2-1125, A2-1127).
- `roc` / `roc-sum`: an RBC notional distribution is counted once (it was
  also taken for a distributions.map row: doubled, with a false
  double-entry warning); the `roc` view warns about a ROC entered both in
  the books and in distributions.map, as `roc-sum` does; a missing base
  book is named when distributions.map exists (re-audit A2-0116,
  A2-1116, A2-1128).
- **A cost adjustment in another currency no longer stops `taxjson run`.**
  A USD return of capital or notional distribution on a TSX listing
  (RBC, Questrade, IB, in either country), or a CAD `.tt` ADJUST /
  DISALLOW on a USD unit, made the native-currency raw pass exit 1 at
  "raw gains" with advice to run an underscore tool. The raw merge now
  restates such a row in the listing's currency at its date (a note per
  row); with no rate on file the native holdings view is skipped with a
  note. The filing books were never affected. The engine's own
  currency-mismatch error names the row and the installed command
  (A2-0055/0191/0204).
- `taxjson list --date` now passes the project's income-dating settings
  (`corporate_distributions`) to its recomputation: a listed
  corporation's return of capital was moved to its record date, as for
  a trust, so the as-of cost disagreed with the run (A2-0995, A2-0996).
- **Canada income dating: more split-share corporations, a loud
  year-end flag.** XTD, GDV, LCS, PWI, SBN, WFS and PIC.A (and any row
  whose description says "SPLIT CORP") are corporations: their
  December-record, January-paid dividends and returns of capital now
  stay in the pay year instead of moving to the record year as a
  trust's. A `corporate_distributions` entry covers its issuer's
  classes and series (`GHI.TO` covers GHI.PR.B.TO; `DEF.UN` now
  matches DEF.UN.TO). A record date 92+ days before the pay date is no
  longer used. Every trust distribution or ROC whose record date puts
  it in another year than its payment is now printed on the console
  (`ATTENTION: income year:`) in both project years — one of them
  leaves it out. The IB January trust-ROC warning stops once the two
  `.tt` lines it prescribes are in the books. US: a bare
  `ric_january_dividends` entry (`T`, `PSA`) is that fund's US listing
  only — a Canadian issuer (SAMPMC.TO) and PSA.PR.H.US are no longer moved (A2-0073,
  A2-0076, A2-0229, A2-0230, A2-0231, A2-0561, A2-0991, A2-0992,
  A2-0993).
- **capital_gains_dividends.map reads what it documents.** A bare root
  (`FTN`, `T`) also claimed the issuer's preferred series (FTN.PR.A.TO)
  and a same-root foreign listing (a US issuer's SAMPMC.US), turning their
  dividends into box-18 capital gains; it now covers only the root's
  Canadian listings. An AMOUNT with a decimal comma (`17,11` read as
  1711) or an underscore is refused, a map that is a directory or a
  dangling symlink is an error instead of "no map", and a date entry
  matches the pay date of a distribution the books date by its record
  date (the documented `ABD.TO 2025-06-16 1.25` example was refused)
  (A2-0075, A2-0227, A2-0228, A2-0560, A2-0987, A2-0990, A2-0994).
- **distributions.map adjustments reach the holder of record's lots in
  the US.** The ADJUST was stamped at the end of the record date, so in
  a trade-date engine a sale traded on the record date (still the
  holder of record under T+1) left it "found no open lots ... NOT
  applied" (and called a basis increase a return of capital), and a buy
  traded on the record date shared it. A trade straddling the record
  date now moves the stamp to the day before it (the record date stays
  the settle date). A map return of capital now warns when the book
  already has that ROC (broker row or .tt ADJUST, by pay or record
  date — also in `roc-sum` across the year end) or when its cash is
  still a DIVIDEND row counted in full as income (A2-0071, A2-0072,
  A2-0232, A2-0988).
- **Per-account holdings and distributions.map sizing follow each
  ticker's own shares.** The record-date balance walk (used to size a
  distributions.map adjustment and to split a blended Canada pool by
  account) kept one running balance for a whole rename family: a
  rename-split scaled shares already held under the new ticker, an old
  ticker bought again after its rename counted under both names, two
  accounts' copies of one split doubled each other, and a buy listed
  before a same-moment split was scaled by it. It now holds shares per
  account and symbol like the engine (list/shares/sanity and the
  adjustments agree with the gains), a map key that still holds shares
  under its own name is sized on them, and the blended-pool
  conservation warning names an excess as over-reporting instead of
  blaming phantoms (A2-0021, A2-0074, A2-0225, A2-0986).
- **Canada: T5 box 18 capital-gains dividends can be named.** A new
  project-root `capital_gains_dividends.map` (`SYMBOL YEAR-or-DATE
  all-or-AMOUNT [ACCOUNT]`) lists the split-share / mutual-fund
  dividends the slip reports in box 18. `divs-sum` shows them apart as
  CAPITAL-GAINS DIVIDENDS (line 17400) and the Canadian estimate taxes
  them as a capital gain instead of a grossed-up eligible dividend. The
  ledger and ACB are unchanged; a US project refuses the file
  (tax-logic CA-INC-06; audit R1-62).
- **distributions.map is read strictly.** A per-share amount must be a
  plain decimal (`nan`, `inf`, `1e309` and `1_0` were accepted) and the
  date a real `YYYY-MM-DD`; a `0` is a placeholder that is no longer
  reported as an applied return of capital; a symbol and date entered
  twice are both applied with a warning and each ADJUST gets its own
  id; `taxjson-apply-distributions --account` refuses a label no row of
  the book carries (S025-12/14/16/19/23).
- **The foreign return-of-capital citation is ITA s.90(1), not
  s.90(2).** s.90(2) is the foreign-affiliate rule; a portfolio
  holder's foreign dividend is included by s.90(1), and a real
  reduction of paid-up capital lowers the ACB (s.53(2)(b)(ii)). The IB
  row note, `taxjson-brokerage --help`, tax-logic CA-ACB-08 and the docs
  say so; nothing is computed differently.
- `taxjson-sum-income` refuses income rows with no amount instead of
  booking them as $0; its help names the real input (a base book).
- **Canada: a Canadian trust's distribution counts in its record-date
  year.** A "DIST ON ... REC 12/30/24 PAY 01/06/25" row (Questrade, RBC)
  on a Canadian issuer is 2024 income (s.104(13)) in `divs-sum`, the
  .sum, the estimate and instalments — as on the T3. Split-share
  corporations (FTN, FFN, DFN, BK, LFE, YCM ...) stay on the pay date,
  as does any symbol in the new `[settings] corporate_distributions`.
  tax-logic CA-INC-DATE-TRUST.
- **Canada: a Canadian trust's return of capital lowers the ACB on its
  record date** (s.53(2)(h)), so a sale between the record date and a
  January pay date uses the reduced ACB and any s.40(3) gain is in the
  record year; `roc-sum` counts it there. IB rows (no record date) keep
  the pay date and a January one on a Canadian trust is warned about.
  tax-logic CA-INC-DATE-ROC-TRUST / CA-INC-DATE-ROC.
- **Canada: a payment in lieu from a Canadian dealer on a Canadian
  issuer's share is a dividend.** ITA s.260 deems it a taxable dividend
  and the dealer's T5 box 24 includes it; it now counts in `divs-sum`
  and as an eligible dividend in the estimate (`dil-sum` shows each
  row's treatment). The IB parser records the dealer from the
  statement's BrokerName and the issuer's ISIN country. tax-logic
  CA-INC-03; US-INC-01 (US: always ordinary income).
- **distributions.map sizes on the holder of record in both countries.**
  The record-date balance followed the project's tax-year date basis, so
  a US project (trade dates) credited a buy traded on the record date.
  It is now always the settled position (unchanged for Canada's default).
  The income note names Form 1099-DIV in a US project (it said T3/T5).
- **Return of capital: no invented proceeds, right sign on a short.** An
  s.40(3) deemed gain (ROC below nil, or ROC after the position was
  sold) was booked with proceeds equal to the gain, overstating
  Schedule 3 line 13199; it now has no proceeds (CRA: 0 on 13199, the
  gain on 13200). A ROC ADJUST on a SHORT position raised the short's
  gain by the amount; it is now the short seller's compensation payment
  and lowers it (a note names it).
- **`roc` / `roc-sum` show `distributions.map` adjustments.** The map's
  ACB adjustments are booked only in `<acct>_base.json`, so both views
  said there were none while the engine applied them; they are listed now
  (MAP_ROWS), and `roc-sum` warns when the same symbol and date also has a
  `.tt` ADJUST (the ACB would be reduced twice) (audit R1-163).
- **`distributions.map` rows find their shares.** A map whose first line
  carried a byte-order mark, a lowercase symbol, a key naming the
  listing a `ticker.map` rule consolidates, or the old ticker after a
  ticker change was skipped as "no shares held" (or booked on a dead
  pool) and the ACB increase was lost. Keys are now matched
  case-insensitively, through `ticker.map`, and onto the ticker live on
  the record date; a sale executed before a split but settling after it
  no longer inflates the record-date balance. The NOTE also says the
  distribution is income to report from the T3/T5 slip.
- **`distributions.map` counts `phantoms.json` shares.** The record-date
  balance was taken from the book without the phantom openings, so a
  position with pre-window history got too small an ACB adjustment (or
  none, "no shares held"). Removing `phantoms.json` now rebuilds the
  adjusted books under `--fast` too.
- **Return of capital after the position is sold (Canada).** A ROC that
  posts when the pool is empty has no ACB to reduce; it is now a capital
  gain in the year received (s.40(3) with a nil ACB, noted on the row)
  instead of silently lowering the NEXT purchase's ACB (which moved the
  gain to a later year). Positive ADJUSTs on an empty pool are unchanged.

### Corporate actions and elections

- An IB corporate-action cancellation (`Ca`) now cancels the leg in its
  own currency: a spin-off delivered on both the CAD and the USD listing
  whose CAD leg IB cancelled used to drop the USD event and offer the
  cancelled CAD one for election (re-audit A2-0518).
- The elections manifest: a directory, an unreadable file, a symlink
  loop or a dangling link is one `manifest ... cannot be read` line in
  `elect`, `spinoffs` and run's FILING REQUIRED check (it was a
  traceback or read as 'no elections'); a failed save is one line and
  keeps the old file; a BOM is accepted; a non-string `summary` /
  `notes` is refused (A2-0160, A2-0463, A2-1402, A2-0804, A2-1399,
  A2-1401, A2-1449).
- Spin-off warnings from the broker parsers (IB, RBC, Questrade) and the
  rows the shared corporate-action emitters write (the parent's cost
  reduction, a ticker rename) use country-neutral words instead of
  s.86.1 / ACB / wash-sale (A2-1242, A2-1278). The row descriptions
  changed, so those generated rows get new ids (no amount changes).
- Two SPLIT rows for one event with different ratios (both applied) are
  now a validation ERROR named on the console: `run --strict` stops and
  checklist run-clean is not done (re-audit A2-0040).
- `taxjson elect ACCOUNT --set A=x --set B=y` is refused (nothing saved)
  instead of saving only the last --set at exit 0; give one --set per
  command (re-audit A2-0563, A2-0568).
- Canada blended books: a split and a trade at the same stamp are walked
  split first in the per-account split of the blended pass (and in the
  distribution balance walk), as the engine does (CA-DATE-14): a buy
  listed before a same-stamp 2:1 split showed 300 shares in `list` /
  `shares` where the books held 250, with a false "likely phantom"
  warning (A2-0013).
- Corporate-action rows name the election actually made instead of
  `election=none` (A2-0558), and a taxable merger booked at $0 says
  whether the saved election values it at 0 and gives the `taxjson
  elect ... --set` command the run-level warning gives (A2-0974).
- **Elections manifest migration never hands one event's election to
  another** (A2-0064, A2-0217, A2-0557, A2-0975, A2-0978; R1-301
  residue A2-0168, A2-0872): a record keyed by a current event's own id
  is never moved; an older id that several same-day, ISIN-less events
  share is resolved only by the record's saved summary, otherwise
  listed for you to set again; the account-rename fallback also needs
  the saved summary's action type and ratio to match; a change of the
  readable symbols is named in a note.
- **An s.86.1 election on a USD parent no longer stops `taxjson run`**
  (A2-0002, a regression of S072-15; A2-0215, A2-0967): the CAD amount
  given as `allocated_acb_cad` is booked in each listing's currency at
  the spin-off date's rate, so the tax books get exactly that CAD figure
  and the native holdings view stays in one currency. The raw-holdings
  guard now also counts ADJUST rows, so a row it cannot convert skips the
  native view instead of aborting the run.
- **A split repeated with a rounded ratio is applied once.** A manual
  `.tt` SPLIT line (2.333333, or convert-tt's 8 decimals) next to the
  broker's own row (2.333333333) scaled the pool twice at exit 0. Copies
  whose ratios agree to 1e-6 within the 7-day split window are one
  event, take the most precise ratio, and `run` prints an ATTENTION
  line naming them (A2-0070). A same-day rename chain (A→B, B→C) gives
  the same superficial-loss unit conversion in either row order
  (A2-0983).
- **`taxjson spinoffs` in a US project** flags a $0 §301 distribution
  (ZERO-VALUE) and a §355 spin-off with no allocated basis
  (NO-ALLOCATION) and exits 1, with US wording (an IRA is not called a
  "registered account") (A2-0063, A2-0221). `spinoffs` and `splits`
  refuse an unreadable or malformed manifest or base file instead of
  crashing or reporting an empty view (A2-0968, A2-0969); a corrupt
  manifest's error no longer suggests deleting it (A2-0979).
- **Renaming an account keeps its corporate-action elections.** An
  election's event id no longer includes the taxjson account name (the
  elections manifest is already stored per account). A manifest written
  by the old scheme is rekeyed on the next run, and so is one whose
  account was renamed first: the single saved election with the event's
  date and symbols is carried over, with a note. Before, every election
  went back to pending (exit 3) after a rename (audit R1-301).
- **Corporate-action legs never share a second.** An event stamped
  23:59:59 (or with no time) put both legs of a taxable exchange on one
  second, so a same-symbol exchange could pool the new shares before
  selling the old; event times now leave room for the one-second leg
  bump (S074-02).
- **A spin-off rollover allocated $0 is loud.** An s.86.1 (Canada) or
  §355 (US) election with `allocated_acb_cad` / `allocated_acb` = 0 booked
  the spun-off shares at $0 with the parent keeping its whole cost and no
  word; `elect --set`, the corp-actions stage, every `taxjson run` and
  the checklist now flag it (S073-21, S074-04). A US §356 boot merger
  with no value for the new shares warns that the recognized gain and
  basis are understated (S073-22).
- **taxjson-corp-actions reads IB Corporate Actions columns by name.** A
  header missing a column, or Data rows before any Header, is refused;
  the fixed-position fallback read Report Date as the event date in IB's
  consolidated layout or dropped the merger (S072-22). Questrade's
  `ON 1,500 SHS` reads as 1500 (the ratio showed 150-for-1) (S073-02).
- **A broken elections manifest is a one-line error everywhere.**
  `taxjson elect` (and every other command) on a manifest that is not
  UTF-8, not JSON, or not the documented shape (a bare-string record, a
  list of hints) printed a traceback; `taxjson elect <account>` now marks
  an election key no rule knows as UNKNOWN (S072-05, S072-16).
- **A merger two brokers book on different dates is one event.** The
  per-account merger-ratio fold grouped rows by exact date, so when IB
  booked a merger on 06-11 and RBC on 06-15 the first renamed the whole
  pool at its own ratio and the second found it empty: the extra share
  RBC delivered was never sold in the books. Rows within 7 days now fold
  into one event.
- **Corporate actions follow ticker_extraction_overrides.txt.** The
  overrides renamed a security's trades but not its merger/spin-off rows,
  so the event consumed an empty pool under the broker's spelling while
  the real position stayed put (a taxable merger's gain vanished). The
  corp-action rows now take the same rename; when the overrides rename
  some of a spelling's rows and not others, a corporate action on it is
  refused instead of guessed (audit S004-00).
- **Election hints must be non-negative amounts.** `elect --hint`, the
  interactive prompt and a hand-edited manifest.json refuse a negative,
  nan or inf hint (a negative allocated ACB created basis from nothing,
  a negative FMV booked negative dividend income, and nan was saved to
  fail only on the next run) (audit S039-00).
- **The FILING REQUIRED reminder survives an unreadable manifest.json**:
  the file is named and the other accounts' reminders still print
  (audit S038-23).
- **An IB corporate-action cancellation reaches the other statement.** A
  split booked in one yearly statement and cancelled (`Ca`) in the next
  is undone when both are in the account's inputs; it used to stay
  applied with a "reverse it by hand" warning (audit S059-04).
- **Corporate-action elections say what the law says.** The s.85.1
  share-for-share rollover is automatic when it applies (you opt out by
  reporting the gain); taxjson described it backwards and printed a
  FILING REQUIRED reminder for a form that does not exist. The option
  text now says when s.85.1 applies (not to a Canadian company bought
  by a foreign purchaser) and the reminder is gone for it. An s.86.1
  spin-off now takes the allocated cost in CAD (`--hint
  allocated_acb_cad=`, the parent's CAD ACB times the spin-off's share of
  the combined FMV, s.86.1(3)); the old source-currency `allocated_acb`
  still works but warns, since converting it at the spin-off date moved
  FX drift between the pools. A misspelled hint in a hand-edited
  `manifest.json` (`fmv`, `allocated_ACB`) now stops the run by name
  instead of booking $0, and a taxable merger at $0 FMV warns on every
  run even when cash-in-lieu was paid.
- **One split booked on two dates applies once.** IB and Questrade date
  the same split days apart; both copies
  were applied, scaling the pool by the ratio twice. Copies of one split
  (same security and ratio) within 7 days are one event, applied on the
  earlier date, with a note.
- **Spin-offs use the broker's value.** A deemed-dividend spin-off with
  no (or a 0) `fmv_per_share` hint is booked at the broker's reported
  value when there is one. A taxable spin-off booked at $0 is warned
  about on every run and in the account's .sum until a value is set.
- **A taxable merger uses one value for both legs.** The old shares'
  proceeds and the new shares' cost are now the same fair value (the
  shares received, plus any cash in lieu); the broker's separate out-leg
  and in-leg figures left their difference as a permanent phantom gain
  or loss. Across currencies, each leg is converted at the event date
  (a hint in USD was booked as CAD proceeds).
- **A merger or spin-off held in two broker accounts is booked for
  both.** Two IB sub-account statements, or two RBC accounts, feeding
  one taxjson account kept only the first account's disposition. Their
  quantities are now combined under the one election; an overlapping
  statement of the same broker account still counts once.
- **`taxjson spinoffs` and `taxjson splits`.** Every spin-off with its
  election, the value per share used and what was booked (income and the
  new shares' cost), flagging a taxable spin-off booked at $0 and showing
  the broker's own value when it reported one; every split,
  consolidation and rename with holdings before and after, flagging a
  split recorded twice, a no-op row and a fractional result.
- **Duplicate split rows warned.** When one account carries the same
  split twice (a parser that now books it plus a manual .tt SPLIT line),
  merge2 warns "duplicate split" (it is applied once); the same event
  with different ratios warns "conflicting splits".
- `elect`: `--pending --json` emits JSON when nothing is pending;
  `elect ACCOUNT --json` lists that account's elections as JSON (and
  `--json` with `--set/--redo/--reset` is refused); `--hint` without
  `--set` is an error; `--set` with an event id that matches nothing is
  refused instead of saving a junk record into manifest.json.
- **Mergers with per-account delivered ratios (Canada).** A merger whose
  accounts received different whole-share counts (15 → 15 in one, 40 → 41
  in another) emitted one rename per account at its own ratio; the
  symbol-wide pool was renamed at the first account's ratio and the
  second found no pool (a phantom short share).
  The rows now fold into one event at the holdings-weighted ratio, so
  the pool lands on the shares actually delivered.

### Broker parsers: all brokers

- A message never cites the other country's law (re-audit partition
  lists 07/08). taxjson-brokerage passes the project's country to the
  parsers, which use it only to pick the citation: the Webull inferred
  exercise/assignment note cites s.49(3)/(3.1) in Canada and Rev. Rul.
  78-182 in a US project (and says "cost or proceeds"); the IB
  share-for-share tender note names s.85.1 or a §354/§368
  reorganization; Questrade's net-of-tax dividend and transfer-in
  warnings name the T5/NR4 slip and the ACB in Canada, the 1099-DIV /
  1042-S and cost basis in the US; RBC's rights note and IB's warrant
  note follow suit, and the multi-account warnings say "sheltered
  account". A US `.sum` names Form 8949 and basis (not Schedule 3 /
  ACB), the audit's rounding note names the country's form, the books'
  TRANSFER and short-history notes say wash-sale walk / basis /
  retirement accounts, the run's crypto-sends note offers only self /
  payment, and `taxjson carryover` names the IRS Schedule D carryover
  (and not the Canadian option settings) instead of CRA.
- The same export file under two accounts' `inputs/` folders is an
  ATTENTION line naming both files, for every broker and `.tt`; `run
  --strict` stops on it and on one broker account feeding two taxjson
  accounts (re-audit A2-0366).
- Questrade / RBC: an option description whose strike is only partly
  readable ('2,50' read as 2, '1,0000' as 1000) is refused, and a
  Questrade row with fewer cells than the header is refused instead of
  booked with blank trailing cells (re-audit A2-1041 and A2-1042, the
  Questrade / RBC halves).
- Questrade / RBC: the stock leg of an option assignment on a class
  share (SAMPLD.B under the Montreal root SAMPLD, SAMPLC.B under SAMPLCB) is booked
  as the stock; it was refused as a contract on another underlying or
  a 100x gross mismatch (re-audit A2-1059).
- **Questrade / RBC: payments in lieu of a dividend.** A Questrade
  'SUBST PAY ... IN LIEU OF DIVIDEND' row and an RBC 'CASH / PAYMENT IN
  LIEU OF DIVIDEND' row were booked as dividends: in Canada a PIL on a
  US issuer got a foreign tax credit nobody withheld, and in a US
  project it was a qualified dividend. They are payments in lieu paid
  by a Canadian dealer now: Canada deems one on a Canadian issuer a
  dividend (s.260) and keeps the rest ordinary income; the US keeps all
  of them ordinary (tax-logic CA-INC-03 names the dealers; US-INC-01)
  (re-audit A2-0098).
- **Concatenated exports keep their same-day order.** Two newest-first
  Questrade or generic exports joined with the header repeated, and RBC
  exports joined without one, were read top-down as a whole (the dates
  go both ways), so a same-day sale replayed after its rebuy and the
  superficial-loss denial changed. The order is now decided per segment
  (per header, or per run of dates for RBC); the generic importer no
  longer reports the repeated header as an UNBOOKED row (re-audit
  A2-0100, A2-1046, A2-1084).
- **RBC / Questrade: a dividend-reinvestment row must fit units x price.**
  A REI row whose cash was 10x its units at the REINV@ price was booked
  as the units' cost with only a schema note; the trade path refuses
  the same numbers. It is refused now when the price is in the row's
  currency; a REINV@ marker in the other currency (C$ on a USD row, U$
  on a CAD row, as real exports carry) is not judged (re-audit
  A2-0268).
- **RBC / Questrade / Webull / Coinbase: a Buy row signed as a sale is
  refused.** An RBC Buy with a negative Quantity was booked as a sale
  with negative proceeds (a sign swing under `--strict`); Questrade
  flipped it to a buy silently; a Webull BUY whose quantity and cash
  both said sale, and a Coinbase Buy carrying Coinbase's own sale
  signature (negative quantity and total), were booked as purchases.
  Each is now refused with the row named, as the generic importer does
  (re-audit A2-0097, A2-0287, A2-1025, A2-1057).
- **RBC / Questrade: book-cost adjustment rows.** An RBC 'Return of
  Capital' row lowers the ACB whatever its description words (a
  'ROC ADJUSTMENT TO BOOK COST' raised it); a Return of Capital row
  describing a NOTIONAL distribution is refused; a $0 adjustment books
  nothing (it booked a 0.00 ADJUST under two contradicting lines); the
  year-end ROC book-cost row is an ATTENTION that the same dollars are
  usually already in the income totals (take income and box 42 from
  the T3). A Questrade zero-cash row stating a book-cost adjustment is
  an UNBOOKED warning, not an 'informational' row (re-audit A2-0094,
  A2-0273, A2-1047, A2-1062).
- **Questrade / RBC: reversals pair across all of an account's exports.**
  A Questrade stock-dividend, cash-in-lieu or DRIP reversal (and an RBC
  REI CANCEL) cancelled its original only inside its own file: with an
  overlapping older download the original came back as phantom shares
  (H1 + full-year exports booked 1,250 shares for 1,100), and an
  original in last year's export refused the whole account. The
  pairing is now planned over every export of the account, counting
  overlap copies once; a reversal cancels the latest original on or
  before its date (a later identical stock dividend survives), a
  reversed stock dividend no longer prints its 'booked' note, and an
  RBC file whose rows all live in the account's other export is no
  longer a '0 transactions' warning that failed `run --strict`
  (re-audit A2-0026, A2-0269, A2-0280, A2-0281, A2-0616, A2-1044,
  A2-1048, A2-1055, A2-1060, A2-1063).
- **Questrade / RBC: one export holding several broker accounts.** A
  Questrade file whose Account Type puts a registered plan's rows in a
  taxable account (or a taxable account's rows in a registered one) is
  refused — its TFSA trades were booked as taxable gains with rc 0.
  Any other multi-account Questrade file, and an RBC file holding rows
  of several RBC accounts (RBC writes no account type), is an ATTENTION
  line: every row goes to one taxjson account (re-audit A2-0025).
- **RBC / Questrade / Webull: money-affecting parser warnings reach the
  run console.** A guessed listing for a USD return of capital on an
  untraded symbol (now suggesting `TOBASE`, which works, instead of
  `GLOBAL`, which stopped the run), an RBC temporary code assumed to be
  the receipt's ticker, a ticker change without a reorganization row
  (now also when the new symbol opens with a buy and then goes short,
  with the `GLOBAL` line on the first line), an RBC notional
  distribution whose income is left to the T3, a Questrade internal
  code, a dividend booked net of non-resident tax and a transfer-in
  with no book value are `ATTENTION` lines now; they sat in the .sum
  only. In a Canada project the $0-cost stock-dividend note is an
  ATTENTION line too, and `taxjson run` echoes an echoed warning's
  indented continuation lines (re-audit A2-0005, A2-0007, A2-0027,
  A2-0096, A2-0099, A2-0265, A2-0270, A2-0276, A2-0279, A2-0282,
  A2-0283, A2-0612, A2-0613).
- A Questrade or RBC share buy at $0 price and $0 cash (almost always a
  transfer or journal row booked with no cost) is flagged ATTENTION
  (audit A2-0619; Webull refuses it, the generic importer already did).
- Parse checks (audit A2-0104, A2-0110, A2-0109, A2-0632, A2-0633):
  a broker-printed settle date more than 7 days after the trade is
  booked as printed but flagged ATTENTION (CA-DATE-03 / US-DATE-04); an
  export cut inside a quoted last cell is refused, and one that ends
  without a line break on a number is flagged; one security-override
  line that rewrites two different raw symbols (IB `LEN` and `LEN B`)
  is flagged; a decimal-comma option strike (`2,50`, `1,0000`) and a
  stacked currency sign (`$€5`) are refused instead of misread.
- A file whose rows are all recognized non-events (a deposit-only RBC
  file, a Questrade FX conversion, a Kraken Earn allocation) prints
  `0 tax objects (N recognized non-event row(s))` instead of the
  `parsed to 0 transactions` warning that failed `run --strict`
  (A2-0301, A2-0303); a Kraken Hybrid Earn move is no longer counted as
  a possible taxable send (A2-1078); security overrides never rewrite a
  `/` or `\` futures row (A2-1092).
- Dedup per broker account (audit A2-0008, A2-0625, A2-0296, A2-1085,
  A2-0286): every parser that reads the broker account (IB, Questrade
  `Account #`, RBC `Account`) stamps each row with it, hashed
  (`source_account`); cross-file dedup in the books, `taxjson-sort
  --dedup` (which now also reads the parse's per-file accounts, A2-0297)
  and the fee report never collapses rows of two different accounts —
  two accounts holding the same ETF no longer lose half the
  distributions.
- Dedup no longer depends on file order (A2-0105, A2-0624): a `.tt` line
  equal to an exported row stands for one exported row, so two accounts'
  identical fill plus a matching `.tt` line book two rows in any order,
  and two `.tt` files plus one export book two.
- A `.tt` line that repeats an exported row by hand (a different id, so
  both are booked) now prints an ATTENTION line (A2-0295); two exports
  that disagree on the dates both cover name the rows only one holds — a
  restated IB statement next to its older vintage (A2-0108).
- One broker account's export placed in two taxjson accounts prints an
  ATTENTION line naming both (A2-0293, A2-0630).
- UTF-16 exports: the Webull, Kraken and Coinbase parsers and the IB
  corporate-actions reader decode them like the IB/Questrade/RBC parsers
  (no more false 'not UTF-8 or UTF-16 text') (A2-0101, A2-1064, A2-1066,
  A2-1451).
- **Cross-file dedup no longer deletes separate trades.** Every parsed
  row now records its input file. Identical rows in two `.tt` files, or
  in IB statements of two different broker accounts, are both booked;
  before, the second was dropped as a duplicate, which left a phantom
  short and a wrong gain. The same row in two overlapping exports is
  still booked once. When the files' overlap is too thin to tell, the
  row is booked once and an `ATTENTION: dedup` line names both files.
  `fees-sum` applies the same rule, so its trade count and commissions
  agree with the books (audit R1-296, S031-02).
- **A trade whose money does not match qty x price is an ATTENTION line
  on the console.** When the parser does not declare the contract
  multiplier (Webull, RBC, crypto, a Questrade/RBC dividend
  reinvestment priced `REINV@U$` on a CAD row), the schema's notional
  warning used to sit only in the .sum DIAGNOSTICS: a 10x Proceeds was
  booked with rc 0 and nothing on screen. `taxjson run` now prints it
  as `warning: ATTENTION: schema: ...` (still not an error: the booked
  money is the row's net amount) (audit S065-12).
- **Cross-listing lint (`crosslistings.rpt`).** Reads ticker.map with the
  engine's parser (`DISTINCT` pairs are OK, keywords in any case, an
  unreadable map fails), nets share positions through splits, and counts
  option rows toward their underlying's listing (audit R1-144, S035-02,
  S035-03).
- **Broker detection and `taxjson-brokerage`.** An IB file is routed by
  its section,Header shape (a Trades-first Flex download, a statement
  without the BrokerName row or with a Title row first), and `taxjson
  fetch` accepts exactly what detection routes; an RBC header after a
  partial preamble or a blank line is found. A file in a legacy encoding
  (cp1252) gets one line naming the remedy, as do RBC, Kraken and
  Coinbase refusals (they were tracebacks); an unknown `--brokerage` is
  a usage error (exit 2). An alias (`--brokerage qt`) is recorded under
  the canonical id, so fees.rpt shows one row per broker. An account id
  in an input file's NAME (IB's default `U1234567_....csv`) is masked
  in every parse line, so it no longer reaches reports/. `taxjson
  transfers` shows an in-book RBC transfer's BOOK VALUE.
- **Shared broker helpers.** The back-computed fee (Webull, RBC) is
  signed by the trade's direction — rounding noise is no longer turned
  into a charge — and a flat commission on a cheap option fill is kept
  (it was zeroed above 25% of net); fees.rpt only. "NOT A RETURN OF
  CAPITAL", "RETURN OF CAPITAL GAINS" and a fund named "... RET OF
  CAPITAL ETF" are dividends, not ACB reductions. Strict number cells
  refuse non-ASCII digits and overflow; a lower-case currency is the
  same currency (Questrade, the shared suffix helper); a decimal comma
  inside description text ("ON 12,5 SHS", "BOOK VALUE $1234,56",
  "$1,250.00 PER SHARE") is never read 10-100x off (the field stays
  unknown, or the grouped number is read whole). Webull's settle-to-
  trade walk-back is right on the T+1 cutover days themselves.
- **Questrade / RBC: a settlement date before the trade date is
  refused.** A garbled Settlement Date cell moved a sale into the prior
  tax year silently. tax-logic CA-DATE-03 / US-DATE-04.
- **Questrade / RBC: numbers inside descriptions take thousands commas
  only.** A decimal comma ('STK SPLIT ON 1,5 SHS', 'ADJUSTMENT TO BOOK
  COST $1,16', 'BOOK VALUE 1234,56') is refused instead of read 10x-100x
  too large; a DRIP price 'REINV@C$1,234.56' reads 1234.56 (was 1).
- **Broker detection: prefixes, then content, then venue words.** A
  `generic_`/`cb_`/`kr_` prefix now always wins, and an IB, Questrade or
  Webull export is recognised by its content even when its name mentions
  coinbase or kraken; `generic_kraken_export.csv` and
  `kr_trades_moved_from_coinbase.csv` went to the wrong crypto parser
  (0 rows, exit 0), and an IB export named after Kraken Robotics was
  refused as crypto data. The crypto/equity refusal names the files
  (audit R1-127, S044-01, S044-02).
- **Comma-grouped numbers read right.** `0,125` is refused as a decimal
  comma everywhere (it read as 125); an option strike written `5,000.00`
  keeps its thousands (it was cut to strike 5); an IB `Split 1 for
  1,000` is 1-for-1000 (audit S055-08, R1-170, S058-19).
- **UTF-16 exports are routed.** Broker detection decodes a UTF-16 BOM
  the way the IB, Questrade and RBC parsers do (audit R1-70).
- **Security overrides are stricter and reach every row.**
  `ticker_extraction_overrides.txt` keys now match whole words (on IB,
  where the description is the bare ticker, `BN` no longer rewrites
  ABNB or BNTX), never rewrite option or futures rows, take the currency
  case-insensitively, ignore a leading BOM, and a malformed line fails
  the parse naming its line number instead of being skipped. The
  override now runs before TRANSFER rows are set aside (the custody
  sidecar gets the corrected symbol), IB TRANSFER rows carry the
  security (`ACATS (SAMPLF)`) so the override can match them, and an IB
  split of an overridden security stays a split (its `symbol_new`
  follows the rewrite) (audit R1-143, S001-00/01/02, S012-09, S027-01,
  S059-03).
- **Transfer sidecar is de-duplicated across overlapping exports.** A
  re-downloaded or overlapping statement no longer doubles every custody
  row in `taxjson transfers` or moves twice the shares in the holdings
  evidence netting (audit S026-23, S027-00).
- **One spelling per Canadian listing.** Questrade's TSX-Venture, CSE
  and NEO symbols (`VVV.VN`, `CCC.CN`, `SAMPLE.NE`) became `VVV.V`,
  `CCC.CN.TO`, `SAMPLE.NE.TO`, while IB, RBC and Webull book every Canadian
  listing `.TO`; the pools split and a superficial loss across accounts
  was missed. Every Canadian venue is now `ROOT.TO` in every parser, the
  generic importer and the live-position mapping (`yf_ticker.map` still
  aliases a price lookup, e.g. `SAMPLY.TO SAMPLY.V`). `taxjson-lint-
  crosslistings` warns when one root is held under two Canadian
  suffixes (a `.tt` line or a map rule).
- **Decimal commas are refused in the IB diagnostics and slip
  reconciliation too** (open-position, dividend-accrual and slip cells
  were still comma-stripped and read 100x too large).
- **Decimal commas are refused, not read 100x too large.** `12,50` in a
  generic CSV, `-48,24` in a `.tt` line and `0,95` in a Webull cell had
  every comma stripped and were booked as 1250, -4824 and 95. A comma is
  now accepted only as a thousands separator (`1,234.56`); anything else
  stops the import with the file and line.
- **Schema: declared multipliers make the notional check an error, and
  `taxjson run` parses with `--strict`.** IB and Questrade trade rows
  carry their contract multiplier; a net amount far from qty x price x
  multiplier is now a schema error for such rows (the 1/100 guess made
  every futures row a false positive — 61 warnings on the real 2025/2026
  margin statements, now 0). Every real 2025/2026 input parses with zero
  schema errors, so `taxjson run` now passes `--strict`: schema errors
  stop the run instead of scrolling past.
- **Parsers: parentheses are negative.** `clean_number` read "(1,234.56)" <!-- pii-ok -->
  as +1234.56; it is now -1234.56 and it warns on unparseable text.
  Webull (the one real user of parentheses) takes the magnitude itself,
  so its outputs are unchanged.

### Broker parsers: Interactive Brokers

- When a statement cancels (`Ca`) a trade, transfer or cash in lieu
  from an EARLIER statement and rebooks it, the earlier original is
  cancelled and the rebook is booked; before, both disappeared from the
  books (re-audit A2-0886, A2-1559, A2-1560).
- IB: a Dividends or Withholding Tax row on a currency-tagged symbol
  (SAMPLE.CAD) gets the same "booked as a security of its own" warning as
  its trades (re-audit A2-1493).
- IB parser: an AUD/HKD/JPY/SGD/NZD fill on a system with no time-zone
  database is a one-line error saying to install `tzdata` (now a declared
  dependency on Windows), not a ZoneInfoNotFoundError traceback
  (A2-1447).
- IB parser: a Dividends or Withholding Tax row whose Description has
  no leading `TICKER (ISIN)` token is refused naming the file line; it
  was booked on UNKNOWN.US (or a word of the text) and a Canadian
  eligible dividend was estimated as foreign. A withholding row on
  credit interest is booked on CASH, like the interest (A2-0780).
- IB: a CNH (Stock Connect) fill printed in the ET evening takes the
  exchange's next-day trade date, like JPY / HKD / SGD / AUD / NZD.
- An IB Corporate Actions `Ca` is offered only to statements of its own
  broker account (A2-1090), merge2's trade-`Ca` pairing compares the
  broker account (A2-1095), and the TRANSFER-evidence sidecar keeps
  identical custody moves of two accounts (A2-1093, A2-1094).
- **IB: a cancelled execution of a multi-fill order cancels that part
  of the order.** IB lists an order filled 400 + 40 as one 440-share
  Order row; a `Ca` naming the 40-share execution never matched it and
  stayed booked as a phantom 40-share sale (with a false "original in
  none of the inputs" warning). The order is now reduced pro rata to
  400 shares, in the statement and across statements (audit A2-0298).
- **IB: a futures fill at a negative price keeps its money sign.** A
  sale at a negative price (WTI, April 2020) was booked as receiving
  money, so a loss became a gain; the parser now keeps the notional's
  sign, the futures settlement books a buy's cost signed and the schema
  accepts the negative buy (tax-logic CA-FX-04 / US-FUT-01; audit
  A2-0092). The generic importer and `.tt` lines still read the
  magnitude (KNOWN_ISSUES).
- IB: a stock buy at zero cost is an ATTENTION line (it books a $0
  cost; almost always a transfer or journal row — audit A2-0263); an
  option description no form reads (a decimal-comma strike
  `6,85`) is refused instead of becoming a raw symbol or a truncated
  strike (A2-1041); a money row shorter than its section header (a
  truncated export) is refused instead of reading the missing cells as
  blank (A2-1042).
- IB: a dividend or return of capital is re-bound to the listing held
  on its payment date: a TSX buy months after an NYSE-line ROC moved
  the ROC onto the TSX line (an EMPTY-pool s.40(3) gain and a currency
  clash that stopped `taxjson run`), and two overlapping downloads
  booked the same dividend twice (audit A2-0089). A row left on its
  ISIN listing because two listings were held that day gets a note.
- IB: one option contract keeps one root across the account's
  statements, whichever statement is parsed (a re-download naming the
  adjusted root QZD1 split one put series in two, audit A2-0087); the
  ticker-change hint names the old symbol first with the listing
  suffix it is booked under and goes quiet once ticker.map joins the
  two (A2-0611); an assigned adjusted (QZX1) or class-share (SAMPLCB)
  option leg shares its stock leg's settle date (A2-1028).
- IB statement coverage is checked per broker account and against the
  project year: another IB account's statement no longer hides this
  account's missing half-year, an account whose only statement is the
  prior year is reported (and `checklist` inputs-frozen says so), and a
  year downloaded in two statements split at a year-end weekend no
  longer reports the weekend as a gap or the next year as missing. A
  label holding the separate statements of several IB accounts is an
  ATTENTION line (audit A2-0091, A2-0261, A2-0262, A2-0609).
- IB: an open dividend accrual and a statement spanning several IB
  accounts are ATTENTION lines on the run's console (they were only in
  the .sum/.diag; audit A2-0264, A2-0610). Another IB account's posted
  dividend no longer pays this account's accrual (A2-1035, A2-1039). A
  dividend's ex date and share count come from its accrual in the
  previous statement too, and a posting a day after the accrued pay
  date keeps its ex date, so the US §852(b)(7) January-dividend
  warning fires (A2-0601, A2-0602).
- IB: a corporate action follows the security it names. A split whose
  new leg names another ticker (`OLDT ... Split 1 for 10 (NEWT, ...)`)
  moves the pool to it, a cash in lieu naming a delivered security
  sells that security's fraction, and a stock dividend paid in ANOTHER
  security is an `UNBOOKED` line instead of new shares in the parent's
  pool (audit A2-0085, A2-0093, A2-0257, A2-1033). A TSX USD unit's
  split, cash takeover, tender or commission refund stays on SAMPMD.U.TO with
  its trades (audit A2-0086).
- IB: a Corporate Actions row IB cancelled (`Ca`) leaves no trace in
  the parse output: a merger, spin-off or tender journal row leaves the
  non-event tally and the tender note's row count and dates, a
  cancelled cash takeover no longer prints its `NOTE: cash takeover
  booked as a sale`, and a cancelled zero-proceeds cash in lieu no
  longer warns (audit A2-0600, A2-1030, A2-0606, A2-1029, A2-1031,
  A2-1034).
- **IB: overlapping statements of one account no longer resurrect a
  cancelled or refunded row.** A statement pairs a `Ca` cancellation
  with its original (Trades, Transfers, Corporate Actions), folds a
  commission refund into its trade and joins a cash in lieu to its
  split on its own; an older overlapping download that still held the
  unadjusted original kept it through dedup (a cancelled split applied
  twice, a cancelled sale booked, a refunded buy booked twice). The
  same adjustment is now applied to every overlapping statement of the
  same IB account, and a `Ca` whose original is in two other
  statements undoes both (audit A2-0023, A2-0024, A2-0088, A2-0258,
  A2-0259, A2-1038).
- **IB: a commission refund folds into its trade in another statement**
  (a December trade refunded in January), and a refund naming one
  execution of an Order row (`Refund (ABC, -150, ...)` for a -250
  order) or an option trade folds too (tax-logic CA-ACB-COMMREFUND /
  US-BASIS-COMMREFUND; audit A2-0604, A2-0605, A2-1032, A2-1036). A
  cash in lieu paid in the statement after its split joins that split
  (A2-1037). A Corporate Actions `Ca` matches its original's currency
  and asset category too (a CAD leg undid a USD split, A2-0603).
- IB: a Trades clock time with an unpadded hour (`9:45:00`) is zero-padded
  before the session rules compare it — it was read as an overnight fill
  and a Dec 31 morning sale moved into January; an impossible time
  (hour 24+, minute or second 60+) is refused naming the row (audit
  A2-0082, A2-0260, A2-0607).
- IB: fills outside the US regular session take their exchange's trade
  date (tax-logic CA-/US-DATE-SESSION): US-dollar futures and
  futures-option fills in the CME evening session (18:00 ET onward,
  Sunday to Thursday, or on an exchange holiday) and SPX/SPXW/XSP/VIX
  options in Cboe Global Trading Hours (20:15 ET onward) trade on the
  next trading day; ASX options and warrants (not only stocks) and
  HKEX, Tokyo, Singapore and NZX fills are dated in local time; the
  after-midnight part of a US overnight session on an NYSE holiday
  moves to the next trading day. A Sunday-evening futures fill is no
  longer dated Sunday, and a Dec 30 evening futures close settles in
  January under `futures_settle = "next_day"` (audit A2-0252, A2-0253,
  A2-0254, A2-0255, A2-0595, A2-0596, A2-0597, A2-0608, A2-1027).
- IB: a stock or warrant settles in its listing's market, not its quote
  currency: a USD unit listed on the TSX (ZSP.U) on the Canadian
  calendar, and a USD line listed on the LSE (a UCITS ETF) is booked as
  `.L` on the UK T+2 cycle instead of a fictional `.US` security on
  US T+1 (audit A2-0595, A2-0081). `check-dates` notes a Sunday-evening
  GTH index-option fill instead of calling it an ERROR.
- IB corporate actions use the statement parser's listing rule: a
  merger or spin-off of a TSX-listed USD unit (QZAA.U) is booked on
  `.U.TO`, where its trades are, not on a phantom `.US` line (A2-0209,
  A2-0219), and a leg on a currency-tagged IB line (ABC.CAD) is
  warned about with the ticker.map fix (A2-0556).
- **IB: a cancelled (`Ca`) corporate action is no longer offered for
  election** (A2-0018, A2-0019, A2-0020, A2-0068, A2-0069, A2-0212,
  A2-0220, A2-0970, A2-0971): each `Ca` row removes its original (same
  description, negated quantity) in whichever statement of the account
  holds it, for mergers, spin-offs and merger shapes taxjson cannot
  book. Only the corrected rebook is offered; overlapping statement
  vintages no longer let the cancelled original win. The Code cell is
  split on `;`, `,` and spaces as the statement parser does. A
  cross-listing journal IB cancelled and rebooked (on a currency-tagged line)
  no longer needs a manual `ignore`.
- IB corporate-action times are zero-padded before they are compared,
  so a merger takes its earlier leg's time (A2-0984); copies of one
  event from several statements combine the same way whatever the file
  order, with a warning when one broker account's statements disagree
  (A2-0977).
- **IB open/close codes reach the missing-history checks.** IB's Trades
  `Code` (O opening, C closing, C;O both) is kept on each trade. A short
  IB declares (a sale coded O, or C;O that closed the long and opened the
  short) is listed as a real short, not "missing a buy — fix before
  filing", and is never offered as a phantom (a declared real short
  no longer blocks the checklist). A sale coded C with no position in the
  data — a long option bought before the statements — is always flagged
  (options and futures included) with IB's Basis, and `option-boundary`
  and the expired-option warning ask for the missing purchase instead of
  an expiry row. Order-level codes are read per order, not per fill
  (audit S013-00, S058-02, S060-12).
- **IB: a commission refund lowers the trade's cost.** A Commission
  Adjustments row naming its trade ("Refund (ABC, 250, 2024-05-14)")
  is folded into that trade — a refunded purchase commission comes off
  the ACB, a refunded sale commission off the outlays — instead of a
  stand-alone FEE row the gains never saw; one whose trade is not in the
  statement stays a FEE row and is said. tax-logic CA-ACB-COMMREFUND /
  US-BASIS-COMMREFUND.
- **IB statement hardening.** A dividend or fee whose description says
  "Total" (a "Total Return" fund, Nasdaq TotalView) is no longer
  skipped as a subtotal; an unreadable Cash Report total on a reconciled
  line, or an unreadable option/futures multiplier, is refused by name;
  rows of a type IB does not write are counted and said (and a file
  with no Data rows is refused); futures rows must match qty x price x
  multiplier to the cent (no Cash Report line backs them); a lower-case
  currency cell is upper-cased; IB rows carry the instrument's name
  (`security_name`), so the cross-listing lint recognizes an IB-held
  CDR.
- **IB corporate actions.** A cash-in-lieu row with Proceeds 0 books no
  cash (it used to book IB's market Value as proceeds) and warns; a
  fraction folds into the split of its symbol nearest its date within a
  week, in either row order (an unrelated earlier fraction no longer
  skews a later split). A two-leg split on a SHORT position gets the
  text ratio, not its reciprocal (102-for-100 was 0.98). A tender parked
  in one statement and resolved in the account's next one is a quiet
  no-op instead of two warnings, and the cash-tender NOTE no longer
  points at a corp-actions election that cannot exist. A cancelled
  untranslated row no longer stays in the skip count. A symbol with a
  currency tag (ABC.CAD) is warned about in every section — it is a
  pool of its own.
- **IB: payment-in-lieu share counts and the open-accrual warning.** A
  PIL's share count/rate comes from the accrual of the listing that
  paid it (the posting's currency first, a pay date within a week, the
  Po row's rate — no longer the first accrual row seen); an accrual in
  another currency than the cash gives its share count instead of a
  wrong rate. The "accrued but not yet booked" warning pairs each
  posted dividend with ONE accrual (exact pay date first), so another
  week's dividend no longer hides an unpaid one, and a posting in the
  account's other IB statement (the next year's) now counts.
- **IB: overnight-session and ASX fills are dated by the exchange's
  trade date.** IB stamps US Eastern clock time: a US stock or ETF
  filled in the overnight session (20:00 ET onward, Sunday to Thursday
  nights) now trades on the NEXT trading day and settles T+1 from it —
  a Dec 30 20:30 sale trades Dec 31 and settles in January, and a
  Christmas-night fill settles Dec 29 instead of Dec 26. An ASX fill,
  stamped in the ET evening, is dated in Sydney time. The overnight row
  sorts before that day's regular session; the broker's stamp is kept
  as `broker_time`. tax-logic CA-DATE-SESSION / US-DATE-SESSION.
- **IB security identity across statements and issuers.** Income is
  moved to the held listing of its ticker only when the ISINs match (an
  US issuer's dividend no longer lands on a Canadian issuer's `SAMPMC.TO`), and the holding may
  come from the account's other statements (a ROC-only statement no
  longer books a gain on a phantom listing). Option-root aliases are
  learned from all of the account's statements, and after a ticker
  rename the canonical root is the contract's underlying (OLDTKR -> SAMPLE). A
  USD trade of a TSX `.U` unit is `SAMPMD.U.TO`, as RBC books it. One stock
  listed under two symbols (a ticker change) is an ATTENTION line
  naming the `ticker.map` GLOBAL rule that joins them (audit S059-24,
  S060-19, S060-00, S059-15, S059-11, S010-06, S059-13, S060-17).
- **IB corporate actions nobody books are loud.** An unhandled Corporate
  Actions row is now an UNBOOKED warning (console; fatal under `run
  --strict`) that points at a `.tt` booking instead of a manual TRANSFER
  row (dropped in taxable accounts). The same goes for an option or
  futures contract adjustment (it used to become an equity SPLIT on an
  invented symbol), a spin-off debit on a short parent, and a tender
  whose allocation delivers another security (a share-for-share exchange
  offer was reported as a no-op). An IB stock dividend books its shares
  at $0 cost with a console note, like Questrade's; an Options
  Expirations row keeps its own asset category (a futures-option expiry
  closes the `F:` position) (audit S058-16, S058-22, S058-24, S013-06,
  R1-56).
- **IB statements that do not cover the year are reported.** `taxjson
  run` now says on the console when an account's IB statements stop
  before Dec 31 of a finished year (the 2024 statement ending Dec 27) or
  leave a gap between two statements, and when a statement has no Cash
  Report to reconcile against (audit R1-2, R1-195, R1-53).
- **IB rows the parser cannot book are refused, not counted.** A Trades
  row in an asset class without a parser branch (Bonds, Mutual Funds)
  that moves money, and a security Transfers row with a blank Qty, stop
  the parse naming the row. An execution-level `Trade` row is skipped
  only when an `Order` row covers the same symbol and day (a Trade-only
  fill of another symbol was dropped, a Trade row before its Order row
  was doubled); levels that disagree are refused (audit S060-08, R1-55,
  S060-10).
- **IB mergers are never dropped half-way.** A merger whose out-leg and
  in-leg sit in two statements (a year-end event split across yearly
  downloads) or carry Date/Times a day apart was skipped with no warning:
  the old shares stayed and the new ones never arrived. The legs are now
  paired across all of the account's statements and within a week; a
  leg with no partner stops the run as an unsupported event naming it.
  A merger held in both the TSX and NYSE listings becomes one event per
  listing (the second listing's shares were never converted), a merger
  of a short position is refused instead of booked as `OLD -> OLD`, and
  a cross-listing journal listed before its merger is folded once
  instead of also becoming its own election.
- **IB spin-offs go through the spin-off election.** IB `Spinoff` rows
  were always booked as a dividend at IB's value, with no s.86.1 choice
  (and a Canadian parent's tax-deferred spin-off taxed as income). They
  now ask like every other broker's; the default dividend uses IB's
  value without asking for it. Existing IB spin-offs need one election.
- **IB cash takeovers are booked.** `Merged(Acquisition) FOR USD 30.00
  PER SHARE` is a sale at the cash amount (it was left in inventory with
  only a .sum note). Decimal-ratio and class-share (`SAMPLC B`) mergers are
  parsed; a stock-plus-cash merger stops the run by name for manual
  booking instead of vanishing.
- **IB: a cancelled trade (`Ca`) nets out.** A Trades row coded `Ca`
  was booked as an ordinary trade, so a cancel-and-rebook was a phantom
  round trip: a loss sale rebooked a cent higher became two denied
  superficial losses and the remaining shares' ACB was wrong. The
  cancellation now drops out with its original fill; when the original
  is in an earlier statement of the same account, `taxjson-merge2`
  (`taxjson run`) pairs them, and a cancellation whose original is in
  no input stays booked with a warning.
- **IB: Transaction Fees are no longer charged twice.** IB's Trades
  Comm/Fee already includes per-fill levies (UK stamp tax, SEC/FINRA
  fees); the Transaction Fees section only breaks them down (the Cash
  Report shows Commissions + Transaction Fees = the Comm/Fee sum). The
  parser folded the levy into the trade again, so an LSE buy's fee and
  cost basis were overstated by the stamp levy; they now equal IB's own
  Basis. A layout that ever excluded the levy now
  fails the Cash Report check instead of under-booking.
- **IB: commission rebates keep their sign.** A positive Comm/Fee is a
  rebate; abs() booked it as a charge, overstating costs and understating
  proceeds by twice the rebate (seen on real statements in every IB
  account that earns rebates). The
  fee is now negative for a rebate and net_amount is the cash IB moved.
  Questrade's commission is signed the same way (none of its real
  exports carries a rebate).
- **IB and Questrade: missing columns fail the parse.** Every Trades,
  Dividends, Withholding Tax, Interest, Fees, Corporate Actions, Transfers
  and Commission Adjustments column that carries money, quantity, price,
  date or currency (IB), and all 14 Questrade columns, are resolved by
  header name and required; a renamed or missing one used to read as 0
  (every fee or every gross silently gone) or as USD. Money cells parse
  strictly (a decimal comma, text or a blank is an error; parentheses
  and the unicode minus are negative), dates must be ISO, and each trade
  row must add up: |proceeds| = |qty| x price x multiplier (IB's own
  instrument multiplier — CL 1000, ES 50, MET 0.1, SI 5000; a futures
  contract size is never guessed) with a plausible commission, and for
  Questrade Net = Gross + Commission.
- **IB: parsed money reconciles to the Cash Report.** Per currency,
  Dividends, Payment in Lieu, Withholding Tax, Broker Interest, Other
  Fees, Commissions (+ Transaction Fees) and Trades (Sales + Purchase)
  must match IB's own Cash Report within 0.02, or the parse fails naming
  the currency and line. All 17 real Activity Statements reconcile.
- **IB: unknown sections are loud.** Only known metadata sections are
  quiet; an unknown section with money columns (a localized
  "Dividendes", a renamed "Transactions") fails the parse, any other
  warns. A "Realized Summary" report is refused instead of being read
  as activity.
- **IB: a cancelled corporate action is undone.** A `Ca` row now removes
  or adjusts its original split leg, cash in lieu, tender sale, spinoff
  or untranslated merger; a restated split 3:1 -> 2:1 leaves one SPLIT
  2.0 (both were booked). A cancellation whose original is not in the
  statement is a loud skip.
- **IB: option root renames share one symbol.** When IB relists an
  adjusted option under a new root after a corporate action (ABC ->
  ABC1, one contract id in the instrument list), both legs are booked
  under the original root, so the assignment folds the premium; a
  ticker.map QZD1 -> QZD rule becomes a no-op. Monthly futures options
  use their real expiry from the instrument list instead of day 20 of
  the delivery month.
- **IB: smaller fixes.** `ADR;Po` / `ADR;Re` dividend accruals pair by
  token; Warrant and futures-option transfers are booked (they were
  treated as cash); a consolidated statement spanning several accounts
  warns (ids masked); an income row whose ISIN country has no suffix
  mapping warns when no position confirms the assumed .US listing.
- **IB: option expiries settle on the expiry date.** Rows coded `Ep`,
  rows of the Options Expirations section and zero-price closes of an
  option were given T+1 like every trade, so a Dec-31 expiry landed in
  the NEXT tax year on the settle basis; now `date_settle = date` (also
  futures final settlement). A same-contract trade executed on the
  expiry day (0DTE) has its T+1 settle clamped to the expiry so the
  expiry closes the position it opened. Assignment/exercise option legs
  keep T+1 (they must share the stock leg's settle date).
- **IB: "(Return of Capital)" is no longer an ACB reduction when it
  can't be one.** A payment in lieu labeled ROC is income from the share
  borrower (`DIVIDEND_IN_LIEU`); ROC from a non-Canadian issuer (ISIN
  country ≠ CA) is a foreign dividend under ITA s.90(2), noted in the
  description. Canadian-issuer ROC stays an ACB reduction. New
  `[settings] foreign_return_of_capital = "dividend" | "acb"` (default
  `dividend`; `taxjson-brokerage --foreign-roc`) restores the ACB
  treatment for foreign issuers. Income rises, later gains fall, for
  affected holdings.
- **IB: no false "accrued but not booked" warning when IB revises a
  dividend's pay date** between the accrual and its reversal (Po/Re now
  net per ex-date; a posting within a week in any currency matches).

### Broker parsers: Questrade

- Questrade: deposit, contribution and withdrawal rows are recognised
  cash non-events (they were 'unclassified ... needs a new branch'),
  INT rows are booked as interest with their sign (credit interest was
  dropped), and stock-lending income is an UNBOOKED warning (re-audit
  A2-0277, A2-0614).
- Questrade: a CAD-settled US trade (EXCHANGE RATE) carries its
  commission in CAD like its price and net; it stayed USD-sized, so the
  fees report and the Schedule 3 proceeds/outlays split were short by
  the rate (the gain was right) (re-audit A2-0615).
- Questrade: a decimal-comma CNV@ rate ('CNV@ 1,3579', read as 1) or
  cash-in-lieu fraction ('1,5' read as 1, '0,5' dropped) is refused,
  as BOOK VALUE already was; a BRW journal's IN leg pairs only with the
  OUT leg of the same security (two journals on one date swapped their
  costs) (re-audit A2-0278, A2-1058, A2-1061).
- Questrade corporate actions: an UNBOOKED line from the corporate-action
  stage (a DIS chain that nets a removal) is echoed on the console and
  refused by `run --strict`, as the parse stage's are (A2-0211); the
  internal-code hint names the symbol as booked (`X000007.TO`) and goes
  quiet once ticker.map renames it (A2-0966); a spin-off parent held only
  in a start `.tt` can be named with a `GLOBAL <SEC#> <PARENT>` ticker.map
  line, which the warning now suggests (A2-0980).
- **Questrade:** the ADR custody fee binds to its ticker ('300 SHARES
  ABCD'); a row whose cell spans a line break (an unescaped quote) or
  has extra cells is refused; a cash dividend mentioning STOCK SPLIT
  stays a dividend; an REI row with units and no cash is refused (its
  units were dropped); the internal-code warning says it is moot once
  ticker.map maps the code.
- **Questrade keeps a TSX listing traded in USD on `.TO`.** `SAMPLF.U.TO`
  and `XUS.U.TO` bought in USD became `SAMPLF.U.US` / `XUS.U.US`: a pool
  apart from the same units at RBC or IB, a `JOURNAL SAMPLF.U.TO SAMPLF.TO`
  rule that never fired, and a Canadian ETF listed as US property on the
  T1135. A `.TO` symbol now keeps `.TO` whatever the row currency, so the
  API (`SAMPNA.TO`) and web (`.FNV`) spellings of a USD dividend agree; a
  CAD dividend or ROC on a US stock bought from the CAD side (`EXCHANGE
  RATE`) now reaches the `.US` pool instead of a phantom `.TO` one.
- **Questrade learns identities from all of an account's exports.** A
  dividend, ROC, stock dividend, DRIP or cash-in-lieu row under an
  internal code (`X000008`) whose trade sits in last year's export stayed
  on the code, and a ROC there became a capital gain. The
  description map now spans every export of the account; the event
  wording (`STK DIV ON`, `STK SPLIT ON`, `REINV@C$`, `CASH IN LIEU OF`)
  and Interactive Brokers' transfer wording no longer block the match;
  a code nothing resolves is a warning (and a `--lint` finding) with the
  `ticker.map` line to add, and a spinoff chain booked under an internal
  code is flagged at the election step.
- **Questrade flags a ticker change with no corporate-action row.** A
  symbol that stops with shares open while another with the same
  description opens with a sale they cover gets a warning with the
  ready `GLOBAL old new` line (the sale's gain used to drop out).
- **Questrade row shapes.** `BUY`/`buy` and lower-case symbols are read
  like `Buy`; an unknown action that moves shares is an `UNBOOKED`
  warning on the console (fatal under `run --strict`); a `BRW`
  Norbert's-gambit journal between `SAMPLF.TO` and `SAMPLF.U.TO` is booked as
  a TRANSFER pair like RBC's journal legs, carrying the stated book
  value (skipped before, leaving the units on `SAMPLF.TO`), which a
  `JOURNAL SAMPLF.U.TO SAMPLF.TO` rule nets; a negated stock-dividend row cancels its original; a SAMPNB or
  stock-dividend row carrying both shares and cash is refused; a
  CAD-settled US trade's CAD net must match the USD gross at the stated
  rate and keeps its sign; a blank settlement date on an option is T+1;
  a transferred option books under its OCC symbol at 1/100 of the book
  value per share; a stock leg whose description quotes the contract
  books as the stock; a split on a short position scales it up; a
  warrant expiry books on the date in its description; a transfer row
  carries its description, so `--security-overrides` reaches it; the
  no-book-value warning names the remedy that works (a `.tt` BUYSELL).
  A Questrade DIS chain that removes units is an `UNBOOKED` warning.
- **Questrade spin-offs land on the parser's symbols.** A dotted target
  (`ABC.WS`, a class share) was left without its market suffix and a
  Venture `.VN` listing became `ABC.VN.TO`, so the spun-off lot and its
  later sale sat in two pools (a phantom long, a short sale, no gain).
  The extractor now uses the parser's own suffix rule, reads a padded or
  UTF-16 header like the parser (the whole spin-off used to vanish), picks
  an interlisted parent by the listing held on the spin-off date (never
  by file order; ambiguity is refused with a warning), and nets a DIS
  chain whose rows straddle two yearly exports.
- **Questrade spin-off parents are found in any export of the account,**
  and a US parent bought from the CAD side (`EXCHANGE RATE` rows) is
  named with its US listing, as the parser books it. The s.86.1 cost
  reduction used to land on an empty pool and book a phantom gain. RBC
  spin-off parents and merger placeholders get the same all-exports
  lookup.
- **Questrade: an assignment stock leg keeps its cash.** A row whose
  description said ASSIGNMENT or EXERCISE (or ` - EXPIRED`) had its
  price and cash zeroed even when it carried money: an ASN stock leg was
  booked at $0 cost, a stock named "... EXERCISE EQUIPMENT" vanished, a
  sale of expiring rights lost its proceeds. Only zero-cash option legs
  are zeroed now; an EXP row with cash, or a zero-cash ASN/EX row that is
  not an option, is refused.
- **Questrade: CIL / REI reversals cancel.** A negated cash-in-lieu or
  DRIP row was read with abs() and booked as a second sale or buy (cash
  counted twice, phantom shares). The reversal now removes its original;
  a reversal whose original is not in the file is refused.
- **Questrade preferred shares are dotted.** `FTN.PRA.TO` is now
  `FTN.PR.A.TO`, as IB, RBC and Webull spell it, so the pools no longer
  split. An existing `GLOBAL FTN.PRA.TO FTN.PR.A.TO` rule is harmless.
- **Questrade: no first-match symbol rebinding.** Only internal codes
  (S098765) and dotted dividend codes (.BTO -> the traded BTG) are
  rebound to the traded symbol; a real ticker is kept, and a
  description matching several traded symbols is never guessed (older
  RESP exports booked FTN.PR income under FTN.PRA; it now stays FTN.PR
  with a warning to add a ticker.map rule).
- **Questrade: quantity-bearing DIS rows are corporate-action legs.**
  Warrant spinoff / rights legs (+100 / -100 / +100, no cash) were
  counted as "zero-net dividend (informational)"; they are now their own
  category and reported (taxjson-corp-actions books them); any other
  quantity DIS row is a loud skip.
- **Questrade: taxable-account caveats warn.** A dividend marked NON-RES
  TAX WITHHELD is booked at the net (the export has no gross or tax), and
  a transfer-in without TRANSFER BOOK VALUE enters at $0 cost; both now
  warn in taxable accounts (`taxjson-brokerage --account-type`, passed by
  `taxjson run`).
- **Questrade .xlsx exports.** `taxjson-xlsx-to-csv` reads every cell as
  text (dates arrive as ISO text, the ticker NA stays NA, a decimal comma
  is not turned into thousands), and the parser accepts ISO dates
  ("2025-12-31 00:00:00"); a settlement date that is present but
  unparseable is an error instead of a silent T+1 fallback.
- **Questrade, RBC: expired options are booked on the contract's expiry
  date**, settle = date. Both brokers post the expiry on the next
  business day (Friday expiry dated Monday), which moved a Dec-31 expiry
  into the next year; a blank settle column also added T+1 on top. RBC's
  blank-settle fallback no longer returns the raw "January 5, 2028" cell
  as `date_settle` (it now computes an ISO T+1 / era-aware settle).

### Broker parsers: RBC Direct Investing

- RBC parse notes in a US project name Form 1099-DIV, basis and
  §305/§307 for rights, not the fund's T3 (box 21 / 42), the ACB or ITA
  s.15(1)(c); a Canada project's notes are unchanged and the booking is
  the same in both (taxjson-brokerage --country picks the words; none
  given: neutral words) (re-audit A2-0729, A2-0731, A2-0733, A2-0736,
  A2-1254, A2-1309, A2-1313, A2-1314, A2-1315, A2-1321, A2-1344,
  A2-1345, A2-1346, A2-1347).
- RBC: a merger whose removal and receipt sit in two yearly exports is
  left to taxjson-corp-actions (which pairs the legs across the
  account's statements, A2-0214) instead of two UNBOOKED legs that
  made `run --strict` refuse (re-audit A2-0271).
- RBC: a USD row whose name reads as the US-dollar class of a TSX fund
  ('... ETF US DOLLAR UNITS'), other than the built-in one, is an ATTENTION line with
  the `GLOBAL SAMPMD.US SAMPMD.U.TO` map line; it was booked as a US listing
  silently (re-audit A2-1043; RBC's spelling for these is unverified,
  so it is not renamed automatically).
- RBC: a swallowed-row error names the line of the stray quote, not the
  end of the swallowed span; a Taxes row with a blank Symbol is refused
  instead of booked on 'UNKNOWN'; files without an Account column no
  longer say 'NOTHING was de-duplicated' next to the run's dedup line
  (re-audit A2-1045, A2-1050, A2-1051).
- **RBC export coverage is judged per account, over trading days.**
  Another RBC account's later export no longer hides this account's
  missing late December; an export taken on Dec 31 is a note (not an
  ATTENTION with the range 'Jan 1 to Dec 31'), and one taken on the
  last trading day before a weekend year end is not told its weekend
  is missing (re-audit A2-0272, A2-0275, A2-1049).
- **RBC: a CLOSE CONTRACT row the books cannot back is said out loud.**
  RBC re-describes an option between yearly exports (.ABC one year,
  .ABC.B the next; an adjusted .ABC1). With the opening position in a
  `.tt` under the old root, the close was booked as a NEW written (or
  long) option — its premium taxed in full, the real position left
  open — with rc 0 and no warning. The parser now carries RBC's OPEN /
  CLOSE CONTRACT marker (and expiries and assignments as closing) as
  the `open_close` code, and the run console prints an ATTENTION line
  for any option row coded closing that the books cannot back, naming
  the contract held under the related root and the exact `ticker.map`
  GLOBAL line. The expired-option warning points at that line instead
  of a missing expiry row, and `taxjson handoff` accepts a re-described
  root (same expiry, strike, quantity and cost) while failing a `.tt`
  whose root this year's export closes under another spelling
  (re-audit A2-0006, A2-0095, A2-0266, A2-0267).
- RBC corporate actions: a spin-off's parent is the listing held on the
  spin-off date, not any listing the account ever traded (A2-0210); a
  spin-off `REVERSE ENTRY` cancels its posting, so a reversed and
  rebooked spin-off is offered once and no false short warning is
  printed (A2-0213); merger legs split across two statements are paired,
  and a removal with no receipt anywhere now blocks the run as an
  `unsupported` event instead of only warning (A2-0214); options
  adjusted together by a special-dividend XCH pair by strike rank,
  whatever the row order (A2-0222); corporate-action symbols use the
  parser's own canonical listing (FTN.PR.A.TO, not FTN.PRA.TO; A2-0223).
- RBC corporate-action text: a ratio or option strike written with a
  decimal comma (`0,5 NEW = 1 OLD`, `6,4`) is refused instead of read
  from after the comma as 5 or 6 (A2-0972, A2-0976); cash on a
  reorganization leg counts as a return of capital only under RBC's
  `ROC OF C$<amount>` clause, not when the words appear in an issuer
  name or a negation (A2-0559).
- **RBC "as of" stamp checked against the year.** An RBC export taken
  before the tax year ended is an ATTENTION on the console (it cannot
  hold the rest of the year), `checklist` inputs-frozen judges each
  account's latest RBC export instead of the latest row of any broker,
  and a note says when the year's back-dated Dec-31 book-cost
  adjustments may not be posted yet (audit S063-22).
- **RBC: another company's cash in lieu is no longer folded into a
  reorganization.** A CIL row joins an event only by the same ticker or
  the company's full name; half the name in common (ALPHA GOLD vs ALPHA
  RESOURCES) moved the cash into the wrong sale and hid its "NOT booked"
  warning (S072-01).
- **RBC spin-off under a temporary code:** the warning names the symbol
  as booked (`X000006.TO`) with the exact `GLOBAL` line, and stops once
  ticker.map renames it (`taxjson run` passes the map to the corp-actions
  stage) (S072-03).
- **RBC:** an in-kind option transfer uses the contract's OCC symbol; a
  Holdings export is refused by name; a split row with no Quantity says
  so; the spin-off and merger notes no longer cite Canadian law or ask
  for a .tt entry the corp-actions stage already books.
- **RBC books what it used to drop, and says what it cannot book.** A
  stock dividend (`DIS - ... STK DIV ON N SHS`) enters at $0 cost like
  Questrade's; an RBC Dominion Securities "Reinvest @ $p" distribution
  row with units books the income and the purchase; a reversed DRIP
  cancels its original; a reorganization whose legs or cash-in-lieu
  straddle two yearly exports is paired across them; a split on a short
  position scales it up; an option strike with a thousands separator
  (`5,025`) is read whole; a cash-in-lieu reversal nets instead of
  adding proceeds. Rows it cannot book (unclassified, unmatched legs,
  a Transfers row with no quantity) are `UNBOOKED` warnings on the
  console and fatal under `run --strict`.
- **RBC refuses rows it would mis-book.** A trade with a blank Value
  (read as $0 proceeds or cost), a Value that does not fit quantity x
  price (a commission below zero or far above RBC's), a Description with
  an unescaped quote that swallowed the next row, and a listed-ticker row
  whose text names a contract on another underlying are errors. A stock
  leg whose text quotes the assigned contract books as the stock; a
  security named DELIVERY... no longer turns a transfer-in into a
  transfer-out; FRACTYL-style names keep their cash-in-lieu of dividend
  as income; a blank Description falls back to the Symbol Description
  for security overrides; a rights/warrant expiry settles on its date; a
  blank-settle assignment's option leg shares its stock leg's cycle; a
  notional distribution warns that its income is not in the totals.
- **RBC spin-off parents and merger sources resolve to the right pool.**
  The parent took its market suffix from the spun-off shares' row (a TSX
  parent became `.US`), could resolve to a covered call's option code or
  to another company sharing its first word (`BROOKFIELD CORP` →
  Brookfield Renewable), and an interlisted company was resolved by
  file order. A spin-off's s.86.1 ACB reduction then landed on an empty
  pool as a phantom gain. The parent now uses its own listing, options
  never qualify, a fuzzy match needs both names to agree, a merger's
  temporary code prefers the removal's currency, and an ambiguous
  listing is refused with a warning. A spin-off debited from a short
  parent is refused instead of booked as a long buy and a negative
  dividend.
- **RBC reorganizations pair on evidence.** A ratio written with a
  thousands comma (`1 FOR 1,000`, `1 NEW = 1,000 OLD`) was read as
  1-for-1, so the cash-in-lieu row sold almost the whole position. Two
  option contracts adjusted the same day now pair by the closest strike
  (they swapped ACB by file order); a removal no longer pairs with a
  lone unrelated receipt or a half-name match; a company named "ROC ..."
  no longer turns merger cash into a return of capital; an exchange into
  a different company worded `XCH TO` asks for the merger election
  instead of rolling over silently; and a merger of a short position is
  refused loudly instead of renaming the new ticker into the temporary
  code.
- **RBC: one identity across an account's yearly exports.** The parser
  learned a symbol's listing, an option code's contract and a temporary
  reorganization code's company from each file alone. Now all of an
  account's RBC files are read together. A US stock's USD dividend no
  longer lands on the TSX listing that shares its bare ticker (its dividends
  and withholding move from .TO to .US; no gain changes). A TSX stock's USD dividend or return of capital in a
  year with no trades keeps its .TO listing. An option re-described
  between exports (ABC vs ABC.B, XCH-adjusted ABC1) keeps one symbol, so
  its close is no longer booked as a new written option. A name change
  under a temporary code finds the old ticker in an earlier export, and
  warns with the `ticker.map` line when no file names it.
- **RBC: overlapping re-downloads are de-duplicated.** A row's time came
  from its position within the day, so the same trade in two downloads
  of one account got two ids and was booked twice. Rows already in an
  earlier file of the same RBC account are now skipped (identical fills
  on one day are matched by count), with a note; files without an
  Account column are never matched, and a warning says so.
- **RBC: ticker change without a reorganization row.** When one symbol
  stops with shares open and another with the same Symbol Description
  and currency opens with a sale they cover (e.g. ABC to ABD), the
  parser warns and prints the `GLOBAL` line for `ticker.map`.
- **RBC: every reorganization booked as one event.** RBC books name
  changes (NAC), reverse splits (REV), 1-for-1 exchanges and blank "MGR -"
  splits (MGR), MER reorganizations and option adjustments (XCH) as a
  removal under a temporary code plus a receipt. Only "MERGER TO" pairs
  were handled; the rest became $0 buys and sells on RBC's internal codes,
  and "SHRS RECEIVED THRU MERGER" receipts vanished. Each removal is now
  paired with its receipt (same company, within 7 days) and booked as ONE
  SPLIT (renaming when the ticker changed; a 1-for-1 on the same ticker
  books nothing), with MER's "ROC OF C$x" as a return-of-capital ADJUST
  and cash in lieu as the sale of the fractional share. An option XCH
  keeps the same contract (and its ACB). "REVERSE ENTRY" corrections
  cancel their leg. Unmatched legs, and any emitted symbol that is an RBC
  internal code, are warned about (a lint failure). Real mergers still go
  to the election prompt; spin-offs ("DIS - ... SPINOFF") now do too
  (s.86.1 or an FMV dividend in kind) instead of becoming $0 buys; rights
  issued to all shareholders stay a nil-cost acquisition, now noted.
- **RBC: income by code, never by security name.** Rows are classified
  by Activity and the RBC code ("DIV - ", "CASH DIV ON", "DIST ON"); the
  word DIVIDEND inside a name ("SAMPLE DIVIDEND SPLIT CORP", "HIGH DIVIDEND
  ETF") turned in-kind transfers into $0 dividends and dropped their
  shares. An income row that carries shares is refused. Reinvestments
  (REI) are purchases of the units, not negative dividends; "ADJUSTMENT
  TO BOOK COST $x" rows are signed ACB adjustments (notional distribution
  up, return of capital down); ADR fees (FCH) are FEE rows; in-kind
  transfers carry RBC's "BOOK VALUE" as evidence (`book_value`, shown by
  `taxjson transfers`), booked exactly as before.
- **RBC: strict reading.** The header row must carry the real columns
  (a preamble line mentioning "Date" no longer passes); numbers must
  parse ("39 043" and garbage raise, "(12.50)" is negative); each date
  column gets ONE format for the whole file and a file that reads as
  both day/month and month/day raises; currencies are upper-cased; an
  unquoted comma is re-joined only inside the last (Description) column.
  Options described only in "Symbol Description" are recognised, one RBC
  option code is one contract (the first description wins, with a
  warning), known cash rows and footers count as non-events, and an
  unclassified row that moves shares or cash is a loud warning and fails
  `--lint`. Same-day rows keep the export's order (it lists newest
  first; an option assignment's two rows share one time so the premium
  still folds into the stock leg), and the older spelling of that ETF's name maps to its .U.TO class like
  the current one.
- **RBC: split-corp retractions are dispositions.** RBC books an issuer
  retraction as an `Other` row coded `TEN` ("... RETRACTION AT C$x PER
  SHARE") with a blank price; it was skipped as unclassified, so the
  shares never left the books and the proceeds and gain were missing
  (seen on real 2023/2024 exports). Filed numbers change for any year
  with a retraction.

### Broker parsers: Webull

- Webull parser: a row whose Action Code is blank but that carries a
  date, quantity, price or proceeds is refused naming the file line; the
  trade was dropped at rc 0 (A2-0788).
- Webull: a last row cut short ('CAD,12-12-2024,') is refused instead of
  dropped; an unreadable Date is refused by file line; a share row with
  no Price and no Proceeds, and a $0 option row that opens a position,
  are refused instead of booked at $0; DIV/transfer rows are
  `warning: UNBOOKED:` (console echo, `--strict` refuses); the ticker-
  change hint now sees the buy-first shape and renames across yearly
  exports; an inferred assignment's option leg settles with its stock
  leg (CA-DATE-04); a split assignment shape is named; a newest-first
  export is read bottom-up (CA-DATE-14); each row names its broker
  account (preamble Account Number) for cross-file dedup (A2-0028,
  A2-0102, A2-0284, A2-0285, A2-0286, A2-0288, A2-0289, A2-0290,
  A2-0617, A2-0618, A2-0619, A2-1065, A2-1067, A2-1068, A2-1069,
  A2-1070, A2-1071).
- **Webull: a sale's debit keeps its sign.** A close at $0.00 whose
  commission was charged ("(1.50)" in Proceeds) books -1.50 proceeds,
  not +1.50 received. The row's
  Type Code decides option vs shares (an OPC row with an unreadable
  description, or an SHS row whose description reads as a contract, is
  refused). The skip warning names only the skipped action codes;
  exercise/assignment messages cite s.49(3) for a call, s.49(3.1) for a
  put.
- **Webull: a Proceeds cell that does not fit its trade is refused.** The
  gap between Proceeds and quantity x price is the commission; one that is
  negative or far beyond commission size (a shifted or mislabelled
  column) now stops the parse naming the line, as Questrade, IB and RBC
  already do — it used to book with only a schema warning (S023-19).
- **Webull: exercise/assignment is inferred only on evidence.** A $0 option
  close is paired with a stock trade at the strike only when that trade
  carries Webull's $1.00 exercise/assignment charge; a limit order at the
  strike after a worthless expiry keeps the expiry's gain or loss (it used
  to fold silently into the shares' cost). Every inferred pair is named on
  stderr, a rejected candidate is a warning, the closest option wins
  whatever the row order, the row's own `@Symbol` names the underlying,
  and a Dec-31 assignment settling in January pairs across the two
  yearly exports (R1-15, R1-94, R1-175, S066-12, S066-02, S065-24).
- **Webull: no trade row is dropped silently.** A BUY/SELL row with a
  blank or unknown currency, or a blank Date, is refused naming the line;
  `usd`/`Sell` spellings are read; a header with two columns matching one
  field (an inserted `Gross Proceeds`) is refused; a cell spanning lines
  (an unescaped quote that swallowed the next rows) is refused. Two
  symbols sharing one Security Description, the new one opening with a
  sale, are named as a likely ticker change with the `ticker.map` line to
  add (R1-92, R1-97, R1-98, S066-04, S066-10).
- **Webull: a priced trade needs its Proceeds.** A blank, garbage or
  misaligned Proceeds cell on a priced BUY/SELL booked $0 (the whole
  gross became a "fee", so even `--strict` passed). Such a row, a row
  wider or narrower than its header, and a decimal comma are refused
  with the file line.
- **Webull: columns by header label, both export layouts pinned.** The
  Trading Summary's 2024 layout has 9 columns (Proceeds in column 8), the
  2025 layout 10 (an empty column 8). The parser read by position with a
  column-9-then-8 fallback; it now resolves every column from the header
  labels, refuses an unrecognised layout instead of guessing, and fails
  on any row-accounting mismatch. Tests pin the amounts of both layouts.
  (A different, older flow read the 2024 layout by position and booked
  Webull purchases at $0 cost on a filed return.)
- **Webull: option assignment and exercise detected.** The Trading
  Summary shows an assignment or exercise only as a $0 option close plus
  a stock trade at the strike. Both legs are now marked ASSIGN when the
  stock trade matches in quantity, direction and price within a few
  days, so the premium folds into the shares' cost (s.49(3)) instead of
  being realized as an expiry; seen on real exports (put assignments
  and a call exercise).
- **Webull: expiry rows are no longer shifted a business day earlier.**
  The Date column is the settlement date for trades but the EXPIRY date
  for zero-price expiry rows; walking it back put a 0DTE long's expiry
  before its buy, booking a phantom $0 short WRITE. The expiry now
  closes the long (direction LONG, loss = premium); gain totals are
  unchanged.

### Generic importer and .tt files

- `.tt` files: a symbol with no market suffix (SAMPLH for SAMPLH.US) on a
  line of an account that is not `crypto = true` is now warned about in
  the run diagnostics like an unknown suffix — it is its own ACB pool and
  the broker's rows for the real listing go short (A2-0777).
- Generic importer: a mapping .toml that is not UTF-8 is reported against
  the .toml, not as the CSV being unreadable (A2-1452).
- The generic importer is no longer documented as a crypto route: the
  README, the mapping template and the importer no longer suggest
  `settle_on_trade_date = true` for a crypto-only export (the coins were
  booked as SAMPMA.US / SAMPLT.TO shares — Schedule 3 line 4, and §1091 in a
  US project); coins go in a `crypto = true` account, and the importer
  says so when the option is set.
- Two .tt files (or generic files) whose names differ only in an
  account-number token (manual_55500001.tt / manual_55500002.tt) are two
  sources again: dedup read both as `manual_55***.tt`, one file
  repeating itself, and silently dropped one file's identical line. A
  masked name now carries a short hash of the real name (`source_key`,
  never the name itself) for dedup only (audit A2-0159).
- A `.tt` SPLIT line carries no currency (it was labelled CAD, and a US
  project with no CAD rates refused the account); a split row with no
  money is relabelled, not converted.
- **Generic importer fixes (re-audit 2).** A cut-off last record (fewer
  cells than the header, a final separator with no line break, a cut
  currency code) is refused instead of completed from `[defaults]` (a USD
  trade was booked as a CAD `.TO` security). Futures spelled `/` or `\`
  are `F:` futures and settle on the trade date (they took the equity T+1
  and a Dec-31 close moved a year), and `futures_settle = "next_day"` now
  reaches the generic importer. An option closed at $0 on its expiry day
  settles that day (CA-DATE-08). A commission rebate lowers the cost and
  raises the proceeds and is booked as a negative fee (it was a charge).
  A buy row with a cash-in amount in a cash-signed file is refused (a
  sale under one action mapped to buy). A dangling sidecar mapping, a
  non-string `[defaults]`/`[formats]` value and a mapped settle date more
  than 31 days late are refused (more than 7 days: ATTENTION). New
  `dividend_in_lieu` target; UTF-16 exports are read (also by
  `taxjson-generate-parser`); a $0 option close beside a stock trade at
  the strike is named as a possible exercise/assignment (ATTENTION).
  Optional `[columns] account` / `[broker] account` name each row's broker
  account (A2-0030, A2-0103, A2-0106, A2-0107, A2-0299, A2-0626, A2-0628,
  A2-0629, A2-1075, A2-1076, A2-1079, A2-1080, A2-1081, A2-1083,
  A2-1085).
- **.tt lines spell Canadian listings like the broker parsers.** A .tt
  `ABC.V` (on a CAD line), `ABC.VN`, `ABC.CN`, `ABC.NE` or `FTN.PRA.TO`
  is now `ABC.TO` / `FTN.PR.A.TO`: it used to be its own ACB pool, so a
  loss sold through a .tt file and the broker's repurchase of `ABC.TO`
  were never linked as identical property. `.VN` is a Canadian venue to
  the schema, T1135 (no more '??' REVIEW) and the price chain too, and
  the venue-split lint catches `.VN` and undotted preferred series
  (tax-logic CA-ACB-04 / US-BASIS-06; audit A2-0300, A2-0635, A2-1077).
- **.tt keeps income facts and full precision.** A DIVIDEND /
  DIVIDEND_IN_LIEU / TAX / ADJUST line may end with `record=`, `ex=`,
  `label=`, `dealer=`, `issuer=` and (ADJUST) `type=roc` tokens, and
  `taxjson-convert-tt book.json` writes them: the round trip used to
  move a December-record distribution and its ROC to the pay year, and
  turn a Canadian dealer's payment in lieu (an s.260 deemed dividend)
  into other income, with no word. Quantities and prices keep every
  digit (a 10-decimal coin quantity was cut to 8, changing the row's
  id). A .tt file whose last line has no line end warns that it may be
  cut short (audit A2-0291, A2-0631, A2-1072, A2-1086).
- **.tt: a sale whose commission exceeds its gross keeps its negative
  proceeds.** A .tt SELL line may now carry the negative total
  qty×price − commission (a penny option close: `-8.95` with a 9.95
  commission) when its own commission explains it; any other negative
  sell total is still refused. The old advice (`enter 0`) left the
  excess commission out of the loss and now warns. `taxjson-convert-tt`
  json→tt and the `taxjson events`/`trades` single-account view wrote
  that negative total already, so their output re-imports again, and the
  view now carries a declared contract size (`x1000`, `x10`) like
  convert-tt (audit A2-0292, A2-0620, A2-0621, A2-0622, A2-0623,
  A2-1073, A2-1226, A2-1227).
- `taxjson events` lines keep a dividend's withholding-netted amount
  (the optional 9th column) and a declared TRANSFER's `DECLARED` token,
  as `.tt` export does, so a pasted line re-imports the same row
  (A2-0989).
- **Generic importer: name the real broker.** A mapping can set
  `[broker] name = "wealthsimple"`. `taxjson run` then parses each named
  broker's generic files on their own and records them as
  `generic:<name>`, so `fees-sum` gives each broker its own row. A
  broker whose fees come in only through a named generic file is no
  longer listed as having "NO fees". One `taxjson-brokerage` call
  refuses files whose mappings name different brokers (audit S027-05).
- **`taxjson-fees-sum`.** `--year` help names the trade date; `--since`
  must be YYYY-MM-DD; a row without an account is `<broker>/?`; fees in a
  hand-entered `.tt` no longer leave the footer claiming the other
  brokers had NO fees (audit R1-101, R1-154, R1-289, S031-03, S031-06).
- **Generic importer.** An explicit 0 in the amount cell of a priced
  trade is refused (it was silently replaced by qty x price); the
  fee-share refusal prints the share to 2 decimals and the 5% limit.
- **`.tt` converter.** A file with a byte-order mark converts; json->tt
  and tt->json write atomically; a row with no currency is refused (it
  was written as CAD); currencies are upper-cased; a sale entered with
  total 0 because the commission exceeded the gross no longer warns
  "check for a typo". `taxjson audit` names the `.tt` file of a
  hand-entered row.
- **`taxjson-convert-tt` writes the project's tax_date.** Converting a
  book to .tt defaulted to the settlement date (the Canadian rule) with
  no country, so a US book's Dec-31 sale came back as a January one. It
  now reads the project's tax_date when the file sits in a project, and
  outside one asks for `--date-basis` when a row's two dates differ.
- **`taxjson trades` prints signed totals and fees.** A fee rebate printed
  as a charge and a penny close's negative proceeds as positive, so the
  single-account taxtext did not round-trip; TOTAL SELL and `trades-sum`
  sold now net those proceeds signed (audit S039-11).
- **Generic importer: a sell whose commission exceeds its gross nets
  negative.** A penny option close with a larger commission is booked with
  its negative net (the schema accepts it since S017-00; the engine deducts
  it) — it used to be clamped to 0 without an amount column, losing the
  excess commission, or refused as a mis-mapped column with one (S057-02).
- **`.tt` lines: canonical symbols and fewer false alarms.** A symbol is
  upper-cased on read (`aapl.us` was its own pool and the broker's sale
  went short with no gain), and a suffix that is not a market (`SAMPLE.TSX`,
  `SAMPLE.CA`) is a warning naming the line. Futures totals are no longer
  called typos (the 1/100 contract-size guess), two identical `ACQUIRED`
  lots arriving the same day no longer collapse into one arrival leg (a
  phantom 100 shares, or a refused `AmbiguousTransferDateError`), and a
  hand-entered split of a USD stock no longer skips the holdings refresh
  with a "cross-currency rollover" message.
- **json → .tt keeps the year a sale settles in.** `taxjson-convert-tt`
  (json to tt) and the single-account `taxjson events` view wrote the
  trade date; a `.tt` line has one date, read as the settlement date too,
  so a Dec-31 sale settling in January moved into the earlier year on
  re-import. They now write the settlement date on a settle-basis project
  (`--date-basis trade` for the converter), count the rows whose dates
  differ, keep a Questrade-style `commission` as the fee, and warn about a
  contract multiplier the format cannot carry.
- **Generic importer: fewer ways to book wrong money quietly.** Symbols
  are upper-cased (`sample` and `SAMPLE` used to be two pools, the sale a
  phantom short). An unmapped action that carries a quantity or an amount
  (a DRIP reinvest, say) is an `UNBOOKED` warning — on the console, fatal
  under `run --strict`, a `--lint` failure — instead of a note in the
  report. A row with no price is still checked against its amount (a
  swapped fee/amount mapping booked the commission as the cost), a
  mapping with no fee column infers the commission from the net instead
  of refusing a valid commission-inclusive export, a futures symbol needs
  its amount (the contract size is never guessed as 1 or 100), and an
  unescaped quote that swallows the next row stops the import naming the
  line.
- **Standalone fee rows have one sign: positive = charged.** The generic
  importer's `fee` action booked a CSV's negative cash as a negative fee
  (the opposite of IB, Questrade and RBC, so `fx-cash` read a charge as
  cash received); `[formats] fee_sign` picks the CSV's convention. The
  `taxjson fees` view flipped every IB/Questrade/RBC fee row into a
  rebate and understated TOTAL FEES; it now reads them as charged.
- **Generic importer: one spelling per security.** `SAMPLC-B` and `SAMPLC/B`
  are `SAMPLC.B` (they were separate ACB pools, so a cross-account
  superficial loss was missed), an OCC symbol padded to 21 characters is
  compacted, and an option description in the symbol column is refused
  instead of booked as a share that never expires (audit S010-04).
- **`.tt`: a negative sell total is refused.** `BUYSELL … -100 CAD 20
  -2000` was booked as -2,000 proceeds (a +1,000 gain became a -3,000
  loss). The total is the positive net proceeds; the error names the
  file and line.
- **Generic importer: strict mapping.** An unknown section or key
  (`ammount`, `commission`, `[format]`, `tax_sgn`) is refused with a
  suggestion instead of being ignored; dividend/tax/interest/fee actions
  require a mapped `amount`, and a blank amount on such a row is refused
  (it booked 0).
- **Generic importer: trade rows must add up.** A buy/sell with neither a
  price nor an amount (the fee became the whole cost), with no quantity,
  or a stock buy at zero cost is refused; so is a quantity/amount sign
  that contradicts the mapped action (a sell under an action mapped to
  `buy` was booked as a buy).
- **Generic importer keeps an explicit exchange suffix.** `SAMPLF.U.TO`
  bought in USD became `SAMPLF.U.US`, a different security, so a
  superficial loss across accounts was missed. A bare symbol still takes
  its suffix from the row currency.
- **Generic importer settles trades on the settlement date.** Every row
  was booked with the trade date as its settlement date, so a Dec-31 sale
  landed in the wrong tax year. A new optional `settle` column is used
  when mapped; otherwise buy/sell rows get the standard holiday-aware
  cycle (T+1/T+2/T+3 by era, options T+1) on the listing's market.
  `[options] settle_on_trade_date = true` keeps the trade date (crypto).
- **A .tt file named after a broker no longer erases that broker's
  trades.** `questrade.tt` (or `webull.tt`, `ib.tt`, `generic.tt` ...)
  wrote its converted JSON over the broker's parse in `work/`, so every
  trade in that broker's CSVs vanished with exit 0. Converted .tt files
  now live at `work/<account>_tt_<stem>.json`; the first run after
  upgrading removes the old `<account>_<stem>.json` copies as stale.
- **Generic importer refuses mis-mapped columns.** Two fields on one
  header, |amount| ≠ qty × price (× 100 for options) ± fee beyond 1%, and
  a fee above 5% of the gross (unless `[options] allow_large_fees =
  true`) now stop the import; an amount equal to qty × price while the
  fee is nonzero warns that the GROSS column is probably mapped as
  `amount`. The currency must be mapped or set in `[defaults]` — there is
  no implicit USD any more (the shipped example already sets it).
- **`.tt` files: unknown actions are errors.** A line starting with
  `SELL`, `buysell`, `BUYSEL` or `Dividend` was skipped silently with
  exit 0; it now fails with the valid actions and a did-you-mean. Dates
  must be YYYY-MM-DD and times HH:MM:SS; inline `# …` comments are
  stripped (a `# DECLARED` remark no longer grants attestation); stray
  trailing tokens are errors; warnings and errors name `file.tt:line`.
  All real `.tt` files convert byte-identically.
- **.tt validation: option lines are checked against qty × price × 100**
  — every option row used to warn "differs from qty*price".
- **A `.tt` file whose name ends in a pipeline suffix is refused**
  (`msft_gains.tt` created a phantom account in `sum` and counted its fees
  twice); rename it, e.g. `msft-gains.tt`.

### Crypto

- Inside a project with no [settings] local_timezone, crypto rows are
  dated in the default zone tax-logic names: the TAXJSON_LOCAL_TZ
  environment variable applies only outside a project, as documented,
  and a project run says so when it is set (re-audit A2-0165).
- tax-logic states the crypto rules the parsers and the US engine apply:
  Coinbase's ETH2 is ETH (a Convert between them is not a sale), a
  Kraken dust sweep splits its receipt over the coins by amountusd or
  equally, and the US engine counts under 1e-08 units as zero — a lot
  residue that small is folded into the sale that closes the lot and a
  sale's excess that small opens no position, each now named in a
  warning (CA-CRYPTO-10/11, US-CRYPTO-06/07/08; re-audit A2-0479,
  A2-0815, A2-1469, A2-0808, A2-0818, A2-1471, A2-1484, A2-1485).
- Crypto sends in a US project: `taxjson run`'s note, the parse NOTE in
  the crypto .sum, and `crypto-sends`' hints, listing and decision error
  no longer say a gift is a disposition or offer `gift` (refused there):
  a payment is a sale, a gift is not a sale for a US donor (US-SEND-02;
  audit A2-0721, A2-0740, A2-1283, A2-1285, A2-1286, A2-1329).
- `taxjson run` stops when sends.json cannot be read while a
  crypto_sends.tt generated from earlier decisions exists: it used to
  book the old file with a warning (a gift since changed to self, or a
  gift a US project refuses), even under `--strict` (audit A2-0415).
- crypto-sends files (re-audit A2-0465, A2-0467, A2-0775, A2-1407,
  A2-1405, A2-1406, A2-1404, A2-1415, A2-0776/A2-1449 for sends.json):
  - The double-booking check now reads a hand-written .tt saved with a
    BOM the way convert-tt does, so a duplicate sale on line 1 is
    flagged.
  - A generated crypto_sends.tt re-saved with a BOM is still recognized
    as generated. It is no longer refused, and it no longer shows as
    OUT OF DATE indefinitely.
  - A transfer sidecar of the wrong shape is reported in one line
    naming the file.
  - An inputs/<acct>/sends.json that is a directory or cannot be read
    is reported in one line. It is no longer read as "no decisions",
    and `--set` no longer leaves sends.json.part behind.
  - sends.json may start with a BOM, and a non-text `note` is refused
    in one line.
  - The error for a bad sends.json no longer suggests deleting the
    file.
- Coinbase, US project (stablecoins are property): a fill priced in a
  stablecoin (PYUSD, USDC ...) whose Notes carry no `on BASE-QUOTE` pair
  no longer folds the stablecoin to USD cash and drops its disposal; the
  row is booked as a crypto-quoted fill, or refused when Notes cannot
  give the quantity.
- Kraken ledger instant trade of a stablecoin for USD away from the peg
  (USDC -> ZUSD at 0.90) now prints the de-peg ATTENTION the trades
  export and Coinbase print, and the forex note names the stablecoin.
- Standalone `taxjson-brokerage` without `--country` books Kraken /
  Coinbase USD stablecoins as property (the neutral answer, as a
  foreign return of capital already defaults to a cost reduction) and
  prints a note naming `--country`; it used to take Canada's US-dollar
  cash model. Its help lists every choice `--country` makes (re-audit
  A2-0742, A2-1238). `taxjson run` always passes the country.
- `taxjson crypto-sends` prices a send, and values the stablecoin pool,
  with a rate from the send's day or the 5 days before it, as the
  conversion stage does; an older rate (a January rate for a June send)
  leaves it unpriced instead (re-audit A2-0414).
- `taxjson crypto-sends`: `--unset ID` removes a saved decision (the send
  is undecided again); `--set`/`--write` refuse while another crypto
  account has not been parsed (a send to it looked unmatched and could be
  booked as a gift), and the checklist step is "blocked" then (re-audit
  A2-0359, A2-1163).
- `run --strict` / checklist run-clean: a crypto send recorded as a gift
  or payment that could not be priced (not booked), a send booked twice
  (a hand-written .tt line next to crypto_sends.tt), an undecided send
  (strict only), and work/ books of a renamed account (counted twice) now
  stop `--strict`; run-clean is "attention" over any UNBOOKED line in a
  report and over the renamed account's books; the crypto-sends step
  flags the double booking and a crypto_sends.tt whose price no longer
  matches sends.json (re-audit A2-0127, A2-0357, A2-0362, A2-1151,
  A2-1165).
- Kraken: a ledger of only fiat deposits and withdrawals is "0 tax
  objects (not tax events)", not the "parsed to 0 transactions" warning
  that `run --strict` refused (re-audit A2-0703).
- `transfers`: a Kraken withdrawal's fee paid in coins shows in the FEE
  column ("0.002_TAO") and as fee_qty / fee_currency in --json; it was
  empty on every real withdrawal (re-audit A2-0663).
- The same exchange export filed under two crypto accounts (rows with
  the same transaction ids) is warned about, and `run --strict` stops,
  instead of booking every trade twice at exit 0 (re-audit A2-0569).
- The stablecoin de-peg warning ("not in the gains; report it by hand")
  is an ATTENTION line, so `taxjson run` shows it on the console, not
  only in the .sum (re-audit A2-1001).
- fill-crypto values PYUSD and GUSD at their 1.00 USD par like USDC
  (a US Coinbase or Kraken PYUSD reward went to Yahoo, and an offline
  run stopped); `run --fast` re-prices after a `work/crypto_ticker.map`
  change, which fill-crypto reads (re-audit A2-1000, A2-0593, A2-0585).
- **Crypto sends: pairing, decisions and prices (re-audit 2).** The
  send/arrival pairing is a minimum-loss assignment, not first come
  first served: a send no longer takes another send's arrival and books
  a phantom network-fee sale; a send that landed as two deposits (or two
  sends as one) pairs; a Kraken Hybrid Earn move is never paired. A
  saved gift/payment that a later arrival pairs with is no longer
  dropped silently: `run` warns, `--strict` stops, the checklist flags
  it, and `crypto-sends --set ID=gift --unpair` keeps it. A full `run`
  parses every crypto account before pairing (a new export pairs on the
  first run; a removed export's sends no longer book); `crypto-sends
  --set` refuses stale evidence. US: a move paired between two crypto
  accounts (basis not carried) is warned about and stops `--strict`.
  `--price` and a hand-edited sends.json price must be finite and at
  least 0.00000001; re-deciding a send drops its old hand price; a
  network fee takes `--set ID-fee=fee --price P`; an unpriceable entry no
  longer holds back the priced ones (it is warned about; `--strict`
  stops). Same-second sends get distinct ids. The stablecoin pool reads
  a Coinbase Convert whichever leg the Asset column names, parses Notes
  numbers strictly and takes deposit fees out. A bad rate in to_base.csv
  is refused; today's open price is not cached; work/crypto_ticker.map
  applies as in fill-crypto; the duplicate-line check catches the UTC
  date, a fee-inclusive or rounded quantity, a thousands comma and a
  split sale.
- **fx-cash: stablecoins, coin legs and holiday settles.** A PYUSD or
  GUSD reward in a Canada book now enters the US-dollar pool, as a USDC
  reward does. fx-cash and the parsers share one stablecoin list. A
  Coinbase Advanced Trade on a crypto-quoted pair (ETH-BTC) and a Kraken
  fee paid in a coin no longer count as US dollars acquired and
  disposed. Rows that settle on the same day are walked in trade-date
  order, so a holiday no longer puts a later buy before an earlier sale.
  tax-logic CA-FX-07 now states the loss side of the $200 exemption and
  the pooled-average-cost method. Re-audit A2-0079, A2-0234, A2-0235,
  A2-0244, A2-0576, A2-0589, A2-1012, A2-1013, A2-1015 and A2-1016.
- **Coinbase rows must add up.** A Buy/Sell whose Total is not
  Subtotal ± fee, a Buy/Sell, Convert or staking reward whose value does
  not fit Quantity × Price (5% for spread), and a Convert whose Quantity
  Transacted disagrees with its Notes are refused, naming the file and
  line. A 10x Subtotal on a Convert used to add about 26.8k to the gain
  under `run --strict`. A sale whose fee exceeds its Subtotal now books
  negative proceeds whatever sign the Total cell carries, and an
  explicit $0.00 Buy is refused like a blank one. Before, fill-crypto
  re-priced it at market. Re-audit A2-0022, A2-0080 (the Coinbase half),
  A2-0250, A2-0565, A2-0997 and A2-1023.
- **Coinbase classification.** A fiat `Withdrawal` is a recognized
  non-event like a fiat `Deposit`. Before, it raised a false UNBOOKED
  "moves coins" warning and `run --strict` failed. A `Deposit` or
  `Subscription` in a coin is now UNBOOKED; it used to be dropped as a
  non-event. A row cut inside Fees or Notes is refused as truncated. The
  unterminated-quote error names the line the quote opened on. The two
  legs of a Convert in an export without an ID column share one id stem,
  so fill-crypto values the swap once. Advanced Trade legs on a
  crypto-quoted pair say "crypto-to-crypto". A USD-valued Convert or
  `*-USDC` Advanced Trade more than 2% off the peg prints the de-peg
  warning. Re-audit A2-0237, A2-0564, A2-0566, A2-0567, A2-1024,
  A2-0249, A2-0584, A2-0998 and A2-1003.
- **Kraken: a stablecoin swap far off the peg is warned about.** A
  ledger instant swap or an ETH/USDC fill whose ledger `amountusd`
  implies a stablecoin price more than 2% from 1.00 USD now prints the
  de-peg warning a USDC/USD fill does (tax-logic CA-CRYPTO-02; re-audit
  A2-1003, Kraken half).
- **tax-logic states the Kraken staked-code fold.** CA-CRYPTO-01 and
  US-CRYPTO-01 now say that Kraken's staked and bonded wallet codes
  (DOT.S, DOT28.S, ETH2, ETH2.S, the .M/.F/.B/.P/.HOLD suffixes) are the
  same coin as the bare code, so a 1:1 swap between them is not a sale,
  which is what the parser already did (re-audit A2-0236).
- **Kraken trades: the cost must fit vol x price.** A fill whose cost
  contradicts |vol| x price by more than rounding, or whose fee is more
  than 5% of the cost, is refused, naming the txid: a shifted, swapped
  or 10x column used to book with at most a schema warning (re-audit
  A2-0080).
- **Kraken: three smaller ledger fixes.** An instant-trade spend with a
  positive amount or a receive with a negative one is refused (the
  amount was taken as abs(), booking an inverted trade as an ordinary
  buy); a fill whose fee was paid with KFEE fee credits books with no
  fee instead of being refused; and a multi-coin dust sweep into one
  coin keeps ids fill-crypto pairs, so each split swap is valued once
  instead of each leg at its own coin's close (re-audit A2-1019,
  A2-0577, A2-0581).
- **Kraken: rows that are not your own cash moving are no longer
  ignored.** A trades row whose type is blank or not buy/sell, a fiat
  `credit` or `adjustment`, and a coin row that moves nothing but a fee
  are UNBOOKED warnings (shown by `taxjson run`, refused by `--strict`);
  they were a quiet note saying moving your own cash is not a tax event.
  A fee taken in a coin on a fiat withdrawal or on a staking reward is a
  sale of those coins at fair value, as on a coin withdrawal (tax-logic
  CA-CRYPTO-03 / US-CRYPTO-03). `earn/migration` rows are a wallet move,
  and a ledger whose rows are all recognized non-events (an ETH->ETH2
  relabel, a fiat deposit) no longer prints the "parsed to 0
  transactions" warning that `run --strict` refused (re-audit A2-0245,
  A2-0578, A2-1002, A2-0582, A2-1018, A2-1017, A2-0583).
- **Kraken: a broken quote or a duplicated column is refused.** A stray
  quote that closed in a later row swallowed the rows between into one
  cell, silently dropping those fills or rewards; an unterminated quote
  was reported at the end of the span as a truncated row; a header with
  two `fee` columns used the last one. Each is now refused, naming the
  line the quote opened on or the duplicated column (re-audit A2-0246,
  A2-0247, A2-0248, A2-1022).
- **US: all five USD stablecoins at par on Kraken.** In a US project
  PYUSD and GUSD are valued at their 1.00 USD par like USDC, USDT and
  DAI (a swap, a reward or a fee in one); an EUR/PYUSD fill is refused
  like EUR/USDC instead of being dropped as a forex conversion; and a
  Kraken ledger instant swap against a stablecoin takes the par ahead
  of the export's amountusd, as the trades export does. tax-logic
  US-CRYPTO-02 says so (re-audit A2-1004, A2-1020).
- **Kraken: every fiat currency is cash.** Only USD, CAD, EUR and GBP
  were: an AUD, JPY or CHF bank deposit or withdrawal became a crypto
  send to classify, an XBT/AUD fill a coin-for-coin swap with a phantom
  `AUD` coin, and an AUD.HOLD reward an unpriced coin. Kraken now uses
  the Coinbase parser's fiat list (re-audit A2-0238, A2-0251, A2-0579,
  A2-0580).
- run: a decided crypto gift/payment that cannot be written (no fair
  value, a malformed sends.json) now reaches the account .sum
  DIAGNOSTICS and stops `run --strict`; the warning also says when the
  previous crypto_sends.tt is still booked (re-audit A2-0112).
- **Canada: a coin residue under a millionth stays a holding.** The
  pool walk emptied any position under 1e-6 units after a sale, so
  9e-7 BTC left after selling 1 BTC vanished from the holdings and its
  cost moved onto the next purchase. A crypto pool now only drains
  float noise (under 1e-11 of the position): the residue keeps its
  units and its own cost, and a sale that overshoots the pool by a few
  satoshis is no longer dropped. Share pools keep the millionth-of-a-
  share tolerance. A new coin-book fuzzer (units, cost and wash
  conservation) pins it (audit S069-13, tax-logic CA-CRYPTO-09).
- **crypto: the network fee hidden in a Coinbase Send is booked.** A
  send matched to its arrival on another exchange that arrived SHORT,
  with no fee stated (Coinbase puts the network fee inside the sent
  quantity), now writes a `<send id>-fee` sale of the shortfall at fair
  value (the send row's spot price, else the Yahoo close) to
  `crypto_sends.tt`, in both countries — the way the Kraken withdrawal
  fee paid in the coin is booked. The fee coins no longer stay in the
  pool as phantom units (which also fed the superficial-loss still-held
  test). A Canada stablecoin's shortfall is US-dollar cash and gets no
  line. `crypto-sends` lists each with its value (audit R1-26).
- **Kraken: PYUSD and GUSD are US-dollar cash in a Canada project.** As
  on Coinbase and like USDC/USDT/DAI: a PYUSD/USD buy is a currency
  conversion, an ETH/PYUSD fill a purchase for dollars, a PYUSD reward
  dollar income at 1.00 — no PYUSD pool that never closes, no Yahoo
  lookup; `crypto-sends` gives a Kraken PYUSD gift the currency gain.
  A US project keeps every stablecoin as property (audit S060-24).
- **Coinbase hardening.** A truncated row and a Send/Receive with no
  quantity are refused; a row of an unknown type that moves coins (an
  airdrop) is an UNBOOKED warning (fatal under `run --strict`); a dust
  convert into a stablecoin whose fee exceeds its value books negative
  proceeds (the excess fee was dropped); the Convert refusal carries its
  own `.tt` workaround.
- **Kraken fees and pairs.** A withdrawal fee charged in another coin
  (`feecurrency`) is a sale of that coin; a coin fee is named in the
  transfer's description instead of the money `fee` field (`taxjson
  fees` read 25 XRP as 25 USD); the ledger's `feeusd` / `amountusd`
  value a coin fee and a trades-CSV crypto/crypto fill; a fee on an Earn
  wallet move is UNBOOKED; a legacy pair ending in PYUSD/RLUSD/FDUSD/GUSD
  is refused as ambiguous; every "use a .tt file" refusal also says to
  remove the row. The coin-fee note says those fees are not in the fee
  reports (KNOWN_ISSUES).
- **`taxjson crypto-sends` lists matched sends that arrived short** — the
  network fee a Coinbase Send carries inside its quantity, which is not
  booked as a sale.
- **Canada: crypto rows under a millionth of a unit are booked.**
  Staking rewards below 1e-6 units were dropped by the ACB pool (their
  income was taxed with no matching cost, and the holdings fell short of
  the exchange balance); a sub-micro sale was never reported. The effect
  on a typical book is negligible. The US engine still skips rows under 1e-8 units
  but now names them (KNOWN_ISSUES).
- **crypto-sends: a Kraken PYUSD or GUSD send is a coin send.** The
  Kraken parser books those two as coins, but crypto-sends treated them
  as US-dollar cash on every exchange, so a gift or payment of them got
  no sale line and the coins stayed in the book. tax-logic CA-CRYPTO-08
  now also says a stablecoin currency loss flagged superficial is
  excluded in full (the conservative reading).
- **US: stablecoins are property.** In a US project USDC, USDT, DAI (and
  PYUSD/GUSD on Coinbase) were folded into US-dollar cash — Canada's
  stated approximation — so a stablecoin payment never reached Form 8949
  and a de-peg loss vanished. `taxjson run` now parses a US project's
  Kraken and Coinbase files with stablecoins as coins: buying one is a
  purchase, selling or spending one a sale, a swap / reward / fee in one
  is valued at its 1.00 USD par, and `crypto-sends` writes a stablecoin
  payment as a sale line. Canada keeps the cash treatment (with a de-peg
  warning). tax-logic CA-CRYPTO-02 / US-CRYPTO-02.
- **Crypto local time is a project setting.** `[settings] local_timezone`
  (an IANA zone, both countries) names the zone Kraken and Coinbase UTC
  stamps are dated in; it was only the `TAXJSON_LOCAL_TZ` environment
  variable, with America/Toronto for everyone, so a trade after midnight
  Eastern on Dec 31 could land in the wrong tax year for someone in
  another zone. The default is unchanged; tax-logic states the zone in
  force. A change re-parses the crypto account.
- **A crypto `gift` saved in sends.json is refused in a US project when
  the .tt is written, not only at `--set`.** A decision carried over
  from a Canada project, copied or hand-edited was written to
  crypto_sends.tt as a sale at fair value under the Canadian rule and
  landed on Form 8949. Now it is never written; `crypto-sends --write`
  and `taxjson run` stop naming the send, the listing marks it REFUSED
  and the checklist flags it. The generated file's header cites only the
  project country's rule.
- **A crypto send booked twice is easier to spot.** The warning when a
  hand-written .tt already sells what crypto_sends.tt sells now names
  both files (with the line number), the coin, quantity and timestamp,
  and says how to keep either line; it searches every account's inputs.
  Nothing is deleted.
- **Overlapping Kraken ledger exports are fine.** Two ledger exports in
  the crypto folder that repeat rows (an all-history ledger beside the
  yearly ones, a copy) stopped every run with a misleading "check that
  the ledger export belongs to this account" error, and a ledger file
  with repeated rows doubled its instant trades without a word. A
  ledger row is now counted once per txid; the same txid with different
  content stops the run naming the files. A Kraken row with fewer or
  more cells than the header (a missing `fee` read as 0) is refused.
- **Kraken rows the parser cannot book are UNBOOKED warnings.** An
  airdrop, forced conversion, adjustment, sale, credit, Earn migration
  or margin/rollover/settlement row used to hide behind a note saying
  transfers "don't affect gains"; an instant-trade spend with no
  receive leg (or the reverse) was a warning only in the report. Both
  now print `warning: UNBOOKED:` on the console, and `run --strict`
  refuses them. A trades fill with a nonzero `margin` value warns that
  it is booked as spot.
- **One crypto symbol per coin.** Lower-case codes (`eth/usd`, `dot.s`,
  Coinbase `sol`) are upper-cased instead of opening a second pool (a
  lower-case Kraken pair became a swap with a phantom `usd` coin);
  Kraken's bonded staking codes (`DOT28.S`, `KSM07.S`, `SOL03.S`, ...)
  fold to the bare coin; Coinbase `ETH2` folds to `ETH` as Kraken's
  already did. Swapping ETH for ETH2 (a Coinbase Convert, a Kraken
  `ETH2.S/ETH` fill or ledger wrap) is a counted non-event instead of a
  sale at market value.
- **Coinbase refuses what it cannot read.** A blank `Price Currency`
  cell (the column present) no longer defaults to USD — a CAD row was
  converted twice; a header written with spaces after the commas is
  recognised; a cell that spans lines (an unterminated quote that
  swallowed the rows after it) or a row wider than the header stops the
  parse naming the line.
- **A crypto-to-crypto swap has one value.** Both legs of a swap (Kraken
  crypto pairs and instant trades, Coinbase Convert without a Subtotal,
  Advanced Trade on a crypto pair) used to be priced from each coin's
  own daily close, so the spent coin's proceeds and the received coin's
  cost differed — a phantom gain or loss. A Kraken instant trade with
  `amountusd` now uses it for both legs, and `taxjson-fill-crypto`
  values both legs at the received coin's fair value (the spent coin's
  when the received one has no price).
- **Kraken Hybrid Earn moves are yours.** `crypto-sends` classifies a
  Kraken `hybridearnwithdrawal` (the coins move to Kraken's Earn product
  and keep earning rewards) as `self` automatically instead of asking;
  `--set ID=gift|payment` still overrides it.
- **`taxjson crypto-sends`: is a crypto send a gift, a payment, or your
  own wallet?** Every withdrawal/send that did not arrive on another of
  your exchanges is listed with its fair value (the exchange's spot
  price, or the Yahoo daily close times the Bank of Canada rate) and the
  ready `.tt` sale line. Record the answer with `--set ID=self|gift|payment
  [--note]` (kept in `inputs/<acct>/sends.json`); `--write` generates
  `inputs/<acct>/crypto_sends.tt` with one BUYSELL per gift or payment.
  `taxjson run` asks at a terminal and refreshes the file; the checklist
  has a "Crypto sends classified" step. Stablecoin gifts get the currency
  gain against the USD pool's average cost instead of a sale line. If you
  already declared a send by hand (a `tao_payment.tt`), delete that file
  when you adopt `crypto_sends.tt` — the command warns while both exist.
- **Coinbase Advanced Trade on a crypto-quoted pair books both coins.**
  A fill on `ETH-BTC` booked only the ETH; the BTC spent (or received)
  was never disposed of (or acquired), so its gain went missing and a
  phantom BTC position stayed in the book. The quote coin's leg is now
  booked at the fill's stated value; Notes that don't say how much of it
  moved stop the parse.
- **Coinbase Buy/Sell with a blank Total.** It booked $0 cost or $0
  proceeds with no warning. The Total is now rebuilt from Subtotal ± fee
  (or quantity × price ± fee), with a note; a row with nothing to rebuild
  it from stops the parse, naming the row.
- **Kraken ledger trades missing from the trades export.** They were
  dropped behind the same note a complete run prints. Each unmatched
  trade is now an `UNBOOKED` warning on the console (count, dates,
  masked refids), and `taxjson run --strict` stops on it. A ledger whose
  trades are all matched no longer prints the "parsed to 0 transactions"
  warning.
- **Crypto price lookups that fail are no longer $0.** A Yahoo error, or
  a reply with no usable close, left staking income and cost basis at $0
  and the run said "Validation passed". Now fill-crypto warns, each
  unpriced row is a validation ERROR on the console, and `run --strict`
  stops. A crypto validation error also no longer crashes `taxjson run`
  with a TypeError.
- **`crypto_ticker.map` at the project root is always read.** It was
  ignored unless the cwd was the project root, and a map in the cwd
  applied to whatever project was run. `run --fast` now re-prices after
  the map is added, edited or deleted.
- **Coinbase staking income is the Subtotal.** Coinbase's Total adds back its
  staking commission ("Fees and/or Spread"), coins you never received; it
  was booked as income and as the rewarded coins' cost. Income and ACB now
  use the Subtotal (quantity x price).
- **Kraken: fees charged in the traded coin.** Kraken's trades CSV states
  every fee in quote units, even when Kraken took it in the coin you
  bought or sold, so the full `vol` was booked and phantom coins piled up
  (seen on real exports in six coins). With the ledgers export in the
  same folder,
  each fill is joined to its ledger rows (refid = trade txid) and booked
  from them: a coin fee reduces the coins received (buy) or adds to the
  coins given (sell), with no quote-currency fee. Any disagreement
  between the two exports raises. Without a ledger the parser warns that
  the fee currency can't be verified. Every crypto asset's parsed units
  now equal the ledger's amount − fee exactly. Crypto gains on real books
  move slightly (mostly this fix); staking income is
  unchanged.
- **Kraken: legacy ledger rows, suffixes, fee currency, required
  columns.** `staking` and `dividend` rows are income (they were
  dropped); `.S`/`.M`/`.F`/`.B`/`.P`/`.HOLD` asset suffixes fold to the
  bare coin, and `ETH2` to ETH; legacy spot-to-staking transfers are
  wallet moves; `feecurrency` is honoured (a reward whose fee is charged
  in another currency is credited in full, the fee reducing the income);
  a coin-denominated withdrawal fee is booked as a disposition of the fee
  coins; a missing `fee`/`vol`/`amount` column, or an unparseable number,
  raises instead of reading as 0.
- **Kraken: multi-asset dust sweeps book every swept coin.** One refid
  spending several assets for one receipt kept only the last spend leg
  (a warning, then the others stayed in the book forever). The receipt is
  now split across the legs by `amountusd` (equally when absent).
- **Coinbase: USDC is US-dollar cash, as on Kraken.** `Buy USDC` rows
  are stablecoin conversions (not positions), and the USDC leg of an
  Advanced Trade on a `*-USDC` pair is cash. Before, the USDC bought was a
  position that the Advanced-Trade spends never reduced (a phantom USDC
  long). Strictly a stablecoin is a crypto-asset for the CRA; what the
  cash model leaves out is the USD/CAD movement while it is held — small
  on real data.
- **Kraken and Coinbase rows are dated in local time.** Both exchanges
  stamp UTC; rows are now converted to America/Toronto (override with
  `TAXJSON_LOCAL_TZ`), so a trade at 03:00 UTC on January 1 lands in
  the previous tax year. None of the real 2025/2026 rows straddle a year
  end; the shift moves some dates by a day (FX/price day). Coinbase and
  Kraken amounts like `CA$4.00`, `US$-3` and `(12.00)` now parse;
  anything unparseable raises (`CA$4.00` used to read as 0).
- **Kraken: staking rewards book the coins actually credited** — amount
  − fee, where the ledger fee is in the reward's own coin units (the
  balance moves by amount − fee in both the pre-2026 and 2026 formats).
  Income and acquired quantity drop by Kraken's commission (typically
  20–30% of the gross reward), the phantom units are gone, and the coin
  fee no longer lands in the USD fee field. Instant trades and
  crypto/crypto trades-CSV fills fold their coin-unit fees into the
  crypto leg's quantity the same way.
- **Kraken: `hybridearnwithdrawal` rows are custody evidence**, like a
  withdrawal (kept aside, never in the book). They were non-events on
  the assumption of an internal Earn move, but the rows carry a funding
  refid, have no counter-leg in any earn wallet and sweep the spot
  balance to dust — the coins left the ledger.
- **Crypto accounts blend across exchanges** (Canada): with two or more
  taxable `crypto = true` accounts, `taxjson run` now runs ONE blended
  crypto pass (like the equity blend) producing each account's
  `<account>_gains_wash.json` — ITA s.47 averaging and the superficial-
  loss rule reach identical crypto held on different exchanges. Each
  exchange's book was computed alone, while the run printed a note
  claiming the blended pass covered them (a sale at a blended-ACB loss
  replaced on the other exchange filed -10,000 allowed instead of 0).
  `audit` and `check-filed` recompute the same way; a single crypto
  account and US crypto (no §1091) are unchanged, and the overlap note
  now only names accounts a blend actually spans.

### FX rates, prices and fx-cash

- The built-in FX placeholder rate follows the direction: a row older
  than the rates file in a US (USD) project converts CAD at the inverse
  of 1.35, not at 1.35 (which booked 1,000 CAD of cost as 1,350 USD);
  the row is still a validation error. `taxjson-fees-sum` and
  `taxjson-audit` use the same per-direction fallback (audit A2-0148).
- Damaged price caches no longer crash a run (re-audit A2-0772,
  A2-0773, A2-1403, A2-1446, A2-0464, A2-0474, A2-0800). An entry in
  ~/.crypto_price_cache.json that is null, text, true or Infinity is
  now a cache miss: fill-crypto warns, naming the cache and the entry,
  and looks the price up again. Before, it either crashed or priced the
  coin at 1.0 or inf. crypto-sends does the same. A cache that is not a
  JSON object is ignored. In ~/.currency_price_cache.json, a
  `_coverage`, `_boc` or `_boc_noon` block of the wrong shape is
  dropped with a warning naming the file, and its dates are fetched
  again (offline they have no rate). It used to be a traceback in the
  FX stage, or the cached Bank of Canada series was silently discarded.
- Rates files (re-audit A2-0790, A2-1434, A2-1411, A2-1437, A2-1423,
  A2-1424). A work/to_base.csv that is not UTF-8, is a directory or
  cannot be read is now reported in one line naming the file. This
  applies to fx-cash, harvest, fees-sum, audit, convert-currency and
  crypto-sends, which all used to print a traceback. A UTF-8 BOM no
  longer drops the first rate. `taxjson-convert-currency --rates FILE`
  refuses a missing FILE (exit 2); before, it converted every row at
  --default-rate. fees-sum reports a NaN rate in one line (exit 2), and
  fx-cash reports a futures row it cannot settle in one line.
- price cache: a cached quote's currency is checked like its price —
  'usd' is read as USD, a non-text value is ignored with a warning
  instead of a traceback (re-audit A2-1170).
- The shared price and rate caches in $HOME (crypto prices, currency
  rates, the price chain) are saved through a unique temp file under a
  lock, so two projects' runs at once no longer fail to save or make a
  reader see an empty cache; the crypto price cache keeps both runs'
  prices (re-audit A2-0233).
- FX rates: a transient failure is no longer cached as a permanent
  answer (re-audit A2-0136, A2-0393). A failed Yahoo download counts as
  "no data" only when Yahoo, asked again right then, answers for the
  dates after the range (later dates already in the cache are no proof);
  a second empty Bank of Canada answer is no longer read as a stopped
  series (a series counts as stopped only after 45 silent days with
  nothing cached after the range), and when the Bank answers again the
  hole an earlier empty answer left is asked for again; a noon or Yahoo
  answer cut off before the range end records only the dates it reached
  and says so.
- FX rates: a cached Bank of Canada (or Yahoo) rate that is not a
  positive number (`"abc"`, `"1,3316"`, a list) is no longer copied into
  the rates file, where the run then blamed the config ("no rates at
  all for USD"): it is dropped, named with `~/.currency_price_cache.json`
  and its date, and asked for again online (re-audit A2-1212).
- tax-logic CA-FX-02 / US-FX-02 now state the rate gap the converter
  really accepts: the rates file carries a rate over weekends and
  holidays for up to 7 days and a day with no row looks back 5 more, so
  a rate up to 12 days old is used; no number changes (re-audit A2-0706).
- **fx-cash: a sale whose commission exceeds its proceeds pays
  currency** (it was counted as received, leaving phantom currency in
  the pool) (audit S033-09).
- **Price and FX caches:** `taxjson fx-cash` no longer prices a cash
  event at a rate older than the converter's 5-day lookback (it is
  counted unrated and named); a cached price that is missing, zero,
  negative, NaN or text is a cache miss instead of a 0.00 quote in
  `harvest`; a non-UTF-8 byte in a price/FX cache degrades to a refetch;
  `yf_ticker.map` keys are case-insensitive and a line with no target is
  warned about; an empty or truncated Bank of Canada answer is re-asked
  for 14 days instead of being cached as coverage for good.
- **`--default-rate` must be a positive number.** convert-currency,
  merge2, fees-sum and audit took -1.35 (every converted amount
  sign-flipped), 0, nan, inf and `1_35` (= 135) with exit 0; they are
  refused at the command line. A rates-file rate written `1_35` is a
  malformed line, not 135 (S028-15, S028-17).
- **FX before March 2017 uses the Bank of Canada noon rate.** Folio
  S5-F4-C1 names the Bank's noon rate for dates before 2017-03-01;
  those dates (back to 2007-05-01, where Valet's legacy noon series
  starts) used Yahoo closes, and Jan-Feb 2017 the new daily average.
  They now take the noon rate (source `boc-noon`); Yahoo stays the
  fallback for earlier dates. A Yahoo download that fails silently (an
  empty answer) is no longer remembered as "no data" forever, so the
  next online run asks again. The default-rate error for a date before
  every rate source no longer tells you to refresh (it cannot help) or
  to pass a flag `taxjson run` does not have: enter the row in the base
  currency at its date's rate.
- **FX rates: freshness is judged per currency.** to_base.csv holds one
  block per source currency; a USD block cut short by a failed download
  hid behind a fresh AUD last line and was served for days. Each
  configured currency must now reach the last few days (audit S046-06).
- **fx-cash counts assignment cash, and only cash.** An option
  assignment's stock leg booked as `ASSIGN` (Webull) now spends or
  receives currency like a trade; crypto-for-crypto swap legs (Kraken,
  Coinbase Convert), staking rewards paid in a coin, and stock-for-stock
  corporate actions no longer count as foreign cash moving (they
  invented thousands of dollars of s.39(1.1) gain or loss).
- **fx-cash says the figure can be wrong either way.** The report (and
  the `sum` FOR THE RETURN line and its `--json`) now carries the
  overdraft count and a caveat that unseen conversions and deposits can
  move the estimate in either direction — it used to say only that it
  "understates activity" — and the report shows the ledger's foreign
  cash at Dec 31 to compare with the brokers' balances.
- **One Bank of Canada 404 no longer switches a currency to Yahoo for
  good.** Any HTTP 404 from the Valet API (a maintenance page, a proxy)
  was cached as "series not published" with no expiry, so every later
  run of every project on the machine converted that currency at Yahoo
  closes, cached Bank rates included. Only the Valet API's own
  "Series FX…CAD not found" answer marks a series now; the mark carries
  its date and is re-checked after 7 days; cached Bank observations keep
  their source; the note names the answer and the source used.
- **FX rates now come from the Bank of Canada.** For a CAD base,
  `taxjson run` converts at the Bank of Canada daily average rate
  (Valet API, series `FX<CUR>CAD`), the rate CRA expects (Folio
  S5-F4-C1) and the one the docs always claimed — earlier releases
  actually used Yahoo Finance closes. Yahoo is now only the fallback
  for dates before 2017-01-03 (where the Valet series begin) and for
  currencies the Bank does not publish. **Computed CAD amounts will
  move by cents to dollars per trade versus earlier releases**: over
  2021–2026 the Yahoo USD/CAD close differed from the Bank's rate by
  0.17% on the median day (about $17 per US$10,000), 0.6% at the 95th
  percentile and up to 1.7% on volatile days (GBP and AUD somewhat
  more). Each rate
  in `work/to_base.csv` carries its source in a new sixth column
  (`boc` / `yahoo`; loaders still read the first five), the run
  prints `FX USD→CAD: Bank of Canada Valet for N dates, Yahoo
  fallback for M`, and each account's `.sum` DIAGNOSTICS gets the
  same count for the rates its rows actually used. Weekends and
  holidays take the most recent prior business-day rate, as before.
  Non-CAD (US) projects still use Yahoo Finance.
- **FX history no longer ends 5.5 years back.** Rates were fetched for
  "today minus 2000 days", so any older transaction silently converted
  at the 1.35 default rate (stderr only; the run exited 0 and the
  checklist said clean). Rates now reach back to 2000-01-01
  (`taxjson-to-base-curr --start`), and a row converted at the default
  rate is a **validation ERROR** — counted in the `.sum` DIAGNOSTICS
  `validation: N error(s)` line and `taxjson checklist`, fatal under
  `run --strict` (crypto books too). An explicit `--default-rate`
  still accepts the fallback.
- **`TAXJSON_OFFLINE`**: the FX stage no longer demands a rate for
  TODAY (a cache warm from yesterday failed offline every new day,
  even for a past-year or all-CAD project). Offline it serves the
  cache and never fails; only dates a transaction actually needs
  matter, and a missing one is a validation error. `TAXJSON_OFFLINE=0`
  (or `false`/`no`/`off`) now means off — it used to switch offline
  mode ON; only `1`/`true`/`yes`/`on` turn it on.
- `taxjson-to-base-curr` no longer imports pandas at start-up: a
  core-only install (install.sh's silent fallback) crashed the first
  run with `ModuleNotFoundError`. The Bank of Canada path needs no
  extra; the Yahoo fallback asks for `taxjson[fx]` by name, and
  install.sh now warns loudly when it falls back to the core package.
- **`taxjson fx-cash`**: withheld tax left the foreign-cash pool twice
  (the dividend was booked net of withholding AND its TAX row was
  spent) — RBC's implied-tax rows and every IB dividend paired by
  merge2. Dividends and interest now enter the pool GROSS; the TAX row
  takes the withholding out once. Phantom overdrafts on withheld
  dividends disappear and the gain changes accordingly.

### Filing: form-export, locks, handoff, checklist, slips, T1135, carryover

- Form 8949 from tax year 2025: a crypto account's sales are digital
  assets on boxes G/H/I (short-term) and J/K/L (long-term), grouped apart
  from the securities' A/B/C and D/E/F with their own totals in the
  export, `sum` and the close-year lock; the box note names them, and the
  TXF (which has no G-L code) leaves them out with a warning (tax-logic
  US-RPT-11, US-RPT-03; re-audit A2-0482, A2-0829). The checklist's
  1099-DA statement is tax-logic US-RPT-10 (US-RPT-09 had two meanings).
- Schedule 3 for 2024 follows the 2024 form's two periods: dispositions
  from January 1 to June 24, 2024 go on the Period 1 codes 10689/10690
  (shares) and 10693/10694 (options, futures, crypto and other
  properties), the rest on 13199/13200 and 15199/15300, in `form-export`,
  `sum`'s FOR THE RETURN and the close-year lock; a security sold in both
  periods has two rows, and the notes name the Period 1 slip lines
  17399/17599. `check-filed` compares a 2024 lock written before the split
  on the Period 2 codes and says so in a note; `carryover` reads the
  Period 1 gain lines (tax-logic CA-DISP-03; re-audit A2-0166, A2-1482).
- `taxjson close-year` blends the taxable accounts in taxjson.toml order,
  as the run does: with two accounts trading one security at the same
  moment, the year-end record could carry a superficial-loss deferral
  (and a lower cost) that the return never had (re-audit A2-1556).
- `taxjson t1135 --year-wash-only`: the superficial loss excluded from the
  cost columns is the sum of each account's share of a blended pool's
  deferral, not the largest account's share (re-audit A2-1552).
- Two taxable accounts with rows at the same moment: check-filed and the
  run's filed-year drift check, `taxjson t1135`, `carryover`, `audit`,
  `wash-sales --explain` and the radar now merge the books in
  taxjson.toml order, as the run does (CA-DATE-14 / US-DATE-13). They
  took the accounts alphabetically, so check-filed reported a false
  DRIFT right after close-year, audit a false tie-out mismatch, and
  t1135 / carryover showed another book's cost and gain (A2-0512,
  A2-1592, A2-0497, A2-0502).
- `taxjson carryover` in a US project whose only taxable accounts are
  crypto accounts no longer applies the wash-sale rule to the coins: the
  books go to the ledger's no-wash crypto pass, as in a mixed project
  (US-WASH-13; audit A2-0146, A2-0411, A2-0412).
- `taxjson handoff`: a prior-year lock whose fields are the wrong shape
  (dispositions, settle_next_year, year_end, schema_version, ...) is one
  `taxjson handoff: error:` line naming the file and field, exit 2; a
  BOM'd lock loads. `close-year --filed-dispositions` refuses a short row
  or a blank symbol naming file:line, and the hand-off reads the gains
  and base books through the shared work-file check (A2-0769, A2-0803,
  A2-1396, A2-1397, A2-0794 handoff part, A2-0776).
- `taxjson checklist`: a checklist.json that is a directory or a looping
  symlink, a wrong-shape mark entry, or a file that cannot be written or
  removed (read-only project, full disk) is now one `taxjson checklist:`
  line; a failed write keeps the old file and leaves no .part; a BOM'd
  hand-edited file loads (A2-0768, A2-0789, A2-1393, A2-1414, A2-0776).
- Standalone `taxjson-t1135` says when it falls back to close timing
  for written options (as `taxjson-gains` does), and `taxjson
  find-missing-history --gen-phantoms` runs the gains engine with the
  project's option timing and income dating (re-audit A2-1361).
- Standalone `taxjson-reconcile-slips` requires `--country` (a missing
  one silently meant Canada: CAD amounts and settlement-date year
  scope), and `--date-basis` defaults to the country's (trade date for
  the USA, so a Dec-31 sale is on its 1099-B year). In a US run the
  notes and the currency refusal name the 1099-B, FIFO basis per
  account and the project's own rates — never the T5008, its boxes,
  the Bank of Canada or a blended ACB (re-audit A2-0423, A2-0744,
  A2-0747, A2-0753, A2-1292, A2-1294, A2-1295, A2-1331, A2-1337,
  A2-1348, A2-1349, A2-1350, A2-1351). `taxjson reconcile-slips` is
  unchanged.
- `taxjson option-boundary` (and the checklist step that runs it) reads
  the lock named by `[settings] prior_year_record` like a local
  filed/<year>.json: the per-year layout got "no filed-year locks" and a
  "no amendment required" checklist row where a local copy of the same
  lock gave ATTENTION (re-audit A2-0360).
- A crypto-only Canada project no longer gets the
  `option_grant_timing_since is not set` warning from `carryover` and
  `option-boundary` (run and the checklist already skipped it: it writes
  no options) (re-audit A2-1144).
- `taxjson checklist --done/--skip/--undo` are repeatable (only the last
  of a repeated flag was recorded, silently), and concurrent marks no
  longer lose each other or corrupt checklist.json (a lock around the
  read-modify-write, an atomic write) (re-audit A2-1159, A2-1160).
- Checklist wording: the T1135 step's "below the threshold" (and
  `t1135 --json`, new `scope` / `scope_note`) says it covers these books
  only; a US project's slip step names Form 1099-DA for crypto sales from
  2025 (tax-logic US-RPT-09); the T5/T3 step's command names the TAXABLE
  lines of divs-sum / roc-sum; Form 8949 exports carry `gain_unrounded`
  so a pure per-row rounding gap no longer makes a US checklist's
  form-export step "attention" (re-audit A2-0682, A2-1149, A2-1152,
  A2-1154).
- Checklist roc-entered: ADJUST rows are counted in the year roc-sum
  windows them on (a Canadian trust's ROC by its record date), and the
  step is "attention" when the same ROC is in the books and in
  distributions.map (roc-sum's and apply-distributions' "reduced twice"
  warning) (re-audit A2-0361, A2-0680).
- Locks: a lock taken before its year ended (`close-year --force` on an
  open year) is called a snapshot, not a filed return, by the checklist's
  filed-lock and lock-committed steps (attention), by check-filed (a note
  beside OK) and by option-boundary (marked partial); a filed/<year>.json
  that is a directory is "blocked", not "no lock". A non-path
  `prior_year_record` is refused by every command (handoff and the
  checklist built a path from a list), and handoff with no `year` refuses
  instead of looking for filed/-1.json (re-audit A2-0679, A2-1146,
  A2-1161, A2-1162, A2-1164).
- Checklist inputs-frozen: an RBC export is judged per RBC account (the
  Account column), so another account's later export in the same folder
  no longer certifies an export taken before the year ended (re-audit
  A2-1147; the IB statement-period half was fixed by A2-0262).
- Checklist run-clean / filing banners / web freshness: the record of
  what the last full run read now covers every run input — an account's
  elections `manifest.json` and crypto `sends.json`, and the root
  `crypto_ticker.map` — so a changed election no longer reads as current.
  Only the settings `run` reads count in taxjson.toml: a comment,
  `[instalments]`, `[estimate]`, `province`, `prior_year_record`,
  `holdings` and the fetch keys no longer mark the books stale (close-year
  refused after an instalment was recorded). A damaged record is
  "attention", not "no record"; a dangling reports/*.sum symlink is named
  instead of crashing the step. Hidden files and Excel `~$` lock files in
  inputs/ are no longer read by `run` (nor fingerprinted, nor taken as
  slips), and an Apple Numbers export is refused like .xlsx (re-audit
  A2-0124, A2-0126, A2-0358, A2-0363, A2-0681, A2-1145, A2-1148, A2-1155,
  A2-1156, A2-1157, A2-1158, A2-1166).
- `run`'s expired-open-option warning uses the same cutoff as
  option-boundary and the checklist: an expiry on Dec 31 of a closed
  year, and one after Dec 31 but within the books' data, are flagged
  (re-audit A2-1210).
- close-year / handoff / option-boundary (re-audit filing locks):
  close-year refuses books with no reports/ (a run that died before
  writing them) and an unreadable work/<acct>_base.json instead of
  locking empty year-end positions; handoff names an unreadable or
  damaged base file instead of reporting every lot as missing or
  naming a deleted /tmp merge file (A2-0035, A2-0346, A2-1137,
  A2-1143). option-boundary reads last year's lock through
  [settings] prior_year_record, as handoff does (A2-0036, A2-0335);
  handoff flags a written option the closed year's record taxed on
  another premium timing than this project (A2-0037); the
  option_grant_timing_since hint quotes the since a lock records
  (A2-1142); handoff refuses a non-string prior_year_record like run
  (A2-1135).
- close-year --force keeps the dispositions another tool filed (and
  their totals) from the lock it replaces, and warns when the replaced
  lock recorded other totals (A2-0119, A2-0345); --filed-dispositions
  goes through the broker decode funnel (UTF-16 read; a directory or a
  stray quote is one line, exit 2) (A2-1136, A2-1138); handoff flags a
  record closed before its year ended as a partial-year snapshot
  (A2-0349).
- close-year's year-end cost places each superficial-loss addition
  where the engine lands it (per replacement symbol, on its own trade
  and settle date) instead of one lump on the first replacement, so a
  January replacement's share is no longer in the Dec 31 cost; a US
  record carries the §1091 basis addition `list` shows (A2-0669,
  A2-0352, A2-1140, A2-0353).
- handoff matching: a straddling trade matches only the same trade (same
  trade date, or the same net within 3 days), so a distinct same-size
  January sale no longer hides a sale missing from both years; a
  date-basis change between the two projects is one item per sale (not
  a position, two doubles and the wrong date); a closed-year sale that
  is its own row in this project is not "reported in both years"; a
  short cover matches another tool's filed short; a sub-unit (crypto
  dust) quantity difference is reported (A2-0354, A2-0356, A2-0673,
  A2-0122, A2-0674, A2-1133).
- close-year records the December and January rows; handoff reports
  rows the two projects date on different sides of Dec 31 — trust
  income or a ROC its record date moves back into the closed year, a
  row local_timezone re-dates to Dec 31, a RIC January dividend kept in
  one project only, an overnight fill the closed project moved into
  January — as in neither or both returns (A2-0120, A2-0343, A2-0344,
  A2-0675, A2-0670).
- check-filed / audit (re-audit filing locks): `audit --year` on a locked
  year reads the lock `[settings] prior_year_record` names (per-year
  layout) and recomputes on the date basis the lock recorded, with a note
  (A2-0334, A2-0335, A2-0664, A2-1129). check-filed reports a lock whose
  account entry records no totals, or whose `form_lines` is not a table,
  as damaged instead of "OK (matches)" (A2-0347, A2-0668); notes when the
  project's `tax_date` or `option_buyback_loss_superficial` differs from
  the lock's (A2-0348, A2-0672); refuses a bad `[settings]` value as a
  settings error, not a damaged lock (A2-1134); and shows the child's
  one-line error, exit 2, when the recompute fails on an input (A2-0676).
  audit and `wash-sales --explain` name a damaged `work/<acct>_base.json`
  instead of a deleted /tmp merge file (A2-1143).
- carryover: a year before the project year with a close-year lock
  (filed/<year>.json or prior_year_record) uses the lock's filed figure
  (filed_totals, else the gain lines) instead of the rebuilt books, so a
  prior year's loss is carried and a carry-back to it is offered; a
  later locked year is compared with the filed lines; an unreadable or
  non-finite lock is named (re-audit A2-0121, A2-0336, A2-0338, A2-0666,
  A2-1130, A2-1131, A2-1132, A2-1139).
- carryover and t1135: the full-history engine pass applies
  [settings] corporate_distributions (a listed corporation's ROC on its
  pay date), as the run does (A2-0123, A2-0337, A2-0339, A2-0340,
  A2-0341, A2-1141).
- carryover: rows after the project year are partial (no T1A
  suggestion, the carryforward stops at the project year); box-18
  capital-gains dividends in capital_gains_dividends.map are netted; a
  Canada ledger in USD or a book whose metadata.target_currency differs
  is refused; a dangling claimed_losses.txt / t1135.map is refused; a
  claimed amount '0,125' is refused; standalone carryover and audit
  note the close-timing default (A2-0351, A2-0355, A2-0665, A2-0667,
  A2-0677, A2-0678).
- harvest, t1135, form-export and carryover strip and upper-case
  --base-currency (' CAD' no longer drops every row or refuses the
  books; re-audit A2-1169).
- US form-export (8949, TXF) and `sum` FOR THE RETURN keep §1256
  contracts — futures, options on futures, broad-based index options
  such as SPX — off Form 8949 and list them for Form 6781 by hand; a
  futures loss no longer shows as negative proceeds, and the close-year
  lock records their net separately (re-audit A2-0118, A2-0322, A2-0323,
  A2-0324).
- Schedule 3: a commission rebate (IB, Questrade) stays netted in the
  proceeds instead of a negative OUTLAYS cell; cells round half-up and
  the ACB is never a negative rounding residual, in the rows, the line
  totals and `sum` (re-audit A2-0653, A2-1053, A2-0649, A2-1107,
  A2-1104). Form 8949 rounds a half-cent wash adjustment half-up so (h)
  matches the allowed gain the other reports print (A2-1105).
- form-export: a crypto account's phantom-basis dispositions are listed
  once under MANUAL REPORTING, not twice (re-audit A2-0113); the export
  prints the per-row rounding note `sum` prints (A2-1108) and says when
  the tax year has not ended, as `sum` FOR THE RETURN now does too
  (A2-1103).
- form-export and carryover refuse a `--base-currency` other than the
  return's currency (CAD / USD) instead of exporting a Canadian Schedule
  3 in USD, or applying the US $3,000 offset to CAD amounts; a
  disposition with no currency is warned about (re-audit A2-0652,
  A2-1118).
- form-export docstrings describe the short-sale columns as the code
  renders them and show `--country` in the usage lines (A2-0643).
- **T1135 cost walk follows the engine.** A return of capital on a
  sold-out position no longer lowers the next purchase's cost, and one
  beyond the ACB leaves the cost nil (CA-ACB-07), so the maximum cost —
  and the filing verdict — match the books (re-audit A2-0034, A2-0115,
  A2-0321). An exercised or assigned option whose root drops the share
  class (SAMPLCB for SAMPLC.B, SAMPLD for SAMPLD.B) folds its premium into the
  shares, through the engine's own resolver (A2-0328, A2-1106). A long
  option expiring on Dec 31 is named as still held (A2-1121); a s.260
  payment in lieu counts in the income column (A2-0661); a foreign
  listing whose rows carry a Canadian ISIN is named for a `SYMBOL CA`
  t1135.map line (A2-0332). A non-CAD `--base-currency` is refused
  instead of testing USD amounts against a "100,000 USD" threshold
  (A2-0660).
- Schedule 3 / reconcile-slips: under grant timing a buy-back nets
  against this year's write only when it closes a write of the same
  year; a buy-back of an earlier year's write (grant or pre-`since`
  close timing) next to a new write of the same series is its own
  disposition (units 2, not 1; re-audit A2-0320, A2-0650, A2-0651). The
  engine's buy-back rows name the write years they close
  (`grant_closed`).
- reconcile-slips: an option written under grant timing and still open
  at Dec 31 is NO_SLIP_EXPECTED in the write year, and the close year's
  T5008 (premium as proceeds) reconciles with a note; both years failed
  (re-audit A2-0657). A computed row with no slip row keeps its listing
  suffix in the label. Two columns that are both exact spellings of one
  amount are refused as ambiguous (A2-0656), and a broker option
  description with a grouped strike (`5,000.00`) is matched (A2-1113).
- option-boundary: an assignment whose option root drops the share
  class (SAMPLD for SAMPLD.B.TO, SAMPLCB for SAMPLC.B.US) is paired with its share
  leg by the engine's own resolver; it was called cash-settled with
  "no amendment" while the engine folds the premium (re-audit A2-0114,
  a regression of S075-09, and A2-0328). A buy-back carried with a
  negative net (a .tt book) is a cost of its magnitude ('net 500.00' and
  '--101.00' before, A2-1111), and a contract on its own expiry day is
  open, not 'expired, missing its expiry row' (A2-1112).
- Wording: the Canada estimate, the checklist walk (both countries) and
  docs/filing.md no longer say that no taxjson output totals interest —
  they point at the .sum's net CASH INTEREST line and say why it is not
  the interest paid; the estimate's Assumes line says mapped T5 box 18
  capital-gains dividends are included (re-audit A2-0644, A2-1153,
  A2-1101).
- Canada: `taxjson list --date` and the close-year `year_end` snapshot
  judge a trust's return of capital by its record date, as the books do
  (CA-INC-DATE-ROC-TRUST): a January-paid ROC with a December record
  date used to be cut off, so the as-of ACB differed from the engine's
  (A2-0554/0960/0202).
- **T1135: a superficial loss denied in an earlier year is in the
  replacement's cost.** The cost walk added only the project year's
  denials, so a 2025 denial on shares still held in 2026 was missing
  from the 2026 maximum and Dec-31 cost columns (buy 120,000, sell at
  90,000, rebuy within 30 days: the 2026 report said "no T1135
  required" at 90,000). `taxjson t1135` now runs the engine once over
  the full history (registered accounts as wash context, the project's
  option timing) and replays every s.53(1)(f) addition where the engine
  applied it; the gains files' `wash_sales` records carry those
  landings (`adjusts`). The cost columns now equal the engine's ACB.
  `--year-wash-only` keeps the old year-only mode with its note
  (audit S008-07, S009-01, S051-21).
- **Schedule 3: a written option's premium is shown gross.** Under grant
  timing, form-export and `sum`'s FOR THE RETURN block show a written
  option's premium GROSS as proceeds with its write commission as an
  outlay, as for a sale; the gain is unchanged. Line 6 proceeds and
  outlays each rise by the year's write commissions. reconcile-slips'
  gross proceeds match (audit R1-40; tax-logic CA-DISP-06). A year
  locked by `close-year` reports the moved line 6 proceeds as drift in
  `check-filed`.
- **close-year and option-boundary guard the lock.** close-year refuses
  (without `--force`) a tax year that has not ended and a year with no
  disposition and no income (a typo'd `[settings] year`), and always
  refuses books built with another option timing than taxjson.toml now
  says (the lock stamped the edited setting next to grant-timed
  totals). option-boundary requires `[settings] year` (it printed "tax
  year 0" and suggested `option_grant_timing_since = None`) and warns
  when a `filed/<year>.json` cannot be read instead of silently giving
  the opposite T1-ADJ advice (audit S045-23, S045-24, S046-02, S044-07,
  S044-06, S044-08).
- **Every `--year` is a plausible tax year** (1900..next year): `audit`,
  `close-year`, `find-missing-history`, `taxjson-fees-sum`,
  `taxjson-missing-history`, `taxjson-reconcile-slips`,
  `taxjson-sum-income` (`audit --year 2204` printed an all-checkmark
  reconciliation of nothing; `--year 0` meant all years).
  `option_grant_timing_since` accepts 1900.. like `init --year` (audit
  S047-14, S047-20).
- **Schedule 3 / Form 8949 export:** under grant timing a written option
  and its buy-back count their contracts once in the units column (5
  contracts showed 10); crypto unit counts keep full precision (a
  0.00003 BTC sale showed 0 units); a write for a net debit shows no
  proceeds and the debit as an outlay (it showed the debit as proceeds
  and an invented ACB of twice it); a permanently denied superficial loss
  is worded for an affiliated person's purchase too; the 2025 crypto-line
  note follows the line routing itself. Form 8949 rows foot — (h) = (d)
  − (e) + (g) on the rounded cells — so part totals, the printed 8949 and
  the TXF agree to the cent. The stand-alone `taxjson-form-export`
  refuses gains rows in another currency (the native `*_raw_gains.json`
  summed USD and CAD under a CAD label), and `--csv` no longer leaves a
  truncated file after a failed write. `taxjson sum` FOR THE RETURN says
  when its cent-rounded rows differ from the gains files' unrounded
  total.
- **Stand-alone report tools refuse a non-gains file:** form-export,
  sum-gains, ccd-gains, leaps-gains and t1135 refuse a JSON with no
  `transactions` list or a pre-gains stage file (it rendered a $0
  Schedule 3 / GRAND TOTAL 0.00 at exit 0); `taxjson-gains` (stdin) and
  `taxjson-merge` refuse a document without the list.
- **T1135:** before Dec 31 a "below the threshold" verdict says "so far
  (books through DATE)" and the checklist keeps the step open (it read
  "no T1135 required this year" with a green [x] in September); the
  year-end column is headed with the books' last date while the year is
  open. Books in another currency than `--base-currency` (a native
  `_raw.json`, a USD-base book) are refused instead of tested against
  CAD 100,000 as if they were CAD; a non-CAD `--base-currency` warns.
  `--threshold` / `--detailed-threshold` must be finite numbers >= 0.
  `t1135.map` symbols match case-insensitively, and a COUNTRY word that
  is not an ISO-3 code or CA/EXCLUDE (EXCLUDED, CDN) is ignored with a
  did-you-mean warning instead of becoming a "country". Crypto rows
  under 1e-6 units add their cost (as the engine books them). A long
  option still held after its expiry is named. The report says the
  test covers the brokerage books only (foreign bank accounts and other
  property outside them must be added by hand), attributes the $250,000
  Part A/B line to the T1135 instructions, and `--help` carries the
  caveats.
- **reconcile-slips:** a slip whose currency column (T5008 Box 13) names
  another currency than the books is refused with a message naming Box
  13 (a USD Webull T5008 gave one MISMATCH per symbol, every amount off
  by the FX rate); an unreadable cost cell (`50000,00`, `nan`, `1 500.00
  CAD`) fails the check like an unreadable proceeds or quantity cell
  (the cost note vanished or came from a partial sum); a directory or a
  CSV with bytes cp1252 cannot decode is one error line, not a
  traceback; `--tolerance` must be a finite number >= 0 (nan or a
  negative turned every symbol into "off by +0.00", and the `taxjson`
  wrapper forwarded `-inf` as a separate token); `--help` lists the
  accepted column spellings; a tainted count reads "1", not "1.0". The
  checklist's T5/T3 step says the slip check is by hand and lists the
  known reasons divs-sum differs from the slips.
- **carryover: claimed_losses.txt and rounding.** A BOM is read, a
  claimed year outside 1900..next year is refused by name instead of
  becoming a phantom ledger row, a directory is a one-line error, and
  every ignored line is listed in the report and JSON and turns the
  checklist's carryover step to attention. A claim equal to the filed
  (per-row-rounded) Schedule 3 loss no longer leaves a cents
  carryforward or an "exceeds the losses" warning. A book whose rows
  are not in `--base-currency` is refused (audit S001-04, S027-10,
  S027-18, S027-19, S027-23, S028-00, S028-02).
- **Filed-year lock.** close-year also locks the amounts the export
  puts on each return line (Schedule 3 codes / Form 8949 part totals),
  so check-filed reports a move between lines or an outlay folded into
  a price; the lock's `proceeds` is documented as the engine's net
  proceeds; totals are rounded once over the accounts; filed/ being a
  file, a read-only project or a gains file that is not a JSON object
  is a one-line error; every OK names what the lock does not cover
  (interest, withholding, FX on cash) (audit R1-205, R1-281, S031-19,
  S031-20, S032-11).
- **checklist: no verdict from part of the books.** wash-reviewed is
  blocked when a taxable gains file is unreadable, missing for an
  account with inputs, or built for another year, and attention when
  the wash pass is stale; audit and form-export say "rebuild" for
  other-year or stale books instead of blaming phantoms; roc-entered,
  inputs-frozen and run-clean name an unreadable base book or .sum;
  missing-history counts every AFFECTS row (SAMPLC/B, `?` currency); the
  form-export check compares the unrounded rows with the .sum, so many
  rows of rounding no longer read as a mismatch; a US project checks
  for option positions left open past expiry; `sanity` names an
  unreadable book instead of "not an account"; `watch` ignores a state
  file that is not an object. Guidance: missing basis can understate
  or overstate; split-share corps report on a T5 (box 18 -> line
  17400) (audit R1-210, R1-338, S023-08, S066-15, S066-19, S067-04,
  S067-10, S067-11, S067-12, S068-06, S068-11, S068-16).
- **option-boundary cites the right law and sees more missing rows.**
  An assignment folds the premium under s.49(3) (call) or s.49(3.1)
  (put), never s.49(2); a buy-back loss cites IT-479R para 29 / 32
  (README, design note and Webull messages corrected too). A write
  still open after an expiry that falls inside the books (a January
  expiry in books that run into February) is ATTENTION, not "open".
  Writes and closes are dated on the project's `tax_date`, so a
  trade-basis Dec-31 write that settles in January is reported as the
  straddle the engine books (audit R1-39, R1-180, S075-00, S075-05).
- **`--year` is checked in taxjson-gains, form-export, t1135, explain
  and audit.** `--year 0` meant "all history" (form-export folded every
  year into one Schedule 3) and a 2-digit year silently matched
  nothing; the flag now takes 1900..next year, like `taxjson init`
  (S033-16).
- **`check-filed` and `handoff` refuse a lock closed under the other
  country.** A US-filed year was recomputed under Canadian rules and
  reported as drift with "amend or refresh the lock" advice (a refresh
  would have overwritten the US record); the lock's country is now
  compared and named, and a lock is recomputed on the date basis it
  recorded (CA-RPT-09, US-RPT-06).
- **`taxjson-form-export` needs `--country`** and refuses the other
  country's form (the standalone tool was not gated), and Schedule 3
  refuses gains files computed by the US engine.
- **The checklist's run-clean step flags unblended books.** With two or
  more taxable equity accounts and no blended pass (`run --account` on
  each, or a run stopped at pending elections) the filing figures are
  per-account ACB; run-clean now says so instead of "done", and every
  command that reports the run state carries it (audit S004-07).
- **Filing commands refuse partial or broken books.** A taxable account
  whose stage failed (inputs but no books) now stops `form-export`,
  `t1135`, `close-year` and `reconcile-slips` instead of a one-line
  warning with the account left out. A truncated taxable gains file stops
  `sum`, `estimate`, `instalments`, `t1135`, `form-export` and
  `close-year` with the file named (no traceback); an unreadable native
  file stops `fx-cash`, and `sum` says why the line-15300 FX note is
  missing. `sum`, `estimate`, `form-export`, `t1135`, `carryover` and
  `check-filed` warn when the books are not the clean result of the
  current inputs (validation errors, pending elections, inputs changed
  since the last full run); `close-year` refuses such books without
  `--force`, and refuses per-account (unblended) books from
  `run --account`. The checklist counts one validation error once.
- **tax_date is honoured everywhere.** `form-export`, the FOR THE RETURN
  block, `carryover` and `t1135` use the project's `tax_date` (a
  Canada project on "trade" dropped a Dec-31 sale settling in January).
- **carryover flags a locked year it disagrees with** (and the moving
  option_grant_timing_since default).
- **t1135:** an assigned written put's premium reduces the shares' cost
  and an exercised call's cost is added; same-timestamp rows follow the
  engine's order (buy before sell) so the threshold test no longer depends
  on file order; a split inside a trade's settle lag no longer strands
  cost; `t1135.map` survives a BOM, follows a ticker change and names an
  override that matches nothing; denied superficial losses missing from
  the cost columns are named. Books built for another year are warned
  about. The other user map files (distributions.map, yf_ticker.map,
  crypto_ticker.map, security overrides) read a BOM too.
- **reconcile-slips reads real slips.** A blank proceeds cell beside a
  cost (an option that expired worthless) is nil proceeds, and a
  worthless expiry with no slip row no longer fails the check. A written
  option booked twice under grant timing counts its contracts once. Slip
  symbols go through the project's ticker.map (SAMPLK ↔ SAMPLJ.TO), and broker
  option descriptions, share classes (`SAMPLC B`), UTF-16 files, French and
  T5008 box headings are read. Two listings of one root (SAMPLB.TO and
  SAMPLB.US) are no longer folded together. A row with amounts but no
  symbol, or an unreadable quantity, counts as not reconciled; a second
  column that also looks like quantity/proceeds/cost is refused instead
  of silently taking over. Books built for another tax year are refused
  with a rebuild message instead of "dropped CSV rows".
- **Filing checklist fixes.**
  - run-clean compares the inputs with what the last full run was built
    from (content, not dates): a deleted input, a corrected export copied
    with its old date, or a new `distributions.map` / `phantoms.json` /
    `ticker_extraction_overrides.txt` now says "inputs changed". It also
    flags an account with inputs but no report (a run that died on it, or
    a `run --account` of another account).
  - wash-reviewed counts a December loss that settles in January in the
    settle year, like every other denial total.
  - t5008 reconciles all slip files together (one per broker is normal;
    each file alone could never reconcile), finds `.CSV` files, reads
    only `inputs/slips/`, and names a non-CSV file there as not
    reconciled. `taxjson reconcile-slips` takes several slip files.
  - form-export's gain check allows for per-row rounding on a big book
    (a 6-cent residual over 831 rows was a false attention).
  - A done mark no longer hides a detector that crashed or had nothing
    to check. An unreadable `checklist.json` is reported by name and never
    overwritten. Changing `year` no longer brings last year's marks back
    at the next mark.
  - The fees step no longer sends trade commissions to line 22100 or
    calls data subscriptions deductible: line 22100 is the margin
    interest from the statements. The carryover step says what to record
    (Canada: the 100% loss; US: the Schedule D line 21 deduction).
- **check-filed recomputes a locked year the way it was filed.** The
  drift check used the current project's written-option timing, so a
  2026 project without `option_grant_timing_since` recomputed the 2025
  lock on close timing and advised amending a correct return. It now
  uses the timing the lock recorded and notes when the project differs.
- **One bad lock no longer switches off the drift guard.** A lock that
  cannot be read (bad JSON, a hand-edited file missing a count) made
  `run --strict` exit 0 and skip every other lock, and made
  `check-filed` crash with a traceback. Each lock is now checked on its
  own; a bad one is named and fails `--strict`.
- **A renamed account is reported, not recomputed from its old book.**
  A locked account that is no longer a taxable account in
  `taxjson.toml` was recomputed from its orphan `work/<name>_base.json`
  and the check said OK.
- **The lock records dividends and payments in lieu separately.** A
  dividend reclassified as a payment in lieu (different return line, no
  gross-up) now shows as drift; locks written before this keep
  comparing the combined income only.
- **reconcile-slips counts phantom-basis sales.** A sale reported by
  hand (phantom cost basis) was still shown as MISSING_FROM_COMPUTED;
  it now matches the slip with the "phantom basis included" note.
- **option-boundary: an open write expiring later this year is open.**
  A written option whose expiry is after today is an ordinary open row
  (premium recognised in the write year), not "expired … the broker
  export is missing it"; the checklist no longer turns `[!]` for it.
- **option-boundary follows renames and keeps the sign.** A ratio-1
  rename SPLIT of an option carries the written lot to the new symbol,
  so its buy-back is matched; a write whose commission exceeds the
  premium shows a negative premium, as the engine books it.
- **option-boundary: cash-settled index options are not folded.** An
  assignment whose underlying never trades as stock in the account
  (XSP, SPX) is reported as a cash settlement — premium in the write
  year, settlement loss in the close year, no T1-ADJ — instead of
  advising the removal of a premium the engine keeps.
- **option-boundary reads a lock's close timing.** When `filed/<year>.json`
  records close timing but this project puts that year's writes on grant
  timing, an expiry, buy-back or still-open write is ATTENTION (the
  premium is in no return: set `option_grant_timing_since` to the next
  year, or T1-ADJ to add it), and an assignment says no amendment is
  needed instead of "remove the premium … filed with it".
- **carryover says what its numbers leave out.** The ledger now notes
  that slip capital gains (lines 17400/17600; US Schedule D line 13) and
  the line-15300 FX gain on foreign cash are not in NET GAIN(LOSS), flags
  rows before the project year as rebuilt from this project's books and
  possibly partial, and `--claimed` / README state the units to record
  (Canada: the 100% loss, line 25300 x2 at 50%; US: Schedule D line 21).
- **claimed_losses.txt takes `1,234.56` and `$1,234.56`.** An amount
  written the way the return or notice of assessment prints it was
  dropped with a warning, so the carryforward kept the claimed loss. A
  malformed grouping is still refused.
- **carryover: an old claim no longer eats a later loss.** A
  `claimed_losses.txt` amount recorded for a year the books show no loss
  for was held and taken by the next loss in ANY later year, lowering
  its carryforward. A loss carries back only 3 years (ITA 111(1)(b)), so
  a claim left unmet past that window now stays unmatched, with a
  warning, and the carryforward is untouched.
- **T1135: a futures contract has no cost amount.** A long futures
  position was counted at its full notional (one CL contract added about
  80,000 CAD to the threshold test and could flip "filing required").
  Plain futures are now listed with a nil cost and a note; options on
  futures still count at their premium.
- **`taxjson t1135` applies phantoms.json.** Positions whose early
  history is cut off read as shorts that later real purchases covered at
  zero cost, so those purchases never reached the max-cost or Dec-31
  columns (2024 books: 313k of max cost missing). The report now adds
  the same phantom openings the gains stage adds, and flags a still-held
  phantom "cost understated".
- **The filed-year lock counts phantom-basis sales.** Its `tainted`
  count read a flag pipeline gains files never carry, so it was always 0
  and a phantom-basis sale appearing or disappearing never showed as
  drift. New locks count them; older locks skip that one comparison.
- **Phantom-basis sales no longer vanish from `form-export`.** Sales
  drawn on `phantoms.json` openings (cost unknown) were left out of the
  Schedule 3 / Form 8949 / TXF output with no warning, and the checklist
  marked the export complete. They are still not in the computed rows or
  totals, but form-export now warns with each one's proceeds, lists them
  in a MANUAL REPORTING section (MANUAL rows in the CSV), and the
  checklist keeps the step open. `t1135` names them too.
- **`check-filed` compares every taxable account, not only the locked
  ones.** An account added (or renamed) after `close-year` was never
  recomputed, so its dispositions were missing from the comparison and
  the check said OK. It is now recomputed in the same blend and reported
  as drift when it has activity in the filed year.
- **Year-to-year hand-off (`taxjson handoff`).** `close-year` now also
  records every sale, the positions and cost at Dec 31 (with the
  superficial-loss deferrals the full history decided), and the trades
  that settle in January; `--filed-dispositions` stores what a return
  prepared elsewhere actually reported. `taxjson handoff`, run in the
  next year's project (`[settings] prior_year_record`), checks the
  opening positions and cost, that every January settlement is booked
  here once, and that no sale is reported in both years; a cost
  difference is listed with its two consistent choices (keep the year as
  filed, or amend it). It is a `checklist` step and `edge-cases` points
  to it. On real books it found Dec-31 sales that were reported in no year.
- **`close-year` no longer locks the wrong year**: after bumping
  `[settings].year` without a rebuild, it wrote LAST year's books into
  `filed/<new year>.json`. It now compares the year recorded in each
  work/ gains file with `[settings].year` and refuses on a mismatch
  ("rebuild with `taxjson run` first"); `sum`, `estimate` and
  `form-export` print a loud WARNING instead.
- `inputs/slips/` (the T5008/1099-B CSVs the checklist asks for) no
  longer triggers the "has no [accounts.slips] section" warning; CSVs
  in a SUBfolder of an account's inputs now warn that they are not read.
- `form-export`: `--out`/`--box` without `--form txf` are refused
  instead of silently ignored.
- **`taxjson t1135`**: a split held in two taxable accounts was applied
  once per account (the pool doubled — cost at Dec 31 overstated);
  the walk now dedupes split events like the gains engines. Crypto
  (suffix-less symbols) is reported under a `CRYPTO` country bucket
  with a "check where held" note instead of the unclassified `??` —
  crypto on a foreign exchange is generally specified foreign
  property; map it in `t1135.map`.
- **`taxjson checklist`**: detectors no longer say "done" on nothing —
  missing-history with no base files and option-boundary with no taxable
  book are blocked; option-boundary reads `--json` and reports ATTENTION
  rows and an unset `option_grant_timing_since`; form-export now compares
  its totals with `sum`'s FOR THE RETURN block and the taxable accounts'
  realized gain (it compared nothing); a T5008 finding names the actual
  mismatch counts and symbols instead of the report's last note line. A
  step marked `--done` whose detector says attention now shows `[!]` with
  the mark and note beside the finding and keeps the list open (it
  counted as done). `--undo` with no mark and `--reset` with no file say
  so; `--note` without `--done`/`--skip` is an error; mark commands honour
  `--json`; `--only` with an unknown id lists the ids; `--walk` re-prompts
  on an unknown key with the key list and exits 1 (after the summary)
  when quit or left with open steps. US projects get the US step names
  (1099-B, Form 8949 / Schedule D, 1099-DIV, Form 1116, §988) and `n/a`
  for option-boundary, T1135 and the NOA; a project with no taxable
  account gets `n/a` rather than blocked for the taxable-only steps.
- **`taxjson carryover` ignored the option-timing settings**: the ledger
  ran every year on close timing while the returns were filed on grant
  timing (a grant-timing year showed its close-timing figure). The
  wrapper now passes `option_premium_timing` / `option_grant_timing_since` /
  `option_buyback_loss_superficial` (new `--option-*` flags on
  `taxjson-carryover`), as do the `list --date` as-of recompute and the
  raw base-currency holdings pass.
- **`taxjson option-boundary`**: a contract past its expiry date within
  the tax year with no expiry/assignment row is flagged "expired but no
  expiry/assignment row — check the export" instead of listed as open;
  with no taxable book in work/ the command exits 1 ("NOT CHECKED")
  instead of printing the all-clear; `--json` adds `attention`, `amend`,
  `since_explicit` and `missing_books`.
- **Schedule 3 line routing** (`form-export --form schedule3`, `taxjson
  sum`): every disposition went on line 13199/13200 ("section 3, publicly
  traded shares"). Rows are now routed by property type to the 2025 form's
  Part 3 lines — line 4 shares and fund units (13199/13200), line 6
  options, futures and other properties (15199/15300; T4037 lists options
  there), line 7 crypto-assets from `crypto = true` accounts (15200/15301;
  15199/15300 for 2024 and earlier) — with per-line totals in the text,
  CSV (`line`, `proceeds_line`, `gain_line`, `property`, `denied` columns)
  and JSON (`lines`, per-code `totals`). Standalone `taxjson-form-export`
  takes crypto books with `--crypto FILE`.
- **FOR THE RETURN block**: one row per Schedule 3 line (was per
  account; the per-account split stays in `--json`), each with its line
  number and codes. The footer said COST *includes* the denied
  superficial losses — backwards: the ACB shown is REDUCED by the denial
  so proceeds − ACB − outlays is the allowed gain, and the denied amount
  goes onto the replacement's ACB. form-export's per-row ACB now uses the
  same convention, so every row foots (a fully denied row's proceeds − ACB −
  outlays used to differ from its 0.00 gain). US projects show Form 8949's own Part I/II (d) proceeds, (e)
  cost, (g) adjustment and (h) gain — the block used to show Schedule 3
  style proceeds and a cost net of the wash adjustment that matched
  neither 8949 column. A line under the block gives the `fx-cash`
  s.39(1.1) estimate (line 15300), or a pointer when it cannot be built.
- **T1135 cost includes denied superficial losses.** The cost walk now
  adds the amount the engine denies under s.54 to the replacement
  property's cost (s.53(1)(f)), as the ACB does, so the year-end and
  maximum cost columns and the $100,000 threshold test no longer
  understate after a superficial loss in the project year.
- **Schedule 3 outputs name the slip capital-gain lines.** `sum`'s FOR
  THE RETURN block, `form-export` and docs/filing.md now say that
  capital gains on T3 (box 21, line 17600) and T5/T5013 (box 18, line
  17400) slips are not in their rows and are entered from the slips.
- **A spin-off booked at $0 keeps the checklist open.** The `elections`
  step now needs attention while any taxable spin-off or merger is
  booked at $0 (`fmv_per_share=0`, the "defer" value), and `taxjson
  elect --set ... --hint fmv_per_share=0` says what it books.

### Planning: wash radar, harvest, buy/sell-check, estimate, instalments, web UI

- `taxjson estimate` (Canada) says on the eligible-dividends row and in
  its printed assumptions that a Canadian trust's distribution (ETF, REIT
  or fund units) is grossed up as an eligible dividend; the T3 decides
  (re-audit A2-0828; CA-EST-TRUST).
- US wash radar, sell-check, buy-check and harvest no longer call a
  stock dividend a "Recent buy" and warn that a partial loss sale would
  be a wash sale: a stock dividend is not a purchase for §1091
  (US-STKDIV-01; re-audit A2-0550). Canada is unchanged (a $0
  acquisition that counts for s.54, CA-STKDIV-01).
- The `taxjson-harvest` console script run on a project's
  work/*_gains_wash.json without --ticker-map finds the project's
  ticker.map (next to the inputs or the folder above), so a TOBASE-renamed
  option is quoted as the contract held, in its own currency, as
  `taxjson harvest` does (re-audit A2-0885).
- US estimate: a capital loss carryover keeps its term — --other-losses
  is the short-term carryover (Schedule D line 6) and the new --long-
  term-losses (or [estimate] long_term_losses) the long-term one (line
  14); each offsets its own term's gains first. A long-term carryover
  used to be applied to short-term gains first, understating the tax.
  The --other-losses help no longer cites the Canadian 50% inclusion in
  a US project; --long-term-losses is refused in a Canada project (US-
  EST-CARRY-TERM; re-audit A2-0481, A2-0809).
- tax-logic states every rule the estimate and instalments apply:
  Canada's loss netting and s.111(1)(b) cap, deductions and carrying
  charges (50% in the AMT base), the BPA phase-down, the provinces
  modelled (ON surtax and Health Premium; QC refused), the provincial
  foreign tax credit (T2036), the AMT base, the rate-table vintage
  fallback (both countries), that a Canadian trust's distribution is
  counted as an eligible dividend because the T3 split is not in the
  export (also in the printed assumptions), and the instalment rules —
  the ITA 161(4.01) least-cumulative schedule, credit interest
  offsetting but never refunded, the $25 floor, the s.163.1 penalty and
  an unknown prior year assumed to meet the test (CA-EST-*, US-EST-
  VINTAGE, CA-INST-*; re-audit A2-0821, A2-0824, A2-1463, A2-1464,
  A2-0828, A2-0825).
- `taxjson watch` states its scope like the other planning tools
  (tax-logic CA-PLAN-04 / US-PLAN-04, re-audit A2-0909): a change report
  ends with the country's scope line (a CLEAR is safe only as far as
  the project's accounts show — a spouse's or controlled corporation's
  purchase is not seen), and `--json` carries `scope_note`. A quiet run
  stays silent.
- `buy-check` / `sell-check`: in a US project a bare coin held in a
  crypto account (`buy-check ETH`) is answered as outside the wash-sale
  rule instead of taking SAMPLT.US's verdict or saying "no tracked taxable
  position"; a LOCKED row (a registered / IRA buy in the window) no
  longer says a full exit escapes the rule; a Canadian call bought
  after a share loss states the denial per share and per 100-share
  contract (US-PLAN-05, CA-PLAN-02; audit A2-0408, A2-0749, A2-0750,
  A2-0752, A2-1340).
- Web UI: `accounts = 5` (or a list) in taxjson.toml is the one-line
  config error `taxjson serve` gives for the other bad shapes, and a
  dangling work/*_base.json symlink no longer breaks the wash-radar page
  (A2-0807, A2-0787).
- `taxjson watch`: a .watch_state.json whose inner radar entries or
  harvest_now are the wrong shape records a new baseline with a warning
  instead of crashing with exit 1 (A2-1430).
- `taxjson-harvest` and `taxjson-corp-actions` default `--base-currency`
  to the country's currency (a US harvest refused the project's own USD
  books). `taxjson spinoffs` labels amounts in the country's currency
  when `base_currency` is unset, and flags a saved election of the other
  country (a `rollover_s_86_1` left in a project switched to usa) as
  WRONG-COUNTRY instead of describing it in Canadian law; `taxjson elect
  --pending` lists such elections instead of "No pending elections".
- Wash radar (Canada): rows that settle on the same day are replayed in
  trade-date order, as the engine does — a Friday sale before a holiday
  is no longer read as a loss against Monday's buy (A2-0443). A written
  option's buy-back loss outside the gains files' year is exempt
  (CA-SL-11) unless `option_buyback_loss_superficial = true` (A2-0442).
- US wash radar / buy-check: a futures contract or an option on one is
  outside §1091 (US-WASH-18) — no COOLING/BLOCKED re-entry date, buy-check
  is no longer UNSAFE, and the futures-option note no longer speaks of
  "shares" (A2-0435, A2-1343). After a short-cover loss a buy is not a
  replacement (§1091(e)); the radar names a re-short instead (A2-0436,
  A2-1369).
- Radar wording: a US LOCKED row says how many shares the IRA bought and
  what it holds now, not "still holds" (A2-0751, A2-0754); a position held
  only in sheltered accounts names its recent purchase instead of "No
  recent buys" (A2-1370).
- sell-check relays the radar's warn-only flags (warrant, adjusted-series
  call, futures option, US long call) on every verdict, and harvest stars
  the ADVISORY cell and lists them (A2-0434, A2-0445, A2-1341).
- `taxjson.toml`, `phantoms.json` and holdings TOML files saved with a
  UTF-8 BOM are read by wash-radar, watch, buy-check, sell-check,
  sanity and taxjson-export too; fetch accepts a BOM'd IB Flex download
  and a BOM'd Questrade fetch file.
- `taxjson harvest` in a US project shows an IRA purchase made within
  the window in SH_ADD even after the IRA sold it (an IRA buy washes a
  loss for good whether or not it is still held), and the SH_ADD legend
  states each country's own rule: in Canada only units the registered
  account still holds 30 days after the sale deny the loss (re-audit
  A2-1298, A2-1299).
- In a US project the web holdings pages and the what-if basis note
  describe the basis as FIFO per account before the wash-sale pass;
  they used to cite the s.47 blend and the filing ACB (re-audit
  A2-0755, A2-1250, A2-1300, A2-1327, A2-1339, A2-1353, A2-1374,
  A2-1375, A2-1376).
- `[settings] year` is checked in the shared settings check, so `taxjson
  serve` refuses 1850, 2024.0, true or "2024" as every CLI command does
  (A2-1373).
- `taxjson scan` no longer prints "No findings — clean scan." (exit 0)
  when an account's holdings report or raw book is missing: it names
  each account it could not scan and exits 1 (re-audit A2-0404).
- Wash radar, sell-check, buy-check, harvest's ADVISORY: each replacement
  unit backs one denial, as in the engine (CA-SL-08) — a rebuy an earlier
  loss used up no longer makes a second, false VIOLATION or a false
  LOCKED for a sale today, and a sale split into fills no longer counts
  its shared rebuy twice (re-audit A2-0009, A2-0038, A2-0377, A2-0689,
  A2-0691; US: an IRA buy the engine already matched is not 'at risk'
  again). Quantities across a split are compared in today's units
  (A2-0382); a long call counts at its declared contract size (A2-0373);
  a class-share option root (SAMPLD for SAMPLD.B.TO) names its class line; a
  futures option on the loss's own contract is a note to check by hand,
  never a VIOLATION (A2-0378, A2-0690, also in edge-cases).
- Wash radar: once the last rescue trade date has passed, a VIOLATION
  says the loss is denied (JSON `deadline_passed`) instead of 'Sell ...
  by <yesterday>'; sell-check no longer answers ACTION/UNSAFE for it and
  safe-to-sell shows DENIED (A2-0688, A2-0368). The sell-by date walks
  back on the listing's calendar (A2-1183) and a futures rescue settles
  on its trade date (A2-1181).
- Wash radar: warrants, adjusted-series calls and futures options bought
  in a loss's window (and, US, a long call bought in the last 30 days for
  a sale today) are noted for a manual check, carried to sell-check;
  buy-check flags buying one after a share loss instead of 'no wash
  exposure', and a US long call after a share loss is a note, not UNSAFE
  (A2-0129, A2-0687).
- Wash radar: a short position is described as a short (cover, re-short;
  a US short's trigger is a new short sale, not an IRA purchase)
  (A2-0371, A2-0686, A2-1184).
- Wash radar's own pool: a Canadian trust's return of capital moves to
  its record date and a return of capital above the ACB floors it at nil
  (A2-1174, A2-1178, A2-0372); a coin rebuy under 1e-6 units is a holding
  (A2-1168); a `.tt` row dated tomorrow in a settle-date project is a
  trade made today (A2-0130).
- buy-check: buying back a written call you are short is SAFE (it
  acquires nothing) (A2-0370). buy-check / sell-check: the 'last loss
  sale' line shows only losses the queried trade can affect (A2-1172);
  a bare-array book no longer crashes them (A2-1180); an unreadable
  ticker.map stops them, as it stops `run` (A2-0683).
- buy-check, sell-check, watch, instalments, wash-sales and `list --date`
  relay a failed child's error line instead of the first 200-400
  characters of its traceback (re-audit A2-1190).
- harvest --options values an option at the contract size its rows
  declare (a x10 mini contract is no longer valued at x100), as `list`
  and the holdings export do (re-audit A2-0367, A2-1177).
- harvest: a VIOLATION whose rescue deadline has passed is no longer
  counted as harvestable now; it waits for the registered buy to age
  out (re-audit A2-0365).
- harvest quotes a coin under the project's crypto_ticker.map spelling,
  the one the books were priced with (re-audit A2-0364).
- harvest / price chain: an LSE (.L) quote that does not state its unit
  (pence or pounds) is left out with a warning instead of valued as
  pounds; a live tier's unit-less quote falls through to the next tier
  and is never cached (re-audit A2-0379, A2-0692).
- harvest --json carries the planning-tool scope note (re-audit
  A2-1171).
- US harvest: a crypto account's losses are claimable now with no
  wash-sale advice (US-WASH-13, new US-PLAN-05); an open short shows ST,
  never LT (US-HOLD-03) (re-audit A2-1173, A2-1176).
- `taxjson serve` on an IPv6 address (`--host ::1`, `[::1]`, or a LAN
  IPv6 address) answers requests instead of refusing every one with 400
  "Invalid host header", and prints the URL with the address in brackets
  (re-audit A2-0695, A2-1186).
- Web: the wash-radar page shows the scope note (verdicts cover the
  project's own accounts only, CA-PLAN-04 / US-PLAN-04); a radar sidecar
  without "sections" or with a row field of the wrong type, and a
  holdings row whose `trades` is not a list, are an error banner instead
  of "no report yet", the stale .rpt, or an HTTP 500; the stale-rate
  label uses harvest's threshold (7 days, not 4); and the freshness
  fallback counts the same input files as the checklist (a Finder
  .DS_Store no longer marks the dashboard stale) (re-audit A2-0374,
  A2-0696, A2-1187, A2-1188, A2-1179, A2-1185).
- Web what-if: an option is priced at the contract size the book's rows
  declare (a x10 mini option was priced at x100, a 10 loss shown as a
  350 gain), and a plain futures contract is refused instead of priced
  at x1 and dated as an equity T+1 sale; the simulated sale settles on
  the listing's market calendar, not the calendar of the currency the
  price was typed in; a Canadian trust's return of capital is booked on
  its record date first, as the run does; a registered account with
  inputs but no built book is named in the warnings; and the engine's
  warn-only option-replacement flag (a call bought in the window) is
  listed in the result (re-audit A2-0131, A2-0384, A2-0132, A2-0375,
  A2-0437, A2-1189, A2-1175, A2-0383, A2-0687).
- `estimate` (USA): §1256 P/L (futures, futures options, broad-based
  index options) is still taxed as short-term, but the estimate now
  names the amount in a NOTE (`section_1256_gain` in --json) and its
  Assumes line says the Form 6781 60/40 split is not modelled
  (re-audit A2-1124).
- `estimate` (Canada): a dividend or s.260 payment in lieu from a
  Canadian issuer on a US listing (CA ISIN in the books) is an eligible
  dividend, not a foreign one with an assumed 15% credit — the estimate
  uses the engine's issuer test (ISIN, else listing) instead of the
  listing suffix (re-audit A2-0319, A2-0662).
- `instalments`: a payment made before January 1 is accepted as a
  prepayment of the project year's instalments when its row says
  `tax_year = YEAR` (credited from January 1, as the interest model
  already did); an undesignated prior-year date is still refused and
  the message names the key (re-audit A2-0648).
- `sum` / `estimate`: --other-income / --other-losses (and their
  [estimate] keys) are checked by one guard that names the flag or key
  it refuses (re-audit A2-1123); the --deductions / --carrying-charges
  guard is pinned by a test that tells it from the library's check
  (A2-1122).
- `sum` / `estimate`: an unreadable sheltered account's gains file is
  refused like a taxable one (it silently changed the SHELTERED and ALL
  ACCOUNTS totals), and in a US project a disposition with no ST/LT
  term stops `sum` the way it stops `form-export`, instead of printing
  RETURN 0.00 for every account (re-audit A2-1119, A2-1120).
- Canada: the gains inventory carries `last_acq_settle`, the latest
  acquisition's settle date, and `taxjson harvest`'s TX_ADD / SH_ADD
  columns measure the 30-day window from it (s.54 counts settle dates);
  they showed the trade date, off by a weekend near day 30 (A2-0958).
- **Wash advice: the still-held test, coins, US crypto.** buy-check no
  longer says any loss sale within 31 days of a buy "would be
  superficial" or that a rebuy always cancels a loss: in a Canadian
  project both hold only if the bought shares are still held 30 days
  after the sale (s.54 'superficial loss' (b)); a full exit is fine
  (the radar's BLOCKED legend says the same). `buy-check ETH` means the
  coin when the books hold one (it mixed in SAMPLT.US's verdict and
  nothing could ask about the coin alone). The "last loss sale" line
  says which date it shows (settled / traded), includes a loss routed
  to manual reporting (phantom basis), and warns when a gains file
  cannot be read. In a US project, `wash-sales --explain` and
  `wash-radar` no longer apply §1091 to crypto accounts (the pipeline
  does not) (audit S048-19, S049-15, S047-06, S047-05, S047-02,
  S047-03, S046-10; pin S047-08).
- **estimate / instalments say what they leave out**: the FX result on
  foreign cash (line 15300; US §988) and slip capital gains, and for
  instalments CPP/EI on self-employment earnings (`NOT MODELLED`
  notes, JSON `not_modelled`). `[instalments]` amounts must be TOML
  numbers (`"5_000"` was read as 5000). The withholding credit is
  summed in a stable order (audit S048-12, S043-15, S043-05, S043-20;
  test pins S042-20, S043-04, G1-3, G1-11).
- **scan: no false all-clear, no egress while offline.** An unreadable
  holdings report stops the scan (it warned, then printed "No findings
  — clean scan." with exit 0); the unused-ticker.map-rule note is
  skipped with a warning when a parsed source cannot be read (it listed
  live rules as unused); `scan --online` honours `TAXJSON_OFFLINE=1`
  (skips the Yahoo probe with a note — SECURITY.md now says so); the
  online findings come in a stable order (audit S049-10, S042-10,
  S042-12, S048-00, S042-13).
- **`taxjson serve`.** The Host allowlist is loopback names only (the
  test client's `testserver` let a rebinding page read the books);
  `taxjson.toml` hot-reload notices an edit that keeps the mtime;
  `--token` requires the per-run token on a loopback bind too, and
  SECURITY.md says other local users can reach a loopback server
  (audit S078-10, S078-12, R1-346).
- **US estimate (experimental): NIIT and the loss carryforward.** The
  up-to-$3,000 capital loss deduction now reduces net investment income
  (Form 8960 line 5a; NIIT was up to $114 too high), and the
  carryforward shown counts as used only what taxable income absorbs
  (Capital Loss Carryover Worksheet line 4: zero other income carries
  the whole loss). The US `--claimed` guidance says the same. tax-logic
  US-EST-NIIT-LOSS, US-EST-CARRY-TI (audit S077-24, S078-01, S078-02).
- **Canada estimate: what it does not model is stated** — credits
  other than the BPA, the OAS recovery tax, and AMT adjustments outside
  the books (README, KNOWN_ISSUES, the printed assumptions) (audit
  S077-15, S077-17, S077-20; S077-22: the README now says the US
  estimate gives no foreign tax credit).
- **instalments.** When instalments are not required (s.156.1(1))
  the JSON carries no shortfall, interest or penalty (it said
  required_at_all=false next to them); the four quarters add up to the
  year's figure to the cent, so paying exactly the net tax is not
  "behind by 0.01"; a configured `prescribed_rates` schedule that
  starts after January 1 says the earlier days assume its first rate
  (audit R1-222, S034-14, S034-15).
- **Wash radar / safe-to-sell: a small crypto lot gets its advisory.**
  A 0.0009 BTC position (about $120) is held for the superficial-loss
  test, as in the engine, but the radar's 0.01-unit display threshold
  showed it with no verdict. Crypto now uses the engine's threshold.
- **harvest: an input it cannot read stops it.** A missing or truncated
  gains, `--sheltered` or `--radar` file now exits 2 naming the file. It
  used to print "No open positions." or show SH_QTY/SH_ADD as '-' (the
  columns that warn of a permanent denial) at exit 0. A native-currency
  `_raw_gains.json` is refused: its USD cost read as CAD showed the FX
  factor as a gain.
- **harvest TOTAL PCT** divides by the gross capital at stake; a short's
  credited proceeds no longer net against long cost (every row -10% used
  to total -50%). JSON `totals.gross_cost` is new.
- **taxjson-harvest finds the project's yf_ticker.map** next to the
  inputs or in the project root above work/, not only in the current
  directory.
- **Wash radar, sell-check, buy-check and safe-to-sell say what they
  cannot see.** Their verdicts cover the project's own accounts only; a
  purchase by a spouse or common-law partner or a controlled corporation
  (Canada: affiliated persons, s.251.1; US: IRS Pub. 550) also denies a
  loss. Each report now says so in one line. tax-logic CA-PLAN-04 /
  US-PLAN-04. The web what-if shows the same line under its verdict.
- **Wash radar: a sale whose commission exceeds its gross is a loss.**
  Its proceeds are negative, as the engine books them. The radar used
  abs() and called the loss a gain, so a superficial loss had no
  VIOLATION and no rescue date. A return of capital on a short position
  now lowers the short's gain, as in the engine.
- **Wash radar: crypto rescue deadlines are not walked back through
  T+1.** A coin settles on its trade date, so the last day to sell is
  the settle bound itself, weekends included; the row says "units", not
  "shares".
- **Wash radar wording.** CAUTION says a loss sale of any size is clean,
  not only a full exit. BLOCKED, RISK and buy-check say that a rebuy (a
  DRIP too) denies the loss only on as many shares as it buys, and
  BLOCKED / buy-check print the amount per unit. `taxjson watch` reports
  these rows as changed once after the upgrade.
- **Wash radar input.** A book row the engine would refuse (a
  non-numeric quantity, a missing date, a trade with no amount) stops
  the radar and safe-to-sell with one line naming the file and row. It
  used to print a traceback, or skip the row silently. A bare-array
  book is accepted.
- **buy-check / sell-check "last loss sale" line** dates the loss on the
  project's window basis (trade date in a US project).
- **Web UI: config and report errors are shown, not hidden.** `taxjson
  serve` refuses the account names `taxjson run` refuses (a
  `[accounts."../../x"]` name read a holdings file outside the project)
  and prints one line — no traceback — for a non-UTF-8 or refused
  taxjson.toml. `/healthz` reports a taxjson.toml edit that no longer
  loads (`ok: false`, `config_error`). A corrupt or wrong-shape
  `wash_radar_<acct>.json` or `<acct>_holdings.toml` is an error banner
  instead of a silent fall-back to the stale .rpt or an HTTP 500.
  `/api/whatif` for an unknown account and a holding page for a symbol
  the account does not hold are 404s; the what-if and holding pages
  accept a symbol in any case.
- **The wash radar and the checks built on it apply the US rule in a US
  project.** `wash-radar`, `sell-check`, `buy-check`, `harvest`, `watch`
  and the web radar applied Canada's s.54 test to US books: windows on
  settlement dates, a replacement that "rescues" the loss if sold before
  day 30 (sell-check said ACTION), and a long call as replacement
  property. In a US project each recent loss's verdict is now the US
  engine's own, run on the same books as of the date: trade-date windows,
  purchases in every account including IRAs, no still-held test. A
  washed loss shows as WASHED with no rescue advice (no sale undoes a
  wash sale); a long call is a note. Canada is unchanged (tax-logic
  CA-PLAN-01/02, US-PLAN-01/02).
- **Web what-if (US) sees every taxable account.** A US what-if ran on
  the account's own book only and missed a sibling account's purchase in
  the window; it now simulates with every taxable account and the IRAs
  as wash-sale context, on the account's own FIFO basis (US-PLAN-03).
  The result page names the rule of the project's country.
- **Harvest prices the right thing in the right currency.** USD-traded
  TSX units (`SAMPLF.U.TO`) are valued in USD and spelled `SAMPLF-U.TO` for
  Yahoo; trust units (`SAMPNC.UN.TO` -> `SAMPNC-UN.TO`) and US class shares
  (`BF.B` -> `BF-B`) get Yahoo's spelling; an LSE quote Yahoo gives in
  pence is converted to pounds (it was valued 100x); a quote whose
  currency cannot be told (a `yf_ticker.map` override to `.DE`, `.T`,
  ...) is omitted with a warning instead of treated as USD; and
  `harvest --crypto` looks coins up as Yahoo crypto pairs (`ETH-USD`),
  never as stock tickers, and skips IBKR for them. `TAXJSON_OFFLINE`
  now also stops the IBKR option-price lookup, and a cache miss refuses
  as for stocks.
- **buy-check / sell-check read broker spellings and Montreal option
  roots.** SAMPLD-B, "SAMPLD B", SAMPLD/B and SAMPLD-B.TO are read as SAMPLD.B(.TO) (they
  answered SAFE beside a loss on SAMPLD.B.TO), and an option on a root that
  names no share listing but exactly one class share of it (RBC's
  SAMPLD271217C00030000.TO for SAMPLD.B.TO shares) is in that share's class
  (audit S007-02, S047-01).
- **The wash tools name a taxable account with no books.** wash-radar,
  watch, buy-check and sell-check warned about nothing when a configured
  taxable account's base book was missing, so a sibling's recent buy read
  as SAFE (audit S046-11).
- **The web radar opens on the COMBINED view** when there are two or
  more taxable accounts, and a per-account view says it sees only its own
  book (it read "CLEAR — safe to sell at a loss" while a sibling's buy
  made the loss superficial) (audit R1-229).
- **Net tax owing guidance names the right lines.** The init template,
  README and the instalments report said "line 48500 minus withholding";
  48500 also subtracts the instalments paid, so following it read "no
  instalments required". They now give CRA's instalment-chart
  definition (42000 + 42200 + 42800 (+ 43200) minus 43700 and the
  refundable credits) (audit R1-215).
- **harvest: claimable-now losses and option marks that match the
  books.** A LOCKED position counts the part of its loss a sale today
  keeps as claimable now (only the units a registered account bought in
  the window and still holds wait for the clear date); a VIOLATION's
  last rescue day reads `sell-by:…,+0d`, not "deadline passed"; a
  written option whose premium was taxed at the write (grant timing)
  shows the buy-back's whole cost as the loss (the engine's inventory
  now carries `recognised_premium`); an option the pipeline renamed via
  ticker.map (TOBASE SAMPLK.US SAMPLJ.TO) is quoted as the contract actually
  held, in its currency; and when the wash-radar reports are older than
  the books (after `run --account`), harvest runs the radar live instead
  of showing a registered-account lock as CLEAR (audit R1-230, R1-232,
  S033-24, S034-11, S038-09).
- **`taxjson-safe-to-sell` reads the wash radar.** Its own position walk
  missed today's (unsettled) buys, booked short covers as long lots and
  ignored ticker renames; quantities and statuses (SAFE, SAFE*,
  FULL-EXIT-ONLY, LOCKED, PARTIAL, VIOLATION) now come from the radar,
  and a bad `--date` is a usage error (audit R1-233, S007-09, S050-03).
- **The wash radar follows the engine's per-holder superficial-loss
  rule.** A loss is superficial only for units a holder — your taxable
  accounts together, or one registered account — bought inside the
  ±30-day window and still holds. A registered account's shares held
  before the window no longer turn a full taxable exit into a
  "PERMANENTLY denied" EXITABLE/VIOLATION, a registered buyer that has
  sold out no longer LOCKs the name, a new short sale or written option
  is no longer a "recent buy" or a trigger (Canada), a long rebuy now
  triggers a short-cover loss, and a replacement under 0.01 units
  (crypto DCA) still counts. VIOLATION names who must sell what; LOCKED
  says how many shares' loss a sale today would lose and that the
  registered account can still defeat it by selling within 30 days.
  `sell-check` answers PARTIAL (exit 1) when only part of a LOCKED
  position is at risk and ACTION for a violation only taxable shares
  back. Also: an exercised option is no longer booked as a loss on the
  option, a sale executed before a split that settles after it is
  re-denominated like the engine does, own-account registered moves keep
  each account's balance, `country = "usa"` projects get §1091's
  re-short and IRA rules, and an unreadable `taxjson.toml` stops
  wash-radar / buy-check / sell-check / watch instead of treating every
  book as taxable (audit R1-231, R1-232, R1-234, R1-241, S047-09,
  S048-13, S053-14, S053-20, S054-00, S054-03, S054-04, S054-08,
  S054-15, S054-20, S055-01).
- **Web what-if follows the filing.** A native price converts at the
  latest rate on or before the sale date (named with its date when it
  is old), not a hardcoded 1.35, and a currency with no rates is
  refused (audit R1-149). The simulated sale settles like a real one,
  so the superficial-loss window runs on the settle date and a Dec-31
  sale says it is a next-year disposition (R1-197). A Canadian project
  with two or more taxable accounts is simulated on the blended s.47
  pool, as filed (R1-258). A negative quantity buys to cover a short
  position; a sale on a short is refused with that hint (S079-00).
- **Web pages: config, radar and freshness.** A `taxjson.toml` the
  server cannot reload (a mis-cased type, a quoted boolean) is shown as
  an error instead of ignored, and quoted booleans are refused
  (S078-15). A VIOLATION's rescue deadline day reads "sell TODAY", not
  "deadline passed" (S079-06). A wash-radar report older than the books
  (after `run --account`) carries a stale warning on every row
  (S038-09). The "inputs have changed" banner sees the project-root
  maps and compares against the oldest per-account report, like the
  checklist (S078-21).
- **`scan` US-LISTING respects DISTINCT and the US line's own dividends.**
  A US stock was called a "Canadian issuer held via its US listing" when
  any `.TO` symbol shared its root, even one `ticker.map` declared
  `DISTINCT` (a CDR), and a non-paying US line borrowed the `.TO` line's
  dividends. The check now needs the US line itself to pay, and a
  `DISTINCT` ruling silences it (audit S042-06, S049-09).
- **instalments passes the estimate's warnings through** (unreadable or
  other-year books, excluded tainted sales) and prints the rate-vintage
  note for a year before the built-in tables.
- **Canada estimate keeps its sign.** Eligible dividends at a low
  bracket can lower the tax on the other income; the estimate (and the
  AMT total and average rate) now shows that as a negative figure — a
  saving — instead of flooring it at 0.00 under a `WITH - BASE` trace
  that was negative (R1-47).
- **Unused foreign tax credit reaches the province.** Foreign tax the
  federal tax cannot absorb is credited against provincial tax (form
  T2036, limited to provincial tax x foreign income / net income)
  instead of being dropped; the estimate and the current-year
  instalment basis were overstated for foreign-dividend-heavy, low-tax
  years (S077-16).
- **Estimate discloses what it leaves out.** A NOTE names the headroom
  a prior-year minimum tax carryover (T691 Part 8, line 40427) could
  use when AMT does not bind — the carryover is not modelled
  (KNOWN_ISSUES); the assumptions line no longer sends interest to
  views that never show it (R1-218, S078-00).
- **Instalment interest on the least amount due by each date.** ITA
  161(4.01) deems the requirement on each due date to be the least
  cumulative amount any method (current-year, prior-year, CRA reminder)
  calls for by that day. `taxjson instalments` priced each method as a
  whole year and took the cheapest, which overstated interest and the
  s.163.1 penalty whenever the cheapest method changed between dates.
  The report now shows the mixed schedule when no single method
  governs.
- **Instalment rates for 2023 and a flag before the table.** The
  built-in CRA overdue-tax rates now start with 2023 (Q1 8%, Q2-Q4 9%).
  A 2023 year was charged 2024's 10% all year and the report called it
  the published rate; a year before 2023 now says its days ASSUME the
  earliest rate (`rate_extrapolated`).
- **One low prior year no longer waives instalments.** With only one
  of `prior_year_net_tax` / `second_prior_net_tax` set and at or below
  $3,000, `taxjson instalments` said "No instalments required" and
  printed the unset year as 0.00. Both preceding years must be at or
  below $3,000 (ITA s.156.1(1)); with one unknown the test is now
  unknown (instalments assumed required) and an unset year prints as
  "not set".
- **Instalment interest keeps compounding after you catch up.**
  `taxjson instalments` now computes interest the way CRA publishes it:
  interest on each required instalment from its due date, minus
  interest on each payment from its date (or January 1), both to the
  balance-due date and compounded daily. The old running balance
  stopped compounding the accrued charge once payments caught up,
  which understated interest by a few percent and could drop a charge
  under the $25 threshold.
- **The wash radar sees today's trades.** `wash-radar`, `buy-check`,
  `sell-check`, `watch` and `reports/wash_radar_*` dropped every trade
  that had not settled yet, so right after a loss sale `buy-check` said
  SAFE (and right after a buy `sell-check` said SAFE). A trade now
  counts from its trade date; the windows stay settlement-based.
- **The wash radar takes losses from the engine.** Whether a sale was a
  loss came from the radar's own per-account pool, which missed the
  s.47 blend across taxable accounts, the cost added back by an earlier
  denied loss, and option cost folded in on exercise; real losses showed
  no window and `buy-check` said SAFE. The radar now reads the engine's
  gains files (grant-timed buy-back losses are not wash losses unless
  `option_buyback_loss_superficial` is on).
- **Web what-if prices options per contract.** Selling an option on the
  holding page used qty x price, 100 times too little, and showed a ~99%
  loss. Proceeds are now qty x premium x 100 (the page labels the field
  as the per-share premium and the cost as per contract); a futures
  option is refused rather than priced with a guessed multiplier.
- **Web what-if maps cross-listed options like the pipeline.** An option
  on a `TOBASE`/`GLOBAL`-mapped underlying (a `.US` call booked as
  `.TO`) was looked up under its unmapped name, and the what-if
  simulated writing a new short. It now follows the underlying's rule,
  and a sale that would open a short is refused.
- **The wash radar applies `phantoms.json`.** Phantom-backed positions
  showed as shorts (rebuys as short covers with invented losses) in the
  radar, `watch`, `buy-check`, `sell-check`, harvest's ADVISORY and the
  web UI, and real violations were missed.
- **The Canada estimate takes deductions.** `--deductions` (RRSP 20800,
  FHSA, RPP ...) and `--carrying-charges` (line 22100), or
  `deductions`/`carrying_charges` in `[estimate]` (which `instalments`
  reads too), lower net and taxable income; the AMT base takes the
  deductions in full and carrying charges at 50%. Before, a year with
  an RRSP deduction and little other income was overstated (by
  thousands on a typical salary-plus-RRSP mix) and a binding
  AMT could read as not binding.
- **No security identity by suffix stripping.** `buy-check`, `sell-check`,
  `harvest` and `scan` matched listings by root, so `SAMPLE.TO` and `SAMPLE.US`
  were one security unless ticker.map said `DISTINCT` (it merged an
  NYSE issuer SAMPLF.US with the TSX currency ETF SAMPLF.TO). Two
  listings are now the same security ONLY through a ticker.map rule
  (GLOBAL/TOBASE/JOURNAL), a split rename, or an option's own underlying,
  exactly as the engine pools them. A bare query (`buy-check SAMPLE`) still
  finds every listing of that ticker, each with its own verdict.
  `DISTINCT` now only records a settled pair for the scan's MAP-GAP check.
- **Estimate: Ontario AMT corrected** — the Ontario additional tax for
  minimum tax is 24.63% of the federal AMT excess from 2024 (it was a
  flat 33.67%, the pre-2024 factor), and the Ontario surtax is now
  recomputed on basic ON tax plus that amount (5006-D "Line 72"). The
  2026 factor is marked assumed until the 2026 form is published.
  On a gains-only Ontario estimate the provincial AMT now comes out
  higher than under the old flat factor.
- **Estimate: BC AMT factor by year** — 33.7% (2024), 34.9% (2025),
  40.0% (2026), per BC Income Tax Act s.4.8 (was 33.7% for all years).
- **Estimate: Alberta 2026 eligible dividend credit** is 8.12% (the
  table had 8%); **BC 2026 BPA** 13,216 (was 13,217).
- **Estimate: federal BPA phase-down** — the enhanced basic personal
  amount now falls linearly on net income between the 29% and 33%
  bracket thresholds (2026: 16,452 → 14,829), in regular tax and in
  the AMT's 50% credit, so a high-income estimate rises slightly.
- **Estimate: Ontario Health Premium** (up to $900) is modelled for ON
  and shown in the `--verbose` trace.
- **Estimate: crypto staking** rewards (a crypto account's dividends)
  are ordinary income with no withholding — they were counted as
  foreign dividends with an assumed 15% foreign tax credit.
- **Estimate notes**: the block now says which surtax/health premium
  applied, the phased BPA, the provincial AMT arithmetic, and — when
  the project year has no built-in rate table — which year's tables
  ran (a year before 2024 also says the post-2024 AMT did not apply).
  The assumptions line keeps disclosing that non-eligible dividends are
  treated as eligible.
- **Instalments**: credit interest runs from the later of the payment
  date and January 1; net interest of $25 or less is not charged; the
  report says CRA charges instalment interest only after a reminder.
  With no `prescribed_rate(s)` set, CRA's published quarterly rates
  (2024–2026, built in) are used instead of 0%. Scaffold and README
  rate examples corrected (2025 Q3 onward: 7%).
- `serve`: malformed taxjson.toml is reported like every other command
  (no traceback); `--port` must be 1-65535. Web UI: error pages for an
  unknown account return 404; the what-if rejects a negative quantity
  (it was simulated as positive).
- `estimate`/`sum --estimate`: a missing or unsupported province fails
  BEFORE the table prints, and errors name the invoking command;
  `sum --province` without the estimate warns that it is ignored.
- `scan` before any run exits 1 instead of reporting a clean scan;
  `leaps`/`leaps-sum` with no books say so and exit 1.
- **Views say what they leave out.** `estimate` keeps the 15% foreign-tax
  fallback for an account whose base book cannot be read (and warns);
  `estimate --verbose` prints the 2025 rate as 14.5% and names the FTC's
  source; `carryover` no longer warns about an account the last run
  skipped for having no inputs; `gains` names the crypto account it does
  not show; `wash-sales --explain` traces the tax year's wash sales only;
  a currency-less row prints `?`, not `CAD`; the merged `audit --json`
  keeps every `reconciliation_failures` reason; the all-accounts views
  read an account whose only native book is `_sorted.json`; a non-UTF-8
  work file is a one-line error, not a traceback; `transfers` refuses an
  unknown account, shows each row's FEE and warns when a base book cannot
  be read; `leaps` / `leaps-sum` count LEAPS closes routed to manual
  reporting instead of reporting none.
- **scan, sanity, fetch.** `scan` matches a plan word only as a whole
  token of the account name (`admiral` is no IRA), warns about an unknown
  `plan`, and counts an option as a sighting of its underlying's listing
  (MAP-GAP / US-LISTING); `sanity` pairs a crypto snapshot's
  venue-suffixed symbols (`LINK.KR`) with the bare coin and masks account
  ids in the holdings file names it prints; a failed Questrade refresh
  says which token was used (the cached chain wins over
  `$QUESTRADE_REFRESH_TOKEN`) and how to start a new chain.

### Reports and views

- `taxjson-export --holdings-toml` given --trades files of two accounts
  applied each account's copy of a split to the summed balance, so a
  closed round's trades stayed on the holding's `trades` list; a split is
  one event there now (re-audit A2-1590).
- `taxjson edge-cases` in a US project no longer lists a stock dividend
  as an in-window acquisition of a loss: it is not a purchase for the
  wash-sale rule (US-STKDIV-01), and the engine already allowed the
  loss. Canada still lists it (CA-STKDIV-01) (re-audit A2-1547).
- `taxjson sum` FOR THE RETURN now names the per-row rounding gap of the
  DENIED column (US: the Form 8949 code-W adjustment) the way it already
  named the gain's, and `sum --json` adds `engine_denied_unrounded`
  (re-audit A2-0912, the second half of R1-166).
- `taxjson list --date` in a US project calls its cost the per-account
  FIFO basis (the return's own basis) and no longer claims a symbol held
  in two accounts has one blended s.47 ACB on the return; the note and
  the "ACB" wording stay in Canada (CA-ACB-01 / US-BASIS-01; audit
  A2-0154, A2-0410, A2-0720, A2-0734, A2-1244, A2-1266, A2-1318,
  A2-1330).
- `taxjson-export`: a gains / inventory file whose rows hold text in a
  number field (or a non-text symbol) is a one-line error naming the file
  and row, exit 2, instead of a float() traceback (A2-0793, export part).
- `taxjson wash-sales --explain` in a US project passes
  `--per-account-basis` explicitly, so the trace of the merged books
  keeps FIFO per account like the table it explains.
- `taxjson audit`: a phantom-basis sale the books route to manual
  reporting is tied out as "phantom basis — manual reporting" instead of
  "MISSING from the check file(s)" (exit 1), so the checklist's audit
  step can complete; the KNOWN_ISSUES entry is gone (re-audit A2-1150).
- edge-cases counts every superficial-loss / wash-sale window on the
  engine's own dates — settlement dates in Canada, trade dates in the
  US — whatever `tax_date` says; it called engine-denied losses OUTSIDE
  the window (and allowed ones INSIDE) under a non-default tax_date,
  dropped year-crossing windows, and printed "THE DATE BASIS DECIDES"
  for a choice that cannot change the verdict (re-audit A2-0133,
  A2-0134, A2-0135, A2-1206).
- edge-cases positions count opening balances, `phantoms.json` openings
  and split ratios: a phantom-backed or opening-balance year-end sale
  read as a short sale, and "held on day 30" was in pre-split units
  (re-audit A2-0388, A2-0389, A2-1208).
- edge-cases no longer lists a buy-to-close of a written call as a long
  call bought in a loss's window, in either section (re-audit A2-0699,
  A2-1197), describes a written option assigned on its expiry date as
  an assignment landing with its share leg (A2-0700), and leaves US
  crypto out of the wash-sale window sections (A2-1201).
- edge-cases "Crypto near midnight" converts with the project's
  `local_timezone` (it assumed EST and missed real UTC year-straddles
  west of Eastern) and lists exactly the rows whose local and UTC dates
  fall in different years (re-audit A2-1200, A2-1202, A2-1203, A2-1204).
- edge-cases judges written options against the filed locks the way
  option-boundary does — the timing each lock records, and the
  `prior_year_record` lock (re-audit A2-0390, A2-1205).
- edge-cases names an unreadable work file instead of printing "None."
  at exit 0, refuses `--margin` below 0, and refuses an invalid
  `futures_settle` as `run` does; check-dates refuses it too (re-audit
  A2-1199, A2-1198, A2-0697).
- check-dates: an unreadable parsed file is an ERROR (it was dropped and
  the exit turned 0); an option expiry row settling after the contract's
  expiry is a WARN; a far-future row is one error, not two; a `.tt`
  line may carry the settlement date of a trade made today; `/ESH5`
  futures are futures; Globex Christmas / New Year's evening fills are
  normal and a weekend futures settle date is reported (re-audit
  A2-0385, A2-0386, A2-1192, A2-1193, A2-1194, A2-1191).
- `list --date` keeps the in-account superficial-loss / wash-sale
  addition to the replacement's cost, as the README and its label say
  (it recomputed with `--no-wash`; re-audit A2-0391, A2-0392, A2-0701);
  pinned on a long call sold at a loss and bought back by an option
  test.
- `list` / `shares` "as of the latest data" is the last settlement date
  on a settle-basis book, and an unreadable base book is named on
  stderr instead of silently moving the date (re-audit A2-0698,
  A2-0702).
- taxjson-export `--dust-threshold` refuses nan, inf and negatives (nan or
  inf hid every zero-cost holding); a position transferred in keeps its
  later trade events in the holdings TOML; JSON piped on stdin is read
  as UTF-8 whatever the locale, as files are (re-audit A2-1214, A2-1216,
  A2-1217, A2-1215, A2-1219).
- `taxjson sanity` refuses a holdings `quantity = true` (read as 1), and
  the cross-account overlap note counts a position moved in kind into a
  second taxable account (re-audit A2-1230, A2-1231).
- `sum`, `list`, `winners` and `wash-sales` refuse a pipeline work-file
  name as an account (`margin_raw` printed native USD under a CAD
  header); `wash-sales`, `list` and `winners` carry the run-state banner
  after per-account runs, and `wash-sales` the other-year banner
  (re-audit A2-0394, A2-0400, A2-0694, A2-0405).
- edge-cases, spinoffs, splits, check-dates, winners, leaps and
  leaps-sum refuse an account that is not in `[accounts]`; harvest names
  a bare symbol filter that matches no position (re-audit A2-0684,
  A2-1167).
- trades, divs, events, roc, gains, shares, fees-sum and check-dates name
  a configured account that has inputs but no books (re-audit A2-1182).
- `sum` FOR THE RETURN: the US footer names a loss denied for good by an
  IRA repurchase and `--json` carries `permanently_denied` (A2-0647); the
  Canadian footer says an affiliated person adds the denial to their own
  ACB instead of "lost for good" (A2-0659), as form-export does.
- Report readers: a work/ row whose date is not a string or whose money
  or quantity field is not a number is refused with the file and row
  named, instead of a traceback in whichever view read it (re-audit
  A2-0330).
- fees report: `--to cad` / `--to " CAD"` no longer converts CAD fees at
  the 1.35 fallback (re-audit A2-0645); fees in a generic import with no
  `[broker] name` no longer leave a broker listed as fee-free (A2-0646);
  the title says the year is windowed by TRADE date, unlike the .sum and
  trades-sum (A2-1102; JSON `meta.date_basis`).
- `.sum` per-asset block: under grant timing each written option is one
  trade whose result is its premium plus a same-year buy-back; the block
  dropped every premium (an expired write vanished, a bought-back one
  showed only its loss) while TOTAL REALIZED OPTION GAIN kept it
  (re-audit A2-1114).
- The row views (`events`, `divs`, `trades`, `roc`, `dil`) warn about an
  account with inputs but no built book, as the -sum views do; `divs-sum`
  / `roc-sum` / `dil-sum` outside a project with a country refuse, as
  `divs` does, instead of an "all history" total with registered
  accounts folded in (re-audit A2-0333, A2-1110).
- `leaps` / `leaps-sum` stop when an account with option gains has no
  native book, or when ticker.map cannot be read (a renamed LEAPS or a
  whole account vanished at exit 0) (re-audit A2-0117, A2-0329,
  A2-1126).
- `ccd-sum` heads a call on a class-share root (SAMPLD) under the
  held class share (SAMPLD.B.TO) even when no share was sold in the year
  (re-audit A2-1115).
- `sum`, `t1135`, `list` and the other report commands now print the
  "not the clean result of the current inputs" banner when a first
  `taxjson run` aborted after writing work/ (no reports yet), instead
  of serving the partial books silently (re-audit A2-0658).
- run: the filing-basis `<acct>_wash.sum` of an account in the blended
  s.47 pass no longer repeats the isolated per-account pass's s.40(3)
  notes (return of capital beyond the account's own ACB, or on its
  empty pool) — the blended pool booked no such gain; the per-account
  `<acct>.sum` baseline keeps them (re-audit A2-0654, A2-1117).
- `taxjson-explain` and `taxjson-audit` take `--sheltered` more than
  once, as `taxjson-gains` does; a second file used to replace the first
  silently, and the superficial-loss denial it backed disappeared
  (A2-0194).
- `reports/<account>_holdings.toml` now says (`meta.base_cost_basis`,
  README) that its costs leave out distributions.map ACB adjustments,
  which `taxjson list` includes (A2-0226).
- **find-missing-history:** a buy the broker marks as covering a short
  (RBC `COVER SHORT.`, IB code `C`) with no short in the data is
  reported as missing history (A2-0306, A2-0175); an ASSIGN stock leg
  counts in the $0-basis check (A2-0307); a Norbert's-gambit pair
  folded by a ticker.map JOURNAL line is no longer a one-day phantom
  short, and `--gen-phantoms` leaves it out (A2-0309, A2-0636); a
  decimal-comma merger ratio is left out of the hint instead of read as
  125 (A2-1096); a Canadian plan name in an account label matches as a
  whole word, so a taxable `sunlife` is not a LIF (A2-1097).
- **`find-missing-history --gen-phantoms` never overwrites a reviewed
  file** (nor does `taxjson-gains --suggest-phantoms`): it stops unless
  `--force`, which keeps a `.bak` (audit A2-0312).
- **audit and find-missing-history follow ticker.map and the locks.**
  `audit SYMBOL` also matches the broker's own ticker of a renamed
  security (`audit ABC.US` found nothing although every block prints
  `SELL ... ABC.US` and its MAPPING); `audit --year <locked year>`
  recomputes with the option timing the lock recorded, and says so;
  the merged `audit --json` total is summed over the events and
  rounded once (it was a cent off wash-sales). find-missing-history
  names the broker's ticker of a renamed symbol (SAMPLJ.TO <- SAMPLK.US: a
  missing buy belongs under the broker's symbol and currency), and
  `--gen-phantoms phantoms.json` from the project root says `taxjson
  run` auto-detects it (audit S048-17, S048-18, S047-17, S049-01,
  S047-19).
- **Query views say what they show.** `shares` is labelled as of the
  books' latest date (it said "tax year 2025" over 2026 positions; JSON
  `as_of`) and leaves futures out; `list` COST/SH is per share for an
  equity option (it was per contract, 100x harvest's); `gains` and
  `wash-sales` show a short row's proceeds/cost the real-world way, as
  winners, ccd-sum and form-export do; `winners margin` / `ccd-sum
  margin` no longer warn that the window 'margin' may exceed the year;
  `divs-sum`'s compare-with-slips line leaves crypto staking out (JSON
  `totals_slips`); `fx-cash --events` prints units to the cent;
  `wash-sales`, `divs-sum` (and the other period views), `winners` and
  `ccd-sum` warn naming a configured account with inputs but no books;
  US projects get §1091 wording in `list` and the checklist (audit
  S044-05, S044-04, S045-03, S048-10, S048-22, S048-24, S046-21,
  S045-09, S049-14; pin S045-13).
- **sanity: no silent gaps, no ids.** A holdings row with a quantity
  but no symbol, or with a missing/blank quantity, is refused (it was
  dropped and the check said OK); accounts with open positions but no
  `holdings` file print `UNCHECKED: ...` and the checklist's sanity
  step is attention instead of done; an unreadable taxjson.toml is
  named as such (it said no account declares `holdings`); the missing-
  file note and `--json` `file_account` mask broker ids (audit S044-18,
  S044-19, S049-08, S044-03, S044-16).
- **A corrupt work/ file is a one-line error everywhere.** A truncated,
  non-UTF-8 or wrong-shape (`[1,2,3]`, `{"transactions": 5}`) gains,
  base, raw or report file printed a traceback from about 17 read
  commands (sum, estimate, winners, shares, wash-sales, list, leaps,
  close-year, audit, t1135, form-export, wash-radar, buy-/sell-check,
  option-boundary, transfers, roc-sum, fx-cash ...); they now name the
  file and stop, or skip it with a warning where they already did
  (`lib/json_input.read_work_doc`; `load_transactions` refuses a
  non-list `transactions`) (audit S042-18).
- **sum: rows foot and TOTAL counts PIL.** NON-OPT + OPTION = REALIZED
  on every row as printed (REALIZED is rounded once; OPTION takes the
  cent), and TOTAL = REALIZED + DIVIDEND + PIL, the same figure as the
  `.sum` GRAND TOTAL (a payment in lieu was printed but left out). The
  FOR THE RETURN footer no longer says every DENIED amount goes onto a
  replacement's ACB: it names the permanently denied part (a
  registered-account acquisition, s.40(2)(g)(i)) and the JSON carries
  `permanently_denied`; form-export's general note says the same
  (audit S042-21, S042-22, S043-02, S048-04).
- **audit: provenance.** The crypto ticker.map stage's `_mapped.json`
  is a derived book, not a second source (every crypto block claimed a
  false 2-file dedup and lost its MAPPING line), and account `margin`
  no longer picks up a sibling `margin_us`'s parses (audit S047-12,
  S047-13).
- Watchlist export: `.V` / `.CN` / `.NE` listings are Canadian
  (`TSXV:` / `CSE:` / `NEO:` on TradingView, `:CA` elsewhere) (audit
  S078-03). `merge2 --to` without `--rates` names the 1.35 default rate
  it uses (audit R1-155). Crypto money cells with two sign markers
  (`--5`, `(-5)`) or `1_000` are refused (audit R1-114).
- **`taxjson audit`:** a grant-timing write is headed WRITE with its
  premium as proceeds (it read "COVER ... (short)" with proceeds 0.00 and
  a negative cost); a short's proceeds and cost are shown as filed (the
  short sale and the cover, not the engine's negated legs); an option's
  per-share note is "N contracts × 100 sh @ price" (it divided by the
  contract count); a denial that is partly deferred and partly permanent
  names both amounts and destinations (the deferred part read as lost);
  a cross-zero option fill's long close and its grant write are two
  events (they were summed into one meaningless SELL); a US sale over
  long- and short-term lots is MIXED with the per-term gains; a `--check`
  file named twice is read once (every tie-out failed); a saved
  disposition with no id fails the reconciliation (it was skipped). The
  totals say they are unrounded engine sums (Schedule 3 rows round to the
  cent first), and the multi-book `--json` keeps the reconciliation
  failures.
- **Fee statistics:** plain futures fees are their own bucket in the
  gains summary, the .sum fee stats and `fees-sum` (they were counted as
  stock fees, and futures contracts as shares in $/share). README and
  `fees-sum --help` now say that `fees-sum` counts by trade date (as
  `taxjson fees`) while `sum` FEES follows the project's tax_date.
- **ACB traces (`*.traces`, `audit`, `explain`):** an option's ACB/Sh,
  Gain/Sh and wash-window acb/sh are per share (they were per contract
  next to a per-share price); a fee the trace derives from the net is
  the signed residual (a cheap option's real commission no longer shows
  as 0, and sub-cent price rounding is no longer shown as a fee); a
  buy that only closes a short is labelled "closes a short — acquires
  nothing" instead of an eligible candidate; a crypto partial denial is
  no longer labelled "full".
- **Blended accounts (`taxjson-split-gains`):** each account's holdings
  show its own position start date (SINCE) instead of the pool's, keep
  full-precision quantities (crypto dust no longer becomes 0 units with
  a cost), carry their option-replacement warnings and phantom log, and
  a US file's `summary.count` counts its own records. A missing or
  unreadable `--base` book is an error instead of an empty fee map.
- Gains JSON keeps full precision for `wash_trigger.trigger_qty` and
  `phantom_application_log[].opening_qty` (a 6.76e-06 AVAX trigger read
  0.0), and `--gen-phantoms` notes keep a crypto short's size;
  find-missing-history links a merger receipt posted up to 7 days after
  the removal; US engine: after a rename, same-day lots are sold in
  order of their purchase time.
- The account `_wash.sum` DIAGNOSTICS no longer repeats each engine line
  (the gains and blend stages both kept a copy).
- **taxjson-explain `--symbol` is case-insensitive** like `taxjson
  audit`, and its `--help` example no longer says `--wash-sales`
  "includes" the wash traces — it filters to wash-sale gains (S029-19,
  S029-24).
- **`taxjson-export` fails loudly on an unusable input.** A missing,
  truncated or wrong-shape gains JSON, `--base-gains`, `--trades` or
  `--transfer-evidence` file, a JSON with no `inventory`, or a `.toml`
  with no `[[holding]]` array now stops the tool (exit 2, one line naming
  the file). Under `run --fast` a corrupt cache used to publish an EMPTY
  `<acct>_holdings.toml` at exit 0 and list every position as closed;
  the previous snapshot is now kept and the stage fails.
- **TradingView exports take `tv_exchange.map` from the project.** The
  map is looked up next to the input and in its parent (the project root
  for `work/*_gains.json`) before the current directory, so `taxjson -C
  <proj> run` from elsewhere keeps the `NYSE:`-style prefixes (new
  `--tv-map FILE` for explicit use).
- **Holdings export details.** A small quantity with real cost (0.0009
  BTC) is no longer dropped as dust; the .sum HOLDINGS REPORT says it
  is the end-of-data inventory, not year-end positions; an option on a
  class share names the held listing (`SAMPLD.B.TO`, not `SAMPLD.TO`); the
  `trades` history follows splits and renames; overseas listings (.L,
  .AX) are in neither currency-split watchlist; a future is `asset_type
  = "future"` and a futures option gets no guessed `contract_multiplier
  = 100` (its report cost is per contract).
- **Report labels say what they add up.** The `.sum` non-option line is
  `TOTAL REALIZED NON-OPTION GAIN` (shares, units, futures and crypto —
  it was "STOCK" and read as Schedule 3 line 4), its bottom line is
  `GRAND TOTAL (GAIN+DIV+PIL)`, and `TOTAL DIVIDENDS / STAKING` names a
  crypto account's staking rewards; `taxjson sum` heads the column
  NON-OPT; `fees-sum` says non-option and $/UNIT; `divs-sum` totals
  crypto staking rewards apart (other income, no T5/T3).
- `ccd-sum` CLOSES/QTY no longer count a grant-timing write as a close.
- `taxjson-ccd-gains` / `taxjson-leaps-gains` (reports/ccd.rpt,
  leaps.rpt): per-contract COST/QTY, PROC/QTY, GAIN/QTY columns (they
  were always 0.0000), no -0.00, totals per currency on mixed-currency
  input, and a break-even legacy row is no longer in both reports.
- `taxjson-sum-gains FILE` keeps wash_solver_iterations like stdin does.
- `taxjson-diff`: a pure re-ordering of same-key rows is no change, a
  sub-micro crypto quantity change is seen, output order is stable, and
  an explicit `--by` field no record carries is refused.
  `taxjson-extractors` parses its arguments.
- `taxjson-missing-history`: an `--account` no row carries is refused
  (it printed the all-clear); registered-account rows are listed under
  SHELTERED (no reportable gain) and the checklist no longer counts them
  as affecting the year.
- work/<acct>_report.json: the wash total reads `disallowed_amount`, so
  a US project's total_disallowed is no longer always 0.
- **`edge-cases` explains a US project with §1091.** It printed s.54's
  still-held reasoning, long calls as replacement property and
  "Schedule 3" for US books; a US project now gets trade dates, no
  still-held items, long calls as warnings only, Form 8949, and no
  written-option (s.49) section (US-RPT-05).
- **Per-account holdings include phantom openings and split the deferred
  loss.** In a blended taxable pass each account's `_gains_wash.json`
  inventory (read by `list`, `shares`, `harvest`) was apportioned from
  the account's own book, which lacks the openings `phantoms.json`
  adds: a position could show as a short at a negative cost or vanish.
  The openings now count. The deferred superficial loss parked in a
  blended pool is apportioned with the shares instead of being copied
  whole into every account.
- **Traces and `taxjson-explain` tell the truth about phantom rows and
  denials.** The traces file counted phantom-basis sales (from
  `phantoms.json`) as ordinary gains in its header and per-symbol totals
  and gave them a holding period counted from 1970; `taxjson-explain`
  did the same. Both now show them as MANUAL REPORTING rows outside the
  totals (the gains JSON's manual rows carry no `days_held`). The
  superficial-loss explanation states the per-holder rule the engine
  applies, says PERMANENTLY denied for a registered or affiliated
  replacement instead of "ACB pool bumped", and no longer claims "the
  earliest is chosen as trigger". A grant-timed buy-back's trace line
  prints the booked cost and gain. `taxjson-explain --no-wash` explains
  a registered account's book the way `taxjson run` computes it. The
  `--affiliated` help no longer calls a parent or sibling affiliated.
- **holdings.toml states what its base-currency cost is**:
  `meta.base_cost_basis` says `base_total_cost` is per-account and
  per-listing, before superficial-loss adjustments and the s.47 blend
  (the filing ACB is `taxjson list`) (audit S037-24).
- **audit fails when a configured account has no books** (it printed
  "✓" over the others and exited 0, and the checklist marked every
  disposition tied) (audit S047-16).
- **LEAPS views: long positions only, through ticker.map, with the
  registered accounts split out.** `leaps` / `leaps-sum` no longer count
  the write and buy-back of a contract that qualified through a long buy
  (those legs are covered-call P&L, already in `ccd-sum`), a LEAPS whose
  root ticker.map TOBASE-renames (BCE...US -> BCE...TO) is no longer
  dropped, and `leaps`, `leaps-sum` and `ccd-sum` print the TAXABLE and
  SHELTERED parts of their total (`--json`: `taxable_gain`,
  `sheltered_gain`) (audit R1-172, R1-237, R1-182).
- **`list --date` applies phantoms.json**, so phantom-backed positions no
  longer show as large shorts. Plain `list` is labelled with the date the
  books run to, not "as of tax year" (it shows end-of-data positions),
  and `--date` no longer claims "pre-ticker.map" (audit R1-187, R1-282).
- **Radar sidecar names follow the account name exactly**: account
  `a_base_x` no longer overwrites account `a_x`'s radar, and an account
  named COMBINED is refused (audit S038-10).
- **`list --date` says its ACB is per account.** The as-of view
  recomputes each account alone, so a symbol held in two taxable
  accounts shows each account's own cost, not the s.47 blend the return
  uses. The label, `--help` and README now say so (they claimed "full
  ACB fidelity"), and a note names the shared symbols (audit S044-21).
- **`taxjson gains` names an account with no native gains.** After a
  cross-currency rollover skips an account's native books, the run
  deletes the previous run's stale native gains and `taxjson gains` says
  the account is missing and why, instead of omitting it or serving the
  old rows (audit S037-23).
- **The `.sum` TOTAL PROCEEDS / TOTAL COST are labelled.** They are the
  engine's signed figures (shorts and written options negated), not the
  Schedule 3 proceeds and ACB; the report and KNOWN_ISSUES now point to
  `taxjson form-export` for those (audit R1-208).
- **Holdings TOML states its option cost unit.** `cost_per_share` in
  `reports/<account>_holdings.toml` is `total_cost / quantity` (per
  contract for an option, the convention the broker holdings files
  share); the file and README now say so next to `contract_multiplier`
  and the per-share trade prices (audit S030-08).
- **`taxjson-diff` sees hand-reported dispositions.** Rows the pipeline
  moves to `manual_reporting_required` (phantom basis) are compared too,
  so adding, dropping or changing one is no longer "0 added | 0 removed"
  (audit S029-16).
- **No false all-clear from sanity or find-missing-history.** When a
  configured `holdings` file is missing, `taxjson sanity` ends with an
  `INCOMPLETE` line (`"complete": false` in `--json`), `run` prints a
  `!!` line and the checklist keeps the step at attention.
  `find-missing-history` (and `taxjson-missing-history`) no longer print
  "No missing-cost-basis issues found" and exit 0 when a base book failed
  to load or a configured account has no book; they name what was not
  checked and exit 1 (audit R1-324, R1-336, S047-18).
- **`reports/ccd.rpt` and `leaps.rpt` match their query twins.** They
  counted phantom-basis (tainted) rows that `ccd-sum` / `leaps-sum` skip,
  `leaps.rpt` was titled "LEAPS" while listing every long option of any
  tenor (now titled so, pointing at `leaps-sum`), and an unreadable input
  printed TOTAL 0.00 with exit 0 (now exit 1) (audit R1-173).
- **Query views stop on an unreadable file.** `list`, `shares`, `winners`,
  `wash-sales`, `ccd-sum`, `leaps`, `gains`, the transaction views and the
  -sum roll-ups warned about a truncated work/ file and printed a partial
  report with exit 0 (`winners` moved by 39k, `scan` said "clean scan");
  they now name the file and exit nonzero (audit S045-01, S042-05).
- **`divs-sum` and `winners` separate registered accounts.** Both summed
  RRSP/TFSA/LIRA/RESP amounts into the headline (the 2025 `winners` total
  was 2.24x the Schedule 3 gain, and the USD `divs-sum` 2.25x the slips);
  they now print TAXABLE and SHELTERED lines, and the checklist points the
  slip tie-out at the TAXABLE line. `divs-sum` counts DIVIDEND rows only
  (payments in lieu are `dil-sum`; adding the two counted PIL twice), and
  the `divs` help says it shows both (audit S040-13, S041-04, R1-272).
- **`winners` and `ccd-sum` count routed phantom-basis sales.** The
  "skipped N tainted" warning read only in-line rows, so a pipeline file's
  `manual_reporting_required` sales vanished from the ranking without a
  word (audit S040-15).
- **Tax-year views follow the settlement date.** `winners`, `gains`,
  `ccd-sum`, `leaps` and `leaps-sum` windowed a tax year on the trade
  date, so a Dec-31 trade that settles in January dropped out of every
  year's view (so `ccd-sum` fell short of `ccd.rpt`). On a
  settle-basis project the year window now uses the settlement date, like
  `sum` and form-export (audit R1-171, R1-186, R1-238, R1-273).
- **`leaps` / `leaps-sum` warn outside the books' year, and the default
  window refuses a stale build.** They had no scope warning (`leaps-sum
  all` said "all history" over one year); `winners`, `ccd-sum`, `leaps`
  and `leaps-sum` with the default window now stop when `work/` was built
  for another year than `[settings] year` instead of saying the year had
  no dispositions (audit S048-11, S048-14).
- **`taxjson fees` counts a charged fee as a fee.** Standalone FEE rows
  (IB market-data subscriptions, Questrade/RBC custody fees) were shown
  negative and netted against commissions, and a commission refund was
  shown as a charge; FEE rows now follow the repo convention (positive =
  charged) (audit R1-54, R1-269).
- **audit:** `--year` for another year and `--all-years` no longer call
  fresh books stale (the saved gains files hold one year); a crypto fee
  priced by the fill stage ties out; a large book's per-row 4-dp rounding
  no longer fails the totals.
- **The account .sum lists phantom-basis sales** in a MANUAL REPORTING
  section. `option-boundary` applies phantoms.json (a phantom long option
  sold to close is not a write). `wash-sales --explain` traces the
  blended books the table comes from.
- **Broker-marked shorts are not missing history.** A position RBC
  marks as a short sale (`SHORT.` / `COVER SHORT.`) is no longer offered
  as a phantom by `find-missing-history`, `--suggest-phantoms` or the
  run's go-short hint; `find-missing-history` lists it apart.
- **`taxjson check-dates`.** Every trade and settlement date the parsers
  produced, checked against the calendar of what was traded: crypto 24/7,
  futures 23/5 (Sunday evening to Friday), US stocks on exchange days plus
  the overnight session, options and Canadian listings on exchange days;
  settlement never before the trade or on a weekend, and normally the
  standard cycle on either market's calendar. On real books it
  found option lots in an opening file dated on a day neither market
  settles (the file carries trade dates).
- **`taxjson edge-cases`.** One report of everything whose treatment turns
  on a boundary: trades that settle in the other year and where
  `tax_date` puts them, dispositions on the last and first days of a
  year, written options and expiries across Dec 31, income paid around
  New Year, crypto near midnight, loss windows spanning Dec 31 and
  deferrals carried into next year. For each taxable loss it lists every
  purchase (any account) or sale within `--margin` days of day 30, with
  the day count on both date bases, and flags where the basis alone
  decides. Long calls bought inside a share loss's window are listed as
  advisory (s.54 'right to acquire'; not enforced).
- `audit`: `--date` matches the trade OR settlement date (and says
  which); `--summary` shows ids long enough to be unique; an unknown
  `--account`, or a symbol/`--id`/`--date` filter that matches nothing,
  exits 1 with a message instead of an empty "0/0 ✓" block;
  `--account` runs only the computation holding that account.
- **`list --date` cuts on the project's date basis.** On a settle-basis
  project (the Canadian default) the as-of positions now drop rows by
  settlement date, like the gains year and `t1135`: a sale traded Dec 31
  that settles in January is still held at Dec 31. The banner names the
  basis; `taxjson-gains --as-of` follows `--tax-date`.
- **The .sum DIAGNOSTICS banners are fresh and whole.** `<acct>.sum`
  (the pre-blend baseline) no longer repeats the previous run's
  cross-account notes (they are in `<acct>_wash.sum`); a note about a
  problem already fixed no longer survives in either banner after an
  account leaves the blended or crypto wash pass; the transfer-cluster
  attestation note keeps its closing sentence; notes whose events all
  fall after the tax year (and its 30-day window) are listed last under
  their own heading instead of asking for action in this year's report;
  the per-file parse counts are echoed for file names with spaces and
  for TRANSFER-only files; the crypto validation line names the file,
  not its absolute path (which carried the OS user name).
- **Report views label and total what they show.** Payments in lieu
  have their own footer total in `events` / `dil` (not TOTAL DIVIDEND);
  a crypto account's DIVIDEND rows are labelled staking rewards (ordinary
  income) in `sum`, the `events` / `divs` footers and the crypto
  `.sum`; `dil-sum`, `roc-sum` and `trades-sum` split out
  registered accounts (no income, no T3, not T5008 proceeds); summary
  TOTALs equal the sum of their printed rows; `winners` shows a short or
  written option's PROCEEDS/COST the way `ccd-sum` and form-export do;
  a grant-timing WRITE record no longer counts as a close in `ccd-sum`,
  `winners` or the `.sum` trade statistics; `reports/ccd.rpt` prints the
  premium and the buy-back as positive PREMIUM / BUYBACK columns.
- **More views say what they mean.** `find-missing-history` lists pairs
  `phantoms.json` already covers apart (the checklist step clears);
  `fees.rpt` / `fees-sum` leave out ticker.map DELETE'd rows and their
  unconverted JSON no longer adds currencies into one total; a LEAPS
  renamed by a SPLIT keeps its LEAPS entry; `instalments` names the inputs
  it assumed to be 0 (other income, withholding); `trades`, `trades-sum`
  and `events` take a trade by its settlement date in a tax-year window on
  a settle-basis project, as Schedule 3 does; per-underlying reports file
  an `SAMPLD…` option under the `SAMPLD.B.TO` shares it is written on.

### Pipeline, configuration and errors

- New per-account setting `combined_broker_accounts = true`
  (`[accounts.<name>]`): every broker account in the folder's statements
  is yours and taxable together, so the 'statement spans N accounts'
  ATTENTION (IB per statement and across statements, Questrade, RBC)
  becomes a one-line note with masked ids. Refused on a sheltered
  account unless the statement shows every account is the same plan
  (only Questrade's Account Type can), and a quoted value is refused by
  every config reader.
- "Phantom" is gone (owner decision): the project file listing sales of
  shares whose purchase is not in your broker files (bought before the
  data starts) is now `missing_history.json`, and the console, reports,
  checklist, help and docs say "missing purchase", "missing-history
  opening" or "unknown cost" (e.g. "1 sale(s) with unknown cost (no
  purchase in your files)"). An existing `phantoms.json` is still read,
  with one NOTE per run asking you to `mv phantoms.json
  missing_history.json` (taxjson never renames or edits it); a project
  with both files is refused (exit 2) until you keep one. Flags:
  `find-missing-history --write-missing-history [FILE]` (default: the
  project's `missing_history.json`), `taxjson-gains
  --suggest-missing-history FILE`, `taxjson-missing-history
  --missing-history FILE`; the old `--gen-phantoms`, `--suggest-phantoms`
  and `--phantoms` still work (hidden, with a note). Code:
  `taxjson.lib.phantom_holdings` is now `taxjson.lib.missing_history` (the
  old module and function names remain as aliases). The gains JSON
  writes `missing_history_log` (`taxjson-split-gains` still reads an
  older file's `phantom_application_log`); the `--json` keys
  `phantom_openings` (edge-cases) and `phantoms_applied` (t1135) are now
  `missing_history_openings` and `missing_history_applied`. Filed locks
  are unaffected (they hold amounts only), and renaming the file is not
  an input change for `run-clean` (CA-ACB-11 / US-BASIS-04).
- After `country` changes in taxjson.toml, every report command
  (`list`, `wash-sales`, `sum`, `divs-sum`, `check-dates` ...) refuses
  the books the last full run built under the other country, instead of
  printing them under this country's labels at exit 0; the run records
  the country in work/.inputs_fingerprint.json, and `check-dates` now
  shows the run-state banner too (CA-CTRY-01 / US-CTRY-01; audit
  A2-0147).
- `taxjson run`'s native-currency (raw) pass checks the same actions as
  the engines' currency guard: an OPENING_BALANCE in another currency
  than its listing skips the raw view with a warning instead of stopping
  the run, and a TRANSFER in another currency no longer skips it
  needlessly (audit A2-0440).
- Errors are one line with a consistent exit code in more places
  (re-audit A2-0161, A2-0791, A2-0770, A2-1421, A2-1435, A2-1436,
  A2-1428, A2-1432): `taxjson <tool>` runs a tool under the same guard
  as its `taxjson-<tool>` console script (in process and with
  TAXJSON_DISPATCH=subprocess), so an unreadable ticker.map in
  `taxjson reconcile-slips` is no longer a traceback; a symlink loop or
  any other OS error on an input is `cannot read <file>: <reason>`
  (exit 2); a ticker.map rename cycle is one line (exit 2) in
  taxjson-ticker-map / merge2 / apply-distributions / reconcile-slips;
  a missing or unreadable named input exits 2 in lint-crosslistings
  --map (1 is a lint finding), brokerage --security-overrides,
  wash-radar/safe-to-sell --incomplete-history, generate-parser and
  `taxjson redact`; a `--ticker-map` that names a missing file is
  refused by corp-actions, apply-distributions and harvest (it was
  ignored); wash-radar --json-out under a file and gains
  --suggest-phantoms into a folder say `cannot write`.
- A tool that `taxjson` runs as a passthrough command (wash-radar,
  harvest, fees-sum, find-missing-history) and whose output is piped
  into `head` exits quietly (141) too; the in-process dispatcher turned
  the BrokenPipeError into a traceback with exit 1 (A2-1417).
- A UTF-8 BOM before hand-edited JSON is accepted by every transaction
  book and JSON loader (taxjson-sort, merge, merge2, fill-crypto,
  validate, json_input, phantoms.json) — it was refused with
  'Unexpected UTF-8 BOM' (A2-0776, A2-1412).
- edge-cases, check-dates, harvest, apply-distributions and split-gains
  read work/ documents through the shared reader: a wrong-shape
  document or a text number is one line naming the file and row
  (A2-0794, A2-1408, A2-0793).
- `taxjson-export`: a tv_exchange.map saved with a BOM keeps its first
  rule, and a holdings TOML row whose quantity or total_cost is not a
  number (or whose symbol is blank) is refused naming the row in every
  mode (A2-0806, A2-1410, A2-1442, A2-1443, A2-1441).
- Errors are one line, never a traceback, with one exit-code rule:
  2 for a named input or output that cannot be read or written, 1 for a
  command's finding, 130 for Ctrl-C, 141 for a closed stdout pipe
  (`taxjson trades | head`). Report text a non-UTF-8 terminal cannot
  show degrades to `?` instead of crashing. Covers a damaged prior-year
  record in `handoff`, a damaged `work/pending_elections.json` (`elect
  --pending`, `elect --set`, `run --account`), a non-UTF-8 or directory
  `ticker_extraction_overrides.txt`, damaged work/ bookkeeping files
  (`run` rebuilds them), a damaged holdings snapshot, `estimate = 5`
  where an `[estimate]` table belongs, unwritable `sends.json` /
  `manifest.json` folders, a dangling `manifest.json` symlink (refused,
  never written through), `init` under a C locale or with `inputs` as a
  file, and Ctrl-C at `checklist --walk` prompts (re-audit lists
  errors-02 / errors-05).
- An ACCOUNT argument must be an account name: a path (`./margin`,
  `../other/work/margin`) or a pattern (`fees-sum '*'`) is refused.
  spinoffs, splits, transfers, edge-cases and fees-sum say "run
  `taxjson run` first" when there are no books, as their twins do.
  `list --date` fails when an account's as-of recompute fails instead
  of silently dropping it; `audit --json` exits 2 like `audit` on an
  unreadable input, and prints the error once.
- A second `taxjson run` in the same project refuses while one is in
  progress (it used to crash on the shared work/ file names).
- A `taxjson.toml` saved with a UTF-8 byte-order mark (Notepad) is read
  by `find-missing-history`, `gains --suggest-phantoms`, `convert-tt`
  and the wash radar the way `taxjson run` reads it; one that does not
  parse stops those commands instead of being treated as "no project"
  (which fell back to settle dates and guessed account types in a US
  project) (re-audit A2-0419, A2-0424, A2-0429, A2-0430, A2-0438).
- Printed standalone commands (`taxjson-gains --suggest-phantoms`,
  `taxjson-explain`, `taxjson-corp-actions`) carry the required
  `--country` (A2-1287).
- An output path that is a directory (or cannot be written) says
  "cannot write <path>" and leaves no `.part` file (taxjson-convert-tt,
  taxjson-brokerage sidecars, wash-radar --json-out); the --explain trace
  rounds a denial to the same cent as the wash-sales table (re-audit
  A2-0707, A2-0708).
- `taxjson run` reads an unset `base_currency` as the country's currency,
  like every other command (it refused it); an empty or unreadable
  statement CSV is named as such instead of "rename it to cb_/kr_/
  generic_"; an Apple Numbers export in an account folder is refused like
  .xlsx (re-audit A2-0712, A2-0713, A2-1228, A2-0714).
- Unreadable is never absent: a statement CSV or .tt that is a dangling
  symlink stops `run` naming the file, every command (not only `run`)
  refuses a dangling or directory ticker.map / phantoms.json /
  distributions.map / overrides file, and a dangling taxjson.toml is
  refused instead of read as "no config" (re-audit A2-0143, A2-0403,
  A2-0401, A2-0144).
- An account named inside another account's work-file namespace
  (`<other>_tt_<x>`, `<other>_<broker>`) is refused — the two overwrote
  each other's books or deleted each other's corp files — and two .tt
  files of one account that convert to the same work file (`start.tt`,
  `start.TT`) stop the run (re-audit A2-0140, A2-0402, A2-0399, A2-1222).
- `run --account <sheltered>` refuses (and keeps `sheltered_base.json`)
  when another sheltered account with inputs has no book in work/; it
  rebuilt the combined book without that account's buys, so the radar
  said "safe to sell at a loss" (re-audit A2-0128).
- Every view and planning command that reads the work/ books (gains,
  divs-sum, roc-sum, wash-sales, fx-cash, list, shares, winners, the
  radar, buy-check, sell-check, harvest and others) prints the
  stale-books banner after a failed run or changed inputs, on stderr
  (re-audit A2-0380, A2-0381).
- **`taxjson fetch` (Questrade) keeps the API's order of one day's
  rows.** The fetched file sorted each day's rows by symbol and action,
  and every re-fetch merge sorted the whole file, so a same-day sale
  listed before its rebuy was booked as rebuy-then-sale: the sale used
  the averaged ACB and gain moved into a later year (CA-DATE-14 /
  US-DATE-13). Rows now sort by trade date only, and the merge keeps
  the download's order (audit A2-0084, A2-0598).
- `taxjson fetch`: the overlap check and `--trim-overlap` read a UTF-16
  or UTF-8-BOM Questrade export the way the parser does. A UTF-16
  manual export showed no overlap, so it double-counted next to the
  fetched file with no warning (audit A2-0256, A2-1040).
- `taxjson fetch` (IBKR Flex): the guard that refuses a download which
  would drop the tax year's activity takes the download's span from its
  Statement Period (else its activity dates), not from any digits on any
  row. An 8-decimal P/L such as -12.20180315 read as 2018-03-15 and
  silenced the refusal (audit A2-0083).
- `taxjson fetch` (Questrade): activity windows ask from local
  (America/Toronto) midnight, -04:00 in summer, not a fixed -05:00 that
  could miss the first day of a summer `--from` window (audit A2-0599).
- The JSON input path (`taxjson-gains` on a hand-written file, the core
  loader) refuses a trade whose settle date is before its trade date, as
  the parsers do (CA-DATE-03 / US-DATE-04); `taxjson-validate` reports it
  as an error and a settle more than a month late as a warning (A2-0959).
- The standalone year flags (`taxjson-gains/-explain/-audit/-carryover/
  -t1135 --option-grant-since`, `taxjson-brokerage --tax-year`,
  `taxjson-carryover --project-year`, `taxjson-audit --check-year`)
  refuse an implausible year such as 226 for 2026, like `--year`
  (A2-0955).
- **`run --fast` sees election, sends and config edits by content.** The
  elections manifest, sends.json and taxjson.toml are now in the
  per-account content fingerprint, so a copy restored with an older
  mtime no longer keeps the previous books (A2-0224, A2-0985,
  A2-1229). `taxjson elect --redo` passes the same rates, base currency
  and ticker.map as `run` (A2-0981, A2-0982), and the legacy manifest
  migration is atomic (A2-0218).
- **phantoms.json is applied to the native books too.** The raw
  holdings pass ran without it, so `reports/<account>_holdings.toml`
  (and the web positions) listed every phantom pair as a short, and
  `taxjson gains` showed a phantom-basis sale as a realized gain; both
  now match `sum` and `list`, and `gains` notes the sales left for
  manual reporting (audit A2-0111, A2-0305).
- **A dangling symlink for a project map stops the run.** A
  `ticker.map`, `phantoms.json`, `distributions.map` or
  `crypto_ticker.map` that exists as a name but cannot be read was
  treated as absent and the run exited 0 with other gains (A2-0313).
- **fetch / watch.** `fetch` masks the Questrade account number in its
  progress line and in API error messages (`questrade #59***`,
  `/v1/accounts/59***/activities: HTTP 400 ...`); `--trim-overlap`
  refuses to rewrite a sibling CSV whose record spans several lines (an
  unbalanced quote swallowed out-of-window trades, which were deleted
  with it); `watch --state` naming a directory, a path under a file or
  an unwritable place is a one-line error, not a traceback (audit
  S046-17, S046-16, S046-12).
- **Non-UTF-8 text files name themselves.** A latin-1 `ticker.map`,
  `distributions.map`, `ticker_extraction_overrides.txt`, `t1135.map` or
  `claimed_losses.txt` is a one-line error naming the file (`taxjson
  run` too); a cp1252 broker export that detection cannot read says
  "not UTF-8" instead of "rename it". `taxjson-lint-crosslistings`
  refuses a non-numeric quantity in one line.
- **`taxjson fetch`.** A project year whose window has not started is
  refused (it fetched the previous year's last 90 days into the new
  year's file); `--from` with `--days` is refused; a window outside the
  year is noted; an API row with no settlement date is written with its
  posting date. `taxjson-fill-crypto` never caches today's still-open
  candle.
- **phantoms.json diagnostics:** an entry that did nothing — its rows
  never go short, or no row has its symbol — is now named once on the
  run's output (it was only in the gains JSON, so a typo silently booked
  the phantom sale); another account's entry no longer prints a "no
  rows" warning in every account's stage; and the "pairs go short" hint
  still names the pairs the file does not list. The hint and
  find-missing-history now point at `taxjson find-missing-history
  --gen-phantoms` (the `taxjson-gains` flags they quoted are not `taxjson
  run` options). The unmapped cross-listing NOTE suggests the rule in
  the right direction for a USD unit (`TOBASE SAMPLF.U.TO SAMPLF.TO`) and in a
  US project.
- **Hand-made JSON books:** a time written `9:30:00` or `09:30` is read
  as `09:30:00` (it used to sort after its own superficial-loss
  adjustment, moving part of a denial into the next year, or crash); a
  time that is not a clock time is refused with the row named.
- **Stand-alone tools: an unreadable input is one line, exit 2.** A
  missing path, a directory, a non-UTF-8 file or bad JSON gave a
  traceback (exit 1) in gains, explain, audit, convert-currency,
  fill-crypto, merge2 --map, safe-to-sell, sort, sum-gains, sum-income,
  ticker-map, wash-radar and others; every `taxjson-*` tool now prints
  `<tool>: error: no such file: ...` / `cannot read ...` and exits 2
  (S070-23, S079-10). A JSON error names the file.
- **explain / audit / carryover: engine refusals are one line.** A
  RENAME-split straddling a settlement, a currency mismatch, a SPLIT
  ratio of 0 or a malformed `phantoms.json` printed a traceback; they
  now print the same one-line `error:` as `taxjson-gains` and exit 2,
  and the phantoms.json message names the file and line (S029-20,
  S071-06, S076-01).
- Stand-alone tools (fees-sum, lint-crosslistings, sum-gains, sum-income,
  ccd/leaps-gains, diff, explain, ticker-map, carryover, form-export,
  split-gains, apply-distributions, reconcile-slips) refuse an
  unreadable, non-UTF-8 or wrong-shape input in one line with a non-zero
  exit — no traceback, and no report built from the files that happened
  to load (fees-sum dropped a broker's fees at exit 0; lint-crosslistings
  printed "(Clean.)"). A bare-array transaction book is accepted.
- **Option-timing settings read the same everywhere.** A quoted
  `option_buyback_loss_superficial = "false"` or `crypto = "false"`
  counted as TRUE in every command except `run` (carryover, check-filed,
  audit, close-year, reconcile-slips, the web UI); every config reader
  now refuses it. With `tax_date = "trade"`, `option_grant_timing_since`
  is tested on the write's trade year, the same date the return's year
  filter uses. Standalone `taxjson-gains` / `taxjson-explain` say when
  they fall back to close timing (a Canada project runs grant timing).
- **A trade row with no amount is refused.** `taxjson-gains` accepted a
  BUYSELL/ASSIGN row whose `net_amount` (or `quantity`) key was missing
  and booked it at $0, and `taxjson-validate` said OK; both now name the
  row. US engine: a sale whose commission exceeds the gross (a $0.01
  close) keeps its negative proceeds instead of booking a credit.
- **`fetch` never loses input activity.** `--trim-overlap` finds the
  trade date by the "Transaction Date" header (a manual export with
  Settlement Date first lost a trade the API file did not hold) and
  writes the trimmed file atomically at 0600; the overlap guard sees an
  upper-case `.CSV` sibling like `run` does; an IBKR Flex download that
  does not cover the tax-year activity already in `ib_flex.csv` is
  refused (saved as `ib_flex.csv.new`), the replaced file is kept as a
  numbered `.bak`, and a past year's download that does not span the
  year warns (audit R1-74, S046-14, S007-00).
- **`run --fast` sees content, not only mtimes.** Each account's input
  files and the project-root maps (ticker.map, overrides,
  distributions.map, phantoms.json, crypto_ticker.map) are fingerprinted
  by content; a CSV replaced by an export with an older mtime, or a
  ticker.map restored the same way, kept the old parse at exit 0 (audit
  R1-253, R1-294).
- **A full run removes sidecars the config no longer produces.**
  work/sheltered_base.json after the last sheltered account is removed
  (the filed-year lock and the radar kept reading it), and the
  `_gains_wash.json` / `_wash.sum` of an account re-typed to sheltered
  (every query preferred them) (audit S004-05, S038-19).
- **`taxjson-merge` fails on an unreadable input** instead of writing a
  book without it (`run --account <sheltered>` rebuilt
  sheltered_base.json without a sibling's rows) (audit S038-18).
- **A spreadsheet in an account subfolder** gets the same "not read"
  warning as a CSV there (audit S043-13).
- **taxjson.toml is checked the same way by every command.**
  `base_currency` is trimmed and upper-cased (" CAD" failed later with a
  misleading "fix the rates file" error; "cad" priced CAD fees at the
  default FX rate) and must be a 3-letter code; a Canada project with a
  non-CAD base warns. `[settings] year` must be a plausible tax year
  (1900 to next year, like `init`), and `run` warns when no transaction
  in the books falls in the configured year. A misspelled top-level
  table (`[estimates]`, `[instalment]`) and an unknown `[estimate]` /
  `[instalments]` key now warn in `estimate`, `sum` and `instalments`,
  not only (or never) in `run`. `[instalments]` withheld, prior-year
  figures and prescribed rates must be non-negative finite numbers
  (rates below 1); a TOML boolean is refused there and in `[estimate]`
  (audit R1-153, R1-256, R1-216, R1-257, S038-13, R1-217).
- **`init --force` never overwrites an earlier backup.** A second
  `--force` replaced taxjson.toml.bak (the user's config) with the first
  template; later backups are numbered (audit R1-255).
- **Account names that collide with work/ artifacts are refused.** A
  name ending in `_raw`, `_base`, `_gains`, `_wash`, `_tt` (and a few
  other artifact suffixes), or the name `sheltered`, now stops every
  command with a rename hint: `cb_raw` silently vanished from `sum` and
  the estimate, and `margin_raw` overwrote `margin`'s native books
  (audit S022-00, S041-14).
- **A sheltered-account rerun no longer serves stale wash numbers.**
  After `run --account <sheltered>` rebuilt `sheltered_base.json`, `sum`,
  `form-export` and `close-year` read the older wash-adjusted gains with
  no warning (a registered-account buy that makes a taxable loss
  superficial was missing). They now warn, and `close-year` refuses
  until a full `taxjson run` (audit R1-251).
- **An unparseable `taxjson.toml` is an error in every query command.**
  It was read as an empty config: `fees-sum` converted a USD-base
  project's fees to CAD at an invented 1.35 and dropped its sibling-account
  guard, and the radar treated registered books as taxable (audit S049-00).
- **`taxjson fetch` covers the superficial-loss window and keeps
  tokens out of error messages.** The default Questrade window for a
  tax year now runs Dec 1 of the prior year through Jan 31 of the next
  (was Dec 15 .. Jan 15): a repurchase on Jan 16-30 or Dec 1-14 in a
  fetch-only account (an RRSP buying back what the margin account sold
  at a loss) was never seen, so a permanently denied loss was allowed.
  An activity with no `netAmount`/`grossAmount`/`commission` is written
  with a blank cell, so the parser refuses it instead of dropping a
  dividend as a "zero-net" row. A refused redirect no longer prints the
  redirect URL's query (the Flex token or Questrade refresh token). The
  live-holdings snapshot takes an option's suffix from the account's
  books when they hold that contract, so a CDR such as SAMPLB.TO no longer
  makes the account's US AMZN option look Montreal-listed (a false
  verify/sanity mismatch).
- **taxjson-merge never emits a partial merge.** The legacy merge that
  builds the crypto books, the blended base, `sheltered_base.json` and
  the audit tie-out printed "cannot read" and exited 0 with the
  unreadable file's rows missing — `run --fast` over a damaged cached
  Coinbase book dropped half the crypto gains with a clean console. A
  missing or unreadable input is now an error (exit 1, nothing on
  stdout), as in taxjson-merge2.
- **`crypto = "false"` (quoted) is refused** by every command instead of
  moving an equity book to the crypto line.
- **`country` and `tax_date` are checked for every command.** "Canada",
  "CA" or " canada" are read as `canada` everywhere (check-filed and the
  web what-if crashed on them, switching the drift guard off); an
  invalid `tax_date` stops every command with a clear message instead
  of an engine usage error. The checklist no longer reports a failed
  check-filed as drift.
- **`phantoms.json` entries for an unknown account stop the run.** The
  file is keyed by account name, so renaming an account silently dropped
  its openings and changed the filed gain. `taxjson run` now names each
  entry whose account is not in `[accounts]` and suggests the closest
  current name.
- **An export that parses to 0 transactions is loud, and fatal under
  `--strict`.** A Coinbase file with a renamed header, or a `kr_`-named
  file that is not a Kraken ledger, dropped its whole book with exit 0
  even under `run --strict`, and the checklist called the run clean.
  Every run (cached or not) now prints a WARNING naming the file on
  stderr, `run --strict` stops, and the checklist's `run-clean` step
  needs attention.
- **An Excel export in `inputs/<account>/` stops the run.** Only `.csv`
  and `.tt` files are read, so a Questrade `.xlsx` dropped in unconverted
  lost every trade in it with exit 0 and no mention. `taxjson run` now
  names each unconverted spreadsheet and stops (a spreadsheet next to
  its converted CSV only warns); an input folder with only a spreadsheet
  and no `[accounts.*]` section is named too; the checklist no longer
  counts `.xlsx`/`.txt` files as account activity and its `run-clean`
  step flags an unread spreadsheet.
- **Every command refuses an invalid account type, not only `run`.** An
  account `type` edited after the run (`"Taxable"`, or deleted) silently
  dropped that account from `estimate`, `instalments`, `sum`,
  `form-export`, `carryover` and `close-year` (which locked the wrong
  total), all with exit 0. Every command that reads `taxjson.toml`, and
  `taxjson serve`, now stops with the same message `run` gives.
- **An account without `type` is now fatal** (it silently defaulted to
  sheltered, so an untyped taxable account dropped out of the return);
  the message lists `taxable | sheltered`.
- **No more false "run `taxjson run` first"** after a successful run:
  `run` records accounts it skipped for having no inputs
  (`work/skipped_accounts.json`), and t1135, form-export,
  reconcile-slips, harvest, audit, `list --date`, wash-radar and
  buy/sell-check stay quiet about them (helper
  `_accounts_skipped_for_no_inputs`).
- `run` on a project where no account has any input says so; with
  `--strict` it exits 1 instead of "Done". A typo'd settings key's
  did-you-mean now prints before the "missing year" error it causes.
  `-C DIR` naming a missing directory says so; `sum` in a non-project
  says there is no taxjson.toml.
- `run`'s holdings check tells a config problem (missing holdings file)
  apart from real position differences.
- `init`: the next-steps line is `taxjson -C '<dir>' run` (the old
  `taxjson run -C <dir>` failed); `--year` is capped at next year;
  `--force` keeps the previous config as `taxjson.toml.bak` and lists
  input folders the new config no longer covers.
- **Every command checks the option-timing and `fx_cash_gains`
  settings.** `fx_cash_gains = "false"` (quoted) turned the FX-on-cash
  report on; a typo'd `option_premium_timing` or a quoted / boolean
  `option_grant_timing_since` was refused only by `run` — option-boundary
  read it as close timing and close-year wrote it into the lock. All
  are now refused by every config reader.
- **taxjson.toml with a UTF-8 BOM is read** (also a generic-importer
  mapping); a taxjson.toml that is a directory or unreadable, `work/` or
  `reports/` that is a file, and `init` onto an existing file now give a
  one-line error instead of a traceback (`run` refuses before any stage).
- **Tolerance and threshold flags must be finite and non-negative**
  (`sanity --tolerance`, `reconcile-slips --tolerance`, `taxjson-t1135
  --threshold/--detailed-threshold` (> 0), `watch --threshold`).
  `watch --threshold 0` now means "any move" (it read as 100), and an
  unreadable or other-version `.watch_state.json` is warned about before
  the new baseline is recorded.
- **`run --fast` notices a code change by content**: a taxjson upgrade
  whose files kept older mtimes (`cp -p`, `rsync -a`, `tar x`) or that
  deleted a module rebuilt nothing; the last complete run's code
  fingerprint is now kept in `work/` and a mismatch rebuilds everything.

### Privacy and security

- The `pre-push` hook refuses a commit or tag message that quotes a
  money-like amount with thousands separators and cents
  (`1,234,567.89`), so no real book total reaches the public history
  again; mark a synthetic number with the word `pii-ok` on its line
  (`scripts/check-pii.sh --message`; A2-1384). Existing history is left
  as is.
- `taxjson audit` and the gains traces keep printing a row's own id
  unmasked, on purpose: it is the `--id` handle (for Kraken, the
  exchange's ledger txid — an exchange reference). Documented in
  SECURITY.md, README and KNOWN_ISSUES (owner decision, re-audit A2-1379).
- `taxjson redact` and the generate-parser privacy gate no longer lose a
  private-denylist pattern silently: a leading UTF-8 BOM is stripped,
  and a denylist that is UTF-16, not UTF-8, unreadable or a directory
  (at the default path too) stops the run with exit 2 and nothing
  written (audit A2-0045, A2-0158, A2-0458, A2-0448).
- `taxjson redact` covers more identity shapes: every Field Value of an
  IB `Account Information` section except a safe list (Account Type,
  Base Currency, ...), every value cell of a multi-cell address (CSV
  and HTML, inline tags included), every `Label:` cell in a row
  wherever it sits, uncoloned `SIN,` / `Phone,` / `Tax ID,` label
  cells, holder / party labels and columns by pattern (Payee Name,
  Recipient, Trustee, Legal Name ...; any other `X Name` column is
  listed for REVIEW), French comma labels (`Nom du client,`), dotted
  SINs and spaced / dotted SSNs, a US ZIP in its own cell, and
  upper-case bech32 addresses. Account ids are matched
  case-insensitively in the content and the file name, and a column
  header row is no longer altered by the `Name,` line rule (audit
  A2-0046, A2-0455, A2-0456, A2-0457, A2-0460, A2-0759, A2-0762,
  A2-0763, A2-0764, A2-0765, A2-1386, A2-1389, A2-1390, A2-1391,
  A2-0451, A2-0452).
- `taxjson redact` shares transaction-id and wallet pseudonyms across
  every file of one run, like account ids: two redacted Coinbase
  exports no longer share an id (taxjson-sort --dedup dropped a real
  trade) and a redacted Kraken trades + ledgers set still links each
  trade to its ledger rows (audit A2-0454, A2-0766).
- `taxjson redact --check` exits 1 when an account id appears only in
  the file NAME (audit A2-0459); a truncated UTF-16 input is one
  refusal line and the rest of the batch is still redacted (A2-1392).
- `taxjson-generate-parser` no longer sends an account id from the
  input's file name to the model API: the default brokerage, class and
  DEFAULT_ACCOUNT names use a placeholder, and its messages mask the
  file name (audit A2-0447).
- `scripts/check-pii.sh` no longer passes silently on a private denylist
  saved with a UTF-8 BOM (the BOM is dropped, so the first pattern
  works) and fails closed on a UTF-16, non-UTF-8 or directory denylist
  instead of reporting clean (A2-0044, A2-0450, A2-0458). It now also
  catches a lower-case IB account id (u + 7-8 digits, as IB HTML element
  ids carry it) in content and file names (A2-0449), a labelled SIN in
  any separator form including unspaced and dotted (A2-0760, A2-1387),
  a labelled SSN / TIN / Tax ID (A2-1387), and an 8-9 digit value under
  an Account column in the pre-push `--diff` scan of a .csv/.tsv
  (A2-1388).
- `taxjson sanity --json` and the file-given-twice / file-in-two-groups
  messages mask an account id in a holdings file name, as the text
  listing already did (audit A2-1380); a holdings argument that is a
  symlink loop is a one-line error instead of a traceback (A2-1392).
- Every parser message (Questrade, RBC, Webull, Kraken, Coinbase, the
  generic importer, security-override and .tt errors) now names its file
  the masked way IB's already did, so a download named after an account
  number prints `55***_activity.csv`, not the number; the `run` stage
  line for a .tt file too. The IB diagnostics and the other parsers are
  now pinned by tests (audit A2-0461).
- Kraken notes, errors and skip summaries now mask every ledger refid
  and txid to its first two characters + *** (the multi-leg instant-trade
  note, the orphan-leg skip count, the both-sides-many-legs refusal and
  the unparseable-cell errors printed it in full into the console, .sum
  and .diag; audit A2-0756, A2-0757, A2-1381). The id itself stays the
  work-JSON transaction id.
- **PII gate (`scripts/check-pii.sh`, pre-push, release).** The text
  inside binary files is scanned (PDF /Author, DOCX creator, PNG text,
  spreadsheet cells — tree, ad hoc and the binaries a push adds); an
  account-id shape in a folder name, an 8-9 digit value under an
  `Account` / `Account #` CSV column, Webull's bilingual account line and
  an IB id inside a token (`_U<id>Body`) are hits; identities get the
  e-mail allowlist; branch and tag names a push publishes are scanned; a
  ':' in a path no longer unmasks a denylist hit. `release.sh` runs the
  pre-push gate itself, a missing `ruff` fails `ci.sh` (it is in the
  `[dev]` extra), and the GitHub workflow runs the consistency, tax-rules
  and PII stages (audit S023-00, S024-04/07/09/11/13/14/16/21/23/24,
  S025-06).
- **`taxjson-generate-parser` refuses a sample that still carries
  personal data** (account ids, names, contact details, denylist matches;
  `--allow-unredacted` overrides), rejects `--sample-lines` below 1, and
  tells drafted parsers to read required cells with
  `parse_strict_number` (audit R1-342, S033-18).
- **`taxjson redact` fixes.** An account id on a line with a very long
  field is now found (the report claimed every occurrence replaced
  while it stayed); `account = "..."` / `broker_account = "..."` keys
  (the live-holdings TOML) are ids; the City/State/Street2/Country
  columns of an IB Flex AccountInformation section and a value in the
  cell after `Phone:` / `SIN:` / `Payee:` / `Beneficiary:` are
  redacted. The street pattern no longer swallows the next CSV field
  (a dropped column). One invocation gives every account its own
  pseudonym and never writes two inputs to the same copy (`--force`
  silently kept only the last); a file-name id keeps the content's
  placeholder. An unreadable input or `--out` is one line and the rest
  of the batch is still redacted.
- **`redact` removes US city/state/ZIP lines** ("Springfield, IL
  62704-1234") like Canadian postal-code lines.
- **redact matches the private denylist the way check-pii does.** A
  denylisted number written with spaces or dashes (`1122 3344`) is
  replaced, a denylisted word in the file name is replaced in the output
  name (and counts for `--check`), and a `TAXJSON_PII_DENYLIST` that
  names a missing file stops the run instead of silently turning the
  denylist off (audit R1-345).
- **redact: Québec addresses and accented or ambiguous names.** A
  French-order street line (`1234 rue Saint-Denis`) is blanked like an
  English one; names with accented letters (`Josée Tremblay`) are
  recognised; a name that contains a statement word (`Bill Sample`,
  `Jane Price`) is listed under REVIEW instead of passing silently, and
  a flat CSV's Description column gets the "may still name people" note
  (audit S036-16, S037-05).
- **redact: label cells, other id columns, plan parties.** A Webull-style
  `Account Number / Numéro de compte:,,,,<id>` or `Name:,<name>` preamble
  row has its value replaced (the id survived and the report said "none
  found"); client / plan / portfolio / acct-number columns are id
  columns; annuitant, subscriber, beneficiary and holder/customer-name
  columns are blanked, each cell in its own position (audit R1-341,
  S037-00, S037-02).
- **`taxjson redact` removes the holder's name from Coinbase and IB
  Flex/HTML exports.** Coinbase's `User,<name>,<id>` line, the `Name`
  column of an IB Flex `Account` section and the Name cell of IB's .html
  statements were kept. IB ids glued to letters (HTML element ids) were
  collected but not replaced, while the report said "every occurrence
  replaced"; they are replaced now, and the report checks the copy and
  lists any id it could not replace.
- **Security/privacy audit (2026-09).**
  - `taxjson redact` also strips names in free text (`Initiated by`,
    `Payee:`, honorifics, `wire from`, the RBC `Account: …,` holder,
    Webull preamble name/street/city lines), account numbers after
    `acct` / `a/c` / `transfer from|to` and alphanumeric ids, IB Flex
    `AcctAlias`, phone numbers, Canadian postal codes, street
    addresses, SIN/SSN-shaped numbers, and crypto wallet addresses and
    exchange transaction ids (stable same-shape pseudonyms). It refuses
    `.xlsx`/binary input (it used to write a corrupt, unredacted copy),
    fails closed on an invalid `--also`/denylist pattern (exit 2,
    nothing written), `--check` exits 1 when it finds something, and
    the report lists (by line number) free text still worth reading.
    It strips what it recognises — review the output before sharing.
  - Owner-only files: `taxjson` and every `taxjson-*` tool add 077 to
    the umask, so `work/`, `reports/`, `filed/`, `export/`,
    `checklist.json` and a new project's `inputs/<account>/` are
    0600/0700. `fetch` writes statements 0600 and tightens an existing
    `inputs/<account>/` to 0700. Existing projects: `chmod -R go-rwx`.
  - `taxjson serve --host <non-loopback>` requires a random per-run
    token (printed URL `?token=…`, then an HttpOnly cookie); `/healthz`
    no longer returns the project path.
  - `fetch`: the Questrade token is only sent to
    `https://*.questrade.com`; `~/.questrade_token.part` is created
    fresh (O_EXCL, never through a symlink); the live-holdings TOML
    escapes broker-supplied strings.
  - `python-multipart >= 0.0.18` (CVE-2024-53981); Dependabot for pip
    and GitHub Actions; the dev repo ignores run outputs (`work/`,
    `filed/`, `export/`, `checklist.json`, `*.xlsx`, `*.pdf`).
  - `scripts/check-pii.sh` / pre-push: scans the identities of new
    commits and annotated tags (your own configured identity only
    warns), tag messages, the new path of a pure rename; denylist
    matching is case-insensitive and separator-tolerant and never
    echoes the private string; 8-digit file-name tokens are exempt only
    when they are real dates; `pii-ok` must be a comment marker
    (`# pii-ok` / `pii-ok:`).
  - SECURITY.md: keep a tax project repository private; FX comes from
    the Bank of Canada Valet with Yahoo Finance as a fallback; what
    Yahoo lookups reveal (held tickers and dates).
  - Test fixtures copied from real statements replaced with synthetic
    values.
- **check-pii closes five gaps.** Ad-hoc mode matches file names
  relative to the scanned argument (a home directory named after its
  owner no longer fails every scan) and hides each matching path
  component; every file that is not a known binary type is scanned as
  text and fails closed on NUL bytes whatever its extension (a UTF-16
  `.tsv` or `.log`); an exempt synthetic token no longer hides a real
  id, account number or e-mail on the same line or in the file path; a
  configured `TAXJSON_PII_DENYLIST` that is missing, or a denylist that
  cannot be read, fails instead of passing on the generic patterns; and
  a spaced or dashed 3-3-3 number with a valid SIN check digit is a hit.

### Docs and tests

- Tests-tagging round (re-audit A2-0167 ... A2-1527, tests-tagging lists
  01-03): fix and law-citing tests carry the @rule / @rule_absent ids they
  pin; tests were added or strengthened wherever a mutant of a rule's
  implementing line survived every tagged test; @rule_absent pairs now
  fail when their country gate is removed (three that could not are
  plain @rule tests now); the unpinned baseline is down to three negative
  statements (CA-INC-02, US-BASIS-02, US-WASH-07).
- Docs that contradicted each other or the code (re-audit A2-0947 to
  A2-0954, A2-1619 to A2-1629): `distributions.map` per-share amounts
  are in the project's base currency (README, `--help`, tax-logic
  CA-DIST-01 / US-DIST-01, and the run's NOTE name it); the stale
  KNOWN_ISSUES entry calling US January fund dividends deferred is gone
  (`ric_january_dividends` implements it); the deck names Webull's
  Trading Summary CSV and its missing income; SECURITY.md and the deck
  give the Bank of Canada noon-rate period (Yahoo only before May
  2007); the crypto-send pairing texts say "another of your crypto
  accounts, from 10 minutes before to 3 days after"; README says the
  generic importer spells `.V`/`.CN`/`.NE` as `.TO`; KNOWN_ISSUES says
  the RBC ticker-change hint needs the old symbol's rows in an export;
  `taxjson --help` states the exit codes (2 = usage or an unreadable
  input, 1 = failure or a finding); README documents
  `crypto-sends --unpair`.
- Source comments, tests and this changelog no longer quote amounts or
  positions from the maintainer's own books; the futures tests use a
  synthetic CL round trip (A2-0758, A2-1382, A2-1383, A2-1385).
- Tests: mutation pins for fill-crypto, the tax estimate, merge2,
  option-boundary, crypto-sends, income dating, the country helpers
  and the settlement calendars (audit G1-0): the kill score of those
  eight modules went from 59.5% to 89%; the survivors left are
  equivalent mutants (epsilon boundaries, formatting, dead defaults).
  No behaviour change.
- **Docs: IB trust distributions and affiliated persons.** tax-logic
  CA-INC-DATE-TRUST, README and KNOWN_ISSUES say why an IB row keeps
  its pay date in a Canada project (IB prints no record date and calls
  a trust's distribution a dividend; the accrual ex date is not used),
  and CA-SL-04 and README document declaring a spouse's or controlled
  corporation's account `sheltered` so its purchases deny your loss
  (audit S057-23, S004-08).
- `scripts/mutation_audit.py` restores the engine sources on SIGTERM,
  SIGHUP and exit; `scripts/mutation_triage.py` has `--help` (audit
  S025-00, S025-04).
- Docs and samples: README T1135 (suffix-less symbols are the CRYPTO
  bucket), the git-identity exception to "nothing personal leaves the
  machine", form-export's outlays split (long sales, and under grant timing a
  written option's write commission), REFERENCES
  (no s.53(1)(h) citation), SECURITY.md egress list; `examples/README`
  step 3 passes `--taxable`, the IB and Webull demo rows are fabricated
  and self-consistent; real trade figures are gone from this changelog
  and from test fixtures (audit R1-344, R1-355, R1-40, S023-13, S023-17,
  S023-18, S023-22, S023-24, S024-00, S079-13, S079-14).
- **Docs: Webull income.** The Webull Trading Summary carries no income;
  README and KNOWN_ISSUES say so and document the `.tt` `INTEREST` line
  for T5 interest (R1-96).
- Help/docs: `fetch --positions` no longer cites the removed
  `taxjson verify`; the module docstring no longer lists `taxjson
  show`; `taxjson-fees-sum` usage examples; futures filter help;
  `reconcile-slips --json`, `serve --port` and `harvest --ibkr-port`
  document themselves/their defaults. README: `[estimate]` vs
  `[instalments]` keys split into their own blocks, the chaining row's
  duplicate example and check-filed's nonexistent `--strict` fixed,
  fetch windows described as the code does them, `inputs/slips/` in
  the file table, the estimate sample shows the real FTC label.

## v0.16.0 (2026-09-25)

- **`taxjson sum`: FOR THE RETURN block** — per taxable account, the
  proceeds, cost (ACB), outlays, gain and denied superficial losses a
  return's capital-gains entry asks for (TurboTax: proceeds / ACB /
  outlays), on form-export's Schedule 3 convention; proceeds − ACB −
  outlays equals the allowed gain. Also under `filing` in `--json`.
- Docs: deck and website refreshed for s.49 option timing, `option-boundary`
  and `checklist`; the site's FAQ named a `taxjson filed` command that does
  not exist (it is `close-year` / `check-filed`).
- **`taxjson checklist`**: the filing checklist as a command. Every
  step is auto-detected by running the command that proves it (run,
  sanity, find-missing-history, elect --pending, audit, option-boundary,
  reconcile-slips on `inputs/slips/`, form-export, t1135, check-filed,
  git status); steps no command can prove are confirmed with `--done ID`
  (or `--skip`, `--undo`, `--reset`; marks in `checklist.json`) and a
  mark never hides a later detector finding; `--walk` steps through the
  open items interactively; `--quick` skips the slow detectors; `--json`
  for machines. Exit 1 while anything is open. The report prints a
  `checking ID (command) ...` line on stderr before each slow detector,
  and `--walk` checks one step at a time so the first open step appears
  at once instead of after the whole list (which took a silent minute
  on a big book).
- **`docs/filing.md`**: the filing checklist — freeze inputs, build and
  clean, reconcile to the slips, produce the numbers, file and lock,
  after assessment — each step with the command that proves it. Linked
  from the README workflow and documentation sections.
- **`taxjson init` scaffold**: every switch in one canonical, column-aligned
  layout (`year`, `country`, `province`, `base_currency`,
  `source_currencies`, `tax_date`, then the commented defaults for
  `cross_asset`, `fx_cash_gains` and — Canada only — the three
  `option_*` keys, each with its default and a one-line meaning), and
  `holdings = [...]` shown on the margin account. Two projects' files now
  diff only where their values differ.
- **RBC Direct**: the 2022-vintage export's `10-Jan-22` settlement dates
  parse (they silently fell back to the trade date); a dividend from a
  company with EXP in its name (PEYTO EXPLORATION) is no longer classed
  as a 0-quantity trade — the trade-code tokens match as whole words.
- **`taxjson estimate`**: 2024 rate vintage (federal 15% first bracket,
  BPA 15,705; ON/BC/AB and IRS 2024 tables) so a 2024 project no longer
  runs on the 2025 tables.

Canada rules, from a design + impact study and an adversarial audit of
the engine against the Act (docs/design/canada-rules-2026-09.md):

- **Option premium timing (ITA s.49(1)–(4)).** A written option's
  premium is a capital gain in the year WRITTEN; a buy-back is a loss in
  its own year; expiry adds nothing; an assignment folds into the share
  leg with no grant record (the s.49(4) post-amendment state). New
  `[settings] option_premium_timing = "grant" | "close"` (Canada default
  grant; the US engine keeps close = §1234) and
  `option_grant_timing_since = YEAR` (contracts written earlier keep
  close timing — the transition from books filed the old way; default:
  the project year). Same-year round trips are unchanged in total; only
  year-straddling contracts move. Threaded through run, the raw pass,
  wash pass, blended pass, audit, explain, close-year/check-filed and
  the web what-if; `summary.option_premium_timing` records the choice.
  Grant records carry `grant: true` and a note; `audit` shows WRITE.
  `option_buyback_loss_superficial` (default false) decides whether a
  buy-back loss is fed to the superficial-loss rule; the strict reading
  can permanently deny the whole loss on a same-minute order
  correction when a registered account holds the same series.
- **`taxjson option-boundary`**: every written option whose write and
  close straddle a tax-year boundary (or that is open at year end), with
  where each amount lands and — using the `filed/` locks — whether a
  filed year needs a T1-ADJ (assignment after the grant year was filed).
- **s.40(3) deemed gain**: a return of capital that drives ACB below
  zero is booked as a qty-0 gain in the distribution year, ACB reset to
  nil (was a warning; the whole amount landed in the sale year).
- **s.54 short sales**: a new short sale or written option is not an
  acquisition and never triggers a superficial loss on a cover loss (the
  US §1091(e) re-short branch applied before); a long purchase held at
  day 30 still does, and its bump lands on that long holding.
- Documented (KNOWN_ISSUES) from the audit: superficial-loss attribution
  order between taxable and registered triggers; the s.40(2)(g)(iv)
  path; second-order denials from the bump date; the estimate's
  suffix-based dividend classification; interest expense not surfaced;
  spin-off wording; pre-2001 loss rates; `days_held` on trade dates.
  REFERENCES: s.39(1.1) for fx-cash, short sales' income character.
- `find-missing-history`: the phantom walk pooled positions per
  (symbol, account, CURRENCY) and had no buy-before-sell tie-break at
  equal timestamps, so a Norbert's-gambit pair (sell SAMPLF.TO in CAD, buy
  SAMPLF.U.TO in USD the same morning, one symbol after the ticker map)
  read as a phantom short "affecting" the year while the engine
  had matched every sale correctly. Pools are per (symbol, account) like
  the engine's, and buys sort before sells at equal times.
- Questrade: a US-listed security bought in a CAD-only account (RESP)
  is settled in CAD with `EXCHANGE RATE r` in the description — Price and
  Gross Amount are USD, Net Amount is the CAD paid, and the Currency
  column says CAD. The parser filed such buys as `.TO` (a CDR-shaped
  symbol the `DISTINCT` rule then kept apart from the real US pool) with
  the USD gross taken as the CAD cost, under-stating the ACB by the
  whole exchange rate (seen on real RBC rows). They are
  now the `.US` listing, costed at the CAD actually paid, and their
  dividends key to the US listing too.
- Kraken: the 2026 ledger format (new `amountusd` / `feeusd` /
  `balanceusd` / `feecurrency` columns) parses as before, and
  `amountusd` — Kraken's USD valuation at credit time — now prices
  staking rewards at the parser (income and the acquisition cost of the
  rewarded coins alike) instead of leaving them to the price filler,
  which had no quote for HYPE and booked those rewards at $0. Earn
  allocation / deallocation / autoallocation and `hybridearn*` rows
  (moves between the spot and Earn wallets) are recognised non-events
  rather than "unhandled" types.
- Removed `taxjson show` (it printed `reports/NAME.sum`; use `cat`) and
  `taxjson verify` (Questrade-only live-positions check; `taxjson sanity`
  with `holdings = [...]` covers every broker and runs at the end of
  `taxjson run`).
- Docs refresh: README (website and REFERENCES links, the broker
  cross-check and the privacy gate under Verification, audit history,
  current test counts), a twelve-slide overview deck
  (`docs/deck/taxjson-deck.{html,pdf}`, rendered with WeasyPrint), and
  taxjson.com gains a commands-at-a-glance section.

## v0.15.1 (2026-09-18)

- `scripts/release.sh` accepts `vX.Y.Z` as well as `X.Y.Z`.
Pre-release audit (2026-09-18) of everything since v0.15.0 — two
independent adversarial reviews, every finding reproduced before it was
fixed:
- `scripts/check-pii.sh` failed OPEN in several ways: file names with an
  apostrophe or a space aborted `xargs` silently ("clean"); a malformed or
  CRLF denylist line disabled that entry; UTF-16 exports read as binary
  and were skipped; errors went to /dev/null. It now uses NUL-delimited
  file lists, validates every denylist regex, fails closed on scanner
  errors and on text files containing NUL bytes, scans file NAMES (IB
  names downloads after the account id) and — via the hook — commit
  messages, resolves ad-hoc paths before changing directory, and masks
  hits without interpreting the pattern.
- `scripts/hooks/pre-push` scanned `git log -p` of `remote..local`, which
  omits merge-commit resolutions and, when the remote tip is unknown
  locally, dies silently and lets the push through. It now scans the net
  `git diff remote local` (or everything not on the remote), plus the
  commit messages, and fails closed.
- `taxjson redact`: ids under five characters and date-like 8-digit
  numbers are no longer ids (small "ids" were rewriting quantities and
  prices); the integer part of a decimal is not an id; `_` is a word
  boundary; IB `DU`/`F` ids, Fidelity/Schwab-style `Z…`/hyphenated ids,
  `ClientAccountID`/`AccountAlias` columns, `Owner:`/`Customer:` rows and
  quoted `"Name: Last, First"` headers are recognised; the id in the
  FILE NAME is replaced too; UTF-16 and cp1252 exports are decoded (and
  noted) instead of passing through unredacted; CRLF, BOM and sibling
  cells' quoting are preserved; an invalid denylist/`--also` regex is a
  note, not a traceback; the report shows id lengths, never digits;
  an existing copy needs `--force`, a symlink is never written through.
- `taxjson shares`: an empty `--taxable`/`--sheltered` scope is an error,
  a non-table `[accounts]` entry no longer tracebacks, JSON quantities
  are rounded.
- `taxjson sanity`: malformed holdings files (non-numeric or non-finite
  quantities, a `[holding]` table) are clear errors; a `+` in a
  configured holdings path no longer breaks the config-driven pairing.
- `install.sh`: only exact `vX.Y.Z` tags are installable (an rc or
  four-part tag never ships), the body runs inside `main()` so a
  truncated download executes nothing, an existing non-symlink
  `~/.local/bin/taxjson` (pipx?) is refused rather than replaced, the
  install dir is canonicalised, and a failed run says re-running resumes.
  `dev-setup.sh` warns when the hook cannot be installed (worktree,
  `core.hooksPath`, an existing hook) and creates the denylist 0600.
- Scope: the US engine is labelled EXPERIMENTAL — README, the country
  table, a note printed by `taxjson init --country usa` and at the start
  of every US `run`. Its rules are implemented and unit-tested but have
  never been validated on a real account; Canada is the supported
  product. CONTRIBUTING gains a "Help wanted: broker exports" section.
- `taxjson redact FILE...`: strip account numbers and identity from
  broker exports while keeping every row shape — same-length placeholders
  (`U99900001`, `99900001`) applied consistently across the file,
  IB Account Information name/alias/address rows, `Name:`/`Client:`
  header lines, e-mail addresses, and the private denylist. Writes
  `NAME.redacted.EXT`, never touches the input, reports masked ids only.
  Verified on real IB, Questrade, RBC and Webull exports: the copies
  parse to identical tax objects apart from the account field.
- `taxjson shares`: the combined quantity held of each symbol across all
  accounts (post ticker.map, wash-adjusted where built) with a
  per-account breakdown and combined book cost; `--taxable` /
  `--sheltered` scope, `--options` to include contracts, `--sort qty`,
  `--json`.
- `scripts/check-pii.sh`: personal-data / secret scan — broker account-id
  shapes, home paths, unlisted e-mail addresses, credential-looking
  strings, and a private denylist kept outside the repository
  (`~/.config/taxjson/pii-denylist`, scaffolded by `dev-setup.sh`). Runs
  as a `ci.sh` stage in every mode and as the `pre-push` hook
  `dev-setup.sh` installs, which scans only the lines a push would
  publish and refuses on a hit. On a public repository a push is
  publication; this is the check that sits in front of it.
## v0.15.0 (2026-09-17)
- Gate: `scripts/ci.sh` runs with stdin detached (`exec </dev/null`).
  CLI tests spawn `taxjson run` subprocesses that inherit stdin, so a
  gate started from a terminal (e.g. by `scripts/release.sh`) stopped at
  a fixture's election prompt waiting on the keyboard, then recorded the
  default and failed the non-TTY deferral test. The pending-election
  tests also pass `stdin=DEVNULL` themselves.
- Tests: the two tests that drive pipeline stages in-process now capture
  their progress lines, so the gate prints only stage summaries — the
  leaked `==> margin wash-radar pass` lines named a synthetic fixture's
  account and read like a run on real books. docs/releasing.md states
  that the gate never reads a real project.


- Tests: the web-UI tests skip (not error) when `fastapi` is installed
  without starlette's test transport (`httpx2`, in the `[dev]` extra) —
  starlette raises `RuntimeError`, not `ImportError`, for that case.
  `scripts/dev-setup.sh` now installs `[web,fx,dev]` so a fresh clone
  runs the whole suite.
- Open-source release plumbing: a curl one-line installer (`install.sh`,
  latest release tag into `~/.local/share/taxjson`, `TAXJSON_CHANNEL=dev`
  tracks main), `scripts/release.sh` (dirty-tree/branch checks, CHANGELOG
  promotion, version bump, full gate, annotated tag, push),
  `scripts/check-consistency.sh` in every `ci.sh` mode, `docs/releasing.md`,
  `CODE_OF_CONDUCT.md`, a feature-request template, and `REFERENCES.md`
  mapping every engine rule (and every deliberate non-feature) to its
  ITA / CRA / IRC source. The old clone-then-run `install.sh` is now
  `scripts/dev-setup.sh`. Project home moves to github.com/taxjson/taxjson.
- KNOWN_ISSUES: two cited scope entries — IT-479R para 25 (option written
  in one year, exercised in the next) and non-eligible dividends in the
  estimate.
- Removed the Qt desktop app (`taxjson gui`, the `[gui]` extra, the
  `taxjson-gui` entry point, `packaging/`, and its tests). It
  duplicated the `sum`/`positions`/`harvest`/`estimate` tables behind
  a ~100MB dependency that stayed outside `[all]`, and its commit
  history was audit fixes rather than features. The local web UI
  (`taxjson serve`) stays. Recoverable from git history.
- IB parser: a `Ca` (cancellation) row in the Transfers section
  consumes its original leg (same symbol, date, opposite quantity,
  same type). IB lists a reversed ACATS/ATON leg as original + Ca +
  rebooked rows; an RRSP move (IB -> Questrade) could carry one
  symbol five times — arithmetically -N, but the two Ca legs read as +N
  ACQUISITIONS to the superficial-loss walk and littered `taxjson
  events`/`transfers`. A Ca whose original sits in an earlier
  statement is kept as a reversing leg with a note.
- IB parser: per-fill Transaction Fees rows all fold into their Order
  row. IB levies UK Stamp Tax per fill while the Trades section
  carries one Order row, so a buy filled in two lots had two levy
  rows; the taken-once fold sent the second out as a standalone FEE
  that never reached the ACB (an LSE buy). Each trade now keeps an unlevied quantity so several
  rows can fold into it; two same-day trades with one levy each still
  pair 1:1.
- `taxjson.toml` accounts accept `holdings = [...]` — paths of the
  account's broker positions files (`[[holding]]` TOML). `taxjson sanity`
  with no arguments builds the paired groups from it (accounts that
  list a common file merge into one group; an account whose file is
  missing is noted and left out), explicit arguments still override,
  and `taxjson run` finishes with the same check as a WARNING — never
  a failing exit, since same-day trades not yet in the CSVs differ
  routinely. Only a compare against the broker catches a stranded
  position every internal report agrees on.
- Engine (Canada): the settle-straddle re-denomination now compares
  the trade's execution MOMENT (date and clock time) against the
  split's, not dates alone. IB posts corporate actions in an evening
  batch (20:25) dated the trade day, so a sale executed that morning
  and settling T+1 was pre-split — the date-only "strictly between"
  test skipped it, the ladder split the pool first, and a fractional
  split of that shape left phantom shares with stranded cost in a
  sheltered account. Found by `taxjson
  sanity` against the broker's positions; taxable books unchanged.
  The straddle fuzzer gains an "evening" placement for this shape.
- `taxjson sanity` matches an option row through its `underlying`
  field when the file spells the option root differently from
  taxjson (IB names the Montréal contract on SAMPLD.B by the underlying,
  a positions export by the exchange root `SAMPLD`); the fallback fires
  only when the file's spelling has no taxjson counterpart, is noted
  in text mode, and listed as `matched_via_underlying` in JSON.
- `taxjson sanity` gains a PAIRED form alongside the aggregate one:
  `ACCOUNT[+ACCOUNT]=FILE[+FILE]` checks only those accounts against
  only those holdings files, as its own group (a repeated left-hand
  side merges: `margin=ibkr.toml margin=webull.toml`). Many-to-many
  because a taxjson account can span several broker accounts and one
  broker export can cover several accounts. The aggregate form is
  blind to a position booked in the wrong account (the totals still
  agree); the paired form flags it per group. Both forms mix freely;
  an account or file placed in two groups is a usage error. JSON gains
  a `groups` list; text mode adds an ACCOUNTS column when paired.
- `taxjson scan` prints a root-aware MAP-UNUSED note for ticker.map
  rules that match no parsed symbol — counting OPTION roots (a rule
  with no stock rows is still live when option trades carry its root,
  since identical-property matching folds the option's underlying
  through it) and suffix-less codes. A root-blind dead-rule check could
  prune live TOBASE/GLOBAL rules, splitting every
  affected option's identity class (an adjusted root's assignment legs
  stopped netting against the original root's short puts). A note only: it never fails
  the scan.

Deep audit, round eight (2026-09-14/15): a privacy/secrets sweep of
the tree AND the full git history ahead of going public, a parser
coverage audit that classified every row category in a full set of
real broker exports (IBKR Flex, Questrade, RBC, Webull, Kraken,
Coinbase), and an adversarial pass over the last cold surfaces (GUI,
web what-if, watch/diff/reconcile-slips/distributions, orchestrator
edge cases). Suite at 2,118 tests.

Parser coverage (real rows the parsers dropped or mis-booked):
- Kraken stablecoin (USDC/USDT/DAI) staking rewards were valued at $0
  income: the asset was folded to `USD` before the reward row was
  built and the price filler skipped `USD`. Priced at 1.0/unit with
  income = quantity; no phantom stablecoin position is opened.
- IBKR option EXERCISE (`Ex` code) was booked as a plain BUYSELL at
  price 0 — the premium realized as an option loss instead of rolling
  into the stock leg's basis/proceeds as an assignment does. Now an
  ASSIGN leg; verified end-to-end in both engines.
- IBKR `Trades / Forex` conversions (hundreds per year) fell through
  the asset filter with no accounting — now counted as recognized
  non-events with a KNOWN_ISSUES entry (FX conversions are not
  modeled as dispositions). `Transaction Fees` (UK stamp duty) fold
  into the same-day trade's fee (else a symbol-bound FEE row);
  `Commission Adjustments` (refunds) become negative FEE rows; tender
  / voluntary-offer corporate actions are recognized (zero-proceeds
  round trips are no-ops, cash allocations surface loudly instead of
  vanishing). IB `Fees` section sign corrected (charges are positive
  FEE amounts, matching every other emitter — a market-data fee had
  registered as a cash INFLOW in fx-cash).
- Kraken: `transfer/transferpeertopeer` outbound stablecoin is custody
  evidence (send NOTE fires); fiat-base fills and fiat-fiat dust sweeps
  no longer create phantom `USD`/`CAD` assets.
- Questrade: `REI` dividend-reinvestment rows are purchases at the
  reinvest price (DRIP shares never entered inventory); `CIL` cash in
  lieu of a fractional stock-dividend share is booked as a same-day
  fraction buy/sell pair; `FCH` ADR custody fees become FEE rows;
  `FXT` conversions are labelled non-events.
- Row accounting everywhere: the IB parser now reconciles under
  `--lint` (every row consumed or counted); recognized non-events
  (subtotals, metadata sections, deposits, staking-wallet shuffles)
  report in a calmer note than genuinely unclassified rows; subtotal
  rows no longer trigger false "dateless fee" or "currency ''"
  warnings; a transfer-only file no longer trips the
  "parsed to 0 transactions" alarm.

Cold surfaces (round eight):
- `run --account X` printed "filed YEAR: OK" against artifacts it had
  just declared stale (wash pass skipped); now "not checked" under
  `--account`, and `sheltered_base.json` is rebuilt when the named
  account is sheltered.
- GUI: the summary shows TAXABLE / SHELTERED subtotals and the tax
  pane's "Realized (taxable)" comes from the taxable subtotal (both
  silently included sheltered accounts); quantity columns use the
  quantity formatter (crypto positions read 0.00).
- `taxjson-apply-distributions` keys the record-date balance on
  SETTLEMENT for settle-basis projects (`--date-basis`; the wrapper
  passes the project's `tax_date`) — an unsettled sale still holds on
  the record date, a buy traded on the record date does not.
- Web: mixed-currency holdings render per-currency costs with a MIXED
  marker (cells were blank); a what-if loss in a sheltered account is
  labelled "not deductible (registered account)".
- `reconcile-slips` excludes crypto accounts (exchanges issue no
  T5008/1099-B, so it could never exit 0); `taxjson-diff` reports a
  missing/malformed input cleanly; `TAXJSON_OFFLINE` now also covers
  the price chain (harvest, watch --harvest, GUI harvest).

Privacy sweep (pre-publication): the working tree carried no secrets
but many real-portfolio fixtures and samples; all replaced with
synthetic equivalents (merger book, RBC option, custody declaration,
README sample tables, dividend-snap examples, fuzz-doc paths). The
git HISTORY still holds real account numbers and disclosing commit
messages — see the release checklist: squash before going public.

- Questrade DRIP (`REI` / "Dividend reinvestment") rows are booked as
  purchases at the reinvestment price (`REINV@C$10.12500` in the
  description; Price column is 0) — the DRIP shares never entered
  inventory before (counted skip). The cash dividend keeps its own
  Dividends row. Questrade `CIL` (cash in lieu of a fractional
  stock-dividend share) is booked as a same-day pair: the fraction at
  its $0 stock-dividend cost, then sold for the cash.

- `scripts/ci.sh` — the authoritative local CI gate (GitHub Actions
  is billing-gated on this private repo): ruff critical tier + the
  full suite + the three property fuzzers at CI depth; `--nightly`
  for 5000/3000/1600 books, `--mutation` to append the mutation
  harness, `--quick` for lint + suite. Appends one line per run to
  `.ci/history.log`. The Actions workflow is slimmed to Linux only
  (macOS runners cost 10x minutes) and gains manual dispatch and a
  nightly deep-fuzz job for whenever Actions is enabled.
- `scripts/mutation_audit.py` refuses to run without `--yes` and
  offers `--list-targets` — a bare invocation used to start the
  hours-long in-place mutation run immediately.
- Docs: README gains a Verification section (audit authority,
  fuzzers, mutation testing, audit rounds, filed-year locks);
  CONTRIBUTING documents the gate, the fuzzers' depth env vars, and
  the mutation harness's in-place hazard; KNOWN_ISSUES header
  updated to seven audit cycles.

## v0.14.0 (2026-09-11)

Deep audit, round seven (2026-09-10): four parallel audits over the
COLD surfaces — the US engine on its own terms, corporate actions +
elections + the forward-looking advisory tools, the security/I/O
boundary, and the stage tools + FX conversion — plus a cross-tool
reconciliation of every filing command against the audit on real
books (agree per symbol to the cent). 27 confirmed defects fixed and
pinned; suite at 2,077 tests.

US engine (wrong numbers):
- Holding period follows Rev. Rul. 66-7: an end-of-month acquisition
  is long-term from the 1st of that month a year later (Feb 29 ->
  Mar 1, not Mar 2; Feb 28 common-year -> Mar 1). The old rule — and
  its pin — had both directions wrong. `held_more_than_one_year` is
  now module-level and `harvest`'s "LT IN" date derives from it
  (was a fixed +366 days, one day early a quarter of the time).
- §1223(3) tacking is per-share: a replacement lot larger than the
  match is split at the match point (in the match loop, or at lot
  creation when the loss preceded the buy) so only the matched shares
  carry the tacked period and the §1091(d) bump. A 200-share
  replacement for a 100-share loss now terms 100 LT + 100 ST.
- Same-timestamp assignment legs: the OPTION leg now sorts before
  the STOCK leg in both tie-break ladders — stock-first input order
  silently dropped the premium (proceeds 10,000 instead of 10,300).
- Documented as out-of-scope with statutory cites: §1091(e)(1)
  long-sale trigger on short-cover losses, the IRA-vs-taxable
  already-sold-replacement asymmetry, advisory-only option
  replacements, FIFO-only (no specific-lot ID).

Corporate actions / elections (money):
- Questrade s.86.1 spinoff: the ACB-reduction ADJUST landed on the
  broker's internal SEC# instead of the parent ticker, leaving the
  parent at full ACB while the spun shares also carried basis
  (double count). The parent ticker is resolved from the statement;
  saved elections keyed under the old id are migrated.
- `elect --set` validates election name and hints against the
  event's action type on EVERY path (re-election after `--reset`
  used to save an invalid election and crash the next run with a
  raw KeyError); the "matches no pending event" warning fires only
  when neither source knows the id.
- IB reverse split with cash-in-lieu: the fractional share's
  disposition is booked at the cash and the pool ends at whole
  shares (was 3.3333 dust, CIL row dropped). IB's exact CIL wording
  is unverified — KNOWN_ISSUES entry.

Advisory tools (a wrong deadline costs the loss permanently):
- The wash-radar VIOLATION rescue deadline was the SETTLEMENT bound;
  trading on it settles a day late and the engine denies the loss.
  The radar, sell-check, and the JSON sidecar now carry the last
  safe TRADE date (T+1 rule, weekends skipped) with the settle
  deadline alongside; new `lib/dates.py` is the single settlement
  helper.
- Radar/sell-check/buy-check match on the engine's rename classes
  (SPLIT symbol_new): a loss on OLD.TO followed by a rebuy of NEW.TO
  showed COOLING/EXITABLE instead of VIOLATION.
- buy-check/sell-check honor `DISTINCT` (a CDR pair is no longer
  treated as identical property); a bare root shared by kept-apart
  members reports the worst verdict with an ambiguity note.
- harvest buckets BLOCKED losses as claimable NOW (a further loss
  sale today is clean; only a rebuy is the risk) — `watch
  --harvest` follows. safe-to-sell counts ASSIGN acquisitions; the
  web what-if renders a passed VIOLATION deadline as "loss denied".

Stage tools / FX (silent wrong money and crashes):
- A lowercase or space-padded currency code (`cad `) under `--to
  CAD` was multiplied by the USD default rate and relabelled CAD.
  Codes normalize on both sides; a currency wholly absent from a
  supplied rates file is now FATAL unless `--default-rate` is
  explicit — `taxjson run` users with an unlisted currency get a
  message naming `source_currencies` instead of a silent 1.35x.
- Rates-file parser: malformed lines counted and reported,
  NaN/Infinity/non-positive rates refused, currency columns
  upper-cased.
- `coerce_transaction_row` is the single type funnel: null/wrong-
  typed numerics and non-string symbol/account/date are refused
  with a row-level error naming the field (were bare tracebacks in
  loaders, phantom walks, and the engines; `taxjson-validate`
  itself crashed on wrong types and passed `null` numerics).
- merge2 / convert-currency / sort / fill-crypto all route through
  the shared loader (comment stripping, qty alias); a per-file read
  error no longer yields an EMPTY merge at exit 0.
- ticker.map DELETE-then-GLOBAL order is the same in merge2 and the
  standalone tool (equity vs crypto accounts read one map
  differently).
- `taxjson-sort --dedup` never drops rows (shared validator; its
  warnings now surface in the .sum); `.tt` books get per-file fill
  disambiguation so two identical hand-entered lines no longer
  collapse under dedup; fill-crypto's cache keys on the resolved
  Yahoo id and leaves qty=0 rows untouched.

Security / I/O boundary (critical surfaces verified clean: token
handling, subprocess argv, web server, input parsing):
- Account names are validated (`../x` wrote artifacts OUTSIDE the
  project; `-x` was parsed as a flag). Project directories are
  created 0700; the IBKR Flex download is atomic.
- Credentialed broker requests refuse redirects (CPython forwards
  the bearer across 30x hops) and non-HTTPS API servers.
- `TAXJSON_OFFLINE=1` forbids the two default egress paths (FX
  rates, un-priced crypto rows) — documented in SECURITY.md with
  the full network/egress list; the web UI no longer loads Swagger
  from a CDN; an oversized CSV field is a clear error instead of a
  `_csv.Error` traceback.

Full audit, round six (2026-09-09): adversarial review + rigor sweep
(5,000/3,000-seed fuzzers clean; a NEW settle-straddle differential
fuzzer found one genuine engine bug — promoted into the suite as
tests/test_settle_straddle_fuzz.py, default 200 seeds,
TAXJSON_STRADDLE_FUZZ_BOOKS scales) + hand-verified 2026 estimate
math (60 checks to the cent). Suite at 2,019 tests.

Engine (wrong numbers):
- The per-account balance walk feeding the cover-vs-opening trigger
  gate sorted WITHOUT the phase ladder: a sale settling exactly on a
  split date was subtracted from an already-scaled balance, creating
  phantom shares that both fabricated spurious denials and masked
  real triggers (settle-straddle fuzzer, seeds 113/317/433/781/1433;
  minimal 7-row repro pinned). Now phase-aware like every sibling
  walk; all 1,600 fuzzer seeds hold.
- The straddle re-denomination window keys on the split's SORT date
  (settle when set): a settle-desynced SPLIT row no longer
  re-denominates a trade the pool splits after (was silent phantom
  shares — the pre-eb6a0a3 refusal had caught it).
- HIGH regression fixed: `_replay_moves_on_base` folded evidence-move
  endpoints through the to-base renames, but the RAW-base holdings
  inventory is PER-LISTING (GLOBAL-only) — every TOBASE-pair depot
  flip was skipped and the flipped shares' base cost vanished from
  holdings.toml. Replays now fold through the JOURNAL set only
  (matching the base build); the mispremised pin corrected; the
  renderer's cross-listing base fallback retired.

Detector/evidence hardening:
- The restatement pre-pass disqualifies segments with MAIN-book
  SPLITs in-span (was: a guarded symbol could raise the event count
  and launder its siblings past the near-trade refusal); null-date
  guards added at both scan sites.
- Kraken fiat (USD/CAD/EUR/GBP) withdrawals/deposits are back to the
  counted note — moving your own cash is not custody evidence, and
  the FMV-disposition note was wrong tax advice for it. Stablecoins
  stay in the evidence branch. Coinbase evidence rows normalize the
  symbol, carry the CSV's price currency, and raise on unparseable
  timestamps like every other dated row.

Estimate/ACQUIRED polish:
- `apply_vintage` resets to the default vintage on a missing year
  (was sticky in-process); pre-earliest years documented as using the
  earliest table. Result vintage labels pinned against the applied
  tables; a 2026 US bracket edge pinned.
- ACQUIRED: comma quantities parse; the counter-TRANSFER is emitted
  at the fixed 09:30:00 default so a hand-written pair and an
  expansion hash to identical ids; qty 0 refuses; the Canada error
  prescribes the one-liner only for LONG-direction rewrites (a short
  can't be expressed by ACQUIRED) and the US error mentions it.
- Docs reconciled: SplitStraddlesSettlementError docstring (refusal
  is rename-split-only now), dead contradictory comment removed,
  `taxjson transfers` help/README mention crypto sends, README
  estimate sample refreshed to the Bill C-4 figures, issuer-match
  docstring aligned with the one-extra-token rule.

- 2026 rate tables, vintage-aware: `apply_vintage(year)` selects the
  published table for the project year (latest-at-or-before for
  future years; the printed vintage discloses which). 2026 figures
  verified against the CRA indexation release (x1.02, Bill C-4 14%
  full-year: brackets 58,523/117,045/181,440/258,482, BPA 16,452),
  ON x1.019 (surtax 5,818/7,446, BPA 12,989; 150k/220k bands
  unindexed), BC Budget 2026 (lowest rate 5.06% -> 5.60%, thresholds
  x1.022), AB Bill 32 (x1.02, 8% first bracket now indexed), and IRS
  Rev. Proc. 2025-32 + OBBBA (std deduction 16,100; indexed ordinary
  and LTCG brackets). The 2025 AB 14->15% boundary corrected
  (302,757 -> 362,961). VERIFY notes retained in-code.
- Settle-straddle re-denomination: a SPLIT dated strictly inside a
  trade's settle lag no longer refuses the book — the executed
  quantity/price are re-denominated through the split (qty x ratio,
  price / ratio, money untouched) and booked correctly against the
  post-split pool, with a NOTE. The loud refusal remains only for
  RENAME-splits straddling the lag (re-symboling mid-flight). The
  06-11/06-12/06-13 golden family now all produce the identical $500
  denial.
- `ACQUIRED` .tt sugar: `ACQUIRED <true-date> <time> <sym> <qty>
  <cur> <price> <total> ARRIVED <arrival-date>` — one line for the
  lost-history custody idiom, expanding to the BUYSELL at true
  cost/date plus the DECLARED counter-TRANSFER netting the arrival
  leg. The AmbiguousTransferDateError message prescribes it.

- Crypto withdrawals/sends become custody EVIDENCE (KNOWN_ISSUES
  graduation): Kraken ledger withdrawal/deposit rows and Coinbase
  Send/Receive rows emit TRANSFER rows into the per-broker sidecar —
  `taxjson transfers crypto` shows the full custody history, matched
  send/arrival pairs read as self-custody moves at a glance, and
  unmatched out-legs are the gift/payment candidates. The parse
  prints the FMV note for out-legs (a send that left your ownership
  is a taxable disposition at fair market value — declare a .tt sell;
  Coinbase rows carry the spot price so it's copy-paste). Stablecoin
  evidence keeps its own name (a USDC gift disposes USDC the
  property) though trade books still fold USDC/USDT/DAI to USD for
  pricing; Kraken Earn shuffles stay ignored. Books, gains, and
  `events` unchanged — evidence, not events.
- The transfer-aware differential fuzzer is promoted into the suite
  (tests/test_transfer_fuzz.py): eight generated churn shapes
  (custody pairs, restatement clusters, DECLARED legs near trades,
  chained-gap residue) checked for crashes, conservation, sign
  sanity, and determinism. Default 150 seeds per run;
  TAXJSON_TRANSFER_FUZZ_BOOKS scales it (round-five ran 2,400 clean).

- Derived prices are stored repr-clean: every parser price computed
  by DIVISION (IB corporate-action and transfer branches, Kraken
  trade legs, the crypto price filler) now rounds to 8 decimals
  before storage — `taxjson events` printed the raw double noise
  (`25.810000000000002`) because the round-trippable taxtext views
  deliberately display the EXACT stored value. 8dp kills the noise
  while keeping satoshi-level crypto prices intact; Questrade
  already rounded. The `transfers` view quantities go through
  `fmt_qty` (no scientific notation on large counts).

- Report tables right-align all-numeric columns (money, counts,
  percentages; '-' placeholders tolerated), lining up decimal places
  and cents down every column — `taxjson sum` and every other
  `format_report_table` view, plus the per-transaction views
  (`events`/`divs`/`trades`/`gains`/`fees`/`roc`/`leaps`: quantities,
  per-share rates, and amounts align; the rows stay paste-into-.tt
  parseable). Free-text columns stay left-aligned; detection is
  per-table, so a column with any text cell is left alone. The .sum
  tables, carryover/t1135/form-export, fx-cash, instalments, harvest,
  the GUI, and the web UI already right-aligned.

## v0.13.0 (2026-09-08)

Full audit, round five (2026-09-08): complete-codebase pass — four
parallel audits (adversarial review of everything since v0.12.0, an
engine-rigor re-run, a filing/money-layer audit with hand-computed
CRA/IRS scenarios, and a repo-wide consistency sweep). Engine core
held: 5,000-book conservation sweep clean, and a new transfer-aware
differential fuzzer (2,400 seeds over custody churn, restatements,
declared legs) found zero violations. Suite at 2,001 tests.

Restatement detector hardened (adversarial findings):
- Three discriminators keep genuine tax events out of the event pool:
  a qualifying cluster's first non-declared leg must be an OUT-leg
  (restatements journal out-and-back; contributions are in-first); a
  detected event's total span is capped at 7 days (pad-chaining can
  no longer glue unrelated pairs weeks apart into a symbol count);
  and guarded segments (in-span trade/SPLIT) never raise the count.
- Segments carrying DECLARED legs keep the attestation bless-pad path
  even inside a detected event — the event bypass no longer widens a
  declaration's reach onto gap-chained genuine legs.
- The real 40-symbol Aug-2026 event still detects; the misclassifying
  shapes (3 in-first contribution pairs; week-spaced pair chains) are
  refused. All pinned.

Evidence-driven depot flips hardened:
- Evidence nets are computed on journal-FOLDED keys: a JOURNAL pair's
  legs cancel, so intrinsically-fungible classes are never
  evidence-moved on top of their fold (previously re-symboled shares
  already sold through the other listing).
- The base inventory now REPLAYS the native pass's applied moves
  (endpoints mapped through the to-base fold; same-key moves no-op)
  instead of re-deriving them — the two inventories can no longer
  diverge. Renderer falls back through the fold for base lookups.
- A transfers=false→true toggle unlinks the stale sidecar (was
  consumed alongside the now-in-book rows: double counting).
- 17 mutation-gap pins landed (partial-flip pro-rata apportionment,
  prune/no-op guards, bless-pad and chain-pad boundaries, journal
  fold, relative tolerance, unordered input).

Filing layer (money findings):
- Rate tables: lowest federal rate corrected to Bill C-4's 2025
  blended 14.5% (also reprices BPA and AMT-BPA credits); US single
  standard deduction to OBBBA's 15,750. Both marked VERIFY in-code.
- Filed-year locks snapshot proceeds and US ST/LT subtotals — the
  two audit-proven check-filed blind spots (gain-preserving
  proceeds/ACB shifts, term flips) now drift loudly; pre-upgrade
  locks stay valid.
- `taxjson sum`: the tainted warning was dead code for
  pipeline-written files (rows are stripped to
  manual_reporting_required, so the in-line count was always 0) and
  its text claimed totals INCLUDE what they exclude. Both homes now
  counted separately with accurate warnings; JSON carries
  `tainted_routed`.
- KNOWN_ISSUES gains the T1135 transfer-in cost caveat.

Scan/CLI/consistency:
- `_issuer_names_match` prefix rule tightened to one extra token
  (Brookfield Asset Management vs ...Reinsurance Partners no longer
  match); the name-cap note states honestly that only the first 80
  symbols are probed; `DISTINCT X X` warns.
- `taxjson harvest --options` actually forwards (it was defined but
  never read — the GUI's include-options toggle was silently
  ignored); `taxjson audit --no-color` exists through the wrapper.
- Dead code removed (unused imports across seven modules,
  `_cont_wrap`, `fetch_price_histories`); committed tmp debris
  purged; doc headers agree on all five ticker.map verbs; README
  documents `taxjson transfers`, the sidecar, CDR-PAIR/MAP-BAD?, and
  the evidence-flip exception; CHANGELOG gained its v0.12.0 heading.

- Evidence-driven depot flips: the holdings view now re-symbols ONLY
  the quantities the transfer sidecar PROVES were journaled between a
  security's listings (matched net residuals within a map identity
  class, capped at held quantity; shares never created or destroyed —
  a flip that was flipped back nets to zero and moves nothing, lone
  migration legs are ignored). `JOURNAL` map lines are for
  intrinsically fungible classes (a Norbert's-gambit ETF's units) — an ordinary
  cross-listing stays `TOBASE` and its holdings only merge when the
  broker's own InterDepot rows say so.

- Custody-transfer sidecar + `taxjson transfers` view: a taxable
  book's TRANSFER rows are deliberately not tax events (basis comes
  from the buy/sell history) — but the parse stage silently DELETED
  them, leaving no way to discover a depot flip, listing journal, or
  broker migration later (an IBKR InterDepot row can sit in the CSV
  all along). Excluded rows now land in a
  per-broker sidecar (`work/<acct>_<broker>_transfers.json`) with a
  parse-time note, and the new `taxjson transfers [ACCOUNT]` view
  shows them (plus in-book TRANSFERs from `transfers = true`
  accounts) with the broker's transfer type (InterDepot / Internal /
  ATON). Books, gains, and `events` are unchanged — evidence, not
  events.

- Account-wide restatement detection: when >= 3 symbols in ONE
  sheltered account share zero-net TRANSFER clusters over a common
  few-day envelope, the pipeline classifies the whole thing as one
  broker custody event and nets it — no per-symbol DECLARED
  attestation needed (the evidence is in the data: no tax event
  journals an account's inventory out-and-back to zero). One NOTE
  names the event. Below the threshold the per-symbol near-trade
  refusal and the DECLARED resolution path still apply.
- Unmapped cross-listing journal candidates: an out-leg of X pairing
  with an in-leg of a DIFFERENT symbol Y (same account, equal qty,
  within 7 days) is the fingerprint of a dual-listing journal the
  ticker map doesn't know — mapped pairs were normalized to one
  symbol before netting ran. Surfaced as a `TOBASE X Y` suggestion
  instead of silently feeding the loss walk a disposal + acquisition.
- CDR awareness + the DISTINCT map verb: Canadian Depositary
  Receipts (SAMPLR.TO over SAMPLR.US) name the SAME issuer but are NOT
  listing equivalents — fractional, CAD-hedged, floating ratio. The
  scan detects them from the exchange shortName (the longName is the
  clean issuer name), never suggests mapping one (CDR-PAIR says so;
  unheld CDR twins are silently skipped), and flags a map entry
  pairing a CDR with its underlying as MAP-BAD?. New ticker.map verb
  `DISTINCT a b` records that two look-alike listings are
  deliberately separate securities (a CDR, or same-root different
  companies, e.g. SAMPLO.TO vs SAMPLO.US) — changes no
  symbol, silences the MAP-GAP nag.
- `taxjson scan --online` map robustness: clusters HELD listings by
  exchange-reported issuer name to catch DIFFERENT-root dual listings
  (SAMPLQ.US/SAMPLP.TO — same-root scanning can never see these), and
  verifies every defined GLOBAL/TOBASE/JOURNAL pair names one issuer
  (`MAP-BAD?` on mismatch — a typo'd pair merges two companies' ACB
  pools). Conservative matching; findings say VERIFY.

## v0.12.0 (2026-09-06)

Full audit, round four (2026-09-06): adversarial review of the
round-three fixes, an engine-rigor re-run (800-book fuzz sweep passed;
an extended 5,000-book sweep and a 132-mutant audit found the items
below), and a release-readiness sweep.

Engine (wrong numbers):
- Cross-pool deferral routing (fuzz seed 2183): when a superficial
  loss's still-held backing sat entirely in a SIBLING pool of the
  alias class (loss on S0, substituted property in S2, united by a
  later rename-merge), the s.53(1)(f) ADJUST landed on the trigger's
  own — empty — pool and the denied loss silently vanished from
  conservation. Bumps now land on a pool that actually holds backing
  at +30 (trigger's own pool preferred, greedy otherwise).
- A SPLIT dated exactly on a settle-lagged loss sale's settlement date
  doubled the denial (the ref side of the lineage conversion treated
  the split as already baked in); `lineage_factor` gains a
  `ref_inclusive` knob driven by the loss row's own settle lag.
- A SPLIT dated STRICTLY between a trade's execution and settlement
  silently booked the pre-split-denominated quantity against the
  post-split pool (a $2,000 loss became a $3,000 "gain" with phantom
  shares). New `SplitStraddlesSettlementError` refuses the book
  loudly with re-dating instructions. US engine (trade-date ordering)
  unaffected and pinned so.
- Default fuzz depth raised 25 → 200 books (mutation testing showed
  10 surviving engine mutants the existing invariants kill at 200).

Transfer declarations hardened (round-three follow-through):
- The declaration marker is now an OPT-IN `.tt` token — `TRANSFER ...
  DECLARED` — instead of every hand-written TRANSFER auto-attesting:
  .tt-only books record genuine in-kind moves as TRANSFERs, and a
  json→tt→json round trip must never grant broker rows attestation.
  The emitter round-trips the token; undeclared `.tt` TRANSFER ids
  stop churning across versions.
- A DECLARED leg's blessing now reaches only rows within 7 days of a
  declared leg — a declared June pair no longer silently nets a
  genuine July contribution that gap-chained into the same segment.
  An attestation that doesn't net to zero within its own reach nets
  nothing and says so.
- Marker check is exact equality (a broker description merely
  containing the marker text no longer attests); engine error
  messages and the refusal NOTE prescribe the DECLARED form.

Reporting/CLI:
- `taxjson sum --json` totals now come from the same 2dp row-sum path
  as the printed tables and `subtotals`, so totals == Σ subtotals
  exactly for machine consumers.
- `taxjson-gui --help` prints usage and exits instead of opening a
  window (and works without PySide6).
- README: grouped `sum` tables documented; new section on sheltered
  transfers, `AmbiguousTransferDateError`, and the DECLARED
  declaration/attestation flow.

- `taxjson sum` groups the summary into TAXABLE ACCOUNTS / SHELTERED
  ACCOUNTS / ALL ACCOUNTS tables (each with its own subtotal) when
  taxjson.toml declares both types; accounts missing a type group as
  UNTYPED rather than joining either bucket. Single-type projects and
  single-account views keep the one-table layout. Every table's
  total row is the exact sum of its displayed rows. JSON gains a
  per-account `type` and a `subtotals` object.

Full audit, round three (2026-09-04): four parallel audits (engine
adversarial re-review, pipeline/orchestrator, parsers/importers,
web/GUI/reporting) over the round-two fixes. 28 confirmed findings,
all fixed and pinned; suite at 1921 tests.

Engine (wrong numbers):
- Canada's superficial-loss trigger walk converted acquired-in-window
  and allocation-cap quantities with the CLASS-WIDE alias factor,
  which is wrong when a rename merges INTO a live symbol whose own
  shares never split. `SplitTimeline.lineage_factor` now simulates
  each raw symbol's forward lineage path and converts per-row
  (`_row_loss_units`); falls back to the class factor only when
  lineages don't converge. Fuzzer generates merge-into-live renames;
  three pre-fix-failing goldens pinned.
- Netter bypass trio: the per-account transfer dropper had no span
  cap (gap-chained Feb..Dec segments netted), no main-book
  visibility (same-account pairs inside a loss window silently
  erased), and ran before the cross-account netter could refuse
  chained rrsp→rrsp2→out hops. Segments now cap at 45 days, and the
  sheltered-side invocation refuses zero-net segments near any
  main-book trade of the symbol (trade or settle date, either sign)
  or containing a SPLIT.
- Hand-written `.tt` TRANSFER rows are stamped as manual
  declarations, and a zero-net segment containing a declared leg
  bypasses that near-trade refusal — the declaration is exactly what
  the `AmbiguousTransferDateError` resolution prescribes, so the
  guard must not re-refuse it. A cluster that already nets to zero
  but is refused for nearness prints the attestation form (a
  declared zero-net `.tt` pair) instead of the counter-TRANSFER
  prescription that would unbalance it.
- `taxjson fees` filtered by settle date while every gains window
  runs on trade date; a year-end trade settling in January silently
  fell out of the year. Trade-date basis now (falls back to settle
  only when trade date is absent).

Pipeline/orchestrator:
- The work-cache cleanup glob deleted PREFIX-SIBLING accounts'
  artifacts (`margin` cleanup removing `margin2_manifest.json`);
  scoped by exact-prefix segment match, and `_manifest.json` /
  `_mapped.json` / `_blend.diag` are never cleanup targets.
- Crypto stage order was sorted→filled→mapped, so ticker-map
  GLOBAL/DELETE rules never saw the symbols fill-crypto-prices
  needed; reordered sorted→mapped→filled→base.
- The atomic stage wrapper published its diag capture under a name
  the strict gate never read (`.stage.diag`); now lands as
  `base_json.diag` and the gate fails on it.
- `ccd-sum`/`winners` warn when the resolved gains artifacts don't
  cover the requested period token; leaps balances are per-account
  (cross-account contract counts no longer blend), and partial
  covers leave the opening remainder in place.

Parsers/importers:
- Coinbase Converts booked the buy leg at subtotal, dropping the fee
  from basis; now subtotal + fee (matches the Kraken convention).
- Generic importer reads `(x)` accounting negatives.
- Kraken orphan spend/receive ledger legs and bare `trade` rows warn
  and count as skips instead of vanishing.
- RBC NON-RES TAX rows derive qty/rate from the GROSS figure instead
  of the withheld net.

Web/GUI/reporting:
- What-if labeled multi-lot sells by the FIRST lot only: wash flag is
  now any-lot, term is `MIXED (LONG_TERM x, SHORT_TERM y)` when lots
  straddle.
- Wash-radar serves the COMBINED cross-account report; stale-window
  advisories say when the window has cleared since generation.
- GUI estimate renders the AMT block (top-up + TOTAL WITH AMT) it
  previously dropped.
- Holdings pages state their basis is the native per-account
  snapshot, not the s.47 filing ACB.

- `taxjson audit` output redesigned for legibility: a fixed label
  gutter (SOURCE / MAPPING / FX / DISPOSITION / WASH / TIE-OUT /
  TRACE) with dim/bold weighting and tty-aware color (auto-on for
  terminals, `--no-color` and NO_COLOR honored); parsed rows read as
  verbs (`SELL 10 ABC.US @ 25.00`) instead of raw
  `BUYSELL -10`; cross-checks are \u2713/\u2717 marks instead of
  `== ties`; figures right-aligned; the redundant raw-gain line
  dropped on non-wash events; prose (permanent-denial explanations,
  warnings) wraps inside the 86-column frame; the reconciliation
  footer aligns and carries per-line marks; proceeds and cost basis carry the per-share figure (`(10 sh @ 25.0000)`) for statement sanity-checks. JSON output unchanged.

Full audit, round two (2026-09-04): four parallel audits over the
layers never previously deep-audited — reporting/filing, pipeline
orchestration, cross-tool contracts — plus an adversarial review of
the v0.11.0 engine changes. 32 confirmed findings, all fixed and
pinned.

Engine (wrong numbers):
- Canada's four wash balance walks scaled the ENTIRE alias class by a
  rename-split's ratio, fabricating still-held balance (denied losses
  on fully-exited positions; conservation broken). All four walks now
  track per-raw-symbol sub-balances, scaling and folding only the
  symbol a SPLIT names — the US engine's model. The fuzzer now
  GENERATES rename-splits, which immediately caught:
- US engine: the rename-migration FIFO re-sort keyed on the
  §1223-tacked `effective_acq_date` — but tacking adjusts holding
  period only; Reg. 1.1012-1(c) FIFO goes by actual acquisition
  order. Wash and no-wash runs consumed different shares after a
  rename-split. Sorts by actual date now; conservation pinned.
- The rename-merge dropped parked `pending_wash` dollars (denied
  loss's future recovery silently erased); they now ride the rename,
  signed by the target pool's direction.
- Cross-account sheltered netting refuses segments containing a SPLIT
  or sitting within 30 days of a taxable sale of the symbol — a
  contribution + withdrawal pair is byte-identical to a custody move,
  and near a loss window the engine's AmbiguousTransferDateError now
  forces the declaration instead of silently allowing the loss.
- AmbiguousTransferDateError is handled cleanly by every consumer
  (explain, audit, carryover, the web what-if) instead of only
  taxjson-gains.

Reporting/filing (wrong numbers):
- work/<acct>_report.json income was ALWAYS zero (field-name
  mismatch); it now summarizes the base book like the .sum does.
- reconcile-slips mis-rendered SHORT dispositions (missing the
  proceeds/cost swap form-export performs) — false MISMATCH equal to
  the gain; and its tainted-row plumbing was dead (the pipeline moves
  tainted rows to manual_reporting_required) — phantom-basis sales
  now get the designed "tainted" note instead of
  MISSING_FROM_COMPUTED.
- check-filed recomputed with the wrong phantoms.json path
  (work/ instead of the project root): guaranteed false DRIFT on
  phantom projects, inviting an erroneous close-year --force.
- carryover now computes on the FILING basis: US per-account FIFO
  lots, and US crypto in a separate no-wash pass (the ledger used to
  deny a loss the return legitimately claims).
- Schedule 3's note distinguishes permanently denied superficial
  losses (registered-account acquisition — NO ACB addition) from
  deferred ones; the old single note told users to bump ACB for both.
- resolve_gains_files warns loudly when the preferred wash file is
  older than the plain gains file (run --account staleness reaching
  form-export/t1135/reconcile-slips silently).

Pipeline orchestration:
- The corp-actions stage joins the sources-manifest dependency —
  deleting a CSV under --fast no longer leaves its corp rows in the
  books.
- merge2 + apply-distributions publish ATOMICALLY: a failed apply can
  no longer leave a fresh-but-unadjusted base that --fast trusts
  forever (silently vanished ROC ADJUSTs).
- Renamed/removed accounts: `taxjson run` warns that orphaned work/
  artifacts are still counted (a renamed account was silently counted
  twice by sum/fees); parsed artifacts of REMOVED broker groups are
  cleaned up (stale .diag banners, dead fees, dead audit sources).
- ticker.map GLOBAL/DELETE rules now apply to crypto accounts (a new
  mapped stage; the README's "every stage" contract held everywhere
  but there). taxjson-ticker-map grows --map/--global-only.
- The blended pass's diagnostics (the only pass that sees
  --sheltered) are mirrored into each account's .sum banner instead
  of dying unread in a dot-file.
- taxjson audit no longer crashes (NameError) on projects with two or
  more taxable equity accounts; uppercase `country = "CA"` no longer
  crashes the blended pass mid-run (normalized at all stage sites).
- A failed pipeline stage exits with a one-line message pointing at
  the child's error instead of a stacked CalledProcessError traceback.

Contracts/docs:
- The README's return-of-capital `.tt` example was in the WRONG FIELD
  ORDER and crashed the parser (with the roc-sum footer pointing
  straight at it); corrected, ADJUST's five-field shape documented,
  and malformed .tt rows now produce a clean one-line error.
- RBC exports are no longer mis-detected as Webull ("Account Number"
  boilerplate matched Webull's marker; the repo's own demo produced
  silently empty books) — pinned end-to-end on both demo files.
- .tt `time` documented as REQUIRED (omitting it misparses); the
  radar's category list gains the missing EXITABLE and CAUTION;
  sector.map removed from the file table; the [estimate] block added
  to the schema docs and the init scaffold; chained `--json` output
  caveat documented; GUI Tax tab documented.

## v0.11.0 — 2026-09-03

The deep audit and the engine-correctness campaign: 31 confirmed audit
findings fixed, three further engine bugs found by conservation
fuzzing, and a standing rigour suite (invariants, differential,
mutation harness). 1,883 tests.

Engine rigour: property-based invariant testing, and the two bugs it
found on its first runs.

- Fuzz generator widened: corporate SPLITs mid-history, same-timestamp
  collisions, sheltered round trips (buys AND sells), non-flat ending
  positions; the conservation identity generalized to
  realized − parked deferrals − permanent == no-wash baseline, so
  open-ended books are covered too. 2,000 books per engine clean.
- NEW: blend-vs-split differential law — the split of the blended
  taxable pass may create or destroy nothing: disposition counts,
  gain totals, and per-symbol inventory must all conserve across
  `taxjson-split-gains` artifacts vs the blended document. Fuzzed
  alongside the engine invariants.
- NEW: targeted mutation testing over the wash-relevant regions of
  core.py and pipeline.py (`scripts/mutation_audit.py` +
  `mutation_triage.py`, committed for reruns): 298 mutants across
  four rounds. The gaps it exposed are now pinned by goldens — the
  engine's own ±30-day boundary on BOTH edges (rebuy day 30 denies /
  day 31 allows, exit day 30 rescues / day 31 too late, including
  the window-widening mutant that hides behind the still-held
  bound), the still-held direction gate (long loss + net-short
  balance), cross-symbol trigger leakage, same-day post-loss
  allocation priority, and crossing-sale self-triggering. Residue
  census: 118 candidates dominated by option-premium paths,
  split-rename walks and inventory-merge orderings (tracked as the
  next kill-set targets), 72 epsilon-boundary equivalents, plus
  known-equivalent creation-sign mutants. Engine branch coverage
  under the kill set: 77%.

- **NEW: conservation fuzzer** (`tests/test_engine_invariants.py`) —
  seeded-random fully-liquidated books checked against LAWS of the
  domain rather than authored examples: realized + permanent denials
  must equal the no-wash baseline exactly (every deferral recovered);
  disallowances only ever on losses; input order must not matter;
  signs never negative; the solver converges. 25 books per engine in
  the suite; `TAXJSON_FUZZ_BOOKS=2000` for the extended run (clean).
  Plus CRA golden cases (full denial, the least-of-three partial
  formula, no-acquisition-no-denial, full-group-exit rescue) encoded
  from the published guidance as an oracle.
- Fuzzer find #1: **a deferral is only real to the extent TAXABLE
  still-held shares back it at the window's end.** Allocating purely
  by trigger let a taxable trigger whose own shares were gone by +30
  collect a deferral that parked on an empty pool and never recovered
  — denied-but-sheltered-backed portions are PERMANENT (s.53(1)(f)
  bumps the basis of property still owned; a bump inside a registered
  account is moot). This shape is now accounted
  correctly: permanent, not deferred-and-stranded.
- Fuzzer find #2: **a deferral landing on a FLAT pool now parks
  direction-agnostically and takes its sign from the NEXT opening.**
  The creation-sign fallback leaked a short-signed deferral into a
  subsequent long opening with inverted effect — the denial
  double-counted instead of recovering.
- The AmbiguousTransferDateError message now carries the exact
  counter-TRANSFER .tt recipe for the custody-move resolution.

Deep audit (2026-09): four parallel audits over the engines, transfer
handling, broker acquisition, and the decision commands produced 31
confirmed findings, all fixed and pinned by regression tests. The ones
that changed NUMBERS or ADVICE:

- **Engine (Canada):** a short-side superficial-loss deferral landing
  on a net-LONG symbol-global pool applied with an inverted sign —
  REDUCING long ACB instead of deferring — and a disallowance from an
  earlier solver iteration was never retracted when basis adjustments
  turned that disposition into a raw gain (a denied "loss" reported on
  a gain). Deferral ADJUSTs now key their sign off the pool's actual
  direction at the landing site, and stale DISALLOWs retract on
  re-iteration. Conservation (realized + deferred = no-wash baseline)
  is pinned by test.
- **Sheltered in-kind contributions now trigger superficial-loss
  detection.** Parsers emit TRANSFER for an in-kind contribution into
  an RRSP/TFSA; the wash context stripped ALL transfers, so the
  canonical permanently-denied superficial loss was silently claimed.
  Custody noise still nets out (same-account pair drop + a new
  registered-to-registered cross-account netting); what survives is
  rewritten to a sheltered acquisition the wash walk can see.
- **Transfer pair-cancellation is time-clustered** (35-day segments):
  an in-kind contribution OUT of a taxable account (a CRA deemed
  disposition) months before an unrelated transfer IN no longer
  cancels silently past the taxable hard-error; a SPLIT between the
  legs now blocks the drop (share terms changed).
- **Wash radar:** custody-move transfers are netted out of its input
  and never count as acquisitions (a pure broker move no longer
  fabricates a VIOLATION demanding liquidation); a sale crossing
  long->short prorates proceeds to the closed portion (a real loss no
  longer computed as a gain -> CLEAR); the flip-side opening is
  recorded; zero-book-value transfer-ins carry average cost instead
  of diluting ACB to zero.
- **buy-check / sell-check:** a VIOLATION leg poisons the whole
  class's dates (no more too-early "safe to buy from" off a sibling
  BLOCKED leg); sell-check exports the binding rescue deadline as
  `act_by` (EARLIEST across the class) separate from the
  safe-to-sell `clears_at` (latest); the sheltered-triggered
  VIOLATION message now states the actual still-held condition
  instead of contradicting its own rescue advisory; OCC option
  symbols fold to their underlying's class (an option is a right to
  acquire identical property).
- **harvest:** VIOLATION/EXITABLE/CAUTION losses bucket as claimable
  NOW (full exit) — their dates are deadlines, not availability;
  the old schedule planned harvests for after the point of no return.
- **watch:** same-category advisory changes (a violation's required
  sell quantity doubling) are reported; the "(a new buy extended the
  window)" explanation only prints when the date actually moved later
  on a window category.
- **estimate:** `[estimate]` config values pass the same
  non-negative/finite guard as the CLI flags (a negative
  `other_losses` fabricated taxable gains, feeding instalments too).
- **instalments:** "Behind by" counts only instalments DUE so far; an
  on-schedule mid-year taxpayer sees "On schedule so far" with the
  year's remaining total.
- **IBKR parser:** Flex files carrying multiple detail levels
  (Order + Trade + ClosedLot per fill) emit each trade once;
  cancelled-trade pairs (code `Ca`) net to exactly zero instead of
  booking a phantom 2x-commission round trip; a cancel/rebook
  RESTATED corporate action consumes its original spinoff rows
  instead of double-counting income and shares.
- **Questrade parser:** TSX-Venture `.VN` normalizes to `.V`
  (was the junk `ABC.VN.TO`, fragmenting identity vs live holdings);
  a transfer-in keeping an internal symbol code (`X000002`) warns.
- **fetch:** `--trim-overlap` is bounded at BOTH window ends — it
  could delete sibling rows dated after the window's end, i.e. real
  trades the fetched file does not own; likely broker restatements
  (an existing row matching a fetched row on date/symbol/type but
  differing elsewhere) are named at fetch time; the rotated Questrade
  token is written 0600 from the first byte; live-holdings option
  suffixes also learn Canadian roots from the account's own books
  (cash-secured puts no longer phantom-mismatch in verify).
- **taxjson audit:** the tie-out now fails on OMISSIONS and
  FABRICATIONS (a saved gains file missing a whole disposition — or
  carrying an invented one — passed with exit 0), and ties the
  in-scope totals; payment-in-lieu records are excluded on both
  sides; blended split apportioning dedupes per-broker duplicate
  SPLIT rows (Σ per-account = blended again).
- Also: the engine no longer crashes on a loss row with `time=''`.
- **Radar EXITABLE with a standing sheltered holding no longer says
  "full exit is fine".** CRA's denial is min(sold, acquired-in-window,
  held-at-+30) and old sheltered shares keep the still-held term
  alive — a full TAXABLE exit still leaves up to the sheltered
  balance denied permanently. The advisory now says so with the
  quantity (the encoded heuristic did not hold for an in-window buy).
- **Ambiguous transfer dates refuse to guess.** A sheltered
  TRANSFER-in whose ARRIVAL date lands inside a superficial-loss /
  wash-sale trigger window now raises a hard error naming both
  resolutions (import the prior broker's history so the pair nets, or
  record the true acquisition as a BUYSELL) — a custody-move arrival
  is not an acquisition, and silently treating it as one could
  wrongly deny a loss just as the old silent strip wrongly allowed
  one. Outside trigger windows the rewritten row counts only toward
  the date-insensitive still-held balance, which is always factual.

Real-book impact: one disposition's denial changes (a superficial
loss whose still-held test now sees transfer-contributed sheltered
shares) — re-run `taxjson run` to refresh saved books after
upgrading; `taxjson audit` names any disposition whose figure moved.

## v0.10.0 — 2026-08-29

The pre-production compat purge. 1,840 tests.

- Pre-production compat purge: every development-era spelling that was
  kept as a hidden alias or no-op is REMOVED — the code has no
  deployed users to keep working, so the old names now fail loudly
  instead of being silently accepted. Gone: `run --force` (full
  rebuild IS the default; `--fast` opts into the cache),
  `sum --estimate` (`taxjson estimate` is the one front door; sum
  still renders the estimate block when income inputs are supplied —
  the GUI's what-if path), the hidden `--account-name` aliases on
  `taxjson-brokerage` and `taxjson-wash-radar` (renamed to
  `--account` in 2026-07), the `taxjson-fees` console-script alias of
  `taxjson-fees-sum`, the hidden `fees-sum --year` alias of the
  PERIOD positional, and the fold-in migration for the pre-naming
  `inputs/<acct>/questrade_api.csv` fetch file. `taxjson-fees-sum
  --since` sheds its deprecation costume — it is the PERIOD wrapper's
  cutoff channel, now a documented flag. The now-unused
  `deprecated_alias` argparse helper is deleted. Kept deliberately:
  the `.tt` plain-split lint tolerance and parser-format tolerances —
  those cover DATA files users author, not UI churn.

## v0.9.0 — 2026-08-29

The authoritative audit command, a machine-wide Questrade token home,
and the removal of the portfolio-analytics commands. 1,841 tests.

- REMOVED: the portfolio-analytics commands `value`, `timeline`,
  `yield` and `sold-perf`, and the never-exposed standalone tools
  `taxjson-beta`, `taxjson-sharpe`, `taxjson-atr`, `taxjson-hv` and
  `taxjson-leaps-missed` (~1,900 lines plus their tests). None fed
  any tax number — they were portfolio-tracker features living in a
  tax toolkit, priced over the network (the brittle dependency
  class), and a portfolio tracker is their proper home. `divs-sum` keeps the
  tax-relevant half of `yield` (dividends actually received);
  `harvest` keeps the pricing chain (and the `[ibkr]` extra) for the
  one decision that needs live prices. The `[analytics]` extra, the
  `sectors_file` setting, and the desktop app's Value tab (a chart
  over `taxjson value`) are gone with them.
- Questrade token: `~/.questrade_token` is now THE token home
  (`$QUESTRADE_TOKEN_FILE` overrides) instead of the per-project
  `work/.questrade_refresh_token`. Questrade runs one rotating chain
  per API app, so the credential belongs to the machine, not to a
  project — two projects (or taxjson next to another Questrade tool) each caching
  their own copy meant whichever ran last held the live token and the
  other failed to authenticate. The resolver is exactly two steps
  ($QUESTRADE_TOKEN_FILE > ~/.questrade_token, written mode 600); a
  leftover legacy work file is ignored entirely — never read or
  written — and can be deleted. First run on a machine without the
  shared file: pass `--refresh-token` once (or set
  $QUESTRADE_REFRESH_TOKEN).
- **`taxjson audit [SYMBOL ...]`** — the authoritative justification
  of every capital-gain figure. One block per taxable disposition:
  the parsed broker row it came from (nominal currency, original
  ticker, source file — joined by the content-hash transaction id),
  the ticker.map rule that renamed it, the exact FX rate the pipeline
  applied (same file, same date-resolution rules, provenance named:
  exact date / carried forward / default) with nominal x rate
  recomputed against the base books to the cent, the engine's
  disposition math from a trace-enabled re-run of the same blended
  computation the pipeline runs (ITA s.47 cross-account ACB / US
  cross-account §1091), the superficial-loss / wash-sale
  determination with each replacement lot resolved to its row and
  the denied loss followed to where it went, a to-the-cent tie-out
  against the pipeline's saved gains files, and the full ACB/FIFO
  pool trace. A RECONCILIATION footer counts every cross-check;
  exit 1 when any disagrees — the audit's claim is precisely that
  these numbers agree, so a mismatch is a finding. `--summary`,
  `--id`/`--date`/`--account`/`--year`/`--all-years` filters,
  `--no-trace`, `--json`; the standalone `taxjson-audit` takes
  explicit paths for use outside a project.

## v0.8.0 — 2026-08-27

Deep audit of the instalments, estimate/AMT and
wash-check features, with the advice-changing fixes
called out below. 1,903 tests.

- Audit pass over the new commands, with the fixes that changed
  ADVICE rather than formatting:
  - `buy-check` no longer prints a VIOLATION's `clears_at` as a
    "safe to buy from" date. That date is the SELL-BY deadline for
    rescuing the loss — quoting it as a re-entry date invited the
    rebuy roughly a month early, permanently killing the loss the
    command exists to protect. Violations now say to wait 31 days
    past the latest in-window loss sale.
  - Across a class of cross-listings both checks take the WORST
    (latest) clearing date, not whichever ticker sorted first.
  - `sell-check` stops telling a sheltered-ONLY holding to "sell at
    a loss" — a registered disposition has no tax effect at all.
  - A VIOLATION on a name a registered account also holds is UNSAFE,
    not the rescueable ACTION: the registered-matched portion is
    permanently denied and selling cannot recover it. The radar's
    taxable/sheltered quantity split is carried through to the
    checks so they can tell these cases apart.
  - The last-loss sanity line dates the sale by SETTLEMENT, the same
    basis the radar's ±30-day windows use; a trade-date age
    contradicted the verdict for anything sold 31-32 days ago.
  - `verify` surfaces a configuration failure as its own message and
    count instead of reporting it as a broker mismatch, and says why
    it needs Questrade (Flex statements carry no live-position feed).
  - `--tolerance 0` means exact; falsy coercion had restored the
    1e-4 default.
  - Live Questrade positions: a trailing CLASS letter is not an
    exchange — `SAMPLC.B` is `SAMPLC.B.US`, and passing it through
    unsuffixed made every class-share position look like a phantom
    mismatch in the verify diff.
  - Rows with a blank category are no longer captioned `(CLEAR)`.
- `estimate` and `instalments` read the same `[estimate]` config, so
  the current-year instalment basis cannot silently differ from the
  estimate it claims to follow.
- AMT: `other_losses` is capped at the realized gain before the 50%
  disallowance, so a loss pool larger than the year's gains stopped
  driving adjusted taxable income negative.
- The derived-dividend-rate snap now requires the shortened rate to
  be UNAMBIGUOUS — if a neighbouring value at the same precision
  also explains the cash, the raw quotient stands. Where the cash
  genuinely cannot resolve the rate (a small payment that fits both a
  three- and a four-decimal rate) the shorter form is kept and claims
  no precision the data cannot back.
- `fx-cash` labels its report ESTIMATE ONLY — it is reconstructed
  from broker cash flows, which do not carry conversions or
  deposits.
- README: documents `--json` on both checks, `verify --tolerance`
  and its Questrade-only constraint, `fetch`'s window and credential
  flags, and `estimate --province` / the `[estimate]` block. The
  Status section now says plainly what the test suite does and does
  not assure.

- **`taxjson instalments`** — Canadian tax instalments from an
  `[instalments]` config: the four due dates (weekend-rolled) under
  the current-year, prior-year, or CRA-reminder basis; what each
  calls for vs what was paid; the ITA 161(2) **offset interest**
  (daily-compounded, charge netted against credit); and the ITA
  163.1 **penalty** (half the excess over the greater of $1,000 and
  25% of the no-payment interest). The current-year basis is driven
  by `taxjson estimate` itself — net tax owing = total tax + any AMT
  top-up − amounts withheld — so the two commands cannot disagree.
  `estimate` gains a compact instalment block when configured, and
  `--json` carries the schedule and interest on both. The prescribed
  rate accepts a dated schedule (`prescribed_rates`) as well as a
  scalar — CRA resets it quarterly and charges each day at the rate
  then in force, so the daily walk looks the rate up per day and the
  report names every rate it used. Interest is assessed on the LEAST
  of the methods the configured figures support (ITA 161(4.01)), as
  CRA does — following any one basis correctly is interest-free — and
  the report names the governing basis. Payments accept an optional
  `note` and are listed. CRA's full two-limb requirement test is
  applied (net tax owing over $3,000 in the current year AND in
  either of the two preceding years), so configured prior-year
  figures at or below the threshold report "no instalments required"
  — naming the failing limb and warning that a placeholder zero
  reads as "I owed nothing" and suppresses both the obligation and
  all interest. Per-date status reads PAID / LATE / MISSED /
  UPCOMING — "SHORT" conflated a date covered late (interest ran, but
  nothing is outstanding) with one still owing.
- `taxjson init` scaffolds the current feature set: `province` (which
  `estimate` requires for Canada), `sectors_file`, `fx_cash_gains`,
  the per-account fetch keys, and a commented `[instalments]` block
  on Canadian projects. All comments — a fresh project parses to
  exactly the same live config as before.

- Estimate: the AMT block is rendered in the house style — the
  report's 30/14 column widths, prose wrapped at 78 columns, no
  over-wide header, and the assumptions footer separated and wrapped.
  The rule summary now rides in a note printed in both the binding
  and non-binding cases.
- Dividends: a DERIVED per-share rate (broker states only the cash
  and the share count) snaps to the fewest decimals that still
  explain the paid amount to the cent. Back-computing manufactured
  spurious precision — an eight-decimal rate shown for a dividend
  declared at three decimals, disagreeing with the same payment in
  another account whose statement states the rate. Genuinely
  fine-grained rates (0.3728) survive; stated rates are untouched;
  cash amounts never move.

## v0.7.0 — 2026-08-26

The Canada AMT check. 1,827 tests.

- Canada AMT check in `taxjson estimate` (post-2024 rules: capital
  gains at 100% inclusion, dividends un-grossed with no DTC, credits
  at 50%, 20.5% over the bracket-pinned exemption, ON/BC/AB
  piggyback). Rendered always — the top-up and 7-year carryforward
  when it binds, the headroom when it doesn't; `estimate.amt` +
  `estimated_tax_with_amt` in JSON. US projects: investment income
  alone rarely triggers US AMT (LTCG keep preferential rates inside
  it) — documented as out of scope, not faked.

## v0.6.0 — 2026-08-26

Three new commands rounding out the decision loop: what will this
year cost me (`estimate`), and is this specific trade wash-safe in
either direction (`buy-check` / `sell-check`). 1,822 tests.

- **`taxjson sell-check SYMBOL ...`** — the sell-side twin: UNSAFE
  when a recent affiliated buy would deny the loss (LOCKED), ACTION
  when a rescueable violation is open, SAFE*/SAFE with caveats. Both
  checks share one symbol-class engine (known-exchange roots +
  ticker.map equivalences) and the last-loss sanity line; both note
  when sheltered context is missing.
- **`taxjson buy-check SYMBOL ...`** — buy-side wash check on the
  combined radar: UNSAFE (with the safe-from date) when a loss was
  sold in the past 30 days, SAFE* when buying merely extends an open
  wash window; root-matched across listings; exit 1 on unsafe.

- **`taxjson estimate`** — the tax estimate as a first-class command:
  the realized-gains summary table followed by the estimate block
  (same code path as `sum --estimate`, which keeps working). The FTC's
  actual-withholding read now comes from the BASE books' TAX rows,
  year-scoped — the gains files never carried them, so the 15%
  assumption always silently won before.

## v0.5.0 — 2026-08-25

Live broker verification, plus advisory and corp-action correctness
fixes proven on real data. 1,807 tests.

- **`taxjson verify`** — fetch LIVE Questrade holdings (positions
  endpoint) and cross-check them against the computed books through
  the sanity machinery; exit 1 on mismatch. `fetch --positions`
  writes the same `work/<account>_live_holdings.toml` snapshots.
  Option symbols convert to OCC (Montréal-listed roots get .TO);
  `.VN` maps to `.V`; bare symbols are US listings. The complete
  integrity loop is `taxjson fetch run verify` — proven live: it
  flagged 4 real stale-book discrepancies, pulled the missing rows,
  and reconciled both accounts to the broker exactly.
- **RISK reinterpreted**: s.40(2)(g) needs an acquisition INSIDE the
  ±30-day window, so a sheltered-held position with no buys in the
  past 30 days is sellable at a loss NOW — RISK losses count in
  harvest's claimable-now bucket with a forward-window caveat (pause
  DRIPs/sheltered adds for 30 days after selling; an affiliated buy
  makes the denial permanent). Previously bucketed as unharvestable.
- **Questrade spinoff chains**: the placeholder/reversal/delivery
  triple (a warrant distribution) is grouped by its REC/PAY +
  ON-N-SHS chain identity — no more empty-symbol events (which
  emitted invalid book rows) or skipped net-zero chains; targets are
  currency-suffixed like trade rows; unresolvable chains skip
  loudly; a failed sheltered-book validation exits cleanly instead
  of a traceback.
- Harvest: currency labels stacked under the money column headers
  (no column widening); money/qty columns right-aligned.
- Every subcommand's `-h` shows a synopsis, and help pages wrap at
  78 columns (the top-level command blob is a COMMAND metavar).

## v0.4.0 — 2026-08-21

The fx-cash feature, the fetch/watch automation polish, and the
2026-08-21 high-effort audit (four parallel review passes over the
whole tree; every confirmed finding fixed and pinned). 1,798 tests.

- 2026-08-21 audit fixes — engine: SPLIT-rename schedule migration is
  input-order-independent (§1091 unit conversion); blended split nets
  fee rebates and warns when phantom shares can't be attributed;
  transfer-drop guard is account-scoped and counts ASSIGN; NaN prices
  can no longer fossilize in the price cache or reach --json output.
- 2026-08-21 audit fixes — flow: a post-conversion currency INVARIANT
  fails validation on residual native rows; the Canada estimate's FTC
  comes from actual TAX rows (capped at the treaty ceiling); holdings
  export stops summing cross-currency costs (mixed_currency +
  per-currency components); leaps-missed totals are per-currency;
  t1135 honors zero-ratio renames; deterministic ADJUST ordering.
- CLI: chains validate fully before executing anything (no more
  half-run on a bad tail segment) and ambiguous boundaries print a
  note; errors name the executing command; `elect --json` emits the
  saved elections; `sum` takes an [account]; wash-radar rejects
  sheltered accounts and notes missing sheltered context; empty-state
  JSON documents keep their full key schema; ibkr_flex refuses
  multi-account downloads; GUI panes name their currency.
- Fetch hardening: --trim-overlap backups never clobber; the trim
  date test parses real dates; prior years' own fetch files no longer
  false-alarm the overlap warning; Flex network errors are clean;
  --dry-run touches nothing and reports would-be overlaps; fetched
  CSV writes are atomic.
- Fetch: `--year N` backfills a past tax year into `questrade_N.csv`
  (window capped at Jan 15 of N+1); `--json` machine summary with
  per-activity-type counts; the text output gains the same type
  summary. Watch: `--state PATH` lets multiple cron cadences keep
  independent baselines.

- **`taxjson fx-cash`** — FX capital gains on foreign-currency cash
  (ITA s.39(1.1) with the $200 de minimis; §988 ordinary-income
  figure for US projects), reconstructed as a per-currency ACB cash
  ledger from the taxable accounts' native books. Standalone report;
  opt-in end-of-run summary via `fx_cash_gains = true` — no other
  number changes either way.

- Fetch config lives on the account: `brokerage` +
  `account`/`query_id` under `[accounts.<name>]` (replaces the
  short-lived `[fetch.<name>]` tables from v0.3.0).
- Questrade fetch always covers the full tax-year window (from Dec 15
  of the prior year, so year-boundary trades that settle in January
  are never missed) and surfaces the API's real error messages.
- Questrade fetch writes tax-year-stamped `questrade_<year>.csv` (a
  legacy `questrade_api.csv` is folded in and removed after a
  successful merge).
- Fetched Questrade rows now reconcile with manual exports
  row-for-row: Transaction Date maps from `tradeDate` (the API's
  `transactionDate` is the posting date), description whitespace is
  collapsed, and chunk-boundary duplicate activities are dropped.
  Manual CSVs overlapping the fetched window are flagged (the two
  sources round price/gross differently, so duplicates never dedup);
  `--trim-overlap` trims them with a `.bak` backup.

## v0.3.0 — 2026-08-20

Two new automation commands plus the 2026-08 flow-consistency audit
(four parallel review passes; every fix verified by an adversarial
agent pass and pinned by a regression test). 1,746 tests.

- **`taxjson watch`** — cron-able change detector: reports only what
  changed since the last run (new/changed/cleared radar advisories,
  moved clear dates; `--harvest` adds the harvestable-now total).
  Silent with exit 0 when nothing changed.
- **`taxjson fetch`** — broker auto-fetch: Questrade REST API and
  IBKR Flex Web Service, per-account `[fetch.<account>]` config,
  writing the same file formats the parsers already read.
- 2026-08 flow audit fixes: `value` no longer double-converts
  ACB-sourced price.map series; the filed-year check recomputes US
  crypto with `--no-wash` (no more false drift); `yield` DIV/SH nets
  same-day reversals and no longer mixes currencies; a combined
  cross-account wash-radar sidecar (`wash_radar_COMBINED`) feeds
  harvest's ADVISORY; fee rebates net into `.sum` FEES; `close-year`
  hard-stops on stale wash artifacts; basis labels everywhere follow
  the actual resolved files; `--fast` notices deleted map files;
  RBC TAX rows keep the traded market's suffix.
- `taxjson list --negative` — show only negative-quantity positions
  (real shorts, or missed corporate actions / import gaps in accounts
  that can't short).
- Questrade: STK DIV rows (stock dividends paid in shares) now enter
  the book as in-kind deliveries instead of being silently dropped;
  a stderr NOTE points taxable accounts at `distributions.map` for the
  declared amount.
- Coinbase: "Incentives Rewards Payout" rows recognized as reward
  income (FMV income event + acquisition), like the staking family.
- IB: split ratios are snapped to the broker's own Corporate Actions
  leg quantities (IB books fractional results to 4 dp), eliminating
  post-split phantom dust.
- Documentation overhaul: repaired README subcommands table, new
  "Project layout and configuration" (full taxjson.toml schema, every
  project file) and generic-importer sections, EXIT@ explained,
  KNOWN_ISSUES brought current.
- Internal simplification: dead code removed; quantity formatters,
  UTC-noon date helpers, soft config reads, comment-strip JSON
  loaders, and tomllib fallbacks each consolidated to one home.

## v0.2.0 — 2026-08-17

First tagged release. Everything below landed since the 0.1.0 scaffold;
the cycle included three full audit → fix → adversarial-verification
rounds (August 2026) on top of the 2026-06/07 audit series.

### Engines & correctness

- **Blended multi-account taxable pass** — the canonical wash-adjusted
  numbers are computed by one combined run over all taxable equity
  accounts: Canada ACB blends across non-registered accounts (ITA
  s.47); US §1091 wash sales match across accounts while FIFO basis
  stays per account (`taxjson-gains --per-account-basis`,
  `taxjson-split-gains`). Per-account `<name>.sum` remains the isolated
  pre-blend baseline for comparison.
- Dozens of engine fixes from the 2026-07 FUZZ/REVIEW series and the
  2026-08 deep audits, including: wash-sale unit conversion across
  splits and renames, §1223(3) holding-period tacking, retained-share
  replacements, sheltered-account partitioning, assignment-premium
  attribution (per account, marked-leg scoped, time-scoped),
  cash-settled index-option assignments, ROC ADJUST handling, and
  share-conservation post-conditions in both engines.
- US crypto exempted from §1091 (property, not securities); Canadian
  crypto stays superficial-loss-checked, including in the wash radar.

### Filing outputs

- IRS **Form 8949** (code-W wash adjustments, Schedule D totals, real
  per-lot acquisition dates, correct short-sale columns) and CRA
  **Schedule 3** (line 13199/13200, superficial-loss notes).
- **TXF export** (`form-export --form txf [--box A|B|C] --out f.txf`)
  for TurboTax import, built on the 8949 model.
- **T1135** screening and per-property/per-country tables; broker slip
  reconciliation (`reconcile-slips`, trade/settle date-basis aware);
  capital-loss **carryover ledger** (Canada + US worksheets).
- **Filed-year lock**: `close-year` snapshots a filed year;
  `check-filed` (and every full run) recomputes it from the current
  books — blended-aware — and reports drift; fatal under `--strict`.
- **`distributions.map`**: reinvested (phantom) capital-gains
  distributions and late-published ROC factors become ACB adjustments
  automatically.

### Importers

- Interactive Brokers, Questrade, RBC Direct Investing, Webull,
  Kraken, Coinbase — with sign-preserving income conventions, loud
  refusal of unrecognized layouts, split-fill dedup protection, and
  zero-drop skip accounting throughout.
- **Generic column-mapped importer**: any other broker via
  `generic_*.csv` plus a TOML mapping (template:
  `examples/generic_wealthsimple.toml`); strict numerics, ambiguity
  refusals, mapping files participate in cache invalidation.
- Crypto: Coinbase Convert and Kraken crypto-to-crypto two-leg
  emission, legacy pair formats, fee-inclusive instant-trade totals,
  FMV backfill with correct currency stamping.

### Workflow & CLI

- **Chained subcommands**: `taxjson run sum`, `taxjson run --fast sum
  --json` — trial-parsed boundaries, per-command flags, fail-fast exit
  codes.
- `taxjson run` — single-command pipeline with in-process stage
  dispatch, `--fast` incremental cache (deletion-aware), `--strict`
  validation mode, headless corp-action elections (exit 3 +
  `taxjson elect`), and a filed-year drift check at the end of every
  full run.
- Advisory tools: wash radar (correct rescue deadlines), harvest
  (recovery schedule, `EXIT@` FX-aware break-even exit price with 2%
  buffer), div-yield, value, sold-perf, fees, t1135, sanity,
  carryover, `--version`.

### Apps & platform

- Local web UI (FastAPI, localhost-only, what-if sell engine) and a
  PySide6 desktop GUI (background pipeline runs, elections dialog).
- CI: ruff critical tier, 3.9–3.13 × ubuntu/macos matrix, GUI
  offscreen cell, all-extras cell. 1,660+ tests.

### Known limitations

See `KNOWN_ISSUES.md` — notably: RBC withholding gross-up assumes the
15% US treaty rate, Questrade emits no standalone INTEREST/TAX rows,
Webull option expiry/assignment needs a sample CSV, §1256 (60/40
mark-to-market) is not implemented, and the tax estimate does not
classify eligible/qualified dividends.
