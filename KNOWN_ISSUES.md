# Known Issues and Errata

Known bugs, limitations, and deferred-fix items in taxjson. Each entry describes the current behavior, why it isn't fixed yet, and what evidence would be needed (or what work is required) to address it. Open a PR or attach a sample CSV to graduate any of these.

The codebase has been through seven audit cycles; everything listed here was triaged and deliberately left in place rather than overlooked.

---

## Brokerage parser limitations

### RBC dividend withholding-tax gross-up
- **Where:** `src/taxjson/lib/brokerages/rbc_direct.py:_build_dividend`.
- **Current behavior:** Dividend rows containing `"NON-RES TAX WITHHELD"` are grossed-up at a flat 15% (`gross = net / 0.85`) regardless of the security's actual domicile / treaty rate.
- **Why deferred:** the RBC CSV ships only a net amount with no separate withholding column, no foreign-country indicator, and no ticker-to-domicile lookup. Computing the correct rate per row requires external data (a ticker → domicile mapping plus the applicable treaty rate). That's an AI/web-lookup problem, not a parser problem.
- **Workaround:** US-domiciled holdings dominate most Canadian users' RBC activity and use the 15% US treaty rate, so the default is accurate for the common case. For other domiciles, hand-edit the affected rows after parsing.

### Kraken staking emits `net_amount=0` (pre-2026 exports only)
- **Update (2026-09):** ledgers exported since 2026 carry `amountusd`; the parser prices both reward legs from it (income and cost basis at Kraken's own credit-time valuation), so this only applies to older exports without the column.
- **Where:** `src/taxjson/lib/brokerages/kraken.py:_build_staking_reward`.
- **Current behavior:** Kraken's `kr_ledgers.csv` has no price column on staking-reward rows, so the parser can't populate `net_amount` from the CSV alone. Staking rows ship with `price=0` / `net_amount=0`.
- **Why this is the design (not a bug):** the pipeline runs `taxjson-fill-crypto` between `taxjson-sort` and `taxjson-convert-currency` precisely to backfill these from a historical-price cache. The filler at `fill_crypto_prices.py` only fills when `abs(tx.price) < 1e-8`, so non-Kraken rows with a real price are left alone.

### IB ISIN→market map `IE → L` is wrong for non-LSE IE-domiciled ETFs
- **Where:** `src/taxjson/lib/brokerages/ib_extractor.py` — `isin_map = {... 'IE': 'L' ...}` in the Dividends and Withholding Tax branches (the Corporate Actions and Transfers branches derive suffixes via `_ib_currency_ext(currency)` instead — corrected 2026-09 round-five audit).
- **Current behavior:** every Irish-domiciled (ISIN prefix `IE`) security is mapped to a `.L` (LSE) market suffix. Partially mitigated since the income-reattribution pass: DIVIDEND / DIVIDEND_IN_LIEU / TAX rows are re-bound to the suffix of the position actually held for that ticker in the statement (`_reattribute_income_to_holdings`), so income no longer lands on a phantom `.L` symbol when the shares are held under another suffix.
- **Why deferred:** the user holds no IE-domiciled ETFs, so the bug doesn't fire on their data. Most IE-domiciled ETFs trade in EUR / multiple currencies, not all on LSE; a real fix needs an ISIN → exchange lookup or a per-ticker override.
- **Workaround:** users who hold IE-domiciled ETFs should add a `ticker.map` GLOBAL rule rewriting the parsed `.L` symbol to the correct market suffix.

### IB cash-in-lieu row wording is unverified
- **Where:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_IB_CIL_RE` in the Corporate Actions branch.
- **Current behavior:** a Corporate Actions row whose description contains the phrase `cash in lieu` (any case, anywhere after the leading `TICKER(ISIN)` token) with a negative Quantity is booked as a sale of that fractional quantity for the row's Proceeds (Value when Proceeds is absent), and the fraction is folded into the matching split's leg-derived ratio so the pool ends on whole shares. The regex was written against a synthesized row (`TINY(US…) Cash in Lieu of Fractional Shares (TINY, TINY CORP, US…)`, quantity `-0.3333`, proceeds `3.10`) — no real IB statement with such a row was available.
- **Risk:** if IB words the row differently (no `cash in lieu` phrase, or the fraction/cash in other columns), the row falls into the unhandled corporate-actions tally (warned at end of parse) and the fractional dust stays in the pool — the pre-fix behavior, not a silent mis-booking.
- **Evidence needed:** a real IB Activity Statement CSV containing a reverse split (or merger) with its cash-in-lieu row; attach it to graduate this item.

### IB `Trades / Forex` conversions are not modeled
- **Where:** `src/taxjson/lib/brokerages/ib_extractor.py` — the Trades branch, asset category `Forex` (rows like `Trades,Data,Order,Forex,CAD,U1,USD.CAD,"…",<qty>,<T. Price>,…`).
- **Current behavior:** an explicit currency conversion is COUNTED as a recognized non-event (the calmer `taxjson-brokerage` note: `Trades/Forex (currency conversion, not modeled — KNOWN_ISSUES)`) and not translated. No phantom `USD` / `CASH.USD` asset is emitted — doing so would put a fake position in the book.
- **Why deferred:** `taxjson fx-cash` (`src/taxjson/bin/taxjson_fx_cash.py`) reconstructs foreign-cash ACB from the security cash flows in the taxable books, and its docstring assumes broker CSVs carry no explicit conversions — IB's do (this section), so on an IB account the ledger is asked to spend currency it saw acquired only through trades and overdrafts on the conversion side. Consuming Forex rows properly means booking each as a disposition of the sold currency at the conversion rate AND an acquisition of the bought one, together with the cash deposits/withdrawals the same statement lists — half of that (conversions only) would still overdraft.
- **Evidence / work needed:** extend the fx-cash ledger to read Forex rows plus the `Deposits & Withdrawals` section as currency acquisitions/dispositions; until then the IB Forex count in the parse note is the size of the gap.

### Kraken fiat conversions are not modeled
- **Where:** `src/taxjson/lib/brokerages/kraken.py` — `_parse_trades` (a fill whose BASE is fiat after stablecoin folding: `USD/CAD`, `USDC/USD`, `USDT/CAD`) and `_build_instant_trade` (a `spend`/`receive` pair whose both legs are fiat: USDC dust swept to USD, USD → CAD).
- **Current behavior:** counted as recognized non-events (`forex conversion … not modeled — KNOWN_ISSUES`). Previously each emitted a BUYSELL of a phantom `USD` / `CAD` asset (the fiat base treated as the traded security), which put a fake position in the crypto book and a nonsense trade in the gains report.
- **Why deferred:** same reason as the IB item above — foreign-cash gains live in `taxjson fx-cash`, which does not read conversion rows yet. Stablecoin↔USD swaps are additionally a wash by construction (folded 1:1 for pricing).

### Questrade `commission` vs everyone else `fee`
- **Where:** `src/taxjson/lib/brokerages/questrade.py`.
- **Current behavior:** Questrade transactions emit a `commission` key; IB / RBC / Webull / Kraken / Coinbase all emit `fee`.
- **Why this isn't a bug:** cost-basis math is correct because downstream sums both fields (`core.py`'s `_effective_fee_for_trace` uses `tx.commission + tx.fee`). The inconsistency is cosmetic — per-row reports that itemize one column show the values under different headers across brokerages.
- **Why deferred:** pure refactor with no behavioral change. Touching every test and downstream consumer for a cosmetic split isn't worth the churn.

---

## Cross-parser asymmetries (from the 2026-06 flow audit)

Capabilities one broker parser has that a comparable one lacks. The ones below are deferred because they need a real broker sample to implement safely, or are a design decision.

### Webull does not handle option expiry/assignment
- **Where:** `src/taxjson/lib/brokerages/webull.py` — only `action_raw in ('BUY','SELL')` rows are processed.
- **Current behavior:** A Webull option that expires/gets assigned under a non-BUY/SELL action code is skipped (the long position never closes → phantom). The skip is at least COUNTED now (the parser's skipped-actions summary names the unhandled code), and settlement dates are correct — the CSV Date column IS the settlement date, with the trade date back-computed era-aware (fixed in the 2026-07 date-semantics audit). IB/Questrade/RBC all distinguish expiry/assignment.
- **Why deferred:** no Webull options-with-expiry CSV sample on hand to confirm the action-code/field layout; implementing blind risks mis-parsing. Provide a Webull options statement to graduate this.

### Questrade emits no standalone INTEREST or withholding-TAX rows
- **Where:** `src/taxjson/lib/brokerages/questrade.py` — strips `TAX WITHHELD`/`NON-RES` only as description-key noise; no TAX/INTEREST emission.
- **Current behavior:** IB and RBC emit dedicated TAX (foreign withholding) and INTEREST records; Questrade does not, so a Questrade account's non-resident-tax-withheld dividend or interest credit is not recorded as such (foreign-tax-credit / interest income under-reported).
- **Why deferred:** needs a Questrade CSV showing the interest and withholding row formats to parse them correctly.

### RBC in-kind transfers are emitted but excluded from taxable accounts (by design)
- **Where:** `src/taxjson/lib/brokerages/rbc_direct.py:_build_transfer` (added 2026-06); `taxjson-brokerage` drops TRANSFER rows unless `--transfers`, driven by `transfers` in `[accounts.<name>]`.
- **Current behavior:** RBC now emits a TRANSFER for an in-kind security move (Activity `Transfers`, e.g. a DTC transfer-in). For sheltered accounts (`transfers = true`) it's kept; for **taxable** accounts `transfers` stays **off**, so the row is dropped.
- **Why this is intentional (decided 2026-06):** taxable cost basis must be computed from *actual* buys and sells — a transfer-in carries no reliable ACB (RBC ships book value 0), so accepting it would fabricate basis. Dropping it instead leaves the position looking short until the user supplies the real acquisition history; that phantom short is the **correct signal** (surfaced by `taxjson-missing-history`) that actual buys are missing, not something to paper over with a transfer. Do not flip the taxable default.

### Questrade stock dividends enter the book at $0 cost
- **Where:** `src/taxjson/lib/brokerages/questrade.py` — the `DIS` + stock-dividend branch (added 2026-08, commit 40d51bb).
- **Current behavior:** a STOCK DIVIDEND row (split-share corps paying non-cash share dividends) is parsed as a zero-cost, zero-cash BUYSELL so the delivered shares exist in inventory (previously the row was silently discarded and the position went phantom-short at the next full sale). The taxable amount of a stock dividend is the fund's *declared* amount, which the CSV does not carry, so the shares enter at $0 cost and a stderr `NOTE:` names the symbol, date, and share count.
- **Impact:** registered accounts — none. Taxable accounts — ACB is understated (gain overstated at sale) until the declared amount is supplied; the zero-basis walk also surfaces the position via `taxjson find-missing-history`.
- **Workaround (the intended flow):** add the fund's declared per-share amount for the record date to `distributions.map`; `taxjson run` converts it into the ACB-raising ADJUST.

### IB settlement T+2→T+1 cutoff is hardcoded to the US/CA date `2024-05-28`
- **Where:** `src/taxjson/lib/brokerages/ib_extractor.py` — per-currency cutovers (US 2024-05-28 / CA 2024-05-27) hardcoded in the settle-date back-computation.
- **Current behavior:** the US/Canada T+1 transition date is applied to all IB venues. EU moved to T+1 on 2027-10-11, so non-US/CA IB trades get T+1 settle dates years too early, which can shift a Dec/Jan trade into the wrong tax year under `--tax-date settle`.
- **Why deferred:** only US/CA IB venues have been exercised in practice, so it hasn't fired; a real fix needs a per-venue settlement calendar.

---
---

## Engine scope (documented out-of-scope rules)

### US: §1091(e)(1) — a long SALE within the window of a short-cover loss is not a wash trigger
- **Where:** `src/taxjson/lib/core.py` US short-side replacement matching (`short_replacements`): only the short-OPEN portion of a SELL (§1091(e)(2), "another short sale") registers as a replacement for a loss on closing a short.
- **Current behavior:** short 100 @100; cover 200 @110 (loss −1,000, opens 100 long); sell 100 @105 two weeks later → the −1,000 cover loss is allowed. §1091(e)(1) ("substantially identical stock ... were **sold**" within the window) would wash it. Same-year totals coincide; cross-year attribution and 8949 code-W reporting can differ.
- **Why deferred:** rare shape (a cover that flips long, then a sale inside the window); documenting the gap is the honest state until a fixture demands it (2026-09 US-engine audit).

### US: sheltered (IRA) replacements already sold before the loss still deny it; taxable ones don't
- **Where:** `core.py` US pass — sheltered BUYs register their full quantity with no lot reference and are never decremented by later sheltered SELLs; taxable replacement lots are zeroed on consumption.
- **Current behavior:** IRA buys 100 on 05-20 and sells 100 on 05-25; taxable loss 06-15 → `permanently_disallowed`. The identical pattern in a second taxable account (`per_account_basis`) → loss allowed. §1091(a) keys on ACQUISITION within the window (no still-held test), so the IRA reading is the literal statute and the taxable reading follows Reg. 1.1091-1's lot consumption — the two books apply different theories.
- **Why deferred:** which reading is right for shares acquired AND disposed inside the window before the loss is not settled authority; flagged so the asymmetry is known (2026-09 audit).

### US: options as replacement property are advisory-only
- **Where:** `core.py` `detect_option_replacement_matches` (warn-only by design).
- **Current behavior:** §1091(a) covers "a contract or option so to acquire"; a deep-ITM call bought inside the window leaves the stock loss allowed, with a warning. A user policy choice, not a bug — the statute itself is mandatory, so treat the warning as an instruction (2026-09 audit).

### US: specific-lot identification is not supported (FIFO only)
- **Where:** the US engine consumes lots FIFO (Reg. 1.1012-1(c)(1) default). Reg. 1.1012-1(c)(2)–(3) specific identification, and a broker's non-FIFO default (e.g. highest-cost), are not modeled.
- **Consequence:** a broker 1099-B computed under specific ID will not reconcile per-lot; year totals agree only when every lot is eventually sold. Set the broker's lot method to FIFO or reconcile by hand.


### §1256 (60/40 mark-to-market) is not implemented
- **Where:** `src/taxjson/lib/core.py` — documented out-of-scope in the US engine's docstring, alongside §1233(b)(1)/(2) anti-conversion rules and §1259 constructive sales.
- **Current behavior:** futures and broad-based index options (SPX, NDX, futures) are run through the ordinary FIFO ST/LT engine — no year-end mark-to-market, no 60/40 split.
- **Why deferred:** needs a contract-classification table (which symbols are §1256 contracts) plus a mark-to-market pass; no user data currently exercises it.
- **Workaround:** report §1256 contracts from your broker's 1099-B (they're reported mark-to-market there) and exclude them from the tool's totals.

### US estimated taxes (1040-ES) are not modeled
- **Where:** `src/taxjson/bin/taxjson_instalments.py` implements the Canadian instalment regime only; `taxjson instalments` refuses on a US project.
- **Why deferred:** the US regime differs in every mechanical detail — four different due dates (Apr 15 / Jun 15 / Sep 15 / Jan 15), safe harbours (90% of the current year, or 100%/110% of the prior year by AGI), the annualized-income method, and a Form 2210 penalty computed at the federal short-term rate plus 3%. Sharing code with the Canadian model would produce a hybrid that is right for neither.
- **Workaround:** US filers should use Form 1040-ES worksheets; `taxjson estimate` still supplies the underlying investment-income tax figure.

### US AMT is not computed
- **Where:** `src/taxjson/lib/tax_estimate.py` — the Canada estimator carries the post-2024 AMT check; the US branch deliberately does not.
- **Why this is the design:** under US AMT, long-term capital gains and qualified dividends KEEP their preferential rates inside the AMT calculation, so investment income alone rarely triggers it. The classic triggers (ISO exercises, private-activity bond interest, large pre-TCJA state-tax deductions) are inputs the brokerage CSVs do not carry — computing a number from partial inputs would be worse than saying nothing.
- **Workaround:** US filers with ISO exercises should run Form 6251 with their full return data.

### `taxjson sum` tax estimate assumes dividend classification
- **Where:** `src/taxjson/lib/tax_estimate.py` (the `--other-income` estimate block).
- **Current behavior:** Canada — every Canadian-listed dividend is treated as *eligible* (38% gross-up + DTC); US — every dividend is assumed *qualified*. Non-eligible Canadian dividends and non-qualified US dividends (REITs, some foreign payers, short-holding-period shares) are therefore estimated at the wrong rate.
- **Why deferred:** eligibility/qualification is issuer- and holding-period-specific data the CSVs don't carry. The output is labeled ESTIMATE ONLY and is never a filing number.

---
---

### Superficial-loss attribution when both a taxable and a registered account bought in the window
- **Where:** `src/taxjson/lib/core.py` allocation loop (post-loss triggers chronologically, then pre-loss latest-first).
- **Current behavior:** when both a taxable account and an RRSP/TFSA acquire the property inside the window and both still hold at day 30, which one absorbs the denial follows that order, so the same loss can be permanent (registered) or deferred (taxable) depending on which leg settled first.
- **Why deferred:** s.53(1)(f) does not prescribe an allocation and CRA has published none; the ordering is a stated policy, not a rule. A "taxable first" option would be a defensible alternative.
- **Workaround:** `taxjson wash-sales` shows the trigger chosen; a `.tt` note of the intended attribution is the record.

### Transfers TO a registered plan at a loss (s.40(2)(g)(iv))
- **Where:** taxable-account TRANSFER rows are dropped at parse and rejected by the engine.
- **Current behavior:** the taxable-side disposition of an in-kind contribution is booked only if you record it as a `.tt` BUYSELL at fair market value in the taxable account. A loss on it is then denied indirectly (as a superficial loss against the plan's acquisition, permanent), which coincides with s.40(2)(g)(iv) — a loss on a transfer to an RRSP/TFSA is nil — in the common case; a gain is taxable as usual.
- **Workaround:** record the contribution day as a BUYSELL sell at FMV in the taxable account (and the plan's acquisition with `transfers = true`).

### Second-order superficial losses from the ACB bump's date
- **Where:** `src/taxjson/lib/core.py` (the deferral ADJUST is dated the trigger).
- **Current behavior:** with a rebuy, a partial sale inside the window and the rest sold later, the inner sale inherits part of the bump and can itself be denied and re-deferred; T4037 attributes the whole denied amount to the shares still held at day 30. Year totals agree unless the inner and outer sales straddle a year end; the extra DISALLOW row shows in `wash-sales`.

### Estimate classifies dividends by listing suffix
- **Where:** `taxjson estimate` / `lib/tax_estimate.py`.
- **Current behavior:** a `.TO` payer is treated as eligible-Canadian and a `.US` payer as foreign (15% FTC assumed). A Canadian corporation held via its US line, or a US issuer on a `.TO` line, is misclassified; `taxjson scan` flags the cross-listing case. The s.126 credit is capped at 15% of the foreign dividends, not at the Canadian tax otherwise payable on them.

### Interest expense and carrying charges are not surfaced
- **Where:** IB `INTEREST` rows keep their sign; `sum-income` nets debit against credit interest.
- **Current behavior:** margin interest paid (deductible under s.20(1)(c), line 22100; only 50% for the 2024+ AMT) disappears into the income total instead of being reported as a deduction. The estimate excludes interest entirely.

### Spin-off default wording
- **Where:** `lib/corp_actions.py` spin-off default.
- **Current behavior:** every spin-off distribution is labelled a "foreign dividend at FMV"; a Canadian parent's in-kind distribution is an eligible dividend (or a s.86 reorganisation), and the estimate then classifies it by the target's suffix.

### Carryover has no inclusion-rate adjustment for pre-2001 losses
- **Where:** `bin/taxjson_carryover.py`.
- **Current behavior:** the ledger is at 100% with the 50% rate applied on the T1A, correct for post-2000 losses; a pre-2001 net capital loss (¾ or ⅔ rate) fed in via `--claimed` is not rescaled per s.111(1.1). (The cancelled 2024 two-thirds proposal was never applied anywhere.)

### `days_held` uses trade dates
- **Where:** `lib/core.py` closing branch.
- **Current behavior:** the days-held figure in traces counts from trade dates while every other Canadian date is settlement-basis. Cosmetic — Canada has no holding-period rule.

### Non-eligible dividends are estimated as eligible
- **Where:** `src/taxjson/lib/tax_estimate.py` `estimate_canada`.
- **Current behavior:** every Canadian-source dividend gets the eligible gross-up (38%) and credit. Split-share corporations, some REIT/LP distributions and small-business dividends are non-eligible (15% gross-up, smaller credit) and are overstated in the estimate; T3 trust allocations (interest, ROC, capital gains) are not split by type at all.
- **Why deferred:** brokers' activity exports do not carry the T5 box; the split is only known from the slip.
- **Workaround:** the estimate is disclosed as an estimate; use the slips for the return. `taxjson reconcile-slips` compares totals.

## Latent assumptions (audit-flagged, not firing on current data)

### Currency⇒exchange suffix map is duplicated in ~6 places
- **Where:** `base.py`, `corp_actions.py`, `ib_extractor.py` (×3), `ticker_map.py` — each hardcodes `{'CAD':'TO','USD':'US','AUD':'AX','GBP':'L'}`; `price_chain.py` and `t1135.py` carry reverse/extended variants (suffix→currency, suffix→country).
- **Risk:** a non-G4-currency listing (EUR/CHF/JPY/…) or a USD security on a non-US exchange gets the wrong suffix, splitting/merging ACB pools; and the copies can drift when one is changed. The IB `IE→L` item above is one instance of this broader pattern. Fix: centralize the map in one helper. (Note: `ticker_map.map_ticker`'s blanket US→TO remap is only used in `generate_summary`, a diagnostic — **not** the live `apply_mapping` transaction path — so it does not silently merge real pools.)

## Test coverage gaps (tracked; lower priority)

Added 2026-06: CLI tests for `taxjson-corp-actions`, `taxjson-missing-history`, and the country-alias helpers. Still uncovered:
- `bin/to_base_curr.py` — `main()` and the FX fetch+cache paths (network-bound; cache read/write is testable). (`fill_crypto_prices.py` cache paths graduated: covered by `test_silent_corruption_fixes` and `test_audit_2026_08_fixes` — 2026-09 round-five audit.)

---

## Report semantics (by design — not a bug)

### `<account>.sum` vs `<account>_wash.sum` — pre-wash and post-wash reports
- **Where:** `src/taxjson/bin/taxjson_run.py:stage_account` writes `<account>.sum` and `stage_wash_pass` writes `<account>_wash.sum`.
- **Behavior:** For each taxable account, two summary files are emitted:
  - **`<account>.sum`** — gains computed WITHOUT cross-account `--sheltered` context. The engine's intra-account wash-sale logic (ITA s. 40(2)(g) for Canada; IRC §1091 for US) still fires on the account's own losses.
  - **`<account>_wash.sum`** — gains re-computed WITH the merged sheltered accounts passed as `--sheltered` context. Adds Rev. Rul. 2008-5 (US) / affiliated-balance (Canada) matching: a sheltered acquisition within ±30 days of a taxable loss disallows the loss.
- **Why this is intentional:** the pair is a deliberate debug check. Comparing the two files line-by-line surfaces which losses got disallowed only because of a cross-account match — useful for sanity-checking the data (and catching wrong-account-tagging errors before filing).
- **Which one do I file from?** **`<account>_wash.sum` is canonical.** It includes the full cross-account wash treatment. `<account>.sum` is the pre-comparison baseline.
- **Why not collapse them:** the pre/post comparison is the design's value-add. Future change candidate: bake the "POST-WASH (FILE FROM THIS)" / "PRE-WASH (DIAGNOSTIC)" label into a header line at the top of each file so the role is unambiguous when a user opens one in isolation.

---

### T1135 cost amounts follow the books — custody transfer-ins carry only declared cost
- **Where:** `src/taxjson/bin/taxjson_t1135.py` (`TRANSFER` in `_NON_CAPITAL`; taxable books post-sidecar contain no TRANSFER rows at all).
- **Current behavior:** a position established by a custody transfer-in contributes to the T1135 cost-amount threshold only through whatever acquisition history the books carry (imported buys, `start_pos`/backdated `.tt` declarations). A transferred-in foreign position with lost history shows as a phantom opening (flagged "cost understated") — the threshold test can understate until the true history is declared.
- **Why this is the design:** T1135 cost amount IS adjusted cost base; the tool refuses to invent one from a transfer's arrival market value. Declare the real history (the same `custody_fixes.tt` pattern the wash engine prescribes) and the threshold is right.

## CLI silent-fail conditions

### `to_base_curr.py` real-time intra-day fetch
- **Where:** `src/taxjson/bin/to_base_curr.py` (the intraday real-time block at the end of main).
- **Current behavior:** if the intra-day spot fetch from yfinance fails, the failure is swallowed without a stderr message. The historical rates already emitted are unaffected.
- **Why deferred:** the most common failure mode is "market closed" (weekends, holidays, evenings). Logging on every off-market run would be steady noise drowning out real warnings.
- **Workaround:** the historical-rates path emits its own loud `WARNING:` line on a fetch failure, so a real outage is still visible — only the optional intra-day stamp is silent.

---

## Known engine corner cases (latent — not on the standard `taxjson run` path)

These are real bugs in code paths the standard `taxjson run` flow never exercises. They're documented so anyone repurposing the engine knows.

### `_drop_self_cancelling_transfers` intervening-event check is BUYSELL-only
- **Where:** `src/taxjson/lib/pipeline.py:_drop_self_cancelling_transfers` (moved from taxjson_gains.py in the pipeline consolidation).
- **Current behavior:** A pair of TRANSFER rows on the same symbol+account that net to zero is auto-dropped UNLESS a `BUYSELL` of the same symbol falls between them. The check excludes `ASSIGN` and `SPLIT` events — if an option ASSIGN or a corp-action SPLIT happens between the two TRANSFERs, the auto-drop fires anyway and the pair is removed, but the position the SPLIT/ASSIGN operated on is now misaligned with the actual brokerage record.
- **Why deferred:** cross-listing journals (the motivating case for the auto-drop) don't normally straddle corp actions or option assignments on the same security. No observed mis-fire on real data.
- **Fix template:** extend the intervening-event check to include `ASSIGN` and `SPLIT` actions, not just `BUYSELL`.

---

### `taxjson audit` reports phantom-backed dispositions as "not found"

A disposition that drains a `phantoms.json` opening is, by design, pulled
out of the gains file into `manual_reporting_required`. The audit's
pipeline tie-out does not consult that list, so each such sale prints
"disposition not found in the pipeline gains file(s) — cannot tie out
(books changed since the last run?)" and the tie-out line ends with ✗
even though nothing is stale. Seen on the 2024 reconstruction (two BK.TO
sales against a pre-history position). Reading fix: the audit should
recognise the manual-reporting rows and tie them out as "phantom basis —
reported manually" instead of counting them as missing.

## Conventions

- **Severity ranking:** items above are loosely ordered: gaps that drop tax-relevant data first, cosmetic / latent items last.
- **What counts as "fixed":** the item is removed from this file *and* a regression test pins the corrected behavior.
- **What counts as a "workaround":** any user-side action that makes the bug not fire on their data (manual edit, `ticker.map` override, splitting input files, etc.).
- **What does NOT belong here:** items that are simply unimplemented features (e.g. a brokerage we haven't written a parser for) — those go in `CONTRIBUTING.md` or roadmap docs.

---

## Graduated (fixed)

- **Option premium timing across a year end (ITA s.49(1); IT-479R paras 21–25)** — 2026-09: a written option's premium is now a gain in the year written under `option_premium_timing = "grant"` (Canada default), a buy-back a loss in its own year, an assignment folded with no grant record; `taxjson option-boundary` names any filed year to amend. Previously the premium was recognised at the close (the US §1234 convention).
- **Negative ACB after a return of capital (s.40(3))** — 2026-09: booked as a deemed gain in the distribution year with the ACB reset to nil; previously only a warning, with the whole amount landing in the sale year.
- **Re-short "superficial loss" (s.54)** — 2026-09: a new short sale or written option no longer triggers a denial of a cover loss (it acquires nothing); a long purchase held at day 30 still does. Previously the US §1091(e) re-short branch applied.

### Broker-parser coverage gaps (graduated 2026-09-14)
Kraken stablecoin (`USDC`/`USDT`/`DAI`) `earn/reward` rows were folded
to the symbol `USD` before pricing, which `taxjson-fill-crypto` refuses
to price — the income booked at $0; the reward now keeps its coin
name and is priced at 1.0/unit (net = qty) with no phantom acquisition
leg. IB exercise code `Ex` (`C;Ex` option leg at T. Price 0) is an
ASSIGN like `A`, so the premium rolls into the stock leg exactly as an
assignment's does (verified in both engines: call exercise cost =
strike + premium; put exercise proceeds = strike − premium). IB
`Transaction Fees` (UK Stamp Tax) fold into the same-day BUYSELL on
the symbol, else a symbol-bound FEE; `Commission Adjustments` refunds
are negative FEE rows; tender / voluntary-offer journals are netted
(zero-proceeds round trip = recognized no-op, cash settlement = a
booked sale with a NOTE). Kraken `transfer/transferpeertopeer` is
custody evidence like a withdrawal; Kraken fiat-base fills and fiat-
fiat instant trades, IB `Trades/Forex`, and Questrade `FXT` are
recognized non-events (see the two "conversions are not modeled"
items above) instead of phantom `USD`/`CAD` trades; Questrade `FCH`
is a FEE row. IB row accounting reconciles under `--lint` for every
section; the IB `Fees` sign now follows the repo FEE convention
(positive = charged). Pinned by tests/test_parser_coverage_audit.py.

### §1223(3) tacking is per-share (graduated 2026-09-10)
The replacement lot is split at the match point — in the match loop
when the lot already exists, at lot creation when the loss preceded
the buy — so only the MATCHED shares carry the tacked holding period
and the §1091(d) basis bump; the remainder is an ordinary lot right
behind it in FIFO order. A 200-share replacement for a 100-share loss
now reports 100 LONG_TERM (tacked) + 100 SHORT_TERM instead of one
200-share LONG_TERM row. Pinned by tests/test_us_engine_audit.py.

### US holding period follows Rev. Rul. 66-7 (graduated 2026-09-10)
An acquisition on the LAST day of a month starts its holding period on
the 1st of the next month and is long-term from the 1st of that month
a year later: Feb 29 -> LT from Mar 1 (not Mar 2); Feb 28 of a common
year -> LT from Mar 1, so a Feb 29 sale the next year is still
SHORT_TERM. The old code (and its pin) had both directions wrong.
`taxjson harvest`'s "LT IN" date derives from the same function.
Pinned by tests/test_engines_expanded.py.

### Crypto withdrawals/sends → transfer sidecar (graduated 2026-09-09)
Kraken ledger `withdrawal`/`deposit` rows and Coinbase `Send`/`Receive`
rows now emit TRANSFER evidence rows: they land in the per-broker
sidecar, `taxjson transfers crypto` shows them, and the parse prints
the FMV-disposition note for out-legs ("if any left your ownership,
each is a taxable disposition at fair market value"). Coinbase rows
carry the spot price so the `.tt` FMV sell is copy-paste; Kraken
ledgers carry no fiat value (price/net stay 0). Stablecoin evidence
keeps its own name (a USDC gift is a disposition of USDC the
property) even though trade books fold USDC/USDT/DAI to USD for
pricing. Kraken Earn shuffles (`hybridearnwithdrawal` etc.) remain
ignored — internal moves. Tax semantics still not assumed: only the
user knows gift vs self-custody move; genuine gifts are declared as
`.tt` sells at FMV. Pinned by tests/test_transfer_sidecar.py
(TestCryptoSendsBecomeEvidence).

- **2026-08 (blended taxable pass):** the multi-account gap is fixed. One combined gains run now produces the canonical wash-adjusted artifacts for all taxable equity accounts: Canada ACB blends across non-registered accounts (ITA s.47) and US §1091 matches cross-account while FIFO basis stays per account (`taxjson-gains --per-account-basis`); `taxjson-split-gains` rebuilds the per-account files, so every consumer reads its usual filenames. The per-account `<name>.sum` remains the isolated pre-blend baseline (deliberate diagnostic pair). Tests: `tests/test_blended_taxable.py`.
- **2026-08:** `taxjson run --strict` — per-account validation ERRORs promoted to fatal (the non-fatal default remains); `crypto_ticker.map` — user-editable Yahoo-collision overrides for `taxjson-fill-crypto` (built-ins remain as defaults); Coinbase "Convert" rows — two-leg SELL+BUY emission for the recognized `Converted X AAA to Y BBB` Notes pattern (unknown variants still fail hard); Kraken trades-CSV crypto/crypto pairs — two USD-denominated legs mirroring the ledgers path; Kraken legacy concatenated pair formats (`XXBTZUSD`, `XETHXXBT`, `ADAUSD`) — recognized explicitly, unknown shapes refuse loudly; Kraken `_normalize_asset` — X-prefix strip extended to the full enumerated set (XLM/XMR/ZEC/XDG→DOGE/ETC/MLN/REP); Webull blank-`Description` carry-over — reset when the symbol changes; crypto-vs-equity path mismatch — `taxjson run` now refuses a crypto-broker file in an equity account (and vice versa); interactive corp-actions stage — stale `.diag` sidecars are cleared. Regression tests: `tests/test_audit_2026_08_fixes.py::TestKnownIssuesGraduated`.
