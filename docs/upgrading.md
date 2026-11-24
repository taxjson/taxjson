# Upgrading

This page says how to upgrade, what to do after, and every change that
asks something of you, release by release. Before 1.0.0 a release could
change a file's format, a command or a project's layout; what a release
keeps from 1.0.0 on is in [What 1.0.0 keeps](#what-100-keeps). The full
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
2. `tjs run`. A command that stops with "run `tjs migrate`" means an
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

### v2.0.0

#### `tjs run` and `tjs sanity`

- **`tjs run` no longer compares positions with the broker's holdings files: `tjs sanity` is a
  step of its own** (TJ-5170). A full run on a project with holdings files (a snapshot in the
  year's `holdings/`, an account's `holdings = [...]`) no longer ends with
  `==> Checking positions against the broker's holdings files` and its messages ("positions
  match …", "positions differ …", "holdings check could not run", "holdings check incomplete",
  the `tjs sanity: note:` lines). Its closing list says
  `Info: positions not yet compared with the broker's holdings files — tjs sanity` instead;
  `reports/run_summary.json` is as before (the line is not in it: a holdings file is a
  check-only input, and `tjs compare filed` on a year closed before the upgrade still finds the
  reports unchanged). **What to do:** run `tjs sanity` after `tjs run`, or
  `tjs checklist --run` (its sanity step runs it, as before); a script that read the run's
  output for those lines reads `tjs sanity`'s (exit 1 on a difference, 2 on an input it cannot
  read). Nothing in the books moves, and a run's exit code never depended on the check. The
  `holdings/README.txt` an earlier `tjs init` wrote still says the end of `tjs run` compares:
  it is a note nothing reads (delete it, or keep it).

#### Carry-backs (Canada)

