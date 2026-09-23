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
- [ ] `taxjson sanity` — every account ties to the broker holdings, or the
      only differences are trades after the last export.
- [ ] `taxjson find-missing-history` — nothing marked **AFFECTS <year>**.
      Import real confirmations first (`.tt` lines); phantoms only for
      what is unrecoverable.
- [ ] `taxjson elect --pending` — no unresolved merger or spin-off
      election.
- [ ] `taxjson audit` — every disposition traced and tied, zero mismatched.
- [ ] `taxjson wash-sales` — read every denial. A **permanently** denied
      loss (repurchase in a registered account) is money gone; make sure
      each one is real and not a custody move (`taxjson transfers`).
- [ ] `taxjson option-boundary` — confirms whether a written option that
      straddles the year end requires a prior-year amendment.

## 3. Reconcile to what the CRA already has

- [ ] **T5008 slips** from every broker: `taxjson reconcile-slips` exits
      clean. The CRA matches Schedule 3 proceeds against these; this is
      the step that prevents a review letter.
- [ ] **T5 / T3 / NR4 slips** against `taxjson divs-sum` and
      `taxjson roc-sum`. Trust units and split-share corps report on a
      T3, often weeks after the T5s.
- [ ] **Foreign tax withheld** from the slips (not the broker rows) for
      the foreign tax credit, line 40500 / Form T2209.

## 4. Produce the filing numbers

- [ ] `taxjson form-export` — Schedule 3 rows; its total must equal the
      realized gain in `reports/<account>.sum` (wash-adjusted).
- [ ] `taxjson t1135` — required when the cost of foreign property
      exceeded CAD 100,000 at any time in the year.
- [ ] `taxjson carryover` — net capital losses of other years (line
      25300); record what is actually claimed in `claimed_losses.txt`.
- [ ] `taxjson fx-cash` — gains on foreign-currency cash above the $200
      de minimis (ITA s.39(1.1)).
- [ ] `taxjson fees` — carrying charges for line 22100 (margin interest,
      data subscriptions). The tool reports them; it does not deduct
      them from any gain.
- [ ] `taxjson estimate` with other income, then `taxjson instalments` —
      a sanity check on the tax and on what is still owed against what
      was paid.

## 5. File and lock

- [ ] Enter the figures (or file the T1-ADJ). Keep `reports/` and
      `reports/exports/` as the working papers — the CRA can ask for the
      ACB computation years later.
- [ ] `taxjson close-year` **immediately after filing** (`--force` when
      re-filing). The lock is what `check-filed`, `option-boundary` and
      every later `run` use to detect drift, and what makes next year's
      T1-ADJ instructions accurate.
- [ ] Commit the `filed/<year>.json` lock; tag the data repo with the
      filing date.

## 6. After assessment

- [ ] Compare the Notice of Assessment with what was filed; put its net
      tax owing into next year's `[instalments]` block
      (`prior_year_net_tax`, then `second_prior_net_tax` the year after).
- [ ] `taxjson check-filed` on every later run: an assignment, a late
      election or a corrected export that changes a filed year is
      amended, not silently absorbed.
