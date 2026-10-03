# Filing checklist

The order to run things in before a return goes to the CRA (or, for a US
project, the IRS — the same steps apply with the form names swapped).
Each step names the command that proves it; a step is done when the
command is clean, not when it has been run. Amending a filed year
(T1-ADJ) is the same list from step 2 with `close-year --force` at the
end.

`taxjson checklist` is this list as a command. Each step below has a
detector that runs the command named beside it and reports done,
attention, to do, blocked, or — for the steps no command can prove —
manual, which you confirm with `taxjson checklist --done ID` (marks are
kept in `checklist.json`; commit it). `taxjson checklist --walk` visits
the open steps one at a time; `--quick` skips the slow detectors.

## 1. Freeze the inputs

- [ ] **Full-year broker activity plus January of the next year** in
      every `inputs/<account>/` folder — `taxjson fetch` where an account
      is configured for it, exports for the rest. December trades settle
      in January and option closes after year end change the year's
      numbers (`option-boundary`, below), so the extra month is not
      optional.
- [ ] **Sheltered accounts too.** RRSP, LIRA, TFSA and RESP owe no tax,
      but their purchases decide the superficial-loss rule for the
      taxable ones; without them a permanently denied loss is invisible.
- [ ] **Crypto** ledgers and trades for the full year.
- [ ] **T3 box 42 / return of capital** entered as `ADJUST` lines (or
      `distributions.map`) before trusting any ACB — some funds publish
      the factors only after year end.
- [ ] Commit `inputs/`, `taxjson.toml`, `ticker.map`, every
      `inputs/<account>/manifest.json` and any `.tt` files, so the filed
      books can be rebuilt later.

## 2. Build and clean

- [ ] `taxjson run` with no account filter. The DIAGNOSTICS block at the
      top of every `reports/<account>.sum` shows **zero validation errors**.
- [ ] `taxjson check-dates` — every trade and settlement date is possible
      for what was traded (crypto any day, futures Sunday evening to Friday,
      US stocks on exchange days plus the overnight session, options and
      Canadian listings on exchange days); no settlement before a trade or
      on a weekend.
- [ ] `taxjson sanity` — every account ties to the broker holdings, or the
      only differences are trades after the last export.
- [ ] `taxjson find-missing-history` — nothing marked **AFFECTS <year>**.
      Import real confirmations first (`.tt` lines); list in
      `missing_history.json` (`find-missing-history
      --write-missing-history`) only the sales whose purchase is
      unrecoverable.
- [ ] `taxjson renames` — every ticker change is a dated event, and no
      trade in an old ticker after its rename date is left undeclared
      (a dated `RENAME OLD NEW YYYY-MM-DD late=fold|late=separate` line
      in `ticker.map` says which; `run --strict` stops until then).
- [ ] `taxjson elect --pending` — no unresolved merger or spin-off
      election.
- [ ] `taxjson crypto-sends` — every crypto send that did not arrive in
      another of your crypto accounts is decided: `self` (your own wallet),
      `gift` or `payment` (a disposition at fair value). `taxjson run`
      asks at a terminal; headless, `--set ID=...`. The gifts and
      payments are written to `inputs/<account>/crypto_sends.tt` (`--write`,
      or the next run); commit it with `sends.json`. Stablecoin gifts
      show a currency gain instead of a sale — add it to the `fx-cash`
      figure.
- [ ] `taxjson audit` — every disposition traced and tied, zero mismatched.
- [ ] `taxjson wash-sales` — read every denial. A **permanently** denied
      loss (repurchase in a registered account) is money gone; make sure
      each one is real and not a custody move (`taxjson transfers`).
- [ ] `taxjson edge-cases` — lists every trade that settles in the other
      year, the options and income around Dec 31, and each loss with a
      purchase or sale within a few days of day 30 of its superficial-loss
      window. Read the items marked THE DATE BASIS DECIDES THIS ONE.
- [ ] `taxjson option-boundary` — confirms whether a written option that
      straddles the year end requires a prior-year amendment.
