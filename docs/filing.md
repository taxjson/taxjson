# Filing checklist

The order to run things in before a return goes to the CRA. A US
project's return goes to the IRS in the same order, but its slips, forms
and carryover differ, and the Canada-only commands (`tjs t1135`,
`instalments`, `amt`, `option-boundary`, `slip-audit`) refuse it: see
[A US return](#a-us-return) at the end.
Each step names the command that proves it; a step is done when the
command is clean, not when it has been run. Amending a filed year
(T1-ADJ) is the same list from step 2 with `close-year --force` at the
end.

`tjs checklist --run` is this list as a command, inside the whole
workflow from install to year end (its sections put these steps where
the work happens: the inputs under "Get your files", the clean-up under
"Fill the gaps" and "Check", the slips and filing numbers under
"Results", the lock under "Year end"). Each step below has a detector
that runs the command named beside it and reports done, attention, to
do, blocked, or — for the steps no command can prove — manual, which
you confirm with `tjs checklist --done ID` (the step's id or its
number; marks are kept in `checklist.json`; commit it). `tjs
checklist --section N` shows one section (`--section 6,7` several). `tjs checklist --walk` visits the open
steps one at a time; it needs a terminal (piped or scripted it stops with
`Error: --walk needs a terminal`; `tjs checklist --done ID`, with
`--force` over a finding, is the non-interactive way). `--quick` skips the slow
detectors.

## 1. Freeze the inputs

- [ ] **Full-year broker activity plus January of the next year** in
      every account's folder of exports (`inputs/<account>/`; with one
      folder of exports for every year, `../inputs/<account>/` beside the
      year folders) — `tjs fetch` (the
      taxjson-fetch plugin) where an account is configured for it,
      exports for the rest. December trades settle
      in January and option closes after year end change the year's
      numbers (`option-boundary`, below), so the extra month is not
      optional.
- [ ] **Every broker's exports reach the year end** wherever it still
      holds positions: `tjs run` warns ("<broker> exports for
      <account> end <date> with open positions") when one stops early —
      its later sales, option expiries and income would be missing. If
      the broker had no activity there after that date, `tjs
      checklist --done export-coverage` answers that gap (a new gap or a
      later end asks again).
- [ ] **Sheltered accounts too.** RRSP, LIRA, TFSA and RESP owe no tax,
      but their purchases decide the superficial-loss rule for the
      taxable ones; without them a permanently denied loss is invisible.
- [ ] **Crypto** ledgers and trades for the full year.
- [ ] **T3 box 42 / return of capital** entered as `ADJUST` lines (or
      `[[distributions]]` in taxjson.toml) before trusting any ACB — some funds publish
      the factors only after year end.
- [ ] Commit `inputs/`, `taxjson.toml`, `ticker.map`, every
      `inputs/<account>/manifest.json` and any `.tt` files, so the filed
      books can be rebuilt later.

## 2. Build and clean

- [ ] `tjs run` with no account filter. The DIAGNOSTICS block at the
      top of every `reports/<account>.sum` shows **zero validation errors**.
- [ ] `tjs check-dates` — every trade and settlement date is possible
      for what was traded (crypto any day, futures Sunday evening to Friday,
      US stocks on exchange days plus the overnight session, options and
      Canadian listings on exchange days); no settlement before a trade or
      on a weekend.
- [ ] `tjs sanity` — every account ties to the broker holdings, or the
      only differences are trades after the last export.
- [ ] `tjs find-missing-history` — nothing marked **AFFECTS <year>**.
      Import real confirmations first (`.tt` lines); open at an unknown
      cost (`.tt` `OPENING ... cost=unknown` lines: `find-missing-history
      --write-missing-history`) only the units whose purchase is
      unrecoverable.
- [ ] `tjs renames` — every ticker change is a dated event, and no
      trade in an old ticker after its rename date is left undeclared
      (a `.tt` line `RENAME YYYY-MM-DD OLD NEW late=fold|late=separate`
      in an account's folder says which; `run --strict` stops until
      then), and
      no look-alike rename the exports show is left unbooked
      (`tjs renames --pending`).
- [ ] `tjs journals --pending` — every broker journal between two
      listings of one security is joined, settled with the `.tt` or
      ticker.map line it names, or kept apart by a line of your
      ticker.map (`DISTINCT`).
- [ ] `tjs elect --pending` — no unresolved merger or spin-off
      election.
- [ ] `tjs crypto-sends` — every crypto send that did not arrive in
      another of your crypto accounts is decided: `self` (your own wallet),
      `gift` or `payment` (a disposition at fair value). `tjs run`
      asks at a terminal; headless, `--set ID=...`. Your decisions are
      kept in `inputs/<account>/sends.json` — commit it. The gifts and
      payments become sales in a generated `crypto_sends.tt` (`--write`,
      or the next run): in `inputs/<account>/` of a single-folder project,
      in the year's `work/crypto_sends/<account>/` when the year folder
      shares one folder of exports (a run never writes the shared
      folder); never edit it. Stablecoin gifts
      show a currency gain instead of a sale. With ledger v2
      (`fx_cash_ledger = "v2"`) put each one's `fx-cash v2` line (a
      `CASHMOVE … proceeds=` line) in a `.tt` file of the account and
      `fx-cash` counts it; do not add it again. With the default ledger,
      add it to the year's net FX gain by hand.