**A typed balance no longer answers what an earlier year carried back (Canada).** This can
stop a `tjs close-year` that went through on v1.1.0 or earlier. It applies to a project with a
`[carryover] claimed` entry, a year before the project year that has no close-year lock, and
`[estimate] other_losses` typed in (in this project, or as the opening of the first lock after
that year). Up to v1.1.0 the balance was set against the books' nets: 60 carried in after a
loss of 100 said the loss year carried back 40, and where that was all the claim allowed (or a
lock before the loss year fixed where it started) nothing was asked. The nets are the books'
sales and the T5 box 18 gains you named; a T3 box 21 gain, another box 18 gain or an FX gain
or loss on foreign cash on that year's return is in none of them, and changes the figure (with
a T3 gain of 30 that year carried back 10, and this year's loss can carry the other 30 back).
From this release, where the project year has a loss left to split (the balance carried in is
larger than the year's gain, or the year has a loss) and its carry-back depends on what that
year carried back, `tjs carryover` prints "can't tell", `tjs estimate` prints the
carry-forward as unknown and `tjs close-year` stops. The message gives the line for each
reading and the figure the nets alone give ("On these books' nets alone, the 60.00 you carried
into 2024 ... would mean 2023 carried back 40.00"). Look at that year's T1A and add the line
to taxjson.toml, such as `[carryover] carried_back = { 2023 = 40.00 }`. If it is the nets'
figure, every figure is the one v1.1.0 gave. A year with nothing left to split is not asked,
and no lock already written changes (TJ-4886).

What else changed with it:

- The answer is asked once. `tjs close-year` records it in the lock (`carry_back_stated`), and
  the next years' projects read it there; an entry in a later taxjson.toml that differs from
  the recorded answer is refused, naming both (correct the entry, or close that year again on
  the right one).
- A `carried_back` entry and a typed balance that don't add up on the books' nets were refused
  ("leaves 90.00 carried into 2024, but [estimate] other_losses says 60.00"). Both are your
  figures and both are now taken; `tjs carryover`, `tjs estimate` and `tjs close-year` print a
  warning that names the capital gain or loss those years' returns must have held outside the
  books for both to be right. Check the two figures against the returns when you see it.
- `tjs carryover --json` is `schema_version` 2 (it was 1). Its rows for a year with no lock
  show what the books fold, never a figure taken from the balance: the line "carried back at
  least ..." and the row's `carried_back_inferred` are gone (`by_books_nets` inside
  `carry_back_cant_tell` holds the nets' figure when you are asked). A script that read
  `carried_back_inferred` reads `carry_back_cant_tell` instead; one that checks the number
  accepts 2. One value changes its type: `carry_back_cant_tell.answered` holds, for a year you
  answered by target year, a table (`{ "2021": 40.0 }`) where it held an amount; a script that
  sums `answered` sums the table's values for such a year. `if_none` and `if_all` gain
  `carried_back_to` (the years this year's carry-back goes to in that reading), and the entry
  gains `books_to`. Every other key it had is where it was, with the meaning it had.

**Which claimed years an earlier carry-back went to can now stop the close (Canada).** This
applies to a project with `[carryover] claimed` amounts under two or more earlier years and a
year before the project year whose loss was carried back to them (rebuilt from the books,
recorded by its lock, or given in `[carryover] carried_back`). A T1A names the years a loss
goes back to, and `claimed` holds one total per year, so nothing said which year that
carry-back met: `tjs carryover` met the oldest first, and v1.1.0 printed and locked the
result. Where it makes no difference to this year, nothing changes. Where this year's
carry-back, the loss it carries forward or the years its own carry-back goes to would differ
had it gone to another of those years, `tjs carryover` now prints "can't tell", `tjs estimate`
prints the carry-forward as unknown and `tjs close-year` stops. The message gives a line for
every set of figures this year can have, the one v1.1.0 took first, and what each gives
(TJ-4882, TJ-5063):

    carried_back = { 2022 = { 2020 = 40.00 } }    (2022 carried 40.00 back to 2020: v1.1.0's reading)
    carried_back = { 2022 = { 2021 = 40.00 } }    (2022 carried 40.00 back to 2021)

Look at that year's T1A and add the line it matches under `[carryover]` in taxjson.toml (the
years and amounts the form names, if it is neither); then run the command again. With the
first line every figure is the one v1.1.0 gave; `{ 2022 = 0 }` says a year with no lock
carried nothing back. A plain total (`{ 2022 = 40.00 }`) says how much, not where: the
question stays until the years are given. No lock already written changes; if the T1A of a
year you closed says the loss went to a later year than the oldest, the years after it were
closed on the other reading: give the entry, run `tjs carryover` in each of them, and re-close
(`--force`) those whose figures differ.

**Two or more earlier years with no lock: you can be asked where v1.0.0 to v1.1.0 closed
unasked (Canada).** The question "did those years' returns carry a loss back" compared two
readings: none of them did, and all of them did as the books read. With two such years, one
that did and one that did not can leave this year another carry-back than both, and those
versions asked nothing and locked the figure the two readings shared. Every combination is
compared now. If `tjs carryover` asks in a project that closed before, answer from those
years' T1As; then, for a year already closed, compare its lock's `carried_back` and `closing`
with `tjs carryover` and re-close it (`--force`) if they differ. An entry you had given that
was noted as "no longer needed" is used now where it changes this year's figures (TJ-4882).

What else changed with it:

- `tjs close-year` records the years this year's carry-back goes to (`carried_back_to` in the
  lock) and your answers about earlier years (`carry_back_stated_to`); the next years'
  projects read them, so the question is asked once. A lock written before this release has
  neither and is read as a total met the oldest year first; a by-target entry for its year
  answers where the ledger asks.
- For this year's own T1A the oldest claimed year first is a proposal: `tjs carryover`, the
  estimate and `tjs close-year` print the split when there was a choice. To carry back to
  other years, say so before closing:
  `carried_back = { 2024 = { 2022 = 20.00, 2023 = 40.00 } }` (the amounts add up to the
  carry-back the ledger computes).
- `tjs handoff` notes when the part of this year's loss it compared a `claimed` entry with, or
  the prior record's own carry-back by year, is such a proposal.

#### FX on foreign cash: Kraken and stablecoin sends

- **FX-on-cash ledger v2, Kraken accounts with Hybrid Earn rows of US dollars, another foreign
  fiat currency or (Canada) a USD stablecoin** (TJ-4951): the `undeclared` problem for each
  `hybridearnwithdrawal` goes, and the statement balances include what is parked. **Asked of
  you:** remove every `.tt` `CASHMOVE` line you wrote for a Hybrid Earn row
  (`... -<amount> kept` for the way out, `... <amount> cost=...` or `own` / `spot` for the way
  back). Until then `tjs fx-cash --ledger v2` gives no figure for the year such a line is
  dated in and names each with its file and line; `tjs cash` no longer lists or counts it. A
  figure computed with those lines was computed on cash that left and came back at the costs
  the lines declared; without them the cash never left, so the year's FX gain or loss on cash
  can differ. A line of an earlier year does not stop the current one: a Warning names it.
  Removing it changes that earlier year's figure (and, where later years open from its close,
  the pool carried forward): compute that year again, and if it is filed see
  `tjs check-filed`. Where a line fits both a Hybrid Earn row and a real deposit or withdrawal
  of the same day and amount, the ledger takes neither reading and names the lines and both
  rows: keep the line for the real move with `row=<file:line>` after it (the row as the
  message names it, in double quotes where the file's name holds a space) and remove the
  others. `tjs t1135`'s cash rows read the same events.
- **FX-on-cash ledger v2, any Kraken account** (TJ-4951, TJ-5028): a statement balance (the
  reconciliation's STATEMENT column, a zero opening read from it, `tjs t1135`'s cash) can move
  where one wallet has several ledger rows in one second and the export lists its rows newest
  first, as Kraken's web export does. The balance is now the one after the second's last row
  (the export read from its last line up; rows that state fractions of a second in the order
  of those); it was the one after the row listed last. A reconcile gap that came from this
  goes; a year that reconciled only because two such balances were off by the same amount in
  opposite directions (a USDC-for-USD trade in a newest-first export) can show a gap now.
  Where the `balance` column chains a second's rows in another order than the export lists
  them in and only one balance beside the second holds that order, the file's order is read
  and the balance says so: in its reconcile gap line, or, when it reconciles, in a new Warning
  of `tjs fx-cash --ledger v2`
  (`... reconciles, and may not be the balance at the end of that day`). **Asked of you** only
  then: check that second's rows against Kraken's history; if the export lists them in an
  order they did not happen in, list them in it; if a row is missing, add the export that
  holds it; if the line says the export states no direction (exports pasted together out of
  order, a row moved), sort its rows by time. No gain figure is computed from a statement
  balance, but a year that was NOT COMPUTED for such a gap can become computed.
- **Kraken accounts whose Hybrid Earn deposits exceed the withdrawals before them** (TJ-4954):
  `tjs run` prints a new ATTENTION line per coin (`tjs run --strict` does not stop on it), and
  one more for a second of several rows whose order the `balance` column does not give. No
  books figure moves. In the FX-on-cash ledger v2 the excess of a foreign cash asset is a cash
  inflow of its own: by default a new `undeclared` problem
  (`came in ... add CASHMOVE DATE USD <excess> cost=<CAD> (or own / spot)`), where there was a
  reconcile gap before; with `fx_cash_inflow_cost = "spot"` it comes in at the day's rate and
  the figure includes it.
- **FX-on-cash ledger v2, Canadian projects with a crypto account whose sends.json decides a
  USD stablecoin send** (TJ-4953): the send's `undeclared` problem goes and the ledger counts
  it by the decision (`gift` / `payment`: a disposition at the day's rate; `self`: `kept`, out
  at its cost). A year that was NOT COMPUTED only for such sends becomes computed, with the
  gift's or payment's currency gain in the net: if you added `tjs crypto-sends`' stablecoin
  total to the figure by hand, take it out. **Asked of you** only where a `.tt` `CASHMOVE`
  line you wrote for the send contradicts the decision (`kept` or `own` for a gift or a
  payment; `spot` or `proceeds=` for `self`): the ledger now gives no figure until one of the
  two is changed; before, the line alone counted. A line of the same side still wins over the
  decision, so a year declared entirely by `.tt` lines that agree with the decisions does not
  move.
- **FX-on-cash ledger v2, Kraken accounts: balances and cash that move on a re-run** (TJ-5027,
  TJ-5032, TJ-5033, TJ-5224). After the upgrade `tjs run`, then `tjs fx-cash --ledger v2`:
  - **Statement balances** (the reconciliation's STATEMENT column, a zero opening read from
    it, `tjs t1135`'s cash) are the account's: every wallet of a currency added up, a wallet
    being its asset code and the export's `wallet` column, over every Kraken ledger export in
    the account's folder. What can move:
    - an asset in two wallets of the `wallet` column (spot and Earn): the balance is the sum,
      where it was the wallet of the last row;
    - several ledger exports: one balance per date for the account, where each export gave
      its own (gaps of the ledger against a partial balance go); the balances are at each
      year end and at the last day of the account's rows, no longer at each export's last
      day;
    - rows missing between two exports (the later one begins at another balance than the
      earlier one ended at): the year ends between them read the balance the later export
      begins at. A year that computed against the earlier export's stale balance is NOT
      COMPUTED now, with the gap;
    - a wallet whose first row begins at a balance other than 0 (its earlier rows are in no
      export): no balance of its currency before the year end just before that row, and a
      note. This reaches one export too: an export from 2025 with US dollars active in 2025
      and a USDC wallet first touched in 2026 that opens at 300 gave an opening balance at
      the end of 2024 that counted the 300; it gives none of that currency there now (the
      2025 year end and later are as they were). The ledger for 2025 then has no
      Kraken balance of that currency at the start of the year to set its opening against
      (a `CASHOPEN` line, or the export of the wallet's earlier rows, gives one). A wallet
      that opens at 0 changes nothing.
    A year that was NOT COMPUTED for a gap against a partial balance can compute now.
  - **Deposits and withdrawals.** Two of the same day, currency and amount that sit in two
    exports are two cash moves now, where one was dropped. The second is undeclared until a
    `.tt` `CASHMOVE` line says what it was: `tjs fx-cash --ledger v2` asks for it, and the
    year's FX gain or loss on cash can change. If the year is filed, see `tjs check-filed`.
  - **Transfers between the spot and the staking wallet** (ledger type `transfer`, subtype
    `spottostaking`, `stakingfromspot`, `stakingtospot`, `spotfromstaking`; TJ-5227) are no
    cash moves now: both wallets are the account's. The two undeclared moves per transfer go.
    **Asked of you:** remove the `.tt` `CASHMOVE` lines you wrote for them (one `-<amount>` and
    one `+<amount>` of the same day). Left in place, each is a cash move of its own, and the
    ledger's balance is off by it. A pair declared `own` or at matching cost and proceeds
    left the year's figure unchanged; any other declaration (`spot`, a `cost=` or
    `proceeds=` that do not match) made a gain or loss that goes with the lines: compute the
    year again, and if it is filed see `tjs check-filed`. Transfers to or from futures are
    cash moves as before.
  - **Hybrid Earn in overlapping exports.** What a row brought back beyond what was parked is
    one amount from every export; where two exports listed a second's rows in different
    orders and two undeclared moves were booked for one row, one is left.
  - **New, where an export is missing.** Cash moved to a wallet (an Earn allocation) whose
    row no export in the folder holds: the currency gets no ledger balance from that day on,
    and a note of `tjs fx-cash` names the row (for a foreign currency; for the project's own
    currency no note is shown yet). **Asked of you:** add the ledger export that holds the
    other wallet (one export of the whole ledger holds every wallet), or give the balance as
    a `CASHBAL` line in a `.tt` file.
- **A new refusal: one broker's id with two contents (Coinbase, Kraken)** (TJ-5034, TJ-5221).
  `tjs cash`, `tjs fx-cash --ledger v2`, `tjs t1135` (where it counts cash held outside
  Canada) and `tjs sum` with the ledger v2 stop (exit 2) when two exports of an account hold
  one Coinbase `ID` or Kraken `txid` on a cash row with another date, currency or amount in
  each: "the broker's id CB*** of one CASHMOVE is in two exports with different content", with
  both rows and the field. `tjs run` finishes and says it as an ATTENTION; `tjs run --strict`
  stops (exit 1), the checklist's run step lists it, and `tjs close-year` refuses the books
  until it is fixed (as for any open problem of the run). Up to this release both copies were
  booked. **Asked of you:** keep only the export with the right copy of the row (remove the
  other file from the account's folder, or the row from it), then `tjs run`. A figure computed
  with both copies counted that deposit or withdrawal twice.

#### `.tt` files

**A `.tt` file converted from a book can move `tjs fx-cash` on a re-run, once you convert it
again** (TJ-5036). This concerns a project that reads a `.tt` file which
`taxjson-convert-tt book.json out.tt` wrote (or lines copied from `tjs events`) from a book
holding a crypto-for-crypto trade, a reward paid in a coin or a stablecoin, a fee an exchange
took in a coin, a broker's cash return of capital, or an IB trade whose commission was
refunded later. Such a file, written up to v1.1.0, reads those rows as ordinary cash lines
(or, the return of capital, as no cash). Nothing changes while you keep the file as it is: a
line without the new tokens reads as it always did. Convert the book again and the lines
carry `cash_leg=none`, `cash_leg=reward`, `cash_leg=cash` or `refunds=DATE:AMOUNT`; then
`tjs fx-cash` (both ledgers; the foreign-cash rows of `tjs t1135` with ledger v2) gives the
book's figures: no US dollars for an in-kind leg or reward, the return of capital's dollars
received, a refunded commission received on its own date. These tokens move no gain on a
security, no income figure and no row id; the dates a new conversion keeps can (below).

**`taxjson-convert-tt book.json out.tt` can now stop where it used to write a file**
(TJ-5036, TJ-5213). It refuses a row whose line would move other foreign cash than the row
does, naming the row, and writes nothing: a move in kind that `tjs run` booked (a row of type
`transfer_book_value` or `in_kind_…`, in a book under `work/`), a reward's INTEREST, FEE or
TAX row and a coin fee's INTEREST or FEE row, and a row whose dates no line says (it settles before the day it
trades or is paid, or more than 366 days after: fix the row's dates in the book). For a move
in kind, convert the account's parsed book instead of the run's, and keep the move's `INKIND`
line or transfer settings in the project.

**`settled=` and row ids** (TJ-5211, TJ-5213). A row that settles after the date its line
carries (an income, tax, interest or fee row paid one day and settled later; a trade
converted with `--date-basis trade`) is now written with `settled=YYYY-MM-DD`, and reads back
with its settlement date: `tjs fx-cash` walks its cash on that day (a dividend paid on Dec 31
and settled on Jan 2 moves from the earlier year's ledger to the later one's, at that day's
rate), and `tjs t1135`'s cash rows follow. The settlement date is part of a row's id, so such
a line has another id than the line an older release wrote for the same row: if a project
reads both an old converted file and a new one of the same book, those rows are no longer
recognised as the same row. The same holds for `traded=YYYY-MM-DD`, which a trade converted
with `--date-basis settle` (Canada's default) now ends with when it settled after it traded:
that is most trades of a converted book, so a file converted before this release and one
converted now share almost no trade ids.

**Replace the old converted file; never keep it beside the new one** (TJ-5214). With both in
an account's folder the run books those trades twice, and its duplicate-rows ATTENTION no
longer names them: it lists only the rows the two files still share (with two old files it
named every row). Delete the old file when you add the new one, then run again.

**A project rebuilt from the new file is not figure-for-figure the one built from the old
file** (TJ-5213). The old settle-basis file gave every trade its settlement date as its trade
date; the new one gives the real trade date. The tax year, the proceeds, the costs and the
settled cash are the same. What reads a trade date can differ: a holding period (a day or two
longer, as the book's own), the order of two trades that settle on one day (it now follows
their trade dates, as in the book, which decides which of them a same-day rule sees first),
and `tjs t1135`'s cash on trade dates. These are the book's figures; the old file's were the
approximation. Lines without a token, and every file you wrote by hand, keep their ids and
their figures.

**`tjs events` marks rows that are not `.tt` rows** (TJ-5212, TJ-5213). The line of a move in
kind the run booked, of a reward's INTEREST, FEE or TAX row or a coin fee's INTEREST or FEE
row, or of a row that
settles before the day it trades or is paid, ends with `not_tt=in_kind`, `not_tt=cash_leg` or
`not_tt=dates`. A script that reads the view's text sees one more token on those lines
(`--json` is unchanged); pasted into a `.tt` file the line is refused, saying why.

**New `.tt` tokens** (TJ-5036, TJ-5211, TJ-5213). `settled=YYYY-MM-DD`, `traded=YYYY-MM-DD`,
`cash_leg=none|reward|cash` and `refunds=DATE:AMOUNT[;DATE:AMOUNT]` (docs/settings.md, ".tt
files"). An older taxjson refuses a line that carries one ("unknown .tt token" or an
unexpected trailing token): a file written by this release is read by this release or a
later one.

#### `tjs t1135` (Canada): a security's country from its issuer

**`tjs t1135`: countries and verdicts can move (Canada).** A security's country now comes from
its issuer where the books say who that is (TJ-4957). After upgrading, run `tjs run` (the
Interactive Brokers reader now stamps the ISIN on every share row, so the books must be
rebuilt), then `tjs t1135 --details`, and compare with the report you last kept:

- **Rows that appear.** A share on a Canadian listing whose ISIN is another country's is now on
  the form under that country (it was left off, with a warning or a NOT RESOLVED verdict). The
  maximum total cost rises by its cost, and a "no T1135 required" can become FILING REQUIRED.
- **Rows that leave.** A share on a foreign listing whose ISIN is Canadian is no longer foreign
  property. The maximum falls, and FILING REQUIRED can become "no T1135 required". If the
  account's `custodian_country` is outside Canada it becomes a category 6 row under the
  custodian's country instead.
- **Rows that change country.** A US-listed share with another country's ISIN (CYM, IRL, NLD,
  BMU) moves from USA to that country: the per-country table changes, the total does not.
- **What to check.** `--details` lists each of these under "COUNTED BY THE ISIN, NOT THE
  LISTING" with its cost. An ISIN says where the issuer is registered; the form asks for its
  country of residence. Where you know they differ, add `T1135 SYMBOL COUNTRY` to ticker.map
  (`CA` for not foreign property). The CRA's T1135 guidance says how residence is decided;
  taxjson gives no tax advice.
- **Lines you already have.** Every `T1135` line still wins. One thing changed: a line now
  covers its security on both sides of a ticker change, so a line on the new ticker also
  decides the months under the old one (the report used to count those by the old listing), and
  a line on an old ticker no longer reaches a merger partner's shares or the same ticker bought
  again later. If a year's figures move for a renamed holding, that is why (TJ-4964, TJ-5048,
  TJ-5049).
- **Options.** A listed option whose listing and share disagree (a US-listed option on a share
  whose ISIN is Canadian; a Montreal-listed one on a foreign issuer's share) can now turn a
  firm verdict into NOT RESOLVED, with the two `T1135` lines on the contract that settle it
  (TJ-4963).
- **New `! ` lines.** "N countries assumed from the listing (no ISIN)": informational, and
  usual in a project of brokers whose exports carry no ISIN. "N to review for the country": a
  depositary receipt, an ISIN prefix that is no country's, sources that disagree, or an earlier
  holding's ISIN of another foreign country; the checklist's t1135 step needs attention until a
  `T1135` line settles each.
- **A ticker bought again.** A security sold out everywhere and bought again later is counted
  by its listing (`assumed`), not by the earlier holding's ISIN; where that ISIN says otherwise
  on being foreign property the verdict is NOT RESOLVED until `T1135 SYMBOL COUNTRY` or
  `T1135 SYMBOL CA` is in ticker.map. A registered account that held the security before the
  taxable accounts bought it is such an earlier holding too: its ISIN is read for them only
  from its rows dated while they hold the security (TJ-5106).
- **Canadian depositary receipts (CDRs).** A receipt that is Canadian by its own ISIN is still
  off the form as counted, and the test is now worked out with it counted too: a large holding
  of them turns "no T1135 required" into NOT RESOLVED until `T1135 SYMBOL COUNTRY` (the country
  of the company behind it) or `T1135 SYMBOL CA` is in ticker.map (TJ-5105).
- **`--json`: `schema_version` 2.** A script that read version 1 changes as follows.
  `deferred_wash_not_in_cost` (always `{}`) and `full_history_wash` (always true) are removed:
  drop the reads. `canadian_issuer_symbols`, `foreign_issuer_symbols` and
  `foreign_issuer_countries` are removed (no symbol is counted against its ISIN): read
  `listing_overruled_symbols` for the names and `country_decisions` for each one's `country`,
  `listing_country`, `country_source` and cost. `classification_conflicts` entries now carry
  `kind` and `countries`; the version 1 kinds (an ISIN against a listing) and their
  `lines.as_counted` / `lines.other` no longer exist: a country-only entry carries
  `lines.choices`, a total-affecting one `lines.foreign` / `lines.not_foreign` as before, and
  `issuer_country` is filled only for an option's entry. A row's `country` can be `??` for a
  share named for review. Keys added: each row's `country_source` (and
  `country_source_account`, `country_learned_accounts`, `isin_country_code`, `country_line`,
  `country_review`, `country_earlier`), `country_decisions` (with `country_earlier`),
  `assumed_symbols`, `assumed_foreign_cost`, `assumed_not_foreign_cost`,
  `listing_overruled_symbols`, `country_review` (the reason `earlier_country` with `earlier`),
  `country_review_symbols`.
- **A previous year.** A closed year's report is not rewritten. If the comparison shows a
  holding that should have been on an earlier year's form, that is a question for the CRA's
  guidance or a tax professional.

#### Figures that move: the engines and the brokers' rows

- **Interactive Brokers, a return of capital and the listing held** (TJ-5011, TJ-5201,
  TJ-5202, TJ-5203). Two figures can move on a re-run:
  - a return of capital that was moved off the listing its ISIN names onto a listing the
    statement's own rows show was not held that day and no other statement of the broker
    account leaves room for (a Canadian-ISIN payment in January, the statement's only trades
    of the ticker a US-line purchase in February) now stays on the ISIN's listing. Before, it
    was a capital gain on the empty US line; now it reduces the cost of the ISIN's listing
    where the account's other exports hold it (no gain), or is a gain there where they do not.
    Either way the run says it (below);
  - one dividend or return of capital that two overlapping statements both hold, put on two
    listings, was booked twice. It is booked once, with
    `Warning: One payment in two exports: …`; where nothing says which listing is right the
    run stops, naming both files and lines, until one statement of the account that holds the
    trades behind both placings replaces them.
  And a re-run now says, for a return of capital on a listing no dated row of its statement
  shows held that day while the account's exports name another listing of the ticker:
  `Warning: Undated listing: return of capital DATE on SYN.TO may belong to SYN.US`, with the
  account, file and line, what the books did with it and what to do, after the cross-account
  pass and again in the closing list. `tjs run --strict` (CI, cron) now stops on it, with
  `--account` too, and `tjs checklist` lists it under run-clean, until the account has a
  statement that holds its Open Positions section, or every trade of the ticker from before
  the holding's purchase, in place of the split exports, or ticker.map has the `TOBASE` line
  the Warning names (when the two listings are one security). The report's DIAGNOSTICS note
  for split exports reads "the account's other exports name more than one listing of the
  ticker (SYN.TO, SYN.US) with no dates this statement can use, so the account may have held
  SYN.US that day instead" (it said "the account held more than one listing of the ticker
  that day"): a script that matched the old words must match the new ones.
- **US, a §355 spin-off (`tax_free_355`) with a record date on a holding in fractions of a
  share** (TJ-4988). When the count the broker paid on equals the whole holding on the pay
  date and the shares held before the record date are within one share of that count, every
  lot now gives up its part of the basis; before, only the lots bought before the record date
  did. What moves: the parent lots' basis (30 and 60 become 36 and 54 in the 0.4 + 0.6 case),
  the spun-off shares' lots (one per parent lot, each with its own date, so their holding
  periods), and the gain and term of a later part sale of the parent or the new shares. The
  basis in total is unchanged. When the count is a whole number the run adds the ATTENTION
  line (the count could be either holding, rounded): check the lots against the Form 8937. A
  count within one share of both holdings gets the same line and figures. Whole-share holdings
  are not affected. Re-run and compare `tjs sum`; a closed year is not rewritten.
- **Canada, a return of capital on a holding of fewer than 0.000001 units that still has its
  cost** (TJ-5001). The distribution now lowers the ACB when it leaves the holding some cost
  (half a cent or more). Before, it was a deemed gain in the year received and the later sale
  had a loss of the same amount. What moves: that gain leaves the year of the distribution;
  the later sale's gain rises by the same amount in its year. If the earlier year is filed
  with the deemed gain, it needs an adjustment. A distribution of the holding's whole cost or
  more is booked as before (no figure moves), with a new warning text and record note. A WASH_
  adjustment, the s.53(1)(f) cut and the s.40(3) reset on such a holding follow the same test,
  and a holding of exactly 0.000001 units counts as held whatever its cost (it shows in the
  inventory). Holdings above the tolerance are not affected.
- **Split-fill marks and row ids** (TJ-5038). A row that was marked ` [fill #2]` (or #3 …)
  only because an earlier row of its file had the same date, time, symbol, quantity, price,
  amount and account, while its description, currency, settlement date, type or gross amount
  differed, loses the mark: its description and its row id change. Rows identical in all of
  those keep their marks and ids. For whom: any importer and `.tt` files; most often two
  same-day dividends or fees of one amount with different descriptions, two fills a broker
  describes differently, or two Questrade journals of one security on one day whose book
  values differ (the arriving legs). What moves: where overlapping exports had booked such a
  row twice, the duplicate goes (holdings and ACB fall by that trade; income by a doubled
  dividend). What it asks of you: anything you saved against the old row id of such a row (a
  loss override, a note keyed to the row) no longer matches it and is entered again against
  the new id; re-run and compare `tjs sum`. In a generic export listed newest first, the mark
  of two fully identical rows may sit on the other row than before: the ids are the same pair.
- **Generic importer, a mapping whose `[formats] date` has a clock** (TJ-5018). Transfer,
  dividend, payment-in-lieu, tax, interest and fee rows now carry the time of their cell, not
  09:30:00. Their row ids change with the time: re-enter anything saved against the old id of
  such a row. What moves, and for whom: only a mapping with `settle_on_trade_date = true`,
  where a transfer in or out now falls among the day's trades at its time. Example: 10 units
  held at 100, 10 more bought at noon for 200, 10 moved in kind to a registered account at
  15:59 at a value of 200: the gain was 100 with 10 left at 200, and is 50 with 10 left at
  150; units bought and moved out whole the same day no longer show as a sale with no
  purchase. In `tjs fx-cash`, the order of a day's foreign cash follows the times. Under the
  default settlement, and for a move between two taxable accounts, no figure moves. A return
  of capital or `adjust` row stays at 09:30:00: no figure of theirs changes. Mappings with a
  date-only format are not affected.
- **Generic importer, a rename row with 0 in the ratio cell** (TJ-5019). The file is now
  refused, naming the line; before, the rename was booked at 1-for-1. Blank the cell for a
  plain rename or write the real ratio. No figure changes where the 0 stood for "no ratio";
  where it hid a real ratio, the books were wrong before and the fix is the ratio.
- **A futures option written with `/` or `\` follows its future's ticker.map rename**
  (TJ-5037; only for a project with such a line). A futures option written with `/` or `\`
  whose future has a ticker.map rename in the same spelling (`GLOBAL /QZ.US /QX.US`) now
  follows the rule: after `tjs run` its rows are under the mapped root, joined with the mapped
  contract's rows, and its gains and holdings move there. Nothing to do.
- **Cash in lieu of a fraction paid on two days is sold on each day** (TJ-5057). **What moves,
  and for whom:** only where one account holds a spin-off or a share-for-share merger in two
  broker accounts that paid their cash in lieu on different days, or one RBC or Questrade
  account shows two payments for the event on two days. The one sale of the fraction becomes
  one sale per day: the total gain on the fraction is the same, but the part paid later moves
  to its own day. When the days are in two tax years, that part's gain (Schedule 3 / the gains
  report) and its cash (the foreign-cash ledger) move from the earlier year to the later one. A
  closed year whose books held such an event will show a changed figure in `tjs check-filed`. A
  payment the broker reversed and made again does not move: it keeps the reversed payment's
  day. Nothing to enter: the election and its allocated cost stand.
- **A snapshot after a rename no longer counts an exercised option's premium twice** (TJ-5005).
  **What moves, and for whom:** only if an account's books hold an option exercised or assigned
  into shares under a ticker that was then renamed, and an `OPENING` snapshot of the new ticker
  that replaces those shares' row. The option's premium was booked a second time, as a loss on
  the option (or in the cost of a trade of the old ticker within 7 days); it is now counted
  once, in the snapshot's cost, and the year's gain rises by the premium (falls, for a written
  option's premium received), in Canada and the US. If the run cannot tell that the replaced
  row is the delivery it says so and books as before: follow the warning (it names the row to
  mark, or the option to rewrite).
- **A new warning for an option exercise row that carries an amount and is booked as delivered
  into an `OPENING` snapshot** (TJ-5005's review). If `tjs run` prints "...: the exercise or
  assignment row of ... carries ... and is booked as DELIVERED", the amount on that row is in
  no gain. **What moves:** where the snapshot is of the underlying's own ticker, nothing (such
  a row was booked as delivered before). Where it is of the ticker the underlying was renamed
  to and a replaced row is the delivery (marked `ASSIGN`, or at the strike), such a row was
  booked as cash-settled, as every exercise on that path was (the item above), and is now
  booked as delivered: its premium is in the snapshot's cost once and the year's gain moves as
  the item above says. **What to do:** if shares were delivered, write the row with no amount
  (a charge or cash in lieu as a row of its own); if it was settled in cash, write it as a
  closing `BUYSELL` of the option at the settlement amount (then its gain or loss appears, and
  the year's gain moves by it).
- **Option gains move where a `RENAME` line renamed a ticker you held options on** (TJ-5008).
  The line now moves each open contract on the old ticker to the new one. **What moves:** in
  Canada, a contract bought under the old ticker and closed under the new one reports its real
  gain or loss (proceeds less the premium paid) where it reported the whole proceeds as a
  written premium; a written contract bought back under the new ticker likewise. In the US, the
  close books its gain or loss where it booked none. The holdings no longer list the old
  contract and an opposite position in the new one, and the "expired … but the books still
  hold" warnings for the pair go away. **Who:** a project with a dated `RENAME` (a `.tt` line
  or the legacy ticker.map line) of a ticker whose options an account held on the date, unless
  each contract already had a `.tt` `SPLIT` line of its own. **To do:** re-run; compare the
  year's option gains before filing; a closed year whose figures change shows in
  `tjs check-filed`. An account that held only open contracts no longer gets "RENAME … books
  nothing", so `tjs run --strict` no longer stops on it.
- **`late=fold` now also re-books late option rows in an account that held only option
  contracts on the old ticker** (TJ-5008). Such an account now carries the rename, so the
  event's `late=fold` applies to its later rows under the old root (shares and options) where
  they were kept as the old ticker and listed as undeclared. **What moves:** those rows'
  positions and gains move from OLD to NEW. **Who:** an account with an option on OLD open at
  the rename date, no OLD shares, and trades under the old root after it, in a project whose
  line says `late=fold`. An account whose contracts were all closed or expired before the date
  is unchanged. **To do:** if those later rows are another company's, give the account its own
  line with `late=separate`.
- **Rows under the old symbol of a contract still held no longer wait for `late=`** (TJ-5008).
  While units the change moved are left in an account, every later row under that contract's
  old symbol (a sale, an expiry, an assignment, an addition) is booked as the new contract.
  **What moves:** for a close of what was held, nothing against 1.1.0 (1.1.0 kept both the
  contract and its close on OLD and reported the same gain); the "N trade(s) in OLD after its
  rename" ATTENTION line and the `tjs renames` late entry for it are gone, so
  `tjs run --strict` no longer stops there. With `late=fold` the gain is now right where 1.1.0
  moved the close to NEW and left the contract on OLD (Canada: the whole proceeds as premium;
  US: no gain). An addition under the old symbol and its later close now sit on the new
  contract, the same figures as 1.1.0 gave on the old one. A close of more than is held books
  its excess as a written position on the new contract (1.1.0: on the old one, the same gain),
  with a new Warning. `tjs renames --json` events gain a `booked_as_new` list (no key changed).
- **Year-end positions on the non-default `tax_date` can gain or lose rows** (TJ-4984). **What
  moves:** in a US project with `tax_date = "settle"`, the year-end lots, the per-lot detail of
  a closed year and the disallowed losses carried into the next year (`tjs edge-cases`) now
  include a row traded after a January-settling sale and settled by December 31; in a Canadian
  project with `tax_date = "trade"`, the denied losses carried into the next year no longer
  count units sold in December whose sale settled after a January row, and count a December
  purchase or denied loss that settled after one. No gain or loss of any year changes. A
  position whose own rows cross the two date orders gets a new Warning on the run's console;
  `tjs run --strict` stops on it and `tjs checklist` shows its run step as needing attention
  until the position's dates are corrected. **Who:** only projects that set the non-default
  `tax_date` and have late-December rows whose trade and settlement order differ. **To do:**
  re-run; a US year already closed on `tax_date = "settle"` keeps the lots it recorded until it
  is closed again (`tjs unclose-year`, then `tjs close-year`).

#### Futures

- **A future written `/` or `\` is `F:`: its pool merges with the broker's, its rows' ids
  change** (TJ-5150, TJ-5160). Every symbol you write with a futures prefix (`/QZM26.US`,
  `\QZM26.US`, an option on such a future) is now read as the `F:` spelling the broker parsers
  write: in `.tt` lines, `ticker.map` and `tobase.map` lines (every keyword), holdings files,
  `ALLOWLOSS` lines, slips, `--filed-dispositions`, command arguments and the rows of a JSON
  book given to a stand-alone tool (`taxjson-gains book.json` …). What a re-run changes:
  - A `/` or `\` `.tt` line and the export's `F:` rows of the same contract are one pool: the
    gains, the deferrals and the positions of that contract can move (a `.tt` sale of an
    exported long position was an opening short of its own pool; it now closes the long and
    realizes the gain). `tjs trades` and `tjs sanity` show one `F:` symbol.
  - Those rows' ids change: a row's id hashes its symbol, and the `F:` spelling is hashed. An
    `F:` row keeps its id, and so does a JSON book's row that states its `id`; a JSON row
    without one gets the id of its `F:` spelling. Two `.tt` lines that differ only in the
    prefix (`/QZM26.US` and `F:QZM26.US`, all else equal) are now identical lines: in one file
    both are booked, the second marked `[fill #2]`; in two files both are booked with an
    ATTENTION line, as two identical lines always were. Delete one if it is one trade typed
    twice. A `.tt` line that repeats an exported `F:` row is now warned about, as any hand copy
    of an exported row is.
  - A ticker.map line written `/` now applies to the `F:` rows. If your map has two lines for
    one future in two spellings (`MULT /QZ 50` and `MULT F:QZ 50`, `GLOBAL /QZ.US …` and
    `GLOBAL F:QZ.US …`), they are one rule: the same value is fine, two values stop `tjs run`,
    naming the second line (keep one).
  - A year closed with such rows: its record (filed/YEAR.json) is read as the books spell it
    now, so the next year's `tjs handoff` compares one `F:` contract (a contract the record
    holds under both spellings is one position, its quantities summed): no re-close for the
    hand-off. If the merge moves the closed year's figures, every full `tjs run` and
    `tjs check-filed` (exit 1) say `filed YEAR DRIFTED vs YEAR.json` with the moved totals and
    form lines. Nothing is dropped silently. Review the drift; then amend the return or
    re-close the year (`tjs close-year --force` in its project).
  - A workaround line you added for the two spellings now names one symbol twice: a
    `GLOBAL /QZM26.US F:QZM26.US` line warns on every `tjs run`
    (`ticker.map:N: GLOBAL renames a symbol to itself (no effect): '…' — skipping`), a
    `DISTINCT /QZM26.US F:QZM26.US` one likewise (`DISTINCT pairs a symbol with itself`), and a
    `.tt` `RENAME <date> /QZM26.US F:QZM26.US` is not booked (a note: both are `F:QZM26.US` in
    the books). Delete the line.
  - A futures symbol that names no contract once read `F:` (`//QZM26.US`, `/F:QZM26.US`,
    `/.US`) is refused, naming the line and the spelling to write; it was booked as a pool of
    its own (`F:/QZM26.US` …).
  - An `ALLOWLOSS` line written `/` names the `F:` sale; one that no longer matches a denied
    loss (the merged pool denies another) stops the run, naming the line, as before.
- **A futures contract bought on a `.tt` line whose total is negative: its gain changes sign in
  the base-currency figures** (TJ-5162). A futures BUY's total written as the cash paid out
  (`... F:SAMPLM6.US 2 USD 300 -60000 0 x100`), or a short's cover written so, was booked as a
  credit when the contract was put on its settled P/L: `tjs gains` showed the trade's real gain
  or loss, while `tjs sum`, the `.sum` reports, Schedule 3 (US: the realized totals),
  `tjs audit` and `tjs fx-cash` showed proceeds plus cost. Either sign is now the cost in every
  figure. **What moves:** after `tjs run`, the realized gain of each such contract in those
  views, by twice the buy's total. `tjs gains` does not move. **What to do:** run `tjs run`; if
  a year holding such a line is closed, `tjs check-filed` there reports the drift, and whether
  to amend is a question for the CRA or IRS guidance or a tax professional. A `.tt` file that
  `taxjson-convert-tt` wrote from a JSON book of raw fills can hold such lines; they read
  correctly now.
- **A settled futures `.tt` file written by an older `taxjson-convert-tt` may be refused**
  (TJ-5162). A file of `type=futures_settlement` lines written from a base-currency book
  (`work/<account>_base.json`) whose futures buy was in the cash sign holds the wrong gain. Its
  opening line (`settle=LO,…` or a cover's `settle=SC,…` with negative money at a price
  above 0) is now refused, naming the file and line. **What to do:** run `tjs run` on the
  source (the export or the hand-written `.tt` file) and write the settled file again from the
  new book. A file that holds only the closing lines of such a contract (its opening in another
  file) cannot be told apart and is read as written: write it again too.
- **A futures `.tt` line at a negative price needs its contract size and its signed total**
  (TJ-5162). It is read only with `x<size>` after the fee and a total of qty × price × size +
  fee (a buy: money received, a negative total) or − fee (a sale: money paid, a negative
  total), within 1% and of that sign. A line without the size, with a total of the other sign
  or of another size, and a futures buy with price 0 and a negative total, are refused, naming
  the file and line (they were booked as written). **What to do:** `tjs check-tt` lists them;
  write the total the message gives.

#### The year end: `tjs close-year` and `tjs handoff`

- **`tjs handoff` compares the Dec 30 and Dec 31 sales and income to the cent, and a close-year
  lock records more of each December and January row** (TJ-5065, TJ-5066, TJ-5180, TJ-5181,
  TJ-5183, TJ-5185, TJ-5186). `tjs close-year` now writes, for each row of the lock's
  `boundary_rows`, `income` (the amount the income report counts: a dividend's gross) and the
  amounts as the broker stated them, each in its own currency: `native_currency`,
  `native_income`, and `native_net` or, for a dividend with tax withheld in TAX rows,
  `native_withheld` ({currency: amount}), or `native_withheld_day` where two or more dividends
  of the security share the day's TAX rows; none of these for a crypto row taxjson priced. No
  field changes meaning and no `schema_version` moves (the lock's stays 2, `record_version` 2,
  `tjs handoff --json` 1); handoff's `boundary` items gain `income`, `recorded_income`,
  `income_difference` and, for a row in another currency, `currency`, `native_income`,
  `recorded_native_income`, `native_income_difference`, `native_amount`,
  `recorded_native_amount`, `native_difference`, and `withheld`, `recorded_withheld`,
  `withheld_difference`; a re-dated, re-counted or re-spelled item `changed`, `recorded_date`,
  `quantity`, `recorded_quantity`, `recorded_symbol`; `difference` is still the base net (it
  includes any move of the rate). **What moves:** `tjs handoff` can report a correction it
  passed before (under 0.5%, a dividend's gross or withholding split anew, a row the broker
  removed), and stops reporting a converted row whose amount moved only with a refreshed
  exchange rate. `tjs close-year` refuses when `work/<account>_raw.json` is missing: run
  `tjs run` first. **An older lock** (closed by v1.1.0 or an older taxjson) has none of the new
  fields: its rows are compared as before, at the converted net within 0.5%, and a note says
  so. To compare them to the cent, re-close that year with this release (`tjs check-filed` in
  its project, then `tjs close-year --force`), then run `tjs handoff` again. No figure moves.
- **`tjs handoff` no longer reports another account's sale as a double** (TJ-5067): nothing to
  do.

#### Rates files

- **A rates file with a row dated on a day that does not exist, or whose rate is not a plain
  decimal number, stops the command** (TJ-5045, TJ-5046). **What is asked, and of whom:** only
  if you edited `work/to_base.csv` or a `--rates` file by hand and left such a row (a rate with
  an underscore, a decimal comma or letters; a date such as `2025-02-30`). A file that loaded
  before with "warning: skipped N malformed FX rate line(s)" for a bad rate now stops instead:
  `tjs run`, `tjs crypto-sends` and the views that read the rates print "rates file ..., line
  N: ... is not a plain decimal number" (or "is not a day of the calendar") and exit. Correct
  or delete the line, or `tjs run` to rebuild the file. A line that is no row at all (a header,
  a comma-separated line) is skipped as before. **What moves:** once the line is corrected, the
  rows of its day convert at its own rate, not at the earlier day's that stood in while it was
  skipped. A crypto gift or payment that `tjs crypto-sends` valued from such a line (at 100
  times the rate for `1_35`, or at the rate of a `2025-02-30` line for the days after it) was
  written to `crypto_sends.tt` with that value and stays as written: check those lines.

#### Planning views

- **`tjs div-estimate`: a renamed company whose old ticker was used again moves** (TJ-5052).
  The holdings of another security under the old ticker after the rename were read as the
  renamed company's, so its row could read irregular (a quarterly date passed "with the shares
  held and no payment"), and its window amount fell. The row and the estimate's totals for
  that company can change; nothing else does.
- **Canada, planning views: a holding a rename merged on one trade date can show a later
  TX_ADD** (TJ-2722). Two holdings bought on the same trade date and merged by a rename now
  show the later settlement date (it showed the target's own, which could be the earlier), so
  `tjs harvest`'s TX_ADD and a loss's clear date can move a few days later for such a holding.
  Gains and every filed figure are unchanged.

#### Scripts and the stand-alone tools

- **`taxjson-wash-radar` and `taxjson-harvest` run on their own refuse inputs they misread**
  (TJ-5068, TJ-5054, TJ-5055). The radar refuses a book in another currency than the return's
  (convert it first with `taxjson-convert-currency --to CAD --rates FILE`, or `--to USD` in a
  US return, or pass the converted `work/<account>_base.json`); harvest refuses a file with no
  `inventory` list (pass the gains files), a file named twice (name each once), two books of
  one account (pass its `_gains_wash.json`, else its `_gains.json`) and the run's
  `_raw_gains.json` / `_raw_base_gains.json`. A script that globbed `work/*gains*.json` into
  harvest now stops: name one book per account. `tjs wash-radar` and `tjs harvest` pass the
  right files and are unchanged.
- **`taxjson-convert-tt` on a JSON book: a book it cannot read exits 2** (TJ-5035, TJ-5154;
  only for a script that runs `taxjson-convert-tt` on a JSON book). A book that is not UTF-8,
  or not valid JSON, now exits 2, the code docs/commands.md's exit table gives an input that
  cannot be read; it exited 1. A .tt line or a JSON row the tool cannot convert still exits 1.
  A script that treated 1 as "unreadable book": test for 2. `tjs run` never runs it on a JSON
  book.
- **`tjs fx-cash` and two stand-alone tools exit 2, not 1, for a refused rates file** (TJ-5045,
  TJ-5046; `taxjson-convert-currency`, `taxjson-merge2`): a script that tests for exit 1 there
  needs the new code. `tjs div-estimate` prints the one `Error:` line where it printed a
  traceback.

### v1.1.0

- **`tjs div-estimate`: a payer with too short a history is projected quarterly, shown as
  assumed, and the estimate's totals move.** A payer with a single payment in the books was
  estimated at one payment a year (pro rata from its 12-month amount per share), and two
  payments six months apart read as semi-annual even when you did not hold the shares on
  the quarterly date between them. Such a payer (one to three payments on quarterly dates,
  the latest in the last 12 months) is now projected every quarter at its latest per-share
  amount, until the books contradict it: a quarterly date with no payment of any kind that
  passed while the books show the shares held, or an overdue date nothing in the books
  explains. The row shows `quarterly?` under FREQ and stays under "payer(s) to check";
  `--details` says "assumed quarterly: N payment(s) in the books, none contradicts it", and
  "the books of ACCOUNT end YYYY-MM-DD: later dates not checked" where an export is older
  than the date asked about (one such date: a missing payment; two: not assumed); `--json`
  rows gain `frequency_assumed` (true or false; `schema_version` stays 1, no key changed).
  **What moves:** "Next N months", "Rest of tax year" and the withholding totals rise where
  you hold such payers (up to four times the old figure for a single payment). A payer that
  really pays once or twice a year, or whose only payment was a special dividend, is
  over-projected until its next payment or a passed quarterly date shows it. A payer whose
  history shows its schedule (four quarterly payments, twelve monthly, two a year apart) is
  projected as before. Nothing to do; these are planning numbers, and no filed figure reads
  them.
- **taxjson.toml gains a "Views" group** (TJ-4975). The template has a new `[settings]` key,
  `benchmark` (the symbol `tjs technicals` measures beta and alpha against). Until
  `tjs format --write` adds its commented-out line, `tjs format --check` reports your
  taxjson.toml as not formatted. Only comments change and no figure moves. Setting the key is
  no run input either: the books stay fresh, a closed year's `tjs check-filed` stays OK, and
  `tjs checklist` keeps its verdicts. Without the key, `tjs technicals` leaves beta and alpha
  blank and says so in one `Info:` line.
- **`tjs run` on a book with many written options is several times faster** (TJ-4977 to
  TJ-4982): no figure moves, and nothing is asked of you.

### v1.0.1

- **Code of your own that imports taxjson's modules** (a script, a notebook; not `tjs`,
  which is unchanged): the old import paths v1.0.0 still kept are gone. `taxjson.lib.core`
  and `taxjson.lib.phantom_holdings` no longer exist, `taxjson.lib.missing_history` no longer
  has its old names (`detect_phantoms`, `load_phantoms`, `PhantomCandidate`, ...), and
  `taxjson.lib.income_dating` no longer re-exports the names of
  `taxjson.characterize.income_dating`. Import each name from the module that defines it
  (the CHANGELOG lists them;
  [troubleshooting](troubleshooting.md#modulenotfounderror-no-module-named-taxjsonlibcore-or-taxjsonlibphantom_holdings-or-importerror-cannot-import-name-is_canadian_issuer-from-taxjsonlibincome_dating-or-detect_phantoms-from-taxjsonlibmissing_history-in-a-script-of-your-own-after-an-upgrade)),
  or better, run `tjs` and read its `--json`, which a release keeps: the import paths are
  internal ([What 1.0.0 keeps](#what-100-keeps)). A plugin reads the core through
  `src/taxjson/pipeline/plugin_api.py`. No figure moves.
- **Exit codes with a closed or full standard stream** (TJ-4831). A command started with
  stdout closed (`>&-`) exits as with `>/dev/null` (several exited 1 with a traceback).
  `tjs run` with stdout on a full disk or device runs to its end and exits 2 with one
  `cannot write the output: No space left on device` line (it exited 120, its books not
  built). A command whose stdout a full disk refused exits 2 when it would have exited 0,
  else with its own exit, and says the lost output either way (after a tool's finding, exit
  1, it exited 2; after a refusal it did not say it). With stderr on a full disk a command
  exits as it does with stderr writable, its messages lost (some exited 120, every exit-1
  refusal among them). **What it asks of you:** a script that tested for 120 in these
  cases, or for 2 after a finding, tests for the command's own exit.
- **taxjson-fetch runs only at the core's own version** (TJ-2554). The installer upgrades
  both together ([How to upgrade](#how-to-upgrade)). If `tjs fetch` says
  `taxjson-fetch … is installed with taxjson …: the two are released together and run only at
  the same version, so it is not loaded`, one of the two was upgraded alone: re-run the
  installer, or, from a checkout, `pip install --no-deps -e packages/taxjson-fetch` into
  taxjson's own environment after the core, from the same checkout. `tjs fetch --list` lists
  it as not loaded, and its `--json` carries a `not_loaded` list then (an added key;
  `schema_version` stays 1). Another broker's fetcher plugin is not held to it.

### v1.0.0

Everything since v0.41.0 (there was no v0.42.0), as one upgrade: what 1.0.0 keeps from now
on, what to do first, the commands that now stop where they went on, the figures a re-run
moves (Canada and the brokers' rows, the cents, the US engine), what changes for a script, and
what needs nothing.

#### What 1.0.0 keeps

From 1.0.0 these stay as they are; one that has to change is listed on this page, in the
release that changes it, with what to do:

- the `tjs` commands and their flags ([commands.md](commands.md));
- the `taxjson.toml` keys and the project files ([settings.md](settings.md));
- the `--json` shapes: from 1.0.0 every `--json` output carries a `schema_version`, and a
  shape that changes raises it, with a line here;
- the exit codes ([commands.md](commands.md));
- the files a program or a person keeps reading from one release to the next: the
  `filed/<year>.json` locks; what `tjs form-export` writes (`--out`, `--csv`); the copies in
  the exports folder (`exports_dir`: `<account>_holdings.toml`, `wash_radar_*.json`) and
  `reports/<account>_holdings.toml`, which `tjs sanity` and the next run read back; and the
  reports' file names;
- for a plugin, the names `src/taxjson/pipeline/plugin_api.py` exports.

Not kept:

- report text: the console's and the `.sum` and `.rpt` reports', which are for people and
  may change in any release; a change that moves a figure or asks something of you is listed
  here ([output-style.md](output-style.md#what-a-release-keeps));
- `work/`, the gains files included: internal, and rebuilt by every run;
- the Python import paths inside taxjson, which are internal (the old paths still kept for
  now go after the release);
- the FX-on-cash report: `tjs fx-cash`, the `fx_cash_*` keys, the `.tt` cash lines
  (`FXCONV`, `CASHMOVE`, `CASHOPEN`, `CASHBAL`, `CASHBOOK`) and a lock's `fx_cash_v2` part,
  under both ledgers ([limits.md](limits.md#fx-on-foreign-cash-ledger-v2-limits)).

#### First

**Python 3.12 or newer is required (was 3.10).** This affects an install on Python 3.10 or
3.11, such as the system Python of Ubuntu 22.04 (3.10) or Debian 12 (3.11). There the
installer stops with "Python 3.12 or newer is required" before it changes anything, so the
install stays on its release and keeps working. To upgrade, install Python 3.12 or newer
(macOS: `brew install python`; Ubuntu 24.04 and Debian 13 ship one), put it first on `PATH`
and re-run the installer: it makes its environment (`~/.local/share/taxjson/venv`) again
with the new Python. A developer checkout whose `venv/` an older Python made: remove `venv/`
and re-run `scripts/dev-setup.sh`. Why: Python 3.10 reached end of life in October 2026, and
the fixes to the vulnerabilities reported against the dependency locks (soupsieve) do not
install on Python before 3.11.5.

**Then `tjs run` in every year folder you work in.** Each full run now records which
taxjson built the books, and the parse records the cash events beside them
(`work/<account>_<broker>.cash`). Books another taxjson built, or with no record, are refused:
until a full run of this release, `tjs close-year`, `tjs check-filed`, `tjs form-export`, `tjs
t1135`, `tjs sum`, `tjs estimate` (and `tjs amt` and `tjs instalments` with it), `tjs cash` and
`tjs fx-cash --ledger v2` stop (exit 1) with "the books in work/ were built by another
taxjson" and "Run `tjs run` first." (New stops, below). That first run writes a new record of
what each work/ file was built from (a new format; one an older taxjson wrote vouches for
nothing), and the files it kept beside them go (`work/.wash_inputs.json`, now in each wash
file's record; `.missing_history_applied`, `.project_maps_applied` and the `.deps/*.bump`
files, now declared inputs); the books no longer carry a merge time (`merged_at`,
`mapped_at`).

**Then `tjs check-filed` in each year you filed.** It (or the year's `tjs run`) names each
filed figure this release moves (`filed <year> DRIFTED`); the parts below say which books move
and why. It compares in whole cents, so a cent the new rounding moves (Cents, below) is not
drift; `tjs compare filed` lists it. Whether a moved figure means amending that return is for
the CRA's or the IRS's guidance and a tax professional.

**Going back to v0.41.0.** Pin it ([How to upgrade](#how-to-upgrade): `--channel v0.41.0`).
A lock 1.0.0 wrote only adds keys to the format (its `schema_version` stays 2), so v0.41.0
reads it. v0.41.0 refuses a `[carryover] carried_back` key by name: remove it from
taxjson.toml first.

#### New stops

**The filing and cash commands refuse books older than the inputs.** `tjs close-year`, `tjs
check-filed`, `tjs form-export`, `tjs t1135`, `tjs sum` and `tjs estimate` stop (exit 1; t1135,
sum and estimate with nothing printed) until a full `tjs run` finishes: after an input changed
since the last full run (an export, a .tt file, taxjson.toml, ticker.map), after a `tjs run
--account` that left the pass across your accounts behind, and on books another taxjson built
or with no record (First, above). `tjs amt` and `tjs instalments` build on the estimate and
stop with it ("could not compute the estimate it builds on"). `close-year --force` no longer
locks such books (it still passes the other problems it names). Before, check-filed and
form-export warned, and t1135, sum and estimate printed the books' banner ("these books are not
the clean result of the current inputs") and their figures, t1135 with its cash rows left out
and sum with the line-15300 estimate omitted. A script that read their figures after a scoped
run runs `tjs run` first.

In a US project, a lock in `filed/` removed, replaced or put back since the last full run is a
change too (the gains read the locks); one `tjs close-year` wrote since from the books as they
are, of the year the command reads or a later one, is not, so close-year then check-filed, and
a re-close, work as before. close-year now records the hashes of the books it locks: a lock an
older release closed, put in place after the last run, needs a `tjs run` first.

A `tjs run` that is not a finished full run leaves the books refused. Every `tjs run` sets
`work/.inputs_fingerprint.json` (the last full run's record of what the books were built from)
aside before it writes into work/, and only a full run that finishes writes a record again. So
after a `tjs run --account <name>`, a run `--strict` or a pending election stopped
(`--no-input`, exit 3), or a run that failed, these commands, `tjs cash` and `tjs fx-cash
--ledger v2` stop (exit 1) with "no full `tjs run` recorded what the books in work/ were built
from (work/.inputs_fingerprint.json)" until a full `tjs run` finishes, and the checklist's
run-clean step says "the last `tjs run` did not finish a full run". Before, such a run rewrote
the books and the cash events and left the earlier full run's record: with the inputs put back
as it named them, these commands read what that run had built.

`tjs cash`, `tjs fx-cash --ledger v2` and `tjs t1135`'s cash read the last run's parse. The cash
events (deposits, withdrawals, conversions, interest in a foreign currency) were read from the
exports by each command; the parse now records them beside the books
(`work/<account>_<broker>.cash`), and these commands read that record, as every other command
reads the books. An input changed since the last full run stops them the way close-year is
stopped: "the books in work/ are older than the inputs (changed: ...)"; and a run's parse with
no record of an export group's cash events, "no cash events recorded for the <broker> exports
of taxable account '<account>' (work/<account>_<broker>.cash) — run `tjs run` first". `tjs
t1135` (its cash held outside Canada) and `tjs sum` (line 15300 with the ledger v2) refuse then
too; the checklist's fx-cash step says the ledger v2 line is left out and why. The cost: a full
`tjs run` after every input change before these commands (they used to read the exports as they
were), and a run's parse reads each taxable export's cash events once more (one `.cash` file per
account and broker in work/). There is no switch to read the exports anyway: a ledger from
exports newer than the books could disagree with them
([troubleshooting](troubleshooting.md#the-books-in-work-are-older-than-the-inputs-from-tjs-cash-or-fx-cash-no-cash-events-recorded-from-tjs-t1135-or-tjs-sum)).

**`tjs close-year` is all or nothing.** Its three writes (the inputs frozen into the year's
own `inputs/`, taxjson.toml rewritten to read them; the reports as filed in
`filed/<year>/reports/`; the lock `filed/<year>.json`) are one step, the lock written last. A
close that stops before the lock puts the first two back, all of it even when Ctrl-C is pressed
again meanwhile, and says "Nothing was written": a file it cannot read or write keeps its one
line and exit 2, with "Nothing was written" under it; any other error (a bug) is one `Error:`
saying so, then its traceback, as before. One that stops after it says "<year> is closed:
filed/<year>.json is written, but the report of the close stopped" first. **What it asks of
you:** reports close-year cannot copy (one it cannot read, a `filed/<year>/` it cannot write)
now stop the close, exit 2, with "the reports as filed cannot be kept: …": fix the file or
folder it names, then close again. It was a warning after the lock, and the year closed with
nothing for `tjs compare filed` to diff against. A year an older release left half filed (the
lock, the frozen inputs, no `filed/<year>/reports/`): `tjs close-year --force` in its project
keeps the reports and writes the lock again with the same totals. No figure moves.

**A loss carry-back the ledger can't tell now stops the close (Canada).** In year folders
`tjs carryover` reads the lock your prior year's lock opened from, and each one behind it: a
year closed in its own folder is that record, and a T1A carry-back its return made stays made.
A later year's `carried_back` and carry-forward can move where that year was rebuilt from the
books: compare the later lock with `tjs carryover`, and re-close it only if it differs. A year
with no lock (taxjson started after it, or its lock is gone) is still rebuilt, and its row
meets a `[carryover] claimed` amount from its loss, though its return may have carried back
nothing. Where the project year's carry-back depends on that and no lock settles it,
`tjs carryover` prints the carryforward as "can't tell", `tjs estimate` prints the
carry-forward as unknown and `tjs close-year` stops, naming the year; before, the close went
through on one reading. You are asked only when the year has a loss left to split and its
carry-back depends on the answer. In v1.0.0 to v1.1.0 a balance you typed in
`[estimate] other_losses` was read as a floor (60 carried in after a loss of 100: at least 40
carried back), and where that was all the claim allowed nothing was asked; from the release
after v1.1.0 the balance settles nothing and you are asked wherever this year's carry-back
depends on the answer (see "A typed balance no longer answers what an earlier year carried
back", above). Answer it in taxjson.toml with `[carryover] carried_back = { 2022 = 0 }` (what
that year's T1A carried back, 0 for none), or put that year's close-year lock in the project's
`filed/`, then run the command again. The message gives the exact line for each reading (from
the release after v1.1.0, by the year the loss went back to where that matters:
`carried_back = { 2022 = { 2021 = 40.00 } }`). An entry carried into the next year's
taxjson.toml, once a lock records the answer or nothing rests on it, is an `Info:` note saying
it can be removed, never a stop; one that contradicts a lock or the books, or exceeds the
claims that year could meet, is refused.

**A stale lock behind the year now stops the close (Canada).** When an earlier year was closed
again (`tjs close-year --force`) after the year between, that year's lock still opens with the
old balance. `tjs estimate` then prints the carry-forward as unknown and `tjs close-year`
stops, naming the year to close again first (`tjs close-year --force` in its project); before,
only `tjs carryover` warned and the close went through on the old balance.

**A close stops when the carryover ledger fails, or left a taxable account's book out
(Canada).** For a year with a T1A carry-back in `[carryover] claimed`, a close now stops;
before, it locked the whole loss as carried forward. Only if that was the state when you
closed: compare `carryforwards` in `filed/YYYY.json` with `tjs carryover` (TJ-1422, TJ-1426,
TJ-1429). What a script reads in that state: Scripts and `--json`, below.

**Broker rows that were booked as they stood now stop the run.** A parser booked these rows
with no check. Each refusal names the export, its line and the fix: correct the row from the
broker's statement, or delete it and enter the trade as a `.tt` line, then `tjs run`. A run
that passes gives the figures it gave, but for the fixes under the figures that move, below.

- Webull: a BUY whose Quantity is negative with no cash (it was booked as a buy of the
  magnitude); a share trade at Price 0 carrying Proceeds (booked at price 0, its Proceeds
  unchecked); two $0 rows of one option contract at one moment before its expiry, nothing
  held (each counted the other as held, so both were booked as closes).
- Coinbase: a Buy whose Quantity Transacted is negative with a blank Total (booked as a
  purchase of the magnitude).
- Questrade: a Buy or Sell of shares at Price 0 carrying a Gross (booked at price 0, its
  Gross unchecked; an assignment's stock leg at Price 0 is booked at its cash, as before); a CAD-settled trade (`EXCHANGE RATE` in the Description) whose Net Amount is
  0.00 (booked at a cost of Gross x rate) or whose rate is 0 (booked on the CAD listing at
  the USD price): fix the Net Amount and the rate from the trade confirmation; a Symbol of
  `.TO` alone, or a BRW journal row with a blank Symbol whose pair's other leg names no
  security either (fill in the security's symbol); a
  stock-split row carrying cash (the cash was dropped): make it a split row at Net Amount
  0.00 and a cash row of what the cash is, such as a DIV row.
- Interactive Brokers: a Corporate Actions split whose Proceeds is not 0 (the cash was
  dropped): set the Proceeds to 0 and write the cash as a `.tt` line of its own, the sale of
  the fraction (cash in lieu) or a DIVIDEND, as the message spells out (a charge, Proceeds
  below 0, as what the statement says it paid for); a share leg coded A (an assignment) at
  T. Price 0 and Proceeds 0 (booked at $0 cost): fill them
  from the statement; a section whose header row names no columns (its rows were read as
  blank): download the statement again. A cash takeover whose Proceeds is 0 is still booked
  at the description's rate, now with a warning naming the amount to check. A dividend
  distribution IB's accrual cannot settle: one with shares lent too (a payment in lieu beside
  the rows) that the accrual does not explain, an accrual with no Quantity, or `Po` rows of
  different rates that the cash bears out neither added nor as the last: write each row's
  rate into its Description as IB prints it (`USD 0.10 per Share`) (TJ-2598).
- RBC Direct and Interactive Brokers: a warrant whose root names none of the day's share
  listings (a SPAC's warrant into the new company) pairs only when it is the day's one
  exercise; exercised beside other warrants, it is now refused (RBC) or said with an
  ATTENTION line (IB), where it took the share leg the others left: book it in a `.tt` file
  as the message says (TJ-2547).
- Generic CSV: a futures row that no reading of its amount fits, neither a cost nor a credit
  matching the price's sign with its fee (it was booked at the price-sign reading, a
  notional of the other sign): check the amount, the fee and the price's sign against the
  statement. A trade at a stated price of 0.00 carrying cash is still booked at its amount,
  now with one warning per file naming the count and the first row: blank the price cells
  if the export has no prices. A futures fill at a stated price of 0 (its only cash is its
  commission or a rebate) whose amount is not its fee, with a fee column; with none, any such
  fill in a file of unsigned amounts, a buy too, though its amount was booked as its cost:
  nothing there tells a charge from a rebate. Export the amounts signed, map the fee column,
  or enter the fill as a `.tt` line.

**Typed `.tt` cash and settlement values.** A `.tt` line whose `cash=` amount is no number
(`1.2.3`) is refused now, naming the line: fx-cash skipped that cash. A settled futures line
whose `settle=` value has an empty field (`LO,,100,5,USD`, a comma first or last), a NaN or an
infinity, or a rate of 0 or below is refused too: its fields were read shifted. Write each
amount and field out and
`tjs run` ([troubleshooting](troubleshooting.md#a-tt-line-is-refused-cash-the-cash-the-event-paid-)).
`taxjson-convert-tt` writes none of these. Cash paid after its row's date keeps every digit of
an amount typed past 10 digits, so fx-cash's ledger can move by a cent there.

**A generic mapping finds a header below a title.** A mapping's columns, and a
`[signature]`'s names, are looked for in a file's first 20 rows, not its first line only:
an export with a title, an account line or a blank line above its header is read. If a
`[signature]` of yours (a folder's `generic.toml` or a layout) names the columns of a
supported broker whose export has lines above its header (Webull, Coinbase), that export is
now refused as ambiguous, as one with its header on the first line always was: narrow the
signature, or give the file a sidecar. `tjs redact` now blanks the lines above a generic
export's header.

**Generic exports of several broker accounts are said.** A generic mapping's `account`
column (or its `[broker] account`) naming more than one broker account, in one file or across
an account's files, is now an ATTENTION line: every row is booked to the one taxjson account.
Split the exports into one folder per account, or set `combined_broker_accounts = true` on a
taxable account if one account is meant. Refused now: exports where one account is a
registered plan (a mapped `account_type` of TFSA, RRSP...) in a taxable taxjson account, and
a sheltered account with `combined_broker_accounts = true` whose accounts the `account_type`
column does not name as one plan (map the column, or split the folder). A run that completes
gives the figures it gave.

**Two spellings of one symbol that differ only in letter case stop `tjs run`; one spelling,
in any case, is the upper-case symbol.** Every corporate-action reader now reads a symbol upper
case as the parsers do (the corporate actions kept the export's case: an Interactive Brokers
spin-off of `PARNt` into `SPNCo` was booked under those spellings), and so does a holdings
file `tjs sanity` compares; a broker export's symbol cell is read upper case for every broker
(RBC, Interactive Brokers, Webull and Questrade's fee rows kept the cell's case). Two
spellings (`zyab` and `ZYAB`, in one file, two files or two
accounts, a trade and a corporate action) stop the run with "Error: the exports spell
ZYAB.TO 2 ways, differing only in letter case", until ticker.map says which they are:
`GLOBAL zyab.TO ZYAB.TO` (one security) or, for a lower-case class letter, `DISTINCT
ZYXa.TO ZYXA.TO` (two: booked `ZYX.A.TO`). Such a line, whose symbols differ only in case,
was a no-op note before. One that cannot be kept apart, its lower-case spelling no class
letter (`DISTINCT zyxa.US ZYXA.US`, a no-op note before), is now a ticker.map problem that
stops the run: delete it, or write `GLOBAL zyxa.US ZYXA.US` if the exports spell one
security so. **Who is affected:** a project whose exports have a symbol cell
with a lower-case letter: no broker is known to write one, so an export edited by hand.
**What moves on a re-run:** one spelling, any case: the symbol's name (`zyab.TO` is
`ZYAB.TO`), and the ticker.map lines naming `ZYAB.TO` now reach it. Two spellings, once the
`GLOBAL` line is written: the two pools RBC, Interactive Brokers and Webull kept become one,
so a loss sold in one spelling and bought back in the other within 30 days is denied (an
RBC account that sold `zyab` at a loss of 20 and bought `ZYAB` back: denied 0 before, 20
after; Canada, `CA-SL-01`, a wash sale in the US). A corporate action's event id is
unchanged for exports spelled in upper case. Where it hashes a symbol written in place of an
ISIN (RBC; an Interactive Brokers parent with none), that symbol is spelled as the books
spell it, so an event read from a lower-case cell takes the id of its upper-case spelling,
and overlapping exports in two spellings are one event with one election; an election in
`manifest.json` saved under the old id is carried over to the new one, with a note in the
account's summary ("note: election ... carried over to ...")
([troubleshooting](troubleshooting.md#error-the-exports-spell-zyxato-2-ways-differing-only-in-letter-case-zyxato-in-inputsmarginrbccsv-zyxato-in-inputsmarginrbccsv)).

**A sale at an unknown cost fails the missing-history step.** A sale of units an `OPENING ...
cost=unknown` line opens is out of the totals, to report by hand (CA-ACB-11); `tjs run` now ends
with a `Warning:` naming it (symbol, date, account), and `tjs checklist` fails missing-history on
it (exit 1) where it passed before. Give the OPENING line the cost (`OPENING <date> <SYMBOL> <qty>
<CUR> <total-cost>`) or write the position from a positions report with `tjs opening`, then
`tjs run`; or, to keep reporting the sale by hand, `tjs checklist --done missing-history` (the
mark answers the sales it saw; a new one asks again). No figure moves; `tjs compare filed` lists
`run_summary.json` as changed on a year closed before the upgrade, for its new
`unknown_cost_sales` key
([troubleshooting](troubleshooting.md#tjs-run-ends-with-warning-not-in-the-totals-1-sale-at-an-unknown-cost-qzqto-2025-06-02-in-margin--add-the-cost-to-its-opening-line-and-tjs-checklist-fails-missing-history-on-a-sale-an-opening--costunknown-line-covers-an-older-release-said-nothing-and-the-step-passed)).

**A sale at an unknown cost also stops a close that splits a T1A carry-back (Canada).** For a
sale with an unknown cost (`OPENING ... cost=unknown`) after the last locked year, up to the
project year, in a year with a loss or a carry-back and a T1A carry-back claim in `[carryover]
claimed` (TJ-1436), `tjs close-year` now stops, naming the sales: the split rests on a ledger
that leaves them out. Give them a cost, or pass `--force` (or answer yes on a terminal) to lock
the split as computed. The lock then records `carry_back_incomplete`, which next year's `tjs
handoff` reports until you re-close. `tjs estimate` keeps its figures, marks them and warns,
the reason now on its own line; its `--json` adds `carry_back_incomplete`. `tjs checklist`'s
carryover step asks for attention, so a plain `close-year` lists it. With no claim nothing
stops. A lock written before this: it may be off by the claim; compare `carryforwards` in
`filed/YYYY.json` once the sale's result is known.

**`tjs t1135` gives no verdict while a holding's ISIN and listing could reverse it, or while a
holding's cost is unknown (Canada).** If your books hold a symbol that `tjs t1135` already
warned about ("N symbol(s) on a Canadian listing carry a foreign ISIN", or "... on a foreign
listing carry a Canadian ISIN"), and counting it the other way would move the total across
the threshold, the verdict line no longer says "no T1135 required" or "FILING REQUIRED": it
reads `=> NOT RESOLVED: below (or over) the ... threshold as counted, but SYMBOL may (not) be
foreign property ...` and gives the two `T1135` lines that settle it, and the checklist's
t1135 step needs attention until then. No cost figure moves. **What to do:** add the line
that applies for each symbol named to ticker.map, as the report names it (after a ticker
change, the line on the holding's first symbol, which covers the whole year), then
`tjs run` and `tjs t1135`. Which line applies is a question about the issuer's residence,
for the CRA's T1135 guidance or a tax professional. Where counting it the other way cannot
change the result, the verdict stays, with a line under it: "Counting SYMBOL the other way
(ISIN and listing disagree) does not change this"
([troubleshooting](troubleshooting.md#tjs-t1135--not-resolved-below-the--threshold-as-counted-but-fgnto-may-be-foreign-property-a-foreign-isin-on-a-canadian-listing-with-it-the-maximum-is--or-over--as-counted-but-qzwus-may-not-be-foreign-property-tjs-checklist-the-t1135-test-is-not-resolved)).

Below the threshold with a foreign property at an unknown ACB (an `OPENING ... cost=unknown`
line: shares bought before your files begin), the verdict read "no T1135 required" beside a
`! SYMBOL: unknown ACB, cost understated` line; it now reads `=> NOT SETTLED: below the ...
threshold, but the cost of SYMBOL is unknown — add its purchase ...`, and the checklist's
t1135 step needs attention. **What to do:** add the purchase (an older export, or an OPENING
line with its cost; `tjs find-missing-history` names them), then `tjs run` and `tjs t1135`
([troubleshooting](troubleshooting.md#tjs-t1135--not-settled-below-the--threshold-but-the-cost-of-fgnto-is-unknown--add-its-purchase-an-older-export-or-an-opening-line-with-its-cost-tjs-find-missing-history-older-releases-no-t1135-required-beside--fgnto-unknown-acb-cost-understated)).

Where a filing is required but the part is not decided (the year still open, or a cost
unknown, with the maximum under the detailed threshold; or a holding counted the other way
would take it across), the verdict line reads "Part A or Part B open (...)" with the reason,
where it read "simplified method (Part A)": the `tjs init --demo` project's reads "Part A or
Part B open (some cash's cost is unknown)". Nothing to do.

A foreign listing whose dividend or return-of-capital rows carry another foreign country's
ISIN (an Irish issuer's shares on a US exchange) is named in a new warning, `1 symbol(s)
counted under one foreign country carry another's ISIN: FGN.US (IRL, counted under USA)`,
and a `! ` line, and listed in `--json` `review_symbols`. No figure or verdict moves. **What
to do:** check the issuer's country and add `T1135 SYMBOL COUNTRY` to ticker.map: the
issuer's country, or the one it is counted under to keep the row where it is (either line
ends the warning; `tjs t1135 --details` names both), then `tjs run` and `tjs t1135`
([troubleshooting](troubleshooting.md#tjs-t1135-warning-1-symbols-counted-under-one-foreign-country-carry-anothers-isin-fgnus-irl-counted-under-usa-and--1-symbols-with-another-countrys-isin-than-counted-fgnus-irl)).

**`tjs run --fast` is gone: every `tjs run` rebuilds everything.** A script that passes
`--fast` now stops with `Error: unrecognized arguments: --fast` (exit 2): drop the flag.

#### Canadian figures that move

**Canada: a listing journal that left a millionth of a share or less.** A broker's journal
between two listings of one security (and, in one account's own figures, a move to another of
your accounts) that left a rest of a millionth of a share or less cleared it whatever it cost;
its cost was stranded ("pool is EMPTY but carries ... of stranded basis") and the sale of the
rest booked no gain. The rest is now held when it costs half a cent or more, and its sale is
realized. A filed figure moves only for such a rest that was then sold: a re-run of that year
books the gain or loss its sale left out. To check a year: `tjs run` said the warning only
while the rest was unsold; once it was sold there was no warning, and `tjs list` showed a tiny
negative position of the symbol (under a millionth of a share, a short nobody opened) after
the journal or the move. With neither, nothing to do.

**Canada: a holding a rename or a listing journal merged shows the later of the two
purchases.** When a ticker change brought shares into a ticker you already held or had bought
and sold (a `.tt` RENAME, a broker's rename row, a SPLIT naming a new symbol), the merged
holding kept the EARLIER of the two holdings' last purchases, and of their opening lots'
dates (onto a ticker sold out, the shares brought in kept their own dates whole); it now
keeps the later, as one holding bought twice does (CA-ACB-RENAME, CA-OPEN-01). The same for
a journal between two listings, in the native-currency books (holdings, `tjs sanity`;
CA-XLIST-01). A re-run moves, for a sale of such a holding, its days held (fewer) and
Schedule 3's year of acquisition (`tjs form-export`: later), and the date `tjs harvest`'s
TX_ADD and the planning views' 30-day window count from: a holding bought into less than 30
days ago, or renamed onto a ticker bought less than 30 days ago and sold, no longer reads as
clear. No gain, cost or denial moves, and nothing moves for a rename onto a ticker you never
held: the source's dates carry, as before. To check a year: `tjs renames` lists each ticker
change; one into a ticker you also held, or had bought, before its date is such a merge.
With none, and no journal between two listings, nothing to do.

**Canada: a denied loss waiting for a position that a journal or a move in opened.** A
superficial loss denied with no purchase left to carry it waits for the position the symbol
next opens. When that position was opened by a listing journal from another listing of the
security, or (in one account's own figures) by a move in from another of your accounts, the
waiting amount was never added: the later sale's cost lacked it, although its record said the
denied loss was recovered. It is now added, as for a purchase. A filed figure moves only for
such a sale: a re-run of that year lowers its gain (or raises its loss) by the denied amount.
To check a year: a denial in `tjs run`'s list whose replacement was sold again within the 30
days, followed by a journal or a move in of that symbol while none was held. With no such
sequence, nothing to do.

**Canada: a denied loss waiting while a journal or a move out left the symbol short.** A
superficial loss's addition that waits on the symbol's pool now joins only a long position: a
short is not the shares held at day 30 (CA-SL-07, CA-SL-19). When, while the amount waited on a
flat pool, a listing journal or a move of your own took out units the books never bought (the
pool short by them, at an unknown cost), the row that later covered that short and opened the
long position lost the denied amount: v0.41.0 added it to no position. It is now added to the
long position that row opens, so a re-run lowers the gain (or raises the loss) of that
position's later sale by the denied amount; the covering row keeps its own gain or loss. To
check a year: a denial in `tjs run`'s list whose replacement was sold again within the 30 days,
then a journal or a move out of that symbol while none was held. With no such sequence, nothing
to do. A denied loss still waiting when the books end is now said: `Warning: OLD.TO: a denied
superficial loss of 1000.00 still waits for a long position at the end of the books: no ACB
holds it`
([troubleshooting](troubleshooting.md#canada-warning-oldto-a-denied-superficial-loss-of-100000-still-waits-for-a-long-position-at-the-end-of-the-books-no-acb-holds-it)).

**Shares of a rename's target bought before the rename no longer deny a loss on the old
ticker (Canada).** When a ticker change or merger brought OLD into NEW, NEW's own shares
bought before the rename counted as identical property to OLD, so a NEW purchase in the
window of an OLD loss denied it, and the loss came back on those shares' sale. The rule is
now (CA-ACB-RENAME): NEW's own trades dated more than a week before the rename are another
security (a buyer would prefer one over the other before the merger),
while a NEW trade within that week (a broker booking the new ticker early), and every trade
after the rename, is the same security as OLD. The other way round, a loss on NEW's own
shares more than a week before the rename is no longer denied by OLD's shares the rename
brings into NEW. Re-run such a book after the upgrade: the loss is allowed in its own year
and the later sale realizes its paid cost alone, so the figures of both years can change;
`tjs wash-sales --explain` shows what the denial pass took as the replacement
([troubleshooting](troubleshooting.md#canada-a-loss-on-a-ticker-later-renamed-or-merged-into-another-is-denied-as-superficial-by-shares-of-the-target-bought-before-the-rename)).

**Canada: each account's written options are its own position.** An option series had one
pool across your taxable accounts, so a contract bought in one account closed the same series
written in another, and a write sold a contract another account held. The written contracts
are the writing account's own obligation now (`CA-OPT-12`): only its buy-back, assignment or
expiry closes them, at its own premium (a move of written contracts between two of your
accounts carries them to the receiving account from the first moment of the move's day at
which the sender holds them, the sender read by how the broker signs its transfer rows, and a
move of bought contracts leaves the sender at that moment too; a `Warning` names a move that
reads more than one way); a purchase in another account is a contract
bought, pooled with the other accounts' bought contracts; a sale past the account's own bought
contracts writes, and a row that does both is two parts. For a book that buys a series in one
account and writes it in another the figures move: the buy-back booked in the buying account
is gone, each account's contracts stay open until it closes them, the bought contracts count
as held at day 30 for the superficial-loss rule (a loss they back is denied, its amount added
to their ACB), and `tjs list` shows each account's position. An assignment on more contracts
than the account wrote (an expiry row past them, or an exercise past the contracts it bought,
alike) closes only its own and opens nothing: the other account's contracts stay open for its
own close, the assigned shares carry this account's premium alone, and a loss the contracts
bought at 0 denied is allowed, with a `Warning` (the files hold more assignments than writes). A move between your accounts that no reading fits
is booked as bought contracts leaving the account whose row is negative in every convention
(Questrade's rows gave a written one), and a move reads the sender's trades dated before it
first, whatever their settlement: the figures of such books move too. Re-run `tjs run`; on a closed
year `tjs check-filed` can show the change as drift
([troubleshooting](troubleshooting.md#canada-an-option-bought-in-one-account-closes-the-same-series-written-in-another-a-write-in-one-account-sells-the-contract-another-account-holds)).

**A denied loss beside a marked short of the same stock (Canada).** When a loss was denied
as superficial, its replacement was a holding the symbol had not opened yet when the denial
was placed (shares of the ticker a later rename or merger brings the loss symbol into, bought
in the window), and a short the broker marks (RBC "SHORT.", an IB sale coded O) of that
symbol was open at the time, in any of your accounts, the denied amount lowered the short's
proceeds instead of waiting for the replacement: the replacement's sale showed its gain at
the paid cost alone and the cover showed the denied loss in its year. Re-run such a book
after the upgrade: the denied amount is in the replacement's ACB and comes back when it is
sold, and the cover's gain or loss is the short's own, so the figures of both years can
change. A book with no marked short, or whose replacement's pool was open when the loss was
denied, is unchanged
([troubleshooting](troubleshooting.md#canada-a-loss-denied-as-superficial-comes-back-in-the-year-a-marked-short-of-the-same-stock-was-covered-not-when-the-replacement-shares-were-sold)).

**A cost adjustment entered beside another account's marked short (Canada).** When an
ADJUST (a `.tt` line, a stock dividend's declared amount, a fund's `[[distributions]]` entry)
in an account that holds no short of the symbol was dated while a short of that symbol the
broker marks (RBC "SHORT.", an IB sale coded O) in another account was the symbol's only
open position, it was booked as that short's compensation payment: the cover's gain changed
by the amount and the shares the ADJUST was for were sold at their paid cost alone. Re-run
such a book after the upgrade: the ADJUST stays on the symbol's pool (the run warns that it
hits an empty pool, as for any adjustment dated when no shares are held: re-date it onto the
shares it belongs to if that is not what you meant) and the cover's gain or loss is the
short's own, so both figures change. An ADJUST in the account that holds the short is
unchanged
([troubleshooting](troubleshooting.md#canada-a-cost-adjustment-entered-in-one-account-is-booked-as-a-short-sellers-compensation-payment-on-a-marked-short-in-another-account)).

**A denied loss whose rebuy was sold again inside the window (Canada).** When a loss was
denied as superficial, the first purchase after the sale (shares, or a call on them) was sold
again before day 30, and the position held at day 30 came later in the window (bought, or
brought in by a rename or merger), the denied amount was added to the rebuy's ACB and came back
on its sale, which was listed as a second superficial loss and denied again, and so on down the
window. Re-run such a book after the upgrade: the sold-out rebuy is no replacement; the denied
amount is in the ACB of the purchases held at day 30, each up to what it opened in the order
of CA-SL-10, and comes back when they are sold (`tjs wash-sales` names them; when no purchase
opened the held position, the shares coming by a rename or merger, the addition waits from the
sell-out for them). When a registered or affiliated account bought in the window before the
taxable purchase held at day 30, that purchase now comes first in the order and the denial is
lost for good where it was deferred onto the taxable shares. When the position held at day 30
was opened by an opening balance (missing history) after the sell-out, the denied amount now
joins that position's cost and comes back when it is sold, where it was lost before (the
later sale then realized the whole proceeds against a cost of nil); the rebuy's sale shows
its own gain or loss, or its own smaller loss, and the total denied falls by the chained amounts
while the ACB of the shares held is the same. The two years' total is unchanged; a gain can move
from one year to the other (a round trip inside the window that showed 0 now shows its gain, and
the later sale a larger loss). A book whose rebuy was held through day 30 is unchanged
([troubleshooting](troubleshooting.md#canada-a-loss-denied-as-superficial-comes-back-on-the-rebuy-sold-again-inside-the-window-which-is-listed-as-a-second-superficial-loss-instead-of-on-the-shares-held-at-day-30)).

**A sale filled in parts, replaced in a taxable and a registered account (Canada).** When one
sale was filled at different prices (one account, one day) and its replacement shares were held
at day 30 partly in a taxable account and partly in a registered one (or by an affiliated
person), the part of the loss deferred into the taxable replacement's ACB and the part lost for
good depended on which fill the export listed first. Re-run such a book after the upgrade: the
sale's denied units are drawn on its replacements once, in acquisition order against the sale's
last fill (a purchase between two fills counts as before the sale), each holder up to the units
it holds at day 30, and each fill takes its units' share, in either order, so the replacement's
ACB and the amount lost for good can both change; the sale's moment in that order is its last
losing fill. A sale with one fill is unchanged; one in fills with two replacements and a sale
between them moves a little in taxable accounts too (each fill takes its units' share of both
replacements), across a year end when the replacements straddle it
([troubleshooting](troubleshooting.md#canada-a-sale-in-two-fills-replaced-in-a-taxable-account-and-a-registered-one-defers-more-or-less-of-its-loss-depending-on-which-fill-the-export-lists-first)).

**Two puts assigned at one moment, delivered in two rows (Canada).** When two put assignments
at one moment delivered their shares in two marked rows with a sale between them, or in two rows
of unequal size, one put's premium could lower the cost of the shares the other put delivered,
and a delivery could carry no premium at all. Re-run such a book after the upgrade: each premium
lowers the cost of its own assignment's shares, so the sale between the deliveries and the ACB
of the shares held change (their total does not). A single assignment's delivery is unchanged
([troubleshooting](troubleshooting.md#canada-two-puts-assigned-at-one-moment-deliver-their-shares-in-two-rows-and-the-premium-of-one-put-lowers-the-cost-of-the-shares-the-other-delivered-a-delivery-row-with-no-premium-at-all)).
In the same book, a delivery row that is the delivered quantity of neither put (two rows of
150 for a 2-contract and a 1-contract put), or of both (two 1-contract puts delivered as two
100-share rows), belonged to the put whose row was nearer: it is now theirs pro rata by
contracts (each 150 is 100 of the one and 50 of the other; each 100 is half each), in whole
shares, so the figures are the same in whatever order the export lists that day's option rows
and marked delivery rows. Every other assignment (a put at another strike, an exercised call)
pairs as before; the tied puts then share what the deliveries have left, a delivery at the
strike first, then a marked one, then one of exactly a put's quantity, the nearest day first.
Re-run such a book: the sale between the deliveries and the ACB held move, their total
does not. Where several rows are exactly one of the puts' quantity (two 100-share rows and a
200-share row for a 1-contract and a 2-contract put), the first in that order is that put's;
it was the row nearest its option row, so a sale between the rows moves by the premiums'
difference when the export listed another row nearer. Unchanged: puts at different strikes,
an exercise beside an assignment, a plain purchase at the strike listed before its option row,
and a US project
([troubleshooting](troubleshooting.md#canada-two-same-strike-puts-assigned-at-one-moment-share-a-delivery-and-the-sale-between-the-deliveries-or-the-acb-held-changes-when-the-two-option-rows-are-listed-the-other-way-round)).

**An assigned option's row was walked twice (Canada).** When an option's stock leg is dated
before the option's ASSIGN row and that leg's loss was a superficial loss, the solver's later
passes walked the option row twice: the inventory showed the option still open, and the
gains could be off by the phantom close. Re-run such a book after the upgrade: the inventory
is flat, and the figures of that year can change. Other books are unchanged
([troubleshooting](troubleshooting.md#the-inventory-shows-an-assigned-option-still-open-quantity-1-or--1-when-its-stock-legs-loss-was-a-superficial-loss)).

- A Canadian loss where one of your accounts (taxable, registered or affiliated) holds a call
  on the shares as an opening balance (an `OPENING` line) dated the day of a split on the
  underlying (TJ-1957): the quantity denied and `total_disallowed` can move by the split
  ratio. The opening balance is read in the day's opening units, as a share row was. Re-run;
  if that year is closed, compare its lock with the new figure.

##### Carry-forwards and T1A carry-backs (Canada)

These move `tjs carryover`'s rows, `tjs estimate`'s figures or a new close's split in the
books each names; a lock already written changes only if you close that year again.

- `tjs carryover` on a closed year with a carried-in balance and a `[carryover] claimed` line under
  an earlier year (TJ-1495, TJ-1547). The row's `carried_back` and `claimed_applied` can move to the
  lock's figures, and `carried_back_to` with them; a script reading `--json` may see `unmatched_claims`,
  `claims_waiting` and `claims_beyond` where it saw none. The lock was right, and that year's return
  doesn't change.
- A year after a closed one with its own `[carryover] claimed` entry, inside the window of a claim
  (TJ-1547, TJ-1558, TJ-1565): `tjs estimate`'s `losses_carried_back`, `tjs carryover`'s rows and a new
  close's split can move. An entry is met by what its lock applied, and only a later year's loss can
  meet the rest. Holding every earlier lock in `filed/`, a loss carried back too much, so a lock's
  carry-forward was too low. With an entry above what the lock applied, the rest could be lost, so it was
  too high. If you closed such a year, compare its lock with `tjs carryover`, and re-close only if it
  differs.
- Year folders, with a T1A claim the prior year's lock didn't use up and a later year's loss inside its
  window (TJ-1554): `tjs estimate`'s `losses_carried_back`, `tjs carryover`'s rows and a new close's
  split can move, with or without a year's own entry. The carry-forward falls, and the row before the
  prior lock now shows no carry-back the lock doesn't support. If you closed such a year, compare its
  lock's `carried_back` and `closing` with `tjs carryover`, and re-close only if they differ: what you
  carried forward may also be on your T1A. Where the figure rests on what an earlier return carried
  back, the close stops (the carry-back "can't tell", under New stops).
- A return filed with another tool (`tjs close-year --filed-dispositions`) whose loss is above the
  books', or that the books don't show, with a T1A carry-back claim in `[carryover] claimed`
  (TJ-1452). The lock kept the claim's rest in its carry-forward. If you closed in that state, re-close
  with `--force`: `carried_back` and `closing` move. Compare them with what you filed. `tjs carryover`
  now folds a closed year on the gain its lock records as filed, so its row for that year can change
  (TJ-1473, TJ-1485). `tjs estimate` stays on the books; when they differ from the lock it adds
  `Locked for YEAR` (`--json`: `locked_split`) (TJ-1489).
- `tjs close-year` (TJ-1413): only if you pressed Ctrl-C during a close that went on
  anyway, for a year with a T1A carry-back in `[carryover] claimed`. That lock may carry
  the carried-back loss forward too: compare `carryforwards` in `filed/YYYY.json` with
  `tjs carryover`.

##### The brokers' rows and ticker.map (either country)

Each moves figures only in the books it names, in a Canadian or a US project.

**Interactive Brokers: an ordinary dividend and a return of capital (or a capital gain) of
one distribution show the shares held, and `tjs div-estimate` moves.** Where not every row
states a per-share rate and IB accrued the payment in its own currency, each row was at its
amount divided by the accrual's whole rate (10.00 and 30.00 of 0.40 a share on 100 shares:
25 and 75 shares at 0.4). A re-run gives each the shares it was paid on and its own rate
(100 at 0.1, 100 at 0.3) in `tjs divs`, and the rows' ids change. No booked amount moves
(income, cost, gains, slips). `tjs div-estimate` reads the share counts, so its figures for
such a security change to the right ones (its last amount per share, and the 12-month
estimate with it). In a statement of two accounts, each
account's rate-less dividend rows now take that account's accrual (the second account's
could show the first one's shares). Where IB accrued a `Po` row for each character, a row
that is the whole payment is at those rows' rates added, where the cash or the rows'
stated rates bear that out (it was at the last row's: 40.00 of 0.10 and 0.30 a share
showed 133.33 shares at 0.3, now 100 at 0.4). A dividend row
posted after the pay date that the accrual does not account for (the next payment of a
weekly payer, its own accrual in no statement) has no share count, where it showed a
wrong one: the row says so (`no_share_count`), `tjs div-estimate` leaves it out of the
per-share figures and says so under the position (it does not borrow the nearest
payment's count for such a row), and `tjs divs` shows 0 in its text listing: download the
statement that holds its accrual. A distribution the accrual cannot settle now stops the
run (New stops, above; TJ-2598; docs/troubleshooting.md).

**Generic CSV: a futures fill at a stated price of 0 books its amount as its commission or
rebate.** Its notional is 0, so its only cash is the commission (or a rebate). The price's
sign was read as the cash's, so a sale's 2.50 commission was booked as 2.50 of proceeds
received: the close's loss was twice the commission too small. A re-run changes those
contracts' gains (each by twice the commission) and the notional shown for that fill (now
0). With a fee column, the fee is the cash; with none, a negative amount is a charge and a
positive one a rebate in a file whose buys show their cost negative. A fill nothing there
tells apart is refused (New stops, above).

**Questrade: a split after an option assignment or exercise, or after a currency journal,
gets its ratio from the right side.** The split's ratio is read against the position, and an
assignment's or exercise's stock leg, or a BRW journal leg, that carried the holding across
zero was not counted: a short of 50 assigned 100 shares, then split 25 on 50 shares, was
booked as `SPLIT` 0.5 (it is 1.5), with no warning, and 50 on 50 was not booked. A re-run
changes that security's share count after the split and the gains on later sales; `tjs
events` shows the new ratio.

**Questrade files written by `tjs fetch`: a trade held twice, the second row with a blank
Symbol.** Until this release a re-fetched activity whose Symbol came back blank was added as
a new row. For an option both rows were booked: twice the contracts, twice the premium and
cash, and in a taxable account twice the gain or loss. **What to do,** once per Questrade
account you fetch: run `tjs fetch` over the years you fetched before (`tjs fetch --year N`
for a past year), then `tjs run`. A fetch that finds such rows names them
(`questrade_<year>.csv: dropped N rows with a blank Symbol ...`) and keeps the file as it was
beside it (`questrade_<year>.csv.bak`); those trades are then booked once, so the account's
positions, its cash and the year's gains move. If you cannot fetch those dates again, `tjs
run` names the pairs' lines in a `Warning:` at its end: check the broker's statement, delete
the blank row when it shows one trade, keep both when it shows two. Then check the account
with `tjs sanity` against the broker's positions. A year you closed from such a file may hold
the doubled trade: `tjs check-filed` lists what moved; what to do about a filed return is a
question for the CRA's or the IRS's guidance and a tax professional. Nothing changes for a
file with no such pair
([troubleshooting](troubleshooting.md#tjs-run-questrade-warning-margin-questrade_2025csv-lines-12-and-14-differ-only-by-a-blank-symbol-probably-one-trade-fetched-twice-the-account-shows-twice-the-option-contracts-it-holds)).

**An `EXTRACT` symbol in lower case, or on a Canadian venue other than `.TO`, joins the
broker's pool.** An `EXTRACT` line's symbol (after the second `|`) was kept as typed, so
`EXTRACT old sample holdings | CAD | zyab.TO` booked the rows it matched under `zyab.TO`,
and `... | CAD | ZYAB.V` under `ZYAB.V`: each an ACB pool of its own beside the broker's
`ZYAB.TO`. The symbol is read upper case now, as every other ticker.map symbol always was,
and a Canadian venue is spelled as every parser and `.tt` row spells it: `ROOT.TO` for a
`.V` on a CAD line and for `.CN`, `.NE` and `.VN` on any line, a preferred series dotted
(`ZYPF.PRA.TO` is `ZYPF.PR.A.TO`). A `.V` on a line for another currency or any (`*`) may be
a class letter: `tjs run` refuses it ("Error: 1 ticker.map problem(s)"), naming the line to
write.
**Who is affected:** a project whose ticker.map has such a line: `grep -in '^ *extract'
ticker.map` (the keyword is read in any case) and look at the last field for a lower-case
letter, a `.V`, `.CN`, `.NE` or `.VN` suffix, or an undotted preferred series. **What moves
on a re-run:** the two pools become one, so the holding's ACB per unit is the average of
both; a loss sold from one pool with the shares bought back in the other within 30 days is
now denied (Canada, `CA-SL-01`; in a US project a wash sale), its amount added to the
repurchase's cost, so that year's net gain rises and a later sale's falls. The lines that
name the books' spelling now apply to those rows too: a `GLOBAL` or `TOBASE` rename of
`ZYAB.TO`, its `SIMILAR` group (the run's warning and the planning tools' caveat), its
`T1135` country, its `QUOTE` price lookup. A report, `tjs list` and the
holdings check show `ZYAB.TO` alone. Each matched row's id changes (a row's id is a hash of
its fields, the symbol among them), so a row id you noted (`tjs audit --id`) names nothing
after the re-run; no saved decision is kept by row id: the corporate-action elections
(`manifest.json`) are keyed by the event (its ISINs, date, kind and ratio), a crypto send's decision
(`sends.json`) by its exchange, time, coin (in upper case) and quantity, an `ALLOWLOSS` line
by its date and symbol (read upper case). A year already filed with such a line: `tjs
check-filed` lists what moved
([troubleshooting](troubleshooting.md#a-security-shows-twice-once-in-lower-case-zyabto-beside-zyabto-and-a-loss-sold-from-one-is-allowed-although-the-other-was-bought-back-within-30-days)).

**A ticker.map line naming a Canadian listing on another venue names the books' `ROOT.TO`.**
A `QUOTE`, `T1135`, `SIMILAR`, `GLOBAL`, `TOBASE`, `JOURNAL`, `RENAME`, `DISTINCT` or `DELETE`
line naming `ZYAB.NE`, `.CN` or `.VN`, or an undotted preferred series (`ZYPF.PRA.TO`), is read
as every parser books it (`ZYAB.TO`, `ZYPF.PR.A.TO`), as an `EXTRACT` target now is (above).
**Who is affected:** `grep -inE '\.(ne|cn|vn|v)\b|\.pr[a-z]\.' ticker.map`. **What moves on a
re-run:** such a line named a pool only an `EXTRACT` line of that spelling booked; now it
applies to the books' `ZYAB.TO`: a `SIMILAR` group warns about its losses, a `QUOTE` prices
it, a `T1135` line classes it, a rename moves its rows; a rename to `ZYAB.NE` (`TOBASE ZYAB.US
ZYAB.NE`) books into `ZYAB.TO`, the pool of the broker's own rows, which `tjs tips` now names.
A `.V` outside a CAD `EXTRACT` line
may be a class letter and is read as written; where the books hold `ZYAB.TO` and not
`ZYAB.V`, `tjs run` warns that the line does nothing for it
([troubleshooting](troubleshooting.md#warning-tickermap2-similar-zyabv-zyqqto-names-zyabv-a-symbol-the-books-do-not-hold-they-spell-a-tsx-venture-listing-zyabto)).

**Wealthsimple exports are read.** A CSV with Wealthsimple's activities columns
(`activity_type`, `activity_sub_type`, `net_cash_amount`) is now read by a layout taxjson
ships, under any file name. Your own mapping still wins: a sidecar, a `generic_` file with
the folder's `generic.toml`, or a layout of yours that fits. The layout is unverified
against a real export, and says so: every `tjs run` that reads it warns once for each
account, again at the run's end. Compare the account's totals with your statements and
report a difference with the header row and made-up rows, never real ones
([brokers.md](brokers.md#wealthsimple)).

- Interactive Brokers, a combined statement whose two accounts each cancelled (`Ca`) an alike
  trade of an earlier download (TJ-2518): the second account's cancelled trade is no longer
  booked. Run `tjs run`; if that year is closed, `tjs check-filed` names the drift.
- Interactive Brokers: two warrants (or rights) exercised the same day in one currency, with one
  share leg in the statement, now pair by the warrant's root: the share leg goes to its own
  warrant, not to the one listed first (TJ-2537). If the statement held that shape, the first run
  after the upgrade moves the warrant's cost into the right shares' cost, and the ATTENTION line
  names the other warrant. A warrant whose root starts two share listings (`ABCD.WT` with ABC and
  ABCD exercised that day) pairs with the longer, ABCD (TJ-2538): its cost goes into ABCD's shares
  where it was booked as a disposal at 0 with an ATTENTION line, and two blocks of one warrant
  into one listing both pair (TJ-2550). Run `tjs run` and read the parse notes (`warrant ...
  exercised into ...`). RBC Direct refused the file in these shapes: the first now names the
  warrant with no share leg, the others are booked. One shape is now refused by RBC and said
  by IB (New stops, above; TJ-2547).
- A Kraken receipt shared by several fiat legs without `amountusd` (a ledger refid spending, say,
  CAD and USD for one coin) is split by each leg's value through the run's rates, and the parse
  now declares the rates (TJ-2497). If a v0.41.0 `tjs run --fast` kept an older split after a rates
  change (a rate first fetched for the day, the rates refreshed), the first run after the
  upgrade re-splits it: the coin's lots move between the legs (the quantities, and each lot's
  basis in the US engine; the Canadian average-cost pool is the same). Nothing to do but read
  the parse note (`split by amountusd`). From then on every run parses with the rates as they
  are (`--fast` is gone; TJ-2508), and a stablecoin's de-peg note or ATTENTION follows them.
- An Interactive Brokers split whose cash in lieu IB cancelled (`Ca`) in one download of the account
  (TJ-2463, TJ-2471): with no copy of the row left, the books keep one copy of the split at the
  ratio of the shares delivered (they kept two when another download held the split, one taking
  the cancelled fraction); with IB's rebook of the row in the next statement, the split takes the
  rebooked fraction, so the shares after it come out whole (they were short by the fraction). The
  position after the split and its ACB per share can move. Re-run; if that year is closed, compare
  its lock with the new figure.
- An Interactive Brokers option whose contract a corporate action re-listed under a
  six-character root, IB listing one contract id under `ABCDE1` beside `ABCDE` (TJ-2376): its
  opening and its closing or assigned legs are booked under one symbol; they were two, one
  opened and never closed and one closed and never opened. The option's gain, the ACB of
  shares an assignment delivered and a denial on either can move. Re-run; if that year is
  closed, compare its lock with the new figure.
- A Kraken coin-to-coin trade (TJ-2375): the quantity of the coin you paid or received on the
  quote side of the pair is computed on the export's decimal digits, so a figure that showed a
  binary tail (`0.0049900000000000005`) is the export's `0.00499`. That coin's ACB per unit
  can move in its last digits; nothing else does.

#### Cents

**Reports round half up from the figure as taxjson holds it.** Every money figure a command
prints or writes is cut half up now, as the gains files and the forms' cells already were,
not half to even on the binary float (Python's `round()`): a figure read from the gains file
from its four places there (`work/<account>_gains.json`), a figure a report computes from
the shortest decimal of its float. A figure on a half at its last place moves one unit
further from zero on a re-run (a gain of 101.925 is 101.93, not 101.92; a loss of -101.925
is -101.93), a total that adds such figures moves with them, and a figure derived from one
moves at its own last place, even when the total it comes from does not move (a cost per
share of 33.20865 is 33.2087 at four places, with the total cost as it was); nothing else
moves. A figure the gains file holds with a 5 in its third decimal moves the same way. A
total a report adds in float can still sit a cent from its parts, as such a total could
before the upgrade (a `.sum` row's TOTAL_GAIN against its CAP_GAIN + OPT_GAIN), and `tjs
stats`' averages are means of P/Ls already cut to the cent: this release leaves both as they
were.

**What it asks of you.** `tjs compare` against a snapshot saved before the upgrade lists
each moved figure as a change; re-save it with `tjs compare --save NAME`. `tjs compare
filed` on a year closed before the upgrade lists the same cents, the ones `tjs check-filed`
and the next year's carry-in checks pass (they compare in whole cents); `tjs close-year
--force` on such a year can store a closing loss balance a cent from the one the next year
opened from, and names each cent its replaced lock's totals moved. A `.tt` or taxjson.toml
line `tjs slip-audit` suggests can differ by a cent from one it suggested before the
upgrade. A crypto send's fair value can sit a cent above the proceeds of the `.tt` line `tjs
crypto-sends` suggests (the line's figure is as before). `tjs instalments` can move a cent
to a quarter from the one after it, the year's total unchanged, so a quarter paid to the
cent before the upgrade can read MISSED or LATE after it ("Behind by 0.01"). A verdict read
off a printed figure turns with it at a half: `tjs slip-audit`'s ok or differs, `tjs
losers`' and `tjs winners`' sides, a stablecoin send's loss, `tjs instalments`' status, `tjs
watch`'s threshold; `tjs watch --threshold 0` can record one change of a cent on its first
run after the upgrade, with no price moved
([troubleshooting](troubleshooting.md#after-an-upgrade-a-figure-is-a-cent-further-from-zero-than-the-last-version-printed)).

**A row's rounded parts may not add to its rounded whole.** Each figure is cut on its own,
so where two parts of one figure each end in a half cent, both are cut away from zero:
in `tjs wash-sales`, a loss of 407.70 split into 305.775 denied and 101.925 allowed shows
DENIED 305.78 and ALLOWED -101.93, a cent more than the loss. The books hold the parts
exactly; nothing to do.

**The engines compute money exactly.** The gains engines add, divide and compare money as
exact decimals now, not binary floats, and round a figure half up from its exact value. A
figure whose exact value is a half at its 5th decimal can move by 0.0001 on a re-run (the demo
book's gain of 91.2262 is 91.2263), in the gains files and in the trace columns, an inventory
row's `deferred_wash` and `recognised_premium` among them. Where that
move crosses a half cent, or a total adds several, a figure shown in cents moves by one; on a
closed year `tjs check-filed` compares in whole cents and does not report that cent as drift
(`tjs compare filed` lists it)
([troubleshooting](troubleshooting.md#after-an-upgrade-a-gain-a-cost-or-a-trace-column-moved-by-00001-or-a-zero-the-gains-file-wrote--00-is-now-00)).
The US engine's §355 spin-off warning and §356 trace line print their amounts from the exact
value too: one at an exact half of its last place (a half cent in the warning) is a unit up.
A zero in the gains file is written 0.0, where it was sometimes -0.0 (a Canadian short whose
cost is all used, too). Large books take about
5 to 25% longer.

**The T1135's maximum total cost.** `tjs t1135` adds the foreign pools' costs exactly now and
rounds the year's maximum half up to the cent, so every Python shows the same figure. A re-run
can move it by a cent on any Python: where the exact total is a half cent (it rounds up now),
and on 3.10 or 3.11, which added the costs differently. When two days' totals are exactly
equal, the date of the maximum is the first of them. Each symbol's maximum and year-end costs
round half up too, so one at an exact half cent moves up a cent, and with it its country's
total; a threshold's answer moves only for a maximum within a half cent of it ([troubleshooting](troubleshooting.md#tjs-t1135-the-years-maximum-total-cost-differs-by-a-cent-on-python-310-or-311-from-312s)).

#### US

**A US filing position's per-row figure.** The `loss_override` note on a gain row of a sale an
`ALLOWLOSS` line claims carries that row's own share of what the wash-sale rule would have
disallowed, its lot's, not the whole fill's: a sale filled from several lots is several rows
with one id, and their shares add up to the record in the gains file's `loss_overrides`. The
record, `tjs run`'s warning and `tjs sum` are unchanged; `tjs form-export`'s row notes and
Form 8949 `filing_position`, `tjs audit` and `tjs wash-sales --explain` show the smaller
per-row figure, and the US `tjs wash-radar` the fill's figure once instead of once per lot. A
lot of the sale that gained carries no note now, so those commands show one note fewer for it.
No gain, basis or total moves
([troubleshooting](troubleshooting.md#us-tjs-wash-radar-the-figure-the-rule-would-disallow-gives-for-a-sale-an-allowloss-line-claims-is-twice-what-tjs-run-says)).

- US projects with two or more taxable accounts: run `tjs run` again if
  one account sold a security at a loss and another had bought it in the
  30 days before, then gave those shares up in a merger for shares and
  cash (§356), or exercised those warrants or call options, before the
  loss (an exercised put delivers no shares and changes nothing). More of
  the loss is disallowed now, and the amount is in the basis of the
  shares the other account received; where the merger's cash made a
  gain on those shares, that gain falls by what the higher basis takes
  from it (the merger's row, split at the matched shares) and the new
  shares take the rest (TJ-2700, TJ-2737, TJ-3427).
- US projects with short sales in two or more taxable accounts: run `tjs
  run` again if one account covered a short at a loss and another had
  sold the security short in the 30 days before and covered before that
  loss. The loss is disallowed now and the other account's cover shows
  a lower gain by the same amount; the year's total is unchanged when
  both are in it. A loss that was already disallowed against a short
  still open matches the earlier, covered short first now (the order
  made): the amount moves from the open short's proceeds to that
  cover's row, so the year's gain falls by the amount moved while that
  short stays open past the year end. A new line asks for a manual
  check: "covered ... at a loss that was itself disallowed — not
  matched" (the earlier cover's own loss was disallowed, so it takes no
  amount; said when its turn comes with shares of the loss still
  unmatched, as the long side's "sold ... at a loss that was itself
  disallowed" line is now: fewer of those than before) (TJ-2708).
- US projects with two or more taxable accounts and a wash sale whose
  replacement another account had sold (or, a short, covered) before
  the loss: run `tjs run` again. Where the disallowed amount turned
  that earlier sale or cover into a loss (about two in three of the
  covered-short cases in our generated books, and the long side's the
  same way), that new loss is now tested for a wash sale of its own. If
  the account, or another of yours, bought the security (sold it short)
  in the 61 days around that earlier sale and units are left, the new
  loss is disallowed there and added to that purchase's basis: the
  earlier row's loss shrinks (toward zero, never to a gain), the year's
  total gain rises by the amount deferred, and it comes back when the
  replacement is sold. With no such purchase nothing moves. It can go
  the other way for another row: the newly tested loss takes
  replacement units, so a later loss that had them can come out
  allowed (the disallowed total falls); and when the units it takes
  are a sheltered account's (an IRA's), that part is disallowed for
  good, not deferred, and the total gain rises by it. A filed year is
  left as filed, down a chain of such rows too: the losses booked in
  the later year carry the disallowance. A year you closed with an
  earlier version, with the later loss's amount already in the filed
  sale's cost and that sale's loss allowed, stays exactly as filed
  and nothing is booked for it: an ATTENTION line names the sale, the
  amount that would now be disallowed and the purchase that replaces
  it (sold since, or still held), and says the return may need an
  amendment (Form 1040-X); `tjs check-filed` stays clean. If you amend,
  close that year again with this version so its lock holds the
  disallowance and the replacement takes it: in that year's project
  move `filed/<year>.json` out of the `filed/` folder (keep it: it is
  the record of what you first filed), then `tjs run` and `tjs close-year`
  (with the old lock in place the run keeps the row as first filed,
  and `close-year --force` records it so again). A chain that lies
  wholly inside the filed year is named the same way. One line of the
  run counts the rows tested and says the order they were tested in:
  after the losses already matched, not the order of the sales (Reg.
  §1.1091-1), so a replacement a later loss took first stays with it
  (TJ-3428).
- US projects with dividend compensation paid on a short (IB, whose rows
  state the ex-date): run `tjs run` again if the account sold more of the
  security short between the ex-date and the pay date, or covered a
  short between them. The payment's split among the shorts changes: a
  cover's cost (Form 8949) and the paid compensation in `tjs sum` move by
  the same amount. Rows with no ex-date keep their figures: the run
  says so for a payment with a short sold or covered in the 60 days
  before it, else in one line with a count. A row whose ex-date no
  short was open over no longer adds to a cover's cost (a Warning asks
  you to check the row) (TJ-2709, TJ-3431, TJ-3432).
- US projects with a security's two listings (the native-currency
  books keep them apart): run `tjs run` again if shares were journaled
  into a listing while the account was short it. In holdings, `tjs
  sanity` and the native view the journal covers the short now, and the
  position matches the broker's net; the native view shows the cover's
  gain or loss on the journal's date. The tax books join the listings
  and date that gain on the sale: Form 8949 and the year's taxable
  gains are unchanged (TJ-2738).
- US projects that moved shares between two taxable accounts into one
  that was short the security: run `tjs run` again. The tax books
  change: the moved shares close the short on the move's date at their
  own basis, so the year of the move gains a cover row on Form 8949
  (long-term only when the lot was held more than a year on the day of
  the short sale), the receiving account holds only the rest of the
  moved lots, and its later cover of that short, if the books had one,
  is now a purchase (or covers less). A Warning names the cover on
  every run, with its term. That term reads the delivered lot only:
  if you held other shares of the security on the day of the short
  sale, bought a year or less before it (or after it), §1233(b) can
  make a long-term gain short-term, and shares held over a year can
  make a short-term loss long-term (§1233(d)); taxjson does not read
  them, and §1259 is not modelled. An account's own books (its
  `reports/<account>.sum`) still hold the short beside the moved
  shares, with a note there. A closed
  year whose rows held the move changes with it: `tjs check-filed`
  reports the drift (TJ-3424).

#### Scripts and `--json`

**Every `--json` output carries a top-level `schema_version`**: an integer, 1 where there was
none (`checklist` stays 3, `years` 2, `tips` 2). Only a key is added: a script reading the
documents reads them as before; one comparing a whole document or its exact set of keys sees
the new key. From 1.0.0 the number changes only when a key is renamed, moved or removed, or a
value changes type or meaning ([settings.md](settings.md), "`--json` outputs"). Two values
change with it:

- `tjs form-export --form txf --json` (and `taxjson-form-export --form txf --json`) is
  refused, exit 2 and nothing on stdout:
  `Error: --json does not apply to --form txf (TXF is its own format, not JSON)`. It printed
  the TXF text, not JSON, under a warning. Drop `--json` to print the TXF (`--out FILE`
  writes it); `--form 8949 --json` gives the same rows as JSON
  ([troubleshooting](troubleshooting.md#tjs-form-export---form-txf---json-error---json-does-not-apply-to---form-txf-txf-is-its-own-format-not-json-exit-2)).
- A NaN in a figure of `tjs wash-radar --json`, `tjs harvest --json`, `tjs fees-sum --json` or
  `tjs reconcile-slips --json` is `null`: it was a bare `NaN`, which is not JSON (a strict
  parser refused the whole document; Python's `json` read a float nan). A script that tested
  a figure for NaN tests it for null. The radar's `--json-out` file is unchanged.

**`tjs close-year` exits 2, not 1, when a file or folder it needs cannot be read or
written**, as the exit-code table in docs/commands.md says: "cannot write filed/<year>.json: …"
(filed/ is a file, or it or the project folder cannot be written), "cannot copy … to
inputs/…", "cannot rewrite taxjson.toml: …", "could not read work/…" (a book it locks) and
"the thaw did not finish: …". **What it asks of you:** a script that tested for exit 1 on
these should test for 2. A refusal that is a finding (the books drifted, the lock exists,
the year has not ended) still exits 1. Nothing else changes: nothing was written, as before.

**Keys left out or removed.** `tjs estimate --json` no longer has `carry_back_counted_unmet`:
read `carry_back_unknown` (the carry-back "can't tell", under New stops). Where a carryover
ledger fails or left a taxable account's book out (New stops), a script must allow for that
state:

- `tjs estimate --json` leaves out `losses_carried_back` and `losses_carried_forward`, and adds
  `carry_back_unknown`;
- `tjs handoff` can exit 2, with `carry_unchecked` in its `--json`, where it reported a mismatch;
- `tjs carryover --json` has `missing_books` when it left a book out.

**`--json` money is printed exactly.** A money figure in a `--json` output that printed a
float's binary error (80.72999999999999) is its exact decimal (80.73): a total is the exact
sum of its rows, a figure from a quotient or a rate is at 6 places, a price at 8, and `tjs
form-export --json`'s `gain_unrounded` at its 4 again (as in v0.41.0). Each moves by less
than 0.000001; no key, type, `schema_version` or text output changes. Quantities can still
print a float sum's error.

**`tjs t1135 --json`: `filing_required` is `true`, `false` or `null`.** It was always a
boolean, the plain comparison with the threshold; it is now `null` wherever the text gives
no verdict: the classification unresolved (`classification_status` `"unresolved"`), or below
the threshold with `total_settled` false, with a property in `unknown_acb_symbols`, or with
`year_complete` false. Read `null` as "no verdict yet", never as false. The plain comparison
is the new `over_threshold_as_counted`. `simplified_method_available` is `null` unless
`filing_required` is true (it was `true` beside a false `filing_required`), and while the
part is open. New keys: `classification_status` (`"resolved"`, `"verdict_holds"`,
`"unresolved"`), `classification_unresolved_symbols`, `classification_conflicts` (each with
the ticker.map `lines` that settle it), `classification_unknown_cost_symbols`,
`max_total_cost_low`, `max_total_cost_high` and `max_total_cost_high_at_least`;
`review_symbols` can also list a holding counted under one foreign country whose ISIN names
another (New stops, above). `schema_version` stays 1. **What it asks of you:** a script that
took a false `filing_required` as "no T1135 required" tests for `false` itself, not for a
falsy value, and one that wants the comparison reads `over_threshold_as_counted`.
`tjs checklist` reads the same keys.

#### Nothing to do

**A full run clears what it no longer builds.** After the first run of this release, a
full `tjs run` removes the work/ and reports/ files the last run built and this
one does not, saying so (`removed N files the last run built and this one does not`): an
account taken out of taxjson.toml (its stamps and notes too), an export taken out of an
account, a radar for an account that is no longer taxable; only the files taxjson writes, in
work/ and reports/, as it wrote them (one edited since is kept and said: `kept N files the
last run listed and this one does not build`). Nothing to do; to keep a removed account's
last books, copy them out of work/ and reports/ before its first run without it. A removed
account whose books an older taxjson built is still only warned about, as before:
`work/ carries artifacts for account(s) not in taxjson.toml`
([troubleshooting](troubleshooting.md#tjs-run-says-removed-n-files-the-last-run-built-and-this-one-does-not)).

**A marked short beside a denied loss's wait (Canada).** While a short the broker marks was
open and a denied loss's addition waited on the symbol's flat pool for the position a rename,
a merger or an opening balance opens, `tjs list --date` and the year-end snapshot listed the
short as the symbol's pooled row, and the cost timeline's row of the symbol was the short's.
They now list the short on its own row with its account, as a short beside a long, and show
no row for the wait (0 units: no position, as for a book with no short); the cost timeline's
row of the symbol is the wait, 0 units and the deferred amount. A `tjs close-year` record
made for a year end inside such an interval lists the short beside the symbol
(`year_end_shorts`) instead of as it; a year closed before the upgrade holds it in `year_end`,
and `tjs handoff` matches that against the short the opening now holds beside, reporting only
a changed quantity or cost. No figure moves
([troubleshooting](troubleshooting.md#canada-while-a-marked-short-of-a-stock-is-open-the-cost-timeline-shows-the-short-as-the-stocks-own-row-and-a-loss-denied-as-superficial-that-waits-for-its-replacement-has-no-row)).

**Fewer repeated warnings from the superficial-loss solver.** The Canadian engine no longer
re-walks the whole book on every pass of its superficial-loss solver; a pass walks on from
the first row its denials changed. Figures, the superficial-loss list and the pass count
(`wash_solver_iterations`) are unchanged. Five warnings printed for one row were printed
once per pass and are now printed once per walk of that row, so a book that took several
passes shows them fewer times: `negative days_held for ...`, `assignment treated as
cash-settled`, `return of capital on ... exceeded the ACB`, `ATTENTION: ... write it as
ASSIGN` and `ATTENTION: ... states no shares per warrant`. Nothing to do.

- Interactive Brokers, in a Canadian project: an ordinary dividend paid the same day as a
  foreign issuer's return of capital (booked as a dividend, s.90(1)) now carries the share
  count and rate from IB's accrual in another currency (it had 0); when the two are alike in
  every number, the one listed second (either) loses its "[fill #2]" mark, which changes its
  row id (TJ-2594). No amount moves; nothing to do.
- `tjs wash-sales --explain`'s ±30-day window table, on a denied loss whose symbol a SPLIT row then renamed
  into a ticker the book already held (TJ-1993): that row's two "pool after" columns show the
  merged holding (they showed the renamed shares alone), and the row's line in the calculation
  trace carries the same figures with the note `RENAMED`. No gain, ACB or denial changes.
- `tjs carryover --on-books` on a closed year shows the books' row, with none of its lock in it (TJ-1559).
- `tjs close-year` relays the warnings of the estimate it builds on, as `amt` and `instalments` do
  (TJ-1434): you may see new `Warning: tjs estimate: ...` lines. The lock is the same.
- `transfers_as_acquisitions = true`: a registered account's custody
  restatement of three or more symbols, with another symbol's longer
  zero-net transfer cluster starting among them, nets without a
  declaration now. Declared counter-TRANSFER pairs you added for it stay
  valid; nothing to do (TJ-2725).
- `tjs sanity` in a Canadian project, a crypto account's holdings file with a USD stablecoin
  row (`USDC.KR`, `asset_type = "crypto"`) (TJ-4950): the row is no longer a difference when
  the books carry no position of the coin; it is left out as a cash row is, with a note
  (`not compared as a coin: USDC (...)`). A sanity step that was red only for such a row turns
  green; `--json` `discrepancies` and `cost.rows` lose the row and `notes` gains one line.
  Nothing is asked of you. (A crypto account's `OPENING` line is refused by the run, so none
  can be there. If you booked the coin with `.tt` BUYSELL lines to clear the old difference,
  the books carry it as a position and sanity compares it as before; the Kraken and Coinbase
  parsers already count the coin as US-dollar cash, so such lines hold it a second time:
  [troubleshooting](troubleshooting.md#tjs-sanity-canada-a-usd-stablecoin-row-of-a-crypto-snapshot-symbol--usdckr-asset_type--crypto-is-missing_in_taxjson-and-never-clears-with--1-with-fewer-shares-usdc-bought-before-your-data-step-5b--tjs-opening-crypto-file).)

### v0.41.0

The notes of v0.39.0, v0.40.0 and v0.41.0, which were listed under no heading.

- `reports/<account>_holdings.toml`: no filed figure moves. `trades` now keeps the
  purchases before a move in kind the engine netted, and lists a same-clock sale
  and purchase of a flat symbol whole, so a chart built on it gains those markers.
  A holding that came by transfer has a new `arrivals` list (`date`, `qty`, `kind`);
  a reader that does not know the key is not affected.

- IB: a statement with an execution-id column, holding fills alike in every
  field where one has no id and two or more others have different ids, lost one
  fill to dedup. All are booked now: that year's gains and holdings change on a
  re-run. Other statements do not move (TJ-1269).
- tjs crypto-sends (TJ-1145): if you decided a send in the hour the clocks
  repeat before v0.39.0, that release moved its id, and the decision may now
  be on another send of the same coin and quantity. The listing names those
  ids; check each and re-decide if it is wrong. A wrong one moves a gain.
- Kraken with several trades exports (TJ-1270): gains can change where a sale
  and a rebuy of one coin share a second across two files (a fraction of a
  second apart, or in the hour the clocks repeat): the sale now comes first.
- An option exercised or assigned before an `OPENING` snapshot, its delivery's
  row replaced by the snapshot, and the snapshot smaller than the delivery
  because delivered shares were sold first: a later plain row at exactly the
  strike no longer takes the premium a second time. Its cost falls, and its sale's
  gain rises, by the premium, in that case only (backlog TJ-1332). New warning:
  where the books cannot say whether a plain row at the strike just after a
  snapshot is the delivery, the run says to write it as `ASSIGN` if it is
  (backlog TJ-1325); do so and run again.
- `tjs stats` and `tjs win-rates` only, after a return of capital took part of a
  denied loss off the cost; nothing filed moves. A sale carrying a put's premium
  delivered before a reversal of that return of capital falls by the part the
  reversal hid (TJ-1333). A sale of a spin-off's new shares rises by their part of
  a put's premium (TJ-1274). A US sale by an assigned written call falls by the
  call's premium (TJ-1272). `tjs run`, then read them again.
- A merger held in two broker accounts, with cash in lieu in only one of them,
  is one event in either order. Where it was two, the new position's quantity
  and cost, and the cash-in-lieu gain, move to the delivered share count. The
  election stays; check the year with `tjs check-filed`.
- RBC: a spin-off whose parent had a stock split under a temporary code, in an
  account that traded two listings of the parent, may name another parent
  listing than before (or none, with the warning, when both hold shares). Its
  event id changes with it: elect it again (`tjs elect --pending`).
- An `OPENING` of a new symbol in one account with the rename only in another:
  the T1135 maximum cost (and the year-end cost columns) no longer count the old
  purchase beside the snapshot's shares, so they can fall; `tjs handoff` reads
  the same units. No gain moves. Compare a filed T1135.
- A warrant exercise after a dated `RENAME`, in an account with rows left on the
  old symbol, whose shares arrive under the new symbol: the warrants' cost moves
  from the old symbol's position to the new one's, which moves those positions'
  later gains. Check with `tjs check-filed`.
- Canada, Questrade: an s.86.1 spin-off paid in two broker accounts after the
  parent was sold now cuts the cost of the sales in both accounts, not only the
  last one's. Each sale's cost moves; the year's total moves only when those
  sales fall in two tax years. Check with `tjs check-filed`.
- Questrade: a spin-off posted, reversed and reposted under another symbol now
  books its units under the symbol that kept them, not the reversed one. A
  saved election still applies.
- US projects with crypto: an earlier message told you to record a gift you
  gave as `self`, as though that booked it (TJ-0928). It does not: the coins
  and their basis stay in your books and a later sale of that coin uses them.
  taxjson cannot book a US gift yet (`docs/limits.md`). No figure moves. `tjs
  checklist` now asks about each send recorded as `self`: if one was a gift,
  work out later sales of that coin by hand; if none was, `tjs checklist --done
  crypto-sends`.

- US projects: Form 8949 figures can move on re-run where a rename (a split
  to a new ticker) is stamped later in the day than a trade of the new ticker
  (TJ-1126). That trade now comes after the rename, as though the rename took
  effect at the day's open, so it can close the renamed position instead of
  opening a new one. A plain split and a day with no such trade do not move;
  Canadian figures do not move. A day on which both tickers traded before the
  stamp keeps its figures and gets a `Warning:`.

- A share position sold down to a rest under 1e-6 units that still carries half
  a cent or more of cost is now held (TJ-1062, TJ-1141). It takes a share priced
  in the thousands per unit. The stranded-basis warning goes, the rest shows in
  `tjs list`, and its later sale is realized at its cost, so the gain of that
  year moves; before, the sale opened a tiny short and its gain was in no total.
  Re-run with `tjs run`; a year already filed keeps its lock until
  `tjs close-year --force`. An ordinary share's rounding rest is unchanged.
- A buy the broker codes as closing a short your files do not hold (IB code
  `C`, RBC `COVER SHORT.`) is now flagged (TJ-0844): `tjs run`'s closing list
  names it, and `tjs sum --json` and `tjs form-export --json` report
  `complete: false` with an `unbacked-cover` issue where they said complete.
  This includes a cover dated before the tax year, whose long is still in the
  books' costs. No figure moves until you answer it. Add the older short sale,
  or run `tjs find-missing-history --write-missing-history`, which drafts `.tt`
  `OPENING <date> <SYMBOL> -<qty> cost=unknown`. With that line the cover goes
  to manual reporting, no long is added, and the cost of later sales and the
  year-end holdings change. Compare `tjs sum` before and after; in a closed year
  `tjs check-filed` may report the change.
- Transfer-ins with a stated book value, with a `.tt` purchase of the same
  stock in the account (TJ-1326, TJ-1327, TJ-1340): a re-run can book a
  transfer-in that was `.tt_covers`, and the gain and `tjs list` move, when
  that purchase is replaced by an `OPENING` line dated on or after it, or a
  broker sale really sold it because the units from an `OPENING` line or a
  move of your own had already left. The phantom short goes. `tjs transfers`
  shows the new status; a filed year's lock (`tjs check-filed`) may report
  the change. A `.tt` purchase added on the arrival's date only to work
  around this should be removed.
- The command is `tjs` in every message, report and doc. `taxjson` still runs
  the same program, so scripts and cron jobs keep working; `taxjson.toml`, the
  `taxjson-*` tools and the `TAXJSON_*` variables are unchanged. No figure
  moves. (a) `tjs format --check` and `tjs format-map --check` report your files
  as not formatted until `tjs format --write` and `tjs format-map --write`
  update their comments. Only comments change, but ticker.map counts as an
  input: `tjs compare` names it as changed, and in a closed year `tjs check-filed`
  notes a changed frozen input (the figures still match), so leave a closed year's
  ticker.map as it is.
  (b) `tjs compare` and `tjs compare filed` treat the old and new command name
  in a report as the same, so nothing shows as changed; a plain `diff -r`
  against an older snapshot or filed report does show those lines once. (c) A script reading a message's prefix or the `usage:` line now sees `tjs` there;
  `tjs --version` still prints `taxjson X.Y.Z`.

- Canada, Schedule 3 / superficial-loss figures can move on re-run
  (TJ-0828, TJ-0829, TJ-0654): (a) a loss whose replacement went in kind
  into a plan outside the project and was withdrawn (INKIND lines) before
  day 30 is now allowed; (b) a declared INKIND contribution with another
  transfer-in of the stock in the same account within 10 days now pairs
  with the project's plan, and the plan's own sale clears the replacement;
  (c) a replacement bought in one registered account and moved to another
  registered account in the window, still held on day 30, now denies the
  loss for good, and older units moved into an account that bought and
  sold a replacement in the window no longer deny one. Compare `tjs
  wash-sales` and `tjs sum` before and after; a filed year's lock (`tjs
  check-filed`) may report the change.
- Canada, IB (TJ-0830): an option moved in kind into a registered plan,
  whose IB statement states a contract size other than 100, now replaces
  that many shares, and a denial it caused shrinks accordingly. A
  ticker.map `MULT` line added only to work around this can be removed.
- Transfer-ins (TJ-0823, TJ-0822, TJ-0821): a re-run can change which
  transfer-ins are booked at the broker's stated cost. (a) A second
  transfer-in covered by its own .tt purchase, after a .tt sale of the
  first, is no longer also booked (a phantom holding goes; gain up by the
  duplicate's cost). (b) An arrival covered by a .tt purchase under its
  old ticker plus a RENAME line is no longer booked again. (c) A .tt
  purchase contributed in kind no longer covers a later arrival, so that
  arrival is booked at its stated cost and its sale enters the totals. (d)
  A transfer-in of a listing a DISTINCT line keeps apart from a same-day
  transfer-out of the other listing is booked as an arrival and its sale
  counts. `tjs transfers` shows the new status of each.
- TJ-0832: a purchase line drafted by an older `--write-purchases` for a
  lot bought before a split that another account reports is in post-split
  units. Divide its quantity by the split ratio (the cost stays), then
  `tjs run`. Re-drafting with `--write-purchases --force` gives the right
  line.
- TJ-0819: an `OPENING … cost=unknown` line that an older
  `--write-missing-history` wrote for the new ticker of a broker-marked
  short after a rename should be deleted from
  inputs/<account>/missing_history.tt; the cover's gain then comes back
  into the totals.
- TJ-0825/TJ-0814: if a project already holds two openings of one security
  under spellings ticker.map joins, keep the one with the right cost and
  remove the other. `tjs opening` now refuses to write such a pair.
- Canada, superficial loss: a share loss whose only replacement was a call
  expiring on day 30 of the window, or a call series a split renamed and
  that was sold before day 30, is now allowed. Schedule 3's capital loss
  grows by the amount once denied, and a denial added to a taxable call's
  cost is gone (TJ-0725, TJ-0716). Re-run tjs run.
- Canada, futures: a loss with a long replacement held at day 30 is now
  denied even when another account opened a short in the same contract
  inside the window. The year's loss shrinks, and the replacement's cost
  (and T1135 cost) grows by the denied amount (TJ-0651, TJ-0650).
- Canada, short sales: a broker-marked short (RBC SHORT. / COVER SHORT.,
  IB codes O and C) in one taxable account no longer sells shares another
  account holds. Its gain moves from the year the short opened to the year
  it is covered, and the long shares stay in the year-end holdings and the
  T1135 cost. A year already filed with the old figure may need an
  adjustment (TJ-0721).
- Canada, grant timing: an assignment in one account now retracts that
  account's own written option, not an older one in another account. The
  year of the grant that is retracted, the share ACB and that year's
  option gain can change (TJ-0718). Buy-backs and expiries close their own
  account's write first, which can move a premium between write years on
  Schedule 3.
- Assignments delivered as one stock row for several option contracts now
  fold every premium into the shares (Canada and US). The shares' ACB
  falls by the premium that was missing, and the later sale's gain rises.
  The unconsumed option-assignment warning goes away (TJ-0838).
- Canada: shares delivered December 31 from an exercise whose option close
  is posted in January now count in the December 31 holdings (tjs
  close-year, tjs list --date) and in the T1135 cost at their
  premium-inclusive cost. A T1135 near the threshold can become required
  (TJ-0839).
- RBC merger elected rollover_s_85_1_5 (or US reorg_368, or a sheltered
  account's default) with a cash-in-lieu row dated after the exchange: the
  fraction's sale moves to the payment date. A year-end merger whose cash
  is paid in January moves the fraction's small gain or loss into the next
  year (TJ-0866).
- RBC spin-off cash in lieu repeated across overlapping exports, or
  reversed and re-paid: the s.86.1 fraction sale now covers one payment
  and one fraction; the fraction's gain and the remaining shares' cost
  change (TJ-0755, TJ-0759).
- RBC merger whose cash in lieu sits in a later export: the fraction is
  now sold for the cash; the fraction's gain appears and the new shares'
  cost falls by its share (TJ-0756).
- Event ids change, so elections must be set again with `tjs elect
  --pending`, for: a Questrade spin-off that was merged with another
  parent's on the same dates (TJ-0751); an RBC merger reversed and
  rebooked (now dated on the rebooking, TJ-0763); RBC share-class
  reorganizations that were paired across classes (TJ-0757); an RBC
  spin-off whose parent was the SEC# code after a stock split (now the
  parent's ticker, TJ-0764); an RBC spin-off a cross-currency reversal had
  replaced (TJ-0762).
- tjs fx-cash --ledger v2 (Canada): a year with a gain or loss on repaying
  a foreign-currency debt (the ON DEBT column) reports a different
  REPORTABLE figure on a re-run. The $200 now applies only to the gain on
  currency, and the gain on debts counts in full. Own currency that an own
  move uses to repay a debt now needs that day's FX rate. Without one the
  ledger is NOT COMPUTED ('no USD rate on file'), as an overdraft already
  is (TJ-0724).
- tjs fx-cash --ledger v2 and tjs t1135: a year with an own move that the
  receiving broker booked a few days before the sending one changes on a
  re-run. The FX gain and the year-end pool can drop (a phantom pool after
  a debt repayment), and the T1135 cash maximum no longer counts those
  units in both accounts (TJ-0717).
- tjs t1135 with tax_date = "trade": the year-end (and maximum) cost of
  foreign cash changes on a re-run when an account had a receipt or
  payment traded in the year and settled after it, because every account
  is now at the trade-date pool's average (TJ-0720).
- tjs fx-cash --ledger v2: a year with overlapping exports of one account
  (a year's statement plus a year-to-date one) and two equal conversions
  on one day may change by a commission on a re-run (TJ-0860).
- tjs fx-cash --ledger v2: a year whose ledger refused, or crashed with
  ZeroDivisionError, over a small Forex order whose commission took all it
  bought, or over a broker's cash return of capital, may now compute a
  figure (TJ-0722, TJ-0846).
- tjs fx-cash --ledger v2: two `CASHOPEN <Jan 1> CAD` lines (base
  currency) for one book are now refused, and the ledger is NOT COMPUTED
  until one is deleted. Delete the wrong line (TJ-0840).
- tjs fx-cash --ledger v2: a year after one in which an account of a
  broker had no export may now carry a balance that was dropped before.
  The opening pool and year-end figures change on a re-run (TJ-0834).
- tjs close-year with ledger v2: a year whose own move is still in transit
  at Dec 31 (its arrival in the books) now computes, and its lock records
  `fx_cash_v2.transit`. An older release reading that lock ignores the key
  and refuses the January arrival, so use this release for the next year
  (TJ-0656).
- Kraken: run `tjs run` again if a Kraken trades export has maker fills
  with a negative fee. Each such fill's cost (a buy) or proceeds (a sale)
  moves by twice the rebate, in your favour (TJ-0749).
- Kraken: with an older and a newer ledger export of the same period, a
  coin-for-coin swap whose older copy had no amountusd is now valued at
  the broker's amountusd instead of a daily close, so its gain can move
  (TJ-0738).
- Kraken: a sale and a rebuy of one coin within the same second, in a
  trades export listed newest first, now replay sale-first. The gain and
  the cost of the remaining coins can move (TJ-0746).
- FX on cash (v2), Kraken: a conversion split across two ledger exports
  now uses the CAD paid as the cost of the currency bought, so the
  realized FX gain can move. A Kraken account with several ledger exports
  of one year that was NOT COMPUTED over a false opening balance may now
  compute; remove any CASHOPEN line added only to satisfy that false
  opening (TJ-0861, TJ-0888).
- FX-on-cash ledger v2 (opt-in): a re-run can change the figures of a year
  with IB Forex fills at the Trade level only (TJ-0858), several Forex
  fills of one day listed out of clock order (TJ-0859), a cancelled (`Ca`)
  Forex order (TJ-0743), or a commission refund dated after its trade
  (TJ-0748).
- IB trades: a trade cancelled in full whose commission IB did not refund
  now leaves a FEE row. It shows in `tjs fees`, and the cash ledger now
  reconciles (TJ-0742).
- IB combined-accounts statements: a commission refund naming its account
  now lowers that trade's cost, so the year's gain can change by the
  refund (TJ-0740).
- IB overlapping statements with several refunds of one trade: a purchase
  booked twice is now booked once, so gains and holdings can change on a
  re-run (TJ-0760).
- A trade that has a commission refund folded into it now carries a
  `commission_refunds` field in work/ JSON and `--json` outputs. The field
  is evidence only and is not part of the row id.
- taxjson-fetch, IBKR Flex (TJ-0869): in a single-folder project, `tjs
  fetch` now refuses to replace ib_flex.csv when the new download drops
  dates before the tax year or up to 37 days after it. Before, it refused
  only for the year's own dates. The download is saved as ib_flex.csv.new.
  Widen the Flex query's period to cover the dates named (for example Jan
  1 of the year to Jan 31 of the next), or move ib_flex.csv aside to
  accept the new file. If an earlier fetch already dropped a January
  repurchase, restore it from ib_flex.csv.bak and re-run: a denied
  superficial loss comes back (the year's gain moves).
- taxjson-fetch, IBKR Flex (TJ-0867): a Flex query that covers several
  accounts (a combined statement) is now refused. Scope it to one account
  per taxjson account.
- taxjson-fetch, Questrade --positions (TJ-0857, TJ-0880): re-run `tjs
  fetch --positions`. An option with a ticker.map MULT line now carries
  that multiplier, so its snapshot cost in `tjs sanity` / `tjs opening`
  changes (for example tenfold for a mini). An open option whose series
  the books also hold on the other listing may change suffix and currency,
  or lose its currency with a note.
- taxjson-fetch, Questrade (TJ-0885): a symlinked token path now rotates
  the shared target. Anyone whose link was already replaced by a separate
  file should copy the newer token over the shared file and restore the
  link.
- Re-run `tjs run` to refresh `reports/<account>_holdings.toml`. Nothing
  filed changes. Its `trades` arrays can change: a round closed before a
  same-day split, or before a same-settlement-day rebuy in a US project,
  is no longer listed, and an opening balance no longer appears as a BUY
  on its snapshot date. A holding of unknown cost under the dust threshold
  (0.001 units, or 1e-6 for a crypto account) but at least a millionth of
  a unit now appears with `cost_unknown = true`; before, it was left out.
- `taxjson-export` with a holdings TOML as input, used on its own (`tjs
  run` never does this): the output gains `[meta] as_of`, the input
  snapshot's day. A holding with no cost field is now `cost_unknown =
  true` instead of `total_cost = 0.0`. A Questrade `average_entry_price`
  gives the cost. `asset_type = "cash"` lines are no longer written as
  holdings. `--map` GLOBAL renames now apply. The base_* and cost_source
  fields are kept. Two new refusals (exit 2): snapshots of different days
  in one call (export them one at a time), and a `[meta] holdings_count`
  that differs from the number of `[[holding]]` tables. The snapshot
  readers (`tjs sanity`, `tjs opening`, and portoml, which ignores unknown
  keys) read the new `as_of` key as the positions' date.
- tjs reconcile-slips can now fail, with no change to any figure in the
  books, where it used to pass: a slip CSV with a duplicated amount column
  (exit 2); a slip quantity of 0; an option strike finer than 0.001; a
  cover of a short sale that is not in your files (UNKNOWN_PROCEEDS); and,
  under grant timing, an option written this year that expired by the year
  end, or a buy-back of an earlier year's write, with no slip row
  (MISSING_FROM_SLIP). Fix the slip CSV or add the broker's row; checklist
  step t5008 shows these.
- The reconcile-slips --json counts gain a key, `unknown_proceeds`, which
  is always present.
- `tjs stats` and `tjs win-rates` figures can change (review views only;
  no filed figure moves). Assigned-option premiums now come out of share
  trades differently in these cases: two same-day assignments, pools
  shared across accounts, negative net premiums, fees on assignment rows,
  and Canadian spin-offs. A custody round-trip of a written option now
  counts its trade. A buy-back partly of pre-data contracts is now counted
  as unknown-cost instead of a trade. For the spin-off case, run `tjs run`
  once so work/ has the cost timeline the view reads.
- option-yield figures can change after a re-run (planning numbers only,
  nothing filed): a class share's options now use the shares' cost as
  their capital (TJ-0910), so the yield can drop a lot; shares held partly
  at an unknown cost now give no yield (TJ-0774); a futures option series
  with its multiplier on one row only now has the right notional
  (TJ-0694); an expiry with a fee is counted as expired instead of bought
  back (TJ-0790).
- The option-yield / win-rates filter `SYN` no longer selects the class
  share SYN.B.US: give `SYN.B` (TJ-0692).
- ccd-sum (grant timing): CLOSES / QTY can change. A narrow window now
  counts an expiry that falls inside it (TJ-0778), and a renamed write
  that was bought back no longer adds an extra close (TJ-0903).
  winners/losers CLOSES follow renames too. Gains and premiums are
  unchanged.
- An account named like a period (for example `2025`) is still read as the
  period when given alone, now with a warning: give a period before it
  (`all 2025`, `tax_year 2025`) to select the account (TJ-0909).
- JSON additions: option-yield rows, parts and totals have
  `shares_cost_unknown`; list rows have `unknown_cost` and list totals
  `unknown_cost_positions`; shares rows have `unknown_cost`.
- tjs edge-cases --json: a year_boundary.option_expiries row for an
  assignment now carries `settlement` ("cash" or "physical"), and its
  `lands_in` can be null when no share leg falls within 7 days of the
  assignment (the text shows LANDS IN '-'). A script that reads lands_in
  as an integer should allow null. No figure the return reports changes.
- Canada, minimum tax carryover (TJ-0719): in a year with foreign
  dividends credited and a carryover larger than the old limit, more
  carryover is recovered and less lapses or carries forward, the foreign
  tax credit falls, and `estimated_tax_with_amt` (and `tjs amt`'s
  carried-forward figure) falls. A year closed by an older release
  recorded the old recovery in filed/<year>.json: re-close it (`tjs
  close-year --force`) or enter the carryover from the notice of
  assessment in [estimate] amt_carryover. `amt.headroom` is unchanged; the
  new limit is `amt.recovery_limit`.
- Canada, line 40427 (TJ-0653): `--json` `amt.carryover.recovered_federal`
  and the lock's `carryforwards.minimum_tax.recovered_federal` are now the
  nominal credit, not the federal tax drop; the drop is
  `recovered_federal_tax`. `recovered_provincial_surtax` is the surtax
  itself, and the foreign tax credit the lower tax limits is
  `recovered_credits_reduced`. `recovered` (the total tax drop) and every
  tax figure are unchanged. A script that read `recovered_federal` as the
  tax drop should read `recovered_federal_tax`.
- Canada, eligible dividend compensation (TJ-0727): where the year's
  eligible dividends net of compensation paid on a short are negative, the
  estimate (and plot-tax and the AMT check) rises by the saving it showed.
  The new `--json` keys are `eligible_dividends` and
  `eligible_compensation_excess`.
- Canada, issuer country (TJ-0726): for a security whose dividends carry
  ISINs of both countries, the estimate, the foreign tax credit and
  plot-tax move: each payment now follows its own issuer country.
- Canada, unpaired withholding (TJ-0715): where one account names a
  security on its TAX rows and another account has TAX rows naming no
  security on the same security's dividends, the credit rises and the
  estimated tax falls (up to 15% of the second account's dividends, within
  the security's ceiling).
- plot-tax (TJ-0783): no figure changes. The JSON gains
  `unknown_cost_routed`, `unknown_cost_included` and `complete`.
- `tjs migrate --to-years` now stops, with nothing moved, when
  taxjson.toml or .gitignore is read-only (and .gitignore still needs its
  `exports/` line), when taxjson.toml is a symlink leaving the project, or
  when an account is named `slips`. Run `chmod u+w` on the file, replace
  the outside link with a copy, or rename the account and its inputs
  folder. Then run it again. No figures move.
- `tjs align` and `tjs years --diff` now compare a `holdings_dir` inside
  each year's own folder as that year's own (`"../2025/snapshots"` in
  2025/ is shown and brought over as `"snapshots"`). A 2026 started by an
  older `new-year` from such a year: set `holdings_dir = "snapshots"` in
  2026/taxjson.toml and make that folder.
- tjs sanity exits 2 instead of 1 for a holdings TOML it cannot read as a
  snapshot: no [[holding]] array, a [[holding]] row with no quantity, a
  quantity that is not a finite number, a quantity with no symbol, or an
  entry that is not a table. Scripts that treated exit 1 as 'positions
  differ' now see these as input errors. The Error names the file from the
  project (holdings/NAME.toml). (TJ-0643)
- tjs sanity may list INCOME ON SHARES THE BOOKS DO NOT HOLD rows it did
  not list before: pre-September-2017 dividends on purchases too late for
  the T+3 cycle, and any project with no holdings snapshot. These rows are
  informational; exit code and clean are unchanged. (TJ-0815, TJ-0826,
  TJ-0688)
- tjs sanity --json may show a COST row as 'n/a' where it said match or
  differs before: a position with cost=unknown, or a dated snapshot whose
  pooled ACB another account moved after its date. A contract matched by
  its underlying may now show a cost difference where it said 'not
  compared'. Informational only. (TJ-0818, TJ-0817, TJ-0813)
- Settlement (TJ-0833): a US or Canadian share traded before 1995-06-07
  whose export prints no settlement date now settles T+5 instead of T+3.
  Its foreign-currency cost is converted at the later day's rate, so the
  ACB and later gains of such long-held positions can change. Re-run `tjs
  run` and compare; a printed settle date is used as before.
- Exit codes (TJ-0644): with no gains files in work/, sum, estimate,
  plot-tax, shares, list, gains, leaps, leaps-sum, ccd-sum, winners and
  losers now exit 2 instead of 1. A script that tested for 1 should test
  for 2 (or for non-zero).
- Exit code (TJ-0645, TJ-0684): carryover's refusal of a damaged
  work/<account>_base.json under [[capital_gains_dividends]], and `list
  --date`'s refusal of a damaged cost timeline, now exit 2 instead of 1.
- Readiness (TJ-0824): `sum --json` / `form-export --json` readiness can
  now be incomplete with an `unconsumed-premium` issue, and the
  checklist's form-export step needs attention, where an assignment's
  stock delivery is missing. Add the delivery rows and re-run. A consumer
  of the readiness kinds should accept the new kind.
- US projects: a sale between a stock dividend's ex-dividend date and its
  pay date, with a split or a ticker change in between, gives up more of
  its cost to the new shares: its gain and the held shares' basis change
  (TJ-0647, TJ-0648). Re-run `tjs run`.
- Canada: a position under a millionth of a share that carries a cent or
  more of cost now appears in `tjs list`, the inventory and the T1135 cost
  amount; after a consolidation into such a fraction its later sale
  realizes a gain where none was booked and the 'stranded basis' warning
  goes away (TJ-0649).
- Canada: a hand-entered ADJUST with record= and no type=roc now lowers
  the ACB on its record date: a sale between the record date and the pay
  date (often across a year end) changes, and its gain can move between
  years (TJ-0655). Check a year already filed with such a line.
- Canada: an IB 'Payment in Lieu of Dividend ... (Return of Capital)' on a
  Canadian corporation's share moves from dividends (divs-sum, gross-up
  and credit) to ordinary payments in lieu (dil-sum); the estimated tax on
  it rises (TJ-0729). Re-run `tjs run` so the parse records the label.
- US projects: §355 spin-off shares received with a $0 allocation now
  carry the parent's purchase dates; their sale's term can change from
  short to long (TJ-0723).
- US projects with single-stock or other security futures: add `SECFUT
  ROOT` to ticker.map to take them off Form 6781's 60/40 and onto Form
  8949 with their own term; without the line nothing changes (TJ-0730).
- US projects: a short covered within 45 days of the short sale, with
  dividend compensation paid while it was open, has that payment in its
  cost (gain lower, the payment no longer negative ordinary income); the
  estimate stops subtracting other paid compensation (it reports
  pil_paid_not_deducted). Estimated tax and Form 8949 figures of such
  years change (TJ-0728).
- Taxable spin-offs (taxable_deemed_dividend, taxable_distribution_301):
  the dividend is now on the parent's ticker. For a foreign parent with
  Canadian new shares, the estimate moves income from Canadian eligible to
  foreign dividends (no gross-up or credit; the tax rises), `tjs t1135`
  adds the income to the parent, and `tjs divs-sum` lists it under the
  parent. Re-run `tjs run` (TJ-0658).
- rollover_s_86_1 spin-offs of a parent sold before the distribution: the
  gain moves from the distribution year (a gain with proceeds 0) to the
  year of the sale, whose cost drops by the allocated amount. If that
  year's return is already filed, it needs an adjustment; the run's note
  names the sale dates (TJ-0842).
- IB spin-offs and mergers that delivered less than 0.01 share in total
  are now booked: a new position appears, and under rollover_s_86_1 the
  parent's cost drops by the allocated amount (TJ-0841).
- IB: a merger that one account's cancellation used to hide is now offered
  for election, and `tjs run` stops (exit 3) until it is elected
  (TJ-0752).
- IB: an earlier listing journal that was folded into a later merger is
  now its own event. The merger's event id changes (its target is now the
  merger's own listing), so both the merger and the journal ask for an
  election again (TJ-0754).
- IB split-ups held in two accounts: the phantom spin-off of the
  continuing company into itself is gone, and any election saved for it is
  no longer used. The merger and spin-off quantities now cover both
  accounts (TJ-0753).
- IB spin-offs delivered in two rows on one day: the shares and the
  dividend (or allocated cost) now cover both deliveries (TJ-0758).
- TJ-0696: after upgrading, run `tjs run` again. In a project with two or
  more taxable accounts holding one security whose split or rename only
  one account's export reports, the other accounts' per-account holdings
  (`tjs list`, `tjs list ACCOUNT DATE`, `tjs sanity`,
  reports/<account>_holdings.toml, the <account>_wash.sum inventory)
  change: their quantity takes the split and their share of the pool's
  cost rises to match. The pool's total cost, every gain and every filed
  figure are unchanged.
- TJ-0852: `tjs renames` (and its `--json` `carried.book_cost`) can show a
  different book cost for shares delivered by an assigned put (lower, by
  the premium) or an exercised call (higher, by its cost). It now matches
  what `tjs list` and the books carry. Filed figures are unchanged.
- TJ-0851: `tjs renames ACCOUNT --json` now lists only that account under
  `accounts` and `carried`. A script that read other accounts' rows from
  an account-filtered report must call `tjs renames --json` without an
  account.
- Canada, fx_cash_ledger = "v2", Coinbase: currency gains move after a
  re-run when the exports hold USDC (or another USD stablecoin) Converted
  to or from a coin (TJ-0916), a Coinbase One subscription paid in USD or
  USDC (TJ-0870), or two exports with distinct IDs but otherwise identical
  cash rows (TJ-0865). A `CASHMOVE … spot` line added by hand for a
  Convert's stablecoin leg or a subscription is now counted twice: remove
  it.
- tjs crypto-sends (Canada): a stablecoin send's currency gain can move.
  An arrival of another stablecoin after a `self` send is now new money
  (TJ-0652), and a Kraken conversion split across two yearly exports now
  keeps its CAD cost (TJ-0667).
- tjs crypto-sends: two sends of one coin and quantity in the same second
  (ids ending `-1` and `-1-2`) are now numbered in the broker's id order.
  Once after upgrading, check the decisions of such pairs: one saved under
  the old row order may now name the other send (TJ-0663).
- taxjson-fill-crypto: a price an older release cached from the wrong
  Yahoo candle stays in ~/.crypto_price_cache.json. To refresh it, delete
  the cache file and run `tjs run` online (TJ-0744).
- Crypto rows in the fall-back repeated hour (early November in Toronto)
  show compressed local times. A sale and a rebuy in that hour can change
  gains after a re-run (TJ-0747). The transfer sidecar now records its
  input file names; sidecars from older releases are checked by time alone
  until the next `tjs run` (TJ-0671).
- FX (TJ-0843): a project whose rates use Yahoo Finance (a currency the
  Bank of Canada does not publish, or a US project's rates) may now stop
  with "no exchange rate for N row(s)" for a row whose only rate is 13 to
  19 days old. Older releases used a stale rate for such a row; the rules
  say 12 days at most. Fix: enter that row in the base currency at its
  date's rate as a .tt line. The rates file is rebuilt once its newest
  rate is more than 3 days old; delete work/to_base.csv to rebuild it at
  once.
- Futures (TJ-0745): only books converted twice with the stand-alone
  taxjson-convert-currency (USD to EUR, then EUR to CAD) are affected. For
  a contract held in two taxable accounts, the Canadian pooled gain moves
  to what a direct conversion gives. Convert the books again. `tjs
  run` converts once and is not affected.
- taxjson-audit (TJ-0773) takes a new optional `--effective-map PATH`, the
  run's work/ticker.map.effective. `tjs audit` passes it on its own; only
  someone who calls taxjson-audit by hand needs to add it.
- Windowed views outside the tax year (`stats all`, `stats 2y`, `win-rates
  all`, `winners all`, `losers 2023`, `ccd-sum`, `leaps`, `leaps-sum`,
  `--all-history`) in a project with two or more taxable accounts holding
  the same security: gains change to use the cost pooled across those
  accounts (as the tax year's books do), and superficial-loss / wash-sale
  denials now span those taxable accounts. The `--json` `basis` value
  "denials within each account, full history" is now "denials among
  taxable accounts, full history". Filed figures do not change. The views
  rebuild the work/<account>_gains_all.json files on their own the next
  time they run.
- `tjs handoff` can now exit 1 with a 'missed' item for a Dec-31
  straddling sale that an earlier release cleared against another
  account's same-size sale: add the sale to that account's inputs.
- In a year folder whose prior year was closed as a single folder and then
  moved by `migrate --to-years`, a deleted prior-year lock now stops `tjs
  estimate`, `amt`, `carryover`, `handoff` and `fx-cash --ledger v2` with
  'the 2024 close-year lock is missing, while the 2024 project holds the
  reports close-year kept as filed'. Restore filed/<year>.json, or close
  that year again.
- IB trade dates change on a re-run for: Cboe evening-session index
  options traded on a weekday the exchange was closed; Montreal Exchange
  futures traded from 20:00 ET; CME fills from May 29, 2026 that fall
  after 17:00 ET, on a weekend or on a holiday. Each moves to the next
  trading day (time 00:00:00, broker_time keeps IB's stamp). Its
  settlement date and exchange-rate date move with it, and a December 31
  evening fill can move into January, which changes the year it is in.
- IB fills on Asia-Pacific exchanges (AUD, HKD, JPY, SGD, CNH and NZD
  rows) now carry the exchange's local time even when the date is
  unchanged. Their time changes, and so does their transaction id, which
  includes the time. Same-day fills can reorder, which can change lot
  matching and short detection.
- IB dividends, withholding and return of capital on an interlisted ticker
  (for example a .US and a .TO listing) can move to a different listing on
  a re-run: when the other listing was only day-traded or sold short
  later, or the income sits in a statement of its own. Canadian deemed
  gains on an empty pool can disappear, and the held listing's ACB
  changes. Re-run `tjs run` and review the year's figures.
- Generic importer, option rows with a ticker.map MULT line (or a mini
  whose amounts show x10): cost, proceeds and gains move to the declared
  contract size; re-run tjs run and compare tjs sum (TJ-0863).
- Generic importer, a zero-price sale or $0 option expiry whose amount
  equals its commission: the fee is now a cost of the disposition (the
  loss grows by twice the fee), and a Dec-31 expiry's loss moves back into
  that year (TJ-0731, TJ-0732).
- Generic importer, mappings whose [formats] date includes a time: trades
  now carry the stated time; a same-day sale and rebuy listed newest first
  can change its gain and superficial-loss result (TJ-0734).
- Generic importer, futures: a row whose fee or rebate exceeds its amount
  in a file of unsigned amounts is now refused; export the amount signed
  (negative = paid) or enter the fill in a .tt file. Futures gross_amount
  (display only) is now the notional's magnitude (TJ-0668).
- Merged books with two accounts' same-day dividends of one security:
  DIVIDEND net_amount and the TAX row's quantity and per-share price
  change; gross dividends and the total withheld do not (TJ-0736).
- taxjson-xlsx-to-csv: a workbook with uncalculated formula cells is now
  refused; open it in a spreadsheet program, recalculate, save and convert
  again (TJ-0737).
- RBC Direct: an in-kind reinvested distribution ('Reinvest @ $p') whose
  description gives a December record date and a January pay date moves
  into the record date's year after `tjs run`. The income on the year's
  return can change (TJ-0741).
- Questrade: a reinvestment (or stock dividend, or cash in lieu) and its
  reversal that were refused because their Symbol cells differed now both
  drop out: run `tjs run` again (TJ-0761).
- Questrade and RBC Direct: a split ratio that was inverted is corrected
  after `tjs run`, so gains, positions and ACB change. This happened in
  three cases: an overlapping Questrade download repeating earlier sales,
  an earlier split on a short position, or two broker accounts under
  combined_broker_accounts on opposite sides of one security (TJ-0664,
  TJ-0665, TJ-0672).
- T1135 figures can move on re-run. A property's INCOME rises where
  negative payments in lieu were paid on a short of it (TJ-0837).
  `max_total_cost` and the filing verdict can rise where an account with a
  foreign `custodian_country` bought and sold Canadian shares within one
  day (TJ-0646). The total can turn NOT SETTLED (checklist t1135 step:
  attention) where the FX-on-cash ledger v2 refuses before walking an
  account's cash, until the problem `tjs fx-cash --ledger v2` names (often
  a missing CASHBOOK line) is fixed (TJ-0657). If you copied T1135 figures
  to a filed return, compare.
- .tt files written by taxjson-convert-tt or copied from `tjs events` can
  now carry new tokens: `symbol=` on INTEREST lines, and
  `type=futures_settlement settle=...` on settled futures lines. An older
  taxjson refuses these lines. A book converted to .tt with an earlier
  release lost those facts (a bond coupon became cash interest; settled
  futures became raw fills), and income rows exported on the settle basis
  took their settlement date: convert such books again from the JSON.
- taxjson-t1135 used on its own: `--cash` now has to be the document for
  the same `--year` and base currency (exit 2 otherwise). `tjs t1135`
  builds it correctly.
- The engine cost timeline (taxjson-gains --cost-timeline FILE, Canada)
  has a new top-level key `row_ids`, one book row id per step. Readers
  that zip `fields` with `rows` are unaffected.
- tjs slip-audit can now give attention or incomplete (exit 1) where an
  older release said ok: (1) a fund's return of capital differs from its
  T3's while the account total agrees (security-differs); (2) an IB
  payment's components do not add up to its Summary row (not_compared kind
  report-mismatch). The checklist's t5-t3 step then shows as not done.
- Projects with status = "amended" typed in slips.toml: the original is
  now left out of the comparison. A slip-audit that showed the two added
  (and a difference) now compares the amended slip alone.
- Re-run tjs slip-audit before applying return-of-capital suggestions
  taken from an older release when a fund's T3 has both a box 42 and a
  [[slip.line]] of the same fund (TJ-0791: the old draft doubled the ROC),
  when a [[slip.line]] carries a date (TJ-0792), or when IB's report
  covers a trust distribution with a record date (TJ-0794). The older
  drafts could put the return of capital on the wrong date, or count it
  twice, which moves the ACB and the gain on a later sale once applied.
  The figures taxjson computes from the books do not change until the user
  changes the books.
- slip-audit --json: new not_compared kinds amended-slip and
  report-mismatch, new issue kind security-differs, and a roc_status key
  on securities[] records (schema_version stays 1; keys only added).
- `tjs stats` / `tjs win-rates` (planning views only; no filed figure
  changes): the share-trade P/L, win counts and NET_P/L can change for an
  assigned put whose delivered shares were later grown by a stock dividend
  (US), sold after older shares (US FIFO) or moved to another of your own
  accounts (US); for an assigned call that opened or partly opened a short
  (both countries); for options sized by a ticker.map MULT line; and, in
  Canada, for an option series split on a day with an earlier trade of the
  new series. No `tjs run` is needed: the views read the existing work/
  files.
- `tjs option-yield`: a project whose ticker.map has MULT lines now uses
  them for the contract size, so the notional, capital and yield of those
  options change (they were 100/N times off).
- Re-run `tjs run` once: the converted books (work/*_base.json) now record
  each foreign-currency row's trade currency (`trade_currency`). Until
  they are rebuilt, the wash radar dates a TSX US-dollar unit on the TSX's
  days alone, and around a US-only holiday its sell-by date can be a day
  late (backlog TJ-0713).
- Planning dates can move: a US-dollar trade of a Canadian listing
  (sell-by one day earlier, re-entry one day earlier around US-only
  holidays), and a bare ticker (no market suffix) traded in a currency
  other than the base one, which is now planned on its trade currency's
  calendar (backlog TJ-0713).
- Planning verdicts that change: sell-check can now say UNSAFE (exit 1)
  beside a superficial loss whose rescue date has passed, when a
  registered account's recent buy would deny a new loss sale (TJ-0775). A
  fractional position under 0.01 units bought in the past 30 days can now
  show as LOCKED or UNSAFE (TJ-0899). US buy-check can now say SAFE*
  instead of UNSAFE for a WASHED row whose remaining loss is a short cover
  (TJ-0681). The wash radar's --json rows gain a `forward` key (null
  unless a settled VIOLATION has a forward view).
- tjs harvest: harvestable 'now' falls for a VIOLATION backed by
  registered-account buys (those units move to their age-out bucket). A
  holding with unknown cost leaves the rows and totals and is listed under
  omitted. A holding with a QUOTE ratio other than 1 changes value and
  unrealized result, and price_base is now per book share (new JSON field
  quote_ratio).
- tjs watch --harvest: the state file gains a harvest_omitted list. The
  first run after upgrading compares as before; after that, a run that
  leaves out a position the baseline valued keeps the baseline and warns
  instead of reporting a change.
- tjs div-estimate: a payer whose latest payment was an off-cycle special
  shows its regular frequency, next date and amount, and is listed to
  check with an 'off the <frequency> schedule' flag.
- tjs fees-sum: %NOTNL moves slightly for hand-entered (.tt) trades and
  for zero-price assignments; NON-OPTION now includes futures commissions.
  TOTAL is unchanged.
- TJ-0895: an unknown-cost sale whose net proceeds are below zero now
  shows them signed in MANUAL REPORTING, the CSV `MANUAL` row and `--json`
  `manual_proceeds`. Report it by hand with proceeds 0 and the commission
  as outlays.
- TJ-0896 (US): `tjs carryover` can now show a lower carryover, with rows
  for the years after the last sale year up to the project year. Check the
  carryover entered on the return.
- TJ-0898 (Canada): to have a closed year's T5 box 18 capital-gains
  dividends count in the next year's carry-back room, run `tjs close-year
  --force` in that year's project once, so its lock records `slip_gains`.
  Locks without it behave as before.
- TJ-0904: scripts that call the standalone `taxjson-carryover
  --filed-lock YEAR=PATH` with a YEAR other than the lock's now stop with
  exit 2. Give each lock with its own year.
- TJ-0901: the standalone `taxjson-form-export` without `--year` may now
  label a form with an earlier year than before (the files' own year).
- TJ-0889/TJ-0693: `tjs cash` text and `--json` `tt` lines can now end
  with `at=<book>`. A positive cash flow is now `#CREDIT` and goes in
  `totals.credits`, and `totals.fees` no longer counts it.
- Redacted copies: run `tjs redact --force` again over a copy made by an
  older release if its taxjson.toml used TOML escapes in account /
  query_id values, or if a settings file's comments named a folder of this
  computer. The new copy has placeholders and `<redacted path>` there.
- Installer: an install whose checkout sits on a newer vX.Y.Z tag that is
  not an annotated release on main (a tag made by hand) moves to its
  channel's release on the next installer run; pin a version with
  --channel vX.Y.Z to choose one.
- Installer: a channels.json on main that is not exactly {"stable":
  "vX.Y.Z", "beta": "vX.Y.Z"} now stops the stable / beta install with an
  error instead of picking a release from it; --channel vX.Y.Z or
  --channel latest still installs.
- RENAME lines of one change more than seven days apart, joined into one
  event by a line between them, now stop the run with 'one change has one
  date'. Keep the one line with the date the change took effect (TJ-0941).
- `tjs format-map --write` in a project whose crypto account (or an
  account of the other kind) has exports but no work/<account>_base.json
  now asks for `tjs run` first. After an older release moved a crypto
  RENAME into the securities account's renames.tt, move that line to the
  crypto account's file and run again (TJ-0940).
- A tobase.map pair added back by an older `update-tobase-map --write`
  after you deleted it: delete it again, or comment it out to opt out for
  good. A pair wrongly recorded as '# removed:': delete that comment and
  update again (TJ-0945).
- `tjs run` while `tjs close-year` is running now stops. The busy
  message now reads 'another `tjs run` (or `tjs close-year`) is in
  progress in this project' (TJ-0924).
- A project that a failed close on an older release left frozen ('Nothing
  was written', yet taxjson.toml's inputs_dir commented out and copies in
  the year's inputs/): restore taxjson.toml from git or its backup, delete
  the copies, fix filed/, then close again (TJ-0917).
- No figures move: these fixes change refusals, warnings and staleness
  only.
- Canada, in-kind contributions declared with an INKIND line: the
  registered plan's acquisition for the superficial-loss rule is now on
  the line's date, not the plan's posting date. A loss sold up to 30 days
  before the line's date can now be denied (for good when the plan holds
  the shares at day 30). Re-run `tjs run`. If the plan really acquired the
  shares on its posting date, date the INKIND line on that day (TJ-0925).
- In-kind moves of an option with a contract size other than 100 (a MULT
  line or the row's multiplier) that were valued by an INKIND line's
  per-share price are now valued at the real size. Their deemed gain or
  loss and the plan's cost change; re-run. An INKIND line for an option
  giving both a price and a total that disagree at the real size is now
  refused (named in the problems) (TJ-0933).
- ALLOWLOSS lines with units that are not exactly the sale's (within a
  millionth) now stop the run ('name no single denied superficial loss').
  Write the sale's units as the books carry them (TJ-0921).
- US handoff: a re-described option whose lots differ from the closed
  year's is now a problem (exit 1). Fix the opening's purchase date
  (TJ-0920).
- crypto-sends (TJ-0927): a `--price` saved by an older release has no
  `price_currency` and is still read in the project's current base
  currency. After changing `base_currency` (for example, a copy of a
  Canadian project for a US return), enter those prices again with `tjs
  crypto-sends <acct> --set ID=payment --price P`. Decisions saved from
  now on keep their currency.
- Coinbase (TJ-0981): a crypto-quoted Advanced Trade buy with a fee (Price
  Currency is a coin) gets a cost that includes the fee, and the quote
  coin's proceeds rise by the same amount after `tjs run`. Gains on both
  coins move by the fee's value.
- Kraken (TJ-0991): a ledger swap exported without `amountusd` that has a
  fee in the received coin is valued at the gross units. The spent coin's
  proceeds and the received coin's cost each rise by the fee's value,
  which moves gain between the swap's year and the year of the later sale.
- Coinbase (TJ-0988): a sale with a blank Price whose Total is 0 (the fee
  took the Subtotal) now books its stated 0 proceeds instead of market
  proceeds, so its loss grows. Other blank-Price rows keep their net
  amounts; only the price shown (Subtotal / quantity) and the gross
  change.
- Coinbase (TJ-0992): `tjs run` now stops on a Buy/Sell row with a blank
  Total whose Subtotal does not fit Quantity x Price. Correct the row, or
  fill in its Total, from the Coinbase statement.
- tjs t1135: when foreign cash left one of your accounts in the tax year
  and arrives in another after December 31, re-running can change the cash
  figures. If both accounts are held abroad in one country, the sending
  account's year-end (and maximum) cost rises by that cash's cost. If only
  one of them is held abroad, or they are in two countries, the total now
  reads NOT SETTLED and cash_not_counted names the amount and its cost for
  you to judge (backlog TJ-0946).
- tjs run / tjs checklist: export-coverage may newly show a gap (Warning,
  and attention on the checklist) for a combined account holding two IB
  accounts where one account's statements stop early, or where one .tt
  sale was counted as closing two brokers' positions. Download the missing
  statements, or answer with `tjs checklist --done export-coverage`
  (backlog TJ-0930, TJ-0932).
- Maintainers: scripts/land.sh --no-wait no longer turns on GitHub
  auto-merge. Merge the pull request yourself once its checks pass (the
  message gives the command). With main's rules requiring checks,
  auto-merge is now turned on only after land.sh has seen every check pass
  (backlog TJ-0942).
- TJ-0947: a sale of shares that came from an unknown-cost option's
  exercise or assignment leaves the gain totals and moves to manual
  reporting. Add the option's purchase to get a gain.
- TJ-0948 (taxable spin-off with the fraction paid in cash): dividend
  income rises by the fraction's value, the small capital gain on the
  fraction disappears, and the new shares' cost rises. An RBC .tt hand
  sale of the fraction is still honoured.
- TJ-0949: with two broker accounts' futures in one folder, gains can move
  between years, to the year each account's own position actually closed.
- TJ-0951 (US §355 spin-off): lots bought on or after the record date keep
  their own basis and term, so Form 8949 basis and term can change for
  later sales.
- TJ-0953 (US): a short covered at a loss on the last trading days of
  December and settled in January moves to the next year. A year already
  filed with that loss needs an amendment.
- TJ-0954 (US): an unsized warrant exercise delivered in several rows
  changes the basis of the FIFO lots, and so later sales' gains. The total
  is unchanged.
- TJ-0956 (US): a return of capital that exceeds the basis of many small
  lots raises that year's gain by the missing excess and lowers a later
  sale's gain by the same amount.
- TJ-0990: if you built an opening with `tjs opening ACCOUNT FILE` from a
  holdings file that taxjson-export wrote, run it again with `--force`. A
  Canadian opening's cost changes to the file's base_total_cost.
- TJ-0963: an option position whose contract size is not 100 is now
  skipped by `tjs opening` until ticker.map has its `MULT` line. Add the
  line, then run `tjs opening` again.
- TJ-0960: if an older `--write-missing-history` wrote an oversized
  OPENING cost=unknown line for a pair that also had a broker-marked
  short, edit that line down. The run now sizes it from the long shortage
  only.
- TJ-0957/TJ-0958: `tjs form-export` and the checklist may now say the
  books are incomplete and list sales as NOT in the totals that an earlier
  release counted as covered.
- TJ-0961: US snapshots that earlier releases refused because of a Dec 31
  sale settling in January are now accepted. Re-run `tjs run`.
- TJ-0962: the gains of a year with an option exercised or assigned on or
  before an OPENING snapshot of its shares change when you re-run: the
  premium is now counted once.
- TJ-0964: in projects with two or more taxable accounts, an OPENING
  snapshot plus a rename that only another account reports changes the
  blended gains and holdings on the next run.
- IB, combined statements (combined_broker_accounts = true) and tickers
  traded in two currencies: a purchase that was dropped because another
  account's or currency's Order row covered the same day is booked again
  on a re-run. Gains, ACB and holdings change (TJ-0965).
- IB combined statements with a Ca cancellation: the cancelled fill is now
  the cancelling account's own. A purchase's cost (its commission) and any
  'commission IB kept' FEE row can change on a re-run (TJ-0982).
- IB Flex exports with Settle Date Target: trades take the broker's settle
  date. A sale near December 31 can move to the other tax year on Canada's
  settle-date basis. A statement with a settle date that is unreadable,
  before the trade or more than 31 days after it now stops the parse; fix
  or blank the cell (TJ-0971).
- IB combined statements with a split that is long in one account and
  short in another: the SPLIT ratio is the declared one. Share counts and
  per-share ACB after the split change (TJ-0967).
- IB Flex exports with execution-id columns: alike fills split over two
  statements are both booked. Proceeds, gains and holdings change
  (TJ-0966).
- tjs sanity / tjs opening with an IB statement whose Open Positions are
  Lot rows only now stops with an error. Export the Summary level (the
  default Activity Statement) and use that file (TJ-0969).
- Re-run `tjs run`: a project holding dotted preferred series L or V
  (SYN.PR.L, SYN.PR.V) now books them as separate securities (SYN.PR.L.TO,
  SYN.PR.V.TO) instead of one pool SYN.PR.TO; their gains and ACB move. A
  ticker.map line written for SYN.PR.TO as a workaround may need its new
  spelling. A .tt line that spells such a series with no venue (SYN.PR.V)
  is now kept as written and warned about: write SYN.PR.V.TO.
- Re-run `tjs run`: two generic mappings with different [broker].name and
  the same account number now keep both brokers' identical trades
  (proceeds, ACB and gains rise to include the previously dropped ones).
- Re-run `tjs run`: an income row duplicated across overlapping exports
  can change category (payment in lieu to dividend) when only one copy
  named the Canadian dealer.
- Re-run `tjs run`: an option on a class share with a ticker.map MULT on
  the class root (MULT SYN.B 10) is now sized by it (it was 100 or the
  root's MULT); assignment cost/proceeds and stats move.
- Re-run `tjs run`: a warrant exercise whose share target is renamed by
  ticker.map no longer realizes a loss of the warrants' cost; that cost
  moves into the shares' ACB.
- Re-run `tjs sanity` / `tjs opening` for an RBC Holdings Export holding
  the ticker CASH: the holding now appears (an opening written from such a
  file omitted it).
- A hand-written or edited JSON book whose dividend, interest, withholding
  or fee rows have a date_settle later than their date now converts those
  rows at the paid date's rate (TJ-0974): the converted income amounts
  change. Parser output gives such rows one date, so a project's own run
  is unchanged.
- A .tt DIVIDEND / DIVIDEND_IN_LIEU / TAX line now reads its PRICE column
  (TJ-0984): such a row's per-share amount is no longer 0, and its row id
  changes when PRICE is not 0 (the id now matches a JSON load of the same
  content). A PRICE token that is not a number is now refused with the
  line.
- A .tt file written by an older taxjson-convert-tt from a book with an IB
  trade cancellation booked the cancelled trade (TJ-0985): convert the
  book again, or add `type=trade_cancel` to the end of the cancellation's
  BUYSELL line. The cancelled trade's proceeds and gain then drop out.
- A .tt file converted by an older release from a book that spelled `qty`,
  or that had empty or null times, needs converting again (TJ-0977,
  TJ-0983).
- slip-audit: an interest line that showed 'under-50' can now be 'differs'
  (exit 1, the t5-t3 checklist step not done) when the same broker's T5
  shows other income or its other income brings the total to 50: type box
  13 from the slip.
- slip-audit: a foreign-currency slip's per-box tolerance is now the CAD
  tolerance converted at the year's average rate (smaller for USD), so a
  box that passed before may now differ.
- slip-audit: a slips.toml table with the same box typed twice under two
  spellings ("024" and 24) is now an error: keep one.
- slip-audit --import-cra: a T5 that an older release wrote under a broker
  account that has none of its kind of income, or a T3 placed from an
  equal name-band tie, may be in the wrong broker account: check its
  broker_key. Two equal-figure T5s for two accounts that an older release
  collapsed: import the PDFs again with --write.
- reconcile-slips: a slip CSV row with more cells than its header now
  fails the check until the amount is quoted ("1,000") or written without
  the comma.
- Re-run `tjs run`: after a return of capital that took the ACB / basis
  below the deferred loss it held, the holdings' `deferred_wash` and a
  later sale's `deferred_loss_recovered` in work/ now come out lower (at
  most the ACB left). `tjs stats` / `win-rates` / `winners` / `losers` P/L
  for such sales drops by the amount already consumed. Filed gains and
  taxable amounts do not change (TJ-1017).
- `tjs stats` / `win-rates` figures move in these cases: an assigned put's
  shares sold after a return of capital beyond the ACB (TJ-1006); a
  futures position delivered by an assigned futures option (TJ-1007, lower
  by the premium); a Canadian one-account view next to another account's
  put-delivery short cover (TJ-0994); a spin-off with a same-day return of
  capital of the same amount (TJ-1019). `tjs option-yield` notional and
  yield move for options renamed into a series of another contract size
  (TJ-1003). These are view figures only, never filing ones.
- Canada, `tjs carryover`: when `[carryover] claimed` records less than a
  year's whole loss and the loss rows are in whole cents, the small
  remainder (for example 0.02) is now carried forward and shown as
  available to apply, where an older release showed 0.00. Re-run `tjs
  carryover` before filing (backlog TJ-1018).
- Canada, `tjs estimate` / `tjs plot-tax` / `tjs instalments`: with
  `[[capital_gains_dividends]]` mapping a security that has a dividend and
  a later correction in the year, eligible dividends, the gross-up and the
  estimated tax can go down. They now match a single net payment (backlog
  TJ-1020).
- Scripts that call `taxjson-form-export` directly with gains files from
  more than one year and no `--year` now get exit 2. Pass `--year YEAR` or
  only that year's files. `tjs form-export` is not affected (backlog
  TJ-0993).
- Scripts that call `taxjson-carryover --claimed-year` with a year outside
  1900 to next year now get exit 2 (backlog TJ-1002).
- Canada, grant timing: run `tjs run` after upgrading so each account's
  inventory carries its own share of a pooled written option's recognised
  premium (TJ-1010) and a net-debit write's negative premium (TJ-1012).
  Only harvest --options figures change (planning); booked gains and the
  year's totals do not.
- harvest: when the saved wash-radar reports are from an earlier day,
  harvest now runs the radar live as of today (TJ-1009). Its
  harvestable-now figure for a LOCKED holding can be larger than before,
  as registered-account buys age out of the window.
- Canada, re-run `tjs run`: a broker-marked short covered by a larger buy,
  or an IB sale coded C;O larger than its account's own holding, changes
  gains. The sale of the shares left after such a buy returns to Schedule
  3. A C;O short no longer sells another account's shares, so its gain
  moves to the year it is covered and the other account's ACB and holding
  change. Compare a filed year with `tjs check-filed`.
- Canada, written options: where the same contract is open in two taxable
  accounts and the close is on close timing, or on a pre-since or
  missing-history lot, an assignment's share ACB and a buy-back's gain now
  use the closing account's own average premium. The other account's
  contract keeps its own, so a later close's figure and year can move.
- Canada: work/<account>_gains.json inventory and inventory_year_end can
  now hold a symbol twice, a long row plus short rows carrying `account`
  and `short_pool`, when a short sits beside a long (shares and futures).
  Anything reading those files by symbol should expect it. Gains from this
  display change do not move. The T1135 income of a stock with a marked
  short in another account rises by the compensation left out.
- Canada: a taxable loss whose replacement was bought in a registered
  account and moved to another registered account in parts (out 100, in 60
  and 40) or gathered from two senders into one row is now denied
  permanently: re-run `tjs run`; the denied amount and the gain move
  (TJ-1102).
- Transfer-in coverage: re-run `tjs run`. If a `.tt` sale lies between two
  transfer-ins, or an `INKIND` contribution of a book-value transfer-in
  comes before another transfer-in, `tjs transfers` statuses and the gain
  can move: a transfer-in with no purchase left is booked at its stated
  book value, and one with its own purchase is `.tt_covers` (TJ-1054,
  TJ-1055). Compared with v0.38.0, the second-arrival fix (TJ-0823)
  applies when that purchase is dated after the `.tt` sale; one dated
  before it stays `book_value` unless the purchase line is dated on the
  arrival day.
- A year closed as a single folder, migrated, and with its
  `filed/<year>.json` deleted now shows `lock missing`; check-filed exits
  1 and the checklist's filed-lock step is blocked until the lock is
  restored or `tjs close-year` is run there (TJ-1119).
- `tjs renames` book cost changes for option series written or (US) bought
  at more than one premium; no filed figure moves (TJ-1069).
- An s.86.1 spin-off distributed after the parent was sold: compared with
  v0.38.0, the reduction now moves from the distribution year (a gain with
  proceeds 0) to the year of the sale of the shares it was made on, the
  holding's last sales. An earlier trim's gain does not change. If that
  sale's year is filed, the return needs an adjustment; the run's note
  names the sales (TJ-1026).
- A warrant exercise after a dated RENAME, in an account that held only
  the warrants: the capital loss of the warrants' cost goes away, and that
  cost joins the new shares' ACB, so the shares' later gain falls by it
  (TJ-1132).
- A .tt file written from the books by an older release has no `corp=`
  tokens and keeps booking an s.86.1 reduction as a return of capital.
  Write the file again, or add `corp=<event id> election=rollover_s_86_1
  parent_qty=<parent shares>` to its ADJUST line (TJ-1117).
- RBC, two broker accounts' exports with no Account column in one folder,
  with a merger or spin-off paid partly in cash: re-run. The cash in lieu
  falls to what the broker paid, so the fraction's gain and the cash
  ledger change. Nothing to re-elect.
- RBC, a stock split on a short where the exports mix the layouts with and
  without an Account column, or where an earlier split is under a
  temporary code: re-run. The split's ratio changes, and so do later gains
  and positions.
- Questrade, a parent distributing two securities on the same dates and
  share count: re-run and elect each one with `tjs elect --pending`. They
  have new ids, and an election saved for the merged event no longer
  applies.
- A merger under a taxable election (Canada taxable_disposition, US
  taxable_exchange, reorg_368_boot) whose cash in lieu was paid after the
  exchange: the gain does not change, but the cash ledger's date for that
  cash does, so an FX gain on the currency can move. A .tt CASHBAL between
  the two dates now reconciles.
- Questrade, if you added a RENAME line only because of a false 'looks
  renamed' warning from overlapping downloads: take it out.
- US project with dividend compensation paid on a partly covered short:
  the cover's gain rises and the paid compensation left rises by the same
  amount. Re-run tjs run.
- US project: a cover whose capitalized compensation makes a loss, with a
  re-short within 30 days: the loss is now deferred, so that year's gain
  rises and the replacement short's falls. The 'add it by hand' warning is
  gone: remove any manual adjustment made for it.
- US project: tjs estimate rises by the tax on compensation paid on a
  short that had been netted against a payment received on the same
  security. tjs run first to stamp paid_on_short.
- US project: dil-sum's and dil's payment-in-lieu totals leave out the
  capitalized part, and dil-sum --json adds totals_capitalized (dil
  --json: totals.capitalized_263h).
- US project: readiness may now report compensation-not-settled, which
  makes complete false and makes the checklist's form-export step need
  attention, so close-year refuses without --yes. Add the later rows, or
  use --yes knowing the figure can move.
- Canada and US: a sub-micro position with a real cost sold in parts now
  realizes each sale (Canada's engine: gain rises, the phantom short is
  gone).
- IB, relative to v0.38.0: a CME or Montreal future quoted in an
  Asia-Pacific currency changes date and time on re-run, which can move a
  fill across a year end or a superficial-loss window.
- IB, relative to v0.38.0: overlapping combined statements where one
  account's identical fill was cancelled change gains on re-run; the other
  account's trade comes back and the cancelled one goes.
- fx-cash v2 (--ledger v2): a year where a same-day IB deposit,
  withdrawal, fee or income row shares a day with a timed Forex conversion
  can change on re-run: phantom borrowings and their gains go away.
- taxjson-export --holdings-toml: a holding summed from inputs where not
  all give base_total_cost no longer has base_* fields (it used to show
  one input's figure as the whole holding's). Export the inputs one at a
  time, or pass --base-gains for all of them.
- taxjson-export --holdings-toml: a cost_source = "blended" holding
  another input also holds is now cost_unknown, with no total_cost (it
  used to show a sum of the blended and own-books figures). Export that
  account's snapshot on its own to keep its figure.
- taxjson-export --holdings-toml now exits 2 on a [[holding]] with
  acquired. Use the lot file directly with tjs sanity and tjs opening;
  --report still sums it. A file re-exported by an older release has lost
  its lot dates: go back to the original.
- taxjson-export --holdings-toml now writes cost_currency beside currency
  when the input has it, cost_kind when the inputs agree, and [meta]
  account and broker_account when every input names the same one. A
  consumer that read currency as the cost's currency should read
  cost_currency.
- tjs fetch --positions: run it again after upgrading. A live option not
  in the books yet, on a root with a closed Montreal option, can change
  from .TO / CAD to .US / USD, or lose its currency with a note when
  Questrade's lookup gives no answer.
- Holdings trades arrays: after a split inside a purchase's settle lag, a
  re-run of tjs run can drop entries from a closed round.
- `tjs t1135`: `max_total_cost` can rise for a year with a foreign-cash
  move between two counted accounts of one country, or for a same-day
  round trip at a superficial loss in a foreign-custody account. A move
  with only one end counted now appears in `cash_not_counted` with its
  largest cost: add it by hand if it was held outside Canada.
- `tjs fx-cash --ledger v2`: a year refused because an own move arrived in
  late December and left in early January now computes; `tjs close-year`
  records `early_out` in `filed/<year>.json`.
- `tjs crypto-sends`: a sends.json with same-second sends may have its
  keys moved once (a note names them) and become `schema_version` 2. The
  `crypto_sends.tt` then shows OUT OF DATE until `tjs crypto-sends
  <acct> --write` or `tjs run`. If an export was downloaded again in
  another order before upgrading, check those sends.
- Rates: a Yahoo rate past the fill limit left in
  `~/.currency_price_cache.json` is dropped once when the rates are next
  built. Delete `work/to_base.csv` and run `tjs run` to rebuild at once. A
  row that relied on it may now stop with 'no exchange rate' and needs a
  rate of its own, entered in the base currency as a `.tt` line.
- Generic importer, from v0.38.0: an option closed at price 0 on its
  expiry with no fee column, where the amount is a negative or (on a
  buy-to-close) any commission-sized charge, now books that charge as a
  cost and settles on the expiry day. A Dec-31 expiry's loss moves back
  into the expiry year, and the old +1 'proceeds' becomes -1. Run `tjs
  run` again and compare `tjs sum`.
- Redact: a copy could keep a folder path after a colon or in a file:// URL in a settings
  comment (TJ-1101). Run `tjs redact --force` again before sharing.
- Review figures only; nothing filed changes. Figures that can move after
  upgrading: `tjs stats` and `tjs win-rates` per-class P/L and win/loss
  counts, for two puts sharing one delivery, for an OPENING line dated an
  option split's day (Canada), and for US projects with an OPENING line of
  the stock, a tax_free_355 spin-off of assigned shares, or an earlier
  year's deemed return-of-capital gain.
- `tjs ccd-sum`, `tjs winners` and `tjs losers` CLOSES can move in a
  window that holds a grant-timing write but not its expiry (one fewer) or
  its expiry but not the write (one more, with no premium). A tax-year
  view can list an underlying whose only row is that close, with a gain of
  0.
- `tjs option-yield` of a window can show a larger average notional and a
  lower yield when the series' multiplier is declared on a row after the
  window.
- US spin-off premium: run `tjs run` after upgrading so the cost timeline
  in work/ exists; without one the premium stays on the parent, as before.
- `tjs win-rates 1.5y` and `tjs option-yield 1.5y` now exit 2 instead of
  reporting the tax year.
- `tjs win-rates SYNX` matches SYNX.DE again.
- A US year view, or a Canadian grant-timing year view, can now build
  work/<account>_gains_all.json on demand when the books hold an
  earlier-year return of capital with an assigned option, or an
  earlier-year sold option that can expire in or after the year.
- reconcile-slips, grant timing: re-run `tjs run` so the gains files name
  nil expiries (grant_nil_closes). An option written in an earlier year
  that expired at nil this year then expects a slip row: with none, the
  check now reports MISSING_FROM_SLIP and exits 1, where it was clean
  before. No gain or tax figure changes.
- slip-audit: an amended T3 with no `security` and no [[slip.line]]s no
  longer replaces another T3 of the issuer. If it really amends that
  table, give both the same `security` or comment out the replaced table;
  the audit's notice says so.
- slip-audit: if you applied suggested ROC lines from an earlier release,
  compare `tjs roc-sum` with the T3's box 42. Dated box-42 lines could
  overshoot the slip, a prior year's IB payment could add a prior-year
  ADJUST, and same-day payments could fall short.
- Minimum tax carryover (Canada, with TJ-0719's note): in a year where the
  foreign tax credit takes all of the federal tax, the amount recovered on
  line 40427 falls and the carryover carried forward rises; the tax with
  AMT and carryover does not change. Example shape: a carryover of 5,000
  with years left now recovers only what still lowers tax, and the rest
  stays open. A year already closed with the larger recovery: re-close it
  (`tjs close-year --force`) or enter the carryover from the notice of
  assessment in `[estimate] amt_carryover`.
- `tjs harvest`: the buckets of a VIOLATION with a registered holder can
  move. Only the units a registered account bought in the window of a sale
  today wait; the rest counts as `now`. Planning output only; no filed
  figure changes.
- `tjs wash-radar` and `tjs sell-check`: around a US-only holiday, the
  dates of a renamed TSX listing or a TSX stock with a USD dividend move a
  day later, to the date the engine accepts. Re-run `tjs run` to refresh
  the books.
- `tjs div-estimate`: a payer that changed schedule now forecasts on the
  new one, so its window and next-payment figures move.
- `tjs fees-sum`: %NOTNL moves for a `.tt` option with a `MULT` line.
  TOTAL does not change.
- The checklist's form-export step can move from done to attention on a
  project whose run prints `warning: conservation: ...`. No figure
  changes: run `tjs audit` and check the account's rows for the symbol.
- Canada: a broker sale of .tt units followed by a transfer-in with a
  stated book value is now booked at that value. Figures in such a project
  change on the next `tjs run`: the gain rises (the book value becomes the
  later sale's cost) and the short position goes away. `tjs transfers`
  shows `book_value` instead of `.tt_covers`.
- A project moved by an older `migrate --to-years`, whose filed year holds
  a relative link to a file at the top: repair it with `ln -sfn
  ../../../maps/more.tt 2025/inputs/margin/more.tt` (your own path), then
  `tjs -C 2025 run`. The troubleshooting entry says the same.
- US projects with a §355 spin-off where the broker prints REC and ON n
  SHS: a parent lot bought after the record date keeps its own basis and
  date when the count matches the shares held before the record date. This
  is a change from v0.38.0. When the count matches the pay-date holding,
  or fits neither, every lot still shares it and the v0.38.0 figures stay.
  RBC US projects now get the same check. Run `tjs run` again; a year
  already filed with the older figures may need correcting.
- A US short covered at a loss and delivered in the next year (TJ-0953):
  the gains file gains the key covers_realized_next_year, present only
  when there is one. Nothing else in the file changes.
- A `.tt` file written earlier by `taxjson-convert-tt` holds no cash=:
  write it again from the books so `tjs fx-cash` counts a corporate
  action's cash.
- Canada, close timing, the same written option open in two taxable
  accounts: `tjs list` and `tjs harvest --options` now show each account's
  own premium instead of the average, so the per-account cost and
  unrealized figures move. The total and the filing figures do not move.
- Canada, marked shorts with own moves: compared with v0.38.0 a sale coded
  C;O past the shares an account received matches the control with no
  move. Where the sender keeps shares and sells past them coded C;O, the
  part past its own long now opens a short instead of selling the moved
  shares, under the same note as TJ-1023's filing-figure change.
- Canadian project with a base-cost OPENING on a foreign listing (from
  `tjs opening` on a holdings file): the native books and
  reports/<account>_holdings.toml are back. `total_cost` there is the base
  cost restated at the snapshot day's rate, an estimate of the native
  cost; `base_total_cost` is as stated. Filing figures do not move.
- An option exercised on or before an OPENING snapshot with its shares
  delivered after it: re-run. Against the earlier fix-round build, gains
  on those shares move back to include the premium (v0.38.0 already had
  these figures).
- `tjs find-missing-history` and checklist step 10 can change verdict
  after a re-run: a used-up OPENING cost=unknown line now shows under
  AFFECTS with [!], and old-symbol lines covering a converging rename now
  show COVERED with [x]. Figures do not move.
- IB, combined accounts: a commission refund or a Ca in one account's own
  statement (no Account column) now reaches only that account's row in a
  combined statement. The ACB and gains of such a year change on a re-run.
- IB: a purchase with two alike commission refunds in one statement (same
  date, amount and wording) now has both in its cost, and an overlapping
  copy no longer books it twice. The ACB, later gains and cash change on a
  re-run.
- Canada: re-run `tjs carryover` if you recorded a partial `[carryover]
  claimed` over losses with sub-cent amounts. The carryforward can rise by
  a few cents (0.005 or less per such row) where the old allowance erased
  it. A claim of the filed Schedule 3 total still leaves 0.00.
- Questrade: after upgrading taxjson-fetch, run `tjs fetch --positions`
  again if you hold an option whose series the books show closed. Its
  `.TO` or `.US` suffix and currency in
  `holdings/<account>_live_holdings.toml` can change, with a matching
  change in `tjs sanity`. If the note says the lookup gave no answer, the
  holding has no currency until a later run.
- T1135 with tax_date = "trade": year-end foreign cash cost and
  max_total_cost can change on a re-run when an own move trades in the
  year and settles after it (a double count falls, an omitted amount
  returns). With no arrival in the books, or one end in Canada, a
  previously settled total becomes NOT SETTLED: add the arrival's CASHMOVE
  ... own line, or decide where it was held.
- T1135: max_total_cost can fall when two own departures are in transit at
  the year end, and can flip a filing verdict. A transit between counted
  accounts can become settled when a move next year had named other
  accounts. A move with senders in both countries is now split: the
  counted part is in its row and only the rest is named.
- T1135: a project with a .tt cash line 'its account folder holds several
  books' goes from settled to NOT SETTLED until the line gets at=<book> or
  the file a CASHBOOK line.
- Kraken trades exports: an unpriced coin-to-coin fill with a fee changes
  both legs' value on a re-run, moving gains between the swap's year and
  the later sale's.
- Coinbase: a Buy or Sell row with a blank Total and a Subtotal of 0 that
  does not fit quantity x price now stops the parse; correct it from the
  statement.
- Coinbase with several exports: gains can change where a sale and a rebuy
  shared a compressed second in the repeated hour.
- Export coverage: a combined account can show a new gap that asks for the
  short account's later exports.
- Generic importer: a share buy at a stated $0 with amount = fee stays
  refused, as in v0.38.0: map the row to skip and enter the real cost in a
  .tt file.
- Generic importer: a `MULT` row that v0.38.0 refused can now be accepted
  at the MULT size. A blank fee cell on a $0 option expiry moves that
  expiry's loss back to the expiry year (a Dec-31 expiry: from the next
  year), so compare `tjs sum`.
- Questrade: a chain that holds two securities and a placeholder or
  take-back nothing explains is now UNBOOKED where v0.38.0 merged it into
  one event; book the distributions by hand in a .tt file. Shapes that now
  split into two events get new event ids, so elect them again with `tjs
  elect --pending`.
- Run `tjs run` after upgrading: the fixes to denied-loss tallies,
  same-date US lots and spin-offs read new or corrected engine output.
  Nothing filed changes: gains, ACB and forms are the same.
- Review figures can move in `tjs stats` and `tjs win-rates` (per-trade
  P/L, wins and losses), `tjs option-yield` (avg_notional, annualized) and
  `tjs ccd-sum` / `tjs winners` / `tjs losers` (closes, contracts). This
  applies only to the shapes in the fixes.
- Gains rows can carry two new keys, only in these shapes:
  `deferred_loss_cut` (what a return of capital took of the denied-loss
  tally) and, on a US deemed row, `deemed_lots`.
- A year view can now build `work/<account>_gains_all.json` on demand in
  two more cases: a US project with an assigned option in any taxable
  account and a return of capital in another, and a Canadian pool that
  went short before the year with an option assigned before it. The first
  such view takes a little longer.
- tjs slip-audit --import-cra: a T5 with income no broker account holds,
  where the broker's second account has trades only, used to be written
  under the paid account and is now listed. A table an older release wrote
  that way: check its broker_key's account.
- tjs slip-audit: a T3 naming one listing beside another listing of the
  same root no longer shows false differences on Canadian dividends and
  foreign income. No computed tax figure moves.
- tjs reconcile-slips: a row with an amount of 000 or 050 right after a
  whole number now fails the check until the amount is quoted, as a row
  with surplus cells already does.
- Re-run `tjs run` after upgrading: `tjs harvest` figures move. A
  registered long call's part of an open VIOLATION's loss leaves 'now'
  until the call's buy ages out, which needs the radar's new
  registered_window_last_add field in the saved reports; with several
  taxable accounts, 'now' drops because registered units apply to each
  account's row.
- `tjs harvest` can now stop with exit 2 ('no sheltered_base.json in work/
  ...') when the saved wash-radar reports are of an earlier day or older
  than the books and that file is missing in a project with a registered
  account: run `tjs run` first.
- `tjs wash-radar` / `tjs sell-check`: a US listing renamed into a TSX
  listing with no trade of its own moves its safe-sale date a day later,
  to the date the engine agrees with. `tjs buy-check` of an option
  spelling a same-market share line rewrites can now answer UNSAFE where
  it said SAFE.
- US projects: run `tjs run` again if a negative payment in lieu is dated
  within 31 days after a short's cover, with no short open on the pay
  date. On a short closed within 45 days in the same year, the cover's
  gain falls by the payment (Form 8949 cost rises) and the payment leaves
  `tjs sum`'s payment-in-lieu figure. On any such row the estimate no
  longer subtracts it, so it can rise when the security also received a
  payment. Check the new note "is read as paid on that short": the rows
  carry no ex-date, so this is a reading of the dates, and a charge that
  actually reverses a smaller payment you received leaves the estimate
  high by the tax on it.
- US, Questrade §355 spin-off with the same ON n SHS in two broker
  accounts of one folder (combined_broker_accounts): re-run `tjs run`.
  Figures can move when a lot was bought on or after the record date. A
  later purchase that came with a due bill now gives up its share of
  basis, and its spun-off shares take that lot's date. Your saved election
  stays.

### v0.38.0

- **A redacted copy left in inputs/ is no longer read.** An account
  folder holding a `NAME.redacted.csv` (or `.tt`) that `tjs redact FILE`
  wrote beside its export had those rows booked twice. Run `tjs run`:
  that account's gains, income and holdings drop to their true figures;
  if a return used them, compare. Move the copy out of inputs/ (`tjs
  redact --out DIR FILE` writes it elsewhere).
- **Moves between your own accounts and in-kind moves can change gains
  and costs.** Run `tjs run`, then compare a year already filed (`tjs
  check-filed`):
  - Canada: a merger whose ratio differs by account, after a move of
    your own between two taxable accounts: the share count after the
    merger, the gain on a later sale and the year-end holdings change,
    because they were wrong before.
  - Both countries: where a taxable account's transfer-out is an in-kind
    move to a registered account that the run did not book, and another
    taxable account has a transfer-in of a different quantity of the
    same security within 10 days, that transfer-in is now booked as one
    from outside your books (at its stated book value, else said as
    ATTENTION), not as a move of your own. In Canada the pool's cost
    takes in the stated book value; in the US the receiver's lots carry
    the stated basis, not the sender's. Settle the in-kind move as its
    warning says; the transfer-in's figure no longer changes when you
    do.
  - A taxable leg and a registered leg 11 to 45 days apart are now an
    in-kind move "NOT booked", a warning naming its INKIND line (`tjs
    run --strict` stops). Add that line to book the contribution or
    withdrawal; the contribution's sale or the withdrawal's purchase
    then enters the figures. An unbooked in-kind withdrawal's taxable
    leg is no longer a NO-cost arrival: delete any .tt BUYSELL or
    OPENING added on the old "NO cost" or "missing purchase" advice and
    add the INKIND line instead.
  - US: where a transfer-out had two candidate transfer-ins in your
    other taxable accounts within 10 days, the lots now go to the
    closest-date receiver and the other arrival is booked at its stated
    basis. A pairing that takes part of a row (100 out, 90 in) now
    carries the paired units' lots, so sales that read as a short are in
    the totals, and the run says ATTENTION ... the quantities do not
    pair (`--strict` stops). Moves whose legs differ only by ticker.map
    spelling, or that were delivered in parts, carry lots too.
  - A transfer journal between two listings the run joins (RBC US-dollar
    units, an IB to Questrade listing change) is now a move of your own:
    the per-account books (`<account>.sum`, `tjs list`, `tjs sanity`)
    move the units between the accounts. Canada's pooled filing figures
    do not change; in a US project the lots carry across, so a figure
    there can. Delete any .tt purchase added on the old NO-cost advice.
  - Canada: coins moved between two taxable crypto accounts now show in
    the receiving account's views (`tjs list`, find-missing-history,
    `reports/<account>_holdings.toml`); no filed figure changes. Delete
    any OPENING cost=unknown line written for such a sale.
- **Crypto income and cost can change on a re-run.** Run `tjs run` in a
  project with a Coinbase or Kraken account:
  - Kraken: a reward paid in CAD or EUR whose fee was charged in another
    currency or a coin changes its income (it was the USD net labelled
    as that currency). US projects: a stablecoin reward whose fee was
    paid in another currency now costs its full par value, which lowers
    a later gain on those coins.
  - Coinbase: a reward with no Subtotal and no Price is income at its
    Total less the fee, so the staking income and the coins' cost go
    down by that fee. A Convert with no Subtotal is valued at its Total
    less the fee, with the fee added to the coins received: the proceeds
    of the coins given up and the cost of the coins received both move.
  - A code ticker.map folds into a USD stablecoin with a `GLOBAL` line
    is now US-dollar cash on Coinbase: the stablecoin position goes
    away, and `tjs fx-cash` and the `tjs crypto-sends` pool include it.
  - A Kraken instant trade split across two yearly ledger exports is now
    booked, including one whose receipts (or spends) are themselves split
    between the two: its disposition and acquisition appear, with every
    leg, and the UNBOOKED warnings go. If you entered it by hand in a .tt file, remove that
    entry.
  - `tjs crypto-sends` no longer books a network-fee sale for a send
    that arrived as two deposits in one account: delete the `-fee` line
    an older release wrote to crypto_sends.tt for such a send, and run
    again. Two exports' Send or Receive rows alike but for their ID now
    count as two moves, so crypto-sends can list a send it did not list
    before, which then needs a decision.
  - Rows that were booked now stop the run, naming the line. Coinbase: a
    reward with a negative quantity, Subtotal or Total (remove it from
    the export and enter the correction as .tt lines); a Convert or
    reward whose Total, Subtotal and fee do not add up, or whose fee is
    not less than its Total, and a Convert that charges a fee with
    Subtotal, Total and Price blank (fix the row or download it again).
    Both: two downloads that give one custody row id (a Kraken
    withdrawal or deposit, a Coinbase Send) or one Coinbase reward with
    different content: keep the newer download only, or correct the row.
- **Income and cost read from broker files can change.** Run `tjs run`:
  - Questrade and RBC Direct: a reinvestment reversal now cancels only a
    reinvestment of its own broker account and, at Questrade, its own
    currency. Where one was matched across currencies or accounts, the
    ACB and income change. A reversal with no original of its own
    account and currency is refused: add the missing older export.
  - Questrade: a reinvestment, stock dividend or cash in lieu that a
    later row reverses now drops out of both overlapping downloads even
    when they write its numbers differently (10 and -100 in one, 10.0000
    and -100.00 in the other). One copy stayed booked as phantom units,
    so the ACB of that security and later gains on it change.
  - IB: a dividend or return of capital named by ISIN may move back to
    the listing actually held on its date when the statement has a split
    or ticker change of the other listing; the ACB follows.
  - Webull: one account's downloads pasted into one file can change that
    account's gains, as a day's sale and rebuy now replay in the order
    each download lists them.
  - Generic importer: a key of `[actions]` written with surrounding
    spaces now matches rows, so rows it left unmapped (an UNBOOKED
    warning) are booked.
  - `tjs slip-audit`, a typed T3 with return of capital: if you sold
    units of the fund between its distributions and already pasted the
    older single `ADJUST ... type=roc` line on the last distribution,
    replace it with the new lines (or the fund's own per-distribution
    breakdown). The units sold get a lower ACB, so their gain rises.
  - After a rename-only SPLIT (ratio 0), the new ticker's record-date
    holdings include the renamed shares, so a distribution sized on them
    can change.
- **Canada: a TSX-listed security traded in US dollars can settle a day
  later.** A trade in US dollars on a Canadian market (a fund's
  US-dollar unit, such as QZX.U.TO) whose export prints no settlement
  date (always for IB; for Questrade, RBC or a generic import only when
  the settle cell is blank) now settles on the next day both Canadian
  and US clearing settle, as CDS settles US dollars (tax-logic
  CA-DATE-05). Before, a trade made the day before a US-only holiday
  (Martin Luther King Jr. Day, Memorial Day, Juneteenth, the Fourth of
  July or the Friday it is observed, US Thanksgiving), or on it, settled
  on that holiday. Run `tjs run`: such a trade's cost or proceeds is
  converted at the new day's rate, so its ACB and gain can change, and a
  sale in the last trading days of December can move into the next tax
  year. Canadian-dollar trades and printed settle dates do not change.
- **A `.tt` file written or copied by an older release may need a line
  changed.** Then run `tjs check-tt` and `tjs run`:
  - A warrant or right exercise (from `taxjson-convert-tt`, or copied
    from `tjs events`): write the file again, or add
    `exercise_of=SHARES` to the end of the warrant's ASSIGN line. The
    exercise year's capital loss of the warrants' cost goes away, and
    the shares' cost, and so a later sale's gain, carries it.
  - Dividend, payment-in-lieu, tax or ADJUST lines copied from `tjs
    events`, `divs`, `dil` or `roc`: copy them again, or add `record=`,
    `label=`, `dealer=`, `issuer=` and `type=` by hand. A January trust
    distribution with a December record date moves back to the record
    year, a Canadian dealer's payment in lieu is a deemed dividend
    again, and a `type=roc` ADJUST is return of capital again.
  - An opening balance copied as `BUYSELL DATE 00:00:00 ...`: replace it
    with its `OPENING DATE SYMBOL QTY CUR TOTAL [LOT-DATE]` line (or
    copy it again), which corrects a doubled position or a missing lot
    date.
  - A stock dividend's shares: write the file again, or add
    `type=stock_dividend` to the end of that BUYSELL line. A US sale of
    the old shares then gets its share of their basis.
  - A purchases.tt drafted from IB lots by `tjs find-missing-history
    --write-purchases` in a `tax_date = "settle"` project (Canada's
    default) holds the trade date: change each line's date to the
    settlement date (T+2 before late May 2024, T+1 after, market
    holidays skipped), or draft it again with `--write-purchases
    --force`. A foreign-currency lot's ACB moves by the rate difference
    between the two days, and the superficial-loss window moves with the
    date.
  - A line that gives the same `key=` token twice now stops `tjs run`
    (`tjs check-tt` names it): delete the one you do not mean. If the
    second value was the one you meant, the figures change from what the
    first gave.
  - A purchases draft kept from `tjs find-missing-history ACCOUNT
    --write-purchases` may name a transfer-in's source wrongly, or leave
    out a line the run needs: draft it again with `--write-purchases
    --force`.
- **`tjs t1135` can change: compare it with a T1135 already filed.**
  - The cash an account holds outside Canada now counts toward the
    $100,000 test, a category 1 row at its cost: a crypto account's US
    dollars and USD stablecoins, and the cash of an account whose
    `custodian_country` is outside Canada. A project below the threshold
    before may now need a T1135: run `tjs t1135` again. The cash is read
    on the project's tax_date dates, and its INCOME (and its country's)
    includes the interest credited on it, converted to CAD. A USD
    stablecoin reward is that cash row's income, no longer a `CRYPTO 6`
    row of its own.
  - If `tjs fx-cash --ledger v2` refuses (usually a missing `CASHOPEN
    <Jan 1> USD <units> <cost>` line, `0 0` when none was held), the
    verdict reads NOT SETTLED and the checklist's t1135 step needs
    attention: fix what the ledger names and run `tjs t1135` again. A
    crypto platform's cash shows under country CRYPTO until you set
    `custodian_country` on the account (`"CAN"`: not foreign property).
  - An account held with a custodian outside Canada (a US brokerage
    account): set `custodian_country = "USA"` (ISO alpha-3) under its
    `[accounts.NAME]` table and run `tjs t1135` again. Its Canadian
    issuers' shares become category 6 rows under that country, at the
    account's share of the pooled ACB (its units that day times the
    pool's cost per unit), which can raise the maximum total cost and
    change the filing verdict. Nothing changes while the key is unset.
    In a Canadian project `tjs format`'s per-account reference block now
    lists the key, so a taxjson.toml formatted by an older release shows
    that block as changed until `tjs format` runs again.
  - The cost amounts are now the engine's (`tjs run`'s) pools: they can
    rise for a warrant exercise or an exercised call with a same-day
    sale, and differ for a split inside a trade's settlement lag. A
    foreign futures contract reopened within 30 days of a loss carries
    the denied loss as its cost amount, a row of its own. Either can
    raise the maximum total cost near the $100,000 threshold.
  - INCOME can rise: a dividend paid on Dec 31 that settles in January
    counts in its pay year, and interest rows that name a held foreign
    security (a bond's coupon) are added to that property.
  - A new warning and `! ` line name shares on a Canadian listing whose
    ISIN is foreign. Once you have checked, add `T1135 SYMBOL COUNTRY`
    (or `T1135 SYMBOL CA`) to ticker.map; with the country line their
    cost counts toward the threshold.
  - `--year-wash-only` is ignored, with a note; in `--json`,
    `deferred_wash_not_in_cost` is always `{}` and `full_history_wash`
    always true.
  - `tjs t1135 --json` adds `total_settled`, `cash_accounts`,
    `cash_ledger_status`, `cash_not_counted` and `cash_unknown_cost`,
    and cash rows carry `cash`, `cash_account`, `currency` and
    `cash_why`; existing keys are unchanged. Canada's gains, ACB and
    income figures do not move.
- **Currency gains in crypto-sends and fx-cash can change.** Run `tjs
  run`, then `tjs crypto-sends` and `tjs fx-cash`:
  - crypto-sends (Canada): the US-dollar and stablecoin pool includes a
    Coinbase account's US dollars, so a stablecoin send's currency gain
    and `pool.avg_cost` / `units_now` change for anyone who held USD on
    Coinbase; a coin bought in USD by card or bank, with no USD deposit
    row, now draws on the pool and may show "the USD/stablecoin pool
    went short". A send that drew on a Kraken stablecoin reward whose
    fee was in CAD gets a much lower currency gain. Sends decided self
    (and Kraken Hybrid Earn moves) stay in the pool, so the gains of
    later gifts and payments, and `units_now`, change.
  - With `fx_cash_ledger = "v2"`, a stablecoin gift or payment's
    currency gain belongs to `tjs fx-cash`: put the `fx-cash v2:
    CASHMOVE <date> USD -<units> proceeds=<value>` line crypto-sends
    shows (or `kept` for a self send) in a .tt of the account, and do
    not add crypto-sends' total by hand. If you added it to a v2 figure
    on an older release, take it out. With the default ledger (v1)
    nothing changes: add it by hand. The two figures can differ: v2
    prices against all taxable accounts' US dollars and does not apply
    the superficial-loss rule.
  - Ledger v2: a conversion's commission in the base currency is part of
    its cost (buying foreign currency) or comes off its proceeds
    (selling it), so gains on currency bought from CAD drop by those
    commissions; a v2 figure filed before the upgrade was too high by
    them. A commission charged in the currency sold or bought (a Kraken
    fee in USD on US dollars sold for CAD) is now part of the conversion
    too, not a spend with a gain of its own: for a year with one, run
    `tjs fx-cash --ledger v2` again; the gain moves by about the fee
    times the day's rate less its cost. In a US project, a
    USD-stablecoin reward priced in EUR or another foreign currency no
    longer adds that currency to the pool.
  - Both ledgers: securities moved in kind into or out of a registered
    plan, and transfers arriving at book value, no longer move cash, so
    the figures change, and ledger v2 may now compute where it said NOT
    COMPUTED. An opening balance in a foreign currency (a .tt `OPENING`
    line) no longer takes cash out of the account either: a ledger that
    showed an overdraft or an unreconciled balance on the snapshot day
    may now reconcile, and its figure may change. Ledger v1:
    USD-stablecoin rewards priced in CAD are now taken in as US dollars,
    which changes the USD pool and its ACB.
  - A work/to_base.csv whose rates are to another currency than
    `base_currency` (copied from another project, or older than a base
    change), or that mixes targets, now stops crypto-sends and the
    de-peg rates: delete it and run `tjs run`.
- **Canada: the estimate's foreign tax credit follows forms T2209 and
  T2036.** Run `tjs estimate`, `tjs amt`, `tjs plot-tax` and `tjs
  instalments` again. If the withholding on your foreign dividends is
  more than the federal tax on their share of net income (with 15%
  withheld, a taxable income under about 105,000 in 2025), the federal
  credit drops to basic federal tax x foreign dividends / (net income
  less line 25300); the provincial credit takes the rest, up to the
  province's tax otherwise payable x the same share (Ontario and Alberta
  before their dividend tax credit), and anything beyond that is not
  credited. So the estimate, the instalments built on it and the AMT
  check rise. The provincial share now divides by net income less line
  25300 (it used line 23600). In `--json`, `trace_with.fed_ftc` is now
  the federal credit claimed (it was the foreign tax), with
  `foreign_tax`, `ftc_share`, `fed_ftc_limit` and `prov_ftc_limit`
  beside it; new top-level keys `foreign_tax_creditable`, `ftc_federal`
  and `ftc_provincial`; `ftc_assumed` keeps its value.
- **Canada: the minimum tax's foreign tax credit is s.127.54's, and a
  spin-off's deemed dividend takes no assumed withholding.** In a year
  with foreign dividends where AMT binds, `tjs amt` and `tjs estimate`
  show a higher federal minimum tax, provincial AMT and minimum tax
  carryover. A taxable spin-off's deemed dividend of a foreign issuer,
  in an account whose books carry no TAX row, no longer takes 15%
  assumed withholding: the foreign tax credits fall, and the estimate,
  the instalments and, where AMT binds, the minimum tax and its
  carryover rise, by up to 15% of the deemed dividend. A year closed by
  an older release recorded the lower carryover in `filed/<year>.json`
  (`carryforwards.minimum_tax`), which the next year reads: if AMT bound
  that year with foreign dividends or such a spin-off, close it again
  (`tjs close-year --force`) or enter the carryover from the notice of
  assessment in `[estimate] amt_carryover`. If a broker did withhold on
  the spin-off, add a .tt TAX line. In `--json`, `amt.ftc` is the
  special credit, with `ftc_regular`, `ftc_foreign_taxes` and
  `ftc_income_limit` beside it; `ftc_source` for a mix of TAX rows and
  the assumption starts "TAX rows X + Y assumed" (it said "actual TAX
  rows"); new keys `ftc_basis`, `foreign_tax_from_rows`,
  `foreign_tax_by_assumption` and `foreign_div_noncash`.
- **Canada: the tax on investment income with AMT can drop.** When your
  other income alone owes minimum tax (other income with large
  `carrying_charges` or `deductions`), that top-up is no longer counted
  against the investments: `estimated_tax_with_amt` in `tjs estimate
  --json`, `tjs amt`'s "with AMT and carryover" and `tjs plot-tax`'s
  "With the minimum tax" drop by it. The return's own minimum tax, the
  carryover `tjs close-year` records and the instalments do not move.
  New `--json` keys `amt.base_topup` and `amt.topup_attributed`. The AMT
  CHECK's text changed ("=> TOTAL WITH AMT" is "=> ESTIMATED TAX ON
  INVESTMENT INCOME WITH AMT:", "=> AFTER CARRYOVER" is "... WITH AMT
  AND CARRYOVER:", and the "federal / ON" row is two rows): a script
  that scrapes it should read `--json`.
- **Canada: the foreign tax credit counts each security's withholding
  only up to 15% of that security's own foreign dividends** (ITA
  s.126(7), s.20(11); form T2209), not 15% of all foreign dividends
  together. A security is summed across the taxable accounts that hold
  it. Where one security had more than 15% withheld (a 30% US
  withholding with no treaty form on file) beside a security with less,
  or a spin-off's deemed dividend was in the year, the credit falls and
  the estimated tax rises, by at most the withholding above each
  security's 15%. Run `tjs estimate`, `tjs amt` and `tjs plot-tax`
  again:
  - The --details note names the amount above 15% by security, as
    possibly deductible on line 23200; the estimate does not deduct it
    (a deduction you claim goes in `[estimate] deductions`). The broker
    rounds each 15% up to the cent, so a security can show a cent above
    the limit.
  - A TAX row with no symbol, or whose symbol paid no foreign dividend
    in the year, counts only up to 15% of its account's foreign cash
    dividends that no TAX row names; a note says how much counted.
  - In a year the minimum tax applies, the special foreign tax credit
    can fall with it, and the AMT top-up and the carryover it creates
    can rise: a year already closed keeps what its lock recorded (close
    it again with `tjs close-year --force`).
  - `--json` adds `foreign_withholding_over_cap_by_security` and
    `foreign_withholding_unpaired`; `ftc_source` reads "capped at 15% of
    each security's foreign divs".
- **Canada: in a year the minimum tax applies with foreign tax withheld,
  the minimum tax carryover the year creates and the provincial minimum
  tax change.** Both move only where AMT binds and the special foreign
  tax credit is above the regular federal credit (foreign dividends with
  tax withheld and little regular tax); without foreign tax nothing
  moves. Run `tjs estimate`, `tjs amt` and `tjs instalments` again:
  - The carryover a year creates is now its s.120.2(3) additional tax
    (T691 Part 7 line 14), higher than before by a third of the special
    credit above the regular one: in `tjs amt`'s "Created this year
    (additional tax)", the carryover `tjs estimate` carries forward, and
    what `tjs close-year` records in `filed/<year>.json`
    (`carryforwards.minimum_tax`: `created`, `closing_by_year`,
    `closing`). The year's tax does not change. A year closed by an
    older release keeps the lower figure in its lock, and the next year
    reads it: if AMT bound that year with foreign tax, compare the
    lock's `created` with line 14 of Part 7 of the T691 you filed, then
    close that year again (`tjs close-year --force`) or enter the
    carryover from your notice of assessment in the next project's
    `[estimate] amt_carryover`. In `--json`, `amt.carryforward` and
    `amt.carryover.created` are now that figure (`amt.excess_fed` stays
    the excess), with the new key `amt.additional_tax_sftc_part`.
  - The provincial side follows forms T2036, AB428 and BC428: Alberta's
    minimum tax rises (35% of the minimum amount over basic federal
    tax), as does British Columbia's (its factor of the s.120.2(3)
    additional tax), and Ontario and Alberta may now take a provincial
    foreign tax credit against the minimum tax. So the AMT top-up, the
    tax on investment income with AMT and the instalments' net tax owing
    move; the federal minimum tax does not. `tjs amt --json` adds
    `provincial_ftc_regular`, `provincial_ftc_min_tax`,
    `provincial_ftc_change`, `provincial_amt_base`,
    `provincial_amt_base_kind`, `minimum_amount`, `basic_fed` and
    `additional_tax_s120_2`.
- **Year end and filed years:**
  - `tjs check-filed` (and the drift check in `tjs run`) in a filed year
    whose inputs were frozen from a shared folder may print new notes: a
    shared UTF-16 export with a changed row of the year, a shared generic
    mapping (`<csv>.toml`, `generic.toml`) changed since the close, or a
    shared file it could not compare. `tjs years` may newly mark such a
    year `shared changed` or `shared unchecked`. No figure moves: each
    is a correction in the shared folder that the filed return does not
    have. Compare it with the year's own copy, then amend (copy it in,
    `tjs run`, file the T1-ADJ, `tjs close-year --force`) or keep the
    books as filed.
  - A next year whose `prior_year_record` names a lock from an older
    release with filed dispositions dated outside that lock's year now
    stops in estimate, amt, carryover and handoff. Close that year again
    in its project with `tjs close-year --force --filed-dispositions
    <CSV>`, the CSV holding only that year's sales.
  - A closed year whose lock was deleted (its taxjson.toml still carries
    the freeze note) now stops the next year's estimate, amt, carryover,
    handoff and `fx-cash --ledger v2`: restore filed/<year>.json from
    git or a backup, or close the year again.
  - A closed year with a loss sale sharing its date and time with a
    replacement bought before it: run `tjs close-year --force` for that
    year to rewrite the record's ACB and deferred figures.
  - `tjs carryover` shows less available to apply where a year has its
    own `[carryover] claimed` entry, and in a closed year shows the
    lock's APPLIED and carried balance instead of the books' rebuild, so
    the balance after the year can drop to what close-year carried
    forward.
  - A filed year built on an earlier year's net capital loss that has
    since been closed again with another balance now warns in
    check-filed and the run, and `tjs run --strict` stops. If its return
    applied more than the new balance, it needs a T1-ADJ and a `tjs
    close-year --force` there. A first year closed with no `[estimate]
    other_losses` may get a note giving the net capital loss the books
    carry in: if that loss is yours, set `other_losses` and close again
    (after filing).
  - `tjs years` may newly mark a filed year `shared changed`, and
    check-filed and the run name a shared export that gained rows of
    that year (or a new shared file holding them), a removed duplicate
    row, or a Webull or RBC-dated row that went unjudged before. Compare
    each with its copy in inputs/ (`tjs -C <year> check-filed`); amend
    only if the filed return was missing the row.
  - A year migrated by an older release's `migrate` still shows its
    taxjson.toml as a changed frozen input, because the lock has no
    migrate record: check the edit, then `tjs close-year --force` there
    to record the current file, or leave it. The checklist's filed-lock
    step is now attention for a frozen year whose `[settings]` names
    `inputs_dir` or `tobase_map` again: delete that line, then `tjs
    run`.
  - `tjs handoff` may newly exit 1 on a Dec 30-31 row the broker
    corrected after the close (compare it with the slip and amend for
    the difference it reports), and exits 1 against a record that is not
    the year before the project's (close the year before in its project
    and hand off against that record).
  - Canada: a project on `option_premium_timing = "close"` whose prior
    year's lock records grant timing, or reports a written option's
    premium, gets an ATTENTION row from `tjs option-boundary`, and so
    from the checklist's option-boundary step: set
    `option_premium_timing = "grant"` with `option_grant_timing_since` =
    that year, or amend that year to remove the premium.
  - A `prev` snapshot an older release kept after a `run --account` is
    not marked partial: run fully twice to get a clean `prev`. `tjs
    compare filed` against reports an older release kept shows one
    fees.rpt change once (the "not from a broker export" line for
    checklist_results.json); nothing to do.
  - A year closed as a single-folder project and then migrated (`migrate
    --to-years`): `tjs check-filed` or `tjs years` may now name a shared
    export or decision in ../inputs that changed since its close. No
    figure moves: compare it with the year's copy, then amend or keep
    the books as filed.
- **US: figures that can change.** Run `tjs run`, then `tjs check-filed`
  in a closed year; a drift is reviewed, then amended or the lock
  refreshed with `tjs close-year --force`:
  - `tjs estimate` (and `tjs plot-tax`) falls where a long-term gain or
    qualified dividend lies between the end of the 0% capital-gain band
    and the end of the 12% ordinary band (taxable income of 48,350 to
    48,475 in 2025, 49,450 to 50,400 in 2026), by up to 3% of the part
    in that range.
  - A `[carryover] claimed` entry for the project's own year is now the
    deduction the estimate's carryover after the year and the close-year
    lock count. A lock an older release closed with a claimed entry, or
    with no `[estimate] other_income`, may carry too much: set the
    year's claimed entry (the Schedule D line 21 deduction) and close
    the year again with `tjs close-year --force`. The next year's `tjs
    handoff` flags a claimed entry that differs from the lock's
    deduction.
  - A US lock now records each lot held at Dec 31; one closed by an
    older release has none, and `tjs handoff` checks it by position only
    and says so: close that year again to have its lots checked. An
    opening whose lots differ from the record's makes handoff exit 1
    even when the units and total basis match.
  - Part of a wash-sale replacement lot moved to another of your taxable
    accounts (or journaled to another listing): the basis add and
    holding period split between the parts, so the gain and term of a
    later sale of either part change.
  - A loss on shares sold after a stock dividend, with purchases in the
    window the dividend was paid on, can be disallowed on more shares,
    and the replacement lots' basis changes with it. A sale of entitled
    shares between a stock dividend's ex or record date and its pay date
    has a lower cost, and the new shares carry that basis with the sold
    shares' dates; for a sale in an earlier year or a wash sale the run
    prints an ATTENTION line: move the amount by hand with .tt ADJUST
    rows.
  - A washed loss on a broad-based index option (SPX, XSP, NDX, RUT, VIX
    roots) is now allowed, and the replacement contract's cost falls by
    it; Form 6781's list shows the realized P/L.
  - In a year after a filed year closed with the next January's rows,
    the "wash-sale basis add to the ... sale (filed ...)" row and its
    ATTENTION line go away, and that year's loss is smaller by the add:
    run both years, and correct a return that already has the extra row.
  - Form 8949: a re-short's (d) proceeds and (e) cost both rise by the
    wash-sale loss it carries; (h) and the year's gain do not change.
    The TXF and `sum`'s Part totals change the same way, and a year
    closed on an older release reports drift on the Part I/II proceeds
    and cost lines (the gain lines match).
  - `tjs edge-cases`' "Denied losses carried in positions held at the
    end of <year>" can change in a project on `tax_date = "settle"`
    (Canada too on `"trade"`): a trade on Dec 31 that settles in January
    counts on the project's own dates. No filed figure changes.
- **US: the year's §1256 P/L is split 40% short-term and 60% long-term,
  as Form 6781 splits it.** In a project with futures, options on
  futures or broad-based index options (SPX, NDX, ...), run `tjs run`,
  then `tjs estimate`, `tjs carryover` and `tjs plot-tax`:
  - The estimate on a §1256 gain goes down, because 60% is taxed at the
    long-term rates. NET ST / NET LT and the short- and long-term
    carryover move between the terms; the total carryover does not
    change.
  - A US year already closed with §1256 P/L that carried a loss out: its
    lock carries that P/L all short-term. The next project's `tjs
    carryover` shows "! YEAR: its lock carries ... of §1256 P/L all
    short-term (closed before the 60/40 split)", and the estimate notes
    it. Close that year again with `tjs close-year --force` in its
    project, then check `tjs handoff` in the next project. A lock that
    carried no loss out needs nothing.
  - `--json`: the close-year lock's `carryforwards.capital_loss` records
    `section_1256_gain` (the year's §1256 net) and its parts
    `section_1256_short_term` and `section_1256_long_term`. `tjs
    carryover --json` rows have `section_1256` whenever the year has
    §1256 P/L (before: only with a carryover), its two parts, and
    `section_1256_unsplit` for a lock closed before the split. `tjs
    estimate --json` (US) adds `lt_input`, `section_1256_short_term` and
    `section_1256_long_term`, and `st_input` holds the §1256 short-term
    part, not the whole §1256 net.
- **Inputs that are now refused or read differently:**
  - Shared layout: a spreadsheet in `../inputs/<account>/` stops `tjs
    run` (exit 1) until its CSV conversion sits beside it or it is moved
    out. A subfolder of exports there gets a warning: move its files up
    a level so they are read. That can change a year's figures, because
    those trades were left out.
  - holdings/: a broker positions CSV saved there (an RBC Holdings
    Export, or an IB Activity Statement with Open Positions) is now
    compared by `tjs sanity` and the run's holdings check, so
    differences it skipped can appear. Any other file there makes sanity
    INCOMPLETE until it is moved out or saved as a holdings TOML or
    broker report. An RBC or IB positions file with a row that has a
    quantity and no symbol, a column given twice, or an IB contract size
    of 0 or below is refused; an RBC cost column whose label names a
    currency in lower case or in brackets is read in that currency.
  - `tjs sanity`: a group with a dated holdings file and an undated one,
    where the books changed after the dated file and a file has no
    `[meta] broker_account`, is refused (exit 2); it was compared clean.
    Add `broker_account` to each file's `[meta]`, give the undated file
    an `as_of`, or check one file at a time (`tjs sanity margin=FILE`).
  - `tjs run` stops on a parsed row with an impossible date or a NaN or
    infinite number, naming the file and line, and on an option with an
    impossible expiry or a strike that cannot be written to the 0.001:
    book such a contract in a .tt file under a symbol of your choice.
  - Webull: a file holding two accounts' downloads stops the run: save
    each account's download as its own file, in the folder of the
    taxjson.toml account it belongs to.
  - Generic importer: a mapping with two `[actions]` keys that differ
    only in case or spaces and map to different actions stops the run:
    keep the right line.
  - Questrade: a folder whose exports (in separate files) hold a
    registered plan and a taxable account is refused: move each plan's
    exports into its own inputs/<account>/ folder with the right type.
    Two taxable Questrade accounts in separate files of one folder, or a
    lone export whose Account Type contradicts the folder's type, get an
    ATTENTION line: set `combined_broker_accounts = true` for the
    account if they are meant to be combined, or move the export.
  - Questrade, a US project: a TFSA, RESP, RDSP or FHSA export in a
    `type = "taxable"` account is now booked (beside a margin account's
    exports only with `combined_broker_accounts = true`; such a folder
    was refused). In a `type = "sheltered"` account it gets an ATTENTION
    line (a US return taxes the plan, tax-logic US-BASIS-09), and `tjs
    run --strict` stops until it moves to a taxable account's folder.
    Moving it puts its trades on the return, so the US figures change.
  - `tjs fetch --positions` (Questrade) stops on a malformed positions
    response instead of writing an empty snapshot. A live option's cost
    in holdings/<account>_live_holdings.toml uses the books' contract
    size, so `tjs sanity`'s cost comparison for mini contracts changes;
    an option the books do not hold yet takes the contract size
    Questrade states (a mini contract 10 shares, not 100), and a
    contract adjusted by a corporate action (a root such as SYN1 on SYN)
    is written with `contract_multiplier_unknown = true`, its cost
    unknown until `tjs run` has its trade in the books: run `tjs
    fetch --positions` again for a snapshot that holds an option opened
    since the last `tjs run`, and sanity and `tjs opening` read the
    corrected cost. A manual export named questrade_YYYY.csv is now
    checked for overlap.
  - RBC: the suggested EXTRACT line for US-dollar units has different
    words; a line copied from the old suggestion may match fewer rows:
    compare it with the new one.
  - Questrade Excel exports: convert again a workbook whose header
    repeats a label (the converted file is now refused, like the raw
    one) or whose ids are formatted with leading zeros. `--sheet N`
    picks a sheet named N before the index N. The converter needs the
    xlsx extra (`TAXJSON_EXTRAS=fx,xlsx` on the installer line), which
    now brings openpyxl only, not pandas (pandas still comes with the
    `fx` extra and `all`); nothing taxjson runs needs pandas through
    `xlsx`.
  - An in-kind move valued from a close cache entry whose unit cannot be
    read ("USd") is looked up again online, so its value can change;
    offline the run stops with "cannot be valued" until it is run online
    or an INKIND line is added. Offline `tjs harvest` no longer serves a
    cached quote dated after today or a boolean price, and can stop with
    "no current price in the cache": run once online. A damaged
    rate-cache record (an impossible date) is dropped, and offline runs
    have no rate for its dates until the next online run.
  - A redacted copy made by an older release can still hold a name kept
    by a misread header row (a label word, then a name split over cells:
    `Name,<first>,<last>`), a year turned into 9999, a missing
    prior-year loss record, a missing linked taxjson.toml, or an e-mail
    address in a file name: make it again with `tjs redact --force`,
    then read the whole copy before sharing it. `tjs -C inputs_redact
    redact --check` on a copy laid out as a project also reads its
    settings files, holdings/ and prior/, and exits 1 while the copy
    holds work/ or reports/ from a run of it: delete those before
    sharing.
  - The stand-alone `taxjson-apply-distributions` refuses a book holding
    several accounts' rows: run it per account, or through `tjs run`.
  - `tjs opening --force` refuses a read-only opening_*.tt (`chmod u+w`
    it first) and one that is a link leaving the project (replace the
    link with a copy).
  - A work/ gains file that holds `{}`, or whose DIVIDEND or
    DIVIDEND_IN_LIEU row lacks its amount or a date, is refused by every
    reader of work files: rebuild it with `tjs run`.
  - `tjs elect --redo` no longer clears elections of events that no
    export in the account holds, and is refused for an account with no
    corporate-action export. `tjs elect` keeps a hidden lock file
    `.manifest.json.lock` beside inputs/<account>/manifest.json (the
    project .gitignore's `.*.lock` covers it).
- **Exit statuses that changed** (a script reading the old one should
  test for nonzero):
  - `tjs sanity` exits 2 (was 1) for books it cannot read, an account
    with inputs and no gains file, a project with no gains files at all,
    and an argument it cannot use: a typo'd account, a missing file, a
    malformed ACCOUNT=FILE pair, an account or file in two groups, the
    aggregate form without both an account and a file. A script or cron
    job that read 1 as "positions differ" now sees 2 for these, and when
    none of the configured holdings files could be checked or no
    holdings/ snapshot could be compared; 1 is only a difference in
    positions. An account of taxjson.toml with holdings and no inputs is
    refused as "account 'NAME' has no inputs (inputs/NAME/ is missing):
    nothing to compare" (it read "'NAME' is not an account"; still exit
    2). No figure changes.
  - The views of the books that read work/ (`tjs shares`, `list`,
    `wash-sales`, `gains`, `positions`, `option-boundary`, `roc` /
    `roc-sum`, `divs-sum` and others) exit 2 (was 1) when a work/ file
    cannot be read, and the Error names it work/<file>. So do `tjs sum`,
    `estimate` and `plot-tax` for a work/ gains file (or the
    work/<account>_base.json the capital-gains-dividends map reads), and
    `tjs amt` and `tjs instalments` pass the estimate's 2 through. `tjs
    wash-sales` exits 2 (was 1) when work/ has no gains files, and
    `--explain` when it has no base files: run `tjs run`.
  - `tjs audit` may now exit 1 on books that passed before: a return of
    capital, split, corporate-action row, opening, dividend (gross) or
    withholding of an audited security that differs from its parsed
    broker row fails (`pool row <id> (<ACTION> <SYMBOL> <date>): AMOUNT
    cross-check FAILED ...`). Run `tjs run` and audit again; a mismatch
    that stays after a fresh run is a bug to report.
  - A manifest.json that cannot be read exits 2 (was 1) in every command
    that reads it, and `tjs elect [ACCOUNT] --json` exits 2 (was 0). A
    flag `tjs elect` refuses (a missing `--hint` value, `--set` beside
    `--pending`, an unknown option, `--event` without `--redo` /
    `--reset`) exits 2 (was 1), its message on stderr as a usage error.
  - `tjs winners` / `losers --top 0`, `tjs edge-cases --margin -1` and
    `tjs crypto-sends --set` without `=` exit 2 (was 1).
  - sell-check, buy-check, rebuy and `wash-sales --explain` exit 2 when
    a project with a registered account has no work/sheltered_base.json
    (run `tjs run`). An unreadable ticker.map exits 2 (was 1) in
    buy-check, sell-check and rebuy; harvest and wash-radar exit 2 on a
    ticker.map with a problem line (fix the line the error names);
    `wash-radar ACCOUNT` with an account not in taxjson.toml exits 2
    (was 1) and lists the configured accounts, and so does naming a
    sheltered account (was 1).
  - `tjs check-dates`: an account whose work/<account>_sources.list is
    missing or unreadable is an ERROR, exit 1: run `tjs run` to rewrite
    it.
  - `tjs audit --year` exits 1 for a close-year lock written under the
    other country. `tjs form-export` exits 2 for a manual-reporting row
    in another currency than the return's: run it on the converted gains
    files. The stand-alone `taxjson-t1135` exits 2 for a gains file with
    short/long-term terms (a US run's): pass a Canadian run's.
  - `tjs init` with a file where the year's holdings/ folder goes exits
    1 with "cannot create 2025/holdings/: it exists and is not a
    directory" before writing anything (it exited 2 after writing part
    of the project).
- **`--json`, text and files that changed:**
  - `tjs years --json`: each year has a new key `shared_not_compared`
    (a list of {file, why}; [] when everything was compared), and
    `shared_exports_changed` can also list a generic mapping (`.toml`).
  - `tjs checklist`: the Next line and some details read differently
    (they name the fix for the finding shown, and run-clean leads with
    the .tt refusal), and work/checklist_results.json entries have a
    `do` field. `--json` `commands` for explore drop `tjs staking
    --details` in a project with no crypto account; in a filed year,
    inputs-frozen lists `tjs check-filed` instead of the download
    commands. A script that matched the old Next text needs updating.
  - `tjs sanity`'s concise output no longer ends in one "! N differ;
    ..." line: it prints one `! ` line per kind of difference, each with
    its command. `sanity --details` and its "N discrepancy(ies)." line,
    which the checklist reads, are unchanged.
  - `tjs find-missing-history`: REMOVE / STALE rows have a third column
    with the line's place (`inputs/margin/hand.tt:1`), and the headings
    read "OPENING cost=unknown entries"; a script reading the rows
    should take the first two words. `tjs align --json`, `tjs years
    --diff --json` and `tjs years --json` add `accounts_only_here` /
    `accounts_only_there` (and `differs_from_newest.accounts`).
  - `tjs align --write` no longer brings over an account table that only
    the other year has, nor comments out the table of an account only
    this year has: copy or delete such a table by hand. A taxjson.toml
    an older release wrote with `# type = "..."` commented out: restore
    taxjson.toml.bak or uncomment the line, then `tjs run`.
  - `tjs edge-cases --json`: `allowed_by` has two US values, `lot_sold`
    and `replacement_used`; each `year_boundary.option_expiries` row
    gains `traded`, `units` and `outcome` (an assigned part and an
    expiring part of one series are two rows; a split series is listed
    under its successor). `tjs renames --json` events have
    `denied_not_in_cost`. The gains files have
    `inventory_year_end_basis`.
  - `tjs form-export --json`: `manual_proceeds` counts long rows only;
    the new `manual_cover_cost` totals the short covers whose proceeds
    are unknown, and each manual row carries `direction`, `cover_cost`
    and `short_sale_proceeds` (null). In the 8949 / Schedule 3 CSV, a
    MANUAL short row has blank proceeds and its amount in the cost / ACB
    column.
  - `tjs wash-sales --json` `totals.denied` (and `permanently_denied`)
    adds the rows' cents and can differ by a cent; the old figure, which
    close-year's lock records, is the new `totals.denied_unrounded`. In
    Canada the gains JSON's `wash_window` records gain `date_settle` and
    `loss_settle`, and `days_from_loss` counts settlement dates, so a
    row that settles outside the window but traded inside it is gone and
    one traded before it but settling inside is added (no figure
    changes; an older traces file keeps the old count until the next
    run).
  - `tjs wash-radar --json`: the WASHED section's `title` differs when
    its rows are ALLOWLOSS filing positions, and the radar adds
    `recent_loss_per_unit`, `recent_loss_per_unit_qty` and `recent_buy`.
    `tjs rebuy --json` rows gain `denied_closed`, and `final` can list a
    loss whose re-entry date has passed. buy-check's text "about $2.00
    of it per unit" reads "about 2.00 CAD of it per unit" (USD in a US
    project).
  - Stand-alone `taxjson-t1135 --json`: `income` and `gain` are null
    (not 0.0) for the properties of accounts no `--gains` file covers,
    and so are the per-country totals that include them; a script that
    adds them must handle null (`tjs t1135` passes every gains file).
    New top-level keys `custodians`, `gains_not_given`,
    `foreign_issuer_symbols` and `foreign_issuer_countries`, a
    `custody_account` field on custody rows, and a flag `--custodian
    ACCOUNT=COUNTRY`.
  - `tjs slip-audit --json`: each `suggestions.tt_lines` entry gains
    `shared`, and its `file` may be `../inputs/<account>/slip-audit.tt`.
    `tjs roc-sum` counts typed `.tt` `type=roc` lines as manual, not as
    broker ROC rows.
  - `tjs option-yield --json`: each row and total gains
    `shares_other_currency`; where the shares held cost another currency
    than the options, `yield_basis` is `other_currency` and `yield` /
    `annualized` are null. An option trade coded closing (IB C) with
    nothing held is counted as unknown-cost (in `closed`, `opened` ""
    and pnl null). `tjs leaps --json` / `tjs leaps-sum --json` for an
    empty window gain `taxable_gain` and `sheltered_gain` (0.0).
  - fx-cash's text tables rename ACQUIRED / DISPOSED to UNITS IN / UNITS
    OUT, GAIN(LOSS) to GAIN(LOSS) <base> and ON DEBT to ON DEBT <base>,
    and "All amounts CAD" is gone (`--json` is unchanged). `tjs cash`
    shows an undeclared base-currency move as `#CASHMOVE <date> CAD
    <amount>` with no `?`, and so does the `tt` field of its `--json`
    rows.
  - work/to_base.csv gains a 7th column (the Bank of Canada's
    publication date) on the weekend and holiday rows the Bank fills;
    readers of 5 or 6 columns are unaffected. `tjs run` rebuilds the
    file once its newest rate is over 3 days old (delete it to see the
    change at once); the evidence then names the publication date with
    "history prior date". Rates and amounts do not change.
  - A Kraken account's `reports/<account>_holdings.toml` writes each
    coin as COIN.KR, not COIN, so a tool reading it keyed on bare coin
    names needs .KR keys (portoml already uses them). In an account
    folder holding several exchanges' exports, a coin only Kraken holds
    is written ZZC.KR, and one that only an exchange with no venue
    suffix (Coinbase) holds, or that more than one exchange holds, is
    written bare (ZZC). Coins under 1e-6 units with a cost under 1 are
    left out and listed in a `# left out as dust` comment. Run `tjs run`
    to rewrite the file.
  - `reports/<account>_holdings.toml` after a full `tjs run`: units
    moved in from another of your accounts now have `total_cost` /
    `base_total_cost`, the cost `tjs list` gives, marked `cost_source =
    "blended"`, where they read 0.0; a listing whose currency is not the
    base currency gets `base_total_cost` only. A holding whose cost the
    run does not have (an `OPENING ... cost=unknown` opening, or
    moved-in units after `tjs run --account`) carries `cost_unknown =
    true` and a comment, and omits `total_cost`, `cost_per_share`,
    `base_total_cost` and `base_cost_per_share`, which read 0.0. A
    reader such as portoml now judges moved-in units at the cost you
    file, and should treat an unknown cost as unknown, not zero
    (portoml's load_cost_basis already skips a holding with no
    `base_cost_per_share`). The gains JSON inventory gains
    `unknown_cost: true` on such positions. Run `tjs run` to rewrite the
    file; nothing filed changes.
  - `reports/<account>.sum`: sales of units moved in from another of
    your accounts move from MANUAL REPORTING REQUIRED to a "MOVED IN
    FROM YOUR OWN ACCOUNT" block, and the HOLDINGS REPORT shows
    `unknown` instead of a partial or zero cost for a holding whose cost
    the account's own books lack (an own move's units, an opening at an
    unknown cost). Filed figures do not change.
  - Windowed views outside the tax year: the `--json` `basis` value
    "pre-wash, full history" is "denials within each account, full
    history". `tjs compare --json` adds `partial_now` /
    `partial_before`. audit, journals, renames, cash, slip-audit,
    reconcile-slips and handoff print the stale-books banner when the
    inputs changed since the last clean run, and a project with one
    export saved in two accounts' folders has run-clean at attention and
    a banner on every report until the copy is removed.
  - The native-currency books: after `tjs run`, a dividend whose
    withholding TAX row is in another currency has a different
    `net_amount` in work/<account>_raw.json and _raw_base.json, and in
    the net column of `tjs divs` / `tjs events` (for example 8.00 USD
    where 7.30 was shown for 10 USD with 2.70 CAD withheld at 1.35). The
    gross, the TAX rows, the base books, the .sum and every filing
    figure are unchanged. If you copied such an events line into a .tt,
    copy it again.
  - After `tjs run`, a short covered before your data (its proceeds
    unknown) no longer gets a "superficial-loss check (manual)" /
    "wash-sale check (manual)" warning: `tjs wash-sales`' MANUAL CHECK
    count drops by one for each, and the checklist's wash-reviewed step
    may close if that was its only open item. No filing figure moves.
  - US gains JSON, `option_replacement_warnings`: `option_qty` is the
    contracts bought (whole), and `covered_shares` the shares counted
    against the loss, rounded to six decimals.
  - `tjs format` now replaces two older Canadian description lines in
    taxjson.toml (option_grant_timing_since's "SET ONCE" wording,
    corporate_distributions' README wording) with the current text
    instead of keeping them as notes: a project formatted by an older
    taxjson shows them as changed once in `tjs format --check`. Values
    are not touched.
  - `tjs audit`: run `tjs run` after upgrading, so the .tt line numbers
    (the converted files' new `metadata.source_lines`) and a corporate
    action's statements (its corp file's new `metadata.statements`) are
    there; older work/ files show neither. The `pool trades` counts can
    rise: trades under an earlier ticker and across a corporate action
    are now included, and corporate-action rows are traced instead of
    counted as having no parsed row. `--json` gains `pool_other_rows`
    (checked, tied, untraced, mismatched, rows) and
    `pool_trades.untraced_rows`, and each row with no parsed row carries
    an `origin`; the texts of `scope.source_amounts` and
    `scope.not_checked` changed, and the footer's `not traced` line
    reads "rows with no parsed row (what made each: --details) and a
    sheltered account's rows: re-run only". The stand-alone
    `taxjson-audit` takes a new optional `--dated-events PATH`
    (work/dated_events.state), which `tjs audit` passes when the file
    exists.
- **Figures a planning view shows can move** (planning figures only;
  nothing filed changes):
  - wash-radar (Canada) measures a loss sale made today from its
    settlement date, and LOCKED / EXITABLE / CAUTION clear dates are the
    first trading day whose sale settles after the buy's settlement +
    30: they can move a day or more earlier, and a position can read
    RISK or CLEAR on a Friday where it read LOCKED. US clear dates that
    fell on a weekend or holiday move to the next trading day. Two
    listings booked as one security now use both exchanges' calendars:
    around a holiday only one keeps, a VIOLATION's sell-by date can be a
    day earlier and a re-entry, safe-from or clear date a day later. In
    Canada a row names its buy by the trade date, not the settlement
    date. `tjs watch` may report the moved dates once, and "advisory
    changed" for a LOCKED, EXITABLE or CAUTION row whose buy settled
    after its trade day, where only the buy's date in the text moved.
  - buy-check's "about X of it per unit": in Canada, with more than one
    loss sale in the window, it adds each sale's loss per unit, so it is
    higher ("for the first N units" when the sales differ in size); in a
    US row that mixes a long loss with a short-cover loss, the amount
    and the wait are the long loss's.
  - `tjs harvest` with IBKR running and a QUOTE line remapping a holding
    to a listing in another currency converts IBKR's quote at its own
    currency's rate: value, unrealized and verdict can change.
  - `tjs div-estimate` forecasts change for a symbol renamed during the
    history, a dividend recorded before a split and paid after it,
    withholding posted in another currency than the payment (converted
    at the books' rate, or flagged and not estimated with no rate), a
    refunded withholding (it follows its reversed payment), and combined
    broker accounts holding a short.
  - `tjs stats` / `tjs win-rates`: a US sale with any lot of unknown
    cost no longer counts as a trade, so TRADES, the win rate and
    NET_P/L can drop; in Canada, with an assigned put whose pooled
    shares were sold (in another taxable account, or in part of a larger
    holding), the share trades' P/L gives up their part of the premium,
    so NET_P/L and wins can drop. An option trade coded closing (IB C)
    with nothing held is no longer a written-option trade. On a
    registered account with `transfers = true`, a withdrawal in kind is
    no longer a trade: trade counts, win rates and the table's P/L can
    change, and the withdrawal's P/L is on its own line ("Moved in kind,
    not trades"; `moved_in_kind` in `--json`). Such a withdrawal out of
    a position with an assigned put takes its share of the premium, and
    the later sale's result changes to match; the premium's total is
    unchanged.
  - `tjs list --date` needs a `tjs run` by this release first (it reads
    the new work/.<gains file>.timeline.json files; the first run after
    upgrading recomputes each account's gains). In Canada a symbol held
    in several taxable accounts shows each account's share of the one
    s.47 ACB, not each account's own ACB, and the "OWN ACB" note is
    gone; the `--json` `basis` text reads e.g. "wash-adjusted, as of D
    by settlement date". The row fields are unchanged.
  - `tjs tips` no longer flags TFSA-US-DIV for a payer that a ticker.map
    `T1135 SYMBOL CAN` line (or the ISIN) says is not US-domiciled; a
    payer with no domicile on record reads "if its issuer is
    US-domiciled".

### v0.37.0

- **The holdings export's cost can change for a holding journaled
  between two listings** (a TSX line to its US line, a Norbert's
  gambit). Run `tjs run`: `reports/<account>_holdings.toml` gives it the
  books' cost (`base_total_cost`) and a `total_cost` in its listing's
  currency, at the journal day's rate, where it had a mixed-currency
  or market-value figure. A plan or script that read the export's cost
  reads the new one. Nothing filed changes.
- **A sheltered account's cost can rise after a move between
  brokers.** A move with a trade of the security between its legs'
  dates, or a few weeks after a listing journal with a sale between
  them, was booked as a sale and a purchase at the rows' values; it is
  now netted. Run `tjs run`: the account's `.sum` loses the gains on
  the transfer-out (and journal) dates, and the security's cost (in the
  books and `reports/<account>_holdings.toml`) carries through the
  move. A plan or script that read the holdings export's cost reads the
  new one. Nothing filed changes (a sheltered account is not on the
  return).
- **`tjs t1135` has a CAT column.** Check each foreign property's
  category before you copy the rows to the form: a fund's or trust's units
  can be another than the 2 taxjson takes for a listed security. Set one
  with `T1135 SYMBOL COUNTRY CATEGORY` in ticker.map. Two rows can also
  appear: a holding at a zero cost, and a position of unknown cost sold
  during the year (flagged "unknown ACB"). The threshold figure does not
  change. A script reading `tjs t1135 --json` finds the same keys, plus
  `category` and `category_from` on each property.
- **`tjs t1135` no longer lists a position held only short.** A written
  option bought back or left to expire during the year, or a short sale
  covered, was a property row at 0.00 cost with its gain; it is now named
  in a `! ` line instead, and its gain leaves the per-country GAIN(LOSS)
  total. If you copied those rows to a T1135, compare. Its gain is still
  in `tjs gains`. `--json` adds `short_only_symbols`.
- **`tjs tax-logic` prints a rule's later sentences as items under its
  first.** The rules and their ids are the same; a script that reads the
  text should read `tjs tax-logic --json`, which is unchanged.
- **A crypto account gets a holdings file.** The next `tjs run` writes
  `reports/<account>_holdings.toml` for each crypto account (and copies
  it to `exports/` when `exports_dir` is set). Nothing else changes.
- **Staking commissions can be explained separately from net income.**
  `tjs staking` lists rewards; `tjs staking-sum` totals them by exchange and
  coin. Re-run `tjs run` after upgrading to preserve source reward evidence
  from Coinbase and Kraken. Existing books remain readable, with missing
  commission evidence shown as unknown. This change adds evidence and
  reports; it does not change the reward income or cost calculation.
  `tjs tax-logic --ids` now states the commission assumptions explicitly.
- **Re-run `tjs run` once.** A US project's gains files gain
  `inventory_year_end` (the lots at the end of Dec 31 of the project
  year; nothing filed changes), which `tjs edge-cases` reads for the
  losses carried into the next year: until a gains file is rebuilt it
  says "held at the end of the books, which may be after <year>". Those
  rows get `as_of` `<year>-12-31` and no longer move with later trades;
  a `tax_date = "settle"` project's say "(by trade date)".
- **US `tjs carryover` can rise in the year after a locked year.** A
  wash-sale loss whose replacement another account bought and sold in
  the locked year is now in its year. Re-run `tjs carryover` and compare
  it with the carryover entered on the next return and `[carryover]
  claimed`; the lock is unchanged. `--json` adds `excluded_unknown_cost`,
  `incomplete_years` and a row's `incomplete`, and
  `first_transaction_year` is no longer null for a crypto-only project.
- **A US lock with a crypto `deferred` above 0.** In
  `filed/<year>.json`, `year_end.crypto.<SYM>.deferred` above 0 is a
  cost the run never had. Run `tjs close-year --force` in that year to
  rewrite `year_end` (the figures and the recorded filed dispositions
  are kept); the next year's `tjs handoff` then agrees.
- **Canada: a new lock's `year_end` `deferred` can be smaller** where a
  sale before Dec 31 released part of a deferral. The `acb` is the same,
  and `tjs handoff` compares only the quantity and the cost: nothing to
  do (`close-year --force` is optional, for the label).
- **`tjs close-year --filed-dispositions` refuses a row of another
  year.** A lock an older release closed with such rows has wrong
  `filed_totals` and, in Canada, a wrong net-capital-loss carry: run
  `tjs close-year --force --filed-dispositions <that year's CSV>`. A
  plain `--force` now refuses such a lock.
- **A hand-edited lock with a malformed `return_row`** fails `tjs
  check-filed` (exit 1) and is refused by the next year's readers:
  restore it from git or close the year again. check-filed's OK line
  now says the RETURN row is not recomputed.
- **Exit statuses that changed** (a script reading the old one):
  - `tjs slip-audit` exits 1 (was 0) when something could not be
    compared: `--json` `status` is `incomplete` and `not_compared` lists
    it. The checklist's `t5-t3` step is blocked or attention where it
    was done: re-run `tjs run`, or accept an IB component taxjson does
    not know with `--done t5-t3 --force`.
  - `tjs t1135` exits 1 with an Error (was 0, with income and gain 0)
    for an account with books and no gains file; `tjs sanity` with no
    arguments exits 1 (was 0, clean and complete) for an account whose
    gains file is missing or cannot be read. Run `tjs run`.
  - `taxjson-reconcile-slips` exits 2 (was 0) for a `--gains` file in
    another currency than the country's; `tjs slip-audit` always passed
    converted files.
  - `tjs check-dates` exits 1 (was 0) when a parsed source that
    `work/<account>_sources.list` names is missing (`--json` error code
    `missing`); `tjs renames` exits 1 without books (was 0, "nothing
    pending"); `tjs splits`, `tjs spinoffs`, `tjs renames` and `tjs
    journals` exit 1 on a damaged book or `cross_listings.state` (was 0,
    nothing found). Run `tjs run`.
  - `tjs check-tt` exits 1 for two known-cost OPENING snapshots of one
    symbol on two dates (was 0, "Every line parses."): keep one.
  - `tjs checklist`: with a tobase.map line the run refuses, step
    `format` is `attention` (was `review` or `done`) and `--only format`
    exits 1; the loader's "ticker.map problem" lines no longer repeat on
    stderr.
  - `tjs buy-check`, `sell-check` and `rebuy` refuse a ticker.map or
    tobase.map with a problem line, exit 2 (they answered from the rest
    of the map). Fix the line it names; treat exit 2 as "no verdict",
    not as SAFE.
  - `tjs opening --date` with an unpadded or impossible day exits 1 and
    writes nothing (was 0, and a file the run refused).
  - `tjs align --write --json` without `--all` exits 2 (it asked the
    questions on a terminal); `--write --all --json` prints JSON, not
    prose.
  - `tjs fetch --list` beside an old per-purpose file such as
    `yf_ticker.map` exits 0 (or 2 when no fetcher is installed) instead
    of 2 with the migrate refusal.
- **`--json` that changed:** `tjs transfers` `arrival` can be
  `UNCLASSIFIED` (where a damaged work/ or an unmatched leg read "own
  move"); `tjs watch` adds `incomplete` (while a book in work/ is
  missing it compares nothing: `changes` `[]`, `tracked` and
  `actionable` null; the exit status is still 0); `wash-radar`, `watch`,
  `rebuy`, `buy-check` and `sell-check` print a document with
  `not_applicable` in a project the wash rule does not reach (they
  printed a sentence); `tjs fees` adds `unlisted_small_fees`;
  `taxjson-fees-sum` takes `--until` and its meta has `until`.
- **Figures a view shows can move** (nothing filed changes):
  - `tjs plot-tax` (Canada) credits the foreign withholding the books
    record, as `tjs estimate` does, not 15%: where it is lower the
    months and the year show more tax, and a withholding posted after
    its dividend moves its credit to that later month.
  - US `tjs stats` and `tjs win-rates` count a sale that took several
    lots as one trade: TRADES, WINS, LOSSES, the rate, the averages and
    the largest win or loss can change; NET_P/L does not. `--json`
    `denied_count` and `tainted_skipped` count sales.
  - `tjs fees` totals can rise by the fees under half a cent it left
    out.
  - A `30d` / `6w` / `3m` / `1y` / `mtd` / `ytd` window ends today: rows
    dated after today leave its totals (`all` or the year keep them).
  - US `tjs renames`: `carried.book_cost` is the FIFO basis of the lots
    carried, as the books have it (it was the average cost).
- **`tjs find-missing-history --write-missing-history`** in a project
  whose `tax_date` is not the default may now write an `OPENING ...
  cost=unknown` line it skipped (a sale traded Dec 31 that settles in
  January); after the next `tjs run` that sale moves from the totals to
  the manually reported sales.
- **`tjs redact`:** a copy an older release made from input that held
  ids already shaped like placeholders (`9990…`, `U999…`) may have
  merged or swapped two accounts. Redact again (`--force`).
- **`tjs migrate`** has the same exit statuses; a refused migrate now
  leaves the shared tobase.map as it was.
- **`tjs audit` compares more, so an audit that passed can fail.** It
  now checks each sale's quantity, proceeds, cost, dates, account, term
  and lots against the saved gains file, a base-currency sale's amount
  against the broker's row, and the audited securities' buys. Run `tjs
  run` before `tjs audit` so work/ is fresh; a FAILED line that stays
  after a fresh run is worth a bug report. Nothing filed changes. The
  footer's `fx cross-check` line is now `sale amounts`, with new `pool
  trades` and `not traced` lines. `--json`:
  - with several books, `total_gain` no longer counts the unknown-cost
    (manual) sales; they are in the new `manual_reporting` (`events`,
    `qty`, `proceeds`);
  - `failed` is true when an account was not audited (exit 1), with the
    new `not_audited` and `coverage_failures`;
  - new `pool_trades` (`checked`, `tied`, `untraced`, `mismatched`) and
    `scope`; an event's `tie_out` has `mismatched`, and a manual event's
    `tie_out.ties` is false when it differs (it was always null);
  - an event's `fx` for a base-currency row carries `nominal_net`,
    `computed_net`, `book_net`, `ties` and `priced_by_fill` (it was only
    from, to and native);
  - `--json --no-trace` events have an empty `trace`; a number that is
    not finite is written as null.

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
  folder, instead of "Run `tjs run` first" (the exit is 1, as
  before).
- **`tjs gains` in a project whose only books are crypto exits 0**
  after its "crypto account … is not shown" line (it exited 1 with
  "run `tjs run` first").

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
- **`.gitignore`: anchor the `reports/` line.** The file `tjs init`
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
  ([CONTRIBUTING.md](../CONTRIBUTING.md#adding-a-broker-fetcher-tjs-fetch)).

### v0.25.0

- **Two commands were removed.** The first-run guide command is replaced
  by `tjs checklist` (every step from install to filing), and the old
  map-and-holdings scanning command by `tjs tips` (advice for next year)
  and `tjs ticker-map --suggest` (map lines to add or delete). The
  v0.25.0 section of the CHANGELOG names both. Scripts that called them
  need the new commands.
- `tjs checklist --json` is `schema_version` 2: a step's `stage` is now
  `section` ([settings.md](settings.md#tjs-checklist---json)).
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
- `tjs fetch` moved into the separate taxjson-fetch plugin (installed
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
- "Warning: 1 position at a $0 cost (1 still held): SPNW.TO (margin). Run `tjs find-missing-history`" for a spin-off you elected with `fmv_per_share=0` (now an Info: the $0 value you declared) — fixed in v0.24.2
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