- [ ] `taxjson handoff` — last year's closing positions, the trades
      that settled in January, and any prior-year correction are carried
      into this year exactly once (needs last year's `close-year` record).

## 3. Reconcile to what the CRA already has

- [ ] **T5008 slips** from every broker, as CSVs in `inputs/slips/`:
      `taxjson reconcile-slips inputs/slips/*.csv` (all brokers' slips
      together) exits clean. The CRA matches Schedule 3 proceeds against
      these; this is the step that prevents a review letter.
- [ ] **T5 / T3 / NR4 slips** against the TAXABLE line of `taxjson divs-sum` and
      `taxjson roc-sum`. Trust units report on a T3, often weeks after
      the T5s; split-share and mutual-fund corporations report on a T5,
      and its box 18 capital-gains dividends go on line 17400 (taxjson
      books them as ordinary dividends — see KNOWN_ISSUES).
- [ ] **Foreign tax withheld** from the slips (not the broker rows) for
      the foreign tax credit, line 40500 / Form T2209.

## 4. Produce the filing numbers

- [ ] `taxjson form-export` — Schedule 3 rows by property type: Part 3
      line 4 shares and fund units (13199/13200), line 6 options, futures
      and other properties (15199/15300), line 7 crypto-assets
      (15200/15301; 15199/15300 before 2025; a 2024 return's January 1 -
      June 24 dispositions on the Period 1 codes 10689/10690 and
      10693/10694). Each line's totals equal
      the matching row of `taxjson sum`'s FOR THE RETURN block, and all
      lines together equal the wash-adjusted realized gain in
      `reports/<account>_wash.sum`. They are not all of line 19700:
      capital gains paid out by funds and trusts go on line 17600 (T3
      box 21) and line 17400 (T5/T5013 box 18), entered from the slips —
      the books carry those distributions as dividends.
- [ ] `taxjson t1135` — required when the cost of foreign property
      exceeded CAD 100,000 at any time in the year.
- [ ] `taxjson carryover` — net capital losses of other years (line
      25300); record what is actually claimed in `claimed_losses.txt`
      as the 100% loss applied (the line 25300 amount divided by the
      inclusion rate — twice it at 50%).
- [ ] `taxjson fx-cash` — gains on foreign-currency cash above the $200
      de minimis (ITA s.39(1.1)).
- [ ] Carrying charges for line 22100 — the margin interest you paid,
      from the broker statements (`taxjson events` lists the INTEREST
      rows; the CASH INTEREST line of reports/<account>.sum nets credit
      against debit interest, so it is not the amount paid). Trade
      commissions are not carrying charges: they are already in the ACB
      and proceeds, so do not take the `taxjson fees` total to line
      22100 (CRA, line 22100).
- [ ] `taxjson estimate` with other income, then `taxjson instalments` —
      a sanity check on the tax and on what is still owed against what
      was paid.
- [ ] `taxjson amt` — the minimum tax line by line (form T691): whether
      it binds, the carryover it creates, and the carryover of the 7
      preceding years recovered against regular tax above the minimum
      (ITA s.120.2, line 40427). Enter last years' carryover by year of
      origin in `amt_carryover.txt` (`YEAR AMOUNT` lines, from the notice
      of assessment) unless last year's close-year lock carries it.

## 5. File and lock

- [ ] Enter the figures (or file the T1-ADJ). Keep `reports/` and
      `reports/exports/` as the working papers — the CRA can ask for the
      ACB computation years later.
- [ ] `taxjson close-year` **immediately after filing** (`--force` when
      re-filing). The lock is what `check-filed`, `option-boundary` and
      every later `run` use to detect drift, and what makes next year's
      T1-ADJ instructions accurate. It also records the year's
      carry-forwards (net capital loss; minimum tax carryover by year of
      origin), which next year's `estimate`, `carryover` and `amt` read
      and its `handoff` checks.
- [ ] Commit the `filed/<year>.json` lock; tag the data repo with the
      filing date.

## 6. After assessment

- [ ] Compare the Notice of Assessment with what was filed; put its net
      tax owing into next year's `[instalments]` block
      (`prior_year_net_tax`, then `second_prior_net_tax` the year after).
      If it shows a minimum tax carryover or a net capital loss balance
      that differs from the lock, enter the notice's figures in next
      year's `amt_carryover.txt` / `[estimate] other_losses` (explicit
      input wins over the lock; `taxjson handoff` names the difference).
- [ ] `taxjson check-filed` on every later run: an assignment, a late
      election or a corrected export that changes a filed year is
      amended, not silently absorbed.