- [ ] `tjs audit` — every disposition traced and tied, zero mismatched.
- [ ] `tjs wash-sales` — read every denial. A **permanently** denied
      loss (repurchase in a registered account) is money gone; make sure
      each one is real and not a custody move (`tjs transfers`).
- [ ] `tjs sum` FILING POSITIONS — each `.tt` `ALLOWLOSS` line is a
      loss you claim although the superficial-loss rule would deny it: your
      position, not the rule's test. `tjs form-export` notes it on the
      row it changes (US: the row has no code W; if the 1099-B shows box
      1g for that sale, see docs/tax-rules.md). Keep its reason, be ready
      to support it, and mark the checklist's filing-positions step done
      (`tjs checklist --done filing-positions`); none listed, the step
      is n/a.
- [ ] `tjs edge-cases` — lists every trade that settles in the other
      year, the options and income around Dec 31, and each loss with a
      purchase or sale within a few days of day 30 of its superficial-loss
      window. Read the items marked THE DATE BASIS DECIDES THIS ONE.
- [ ] `tjs option-boundary` (Canada) — confirms whether a written option that
      straddles the year end requires a prior-year amendment, and asks
      about a contract written before `option_grant_timing_since` and
      closed this year: did the year it was written report its premium?
- [ ] `tjs handoff` — last year's closing positions, the trades
      that settled in January, and any prior-year correction are carried
      into this year exactly once (needs last year's `close-year` record).

## 3. Reconcile to what the CRA already has

Canada; a US return: [A US return](#a-us-return).

- [ ] **T5008 slips** from every broker, as CSVs in the year's own
      `inputs/slips/` (in a year folder: `2025/inputs/slips/`, not the
      shared exports folder — slips belong to one tax year; how to get
      them: [brokers.md, "Tax slips"](brokers.md#tax-slips)):
      `tjs reconcile-slips inputs/slips/*.csv` (all brokers' slips
      together) exits clean. The CRA matches Schedule 3 proceeds against
      these; this is the step that prevents a review letter.
- [ ] **T5 / T3 slips**: `tjs slip-audit` agrees with the books.
      Download them from CRA My Account (Tax information slips, one PDF
      per slip) and import them: `tjs slip-audit --import-cra
      <folder> --write`: an amended slip, the issuer's second amendment
      too, replaces the one it amends; or type each slip into
      `inputs/slips/slips.toml`
      (`tjs slip-audit --template` prints one per account), or drop
      IB's dividends report
      (`U*.YYYY.dividends.csv`) in `inputs/slips/`. It compares each box
      with the books' income of the account (a USD slip in USD, with the
      Bank of Canada daily and annual-average conversions side by side),
      lists the payments missing on either side and the accounts with
      income and no slip, and prints the `[[capital_gains_dividends]]`
      entries (box 18, line 17400) and the `.tt` return-of-capital lines
      (T3 box 42; the rest only, where the books hold part of a payment's)
      that bring the books to the slips. Trust units report
      on a T3, often weeks after the T5s; split-share and mutual-fund
      corporations report on a T5. A difference you accept is answered
      per account by `tjs checklist --done t5-t3`. **NR4 slips**: by
      hand.
- [ ] **Foreign tax withheld** from the slips (not the broker rows) for
      the foreign tax credit, line 40500 / Form T2209.

## 4. Produce the filing numbers

Canada; a US return: [A US return](#a-us-return).

- [ ] `tjs form-export` — Schedule 3 rows by property type: Part 3
      line 4 shares and fund units (13199/13200), line 6 options, futures
      and other properties (15199/15300), line 7 crypto-assets
      (15200/15301; 15199/15300 before 2025; a 2024 return's January 1 -
      June 24 dispositions on the Period 1 codes 10689/10690 and
      10693/10694). Each line's totals equal
      the matching row of `tjs sum`'s FOR THE RETURN block, and all
      lines together equal the wash-adjusted realized gain in
      `reports/<account>_wash.sum`. They are not all of line 19700:
      capital gains paid out by funds and trusts go on line 17600 (T3
      box 21) and line 17400 (T5/T5013 box 18), entered from the slips —
      the books carry those distributions as dividends.
- [ ] `tjs t1135` — required when the cost of foreign property
      exceeded CAD 100,000 at any time in the year.
- [ ] `tjs carryover` — net capital losses of other years (line
      25300); record what is actually claimed in taxjson.toml's
      `[carryover] claimed = { YEAR = AMOUNT }` as the 100% loss applied (the line 25300 amount divided by the
      inclusion rate — twice it at 50%).
- [ ] `tjs fx-cash` — gains on foreign-currency cash above the $200
      de minimis (ITA s.39(1.1)). The default ledger says NOT RELIABLE:
      it reads no conversion, deposit or margin balance, so never file
      its figure. The opt-in ledger v2 (`--ledger v2`, under audit) reads
      them and says what it computed or what it needs; mark a computed
      figure reviewed with `tjs checklist --done fx-cash`.
- [ ] Carrying charges for line 22100 — the margin interest you paid:
      the INTEREST PAID line of reports/<account>.sum, per currency
      (CASH INTEREST is the interest earned; `tjs events` lists
      the INTEREST rows; convert a USD figure yourself). Trade
      commissions are not carrying charges: they are already in the ACB
      and proceeds, so do not take the `tjs fees` total to line
      22100 (CRA, line 22100).
- [ ] `tjs estimate` with other income, then `tjs instalments` —
      a sanity check on the tax and on what is still owed against what
      was paid.
- [ ] `tjs amt` — the minimum tax line by line (form T691): whether
      it binds, the carryover it creates, and the carryover of the 7
      preceding years recovered against basic federal tax above the
      minimum amount (ITA s.120.2, line 40427). Enter last years' carryover by year of
      origin in `[estimate] amt_carryover = { YEAR = AMOUNT }` (from the
      notice of assessment) unless last year's close-year lock carries it.

## 5. File and lock

- [ ] Enter the figures (or file the T1-ADJ). Keep `reports/` as the
      working papers — the CRA can ask for the ACB computation years
      later.
- [ ] `tjs close-year` **immediately after filing** (it lists the
      checklist items still open and asks before locking; `--force` when
      re-filing). The lock is what `check-filed`, `option-boundary` and
      every later `run` use to detect drift, and what makes next year's
      T1-ADJ instructions accurate. It also records the year's
      carry-forwards (net capital loss; minimum tax carryover by year of
      origin), which next year's `estimate`, `carryover` and `amt` read
      and its `handoff` checks. Filed with another tool? Pass the sales
      it reported with `--filed-dispositions CSV`: the net capital loss
      carried forward is then the filed one. And it freezes the inputs: the exports
      the filed books were built from are copied into the year's own
      `inputs/`, which the year reads from then on, and `reports/` is
      kept as `filed/<year>/reports/` (`tjs compare filed` shows
      whether a later run still reproduces them). A lock taken before
      the year ended (`--force`) is a snapshot and freezes nothing: the
      year keeps reading the shared exports, so its later trades show
      as drift, and the close after filing freezes the whole year.
- [ ] Commit the `filed/<year>.json` lock, the frozen `inputs/` copy and
      `filed/<year>/reports/`; tag the data repo with the filing date.
      A `.gitignore` written by an earlier `tjs init` has an
      unanchored `reports/` line that also hides `filed/<year>/reports/`
      (`close-year` says so): change it to `/reports/`.

## 6. After assessment

Canada; a US return: [A US return](#a-us-return).

- [ ] Compare the Notice of Assessment with what was filed; put its net
      tax owing into next year's `[instalments]` block
      (`prior_year_net_tax`, then `second_prior_net_tax` the year after).
      If it shows a minimum tax carryover or a net capital loss balance
      that differs from the lock, enter the notice's figures in next
      year's `[estimate] amt_carryover` / `[estimate] other_losses` (explicit
      input wins over the lock; `tjs handoff` names the difference).
- [ ] A late correction (a corrected export, a late election, an
      assignment) saved in the shared `inputs/` does not reach the filed
      year: it reads its own frozen copy, so its figures stay OK. Its
      `tjs check-filed` (and `tjs run` there) names a shared
      export that gained, changed or lost a row of that year since the
      close, a new file there holding a row of that year, and a
      corrected generic mapping (`<csv>.toml`) that reads one.
      Next year's `tjs handoff` (positions and cost at Dec 31
      against the filed books) and `tjs option-boundary` show what
      moves the year-end; a corrected sale (its proceeds, or the cost of
      a lot sold that year) shows in neither. To amend, put the corrected file in the filed
      year's own `inputs/<account>/` (or `tjs elect` in its folder)
      and `tjs run` there: it names the frozen input that changed
      and warns `DRIFTED` when the filed figures moved. Then file the
      T1-ADJ and `tjs close-year --force`. When the amendment moves
      the net capital loss the year carries forward and a later year
      was already filed on the old balance, that close names the later
      year's lock, and the later year's `tjs check-filed` warns: if
      its return applied more than the new balance, amend it too
      (`tjs run` there, a T1-ADJ, then `tjs close-year --force`
      there). `tjs check-filed`
      also catches a taxjson upgrade that moves a filed year.

## A US return

A US project (`country = "usa"`) follows steps 1, 2 and 5 with the US
terms: retirement accounts (IRA, 401(k)) in place of the registered ones,
whose purchases decide the wash-sale rule; Form 1099-DIV box 3
nondividend distributions entered as `ADJUST` lines in place of T3 box
42; Form 1040-X in place of the T1-ADJ. `tjs option-boundary` is
Canada's only (a US premium is taxed at the close). Steps 3, 4 and 6
are these instead (`tjs checklist --run` in a US project names the
US slips and skips the Canada-only steps):

- [ ] **Form 1099-B** (and, for a crypto account's sales from tax year
      2025, Form 1099-DA) as CSVs in the year's own `inputs/slips/`:
      `tjs reconcile-slips inputs/slips/*.csv` exits clean. The IRS
      matches Form 8949 / Schedule D to them. Forms 1099-DIV and
      1099-INT: by hand (`tjs slip-audit` is Canada's only); the
      foreign tax paid is the 1099-DIV's, not the broker rows'.
- [ ] `tjs form-export --form 8949` — Part I short-term, Part II
      long-term, wash sales as code W, a crypto account's digital
      assets on their own boxes from 2025 (`--form txf`: the securities
      rows for TurboTax). The totals equal the matching rows of
      `tjs sum`'s FOR THE RETURN block. Not in them: §1256 contracts
      (futures, broad-based index options), which `tjs sum` lists
      with their P/L for **Form 6781 by hand** (its 60/40 split is what
      the estimate and `tjs carryover` use; contracts open at year
      end marked to market and a loss carryback election are not
      modelled: take them from your 1099-B), and capital gain
      distributions (1099-DIV box 2a, Schedule D line 13), from the
      slips.
- [ ] `tjs carryover` — the short- and long-term capital loss
      carryover into the year (Schedule D lines 6 and 14). Record in
      taxjson.toml's `[carryover] claimed = { YEAR = AMOUNT }` the
      deduction each return took against ordinary income (Schedule D
      line 21, at most $3,000), not a loss applied against gains.
- [ ] `tjs fx-cash` — §988 gains on foreign-currency cash are
      ordinary income; the default ledger says NOT RELIABLE, as above.
- [ ] `tjs estimate` with other income — a sanity check on the tax.
      US estimated tax (Form 1040-ES) and the alternative minimum tax
      (Form 6251) are not modelled: `tjs instalments`, `tjs amt`
      and `tjs t1135` refuse a US project.
- [ ] Before `tjs close-year`, set the year's `[carryover] claimed`
      entry (the Schedule D line 21 deduction the return took) or
      `[estimate] other_income`: the lock counts the deduction from it
      when it carries the loss into next year, and close-year says when
      neither was set.
- [ ] After the return is processed: if the carryover the return
      carried (its Capital Loss Carryover Worksheet) differs from the
      lock's, enter it in next year's `[estimate] other_losses` /
      `long_term_losses` (explicit input wins over the lock; `tjs
      handoff` names the difference).
